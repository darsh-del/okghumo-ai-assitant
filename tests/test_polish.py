"""Logging safety, rate limiting, reply sanitizing, language, accuracy signal."""
import logging

import jwt
from fastapi.testclient import TestClient

import app.main as m
from app import assistant, lang, logs, ratelimit, sanitize
from tests.test_chat import FakeClient, app, get_client

OID = "507f1f77bcf86cd799439011"


# ---- logging ----
def test_log_injection_is_neutralised(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.INFO):
        log.info("hello\r\nFAKE LINE level=ERROR")
        logs.event(log, "x", note="a\nb")
    assert all("\n" not in r.getMessage() and "\r" not in r.getMessage() for r in caplog.records)
    assert "\\r\\n" in caplog.records[0].getMessage()


def test_masking():
    assert logs.mask_phone("+91 98765 43210") == "***3210"
    assert logs.mask_id("wa:919876543210") == "wa:***3210"
    assert logs.mask_id("vendor-123456") == "ve***56" and logs.mask_id("ab") == "ab"


def test_chat_logs_have_no_text_or_tokens(caplog):
    f = FakeClient()
    app.dependency_overrides[get_client] = lambda: f
    m.mcp_token = lambda vid: "SECRET-MCP-TOKEN"
    h = {"Authorization": "Bearer " + jwt.encode({"role": "viewer", "sub": "vendor-123456"}, "s", algorithm="HS256")}
    with caplog.at_level(logging.INFO):
        TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "my secret question about sales"}]}, headers=h)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "event=chat_reply" in text and "model_called=True" in text
    assert "SECRET-MCP-TOKEN" not in text and "secret question" not in text and "vendor-123456" not in text


# ---- rate limit ----
def test_sliding_window():
    assert [ratelimit.hit("k", 2, 60) for _ in range(3)][:2] == [0, 0] and ratelimit.hit("k", 2, 60) > 0


def test_chat_429_with_retry_after_and_no_model_call(monkeypatch):
    monkeypatch.setenv("RATE_CHAT_PER_MIN", "2")
    f = FakeClient()
    app.dependency_overrides[get_client] = lambda: f
    m.mcp_token = lambda vid: "t"
    h = {"Authorization": "Bearer " + jwt.encode({"role": "viewer", "sub": "rl"}, "s", algorithm="HS256")}
    c = TestClient(app)
    codes = [c.post("/chat", json={"messages": [{"role": "user", "content": "sales today?"}]}, headers=h) for _ in range(3)]
    assert [r.status_code for r in codes] == [200, 200, 429]
    assert int(codes[2].headers["Retry-After"]) >= 1 and len(f.calls) == 2
    other = {"Authorization": "Bearer " + jwt.encode({"role": "viewer", "sub": "someone-else"}, "s", algorithm="HS256")}
    assert c.post("/chat", json={"messages": [{"role": "user", "content": "sales today?"}]}, headers=other).status_code == 200


# ---- sanitizer ----
def test_object_ids_and_dashes_removed_numbers_untouched():
    t = f"Agent Ravi ({OID}) owes ₹12,345.50 — bookings 120–150, kal {OID.upper()}ka data"
    out = sanitize.clean_reply(t)
    assert OID not in out and OID.upper() not in out and "—" not in out and "–" not in out
    assert "₹12,345.50" in out and "120-150" in out and "()" not in out


def test_devanagari_glued_id_and_split_ceiling():
    assert OID not in sanitize.clean_reply(f"आईडी{OID}है")
    assert OID + "ab" in sanitize.clean_reply(OID + "ab")  # documented ceiling: longer hex run is left alone


def test_emoji_cap():
    hist = [{"role": "assistant", "content": "ok 😊"}]
    assert "😊" not in sanitize.clean_reply("Sure thing 😊", hist)  # previous had one
    assert sanitize.clean_reply("Sure thing 😊😊 and 👍", []).count("😊") == 1 and "👍" not in sanitize.clean_reply("Sure 😊 and 👍", [])
    assert "😊" not in sanitize.clean_reply("Sales were 1,200 and 300 and 45 😊", [])  # data reply
    assert "😊" not in sanitize.clean_reply("Which day? 😊", [])  # question


# ---- language ----
def test_language_detection_and_roman_replies():
    assert lang.detect("aaj ki sales kitni hai") == "hi" and lang.detect("aaje sales ketli chhe") == "gu"
    assert lang.detect("what are my sales") == "en" and lang.detect("आज की सेल") == "hi"
    for kind, by_lang in lang.REPLIES.items():
        for txt in by_lang.values():
            assert not any("ऀ" <= ch <= "૿" for ch in txt), kind  # Roman letters only
            assert sum(ch in "😊👋" for ch in txt) <= 1


def test_guard_replies_in_vendor_language():
    from app import guard
    assert guard.check([{"role": "user", "content": "kem cho"}]) == lang.REPLIES["hello"]["gu"]
    assert guard.check([{"role": "user", "content": "mujhe ek kavita likh do poem"}]) == lang.REPLIES["off_topic"]["hi"]


# ---- accuracy signal ----
def test_ungrounded_numbers_counts_only_missing_amounts():
    tool = ['{"total": 12500, "count": 48}']
    assert assistant.ungrounded("Sales were 12,500 across 48 bookings in 2026", tool) == 0
    assert assistant.ungrounded("Sales were 13,500", tool) == 1


def test_prompt_has_accuracy_language_style_rules():
    for w in ("ACCURACY", "LANGUAGE", "Roman", "STYLE", "at most one emoji"):
        assert w in assistant.SYSTEM
