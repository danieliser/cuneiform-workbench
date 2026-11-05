"""Glyph coverage of the transliteration layer: share of positions whose every written part maps to exactly one
sign with a verified (or eBL-only) code point. Usage: python -I tools/glyph_coverage.py <graph.duckdb> [out.json]"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import duckdb  # noqa: E402
from server import reading_aids as RA  # noqa: E402

db = duckdb.connect(sys.argv[1], read_only=True, config={'memory_limit': '1GB', 'threads': 2})
rows = db.execute("SELECT coalesce(o.reading, o.raw) t, o.kind, count(*) n FROM occurrence o JOIN line l ON l.id=o.line_id "
                  "JOIN sequence_version s ON s.id=l.sequence_id WHERE s.field='atf' GROUP BY 1, 2").fetchall()
c = collections.Counter()
for t, kind, n in rows:
    if kind == 'gap' or RA.FLAGS.sub('', t) in ('x', 'X', '...', '…', ''):
        c['broken-or-unreadable'] += n
        continue
    parts = RA.glyph_parts(t)
    if not parts:
        c['no-sign-token'] += n
    elif all(p['glyph'] for p in parts):
        c['all-parts-glyph'] += n
    elif any(p['glyph'] for p in parts):
        c['some-parts-glyph'] += n
    elif any(p['status'] == 'ambiguous' for p in parts):
        c['ambiguous-only'] += n
    else:
        c['no-glyph'] += n
total = sum(c.values())
legible = total - c['broken-or-unreadable'] - c['no-sign-token']
out = {'positions': total, 'counts': dict(c), 'share_of_legible_with_full_glyph': round(c['all-parts-glyph'] / legible, 4) if legible else None}
print(json.dumps(out, indent=1))
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
