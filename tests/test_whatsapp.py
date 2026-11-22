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


@pytest.mark.parametrize("cmd", ["/logout", "logout", "Log out", "  /LOGOUT  ", "/log   out", "unlink", "/stop"])
def test_logout_commands_revoke_and_never_reach_model(env, cmd):
    env.tokens["919800000001"] = {"role": "vendor", "mcpAccessToken": "t"}
    post(cmd)
    assert env.revoked == ["919800000001"] and "Logged out" in said(env)[0] and not env.model.calls


@pytest.mark.parametrize("msg", ["how do I logout", "/logout please", "logout of what?", "stopping by tomorrow", "/logoutx"])
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
