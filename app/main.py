import os
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import mcp_auth, tasks, whatsapp
from app.assistant import ask, get_client  # noqa: F401 (get_client is the test override key)
from app.auth import current_user
from app.credentials import NoCredential
from app.mcp_auth import mcp_token
from app.rbac import mcp_toolset

app = FastAPI(title="Okghumo AI Assistant")
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
        mcp_toolset(claims.get("role"))  # role gate BEFORE any token mint or model call
    except KeyError:
        raise HTTPException(403, "role not permitted")
    try:
        token = mcp_token(claims["sub"])  # this vendor's own token; fails closed, no model call
    except NoCredential:
        raise HTTPException(409, "no okghumo credential registered: PUT /credentials first")
    except (KeyError, httpx.HTTPError):
        raise HTTPException(502, "okghumo auth unavailable")
    return ask(client, claims.get("role"), token, [m.model_dump() for m in body.messages])
