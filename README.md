# Okghumo AI Assistant

REST chat API (FastAPI) backed by Claude, with the Okghumo MCP server as the primary data source and role-based access control.

Repo: https://github.com/darsh-del/okghumo-ai-assitant

## How it works
- `POST /chat` takes `{"messages": [{"role","content"}, ...]}` (stateless; client sends history) and a `Authorization: Bearer <JWT>` header.
- The JWT (HS256, `JWT_SECRET`) must carry `sub` (vendor id) and `role` claims. Unknown role -> 403.
- `app/rbac.py` maps role -> allowed MCP tools. The allowlist is passed to Claude's MCP connector as an `mcp_toolset`, so tools a role can't use are never exposed to the model.
- Each vendor registers their own OkGhumo token once (`PUT /credentials`, stored encrypted); `app/mcp_auth.py` exchanges it for a 15-minute MCP token (cached per vendor). No shared token; no credential -> 409.
- Scheduled reminders: `/tasks` CRUD + `/inbox`; the `scheduler` container (`python -m app.scheduler`) fires them. Roadmap for data reports/email/WhatsApp: `SCHEDULING_ROADMAP.md`.
- Claude calls the Okghumo MCP server (`OKGHUMO_MCP_URL`) server-side via the Messages API MCP connector.
- Row/data-level permissions must be enforced by the MCP server itself.

## Run
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in values, then export them
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest
```

## TODO
- Okghumo MCP is read-only; see CLAUDE.md for the guardrails.
- Real auth (replace self-signed JWT with OkGhumo-validated identity), OkGhumo-issued read-only delegated credential — see `SCHEDULING_ROADMAP.md`.
- Streaming responses, conversation persistence.
