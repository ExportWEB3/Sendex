"""
Day 7: Unsubscribe Page
Public endpoint for recipients to unsubscribe
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from services.list_service import UnsubscribeService

router = APIRouter(tags=["Unsubscribe"])


UNSUBSCRIBE_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unsubscribe</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 480px;
            width: 100%;
            text-align: center;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        }}
        .icon {{
            font-size: 64px;
            margin-bottom: 20px;
        }}
        .icon.success {{
            color: #10B981;
        }}
        .icon.error {{
            color: #EF4444;
        }}
        .icon.confirm {{
            color: #F59E0B;
        }}
        h1 {{
            color: #1F2937;
            font-size: 24px;
            margin-bottom: 12px;
        }}
        p {{
            color: #6B7280;
            font-size: 16px;
            line-height: 1.6;
            margin-bottom: 24px;
        }}
        .email {{
            background: #F3F4F6;
            padding: 12px 20px;
            border-radius: 8px;
            color: #374151;
            font-weight: 500;
            margin-bottom: 24px;
            display: inline-block;
        }}
        .btn {{
            display: inline-block;
            padding: 14px 32px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: #EF4444;
            color: white;
        }}
        .btn-primary:hover {{
            background: #DC2626;
        }}
        .btn-secondary {{
            background: #E5E7EB;
            color: #374151;
            margin-left: 12px;
        }}
        .btn-secondary:hover {{
            background: #D1D5DB;
        }}
        .footer {{
            margin-top: 32px;
            padding-top: 24px;
            border-top: 1px solid #E5E7EB;
            color: #9CA3AF;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="card">
        {content}
    </div>
</body>
</html>
"""

CONFIRM_CONTENT = """
<div class="icon confirm">📧</div>
<h1>Unsubscribe from emails?</h1>
<p>You're about to unsubscribe this email address from our mailing list:</p>
<div class="email">{email}</div>
<p>You will no longer receive marketing emails from us.</p>
<form method="POST" style="display: inline;">
    <button type="submit" class="btn btn-primary">Yes, Unsubscribe</button>
</form>
<button type="button" class="btn btn-secondary" onclick="
    document.querySelector('.card').innerHTML = '<div class=\\'icon success\\'>✓</div><h1>No Changes Made</h1><p>Your email address remains on the mailing list. You can safely close this page.</p>';
">Cancel</button>
<div class="footer">
    If you didn't request this, you can safely ignore this page.
</div>
"""

SUCCESS_CONTENT = """
<div class="icon success">✓</div>
<h1>Successfully Unsubscribed</h1>
<p>The following email has been removed from our mailing list:</p>
<div class="email">{email}</div>
<p>You will no longer receive marketing emails from us. It may take up to 24 hours for this change to take full effect.</p>
<div class="footer">
    We're sorry to see you go! If you unsubscribed by mistake, please contact us.
</div>
"""

ALREADY_UNSUBSCRIBED_CONTENT = """
<div class="icon success">✓</div>
<h1>Already Unsubscribed</h1>
<p>This email address is not on our mailing list:</p>
<div class="email">{email}</div>
<p>No action is needed.</p>
<div class="footer">
    You won't receive any marketing emails from us.
</div>
"""

ERROR_CONTENT = """
<div class="icon error">✗</div>
<h1>Invalid Link</h1>
<p>This unsubscribe link is invalid or has expired.</p>
<p>If you continue to receive unwanted emails, please contact us directly.</p>
<div class="footer">
    Please check that you copied the entire link from your email.
</div>
"""


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, db: Session = Depends(get_db)):
    """Show unsubscribe confirmation page"""
    
    unsub_service = UnsubscribeService(db)
    recipient = unsub_service.get_recipient_by_token(token)
    
    if not recipient:
        content = ERROR_CONTENT
    else:
        content = CONFIRM_CONTENT.format(email=recipient.email)
    
    return HTMLResponse(
        content=UNSUBSCRIBE_PAGE_TEMPLATE.format(content=content)
    )


@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
def process_unsubscribe(token: str, db: Session = Depends(get_db)):
    """Process unsubscribe request"""
    
    unsub_service = UnsubscribeService(db)
    result = unsub_service.unsubscribe_by_token(token)
    
    if not result["success"]:
        content = ERROR_CONTENT
    elif "already" in result["message"].lower():
        content = ALREADY_UNSUBSCRIBED_CONTENT.format(email=result.get("email", ""))
    else:
        content = SUCCESS_CONTENT.format(email=result.get("email", ""))
    
    return HTMLResponse(
        content=UNSUBSCRIBE_PAGE_TEMPLATE.format(content=content)
    )


# API endpoint for programmatic unsubscribe
@router.post("/api/unsubscribe/{token}")
def api_unsubscribe(token: str, db: Session = Depends(get_db)):
    """API endpoint for unsubscribe (returns JSON)"""
    
    unsub_service = UnsubscribeService(db)
    return unsub_service.unsubscribe_by_token(token)
