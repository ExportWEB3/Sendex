from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import campaign as campaign_api
from api import inbox as inbox_api
from api import list as list_api
from api import monitoring
from api import recipient as recipient_api
from database import Base
from models import (
    Alert,
    AlertSeverity,
    AlertType,
    Campaign,
    CampaignRecipient,
    Inbox,
    Recipient,
    RecipientList,
    SESEmailTemplate,
    SMTPAccount,
    User,
    UserRole,
)
from models.campaign import RecipientSendStatus
from schemas.inbox import InboxCreate, InboxUpdate
from schemas.list import RecipientBulkAction
from services.campaign_service import CampaignService


@pytest.fixture()
def tenant_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    owner = User(
        email="owner@example.com",
        password_hash="unused",
        role=UserRole.USER,
        is_active=True,
    )
    other = User(
        email="other@example.com",
        password_hash="unused",
        role=UserRole.USER,
        is_active=True,
    )
    session.add_all([owner, other])
    session.flush()

    owner_smtp = SMTPAccount(
        user_id=owner.id,
        name="Owner SMTP",
        host="smtp.example.com",
        port=587,
        username="owner",
        password="unused",
        from_email="owner@example.com",
    )
    other_smtp = SMTPAccount(
        user_id=other.id,
        name="Other SMTP",
        host="smtp.example.com",
        port=587,
        username="other",
        password="unused",
        from_email="other@example.com",
    )
    session.add_all([owner_smtp, other_smtp])
    session.flush()

    owner_list = RecipientList(user_id=owner.id, name="Owner List")
    other_list = RecipientList(user_id=other.id, name="Other List")
    owner_inbox = Inbox(
        user_id=owner.id,
        email="owner@example.com",
        smtp_account_id=owner_smtp.id,
    )
    other_inbox = Inbox(
        user_id=other.id,
        email="other@example.com",
        smtp_account_id=other_smtp.id,
    )
    session.add_all([owner_list, other_list, owner_inbox, other_inbox])
    session.flush()

    other_recipient = Recipient(email="recipient@example.com", list_id=other_list.id)
    orphan_recipient = Recipient(email="orphan@example.com", list_id=None)
    other_alert = Alert(
        inbox_id=other_inbox.id,
        alert_type=AlertType.REPUTATION_DROP,
        severity=AlertSeverity.WARNING,
        title="Other tenant alert",
    )
    session.add_all([other_recipient, orphan_recipient, other_alert])
    session.commit()

    try:
        yield {
            "session": session,
            "owner": owner,
            "other": other,
            "owner_smtp": owner_smtp,
            "other_smtp": other_smtp,
            "owner_list": owner_list,
            "other_list": other_list,
            "owner_inbox": owner_inbox,
            "other_inbox": other_inbox,
            "other_recipient": other_recipient,
            "orphan_recipient": orphan_recipient,
            "other_alert": other_alert,
        }
    finally:
        session.close()
        engine.dispose()


def test_campaign_creation_rejects_another_tenants_resources(tenant_db):
    service = CampaignService(tenant_db["session"])

    with pytest.raises(ValueError, match="inbox IDs are invalid"):
        service.create_campaign(
            name="Unsafe campaign",
            subject="Subject",
            body_html="<p>Body</p>",
            list_id=tenant_db["owner_list"].id,
            inbox_ids=[tenant_db["other_inbox"].id],
            user_id=tenant_db["owner"].id,
        )

    with pytest.raises(ValueError, match="Recipient list not found"):
        service.create_campaign(
            name="Unsafe campaign",
            subject="Subject",
            body_html="<p>Body</p>",
            list_id=tenant_db["other_list"].id,
            inbox_ids=[tenant_db["owner_inbox"].id],
            user_id=tenant_db["owner"].id,
        )


def test_campaign_update_rejects_another_tenants_inbox(tenant_db):
    campaign = Campaign(
        user_id=tenant_db["owner"].id,
        name="Owner campaign",
        subject="Subject",
        body_html="<p>Body</p>",
        list_id=tenant_db["owner_list"].id,
        inbox_ids=[tenant_db["owner_inbox"].id],
    )
    tenant_db["session"].add(campaign)
    tenant_db["session"].commit()

    with pytest.raises(ValueError, match="inbox IDs are invalid"):
        CampaignService(tenant_db["session"]).update_campaign(
            campaign.id,
            inbox_ids=[tenant_db["other_inbox"].id],
        )


def test_single_template_results_and_historic_preview(tenant_db):
    db = tenant_db["session"]
    template = SESEmailTemplate(
        user_id=tenant_db["owner"].id,
        name="Owner Template",
        subject_line="Hello {{first_name}}",
        html_content="<p>Welcome {{first_name}}</p>",
    )
    recipient = Recipient(
        email="alex@example.com",
        first_name="Alex",
        list_id=tenant_db["owner_list"].id,
    )
    db.add_all([template, recipient])
    db.flush()

    campaign = Campaign(
        user_id=tenant_db["owner"].id,
        name="Single template campaign",
        subject=template.subject_line,
        body_html=template.html_content,
        list_id=tenant_db["owner_list"].id,
        inbox_ids=[tenant_db["owner_inbox"].id],
        template_ids=[template.id],
    )
    db.add(campaign)
    db.flush()

    campaign_recipient = CampaignRecipient(
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        inbox_id=tenant_db["owner_inbox"].id,
        status=RecipientSendStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(campaign_recipient)
    db.commit()

    results = campaign_api.get_campaign_recipients(
        campaign.id,
        status_filter=None,
        page=1,
        per_page=100,
        current_user=tenant_db["owner"],
        db=db,
    )
    row = results["recipients"][0]
    assert row["template_id"] == template.id
    assert row["template_name"] == "Owner Template"
    assert row["has_preview"] is True

    preview = campaign_api.get_campaign_recipient_preview(
        campaign.id,
        campaign_recipient.id,
        current_user=tenant_db["owner"],
        db=db,
    )
    assert preview["preview_source"] == "reconstructed"
    assert preview["template_name"] == "Owner Template"
    assert preview["sent_subject"] == "Hello Alex"
    assert "Welcome Alex" in preview["sent_body_html"]


def test_inbox_cannot_link_another_tenants_smtp_account(tenant_db):
    with pytest.raises(HTTPException) as create_error:
        inbox_api.create_inbox(
            InboxCreate(
                email="new-owner@example.com",
                smtp_account_id=tenant_db["other_smtp"].id,
                imap_host="imap.example.com",
                imap_username="new-owner@example.com",
                imap_password="unused",
            ),
            current_user=tenant_db["owner"],
            db=tenant_db["session"],
        )
    assert create_error.value.status_code == 404

    with pytest.raises(HTTPException) as update_error:
        inbox_api.update_inbox(
            tenant_db["owner_inbox"].id,
            InboxUpdate(smtp_account_id=tenant_db["other_smtp"].id),
            current_user=tenant_db["owner"],
            db=tenant_db["session"],
        )
    assert update_error.value.status_code == 404

def test_bulk_action_cannot_delete_another_tenants_recipient(tenant_db):
    with pytest.raises(HTTPException) as exc_info:
        list_api.bulk_action(
            RecipientBulkAction(
                recipient_ids=[tenant_db["other_recipient"].id],
                action="delete",
            ),
            current_user=tenant_db["owner"],
            db=tenant_db["session"],
        )

    assert exc_info.value.status_code == 404
    assert tenant_db["session"].get(Recipient, tenant_db["other_recipient"].id) is not None


@pytest.mark.parametrize("recipient_key", ["other_recipient", "orphan_recipient"])
def test_recipient_api_hides_unowned_records(tenant_db, recipient_key):
    with pytest.raises(HTTPException) as exc_info:
        recipient_api.get_recipient(
            tenant_db[recipient_key].id,
            current_user=tenant_db["owner"],
            db=tenant_db["session"],
        )
    assert exc_info.value.status_code == 404


def test_monitoring_hides_another_tenants_inbox_and_alert(tenant_db):
    with pytest.raises(HTTPException) as inbox_error:
        monitoring._get_accessible_inbox(
            tenant_db["session"],
            tenant_db["other_inbox"].id,
            tenant_db["owner"],
        )
    assert inbox_error.value.status_code == 404

    with pytest.raises(HTTPException) as alert_error:
        monitoring.get_alert(
            tenant_db["other_alert"].id,
            current_user=tenant_db["owner"],
            db=tenant_db["session"],
        )
    assert alert_error.value.status_code == 404
    assert tenant_db["session"].get(Alert, tenant_db["other_alert"].id).is_read is False
