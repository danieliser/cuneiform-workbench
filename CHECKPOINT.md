# Checkpoint: cuneiform-ui

2026-11-03. Sole implementation worker. Independent sibling repo, local git only, no remote. The parent owns integration and review.

## Running now

| Service | State | Address |
|---|---|---|
| Workbench UI (stdlib Python, research venv: Python 3.13.12, DuckDB 1.4.3) | running, PID in `~/.local/share/cuneiform-ui/run/ui.pid` | http://127.0.0.1:8740/ |
| FalkorDB 4.18.8 + Browser 2.0.5, container `cuneiform-ui-falkordb` | running, `--restart no`, `--memory 1g` | `redis://127.0.0.1:26380`, http://127.0.0.1:23001/ |

Image: `falkordb/falkordb@sha256:bbebe3dc3fd109f9a88a48fc97cd146bf4301410872d4dd5d4a755c2d71e37a7`. It was already cached locally, so nothing was pulled. The module reports `ver 41808`, which is 4.18.8. Bundled Browser `package.json` version: 2.0.5. Data is bind-mounted at `/var/lib/falkordb/data`, which is the path the official docs give for v4.10–4.22 (they warn against using `/data` there). Port publishing is `127.0.0.1` only. Existing CompanyKit FalkorDB, Browser and the other containers were inspected and left untouched. No runtime restart.

Lifecycle: `bin/cuneiform-ui start|stop|restart|status|logs|test`. `stop` keeps the container and data. The script refuses to adopt a same-named container that lacks the `com.cuneiform-ui.owner=cuneiform-ui` label.

## Backend integration

- Imports `cuneiform` read-only from `/Users/danieliser/Projects/Fun/cuneiform-research`, with bytecode writes disabled. It calls backend `ingest/Builder`, `validate_graph`, `projections.build/lookup/source_span/coverage/report/assert_fresh`, `query.motif/trace_rows`, and `storage.verify/read_bounded`. There is no copied matching, counting or parsing logic. UI-side SQL is fixed and parameterized, and only reads display properties from typed tables and views.
- Version gate: schema `2`, parser `conservative-3`, projection `linked-1`, plus the required API names. The tested commit is `2ab620b3`. During this session the corpus worker began uncommitted edits (adapters, storage, projections, query, cli; new persisted/corpus/bulk/acquire). The UI correctly reports **compatible-untested**, and all tests below ran against that live, dirty backend.
- Discovery: `<research_root>/snapshots/snapshot:<32 hex>/manifest.json`. That directory **does not exist yet**, because the corpus pilot is not persisted. Discovery runs at boot, on **Refresh data**, and every 30 s from the open page. Each candidate gets a manifest version pre-check, then backend `verify(path, sid)` (all file hashes; 2 GiB cap), then a `read_only=True` DuckDB connection, then `assert_fresh`. Samples and synthetic data hide automatically once a real snapshot verifies.
- Until then the UI serves three real retained samples from `evidence/`, parsed in memory and labeled *in-memory sample*, and the backend's synthetic fixtures, labeled *SYNTHETIC*. Input SHA-256s are matched to `source-receipts.json` where present. No corpus download or duplicate ingestion was done.

## Evidence

**Tests:** `bin/cuneiform-ui test` ran 9 tests in 6.7 s. **OK**, none skipped (FalkorDB was up). Coverage:

- boot and CSP;
- synthetic expected counts 6/11/0/strict 0/12 postings, with graph/index parity;
- trace round trip;
- aggregated neighborhood;
- missing layer reported as *unavailable*;
- Host/Origin/Sec-Fetch/content-type/413/405 guards;
- path traversal and bad IDs return 404/400;
- SQL/HTML-looking tokens are inert and return 0 matches;
- token and limit bounds;
- LIKE wildcard escaping;
- Cypher literal round trip for quotes, backslashes, newlines and an injection string;
- `RO_QUERY` refuses writes, foreign graph names are refused, and the loaded counts equal the receipt;
- **persisted-snapshot path**: the backend `import_source` writes a fixture snapshot into a scratch root under `~/.local/share/cuneiform-ui/test-scratch-root` (deleted afterwards). Discovery, verify, read-only open, query = 6 with parity, and trace with verified-manifest attribution all pass.

**Live browser (T3 preview, Chrome profile), not screenshots alone:**

1. The page loads the eBL README sample by default. Example `ABZ215 ABZ13` (signs) returns **3**, index 3 = graph 3 ✓. `DIŠ NA` (atf) returns **3**.
2. Clicking token `NA#` opens the evidence panel: line 7' context, raw excerpt with the highlighted span, and **✓ exact round trip UTF-8 [1857,1860) → "NA#"**. Also shown: raw SHA `f9486da5…`, locator `/fragments/0`, rights `unresolved-conflict`, and 9 translations labeled `unresolved-span`.
3. **Open in graph** centers on that occurrence (a race was found and fixed). Double-clicking line 7' recenters there: 8 occurrences, `+2 HAS_OCCURRENCE` aggregate, NEXT_LINE neighbors, and breadcrumbs.
4. Synthetic: searching `P000001`, then the eBL alias claim, gives catalog candidate `cdli:P000001`. Clicking it opens the CDLI artifact, which shows incoming eBL claims and no merge.
5. Missing data: the K.1 sample `A B` shows *no atf sequences (layer unavailable, not zero occurrences)*. Coverage marks composition/passage/word/lemma/sense as *unavailable (not supplied)*.
6. FalkorDB tab: Export & load gives synthetic 78/124 and README 682/1,446, equal to the backend report counts. The console query returned A→B 6 and B→C 6. `MATCH (n) DETACH DELETE n` was refused by FalkorDB as read-only.
7. **Official FalkorDB Browser** at :23001: logged in to `localhost:6379` and showed FalkorDB v4.18.08. Selected `cuneiform_synthetic_fixtures_9d6defe39cad`. It rendered 79 nodes (78 plus the receipt) and 124 edges, with the cuneiform labels and relationship types.

Exports are content-addressed (same input means the same export ID, which is reused). Files are chmod 0444 and accompanied by a receipt with file SHA-256s, source bindings, backend commit/versions and the projection registry. Graph names are `cuneiform_<dataset>_<export12>`.

## Storage and resources

UI runtime `~/.local/share/cuneiform-ui` is **1.0 MiB** (exports 752 KiB, FalkorDB data 236 KiB). The repo is 264 KiB. The container writable layer is 2.9 MB, using about 90 MiB of RAM against a 1 GiB limit. The UI process RSS is about 185 MiB. No new images, packages or venv. Free space 163 GiB.

## Limits / integration dependencies (for the parent)

1. **Real corpus not yet visible.** It appears once the corpus worker persists schema-2/linked-1 snapshots under `<root>/snapshots/`. If the pilot changes the layout, manifest versions or the `verify(path, sid)` signature, update `SUPPORTED`/`TESTED_COMMITS` in `server/bridge.py` and rerun `bin/cuneiform-ui test`. A server restart is needed to pick up backend code changes. Refresh is enough for new snapshots.
2. The backend now has `persisted.export_graph` (Parquet nodes/edges under `<root>/derived/exports`). A future Q-009 could have the FalkorDB loader consume that canonical export instead of the UI's JSONL export.
3. `projections.lookup` re-hashes all canonical tables on every call through `assert_fresh`. That's fine now, but slow at corpus scale. A cached freshness check belongs in the backend.
4. FalkorDB Browser fetches Monaco from `cdn.jsdelivr.net` (editor code only). Its first-login tour creates and then deletes `social-demo*` graphs in this isolated instance.
5. Cypher literal serialization turns control characters other than `\n\r\t` into spaces. The JSONL export is exact.
6. Graph-engine matches are capped at 100 inside the backend. Counts are complete.
