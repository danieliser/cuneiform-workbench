"""Persisted-snapshot lifecycle on hermetic scratch roots (review F-2/F-3/F-4/F-5/F-12).

The backend itself publishes snapshots (import_source / build_cohort + import_cohort, with its
ledger) into a scratch research root; the bridge then discovers, verifies, serves and traces
them. Lifecycle cases mutate only that scratch root. The canonical corpus root is never touched.
"""
import hashlib
import json
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
from support import REPO, ROOT, make_roots, rm, writable  # noqa: E402

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

RULE = {'name': 'test-rule', 'min_eligible_positions': 1, 'min_eligible_pairs': 1, 'max_record_bytes': 262144,
        'require_unique_source_key': True, 'order': 'sha256(parent_sha256 + ":" + source_key) ascending'}


def wait_ready(b, key, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = b.datasets.get(key)
        if d is not None and d.status not in ('pending', 'verifying'):
            return d
        time.sleep(0.05)
    return b.datasets.get(key)


class SnapshotPathTest(unittest.TestCase):
    def setUp(self):
        self.scratch, self.research, self.home = make_roots('snap')

    def tearDown(self):
        if getattr(self, 'b', None):
            self.b.close()
        rm(self.scratch)

    def backend(self, stable_s=0.0):
        from server.bridge import Backend
        self.b = Backend(REPO, self.research, show_demos='never', home=self.home, stable_s=stable_s)
        return self.b

    def import_fixture(self):
        from cuneiform.storage import Config, import_source
        try:
            return import_source('ebl', REPO / 'fixtures/ebl-synthetic.json', Config(self.research), source_url='synthetic:fixture')['snapshot']
        except ValueError as exc:
            self.skipTest(f'backend refused scratch import: {exc}')

    def build_cohort(self, with_parent_raw=True):
        """Parent = top-level JSON array of synthetic eBL records; cohort payload = byte-identical slices."""
        from cuneiform.corpus import build_cohort
        from cuneiform.storage import Config, import_cohort
        recs = [r for r in json.loads((REPO / 'fixtures/ebl-synthetic.json').read_text())['fragments'] if isinstance(r.get('atf'), str)]
        seen, unique = set(), []
        for r in recs:
            if r.get('_id') not in seen:
                seen.add(r.get('_id'))
                unique.append(r)
        parent_bytes = ('[\n' + ',\n'.join(json.dumps(r, ensure_ascii=False) for r in unique) + '\n]\n').encode()
        sha = hashlib.sha256(parent_bytes).hexdigest()
        parent_path = self.scratch / 'parent.json'
        parent_path.write_bytes(parent_bytes)
        if with_parent_raw:
            raw = self.research / 'raw' / 'sha256' / sha
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(parent_bytes)
        parent = {'sha256': sha, 'md5': hashlib.md5(parent_bytes).hexdigest(), 'bytes': len(parent_bytes), 'url': 'synthetic:parent',
                  'receipt_id': 'receipt:' + '0' * 32, 'label': 'synthetic-parent.json', 'acquired_at': '2026-11-03T00:00:00+00:00',
                  'rights': {'status': 'synthetic'}}
        cohort = build_cohort(parent_path, parent, None, self.scratch / 'cohorts', RULE)
        out = import_cohort(Path(cohort['directory']), Config(self.research), parent_path=parent_path, memory='256MB')
        return out['snapshot'], sha

    # -- basic path -----------------------------------------------------------
    def test_discover_verify_query_trace_single_input(self):
        sid = self.import_fixture()
        b = self.backend()
        listing = b.discover(wait=30)
        self.assertEqual([d['key'] for d in listing['datasets']], [sid])
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'ready', ds.error)
        self.assertEqual(ds.publication['status'], 'ledgered')
        self.assertEqual(ds.version, json.loads((ds.path / 'manifest.json').read_text())['manifest_sha256'])
        self.assertEqual(ds.selection['kind'], 'single-input')
        from server.explore import Explorer
        e = Explorer(b)
        r = e.query({'dataset': sid, 'version': ds.version, 'pattern': ['A', 'B', 'C']})
        self.assertEqual(r['count'], 6)
        self.assertTrue(r['parity']['ok'])
        self.assertEqual(r['dataset']['version'], ds.version)
        self.assertTrue(r['scope_note'])
        occ = e.occurrence(sid, r['rows'][0]['occurrence_ids'][0], ds.version)
        self.assertIsNone(occ['trace_error'])
        self.assertEqual(occ['trace']['rights_attribution'], 'verified snapshot manifest; not an authenticated license grant')
        self.assertTrue(ds.examples)

    def test_no_connection_is_held(self):
        sid = self.import_fixture()
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        from server.explore import Explorer
        Explorer(b).query({'dataset': sid, 'pattern': ['A', 'B']})
        self.assertIsNone(ds.db, 'snapshots are opened per request, never kept open')
        import duckdb
        rw = duckdb.connect(str(self.scratch / 'probe.duckdb'))  # a writer elsewhere is unaffected; and the snapshot file is not locked:
        rw.close()
        con = duckdb.connect(str(ds.path / 'graph.duckdb'), read_only=True)
        con.close()

    # -- cohort scope + parent trace ---------------------------------------------
    def test_cohort_scope_and_parent_trace(self):
        try:
            sid, parent_sha = self.build_cohort(with_parent_raw=True)
        except ValueError as exc:
            self.skipTest(f'backend refused scratch cohort: {exc}')
        b = self.backend()
        listing = b.discover(wait=60)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'ready', ds.error)
        pub = next(d for d in listing['datasets'] if d['key'] == sid) if ds.status == 'ready' else None
        sel = b.datasets[sid].public()['selection']
        self.assertEqual(sel['kind'], 'cohort')
        self.assertIn('COHORT', sel['label'])
        self.assertIsInstance(sel['eligible_records'], int)
        self.assertIsInstance(sel['parent_records'], int)
        self.assertIn('not corpus frequencies', sel['note'])
        self.assertEqual(sel['parent']['sha256'], parent_sha)
        self.assertIn('COHORT', b.datasets[sid].title)
        self.assertIsNotNone(pub)
        self.assertEqual({i['source'] for i in ds.inputs}, {'ebl-cohort', 'parent'})
        self.assertEqual(next(i for i in ds.inputs if i['source'] == 'parent')['receipt'], 'receipt:' + '0' * 32)
        from server.explore import Explorer
        e = Explorer(b)
        r = e.query({'dataset': sid, 'pattern': ['A', 'B']})
        self.assertGreater(r['count'], 0)
        self.assertIn('cohort', r['scope_note'])
        occ = e.occurrence(sid, r['rows'][0]['occurrence_ids'][0], ds.version)
        self.assertIsNone(occ['trace_error'])
        pt = occ['trace']['parent_trace']
        self.assertEqual(pt['status'], 'verified')
        self.assertEqual(pt['parent_sha256'], parent_sha)
        self.assertIn('parent_file_byte_span', pt)
        self.assertIn('byte-identical', occ['trace']['origin'])
        # parent raw object removed -> explicit status, not a silent payload-only success
        raw = self.research / 'raw' / 'sha256' / parent_sha
        raw.unlink()
        occ = e.occurrence(sid, r['rows'][0]['occurrence_ids'][0], ds.version)
        self.assertEqual(occ['trace']['parent_trace']['status'], 'parent-raw-unavailable')

    # -- lifecycle -------------------------------------------------------------------
    def test_settling_then_ready(self):
        sid = self.import_fixture()
        b = self.backend(stable_s=3600)
        b.discover()
        self.assertEqual(b.datasets[sid].status, 'settling')
        self.assertIsNone(b.datasets[sid].version)
        b.stable_s = 0
        b.discover(wait=30)
        self.assertEqual(wait_ready(b, sid).status, 'ready')

    def test_changed_bytes_refuse_results_and_reverify(self):
        sid = self.import_fixture()
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        old = ds.version
        from server.bridge import VersionChanged
        from server.explore import Explorer
        e = Explorer(b)
        target = sorted((ds.path / 'parquet').glob('*.parquet'))[0]
        writable(ds.path / 'parquet')
        writable(target)
        target.write_bytes(target.read_bytes() + b'\0')  # same ID, different bytes
        with self.assertRaises(VersionChanged):
            e.query({'dataset': sid, 'version': old, 'pattern': ['A', 'B']})
        self.assertNotEqual(b.datasets[sid].status, 'ready')
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'failed')
        self.assertIn('integrity', ds.error)
        with self.assertRaises(VersionChanged):
            e.report(sid, old)

    def test_same_size_byte_change_caught_by_per_request_verify(self):
        sid = self.import_fixture()
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        from server.bridge import VersionChanged, _fingerprint
        from server.explore import Explorer
        target = sorted((ds.path / 'parquet').glob('*.parquet'))[0]
        st = target.stat()
        writable(ds.path / 'parquet')
        writable(target)
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0xFF
        target.write_bytes(bytes(data))
        import os
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size, same mtime: only hashing can tell
        target.chmod(st.st_mode)
        (ds.path / 'parquet').chmod(0o555)
        fp = _fingerprint(ds.path)
        fp['@ledger'] = ds.fingerprint['@ledger']
        ds.fingerprint = fp  # simulate a stat-identical change (ctime/inode aside)
        with self.assertRaises(VersionChanged):
            Explorer(b).query({'dataset': sid, 'version': ds.version, 'pattern': ['A', 'B']})

    def test_removed_snapshot_is_dropped(self):
        sid = self.import_fixture()
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        ver = ds.version
        rm(ds.path)
        listing = b.discover()
        self.assertNotIn(sid, [d['key'] for d in listing['datasets']])
        from server.bridge import VersionChanged
        with self.assertRaises(VersionChanged):
            b.get(sid, ver)

    def test_partial_then_complete_becomes_ready(self):
        sid = self.import_fixture()
        snap = self.research / 'snapshots' / sid
        hold = self.scratch / 'hold'
        writable(snap)
        shutil.move(str(snap), str(hold))
        snap.mkdir()
        shutil.copy2(hold / 'manifest.json', snap / 'manifest.json')
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'failed')
        for p in hold.iterdir():
            if p.name != 'manifest.json':
                (shutil.copytree if p.is_dir() else shutil.copy2)(p, snap / p.name)
        b.discover(wait=30)  # fingerprint changed -> retried immediately, not after the backoff
        self.assertEqual(wait_ready(b, sid).status, 'ready', b.datasets[sid].error)

    def test_unledgered_snapshot_is_not_served(self):
        sid = self.import_fixture()
        ledger = self.research / 'snapshots' / '.publication-ledger.jsonl'
        saved = ledger.read_text()
        writable(ledger)
        ledger.write_text('')
        b = self.backend()
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'unpublished')
        self.assertEqual(ds.publication['status'], 'not-in-ledger')
        ledger.write_text(saved)
        b.discover(wait=30)
        self.assertEqual(wait_ready(b, sid).status, 'ready')

    def test_legacy_incident_is_distinguished(self):
        from server.bridge import publication_of
        m = {'manifest_sha256': 'a' * 64}
        self.assertEqual(publication_of([{'manifest_sha256': 'a' * 64, 'backfilled': True, 'incident': 'rebuilt'}], m)['status'], 'legacy-incident')
        self.assertEqual(publication_of([{'manifest_sha256': 'a' * 64, 'backfilled': True}], m)['status'], 'ledgered-legacy')
        self.assertEqual(publication_of([{'manifest_sha256': 'a' * 64}], m)['status'], 'ledgered')
        self.assertFalse(publication_of([{'manifest_sha256': 'b' * 64}], m)['servable'])
        self.assertFalse(publication_of([{'manifest_sha256': 'a' * 64}] * 2, m)['servable'])

    def test_only_snapshot_dirs_are_discovered(self):
        sid = self.import_fixture()
        snaps = self.research / 'snapshots'
        writable(snaps)
        (snaps / '.pending-abc').mkdir()
        (snaps / 'snapshot:nothex').mkdir()
        b = self.backend()
        listing = b.discover(wait=30)
        self.assertEqual([d['key'] for d in listing['datasets']], [sid])


if __name__ == '__main__':
    unittest.main()
