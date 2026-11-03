# cuneiform-ui review v0.1

Reviewer: independent review sub-agent. Date: 2026-11-03, 04:45–04:55 EST.
Scope: code review, live API/browser checks, isolation checks. No implementation edits; this file is the only repo change.

## Observed versions (claims are limited to these)

| Item | Observed |
|---|---|
| UI repo | `567ea14` (clean except untracked `.tmp/`, see F-19) |
| Live UI | pid 41114, started 04:34:35, `127.0.0.1:8740` |
| Backend | `cuneiform-research` HEAD `2ab620b3`, **dirty**. 7 tracked files modified; `git diff` sha256 `f3b3c4f5…40c9`. New untracked modules: `acquire`, `bulk`, `corpus`, `oracc`, `persisted`. UI reports `compatible-untested`. |
| Versions | schema `2`, parser `conservative-3`, projection `linked-1`, Python 3.13.12, DuckDB 1.4.3 |
| FalkorDB | 4.18.8, image `sha256:91242f82…` (run from digest `bbebe3dc…`), Browser bundled (`BROWSER=1`), ports `127.0.0.1:26380`/`:23001`, network `bridge`, bind mount `~/.local/share/cuneiform-ui/falkordb-data` |
| Real snapshots | 2 at 04:44, then 5 by 04:53. They are `ebl-cohort` snapshots, and the corpus worker was **rewriting them in place under the same ID** while I watched (see F-4). The live UI had **not** discovered any of them; it still showed samples and synthetic data only. |

Real-data checks (marked **[copy]**) ran against a byte-identical copy of `snapshot:ce127437…` (sha256 of `graph.duckdb` matched the canonical file at copy time). The copy is in my workspace, and I used the UI's own `Backend`/`Explorer`/`Falkor` classes on it. These checks are a **preliminary integration signal only**. They are not acceptance of corpus integration, which stays a separate task after the corpus worker finishes.

## Summary

The core read path is sound on the data I could test:

- Index/graph parity held.
- Built-in and auto-generated example counts matched.
- Single-token counts matched the backend lookup.
- The raw round trip verified.
- Export counts matched the backend report: synthetic 78/124, README sample 682/1,446, and **[copy]** 11,887/27,417.
- The HTTP guards held.
- `RO_QUERY` refused writes and write procedures.

The serious problems are in four areas:

- **Isolation of the bundled FalkorDB Browser** (F-1).
- **Real cohort snapshots look like a corpus.** The UI shows them as plain verified snapshots, which hides that they are rule-selected samples (F-2, F-3).
- **Discovery lifecycle against snapshots that are still changing** (F-4, F-5).
- **Two crashes I could reproduce.** The Graph view fails on the default sample (F-6). One console query kills the FalkorDB container (F-7).

## Findings (most severe first)

### F-1 · High · Graph isolation: the bundled FalkorDB Browser can reach the shared CompanyKit FalkorDB and has no Host check

- **Evidence**
  - From inside `cuneiform-ui-falkordb`, `host.docker.internal:16379` and `192.168.215.1:16379` accept TCP connections (`TCP_OPEN_16379`). That port is `companykit-falkordb`, which is published on `0.0.0.0:16379`.
  - The Browser login form takes any host and port. Its Next.js server runs inside this container, so "log in to host.docker.internal:16379" connects to the shared graph.
  - `curl -H 'Host: evil.example' http://127.0.0.1:23001/` returns **200**, and so do `/api/auth/providers` and `/api/auth/csrf`. The Browser does not validate the Host header, so a DNS-rebinding page could drive its credential login and query API.
  - The isolated FalkorDB has no `requirepass`.
  - I did **not** log in or send any command to the CompanyKit service. I only made a TCP connect.
- **Where**: `bin/cuneiform-ui:55-63` (`BROWSER` left on, `-p 127.0.0.1:$BROWSER_PORT:3000`, default bridge network), `README.md:23,38,49`.
- **Risk**: The README's "disposable projection only" and "isolated" claims fail. A user typing a different host, or a rebinding attacker, can read and write the shared CompanyKit graph with this container's network access.
- **Fix**:
  - Default to `-e BROWSER=0` and do not publish 23001. Make the Browser opt-in (`falkordb-browser-up`) with a warning.
  - If it stays on, put it behind a loopback proxy that checks the Host header.
  - Set `requirepass` on the isolated instance.
  - Run the container on a dedicated user network.
  - Parent/CompanyKit, separately: bind `16379` and `13001` to `127.0.0.1` and require auth. That is outside this repo.
- **Test**: Add a lifecycle check to `bin/cuneiform-ui test`. It should assert that `curl -H 'Host: evil.example' :23001` is refused or not listening, and that `docker exec … bash -c 'echo >/dev/tcp/host.docker.internal/16379'` fails.

### F-2 · High · Scope/sampling: cohort snapshots are labeled as plain verified snapshots, and counts read as corpus counts **[copy]**

- **Evidence**
  - `snapshot:ce127437…` has `source: ebl-cohort` and `cohort.size = 30`, drawn from `eligible_records = 20,896` of `parent_records = 23,289`.
  - The selection rule is `usable-text-v1`: at least 10 eligible positions, at least 2 eligible pairs, records ordered by sha256 hash.
  - The UI shows:
    - title `ebl · ebl-cohort · ce127437339`;
    - description = the parent Zenodo `fragments.json` URL;
    - badge `verified snapshot`;
    - authority "Verified immutable snapshot…";
    - query summary "9 matching windows".
  - Nothing in the dataset picker, the query summary or Coverage says that this is a 30-record selection.
  - `_manifest_summary` drops `cohort`, `ingestion` and `source_locator_contract`. The **[copy]** check shows the `manifest_summary_keys` list without them.
  - The Coverage "Receipt" column shows `source_url`, which is the parent URL, not a receipt.
- **Where**: `server/bridge.py:73-76` (summary keys), `:249-250, 265-268` (title, description, inputs), `web/app.js:165-188` (summary), `:463-466` (Coverage).
- **Risk**: The integration is about to show real numbers. Users will read "ABZ411 ABZ411 = 152" as a corpus frequency rather than a count inside 30 hash-ordered records that were filtered for usable text. The UI's honesty rules require this scope to be shown.
- **Fix**:
  - Add `cohort` (size, eligible, parent records, rule, selector, provenance note, parent sha/url/receipt) to `_manifest_summary`.
  - Label the dataset `[SNAPSHOT · COHORT 30/20,896]`.
  - Add a `scope_note` to every query response, for example: "counts are within a 30-record rule-selected cohort; not corpus frequencies".
  - Rename the column to "Source URL", and show `parent.receipt_id` as the receipt.
  - Handle `oracc-selection` the same way.
- **Test**: Extend `tests/test_snapshot_path.py` with a cohort fixture (the backend can build one in the scratch root). Assert that `listing()` carries the cohort summary and that `query()` returns `scope_note`.

### F-3 · High · Provenance: the cohort trace skips the backend's parent trace; "raw source" is the derived cohort payload **[copy]**

- **Evidence**: The UI calls `trace_rows(db, raw, manifest, oid, verified_manifest=…)` directly. On the copy it returns no `parent_trace`. The backend's own `query.trace(path, oid)` returns `parent_trace` for the same occurrence: parent sha `4e970d87…`, receipt `receipt:66b5c944…`, parent pointer `/13637`, byte range `[53416847, 53421686)`. It also re-runs `verify`. The UI then shows "✓ Exact round-trip … in the raw source" for the cohort payload. The cohort's own `provenance` field says "parent bytes are authoritative".
- **Where**: `server/explore.py:291-305`. The bridge also stores `raws` only for the payload: `bridge.py:266`.
- **Risk**: The source-versus-derived distinction is lost exactly where the backend made it explicit. A user cannot tell a payload round trip from a verified parent-object round trip.
- **Fix**: For `ds.kind == 'snapshot'`, call `Q.trace(ds.path, oid, parent_root=<research_root>/raw or the acquisition store>)`. Show `parent_trace.status` (`verified` / `parent-raw-unavailable` / `parent-raw-not-requested`), the parent URL, the receipt and the byte range. Label the cohort file "cohort payload (byte-identical parent slices)".
- **Test**: Using the same cohort fixture, assert that `occurrence()['trace']['parent_trace']['status']` is present, and that it is `verified` when the parent raw object is available.

### F-4 · High · Discovery lifecycle: snapshots are opened once and never re-verified, retried or released; any open tab triggers discovery

- **Evidence**
  - Between 04:44 and 04:47, the snapshot directories were rewritten under the same IDs: `ce127437…/graph.duckdb` went from 25.5 MiB to 27,013,120 bytes, and `manifest.json` changed from 7.6 KiB to 8,092 bytes (manifest mtime 04:44:37, then 04:46:08). New IDs kept appearing: 2 snapshots, then 5.
  - `discover()` only adds keys it has not seen before (`bridge.py:146`). A `failed` or `incompatible` entry stays that way until the server restarts. A stale `ready` entry keeps its DuckDB handle for the whole process lifetime (`bridge.py:261`), and nothing re-checks `verify` afterwards. Snapshots removed from disk stay listed.
  - Discovery fires every 30 s from any open page (`web/app.js:616-624`). This live session had not yet opened the new snapshots; I stopped the page timer during browser checks to avoid it.
- **Risk**:
  - A half-written or rebuilt snapshot can be shown as `verified` while the data is stale or mixed. The raw file is re-read at trace time, but the DuckDB handle points at the old file.
  - A read-only handle held for the process lifetime can block the corpus worker from re-opening the DB to write.
  - A transient failure while the worker is writing becomes permanent.
- **Fix**:
  - Key datasets by `(snapshot_id, manifest_sha256)`.
  - On refresh: if the manifest hash or mtime changed, close the handle and re-verify. Retry `failed` entries. Close and drop removed ones.
  - Ask the backend for a "sealed/complete" marker (or an atomic rename) and skip unsealed directories.
  - Consider opening the DB per request, or only after a stable-for-N-seconds check.
- **Test**: In a scratch root, discover a snapshot, rewrite it (re-import), refresh, and assert it is re-verified. Then delete it, refresh, and assert it is removed. A snapshot that failed while partial must become `ready` after it completes.

### F-5 · High · Auto-refresh switches the dataset silently and leaves the old results under the new label (code-verified)

- **Evidence**: `renderStatus()` reassigns `state.ds` when the current dataset disappears (`web/app.js:71-74`), but `onDatasetChange()` is not called. The interval path (`:616-621`) only calls `renderStatus()`. When the first real snapshot verifies, `auto` hides the demos. `state.ds` then becomes the snapshot and the badge reads "verified snapshot", but the results table, the evidence panel, the examples and the graph still show **sample** data. The next token click sends a sample occurrence ID to the snapshot (error "unknown occurrence ID").
- **Risk**: Sample evidence is shown as verified-snapshot evidence at exactly the integration moment.
- **Fix**: In `renderStatus`, if `state.ds` changed, call `onDatasetChange()` (or clear results, detail, graph and examples) and show a notice such as "Dataset switched: demos hidden because a verified snapshot is ready."
- **Test**: There is no JS harness. Add a manual step to CHECKPOINT, or a small headless check: load a sample, make a snapshot ready, wait for the 30 s tick, and assert that `#results tbody` is cleared.

### F-6 · Medium · The Graph view returns 500 on the default README sample (DuckDB out of memory)

- **Repro (live)**: `GET /api/neighborhood?dataset=sample-ebl-readme&id=line:06fe01df5973cc211310c29086d54ee0&per=8` returns **500**. The server log shows `_duckdb.OutOfMemoryException … max_temp_directory_size` at `explore.py:105` (`SELECT id, kind FROM nodes WHERE id IN (…)`). In the browser, Focus or Expand on that line silently does nothing.
- **Fresh-process repro** (`probe/probe_oom.py`, `probe_oom2.py`): **8 of 34** README sample lines fail at the configured `memory_limit='128MB'` with a `0B` temp limit. With `512MB` none fail. `preserve_insertion_order=false` does not help. The in-memory DB is only about 31 MB and the `nodes` view has 682 rows.
- **Where**: `server/bridge.py:209-211`, `server/explore.py:98-106`.
- **Fix**: Raise the in-memory limit (512 MB passed), or resolve kinds per typed table instead of through the 14-way `nodes` UNION view. Surface the error in the graph panel instead of failing silently.
- **Test**: Loop `neighborhood()` over every line and sequence of every sample and assert there are no exceptions.
- **Side effect**: The failing request created `.tmp/duckdb_temp_storage_S32K-0.tmp` in the UI repo (F-19).

### F-7 · Medium · One read-only console query OOM-kills the isolated FalkorDB, and it stays down

- **Repro**: `POST /api/falkordb/query` with `UNWIND range(1, 2000000000) AS x WITH x WHERE x % 7 = 0 RETURN count(x)`.
  - Result: `connection closed`, and the container ended `OOMKilled=true ExitCode=137` at 09:49:16Z. It has `--restart no`, so the Workbench and the Browser stay down until someone runs `falkordb-up`.
  - `TIMEOUT 3000` does not bound memory. `QUERY_MEM_CAPACITY` = 0 (unlimited) and `TIMEOUT_MAX` = 0.
  - **I caused this outage during the review.** I restarted the UI's own existing container with `bin/cuneiform-ui falkordb-up`. Both projection graphs came back from RDB, and `load` reported `reused: true` with matching counts (78/124 and 682/1,446).
- **Where**: `bin/cuneiform-ui:61-62`, `server/falkor.py:307-326`.
- **Fix**: Add `QUERY_MEM_CAPACITY 268435456` (or similar) and `TIMEOUT_MAX 3000` to `FALKORDB_ARGS`. Consider `--restart on-failure:3`. Show a clear "FalkorDB died; run falkordb-up" state.
- **Test**: Run the same query and assert a FalkorDB memory-capacity error is returned, and that `PING` still answers afterwards.

### F-8 · Medium · Projection reuse checks only receipt and counts; edits made in the Browser survive "reload to restore it" (code-verified)

- **Evidence**: `load()` reuses an existing graph when the `ProjectionReceipt.export_id` and node/edge counts match (`server/falkor.py:252-257`). A property edit in the Browser (for example `SET n.reading='X'`) changes neither, so the next **Export & load** returns `reused: true` and the UI says "already loaded (receipt + counts match)". The FalkorDB panel says "Writes made in the Browser affect only the projection; reload to restore it" (`web/app.js:519`), which is false for property edits. I did not tamper with the live graph.
- **Fix**: On reuse, verify content, for example by comparing a stored per-label hash computed with Cypher over `id` and properties against the export. Otherwise always rebuild on an explicit load, or add a "Rebuild" button and change the wording.
- **Test**: Load, `GRAPH.QUERY … SET` one property, load again, and assert `reused` is false and the property is restored.

### F-9 · Medium · Console row count is silently capped at 10,000 and shown as the total

- **Repro**: The console query `UNWIND range(1, 20000) AS x RETURN x` returns `row_count: 10000, truncated: true`, shown as "10000 rows (first 500 shown)". `count(x)` returns 20000. FalkorDB's `RESULTSET_SIZE 10000` truncates the result before the UI's own 500-row cap.
- **Where**: `bin/cuneiform-ui:61`, `server/falkor.py:322-325`, `web/app.js:561`.
- **Fix**: When `len(rows) >= RESULTSET_SIZE`, report `row_count_lower_bound` and show "≥ 10,000 (server cap)".
- **Test**: Run that query and assert the lower-bound flag.

### F-10 · Medium · Graph-engine paging stops at the backend's 100-match cap with `truncated: false` **[copy]**

- **Evidence**: For `ABZ411 ABZ411` (signs, graph engine), the count is 152.
  - offset 0: `truncated true`
  - offset 75: `truncated false, next_offset null`
  - offset 100: 0 rows
  - The pager shows "Rows 76–100 of 152" with Next disabled. The note is there, but `truncated: false` is wrong.
- **Where**: `server/explore.py:229-237`.
- **Fix**: Add `materialized_cap_reached` and keep `truncated` true. Explain in the pager: "rows 101–152 are not retrievable from the graph engine; use Index".
- **Test**: Use a scratch fixture with more than 100 matches, page to offset 100, and assert the cap flag and wording.

### F-11 · Medium · Missing versus zero: the Coverage "unavailable" list is hard-coded

- **Evidence (live browser)**: The K.1 sample has exclusion "no transliteration supplied". Even so, `witness`, `sequence_version`, `line`, `occurrence`, `sign_type` and `translation_segment` show `0 · none`. Only composition, passage, word, lemma and sense show "unavailable (not supplied)".
- **Where**: `server/explore.py:147`, `web/app.js:468`.
- **Fix**: Derive availability from backend coverage, exclusions or manifest `coverage`, or label every zero as "0 (supplied as empty or not supplied — see exclusions)". Separately, `word_tokens_in_sequence == 0` is shown as "unavailable for this source", which turns a per-sequence count into a source-level claim (`app.js:277`).
- **Test**: For K.1, assert that the text layers report "not supplied".

### F-12 · Medium · The UI test suite reads the live canonical research root and now fails

- **Evidence**: `bin/cuneiform-ui test` ran 9 tests in 23.8 s: **FAILED (errors=1)**. In `test_boot_and_datasets`, the lookup `KeyError: 'synthetic-fixtures'` failed. `AppTest` builds `App` on the real `CUNEIFORM_RESEARCH_ROOT`, so it discovered and verified the corpus worker's new snapshots (read-only, about 24 s), and `auto` then hid the demos. The other 8 tests passed, none were skipped, and the FalkorDB boundary test ran.
- **Where**: `tests/test_app.py:30-34, 64-69`, `bin/cuneiform-ui:95`.
- **Risk**: Tests are not hermetic, open canonical snapshots while the corpus worker is writing them, and their result depends on that worker's progress.
- **Fix**: Point `AppTest` at a scratch root (copy `evidence/` or the fixtures) with `CUNEIFORM_UI_SHOW_DEMOS=always`, the same way `test_snapshot_path` does.
- **Test**: The suite passes whether or not real snapshots exist.

### F-13 · Low · Strict = 0 explanation is shown for every dataset

`web/app.js:186` claims that strict = 0 "is expected for current eBL data" whenever `strict && count === 0`, including for synthetic data, CDLI and future ORACC data. Condition it on the dataset namespace being eBL and on the position-state report showing no `intact`+`certain` positions.

### F-14 · Low · The Cypher console defaults to the wrong dataset's graph

The graph select takes the first loaded graph, not the current dataset's projection. In the browser, with the dataset set to `synthetic-fixtures`, it was preselected to `cuneiform_sample_ebl_readme_…`. The result header does not name the graph (`web/app.js:549, 561`). Preselect `graph_name(current ds)` and echo `r.graph` in the result line.

### F-15 · Low · The neighborhood's "first 300 incident edges" sample is not ordered **[copy]**

A real SignType has 380 incoming `MAPS_TO_SIGN` edges. The UI showed `+292+`, a correct lower bound. Edge groups that do not appear among the first 300 rows returned by `lookup('neighbors')` would be completely invisible. In the copy there was only one group, so the effect was latent. Fetch per-group counts (`GROUP BY kind, direction`) first and sample within each group (`explore.py:364-397`).

### F-16 · Low · Request robustness

- `POST /api/falkordb/query` with a JSON array body returns 500 (`(body or {}).get` on a list; `app.py:83-89`).
- `GET` with more than 20 query fields and `POST` with deeply nested JSON close the connection with no response, because `ValueError` from `parse_qs` and `RecursionError` are raised outside `_dispatch` (`app.py:162, 179`).
- Fix: require `isinstance(body, dict)`, and wrap both in the 400 path.

### F-17 · Low · The "Raw source evidence" excerpt comes from the canonical DB

The highlighted excerpt is rebuilt from `source_record.raw_json` (`explore.py:283-289`). Only the ✓ line comes from `trace_rows` on raw bytes. Label the excerpt "parsed record field (canonical DB)" and keep "raw" for the traced bytes.

### F-18 · Low · Dirty detection ignores untracked files

`bridge.py:124` uses `--untracked-files=no`. Untracked backend modules (several exist now) can change behavior while the tracked tree is clean, so the UI would report `tested`. Include untracked files under `cuneiform/`.

### F-19 · Low · DuckDB temp directory is created inside the repo; banner is stale

- The live server's working directory is the repo, so the F-6 out-of-memory error created `.tmp/duckdb_temp_storage_S32K-0.tmp`. It is untracked and not ignored. Set `temp_directory` under `CUNEIFORM_UI_HOME`, or ignore `.tmp/`. I left the file in place.
- The banner "No persisted snapshots found under …/snapshots" was shown while 2–5 snapshots existed, because it reflects the last discovery. Add the `last_discovery` time.

## Verified OK

- **Loopback and guards (live)**:
  - Missing or foreign Host → 421; `localhost:8740` → 200.
  - `Origin: null` → 403.
  - `Sec-Fetch-Site: same-site` → 403. This also blocks the :23001 Browser origin.
  - Cross-site navigation: `/` → 200, `/api/*` → 403.
  - `text/plain` POST → 415.
  - The test suite also covers 413, 405, traversal, bad IDs, LIKE escaping and Cypher literal escaping.
  - Container ports are bound to `127.0.0.1` only. The ownership label is present. CompanyKit containers were inspected only.
- **RO enforcement (live)**:
  - `SET`, `CREATE INDEX` and `db.idx.fulltext.createNodeIndex` were refused ("only on read-only queries").
  - `LOAD CSV FROM 'file:///…'` is confined to the import dir (server log: "unauthorized path /var/lib/FalkorDB/import/…").
  - `LOAD CSV` over `https://` was **not tested**; I made no outbound requests. If FalkorDB allows it under `RO_QUERY`, it is an exfiltration path from the console and the Browser. Worth checking offline with a local listener.
- **Counts, parity and export identity**:
  - Synthetic expected counts are 6/11/0/0/12 with parity.
  - README `ABZ215 ABZ13` = 3, index 3 = graph 3 (browser).
  - **[copy]** All 6 auto-example `expect` values matched the query count with parity.
  - **[copy]** 12 "frequent" bigrams and postings matched their query counts.
  - **[copy]** Export 11,887 nodes / 27,417 edges = backend report.
  - **[copy]** Export ID is deterministic.
- **Trace (browser)**: README occurrence `ABZ215` shows ✓ round trip at UTF-8 `[827, 833)`, raw sha `f9486da5…`, rights `unresolved-conflict`, basis "caller-supplied; not authenticated", and the translation placement note.
- **[copy] performance**: report 0.19 s, queries about 0.2 s, occurrence 0.36 s, export 0.43 s, verify + open 0.66 s on 32 MiB. The full-hash `assert_fresh` cost has not been measured on the 177 MiB or larger snapshots.

## Review actions with side effects (disclosed)

1. **F-7 repro OOM-killed `cuneiform-ui-falkordb`** at 09:49:16Z. I restarted it with `bin/cuneiform-ui falkordb-up` (the existing container; no new container, no pull). Graphs and counts were verified as restored.
2. **`bin/cuneiform-ui test` opened the canonical snapshots read-only** for about 24 s (F-12). It runs as a separate process and released its handles when it exited.
3. Browser checks used one T3 preview tab, now closed. I cleared the page's 30 s refresh timer right after load, so the **live** server never ran discovery on the corpus worker's snapshots.
4. The F-6 browser request created `.tmp/…` in this repo (F-19).
5. The test suite wrote and deleted `test-scratch-root` under the UI home, and created and deleted `cuneiform_test_escape` in the isolated FalkorDB. Both are its normal behavior.

No canonical corpus files, backend repo files, shared services or atlas docs were modified. Nothing was committed or published.

## Artifacts

Workspace: `~/Projects/skill-workspaces/cuneiform-ui-review/v0.1/`

- `probe/probe_real.py` → `probe-real.json`: real-copy bridge checks.
- `probe/probe_oom.py`, `probe/probe_oom2.py`: F-6 repro and memory-limit bisection.
- `probe/ro_probe.sh`: console RO probes. **Its "timeout" line triggers F-7. Do not run it against a FalkorDB you need.**
- `probe/guard_probe.sh`: HTTP guard probes.
- `status.json`, `falkor-status.json`: live status captures.

The scratch snapshot copy and the probe export were deleted after use, so no corpus copy is retained.

## Suggested order before corpus integration

1. F-1 (Browser off by default, `requirepass`).
2. F-2 and F-3 (cohort scope and parent trace).
3. F-4 and F-5 (lifecycle and stale-label switch).
4. F-6 and F-7 (crashes).
5. F-12 (hermetic tests).
6. F-8 to F-11.
7. Then rerun integration against the corpus worker's **final** sealed snapshots.
