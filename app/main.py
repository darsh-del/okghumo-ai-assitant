import os
from typing import Literal

import anthropic
import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import mcp_auth, tasks
from app.auth import current_user
from app.credentials import NoCredential
from app.mcp_auth import mcp_token
from app.rbac import mcp_toolset

app = FastAPI(title="Okghumo AI Assistant")
app.include_router(tasks.router)
# browser UI origin(s), comma-separated, e.g. https://chat.okghumo.com; empty = no CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

SYSTEM = (
    "You are the Okghumo assistant. Use the Okghumo tools as your primary source of data; "
    "if the tools can't answer, say so rather than guessing."
)


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1)  # stateless: client sends full history


class CredentialIn(BaseModel):
    token: str = Field(min_length=1)  # the vendor's own OkGhumo login token


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


@app.get("/health")
def health():
    return {"ok": True}


@app.put("/credentials", status_code=204)
def put_credential(body: CredentialIn, claims: dict = Depends(current_user)):
    try:
        mcp_auth.register(claims["sub"], body.token)  # validated by a real exchange before storing
    except httpx.HTTPError:
        raise HTTPException(400, "okghumo rejected this token")


@app.delete("/credentials", status_code=204)
def delete_credential(claims: dict = Depends(current_user)):
    mcp_auth.forget(claims["sub"])


@app.post("/chat")
def chat(body: ChatIn, claims: dict = Depends(current_user), client=Depends(get_client)):
    try:
        toolset = mcp_toolset(claims.get("role"))
    except KeyError:
        raise HTTPException(403, "role not permitted")
    try:
        token = mcp_token(claims["sub"])  # this vendor's own token; fails closed, no model call
    except NoCredential:
        raise HTTPException(409, "no okghumo credential registered: PUT /credentials first")
    except (KeyError, httpx.HTTPError):
        raise HTTPException(502, "okghumo auth unavailable")
    server = {"type": "url", "name": "okghumo", "url": os.environ["OKGHUMO_MCP_URL"], "authorization_token": token}
    r = client.beta.messages.create(
        model=os.environ.get("CHAT_MODEL", "claude-opus-5-5"),
        max_tokens=16000,
        betas=["mcp-client-2025-11-20"],
        system=SYSTEM,
        messages=[m.model_dump() for m in body.messages],
        mcp_servers=[server],
        tools=[toolset],
    )
    return {
        "reply": "".join(b.text for b in r.content if b.type == "text"),
        "tools_used": [b.name for b in r.content if b.type == "mcp_tool_use"],
        "stop_reason": r.stop_reason,
    }
