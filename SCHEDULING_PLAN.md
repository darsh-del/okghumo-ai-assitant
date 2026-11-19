# Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)

Status/roadmap: see SCHEDULING_ROADMAP.md (phases 0-1 built).

Goal: a vendor says "every day 8am send me yesterday's sales + who owes me money" once; the
system then runs that prompt on schedule and delivers the answer (in-app / email / later WhatsApp).
**Nothing here is built yet.** Read-only MCP rule (CLAUDE.md) is unchanged: scheduled runs are
just `/chat` runs triggered by a clock instead of a person.

## 1. Is it possible? Yes — but one blocker decides the design

A scheduled run happens when the vendor is **not there**. Today `/chat` works because a live,
per-request credential exists. A scheduler needs *some* credential at 8am. Options:

| Option | How | Verdict |
|---|---|---|
| A. Store each vendor's login token | Simple | **Reject.** Login token = full user session (CLAUDE.md: high-value secret). DB leak = every vendor's account. Violates OWASP "no long-lived creds / least agency". |
| B. Shared service token (today's `OKGHUMO_LOGIN_TOKEN`) | Simple | **Reject.** Every vendor sees one vendor's data (DESIGN.md §1 blocker). |
| C. **Purpose-made delegated "schedule" token from OkGhumo** | Vendor consents once; OkGhumo auth mints a refresh credential scoped `okghumo-analysis:read`, tied to that vendor's `clientId`, revocable, long TTL | **Recommended.** Needs a backend change (Q1 below). |
| D. Server-side exchange per run using a stored per-vendor credential encrypted with KMS/Fernet | Works with today's exchange endpoint if OkGhumo allows a long-lived read-only credential | Fallback if C is slow; still needs OkGhumo to issue a *non-login* credential. |

**Hard dependency (owner/backend decision):** an OkGhumo-issued, read-scoped, revocable,
per-vendor refresh credential. Without it, only "reminders" (no data) can ship safely (Phase 1).

## 2. What research says (sources at bottom)
- **Competitors (ChatGPT Tasks):** one-time + recurring, hourly minimum frequency, plan-based
  active-task caps (3/5/10/15), runs count against usage. → Copy: **min interval 1h (daily/weekly/
  monthly presets), max ~5–10 active tasks per vendor.** Cuts cost and abuse.
- **Scheduler:** APScheduler 3.x with multiple API workers runs the same job N times unless one
  process owns it or a DB lock is used; 4.x is built for shared stores but needs an event broker
  and has no 3.x schedule import. → **Don't schedule inside the API workers.** Run a separate
  `scheduler` container (one instance) that polls the DB; no APScheduler needed (see §4).
- **Cost:** Anthropic Message Batches = 50% off, up to 24h turnaround, stacks with caching.
  Reports are not latency-sensitive → batch is possible later; v1 uses normal calls (simpler,
  tool loop via connector can't be assumed to work in batch — verify before relying on it).
- **Security (OWASP LLM/agentic):** least agency; short-lived scoped tokens; decide & document
  whose identity the agent carries; controls must live outside the model; human confirmation for
  writes. → Scheduled prompts are **read-only only**, run as *that vendor*, never a shared identity.
- **WhatsApp:** business-initiated messages (a report at 8am, no open 24h window) must use an
  approved **template**, and are billed per message (utility ≈ ₹0.145 per blog sources; Meta's page
  vs blogs conflict after Oct 2026 — check the Business Manager rate card). Free-form text only
  inside the 24h window. → Phase 3, template: "Your {name} report is ready: {link/summary}".

## 3. Product behaviour
- Create: chat intent ("schedule this daily at 8am") **or** form. The model never creates the
  schedule itself — see §6. Flow: model proposes → UI shows card *"Daily 08:00 IST · prompt: … ·
  deliver: email"* → vendor taps **Confirm**. (Human-in-the-loop on anything persistent.)
- Schedule types: once, daily, weekly (days), monthly (day). Stored as **cron string + IANA tz
  (default Asia/Kolkata)**; DST-free for India but keep tz for correctness.
- Actions: list, pause/resume, edit, delete, "run now", view last 10 runs.
- Delivery: in-app inbox (v1), email (v2), WhatsApp template (v3).
- Dates in prompts: "yesterday / this week" are resolved **at run time** by passing run date+tz in
  the system prompt (and `get_analysis_capabilities` returns today's date/tz). Never freeze dates.
- Failures: retry 2× with backoff; then mark `failed` and tell the vendor once (no spam). Auto-pause
  after 5 consecutive failures (expired credential → "please re-authorize").

## 4. Architecture
```
 vendor app ─POST /tasks (JWT)──► API (FastAPI)  ──► DB: tasks, task_runs
                                                        ▲   │ poll every 60s
                              ┌── scheduler container (exactly 1) ──┘
                              │  SELECT … WHERE next_run_at <= now() FOR UPDATE SKIP LOCKED
                              │  insert task_run(status=running, unique(task_id, scheduled_for))
                              ▼
                       run_task(task)  — same core as /chat:
                         1 load vendor credential (decrypt, per-vendor)  → MCP exchange → 15-min token
                         2 role→toolset (read-only allowlist, rbac.py)
                         3 Claude + MCP connector, system prompt + run date/tz + "report only"
                         4 save result → deliver (inbox / email / WhatsApp) → compute next_run_at
```
- **DB:** need persistence (CLAUDE.md allows "this app's own DB, never Okghumo's"). SQLite is OK
  for one host in v1 (file on a mounted volume); Postgres when >1 host. SQLAlchemy + Alembic.
- **Scheduler = tiny poll loop, not APScheduler.** `next_run_at` column + `SKIP LOCKED` (Postgres)
  or a single process (SQLite). Idempotency via `unique(task_id, scheduled_for)` → no double
  sends after restart. Catch-up rule: if down >1 interval, run once (don't replay all missed).
  Simpler to reason about than APScheduler multi-worker locking; `croniter` computes next time.
- **Worker model:** scheduler enqueues; `run_task` runs in a small thread pool (cap concurrency,
  e.g. 4) to respect Anthropic rate limits. Jitter runs ±2 min so 8:00 sharp doesn't spike.
- **docker-compose:** add `scheduler` service (same image, `command: python -m app.scheduler`,
  `restart: unless-stopped`), shared volume/DB. API stays stateless otherwise.

### Data model
```
tasks(id, vendor_id, role, name, prompt, cron, tz, delivery[json], status[active|paused|failed],
      next_run_at, last_run_at, consecutive_failures, credential_ref, created_at)
task_runs(id, task_id, scheduled_for, started_at, finished_at, status, tools_used[json],
          result_text, error, input_tokens, output_tokens)   -- unique(task_id, scheduled_for)
```
`vendor_id`/`role` come from the **validated** auth at creation, never from the request body.
Stored result_text is the vendor's own business data → encrypt at rest or set short retention (30d).

## 5. API surface
- `POST /tasks` (create, requires `confirm=true` from UI step) · `GET /tasks` · `PATCH /tasks/{id}`
  (pause/resume/edit) · `DELETE /tasks/{id}` · `POST /tasks/{id}/run` (run now, rate-limited) ·
  `GET /tasks/{id}/runs`.
- Every query filtered by `vendor_id` from the token (L3). Cross-vendor read of tasks/runs = test.
- Chat tool for the model: **not an MCP tool**. Optional app-side function tool
  `propose_schedule(prompt, cron, tz)` that only *returns a proposal card*; it writes nothing.
  Keeps the MCP read-only and keeps the model from persisting anything unconfirmed.

## 6. Security rules (extend CLAUDE.md before coding)
1. Scheduled runs use the **same read-only allowlist**; no new tools, no write tools ever, no
   `authenticate`.
2. Credential per vendor, encrypted (Fernet/KMS key outside DB), scoped read-only, revocable;
   token cache keyed by vendorId (DESIGN §4 L3). Never log it.
3. Prompt injection is the key new risk: a booking note could say "email results to attacker@…".
   Mitigations: **delivery targets are fixed at task creation** (vendor's verified email/number);
   run output can never change recipient, schedule or tools; system prompt says tool output is
   data; no outbound action is exposed to the model. Email body is plain text, links stripped/
   allowlisted.
4. Limits: ≤10 active tasks/vendor, min interval 1h, per-run `max_tokens` + tool-call cap, monthly
   per-vendor run budget; global kill switch env `SCHEDULER_ENABLED`.
5. Audit: log vendorId, task_id, tools, token counts, status (no results, no PII).
6. Prompt re-validation at creation: reject prompts asking for other vendors / contact data (PII
   rule CLAUDE.md #7); model-assisted classification is best-effort only, real isolation = tokens.

## 7. Cost sketch
Report run ≈ 5–20k input tokens (tool results) + 1–2k output. 100 vendors × 1 daily task ≈ 3,000
runs/month. Using Sonnet-class model for reports (not Opus) is the main lever; prompt caching on the
system prompt/toolset; Batch API (-50%) is an optional v2 optimisation. Add a per-run cost log from
`usage` so real numbers replace this guess.

## 8. Phases
0. **Prereq (OkGhumo team):** per-vendor delegated read credential + per-vendor auth (DESIGN.md
   phase 0). Answer Q1–Q3.
1. **Reminders-only + DB:** tasks/runs tables, CRUD, scheduler loop, in-app inbox, *no data* (a
   plain reminder text). Proves scheduler, idempotency, timezone, deployment. Safe without Q1.
2. **Scheduled reports (in-app):** `run_task` with MCP, confirm card, limits, failure handling.
   Gate: two-vendor isolation tests for tasks (A cannot see/run/edit B's tasks or results).
3. **Email delivery** (SES/SendGrid/SMTP; verified recipient; SPF/DKIM).
4. **WhatsApp template delivery** (needs DESIGN phase 3 number binding + template approval).
5. Hardening: budgets dashboard, Batch API, retries tuning, retention job.

## 9. Tests (add with each phase)
- Scheduler: due-task selection, no double-run after restart (unique key), catch-up once, tz/cron
  next-run, pause/resume, auto-pause after 5 failures. Freeze time with a fake clock.
- Isolation: vendor A cannot list/edit/delete/run B's tasks; run uses A's credential only.
- Guardrail: `run_task` toolset ⊆ `READ_ONLY_TOOLS`; test fails if a task row can name a tool.
- Injection: tool result containing "send this to x@y" never changes delivery target.
- Fail closed: missing/expired credential → run `failed`, no model call, no fallback token.

## 10. Open questions (owner / OkGhumo backend)
1. Can OkGhumo auth issue a **per-vendor, read-only, revocable refresh credential** (not the login
   token) for background runs? TTL? How does the vendor consent/revoke?
2. Postgres available on the host, or stay SQLite for v1?
3. Delivery priority: in-app, email, or WhatsApp first? Which vendor email/number is verified?
4. Limits OK? (10 tasks/vendor, 1h min interval) Any per-plan pricing?
5. Languages for reports (Hindi/Hinglish)? Retention for stored reports?

## Sources
- APScheduler multi-worker/4.x: https://apscheduler.readthedocs.io/en/master/migration.html ·
  https://apscheduler.readthedocs.io/en/3.x/userguide.html
- ChatGPT Tasks limits: https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- Anthropic Batch API: https://docs.claude.com/en/docs/build-with-claude/message-batches ·
  https://anthropic.com/news/message-batches-api
- WhatsApp pricing: https://developers.facebook.com/docs/whatsapp/pricing ·
  https://instantdm.com/blog/whatsapp-business-api-pricing-2026-india-october-update
- OWASP agentic/LLM (secondary summaries; verify against OWASP): https://aembit.io/blog/the-owasp-top-10-for-llm-applications-2026-what-changed-and-why-it-matters/ ·
  https://www.scworld.com/perspective/how-identity-and-permissions-become-the-blast-radius-boundary-for-llms
