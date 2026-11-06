"""Shared harness: guarded read-only snapshot access, canonical position extraction, families, splits, run records."""
from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / 'runconfig.json').read_text())
SEED = CONFIG['seed']
GAP, UNS, BOS, EOS = -1, -2, -3, -4
SPECIAL = {GAP: 'GAP', UNS: 'UNS', BOS: 'BOS', EOS: 'EOS'}


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


SNAPSHOT_ID = CONFIG['input']['snapshot_id']
SNAPSHOT = expand(CONFIG['input']['store_root']) / SNAPSHOT_ID
PIN = expand(CONFIG['input']['backend_pin'])


def backend():
    """Import the pinned, read-only backend (never the live UI worker checkout)."""
    if str(PIN) not in sys.path:
        sys.path.insert(0, str(PIN))
    import cuneiform  # noqa: F401
    from cuneiform import storage, projections, query
    assert Path(storage.__file__).resolve().is_relative_to(PIN.resolve()), storage.__file__
    return storage, projections, query


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- guards

def stat_fingerprint(snapshot: Path = SNAPSHOT) -> dict:
    """Cheap per-stage integrity: stat of every snapshot file plus ledger bytes hash (no payload re-hash)."""
    out = {}
    for p in sorted(snapshot.rglob('*')):
        st = p.stat()
        out[str(p.relative_to(snapshot))] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_mode]
    ledger = snapshot.parent / '.publication-ledger.jsonl'
    if ledger.exists():
        out['@ledger_sha256'] = hashlib.sha256(ledger.read_bytes()).hexdigest()
    return out


def free_gib(path: Path = ROOT) -> float:
    return shutil.disk_usage(path).free / 2**30


def check_free():
    free = free_gib()
    if free < CONFIG['resources']['min_free_disk_gib']:
        raise SystemExit(f'refusing: free disk {free:.1f} GiB < minimum')
    return free


def peak_rss() -> int:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == 'darwin' else r * 1024


def check_rss(where: str = ''):
    if peak_rss() > CONFIG['resources']['rss_soft_abort_bytes']:
        raise MemoryError(f'RSS soft limit exceeded {where}')


def cpu_s() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def git_state() -> dict:
    def run(*a):
        return subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    # Uncommitted result directories do not make code dirty; anything else does.
    changed = [ln for ln in run('status', '--porcelain', '--untracked-files=all').splitlines() if not ln[3:].startswith('results/')]
    return {'commit': run('rev-parse', 'HEAD') or None, 'dirty': bool(changed)}


def connect(snapshot: Path = SNAPSHOT):
    import duckdb
    tmp = ROOT / 'out' / 'duckdb-tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    r = CONFIG['resources']
    db = duckdb.connect(str(snapshot / 'graph.duckdb'), read_only=True)
    db.execute(f"SET memory_limit='{r['duckdb_memory_limit']}'")
    db.execute(f"SET threads={int(r['duckdb_threads'])}")
    db.execute(f"SET temp_directory='{tmp}'")
    db.execute(f"SET max_temp_directory_size='{r['duckdb_max_temp_directory_size']}'")
    return db


# ---------------------------------------------------------------- runs

class Run:
    """One stage run: refuses on snapshot drift, records timings/resources/commit, writes results and raw outputs."""

    def __init__(self, stage: str, allow_dirty: bool = False, snapshot: Path = SNAPSHOT):
        self.stage = stage
        self.git = git_state()
        if self.git['dirty'] and not allow_dirty:
            raise SystemExit('refusing official run with uncommitted code (commit first or pass --allow-dirty for pilots)')
        self.started = datetime.now(timezone.utc)
        self.id = f"{stage}-{self.started:%Y%m%dT%H%M%SZ}-{(self.git['commit'] or 'nocommit')[:7]}{'-dirty' if self.git['dirty'] else ''}"
        self.results = ROOT / 'results' / self.id
        self.raw = ROOT / 'out' / self.id
        self.results.mkdir(parents=True)
        self.raw.mkdir(parents=True)
        self.snapshot = snapshot
        pre = ROOT / 'results' / 'preflight.json'
        self.preflight = json.loads(pre.read_text()) if pre.exists() else None
        if snapshot == SNAPSHOT:
            if not self.preflight:
                raise SystemExit('run bench.preflight first (verifies manifest, every file hash and the ledger once)')
            self.fp_before = stat_fingerprint(snapshot)
            if self.fp_before != self.preflight['stat_fingerprint']:
                raise SystemExit('snapshot stat fingerprint differs from preflight; refusing')
        self.t0, self.c0 = time.monotonic(), cpu_s()
        self.free_before = check_free()
        self.load_before = os.getloadavg()
        self.marks = {}
        self.partial = []

    def mark(self, name: str):
        self.marks[name] = {'wall_s': round(time.monotonic() - self.t0, 2), 'cpu_s': round(cpu_s() - self.c0, 2), 'peak_rss_bytes': peak_rss()}
        check_rss(name)

    def cpu_used(self) -> float:
        return cpu_s() - self.c0

    def write(self, name: str, obj, raw: bool = False) -> str:
        path = (self.raw if raw else self.results) / name
        if name.endswith('.csv'):
            import csv
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                for row in obj:
                    w.writerow(row)
        else:
            path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False, default=str) + '\n')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def finish(self, extra: dict | None = None) -> dict:
        if self.snapshot == SNAPSHOT:
            after = stat_fingerprint(self.snapshot)
            unchanged = after == self.fp_before
        else:
            unchanged = None
        def du(p):
            return sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        rec = {
            'run_id': self.id, 'stage': self.stage, 'protocol': CONFIG['protocol_version'], 'seed': SEED,
            'code': self.git, 'backend_pin': CONFIG['input']['backend_commit'],
            'snapshot': SNAPSHOT_ID if self.snapshot == SNAPSHOT else str(self.snapshot),
            'manifest_sha256': (self.preflight or {}).get('manifest_sha256'),
            'runconfig_sha256': hashlib.sha256((ROOT / 'runconfig.json').read_bytes()).hexdigest(),
            'started': self.started.isoformat(), 'wall_s': round(time.monotonic() - self.t0, 2),
            'cpu_s': round(self.cpu_used(), 2), 'peak_rss_bytes': peak_rss(),
            'disk_bytes': {'results': du(self.results), 'raw': du(self.raw)},
            'free_gib': {'before': round(self.free_before, 1), 'after': round(free_gib(), 1)},
            'loadavg': {'before': self.load_before, 'after': os.getloadavg()},
            'snapshot_stat_unchanged': unchanged, 'marks': self.marks, 'partial': self.partial,
            'raw_outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.raw.glob('*')) if p.is_file()},
        }
        rec.update(extra or {})
        self.write('run.json', rec)
        if unchanged is False:
            raise SystemExit('snapshot changed during run; results invalid')
        return rec


# ---------------------------------------------------------------- corpus

@dataclass
class Corpus:
    field: str
    labels: list = field(default_factory=list)        # code -> label
    code: dict = field(default_factory=dict)          # label -> code
    artifacts: list = field(default_factory=list)     # idx -> artifact id
    witness: list = field(default_factory=list)       # idx -> witness id
    line_art: array = field(default_factory=lambda: array('i'))
    line_ids: list = field(default_factory=list)
    line_labels: list = field(default_factory=list)
    lines: list = field(default_factory=list)          # array('i') of codes; GAP/UNS negative

    def label(self, c: int) -> str:
        return SPECIAL.get(c) or self.labels[c]

    def art_lines(self) -> dict:
        out = defaultdict(list)
        for i, a in enumerate(self.line_art):
            out[a].append(i)
        return out


def load_field(db, field_name: str, art_index: dict | None = None) -> Corpus:
    """Canonical represented positions for one field, ordered by artifact, line ordinal, position.

    Reads occurrence/line/sequence_version/witness only (never ngram/posting/edge). Positions must be contiguous 0..L-1."""
    c = Corpus(field_name)
    if art_index is None:
        art_index = {}
    rows = db.execute(
        "SELECT w.artifact_id, w.id, l.id, l.label, o.position, o.kind, o.reading "
        "FROM occurrence o JOIN line l ON o.line_id=l.id JOIN sequence_version s ON s.id=l.sequence_id "
        "JOIN witness w ON w.id=s.witness_id WHERE s.field=? ORDER BY w.artifact_id, l.ordinal, o.position", [field_name])
    cur_line = None
    toks = None
    witness_of = {}
    while True:
        chunk = rows.fetchmany(200_000)
        if not chunk:
            break
        for art, wit, lid, llabel, pos, kind, reading in chunk:
            if lid != cur_line:
                a = art_index.setdefault(art, len(art_index))
                witness_of[a] = wit
                c.line_art.append(a)
                c.line_ids.append(lid)
                c.line_labels.append(llabel)
                toks = array('i')
                c.lines.append(toks)
                cur_line = lid
            if pos != len(toks):
                raise ValueError(f'non-contiguous positions in {lid}')
            if reading is None:
                toks.append(GAP if kind == 'gap' else UNS)
            else:
                code = c.code.get(reading)
                if code is None:
                    code = c.code[reading] = len(c.labels)
                    c.labels.append(reading)
                toks.append(code)
        check_rss('load_field')
    c.artifacts = [None] * len(art_index)
    for k, v in art_index.items():
        c.artifacts[v] = k
    c.witness = [witness_of.get(i) for i in range(len(art_index))]
    return c


# ---------------------------------------------------------------- families and split

class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def seq_key(corpus: Corpus, line_idx: list, min_readings: int):
    n = 0
    parts = []
    for li in line_idx:
        t = corpus.lines[li]
        n += sum(1 for x in t if x >= 0)
        parts.append(' '.join(corpus.label(x) for x in t))
    return h('\n'.join(parts)) if n >= min_readings else None


def primary_families(artifacts: list, alias_pairs: list, corpora: list) -> tuple[list, dict]:
    """Union of exact shared-alias claims and exact normalized duplicate sequences. Returns family index per artifact and evidence."""
    uf = UF(len(artifacts))
    idx = {a: i for i, a in enumerate(artifacts)}
    evidence = {'alias_groups': 0, 'alias_artifacts': 0, 'dup_groups': {}, 'dup_artifacts': {}}
    by_key = defaultdict(list)
    for art, key in alias_pairs:
        if art in idx:
            by_key[key].append(idx[art])
    for key, members in by_key.items():
        if len(members) > 1:
            evidence['alias_groups'] += 1
            evidence['alias_artifacts'] += len(members)
            for m in members[1:]:
                uf.union(members[0], m)
    for corpus in corpora:
        groups = defaultdict(list)
        for a, lines in corpus.art_lines().items():
            k = seq_key(corpus, lines, 10)
            if k:
                groups[k].append(a)
        dups = [g for g in groups.values() if len(g) > 1]
        evidence['dup_groups'][corpus.field] = len(dups)
        evidence['dup_artifacts'][corpus.field] = sum(len(g) for g in dups)
        for g in dups:
            for m in g[1:]:
                uf.union(g[0], m)
    roots = [uf.find(i) for i in range(len(artifacts))]
    return roots, evidence


def family_keys(artifacts: list, roots: list) -> list:
    """Family key = smallest artifact id in the family (stable, content-independent)."""
    best = {}
    for i, r in enumerate(roots):
        a = artifacts[i]
        if r not in best or a < best[r]:
            best[r] = a
    return [best[r] for r in roots]


def split_of(family_key: str) -> str:
    b = int(h(f'{SEED}:split:{family_key}')[:8], 16) % 100
    s = CONFIG['split']
    if s['train'][0] <= b <= s['train'][1]:
        return 'train'
    if s['dev'][0] <= b <= s['dev'][1]:
        return 'dev'
    return 'test'


def near_duplicates(corpus: Corpus, fam: list) -> dict:
    """Uncertain near-duplicate links between artifacts of different primary families (sensitivity only)."""
    cfg = CONFIG['families']['near_duplicate_sensitivity']
    k, max_df, min_shared, min_cont = cfg['shingle'], cfg['max_shingle_df'], cfg['min_shared'], cfg['min_containment']
    sets = defaultdict(set)
    for li, t in enumerate(corpus.lines):
        a = corpus.line_art[li]
        run = []
        for x in list(t) + [GAP]:
            if x >= 0:
                run.append(x)
                continue
            for i in range(len(run) - k + 1):
                sets[a].add(hash(tuple(run[i:i + k])))
            run = []
    post = defaultdict(list)
    for a, s in sets.items():
        for sh in s:
            post[sh].append(a)
    pair = defaultdict(int)
    for arts in post.values():
        if 2 <= len(arts) <= max_df:
            arts = sorted(arts)
            for i in range(len(arts)):
                for j in range(i + 1, len(arts)):
                    if fam[arts[i]] != fam[arts[j]]:
                        pair[(arts[i], arts[j])] += 1
    partners = defaultdict(set)
    edges = 0
    for (a, b), n in pair.items():
        if n >= min_shared and n / min(len(sets[a]), len(sets[b])) >= min_cont:
            partners[a].add(b)
            partners[b].add(a)
            edges += 1
    return {'partners': partners, 'edges': edges, 'artifacts_with_shingles': len(sets)}


@dataclass
class Universe:
    signs: Corpus
    atf: Corpus | None
    artifacts: list
    catalog: dict
    family: list           # family key per artifact idx
    split: list            # split per artifact idx
    evidence: dict
    near: dict | None = None


def load_universe(db, with_atf: bool = True, with_near: bool = True) -> Universe:
    art_index: dict = {}
    signs = load_field(db, 'signs', art_index)
    atf = load_field(db, 'atf', art_index) if with_atf else None
    # Artifacts that have neither field still need an index (none expected, recorded if present).
    for (a,) in db.execute('SELECT id FROM artifact ORDER BY id').fetchall():
        art_index.setdefault(a, len(art_index))
    artifacts = [None] * len(art_index)
    for k, v in art_index.items():
        artifacts[v] = k
    for c in (signs, atf):
        if c:
            c.artifacts = artifacts
            c.witness = c.witness + [None] * (len(artifacts) - len(c.witness))
    catalog = dict(db.execute('SELECT id, catalog_key FROM artifact').fetchall())
    aliases = db.execute("SELECT subject_id, object_key FROM claim WHERE predicate='catalog_alias'").fetchall()
    roots, ev = primary_families(artifacts, aliases, [c for c in (signs, atf) if c])
    fam = family_keys(artifacts, roots)
    split = [split_of(f) for f in fam]
    ev['families'] = len(set(fam))
    ev['artifacts'] = len(artifacts)
    u = Universe(signs, atf, artifacts, catalog, fam, split, ev)
    if with_near:
        fam_idx = {f: i for i, f in enumerate(sorted(set(fam)))}
        u.near = near_duplicates(signs, [fam_idx[f] for f in fam])
    return u


def split_manifest(u: Universe) -> dict:
    """Family -> split listing and its hash (written before any score is read)."""
    rows = sorted({(u.family[i], u.split[i]) for i in range(len(u.artifacts))})
    text = '\n'.join(f'{f}\t{s}' for f, s in rows)
    counts = defaultdict(lambda: {'families': 0, 'artifacts': 0, 'sign_positions': 0, 'derived_signs': 0})
    for f, s in rows:
        counts[s]['families'] += 1
    for i, s in enumerate(u.split):
        counts[s]['artifacts'] += 1
    for li, t in enumerate(u.signs.lines):
        s = u.split[u.signs.line_art[li]]
        counts[s]['sign_positions'] += len(t)
        counts[s]['derived_signs'] += sum(1 for x in t if x >= 0)
    return {'sha256': h(text), 'counts': dict(counts), 'listing': text}


def connect_backend(snapshot: Path = SNAPSHOT):
    """Pinned-backend read-only connection marked frozen-verified from the manifest that preflight verified.

    No re-hash: preflight hashed every file once and Run() refuses if the stat fingerprint changed since."""
    _, _, query = backend()
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    db = query.open_verified(snapshot, manifest)
    tmp = ROOT / 'out' / 'duckdb-tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    r = CONFIG['resources']
    db.execute(f"SET memory_limit='{r['duckdb_memory_limit']}'")
    db.execute(f"SET threads={int(r['duckdb_threads'])}")
    db.execute(f"SET temp_directory='{tmp}'")
    db.execute(f"SET max_temp_directory_size='{r['duckdb_max_temp_directory_size']}'")
    return db, manifest
