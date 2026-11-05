"""Full-corpus check of translation placement against an independent scan of each record's raw ATF.

For every translation segment of the snapshot: the UI placement (server/translations.py over the canonical
`line` rows) is compared with a plain re-scan of the source ATF text (eBL text lines `<number>. …`).
Checks: (1) the first placed line is the raw text line directly before the `#tr` line; (2) for extents, the
number of raw text lines in the span equals the number of placed lines; (3) every segment gets a status.

Usage: python -I tools/check_translation_placement.py <graph.duckdb> [out.json]
"""
from __future__ import annotations

import bisect
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.translations import place  # noqa: E402

import duckdb  # noqa: E402

# eBL text line: optional "a+" prefix, number, optional prime (' or U+2032 or U+2019, UTF-8 bytes), optional letter, optional range.
TEXT_LINE = re.compile(rb"^(?:[A-Za-z]\+)?\d+(?:'|\xe2\x80\xb2|\xe2\x80\x99)?[A-Za-z]?(?:-\S+)?\. ")


def raw_text_lines(atf: bytes) -> list[int]:
    out, pos = [], 0
    for chunk in atf.split(b'\n'):
        if TEXT_LINE.match(chunk):
            out.append(pos)
        pos += len(chunk) + 1
    return out


def main() -> None:
    db = duckdb.connect(sys.argv[1], read_only=True, config={'memory_limit': '1GB', 'threads': 2})
    recs = [r[0] for r in db.execute('SELECT DISTINCT source_record_id FROM translation_segment ORDER BY 1').fetchall()]
    status = collections.Counter()
    problems = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    langs = collections.Counter()
    n = 0
    for sid in recs:
        atf = json.loads(db.execute('SELECT raw_json FROM source_record WHERE id=?', [sid]).fetchone()[0]).get('atf') or ''
        atf_b = atf.encode()
        seq = db.execute("SELECT id FROM sequence_version WHERE source_record_id=? AND field='atf'", [sid]).fetchone()
        lines = []
        if seq:
            cols = ['id', 'ordinal', 'label', 'surface', 'column_label', 'start_byte', 'end_byte']
            lines = [dict(zip(cols, r)) for r in db.execute(f"SELECT {', '.join(cols)} FROM line WHERE sequence_id=? ORDER BY ordinal", [seq[0]]).fetchall()]
        segs = [dict(zip(['id', 'language', 'text', 'locator'], r)) for r in
                db.execute('SELECT id, language, text, locator FROM translation_segment WHERE source_record_id=?', [sid]).fetchall()]
        placed = place(lines, segs, atf_b)
        raw = raw_text_lines(atf_b)
        by_id = {ln['id']: ln for ln in lines}
        for p in placed:
            n += 1
            status[p['status']] += 1
            langs[p['language']] += 1
            if p['first_line'] is None:
                continue
            k = bisect.bisect_right(raw, p['start_byte'] - 1) - 1
            first = by_id[p['first_line']]
            if k < 0 or raw[k] != first['start_byte']:
                problems['first-line-differs-from-raw-scan'] += 1
                if len(examples['first']) < 8:
                    examples['first'].append({'segment': p['id'], 'record': sid, 'placed_start': first['start_byte'], 'raw_start': raw[k] if k >= 0 else None})
                continue
            if p['status'] == 'extent':
                last = by_id[p['last_line']]
                raw_count = sum(1 for s in raw if first['start_byte'] <= s <= last['start_byte'])
                if raw_count != p['lines']:
                    problems['extent-line-count-differs'] += 1
                    if len(examples['extent']) < 8:
                        examples['extent'].append({'segment': p['id'], 'record': sid, 'placed': p['lines'], 'raw': raw_count})
    report = {'segments': n, 'records': len(recs), 'status': dict(status), 'languages': dict(langs), 'problems': dict(problems),
              'examples': examples}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
