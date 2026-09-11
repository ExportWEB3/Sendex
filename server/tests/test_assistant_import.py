#!/usr/bin/env python3
"""
Assistant bundle-import tests — classification, pairing engine, plan builder.

Pure-logic tests (no external AI calls — the AI classification pass is
patched out). Redis DB 15 is used for context storage tests.

Usage:
    python3 tests/test_assistant_import.py
"""

import io
import json
import os
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
if urlsplit(TEST_REDIS_URL).path.rstrip("/") != "/15":
    raise RuntimeError("TEST_REDIS_URL must select isolated Redis DB 15")
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["DATABASE_URL"] = "sqlite:///test_import.db"

import redis as _redis

_passed = 0
_failed = 0

def _r():
    return _redis.from_url(TEST_REDIS_URL, decode_responses=True)

def _flush():
    _r().flushdb()

def _header(num, title):
    print(f"\n{'━' * 70}\n  TEST #{num}: {title}\n{'━' * 70}")

def _pass(msg=""):
    global _passed
    _passed += 1
    print(f"  ✅ PASS{': ' + msg if msg else ''}")

def _fail(msg):
    global _failed
    _failed += 1
    print(f"  ❌ FAIL: {msg}")

def _assert(condition, pass_msg, fail_msg):
    if condition:
        _pass(pass_msg)
    else:
        _fail(fail_msg)
        raise AssertionError(fail_msg)


def _mk_template(name, tid):
    t = SimpleNamespace()
    t.id = tid
    t.name = name
    t.subject_line = "Subject " + name
    t.html_content = "<p>hi</p>"
    t.text_fallback = None
    t.attachments = None
    return t


def _mk_list(name, lid, count=10):
    l = SimpleNamespace()
    l.id = lid
    l.name = name
    l.recipient_count = count
    return l


def _mk_inbox(email, iid):
    i = SimpleNamespace()
    i.id = iid
    i.email = email
    return i


# ── TEST 1: deterministic classification ──────────────────────────────────
def test_1_classify_bundle():
    _header(1, "Bundle classification — deterministic rules")
    from services import assistant_import_service as imp

    files = {
        "accounts.txt": b"account: alpha\nemail: alpha@emailsuport.com",
        "lists/leads-a.csv": b"email,first_name,company\njohn@x.com,John,Acme\njane@y.com,Jane,Co\n",
        "lists/leads-b.txt": b"mary@x.com Mary\npeter@y.com\n",
        "leads-c.txt": b"# newsletter list\nann@x.com\nbob@y.com\n",
        "templates/welcome.html": b"Subject: Hello {{first_name}}\n<p>Body</p>",
        "templates/welcome.pdf": b"%PDF-fake",
        "templates/notes.html": b"Subject: Notes\n<p>see attached</p>",
        "templates/notes.txt": b"plain-text attachment content",
        "templates/brochure.html": b"Subject: Brochure\n<p>body</p>",
        "templates/brochure-pack.pdf": b"%PDF-prefix",
        "templates/annual_report.txt": b"Subject: Annual report\nPlease review it.",
        "templates/Annual Report.pdf": b"%PDF-normalized",
        "templates/flyer.pdf": b"%PDF-standalone",
        "mystery.data": b"unknown",
    }
    with patch.object(imp, "_ai_classify_orphans", return_value={}):
        plan = imp.classify_bundle(files)

    _assert(plan["accounts_file"] == "accounts.txt", "accounts file detected", f"got {plan['accounts_file']}")
    _assert(len(plan["lists"]) == 3, f"3 lists parsed (csv + txt in lists/ + top-level txt) (got {len(plan['lists'])})", f"{[l['name'] for l in plan['lists']]}")
    _assert(len(plan["lists"][0]["rows"]) == 2, "CSV list parsed (2 rows)", f"{plan['lists'][0]}")
    txt_rows = [l for l in plan["lists"] if l["name"] == "Leads B"][0]["rows"]
    _assert(len(txt_rows) == 2, "txt list in lists/ parsed (2 emails)", f"{txt_rows}")
    top_rows = [l for l in plan["lists"] if l["name"] == "Leads C"][0]["rows"]
    _assert(len(top_rows) == 2, "top-level txt without Subject: parsed as list (2 emails)", f"{top_rows}")
    names = {t["name"] for t in plan["templates"]}
    _assert("Welcome" in names and "Notes" in names and "Brochure" in names and "Flyer" in names, f"templates created ({names})", f"{names}")
    welcome = next(t for t in plan["templates"] if t["name"] == "Welcome")
    _assert(len(welcome["attachments"]) == 1 and welcome["attachments"][0][0] == "templates/welcome.pdf", "same-name pdf paired as attachment", f"{welcome['attachments']}")
    notes = next(t for t in plan["templates"] if t["name"] == "Notes")
    _assert(len(notes["attachments"]) == 1 and notes["attachments"][0][0].endswith("notes.txt"), "same-name TXT paired as attachment (not a second template)", f"{notes['attachments']}")
    brochure = next(t for t in plan["templates"] if t["name"] == "Brochure")
    _assert(len(brochure["attachments"]) == 1 and brochure["attachments"][0][0].endswith("brochure-pack.pdf"), "prefix-matched pdf attached to brochure", f"{brochure['attachments']}")
    annual = next(t for t in plan["templates"] if t["name"] == "Annual Report")
    _assert(len(annual["attachments"]) == 1 and annual["attachments"][0][0].endswith("Annual Report.pdf"), "normalized TXT/PDF stems pair after TXT registration", f"{annual['attachments']}")
    flyer = next(t for t in plan["templates"] if t["name"] == "Flyer")
    _assert(len(flyer["attachments"]) == 1 and flyer["attachments"][0][0].endswith("flyer.pdf"), "partnerless pdf becomes standalone template with itself attached", f"{flyer['attachments']}")
    _assert(plan["orphans"] == ["mystery.data"], "unclassified file lands in orphans", f"{plan['orphans']}")


# ── TEST 2: unzip + automation csv ────────────────────────────────────────
def test_2_unzip_and_automation():
    _header(2, "Unzip bundle + automation.csv parsing")
    from services import assistant_import_service as imp

    buf = io.BytesIO()
    root = "my-bundle (7)"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{root}/accounts.txt", "1. Name: Alpha, email: alpha@uploaded.invalid, from name: Alpha")
        zf.writestr(f"{root}/templates/a.html", "Subject: A\n<p>a</p>")
        zf.writestr(f"{root}/lists/x.csv", "email\nx@y.com\n")
        zf.writestr(f"{root}/automation.csv", "template,list,inboxes,send_timezone,start\na,x.csv,warmed,US/Eastern,yes\n")
        zf.writestr(f"__MACOSX/._{root}", b"macOS resource fork")
    files = imp.unzip_bundle(buf.getvalue())
    _assert(
        set(files) == {"accounts.txt", "templates/a.html", "lists/x.csv", "automation.csv"},
        "single enclosing macOS folder stripped and metadata ignored",
        f"{files.keys()}",
    )
    plan = imp.classify_bundle(files, use_orphan_ai=False)
    _assert(plan["accounts_file"] == "accounts.txt", "normalized accounts path remains resolvable", f"{plan}")

    rows = imp.parse_automation_csv(files["automation.csv"])
    _assert(len(rows) == 1 and rows[0]["template"] == "a" and rows[0]["start"] is True, "automation row parsed", f"{rows}")


# ── TEST 3: pairing engine — lists > templates (reuse templates) ──────────
def test_3_pairing_lists_more():
    _header(3, "Pairing — 8 lists, 3 templates → templates reused, lists never")
    from services import assistant_import_service as imp

    templates = [_mk_template("welcome", 1), _mk_template("followup", 2), _mk_template("offer", 3)]
    lists = [_mk_list(f"list-{i}", 100 + i) for i in range(8)]
    inboxes = [_mk_inbox("a@d.com", 1)]

    plan = imp.build_campaign_plan(templates, lists, inboxes)

    _assert(len(plan) == 8, "one campaign per list", f"{len(plan)}")
    used_list_ids = {p["list"].id for p in plan}
    _assert(len(used_list_ids) == 8, "no list reused", f"{used_list_ids}")
    used_tpl_counts = {}
    for p in plan:
        used_tpl_counts[p["template"].id] = used_tpl_counts.get(p["template"].id, 0) + 1
    _assert(all(c >= 2 for c in used_tpl_counts.values()) and len(used_tpl_counts) == 3, "templates reused round-robin", f"{used_tpl_counts}")


# ── TEST 4: pairing engine — templates > lists (lists used once) ──────────
def test_4_pairing_templates_more():
    _header(4, "Pairing — 8 templates, 3 lists → lists used once, spare templates unused")
    from services import assistant_import_service as imp

    templates = [_mk_template(f"tpl-{i}", i) for i in range(1, 9)]
    lists = [_mk_list("leads-a", 10), _mk_list("leads-b", 11), _mk_list("leads-c", 12)]
    inboxes = [_mk_inbox("a@d.com", 1)]

    plan = imp.build_campaign_plan(templates, lists, inboxes)

    _assert(len(plan) == 3, "3 campaigns for 3 lists", f"{len(plan)}")
    used_list_ids = {p["list"].id for p in plan}
    _assert(len(used_list_ids) == 3, "every list used exactly once", f"{used_list_ids}")
    _assert(len({p["template"].id for p in plan}) == 3, "3 distinct templates used, 5 spare", f"{[p['template'].name for p in plan]}")


# ── TEST 5: pairing engine — name-match first ─────────────────────────────
def test_5_pairing_name_match():
    _header(5, "Pairing — name-matched template↔list pair first")
    from services import assistant_import_service as imp

    templates = [_mk_template("welcome", 1), _mk_template("other", 2)]
    lists = [_mk_list("welcome", 10), _mk_list("leads", 11)]
    inboxes = [_mk_inbox("a@d.com", 1)]

    plan = imp.build_campaign_plan(templates, lists, inboxes)
    by_list = {p["list"].name: p["template"].name for p in plan}
    _assert(by_list.get("welcome") == "welcome", "welcome list ↔ welcome template by name", f"{by_list}")
    _assert(by_list.get("leads") == "other", "remaining list gets remaining template", f"{by_list}")


# ── TEST 6: automation.csv drives the plan ────────────────────────────────
def test_6_automation_plan():
    _header(6, "Automation rows drive template/list pairing + timezone + start")
    from services import assistant_import_service as imp

    templates = [_mk_template("welcome", 1), _mk_template("followup", 2)]
    lists = [_mk_list("leads-a", 10), _mk_list("leads-b", 11)]
    inboxes = [_mk_inbox("box1@d.com", 1), _mk_inbox("box2@d.com", 2)]

    rows = [
        {"template": "followup", "list": "leads-b", "inboxes": "box2@d.com", "send_timezone": "US/Pacific", "start": True},
    ]
    plan = imp.build_campaign_plan(templates, lists, inboxes, automation_rows=rows)

    by_list = {p["list"].name: p for p in plan}
    _assert(by_list["leads-b"]["template"].name == "followup", "automation pairing honored", f"{by_list['leads-b']['template'].name}")
    _assert(by_list["leads-b"]["send_timezone"] == "US/Pacific" and by_list["leads-b"]["start"] is True, "timezone+start honored", f"{by_list['leads-b']}")
    _assert(by_list["leads-b"]["inbox_ids"] == [2], "explicit inbox honored", f"{by_list['leads-b']['inbox_ids']}")
    _assert(by_list["leads-a"]["template"].name == "welcome", "uncovered list falls back to round-robin", f"{by_list['leads-a']['template'].name}")


# ── TEST 7: JSON extraction from LLM output ───────────────────────────────
def test_7_json_extraction():
    _header(7, "LLM JSON extraction — fences + inline objects")
    from services import assistant_import_service as imp

    a = imp._extract_json_from_text('```json\n{"accounts": [{"name": "x"}]}\n```')
    _assert(isinstance(a, dict) and a["accounts"][0]["name"] == "x", "fenced json parsed", f"{a}")
    b = imp._extract_json_from_text('here you go: {"roles": {"a.txt": "accounts"}} done')
    _assert(isinstance(b, dict) and b["roles"]["a.txt"] == "accounts", "inline json parsed", f"{b}")
    c = imp._extract_json_from_text("no json here")
    _assert(c is None, "garbage returns None", f"{c}")


# ── TEST 8: context storage roundtrip ─────────────────────────────────────
def test_8_context_roundtrip():
    _header(8, "Conversation import context stored + loaded")
    _flush()
    from services import assistant_import_service as imp

    _assert(imp.load_import_context(999) is None, "missing context returns None", "")

    import redis as _redis_mod
    r = _redis_mod.from_url(TEST_REDIS_URL, decode_responses=True)
    r.setex(imp.CONTEXT_KEY.format(conversation_id=999), 600, json.dumps({"template_ids": [1], "list_ids": [2]}))

    ctx = imp.load_import_context(999)
    _assert(ctx and ctx["template_ids"] == [1], "context roundtrip works", f"{ctx}")
    _flush()


# ── TEST 9: Taker-style account parsing + universal domain ────────────────
def test_9_account_prefixes():
    _header(9, "Taker-style accounts use local part + universal domain")
    from services import assistant_import_service as imp

    content = (
        "SES sending accounts:\n"
        "1. Name: AIG, email: Carol-Y.Cao@wrong-domain.example, from name: Carol Cao\n"
        "2. Name: Brattle, email: marcel.kemp, from name: Marcel Kemp\n"
        "3. Name: BIPO, email: Sarah.Egan@anything.test, from name: Sarah Egan\n"
        "4.\nAccount Name: ConMet\nFrom Email Prefix: conmet.team\nSender Name: ConMet Team\n"
    ).encode()
    with patch.dict(os.environ, {"SES_VERIFIED_DOMAIN": "Universal.Example"}, clear=False):
        accounts = imp.extract_accounts("accounts.txt", content)

    _assert(len(accounts) == 4, "all structured account rows parsed deterministically", f"{accounts}")
    _assert(
        [item["from_email"] for item in accounts] == [
            "carol-y.cao@universal.example",
            "marcel.kemp@universal.example",
            "sarah.egan@universal.example",
            "conmet.team@universal.example",
        ],
        "uploaded domains ignored and prefixes normalized",
        f"{accounts}",
    )


# ── TEST 10: friendly variable syntax ─────────────────────────────────────
def test_10_variable_canonicalization():
    _header(10, "Friendly brace styles and semantic aliases canonicalize")
    from services import assistant_import_service as imp

    raw = (
        "Hi {firstname} / {{ First Name }} / [client name]. "
        "Open {{agreement name}} at {{Microsoft eSignature agreement link}}. "
        "See {{reporting period|this month}}."
    )
    canonical = imp.canonicalize_template_variables(raw)
    _assert(canonical.count("{{first_name}}") == 2, "single/double first-name braces normalized", canonical)
    _assert("{{full_name}}" in canonical, "known bracket alias normalized", canonical)
    _assert("{{agreement_name}}" in canonical, "spaced agreement name normalized", canonical)
    _assert(
        "{{microsoft_esignature_agreement_link}}" in canonical,
        "Microsoft link alias normalized",
        canonical,
    )
    _assert("{{reporting_period|this month}}" in canonical, "fallback preserved", canonical)


# ── TEST 11: realistic name inference + neutral fallback ──────────────────
def test_11_recipient_names():
    _header(11, "Realistic email names are stored; unclear handles render as there")
    from services.template_engine import TemplateEngine, infer_recipient_name
    from services import assistant_import_service as imp

    _assert(infer_recipient_name("arti.johri@aig.com") == ("Arti", "Johri"), "delimited full name inferred", "")
    _assert(infer_recipient_name("m.galingana@example.com") == ("", "Galingana"), "initial not invented as first name", "")
    _assert(infer_recipient_name("zswilliams@example.com") == ("", ""), "concatenated handle left blank", "")
    _assert(infer_recipient_name("98kkcvktt@example.com") == ("", ""), "random handle left blank", "")
    _assert(infer_recipient_name("support@example.com") == ("", ""), "generic mailbox left blank", "")
    explicit = imp._parse_list_csv("clients.csv", b"email,client_name\njohn.smith@example.com,Jay\n")["rows"][0]
    _assert(explicit["first_name"] == "Jay" and explicit["last_name"] == "", "explicit client name outranks email inference", f"{explicit}")
    loose = imp._parse_loose_list("clients.txt", b"98kkcvktt@example.com Jane Doe\n")["rows"][0]
    _assert(loose["first_name"] == "Jane" and loose["last_name"] == "Doe", "loose-list name after email is preserved", f"{loose}")
    semicolon = imp._parse_list_csv("clients.csv", b"E-mail;Full Name\nalex.roe@example.com;Alex Roe\n")["rows"][0]
    _assert(semicolon["first_name"] == "Alex" and semicolon["last_name"] == "Roe", "semicolon CSV aliases parse without exact headers", f"{semicolon}")
    rendered = TemplateEngine("https://example.test").render(
        "Hi {{first_name}}, hello {{client_name}}.",
        {"email": "98kkcvktt@example.com"},
    )
    _assert(rendered == "Hi there, hello there.", "missing names receive neutral greeting fallback", rendered)


# ── TEST 12: opaque values + scope ────────────────────────────────────────
def test_12_variable_scope():
    _header(12, "Opaque tagged values require shared scope once and support overrides")
    from services import assistant_import_service as imp

    context = {
        "requirements": [
            {"template_id": 1, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "value": ""},
            {"template_id": 2, "template": "Chambers", "variable": "linkedin_url", "category": "linkedin", "resolved": False, "value": ""},
            {"template_id": 3, "template": "BIPO", "variable": "microsoft_esignature_agreement_link", "category": "microsoft", "resolved": False, "value": ""},
        ],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(context, "LinkedIn: opaque-value-not-a-url")
    _assert(not result["applied"] and result["scope_requests"][0]["count"] == 2, "bare shared tag asks once for scope", f"{result}")
    _assert(not context["template_values"], "ambiguous value not applied early", f"{context}")

    result = imp.resolve_variable_message(context, "Use it for both")
    _assert(len(result["applied"]) == 2, "follow-up scope applies to both matching templates", f"{result}")
    _assert(
        context["template_values"]["1"]["linkedin"] == "opaque-value-not-a-url"
        and context["template_values"]["2"]["linkedin"] == "opaque-value-not-a-url",
        "opaque value persisted verbatim without validation",
        f"{context['template_values']}",
    )

    result = imp.resolve_variable_message(context, "Alstom LinkedIn: replacement")
    _assert(len(result["applied"]) == 1, "named template limits assignment scope", f"{result}")
    _assert(
        context["template_values"]["1"]["linkedin"] == "replacement"
        and context["template_values"]["2"]["linkedin"] == "opaque-value-not-a-url",
        "latest explicit instruction replaces only its target",
        f"{context['template_values']}",
    )
    result = imp.resolve_variable_message(context, "Microsoft: definitely-not-a-link")
    _assert(len(result["applied"]) == 1, "single matching custom value applies immediately", f"{result}")
    _assert(
        imp.resolve_variable_message(context, "all") is None
        and imp.resolve_variable_message(context, "confirm") is None
        and context["pending_assignment"] is None,
        "one-word replies are not captured as values after every requirement is resolved",
        f"{context}",
    )

    named_context = {
        "requirements": context["requirements"],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(named_context, "Use one-opaque-value for Alstom and Chambers")
    _assert(len(result["applied"]) == 2, "natural instruction applies one value to several named templates", f"{result}")

    affirmative_context = {
        "requirements": [
            {"template_id": 1, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "value": ""},
            {"template_id": 2, "template": "Chambers", "variable": "linkedin_url", "category": "linkedin", "resolved": False, "value": ""},
        ],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(affirmative_context, "LinkedIn: shared-value")
    _assert(bool(result["scope_requests"]), "shared assignment waits for one scope answer", f"{result}")
    result = imp.resolve_variable_message(affirmative_context, "yes use it")
    _assert(
        len(result["applied"]) == 2 and affirmative_context["pending_assignment"] is None,
        "conversational yes applies the pending value to all templates",
        f"{result} / {affirmative_context}",
    )

    skip_context = {
        "requirements": [
            {"template_id": 1, "template": "BIPO", "variable": "agreement_name", "category": "agreement_name", "resolved": False, "value": ""},
            {"template_id": 2, "template": "ConMet", "variable": "agreement_name", "category": "agreement_name", "resolved": False, "value": ""},
            {"template_id": 3, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "value": ""},
        ],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(skip_context, "Agreement Name: arollanni")
    _assert(bool(result["scope_requests"]), "agreement value waits for scope", f"{result}")
    result = imp.resolve_variable_message(skip_context, "create them without those values")
    _assert(
        result["intent"] == "skip_values" and result["proceed"] and len(result["skipped"]) == 3,
        "explicit create-without-values request skips every unresolved value and proceeds",
        f"{result}",
    )
    _assert(
        skip_context["pending_assignment"] is None
        and not any(values for values in skip_context["template_values"].values()),
        "stale pending assignment is cleared instead of being applied by the word those",
        f"{skip_context}",
    )
    result = imp.resolve_variable_message(skip_context, "LinkedIn: later-value")
    linkedin_requirement = next(item for item in skip_context["requirements"] if item["category"] == "linkedin")
    _assert(
        len(result["applied"]) == 1 and not linkedin_requirement["skipped"],
        "a later explicit value reverses an earlier blank choice",
        f"{result} / {linkedin_requirement}",
    )

    selective_context = {
        "requirements": [
            {"template_id": 1, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "value": ""},
            {"template_id": 2, "template": "Chambers", "variable": "linkedin_url", "category": "linkedin", "resolved": False, "value": ""},
            {"template_id": 3, "template": "BIPO", "variable": "microsoft_esignature_agreement_link", "category": "microsoft", "resolved": False, "value": ""},
        ],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(selective_context, "leave LinkedIn blank")
    _assert(
        len(result["skipped"]) == 2 and len(result["remaining"]) == 1 and not result["proceed"],
        "category-specific blank request leaves unrelated requirements pending",
        f"{result}",
    )

    question_context = {
        "requirements": [
            {"template_id": 1, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "value": ""},
        ],
        "template_values": {},
        "pending_assignment": None,
    }
    result = imp.resolve_variable_message(question_context, "Can I create them without those values?")
    _assert(
        result["intent"] == "confirm_skip_values" and not question_context["requirements"][0]["resolved"],
        "question about blanks explains consequences without mutating review state",
        f"{result} / {question_context}",
    )


# ── TEST 13: stable bundle fingerprint ────────────────────────────────────
def test_13_bundle_fingerprint():
    _header(13, "Bundle identity is stable and content-sensitive")
    from services import assistant_import_service as imp

    first = {"b.txt": b"two", "a.txt": b"one"}
    reordered = {"a.txt": b"one", "b.txt": b"two"}
    changed = {"a.txt": b"ONE", "b.txt": b"two"}
    _assert(imp.bundle_fingerprint(first) == imp.bundle_fingerprint(reordered), "archive ordering does not change identity", "")
    _assert(imp.bundle_fingerprint(first) != imp.bundle_fingerprint(changed), "file content changes identity", "")


# ── TEST 14: stage first, then idempotent materialization ─────────────────
def test_14_staged_import_lifecycle():
    _header(14, "Bundle stage is non-mutating; approval materializes once")
    _flush()
    import models  # noqa: F401 - register full metadata
    from database import Base
    from api.assistant import CampaignsActionRequest, confirm_campaigns
    from models.campaign import Campaign
    from models.inbox import Inbox
    from models.list import RecipientList
    from models.recipient import Recipient
    from models.ses_template import SESEmailTemplate
    from models.smtp_account import SMTPAccount
    from models.user import User
    from services.assistant_service import create_conversation
    from services import assistant_import_service as imp

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    db = TestSession()
    user = User(email="stage@test.local", password_hash="x", name="Stage", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    conversation = create_conversation(db, user.id, title="Bundle lifecycle")
    files = {
        "accounts.txt": b"1. Name: AIG, email: carol@uploaded.invalid, from name: Carol Cao\n",
        "lists/AIG.csv": b"email\narti.johri@example.com\nzswilliams@example.com\n",
        "templates/AIG.txt": b"Subject: Hello {firstname}\nHi {{first name}}, see {{LinkedIn URL}}.",
        "templates/AIG.pdf": b"%PDF-fake",
    }

    with patch.dict(os.environ, {"SES_VERIFIED_DOMAIN": "universal.example"}, clear=False), \
            patch.object(imp, "_ai_classify_orphans", return_value={}) as orphan_ai, \
            patch.object(imp, "_ai_bundle_hints", return_value={}) as bundle_ai:
        summary = imp.stage_import(db, user, files, conversation_id=conversation.id)
        _assert(bundle_ai.call_count == 1, "one whole-bundle semantic pass performed", f"{bundle_ai.call_count}")
        _assert(orphan_ai.call_count == 0, "legacy isolated orphan AI skipped during staging", f"{orphan_ai.call_count}")

        database_counts = (
            db.query(SMTPAccount).count(),
            db.query(Inbox).count(),
            db.query(RecipientList).count(),
            db.query(SESEmailTemplate).count(),
        )
        _assert(database_counts == (0, 0, 0, 0), "stage creates no database resources", f"{database_counts}")
        context = imp.load_import_context(conversation.id)
        _assert(context and context["materialized"] is False, "structured staged manifest persisted", f"{context}")
        _assert(context["inferred_name_count"] == 1, "review reports recipient names inferred from email prefixes", f"{context['inferred_name_count']}")
        _assert(summary["unresolved_count"] == 1, "custom LinkedIn value blocks approval", f"{summary}")
        _assert(context["manifest"]["templates"][0]["attachment_names"], "matching PDF staged as attachment", f"{context['manifest']}")

        assignment = imp.resolve_variable_message(context, "LinkedIn: opaque")
        _assert(len(assignment["applied"]) == 1, "single staged value resolves immediately", f"{assignment}")
        imp.save_import_context(context)

        materialized, materialized_context = imp.materialize_staged_import(db, user, context)
        _assert(materialized_context["materialized"] is True, "approved plan marked materialized", f"{materialized_context}")
        _assert(
            (db.query(SMTPAccount).count(), db.query(Inbox).count(), db.query(RecipientList).count(), db.query(SESEmailTemplate).count()) == (1, 1, 1, 1),
            "approval creates exactly one of each resource",
            "unexpected materialized counts",
        )
        _assert(db.query(SMTPAccount).one().provider_type == "brevo", "account uses the Resend API path", "unexpected provider path")
        _assert(materialized["unresolved_count"] == 0, "resolved value survives logical-to-database ID translation", f"{materialized}")

        repeated = imp.stage_import(db, user, files, conversation_id=conversation.id)
        _assert(repeated["bundle_reused"] is True and repeated["materialized"] is True, "identical bundle reuses prior materialized plan", f"{repeated}")
        _assert(bundle_ai.call_count == 1, "identical bundle does not repeat semantic analysis", f"{bundle_ai.call_count}")
        _assert(
            (db.query(SMTPAccount).count(), db.query(Inbox).count(), db.query(RecipientList).count(), db.query(SESEmailTemplate).count()) == (1, 1, 1, 1),
            "re-upload creates no numbered duplicate resources",
            "duplicate resources created",
        )

        mapping = materialized_context["mappings"][0]
        plan_payload = [{
            "template_id": mapping["template_id"],
            "list_id": mapping["list_id"],
            "inbox_ids": mapping["inbox_ids"],
            "send_timezone": mapping["send_timezone"],
            "bundle_hash": materialized_context["bundle_hash"],
        }]
        _r().setex(f"assistant:conv:{conversation.id}:plan", 1800, json.dumps(plan_payload))
        first_confirmation = confirm_campaigns(
            CampaignsActionRequest(conversation_id=conversation.id),
            current_user=user,
            db=db,
        )
        _assert(first_confirmation["action"]["created"] == 1, "first final confirmation creates one draft campaign", f"{first_confirmation}")
        campaign = db.query(Campaign).filter(Campaign.user_id == user.id).one()
        _assert(campaign.status.value == "draft", "assistant-created campaign remains draft", f"{campaign.status}")

        materialized_context["template_values"][str(mapping["template_id"])]["linkedin"] = "fresh-opaque-value"
        imp.save_import_context(materialized_context)
        _r().setex(f"assistant:conv:{conversation.id}:plan", 1800, json.dumps(plan_payload))
        second_confirmation = confirm_campaigns(
            CampaignsActionRequest(conversation_id=conversation.id),
            current_user=user,
            db=db,
        )
        db.refresh(campaign)
        _assert(
            second_confirmation["action"]["reused"] == 1 and db.query(Campaign).filter(Campaign.user_id == user.id).count() == 1,
            "exact import identity reuses its existing draft",
            f"{second_confirmation}",
        )
        _assert(campaign.template_data["linkedin"] == "fresh-opaque-value", "reused draft receives fresh template values", f"{campaign.template_data}")
        _assert(bool(campaign.template_data.get("_assistant_import_hash")), "draft stores deterministic import identity", f"{campaign.template_data}")

        original_template = db.query(SESEmailTemplate).filter(SESEmailTemplate.user_id == user.id).one()
        original_template_id = original_template.id
        original_version = int(original_template.version or 1)
        os.environ["SES_VERIFIED_DOMAIN"] = "new-universal.example"
        changed_files = {
            "accounts.txt": files["accounts.txt"],
            "lists/AIG.csv": (
                b"email\narti.johri@example.com\nnew.person@example.com\nnew.person@example.com\n"
            ),
            "templates/AIG.txt": b"Subject: Revised {firstname}\nHi {{first name}}, updated {{LinkedIn URL}}.",
            "templates/AIG.pdf": b"%PDF-replaced-content",
        }
        changed_stage = imp.stage_import(db, user, changed_files, conversation_id=conversation.id)
        changed_context = imp.load_import_context(conversation.id)
        _assert(
            changed_stage["accounts_updated"] == 1 and changed_stage["inboxes_updated"] == 1,
            "stable prefix identities are predicted as in-place universal-domain updates",
            f"{changed_stage}",
        )
        _assert(
            changed_stage["lists_updated"] == 1 and changed_stage["templates_updated"] == 1,
            "modified list and template are predicted as updates",
            f"{changed_stage}",
        )
        changed_assignment = imp.resolve_variable_message(changed_context, "LinkedIn: changed-opaque-value")
        _assert(bool(changed_assignment and changed_assignment["applied"]), "changed bundle custom value resolved", f"{changed_assignment}")
        imp.save_import_context(changed_context)
        changed_summary, _changed_materialized = imp.materialize_staged_import(db, user, changed_context)
        updated_template = db.query(SESEmailTemplate).filter(SESEmailTemplate.user_id == user.id).one()
        _assert(
            updated_template.id == original_template_id and int(updated_template.version or 1) == original_version + 1,
            "changed template updates in place and increments its version",
            f"id={updated_template.id}, version={updated_template.version}",
        )
        with open(updated_template.attachments[0]["filepath"], "rb") as attachment_file:
            updated_attachment_content = attachment_file.read()
        _assert(
            len(updated_template.attachments or []) == 1
            and updated_attachment_content == b"%PDF-replaced-content",
            "same-name changed attachment replaces old content without duplication",
            f"{updated_template.attachments}",
        )
        updated_list = db.query(RecipientList).filter(RecipientList.user_id == user.id).one()
        _assert(
            db.query(Recipient).filter(Recipient.list_id == updated_list.id).count() == 3,
            "recipient merge adds one normalized email and ignores repeated rows",
            f"count={updated_list.recipient_count}",
        )
        _assert(
            changed_summary["templates_updated"] == 1
            and changed_summary["lists_updated"] == 1
            and changed_summary["accounts_updated"] == 1
            and changed_summary["inboxes_updated"] == 1
            and db.query(SMTPAccount).filter(SMTPAccount.user_id == user.id).count() == 1
            and db.query(SMTPAccount).filter(SMTPAccount.user_id == user.id).one().from_email == "carol@new-universal.example"
            and db.query(Inbox).filter(Inbox.user_id == user.id).one().email == "carol@new-universal.example",
            "materialized updates retain stable resource identities",
            f"{changed_summary}",
        )
    db.close()
    _flush()


# ── TEST 15: constrained whole-bundle AI file roles ──────────────────────
def test_15_ai_file_roles():
    _header(15, "Whole-bundle AI roles can classify safe orphan files before approval")
    from services import assistant_import_service as imp

    files = {
        "campaign.md": b"Subject: AI-classified\nHi {{first_name}}, use {{LinkedIn URL}}.",
        "campaign-resource.bin": b"opaque-attachment-content",
    }
    plan = imp.classify_bundle(files, use_orphan_ai=False)
    changed = imp._apply_ai_file_roles(plan, files, {
        "file_roles": [
            {"filename": "campaign.md", "role": "template", "mode": "mixed"},
            {"filename": "campaign-resource.bin", "role": "attachment", "template": "Campaign"},
        ],
    })
    _assert(changed and len(plan["templates"]) == 1, "orphan markdown promoted to a reviewed template", f"{plan}")
    _assert(len(plan["templates"][0]["attachments"]) == 1, "AI-targeted orphan retained as an attachment", f"{plan['templates'][0]}")
    _assert(not plan["orphans"], "successfully classified files removed from blockers", f"{plan['orphans']}")


# ── TEST 16: conversational relationship correction ──────────────────────
def test_16_mapping_correction():
    _header(16, "Unclear relationships can be corrected conversationally before mutation")
    from services import assistant_import_service as imp

    context = {
        "materialized": False,
        "manifest": {
            "accounts": [
                {"id": 1, "source_name": "Alpha Outreach", "name": "Alpha Outreach", "inbox_id": 1},
                {"id": 2, "source_name": "Beta Outreach", "name": "Beta Outreach", "inbox_id": 2},
            ],
            "lists": [{"id": 1, "name": "Leads A", "source_name": "Leads A", "recipient_count": 12}],
            "templates": [
                {
                    "id": 1, "name": "Welcome", "source_name": "welcome.txt", "identity": "welcome",
                    "subject": "Welcome", "body": "Use {{linkedin}}", "variables": ["linkedin"],
                    "attachment_names": [], "template_type": "plain_text",
                },
                {
                    "id": 2, "name": "Followup", "source_name": "followup.txt", "identity": "followup",
                    "subject": "Followup", "body": "Hello", "variables": [],
                    "attachment_names": [], "template_type": "plain_text",
                },
            ],
        },
        "mappings": [{
            "template_id": 2, "list_id": 1, "account_id": None, "inbox_ids": [],
            "send_timezone": "US/Eastern", "start": False, "source": "fallback",
        }],
        "relationship_issues": ["Confirm which template belongs to list Leads A"],
        "ai_hints": {},
        "requirements": [],
        "template_values": {},
    }
    result = imp.resolve_mapping_message(context, "Use Welcome for Leads A with Alpha Outreach")
    mapping = context["mappings"][0]
    _assert(
        bool(result) and mapping["template_id"] == 1 and mapping["list_id"] == 1 and mapping["account_id"] == 1,
        "named template, list, and sender account replace the fallback mapping",
        f"{result} / {mapping}",
    )
    _assert(not context["relationship_issues"] and mapping["source"] == "user", "explicit correction clears structural blocker", f"{context}")
    _assert(len(imp.unresolved_requirements(context)) == 1, "newly mapped template variable is added to review", f"{context['requirements']}")


# ── TEST 17: early logical duplicate consolidation ────────────────────────
def test_17_plan_duplicates():
    _header(17, "Duplicate bundle resources consolidate before IDs and mappings")
    from services import assistant_import_service as imp

    plan = imp.classify_bundle({
        "lists/Leads-A.csv": b"email,first_name\none@example.com,One\n",
        "lists/leads_a.csv": b"email,company\none@example.com,Acme\ntwo@example.com,Globex\n",
        "templates/Welcome-A.txt": b"Subject: Hello\nBody",
        "templates/welcome_a.txt": b"Subject: Hello\nBody",
    }, use_orphan_ai=False)
    issues = imp._deduplicate_plan_resources(plan)
    _assert(len(plan["lists"]) == 1 and len(plan["lists"][0]["rows"]) == 2, "same-identity lists merge by normalized recipient email", f"{plan['lists']}")
    merged_one = next(row for row in plan["lists"][0]["rows"] if row["email"] == "one@example.com")
    _assert(merged_one["first_name"] == "One" and merged_one["company"] == "Acme", "duplicate recipient fields merge without replacement loss", f"{merged_one}")
    _assert(len(plan["templates"]) == 1 and not issues, "identical same-identity templates consolidate", f"{plan['templates']} / {issues}")

    conflicting = imp.classify_bundle({
        "templates/Welcome-A.txt": b"Subject: Hello\nBody one",
        "templates/welcome_a.txt": b"Subject: Hello\nBody two",
    }, use_orphan_ai=False)
    conflict_issues = imp._deduplicate_plan_resources(conflicting)
    _assert(len(conflicting["templates"]) == 1 and len(conflict_issues) == 1, "conflicting same-identity templates block instead of creating numbered copies", f"{conflict_issues}")


def test_18_variable_template_previews():
    _header(18, "Variable cards expose only their matching staged template previews")
    from services import assistant_import_service as imp

    context = {
        "requirements": [
            {"template_id": 1, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False},
            {"template_id": 2, "template": "Chambers", "variable": "linkedin_url", "category": "linkedin", "resolved": True, "skipped": True},
            {"template_id": 3, "template": "BIPO", "variable": "agreement_name", "category": "agreement_name", "resolved": False},
        ],
        "manifest": {
            "templates": [
                {"id": 1, "name": "Alstom", "subject": "Alstom update", "body": "<p>Visit {{linkedin}}</p>", "template_type": "html", "attachment_names": []},
                {"id": 2, "name": "Chambers", "subject": "Chambers update", "body": "<p>Visit {{linkedin_url}}</p>", "template_type": "html", "attachment_names": ["terms.pdf"]},
                {"id": 3, "name": "BIPO", "subject": "Agreement", "body": "<p>{{agreement_name}}</p>", "template_type": "html", "attachment_names": []},
            ],
        },
    }

    listing = imp.variable_template_previews(context, "LinkedIn")
    _assert([item["id"] for item in listing] == [1, 2], "category lists only matching templates with unresolved first", f"{listing}")
    _assert(all("body" not in item for item in listing), "template list omits full bodies", f"{listing}")
    _assert(listing[1]["skipped_variables"] == ["linkedin_url"], "preview distinguishes a blank choice from a supplied value", f"{listing}")
    selected = imp.variable_template_previews(context, "linkedin", template_id=2)
    _assert(len(selected) == 1 and selected[0]["body"] == "<p>Visit {{linkedin_url}}</p>", "selected template includes its full preview body", f"{selected}")
    _assert(not imp.variable_template_previews(context, "linkedin", template_id=3), "template from another category cannot be loaded", "unexpected cross-category preview")

    staged_context = {
        "manifest": {
            "accounts": [],
            "lists": [],
            "templates": [{"id": 1, "name": "Alstom", "identity": "alstom"}],
        },
        "mappings": [],
        "template_values": {"1": {}},
        "requirements": [{
            "id": "1:linkedin",
            "template_id": 1,
            "template": "Alstom",
            "variable": "linkedin",
            "category": "linkedin",
            "value": "",
            "resolved": True,
            "skipped": True,
        }],
        "ai_hints": {},
    }
    actual_templates = [{
        "id": 77,
        "name": "Alstom",
        "identity": "alstom",
        "subject": "Update",
        "body": "<p>{{linkedin}}</p>",
        "variables": ["linkedin"],
        "attachment_names": [],
    }]
    _mappings, translated, translated_hints = imp._translate_staged_state(
        staged_context,
        [],
        [],
        actual_templates,
    )
    rebuilt_requirements, _values = imp._build_variable_state(
        actual_templates,
        translated_hints,
        translated,
    )
    _assert(
        rebuilt_requirements[0]["template_id"] == 77
        and rebuilt_requirements[0]["resolved"]
        and rebuilt_requirements[0]["skipped"],
        "intentional blank survives staged-to-materialized template ID translation",
        f"{translated} / {rebuilt_requirements}",
    )


def test_19_context_aware_chat_routing():
    _header(19, "Bundle chat understands create-without-values in context")
    import models  # noqa: F401 - register full metadata
    from database import Base
    from api import assistant as assistant_api
    from models.user import User
    from services.assistant_service import create_conversation
    from services import assistant_import_service as imp
    from services import llm_provider

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    db = TestSession()
    user = User(email="smart-chat@test.local", password_hash="x", name="Smart Chat", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    conversation = create_conversation(db, user.id, title="Smart bundle chat")
    context = {
        "conversation_id": conversation.id,
        "user_id": user.id,
        "materialized": False,
        "requirements": [
            {"template_id": 1, "template": "BIPO", "variable": "agreement_name", "category": "agreement_name", "resolved": True, "skipped": False, "value": "arollanni"},
            {"template_id": 2, "template": "Knick", "variable": "application", "category": "application", "resolved": False, "skipped": False, "value": ""},
            {"template_id": 3, "template": "Alstom", "variable": "linkedin", "category": "linkedin", "resolved": False, "skipped": False, "value": ""},
        ],
        "template_values": {"1": {"agreement_name": "arollanni"}, "2": {}, "3": {}},
        "pending_assignment": None,
        "manifest": {"templates": [{"id": 1}, {"id": 2}, {"id": 3}], "lists": [{"id": 1}]},
        "mappings": [],
        "relationship_issues": [],
        "errors": [],
    }
    plan_action = {
        "type": "create_campaigns_plan",
        "status": "pending",
        "rows": [],
        "skipped_count": 2,
        "skipped_categories": ["application", "linkedin"],
    }
    with patch.object(imp, "load_import_context", return_value=context), \
            patch.object(imp, "save_import_context") as save_context, \
            patch.object(assistant_api, "_prepare_campaign_plan", return_value=("Campaign review is ready.", plan_action)) as prepare_plan, \
            patch.object(llm_provider, "chat", side_effect=AssertionError("generic LLM chat should not run")):
        response = assistant_api.chat_with_assistant(
            assistant_api.ChatRequest(
                message="create them without those values",
                conversation_id=conversation.id,
            ),
            current_user=user,
            db=db,
        )

    _assert(save_context.call_count == 1, "blank decision is persisted before campaign preparation", f"calls={save_context.call_count}")
    _assert(prepare_plan.call_count == 1, "explicit create command advances directly to campaign review", f"calls={prepare_plan.call_count}")
    _assert(response.action and response.action["type"] == "create_campaigns_plan", "chat returns the real campaign review action", f"{response.action}")
    _assert("left **2 custom value(s)** blank" in response.reply and "Campaign review is ready" in response.reply, "reply explains the decision and next state", response.reply)
    _assert(
        context["requirements"][0]["value"] == "arollanni"
        and not context["requirements"][0]["skipped"]
        and all(item["skipped"] for item in context["requirements"][1:]),
        "existing value is preserved while only unresolved values are left blank",
        f"{context['requirements']}",
    )
    db.close()


def test_20_date_shifted_account_matching():
    _header(20, "Account matching ignores trailing campaign dates but remains ambiguity-safe")
    from services import assistant_import_service as imp

    accounts = [
        {
            "id": 1,
            "source_name": "China_Media_Coverage_Summary_2026-09-07",
            "name": "China_Media_Coverage_Summary_2026-09-07",
            "from_name": "Carol Cao",
            "inbox_id": 101,
        },
        {
            "id": 17,
            "source_name": "Keller_North_America_Pre-Construction_Update_2026-09-07",
            "name": "Keller_North_America_Pre-Construction_Update_2026-09-07",
            "from_name": "Shuihan (Bill) Li",
            "inbox_id": 117,
        },
        {
            "id": 27,
            "source_name": "Unrelated_Account_2026-09-07",
            "name": "Unrelated_Account_2026-09-07",
            "from_name": "Different Sender",
            "inbox_id": 127,
        },
    ]
    lists = [
        {"id": 1, "name": "Aig", "source_name": "Aig"},
        {"id": 17, "name": "Keller North America", "source_name": "Keller North America"},
    ]
    templates = [
        {
            "id": 7,
            "name": "China Media Coverage Summary 2026 09 09",
            "source_name": "China_Media_Coverage_Summary_2026-09-09.txt",
            "subject": "Daily China Media Coverage Summary",
            "body": "Best regards, Carol Y. Cao, AIG",
        },
        {
            "id": 18,
            "name": "Keller North America Pre Construction Update 2026 09 09",
            "source_name": "Keller_North_America_Pre-Construction_Update_2026-09-09.txt",
            "subject": "Pre-Construction Review Update",
            "body": "Best regards, Shuihan Li, Keller North America",
        },
    ]
    mappings = imp._build_smart_mappings(
        accounts,
        lists,
        templates,
        {
            "mappings": [
                {"template_id": 7, "list_id": 1},
                {"template_id": 18, "list_id": 17},
            ],
        },
    )
    by_list = {mapping["list_id"]: mapping for mapping in mappings}
    _assert(
        by_list[1]["account_id"] == 1 and by_list[1]["inbox_ids"] == [101],
        "China account matches its template when only the trailing date changed",
        f"{by_list[1]}",
    )
    _assert(
        by_list[17]["account_id"] == 17 and by_list[17]["inbox_ids"] == [117],
        "Keller account matches its template when only the trailing date changed",
        f"{by_list[17]}",
    )

    ambiguous = imp._build_smart_mappings(
        [
            {"id": 1, "source_name": "Regional_Update_2026-09-07", "name": "Regional_Update_2026-09-07", "from_name": "", "inbox_id": 1},
            {"id": 2, "source_name": "Regional_Update_2026-09-08", "name": "Regional_Update_2026-09-08", "from_name": "", "inbox_id": 2},
        ],
        [{"id": 1, "name": "Recipients", "source_name": "Recipients"}],
        [{"id": 1, "name": "Regional Update", "source_name": "Regional_Update_2026-09-09.txt", "subject": "Update", "body": "Body"}],
        {"mappings": [{"template_id": 1, "list_id": 1}]},
    )[0]
    _assert(
        ambiguous["account_id"] is None and not ambiguous["inbox_ids"],
        "equal date-neutral account matches still require explicit confirmation",
        f"{ambiguous}",
    )


def test_21_friendly_clarification_language():
    _header(21, "Clarification prompts explain the choice in plain language")
    import models  # noqa: F401 - register full metadata
    from database import Base
    from api import assistant as assistant_api
    from models.user import User
    from services.assistant_service import create_conversation
    from services import assistant_import_service as imp
    from services import llm_provider

    account_issue = imp.friendly_relationship_issue(
        "Confirm the sending account for template Welcome Email"
    )
    _assert(
        "Welcome Email" in account_issue
        and "wrong address" in account_issue
        and "Confirm the sending account" not in account_issue,
        "legacy account blockers explain the uncertainty and safety reason",
        account_issue,
    )
    template_issue = imp.friendly_relationship_issue(
        "Confirm which template belongs to list Customer Leads"
    )
    _assert(
        "Customer Leads" in template_issue
        and "correct template" in template_issue
        and "Confirm which template" not in template_issue,
        "legacy template blockers ask for the needed choice without technical shorthand",
        template_issue,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    db = TestSession()
    user = User(email="friendly-chat@test.local", password_hash="x", name="Friendly", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    conversation = create_conversation(db, user.id, title="Friendly clarification")
    context = {
        "conversation_id": conversation.id,
        "user_id": user.id,
        "materialized": False,
        "requirements": [
            {"template_id": 1, "template": "Welcome", "variable": "linkedin", "category": "linkedin", "resolved": False, "skipped": False, "value": ""},
            {"template_id": 2, "template": "Agreement", "variable": "microsoft", "category": "microsoft", "resolved": False, "skipped": False, "value": ""},
        ],
        "template_values": {"1": {}, "2": {}},
        "pending_assignment": None,
        "manifest": {"accounts": [], "templates": [{"id": 1}, {"id": 2}], "lists": []},
        "mappings": [],
        "relationship_issues": [],
        "errors": [],
    }
    with patch.object(imp, "load_import_context", return_value=context), \
            patch.object(imp, "save_import_context"), \
            patch.object(llm_provider, "chat", side_effect=AssertionError("generic LLM chat should not run")):
        response = assistant_api.chat_with_assistant(
            assistant_api.ChatRequest(message="opaque-value", conversation_id=conversation.id),
            current_user=user,
            db=db,
        )

    _assert(
        "not sure where that value should go" in response.reply
        and "wrong email" in response.reply
        and "Linkedin" in response.reply
        and "Microsoft" in response.reply
        and "Current tags" not in response.reply,
        "an unlabelled value gets a clear explanation, choices, and example",
        response.reply,
    )
    db.close()


def main():
    for t in [
        test_1_classify_bundle,
        test_2_unzip_and_automation,
        test_3_pairing_lists_more,
        test_4_pairing_templates_more,
        test_5_pairing_name_match,
        test_6_automation_plan,
        test_7_json_extraction,
        test_8_context_roundtrip,
        test_9_account_prefixes,
        test_10_variable_canonicalization,
        test_11_recipient_names,
        test_12_variable_scope,
        test_13_bundle_fingerprint,
        test_14_staged_import_lifecycle,
        test_15_ai_file_roles,
        test_16_mapping_correction,
        test_17_plan_duplicates,
        test_18_variable_template_previews,
        test_19_context_aware_chat_routing,
        test_20_date_shifted_account_matching,
        test_21_friendly_clarification_language,
    ]:
        try:
            t()
        except AssertionError:
            continue
        except Exception:
            _fail("unexpected exception")
            import traceback
            traceback.print_exc()

    print(f"\n{'═' * 70}\n  RESULTS: {_passed} passed, {_failed} failed\n{'═' * 70}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
