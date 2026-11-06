"""Focused checks for the cb-value-1 harness (toy data; no snapshot access)."""
import unittest
from array import array
from collections import Counter
from types import SimpleNamespace

from bench.core import Corpus
from bench.value_common import verdict, combine, paired_bootstrap
from bench.value_e1 import Stats, rank_of
from bench.value_e3 import seed_score, metrics
from bench import value_e2


def toy():
    c = Corpus('signs')
    c.labels = ['A', 'B', 'C', 'D', 'E']
    lines = [[0, 1, 2, 3, 4], [0, 1, 2, 3, 0], [4, 1, 3, 2, 0], [0, 1, 3, 3, 4]]
    c.lines = [array('i', t) for t in lines]
    c.line_art = array('i', [0, 1, 2, 3])
    return SimpleNamespace(signs=c)


class ValueTests(unittest.TestCase):
    def test_scan_counts_and_longest_match(self):
        u = toy()
        st = Stats(u, [0, 1, 2], array('i', [0, 0, 1, 0]), array('i', [-1] * 4))
        t = array('i', [0, 1, 2, 3, 4])           # target position 2 (C) in a copy of line 0
        res = st.scan(t, 2, 0, -1)
        self.assertEqual(res[2][0], 2)             # frame B ? D filled by C in lines 0 and 1
        self.assertEqual(res[2][3], 1)             # full 5-frame only in line 0
        self.assertEqual(Stats.best(res[2][4], 8), (4, 1))
        lists = st.cascade(t, 2, res)
        self.assertEqual(lists['cascade'][0], 2)
        self.assertEqual(st.lm(res, lists['cascade'], 2)[0], 2)
        self.assertEqual(res[2][5], 2)             # same-genre count

    def test_target_never_needed(self):
        u = toy()
        st = Stats(u, [0], array('i', [-1] * 4), array('i', [-1] * 4))
        t = array('i', [4, 1, 9, 3, 4])           # unseen target code 9 must simply be absent
        res = st.scan(t, 2, -1, -1)
        self.assertEqual(rank_of(st.cascade(t, 2, res)['cascade'], 9), 0)

    def test_verdict_rules(self):
        self.assertEqual(verdict(0.05, [0.01, 0.09], 0.03), 'supported')
        self.assertEqual(verdict(0.0, [-0.01, 0.02], 0.03), 'refuted')
        self.assertEqual(verdict(0.02, [-0.01, 0.05], 0.03), 'inconclusive')
        self.assertEqual(combine(['supported', 'refuted']), 'inconclusive')

    def test_paired_bootstrap_zero(self):
        rows = [{'a': i % 2, 'b': i % 2, 'c': i // 3} for i in range(30)]
        r = paired_bootstrap(rows, 'a', 'b', 'c', 50, 's')
        self.assertEqual(r['point'], 0.0)
        self.assertEqual(r['ci95'], [0.0, 0.0])

    def test_seed_and_metrics(self):
        self.assertEqual(seed_score([1, 2, 3, 4, 5], [9, 1, 2, 3, 4, 5], 3), 3)
        m = metrics([5, 7, 9], [7, 8])
        self.assertEqual(m['first_rank'], 2)
        self.assertEqual(m['recall10'], 0.5)

    def test_raw_tokenizer_matches_adapter_rule(self):
        toks = []
        for s in ['ABZ1', '[ABZ2]', 'X', 'ABZ532/ABZ533', 'ABZ331e+152i', 'ABZ15#?']:
            s2 = s.strip('[]?#')
            toks.append('GAP' if s2 in value_e2.GAPS else (s2 if value_e2.SIGN.fullmatch(s2) else 'UNS'))
        self.assertEqual(toks, ['ABZ1', 'ABZ2', 'GAP', 'UNS', 'UNS', 'ABZ15'])


if __name__ == '__main__':
    unittest.main()
