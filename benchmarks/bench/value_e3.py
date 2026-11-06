"""E3 (cb-value-1): fragment retrieval against published eBL chapter manuscript lists (independent gold, incomplete).

Recall/hit over known associations only (unlisted candidates are unlabelled). No tuning.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
from collections import Counter, defaultdict

from .core import ROOT, Run, connect, load_universe, check_rss
from .value_common import VCONFIG, VSEED, artifact_meta, combine, mean_ci, paired_bootstrap, vconfig_sha, verdict

E = VCONFIG['e3']
BASELINES = ['ovl', 'ovl_len_tfidf', 'tfidf_cosine', 'bm25', 'seed_align']
TOPN = 1000


def gold(u, exclude_types):
    idx = defaultdict(list)
    for i, a in enumerate(u.artifacts):
        idx[u.catalog.get(a)].append(i)
    chapters, files = {}, {}
    for f in sorted(glob.glob(str(ROOT / E['gold_dir'] / 'ms_*.json'))):
        raw = open(f, 'rb').read()
        name = f.rsplit('/', 1)[1][3:-5]
        files[name] = hashlib.sha256(raw).hexdigest()
        ms = json.loads(raw)
        chapters[name] = {'listed': len(ms), 'in_corpus': sorted({i for m in ms if m['type'] not in exclude_types for i in idx.get(m['museumNumber'], [])}),
                          'types_in_corpus': dict(Counter(m['type'] for m in ms if m['museumNumber'] in idx))}
    member = defaultdict(set)
    for ch, d in chapters.items():
        for i in d['in_corpus']:
            member[i].add(ch)
    queries = []
    for q in sorted(member, key=lambda i: u.artifacts[i]):
        rel = {j for ch in member[q] for j in chapters[ch]['in_corpus'] if u.family[j] != u.family[q]}
        if rel:
            queries.append((q, sorted(member[q]), sorted(rel)))
    return chapters, files, queries


def runs_of(lines):
    for t in lines:
        run = []
        for x in list(t) + [-1]:
            if x >= 0:
                run.append(x)
                continue
            if run:
                yield run
            run = []


def build_docs(seqs, ns):
    docs = {}
    for a, lines in seqs.items():
        c = Counter()
        for run in runs_of(lines):
            for n in ns:
                for i in range(len(run) - n + 1):
                    c[tuple(run[i:i + n])] += 1
        if c:
            docs[a] = c
    return docs


class Index:
    def __init__(self, docs, ns):
        self.docs = docs
        N = self.N = len(docs)
        post = defaultdict(list)
        for a, c in docs.items():
            for g in c:
                post[g].append(a)
        self.post = post
        self.idf = {g: math.log((1 + N) / (1 + len(v))) for g, v in post.items()}
        self.bm_idf = {g: math.log(1 + (N - len(v) + 0.5) / (len(v) + 0.5)) for g, v in post.items()}
        idf = self.idf
        self.size = {a: len(c) for a, c in docs.items()}
        self.wlen = {a: sum(len(g) ** 2 * idf[g] for g in c) for a, c in docs.items()}
        self.norm = {a: math.sqrt(sum(idf[g] ** 2 for g in c)) for a, c in docs.items()}
        self.dl = {a: sum(c.values()) for a, c in docs.items()}
        self.avgdl = sum(self.dl.values()) / N

    def scores(self, q, excl):
        F = self.docs[q]
        k1, b = E['bm25']['k1'], E['bm25']['b']
        inter, winter, cos, bm = Counter(), defaultdict(float), defaultdict(float), defaultdict(float)
        for g in F:
            w = len(g) ** 2 * self.idf[g]
            i2 = self.idf[g] ** 2
            bi = self.bm_idf[g]
            for c in self.post[g]:
                if c in excl:
                    continue
                inter[c] += 1
                winter[c] += w
                cos[c] += i2
                tf = self.docs[c][g]
                bm[c] += bi * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.dl[c] / self.avgdl))
        out = {'ovl': {}, 'ovl_len_tfidf': {}, 'tfidf_cosine': {}, 'bm25': dict(bm)}
        for c in inter:
            out['ovl'][c] = inter[c] / min(len(F), self.size[c])
            m = min(self.wlen[q], self.wlen[c])
            out['ovl_len_tfidf'][c] = winter[c] / m if m > 0 else 0.0
            nn = self.norm[q] * self.norm[c]
            out['tfidf_cosine'][c] = cos[c] / nn if nn > 0 else 0.0
        return out


def seq_flat(lines):
    s = []
    for t in lines:
        s.extend(t)
        s.append(-9)
    return s


def seed_score(qseq, cseq, n):
    pos = defaultdict(list)
    for i in range(len(qseq) - n + 1):
        g = qseq[i:i + n]
        if min(g) >= 0:
            pos[tuple(g)].append(i)
    diag = Counter()
    for j in range(len(cseq) - n + 1):
        g = tuple(cseq[j:j + n])
        for i in pos.get(g, ()):
            diag[j - i] += 1
    return max(diag.values()) if diag else 0


def topk(scores, artifacts, k=TOPN):
    return sorted(scores, key=lambda c: (-scores[c], artifacts[c]))[:k]


def metrics(ranked, rel):
    rel = set(rel)
    first = next((k + 1 for k, c in enumerate(ranked) if c in rel), 0)
    return {'hit10': int(0 < first <= 10), 'recall10': len(rel & set(ranked[:10])) / len(rel), 'recall100': len(rel & set(ranked[:100])) / len(rel),
            'rr': 1 / first if first else 0.0, 'first_rank': first}


def evaluate(u, meta, idx, seqs, queries, chapters_of_query, label):
    rows = []
    for q, chs, rel in queries:
        if q not in idx.docs:
            continue
        excl = {i for i in idx.docs if u.family[i] == u.family[q]}
        sc = idx.scores(q, excl)
        ranked = {m: topk(sc[m], u.artifacts) for m in ('ovl', 'ovl_len_tfidf', 'tfidf_cosine', 'bm25')}
        pool = list(dict.fromkeys(ranked['bm25'][:E['rerank_pool']] + ranked['tfidf_cosine'][:E['rerank_pool']]))
        qs = seq_flat(seqs[q])
        sa = {c: seed_score(qs, seq_flat(seqs[c]), E['seed_n']) for c in pool}
        bmpos = {c: k for k, c in enumerate(ranked['bm25'])}
        head = sorted(pool, key=lambda c: (-sa[c], bmpos.get(c, 10**9), u.artifacts[c]))
        hs = set(head)
        ranked['seed_align'] = head + [c for c in ranked['bm25'] if c not in hs]
        qg = meta[u.artifacts[q]][0]
        top = ranked['bm25'][:E['rerank_pool']]
        if qg:
            same = [c for c in top if meta[u.artifacts[c]][0] == qg]
            other = [c for c in top if meta[u.artifacts[c]][0] != qg]
            top = same + other
        ranked['bm25+genre'] = top + ranked['bm25'][E['rerank_pool']:]
        row = {'query': u.catalog[u.artifacts[q]], 'cluster': chs[0], 'chapters': chs, 'n_relevant': len(rel), 'query_genre2': qg,
               'query_signs': sum(1 for t in seqs[q] for x in t if x >= 0)}
        for m, lst in ranked.items():
            mm = metrics(lst, rel)
            row[m] = mm
            for k, v in mm.items():
                row[f'{m}_{k}'] = v
        rows.append(row)
        check_rss(label)
    return rows


def summarize(rows, methods, reps, tag):
    out = {'queries': len(rows), 'chapters': len({r['cluster'] for r in rows}), 'methods': {}}
    for m in methods:
        out['methods'][m] = {k: mean_ci(rows, f'{m}_{k}', 'cluster', reps, f'{VSEED}:{tag}:{m}:{k}') for k in ('hit10', 'recall10', 'recall100', 'rr')}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    run = Run('V3', allow_dirty=a.allow_dirty)
    db = connect()
    u = load_universe(db, with_atf=True, with_near=False)
    meta = artifact_meta(db)
    # ATF readings without editorial restorations (sensitivity), excluding contaminated '#'-label lines
    atf_rows = db.execute(
        "SELECT w.artifact_id, l.id, o.position, o.reading, o.kind, o.preservation FROM occurrence o JOIN line l ON o.line_id=l.id "
        "JOIN sequence_version s ON s.id=l.sequence_id JOIN witness w ON w.id=s.witness_id WHERE s.field='atf' AND l.label NOT LIKE '#%' "
        "ORDER BY w.artifact_id, l.ordinal, o.position").fetchall()
    db.close()
    ai = {x: i for i, x in enumerate(u.artifacts)}
    vocab = {}
    atf_seqs = defaultdict(list)
    cur = None
    for art, lid, pos, reading, kind, pres in atf_rows:
        if lid != cur:
            atf_seqs[ai[art]].append([])
            cur = lid
        ok = kind == 'reading' and pres != 'lost'
        atf_seqs[ai[art]][-1].append(vocab.setdefault(reading, len(vocab)) if ok else -1)
    del atf_rows
    run.mark('load')
    seqs = defaultdict(list)
    for li, t in enumerate(u.signs.lines):
        seqs[u.signs.line_art[li]].append(list(t))
    ns = E['n_values']
    reps = E['bootstrap']['reps']
    thr = E['practical_threshold_recall10']
    chapters, files, queries = gold(u, set(E['exclude_types_primary']))
    _, _, queries_s = gold(u, set(E['exclude_types_sensitivity']))
    idx = Index(build_docs(seqs, ns), ns)
    run.mark('index_signs')
    rows = evaluate(u, meta, idx, seqs, queries, None, 'primary')
    rows_s = evaluate(u, meta, idx, seqs, queries_s, None, 'sens_quotation')
    run.mark('queries_signs')
    methods = BASELINES + ['bm25+genre']
    prim = summarize(rows, methods, reps, 'prim')
    best = max(BASELINES, key=lambda m: (prim['methods'][m]['recall10']['point'], m))
    for r in rows:
        r['_best'] = r[f'{best}_recall10']
    c4 = paired_bootstrap(rows, 'bm25+genre_recall10', '_best', 'cluster', reps, f'{VSEED}:C4')
    for r in rows:
        r.pop('_best')
    c4['verdict'] = verdict(c4['point'], c4['ci95'], thr)
    c4['best_baseline'] = best
    pairs = {f'{m}_minus_ovl': paired_bootstrap(rows, f'{m}_recall10', 'ovl_recall10', 'cluster', reps, f'{VSEED}:vs_ovl:{m}') for m in methods if m != 'ovl'}
    del idx
    aidx = Index(build_docs(atf_seqs, ns), ns)
    run.mark('index_atf')
    rows_a = evaluate(u, meta, aidx, atf_seqs, queries, None, 'atf_unrestored')
    sens_atf = summarize(rows_a, methods, reps, 'atf')
    summary = {'protocol': 'cb-value-1', 'value_config_sha256': vconfig_sha(), 'gold_files_sha256': files,
               'gold': {'chapters': len(chapters), 'listed_manuscripts': sum(d['listed'] for d in chapters.values()),
                        'in_corpus_non_commentary_quotation': sum(len(d['in_corpus']) for d in chapters.values()),
                        'per_chapter': chapters, 'queries_primary': len(queries), 'queries_with_quotation': len(queries_s),
                        'query_genre_known': sum(1 for r in rows if r['query_genre2']),
                        'query_genres': dict(Counter(r['query_genre2'] for r in rows))},
               'primary': prim, 'C4': c4, 'vs_published_ovl': pairs,
               'sensitivity_with_quotation': summarize(rows_s, methods, reps, 'sensq'),
               'sensitivity_atf_unrestored': sens_atf,
               'ties_note': 'ties broken by artifact id (as Stage D)'}
    run.write('summary.json', summary)
    run.write('per_query.json', [{k: v for k, v in r.items() if not any(k.startswith(m + '_') for m in methods)} for r in rows], raw=True)
    rec = run.finish({'value_protocol': 'cb-value-1', 'C4': c4, 'queries': len(rows)})
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
