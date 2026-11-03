"""Read-only bridge to the cuneiform-research backend.

The research repository stays authoritative: this module imports its package
from a configured path, checks versions, and calls its own ingest/projection/
query functions. It never writes to the repository or to canonical snapshots.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

# Versions this UI was built and exercised against.
SUPPORTED = {'schema': {'2'}, 'parser': {'conservative-3'}, 'projection': {'linked-1'}}
TESTED_COMMITS = {'2ab620b3ec133fce4221d63728a887404a207dea'}

SNAPSHOT_RE = re.compile(r'^snapshot:[0-9a-f]{32}$')
DATASET_RE = re.compile(r'^(snapshot:[0-9a-f]{32}|[a-z][a-z0-9-]{1,40})$')
NODE_RE = re.compile(r'^[a-z][a-z-]{1,24}:[0-9a-f]{32}$')
TOKEN_RE = re.compile(r'^[^\s\x00-\x1f\x7f]{1,64}$')

QUERY_TIMEOUT_S = 10.0
LOCK_WAIT_S = 15.0
MAX_VERIFY_BYTES = 2 * 1024**3
MAX_RAW_TRACE_BYTES = 64 * 1024**2


class UserError(ValueError):
    """Safe to show to the client."""


@dataclass
class Dataset:
    key: str
    kind: str  # snapshot | sample | synthetic
    title: str
    description: str
    status: str = 'pending'  # pending | verifying | ready | failed | incompatible
    error: str | None = None
    db: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    raws: dict[str, tuple[str, bytes | Path]] = field(default_factory=dict)  # sha -> (source kind, bytes or path)
    manifest: dict | None = None
    path: Path | None = None
    inputs: list[dict] = field(default_factory=list)
    scopes: list[dict] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    loaded_at: float | None = None

    def public(self) -> dict:
        return {'key': self.key, 'kind': self.kind, 'title': self.title, 'description': self.description,
                'status': self.status, 'error': self.error, 'scopes': self.scopes, 'inputs': self.inputs,
                'examples': self.examples, 'authority': AUTHORITY[self.kind],
                'manifest': _manifest_summary(self.manifest) if self.manifest else None}


AUTHORITY = {
    'snapshot': 'Verified immutable snapshot (manifest + file hashes checked by the backend). Opened read-only.',
    'sample': 'Real retained source sample parsed in memory by the backend. Not a verified snapshot; not a corpus.',
    'synthetic': 'SYNTHETIC engineering fixture. Not philological evidence.',
}


def _manifest_summary(m: dict) -> dict:
    keys = ['snapshot_id', 'schema_version', 'parser_version', 'namespace', 'source', 'source_url', 'record_id',
            'raw_sha256', 'source_bytes', 'rights', 'retrieved_or_imported_at', 'coverage', 'projection_spec', 'counts']
    return {k: m.get(k) for k in keys if k in m}


class Backend:
    def __init__(self, repo: Path, research_root: Path, show_demos: str = 'auto'):
        self.repo = repo.resolve()
        self.research_root = research_root.expanduser().resolve()
        self.show_demos = show_demos
        self.datasets: dict[str, Dataset] = {}
        self.discovery_lock = threading.Lock()
        self.last_discovery: float | None = None
        self.info = self._load()

    # -- backend import and compatibility ---------------------------------
    def _load(self) -> dict:
        if not (self.repo / 'cuneiform' / '__init__.py').is_file():
            raise RuntimeError(f'backend package not found under {self.repo}')
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        import cuneiform
        from cuneiform import adapters, model, projections, query, storage
        if Path(cuneiform.__file__).resolve().parent != self.repo / 'cuneiform':
            raise RuntimeError('imported cuneiform package is not the configured repository')
        self.adapters, self.model, self.projections, self.query, self.storage = adapters, model, projections, query, storage
        required = {'projections': ['build', 'lookup', 'source_span', 'coverage', 'report', 'assert_fresh', 'ProjectionConfig'],
                    'query': ['motif', 'trace_rows', 'array_count'],
                    'storage': ['verify', 'read_bounded', 'validate_graph', 'Config'],
                    'adapters': ['Builder', 'ingest', 'RIGHTS'], 'model': ['identity', 'digest', 'canonical', 'EDGE_TYPES']}
        missing = [f'{mod}.{name}' for mod, names in required.items() for name in names if not hasattr(getattr(self, mod), name)]
        versions = {'schema': model.SCHEMA_VERSION, 'parser': model.PARSER_VERSION, 'projection': projections.REVISION}
        commit, dirty = self._git()
        problems = [f'{k} {v} not in supported {sorted(SUPPORTED[k])}' for k, v in versions.items() if v not in SUPPORTED[k]]
        problems += [f'missing backend API: {m}' for m in missing]
        if problems:
            compat = 'incompatible'
        elif commit in TESTED_COMMITS and not dirty:
            compat = 'tested'
        else:
            compat = 'compatible-untested'
        return {'repo': str(self.repo), 'commit': commit, 'dirty': dirty, 'versions': versions,
                'compatibility': compat, 'problems': problems, 'tested_commits': sorted(TESTED_COMMITS),
                'research_root': str(self.research_root), 'python': sys.version.split()[0],
                'duckdb': __import__('duckdb').__version__}

    def _git(self) -> tuple[str | None, bool | None]:
        try:
            env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
            head = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5, env=env)
            status = subprocess.run(['git', '--no-optional-locks', '-C', str(self.repo), 'status', '--porcelain', '--untracked-files=no'],
                                    capture_output=True, text=True, timeout=5, env=env)
            return (head.stdout.strip() or None), bool(status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return None, None

    def compatible(self) -> bool:
        return self.info['compatibility'] != 'incompatible'

    # -- discovery ----------------------------------------------------------
    def discover(self) -> dict:
        """Find verified snapshots under the research root, plus labeled samples."""
        if not self.compatible():
            return self.listing()
        with self.discovery_lock:
            snapshot_dir = self.research_root / 'snapshots'
            found = []
            if snapshot_dir.is_dir():
                for p in sorted(snapshot_dir.iterdir()):
                    if p.is_dir() and SNAPSHOT_RE.match(p.name) and (p / 'manifest.json').is_file():
                        found.append(p)
            for p in found:
                if p.name not in self.datasets:
                    ds = Dataset(p.name, 'snapshot', p.name, 'Persisted snapshot', path=p)
                    self.datasets[p.name] = ds
                    threading.Thread(target=self._open_snapshot, args=(ds,), daemon=True).start()
            for key, kind, title, description, inputs, examples in self._sample_specs():
                if key not in self.datasets and all(path.is_file() for _, path in inputs):
                    ds = Dataset(key, kind, title, description, examples=examples)
                    self.datasets[key] = ds
                    self._open_memory(ds, inputs)
            self.last_discovery = time.time()
        return self.listing()

    def listing(self) -> dict:
        real = [d for d in self.datasets.values() if d.kind == 'snapshot']
        real_ready = any(d.status == 'ready' for d in real)
        demos_visible = self.show_demos == 'always' or (self.show_demos == 'auto' and not real_ready)
        order = {'snapshot': 0, 'sample': 1, 'synthetic': 2}
        spec_rank = {key: i for i, (key, *_rest) in enumerate(self._sample_specs())}
        shown = [d for d in self.datasets.values() if d.kind == 'snapshot' or demos_visible]
        # Richest first: real snapshots, then samples that carry text, in curated order.
        shown.sort(key=lambda d: (order[d.kind], not d.scopes, spec_rank.get(d.key, 0), d.title))
        return {'datasets': [d.public() for d in shown], 'real_snapshots': len(real), 'real_ready': real_ready,
                'demos_visible': demos_visible, 'demo_policy': self.show_demos,
                'snapshot_dir': str(self.research_root / 'snapshots'),
                'snapshot_dir_exists': (self.research_root / 'snapshots').is_dir(),
                'last_discovery': self.last_discovery}

    def _sample_specs(self):
        ev = self.research_root / 'evidence'
        fx = self.repo / 'fixtures'
        return [
            ('sample-ebl-readme', 'sample', 'eBL frozen README example',
             'One complete fragment record embedded in the Zenodo 10018951 README (rights unresolved).',
             [('ebl', ev / 'ebl-frozen-example.json')],
             [{'label': 'ABZ215 ABZ13 — signs field', 'pattern': ['ABZ215', 'ABZ13'], 'field': 'signs', 'policy': 'source', 'engine': 'index', 'expect': 3},
              {'label': 'DIŠ NA — atf transliteration', 'pattern': ['DIŠ', 'NA'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 3},
              {'label': 'ina ? — reading then any position', 'pattern': ['ina', '?'], 'field': 'atf', 'policy': 'source', 'engine': 'index'},
              {'label': 'ABZ215 ? ABZ13 — wildcard', 'pattern': ['ABZ215', '?', 'ABZ13'], 'field': 'signs', 'policy': 'source', 'engine': 'index'},
              {'label': 'ABZ215 ABZ13 — strict (eBL asserts no certainty)', 'pattern': ['ABZ215', 'ABZ13'], 'field': 'signs', 'policy': 'strict', 'engine': 'index', 'expect': 0}]),
            ('synthetic-fixtures', 'synthetic', 'Synthetic fixtures (eBL + CDLI)',
             'Backend test fixtures: uncertainty, gaps, alternatives, compounds, duplicate aliases.',
             [('ebl', fx / 'ebl-synthetic.json'), ('cdli', fx / 'cdli-synthetic.csv')],
             [{'label': 'A B C — exact motif', 'pattern': ['A', 'B', 'C'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 6},
              {'label': 'A ? C — one represented position', 'pattern': ['A', '?', 'C'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 11},
              {'label': 'A C — adjacent (expect none)', 'pattern': ['A', 'C'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 0},
              {'label': 'A B C — strict physical evidence', 'pattern': ['A', 'B', 'C'], 'field': 'atf', 'policy': 'strict', 'engine': 'index', 'expect': 0},
              {'label': 'A B … C — up to 2 skipped positions', 'pattern': ['A', 'B', 'C'], 'field': 'atf', 'policy': 'source', 'engine': 'graph', 'max_skip': 2},
              {'label': 'ABZ1 ABZ2 — source-derived signs', 'pattern': ['ABZ1', 'ABZ2'], 'field': 'signs', 'policy': 'source', 'engine': 'index'},
              {'label': 'A — single-reading postings', 'pattern': ['A'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 12}]),
            ('sample-ebl-k1', 'sample', 'eBL K.1 live metadata (no text)',
             'Bounded live API response for K.1: artifact metadata and CDLI alias only; no transliteration supplied.',
             [('ebl', ev / 'ebl-one')],
             [{'label': 'A B — expect no text layer', 'pattern': ['A', 'B'], 'field': 'atf', 'policy': 'source', 'engine': 'index', 'expect': 0}]),
            ('sample-cdli-three', 'sample', 'CDLI catalog — first three rows',
             'Three historical CDLI catalog rows: artifacts and museum-label aliases; no text layers.',
             [('cdli', ev / 'cdli-three.csv')], []),
        ]

    def _open_memory(self, ds: Dataset, inputs: list[tuple[str, Path]]) -> None:
        import duckdb
        st, mdl = self.storage, self.model
        try:
            db = duckdb.connect(':memory:')
            db.execute("SET memory_limit='128MB'")
            db.execute('SET threads=1')
            db.execute("SET max_temp_directory_size='0B'")
            db.execute(Path(self.projections.__file__).with_name('schema.sql').read_text())
            cfg = st.Config(self.research_root)
            receipts = self._receipts()
            for source, path in inputs:
                raw = st.read_bounded(path, cfg)
                sha = mdl.digest(raw)
                sid = mdl.identity('demo', source, sha)
                self.adapters.ingest(self.adapters.Builder(db, sid, source), source, raw)
                ds.raws[sha] = (source, raw)
                ds.inputs.append({'source': source, 'file': path.name, 'bytes': len(raw), 'sha256': sha,
                                  'snapshot_id': sid, 'receipt': receipts.get(sha, receipts.get(path.name, 'no download receipt (fixture or derived sample)'))})
            st.validate_graph(db)
            self.projections.build(db)
            ds.db = db
            self._finish(ds)
        except Exception as exc:  # report, never crash discovery
            ds.status, ds.error = 'failed', f'{type(exc).__name__}: {exc}'

    def _receipts(self) -> dict:
        p = self.research_root / 'evidence' / 'source-receipts.json'
        out: dict[str, str] = {}
        try:
            doc = json.loads(p.read_text())
            for a in doc.get('assets', []):
                out[a['sha256']] = f"download receipt: {a['url']} ({a['scope']})"
            for name, note in doc.get('derived_samples', {}).items():
                out[name] = 'derived sample: ' + note
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return out

    def _open_snapshot(self, ds: Dataset) -> None:
        import duckdb
        assert ds.path is not None
        try:
            ds.status = 'verifying'
            pre = json.loads((ds.path / 'manifest.json').read_text())
            ds.title = f"{pre.get('namespace', '?')} · {pre.get('source', '?')} · {ds.key[9:21]}"
            ds.description = pre.get('source_url') or 'persisted snapshot'
            schema, parser = pre.get('schema_version'), pre.get('parser_version')
            rev = (pre.get('projection_spec') or {}).get('revision')
            if schema not in SUPPORTED['schema'] or parser not in SUPPORTED['parser'] or rev not in SUPPORTED['projection']:
                ds.status = 'incompatible'
                ds.error = f'schema {schema} / parser {parser} / projection {rev}: not supported by this UI build'
                return
            total = sum(p.stat().st_size for p in ds.path.rglob('*') if p.is_file())
            if total > MAX_VERIFY_BYTES:
                raise UserError(f'snapshot is {total} bytes; UI verification cap is {MAX_VERIFY_BYTES}')
            manifest = self.storage.verify(ds.path, ds.key)  # backend hashes every payload file
            db = duckdb.connect(str(ds.path / 'graph.duckdb'), read_only=True)
            db.execute("SET memory_limit='256MB'")
            db.execute('SET threads=2')
            self.projections.assert_fresh(db)
            ds.manifest, ds.db = manifest, db
            ds.raws[manifest['raw_sha256']] = (manifest['source'], ds.path / 'raw')
            ds.inputs = [{'source': manifest['source'], 'file': 'raw', 'bytes': manifest['source_bytes'],
                          'sha256': manifest['raw_sha256'], 'snapshot_id': ds.key, 'receipt': manifest.get('source_url') or 'none'}]
            self._finish(ds)
        except Exception as exc:
            ds.status, ds.error = 'failed', f'{type(exc).__name__}: {exc}'

    def _finish(self, ds: Dataset) -> None:
        ds.scopes = self.projections.rows(ds.db, 'SELECT DISTINCT namespace,snapshot_id,field FROM ordered_position ORDER BY 1,2,3')
        if not ds.examples:
            ds.examples = self._auto_examples(ds)
        ds.loaded_at = time.time()
        ds.status = 'ready'

    def _auto_examples(self, ds: Dataset) -> list[dict]:
        """For real snapshots: most frequent indexed bigrams as starting points."""
        out = []
        for scope in ds.scopes[:2]:
            try:
                top = self.projections.rows(ds.db, "SELECT readings, count(*) n FROM ngram WHERE namespace=? AND snapshot_id=? AND field=? AND policy='source' AND length=2 AND json_extract_string(readings,'$[0]') IS NOT NULL AND json_extract_string(readings,'$[1]') IS NOT NULL GROUP BY readings ORDER BY n DESC, readings LIMIT 3",
                                           [scope['namespace'], scope['snapshot_id'], scope['field']])
            except Exception:
                continue
            for t in top:
                pattern = json.loads(t['readings'])
                out.append({'label': f"{' '.join(pattern)} — frequent {scope['field']} bigram", 'pattern': pattern,
                            'field': scope['field'], 'policy': 'source', 'engine': 'index', 'expect': t['n']})
        return out

    # -- access -------------------------------------------------------------
    def get(self, key: str) -> Dataset:
        if not isinstance(key, str) or not DATASET_RE.match(key):
            raise UserError('invalid dataset key')
        ds = self.datasets.get(key)
        if ds is None:
            raise UserError('unknown dataset; refresh discovery')
        if ds.status != 'ready':
            raise UserError(f'dataset not ready: {ds.status}' + (f' ({ds.error})' if ds.error else ''))
        return ds

    def run(self, ds: Dataset, fn: Callable[[Any], Any], timeout: float = QUERY_TIMEOUT_S):
        """Serialize access per dataset and interrupt DuckDB work past the time bound."""
        if not ds.lock.acquire(timeout=LOCK_WAIT_S):
            raise UserError('dataset busy; try again')
        timer = threading.Timer(timeout, ds.db.interrupt)
        timer.start()
        try:
            return fn(ds.db)
        except Exception as exc:
            if 'interrupt' in str(exc).lower():
                raise UserError(f'query exceeded {timeout:.0f}s bound and was interrupted') from exc
            raise
        finally:
            timer.cancel()
            ds.lock.release()

    def close(self) -> None:
        for ds in self.datasets.values():
            if ds.db is not None:
                try:
                    ds.db.close()
                except Exception:
                    pass
