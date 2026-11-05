"""Question endpoints: every aggregate is checked against an independent count.

The server answers from the backend's n-gram/posting projection. These tests recompute the same answers
from the canonical occurrence table (positions per line, in Python), so a projection, paging or grouping
bug shows up as a mismatch. Hermetic: synthetic fixtures and retained samples only.
Run: bin/cuneiform-ui test
"""
import collections
import http.client
import json
import os
import sys
import threading
import unittest
import urllib.parse

sys.dont_write_bytecode = True
from support import ROOT, make_roots, rm  # noqa: E402

sys.path.insert(0, str(ROOT))
from test_app import free_port  # noqa: E402

KEY = 'synthetic-fixtures'


class QuestionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, research, home = make_roots('questions')
        cls.port = free_port()
        cls._env = {k: os.environ.get(k) for k in ('CUNEIFORM_UI_PORT', 'CUNEIFORM_RESEARCH_ROOT', 'CUNEIFORM_UI_HOME', 'CUNEIFORM_UI_SHOW_DEMOS')}
        os.environ.update({'CUNEIFORM_UI_PORT': str(cls.port), 'CUNEIFORM_RESEARCH_ROOT': str(research),
                           'CUNEIFORM_UI_HOME': str(home), 'CUNEIFORM_UI_SHOW_DEMOS': 'always'})
        from server import app as appmod
        cls.app = appmod.App(cls.port)
        appmod.Handler.app = cls.app
        cls.httpd = appmod.Server(('127.0.0.1', cls.port), appmod.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.lines = cls.canonical_lines()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.backend.close()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rm(cls.scratch)

    # -- independent reference: canonical positions, line by line ------------------------------
    @classmethod
    def canonical_lines(cls) -> dict:
        ds = cls.app.backend.get(KEY)
        scope = next(s for s in ds.scopes if s['field'] == 'atf')
        with ds.lock:
            rows = ds.db.execute(
                'SELECT t.line_id, t.position, t.reading, t.raw, t.kind, t.artifact_id FROM token_context t '
                'JOIN source_record r ON r.id=t.source_record_id JOIN source_binding b ON b.source_record_id=r.id '
                "WHERE t.field='atf' AND r.namespace=? AND b.snapshot_id=? ORDER BY t.line_id, t.position",
                [scope['namespace'], scope['snapshot_id']]).fetchall()
        lines = collections.OrderedDict()
        for line_id, pos, reading, raw, kind, art in rows:
            lines.setdefault(line_id, []).append({'reading': reading, 'raw': raw, 'kind': kind, 'artifact': art})
        assert lines, 'synthetic fixture has atf lines'
        return lines

    def windows(self, pattern):
        """Every line-local window matching pattern: str = reading, None = any position."""
        out = []
        for line_id, ps in self.lines.items():
            for i in range(len(ps) - len(pattern) + 1):
                w = ps[i:i + len(pattern)]
                if all(p is None or w[j]['reading'] == p for j, p in enumerate(pattern)):
                    out.append((line_id, w))
        return out

    @staticmethod
    def key(p):
        return ('r', p['reading']) if p['reading'] is not None else ('u', p['raw'], p['kind'])

    # -- HTTP helpers ------------------------------------------------------------------------
    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        h = {'Host': f'127.0.0.1:{self.port}'}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data), dict(r.getheaders())
        except ValueError:
            return r.status, data, dict(r.getheaders())

    def version(self):
        _, st, _ = self.req('GET', '/api/status')
        return {d['key']: d['version'] for d in st['datasets']}[KEY]

    def ask(self, question, params, version=None):
        st, r, _ = self.req('POST', '/api/ask', {'question': question, 'dataset': KEY, 'version': version or self.version(), 'params': params})
        return st, r

    def ok(self, question, params):
        st, r = self.ask(question, params)
        self.assertEqual(st, 200, r)
        self.assertEqual(r['dataset']['version'], self.version(), 'every answer is bound to the dataset version')
        self.assertIn('end_to_end_ms', r['timing'])
        return r

    # -- registry ------------------------------------------------------------------------------
    def test_registry_is_declarative_and_bound(self):
        st, r, _ = self.req('GET', '/api/questions')
        self.assertEqual(st, 200)
        self.assertEqual([q['id'] for q in r['questions']], ['sequence', 'between', 'usage', 'tablet'])
        for q in r['questions']:
            self.assertTrue(q['title'].endswith('?'))
            self.assertTrue(q['fields'])
            self.assertTrue(all({'name', 'label', 'kind'} <= set(f) for f in q['fields']))
        st, r, _ = self.req('GET', f'/api/questions?dataset={KEY}&version={self.version()}')
        self.assertEqual(st, 200, r)
        self.assertEqual(r['dataset']['key'], KEY)
        for ex in r['examples']['sequence']:  # seeded examples are real counts from this dataset
            got = self.ok('sequence', ex['params'])
            self.assertEqual(got['totals']['matches'], ex['n'])

    # -- 1. sequence -----------------------------------------------------------------------------
    def test_sequence_totals_match_canonical_and_motif_query(self):
        for tokens in (['A', 'B', 'C'], ['A', '?', 'C'], ['A'], ['A', 'C'], ['B', '?']):
            pattern = [None if t == '?' else t for t in tokens]
            if len(tokens) == 1:
                ref = [(lid, [p]) for lid, ps in self.lines.items() for p in ps if p['reading'] == tokens[0]]
            else:
                ref = self.windows(pattern)
            r = self.ok('sequence', {'tokens': ' '.join(tokens), 'limit': 50})
            t = r['totals']
            self.assertEqual(t['matches'], len(ref), tokens)
            self.assertEqual(t['tablets'], len({w[0]['artifact'] for _, w in ref}), tokens)
            self.assertEqual(t['lines'], len({lid for lid, _ in ref}), tokens)
            self.assertEqual(sum(p['matches'] for p in r['by_period']), t['matches'], 'periods partition all matches')
            if len(tokens) > 1:  # same count as the existing motif query (backend lookup)
                _, q, _ = self.req('POST', '/api/query', {'dataset': KEY, 'pattern': tokens})
                self.assertEqual(q['count'], t['matches'])
            # paging covers every match exactly once, and each page row's match sits inside its own line
            seen, offset = [], 0
            while True:
                page = self.ok('sequence', {'tokens': ' '.join(tokens), 'limit': 2, 'offset': offset})['page']
                for row in page['rows']:
                    ids = {x['id'] for x in row['tokens']}
                    self.assertTrue(set(row['occurrence_ids']) <= ids, 'match is line-local')
                    seen.append(tuple(row['occurrence_ids']))
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
            self.assertEqual(len(seen), len(ref), tokens)
            self.assertEqual(len(set(seen)), len(seen), 'no row repeats across pages')

    def test_missing_and_unparsed_tokens_are_reported(self):
        r = self.ok('sequence', {'tokens': 'zzqq-not-here'})
        self.assertEqual(r['resolved'][0]['as'], 'missing')
        self.assertEqual(r['totals']['matches'], 0)
        unparsed = collections.Counter(p['raw'] for ps in self.lines.values() for p in ps
                                       if p['reading'] is None and not any(q['reading'] == p['raw'] for qs in self.lines.values() for q in qs))
        if not unparsed:
            self.skipTest('fixture has no unparsed positions')
        raw, n = unparsed.most_common(1)[0]
        r = self.ok('sequence', {'tokens': raw})
        self.assertEqual(r['resolved'][0]['as'], 'unparsed')
        self.assertEqual(r['totals']['matches'], n)

    # -- 2. between --------------------------------------------------------------------------------
    def test_between_groups_every_filler(self):
        for before, gap, after in (('A', 1, 'C'), ('A', 2, 'C'), ('A', 1, 'B')):
            ref = self.windows([before] + [None] * gap + [after])
            groups = collections.Counter(tuple(self.key(p) for p in w[1:1 + gap]) for _, w in ref)
            tablets = collections.defaultdict(set)
            for _, w in ref:
                tablets[tuple(self.key(p) for p in w[1:1 + gap])].add(w[0]['artifact'])
            r = self.ok('between', {'before': before, 'gap': str(gap), 'after': after})
            self.assertEqual(r['totals']['matches'], len(ref))
            self.assertEqual(r['distinct_fillers'], len(groups))
            self.assertEqual(sum(x['matches'] for x in r['rows']), len(ref), 'groups cover all matches (fixture is small)')
            for row in r['rows']:
                k = tuple(('r', p['text']) if p['parsed'] else ('u', p['text'], p['kind']) for p in row['filler'])
                self.assertEqual(row['matches'], groups[k], k)
                self.assertEqual(row['tablets'], len(tablets[k]), k)
                drill = self.ok('sequence', {'slots': row['slots']})  # drill-down shows exactly this group's contexts
                self.assertEqual(drill['totals']['matches'], row['matches'], row['slots'])
            blocked = sum(n for k, n in groups.items() if any(x[0] == 'u' for x in k))
            self.assertEqual(r['unparsed_or_broken']['matches'], blocked)

    def test_between_bounds(self):
        for params in ({'before': 'A B C', 'after': 'D'}, {'before': 'A B', 'gap': '2', 'after': 'C D'}, {'before': 'A', 'gap': '3', 'after': 'C'},
                       {'before': '?', 'after': 'C'}):
            st, r = self.ask('between', params)
            self.assertEqual(st, 400, (params, r))

    # -- 3. usage -------------------------------------------------------------------------------
    def test_usage_neighbors_and_line_edges(self):
        token = 'A'
        occ = [(lid, i) for lid, ps in self.lines.items() for i, p in enumerate(ps) if p['reading'] == token]
        before = collections.Counter(self.key(self.lines[lid][i - 1]) for lid, i in occ if i > 0)
        after = collections.Counter(self.key(self.lines[lid][i + 1]) for lid, i in occ if i + 1 < len(self.lines[lid]))
        r = self.ok('usage', {'token': token})
        self.assertEqual(r['totals']['matches'], len(occ))
        for side, ref in (('before', before), ('after', after)):
            s = r[side]
            self.assertEqual(s['windows'], sum(ref.values()))
            self.assertEqual(s['windows'] + s['line_edge'], len(occ), 'every occurrence has a neighbor or sits at the line edge')
            self.assertEqual(s['distinct'], len(ref))
            for row in s['rows']:
                n = row['neighbor']
                k = ('r', n['text']) if n['parsed'] else ('u', n['text'], n['kind'])
                self.assertEqual(row['matches'], ref[k], (side, k))

    # -- 4. tablet and helpers ---------------------------------------------------------------------
    def test_tablet_passage_and_suggestions(self):
        ds = self.app.backend.get(KEY)
        with ds.lock:
            cat, n_lines = ds.db.execute("SELECT a.catalog_key, count(l.id) FROM artifact a JOIN witness w ON w.artifact_id=a.id JOIN sequence_version s ON s.witness_id=w.id "
                                         "JOIN line l ON l.sequence_id=s.id WHERE s.field='atf' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1").fetchone()
        r = self.ok('tablet', {'catalog': cat})
        self.assertTrue(r['found'])
        self.assertEqual(len(r['lines']), n_lines)
        self.assertIn('atf', r['layers'])
        lid = r['lines'][0]['id']
        st, p, _ = self.req('GET', f'/api/passage?dataset={KEY}&version={self.version()}&line={lid}&around=1')
        self.assertEqual(st, 200, p)
        self.assertIn(lid, [x['id'] for x in p['lines']])
        self.assertEqual(p['tablet']['catalog'], cat)
        miss = self.ok('tablet', {'catalog': cat[:1] + '-no-such'})
        self.assertFalse(miss['found'])
        st, s, _ = self.req('GET', f'/api/suggest?dataset={KEY}&version={self.version()}&field=atf&prefix=A')
        self.assertEqual(st, 200, s)
        self.assertIn('A', [x['value'] for x in s['readings']])
        st, t, _ = self.req('GET', f'/api/tablets?dataset={KEY}&version={self.version()}&prefix={urllib.parse.quote(cat[:2])}')
        self.assertEqual(st, 200, t)
        self.assertIn(cat, t['tablets'])

    # -- guards -------------------------------------------------------------------------------------
    def test_stale_version_refused_everywhere(self):
        bad = 'f' * 32
        st, r = self.ask('sequence', {'tokens': 'A B'}, version=bad)
        self.assertEqual((st, r.get('code')), (409, 'dataset-version-changed'))
        for path in (f'/api/questions?dataset={KEY}&version={bad}', f'/api/suggest?dataset={KEY}&version={bad}&prefix=A',
                     f'/api/tablets?dataset={KEY}&version={bad}&prefix=A', f'/api/passage?dataset={KEY}&version={bad}&line=line:{"0" * 32}'):
            st, r, _ = self.req('GET', path)
            self.assertEqual(st, 409, path)

    def test_bad_inputs_are_400_and_sql_is_data(self):
        cases = [('nope', {'tokens': 'A'}), ('sequence', {'tokens': 'A B C D E F'}), ('sequence', {'tokens': '?'}),
                 ('sequence', {'tokens': 'A', 'field': 'x'}), ('sequence', {'slots': [{'reading': 'A', 'raw': 'B'}]}),
                 ('sequence', {'slots': [{'any': True}]}), ('sequence', {'tokens': 'A', 'period': ['x']}), ('usage', {'token': 'A B'}),
                 ('usage', {'token': '?'}), ('tablet', {'catalog': ''}), ('sequence', {'tokens': 'A', 'limit': 999})]
        for q, p in cases:
            st, r = self.ask(q, p)
            self.assertEqual(st, 400, (q, p, r))
        r = self.ok('sequence', {'tokens': "A' OR 1=1 --"})  # quotes are data: no match, no error
        self.assertEqual(r['totals']['matches'], 0)
        st, r, _ = self.req('GET', f'/api/passage?dataset={KEY}&version={self.version()}&line=../../etc')
        self.assertEqual(st, 400)

    def test_static_questions_script_served_with_csp(self):
        st, body, hdr = self.req('GET', '/questions.js')
        self.assertEqual(st, 200)
        self.assertIn(b'/api/ask', body)
        self.assertNotIn(b'innerHTML', body)
        self.assertIn("script-src 'self'", hdr['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
