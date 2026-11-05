"""Guided explorations: English-named entry points into the Questions view, each a real filter over the data.

Every exploration is a fixed genre filter over the source catalogue's own genre labels (eBL `genres.category`,
matched by exact path prefix) and/or translation availability. Counts are computed over all texts in the bound
dataset version. Nothing is classified by the UI: texts without a genre label are never assigned one, and texts
without a source translation are shown as untranslated, never machine-translated.
"""
from __future__ import annotations

import json
import re

from .bridge import NODE_RE, TOKEN_RE, UserError
from .explore import _jsonable, _rows
from . import reading_aids as RA
from .translations import place

EXPLORATIONS = [
    {'id': 'translated', 'title': 'Browse passages with translations',
     'prompt': 'Read passages where the source edition gives its own translation next to the transliteration.',
     'genres': None, 'translated': True,
     'explain': 'Every text whose source record includes translation lines. Each translation is shown beside the lines it covers in the source; it renders the passage, not individual signs.'},
    {'id': 'medicine', 'title': 'Explore medical texts',
     'prompt': 'Texts the source catalogues as medicine: symptoms, diagnoses and remedies.',
     'genres': ['CANONICAL > Technical > Medicine'],
     'explain': 'Texts whose source genre is Technical › Medicine. About half of all texts carry no genre label and are not included.'},
    {'id': 'omens', 'title': 'Compare omen openings',
     'prompt': 'Omen collections list observations as “if … then …”. See how their lines open and what the editors’ translations say.',
     'genres': ['CANONICAL > Divination'], 'view': 'openings', 'opening': 'DIŠ',
     'explain': 'Lines in texts whose source genre is Divination. Most omen lines open with DIŠ, usually read šumma (Borger marks this with a query) ‘if’ in this position (Borger, MZL 748); the next readings name what was observed.'},
    {'id': 'letters', 'title': 'Read letters',
     'prompt': 'Letters and reports the source catalogues as archival correspondence.',
     'genres': ['ARCHIVAL > Letter'],
     'explain': 'Texts whose source genre is Archival › Letter, including reports.'},
    {'id': 'literature', 'title': 'Find literary compositions',
     'prompt': 'Epics, hymns, laments and wisdom texts, with links to scholars’ recordings where one exists for the composition.',
     'genres': ['CANONICAL > Literature'],
     'explain': 'Texts whose source genre is Literature. Recordings are linked only when the source names the composition.'},
]
BY_ID = {e['id']: e for e in EXPLORATIONS}
PAGE = 25
PASSAGE_TEXTS = 4
PASSAGES_PER_TEXT = 3
MAX_LINES_PER_TEXT = 2000


def _match(genres: list[str], prefixes: list[str] | None) -> bool:
    if prefixes is None:
        return True
    return any(g == p or g.startswith(p + ' > ') for g in genres for p in prefixes)


def _short(g: str) -> str:
    parts = [p for p in g.split(' > ') if p not in ('CANONICAL', 'ARCHIVAL', 'MONUMENTAL', 'OTHER')]
    return ' › '.join(parts) if parts else g.title()


class Explorations:
    def __init__(self, questions):
        self.q = questions
        self.x = questions.x
        self.b = questions.b
        self.genres = RA.GenreIndex()

    # -- registry ------------------------------------------------------------
    def registry(self, ds) -> list[dict]:
        arts = self.genres.get(self.b, ds)
        out = []
        for e in EXPLORATIONS:
            sel = [a for a in arts.values() if _match(a['genres'], e['genres']) and (not e.get('translated') or a['tr'])]
            out.append({k: v for k, v in e.items() if k != 'genres'} | {
                'filter': {'genres': e['genres'], 'translated': bool(e.get('translated'))},
                'texts': len(sel), 'with_english': sum(1 for a in sel if a['tr'].get('en')),
                'with_any_translation': sum(1 for a in sel if a['tr'])})
        return [e for e in out if e['texts']]

    # -- dispatch -----------------------------------------------------------
    def explore(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise UserError('JSON object required')
        e = BY_ID.get(body.get('exploration'))
        if e is None:
            raise UserError('unknown exploration')
        p = body.get('params') or {}
        if not isinstance(p, dict):
            raise UserError('params must be an object')
        ds = self.x._ds(body.get('dataset'), body.get('version'))
        view = p.get('view') or e.get('view') or 'texts'
        if view not in ('texts', 'passages', 'openings'):
            raise UserError('view must be texts, passages or openings')
        if view == 'openings' and e['id'] != 'omens':
            raise UserError('openings are only defined for omen texts')
        offset = self.x._int(p.get('offset'), 0, 100000, 'offset', 0)
        sub = p.get('subgenre') or None
        if sub is not None and (not isinstance(sub, str) or len(sub) > 200 or not any(sub == g or sub.startswith(g + ' > ') for g in (e['genres'] or [sub]))):
            raise UserError('subgenre must lie inside this exploration')
        lang = p.get('language') or None
        if lang is not None and (not isinstance(lang, str) or not re.fullmatch(r'[a-z]{2,3}', lang)):
            raise UserError('language: 2–3 letter code')
        only_tr = bool(p.get('translated_only')) or bool(e.get('translated')) or view == 'passages'
        arts = self.genres.get(self.b, ds)
        prefixes = [sub] if sub else e['genres']
        sel = [(aid, a) for aid, a in arts.items() if _match(a['genres'], prefixes)]
        base_n = len(sel)
        if only_tr:
            sel = [(aid, a) for aid, a in sel if (a['tr'].get(lang) if lang else a['tr'])]
        sel.sort(key=lambda x: (-(x[1]['tr'].get(lang or 'en', 0)), -sum(x[1]['tr'].values()), x[1]['catalog']))
        langs: dict[str, int] = {}
        subs: dict[str, int] = {}
        for _aid, a in ((aid, a) for aid, a in arts.items() if _match(a['genres'], e['genres'])):
            for l in a['tr']:
                langs[l] = langs.get(l, 0) + 1
            for g in a['genres']:
                if e['genres'] and _match([g], e['genres']) and g not in e['genres'] and g.count(' > ') <= e['genres'][0].count(' > ') + 1:
                    subs[g] = subs.get(g, 0) + 1
        out = {'exploration': e['id'], 'view': view, 'params': p, 'title': e['title'], 'explain': e['explain'],
               'totals': {'texts_in_filter': base_n, 'shown_texts': len(sel), 'with_english': sum(1 for _i, a in sel if a['tr'].get('en'))},
               'languages': sorted(({'code': k, 'texts': v} for k, v in langs.items()), key=lambda r: -r['texts']),
               'subgenres': sorted(({'genre': k, 'label': _short(k), 'texts': v} for k, v in subs.items()), key=lambda r: -r['texts'])[:12],
               'filters': {'subgenre': sub, 'language': lang, 'translated_only': only_tr}}
        if view == 'texts':
            page = sel[offset:offset + PAGE]
            out['texts'] = [self._text_row(aid, a, prefixes) for aid, a in page]
            out['page'] = {'offset': offset, 'limit': PAGE, 'next_offset': offset + PAGE if offset + PAGE < len(sel) else None}
        elif view == 'passages':
            page = sel[offset:offset + PASSAGE_TEXTS]
            out['passages'] = self._passages(ds, page, lang)
            out['page'] = {'offset': offset, 'limit': PASSAGE_TEXTS, 'next_offset': offset + PASSAGE_TEXTS if offset + PASSAGE_TEXTS < len(sel) else None}
        else:
            out['openings'] = self._openings(ds, [aid for aid, _a in sel] if not only_tr else [aid for aid, _a in sel], e['opening'], arts)
        return _jsonable(self.x._bind(ds, out))

    @staticmethod
    def _text_row(aid: str, a: dict, prefixes) -> dict:
        g = [x for x in a['genres'] if _match([x], prefixes)] or a['genres']
        base = prefixes[0] if prefixes and len(prefixes) == 1 else None

        def rel(x):  # inside an exploration, name only what is more specific than its filter
            return _short(x[len(base) + 3:]) if base and x.startswith(base + ' > ') else _short(x)
        return {'artifact_id': aid, 'catalog': a['catalog'], 'genres': [rel(x) for x in g][:2], 'period': a['period'],
                'lines': a['lines'], 'translations': a['tr'], 'description': a['description'],
                'recordings': len(RA.recordings_for(a['genres']))}

    # -- passages ---------------------------------------------------------------
    def load_text(self, db, record_id: str, field: str = 'atf', max_lines: int = MAX_LINES_PER_TEXT) -> dict:
        seq = db.execute('SELECT id FROM sequence_version WHERE source_record_id=? AND field=?', [record_id, field]).fetchone()
        lines = []
        if seq:
            lines = _rows(db, 'SELECT id, ordinal, label, surface, column_label, start_byte, end_byte FROM line WHERE sequence_id=? ORDER BY ordinal LIMIT ?',
                          [seq[0], max_lines + 1])
        segs = _rows(db, 'SELECT id, language, text, locator FROM translation_segment WHERE source_record_id=? ORDER BY id LIMIT 600', [record_id]) if field == 'atf' else []
        atf = None
        if segs:
            raw = db.execute('SELECT raw_json FROM source_record WHERE id=?', [record_id]).fetchone()
            try:
                atf = (json.loads(raw[0]).get('atf') or '').encode() if raw else None
            except ValueError:
                atf = None
        truncated = len(lines) > max_lines
        lines = lines[:max_lines]
        placed = place(lines, segs, atf) if segs else []
        return {'lines': lines, 'placed': placed, 'truncated': truncated}

    def _passages(self, ds, page, lang) -> list[dict]:
        def work(db):
            res = []
            for aid, a in page:
                t = self.load_text(db, a['record'])
                pl = [p for p in t['placed'] if p['first_line'] and (lang is None or p['language'] == lang)]
                if lang is None:
                    en = [p for p in pl if p['language'] == 'en']
                    pl = en or pl
                pl = pl[:PASSAGES_PER_TEXT]
                by_id = {ln['id']: i for i, ln in enumerate(t['lines'])}
                blocks, need = [], []
                for p in pl:
                    i0, i1 = by_id[p['first_line']], by_id[p['last_line']]
                    ids = [ln['id'] for ln in t['lines'][i0:i1 + 1]]
                    need += ids
                    blocks.append({'translation': {k: p[k] for k in ('id', 'language', 'text', 'status', 'extent', 'lines')}, 'line_ids': ids})
                toks = self.q._line_tokens(db, need)
                meta = {ln['id']: ln for ln in t['lines']}
                for bl in blocks:
                    bl['lines'] = [{'id': i, 'label': meta[i]['label'], 'surface': meta[i]['surface'], 'column': meta[i]['column_label'],
                                    'tokens': RA.annotate(toks.get(i, []), 'atf')} for i in bl.pop('line_ids')]
                res.append({'catalog': a['catalog'], 'artifact_id': aid, 'period': a['period'], 'genres': [_short(g) for g in a['genres']][:2],
                            'translations': a['tr'], 'shown': len(blocks), 'blocks': blocks})
            return res
        return self.b.run(ds, work)

    # -- omen openings ------------------------------------------------------------
    def _openings(self, ds, artifact_ids: list[str], opening: str, arts: dict) -> dict:
        tr_records = sorted({a['record'] for a in arts.values() if a['tr'].get('en')})

        def work(db):
            db.execute('CREATE OR REPLACE TEMP TABLE x_art AS SELECT unnest(?::VARCHAR[]) artifact_id', [artifact_ids])
            db.execute('CREATE OR REPLACE TEMP TABLE x_tr AS SELECT unnest(?::VARCHAR[]) record', [tr_records])
            try:
                db.execute("CREATE OR REPLACE TEMP TABLE x_lines AS SELECT l.id line_id, w.artifact_id, s.source_record_id, "
                           "s.source_record_id IN (SELECT record FROM x_tr) tr FROM line l "
                           "JOIN sequence_version s ON s.id=l.sequence_id JOIN witness w ON w.id=s.witness_id "
                           "JOIN x_art g ON g.artifact_id=w.artifact_id WHERE s.field='atf' AND NOT starts_with(coalesce(l.label,''), '#')")
                db.execute('CREATE OR REPLACE TEMP TABLE x_occ AS SELECT o.line_id, o.position, o.reading, o.raw, o.kind FROM occurrence o '
                           'WHERE o.position<3 AND o.line_id IN (SELECT line_id FROM x_lines)')
                total_lines, tablets = db.execute('SELECT count(*), count(DISTINCT artifact_id) FROM x_lines').fetchone()
                opened = db.execute('SELECT count(*), count(DISTINCT x.artifact_id) FROM x_occ o JOIN x_lines x USING (line_id) '
                                    'WHERE o.position=0 AND o.reading=?', [opening]).fetchone()
                firsts = _rows(db, 'SELECT coalesce(o.reading, o.raw) word, o.reading IS NOT NULL parsed, count(*) lines, count(DISTINCT x.artifact_id) tablets '
                                   'FROM x_occ o JOIN x_lines x USING (line_id) WHERE o.position=0 GROUP BY 1, 2 ORDER BY lines DESC, word LIMIT 8')
                rows = _rows(db, 'WITH d AS (SELECT line_id FROM x_occ WHERE position=0 AND reading=?) '
                                 'SELECT coalesce(b.reading, b.raw) word, b.reading IS NOT NULL parsed, b.kind, count(*) lines, count(DISTINCT x.artifact_id) tablets, '
                                 'list(x.line_id ORDER BY x.tr DESC, x.line_id)[1:40] sample_lines '
                                 'FROM d JOIN x_occ b ON b.line_id=d.line_id AND b.position=1 JOIN x_lines x ON x.line_id=d.line_id '
                                 'GROUP BY 1, 2, 3 ORDER BY lines DESC, word LIMIT 20', [opening])
                distinct = db.execute('WITH d AS (SELECT line_id FROM x_occ WHERE position=0 AND reading=?) SELECT count(DISTINCT coalesce(b.reading, b.raw)) '
                                      'FROM d JOIN x_occ b ON b.line_id=d.line_id AND b.position=1', [opening]).fetchone()[0]
                rec_of = dict(db.execute('SELECT line_id, source_record_id FROM x_lines WHERE line_id IN (SELECT unnest(?::VARCHAR[]))',
                                         [[i for r in rows for i in r['sample_lines']]]).fetchall())
            finally:
                for t in ('x_occ', 'x_lines', 'x_art', 'x_tr'):
                    db.execute(f'DROP TABLE IF EXISTS temp.{t}')
            # One example per row, preferring a line that an English source translation covers.
            texts_cache: dict[str, dict] = {}
            tr_set = set(tr_records)
            for r in rows:
                ex = None
                cand = r.pop('sample_lines')
                for lid in sorted(cand, key=lambda i: rec_of.get(i) not in tr_set)[:12]:
                    rid = rec_of.get(lid)
                    if rid in tr_set:
                        if rid not in texts_cache and len(texts_cache) < 30:
                            texts_cache[rid] = self.load_text(db, rid)
                        t = texts_cache.get(rid)
                        if not t:
                            continue
                        order = [ln['id'] for ln in t['lines']]
                        if lid not in order:
                            continue
                        k = order.index(lid)
                        cover = next((p for p in t['placed'] if p['language'] == 'en' and p['first_line'] and
                                      order.index(p['first_line']) <= k <= order.index(p['last_line'])), None)
                        if cover:
                            ex = {'line_id': lid, 'translation': {x: cover[x] for x in ('text', 'status', 'extent', 'lines')}}
                            break
                if ex is None and cand:
                    ex = {'line_id': cand[0], 'translation': None}
                r['example'] = ex
            toks = self.q._line_tokens(db, [r['example']['line_id'] for r in rows if r['example']])
            info = _rows(db, 'SELECT l.id, l.label, a.catalog_key FROM line l JOIN sequence_version s ON s.id=l.sequence_id JOIN witness w ON w.id=s.witness_id '
                             'JOIN artifact a ON a.id=w.artifact_id WHERE l.id IN (SELECT unnest(?::VARCHAR[]))',
                         [[r['example']['line_id'] for r in rows if r['example']]])
            info = {i['id']: i for i in info}
            for r in rows:
                if r['example']:
                    lid = r['example']['line_id']
                    r['example'].update({'tokens': RA.annotate(toks.get(lid, []), 'atf'), 'line_label': info.get(lid, {}).get('label'),
                                         'tablet': info.get(lid, {}).get('catalog_key')})
            return {'opening': opening, 'lines': total_lines, 'tablets': tablets, 'opened': {'lines': opened[0], 'tablets': opened[1]},
                    'first_words': firsts, 'next': rows, 'distinct_next': distinct}
        return self.b.run(ds, work)

    # -- reading explanation ----------------------------------------------------------
    def reading(self, key, version, text, field, record_hint=None) -> dict:
        ds = self.x._ds(key, version)
        field = field or 'atf'
        if field not in ('atf', 'signs'):
            raise UserError('field must be atf or signs')
        if not isinstance(text, str) or not TOKEN_RE.match(text):
            raise UserError('reading: 1–64 visible characters')
        bare = RA.FLAGS.sub('', text)
        out = {'text': text, 'bare': bare, 'field': field, 'kinds': RA.classify(text, field)}
        if field == 'signs':
            e = RA.SIGN_MAP['abz'].get(text)
            out['abz'] = {'status': e['status'] if e else 'not-in-map',
                          'signs': [RA.sign_card(s['name']) if s['name'] in RA.SIGN_MAP['signs'] else {'name': s['name']} for s in (e['signs'] if e else [])]}
            out['parts'] = []
        else:
            out['parts'] = []
            for p in RA.glyph_parts(text):
                d = {'part': p['part'], 'determinative': p['determinative'], 'status': p['status']}
                if p.get('sign'):
                    d['sign'] = RA.sign_card(p['sign'])
                    if not p.get('glyph'):
                        d['sign']['glyph'] = None
                    d['assignment_note'] = {
                        'value-agrees': 'Reading → sign assignment agrees with the retained OSL value list; this is not independent verification of the tablet reading.',
                        'eBL-only': 'Reading → sign assignment from eBL only; not found in the retained OSL value list.',
                        'ambiguous-index': 'Unspecified reading index (ₓ): no single glyph is assigned.',
                        'disagrees-or-ambiguous': 'eBL assigns this reading to this sign, but OSL lists a different or additional glyph. No glyph is drawn.'
                    }.get(p['status'], 'No unambiguous glyph assignment.')
                if p.get('candidates'):
                    d['candidates'] = [RA.sign_card(n) for n in p['candidates']]
                out['parts'].append(d)
        notes = RA.NOTES
        out['note'] = notes['readings'].get(bare)
        out['conventions'] = [dict(notes['conventions'][k], kind=k) for k in out['kinds'] if k in notes['conventions']]
        out['sources'] = notes['meta']['sources']
        out['pronunciation'] = notes['pronunciation']
        out['lemmas'] = self._oracc(bare) if field == 'atf' else None
        scope = self.x._scope(ds, field, None)
        arts = self.genres.get(self.b, ds) if scope is not None else {}  # outside the run below: runs never nest

        def work(db):
            res = {'positions': 0, 'tablets': 0, 'by_genre': [], 'translated_examples': []}
            if scope is None:
                return res
            args = [scope['namespace'], scope['snapshot_id'], field, 'source', bare]
            n = db.execute('SELECT count(*), count(DISTINCT t.artifact_id) FROM posting p JOIN token_context t ON t.id=p.occurrence_id '
                           'WHERE p.namespace=? AND p.snapshot_id=? AND p.field=? AND p.policy=? AND p.label=?', args).fetchone()
            as_raw = False
            if not n[0]:
                n = db.execute('SELECT count(*), count(DISTINCT t.artifact_id) FROM token_context t JOIN source_binding b ON b.source_record_id=t.source_record_id '
                               'WHERE b.snapshot_id=? AND t.field=? AND t.reading IS NULL AND t.raw=?', [scope['snapshot_id'], field, text]).fetchone()
                as_raw = True
            res['positions'], res['tablets'], res['matched_as'] = n[0], n[1], 'unparsed source text' if as_raw else 'parsed reading'
            if not n[0] or field != 'atf':
                return res
            tr_recs = [a['record'] for a in arts.values() if a['tr'].get('en')]
            if as_raw:
                cand = _rows(db, 'SELECT t.line_id, t.source_record_id, t.id FROM token_context t WHERE t.field=? AND t.reading IS NULL AND t.raw=? '
                                 'AND t.source_record_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY t.source_record_id, t.line_id LIMIT 400', [field, text, tr_recs])
            else:
                cand = _rows(db, 'SELECT t.line_id, t.source_record_id, t.id FROM posting p JOIN token_context t ON t.id=p.occurrence_id '
                                 'WHERE p.namespace=? AND p.snapshot_id=? AND p.field=? AND p.policy=? AND p.label=? '
                                 'AND t.source_record_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY t.source_record_id, t.line_id LIMIT 400', args + [tr_recs])
            res['in_translated_texts'] = len(cand)
            seen, examples = set(), []
            if record_hint and isinstance(record_hint, str) and NODE_RE.match(record_hint) and record_hint in tr_recs:
                # the open tablet first: its own translated lines with this reading, then other texts
                own = [c for c in _rows(db, 'SELECT t.line_id, t.source_record_id, t.id FROM token_context t WHERE t.source_record_id=? AND t.field=? '
                                            'AND (t.reading=? OR (t.reading IS NULL AND t.raw=?)) ORDER BY t.line_id LIMIT 50', [record_hint, field, bare, text])]
                cand = own + [c for c in cand if c['source_record_id'] != record_hint]
            loaded: dict[str, dict] = {}
            tries: dict[str, int] = {}
            for c in cand:
                rid = c['source_record_id']
                if len(examples) >= 3 or len(loaded) >= 12:
                    break
                if rid in seen or tries.get(rid, 0) >= 8:  # one example per text; at most 8 lines tried per text
                    continue
                tries[rid] = tries.get(rid, 0) + 1
                if rid not in loaded:
                    loaded[rid] = self.load_text(db, rid)
                t = loaded[rid]
                order = [ln['id'] for ln in t['lines']]
                if c['line_id'] not in order:
                    continue
                k = order.index(c['line_id'])
                cover = next((p for p in t['placed'] if p['language'] == 'en' and p['first_line'] and
                              order.index(p['first_line']) <= k <= order.index(p['last_line'])), None)
                if cover:
                    seen.add(rid)
                    examples.append({'line_id': c['line_id'], 'occurrence_id': c['id'], 'record': c['source_record_id'],
                                     'translation': {x: cover[x] for x in ('text', 'status', 'extent', 'lines')}})
            toks = self.q._line_tokens(db, [e['line_id'] for e in examples])
            info = {i['id']: i for i in _rows(db, 'SELECT l.id, l.label, a.catalog_key FROM line l JOIN sequence_version s ON s.id=l.sequence_id '
                                                  'JOIN witness w ON w.id=s.witness_id JOIN artifact a ON a.id=w.artifact_id '
                                                  'WHERE l.id IN (SELECT unnest(?::VARCHAR[]))', [[e['line_id'] for e in examples]])}
            for e in examples:
                e.update({'tokens': toks.get(e['line_id'], []), 'line_label': info[e['line_id']]['label'], 'tablet': info[e['line_id']]['catalog_key']})
            res['translated_examples'] = examples
            gen: dict[str, int] = {}
            for (aid,) in db.execute('SELECT DISTINCT t.artifact_id FROM posting p JOIN token_context t ON t.id=p.occurrence_id '
                                     'WHERE p.namespace=? AND p.snapshot_id=? AND p.field=? AND p.policy=? AND p.label=?' if not as_raw else
                                     'SELECT DISTINCT t.artifact_id FROM token_context t JOIN source_binding b ON b.source_record_id=t.source_record_id '
                                     'WHERE b.snapshot_id=? AND t.field=? AND t.reading IS NULL AND t.raw=?',
                                     args if not as_raw else [scope['snapshot_id'], field, text]).fetchall():
                gs = {_short(' > '.join(g.split(' > ')[:3])) for g in arts.get(aid, {}).get('genres', [])} or {'No genre recorded'}
                for g in gs:
                    gen[g] = gen.get(g, 0) + 1
            res['by_genre'] = sorted(({'genre': g, 'tablets': v} for g, v in gen.items()), key=lambda r: (r['genre'] == 'No genre recorded', -r['tablets']))[:8]
            return res
        out['corpus'] = self.b.run(ds, work)
        return _jsonable(self.x._bind(ds, out))

    def _oracc(self, form: str) -> dict:
        """Exact word-form lookup in the served ORACC ccpo (oracc-cdl-2) snapshot, if one is ready."""
        cand = [d for d in self.b.datasets.values() if d.kind == 'snapshot' and d.status == 'ready'
                and ((d.selection or {}).get('oracc') or {}).get('adapter') == 'oracc-cdl-2']
        if not cand:
            return {'available': False, 'note': 'No ORACC lemmatized snapshot is being served right now.'}
        ods = cand[0]

        def work(db):
            rows = _rows(db, "SELECT l.citation_form cf, l.pos, l.language, s.guideword gw, s.gloss, count(*) n "
                             "FROM word_token w JOIN edge e ON e.source_id=w.id AND e.kind='IN_SENSE' JOIN sense s ON s.id=e.target_id "
                             "JOIN lemma l ON l.id=s.lemma_id WHERE w.form=? GROUP BY ALL ORDER BY n DESC, cf LIMIT 24", [form])
            words = db.execute('SELECT count(*) FROM word_token WHERE form=?', [form]).fetchone()[0]
            texts = db.execute('SELECT count(DISTINCT source_record_id) FROM sequence_version').fetchone()[0]
            return rows, words, texts
        rows, words, texts = self.b.run(ods, work)
        lemmas: dict[tuple, dict] = {}
        for r in rows:
            k = (r['cf'], r['pos'], r['gw'])
            d = lemmas.setdefault(k, {'citation_form': r['cf'], 'pos': r['pos'], 'language': r['language'], 'guideword': r['gw'], 'n': 0, 'senses': []})
            d['n'] += r['n']
            d['senses'].append({'gloss': r['gloss'], 'n': r['n']})
        return {'available': True, 'dataset': ods.key, 'version': ods.version, 'project': 'ORACC ccpo (Cuneiform Commentaries Project)',
                'texts': texts, 'form': form, 'words_with_form': words,
                'lemmas': sorted(lemmas.values(), key=lambda d: -d['n']),
                'note': 'How ORACC ccpo editors lemmatized this exact written form. Guidewords and senses are dictionary glosses for one word, not translations of a line.'}
