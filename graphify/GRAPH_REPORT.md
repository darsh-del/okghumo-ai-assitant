# Graph Report - Okghumo Chatbot  (2026-11-18)

## Corpus Check
- 12 files · ~4,070 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 80 nodes · 91 edges · 11 communities (10 shown, 1 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `12475b43`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- main.py
- Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)
- CLAUDE.md — Okghumo AI Assistant
- package.json
- test_chat.py
- main.js
- mcp_toolset
- Okghumo AI Assistant
- deploy.sh

## God Nodes (most connected - your core abstractions)
1. `Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)` - 12 edges
2. `CLAUDE.md — Okghumo AI Assistant` - 10 edges
3. `FakeClient` - 7 edges
4. `chat()` - 6 edges
5. `mcp_toolset()` - 6 edges
6. `call()` - 5 edges
7. `Okghumo AI Assistant` - 4 edges
8. `ChatIn` - 3 edges
9. `get_client()` - 3 edges
10. `mcp_token()` - 3 edges

## Surprising Connections (you probably didn't know these)
- `test_every_role_is_read_only()` --calls--> `mcp_toolset()`  [EXTRACTED]
  tests/test_chat.py → app/rbac.py
- `chat()` --calls--> `mcp_toolset()`  [EXTRACTED]
  app/main.py → app/rbac.py
- `chat()` --calls--> `mcp_token()`  [EXTRACTED]
  app/main.py → app/mcp_auth.py

## Import Cycles
- None detected.

## Communities (11 total, 1 thin omitted)

### Community 0 - "main.py"
Cohesion: 0.19
Nodes (13): Anthropic, chat(), ChatIn, current_user(), get_client(), health(), Msg, mcp_token() (+5 more)

### Community 1 - "Okghumo Vendor Chatbot — System Design (draft v1, 2026-11-11)"
Cohesion: 0.15
Nodes (12): 10. Open questions for the OkGhumo backend team / owner, 1. Blocker in the current code, 2. Core principle, 3. Architecture, 4. Isolation layers (defense in depth), 5. Tools → vendor questions, 6. Verification plan (must pass before launch), 7. Voice notes (+4 more)

### Community 2 - "CLAUDE.md — Okghumo AI Assistant"
Cohesion: 0.18
Nodes (10): Auth (verified 2026-11-11), CLAUDE.md — Okghumo AI Assistant, Commands, Git workflow & pushing (from GITHUB_PUSH_INSTRUCTIONS.md — read it before ANY push), 🔒 HARD RULE: the Okghumo MCP is READ-ONLY. Never write to it., Layout, MCP connector facts (verified against Anthropic docs, Nov 2026), Secrets (+2 more)

### Community 3 - "package.json"
Cohesion: 0.20
Nodes (9): devDependencies, vite, name, private, scripts, build, dev, type (+1 more)

### Community 4 - "test_chat.py"
Cohesion: 0.38
Nodes (6): call(), FakeClient, test_admin_is_still_read_only_allowlist(), test_mcp_token_sent_and_exchange_failure_is_502(), test_unknown_role_403_and_no_model_call(), test_viewer_gets_allowlist_only()

### Community 5 - "main.js"
Cohesion: 0.33
Nodes (4): jwt, log, messages, q

### Community 6 - "mcp_toolset"
Cohesion: 0.40
Nodes (4): mcp_toolset(), Role -> allowed Okghumo MCP tools. READ-ONLY BY DESIGN (see CLAUDE.md). Deny by…, Build the mcp_toolset for a role. Raises KeyError for unknown roles (deny by…, test_every_role_is_read_only()

### Community 7 - "Okghumo AI Assistant"
Cohesion: 0.40
Nodes (4): How it works, Okghumo AI Assistant, Run, TODO

## Knowledge Gaps
- **34 isolated node(s):** `deploy.sh script`, `jwt`, `log`, `q`, `messages` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `mcp_toolset()` connect `mcp_toolset` to `main.py`, `test_chat.py`?**
  _High betweenness centrality (0.017) - this node is a cross-community bridge._
- **What connects `deploy.sh script`, `jwt`, `log` to the rest of the system?**
  _34 weakly-connected nodes found - possible documentation gaps or missing edges._