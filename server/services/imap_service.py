import imaplib
import email
from email.header import decode_header
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
import re

logger = logging.getLogger(__name__)


class IMAPService:
    """Service for reading emails via IMAP"""
    
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool = True
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.connection: Optional[imaplib.IMAP4] = None
    
    def connect(self, timeout: int = 15) -> bool:
        """Connect to IMAP server"""
        try:
            import socket
            socket.setdefaulttimeout(timeout)
            if self.use_ssl:
                self.connection = imaplib.IMAP4_SSL(self.host, self.port, timeout=timeout)
            else:
                self.connection = imaplib.IMAP4(self.host, self.port, timeout=timeout)
            
            self.connection.login(self.username, self.password)
            logger.info(f"Connected to IMAP: {self.host}")
            return True
            
        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"IMAP error: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from IMAP server"""
        if self.connection:
            try:
                self.connection.logout()
            except Exception:
                pass
            self.connection = None
    
    def select_folder(self, folder: str = "INBOX") -> bool:
        """Select a mail folder"""
        try:
            status, _ = self.connection.select(folder)
            return status == "OK"
        except Exception:
            return False
    
    def _decode_header_value(self, value: str) -> str:
        """Decode email header value"""
        if not value:
            return ""
        
        decoded_parts = decode_header(value)
        result = []
        
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or 'utf-8', errors='ignore'))
            else:
                result.append(part)
        
        return ''.join(result)
    
    def _parse_email(self, raw_email: bytes) -> Dict[str, Any]:
        """Parse raw email bytes into structured data"""
        msg = email.message_from_bytes(raw_email)
        
        # Get headers
        subject = self._decode_header_value(msg.get("Subject", ""))
        from_addr = self._decode_header_value(msg.get("From", ""))
        to_addr = self._decode_header_value(msg.get("To", ""))
        date_str = msg.get("Date", "")
        message_id = msg.get("Message-ID", "")
        in_reply_to = msg.get("In-Reply-To", "")
        references = msg.get("References", "")
        
        # Extract email address from "Name <email>" format
        from_email = self._extract_email(from_addr)
        to_email = self._extract_email(to_addr)
        
        # Get body
        body_text = ""
        body_html = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # Skip attachments
                if "attachment" in content_disposition:
                    continue
                
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        text = payload.decode(charset, errors='ignore')
                        
                        if content_type == "text/plain":
                            body_text = text
                        elif content_type == "text/html":
                            body_html = text
                except Exception:
                    pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or 'utf-8'
                    body_text = payload.decode(charset, errors='ignore')
            except Exception:
                pass
        
        return {
            "subject": subject,
            "from_email": from_email,
            "from_full": from_addr,
            "to_email": to_email,
            "to_full": to_addr,
            "date": date_str,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "body_text": body_text,
            "body_html": body_html,
            "is_reply": self._classify_is_reply(from_email, subject, in_reply_to),
            "is_bounce": self._classify_is_bounce(from_email, subject),
            "is_read_receipt": self._classify_is_read_receipt(subject),
        }
    
    @staticmethod
    def _classify_is_bounce(from_email: str, subject: str) -> bool:
        """Detect bounce/NDR emails (not real replies)"""
        from_lower = from_email.lower()
        subject_lower = subject.lower()
        
        bounce_senders = {'postmaster', 'mailer-daemon', 'mail-daemon', 'noreply', 'no-reply', 'bounce'}
        if any(from_lower.startswith(s) for s in bounce_senders):
            return True
        if 'mail delivery' in from_lower or 'delivery system' in from_lower:
            return True
        
        bounce_subjects = [
            'undeliverable', 'undelivered', 'delivery failed', 'delivery status',
            'failure notice', 'returned mail', 'mail delivery failed',
            'delivery notification', 'non-delivery', 'nicht zustellbar',
        ]
        if any(term in subject_lower for term in bounce_subjects):
            return True
        
        return False
    
    @staticmethod
    def _classify_is_read_receipt(subject: str) -> bool:
        """Detect read receipts (not real replies)"""
        subject_lower = subject.lower()
        read_patterns = ['read:', 'not read:', 'gelesen:', 'lu:', 'leído:']
        return any(subject_lower.startswith(p) for p in read_patterns)
    
    @staticmethod
    def _classify_is_reply(from_email: str, subject: str, in_reply_to: str) -> bool:
        """Detect real replies (excludes bounces and read receipts)"""
        from_lower = from_email.lower()
        subject_lower = subject.lower()
        
        # Exclude bounces
        bounce_senders = {'postmaster', 'mailer-daemon', 'mail-daemon', 'noreply', 'no-reply', 'bounce'}
        if any(from_lower.startswith(s) for s in bounce_senders):
            return False
        if 'mail delivery' in from_lower or 'delivery system' in from_lower:
            return False
        
        # Exclude bounce subjects
        bounce_subjects = [
            'undeliverable', 'undelivered', 'delivery failed', 'delivery status',
            'failure notice', 'returned mail', 'mail delivery failed',
            'delivery notification', 'non-delivery',
        ]
        if any(term in subject_lower for term in bounce_subjects):
            return False
        
        # Exclude read receipts
        read_patterns = ['read:', 'not read:', 'gelesen:', 'lu:', 'leído:']
        if any(subject_lower.startswith(p) for p in read_patterns):
            return False
        
        # Now check if it's actually a reply
        has_reply_indicator = bool(in_reply_to) or subject_lower.startswith("re:")
        return has_reply_indicator
    
    def _extract_email(self, full_address: str) -> str:
        """Extract email from 'Name <email@domain.com>' format"""
        match = re.search(r'<([^>]+)>', full_address)
        if match:
            return match.group(1).lower()
        # Already just an email
        if '@' in full_address:
            return full_address.strip().lower()
        return ""

    def fetch_reply_headers(
        self,
        folder: str = "INBOX",
        since_hours: int = 24,
        limit: int = 30
    ) -> List[Dict[str, Any]]:
        """Fast fetch — headers only, for reply detection. 10-20x faster than full RFC822."""
        
        if not self.connection:
            if not self.connect():
                return []
        
        if not self.select_folder(folder):
            return []
        
        since_date = (datetime.now() - timedelta(hours=since_hours)).strftime("%d-%b-%Y")
        
        try:
            status, message_ids = self.connection.uid('search', None, f'SINCE {since_date}')
            if status != "OK":
                return []
            
            ids = message_ids[0].split()
            ids = ids[-limit:] if len(ids) > limit else ids
            
            if not ids:
                return []
            
            # Fetch ONLY headers (From, To, Subject, Date, Message-ID, In-Reply-To, References, X-Thread-ID)
            # This is massively faster than RFC822 (full body)
            header_fields = "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES X-THREAD-ID)]"
            
            emails = []
            for msg_id in ids:
                try:
                    status, msg_data = self.connection.uid('fetch', msg_id, f"({header_fields})")
                    if status != "OK" or not msg_data[0]:
                        continue
                    
                    raw_headers = msg_data[0][1]
                    msg = email.message_from_bytes(raw_headers)
                    
                    from_decoded = self._decode_header_value(msg.get("From", ""))
                    subject_decoded = self._decode_header_value(msg.get("Subject", ""))
                    
                    in_reply_to = msg.get("In-Reply-To", "")
                    references = msg.get("References", "")
                    subject_lower = subject_decoded.lower().strip()
                    is_reply = bool(in_reply_to or references or subject_lower.startswith("re:") or subject_lower.startswith("re :"))
                    
                    to_decoded = self._decode_header_value(msg.get("To", ""))
                    
                    # Extract thread ID from X-Thread-ID header or from Message-ID/References
                    x_thread_id = msg.get("X-Thread-ID", "")
                    
                    emails.append({
                        "uid": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                        "from": from_decoded,
                        "from_email": self._extract_email(from_decoded),
                        "to": to_decoded,
                        "to_email": self._extract_email(to_decoded),
                        "subject": subject_decoded,
                        "date": msg.get("Date", ""),
                        "message_id": msg.get("Message-ID", ""),
                        "in_reply_to": in_reply_to,
                        "references": references,
                        "is_reply": is_reply,
                        "x_thread_id": x_thread_id.strip() if x_thread_id else "",
                    })
                except Exception as e:
                    logger.error(f"Error fetching header {msg_id}: {e}")
                    continue
            
            return emails
            
        except Exception as e:
            logger.error(f"Error in fetch_reply_headers: {e}")
            return []
    
    def fetch_email_body(self, uid: str, folder: str = "INBOX") -> Optional[Dict[str, str]]:
        """Fetch the body of a single email by UID. Returns {body_text, body_html} or None."""
        if not self.connection:
            if not self.connect():
                return None
        
        if not self.select_folder(folder):
            return None
        
        try:
            uid_bytes = uid.encode() if isinstance(uid, str) else uid
            status, msg_data = self.connection.uid('fetch', uid_bytes, "(RFC822)")
            if status != "OK" or not msg_data[0]:
                return None
            
            raw_email = msg_data[0][1]
            parsed = self._parse_email(raw_email)
            return {"body_text": parsed.get("body_text", ""), "body_html": parsed.get("body_html", "")}
        except Exception as e:
            logger.error(f"Error fetching email body for uid {uid}: {e}")
            return None

    def fetch_recent_emails(
        self,
        folder: str = "INBOX",
        since_hours: int = 24,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch recent emails from a folder"""
        
        if not self.connection:
            if not self.connect():
                return []
        
        if not self.select_folder(folder):
            return []
        
        # Calculate since date
        since_date = (datetime.now() - timedelta(hours=since_hours)).strftime("%d-%b-%Y")
        
        try:
            # Search for emails since date
            status, message_ids = self.connection.search(None, f'SINCE {since_date}')
            
            if status != "OK":
                return []
            
            ids = message_ids[0].split()
            
            # Get most recent (last N)
            ids = ids[-limit:] if len(ids) > limit else ids
            
            emails = []
            
            for msg_id in ids:
                try:
                    status, msg_data = self.connection.fetch(msg_id, "(RFC822)")
                    
                    if status == "OK" and msg_data[0]:
                        raw_email = msg_data[0][1]
                        parsed = self._parse_email(raw_email)
                        parsed["uid"] = msg_id.decode()
                        emails.append(parsed)
                except Exception as e:
                    logger.error(f"Error fetching email {msg_id}: {e}")
                    continue
            
            return emails
            
        except Exception as e:
            logger.error(f"Error searching emails: {e}")
            return []
    
    def fetch_unread_emails(self, folder: str = "INBOX", limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch unread emails"""
        
        if not self.connection:
            if not self.connect():
                return []
        
        if not self.select_folder(folder):
            return []
        
        try:
            status, message_ids = self.connection.search(None, "UNSEEN")
            
            if status != "OK":
                return []
            
            ids = message_ids[0].split()
            ids = ids[-limit:] if len(ids) > limit else ids
            
            emails = []
            
            for msg_id in ids:
                try:
                    status, msg_data = self.connection.fetch(msg_id, "(RFC822)")
                    
                    if status == "OK" and msg_data[0]:
                        raw_email = msg_data[0][1]
                        parsed = self._parse_email(raw_email)
                        parsed["uid"] = msg_id.decode()
                        emails.append(parsed)
                except Exception as e:
                    logger.error(f"Error fetching email {msg_id}: {e}")
                    continue
            
            return emails
            
        except Exception as e:
            logger.error(f"Error fetching unread: {e}")
            return []
    
    def mark_as_read(self, uid: str) -> bool:
        """Mark an email as read"""
        try:
            self.connection.store(uid.encode(), '+FLAGS', '\\Seen')
            return True
        except Exception:
            return False
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


def check_inbox_for_warmup_replies(
    host: str,
    port: int,
    username: str,
    password: str,
    warmup_emails: List[str]  # List of warmup inbox emails to look for
) -> List[Dict[str, Any]]:
    """
    Check inbox for replies from warmup partner inboxes.
    Returns list of warmup-related emails.
    """
    
    with IMAPService(host, port, username, password) as imap:
        emails = imap.fetch_recent_emails(since_hours=24)
        
        warmup_replies = []
        
        for email_data in emails:
            # Check if from a warmup partner
            if email_data["from_email"] in warmup_emails:
                warmup_replies.append(email_data)
        
        return warmup_replies
