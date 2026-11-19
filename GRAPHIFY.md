# Graphify — codebase knowledge graph

`graphify/` holds a generated graph of this repo (code + docs). Use it to find how pieces connect
without reading every file.

## Contents
- `GRAPH_REPORT.md` — summary: god nodes, communities, surprising connections. Start here.
- `graph.html` — interactive view; open in a browser.
- `../.graphifyignore` — excludes `graphify/` itself from scans (otherwise its cache gets indexed).
- `graph.json` — raw graph (nodes/edges) used by the CLI queries.
- `manifest.json`, `cache/` — incremental-update state; don't edit.

## Tool location
Graphify lives on the external drive, not on `PATH`:
```bash
/Volumes/ExternalSSD/tools/bin/graphify
```

## Refresh after code changes (no LLM, no API cost)
```bash
cd "/Volumes/ExternalSSD/Codebase/Okghumo Chatbot"
/Volumes/ExternalSSD/tools/bin/graphify update .
rm -rf graphify && mv graphify-out graphify
```
The tool always writes to `graphify-out/`; the `mv` keeps our folder name (`graphify/`).
Check staleness: the report's "Built from commit" vs `git rev-parse HEAD`.

## Query the graph
`--graph` is needed because the default path is `graphify-out/graph.json`.
```bash
G=/Volumes/ExternalSSD/tools/bin/graphify
$G explain "chat()" --graph graphify/graph.json
$G path "chat()" "mcp_toolset()" --graph graphify/graph.json
```

## Notes
- Only code/docs changes are picked up by `update`; images/papers need `/graphify --update` in an AI assistant.
- The graph is derived data and contains no secrets (`.env*` is gitignored and not scanned). Never commit `graphify-out/` alongside `graphify/`.
- Regenerate rather than hand-edit.
