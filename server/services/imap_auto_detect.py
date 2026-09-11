"""
IMAP Auto-Detection Service
Automatically detects IMAP settings from SMTP host/credentials.
Uses well-known provider mappings + fallback discovery via common patterns.
"""

import imaplib
import ssl
import logging
import dns.resolver
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# Well-known SMTP → IMAP mappings
PROVIDER_MAP: Dict[str, Dict[str, Any]] = {
    # Google / Gmail / Google Workspace
    "smtp.gmail.com":        {"imap_host": "imap.gmail.com",        "imap_port": 993},
    "smtp.googlemail.com":   {"imap_host": "imap.gmail.com",        "imap_port": 993},
    "smtp-relay.gmail.com":  {"imap_host": "imap.gmail.com",        "imap_port": 993},
    
    # Microsoft / Outlook / Office365
    "smtp.outlook.com":      {"imap_host": "outlook.office365.com", "imap_port": 993},
    "smtp.office365.com":    {"imap_host": "outlook.office365.com", "imap_port": 993},
    "smtp-mail.outlook.com": {"imap_host": "outlook.office365.com", "imap_port": 993},
    "smtp.live.com":         {"imap_host": "imap-mail.outlook.com", "imap_port": 993},
    "smtp.hotmail.com":      {"imap_host": "imap-mail.outlook.com", "imap_port": 993},
    
    # Yahoo
    "smtp.mail.yahoo.com":   {"imap_host": "imap.mail.yahoo.com",  "imap_port": 993},
    "smtp.yahoo.com":        {"imap_host": "imap.mail.yahoo.com",  "imap_port": 993},
    
    # Zoho
    "smtp.zoho.com":         {"imap_host": "imap.zoho.com",         "imap_port": 993},
    "smtppro.zoho.com":      {"imap_host": "imappro.zoho.com",      "imap_port": 993},
    "smtp.zoho.eu":          {"imap_host": "imap.zoho.eu",          "imap_port": 993},
    "smtp.zoho.in":          {"imap_host": "imap.zoho.in",          "imap_port": 993},
    
    # iCloud / Apple
    "smtp.mail.me.com":      {"imap_host": "imap.mail.me.com",      "imap_port": 993},
    "smtp.icloud.com":       {"imap_host": "imap.mail.me.com",      "imap_port": 993},
    
    # AOL
    "smtp.aol.com":          {"imap_host": "imap.aol.com",          "imap_port": 993},
    
    # ProtonMail Bridge
    "127.0.0.1":             {"imap_host": "127.0.0.1",             "imap_port": 1143},
    
    # GoDaddy
    "smtpout.secureserver.net": {"imap_host": "imap.secureserver.net", "imap_port": 993},
    "relay-hosting.secureserver.net": {"imap_host": "imap.secureserver.net", "imap_port": 993},
    
    # Yandex
    "smtp.yandex.com":       {"imap_host": "imap.yandex.com",       "imap_port": 993},
    "smtp.yandex.ru":        {"imap_host": "imap.yandex.ru",        "imap_port": 993},
    
    # Mail.ru
    "smtp.mail.ru":          {"imap_host": "imap.mail.ru",          "imap_port": 993},
    
    # GMX
    "mail.gmx.com":          {"imap_host": "imap.gmx.com",          "imap_port": 993},
    "smtp.gmx.com":          {"imap_host": "imap.gmx.com",          "imap_port": 993},
    
    # Fastmail
    "smtp.fastmail.com":     {"imap_host": "imap.fastmail.com",      "imap_port": 993},
    
    # Namecheap
    "mail.privateemail.com": {"imap_host": "mail.privateemail.com",  "imap_port": 993},
    
    # hMailServer / generic
    "mail.hover.com":        {"imap_host": "mail.hover.com",         "imap_port": 993},
    
    # SendGrid (send-only, no IMAP)
    # We'll skip: "smtp.sendgrid.net"
    
    # Mailgun (send-only, no IMAP)
    # We'll skip: "smtp.mailgun.org"
    
    # Brevo / Sendinblue (send-only)
    # We'll skip: "smtp-relay.brevo.com"
}

# MX domain → IMAP provider mapping
# When MX records point to these domains, use the corresponding IMAP server
MX_TO_IMAP_MAP: Dict[str, Dict[str, Any]] = {
    # Google
    "google.com":           {"imap_host": "imap.gmail.com",           "imap_port": 993},
    "googlemail.com":       {"imap_host": "imap.gmail.com",           "imap_port": 993},
    "smtp.google.com":      {"imap_host": "imap.gmail.com",           "imap_port": 993},
    # Microsoft
    "outlook.com":          {"imap_host": "outlook.office365.com",    "imap_port": 993},
    "microsoft.com":        {"imap_host": "outlook.office365.com",    "imap_port": 993},
    "hotmail.com":          {"imap_host": "imap-mail.outlook.com",    "imap_port": 993},
    "protection.outlook.com": {"imap_host": "outlook.office365.com",  "imap_port": 993},
    # Zoho
    "zoho.com":             {"imap_host": "imap.zoho.com",            "imap_port": 993},
    "zoho.in":              {"imap_host": "imap.zoho.in",             "imap_port": 993},
    "zoho.eu":              {"imap_host": "imap.zoho.eu",             "imap_port": 993},
    # Yahoo
    "yahoodns.net":         {"imap_host": "imap.mail.yahoo.com",      "imap_port": 993},
    "yahoo.com":            {"imap_host": "imap.mail.yahoo.com",      "imap_port": 993},
    # Yandex
    "yandex.net":           {"imap_host": "imap.yandex.com",          "imap_port": 993},
    "yandex.ru":            {"imap_host": "imap.yandex.ru",           "imap_port": 993},
    # Fastmail
    "fastmail.com":         {"imap_host": "imap.fastmail.com",        "imap_port": 993},
    # ProtonMail
    "protonmail.ch":        {"imap_host": "127.0.0.1",               "imap_port": 1143},
    # GoDaddy
    "secureserver.net":     {"imap_host": "imap.secureserver.net",    "imap_port": 993},
    # Namecheap
    "privateemail.com":     {"imap_host": "mail.privateemail.com",    "imap_port": 993},
    "registrar-servers.com": {"imap_host": "mail.privateemail.com",   "imap_port": 993},
    # Hover
    "hover.com":            {"imap_host": "mail.hover.com",           "imap_port": 993},
    # Mail.ru
    "mail.ru":              {"imap_host": "imap.mail.ru",             "imap_port": 993},
    # Mimecast
    "mimecast.com":         {"imap_host": "outlook.office365.com",    "imap_port": 993},
    # Barracuda
    "barracudanetworks.com": {"imap_host": "outlook.office365.com",   "imap_port": 993},
}


def detect_imap_from_mx(email_address: str) -> Optional[Dict[str, Any]]:
    """
    Detect the correct IMAP server by looking up MX records for the email domain.
    This is the most reliable method for custom domains where SMTP host != MX host.
    Returns dict with imap_host and imap_port, or None if not detected.
    """
    try:
        domain = email_address.split("@")[-1].lower().strip()
        if not domain:
            return None
        
        mx_records = dns.resolver.resolve(domain, 'MX')
        mx_hosts = []
        for mx in mx_records:
            mx_host = str(mx.exchange).rstrip('.').lower()
            mx_hosts.append(mx_host)
            logger.debug(f"MX for {domain}: {mx.preference} {mx_host}")
        
        # Check each MX against our known provider map
        for mx_host in mx_hosts:
            # Check exact match
            if mx_host in MX_TO_IMAP_MAP:
                result = MX_TO_IMAP_MAP[mx_host].copy()
                logger.info(f"MX detection: {domain} -> MX {mx_host} -> IMAP {result['imap_host']}")
                return result
            
            # Check if MX ends with a known domain
            for known_domain, imap_info in MX_TO_IMAP_MAP.items():
                if mx_host.endswith("." + known_domain) or mx_host == known_domain:
                    result = imap_info.copy()
                    logger.info(f"MX detection: {domain} -> MX {mx_host} (matches *.{known_domain}) -> IMAP {result['imap_host']}")
                    return result
        
        logger.info(f"MX detection: no known provider match for {domain} (MX: {', '.join(mx_hosts)})")
        return None
        
    except dns.resolver.NXDOMAIN:
        logger.warning(f"MX detection: domain not found for {email_address}")
        return None
    except dns.resolver.NoAnswer:
        logger.warning(f"MX detection: no MX records for {email_address}")
        return None
    except Exception as e:
        logger.warning(f"MX detection failed for {email_address}: {e}")
        return None


# SMTP hosts that are send-only services (no IMAP)
SEND_ONLY_PROVIDERS = {
    "email-smtp.us-east-1.amazonaws.com",
    "email-smtp.us-west-2.amazonaws.com",
    "email-smtp.eu-west-1.amazonaws.com",
    "smtp.sendgrid.net",
    "smtp.mailgun.org",
    "smtp-relay.brevo.com",
    "smtp-relay.sendinblue.com",
    "smtp.postmarkapp.com",
    "smtp.sparkpostmail.com",
    "smtp.mandrillapp.com",
}


def detect_imap_from_smtp(smtp_host: str) -> Optional[Dict[str, Any]]:
    """
    Detect IMAP settings from SMTP host using well-known provider mappings.
    Returns dict with imap_host and imap_port, or None if unknown.
    """
    smtp_host_lower = smtp_host.lower().strip()
    
    # Check exact match first
    if smtp_host_lower in PROVIDER_MAP:
        return PROVIDER_MAP[smtp_host_lower].copy()
    
    # Check send-only providers
    if smtp_host_lower in SEND_ONLY_PROVIDERS:
        return None
    
    return None


def generate_imap_candidates(smtp_host: str) -> List[Tuple[str, int]]:
    """
    Generate possible IMAP host candidates from an SMTP host.
    Returns list of (host, port) tuples to try.
    """
    smtp_host_lower = smtp_host.lower().strip()
    candidates = []
    
    # Strategy 1: Replace "smtp" with "imap"
    if "smtp" in smtp_host_lower:
        imap_candidate = smtp_host_lower.replace("smtp", "imap", 1)
        candidates.append((imap_candidate, 993))
    
    # Strategy 1b: Replace "webmail" or "mail" with "imap"
    if smtp_host_lower.startswith("webmail."):
        candidates.append((smtp_host_lower.replace("webmail.", "imap.", 1), 993))
    
    # Strategy 2: Use "imap." + domain
    parts = smtp_host_lower.split(".", 1)
    if len(parts) == 2:
        domain = parts[1]
        candidates.append((f"imap.{domain}", 993))
        candidates.append((f"mail.{domain}", 993))
    
    # Strategy 3: Try the same host (many hosts serve both SMTP and IMAP)
    # This is critical for self-hosted servers (Zimbra, hMailServer, etc.)
    candidates.append((smtp_host_lower, 993))
    
    # Strategy 4: For "mail.domain.com", try "imap.domain.com"
    if smtp_host_lower.startswith("mail."):
        domain = smtp_host_lower[5:]
        candidates.append((f"imap.{domain}", 993))
    
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    
    return unique


def test_imap_connection(
    host: str, port: int, username: str, password: str, timeout: int = 10
) -> Dict[str, Any]:
    """
    Test if IMAP connection works with given credentials.
    Returns success status and details.
    """
    try:
        if port == 993:
            context = ssl.create_default_context()
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
            # Try STARTTLS for non-SSL ports
            try:
                context = ssl.create_default_context()
                conn.starttls(ssl_context=context)
            except Exception:
                pass  # Some servers don't support STARTTLS
        
        conn.login(username, password)
        
        # Try to select INBOX to verify full access
        status, _ = conn.select("INBOX")
        inbox_ok = status == "OK"
        
        conn.logout()
        
        return {
            "success": True,
            "host": host,
            "port": port,
            "inbox_accessible": inbox_ok,
            "message": f"IMAP connection successful to {host}:{port}"
        }
    except imaplib.IMAP4.error as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "message": f"IMAP auth failed: {str(e)}"
        }
    except (ConnectionRefusedError, OSError, TimeoutError) as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "message": f"Cannot connect to {host}:{port} - {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "host": host,
            "port": port,
            "message": f"IMAP error: {str(e)}"
        }


def auto_detect_imap(
    smtp_host: str, smtp_username: str, smtp_password: str
) -> Dict[str, Any]:
    """
    Auto-detect IMAP settings from SMTP credentials.
    
    1. First checks well-known provider mappings
    2. Then generates candidates and tests each one
    
    Returns dict with:
        - detected: bool
        - imap_host: str (if detected)
        - imap_port: int (if detected)
        - imap_username: str (same as smtp_username)
        - imap_password: str (same as smtp_password)
        - method: str (how it was detected)
        - message: str
    """
    result = {
        "detected": False,
        "imap_host": None,
        "imap_port": None,
        "imap_username": smtp_username,
        "imap_password": smtp_password,
        "method": None,
        "message": ""
    }
    
    # Step 1: Check well-known provider map
    known = detect_imap_from_smtp(smtp_host)
    if known:
        # Test the known mapping
        test = test_imap_connection(
            known["imap_host"], known["imap_port"],
            smtp_username, smtp_password, timeout=10
        )
        if test["success"]:
            result.update({
                "detected": True,
                "imap_host": known["imap_host"],
                "imap_port": known["imap_port"],
                "method": "provider_map",
                "message": f"Detected via known provider: {known['imap_host']}:{known['imap_port']}"
            })
            return result
        else:
            # Known provider but credentials failed - still return the host info
            # The SMTP and IMAP might need different passwords (e.g., app passwords)
            result.update({
                "detected": True,
                "imap_host": known["imap_host"],
                "imap_port": known["imap_port"],
                "method": "provider_map_untested",
                "message": f"IMAP host detected ({known['imap_host']}) but login failed: {test['message']}. "
                           f"You may need a separate app password for IMAP."
            })
            return result
    
    # Step 2: Check MX records for the email domain
    # This catches cases where SMTP host != where replies go (e.g. Zimbra SMTP but Zoho MX)
    email_domain_imap = detect_imap_from_mx(smtp_username)
    if email_domain_imap:
        # MX says replies go to a known provider - test it
        test = test_imap_connection(
            email_domain_imap["imap_host"], email_domain_imap["imap_port"],
            smtp_username, smtp_password, timeout=10
        )
        if test["success"]:
            result.update({
                "detected": True,
                "imap_host": email_domain_imap["imap_host"],
                "imap_port": email_domain_imap["imap_port"],
                "method": "mx_record",
                "message": f"Detected via MX records: {email_domain_imap['imap_host']}:{email_domain_imap['imap_port']} "
                           f"(replies are delivered here, not to {smtp_host})"
            })
            return result
        else:
            # MX points to a known provider but credentials failed
            # Still return it - the IMAP host is correct, password might differ
            logger.info(
                f"MX detection found {email_domain_imap['imap_host']} for {smtp_username} "
                f"but login failed: {test['message']}. Will also try SMTP-based candidates."
            )
            # Store as fallback - we'll return this if SMTP-based detection also fails
            mx_fallback = email_domain_imap.copy()
            mx_fallback["test_message"] = test["message"]
    else:
        mx_fallback = None
    
    # Step 3: Check if send-only provider
    smtp_lower = smtp_host.lower().strip()
    if smtp_lower in SEND_ONLY_PROVIDERS:
        # Even for send-only, if MX detection found something, use that
        if mx_fallback:
            result.update({
                "detected": True,
                "imap_host": mx_fallback["imap_host"],
                "imap_port": mx_fallback["imap_port"],
                "method": "mx_record_untested",
                "message": f"{smtp_host} is send-only, but MX points to {mx_fallback['imap_host']}. "
                           f"Login failed: {mx_fallback['test_message']}. You may need the IMAP password for this provider."
            })
            return result
        result["message"] = f"{smtp_host} is a send-only service (no IMAP available)"
        result["method"] = "send_only"
        return result
    
    # Step 4: Try candidate hosts (SMTP-based guessing)
    candidates = generate_imap_candidates(smtp_host)
    for host, port in candidates:
        test = test_imap_connection(host, port, smtp_username, smtp_password, timeout=8)
        if test["success"]:
            result.update({
                "detected": True,
                "imap_host": host,
                "imap_port": port,
                "method": "auto_discovery",
                "message": f"Auto-discovered IMAP: {host}:{port}"
            })
            return result
    
    # Nothing worked via SMTP candidates
    # If MX detection found a host (but login failed), return that as best guess
    if mx_fallback:
        result.update({
            "detected": True,
            "imap_host": mx_fallback["imap_host"],
            "imap_port": mx_fallback["imap_port"],
            "method": "mx_record_untested",
            "message": f"MX records point to {mx_fallback['imap_host']} but login failed: {mx_fallback['test_message']}. "
                       f"Replies will be delivered to this server - you may need separate IMAP credentials."
        })
        return result
    
    tried_hosts = ", ".join(f"{h}:{p}" for h, p in candidates)
    result["message"] = f"Could not auto-detect IMAP. Tried: {tried_hosts}"
    result["method"] = "failed"
    return result


def auto_detect_and_save_imap(smtp_account, db) -> Dict[str, Any]:
    """
    Auto-detect IMAP settings for an SMTP account and save to DB.
    Also updates any linked inboxes.
    """
    from models.smtp_account import SMTPAccount
    from models.inbox import Inbox
    
    detection = auto_detect_imap(
        smtp_host=smtp_account.host,
        smtp_username=smtp_account.username,
        smtp_password=smtp_account.password
    )
    
    if detection["detected"]:
        # Update SMTP account with IMAP settings
        smtp_account.imap_host = detection["imap_host"]
        smtp_account.imap_port = detection["imap_port"]
        smtp_account.imap_username = detection["imap_username"]
        smtp_account.imap_password = detection["imap_password"]
        
        # Also update any linked inboxes that don't have IMAP configured
        linked_inboxes = db.query(Inbox).filter(
            Inbox.smtp_account_id == smtp_account.id
        ).all()
        
        inboxes_updated = 0
        for inbox in linked_inboxes:
            if not inbox.has_imap:
                inbox.imap_host = detection["imap_host"]
                inbox.imap_port = detection["imap_port"]
                inbox.imap_username = detection["imap_username"]
                inbox.imap_password = detection["imap_password"]
                inbox.reply_enabled = True
                inboxes_updated += 1
        
        db.commit()
        
        logger.info(
            f"IMAP auto-detected for SMTP '{smtp_account.name}': "
            f"{detection['imap_host']}:{detection['imap_port']} "
            f"(method: {detection['method']}, inboxes updated: {inboxes_updated})"
        )
        
        detection["inboxes_updated"] = inboxes_updated
    else:
        logger.warning(
            f"IMAP auto-detection failed for SMTP '{smtp_account.name}' "
            f"({smtp_account.host}): {detection['message']}"
        )
        detection["inboxes_updated"] = 0
    
    return detection
