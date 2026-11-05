# Checkpoint: cuneiform-ui

## 2026-11-03 (final) — re-pinned to accepted backend 63f4221; deployed live

Backend `63f42216243141f4b10cdfd3d7ddd514c85b63d7` was independently accepted in `cuneiform-research/reviews/corpus-pilot-review-v0.3.md` (70 tests on disk, R1–R5 closed, UI re-pin allowed). The UI now runs on a read-only `git archive` export of it (22 files, digest `bb926fbb8ab08af3`) at `~/.local/share/cuneiform-ui/backend-pin/63f4221…`. `TESTED_COMMITS` and `PIN_COMMIT` were set to it only after the suite passed. No backend, atlas or QUEUE edits. No delegates. No corpus rebuild, adoption, cohort build, canonical writes, downloads, images or embeddings. The three legacy cohorts stay unledgered.

**Live:** http://127.0.0.1:8740/ (pid in `run/ui.pid`; restarted 2026-11-03), backend chip `63f4221 · tested`. FalkorDB and relay were not restarted (configuration unchanged). Browser 23001 is closed and the arbitrary Cypher endpoint returns 403.

| Snapshot | Status | Publication (backend label) | Population |
|---|---|---|---|
| `ce127437…` | ready | legacy-incident (`ledgered-legacy-backfilled` + incident) | COHORT 30/20,896 eligible of 23,289 |
| `84971360…` | ready | legacy-incident (`ledgered-legacy-backfilled` + incident) | COHORT 300/20,896 eligible of 23,289 |
| `c316e7cb…` | ready | ledgered-legacy-backfilled | COHORT 51/51 eligible of 23,289 |
| `fc7c3504…` | ready | ledgered-legacy-backfilled | CDLI SELECTION 51 |
| `edaab55c…` | ready, **superseded for sense provenance** by `5e51403b…` | ledgered-legacy-backfilled | ORACC SELECTION 51 · oracc-cdl-1 |
| `5e51403b…` | ready (was `incompatible` under d6049e2) | ledgered (format 2, manifest file bytes bound) | ORACC SELECTION 51 · oracc-cdl-2 |

Ledger: 7 records, no torn tail, not corrupt.

What changed:
- **One read path.** Verification and every request go through `storage.verified(storage.store_path(config, id), ledger='require')`. Traces use the StoreEntry too (`query.trace`, `oracc.trace_word`, `persisted.scope`). The UI's own ledger parser and format-1 logic were removed. `storage.verified`, `storage.store_path` and `publication.Ledger` are now required APIs, so a backend without them reports `incompatible`.
- **Typed states.** `reserved` → `not-yet-available`; the backend's "no live writer holds its build lock" wording is never shown (review v0.3 N1). `symlinked-entry`, `misplaced`, `ledger-mismatch`, `conflict` and `ledger-corrupt` → `refused`. `published-missing`, `abandoned` and `not-found` → `unavailable`. `not-in-ledger` → `unpublished`. Ledger IDs without a directory are listed with their state. The UI pre-check uses the backend's state names; it still refuses any symlink inside an entry and types a foreign manifest ID as `misplaced`.
- **Torn ledger.** It is observed with the backend's `Ledger.scan`, never with a separate parser. An unparseable record before the end refuses every snapshot at scan time and on the next request (409). An unterminated tail is shown in the banner and in `publication.ledger_tail_note`. A torn commit leaves its ID `reserved`, so it is not served. This matches the backend, and nothing is skipped silently.
- **ORACC.** Both adapters are accepted; an unknown one is `incompatible`. cdl-1 is labelled superseded for sense provenance (picker, badge, banner, Coverage, word view), and stays listed and served. The word view shows the word's own sig, inst and payload pointer from its IN_SENSE link (cdl-2), or labels the shared Sense row sig as the first attesting instance (cdl-1). Coverage has a per-word sense-link panel.
- **Timing.** Every API response except status/health carries `timing.end_to_end_ms` (whole server request) and a breakdown (lock wait, stat checks, verify, open, query, backend trace with its own verify and parent re-read). The UI shows "End-to-end N ms" and puts the breakdown behind a toggle.
- **Client fix found in the browser:** switching dataset kept the previous dataset's banner note until the next poll. Fixed.

Evidence (logs and scripts in `~/Projects/skill-workspaces/cuneiform-ui-fixes/v0.4/`):
- Baseline: the 37 existing tests against the 63f4221 export gave 4 failures, all in a test harness that proxied `verify` but not `verified`, plus 1 skip for the old-pin test (`baseline-63f4221.log`).
- Final `bin/cuneiform-ui test`: 24/24 isolation checks (read-only; host/internet/DNS reachability printed as unprobed); **42 tests OK, 0 skipped** (`test-final.log`). Added cases: format 2 served via the backend; reserved shown as not yet available, without the backend's wording; torn commit tail fails closed and is reported, then becomes ready when the commit completes; an unrelated torn tail is shown on a served snapshot; a corrupt ledger refuses per request and on scan, then recovers; ledger-only reserved/abandoned IDs are typed and aborted ones hidden; removed published snapshot → `published-missing` with its stale version; backend's own `symlinked-entry` refusal; `misplaced`; ORACC adapter/supersession/sense-provenance units; timing breakdown; HTTP `timing` on queries.
- Real store, read-only, in process (`real-integration.json`) and live HTTP (`live-http.json`): 6/6 ready, discovery 4.7 s. 300-cohort `ABZ480 ABZ13` signs: index 201 = graph 201, 38 witnesses, 300 sequences; graph engine 201 with 100 retrievable; last page 1 row, Next disabled. Trace: payload round trip, parent `verified`, Zenodo URL, `receipt:66b5c944…`. Coverage pages 1–200 and 201–300 of 300. ORACC cdl-2: 4,544 IN_SENSE links for 4,544 words, 4,508 carry their own sig (36 have none in the source node), 0 of 1,317 Sense rows carry an instance sig. ORACC cdl-1: 0 links with their own sig, 1,309 of 1,317 Sense rows hold one instance's sig. ZIP member traces `verified` on both. Live guards: stale version 409, foreign Host 421, foreign Origin 403, Cypher 403, port 23001 closed.
- Real store fingerprint (snapshots, derived/cohorts, derived/exports, receipts; size, mtimes, mode, SHA-256): 262 entries, digest `101e44e3…`, identical before and after (`fp-before.json`, `fp-after-integration.json`).
- Browser (T3 preview, own tab, closed afterwards): picker lists 6 snapshots with adapter and superseded labels; the query form shows 201, parity ✓, 38 witnesses / 300 sequences, and "End-to-end 208 ms" with breakdown. Row click shows the Zenodo parent trace verified at bytes [62293318, 62293324). "Open in graph" → SignOccurrence → Line clickthrough (10 nodes). Coverage pages 1–200 → 201–300. ORACC cdl-2 word shows its own sig `{d}MAŠ=Ninurta…` from IN_SENSE. The cdl-1 word shows the superseded callout and labels the shared Sense row sig `{d}MAŠ…` as the first attesting instance, while its own LEMMATIZED_AS sig is `{d}NIN.URTA…`. **Refresh & re-verify** kept the dataset and its 25 rows, showed "re-verifying", and no demos appeared. A simulated `reserved` status in that tab only gave STALE · not-yet-available, cleared results, refused to run, kept the dataset, and returned to verified.
- `gitleaks protect --staged`: no leaks (~76 KB scanned).

Rollback: `git checkout 30f1f45 -- server web bin tests README.md` then `bin/cuneiform-ui ui-stop && bin/cuneiform-ui ui-start`. That restores `PIN_COMMIT` d6049e2, whose export is still at `backend-pin/d6049e2…`. Not needed.

Residual, non-blocking:
1. The live pre-restart `/api/status` capture failed: the shell's `curl` is a wrapper that prints a schema summary. The predecessor's state is the previous section of this file.
2. The "verified again" notice after a stale snapshot returns is transient: it is cleared on the next 1 s poll during Refresh. The badge and banner state are correct.
3. In cdl-2, 36 IN_SENSE links carry no sig because the source node has none. The UI says "No per-word sig recorded".
4. FalkorDB: the three snapshot projections (300-cohort, 30-cohort, ORACC cdl-1) are verifiable. The two demo graphs (README sample, synthetic) are in the old export format and show "rebuild required". `5e51403b…` has no projection yet. No rebuild was done this round.
5. Backend review N2/N3 (cohort `ledger='store'` labels, `publication-clean-stage`) do not affect the UI, which reads no cohort store. The three legacy cohorts await the parent's explicit adoption decision.
6. `reviews/ui-review-v0.2.md` is committed unchanged with this round.

**Q008 (for the atlas/queue owner, task 1359e965):** UI integration complete against accepted backend `63f4221`. The UI consumes `storage.verified(store_path, ledger='require')`, typed `PublicationError.state`, `persisted.scope`, `query.trace(StoreEntry, parent_root)`, `oracc.trace_word(StoreEntry)` and `timing.end_to_end_ms`. It is deployed live with the evidence above. Suggested status: DONE (local pilot), pending only an independent UI re-review if the parent wants one.

## 2026-11-03 (later) — review v0.2 fixes N-1, N-3…N-7; N-2 prepared, not re-pinned

Still pinned to backend `d6049e2` (Q-020: backend `94af111` B2 symlink-store bypass still open; parent fixing R1/R2; no upgrade until told). Review file `reviews/ui-review-v0.2.md` preserved unchanged (untracked, not committed by me). No backend/atlas/QUEUE edits, no delegates, no egress, no downloads, no canonical writes.

| # | Disposition |
|---|---|
| N-1 re-verify switches dataset | **Fixed.** A routine re-verify (deep refresh or 900 s timer) of an unchanged ready snapshot keeps `status=ready` and its version, with a `reverifying` flag; requests keep working (each still re-hashes every file). Only changed bytes, a changed ledger entry or a failed check retire the version, recorded as `stale {version, reason}`. A generation counter stops a verification that started earlier from committing over a newer invalidation. Demos stay hidden while a once-ready snapshot is settling/re-verifying. Client: never auto-switches after first load; the chosen dataset stays selected, with an explicit STALE banner/badge and results cleared when it stops being served; "verified again" or "new version" notices on return; late responses from a reset view are dropped (epoch guard); refresh polls every 1 s until the re-check ends. |
| N-2 ledger format 2 | **Prepared, live pin unchanged.** Under d6049e2, format-2 entries are typed `unsupported-ledger-format` (status `incompatible`, "version gap, not an integrity failure"), not `ledger-mismatch`. Mismatch/conflict notes now quote the entries. An unparseable terminated ledger line fails every ID closed (`ledger-corrupt`); an unterminated tail is ignored. When the pinned backend exposes `storage.verified`, the UI uses `verified(path, sid, ledger='require')` and maps `PublicationError.state` to typed states (hermetic test with a stand-in). `PublicationError`/`LedgerCorrupt` → 409 `publication-refused`, `ScopeMismatch` → 400, `LayerUnavailable` → 422. Q-020 hardening on the UI side: symlinked store entries, symlinks inside an entry, entries resolving outside the store, and manifest-ID ≠ directory are refused before hashing. Still to do at re-pin: `TESTED_COMMITS`/`PIN_COMMIT`, `end_to_end_ms`, both ORACC adapter labels, rerun the suite. |
| N-3 egress/mutating probes | **Fixed.** `security-check`/`test` are read-only and local: `/proc/net/route` has no default route, network mode, ACL-file and TIMEOUT_MAX from container config, NOPERM via non-mutating `GRAPH.CONFIG GET`/`CONFIG GET`/`KEYS`/`ACL LIST`, ro `GRAPH.QUERY` checked in the ACL file. Host/internet/DNS reachability is printed as **unprobed**; live probes only with `security-check --active-probes` (never from `test`). |
| N-4 coverage cap | **Fixed.** Report returns `coverage_total`, offset, `coverage_truncated`, next/prev; `/api/coverage?offset=` pages; UI shows "records 1–200 of 300" with a pager. |
| N-5 legacy verify 500 | **Fixed.** Receipts in another export format, without a receipt or incomplete → 200 `{status: unsupported-format…, rebuild_required: true}`; the graph is not modified (test checks before/after). Graph list flags `verifiable:false`; the UI shows "old format · rebuild required" instead of Verify. |
| N-6 readback columns only | **Fixed.** Read-back compares each node's exact label set and property-key set and each edge's key set as well as values, and requires exactly one fixed-shape ProjectionReceipt. Test: added node property, added label, added edge property, removed property and extra receipt each fail; reload restores. A load whose own read-back fails drops the just-built graph. |
| N-7 cleanup unchecked | **Fixed.** Leftover `cuneiform_test_*` graphs are dropped in `setUpClass`; every drop is asserted (200 + gone from `GRAPH.LIST`); `tearDownClass` fails if any test graph remains. |

Evidence:
- `bin/cuneiform-ui test`: 24/24 isolation checks ok (plus the labeled unprobed line), no outbound connection; `Ran 37 tests in 135.9s OK`, none skipped (FalkorDB up). Log: `~/Projects/skill-workspaces/cuneiform-ui-fixes/v0.3/test-full.log`.
- `gitleaks` on the changed tree: no leaks.
- Live UI restarted (pid in `run/ui.pid`, 127.0.0.1:8740), backend `d6049e2 · tested`. Five snapshots ready; `5e51403b…` is `incompatible [unsupported-ledger-format]`.
- Browser (T3 preview, incognito): 300-cohort `ABZ480 ABZ13` signs = 201 (parity 201 = 201, 38/300 witnesses); paged to "Rows 201–201 of 201" with Next disabled. **Refresh & re-verify** kept the 300-cohort, its rows and the pager, showed "re-verifying…" for about 1 s, no demos, no switch; a page request during the re-check returned 25 rows. A simulated failed re-verify (fetch intercepted in that tab only) gave an explicit STALE state on the same dataset, cleared results, refused to run, and announced "verified again" on recovery. Coverage: "300 source records", pages 1–200 → 201–300. Occurrence trace: payload round trip + parent verified (`receipt:66b5c944…`). Graph view renders BM.36650. FalkorDB tab: format-1 graphs show "rebuild required"; verify gives a typed `unsupported-format`.
- The stricter read-back passes on the real projections: 300-cohort 93,831/217,365 (77.8 s), 30-cohort 11,887/27,417, ORACC 9,636/17,707.

## 2026-11-03 — review v0.1 fixes + corpus integration against pinned backend d6049e2

Sole implementation worker for the UI repo and its own runtime (`~/.local/share/cuneiform-ui`, containers labeled `com.cuneiform-ui.owner=cuneiform-ui`). No subagents. The backend repo, the research root, CompanyKit services and atlas/QUEUE files were read only, never written. Review file `reviews/ui-review-v0.1.md` is preserved unchanged. **Not accepted**: the parent arranges an independent review; the backend corpus review (`reviews/corpus-pilot-review-v0.1.md` in the backend) is also pending.

### Running now

| Service | State | Address |
|---|---|---|
| Workbench UI | running (`bin/cuneiform-ui status`) | http://127.0.0.1:8740/ |
| FalkorDB 4.18.8 `cuneiform-ui-falkordb` | running, internal network only, `BROWSER=0` | no published port |
| Relay `cuneiform-ui-falkordb-relay` | running | `redis://127.0.0.1:26380` (ACL auth) |
| FalkorDB Browser | **disabled** | 23001 closed |
| Arbitrary Cypher endpoint | **disabled** (403) | templates via `POST /api/falkordb/template` |

Backend: pinned read-only export of `d6049e21b05dcc9c96b03cb7ca722cea921031a4` (21 files, digest `754caa6714b257a7`) at `~/.local/share/cuneiform-ui/backend-pin/d6049e2…`. The UI reports `backend d6049e2 · tested`. The live backend checkout has since moved to `11be81d` (docs/queue only; code trees identical to d6049e2) and the corpus worker added an untracked `cuneiform/publication.py`; the pin keeps the UI independent of that.

Snapshots served (all `ready`, verified per request):

| Snapshot | Population (backend `persisted.scope`) | Version (manifest SHA-256) | Publication |
|---|---|---|---|
| `snapshot:ce127437339109285185a534145edf50` | COHORT 30/20,896 eligible of 23,289 | `54ee6523a4fd3d19…` | legacy-incident (same-ID rebuild before ledger) |
| `snapshot:84971360d212834e0b7acbeac1e56abe` | COHORT 300/20,896 eligible of 23,289 | `5f5bbeaa28a6feb9…` | legacy-incident |
| `snapshot:c316e7cbbaf8a7fca7705f0efa5c4a07` | COHORT 51/51 eligible of 23,289 (P-number filter) | `075f6140562f63c9…` | ledgered-legacy |
| `snapshot:edaab55c1a39cc65ad7c76e6d12547a7` | ORACC ccpo SELECTION 51 | `be6b46f4645c7804…` | ledgered-legacy |
| `snapshot:fc7c350439f9edfee61b5f35bdd4d65a` | CDLI SELECTION 51 | `49ee7411ef455…` | ledgered-legacy |

### Findings dispositions (reviews/ui-review-v0.1.md)

| # | Disposition |
|---|---|
| F-1 Browser reaches CompanyKit / no Host check | **Fixed.** Browser off (`BROWSER=0`, 23001 unpublished). FalkorDB moved to `--internal` network (no host/internet/DNS egress, verified) behind a fixed-code loopback relay. ACL: default user off; ro/rw users; 0600 secrets; hash-only ACL file. `bin/cuneiform-ui security-check` (17 checks) runs before tests. Old container recreated (owned, data dir kept; backup `falkordb-data.backup-pre-isolation`). |
| F-2 cohort scope hidden | **Fixed.** Uses backend `persisted.scope`; dataset tag `[SNAPSHOT · COHORT 300/20,896 eligible of 23,289]`; every query response carries `scope_note`; Coverage shows the population panel, Source URL vs Receipt columns and a parent-object row. ORACC/CDLI selections handled the same way. |
| F-3 parent trace skipped | **Fixed.** Snapshots use backend `query.trace(path, occ, parent_root=<research root>)`; UI shows payload round trip and parent status/receipt/pointer/byte range/absolute span separately. ORACC words use `oracc.trace_word`. |
| F-4 discovery lifecycle | **Fixed.** Keyed by `(snapshot_id, manifest_sha256)`; stat fingerprint + quiet period; no persistent connections (per-request read-only open + full `storage.verify` + post-check); failed → retried with backoff, changed → re-verified, removed → dropped; publication ledger checked; only `snapshot:<32hex>` dirs. Server-side scan; pages no longer trigger discovery. |
| F-5 silent dataset switch | **Fixed.** Every request/response is bound to the dataset version; stale → 409 → page clears results and shows a notice; status changes that switch dataset/version reset all views. Browser-verified (simulated 409 cleared 9 rows). |
| F-6 Graph 500 (DuckDB OOM) | **Fixed.** Kinds resolved per typed table; in-memory limit 512 MB; graph errors shown in note + panel. Test walks every line and sequence of the README and synthetic samples. |
| F-7 console OOM-kills FalkorDB | **Fixed by removal of arbitrary Cypher.** Probe showed `QUERY_MEM_CAPACITY` does **not** stop `range()` OOM, and `RO_QUERY` allows outbound `LOAD CSV https`. Templates only; `TIMEOUT_MAX 3000`, mem cap, `--restart on-failure:3`; unreachable state explains `falkordb-up`. No OOM repro was run on the real instance (only a disposable probe). |
| F-8 reuse trusted counts | **Fixed.** Every load rebuilds; every node/edge is read back and compared with the export (`content_verified`); **Verify** button. Test tampers a property, sees verify fail, reloads, sees it restored. Also fixed a found bug: export overwrote SignOccurrence `kind` with the label (now `node_kind`). |
| F-9 10k cap shown as total | **Fixed.** Templates fetch limit+1 → exact `truncated`; `server_cap_hit` flag. |
| F-10 graph paging cap | **Fixed.** `materialized_cap_reached`, `retrievable`, truncated stays true, pager explains and points to Index. |
| F-11 hard-coded unavailable list | **Fixed.** Every zero data layer is `unavailable — not supplied…`; no-text sources say `no text supplied`; audit tables say `none recorded`; per-sequence word note no longer claims source-level absence. |
| F-12 non-hermetic tests | **Fixed.** Tests build scratch research roots + UI homes under `~/.local/share/cuneiform-ui/test-roots/`, demos forced on, test graphs prefixed `cuneiform_test_`; they never read the canonical snapshots. |
| F-13 strict note everywhere | **Fixed.** `strict_note` computed from the dataset's intact+certain positions; eBL wording only for eBL scope. |
| F-14 console graph default | **Fixed.** Preselects the current dataset version's projection; results echo the graph. |
| F-15 unordered 300-edge sample | **Fixed.** Exact per-group counts first, then bounded samples per group. |
| F-16 request robustness | **Fixed.** Non-object bodies, >20 query fields, deep JSON → 400; route-miss vs KeyError separated (404 vs 500). |
| F-17 excerpt labelled raw | **Fixed.** "Parsed record field (canonical DB)" vs "Raw-byte trace". |
| F-18 untracked files ignored | **Fixed.** Code-path untracked files count; pin by code tree or by hashed export; re-checked every scan. |
| F-19 temp dir in repo / stale banner | **Fixed.** DuckDB temp dir under UI home, UI cwd is the runtime dir, `.tmp/` ignored; banner shows last scan time. |

### Evidence

- `bin/cuneiform-ui test` → 17/17 isolation checks ok; `Ran 24 tests in 85.1s OK` (none skipped; FalkorDB up) against the pinned export.
- Live HTTP (pid in `run/ui.pid`): Host `evil.example` → 421, `localhost:8740` → 200, `Origin: http://evil.example` / `null` → 403, `Sec-Fetch-Site: same-site` → 403, cross-site navigation `/` → 200 and `/api/*` → 403, `text/plain` POST → 415, arbitrary Cypher → 403, stale version → 409, 23001 → connection refused.
- Real data (live API and browser): 300-cohort `ABZ480 ABZ13` signs = 201 (index = graph, 38/300 witnesses), `DIŠ ina` = 45, `ABZ411×3` graph engine = 174 with 100 retrievable and the cap flagged, strict = 0 with data-derived note. Occurrence trace: payload round trip + parent `verified` (`receipt:66b5c944…`, pointer `/17010`, record bytes [62291849, 62295828), token span [62293318, 62293324)). ORACC `LUGAL → šarru`, gloss "king" labeled not a translation, parent ZIP member `ccpo/corpusjson/P452134.json` verified. 30-cohort FalkorDB load 11,887 / 27,417 content-verified (6.2 s), template `ABZ411→ABZ411` = 152; ORACC load 9,636 / 17,707 content-verified. SignType neighborhood: exact `MAPS_TO_SIGN in` total 1,480.
- FalkorDB probes (disposable containers, since removed): internal network has no TCP/DNS egress; `RO_QUERY` + `LOAD CSV https` connected to a local listener; `http://` refused; `range()` OOM-killed a probe despite `QUERY_MEM_CAPACITY 268435456`.

### Remaining blockers / notes for the parent

1. Independent re-review of this UI change set; backend corpus review pending. Do not call the integration accepted until both close.
2. The two `legacy-incident` snapshots are served with a banner; their pre-rebuild bytes are lost (backend incident record). The parent/corpus owner may prefer superseding them with new manifest-3 IDs.
3. The corpus worker's new untracked `cuneiform/publication.py` (after d6049e2) is not used. To move the UI to a new backend commit: add it to `TESTED_COMMITS`, `bin/cuneiform-ui pin-backend <sha>`, update `PIN_COMMIT`, run `test`, restart.
4. Rollback: `git checkout 567ea14 -- .` and restart would re-expose the Browser; prefer reverting only server/web files. Old FalkorDB data backup: `~/.local/share/cuneiform-ui/falkordb-data.backup-pre-isolation` (232 KiB; deletable after review).
5. CompanyKit `16379`/`13001` are published on `0.0.0.0` (review F-1); outside this repo, untouched, for the CompanyKit owner.

Suggested QUEUE update (for the atlas/queue owner): Q-002 → `IN_REVIEW` with this commit; Q-006 findings F-1…F-19 dispositioned as above; Q-008 unblocked for review: UI consumes `persisted.scope`, `query.trace(parent_root)`, `oracc.trace_word`, `projections.lookup` per request, keyed by `(snapshot_id, manifest_sha256)`.

### Storage

UI runtime ~30 MiB (exports, FalkorDB data, pinned export 21 files). No new images (pinned digest already cached), packages, downloads or public exposure.

## Full-corpus review follow-ups N1–N3 (2026-11-05)

Parent fixed typed per-request ledger retirement to invalidate its discovery fingerprint. Restoring the original ledger before discovery now triggers re-verification instead of leaving the snapshot unavailable until restart. New hermetic regression exercises refusal, byte-identical ledger restoration, discovery and a successful query. Strict empty-posting text and parser attribution corrected; integrity wording now distinguishes full hash checks from bounded per-request checks and approximate background cadence. No data or backend pin changed.

Validation: `bin/cuneiform-ui test`: 24 isolation checks, 45 tests OK, zero skipped, 163.809 s. `node --check web/app.js` passed. Secret scan clean. Rollback: revert this follow-up commit and use ui-stop/ui-start (no FalkorDB restart).

Live follow-up: the detached ui-start launcher serves pid 67688 on 8740; source query ABZ480 ABZ13 returned 7,170 and strict returned 0 with the corrected conservative-3 explanation. Initial connection attempts raced startup. A temporary launchctl fallback collided with the already-running detached process and was removed; no launchd job retained. The normal PID file identifies the serving process. FalkorDB was not restarted.
