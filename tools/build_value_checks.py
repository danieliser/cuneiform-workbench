"""Build reading-value checks from the retained OSL file; no network access.
Usage: python tools/build_value_checks.py path/to/osl.asl
The sign-name Unicode check and value assignment check are separate.
"""
import hashlib
import json
import re
import sys
from pathlib import Path


def build(path):
    # Only the enclosing sign's own Unicode encoding counts here. Form
    # encodings must not overwrite a sign with the same name elsewhere.
    codes = {}
    sign = None
    in_form = False
    for line in path.read_text().splitlines():
        if line.startswith('@sign '):
            sign = line.split(None, 1)[1].strip()
            in_form = False
        elif line.startswith('@form'):
            in_form = True
        elif line.startswith('@@') or line.startswith('@end form'):
            in_form = False
        elif sign and not in_form and line.startswith('@ucun'):
            codes.setdefault(sign, [ord(c) for c in line[5:].strip() if ord(c) > 0x11fff])
    values = {}
    value_names = {}
    current = None
    parent = None
    for line in path.read_text().splitlines():
        m = re.match(r'^@(sign|form)\s+(\S+)', line)
        if m:
            current = m[2]
            if m[1] == 'sign':
                parent = current
        elif line.startswith('@end form'):
            current = parent
        elif line.startswith('@sign-') or line.startswith('@end sign'):
            current = parent = None
        else:
            m = re.match(r'^@v\s+(\S+)', line)
            if m and parent:
                # OSL forms can represent a value of the enclosing sign using a
                # different named form (e.g. gu₃ under KA in |KA×GAR|). Keep the
                # enclosing sign attribution too; do not erase that ambiguity.
                parts = re.sub(r'^%\S+\s+', '', line.split(None, 1)[1]).strip('/').split()
                if not parts:
                    continue
                value = parts[0].rstrip('?!')
                value_names.setdefault(value, set()).add(parent)
                if parent in codes:
                    values.setdefault(value, set()).add(tuple(codes[parent]))
    sm = json.loads((Path(__file__).resolve().parents[1] / 'server/refdata/sign-map.json').read_text())
    checks = {}
    for value, names in sm['values'].items():
        if len(names) != 1:
            continue
        c = tuple(sm['signs'][names[0]]['unicode'])
        alternatives = values.get(value, set())
        checks[value] = ('ambiguous-index' if 'ₓ' in value else
                         'not-in-osl' if value not in value_names else
                         'agrees' if value_names[value] == {names[0]} and alternatives == {c} else 'disagrees-or-ambiguous')
    return {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'note': 'OSL enclosing-sign names and Unicode sets compared with eBL assignments (different names suppressed conservatively, even possible aliases); agreement is not proof of a tablet reading.',
            'checks': checks}


if __name__ == '__main__':
    print(json.dumps(build(Path(sys.argv[1])), ensure_ascii=False, indent=1))
