# Value audit protocol cb-value-1 (frozen before any dev score is computed)

Written 2026-11-05/06 before any reserved-dev completion score, tuning score or gold-retrieval score was computed. Parameters are in [runconfig-value.json](runconfig-value.json). Any change after a scoring run starts is reported as a deviation. This study is the final bounded falsification test of whether the local construction (`snapshot:965aa262…`, pinned backend `057a0b7`, graph + crosswalks + typed occurrences) gives **any** measured incremental advantage over existing tools or a straightforward raw-data baseline. It is not feature development.

Inputs are the same as `cb-protocol-1` (manifest `44a20685…3e84`; raw `fragments.json` sha256 `4e970d87…`). Raw corpus files are read-only; the stat fingerprint is compared before and after every run. Resources: ≤ 2 CPU-hours total for the whole study, 2 DuckDB threads, RSS soft abort 4 GiB, ≤ 5 GiB extra disk, ≥ 30 GiB free. No model downloads, no GPU, no paid inference, no LLM.

## 0. Facts fixed before design (observed, not assumed)

- The raw eBL record already carries period (`script.period`, 98% present), genre (48% present), museum, collection, description, notes, CDLI/BM numbers and ORACC project. The construction stores the raw record verbatim in `artifact.metadata`; its derived tables add **no** metadata beyond raw, and period is not parsed into a column. Therefore no E1 feature is construction-only: every feature is computable from raw `fragments.json`. **E1 can at most show a modeling advantage, never a construction advantage.** This is stated before results.
- Existing tools (feature matrix in `VALUE-VERDICT.md`, sources in `out/value-refs/receipts.log`): eBL serves public transliteration search (observed live, no login), and its open-source API contains sign/lemma/pattern matchers, `line_to_vec` fragment matching, global sequence alignment, parallel-line links and chapter manuscript alignment; the eBL n-gram matcher is public. ORACC-in-Korp offers CQP token sequences with 0–8 "any word" gaps, statistics/aggregation by attribute, genre/period filters and CSV export (word level, ORACC texts). CDLI offers grapheme-sequence search with regex, catalogue filters and downloads.

## E1. Completion: does our feature-based model beat credible baselines where retrieval is absent?

**Task:** masked single edited sign (signs field, reading-derived; may itself be an editorial restoration). Not natural-damage restoration. Target rule as Stage C: derived-sign target with derived-sign immediate neighbours in the same line.

**Splits (family-level, same split hash as cb-protocol-1):**
- `train-A` = train buckets 0–49, `train-B` = train buckets 50–59 (tuning targets only), `dev` = buckets 60–69 (sealed evaluation). The test split (70–99) was scored by Stage C and the reviewer and is **not used**.
- Dev contamination reserve: Stage D used 12 dev query artifacts and pooled candidates from every split. Every dev family containing a Stage D query artifact **or** any Stage D packet candidate is excluded. The excluded family list and its hash are written by `select`.
- Tuning statistics come from `train-A`; final dev statistics are refit on all train (0–59) for every method alike; reranker weights stay frozen.

**Strata (predeclared, computed from train statistics only, never from model output):**
- `S1 no-shared-8run`: the target's artifact shares **no** 8-sign run (consecutive derived signs in one line) with any training artifact, with **no document-frequency cap** (frequent runs >50 included). Artifacts with no 8-run count as sharing none.
- `S2 no-exact-frame`: the 5-sign frame `L2 L1 ? R1 R2` never occurs in training.
- `S3 no-restoration`: the artifact has ≥ 1 ATF reading position and **zero** ATF positions with `preservation='lost'`. Artifact-level label, not the target's truth. ATF lines whose label starts with `#` (known parser contamination: 129 lines in 49 texts) are excluded from this count; the manifest lists them.
- `ALL`: no stratum condition (context only).
- Each stratum draws its own targets: eligible positions sorted by `sha256(seed:target:line_id:position)`, caps 1 per line and 10 per family, up to **1,500** per stratum. If fewer exist, the available n is reported; criteria are never changed to reach n. Overlaps (S1∩S2, S1∩S3, S2∩S3, S1∩S2∩S3) are reported on the union.

**Methods (all trained on training lines only):**
- `unigram`, `markov1_bidir`, `cascade` (identical to Stage C).
- `longest_match` (LM): over training positions with the same immediate frame `L1 ? R1`, extend matching derived signs left (a) and right (b) up to K each; rank fillers by best a+b, then number of positions at that best, then cascade order; unranked fillers follow in cascade order. K ∈ {2,3,4,6,8} tuned on train-B.
- `rerank_raw`: conditional-logit (softmax over candidates) reranker over the union of top-10 cascade and top-10 LM candidates, L2 penalty λ ∈ {0.01,0.1,1,10,100} chosen by 2-fold family CV on train-B. Features: log counts of frame2, left-frame `L2 L1 ? R1`, right-frame `L1 ? R1 R2`, frame1; smoothed log p(x|L1), p(R1|x), p(x|R1), unigram; LM best a+b, a, b, log count at best; cascade/LM rank-1 indicators and −log ranks. All raw-sequence features.
- `rerank_meta`: `rerank_raw` plus metadata features: log count of `L1 ? R1` filler x among training artifacts with the same genre (first two category levels) and with the same period as the target artifact (0 when the target's value is missing, with an explicit missing interaction). Metadata comes from the target tablet's own record and training tablets, never from the answer. Same λ grid.
- Equal tuning budget: 5 configurations each for LM, `rerank_raw`, `rerank_meta`; 0 for the parameter-free baselines. Dev is evaluated **once**.

**Best baseline per stratum:** the higher-top-1 of `cascade` and `longest_match` on that stratum's **train-B** tuning targets (frozen before dev).

**Endpoints and claims (paired, 95% bootstrap, 1,000 reps, Poisson(1) weights over clusters = primary families joined by the uncertain near-duplicate links):**
- **C1 (modeling):** `rerank_meta` − best baseline, top-1, in each of S1, S2, S3.
- **C2 (metadata increment):** `rerank_meta` − `rerank_raw`, top-1, in each of S1, S2, S3.
- Practical threshold **+0.03 top-1** (3 points). Rationale fixed from the task, not results: with ~1,500 targets per stratum and ~15–25% discordant pairs the paired 95% half-width is about ±2 points, so 3 points is the smallest effect this design can separate from noise; it equals one more correct first suggestion per 33 masked signs, below which a reader using a ranked suggestion list would not notice a change.
- **Supported** iff in all three strata the lower bound > 0 **and** the point estimate ≥ 0.03. **Refuted** iff in all three strata the upper bound < 0.03. Otherwise **inconclusive**. A CI containing 0 is never read as proof of no effect.
- Secondary (descriptive): top-5, MRR, all methods, ALL stratum, overlaps.

## E2. Workflow/evidence tasks: construction vs ordinary raw-data implementation

Same answers, same information. "Ours" = read-only DuckDB over the snapshot's prebuilt tables (`ngram`, `token_context`, `artifact`, `claim`, `translation_segment`) on the pinned schema. "Raw" = one Python script over raw `fragments.json` that tokenizes the `signs` field with the pinned parser's rule (`conservative-3` derived-line tokenization: whitespace tokens, strip `[]?#`, X/…/(x) = gap, `[^\W\d][\w₀-₉]*` = sign, else unsupported) and builds in-memory dictionaries. Neither is a strawman: the raw baseline uses hash indexes; ours uses the shipped tables.

Tasks:
- **T1 variant aggregates:** for the 16 Stage A `A ? C` frames, the complete filler distribution with occurrence and distinct-artifact counts (gap/unsupported as categories).
- **T2 navigation to independent sources:** for 40 artifacts (20 hash-chosen with a CDLI number, 20 hash-chosen at large): CDLI number, other eligible artifacts claiming the same CDLI number, ORACC project, BM ID, genre, period, translation-line count.
- **T3 impact of a reading alternative:** for 10 sign labels (from the Stage A n=2 queries), if every occurrence of that label were reassigned (e.g. a sign-list revision), the number of affected signs n-gram windows (n = 2..5, source policy) and distinct artifacts affected.

Measured mechanically: answer identity (must be 100% or the task is reported as a defect), setup time to first answer (ours: connect; raw: parse + index; ours' one-time 22-min build is quoted from the Q-023 record), p50/p95 per-query latency (warm, 3 repeats), peak RSS, disk footprint, task-specific code lines.

- **C3 (construction engineering):** **supported** iff for ≥ 2 of 3 tasks the answers are identical and ours is interactive (p95 < 1 s) where raw is not, or ours' cold setup is ≥ 60 s shorter while per-query latency is no worse than 1 s; **refuted** iff raw meets the 1 s interactivity limit on all three tasks with identical answers and a cold setup under 60 s; otherwise inconclusive. No fictional expert time savings are claimed. The known raw ATF parsing bug is not counted as an advance.

## E3. Retrieval against published gold: eBL chapter manuscript lists

**Gold:** eBL corpus chapter manuscript lists (`/api/texts/{g}/{c}/{i}/chapters/Standard Babylonian/{name}/manuscripts`) for 20 SB chapters of Enūma eliš I–VII, Ludlul I–V, Erra I–V, Theodicy, Maqlû I–II (fetched 2026-11-06, ≤ 2 MB, receipts and sha256 in `out/value-refs/`). Editor-curated witness assignments (e.g. Lambert 2013 sigla) are independent of our construction. Manuscripts of type Commentary or Quotation are not witnesses of the chapter text and are excluded from queries and relevance (Quotation-inclusive sensitivity reported). Gold is incomplete: unlisted candidates are **unlabelled**, so only hit@k and recall@k over known associations are reported, never precision.

**Queries:** every in-corpus gold manuscript with ≥ 1 other in-corpus gold manuscript of the same chapter outside its own primary family. Relevant = the other in-corpus gold manuscripts of any chapter the query belongs to. The query's own artifact and primary family are excluded from candidates; fragments.json contains no chapter reconstructions (composites), so none can leak.

**Candidate universe:** all 20,896 artifacts (identical for all methods). Documents are artifact sign sequences (signs field).

**Methods:** `ovl` (Simonjetz et al. overlap coefficient, n=1..3), `ovl_len_tfidf`, `tfidf_cosine`, `bm25` (n=1..3, k1=1.2, b=0.75), `seed_align` (local diagonal seed alignment: maximum number of shared 3-sign seeds on one diagonal, BLAST-style, reranking the union of the top-200 of bm25 and tfidf_cosine), and the relation feature `bm25+genre`: bm25 top-200 reranked by the metadata relation "candidate shares the query's genre path (two levels)" first, then bm25. No tuning (no tuning gold exists).

- **C4 (relation feature):** `bm25+genre` − best baseline (max recall@10 over the five baselines, chosen on the evaluation itself, which favours the baseline), recall@10, chapter-cluster bootstrap. Threshold **+0.05 recall@10** (one more known witness in a ten-item review list per 20 queries; smaller differences cannot be resolved with ~90 queries). Supported iff LB > 0 and point ≥ 0.05; refuted iff UB < 0.05; else inconclusive. Circularity caveat stated in advance: eBL genre paths sometimes name the composition, so a genre win partly re-reads editor labels.
- Sensitivity: best method on ATF readings with restored (`lost`) positions removed, to bound inflation from editorial restoration copied from parallels.

## Decision rule (frozen)

- **GO** only if C3 is supported (a construction-attributable capability), or C4 is supported with an advantage the raw baseline cannot obtain at equal effort. A supported C1 or C2 alone would justify sharing a small completion baseline, not continuing the graph workbench, because every E1 feature is raw-computable.
- **NO-GO** otherwise. NO-GO means no credible advantage under the tested conditions; it does not prove nobody could ever benefit.
- Research novelty is judged separately from personal exploration value.

## Order of operations

1. Commit this protocol, the config and the code.
2. `value_e1 select` writes target manifests and stratum counts (no scores); commit.
3. `value_e1 tune` (train-A statistics, train-B targets only) writes `frozen.json`; commit.
4. `value_e1 evaluate --frozen …` once on dev. `value_e2` and `value_e3` once each. Commit results, deviations and `VALUE-VERDICT.md`.
