#!/usr/bin/env python3
"""
Concurrency engine tests — hundreds-of-campaigns architecture.

Covers:
  1. Campaign batching work-queue (due ZSET → work list)
  2. Due-only draining (future schedules stay put)
  3. Inbox quota reservation (timezone-keyed) + release
  4. Domain daily budget + release
  5. Domain per-minute send slots
  6. Domain health circuit breaker
  7. Weighted priority pop (warmup vs campaign fairness)
  8. Atomic recipient claiming (Postgres, skips when no test DB)
  9. Hold-and-roll re-queue (quota holds don't mark FAILED)

Redis state lives in DB 15 — never touches production data.

Usage:
    python3 tests/test_concurrency_engine.py
"""

import sys, os, time, json, threading, random
from urllib.parse import urlsplit

# ── Bootstrap ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Force every service module onto test Redis DB 15, never a runtime DB.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
if urlsplit(TEST_REDIS_URL).path.rstrip("/") != "/15":
    raise RuntimeError("TEST_REDIS_URL must select isolated Redis DB 15")
os.environ["REDIS_URL"] = TEST_REDIS_URL

import redis as _redis

TEST_PG_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://root@/email_sender_test?host=/var/run/postgresql&port=5433",
)

# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════

_passed = 0
_failed = 0
_skipped = 0

def _r():
    return _redis.from_url(TEST_REDIS_URL, decode_responses=True)

def _flush():
    _r().flushdb()

def _header(num, title):
    print(f"\n{'━' * 70}")
    print(f"  TEST #{num}: {title}")
    print(f"{'━' * 70}")

def _pass(msg=""):
    global _passed
    _passed += 1
    print(f"  ✅ PASS{': ' + msg if msg else ''}")

def _fail(msg):
    global _failed
    _failed += 1
    print(f"  ❌ FAIL: {msg}")

def _skip(msg):
    global _skipped
    _skipped += 1
    print(f"  ⏭️  SKIP: {msg}")

def _assert(condition, pass_msg, fail_msg):
    if condition:
        _pass(pass_msg)
    else:
        _fail(fail_msg)
        raise AssertionError(fail_msg)

def _new_queue():
    from services.queue_service import EmailQueue
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    return q


# ═════════════════════════════════════════════════════════════════════════
# TEST 1: Campaign batching work-queue
# ═════════════════════════════════════════════════════════════════════════
def test_1_campaign_work_queue():
    _header(1, "Campaign batching work-queue — schedule → drain → pop (tz-scoped)")
    _flush()
    q = _new_queue()

    # Schedule 3 ET campaigns due now, 1 in the future
    now = time.time()
    q.schedule_campaign(1, tz="US/Eastern")
    q.schedule_campaign(2, tz="US/Eastern")
    q.schedule_campaign(3, tz="US/Eastern")
    q.schedule_campaign(4, due_at=now + 3600, tz="US/Eastern")

    et_due = q.campaign_due_key("US/Eastern")
    pt_due = q.campaign_due_key("US/Pacific")

    _assert(
        q.redis.zcard(et_due) == 4,
        "4 ET campaigns in ET due ZSET",
        f"Expected 4 due entries, got {q.redis.zcard(et_due)}"
    )
    _assert(
        q.redis.zcard(pt_due) == 0,
        "PT due ZSET is empty (timezone isolation)",
        f"PT due has {q.redis.zcard(pt_due)} entries"
    )

    moved = q.drain_due_campaigns(limit=100, tz="US/Eastern")
    _assert(moved == 3, "Only the 3 due campaigns moved to ET work", f"Moved {moved}, expected 3")

    work = q.redis.lrange(q.campaign_work_key("US/Eastern"), 0, -1)
    _assert(
        set(work) == {"1", "2", "3"},
        "ET work list contains the 3 due campaign IDs",
        f"Work list wrong: {work}"
    )
    _assert(
        q.redis.zcard(et_due) == 1,
        "Future-scheduled campaign (4) still in ET due ZSET",
        f"ET due ZSET should have 1 entry, has {q.redis.zcard(et_due)}"
    )

    # A PT worker draining must NOT move ET campaigns
    moved_pt = q.drain_due_campaigns(limit=100, tz="US/Pacific")
    _assert(
        moved_pt == 0 and q.redis.llen(q.campaign_work_key("US/Pacific")) == 0,
        "PT drain does not touch ET campaigns (cross-tz fix)",
        f"PT drain moved {moved_pt}"
    )

    # NX preservation: scheduler refresh must not disturb a future due score
    q.schedule_campaign(4, due_at=now, overwrite=False, tz="US/Eastern")
    score = q.redis.zscore(et_due, "4")
    _assert(
        score is not None and float(score) >= now + 3500,
        "NX schedule preserves the future due score",
        f"Score was overwritten: {score}"
    )

    # Batcher-style BLPOP
    popped = q.redis.blpop(q.campaign_work_key("US/Eastern"), timeout=1)
    _assert(popped is not None and popped[1] in ("1", "2", "3"), "BLPOP returns a work item", f"BLPOP: {popped}")

    # Unschedule removes from every timezone's due + work
    q.schedule_campaign(9, tz="US/Eastern")
    q.schedule_campaign(9, tz="US/Pacific")
    q.drain_due_campaigns(tz="US/Eastern")
    q.unschedule_campaign(9)
    _assert(
        q.redis.zscore(et_due, "9") is None
        and q.redis.zscore(pt_due, "9") is None
        and q.redis.lrem(q.campaign_work_key("US/Eastern"), 0, "9") == 0,
        "unschedule removes campaign from all tz due + work",
        "unschedule left campaign behind"
    )
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 2: Inbox quota reservation
# ═════════════════════════════════════════════════════════════════════════
def test_2_inbox_quota():
    _header(2, "Inbox quota — reserve, cap, release (timezone-keyed)")
    _flush()
    q = _new_queue()

    # cap = 3 campaign emails/day for inbox 7 in US/Eastern today
    r1 = q.reserve_inbox_quota(7, "US/Eastern", "2026-09-03", 1, 3)
    r2 = q.reserve_inbox_quota(7, "US/Eastern", "2026-09-03", 1, 3)
    r3 = q.reserve_inbox_quota(7, "US/Eastern", "2026-09-03", 1, 3)
    r4 = q.reserve_inbox_quota(7, "US/Eastern", "2026-09-03", 1, 3)

    _assert(r1 == 1 and r2 == 1 and r3 == 1, "First three reservations granted", f"{r1},{r2},{r3}")
    _assert(r4 == 0, "Fourth reservation blocked at cap", f"Expected 0, got {r4}")

    # Different timezone day is a separate bucket
    r_other = q.reserve_inbox_quota(7, "US/Pacific", "2026-09-03", 1, 3)
    _assert(r_other == 1, "Different timezone has its own quota bucket", f"Got {r_other}")

    # Release frees capacity
    q.release_inbox_quota(7, "US/Eastern", "2026-09-03", 1)
    r5 = q.reserve_inbox_quota(7, "US/Eastern", "2026-09-03", 1, 3)
    _assert(r5 == 1, "Released capacity is available again", f"Got {r5}")

    # Unlimited inbox (cap <= 0)
    r6 = q.reserve_inbox_quota(8, "US/Eastern", "2026-09-03", 50, 0)
    _assert(r6 == 50, "cap<=0 means unlimited", f"Got {r6}")

    # Release never goes negative
    q.release_inbox_quota(9, "US/Eastern", "2026-09-03", 10)
    raw = q.redis.get(q.INBOX_QUOTA_KEY.format(inbox_id=9, tz="US_Eastern", date="2026-09-03"))
    _assert(raw is None or int(raw) >= 0, "Release on empty quota does not go negative", f"Raw: {raw}")
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 3: Domain daily budget
# ═════════════════════════════════════════════════════════════════════════
def test_3_domain_budget():
    _header(3, "Domain daily budget — reserve, cap, release")
    _flush()

    from services import domain_service as ds

    domain = "example.test"
    # budget of 4 for this test
    ok = [ds.reserve_daily(domain, 1, budget=4) for _ in range(4)]
    blocked = ds.reserve_daily(domain, 1, budget=4)
    _assert(all(o == 1 for o in ok) and blocked == 0, "4 reservations then blocked", f"ok={ok} blocked={blocked}")

    # budget <= 0 means unlimited
    unlim = ds.reserve_daily(domain, 100, budget=0)
    _assert(unlim == 100, "budget<=0 is unlimited", f"Got {unlim}")

    # Release frees budget
    ds.release_daily(domain, day=ds.utc_date(), n=2)
    after = ds.reserve_daily(domain, 1, budget=4)
    _assert(after == 1, "Released budget is available again", f"Got {after}")
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 4: Domain per-minute send slots
# ═════════════════════════════════════════════════════════════════════════
def test_4_domain_send_slots():
    _header(4, "Domain send slots — per-minute burst limit")
    _flush()

    from services import domain_service as ds

    # Patch the minute budget to 5 for the test
    old = ds.DOMAIN_MINUTE_BUDGET
    ds.DOMAIN_MINUTE_BUDGET = 5
    try:
        grants = [ds.acquire_send_slot("slot.test", timeout_seconds=0.2) for _ in range(5)]
        _assert(all(grants), "First 5 slots granted within budget", f"{grants}")
        blocked = ds.acquire_send_slot("slot.test", timeout_seconds=0.2)
        _assert(not blocked, "6th slot blocked until next minute", "6th slot should be blocked")
        # Unlimited domain (None) always granted
        _assert(ds.acquire_send_slot(None), "No domain → always allowed", "No-domain slot denied")
    finally:
        ds.DOMAIN_MINUTE_BUDGET = old
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 5: Domain health circuit breaker
# ═════════════════════════════════════════════════════════════════════════
def test_5_domain_health():
    _header(5, "Domain health circuit breaker — daily bounces and complaint rate")
    _flush()

    from services import domain_service as ds

    domain = "health.test"
    day = ds.utc_date()
    r = _r()

    dedupe_domain = "dedupe.test"
    ds.record_bounce(dedupe_domain, event_id="re_same_message")
    ds.record_bounce(dedupe_domain, event_id="re_same_message")
    deduped_bounces = int(r.hget(ds.stats_key(dedupe_domain, day), "bounced") or 0)
    _assert(deduped_bounces == 1, "Duplicate bounce webhook counts once", f"Got {deduped_bounces}")

    # The first 49 bounces remain below the daily cutoff.
    key = ds.stats_key(domain, day)
    pipe = r.pipeline()
    pipe.hincrby(key, "sent", 1000)
    pipe.hincrby(key, "bounced", 49)
    pipe.expire(key, 172800)
    pipe.execute()

    _assert(not ds.is_throttled(domain), "Not throttled before evaluation", "Throttled before evaluation")
    _assert(not ds.evaluate_domain_health(domain), "49 daily bounces remain below cutoff", "49 bounces tripped throttle")

    # The 50th daily bounce trips the absolute cutoff.
    r.hincrby(key, "bounced", 1)
    throttled = ds.evaluate_domain_health(domain)
    reason = ds.get_throttle_reason(domain) or ""
    _assert(throttled and ds.is_throttled(domain), "50th daily bounce throttles domain", f"evaluate={throttled}")
    _assert("50/50" in reason, "Throttle reason reports the daily cutoff", reason)

    # Corrected healthy stats clear the throttle.
    r.hset(key, "bounced", 0)
    ds.evaluate_domain_health(domain)
    _assert(not ds.is_throttled(domain), "Throttle clears below daily cutoff", "Throttle did not clear")

    # Complaint protection remains rate-based after the minimum sample.
    complaint_domain = "complaint.test"
    complaint_key = ds.stats_key(complaint_domain, day)
    r.hset(complaint_key, mapping={"sent": 1000, "complained": 20})
    complaint_throttled = ds.evaluate_domain_health(complaint_domain)
    _assert(complaint_throttled, "2% complaint rate throttles domain", "Complaint rate did not throttle")

    # Too little data for a rate decision leaves the domain unthrottled.
    domain2 = "small.test"
    _assert(not ds.is_throttled(domain2), "Unknown domain is not throttled", "Unknown domain is throttled")
    r.hincrby(ds.stats_key(domain2, day), "complained", 10)
    ds.evaluate_domain_health(domain2)
    _assert(not ds.is_throttled(domain2), "Below min sample → no rate throttle", "Small sample tripped throttle")
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 6: Weighted priority pop
# ═════════════════════════════════════════════════════════════════════════
def test_6_weighted_pop():
    _header(6, "Weighted pop — warmup can't starve campaigns (statistical)")
    _flush()
    q = _new_queue()

    os.environ["QUEUE_POP_WEIGHT_NORMAL"] = "0.5"
    os.environ["QUEUE_POP_WEIGHT_HIGH"] = "0.5"
    try:
        high_key = q.QUEUE_KEY.format(priority="high")
        norm_key = q.QUEUE_KEY.format(priority="normal")
        now = time.time()
        for i in range(100):
            q.redis.zadd(high_key, {json.dumps({"_id": f"h{i}", "to_email": "w@x.y"}): now - 1})
            q.redis.zadd(norm_key, {json.dumps({"_id": f"n{i}", "to_email": "c@x.y"}): now - 1})

        high_popped = 0
        normal_popped = 0
        for _ in range(100):
            item = q.pop()
            if item is None:
                break
            if item["_id"].startswith("h"):
                high_popped += 1
            else:
                normal_popped += 1

        _assert(
            high_popped >= 20 and normal_popped >= 20,
            f"Both priorities served (high={high_popped}, normal={normal_popped})",
            f"Starvation suspected: high={high_popped} normal={normal_popped}"
        )
    finally:
        os.environ.pop("QUEUE_POP_WEIGHT_NORMAL", None)
        os.environ.pop("QUEUE_POP_WEIGHT_HIGH", None)
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 7: Hold-and-roll re-queue
# ═════════════════════════════════════════════════════════════════════════
def test_7_hold_and_roll():
    _header(7, "Hold-and-roll — blocked emails re-queue, never FAILED")
    _flush()
    q = _new_queue()

    from services.worker_service import WorkerService
    from services.queue_service import Priority

    ws = WorkerService.__new__(WorkerService)
    ws.queue = q

    email_data = {
        "_id": "campaign:42:recipient:7",
        "inbox_id": 1,
        "to_email": "hold@example.test",
        "campaign_id": 42,
        "recipient_id": 7,
        "_priority": "normal",
        "_user_id": 3,
    }
    # Simulate an email that was popped (in processing + active ids)
    q.redis.hset(q.PROCESSING_KEY, email_data["_id"], json.dumps(email_data))
    q.redis.sadd(q.ACTIVE_IDS_KEY, email_data["_id"])

    ws._requeue_blocked_email(email_data, 0, 3600, "inbox_daily_limit")

    _assert(
        q.redis.hget(q.PROCESSING_KEY, email_data["_id"]) is None,
        "Email removed from processing hash",
        "Email still in processing hash",
    )
    _assert(
        q.redis.zcard(q.QUEUE_KEY.format(priority="normal")) == 1,
        "Email re-queued in the normal queue",
        f"Normal queue has {q.redis.zcard(q.QUEUE_KEY.format(priority='normal'))} items",
    )
    _assert(
        q.redis.sismember(q.ACTIVE_IDS_KEY, email_data["_id"]),
        "Re-queued email tracked in active ids again",
        "Email missing from active ids",
    )

    # Re-adding the same ID must NOT duplicate it (dedup guard)
    q.add(email_data, Priority.NORMAL, delay_seconds=5)
    _assert(
        q.redis.zcard(q.QUEUE_KEY.format(priority="normal")) == 1,
        "Duplicate re-add is skipped (dedup guard)",
        "Queue grew on duplicate add",
    )

    # It must be scheduled in the future (hold delay)
    raw = q.redis.zrange(q.QUEUE_KEY.format(priority="normal"), 0, 0, withscores=True)[0][1]
    _assert(raw >= time.time() + 3500, f"Re-queued with future hold delay ({int(raw - time.time())}s)", f"Delay too small: {raw - time.time()}")
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 8: Atomic recipient claiming (Postgres)
# ═════════════════════════════════════════════════════════════════════════
def test_8_atomic_claim():
    _header(8, "Atomic recipient claiming — concurrent claims never double-claim")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    try:
        engine = create_engine(TEST_PG_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        _skip(f"Postgres test DB unavailable: {e}")
        return

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    setup = SessionLocal()
    try:
        setup.execute(text("INSERT INTO campaigns (name, subject) VALUES ('t', 't') RETURNING id"))
        setup.execute(text("INSERT INTO campaigns (name, subject) VALUES ('t', 't') RETURNING id"))
        setup.commit()
        cid1, cid2 = [r[0] for r in setup.execute(text("SELECT id FROM campaigns ORDER BY id DESC LIMIT 2"))]
        rid1 = setup.execute(text("INSERT INTO recipients (email) VALUES ('claim1@test.local') RETURNING id")).scalar()
        rid2 = setup.execute(text("INSERT INTO recipients (email) VALUES ('claim2@test.local') RETURNING id")).scalar()
        setup.commit()

        for rid in (rid1, rid2):
            setup.execute(text(
                "INSERT INTO campaign_recipients (campaign_id, recipient_id, status) "
                "VALUES (:c, :r, 'PENDING')"
            ), {"c": cid1, "r": rid})
        setup.commit()

        cr_ids = [r[0] for r in setup.execute(text(
            "SELECT id FROM campaign_recipients WHERE campaign_id = :c ORDER BY id"
        ), {"c": cid1})]
        setup.commit()

        from services.worker_service import WorkerService

        def claim(session, ids):
            ws = WorkerService.__new__(WorkerService)
            res = set(ws._atomic_claim_recipient_ids(session, ids))
            session.commit()  # release row locks (production commits per email)
            return res

        # Two concurrent sessions claim the same IDs — union must equal all,
        # intersection must be empty.
        s1, s2 = SessionLocal(), SessionLocal()
        try:
            results = []
            errors = []

            def claim_a():
                try:
                    results.append(("a", claim(s1, cr_ids)))
                except Exception as e:
                    errors.append(("a", str(e)))

            def claim_b():
                try:
                    results.append(("b", claim(s2, cr_ids)))
                except Exception as e:
                    errors.append(("b", str(e)))

            t1 = threading.Thread(target=claim_a)
            t2 = threading.Thread(target=claim_b)
            t1.start(); t2.start(); t1.join(); t2.join()

            _assert(not errors, "Both concurrent claims completed without errors", f"{errors}")
            if results:
                a = results[0][1]
                b = results[1][1]
                all_ids = set(cr_ids)
                _assert(a | b == all_ids, "All recipient IDs claimed across the two calls", f"a={a} b={b}")
                _assert(not (a & b), "No recipient claimed twice (intersection empty)", f"Overlap: {a & b}")
        finally:
            s1.close(); s2.close()
    finally:
        # Rollback the inserted rows so the test DB stays clean
        setup.rollback()
        setup.close()
        engine.dispose()


# ═════════════════════════════════════════════════════════════════════════
# TEST 9: Mode engine — ACTIVE start, HYPER at day 5
# ═════════════════════════════════════════════════════════════════════════
def test_9_mode_graduation():
    _header(9, "Mode engine — new inboxes ACTIVE, day>=5 graduates to HYPER")
    from services.sending_mode_service import determine_mode, SendingMode

    # Brand-new inbox (day 0, not_started) → ACTIVE
    mode, reason = determine_mode(
        provider_type="ses_api",
        current_daily_count=0,
        daily_cap=5,
        inbox_state="not_started",
        warmup_day=0,
    )
    _assert(
        mode == SendingMode.ACTIVE,
        f"New inbox starts ACTIVE ({reason})",
        f"Got {mode.value} — {reason}"
    )

    # Day 3 → still ACTIVE
    mode, _ = determine_mode(
        provider_type="ses_api",
        current_daily_count=0,
        daily_cap=44,
        inbox_state="warming_up",
        warmup_day=3,
    )
    _assert(mode == SendingMode.ACTIVE, "Day-3 inbox is ACTIVE", f"Got {mode.value}")

    # Day 5 → HYPER (graduation boundary)
    mode, reason = determine_mode(
        provider_type="ses_api",
        current_daily_count=42,
        daily_cap=44,
        inbox_state="warming_up",
        warmup_day=5,
    )
    _assert(
        mode == SendingMode.HYPER,
        f"Day-5 inbox graduates to HYPER ({reason})",
        f"Got {mode.value} — {reason}"
    )

    # Day 10 → HYPER
    mode, _ = determine_mode(
        provider_type="ses_api",
        current_daily_count=42,
        daily_cap=44,
        inbox_state="warming_up",
        warmup_day=10,
    )
    _assert(mode == SendingMode.HYPER, "Day-10 inbox is HYPER", f"Got {mode.value}")

    # Warmed-up inbox → HYPER
    mode, _ = determine_mode(
        provider_type="ses_api",
        current_daily_count=99,
        daily_cap=100,
        inbox_state="warmed_up",
        warmup_day=40,
    )
    _assert(mode == SendingMode.HYPER, "warmed_up inbox is HYPER", f"Got {mode.value}")

    # Paused inbox → still ACTIVE pacing (state gates sending elsewhere)
    mode, _ = determine_mode(
        provider_type="ses_api",
        current_daily_count=0,
        daily_cap=100,
        inbox_state="paused",
        warmup_day=2,
    )
    _assert(mode == SendingMode.ACTIVE, "Paused inbox stays ACTIVE (no OFFLINE mode anymore)", f"Got {mode.value}")

    # Mode profiles exist for both new modes
    from services.sending_mode_service import MODE_PROFILES
    _assert(
        SendingMode.HYPER in MODE_PROFILES and "batch_size" in MODE_PROFILES[SendingMode.HYPER],
        "HYPER profile defined",
        "HYPER profile missing"
    )


# ═════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════
def main():
    tests = [
        test_1_campaign_work_queue,
        test_2_inbox_quota,
        test_3_domain_budget,
        test_4_domain_send_slots,
        test_5_domain_health,
        test_6_weighted_pop,
        test_7_hold_and_roll,
        test_8_atomic_claim,
        test_9_mode_graduation,
    ]
    for t in tests:
        try:
            t()
        except AssertionError:
            continue
        except Exception:
            _fail("unexpected exception")
            import traceback
            traceback.print_exc()

    print(f"\n{'═' * 70}")
    print(f"  RESULTS: {_passed} passed, {_failed} failed, {_skipped} skipped")
    print(f"{'═' * 70}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
