# Results — cb-protocol-1, first bounded trials (2026-11-05)

Input: `snapshot:965aa26214dc0388dd3af1be367d2acc`, manifest `44a20685…3e84`. It holds 20,896 eligible artifacts: 2,714,629 signs positions on 311,100 lines and 1,827,524 ATF positions on 314,884 lines.

The backend was the read-only pin `057a0b7`. Preflight (`results/preflight.json`) ran the pinned `storage.verified(..., ledger='require')` once: every file hash, publication `ledgered`, 5.9 s. Each stage compared stat fingerprints before and after the run, and all were unchanged (`snapshot_stat_unchanged: true` in every `run.json`).

Split `df420d3e…79c`: 20,820 primary families.
- train: 12,520 families, 1,470,494 derived signs
- dev: 2,026 families
- test: 6,274 families, 721,445 derived signs

Primary families merge 100 artifacts through 43 shared CDLI numbers (candidate aliases), 30 through 13 exact-duplicate signs groups and 6 through 3 exact-duplicate ATF groups.

The uncertain near-duplicate links (3,475 edges, 2,777 artifacts) were used **only** to stratify results. Of the test families, 614 have a near-duplicate partner in train.

## Runs

| Stage | Run | Code | Wall / CPU s | Peak RSS | Key output hash |
|---|---|---|---|---|---|
| A | `A-20261105T220825Z-da32a56` | `da32a56` | 259 / 471 | 2.81 GiB | `queries.json`, `aggregate.csv` |
| B1 discovery | `B1-20261105T221333Z-cf8d6ff` | `cf8d6ff` | 13 / 13 | 1.24 GiB | `frozen.json` sha256 `186734ab…6979`, sealed in commit `a829440` before B2 |
| B2 confirmation | `B2-20261105T221444Z-6f8e05a` | `6f8e05a` | 21 / 27 | 1.48 GiB | `ranked.json`, `summary.json` |
| C | `C-20261105T221533Z-357520f` | `357520f` | 18 / 17 | 1.63 GiB | `summary.json`, `groups.csv`; raw predictions in `out/` (hash in `run.json`) |
| D | `D-20261105T221604Z-da7f694` | `da7f694` | 16 / 16 | 1.76 GiB | `packet_blinded.json` `53580bc7…6bd8`; key (ignored) `216ed84c…2c43` |

**Totals:**
- CPU is about 545 s across the official runs, plus about 1.5 minutes of preflight, the resource pilot, tests and coverage. That is about 11 of the 90 budgeted minutes.
- Disk: 1.8 MB committed results and 21 MB of ignored raw output. Free space stayed at 176.6 GiB or more.
- Load average was about 9–12 on 12 cores throughout, because the machine is shared.

**Resource note:** Stage A peaked at 2.81 GiB. That is under the 4 GiB maximum but **above the 2 GiB default**, because the in-memory oracle arrays and the DuckDB buffer (limit 2 GB) coexisted. B–D stayed under 1.8 GiB.

**Failed attempt:** the first B2 run crashed on a path bug after the bootstrap step and before writing or printing anything. The code was fixed in `6f8e05a` and the run repeated. The computation is deterministic, and the crash is logged in `out/failed-runs.log`.

**Tests:** 15 focused tests, all OK. Line coverage of `bench/` under the tests, measured with stdlib `trace` (coverage.py is not installed, and nothing was installed):

| Module | Coverage |
|---|---|
| `core` | 71% |
| `stage_c` | 36% |
| `stage_a` | 30% |
| `stage_b` | 18% |
| `preflight`, `stage_d` | 0% |
| total | 37% (463/1,253 lines) |

The stages' `main()` paths are exercised only by the official runs.

A mutation check confirms the tests catch real errors: making the oracle's wildcard refuse gaps makes the parity test fail.

## A. Query-path correctness and performance

**A1 and A2 hold on the full corpus.** For **82/82** predeclared queries, the independent streaming oracle (pure Python over the canonical `occurrence` positions) and the materialized index agree on all of the following:
- occurrence count
- exact span set
- distinct artifacts, witnesses and families
- the **complete** `A ? C` filler distribution, with gaps and unsupported positions as explicit categories

The query mix: signs exact n=2..5 (common, mid and rare strata), 16 frames, 6 bounded gaps `A B [0–2] C`, and 12 ATF exact queries. All counts in this section are frequencies of represented positions in the editorial source view; they are not ancient sign counts.

The pinned graph engine agrees on **14/14** subset queries, including `max_skip=2`.

Latency, index lookup through the pinned `projections.lookup` (count plus first page):

| Measure | p50 | p95 | Samples |
|---|---|---|---|
| process-cold | 375 ms | 1,105 ms | 82 queries |
| warm | 371 ms | 1,095 ms | 328 calls |
| oracle verification (separate) | 102 ms | 276 ms | 82 queries |
| graph engine | 0.49–1.58 s exact; 2.1–2.3 s bounded gap | — | 14 queries |

The OS cache was not flushed and its state is unknown. Cold and warm are equal, which means the index is not cache-bound: **every lookup is a full scan** of the length partition, using JSON extraction.

Bounded gaps cost about 1.1 s, because each one is three scans. Latency does not depend on selectivity: rare (count 1) and common (count 4,021) bigrams both take 370 ms.

Scaling on the existing read-only stage snapshots (24 queries each, warm p50):

| Records | n-gram rows | Warm p50 |
|---|---|---|
| 1,000 | 0.63M | 27 ms |
| 1,500 | 0.92M | 34 ms |
| 5,000 | 2.99M | 95 ms |
| 20,896 | 12.29M | about 371 ms |

Latency is linear in n-gram rows. That is acceptable for an interactive UI at this size, but **it does not scale to larger corpora without a key index on the leading token**. This is an engineering finding, not a correctness defect.

Sample `A ? C` filler distributions (occurrences / distinct families):

| Frame | Occurrences | Families | Distinct fillers | Leading fillers |
|---|---|---|---|---|
| `ABZ366 ? ABZ396` | 600 | 532 | 32 | ABZ13 496/441, ABZ75 24/24, ABZ328 9/9 |
| `ABZ411 ? ABZ142` | 1,117 | 776 | 62 | ABZ297 667/452, ABZ411 137/124, ABZ15 64/54 |
| `ABZ411 ? ABZ70` | 662 | 519 | 63 | ABZ15 140/90, ABZ411 137/114, …, **UNS 40/36** (unsupported positions are a visible filler) |
| `ABZ312 ? ABZ429` | 16 | 8 | 1 | 16 occurrences from 8 families |

The last row shows why a witness or family count is needed next to the occurrence count.

Limits:
- Agreement proves that the projection is faithful to canonical positions. It does not show that the parser or the readings are correct.
- Every query runs in the source policy with editorial restorations included. Strict is structurally empty.

## B. Formula and motif discovery with a sealed confirmation

The B0 resource pilot (train only) fixed T = 10, the first rule value. At that threshold there are 17,645 trigram types and 15,896 frame types with O ≥ 10, and the counting pass takes under 1 s. With the 3-family minimum, 17,384 trigrams and 15,866 frames were eligible. The frozen list holds 200 trigrams and 100 frames, each with a frequency-matched control, and no control was missing.

**Primary hypothesis H_B1 (preregistered; 1,000-rep family bootstrap):**

| Test family | Mean d_conf, candidates | Mean d_conf, controls | Difference [95% CI] | Without a near-duplicate partner in train |
|---|---|---|---|---|
| trigram ABC | 4.51 | −0.05 | **4.56 [4.39, 4.66]** | 3.95 [3.78, 4.06] |
| frame A?C | 3.70 | −0.15 | **3.85 [3.61, 3.98]** | 3.46 [3.23, 3.61] |

H_B1 is supported for both test families. Effect sizes shrink from discovery to confirmation: the mean d falls from 5.38 to 4.51 for trigrams and from 4.57 to 3.70 for frames. That is expected regression to the mean.

**Exploratory, uncorrected per-motif replication:** the 95% lower bound is above 0 for 185 of 200 trigram candidates against 13 of 200 controls, and for 83 of 100 frame candidates against 3 of 100 controls.

Negative cases with zero confirmation occurrences: one trigram (`ABZ537 ABZ449 ABZ313`) and three frames (`ABZ394 ? NU₁₁`, `ABZ377 ? ABZ344`, `ABZ159 ? ABZ84`).

Top exploratory motifs, ranked by confirmation lower bound (O/E is observed over expected; d is log2 excess):

| Kind | Motif | Disc. rank | Disc. O/E | Conf. O/E | d_conf [CI] | Families disc/conf | d_conf, no near-dup | Source example |
|---|---|---|---|---|---|---|---|---|
| trigram | ABZ515_9 ABZ579 ABZ515_9 | 1 | 103 / 0.23 | 53 / 0.12 | 6.44 [6.01, 6.76] | 77 / 43 | 6.04 | BM.40635 l.8 pos 2 |
| trigram | ABZ133 ABZ13 ABZ328 | 10 | 219 / 2.45 | 123 / 1.29 | 6.11 [5.87, 6.28] | 163 / 88 | 5.94 | 1881,0727.259 l.2 pos 12 |
| trigram | ABZ104 ABZ13 ABZ126 | 2 | 154 / 0.79 | 62 / 0.39 | 6.13 [5.78, 6.38] | 152 / 62 | 5.95 | K.3118 l.46 pos 7 |
| trigram | ABZ129a ABZ545 ABZ595 | 5 | 94 / 0.57 | 56 / 0.33 | 6.08 [5.22, 6.58] | 51 / 22 | 5.52 | K.9787 l.8 pos 0 |
| trigram | ABZ353 ABZ1 ABZ151 | 11 | 148 / 1.52 | 52 / 0.62 | 5.55 [5.16, 5.87] | 147 / 52 | 5.26 | K.2596 l.95 pos 0 |
| frame | ABZ515_9 ? ABZ515_9 | 1 | 108 / 0.24 | 62 / 0.15 | 6.59 [6.16, 6.89] | 80 / 48 | 6.10 | BM.40635 l.8 pos 2 |
| frame | ABZ51 ? ABZ331 | 6 | 50 / 0.42 | 33 / 0.30 | 5.40 [4.95, 5.72] | 49 / 33 | 5.31 | U.3966 l.3 pos 1 |
| frame | ABZ60 ? ABZ280 | 9 | 74 / 1.23 | 55 / 0.66 | 5.59 [4.73, 6.17] | 52 / 26 | 5.55 | Sm.1006 l.12 pos 0 |

The full ranked list, with denominators, maximum family share, discovery and confirmation examples (catalog key, line ID and label, position) and controls, is in `results/B2-…/ranked.json` and `ranked.csv`.

**Denominators:**
- confirmation: 535,221 test trigram windows, 6,274 test families, 92,477 test lines
- discovery: 1,091,238 train windows, 12,520 families

**What this does and does not show:**
- The order-1 null is weak: any multi-sign word, logogram spelling or stock phrase beats a bigram chain. Many top motifs are probably **orthographic or lexical units**, not formulae.
- Held-out families replicate the excess, but because composition labels are absent, test families can share compositions with train families. Removing the 614 test families with near-duplicate partners lowers the effect only slightly (4.56 → 3.95). So this is held-out-family replication of **recurrent sign sequences**. It is not independent-composition replication, and it is not semantics.
- No UI ranking change.

## C. Masked edited-sign completion diagnostic

There were 2,000 targets from 1,209 test families. They were drawn from 535,221 eligible test positions, with caps of 1 per line and 10 per family; 3 targets are OOV. The train statistics cover 1,470,494 derived signs, a vocabulary of 584, 49,398 frame-1 contexts and 743,807 frame-2 contexts.

All metrics are over all 2,000 targets. Abstentions and absent targets score 0, and the intervals are 95% family-bootstrap CIs.

| Method | Top-1 | Top-5 | MRR | Coverage | Top-1 when covered |
|---|---|---|---|---|---|
| unigram | 0.040 [0.031, 0.048] | 0.151 | 0.113 | 1.00 | 0.040 |
| left bigram | 0.112 [0.098, 0.126] | 0.313 | 0.217 | 1.00 | 0.112 |
| markov1 bidirectional | 0.218 [0.198, 0.236] | 0.442 | 0.330 | 1.00 | 0.218 |
| frame1 `L ? R` | 0.327 [0.304, 0.346] | 0.579 | 0.437 | 0.987 | 0.331 |
| frame2 `L2 L ? R R2` | 0.401 [0.376, 0.427] | 0.419 | 0.409 | 0.480 | 0.834 |
| **cascade** | **0.507 [0.481, 0.532]** | **0.663** | **0.581** | 1.00 | 0.507 |

The cascade beats markov1 by 0.264 to 0.315 top-1 (95% CI).

Breakdowns (cascade top-1):

| Group | n | Cascade top-1 | Notes |
|---|---|---|---|
| frame 2 seen in train | 960 | 0.834 | the train frame contains the target for 41.9% of all targets |
| frame 2 unseen | 1,040 | **0.204** | frame1 0.201, markov1 0.165 |
| near-duplicate partner in train | 610 | 0.685 | |
| no near-duplicate partner | 1,390 | 0.428 | |
| frame-1 support 0 | 27 | 0.111 | |
| frame-1 support 50+ | 1,344 | 0.540 | |
| target train frequency 100–999 | 76 | 0.342 | |
| target train frequency 1000+ | 1,919 | 0.514 | |
| OOV | 3 | 0 | |

**Interpretation for the next decision:**
- Most of the measured skill is retrieval of parallel text. Where no exact 5-gram frame exists, the best n-gram statistic gets about 20% top-1.
- A trained model is worth trying only if it beats the cascade on a fresh sealed split, **especially in the frame-2-unseen and no-near-duplicate strata**.
- This is edited-text completion of a reading-derived sign whose restoration status is unknown. It is not restoration of natural damage, and it is not comparable to EvaCun 2025, which masked 20% of complete words in ATF.

## D. Retrieval baseline and blinded packet

The paper-equivalent local baseline (Simonjetz et al. 2024 §5): binary sign n-grams with n = 1..3 and the overlap coefficient, plus a length × smoothed-IDF variant. TF-IDF cosine was added as a size-robust comparator. There were 12 dev queries over 20,896 artifact documents. Each query excluded its own primary family; independent parallels stayed in the pool. The top 10 of the three methods were pooled into **271 blinded items** (IDs and catalog keys only) in `results/D-…/packet_blinded.json`. The unblinding key is in `out/` (hash committed).

**Negative finding:** at artifact level the overlap coefficient is degenerate.
- For 9 of 12 queries the median top-10 candidate has only 3–7 n-grams, and 7 of 12 queries have a top-1 overlap score of 1.0. Tiny fragments whose few signs all occur in the query rank first.
- The paper avoided this by matching against large merged chapter documents, which do not exist here.
- Agreement between methods is near zero: the top-10 Jaccard between overlap and cosine is 0.00–0.05.

There are no gold labels, so **no accuracy claim** is made and the 0.94 P@3 is not replicated. The packet is for future expert judgment only.

## Claim boundary

| Kind | Status after this run |
|---|---|
| **Engineering correctness** | Established for the source-policy query paths. The independent oracle, index, SQL grouping and graph agree exactly on the full corpus (82 + 14 queries plus synthetic fixtures). Latency, RSS and disk were measured, and the index's linear-scan scaling is identified. |
| **Predictive edited-text diagnostic** | Measured on a family-held-out split. Held-out recurrent-sequence excess replicates (H_B1). Masked edited-sign completion reaches 50.7% top-1 with retrieval-dominated statistics and about 20% without an exact parallel. |
| **Philological validity** | **Not tested.** There are no expert labels, compositions, joins, natural-damage targets or translations. Nothing here certifies a formula, a reading, a restoration, a join or a semantic relation. |
