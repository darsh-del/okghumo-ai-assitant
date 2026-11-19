# Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)

Goal: a chatbot (text + voice notes) where each vendor can ask about **their own** business only.
**Hard requirement: no vendor may ever see another vendor's data.** MCP stays read-only (see CLAUDE.md).

## 1. Blocker in the current code
Today `/chat` uses ONE shared `OKGHUMO_LOGIN_TOKEN` (a single vendor's session). Every caller would see
that one vendor's data. The JWT `role` is also self-asserted by our own secret. **This must be replaced by
per-vendor identity before any vendor uses it.** Nothing in this doc is built yet.

## 2. Core principle
**The model never decides who the user is or what they may see.** Identity comes from a credential the
vendor holds, is checked by OkGhumo's own auth, and travels to the MCP as that vendor's token. Prompts are
guidance, not security (OWASP: authorization must live outside the model, tools run as the *user*, not a shared service identity).

## 3. Architecture
```
Vendor (app / WhatsApp)
   │  text or voice note + vendor's own OkGhumo login token
   ▼
┌──────────────── Chat API (FastAPI) ─────────────────┐
│ 1 Gate    the vendor APP only hands us a token. WE   │
│           validate it (exchange call) and derive     │
│           vendorId/role OURSELVES; identity is never │
│           taken from the request body or the app     │
│ 2 Voice   (if audio) validate → STT → transcript    │
│ 3 Exchange POST /api/auth/mcp/exchange with THIS    │
│           vendor's token → 15-min MCP token         │
│           (cache keyed by vendorId, never shared)   │
│ 4 Toolset read-only allowlist for the role          │
│ 5 Claude  Messages API + MCP connector, vendor's    │
│           MCP token → mcp.okghumo.com               │
│ 6 Audit   log vendorId, tools, arg hashes (no PII)  │
└──────────────────────────────────────────────────────┘
   ▼ MCP server: re-checks user, mints 60s read tokens locked to that user's clientId
   ▼ Commerce/auth analysis GET routes (accept only that locked token)
```

## 4. Isolation layers (defense in depth)
| # | Layer | What it guarantees | Who enforces |
|---|---|---|---|
| L1 | Per-vendor identity | No shared service token anywhere. Token = the vendor's own login | Chat API + OkGhumo auth |
| L2 | clientId-locked tokens | MCP/commerce return only that client's rows, even if the model asks for another ID | OkGhumo servers (per team docs — **must be verified with two real vendors**) |
| L3 | Per-vendor state | Token cache, chat history, logs all keyed by vendorId; history is sent by the client but is text-only (no tool results can be forged) | Chat API |
| L4 | Read-only allowlist | Only the 9 read tools; `authenticate` never exposed; default-deny | `app/rbac.py` |
| L5 | Role gate | Only vendor/superVendor may use the vendor bot; admin/superAdmin refused until a separate admin bot exists | Chat API |
| L6 | Prompt rules | System prompt: answer only about the signed-in vendor, refuse requests about other vendors/IDs. NOT relied on for security | Model (best effort) |
| L7 | Tests | Two-vendor red-team suite in CI (below) | us |
| L8 | **Own tool loop (core, Phase 1)** | OUR code calls the MCP, not Anthropic's connector. By-ID calls (`bookingId`, `agentId`, `activityId`) are allowed only for IDs that appeared in THIS vendor's own earlier results (session allowlist); anything else is blocked before it reaches MCP. If rows carry a clientId, we also assert it matches | Chat API (**our guarantee, independent of OkGhumo servers**) |

Prompt injection assumed to succeed sometimes (booking names/notes are attacker-controlled text). That is
why L1–L2 hold even when the model is fully fooled: the token simply cannot read another client.

## 5. Tools → vendor questions
| Tool | Vendor asks | Cross-vendor risk if L2 failed | Notes |
|---|---|---|---|
| get_analysis_capabilities | "what can I filter by?" | low | lists *their* activities/agents/offices; also gives today's date/timezone |
| get_analysis_summary | "sales this month?" | high | totals |
| get_booking_source_breakdown | walk-in vs agent vs online | high | |
| get_booking_trends | day/week/month trend | high | |
| get_collection_summary | cash vs online, office split | high | |
| get_agent_outstandings | "who owes me?" | high | all-time balances |
| get_agent_ledger | one agent's statement | high | takes `agentId` — IDs are the main injection target |
| get_bookings | list bookings | high | takes `activityId/agentId`; PII omitted by server |
| get_booking_details | one booking | high | takes `bookingId` — guessable IDs must be rejected server-side |
Guess/forge-able inputs: `agentId`, `bookingId`, `activityId`. Test every one with another vendor's ID.

## 6. Verification plan (must pass before launch)
1. Two test vendors A and B with distinct data. As A, via chat: ask for B's bookingId / agentId / activityId; ask "show all vendors"; paste injection text in a booking note; ask for another phone number's data.
2. Expect: refusal or empty/not-found, never B's numbers. Assert on tool responses (not model wording).
3. Replay: A's MCP token + B's ID directly against `/mcp` → must be denied.
4. Cache test: A then B in the same process → B never gets A's token (`mcp_auth` cache must be per-vendor).
5. Expired/revoked token → 401/502, no fallback to any other token.
6. Role test: admin token on vendor bot → 403.
Automate 1–6 in CI against staging with fixture vendors.

## 7. Voice notes
```
voice note ─► validate (ogg/opus|mp3|m4a|wav, ≤ ~60s, ≤ ~5 MB)
           ─► STT (Hindi/Hinglish/English) ─► transcript
           ─► show "I heard: …" (vendor can edit/confirm) ─► same /chat pipeline as text
```
- **Voice = text.** Same auth, same tools, same isolation. The transcript is untrusted user text. Never identify a vendor by voice.
- **STT choice:** evidence favours **Sarvam (Saaras/Saarika)** for Hindi/Hinglish; Whisper/gpt-4o-transcribe as fallback; Deepgram better for English-heavy. Mostly vendor-published numbers → run a bake-off on ~50 real vendor voice notes before choosing. Put STT behind one function so it's swappable.
- **Confirm numbers/dates:** echo transcript; for money/date ranges show the interpreted range ("13 Oct–11 Nov") in the answer.
- **Privacy:** don't store audio after transcription; log transcript hash/length, not content.
- **Channels:** (a) in-app mic → `POST /voice` multipart; (b) WhatsApp Cloud API: webhook gives media id → fetch media URL → download with token (7-day expiry, ogg/opus, `voice` flag; duration not in webhook). WhatsApp sender number is NOT proof of vendor: bind number→vendor once via OTP, then treat as a session.

## 8. API surface (target)
- `POST /chat` — `{messages}` + `Authorization: Bearer <vendor OkGhumo token>`.
- `POST /voice` — multipart audio + same auth → `{transcript, reply, tools_used}`.
- `POST /whatsapp/webhook` (later) — verify Meta signature, map number→vendor, reuse the same core.
- `GET /health`.

## 9. Phases
0. **Per-vendor auth + own tool loop (L8)** (remove shared token; role from OkGhumo, not self-signed JWT; per-vendor cache; role gate).
1. **Isolation tests** with two real/staging vendors (section 6) — gate for everything else.
2. **Voice** via `/voice` + STT bake-off.
3. **WhatsApp** channel (+ number binding).
4. **Hardening:** rate limits/cost caps per vendor, audit dashboard, token refresh UX, streaming replies.

## 10. Open questions for the OkGhumo backend team / owner
1. The vendor app will pass us a token. Which token (dashboard session / a purpose-made one) and how can we validate it and read vendorId/clientId (exchange response or JWT claims)? The `authenticate` MCP tool also returns an MCP token but is tool-only.
2. Confirm in writing: MCP/delegate tokens return **only** the token-owner's `clientId` for every tool, including by-ID lookups (`bookingId`, `agentId`).
3. What is `superVendor` — multiple clients? Should it be allowed, and scoped how?
4. Languages needed (Hindi, Hinglish, Gujarati, Marathi…)? Voice channel first: in-app or WhatsApp?
5. Retention rules for chat history/transcripts?

## Sources
- OWASP/LLM guidance summarised: https://aembit.io/blog/the-owasp-top-10-for-llm-applications-2026-what-changed-and-why-it-matters/
- Anthropic MCP connector: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- STT comparisons: https://agent-rush.com/blog/speech-to-text-indian-languages-comparison-2026 · https://www.sarvam.ai/speech-to-text
- WhatsApp audio: https://bird.com/docs/guides/whatsapp/receiving-whatsapp/audio
