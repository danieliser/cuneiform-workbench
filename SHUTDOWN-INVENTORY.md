# Shutdown inventory (read-only, 2026-11-06 ~00:20Z)

I inspected everything below read-only for the parent's conditional cleanup. Nothing was deleted, stopped, moved, pushed or backed up. I did not read secrets or environment values. I listed the unrelated CompanyKit processes and containers only to rule them out; I did not inspect them.

**Remote visibility:** none of the three repositories has a git remote. Whether a backup destination would be public or private is unknown, so I have not guessed.

## Blockers before any cleanup

1. **A parser-repair worker is running in `cuneiform-research`.**
   - Process: pid 10343, parent pid 10342, started about 18 minutes before inspection.
   - Command: `full_import_stage.py build --root ~/.local/share/cuneiform-research`, with working directory `cuneiform-research/.output/q026-parser-repair`.
   - It is writing `~/.local/share/cuneiform-research/snapshots/.pending-1c1f58a1352f4ec2a4e37fc5a485c7af` (12 GB and growing).
   - Do not touch `snapshots/`, `.publication-ledger.jsonl`, `.locks/` or `.output/q026-parser-repair/` until the parent has stopped the worker or it has finished, and the outcome is recorded.
2. **`cuneiform-research` has 2 untracked files**, owned by the active worker or reviewer: `docs/PARSER-REVISIONS.md` and `reviews/reading-aids-review-v0.1.md`. They need committing, or an explicit decision to drop them, before backup.
3. **The live UI is serving** on 127.0.0.1:8740. Process: pid 12251, `python -B -m server.app`, working directory `~/.local/share/cuneiform-ui/run`, restarted during this study. It reads the snapshot store. Stop it before removing runtime data.

## Repositories (code and evidence: keep, back up to a known git destination)

| Repo | Branch | HEAD | Commits | Remotes | Dirty / untracked | `.git` | Working tree | Ignored output |
|---|---|---|---|---|---|---|---|---|
| `~/Projects/Fun/cuneiform-research` | `main` (only branch) | `6e548c1` | 20 | **0** | 0 / **2** | 3.0 MB | 347 MB | `.output/` 342 MB (q026-parser-repair 323 MB **active**; q025-refs 15 MB; q025-review 0.8 MB; atlas-render 2 MB; q018 1.1 MB); `__pycache__` |
| `~/Projects/Fun/cuneiform-ui` | `main` (only branch) | `2a43151` (moved from `6354880` during this study) | 12 | **0** | 0 / 0 | 1.9 MB | 65 MB | `output/` 61 MB, `.tmp/` 0.5 MB, `__pycache__` |
| `~/Projects/Fun/cuneiform-benchmarks` | `master` (only branch) | this commit | 18 | **0** | 0 / 0 after this commit | 1.3 MB | about 102 MB | `out/` about 100 MB (`review/` 65 MB, `B2…` 18 MB, `value-refs/` 6.4 MB, `V1eval…` 5 MB predictions) |

None has stashes or extra worktrees.

`cuneiform-benchmarks/README.md` says "Do not push or redistribute". The cause is the unresolved eBL rights. A **private** destination is needed, or `results/` (catalogue keys, sign-label examples) must be reviewed before any public push.

## Runtime and scratch (data; eligible for cleanup once the blockers clear)

| Path | Size | Origin / reproducibility | Suggested disposition |
|---|---|---|---|
| `~/.local/share/cuneiform-research/snapshots/snapshot:965aa262…` | 8.9 GB | Built from raw + research `057a0b7` (about 22 min, 6.25 GB RSS) | eligible; keep `manifest.json` (8 KB) as evidence |
| `…/snapshots/.pending-1c1f58a1…` | 12 GB | **active parser-repair build** | blocked (worker) |
| `…/snapshots/` (6 small snapshots: `5e51403b`, `84971360`, `c316e7cb`, `ce127437`, `edaab55c`, `fc7c3504`) | about 340 MB | pilots and adopted cohorts | eligible; keep manifests and the ledger as evidence |
| `…/snapshots/.publication-ledger.jsonl`, `.locks/`, `.publication.lock` | 3.4 KB | ledger | keep a copy with the backup |
| `…/raw/sha256/4e970d87…` (`fragments.json`) | 73.9 MB (`raw/` 81 MB) | eBL Zenodo 10018951 v2, md5 `71538e2d…`, sha256 `4e970d87…` | eligible (re-downloadable); keep receipts |
| `…/derived/` | 222 MB | derived parquet and arrays | eligible |
| `…/scratch/` | 4.0 GB, of which q023-full-import is 3.4 GB (the Q-023 reviewer said removable) | review/repair scratch q005–q023 | eligible; each has an INDEX or review reference |
| `…/receipts/`, `…/trust/`, `…/evidence/`, `…/runs/` | 0.7 MB total | provenance receipts | **keep** (archive with the code) |
| `…/venv/` | 50 MB | stdlib + duckdb 1.4.3 interpreter used by all three repos | eligible last |
| `~/.local/share/cuneiform-ui/backend-pin/057a0b7…` | 0.8 MB | `git archive` of research `057a0b7` | eligible (reproducible from git) |
| `~/.local/share/cuneiform-ui/exports/` | 121 MB | UI exports | eligible |
| `~/.local/share/cuneiform-ui/falkordb-data/` (+ `falkordb-data.backup-pre-isolation/` 232 KB, `falkordb-conf/`) | 36 MB | FalkorDB volume (bounded projections) | eligible after the containers are removed |
| `~/.local/share/cuneiform-ui/logs/`, `run/`, `duckdb-tmp/`, `test-roots/` | under 0.3 MB | runtime | eligible |
| `~/.local/share/cuneiform-ui/secrets/` | 8 KB | UI-local secrets; **not read** | owner decision; delete with the runtime, never copy into git |
| `cuneiform-research/.output/q025-refs/` | 15 MB | reading-aid reference downloads (eBL signs, OSL, Noto font, ORACC) | eligible; receipts are already in the research docs |
| `cuneiform-benchmarks/out/` | about 100 MB | raw stage outputs, review scratch, value-audit reference cache, predictions | eligible; hashes are committed in each `results/*/run.json` and `results/value-refs/manifest.json` |
| `~/.local/share/cuneiform-ui-demo` | absent | default home of `bin/demo` | — |

## Services and containers (stop or remove: parent action)

| Name | Kind | State | Ports / mounts |
|---|---|---|---|
| UI server pid 12251 | process (`server.app`) | listening | 127.0.0.1:8740 |
| parser build pid 10343 (+ `time` wrapper 10342) | process | running | none; writes the snapshot `.pending-*` |
| `cuneiform-ui-falkordb` | Docker, `falkordb/falkordb`, restart `on-failure` | Up 2 days | 6379/tcp internal; mounts `~/.local/share/cuneiform-ui/falkordb-conf` → `/run/cuneiform-ui` and `falkordb-data` → `/var/lib/falkordb/data` |
| `cuneiform-ui-falkordb-relay` | Docker, `falkordb/falkordb`, restart `on-failure` | Up 2 days | 127.0.0.1:26380 → 6379 |

Ruled out as unrelated, not touched: `companykit-falkordb`, `companykit-falkordb-browser`, the exited `trading-floor-falkordb-1`, and the CompanyKit, HomeLab and model-gateway Python listeners on 8767, 8800, 8899, 8901, 8912, 4174, 18123, 61252 and 61253.

## External references to runtime paths (cheap check)

- No LaunchAgents and no crontab entries mention cuneiform.
- `cuneiform-ui/bin/cuneiform-ui` defaults to `~/.local/share/cuneiform-ui` and `~/.local/share/cuneiform-research` (overridable with `CUNEIFORM_UI_HOME` and `CUNEIFORM_RESEARCH_ROOT`). `bin/demo` defaults to `~/.local/share/cuneiform-ui-demo`.
- `cuneiform-benchmarks/runconfig.json` points at the snapshot store, the backend pin and the research venv. These runs become unreproducible locally once the data is gone; they stay reproducible from raw + commits.
- AutoMem memories tagged `cuneiform-*` cite these paths and commits. They should be updated to mention the archive location after cleanup.

## Suggested order for the parent (not executed)

1. Resolve the parser worker: stop it or wait. Record its outcome. Commit or drop the 2 untracked research files.
2. Choose a **private** git destination. Add the remotes and push all three repos. Verify the remote heads.
3. Archive the small evidence: `receipts/`, `trust/`, `evidence/`, `runs/`, the snapshot manifests and ledger, and the `out/value-refs/` receipts. A tarball next to the backup would do.
4. Stop the UI (pid 12251) and remove the two `cuneiform-ui-falkordb*` containers.
5. Delete these runtime data paths:
   - `snapshots/` (after step 1)
   - `derived/`, `scratch/`, `raw/` (re-downloadable)
   - the UI `exports/` and `falkordb-*` directories
   - the repos' ignored `.output/`, `output/` and `out/`
   - then `venv/`
6. Re-check free space and confirm the remote copies before deleting the checkouts, if the checkouts are to go at all.
