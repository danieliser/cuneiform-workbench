# Repository consolidation

The unified public repository is https://github.com/danieliser/cuneiform-workbench.
UI history through `286b7e9`, backend history through `2470caa`, and benchmark
history through `47c4b10` were combined with non-squashed subtree merges. No
history was rewritten. The original backend commit `057a0b757ed3dc05e486893bc344a15dd73c86d8`
remains a reachable ancestor and `git archive` can export its original root paths.
The demo uses that export; current backend source is under `backend/`.

The original local checkouts remain in place. New pre-consolidation bundles were
created beside them, outside the existing `cuneiform-archive-2026-11-05` backup,
which was left unchanged. The UI remote was renamed rather than replaced.

## Historical paths and evidence

Old `cuneiform-research/…` paths refer to files now under `backend/…`;
`cuneiform-benchmarks/…` refers to `benchmarks/…`; old UI paths are at the root.
Absolute paths and runtime inventories in checkpoints, protocols, runconfigs,
reviews and historical runs describe the original development machine. They
are not installation requirements. Results, frozen protocols/configs and reviews
were retained byte-identically. Landing documents and executable path resolution
were updated; those edits do not revalidate past experiments.

The owner explicitly authorized public benchmark publication, superseding old
private-only/no-push recommendations in historical records. Source-license notices
and unresolved eBL terms still apply. No corpus payloads or runtime stores were
imported. Small source fixtures, catalogue/sign examples and reference facts retain
their attribution. No root software license was chosen.

## Atlas and queues

The backend atlas JSON, HTML and checker retain their original commit/path bindings.
They have **not** been ported or requalified against the unified multi-root tree.
Do not report the old atlas as a passing current check or treat its recorded corpus
coverage as installed data. To inspect its original scope, export the cited original
backend commit and read its recorded evidence. The corpus no longer exists locally,
so full runtime requalification is unavailable. Root development instructions apply;
old queues and agent procedures are historical references, not required reading for
users or a current task admission system.

## Verification scope

The clean-clone checks cover the pinned synthetic demo and local suites, not a corpus
rebuild, physical tablets, scholarly usefulness or restoration. Optional source-sample,
FalkorDB and disk tests must skip when their prerequisites are absent.
The history secret scan uses six exact historical false-positive fingerprints:
four packet-key SHA256/catalogue result matches, the same museum ID quoted in a
review, and specification prose about tokens/uncertainty. No broad rule is disabled.
See VERIFICATION.md for measured results.
