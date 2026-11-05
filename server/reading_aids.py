"""Reading aids: standardized glyphs, reading explanations, genre index and explorations' shared helpers.

Sources (see server/refdata/*.json for receipts):
- sign-map.json: eBL sign list (ABZ number -> sign -> Unicode; sign values), cross-checked against ORACC OSL (CC0).
  A glyph is a standardized Unicode character drawn by a font, never this tablet's handwriting.
- reading-notes.json: a small set of cited notes (one context-dependent sign, transliteration conventions,
  pronunciation caveats, composition recordings). No universal English meaning is assigned to any sign.
- ORACC ccpo lemmatization (the served oracc-cdl-2 snapshot): word form -> lemma -> sense, counted. Senses are
  dictionary glosses for one word, not translations.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

REF = Path(__file__).resolve().parent / 'refdata'
FLAGS = re.compile(r'[\[\]#?!⸢⸣<>()*]')
SPLIT = re.compile(r'[-.+{}]+')
GLYPH_OK = {'verified', 'eBL-only', 'same-glyph'}


def _load(name: str) -> dict:
    return json.loads((REF / name).read_text(encoding='utf-8'))


SIGN_MAP = _load('sign-map.json')
NOTES = _load('reading-notes.json')
VALUE_CHECKS = _load('value-checks.json')['checks']


def value_key(part: str) -> str | None:
    """ATF part -> sign-list value key (capitals mark a logogram but name the same value: KUR -> kur)."""
    if not part or re.fullmatch(r'\d+', part) or part in ('x', 'X', '...', '…'):
        return None
    return part.lower() if part.upper() == part and part.lower() != part else part


def split_token(text: str) -> list[dict]:
    """A transliteration token -> its written parts in order, with determinatives marked. Language shifts,
    separators and whole-token omissions write no sign."""
    if not text or text.startswith(('%', '&', '($')) or re.fullmatch(r'<[^<>]*>[#?!]*', text):
        return []
    t = FLAGS.sub('', text)
    out, i, det = [], 0, False
    for m in re.finditer(r'[-.+{}]|[^-.+{}]+', t):
        s = m.group(0)
        if s == '{':
            det = True
        elif s == '}':
            det = False
        elif s not in '-.+':
            out.append({'part': s, 'determinative': det})
        i += 1
    return out


def glyph_parts(text: str) -> list[dict]:
    parts = []
    for p in split_token(text):
        k = value_key(p['part'])
        names = SIGN_MAP['values'].get(k, []) if k else []
        if len(names) == 1:
            s = SIGN_MAP['signs'][names[0]]
            check = VALUE_CHECKS.get(k, 'not-in-osl')
            ok = bool(s['unicode']) and s['osl'] != 'differs' and check not in ('ambiguous-index', 'disagrees-or-ambiguous')
            parts.append(dict(p, sign=names[0], glyph=''.join(map(chr, s['unicode'])) if ok else None,
                              status=('value-agrees' if check == 'agrees' else 'eBL-only') if ok else check, abz=s['abz']))
        elif len(names) > 1:
            parts.append(dict(p, sign=None, glyph=None, status='ambiguous', candidates=names))
        else:
            parts.append(dict(p, sign=None, glyph=None, status='number' if p['part'].isdigit() else 'not-in-sign-list'))
    return parts


def token_glyph(raw: str, reading: str | None, field: str) -> dict | None:
    """Compact glyph for display next to a token: {'g': glyphs, 'full': all parts mapped} or None."""
    if field == 'signs':
        e = SIGN_MAP['abz'].get(raw)
        if not e:
            return None
        if e['status'] in GLYPH_OK:
            return {'g': ''.join(map(chr, e['signs'][0]['unicode'])), 'full': True}
        return {'g': None, 'full': False, 'ambiguous': len(e['signs'])}
    parts = glyph_parts(reading if reading else raw)
    if not parts:
        return None
    g = ''.join(p['glyph'] or '' for p in parts)
    return {'g': g or None, 'full': all(p['glyph'] for p in parts)}


def annotate(tokens: list[dict], field: str) -> list[dict]:
    for t in tokens:
        t['glyph'] = token_glyph(t.get('raw') or '', t.get('reading'), field)
    return tokens


def classify(text: str, field: str) -> list[str]:
    if field == 'signs':
        return ['sign-number']
    t = FLAGS.sub('', text)
    kinds = []
    if t in ('x', 'X'):
        return ['unknown']
    if re.fullmatch(r'\d+', t):
        return ['number']
    if '{' in t:
        kinds.append('determinative')
    letters = re.sub(r'[^A-Za-zÀ-žḀ-ỿ]', '', re.sub(r'\{[^}]*\}', '', t))
    if letters and letters.upper() == letters:
        kinds.append('logogram')
    elif letters:
        kinds.append('syllabic')
    if re.search('[₀-₉ₓ]', t):
        kinds.append('subindex')
    if re.search(r'[\[\]#?!]|(?:^|[-.])x(?:$|[-.])', text):
        kinds.append('flags')
    return kinds


def sign_card(name: str) -> dict:
    s = SIGN_MAP['signs'][name]
    ok = bool(s['unicode']) and s['osl'] != 'differs'
    from urllib.parse import quote
    return {'name': name, 'glyph': ''.join(map(chr, s['unicode'])) if ok else None,
            'codepoints': ['U+%04X' % c for c in s['unicode']], 'abz': s['abz'], 'values_count': len(s['values']),
            'values_sample': s['values'][:12], 'check': s['osl'], 'link': 'https://www.ebl.lmu.de/signs/' + quote(name, safe='')}


def recordings_for(genres: list[str]) -> list[dict]:
    by = NOTES['recordings']['by_genre']
    out = []
    for g in genres:
        for k, recs in by.items():
            if g == k or g.startswith(k + ' > '):
                out += [dict(r, composition=k.split(' > ')[-1]) for r in recs]
    return out


class GenreIndex:
    """artifact -> genre paths and translation counts, per dataset version (built once inside a bounded run)."""

    def __init__(self):
        self._cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def get(self, b, ds) -> dict:
        key = (ds.key, ds.version)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        def work(db):
            arts = {}
            for aid, rid, cat, meta in db.execute('SELECT a.id, a.source_record_id, a.catalog_key, a.metadata FROM artifact a '
                                                  'JOIN source_binding b ON b.source_record_id=a.source_record_id').fetchall():
                try:
                    m = json.loads(meta or '{}')
                except ValueError:
                    m = {}
                genres = []
                for g in m.get('genres') or []:
                    if isinstance(g, dict) and isinstance(g.get('category'), list):
                        genres.append(' > '.join(str(c) for c in g['category']))
                script = m.get('script') if isinstance(m.get('script'), dict) else {}
                period = script.get('period') if script.get('period') not in (None, 'None') else None
                arts[aid] = {'record': rid, 'catalog': cat, 'genres': genres, 'period': period,
                             'description': (m.get('description') or '')[:240] or None, 'tr': {}}
            by_rec = {a['record']: aid for aid, a in arts.items()}
            for rid, lang, n in db.execute('SELECT source_record_id, language, count(*) FROM translation_segment GROUP BY 1, 2').fetchall():
                aid = by_rec.get(rid)
                if aid:
                    arts[aid]['tr'][lang] = n
            lines = dict(db.execute("SELECT w.artifact_id, count(*) FROM line l JOIN sequence_version s ON s.id=l.sequence_id "
                                    "JOIN witness w ON w.id=s.witness_id WHERE s.field='atf' GROUP BY 1").fetchall())
            for aid, a in arts.items():
                a['lines'] = lines.get(aid, 0)
            return arts
        arts = b.run(ds, work)
        with self._lock:
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = arts
        return arts
