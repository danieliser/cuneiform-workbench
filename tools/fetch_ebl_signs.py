"""Fetch eBL sign-list records for every ABZ number used by the snapshot's signs layer.

Reference download only (public eBL API, no authentication). For each label of the form ABZ<number>
the script asks eBL's own sign search for signs whose sign-list entry is ABZ <number>
(GET /api/signs?listsName=ABZ&listsNumber=<number>) and stores every raw response with its SHA-256.
Nothing is guessed: labels that are not ABZ numbers are not looked up.

Usage: python -I tools/fetch_ebl_signs.py <sign_labels.json> <empty output directory>
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = 'https://www.ebl.lmu.de/api/signs'
UA = 'cuneiform-workbench-reference-fetch/1 (local research, low rate)'
ABZ = re.compile(r'^ABZ(.+)$')


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f'{url}: HTTP {r.status}')
        return r.read()


def main() -> None:
    labels = [l for l, _ in json.loads(Path(sys.argv[1]).read_text())]
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    receipts = []
    for label in labels:
        m = ABZ.match(label)
        if not m:
            continue
        number = m.group(1)
        url = f'{API}?{urllib.parse.urlencode({"listsName": "ABZ", "listsNumber": number})}'
        body = get(url)
        name = f'abz-{hashlib.sha256(label.encode()).hexdigest()[:16]}.json'
        (out / name).write_bytes(body)
        receipts.append({'label': label, 'url': url, 'file': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
        time.sleep(0.35)
    (out / 'receipts.json').write_text(json.dumps({'fetched_at': datetime.now(timezone.utc).isoformat(), 'api': API,
                                                   'user_agent': UA, 'responses': receipts}, ensure_ascii=False, indent=1))
    print(len(receipts), 'responses,', sum(r['bytes'] for r in receipts), 'bytes')


if __name__ == '__main__':
    main()
