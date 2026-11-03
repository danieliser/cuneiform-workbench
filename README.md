# Cuneiform Workbench (local query UI)

A local, read-only browser UI for exploring the [cuneiform-research](../cuneiform-research) backend. You can run motif queries, read occurrence evidence back to the original source bytes, walk the graph, look up ORACC words, and run allowlisted read-only templates against an isolated, disposable FalkorDB projection.

Open **http://127.0.0.1:8740/** after starting it.

## Start / stop

```sh
cd /Users/danieliser/Projects/Fun/cuneiform-ui
bin/cuneiform-ui pin-backend    # once: read-only git-archive export of the tested backend commit (63f4221)
bin/cuneiform-ui start          # isolated FalkorDB (+ relay), then the UI
bin/cuneiform-ui status         # PIDs, containers, backend source, runtime storage
bin/cuneiform-ui logs 100
bin/cuneiform-ui stop           # stop the UI and the containers (data kept)
bin/cuneiform-ui restart
bin/cuneiform-ui ui-start | ui-stop | falkordb-up | falkordb-down
bin/cuneiform-ui security-check # FalkorDB isolation checks (also run by `test`)
bin/cuneiform-ui test           # isolation checks + hermetic unit/HTTP tests
```

| What | URL / location | State |
|---|---|---|
| Workbench UI | http://127.0.0.1:8740/ (loopback; Host/Origin/Sec-Fetch checked) | available |
| FalkorDB server (Redis protocol) | `redis://127.0.0.1:26380`, via relay; ACL login required | available to the UI server; no anonymous access |
| FalkorDB Browser | http://127.0.0.1:23001/ | **intentionally disabled** (port closed) |
| Arbitrary Cypher console (`POST /api/falkordb/query`) | — | **intentionally disabled** (HTTP 403); allowlisted templates instead |
| Backend source | `~/.local/share/cuneiform-ui/backend-pin/<commit>/` (read-only export) | pinned |
| UI PID / log | `~/.local/share/cuneiform-ui/run/ui.pid`, `~/.local/share/cuneiform-ui/logs/ui.log` | |
| Graph exports (write-once, hashed) | `~/.local/share/cuneiform-ui/exports/<dataset>/<export-id>/` | |
| FalkorDB data / ACL / secrets | `…/falkordb-data`, `…/falkordb-conf/users.acl` (hashes only), `…/secrets/*.password` (0600) | |
| Containers | `cuneiform-ui-falkordb`, `cuneiform-ui-falkordb-relay`; networks `cuneiform-ui-internal` (internal), `cuneiform-ui-edge`; label `com.cuneiform-ui.owner=cuneiform-ui` | |

Environment overrides: `CUNEIFORM_SOURCE_REPO` (default: pinned export if present), `CUNEIFORM_BACKEND_REPO`, `CUNEIFORM_RESEARCH_ROOT`, `CUNEIFORM_PY`, `CUNEIFORM_UI_HOME`, `CUNEIFORM_UI_PORT`, `CUNEIFORM_FALKORDB_PORT`, `CUNEIFORM_UI_SHOW_DEMOS` (`auto` | `always` | `never`), `CUNEIFORM_UI_STABLE_S` (quiet period before a changed snapshot is verified; default 30). The bind address is fixed at `127.0.0.1`.

## What you can do

- **Query**: 1–5 token motifs with `?` for one represented position, over `atf` or `signs`, with the **source** or **strict** policy and the **index** or **graph** engine. Every result shows its count *with the population it describes* (for example `COHORT 300/20,896 eligible of 23,289` and the backend's scope note: counts within a rule-selected cohort are **not corpus frequencies**), graph/index parity, witness/artifact support, denominators and the dataset version it is bound to. The graph engine lists at most 100 matches; the pager says so and points to the index engine for the rest.
- **Occurrence evidence**: token properties and line context; the *parsed record field (canonical DB)* excerpt (display only); and the **raw-byte trace** from the backend's `query.trace`. For eBL cohorts the trace also re-reads the **original Zenodo parent file** and shows its receipt, pointer, record byte range and the token's absolute byte span (`verified` / `parent-raw-unavailable` / `parent-raw-not-requested`), separate from the cohort payload round trip.
- **Word lookup (ORACC)**: word → lemma → contextual sense, traced with `oracc.trace_word` back to the parent ZIP member. Sense glosses are lexical labels for one word; **they are not translations**. Both ORACC adapters are accepted. `oracc-cdl-2` (`snapshot:5e51403b…`) keeps each word's own sig, inst and payload pointer on its `IN_SENSE` / `LEMMATIZED_AS` links, and the word view shows them. `oracc-cdl-1` (`snapshot:edaab55c…`) stored only the first attesting instance's sig on the shared Sense row. The UI labels it that way and marks the snapshot **superseded for sense provenance** by the cdl-2 snapshot of the same ZIP and texts. It stays listed and served as historical evidence. Coverage shows the per-word sense-link counts.
- **Timing**: every dataset response carries `timing.end_to_end_ms` (the whole request on the server, including re-hashing every snapshot file and the ledger check) with an optional breakdown (stat checks, verify, open, query, backend trace). The query time alone is never shown as the latency.
- **Graph**: a bounded neighborhood; each relationship group shows an exact total and up to N neighbors; hidden neighbors collapse into aggregate nodes.
- **Coverage**: dataset version, publication status, inputs with separate *Source URL* and *Receipt* columns (parent object row for cohorts), the population the counts describe, canonical layers (zero data layers say *unavailable — not supplied*, never "none"), coverage per record, position states, graph shape, exclusions, projection registry.
- **FalkorDB**: **Export, rebuild & verify** writes content-addressed `nodes.jsonl`/`edges.jsonl` + `receipt.json` (bound to the dataset version), always rebuilds graph `cuneiform_<dataset>_<export>`, then reads every node and edge back and compares them with the export. **Verify** re-checks a loaded graph. **Read-only templates** (node/edge kinds, adjacent readings, reading-pair occurrences/count, artifact lines, alias claims, node neighbors, receipt) run as `GRAPH.RO_QUERY` with typed parameters and a 1–500 row limit, under a read-only ACL user.

## Data sources and honesty rules

- **Snapshots**: only `<research root>/snapshots/snapshot:<32 hex>` directories. Once its files have been quiet for 30 s, each is verified by the backend: `storage.verified(storage.store_path(config, id), ledger='require')` hashes every payload file and checks the **publication ledger** (formats 1 and 2). The UI does not parse the ledger itself; it reads it with the backend's `Ledger.scan` only to notice changes and to show its state. Each snapshot is described with `persisted.scope`. The dataset **version** is the manifest SHA-256; datasets are keyed by `(snapshot_id, manifest_sha256)`.
  - Served publication labels (the backend's): `ledgered`, `ledgered-recovered`, `adopted`, `ledgered-legacy-backfilled`, and `legacy-incident` (UI label for a backfilled ID that was deleted and rebuilt under the same ID before the ledger existed; current bytes verify but earlier results under that ID are not reproducible; shown as a banner).
  - Refusals keep the backend's `PublicationError.state`: `reserved` → **not yet available** (the backend's "no live writer" wording is not shown, because a reader cannot know that), `not-in-ledger` / `unledgered` → unpublished, `ledger-mismatch` / `conflict` / `ledger-corrupt` / `symlinked-entry` / `misplaced` → refused, `published-missing` / `abandoned` / `not-found` → unavailable. Ledger IDs without a directory (reserved, published-missing, abandoned, conflict) are listed with their state. A UI pre-check also refuses any symlink inside an entry and types a manifest naming another ID as `misplaced` before any hashing.
  - Ledger damage is never skipped silently: an unparseable record before the end refuses every snapshot (`ledger-corrupt`), and an unterminated final record is shown in the banner. As in the backend, a torn commit leaves that ID `reserved` (not served); other IDs stay served with the torn tail noted.
  - No DuckDB connection is kept open. Each request re-runs `storage.verified(..., ledger='require')` (every file hash plus the ledger; ~0.1 s at 174 MB), opens the file read-only, checks the file fingerprint after closing, and refuses the result if anything changed or the ledger now refuses it (HTTP 409). Changed snapshots are re-verified, failed ones retried with backoff, removed ones dropped (or listed as `published-missing` while the ledger still says published).
  - Every response carries the dataset key + version. The page sends the version back; a stale version gets **409** and the page clears its results with a notice. The page polls `/api/status` only; discovery runs on the server every 30 s (stat-only unless something changed) and on **Refresh & re-verify**.
- **Samples / synthetic**: the small retained files under `<research root>/evidence/` (parsed in memory, labeled *in-memory sample*) and the backend's fixtures (labeled *SYNTHETIC*). They are hidden when a real snapshot is ready (`CUNEIFORM_UI_SHOW_DEMOS=auto`).
- **Backend**: imported from the pinned read-only export with bytecode writes disabled. "tested" means the code is byte-identical to a tested commit (`63f4221`, independently accepted in the backend's `reviews/corpus-pilot-review-v0.3.md`): for an export, every file hash matches its pin record; for a git checkout, the `cuneiform/`, `fixtures/` and `pyproject.toml` trees equal the tested commit's and nothing under them is modified or untracked. The server re-checks this on every scan and shows a banner if the code changes after it started. Schema `2`, parser `conservative-3`, projection `linked-1` are required.

## Security boundaries

- Workbench server: Python standard library only, binds `127.0.0.1`, rejects unexpected `Host` (421; DNS rebinding), cross-origin `Origin` including `null` (403) and cross-site `Sec-Fetch-Site` (403, except top-level navigation to `/`). POST must be `application/json`, a JSON object, at most 64 KiB; malformed/deep JSON and more than 20 query fields return 400. Static files come from a fixed allow-list. IDs, dataset keys, versions and tokens are validated; SQL is fixed and parameterized. DuckDB work is interrupted after 10 s and capped at 512/256 MB with spill disabled (temp dir under the UI home). CSP `default-src 'none'; script-src 'self'`; the client builds the DOM with `textContent` only.
- FalkorDB: the server container is on an `--internal` Docker network (no egress, no external DNS, no published ports) with `BROWSER=0`, `no-new-privileges`, 1 GiB memory, `--restart on-failure:3`, `TIMEOUT_MAX 3000`, `QUERY_MEM_CAPACITY 256 MB`. A fixed-code relay (same pinned image, read-only rootfs, no capabilities, nobody user) publishes it on `127.0.0.1:26380`. Redis ACL: `default` off; `cuneiform-ro` = PING/GRAPH.RO_QUERY/GRAPH.LIST/MODULE LIST; `cuneiform-rw` adds GRAPH.QUERY/GRAPH.DELETE (no CONFIG, UDF, FLUSH). Passwords are generated locally into 0600 files and never printed; the mounted ACL file holds SHA-256 hashes only.
- Why no arbitrary Cypher: on FalkorDB 4.18.8, `GRAPH.RO_QUERY` accepts `LOAD CSV FROM 'https://…'` and opens an outbound TLS connection (observed against a local listener), and `UNWIND range(1, 2000000000)` OOM-kills the server even with `QUERY_MEM_CAPACITY` set (observed on a disposable probe container). Read-only mode, timeouts and container limits do not make arbitrary Cypher safe, so only the allowlisted templates run.
- Why no Browser: the bundled FalkorDB Browser logs in to any host:port the user types (from inside the container) and does not check the `Host` header (DNS rebinding). It stays off until it can sit behind a loopback Host-checking proxy and the network isolation above.

## Limits

- Cohort and selection snapshots are samples. Counts inside them are not corpus frequencies; the UI states the population on every count.
- Per-request verification re-hashes every snapshot file (~0.1 s for the 174 MB cohort). Snapshot verification is capped at 2 GiB.
- The graph engine materializes at most 100 matches; counts cover all of them; the index engine pages the rest.
- FalkorDB exports are capped at 150,000 nodes / 400,000 edges. Cypher literal serialization turns control characters other than `\n\r\t` into spaces; the JSONL export keeps exact values and the readback comparison applies the same normalization.
- Parent traces exist for eBL cohorts (Zenodo file) and ORACC words (ZIP member). CDLI selection snapshots have no parent object trace from the backend.
