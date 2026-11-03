"""Bounded read-only exploration over one dataset, using backend lookup/query functions.

Fixed, parameterized SQL is used only to read display properties from the
backend's typed tables and views; matching, counting, tracing and projection
logic stays in the backend.
"""
from __future__ import annotations

import json
from typing import Any

from .bridge import NODE_RE, TOKEN_RE, Backend, Dataset, UserError

FIELDS = {'atf', 'signs'}
POLICIES = {'source', 'strict'}
MAX_LIST = 100
EDGE_PAGES = 3  # neighbors pages of 100 => at most 300 incident edges inspected per node

# kind -> (table, columns) for display properties. Columns are fixed identifiers.
PROPS = {
    'Artifact': ('artifact', ['catalog_key', 'source_record_id', 'metadata']),
    'Witness': ('witness', ['scope', 'artifact_id', 'source_record_id']),
    'Composition': ('composition', ['source_key', 'source_record_id']),
    'Passage': ('passage', ['source_key', 'composition_id', 'source_record_id']),
    'SequenceVersion': ('sequence_version', ['field', 'evidence_kind', 'parser_version', 'witness_id', 'source_record_id']),
    'Line': ('line', ['label', 'ordinal', 'surface', 'column_label', 'sequence_id', 'start_byte', 'end_byte']),
    'SignOccurrence': ('occurrence', ['raw', 'reading', 'kind', 'preservation', 'certainty', 'restoration_basis', 'position', 'line_id', 'start_byte', 'end_byte']),
    'WordToken': ('word_token', ['form', 'segmentation', 'sequence_id', 'source_locator']),
    'Lemma': ('lemma', ['citation_form', 'language', 'pos', 'namespace', 'revision']),
    'Sense': ('sense', ['guideword', 'gloss', 'lemma_id']),
    'TranslationSegment': ('translation_segment', ['language', 'text', 'granularity', 'locator', 'sequence_id', 'author', 'source_record_id']),
    'SourceRecord': ('source_record', ['namespace', 'source_key', 'locator']),
    'SignType': ('sign_type', ['label', 'namespace', 'revision']),
    'Claim': ('claim', ['predicate', 'object_key', 'status', 'rationale', 'subject_id', 'source_record_id', 'details']),
}


def _rows(db, sql: str, args=()) -> list[dict]:
    cur = db.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _clip(v: Any, n: int = 400) -> Any:
    if isinstance(v, str) and len(v) > n:
        return v[:n] + '…'
    return v


def label_for(kind: str, p: dict) -> str:
    if kind == 'Artifact':
        return p.get('catalog_key') or 'artifact'
    if kind == 'SignOccurrence':
        return f"{p.get('raw')} @{p.get('position')}"
    if kind == 'Line':
        return f"line {p.get('label')}" + (f" ({p['surface']})" if p.get('surface') else '')
    if kind == 'SequenceVersion':
        return f"{p.get('field')} sequence"
    if kind == 'SignType':
        return p.get('label') or 'sign'
    if kind == 'TranslationSegment':
        return f"{p.get('language')}: {str(p.get('text') or '')[:40]}"
    if kind == 'SourceRecord':
        return f"{p.get('namespace')}:{p.get('source_key')}"
    if kind == 'Claim':
        return f"{p.get('predicate')} → {p.get('object_key')}"
    if kind == 'Witness':
        return 'witness'
    return kind


class Explorer:
    def __init__(self, backend: Backend):
        self.b = backend

    # -- helpers ------------------------------------------------------------
    def _ds(self, key) -> Dataset:
        return self.b.get(key)

    @staticmethod
    def _node_id(value) -> str:
        if not isinstance(value, str) or not NODE_RE.match(value):
            raise UserError('invalid node ID')
        return value

    @staticmethod
    def _int(value, lo: int, hi: int, name: str, default: int) -> int:
        if value is None or value == '':
            return default
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise UserError(f'{name} must be an integer')
        if not lo <= v <= hi:
            raise UserError(f'{name} must be {lo}–{hi}')
        return v

    def kinds(self, db, ids: list[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(ids))[:400]
        if not ids:
            return {}
        out = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            out.update(dict(db.execute(f"SELECT id, kind FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()))
        return out

    def describe(self, db, ids: list[str]) -> dict[str, dict]:
        kinds = self.kinds(db, ids)
        by_kind: dict[str, list[str]] = {}
        for i, k in kinds.items():
            by_kind.setdefault(k, []).append(i)
        out = {}
        for kind, group in by_kind.items():
            table, cols = PROPS[kind]
            for r in _rows(db, f"SELECT id,{','.join(cols)} FROM {table} WHERE id IN ({','.join('?' * len(group))})", group):
                props = {k: _clip(v) for k, v in r.items() if k != 'id'}
                out[r['id']] = {'id': r['id'], 'kind': kind, 'label': label_for(kind, props), 'props': props}
        return out

    # -- dataset report -----------------------------------------------------
    def report(self, key: str) -> dict:
        ds = self._ds(key)
        P = self.b.projections

        def work(db):
            rep = P.report(db)
            cov = P.coverage(db)[:200]
            counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in
                      ['source_record', 'artifact', 'witness', 'composition', 'passage', 'sequence_version', 'line', 'occurrence',
                       'sign_type', 'word_token', 'lemma', 'sense', 'translation_segment', 'claim', 'rejection', 'exclusion', 'edge']}
            kinds = dict(db.execute('SELECT kind, count(*) FROM nodes GROUP BY kind ORDER BY kind').fetchall())
            edge_kinds = dict(db.execute('SELECT kind, count(*) FROM portable_edge GROUP BY kind ORDER BY kind').fetchall())
            occ_kinds = _rows(db, 'SELECT field, kind, preservation, certainty, count(*) n FROM token_context GROUP BY ALL ORDER BY field, n DESC LIMIT 60')
            rejections = _rows(db, 'SELECT locator, reason FROM rejection ORDER BY locator LIMIT 20')
            exclusions = _rows(db, 'SELECT reason, count(*) n FROM exclusion GROUP BY reason ORDER BY n DESC LIMIT 20')
            first = {}
            for kind in ('Artifact', 'SequenceVersion', 'SourceRecord', 'Claim', 'TranslationSegment'):
                r = db.execute('SELECT id FROM nodes WHERE kind=? ORDER BY id LIMIT 1', [kind]).fetchone()
                if r:
                    first[kind] = r[0]
            return {'registry': rep['registry'], 'scopes': rep['scopes'], 'nodes': rep['nodes'], 'edges': rep['edges'],
                    'coverage': cov, 'coverage_note': rep['coverage_note'], 'counts': counts, 'node_kinds': kinds,
                    'edge_kinds': edge_kinds, 'position_kinds': occ_kinds, 'rejections': rejections, 'exclusions': exclusions,
                    'entry_nodes': first}
        out = self.b.run(ds, work)
        unavailable = [k for k in ('composition', 'passage', 'word_token', 'lemma', 'sense') if out['counts'][k] == 0]
        out['unavailable_layers'] = unavailable
        out['dataset'] = ds.public()
        return out

    def top(self, key: str, field: str, policy: str) -> dict:
        ds = self._ds(key)
        if field not in FIELDS or policy not in POLICIES:
            raise UserError('invalid field/policy')
        scope = self._scope(ds, field, None)

        def work(db):
            if scope is None:
                return {'postings': [], 'bigrams': []}
            args = [scope['namespace'], scope['snapshot_id'], field, policy]
            posts = _rows(db, 'SELECT label, count(*) n FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? GROUP BY label ORDER BY n DESC, label LIMIT 24', args)
            grams = _rows(db, "SELECT readings, count(*) n FROM ngram WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND length=2 AND json_extract_string(readings,'$[0]') IS NOT NULL AND json_extract_string(readings,'$[1]') IS NOT NULL GROUP BY readings ORDER BY n DESC, readings LIMIT 16", args)
            for g in grams:
                g['readings'] = json.loads(g['readings'])
            return {'postings': posts, 'bigrams': grams}
        return self.b.run(ds, work)

    def _scope(self, ds: Dataset, field: str, snapshot: str | None) -> dict | None:
        scopes = [s for s in ds.scopes if s['field'] == field]
        if snapshot:
            scopes = [s for s in scopes if s['snapshot_id'] == snapshot]
            if not scopes:
                raise UserError('selected scope not present in dataset')
        return scopes[0] if scopes else None

    # -- queries ------------------------------------------------------------
    def query(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise UserError('JSON object required')
        ds = self._ds(body.get('dataset'))
        pattern = body.get('pattern')
        if isinstance(pattern, str):
            pattern = pattern.split()
        if not isinstance(pattern, list) or not 1 <= len(pattern) <= 5 or not all(isinstance(t, str) and TOKEN_RE.match(t) for t in pattern):
            raise UserError('pattern: 1–5 tokens, each 1–64 visible characters; use ? for one represented position')
        field, policy = body.get('field', 'atf'), body.get('policy', 'source')
        engine = body.get('engine', 'index')
        if field not in FIELDS or policy not in POLICIES or engine not in {'index', 'graph'}:
            raise UserError('invalid field, policy or engine')
        limit = self._int(body.get('limit'), 1, MAX_LIST, 'limit', 25)
        offset = self._int(body.get('offset'), 0, 100000, 'offset', 0)
        max_skip = self._int(body.get('max_skip'), 0, 2, 'max_skip', 0)
        parity = bool(body.get('parity', True))
        scope = self._scope(ds, field, body.get('snapshot'))
        P, Q, M = self.b.projections, self.b.query, self.b.model
        base = {'dataset': ds.key, 'dataset_kind': ds.kind, 'pattern': pattern, 'field': field, 'policy': policy, 'engine': engine,
                'evidence': 'source editorial readings' if field == 'atf' else 'source-derived sign IDs, not image evidence',
                'policy_note': ('strict: every position explicitly intact AND certain' if policy == 'strict'
                                else 'source readings, including uncertain/restored readings'),
                'gap_extent': 'represented positions, not ancient sign count; an unknown-length loss is one position'}
        if scope is None:
            return dict(base, scope=None, count=0, rows=[], truncated=False, next_offset=None,
                        missing=f'no {field} sequences in this dataset (layer unavailable, not zero occurrences)')
        base['scope'] = scope
        if len(pattern) == 1 and (pattern[0] == '?' or engine == 'graph'):
            raise UserError('single-token search uses the posting index and needs a concrete token')
        if engine == 'graph' and policy == 'strict' and max_skip:
            pass  # backend applies strict to inserted wildcard positions as well

        def work(db):
            if len(pattern) == 1:
                res = P.lookup(db, 'postings', pattern[0], namespace=scope['namespace'], snapshot=scope['snapshot_id'],
                               field=field, policy=policy, limit=limit, offset=offset)
                cnt = db.execute('SELECT count(*) FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND (token_id=? OR label=?)',
                                 [scope['namespace'], scope['snapshot_id'], field, policy, pattern[0], pattern[0]]).fetchone()[0]
                hits = [{'match_id': r['occurrence_id'], 'occurrence_ids': [r['occurrence_id']], 'sequence_id': r['sequence_id'],
                         'line_id': r['line_id'], 'start_position': r['position'], 'end_position': r['position'],
                         'artifact_id': r['artifact_id'], 'witness_id': r['witness_id'], 'source_record_id': r['source_record_id']} for r in res['rows']]
                out = {'count': cnt, 'truncated': res['truncated'], 'next_offset': res['next_offset'], 'kind': 'postings'}
            elif engine == 'index':
                res = P.lookup(db, 'motifs', M.canonical(pattern), namespace=scope['namespace'], snapshot=scope['snapshot_id'],
                               field=field, policy=policy, limit=limit, offset=offset)
                hits = [{'match_id': r['match_id'], 'occurrence_ids': json.loads(r['occurrence_ids']), 'sequence_id': r['sequence_id'],
                         'line_id': r['line_id'], 'start_position': r['start_position'], 'end_position': r['end_position'],
                         'artifact_id': r['artifact_id'], 'witness_id': r['witness_id'], 'source_record_id': r['source_record_id']} for r in res['rows']]
                out = {'count': res['count'], 'truncated': res['truncated'], 'next_offset': res['next_offset'], 'kind': 'motifs'}
            else:
                res = Q.motif(db, pattern, field, policy == 'strict', max_skip)
                allm = res['matches']
                page = allm[offset:offset + limit]
                hits = [{'match_id': m['id'], 'occurrence_ids': m['occurrences'], 'sequence_id': m['sequence'], 'line_id': m['line'],
                         'artifact_id': m['artifact'], 'witness_id': m['witness'], 'skipped': m['skipped_represented_positions']} for m in page]
                out = {'count': res['occurrences'], 'truncated': offset + limit < len(allm), 'next_offset': offset + limit if offset + limit < len(allm) else None,
                       'kind': 'graph-motif', 'graph_summary': {k: res[k] for k in ('witnesses', 'artifacts', 'denominators', 'certain_physical_evidence_count', 'bounds', 'matches_truncated', 'cross_line')}}
                if res['matches_truncated']:
                    out['note'] = 'backend graph motif returns at most 100 matches; count covers all'
            if parity and len(pattern) >= 2 and engine == 'index' and max_skip == 0:
                try:
                    g = Q.motif(db, pattern, field, policy == 'strict', 0)
                    out['graph_summary'] = {k: g[k] for k in ('witnesses', 'artifacts', 'denominators', 'certain_physical_evidence_count', 'bounds', 'matches_truncated', 'cross_line')}
                    out['parity'] = {'index': out['count'], 'graph': g['occurrences'], 'ok': g['occurrences'] == out['count']}
                except ValueError as exc:
                    out['parity'] = {'skipped': str(exc)}
            self._enrich(db, hits)
            out['rows'] = hits
            return out
        return dict(base, **self.b.run(ds, work), limit=limit, offset=offset, max_skip=max_skip)

    def _enrich(self, db, hits: list[dict]) -> None:
        occ_ids = [o for h in hits for o in h['occurrence_ids']]
        occ = {}
        for i in range(0, len(occ_ids), 200):
            chunk = occ_ids[i:i + 200]
            for r in _rows(db, f"SELECT id, raw, reading, kind, preservation, certainty, position FROM occurrence WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                occ[r['id']] = r
        line_ids = list({h['line_id'] for h in hits})
        lines = {}
        if line_ids:
            lines = {r['id']: r for r in _rows(db, f"SELECT id, label, surface, column_label, ordinal FROM line WHERE id IN ({','.join('?' * len(line_ids))})", line_ids)}
        art_ids = list({h['artifact_id'] for h in hits})
        arts = {}
        if art_ids:
            arts = dict(db.execute(f"SELECT id, catalog_key FROM artifact WHERE id IN ({','.join('?' * len(art_ids))})", art_ids).fetchall())
        for h in hits:
            h['tokens'] = [occ.get(o, {'id': o}) for o in h['occurrence_ids']]
            h['line'] = lines.get(h['line_id'])
            h['artifact'] = arts.get(h['artifact_id'])

    # -- occurrence detail ----------------------------------------------------
    def occurrence(self, key: str, oid: str) -> dict:
        ds = self._ds(key)
        oid = self._node_id(oid)
        P, Q = self.b.projections, self.b.query

        def work(db):
            found = P.lookup(db, 'occurrence', oid, limit=1)['rows']
            if not found:
                raise UserError('unknown occurrence ID in this dataset')
            o = found[0]
            line = _rows(db, 'SELECT id, label, surface, column_label, ordinal, start_byte, end_byte FROM line WHERE id=?', [o['line_id']])[0]
            context = P.source_span(db, o['source_record_id'], o['field'], line['start_byte'], line['end_byte'], 100)
            rec = db.execute('SELECT source_key, locator, raw_json FROM source_record WHERE id=?', [o['source_record_id']]).fetchone()
            field_text = json.loads(rec[2]).get(o['field'], '')
            fb = field_text.encode('utf-8') if isinstance(field_text, str) else b''
            excerpt = {'before': fb[line['start_byte']:o['start_byte']].decode('utf-8', 'replace'),
                       'token': fb[o['start_byte']:o['end_byte']].decode('utf-8', 'replace'),
                       'after': fb[o['end_byte']:line['end_byte']].decode('utf-8', 'replace'),
                       'line_span': [line['start_byte'], line['end_byte']]}
            trace, trace_error = None, None
            source_kind, raw = ds.raws.get(o['raw_sha256'], (None, None))
            if raw is None:
                trace_error = 'raw source bytes not available to the UI for this binding'
            else:
                try:
                    data = raw
                    if not isinstance(raw, bytes):
                        if raw.stat().st_size > 64 * 1024**2:
                            raise UserError('raw source too large for interactive trace')
                        data = raw.read_bytes()
                    manifest = ds.manifest or {'source': source_kind, 'raw_sha256': o['raw_sha256'],
                                               'rights': self.b.adapters.RIGHTS.get(source_kind, {'status': 'unknown'}), 'snapshot_id': o['snapshot_id']}
                    trace = Q.trace_rows(db, data, manifest, oid, verified_manifest=ds.kind == 'snapshot')
                except ValueError as exc:
                    trace_error = str(exc)
            translations = _source_order([t for t in P.lookup(db, 'translations', o['sequence_id'], limit=50)['rows'] if t['sequence_id'] == o['sequence_id']])
            postings = P.lookup(db, 'reverse-postings', oid, limit=20)
            motifs = P.lookup(db, 'reverse-motifs', oid, limit=30)
            for m in motifs['rows']:
                m['readings'] = json.loads(m['readings'])
            translation_total = db.execute('SELECT count(*) FROM translation_segment WHERE source_record_id=?', [o['source_record_id']]).fetchone()[0]
            words = db.execute('SELECT count(*) FROM word_token WHERE sequence_id=?', [o['sequence_id']]).fetchone()[0]
            sign = db.execute("SELECT e.target_id, s.label, s.namespace, e.qualification FROM edge e JOIN sign_type s ON s.id=e.target_id WHERE e.kind='MAPS_TO_SIGN' AND e.source_id=?", [oid]).fetchone()
            art = db.execute('SELECT catalog_key FROM artifact WHERE id=?', [o['artifact_id']]).fetchone()
            return {'occurrence': o, 'line': line, 'line_positions': context, 'source': {'source_key': rec[0], 'locator': rec[1]},
                    'excerpt': excerpt, 'trace': trace, 'trace_error': trace_error, 'translations': translations,
                    'translation_note': 'Translation strings are placed in this sequence; their span is unresolved. This is context, not word-level alignment.',
                    'translation_total_in_record': translation_total, 'word_tokens_in_sequence': words,
                    'sign': {'id': sign[0], 'label': sign[1], 'namespace': sign[2], 'qualification': json.loads(sign[3])} if sign else None,
                    'artifact_catalog_key': art[0] if art else None, 'postings': postings, 'motifs': motifs}
        out = self.b.run(ds, work)
        out['dataset'] = {'key': ds.key, 'kind': ds.kind, 'authority': ds.public()['authority']}
        return _jsonable(out)

    # -- graph ----------------------------------------------------------------
    def node(self, key: str, nid: str) -> dict:
        ds = self._ds(key)
        nid = self._node_id(nid)
        P = self.b.projections

        def work(db):
            d = self.describe(db, [nid]).get(nid)
            if d is None:
                raise UserError('unknown node ID in this dataset')
            extra: dict[str, Any] = {}
            if d['kind'] == 'Claim' and d['props'].get('predicate') == 'catalog_alias':
                extra['catalog_candidates'] = P.lookup(db, 'catalog', d['props']['object_key'], limit=20)['rows']
                extra['candidate_note'] = 'Exact catalog-key candidates. No identity merge or equivalence edge is asserted.'
            if d['kind'] == 'Artifact':
                extra['alias_claims'] = P.lookup(db, 'aliases', nid, limit=20)['rows']
                extra['incoming_alias_claims'] = []
                key_rows = db.execute("SELECT r.namespace || ':' || a.catalog_key FROM artifact a JOIN source_record r ON r.id=a.source_record_id WHERE a.id=?", [nid]).fetchone()
                if key_rows:
                    extra['incoming_alias_claims'] = P.lookup(db, 'aliases', key_rows[0], limit=20)['rows']
            if d['kind'] == 'SequenceVersion':
                extra['translations'] = _source_order([t for t in P.lookup(db, 'translations', nid, limit=50)['rows'] if t['sequence_id'] == nid])
                extra['lines'] = _rows(db, 'SELECT id, label, ordinal, surface FROM line WHERE sequence_id=? ORDER BY ordinal LIMIT 100', [nid])
            if d['kind'] == 'Line':
                line = _rows(db, 'SELECT l.start_byte, l.end_byte, s.source_record_id, s.field FROM line l JOIN sequence_version s ON s.id=l.sequence_id WHERE l.id=?', [nid])[0]
                extra['positions'] = P.source_span(db, line['source_record_id'], line['field'], line['start_byte'], line['end_byte'], 100)
            return dict(d, **extra)
        return _jsonable(self.b.run(ds, work))

    def neighborhood(self, key: str, nid: str, per_group=None) -> dict:
        ds = self._ds(key)
        nid = self._node_id(nid)
        per = self._int(per_group, 1, 25, 'per', 8)
        P = self.b.projections

        def work(db):
            center = self.describe(db, [nid]).get(nid)
            if center is None:
                raise UserError('unknown node ID in this dataset')
            edges, offset, truncated = [], 0, False
            for _ in range(EDGE_PAGES):
                page = P.lookup(db, 'neighbors', nid, limit=100, offset=offset)
                edges += page['rows']
                truncated = page['truncated']
                if not truncated:
                    break
                offset = page['next_offset']
            groups: dict[tuple[str, str], list[dict]] = {}
            for e in edges:
                direction = 'out' if e['source_id'] == nid else 'in'
                groups.setdefault((e['kind'], direction), []).append(e)
            other_ids = [e['target_id'] if e['source_id'] == nid else e['source_id'] for e in edges]
            info = self.describe(db, other_ids)
            nodes = {nid: dict(center, center=True)}
            links, aggregates = [], []
            for (kind, direction), group in sorted(groups.items()):
                def order(e):
                    other = e['target_id'] if direction == 'out' else e['source_id']
                    p = info.get(other, {}).get('props', {})
                    return (p.get('ordinal') if p.get('ordinal') is not None else p.get('position') if p.get('position') is not None else 0, other)
                group.sort(key=order)
                for e in group[:per]:
                    other = e['target_id'] if direction == 'out' else e['source_id']
                    nodes.setdefault(other, info.get(other) or {'id': other, 'kind': '?', 'label': other, 'props': {}})
                    links.append({'id': e['id'], 'source': e['source_id'], 'target': e['target_id'], 'kind': kind,
                                  'qualification': json.loads(e['qualification']) if isinstance(e['qualification'], str) else e['qualification']})
                hidden = len(group) - per
                if hidden > 0:
                    agg = f'more:{kind}:{direction}'
                    aggregates.append({'id': agg, 'kind': 'Aggregate', 'edge_kind': kind, 'direction': direction,
                                       'hidden': hidden, 'lower_bound': truncated, 'label': f"+{hidden}{'+' if truncated else ''} {kind}"})
                    links.append({'id': agg + ':link', 'source': nid if direction == 'out' else agg, 'target': agg if direction == 'out' else nid,
                                  'kind': kind, 'aggregate': True})
            return {'center': nid, 'nodes': list(nodes.values()) + aggregates, 'links': links, 'edges_seen': len(edges),
                    'edges_truncated': truncated, 'per_group': per,
                    'note': f'Showing up to {per} neighbors per relationship type and direction; inspected at most {EDGE_PAGES * 100} incident edges.'}
        return _jsonable(self.b.run(ds, work))

    def search_nodes(self, key: str, text: str) -> dict:
        ds = self._ds(key)
        if not isinstance(text, str) or not 1 <= len(text) <= 64:
            raise UserError('search text 1–64 characters')

        def work(db):
            if NODE_RE.match(text):
                hit = self.describe(db, [text])
                return {'rows': list(hit.values())}
            like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            ids = [r[0] for r in db.execute("SELECT id FROM artifact WHERE catalog_key ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM sign_type WHERE label ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM source_record WHERE source_key ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM claim WHERE object_key ILIKE ? ESCAPE '\\' LIMIT 30", [like] * 4).fetchall()]
            return {'rows': list(self.describe(db, ids).values())}
        return _jsonable(self.b.run(ds, work))


def _source_order(rows: list[dict]) -> list[dict]:
    """Sort translation placements by their UTF-8 locator start (field:utf8:start:end); unknown last."""
    def start(t):
        parts = str(t.get('locator') or '').split(':')
        return (0, int(parts[2])) if len(parts) == 4 and parts[1] == 'utf8' and parts[2].isdigit() else (1, 0)
    return sorted(rows, key=start)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
