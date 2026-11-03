"""Smoke + boundary tests through the HTTP server, on a hermetic research root (review F-12).

The in-process server reads only a scratch research root (retained samples copied in, no
snapshots) with CUNEIFORM_UI_SHOW_DEMOS=always, so results do not depend on corpus work.
Run: bin/cuneiform-ui test
"""
import http.client
import json
import os
import socket
import sys
import threading
import unittest

sys.dont_write_bytecode = True
from support import ROOT, make_roots, rm  # noqa: E402

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
        cls.scratch, research, home = make_roots('app')
        cls.port = free_port()
        cls._env = {k: os.environ.get(k) for k in ('CUNEIFORM_UI_PORT', 'CUNEIFORM_RESEARCH_ROOT', 'CUNEIFORM_UI_HOME', 'CUNEIFORM_UI_SHOW_DEMOS', 'CUNEIFORM_FALKORDB_GRAPH_PREFIX')}
        os.environ.update({'CUNEIFORM_UI_PORT': str(cls.port), 'CUNEIFORM_RESEARCH_ROOT': str(research),
                           'CUNEIFORM_UI_HOME': str(home), 'CUNEIFORM_UI_SHOW_DEMOS': 'always',
                           'CUNEIFORM_FALKORDB_GRAPH_PREFIX': 'cuneiform_test_'})
        from server import app as appmod
        cls.appmod = appmod
        cls.app = appmod.App(cls.port)
        appmod.Handler.app = cls.app
        cls.httpd = appmod.Server(('127.0.0.1', cls.port), appmod.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.leftovers = cls.drop_test_graphs()  # review N-7: an earlier interrupted run must not leak into this one

    @classmethod
    def live_test_graphs(cls) -> list[str] | None:
        fk = cls.app.falkor
        try:
            with fk.conn('cuneiform-ro', timeout=5) as r:
                return sorted(g for g in (r.cmd('GRAPH.LIST') or []) if g.startswith('cuneiform_test_'))
        except Exception:
            return None  # FalkorDB not running: the FalkorDB tests skip

    @classmethod
    def drop_test_graphs(cls) -> list[str]:
        found = cls.live_test_graphs() or []
        for g in found:
            assert cls.app.falkor.drop(g) == {'dropped': g}
        return found

    @classmethod
    def tearDownClass(cls):
        left = cls.live_test_graphs()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.backend.close()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rm(cls.scratch)
        if left:
            raise AssertionError(f'test graphs left behind: {left}')

    def req(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
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

    def drop_checked(self, graph):
        st, out, _ = self.req('POST', '/api/falkordb/drop', {'graph': graph})
        self.assertEqual(st, 200, out)
        self.assertIn('end_to_end_ms', out.pop('timing'))
        self.assertEqual(out, {'dropped': graph})
        self.assertNotIn(graph, self.live_test_graphs() or [])

    def ds(self, key):
        st, body, _ = self.req('GET', '/api/status')
        return {d['key']: d for d in body['datasets']}.get(key)

    def need(self, key):
        d = self.ds(key)
        if d is None:
            self.skipTest(f'retained sample {key} not present on this machine')
        return d

    # -- smoke ------------------------------------------------------------------
    def test_boot_is_hermetic(self):
        st, body, hdr = self.req('GET', '/api/status')
        self.assertEqual(st, 200)
        self.assertNotEqual(body['backend']['compatibility'], 'incompatible')
        self.assertEqual(body['real_snapshots'], 0, 'hermetic root has no snapshots')
        self.assertFalse(body['snapshot_dir_exists'])
        self.assertNotIn('cuneiform-research/snapshots', body['snapshot_dir'].replace(os.sep, '/').split('test-roots')[0])
        syn = {d['key']: d for d in body['datasets']}['synthetic-fixtures']
        self.assertEqual(syn['status'], 'ready')
        self.assertRegex(syn['version'], r'^[0-9a-f]{32}$')
        self.assertEqual(syn['selection']['kind'], 'synthetic')
        self.assertIn('Content-Security-Policy', hdr)
        st, html, hdr = self.req('GET', '/')
        self.assertEqual(st, 200)
        self.assertNotIn(b'<script>', html)
        self.assertIn("script-src 'self'", hdr['Content-Security-Policy'])

    def test_synthetic_expected_counts_carry_scope_and_version(self):
        ver = self.ds('synthetic-fixtures')['version']
        cases = [(['A', 'B', 'C'], 'source', 6), (['A', '?', 'C'], 'source', 11), (['A', 'C'], 'source', 0),
                 (['A', 'B', 'C'], 'strict', 0), (['A'], 'source', 12)]
        for pattern, policy, expect in cases:
            st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'version': ver, 'pattern': pattern, 'policy': policy})
            self.assertEqual(st, 200, r)
            self.assertEqual(r['count'], expect, pattern)
            self.assertEqual(r['dataset']['version'], ver)
            self.assertIn('not evidence', r['scope_note'])
            if len(pattern) > 1:
                self.assertTrue(r['parity']['ok'])
            t = r['timing']  # end-to-end request time with an optional breakdown; query_ms is only part of it
            self.assertGreaterEqual(t['end_to_end_ms'], t['breakdown']['query_ms'])
            self.assertIn('not the request latency', t['note'])
            if policy == 'strict':
                self.assertIn('intact AND certain', r['strict_note'])

    def test_stale_version_is_409(self):
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'version': 'f' * 32, 'pattern': ['A', 'B']})
        self.assertEqual(st, 409, r)
        self.assertEqual(r['code'], 'dataset-version-changed')
        st, r, _ = self.req('GET', f'/api/report?dataset=synthetic-fixtures&version={"0" * 32}')
        self.assertEqual(st, 409)
        st, r, _ = self.req('GET', '/api/report?dataset=snapshot:' + 'a' * 32)
        self.assertEqual(st, 409, 'unknown (removed) dataset is a version change, not a crash')

    def test_occurrence_trace_roundtrip_and_graph(self):
        _, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A', 'B', 'C']})
        oid = r['rows'][0]['occurrence_ids'][1]
        st, d, _ = self.req('GET', f'/api/occurrence?dataset=synthetic-fixtures&id={oid}')
        self.assertEqual(st, 200, d)
        self.assertIsNone(d['trace_error'])
        self.assertEqual(d['trace']['resolved_raw'], d['occurrence']['raw'])
        self.assertIn('not a verified snapshot', d['trace']['origin'])
        self.assertIn('canonical DB', d['excerpt']['origin'])
        self.assertEqual(d['excerpt']['token'], d['occurrence']['raw'])
        st, g, _ = self.req('GET', f'/api/neighborhood?dataset=synthetic-fixtures&id={d["occurrence"]["line_id"]}&per=2')
        self.assertEqual(st, 200)
        aggs = [n for n in g['nodes'] if n['kind'] == 'Aggregate']
        self.assertTrue(aggs, 'large fan-out must be aggregated')
        for a in aggs:
            self.assertFalse(a['lower_bound'])
            grp = next(x for x in g['groups'] if x['kind'] == a['edge_kind'] and x['direction'] == a['direction'])
            self.assertEqual(a['hidden'], grp['total'] - 2)
        self.assertLessEqual(len([n for n in g['nodes'] if n['kind'] != 'Aggregate']), 1 + 2 * len(g['groups']))

    def test_graph_neighborhood_every_line_and_sequence_of_samples(self):
        """Review F-6: the README sample's Graph view failed on 8/34 lines with DuckDB OOM."""
        from server.explore import PROPS  # noqa: F401
        checked = 0
        for key in ('sample-ebl-readme', 'synthetic-fixtures'):
            d = self.ds(key)
            if d is None:
                continue
            ds = self.app.backend.get(key)
            ids = ds.db.execute('SELECT id FROM line UNION ALL SELECT id FROM sequence_version').fetchall()
            for (nid,) in ids:
                st, g, _ = self.req('GET', f'/api/neighborhood?dataset={key}&id={nid}&per=8')
                self.assertEqual(st, 200, (key, nid, g))
                checked += 1
        self.assertGreater(checked, 10)

    def test_missing_layers_are_unavailable_not_zero(self):
        self.need('sample-ebl-k1')
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'sample-ebl-k1', 'pattern': ['A', 'B']})
        self.assertEqual(st, 200)
        self.assertIn('unavailable', r['missing'])
        st, rep, _ = self.req('GET', '/api/report?dataset=sample-ebl-k1')
        self.assertEqual(st, 200, rep)
        states = {x['layer']: x for x in rep['layers']}
        for layer in ('witness', 'sequence_version', 'line', 'occurrence', 'sign_type', 'translation_segment'):
            self.assertEqual(states[layer]['rows'], 0)
            self.assertIn('no text supplied', states[layer]['state'], layer)
        for x in rep['layers']:
            if x['layer'] not in ('rejection', 'exclusion') and x['rows'] == 0:
                self.assertTrue(x['state'].startswith('unavailable'), x)

    # -- boundaries ---------------------------------------------------------------
    def test_host_origin_and_method_guards(self):
        self.assertEqual(self.req('GET', '/api/status', headers={'Host': 'evil.example:80'})[0], 421)
        self.assertEqual(self.req('GET', '/api/status', headers={'Host': f'evil.example:{self.port}'})[0], 421)
        self.assertEqual(self.req('GET', '/api/status', headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.req('GET', '/api/status', headers={'Origin': 'null'})[0], 403)
        self.assertEqual(self.req('GET', '/api/status', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.req('GET', '/api/status', headers={'Host': f'localhost:{self.port}'})[0], 200)
        self.assertEqual(self.req('POST', '/api/query', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
        big = b'{"x":"' + b'a' * 70000 + b'"}'
        self.assertEqual(self.req('POST', '/api/query', raw=big, headers={'Content-Type': 'application/json'})[0], 413)
        self.assertEqual(self.req('DELETE', '/api/status')[0], 405)

    def test_request_robustness(self):
        """Review F-16: these used to 500 or drop the connection."""
        st, r, _ = self.req('POST', '/api/falkordb/drop', raw=b'["x"]', headers={'Content-Type': 'application/json'})
        self.assertEqual(st, 400, r)
        st, r, _ = self.req('POST', '/api/query', raw=b'[]', headers={'Content-Type': 'application/json'})
        self.assertEqual(st, 400, r)
        many = '&'.join(f'f{i}=1' for i in range(30))
        st, r, _ = self.req('GET', '/api/status?' + many)
        self.assertEqual(st, 400, r)
        deep = b'[' * 50000 + b']' * 50000
        st, r, _ = self.req('POST', '/api/query', raw=deep[:60000], headers={'Content-Type': 'application/json'})
        self.assertEqual(st, 400, r)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A', 'B'], 'limit': True})
        self.assertEqual(st, 400, r)

    def test_path_traversal_and_bad_ids(self):
        for path in ['/../server/app.py', '/%2e%2e/server/app.py', '/app.js/../../README.md', '/web/app.js', '/.git/config']:
            self.assertEqual(self.req('GET', path)[0], 404, path)
        st, r, _ = self.req('GET', '/api/report?dataset=../../etc/passwd')
        self.assertEqual(st, 400)
        st, r, _ = self.req('GET', "/api/node?dataset=synthetic-fixtures&id=artifact:x%27%20OR%201=1")
        self.assertEqual(st, 400)
        st, r, _ = self.req('GET', '/api/report?dataset=synthetic-fixtures&version=..%2F..')
        self.assertEqual(st, 400)

    def test_injection_inputs_are_data(self):
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ["A'", 'OR', "1=1;--"]})
        self.assertEqual(st, 200)
        self.assertEqual(r['count'], 0)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['<img', 'src=x', 'onerror=alert(1)>']})
        self.assertEqual(st, 200)
        self.assertEqual(r['count'], 0)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A B']})
        self.assertEqual(st, 400)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A'] * 6})
        self.assertEqual(st, 400)
        st, r, _ = self.req('POST', '/api/query', {'dataset': 'synthetic-fixtures', 'pattern': ['A', 'B'], 'limit': 5000})
        self.assertEqual(st, 400)
        st, r, _ = self.req('GET', '/api/search?dataset=synthetic-fixtures&q=%25_%5C')
        self.assertEqual(st, 200)

    def test_graph_engine_paging_reports_materialization_cap(self):
        """Review F-10: page past the backend's 100-match cap without claiming 'not truncated'."""
        from server.explore import graph_page
        res = {'occurrences': 152, 'matches': [{'id': i} for i in range(100)]}
        p = graph_page(res, 75, 25)
        self.assertTrue(p['truncated'])
        self.assertIsNone(p['next_offset'])
        self.assertTrue(p['materialized_cap_reached'])
        self.assertIn('101–152', p['note'])
        p = graph_page(res, 0, 25)
        self.assertEqual(p['next_offset'], 25)
        p = graph_page({'occurrences': 6, 'matches': [{'id': i} for i in range(6)]}, 0, 25)
        self.assertFalse(p['truncated'])
        self.assertFalse(p['materialized_cap_reached'])

    # -- FalkorDB ---------------------------------------------------------------------
    def test_falkordb_arbitrary_cypher_disabled(self):
        st, q, _ = self.req('POST', '/api/falkordb/query', {'graph': 'cuneiform_x', 'query': "LOAD CSV FROM 'https://example.invalid/x' AS r RETURN r"})
        self.assertEqual(st, 403)
        st, s, _ = self.req('GET', '/api/falkordb/status')
        self.assertFalse(s['browser']['enabled'])
        self.assertFalse(s['console']['arbitrary_cypher'])
        from server.falkor import TEMPLATES, _FORBIDDEN
        for t in TEMPLATES.values():
            self.assertIsNone(_FORBIDDEN.search(t['cypher']))
            self.assertIn('LIMIT {limit}', t['cypher'])

    def test_falkordb_load_verify_tamper_rebuild_and_templates(self):
        from server.falkor import Resp, cy
        fk = self.app.falkor
        if not fk.status()['reachable']:
            self.skipTest('isolated FalkorDB not running (bin/cuneiform-ui falkordb-up)')
        evil = ['a"b', 'c\\"d', "x'y", 'nl\nx', '") MATCH (n) DETACH DELETE n //', 'KA×A {d}EN.LIL₂']
        with fk.conn('cuneiform-rw') as r:
            res = r.cmd('GRAPH.QUERY', 'cuneiform_test_escape', 'CYPHER xs=' + cy(evil) + ' UNWIND $xs AS x RETURN x')
            self.assertEqual(r.cmd('GRAPH.DELETE', 'cuneiform_test_escape'), 'OK')
        self.assertEqual([row[0] for row in res[1]], evil)
        anon = Resp(fk.host, fk.port, timeout=2)
        try:
            with self.assertRaisesRegex(Exception, 'NOAUTH'):
                anon.cmd('GRAPH.LIST')  # unauthenticated
        finally:
            anon.close()
        ver = self.ds('synthetic-fixtures')['version']
        st, out, _ = self.req('POST', '/api/falkordb/load', {'dataset': 'synthetic-fixtures', 'version': ver})
        self.assertEqual(st, 200, out)
        g = out['graph']
        try:
            self.assertTrue(g.startswith('cuneiform_test_'))
            self.assertTrue(out['content_verified'])
            self.assertEqual(out['counts'], out['receipt']['counts'])
            self.assertEqual(out['receipt']['dataset_version'], ver)
            # tamper with one property (as a Browser user could have), then prove verify notices and reload restores
            with fk.conn('cuneiform-rw') as r:
                r.cmd('GRAPH.QUERY', g, "MATCH (n:SignOccurrence) WITH n ORDER BY n.id LIMIT 1 SET n.reading = 'TAMPERED'")
            st, v, _ = self.req('POST', '/api/falkordb/verify', {'graph': g})
            self.assertEqual(st, 200, v)
            self.assertFalse(v['content_verified'])
            st, out2, _ = self.req('POST', '/api/falkordb/load', {'dataset': 'synthetic-fixtures', 'version': ver})
            self.assertEqual(st, 200, out2)
            self.assertTrue(out2['rebuilt'] and out2['content_verified'])
            st, t, _ = self.req('POST', '/api/falkordb/template', {'graph': g, 'template': 'reading-pair-count', 'params': {'a': 'A', 'b': 'B'}})
            self.assertEqual(st, 200, t)
            self.assertNotIn('TAMPERED', json.dumps(t))
            # bounded, exact truncation
            st, t, _ = self.req('POST', '/api/falkordb/template', {'graph': g, 'template': 'node-kinds', 'limit': 2})
            self.assertEqual(st, 200, t)
            self.assertEqual(t['row_count'], 2)
            self.assertTrue(t['truncated'])
            # injection-looking parameters are data
            st, t, _ = self.req('POST', '/api/falkordb/template', {'graph': g, 'template': 'reading-pair', 'params': {'a': '"})-[]-()', 'b': 'x'}})
            self.assertEqual(st, 200, t)
            self.assertEqual(t['rows'], [])
            for bad in ({'graph': g, 'template': 'nope'}, {'graph': g, 'template': 'node-kinds', 'params': {'q': 'x'}},
                        {'graph': g, 'template': 'node-kinds', 'limit': 501}, {'graph': 'companykit', 'template': 'node-kinds'}):
                self.assertEqual(self.req('POST', '/api/falkordb/template', bad)[0], 400, bad)
            self.assertEqual(self.req('POST', '/api/falkordb/drop', {'graph': 'social'})[0], 400)
        finally:
            self.drop_checked(g)  # review N-7: cleanup success is asserted, a failure is reported, not swallowed

    def falkor_or_skip(self):
        fk = self.app.falkor
        if not fk.status()['reachable']:
            self.skipTest('isolated FalkorDB not running (bin/cuneiform-ui falkordb-up)')
        return fk

    def test_falkordb_verify_detects_added_properties_and_labels(self):
        """Review N-6: an added property, label or extra receipt must fail verification, not just a changed value."""
        fk = self.falkor_or_skip()
        ver = self.ds('synthetic-fixtures')['version']
        st, out, _ = self.req('POST', '/api/falkordb/load', {'dataset': 'synthetic-fixtures', 'version': ver})
        self.assertEqual(st, 200, out)
        g = out['graph']
        self.addCleanup(self.drop_checked, g)
        tampers = [
            ("MATCH (n:SignOccurrence) WITH n ORDER BY n.id LIMIT 1 SET n.injected = 'x'", 'labels/properties'),
            ("MATCH (n:Artifact) WITH n ORDER BY n.id LIMIT 1 SET n:Extra", 'labels/properties'),
            ("MATCH ()-[e:NEXT]->() WITH e ORDER BY e.id LIMIT 1 SET e.weight = 1", 'labels/properties'),
            ("MATCH (n:Line) WITH n ORDER BY n.id LIMIT 1 SET n.display = NULL", 'labels/properties'),
            ("CREATE (:ProjectionReceipt {export_id: 'x', dataset: 'y', dataset_version: 'z', receipt: '{}'})", 'ProjectionReceipt'),
        ]
        for cypher, expect in tampers:
            with fk.conn('cuneiform-rw') as r:
                r.cmd('GRAPH.QUERY', g, cypher)
            st, v, _ = self.req('POST', '/api/falkordb/verify', {'graph': g})
            self.assertEqual(st, 200, v)
            self.assertFalse(v['content_verified'], cypher)
            self.assertEqual(v['status'], 'content-differs')
            self.assertTrue(any(expect in p for p in v['problems']), (cypher, v['problems']))
            st, out2, _ = self.req('POST', '/api/falkordb/load', {'dataset': 'synthetic-fixtures', 'version': ver})
            self.assertEqual(st, 200, out2)
            self.assertTrue(out2['content_verified'])
        st, v, _ = self.req('POST', '/api/falkordb/verify', {'graph': g})
        self.assertTrue(v['content_verified'], v)
        self.assertIn('property-key set', v['compared'])

    def test_falkordb_legacy_projection_verify_is_typed_and_read_only(self):
        """Review N-5: a format-1 projection gets a typed rebuild-required answer (not 500) and is left unchanged."""
        fk = self.falkor_or_skip()
        g = 'cuneiform_test_legacy_format1'
        legacy = json.dumps({'format': 'cuneiform-ui-graph-export-1', 'export_id': 'a' * 24, 'dataset': 'synthetic-fixtures',
                             'files': {'nodes.jsonl': 'b' * 64, 'edges.jsonl': 'c' * 64}, 'counts': {'nodes': 1, 'edges': 0}})
        with fk.conn('cuneiform-rw') as r:
            r.cmd('GRAPH.QUERY', g, "CREATE (:SignOccurrence {id: 'occurrence:" + '0' * 32 + "', kind: 'SignOccurrence'})")
            r.cmd('GRAPH.QUERY', g, 'CYPHER p={export_id: "' + 'a' * 24 + '", dataset: "synthetic-fixtures", receipt: ' + json.dumps(legacy) + '} '
                                    'CREATE (x:ProjectionReceipt) SET x = $p')
        self.addCleanup(self.drop_checked, g)
        with fk.conn('cuneiform-ro') as r:
            before = r.cmd('GRAPH.RO_QUERY', g, 'MATCH (n) RETURN count(n), collect(keys(n))')[1]
        st, v, _ = self.req('POST', '/api/falkordb/verify', {'graph': g})
        self.assertEqual(st, 200, v)
        self.assertEqual((v['status'], v['content_verified'], v['rebuild_required']), ('unsupported-format', False, True))
        self.assertEqual(v['format'], 'cuneiform-ui-graph-export-1')
        self.assertIn('Rebuild', v['problems'][0])
        with fk.conn('cuneiform-ro') as r:
            self.assertEqual(r.cmd('GRAPH.RO_QUERY', g, 'MATCH (n) RETURN count(n), collect(keys(n))')[1], before)
        listed = {x['graph']: x for x in self.req('GET', '/api/falkordb/status')[1]['graphs']}[g]
        self.assertEqual((listed['verifiable'], listed['verify_status']), (False, 'unsupported-format'))

    # -- coverage paging (review N-4) -------------------------------------------------
    def test_coverage_page_is_explicit(self):
        from server.explore import coverage_page
        rows = [{'source_record_id': i} for i in range(300)]
        p = coverage_page(rows, 0)
        self.assertEqual((len(p['coverage']), p['coverage_total'], p['coverage_truncated'], p['coverage_next_offset']), (200, 300, True, 200))
        self.assertEqual(p['coverage_shown'], 'records 1–200 of 300')
        p = coverage_page(rows, 200)
        self.assertEqual((len(p['coverage']), p['coverage_next_offset'], p['coverage_prev_offset']), (100, None, 0))
        self.assertEqual(p['coverage_shown'], 'records 201–300 of 300')
        p = coverage_page(rows[:3], 0)
        self.assertEqual((p['coverage_truncated'], p['coverage_next_offset']), (False, None))

    def test_coverage_endpoint_reports_exact_total(self):
        ver = self.ds('synthetic-fixtures')['version']
        st, rep, _ = self.req('GET', f'/api/report?dataset=synthetic-fixtures&version={ver}')
        self.assertEqual(st, 200, rep)
        total = rep['coverage_total']
        self.assertEqual(len(rep['coverage']), min(total, 200))
        self.assertEqual(rep['coverage_truncated'], total > 200)
        st, page, _ = self.req('GET', f'/api/coverage?dataset=synthetic-fixtures&version={ver}&offset=1')
        self.assertEqual(st, 200, page)
        self.assertEqual((page['coverage_total'], page['coverage_offset']), (total, 1))
        self.assertEqual(page['coverage'], rep['coverage'][1:201])
        self.assertEqual(page['dataset']['version'], ver)
        self.assertEqual(self.req('GET', f'/api/coverage?dataset=synthetic-fixtures&version={ver}&offset=-1')[0], 400)
        self.assertEqual(self.req('GET', f'/api/coverage?dataset=synthetic-fixtures&version={"0" * 32}')[0], 409)


if __name__ == '__main__':
    unittest.main()
