# Cuneiform Workbench

A local tool for browsing edited cuneiform texts, following their sources, and asking small, reproducible questions about represented sign sequences. It is a useful starting point for Assyriologists, students learning transliteration, and computational researchers inspecting a corpus before designing an experiment. It is experimental software; scholarly usefulness has not been established by a researcher study.

## Try it without a corpus or Docker

Requires Python **3.11 or newer**, Git, Bash and a modern browser. There is no Node build step. The backend is a separate dependency, pinned to `057a0b757ed3dc05e486893bc344a15dd73c86d8`; it is not bundled here. Obtain the existing `cuneiform-research` repository alongside this checkout, or set `CUNEIFORM_BACKEND_REPO` to its location. That repository must contain the pinned commit. No public clone URL is configured, so none is supplied here.

From this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bin/demo start
# Open http://127.0.0.1:8741/
bin/demo stop
```

The demo uses tiny **synthetic engineering fixtures**, not ancient evidence. Open **Change task → Where else does this sequence occur?**, enter `A B C` and run: expect **6 matches**. Try `A ? C`: expect **11**. Strict evidence policy returns zero because the parser does not infer certainty. Any translations in the fixtures are synthetic too; choose a sequence question for the reproducible counts above.

`bin/demo` exports the pinned backend read-only into a separate runtime under `~/.local/share/cuneiform-ui-demo`, creates fixture projections in memory, and never uses the canonical corpus store. No corpus download, Docker, API key, model or paid service is needed. Override `CUNEIFORM_DEMO_HOME`, `CUNEIFORM_UI_PORT`, or `CUNEIFORM_PY` if needed. Stop only the demo with `bin/demo stop`.

## Read and ask

With an eligible eBL snapshot configured, browse source translations, medical texts, omen openings, letters or literary compositions. Filter for English translations where available. Open a tablet, compare transliteration and available source translation, and select a reading for cited notes and occurrence examples. URLs retain the dataset, task and inputs for returning to a view.

Four current questions are implemented:

- Where else does this sequence occur? Exact or wildcard windows, with contextual results.
- What occurs between these signs? Group represented intervening positions.
- How is this reading used? Occurrences, catalogue groupings and source examples.
- What is written on this tablet? Edited lines and available translations with source links.

Advanced queries and bounded graph neighborhoods remain in the same package. Counts refer to represented positions in the selected dataset, not all historical uses of a word or sign. Matching patterns do not establish shared composition or identity.

## What you are looking at

**Transliteration** is an editor's sign reading, including uncertainty and restoration marks. **Glyphs** are standardized Unicode characters drawn by the bundled Noto font, not photographs or this tablet's handwriting. Sign-name → Unicode agreement and reading → sign assignment are checked separately; missing, ambiguous and disagreeing assignments remain visible without a guessed glyph. **Translation** is the source edition's rendering of a line or passage, not a sign-by-sign gloss. A translation attached after line 4 can render lines 1–4: attachment does not prove a range. Dictionary glosses and modern scholarly pronunciation notes are separate aids.

## Data and known limits

The locally qualified full snapshot is `snapshot:965aa26214dc0388dd3af1be367d2acc`: **20,896 eligible records of 23,289** in the fixed eBL Zenodo release **10018951**, with 2,393 recorded exclusions. This is neither all eBL nor all cuneiform, ORACC or CDLI. It contains no tablet photographs. Other local samples have narrower scope.

The pinned `conservative-3` parser misfiled **129 comment/translation lines (2,258 positions)** as text in 49 records. ATF sequence counts and reading examples can include them. The signs layer is clean. This UI deliberately preserves the snapshot and counts; a separate backend repair needs a new immutable snapshot and a subsequent compatibility review. Translation ranges, catalogue dating and incomplete genre labels also limit interpretation. A clean engineering check does not certify an editorial reading.

The full corpus is **not bundled or automatically downloaded**. Expect roughly **10 GiB for the full snapshot alone**, plus raw sources, environment, exports and working headroom. Download/ingestion is a separate, explicit opt-in workflow in the backend's [source audit](../cuneiform-research/docs/SOURCE-AUDIT.md), [ingestion design](../cuneiform-research/docs/INGESTION-DESIGN.md) and [publication protocol](../cuneiform-research/docs/PUBLICATION-PROTOCOL.md). Review source terms first. Configure `CUNEIFORM_RESEARCH_ROOT` only when deliberately using that store; see [operations](docs/OPERATIONS.md). No download or corpus build is performed by this quickstart.

## Evidence and possibilities

Existing tests establish bounded retrieval, source tracing, snapshot version handling and access controls. They do **not** establish scholarly discovery or restoration quality. Read the separate benchmark [RESULTS](../cuneiform-benchmarks/RESULTS.md), [protocol](../cuneiform-benchmarks/protocol.md) and [reviews](../cuneiform-benchmarks/reviews/), together with the [reading aids review](../cuneiform-research/reviews/reading-aids-review-v0.1.md), [source documentation](../cuneiform-research/docs/READING-AIDS-SOURCES.md) and [UI reviews](reviews/). These sibling links require the corresponding local repositories; they are not published URLs.

The [future experiments](docs/FUTURE-EXPERIMENTS.md) describe testing proposed interpretations for individual signs, groups of signs, words and phrases across their contexts, including reversible propagation and agent-assisted review. These, restoration suggestions, ranked semantic similarity and collaborative annotation are **future possibilities, not implemented or validated capabilities**. No restoration accuracy is claimed. Any future work needs philological evaluation, leakage-resistant comparisons and evidence that it helps researchers.

## Development and publication readiness

Runtime, security contracts, provenance receipts and earlier technical evidence are preserved in [OPERATIONS.md](docs/OPERATIONS.md) and [CHECKPOINT.md](CHECKPOINT.md). Optional FalkorDB is described separately there; it is unnecessary for demo/index queries, and the full snapshot exceeds its configured export cap. The UI binds to loopback and is not a hosted multi-user service.

Run `bin/cuneiform-ui test` in the configured local environment for the existing security and contract suite. The suite uses isolated roots; retained real-source sample checks need those local samples and are not proof of a fixture-only install. See the [verification report](docs/UI-SIMPLIFICATION.md) for the clean boot actually exercised.

Publication is **prepared, not launched**. There is currently no Git remote and no root software license. A maintainer must select a software license, make the pinned backend obtainable, review redistributed reference facts and source-data rights, and make sibling evidence links accessible before a public release. Code rights and corpus rights are separate: a future software license does not grant rights to eBL, ORACC or CDLI data. The bundled Noto font has its own [OFL](web/fonts/OFL.txt); OSL reference facts record CC0 provenance in `server/refdata/sign-map.json`. No public push, release or corpus redistribution is part of this work.
