"""Shared helpers for the cb-value-1 value audit (read-only; see protocol-value.md)."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
from collections import defaultdict

from .core import SOURCE_ROOT, ROOT, SEED as SPLIT_SEED, UF, h

VCONFIG_PATH = Path(os.environ.get('CUNEIFORM_BENCH_VALUE_CONFIG', str(SOURCE_ROOT / 'runconfig-value.json'))).expanduser()
VCONFIG = json.loads(VCONFIG_PATH.read_text())
VSEED = VCONFIG['seed']


def vconfig_sha() -> str:
    return hashlib.sha256(VCONFIG_PATH.read_bytes()).hexdigest()


def bucket(family_key: str) -> int:
    """Same split hash as cb-protocol-1 (core.split_of), exposed as the bucket number."""
    return int(h(f'{SPLIT_SEED}:split:{family_key}')[:8], 16) % 100


def in_range(b: int, rng) -> bool:
    return rng[0] <= b <= rng[1]


def artifact_meta(db) -> dict:
    """artifact id -> (genre2 or None, period or None, raw record dict) from the verbatim raw record."""
    out = {}
    for aid, meta in db.execute('SELECT id, metadata FROM artifact').fetchall():
        r = json.loads(meta)
        genre = None
        if r.get('genres'):
            cat = r['genres'][0].get('category') or []
            if cat:
                genre = '>'.join(cat[:2])
        period = (r.get('script') or {}).get('period')
        if period in (None, '', 'None'):
            period = None
        out[aid] = (genre, period, r)
    return out


def atf_restoration(db) -> tuple[dict, list]:
    """Per artifact: (ATF reading positions, ATF lost positions) excluding contaminated '#'-label lines; plus contamination manifest."""
    rows = db.execute(
        "SELECT w.artifact_id, count(*) FILTER (WHERE o.kind='reading'), count(*) FILTER (WHERE o.preservation='lost') "
        "FROM occurrence o JOIN line l ON o.line_id=l.id JOIN sequence_version s ON s.id=l.sequence_id JOIN witness w ON w.id=s.witness_id "
        "WHERE s.field='atf' AND l.label NOT LIKE '#%' GROUP BY 1").fetchall()
    stats = {a: (n, lost) for a, n, lost in rows}
    contam = db.execute(
        "SELECT a.catalog_key, l.label, count(o.id) FROM line l JOIN sequence_version s ON s.id=l.sequence_id JOIN witness w ON w.id=s.witness_id "
        "JOIN artifact a ON a.id=w.artifact_id LEFT JOIN occurrence o ON o.line_id=l.id WHERE s.field='atf' AND l.label LIKE '#%' "
        "GROUP BY 1,2 ORDER BY 1,2").fetchall()
    return stats, [list(r) for r in contam]


def clusters(u) -> list:
    """Cluster id per artifact index: primary family joined by the uncertain near-duplicate links (bootstrap unit)."""
    fams = sorted(set(u.family))
    fi = {f: i for i, f in enumerate(fams)}
    uf = UF(len(fams))
    for a, partners in u.near['partners'].items():
        for b in partners:
            uf.union(fi[u.family[a]], fi[u.family[b]])
    return [uf.find(fi[u.family[i]]) for i in range(len(u.artifacts))]


def poisson1(rnd):
    x, k, p, cum = rnd.random(), 0, math.exp(-1), math.exp(-1)
    while x > cum and k < 20:
        k += 1
        p /= k
        cum += p
    return k


def paired_bootstrap(rows, a_key, b_key, cluster_key, reps, seed):
    """rows: dicts with 0/1 (or float) values under a_key/b_key. Returns point, 95% CI of mean(a)-mean(b) with cluster Poisson weights."""
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        c = by[r[cluster_key]]
        c[0] += 1
        c[1] += r[a_key]
        c[2] += r[b_key]
    keys = sorted(by, key=str)
    n = sum(by[k][0] for k in keys)
    if not n:
        return None
    point = sum(by[k][1] - by[k][2] for k in keys) / n
    rnd = random.Random(seed)
    diffs = []
    for _ in range(reps):
        sw = sd = 0.0
        for k in keys:
            w = poisson1(rnd)
            if w:
                c = by[k]
                sw += w * c[0]
                sd += w * (c[1] - c[2])
        diffs.append(sd / sw if sw else 0.0)
    diffs.sort()
    return {'n': n, 'clusters': len(keys), 'point': round(point, 4),
            'ci95': [round(diffs[int(0.025 * (reps - 1))], 4), round(diffs[int(round(0.975 * (reps - 1)))], 4)]}


def mean_ci(rows, key, cluster_key, reps, seed):
    for r in rows:
        r['_zero'] = 0
    res = paired_bootstrap(rows, key, '_zero', cluster_key, reps, seed)
    for r in rows:
        r.pop('_zero', None)
    return res


def verdict(point, ci, threshold):
    if ci is None:
        return 'inconclusive'
    if ci[0] > 0 and point >= threshold:
        return 'supported'
    if ci[1] < threshold:
        return 'refuted'
    return 'inconclusive'


def combine(verdicts: list) -> str:
    if verdicts and all(v == 'supported' for v in verdicts):
        return 'supported'
    if verdicts and all(v == 'refuted' for v in verdicts):
        return 'refuted'
    return 'inconclusive'
