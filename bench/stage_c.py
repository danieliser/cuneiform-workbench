"""Stage C: masked edited-sign completion diagnostic. Train-only statistics, family-held-out test targets, no LLM."""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict

from .core import BOS, CONFIG, EOS, SEED, Run, connect, load_universe, split_manifest, check_rss, h

C = CONFIG['stage_c']
METHODS = ['unigram', 'left_bigram', 'markov1_bidir', 'frame1', 'frame2', 'cascade']


class Model:
    """Statistics from training lines only. predict() sees a context tuple (L2, L1, R1, R2); the target is never passed."""

    def __init__(self, corpus, line_ids):
        self.labels = corpus.labels
        self.uni = Counter()
        self.c2 = defaultdict(Counter)
        self.f1 = defaultdict(Counter)
        self.f2 = defaultdict(Counter)
        for li in line_ids:
            t = corpus.lines[li]
            L = len(t)
            for i, x in enumerate(t):
                if x < 0:
                    continue
                self.uni[x] += 1
                if i + 1 < L and t[i + 1] >= 0:
                    self.c2[x][t[i + 1]] += 1
                l1 = t[i - 1] if i >= 1 else BOS
                r1 = t[i + 1] if i + 1 < L else EOS
                l2 = t[i - 2] if i >= 2 else BOS
                r2 = t[i + 2] if i + 2 < L else EOS
                self.f1[(l1, r1)][x] += 1
                self.f2[(l2, l1, r1, r2)][x] += 1
        self.out = {x: sum(r.values()) for x, r in self.c2.items()}
        self.uni_rank = sorted(self.uni, key=lambda x: (-self.uni[x], self.labels[x]))

    def order(self, scores: dict) -> list:
        return sorted(scores, key=lambda x: (-scores[x], -self.uni[x], self.labels[x]))

    def rank_lists(self, ctx) -> dict:
        l2, l1, r1, r2 = ctx
        out = {'unigram': self.uni_rank}
        lb = self.c2.get(l1, {})
        out['left_bigram'] = self.order(dict(lb))
        mk = {}
        if l1 in self.out:
            for x, n in lb.items():
                m = self.c2.get(x, {}).get(r1, 0)
                if m:
                    mk[x] = (n / self.out[l1]) * (m / self.out[x])
        out['markov1_bidir'] = self.order(mk)
        out['frame1'] = self.order(dict(self.f1.get((l1, r1), {})))
        out['frame2'] = self.order(dict(self.f2.get((l2, l1, r1, r2), {})))
        seen, casc = set(), []
        for m in ('frame2', 'frame1', 'markov1_bidir', 'left_bigram', 'unigram'):
            for x in out[m]:
                if x not in seen:
                    seen.add(x)
                    casc.append(x)
        out['cascade'] = casc
        return out


def select_targets(u, n, caps):
    elig = []
    for li, a in enumerate(u.signs.line_art):
        if u.split[a] != C['test_split']:
            continue
        t = u.signs.lines[li]
        for i in range(1, len(t) - 1):
            if t[i] >= 0 and t[i - 1] >= 0 and t[i + 1] >= 0:
                elig.append((h(f"{SEED}:target:{u.signs.line_ids[li]}:{i}"), li, i))
    elig.sort()
    per_line, per_fam, chosen = Counter(), Counter(), []
    for _, li, i in elig:
        fam = u.family[u.signs.line_art[li]]
        if per_line[li] >= caps['per_line'] or per_fam[fam] >= caps['per_family']:
            continue
        per_line[li] += 1
        per_fam[fam] += 1
        chosen.append((li, i))
        if len(chosen) == n:
            break
    return chosen, len(elig)


def context(t, i):
    L = len(t)
    return (t[i - 2] if i >= 2 else BOS, t[i - 1], t[i + 1], t[i + 2] if i + 2 < L else EOS)


def freq_bin(n):
    return 'OOV(0)' if n == 0 else '1-9' if n < 10 else '10-99' if n < 100 else '100-999' if n < 1000 else '1000+'


def support_bin(n):
    return '0' if n == 0 else '1-4' if n < 5 else '5-49' if n < 50 else '50+'


def poisson1(rnd):
    u, k, p, cum = rnd.random(), 0, 0.36787944117144233, 0.36787944117144233
    while u > cum and k < 20:
        k += 1
        p /= k
        cum += p
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    run = Run('C', allow_dirty=a.allow_dirty)
    db = connect()
    u = load_universe(db, with_atf=True, with_near=True)
    db.close()
    u.atf = None
    sm = split_manifest(u)
    run.write('split.json', {'sha256': sm['sha256'], 'counts': sm['counts']})
    train = [li for li, art in enumerate(u.signs.line_art) if u.split[art] == C['train_split']]
    model = Model(u.signs, train)          # sealed before any test line is read
    run.mark('train_statistics')
    targets, n_elig = select_targets(u, C['n_targets'], C['caps'])
    train_fams = {u.family[x] for x in range(len(u.artifacts)) if u.split[x] == 'train'}
    rows = []
    for li, i in targets:
        t = u.signs.lines[li]
        ctx = context(t, i)
        lists = model.rank_lists(ctx)       # target hidden
        y = t[i]
        art = u.signs.line_art[li]
        r = {'line_id': u.signs.line_ids[li], 'position': i, 'catalog_key': u.catalog[u.artifacts[art]], 'family': u.family[art],
             'line_label': u.signs.line_labels[li], 'context': [u.signs.label(x) for x in ctx], 'target': u.signs.label(y),
             'target_train_freq': model.uni[y], 'frame1_support': sum(model.f1.get((ctx[1], ctx[2]), {}).values()),
             'frame2_seen': (ctx in model.f2), 'frame2_contains_target': model.f2.get(ctx, {}).get(y, 0) > 0,
             'near_dup_partner_in_train': any(u.family[b] in train_fams for b in u.near['partners'].get(art, ())),
             'restoration_status': 'unknown (signs field is reading-derived; may be editorial restoration)'}
        for m in METHODS:
            lst = lists[m]
            rank = next((k + 1 for k, x in enumerate(lst) if x == y), 0)
            r[m] = {'covered': bool(lst), 'rank': rank, 'top5': [u.signs.label(x) for x in lst[:5]]}
        rows.append(r)
        check_rss('targets')
    run.mark('predict')

    def metrics(rs):
        n = len(rs)
        out = {'n': n}
        for m in METHODS:
            cov = sum(r[m]['covered'] for r in rs)
            t1 = sum(r[m]['rank'] == 1 for r in rs)
            out[m] = {'top1': round(t1 / n, 4) if n else None, 'top5': round(sum(1 <= r[m]['rank'] <= 5 for r in rs) / n, 4) if n else None,
                      'mrr': round(sum(1 / r[m]['rank'] for r in rs if r[m]['rank']) / n, 4) if n else None,
                      'coverage': round(cov / n, 4) if n else None, 'top1_given_covered': round(t1 / cov, 4) if cov else None}
        return out

    overall = metrics(rows)
    # family bootstrap (Poisson(1) weights per test family), 1000 reps
    by_fam = defaultdict(list)
    for r in rows:
        by_fam[r['family']].append(r)
    fam_sums = {}
    for f, rs in by_fam.items():
        fam_sums[f] = {'n': len(rs), **{m: (sum(r[m]['rank'] == 1 for r in rs), sum(1 <= r[m]['rank'] <= 5 for r in rs), sum(1 / r[m]['rank'] for r in rs if r[m]['rank'])) for m in METHODS}}
    rnd = random.Random(f'{SEED}:stageC:boot')
    fams = sorted(fam_sums)
    boot = {m: ([], [], []) for m in METHODS}
    diff = []
    for _ in range(C['bootstrap']['reps']):
        w = {f: poisson1(rnd) for f in fams}
        n = sum(w[f] * fam_sums[f]['n'] for f in fams) or 1
        vals = {}
        for m in METHODS:
            for j in range(3):
                v = sum(w[f] * fam_sums[f][m][j] for f in fams) / n
                boot[m][j].append(v)
            vals[m] = boot[m][0][-1]
        diff.append(vals['cascade'] - vals['markov1_bidir'])

    def ci(xs):
        xs = sorted(xs)
        return [round(xs[int(0.025 * (len(xs) - 1))], 4), round(xs[int(round(0.975 * (len(xs) - 1)))], 4)]
    for m in METHODS:
        overall[m]['top1_ci95'] = ci(boot[m][0])
        overall[m]['top5_ci95'] = ci(boot[m][1])
        overall[m]['mrr_ci95'] = ci(boot[m][2])
    groups = {}
    for gname, fn in (('target_train_freq', lambda r: freq_bin(r['target_train_freq'])), ('frame1_support', lambda r: support_bin(r['frame1_support'])),
                      ('frame2_seen', lambda r: str(r['frame2_seen'])), ('near_dup_partner_in_train', lambda r: str(r['near_dup_partner_in_train']))):
        g = defaultdict(list)
        for r in rows:
            g[fn(r)].append(r)
        groups[gname] = {k: metrics(v) for k, v in sorted(g.items())}
    summary = {
        'targets': len(rows), 'eligible_positions_test': n_elig, 'target_families': len(by_fam),
        'oov_targets': sum(r['target_train_freq'] == 0 for r in rows), 'frame2_contains_target_rate': round(sum(r['frame2_contains_target'] for r in rows) / len(rows), 4),
        'train': {'lines': len(train), 'derived_signs': sum(model.uni.values()), 'vocab': len(model.uni), 'frame1_contexts': len(model.f1), 'frame2_contexts': len(model.f2)},
        'overall': overall, 'cascade_minus_markov1_top1_ci95': ci(diff), 'groups': groups,
        'task': 'masked single edited sign (reading-derived; restoration status unknown); NOT natural-damage restoration',
    }
    run.write('summary.json', summary)
    run.write('predictions.json', rows, raw=True)
    run.write('examples.json', rows[:20])
    cols = ['method', 'top1', 'top1_ci95', 'top5', 'top5_ci95', 'mrr', 'mrr_ci95', 'coverage', 'top1_given_covered']
    run.write('overall.csv', [cols] + [[m] + [overall[m][c] for c in cols[1:]] for m in METHODS])
    gcols = ['group', 'bin', 'n'] + [f'{m}_{k}' for m in METHODS for k in ('top1', 'top5', 'mrr', 'coverage')]
    grows = [gcols]
    for g, bins in groups.items():
        for b, mm in bins.items():
            grows.append([g, b, mm['n']] + [mm[m][k] for m in METHODS for k in ('top1', 'top5', 'mrr', 'coverage')])
    run.write('groups.csv', grows)
    rec = run.finish({'summary': {k: summary[k] for k in ('targets', 'eligible_positions_test', 'target_families', 'oov_targets', 'frame2_contains_target_rate', 'train')}})
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
