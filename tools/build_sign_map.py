"""Build server/refdata/sign-map.json from fetched eBL sign records, cross-checked against ORACC OSL.

Inputs (all reference downloads, kept outside git):
  <ebl dir>      responses of tools/fetch_ebl_signs.py (+ receipts.json)
  <osl.asl>      ORACC OSL source file (CC0), oracc/osl at a pinned commit
Output: a compact JSON table, committed with the UI.

What is recorded, per ABZ label used in the snapshot's signs layer:
  verified    exactly one eBL sign lists this ABZ number, it has a Unicode code point sequence, and ORACC OSL gives
              the same code points for the same sign name;
  eBL-only    as verified, but OSL has no entry under that sign name (or no code point) to compare with;
  conflict    eBL and OSL disagree on the code points (never displayed as a glyph);
  same-glyph  several eBL signs list this ABZ number but all have the same code points (glyph certain, name not);
  ambiguous   several eBL signs list this ABZ number with different glyphs (all kept; never collapsed to one);
  no-unicode  the eBL sign has no Unicode code point;
  not-found   no eBL sign lists this number.
Reading -> sign: every value of every fetched sign (eBL `values`: value + subIndex, rendered as in ATF:
index 1 unmarked, 2..n as subscripts, no index as ₓ). A value listed by more than one sign is ambiguous.

Usage: python -I tools/build_sign_map.py <ebl dir> <osl.asl> <osl commit> <out.json>
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

SUB = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')


def atf_value(v: dict) -> str:
    s = v.get('subIndex')
    if s is None:
        return v['value'] + 'ₓ'
    return v['value'] if s == 1 else v['value'] + str(s).translate(SUB)


def osl_codes(path: Path) -> dict[str, list[int]]:
    """@sign / @form name -> code points from its `@list U+XXXXX` lines or `@ucun` text."""
    out: dict[str, list[int]] = {}
    cur = None
    for line in path.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^@(sign|form)\s+(\S+)', line)
        if m:
            cur = m.group(2)
            continue
        if line.startswith('@end'):
            cur = None if line.startswith('@end sign') else cur
            continue
        if cur is None:
            continue
        m = re.match(r'^@ucun\s+(\S+)', line)
        if m and cur not in out:
            out[cur] = [ord(c) for c in m.group(1)]
            continue
        m = re.match(r'^@list\s+U\+([0-9A-Fa-f]{4,6})$', line)
        if m and cur not in out:
            out[cur] = [int(m.group(1), 16)]
    return out


def main() -> None:
    ebl_dir, osl_path, osl_commit, out_path = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    receipts = json.loads((ebl_dir / 'receipts.json').read_text())
    osl = osl_codes(osl_path)
    abz: dict[str, dict] = {}
    signs: dict[str, dict] = {}
    for r in receipts['responses']:
        body = (ebl_dir / r['file']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == r['sha256'], r['file']
        found = json.loads(body)
        cands = []
        for s in found:
            name = s['name']
            uni = [int(c) for c in (s.get('unicode') or [])]
            o = osl.get(name)
            check = 'absent' if o is None else ('agrees' if o == uni else 'differs')
            cands.append({'name': name, 'unicode': uni, 'osl': check})
            abz_numbers = [l['number'] for l in s.get('lists', []) if l.get('name') == 'ABZ']
            signs[name] = {'unicode': uni, 'osl': check, 'abz': abz_numbers,
                           'values': sorted({atf_value(v) for v in s.get('values', []) if v.get('value')})}
        if not cands:
            status = 'not-found'
        elif len(cands) > 1:
            same = all(c['unicode'] and c['unicode'] == cands[0]['unicode'] for c in cands)
            ok = same and not any(c['osl'] == 'differs' for c in cands) and any(c['osl'] == 'agrees' for c in cands)
            # Several sign names, one code point sequence: the glyph is certain, the sign name is not.
            status = 'same-glyph' if ok else 'ambiguous'
        elif not cands[0]['unicode']:
            status = 'no-unicode'
        elif cands[0]['osl'] == 'differs':
            status = 'conflict'
        elif cands[0]['osl'] == 'agrees':
            status = 'verified'
        else:
            status = 'eBL-only'
        abz[r['label']] = {'status': status, 'signs': cands}
    values: dict[str, list[str]] = {}
    for name, s in signs.items():
        for v in s['values']:
            values.setdefault(v, []).append(name)
    out = {
        'meta': {
            'what': 'ABZ sign-list numbers used by the eBL release, mapped to Unicode cuneiform through eBL\'s own sign list, '
                    'cross-checked against ORACC OSL. Glyphs are standardized Unicode characters, not the tablet\'s handwriting.',
            'ebl': {'api': receipts['api'], 'query': 'GET /api/signs?listsName=ABZ&listsNumber=<n>', 'fetched_at': receipts['fetched_at'],
                    'responses': len(receipts['responses']),
                    'responses_sha256': hashlib.sha256(json.dumps([r['sha256'] for r in receipts['responses']]).encode()).hexdigest(),
                    'rights': 'eBL sign list (LMU Munich); facts only (sign name, list number, code points, value list) are kept here.'},
            'osl': {'source': f'https://raw.githubusercontent.com/oracc/osl/{osl_commit}/00lib/osl.asl', 'commit': osl_commit,
                    'sha256': hashlib.sha256(osl_path.read_bytes()).hexdigest(), 'rights': 'CC0 (stated in osl.asl)'},
            'caveat': 'The eBL release (Zenodo 10018951) does not state the sign-list revision used to compute its signs field; '
                      'this table is eBL\'s sign list as fetched, so a renumbering between release and fetch is possible. '
                      'tools/check_sign_map.py measures agreement with the release itself.',
        },
        'abz': abz,
        'signs': signs,
        'values': {v: sorted(n) for v, n in sorted(values.items())},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':'), sort_keys=True))
    from collections import Counter
    print(Counter(x['status'] for x in abz.values()), len(signs), 'signs,', len(values), 'values,',
          sum(1 for v in values.values() if len(v) > 1), 'ambiguous values;', out_path.stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
