# Consolidation verification — 5 November 2026

Code checked: `0358cb9` (test runner/docs introduced at `4634c3f`). Clean normal
Git clone, fresh `.venv`, Python 3.13.12, `duckdb==1.4.3` installed from the root
requirements. Clone and runtime were isolated under ignored `output/consolidation/`.
No corpus was imported and no full service was restarted.

## Executed checks

- `bin/test`: UI **74 tests, OK, 8 skipped** (44.948 s); current backend
  **87 tests, OK, 34 skipped** (16.058 s); benchmarks **21 tests, OK,
  1 skipped** (13.373 s). After the final benchmark subprocess path correction,
  benchmark suite repeated: **21 tests, OK, 1 skipped** (10.330 s).
- Skips explicitly cover absent retained source samples, optional FalkorDB,
  opt-in disk/storage tests and the missing full snapshot. No full-corpus
  requalification or new benchmark scores are claimed.
- Actual `bin/demo start`, with isolated `CUNEIFORM_DEMO_HOME` and port 18741:
  `/api/status` reported backend `057a0b757ed3dc05e486893bc344a15dd73c86d8`,
  compatibility `tested`. `POST /api/ask` sequence question returned **6**
  matches for `A B C`, **11** for `A ? C`. Main page HTTP **200**.
  `bin/demo stop` completed; the test service was stopped.
- Supplied benchmark runtime/store/backend/raw environment paths were read back;
  an actual Python child process launched from the source directory imported the
  pinned backend while outputs lived in a different directory.
- Git ancestor checks passed for original UI `286b7e9`, backend `2470caa`,
  benchmark `47c4b10`, and demo pin `057a0b7`. Historical backend/benchmark reviews,
  benchmark results and frozen configs/protocols compared byte-identically with
  the imported trees. Current landing docs and executable paths intentionally differ.
- `gitleaks git . --log-opts=--all --redact`: no leaks after six exact reviewed
  historical false-positive fingerprints. Initial findings were packet SHA256s,
  museum catalogue identifiers (including a quoted review example), and prose
  about tokens/uncertainty. No broad detector was disabled. No corpus/database
  archives, credentials or runtime payloads are tracked.
- GitHub rendered README fetched through the API: images present; **25** relative
  link/image references resolve locally and **8** remote screenshot files fetched
  successfully. The old UI URL returns **301** to the canonical workbench URL.

## Publication and retention

Canonical repository: https://github.com/danieliser/cuneiform-workbench (public,
branch `main`). Original backend and benchmark remotes now show redirect READMEs
and are archived. Backend remains public; the old benchmark remote remains private.
Benchmark history and results are included in the public unified repository.

Original local checkouts remain in place. The existing private backup was untouched.
Fresh pre-consolidation bundles for all three repositories are beside the checkouts
(`*-pre-consolidation-2026-11-05.bundle`), including the latest UI screenshots/docs.
Disposable clone/runtime removed after checks; local logs remain in ignored
`output/consolidation/`, described by its INDEX.md.

Remaining limits: no corpus, corpus-dependent tests skipped, atlas checker archival,
source-license conflict unresolved, no root software license selected, no measured
construction advantage or restoration/decipherment claim. Screenshots are historical.
