"""Smoke + boundary tests against the real backend (in-process server on an ephemeral loopback port).

Run: bin/cuneiform-ui test
"""
import http.client
import json
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        os.environ['CUNEIFORM_UI_PORT'] = str(cls.port)
        from server import app as appmod
        cls.appmod = appmod
        cls.app = appmod.App(cls.port)
        appmod.Handler.app = cls.app
        cls.httpd = appmod.Server(('127.0.0.1', cls.port), appmod.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.backend.close()

    def req(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        h = {'Host': f'127.0.0.1:{self.port}'}
        if body is not None:
            raw = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            payload = json.loads(data)
        except ValueError:
            payload = data
        return r.status, payload, dict(r.getheaders())

    # -- smoke ------------------------------------------------------------------
    def test_boot_and_datasets(self):
        st, body, hdr = self.req('GET', '/api/status')
        self.assertEqual(st, 200)
        self.assertNotEqual(body['backend']['compatibility'], 'incompatible')
        keys = {d['key']: d for d in body['datasets']}
        self.assertEqual(keys['synthetic-fixtures']['status'], 'ready')
        self.assertIn('Content-Security-Policy', hdr)
        st, html, hdr = self.req('GET', '/')
        self.assertEqual(st, 200)
        self.assertNotIn(b'<script>', html)  # no inline script; CSP script-src 'self'
        self.assertIn("script-src 'self'", hdr['Content-Security-Policy'])

    def test_synthetic_expected_counts(self):
        cases = [(['A', 'B', 'C'], 'source', 6), (['A', '?', 'C'], 'source', 11), (['A', 'C'], 'source', 0),
                 (['A', 'B', 'C'], 'strict', 0), (['A'], 'source', 12)]
        for pattern, policy, expect in cases:
            st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': pattern, 'policy': policy})
            self.assertEqual(st, 200, r)
            self.assertEqual(r['count'], expect, pattern)
            if len(pattern) > 1:
                self.assertTrue(r['parity']['ok'])

    def test_occurrence_trace_roundtrip_and_graph(self):
        _, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A', 'B', 'C']})
        oid = r['rows'][0]['occurrence_ids'][1]
        st, d, _ = self.req('GET', f'/api/occurrence?dataset=synthetic-fixtures&id={oid}')
        self.assertEqual(st, 200, d)
        self.assertIsNone(d['trace_error'])
        self.assertEqual(d['trace']['resolved_raw'], d['occurrence']['raw'])
        self.assertEqual(d['excerpt']['token'], d['occurrence']['raw'])
        st, g, _ = self.req('GET', f'/api/neighborhood?dataset=synthetic-fixtures&id={d["occurrence"]["line_id"]}&per=2')
        self.assertEqual(st, 200)
        self.assertTrue(any(n['kind'] == 'Aggregate' for n in g['nodes']), 'large fan-out must be aggregated')
        self.assertLessEqual(len([n for n in g['nodes'] if n['kind'] != 'Aggregate']), 1 + 2 * 6)

    def test_missing_layer_is_not_zero(self):
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'sample-ebl-k1', 'pattern': ['A', 'B']})
        if st == 400 and 'unknown dataset' in r.get('error', ''):
            self.skipTest('retained K.1 sample not present on this machine')
        self.assertEqual(st, 200)
        self.assertIn('unavailable', r['missing'])

    # -- boundaries ---------------------------------------------------------------
    def test_host_origin_and_method_guards(self):
        self.assertEqual(self.req('GET', '/api/status', headers={'Host': 'evil.example:80'})[0], 421)
        self.assertEqual(self.req('GET', '/api/status', headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.req('GET', '/api/status', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.req('POST', '/api/query', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
        big = b'{"x":"' + b'a' * 70000 + b'"}'
        self.assertEqual(self.req('POST', '/api/query', raw=big, headers={'Content-Type': 'application/json'})[0], 413)
        self.assertEqual(self.req('DELETE', '/api/status')[0], 405)

    def test_path_traversal_and_bad_ids(self):
        for path in ['/../server/app.py', '/%2e%2e/server/app.py', '/app.js/../../README.md', '/web/app.js', '/.git/config']:
            self.assertEqual(self.req('GET', path)[0], 404, path)
        st, r, _ = self.req('GET', '/api/report?dataset=../../etc/passwd')
        self.assertEqual(st, 400)
        st, r, _ = self.req('GET', "/api/node?dataset=synthetic-fixtures&id=artifact:x%27%20OR%201=1")
        self.assertEqual(st, 400)

    def test_injection_inputs_are_data(self):
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ["A'", 'OR', "1=1;--"]})
        self.assertEqual(st, 200)
        self.assertEqual(r['count'], 0)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['<img', 'src=x', 'onerror=alert(1)>']})
        self.assertEqual(st, 200)
        self.assertEqual(r['count'], 0)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A B']})
        self.assertEqual(st, 400)  # whitespace inside a token is refused
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A'] * 6})
        self.assertEqual(st, 400)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A', 'B'], 'limit': 5000})
        self.assertEqual(st, 400)
        st, r, _ = self.req('GET', '/api/search?dataset=synthetic-fixtures&q=%25_%5C')
        self.assertEqual(st, 200)  # LIKE wildcards escaped, no error

    def test_falkordb_projection_boundaries(self):
        from server.falkor import Resp, cy
        fk = self.app.falkor
        if not fk.status()['reachable']:
            self.skipTest('isolated FalkorDB not running')
        evil = ['a"b', 'c\\"d', "x'y", 'nl\nx', '") MATCH (n) DETACH DELETE n //', 'KA×A {d}EN.LIL₂']
        with Resp(fk.host, fk.port) as r:
            res = r.cmd('GRAPH.QUERY', 'cuneiform_test_escape', 'CYPHER xs=' + cy(evil) + ' UNWIND $xs AS x RETURN x')
            r.cmd('GRAPH.DELETE', 'cuneiform_test_escape')
        self.assertEqual([row[0] for row in res[1]], evil)
        st, out, _ = self.req('POST', '/api/falkordb/load', {'dataset': 'synthetic-fixtures'})
        self.assertEqual(st, 200, out)
        self.assertEqual(out['counts'], out['receipt']['counts'])
        st, q, _ = self.req('POST', '/api/falkordb/query', {'graph': out['graph'], 'query': 'MATCH (n) DETACH DELETE n'})
        self.assertEqual(st, 400)
        self.assertIn('read-only', q['error'])
        st, q, _ = self.req('POST', '/api/falkordb/query', {'graph': 'companykit', 'query': 'MATCH (n) RETURN n'})
        self.assertEqual(st, 400)
        st, q, _ = self.req('POST', '/api/falkordb/drop', {'graph': 'social'})
        self.assertEqual(st, 400)


if __name__ == '__main__':
    unittest.main()
