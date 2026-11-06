"""Stage D: paper-equivalent local n-gram retrieval baseline + blinded pooled candidate packet (no gold, no accuracy)."""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict

from .core import CONFIG, SEED, Run, connect, load_universe, h

D = CONFIG['stage_d']


def doc_grams(corpus, line_ids):
    g = set()
    for li in line_ids:
        t = corpus.lines[li]
        run = []
        for x in list(t) + [-1]:
            if x >= 0:
                run.append(x)
                continue
            for n in (1, 2, 3):
                for i in range(len(run) - n + 1):
                    g.add(tuple(run[i:i + n]))
            run = []
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-dirty', action='store_true')
    a = ap.parse_args()
    run = Run('D', allow_dirty=a.allow_dirty)
    db = connect()
    u = load_universe(db, with_atf=True, with_near=True)
    db.close()
    u.atf = None
    al = u.signs.art_lines()
    docs = {art: doc_grams(u.signs, lines) for art, lines in al.items()}
    docs = {k: v for k, v in docs.items() if v}
    N = len(docs)
    post = defaultdict(list)
    for art, g in docs.items():
        for x in g:
            post[x].append(art)
    idf = {x: math.log((1 + N) / (1 + len(v))) for x, v in post.items()}
    wlen = {art: sum(len(x) ** 2 * idf[x] for x in g) for art, g in docs.items()}
    norm = {art: math.sqrt(sum(idf[x] ** 2 for x in g)) for art, g in docs.items()}
    run.mark('index')

    dev = sorted(art for art in docs if u.split[art] == D['query_split'] and sum(1 for x in docs[art] if len(x) == 1) and
                 sum(1 for li in al[art] for x in u.signs.lines[li] if x >= 0) >= 20)
    queries = random.Random(f'{SEED}:stageD:queries').sample(dev, D['n_queries'])
    K = D['pool_top_k']
    packet, key, desc = [], [], []
    for q in queries:
        F = docs[q]
        inter = defaultdict(int)
        winter = defaultdict(float)
        cos = defaultdict(float)
        for x in F:
            w = len(x) ** 2 * idf[x]
            i2 = idf[x] ** 2
            for c in post[x]:
                inter[c] += 1
                winter[c] += w
                cos[c] += i2
        excl = {c for c in inter if u.family[c] == u.family[q]}
        scores = {'ovl': {}, 'ovl_len_tfidf': {}, 'tfidf_cosine': {}}
        for c in inter:
            if c in excl:
                continue
            scores['ovl'][c] = inter[c] / min(len(F), len(docs[c]))
            scores['ovl_len_tfidf'][c] = winter[c] / min(wlen[q], wlen[c]) if min(wlen[q], wlen[c]) > 0 else 0.0
            scores['tfidf_cosine'][c] = cos[c] / (norm[q] * norm[c]) if norm[q] * norm[c] > 0 else 0.0
        tops = {m: sorted(s, key=lambda c: (-s[c], u.artifacts[c]))[:K] for m, s in scores.items()}
        pooled = sorted({c for t in tops.values() for c in t}, key=lambda c: h(f'{SEED}:packet:{u.artifacts[q]}:{u.artifacts[c]}'))
        near = u.near['partners'].get(q, set())
        for c in pooled:
            item = h(f'{SEED}:item:{u.artifacts[q]}:{u.artifacts[c]}')[:16]
            packet.append({'item': item, 'query_artifact': u.artifacts[q], 'query_catalog_key': u.catalog[u.artifacts[q]],
                           'candidate_artifact': u.artifacts[c], 'candidate_catalog_key': u.catalog[u.artifacts[c]],
                           'expert_label': None, 'expert_notes': None})
            key.append({'item': item, 'ranks': {m: (tops[m].index(c) + 1 if c in tops[m] else None) for m in tops},
                        'scores': {m: round(scores[m][c], 5) for m in scores}, 'uncertain_near_dup_partner': c in near})
        jac = lambda a_, b_: round(len(set(a_) & set(b_)) / len(set(a_) | set(b_)), 3)
        desc.append({'query_catalog_key': u.catalog[u.artifacts[q]], 'query_grams': len(F), 'excluded_same_family': len(excl), 'pooled': len(pooled),
                     'top1_score': {m: round(scores[m][tops[m][0]], 4) if tops[m] else None for m in tops},
                     'top10_overlap_jaccard': {'ovl~len_tfidf': jac(tops['ovl'], tops['ovl_len_tfidf']), 'ovl~cosine': jac(tops['ovl'], tops['tfidf_cosine']),
                                               'len_tfidf~cosine': jac(tops['ovl_len_tfidf'], tops['tfidf_cosine'])},
                     'top10_candidate_grams_median': {m: sorted(len(docs[c]) for c in tops[m])[len(tops[m]) // 2] if tops[m] else None for m in tops},
                     'uncertain_near_dup_partners_in_pool': sum(1 for c in pooled if c in near)})
    run.mark('queries')
    packet.sort(key=lambda r: r['item'])
    psha = run.write('packet_blinded.json', {'instructions': 'Blinded pooled candidates: for each item judge whether the candidate shares text with the query (parallel/duplicate/join/none/unsure). Method, rank and score are hidden. Candidates are unverified; similarity cannot certify a physical join.', 'items': packet})
    ksha = run.write('packet_key.json', key, raw=True)
    summary = {'documents': N, 'queries': len(queries), 'packet_items': len(packet), 'packet_sha256': psha, 'key_sha256': ksha, 'per_query': desc,
               'claims': 'none: no gold labels; descriptive only', 'deviation': 'artifacts not chapters; real dev queries not synthetic fragments; not a replication of 0.94'}
    run.write('summary.json', summary)
    rec = run.finish({'summary': {k: summary[k] for k in ('documents', 'queries', 'packet_items', 'packet_sha256', 'key_sha256')}})
    print(json.dumps(rec, indent=1, default=str))


if __name__ == '__main__':
    main()
