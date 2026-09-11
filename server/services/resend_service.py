"""
Resend Transactional Email API Service.
Sends API-backed email through Resend.
"""

import resend
import logging
import os
import time
import re as _re
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class ResendService:
    """Send emails via Resend API"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        resend.api_key = api_key

    # ---- async path (used by brevo API endpoint) ----
    async def send_email(
        self,
        to_email: str,
        to_name: Optional[str],
        from_email: str,
        from_name: Optional[str],
        subject: str,
        html_content: Optional[str] = None,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        tags: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a single email via Resend API (async-compatible wrapper around sync SDK)"""
        return self._do_send(
            to_email, to_name, from_email, from_name,
            subject, html_content, text_content, reply_to,
            tags, headers, attachments,
        )

    # ---- sync path (used by worker + campaign service) ----
    def send_email_sync(
        self,
        to_email: str,
        to_name: Optional[str],
        from_email: str,
        from_name: Optional[str],
        subject: str,
        html_content: Optional[str] = None,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        tags: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a single email via Resend API (sync)"""
        return self._do_send(
            to_email, to_name, from_email, from_name,
            subject, html_content, text_content, reply_to,
            tags, headers, attachments,
        )

    # ---- shared implementation ----
    def _do_send(
        self,
        to_email: str,
        to_name: Optional[str],
        from_email: str,
        from_name: Optional[str],
        subject: str,
        html_content: Optional[str] = None,
        text_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        tags: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Core send logic using the Resend SDK"""
        # Build "from" string
        from_str = f"{from_name} <{from_email}>" if from_name else from_email

        params: Dict[str, Any] = {
            "from": from_str,
            "to": [to_email],
            "subject": subject,
        }

        if html_content:
            params["html"] = html_content
        if text_content:
            params["text"] = text_content
        if not html_content and not text_content:
            params["text"] = subject

        if reply_to:
            params["reply_to"] = [reply_to]

        if headers:
            params["headers"] = headers

        if tags:
            # Resend tags are [{name, value}] — we store just names, use name=name
            params["tags"] = [{"name": t, "value": t} for t in tags[:50]]

        # Attachments: Resend expects [{content: list[int]|str, filename: str}]
        if attachments:
            att_list = []
            for att in attachments:
                content_bytes = att.get("content", b"")
                if isinstance(content_bytes, bytes):
                    att_list.append({
                        "content": list(content_bytes),
                        "filename": att.get("filename", "attachment"),
                    })
            if att_list:
                params["attachments"] = att_list

        try:
            logger.info(f"Resend sending to {to_email}")
            resp = resend.Emails.send(params)
            # resp is an object with .id attribute
            msg_id = getattr(resp, "id", None) or (resp.get("id") if isinstance(resp, dict) else None)
            logger.info(f"Resend email sent to {to_email}, id={msg_id}")
            return {"success": True, "message_id": msg_id, "response": resp}
        except resend.exceptions.ResendError as e:
            logger.error(f"Resend API error: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Resend send error: {e}")
            return {"success": False, "error": str(e)}

    def get_account_info_sync(self) -> Dict[str, Any]:
        """Validate the API key by listing domains"""
        try:
            domains = resend.Domains.list()
            return {"success": True, "data": {"domains": domains}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_account_info(self) -> Dict[str, Any]:
        """Async wrapper for account info"""
        return self.get_account_info_sync()


# ---------- Global instance / factory ----------
_resend_service: Optional[ResendService] = None


def get_resend_service(api_key: Optional[str] = None) -> Optional[ResendService]:
    """Get or create Resend service instance"""
    global _resend_service
    if api_key:
        _resend_service = ResendService(api_key)
    if _resend_service is None:
        env_key = os.getenv("RESEND_API_KEY")
        if env_key:
            _resend_service = ResendService(env_key)
    return _resend_service


def init_resend(api_key: str) -> ResendService:
    """Initialize service with API key"""
    global _resend_service
    _resend_service = ResendService(api_key)
    return _resend_service
