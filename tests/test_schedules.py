"""Chat-driven scheduling: the model only proposes; code creates after the vendor's own YES."""
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.update(JWT_SECRET="s", OKGHUMO_MCP_URL="https://mcp.test/mcp")
import app.main as m  # noqa: E402
import app.runner as runner  # noqa: E402
from app import assistant, db, pdfreport, scheduler, schedules  # noqa: E402
from app.credentials import NoCredential  # noqa: E402
from app.db import run  # noqa: E402
from app.main import app, get_client  # noqa: E402
from app.rbac import READ_ONLY_TOOLS  # noqa: E402

c = TestClient(app)
GOOD = {"kind": "report", "name": "Daily sales", "text": "Sales and bookings for yesterday", "frequency": "daily", "time": "08:00"}


class Fake:
    """Scripted model: each create() pops the next list of content blocks."""
    def __init__(self, *script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        blocks = self.script.pop(0) if self.script else [text("ok")]
        return NS(stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn", content=blocks)


def text(t): return NS(type="text", text=t)
def tool(name, inp, i="t1"): return NS(type="tool_use", id=i, name=name, input=inp)
def propose(**kw): return tool("propose_schedule", {**GOOD, **kw})
def user(t): return {"role": "user", "content": t}
def bot(t): return {"role": "assistant", "content": t}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for t in ("tasks", "inbox", "proposals"):
        run(f"DELETE FROM {t}")
    monkeypatch.setattr(m, "mcp_token", lambda vid: "mcp-" + vid)
    monkeypatch.delenv("SCHEDULING_VIA_CHAT", raising=False)
    yield
    app.dependency_overrides.clear()


def chat(vendor, messages, fake=None, role="vendor"):
    fake = fake or Fake()
    app.dependency_overrides[get_client] = lambda: fake
    h = {"Authorization": "Bearer " + jwt.encode({"sub": vendor, "role": role}, "s", algorithm="HS256")}
    r = c.post("/chat", json={"messages": messages}, headers=h)
    assert r.status_code == 200, r.text
    return r.json(), fake


def tasks_of(v): return run("SELECT * FROM tasks WHERE vendor_id=?", (v,))
def proposed(v, fake=None, **kw):
    """Run the propose turn; returns the assistant reply to feed back as history."""
    out, _ = chat(v, [user("send me daily sales")], fake or Fake([propose(**kw)], [text("Here's the plan:")]))
    return out["reply"]


# ---------- propose -> confirm happy path ----------
def test_propose_creates_nothing_then_yes_creates():
    reply = proposed("A")
    assert schedules.MARK in reply and "Daily sales" in reply and "08:00" in reply and "Asia/Kolkata" in reply
    assert tasks_of("A") == []  # a proposal alone never creates a task
    out, f = chat("A", [user("send me daily sales"), bot(reply), user("Yes")])
    assert f.calls == []  # confirmation handled in code: zero model calls
    (t,) = tasks_of("A")
    assert (t["kind"], t["role"], t["cron"], t["tz"], t["status"]) == ("report", "vendor", "0 8 * * *", "Asia/Kolkata", "active")
    assert out["reply"].startswith("Done") and "inbox" in out["reply"]
    assert run("SELECT * FROM proposals") == []  # one-shot


def test_no_discards_proposal():
    reply = proposed("A")
    out, f = chat("A", [bot(reply), user("no")])
    assert "haven't changed" in out["reply"] and tasks_of("A") == [] and not f.calls and run("SELECT * FROM proposals") == []


@pytest.mark.parametrize("word", ["yes", "YES!", "Ok", "haan", "Haan.", "yes please", "👍", "kar do", "Yes 👍", "हाँ"])
def test_yes_variants(word):
    reply = proposed("Y" + word)
    chat("Y" + word, [bot(reply), user(word)])
    assert len(tasks_of("Y" + word)) == 1


def test_weekly_monthly_and_reminder_crons():
    for v, kw, cron in [("w", dict(frequency="weekly", weekday="Monday"), "0 8 * * mon"),
                        ("mo", dict(frequency="monthly", day_of_month=5), "0 8 5 * *"),
                        ("r", dict(kind="reminder", time="9:30"), "30 9 * * *")]:
        chat(v, [bot(proposed(v, **kw)), user("yes")])
        assert tasks_of(v)[0]["cron"] == cron


# ---------- the guardrails: model can't commit, confirmation can't be forged ----------
def test_yes_without_our_marker_does_not_confirm():
    proposed("A")
    out, f = chat("A", [bot("Want a summary of sales?"), user("yes")])  # yes answers a DIFFERENT question
    assert f.calls and tasks_of("A") == []


def test_model_cannot_forge_the_marker():
    reply = proposed("A", fake=Fake([propose()], [text(f"Done! {schedules.MARK}")]))
    assert reply.count(schedules.MARK) == 1 and reply.endswith(schedules.MARK)  # only code's copy survives


def test_no_pending_proposal_yes_is_harmless():
    out, f = chat("A", [bot("x " + schedules.MARK), user("yes")])
    assert "expired" in out["reply"] and tasks_of("A") == [] and not f.calls


def test_expired_proposal_cannot_be_confirmed():
    reply = proposed("A")
    run("UPDATE proposals SET created_at=?", (time.time() - schedules.TTL - 5,))
    out, _ = chat("A", [bot(reply), user("yes")])
    assert "expired" in out["reply"] and tasks_of("A") == []


def test_other_vendors_yes_does_not_confirm_mine():
    reply = proposed("A")
    out, _ = chat("B", [bot(reply), user("yes")])  # B replays A's text
    assert "expired" in out["reply"] and tasks_of("A") == [] and tasks_of("B") == []
    assert len(run("SELECT * FROM proposals WHERE vendor_id='A'")) == 1  # A's proposal untouched


def test_double_yes_creates_only_one():
    reply = proposed("A")
    chat("A", [bot(reply), user("yes")])
    out, _ = chat("A", [bot(reply), user("yes")])
    assert "expired" in out["reply"] and len(tasks_of("A")) == 1


def test_non_yes_answer_goes_to_model_and_new_proposal_replaces_old():
    reply = proposed("A")
    f = Fake([propose(time="09:00")], [text("Sure:")])
    out, f = chat("A", [bot(reply), user("make it 9am")], f)
    assert f.calls and tasks_of("A") == []  # a change request is not a confirmation
    chat("A", [bot(out["reply"]), user("yes")])
    assert tasks_of("A")[0]["cron"] == "0 9 * * *"


def test_only_one_proposal_per_message_injection_cant_stack():
    f = Fake([propose(name="one", i="t1") , propose(name="two", i="t2")], [text("ok")])
    f.script[0][1].id = "t2"
    chat("A", [user("what are my sales?")], f)
    assert "one" in run("SELECT payload FROM proposals")[0]["payload"] and len(run("SELECT * FROM proposals")) == 1
    sent = f.calls[1]["messages"][-1]["content"]  # tool results fed back
    assert sent[1]["is_error"] and "one proposal" in sent[1]["content"]


def test_tools_offered_are_propose_or_read_only_and_mcp_stays_read_only():
    out, f = chat("A", [user("what are my sales?")])
    tools = f.calls[0]["tools"]
    assert set(tools[0]["configs"]) <= READ_ONLY_TOOLS and tools[0]["default_config"] == {"enabled": False}
    assert [t["name"] for t in tools[1:]] == ["propose_schedule", "propose_cancel", "list_schedules", "create_pdf_report"]
    assert "NOT scheduled" in schedules.Scheduling("A", "vendor").call("propose_schedule", GOOD)


def test_kill_switch_removes_tools_and_confirmation(monkeypatch):
    reply = proposed("A")
    monkeypatch.setenv("SCHEDULING_VIA_CHAT", "0")
    out, f = chat("A", [bot(reply), user("yes")])
    assert len(f.calls) == 1 and [t["name"] for t in f.calls[0]["tools"][1:]] == ["create_pdf_report"] and tasks_of("A") == []


def test_unknown_role_gets_no_scheduling():
    app.dependency_overrides[get_client] = lambda: Fake()
    h = {"Authorization": "Bearer " + jwt.encode({"sub": "A", "role": "hacker"}, "s", algorithm="HS256")}
    assert c.post("/chat", json={"messages": [user("what are my sales?")]}, headers=h).status_code == 403


def test_tool_loop_is_capped():
    f = Fake(*[[tool("list_schedules", {}, f"t{i}")] for i in range(20)])
    chat("A", [user("what are my sales?")], f)
    assert len(f.calls) == assistant.MAX_STEPS


# ---------- bad input from the model never reaches the DB ----------
@pytest.mark.parametrize("bad", [
    dict(time="25:00"), dict(time="8am"), dict(time=""), dict(frequency="hourly"), dict(frequency="weekly"),
    dict(frequency="weekly", weekday="funday"), dict(frequency="monthly"), dict(frequency="monthly", day_of_month=31),
    dict(frequency="monthly", day_of_month=True), dict(frequency="monthly", day_of_month="5"), dict(tz="Mars/Base"),
    dict(tz="America"), dict(tz="../etc/passwd"), dict(kind="delete_everything"), dict(name=""), dict(name="x" * 81),
    dict(text=""), dict(text="x" * 501), dict(time="08:00; DROP TABLE tasks"),
])
def test_invalid_proposals_rejected(bad):
    s = schedules.Scheduling("A", "vendor")
    assert s.call("propose_schedule", {**GOOD, **bad}).startswith("error:") and s.note is None
    assert run("SELECT * FROM proposals") == []


def test_garbage_tool_input_and_unknown_tool():
    s = schedules.Scheduling("A", "vendor")
    for inp in (None, "str", [], {}):
        assert s.call("propose_schedule", inp).startswith("error:")
        assert s.call("propose_cancel", inp).startswith("error:")
    assert s.call("delete_all_tasks", {}).startswith("error:")
    assert s.call("authenticate", {}).startswith("error:")


def test_limits_and_duplicates():
    for i in range(5):
        chat("L", [bot(proposed("L", text=f"q{i}")), user("yes")])
    s = schedules.Scheduling("L", "vendor")
    assert "scheduled reports" in s.call("propose_schedule", {**GOOD, "text": "q99"})  # max 5 reports
    chat("L", [bot(proposed("L", kind="reminder", text="r")), user("yes")])  # reminders still fine
    assert len(tasks_of("L")) == 6
    assert "already scheduled" in s.call("propose_schedule", {**GOOD, "kind": "reminder", "text": "r"})


def test_limit_hit_between_proposal_and_yes():
    reply = proposed("A")
    for i in range(10):  # another path fills the vendor's quota meanwhile
        c.post("/tasks", json={"name": "n", "message": f"m{i}", "cron": "0 8 * * *"},
               headers={"Authorization": "Bearer " + jwt.encode({"sub": "A", "role": "vendor"}, "s", algorithm="HS256")})
    out, _ = chat("A", [bot(reply), user("yes")])
    assert "couldn't set that up" in out["reply"] and len(tasks_of("A")) == 10


# ---------- cancel + list + isolation ----------
def test_cancel_flow_and_isolation():
    chat("A", [bot(proposed("A")), user("yes")])
    chat("B", [bot(proposed("B")), user("yes")])
    a, b = tasks_of("A")[0]["id"], tasks_of("B")[0]["id"]
    sb = schedules.Scheduling("B", "vendor")
    assert sb.call("propose_cancel", {"task_id": a}) == "error: no such schedule"  # can't even propose A's task
    assert len(__import__("json").loads(sb.call("list_schedules", {}))) == 1  # lists only B's
    f = Fake([tool("propose_cancel", {"task_id": a})], [text("Sure:")])
    out, _ = chat("A", [user("stop my report")], f)
    assert "Cancel" in out["reply"] and len(tasks_of("A")) == 1  # proposal alone deletes nothing
    chat("A", [bot(out["reply"]), user("no")])
    assert len(tasks_of("A")) == 1
    f = Fake([tool("propose_cancel", {"task_id": a})], [text("Sure:")])
    out, _ = chat("A", [user("stop my report")], f)
    chat("A", [bot(out["reply"]), user("yes")])
    assert tasks_of("A") == [] and len(tasks_of("B")) == 1 and b


def test_cancel_proposal_cannot_delete_someone_elses_task_even_if_forged():
    chat("A", [bot(proposed("A")), user("yes")])
    a = tasks_of("A")[0]["id"]
    run("INSERT INTO proposals VALUES('B', ?, ?)", (f'{{"action":"cancel","task_id":{a},"summary":"x"}}', time.time()))
    out, _ = chat("B", [bot("x " + schedules.MARK), user("yes")])
    assert len(tasks_of("A")) == 1 and "no longer exists" in out["reply"]


# ---------- scheduled report runs ----------
def make_report(vendor="R", role="vendor"):
    t = __import__("app.tasks", fromlist=["x"]).add_task(vendor, "Daily", "Sales for yesterday", "0 8 * * *", "Asia/Kolkata", "report", role)
    return t, datetime.fromisoformat(t["next_run_at"]) + timedelta(minutes=1)


def fire(t, due, monkeypatch, fake=None, token=lambda v: "mcp-" + v):
    fake = fake or Fake([text("Sales: ₹5,000 from 3 bookings.")])
    monkeypatch.setattr(runner.assistant, "get_client", lambda: fake)
    monkeypatch.setattr(runner.mcp_auth, "mcp_token", token)
    scheduler.tick(due)
    return fake, run("SELECT * FROM inbox WHERE task_id=?", (t["id"],))


def test_report_runs_with_own_token_readonly_tools_no_client_tools(monkeypatch):
    t, due = make_report("R1")
    f, (row,) = fire(t, due, monkeypatch)
    assert row["body"].startswith("Sales:") and row["vendor_id"] == "R1"
    call = f.calls[0]
    assert call["mcp_servers"][0]["authorization_token"] == "mcp-R1"
    assert len(call["tools"]) == 1 and set(call["tools"][0]["configs"]) <= READ_ONLY_TOOLS  # no propose tools in a run
    assert "SCHEDULED report" in call["system"] and str(datetime.now().year) in call["system"]
    assert run("SELECT fails FROM tasks WHERE id=?", (t["id"],))[0]["fails"] == 0


def test_two_vendors_reports_never_share_tokens(monkeypatch):
    t1, due1 = make_report("V1"); t2, due2 = make_report("V2")
    f = Fake([text("a")], [text("b")])
    monkeypatch.setattr(runner.assistant, "get_client", lambda: f)
    monkeypatch.setattr(runner.mcp_auth, "mcp_token", lambda v: "mcp-" + v)
    scheduler.tick(max(due1, due2))
    assert sorted(x["mcp_servers"][0]["authorization_token"] for x in f.calls) == ["mcp-V1", "mcp-V2"]


def test_missing_credential_no_model_call_and_friendly_notice(monkeypatch):
    t, due = make_report("NC")
    def nocred(v): raise NoCredential(v)
    f, (row,) = fire(t, due, monkeypatch, token=nocred)
    assert f.calls == [] and "reconnect" in row["body"].lower()
    assert run("SELECT fails FROM tasks WHERE id=?", (t["id"],))[0]["fails"] == 1


def test_unknown_role_no_token_no_model_call(monkeypatch):
    t, due = make_report("UR", role="hacker")
    f, (row,) = fire(t, due, monkeypatch)
    assert f.calls == [] and "role" in row["body"]


def test_model_crash_empty_answer_and_long_answer(monkeypatch):
    t, due = make_report("CR")
    class Boom:
        beta = NS(messages=NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("secret detail"))))
    f, (row,) = fire(t, due, monkeypatch, fake=Boom())
    assert "secret" not in row["body"] and "couldn't" in row["body"]
    t2, due2 = make_report("EM")
    f, (row,) = fire(t2, due2, monkeypatch, fake=Fake([text("   ")]))
    assert "couldn't" in row["body"]
    t3, due3 = make_report("LG")
    f, (row,) = fire(t3, due3, monkeypatch, fake=Fake([text("x" * 9000)]))
    assert len(row["body"]) == runner.MAX_BODY


def test_five_failures_pause_and_resume_resets(monkeypatch):
    t, _ = make_report("PA")
    def nocred(v): raise NoCredential(v)
    monkeypatch.setattr(runner.mcp_auth, "mcp_token", nocred)
    for i in range(runner.MAX_FAILS):
        row = run("SELECT * FROM tasks WHERE id=?", (t["id"],))[0]
        iid = run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at) VALUES('PA',?,?, 'x','p','n') RETURNING id", (t["id"], str(i)))[0]["id"]
        runner.run_report(iid, dict(row))
    row = run("SELECT * FROM tasks WHERE id=?", (t["id"],))[0]
    assert row["status"] == "paused" and row["fails"] == 5
    hdr = {"Authorization": "Bearer " + jwt.encode({"sub": "PA", "role": "vendor"}, "s", algorithm="HS256")}
    assert c.patch(f"/tasks/{t['id']}", json={"status": "active"}, headers=hdr).json()["fails"] == 0


def test_report_slot_fires_once_even_if_scheduler_restarts(monkeypatch):
    t, due = make_report("ID")
    f, rows = fire(t, due, monkeypatch)
    run("UPDATE tasks SET next_run_at=? WHERE id=?", (t["next_run_at"], t["id"]))  # simulate crash before update
    fire(t, due, monkeypatch, fake=f)
    assert len(rows) == 1 and len(run("SELECT * FROM inbox WHERE task_id=?", (t["id"],))) == 1 and len(f.calls) == 1


def test_reminders_still_plain_text_no_model(monkeypatch):
    t = __import__("app.tasks", fromlist=["x"]).add_task("RM", "Pay", "Pay staff", "0 8 * * *", "Asia/Kolkata")
    f, (row,) = fire(t, datetime.fromisoformat(t["next_run_at"]) + timedelta(minutes=1), monkeypatch)
    assert row["body"] == "Pay staff" and f.calls == []


# ---------- migration of an existing prod DB ----------
def test_old_tasks_table_is_migrated(monkeypatch):
    p = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sqlite3.connect(p)
    old.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT NOT NULL, name TEXT NOT NULL, "
                "message TEXT NOT NULL, cron TEXT NOT NULL, tz TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', "
                "next_run_at TEXT NOT NULL, last_run_at TEXT, created_at TEXT NOT NULL)")
    old.execute("INSERT INTO tasks(vendor_id,name,message,cron,tz,next_run_at,created_at) VALUES('v','n','m','0 8 * * *','UTC','x','x')")
    old.commit(); old.close()
    monkeypatch.setenv("DB_PATH", p)
    row = run("SELECT * FROM tasks")[0]
    assert (row["kind"], row["role"], row["fails"]) == ("reminder", None, 0)
    run("SELECT * FROM tasks")  # second connect is a no-op


# ---------- PDF reports (format=pdf) ----------
SPEC = ('{"title":"Weekly sales","period":"Last 7 days","summary":"Sales were ₹12,500 across 8 bookings.",'
        '"kpis":[{"label":"Revenue","value":"₹12,500"}],'
        '"charts":[{"type":"bar","title":"By source","labels":["online","agent"],"values":[5000,7500]}],'
        '"tables":[{"title":"Top","columns":["Agent","Amt"],"rows":[["<b>Ravi</b> & co","₹100"]]}],"insights":["Agents lead"]}')


def make_pdf_report(vendor, role="vendor"):
    t = __import__("app.tasks", fromlist=["x"]).add_task(vendor, "Weekly", "Sales last 7 days", "0 8 * * *", "Asia/Kolkata", "report", role, "pdf")
    return t, datetime.fromisoformat(t["next_run_at"]) + timedelta(minutes=1)


def hdr(v): return {"Authorization": "Bearer " + jwt.encode({"sub": v, "role": "vendor"}, "s", algorithm="HS256")}


def test_pdfreport_parse_clamps_and_render_handles_markup():
    from app import pdfreport
    s = pdfreport.parse("noise " + SPEC + " noise")
    assert pdfreport.render(s).startswith(b"%PDF") and "Rs. 12,500" in pdfreport.text_of(s)
    bad = pdfreport.parse('{"summary":"x","charts":[{"type":"pie","labels":["a"],"values":[-1]},{"type":"bar","labels":["a","b"],"values":[1]},'
                          '{"type":"line","labels":["a"],"values":[1]},{"type":"evil","labels":["a"],"values":[1]}]}')
    assert bad["charts"] == []
    for junk in ("", "not json", "[]", '{"charts":[]}'):
        with pytest.raises(pdfreport.BadSpec):
            pdfreport.parse(junk)


def test_pdf_format_proposal_then_yes_stores_format_and_reminders_stay_text():
    reply = proposed("PF", format="pdf")
    assert "PDF" in reply
    chat("PF", [user("x"), bot(reply), user("yes")])
    assert tasks_of("PF")[0]["format"] == "pdf"
    reply = proposed("PR", kind="reminder", format="pdf")
    assert "PDF" not in reply
    chat("PR", [user("x"), bot(reply), user("yes")])
    assert tasks_of("PR")[0]["format"] == "text"
    assert schedules._clean({**GOOD})["format"] == "text"  # default
    with pytest.raises(ValueError):
        schedules._clean({**GOOD, "format": "docx"})


def test_pdf_report_run_stores_pdf_and_stays_readonly(monkeypatch):
    t, due = make_pdf_report("P1")
    f, (row,) = fire(t, due, monkeypatch, fake=Fake([text(SPEC)]))
    assert row["pdf"].startswith(b"%PDF") and "Rs. 12,500" in row["body"] and "{" not in row["body"]
    call = f.calls[0]
    assert "OUTPUT FORMAT" in call["system"] and call["max_tokens"] == pdfreport.PDF_TOKENS
    assert len(call["tools"]) == 1 and set(call["tools"][0]["configs"]) <= READ_ONLY_TOOLS
    assert run("SELECT fails FROM tasks WHERE id=?", (t["id"],))[0]["fails"] == 0


def test_text_report_unchanged_and_bad_pdf_json_fails_cleanly(monkeypatch):
    t, due = make_report("P2")
    f, (row,) = fire(t, due, monkeypatch)
    assert row["pdf"] is None and "OUTPUT FORMAT" not in f.calls[0]["system"]
    t, due = make_pdf_report("P3")
    f, (row,) = fire(t, due, monkeypatch, fake=Fake([text("Sorry, here are some numbers in prose")]))
    assert row["pdf"] is None and "PDF" in row["body"]
    assert run("SELECT fails FROM tasks WHERE id=?", (t["id"],))[0]["fails"] == 1


def test_inbox_lists_has_pdf_without_blob_and_pdf_download_is_owner_only(monkeypatch):
    t, due = make_pdf_report("P4")
    fire(t, due, monkeypatch, fake=Fake([text(SPEC)]))
    items = c.get("/inbox", headers=hdr("P4")).json()
    assert items[0]["has_pdf"] == 1 and "pdf" not in items[0]
    r = c.get(f"/inbox/{items[0]['id']}/pdf", headers=hdr("P4"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert c.get(f"/inbox/{items[0]['id']}/pdf", headers=hdr("OTHER")).status_code == 404


def test_whatsapp_pdf_report_is_sent_as_document(monkeypatch):
    from app import whatsapp
    sent = []
    monkeypatch.setattr(whatsapp, "send_pdf", lambda *a: sent.append(a))
    monkeypatch.setattr(whatsapp, "send", lambda *a: sent.append(("text",) + a))
    iid = run("INSERT INTO inbox(vendor_id,task_id,scheduled_for,title,body,created_at,sent) VALUES('wa:1',1,'s','Weekly','b','n',0) RETURNING id")[0]["id"]
    whatsapp.deliver(iid, "wa:1", "Weekly", "Sales were X", b"%PDF-1")
    assert sent == [("1", b"%PDF-1", "Weekly", "*Weekly*\nSales were X")]
    assert run("SELECT sent FROM inbox WHERE id=?", (iid,))[0]["sent"] == 1


# ---------- on-demand PDF report in chat ----------
ASK = [user("give me a detailed report of last month sales")]


def test_on_demand_pdf_tool_creates_pdf_for_owner_only_readonly_nested_call():
    f = Fake([tool("create_pdf_report", {"question": "Sales and bookings, last 30 days"})], [text(SPEC)], [text("Your report is ready.")])
    out, f = chat("OD", ASK, f)
    assert out["reply"] == "Your report is ready." and out["pdf_id"]
    outer, nested = f.calls[0], f.calls[1]
    assert "DETAILED REPORTS" in outer["system"] and "politely" in outer["system"]
    assert [t["name"] for t in outer["tools"][1:]] == ["propose_schedule", "propose_cancel", "list_schedules", "create_pdf_report"]
    assert "OUTPUT FORMAT" in nested["system"] and len(nested["tools"]) == 1  # nested call: read-only MCP only, no app tools
    assert nested["mcp_servers"][0]["authorization_token"] == "mcp-OD"
    r = c.get(f"/inbox/{out['pdf_id']}/pdf", headers=hdr("OD"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert c.get(f"/inbox/{out['pdf_id']}/pdf", headers=hdr("OTHER")).status_code == 404


def test_on_demand_pdf_failure_and_daily_cap_and_kill_switch(monkeypatch):
    out, f = chat("OF", ASK, Fake([tool("create_pdf_report", {"question": "x"})], [text("not json")], [text("still not json")], [text("Sorry, here are the figures.")]))
    assert "pdf_id" not in out and "error" in str(f.calls[3]["messages"][-1]["content"])  # call 2 = nested, 3 = its one retry
    monkeypatch.setenv("RATE_PDF_PER_DAY", "1")
    chat("OC", ASK, Fake([tool("create_pdf_report", {"question": "x"})], [text(SPEC)], [text("ok")]))
    out, f = chat("OC", ASK, Fake([tool("create_pdf_report", {"question": "x"})], [text("never used")]))
    assert "pdf_id" not in out and "daily limit" in str(f.calls[1]["messages"][-1]["content"]) and len(f.calls) == 2
    monkeypatch.setenv("PDF_REPORTS", "0")
    out, f = chat("OK", ASK)
    assert "create_pdf_report" not in [t.get("name") for t in f.calls[0]["tools"]] and "DETAILED REPORTS" not in f.calls[0]["system"]


def test_on_demand_whatsapp_vendor_gets_file_object_but_nothing_stored(monkeypatch):
    from app import reports
    monkeypatch.setattr(reports.pdfreport, "generate", lambda *a: (pdfreport.parse(SPEC), b"%PDF-x"))
    h = reports.OnDemand(None, "wa:99", "vendor", "tok")
    assert h.call("create_pdf_report", {"question": "q"}).startswith("PDF created")
    assert h.pdf == ("Weekly sales", b"%PDF-x") and h.pdf_id is None
    assert run("SELECT 1 FROM inbox WHERE vendor_id='wa:99'") == []
    assert h.call("create_pdf_report", {"question": "q"}).startswith("error")  # one per message
    assert reports.OnDemand(None, "v", "hacker", "t").call("create_pdf_report", {"question": "q"}).startswith("error")


def test_pdf_long_labels_kept_and_long_kpis_render():
    s = pdfreport.parse('{"summary":"x","kpis":[{"label":"Booking value","value":"Rs. 33,48,608.2 vs Rs. 41,27,574"}],'
                        '"charts":[{"type":"bar","title":"t","labels":["Walk-in discounted Oct","Walk-in discounted Nov"],"values":[1,2]}]}')
    assert s["charts"][0]["labels"][0] == "Walk-in discounted Oct"  # not cut to a few characters
    assert pdfreport.render(s).startswith(b"%PDF")


def test_pdf_indian_number_grouping_for_chart_values():
    assert [pdfreport._inr(v) for v in (2500000, 12345678, 950, -4200, 0.5)] == ["25,00,000", "1,23,45,678", "950", "-4,200", "0.5"]


# ---------- why PDFs failed on WhatsApp: paused turns, cut-off JSON, prose instead of JSON, the "share" guard bug ----------
class Seq:
    """Scripted model that also sets stop_reason, like the real API (pause_turn / max_tokens / end_turn)."""
    def __init__(self, *script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        stop, blocks = self.script.pop(0) if self.script else ("end_turn", [text("ok")])
        if isinstance(blocks, Exception):
            raise blocks
        return NS(stop_reason=stop, content=blocks)


def gen(client):
    return pdfreport.generate(client, "vendor", "mcp-tok", "Monthly comparison, last 12 months")


def test_paused_turn_is_continued_not_treated_as_a_failure():
    mcp = NS(type="mcp_tool_use", id="m1", name="get_analysis_summary", input={}, server_name="okghumo")
    res = NS(type="mcp_tool_result", tool_use_id="m1", content=[NS(text="data")], is_error=False)
    s = Seq(("pause_turn", [text("Let me gather the months."), mcp, res]), ("end_turn", [text(SPEC)]))
    spec, pdf = gen(s)
    assert pdf.startswith(b"%PDF") and spec["title"] == "Weekly sales" and len(s.calls) == 2
    cont = s.calls[1]["messages"]
    assert cont[-1]["role"] == "assistant" and cont[-1]["content"][1]["name"] == "get_analysis_summary"  # paused part sent back
    assert s.calls[1]["tools"] == s.calls[0]["tools"] and s.calls[1]["mcp_servers"] == s.calls[0]["mcp_servers"]


def test_pause_continuation_error_falls_back_to_old_behaviour_no_crash():
    s = Seq(("pause_turn", [text("partial answer")]), ("end_turn", RuntimeError("api down")))
    out = assistant.ask(s, "vendor", "tok", [user("hi")])
    assert out["reply"] == "partial answer" and out["stop_reason"] == "pause_turn"


def test_cut_off_json_is_retried_compact_with_bigger_budget():
    s = Seq(("max_tokens", [text(SPEC[:150])]), ("end_turn", [text(SPEC)]))
    spec, pdf = gen(s)
    assert pdf.startswith(b"%PDF") and len(s.calls) == 2
    assert s.calls[0]["max_tokens"] == pdfreport.PDF_TOKENS >= 8000 and "compact" in s.calls[1]["messages"][-1]["content"]
    assert "ONLY the JSON" in s.calls[0]["messages"][-1]["content"]  # the format reminder is in the user turn too


def test_prose_answer_is_retried_and_two_failures_raise_badspec():
    s = Seq(("end_turn", [text("Here is your report in words: June was busiest.")]), ("end_turn", [text(SPEC)]))
    assert gen(s)[1].startswith(b"%PDF") and len(s.calls) == 2
    with pytest.raises(pdfreport.BadSpec):
        gen(Seq(("end_turn", [text("prose")]), ("end_turn", [text("more prose")])))


def test_parse_tolerates_narration_braces_and_numbers_typed_as_text():
    raw = 'Sure {as asked}. ' + '{"summary":"x","charts":[{"type":"bar","title":"t","labels":["a","b"],"values":["2,302","Rs. 1,200"]}]} Done {ok}'
    assert pdfreport.parse(raw)["charts"][0]["values"] == [2302.0, 1200.0]


def test_guard_no_longer_blocks_everyday_share_but_still_blocks_stock_talk():
    from app import guard
    ok = lambda t: guard.classify([{"role": "user", "content": t}])  # noqa: E731
    assert ok("Ok share it") is None and ok("share the report with me") is None
    assert ok("what is the share price of tata")[0] == "off_topic" and ok("which stocks should I buy")[0] == "off_topic"
