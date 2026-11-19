# Graph Report - Okghumo Chatbot  (2026-11-19)

## Corpus Check
- 22 files · ~9,350 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 177 nodes · 278 edges · 17 communities (16 shown, 1 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 4 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `acd2e2de`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- tasks.py
- run
- main.py
- test_chat.py
- test_tasks.py
- Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)
- Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)
- Scheduling — Status & Roadmap (2026-11-18)
- CLAUDE.md — Okghumo AI Assistant
- package.json
- Graphify — codebase knowledge graph
- main.js
- Okghumo AI Assistant
- deploy.sh

## God Nodes (most connected - your core abstractions)
1. `run()` - 20 edges
2. `Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)` - 12 edges
3. `Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)` - 12 edges
4. `get()` - 11 edges
5. `h()` - 10 edges
6. `CLAUDE.md — Okghumo AI Assistant` - 10 edges
7. `Scheduling — Status & Roadmap (2026-11-18)` - 10 edges
8. `tick()` - 9 edges
9. `next_run()` - 8 edges
10. `create_task()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `test_rejected_credential_not_stored()` --calls--> `get()`  [EXTRACTED]
  tests/test_tasks.py → app/credentials.py
- `clean()` --calls--> `run()`  [EXTRACTED]
  tests/test_tasks.py → app/db.py
- `test_catch_up_fires_once_and_paused_never()` --calls--> `run()`  [EXTRACTED]
  tests/test_tasks.py → app/db.py
- `test_scheduler_fires_once_and_isolated_inbox()` --calls--> `run()`  [EXTRACTED]
  tests/test_tasks.py → app/db.py
- `test_chat_without_credential_is_409_and_no_model_call()` --indirect_call--> `get_client()`  [INFERRED]
  tests/test_tasks.py → app/main.py

## Import Cycles
- None detected.

## Communities (17 total, 1 thin omitted)

### Community 0 - "tasks.py"
Cohesion: 0.16
Nodes (24): connect(), This app's OWN sqlite DB (never Okghumo's): vendor credentials, reminder tasks,…, main(), datetime, Scheduler process: `python -m app.scheduler`. Run EXACTLY ONE instance (see…, tick(), create_task(), delete_task() (+16 more)

### Community 1 - "run"
Cohesion: 0.16
Nodes (20): delete(), _f(), get(), put(), Per-vendor OkGhumo credential store (Fernet-encrypted at rest; key in…, run(), delete_credential(), health() (+12 more)

### Community 2 - "main.py"
Cohesion: 0.21
Nodes (13): Anthropic, current_user(), Verified claims. `sub` (vendor id) is mandatory: identity is never taken from a…, NoCredential, chat(), ChatIn, CredentialIn, Msg (+5 more)

### Community 3 - "test_chat.py"
Cohesion: 0.23
Nodes (10): mcp_toolset(), Role -> allowed Okghumo MCP tools. READ-ONLY BY DESIGN (see CLAUDE.md). Deny by…, Build the mcp_toolset for a role. Raises KeyError for unknown roles (deny by…, call(), FakeClient, test_admin_is_still_read_only_allowlist(), test_every_role_is_read_only(), test_mcp_token_sent_and_exchange_failure_is_502() (+2 more)

### Community 4 - "test_tasks.py"
Cohesion: 0.24
Nodes (12): get_client(), fixture, clean(), h(), test_bad_schedules_rejected(), test_catch_up_fires_once_and_paused_never(), test_chat_without_credential_is_409_and_no_model_call(), test_rejected_credential_not_stored() (+4 more)

### Community 5 - "Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)"
Cohesion: 0.14
Nodes (13): 10. Open questions (owner / OkGhumo backend), 1. Is it possible? Yes — but one blocker decides the design, 2. What research says (sources at bottom), 3. Product behaviour, 4. Architecture, 5. API surface, 6. Security rules (extend CLAUDE.md before coding), 7. Cost sketch (+5 more)

### Community 6 - "Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)"
Cohesion: 0.15
Nodes (12): 10. Open questions for the OkGhumo backend team / owner, 1. Blocker in the current code, 2. Core principle, 3. Architecture, 4. Isolation layers (defense in depth), 5. Tools → vendor questions, 6. Verification plan (must pass before launch), 7. Voice notes (+4 more)

### Community 7 - "Scheduling — Status & Roadmap (2026-11-18)"
Cohesion: 0.17
Nodes (11): Known limits of what's built (be honest), Open questions (owner), Phase 0b — OkGhumo-issued delegated credential (blocker for Phase 2), Phase 2 — Scheduled reports (in-app), Phase 3 — Email delivery, Phase 4 — WhatsApp delivery, Phase 5 — Hardening, Scheduling — Status & Roadmap (2026-11-18) (+3 more)

### Community 8 - "CLAUDE.md — Okghumo AI Assistant"
Cohesion: 0.18
Nodes (10): Auth (verified 2026-11-11), CLAUDE.md — Okghumo AI Assistant, Commands, Git workflow & pushing (from GITHUB_PUSH_INSTRUCTIONS.md — read it before ANY push), 🔒 HARD RULE: the Okghumo MCP is READ-ONLY. Never write to it., Layout, MCP connector facts (verified against Anthropic docs, Nov 2026), Secrets (+2 more)

### Community 9 - "package.json"
Cohesion: 0.20
Nodes (9): devDependencies, vite, name, private, scripts, build, dev, type (+1 more)

### Community 10 - "Graphify — codebase knowledge graph"
Cohesion: 0.29
Nodes (6): Contents, Graphify — codebase knowledge graph, Notes, Query the graph, Refresh after code changes (no LLM, no API cost), Tool location

### Community 11 - "main.js"
Cohesion: 0.33
Nodes (4): jwt, log, messages, q

### Community 12 - "Okghumo AI Assistant"
Cohesion: 0.40
Nodes (4): How it works, Okghumo AI Assistant, Run, TODO

## Knowledge Gaps
- **59 isolated node(s):** `deploy.sh script`, `jwt`, `log`, `q`, `messages` (+54 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `run` to `tasks.py`, `test_tasks.py`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Why does `get()` connect `run` to `main.py`, `test_tasks.py`?**
  _High betweenness centrality (0.012) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `jwt`, `log` to the rest of the system?**
  _59 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Scheduled Prompts ("Tasks") — Implementation Plan (draft v1, 2026-11-18)` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._