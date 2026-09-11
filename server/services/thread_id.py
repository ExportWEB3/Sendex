"""
Thread ID System for Email Conversation Tracking
=================================================

Format: MMMMMMM-CCCCCCCC-NNN
  - MMMMMMM  : 7-char hex — unique per campaign send batch (generated once per campaign)
  - CCCCCCCC : 8-char hex — deterministic hash of recipient email (no DB lookup needed)
  - NNN      : 3-digit sequence — 000 = original send, 001 = first reply, 002 = second, etc.

The base thread ID (MMMMMMM-CCCCCCCC) is stored on CampaignRecipient.thread_id
The full thread ID (MMMMMMM-CCCCCCCC-NNN) is embedded in:
  - X-Thread-ID header on outgoing emails
  - Used for reply matching and dedup on incoming replies
"""

import hashlib
import os
import re


def generate_campaign_batch_id(campaign_id: int = None) -> str:
    """
    Generate a 7-char hex batch ID for a campaign.
    If campaign_id is provided, generates a deterministic ID (no storage needed).
    Otherwise generates a random one.
    """
    if campaign_id is not None:
        return hashlib.md5(f"campaign-thread-{campaign_id}".encode()).hexdigest()[:7]
    return os.urandom(4).hex()[:7]


def hash_recipient_email(email: str) -> str:
    """Generate a deterministic 8-char hex hash of a recipient email address."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:8]


def make_thread_id(batch_id: str, recipient_email: str) -> str:
    """
    Generate the base thread ID: MMMMMMM-CCCCCCCC
    This is stored on CampaignRecipient.thread_id
    """
    return f"{batch_id}-{hash_recipient_email(recipient_email)}"


def make_full_thread_id(base_thread_id: str, sequence: int) -> str:
    """
    Generate the full thread ID with sequence: MMMMMMM-CCCCCCCC-NNN
    sequence=0 for original send, 1 for first auto-reply, etc.
    """
    return f"{base_thread_id}-{sequence:03d}"


def parse_thread_id(full_thread_id: str) -> dict:
    """
    Parse a full thread ID into components.
    Returns {"batch_id": str, "contact_hash": str, "sequence": int} or None if invalid.
    """
    if not full_thread_id:
        return None
    match = re.match(r'^([a-f0-9]{7})-([a-f0-9]{8})-(\d{3})$', full_thread_id)
    if not match:
        return None
    return {
        "batch_id": match.group(1),
        "contact_hash": match.group(2),
        "sequence": int(match.group(3)),
        "base": f"{match.group(1)}-{match.group(2)}",
    }


def extract_thread_id_from_references(references: str) -> str:
    """
    Extract X-Thread-ID from References or In-Reply-To headers.
    Our Message-IDs contain the thread ID: <MMMMMMM-CCCCCCCC-NNN@domain>
    
    Returns the full thread ID (MMMMMMM-CCCCCCCC-NNN) or None.
    """
    if not references:
        return None
    # Look for our thread ID pattern in message IDs within angle brackets
    pattern = r'<([a-f0-9]{7}-[a-f0-9]{8}-\d{3})@'
    match = re.search(pattern, references)
    if match:
        return match.group(1)
    return None
