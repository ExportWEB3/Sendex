"""
Email Tracking Endpoints
- Open tracking (1x1 pixel)
- Click tracking (link redirect)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlsplit

from database import get_db
from services.monitoring_service import MetricsService
from services.tracking_signature import verify_tracking_signature

router = APIRouter(prefix="/api/track", tags=["Tracking"])

# 1x1 transparent GIF pixel
TRACKING_PIXEL = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00,
    0x01, 0x00, 0x80, 0x00, 0x00, 0xff, 0xff, 0xff,
    0x00, 0x00, 0x00, 0x21, 0xf9, 0x04, 0x01, 0x00,
    0x00, 0x00, 0x00, 0x2c, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44,
    0x01, 0x00, 0x3b
])


@router.get("/open")
def track_open(
    c: int = Query(..., description="Campaign ID"),
    r: Optional[int] = Query(None, description="Recipient ID"),
    s: Optional[str] = Query(None, description="Tracking signature"),
    db: Session = Depends(get_db)
):
    """
    Track email opens via a 1x1 tracking pixel.
    Returns a transparent GIF image.
    """
    if s and not verify_tracking_signature(s, c, r):
        raise HTTPException(status_code=403, detail="Invalid tracking signature")

    try:
        # Record the open event
        if s and r:
            from models.campaign import CampaignRecipient
            recipient = db.query(CampaignRecipient).filter(
                CampaignRecipient.id == r,
                CampaignRecipient.campaign_id == c
            ).first()
            if recipient and not recipient.opened_at:
                from datetime import datetime, timezone
                recipient.opened_at = datetime.now(timezone.utc)
                db.commit()

        # Also record in daily metrics if we can find the inbox
        from models.campaign import Campaign
        campaign = db.query(Campaign).filter(Campaign.id == c).first() if s else None
        if campaign:
            metrics_service = MetricsService(db)
            # Record open for all inboxes in this campaign (simplified)
            from models.inbox import Inbox
            inboxes = db.query(Inbox).filter(Inbox.id.in_(campaign.inbox_ids or [])).all()
            for inbox in inboxes:
                metrics_service.record_engagement(inbox.id, "open")
    except Exception:
        pass  # Never fail the tracking pixel

    return Response(
        content=TRACKING_PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@router.get("/click")
def track_click(
    url: str = Query(..., description="Original destination URL"),
    c: int = Query(..., description="Campaign ID"),
    r: Optional[int] = Query(None, description="Recipient ID"),
    s: Optional[str] = Query(None, description="Tracking signature"),
    db: Session = Depends(get_db)
):
    """
    Track link clicks and redirect to the original URL.
    """
    if s and not verify_tracking_signature(s, c, r, url):
        raise HTTPException(status_code=403, detail="Invalid tracking signature")

    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="Invalid redirect URL")

    try:
        # Record the click event
        if s and r:
            from models.campaign import CampaignRecipient
            recipient = db.query(CampaignRecipient).filter(
                CampaignRecipient.id == r,
                CampaignRecipient.campaign_id == c
            ).first()
            if recipient and not recipient.clicked_at:
                from datetime import datetime, timezone
                recipient.clicked_at = datetime.now(timezone.utc)
                db.commit()

        # Record in daily metrics
        from models.campaign import Campaign
        campaign = db.query(Campaign).filter(Campaign.id == c).first() if s else None
        if campaign:
            metrics_service = MetricsService(db)
            from models.inbox import Inbox
            inboxes = db.query(Inbox).filter(Inbox.id.in_(campaign.inbox_ids or [])).all()
            for inbox in inboxes:
                metrics_service.record_engagement(inbox.id, "click")
    except Exception:
        pass  # Never fail the redirect

    return RedirectResponse(url=url, status_code=302)
