# Checkpoint: cuneiform-ui

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
