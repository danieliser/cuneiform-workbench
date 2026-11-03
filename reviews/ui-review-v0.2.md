# cuneiform-ui review v0.2 (independent re-review of the v0.1 fixes)

Reviewer: independent review sub-agent. Date: 2026-11-03, 05:37–05:55 EST.
Scope: the v0.1 findings F-1…F-19 and the CHECKPOINT dispositions at UI commit `ca4b514`, checked against the **pinned** backend `d6049e2`. Methods: code review, live read-only API probes, the user's browser workflow (T3 preview, incognito profile), and isolated unit tests. This file is the only repo change. I made no implementation edits, commits, delegations or publications.

**This is not end-to-end acceptance.** The backend publication fixes (now `94af111`) still await their own independent review. I did not review or load them; the UI was not upgraded during this review.

## Reviewed identities

| Item | Observed |
|---|---|
| UI repo | `ca4b51414810122bb6107ee8c37fe1e11537d1ae`, worktree clean (`.tmp/` ignored) |
| Live UI | pid 80544, started 05:32:21. That is after the last code mtime (05:30:32), so it runs `ca4b514`. Bound to `127.0.0.1:8740` only. |
| Backend (served) | Pinned export `~/.local/share/cuneiform-ui/backend-pin/d6049e21b05dcc9c96b03cb7ca722cea921031a4`: 21 files, tree digest `754caa6714b257a7`, all files `0444` and directories `0555`. **I re-derived it independently**: a read-only `git archive d6049e2 cuneiform fixtures pyproject.toml` is byte-identical (21/21 files) and recomputes the same digest. The UI reports `backend d6049e2 · tested`, dirty `false`, `code_changed_since_start=false`. |
| Backend repo HEAD (not served) | `94af111` (8 code files differ from `d6049e2`, +775/−96), committed after the pin. It is out of scope here. |
| Versions | schema `2`, parser `conservative-3`, projection `linked-1`, Python 3.13.12, DuckDB 1.4.3 |
| FalkorDB | 4.18.8, image `sha256:91242f82…` (run by digest `bbebe3dc…`). Config label `4f09432fb8f7ade8` equals the hash recomputed from `bin/cuneiform-ui`. Server on `cuneiform-ui-internal` only (`Internal=true`), no published ports, `BROWSER=0`. Relay published on `127.0.0.1:26380` only, with read-only rootfs, `cap-drop ALL`, uid 65534 and `no-new-privileges`. |

Snapshots as served (all verified per request):

| Snapshot | Population | Version (manifest SHA-256) | Publication |
|---|---|---|---|
| `ce127437339109285185a534145edf50` | COHORT 30/20,896 eligible of 23,289 | `54ee6523a4fd3d19…` | legacy-incident |
| `84971360d212834e0b7acbeac1e56abe` | COHORT 300/20,896 eligible of 23,289 | `5f5bbeaa28a6feb9…` | legacy-incident |
| `c316e7cbbaf8a7fca7705f0efa5c4a07` | COHORT 51/51 | `075f6140562f63c9…` | ledgered-legacy |
| `fc7c350439f9edfee61b5f35bdd4d65a` | CDLI SELECTION 51 | `49ee7411ef4550c7…` | ledgered-legacy |
| `edaab55c1a39cc65ad7c76e6d12547a7` | ORACC ccpo SELECTION 51 | `be6b46f4645c7804…` | ledgered-legacy |
| `5e51403b0d7dcdb90cb429d444e6a23b` | ORACC selection, manifest-3 (new, 05:36) | `48bdc39e62d6e35d…` | **refused**: shown as `ledger-mismatch` (see N-2) |

Incidents `ce127437` and `84971360` stay visible in four places: the banner, the dataset badge ("verified · legacy ID incident"), its tooltip, and the Coverage publication row. The wording is "first-version bytes were not retained … cannot be reproduced". Nothing claims the original bytes were recovered.

## Verdict

- **Usable for a local, read-only pilot** on the five served snapshots.
- The v0.1 blockers (F-1 to F-7) and F-8 to F-19 are fixed. I verified them live or in code, with the residuals noted below.
- I found **no High-severity issue**.
- Two Medium items remain:
  - **N-1**: re-verification takes a ready snapshot out of service, and the page then switches the user's dataset.
  - **N-2**: a compatibility dependency for the next backend. It is **not a regression at the pinned target**.
- Neither causes wrong data to be shown; both fail safe.

## v0.1 findings: re-review dispositions

| # | CHECKPOINT | Re-review | Evidence / residual |
|---|---|---|---|
| F-1 Browser / isolation | Fixed | **Verified** | Nothing listens on 23001 (`lsof`). The FalkorDB container has only a link-local route: `/proc/net/route` has no default gateway. Its process list is `redis-server` only. Anonymous `PING` gets `NOAUTH`. Live HTTP: Host `evil.example` → 421; `Origin` evil, `null` or `:23001` → 403; `Sec-Fetch-Site: same-site` → 403. Residuals: CompanyKit `16379`/`13001` are still bound to `*` (outside this repo). I verified no-egress by configuration only; I sent no probe (see N-3). |
| F-2 cohort scope | Fixed | **Verified** | Dataset tag, scope note on every query response, Coverage population panel (size 300, eligible 20,896, parent 23,289, rule `usable-text-v1`, selector, provenance). Parent exclusions sum to 2,393 = 23,289 − 20,896. Source URL and Receipt are separate columns. |
| F-3 parent trace | Fixed | **Verified (browser + API)** | "✓ Exact round-trip in cohort payload … [735, 741)" is shown separately from "✓ Parent object verified … absolute bytes [62293318, 62293324)" with parent sha `4e970d87…`, receipt `receipt:66b5c944…`, pointer `/17010` and record range `[62291849, 62295828)`. |
| F-4 lifecycle | Fixed | **Verified for change detection** | The hermetic tests cover settling, changed bytes, same-size change, removal, partial-then-complete, unledgered and legacy cases. No connection is held. New side effect: **N-1**. |
| F-5 silent switch | Fixed | **Verified** for version changes: the stale version gets a live 409 and the page clears with a notice. Switches are now announced, not silent. N-1 is a separate trigger of the same reset. |
| F-6 Graph 500 | Fixed | **Verified** | Neighborhoods of a SignType (exact `MAPS_TO_SIGN in` = 1,480), Artifact, SequenceVersion, Line and SourceRecord all return 200 in 0.13–0.28 s with no unknown-kind nodes. Browser Graph view renders. |
| F-7 console OOM | Fixed by removal | **Verified** | `/api/falkordb/query` → 403. The templates contain no `LOAD CSV`, `CALL`, `range(`, `UNWIND` or write clauses (asserted at import). I did not run an OOM query. |
| F-8 reuse trusts counts | Fixed | **Verified**, with a residual | Verify passes on the 30-cohort (11,887/27,417), ORACC (9,636/17,707) and the new 300-cohort (93,831/217,365). The tamper → verify-fail → rebuild test passes. Residual: N-6. |
| F-9 10k cap | Fixed | **Verified** | `reading-pair` limit 100 → `row_count 100, truncated true`. Limit 500 → 152, `truncated false`. |
| F-10 graph paging | Fixed | **Verified** | `ABZ480 ABZ13`, graph engine: offset 75 gives `retrievable 100`, `truncated true`, `materialized_cap_reached true`, and the note "Rows 101–201 exist … use the Index engine". Offset 100 returns 0 rows with `truncated true`. |
| F-11 unavailable layers | Fixed | **Verified** | Zero layers read "unavailable — not supplied…". Audit tables read "none recorded". The browser shows "Word / lemma / sense layers: unavailable in this dataset (… not zero linguistic words)". |
| F-12 hermetic tests | Fixed | **Verified** | 24/24 OK in 76 s against the pin. Scratch roots are created and removed under `test-roots/`. Tests copy only the small retained `evidence/` samples and never read `snapshots/`. |
| F-13 strict note | Fixed | **Verified** | Strict = 0 on the 300-cohort gives the eBL-specific wording, derived from the data. |
| F-14 console default | Fixed | **Verified** when a projection of the current version exists. If none exists, the first listed graph (a legacy sample graph) is preselected, but the hint says so. Acceptable. |
| F-15 edge sampling | Fixed | **Verified** | Exact per-group totals (1,480) appear before sampling. |
| F-16 robustness | Fixed | **Verified (live)** | Array body → 400. Deep JSON under 64 KiB → 400. Over 64 KiB → 413. 25 query fields → 400. URL over 4 KiB → 414. PUT → 405. `text/plain` → 415. Path traversal variants (`/../server/app.py`, `/%2e%2e/…`, `//etc/passwd`, `/api/../README.md`) → 404. |
| F-17 excerpt label | Fixed | **Verified (browser)** | "PARSED RECORD FIELD (CANONICAL DB)" vs "RAW-BYTE TRACE". |
| F-18 untracked files | Fixed | **Verified** | The pin hashes every file and flags extra or missing files. I independently confirmed the pin is identical to `d6049e2`. |
| F-19 temp dir / banner | Fixed | **Verified** | Process cwd is the runtime dir, DuckDB temp is under the UI home, `.tmp/` is ignored, and the banner and footer show the last scan time. |

## New findings (most severe first)

### N-1 · Medium · Re-verifying an unchanged snapshot takes it out of service; the page then switches the user to a different dataset

- **Repro (browser)**
  1. On the 300-cohort, run `ABZ480 ABZ13` (25 rows shown).
  2. Click **Refresh & re-verify**.
  3. Within 2.5 s, all five snapshots read `verifying` and are disabled in the picker. The demos reappear. The page shows "Dataset switched from snapshot:84971360… to sample-ebl-readme … Earlier results were cleared". There are 0 rows.
  4. The next 30 s poll switches again, to `snapshot:ce127437…`. That is the **30**-cohort, not the 300-cohort the user had.
- **Cause**
  - `_consider` sets `status='verifying'` and `version=None` for every *routine* re-verify, even when the fingerprint is unchanged (`server/bridge.py:362-376`). This happens on deep refresh and on the 900 s `REVERIFY_S` timer.
  - `Backend.get()` then refuses the dataset: 409 when a version is sent, 400 otherwise (`bridge.py:590-593`).
  - `renderStatus()` falls back to `keys[0]` when the current dataset is not ready (`web/app.js:88`).
  - The periodic path affects one snapshot every 15 minutes. It shows up as a spurious 409 or a dataset switch whenever a request or poll lands inside the verification window (code-verified; I did not wait for it).
- **Risk**: This fails safe; no stale data is shown. But the user is moved between similarly labeled cohorts (30 vs 300) and can re-run queries against a different population. The banner names the switch, but it is easy to miss.
- **Fix**:
  - When the fingerprint and ledger entries are unchanged, keep `status='ready'` and the current version while re-verifying (add a `reverifying` flag). Only invalidate when bytes or the manifest actually change, or the check fails.
  - On the client, keep `state.ds` while it is transiently `verifying`/`settling` instead of falling back to `keys[0]`, and do not unhide demos for a transient state.
- **Test**: `discover(deep=True)` on a ready scratch snapshot → `get(key, version)` still succeeds during verification.

### N-2 · Medium (compatibility dependency, not a regression at the pinned target) · Ledger format 2 snapshots are refused with a misleading message

- **Evidence**: The new manifest-3 snapshot `5e51403b…` has two legitimate ledger lines (`event: reserve`, then `event: commit`, `ledger_format 2`). `publication_of()` requires exactly one entry (`server/bridge.py:171`). The snapshot is therefore refused, and the banner says "manifest SHA-256 differs from the publication ledger (or the ID was published more than once)". Its manifest SHA in both lines equals the manifest.
- **Assessment**: Refusing a contract the pinned backend `d6049e2` never produced is the right fail-closed behavior. The wording, though, implies an integrity violation that did not happen. The UI's own ledger parser also duplicates backend logic.
- **Integration requirements** for the later move to the reviewed backend (`94af111` or later). Do **not** apply these mid-review:
  - Parse format 2 as reserve + commit. Multiple lines per ID are legitimate. Serve only committed IDs and treat a reserve with no commit as not published.
  - Prefer the backend's trusted read path (`storage.verified`) over the UI's own `storage.verify` + ledger re-implementation.
  - Handle the backend's `PublicationError`, `ScopeMismatch` and `LayerUnavailable` as typed user-facing states, not generic 400 or 500.
  - Surface `end_to_end_ms`.
  - Accept both old and new ORACC adapter labels.
  - Ignore `.locks`/`.recovery` entries. The current `snapshot:<32hex>` filter already skips `snapshots/.locks`.
  - Re-pin, add to `TESTED_COMMITS`, rerun the hermetic suite, and replace the "differs" message with a reason taken from the actual entries.

### N-3 · Low · `security-check` (and therefore `test`) embeds outbound and mutating probes

- `bin/cuneiform-ui:212-214` attempts `/dev/tcp/1.1.1.1/443` and resolves `example.com` from inside the container.
- `:219` runs `GRAPH.CONFIG SET TIMEOUT_MAX 0` as the loader user.
- If isolation ever regressed, the check would itself make egress, or would actually remove the timeout cap.
- Under this review's no-egress rule I could not run `test` as packaged. I ran the unit suite directly and did the checks manually instead: route table, process list, `NOAUTH`, `NOPERM` for `ro GRAPH.QUERY`, `rw GRAPH.CONFIG GET`, `rw CONFIG GET` and `rw KEYS`, plus 0600 secrets and a hash-only ACL.
- Fix:
  - Assert "no default route" (`/proc/net/route`) and `Internal=true` instead of connecting out.
  - Use a non-mutating `GRAPH.CONFIG GET`.
  - Keep any real egress probe behind an explicit opt-in.

### N-4 · Low · Per-record coverage table silently drops records

- `explore.py:155` keeps `P.coverage(db)[:200]` and does not forward the backend's `coverage_total`/`coverage_truncated`.
- Browser: the 300-cohort Coverage tab shows 200 rows with no note that 100 records are hidden.
- Fix: return the total and the truncation flag and say "first 200 of 300", or page the table.

### N-5 · Low · Verify on a legacy (export format 1) projection returns 500

- `POST /api/falkordb/verify {"graph":"cuneiform_sample_ebl_readme_07284fce890c"}` → 500. The log shows `KeyError: 'node_kind'` at `server/falkor.py:427`.
- Two format-1 graphs from backend `2ab620b` are still listed with a **Verify** button.
- Fix: if `receipt.format != EXPORT_FORMAT`, return `content_verified: false, problems: ['legacy export format; rebuild']`.
- Informational: the 30-cohort and ORACC projections carry receipts from backend `11be81d`, not `d6049e2`. Their code trees are identical, so this is consistent with the code-tree pin, but the FalkorDB table shows `11be81d`.

### N-6 · Low · Readback compares only exported columns

- `_readback` reads `node_columns(kind)` and fixed edge fields (`falkor.py:436-450`).
- A property **added** to an existing node or edge, or an extra label on an exported node, would not change the digest.
- Exposure is small: the Browser is off and the rw credential is held only by the UI server.
- Fix: also compare `keys(n)`/`labels(n)` per page, or count properties.

### N-7 · Low · Test cleanup result is unchecked

- `cuneiform_test_synthetic_fixtures_0ecc30d589ad` (d6049e2, version `7d646108…`) existed before my full-suite run and was still present after it.
- An isolated rerun of that test cleaned up correctly, so I could not reproduce the leak.
- `tests/test_app.py:310` ignores the drop status. Assert 200, and drop leftover `cuneiform_test_*` graphs in `setUpClass`.

### Notes (no action required for the pilot)

- **Load cost**: Export, rebuild and verify of the 300-cohort took 81.4 s (server 70 s). A standalone Verify took 50 s. FalkorDB memory went from 61 to 184 MiB with no OOM and no restarts. The request blocks the page; this is acceptable at pilot scale but worth a progress indicator.
- **Server container hardening**: `redis-server` runs as uid 0 with Docker's default capability set. This is mitigated by the internal network, the ACL and `no-new-privileges`. Consider `--cap-drop ALL` plus the needed caps, or a non-root user.
- **Pin is self-attested**: `_pin` trusts `.cuneiform-ui-pin.json` as written by `pin-backend`; it is not bound to the git object. I verified it externally. A tamper would need write access to a `0555` tree in the user's home.

## Real read-only sample (300-cohort `snapshot:84971360…`, version `5f5bbeaa…`)

| Check | Result |
|---|---|
| `ABZ480 ABZ13`, signs, source, index | **201**; parity index 201 = graph 201 ✓; **38 witnesses / 38 artifacts** of denominators 300 witnesses, 300 artifacts, 300 sequences and 48,597 represented positions; scope note present; 0.23 s |
| Index paging | offset 200 → 1 row, `truncated false`, `next null` |
| Graph engine | count 201, retrievable 100, cap flagged (see F-10) |
| Strict | 0, with the data-derived eBL note |
| Others | `DIŠ ina` = 45 (parity ✓); `ABZ480 ? ABZ13` = 46 (parity ✓); single `ABZ480` = 1,480 |
| Provenance trace (`occurrence:cba4139f…`, YBC.4111 line 9) | Payload round trip `[735, 741)`, raw sha `28d1c906…`. Parent **verified** at `[62293318, 62293324)`, receipt `66b5c944…`, pointer `/17010`. Rights `unresolved-conflict`. 0.69 s. |
| ORACC `LUGAL` (`edaab55c…`) | 44 words. `šarru` / guideword "king", `gloss_status: lexical gloss; not a translation`, with the gloss note. Trace to ZIP member `ccpo/corpusjson/P452134.json` (sha `f085d601…`), pointer `/39/cdl/0/cdl/5/cdl/0/cdl/410`. |
| FalkorDB (own disposable projection of the 300-cohort) | Content-verified 93,831 / 217,365. Template counts equal the index: `ABZ480→ABZ13` 201, `DIŠ→ina` 45, `ABZ411→ABZ411` 565. A SignOccurrence keeps its label separate from its `kind` property (`derived_sign`), and node-neighbors match the DuckDB neighborhood groups. |
| Hostile input | Cypher-breakout strings (`"})-[]-()`, `x"}),(m)RETURN(m)//`, `\"})`, `x'})`) are treated as data (0 rows). Whitespace or control characters are rejected. Non-`cuneiform_*` graph → 400. |

## Security boundary limitations (as reviewed)

- Any same-origin page on `127.0.0.1:8740` can trigger load, drop and refresh. These act on disposable `cuneiform_*` projections and read-only re-verification only. There is no user authentication; it is loopback single-user by design.
- No-egress from FalkorDB rests on Docker `--internal` plus the missing default route. I did not independently test DNS forwarding through the embedded resolver (`127.0.0.11`); the CHECKPOINT reports a probe.
- The relay container sits on a normal bridge network (it has egress), but its code is fixed and forwards only to `falkordb:6379`.
- Local processes can reach `26380`, but ACL credentials are required. Those are 0600 files readable by this user account.
- CompanyKit services remain published on `0.0.0.0` (outside this repo; untouched).

## Side effects of this review (disclosed)

1. **Dropped** the leftover test graph `cuneiform_test_synthetic_fixtures_0ecc30d589ad` (clearly test-generated) and reran one test, which recreated and dropped it.
2. **Created** the UI-owned projection `cuneiform_snapshot_84971360d212834e0b7acbeac1e56ab_7589f3892d0f` and its write-once export under `~/.local/share/cuneiform-ui/exports/`. This is a normal UI action, and the projection is disposable. All pre-existing user graphs were preserved.
3. **Refresh & re-verify** (N-1 repro) re-hashed the five canonical snapshots read-only. The full test suite created and removed scratch roots and `cuneiform_test_*` graphs.
4. Intentional error responses: one stale-version 409, and one 500 from Verify on a legacy graph (N-5).
5. Read the backend git objects with a read-only `git --no-optional-locks archive` into my workspace (deleted after the comparison).
6. One incognito T3 preview tab, closed.

No writes to canonical snapshots, the ledger, the backend repo, CompanyKit services or atlas/QUEUE files. No egress requests, no downloads, no dependency changes.

## Status handoff for the Atlas reviewer (QUEUE owner; I did not edit QUEUE)

- **Q-002 / Q-006 (UI review v0.1 findings)**: re-reviewed at `ca4b514` against pin `d6049e2`. F-1 to F-19 are verified fixed, with the residuals listed. New findings: N-1 and N-2 (Medium), N-3 to N-7 (Low). Recommended state: **pilot-usable, not accepted end-to-end**.
- **Q-008 integration**: blocked on (a) the independent review of backend `94af111` publication fixes, then (b) a UI re-pin implementing the N-2 integration requirements. N-1 is advisable before wider use.

## Artifacts

Workspace: `~/Projects/skill-workspaces/cuneiform-ui-review/v0.2/`

- `probe/h.py`: loopback client
- `probe/guards.py` → `guards.out`
- `probe/real.py` → `real.out`, `real-sample.json`
- `probe/real2.py` → `real2.out`
- `falkor.out`, `load300.out`
- `unittest.log`
- `status-before.json`, `falkor-status-before.json`, `falkor-status-after-tests.json`
