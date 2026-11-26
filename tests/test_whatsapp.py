import hashlib
import hmac
import itertools
import json
import os
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

os.environ.update(WHATSAPP_APP_SECRET="appsecret", WHATSAPP_VERIFY_TOKEN="vt", WHATSAPP_PHONE_NUMBER_ID="pn1",
                  WHATSAPP_TOKEN="wat", WHATSAPP_LINK_SECRET="ls", OKGHUMO_MCP_URL="https://mcp.test/mcp")
import app.whatsapp as w  # noqa: E402
from app.main import app  # noqa: E402
from app.rbac import READ_ONLY_TOOLS  # noqa: E402

c = TestClient(app)
ids = itertools.count()


class FakeClient:
    def __init__(self):
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="answer")])


@pytest.fixture
def env(monkeypatch):
    e = NS(sent=[], model=FakeClient(), redeemed=[], revoked=[], tokens={})
    w._hits.clear(); w._hist.clear()
    monkeypatch.setattr(w, "_graph", lambda p: e.sent.append(p) if "text" in p else None)
    monkeypatch.setattr(w.assistant, "get_client", lambda: e.model)
    monkeypatch.setattr(w, "start", lambda phone: "https://okg.test/connect?state=abc")

    def tok(phone):
        if phone not in e.tokens: raise w.NotLinked
        return e.tokens[phone]
    monkeypatch.setattr(w, "token", tok)
    monkeypatch.setattr(w, "redeem", lambda phone, code: (e.redeemed.append((phone, code)), {"role": "vendor"})[1])
    monkeypatch.setattr(w, "revoke", lambda phone: e.revoked.append(phone))
    return e


def post(text, phone="919800000001", wamid=None, sign=True, pn="pn1", typ="text"):
    msg = {"id": wamid or f"wamid{next(ids)}", "from": phone, "type": typ}
    if typ == "text": msg["text"] = {"body": text}
    if typ == "audio": msg["audio"] = {"id": "media1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    raw = json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": pn}, "messages": [msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"appsecret", raw, hashlib.sha256).hexdigest()
    return c.post("/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": sig if sign else "sha256=bad"})


def said(e): return [p["text"]["body"] for p in e.sent]


def test_verify_handshake():
    q = {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "123"}
    assert c.get("/whatsapp/webhook", params=q).text == "123"
    assert c.get("/whatsapp/webhook", params={**q, "hub.verify_token": "no"}).status_code == 403


def test_forged_webhook_rejected_and_nothing_runs(env):
    assert post("hi", sign=False).status_code == 403 and not env.sent and not env.model.calls


def test_other_phone_number_id_ignored(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    assert post("hi", pn="someone-else").status_code == 200 and not env.model.calls


def test_unknown_phone_gets_login_link_and_no_model_call(env):
    post("hello")
    assert "https://okg.test/connect?state=abc" in said(env)[0] and not env.model.calls


def test_linked_vendor_gets_own_token_and_all_read_tools_only(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "tok-A"}
    post("sales?")
    call = env.model.calls[0]
    assert call["mcp_servers"][0]["authorization_token"] == "tok-A" and said(env) == ["answer"]
    ts = call["tools"][0]
    assert ts["default_config"] == {"enabled": False} and set(ts["configs"]) == READ_ONLY_TOOLS


def test_two_vendors_never_share_token_or_history(env):
    env.tokens.update({"919800000001": {"role": "vendor", "mcpAccessToken": "tok-A"},
                       "919800000002": {"role": "vendor", "mcpAccessToken": "tok-B"}})
    post("A secret question", phone="919800000001")
    post("B question", phone="919800000002")
    b = env.model.calls[1]
    assert b["mcp_servers"][0]["authorization_token"] == "tok-B"
    assert all("A secret" not in m["content"] for m in b["messages"])


def test_pdf_mention_sometimes_gets_thumbs_up_reaction(env, monkeypatch):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    graphed = []
    monkeypatch.setattr(w, "_graph", lambda p: graphed.append(p))

    monkeypatch.setattr(w.random, "random", lambda: 0.1)  # inside the 1/3 chance
    post("can I get a pdf report", wamid="m1")
    reactions = [p for p in graphed if p.get("type") == "reaction"]
    assert reactions == [{"to": "919800000001", "type": "reaction",
                          "reaction": {"message_id": "m1", "emoji": "👍"}}]

    graphed.clear()
    monkeypatch.setattr(w.random, "random", lambda: 0.9)  # outside the 1/3 chance
    post("another pdf please", wamid="m2")
    assert not [p for p in graphed if p.get("type") == "reaction"]

    graphed.clear()
    monkeypatch.setattr(w.random, "random", lambda: 0.1)
    post("what are my sales", wamid="m3")  # no "pdf" mention: never reacts, even in-chance
    assert not [p for p in graphed if p.get("type") == "reaction"]


def test_follow_up_keeps_same_vendor_context(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    post("first"); post("second")
    assert [m["content"] for m in env.model.calls[1]["messages"]] == ["first", "answer", "second"]


def test_admin_role_refused_no_model_call(env):
    env.tokens["919800000001"] = {"role": "admin", "mcpAccessToken": "t"}
    post("hi")
    assert not env.model.calls and "can't use" in said(env)[0]


def test_missing_mcp_token_fails_closed(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": None}
    post("hi")
    assert not env.model.calls and "isn't available" in said(env)[0]


def test_link_code_uses_webhook_phone_and_never_reaches_model(env):
    post("LINK-abc123", phone="919800000009")
    assert env.redeemed == [("919800000009", "LINK-abc123")] and not env.model.calls and "Connected" in said(env)[0]


def test_bad_code_message(env, monkeypatch):
    def bad(phone, code): raise w.BadCode
    monkeypatch.setattr(w, "redeem", bad)
    post("LINK-nope")
    assert "invalid or expired" in said(env)[0]


def test_stop_unlinks(env):
    post("STOP", phone="919800000003")
    assert env.revoked == ["919800000003"] and "Logged out" in said(env)[0]


@pytest.mark.parametrize("cmd", ["/logout", "logout", "Log out", "  /LOGOUT  ", "unlink", "/stop"])
def test_logout_commands_revoke_and_never_reach_model(env, cmd):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    post(cmd)
    assert env.revoked == ["919800000001"] and "Logged out" in said(env)[0] and not env.model.calls


@pytest.mark.parametrize("msg", ["how do I logout", "/logout please", "logout of what?", "stopping by tomorrow", "/logoutx", "//stop", "/log   out", "stop."])
def test_logout_only_matches_whole_message(env, msg):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    post(msg)
    assert not env.revoked and len(env.model.calls) == 1


def test_logout_then_next_message_needs_relinking(env, monkeypatch):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    monkeypatch.setattr(w, "revoke", lambda p: (env.revoked.append(p), env.tokens.pop(p)))
    post("/logout")
    post("sales?")
    assert "https://okg.test/connect?state=abc" in said(env)[-1] and not env.model.calls


def test_logout_clears_history_and_only_this_phones_state(env):
    a, b = "919800000001", "919800000002"
    env.tokens.update({a: {"role": "vendor", "mcpAccessToken": "t"}, b: {"role": "vendor", "mcpAccessToken": "t"}})
    for p in ("tasks", "proposals", "inbox"):
        dbrun(f"DELETE FROM {p}")
    post("secret A", phone=a); post("B q", phone=b)
    for v in ("wa:" + a, "wa:" + b):
        dbrun("INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
              (v, "n", "m", "0 8 * * *", "Asia/Kolkata", "2099-01-01T00:00:00+00:00", "x"))
        dbrun("INSERT INTO proposals(vendor_id,payload,created_at) VALUES(?,?,?)", (v, "{}", 0))
        dbrun("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES(?,?,?,?,?,?,0)",
              (v, 1, v, "t", "report body", "x"))
    post("/logout", phone=a)
    assert a not in w._hist and b in w._hist
    for tbl in ("tasks", "proposals", "inbox"):
        assert [r["vendor_id"] for r in dbrun(f"SELECT vendor_id FROM {tbl}")] == ["wa:" + b]


def test_logout_when_already_unlinked_still_cleans_up(env, monkeypatch):
    def nl(phone): raise w.NotLinked
    monkeypatch.setattr(w, "revoke", nl)
    dbrun("DELETE FROM tasks")
    dbrun("INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
          ("wa:919800000001", "n", "m", "0 8 * * *", "Asia/Kolkata", "2099-01-01T00:00:00+00:00", "x"))
    post("/logout")
    assert "already logged out" in said(env)[0] and not dbrun("SELECT * FROM tasks")


def test_logout_invalid_account_counts_as_logged_out(env, monkeypatch):
    def bad(phone): raise w.LinkInvalid
    monkeypatch.setattr(w, "revoke", bad)
    post("/logout")
    assert "Logged out" in said(env)[0]


def test_logout_okghumo_down_keeps_state_and_tells_vendor(env, monkeypatch):
    def down(phone): raise httpx.ConnectError("down")
    monkeypatch.setattr(w, "revoke", down)
    dbrun("DELETE FROM tasks")
    dbrun("INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
          ("wa:919800000001", "n", "m", "0 8 * * *", "Asia/Kolkata", "2099-01-01T00:00:00+00:00", "x"))
    w._hist["919800000001"] = (9e12, [{"role": "user", "content": "x"}])
    post("/logout")
    assert "couldn't log you out" in said(env)[0] and "Logged out" not in said(env)[0]
    assert dbrun("SELECT * FROM tasks") and "919800000001" in w._hist


def test_duplicate_delivery_processed_once(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    post("x", wamid="dup-1"); post("x", wamid="dup-1")
    assert len(env.model.calls) == 1


def test_non_text_message_politely_refused(env):
    post("", typ="image")
    assert "text messages" in said(env)[0] and not env.model.calls


def test_rate_limit(env):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    for _ in range(22): post("q")
    assert len(env.model.calls) == 20


def test_link_client_maps_okghumo_errors_and_sends_secret(monkeypatch):
    import httpx
    seen = {}

    def fake(status, msg):
        def post(url, json, headers, timeout):
            seen.update(url=url, headers=headers, json=json)
            return httpx.Response(status, json={"success": status < 400, "message": msg}, request=httpx.Request("POST", url))
        return post

    for msg, exc in (("This phone is not linked", w.NotLinked), ("This vendor link is no longer valid", w.LinkInvalid),
                     ("Invalid or expired code", w.BadCode)):
        monkeypatch.setattr(httpx, "post", fake(401, msg))
        with pytest.raises(exc):
            w.token("919800000001")
    assert seen["headers"] == {"X-Whatsapp-Link-Secret": "ls"} and seen["url"].endswith("/api/users/whatsapp-link/token")
    monkeypatch.setattr(httpx, "post", fake(401, "Unauthorized"))  # wrong shared secret = generic failure
    with pytest.raises(httpx.HTTPStatusError):
        w.token("919800000001")


# ---- scheduling over WhatsApp ----
import httpx  # noqa: E402

from app import runner, scheduler, schedules  # noqa: E402
from app.db import run as dbrun  # noqa: E402

P = "919800000001"
GOODP = {"kind": "report", "name": "Daily sales", "text": "Sales for yesterday", "frequency": "daily", "time": "08:00"}


class Script(FakeClient):
    def __init__(self, *script):
        super().__init__()
        self.script = list(script)

    def _create(self, **kw):
        self.calls.append(kw)
        blocks = self.script.pop(0) if self.script else [NS(type="text", text="ok")]
        return NS(stop_reason="tool_use" if blocks[0].type == "tool_use" else "end_turn", content=blocks)


@pytest.fixture
def sch(env):
    for t in ("tasks", "inbox", "proposals"):
        dbrun(f"DELETE FROM {t}")
    env.tokens[P] = {"role": "vendor", "mcpAccessToken": "tok"}
    return env


def propose_via_wa(e, phone=P):
    e.model.script = [[NS(type="tool_use", id="t", name="propose_schedule", input=GOODP)], [NS(type="text", text="Sure:")]]
    post("send me a daily report at 8", phone=phone)


def test_wa_propose_then_yes_creates_task_for_this_phone_only(sch):
    sch.model = Script([NS(type="tool_use", id="t", name="propose_schedule", input=GOODP)], [NS(type="text", text="Sure:")])
    post("daily report at 8 please")
    assert not dbrun("SELECT * FROM tasks")  # model alone never creates
    assert schedules.MARK in said(sch)[-1]
    n = len(sch.model.calls)
    post("yes")
    assert len(sch.model.calls) == n  # YES handled in code, no model call
    t = dbrun("SELECT * FROM tasks")
    assert len(t) == 1 and t[0]["vendor_id"] == "wa:" + P and t[0]["role"] == "vendor" and "WhatsApp" in said(sch)[-1]


def test_wa_other_phone_cannot_confirm_my_proposal(sch):
    sch.model = Script([NS(type="tool_use", id="t", name="propose_schedule", input=GOODP)], [NS(type="text", text="Sure:")])
    sch.tokens["919800000002"] = {"role": "vendor", "mcpAccessToken": "t2"}
    post("daily report at 8")
    post("yes", phone="919800000002")
    assert not dbrun("SELECT * FROM tasks")


def test_wa_stop_deletes_schedules(sch):
    dbrun("INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES(?,?,?,?,?,?,?)",
          ("wa:" + P, "n", "m", "0 8 * * *", "Asia/Kolkata", "2099-01-01T00:00:00+00:00", "x"))
    post("stop")
    assert not dbrun("SELECT * FROM tasks")


def _due_task(kind="report", vendor="wa:" + P):
    return dbrun("INSERT INTO tasks(vendor_id,name,message,cron,tz,kind,role,next_run_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)"
                 " RETURNING id", (vendor, "Daily sales", "Sales for yesterday", "0 8 * * *", "Asia/Kolkata", kind, "vendor",
                                   "2000-01-01T00:00:00+00:00", "x"))[0]["id"]


def test_wa_report_runs_with_phone_token_and_is_sent(sch, monkeypatch):
    _due_task()
    scheduler.tick()
    assert any("Daily sales" in b and "answer" in b for b in said(sch))
    assert sch.model.calls[0]["mcp_servers"][0]["authorization_token"] == "tok"
    assert dbrun("SELECT sent FROM inbox")[0]["sent"] == 1


def test_wa_reminder_is_sent(sch):
    _due_task("reminder")
    scheduler.tick()
    assert any("Sales for yesterday" in b for b in said(sch)) and not sch.model.calls


def test_wa_unlinked_number_pauses_silently(sch):
    sch.tokens.clear()
    tid = _due_task()
    scheduler.tick()
    assert not sch.sent and not sch.model.calls
    assert dbrun("SELECT status FROM tasks WHERE id=?", (tid,))[0]["status"] == "paused"


def test_wa_window_closed_sends_template_then_flushes_on_next_message(sch, monkeypatch):
    monkeypatch.setenv("WHATSAPP_REPORT_TEMPLATE", "report_ready")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_PARAM", "report_type")
    sent = []

    def graph(p):
        if p.get("type") == "text" and not sch.tokens.get("open"):
            raise httpx.HTTPStatusError("window", request=None, response=None)
        sent.append(p)
    monkeypatch.setattr(w, "_graph", graph)
    _due_task()
    scheduler.tick()
    assert [p.get("type") for p in sent] == ["template"] and sent[0]["template"]["components"][0]["parameters"][0]["parameter_name"] == "report_type" and dbrun("SELECT sent FROM inbox")[0]["sent"] == 0
    sch.tokens["open"] = True  # vendor replied: window open
    post("report")
    assert any(p.get("type") == "text" and "Daily sales" in p["text"]["body"] for p in sent)
    assert dbrun("SELECT sent FROM inbox")[0]["sent"] == 1 and not sch.model.calls[1:]  # no extra model call for a ping


def test_web_vendor_rows_are_not_pushed_to_whatsapp(sch):
    _due_task("reminder", vendor="web-vendor")
    scheduler.tick()
    assert not sch.sent


def test_burst_limit_per_minute_no_model_call(env, monkeypatch):
    monkeypatch.setenv("RATE_WA_PER_MIN", "3")
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    for _ in range(6): post("what are my sales")
    assert len(env.model.calls) == 3


def test_quick_messages_are_answered_as_one_question(env):
    from app import whatsapp as w
    w.enqueue("919800000001", "kya hal chal kesa chal raha he dhandha", "w1")
    w.enqueue("919800000001", "?", "w2")
    seen = []
    orig, w.handle = w.handle, lambda p, t, i, v=False: seen.append((t, i))
    try:
        w.process("919800000001"); w.process("919800000001")  # second task finds nothing left
    finally:
        w.handle = orig
    assert seen == [("kya hal chal kesa chal raha he dhandha\n?", "w2")]


# ---- voice notes ----
@pytest.fixture
def vox(env, monkeypatch):
    """Voice on, linked vendor, STT faked (no network). vox.say = what the 'speech-to-text' returns."""
    from app import voice
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    monkeypatch.setenv("STT_API_KEY", "k")
    env.say, env.stt_calls = "how many bookings today", []
    def fake(media_id):
        env.stt_calls.append(media_id)
        if isinstance(env.say, Exception): raise env.say
        return env.say
    monkeypatch.setattr(voice, "get_text", fake)
    return env


def test_voice_note_is_echoed_then_answered_like_text(vox):
    post("", typ="audio")
    assert said(vox) == ["🎙 I heard: how many bookings today", "answer"]
    assert vox.model.calls[0]["messages"][-1]["content"] == "how many bookings today"


def test_voice_stop_and_link_code_are_not_commands(vox):
    for spoken in ("stop", "logout", "LINK-abc123"):
        vox.say = spoken
        post("", typ="audio")
    assert vox.revoked == [] and vox.redeemed == []


def test_voice_yes_never_confirms_a_proposal(vox):
    from app import schedules
    w._remember("919800000001", [{"role": "user", "content": "remind me daily"},
                                 {"role": "assistant", "content": "Every day 9am. " + schedules.MARK}])
    vox.say = "yes"
    post("", typ="audio")
    assert "type YES or NO" in said(vox)[-1] and not vox.model.calls
    post("yes")  # the typed answer still works afterwards (history was left alone)
    assert "expired" in said(vox)[-1] or "Done" in said(vox)[-1]


@pytest.mark.parametrize("err,frag", [("too_long", "too long"), ("empty", "couldn't hear"), ("failed", "couldn't process")])
def test_voice_failures_get_a_friendly_reply_and_no_model_call(vox, err, frag):
    from app import voice
    vox.say = voice.VoiceError(err)
    post("", typ="audio")
    assert frag in said(vox)[-1] and not vox.model.calls


def test_voice_unexpected_error_is_generic(vox):
    vox.say = RuntimeError("boom secret")
    post("", typ="audio")
    assert "couldn't process" in said(vox)[-1] and "boom" not in said(vox)[-1] and not vox.model.calls


def test_voice_off_without_key_or_with_kill_switch(vox, monkeypatch):
    monkeypatch.delenv("STT_API_KEY")
    post("", typ="audio")
    assert said(vox) == ["I can only read text messages for now."] and not vox.stt_calls
    monkeypatch.setenv("STT_API_KEY", "k"); monkeypatch.setenv("VOICE_ENABLED", "0")
    post("", typ="audio")
    assert len(vox.stt_calls) == 0


def test_voice_daily_limit_is_per_phone_and_stops_before_download(vox, monkeypatch):
    monkeypatch.setenv("RATE_WA_VOICE_PER_DAY", "2")
    vox.tokens["919800000002"] = {"role": "vendor", "mcpAccessToken": "t2"}
    for _ in range(3): post("", typ="audio")
    assert len(vox.stt_calls) == 2 and "limit of 2 voice notes" in said(vox)[-1]
    n = len(vox.stt_calls)
    post("", typ="audio", phone="919800000002")  # another vendor still has their own 15
    assert len(vox.stt_calls) == n + 1


def test_voice_default_daily_limit_is_15(vox):
    for _ in range(16): post("", typ="audio")
    assert len(vox.stt_calls) == 15 and "limit of 15 voice notes" in said(vox)[-1]


def test_long_voice_transcript_passes_guard_but_typed_long_text_does_not(vox):
    vox.say = "bookings " * 250  # ~2250 chars
    post("", typ="audio")
    assert vox.model.calls
    vox.model.calls.clear()
    post("bookings " * 250)
    assert not vox.model.calls


def test_ogg_seconds_and_too_long_is_rejected_before_stt(monkeypatch):
    from app import voice
    ogg = lambda sec: b"OggS\x00\x04" + int(sec * 48000).to_bytes(8, "little") + b"\x00" * 20
    assert voice.ogg_seconds(ogg(10)) == 10.0 and voice.ogg_seconds(b"junk") is None
    monkeypatch.setattr(voice, "fetch", lambda m: ogg(200))
    monkeypatch.setattr(voice, "transcribe", lambda a: pytest.fail("must not be billed"))
    with pytest.raises(voice.VoiceError) as e: voice.get_text("m")
    assert e.value.kind == "too_long"
    monkeypatch.setattr(voice, "fetch", lambda m: ogg(30)); monkeypatch.setattr(voice, "transcribe", lambda a: "")
    with pytest.raises(voice.VoiceError) as e: voice.get_text("m")
    assert e.value.kind == "empty"


def test_silence_from_whisper_counts_as_empty(monkeypatch):
    from app import voice
    monkeypatch.setenv("STT_API_KEY", "k"); monkeypatch.setenv("STT_MODEL", "whisper-1")
    class R:
        def raise_for_status(self): pass
        def json(self): return {"text": "you", "segments": [{"no_speech_prob": 0.9}]}
    monkeypatch.setattr(voice.httpx, "post", lambda *a, **k: R())
    assert voice.transcribe(b"x") == ""
    R.json = lambda self: {"text": " hello ", "segments": [{"no_speech_prob": 0.1}]}
    assert voice.transcribe(b"x") == "hello"


def test_voice_note_skips_the_typing_wait_but_text_still_waits(env, monkeypatch):
    waits = []
    monkeypatch.setattr(w.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setenv("WA_DEBOUNCE_S", "5")
    monkeypatch.setattr(w, "handle", lambda *a, **k: None)
    w._pending.clear(); w._voice.clear()
    w.enqueue("919800000001", "hello", "t1"); w.process("919800000001")
    w._voice["v1"] = "m"; w.enqueue("919800000001", None, "v1")
    monkeypatch.setattr(w, "_transcribe", lambda p, m: "text")
    w.process("919800000001")
    assert waits == [5.0, 0]


# ---- PDF reports over WhatsApp (on-demand and scheduled) ----
from datetime import datetime, timedelta  # noqa: E402

SPECJ = ('{"title":"Weekly sales","summary":"Sales were Rs. 12,500.","kpis":[{"label":"Revenue","value":"Rs. 12,500"}],'
         '"charts":[{"type":"bar","title":"By source","labels":["online","agent"],"values":[5000,7500]}]}')


def T(t): return NS(type="text", text=t)


@pytest.fixture
def docs(sch, monkeypatch):
    sch.docs = []
    monkeypatch.setattr(w, "send_pdf", lambda *a: sch.docs.append(a))
    return sch


def test_wa_on_demand_pdf_asks_politely_then_sends_document_and_stores_nothing(docs):
    docs.model = Script([NS(type="tool_use", id="t", name="create_pdf_report", input={"question": "Sales last 30 days"})],
                        [T(SPECJ)], [T("Your report is ready, thank you for waiting.")])
    post("detailed report of last month sales")
    assert said(docs) == ["Your report is ready, thank you for waiting."]
    (to, pdf, title, cap), = docs.docs
    assert to == P and pdf.startswith(b"%PDF") and title == "Weekly sales"
    outer, nested = docs.model.calls[0], docs.model.calls[1]
    assert "DETAILED REPORTS" in outer["system"] and "WhatsApp" in outer["system"] and "politely" in outer["system"]
    assert len(nested["tools"]) == 1 and nested["mcp_servers"][0]["authorization_token"] == "tok"  # read-only, own token
    assert not dbrun("SELECT 1 FROM inbox WHERE vendor_id=?", ("wa:" + P,))


def test_wa_pdf_attach_failure_apologises(docs, monkeypatch):
    def boom(*a): raise httpx.ConnectError("x")
    monkeypatch.setattr(w, "send_pdf", boom)
    docs.model = Script([NS(type="tool_use", id="t", name="create_pdf_report", input={"question": "q"})], [T(SPECJ)], [T("Ready!")])
    post("detailed report of last month sales")
    assert said(docs)[0] == "Ready!" and "couldn't attach the PDF" in said(docs)[-1]


def test_wa_scheduled_pdf_report_is_delivered_as_document_then_held_if_window_closed(docs, monkeypatch):
    from app.tasks import add_task
    t = add_task("wa:" + P, "Weekly", "Sales last 7 days", "0 8 * * *", "Asia/Kolkata", "report", "vendor", "pdf")
    due = datetime.fromisoformat(t["next_run_at"]) + timedelta(minutes=1)
    monkeypatch.setattr(runner.assistant, "get_client", lambda: Script([T(SPECJ)]))
    scheduler.tick(due)
    (to, pdf, title, cap), = docs.docs
    assert to == P and pdf.startswith(b"%PDF") and cap.startswith("*Weekly*")
    assert dbrun("SELECT sent FROM inbox WHERE vendor_id=?", ("wa:" + P,))[0]["sent"] == 1
    # window closed: Meta refuses, the row stays unsent and is delivered when the vendor next writes
    docs.docs.clear()
    n = {"i": 0}
    def closed(*a):
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://x"), response=httpx.Response(400))
        docs.docs.append(a)
    monkeypatch.setattr(w, "send_pdf", closed)
    dbrun("UPDATE tasks SET next_run_at=? WHERE id=?", (t["next_run_at"], t["id"]))
    dbrun("DELETE FROM inbox")
    monkeypatch.setattr(runner.assistant, "get_client", lambda: Script([T(SPECJ)]))
    scheduler.tick(due)
    assert dbrun("SELECT sent FROM inbox")[0]["sent"] == 0 and not docs.docs
    post("hello")  # vendor writes: window open again
    assert len(docs.docs) == 1 and dbrun("SELECT sent FROM inbox")[0]["sent"] == 1


def test_reaction_outcomes_are_logged_and_pdf_sent_also_reacts(docs, monkeypatch, caplog):
    graphed = []
    monkeypatch.setattr(w, "_graph", lambda p: graphed.append(p))
    caplog.set_level("INFO")
    logged = lambda: caplog.text.replace('"', '')  # the log formatter quotes some values, not others  # noqa: E731
    # a PDF is actually sent although the message never says "pdf": still eligible for the 👍
    monkeypatch.setattr(w.random, "random", lambda: 0.1)
    docs.model = Script([NS(type="tool_use", id="t", name="create_pdf_report", input={"question": "q"})], [T(SPECJ)], [T("Ready!")])
    post("and another of the months sales please", wamid="m9")
    assert [p["reaction"]["message_id"] for p in graphed if p.get("type") == "reaction"] == ["m9"] and docs.docs
    assert 'outcome=sent' in logged() and 'why=pdf_sent' in logged()
    # the dice say no: nothing sent, but the skip is logged
    graphed.clear(); caplog.clear()
    monkeypatch.setattr(w.random, "random", lambda: 0.9)
    post("a pdf please", wamid="m10")
    assert not [p for p in graphed if p.get("type") == "reaction"] and 'outcome=skipped_dice' in logged()
    # Meta refuses the reaction: the reply still goes out, and the failure is logged (type only)
    caplog.clear()
    monkeypatch.setattr(w.random, "random", lambda: 0.1)
    def boom(p):
        if p.get("type") == "reaction": raise httpx.ConnectError("x")
    monkeypatch.setattr(w, "_graph", boom)
    post("one more pdf", wamid="m11")
    assert 'outcome=failed' in logged() and "ConnectError" in logged()
