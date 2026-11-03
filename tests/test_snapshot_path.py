"""Persisted-snapshot lifecycle on hermetic scratch roots (review F-2/F-3/F-4/F-5/F-12).

The backend itself publishes snapshots (import_source / build_cohort + import_cohort, with its
ledger) into a scratch research root; the bridge then discovers, verifies, serves and traces
them. Lifecycle cases mutate only that scratch root. The canonical corpus root is never touched.
"""
import hashlib
import json
import shutil
import sys
import threading
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


class Roots:
    """Scratch research root + UI home per test, with backend-published fixtures."""

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


class SnapshotPathTest(Roots, unittest.TestCase):
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

    def test_symlinked_entries_and_id_mismatch_are_refused(self):
        """Q-020 B2 (UI side): a symlinked store entry, a symlink inside one, or a foreign manifest ID is never served."""
        sid = self.import_fixture()
        snaps = self.research / 'snapshots'
        writable(snaps)
        alias = 'snapshot:' + 'e' * 32
        (snaps / alias).symlink_to(snaps / sid, target_is_directory=True)  # same bytes under another ID, via a link
        elsewhere = self.scratch / 'elsewhere'
        shutil.copytree(snaps / sid, elsewhere / sid, symlinks=True)
        writable(snaps / sid)
        moved = self.scratch / 'moved'
        shutil.move(str(snaps / sid), str(moved))
        (snaps / sid).symlink_to(elsewhere / sid, target_is_directory=True)  # the published ID itself is a link
        b = self.backend()
        b.discover(wait=30)
        for key in (alias, sid):
            ds = wait_ready(b, key)
            self.assertEqual(ds.status, 'refused', (key, ds.status, ds.error))
            self.assertEqual(ds.publication['status'], 'symlinked-entry')
        (snaps / sid).unlink()
        shutil.move(str(moved), str(snaps / sid))
        (snaps / alias).unlink()
        copy = snaps / alias
        shutil.copytree(snaps / sid, copy)  # a plain directory whose manifest names another ID
        b2 = self.backend()
        b2.discover(wait=30)
        self.assertEqual(wait_ready(b2, alias).publication['status'], 'id-mismatch')
        self.assertEqual(wait_ready(b2, sid).status, 'ready', b2.datasets[sid].error)
        raw = snaps / sid / 'raw'
        writable(snaps / sid)
        (snaps / sid / 'extra-link').symlink_to(raw)
        b3 = self.backend()
        b3.discover(wait=30)
        self.assertEqual(wait_ready(b3, sid).publication['status'], 'symlink-inside-entry')

    def test_only_snapshot_dirs_are_discovered(self):
        sid = self.import_fixture()
        snaps = self.research / 'snapshots'
        writable(snaps)
        (snaps / '.pending-abc').mkdir()
        (snaps / 'snapshot:nothex').mkdir()
        b = self.backend()
        listing = b.discover(wait=30)
        self.assertEqual([d['key'] for d in listing['datasets']], [sid])


class SlowVerify:
    """Proxy for the backend storage module: background verification passes block until released.

    Request-time verification (the HTTP/Explorer thread) runs unblocked, so a test can query a dataset while
    its routine re-verification is still in flight (review N-1 race).
    """

    def __init__(self, real):
        self._real = real
        self.release = threading.Event()
        self.entered = threading.Event()
        self.fail = None

    def __getattr__(self, name):
        return getattr(self._real, name)

    def verify(self, path, sid=None, **kw):
        if threading.current_thread() is not threading.main_thread():
            self.entered.set()
            self.release.wait(30)
            if self.fail:
                raise self.fail
        return self._real.verify(path, sid, **kw)


class ReverifyRaceTest(Roots, unittest.TestCase):
    """Review N-1: routine re-verification must not take a ready, unchanged snapshot out of service."""

    def ready_snapshot(self, show_demos='never'):
        sid = self.import_fixture()
        from server.bridge import Backend
        self.b = Backend(REPO, self.research, show_demos=show_demos, home=self.home, stable_s=0.0)
        self.b.discover(wait=30)
        ds = wait_ready(self.b, sid)
        self.assertEqual(ds.status, 'ready', ds.error)
        return sid, ds

    def start_routine(self, b):
        slow = SlowVerify(b.storage)
        b.storage = slow
        b.discover(deep=True)  # returns immediately; the routine pass blocks inside verify
        self.assertTrue(slow.entered.wait(10), 'routine verification did not start')
        return slow

    def test_deep_refresh_keeps_version_serving(self):
        sid, ds = self.ready_snapshot()
        version, verified_at = ds.version, ds.verified_at
        slow = self.start_routine(self.b)
        try:
            self.assertEqual(ds.status, 'ready')
            self.assertTrue(ds.reverifying)
            self.assertIs(self.b.get(sid, version), ds)  # no 409 during the routine pass
            from server.explore import Explorer
            r = Explorer(self.b).query({'dataset': sid, 'version': version, 'pattern': ['A', 'B', 'C']})
            self.assertEqual(r['count'], 6)
            self.assertEqual(r['dataset']['version'], version)
            pub = next(d for d in self.b.listing()['datasets'] if d['key'] == sid)
            self.assertEqual((pub['status'], pub['reverifying']), ('ready', True))
        finally:
            slow.release.set()
        self.assertTrue(wait_until(lambda: not ds.verifying))
        self.assertEqual((ds.status, ds.version, ds.reverifying, ds.stale), ('ready', version, False, None))
        self.assertGreater(ds.verified_at, verified_at)

    def test_demos_stay_hidden_during_routine_pass(self):
        sid, ds = self.ready_snapshot(show_demos='auto')
        self.assertFalse(self.b.listing()['demos_visible'])
        slow = self.start_routine(self.b)
        try:
            self.assertFalse(self.b.listing()['demos_visible'])
            self.assertEqual([d['key'] for d in self.b.listing()['datasets']], [sid])
        finally:
            slow.release.set()

    def test_routine_failure_is_explicit_stale_state(self):
        sid, ds = self.ready_snapshot()
        version = ds.version
        slow = self.start_routine(self.b)
        slow.fail = ValueError('integrity failure (injected)')
        slow.release.set()
        self.assertTrue(wait_until(lambda: not ds.verifying))
        self.assertEqual(ds.status, 'failed')
        self.assertIsNone(ds.version)
        self.assertEqual(ds.stale['version'], version)
        self.assertIn('re-verification of the served version failed', ds.stale['reason'])
        from server.bridge import VersionChanged
        with self.assertRaises(VersionChanged):
            self.b.get(sid, version)
        pub = next(d for d in self.b.listing()['datasets'] if d['key'] == sid)
        self.assertEqual(pub['stale']['version'], version)

    def test_invalidation_during_routine_pass_is_not_overwritten(self):
        sid, ds = self.ready_snapshot()
        version = ds.version
        slow = self.start_routine(self.b)
        target = sorted((ds.path / 'parquet').glob('*.parquet'))[0]
        writable(ds.path / 'parquet')
        writable(target)
        target.write_bytes(target.read_bytes() + b'\0')  # changed while the routine pass is in flight
        from server.bridge import VersionChanged
        from server.explore import Explorer
        with self.assertRaises(VersionChanged):
            Explorer(self.b).query({'dataset': sid, 'version': version, 'pattern': ['A', 'B']})
        self.assertEqual(ds.status, 'settling')
        slow.release.set()  # the routine pass now "passes" on stale reads; it must not resurrect the old version
        self.assertTrue(wait_until(lambda: not ds.verifying))
        self.assertNotEqual(ds.status, 'ready')
        self.assertIsNone(ds.version)
        self.assertEqual(ds.stale['version'], version)
        with self.assertRaises(VersionChanged):
            self.b.get(sid, version)


def wait_until(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class LedgerFormatTest(Roots, unittest.TestCase):
    """Review N-2: ledger format 2 is refused under the d6049e2 pin with a typed reason; storage.verified is used when present."""

    def entries(self, sha='a' * 64):
        return [{'ledger_format': 2, 'event': 'reserve', 'snapshot_id': 'snapshot:' + '1' * 32, 'manifest_sha256': sha},
                {'ledger_format': 2, 'event': 'commit', 'snapshot_id': 'snapshot:' + '1' * 32, 'manifest_sha256': sha}]

    def test_format2_publication_messages(self):
        from server.bridge import publication_of
        m = {'manifest_sha256': 'a' * 64}
        p = publication_of(self.entries(), m)
        self.assertEqual(p['status'], 'unsupported-ledger-format')
        self.assertFalse(p['servable'])
        self.assertIn('reserve → commit', p['note'])
        self.assertIn('not an integrity failure', p['note'])
        self.assertNotIn('differs', p['note'])
        p = publication_of(self.entries()[:1], m)  # reserve without commit: still a format gap, never served
        self.assertEqual(p['status'], 'unsupported-ledger-format')
        self.assertFalse(p['servable'])
        p = publication_of([{'manifest_sha256': 'b' * 64}], m)
        self.assertEqual(p['status'], 'ledger-mismatch')
        self.assertIn('bbbbbbbb', p['note'])
        self.assertEqual(publication_of([{'manifest_sha256': 'a' * 64}] * 2, m)['status'], 'ledger-conflict')
        self.assertEqual(publication_of([{'manifest_sha256': 'a' * 64}], m, corrupt=True)['status'], 'ledger-corrupt')

    def test_read_ledger_corrupt_and_torn_tail(self):
        from server.bridge import LEDGER_CORRUPT, read_ledger
        d = self.scratch / 'ledger'
        d.mkdir()
        good = json.dumps({'snapshot_id': 'snapshot:' + '2' * 32, 'manifest_sha256': 'c' * 64})
        (d / '.publication-ledger.jsonl').write_text(good + '\n{"snapshot_id": "snapshot:3')  # unterminated append in progress
        led = read_ledger(d)
        self.assertNotIn(LEDGER_CORRUPT, led)
        self.assertEqual(len(led['snapshot:' + '2' * 32]), 1)
        (d / '.publication-ledger.jsonl').write_text(good + '\nnot json\n' + good + '\n')
        self.assertIn(LEDGER_CORRUPT, read_ledger(d))

    def test_format2_snapshot_is_typed_incompatible_under_old_pin(self):
        sid = self.import_fixture()
        ledger = self.research / 'snapshots' / '.publication-ledger.jsonl'
        sha = json.loads((self.research / 'snapshots' / sid / 'manifest.json').read_text())['manifest_sha256']
        writable(ledger)
        ledger.write_text(''.join(json.dumps(dict(e, snapshot_id=sid)) + '\n' for e in self.entries(sha)))
        b = self.backend()
        if b.trusted_read:
            self.skipTest('backend provides storage.verified; format 2 is the backend\'s to judge')
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'incompatible')
        self.assertEqual(ds.publication['status'], 'unsupported-ledger-format')
        self.assertIn('d6049e2', ds.error)
        self.assertEqual(b.info['publication_read'].split(' ')[1], 'storage.verify')

    def test_trusted_read_path_when_backend_provides_it(self):
        sid = self.import_fixture()
        b = self.backend()
        real = b.storage

        class PublicationError(ValueError):
            def __init__(self, msg, state):
                super().__init__(msg)
                self.state = state

        class Storage:
            mode = 'ok'

            def __getattr__(self, name):
                return getattr(real, name)

            def verified(self, path, sid=None, *, ledger='auto'):
                assert ledger == 'require'
                if self.mode == 'reserved':
                    raise PublicationError(f'{sid} publication is not committed', 'reserved')
                return {'manifest': real.verify(path, sid), 'publication': {'status': 'ledgered', 'ledger_checked': True, 'binding': 'test'}}
        b.storage, b.trusted_read = Storage(), True
        b.discover(wait=30)
        ds = wait_ready(b, sid)
        self.assertEqual(ds.status, 'ready', ds.error)
        self.assertEqual((ds.publication['status'], ds.publication['source']), ('ledgered', 'backend storage.verified'))
        b.storage.mode = 'reserved'
        b.discover(deep=True, wait=30)
        self.assertTrue(wait_until(lambda: not ds.verifying))
        self.assertEqual(ds.status, 'unpublished')
        self.assertEqual(ds.publication['status'], 'reserved')
        self.assertEqual(ds.stale['reason'].split(':')[0], 're-verification of the served version failed')


if __name__ == '__main__':
    unittest.main()
