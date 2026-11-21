from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

import app.mcp_auth as ma
from app import credentials, scheduler
from app.db import run
from app.main import app

c = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    run("DELETE FROM tasks")
    run("DELETE FROM inbox")


def h(vendor):
    return {"Authorization": "Bearer " + jwt.encode({"sub": vendor, "role": "viewer"}, "s", algorithm="HS256")}


TASK = {"name": "Daily", "message": "Check outstandings", "cron": "0 8 * * *"}


def test_no_sub_rejected():
    bad = {"Authorization": "Bearer " + jwt.encode({"role": "viewer"}, "s", algorithm="HS256")}
    assert c.get("/tasks", headers=bad).status_code == 401


def test_bad_schedules_rejected():
    for cron in ("*/5 * * * *", "nonsense", "0 8 * * * *"):
        assert c.post("/tasks", json={**TASK, "cron": cron}, headers=h("lim")).status_code == 422
    assert c.post("/tasks", json={**TASK, "tz": "Mars/Base"}, headers=h("lim")).status_code == 422


def test_task_limit():
    for i in range(10):
        assert c.post("/tasks", json={**TASK, "message": f"m{i}"}, headers=h("cap")).status_code == 201
    assert c.post("/tasks", json={**TASK, "message": "m11"}, headers=h("cap")).status_code == 409
    assert c.post("/tasks", json=TASK, headers=h("dup")).status_code == 201  # exact duplicate refused
    assert c.post("/tasks", json=TASK, headers=h("dup")).status_code == 409


def test_vendor_isolation():
    tid = c.post("/tasks", json=TASK, headers=h("A")).json()["id"]
    assert c.get("/tasks", headers=h("B")).json() == []
    assert c.patch(f"/tasks/{tid}", json={"status": "paused"}, headers=h("B")).status_code == 404
    assert c.delete(f"/tasks/{tid}", headers=h("B")).status_code == 404
    assert len(c.get("/tasks", headers=h("A")).json()) == 1


def test_scheduler_fires_once_and_isolated_inbox():
    t = c.post("/tasks", json=TASK, headers=h("S")).json()
    due = datetime.fromisoformat(t["next_run_at"]) + timedelta(minutes=1)
    assert scheduler.tick(due) == 1
    assert scheduler.tick(due) == 0  # not due again
    # restart-safety: force the same slot due again -> UNIQUE(task_id, scheduled_for) blocks a duplicate
    run("UPDATE tasks SET next_run_at=? WHERE id=?", (t["next_run_at"], t["id"]))
    assert scheduler.tick(due) == 0
    items = c.get("/inbox", headers=h("S")).json()
    assert len(items) == 1 and items[0]["body"] == TASK["message"]
    assert c.get("/inbox", headers=h("other")).json() == []
    c.post(f"/inbox/{items[0]['id']}/read", headers=h("other"))  # other vendor can't mark it
    assert c.get("/inbox?unread=true", headers=h("S")).json() != []
    c.post(f"/inbox/{items[0]['id']}/read", headers=h("S"))
    assert c.get("/inbox?unread=true", headers=h("S")).json() == []


def test_catch_up_fires_once_and_paused_never():
    t = c.post("/tasks", json=TASK, headers=h("C")).json()
    far = datetime.fromisoformat(t["next_run_at"]) + timedelta(days=5)
    assert scheduler.tick(far) == 1  # 5 missed days -> one reminder
    t2 = c.post("/tasks", json=TASK, headers=h("P")).json()
    c.patch(f"/tasks/{t2['id']}", json={"status": "paused"}, headers=h("P"))
    run("UPDATE tasks SET next_run_at='2000-01-01T00:00:00+00:00' WHERE id=?", (t2["id"],))
    assert scheduler.tick(far) == 0


def test_timezone_respected():
    t = c.post("/tasks", json={**TASK, "tz": "Asia/Kolkata"}, headers=h("TZ")).json()
    assert datetime.fromisoformat(t["next_run_at"]).astimezone(timezone(timedelta(hours=5, minutes=30))).hour == 8


def test_credentials_are_per_vendor_and_encrypted(monkeypatch):
    seen = []

    def fake_exchange(login):
        seen.append(login)
        return "mcp-" + login, 9e12

    monkeypatch.setattr(ma, "_exchange", fake_exchange)
    assert c.put("/credentials", json={"token": "tokA"}, headers=h("va")).status_code == 204
    assert c.put("/credentials", json={"token": "tokB"}, headers=h("vb")).status_code == 204
    assert ma.mcp_token("va") == "mcp-tokA" and ma.mcp_token("vb") == "mcp-tokB"  # never crossed
    assert b"tokA" not in run("SELECT token_enc FROM credentials WHERE vendor_id='va'")[0]["token_enc"]
    assert credentials.get("va") == "tokA"
    c.delete("/credentials", headers=h("va"))
    with pytest.raises(credentials.NoCredential):
        credentials.get("va")
    assert ma.mcp_token("vb") == "mcp-tokB"


def test_rejected_credential_not_stored(monkeypatch):
    def boom(login):
        raise httpx.HTTPError("401")

    monkeypatch.setattr(ma, "_exchange", boom)
    assert c.put("/credentials", json={"token": "bad"}, headers=h("vz")).status_code == 400
    with pytest.raises(credentials.NoCredential):
        credentials.get("vz")


def test_chat_without_credential_is_409_and_no_model_call():
    called = []

    class Spy:
        @property
        def beta(self):
            called.append(1)  # any model-call attempt lands here

    from app.main import get_client
    app.dependency_overrides[get_client] = lambda: Spy()
    ma._cache.pop("nocred", None)
    import app.main as m
    m.mcp_token = ma.mcp_token  # test_chat replaces it with a stub
    r = c.post("/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=h("nocred"))
    app.dependency_overrides.pop(get_client)
    assert r.status_code == 409 and not called
