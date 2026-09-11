from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from api import campaign, inbox, logs, queue, recipient, replies, system, tags, warmup
from api.auth import require_admin, require_auth


def _route_dependency(router, path: str, method: str):
    route = next(
        candidate
        for candidate in router.routes
        if isinstance(candidate, APIRoute)
        and candidate.path == path
        and method in candidate.methods
    )
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize(
    ("router", "path", "method"),
    [
        (logs.router, "/api/logs/", "GET"),
        (logs.router, "/api/logs/tail", "GET"),
        (logs.router, "/api/logs/errors", "GET"),
        (logs.router, "/api/logs/requests", "GET"),
        (logs.router, "/api/logs/info", "GET"),
        (logs.router, "/api/logs/clear", "DELETE"),
        (tags.router, "/api/tags/", "GET"),
        (tags.router, "/api/tags/", "POST"),
        (tags.router, "/api/tags/{tag_id}", "GET"),
        (tags.router, "/api/tags/{tag_id}", "PUT"),
        (tags.router, "/api/tags/{tag_id}", "DELETE"),
        (tags.router, "/api/tags/{tag_id}/recipients", "GET"),
        (system.router, "/api/system/reset", "POST"),
        (system.router, "/api/system/resync", "POST"),
        (system.router, "/api/system/fix-campaign/{campaign_id}", "POST"),
        (queue.router, "/api/queue/length", "GET"),
        (queue.router, "/api/queue/peek", "GET"),
        (queue.router, "/api/queue/dead-letters", "GET"),
        (queue.router, "/api/queue/dead-letters/{email_id}/retry", "POST"),
        (queue.router, "/api/queue/clear", "DELETE"),
        (queue.router, "/api/queue/dead-letters/clear", "DELETE"),
        (queue.router, "/api/queue/stats/reset", "DELETE"),
        (queue.router, "/api/queue/rate-limit/smtp/{smtp_id}", "GET"),
        (queue.router, "/api/queue/rate-limit/domain/{domain}", "GET"),
        (queue.router, "/api/queue/rate-limit/check", "POST"),
        (queue.router, "/api/queue/rate-limit/smtp/{smtp_id}/reset", "DELETE"),
        (queue.router, "/api/queue/worker/config", "POST"),
        (queue.router, "/api/queue/worker/start", "POST"),
        (queue.router, "/api/queue/worker/stop", "POST"),
        (queue.router, "/api/queue/worker/process-now", "POST"),
        (queue.router, "/api/queue/worker/campaigns", "GET"),
        (campaign.router, "/api/campaigns/{campaign_id}/process-batch", "POST"),
        (inbox.router, "/api/inboxes/diagnostic/last-check", "GET"),
        (warmup.router, "/api/warmup/run-daily", "POST"),
        (warmup.router, "/api/warmup/partners/assign", "POST"),
        (warmup.router, "/api/warmup/partners/{inbox_id}", "GET"),
        (warmup.router, "/api/warmup/partners/rotate", "POST"),
        (warmup.router, "/api/warmup/partners/rotate/{inbox_id}", "POST"),
        (warmup.router, "/api/warmup/partners/available/{inbox_id}", "GET"),
        (warmup.router, "/api/warmup/replies/schedule", "POST"),
        (warmup.router, "/api/warmup/replies/pending", "GET"),
        (warmup.router, "/api/warmup/replies/process", "POST"),
        (warmup.router, "/api/warmup/inbox/check/{inbox_id}", "POST"),
        (warmup.router, "/api/warmup/inbox/check-all", "POST"),
        (recipient.router, "/api/recipients/", "POST"),
    ],
)
def test_global_and_sensitive_routes_require_admin(router, path, method):
    assert require_admin in _route_dependency(router, path, method)


def test_system_health_requires_authentication():
    assert require_auth in _route_dependency(
        system.router,
        "/api/system/health",
        "GET",
    )


@pytest.mark.parametrize(
    ("router", "path", "method"),
    [
        (campaign.router, "/api/campaigns/all-auto-replies", "GET"),
        (campaign.router, "/api/campaigns/{campaign_id}/stats", "GET"),
        (campaign.router, "/api/campaigns/{campaign_id}/recipient/{recipient_id}/preview", "GET"),
        (campaign.router, "/api/campaigns/{campaign_id}/check-replies", "POST"),
        (campaign.router, "/api/campaigns/{campaign_id}/check-and-reply", "POST"),
        (campaign.router, "/api/campaigns/check-all-replies", "POST"),
        (campaign.router, "/api/campaigns/process-auto-replies", "POST"),
        (campaign.router, "/api/campaigns/{campaign_id}/auto-replies", "GET"),
        (campaign.router, "/api/campaigns/auto-replies/{reply_id}/cancel", "POST"),
        (replies.router, "/api/replies/{engine}/campaigns", "GET"),
        (replies.router, "/api/replies/{engine}/campaign/{campaign_id}", "GET"),
        (replies.router, "/api/replies/{engine}/check", "POST"),
        (replies.router, "/api/replies/{engine}/stats", "GET"),
        (queue.router, "/api/queue/stats", "GET"),
        (queue.router, "/api/queue/my-stats", "GET"),
        (queue.router, "/api/queue/add", "POST"),
        (queue.router, "/api/queue/add-bulk", "POST"),
        (queue.router, "/api/queue/worker/status", "GET"),
    ],
)
def test_tenant_routes_require_authentication(router, path, method):
    assert require_auth in _route_dependency(router, path, method)


def test_direct_queue_rejects_unowned_smtp_account():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        queue._require_owned_smtp_account(db, user_id=7, smtp_account_id=99)

    assert exc_info.value.status_code == 404
