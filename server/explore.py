"""Bounded read-only exploration over one dataset version, using backend lookup/query/trace functions.

Fixed, parameterized SQL is used only to read display properties and group counts from
the backend's typed tables and views; matching, counting, tracing and projection logic
stays in the backend. Every response carries the dataset binding (key + version) and the
backend's scope note, so a count is never shown without the population it describes.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .bridge import NODE_RE, TOKEN_RE, Backend, Dataset, UserError

FIELDS = {'atf', 'signs'}
POLICIES = {'source', 'strict'}
MAX_LIST = 100
GROUP_SAMPLE = 200  # per (edge kind, direction): rows fetched for ordering; the group count is exact
COVERAGE_PAGE = 200  # per-record coverage rows per page; the total is always exact (review N-4)

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

# Data layers: zero rows means the layer is unavailable for this dataset (not supplied, or supplied empty).
DATA_LAYERS = ['source_record', 'artifact', 'witness', 'composition', 'passage', 'sequence_version', 'line', 'occurrence',
               'sign_type', 'word_token', 'lemma', 'sense', 'translation_segment', 'claim', 'edge']
# Audit tables: zero rows is a true zero ("none recorded").
AUDIT_TABLES = ['rejection', 'exclusion']
GLOSS_NOTE = 'English sense glosses are lexical labels for one word in context; they are not translations of the text.'


def _rows(db, sql: str, args=()) -> list[dict]:
    cur = db.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def coverage_page(rows: list[dict], offset: int, page: int = COVERAGE_PAGE) -> dict:
    total = len(rows)
    chunk = rows[offset:offset + page]
    end = offset + len(chunk)
    return {'coverage': chunk, 'coverage_total': total, 'coverage_offset': offset, 'coverage_page_size': page,
            'coverage_truncated': total > end or offset > 0,
            'coverage_next_offset': end if end < total else None,
            'coverage_prev_offset': max(0, offset - page) if offset > 0 else None,
            'coverage_shown': f'records {offset + 1}–{end} of {total}' if chunk else f'no records at offset {offset} (total {total})'}


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
    if kind == 'WordToken':
        return f"word {p.get('form')}"
    if kind == 'Lemma':
        return f"{p.get('citation_form')} ({p.get('pos')})"
    if kind == 'Sense':
        return f"sense: {p.get('gloss')} (gloss, not translation)"
    if kind == 'Witness':
        return 'witness'
    return kind


class Explorer:
    def __init__(self, backend: Backend):
        self.b = backend

    # -- helpers ------------------------------------------------------------
    def _ds(self, key, version=None) -> Dataset:
        return self.b.get(key, version or None)

    @staticmethod
    def _node_id(value) -> str:
        if not isinstance(value, str) or not NODE_RE.match(value):
            raise UserError('invalid node ID')
        return value

    @staticmethod
    def _int(value, lo: int, hi: int, name: str, default: int) -> int:
        if value is None or value == '':
            return default
        if isinstance(value, bool):
            raise UserError(f'{name} must be an integer')
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise UserError(f'{name} must be an integer')
        if not lo <= v <= hi:
            raise UserError(f'{name} must be {lo}–{hi}')
        return v

    @staticmethod
    def _bind(ds: Dataset, out: dict) -> dict:
        out['dataset'] = dict(ds.binding(), **{k: v for k, v in (out.get('dataset') or {}).items() if k not in ds.binding()})
        return out

    def kinds(self, db, ids: list[str]) -> dict[str, str]:
        """Resolve node kinds per typed table (not through the 14-way nodes UNION view; review F-6)."""
        ids = list(dict.fromkeys(ids))[:400]
        out: dict[str, str] = {}
        if not ids:
            return out
        for kind, (table, _cols) in PROPS.items():
            rest = [i for i in ids if i not in out]
            if not rest:
                break
            for i in range(0, len(rest), 100):
                chunk = rest[i:i + 100]
                for (nid,) in db.execute(f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall():
                    out[nid] = kind
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
    def coverage(self, key: str, version=None, offset=None) -> dict:
        """One page of the backend's per-source-record coverage, with the exact total."""
        ds = self._ds(key, version)
        off = self._int(offset, 0, 10**7, 'offset', 0)
        out = self.b.run(ds, lambda db: coverage_page(self.b.projections.coverage(db), off))
        return _jsonable(self._bind(ds, out))

    def report(self, key: str, version=None) -> dict:
        ds = self._ds(key, version)
        P = self.b.projections

        def work(db):
            rep = P.report(db)
            cov = coverage_page(P.coverage(db), 0)
            counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in DATA_LAYERS + AUDIT_TABLES}
            kinds = {}
            for kind, (table, _c) in PROPS.items():
                n = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                if n:
                    kinds[kind] = n
            edge_kinds = dict(db.execute('SELECT kind, count(*) FROM portable_edge GROUP BY kind ORDER BY kind').fetchall())
            occ_kinds = _rows(db, 'SELECT field, kind, preservation, certainty, count(*) n FROM token_context GROUP BY ALL ORDER BY field, n DESC LIMIT 60')
            rejections = _rows(db, 'SELECT locator, reason FROM rejection ORDER BY locator LIMIT 20')
            exclusions = _rows(db, 'SELECT reason, count(*) n FROM exclusion GROUP BY reason ORDER BY n DESC LIMIT 20')
            first = {}
            for kind in ('Artifact', 'SequenceVersion', 'SourceRecord', 'Claim', 'TranslationSegment', 'WordToken'):
                table = PROPS[kind][0]
                r = db.execute(f'SELECT id FROM {table} ORDER BY id LIMIT 1').fetchone()
                if r:
                    first[kind] = r[0]
            senses = None
            if counts['sense']:
                in_sense, with_sig, words = db.execute("SELECT count(*), count(*) FILTER (WHERE json_extract_string(qualification,'$.sig') IS NOT NULL), "
                                                       "count(DISTINCT source_id) FROM edge WHERE kind='IN_SENSE'").fetchone()
                row_sig = db.execute("SELECT count(*) FROM sense WHERE source_locator LIKE '@%'").fetchone()[0]
                senses = {'in_sense_links': in_sense, 'links_with_own_sig': with_sig, 'words_with_sense': words, 'sense_rows': counts['sense'],
                          'sense_rows_with_instance_sig': row_sig,
                          'note': 'Per-word sense provenance lives on IN_SENSE links. A sig on a shared Sense row is one attesting instance (oracc-cdl-1), not every word\'s.'}
            return {'registry': rep['registry'], 'scopes': rep['scopes'], 'nodes': rep['nodes'], 'edges': rep['edges'], 'sense_links': senses,
                    **cov, 'coverage_note': rep['coverage_note'], 'counts': counts, 'node_kinds': kinds,
                    'edge_kinds': edge_kinds, 'position_kinds': occ_kinds, 'rejections': rejections, 'exclusions': exclusions,
                    'entry_nodes': first}
        out = self.b.run(ds, work)
        out['layers'] = layer_states(out['counts'], out['exclusions'])
        out['unavailable_layers'] = [r['layer'] for r in out['layers'] if r['state'].startswith('unavailable')]
        out['dataset'] = ds.public()
        out['binding'] = ds.binding()
        return _jsonable(out)

    def top(self, key: str, field: str, policy: str, version=None) -> dict:
        ds = self._ds(key, version)
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
        return self._bind(ds, self.b.run(ds, work))

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
        ds = self._ds(body.get('dataset'), body.get('version'))
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
        snapshot = body.get('snapshot')
        if snapshot is not None and not isinstance(snapshot, str):
            raise UserError('invalid snapshot scope')
        scope = self._scope(ds, field, snapshot)
        P, Q, M = self.b.projections, self.b.query, self.b.model
        base = {'pattern': pattern, 'field': field, 'policy': policy, 'engine': engine,
                'evidence': 'source editorial readings' if field == 'atf' else 'source-derived sign IDs, not image evidence',
                'policy_note': ('strict: every position explicitly intact AND certain' if policy == 'strict'
                                else 'source readings, including uncertain/restored readings'),
                'gap_extent': 'represented positions, not ancient sign count; an unknown-length loss is one position',
                'scope_note': ds.selection.get('note'), 'selection_label': ds.selection.get('label')}
        if scope is None:
            return self._bind(ds, dict(base, scope=None, count=0, rows=[], truncated=False, next_offset=None,
                                       missing=f'no {field} sequences in this dataset (layer unavailable, not zero occurrences)'))
        base['scope'] = scope
        if len(pattern) == 1 and (pattern[0] == '?' or engine == 'graph'):
            raise UserError('single-token search uses the posting index and needs a concrete token')

        def work(db):
            if len(pattern) == 1:
                res = P.lookup(db, 'postings', pattern[0], namespace=scope['namespace'], snapshot=scope['snapshot_id'],
                               field=field, policy=policy, limit=limit, offset=offset)
                cnt = db.execute('SELECT count(*) FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND (token_id=? OR label=?)',
                                 [scope['namespace'], scope['snapshot_id'], field, policy, pattern[0], pattern[0]]).fetchone()[0]
                hits = [{'match_id': r['occurrence_id'], 'occurrence_ids': [r['occurrence_id']], 'sequence_id': r['sequence_id'],
                         'line_id': r['line_id'], 'start_position': r['position'], 'end_position': r['position'],
                         'artifact_id': r['artifact_id'], 'witness_id': r['witness_id'], 'source_record_id': r['source_record_id']} for r in res['rows']]
                out = {'count': cnt, 'retrievable': cnt, 'truncated': res['truncated'], 'next_offset': res['next_offset'], 'kind': 'postings'}
            elif engine == 'index':
                res = P.lookup(db, 'motifs', M.canonical(pattern), namespace=scope['namespace'], snapshot=scope['snapshot_id'],
                               field=field, policy=policy, limit=limit, offset=offset)
                hits = [{'match_id': r['match_id'], 'occurrence_ids': json.loads(r['occurrence_ids']), 'sequence_id': r['sequence_id'],
                         'line_id': r['line_id'], 'start_position': r['start_position'], 'end_position': r['end_position'],
                         'artifact_id': r['artifact_id'], 'witness_id': r['witness_id'], 'source_record_id': r['source_record_id']} for r in res['rows']]
                out = {'count': res['count'], 'retrievable': res['count'], 'truncated': res['truncated'], 'next_offset': res['next_offset'], 'kind': 'motifs'}
            else:
                res = Q.motif(db, pattern, field, policy == 'strict', max_skip)
                out = dict(graph_page(res, offset, limit), kind='graph-motif',
                           graph_summary={k: res[k] for k in ('witnesses', 'artifacts', 'denominators', 'certain_physical_evidence_count', 'bounds', 'matches_truncated', 'cross_line')})
                hits = [{'match_id': m['id'], 'occurrence_ids': m['occurrences'], 'sequence_id': m['sequence'], 'line_id': m['line'],
                         'artifact_id': m['artifact'], 'witness_id': m['witness'], 'skipped': m['skipped_represented_positions']} for m in out.pop('page')]
            if parity and len(pattern) >= 2 and engine == 'index' and max_skip == 0:
                try:
                    g = Q.motif(db, pattern, field, policy == 'strict', 0)
                    out['graph_summary'] = {k: g[k] for k in ('witnesses', 'artifacts', 'denominators', 'certain_physical_evidence_count', 'bounds', 'matches_truncated', 'cross_line')}
                    out['parity'] = {'index': out['count'], 'graph': g['occurrences'], 'ok': g['occurrences'] == out['count']}
                except ValueError as exc:
                    out['parity'] = {'skipped': str(exc)}
                except Exception as exc:  # duckdb OutOfMemory/Interrupt: the add-on exceeded the interactive bound
                    text = str(exc).lower()
                    if 'out of memory' not in text and 'interrupt' not in text:
                        raise
                    out['parity'] = {'skipped': 'graph traversal for parity exceeded the interactive bound (DuckDB memory or time); the index count stands. '
                                                'Offline graph/array/index parity: python -m cuneiform parity <snapshot> <pattern> --field <field>'}
            if policy == 'strict' and out['count'] == 0:
                certain = db.execute("SELECT count(*) FROM token_context WHERE field=? AND preservation='intact' AND certainty='certain'", [field]).fetchone()[0]
                out['strict_note'] = (f'Strict = 0 here because no {field} position in this dataset is marked intact AND certain by the source'
                                      + (' (eBL does not assert certainty from an absence of editorial marks).' if str(scope['namespace']).startswith('ebl') else '.')
                                      + ' See Coverage → position states.') if certain == 0 else \
                    f'{certain} {field} positions are intact AND certain, but none form this pattern.'
            self._enrich(db, hits)
            out['rows'] = hits
            return out
        return self._bind(ds, dict(base, **self.b.run(ds, work), limit=limit, offset=offset, max_skip=max_skip))

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
    def occurrence(self, key: str, oid: str, version=None) -> dict:
        ds = self._ds(key, version)
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
                       'line_span': [line['start_byte'], line['end_byte']],
                       'origin': 'parsed record field stored in the canonical DB (source_record.raw_json); display only — the trace below re-reads raw bytes'}
            trace, trace_error = None, None
            if ds.kind != 'snapshot':
                source_kind, raw = ds.raws.get(o['raw_sha256'], (None, None))
                if raw is None:
                    trace_error = 'raw source bytes not available to the UI for this binding'
                else:
                    try:
                        manifest = {'source': source_kind, 'raw_sha256': o['raw_sha256'],
                                    'rights': self.b.adapters.RIGHTS.get(source_kind, {'status': 'unknown'}), 'snapshot_id': o['snapshot_id']}
                        trace = Q.trace_rows(db, raw, manifest, oid, verified_manifest=False)
                        trace['origin'] = 'in-memory input bytes (hashed; not a verified snapshot)'
                    except ValueError as exc:
                        trace_error = str(exc)
            translations = _source_order([t for t in P.lookup(db, 'translations', o['sequence_id'], limit=50)['rows'] if t['sequence_id'] == o['sequence_id']])
            postings = P.lookup(db, 'reverse-postings', oid, limit=20)
            motifs = P.lookup(db, 'reverse-motifs', oid, limit=30)
            for m in motifs['rows']:
                m['readings'] = json.loads(m['readings'])
            translation_total = db.execute('SELECT count(*) FROM translation_segment WHERE source_record_id=?', [o['source_record_id']]).fetchone()[0]
            words = db.execute('SELECT count(*) FROM word_token WHERE sequence_id=?', [o['sequence_id']]).fetchone()[0]
            words_in_dataset = db.execute('SELECT count(*) FROM word_token').fetchone()[0]
            sign = db.execute("SELECT e.target_id, s.label, s.namespace, e.qualification FROM edge e JOIN sign_type s ON s.id=e.target_id WHERE e.kind='MAPS_TO_SIGN' AND e.source_id=?", [oid]).fetchone()
            art = db.execute('SELECT catalog_key FROM artifact WHERE id=?', [o['artifact_id']]).fetchone()
            return {'occurrence': o, 'line': line, 'line_positions': context, 'source': {'source_key': rec[0], 'locator': rec[1]},
                    'excerpt': excerpt, 'trace': trace, 'trace_error': trace_error, 'translations': translations,
                    'translation_note': 'Translation strings are placed in this sequence; their span is unresolved. This is context, not word-level alignment.',
                    'translation_total_in_record': translation_total, 'word_tokens_in_sequence': words, 'word_tokens_in_dataset': words_in_dataset,
                    'sign': {'id': sign[0], 'label': sign[1], 'namespace': sign[2], 'qualification': json.loads(sign[3])} if sign else None,
                    'artifact_catalog_key': art[0] if art else None, 'postings': postings, 'motifs': motifs}
        out = self.b.run(ds, work)
        if ds.kind == 'snapshot':
            # The backend's own verified trace: re-verifies the snapshot and, for cohorts, re-reads the parent raw object.
            try:
                with self.b.guarded(ds):
                    # StoreEntry: the backend re-verifies with ledger='require' and this exact ID.
                    trace = Q.trace(self.b.store_entry(ds.key), oid, parent_root=self.b.research_root)
                if isinstance(trace.get('timing'), dict):
                    self.b.note_timing('backend_trace', trace['timing'])
                trace['origin'] = ('cohort payload: byte-identical slices of the parent object (snapshot raw file)'
                                   if ds.selection.get('kind') == 'cohort' else 'snapshot raw payload')
                out['trace'] = trace
            except UserError:
                raise
            except (ValueError, OSError, KeyError) as exc:
                out['trace_error'] = f'{type(exc).__name__}: {exc}'
        out['dataset'] = {'authority': ds.public()['authority']}
        return _jsonable(self._bind(ds, out))

    # -- ORACC words (gloss is not translation) -------------------------------
    def word(self, key: str, wid: str, version=None) -> dict:
        ds = self._ds(key, version)
        wid = self._node_id(wid)
        P = self.b.projections

        def work(db):
            rows = [r for r in P.lookup(db, 'words', wid, limit=5)['rows'] if r['word_id'] == wid]
            links = _rows(db, "SELECT kind, target_id, qualification FROM edge WHERE source_id=? AND kind IN ('LEMMATIZED_AS','IN_SENSE') ORDER BY kind, target_id", [wid])
            for link in links:
                link['qualification'] = _json_or(link['qualification'])
            sense_ids = [link['target_id'] for link in links if link['kind'] == 'IN_SENSE']
            senses = _rows(db, f"SELECT id, source_locator FROM sense WHERE id IN ({','.join('?' * len(sense_ids))})", sense_ids) if sense_ids else []
            return rows, links, senses
        rows, links, senses = self.b.run(ds, work)
        if not rows:
            raise UserError('unknown word ID in this dataset')
        out = {'word': rows[0], 'gloss_note': GLOSS_NOTE, 'trace': None, 'trace_error': None,
               'links': links, 'sense_provenance': sense_provenance(ds, links, senses)}
        if ds.kind == 'snapshot' and ds.manifest and ds.manifest.get('source') == 'oracc-selection':
            try:
                with self.b.guarded(ds):
                    t0 = time.monotonic()
                    out['trace'] = self.b.oracc.trace_word(self.b.store_entry(ds.key), wid, parent_root=self.b.research_root)
                    self.b.note_timing('backend_word_trace_ms', round((time.monotonic() - t0) * 1000, 1))
            except UserError:
                raise
            except (ValueError, OSError, KeyError, StopIteration) as exc:
                out['trace_error'] = f'{type(exc).__name__}: {exc}'
        else:
            out['trace_error'] = 'word trace is provided by the backend for ORACC-selection snapshots only'
        return _jsonable(self._bind(ds, out))

    def words(self, key: str, text: str, version=None, offset=None) -> dict:
        ds = self._ds(key, version)
        if not isinstance(text, str) or not 1 <= len(text) <= 64:
            raise UserError('word search text 1–64 characters')
        off = self._int(offset, 0, 100000, 'offset', 0)
        P = self.b.projections
        res = self.b.run(ds, lambda db: P.lookup(db, 'words', text, limit=50, offset=off))
        return _jsonable(self._bind(ds, dict(res, gloss_note=GLOSS_NOTE, query=text,
                                             scope_note=ds.selection.get('note'), offset=off)))

    # -- graph ----------------------------------------------------------------
    def node(self, key: str, nid: str, version=None) -> dict:
        ds = self._ds(key, version)
        nid = self._node_id(nid)
        P = self.b.projections

        def work(db):
            d = self.describe(db, [nid]).get(nid)
            if d is None:
                raise UserError('unknown node ID in this dataset')
            extra: dict[str, Any] = {}
            if d['kind'] == 'Claim' and d['props'].get('predicate') == 'catalog_alias':
                extra['catalog_candidates'] = P.lookup(db, 'catalog', d['props']['object_key'], limit=20)['rows']
                extra['candidate_note'] = 'Exact catalog-key candidates in this dataset only. No identity merge or equivalence edge is asserted.'
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
            if d['kind'] in ('WordToken', 'Sense', 'Lemma'):
                extra['gloss_note'] = GLOSS_NOTE
            return dict(d, **extra)
        return _jsonable(self._bind(ds, self.b.run(ds, work)))

    def neighborhood(self, key: str, nid: str, per_group=None, version=None) -> dict:
        ds = self._ds(key, version)
        nid = self._node_id(nid)
        per = self._int(per_group, 1, 25, 'per', 8)

        def work(db):
            center = self.describe(db, [nid]).get(nid)
            if center is None:
                raise UserError('unknown node ID in this dataset')
            # Exact per-group counts first, then a bounded sample inside each group (review F-15).
            counts = db.execute("SELECT kind, 'out', count(*) FROM portable_edge WHERE source_id=? GROUP BY kind "
                                "UNION ALL SELECT kind, 'in', count(*) FROM portable_edge WHERE target_id=? AND source_id<>? GROUP BY kind",
                                [nid, nid, nid]).fetchall()
            groups: dict[tuple[str, str], tuple[int, list[dict]]] = {}
            for kind, direction, n in counts:
                col = 'source_id' if direction == 'out' else 'target_id'
                sample = _rows(db, f'SELECT * FROM portable_edge WHERE {col}=? AND kind=?'
                                   + (" AND source_id<>?" if direction == 'in' else '') + ' ORDER BY id LIMIT ?',
                               [nid, kind] + ([nid] if direction == 'in' else []) + [GROUP_SAMPLE])
                groups[(kind, direction)] = (n, sample)
            other_ids = [e['target_id'] if d == 'out' else e['source_id'] for (_k, d), (_n, s) in groups.items() for e in s]
            info = self.describe(db, other_ids)
            nodes = {nid: dict(center, center=True)}
            links, aggregates = [], []
            for (kind, direction), (total, group) in sorted(groups.items()):
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
                hidden = total - min(per, len(group))
                if hidden > 0:
                    agg = f'more:{kind}:{direction}'
                    aggregates.append({'id': agg, 'kind': 'Aggregate', 'edge_kind': kind, 'direction': direction, 'total': total,
                                       'hidden': hidden, 'lower_bound': False, 'label': f'+{hidden} {kind}'})
                    links.append({'id': agg + ':link', 'source': nid if direction == 'out' else agg, 'target': agg if direction == 'out' else nid,
                                  'kind': kind, 'aggregate': True})
            return {'center': nid, 'nodes': list(nodes.values()) + aggregates, 'links': links,
                    'edges_total': sum(n for n, _s in groups.values()), 'edges_truncated': False, 'per_group': per,
                    'groups': [{'kind': k, 'direction': d, 'total': n} for (k, d), (n, _s) in sorted(groups.items())],
                    'note': f'Showing up to {per} neighbors per relationship type and direction; group totals are exact.'}
        return _jsonable(self._bind(ds, self.b.run(ds, work)))

    def search_nodes(self, key: str, text: str, version=None) -> dict:
        ds = self._ds(key, version)
        if not isinstance(text, str) or not 1 <= len(text) <= 64:
            raise UserError('search text 1–64 characters')

        def work(db):
            if NODE_RE.match(text):
                hit = self.describe(db, [text])
                return {'rows': list(hit.values())}
            like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            ids = [r[0] for r in db.execute("SELECT id FROM artifact WHERE catalog_key ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM sign_type WHERE label ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM source_record WHERE source_key ILIKE ? ESCAPE '\\' UNION ALL SELECT id FROM claim WHERE object_key ILIKE ? ESCAPE '\\' LIMIT 30", [like] * 4).fetchall()]
            return {'rows': list(self.describe(db, ids).values())}
        return _jsonable(self._bind(ds, self.b.run(ds, work)))


def graph_page(res: dict, offset: int, limit: int) -> dict:
    """Page the backend graph motif's materialized matches without implying the rest are absent (review F-10)."""
    allm = res['matches']
    total = res['occurrences']
    page = allm[offset:offset + limit]
    more_materialized = offset + limit < len(allm)
    cap_reached = len(allm) < total
    out = {'count': total, 'retrievable': len(allm), 'page': page,
           'truncated': more_materialized or (cap_reached and offset + len(page) < total),
           'next_offset': offset + limit if more_materialized else None,
           'materialized_cap_reached': cap_reached}
    if cap_reached:
        out['note'] = (f'The backend graph engine materializes at most {len(allm)} of {total} matches. Rows {len(allm) + 1}–{total} '
                       'exist but cannot be listed from the graph engine; use the Index engine (same count) to page through them.')
    return out


def layer_states(counts: dict, exclusions: list[dict]) -> list[dict]:
    """Never show a zero data layer as a plain 'none' (review F-11)."""
    out = []
    for layer in DATA_LAYERS:
        n = counts[layer]
        state = '' if n else 'unavailable — not supplied by this source (or supplied empty)' + ('; see Exclusions & rejections' if exclusions else '')
        out.append({'layer': layer, 'rows': n, 'state': state})
    no_text = counts['sequence_version'] == 0
    if no_text:
        for r in out:
            if r['layer'] in ('witness', 'sequence_version', 'line', 'occurrence', 'sign_type', 'translation_segment', 'word_token', 'lemma', 'sense') and not r['rows']:
                r['state'] = 'unavailable — no text supplied by this source (not zero occurrences)'
    for t in AUDIT_TABLES:
        out.append({'layer': t, 'rows': counts[t], 'state': 'none recorded' if not counts[t] else ''})
    return out


def _json_or(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def sense_provenance(ds: Dataset, links: list[dict], senses: list[dict]) -> dict:
    """Where this word's sense attribution comes from, by ORACC adapter (oracc-cdl-1 vs oracc-cdl-2).

    oracc-cdl-2 puts each word's own sig/inst/payload pointer on its IN_SENSE link; the shared Sense row holds a
    sense-key locator only. oracc-cdl-1 stored the first attesting instance's sig on the shared Sense row, so that sig
    is shown labelled as such, never as this word's own.
    """
    o = (ds.selection or {}).get('oracc') or {}
    in_sense = next((link for link in links if link['kind'] == 'IN_SENSE'), None)
    q = in_sense['qualification'] if in_sense and isinstance(in_sense['qualification'], dict) else {}
    row = senses[0]['source_locator'] if senses else None
    parsed = _json_or(row)
    out = {'adapter': o.get('adapter'), 'mode': o.get('sense_provenance'), 'adapter_note': o.get('note'),
           'word_sig': q.get('sig'), 'word_inst': q.get('inst'), 'word_payload_pointer': q.get('payload_pointer'),
           'sense_row_locator': parsed, 'superseded': ds.superseded}
    if not in_sense:
        out['note'] = 'This word has no IN_SENSE link (no contextual sense supplied).'
    elif q.get('sig'):
        out['note'] = "This word's own sig, from its IN_SENSE link (per-word provenance)."
    elif isinstance(parsed, str) and parsed:
        out['note'] = ("No per-word sig on this word's IN_SENSE link. The shared Sense row's sig below belongs to the first "
                       'attesting instance in the selection; it is not necessarily this word\'s own.')
        out['sense_row_sig_is'] = 'first attesting instance (shared Sense row), not this word'
    else:
        out['note'] = 'No per-word sig recorded.'
    return out


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
