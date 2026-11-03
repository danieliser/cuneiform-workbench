"""Persisted-snapshot path: backend imports a synthetic fixture into a scratch root owned by this UI,
then the bridge discovers, verifies, opens read-only, queries and traces it. Never touches the corpus root."""
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCRATCH = Path(os.environ.get('CUNEIFORM_UI_HOME', str(Path.home() / '.local/share/cuneiform-ui'))) / 'test-scratch-root'


class SnapshotPathTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        SCRATCH.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)

    def test_discover_verify_query_trace(self):
        from server.bridge import Backend
        from server.explore import Explorer
        repo = Path(os.environ.get('CUNEIFORM_SOURCE_REPO', str(Path.home() / 'Projects/Fun/cuneiform-research')))
        b = Backend(repo, SCRATCH, show_demos='never')
        try:
            from cuneiform.storage import Config, import_source
            out = import_source('ebl', repo / 'fixtures/ebl-synthetic.json', Config(SCRATCH), source_url='synthetic:fixture')
        except ValueError as exc:
            self.skipTest(f'backend refused scratch import: {exc}')
        sid = out['snapshot']
        listing = b.discover()
        self.assertEqual([d['key'] for d in listing['datasets']], [sid])
        for _ in range(100):
            if b.datasets[sid].status not in ('pending', 'verifying'):
                break
            time.sleep(0.1)
        ds = b.datasets[sid]
        self.assertEqual(ds.status, 'ready', ds.error)
        e = Explorer(b)
        r = e.query({'dataset': sid, 'pattern': ['A', 'B', 'C']})
        self.assertEqual(r['count'], 6)
        self.assertTrue(r['parity']['ok'])
        occ = e.occurrence(sid, r['rows'][0]['occurrence_ids'][0])
        self.assertIsNone(occ['trace_error'])
        self.assertEqual(occ['trace']['rights_attribution'], 'verified snapshot manifest; not an authenticated license grant')
        self.assertTrue(ds.examples, 'real snapshots get auto examples from frequent bigrams')
        b.close()


if __name__ == '__main__':
    unittest.main()
