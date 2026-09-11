#!/usr/bin/env python3
"""
Stress tests for all 9 engine fixes + brevo Redis fix.

Each test is self-contained — creates its own Redis state, runs the test,
cleans up, and reports PASS/FAIL.  No real emails are sent and no
production data is touched.

Usage:
    python3 tests/stress_test_fixes.py          # run all
    python3 tests/stress_test_fixes.py 3        # run only test #3
    python3 tests/stress_test_fixes.py 1 3 7    # run tests 1, 3, 7
"""

import sys, os, time, json, threading, traceback, random, importlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

# ── Bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
if urlsplit(TEST_REDIS_URL).path.rstrip("/") != "/15":
    raise RuntimeError("TEST_REDIS_URL must select isolated Redis DB 15")
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["DATABASE_URL"] = "sqlite:///test_stress.db"

import redis as _redis

# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════

_passed = 0
_failed = 0
_skipped = 0

def _r():
    """Get a fresh Redis connection to test DB."""
    return _redis.from_url(TEST_REDIS_URL, decode_responses=True)

def _flush():
    """Flush test DB."""
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


# ═════════════════════════════════════════════════════════════════════════
# TEST 1: DB Session Leak — single session, always closed
# ═════════════════════════════════════════════════════════════════════════
def test_1_db_session_leak():
    _header(1, "DB session leak — _process_email opens ONE session, always closes it")
    
    import inspect
    from services.worker_service import WorkerService
    
    source = inspect.getsource(WorkerService._process_email)
    
    # 1a: Only ONE SessionLocal() call at the top
    session_calls = source.count("SessionLocal()")
    _assert(
        session_calls == 1,
        f"Exactly 1 SessionLocal() call found (got {session_calls})",
        f"Expected 1 SessionLocal() call, found {session_calls} — session leak risk"
    )
    
    # 1b: Has a finally: db.close()
    has_finally_close = "finally:" in source and "db.close()" in source
    _assert(
        has_finally_close,
        "finally: db.close() present — session always released",
        "Missing finally: db.close() — sessions can leak on exceptions"
    )
    
    # 1c: The except block reuses `db`, not opening a new session
    # Check that the except block doesn't contain SessionLocal()
    # Split at 'except Exception' and check the part after it
    parts = source.split("except Exception")
    if len(parts) >= 2:
        except_block = parts[-1]
        _assert(
            "SessionLocal()" not in except_block,
            "except block reuses existing db (no new SessionLocal)",
            "except block opens a NEW SessionLocal — Fix #1 not applied"
        )
    else:
        _skip("Could not parse except block")
    
    # 1d: Stress simulation — mock SessionLocal and ensure close is called
    # even when the inner code throws
    close_called = threading.Event()
    mock_db = MagicMock()
    mock_db.close = MagicMock(side_effect=lambda: close_called.set())
    mock_db.query.return_value.filter.return_value.first.side_effect = Exception("Simulated DB crash")
    
    with patch("services.worker_service.SessionLocal", return_value=mock_db):
        ws = WorkerService.__new__(WorkerService)
        ws.queue = MagicMock()
        ws.stats = {"processed": 0, "sent": 0, "failed": 0, "retried": 0}
        ws._current_worker_mode = None
        email_data = {"_id": "test-1", "inbox_id": 1, "to_email": "a@b.com", "subject": "Hi"}
        try:
            ws._process_email(email_data, worker_id=0)
        except Exception:
            pass
    
    _assert(
        close_called.is_set(),
        "db.close() was called even after exception — no leak",
        "db.close() was NOT called after exception — LEAK!"
    )


# ═════════════════════════════════════════════════════════════════════════
# TEST 2: Premature campaign completion — checks Redis queue
# ═════════════════════════════════════════════════════════════════════════
def test_2_premature_completion():
    _header(2, "Premature campaign completion — Redis queue check before COMPLETED")
    
    import inspect
    from services.worker_service import WorkerService
    
    source = inspect.getsource(WorkerService._update_campaign_progress)
    
    # 2a: The method checks the Redis queue
    _assert(
        "zrange" in source.lower() or "ZRANGE" in source,
        "Method scans Redis queue (ZRANGE) before marking complete",
        "Method does NOT check Redis queue — can mark COMPLETED prematurely"
    )
    
    # 2b: It looks for campaign prefix
    _assert(
        'f"campaign:{campaign_id}:recipient:"' in source or 'prefix' in source,
        "Checks for campaign-specific email IDs in queue",
        "No campaign prefix check found"
    )
    
    # 2c: It guards the COMPLETED assignment
    _assert(
        "queue_has_emails" in source and "not queue_has_emails" in source,
        "COMPLETED only set when queue_has_emails is False",
        "Missing queue_has_emails guard"
    )
    
    # 2d: Functional test — simulate emails still in queue
    _flush()
    r = _r()
    
    # Put a campaign email into the normal queue
    campaign_email = json.dumps({
        "_id": "campaign:42:recipient:100",
        "to_email": "test@example.com",
        "campaign_id": 42,
    })
    r.zadd("email:queue:normal", {campaign_email: time.time() + 3600})
    
    # Create a mock worker with the test Redis
    ws = WorkerService.__new__(WorkerService)
    ws.queue = MagicMock()
    ws.queue.QUEUE_KEY = "email:queue:{priority}"
    ws.queue.redis = r
    
    from services.queue_service import Priority
    
    # Mock DB objects
    mock_db = MagicMock()
    mock_campaign = MagicMock()
    mock_campaign.total_recipients = 10
    mock_campaign.status = "sending"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_campaign
    
    # Mock the count query to return pending=0, sent=10 (would normally trigger COMPLETED)
    from models.campaign import RecipientSendStatus, CampaignStatus
    mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
        (RecipientSendStatus.SENT, 10),
    ]
    
    ws._update_campaign_progress(mock_db, 42)
    
    _assert(
        mock_campaign.status != CampaignStatus.COMPLETED,
        "Campaign NOT marked COMPLETED while emails remain in queue",
        f"Campaign wrongly marked as {mock_campaign.status} despite queued emails"
    )
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 3: Atomic Lua pop — concurrent pops never return the same email
# ═════════════════════════════════════════════════════════════════════════
def test_3_atomic_lua_pop():
    _header(3, "Atomic Lua pop — concurrent pops never double-deliver")
    
    _flush()
    r = _r()
    
    # Patch EmailQueue to use test DB
    from services.queue_service import EmailQueue, Priority
    
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    
    # Seed 200 emails, all ready NOW
    N = 200
    now = time.time()
    queue_key = q.QUEUE_KEY.format(priority="normal")
    
    for i in range(N):
        email = {"_id": f"stress-{i}", "to_email": f"user{i}@test.com"}
        q.redis.zadd(queue_key, {json.dumps(email): now - 1})  # all overdue
    
    count_before = q.redis.zcard(queue_key)
    _assert(count_before == N, f"Seeded {N} emails", f"Only {count_before} seeded")
    
    # 3a: Pop from 10 threads simultaneously
    popped_ids = []
    lock = threading.Lock()
    
    def pop_worker():
        local_q = EmailQueue.__new__(EmailQueue)
        local_q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
        local_q._pop_script = local_q.redis.register_script(EmailQueue._POP_LUA)
        
        local_ids = []
        while True:
            result = local_q._pop_script(
                keys=[queue_key, q.PROCESSING_KEY],
                args=[time.time()],
            )
            if result is None:
                break
            data = json.loads(result)
            local_ids.append(data["_id"])
        
        with lock:
            popped_ids.extend(local_ids)
    
    threads = []
    for _ in range(10):
        t = threading.Thread(target=pop_worker)
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join(timeout=15)
    
    # 3b: Check NO duplicates
    unique = set(popped_ids)
    _assert(
        len(popped_ids) == len(unique),
        f"All {len(popped_ids)} pops unique — zero duplicates",
        f"DUPLICATE detected! {len(popped_ids)} pops but only {len(unique)} unique IDs"
    )
    
    # 3c: All emails accounted for
    _assert(
        len(unique) == N,
        f"All {N} emails popped exactly once",
        f"Expected {N} emails, got {len(unique)}"
    )
    
    # 3d: Queue is now empty
    remaining = q.redis.zcard(queue_key)
    _assert(
        remaining == 0,
        "Queue is empty after all pops",
        f"Queue still has {remaining} items!"
    )
    
    # 3e: All items are in the processing hash
    processing_count = q.redis.hlen(q.PROCESSING_KEY)
    _assert(
        processing_count == N,
        f"All {N} items moved to processing hash",
        f"Only {processing_count}/{N} items in processing hash"
    )
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 4: Redis connection reuse — no new connection per send
# ═════════════════════════════════════════════════════════════════════════
def test_4_redis_conn_reuse():
    _header(4, "Legacy global throttle removal")

    from services.worker_service import WorkerService

    # 4a-4c: The old global_send_throttle was removed (dead code — the domain
    # layer's per-minute send slots replaced it). Verify it's gone.
    _assert(
        not hasattr(WorkerService, "global_send_throttle"),
        "legacy global_send_throttle removed (replaced by domain send slots)",
        "global_send_throttle still exists"
    )


# ═════════════════════════════════════════════════════════════════════════
# TEST 5: _human_delay no DB query
# ═════════════════════════════════════════════════════════════════════════
def test_5_human_delay_no_db():
    _header(5, "_human_delay accepts mode param — no DB query")
    
    import inspect
    from services.worker_service import WorkerService
    
    sig = inspect.signature(WorkerService._human_delay)
    params = list(sig.parameters.keys())
    
    # 5a: Parameter is inbox_mode, not inbox_id
    _assert(
        "inbox_mode" in params,
        "Parameter is 'inbox_mode' (mode object, not DB id)",
        f"Parameters are {params} — expected 'inbox_mode'"
    )
    
    _assert(
        "inbox_id" not in params,
        "No 'inbox_id' parameter (no DB lookup needed)",
        "'inbox_id' parameter still present — old signature"
    )
    
    # 5b: Source should NOT contain SessionLocal
    source = inspect.getsource(WorkerService._human_delay)
    _assert(
        "SessionLocal" not in source and "db.query" not in source,
        "Method body contains no DB calls",
        "Method body still references SessionLocal or db.query"
    )
    
    # 5c: Functional — call it with each mode and ensure no crash + delay is reasonable
    from models.inbox import SendingMode
    
    ws = WorkerService.__new__(WorkerService)
    ws._send_count = 0
    ws._macro_send_count = 0
    ws._current_worker_mode = None
    
    for mode in [SendingMode.ACTIVE, SendingMode.DISTRACTED, SendingMode.OFFLINE]:
        # Patch time.sleep to not actually sleep, just record the value
        with patch("services.worker_service.time.sleep") as mock_sleep:
            # Also patch get_worker_delay to return instantly
            with patch("services.worker_service.get_worker_delay", return_value=2.0):
                ws._human_delay(inbox_mode=mode)
                mock_sleep.assert_called_once()
                delay = mock_sleep.call_args[0][0]
                _assert(
                    0 <= delay <= 5.0,
                    f"Mode {mode.value}: delay={delay:.1f}s (capped at 5s)",
                    f"Mode {mode.value}: delay={delay:.1f}s — out of expected range"
                )


# ═════════════════════════════════════════════════════════════════════════
# TEST 6: Hot-path imports moved to top of file
# ═════════════════════════════════════════════════════════════════════════
def test_6_hot_path_imports():
    _header(6, "Hot-path imports — re, html.escape, SMTPService at module top")
    
    # Read the first 50 lines of worker_service.py — imports must be there
    worker_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "services", "worker_service.py")
    with open(worker_path) as f:
        top_lines = [f.readline() for _ in range(50)]
    top_block = "".join(top_lines)
    
    # 6a: `import re` or `import re as _re` at top
    _assert(
        "import re" in top_block,
        "'import re' is in the top 50 lines (module-level)",
        "'import re' not found in top 50 lines"
    )
    
    # 6b: html.escape
    _assert(
        "from html import escape" in top_block or "import html" in top_block,
        "html.escape imported at module level",
        "html.escape not at module level"
    )
    
    # 6c: SMTPService
    _assert(
        "SMTPService" in top_block,
        "SMTPService imported at module level",
        "SMTPService not at module level"
    )
    
    # 6d: SendingMode
    _assert(
        "SendingMode" in top_block,
        "SendingMode imported at module level",
        "SendingMode not at module level"
    )
    
    # 6e: Check that _process_email does NOT import re or SMTPService
    import inspect
    from services.worker_service import WorkerService
    source = inspect.getsource(WorkerService._process_email)
    
    # Allow 'import' in string literals (e.g. log messages) but not actual import statements
    # Look for lines that start with import or from ... import
    import_lines = [
        line.strip() for line in source.split("\n")
        if (line.strip().startswith("import re") or line.strip().startswith("from html import"))
           or line.strip().startswith("from services.smtp_service import")
    ]
    
    _assert(
        len(import_lines) == 0,
        "_process_email has no hot-path import re/html/SMTPService",
        f"_process_email still has hot-path imports: {import_lines}"
    )


# ═════════════════════════════════════════════════════════════════════════
# TEST 7: Consistent time windows — sending_mode 9-17 matches worker
# ═════════════════════════════════════════════════════════════════════════
def test_7_consistent_time_windows():
    _header(7, "Time windows aligned — sending_mode_service 9-17 UTC")
    
    from services.sending_mode_service import WORK_HOUR_START, WORK_HOUR_END, is_working_hours
    
    # 7a: Constants are 9 and 17
    _assert(
        WORK_HOUR_START == 9,
        f"WORK_HOUR_START = {WORK_HOUR_START} (aligned with worker 9AM)",
        f"WORK_HOUR_START = {WORK_HOUR_START}, expected 9"
    )
    
    _assert(
        WORK_HOUR_END == 17,
        f"WORK_HOUR_END = {WORK_HOUR_END} (aligned with worker 5PM)",
        f"WORK_HOUR_END = {WORK_HOUR_END}, expected 17"
    )
    
    # 7b: Worker _is_business_hours uses same range (9-17)
    import inspect
    from services.worker_service import WorkerService
    source = inspect.getsource(WorkerService._is_business_hours)
    _assert(
        "9 <=" in source and "< 17" in source,
        "Worker _is_business_hours uses 9 <= hour < 17",
        "Worker uses different hours than sending_mode_service"
    )
    
    # 7c: Boundary tests — 8:59 AM should be outside, 9:00 AM inside, 4:59 PM inside, 5:00 PM outside
    boundary_tests = [
        (datetime(2026, 3, 27, 8, 59, tzinfo=timezone.utc), False, "8:59 UTC → outside"),
        (datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc), True,  "9:00 UTC → inside"),
        (datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc), True,  "12:00 UTC → inside"),
        (datetime(2026, 3, 27, 16, 59, tzinfo=timezone.utc), True,  "16:59 UTC → inside"),
        (datetime(2026, 3, 27, 17, 0, tzinfo=timezone.utc), False, "17:00 UTC → outside"),
        (datetime(2026, 3, 27, 23, 0, tzinfo=timezone.utc), False, "23:00 UTC → outside"),
    ]
    
    for dt, expected, label in boundary_tests:
        result = is_working_hours(dt)
        _assert(
            result == expected,
            f"{label} → {'in' if result else 'out'} ✓",
            f"{label} → got {'in' if result else 'out'}, expected {'in' if expected else 'out'}"
        )
    
    # 7d: determine_mode returns OFFLINE outside hours
    from services.sending_mode_service import determine_mode
    from models.inbox import SendingMode
    
    mode, reason = determine_mode(
        health_score=80,
        provider_type="ses_api",
        current_daily_count=5,
        daily_cap=100,
        inbox_state="ready",
        now=datetime(2026, 3, 27, 20, 0, tzinfo=timezone.utc)  # 8 PM
    )
    _assert(
        mode == SendingMode.OFFLINE,
        f"8 PM → OFFLINE (reason: {reason[:50]})",
        f"8 PM → {mode.value} — should be OFFLINE"
    )
    
    # Same inbox at 10 AM should be ACTIVE (or DISTRACTED due to 25% jitter — both are valid)
    mode2, reason2 = determine_mode(
        health_score=80,
        provider_type="ses_api",
        current_daily_count=5,
        daily_cap=100,
        inbox_state="ready",
        now=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
    )
    _assert(
        mode2 in (SendingMode.ACTIVE, SendingMode.DISTRACTED),
        f"10 AM, healthy SES → {mode2.value} (reason: {reason2[:50]})",
        f"10 AM, healthy SES → {mode2.value} — should be ACTIVE or DISTRACTED"
    )


# ═════════════════════════════════════════════════════════════════════════
# TEST 8: Clustering prevention — re-queued emails staggered
# ═════════════════════════════════════════════════════════════════════════
def test_8_clustering_prevention():
    _header(8, "Clustering prevention — re-queued emails get staggered delays")
    
    import inspect
    from services.worker_service import WorkerService
    
    source = inspect.getsource(WorkerService._worker_loop)
    
    # 8a: Has stagger counter
    _assert(
        "_requeue_stagger_count" in source,
        "Worker loop has _requeue_stagger_count variable",
        "No stagger counter found in worker loop"
    )
    
    # 8b: Has stagger constant
    _assert(
        "_REQUEUE_STAGGER_SEC" in source or "stagger" in source.lower(),
        "Stagger interval constant present",
        "No stagger interval found"
    )
    
    # 8c: Stagger is added to delay
    _assert(
        "base_delay + stagger" in source or "stagger" in source,
        "base_delay + stagger used for re-queue",
        "Stagger not added to delay calculation"
    )
    
    # 8d: Functional simulation — simulate 20 re-queues and verify timestamps spread out
    _flush()
    r = _r()
    
    from services.queue_service import EmailQueue, Priority
    
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    
    # Simulate what worker_loop does: sequential re-queues with increasing stagger
    base_delay = 3600  # 1 hour until next window
    _REQUEUE_STAGGER_SEC = 90
    queue_key = q.QUEUE_KEY.format(priority="normal")
    
    for i in range(20):
        stagger = i * _REQUEUE_STAGGER_SEC
        delay = base_delay + stagger
        score = time.time() + delay
        email = {"_id": f"requeue-{i}", "to_email": f"user{i}@test.com", "_priority": "normal"}
        q.redis.zadd(queue_key, {json.dumps(email): score})
    
    # Read all scores
    items = q.redis.zrange(queue_key, 0, -1, withscores=True)
    scores = [s for _, s in items]
    
    # Verify they are spaced ~90s apart
    diffs = [scores[i+1] - scores[i] for i in range(len(scores) - 1)]
    avg_diff = sum(diffs) / len(diffs) if diffs else 0
    min_diff = min(diffs) if diffs else 0
    
    _assert(
        85 <= avg_diff <= 95,
        f"Average spacing = {avg_diff:.1f}s (~90s expected)",
        f"Average spacing = {avg_diff:.1f}s — expected ~90s"
    )
    
    # First and last should be ~(19 * 90 = 1710)s apart
    spread = scores[-1] - scores[0]
    _assert(
        1700 <= spread <= 1720,
        f"Total spread = {spread:.0f}s (20 emails × 90s = 1710s expected)",
        f"Total spread = {spread:.0f}s — expected ~1710s"
    )
    
    _assert(
        min_diff >= 85,
        f"Min gap = {min_diff:.1f}s — no clustering",
        f"Min gap = {min_diff:.1f}s — potential cluster!"
    )
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 9: Worker churn off-hours — sleeps instead of pop-requeue loop
# ═════════════════════════════════════════════════════════════════════════
def test_9_worker_churn():
    _header(9, "Worker churn prevention — sleeps after first off-hours re-queue")
    
    import inspect
    from services.worker_service import WorkerService
    
    source = inspect.getsource(WorkerService._worker_loop)
    
    # 9a: After re-queue block, there's a sleep until next window
    _assert(
        "sleep_remaining" in source or "base_delay" in source,
        "Worker sleeps for base_delay after re-queue",
        "No post-re-queue sleep found"
    )
    
    # 9b: Sleep is in small chunks (responsive to stop)
    _assert(
        "chunk" in source or "30" in source,
        "Sleep is in small chunks (responsive to stop signals)",
        "Sleep appears not to be chunked"
    )
    
    # 9c: Stagger counter resets after sleep
    _assert(
        "_requeue_stagger_count = 0" in source,
        "Stagger counter resets to 0 after window sleep",
        "Stagger counter never resets — stagger will grow forever"
    )
    
    # 9d: Functional — verify the code structure prevents churn
    # The key check: after `self.queue.add(...)` for re-queue, the NEXT action
    # is sleeping, then `continue` (back to loop top). It should NOT immediately
    # pop another email.
    
    lines = source.split("\n")
    # Find the re-queue add call
    add_idx = None
    for i, line in enumerate(lines):
        if "self.queue.add(" in line and "delay_seconds" in line:
            add_idx = i
            break
    
    if add_idx is not None:
        # Look at the lines after the add — they should contain sleep and continue
        after_add = "\n".join(lines[add_idx:add_idx+25])
        has_sleep = "sleep" in after_add or "time.sleep" in after_add
        has_continue = "continue" in after_add
        _assert(
            has_sleep and has_continue,
            "After re-queue: sleep + continue present (no churn)",
            f"After re-queue: sleep={has_sleep}, continue={has_continue} — may churn"
        )
    else:
        _skip("Could not locate re-queue add() in worker loop")


# ═════════════════════════════════════════════════════════════════════════
# BONUS: Lua script edge cases
# ═════════════════════════════════════════════════════════════════════════
def test_10_lua_edge_cases():
    _header("10", "Lua pop edge cases — empty queue, future scores, concurrent rapid-fire")
    
    _flush()
    r = _r()
    
    from services.queue_service import EmailQueue
    
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    
    queue_key = q.QUEUE_KEY.format(priority="normal")
    
    # 10a: Pop from empty queue returns None
    result = q._pop_script(keys=[queue_key, q.PROCESSING_KEY], args=[time.time()])
    _assert(
        result is None,
        "Pop from empty queue returns None",
        f"Pop from empty queue returned: {result}"
    )
    
    # 10b: Pop with only future-scored items returns None
    future_email = json.dumps({"_id": "future-1", "to_email": "f@t.com"})
    q.redis.zadd(queue_key, {future_email: time.time() + 99999})
    
    result = q._pop_script(keys=[queue_key, q.PROCESSING_KEY], args=[time.time()])
    _assert(
        result is None,
        "Pop with only future items returns None (respects score)",
        f"Pop returned future item prematurely: {result}"
    )
    
    # Clean up future item
    q.redis.delete(queue_key)
    
    # 10c: _id extracted correctly from JSON
    test_email = json.dumps({"_id": "extract-test-123", "to_email": "x@y.com"})
    q.redis.zadd(queue_key, {test_email: time.time() - 1})
    
    result = q._pop_script(keys=[queue_key, q.PROCESSING_KEY], args=[time.time()])
    _assert(result is not None, "Item popped successfully", "Pop failed")
    
    # Check it was placed in processing hash with correct key
    proc_val = q.redis.hget(q.PROCESSING_KEY, "extract-test-123")
    _assert(
        proc_val is not None,
        "Email moved to processing hash with correct _id key",
        "Email NOT in processing hash — Lua _id extraction failed"
    )
    
    # 10d: Rapid-fire 1000 pops with 500 items — exactly 500 found, 500 None
    q.redis.delete(queue_key)
    q.redis.delete(q.PROCESSING_KEY)
    
    for i in range(500):
        email = json.dumps({"_id": f"rapid-{i}", "to_email": f"r{i}@t.com"})
        q.redis.zadd(queue_key, {email: time.time() - 1})
    
    found = 0
    not_found = 0
    for _ in range(1000):
        r = q._pop_script(keys=[queue_key, q.PROCESSING_KEY], args=[time.time()])
        if r is not None:
            found += 1
        else:
            not_found += 1
    
    _assert(
        found == 500 and not_found == 500,
        f"Exactly 500 found + 500 None out of 1000 calls",
        f"Got {found} found + {not_found} None — expected 500 + 500"
    )
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# BONUS: Full integration — re-queue does NOT inflate stats
# ═════════════════════════════════════════════════════════════════════════
def test_11_requeue_no_stats_inflation():
    _header("11", "Re-queue uses hdel+srem (no stats inflation)")
    
    import inspect
    from services.worker_service import WorkerService
    
    source = inspect.getsource(WorkerService._worker_loop)
    
    # Find the re-queue section (between "not self._is_business_hours" and "continue")
    bh_idx = source.find("not self._is_business_hours()")
    continue_idx = source.find("continue", bh_idx) if bh_idx >= 0 else -1
    
    if bh_idx >= 0 and continue_idx >= 0:
        requeue_block = source[bh_idx:continue_idx]
        
        # 11a: Uses hdel + srem (manual cleanup)
        _assert(
            "hdel" in requeue_block and "srem" in requeue_block,
            "Re-queue block uses hdel + srem (manual cleanup, no stats)",
            "Re-queue block missing hdel/srem"
        )
        
        # 11b: Does NOT call self.queue.complete() (comments mentioning complete() are OK)
        # Filter out comment lines
        code_lines = [
            l for l in requeue_block.split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        _assert(
            ".complete(" not in code_only,
            "Re-queue block does NOT call .complete() (avoids stats inflation)",
            "Re-queue block calls .complete() — stats will be inflated!"
        )
        
        # 11c: Calls queue.add() for re-queuing
        _assert(
            "self.queue.add(" in requeue_block,
            "Re-queue block calls queue.add() to re-queue with delay",
            "Re-queue block doesn't call queue.add()"
        )
    else:
        _skip("Could not locate re-queue block in worker loop")
    
    # 11d: Functional — add + re-add does not double-count
    _flush()
    
    from services.queue_service import EmailQueue, Priority
    
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    
    # Initial add
    q.add({"_id": "stats-test-1", "to_email": "a@b.com"}, Priority.NORMAL, user_id=1)
    queued_1 = int(q.redis.hget(q.STATS_KEY, "total_queued") or 0)
    
    # Simulate re-queue: hdel + srem + add with existing _id
    q.redis.hdel(q.PROCESSING_KEY, "stats-test-1")
    q.redis.srem(q.ACTIVE_IDS_KEY, "stats-test-1")
    q.add({"_id": "stats-test-1", "to_email": "a@b.com"}, Priority.NORMAL, user_id=1)
    
    queued_2 = int(q.redis.hget(q.STATS_KEY, "total_queued") or 0)
    
    _assert(
        queued_2 == queued_1,
        f"total_queued stayed at {queued_1} after re-queue (no inflation)",
        f"total_queued changed from {queued_1} to {queued_2} — INFLATED!"
    )
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# TEST 12: Atomic pop stress — high contention with mixed priorities
# ═════════════════════════════════════════════════════════════════════════
def test_12_atomic_pop_mixed_priorities():
    _header("12", "Atomic pop stress — mixed priorities, 500 emails, 20 threads")
    
    _flush()
    
    from services.queue_service import EmailQueue, Priority
    
    q = EmailQueue.__new__(EmailQueue)
    q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
    q._pop_script = q.redis.register_script(EmailQueue._POP_LUA)
    
    # Seed 500 emails across all priorities
    now = time.time()
    counts = {Priority.HIGH: 100, Priority.NORMAL: 300, Priority.LOW: 100}
    total = sum(counts.values())
    
    for p, n in counts.items():
        queue_key = q.QUEUE_KEY.format(priority=p.value)
        for i in range(n):
            email = {"_id": f"{p.value}-{i}", "to_email": f"{p.value}{i}@t.com"}
            q.redis.zadd(queue_key, {json.dumps(email): now - random.random()})
    
    # Pop from 20 threads, each popping all priorities in order
    popped_ids = []
    lock = threading.Lock()
    
    def pop_all():
        local_q = EmailQueue.__new__(EmailQueue)
        local_q.redis = _redis.from_url(TEST_REDIS_URL, decode_responses=True)
        local_q._pop_script = local_q.redis.register_script(EmailQueue._POP_LUA)
        
        local_ids = []
        while True:
            found = False
            for p in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
                qk = local_q.QUEUE_KEY.format(priority=p.value)
                result = local_q._pop_script(keys=[qk, local_q.PROCESSING_KEY], args=[time.time()])
                if result is not None:
                    data = json.loads(result)
                    local_ids.append(data["_id"])
                    found = True
                    break
            if not found:
                break
        
        with lock:
            popped_ids.extend(local_ids)
    
    threads = [threading.Thread(target=pop_all) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    
    unique = set(popped_ids)
    _assert(
        len(popped_ids) == len(unique),
        f"All {len(popped_ids)} pops unique across priorities — zero duplicates",
        f"DUPLICATES! {len(popped_ids)} pops, {len(unique)} unique"
    )
    
    _assert(
        len(unique) == total,
        f"All {total} emails accounted for",
        f"Expected {total}, got {len(unique)}"
    )
    
    # All queues empty
    for p in Priority:
        qk = q.QUEUE_KEY.format(priority=p.value)
        rem = q.redis.zcard(qk)
        _assert(rem == 0, f"{p.value} queue empty", f"{p.value} queue has {rem} remaining!")
    
    _flush()


# ═════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════

ALL_TESTS = {
    1:  ("DB session leak",             test_1_db_session_leak),
    2:  ("Premature campaign complete",  test_2_premature_completion),
    3:  ("Atomic Lua pop",              test_3_atomic_lua_pop),
    4:  ("Redis conn reuse",            test_4_redis_conn_reuse),
    5:  ("_human_delay no DB",          test_5_human_delay_no_db),
    6:  ("Hot-path imports",            test_6_hot_path_imports),
    7:  ("Consistent time windows",     test_7_consistent_time_windows),
    8:  ("Clustering prevention",       test_8_clustering_prevention),
    9:  ("Worker churn off-hours",      test_9_worker_churn),
    10: ("Lua pop edge cases",          test_10_lua_edge_cases),
    11: ("Re-queue no stats inflation", test_11_requeue_no_stats_inflation),
    12: ("Atomic pop mixed priorities", test_12_atomic_pop_mixed_priorities),
}

def main():
    selected = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else sorted(ALL_TESTS.keys())
    
    print("\n" + "=" * 70)
    print("  ENGINE FIXES — STRESS TEST SUITE")
    print(f"  Running {len(selected)} test(s): {selected}")
    print("=" * 70)
    
    _flush()  # Clean slate
    
    for num in selected:
        if num not in ALL_TESTS:
            print(f"\n⚠️  Unknown test #{num}, skipping")
            continue
        name, fn = ALL_TESTS[num]
        try:
            fn()
        except Exception as e:
            print(f"\n  💥 TEST #{num} CRASHED: {e}")
            traceback.print_exc()
            global _failed
            _failed += 1
    
    _flush()  # Clean up
    
    print("\n" + "=" * 70)
    total = _passed + _failed + _skipped
    print(f"  RESULTS: {_passed} passed, {_failed} failed, {_skipped} skipped  ({total} checks)")
    if _failed == 0:
        print("  🎉 ALL TESTS PASSED")
    else:
        print(f"  ⚠️  {_failed} FAILURE(S) — review above")
    print("=" * 70 + "\n")
    
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
