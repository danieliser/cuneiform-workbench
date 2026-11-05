"""Measure the sign map against the eBL release itself.

The release carries, for each record, a transliteration (atf) and a sign-number field (signs) that eBL computed
from it. If the sign map (eBL's current sign list) still matches the list eBL used for the release, then expanding
each transliteration token into its signs (split at - . + and determinative braces), looking each value up in
the map and taking its ABZ number should reproduce the release's signs line, position by position.

Only lines where both layers exist, the line pairing is unambiguous and the expansion has the same length are
compared; everything else is counted as not comparable. Positions whose value maps to several signs are counted
separately. Usage: python -I tools/check_sign_map.py <graph.duckdb> <sign-map.json> [out.json] [every-nth-record]
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

import duckdb

FLAGS = re.compile(r'[\[\]#?!⸢⸣<>()*]')
SPLIT = re.compile(r'[-.+{}]+')


def expand(token: str) -> list[str] | None:
    """Signs written by one transliteration token. Non-sign tokens (language shifts %xx, column separators &,
    `($…$)` notes, whole-token scribal omissions <…>) write no sign and expand to []."""
    if token.startswith(('%', '&', '($')) or re.fullmatch(r'<[^<>]*>[#?!]*', token):
        return []
    t = FLAGS.sub('', token)
    if not t or ':' in t or '|' in t or '@' in t or '~' in t or '/' in t:
        return None
    parts = [p for p in SPLIT.split(t) if p]
    return parts or None


def key(part: str) -> str | None:
    if part in ('x', 'X'):
        return 'X'
    if re.fullmatch(r'\d+', part):
        return None
    return part.lower() if part.upper() == part and part.lower() != part else part


def main() -> None:
    db = duckdb.connect(sys.argv[1], read_only=True, config={'memory_limit': '1GB', 'threads': 2})
    m = json.loads(Path(sys.argv[2]).read_text())
    every = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    values, signs = m['values'], m['signs']
    c = collections.Counter()
    bad_examples = collections.Counter()
    recs = [r[0] for r in db.execute('SELECT source_record_id FROM sequence_version GROUP BY 1 HAVING count(DISTINCT field)=2 ORDER BY 1').fetchall()]
    for n, sid in enumerate(recs):
        if n % every:
            continue
        seqs = dict(db.execute('SELECT field, id FROM sequence_version WHERE source_record_id=?', [sid]).fetchall())
        layer = {}
        for field in ('atf', 'signs'):
            rows = db.execute('SELECT l.ordinal, l.label, o.position, o.raw, o.reading FROM line l JOIN occurrence o ON o.line_id=l.id '
                              'WHERE l.sequence_id=? ORDER BY l.ordinal, o.position', [seqs[field]]).fetchall()
            lines: dict[int, list] = {}
            labels = {}
            for ordinal, label, _pos, raw, reading in rows:
                lines.setdefault(ordinal, []).append(raw)
                labels[ordinal] = label
            layer[field] = [lines[k] for k in sorted(lines) if not str(labels[k] or '').startswith('#')]
        if len(layer['atf']) != len(layer['signs']):
            c['records-line-count-differs'] += 1
            continue
        c['records-compared'] += 1
        for atf_line, sign_line in zip(layer['atf'], layer['signs']):
            parts = []
            for tok in atf_line:
                e = expand(tok)
                if e is None:
                    parts = None
                    break
                parts += e
            if parts is not None and not parts:
                parts = None
            if parts is None or len(parts) != len(sign_line):
                c['lines-not-comparable'] += 1
                continue
            c['lines-compared'] += 1
            for part, label in zip(parts, sign_line):
                k = key(part)
                if k is None:
                    c['pos-number-skipped'] += 1
                    continue
                if k == 'X':
                    c['pos-x'] += 1
                    continue
                cands = values.get(k)
                if not cands:
                    c['pos-value-not-in-map'] += 1
                    continue
                if not label.startswith('ABZ'):
                    c['pos-release-label-not-abz'] += 1
                    continue
                num = label[3:]
                ok = [s for s in cands if num in signs[s]['abz']]
                if len(cands) > 1:
                    c['pos-ambiguous-value-' + ('one-candidate-agrees' if ok else 'no-candidate-agrees')] += 1
                elif ok:
                    c['pos-agree'] += 1
                else:
                    c['pos-disagree'] += 1
                    bad_examples[(k, label, cands[0])] += 1
    agree, dis = c['pos-agree'], c['pos-disagree']
    report = {'counts': dict(c), 'agreement_unique_values': round(agree / (agree + dis), 6) if agree + dis else None,
              'top_disagreements': [{'value': k, 'release_label': l, 'map_sign': s, 'n': n} for (k, l, s), n in bad_examples.most_common(25)],
              'sampled_every_nth_record': every}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
