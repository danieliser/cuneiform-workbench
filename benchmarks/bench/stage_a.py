"""Stage A: query-path correctness (independent streaming oracle vs index vs SQL grouping vs graph) and performance."""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import Counter

from .core import CONFIG, GAP, SEED, UNS, Corpus, Run, backend, connect, connect_backend, load_universe, split_manifest, check_rss, expand

A = CONFIG['stage_a']


# ---------------------------------------------------------------- independent oracle

def encode(corpus: Corpus, pattern: list) -> list:
    return [None if p == '?' else corpus.code.get(p, -99) for p in pattern]


def oracle(corpus: Corpus, pattern: list, skips=(0,)) -> list:
    """Streaming window scan over canonical positions. '?' matches exactly one represented position (any kind);
    an exact token never matches a gap/unsupported position. Windows never cross lines. Returns match tuples
    (line_idx, start, length, middle_code_or_None)."""
    codes = encode(corpus, pattern)
    out = []
    for skip in skips:
        p = codes[:-1] + [None] * skip + codes[-1:]
        n = len(p)
        first = p[0]
        for li, t in enumerate(corpus.lines):
            L = len(t)
            for i in range(L - n + 1):
                if first is not None and t[i] != first:
                    continue
                ok = True
                for j in range(1, n):
                    want = p[j]
                    if want is not None and t[i + j] != want:
                        ok = False
                        break
                if ok:
                    out.append((li, i, n, t[i + 1] if n == 3 and p[1] is None else None))
    return out


def summarize_oracle(u, corpus: Corpus, matches: list) -> dict:
    arts = {corpus.line_art[m[0]] for m in matches}
    fill = Counter(corpus.label(m[3]) for m in matches if m[3] is not None)
    return {
        'count': len(matches),
        'spans': {(corpus.line_ids[m[0]], m[1], m[2]) for m in matches},
        'artifacts': {u.artifacts[a] for a in arts},
        'witnesses': {corpus.witness[a] for a in arts},
        'families': {u.family[a] for a in arts},
        'fillers': fill,
    }


# ---------------------------------------------------------------- query mix

def window_counts(corpus: Corpus, n: int) -> Counter:
    c = Counter()
    for t in corpus.lines:
        for i in range(len(t) - n + 1):
            w = tuple(t[i:i + n])
            if min(w) >= 0:
                c[w] += 1
    return c


def frame_counts(corpus: Corpus) -> Counter:
    c = Counter()
    for t in corpus.lines:
        for i in range(len(t) - 2):
            if t[i] >= 0 and t[i + 2] >= 0:
                c[(t[i], t[i + 2])] += 1
    return c


def stratum(count: int, n: int, kind: str) -> str | None:
    if kind == 'frame':
        hi, lo = 500, 10
    else:
        hi, lo = (1000, 10) if n == 2 else (200, 10)
    if count >= hi:
        return 'common'
    if lo <= count < hi:
        return 'mid'
    if 1 <= count <= 3:
        return 'rare'
    return None


def pick(counts: Counter, corpus: Corpus, n: int, kind: str, want: dict, key: str) -> list:
    by = {}
    for w, c in counts.items():
        s = stratum(c, n, kind)
        if s in want:
            by.setdefault(s, []).append((tuple(corpus.label(x) for x in w), c))
    chosen = []
    for s, k in want.items():
        pool = sorted(by.get(s, []))
        rnd = random.Random(f'{SEED}:stageA:{key}:{s}')
        sample = rnd.sample(pool, min(k, len(pool)))
        for labels, c in sample:
            chosen.append({'stratum': s, 'labels': list(labels), 'selection_count': c, 'pool_size': len(pool)})
    return chosen


def build_mix(u) -> list:
    mix = []
    fm = A['field_mix']
    for n_s, want in fm['signs_exact'].items():
        n = int(n_s)
        c = window_counts(u.signs, n)
        for q in pick(c, u.signs, n, 'exact', want, f'signs{n}'):
            mix.append(dict(q, field='signs', kind='exact', pattern=q['labels']))
        if n == 3:
            tri = c
        del c
        check_rss('mix')
    fr = frame_counts(u.signs)
    for q in pick(fr, u.signs, 3, 'frame', fm['signs_frame_A?C'], 'frame'):
        mix.append(dict(q, field='signs', kind='frame', pattern=[q['labels'][0], '?', q['labels'][1]]))
    del fr
    for q in pick(tri, u.signs, 3, 'exact', fm['signs_bounded_gap_AB[0-2]C'], 'gap'):
        mix.append(dict(q, field='signs', kind='bounded_gap', pattern=q['labels'], max_skip=2))
    del tri
    for n_s, want in fm['atf_exact'].items():
        n = int(n_s)
        c = window_counts(u.atf, n)
        for q in pick(c, u.atf, n, 'exact', want, f'atf{n}'):
            mix.append(dict(q, field='atf', kind='exact', pattern=q['labels']))
    for i, q in enumerate(mix):
        q['qid'] = f'Q{i:03d}'
    return mix


# ---------------------------------------------------------------- engines

def effective_patterns(q) -> list:
    if q['kind'] == 'bounded_gap':
        a = q['pattern']
        return [a[:-1] + ['?'] * s + a[-1:] for s in range(q['max_skip'] + 1)]
    return [q['pattern']]


def index_call(projections, db, manifest, q):
    total = 0
    first = None
    for p in effective_patterns(q):
        r = projections.lookup(db, 'motifs', json.dumps(p, ensure_ascii=False), namespace=manifest['namespace'],
                               snapshot=manifest['snapshot_id'], field=q['field'], policy='source', limit=20)
        total += r['count']
        first = first or r
    return total


def sql_grouped(db, manifest, q, u) -> dict:
    """Own SQL over the materialized ngram index (not the backend lookup), returning spans, supports and fillers."""
    spans, arts, wits, fill = set(), set(), set(), Counter()
    for p in effective_patterns(q):
        where = ['n.namespace=?', 'n.snapshot_id=?', 'n.field=?', "n.policy='source'", 'n.length=?']
        args = [manifest['namespace'], manifest['snapshot_id'], q['field'], len(p)]
        for i, tok in enumerate(p):
            if tok != '?':
                where.append(f"json_extract_string(n.readings,'$[{i}]')=?")
                args.append(tok)
        frame = q['kind'] == 'frame'
        sel = "n.line_id,n.start_position,n.length,n.artifact_id,n.witness_id" + (",json_extract_string(n.readings,'$[1]'),o.kind" if frame else '')
        join = " LEFT JOIN occurrence o ON o.id=json_extract_string(n.occurrence_ids,'$[1]')" if frame else ''
        for row in db.execute(f"SELECT {sel} FROM ngram n{join} WHERE {' AND '.join(where)}", args).fetchall():
            spans.add((row[0], row[1], row[2]))
            arts.add(row[3])
            wits.add(row[4])
            if frame:
                fill[row[5] if row[5] is not None else ('GAP' if row[6] == 'gap' else 'UNS')] += 1
    fam_of = {a: u.family[i] for i, a in enumerate(u.artifacts)}
    return {'count': len(spans), 'spans': spans, 'artifacts': arts, 'witnesses': wits, 'families': {fam_of[a] for a in arts}, 'fillers': fill}


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-dirty', action='store_true')
    ap.add_argument('--skip-graph', action='store_true')
    ap.add_argument('--skip-stages', action='store_true')
    args = ap.parse_args()
    run = Run('A', allow_dirty=args.allow_dirty)
    storage, projections, query = backend()

    db0 = connect()
    u = load_universe(db0, with_atf=True, with_near=False)
    db0.close()
    run.mark('load_universe')
    sm = split_manifest(u)
    mix = build_mix(u)
    run.mark('query_mix')
    run.write('querymix.json', [{k: v for k, v in q.items()} for q in mix])

    db, manifest = connect_backend()
    rows, fail = [], []
    for q in mix:
        corpus = u.signs if q['field'] == 'signs' else u.atf
        # independent oracle (verification time, kept separate from query time)
        t = time.monotonic()
        skips = tuple(range(q.get('max_skip', 0) + 1))
        om = oracle(corpus, q['pattern'], skips)
        orc = summarize_oracle(u, corpus, om)
        oracle_ms = (time.monotonic() - t) * 1000
        # backend index lookup: process-cold then warm repeats
        t = time.monotonic()
        idx_count = index_call(projections, db, manifest, q)
        cold = (time.monotonic() - t) * 1000
        warm = []
        for _ in range(A['warm_repeats']):
            t = time.monotonic()
            index_call(projections, db, manifest, q)
            warm.append((time.monotonic() - t) * 1000)
        t = time.monotonic()
        sg = sql_grouped(db, manifest, q, u)
        sql_ms = (time.monotonic() - t) * 1000
        checks = {
            'index_count': idx_count == orc['count'],
            'sql_spans': sg['spans'] == orc['spans'],
            'artifacts': sg['artifacts'] == orc['artifacts'],
            'witnesses': sg['witnesses'] == orc['witnesses'],
            'families': sg['families'] == orc['families'],
            'fillers': sg['fillers'] == orc['fillers'],
        }
        if not all(checks.values()):
            fail.append({'qid': q['qid'], 'checks': checks, 'oracle_only': sorted(orc['spans'] - sg['spans'])[:5], 'index_only': sorted(sg['spans'] - orc['spans'])[:5]})
        top = [{'filler': k, 'occurrences': v,
                'families': len({u.family[corpus.line_art[m[0]]] for m in om if m[3] is not None and corpus.label(m[3]) == k})}
               for k, v in sorted(orc['fillers'].items(), key=lambda kv: (-kv[1], kv[0]))[:A['filler_top_k']]] if q['kind'] == 'frame' else None
        ex = om[0] if om else None
        rows.append({
            'qid': q['qid'], 'field': q['field'], 'kind': q['kind'], 'stratum': q['stratum'], 'pattern': q['pattern'],
            'length': len(q['pattern']), 'max_skip': q.get('max_skip', 0),
            'count': orc['count'], 'witnesses': len(orc['witnesses']), 'artifacts': len(orc['artifacts']), 'families': len(orc['families']),
            'distinct_fillers': len(orc['fillers']) if q['kind'] == 'frame' else None, 'top_fillers': top,
            'agree': all(checks.values()), 'checks': checks,
            'oracle_ms': round(oracle_ms, 1), 'index_cold_ms': round(cold, 1), 'index_warm_ms': [round(w, 1) for w in warm], 'sql_grouped_ms': round(sql_ms, 1),
            'example': {'catalog_key': u.catalog[u.artifacts[corpus.line_art[ex[0]]]], 'line_id': corpus.line_ids[ex[0]], 'line_label': corpus.line_labels[ex[0]], 'start_position': ex[1]} if ex else None,
        })
        check_rss(q['qid'])
    run.mark('main_loop')

    # graph engine subset (pinned query.motif; slower; single cold call each)
    graph = []
    if not args.skip_graph:
        want = [('signs', 'exact', 2, 'common'), ('signs', 'exact', 2, 'mid'), ('signs', 'exact', 3, 'common'), ('signs', 'exact', 3, 'mid'),
                ('signs', 'frame', 3, 'common'), ('signs', 'frame', 3, 'mid'), ('signs', 'bounded_gap', 3, 'common'), ('signs', 'bounded_gap', 3, 'mid')]
        for f, k, n, s in want:
            subset = [r for r in rows if r['field'] == f and r['kind'] == k and r['length'] == n and r['stratum'] == s][:2 if k != 'bounded_gap' else 1]
            for r in subset:
                t = time.monotonic()
                g = query.motif(db, r['pattern'], field=f, strict=False, max_skip=r['max_skip'])
                ms = (time.monotonic() - t) * 1000
                graph.append({'qid': r['qid'], 'pattern': r['pattern'], 'max_skip': r['max_skip'], 'graph_count': g['occurrences'], 'graph_witnesses': g['witnesses'],
                              'graph_artifacts': g['artifacts'], 'oracle_count': r['count'],
                              'agree': g['occurrences'] == r['count'] and g['witnesses'] == r['witnesses'] and g['artifacts'] == r['artifacts'], 'graph_ms': round(ms, 1)})
                check_rss('graph')
        run.mark('graph_subset')
    db.close()

    # optional existing read-only stage snapshots (timing scaling only)
    stages = []
    root = expand(CONFIG['input']['optional_stage_root'])
    if not args.skip_stages and root.exists():
        sub = [r for r in rows if r['field'] == 'signs' and ((r['kind'] == 'exact' and r['length'] == 2) or r['kind'] == 'frame') and r['stratum'] in ('common', 'mid')]
        for snap in sorted(root.glob('snapshot:*')):
            t = time.monotonic()
            chk = storage.verified(snap, snap.name, ledger='auto')
            vms = (time.monotonic() - t) * 1000
            sdb, sman = connect_backend(snap)
            cold, warm, counts = [], [], {}
            for r in sub:
                q = {'pattern': r['pattern'], 'field': 'signs', 'kind': r['kind']}
                t = time.monotonic(); counts[r['qid']] = index_call(projections, sdb, sman, q); cold.append((time.monotonic() - t) * 1000)
                for _ in range(A['warm_repeats']):
                    t = time.monotonic(); index_call(projections, sdb, sman, q); warm.append((time.monotonic() - t) * 1000)
            sdb.close()
            stages.append({'snapshot': snap.name, 'records': sman['ingestion']['records'], 'ngram_rows': sman['counts']['ngram'], 'verify_ms': round(vms, 1),
                           'publication': chk['publication']['status'], 'queries': len(sub), 'cold_p50': pct(cold, .5), 'cold_p95': pct(cold, .95),
                           'warm_p50': pct(warm, .5), 'warm_p95': pct(warm, .95), 'warm_n': len(warm), 'counts': counts})
        run.mark('stage_scaling')

    # aggregates
    agg = []
    keyf = lambda r: (r['field'], r['kind'], r['length'], r['stratum'])
    for key in sorted({keyf(r) for r in rows}):
        rs = [r for r in rows if keyf(r) == key]
        warm = [w for r in rs for w in r['index_warm_ms']]
        agg.append({'field': key[0], 'kind': key[1], 'length': key[2], 'stratum': key[3], 'queries': len(rs), 'agree': sum(r['agree'] for r in rs),
                    'count_min': min(r['count'] for r in rs), 'count_max': max(r['count'] for r in rs),
                    'cold_p50_ms': pct([r['index_cold_ms'] for r in rs], .5), 'cold_p95_ms': pct([r['index_cold_ms'] for r in rs], .95), 'cold_n': len(rs),
                    'warm_p50_ms': pct(warm, .5), 'warm_p95_ms': pct(warm, .95), 'warm_n': len(warm),
                    'sql_grouped_p50_ms': pct([r['sql_grouped_ms'] for r in rs], .5), 'oracle_p50_ms': pct([r['oracle_ms'] for r in rs], .5)})
    allwarm = [w for r in rows for w in r['index_warm_ms']]
    summary = {'queries': len(rows), 'agree_all_checks': sum(r['agree'] for r in rows), 'failures': fail,
               'graph_subset': {'queries': len(graph), 'agree': sum(g['agree'] for g in graph)},
               'index_cold_ms': {'p50': pct([r['index_cold_ms'] for r in rows], .5), 'p95': pct([r['index_cold_ms'] for r in rows], .95), 'n': len(rows)},
               'index_warm_ms': {'p50': pct(allwarm, .5), 'p95': pct(allwarm, .95), 'n': len(allwarm)},
               'oracle_ms': {'p50': pct([r['oracle_ms'] for r in rows], .5), 'p95': pct([r['oracle_ms'] for r in rows], .95), 'n': len(rows)},
               'universe': {'artifacts': len(u.artifacts), 'signs_lines': len(u.signs.lines), 'signs_positions': sum(len(t) for t in u.signs.lines),
                            'atf_lines': len(u.atf.lines), 'atf_positions': sum(len(t) for t in u.atf.lines), 'families': u.evidence},
               'timing_definition': 'process-cold = first index call for that query on a fresh read-only connection (OS page cache NOT flushed, state unknown, shared machine); warm = 4 repeats same connection; oracle = verification time, separate'}
    run.write('queries.json', rows)
    run.write('aggregate.json', agg)
    run.write('summary.json', summary)
    run.write('graph_subset.json', graph)
    run.write('stage_scaling.json', stages)
    cols = list(agg[0].keys())
    run.write('aggregate.csv', [cols] + [[a[c] for c in cols] for a in agg])
    rec = run.finish({'summary': {k: summary[k] for k in ('queries', 'agree_all_checks', 'graph_subset', 'index_cold_ms', 'index_warm_ms', 'oracle_ms')}})
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
