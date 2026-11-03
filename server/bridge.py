"""Read-only bridge to the cuneiform-research backend.

The research repository stays authoritative: this module imports its package
from a configured path, checks versions, and calls its own ingest/projection/
query functions. It never writes to the repository or to canonical snapshots.

Snapshot lifecycle (canonical snapshots are treated as read-only but possibly
still changing until the corpus owner confirms publication):

* Each snapshot directory gets a stat fingerprint (size, mtime, inode, ctime of
  every file). A snapshot is verified with the backend's ``storage.verify`` only
  after the directory has been quiet for ``stable_s`` seconds.
* A verified snapshot's *version* is its ``manifest_sha256``. Every response is
  bound to that version; a client that sends an older version gets HTTP 409.
* No DuckDB connection is kept open. Each request opens the file read-only,
  checks the fingerprint before and after, and closes it. A fingerprint change
  invalidates the version and schedules a fresh verification.
* Failed snapshots are retried with backoff; removed ones are dropped.
"""
from __future__ import annotations

import contextlib
import hashlib
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
# d6049e2: persisted corpus pilot with manifest-3 + publication ledger (pinned for corpus integration).
TESTED_COMMITS = {'d6049e21b05dcc9c96b03cb7ca722cea921031a4'}

SNAPSHOT_RE = re.compile(r'^snapshot:[0-9a-f]{32}$')
DATASET_RE = re.compile(r'^(snapshot:[0-9a-f]{32}|[a-z][a-z0-9-]{1,40})$')
VERSION_RE = re.compile(r'^[0-9a-f]{16,64}$')
NODE_RE = re.compile(r'^[a-z][a-z-]{1,24}:[0-9a-f]{32}$')
TOKEN_RE = re.compile(r'^[^\s\x00-\x1f\x7f]{1,64}$')

QUERY_TIMEOUT_S = 10.0
LOCK_WAIT_S = 15.0
MAX_VERIFY_BYTES = 2 * 1024**3
MAX_RAW_TRACE_BYTES = 64 * 1024**2
STABLE_S = float(os.environ.get('CUNEIFORM_UI_STABLE_S', '30'))  # quiet period before a snapshot is verified
REVERIFY_S = 900.0  # full re-hash of ready snapshots at most this often (stat fingerprint is checked per request)
SELECTION_SOURCES = {'oracc-selection', 'cdli-selection'}
LEDGER = '.publication-ledger.jsonl'


class UserError(ValueError):
    """Safe to show to the client."""


class VersionChanged(UserError):
    """The dataset version the client holds is no longer the served one (HTTP 409)."""


@dataclass
class Dataset:
    key: str
    kind: str  # snapshot | sample | synthetic
    title: str
    description: str
    status: str = 'pending'  # pending | settling | verifying | ready | failed | incompatible | unpublished
    error: str | None = None
    db: Any = None  # in-memory datasets only; snapshots open per request
    lock: threading.Lock = field(default_factory=threading.Lock)
    raws: dict[str, tuple[str, bytes | Path]] = field(default_factory=dict)  # sha -> (source kind, bytes or path)
    manifest: dict | None = None
    path: Path | None = None
    inputs: list[dict] = field(default_factory=list)
    scopes: list[dict] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    selection: dict = field(default_factory=dict)  # what part of which source this dataset covers
    loaded_at: float | None = None
    version: str | None = None
    fingerprint: dict | None = None
    verified_at: float | None = None
    checked_at: float | None = None
    verifying: bool = False
    attempts: int = 0
    next_retry: float = 0.0
    builtin_examples: bool = False
    publication: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {'key': self.key, 'kind': self.kind, 'title': self.title, 'description': self.description,
                'status': self.status, 'error': self.error, 'scopes': self.scopes, 'inputs': self.inputs,
                'examples': self.examples, 'authority': AUTHORITY[self.kind], 'version': self.version,
                'selection': self.selection, 'verified_at': self.verified_at, 'checked_at': self.checked_at,
                'publication': self.publication or {'status': 'not-a-snapshot', 'note': PUBLICATION[self.kind]},
                'manifest': _manifest_summary(self.manifest) if self.manifest else None}

    def binding(self) -> dict:
        """Identity every response carries, so a result can never outlive its dataset version."""
        return {'key': self.key, 'kind': self.kind, 'version': self.version, 'verified_at': self.verified_at,
                'selection_label': self.selection.get('label'), 'scope_note': self.selection.get('note'),
                'publication': (self.publication or {}).get('status')}


AUTHORITY = {
    'snapshot': 'Backend-verified snapshot (manifest + every file hash checked by storage.verify). Opened read-only per request.',
    'sample': 'Real retained source sample parsed in memory by the backend. Not a verified snapshot; not a corpus.',
    'synthetic': 'SYNTHETIC engineering fixture. Not philological evidence.',
}
PUBLICATION = {
    'snapshot': 'see publication ledger status',
    'sample': 'not a snapshot',
    'synthetic': 'not a snapshot',
}


def _manifest_summary(m: dict) -> dict:
    keys = ['snapshot_id', 'schema_version', 'parser_version', 'namespace', 'source', 'source_url', 'record_id',
            'raw_sha256', 'source_bytes', 'rights', 'retrieved_or_imported_at', 'coverage', 'projection_spec', 'counts',
            'cohort', 'selection', 'ingestion', 'adapter', 'source_locator_contract', 'manifest_sha256']
    return {k: m.get(k) for k in keys if k in m}


def _fingerprint(path: Path) -> dict:
    """Stat fingerprint of every file under a snapshot directory (no reads)."""
    out = {}
    for p in sorted(path.rglob('*')):
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_file() or p.is_symlink():
            out[str(p.relative_to(path))] = [st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns]
    return out


def _newest(fp: dict) -> float:
    return max((v[1] for k, v in fp.items() if not k.startswith('@')), default=0) / 1e9


def selection_of(persisted, manifest: dict, path: Path) -> dict:
    """Population the counts describe, from the backend's own ``persisted.scope`` (never re-derived here)."""
    sc = persisted.scope(path, manifest)
    kind = {'rule-selected cohort': 'cohort', 'explicit selection': 'selection', 'single input': 'single-input'}.get(sc.get('kind'), sc.get('kind'))
    return dict(sc, kind=kind, backend_kind=sc.get('kind'), note=sc.get('scope_note'),
                size=sc.get('cohort_size', sc.get('selected')), rule_name=(sc.get('rule') or {}).get('name') or sc.get('selection_rule'))


def read_ledger(snapshot_dir: Path) -> dict[str, list[dict]]:
    """Backend publication ledger (append-only; read here, never written). snapshot_id -> entries."""
    out: dict[str, list[dict]] = {}
    try:
        for line in (snapshot_dir / LEDGER).read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                out.setdefault(e.get('snapshot_id'), []).append(e)
    except (OSError, ValueError):
        pass
    return out


def publication_of(entries: list[dict] | None, manifest: dict) -> dict:
    """Separate trusted immutability (ledgered once, read-only) from the legacy same-ID rebuild incident."""
    if not entries:
        return {'status': 'not-in-ledger', 'servable': False,
                'note': 'not listed in the backend publication ledger; not served until the corpus owner publishes it'}
    if len(entries) != 1 or entries[0].get('manifest_sha256') != manifest.get('manifest_sha256'):
        return {'status': 'ledger-mismatch', 'servable': False,
                'note': 'manifest SHA-256 differs from the publication ledger (or the ID was published more than once); refused'}
    e = entries[0]
    contract = manifest.get('snapshot_contract')
    base = {'published_at': e.get('published_at'), 'backfilled': bool(e.get('backfilled')), 'snapshot_contract': contract,
            'ledger_manifest_sha256': e.get('manifest_sha256'), 'servable': True}
    if e.get('incident'):
        return dict(base, status='legacy-incident',
                    note=('Ledgered after the fact. This ID was deleted and rebuilt under the same ID before the ledger existed; '
                          'first-version bytes were not retained. Current bytes verify, but results produced from this ID before '
                          'the rebuild cannot be reproduced. Incident: ' + str(e.get('incident'))))
    if e.get('backfilled'):
        return dict(base, status='ledgered-legacy',
                    note='Published once before the ledger existed (backfilled, legacy pre-manifest-3 identity); read-only since.')
    return dict(base, status='ledgered', note=f'Published once through the ledger ({contract or "contract unrecorded"}); read-only and never republishable.')


class Backend:
    def __init__(self, repo: Path, research_root: Path, show_demos: str = 'auto', home: Path | None = None,
                 stable_s: float = STABLE_S):
        self.repo = repo.resolve()
        self.research_root = research_root.expanduser().resolve()
        self.show_demos = show_demos
        self.home = (home or Path(os.environ.get('CUNEIFORM_UI_HOME', str(Path.home() / '.local/share/cuneiform-ui')))).expanduser()
        self.tmp = self.home / 'duckdb-tmp'
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.stable_s = stable_s
        self.datasets: dict[str, Dataset] = {}
        self.discovery_lock = threading.Lock()
        self.last_discovery: float | None = None
        self.ledger: dict[str, list[dict]] = {}
        self.info = self._load()

    # -- backend import and compatibility ---------------------------------
    def _load(self) -> dict:
        if not (self.repo / 'cuneiform' / '__init__.py').is_file():
            raise RuntimeError(f'backend package not found under {self.repo}')
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        import cuneiform
        from cuneiform import adapters, model, projections, query, storage
        try:
            from cuneiform import oracc, persisted
        except ImportError:
            oracc = persisted = None
        self.oracc, self.persisted = oracc, persisted
        if Path(cuneiform.__file__).resolve().parent != self.repo / 'cuneiform':
            raise RuntimeError('imported cuneiform package is not the configured repository')
        self.adapters, self.model, self.projections, self.query, self.storage = adapters, model, projections, query, storage
        required = {'projections': ['build', 'lookup', 'source_span', 'coverage', 'report', 'assert_fresh', 'ProjectionConfig'],
                    'query': ['motif', 'trace_rows', 'array_count', 'trace'],
                    'storage': ['verify', 'read_bounded', 'validate_graph', 'Config'],
                    'adapters': ['Builder', 'ingest', 'RIGHTS'], 'model': ['identity', 'digest', 'canonical', 'EDGE_TYPES'],
                    'persisted': ['scope'], 'oracc': ['trace_word']}
        missing = [f'{mod}.{name}' for mod, names in required.items() for name in names
                   if getattr(self, mod) is None or not hasattr(getattr(self, mod), name)]
        versions = {'schema': model.SCHEMA_VERSION, 'parser': model.PARSER_VERSION, 'projection': projections.REVISION}
        commit, dirty, dirty_files = self._git()
        problems = [f'{k} {v} not in supported {sorted(SUPPORTED[k])}' for k, v in versions.items() if v not in SUPPORTED[k]]
        problems += [f'missing backend API: {m}' for m in missing]
        if problems:
            compat = 'incompatible'
        elif dirty is False and getattr(self, 'code_equivalent', None):
            compat = 'tested'
        else:
            compat = 'compatible-untested'
        return {'repo': str(self.repo), 'pinned_export': (self.repo / self.PIN_FILE).is_file(), 'commit': commit, 'dirty': dirty, 'dirty_files': dirty_files, 'versions': versions,
                'code_equivalent_to': getattr(self, 'code_equivalent', None), 'code_trees': getattr(self, 'code_trees', None),
                'code_changed_since_start': False,
                'compatibility': compat, 'problems': problems, 'tested_commits': sorted(TESTED_COMMITS),
                'research_root': str(self.research_root), 'python': sys.version.split()[0],
                'duckdb': __import__('duckdb').__version__,
                'frozen_fast_path': hasattr(projections, 'mark_frozen_verified')}

    CODE_PATHS = ('cuneiform', 'fixtures', 'pyproject.toml')

    PIN_FILE = '.cuneiform-ui-pin.json'

    def _pin(self) -> tuple[str | None, bool | None, int | None]:
        """A read-only `git archive` export of one backend commit (bin/cuneiform-ui pin-backend).

        Every exported file's SHA-256 is recorded; any changed, missing or extra file makes the pin dirty.
        """
        meta = json.loads((self.repo / self.PIN_FILE).read_text())
        want = meta['files']
        have = {}
        for path in sorted(self.repo.rglob('*')):
            rel = str(path.relative_to(self.repo))
            if path.is_file() and rel != self.PIN_FILE and '__pycache__' not in path.parts:
                have[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        changed = len({k for k in set(want) | set(have) if want.get(k) != have.get(k)})
        commit = meta['commit']
        self.code_equivalent = commit if commit in TESTED_COMMITS and not changed else None
        self.code_trees = {'pinned_export': meta.get('tree_digest'), 'files': len(have), 'source': meta.get('source_repo')}
        return commit, bool(changed) or self.code_equivalent is None, changed

    def _git(self) -> tuple[str | None, bool | None, int | None]:
        if (self.repo / self.PIN_FILE).is_file():
            try:
                return self._pin()
            except (OSError, ValueError, KeyError):
                return None, None, None
        """HEAD, whether the *code* differs from a tested commit, and how many code paths are changed.

        The pin is by code tree: HEAD counts as tested when the git trees of cuneiform/, fixtures/ and
        pyproject.toml equal a tested commit's and nothing under those paths is modified or untracked.
        Documentation/queue commits on top of a tested commit therefore stay tested.
        """
        try:
            env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')

            def git(*args):
                r = subprocess.run(['git', '--no-optional-locks', '-C', str(self.repo), *args], capture_output=True, text=True, timeout=5, env=env)
                return r.stdout.strip() if r.returncode == 0 else None
            head = git('rev-parse', 'HEAD')
            status = git('status', '--porcelain', '--untracked-files=all', '--', *self.CODE_PATHS)
            if head is None or status is None:
                return head, None, None
            changed = len([ln for ln in status.splitlines() if ln.strip()])
            trees = [git('rev-parse', f'HEAD:{p}') for p in self.CODE_PATHS]
            self.code_equivalent = None
            for tested in sorted(TESTED_COMMITS):
                if [git('rev-parse', f'{tested}:{p}') for p in self.CODE_PATHS] == trees and None not in trees:
                    self.code_equivalent = tested
            self.code_trees = dict(zip(self.CODE_PATHS, trees))
            return head, bool(changed) or self.code_equivalent is None, changed
        except (OSError, subprocess.SubprocessError):
            return None, None, None

    def recheck_code(self) -> None:
        """Called on every scan: flag backend code that changed after this process imported it."""
        before = getattr(self, 'code_trees', None)
        _head, dirty, changed = self._git()
        if before is not None and (self.code_trees != before or (changed or 0) != (self.info.get('dirty_files') or 0)):
            self.info['code_changed_since_start'] = True
            loaded = (self.info.get('code_equivalent_to') or self.info.get('commit') or '?')[:7]
            self.info['problems_runtime'] = [
                f'Backend code on disk changed after this UI process imported it ({changed} changed code paths now). '
                f'This process keeps using the code it loaded at start (equivalent to {loaded}); restarting would load the '
                'new code and report it as compatible-untested.']

    def compatible(self) -> bool:
        return self.info['compatibility'] != 'incompatible'

    # -- discovery ----------------------------------------------------------
    def discover(self, deep: bool = False, wait: float = 0.0) -> dict:
        """Scan for snapshots and samples. Cheap (stat only) unless something changed or ``deep`` is set.

        ``wait`` blocks up to that many seconds for verifications started by this scan (tests, CLI).
        """
        if not self.compatible():
            return self.listing()
        started: list[threading.Thread] = []
        self.recheck_code()
        with self.discovery_lock:
            now = time.time()
            snapshot_dir = self.research_root / 'snapshots'
            present = set()
            self.ledger = read_ledger(snapshot_dir)
            if snapshot_dir.is_dir():
                for p in sorted(snapshot_dir.iterdir()):
                    if p.is_dir() and SNAPSHOT_RE.match(p.name) and (p / 'manifest.json').is_file():
                        present.add(p.name)
                        t = self._consider(p, now, deep)
                        if t:
                            started.append(t)
            for key in [k for k, d in self.datasets.items() if d.kind == 'snapshot' and k not in present]:
                self.datasets.pop(key)  # removed from disk: forget it (no connection is held)
            for key, kind, title, description, inputs, examples in self._sample_specs():
                if key not in self.datasets and all(path.is_file() for _, path in inputs):
                    ds = Dataset(key, kind, title, description, examples=examples, builtin_examples=bool(examples))
                    self.datasets[key] = ds
                    self._open_memory(ds, inputs)
            self.last_discovery = now
        deadline = time.time() + wait
        for t in started:
            t.join(max(0.0, deadline - time.time()))
        return self.listing()

    def _consider(self, path: Path, now: float, deep: bool) -> threading.Thread | None:
        key = path.name
        ds = self.datasets.get(key)
        if ds is None:
            ds = Dataset(key, 'snapshot', key, 'Persisted snapshot (not yet verified)', path=path)
            self.datasets[key] = ds
        if ds.verifying:
            return None
        fp = _fingerprint(path)
        fp['@ledger'] = self.ledger.get(key)  # a ledger change re-evaluates publication status
        changed = fp != ds.fingerprint
        due = (ds.status in ('pending', 'settling')
               or changed
               or (ds.status == 'failed' and now >= ds.next_retry)
               or (ds.status == 'ready' and (deep or now - (ds.verified_at or 0) > REVERIFY_S)))
        if not due:
            return None
        if changed and ds.status == 'ready':
            self._invalidate(ds, 'snapshot files changed since verification; waiting for them to settle')
        if now - _newest(fp) < self.stable_s:
            ds.status, ds.version = 'settling', None
            ds.error = f'files changed {now - _newest(fp):.0f}s ago; verifying after {self.stable_s:.0f}s without changes'
            ds.fingerprint = fp
            return None
        ds.verifying = True
        ds.status = 'verifying'
        t = threading.Thread(target=self._verify_snapshot, args=(ds, fp), daemon=True)
        t.start()
        return t

    @staticmethod
    def _invalidate(ds: Dataset, why: str) -> None:
        ds.status, ds.version, ds.error = 'settling', None, why

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
                'last_discovery': self.last_discovery, 'stable_s': self.stable_s, 'now': time.time()}

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

    def _configure(self, db, memory: str, threads: int) -> None:
        db.execute(f"SET memory_limit='{memory}'")
        db.execute(f'SET threads={threads}')
        # Spill is disabled (0B), but DuckDB still creates its temp directory; keep it out of the repo (review F-19).
        db.execute("SET temp_directory='" + str(self.tmp).replace("'", "''") + "'")
        db.execute("SET max_temp_directory_size='0B'")

    def _open_memory(self, ds: Dataset, inputs: list[tuple[str, Path]]) -> None:
        import duckdb
        st, mdl = self.storage, self.model
        try:
            db = duckdb.connect(':memory:')
            # 512 MB: the 14-way nodes view needed more than 128 MB on the README sample (review F-6).
            self._configure(db, '512MB', 1)
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
                                  'snapshot_id': sid, 'source_url': None,
                                  'receipt': receipts.get(sha, receipts.get(path.name, 'no download receipt (fixture or derived sample)'))})
            st.validate_graph(db)
            self.projections.build(db)
            ds.db = db
            ds.version = hashlib.sha256(json.dumps([ds.kind, sorted(i['sha256'] for i in ds.inputs), self.info['versions']],
                                                   sort_keys=True).encode()).hexdigest()[:32]
            ds.selection = ({'kind': 'synthetic', 'label': 'SYNTHETIC', 'note': 'Synthetic fixture counts; engineering checks only, not evidence.'}
                            if ds.kind == 'synthetic' else
                            {'kind': 'sample', 'label': 'SAMPLE', 'note': 'Counts are within one small retained sample parsed in memory; not corpus frequencies.'})
            self._finish(ds, db)
            ds.verified_at = ds.checked_at = time.time()
            ds.status = 'ready'
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

    def _verify_snapshot(self, ds: Dataset, fp_before: dict) -> None:
        assert ds.path is not None
        try:
            pre = json.loads((ds.path / 'manifest.json').read_text())
            schema, parser = pre.get('schema_version'), pre.get('parser_version')
            rev = (pre.get('projection_spec') or {}).get('revision')
            if schema not in SUPPORTED['schema'] or parser not in SUPPORTED['parser'] or rev not in SUPPORTED['projection']:
                ds.status, ds.version = 'incompatible', None
                ds.error = f'schema {schema} / parser {parser} / projection {rev}: not supported by this UI build'
                ds.fingerprint = fp_before
                return
            total = sum(v[0] for k, v in fp_before.items() if not k.startswith('@'))
            if total > MAX_VERIFY_BYTES:
                raise UserError(f'snapshot is {total} bytes; UI verification cap is {MAX_VERIFY_BYTES}')
            manifest = self.storage.verify(ds.path, ds.key)  # backend hashes every payload file and checks bindings
            publication = publication_of(fp_before.get('@ledger'), manifest)
            if not publication['servable']:
                ds.status, ds.version, ds.publication = 'unpublished', None, publication
                ds.error = publication['note']
                ds.fingerprint = fp_before
                return
            selection = selection_of(self.persisted, manifest, ds.path)
            db = self._open_snapshot_db(ds.path, manifest)
            try:
                scopes = self.projections.rows(db, 'SELECT DISTINCT namespace,snapshot_id,field FROM ordered_position ORDER BY 1,2,3')
                probe = Dataset(ds.key, 'snapshot', '', '', scopes=scopes)
                examples = self._auto_examples(probe, db)
            finally:
                db.close()
            fp_after = _fingerprint(ds.path)
            fp_after['@ledger'] = fp_before.get('@ledger')
            if fp_after != fp_before:
                self._invalidate(ds, 'snapshot changed while it was being verified; will retry once it settles')
                ds.fingerprint = fp_after
                return
            ds.manifest, ds.selection, ds.scopes, ds.examples = manifest, selection, scopes, examples
            ds.publication = publication
            ds.title = f"{manifest.get('namespace', '?')} · {selection['label']} · {ds.key[9:21]}"
            ds.description = selection['note']
            ds.raws = {manifest['raw_sha256']: (manifest['source'], ds.path / 'raw')}
            parent = selection.get('parent') or {}
            payload_label = {'cohort': 'raw (cohort payload: byte-identical parent slices)',
                             'selection': 'raw (selection payload)'}.get(selection['kind'], 'raw')
            ds.inputs = [{'source': manifest['source'], 'file': payload_label, 'bytes': manifest['source_bytes'],
                          'sha256': manifest['raw_sha256'], 'snapshot_id': ds.key, 'source_url': manifest.get('source_url'),
                          'receipt': 'derived from parent (see next row)' if parent else 'none recorded in manifest'}]
            if parent:
                ds.inputs.append({'source': 'parent', 'file': parent.get('label') or 'parent raw object', 'bytes': parent.get('bytes'),
                                  'sha256': parent.get('sha256'), 'snapshot_id': '—', 'source_url': parent.get('url'),
                                  'receipt': parent.get('receipt_id') or 'none recorded'})
            ds.version = manifest['manifest_sha256']
            ds.fingerprint = fp_after
            ds.verified_at = ds.checked_at = ds.loaded_at = time.time()
            ds.status, ds.error, ds.attempts = 'ready', None, 0
        except Exception as exc:
            ds.attempts += 1
            ds.next_retry = time.time() + min(300.0, 15.0 * 2 ** min(ds.attempts, 5))
            ds.status, ds.version = 'failed', None
            ds.error = f'{type(exc).__name__}: {exc} (retry {ds.attempts} scheduled)'
            ds.fingerprint = fp_before
        finally:
            ds.verifying = False

    def _open_snapshot_db(self, path: Path, manifest: dict):
        import duckdb
        db = duckdb.connect(str(path / 'graph.duckdb'), read_only=True)
        try:
            self._configure(db, '256MB', 2)
            recorded = manifest.get('projection_registry')
            if recorded and hasattr(self.projections, 'mark_frozen_verified'):
                # Valid only because graph.duckdb's hash matched the manifest and the stat fingerprint is unchanged.
                self.projections.mark_frozen_verified(db, recorded)
            self.projections.assert_fresh(db)
        except BaseException:
            db.close()
            raise
        return db

    def _finish(self, ds: Dataset, db) -> None:
        ds.scopes = self.projections.rows(db, 'SELECT DISTINCT namespace,snapshot_id,field FROM ordered_position ORDER BY 1,2,3')
        if not ds.examples:
            ds.examples = self._auto_examples(ds, db)
        ds.loaded_at = time.time()

    def _auto_examples(self, ds: Dataset, db) -> list[dict]:
        """For real snapshots: most frequent indexed bigrams as starting points (counts are within the dataset's selection)."""
        out = []
        for scope in ds.scopes[:2]:
            try:
                top = self.projections.rows(db, "SELECT readings, count(*) n FROM ngram WHERE namespace=? AND snapshot_id=? AND field=? AND policy='source' AND length=2 AND json_extract_string(readings,'$[0]') IS NOT NULL AND json_extract_string(readings,'$[1]') IS NOT NULL GROUP BY readings ORDER BY n DESC, readings LIMIT 3",
                                           [scope['namespace'], scope['snapshot_id'], scope['field']])
            except Exception:
                continue
            for t in top:
                pattern = json.loads(t['readings'])
                out.append({'label': f"{' '.join(pattern)} — frequent {scope['field']} bigram in this selection", 'pattern': pattern,
                            'field': scope['field'], 'policy': 'source', 'engine': 'index', 'expect': t['n']})
        return out

    # -- access -------------------------------------------------------------
    def get(self, key: str, version: str | None = None) -> Dataset:
        if not isinstance(key, str) or not DATASET_RE.match(key):
            raise UserError('invalid dataset key')
        ds = self.datasets.get(key)
        if ds is None:
            raise VersionChanged('unknown dataset (removed or not yet discovered); refresh')
        if ds.status != 'ready':
            if version:
                raise VersionChanged(f'dataset is {ds.status}; results bound to version {version[:12]} are no longer served')
            raise UserError(f'dataset not ready: {ds.status}' + (f' ({ds.error})' if ds.error else ''))
        if version is not None:
            if not isinstance(version, str) or not VERSION_RE.match(version):
                raise UserError('invalid dataset version')
            if version != ds.version:
                raise VersionChanged(f'dataset version changed from {version[:12]} to {str(ds.version)[:12]}; earlier results are stale')
        return ds

    @contextlib.contextmanager
    def guarded(self, ds: Dataset):
        """Fingerprint guard for work that reads snapshot files outside our own connection (backend trace)."""
        if ds.kind != 'snapshot':
            yield
            return
        version = ds.version
        self._check_unchanged(ds, version)
        yield
        self._check_unchanged(ds, version)

    def _check_unchanged(self, ds: Dataset, version: str | None) -> None:
        assert ds.path is not None
        if not ds.path.is_dir():
            self.datasets.pop(ds.key, None)
            raise VersionChanged('snapshot directory was removed')
        fp = _fingerprint(ds.path)
        fp['@ledger'] = (ds.fingerprint or {}).get('@ledger')
        if ds.version != version or fp != ds.fingerprint:
            if ds.version == version:
                self._invalidate(ds, 'snapshot files changed since verification; waiting for them to settle')
            raise VersionChanged('snapshot files changed since verification; the result was discarded and the snapshot will be re-verified')

    def _reverify_bytes(self, ds: Dataset, version: str | None) -> None:
        """Per-request byte check: backend storage.verify re-hashes every file (~0.1 s for the 174 MB cohort)."""
        try:
            manifest = self.storage.verify(ds.path, ds.key)
        except (OSError, ValueError) as exc:
            self._invalidate(ds, f'per-request verification failed: {exc}')
            raise VersionChanged(f'snapshot failed byte verification on this request ({exc}); result refused') from exc
        if manifest.get('manifest_sha256') != version:
            self._invalidate(ds, 'manifest changed under the same snapshot ID')
            raise VersionChanged('snapshot manifest changed under the same ID; result refused')

    def run(self, ds: Dataset, fn: Callable[[Any], Any], timeout: float = QUERY_TIMEOUT_S):
        """One bounded unit of work on one dataset version.

        In-memory datasets use their own connection. Snapshots get a fresh read-only connection that is closed
        afterwards; the stat fingerprint is checked before opening and after closing, and the backend
        re-verifies every file hash before opening.
        """
        if not ds.lock.acquire(timeout=LOCK_WAIT_S):
            raise UserError('dataset busy; try again')
        db = None
        timer = None
        try:
            if ds.kind == 'snapshot':
                version = ds.version
                self._check_unchanged(ds, version)
                self._reverify_bytes(ds, version)
                db = self._open_snapshot_db(ds.path, ds.manifest)
            else:
                db = ds.db
            timer = threading.Timer(timeout, db.interrupt)
            timer.start()
            try:
                result = fn(db)
            except Exception as exc:
                if 'interrupt' in str(exc).lower():
                    raise UserError(f'query exceeded {timeout:.0f}s bound and was interrupted') from exc
                if 'out of memory' in str(exc).lower():
                    raise UserError('query exceeded the DuckDB memory bound; narrow it') from exc
                raise
            if ds.kind == 'snapshot':
                db.close()
                db = None
                self._check_unchanged(ds, version)
                ds.checked_at = time.time()
            return result
        finally:
            if timer is not None:
                timer.cancel()
            if db is not None and ds.kind == 'snapshot':
                db.close()
            ds.lock.release()

    def close(self) -> None:
        for ds in self.datasets.values():
            if ds.db is not None:
                try:
                    ds.db.close()
                except Exception:
                    pass
