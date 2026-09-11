from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
import random
import logging

from models.inbox import Inbox, InboxState
from models.warmup import WarmupThread, WarmupPartner, WarmupReply
from services.warmup_email_generator import generate_warmup_reply
from services.queue_service import email_queue, Priority
from services.imap_service import IMAPService

logger = logging.getLogger(__name__)


class ReplyService:
    """Service for managing warm-up replies"""
    
    # Reply delay range (in seconds)
    MIN_REPLY_DELAY = 2 * 60 * 60   # 2 hours
    MAX_REPLY_DELAY = 6 * 60 * 60   # 6 hours
    
    # Reply probability (not all emails get replies)
    REPLY_PROBABILITY = 0.7  # 70% chance to reply
    
    def __init__(self, db: Session):
        self.db = db
    
    def calculate_reply_delay(self) -> int:
        """Calculate random delay with bell curve distribution (most around 3-4 hours)"""
        
        # Use normal distribution centered around 4 hours
        mean = (self.MIN_REPLY_DELAY + self.MAX_REPLY_DELAY) / 2
        std_dev = (self.MAX_REPLY_DELAY - self.MIN_REPLY_DELAY) / 4
        
        delay = random.gauss(mean, std_dev)
        
        # Clamp to min/max
        delay = max(self.MIN_REPLY_DELAY, min(self.MAX_REPLY_DELAY, delay))
        
        return int(delay)
    
    def should_reply(self) -> bool:
        """Determine if we should reply to an email (probabilistic)"""
        return random.random() < self.REPLY_PROBABILITY
    
    def get_reply_type(self) -> str:
        """Get random reply type with weighted distribution"""
        
        types = [
            ("acknowledgment", 0.35),  # 35%
            ("question", 0.20),        # 20%
            ("agreement", 0.25),       # 25%
            ("detailed", 0.10),        # 10%
            ("clarification", 0.10),   # 10%
        ]
        
        rand = random.random()
        cumulative = 0
        
        for reply_type, probability in types:
            cumulative += probability
            if rand < cumulative:
                return reply_type
        
        return "acknowledgment"
    
    def schedule_reply(
        self,
        inbox: Inbox,
        to_email: str,
        original_subject: str,
        thread_id: Optional[int] = None
    ) -> Optional[WarmupReply]:
        """Schedule a reply to be sent later"""
        
        if not self.should_reply():
            logger.info(f"Skipping reply (probabilistic) for {inbox.email} to {to_email}")
            return None
        
        delay_seconds = self.calculate_reply_delay()
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        
        reply_type = self.get_reply_type()
        
        # Generate subject (add Re: if not present)
        if not original_subject.lower().startswith("re:"):
            subject = f"Re: {original_subject}"
        else:
            subject = original_subject
        
        reply = WarmupReply(
            thread_id=thread_id,
            inbox_id=inbox.id,
            to_email=to_email,
            from_email=inbox.email,
            subject=subject,
            original_subject=original_subject,
            reply_type=reply_type,
            scheduled_at=scheduled_at,
            status="pending"
        )
        
        self.db.add(reply)
        self.db.commit()
        
        logger.info(f"Reply scheduled: {inbox.email} -> {to_email} at {scheduled_at}")
        
        return reply
    
    def get_pending_replies(self) -> List[WarmupReply]:
        """Get replies that are due to be sent"""
        
        return self.db.query(WarmupReply).filter(
            WarmupReply.status == "pending",
            WarmupReply.scheduled_at <= datetime.now(timezone.utc)
        ).all()
    
    def process_pending_replies(self) -> Dict[str, Any]:
        """Process all pending replies that are due"""
        
        pending = self.get_pending_replies()
        results = {
            "processed": 0,
            "queued": 0,
            "errors": []
        }
        
        for reply in pending:
            try:
                # Get inbox
                inbox = self.db.query(Inbox).filter(Inbox.id == reply.inbox_id).first()
                
                if not inbox or inbox.state not in [InboxState.WARMING_UP, InboxState.WARMED_UP]:
                    reply.status = "failed"
                    reply.error_message = "Inbox not available"
                    continue
                
                # Generate reply content
                reply_content = generate_warmup_reply(
                    original_subject=reply.original_subject,
                    sender_name=inbox.email.split('@')[0]
                )
                
                # Queue the reply
                email_data = {
                    # CRITICAL: Use stable ID to prevent duplicates on resync/retry
                    "_id": f"warmup_reply:{reply.id}",
                    "smtp_account_id": inbox.smtp_account_id,
                    "inbox_id": inbox.id,
                    "to_email": reply.to_email,
                    "from_email": inbox.email,
                    "subject": reply.subject,
                    "body_text": reply_content["body_text"],
                    "body_html": reply_content["body_html"],
                    "email_type": "warmup_reply",
                }
                
                email_queue.add(email_data, Priority.HIGH)
                
                reply.status = "queued"
                results["queued"] += 1
                
            except Exception as e:
                reply.status = "failed"
                reply.error_message = str(e)
                results["errors"].append(str(e))
            
            results["processed"] += 1
        
        self.db.commit()
        return results
    
    def check_inbox_and_schedule_replies(self, inbox: Inbox) -> Dict[str, Any]:
        """Check an inbox for warmup emails and schedule replies"""
        
        if not inbox.reply_enabled or not inbox.imap_host or not inbox.imap_username:
            return {"success": False, "message": "Replies not enabled or IMAP not configured"}
        
        results = {
            "emails_found": 0,
            "replies_scheduled": 0,
            "errors": []
        }
        
        try:
            # Get partner inbox emails
            partners = self.db.query(WarmupPartner).filter(
                WarmupPartner.inbox_id == inbox.id,
                WarmupPartner.is_active == True
            ).all()
            
            partner_emails = [
                self.db.query(Inbox).filter(Inbox.id == p.partner_inbox_id).first().email
                for p in partners
            ]
            
            # Connect to IMAP
            with IMAPService(
                inbox.imap_host,
                inbox.imap_port,
                inbox.imap_username,
                inbox.imap_password
            ) as imap:
                emails = imap.fetch_recent_emails(since_hours=12)
                
                for email_data in emails:
                    # Only process emails from partners
                    if email_data["from_email"] not in partner_emails:
                        continue
                    
                    results["emails_found"] += 1
                    
                    # Check if we already scheduled a reply
                    existing = self.db.query(WarmupReply).filter(
                        WarmupReply.inbox_id == inbox.id,
                        WarmupReply.original_subject == email_data["subject"],
                        WarmupReply.to_email == email_data["from_email"]
                    ).first()
                    
                    if existing:
                        continue
                    
                    # Schedule reply
                    reply = self.schedule_reply(
                        inbox=inbox,
                        to_email=email_data["from_email"],
                        original_subject=email_data["subject"]
                    )
                    
                    if reply:
                        results["replies_scheduled"] += 1
            
            # Update inbox received count
            inbox.total_received += results["emails_found"]
            inbox.last_received_at = datetime.now(timezone.utc)
            self.db.commit()
            
        except Exception as e:
            results["errors"].append(str(e))
            logger.error(f"Error checking inbox {inbox.email}: {e}")
        
        return results


class PartnerService:
    """Service for managing warm-up partners"""
    
    PARTNERS_PER_INBOX = 5  # Default partners per inbox
    COOLDOWN_HOURS = 36     # Hours between emails to same partner
    ROTATION_DAYS = 4       # Days before rotating partners
    
    def __init__(self, db: Session):
        self.db = db
    
    def assign_partners(self, inbox: Inbox, count: int = None) -> List[WarmupPartner]:
        """Assign partner inboxes to an inbox"""
        
        count = count or self.PARTNERS_PER_INBOX
        
        # Get available inboxes (not self, active, warming/warmed)
        available = self.db.query(Inbox).filter(
            Inbox.id != inbox.id,
            Inbox.is_active == True,
            Inbox.state.in_([InboxState.WARMING_UP, InboxState.WARMED_UP])
        ).all()
        
        # Remove already assigned partners
        existing_ids = [
            p.partner_inbox_id for p in 
            self.db.query(WarmupPartner).filter(
                WarmupPartner.inbox_id == inbox.id,
                WarmupPartner.is_active == True
            ).all()
        ]
        
        available = [i for i in available if i.id not in existing_ids]
        
        # Shuffle and select
        random.shuffle(available)
        to_assign = available[:count]
        
        partners = []
        
        for partner_inbox in to_assign:
            partner = WarmupPartner(
                inbox_id=inbox.id,
                partner_inbox_id=partner_inbox.id,
                assigned_at=datetime.now(timezone.utc)
            )
            self.db.add(partner)
            partners.append(partner)
        
        self.db.commit()
        
        return partners
    
    def get_partners(self, inbox: Inbox) -> List[Dict[str, Any]]:
        """Get current partners for an inbox"""
        
        partners = self.db.query(WarmupPartner).filter(
            WarmupPartner.inbox_id == inbox.id,
            WarmupPartner.is_active == True
        ).all()
        
        result = []
        
        for p in partners:
            partner_inbox = self.db.query(Inbox).filter(Inbox.id == p.partner_inbox_id).first()
            
            result.append({
                "partner_id": p.id,
                "partner_inbox_id": p.partner_inbox_id,
                "partner_email": partner_inbox.email if partner_inbox else None,
                "emails_sent": p.emails_sent_count,
                "emails_received": p.emails_received_count,
                "last_email_sent": p.last_email_sent_at.isoformat() if p.last_email_sent_at else None,
                "cooldown_until": p.cooldown_until.isoformat() if p.cooldown_until else None,
                "is_on_cooldown": p.cooldown_until and p.cooldown_until > datetime.now(timezone.utc)
            })
        
        return result
    
    def get_available_partner(self, inbox: Inbox) -> Optional[Inbox]:
        """Get a partner that's not on cooldown"""
        
        partners = self.db.query(WarmupPartner).filter(
            WarmupPartner.inbox_id == inbox.id,
            WarmupPartner.is_active == True,
            or_(
                WarmupPartner.cooldown_until == None,
                WarmupPartner.cooldown_until < datetime.now(timezone.utc)
            )
        ).all()
        
        if not partners:
            return None
        
        # Pick random available partner
        partner = random.choice(partners)
        partner_inbox = self.db.query(Inbox).filter(Inbox.id == partner.partner_inbox_id).first()
        
        return partner_inbox
    
    def record_email_sent(self, inbox_id: int, partner_inbox_id: int):
        """Record that an email was sent to a partner"""
        
        partner = self.db.query(WarmupPartner).filter(
            WarmupPartner.inbox_id == inbox_id,
            WarmupPartner.partner_inbox_id == partner_inbox_id
        ).first()
        
        if partner:
            partner.last_email_sent_at = datetime.now(timezone.utc)
            partner.emails_sent_count += 1
            partner.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=self.COOLDOWN_HOURS)
            self.db.commit()
    
    def record_email_received(self, inbox_id: int, partner_inbox_id: int):
        """Record that an email was received from a partner"""
        
        partner = self.db.query(WarmupPartner).filter(
            WarmupPartner.inbox_id == inbox_id,
            WarmupPartner.partner_inbox_id == partner_inbox_id
        ).first()
        
        if partner:
            partner.last_email_received_at = datetime.now(timezone.utc)
            partner.emails_received_count += 1
            self.db.commit()
    
    def rotate_partners(self, inbox: Inbox) -> Dict[str, Any]:
        """Rotate partners for an inbox (deactivate old, assign new)"""
        
        # Deactivate current partners
        self.db.query(WarmupPartner).filter(
            WarmupPartner.inbox_id == inbox.id,
            WarmupPartner.is_active == True
        ).update({"is_active": False, "last_rotated_at": datetime.now(timezone.utc)})
        
        # Assign new partners
        new_partners = self.assign_partners(inbox)
        
        return {
            "rotated": True,
            "new_partner_count": len(new_partners)
        }
    
    def check_and_rotate_all(self) -> Dict[str, Any]:
        """Check all inboxes and rotate partners if needed"""
        
        results = {
            "checked": 0,
            "rotated": 0
        }
        
        # Get warming inboxes
        inboxes = self.db.query(Inbox).filter(
            Inbox.state == InboxState.WARMING_UP,
            Inbox.is_active == True
        ).all()
        
        for inbox in inboxes:
            results["checked"] += 1
            
            # Check oldest partner assignment
            oldest = self.db.query(WarmupPartner).filter(
                WarmupPartner.inbox_id == inbox.id,
                WarmupPartner.is_active == True
            ).order_by(WarmupPartner.assigned_at.asc()).first()
            
            if oldest:
                days_since = (datetime.now(timezone.utc) - oldest.assigned_at).days
                
                if days_since >= self.ROTATION_DAYS:
                    self.rotate_partners(inbox)
                    results["rotated"] += 1
        
        return results
