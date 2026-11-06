# Benchmarks

Executable protocols and historical results for the eligible eBL snapshot `snapshot:965aa26214dc0388dd3af1be367d2acc`. The corpus is not included. See [results](RESULTS.md), the [final value verdict](VALUE-VERDICT.md), [original protocol](protocol.md), and [value protocol](protocol-value.md). NO-GO means no measured construction advantage in these experiments, not that the app has no possible use.

## Test without a corpus

Install the root requirements, then run from the repository root:

```sh
(cd benchmarks && ../.venv/bin/python -B -m unittest discover -s tests -t . -v)
```

## Reproduce with your own inputs

Requires the eligible immutable snapshot and its publication ledger, the pinned backend, and for E2 the original raw fragments file; none is downloaded automatically. Results depend on the exact manifests, hashes, source rights and versions in the frozen configs. Missing inputs are failures, not new benchmark scores. Rebuilding the corpus is a separate opt-in operation.

```sh
# From the repository root; change these paths to inputs you have obtained lawfully.
export CUNEIFORM_UI_HOME="$PWD/output/benchmark-pin"
bin/cuneiform-ui pin-backend
export CUNEIFORM_BENCH_BACKEND="$CUNEIFORM_UI_HOME/backend-pin/057a0b757ed3dc05e486893bc344a15dd73c86d8"
export CUNEIFORM_BENCH_STORE="/path/to/snapshots"  # parent of snapshot:<id>
export CUNEIFORM_BENCH_RAW="/path/to/raw/fragments.json"  # E2 only
export CUNEIFORM_BENCH_RUNTIME="$PWD/output/benchmark-run"
mkdir -p "$CUNEIFORM_BENCH_RUNTIME"
# Supply historical packets consumed by later stages, without modifying the originals:
cp -R benchmarks/results "$CUNEIFORM_BENCH_RUNTIME/"
cd benchmarks
../.venv/bin/python -B -m bench.preflight
../.venv/bin/python -B -m bench.stage_a
../.venv/bin/python -B -m bench.stage_b discover
# Freeze/commit the selection before confirmation; follow protocol.md for the sequence.
# ../.venv/bin/python -B -m bench.stage_b confirm --frozen /path/to/frozen.json
../.venv/bin/python -B -m bench.stage_c
../.venv/bin/python -B -m bench.stage_d
```

Follow VALUE-VERDICT.md for the sealed E1 select → tune → evaluate sequence and E2/E3 prerequisites; do not treat the command list as a new sealed run. `CUNEIFORM_BENCH_CONFIG` and `CUNEIFORM_BENCH_VALUE_CONFIG` can select explicit configs. The original runconfigs remain byte-identical, including their historical paths; live path selection uses the environment variables above. Defaults use an isolated `output/benchmarks` runtime, never Daniel's former runtime. New run receipts include the supplied paths. Config changes require a new protocol/deviation record, not rewriting previous runs.

Historical runs, protocols and reviews are preserved unchanged. Their old paths and private-only recommendations describe the original experiment; [consolidation](../docs/CONSOLIDATION.md) records the owner's authorization to publish benchmarks. The eBL source-license conflict remains unresolved. Small catalogue/sign examples and their source notices remain; this repository grants no new source-data rights and ships no corpus payloads.
