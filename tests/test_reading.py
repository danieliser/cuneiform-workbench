"""Q-025 reading aids: translation placement, glyph mapping and the exploration / reading endpoints.

Placement is checked against eBL's rule (a #tr line covers the text line before it, or through its extent) on
synthetic lines and against an independent raw-ATF scan of the retained eBL README record. Glyph lookups are
checked for verified, same-glyph, ambiguous and unmapped cases. Endpoint tests are hermetic (README sample and
synthetic fixtures). Run: bin/cuneiform-ui test
"""
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
sys.path.insert(0, str(ROOT / 'tools'))
from test_app import free_port  # noqa: E402
from server import reading_aids as RA  # noqa: E402
from server.translations import parse_extent, place  # noqa: E402

README = 'sample-ebl-readme'


def L(i, label, surface='obverse', column=None):
    return {'id': f'l{i}', 'ordinal': i, 'label': label, 'surface': surface, 'column_label': column, 'start_byte': i * 100, 'end_byte': i * 100 + 50}


class PlacementTest(unittest.TestCase):
    def setUp(self):
        self.lines = [L(0, '1'), L(1, '2'), L(2, '3'), L(3, '1', 'reverse'), L(4, '2', 'reverse')]

    def seg(self, after_line: int, prefix: str, sid='t'):
        start = self.lines[after_line]['end_byte'] + 1 if after_line >= 0 else 0
        text = f'{prefix} words'
        return {'id': sid, 'language': 'en', 'text': 'words', 'locator': f'atf:utf8:{start}:{start + len(text)}'}, start, text

    def atf_with(self, start, text):
        b = bytearray(b' ' * 600)
        b[start:start + len(text)] = text.encode()
        return bytes(b)

    def test_plain_translation_covers_line_before(self):
        s, start, text = self.seg(1, '#tr.en:')
        p, = place(self.lines, [s], self.atf_with(start, text))
        self.assertEqual((p['status'], p['first_line'], p['last_line'], p['lines']), ('line', 'l1', 'l1', 1))

    def test_extent_runs_through_named_line_on_other_surface(self):
        s, start, text = self.seg(1, '#tr.en.(r 1):')
        p, = place(self.lines, [s], self.atf_with(start, text))
        self.assertEqual((p['status'], p['first_line'], p['last_line'], p['lines']), ('extent', 'l1', 'l3', 3))

    def test_extent_not_in_edition_is_unresolved_not_guessed(self):
        s, start, text = self.seg(0, '#tr.en.(r 9):')
        p, = place(self.lines, [s], self.atf_with(start, text))
        self.assertEqual((p['status'], p['first_line'], p['last_line']), ('extent-unresolved', 'l0', 'l0'))
        self.assertEqual(p['extent'], 'r 9')

    def test_before_first_line_is_unplaced(self):
        s = {'id': 't', 'language': 'en', 'text': 'x', 'locator': 'atf:utf8:0:10'}
        p, = place(self.lines, [s], b'#tr.en: x ' + b' ' * 600)
        self.assertEqual((p['status'], p['first_line']), ('before-first-line', None))

    def test_parser_artifact_line_is_skipped(self):
        lines = self.lines[:2] + [dict(L(2, '#tr.en.(b.e'), start_byte=160, end_byte=190)] + self.lines[3:]
        s = {'id': 't', 'language': 'en', 'text': 'x', 'locator': 'atf:utf8:160:190'}
        p, = place(lines, [s], self.atf_with(160, '#tr.en.(r 1): words'))
        self.assertEqual((p['first_line'], p['last_line'], p['lines']), ('l1', 'l3', 2))

    def test_extent_grammar(self):
        self.assertEqual(parse_extent("r ii 3'"), {'surface': 'reverse', 'column': '2', 'number': "3'"})
        self.assertEqual(parse_extent('o 15'), {'surface': 'obverse', 'column': None, 'number': '15'})
        self.assertIsNone(parse_extent('1 2-6 x'))


class GlyphMapTest(unittest.TestCase):
    def test_reading_to_glyph_is_unique_or_absent(self):
        g = RA.token_glyph('ina', 'ina', 'atf')
        self.assertEqual(g, {'g': chr(0x12038), 'full': True})  # AŠ, ABZ 1
        self.assertEqual(RA.token_glyph('DIŠ', 'DIŠ', 'atf')['g'], chr(0x12079))
        parts = RA.glyph_parts('{d}UTU')
        self.assertTrue(parts[0]['determinative'])
        self.assertEqual(parts[0]['sign'], 'AN')

    def test_ambiguous_values_get_no_glyph(self):
        amb = next(v for v, names in RA.SIGN_MAP['values'].items() if len(names) > 1)
        p, = RA.glyph_parts(amb)
        self.assertEqual(p['status'], 'ambiguous')
        self.assertIsNone(p['glyph'])
        self.assertGreater(len(p['candidates']), 1)

    def test_sign_numbers_same_glyph_vs_ambiguous(self):
        self.assertEqual(RA.SIGN_MAP['abz']['ABZ480']['status'], 'same-glyph')
        self.assertEqual(RA.token_glyph('ABZ480', None, 'signs')['g'], chr(0x12079))
        self.assertEqual(RA.SIGN_MAP['abz']['ABZ597']['status'], 'ambiguous')  # GAR / LIMMU
        self.assertIsNone(RA.token_glyph('ABZ597', None, 'signs')['g'])
        self.assertIsNone(RA.token_glyph('LAGAB×HAL', None, 'signs'))

    def test_numbers_and_unknown_are_not_looked_up(self):
        self.assertEqual(RA.glyph_parts('30')[0]['status'], 'number')
        self.assertIsNone(RA.token_glyph('x', None, 'atf')['g'])
        self.assertEqual(RA.split_token('%sux'), [])

    def test_eBL_OSL_disagreement_is_never_drawn(self):
        differs = [n for n, s in RA.SIGN_MAP['signs'].items() if s['osl'] == 'differs']
        self.assertTrue(differs)  # e.g. |GA₂×ZIZ₂|: eBL and OSL give different code points
        for n in differs:
            self.assertIsNone(RA.sign_card(n)['glyph'], n)
            for v in RA.SIGN_MAP['signs'][n]['values']:
                for p in RA.glyph_parts(v):
                    if p.get('sign') == n:
                        self.assertIsNone(p['glyph'], (n, v))
        for label, e in RA.SIGN_MAP['abz'].items():
            if e['status'] in RA.GLYPH_OK:
                self.assertTrue(all(s['osl'] != 'differs' for s in e['signs']), label)


class ReadingEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch, research, home = make_roots('reading')
        cls.port = free_port()
        cls._env = {k: os.environ.get(k) for k in ('CUNEIFORM_UI_PORT', 'CUNEIFORM_RESEARCH_ROOT', 'CUNEIFORM_UI_HOME', 'CUNEIFORM_UI_SHOW_DEMOS')}
        os.environ.update({'CUNEIFORM_UI_PORT': str(cls.port), 'CUNEIFORM_RESEARCH_ROOT': str(research),
                           'CUNEIFORM_UI_HOME': str(home), 'CUNEIFORM_UI_SHOW_DEMOS': 'always'})
        from server import app as appmod
        cls.app = appmod.App(cls.port)
        appmod.Handler.app = cls.app
        cls.httpd = appmod.Server(('127.0.0.1', cls.port), appmod.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.has_readme = README in cls.app.backend.datasets and cls.app.backend.datasets[README].status == 'ready'

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

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        hdrs = {'Host': f'127.0.0.1:{self.port}'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs['Content-Type'] = 'application/json'
        c.request(method, path, body=data, headers=hdrs)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw, r.getheader('Content-Type')

    def get(self, path, **params):
        st, raw, _ = self.req('GET', path + '?' + urllib.parse.urlencode(params))
        return st, json.loads(raw)

    def need_readme(self):
        if not self.has_readme:
            self.skipTest('README sample not present in this environment')

    def test_font_is_served_from_allow_list(self):
        st, raw, ctype = self.req('GET', '/fonts/NotoSansCuneiform-Regular.otf')
        self.assertEqual((st, ctype), (200, 'font/otf'))
        self.assertGreater(len(raw), 100000)
        self.assertEqual(self.req('GET', '/fonts/../server/app.py')[0], 404)

    def test_tablet_translations_follow_raw_atf(self):
        self.need_readme()
        from check_translation_placement import raw_text_lines
        ds = self.app.backend.get(README)
        with ds.lock:
            cat = ds.db.execute('SELECT catalog_key, source_record_id FROM artifact').fetchone()
            atf = json.loads(ds.db.execute('SELECT raw_json FROM source_record WHERE id=?', [cat[1]]).fetchone()[0])['atf'].encode()
            n_tr = ds.db.execute('SELECT count(*) FROM translation_segment').fetchone()[0]
        st, r = self.req('POST', '/api/ask', {'question': 'tablet', 'dataset': README, 'params': {'catalog': cat[0]}})[:2]
        r = json.loads(r)
        self.assertEqual(st, 200, r)
        self.assertEqual(len(r['translations']), n_tr)
        raw = raw_text_lines(atf)
        with ds.lock:
            seg_start = {i: int(loc.split(':')[2]) for i, loc in ds.db.execute('SELECT id, locator FROM translation_segment').fetchall()}
            line_start = dict(ds.db.execute('SELECT id, start_byte FROM line').fetchall())
        placed = 0
        for t in r['translations']:
            self.assertIn(t['status'], ('line', 'extent', 'extent-unresolved', 'before-first-line'))
            if not t['first_line']:
                continue
            placed += 1
            expected = max(x for x in raw if x < seg_start[t['id']])  # independent: last raw text line before the #tr line
            self.assertEqual(line_start[t['first_line']], expected, t)
            if t['status'] != 'extent':
                self.assertEqual(t['first_line'], t['last_line'])
        self.assertEqual(placed, n_tr)
        self.assertTrue(all('glyph' in tk for ln in r['lines'] for tk in ln['tokens']))

    def test_explorations_registry_and_views(self):
        self.need_readme()
        st, reg = self.get('/api/questions', dataset=README)
        self.assertEqual(st, 200, reg)
        ids = {e['id'] for e in reg['explorations']}
        self.assertIn('translated', ids)
        tr = next(e for e in reg['explorations'] if e['id'] == 'translated')
        self.assertEqual(tr['texts'], 1)
        st, raw, _ = self.req('POST', '/api/explore', {'exploration': 'translated', 'dataset': README, 'params': {'view': 'passages'}})
        r = json.loads(raw)
        self.assertEqual(st, 200, r)
        self.assertEqual(len(r['passages']), 1)
        for b in r['passages'][0]['blocks']:
            self.assertGreaterEqual(len(b['lines']), 1)
            self.assertEqual(b['translation']['lines'], len(b['lines']))
        st, raw, _ = self.req('POST', '/api/explore', {'exploration': 'translated', 'dataset': README, 'params': {'view': 'texts'}})
        self.assertEqual(json.loads(raw)['texts'][0]['translations'].get('en'), 9)

    def test_explore_rejects_bad_input(self):
        for p in ({'view': 'everything'}, {'subgenre': 'CANONICAL > Magic'}, {'language': 'English!'}):
            st, raw, _ = self.req('POST', '/api/explore', {'exploration': 'medicine', 'dataset': 'synthetic-fixtures', 'params': p})
            self.assertEqual(st, 400, (p, raw))
        st, raw, _ = self.req('POST', '/api/explore', {'exploration': 'nope', 'dataset': 'synthetic-fixtures'})
        self.assertEqual(st, 400)

    def test_reading_with_cold_genre_cache_does_not_nest_runs(self):
        self.need_readme()
        self.app.questions.explorations.genres._cache.clear()
        st, r = self.get('/api/reading', dataset=README, text='DIŠ', field='atf')
        self.assertEqual(st, 200, r)
        self.assertGreater(r['corpus']['positions'], 0)

    def test_reading_explanation_is_cited_and_not_a_translation(self):
        self.need_readme()
        st, r = self.get('/api/reading', dataset=README, text='DIŠ', field='atf')
        self.assertEqual(st, 200, r)
        self.assertEqual(r['parts'][0]['sign']['codepoints'], ['U+12079'])
        self.assertTrue(r['note'] and len(r['note']['uses']) >= 3)
        for u in r['note']['uses']:
            self.assertIn(u['cite'], r['sources'])
        self.assertIn('logogram', r['kinds'])
        self.assertFalse(r['lemmas']['available'])  # no ORACC snapshot in the hermetic root: stated, not faked
        st, r = self.get('/api/reading', dataset=README, text='ABZ597', field='signs')
        self.assertEqual(r['abz']['status'], 'ambiguous')
        self.assertGreater(len(r['abz']['signs']), 1)
        st, r = self.get('/api/reading', dataset=README, text='ina', field='atf')
        self.assertIsNone(r['note'])  # no curated note: nothing invented
        self.assertEqual(self.get('/api/reading', dataset=README, text='', field='atf')[0], 400)


if __name__ == '__main__':
    unittest.main()
