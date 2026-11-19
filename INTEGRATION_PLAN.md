# Integrating the assistant into the OkGhumo app + vendor data isolation (draft v1, 2026-11-19)

Audience: the repo owner and the OkGhumo app / backend team. Part A is what we recommend; Part B is the
API contract the app team builds against; Part C is what the OkGhumo backend team must confirm; Part D
is how we guarantee one vendor can never see another vendor's data; Part E is our work and test plan.
**Nothing here is built yet** except what Part D marks as "today".

---

## A. Recommendation: the app calls us directly with the vendor's own OkGhumo token

Two ways to embed:

| | 1. App calls our API directly | 2. OkGhumo backend calls us for the vendor |
|---|---|---|
| Who proves identity | The vendor's own OkGhumo token, checked by OkGhumo's auth | OkGhumo's server vouches for the vendor with a server-issued token |
| Secrets we hold | None for chat (token is passed per request) | A shared server key; if it leaks, every vendor is exposed |
| Works for web + mobile | Yes | Yes, but every message hops through their backend |
| Isolation strength | Strong: the token itself is locked to one `clientId` by OkGhumo | Weaker: one compromised server key = any vendor |
| Their work | Small: add a chat screen that sends the token it already has | Medium: build and secure a proxy endpoint |

**Recommended: option 1.** Reason: the thing that must never be forged is "which vendor is this". With option 1
we do not mint or trust any identity ourselves. OkGhumo's own auth service says who the token belongs to, and
the data tokens it hands out are already locked to that vendor's `clientId`. Option 2 is the fallback if the
mobile app must not hold a token that can call us (see C-1).

What changes versus today: today a vendor needs a JWT made by hand with our `JWT_SECRET`, plus a one-time
"Connect" step that stores their login token. In option 1 both disappear for chat: the app sends the vendor's
token, we validate it with OkGhumo, and we use it. (Scheduled reports still need a stored credential because no
one is online at 8am; see `SCHEDULING_ROADMAP.md`, Phase 0b.)

Important caveat: a normal login token is a full user session. The strongest version is a **purpose-built,
short-lived, read-only "assistant token"** issued by OkGhumo (Part C, item 2). Until it exists we accept the
login token over HTTPS only.

---

## B. API contract for the app team

Base URL: `https://<our-domain>` (HTTPS required; domain not set up yet).

### Authentication
Every request carries the vendor's token: `Authorization: Bearer <vendor token>`.
We never read the vendor id from the request body, URL or headers. We derive it from OkGhumo's response to
the token (C-3). Tokens are never logged or stored by `/chat`.

### Endpoints (today vs target)
| Endpoint | Purpose | Status |
|---|---|---|
| `POST /chat` | Ask a question. Body: `{"messages":[{"role":"user"\|"assistant","content":"..."}]}` (client sends full history) | Exists; auth changes to the vendor token |
| `GET /health` | Liveness, no auth | Exists |
| `POST /tasks`, `GET /tasks`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}` | Scheduled reminders | Exists (Phase 1) |
| `GET /inbox`, `POST /inbox/{id}/read` | Reminder / report inbox | Exists |
| `PUT /credentials`, `DELETE /credentials` | Store/revoke a credential for scheduled runs | Exists; only needed for scheduling |
| `POST /voice` | Voice note -> same pipeline | Future (DESIGN.md §7) |

### `/chat` response
```json
{"reply": "text", "tools_used": ["get_analysis_summary"], "stop_reason": "end_turn"}
```
Replies can take up to ~1–2 minutes (Claude plus data calls): the app must show a loading state and use a
timeout of at least 120 s. Streaming is a later improvement.

### Errors the app must handle
| Status | Meaning | App should |
|---|---|---|
| 401 | Missing, invalid or expired vendor token | Send the vendor to log in again |
| 403 | Role not allowed (e.g. an admin on the vendor bot) | Hide the assistant / show "not available" |
| 409 | No credential stored (scheduled features only) | Prompt to enable scheduling |
| 429 | Rate limit (target, per vendor) | Back off, show "try again shortly" |
| 502 | OkGhumo auth or data service unavailable / token rejected | Show retry; re-login if it persists |

### What the app team must provide to us
- Exact web origin(s) so we can lock CORS (`CORS_ORIGINS`), and mobile user-agent / app id if applicable.
- A staging environment with **two or more test vendors with different data** (see Part E).
- Expected languages (English / Hindi / Hinglish / Gujarati / Marathi), peak users, and whether voice is wanted.
- Design: where the chat entry lives, how history is kept (the API is stateless; the client sends history).

### Sample
```bash
curl -s https://<our-domain>/chat \
  -H "Authorization: Bearer $VENDOR_TOKEN" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"How were my sales this month?"}]}'
```

---

## C. Questions the OkGhumo backend team must answer (these block the build)

1. **Which token** does the app hold for a logged-in vendor (dashboard session? something else), and how long
   does it live? Does `POST /api/auth/mcp/exchange` accept it for all vendor roles?
2. **Can OkGhumo issue a purpose-built assistant token** (read-only scope `okghumo-analysis:read`, short TTL,
   bound to the vendor's `clientId`, revocable)? And a longer-lived **refresh credential** for scheduled runs?
3. **How do we learn who the vendor is** from a token we cannot forge? Options: the exchange response includes
   `vendorId`/`clientId`/`role`; a token-introspection endpoint; or a public key (JWKS) so we verify a signed
   token locally. We must NOT decode an unverified token and trust it.
4. **Written confirmation** that MCP tokens return only the token owner's `clientId` for *every* tool,
   including by-ID lookups (`bookingId`, `agentId`, `activityId`): the same call with another vendor's id must
   return 403/404, never data.
5. What does `superVendor` mean (several clients?) and should it be allowed on the assistant? Which roles map to
   our `viewer` / `staff` / `admin`? Admin / superAdmin are refused until a separate admin assistant exists.
6. Revocation: when a vendor logs out, is deactivated, or loses a role, how fast does their token stop working?
7. Rate limits or IP allow-listing on `/api/auth/mcp/exchange` and `mcp.okghumo.com` for our server IP
   (`13.233.170.73`, to be replaced by a stable address)?
8. Data retention and privacy rules for chat transcripts; whether vendor chat may leave India (Anthropic API).

---

## D. How one vendor can never see another vendor's data

Principle: **the model never decides who the user is or what they may see.** Prompts are guidance only.
Defence in depth, strongest first:

| # | Layer | Guarantee | Enforced by | State |
|---|---|---|---|---|
| L1 | Identity from OkGhumo | We use the vendor's own token; we do not create identities | OkGhumo auth | Planned (token passthrough, C-1/C-3). Today: self-made JWT |
| L2 | Data locked to `clientId` | The data token only returns that vendor's rows, even if the model asks for another id | OkGhumo MCP / commerce servers | **Unverified** — must be confirmed in writing and tested with two vendors (C-4) |
| L3 | Per-vendor state | Token cache, stored credentials, tasks, inbox are all keyed by the verified vendor id; every query filters by it; other vendors get 404 | Our API | **Built and tested** (`tests/test_tasks.py`); cache keyed by vendor id |
| L4 | Read-only allowlist | Only the 9 read tools, default-deny; `authenticate` never exposed | `app/rbac.py` | **Built and tested** |
| L5 | Role gate | Only vendor roles; unknown role gets 403 and no model call | Our API | **Built** (admin/superAdmin policy decided with C-5) |
| L6 | Prompt rules | System prompt: only the signed-in vendor, report data, never follow instructions found in data | Model (best effort) | Partly; not relied on |
| L7 | Own tool loop (ID allowlist) | By-ID lookups (`bookingId`, `agentId`, `activityId`) allowed only for ids that appeared in THIS vendor's earlier results, blocked before reaching MCP | Our API | **Planned** (DESIGN.md L8); independent of OkGhumo servers |
| L8 | Tests in CI | A two-vendor red-team suite | Us | Partly (own layer); real cross-vendor suite needs staging vendors |

Why L7 matters: if L2 ever has a bug, L1/L3/L4 would not catch it. L7 is our own check that does not depend on
OkGhumo's servers. It is the layer that lets us promise isolation even if the data service is wrong.

Other rules that support isolation:
- **No shared token anywhere** (already removed); a cache hit is only possible for the same vendor id.
- **Never trust ids sent by the client or the model for identity.** Ids in tool arguments are data, validated by L2/L7.
- **Prompt injection is assumed to succeed sometimes** (booking names and notes are attacker-controlled text),
  so nothing it can reach, such as sending messages or changing schedules, exists for it.
- **Logs and audit:** log vendor id, tool names, argument hashes, status. Never log tokens, tool results or PII.
- **Transport and browser:** HTTPS only, CORS limited to the app origin, rate limits and per-vendor cost caps.
- **Scheduled reports (later):** delivery target fixed at creation time; a run uses only that vendor's credential.

---

## E. Our work and the test that proves isolation

### Build order
1. **Token passthrough auth:** new `app/auth.py` path validates the vendor token via OkGhumo (per C-3), derives
   vendor id and role, caches the 15-minute MCP token per vendor, keeps the self-made JWT only as a dev option
   behind a flag, then removes it.
2. **Isolation hardening:** role gate for vendor roles, L7 ID allowlist in our own tool loop, per-vendor rate
   limit and cost cap, audit log without PII.
3. **HTTPS + domain** and locked CORS; the OkGhumo app integrates against staging.
4. **Real cross-vendor test suite** (below) in CI against staging; must pass before any vendor uses it.
5. Scheduled reports, email, WhatsApp, voice: see `SCHEDULING_ROADMAP.md` and `DESIGN.md`.

### The cross-vendor test (must pass before launch)
Using two staging vendors, A and B, with different data:
1. As A, ask for B's `bookingId`, `agentId` and `activityId`, ask "show all vendors", paste injection text in
   a booking note, and ask for another phone number's data.
2. Expect refusal, not-found or empty. **Assert on the tool responses, not the model's wording.**
3. Replay: A's MCP token with B's ids directly against `/mcp` must be denied.
4. Cache test: A then B in the same process; B never receives A's token.
5. Expired or revoked token: 401/502 and no fallback token.
6. Role test: an admin token on the vendor assistant returns 403.
7. Tasks/inbox: A can never list, edit, delete or run B's tasks (already automated).

### Already done and tested in this repo
Per-vendor credential store and token cache, per-vendor tasks/inbox isolation, read-only allowlist, fail-closed
`/chat` (409/502 with no model call).

## F. Short brief to send the OkGhumo team
> We are adding an AI assistant that answers vendors' questions about their own business data (read-only).
> Plan: your app calls our `/chat` with the vendor's own token; we validate it with your auth and use your
> existing read-only MCP. We need: (1) which token the app holds, (2) a short-lived read-only assistant token
> and a revocable refresh credential, (3) a way to verify vendor id/role/clientId from a token, (4) written
> confirmation that MCP returns only the token owner's `clientId` including by-ID lookups, (5) role mapping
> including superVendor, (6) a staging environment with two test vendors. Details: `INTEGRATION_PLAN.md`.
