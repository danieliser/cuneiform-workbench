"""Question-led exploration: a small declarative registry of research questions, each with typed fields.

Every answer is computed over ALL matches in one dataset version (never from a first page) with fixed,
parameterized SQL over the backend's n-gram/posting projections and typed tables, inside the bridge's
bounded run (time, memory, per-request integrity check, version binding). Windows are line-local and
cover every position of a line, blocking positions included, exactly as the backend projection builds them.

Token resolution: a typed token is matched as a parsed reading when that reading exists in the scope;
otherwise as the exact unparsed source text of a position that has no reading (conservative-3 keeps
hyphenated words, determinatives, column breaks and language shifts unparsed); otherwise it is reported
as not found. The response says which applied. Nothing is translated, restored or predicted.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from .bridge import NODE_RE, TOKEN_RE, Dataset, UserError
from .explore import Explorer, _jsonable, _rows

POLICY = 'source'  # questions use readings as edited; the strict policy stays in Advanced query
FIELD_LABELS = {'atf': 'Transliteration', 'signs': 'Sign numbers (ABZ)'}
MAX_PAGE = 50
MAX_GROUPS = 40
TABLET_GROUPS = 25
MAX_LINES = 600
CATALOG_RE = r'^[^\x00-\x1f\x7f]{1,64}$'

FIELD_CHOICE = {'name': 'field', 'label': 'Text layer', 'kind': 'select', 'default': 'atf', 'advanced': True,
                'options': [{'value': 'atf', 'label': 'Transliteration (readings as edited)'},
                            {'value': 'signs', 'label': 'Sign numbers (ABZ, source-derived)'}]}

# Declarative registry. The client renders the form from `fields` and the result view named by `view`.
QUESTIONS = [
    {'id': 'sequence', 'title': 'Where else does this sequence occur?', 'view': 'contexts',
     'prompt': 'Find every line where these readings occur next to each other, and see each one in its line.',
     'explain': 'Counts every match in the dataset, line by line; ? stands for exactly one position of any kind.',
     'fields': [{'name': 'tokens', 'label': 'Sequence', 'kind': 'tokens', 'min': 1, 'max': 5, 'default': '',
                 'placeholder': 'DIŠ ina', 'help': '1–5 readings separated by spaces. ? = any one position. Type sz for š, digits become subscripts (du3 → du₃).'},
                FIELD_CHOICE]},
    {'id': 'between', 'title': 'What occurs between these signs?', 'view': 'fillers',
     'prompt': 'Fix what comes before and after a gap, and list every reading attested in the gap.',
     'explain': 'Groups every attested window A … B by what fills the gap, with how often and on how many tablets. It compares attested variants; it does not predict or restore anything.',
     'fields': [{'name': 'before', 'label': 'Before the gap', 'kind': 'tokens', 'min': 1, 'max': 2, 'default': '', 'placeholder': 'ina'},
                {'name': 'gap', 'label': 'Gap', 'kind': 'select', 'default': '1',
                 'options': [{'value': '1', 'label': '1 position'}, {'value': '2', 'label': '2 positions'}]},
                {'name': 'after', 'label': 'After the gap', 'kind': 'tokens', 'min': 1, 'max': 2, 'default': '', 'placeholder': 'KUR'},
                FIELD_CHOICE]},
    {'id': 'usage', 'title': 'How is this reading used?', 'view': 'usage',
     'prompt': 'See how often a reading occurs, on how many tablets, and what stands immediately before and after it.',
     'explain': 'Neighbors are the positions directly before and after every occurrence within its line; line start and line end are counted separately.',
     'fields': [{'name': 'token', 'label': 'Reading', 'kind': 'tokens', 'min': 1, 'max': 1, 'default': '', 'placeholder': 'ina',
                 'help': 'One reading, or the exact text of an unparsed word such as a-na.'},
                FIELD_CHOICE]},
    {'id': 'tablet', 'title': 'What is written on this tablet?', 'view': 'tablet',
     'prompt': 'Open one tablet by its museum number and read it line by line.',
     'explain': 'Shows the edition as the source gives it, with the source’s own translation lines where it supplies them. Select readings in a line to ask about them.',
     'fields': [{'name': 'catalog', 'label': 'Museum number', 'kind': 'catalog', 'default': '', 'placeholder': 'K.8630',
                 'help': 'As catalogued by the source, for example K.8630 or BM.34567.'},
                FIELD_CHOICE]},
]
BY_ID = {q['id']: q for q in QUESTIONS}

METHOD_NOTES = [
    'Counts are positions in the conservative parse of the records in this dataset, not every ancient attestation.',
    'Matches stay within one line. Broken, lost and unparsed positions count as one position each.',
    'Readings are as edited by the source, including restored and uncertain ones.',
]


def _period(meta_col: str) -> str:
    return f"coalesce(nullif(json_extract_string({meta_col},'$.script.period'),'None'),'Not recorded')"


class Questions:
    def __init__(self, explorer: Explorer):
        self.x = explorer
        self.b = explorer.b
        self._examples: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    # -- registry -----------------------------------------------------------
    def registry(self, key: str | None, version: str | None) -> dict:
        out = {'questions': QUESTIONS, 'method': METHOD_NOTES, 'field_labels': FIELD_LABELS, 'examples': {}}
        if key:
            ds = self.x._ds(key, version)
            out['examples'] = self.examples(ds)
            out = self.x._bind(ds, out)
        return _jsonable(out)

    def examples(self, ds: Dataset) -> dict:
        cache_key = (ds.key, ds.version)
        with self._lock:
            if cache_key in self._examples:
                return self._examples[cache_key]
        scope = self.x._scope(ds, 'atf', None)

        def work(db):
            ex: dict[str, list] = {'sequence': [], 'between': [], 'usage': [], 'tablet': []}
            if scope is None:
                return ex
            args = [scope['namespace'], scope['snapshot_id'], 'atf', POLICY]
            for r in _rows(db, "SELECT readings, count(*) n FROM ngram WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND length=2 "
                               "AND json_extract_string(readings,'$[0]') IS NOT NULL AND json_extract_string(readings,'$[1]') IS NOT NULL "
                               "GROUP BY readings ORDER BY n DESC, readings LIMIT 3", args):
                ex['sequence'].append({'params': {'tokens': ' '.join(json.loads(r['readings']))}, 'n': r['n']})
            for r in _rows(db, "SELECT json_extract_string(readings,'$[0]') a, json_extract_string(readings,'$[2]') c, count(*) n, "
                               "count(DISTINCT json_extract_string(readings,'$[1]')) k FROM ngram WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND length=3 "
                               "AND json_extract_string(readings,'$[0]') IS NOT NULL AND json_extract_string(readings,'$[2]') IS NOT NULL "
                               "AND json_extract_string(readings,'$[0]')<>json_extract_string(readings,'$[2]') "
                               "GROUP BY ALL HAVING count(DISTINCT json_extract_string(readings,'$[1]'))>=5 ORDER BY n DESC, a, c LIMIT 3", args):
                ex['between'].append({'params': {'before': r['a'], 'gap': '1', 'after': r['c']}, 'n': r['n']})
            for r in _rows(db, 'SELECT label, count(*) n FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? '
                               'GROUP BY label ORDER BY n DESC, label LIMIT 3', args):
                ex['usage'].append({'params': {'token': r['label']}, 'n': r['n']})
            for r in _rows(db, "SELECT a.catalog_key, count(*) n FROM translation_segment t JOIN artifact a ON a.source_record_id=t.source_record_id "
                               "JOIN source_binding b ON b.source_record_id=a.source_record_id WHERE b.snapshot_id=? AND t.language='en' "
                               "GROUP BY 1 ORDER BY n DESC, 1 LIMIT 2", [scope['snapshot_id']]):
                ex['tablet'].append({'params': {'catalog': r['catalog_key']}, 'n': r['n'], 'unit': 'translation lines'})
            if not ex['tablet']:
                for r in _rows(db, 'SELECT a.catalog_key, count(*) n FROM artifact a JOIN witness w ON w.artifact_id=a.id JOIN sequence_version s ON s.witness_id=w.id '
                                   'JOIN line l ON l.sequence_id=s.id JOIN source_binding b ON b.source_record_id=s.source_record_id '
                                   "WHERE s.field='atf' AND b.snapshot_id=? GROUP BY 1 ORDER BY n DESC, 1 LIMIT 2", [scope['snapshot_id']]):
                    ex['tablet'].append({'params': {'catalog': r['catalog_key']}, 'n': r['n'], 'unit': 'lines'})
            return ex
        ex = self.b.run(ds, work)
        with self._lock:
            if len(self._examples) > 32:
                self._examples.clear()
            self._examples[cache_key] = ex
        return ex

    # -- dispatch -----------------------------------------------------------
    def ask(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise UserError('JSON object required')
        qid = body.get('question')
        if qid not in BY_ID:
            raise UserError('unknown question')
        params = body.get('params') or {}
        if not isinstance(params, dict):
            raise UserError('params must be an object')
        ds = self.x._ds(body.get('dataset'), body.get('version'))
        field = params.get('field') or 'atf'
        if field not in FIELD_LABELS:
            raise UserError('field must be atf or signs')
        scope = self.x._scope(ds, field, None)
        base = {'question': qid, 'params': params, 'field': field, 'field_label': FIELD_LABELS[field],
                'scope': scope, 'scope_note': ds.selection.get('note'), 'selection_label': ds.selection.get('label')}
        if scope is None and qid != 'tablet':
            return _jsonable(self.x._bind(ds, dict(base, unavailable=f'This dataset has no {FIELD_LABELS[field].lower()} layer, so this question cannot be answered here (unavailable, not zero).')))
        fn = {'sequence': self._sequence, 'between': self._between, 'usage': self._usage, 'tablet': self._tablet}[qid]
        return _jsonable(self.x._bind(ds, dict(base, **fn(ds, scope, field, params))))

    # -- validation helpers ---------------------------------------------------
    @staticmethod
    def _tokens(value, lo: int, hi: int, name: str, allow_any: bool = True) -> list[str]:
        if isinstance(value, str):
            value = value.split()
        if not isinstance(value, list) or not lo <= len(value) <= hi or not all(isinstance(t, str) and TOKEN_RE.match(t) for t in value):
            raise UserError(f'{name}: {lo}–{hi} readings separated by spaces' if lo != hi else f'{name}: one reading')
        if not allow_any and '?' in value:
            raise UserError(f'{name}: ? is not allowed here')
        if all(t == '?' for t in value):
            raise UserError(f'{name}: at least one reading must be concrete (not ?)')
        return value

    @staticmethod
    def _slots(value) -> list[tuple[str, str | None]]:
        """Structured slots from a drill-down: [{'reading': v} | {'raw': v} | {'any': true}]."""
        if not isinstance(value, list) or not 1 <= len(value) <= 5:
            raise UserError('slots: 1–5 entries')
        out = []
        for s in value:
            if not isinstance(s, dict) or len(s) != 1:
                raise UserError('slot: one of reading, raw or any')
            (k, v), = s.items()
            if k == 'any' and v is True:
                out.append(('any', None))
            elif k in ('reading', 'raw') and isinstance(v, str) and TOKEN_RE.match(v):
                out.append((k, v))
            else:
                raise UserError('slot: one of reading, raw or any')
        if all(k == 'any' for k, _ in out):
            raise UserError('slots: at least one concrete entry')
        return out

    # -- shared SQL -------------------------------------------------------------
    def _check_projection(self, db, field: str, length: int) -> None:
        P = self.b.projections
        entry = P.assert_fresh(db)
        assets = json.loads(entry['assets'])
        cfg = json.loads(entry['config'])
        need = 'posting' if length == 1 else 'ngram'
        if need not in assets or field not in cfg['fields'] or POLICY not in cfg['policies'] or (length > 1 and length not in cfg['lengths']):
            raise UserError('this dataset’s index does not cover this question (projection not configured for it)')

    def _resolve(self, db, scope: dict, field: str, tokens: list[str]) -> tuple[list, list]:
        """Each typed token -> ('any'|'reading'|'raw', value), with what it matched."""
        slots, report = [], []
        for t in tokens:
            if t == '?':
                slots.append(('any', None))
                report.append({'token': t, 'as': 'any'})
                continue
            n = db.execute('SELECT count(*) FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? AND label=?',
                           [scope['namespace'], scope['snapshot_id'], field, POLICY, t]).fetchone()[0]
            if n:
                slots.append(('reading', t))
                report.append({'token': t, 'as': 'reading', 'positions': n})
                continue
            m = db.execute('SELECT count(*) FROM token_context t JOIN source_binding b ON b.source_record_id=t.source_record_id '
                           'JOIN source_record r ON r.id=t.source_record_id WHERE r.namespace=? AND b.snapshot_id=? AND t.field=? '
                           'AND t.reading IS NULL AND t.raw=?', [scope['namespace'], scope['snapshot_id'], field, t]).fetchone()[0]
            if m:
                slots.append(('raw', t))
                report.append({'token': t, 'as': 'unparsed', 'positions': m})
            else:
                slots.append(('reading', t))
                report.append({'token': t, 'as': 'missing', 'positions': 0})
        return slots, report

    @staticmethod
    def _slot_report(slots) -> list[dict]:
        return [{'token': '?' if k == 'any' else v, 'as': {'any': 'any', 'reading': 'reading', 'raw': 'unparsed'}[k]} for k, v in slots]

    def _hits(self, scope: dict, field: str, slots: list, group_at: tuple = ()) -> tuple[str, list]:
        """SELECT of every match: one row per window/position with start occurrence, line, sequence, artifact,
        occurrence_ids (JSON) and, for each index in group_at, g{i}_reading and g{i}_id (that position's occurrence)."""
        if len(slots) == 1:
            k, v = slots[0]
            common = ('t.id start_id, t.line_id, t.sequence_id, t.position start_position, t.position end_position, t.artifact_id, '
                      'json_array(t.id) occurrence_ids')
            if k == 'reading':
                return (f'SELECT {common} FROM posting p JOIN token_context t ON t.id=p.occurrence_id '
                        'WHERE p.namespace=? AND p.snapshot_id=? AND p.field=? AND p.policy=? AND p.label=?',
                        [scope['namespace'], scope['snapshot_id'], field, POLICY, v])
            return (f'SELECT {common} FROM token_context t JOIN source_binding b ON b.source_record_id=t.source_record_id '
                    'JOIN source_record r ON r.id=t.source_record_id WHERE r.namespace=? AND b.snapshot_id=? AND t.field=? '
                    'AND t.reading IS NULL AND t.raw=?', [scope['namespace'], scope['snapshot_id'], field, v])
        joins, where, args = [], [], []
        for i, (k, v) in enumerate(slots):
            if k == 'reading':
                where.append(f"json_extract_string(n.readings,'$[{i}]')=?")
                args.append(v)
            elif k == 'raw':  # semi-join on the (small) set of positions with this exact unparsed text
                where.append(f"json_extract_string(n.readings,'$[{i}]') IS NULL AND json_extract_string(n.occurrence_ids,'$[{i}]') IN "
                             '(SELECT id FROM occurrence WHERE reading IS NULL AND raw=?)')
                args.append(v)
        cols = ["json_extract_string(n.occurrence_ids,'$[0]') start_id", 'n.line_id', 'n.sequence_id', 'n.start_position', 'n.end_position',
                'n.artifact_id', 'n.occurrence_ids']
        for i in group_at:
            cols += [f"json_extract_string(n.readings,'$[{i}]') g{i}_reading", f"json_extract_string(n.occurrence_ids,'$[{i}]') g{i}_id"]
        sql = (f"SELECT {', '.join(cols)} FROM ngram n {' '.join(joins)} WHERE n.namespace=? AND n.snapshot_id=? AND n.field=? "
               f"AND n.policy=? AND n.length=?" + ''.join(' AND ' + w for w in where))
        return sql, [scope['namespace'], scope['snapshot_id'], field, POLICY, len(slots), *args]

    # Each request materializes its full match set once (bounded by the positions of its concrete tokens) and
    # computes every aggregate from it. Temp tables belong to this connection only and are dropped afterwards.
    @staticmethod
    def _materialize(db, name: str, sql: str, args: list) -> None:
        db.execute(f'CREATE OR REPLACE TEMP TABLE {name} AS {sql}', args)

    @staticmethod
    def _drop(db, *names: str) -> None:
        for n in names:
            db.execute(f'DROP TABLE IF EXISTS temp.{n}')

    @staticmethod
    def _totals(db, h: str) -> dict:
        n, tablets, lines = db.execute(f'SELECT count(*), count(DISTINCT artifact_id), count(DISTINCT line_id) FROM {h}').fetchone()
        return {'matches': n, 'tablets': tablets, 'lines': lines}

    @staticmethod
    def _by_period(db, h: str) -> list[dict]:
        return _rows(db, f'SELECT {_period("a.metadata")} period, count(*) matches, count(DISTINCT h.artifact_id) tablets '
                         f'FROM {h} h JOIN artifact a ON a.id=h.artifact_id GROUP BY 1 ORDER BY matches DESC, period')

    def _by_tablet(self, db, h: str, period: str | None) -> dict:
        pf, pa = self._period_filter(period)
        total = db.execute(f'SELECT count(DISTINCT h.artifact_id) FROM {h} h JOIN artifact a ON a.id=h.artifact_id WHERE true{pf}', pa).fetchone()[0]
        rows = _rows(db, f'SELECT a.catalog_key tablet, {_period("a.metadata")} period, count(*) matches, count(DISTINCT h.line_id) lines '
                         f'FROM {h} h JOIN artifact a ON a.id=h.artifact_id WHERE true{pf} GROUP BY 1, 2 ORDER BY matches DESC, tablet LIMIT {TABLET_GROUPS}', pa)
        return {'rows': rows, 'total': total, 'truncated': total > len(rows)}

    @staticmethod
    def _period_filter(period) -> tuple[str, list]:
        if period in (None, ''):
            return '', []
        if not isinstance(period, str) or len(period) > 64:
            raise UserError('invalid period')
        return f' AND {_period("a.metadata")}=?', [period]

    def _page(self, db, h: str, offset: int, limit: int, period: str | None) -> dict:
        pf, pa = self._period_filter(period)
        filtered = db.execute(f'SELECT count(*) FROM {h} h JOIN artifact a ON a.id=h.artifact_id WHERE true{pf}', pa).fetchone()[0] if pf else None
        rows = _rows(db, f'SELECT h.line_id, h.artifact_id, h.occurrence_ids, h.start_position, h.end_position, a.catalog_key tablet, '
                         f'{_period("a.metadata")} period, l.label line_label, l.surface, l.column_label, l.ordinal '
                         f'FROM {h} h JOIN artifact a ON a.id=h.artifact_id JOIN line l ON l.id=h.line_id WHERE true{pf} '
                         f'ORDER BY a.catalog_key, h.artifact_id, h.sequence_id, l.ordinal, h.start_position LIMIT ? OFFSET ?', pa + [limit + 1, offset])
        more = len(rows) > limit
        rows = rows[:limit]
        tokens = self._line_tokens(db, list({r['line_id'] for r in rows}))
        out = [{'tablet': r['tablet'], 'period': r['period'], 'artifact_id': r['artifact_id'], 'line_id': r['line_id'],
                'line': {'label': r['line_label'], 'surface': r['surface'], 'column': r['column_label'], 'ordinal': r['ordinal']},
                'occurrence_ids': json.loads(r['occurrence_ids']), 'start_position': r['start_position'], 'end_position': r['end_position'],
                'tokens': tokens.get(r['line_id'], [])} for r in rows]
        return {'rows': out, 'offset': offset, 'limit': limit, 'next_offset': offset + limit if more else None, 'filtered_matches': filtered}

    @staticmethod
    def _groups(db, h: str, at: tuple, limit: int) -> dict:
        """Group every row of h by what stands at positions `at`: the parsed reading, or, for a position without a
        reading, its exact source text and kind. The distinct count is exact; the top `limit` groups are returned."""
        # Parsed rows need no join; only rows with a position lacking a reading look up its source text (equality joins only).
        all_parsed = ' AND '.join(f'g{i}_reading IS NOT NULL' for i in at)
        parsed = ', '.join(f'g{i}_reading r{i}, NULL::VARCHAR raw{i}, NULL::VARCHAR kind{i}' for i in at)
        # A hash join against the whole occurrence table exceeds the memory bound, so the needed IDs are collected
        # first and looked up once (the small ID set is the build side).
        need = ' UNION '.join(f'SELECT g{i}_id id FROM {h} WHERE g{i}_reading IS NULL' for i in at)
        joins = ' '.join(f'LEFT JOIN q_raw o{i} ON o{i}.id=h.g{i}_id' for i in at)
        keys = ', '.join(f'h.g{i}_reading r{i}, CASE WHEN h.g{i}_reading IS NULL THEN o{i}.raw END raw{i}, '
                         f'CASE WHEN h.g{i}_reading IS NULL THEN o{i}.kind END kind{i}' for i in at)
        knames = ', '.join(f'r{i}, raw{i}, kind{i}' for i in at)
        db.execute(f'CREATE OR REPLACE TEMP TABLE q_ids AS {need}')
        db.execute('CREATE OR REPLACE TEMP TABLE q_raw AS SELECT o.id, o.raw, o.kind FROM occurrence o WHERE o.id IN (SELECT id FROM q_ids)')
        db.execute(f'CREATE OR REPLACE TEMP TABLE q_keys AS SELECT {parsed}, artifact_id FROM {h} WHERE {all_parsed} '
                   f'UNION ALL SELECT {keys}, h.artifact_id FROM (SELECT * FROM {h} WHERE NOT ({all_parsed})) h {joins}')
        db.execute('DROP TABLE IF EXISTS temp.q_ids')
        db.execute('DROP TABLE IF EXISTS temp.q_raw')
        try:
            distinct = db.execute(f'SELECT count(*) FROM (SELECT DISTINCT {knames} FROM q_keys)').fetchone()[0]
            groups = _rows(db, f'SELECT {knames}, count(*) matches, count(DISTINCT artifact_id) tablets FROM q_keys GROUP BY ALL '
                               f'ORDER BY matches DESC, tablets DESC, {knames} NULLS LAST LIMIT {limit}')
            blocked = db.execute('SELECT count(*), count(DISTINCT artifact_id) FROM q_keys WHERE '
                                 + ' OR '.join(f'r{i} IS NULL' for i in at)).fetchone()
        finally:
            db.execute('DROP TABLE IF EXISTS temp.q_keys')
        rows = [{'parts': [{'text': g[f'r{i}'], 'parsed': True} if g[f'r{i}'] is not None
                           else {'text': g[f'raw{i}'], 'parsed': False, 'kind': g[f'kind{i}']} for i in at],
                 'matches': g['matches'], 'tablets': g['tablets']} for g in groups]
        return {'distinct': distinct, 'rows': rows, 'truncated': distinct > len(rows),
                'unparsed_or_broken': {'matches': blocked[0], 'tablets': blocked[1]}}

    @staticmethod
    def _line_tokens(db, line_ids: list[str]) -> dict[str, list]:
        out: dict[str, list] = {}
        for i in range(0, len(line_ids), 100):
            chunk = line_ids[i:i + 100]
            for r in _rows(db, f"SELECT id, line_id, position, raw, reading, kind, preservation, certainty FROM occurrence "
                               f"WHERE line_id IN ({','.join('?' * len(chunk))}) ORDER BY line_id, position", chunk):
                out.setdefault(r.pop('line_id'), []).append(r)
        return out

    @staticmethod
    def _int(v, lo, hi, name, default):
        return Explorer._int(v, lo, hi, name, default)

    @staticmethod
    def _part_slot(p: dict) -> dict:
        return {'reading': p['text']} if p['parsed'] else {'raw': p['text']}

    @staticmethod
    def _slot_dicts(slots) -> list[dict]:
        return [{'reading': v} if k == 'reading' else {'raw': v} if k == 'raw' else {'any': True} for k, v in slots]

    @staticmethod
    def _empty_groups() -> dict:
        return {'distinct': 0, 'rows': [], 'truncated': False, 'unparsed_or_broken': {'matches': 0, 'tablets': 0}}

    # -- 1. where else does this sequence occur --------------------------------
    def _sequence(self, ds, scope, field, params) -> dict:
        if params.get('slots') is not None:
            slots, typed = self._slots(params['slots']), None
        else:
            typed = self._tokens(params.get('tokens'), 1, 5, 'Sequence')
            slots = None
        offset = self._int(params.get('offset'), 0, 100000, 'offset', 0)
        limit = self._int(params.get('limit'), 1, MAX_PAGE, 'limit', 20)
        period = params.get('period') or None
        self._period_filter(period)

        def work(db):
            nonlocal slots
            if slots is None:
                slots, resolved = self._resolve(db, scope, field, typed)
            else:
                resolved = self._slot_report(slots)
            if len(slots) == 1 and slots[0][0] == 'any':
                raise UserError('a single ? matches everything; give a reading')
            self._check_projection(db, field, len(slots))
            sql, args = self._hits(scope, field, slots)
            self._materialize(db, 'q_hits', sql, args)
            try:
                totals = self._totals(db, 'q_hits')
                out = {'resolved': resolved, 'slots': self._slot_dicts(slots), 'totals': totals, 'by_period': [],
                       'by_tablet': {'rows': [], 'total': 0, 'truncated': False}, 'period': period,
                       'page': {'rows': [], 'offset': 0, 'limit': limit, 'next_offset': None, 'filtered_matches': None}}
                if totals['matches']:
                    out['by_period'] = self._by_period(db, 'q_hits')
                    out['by_tablet'] = self._by_tablet(db, 'q_hits', period)
                    out['page'] = self._page(db, 'q_hits', offset, limit, period)
                return out
            finally:
                self._drop(db, 'q_hits')
        return self.b.run(ds, work)

    # -- 2. what occurs between --------------------------------------------------
    def _between(self, ds, scope, field, params) -> dict:
        before = self._tokens(params.get('before'), 1, 2, 'Before the gap')
        after = self._tokens(params.get('after'), 1, 2, 'After the gap')
        gap = self._int(params.get('gap'), 1, 2, 'gap', 1)
        if len(before) + gap + len(after) > 5:
            raise UserError('at most 5 positions in total (before + gap + after)')

        def work(db):
            sb, rb = self._resolve(db, scope, field, before)
            sa, ra = self._resolve(db, scope, field, after)
            slots = sb + [('any', None)] * gap + sa
            self._check_projection(db, field, len(slots))
            at = tuple(range(len(sb), len(sb) + gap))
            sql, args = self._hits(scope, field, slots, at)
            self._materialize(db, 'q_hits', sql, args)
            try:
                totals = self._totals(db, 'q_hits')
                g = self._groups(db, 'q_hits', at, MAX_GROUPS) if totals['matches'] else self._empty_groups()
            finally:
                self._drop(db, 'q_hits')
            rows = [{'filler': r['parts'], 'matches': r['matches'], 'tablets': r['tablets'],
                     'slots': self._slot_dicts(sb) + [self._part_slot(p) for p in r['parts']] + self._slot_dicts(sa)} for r in g['rows']]
            return {'resolved': {'before': rb, 'after': ra}, 'gap': gap, 'totals': totals, 'distinct_fillers': g['distinct'],
                    'shown': len(rows), 'truncated': g['truncated'], 'rows': rows, 'unparsed_or_broken': g['unparsed_or_broken']}
        return self.b.run(ds, work)

    # -- 3. how is this reading used ----------------------------------------------
    def _usage(self, ds, scope, field, params) -> dict:
        token = self._tokens(params.get('token'), 1, 1, 'Reading', allow_any=False)

        def work(db):
            slots, resolved = self._resolve(db, scope, field, token)
            self._check_projection(db, field, 1)
            self._check_projection(db, field, 2)
            sql, args = self._hits(scope, field, slots)
            self._materialize(db, 'q_hits', sql, args)
            try:
                totals = self._totals(db, 'q_hits')
                out = {'resolved': resolved, 'slots': self._slot_dicts(slots), 'totals': totals, 'before': None, 'after': None,
                       'by_period': [], 'examples': None}
                if not totals['matches']:
                    return out
                out['by_period'] = self._by_period(db, 'q_hits')
                out['examples'] = self._page(db, 'q_hits', 0, 8, None)
            finally:
                self._drop(db, 'q_hits')
            for side, pair, gi in (('before', [('any', None)] + slots, 0), ('after', slots + [('any', None)], 1)):
                wsql, wargs = self._hits(scope, field, pair, (gi,))
                self._materialize(db, 'q_side', wsql, wargs)
                try:
                    n = db.execute('SELECT count(*) FROM q_side').fetchone()[0]
                    g = self._groups(db, 'q_side', (gi,), 20) if n else self._empty_groups()
                finally:
                    self._drop(db, 'q_side')
                rows = []
                for r in g['rows']:
                    part = r['parts'][0]
                    pair_slots = ([self._part_slot(part)] + self._slot_dicts(slots)) if side == 'before' else (self._slot_dicts(slots) + [self._part_slot(part)])
                    rows.append({'neighbor': part, 'matches': r['matches'], 'tablets': r['tablets'], 'slots': pair_slots})
                # Windows are line-local and cover every position, so occurrences without a window on this side sit at the line edge.
                out[side] = {'windows': n, 'line_edge': totals['matches'] - n, 'distinct': g['distinct'], 'rows': rows,
                             'truncated': g['truncated'], 'unparsed_or_broken': g['unparsed_or_broken']}
            return out
        return self.b.run(ds, work)

    # -- 4. what is written on this tablet ------------------------------------------
    def _tablet(self, ds, scope, field, params) -> dict:
        import re
        cat = params.get('catalog')
        if not isinstance(cat, str) or not re.match(CATALOG_RE, cat.strip()):
            raise UserError('Museum number: 1–64 characters')
        cat = cat.strip()

        def work(db):
            arts = _rows(db, 'SELECT a.id, a.catalog_key, a.metadata, r.namespace, b.snapshot_id FROM artifact a JOIN source_record r ON r.id=a.source_record_id '
                             'JOIN source_binding b ON b.source_record_id=r.id WHERE a.catalog_key=? ORDER BY a.id LIMIT 5', [cat])
            if not arts:
                like = _rows(db, 'SELECT catalog_key FROM artifact WHERE starts_with(lower(catalog_key), lower(?)) ORDER BY length(catalog_key), catalog_key LIMIT 8', [cat])
                return {'found': False, 'suggestions': [r['catalog_key'] for r in like]}
            a = arts[0]
            return dict(self._tablet_body(db, a, field), found=True, other_records=len(arts) - 1)
        return self.b.run(ds, work)

    def _tablet_body(self, db, a: dict, field: str) -> dict:
        meta = {}
        try:
            meta = json.loads(a['metadata'] or '{}')
        except ValueError:
            meta = {}
        script = meta.get('script') if isinstance(meta.get('script'), dict) else {}
        genres = []
        for g in meta.get('genres') or []:
            if isinstance(g, dict) and isinstance(g.get('category'), list):
                genres.append(' › '.join(str(c) for c in g['category'] if c != 'CANONICAL') + (' (uncertain)' if g.get('uncertain') else ''))
        ext = meta.get('externalNumbers') if isinstance(meta.get('externalNumbers'), dict) else {}
        info = {'catalog': a['catalog_key'], 'artifact_id': a['id'], 'namespace': a['namespace'],
                'description': meta.get('description') or None, 'collection': meta.get('collection') or None, 'museum': meta.get('museum') or None,
                'period': script.get('period') if script.get('period') not in (None, 'None') else None,
                'period_uncertain': bool(script.get('uncertain')), 'genres': [g for g in genres if g],
                'cdli': ext.get('cdliNumber') or None, 'joins': len(meta.get('joins') or [])}
        seqs = _rows(db, 'SELECT s.id, s.field, s.source_record_id FROM witness w JOIN sequence_version s ON s.witness_id=w.id WHERE w.artifact_id=? ORDER BY s.field, s.id', [a['id']])
        layers = sorted({s['field'] for s in seqs})
        seq = next((s for s in seqs if s['field'] == field), None)
        lines, translations, truncated = [], [], False
        if seq:
            lr = _rows(db, 'SELECT id, ordinal, label, surface, column_label, start_byte, end_byte FROM line WHERE sequence_id=? ORDER BY ordinal LIMIT ?', [seq['id'], MAX_LINES + 1])
            truncated = len(lr) > MAX_LINES
            lr = lr[:MAX_LINES]
            toks = self._line_tokens(db, [r['id'] for r in lr])
            lines = [dict(r, tokens=toks.get(r['id'], [])) for r in lr]
            if field == 'atf':
                for t in _rows(db, 'SELECT language, text, locator FROM translation_segment WHERE source_record_id=? ORDER BY id LIMIT 400', [seq['source_record_id']]):
                    parts = str(t['locator'] or '').split(':')
                    start = int(parts[2]) if len(parts) == 4 and parts[0] == 'atf' and parts[1] == 'utf8' and parts[2].isdigit() else None
                    after = None
                    if start is not None:
                        prior = [ln for ln in lines if ln['end_byte'] <= start]
                        after = prior[-1]['id'] if prior else None
                    translations.append({'language': t['language'], 'text': t['text'], 'after_line': after, 'start_byte': start})
                translations.sort(key=lambda t: (t['start_byte'] is None, t['start_byte'] or 0))
        for ln in lines:
            ln.pop('start_byte', None)
            ln.pop('end_byte', None)
        return {'tablet': info, 'layers': layers, 'lines': lines, 'lines_truncated': truncated, 'translations': translations,
                'translation_note': 'Translations are the source’s own, shown after the line they follow in the source; which words they render is not aligned.'}

    # -- helpers for the client -------------------------------------------------------
    def suggest(self, key: str, version: str | None, field: str | None, prefix: str | None) -> dict:
        ds = self.x._ds(key, version)
        field = field or 'atf'
        if field not in FIELD_LABELS:
            raise UserError('field must be atf or signs')
        if not isinstance(prefix, str) or not TOKEN_RE.match(prefix):
            raise UserError('prefix: 1–64 visible characters')
        scope = self.x._scope(ds, field, None)

        def work(db):
            if scope is None:
                return {'readings': [], 'unparsed': []}
            readings = _rows(db, 'SELECT label AS value, count(*) positions FROM posting WHERE namespace=? AND snapshot_id=? AND field=? AND policy=? '
                                 'AND starts_with(label, ?) GROUP BY 1 ORDER BY positions DESC, value LIMIT 10',
                             [scope['namespace'], scope['snapshot_id'], field, POLICY, prefix])
            unparsed = []
            if len(prefix) >= 2:
                unparsed = _rows(db, 'SELECT t.raw AS value, count(*) positions FROM token_context t JOIN source_binding b ON b.source_record_id=t.source_record_id '
                                     'JOIN source_record r ON r.id=t.source_record_id WHERE r.namespace=? AND b.snapshot_id=? AND t.field=? AND t.reading IS NULL '
                                     'AND starts_with(t.raw, ?) GROUP BY 1 ORDER BY positions DESC, value LIMIT 5',
                                 [scope['namespace'], scope['snapshot_id'], field, prefix])
            return {'readings': readings, 'unparsed': unparsed}
        return _jsonable(self.x._bind(ds, dict(self.b.run(ds, work), prefix=prefix, field=field)))

    def tablets(self, key: str, version: str | None, prefix: str | None) -> dict:
        import re
        ds = self.x._ds(key, version)
        if not isinstance(prefix, str) or not re.match(CATALOG_RE, prefix):
            raise UserError('prefix: 1–64 characters')

        def work(db):
            return {'tablets': [r['catalog_key'] for r in _rows(db, 'SELECT catalog_key FROM artifact WHERE starts_with(lower(catalog_key), lower(?)) '
                                                                   'ORDER BY length(catalog_key), catalog_key LIMIT 10', [prefix])]}
        return _jsonable(self.x._bind(ds, self.b.run(ds, work)))

    def passage(self, key: str, version: str | None, line_id: str | None, around) -> dict:
        ds = self.x._ds(key, version)
        if not isinstance(line_id, str) or not NODE_RE.match(line_id):
            raise UserError('invalid line ID')
        k = self._int(around, 0, 5, 'around', 2)

        def work(db):
            row = _rows(db, 'SELECT l.sequence_id, l.ordinal, s.field, w.artifact_id FROM line l JOIN sequence_version s ON s.id=l.sequence_id '
                            'JOIN witness w ON w.id=s.witness_id WHERE l.id=?', [line_id])
            if not row:
                raise UserError('unknown line in this dataset')
            r = row[0]
            lr = _rows(db, 'SELECT id, ordinal, label, surface, column_label FROM line WHERE sequence_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal',
                       [r['sequence_id'], r['ordinal'] - k, r['ordinal'] + k])
            toks = self._line_tokens(db, [x['id'] for x in lr])
            total = db.execute('SELECT count(*) FROM line WHERE sequence_id=?', [r['sequence_id']]).fetchone()[0]
            a = _rows(db, 'SELECT catalog_key, metadata FROM artifact WHERE id=?', [r['artifact_id']])[0]
            meta = {}
            try:
                meta = json.loads(a['metadata'] or '{}')
            except ValueError:
                pass
            script = meta.get('script') if isinstance(meta.get('script'), dict) else {}
            return {'line_id': line_id, 'field': r['field'], 'lines': [dict(x, tokens=toks.get(x['id'], [])) for x in lr], 'lines_in_text': total,
                    'tablet': {'catalog': a['catalog_key'], 'artifact_id': r['artifact_id'], 'description': meta.get('description') or None,
                               'period': script.get('period') if script.get('period') not in (None, 'None') else None}}
        return _jsonable(self.x._bind(ds, self.b.run(ds, work)))
