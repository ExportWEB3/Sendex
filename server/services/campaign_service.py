"""
Campaign Service - Orchestrates email campaign sending

Handles:
- Campaign lifecycle management (create, start, pause, resume, cancel)
- Recipient queuing and distribution across inboxes
- Send orchestration with rate limiting
- Progress tracking and statistics
"""

import json
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select, update, func, and_
import random
from services.attachment_service import resolve_attachment_path

from models.campaign import Campaign, CampaignStatus, CampaignRecipient, RecipientSendStatus
from models.recipient import Recipient
from models.list import RecipientList, RecipientStatus
from models.inbox import Inbox
from models.smtp_account import SMTPAccount
from services.template_engine import TemplateEngine
from services.smtp_service import SMTPService
from services.rate_limiter import RateLimiter
from services.campaign_logger import log_campaign_activity
from services.queue_service import email_queue as _eq
from services.kill_switch import is_kill_switch_enabled, KILL_SWITCH_MESSAGE

logger = logging.getLogger(__name__)


class CampaignService:
    """
    Manages campaign lifecycle and send orchestration.
    
    Campaign Flow:
    1. Create campaign (DRAFT) -> links to list, inboxes, template
    2. Start campaign (RUNNING) -> queues recipients, begins sending
    3. Send loop -> renders templates, sends via SMTP, tracks progress
    4. Complete (COMPLETED) -> all recipients processed
    
    Can be paused/resumed at any point during sending.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.template_engine = TemplateEngine()
        self.rate_limiter = RateLimiter()
        self._stop_flag = False

    def _set_kill_switch_status(self, campaign_id: int, action: str) -> bool:
        """Expose and log the global sending block for a campaign action."""
        if not is_kill_switch_enabled():
            return False

        try:
            _eq.redis.set(
                f"campaign:{campaign_id}:status",
                json.dumps({
                    "state": "kill_switch",
                    "reason": "kill_switch",
                    "message": KILL_SWITCH_MESSAGE,
                }),
                ex=300,
            )
        except Exception:
            pass

        log_campaign_activity(
            campaign_id,
            f"🔒 {KILL_SWITCH_MESSAGE} — {action}; sending paused",
        )
        return True
    
    # =========================================================================
    # Campaign CRUD Operations
    # =========================================================================
    
    def create_campaign(
        self,
        name: str,
        subject: str,
        body_html: str,
        list_id: int,
        inbox_ids: List[int],
        body_text: Optional[str] = None,
        reply_to_email: Optional[str] = None,
        track_opens: bool = True,
        track_clicks: bool = True,
        template_data: Optional[Dict] = None,
        scheduled_at: Optional[datetime] = None,
        user_id: Optional[int] = None,
        attachments: Optional[List[Dict]] = None,
        template_ids: Optional[List[int]] = None,
        send_timezone: Optional[str] = None
    ) -> Campaign:
        """
        Create a new campaign in DRAFT status.
        
        Args:
            name: Campaign name for internal reference
            subject: Email subject line (supports {{variables}})
            body_html: HTML email body (supports {{variables}})
            list_id: ID of recipient list to send to
            inbox_ids: List of inbox IDs to send from (round-robin)
            body_text: Plain text version (auto-generated if not provided)
            reply_to_email: Custom reply-to address
            track_opens: Whether to inject tracking pixel
            track_clicks: Whether to wrap links for tracking
            template_data: Additional variables for template
            scheduled_at: When to auto-start (None = manual start)
            user_id: Owner user ID for multi-tenancy
        
        Returns:
            Created Campaign object
        """
        if user_id is None:
            raise ValueError("Campaign owner is required")

        # Validate every linked resource belongs to the campaign owner.
        recipient_list = self.db.query(RecipientList).filter(
            RecipientList.id == list_id,
            RecipientList.user_id == user_id,
        ).first()
        if not recipient_list:
            raise ValueError("Recipient list not found")
        
        unique_inbox_ids = set(inbox_ids)
        if not unique_inbox_ids:
            raise ValueError("At least one inbox is required")
        inboxes = self.db.query(Inbox).filter(
            Inbox.id.in_(unique_inbox_ids),
            Inbox.user_id == user_id,
        ).all()
        if len(inboxes) != len(unique_inbox_ids):
            raise ValueError("One or more inbox IDs are invalid")
        
        # Validate template_ids if provided
        if template_ids:
            from models.ses_template import SESEmailTemplate
            unique_template_ids = set(template_ids)
            templates = self.db.query(SESEmailTemplate).filter(
                SESEmailTemplate.id.in_(unique_template_ids),
                SESEmailTemplate.user_id == user_id,
            ).all()
            if len(templates) != len(unique_template_ids):
                raise ValueError("One or more template IDs are invalid")
        
        # Create campaign
        campaign = Campaign(
            name=name,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            list_id=list_id,
            inbox_ids=inbox_ids,
            reply_to_email=reply_to_email,
            track_opens=track_opens,
            track_clicks=track_clicks,
            template_data=template_data or {},
            attachments=attachments,
            template_ids=template_ids,
            status=CampaignStatus.SCHEDULED if scheduled_at else CampaignStatus.DRAFT,
            scheduled_at=scheduled_at,
            user_id=user_id,  # Set owner
            send_timezone=send_timezone,
            send_progress={
                "total": 0,
                "sent": 0,
                "failed": 0,
                "pending": 0
            }
        )
        
        self.db.add(campaign)
        self.db.commit()
        self.db.refresh(campaign)

        outside_window = False
        next_window_open = None
        local_time = None
        if campaign.send_timezone:
            try:
                from zoneinfo import ZoneInfo

                now_local = datetime.now(ZoneInfo(campaign.send_timezone))
                local_time = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
                in_business_hours = now_local.weekday() <= 4 and 9 <= now_local.hour < 17
                if not in_business_hours:
                    outside_window = True

                    candidate = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
                    if now_local.hour >= 9:
                        candidate += timedelta(days=1)
                    while candidate.weekday() > 4:
                        candidate += timedelta(days=1)

                    next_window_open = candidate.isoformat()
                    remaining = max(0, int((candidate - now_local).total_seconds()))
                    ttl_seconds = max(120, min(86400, remaining + 180))

                    _eq.redis.set(
                        f"campaign:{campaign.id}:status",
                        json.dumps(
                            {
                                "state": "cooldown",
                                "reason": "outside_business_hours",
                                "remaining": remaining,
                                "next_window_open": next_window_open,
                                "worker_timezone": campaign.send_timezone,
                                "message": f"Worker {campaign.send_timezone} is outside business hours; waiting for next window.",
                            }
                        ),
                        ex=ttl_seconds,
                    )
            except Exception:
                pass
        
        return campaign
    
    def get_campaign(self, campaign_id: int) -> Optional[Campaign]:
        """Get campaign by ID with recipients loaded."""
        return self.db.query(Campaign).options(
            selectinload(Campaign.recipients)
        ).filter(Campaign.id == campaign_id).first()
    
    def list_campaigns(
        self,
        status: Optional[CampaignStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Campaign]:
        """List campaigns with optional status filter."""
        query = self.db.query(Campaign).order_by(Campaign.created_at.desc())
        
        if status:
            query = query.filter(Campaign.status == status)
        
        return query.limit(limit).offset(offset).all()
    
    def update_campaign(
        self,
        campaign_id: int,
        **updates
    ) -> Optional[Campaign]:
        """
        Update campaign fields. Only allowed in DRAFT/SCHEDULED status.
        """
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return None
        
        if campaign.status not in [CampaignStatus.DRAFT, CampaignStatus.SCHEDULED]:
            raise ValueError("Can only update campaigns in DRAFT or SCHEDULED status")

        if "inbox_ids" in updates:
            unique_inbox_ids = set(updates["inbox_ids"] or [])
            if not unique_inbox_ids:
                raise ValueError("At least one inbox is required")
            inbox_count = self.db.query(Inbox).filter(
                Inbox.id.in_(unique_inbox_ids),
                Inbox.user_id == campaign.user_id,
            ).count()
            if inbox_count != len(unique_inbox_ids):
                raise ValueError("One or more inbox IDs are invalid")

        if updates.get("template_ids"):
            from models.ses_template import SESEmailTemplate
            unique_template_ids = set(updates["template_ids"])
            template_count = self.db.query(SESEmailTemplate).filter(
                SESEmailTemplate.id.in_(unique_template_ids),
                SESEmailTemplate.user_id == campaign.user_id,
            ).count()
            if template_count != len(unique_template_ids):
                raise ValueError("One or more template IDs are invalid")
        
        for key, value in updates.items():
            if hasattr(campaign, key):
                setattr(campaign, key, value)
        
        self.db.commit()
        self.db.refresh(campaign)
        return campaign
    
    def delete_campaign(self, campaign_id: int, force: bool = False) -> bool:
        """Delete campaign. Only allowed in DRAFT status unless force=True."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return False
        
        if campaign.status != CampaignStatus.DRAFT and not force:
            raise ValueError("Can only delete campaigns in DRAFT status")

        # Import here to avoid circular imports at module load time.
        from models.warmup import CampaignAutoReply
        from models.email import Email

        try:
            # Order matters:
            # campaign_auto_replies -> campaign_recipients -> campaigns
            self.db.query(CampaignAutoReply).filter(
                CampaignAutoReply.campaign_id == campaign_id
            ).delete(synchronize_session=False)

            self.db.query(CampaignRecipient).filter(
                CampaignRecipient.campaign_id == campaign_id
            ).delete(synchronize_session=False)

            # emails.campaign_id also has an FK to campaigns.id
            self.db.query(Email).filter(
                Email.campaign_id == campaign_id
            ).delete(synchronize_session=False)

            # Use bulk delete for parent too; mixing child bulk deletes with
            # ORM instance delete can trigger stale row checks on relationships.
            self.db.query(Campaign).filter(
                Campaign.id == campaign_id
            ).delete(synchronize_session=False)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise
    
    # =========================================================================
    # Campaign Lifecycle Management
    # =========================================================================
    
    def start_campaign(self, campaign_id: int) -> Campaign:
        """
        Start a campaign - queue recipients and begin sending.
        
        Process:
        1. Load all recipients from linked list
        2. Create CampaignRecipient entries for tracking
        3. Set status to RUNNING
        4. Begin send loop (async)
        """
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        if campaign.status not in [CampaignStatus.DRAFT, CampaignStatus.SCHEDULED]:
            raise ValueError(f"Cannot start campaign in {campaign.status} status")
        
        # Initialize template rotation state if multi-template
        if campaign.template_ids and len(campaign.template_ids) >= 2:
            shuffled = list(campaign.template_ids)
            random.shuffle(shuffled)
            campaign.template_rotation_state = {
                "shuffled_order": shuffled,
                "current_index": 0,
                "current_batch_sent": 0,
                "current_batch_size": 4,
                "batch_toggle": False  # False=4, True=6
            }
        
        # Queue recipients from the list
        queued_count = self._queue_recipients(campaign)
        
        # Derive engine_type from ALL inboxes' SMTP account provider_types
        # ses = all non-smtp providers, smtp = all smtp providers, mixed = both
        if not campaign.engine_type and campaign.inbox_ids:
            try:
                engine_types = set()
                for iid in campaign.inbox_ids:
                    ib = self.db.query(Inbox).filter(Inbox.id == iid).first()
                    if ib and ib.smtp_account_id:
                        smtp_acct = self.db.query(SMTPAccount).filter(SMTPAccount.id == ib.smtp_account_id).first()
                        provider = getattr(smtp_acct, 'provider_type', 'smtp') or 'smtp'
                        engine_types.add('smtp' if provider == 'smtp' else 'ses')
                    else:
                        engine_types.add('ses')
                if engine_types == {'smtp'}:
                    campaign.engine_type = 'smtp'
                elif engine_types == {'ses'}:
                    campaign.engine_type = 'ses'
                else:
                    campaign.engine_type = 'mixed'
            except Exception:
                campaign.engine_type = 'ses'
        elif not campaign.engine_type:
            campaign.engine_type = 'ses'
        
        # Update status
        campaign.status = CampaignStatus.RUNNING
        campaign.started_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(campaign)
        
        # Log to live terminal
        inbox_count = len(campaign.inbox_ids) if campaign.inbox_ids else 0
        tz_label = ''
        if campaign.send_timezone:
            tz_label = ' [ET]' if campaign.send_timezone == 'US/Eastern' else ' [CT]' if campaign.send_timezone == 'US/Central' else f' [{campaign.send_timezone}]'
        log_campaign_activity(
            campaign.id,
            f"🚀 Campaign started{tz_label} — {queued_count} recipients prepared across {inbox_count} inbox(es)"
        )

        sending_blocked = self._set_kill_switch_status(campaign.id, "campaign start blocked")

        # Register with the batching work-queue only when sending is allowed.
        # The scheduler self-heals RUNNING campaigns after the block is lifted.
        if not sending_blocked:
            try:
                _eq.schedule_campaign(campaign.id, tz=campaign.send_timezone)
            except Exception:
                pass

        # Keep post-start telemetry best-effort; start API must not fail after commit.
        try:
            outside_window = False
            next_window_open = None
            local_time = None

            if campaign.send_timezone:
                from zoneinfo import ZoneInfo

                now_local = datetime.now(ZoneInfo(campaign.send_timezone))
                local_time = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
                outside_window = not (now_local.weekday() <= 4 and 9 <= now_local.hour < 17)

                if outside_window:
                    candidate = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
                    if now_local.hour >= 9:
                        candidate += timedelta(days=1)
                    while candidate.weekday() > 4:
                        candidate += timedelta(days=1)
                    next_window_open = candidate.isoformat()

            if outside_window:
                if next_window_open:
                    log_campaign_activity(
                        campaign.id,
                        f"⏰ {campaign.send_timezone} worker is outside business hours ({local_time}). Waiting until {next_window_open}.",
                    )
                else:
                    log_campaign_activity(
                        campaign.id,
                        f"⏰ {campaign.send_timezone} worker is outside business hours ({local_time}). Waiting for next send window.",
                    )
        except Exception:
            pass
        
        return campaign
    
    def start_all_campaigns(
        self,
        user_id: int,
        stagger_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Start every DRAFT/SCHEDULED campaign owned by ``user_id``.

        All campaigns become RUNNING immediately, but their first micro-batch
        due times are staggered evenly across the stagger window so the fleet
        ignites smoothly instead of firing everything at once.

        Returns:
            {"started": [ids], "failed": [{"id", "reason"}],
             "stagger_window_seconds": float}
        """
        if stagger_seconds is None:
            stagger_seconds = float(os.getenv("START_ALL_STAGGER_SECONDS", "900"))

        eligible = self.db.query(Campaign).filter(
            Campaign.user_id == user_id,
            Campaign.status.in_([CampaignStatus.DRAFT, CampaignStatus.SCHEDULED]),
        ).order_by(Campaign.id).all()

        started: List[int] = []
        failed: List[Dict[str, Any]] = []
        total = max(1, len(eligible))
        now = time.time()

        for i, campaign in enumerate(eligible):
            try:
                # Initialize template rotation state if multi-template
                if campaign.template_ids and len(campaign.template_ids) >= 2:
                    shuffled = list(campaign.template_ids)
                    random.shuffle(shuffled)
                    campaign.template_rotation_state = {
                        "shuffled_order": shuffled,
                        "current_index": 0,
                        "current_batch_sent": 0,
                        "current_batch_size": 4,
                        "batch_toggle": False
                    }

                # Bulk-create recipient tracking rows
                self._queue_recipients(campaign)

                campaign.status = CampaignStatus.RUNNING
                campaign.started_at = datetime.now(timezone.utc)
                self.db.commit()

                offset = int(stagger_seconds * i / total)
                started.append(campaign.id)
                log_campaign_activity(
                    campaign.id,
                    f"🚀 Campaign started (start-all) — first batch in ~{offset}s"
                )

                sending_blocked = self._set_kill_switch_status(
                    campaign.id,
                    "start-all campaign blocked",
                )
                if not sending_blocked:
                    # Staggered ignition: evenly space first batches across the window.
                    _eq.schedule_campaign(
                        campaign.id,
                        due_at=now + offset,
                        overwrite=False,
                        tz=campaign.send_timezone,
                    )
            except Exception as e:
                self.db.rollback()
                failed.append({"id": campaign.id, "reason": str(e)})
                logger.error(f"start-all: campaign {campaign.id} failed: {e}")

        return {
            "started": started,
            "failed": failed,
            "stagger_window_seconds": stagger_seconds,
        }

    def pause_all_campaigns(self, user_id: int) -> Dict[str, Any]:
        """Pause every RUNNING campaign owned by ``user_id``.

        Returns {"paused": [ids], "failed": [{"id", "reason"}]}.
        """
        running = self.db.query(Campaign).filter(
            Campaign.user_id == user_id,
            Campaign.status == CampaignStatus.RUNNING,
        ).order_by(Campaign.id).all()

        paused: List[int] = []
        failed: List[Dict[str, Any]] = []
        for campaign in running:
            try:
                self.pause_campaign(campaign.id, reason="Paused via pause-all")
                paused.append(campaign.id)
            except Exception as e:
                self.db.rollback()
                failed.append({"id": campaign.id, "reason": str(e)})
                logger.error(f"pause-all: campaign {campaign.id} failed: {e}")

        return {"paused": paused, "failed": failed}

    def pause_campaign(
        self,
        campaign_id: int,
        reason: Optional[str] = None
    ) -> Campaign:
        """Pause a running campaign.

        Freezes all sending state:
        1. Sets status to PAUSED
        2. Drains queued-but-unsent emails from the Redis queue
        3. Resets those recipients' queued_at so the batcher re-queues them on resume
        4. Clears schedule, cooldown, and per-inbox cooldown keys
        """
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        if campaign.status != CampaignStatus.RUNNING:
            raise ValueError("Can only pause RUNNING campaigns")
        
        campaign.status = CampaignStatus.PAUSED
        campaign.pause_reason = reason or "Manually paused"
        self._stop_flag = True
        
        # ── Drain queued-but-unsent emails from Redis queue ──
        drained = _eq.drain_campaign(campaign_id)
        
        # ── Reset queued_at on recipients that were queued but not yet sent ──
        # This lets the batcher pick them up fresh on resume
        reset_count = self.db.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status == RecipientSendStatus.PENDING,
            CampaignRecipient.queued_at != None
        ).update({CampaignRecipient.queued_at: None}, synchronize_session='fetch')
        
        self.db.commit()
        self.db.refresh(campaign)
        
        # Log to live terminal
        log_campaign_activity(
            campaign.id,
            f"⏸️  Campaign paused — {campaign.pause_reason}"
            + (f" ({drained} queued emails returned to pending, {reset_count} recipients reset)" if drained else "")
        )
        
        # ── Clean up all Redis scheduling state ──
        try:
            import json as _json
            _eq.unschedule_campaign(campaign_id)
            _eq.redis.set(
                f"campaign:{campaign.id}:status",
                _json.dumps({"state": "paused"}),
                ex=86400
            )
            # Clear pacing, schedule, and per-inbox cooldown keys
            _eq.redis.delete(f"campaign:{campaign.id}:next_batch")
            _eq.redis.delete(f"campaign:{campaign.id}:schedule")
            if campaign.inbox_ids:
                for inbox_id in campaign.inbox_ids:
                    _eq.redis.delete(f"campaign:{campaign.id}:inbox:{inbox_id}:next_batch")
        except Exception:
            pass
        
        return campaign
    
    def resume_campaign(self, campaign_id: int) -> Campaign:
        """Resume a paused campaign.

        Restores sending state:
        1. Sets status to RUNNING
        2. Sets a wake-up hint so the worker picks up immediately
        3. The batcher loop will see the running campaign and create a fresh batch
        """
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        if campaign.status != CampaignStatus.PAUSED:
            raise ValueError("Can only resume PAUSED campaigns")
        
        campaign.status = CampaignStatus.RUNNING
        campaign.pause_reason = None
        self._stop_flag = False
        
        self.db.commit()
        self.db.refresh(campaign)
        
        # Count how many are still pending
        pending = self.db.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.status == RecipientSendStatus.PENDING
        ).count()
        
        sending_blocked = self._set_kill_switch_status(campaign.id, "campaign resume blocked")

        if not sending_blocked:
            # Log to live terminal
            log_campaign_activity(
                campaign.id,
                f"▶️  Campaign resumed — {pending} emails pending, scheduling fresh batch"
            )

            # Set Redis status and wake-up hint for immediate pickup
            try:
                import json as _json
                _eq.schedule_campaign(campaign_id, tz=campaign.send_timezone)
                _eq.redis.set(
                    f"campaign:{campaign.id}:status",
                    _json.dumps({"state": "sending", "message": "Resumed", "remaining": pending}),
                    ex=3600
                )
                # Set immediate wake-up so worker and batcher pick up fast
                _eq.redis.set("queue:wake_at", str(time.time()), ex=120)
            except Exception:
                pass
        
        return campaign
    
    def cancel_campaign(self, campaign_id: int) -> Campaign:
        """Cancel a campaign - stops all sending."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        if campaign.status in [CampaignStatus.COMPLETED, CampaignStatus.CANCELLED]:
            raise ValueError(f"Campaign already in {campaign.status} status")
        
        campaign.status = CampaignStatus.CANCELLED
        campaign.completed_at = datetime.now(timezone.utc)
        self._stop_flag = True
        
        self.db.commit()
        self.db.refresh(campaign)
        
        # Log to live terminal
        log_campaign_activity(
            campaign.id,
            "🛑 Campaign cancelled"
        )
        # Set Redis status so terminal badge shows CANCELLED
        try:
            import json as _json
            _eq.unschedule_campaign(campaign_id)
            _eq.redis.set(
                f"campaign:{campaign.id}:status",
                _json.dumps({"state": "cancelled"}),
                ex=3600
            )
            _eq.redis.delete(f"campaign:{campaign.id}:next_batch")
            # Clear per-inbox cooldown keys
            if campaign.inbox_ids:
                for inbox_id in campaign.inbox_ids:
                    _eq.redis.delete(f"campaign:{campaign.id}:inbox:{inbox_id}:next_batch")
        except Exception:
            pass
        
        return campaign
    
    # =========================================================================
    # Recipient Queuing
    # =========================================================================
    
    def _queue_recipients(self, campaign: Campaign) -> int:
        """
        Load recipients from list and create tracking entries.
        
        Returns count of recipients queued.
        """
        # Get all active, non-unsubscribed recipients from the list
        recipients = self.db.query(Recipient).filter(
            and_(
                Recipient.list_id == campaign.list_id,
                Recipient.status == RecipientStatus.ACTIVE,
                Recipient.unsubscribed_at.is_(None)
            )
        ).all()
        
        # Create CampaignRecipient entries in a single bulk insert.
        # Round-robin inbox assignment.
        inbox_ids = campaign.inbox_ids or []
        rows = []
        for i, recipient in enumerate(recipients):
            assigned_inbox_id = inbox_ids[i % len(inbox_ids)] if inbox_ids else None
            rows.append({
                "campaign_id": campaign.id,
                "recipient_id": recipient.id,
                "inbox_id": assigned_inbox_id,
                "status": RecipientSendStatus.PENDING,
            })
        if rows:
            self.db.bulk_insert_mappings(CampaignRecipient, rows)
        
        # Update progress
        campaign.send_progress = {
            "total": len(recipients),
            "sent": 0,
            "failed": 0,
            "pending": len(recipients)
        }
        campaign.total_recipients = len(recipients)
        
        self.db.commit()
        
        return len(recipients)
    
    # =========================================================================
    # Send Engine
    # =========================================================================
    
    def process_campaign_batch(
        self,
        campaign_id: int,
        batch_size: int = 10
    ) -> Dict[str, Any]:
        """
        Process a batch of recipients for a campaign.
        
        This is called repeatedly by a worker/scheduler until all
        recipients are processed. Uses rate limiting to respect
        inbox sending limits.
        
        Returns:
            Dict with sent, failed, remaining counts
        """
        if self._set_kill_switch_status(campaign_id, "manual batch blocked"):
            return {
                "sent": 0,
                "failed": 0,
                "remaining": None,
                "status": "kill_switch",
                "error": KILL_SWITCH_MESSAGE,
            }

        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        if campaign.status != CampaignStatus.RUNNING:
            return {"error": f"Campaign not running (status: {campaign.status})"}
        
        # Get pending recipients for this campaign
        pending_recipients = self.db.query(CampaignRecipient).options(
            selectinload(CampaignRecipient.recipient)
        ).filter(
            and_(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status == RecipientSendStatus.PENDING
            )
        ).limit(batch_size).all()
        
        if not pending_recipients:
            # All done!
            campaign.status = CampaignStatus.COMPLETED
            campaign.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return {
                "sent": 0,
                "failed": 0,
                "remaining": 0,
                "status": "completed"
            }
        
        # Load inboxes for sending with their SMTP accounts
        inbox_list = self.db.query(Inbox).filter(
            Inbox.id.in_(campaign.inbox_ids)
        ).all()
        
        inboxes = {inbox.id: inbox for inbox in inbox_list}
        
        # Load SMTP accounts for inboxes
        smtp_account_ids = [inbox.smtp_account_id for inbox in inbox_list]
        smtp_accounts = {
            account.id: account
            for account in self.db.query(SMTPAccount).filter(
                SMTPAccount.id.in_(smtp_account_ids)
            ).all()
        }
        
        sent_count = 0
        failed_count = 0
        
        for campaign_recipient in pending_recipients:
            if self._stop_flag:
                break
            
            recipient = campaign_recipient.recipient
            inbox = inboxes.get(campaign_recipient.inbox_id)
            
            if not inbox:
                campaign_recipient.status = RecipientSendStatus.FAILED
                campaign_recipient.error_message = "Inbox not found"
                failed_count += 1
                continue
            
            # Get SMTP account for this inbox
            smtp_account = smtp_accounts.get(inbox.smtp_account_id)
            if not smtp_account:
                campaign_recipient.status = RecipientSendStatus.FAILED
                campaign_recipient.error_message = "SMTP account not found"
                failed_count += 1
                continue
            
            # Check rate limit using inbox's daily_cap
            can_send = self.rate_limiter.check_rate_limit(
                f"inbox:{inbox.id}",
                inbox.daily_cap
            )
            
            if not can_send:
                # Skip this recipient for now, will retry later
                continue
            
            # Render template with recipient data
            recipient_data = {
                "id": recipient.id,
                "first_name": recipient.first_name or "",
                "last_name": recipient.last_name or "",
                "email": recipient.email,
                "company": recipient.company or "",
                "title": recipient.title or "",
                "unsubscribe_token": recipient.unsubscribe_token or "",
            }

            extra_ctx = None
            if campaign.template_data:
                extra_ctx = dict(campaign.template_data)
                # Per-template overrides are only used in rotation mode elsewhere.
                extra_ctx.pop('__per_template__', None)

            sender_context = self.template_engine.resolve_sender_context(
                recipient=recipient_data,
                sender_email=smtp_account.from_email,
                sender_name_template=smtp_account.from_name,
                extra_context=extra_ctx,
                fallback_sender_name=(inbox.email.split('@')[0].replace('.', ' ').title() if inbox and inbox.email else ''),
            )
            
            # Render subject
            rendered_subject = self.template_engine.render(
                template=campaign.subject,
                recipient=recipient_data,
                sender=sender_context,
                extra_context=extra_ctx
            )
            
            # Render HTML body with tracking
            rendered_html = self.template_engine.render_html(
                template=campaign.body_html,
                recipient=recipient_data,
                sender=sender_context,
                campaign_id=campaign.id,
                include_tracking=campaign.track_opens,
                include_unsubscribe=True,
                extra_context=extra_ctx
            )
            
            # Render plain text body
            rendered_text = None
            if campaign.body_text:
                rendered_text = self.template_engine.render(
                    template=campaign.body_text,
                    recipient=recipient_data,
                    sender=sender_context,
                    extra_context=extra_ctx
                )
            
            # Send email
            try:
                # Load file attachments if campaign has them
                attachment_list = None
                if campaign.attachments:
                    attachment_list = []
                    for att in campaign.attachments:
                        filepath = resolve_attachment_path(att.get('stored_name', ''))
                        if filepath:
                            with open(filepath, 'rb') as af:
                                attachment_list.append({
                                    'filename': att.get('filename', os.path.basename(filepath)),
                                    'content': af.read(),
                                    'content_type': att.get('content_type', 'application/octet-stream'),
                                })
                
                # Route based on provider type
                provider = getattr(smtp_account, 'provider_type', 'smtp') or 'smtp'
                # Resolve reply-to: campaign override > inbox override > sender address
                effective_reply_to = campaign.reply_to_email or inbox.reply_to_email
                if not effective_reply_to:
                    effective_reply_to = smtp_account.from_email or inbox.email

                if provider in ('brevo', 'ses_api'):
                    # Use the configured API sender — include List-Unsubscribe for deliverability.
                    from services.resend_service import get_resend_service
                    api_sender = get_resend_service()
                    if not api_sender:
                        raise ValueError("Resend API not configured. Set RESEND_API_KEY in .env")
                    send_headers = {}
                    import re as _re
                    unsub_match = _re.search(r'href=["\']([^"\']*?/unsubscribe/[^"\']+)["\']', rendered_html or '')
                    if unsub_match:
                        unsub_url = unsub_match.group(1)
                        send_headers['List-Unsubscribe'] = f'<{unsub_url}>'
                        send_headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
                    resp = api_sender.send_email_sync(
                        to_email=recipient.email,
                        to_name=None,
                        from_email=smtp_account.from_email,
                        from_name=sender_context.get('from_name') or None,
                        subject=rendered_subject,
                        html_content=rendered_html or '',
                        text_content=rendered_text or 'This email requires an HTML-capable email client.',
                        reply_to=effective_reply_to,
                        headers=send_headers if send_headers else None,
                    )
                    if not resp.get('success'):
                        raise Exception(f"Resend send failed: {resp.get('error')}")
                else:
                    # Use standard SMTP
                    smtp_service = SMTPService(smtp_account=smtp_account)
                    smtp_service.send_email(
                        to_email=recipient.email,
                        subject=rendered_subject,
                        body_html=rendered_html,
                        body_text=rendered_text,
                        from_name=sender_context.get('from_name') or None,
                        reply_to=effective_reply_to,
                        attachments=attachment_list
                    )
                
                # Update success
                campaign_recipient.status = RecipientSendStatus.SENT
                campaign_recipient.sent_at = datetime.now(timezone.utc)
                sent_count += 1
                
                # Track rate limit usage
                self.rate_limiter.record_send(f"inbox:{inbox.id}")
                
                # Human-like delay between sends (1-3 seconds)
                time.sleep(random.uniform(1.0, 3.0))
                
            except Exception as e:
                campaign_recipient.status = RecipientSendStatus.FAILED
                campaign_recipient.error_message = str(e)
                failed_count += 1
        
        # Update campaign progress
        progress = campaign.send_progress or {"total": 0, "sent": 0, "failed": 0, "pending": 0}
        progress["sent"] = progress.get("sent", 0) + sent_count
        progress["failed"] = progress.get("failed", 0) + failed_count
        progress["pending"] = progress.get("pending", 0) - sent_count - failed_count
        campaign.send_progress = progress
        
        # Update campaign stats
        campaign.total_sent = progress["sent"]
        campaign.total_failed = progress["failed"]
        
        self.db.commit()
        
        return {
            "sent": sent_count,
            "failed": failed_count,
            "remaining": progress["pending"],
            "status": "running"
        }
    
    # =========================================================================
    # Statistics & Tracking
    # =========================================================================
    
    def get_campaign_stats(self, campaign_id: int) -> Dict[str, Any]:
        """Get detailed campaign statistics."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return {}
        
        # Count by status
        status_counts = self.db.query(
            CampaignRecipient.status,
            func.count(CampaignRecipient.id)
        ).filter(
            CampaignRecipient.campaign_id == campaign_id
        ).group_by(CampaignRecipient.status).all()
        
        counts = {str(row[0].value): row[1] for row in status_counts}
        
        return {
            "campaign_id": campaign_id,
            "name": campaign.name,
            "subject": campaign.subject,
            "body_html": campaign.body_html,
            "body_text": campaign.body_text,
            "attachments": campaign.attachments,
            "status": campaign.status.value,
            "reply_to_email": campaign.reply_to_email,
            "inbox_ids": campaign.inbox_ids or [],
            "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
            "progress": campaign.send_progress,
            "recipient_counts": counts,
            "total_recipients": campaign.total_recipients,
            "total_sent": campaign.total_sent,
            "total_failed": campaign.total_failed,
            "total_replies": campaign.total_replies,
            "opens": campaign.total_opens,
            "clicks": campaign.total_clicks,
            "unsubscribes": campaign.total_unsubscribes,
            "bounces": campaign.total_bounces
        }
    
    def record_open(self, campaign_id: int, recipient_id: int) -> bool:
        """Record an email open from tracking pixel."""
        campaign_recipient = self.db.query(CampaignRecipient).filter(
            and_(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.recipient_id == recipient_id
            )
        ).first()
        
        if campaign_recipient:
            campaign_recipient.opened_at = campaign_recipient.opened_at or datetime.now(timezone.utc)
            campaign_recipient.open_count = (campaign_recipient.open_count or 0) + 1
            
            # Update campaign total
            self.db.query(Campaign).filter(Campaign.id == campaign_id).update(
                {Campaign.total_opens: Campaign.total_opens + 1}
            )
            
            self.db.commit()
            return True
        
        return False
    
    def record_click(
        self,
        campaign_id: int,
        recipient_id: int,
        url: str
    ) -> bool:
        """Record a link click from tracking redirect."""
        campaign_recipient = self.db.query(CampaignRecipient).filter(
            and_(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.recipient_id == recipient_id
            )
        ).first()
        
        if campaign_recipient:
            campaign_recipient.clicked_at = campaign_recipient.clicked_at or datetime.now(timezone.utc)
            campaign_recipient.click_count = (campaign_recipient.click_count or 0) + 1
            
            # Store clicked URLs
            clicked_urls = campaign_recipient.clicked_urls or []
            if url not in clicked_urls:
                clicked_urls.append(url)
                campaign_recipient.clicked_urls = clicked_urls
            
            # Update campaign total
            self.db.query(Campaign).filter(Campaign.id == campaign_id).update(
                {Campaign.total_clicks: Campaign.total_clicks + 1}
            )
            
            self.db.commit()
            return True
        
        return False


# =============================================================================
# Campaign Scheduler Worker
# =============================================================================

def campaign_worker(db_session_factory):
    """
    Background worker that processes scheduled campaigns.
    
    Run this as a background task:
    - Checks for scheduled campaigns ready to start
    - Processes batches for running campaigns
    - Respects rate limits
    """
    import time
    
    while True:
        try:
            db = db_session_factory()
            try:
                service = CampaignService(db)
                
                # Check for scheduled campaigns
                now = datetime.now(timezone.utc)
                scheduled = db.query(Campaign).filter(
                    and_(
                        Campaign.status == CampaignStatus.SCHEDULED,
                        Campaign.scheduled_at <= now
                    )
                ).all()
                
                for campaign in scheduled:
                    service.start_campaign(campaign.id)
                
                # Process running campaigns
                running = db.query(Campaign).filter(
                    Campaign.status == CampaignStatus.RUNNING
                ).all()
                
                for campaign in running:
                    service.process_campaign_batch(campaign.id)
                    time.sleep(1)  # Small delay between campaigns
            finally:
                db.close()
        
        except Exception as e:
            print(f"Campaign worker error: {e}")
        
        # Wait before next cycle
        time.sleep(10)
