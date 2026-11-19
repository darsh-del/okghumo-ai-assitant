import os
from types import SimpleNamespace as NS

import jwt
from fastapi.testclient import TestClient

os.environ.update(JWT_SECRET="s", OKGHUMO_MCP_URL="https://mcp.test/mcp")
import app.main as m  # noqa: E402
from app.main import app, get_client  # noqa: E402
from app.rbac import READ_ONLY_TOOLS, ROLE_TOOLS, mcp_toolset  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="mcp_tool_use", name="get_bookings"), NS(type="text", text="hi")])


def call(role, fake):
    m.mcp_token = lambda vid: "mcp-tok"
    app.dependency_overrides[get_client] = lambda: fake
    h = {"Authorization": "Bearer " + jwt.encode({"role": role, "sub": "v1"}, "s", algorithm="HS256")}
    return TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "yo"}]}, headers=h)


def test_viewer_gets_allowlist_only():
    f = FakeClient()
    r = call("viewer", f)
    assert r.status_code == 200 and r.json()["reply"] == "hi" and r.json()["tools_used"] == ["get_bookings"]
    ts = f.calls[0]["tools"][0]
    assert ts["default_config"] == {"enabled": False} and "get_bookings" not in ts["configs"]


def test_admin_is_still_read_only_allowlist():
    f = FakeClient()
    call("admin", f)
    ts = f.calls[0]["tools"][0]
    assert ts["default_config"] == {"enabled": False}
    assert set(ts["configs"]) == READ_ONLY_TOOLS and "authenticate" not in ts["configs"]


def test_every_role_is_read_only():
    for role in ROLE_TOOLS:
        assert set(mcp_toolset(role)["configs"]) <= READ_ONLY_TOOLS
        assert not any(w in t for t in ROLE_TOOLS[role] for w in ("create", "update", "delete", "set_", "authenticate"))


def test_unknown_role_403_and_no_model_call():
    f = FakeClient()
    assert call("hacker", f).status_code == 403 and not f.calls


def test_no_or_bad_token_rejected():
    c = TestClient(app)
    assert c.post("/chat", json={}).status_code in (401, 403)
    assert c.post("/chat", json={}, headers={"Authorization": "Bearer x"}).status_code == 401


def test_mcp_token_sent_and_exchange_failure_is_502():
    f = FakeClient()
    call("viewer", f)
    assert f.calls[0]["mcp_servers"][0]["authorization_token"] == "mcp-tok"
    def boom(vid):
        raise KeyError("exchange")
    app.dependency_overrides[get_client] = lambda: f
    m.mcp_token = boom
    h = {"Authorization": "Bearer " + jwt.encode({"role": "viewer", "sub": "v1"}, "s", algorithm="HS256")}
    n = len(f.calls)
    assert TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=h).status_code == 502
    assert len(f.calls) == n
