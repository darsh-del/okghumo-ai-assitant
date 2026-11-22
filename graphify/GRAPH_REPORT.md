# Graph Report - Okghumo Chatbot  (2026-11-22)

## Corpus Check
- 31 files · ~21,444 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 376 nodes · 810 edges · 22 communities (21 shown, 1 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6ddf2354`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- main.py
- test_schedules.py
- test_whatsapp.py
- whatsapp.py
- tasks.py
- run
- main.js
- B. API contract for the app team
- Scheduling
- Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)
- Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)
- Scheduling — Status & Roadmap (2026-11-18)
- CLAUDE.md — Okghumo AI Assistant
- package.json
- WhatsApp assistant: production workflow (draft v1, 2026-11-20)
- Okghumo Assistant — Tester Guide
- Graphify — codebase knowledge graph
- Okghumo AI Assistant
- deploy.sh

## God Nodes (most connected - your core abstractions)
1. `run()` - 56 edges
2. `post()` - 26 edges
3. `proposed()` - 22 edges
4. `user()` - 21 edges
5. `chat()` - 21 edges
6. `get()` - 19 edges
7. `tick()` - 17 edges
8. `tasks_of()` - 17 edges
9. `Fake` - 16 edges
10. `bot()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `test_chat_without_credential_is_409_and_no_model_call()` --indirect_call--> `get_client()`  [INFERRED]
  tests/test_tasks.py → app/assistant.py
- `Fake` --uses--> `NoCredential`  [INFERRED]
  tests/test_schedules.py → app/credentials.py
- `test_missing_credential_no_model_call_and_friendly_notice()` --calls--> `NoCredential`  [EXTRACTED]
  tests/test_schedules.py → app/credentials.py
- `test_credentials_are_per_vendor_and_encrypted()` --calls--> `get()`  [EXTRACTED]
  tests/test_tasks.py → app/credentials.py
- `test_rejected_credential_not_stored()` --calls--> `get()`  [EXTRACTED]
  tests/test_tasks.py → app/credentials.py

## Import Cycles
- None detected.

## Communities (22 total, 1 thin omitted)

### Community 0 - "main.py"
Cohesion: 0.06
Nodes (50): Anthropic, ask(), _dump(), get_client(), The one place that calls the model. Shared by POST /chat, the WhatsApp worker…, KeyError if the role is unknown (deny by default). The caller supplies THIS…, current_user(), Verified claims. `sub` (vendor id) is mandatory: identity is never taken from a… (+42 more)

### Community 1 - "test_schedules.py"
Cohesion: 0.22
Nodes (34): bot(), chat(), Fake, propose(), proposed(), Chat-driven scheduling: the model only proposes; code creates after the…, Scripted model: each create() pops the next list of content blocks., Run the propose turn; returns the assistant reply to feed back as history. (+26 more)

### Community 2 - "test_whatsapp.py"
Cohesion: 0.11
Nodes (31): _due_task(), env(), FakeClient, post(), propose_via_wa(), fixture, said(), sch() (+23 more)

### Community 3 - "whatsapp.py"
Cohesion: 0.12
Nodes (33): get(), health(), inbox(), list_tasks(), allow(), _answer(), BadCode, deliver() (+25 more)

### Community 4 - "tasks.py"
Cohesion: 0.12
Nodes (30): connect(), This app's OWN sqlite DB (never Okghumo's): vendor credentials, reminder tasks,…, main(), datetime, Scheduler process: `python -m app.scheduler`. Run EXACTLY ONE instance (see…, tick(), intercept(), _norm() (+22 more)

### Community 5 - "run"
Cohesion: 0.13
Nodes (25): run(), mark_read(), Row, clean(), fire(), make_report(), fixture, test_missing_credential_no_model_call_and_friendly_notice() (+17 more)

### Community 6 - "main.js"
Cohesion: 0.15
Nodes (15): add(), empty(), ERRORS, flash(), jwt, log, messages, msg (+7 more)

### Community 7 - "B. API contract for the app team"
Cohesion: 0.12
Nodes (16): A. Recommendation: the app calls us directly with the vendor's own OkGhumo token, Already done and tested in this repo, Authentication, B. API contract for the app team, Build order, C. Questions the OkGhumo backend team must answer (these block the build), `/chat` response, D. How one vendor can never see another vendor's data (+8 more)

### Community 8 - "Scheduling"
Cohesion: 0.18
Nodes (8): _clean(), Per-request tool handler. `.tools`/`.call` plug into assistant.ask(extra=...)., Append the canonical confirmation (only code writes MARK) after stripping any…, Validate model-supplied input into a proposal payload; ValueError carries a…, Scheduling, parametrize, test_garbage_tool_input_and_unknown_tool(), test_invalid_proposals_rejected()

### Community 9 - "Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)"
Cohesion: 0.14
Nodes (13): 10. Open questions (owner / OkGhumo backend), 1. Is it possible? Yes — but one blocker decides the design, 2. What research says (sources at bottom), 3. Product behaviour, 4. Architecture, 5. API surface, 6. Security rules (extend CLAUDE.md before coding), 7. Cost sketch (+5 more)

### Community 10 - "Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)"
Cohesion: 0.15
Nodes (12): 10. Open questions for the OkGhumo backend team / owner, 1. Blocker in the current code, 2. Core principle, 3. Architecture, 4. Isolation layers (defense in depth), 5. Tools → vendor questions, 6. Verification plan (must pass before launch), 7. Voice notes (+4 more)

### Community 11 - "Scheduling — Status & Roadmap (2026-11-18)"
Cohesion: 0.17
Nodes (11): Known limits of what's built (be honest), Open questions (owner), Phase 0b — OkGhumo-issued delegated credential (blocker for Phase 2), Phase 2 — Scheduled reports (in-app), Phase 3 — Email delivery, Phase 4 — WhatsApp delivery, Phase 5 — Hardening, Scheduling — Status & Roadmap (2026-11-18) (+3 more)

### Community 12 - "CLAUDE.md — Okghumo AI Assistant"
Cohesion: 0.18
Nodes (10): Auth (verified 2026-11-11), CLAUDE.md — Okghumo AI Assistant, Commands, Git workflow & pushing (from GITHUB_PUSH_INSTRUCTIONS.md — read it before ANY push), 🔒 HARD RULE: the Okghumo MCP is READ-ONLY. Never write to it., Layout, MCP connector facts (verified against Anthropic docs, Nov 2026), Secrets (+2 more)

### Community 13 - "package.json"
Cohesion: 0.20
Nodes (9): devDependencies, vite, name, private, scripts, build, dev, type (+1 more)

### Community 14 - "WhatsApp assistant: production workflow (draft v1, 2026-11-20)"
Cohesion: 0.20
Nodes (9): 1. The idea in one paragraph, 2. Linking flow (do this once per vendor), 3. Everyday chat flow, 4. Production decisions (opinionated), 5. Failure cases to design for, 6. Build order, 7. What OkGhumo must provide, Sources (+1 more)

### Community 15 - "Okghumo Assistant — Tester Guide"
Cohesion: 0.22
Nodes (8): For the test organiser (delete before sending), Good to know, If you see a red message, Okghumo Assistant — Tester Guide, Steps, Things to try, What to tell us, What you need (you will be sent the first one)

### Community 16 - "Graphify — codebase knowledge graph"
Cohesion: 0.29
Nodes (6): Contents, Graphify — codebase knowledge graph, Notes, Query the graph, Refresh after code changes (no LLM, no API cost), Tool location

### Community 17 - "Okghumo AI Assistant"
Cohesion: 0.40
Nodes (4): How it works, Okghumo AI Assistant, Run, TODO

## Knowledge Gaps
- **92 isolated node(s):** `deploy.sh script`, `log`, `q`, `jwt`, `settings` (+87 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `run` to `main.py`, `test_schedules.py`, `test_whatsapp.py`, `whatsapp.py`, `tasks.py`, `Scheduling`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Why does `Scheduling` connect `Scheduling` to `main.py`, `test_schedules.py`, `tasks.py`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Why does `get()` connect `whatsapp.py` to `main.py`, `run`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `log`, `q` to the rest of the system?**
  _92 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06448087431693988 - nodes in this community are weakly interconnected._
- **Should `test_whatsapp.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11411411411411411 - nodes in this community are weakly interconnected._
- **Should `whatsapp.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12477718360071301 - nodes in this community are weakly interconnected._