# Benchmark protocol cb-protocol-1 (frozen before the main runs)

Written 2026-11-05, before any test or confirmation score was computed. Parameters are in [runconfig.json](runconfig.json). A change after a main run starts gets a new protocol version and is reported as a deviation.

## Input and what it is

- `snapshot:965aa26214dc0388dd3af1be367d2acc`, manifest `44a20685…3e84`: 20,896 eligible records (usable-text-v1) of eBL Zenodo 10018951 `fragments.json` (23,289 raw, 2,393 excluded), parser `conservative-3`, projection `linked-1`.
- Backend code: the read-only pin `057a0b7` in `~/.local/share/cuneiform-ui/backend-pin/`. We do not use the live UI worker's source.
- Integrity: the pinned `storage.verified()` (manifest, every file hash and the publication ledger) runs **once**, in the preflight. Every stage then compares a stat fingerprint (size, mtime_ns, ctime_ns, inode, mode for every snapshot file plus ledger bytes) before and after, and refuses on any difference. DuckDB connections are `read_only=True` with `memory_limit=2GB` and `threads=2`. There is no corpus copy; derived arrays live only in process memory.

**Data facts that bound every claim:**

- `signs` positions are sign labels **derived from the editorial reading**. They include editorial restorations, and every one has preservation and certainty set to `unknown`.
- No signs↔ATF alignment exists.
- The `strict` policy is structurally empty, so all results use the **source view**, editorial restorations included.
- There are no composition or chapter labels, no join data (`joins` is empty everywhere) and no prior text versions (only edit-event timestamps). Genre is missing for 52%, and period is not parsed.
- 43 CDLI P-numbers are shared by 100 eligible artifacts. These are candidate aliases, not proven identities.

## Units, families and splits

- **Primary family:** the union-find closure of (a) artifacts that share an exact CDLI P-number claim, (b) artifacts with identical normalized signs token sequences (at least 10 derived signs) and (c) artifacts with identical normalized ATF reading sequences (at least 10 readings). These links are labeled *candidate alias / exact duplicate*, not identity.
- **Split:** family-level, `sha256(seed:split:family_key)` mod 100. Buckets 0–59 are train (B discovery), 60–69 dev (D queries only) and 70–99 test (B confirmation, C test). The split ID list and its hash are written before any stage reads scores.
- **Near-duplicate sensitivity (uncertain):** two artifacts are linked when they share at least 5 hashed 8-shingles of consecutive derived signs, and their containment (shared shingles divided by the smaller set) is at least 0.5. Shingles with document frequency above 50 are ignored. These links may be genuine independent parallel witnesses. They are **never** deleted or merged into the split. They are used only to stratify results ("has a near-duplicate partner in train or discovery": yes/no).
- **Parallel copies:** these remain available at inference only when they lie in the training split. A test target's own family is never in training.

## Stage A: correctness and performance of the query paths

**Hypothesis A1:** for every predeclared query, the occurrence count from the index (`projections.lookup(...'motifs'...)` on the pin) equals the count from an independent streaming window oracle. The oracle is a pure-Python scan over canonical `occurrence` positions ordered by line ordinal and position, and it never reads `ngram`, `posting` or `edge`. Distinct witness, artifact and family counts must also agree, as must the **complete** filler distribution for `A ? C` frames (gap and unsupported positions are explicit categories).

**Hypothesis A2:** on a predeclared subset, the graph engine (`query.motif`) gives the same counts, including `A B [0–2] C` with `max_skip=2`. For the index path, the bounded gap is the union of the length-3, 4 and 5 patterns `[A,B,C]`, `[A,B,?,C]` and `[A,B,?,?,C]`.

**Query mix:** 82 queries; strata are in the runconfig. The seeded sample is drawn from the oracle's type frequencies, and frequency is used only to assign strata. The query list is written to disk before any index call.

**Timing definitions:**

- *Process-cold* is the first index call for that query in a fresh process with a fresh read-only connection. The OS page cache is **not** flushed and its state is unknown; the machine is shared, and load averages are recorded.
- *Warm* is 4 repeats on the same connection.

The report gives p50 and p95 per stratum with sample counts, and keeps query time separate from oracle (verification) time. It also gives stage wall and CPU time, peak RSS and output disk use.

**Optional scaling:** the same timing subset runs on the existing read-only 1,000, 1,500 and 5,000 record stage snapshots, if present. Timing only.

**Stop rule:** any count disagreement is a failure to report with the first five differing occurrences. It is never retuned away.

## Stage B: formula and motif discovery with a sealed confirmation

**Test families:** (T) trigrams `A B C` of three consecutive derived signs, and (F) frames `A ? C` with a derived middle. All windows stay inside one line, and a gap or unsupported position breaks the window (it is never concatenated).

**Null model:** an order-1 sign-transition chain fitted on **train lines only**: `p(y|x) = c(xy) / Σ_y c(xy)` over adjacent derived-sign pairs. The expectation is analytic and conditioned on the observed context slots: `E_ABC = N(A,B,sign)·p(C|B)` and `E_A?C = N(A,sign,sign)·Σ_B p(B|A)p(C|B)`. This keeps line boundaries, gap placement and run lengths exactly as observed, because only positions where a valid window exists contribute. There are no shuffles and no Monte Carlo p-values.

An order-2 null would reproduce trigrams by construction, so it is not used for trigrams. This is stated as a limitation: frame excess is measured against chained bigrams only.

**Effect:** `d = log2((O+0.5)/(E+0.5))`. The +0.5 applies to both terms and handles zero counts.

**Procedure:**

1. B0 (resource pilot, train only): count eligible types for each `min_count` in {10, 20, 40} and time the pass. The protocol rule fixes the threshold. No effect sizes are looked at.
2. B1 (discovery = train): eligible types have `O ≥ T` and occur in at least 3 discovery families. Take the top 200 trigrams and top 100 frames by `d_disc`, with ties broken by O descending and then label. Each candidate gets one frequency-matched control (same `floor(log2 O_disc)` bin, `|d_disc| ≤ 0.25`, chosen by hash without replacement). The frozen list is written with its sha256 and **committed before B2 runs**. Stage B1 drops every non-train line from memory right after split assignment, before any statistic is computed. Only identity structure (exact-duplicate keys, near-duplicate links) is computed over all artifacts.
3. B2 (confirmation = test): compute `O_conf` and `E_conf` with the train-fitted `p`, then bootstrap over confirmation families (Poisson(1) weights, 1,000 reps, fixed seed). B2 refuses to run if the frozen-list hash differs.

**Primary hypothesis H_B1, one per test family:** the mean `d_conf` of the candidates exceeds the mean `d_conf` of their controls, judged by the family-bootstrap 95% lower bound being above 0. That makes two tests in total. Individual motifs are **exploratory**: uncorrected 95% intervals, no BH or "significant" labels. Outputs are ranked motifs with discovery and confirmation counts, expectations, denominators (positions and families per split), family support, maximum single-family share, the near-duplicate-stratified `d_conf`, and source examples (artifact catalog key, line label, occurrence IDs).

**Claim limit:** replication across held-out families is not semantics. Because composition labels are absent, held-out families can still contain parallel copies of the same composition, so replication partly measures shared text across splits. The near-duplicate stratum quantifies part of that effect. UI ranking does not change.

## Stage C: masked-sign completion diagnostic (CuniGPT feasibility)

This is an **edited-text completion** task, not restoration of natural damage. The masked sign is a derived sign from the editorial reading, and it may itself be an editorial restoration (restoration status is unknown on the signs field).

**Targets:** 2,000 test-split positions whose target and both immediate neighbors are derived signs in the same line. Order is `sha256(seed:target:line_id:position)`, with caps of 1 per line and 10 per family. Context is L2 L1 [MASK] R1 R2. Context tokens are sign labels or the literals GAP, UNS, BOS and EOS, so gaps are kept as gaps.

**Training statistics** come from train-split lines only and are built before any test line is read. Test families never enter training. Because aliases and exact duplicates share a family, they cannot leak across.

**Methods:**

- unigram
- left bigram
- bidirectional order-1 Markov (`p(x|L1)·p(R1|x)`)
- exact frame retrieval (`L1 ? R1`, and `L2 L1 ? R1 R2`)
- a backoff cascade: the ranked list is frame2's fillers, then any remaining frame1 fillers, markov1, left-bigram and unigram candidates, deduplicated

The non-cascade methods abstain when they have no evidence. Ties are broken by score descending, train unigram count descending, then label ascending.

**Metrics:** top-1, top-5 and MRR over **all** targets (an abstention or an absent target scores 0), coverage, and top-1 given coverage. Each has a 95% family-bootstrap interval. OOV targets (absent from train) are reported separately and count as misses. Breakdowns:

- train frequency of the target
- frame-1 train support
- whether frame 2 was seen
- whether the target's artifact has a near-duplicate partner in train

The masked gap is a single position. There is no LLM and no tuning, so dev is unused.

**Decision use:** this is a statistical baseline for deciding whether a trained model is worth trying. A neural model would have to beat the cascade, and especially the no-exact-frame stratum, on a fresh sealed test.

## Stage D: retrieval baseline and blinded packet (only if the budget allows)

**Paper-equivalent local baseline:** Simonjetz et al., LREC-COLING 2024, §5. Each document is a binary set of sign n-grams with n = 1..3, scored by the overlap coefficient `|F∩C|/min(|F|,|C|)`. The weighted variant uses n² × smoothed IDF `log((1+|D|)/(1+df))`; the paper does not fully specify how the length and TF-IDF weights combine, so this combination is our interpretation.

**Deviations:**

- Documents are artifacts, not chapters (no chapter labels exist).
- Queries are 12 real dev artifacts, not synthetic broken fragments.
- There is **no gold, so no accuracy claim**. We do not claim to replicate the paper's 0.94.

**Exclusions:** each query excludes its own artifact and primary family. Independent parallels stay in the candidate pool. The top 10 of both methods are pooled, deduplicated and shuffled by seed into a blinded packet for future expert labeling. The packet lists IDs and catalog keys only, with no scores and no method. The unblinding key stays in the ignored output, and its hash is committed. The `ngram-matcher` repository has no license, so nothing is vendored.

## Budgets and stop rules

- Total CPU ≤ 90 minutes, 2 DuckDB threads, RSS soft abort at 4 GiB, ≤ 5 GiB of additional disk, ≥ 30 GiB free.
- Stages run sequentially. A stage that hits its budget writes partial results labeled `PARTIAL` and stops.
- D runs only if A–C finish under 60 cumulative CPU minutes.

## Claim boundary

| Kind | What this run can show |
|---|---|
| Engineering correctness | count, filler-distribution and grouped-support agreement among independent paths; latency, RSS and disk |
| Predictive edited-text diagnostic | how well n-gram statistics complete a masked edited sign, with a family-held-out split |
| Philological validity | **not tested**: no expert labels, no physical joins, no natural-damage restoration, no compositions |
