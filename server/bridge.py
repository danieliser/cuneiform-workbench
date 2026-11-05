"""Read-only bridge to the cuneiform-research backend.

The research repository stays authoritative: this module imports its package
from a configured path, checks versions, and calls its own ingest/projection/
query functions. It never writes to the repository or to canonical snapshots.

Snapshot lifecycle (canonical snapshots are treated as read-only but possibly
still changing until the corpus owner confirms publication):

* Each snapshot directory gets a stat fingerprint (size, mtime, inode, ctime of
  every file). A snapshot is verified only after the directory has been quiet for
  ``stable_s`` seconds, by the backend: ``storage.verified(storage.store_path(...),
  ledger='require')`` hashes every payload file and checks the publication ledger.
  Refusals keep the backend's typed ``PublicationError.state``.
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
# 63f4221: corpus pilot with ledger format 2, ledger-bound store reads (StoreEntry) and typed refusals; independently
# accepted in cuneiform-research reviews/corpus-pilot-review-v0.3.md. d6049e2 lacks storage.verified/store_path.
# 057a0b7 (Q-023): full eligible eBL import, bounded lookups (SCOPES_SQL, partition-first). Writer-tested only: its 75-test
# suite passed on a clean export (disk tests included); independent review pending. The UI calls it "tested" in the byte-identity sense.
TESTED_COMMITS = {'63f42216243141f4b10cdfd3d7ddd514c85b63d7', '057a0b757ed3dc05e486893bc344a15dd73c86d8'}

SNAPSHOT_RE = re.compile(r'^snapshot:[0-9a-f]{32}$')
DATASET_RE = re.compile(r'^(snapshot:[0-9a-f]{32}|[a-z][a-z0-9-]{1,40})$')
VERSION_RE = re.compile(r'^[0-9a-f]{16,64}$')
NODE_RE = re.compile(r'^[a-z][a-z-]{1,24}:[0-9a-f]{32}$')
TOKEN_RE = re.compile(r'^[^\s\x00-\x1f\x7f]{1,64}$')

QUERY_TIMEOUT_S = 10.0
LOCK_WAIT_S = 15.0
# Background (discovery and routine) verification hashes every payload file. The full eligible eBL snapshot is ~10 GiB.
MAX_VERIFY_BYTES = 48 * 1024**3
# Per request, snapshots up to this size are re-hashed in full. Larger ones get a bounded check instead: unchanged stat
# fingerprint of every file (size, mtime, inode, ctime), manifest.json bytes equal to those hashed at verification, and the
# backend ledger check; their payload hashes are re-verified in the background every REVERIFY_LARGE_S (labelled per request).
PER_REQUEST_REHASH_MAX = 2 * 1024**3
REVERIFY_LARGE_S = 3600.0
# Per-request DuckDB memory bound (spill disabled). Graph traversal on the full eBL snapshot needs ~512 MB (Q-023 probe).
SNAPSHOT_MEMORY = '256MB'
LARGE_SNAPSHOT_MEMORY = '512MB'
MAX_RAW_TRACE_BYTES = 64 * 1024**2
STABLE_S = float(os.environ.get('CUNEIFORM_UI_STABLE_S', '30'))  # quiet period before a snapshot is verified
REVERIFY_S = 900.0  # full re-hash of ready snapshots at most this often (stat fingerprint is checked per request)
SELECTION_SOURCES = {'oracc-selection', 'cdli-selection'}
STATE_LOCK = threading.RLock()  # dataset state transitions: verification commits vs request-time invalidation


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
    status: str = 'pending'  # pending | settling | verifying | ready | failed | incompatible | unpublished | refused
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
    reverifying: bool = False  # routine re-hash of an unchanged ready snapshot: it keeps serving its version meanwhile
    generation: int = 0  # bumped on every invalidation; a verification started earlier never commits over it
    ever_ready: bool = False
    stale: dict | None = None  # why the last served version stopped being served (explicit stale state for clients)
    attempts: int = 0
    next_retry: float = 0.0
    builtin_examples: bool = False
    publication: dict = field(default_factory=dict)
    superseded: dict | None = None  # oracc-cdl-1 snapshot superseded for sense provenance (still served)
    supersedes: dict | None = None
    payload_bytes: int = 0
    manifest_file_sha256: str | None = None

    def verification(self) -> dict:
        if self.kind != 'snapshot':
            return {'mode': 'in-memory', 'note': 'not a snapshot'}
        if self.payload_bytes > PER_REQUEST_REHASH_MAX:
            return {'mode': 'bounded-per-request', 'payload_bytes': self.payload_bytes, 'full_rehash_interval_s': REVERIFY_LARGE_S,
                    'note': (f'Snapshot is above {PER_REQUEST_REHASH_MAX // 1024**3} GiB. Every payload hash was verified by the backend at verified_at and is '
                             f're-verified in the background about every {REVERIFY_LARGE_S / 3600:.0f} h while unchanged; changes trigger re-verification. Each request checks the stat fingerprint of every file '
                             '(size, mtime, inode, ctime), the manifest.json bytes and the publication ledger, but does not re-hash the payload.')}
        return {'mode': 'full-rehash-per-request', 'payload_bytes': self.payload_bytes,
                'note': 'Each request re-hashes every payload file and checks the publication ledger (backend storage.verified).'}

    def public(self) -> dict:
        return {'key': self.key, 'kind': self.kind, 'title': self.title, 'description': self.description,
                'status': self.status, 'error': self.error, 'scopes': self.scopes, 'inputs': self.inputs,
                'examples': self.examples, 'authority': AUTHORITY[self.kind], 'version': self.version,
                'selection': self.selection, 'verified_at': self.verified_at, 'checked_at': self.checked_at,
                'reverifying': self.reverifying, 'stale': self.stale, 'superseded': self.superseded, 'supersedes': self.supersedes,
                'publication': self.publication or {'status': 'not-a-snapshot', 'note': PUBLICATION[self.kind]},
                'verification': self.verification(),
                'manifest': _manifest_summary(self.manifest) if self.manifest else None}

    def binding(self) -> dict:
        """Identity every response carries, so a result can never outlive its dataset version."""
        return {'key': self.key, 'kind': self.kind, 'version': self.version, 'verified_at': self.verified_at,
                'selection_label': self.selection.get('label'), 'scope_note': self.selection.get('note'),
                'publication': (self.publication or {}).get('status')}


AUTHORITY = {
    'snapshot': 'Payload hashes verified at verified_at. Opened read-only with the per-request integrity check described below.',
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


def store_entry_refusal(path: Path, key: str) -> dict | None:
    """UI pre-check before any hashing, using the backend's state names (Q-020 R1 / review v0.3).

    The backend (63f4221) refuses symlinked or escaping entries and payload-path symlinks itself. This check is a strict
    superset: it also refuses a symlink anywhere inside the entry, and types a manifest naming another ID as
    ``misplaced`` (the backend reports that case as a plain identity-mismatch error). Nothing is followed.
    """
    def refuse(state: str, detail: str) -> dict:
        out = publication_refusal(state)
        out.update(source='UI pre-check (before backend verification)', detail=detail)
        return out
    if path.is_symlink():
        return refuse('symlinked-entry', 'the store entry itself is a symbolic link')
    store = path.parent
    try:
        if path.resolve().parent != store.resolve():
            return refuse('symlinked-entry', 'the store entry resolves outside the snapshot store')
        links = [str(p.relative_to(path)) for p in path.rglob('*') if p.is_symlink()]
    except OSError as exc:
        return refuse('symlinked-entry', f'the store entry could not be inspected ({type(exc).__name__})')
    if links:
        return refuse('symlinked-entry', f'the store entry contains symbolic links ({", ".join(sorted(links)[:3])})')
    try:
        sid = json.loads((path / 'manifest.json').read_text()).get('snapshot_id')
    except (OSError, ValueError, AttributeError):
        return None  # unreadable/incomplete manifest: verification reports it (and retries if it settles)
    if sid != key:
        return refuse('misplaced', f'manifest names {str(sid)[:41]}, but the store entry is {key}')
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def _fingerprint_eq(path: Path, fp: dict) -> bool:
    now = _fingerprint(path)
    return now == {k: v for k, v in fp.items() if not k.startswith('@')}


def _newest(fp: dict) -> float:
    return max((v[1] for k, v in fp.items() if not k.startswith('@')), default=0) / 1e9


def selection_of(persisted, manifest: dict, path: Path) -> dict:
    """Population the counts describe, from the backend's own ``persisted.scope`` (never re-derived here)."""
    sc = {k: v for k, v in persisted.scope(path, manifest).items() if k != 'publication'}  # publication is held separately
    # 'all eligible records of a fixed release' is structurally a cohort (cohort.json, parent trace); its label/note say what it is.
    kind = {'rule-selected cohort': 'cohort', 'all eligible records of a fixed release': 'cohort', 'explicit selection': 'selection',
            'single input': 'single-input'}.get(sc.get('kind'), sc.get('kind'))
    out = dict(sc, kind=kind, backend_kind=sc.get('kind'), note=sc.get('scope_note'),
               size=sc.get('cohort_size', sc.get('selected')), rule_name=(sc.get('rule') or {}).get('name') or sc.get('selection_rule'))
    if manifest.get('source') == 'oracc-selection':
        out['oracc'] = oracc_info(manifest, path)
    return out


def oracc_adapter_problem(manifest: dict) -> str | None:
    if manifest.get('source') != 'oracc-selection' or manifest.get('adapter') in ORACC_ADAPTERS:
        return None
    return f"ORACC adapter {manifest.get('adapter')!r} is not one this UI understands ({', '.join(sorted(ORACC_ADAPTERS))}); not served"


def oracc_info(manifest: dict, path: Path) -> dict:
    """Adapter label plus a same-inputs key (parent zip, project, texts, rule) from selection.json, which the backend's
    verification binds to the manifest. Snapshots sharing the key are the same texts read by different adapters; they
    are labelled, never merged (the backend's input_identity differs because the adapter is part of the record ID)."""
    adapter = manifest.get('adapter')
    sel = json.loads((path / 'selection.json').read_text())
    same_inputs = hashlib.sha256(json.dumps([(sel.get('parent') or {}).get('sha256'), sel.get('project'), sorted(sel.get('texts') or []),
                                             sel.get('selection_rule')], sort_keys=True).encode()).hexdigest()[:32]
    return dict(ORACC_ADAPTERS.get(adapter, {}), adapter=adapter, same_inputs=same_inputs, texts=len(sel.get('texts') or []),
                project=sel.get('project'))


# Backend PublicationError.state -> (dataset status, UI note). The backend decides the state (storage.verified with
# ledger='require'); the UI only words it. Review v0.3 N1: a reader's ``reserved`` is "not yet available" -- the backend's
# refusal text can claim no live writer holds the lock, which a reader never checks, so that prose is not shown.
PUBLICATION_STATES = {
    'reserved': ('not-yet-available', 'Not yet available: the ledger has a reservation for this ID but no commit. A writer may '
                 'still be completing it, or it was interrupted; nothing is served until a commit is recorded.'),
    'not-in-ledger': ('unpublished', 'Not listed as published in the backend publication ledger; not served.'),
    'unledgered': ('unpublished', 'Present in the store without a publication record; not served.'),
    'ledger-mismatch': ('refused', 'The bytes in the store differ from what the ledger published under this ID; refused.'),
    'conflict': ('refused', 'The ledger holds an impossible sequence of records for this ID; refused.'),
    'ledger-corrupt': ('refused', 'The publication ledger has an unparseable record before its end; every snapshot is refused '
                       'until the corpus owner repairs it.'),
    'symlinked-entry': ('refused', 'The store entry is a symbolic link, contains one, or resolves outside the store; only real '
                        'directories inside the store are served.'),
    'misplaced': ('refused', 'The directory name and the snapshot ID in its manifest differ; refused.'),
    'published-missing': ('unavailable', 'Published in the ledger, but its directory is missing from the store; not served.'),
    'abandoned': ('unavailable', 'Reserved and then lost before commit; the ID is burned permanently and never served.'),
    'aborted': ('unavailable', 'Its reservation was aborted; nothing was published under this ID.'),
    'not-found': ('unavailable', 'Not present in the store.'),
}
# States worth listing for a ledger ID that has no directory (aborted / never-seen IDs are not shown).
LEDGER_ONLY_STATES = {'reserved', 'published-missing', 'abandoned', 'conflict'}

# ORACC selection adapters this UI understands. Both stay servable; oracc-cdl-1 is superseded for sense provenance.
ORACC_ADAPTERS = {
    'oracc-cdl-1': {'sense_provenance': 'legacy',
                    'note': 'oracc-cdl-1: the shared Sense row stores the sig of the first attesting instance only; it is not '
                            'each word\'s own sig. Per-word sense provenance is not recorded in this snapshot.'},
    'oracc-cdl-2': {'sense_provenance': 'per-word',
                    'note': 'oracc-cdl-2: each word\'s own sig, inst and payload pointer are on its LEMMATIZED_AS / IN_SENSE '
                            'links; the shared Sense row holds only a sense-key locator.'},
}


def publication_refusal(state: str, exc: BaseException | None = None) -> dict:
    """Typed refusal for a backend PublicationError state."""
    status, note = PUBLICATION_STATES.get(state, ('refused', f'The backend refused this snapshot (publication state {state}).'))
    out = {'status': state, 'dataset_status': status, 'servable': False, 'source': 'backend storage.verified', 'note': note}
    if exc is not None and state != 'reserved':
        out['backend_message'] = str(exc)[:600]
    return out


def incident_note(incident: str) -> str:
    return ('Ledgered after the fact. This ID was deleted and rebuilt under the same ID before the ledger existed; '
            'first-version bytes were not retained. Current bytes verify, but results produced from this ID before '
            'the rebuild cannot be reproduced. Incident: ' + str(incident))


def tail_note(tail: dict) -> str:
    return (f"The publication ledger ends with an unterminated record at byte {tail.get('offset')} "
            f"({tail.get('length')} bytes, sha256 {str(tail.get('sha256'))[:16]}…, parsed={tail.get('parsed')}): an append in "
            'progress or a torn write. The backend ignores it for reads (a torn commit leaves its ID reserved, so that ID stays '
            'refused) and refuses further publication until `publication-repair-ledger` acknowledges it.')


def publication_from_backend(pub: dict) -> dict:
    """Map ``storage.verified(...)['publication']`` to the UI label; the legacy incident and a torn tail stay visible."""
    out = dict(pub, servable=True, source='backend storage.verified')
    if pub.get('incident'):
        out.update(status='legacy-incident', note=incident_note(pub['incident']), backend_status=pub.get('status'))
    else:
        out.setdefault('note', pub.get('binding') or 'published (backend ledger check)')
    if pub.get('ledger_tail'):
        out['ledger_tail_note'] = 'unterminated final ledger record present at this read (' + str(pub['ledger_tail']) + ')'
    return out


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
        self.ledger: dict = {'by_id': {}}  # last observation of the backend ledger (Ledger.scan), for display/change detection
        self.timing = threading.local()  # per-request timing breakdown (see timing_begin/timing_parts)
        self.info = self._load()

    # -- per-request timing -------------------------------------------------
    def timing_begin(self) -> None:
        self.timing.parts = {}

    def timing_parts(self) -> dict:
        return dict(getattr(self.timing, 'parts', None) or {})

    def _mark(self, name: str, started: float) -> None:
        parts = getattr(self.timing, 'parts', None)
        if parts is not None:
            parts[name] = round(parts.get(name, 0.0) + (time.monotonic() - started) * 1000, 1)

    def note_timing(self, name: str, value) -> None:
        parts = getattr(self.timing, 'parts', None)
        if parts is not None:
            parts[name] = value

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
        try:
            from cuneiform import publication
        except ImportError:
            publication = None  # pre-ledger-2 backend: reported as incompatible below
        self.publication = publication
        # The only read path: StoreEntry (storage.store_path) + storage.verified(..., ledger='require'). The UI does not
        # parse the ledger itself; it observes it with the backend's own Ledger.scan for change detection and display.
        required = {'projections': ['build', 'lookup', 'source_span', 'coverage', 'report', 'assert_fresh', 'ProjectionConfig'],
                    'query': ['motif', 'trace_rows', 'array_count', 'trace'],
                    'storage': ['verify', 'verified', 'store_path', 'read_bounded', 'validate_graph', 'Config'],
                    'publication': ['Ledger', 'PublicationError'],
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
                'frozen_fast_path': hasattr(projections, 'mark_frozen_verified'),
                'publication_read': "backend storage.store_path + storage.verified(ledger='require') (ledger formats 1 and 2)",
                'oracc_adapters': sorted(ORACC_ADAPTERS)}

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
            self.ledger = self._observe_ledger(snapshot_dir)
            if snapshot_dir.is_dir():
                for p in sorted(snapshot_dir.iterdir()):
                    # is_dir() follows a symlinked entry on purpose: it is listed, then refused with a typed reason.
                    if p.is_dir() and SNAPSHOT_RE.match(p.name) and (p / 'manifest.json').is_file():
                        present.add(p.name)
                        t = self._consider(p, now, deep)
                        if t:
                            started.append(t)
            listed = self._ledger_only(present)
            for key in [k for k, d in self.datasets.items() if d.kind == 'snapshot' and k not in present and k not in listed]:
                self.datasets.pop(key)  # removed from disk and nothing to say from the ledger: forget it (no connection is held)
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

    def _observe_ledger(self, snapshot_dir: Path) -> dict:
        """Observe the publication ledger through the backend's own parser (Ledger.scan); never re-derived here.

        Used for change detection (a ledger change re-evaluates the IDs it touches) and for display: a torn final
        record or an unparseable record is reported explicitly, never skipped silently. Publication decisions are
        made only by storage.verified(..., ledger='require').
        """
        out: dict = {'path': str(snapshot_dir / '.publication-ledger.jsonl'), 'exists': False, 'records': 0, 'tail': None,
                     'tail_note': None, 'corrupt': None, 'by_id': {}, 'states': {}}
        if self.publication is None:
            return out
        led = self.publication.Ledger(snapshot_dir)
        out['exists'] = led.exists()
        try:
            entries, tail = led.scan()
        except ValueError as exc:  # LedgerCorrupt (PublicationError): every decision is refused by the backend too
            out['corrupt'] = str(exc)[:600]
            return out
        except OSError as exc:
            out['corrupt'] = f'ledger unreadable ({type(exc).__name__})'
            return out
        out['records'] = len(entries)
        if tail is not None:
            out['tail'] = {k: tail.get(k) for k in ('offset', 'length', 'sha256', 'parsed')}
            out['tail_note'] = tail_note(tail)
        groups: dict[str, list] = {}
        for e in entries:
            sid = e.get('snapshot_id')
            if isinstance(sid, str):
                groups.setdefault(sid, []).append({k: v for k, v in e.items() if not k.startswith('_') or k == '_unterminated'})
        for sid, group in groups.items():
            out['by_id'][sid] = hashlib.sha256(json.dumps(group, sort_keys=True, default=str).encode()).hexdigest()
            try:
                out['states'][sid] = led.status(sid, entries).state
            except ValueError:
                out['states'][sid] = 'conflict'
        return out

    def _ledger_key(self, key: str) -> list:
        """Change-detection key for one ID: its own ledger records and whether the ledger as a whole is corrupt."""
        return [self.ledger.get('by_id', {}).get(key), bool(self.ledger.get('corrupt'))]

    def _ledger_only(self, present: set) -> set:
        """List ledger IDs without a directory whose state matters (reserved, published-missing, abandoned, conflict).

        The state comes from the backend: a store read of the absent entry raises a typed PublicationError.
        """
        listed: set = set()
        if self.publication is None:
            return listed
        cfg = self.storage.Config(self.research_root)
        for sid in sorted(self.ledger.get('states', {})):
            if sid in present or not SNAPSHOT_RE.match(sid):
                continue
            try:
                self.storage.verified(self.storage.store_path(cfg, sid), ledger='require')
                continue  # cannot normally happen without a directory; the next scan lists it from disk
            except ValueError as exc:
                state = getattr(exc, 'state', None)
            except OSError:
                state = 'not-found'
            if not isinstance(state, str):
                state = 'not-found'
            if state not in LEDGER_ONLY_STATES:
                continue
            listed.add(sid)
            ds = self.datasets.get(sid)
            if ds is None:
                ds = Dataset(sid, 'snapshot', sid, 'Ledger record without a directory in the store')
                self.datasets[sid] = ds
            pub = publication_refusal(state)
            if ds.publication.get('status') != state or ds.status == 'ready':
                ds.publication = pub
                self._retire(ds, pub['dataset_status'], pub['note'])
                ds.fingerprint = None
        return listed

    def _consider(self, path: Path, now: float, deep: bool) -> threading.Thread | None:
        key = path.name
        ds = self.datasets.get(key)
        if ds is None:
            ds = Dataset(key, 'snapshot', key, 'Persisted snapshot (not yet verified)', path=path)
            self.datasets[key] = ds
        if ds.path is None:  # was listed from the ledger only; its directory has appeared
            ds.path = path
        if ds.verifying:
            return None
        fp = _fingerprint(path)
        fp['@ledger'] = self._ledger_key(key)  # a ledger change re-evaluates publication status
        changed = fp != ds.fingerprint
        interval = REVERIFY_LARGE_S if ds.payload_bytes > PER_REQUEST_REHASH_MAX else REVERIFY_S
        routine = ds.status == 'ready' and not changed and (deep or now - (ds.verified_at or 0) > interval)
        due = (ds.status in ('pending', 'settling')
               or changed
               or (ds.status == 'failed' and now >= ds.next_retry)
               or routine)
        if not due:
            return None
        if routine:
            # Review N-1: nothing changed on disk, so the served version stays ready while the bytes are re-hashed.
            # Requests use the size-dependent integrity check; this pass refreshes verified_at (or, if the
            # bytes turn out different, invalidates the version with an explicit stale reason).
            ds.verifying = ds.reverifying = True
            t = threading.Thread(target=self._verify_snapshot, args=(ds, fp, ds.generation, True), daemon=True)
            t.start()
            return t
        if changed and ds.status == 'ready':
            self._invalidate(ds, 'snapshot files or its ledger entry changed since verification; waiting for them to settle')
        if now - _newest(fp) < self.stable_s:
            ds.status, ds.version = 'settling', None
            ds.error = f'files changed {now - _newest(fp):.0f}s ago; verifying after {self.stable_s:.0f}s without changes'
            ds.fingerprint = fp
            return None
        ds.verifying = True
        ds.status = 'verifying'
        t = threading.Thread(target=self._verify_snapshot, args=(ds, fp, ds.generation, False), daemon=True)
        t.start()
        return t

    @staticmethod
    def _invalidate(ds: Dataset, why: str) -> None:
        Backend._retire(ds, 'settling', why)

    @staticmethod
    def _retire(ds: Dataset, status: str, why: str) -> None:
        """Stop serving the current version; record which version became stale and why (explicit stale state)."""
        with STATE_LOCK:
            if ds.version is not None:
                ds.stale = {'version': ds.version, 'reason': why, 'at': time.time()}
            ds.generation += 1
            ds.status, ds.version, ds.error = status, None, why

    def listing(self) -> dict:
        real = [d for d in self.datasets.values() if d.kind == 'snapshot']
        real_ready = any(d.status == 'ready' for d in real)
        # A snapshot that was ready and is only settling/re-verifying keeps demos hidden: demos appearing and
        # disappearing during a re-check would reshuffle the picker (review N-1).
        transient = any(d.ever_ready and d.status in ('settling', 'verifying') for d in real)
        demos_visible = self.show_demos == 'always' or (self.show_demos == 'auto' and not real_ready and not transient)
        order = {'snapshot': 0, 'sample': 1, 'synthetic': 2}
        spec_rank = {key: i for i, (key, *_rest) in enumerate(self._sample_specs())}
        self._mark_supersession(real)
        shown = [d for d in self.datasets.values() if d.kind == 'snapshot' or demos_visible]
        # Richest first: real snapshots, then samples that carry text, in curated order.
        shown.sort(key=lambda d: (order[d.kind], not d.scopes, spec_rank.get(d.key, 0), d.title))
        return {'datasets': [d.public() for d in shown], 'real_snapshots': len(real), 'real_ready': real_ready,
                'demos_visible': demos_visible, 'demo_policy': self.show_demos,
                'snapshot_dir': str(self.research_root / 'snapshots'),
                'snapshot_dir_exists': (self.research_root / 'snapshots').is_dir(),
                'ledger': {k: self.ledger.get(k) for k in ('path', 'exists', 'records', 'tail', 'tail_note', 'corrupt', 'states')},
                'last_discovery': self.last_discovery, 'stable_s': self.stable_s, 'now': time.time()}

    @staticmethod
    def _mark_supersession(real: list) -> None:
        """oracc-cdl-1 snapshots are superseded for sense provenance by an oracc-cdl-2 snapshot of the same inputs.

        Both stay listed and servable (historical evidence is never hidden); only the label and the word view change.
        """
        newer: dict[str, Dataset] = {}
        for d in real:
            o = (d.selection or {}).get('oracc') or {}
            if o.get('adapter') == 'oracc-cdl-2':
                newer[o['same_inputs']] = d
        for d in real:
            o = (d.selection or {}).get('oracc') or {}
            succ = newer.get(o.get('same_inputs')) if o.get('adapter') == 'oracc-cdl-1' else None
            d.superseded = None if succ is None else {
                'by': succ.key, 'by_status': succ.status, 'for': 'sense provenance', 'by_adapter': 'oracc-cdl-2',
                'note': (f'Superseded for sense provenance by {succ.key} (oracc-cdl-2, same ZIP receipt and {o.get("texts")} texts'
                         + ('' if succ.status == 'ready' else f'; that snapshot is currently {succ.status}')
                         + '). This snapshot stays available as historical evidence; its Sense rows carry the first attesting '
                           "instance's sig, not each word's own.")}
            d.supersedes = None
        for d in real:
            if d.superseded:
                succ = next(x for x in real if x.key == d.superseded['by'])
                succ.supersedes = {'key': d.key, 'for': 'sense provenance',
                                   'note': f'Supersedes {d.key} (oracc-cdl-1) for sense provenance; same ZIP receipt and texts. Both are listed.'}

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
              {'label': 'ABZ215 ABZ13 — strict (parser does not infer certainty)', 'pattern': ['ABZ215', 'ABZ13'], 'field': 'signs', 'policy': 'strict', 'engine': 'index', 'expect': 0}]),
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

    def store_entry(self, key: str):
        """Backend StoreEntry for a snapshot ID: every read of it runs with ledger='require' and expected_sid=key."""
        return self.storage.store_path(self.storage.Config(self.research_root), key)

    def verified(self, key: str) -> dict:
        """Content verification (every payload hash) + ledger check, both by the backend. Raises typed PublicationError."""
        return self.storage.verified(self.store_entry(key), ledger='require')

    def _verify_and_publication(self, ds: Dataset) -> tuple[dict | None, dict]:
        """Content verification + publication status. Returns (manifest or None when refused, publication)."""
        try:
            out = self.verified(ds.key)
        except ValueError as exc:
            state = getattr(exc, 'state', None)
            if not isinstance(state, str):
                raise  # content/integrity failure: the snapshot failed, it was not merely unpublished
            return None, publication_refusal(state, exc)
        return out['manifest'], publication_from_backend(out.get('publication') or {})

    def _verify_snapshot(self, ds: Dataset, fp_before: dict, gen: int, routine: bool = False) -> None:
        """Verify one snapshot in a background thread.

        ``gen`` is the dataset generation when the pass started; if anything invalidated the dataset meanwhile
        (a request saw changed files, a newer scan), this pass commits nothing. A *routine* pass runs while the
        dataset keeps serving its current version; it only refreshes ``verified_at`` when the same version
        verifies again, and otherwise retires that version with an explicit stale reason.
        """
        assert ds.path is not None
        commit = {}
        try:
            refusal = store_entry_refusal(ds.path, ds.key)
            if refusal:
                commit = {'status': refusal['dataset_status'], 'error': f"{refusal['note']} ({refusal['detail']})", 'publication': refusal}
                return
            pre = json.loads((ds.path / 'manifest.json').read_text())
            schema, parser = pre.get('schema_version'), pre.get('parser_version')
            rev = (pre.get('projection_spec') or {}).get('revision')
            if schema not in SUPPORTED['schema'] or parser not in SUPPORTED['parser'] or rev not in SUPPORTED['projection']:
                commit = {'status': 'incompatible', 'error': f'schema {schema} / parser {parser} / projection {rev}: not supported by this UI build'}
                return
            total = sum(v[0] for k, v in fp_before.items() if not k.startswith('@'))
            if total > MAX_VERIFY_BYTES:
                raise UserError(f'snapshot is {total} bytes; UI verification cap is {MAX_VERIFY_BYTES}')
            manifest, publication = self._verify_and_publication(ds)
            if manifest is None or not publication.get('servable'):
                commit = {'status': publication.get('dataset_status', 'refused'), 'error': publication['note'], 'publication': publication}
                return
            adapter_problem = oracc_adapter_problem(manifest)
            if adapter_problem:
                commit = {'status': 'incompatible', 'error': adapter_problem}
                return
            if routine and manifest.get('manifest_sha256') == ds.version and _fingerprint_eq(ds.path, fp_before):
                commit = {'status': 'ready', 'publication': publication, 'refresh_only': True}
                return
            selection = selection_of(self.persisted, manifest, self.store_entry(ds.key))
            db = self._open_snapshot_db(ds.path, manifest, total)
            try:
                scopes = self.projections.rows(db, self.scopes_sql())
                probe = Dataset(ds.key, 'snapshot', '', '', scopes=scopes)
                examples = self._auto_examples(probe, db)
            finally:
                db.close()
            if not _fingerprint_eq(ds.path, fp_before):
                commit = {'status': 'settling', 'error': 'snapshot changed while it was being verified; will retry once it settles',
                          'fingerprint': _fingerprint(ds.path)}
                return
            commit = {'status': 'ready', 'manifest': manifest, 'selection': selection, 'scopes': scopes, 'examples': examples,
                      'publication': publication, 'payload_bytes': total, 'manifest_file_sha256': _sha256_file(ds.path / 'manifest.json')}
        except Exception as exc:
            commit = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}', 'exc': True}
        finally:
            self._commit(ds, fp_before, gen, routine, commit)

    def _commit(self, ds: Dataset, fp_before: dict, gen: int, routine: bool, c: dict) -> None:
        with STATE_LOCK:
            try:
                if ds.generation != gen:
                    return  # invalidated while verifying: a newer scan decides (never resurrect an old result)
                status = c.get('status', 'failed')
                if status == 'ready' and c.get('refresh_only'):
                    ds.publication = c['publication']
                    ds.verified_at = ds.checked_at = time.time()
                    ds.error, ds.attempts = None, 0
                    return
                if status == 'ready':
                    manifest, selection = c['manifest'], c['selection']
                    if ds.version is not None and ds.version != manifest['manifest_sha256']:
                        ds.stale = {'version': ds.version, 'reason': 'a different manifest verified under the same snapshot ID', 'at': time.time()}
                    ds.manifest, ds.selection, ds.scopes, ds.examples = manifest, selection, c['scopes'], c['examples']
                    ds.publication = c['publication']
                    adapter = (selection.get('oracc') or {}).get('adapter')
                    ds.title = f"{manifest.get('namespace', '?')} · {selection['label']}{' · ' + adapter if adapter else ''} · {ds.key[9:21]}"
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
                    ds.fingerprint = fp_before
                    ds.payload_bytes, ds.manifest_file_sha256 = c['payload_bytes'], c['manifest_file_sha256']
                    ds.verified_at = ds.checked_at = ds.loaded_at = time.time()
                    ds.status, ds.error, ds.attempts, ds.ever_ready = 'ready', None, 0, True
                    if ds.stale and ds.stale.get('version') == ds.version:
                        ds.stale = None
                    return
                why = c.get('error') or status
                if c.get('exc'):
                    ds.attempts += 1
                    ds.next_retry = time.time() + min(300.0, 15.0 * 2 ** min(ds.attempts, 5))
                    why = f'{why} (retry {ds.attempts} scheduled)'
                if 'publication' in c:
                    ds.publication = c['publication']
                prefix = 're-verification of the served version failed: ' if routine else ''
                self._retire(ds, status, prefix + why)
                ds.fingerprint = c.get('fingerprint', fp_before)
            finally:
                ds.verifying = ds.reverifying = False

    def scopes_sql(self) -> str:
        # Backend Q-023 form avoids ordered_position's corpus-wide window; same rows. Older pins keep the original query.
        return getattr(self.projections, 'SCOPES_SQL', 'SELECT DISTINCT namespace,snapshot_id,field FROM ordered_position ORDER BY 1,2,3')

    def _open_snapshot_db(self, path: Path, manifest: dict, payload_bytes: int = 0):
        import duckdb
        db = duckdb.connect(str(path / 'graph.duckdb'), read_only=True)
        try:
            self._configure(db, LARGE_SNAPSHOT_MEMORY if payload_bytes > PER_REQUEST_REHASH_MAX else SNAPSHOT_MEMORY, 2)
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
        ds.scopes = self.projections.rows(db, self.scopes_sql())
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
        t = time.monotonic()
        self._check_unchanged(ds, version)
        self._mark('stat_check_ms', t)
        yield
        t = time.monotonic()
        self._check_unchanged(ds, version)
        self._mark('stat_check_ms', t)

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
        """Size-dependent per-request verification: full hashes or stat/manifest checks, plus the backend ledger.

        A publication refusal (reserved, mismatch, corrupt ledger, symlinked entry...) retires the version with its typed
        state; a byte failure invalidates it. Either way the result is refused (HTTP 409).
        """
        started = time.monotonic()
        try:
            out = self._per_request_verified(ds)
        except ValueError as exc:
            state = getattr(exc, 'state', None)
            if isinstance(state, str):
                pub = publication_refusal(state, exc)
                with STATE_LOCK:
                    if ds.version == version:
                        ds.publication = pub
                        self._retire(ds, pub['dataset_status'], pub['note'])
                        # Recheck even if the ledger returns to its prior bytes before discovery.
                        ds.fingerprint = {}
                raise VersionChanged(f'snapshot is no longer served ({state}): {pub["note"]} Result refused.') from exc
            self._invalidate(ds, f'per-request verification failed: {exc}')
            raise VersionChanged(f'snapshot failed byte verification on this request ({exc}); result refused') from exc
        except OSError as exc:
            self._invalidate(ds, f'per-request verification failed: {exc}')
            raise VersionChanged(f'snapshot failed byte verification on this request ({exc}); result refused') from exc
        finally:
            self._mark('verify_ms', started)
        if out['manifest'].get('manifest_sha256') != version:
            self._invalidate(ds, 'manifest changed under the same snapshot ID')
            raise VersionChanged('snapshot manifest changed under the same ID; result refused')
        pub = publication_from_backend(out.get('publication') or {})
        with STATE_LOCK:
            if ds.version == version:
                ds.publication = pub  # e.g. a torn ledger tail seen on this read is shown, not ignored
        self.note_timing('publication_at_read', pub.get('status'))

    def _per_request_verified(self, ds: Dataset) -> dict:
        """Full backend re-verification up to PER_REQUEST_REHASH_MAX; above it, the bounded check (stat fingerprint was
        checked by the caller; here: manifest.json bytes and the backend ledger check). Both raise typed PublicationErrors."""
        if ds.payload_bytes <= PER_REQUEST_REHASH_MAX:
            self.note_timing('per_request_check', 'full payload re-hash + ledger')
            return self.verified(ds.key)
        entry = self.store_entry(ds.key)
        data = (ds.path / 'manifest.json').read_bytes()
        if hashlib.sha256(data).hexdigest() != ds.manifest_file_sha256:
            raise ValueError('manifest.json bytes differ from the verified manifest')
        manifest = json.loads(data)
        publication = self.storage.publication_of(entry, manifest, 'require')
        self.note_timing('per_request_check', 'stat fingerprint + manifest bytes + ledger; payload hashes verified at verified_at (snapshot above 2 GiB)')
        return {'manifest': manifest, 'publication': publication}

    def run(self, ds: Dataset, fn: Callable[[Any], Any], timeout: float = QUERY_TIMEOUT_S):
        """One bounded unit of work on one dataset version.

        In-memory datasets use their own connection. Snapshots get a fresh read-only connection that is closed
        afterwards; the stat fingerprint is checked before opening and after closing, and the backend
        runs the size-dependent integrity check before opening.
        """
        t = time.monotonic()
        if not ds.lock.acquire(timeout=LOCK_WAIT_S):
            raise UserError('dataset busy; try again')
        self._mark('lock_wait_ms', t)
        db = None
        timer = None
        try:
            if ds.kind == 'snapshot':
                version = ds.version
                t = time.monotonic()
                self._check_unchanged(ds, version)
                self._mark('stat_check_ms', t)
                self._reverify_bytes(ds, version)
                t = time.monotonic()
                db = self._open_snapshot_db(ds.path, ds.manifest, ds.payload_bytes)
                self._mark('open_ms', t)
            else:
                db = ds.db
            timer = threading.Timer(timeout, db.interrupt)
            timer.start()
            t = time.monotonic()
            try:
                result = fn(db)
            except Exception as exc:
                if 'interrupt' in str(exc).lower():
                    raise UserError(f'query exceeded {timeout:.0f}s bound and was interrupted') from exc
                if 'out of memory' in str(exc).lower():
                    raise UserError('query exceeded the DuckDB memory bound; narrow it') from exc
                raise
            finally:
                self._mark('query_ms', t)
            if ds.kind == 'snapshot':
                t = time.monotonic()
                db.close()
                db = None
                self._check_unchanged(ds, version)
                self._mark('stat_check_ms', t)
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
