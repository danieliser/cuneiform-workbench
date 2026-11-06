"""E1 (cb-value-1): sealed masked-sign completion on fresh dev families; feature reranker vs credible baselines.

select   -> target manifests per stratum (no scores) for train-B (vs train-A) and dev (vs all train)
tune     -> train-A statistics, train-B targets only: LM K, reranker lambda/weights, best baseline per stratum -> frozen.json
evaluate -> all-train statistics, dev targets, one shot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from .core import BOS, EOS, ROOT, Run, check_rss, connect, load_universe, h
from .value_common import (VCONFIG, VSEED, artifact_meta, atf_restoration, bucket, clusters, combine, in_range,
                           mean_ci, paired_bootstrap, vconfig_sha, verdict)

E = VCONFIG['e1']
STRATA = ['S1_no_shared_8run', 'S2_no_exact_frame', 'S3_no_restoration', 'ALL']
PRIMARY = STRATA[:3]
D_RUN = ROOT / 'results' / 'D-20261105T221604Z-da7f694'
MAXR = 50


# ------------------------------------------------------------------ universe and roles

def load():
    db = connect()
    u = load_universe(db, with_atf=True, with_near=True)
    meta = artifact_meta(db)
    atf_stats, contam = atf_restoration(db)
    db.close()
    u.atf = None
    role = []
    for i in range(len(u.artifacts)):
        b = bucket(u.family[i])
        role.append('A' if in_range(b, E['train_a_buckets']) else 'B' if in_range(b, E['train_b_buckets'])
                    else 'dev' if in_range(b, E['dev_buckets']) else 'test')
    return u, meta, atf_stats, contam, role


def contaminated_families(u) -> tuple[set, list]:
    s = json.loads((D_RUN / 'summary.json').read_text())
    p = json.loads((D_RUN / 'packet_blinded.json').read_text())
    keys = {q['query_catalog_key'] for q in s['per_query']}
    keys |= {it['candidate_catalog_key'] for it in p['items']} | {it['query_catalog_key'] for it in p['items']}
    by_key = defaultdict(list)
    for i, a in enumerate(u.artifacts):
        by_key[u.catalog.get(a)].append(i)
    fams = {u.family[i] for k in keys for i in by_key.get(k, [])}
    return fams, sorted(keys)


def run_hashes(corpus, line_ids, k):
    out = defaultdict(set)
    for li in line_ids:
        a = corpus.line_art[li]
        run = []
        for x in list(corpus.lines[li]) + [-1]:
            if x >= 0:
                run.append(x)
                continue
            for i in range(len(run) - k + 1):
                out[a].add(hash(tuple(run[i:i + k])))
            run = []
    return out


def ctx5(t, i):
    return (t[i - 2] if i >= 2 else BOS, t[i - 1], t[i + 1], t[i + 2] if i + 2 < len(t) else EOS)


def f2_keys(corpus, line_ids):
    keys = set()
    for li in line_ids:
        t = corpus.lines[li]
        for i in range(1, len(t) - 1):
            if t[i] >= 0 and t[i - 1] >= 0 and t[i + 1] >= 0:
                keys.add(hash(ctx5(t, i)))
    return keys


def strata_targets(u, role, eval_roles, train_lines, atf_stats, excluded_fams):
    """Eligible positions of artifacts in eval_roles, flagged by stratum, relative to the given training lines."""
    k = E['shingle']
    train_runs = set()
    for s in run_hashes(u.signs, train_lines, k).values():
        train_runs |= s
    eval_lines = [li for li, a in enumerate(u.signs.line_art) if role[a] in eval_roles and u.family[a] not in excluded_fams]
    eval_runs = run_hashes(u.signs, eval_lines, k)
    f2 = f2_keys(u.signs, train_lines)
    s1_art, s3_art = {}, {}
    for li in eval_lines:
        a = u.signs.line_art[li]
        if a not in s1_art:
            s1_art[a] = not (eval_runs.get(a, set()) & train_runs)
            n, lost = atf_stats.get(u.artifacts[a], (0, 0))
            s3_art[a] = n >= 1 and lost == 0
    elig = []
    for li in eval_lines:
        t = u.signs.lines[li]
        a = u.signs.line_art[li]
        for i in range(1, len(t) - 1):
            if t[i] >= 0 and t[i - 1] >= 0 and t[i + 1] >= 0:
                flags = {'S1_no_shared_8run': s1_art[a], 'S2_no_exact_frame': hash(ctx5(t, i)) not in f2,
                         'S3_no_restoration': s3_art[a], 'ALL': True}
                elig.append((h(f'{VSEED}:target:{u.signs.line_ids[li]}:{i}'), li, i, flags))
    elig.sort()
    out, avail = {}, {}
    for s in STRATA:
        per_line, per_fam, chosen = Counter(), Counter(), []
        pool = [e for e in elig if e[3][s]]
        avail[s] = {'eligible_positions': len(pool), 'artifacts': len({u.signs.line_art[e[1]] for e in pool})}
        for _, li, i, _f in pool:
            fam = u.family[u.signs.line_art[li]]
            if per_line[li] >= E['caps']['per_line'] or per_fam[fam] >= E['caps']['per_family']:
                continue
            per_line[li] += 1
            per_fam[fam] += 1
            chosen.append([u.signs.line_ids[li], i])
            if len(chosen) == E['per_stratum_max']:
                break
        out[s] = chosen
    flags_of = {(u.signs.line_ids[li], i): f for _, li, i, f in elig}
    return out, avail, flags_of


def overlaps(sel, flags_of):
    union = {tuple(x) for s in STRATA[:3] for x in sel[s]}
    cnt = Counter()
    for key in union:
        f = flags_of[key]
        cnt['+'.join(s.split('_')[0] for s in PRIMARY if f[s]) or 'none'] += 1
    return {'union_primary_targets': len(union), 'membership_counts': dict(sorted(cnt.items()))}


# ------------------------------------------------------------------ statistics and methods

class Stats:
    def __init__(self, u, line_ids, genre_code, period_code):
        c = u.signs
        self.labels, self.lines, self.line_art = c.labels, c.lines, c.line_art
        V = self.V = len(c.labels)
        uni = [0] * V
        c2 = defaultdict(Counter)
        occ = defaultdict(lambda: array('q'))
        for li in line_ids:
            t = c.lines[li]
            L = len(t)
            assert L < 65536
            for i, x in enumerate(t):
                if x < 0:
                    continue
                uni[x] += 1
                if i + 1 < L and t[i + 1] >= 0:
                    c2[x][t[i + 1]] += 1
                if 1 <= i < L - 1 and t[i - 1] >= 0 and t[i + 1] >= 0:
                    occ[(t[i - 1], t[i + 1])].append(li << 16 | i)
            check_rss('stats')
        self.uni, self.c2, self.occ = uni, dict(c2), dict(occ)
        self.out = {x: sum(r.values()) for x, r in self.c2.items()}
        inr = Counter()
        for x, r in self.c2.items():
            for y, n in r.items():
                inr[y] += n
        self.inr = inr
        self.N = sum(uni)
        self.uni_rank = sorted((x for x in range(V) if uni[x]), key=lambda x: (-uni[x], self.labels[x]))
        self.genre, self.period = genre_code, period_code

    def order(self, scores):
        return sorted(scores, key=lambda x: (-scores[x], -self.uni[x], self.labels[x]))

    def scan(self, t, i, gcode, pcode):
        l2, l1, r1, r2 = ctx5(t, i)
        res = {}
        lst = self.occ.get((l1, r1))
        if not lst:
            return res
        lines, la, G, P = self.lines, self.line_art, self.genre, self.period
        for code in lst:
            li = code >> 16
            j = code & 0xFFFF
            u = lines[li]
            Lu = len(u)
            x = u[j]
            mL = (u[j - 2] if j >= 2 else BOS) == l2
            mR = (u[j + 2] if j + 2 < Lu else EOS) == r2
            a = 1
            while a < 8:
                ti, uj = i - 1 - a, j - 1 - a
                if ti < 0 or uj < 0:
                    break
                tv = t[ti]
                if tv < 0 or tv != u[uj]:
                    break
                a += 1
            b = 1
            while b < 8:
                ti, uj = i + 1 + b, j + 1 + b
                if ti >= len(t) or uj >= Lu:
                    break
                tv = t[ti]
                if tv < 0 or tv != u[uj]:
                    break
                b += 1
            r = res.get(x)
            if r is None:
                r = res[x] = [0, 0, 0, 0, Counter(), 0, 0]
            r[0] += 1
            if mL:
                r[1] += 1
            if mR:
                r[2] += 1
            if mL and mR:
                r[3] += 1
            r[4][(a, b)] += 1
            if gcode >= 0 and G[la[li]] == gcode:
                r[5] += 1
            if pcode >= 0 and P[la[li]] == pcode:
                r[6] += 1
        return res

    def cascade(self, t, i, res):
        _, l1, r1, _ = ctx5(t, i)
        lb = self.c2.get(l1, {})
        mk = {}
        if l1 in self.out:
            for x, n in lb.items():
                m = self.c2.get(x, {}).get(r1, 0)
                if m:
                    mk[x] = (n / self.out[l1]) * (m / self.out[x])
        lists = {'frame2': self.order({x: r[3] for x, r in res.items() if r[3]}),
                 'frame1': self.order({x: r[0] for x, r in res.items()}),
                 'markov1_bidir': self.order(mk), 'left_bigram': self.order(dict(lb)), 'unigram': self.uni_rank}
        seen, casc = set(), []
        for m in ('frame2', 'frame1', 'markov1_bidir', 'left_bigram', 'unigram'):
            for x in lists[m]:
                if x not in seen:
                    seen.add(x)
                    casc.append(x)
        lists['cascade'] = casc
        return lists

    @staticmethod
    def best(ab: Counter, K: int):
        bt, bc = 0, 0
        for (a, b), n in ab.items():
            s = min(a, K) + min(b, K)
            if s > bt:
                bt, bc = s, n
            elif s == bt:
                bc += n
        return bt, bc

    def lm(self, res, casc, K):
        pos = {x: k for k, x in enumerate(casc)}
        sc = {x: self.best(r[4], K) for x, r in res.items()}
        head = sorted(sc, key=lambda x: (-sc[x][0], -sc[x][1], pos.get(x, 10**9)))
        hs = set(head)
        return head + [x for x in casc if x not in hs]


RAW_FEATURES = ['log_f2', 'log_fL', 'log_fR', 'log_f1', 'lp_left', 'lp_right', 'lp_rev', 'lp_uni', 'lm_best8', 'lm_max_a', 'lm_max_b',
                'lm_log_n_best', 'cascade_top1', 'lm_top1', 'neglog_cascade_rank', 'neglog_lm_rank']
META_FEATURES = ['log_f1_same_genre', 'log_f1_same_period', 'log_f1_x_genre_missing', 'log_f1_x_period_missing']


def features(st, t, i, x, res, cpos, lpos, gmiss, pmiss):
    _, l1, r1, _ = ctx5(t, i)
    r = res.get(x)
    V = st.V
    c_l = st.c2.get(l1, {}).get(x, 0)
    c_r = st.c2.get(x, {}).get(r1, 0)
    lp_left = math.log((c_l + 0.5) / (st.out.get(l1, 0) + 0.5 * V))
    lp_right = math.log((c_r + 0.5) / (st.out.get(x, 0) + 0.5 * V))
    lp_rev = math.log((c_r + 0.5) / (st.inr.get(r1, 0) + 0.5 * V))
    lp_uni = math.log((st.uni[x] + 0.5) / (st.N + 0.5 * V))
    if r:
        bt, bc = Stats.best(r[4], 8)
        ma = max(a for a, _ in r[4])
        mb = max(b for _, b in r[4])
        f1, fL, fR, f2, g, p = r[0], r[1], r[2], r[3], r[5], r[6]
    else:
        bt = bc = ma = mb = f1 = fL = fR = f2 = g = p = 0
    cr = min(cpos + 1, MAXR)
    lr = min(lpos + 1, MAXR)
    raw = [math.log1p(f2), math.log1p(fL), math.log1p(fR), math.log1p(f1), lp_left, lp_right, lp_rev, lp_uni, bt / 16, ma / 8, mb / 8,
           math.log1p(bc), float(cr == 1), float(lr == 1), -math.log(cr), -math.log(lr)]
    meta = [0.0 if gmiss else math.log1p(g), 0.0 if pmiss else math.log1p(p), math.log1p(f1) * gmiss, math.log1p(f1) * pmiss]
    return raw, meta


def rank_of(lst, y):
    for k, x in enumerate(lst):
        if x == y:
            return k + 1
    return 0


def predict_target(st, t, i, gcode, pcode, K):
    res = st.scan(t, i, gcode, pcode)
    lists = st.cascade(t, i, res)
    casc = lists['cascade']
    lm = st.lm(res, casc, K)
    cand = []
    seen = set()
    for x in casc[:E['candidate_pool']['cascade_top']] + lm[:E['candidate_pool']['lm_top']]:
        if x not in seen:
            seen.add(x)
            cand.append(x)
    cpos = {x: k for k, x in enumerate(casc)}
    lpos = {x: k for k, x in enumerate(lm)}
    feats = [features(st, t, i, x, res, cpos[x], lpos[x], gcode < 0, pcode < 0) for x in cand]
    return res, lists, lm, cand, feats


def rerank(cand, feats, casc, w, mean, std, use_meta):
    sc = []
    for x, (raw, meta) in zip(cand, feats):
        f = raw + meta if use_meta else raw
        sc.append(sum(wi * (fi - m) / s for wi, fi, m, s in zip(w, f, mean, std)))
    order = sorted(range(len(cand)), key=lambda k: (-sc[k], k))
    head = [cand[k] for k in order]
    hs = set(head)
    return head + [x for x in casc if x not in hs]


# ------------------------------------------------------------------ conditional logit (Newton, DuckDB aggregates)

def solve(A, bvec):
    n = len(bvec)
    M = [row[:] + [bvec[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c] / pv
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


class Logit:
    def __init__(self, data, d, workdir: Path):
        """data: list of (target_key, fold, pos_index, [feature vectors])."""
        import duckdb
        self.d = d
        rows = [fv for _, _, _, fvs in data for fv in fvs]
        n = len(rows)
        self.mean = [sum(r[k] for r in rows) / n for k in range(d)]
        self.std = []
        for k in range(d):
            v = sum((r[k] - self.mean[k]) ** 2 for r in rows) / n
            self.std.append(math.sqrt(v) if v > 1e-12 else 1.0)
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / f'logit-{d}.csv'
        with open(path, 'w') as f:
            f.write('t,fold,y,' + ','.join(f'f{k}' for k in range(d)) + '\n')
            for ti, (_, fold, pos, fvs) in enumerate(data):
                for ci, fv in enumerate(fvs):
                    z = [(fv[k] - self.mean[k]) / self.std[k] for k in range(d)]
                    f.write(f'{ti},{fold},{1 if ci == pos else 0},' + ','.join(repr(x) for x in z) + '\n')
        self.db = duckdb.connect()
        self.db.execute(f"SET threads={VCONFIG['resources']['duckdb_threads']}")
        self.db.execute(f"CREATE TABLE r AS SELECT * FROM read_csv('{path}', header=true)")
        path.unlink()

    def fit(self, lam, folds, iters):
        d = self.d
        w = [0.0] * d
        fl = ','.join(str(f) for f in folds)
        cols = ','.join(f'f{k}' for k in range(d))
        pairs = [(i, j) for i in range(d) for j in range(i, d)]
        for _ in range(iters):
            z = '+'.join(f'f{k}*({w[k]!r})' for k in range(d))
            base = (f"WITH s AS (SELECT t,y,{cols},{z} AS z FROM r WHERE fold IN ({fl})), m AS (SELECT t,max(z) mz FROM s GROUP BY t), "
                    "e AS (SELECT s.*, exp(z-mz) ez FROM s JOIN m USING(t)), n AS (SELECT t,sum(ez) se FROM e GROUP BY t), "
                    "p AS (SELECT e.*, ez/se AS p FROM e JOIN n USING(t))")
            q1 = base + ' SELECT ' + ','.join(f'sum((y-p)*f{k})' for k in range(d)) + ',' + ','.join(f'sum(p*f{i}*f{j})' for i, j in pairs) + ' FROM p'
            q2 = base + ', mu AS (SELECT t,' + ','.join(f'sum(p*f{k}) m{k}' for k in range(d)) + ' FROM p GROUP BY t) SELECT ' + \
                ','.join(f'sum(m{i}*m{j})' for i, j in pairs) + ' FROM mu'
            r1 = self.db.execute(q1).fetchone()
            r2 = self.db.execute(q2).fetchone()
            g = list(r1[:d])
            H = [[0.0] * d for _ in range(d)]
            for (i, j), a, b in zip(pairs, r1[d:], r2):
                H[i][j] = H[j][i] = a - b
            for k in range(d):
                H[k][k] += lam
                g[k] -= lam * w[k]
            step = solve(H, g)
            w = [wi + si for wi, si in zip(w, step)]
            if max(abs(s) for s in step) < 1e-6:
                break
        return w


# ------------------------------------------------------------------ commands

def codes(u, meta):
    gmap, pmap = {}, {}
    G = array('i', [-1] * len(u.artifacts))
    P = array('i', [-1] * len(u.artifacts))
    for i, a in enumerate(u.artifacts):
        g, p, _ = meta.get(a, (None, None, None))
        if g:
            G[i] = gmap.setdefault(g, len(gmap))
        if p:
            P[i] = pmap.setdefault(p, len(pmap))
    return G, P


def cmd_select(a):
    run = Run('V1select', allow_dirty=a.allow_dirty)
    u, meta, atf_stats, contam, role = load()
    run.mark('load')
    excl_fams, excl_keys = contaminated_families(u)
    fam_role = {}
    for i in range(len(u.artifacts)):
        fam_role[u.family[i]] = role[i]
    dev_excl = sorted(f for f in excl_fams if fam_role.get(f) == 'dev')
    lines_A = [li for li, x in enumerate(u.signs.line_art) if role[x] == 'A']
    lines_T = [li for li, x in enumerate(u.signs.line_art) if role[x] in ('A', 'B')]
    selB, availB, flagsB = strata_targets(u, role, {'B'}, lines_A, atf_stats, set())
    run.mark('select_B')
    selD, availD, flagsD = strata_targets(u, role, {'dev'}, lines_T, atf_stats, set(dev_excl))
    run.mark('select_dev')
    # parity: scan-based cascade must reproduce Stage C on its (already scored) test targets
    from .stage_c import select_targets
    from .core import CONFIG
    G, P = codes(u, meta)
    st = Stats(u, lines_T, G, P)
    u.split = ['train' if r in ('A', 'B') else 'dev' if r == 'dev' else 'test' for r in role]
    tC, _ = select_targets(u, CONFIG['stage_c']['n_targets'], CONFIG['stage_c']['caps'])
    hits = 0
    for li, i in tC:
        t = u.signs.lines[li]
        res = st.scan(t, i, -1, -1)
        hits += st.cascade(t, i, res)['cascade'][0] == t[i]
    run.mark('parity')
    fam_counts = Counter(fam_role.values())
    sel = {'protocol': 'cb-value-1', 'value_config_sha256': vconfig_sha(),
           'train_B': {'targets': selB, 'available': availB, 'overlaps': overlaps(selB, flagsB), 'relative_to': 'train-A (buckets 0-49)'},
           'dev': {'targets': selD, 'available': availD, 'overlaps': overlaps(selD, flagsD), 'relative_to': 'all train (buckets 0-59)'},
           'families_by_role': dict(fam_counts),
           'dev_contamination': {'source_catalog_keys': len(excl_keys), 'dev_families_excluded': len(dev_excl), 'families_sha256': h('\n'.join(dev_excl)),
                                 'families': dev_excl},
           'atf_contamination_lines_excluded_from_S3': {'lines': len(contam), 'artifacts': len({c[0] for c in contam}), 'manifest': contam},
           'parity_stage_c_cascade_top1': {'hits': hits, 'n': len(tC), 'expected_hits': 1013}}
    for part in ('train_B', 'dev'):
        sel[part]['targets_sha256'] = {s: h(json.dumps(v)) for s, v in sel[part]['targets'].items()}
        sel[part]['n'] = {s: len(v) for s, v in sel[part]['targets'].items()}
    run.write('selection.json', sel)
    rec = run.finish({'value_protocol': 'cb-value-1', 'parity': sel['parity_stage_c_cascade_top1'],
                      'n': {p: sel[p]['n'] for p in ('train_B', 'dev')}, 'available': {p: sel[p]['available'] for p in ('train_B', 'dev')}})
    print(json.dumps(rec, indent=1, default=str))


def index_lines(u):
    return {lid: li for li, lid in enumerate(u.signs.line_ids)}


def score_targets(st, u, li_of, targets, G, P, K):
    out = []
    for line_id, i in targets:
        li = li_of[line_id]
        t = u.signs.lines[li]
        art = u.signs.line_art[li]
        res, lists, lm, cand, feats = predict_target(st, t, i, G[art], P[art], K)
        out.append({'key': (line_id, i), 'art': art, 'y': t[i], 'lists': lists, 'lm': lm, 'cand': cand, 'feats': feats, 'res': res, 't': t, 'i': i})
        check_rss('score')
    return out


def cmd_tune(a):
    sel = json.loads(Path(a.selection).read_text())
    run = Run('V1tune', allow_dirty=a.allow_dirty)
    u, meta, _, _, role = load()
    G, P = codes(u, meta)
    lines_A = [li for li, x in enumerate(u.signs.line_art) if role[x] == 'A']
    st = Stats(u, lines_A, G, P)
    run.mark('stats_A')
    li_of = index_lines(u)
    tg = sel['train_B']['targets']
    union = sorted({tuple(x) for s in STRATA for x in tg[s]})
    member = {s: {tuple(x) for x in tg[s]} for s in STRATA}
    scored = score_targets(st, u, li_of, union, G, P, 8)
    run.mark('scan_B')
    # LM K grid and baselines on train-B, per stratum
    grid = {}
    for K in E['lm_K_grid']:
        hits = {s: 0 for s in STRATA}
        for r in scored:
            lm = st.lm(r['res'], r['lists']['cascade'], K)
            for s in STRATA:
                if r['key'] in member[s]:
                    hits[s] += lm[0] == r['y']
        grid[K] = {s: round(hits[s] / max(1, len(member[s])), 4) for s in STRATA}
    K = max(E['lm_K_grid'], key=lambda k: (round(sum(grid[k][s] for s in PRIMARY) / 3, 6), -k))
    casc = {s: round(sum(r['lists']['cascade'][0] == r['y'] for r in scored if r['key'] in member[s]) / max(1, len(member[s])), 4) for s in STRATA}
    best_base = {s: ('longest_match' if grid[K][s] > casc[s] else 'cascade') for s in STRATA}
    # reranker data with the tuned K (rank features use LM at K)
    data_raw, data_meta = [], []
    for r in scored:
        lm = st.lm(r['res'], r['lists']['cascade'], K)
        casc_l = r['lists']['cascade']
        cand = []
        seen = set()
        for x in casc_l[:E['candidate_pool']['cascade_top']] + lm[:E['candidate_pool']['lm_top']]:
            if x not in seen:
                seen.add(x)
                cand.append(x)
        cpos = {x: k for k, x in enumerate(casc_l)}
        lpos = {x: k for k, x in enumerate(lm)}
        art = r['art']
        feats = [features(st, r['t'], r['i'], x, r['res'], cpos[x], lpos[x], G[art] < 0, P[art] < 0) for x in cand]
        r['cand'], r['feats'] = cand, feats
        if r['y'] not in seen:
            continue
        pos = cand.index(r['y'])
        fold = int(h(f'{VSEED}:fold:{u.family[art]}')[:8], 16) % E['cv_folds']
        data_raw.append((r['key'], fold, pos, [f[0] for f in feats]))
        data_meta.append((r['key'], fold, pos, [f[0] + f[1] for f in feats]))
    run.mark('features')
    work = ROOT / 'out' / 'value-tmp'
    frozen = {'protocol': 'cb-value-1', 'value_config_sha256': vconfig_sha(), 'selection_sha256': hashlib.sha256(Path(a.selection).read_bytes()).hexdigest(),
              'lm_K': K, 'lm_grid_top1_trainB': grid, 'cascade_top1_trainB': casc, 'best_baseline': best_base,
              'trainB_targets': len(scored), 'trainB_targets_with_target_in_pool': len(data_raw), 'models': {}}
    fold_of = {r['key']: int(h(f'{VSEED}:fold:{u.family[r["art"]]}')[:8], 16) % E['cv_folds'] for r in scored}
    for name, data, use_meta in (('rerank_raw', data_raw, False), ('rerank_meta', data_meta, True)):
        d = len(data[0][3][0])
        lg = Logit(data, d, work)
        cv = {}
        for lam in E['lambda_grid']:
            hits = {s: 0 for s in STRATA}
            for f in range(E['cv_folds']):
                w = lg.fit(lam, [g for g in range(E['cv_folds']) if g != f], E['newton_iters'])
                for r in scored:
                    if fold_of[r['key']] != f:
                        continue
                    top = rerank(r['cand'], r['feats'], r['lists']['cascade'], w, lg.mean, lg.std, use_meta)[0]
                    for s in STRATA:
                        if r['key'] in member[s]:
                            hits[s] += top == r['y']
            cv[lam] = {s: round(hits[s] / max(1, len(member[s])), 4) for s in STRATA}
            run.mark(f'{name}_cv_{lam}')
        lam = max(E['lambda_grid'], key=lambda l: (round(sum(cv[l][s] for s in PRIMARY) / 3, 6), l))
        w = lg.fit(lam, list(range(E['cv_folds'])), E['newton_iters'])
        frozen['models'][name] = {'lambda': lam, 'cv_top1_trainB': cv, 'features': RAW_FEATURES + (META_FEATURES if use_meta else []),
                                  'weights': w, 'mean': lg.mean, 'std': lg.std}
    sha = run.write('frozen.json', frozen)
    rec = run.finish({'value_protocol': 'cb-value-1', 'frozen_sha256': sha, 'lm_K': K, 'best_baseline': best_base,
                      'lambda': {m: frozen['models'][m]['lambda'] for m in frozen['models']}})
    print(json.dumps(rec, indent=1, default=str))


def cmd_evaluate(a):
    sel_bytes = Path(a.selection).read_bytes()
    frozen = json.loads(Path(a.frozen).read_text())
    if frozen['selection_sha256'] != hashlib.sha256(sel_bytes).hexdigest():
        raise SystemExit('selection hash differs from the one frozen at tuning')
    sel = json.loads(sel_bytes)
    run = Run('V1eval', allow_dirty=a.allow_dirty)
    u, meta, _, _, role = load()
    clu = clusters(u)
    G, P = codes(u, meta)
    lines_T = [li for li, x in enumerate(u.signs.line_art) if role[x] in ('A', 'B')]
    st = Stats(u, lines_T, G, P)
    run.mark('stats_all_train')
    li_of = index_lines(u)
    tg = sel['dev']['targets']
    union = sorted({tuple(x) for s in STRATA for x in tg[s]})
    member = {s: {tuple(x) for x in tg[s]} for s in STRATA}
    K = frozen['lm_K']
    scored = score_targets(st, u, li_of, union, G, P, K)
    run.mark('scan_dev')
    methods = ['unigram', 'markov1_bidir', 'cascade', 'longest_match', 'rerank_raw', 'rerank_meta']
    rows = []
    for r in scored:
        casc = r['lists']['cascade']
        ranked = {'unigram': r['lists']['unigram'], 'markov1_bidir': r['lists']['markov1_bidir'], 'cascade': casc, 'longest_match': r['lm']}
        for m in ('rerank_raw', 'rerank_meta'):
            mm = frozen['models'][m]
            ranked[m] = rerank(r['cand'], r['feats'], casc, mm['weights'], mm['mean'], mm['std'], m == 'rerank_meta')
        row = {'line_id': r['key'][0], 'position': r['key'][1], 'catalog_key': u.catalog[u.artifacts[r['art']]], 'cluster': clu[r['art']],
               'target': u.signs.label(r['y']), 'strata': [s for s in STRATA if r['key'] in member[s]],
               'genre_known': G[r['art']] >= 0, 'period_known': P[r['art']] >= 0}
        for m in methods:
            k = rank_of(ranked[m], r['y'])
            row[m] = {'rank': k, 'top5': [u.signs.label(x) for x in ranked[m][:5]]}
            row[f'{m}_top1'] = int(k == 1)
            row[f'{m}_top5'] = int(1 <= k <= 5)
            row[f'{m}_rr'] = 1 / k if k else 0.0
        rows.append(row)
    run.mark('predict')
    reps = E['bootstrap']['reps']
    thr = E['practical_threshold_top1']
    summary = {'protocol': 'cb-value-1', 'frozen_sha256': hashlib.sha256(Path(a.frozen).read_bytes()).hexdigest(), 'lm_K': K,
               'best_baseline': frozen['best_baseline'], 'strata': {}, 'claims': {}}
    for s in STRATA:
        rs = [r for r in rows if s in r['strata']]
        best = frozen['best_baseline'][s]
        for r in rs:
            r['_best_top1'] = r[f'{best}_top1']
        ent = {'n': len(rs), 'artifacts': len({r['catalog_key'] for r in rs}), 'clusters': len({r['cluster'] for r in rs}), 'best_baseline': best,
               'genre_known_rate': round(sum(r['genre_known'] for r in rs) / max(1, len(rs)), 3), 'methods': {}}
        for m in methods:
            ent['methods'][m] = {k: mean_ci(rs, f'{m}_{k}', 'cluster', reps, f'{VSEED}:ci:{s}:{m}:{k}') for k in ('top1', 'top5', 'rr')}
        ent['C1_rerank_meta_minus_best'] = paired_bootstrap(rs, 'rerank_meta_top1', '_best_top1', 'cluster', reps, f'{VSEED}:C1:{s}')
        ent['C2_rerank_meta_minus_rerank_raw'] = paired_bootstrap(rs, 'rerank_meta_top1', 'rerank_raw_top1', 'cluster', reps, f'{VSEED}:C2:{s}')
        ent['rerank_raw_minus_best'] = paired_bootstrap(rs, 'rerank_raw_top1', '_best_top1', 'cluster', reps, f'{VSEED}:RAW:{s}')
        ent['lm_minus_cascade'] = paired_bootstrap(rs, 'longest_match_top1', 'cascade_top1', 'cluster', reps, f'{VSEED}:LM:{s}')
        for r in rs:
            r.pop('_best_top1')
        for c in ('C1_rerank_meta_minus_best', 'C2_rerank_meta_minus_rerank_raw'):
            ent[c]['verdict'] = verdict(ent[c]['point'], ent[c]['ci95'], thr) if ent[c] else 'inconclusive'
        summary['strata'][s] = ent
    for c in ('C1_rerank_meta_minus_best', 'C2_rerank_meta_minus_rerank_raw'):
        summary['claims'][c] = combine([summary['strata'][s][c]['verdict'] for s in PRIMARY])
    run.write('summary.json', summary)
    run.write('predictions.json', rows, raw=True)
    rec = run.finish({'value_protocol': 'cb-value-1', 'claims': summary['claims'], 'n': {s: summary['strata'][s]['n'] for s in STRATA}})
    print(json.dumps(rec, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('select')
    s.add_argument('--allow-dirty', action='store_true')
    t = sp.add_parser('tune')
    t.add_argument('--selection', required=True)
    t.add_argument('--allow-dirty', action='store_true')
    e = sp.add_parser('evaluate')
    e.add_argument('--selection', required=True)
    e.add_argument('--frozen', required=True)
    e.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    {'select': cmd_select, 'tune': cmd_tune, 'evaluate': cmd_evaluate}[a.cmd](a)


if __name__ == '__main__':
    main()
