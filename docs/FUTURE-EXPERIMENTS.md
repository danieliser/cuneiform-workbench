# Future experiments

These are proposals, not implemented features, validated discoveries, or a commitment to continue development. The value audit found no demonstrated practical advantage for the tested construction. These semantic experiments were not evaluated by that audit.

## Does this interpretation fit?

A researcher could assign a proposed reading, value, gloss or meaning to an individual sign **or a group of signs**, and inspect every matching occurrence in the selected corpus with surrounding lines. The same workflow could target a word, phrase, or selected contextual sense. Preserve order, boundaries and the exact matching rule for groups; do not equate a sign sequence with a linguistic word automatically.

Two scopes would be available:

- **Literal sign or sign-group hypothesis:** apply the proposal across all represented matching occurrences, including uses that might be phonetic, logographic, determinative, or part of a longer expression.
- **Word or phrase hypothesis:** restrict the proposal to identified words, phrases or senses, optionally by language, period and genre. This requires linguistic annotation which the full eBL export does not supply.

The screen would show original readings, source translation where available, and a visibly separate proposed interpretation. A proposed English label is an experimental gloss, not an automatic translation of the surrounding sentence. No source text or accepted annotation would be overwritten.

Researchers could classify each context as supporting, contradicting, inconclusive, or a different use. Counterexamples should stay visible. Failure to fit every occurrence can indicate multiple senses, grammatical differences, a phonetic use, a different language, a mistaken sign identification, or a poor hypothesis. Conversely, a broad English label fitting everywhere is not proof of correctness.

Useful controls: compare competing hypotheses; inspect all matches with paging; group duplicate passages; filter or stratify by source metadata; keep missing metadata explicit; save and export an evidence packet containing supporting examples and the strongest counterexamples. Report counts over the entire selected scope, not just displayed examples.

## Agent-assisted hypothesis testing

An agent could propose alternatives, find contradictory contexts, compare dictionary senses and flag interpretations that merit specialist attention. A researcher could then propagate a proposal through explicitly recorded dependencies in both directions: from a sign or group to phrases and passages that use it, and from passage constraints back to candidate readings.

Dependency links must distinguish actual interpretive dependence from proximity or co-occurrence. Changing one proposal should flag dependent claims for re-evaluation, not silently change neighbouring signs. Each proposal needs its scope, author or model, evidence, alternatives, version, review state and rollback history. Multiple competing proposals can coexist.

Repeated automated passes could prioritize unresolved cases, but agreement with earlier model-generated interpretations is not independent evidence. Keep established anchors fixed, preserve disagreements and abstention, cap iterations, and stop when no new external evidence or held-out improvement appears. Do not promote a suggestion merely because thousands of passes converge.

## How to test whether this helps

Start with a small, well-annotated corpus and known competing senses. Compare against ordinary concordance search plus dictionary lookup. Reserve unseen compositions and account for parallel passages, editorial restorations and repeated copies before fitting proposals.

Measure whether specialists find useful counterexamples faster, whether proposals improve blinded expert judgments, how often they introduce errors, and whether improvements generalize to held-out texts. Evaluate sign, sign-group, word and phrase hypotheses separately. An agent's own confidence or fluency is not the evaluation target.

The first useful deliverable could be a reversible hypothesis overlay and exportable review packet. Autonomous dictionary revision would require substantially stronger evidence.

## Other possible extensions

- **Parallel-passage retrieval:** rank candidate parallels with standard retrieval baselines and show exact supporting spans. Similarity alone does not establish a join or shared composition.
- **Missing-reading suggestions:** show ranked candidates and their attested parallels. Evaluate natural damage separately from artificially masked edited text.
- **Annotation and review:** collect independent judgments, disagreements and cited corrections. This can support evaluation data; it is not automatically RLHF or permission to train a model on contributions.
- **Teaching:** compare readings, conventional glyphs, source translations and scholarly pronunciation resources without presenting glyphs as photographs or glosses as literal translations.

All extensions remain in the existing Questions or Explorations interface. No separate dashboard, image pack, trained model or hosted collaboration service is implied.
