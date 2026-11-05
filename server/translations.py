"""Line-level placement of the source's own translation lines (eBL ATF `#tr` lines).

The backend stores every translation segment with granularity `unresolved-span` and a locator that is the byte
span of the whole `#tr…:` line in the record's ATF. Placement here follows eBL's own rule, read from the eBL code
(ebl-api `ebl/transliteration/domain/labels_validator.py`, commit 36c41664): a translation line belongs to the
text line immediately before it; with an extent `#tr.en.(r 3): …` it covers every text line from that one
through the line named in the extent, inclusive. A translation before any text line is invalid in eBL.

This is placement at line granularity only. It says which lines a translation renders, never which word renders
which sign. When an extent cannot be located in this edition's lines, the translation is shown after its first
line and the unresolved extent is reported, not guessed.
"""
from __future__ import annotations

import re

PREFIX_RE = re.compile(r'^#tr(?:\.([A-Za-z]{2,3}))?(?:\.\(([^)]*)\))?:\s?')
SURFACES = {'o': 'obverse', 'r': 'reverse', 'b.e.': 'bottom', 'e.': 'edge', 'l.e.': 'left', 'r.e.': 'right', 't.e.': 'top'}
ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10, 'xi': 11, 'xii': 12}
EXTENT_RE = re.compile(r"^(?:(o|r|b\.e\.|e\.|l\.e\.|r\.e\.|t\.e\.)[?!]?\s+)?(?:([ivx]+)('?)[?!]?\s+)?(\S+)$")


def parse_locator(locator: str) -> tuple[int, int] | None:
    parts = str(locator or '').split(':')
    if len(parts) == 4 and parts[0] == 'atf' and parts[1] == 'utf8' and parts[2].isdigit() and parts[3].isdigit():
        return int(parts[2]), int(parts[3])
    return None


def parse_extent(text: str) -> dict | None:
    """'(r ii 3\')' body -> {'surface': 'reverse', 'column': "2'", 'number': '3\''}; None when not in eBL form."""
    m = EXTENT_RE.match(text.strip())
    if not m:
        return None
    surf, col, prime, number = m.groups()
    if col and col not in ROMAN:
        return None
    return {'surface': SURFACES[surf] if surf else None, 'column': f'{ROMAN[col]}{prime}' if col else None, 'number': number}


def is_comment_line(line: dict) -> bool:
    return str(line.get('label') or '').startswith('#')


def _surface_of(line: dict) -> str | None:
    s = line.get('surface')
    return s.rstrip('?!') if isinstance(s, str) else None


def place(lines: list[dict], segments: list[dict], atf: bytes | None) -> list[dict]:
    """lines: [{id, ordinal, label, surface, column_label, start_byte, end_byte}] of one ATF sequence, in order.
    segments: [{id, language, text, locator}]. Returns one placement per segment, in source order."""
    # conservative-3 sometimes keeps a `#tr…`/`#note` line as a "text line" (label '#tr.en.(b.e' etc. when the
    # extent contains '. '); such rows are not text lines and are never counted as translated lines.
    lines = sorted((ln for ln in lines if not is_comment_line(ln)), key=lambda r: r['ordinal'])
    starts = [ln['start_byte'] for ln in lines]
    out = []
    for seg in segments:
        span = parse_locator(seg.get('locator'))
        p = {'id': seg.get('id'), 'language': seg.get('language'), 'text': seg.get('text'), 'start_byte': span[0] if span else None,
             'first_line': None, 'last_line': None, 'lines': 0, 'status': 'unplaced', 'extent': None}
        if span is None:
            p['status'] = 'no-locator'
            out.append(p)
            continue
        prior = [i for i, s in enumerate(starts) if lines[i]['end_byte'] <= span[0]]
        if not prior:
            p['status'] = 'before-first-line'
            out.append(p)
            continue
        i0 = prior[-1]
        raw_prefix = atf[span[0]:min(span[1], span[0] + 160)].decode('utf-8', 'replace') if atf else ''
        m = PREFIX_RE.match(raw_prefix)
        ext_text = m.group(2) if m else None
        i1 = i0
        if ext_text is None:
            p['status'] = 'line'
        else:
            p['extent'] = ext_text
            ext = parse_extent(ext_text)
            found = None
            if ext:
                for j in range(i0 + 1, len(lines)):
                    ln = lines[j]
                    if str(ln.get('label')) != ext['number']:
                        continue
                    if ext['surface'] and _surface_of(ln) != ext['surface']:
                        continue
                    if ext['column'] and str(ln.get('column_label') or '').rstrip('?!') != ext['column']:
                        continue
                    found = j
                    break
            if found is None:
                p['status'] = 'extent-unresolved'
            else:
                i1 = found
                p['status'] = 'extent'
        p['first_line'], p['last_line'], p['lines'] = lines[i0]['id'], lines[i1]['id'], i1 - i0 + 1
        out.append(p)
    out.sort(key=lambda x: (x['start_byte'] is None, x['start_byte'] or 0))
    return out
