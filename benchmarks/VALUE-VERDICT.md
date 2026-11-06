# Value verdict: cb-value-1 (2026-11-06)

## Recommendation: NO-GO for a standalone project

Under the conditions tested, the construction gives **no credible incremental advantage** over existing tools or a plain raw-data baseline. These parts were tested:

- the local graph workbench: snapshot `965aa262…`, backend pin `057a0b7`, typed occurrences, crosswalks and the n-gram/posting projections
- the features built on it

All four preregistered claims failed:

| Claim | Result |
|---|---|
| C3: construction-specific engineering value | **refuted** |
| C4: relation/metadata feature in gold retrieval | **refuted** |
| C2: metadata increment in completion | **refuted** |
| C1: feature-based completion model | **inconclusive**; refuted in the two main strata, underpowered in the third |

Even a positive C1 would not have counted for the construction. Every feature it uses can be computed from the raw eBL `fragments.json`, and this was stated before any result.

NO-GO is a statement about these tests. It does not prove that nobody could ever benefit.

**What this means for the shutdown decision.** The parent's conditional applies: experiment over. Back up the code and results, then remove the bulky runtime data. A data-cleanup checklist that preserves the evidence is in [SHUTDOWN-INVENTORY.md](SHUTDOWN-INVENTORY.md).

**Smallest thing worth keeping or sharing:**
- this repository: protocols, harness, results and this verdict, about 1.3 MB of git;
- the backend and UI git histories as an archive.

The rights caveat still applies. The eBL rights conflict is unresolved, and `results/` contains catalogue keys and sign-label examples. So: keep it private, or strip the examples from `results/` before any public release. No dataset needs keeping. The raw input is re-downloadable from Zenodo 10018951 (sha256 `4e970d87…`), and the 9.5 GB snapshot rebuilds from it plus commit `057a0b7` in about 22 minutes.

**Research novelty versus personal exploration.** Nothing here is new research. The pipeline, audits and negative results are a worked personal exploration. They show which ideas already exist (eBL, ORACC-in-Korp, CDLI) and how strongly simple retrieval explains the apparent signal. That has learning value, not research or product value.

## What was tested

The frozen protocol is [protocol-value.md](protocol-value.md) with [runconfig-value.json](runconfig-value.json), committed in `9683d10` before any score. The sequence:

1. Target manifests were sealed in `b005b33`.
2. Tuning on train-only data was sealed in `7e84828` (`frozen.json` sha256 `7cf8e4df…6617`).
3. The dev split was evaluated once, at code `7e84828`; results were committed in `86cd0ea`.
4. E2 and E3 were run once each.

The machine-readable claims and run manifest are in [results/value-verdict.json](results/value-verdict.json).

### Existing-tool feature matrix

Sources: primary documents and code, with receipts in `results/value-refs/manifest.json`. That is 29 HTTP requests (6.6 MB) and 2 web-search queries; nothing was downloaded beyond the receipts.

Status key: **observed** = seen live or in primary docs or code. **code** = present in the open-source tree, but behaviour not exercised. **unknown** = not verified. **n/a** = the tool does not cover eBL data.

| Capability | eBL (site + open API) | ORACC-in-Korp | CDLI | This construction | Raw-JSON baseline |
|---|---|---|---|---|---|
| Sign/transliteration sequence search on eBL fragments | **observed**: public `/api/fragments/query?transliteration=` with no login returned 8 fragments, matching line indices, period and genre in 10.8 s; `fragment_sign_matcher.py` | n/a (ORACC texts, word level) | grapheme-sequence search with regex **observed** (docs; CDLI corpus) | yes | yes (E2) |
| Gapped patterns (`A ? C`, bounded gaps) | `fragment_pattern_matcher.py` **code**; semantics unknown | **observed**: CQP "any word", repeated 0–8 times | regex **observed** | yes | yes |
| Variant/filler aggregation with counts | unknown | **observed**: Statistics tab by attribute; CSV/TSV/xls export | not documented | yes | yes, with identical answers (E2 T1) |
| Fragment ↔ fragment/chapter matching | **observed**: public `ngram-matcher` (overlap and TF-IDF variants); `line_to_vec` matcher and sequence alignment **code** | unknown | unknown | reimplementation only (E3) | yes |
| Chapter witness lists, manuscript alignment | **observed**: corpus manuscripts API (the E3 gold); alignment modules **code** | n/a | n/a | **none** (no composition labels) | n/a |
| Lemma / POS / gloss | lemma search **code** | **observed**: lemma, autolemma, POS, CDA gloss | unknown | ccpo pilot only (51 records) | n/a |
| Period / genre / provenience filters | period and genre in live results **observed** | genre and period **observed** | catalogue filters **observed** | raw record only; period not parsed | yes (raw fields) |
| Per-occurrence byte-offset provenance with hashed, ledgered snapshots | unknown (frozen release has a DOI) | unknown | unknown | **yes, unique here** (reviewed: 4.54M occurrences verified) | partly: offsets can be computed while tokenizing; no ledger |

The only cell unique to the construction is the last row: verified byte-offset provenance plus an immutable, ledgered snapshot. No tested task turned that into measured value over "sha256 of the raw file plus a deterministic script" (E2). It is an engineering-quality property, not a capability users lack.

## Claims

Each row gives the baseline, endpoint, result, practical threshold, extra resources, caveats and artefact. All confidence intervals are 95% Poisson bootstraps (1,000 reps), clustered as stated. A CI that includes 0 is never read as proof of no effect.

### C1: feature reranker vs the best baseline (completion, fresh dev families)

- **Task:** a masked single edited sign in the signs field. The sign is reading-derived and may itself be an editorial restoration. This is not natural-damage restoration.
- **Dev families:** buckets 60–69, never scored before. 34 dev families tied to Stage D queries or packet candidates were excluded (manifest in `V1select-…/selection.json`).
- **Training:** statistics from train families only. Clusters are primary families joined by near-duplicate links.
- **Best baseline:** longest-match parallel retrieval (LM, K=6) in every stratum, chosen on train-B before dev.

| Stratum (n, clusters) | cascade | LM (best baseline) | rerank_raw | rerank_meta | C1: meta − LM [CI] | Verdict (threshold +0.03) |
|---|---|---|---|---|---|---|
| S1 no shared 8-sign run with train, frequent runs included (1,500, 662) | 0.335 | 0.391 | 0.398 | 0.407 | **+0.017 [0.005, 0.028]** | refuted (UB < 0.03) |
| S2 no exact 5-sign frame (1,500, 634) | 0.224 | 0.324 | 0.328 | 0.329 | **+0.005 [−0.008, 0.018]** | refuted |
| S3 no editorial restorations (artifact-level ATF label) (**477**, 60) | 0.587 | 0.585 | 0.600 | 0.610 | **+0.025 [0.002, 0.049]** | inconclusive |
| ALL (context only) (1,500, 636) | 0.511 | 0.577 | 0.578 | 0.581 | +0.005 [−0.006, 0.015] | — |

**Combined: inconclusive by the frozen rule.** "Supported" needs all three strata to pass, and "refuted" needs all three to fail.

**Interpretation.** In the two strata that test anything beyond retrieval, the effect is ruled out at the practical size. S3 cannot be resolved: only 61 dev artifacts have no bracketed restorations, so n = 477 against the 1,500 aimed for, and the criteria were not changed.

**Supporting numbers:**

| Comparison | S1 | S2 | S3 |
|---|---|---|---|
| raw-feature reranker − LM | +0.007 [−0.003, 0.017] | +0.004 [−0.006, 0.013] | +0.015 [−0.004, 0.035] |
| top-5, LM → rerank_meta | 0.563 → 0.568 | 0.491 → 0.505 | 0.711 → 0.723 |

**Resources and caveats:**
- Extra resources: about 30 s CPU, 1.6 GB RSS, no extra data. No LLM, GPU or download.
- Leakage: none by design. The target is never passed to features. Metadata comes from the target tablet's own record and from training tablets. Clustered by near-duplicate links.
- Parity: the scan-based cascade reproduces Stage C exactly (1,013/2,000).
- Coverage: genre is known for about 48% of artifacts and period for 98%.

**Artefacts:** `results/V1select-20261106T000641Z-9683d10`, `results/V1tune-20261106T001003Z-13b5820`, `results/V1eval-20261106T001042Z-7e84828` (raw predictions in `out/`, hash in `run.json`).

### C2: metadata increment (rerank_meta − rerank_raw)

| Stratum | Difference [CI] | Verdict (threshold +0.03) |
|---|---|---|
| S1 | +0.009 [0.001, 0.017] | refuted |
| S2 | +0.001 [−0.008, 0.010] | refuted |
| S3 | +0.011 [−0.002, 0.023] | refuted |

**Refuted.** Genre and period conditioning do not add a practically useful amount. These fields are raw-JSON fields in any case.

### C3: construction vs ordinary raw implementation on three evidence tasks

- **Ours:** read-only DuckDB over the shipped tables.
- **Raw:** one Python script over `fragments.json` using the pinned tokenization rule, with in-memory dictionaries.
- **Answers:** identical on all three tasks (sha256-compared): **T1** filler distributions for 16 frames; **T2** source navigation for 40 artifacts; **T3** reading-reassignment impact for 10 labels.

| | Raw baseline | Construction |
|---|---|---|
| Setup to first answer | 5.1 s (parse + index) | 0.05 s connect (first answer 2.6 s). One-time build: about 22 min and 6.25 GB RSS (Q-023 record) |
| T1 latency p50 / p95 | < 0.001 s / < 0.001 s | 0.29 s / 3.40 s (JSON extraction over the 12.3M-row n-gram table) |
| T2 latency p50 / p95 | < 0.001 s / < 0.001 s | 0.003 s / 0.013 s |
| T3 latency p50 / p95 | 0.08 s / 0.34 s | 0.20 s / 0.33 s (posting variant; n-gram variant identical but slower) |
| Peak RSS | 0.94 GB | 1.10 GB |
| Disk | 74 MB raw input | 9.5 GB snapshot |
| Task code (lines) | 3 / 7 / 9 (setup 29) | 9 / 11 / 10 (setup 6) |

**Refuted.** The raw baseline is interactive on every task, sets up in under 60 s and gives the same answers.

A typed leading-token index would speed up "ours". It still could not win: the baseline is already under 1 ms per query.

No expert time savings are claimed.

**Artefact:** `results/V2-20261106T001124Z-86cd0ea`.

### C4: relation feature in retrieval against published gold

- **Gold:** eBL corpus chapter manuscript lists for 20 Standard Babylonian chapters: Enūma eliš I–VII, Ludlul I–V, Erra I–V, Theodicy, Maqlû I–II. That is 580 listed manuscripts, of which 76 are in the corpus and are neither commentary nor quotation.
- **Queries:** 69 queries in 13 chapters with at least one known co-witness outside the query's own family. This is fewer than the about 90 assumed in the threshold rationale, so the test is underpowered.
- **Labels:** the gold is incomplete. Unlisted candidates are unlabelled, so only recall and hit rate over known associations are reported.
- **Bootstrap:** clustered by chapter.

| Method (signs, n = 1..3, 20,896 artifacts) | hit@10 | recall@10 [CI] | recall@100 | MRR |
|---|---|---|---|---|
| overlap coefficient (Simonjetz et al.) | 0.072 | 0.008 [0.000, 0.014] | 0.020 | 0.030 |
| overlap, length × TF-IDF | 0.116 | 0.013 | 0.049 | 0.062 |
| TF-IDF cosine | 0.290 | 0.057 | 0.130 | 0.240 |
| **BM25 (best baseline)** | **0.420** | **0.082 [0.036, 0.127]** | 0.153 | 0.269 |
| diagonal seed alignment (rerank) | 0.290 | 0.049 | 0.135 | 0.243 |
| BM25 + same-genre relation | 0.319 | 0.057 [0.021, 0.091] | 0.151 | 0.221 |

**C4: BM25+genre − BM25 = −0.025 [−0.049, −0.003] → refuted.** The relation feature makes retrieval worse. Genre is missing for 35 of the 69 queries. Where present, it often groups whole classes such as "Literature", which promotes unrelated texts. Including quotation-type manuscripts (86 queries) does not change the ordering.

**Restoration sensitivity.** On ATF readings with restored positions removed (58 queries), every method collapses: BM25 recall@10 falls to 0.010. The ATF layer also has many unsupported positions, so this mixes removal of restorations with parse sparsity. It is not an estimate of restoration inflation.

**Artefact:** `results/V3-20261106T001329Z-a47ce1f`.

## Incidental measured facts

These were not preregistered claims, but the numbers are worth keeping.

- **Longest-match retrieval is a much stronger completion baseline than the Stage C cascade.** It gains +0.056 [0.038, 0.074] (S1), +0.100 [0.079, 0.121] (S2) and +0.065 [0.048, 0.084] (ALL) top-1. Any future model would have to beat LM, not the cascade. This is a raw-sequence technique, not a construction result.
- **BM25 beats the published overlap coefficient at artifact level.** It gains +0.074 [0.033, 0.113] recall@10. This matches the earlier finding that overlap is degenerate on small artifact documents. The eBL matcher was designed for merged chapter documents, so this is not a claim against eBL at its own task. Absolute recall stays low (0.08 at 10).
- **Coverage gap.** Only 76 of 580 chapter manuscripts (13%) are among the 20,896 eligible fragments here. That alone limits any use of this snapshot for chapter work.

## Deviations and negative process findings

1. **The reranker Newton solver diverged on one train-B CV fold** (CV top-1 about 0.01) in the first tune run. The run was moved to `out/failed-runs/` and logged. Step-halving line search was added in `13b5820` and tuning was re-run. This happened before any dev score existed.
2. **T3 variant choice (E2).** The construction side had two pre-coded T3 variants (n-gram and posting). The comparator automatically used the faster variant, which had identical answers. This rule was in the committed code but not in the protocol text. It favours the construction.
3. **Contamination count.** The selection manifest lists 51 distinct (artifact, label) groups of contaminated `#` ATF lines. The exclusion rule works on the label prefix and removes all 129 lines in 49 texts.
4. **Undersized samples.** S3 has n = 477 and E3 has 69 queries; both are below the design targets. The criteria were not changed.
5. **E2 resource record.** In E2, `run.json` stores `peak_rss_bytes` per side, and stage CPU counts only the parent process. The child subprocesses took about 114 s wall.
6. **Live UI state changed during the study.** The UI repo moved from `6354880` to `2a43151`, the UI process restarted (now pid 12251 on 8740), and a parser-repair full build started (pid 10343 writing `snapshots/.pending-…`, 12 GB). None of it was touched. The canonical snapshot's stat fingerprint was unchanged in every run, and the raw `fragments.json` sha256 was re-verified after the runs.

## Budget used

- **CPU:** about 7 minutes out of 120.
  - Official runs: select 17 s, tune 24 s, evaluate 31 s, E2 about 114 s, E3 100 s.
  - Failed tune: 25 s.
  - Debug, tests and probes: about 2 min.
- **Peak RSS:** 2.11 GB (E3).
- **Extra disk:** about 15 MB (`out/` plus results).
- **Free disk:** fell from 176 to 155 GiB. Most of that is the concurrent parser worker's 12 GB stage.
- **Network:** 29 requests, 6.6 MB.

## Reproduce

```sh
PY=~/.local/share/cuneiform-research/venv/bin/python
$PY -B -m unittest discover -s tests -t .            # 21 tests
$PY -B -m bench.preflight                            # once, if results/preflight.json is absent
$PY -B -m bench.value_e1 select
$PY -B -m bench.value_e1 tune --selection results/V1select-…/selection.json
$PY -B -m bench.value_e1 evaluate --selection results/V1select-…/selection.json --frozen results/V1tune-…/frozen.json
$PY -B -m bench.value_e2
$PY -B -m bench.value_e3                              # needs the gold files in out/value-refs/ebl (see results/value-refs/manifest.json)
gitleaks detect --no-banner
```
