# Cuneiform Workbench (local query UI)

A local, read-only browser UI for exploring the [cuneiform-research](../cuneiform-research) backend. You can run motif queries, read occurrence evidence, walk the graph, and query an isolated, disposable FalkorDB projection with the official FalkorDB Browser.

Open **http://127.0.0.1:8740/** after starting it.

## Start / stop

```sh
cd /Users/danieliser/Projects/Fun/cuneiform-ui
bin/cuneiform-ui start      # isolated FalkorDB container, then the UI
bin/cuneiform-ui status     # PIDs, container state, URLs, runtime storage
bin/cuneiform-ui logs 100   # tail the UI log
bin/cuneiform-ui stop       # stop the UI and the container (data kept)
bin/cuneiform-ui restart
bin/cuneiform-ui ui-start | ui-stop | falkordb-up | falkordb-down
bin/cuneiform-ui test       # smoke + boundary tests against the real backend
```

| What | URL / location |
|---|---|
| Workbench UI | http://127.0.0.1:8740/ (loopback only; Host/Origin checked) |
| FalkorDB Browser (official, bundled) | http://127.0.0.1:23001/ — log in with host `localhost`, port `6379`, no user/password |
| FalkorDB server (Redis protocol) | `redis://127.0.0.1:26380` |
| UI PID file / log | `~/.local/share/cuneiform-ui/run/ui.pid`, `~/.local/share/cuneiform-ui/logs/ui.log` |
| Graph exports (write-once, hashed) | `~/.local/share/cuneiform-ui/exports/<dataset>/<export-id>/` |
| FalkorDB data volume | `~/.local/share/cuneiform-ui/falkordb-data` (bind mount) |
| Container | `cuneiform-ui-falkordb`, label `com.cuneiform-ui.owner=cuneiform-ui` |

Environment overrides: `CUNEIFORM_SOURCE_REPO`, `CUNEIFORM_RESEARCH_ROOT`, `CUNEIFORM_PY`, `CUNEIFORM_UI_HOME`, `CUNEIFORM_UI_PORT`, `CUNEIFORM_FALKORDB_PORT`, `CUNEIFORM_FALKORDB_BROWSER_PORT`, `CUNEIFORM_UI_SHOW_DEMOS` (`auto` | `always` | `never`). The bind address is fixed at `127.0.0.1`.

## What you can do

- **Query**: 1–5 token motifs with `?` for one represented position. You can choose the `atf` or `signs` field, the **source** or **strict** evidence policy, and the **index** engine (paged n-gram projection) or **graph** engine (NEXT-edge walk, 0–2 skipped positions). It shows built-in example queries per dataset with expected counts, queries you save in this browser, and frequent readings and bigrams. Each result shows its count, graph/index parity, witness/artifact support, represented-position denominators and evidence notes.
- **Occurrence detail**: click a token to see its reading, kind, preservation and certainty, and the whole line in context. The **raw-source evidence** panel shows the exact UTF-8 span, verified by the backend's `trace_rows` round trip, plus the raw SHA-256, the record locator and the rights status. Translation strings are placed in the sequence and labeled *unresolved span, not word alignment*. You also see reverse postings and n-gram windows, and canonical IDs that click through to the graph.
- **Graph**: a bounded neighborhood around any node ID: up to N neighbors per relationship type and direction. Hidden neighbors are collapsed into dashed *aggregate* nodes, so large fan-outs don't turn into hairballs. Click a node to inspect it, double-click or press **Focus** to recenter, or press **Expand here** to merge another neighborhood into the view (capped at 150 nodes). You can search by artifact key, sign label, source key, alias target or exact ID. Alias claims show exact catalog-key *candidates* without merging identities.
- **Coverage**: dataset authority and input hashes or receipts, canonical layer counts (missing layers say *unavailable*, not zero), translation and linguistic coverage per source record, position states (which explain why **strict** is empty for eBL), node and edge kinds, exclusions and rejections, and the projection registry.
- **FalkorDB**: **Export & load** writes content-addressed `nodes.jsonl`/`edges.jsonl` plus `receipt.json` (SHA-256s, source bindings, backend commit and versions, projection registry), then builds graph `cuneiform_<dataset>_<export>`. The same input gives the same export ID, so the graph is reused. A read-only Cypher console (`GRAPH.RO_QUERY`, 3 s timeout, 500 rows) and the official FalkorDB Browser give arbitrary Cypher over this **disposable projection only**.

## Data sources and honesty rules

- **Verified snapshots** come from `~/.local/share/cuneiform-research/snapshots/snapshot:<hex>/`. The UI scans for them at startup, when you press **Refresh data**, and every 30 s while the page is open. Each one is checked with the backend's own `storage.verify` (manifest plus every file hash), opened with DuckDB `read_only=True`, and freshness-checked with `projections.assert_fresh`. Snapshots are never written.
- **Samples** come from the small real files already retained by the research worker under `~/.local/share/cuneiform-research/evidence/`. They are parsed **in memory** by the backend's adapters and labeled *in-memory sample, not a verified snapshot*. **Synthetic** means the backend's own test fixtures, labeled *SYNTHETIC*. Samples and synthetic data are hidden automatically once a real snapshot verifies (`CUNEIFORM_UI_SHOW_DEMOS=auto`).
- The backend is imported read-only from `CUNEIFORM_SOURCE_REPO`, with bytecode writes disabled. Matching, counting, tracing, coverage and projections all call backend functions; the UI does not re-implement them. The UI checks schema `2`, parser `conservative-3` and projection `linked-1` plus the required API functions. The tested commit is `2ab620b`. A different or dirty commit with matching versions is shown as **compatible-untested** in a banner. Mismatched versions are refused.
- Only loopback is used. No corpus data leaves the machine. Exceptions are noted under Limits.

## Security boundaries

The server uses only the Python standard library, binds `127.0.0.1`, and rejects unexpected `Host` (DNS rebinding), cross-origin `Origin` and cross-site `Sec-Fetch-Site`. POST must be `application/json` with a body of at most 64 KiB. Static files come from a fixed allow-list, so URLs never map to filesystem paths. IDs, dataset keys and tokens are validated with regexes, and all SQL is parameterized. DuckDB work is interrupted after 10 s, and list results are capped at 100 per page. CSP is `default-src 'none'; script-src 'self'` with no inline script. The client builds the DOM with `textContent` only, never `innerHTML`, and corpus text is treated as untrusted. Cypher parameters are serialized as escaped literals, and labels and relationship types come from allow-lists. The console uses `GRAPH.RO_QUERY` on `cuneiform_*` graphs only. The container publishes only to `127.0.0.1`.

## Limits

- **FalkorDB Browser loads its Monaco editor from `cdn.jsdelivr.net`.** That's an outbound request for editor code made by your browser. Queries and corpus data stay on the local container. The Workbench UI itself makes no external requests.
- On its first login, the Browser's tour creates and then deletes `social-demo*` graphs in the isolated instance. They are not cuneiform data and are never shown in the Workbench.
- When a Cypher literal is serialized for FalkorDB, control characters other than `\n \r \t` become spaces. The canonical data is unaffected; the export JSONL keeps exact values.
- Each request re-runs the backend's `assert_fresh` canonical hash. That's fine at current sizes, but it will get slow on large snapshots (an integration item for the backend).
- The graph engine materializes at most 100 matches inside the backend. The count still covers everything.
- Real snapshot verification hashes every file on discovery and is capped at 2 GiB per snapshot.
