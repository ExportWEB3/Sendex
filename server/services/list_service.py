"""
Day 7: CSV Import Service
- Parse CSV files
- Detect duplicates
- Bulk import recipients
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from models.recipient import Recipient
from models.list import RecipientList, RecipientTag, ImportJob, RecipientStatus

logger = logging.getLogger(__name__)


# Expected CSV columns
REQUIRED_COLUMNS = ['email']
OPTIONAL_COLUMNS = ['first_name', 'last_name', 'company', 'title']
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


class CSVImportService:
    """Service for importing recipients from CSV"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def parse_csv(self, content: str) -> Tuple[List[Dict], List[str]]:
        """
        Parse CSV content and return rows + errors
        Returns: (rows, errors)
        """
        rows = []
        errors = []
        
        try:
            # Try to detect delimiter
            sample = content[:1024]
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel  # Default to comma
        
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        
        # Normalize column names (lowercase, strip whitespace)
        if reader.fieldnames:
            reader.fieldnames = [f.lower().strip().replace(' ', '_') for f in reader.fieldnames]
        
        # Check for required columns
        if not reader.fieldnames or 'email' not in reader.fieldnames:
            errors.append("CSV must have an 'email' column")
            return [], errors
        
        for i, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
            try:
                email = row.get('email', '').strip().lower()
                
                if not email:
                    errors.append(f"Row {i}: Empty email")
                    continue
                
                if not self._is_valid_email(email):
                    errors.append(f"Row {i}: Invalid email format '{email}'")
                    continue
                
                parsed_row = {
                    'email': email,
                    'first_name': row.get('first_name', '').strip() or None,
                    'last_name': row.get('last_name', '').strip() or None,
                    'company': row.get('company', '').strip() or None,
                    'title': row.get('title', '').strip() or None,
                }
                rows.append(parsed_row)
                
            except Exception as e:
                errors.append(f"Row {i}: Parse error - {str(e)}")
        
        return rows, errors
    
    def _is_valid_email(self, email: str) -> bool:
        """Basic email validation"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    def check_duplicates(self, emails: List[str], list_id: int = None) -> Dict[str, int]:
        """
        Check which emails already exist
        Returns: dict of email -> existing recipient ID
        """
        duplicates = {}
        
        # Check in batches for efficiency
        batch_size = 500
        for i in range(0, len(emails), batch_size):
            batch = emails[i:i + batch_size]
            
            query = self.db.query(Recipient.id, Recipient.email).filter(
                Recipient.email.in_(batch)
            )
            
            # If importing to a specific list, only check within that list
            if list_id:
                query = query.filter(Recipient.list_id == list_id)
            
            existing = query.all()
            
            for recipient_id, email in existing:
                duplicates[email] = recipient_id
        
        return duplicates
    
    def import_recipients(
        self,
        rows: List[Dict],
        list_id: int = None,
        tag_ids: List[int] = None,
        handle_duplicates: str = "flag"  # "flag", "skip", "update"
    ) -> Dict[str, Any]:
        """
        Import parsed rows into database
        
        Args:
            rows: Parsed CSV rows
            list_id: Optional list to add recipients to
            tag_ids: Optional tags to apply
            handle_duplicates: How to handle duplicates
                - "flag": Import with needs_review status
                - "skip": Don't import duplicates
                - "update": Update existing records
        
        Returns: Import stats
        """
        stats = {
            "total": len(rows),
            "imported": 0,
            "duplicates": 0,
            "updated": 0,
            "skipped": 0,
            "errors": []
        }
        
        # Check for duplicates
        emails = [r['email'] for r in rows]
        existing = self.check_duplicates(emails, list_id)
        
        # Get tags if specified
        tags = []
        if tag_ids:
            tags = self.db.query(RecipientTag).filter(RecipientTag.id.in_(tag_ids)).all()
        
        for row in rows:
            email = row['email']
            
            try:
                if email in existing:
                    stats["duplicates"] += 1
                    
                    if handle_duplicates == "skip":
                        stats["skipped"] += 1
                        continue
                    
                    elif handle_duplicates == "update":
                        # Update existing record
                        recipient = self.db.query(Recipient).filter(
                            Recipient.id == existing[email]
                        ).first()
                        
                        if recipient:
                            recipient.first_name = row['first_name'] or recipient.first_name
                            recipient.last_name = row['last_name'] or recipient.last_name
                            recipient.company = row['company'] or recipient.company
                            recipient.title = row['title'] or recipient.title
                            
                            # Add tags
                            for tag in tags:
                                if tag not in recipient.tags_rel:
                                    recipient.tags_rel.append(tag)
                            
                            stats["updated"] += 1
                        continue
                    
                    elif handle_duplicates == "flag":
                        # Import as duplicate needing review
                        recipient = Recipient(
                            email=email,
                            first_name=row['first_name'],
                            last_name=row['last_name'],
                            company=row['company'],
                            title=row['title'],
                            list_id=list_id,
                            status=RecipientStatus.NEEDS_REVIEW,
                            is_duplicate=True,
                            duplicate_of_id=existing[email]
                        )
                        self.db.add(recipient)
                        
                        # Add tags
                        for tag in tags:
                            recipient.tags_rel.append(tag)
                        
                        stats["imported"] += 1
                        continue
                
                # New recipient
                recipient = Recipient(
                    email=email,
                    first_name=row['first_name'],
                    last_name=row['last_name'],
                    company=row['company'],
                    title=row['title'],
                    list_id=list_id,
                    status=RecipientStatus.ACTIVE
                )
                self.db.add(recipient)
                
                # Add tags
                for tag in tags:
                    recipient.tags_rel.append(tag)
                
                stats["imported"] += 1
                
            except Exception as e:
                stats["errors"].append(f"Error importing {email}: {str(e)}")
        
        # Commit all changes
        self.db.commit()
        
        # Update list stats
        if list_id:
            self._update_list_stats(list_id)
        
        # Update tag counts
        for tag in tags:
            self._update_tag_count(tag.id)
        
        return stats
    
    def _update_list_stats(self, list_id: int):
        """Update recipient counts for a list"""
        
        recipient_list = self.db.query(RecipientList).filter(
            RecipientList.id == list_id
        ).first()
        
        if recipient_list:
            recipient_list.recipient_count = self.db.query(Recipient).filter(
                Recipient.list_id == list_id
            ).count()
            
            recipient_list.active_count = self.db.query(Recipient).filter(
                Recipient.list_id == list_id,
                Recipient.status == RecipientStatus.ACTIVE
            ).count()
            
            recipient_list.unsubscribed_count = self.db.query(Recipient).filter(
                Recipient.list_id == list_id,
                Recipient.status == RecipientStatus.UNSUBSCRIBED
            ).count()
            
            recipient_list.bounced_count = self.db.query(Recipient).filter(
                Recipient.list_id == list_id,
                Recipient.status == RecipientStatus.BOUNCED
            ).count()
            
            self.db.commit()
    
    def _update_tag_count(self, tag_id: int):
        """Update recipient count for a tag"""
        
        tag = self.db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
        if tag:
            tag.recipient_count = len(tag.recipients)
            self.db.commit()
    
    def export_list_to_csv(self, list_id: int = None, tag_id: int = None) -> str:
        """Export recipients to CSV string"""
        
        query = self.db.query(Recipient)
        
        if list_id:
            query = query.filter(Recipient.list_id == list_id)
        
        if tag_id:
            tag = self.db.query(RecipientTag).filter(RecipientTag.id == tag_id).first()
            if tag:
                query = query.filter(Recipient.id.in_([r.id for r in tag.recipients]))
        
        recipients = query.all()
        
        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow(['email', 'first_name', 'last_name', 'company', 'title', 'status'])
        
        # Data
        for r in recipients:
            writer.writerow([
                r.email,
                r.first_name or '',
                r.last_name or '',
                r.company or '',
                r.title or '',
                r.status.value if r.status else 'active'
            ])
        
        return output.getvalue()


class UnsubscribeService:
    """Service for handling unsubscribes"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def unsubscribe_by_token(self, token: str) -> Dict[str, Any]:
        """Unsubscribe a recipient by their unique token"""
        
        recipient = self.db.query(Recipient).filter(
            Recipient.unsubscribe_token == token
        ).first()
        
        if not recipient:
            return {
                "success": False,
                "message": "Invalid unsubscribe link"
            }
        
        if recipient.status == RecipientStatus.UNSUBSCRIBED:
            return {
                "success": True,
                "message": "You are already unsubscribed",
                "email": recipient.email
            }
        
        # Unsubscribe
        recipient.status = RecipientStatus.UNSUBSCRIBED
        recipient.is_suppressed = True
        recipient.suppression_reason = "Unsubscribed via link"
        recipient.unsubscribed_at = datetime.now(timezone.utc)
        
        self.db.commit()
        
        # Update list stats if in a list
        if recipient.list_id:
            CSVImportService(self.db)._update_list_stats(recipient.list_id)
        
        return {
            "success": True,
            "message": "Successfully unsubscribed",
            "email": recipient.email
        }
    
    def get_recipient_by_token(self, token: str) -> Optional[Recipient]:
        """Get recipient by unsubscribe token"""
        return self.db.query(Recipient).filter(
            Recipient.unsubscribe_token == token
        ).first()
