# UI simplification and clean setup verification — 2026-11-05

Scope: UI package only; initial commit `6354880`. Backend pin `057a0b7` and canonical corpus unchanged. No research/benchmark repository writes, corpus downloads/builds, data deletion, public push or FalkorDB restart.

The prior desktop/mobile views gave a persistent nine-choice sidebar and repeated introductions substantial space. White/cool-grey surfaces, dark type and blue actions now frame one active task. Navigation, task explanations and examples use native disclosures. Reading-only is the default; glyphs remain an explicit option. Original URL keys, counts and evidence policies remain. In-page hash changes now re-render as well as fresh deep links. Source warnings stay visible.

Reading-aids review disposition:

- C1 parser contamination: no UI count/filter workaround, per task instruction. One visible warning explains the 129 comment lines / 2,258 positions; the backend fix is separate. Signs remain untouched.
- C2: a range-less translation says “attached after line N · no source range; may cover earlier lines.” This deliberately hedges every such attachment, including consecutive translated lines, rather than claiming semantic alignment. Raw placement and `unresolved-span` data are unchanged.
- C3: reproducible `tools/build_value_checks.py` compares the retained OSL enclosing sign/value declarations separately from sign-name Unicode checks. `ₓ` and different/additional sign names get no glyph, conservatively including possible aliases. No reading assignment is labelled independently verified. `du₈`, `gu₃`, `uₓ` regressions are covered; `ina` and `KUR` retain agreeing assignments. Source hash is in `server/refdata/value-checks.json`; OSL source URL/commit and rights remain in `sign-map.json`. This is a conservative aid, not a new sign-list edition or philological adjudication.
- C4: removed invented DIŠ editorial intention, qualified šumma with Borger's query, narrowed ana wording to Neo-Assyrian. Tablet periods say source catalogue.

The review's tolerant extent-matching finding F4 is not repaired here. Range placement still uses the pre-existing UI semantics, including missing surface/column tolerance; it is not an independent reproduction of eBL validation. The earlier operations evidence is preserved with a correction below. Backend parser/corpus acceptance remains a separate decision.

## Reproduced clean boot

A fresh Python 3.14 virtual environment installed only `duckdb==1.4.3` from `requirements.txt`. `bin/demo start` on **8743** exported pin `057a0b7` into `output/simplify/quickstart/backend-pin`, with a distinct research root that had no snapshots/evidence. No canonical store, sample copy or Docker was needed. Page `/` returned 200; `/api/status` listed only `synthetic-fixtures`; `/api/query` returned 6 for `A B C` and 11 for `A ? C`. `bin/demo stop` stopped it. `output/simplify/quickstart.log` retains the exact readback. A second supervised demo on 8742 supplied browser checks and is stopped at handoff. Terminal background-process cleanup required keeping that browser instance in a supervised session; the start/query/stop quickstart itself passed in one shell session.

The demo is a working engineering fixture, not useful source material for philology. A public newcomer still needs access to the separate backend repository containing the pin. The README makes this dependency explicit instead of inventing a clone or release URL.

## Evidence and remaining limits

Ignored `output/simplify/` retains install/test logs, isolated demo roots, API readbacks, before/after screenshots and an INDEX. Browser checks cover desktop 1280×800 and mobile 390×844, task navigation, reading/glyph toggle, reading inspector and deep links. Test and deployment receipts are recorded there. No scholarly-value claim follows from these checks.

Publication blockers: no configured remote, no root software license, no public backend distribution, sibling evidence links require local repositories, and reference/data redistribution rights need review. No publication occurred.

The full existing suite passed 72 tests plus 24 local isolation checks. After the value-check changes, the reading-aids suite passed 19 tests (including two new regression tests). JavaScript syntax, Bash syntax and `git diff --check` passed. Gitleaks scanned the tracked/new source content (excluding ignored runtime/scratch) and reported no leaks.
