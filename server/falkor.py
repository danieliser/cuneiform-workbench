"""Disposable FalkorDB projection of a dataset's nodes/portable_edge views.

Canonical DuckDB/Parquet data stays authoritative. Each export is written once
under the UI's own runtime directory with SHA-256 receipts, and can be dropped
and reloaded at any time. The isolated FalkorDB instance is loopback-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any

from .bridge import Backend, Dataset, UserError
from .explore import PROPS, label_for

GRAPH_PREFIX = 'cuneiform_'
GRAPH_RE = re.compile(r'^cuneiform_[a-z0-9_]{1,80}$')
LABEL_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]{0,40}$')
REL_RE = re.compile(r'^[A-Z][A-Z_]{0,40}$')
KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,40}$')
MAX_EXPORT_NODES = 100_000
MAX_EXPORT_EDGES = 300_000
BATCH = 250
MAX_CYPHER_CHARS = 4000
MAX_RESULT_ROWS = 500
RO_TIMEOUT_MS = 3000
EXPORT_FORMAT = 'cuneiform-ui-graph-export-1'


class RespError(Exception):
    pass


class Resp:
    """Minimal RESP2 client (enough for GRAPH.* and PING); stdlib only."""

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile('rb')

    def close(self):
        try:
            self.file.close()
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def cmd(self, *args: str | int) -> Any:
        parts = [f'*{len(args)}\r\n'.encode()]
        for a in args:
            b = str(a).encode('utf-8')
            parts.append(b'$%d\r\n%s\r\n' % (len(b), b))
        self.sock.sendall(b''.join(parts))
        return self._read()

    def _read(self) -> Any:
        line = self.file.readline()
        if not line:
            raise RespError('connection closed')
        t, rest = line[:1], line[1:-2]
        if t == b'+':
            return rest.decode()
        if t == b'-':
            raise RespError(rest.decode('utf-8', 'replace'))
        if t == b':':
            return int(rest)
        if t == b'$':
            n = int(rest)
            if n < 0:
                return None
            data = self.file.read(n + 2)[:-2]
            return data.decode('utf-8', 'replace')
        if t == b'*':
            n = int(rest)
            return None if n < 0 else [self._read() for _ in range(n)]
        if t == b',':
            return float(rest)
        raise RespError('unsupported reply type')


def cy(v: Any) -> str:
    """Serialize a Python value as a Cypher literal for the CYPHER parameter header.

    Corpus strings are untrusted: everything is emitted as an escaped string
    literal; map keys are restricted identifiers.
    """
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        out = []
        for ch in v:
            if ch == '\\':
                out.append('\\\\')
            elif ch == '"':
                out.append('\\"')
            elif ch == '\n':
                out.append('\\n')
            elif ch == '\r':
                out.append('\\r')
            elif ch == '\t':
                out.append('\\t')
            elif ord(ch) < 0x20 or ord(ch) == 0x7f:
                out.append(' ')
            else:
                out.append(ch)
        return '"' + ''.join(out) + '"'
    if isinstance(v, (list, tuple)):
        return '[' + ','.join(cy(x) for x in v) + ']'
    if isinstance(v, dict):
        items = []
        for k, x in v.items():
            if not KEY_RE.match(k):
                raise ValueError('unsafe map key')
            items.append(f'{k}:{cy(x)}')
        return '{' + ','.join(items) + '}'
    return cy(str(v))


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


class Falkor:
    def __init__(self, backend: Backend, home: Path, host: str = '127.0.0.1', port: int = 26380, browser_port: int = 23001):
        self.b = backend
        self.home = home
        self.host, self.port, self.browser_port = host, port, browser_port
        self.exports = home / 'exports'
        self.loaded: dict[str, dict] = {}

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        out = {'host': self.host, 'port': self.port, 'browser_url': f'http://127.0.0.1:{self.browser_port}',
               'browser_login': {'host': 'localhost', 'port': 6379, 'note': 'Browser connects from inside the container; host port is ' + str(self.port)},
               'reachable': False}
        try:
            with Resp(self.host, self.port, timeout=2) as r:
                out['ping'] = r.cmd('PING')
                mods = r.cmd('MODULE', 'LIST')
                for m in mods or []:
                    d = dict(zip(m[::2], m[1::2]))
                    if d.get('name') == 'graph':
                        v = int(d.get('ver', 0))
                        out['falkordb_version'] = f'{v // 10000}.{(v // 100) % 100}.{v % 100}'
                graphs = r.cmd('GRAPH.LIST') or []
                out['graphs'] = [self._graph_info(r, g) for g in sorted(graphs) if GRAPH_RE.match(g)]
                out['foreign_graphs'] = len([g for g in graphs if not GRAPH_RE.match(g)])
                out['reachable'] = True
        except (OSError, RespError) as exc:
            out['error'] = f'{type(exc).__name__}: {exc}'
        return out

    def _graph_info(self, r: Resp, g: str) -> dict:
        try:
            res = r.cmd('GRAPH.RO_QUERY', g, 'MATCH (p:ProjectionReceipt) RETURN p.receipt LIMIT 1', 'TIMEOUT', 2000)
            rows = res[1] if isinstance(res, list) and len(res) >= 2 else []
            receipt = json.loads(rows[0][0]) if rows and rows[0] and rows[0][0] else None
        except (RespError, ValueError, IndexError, TypeError):
            receipt = None
        return {'graph': g, 'receipt': receipt}

    # -- export ----------------------------------------------------------------
    def export(self, ds: Dataset) -> dict:
        """Write nodes/edges JSONL once, content-addressed, with a hash receipt."""
        def work(db):
            n_nodes = db.execute('SELECT count(*) FROM nodes').fetchone()[0]
            n_edges = db.execute('SELECT count(*) FROM portable_edge').fetchone()[0]
            if n_nodes > MAX_EXPORT_NODES or n_edges > MAX_EXPORT_EDGES:
                raise UserError(f'graph too large for UI projection ({n_nodes} nodes / {n_edges} edges)')
            nodes = []
            for kind, (table, cols) in PROPS.items():
                cur = db.execute(f"SELECT id,{','.join(cols)} FROM {table} ORDER BY id")
                names = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    p = dict(zip(names, row))
                    props = {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)) for k, v in p.items()}
                    props['kind'] = kind
                    props['display'] = label_for(kind, props)
                    nodes.append(props)
            nodes.sort(key=lambda x: x['id'])
            edges = [{'id': r[0], 'source_id': r[1], 'target_id': r[2], 'kind': r[3], 'source_record_id': r[4], 'qualification': str(r[5])}
                     for r in db.execute('SELECT id, source_id, target_id, kind, source_record_id, qualification FROM portable_edge ORDER BY id').fetchall()]
            registry = self.b.projections.rows(db, 'SELECT * FROM projection_registry')
            bindings = self.b.projections.rows(db, 'SELECT DISTINCT snapshot_id, raw_sha256 FROM source_binding ORDER BY 1, 2')
            return nodes, edges, registry, bindings, n_nodes
        nodes, edges, registry, bindings, n_nodes = self.b.run(ds, work, timeout=60)
        if len(nodes) != n_nodes:
            raise UserError('node property export does not cover nodes view; backend schema changed')
        node_bytes = ''.join(json.dumps(n, ensure_ascii=False, sort_keys=True) + '\n' for n in nodes).encode()
        edge_bytes = ''.join(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n' for e in edges).encode()
        nh, eh = hashlib.sha256(node_bytes).hexdigest(), hashlib.sha256(edge_bytes).hexdigest()
        backend = {k: self.b.info[k] for k in ('commit', 'dirty', 'versions', 'compatibility')}
        export_id = hashlib.sha256(json.dumps([EXPORT_FORMAT, ds.key, nh, eh, backend['versions']], sort_keys=True).encode()).hexdigest()[:24]
        receipt = {'format': EXPORT_FORMAT, 'export_id': export_id, 'dataset': ds.key, 'dataset_kind': ds.kind,
                   'source_bindings': bindings, 'projection_registry': registry, 'backend': backend,
                   'files': {'nodes.jsonl': nh, 'edges.jsonl': eh}, 'counts': {'nodes': len(nodes), 'edges': len(edges)},
                   'authority': 'Derived, disposable. Canonical data is the backend DuckDB/Parquet snapshot or in-memory parse of the hashed inputs.'}
        target = self.exports / re.sub(r'[^a-z0-9-]', '-', ds.key.lower()) / export_id
        if target.exists():
            for name, expected in receipt['files'].items():
                if _sha_file(target / name) != expected:
                    raise UserError(f'existing export {export_id} failed hash check: {name}')
            receipt = json.loads((target / 'receipt.json').read_text())
            receipt['reused'] = True
        else:
            stage = target.parent / f'.pending-{export_id}-{os.getpid()}'
            stage.mkdir(parents=True, exist_ok=False)
            (stage / 'nodes.jsonl').write_bytes(node_bytes)
            (stage / 'edges.jsonl').write_bytes(edge_bytes)
            receipt['created_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            (stage / 'receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
            for p in stage.iterdir():
                p.chmod(0o444)
            stage.rename(target)
            receipt['reused'] = False
        receipt['path'] = str(target)
        return {'receipt': receipt, 'nodes': nodes, 'edges': edges}

    def graph_name(self, ds: Dataset, export_id: str) -> str:
        return GRAPH_PREFIX + re.sub(r'[^a-z0-9]', '_', ds.key.lower())[:40] + '_' + export_id[:12]

    # -- load -------------------------------------------------------------------
    def load(self, ds: Dataset) -> dict:
        ex = self.export(ds)
        receipt, nodes, edges = ex['receipt'], ex['nodes'], ex['edges']
        g = self.graph_name(ds, receipt['export_id'])
        started = time.time()
        try:
            with Resp(self.host, self.port, timeout=30) as r:
                existing = r.cmd('GRAPH.LIST') or []
                if g in existing:
                    info = self._graph_info(r, g)
                    if info['receipt'] and info['receipt'].get('export_id') == receipt['export_id']:
                        counts = self._counts(r, g)
                        if counts == receipt['counts']:
                            return {'graph': g, 'reused': True, 'counts': counts, 'receipt': receipt}
                    r.cmd('GRAPH.DELETE', g)  # our own disposable projection
                by_kind: dict[str, list[dict]] = {}
                for n in nodes:
                    by_kind.setdefault(n['kind'], []).append(n)
                kind_of = {n['id']: n['kind'] for n in nodes}
                for kind, group in by_kind.items():
                    if not LABEL_RE.match(kind):
                        raise UserError('unsafe node label')
                    for i in range(0, len(group), BATCH):
                        r.cmd('GRAPH.QUERY', g, f'CYPHER rows={cy(group[i:i + BATCH])} UNWIND $rows AS row CREATE (n:{kind}) SET n = row')
                    r.cmd('GRAPH.QUERY', g, f'CREATE INDEX FOR (n:{kind}) ON (n.id)')
                by_rel: dict[tuple[str, str, str], list[dict]] = {}
                for e in edges:
                    sk, tk = kind_of.get(e['source_id']), kind_of.get(e['target_id'])
                    if sk is None or tk is None:
                        raise UserError('dangling portable edge; export refused')
                    by_rel.setdefault((e['kind'], sk, tk), []).append(e)
                for (rel, sk, tk), group in by_rel.items():
                    if not REL_RE.match(rel):
                        raise UserError('unsafe relationship type')
                    for i in range(0, len(group), BATCH):
                        rows = [{'id': e['id'], 's': e['source_id'], 't': e['target_id'], 'src': e['source_record_id'], 'q': e['qualification']} for e in group[i:i + BATCH]]
                        r.cmd('GRAPH.QUERY', g, f'CYPHER rows={cy(rows)} UNWIND $rows AS row MATCH (a:{sk} {{id: row.s}}), (b:{tk} {{id: row.t}}) '
                                                f'CREATE (a)-[:{rel} {{id: row.id, source_record_id: row.src, qualification: row.q}}]->(b)')
                public = {k: receipt[k] for k in ('format', 'export_id', 'dataset', 'dataset_kind', 'files', 'counts', 'backend', 'source_bindings', 'authority')}
                r.cmd('GRAPH.QUERY', g, f'CYPHER p={cy({"export_id": receipt["export_id"], "dataset": ds.key, "receipt": json.dumps(public, sort_keys=True)})} '
                                        'CREATE (x:ProjectionReceipt) SET x = $p')
                counts = self._counts(r, g)
        except (OSError, RespError) as exc:
            raise UserError(f'FalkorDB load failed: {exc}') from exc
        if counts != receipt['counts']:
            raise UserError(f'loaded counts {counts} differ from export {receipt["counts"]}')
        return {'graph': g, 'reused': False, 'counts': counts, 'seconds': round(time.time() - started, 2), 'receipt': receipt}

    def _counts(self, r: Resp, g: str) -> dict:
        n = r.cmd('GRAPH.RO_QUERY', g, 'MATCH (n) WHERE NOT n:ProjectionReceipt RETURN count(n)', 'TIMEOUT', 5000)[1][0][0]
        e = r.cmd('GRAPH.RO_QUERY', g, 'MATCH ()-[e]->() RETURN count(e)', 'TIMEOUT', 5000)[1][0][0]
        return {'nodes': int(n), 'edges': int(e)}

    def drop(self, graph: str) -> dict:
        if not isinstance(graph, str) or not GRAPH_RE.match(graph):
            raise UserError('only cuneiform_* projection graphs can be dropped')
        with Resp(self.host, self.port) as r:
            if graph not in (r.cmd('GRAPH.LIST') or []):
                raise UserError('graph not found')
            r.cmd('GRAPH.DELETE', graph)
        return {'dropped': graph}

    # -- read-only Cypher ---------------------------------------------------------------
    def ro_query(self, graph: str, query: str) -> dict:
        if not isinstance(graph, str) or not GRAPH_RE.match(graph):
            raise UserError('choose a loaded cuneiform_* projection graph')
        if not isinstance(query, str) or not 1 <= len(query) <= MAX_CYPHER_CHARS:
            raise UserError(f'query must be 1–{MAX_CYPHER_CHARS} characters')
        started = time.time()
        try:
            with Resp(self.host, self.port, timeout=RO_TIMEOUT_MS / 1000 + 3) as r:
                if graph not in (r.cmd('GRAPH.LIST') or []):
                    raise UserError('graph not loaded')
                res = r.cmd('GRAPH.RO_QUERY', graph, query, 'TIMEOUT', RO_TIMEOUT_MS)
        except RespError as exc:
            raise UserError(f'FalkorDB: {exc}') from exc
        except OSError as exc:
            raise UserError(f'FalkorDB unreachable: {exc}') from exc
        header, rows, stats = (res + [[], [], []])[:3] if len(res) == 3 else ([], [], res[0] if res else [])
        columns = [h[1] if isinstance(h, list) and len(h) == 2 else h for h in header]
        return {'graph': graph, 'columns': columns, 'rows': rows[:MAX_RESULT_ROWS], 'row_count': len(rows),
                'truncated': len(rows) > MAX_RESULT_ROWS, 'stats': stats, 'ms': round((time.time() - started) * 1000),
                'mode': 'GRAPH.RO_QUERY (read-only) on disposable projection; not canonical data'}
