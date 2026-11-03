"""Disposable FalkorDB projection of a dataset version's nodes/portable_edge views.

Canonical DuckDB/Parquet data stays authoritative. Each export is written once under the
UI's own runtime directory with SHA-256 receipts. Every load rebuilds the graph and then
reads it back to prove its contents equal the export (not just its counts).

Security model (review F-1, F-7, F-9):
* The FalkorDB server runs on a Docker ``--internal`` network with no egress; a fixed-code
  relay publishes it on 127.0.0.1 only. The official FalkorDB Browser is disabled.
* Redis ACL: the default user is off. ``cuneiform-ro`` may only PING/GRAPH.RO_QUERY/GRAPH.LIST;
  ``cuneiform-rw`` additionally GRAPH.QUERY/GRAPH.DELETE. Passwords live in 0600 files.
* Arbitrary Cypher is not accepted from the browser. ``GRAPH.RO_QUERY`` still allows
  ``LOAD CSV FROM 'https://…'`` (outbound connections) and unbounded ``range()`` lists that
  OOM-kill the server regardless of QUERY_MEM_CAPACITY. Only allowlisted, parameterized,
  LIMIT-bounded templates run.
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

from .bridge import NODE_RE, TOKEN_RE, Backend, Dataset, UserError
from .explore import PROPS, label_for

GRAPH_PREFIX = 'cuneiform_'
GRAPH_RE = re.compile(r'^cuneiform_[a-z0-9_]{1,80}$')
LABEL_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]{0,40}$')
REL_RE = re.compile(r'^[A-Z][A-Z_]{0,40}$')
KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,40}$')
EXPORT_ID_RE = re.compile(r'^[0-9a-f]{24}$')
MAX_EXPORT_NODES = 150_000
MAX_EXPORT_EDGES = 400_000
BATCH = 250
READBACK_PAGE = 5000  # below the server RESULTSET_SIZE (10000)
RESULTSET_SIZE = 10000
MAX_TEMPLATE_ROWS = 500
RO_TIMEOUT_MS = 3000
EXPORT_FORMAT = 'cuneiform-ui-graph-export-2'
USERS = ('cuneiform-ro', 'cuneiform-rw')

# Allowlisted read-only templates. Parameters are typed and validated; strings are emitted as escaped
# Cypher literals; ints are validated then inlined. No template uses LOAD CSV, CALL, range() or UNWIND.
TEMPLATES: dict[str, dict] = {
    'node-kinds': {'label': 'Node kinds', 'params': {},
                   'cypher': 'MATCH (n) RETURN labels(n)[0] AS kind, count(*) AS n ORDER BY n DESC LIMIT {limit}'},
    'edge-kinds': {'label': 'Relationship kinds', 'params': {},
                   'cypher': 'MATCH ()-[e]->() RETURN type(e) AS kind, count(*) AS n ORDER BY n DESC LIMIT {limit}'},
    'adjacent-readings': {'label': 'Frequent adjacent readings (NEXT)', 'params': {},
                          'cypher': 'MATCH (a:SignOccurrence)-[:NEXT]->(b:SignOccurrence) WHERE a.reading IS NOT NULL AND b.reading IS NOT NULL '
                                    'RETURN a.reading AS first, b.reading AS second, count(*) AS n ORDER BY n DESC, first, second LIMIT {limit}'},
    'reading-pair': {'label': 'Occurrences of reading A followed by B', 'params': {'a': 'token', 'b': 'token'},
                     'cypher': 'MATCH (a:SignOccurrence {reading: $a})-[:NEXT]->(b:SignOccurrence {reading: $b}) '
                               'RETURN a.id AS first, b.id AS second, a.line_id AS line ORDER BY first LIMIT {limit}'},
    'reading-pair-count': {'label': 'Count of reading A followed by B', 'params': {'a': 'token', 'b': 'token'},
                           'cypher': 'MATCH (a:SignOccurrence {reading: $a})-[:NEXT]->(b:SignOccurrence {reading: $b}) RETURN count(*) AS n LIMIT {limit}'},
    'artifact-lines': {'label': 'Artifacts → lines per sequence', 'params': {},
                       'cypher': 'MATCH (w:Witness)-[:ON_ARTIFACT]->(a:Artifact), (w)-[:HAS_SEQUENCE]->(s:SequenceVersion)-[:HAS_LINE]->(l:Line) '
                                 'RETURN a.catalog_key AS artifact, s.field AS field, count(l) AS lines ORDER BY artifact, field LIMIT {limit}'},
    'alias-claims': {'label': 'Catalog alias claims (no merges)', 'params': {},
                     'cypher': 'MATCH (c:Claim)-[:CLAIM_SUBJECT]->(a:Artifact) RETURN a.catalog_key AS artifact, c.object_key AS claims, c.status AS status '
                               'ORDER BY artifact, claims LIMIT {limit}'},
    'node-neighbors': {'label': 'Neighbors of a node ID', 'params': {'id': 'node'},
                       'cypher': 'MATCH (n {id: $id})-[e]-(m) RETURN type(e) AS rel, startNode(e) = n AS outgoing, labels(m)[0] AS node_kind, m.id AS id, '
                                 'm.display AS display ORDER BY rel, id LIMIT {limit}'},
    'projection-receipt': {'label': 'Projection receipt', 'params': {},
                           'cypher': 'MATCH (p:ProjectionReceipt) RETURN p.export_id AS export_id, p.dataset AS dataset, p.dataset_version AS version, '
                                     'p.receipt AS receipt LIMIT {limit}'},
}
_FORBIDDEN = re.compile(r'LOAD\s+CSV|\bCALL\b|\brange\s*\(|\bUNWIND\b|\bCREATE\b|\bMERGE\b|\bSET\b|\bDELETE\b|\bREMOVE\b|\bFOREACH\b', re.I)
for _t in TEMPLATES.values():  # fail fast at import if a template ever gains an unsafe capability
    assert not _FORBIDDEN.search(_t['cypher']), _t['cypher']


class RespError(Exception):
    pass


class Resp:
    """Minimal RESP2 client (enough for AUTH, GRAPH.* and PING); stdlib only."""

    def __init__(self, host: str, port: int, timeout: float = 10.0, auth: tuple[str, str] | None = None):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile('rb')
        if auth:
            try:
                self.cmd('AUTH', auth[0], auth[1])
            except BaseException:
                self.close()
                raise

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


def cy_str(v: str) -> str:
    """The exact string FalkorDB stores for ``cy(v)``: control characters other than \\n \\r \\t become spaces."""
    return ''.join(' ' if (ord(ch) < 0x20 and ch not in '\n\r\t') or ord(ch) == 0x7f else ch for ch in v)


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
        for ch in cy_str(v):
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


def _norm(v: Any) -> Any:
    """Value as stored in / returned by FalkorDB (strings normalized; ints kept)."""
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        return repr(v)
    return cy_str(str(v))


def node_columns(kind: str) -> list[str]:
    return ['id'] + PROPS[kind][1] + ['node_kind', 'display']


class Falkor:
    def __init__(self, backend: Backend, home: Path, host: str = '127.0.0.1', port: int = 26380, secret_dir: Path | None = None):
        self.b = backend
        self.home = home
        self.host, self.port = host, port
        self.exports = home / 'exports'
        self.secret_dir = secret_dir or Path(os.environ.get('CUNEIFORM_FALKORDB_SECRET_DIR', str(home / 'secrets')))
        prefix = os.environ.get('CUNEIFORM_FALKORDB_GRAPH_PREFIX', GRAPH_PREFIX)
        self.prefix = prefix if re.match(r'^cuneiform_[a-z_]{0,20}$', prefix) else GRAPH_PREFIX

    # -- connection ------------------------------------------------------------
    def _secret(self, user: str) -> str:
        p = self.secret_dir / f'falkordb-{user.split("-")[1]}.password'
        try:
            st = p.stat()
            if st.st_mode & 0o077:
                raise UserError(f'FalkorDB secret file has unsafe permissions; run bin/cuneiform-ui falkordb-up ({p.name})')
            return p.read_text().strip()
        except OSError as exc:
            raise UserError(f'FalkorDB credentials not provisioned ({p.name}); run bin/cuneiform-ui falkordb-up') from exc

    def conn(self, user: str = 'cuneiform-ro', timeout: float = 10.0) -> Resp:
        assert user in USERS
        return Resp(self.host, self.port, timeout=timeout, auth=(user, self._secret(user)))

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        out = {'host': self.host, 'port': self.port, 'reachable': False,
               'browser': {'enabled': False,
                           'reason': ('Disabled: the bundled FalkorDB Browser connects to any host the user types and does not check the Host header '
                                      '(DNS rebinding). It stays off until it can sit behind a loopback Host-checking proxy.')},
               'console': {'arbitrary_cypher': False,
                           'reason': ('Arbitrary Cypher is disabled: GRAPH.RO_QUERY still allows LOAD CSV over https (outbound connections) and '
                                      'unbounded range() lists that OOM-kill the server despite QUERY_MEM_CAPACITY. Allowlisted bounded templates only.')},
               'templates': [{'id': k, 'label': t['label'], 'params': t['params']} for k, t in TEMPLATES.items()],
               'isolation': 'internal Docker network without egress; loopback relay; ACL users with 0600 secret files'}
        try:
            with self.conn('cuneiform-ro', timeout=2) as r:
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
        except UserError as exc:
            out['error'] = str(exc)
        except (OSError, RespError) as exc:
            out['error'] = (f'{type(exc).__name__}: {exc}. If the container died (for example OOM), '
                            'run bin/cuneiform-ui falkordb-up; graphs reload from disk and can be rebuilt.')
        return out

    def _graph_info(self, r: Resp, g: str) -> dict:
        try:
            res = r.cmd('GRAPH.RO_QUERY', g, 'MATCH (p:ProjectionReceipt) RETURN p.receipt LIMIT 1', 'TIMEOUT', 2000)
            rows = res[1] if isinstance(res, list) and len(res) >= 2 else []
            receipt = json.loads(rows[0][0]) if rows and rows[0] and rows[0][0] else None
        except (RespError, ValueError, IndexError, TypeError):
            receipt = None
        refusal = receipt_refusal(g, receipt)
        return {'graph': g, 'receipt': receipt, 'verifiable': refusal is None,
                'verify_status': refusal['status'] if refusal else None, 'verify_note': refusal['problems'][0] if refusal else None}

    # -- export ----------------------------------------------------------------
    def export(self, ds: Dataset) -> dict:
        """Write nodes/edges JSONL once, content-addressed by dataset version and contents, with a hash receipt."""
        def work(db):
            n_nodes = db.execute('SELECT count(*) FROM nodes').fetchone()[0]
            n_edges = db.execute('SELECT count(*) FROM portable_edge').fetchone()[0]
            if n_nodes > MAX_EXPORT_NODES or n_edges > MAX_EXPORT_EDGES:
                raise UserError(f'graph too large for the UI projection ({n_nodes} nodes / {n_edges} edges; cap {MAX_EXPORT_NODES}/{MAX_EXPORT_EDGES})')
            nodes = []
            for kind, (table, cols) in PROPS.items():
                cur = db.execute(f"SELECT id,{','.join(cols)} FROM {table} ORDER BY id")
                names = [d[0] for d in cur.description]
                while batch := cur.fetchmany(5000):
                    for row in batch:
                        p = dict(zip(names, row))
                        props = {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)) for k, v in p.items()}
                        props['node_kind'] = kind  # label; SignOccurrence keeps its own 'kind' property (review fix)
                        props['display'] = label_for(kind, props)
                        nodes.append(props)
            nodes.sort(key=lambda x: x['id'])
            edges = [{'id': r[0], 'source_id': r[1], 'target_id': r[2], 'kind': r[3], 'source_record_id': r[4], 'qualification': str(r[5])}
                     for r in db.execute('SELECT id, source_id, target_id, kind, source_record_id, qualification FROM portable_edge ORDER BY id').fetchall()]
            registry = self.b.projections.rows(db, 'SELECT * FROM projection_registry')
            bindings = self.b.projections.rows(db, 'SELECT DISTINCT snapshot_id, raw_sha256 FROM source_binding ORDER BY 1, 2')
            return nodes, edges, registry, bindings, n_nodes
        nodes, edges, registry, bindings, n_nodes = self.b.run(ds, work, timeout=120)
        if len(nodes) != n_nodes:
            raise UserError('node property export does not cover nodes view; backend schema changed')
        node_bytes = ''.join(json.dumps(n, ensure_ascii=False, sort_keys=True) + '\n' for n in nodes).encode()
        edge_bytes = ''.join(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n' for e in edges).encode()
        nh, eh = hashlib.sha256(node_bytes).hexdigest(), hashlib.sha256(edge_bytes).hexdigest()
        backend = {k: self.b.info[k] for k in ('commit', 'dirty', 'versions', 'compatibility')}
        export_id = hashlib.sha256(json.dumps([EXPORT_FORMAT, ds.key, ds.version, nh, eh, backend['versions']], sort_keys=True).encode()).hexdigest()[:24]
        receipt = {'format': EXPORT_FORMAT, 'export_id': export_id, 'dataset': ds.key, 'dataset_kind': ds.kind, 'dataset_version': ds.version,
                   'selection': {k: ds.selection.get(k) for k in ('kind', 'label', 'scope_note')},
                   'source_bindings': bindings, 'projection_registry': registry, 'backend': backend,
                   'files': {'nodes.jsonl': nh, 'edges.jsonl': eh}, 'counts': {'nodes': len(nodes), 'edges': len(edges)},
                   'content_digest': content_digest(nodes, edges),
                   'authority': 'Derived, disposable. Canonical data is the backend DuckDB/Parquet snapshot or in-memory parse of the hashed inputs.'}
        target = self._export_dir(ds.key, export_id)
        if target.exists():
            for name, expected in receipt['files'].items():
                if _sha_file(target / name) != expected:
                    raise UserError(f'existing export {export_id} failed hash check: {name}')
            receipt = json.loads((target / 'receipt.json').read_text())
            receipt['content_digest'] = content_digest(nodes, edges)  # current comparison scheme; the stored file stays write-once
            receipt['reused_export_files'] = True
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
            receipt['reused_export_files'] = False
        receipt['path'] = str(target)
        return {'receipt': receipt, 'nodes': nodes, 'edges': edges}

    def _export_dir(self, dataset: str, export_id: str) -> Path:
        if not EXPORT_ID_RE.match(export_id):
            raise UserError('invalid export ID')
        return self.exports / re.sub(r'[^a-z0-9-]', '-', dataset.lower()) / export_id

    def graph_name(self, ds: Dataset, export_id: str) -> str:
        return self.prefix + re.sub(r'[^a-z0-9]', '_', ds.key.lower())[:40] + '_' + export_id[:12]

    # -- load (always rebuild, then prove contents) --------------------------------------
    def load(self, ds: Dataset) -> dict:
        ex = self.export(ds)
        receipt, nodes, edges = ex['receipt'], ex['nodes'], ex['edges']
        g = self.graph_name(ds, receipt['export_id'])
        started = time.time()
        try:
            with self.conn('cuneiform-rw', timeout=60) as r:
                if g in (r.cmd('GRAPH.LIST') or []):
                    r.cmd('GRAPH.DELETE', g)  # our own disposable projection; never trust a previous copy
                by_kind: dict[str, list[dict]] = {}
                for n in nodes:
                    by_kind.setdefault(n['node_kind'], []).append(n)
                kind_of = {n['id']: n['node_kind'] for n in nodes}
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
                public = {k: receipt[k] for k in ('format', 'export_id', 'dataset', 'dataset_kind', 'dataset_version', 'selection', 'files',
                                                  'counts', 'backend', 'source_bindings', 'authority', 'content_digest')}
                r.cmd('GRAPH.QUERY', g, f'CYPHER p={cy({"export_id": receipt["export_id"], "dataset": ds.key, "dataset_version": ds.version, "receipt": json.dumps(public, sort_keys=True)})} '
                                        'CREATE (x:ProjectionReceipt) SET x = $p')
        except (OSError, RespError) as exc:
            raise UserError(f'FalkorDB load failed: {exc}') from exc
        check = self.verify_graph(g, nodes=nodes, edges=edges, receipt=receipt)
        if not check['content_verified']:
            dropped = self._drop_quietly(g)  # never leave a just-built graph that failed its own read-back
            raise UserError(f'loaded graph contents differ from the export ({"graph dropped" if dropped else "graph could NOT be dropped"}): '
                            f'{check["problems"][:3]}')
        return {'graph': g, 'rebuilt': True, 'counts': check['counts'], 'content_verified': True, 'content_digest': check['digest'],
                'seconds': round(time.time() - started, 2), 'receipt': receipt}

    def verify_graph(self, graph: str, nodes: list[dict] | None = None, edges: list[dict] | None = None, receipt: dict | None = None) -> dict:
        """Read every node and edge back and compare with the export files (review F-8)."""
        if not isinstance(graph, str) or not GRAPH_RE.match(graph):
            raise UserError('only cuneiform_* projection graphs can be verified')
        try:
            with self.conn('cuneiform-ro', timeout=30) as r:
                if graph not in (r.cmd('GRAPH.LIST') or []):
                    raise UserError('graph not loaded')
                if receipt is None:
                    receipt = self._graph_info(r, graph)['receipt']
                    refusal = receipt_refusal(graph, receipt)
                    if refusal:
                        return refusal  # typed, read-only: the old graph is never modified here (review N-5)
                if nodes is None or edges is None:
                    target = self._export_dir(receipt['dataset'], receipt['export_id'])
                    try:
                        for name, expected in receipt['files'].items():
                            if _sha_file(target / name) != expected:
                                return unverified(graph, 'export-mismatch', [f'export file {name} hash mismatch'])
                        nodes = [json.loads(x) for x in (target / 'nodes.jsonl').read_text().splitlines()]
                        edges = [json.loads(x) for x in (target / 'edges.jsonl').read_text().splitlines()]
                    except FileNotFoundError:
                        return unverified(graph, 'export-missing', ['the export files this graph was loaded from are gone; rebuild from the current dataset'])
                got_nodes, got_edges, extra = self._readback(r, graph, nodes, edges)
        except (OSError, RespError) as exc:
            raise UserError(f'FalkorDB verify failed: {exc}') from exc
        want = content_digest(nodes, edges)
        have = digest_rows(got_nodes, got_edges)
        problems = list(extra)
        if len(got_nodes) != len(nodes):
            problems.append(f'node count {len(got_nodes)} != {len(nodes)}')
        if len(got_edges) != len(edges):
            problems.append(f'edge count {len(got_edges)} != {len(edges)}')
        if have != want:
            problems += row_diffs('node', node_rows(nodes), got_nodes) + row_diffs('edge', edge_rows(edges), got_edges)
            problems.append('content digest differs (values, labels or property sets)')
        return {'graph': graph, 'content_verified': not problems, 'status': 'verified' if not problems else 'content-differs',
                'digest': have, 'expected_digest': want, 'problems': problems, 'compared': COMPARED,
                'counts': {'nodes': len(got_nodes), 'edges': len(got_edges)}}

    def _readback(self, r: Resp, g: str, nodes: list[dict], edges: list[dict]) -> tuple[list, list, list]:
        """Read every node and edge back with its labels and full property-key set (review N-6).

        Values are read for the exported columns; ``keys()`` proves no property was added or removed, and
        ``labels()`` proves no label was added. The receipt node is checked separately.
        """
        kinds = sorted({n['node_kind'] for n in nodes})
        extra: list[str] = []
        got_nodes: list = []
        q = ('MATCH (n) WHERE NOT n:ProjectionReceipt AND NOT (' + ' OR '.join(f'n:{k}' for k in kinds or ['ProjectionReceipt']) +
             ') RETURN n.id, labels(n) LIMIT 10')
        res = r.cmd('GRAPH.RO_QUERY', g, q, 'TIMEOUT', RO_TIMEOUT_MS)
        got_nodes += [[row[0], 'unexpected:' + '|'.join(sorted(row[1] or []))] for row in (res[1] if len(res) == 3 else [])]
        for kind in kinds:
            if not LABEL_RE.match(kind):
                raise UserError('unsafe node label')
            ret = ', '.join(f'n.{c}' for c in node_columns(kind))
            skip = 0
            while True:
                res = r.cmd('GRAPH.RO_QUERY', g, f'MATCH (n:{kind}) RETURN {ret}, labels(n), keys(n) ORDER BY n.id SKIP {skip} LIMIT {READBACK_PAGE}',
                            'TIMEOUT', RO_TIMEOUT_MS)
                rows = res[1] if len(res) == 3 else []
                got_nodes += [[row[0], kind] + row[1:-2] + [_shape(row[-2]), _shape(row[-1])] for row in rows]
                if len(rows) < READBACK_PAGE:
                    break
                skip += READBACK_PAGE
        got_edges: list = []
        skip = 0
        while True:
            res = r.cmd('GRAPH.RO_QUERY', g, f'MATCH (a)-[e]->(b) RETURN e.id, a.id, b.id, type(e), e.source_record_id, e.qualification, keys(e) '
                                             f'ORDER BY e.id SKIP {skip} LIMIT {READBACK_PAGE}', 'TIMEOUT', RO_TIMEOUT_MS)
            rows = res[1] if len(res) == 3 else []
            got_edges += [list(row[:-1]) + [_shape(row[-1])] for row in rows]
            if len(rows) < READBACK_PAGE:
                break
            skip += READBACK_PAGE
        res = r.cmd('GRAPH.RO_QUERY', g, 'MATCH (p:ProjectionReceipt) RETURN labels(p), keys(p) LIMIT 3', 'TIMEOUT', RO_TIMEOUT_MS)
        receipts = res[1] if len(res) == 3 else []
        if len(receipts) != 1:
            extra.append(f'{len(receipts)} ProjectionReceipt nodes (expected exactly 1)')
        for row in receipts:
            if _shape(row[0]) != 'ProjectionReceipt' or _shape(row[1]) != RECEIPT_KEYS:
                extra.append(f'ProjectionReceipt node has labels [{_shape(row[0])}] and properties [{_shape(row[1])}]; expected [ProjectionReceipt] / [{RECEIPT_KEYS}]')
        got_nodes.sort(key=lambda x: (str(x[0]), x[1]))
        got_edges.sort(key=lambda x: str(x[0]))
        return got_nodes, got_edges, extra

    def _drop_quietly(self, graph: str) -> bool:
        try:
            self.drop(graph)
            return True
        except UserError:
            return False

    def drop(self, graph: str) -> dict:
        if not isinstance(graph, str) or not GRAPH_RE.match(graph):
            raise UserError('only cuneiform_* projection graphs can be dropped')
        try:
            with self.conn('cuneiform-rw') as r:
                if graph not in (r.cmd('GRAPH.LIST') or []):
                    raise UserError('graph not found')
                r.cmd('GRAPH.DELETE', graph)
        except (OSError, RespError) as exc:
            raise UserError(f'FalkorDB: {exc}') from exc
        return {'dropped': graph}

    # -- allowlisted read-only templates --------------------------------------------------
    def template(self, graph: str, template_id: str, params: dict | None, limit=None) -> dict:
        if not isinstance(graph, str) or not GRAPH_RE.match(graph):
            raise UserError('choose a loaded cuneiform_* projection graph')
        t = TEMPLATES.get(template_id) if isinstance(template_id, str) else None
        if t is None:
            raise UserError('unknown query template')
        params = params if isinstance(params, dict) else {}
        if set(params) - set(t['params']):
            raise UserError('unexpected template parameter')
        try:
            lim = int(limit if limit not in (None, '') else 50)
        except (TypeError, ValueError):
            raise UserError('limit must be an integer')
        if isinstance(limit, bool) or not 1 <= lim <= MAX_TEMPLATE_ROWS:
            raise UserError(f'limit must be 1–{MAX_TEMPLATE_ROWS}')
        header = []
        for name, typ in t['params'].items():
            v = params.get(name)
            if typ == 'token' and not (isinstance(v, str) and TOKEN_RE.match(v)):
                raise UserError(f'{name}: 1–64 visible characters')
            if typ == 'node' and not (isinstance(v, str) and NODE_RE.match(v)):
                raise UserError(f'{name}: node ID required')
            header.append(f'{name}={cy(v)}')
        query = t['cypher'].replace('{limit}', str(lim + 1))  # one extra row proves truncation exactly
        if header:
            query = 'CYPHER ' + ' '.join(header) + ' ' + query
        started = time.time()
        try:
            with self.conn('cuneiform-ro', timeout=RO_TIMEOUT_MS / 1000 + 3) as r:
                if graph not in (r.cmd('GRAPH.LIST') or []):
                    raise UserError('graph not loaded')
                res = r.cmd('GRAPH.RO_QUERY', graph, query, 'TIMEOUT', RO_TIMEOUT_MS)
        except RespError as exc:
            raise UserError(f'FalkorDB: {exc}') from exc
        except OSError as exc:
            raise UserError(f'FalkorDB unreachable: {exc}. Run bin/cuneiform-ui falkordb-up.') from exc
        header_row, rows = (res[0], res[1]) if len(res) == 3 else ([], [])
        columns = [h[1] if isinstance(h, list) and len(h) == 2 else h for h in header_row]
        return {'graph': graph, 'template': template_id, 'label': t['label'], 'params': params, 'columns': columns,
                'rows': rows[:lim], 'row_count': min(len(rows), lim), 'limit': lim, 'truncated': len(rows) > lim,
                'server_cap_hit': len(rows) >= RESULTSET_SIZE, 'ms': round((time.time() - started) * 1000),
                'mode': 'allowlisted template via GRAPH.RO_QUERY (cuneiform-ro ACL user) on a disposable projection; not canonical data'}


COMPARED = ('every node: exported column values, its exact label set and its exact property-key set; every edge: endpoints, type, '
            'exported values and exact property-key set; exactly one ProjectionReceipt node with its fixed shape')
RECEIPT_KEYS = 'dataset|dataset_version|export_id|receipt'


def _shape(names) -> str:
    """Order-independent label/key set (FalkorDB does not store null properties, so exported nulls have no key).

    FalkorDB's verbose reply renders a list as one string ``[a, b]``; labels and property keys are plain identifiers
    (LABEL_RE / KEY_RE on the export side), so splitting on ", " is unambiguous. Anything else is kept verbatim and
    therefore cannot match an expected shape.
    """
    if isinstance(names, str):
        inner = names[1:-1] if names.startswith('[') and names.endswith(']') else names
        names = [x for x in inner.split(', ')] if inner else []
    return '|'.join(sorted(str(x) for x in (names or [])))


def node_rows(nodes: list[dict]) -> list[list]:
    out = []
    for n in nodes:
        cols = node_columns(n['node_kind'])
        out.append([n['id'], n['node_kind']] + [_norm(n.get(c)) for c in cols[1:]]
                   + [n['node_kind'], _shape(k for k, v in n.items() if v is not None)])
    out.sort(key=lambda x: (str(x[0]), x[1]))
    return out


EDGE_PROPS = {'id': 'id', 'source_record_id': 'source_record_id', 'qualification': 'qualification'}


def edge_rows(edges: list[dict]) -> list[list]:
    out = [[e['id'], e['source_id'], e['target_id'], e['kind'], _norm(e['source_record_id']), _norm(e['qualification']),
            _shape(k for k in EDGE_PROPS if e.get(k) is not None)] for e in edges]
    out.sort(key=lambda x: str(x[0]))
    return out


def row_diffs(what: str, want: list[list], got: list[list], limit: int = 5) -> list[str]:
    """Human-readable first differences: missing, unexpected, or changed (values vs labels/property set)."""
    w = {(str(r[0]), r[1] if what == 'node' else ''): r for r in want}
    g = {(str(r[0]), r[1] if what == 'node' else ''): r for r in got}
    out = []
    for k in sorted(set(w) | set(g)):
        a, b = w.get(k), g.get(k)
        if a == b:
            continue
        if a is None:
            out.append(f'unexpected {what} {k[0]} ({k[1] or "edge"})')
        elif b is None:
            out.append(f'missing {what} {k[0]}')
        elif a[-1] != b[-1] or (what == 'node' and a[-2] != b[-2]):
            out.append(f'{what} {k[0]}: labels/properties [{b[-2] + " / " if what == "node" else ""}{b[-1]}] '
                       f'differ from the export [{a[-2] + " / " if what == "node" else ""}{a[-1]}]')
        else:
            out.append(f'{what} {k[0]}: property values differ from the export')
        if len(out) >= limit:
            break
    return out


def unverified(graph: str, status: str, problems: list[str], **extra) -> dict:
    return dict({'graph': graph, 'content_verified': False, 'status': status, 'problems': problems}, **extra)


def receipt_refusal(graph: str, receipt: dict | None) -> dict | None:
    """Typed refusal for graphs this build cannot verify: no receipt, an older export format, or an incomplete receipt."""
    if not receipt:
        return unverified(graph, 'no-receipt', ['no projection receipt in graph; drop it or rebuild from a dataset'], rebuild_required=True)
    fmt = receipt.get('format')
    if fmt != EXPORT_FORMAT:
        return unverified(graph, 'unsupported-format', [f'legacy export format {fmt or "unrecorded"}; this UI verifies {EXPORT_FORMAT} only. '
                                                        'Rebuild it from the current dataset version (the old graph was not modified).'],
                          format=fmt, supported_format=EXPORT_FORMAT, rebuild_required=True)
    if not all(isinstance(receipt.get(k), str) for k in ('dataset', 'export_id')) or not isinstance(receipt.get('files'), dict):
        return unverified(graph, 'receipt-incomplete', ['projection receipt lacks dataset/export_id/files; rebuild it'], rebuild_required=True)
    return None


def digest_rows(nodes: list, edges: list) -> str:
    h = hashlib.sha256()
    for row in nodes:
        h.update(json.dumps([_norm(v) for v in row], ensure_ascii=False).encode() + b'\n')
    h.update(b'--edges--\n')
    for row in edges:
        h.update(json.dumps([_norm(v) for v in row], ensure_ascii=False).encode() + b'\n')
    return h.hexdigest()


def content_digest(nodes: list[dict], edges: list[dict]) -> str:
    return digest_rows(node_rows(nodes), edge_rows(edges))
