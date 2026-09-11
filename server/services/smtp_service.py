import smtplib
import ssl
import asyncio
import base64
import json
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import requests

from models.smtp_account import SMTPAccount, EncryptionType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OAuth2 token cache (in-memory, keyed by smtp_account_id)
# Tokens last ~60 min; we cache for 55 min to be safe.
# ---------------------------------------------------------------------------
_oauth2_token_cache: Dict[int, Dict[str, Any]] = {}
OAUTH2_TOKEN_TTL = 55 * 60  # 55 minutes


def _get_oauth2_access_token(smtp_account: SMTPAccount) -> str:
    """
    Fetch an OAuth2 access token for Microsoft 365 SMTP.
    Uses client_credentials flow (application permission).
    Caches the token in-memory for ~55 minutes.
    """
    account_id = smtp_account.id or 0
    cached = _oauth2_token_cache.get(account_id)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    client_id = smtp_account.oauth2_client_id
    client_secret = smtp_account.oauth2_client_secret
    tenant_id = smtp_account.oauth2_tenant_id

    if not all([client_id, client_secret, tenant_id]):
        raise SMTPConnectionError(
            "OAuth2 credentials incomplete — need client_id, client_secret, and tenant_id"
        )

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://outlook.office365.com/.default",
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        access_token = data["access_token"]
    except requests.RequestException as e:
        raise SMTPConnectionError(f"OAuth2 token request failed: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        raise SMTPConnectionError(f"OAuth2 token response invalid: {e}")

    # Cache
    _oauth2_token_cache[account_id] = {
        "token": access_token,
        "expires_at": time.time() + OAUTH2_TOKEN_TTL,
    }

    logger.info(f"OAuth2 token acquired for SMTP account {account_id}")
    return access_token


def _build_xoauth2_string(username: str, access_token: str) -> str:
    """Build the XOAUTH2 auth string for SMTP AUTH."""
    auth_string = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth_string.encode()).decode()


class SMTPConnectionError(Exception):
    """Custom exception for SMTP connection errors"""
    pass


class SMTPSendError(Exception):
    """Custom exception for SMTP send errors"""
    def __init__(self, message: str, error_code: Optional[str] = None, is_permanent: bool = False):
        self.message = message
        self.error_code = error_code
        self.is_permanent = is_permanent  # True = hard bounce, False = soft bounce
        super().__init__(self.message)


class SMTPService:
    """Service for managing SMTP connections and sending emails"""
    
    # Common bounce codes
    HARD_BOUNCE_CODES = ['550', '551', '552', '553', '554', '521']
    SOFT_BOUNCE_CODES = ['421', '450', '451', '452', '422', '441', '442']
    
    def __init__(self, smtp_account: SMTPAccount):
        self.smtp_account = smtp_account
        self.connection: Optional[smtplib.SMTP] = None
        
    def connect(self) -> bool:
        """Establish connection to SMTP server"""
        try:
            if self.smtp_account.encryption == EncryptionType.SSL:
                # SSL connection (usually port 465)
                context = ssl.create_default_context()
                self.connection = smtplib.SMTP_SSL(
                    self.smtp_account.host,
                    self.smtp_account.port,
                    context=context,
                    timeout=30
                )
            else:
                # Regular connection or STARTTLS (usually port 587 or 25)
                self.connection = smtplib.SMTP(
                    self.smtp_account.host,
                    self.smtp_account.port,
                    timeout=30
                )
                
                if self.smtp_account.encryption == EncryptionType.TLS:
                    context = ssl.create_default_context()
                    self.connection.starttls(context=context)
            
            # Authenticate based on auth_type
            auth_type = getattr(self.smtp_account, 'auth_type', 'password') or 'password'
            
            if auth_type == 'oauth2':
                # OAuth2 XOAUTH2 authentication (Microsoft 365 / Outlook)
                access_token = _get_oauth2_access_token(self.smtp_account)
                auth_string = _build_xoauth2_string(self.smtp_account.username, access_token)
                code, msg = self.connection.docmd("AUTH", "XOAUTH2 " + auth_string)
                if code not in (235, 250):
                    raise smtplib.SMTPAuthenticationError(code, msg)
                logger.info(f"OAuth2 XOAUTH2 auth succeeded for {self.smtp_account.username}")
            else:
                # Standard password login
                self.connection.login(
                    self.smtp_account.username,
                    self.smtp_account.password
                )
            
            logger.info(f"Connected to SMTP: {self.smtp_account.host}:{self.smtp_account.port}")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP Authentication failed: {e}")
            raise SMTPConnectionError(f"Authentication failed: {e}")
        except smtplib.SMTPConnectError as e:
            logger.error(f"SMTP Connection failed: {e}")
            raise SMTPConnectionError(f"Connection failed: {e}")
        except Exception as e:
            logger.error(f"SMTP Error: {e}")
            raise SMTPConnectionError(f"SMTP Error: {e}")
    
    def disconnect(self):
        """Close SMTP connection"""
        if self.connection:
            try:
                self.connection.quit()
            except Exception:
                pass
            self.connection = None
            logger.info(f"Disconnected from SMTP: {self.smtp_account.host}")
    
    def is_connected(self) -> bool:
        """Check if connection is still alive"""
        if not self.connection:
            return False
        try:
            status = self.connection.noop()[0]
            return status == 250
        except Exception:
            return False
    
    def compose_email(
        self,
        to_email: str,
        subject: str,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
        to_name: Optional[str] = None,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        custom_message_id: Optional[str] = None,
    ) -> MIMEMultipart:
        """Compose email message with MIME support"""
        
        has_attachments = attachments and len(attachments) > 0
        
        # When attachments exist, root must be 'mixed' with a nested 'alternative' for text/html.
        # Without attachments, root is 'alternative' (text/html variants only).
        if has_attachments:
            msg = MIMEMultipart('mixed')
            body_part = MIMEMultipart('alternative')
        else:
            msg = MIMEMultipart('alternative')
            body_part = msg  # Same object
        
        # Set headers
        from_header = self.smtp_account.from_email
        effective_from_name = self.smtp_account.from_name if from_name is None else from_name
        if effective_from_name:
            from_header = f"{effective_from_name} <{self.smtp_account.from_email}>"
        
        to_header = to_email
        if to_name:
            to_header = f"{to_name} <{to_email}>"
        
        msg['From'] = from_header
        msg['To'] = to_header
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        
        # Set custom Message-ID (for thread tracking)
        if custom_message_id:
            msg['Message-ID'] = custom_message_id
        
        if reply_to:
            msg['Reply-To'] = reply_to
        
        # Add custom headers (e.g. X-Thread-ID)
        if custom_headers:
            for header_name, header_value in custom_headers.items():
                msg[header_name] = header_value
        
        # Add text body
        if body_text:
            text_part = MIMEText(body_text, 'plain', 'utf-8')
            body_part.attach(text_part)
        
        # Add HTML body
        if body_html:
            html_part = MIMEText(body_html, 'html', 'utf-8')
            body_part.attach(html_part)
        
        # If neither provided, add empty text
        if not body_text and not body_html:
            body_part.attach(MIMEText('', 'plain', 'utf-8'))
        
        # For attachments: nest the body_part inside msg, then add attachments
        if has_attachments:
            msg.attach(body_part)
            for attachment in attachments:
                # Detect MIME type from filename
                import mimetypes
                filename = attachment.get('filename', 'attachment')
                mime_type = attachment.get('content_type') or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                maintype, subtype = mime_type.split('/', 1)
                
                part = MIMEBase(maintype, subtype)
                part.set_payload(attachment['content'])
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    'attachment',
                    filename=filename
                )
                msg.attach(part)
        
        return msg
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
        to_name: Optional[str] = None,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        custom_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a single email"""
        
        # Ensure connected
        if not self.is_connected():
            self.connect()
        
        try:
            # Compose message
            msg = self.compose_email(
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                to_name=to_name,
                from_name=from_name,
                reply_to=reply_to,
                attachments=attachments,
                custom_headers=custom_headers,
                custom_message_id=custom_message_id,
            )
            
            # Send
            self.connection.sendmail(
                self.smtp_account.from_email,
                to_email,
                msg.as_string()
            )
            
            logger.info(f"Email sent to {to_email} via {self.smtp_account.host}")
            
            return {
                "success": True,
                "message_id": msg.get('Message-ID'),
                "to_email": to_email,
                "from_email": self.smtp_account.from_email
            }
            
        except smtplib.SMTPRecipientsRefused as e:
            error_code = str(list(e.recipients.values())[0][0])
            is_permanent = error_code in self.HARD_BOUNCE_CODES
            raise SMTPSendError(
                f"Recipient refused: {to_email}",
                error_code=error_code,
                is_permanent=is_permanent
            )
        except smtplib.SMTPDataError as e:
            error_code = str(e.smtp_code)
            is_permanent = error_code in self.HARD_BOUNCE_CODES
            raise SMTPSendError(
                f"Data error: {e.smtp_error}",
                error_code=error_code,
                is_permanent=is_permanent
            )
        except smtplib.SMTPServerDisconnected:
            self.connection = None
            raise SMTPSendError("Server disconnected", is_permanent=False)
        except Exception as e:
            raise SMTPSendError(f"Send failed: {str(e)}", is_permanent=False)
    
    def send_with_retry(
        self,
        to_email: str,
        subject: str,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
        to_name: Optional[str] = None,
        from_name: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Dict[str, Any]:
        """Send email with exponential backoff retry"""
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                return self.send_email(
                    to_email=to_email,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    to_name=to_name,
                    from_name=from_name
                )
            except SMTPSendError as e:
                last_error = e
                
                # Don't retry permanent failures (hard bounces)
                if e.is_permanent:
                    logger.warning(f"Hard bounce for {to_email}: {e.message}")
                    raise
                
                # Retry soft failures
                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.info(f"Retry {attempt + 1}/{max_retries} for {to_email} in {delay}s")
                    import time
                    time.sleep(delay)
                    
                    # Reconnect if needed
                    if not self.is_connected():
                        try:
                            self.connect()
                        except SMTPConnectionError:
                            continue
            except SMTPConnectionError as e:
                last_error = SMTPSendError(str(e), is_permanent=False)
                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    import time
                    time.sleep(delay)
        
        # All retries failed
        raise last_error or SMTPSendError("Max retries exceeded", is_permanent=False)
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


def validate_smtp_credentials(
    host: str,
    port: int,
    username: str,
    password: str,
    encryption: str = "tls",
    auth_type: str = "password",
    oauth2_client_id: str = None,
    oauth2_client_secret: str = None,
    oauth2_tenant_id: str = None,
) -> Dict[str, Any]:
    """Validate SMTP credentials without sending an email"""
    
    try:
        if encryption == "ssl":
            context = ssl.create_default_context()
            connection = smtplib.SMTP_SSL(host, port, context=context, timeout=10)
        else:
            connection = smtplib.SMTP(host, port, timeout=10)
            if encryption == "tls":
                context = ssl.create_default_context()
                connection.starttls(context=context)
        
        if auth_type == "oauth2":
            # Build a temporary mock account to reuse token fetcher
            class _TmpAccount:
                id = 0
            tmp = _TmpAccount()
            tmp.oauth2_client_id = oauth2_client_id
            tmp.oauth2_client_secret = oauth2_client_secret
            tmp.oauth2_tenant_id = oauth2_tenant_id
            access_token = _get_oauth2_access_token(tmp)
            auth_string = _build_xoauth2_string(username, access_token)
            code, msg = connection.docmd("AUTH", "XOAUTH2 " + auth_string)
            if code not in (235, 250):
                connection.quit()
                return {
                    "valid": False,
                    "message": f"OAuth2 authentication failed (code {code}): {msg}"
                }
        else:
            connection.login(username, password)
        
        connection.quit()
        
        return {
            "valid": True,
            "message": "SMTP credentials are valid"
        }
        
    except smtplib.SMTPAuthenticationError:
        return {
            "valid": False,
            "message": "Authentication failed - check username/password"
        }
    except smtplib.SMTPConnectError:
        return {
            "valid": False,
            "message": "Could not connect to server - check host/port"
        }
    except ssl.SSLError:
        return {
            "valid": False,
            "message": "SSL/TLS error - check encryption settings"
        }
    except Exception as e:
        return {
            "valid": False,
            "message": f"Error: {str(e)}"
        }
