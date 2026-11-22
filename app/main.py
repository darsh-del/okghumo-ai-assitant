import logging
import os
import time
import uuid
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import guard, logs, mcp_auth, ratelimit, sanitize, schedules, tasks, whatsapp
from app.assistant import SYSTEM, ask, get_client  # noqa: F401 (get_client is the test override key)
from app.auth import current_user
from app.credentials import NoCredential
from app.mcp_auth import mcp_token
from app.rbac import mcp_toolset

logs.setup()
log = logging.getLogger("api")
app = FastAPI(title="Okghumo AI Assistant")


@app.middleware("http")
async def access_log(request: Request, call_next):
    """One line per request: id, method, path (never the query string), status, duration. /health is skipped."""
    rid, t0 = uuid.uuid4().hex[:8], time.time()
    try:
        resp = await call_next(request)
    except Exception as e:
        logs.event(log, "request_error", logging.ERROR, rid=rid, method=request.method, path=request.url.path,
                   error=type(e).__name__)
        raise
    if request.url.path != "/health":
        logs.event(log, "request", rid=rid, method=request.method, path=request.url.path, status=resp.status_code,
                   ms=int((time.time() - t0) * 1000))
    resp.headers["X-Request-Id"] = rid
    return resp


app.include_router(tasks.router)
app.include_router(whatsapp.router)
# browser UI origin(s), comma-separated, e.g. https://chat.okghumo.com; empty = no CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1)  # stateless: client sends full history


class CredentialIn(BaseModel):
    token: str = Field(min_length=1)  # the vendor's own OkGhumo login token


@app.get("/health")
def health():
    return {"ok": True}


@app.put("/credentials", status_code=204)
def put_credential(body: CredentialIn, claims: dict = Depends(current_user)):
    ratelimit.enforce_credentials(claims["sub"])
    try:
        mcp_auth.register(claims["sub"], body.token)  # validated by a real exchange before storing
    except httpx.HTTPError:
        logs.event(log, "credential_rejected", logging.WARNING, vendor=logs.mask_id(claims["sub"]))
        raise HTTPException(400, "okghumo rejected this token")
    logs.event(log, "credential_registered", vendor=logs.mask_id(claims["sub"]))


@app.delete("/credentials", status_code=204)
def delete_credential(claims: dict = Depends(current_user)):
    mcp_auth.forget(claims["sub"])
    logs.event(log, "credential_deleted", vendor=logs.mask_id(claims["sub"]))


@app.post("/chat")
def chat(body: ChatIn, claims: dict = Depends(current_user), client=Depends(get_client)):
    ratelimit.enforce_chat(claims["sub"])  # 429 + Retry-After before anything else costs money
    msgs, role, vendor = [m.model_dump() for m in body.messages], claims.get("role"), claims["sub"]
    who = logs.mask_id(vendor)
    try:
        mcp_toolset(role)  # role gate BEFORE any token mint or model call
    except KeyError:
        logs.event(log, "chat_denied", logging.WARNING, reason="role_not_permitted", vendor=who, role=role)
        raise HTTPException(403, "role not permitted")
    done = schedules.intercept(vendor, role, msgs)  # vendor's yes/no to OUR confirmation prompt: handled in code
    if done is not None:
        logs.event(log, "chat_reply", vendor=who, source="confirmation", model_called=False)
        return {"reply": done, "tools_used": [], "stop_reason": "end_turn"}
    blocked = guard.classify(msgs)  # off-topic / injection / oversized / greeting: polite canned reply, no model call
    if blocked:
        logs.event(log, "chat_reply", vendor=who, source="guard", reason=blocked[0], model_called=False)
        return {"reply": blocked[1], "tools_used": [], "stop_reason": "end_turn"}
    msgs = guard.trim(msgs)
    try:
        token = mcp_token(vendor)  # this vendor's own token; fails closed, no model call
    except NoCredential:
        logs.event(log, "chat_denied", logging.WARNING, reason="no_credential", vendor=who)
        raise HTTPException(409, "no okghumo credential registered: PUT /credentials first")
    except (KeyError, httpx.HTTPError) as e:
        logs.event(log, "chat_denied", logging.ERROR, reason="mcp_exchange_failed", vendor=who, error=type(e).__name__)
        raise HTTPException(502, "okghumo auth unavailable")
    sched = schedules.for_vendor(vendor, role)
    out = ask(client, role, token, msgs, SYSTEM + (schedules.SCHED_SYSTEM if sched else ""), extra=sched)
    reply = sanitize.clean_reply(out["reply"], msgs)  # presentation only: ids, dashes, emoji cap (digits untouched)
    logs.event(log, "chat_reply", vendor=who, source="model", model_called=True, stop=out["stop_reason"],
               tools=",".join(out["tools_used"]) or None, reply_chars=len(reply))
    return {**out, "reply": sched.finish(reply) if sched else reply}
