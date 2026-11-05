"""Focused tests: bounds, independent counts, split disjointness, masked target hidden, ties, gaps preserved."""
import inspect
import json
import random
import unittest
from array import array
from collections import Counter
from pathlib import Path

import duckdb

from bench import core, stage_a, stage_b, stage_c
from bench.core import GAP, UNS, BOS, EOS, Corpus


def toy(lines_labels, arts=None):
    c = Corpus('signs')
    for li, labels in enumerate(lines_labels):
        t = array('i')
        for x in labels:
            if x == 'X':
                t.append(GAP)
            elif x == 'U':
                t.append(UNS)
            else:
                if x not in c.code:
                    c.code[x] = len(c.labels)
                    c.labels.append(x)
                t.append(c.code[x])
        c.lines.append(t)
        c.line_art.append(arts[li] if arts else li)
        c.line_ids.append(f'line:{li}')
        c.line_labels.append(str(li + 1))
    n = max(c.line_art) + 1
    c.artifacts = [f'artifact:{i}' for i in range(n)]
    c.witness = [f'witness:{i}' for i in range(n)]
    return c


class OracleHand(unittest.TestCase):
    def test_counts_wildcards_gaps_lines(self):
        c = toy([['A', 'B', 'C', 'A', 'B', 'C'], ['A', 'X', 'C'], ['A', 'A', 'A', 'A'], ['A', 'B'], ['C', 'D']])
        self.assertEqual(len(stage_a.oracle(c, ['A', 'B', 'C'])), 2)
        self.assertEqual(len(stage_a.oracle(c, ['A', 'C'])), 0)              # exact never jumps a gap
        self.assertEqual(len(stage_a.oracle(c, ['A', '?', 'C'])), 3)         # '?' covers the gap
        self.assertEqual(len(stage_a.oracle(c, ['A', 'A'])), 3)              # overlapping
        self.assertEqual(len(stage_a.oracle(c, ['B', 'C'])), 2)              # no cross-line B|C D
        self.assertEqual(len(stage_a.oracle(c, ['B', 'C', 'D'])), 0)
        fill = Counter(c.label(m[3]) for m in stage_a.oracle(c, ['A', '?', 'C']))
        self.assertEqual(fill, Counter({'B': 2, 'GAP': 1}))
        self.assertEqual(len(stage_a.oracle(c, ['A', 'B', 'C'], (0, 1, 2))), 2)
        g = toy([['A', 'B', 'X', 'C'], ['A', 'B', 'D', 'U', 'C'], ['A', 'B', 'D', 'U', 'E', 'C'], ['A', 'B', 'C']])
        self.assertEqual(len(stage_a.oracle(g, ['A', 'B', 'C'], (0, 1, 2))), 3)        # skips 1, 2, 0; skip 3 excluded

    def test_unknown_label_never_matches(self):
        c = toy([['A', 'B']])
        self.assertEqual(stage_a.oracle(c, ['A', 'Z']), [])


def synthetic_db():
    """In-memory DB built by the pinned backend's own adapter + projection builder (independent of our oracle)."""
    storage, projections, query = core.backend()
    from cuneiform.adapters import Builder, ingest
    from cuneiform.model import identity, digest
    rnd = random.Random(7)
    vocab = ['ABZ1', 'ABZ2', 'ABZ3', 'ABZ4', 'ABZ5']
    recs = []
    for r in range(25):
        lines = []
        for _ in range(rnd.randint(1, 4)):
            toks = [rnd.choice(vocab + ['X', 'ABZ1/ABZ2']) if rnd.random() < 0.2 else rnd.choice(vocab) for _ in range(rnd.randint(2, 9))]
            lines.append(' '.join(toks))
        rec = {'_id': f'T.{r}', 'atf': '\n'.join(f'{i + 1}. a-na {"x" if i % 2 else "ša"} be-lí' for i in range(len(lines))), 'signs': '\n'.join(lines),
               'externalNumbers': {'cdliNumber': 'P000001' if r in (3, 4) else f'P10{r:04d}'}}
        recs.append(rec)
    recs.append(dict(recs[5], _id='T.dup'))      # exact duplicate sequence under another key
    raw = json.dumps(recs).encode()
    db = duckdb.connect(':memory:')
    db.execute((core.PIN / 'cuneiform' / 'schema.sql').read_text())
    sid = identity('demo', 'ebl', digest(raw))
    ingest(Builder(db, sid, 'ebl'), 'ebl', raw)
    projections.build(db, projections.ProjectionConfig(fields=('atf', 'signs'), policies=('source',), lengths=(2, 3, 4, 5)))
    ns = db.execute('SELECT namespace FROM source_record LIMIT 1').fetchone()[0]
    return db, {'namespace': ns, 'snapshot_id': sid}, projections, query


class BackendParitySynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.man, cls.proj, cls.query = synthetic_db()
        cls.u = core.load_universe(cls.db, with_atf=True, with_near=True)

    def test_oracle_equals_index_sql_graph(self):
        u = self.u
        labels = u.signs.labels
        pats = [[a, b] for a in labels for b in labels] + [[a, '?', c] for a in labels for c in labels] + [[a, b, c] for a in labels[:3] for b in labels[:3] for c in labels[:3]]
        checked = 0
        for p in pats:
            kind = 'frame' if p[1] == '?' else 'exact'
            q = {'pattern': p, 'field': 'signs', 'kind': kind}
            om = stage_a.oracle(u.signs, p)
            orc = stage_a.summarize_oracle(u, u.signs, om)
            self.assertEqual(stage_a.index_call(self.proj, self.db, self.man, q), orc['count'], p)
            sg = stage_a.sql_grouped(self.db, self.man, q, u)
            for k in ('spans', 'artifacts', 'witnesses', 'families', 'fillers'):
                self.assertEqual(sg[k], orc[k], (p, k))
            g = self.query.motif(self.db, p, field='signs')
            self.assertEqual(g['occurrences'], orc['count'], p)
            checked += 1
        for p in [[a, b, c] for a in labels[:2] for b in labels[:2] for c in labels[:3]]:
            q = {'pattern': p, 'field': 'signs', 'kind': 'bounded_gap', 'max_skip': 2}
            om = stage_a.oracle(u.signs, p, (0, 1, 2))
            self.assertEqual(stage_a.index_call(self.proj, self.db, self.man, q), len(om), p)
            self.assertEqual(self.query.motif(self.db, p, field='signs', max_skip=2)['occurrences'], len(om), p)
        self.assertGreater(checked, 50)

    def test_families_alias_and_exact_duplicate(self):
        u = self.u
        cat = {u.catalog[a]: i for i, a in enumerate(u.artifacts)}
        self.assertEqual(u.family[cat['T.3']], u.family[cat['T.4']])           # shared CDLI claim
        self.assertEqual(u.family[cat['T.5']], u.family[cat['T.dup']])         # exact duplicate signs
        self.assertNotEqual(u.family[cat['T.1']], u.family[cat['T.2']])

    def test_split_disjoint_by_family_and_deterministic(self):
        u = self.u
        fam_split = {}
        for f, s in zip(u.family, u.split):
            self.assertEqual(fam_split.setdefault(f, s), s)
        self.assertEqual([core.split_of(f) for f in u.family], u.split)
        a = core.split_manifest(u)['sha256']
        self.assertEqual(a, core.split_manifest(u)['sha256'])


class StageBTests(unittest.TestCase):
    def test_windows_break_at_gaps_and_lines(self):
        c = toy([['A', 'B', 'C', 'X', 'A', 'B', 'C', 'D'], ['C', 'D', 'A']])
        u = type('U', (), {'signs': c})()
        w = [(li, i) for li, i, *_ in stage_b.windows(c, [0, 1])]
        self.assertEqual(w, [(0, 0), (0, 4), (0, 5), (1, 0)])

    def test_expectation_and_zero_handling(self):
        c = toy([['A', 'B', 'C'], ['A', 'B', 'D'], ['E', 'B', 'C']])
        p, _, pairs = stage_b.transitions(c, [0, 1, 2])
        A_, B_, C_ = c.code['A'], c.code['B'], c.code['C']
        self.assertAlmostEqual(p[B_][C_], 2 / 3)
        self.assertEqual(stage_b.d_eff(0, 0), 0.0)
        self.assertAlmostEqual(stage_b.d_eff(1.5, 0.5), 1.0)
        tri, ctx_ab, ctx_a, frame = stage_b.count_types(type('U', (), {'signs': c})(), [0, 1, 2])
        self.assertEqual(ctx_ab[(A_, B_)], 2)
        self.assertAlmostEqual(ctx_ab[(A_, B_)] * p[B_][C_], 4 / 3)   # E_ABC
        self.assertAlmostEqual(stage_b.p2(p, A_, C_), 2 / 3)

    def test_discovery_drops_test_lines(self):
        src = inspect.getsource(stage_b.discover)
        self.assertIn('u.signs.lines[li] = None', src)
        self.assertLess(src.index('u.signs.lines[li] = None'), src.index('transitions('))


class StageCTests(unittest.TestCase):
    def setUp(self):
        self.c = toy([['A', 'B', 'C', 'D'], ['A', 'B', 'C'], ['A', 'E', 'C'], ['X', 'B', 'E', 'C', 'B'], ['Q', 'Q', 'Q']])
        self.m = stage_c.Model(self.c, [0, 1, 2, 3])

    def test_target_hidden(self):
        self.assertEqual(list(inspect.signature(stage_c.Model.rank_lists).parameters), ['self', 'ctx'])
        t = self.c.lines[4]
        self.assertEqual(self.m.uni[self.c.code['Q']], 0)          # test-only sign is OOV: test line never trained
        ctx = stage_c.context(t, 1)
        before = self.m.rank_lists(ctx)
        t2 = array('i', t)
        t2[1] = self.c.code['A']                                     # change the target only
        self.assertEqual(stage_c.context(t2, 1), ctx)
        self.assertEqual(self.m.rank_lists(stage_c.context(t2, 1)), before)

    def test_ties_deterministic_and_frames(self):
        code = self.c.code
        lists = self.m.rank_lists((BOS, code['A'], code['C'], EOS))
        # frame1 A ? C: B x2, E x1
        self.assertEqual([self.c.label(x) for x in lists['frame1']], ['B', 'E'])
        # unigram ties: counts B4? -> sorted by count desc then label
        u = self.m.uni
        self.assertEqual(self.m.uni_rank, sorted(u, key=lambda x: (-u[x], self.c.labels[x])))
        self.assertEqual(lists['cascade'][:2], lists['frame1'][:2])

    def test_gap_preserved_in_context(self):
        t = self.c.lines[3]
        self.assertEqual(stage_c.context(t, 2), (GAP, self.c.code['B'], self.c.code['C'], self.c.code['B']))
        self.assertIn((GAP, self.c.code['B'], self.c.code['C'], self.c.code['B']), self.m.f2)

    def test_poisson_mean(self):
        rnd = random.Random(1)
        xs = [stage_c.poisson1(rnd) for _ in range(20000)]
        self.assertAlmostEqual(sum(xs) / len(xs), 1.0, delta=0.03)


class Bounds(unittest.TestCase):
    def test_rss_guard(self):
        old = core.CONFIG['resources']['rss_soft_abort_bytes']
        core.CONFIG['resources']['rss_soft_abort_bytes'] = 1
        try:
            with self.assertRaises(MemoryError):
                core.check_rss('t')
        finally:
            core.CONFIG['resources']['rss_soft_abort_bytes'] = old

    def test_connect_read_only_and_limits(self):
        if not core.SNAPSHOT.exists():
            self.skipTest('snapshot absent')
        db = core.connect()
        try:
            self.assertEqual(db.execute("SELECT current_setting('access_mode')").fetchone()[0], 'read_only')
            self.assertEqual(int(db.execute("SELECT current_setting('threads')").fetchone()[0]), 2)
            with self.assertRaises(Exception):
                db.execute('CREATE TABLE x(a INT)')
        finally:
            db.close()

    def test_backend_is_pinned(self):
        storage, _, _ = core.backend()
        self.assertTrue(Path(storage.__file__).resolve().is_relative_to(core.PIN.resolve()))


if __name__ == '__main__':
    unittest.main()
