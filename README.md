# cuneiform-benchmarks

These are the executable research protocol and measured results for the local eBL full eligible snapshot (`snapshot:965aa262…`). This project only reads the snapshot and the pinned backend. It never writes to `cuneiform-research`, `cuneiform-ui`, the snapshot store or any running service.

- [protocol.md](protocol.md) and [runconfig.json](runconfig.json): hypotheses, splits, metrics, thresholds, budgets and stop rules, frozen before the main runs.
- [RESULTS.md](RESULTS.md): measured results and the claim boundary.
- `bench/`: the harness. `preflight` verifies the snapshot once. Stage A is query correctness and performance, B is motif discovery with sealed confirmation, C is the masked-sign diagnostic and D is the retrieval packet.
- `results/<run_id>/`: committed aggregates (JSON/CSV) plus `run.json` (input hash, code commit, seeds, timings, resources).
- `out/<run_id>/`: ignored raw outputs, linked by sha256 from `run.json`. The raw outputs contain no full source redistribution.

Interpreter: `~/.local/share/cuneiform-research/venv/bin/python` (stdlib + duckdb 1.4.3; nothing installed).

```sh
PY=~/.local/share/cuneiform-research/venv/bin/python
$PY -B -m unittest discover -s tests -t .
$PY -B -m bench.preflight
$PY -B -m bench.stage_a
$PY -B -m bench.stage_b discover   # commit results/B1-*/frozen.json before:
$PY -B -m bench.stage_b confirm --frozen results/B1-…/frozen.json
$PY -B -m bench.stage_c
$PY -B -m bench.stage_d
```

Rights: the eBL rights conflict is unresolved, so this is private local research only. Do not push or redistribute this repository, its packets or its source examples.
