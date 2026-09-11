from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
import logging
import random
import math
import os
from dotenv import load_dotenv

from models.inbox import Inbox, InboxState, InboxGroup
from models.smtp_account import SMTPAccount
from services.warmup_email_generator import generate_warmup_email
from services.queue_service import email_queue, Priority

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration from .env
WARMUP_START_VOLUME = int(os.getenv("WARMUP_START_VOLUME", 5))
WARMUP_END_VOLUME = int(os.getenv("WARMUP_END_VOLUME", 100))
WARMUP_DAYS = int(os.getenv("WARMUP_DAYS", 30))
WARMUP_INFINITE = (
    os.getenv("WARMUP_INFINITE", "false").strip().lower() in {"1", "true", "yes", "on"}
    or WARMUP_DAYS <= 0
)
WARMUP_RAMP_DAYS = max(1, int(os.getenv("WARMUP_RAMP_DAYS", str(WARMUP_DAYS if WARMUP_DAYS > 0 else 30))))
WARMUP_LONGTAIL_GROWTH = float(os.getenv("WARMUP_LONGTAIL_GROWTH", 0.03))
WARMUP_LONGTAIL_STEP_DAYS = max(1, int(os.getenv("WARMUP_LONGTAIL_STEP_DAYS", 14)))
WARMUP_MAX_VOLUME = int(os.getenv("WARMUP_MAX_VOLUME", max(WARMUP_END_VOLUME, WARMUP_END_VOLUME * 3)))

# Warmup gets 30% of daily cap, campaigns get 70%
WARMUP_SHARE = float(os.getenv("WARMUP_SHARE", 0.30))


class WarmupService:
    """Service for managing inbox warm-up process"""
    
    # Group stagger delays (days)
    GROUP_STAGGER = {
        InboxGroup.A: 0,   # Starts immediately
        InboxGroup.B: 2,   # Starts 2 days later
        InboxGroup.C: 4,   # Starts 4 days later
    }
    
    def __init__(self, db: Session):
        self.db = db
    
    def calculate_daily_volume(self, warmup_day: int) -> int:
        """
        Calculate how many emails an inbox should send on a given warm-up day.

        Finite mode:
        - Progressive scaling from start to end volume across WARMUP_DAYS.
        - Hard cap at WARMUP_END_VOLUME.

        Infinite mode:
        - Progressive scaling to WARMUP_END_VOLUME across WARMUP_RAMP_DAYS.
        - Slow long-tail growth after ramp with configurable upper cap.
        """
        
        if warmup_day <= 0:
            return 0

        if not WARMUP_INFINITE and warmup_day >= WARMUP_DAYS:
            return WARMUP_END_VOLUME

        ramp_days = WARMUP_RAMP_DAYS if WARMUP_INFINITE else max(1, WARMUP_DAYS)
        upper_bound = WARMUP_MAX_VOLUME if WARMUP_INFINITE else WARMUP_END_VOLUME

        if warmup_day <= ramp_days:
            # Progressive scaling (roughly 5-8% daily increase)
            # Using exponential growth formula
            ratio = warmup_day / ramp_days
            # Smooth curve from start to end
            volume = WARMUP_START_VOLUME + (WARMUP_END_VOLUME - WARMUP_START_VOLUME) * (
                math.pow(ratio, 0.8)  # Slightly slower start, faster middle
            )
        else:
            # Infinite mode long-tail growth after ramp completion.
            extra_days = warmup_day - ramp_days
            growth_steps = extra_days / WARMUP_LONGTAIL_STEP_DAYS
            growth_multiplier = 1 + (growth_steps * WARMUP_LONGTAIL_GROWTH)
            volume = WARMUP_END_VOLUME * growth_multiplier
        
        volume = int(volume)
        
        # Ensure within bounds
        return max(WARMUP_START_VOLUME, min(upper_bound, volume))
    
    def get_stagger_delay(self, group: InboxGroup) -> int:
        """Get the stagger delay in days for a group"""
        return self.GROUP_STAGGER.get(group, 0)
    
    def start_warmup(self, inbox: Inbox) -> Dict[str, Any]:
        """Start warm-up for an inbox"""
        
        if inbox.state == InboxState.WARMING_UP:
            return {"success": False, "message": "Already warming up"}
        
        if inbox.state == InboxState.WARMED_UP:
            return {"success": False, "message": "Already warmed up"}
        
        # Calculate start date with group stagger
        stagger_days = self.get_stagger_delay(inbox.group)
        start_date = datetime.now(timezone.utc) + timedelta(days=stagger_days)
        
        # Update inbox
        inbox.state = InboxState.WARMING_UP
        inbox.warmup_start_date = start_date
        inbox.warmup_day = 0
        inbox.daily_cap = WARMUP_START_VOLUME
        inbox.current_daily_count = 0

        self.db.commit()

        partners_assigned = 0
        queued_initial = 0

        # Reduce first-send latency: assign partners immediately at start.
        try:
            from services.reply_service import PartnerService

            partner_svc = PartnerService(self.db)
            existing = partner_svc.get_partners(inbox)
            if not existing:
                partners_assigned = len(partner_svc.assign_partners(inbox))

            # If warmup starts immediately (no stagger), queue the first email now.
            if stagger_days == 0:
                partner_data = partner_svc.get_partners(inbox)
                partners = []
                for pd in partner_data:
                    if pd.get("is_on_cooldown"):
                        continue
                    pid = pd.get("partner_inbox_id")
                    if not pid:
                        continue
                    p_inbox = self.db.query(Inbox).filter(Inbox.id == pid).first()
                    if p_inbox and p_inbox.is_active:
                        partners.append(p_inbox)

                if partners:
                    q = self.queue_warmup_emails(inbox, partners, count=1)
                    queued_initial = q.get("queued", 0)
        except Exception as e:
            logger.warning(f"Immediate warmup bootstrap failed for {inbox.email}: {e}")

        return {
            "success": True,
            "message": f"Warm-up started for {inbox.email}",
            "start_date": start_date.isoformat(),
            "stagger_days": stagger_days,
            "group": inbox.group.value,
            "partners_assigned": partners_assigned,
            "queued_initial": queued_initial,
        }
    
    def pause_warmup(self, inbox: Inbox, reason: str = None) -> Dict[str, Any]:
        """Pause warm-up for an inbox"""
        
        if inbox.state != InboxState.WARMING_UP:
            return {"success": False, "message": "Not currently warming up"}
        
        inbox.state = InboxState.PAUSED
        inbox.pause_reason = reason
        
        self.db.commit()
        
        return {
            "success": True,
            "message": f"Warm-up paused for {inbox.email}",
            "reason": reason
        }
    
    def resume_warmup(self, inbox: Inbox) -> Dict[str, Any]:
        """Resume paused warm-up"""
        
        if inbox.state != InboxState.PAUSED:
            return {"success": False, "message": "Not currently paused"}
        
        inbox.state = InboxState.WARMING_UP
        inbox.pause_reason = None

        self.db.commit()

        partners_assigned = 0
        queued_initial = 0

        # On resume, ensure partners exist and queue one initial warmup email.
        try:
            from services.reply_service import PartnerService

            partner_svc = PartnerService(self.db)
            partner_data = partner_svc.get_partners(inbox)
            if not partner_data:
                partners_assigned = len(partner_svc.assign_partners(inbox))
                partner_data = partner_svc.get_partners(inbox)

            partners = []
            for pd in partner_data:
                if pd.get("is_on_cooldown"):
                    continue
                pid = pd.get("partner_inbox_id")
                if not pid:
                    continue
                p_inbox = self.db.query(Inbox).filter(Inbox.id == pid).first()
                if p_inbox and p_inbox.is_active:
                    partners.append(p_inbox)

            if partners:
                q = self.queue_warmup_emails(inbox, partners, count=1)
                queued_initial = q.get("queued", 0)
        except Exception as e:
            logger.warning(f"Resume warmup bootstrap failed for {inbox.email}: {e}")

        return {
            "success": True,
            "message": f"Warm-up resumed for {inbox.email}",
            "partners_assigned": partners_assigned,
            "queued_initial": queued_initial,
        }
    
    def stop_warmup(self, inbox: Inbox) -> Dict[str, Any]:
        """Stop warm-up completely"""
        
        inbox.state = InboxState.NOT_STARTED
        inbox.warmup_start_date = None
        inbox.warmup_day = 0
        inbox.daily_cap = WARMUP_START_VOLUME
        
        self.db.commit()
        
        return {
            "success": True,
            "message": f"Warm-up stopped for {inbox.email}"
        }
    
    def update_warmup_day(self, inbox: Inbox) -> Dict[str, Any]:
        """Update warm-up day and daily cap for an inbox"""
        
        if inbox.state != InboxState.WARMING_UP:
            return {"success": False, "message": "Not in warming up state"}
        
        if not inbox.warmup_start_date:
            return {"success": False, "message": "No start date set"}
        
        # Calculate current warm-up day using CALENDAR dates (not elapsed hours)
        now = datetime.now(timezone.utc)
        start_date = inbox.warmup_start_date
        
        # Make both dates timezone-aware for comparison
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        
        # Use calendar date difference, not timedelta.days (which needs full 24h)
        # e.g. started March 15 at 11PM, now March 16 at 8AM = day 2
        days_elapsed = (now.date() - start_date.date()).days
        
        if days_elapsed < 0:
            # Not started yet (staggered)
            return {
                "success": True,
                "message": "Warm-up not started yet (staggered)",
                "starts_in_days": abs(days_elapsed)
            }
        
        # Update warmup day
        new_day = days_elapsed + 1  # Day 1 is first day
        
        # Only reset daily count when the day actually changes
        if new_day != inbox.warmup_day:
            logger.info(
                f"Warmup day changed for {inbox.email}: "
                f"day {inbox.warmup_day} → {new_day}, "
                f"resetting current_daily_count ({inbox.current_daily_count} → 0)"
            )
            inbox.warmup_day = new_day
            inbox.daily_cap = self.calculate_daily_volume(inbox.warmup_day)
            inbox.current_daily_count = 0
        
        # Check if warm-up complete (finite mode only)
        if not WARMUP_INFINITE and inbox.warmup_day >= WARMUP_DAYS:
            inbox.state = InboxState.WARMED_UP
            inbox.daily_cap = WARMUP_END_VOLUME
            self.db.commit()
            
            return {
                "success": True,
                "message": f"Warm-up complete for {inbox.email}!",
                "state": InboxState.WARMED_UP.value,
                "final_daily_cap": inbox.daily_cap
            }
        
        self.db.commit()

        days_remaining = None if WARMUP_INFINITE else max(0, WARMUP_DAYS - inbox.warmup_day)
        
        return {
            "success": True,
            "warmup_day": inbox.warmup_day,
            "daily_cap": inbox.daily_cap,
            "days_remaining": days_remaining,
            "infinite": WARMUP_INFINITE,
        }
    
    def get_warmup_status(self, inbox: Inbox) -> Dict[str, Any]:
        """Get detailed warm-up status for an inbox"""
        
        status = {
            "inbox_id": inbox.id,
            "email": inbox.email,
            "group": inbox.group.value,
            "state": inbox.state.value,
            "warmup_day": inbox.warmup_day,
            "daily_cap": inbox.daily_cap,
            "current_daily_count": inbox.current_daily_count,
            "remaining_today": max(0, inbox.daily_cap - inbox.current_daily_count),
            "total_sent": inbox.total_sent,
            "total_received": inbox.total_received,
            "total_replied": inbox.total_replied,
        }
        
        if inbox.warmup_start_date:
            status["warmup_start_date"] = inbox.warmup_start_date.isoformat()
            status["days_elapsed"] = (datetime.now(timezone.utc) - inbox.warmup_start_date).days
            status["days_remaining"] = None if WARMUP_INFINITE else max(0, WARMUP_DAYS - inbox.warmup_day)
            progress_days = WARMUP_RAMP_DAYS if WARMUP_INFINITE else max(WARMUP_DAYS, 1)
            status["progress_percent"] = min(100, int((inbox.warmup_day / progress_days) * 100))
            status["infinite"] = WARMUP_INFINITE
            status["ramp_days"] = WARMUP_RAMP_DAYS
        
        if inbox.pause_reason:
            status["pause_reason"] = inbox.pause_reason
        
        return status
    
    def get_inboxes_needing_warmup_emails(self) -> List[Inbox]:
        """Get all inboxes that need warm-up emails sent today"""
        now = datetime.now(timezone.utc)
        
        return self.db.query(Inbox).filter(
            Inbox.state == InboxState.WARMING_UP,
            Inbox.is_active == True,
            Inbox.current_daily_count < Inbox.daily_cap,
            # Don't send warmup emails before stagger start date
            Inbox.warmup_start_date <= now
        ).all()
    
    def generate_warmup_emails_for_inbox(
        self,
        inbox: Inbox,
        partner_inboxes: List[Inbox],
        count: int = None
    ) -> List[Dict[str, Any]]:
        """Generate warm-up emails for an inbox to send to partner inboxes"""
        
        if not partner_inboxes:
            return []
        
        # Warmup only gets WARMUP_SHARE (30%) of daily cap — rest reserved for campaigns
        warmup_cap = max(1, int(inbox.daily_cap * WARMUP_SHARE))
        remaining = warmup_cap - inbox.current_daily_count
        to_send = min(count or remaining, remaining)
        
        if to_send <= 0:
            return []
        
        # Get SMTP account
        smtp = self.db.query(SMTPAccount).filter(
            SMTPAccount.id == inbox.smtp_account_id
        ).first()
        
        if not smtp:
            return []
        
        emails = []
        
        for i in range(to_send):
            # Pick random partner inbox
            partner = random.choice(partner_inboxes)
            
            # Generate email content
            email_content = generate_warmup_email(
                recipient_name=partner.email.split('@')[0],  # Use email prefix as name
                sender_name=smtp.from_name or inbox.email.split('@')[0]
            )
            
            # Create email data for queue
            email_data = {
                "smtp_account_id": smtp.id,
                "inbox_id": inbox.id,
                "to_email": partner.email,
                "to_name": None,
                "from_email": inbox.email,
                "from_name": smtp.from_name,
                "subject": email_content["subject"],
                "body_text": email_content["body_text"],
                "body_html": email_content["body_html"],
                "email_type": "warmup",
            }
            
            emails.append(email_data)
        
        return emails
    
    def queue_warmup_emails(
        self,
        inbox: Inbox,
        partner_inboxes: List[Inbox],
        count: int = None
    ) -> Dict[str, Any]:
        """Generate and queue warm-up emails for an inbox"""
        
        # Prevent double-queuing: check if we already queued for this inbox today
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        dedup_key = f"warmup:queued:{inbox.id}:{today_str}"
        try:
            already_queued = int(email_queue.redis.get(dedup_key) or 0)
            # Warmup only gets WARMUP_SHARE (30%) of daily cap — rest reserved for campaigns
            warmup_cap = max(1, int(inbox.daily_cap * WARMUP_SHARE))
            remaining_to_queue = warmup_cap - inbox.current_daily_count - already_queued
            if remaining_to_queue <= 0:
                return {
                    "success": False,
                    "message": "Already queued for today",
                    "queued": 0
                }
        except Exception:
            already_queued = 0
            warmup_cap = max(1, int(inbox.daily_cap * WARMUP_SHARE))
            remaining_to_queue = warmup_cap - inbox.current_daily_count
        
        emails = self.generate_warmup_emails_for_inbox(inbox, partner_inboxes, min(count, remaining_to_queue) if count else remaining_to_queue)
        
        if not emails:
            return {
                "success": False,
                "message": "No emails to send",
                "queued": 0
            }
        
        queued_ids = []
        
        for email_data in emails:
            # Add random delay between emails (1-10 minutes)
            delay = random.randint(60, 600)
            
            email_id = email_queue.add(
                email_data,
                priority=Priority.HIGH,  # Warm-up emails are high priority
                delay_seconds=delay * len(queued_ids)  # Stagger sends
            )
            queued_ids.append(email_id)
        
        # Track queued count in Redis (NOT in DB) to prevent double-queuing
        # current_daily_count only increments when worker actually sends
        try:
            email_queue.redis.incrby(dedup_key, len(queued_ids))
            email_queue.redis.expire(dedup_key, 86400)  # Auto-expire after 24h
        except Exception:
            pass
        
        return {
            "success": True,
            "queued": len(queued_ids),
            "email_ids": queued_ids,
            "inbox_daily_count": inbox.current_daily_count,
            "inbox_daily_cap": inbox.daily_cap
        }
    
    def get_partner_inboxes(self, inbox: Inbox, count: int = 5) -> List[Inbox]:
        """Get partner inboxes for warm-up (other active warming inboxes)"""
        
        # Get other active inboxes (not the same one)
        partners = self.db.query(Inbox).filter(
            Inbox.id != inbox.id,
            Inbox.is_active == True,
            Inbox.state.in_([InboxState.WARMING_UP, InboxState.WARMED_UP])
        ).limit(count * 2).all()
        
        # Shuffle and return requested count
        random.shuffle(partners)
        return partners[:count]
    
    def run_daily_warmup(self) -> Dict[str, Any]:
        """Run daily warm-up routine for all inboxes (called by scheduler)"""
        from services.reply_service import PartnerService
        
        results = {
            "processed": 0,
            "emails_queued": 0,
            "errors": [],
            "details": []
        }
        
        partner_svc = PartnerService(self.db)
        
        # Get all warming inboxes
        inboxes = self.get_inboxes_needing_warmup_emails()
        
        for inbox in inboxes:
            try:
                # Update warmup day first
                self.update_warmup_day(inbox)
                
                # Skip if paused or complete
                if inbox.state != InboxState.WARMING_UP:
                    continue
                
                # Use formal partners from DB (so reply system can match them)
                partner_data = partner_svc.get_partners(inbox)
                partners = []
                for pd in partner_data:
                    if pd.get("is_on_cooldown"):
                        continue
                    pid = pd.get("partner_inbox_id")
                    if pid:
                        p_inbox = self.db.query(Inbox).filter(Inbox.id == pid).first()
                        if p_inbox and p_inbox.is_active:
                            partners.append(p_inbox)
                
                # Fallback to ad-hoc if no formal partners yet
                if not partners:
                    partners = self.get_partner_inboxes(inbox)
                
                if not partners:
                    results["errors"].append(f"No partners for {inbox.email}")
                    continue
                
                # Queue emails
                queue_result = self.queue_warmup_emails(inbox, partners)
                
                # Record sends in partner tracking
                for p in partners:
                    try:
                        partner_svc.record_email_sent(inbox.id, p.id)
                    except Exception:
                        pass
                
                results["processed"] += 1
                results["emails_queued"] += queue_result.get("queued", 0)
                results["details"].append({
                    "inbox": inbox.email,
                    "queued": queue_result.get("queued", 0),
                    "day": inbox.warmup_day
                })
                
            except Exception as e:
                results["errors"].append(f"{inbox.email}: {str(e)}")
        
        return results
