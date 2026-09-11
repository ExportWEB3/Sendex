import json
from types import SimpleNamespace

import services.campaign_service as campaign_service_module
import services.kill_switch as kill_switch
import services.worker_service as worker_service_module
from services.campaign_service import CampaignService
from services.worker_service import WorkerService


class FakeRedis:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.set_calls = []

    def get(self, _key):
        if self.error:
            raise self.error
        return self.value

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return True


def test_kill_switch_reads_enabled_and_disabled_states(monkeypatch):
    fake_redis = FakeRedis(value="1")
    monkeypatch.setattr(kill_switch, "_get_redis", lambda: fake_redis)
    assert kill_switch.is_kill_switch_enabled() is True

    fake_redis.value = None
    assert kill_switch.is_kill_switch_enabled() is False


def test_kill_switch_fails_closed_when_redis_is_unavailable(monkeypatch):
    fake_redis = FakeRedis(error=ConnectionError("redis unavailable"))
    monkeypatch.setattr(kill_switch, "_get_redis", lambda: fake_redis)

    assert kill_switch.is_kill_switch_enabled() is True


def test_campaign_block_sets_status_and_logs_sof_message(monkeypatch):
    fake_redis = FakeRedis()
    activity = []
    monkeypatch.setattr(campaign_service_module, "_eq", SimpleNamespace(redis=fake_redis))
    monkeypatch.setattr(campaign_service_module, "is_kill_switch_enabled", lambda: True)
    monkeypatch.setattr(
        campaign_service_module,
        "log_campaign_activity",
        lambda campaign_id, message: activity.append((campaign_id, message)),
    )

    service = CampaignService.__new__(CampaignService)
    assert service._set_kill_switch_status(42, "campaign resume blocked") is True

    args, kwargs = fake_redis.set_calls[0]
    assert args[0] == "campaign:42:status"
    assert json.loads(args[1]) == {
        "state": "kill_switch",
        "reason": "kill_switch",
        "message": kill_switch.KILL_SWITCH_MESSAGE,
    }
    assert kwargs == {"ex": 300}
    assert activity == [
        (42, f"🔒 {kill_switch.KILL_SWITCH_MESSAGE} — campaign resume blocked; sending paused")
    ]


def test_worker_batching_stops_before_queue_work(monkeypatch):
    blocked_updates = []
    monkeypatch.setattr(worker_service_module, "is_kill_switch_enabled", lambda: True)

    worker = WorkerService.__new__(WorkerService)
    worker._set_campaign_kill_switch_status = lambda: blocked_updates.append(True)

    assert worker._process_campaign_batch(None, SimpleNamespace(id=42)) == 0
    assert blocked_updates == [True]


def test_manual_processing_stops_before_database_access(monkeypatch):
    blocked_updates = []
    monkeypatch.setattr(worker_service_module, "is_kill_switch_enabled", lambda: True)

    worker = WorkerService.__new__(WorkerService)
    worker._set_campaign_kill_switch_status = lambda: blocked_updates.append(True)

    result = worker.process_campaigns_now()

    assert result["queued"] == 0
    assert result["errors"] == [kill_switch.KILL_SWITCH_MESSAGE]
    assert blocked_updates == [True]
