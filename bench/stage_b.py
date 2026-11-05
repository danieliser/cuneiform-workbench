"""Stage B: motif/formula discovery (train) with a sealed held-out confirmation (test), order-1 transition null.

  python -m bench.stage_b discover            # B0 resource pilot + B1 frozen list (test lines dropped before any statistic)
  git add results/<B1 run>/frozen.json && git commit    # seal
  python -m bench.stage_b confirm --frozen results/<B1 run>/frozen.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

from .core import CONFIG, ROOT, SEED, Run, connect, load_universe, split_manifest, check_rss, h

B = CONFIG['stage_b']


def d_eff(o: float, e: float) -> float:
    return math.log2((o + 0.5) / (e + 0.5))


def windows(corpus, line_ids):
    """Yield (line_idx, i, A, B, C) for 3 consecutive derived signs in one line (gaps/unsupported break windows)."""
    for li in line_ids:
        t = corpus.lines[li]
        for i in range(len(t) - 2):
            a, b, c = t[i], t[i + 1], t[i + 2]
            if a >= 0 and b >= 0 and c >= 0:
                yield li, i, a, b, c


def transitions(corpus, line_ids):
    c2 = defaultdict(Counter)
    for li in line_ids:
        t = corpus.lines[li]
        for i in range(len(t) - 1):
            if t[i] >= 0 and t[i + 1] >= 0:
                c2[t[i]][t[i + 1]] += 1
    p = {}
    for x, row in c2.items():
        tot = sum(row.values())
        p[x] = {y: n / tot for y, n in row.items()}
    digest = h(json.dumps(sorted((x, sorted(r.items())) for x, r in c2.items())))
    return p, digest, sum(sum(r.values()) for r in c2.values())


def p2(p, a, c):
    return sum(pb * p.get(b, {}).get(c, 0.0) for b, pb in p.get(a, {}).items())


def split_lines(u, which):
    return [li for li, a in enumerate(u.signs.line_art) if u.split[a] == which]


def count_types(u, line_ids):
    tri = Counter()
    for _, _, a, b, c in windows(u.signs, line_ids):
        tri[(a, b, c)] += 1
    ctx_ab = Counter()
    ctx_a = Counter()
    frame = Counter()
    for (a, b, c), n in tri.items():
        ctx_ab[(a, b)] += n
        ctx_a[a] += n
        frame[(a, c)] += n
    return tri, ctx_ab, ctx_a, frame


def family_support(u, line_ids, wanted_tri, wanted_frame):
    ft, ff = defaultdict(Counter), defaultdict(Counter)
    ex_t, ex_f = defaultdict(list), defaultdict(list)
    for li, i, a, b, c in windows(u.signs, line_ids):
        fam = u.family[u.signs.line_art[li]]
        if (a, b, c) in wanted_tri:
            ft[(a, b, c)][fam] += 1
            if len(ex_t[(a, b, c)]) < 3:
                ex_t[(a, b, c)].append((li, i))
        if (a, c) in wanted_frame:
            ff[(a, c)][fam] += 1
            if len(ex_f[(a, c)]) < 3:
                ex_f[(a, c)].append((li, i))
    return ft, ff, ex_t, ex_f


def example(u, li, i, n=3):
    c = u.signs
    return {'catalog_key': u.catalog[u.artifacts[c.line_art[li]]], 'artifact_id': u.artifacts[c.line_art[li]], 'line_id': c.line_ids[li],
            'line_label': c.line_labels[li], 'start_position': i, 'window': [c.label(x) for x in c.lines[li][i:i + n]]}


def discover(args):
    run = Run('B1', allow_dirty=args.allow_dirty)
    db = connect()
    u = load_universe(db, with_atf=True, with_near=True)
    db.close()
    sm = split_manifest(u)
    run.write('split.json', {'sha256': sm['sha256'], 'counts': sm['counts']})
    (run.raw / 'split_listing.tsv').write_text(sm['listing'])
    train = split_lines(u, B['discovery_split'])
    # Seal: drop every non-train line from memory before any statistic is computed.
    keep = set(train)
    dropped = 0
    for li in range(len(u.signs.lines)):
        if li not in keep:
            u.signs.lines[li] = None
            dropped += 1
    u.atf = None
    run.mark('load_and_seal')

    p, p_digest, pairs = transitions(u.signs, train)
    t0 = time.monotonic()
    tri, ctx_ab, ctx_a, frame = count_types(u, train)
    pass_s = time.monotonic() - t0
    run.mark('discovery_counts')

    # B0 resource pilot: eligible type counts per threshold (min families checked below for the chosen T only)
    pilot = {}
    for T in B['min_count_candidates']:
        pilot[T] = {'trigram_types_O>=T': sum(1 for n in tri.values() if n >= T), 'frame_types_O>=T': sum(1 for n in frame.values() if n >= T)}
    chosen_T = next((T for T in B['min_count_candidates'] if pilot[T]['trigram_types_O>=T'] <= 100000 and pilot[T]['frame_types_O>=T'] <= 100000 and pass_s < 600),
                    B['min_count_candidates'][-1])
    run.write('pilot.json', {'pilot': pilot, 'count_pass_s': round(pass_s, 2), 'chosen_min_count': chosen_T, 'rule': B['threshold_rule'],
                             'note': 'resource pilot on train only; no effect sizes inspected'})
    T = chosen_T

    cand_t = {k for k, n in tri.items() if n >= T}
    cand_f = {k for k, n in frame.items() if n >= T}
    ft, ff, ex_t, ex_f = family_support(u, train, cand_t, cand_f)
    run.mark('family_support')

    def stats_t(k):
        a, b, c = k
        o = tri[k]
        e = ctx_ab[(a, b)] * p[b].get(c, 0.0)
        fams = ft[k]
        return {'type': 'trigram', 'labels': [u.signs.label(x) for x in k], 'codes': list(k), 'O_disc': o, 'E_disc': round(e, 4), 'd_disc': round(d_eff(o, e), 4),
                'families_disc': len(fams), 'max_family_share_disc': round(max(fams.values()) / o, 4), 'context_disc': ctx_ab[(a, b)]}

    def stats_f(k):
        a, c = k
        o = frame[k]
        e = ctx_a[a] * p2(p, a, c)
        fams = ff[k]
        return {'type': 'frame', 'labels': [u.signs.label(a), '?', u.signs.label(c)], 'codes': [a, c], 'O_disc': o, 'E_disc': round(e, 4), 'd_disc': round(d_eff(o, e), 4),
                'families_disc': len(fams), 'max_family_share_disc': round(max(fams.values()) / o, 4), 'context_disc': ctx_a[a]}

    frozen = {}
    for kind, keys, fn, k_top in (('trigram', cand_t, stats_t, B['top_k']['trigram']), ('frame', cand_f, stats_f, B['top_k']['frame'])):
        elig = [s for s in (fn(k) for k in keys) if s['families_disc'] >= B['min_families']]
        elig.sort(key=lambda s: (-s['d_disc'], -s['O_disc'], s['labels']))
        cands = elig[:k_top]
        cand_ids = {tuple(s['codes']) for s in cands}
        pool = defaultdict(list)
        for s in elig:
            if abs(s['d_disc']) <= 0.25 and tuple(s['codes']) not in cand_ids:
                pool[int(math.log2(s['O_disc']))].append(s)
        used = set()
        for rank, s in enumerate(cands, 1):
            s['rank'] = rank
            s['examples_disc'] = [example(u, li, i, 3) for li, i in (ex_t if kind == 'trigram' else ex_f)[tuple(s['codes'])]]
            b = int(math.log2(s['O_disc']))
            opts = [c for c in pool[b] if tuple(c['codes']) not in used]
            if opts:
                ctl = min(opts, key=lambda c: h(f"{SEED}:control:{'|'.join(s['labels'])}:{'|'.join(c['labels'])}"))
                used.add(tuple(ctl['codes']))
                s['control'] = dict(ctl)
            else:
                s['control'] = None
        frozen[kind] = {'eligible_types': len(elig), 'candidates': cands,
                        'controls_missing': sum(1 for s in cands if s['control'] is None)}
    run.mark('rank_and_controls')

    denom = {'train_trigram_windows': sum(tri.values()), 'train_adjacent_sign_pairs': pairs, 'train_families': sm['counts']['train']['families'],
             'train_lines': len(train), 'non_train_lines_dropped_before_statistics': dropped}
    body = {'protocol': CONFIG['protocol_version'], 'seed': SEED, 'split_sha256': sm['sha256'], 'transition_digest': p_digest,
            'min_count': T, 'min_families': B['min_families'], 'denominators': denom, 'frozen': frozen}
    text = json.dumps(body, indent=1, ensure_ascii=False)
    (run.results / 'frozen.json').write_text(text + '\n')
    digest = hashlib.sha256((text + '\n').encode()).hexdigest()
    rec = run.finish({'frozen_sha256': digest, 'min_count': T, 'pilot': pilot, 'denominators': denom})
    print(json.dumps(rec, indent=1, default=str))


POISSON_CASE = ("CASE WHEN u<0.36787944 THEN 0 WHEN u<0.73575888 THEN 1 WHEN u<0.91969860 THEN 2 WHEN u<0.98101184 THEN 3 "
                "WHEN u<0.99634015 THEN 4 WHEN u<0.99940582 THEN 5 WHEN u<0.99991676 THEN 6 WHEN u<0.99998975 THEN 7 ELSE 8 END")


def confirm(args):
    import duckdb
    frozen_path = Path(args.frozen)
    frozen_bytes = frozen_path.read_bytes()
    fz = json.loads(frozen_bytes)
    run = Run('B2', allow_dirty=args.allow_dirty)
    db = connect()
    u = load_universe(db, with_atf=True, with_near=True)
    db.close()
    sm = split_manifest(u)
    if sm['sha256'] != fz['split_sha256']:
        raise SystemExit('split differs from the frozen discovery split')
    train = split_lines(u, B['discovery_split'])
    p, p_digest, _ = transitions(u.signs, train)
    if p_digest != fz['transition_digest']:
        raise SystemExit('train transition table differs from the frozen one')
    test = split_lines(u, B['confirmation_split'])
    train_fams = {u.family[a] for a in range(len(u.artifacts)) if u.split[a] == 'train'}
    near_partner = {}
    for a in range(len(u.artifacts)):
        if u.split[a] == 'test':
            near_partner[u.family[a]] = near_partner.get(u.family[a], False) or any(u.family[b] in train_fams for b in u.near['partners'].get(a, ()))
    run.mark('load')

    types = []
    pairs_of = {'trigram': [], 'frame': []}
    for kind in ('trigram', 'frame'):
        for s in fz['frozen'][kind]['candidates']:
            ci_ = len(types)
            types.append((kind, 'candidate', s['rank'], s))
            if s['control']:
                types.append((kind, 'control', s['rank'], s['control']))
                pairs_of[kind].append((ci_, ci_ + 1))
    want_t = {tuple(s['codes']): i for i, (k, _, _, s) in enumerate(types) if k == 'trigram'}
    want_f = {tuple(s['codes']): i for i, (k, _, _, s) in enumerate(types) if k == 'frame'}
    q = {}
    for i, (kind, _, _, s) in enumerate(types):
        q[i] = p[s['codes'][1]].get(s['codes'][2], 0.0) if kind == 'trigram' else p2(p, s['codes'][0], s['codes'][1])
    O = defaultdict(Counter)
    C = defaultdict(Counter)
    ex = defaultdict(list)
    ctx_t = defaultdict(list)
    for key, i in want_t.items():
        ctx_t[key[:2]].append(i)
    ctx_f = defaultdict(list)
    for key, i in want_f.items():
        ctx_f[key[0]].append(i)
    for li, pos, a, b, c in windows(u.signs, test):
        fam = u.family[u.signs.line_art[li]]
        for i in ctx_t.get((a, b), ()):
            C[i][fam] += 1
        i = want_t.get((a, b, c))
        if i is not None:
            O[i][fam] += 1
            if len(ex[i]) < 3:
                ex[i].append((li, pos))
        for i in ctx_f.get(a, ()):
            C[i][fam] += 1
        i = want_f.get((a, c))
        if i is not None:
            O[i][fam] += 1
            if len(ex[i]) < 3:
                ex[i].append((li, pos))
    run.mark('confirmation_counts')

    # Bootstrap over confirmation families in an in-memory DuckDB (deterministic hash-derived Poisson(1) weights).
    m = duckdb.connect()
    m.execute(f"SET memory_limit='{CONFIG['resources']['duckdb_memory_limit']}'; SET threads={CONFIG['resources']['duckdb_threads']}")
    m.execute('CREATE TABLE e(t INTEGER, fam VARCHAR, o DOUBLE, ex DOUBLE)')
    tmp = run.raw / 'boot_entries.csv'
    with open(tmp, 'w') as fh:
        for i in range(len(types)):
            for fam in sorted(set(O[i]) | set(C[i])):
                fh.write(f'{i},{fam},{float(O[i][fam])!r},{C[i][fam] * q[i]!r}\n')
    m.execute(f"INSERT INTO e SELECT * FROM read_csv('{tmp}', header=false, quote='', columns={{'t':'INTEGER','fam':'VARCHAR','o':'DOUBLE','ex':'DOUBLE'}})")
    fams = sorted({u.family[a] for a in range(len(u.artifacts)) if u.split[a] == 'test'})
    m.execute('CREATE TABLE f(fam VARCHAR, nd BOOLEAN)')
    m.executemany('INSERT INTO f VALUES (?,?)', [(f_, bool(near_partner.get(f_))) for f_ in fams])
    reps = B['bootstrap']['reps']
    m.execute(f"""CREATE TABLE w AS SELECT rep, fam, {POISSON_CASE} AS w FROM
                  (SELECT r.range AS rep, f.fam, (hash('{SEED}', r.range, f.fam) % 1000000007) / 1000000007.0 AS u FROM range({reps}) r, f)""")
    meta = [(i, k, role, rank) for i, (k, role, rank, _) in enumerate(types)]
    m.execute('CREATE TABLE meta(t INTEGER, kind VARCHAR, role VARCHAR, rnk INTEGER)')
    m.executemany('INSERT INTO meta VALUES (?,?,?,?)', meta)

    def boot(subset_sql: str):
        res = m.execute(f"""
          WITH s AS (SELECT w.rep, e.t, sum(w.w*e.o) o, sum(w.w*e.ex) ex FROM e JOIN w USING(fam) JOIN f USING(fam) WHERE {subset_sql} GROUP BY ALL),
               full_ AS (SELECT r.range rep, meta.t FROM range({reps}) r, meta),
               d AS (SELECT full_.rep, full_.t, log2((coalesce(o,0)+0.5)/(coalesce(ex,0)+0.5)) d FROM full_ LEFT JOIN s USING(rep,t))
          SELECT d.rep, d.t, d.d FROM d ORDER BY 1,2""").fetchall()
        per = defaultdict(list)
        for rep, t, d in res:
            per[t].append(d)
        return per

    def point(i, nd_only=False):
        fs = [f_ for f_ in set(O[i]) | set(C[i]) if not nd_only or not near_partner.get(f_)]
        o = sum(O[i][f_] for f_ in fs)
        e = sum(C[i][f_] for f_ in fs) * q[i]
        return o, e, d_eff(o, e), len([f_ for f_ in fs if O[i][f_] > 0])

    def ci(xs):
        xs = sorted(xs)
        return [round(xs[int(0.025 * (len(xs) - 1))], 4), round(xs[int(round(0.975 * (len(xs) - 1)))], 4)]

    out = {}
    for label, sub in (('all_test_families', 'TRUE'), ('test_families_without_near_dup_partner_in_train', 'NOT f.nd')):
        per = boot(sub)
        hyp = {}
        for kind in ('trigram', 'frame'):
            pairs = pairs_of[kind]
            diffs = []
            for r in range(reps):
                diffs.append(sum(per[a][r] for a, _ in pairs) / len(pairs) - sum(per[b][r] for _, b in pairs) / len(pairs))
            pt_c = sum(point(a, label != 'all_test_families')[2] for a, _ in pairs) / len(pairs)
            pt_k = sum(point(b, label != 'all_test_families')[2] for _, b in pairs) / len(pairs)
            lo, hi = ci(diffs)
            hyp[kind] = {'pairs': len(pairs), 'mean_d_conf_candidates': round(pt_c, 4), 'mean_d_conf_controls': round(pt_k, 4), 'difference': round(pt_c - pt_k, 4),
                         'ci95': [lo, hi], 'H_B1_supported': lo > 0}
        out[label] = {'hypothesis': hyp, 'per_type_ci': {i: ci(per[i]) for i in range(len(types))}}
    run.mark('bootstrap')

    ranked = []
    for i, (kind, role, rank, s) in enumerate(types):
        o, e, d, nf = point(i)
        o2, e2, d2, _ = point(i, True)
        ranked.append({'kind': kind, 'role': role, 'rank': rank, 'labels': s['labels'], 'O_disc': s['O_disc'], 'E_disc': s['E_disc'], 'd_disc': s['d_disc'],
                       'families_disc': s['families_disc'], 'max_family_share_disc': s['max_family_share_disc'],
                       'O_conf': o, 'E_conf': round(e, 4), 'd_conf': round(d, 4), 'd_conf_ci95_exploratory': out['all_test_families']['per_type_ci'][i],
                       'families_conf': nf, 'context_conf': sum(C[i].values()),
                       'd_conf_no_near_dup': round(d2, 4), 'O_conf_no_near_dup': o2,
                       'examples_conf': [example(u, li, pos, 3) for li, pos in ex[i]], 'examples_disc': s.get('examples_disc')})
    conf_windows = sum(1 for _ in windows(u.signs, test))
    denom = {'test_trigram_windows': conf_windows, 'test_families': len(fams), 'test_lines': len(test),
             'test_families_with_near_dup_partner_in_train': sum(1 for f_ in fams if near_partner.get(f_)), 'bootstrap_reps': reps,
             'bootstrap_weights': 'Poisson(1) by inverse CDF of duckdb hash(seed, rep, family) (duckdb 1.4.3)'}
    summ = {'frozen_file': str(frozen_path.relative_to(ROOT)), 'frozen_sha256': hashlib.sha256(frozen_bytes).hexdigest(), 'denominators': denom,
            'hypotheses': {k: v['hypothesis'] for k, v in out.items()},
            'replication_rate_exploratory': {kind: {role: round(sum(1 for r in ranked if r['kind'] == kind and r['role'] == role and r['d_conf_ci95_exploratory'][0] > 0) /
                                                             max(1, sum(1 for r in ranked if r['kind'] == kind and r['role'] == role)), 4) for role in ('candidate', 'control')}
                                             for kind in ('trigram', 'frame')}}
    run.write('ranked.json', ranked)
    run.write('summary.json', summ)
    cols = ['kind', 'role', 'rank', 'labels', 'O_disc', 'E_disc', 'd_disc', 'families_disc', 'O_conf', 'E_conf', 'd_conf', 'd_conf_ci95_exploratory', 'families_conf', 'd_conf_no_near_dup']
    run.write('ranked.csv', [cols] + [[' '.join(r[c]) if c == 'labels' else r[c] for c in cols] for r in ranked])
    rec = run.finish({'summary': summ})
    print(json.dumps(rec, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['discover', 'confirm'])
    ap.add_argument('--frozen')
    ap.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    (discover if a.mode == 'discover' else confirm)(a)


if __name__ == '__main__':
    main()
