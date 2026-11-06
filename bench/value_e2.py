"""E2 (cb-value-1): three evidence-workflow tasks, construction (prebuilt snapshot tables) vs an ordinary raw-JSON implementation.

Each side runs in its own subprocess (separate RSS). Answers must be identical; latency, setup, RSS, disk and code size are measured.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from .core import ROOT, SNAPSHOT, Run, h, peak_rss
from .value_common import VCONFIG, VSEED, vconfig_sha

E = VCONFIG['e2']
RAW = Path(os.path.expanduser('~/.local/share/cuneiform-research/raw/sha256')) / VCONFIG['raw_fragments_sha256']
A_RUN = ROOT / 'results' / 'A-20261105T220825Z-da32a56'


def genre2(rec):
    g = rec.get('genres') or []
    cat = (g[0].get('category') or []) if g else []
    return '>'.join(cat[:2]) if cat else None


def period(rec):
    p = (rec.get('script') or {}).get('period')
    return None if p in (None, '', 'None') else p


TR = re.compile(r'#tr\.([a-z-]+)(?:\.\([^)]*\))?:\s*(.*)')


# ------------------------------------------------------------------ predeclared task inputs (no answers)

def task_inputs():
    q = json.loads((A_RUN / 'queries.json').read_text())
    frames = [e['pattern'] for e in q if e['kind'] == 'frame' and e['field'] == 'signs']
    labels = []
    for e in q:
        if e['kind'] == 'exact' and e['field'] == 'signs' and e['length'] == 2 and e['pattern'][0] not in labels:
            labels.append(e['pattern'][0])
    labels = labels[:E['t3_labels']]
    cohort = json.loads((SNAPSHOT / 'cohort.json').read_text())['records']
    with_cdli = sorted((r['source_key'] for r in cohort if r['metadata'].get('cdli')), key=lambda k: h(f'{VSEED}:t2c:{k}'))[:E['t2_with_cdli']]
    rest = sorted((r['source_key'] for r in cohort if r['source_key'] not in with_cdli), key=lambda k: h(f'{VSEED}:t2r:{k}'))[:E['t2_random']]
    return {'frames': frames, 'labels': labels, 'artifacts': with_cdli + rest}


# ------------------------------------------------------------------ construction side

def ours_setup():
    import duckdb
    db = duckdb.connect(str(SNAPSHOT / 'graph.duckdb'), read_only=True)
    db.execute(f"SET threads={VCONFIG['resources']['duckdb_threads']}")
    db.execute(f"SET memory_limit='{VCONFIG['resources']['duckdb_memory_limit']}'")
    return db


def ours_t1(db, frame):
    a, _, c = frame
    rows = db.execute(
        "SELECT coalesce(json_extract_string(n.readings,'$[1]'), CASE WHEN o.kind='gap' THEN 'GAP' ELSE 'UNS' END) f, count(*), "
        "list(DISTINCT x.catalog_key) FROM ngram n JOIN artifact x ON x.id=n.artifact_id "
        "LEFT JOIN occurrence o ON json_extract_string(n.readings,'$[1]') IS NULL AND o.id=json_extract_string(n.occurrence_ids,'$[1]') "
        "WHERE n.field='signs' AND n.policy='source' AND n.length=3 AND json_extract_string(n.readings,'$[0]')=? "
        "AND json_extract_string(n.readings,'$[2]')=? GROUP BY 1", [a, c]).fetchall()
    return {f: [n, len(arts)] for f, n, arts in rows}


def ours_t2(db, key):
    aid, meta, src = db.execute('SELECT id, metadata, source_record_id FROM artifact WHERE catalog_key=?', [key]).fetchone()
    rec = json.loads(meta)
    others = db.execute(
        "SELECT a2.catalog_key FROM claim c1 JOIN claim c2 ON c2.object_key=c1.object_key AND c2.subject_id<>c1.subject_id "
        "JOIN artifact a2 ON a2.id=c2.subject_id WHERE c1.subject_id=? AND c1.predicate='catalog_alias' AND c2.predicate='catalog_alias' "
        "ORDER BY 1", [aid]).fetchall()
    ntr = db.execute('SELECT count(*) FROM translation_segment WHERE source_record_id=?', [src]).fetchone()[0]
    ext = rec.get('externalNumbers') or {}
    return {'cdli': ext.get('cdliNumber') or None, 'same_cdli': [o[0] for o in others], 'oracc_project': rec.get('editedInOraccProject') or None,
            'bm_id': ext.get('bmIdNumber') or None, 'genre2': genre2(rec), 'period': period(rec), 'translation_lines': ntr}


def ours_t3_ngram(db, label):
    n, arts = db.execute(
        "SELECT count(*), count(DISTINCT artifact_id) FROM ngram WHERE field='signs' AND policy='source' AND length BETWEEN 2 AND 5 "
        "AND list_contains(json_extract_string(readings,'$[*]'), ?)", [label]).fetchone()
    return {'windows': n, 'artifacts': arts}


def ours_t3_posting(db, label):
    n, arts = db.execute(
        "WITH p AS (SELECT o.line_id, o.position FROM posting po JOIN occurrence o ON o.id=po.occurrence_id "
        "WHERE po.field='signs' AND po.policy='source' AND po.label=?), "
        "ln AS (SELECT o.line_id, count(*) len FROM occurrence o WHERE o.line_id IN (SELECT DISTINCT line_id FROM p) GROUP BY 1), "
        "w AS (SELECT DISTINCT p.line_id, k.n, p.position - d.d AS s FROM p JOIN ln USING (line_id), range(2,6) k(n), range(0,5) d(d) "
        "WHERE d.d < k.n AND p.position - d.d >= 0 AND p.position - d.d <= ln.len - k.n) "
        "SELECT count(*), count(DISTINCT wi.artifact_id) FROM w JOIN line l ON l.id=w.line_id JOIN sequence_version s ON s.id=l.sequence_id "
        "JOIN witness wi ON wi.id=s.witness_id", [label]).fetchone()
    return {'windows': n, 'artifacts': arts}


# ------------------------------------------------------------------ raw side (ordinary in-memory implementation)

SIGN = re.compile(r'[^\W\d][\w₀-₉]*')
GAPS = {'x', 'X', '...', '…', '(x)'}


def raw_setup():
    data = json.loads(RAW.read_bytes())
    cohort = json.loads((SNAPSHOT / 'cohort.json').read_text())['records']
    recs = [data[r['parent_index']] for r in cohort]
    lines, line_art = [], []
    for ai, rec in enumerate(recs):
        for line in rec['signs'].splitlines():
            toks = []
            for m in re.finditer(r'\S+', line):
                s = m[0].strip('[]?#')
                toks.append('GAP' if s in GAPS else (s if SIGN.fullmatch(s) else 'UNS'))
            lines.append(toks)
            line_art.append(ai)
    frames, post = defaultdict(lambda: defaultdict(set)), defaultdict(list)
    fcount = defaultdict(Counter)
    for li, t in enumerate(lines):
        for i, x in enumerate(t):
            if x not in ('GAP', 'UNS'):
                post[x].append((li, i))
            if i + 2 < len(t) and t[i] not in ('GAP', 'UNS') and t[i + 2] not in ('GAP', 'UNS'):
                fcount[(t[i], t[i + 2])][t[i + 1]] += 1
                frames[(t[i], t[i + 2])][t[i + 1]].add(line_art[li])
    by_key = {r['_id']: r for r in recs}
    cdli = defaultdict(list)
    for r in recs:
        c = (r.get('externalNumbers') or {}).get('cdliNumber')
        if c:
            cdli[c].append(r['_id'])
    return {'recs': recs, 'lines': lines, 'line_art': line_art, 'frames': frames, 'fcount': fcount, 'post': post, 'by_key': by_key, 'cdli': cdli}


def raw_t1(ix, frame):
    a, _, c = frame
    return {f: [n, len(ix['frames'][(a, c)][f])] for f, n in ix['fcount'].get((a, c), {}).items()}


def raw_t2(ix, key):
    rec = ix['by_key'][key]
    ext = rec.get('externalNumbers') or {}
    c = ext.get('cdliNumber') or None
    ntr = sum(1 for ln in rec['atf'].splitlines() if ln.rstrip('\r\n').startswith('#tr.') and TR.match(ln.rstrip('\r\n')))
    return {'cdli': c, 'same_cdli': sorted(k for k in ix['cdli'].get(c, []) if k != key) if c else [], 'oracc_project': rec.get('editedInOraccProject') or None,
            'bm_id': ext.get('bmIdNumber') or None, 'genre2': genre2(rec), 'period': period(rec), 'translation_lines': ntr}


def raw_t3(ix, label):
    wins, arts = set(), set()
    for li, p in ix['post'].get(label, []):
        L = len(ix['lines'][li])
        for n in range(2, 6):
            for s in range(max(0, p - n + 1), min(p, L - n) + 1):
                wins.add((li, n, s))
                arts.add(ix['line_art'][li])
    return {'windows': len(wins), 'artifacts': len(arts)}


# ------------------------------------------------------------------ side runner

def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)


def run_side(side):
    inp = task_inputs()
    t0 = time.monotonic()
    ctx = ours_setup() if side == 'ours' else raw_setup()
    setup = time.monotonic() - t0
    fns = {'ours': {'T1': ours_t1, 'T2': ours_t2, 'T3': ours_t3_ngram, 'T3alt': ours_t3_posting},
           'raw': {'T1': raw_t1, 'T2': raw_t2, 'T3': raw_t3}}[side]
    args = {'T1': inp['frames'], 'T2': inp['artifacts'], 'T3': inp['labels'], 'T3alt': inp['labels']}
    out = {'side': side, 'setup_s': round(setup, 3), 'tasks': {}}
    first_answer = None
    for task, fn in fns.items():
        answers, first, warm = [], [], []
        for x in args[task]:
            t = time.monotonic()
            ans = fn(ctx, x)
            first.append(time.monotonic() - t)
            if first_answer is None:
                first_answer = time.monotonic() - t0
            for _ in range(E['repeats']):
                t = time.monotonic()
                fn(ctx, x)
                warm.append(time.monotonic() - t)
            answers.append(ans)
        out['tasks'][task] = {'n': len(answers), 'answers_sha256': h(json.dumps(answers, sort_keys=True, ensure_ascii=False)), 'answers': answers,
                              'first_p50_s': pct(first, .5), 'first_p95_s': pct(first, .95), 'warm_p50_s': pct(warm, .5), 'warm_p95_s': pct(warm, .95),
                              'code_lines': len(inspect.getsource(fn).splitlines())}
    out['time_to_first_answer_s'] = round(first_answer, 3)
    out['setup_code_lines'] = len(inspect.getsource(ours_setup if side == 'ours' else raw_setup).splitlines())
    out['peak_rss_bytes'] = peak_rss()
    print(json.dumps(out, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--side', choices=['ours', 'raw'])
    ap.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    if a.side:
        return run_side(a.side)
    run = Run('V2', allow_dirty=a.allow_dirty)
    res = {}
    for side in ('raw', 'ours'):
        p = subprocess.run([sys.executable, '-B', '-m', 'bench.value_e2', '--side', side], cwd=ROOT, capture_output=True, text=True)
        if p.returncode:
            raise SystemExit(p.stderr)
        res[side] = json.loads(p.stdout)
        run.mark(f'side_{side}')
    disk = {'ours_snapshot_bytes': sum(f.stat().st_size for f in SNAPSHOT.rglob('*') if f.is_file()), 'raw_input_bytes': RAW.stat().st_size,
            'ours_one_time_build': 'about 22 min and 6.25 GB RSS for the full build (Q-023 record; not re-run here)'}
    tasks = {}
    thr, margin = E['interactive_p95_s'], E['setup_margin_s']
    for task in ('T1', 'T2', 'T3'):
        r, o = res['raw']['tasks'][task], res['ours']['tasks'][task]
        alt = res['ours']['tasks'].get('T3alt') if task == 'T3' else None
        ob = o
        if alt and alt['answers_sha256'] == o['answers_sha256'] and alt['warm_p95_s'] < o['warm_p95_s']:
            ob = alt
        identical = r['answers_sha256'] == o['answers_sha256']
        diffs = [i for i, (x, y) in enumerate(zip(r['answers'], o['answers'])) if x != y][:5]
        ours_interactive = max(ob['warm_p95_s'], ob['first_p95_s']) < thr
        raw_interactive = max(r['warm_p95_s'], r['first_p95_s']) < thr
        tasks[task] = {'identical_answers': identical, 'first_differences': [{'i': i, 'raw': r['answers'][i], 'ours': o['answers'][i]} for i in diffs],
                       'ours_variant': 'posting' if ob is alt else 'ngram' if task == 'T3' else 'sql',
                       'latency_s': {'raw': {k: r[k] for k in ('first_p50_s', 'first_p95_s', 'warm_p50_s', 'warm_p95_s')},
                                     'ours': {k: ob[k] for k in ('first_p50_s', 'first_p95_s', 'warm_p50_s', 'warm_p95_s')}},
                       'code_lines': {'raw': r['code_lines'], 'ours': ob['code_lines']},
                       'ours_interactive_raw_not': ours_interactive and not raw_interactive, 'raw_interactive': raw_interactive,
                       'ours_setup_advantage': (res['raw']['setup_s'] - res['ours']['setup_s'] >= margin) and max(ob['warm_p95_s'], ob['first_p95_s']) <= thr}
        if task == 'T3' and alt:
            tasks[task]['ours_alt'] = {'identical_to_ngram_variant': alt['answers_sha256'] == o['answers_sha256'], 'warm_p95_s': alt['warm_p95_s'], 'first_p95_s': alt['first_p95_s']}
    wins = sum(1 for t in tasks.values() if t['identical_answers'] and (t['ours_interactive_raw_not'] or t['ours_setup_advantage']))
    refuted = all(t['identical_answers'] and t['raw_interactive'] for t in tasks.values()) and res['raw']['setup_s'] < margin
    claim = 'supported' if wins >= 2 else 'refuted' if refuted else 'inconclusive'
    for s in res.values():
        for t in s['tasks'].values():
            t['answers_preview'] = t['answers'][:2]
            del t['answers']
    summary = {'protocol': 'cb-value-1', 'value_config_sha256': vconfig_sha(), 'inputs': task_inputs(), 'sides': res, 'tasks': tasks, 'disk': disk,
               'C3': claim, 'C3_tasks_with_construction_advantage': wins}
    run.write('summary.json', summary)
    rec = run.finish({'value_protocol': 'cb-value-1', 'C3': claim, 'setup_s': {k: v['setup_s'] for k, v in res.items()},
                      'peak_rss_bytes': {k: v['peak_rss_bytes'] for k, v in res.items()}})
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
