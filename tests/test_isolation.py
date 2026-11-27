"""Vendor isolation for everything scheduled: reminders, reports, proposals, inbox, WhatsApp delivery.
One vendor's schedules, tokens, reports and chat context must never reach, or be mixed with, another's."""
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.update(JWT_SECRET="s", OKGHUMO_MCP_URL="https://mcp.test/mcp")
import app.runner as runner  # noqa: E402
import app.whatsapp as w  # noqa: E402
from app import scheduler, schedules, tasks  # noqa: E402
from app.credentials import NoCredential  # noqa: E402
from app.db import run  # noqa: E402
from app.main import app  # noqa: E402
from test_whatsapp import env, post, said  # noqa: E402,F401  (fixtures/helpers for the webhook path)

c = TestClient(app)
PA, PB = "919800000001", "919800000002"


@pytest.fixture(autouse=True)
def clean():
    for t in ("tasks", "inbox", "proposals", "wa_account"):
        run(f"DELETE FROM {t}")
    w._hist.clear()
    yield


def jwt_for(sub): return jwt.encode({"sub": sub, "role": "vendor"}, "s", algorithm="HS256")
def H(sub): return {"Authorization": "Bearer " + jwt_for(sub)}
def acct(sub): return jwt.encode({"sub": sub, "aud": "okghumo-mcp"}, "k", algorithm="HS256")  # MCP-token shaped
def add(v, name="Daily sales", text="Sales for yesterday", cron="0 8 * * *", kind="report", role="vendor"):
    return tasks.add_task(v, name, text, cron, "Asia/Kolkata", kind, role)
def due_of(*ts): return max(datetime.fromisoformat(t["next_run_at"]) for t in ts) + timedelta(minutes=1)
def inbox_of(v): return run("SELECT * FROM inbox WHERE vendor_id=? ORDER BY id", (v,))


class EchoModel:
    """Thread-safe fake: the report body names the token it was asked with, so any mix-up is visible."""
    def __init__(self): self.calls = []; self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="report-for " + kw["mcp_servers"][0]["authorization_token"])])


def patch_runner(monkeypatch, model=None, token=lambda v: "mcp-" + v):
    model = model or EchoModel()
    monkeypatch.setattr(runner.assistant, "get_client", lambda: model)
    monkeypatch.setattr(runner.mcp_auth, "mcp_token", token)
    return model


# ---------- reports: many vendors, identical schedules ----------
def test_identical_reports_for_many_vendors_never_mix(monkeypatch):
    vs = [f"V{i}" for i in range(12)]
    ts = [add(v) for v in vs]  # same name, same text, same cron, same minute
    patch_runner(monkeypatch)
    scheduler.tick(due_of(*ts))
    for v in vs:
        rows = inbox_of(v)
        assert len(rows) == 1 and rows[0]["body"] == f"report-for mcp-{v}", v


def test_concurrent_runs_in_the_thread_pool_never_mix(monkeypatch):
    vs = [f"C{i}" for i in range(20)]
    ts = [add(v) for v in vs]
    patch_runner(monkeypatch)
    with ThreadPoolExecutor(max_workers=8) as pool:  # same as scheduler.main()
        scheduler.tick(due_of(*ts), submit=pool.submit)
    for v in vs:
        assert [r["body"] for r in inbox_of(v)] == [f"report-for mcp-{v}"], v


def test_concurrent_runs_with_timing_jitter_never_mix(monkeypatch):
    """Stress version of test_concurrent_runs_in_the_thread_pool_never_mix: more vendors, more workers, and a
    random sleep in both the token mint and the model call, so threads genuinely interleave (finish out of
    submission order) instead of racing through in lockstep."""
    vs = [f"J{i}" for i in range(50)]
    ts = [add(v) for v in vs]

    def jittery_token(v):
        time.sleep(random.uniform(0, 0.02))
        return "mcp-" + v

    class JitteryModel(EchoModel):
        def _create(self, **kw):
            time.sleep(random.uniform(0, 0.02))
            return super()._create(**kw)

    patch_runner(monkeypatch, model=JitteryModel(), token=jittery_token)
    with ThreadPoolExecutor(max_workers=16) as pool:
        scheduler.tick(due_of(*ts), submit=pool.submit)
    for v in vs:
        assert [r["body"] for r in inbox_of(v)] == [f"report-for mcp-{v}"], v


def test_one_vendors_failure_does_not_touch_another(monkeypatch):
    a, b = add("FA"), add("FB")
    def tok(v):
        if v == "FA": raise NoCredential(v)
        return "mcp-" + v
    m = patch_runner(monkeypatch, token=tok)
    scheduler.tick(due_of(a, b))
    assert inbox_of("FB")[0]["body"] == "report-for mcp-FB" and "reconnect" in inbox_of("FA")[0]["body"].lower()
    assert run("SELECT fails FROM tasks WHERE id=?", (a["id"],))[0]["fails"] == 1
    assert run("SELECT fails FROM tasks WHERE id=?", (b["id"],))[0]["fails"] == 0 and len(m.calls) == 1


def test_a_paused_task_of_one_vendor_never_blocks_another(monkeypatch):
    a, b = add("PA"), add("PB")
    run("UPDATE tasks SET status='paused' WHERE id=?", (a["id"],))
    patch_runner(monkeypatch)
    scheduler.tick(due_of(a, b))
    assert inbox_of("PA") == [] and len(inbox_of("PB")) == 1


def test_reminder_text_is_never_run_through_the_model_or_shared(monkeypatch):
    a, b = add("RA", kind="reminder", text="Call Raj"), add("RB", kind="reminder", text="Check dues")
    m = patch_runner(monkeypatch)
    scheduler.tick(due_of(a, b))
    assert inbox_of("RA")[0]["body"] == "Call Raj" and inbox_of("RB")[0]["body"] == "Check dues" and not m.calls


# ---------- limits and duplicates are per vendor ----------
def test_limits_and_duplicate_checks_are_per_vendor():
    for i in range(tasks.MAX_TASKS): add("L1", name=f"n{i}", text=f"t{i}", kind="reminder")
    with pytest.raises(Exception): add("L1", name="x", text="x", kind="reminder")  # L1 is full
    add("L2", name="x", text="t0", kind="reminder")  # L2 unaffected, same text as L1's
    with pytest.raises(Exception): add("L2", name="x", text="t0", kind="reminder")  # duplicate only against own
    for i in range(tasks.MAX_REPORTS): add("L3", text=f"q{i}")
    add("L4", text="q0")


# ---------- REST API ----------
def test_api_cross_vendor_everything_is_404_or_empty():
    tid = c.post("/tasks", json={"name": "n", "message": "m", "cron": "0 8 * * *"}, headers=H("A")).json()["id"]
    run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at) VALUES('A',?,?,?,?,?)", (tid, "x", "t", "secret", "x"))
    iid = run("SELECT id FROM inbox")[0]["id"]
    assert c.get("/tasks", headers=H("B")).json() == [] and c.get("/inbox", headers=H("B")).json() == []
    assert c.patch(f"/tasks/{tid}", json={"status": "paused"}, headers=H("B")).status_code == 404
    assert c.delete(f"/tasks/{tid}", headers=H("B")).status_code == 404
    c.post(f"/inbox/{iid}/read", headers=H("B"))
    assert run("SELECT read FROM inbox WHERE id=?", (iid,))[0]["read"] == 0
    assert run("SELECT status FROM tasks WHERE id=?", (tid,))[0]["status"] == "active"


def test_app_vendor_can_never_enter_the_whatsapp_namespace():
    add("wa:" + PA, kind="reminder", text="private")
    assert c.get("/tasks", headers=H("wa:" + PA)).status_code == 401
    assert c.post("/tasks", json={"name": "n", "message": "m", "cron": "0 8 * * *"}, headers=H("wa:" + PA)).status_code == 401
    assert c.get("/inbox", headers=H("wa:" + PA)).status_code == 401
    assert c.get("/tasks", headers=H(PA)).json() == []  # the same digits as an app vendor id are a different vendor


# ---------- chat proposals ----------
def test_proposal_of_one_vendor_cannot_be_confirmed_or_replaced_by_another():
    payload = ('{"action":"create","kind":"reminder","name":"n","text":"t","cron":"0 8 * * *","tz":"Asia/Kolkata","summary":"s"}')
    run("INSERT INTO proposals VALUES('PA', ?, ?)", (payload, time.time()))
    hist = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "ok " + schedules.MARK}, {"role": "user", "content": "yes"}]
    assert "expired" in schedules.intercept("PB", "vendor", hist)  # B has nothing pending
    assert run("SELECT 1 FROM proposals WHERE vendor_id='PA'") and not run("SELECT 1 FROM tasks")
    assert "Done" in schedules.intercept("PA", "vendor", hist)
    assert [r["vendor_id"] for r in run("SELECT vendor_id FROM tasks")] == ["PA"]


# ---------- WhatsApp: delivery goes only to the owner's phone ----------
def sent_to(env, phone): return [p["text"]["body"] for p in env.sent if p["to"] == phone]


def test_reports_and_reminders_are_delivered_only_to_their_own_phone(env, monkeypatch):
    env.tokens.update({PA: {"role": "vendor", "mcpAccessToken": "tA"}, PB: {"role": "vendor", "mcpAccessToken": "tB"}})
    ta, tb = add("wa:" + PA), add("wa:" + PB)
    ra = add("wa:" + PA, name="Dues", text="Call Raj", cron="0 9 * * *", kind="reminder")
    monkeypatch.setattr(w, "token", lambda ph: env.tokens[ph])
    patch_runner(monkeypatch)
    scheduler.tick(due_of(ta, tb, ra))
    assert sent_to(env, PA) == ["*Daily sales*\nreport-for tA", "*Dues*\nCall Raj"] or set(sent_to(env, PA)) == {"*Daily sales*\nreport-for tA", "*Dues*\nCall Raj"}
    assert sent_to(env, PB) == ["*Daily sales*\nreport-for tB"]


def test_report_for_a_phone_uses_that_phones_token_not_the_vendors_neighbours(env, monkeypatch):
    env.tokens.update({PA: {"role": "vendor", "mcpAccessToken": "tA"}, PB: {"role": "vendor", "mcpAccessToken": "tB"}})
    ts = [add("wa:" + PA), add("wa:" + PB)]
    monkeypatch.setattr(w, "token", lambda ph: env.tokens[ph])
    m = patch_runner(monkeypatch)
    scheduler.tick(due_of(*ts))
    assert sorted(x["mcp_servers"][0]["authorization_token"] for x in m.calls) == ["tA", "tB"]


def test_flush_only_sends_the_phones_own_undelivered_items(env):
    for ph, body in ((PA, "A-private"), (PB, "B-private")):
        run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES(?,?,?,?,?,?,0)",
            ("wa:" + ph, 1 if ph == PA else 2, "x", "T", body, "now"))
    assert w.flush(PB) == 1
    assert sent_to(env, PB) == ["*T*\nB-private"] and sent_to(env, PA) == []
    assert run("SELECT sent FROM inbox WHERE vendor_id=?", ("wa:" + PA,))[0]["sent"] == 0  # A's still waiting for A


def test_logout_of_one_phone_leaves_the_other_completely_alone(env):
    env.tokens.update({PA: {"role": "vendor", "mcpAccessToken": "tA"}, PB: {"role": "vendor", "mcpAccessToken": "tB"}})
    for ph in (PA, PB):
        add("wa:" + ph)
        run("INSERT INTO proposals VALUES(?,?,?)", ("wa:" + ph, "{}", time.time()))
        run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES(?,?,?,?,?,?,0)", ("wa:" + ph, int(ph[-1]) + 20, "x", "T", "b", "n"))
        w._remember(ph, [{"role": "user", "content": "hi " + ph}])
    post("STOP", phone=PA)
    for tbl in ("tasks", "proposals", "inbox"):
        assert [r["vendor_id"] for r in run(f"SELECT vendor_id FROM {tbl}")] == ["wa:" + PB], tbl
    assert PB in w._hist and PA not in w._hist


def test_yes_from_phone_b_does_not_confirm_phone_as_proposal(env):
    env.tokens.update({PA: {"role": "vendor", "mcpAccessToken": "tA"}, PB: {"role": "vendor", "mcpAccessToken": "tB"}})
    payload = '{"action":"create","kind":"reminder","name":"n","text":"t","cron":"0 8 * * *","tz":"Asia/Kolkata","summary":"s"}'
    run("INSERT INTO proposals VALUES(?, ?, ?)", ("wa:" + PA, payload, time.time()))
    w._remember(PA, [{"role": "user", "content": "remind me"}, {"role": "assistant", "content": "ok " + schedules.MARK}])
    w._remember(PB, [{"role": "user", "content": "remind me"}, {"role": "assistant", "content": "ok " + schedules.MARK}])
    post("yes", phone=PB)
    assert not run("SELECT 1 FROM tasks") and run("SELECT 1 FROM proposals WHERE vendor_id=?", ("wa:" + PA,))
    post("yes", phone=PA)
    assert [r["vendor_id"] for r in run("SELECT vendor_id FROM tasks")] == ["wa:" + PA]


# ---------- WhatsApp: a number that changes owner ----------
def seed_phone_a(env, account="acctX"):
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": acct(account)}
    t = add("wa:" + PA)
    add("wa:" + PA, name="Dues", text="Call Raj", cron="0 9 * * *", kind="reminder")
    run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES(?,?,?,?,?,?,0)",
        ("wa:" + PA, t["id"], "x", "Old report", "OLD OWNER'S NUMBERS", "n"))
    run("INSERT INTO proposals VALUES(?,?,?)", ("wa:" + PA, "{}", time.time()))
    post("sales?", phone=PA)  # first message: the number is bound to acctX
    return t


def test_same_account_keeps_everything(env):
    seed_phone_a(env)
    env.sent.clear()
    post("sales?", phone=PA)
    assert len(run("SELECT 1 FROM tasks")) == 2 and run("SELECT 1 FROM proposals")


def test_number_relinked_to_a_different_account_wipes_the_old_owners_schedules(env):
    seed_phone_a(env, "acctX")
    env.sent.clear()
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": acct("acctY")}  # recycled number / re-linked elsewhere
    post("sales?", phone=PA)
    assert not run("SELECT 1 FROM tasks") and not run("SELECT 1 FROM proposals") and not run("SELECT 1 FROM inbox")
    assert all("OLD OWNER" not in m for m in said(env))
    assert run("SELECT account FROM wa_account WHERE phone=?", (PA,))[0]["account"] == "acctY"


def test_report_firing_after_the_number_changed_hands_sends_nothing_and_calls_no_model(env, monkeypatch):
    t = seed_phone_a(env, "acctX")
    env.sent.clear()
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": acct("acctY")}
    monkeypatch.setattr(w, "token", lambda ph: env.tokens[ph])
    m = patch_runner(monkeypatch)
    scheduler.tick(due_of(t))
    assert not m.calls and sent_to(env, PA) == [] and not run("SELECT 1 FROM tasks")


def test_opaque_token_cannot_be_checked_so_nothing_is_wiped(env):
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": "not-a-jwt"}
    add("wa:" + PA)
    post("sales?", phone=PA)
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": "another-opaque"}
    post("sales?", phone=PA)
    assert len(run("SELECT 1 FROM tasks")) == 1 and not run("SELECT 1 FROM wa_account")


def test_two_phones_one_account_are_tracked_separately(env):
    env.tokens.update({PA: {"role": "vendor", "mcpAccessToken": acct("same")}, PB: {"role": "vendor", "mcpAccessToken": acct("same")}})
    add("wa:" + PA); add("wa:" + PB)
    post("sales?", phone=PA); post("sales?", phone=PB)
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": acct("other")}  # only A's number changes hands
    post("sales?", phone=PA)
    assert [r["vendor_id"] for r in run("SELECT vendor_id FROM tasks")] == ["wa:" + PB]


def test_logout_forgets_the_binding_so_a_fresh_link_starts_clean(env):
    seed_phone_a(env, "acctX")
    post("STOP", phone=PA)
    assert not run("SELECT 1 FROM wa_account")
    env.tokens[PA] = {"role": "vendor", "mcpAccessToken": acct("acctY")}
    add("wa:" + PA, name="new", text="new")
    post("sales?", phone=PA)
    assert len(run("SELECT 1 FROM tasks")) == 1  # nothing to wipe: the new owner's own task survives
