# Independent review: cb-protocol-1 first bounded trials (v0.1)

Reviewer: independent review agent, 2026-11-05. Reviewed `HEAD 4813349` (tree clean before and after). I did not use subagents and did not change any code, docs or commits. The only file written in git is this one. Scratch work went to the ignored `out/review/` (see its `INDEX.md`; 65 MB, disposable).

## Verdict (tl;dr)

All four stages **reproduce exactly** from my own loader and my own code. There is no split leakage and no change to the protocol after results. The pilot is acceptable as a pilot. Five claims need correcting before the results are cited:

| Stage | Reproduced | Supportable claim | Must change |
|---|---|---|---|
| A | 82/82 counts, artifacts, families and fillers from an independent loader; 15/15 tests | The index path is faithful to canonical positions. Each lookup is a full-table scan. | The cold/warm label and the cache inference (A-1). Name which path checked spans and fillers (A-2). |
| B | Frozen list rediscovered in identical order; 0/600 O/E/d mismatches; bootstrap CI matches | H_B1: held-out excess of recurrent sign sequences, robust in families that share **no** 8-sign run with train | The near-duplicate stratum hides most shared text (B-1, material). Most of the "regression to the mean" is a smoothing artifact (B-2). The size of d depends on its convention (B-2). |
| C | Same 2,000 targets; 0 rank mismatches across 6 methods × 2,000 targets; metrics and CIs match | Edited-text completion: 50.7% overall, about 20% without an exact frame | The 50.7% is mostly parallel retrieval and is inflated on restored artifacts (C-1, C-2, material for interpretation). It is not restoration accuracy. |
| D | Same 12 queries; all 360 top-10 ranks match the key; hashes match | Overlap is degenerate at artifact level (negative finding). The packet is valid for blinded labeling. | 9/12 should read 10/12. Disclose the tie-break effect and the third method (D-1 to D-3). |

## How I reproduced it (commands, exit codes, observations)

Interpreter: `~/.local/share/cuneiform-research/venv/bin/python` (duckdb 1.4.3, no numpy). DuckDB was `read_only`, 2 GB, 2 threads. Peak RSS was 1.44 GiB. Total CPU was about 4 minutes. Free disk stayed at 176 GiB or more.

| Step | Command (in `out/review/`) | Exit | Observation |
|---|---|---|---|
| Integrity, once | `python -B r0_integrity.py --verify` | 0 | `storage.verified(..., ledger='require')` returned `ledgered`, manifest `44a20685…3e84`, 6.0 s. The stat fingerprint (28 entries) equals `results/preflight.json` before, after, and again at the end of the review. |
| Plan check | `r0b_plan.py` | 0 | Lookup count query: `TABLE_SCAN … Type: Sequential Scan` on `ngram` with every predicate as a filter. The `ngram` table holds 12,292,380 rows (signs 7,961,529, ATF 4,330,851). |
| Independent loader | `r1_load.py` | 0 | Split sha256 `df420d3e…c79b` is identical. Same 20,820 families (12,520/2,026/6,274), same 43 alias groups (100 artifacts), same 13 signs and 3 ATF duplicate groups, same 2,714,629 signs and 1,827,524 ATF positions. |
| Stage A | `r5_stage_a.py` | 0 | Own scan over the 82 queries: 0 mismatches on count, distinct artifacts, distinct families, distinct fillers and top-10 filler counts. |
| Stage B | `r2_stage_b.py`, `r2b_strata.py` | 0 | Results in `r2_stage_b.json` and `r2b_strata.json` (below). |
| Stage C | `r3_stage_c.py`, `r3b_cluster.py` | 0 | Results in `r3_stage_c.json` and `r3b_cluster.json` (below). |
| Stage D | `r4_stage_d.py` | 0 | Results in `r4_stage_d.json` (below). |
| Writer tests | `python -B -m unittest discover -s tests -t .` (repo root) | 0 | `Ran 15 tests in 6.344s OK`. `git status` clean before and after. |

## Chronology and protocol integrity

- **Order is consistent with prespecification.**
  - `da32a56` (initial commit, 17:08:17) already contains `protocol.md`, `runconfig.json`, all stage code including `stage_c.py` and `stage_d.py`, and the tests.
  - Stage A started 8 seconds later (22:08:25Z).
  - `git diff da32a56 4813349 -- bench tests protocol.md runconfig.json README.md` shows two changes only: the dirty check ignoring `results/` (`core.py:91-93`) and `Path(args.frozen).resolve()` (`stage_b.py:194`). The protocol and runconfig are byte-unchanged.
- **The seal holds.**
  - `frozen.json` (sha256 `186734ab…6979`, re-hashed here) was committed in `a829440` at 17:13:56. The failed B2 started at 22:14:03Z, after that commit.
  - I rediscovered the frozen candidate list from train lines alone, in the identical order (`rediscovered_*_identical_order: true`), and the train transition digest matches. So the list is a deterministic function of train data, and the crash plus fix could not have changed it.
- **Limits.**
  - The timestamps are self-recorded in a single session, with no external notarization. Git cannot rule out exploration done earlier elsewhere (for example the Q-023 work in `cuneiform-research`).
  - The failed run's directories were deleted. Only a one-line note remains in `out/failed-runs.log`, with no traceback. Minor: keep failed-run stderr next time.
- **Gitleaks exceptions are not secrets.** They are four line-scoped fingerprints:
  - `ranked.json:19801` is the museum number `"catalog_key": "Ashm-1924.874"`.
  - D `run.json:53`, `run.json:60` and `summary.json:6` are the unblinding-key sha256 `216ed84c…2c43`.
- **Nothing vendored.** `bench/` contains only original code, and nothing comes from `ngram-matcher`. The citation (Simonjetz et al., LREC-COLING 2024, §5) is in `protocol.md:96`. I did not re-read the paper (no downloads), so "paper-equivalent" rests on the protocol's description.

## A. Query-path correctness and performance

**Reproduced.**
- 82/82 queries match my independent loader and scan.
- 14/14 graph-subset queries agree, per `graph_subset.json`. I did not re-run the graph engine.

**A-1. "Process-cold" is mislabeled, and the cache inference is unsupported (moderate, wording and protocol deviation).**
- `stage_a.py:212` opens **one** backend connection for all 82 queries. So "cold" (`stage_a.py:223-225`) is the first call for each query on a connection and buffer pool that earlier queries have already warmed. It is not a fresh process or a fresh connection, which `protocol.md:36` requires.
- Only Q000 is the first call on its connection. Preflight had also just read all 9.5 GB for hashing, so the OS cache was probably warm.
- So "Cold and warm are equal, which means the index is not cache-bound" (`RESULTS.md:71`) does not follow from these timings.
- The diagnosis itself is right, and it is supported by the query plan rather than the timings:
  - The count query is a sequential scan over the **whole** `ngram` table, both fields and all lengths. It is not a scan of "the length partition". The existing `ngram_scope` index is unused.
  - `projections.lookup` scans twice per call: a count, then an ordered page (`backend-pin/…/projections.py:210-211`). I measured 355–407 ms here for both rare and common bigrams.
- Fix: add a typed leading-token column with a sort or index, and avoid the second scan. Relabel the timings as "first-touch per query, warm connection".

**A-2. Which path checked what (minor, wording).**
- Spans, artifacts, witnesses, families and fillers were compared against the writer's own SQL over the `ngram` table (`stage_a.py:161-181, 234-241`).
- The backend `projections.lookup` path was compared on **count only**.
- The graph engine was compared on occurrences, witnesses and artifacts.
- `RESULTS.md` reads as if every check ran on the materialized index lookup. Name the path for each check.

**A-3. Bounded gaps cross breaks (minor, semantics).** The `?` wildcard matches gap and unsupported positions. So `A B [0–2] C` matches across lacunae, consistently in all engines. Document this.

## B. Motif discovery with sealed confirmation

**Reproduced exactly.**
- Eligible types: 17,384 trigrams and 15,866 frames.
- Controls:
  - All 300 are unique, and none equals a candidate.
  - All have |d_disc| ≤ 0.2473 and the same `floor(log2 O)` bin as their candidate.
  - The +0.5 smoothing and zero handling follow the protocol.
- Confirmation O, E and d: 0 mismatches against `ranked.json`. Point differences: 4.5596 (trigram) and 3.8517 (frame).
- My own Python-RNG Poisson family bootstrap gives [4.394, 4.661] and [3.623, 3.964], matching the writer's DuckDB-hash bootstrap.
- My re-implementation of the near-duplicate links gives 3,475 edges, 2,777 artifacts and 614 test families, identical to the writer.
- The null is calibrated on held-out data: pooled control O/E is 1.039 (trigram) and 0.972 (frame).

**B-1. The near-duplicate stratum misses most shared text (material, interpretation).**
- The detector ignores shingles with document frequency above 50 (`protocol.md:23`, `core.py:358`). It therefore drops exactly the most widely copied passages, and it also needs containment ≥ 0.5.
- Without a df cap, **2,327 of 6,274 test families (37%) share at least one 8-sign run with a train artifact**. Those families hold **428,058 of 535,221 (80%) test trigram windows**. 872 families have containment ≥ 0.5.
- In the families that share no 8-sign run with train:

| Test family | Difference [95% CI] | Pooled candidate O/E | Pooled control O/E |
|---|---|---|---|
| trigram | **2.05 [1.90, 2.23]** | 45.4 (63.3 in sharing families) | 1.17 |
| frame | **1.75 [1.55, 2.00]** | 17.4 (30.2 in sharing families) | 0.92 |

- **H_B1 survives the strict stratum.**
- But "Removing the 614 … lowers the effect only slightly (4.56 → 3.95)" (`RESULTS.md:139`) must not be read as evidence of low leakage. The detector does not see most of the leakage.
- The strict stratum is itself conservative: it also removes families that share genuine formulae. So the excess is bracketed between the two strata rather than measured.
- 143/200 trigram and 65/100 frame candidates still occur in no-shared-8-gram families.

**B-2. Mean d depends on its convention, and the "regression to the mean" is mostly a smoothing artifact (moderate).**
- Candidate E is tiny: the median E_conf is 0.23 (trigram) and 0.37 (frame). So d ≈ log2(2·O+1) is driven largely by the +0.5 term and by sample size.
- The same frozen data give a trigram difference from 5.96 down to 3.17, depending only on the smoothing constant:

| Smoothing constant | 0.05 | 0.1 | 0.5 | 1 | 2 |
|---|---|---|---|---|---|
| Trigram difference | 5.96 | 5.68 | 4.56 | 3.89 | 3.17 |
| Frame difference | 4.89 | 4.69 | 3.85 | 3.32 | 2.73 |

- The trigram shrink from discovery to confirmation (5.38 → 4.51) splits as follows:
  - **0.67 of the 0.87 bits** is expected with an unchanged O/E ratio, just from the smaller test counts (4.71).
  - The median raw O/E moves only from 87.6 to 80.1.
  - Frames: 4.57 → 4.06 is expected with an unchanged ratio, against 3.70 observed (median raw O/E 35.3 → 33.0).
- The same mechanics explain much of the drop in the strict stratum. If the all-test ratio held, the expected candidate d there would be 2.65, against 1.93 observed. So 4.51 → 2.65 is sample size and 2.65 → 1.93 is real attenuation.
- Fix: report "4.56 bits" as specific to the +0.5 convention, not as an effect size. Use pooled log2(O/E) with a family bootstrap when comparing strata.

**B-3. The confirmation test is easy to pass (moderate, acknowledged in spirit).**
- With E ≈ 0.2, a per-motif lower bound above 0 essentially means "occurs in enough test families". 199/200 trigram candidates occur in test at least once, 193 in at least 3 families, and 185 have a lower bound above 0.
- H_B1 is correctly prespecified and passes. But it is closer to "rare, sticky sequences recur" than to a test of formulae.
- I agree with `RESULTS.md` that many motifs are probably spellings or lexical units. There is no composition gold, so no formula claim.

**B-4. Candidates are not independent (minor).**
- 45/100 frame candidates have the same outer signs (A ? C) as a trigram candidate.
- 18 trigram candidates share a bigram with another candidate.
- 8 trigram and 6 frame candidates have A = C (for example `ABZ515_9 ABZ579 ABZ515_9`).
- The two "test families" therefore overlap, and the per-motif counts (185/200, 83/100) are not independent replications.

## C. Masked edited-sign completion

**Reproduced exactly.**
- The `predictions.json` hash equals the one in `run.json`.
- Metrics recomputed from the predictions equal `summary.json`.
- My independent target selection gives the same 535,221 eligible positions and the identical 2,000 targets.
- My own model reproduces every rank for all six methods (0 mismatches).
- Cascade top-1 is 0.507, CI [0.483, 0.530]. Cascade minus markov1 is [0.263, 0.314].

**No leakage found.**
- No target family and no target line is in train.
- Counts, vocabulary, backoff and tie order come from train lines only (`stage_c.py:125-126, 18-43`).
- The target never reaches `rank_lists`.
- There is no tuning, and dev is untouched.
- Abstentions and OOV score 0 over all 2,000 targets.
- Only 27 of 1,013 cascade hits were decided by a tie-break.

**C-1. The 50.7% is mostly retrieval of parallels (material for interpretation).** Corrected strata (n, cascade top-1):

| Stratum | n | Cascade top-1 |
|---|---|---|
| Target line appears verbatim in a train line | 243 (12%) | **0.988** |
| Artifact's 8-grams ≥ 50% found in train | 349 | 0.799 |
| Artifact's 8-grams 0–50% found in train | 1,283 | 0.487 |
| Artifact shares no 8-gram with train (or has none) | 368 | **≈0.30, CI [0.248, 0.344]** |
| Frame 2 unseen (writer's stratum) | 1,040 | 0.204, CI [0.178, 0.227] |
| Frame 2 seen, artifact shares no 8-gram | 102 | 0.588 |

**C-2. Restored artifacts score higher, a likely circularity (material for claim wording).**
- The ATF field marks bracketed readings with `preservation='lost'`. There is no signs↔ATF alignment, so this stratification is at artifact level only.
- Targets in artifacts with **no** restored ATF readings score **0.399** (n = 414). Artifacts with more than 25% restored readings score **0.600** (n = 745).
- The gap comes almost entirely from more exact-frame hits:
  - Frame 2 is seen for 35% of targets in unrestored artifacts and 51% in restored ones.
  - Within frame-2-unseen targets, accuracy is 0.198 vs 0.206.
- This fits editors restoring from the same parallels that the cascade retrieves, or canonical compositions being both more restored and better attested. Either way, the 50.7% partly scores the editor's restoration.
- **Do not present 50.7% as restoration accuracy.** The nearest proxy, on unrestored artifacts, is about 40%. Targets need both immediate neighbors present, so this is in-text completion, not natural-break restoration.

**C-3. Clustered uncertainty (minor).** Bootstrapping over primary families joined by near-duplicate links (1,065 clusters instead of 1,209 families) widens the cascade CI only slightly, to [0.477, 0.535].

## D. Retrieval baseline and blinded packet

**Reproduced exactly.**
- 20,896 documents and the same 12 queries (sampled from a dev pool of 1,607).
- All 360 top-10 ranks across three methods equal the unblinding key. The packet and key hashes match.
- The formulas are implemented as the protocol states:
  - The overlap coefficient is |F∩C|/min(|F|,|C|).
  - The weighted variant uses n²·IDF over the intersection, divided by the minimum weighted length. This is self-consistent and labeled as an interpretation.
- Exclusion removes only the query's own family. `excluded_same_family` is 1 for every query, meaning only the query itself. Independent near-duplicate parallels stay in the pool (2, for K.6853).
- The packet fields are IDs and catalog keys only, with no score or method.

**D-1. Tie-break bias in the pool (moderate for the packet).**
- Tie-breaking by artifact ID (`stage_d.py:78`) fills 22/120 overlap top-10 slots and 10/120 weighted top-10 slots.
- Query K.16666 has only 11 distinct n-grams, and 91 candidates tie at 1.0. Its whole overlap top-10 is therefore chosen by ID order, and those candidates are large (median 367 n-grams). This is the opposite degeneracy to the tiny-document one: a tiny query makes any large document that contains it score 1.0.
- The packet is still valid for blinded labeling. But the pool is not "the top 10 per method" in any meaningful sense for such queries.

**D-2. Count error (minor).** `RESULTS.md:183` says "9 of 12 queries". `summary.json` gives overlap top-10 medians of 7, 367, 6, 7, 7, 890, 7, 4, 7, 4, 7, 3, so it is **10 of 12** with 3–7 n-grams. The figures 7/12 with top-1 = 1.0 and Jaccard 0.00–0.053 are correct.

**D-3. Third method is a deviation (minor).** `tfidf_cosine` (`stage_d.py:71`) was in the code before the runs but not in `protocol.md` or `runconfig.json`. Under the protocol's own rule it is a deviation, and it contributes to the 271-item pool. Label it as one.

## Recommended next trials

1. **C2: a sealed completion trial on fresh families, against a retrieval baseline.**
   - Use the dev families as the sealed test. Stage C never scored them; Stage D only looked at 12 query artifacts, and those could be excluded. Carve any tuning slice out of train families.
   - Prespecify these strata as primary endpoints: (a) the artifact shares no 8-gram with train, (b) frame 2 unseen, (c) the artifact has no restored ATF readings.
   - Add a longest-match parallel-retrieval baseline (vote over train continuations of the longest matching left and right context). This separates retrieval from modeling.
   - Size the trial so each primary stratum has about 1,500 targets or more (the current strata have about 300–400, roughly ±4–5 points). Use a paired bootstrap clustered by families joined through near-duplicate links.
2. **B2′: a confirm-only re-analysis under prespecified leakage strata.** This needs no new discovery. Use the pooled log2(O/E) effect and the no-shared-8-gram stratum, and add an order-2 null for frames.

**Neural model: justified only as C2's challenger.**
- Train on train families only, and evaluate once on the sealed split.
- It must beat both the cascade and the longest-match retrieval baseline, with a paired lower bound above 0, in the no-shared-8-gram stratum (current cascade about 0.30 [0.25, 0.34]) and the frame-2-unseen stratum (0.20 [0.18, 0.23]).
- Winning overall alone is not enough, because overall wins can come from memorized parallels.
- Even a win remains edited-text completion, not restoration.

## Not findings

These are acknowledged limitations or out of scope:
- No expert gold, composition labels, joins or natural-damage targets.
- The order-1 null is weak.
- The strict policy is empty.
- Peak RSS reached 2.81 GiB in Stage A.

All are already stated in `RESULTS.md` and do not block a pilot.
