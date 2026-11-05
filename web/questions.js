/* Question-led exploration. The registry (fields, views, wording) comes from the server; every answer is
   computed server-side over all matches in the bound dataset version. DOM is built with textContent only. */
(function () {
  'use strict';
  const CW = window.CW;
  const { el, clear, fmt, api, q, bound, state } = CW;
  const $ = s => document.querySelector(s);
  const QS = { registry: null, regFor: null, current: 'sequence', inputs: {}, results: {}, drill: {}, view: {}, lastDs: null,
    notice: null, seq: 0, insp: null, sel: null, inspReturn: null };

  // ---------- small helpers ----------
  const share = (n, of) => { const v = 100 * n / of; return v < 1 ? '<1%' : (of >= 1000 && v < 10 ? v.toFixed(1) : Math.round(v)) + '%'; };
  const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : (many || one + 's')}`;
  const qdef = id => QS.registry && QS.registry.questions.find(x => x.id === id);
  const SUB = { 0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄', 5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉' };
  // ASCII ATF conveniences: sz → š, s, → ṣ, t, → ṭ, trailing digits after letters → subscript (du3 → du₃).
  function normToken(t, field) {
    if (field !== 'atf' || t === '?' || /^ABZ/i.test(t)) return t;
    let s = t.replace(/sz/g, 'š').replace(/SZ|Sz/g, 'Š').replace(/s,/g, 'ṣ').replace(/S,/g, 'Ṣ').replace(/t,/g, 'ṭ').replace(/T,/g, 'Ṭ');
    s = s.replace(/^(.*[^\d.\s])(\d+)$/u, (_m, a, d) => a + [...d].map(c => SUB[c]).join(''));
    return s;
  }
  const normTokens = (str, field) => String(str || '').trim().split(/\s+/).filter(Boolean).map(t => normToken(t, field));
  function slotText(s) { return s.any ? '?' : (s.reading !== undefined ? s.reading : s.raw); }
  function seqPhrase(slots) {
    return el('span', { class: 'seq' }, slots.map((s, i) => [i ? ' ' : null,
      el('span', { class: 'r' + (s.raw !== undefined ? ' unparsed' : '') + (s.any ? ' any' : ''), text: slotText(s) })]));
  }
  function partEl(p) {
    if (p.parsed) return el('span', { class: 'r', text: p.text });
    return el('span', { class: 'r unparsed', title: p.kind === 'gap' ? 'Broken, lost or unreadable in the source' : 'Unparsed source text (kept whole by the conservative parser)' },
      p.text, el('small', { class: 'tag', text: p.kind === 'gap' ? 'broken' : 'unparsed' }));
  }
  function tokenSpan(t, mark) {
    const c = ['t'];
    if (t.reading === null || t.reading === undefined) c.push(t.kind === 'gap' ? 'gap' : 'unparsed');
    if (t.preservation === 'damaged') c.push('damaged');
    if (t.preservation === 'lost') c.push('lost');
    if (t.certainty === 'uncertain') c.push('uncertain');
    if (mark) c.push('hit');
    return el(mark ? 'mark' : 'span', { class: c.join(' '), title: CW.tokenTitle(t) }, t.raw);
  }
  function lineLabel(l) {
    if (!l) return '';
    return [l.surface, l.column ? 'col. ' + l.column : null, l.label].filter(Boolean).join(' ');
  }
  function bar(n, max) {
    const pct = max ? Math.max(1, Math.round(100 * n / max)) : 0;
    return el('span', { class: 'bar', 'aria-hidden': 'true' }, el('span', { style: null, class: 'bar-fill', 'data-w': String(pct) }));
  }
  // CSP forbids inline styles from strings; widths are set through the CSSOM.
  function applyBars(root) { root.querySelectorAll('.bar-fill[data-w]').forEach(b => { b.style.width = b.dataset.w + '%'; }); }
  function friendlyError(msg) {
    const m = String(msg || '');
    if (/memory bound/.test(m)) return 'This is too common to summarise within the interactive limits. Try a longer or more specific sequence.';
    if (/exceeded \d+s bound/.test(m)) return 'This took longer than the 10-second limit and was stopped. Try a more specific sequence.';
    return m.replace(/^backend refused: /, '');
  }
  function scopeLine() {
    const d = CW.current();
    if (!d) return null;
    const s = d.selection || {};
    if (s.kind === 'cohort' && !/^FULL/.test(s.label || '')) return `Sample of ${fmt(s.records_in_snapshot)} texts chosen by rule — counts are not corpus frequencies.`;
    if (d.kind !== 'snapshot') return d.kind === 'sample' ? 'Small sample file — counts are illustrations only.' : 'Synthetic test data — not evidence.';
    return null;
  }

  // ---------- registry ----------
  async function loadRegistry() {
    const key = state.version ? state.ds + '@' + state.version : '';
    if (QS.registry && QS.regFor === key) return;
    const r = await api('/api/questions' + (state.version ? q(bound()) : ''));
    QS.registry = r; QS.regFor = key;
  }

  // ---------- rendering: shell ----------
  function renderNav() {
    const ol = clear($('#q-list'));
    for (const d of QS.registry.questions) {
      ol.appendChild(el('li', {}, el('button', { type: 'button', class: 'q-item', 'aria-current': d.id === QS.current ? 'true' : null,
        onclick: () => select(d.id, true) }, d.title)));
    }
  }
  function select(id, focus) {
    if (!qdef(id)) return;
    readForm();
    QS.current = id;
    closeInspector();
    render();
    CW.setHash();
    if (focus) $('#q-title').focus();
  }
  function render() {
    if (!QS.registry) return;
    const d = qdef(QS.current);
    renderNav();
    $('#q-title').textContent = d.title;
    $('#q-title').tabIndex = -1;
    $('#q-prompt').textContent = d.prompt;
    renderForm(d);
    renderExamples(d);
    renderResult();
  }
  function renderForm(d) {
    const form = clear($('#q-form'));
    const vals = QS.inputs[d.id] || {};
    const main = el('div', { class: 'qf-main' }), adv = el('div', { class: 'qf-adv' });
    for (const f of d.fields) {
      const id = `qf-${d.id}-${f.name}`;
      const v = vals[f.name] !== undefined ? vals[f.name] : f.default;
      let input;
      if (f.kind === 'select') {
        input = el('select', { id, name: f.name }, f.options.map(o => el('option', { value: o.value, selected: String(v) === String(o.value) ? true : null, text: o.label })));
        if (f.name === 'field') input.addEventListener('change', () => { readForm(); });
      } else {
        input = el('input', { id, name: f.name, type: 'text', value: v || '', placeholder: f.placeholder || '', spellcheck: 'false', autocapitalize: 'off',
          maxlength: f.kind === 'catalog' ? 64 : 340, 'aria-describedby': f.help ? id + '-help' : null, 'aria-autocomplete': 'list', 'aria-expanded': 'false' });
      }
      const wrap = el('div', { class: 'qf qf-' + f.kind + (f.kind === 'tokens' && f.max === 1 ? ' one' : '') },
        el('label', { for: id, text: f.label }), el('div', { class: 'qf-input' }, input),
        f.help ? el('div', { class: 'hint', id: id + '-help', text: f.help }) : null);
      (f.advanced ? adv : main).appendChild(wrap);
      if (f.kind === 'tokens' || f.kind === 'catalog') attachSuggest(input, f.kind, () => (form.querySelector('[name=field]') || {}).value || 'atf');
    }
    main.appendChild(el('div', { class: 'qf qf-submit' }, el('button', { type: 'submit', class: 'primary' }, 'Ask')));
    form.appendChild(main);
    if (adv.childNodes.length) form.appendChild(el('details', { class: 'qf-options', open: (vals.field && vals.field !== 'atf') ? true : null },
      el('summary', { text: 'Options' }), adv));
    form.onsubmit = ev => { ev.preventDefault(); readForm(); run(d.id, {}); };
  }
  function readForm() {
    const d = qdef(QS.current); if (!d) return;
    const form = $('#q-form'); const vals = Object.assign({}, QS.inputs[d.id] || {});
    for (const f of d.fields) { const n = form.querySelector(`[name="${f.name}"]`); if (n) vals[f.name] = n.value; }
    QS.inputs[d.id] = vals;
  }
  function exampleLabel(qid, p) {
    if (qid === 'between') return `${p.before} ${'_ '.repeat(Number(p.gap || 1))}${p.after}`;
    return p.tokens || p.token || p.catalog;
  }
  function renderExamples(d) {
    const box = clear($('#q-examples'));
    const ex = (QS.registry.examples || {})[d.id] || [];
    if (!ex.length) return;
    box.appendChild(el('span', { class: 'hint', text: 'Try' }));
    for (const e of ex) {
      box.appendChild(el('button', { type: 'button', class: 'chip-btn', title: e.unit ? `${fmt(e.n)} ${e.unit}` : `${fmt(e.n)} in this dataset`,
        onclick: () => { QS.inputs[d.id] = Object.assign({ field: 'atf' }, e.params); renderForm(d); run(d.id, {}); } },
        exampleLabel(d.id, e.params), el('span', { class: 'n', text: fmt(e.n) })));
    }
  }

  // ---------- running ----------
  function paramsFor(qid, extra) {
    const v = Object.assign({}, QS.inputs[qid] || {});
    const field = v.field || 'atf';
    const p = { field };
    if (qid === 'sequence') p.tokens = normTokens(v.tokens, field).join(' ');
    if (qid === 'between') { p.before = normTokens(v.before, field).join(' '); p.after = normTokens(v.after, field).join(' '); p.gap = v.gap || '1'; }
    if (qid === 'usage') p.token = normTokens(v.token, field).join(' ');
    if (qid === 'tablet') p.catalog = String(v.catalog || '').trim();
    return Object.assign(p, extra || {});
  }
  function missingInput(qid, p) {
    if (qid === 'sequence' && !p.tokens && !p.slots) return 'Type a sequence of readings, for example DIŠ ina.';
    if (qid === 'between' && (!p.before || !p.after)) return 'Give a reading before and after the gap.';
    if (qid === 'usage' && !p.token) return 'Type one reading, for example ina.';
    if (qid === 'tablet' && !p.catalog) return 'Type a museum number, for example K.8630.';
    return null;
  }
  async function ask(qid, params) {
    return api('/api/ask', bound({ question: qid, params }));
  }
  async function run(qid, extra, opts) {
    const p = paramsFor(qid, extra);
    const miss = missingInput(qid, p);
    if (miss) { QS.results[qid] = { error: miss, kind: 'input' }; if (qid === QS.current) renderResult(); return; }
    if (!state.version) { QS.results[qid] = { error: 'This dataset is not being served right now, so nothing was counted. Pick another dataset or wait for it to verify again.', kind: 'unavailable' }; renderResult(); return; }
    const seq = ++QS.seq;
    QS.notice = null;
    if (!(opts && opts.keepDrill)) delete QS.drill[qid];
    QS.results[qid] = { loading: true, params: p, prev: QS.results[qid] && QS.results[qid].data };
    if (qid === QS.current) renderResult();
    CW.setHash();
    try {
      const data = await ask(qid, p);
      if (seq !== QS.seq && QS.results[qid] && !QS.results[qid].loading) return;
      QS.results[qid] = { data, params: p };
    } catch (e) {
      QS.results[qid] = { error: friendlyError(e.message), params: p };
    }
    if (qid === QS.current) renderResult();
  }

  // ---------- result shell ----------
  function renderResult() {
    const box = clear($('#q-result'));
    box.removeAttribute('aria-busy');
    const qid = QS.current, res = QS.results[qid];
    if (QS.notice) box.appendChild(el('div', { class: 'state stale', role: 'alert' }, el('b', { text: 'Results cleared. ' }), QS.notice));
    if (!res) {
      if (!QS.notice) box.appendChild(el('p', { class: 'state empty', text: qdef(qid).explain }));
      return;
    }
    if (res.loading) {
      box.setAttribute('aria-busy', 'true');
      box.appendChild(el('p', { class: 'state loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), qid === 'tablet' ? 'Opening the tablet…' : 'Counting every match in the dataset…'));
      return;
    }
    if (res.error) { box.appendChild(el('div', { class: 'state ' + (res.kind === 'input' ? 'empty' : 'error'), role: res.kind === 'input' ? null : 'alert', text: res.error })); return; }
    const r = res.data;
    if (r.unavailable) { box.appendChild(el('div', { class: 'state unavailable', text: r.unavailable })); return; }
    ({ sequence: renderSequence, between: renderBetween, usage: renderUsage, tablet: renderTablet })[qid](r, box);
    const sl = scopeLine();
    box.appendChild(el('footer', { class: 'q-foot' },
      sl ? el('p', { class: 'scope-warn', text: sl }) : null,
      el('details', { class: 'method' }, el('summary', { text: 'How this is counted' }),
        el('ul', {}, el('li', { text: qdef(qid).explain }), (QS.registry.method || []).map(m => el('li', { text: m })),
          r.timing ? el('li', { class: 'hint', text: `Answered in ${fmt(Math.round(r.timing.end_to_end_ms))} ms on the server, including the per-request integrity check of dataset version ${String(r.dataset.version).slice(0, 12)}.` }) : null))));
    applyBars(box);
  }
  function resolutionNotes(list) {
    const notes = [];
    for (const x of list || []) {
      if (x.as === 'unparsed') notes.push(el('li', {}, el('span', { class: 'r unparsed', text: x.token }), ' has no parsed reading here; matched as the exact source text of an unparsed word.'));
      if (x.as === 'missing') notes.push(el('li', {}, el('span', { class: 'r', text: x.token }), ' does not occur as a reading or as unparsed text in this dataset.'));
    }
    return notes.length ? el('ul', { class: 'res-notes' }, notes) : null;
  }
  function segmented(name, options, value, onChange) {
    return el('div', { class: 'seg', role: 'group', 'aria-label': name }, options.map(([v, label]) =>
      el('button', { type: 'button', 'aria-pressed': String(v === value), onclick: () => onChange(v) }, label)));
  }

  // ---------- 1. sequence ----------
  function renderSequence(r, box, ctx) {
    ctx = ctx || { qid: 'sequence', viewKey: 'sequence', rerun: extra => run('sequence', Object.assign(slotParams(r), extra)) };
    const t = r.totals;
    const head = el('div', { class: 'answer' });
    if (!t.matches) {
      head.appendChild(el('h2', { class: 'headline' }, seqPhrase(r.slots), ' does not occur in this dataset.'));
      head.appendChild(resolutionNotes(r.resolved));
      box.appendChild(head);
      return;
    }
    head.appendChild(el('h2', { class: 'headline' }, seqPhrase(r.slots), ` occurs ${plural(t.matches, 'time')} on ${plural(t.tablets, 'tablet')}.`));
    head.appendChild(el('p', { class: 'sub', text: `${plural(t.lines, 'line')} · ${r.field_label}${r.period ? ' · showing ' + r.period + ' only' : ''}` }));
    const rn = resolutionNotes(r.resolved); if (rn) head.appendChild(rn);
    box.appendChild(head);
    const view = QS.view[ctx.viewKey] || 'lines';
    const setView = v => { QS.view[ctx.viewKey] = v; ctx.redraw ? ctx.redraw() : renderResult(); };
    const tools = el('div', { class: 'toolbar' }, segmented('Show', [['lines', 'Lines'], ['tablets', 'By tablet'], ['periods', 'By period']], view, setView));
    if (r.period) tools.appendChild(el('button', { type: 'button', class: 'filter-chip', onclick: () => ctx.rerun({ period: null }), 'aria-label': `Remove period filter ${r.period}` }, r.period, el('span', { 'aria-hidden': 'true', text: ' ×' })));
    box.appendChild(tools);
    if (view === 'lines') box.appendChild(kwicTable(r, ctx));
    if (view === 'tablets') box.appendChild(tabletTable(r));
    if (view === 'periods') box.appendChild(periodTable(r.by_period, row => { QS.view[ctx.viewKey] = 'lines'; ctx.rerun({ period: row.period }); }));
  }
  function slotParams(r) { return { slots: r.slots, field: r.field }; }
  function kwicTable(r, ctx) {
    const pg = r.page;
    const wrap = el('div', { class: 'kwic-wrap' });
    const tb = el('tbody');
    let prevTab = null;
    pg.rows.forEach(row => {
      const repeat = row.artifact_id === prevTab; prevTab = row.artifact_id;
      const ids = new Set(row.occurrence_ids);
      const toks = row.tokens;
      let first = toks.findIndex(x => ids.has(x.id));
      let last = first; toks.forEach((x, j) => { if (ids.has(x.id)) last = j; });
      if (first < 0) { first = 0; last = -1; }
      const L = toks.slice(Math.max(0, first - 7), first), R = toks.slice(last + 1, last + 8);
      const tr = el('tr', { tabindex: '0', class: 'kwic-row', 'aria-label': `${row.tablet} line ${row.line.label}: open passage`,
        onclick: () => openPassage(row, tr), onkeydown: ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPassage(row, tr); } } },
        el('td', { class: 'k-tab' + (repeat ? ' repeat' : '') }, el('span', { class: 'tab-id', text: row.tablet }), el('span', { class: 'per', text: row.period === 'Not recorded' ? '' : row.period })),
        el('td', { class: 'k-line', text: lineLabel(row.line) }),
        el('td', { class: 'k-left' }, first > 7 ? el('span', { class: 'ell', text: '… ' }) : null, L.map(x => [tokenSpan(x), ' '])),
        el('td', { class: 'k-hit' }, toks.slice(first, last + 1).map((x, j) => [j ? ' ' : null, tokenSpan(x, ids.has(x.id))])),
        el('td', { class: 'k-right' }, R.map(x => [' ', tokenSpan(x)]), last + 8 < toks.length ? el('span', { class: 'ell', text: ' …' }) : null));
      tb.appendChild(tr);
    });
    wrap.appendChild(el('table', { class: 'kwic' }, el('caption', { class: 'sr-only', text: 'Matches in their lines' }),
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Tablet' }), el('th', { scope: 'col', text: 'Line' }), el('th', { scope: 'col', class: 'k-left', text: 'Before' }),
        el('th', { scope: 'col', text: 'Match' }), el('th', { scope: 'col', text: 'After' }))), tb));
    const total = pg.filtered_matches !== null && pg.filtered_matches !== undefined ? pg.filtered_matches : r.totals.matches;
    const from = pg.rows.length ? pg.offset + 1 : 0, to = pg.offset + pg.rows.length;
    wrap.appendChild(el('div', { class: 'pager' }, el('span', { text: `${fmt(from)}–${fmt(to)} of ${fmt(total)}, ordered by museum number` }),
      el('span', { class: 'btns' },
        el('button', { type: 'button', disabled: pg.offset > 0 ? null : true, onclick: () => ctx.rerun({ offset: Math.max(0, pg.offset - pg.limit), period: r.period }) }, '← Previous'),
        el('button', { type: 'button', disabled: pg.next_offset === null ? true : null, onclick: () => ctx.rerun({ offset: pg.next_offset, period: r.period }) }, 'Next →'))));
    return wrap;
  }
  function tabletTable(r) {
    const bt = r.by_tablet, max = bt.rows.length ? bt.rows[0].matches : 0;
    return el('div', {}, el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Tablet' }), el('th', { scope: 'col', text: 'Period' }), el('th', { scope: 'col', class: 'n', text: 'Matches' }), el('th', { scope: 'col', class: 'n', text: 'Lines' }), el('th', { scope: 'col' }))),
      el('tbody', {}, bt.rows.map(x => el('tr', {}, el('td', {}, el('button', { type: 'button', class: 'linkish', onclick: () => openTablet(x.tablet) }, x.tablet)),
        el('td', { text: x.period }), el('td', { class: 'n', text: fmt(x.matches) }), el('td', { class: 'n', text: fmt(x.lines) }), el('td', { class: 'barcell' }, bar(x.matches, max)))))),
      el('p', { class: 'hint', text: bt.truncated ? `The ${bt.rows.length} tablets with most matches, of ${fmt(bt.total)}.` : `All ${fmt(bt.total)} tablets.` }));
  }
  function periodTable(rows, onPick) {
    const max = rows.length ? Math.max(...rows.map(x => x.matches)) : 0;
    return el('div', {}, el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Period (as catalogued by the source)' }), el('th', { scope: 'col', class: 'n', text: 'Matches' }), el('th', { scope: 'col', class: 'n', text: 'Tablets' }), el('th', { scope: 'col' }))),
      el('tbody', {}, rows.map(x => el('tr', {}, el('td', {}, onPick ? el('button', { type: 'button', class: 'linkish', onclick: () => onPick(x), title: 'Show only these lines' }, x.period) : x.period),
        el('td', { class: 'n', text: fmt(x.matches) }), el('td', { class: 'n', text: fmt(x.tablets) }), el('td', { class: 'barcell' }, bar(x.matches, max)))))),
      el('p', { class: 'hint', text: 'Periods are the source catalogue’s dating of each tablet, not a dating of the passage.' }));
  }

  // ---------- drill-down panel (between / usage) ----------
  function drillPanel(qid, box) {
    const dr = QS.drill[qid];
    if (!dr) return;
    const panel = el('section', { class: 'drill', 'aria-label': 'Contexts' });
    const redraw = () => { const fresh = el('div'); drillPanel(qid, fresh); panel.replaceWith(fresh.firstChild || el('span')); applyBars(document); };
    panel.appendChild(el('div', { class: 'drill-head' }, el('h3', {}, 'Contexts for ', seqPhrase(dr.slots)),
      el('button', { type: 'button', class: 'ghost', onclick: () => { QS.inputs.sequence = Object.assign({}, QS.inputs.sequence, { tokens: dr.slots.map(slotText).join(' '), field: dr.field });
        QS.current = 'sequence'; render(); run('sequence', { slots: dr.slots }); } }, 'Open in “Where else…”'),
      el('button', { type: 'button', class: 'ghost icon', 'aria-label': 'Close contexts', onclick: () => { delete QS.drill[qid]; renderResult(); } }, '×')));
    if (dr.loading) panel.appendChild(el('p', { class: 'state loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Finding every context…'));
    else if (dr.error) panel.appendChild(el('div', { class: 'state error', text: dr.error }));
    else renderSequence(dr.data, panel, { viewKey: 'drill-' + qid, redraw, rerun: extra => drill(qid, dr.slots, dr.field, extra) });
    box.appendChild(panel);
  }
  async function drill(qid, slots, field, extra) {
    const token = ++QS.seq;
    QS.drill[qid] = { slots, field, loading: true };
    renderResult();
    try {
      const data = await ask('sequence', Object.assign({ slots, field }, extra || {}));
      if (!QS.drill[qid] || token !== QS.seq) return;
      QS.drill[qid] = { slots, field, data };
    } catch (e) {
      if (!QS.drill[qid]) return;
      QS.drill[qid] = { slots, field, error: friendlyError(e.message) };
    }
    if (QS.current === qid) { renderResult(); const d = document.querySelector('.drill'); if (d) d.scrollIntoView({ block: 'nearest' }); }
  }

  // ---------- 2. between ----------
  function renderBetween(r, box) {
    const t = r.totals, rb = r.resolved;
    const before = rb.before.map(x => ({ reading: x.token })), after = rb.after.map(x => ({ reading: x.token }));
    const gapSlots = Array.from({ length: r.gap }, () => ({ any: true }));
    const head = el('div', { class: 'answer' });
    const frame = el('span', { class: 'seq' }, seqPhrase(before), ' ', el('span', { class: 'r gapmark', text: '_ '.repeat(r.gap).trim() }), ' ', seqPhrase(after));
    if (!t.matches) {
      head.appendChild(el('h2', { class: 'headline' }, 'Nothing is attested as ', frame, ' in this dataset.'));
      head.appendChild(resolutionNotes([...rb.before, ...rb.after]));
      box.appendChild(head); return;
    }
    head.appendChild(el('h2', { class: 'headline' }, frame, ` is attested ${plural(t.matches, 'time')} on ${plural(t.tablets, 'tablet')}, with ${plural(r.distinct_fillers, 'different filler')}.`));
    const ub = r.unparsed_or_broken;
    head.appendChild(el('p', { class: 'sub', text: `${r.field_label}${ub.matches ? ` · ${fmt(ub.matches)} of these have a broken or unparsed position in the gap` : ''}` }));
    const rn = resolutionNotes([...rb.before, ...rb.after]); if (rn) head.appendChild(rn);
    box.appendChild(head);
    const max = r.rows.length ? r.rows[0].matches : 0;
    const dr = QS.drill.between;
    const tb = el('tbody');
    const showAll = QS.view['between-all'];
    r.rows.slice(0, showAll ? r.rows.length : 15).forEach(x => {
      const active = dr && JSON.stringify(dr.slots) === JSON.stringify(x.slots);
      const open = () => drill('between', x.slots, r.field);
      tb.appendChild(el('tr', { tabindex: '0', class: 'pick' + (active ? ' active' : ''), 'aria-label': `${x.filler.map(p => p.text).join(' ')}: show ${x.matches} contexts`,
        onclick: open, onkeydown: ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } } },
        el('td', { class: 'filler' }, x.filler.map((p, i) => [i ? ' ' : null, partEl(p)])),
        el('td', { class: 'n', text: fmt(x.matches) }), el('td', { class: 'n', text: fmt(x.tablets) }),
        el('td', { class: 'n share', text: share(x.matches, t.matches) }),
        el('td', { class: 'barcell' }, bar(x.matches, max))));
    });
    box.appendChild(el('table', { class: 'data fillers' }, el('caption', { class: 'sr-only', text: 'Attested fillers' }),
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'In the gap' }), el('th', { scope: 'col', class: 'n', text: 'Times' }), el('th', { scope: 'col', class: 'n', text: 'Tablets' }),
        el('th', { scope: 'col', class: 'n', text: 'Share' }), el('th', { scope: 'col' }))), tb));
    const shownNow = Math.min(r.rows.length, showAll ? r.rows.length : 15);
    box.appendChild(el('p', { class: 'hint' }, `Showing the ${shownNow} most frequent of ${fmt(r.distinct_fillers)} fillers${r.truncated && showAll ? ' (the list stops at ' + r.shown + ')' : ''}. Shares are of all ${fmt(t.matches)} attestations. Select a row to read its contexts. `,
      r.rows.length > 15 ? el('button', { type: 'button', class: 'linkish', onclick: () => { QS.view['between-all'] = !showAll; renderResult(); } }, showAll ? 'Show fewer' : `Show ${r.rows.length - 15} more`) : null));
    drillPanel('between', box);
    void gapSlots;
  }

  // ---------- 3. usage ----------
  function renderUsage(r, box) {
    const t = r.totals;
    const head = el('div', { class: 'answer' });
    if (!t.matches) {
      head.appendChild(el('h2', { class: 'headline' }, seqPhrase(r.slots), ' does not occur in this dataset.'));
      head.appendChild(resolutionNotes(r.resolved)); box.appendChild(head); return;
    }
    head.appendChild(el('h2', { class: 'headline' }, seqPhrase(r.slots), ` occurs ${plural(t.matches, 'time')} on ${plural(t.tablets, 'tablet')}.`));
    head.appendChild(el('p', { class: 'sub', text: `${plural(t.lines, 'line')} · ${r.field_label}` }));
    const rn = resolutionNotes(r.resolved); if (rn) head.appendChild(rn);
    box.appendChild(head);
    const side = (name, s, edgeLabel) => {
      const max = s.rows.length ? s.rows[0].matches : 0;
      const dr = QS.drill.usage;
      return el('div', { class: 'nb' }, el('h3', { text: name }),
        el('table', { class: 'data' }, el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Neighbor' }), el('th', { scope: 'col', class: 'n', text: 'Times' }), el('th', { scope: 'col', class: 'n', text: 'Tablets' }), el('th', { scope: 'col' }))),
          el('tbody', {},
            s.rows.map(x => {
              const active = dr && JSON.stringify(dr.slots) === JSON.stringify(x.slots);
              const open = () => drill('usage', x.slots, r.field);
              return el('tr', { tabindex: '0', class: 'pick' + (active ? ' active' : ''), onclick: open, onkeydown: ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } } },
                el('td', { class: 'filler' }, partEl(x.neighbor)), el('td', { class: 'n', text: fmt(x.matches) }), el('td', { class: 'n', text: fmt(x.tablets) }), el('td', { class: 'barcell' }, bar(x.matches, max)));
            }),
            el('tr', { class: 'edge' }, el('td', { text: edgeLabel }), el('td', { class: 'n', text: fmt(s.line_edge) }), el('td'), el('td')))),
        el('p', { class: 'hint', text: s.truncated ? `Top ${s.rows.length} of ${fmt(s.distinct)} different neighbors.` : `All ${fmt(s.distinct)} neighbors.` }));
    };
    box.appendChild(el('div', { class: 'neighbors' }, side('Directly before', r.before, 'At the start of a line'), side('Directly after', r.after, 'At the end of a line')));
    drillPanel('usage', box);
    if (r.examples && r.examples.rows.length) {
      box.appendChild(el('h3', { class: 'sec', text: 'Examples' }));
      const ctx = { viewKey: 'usage-ex', rerun: () => { QS.inputs.sequence = { tokens: r.slots.map(slotText).join(' '), field: r.field }; QS.current = 'sequence'; render(); run('sequence', { slots: r.slots }); } };
      const k = kwicTable({ page: Object.assign({}, r.examples, { next_offset: null }), totals: t, period: null }, ctx);
      k.querySelector('.pager').replaceWith(el('div', { class: 'pager' }, el('span', { text: `First ${r.examples.rows.length} of ${fmt(t.matches)} by museum number` }),
        el('button', { type: 'button', onclick: ctx.rerun }, 'See all in “Where else…”')));
      box.appendChild(k);
    }
    box.appendChild(el('h3', { class: 'sec', text: 'By period' }));
    box.appendChild(periodTable(r.by_period, null));
  }

  // ---------- 4. tablet ----------
  function openTablet(catalog, field) {
    QS.inputs.tablet = Object.assign({}, QS.inputs.tablet, { catalog, field: field || (QS.inputs.tablet && QS.inputs.tablet.field) || 'atf' });
    closeInspector();
    QS.current = 'tablet'; render(); run('tablet', {});
  }
  function renderTablet(r, box) {
    if (!r.found) {
      const p = QS.results.tablet.params;
      box.appendChild(el('div', { class: 'answer' }, el('h2', { class: 'headline', text: `No tablet ${p.catalog} in this dataset.` }),
        r.suggestions.length ? el('div', { class: 'q-examples' }, el('span', { class: 'hint', text: 'Did you mean' }), r.suggestions.map(s => el('button', { type: 'button', class: 'chip-btn', onclick: () => openTablet(s) }, s))) : null));
      return;
    }
    const tb = r.tablet;
    const meta = [['Period', tb.period ? tb.period + (tb.period_uncertain ? ' (uncertain)' : '') : null], ['Collection', tb.collection], ['Museum', tb.museum],
      ['CDLI', tb.cdli], ['Genre', tb.genres.join('; ') || null], ['Joins', tb.joins ? String(tb.joins) : null]].filter(x => x[1]);
    box.appendChild(el('div', { class: 'answer tablet-head' }, el('h2', { class: 'headline', text: tb.catalog }),
      tb.description ? el('p', { class: 'desc', text: tb.description }) : null,
      meta.length ? el('dl', { class: 'meta' }, meta.map(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })])) : null));
    const field = r.field;
    if (r.layers.length > 1) box.appendChild(el('div', { class: 'toolbar' }, segmented('Layer', r.layers.map(l => [l, l === 'atf' ? 'Transliteration' : 'Sign numbers (ABZ)']), field,
      v => { QS.inputs.tablet = Object.assign({}, QS.inputs.tablet, { field: v }); renderForm(qdef('tablet')); run('tablet', {}); })));
    if (!r.lines.length) { box.appendChild(el('div', { class: 'state unavailable', text: `This tablet has no ${r.field_label.toLowerCase()} layer in this dataset.` })); return; }
    QS.sel = null;
    const trAfter = {};
    const loose = [];
    for (const t of r.translations) { if (t.after_line) (trAfter[t.after_line] = trAfter[t.after_line] || []).push(t); else loose.push(t); }
    const list = el('ol', { class: 'tablet-lines', 'aria-label': 'Lines' });
    let lastSurface = null;
    for (const ln of r.lines) {
      const surf = [ln.surface, ln.column_label ? 'column ' + ln.column_label : null].filter(Boolean).join(', ');
      if (surf && surf !== lastSurface) list.appendChild(el('li', { class: 'surface', text: surf }));
      lastSurface = surf || lastSurface;
      const toks = el('span', { class: 'ln-toks' }, ln.tokens.map((t, i) => [i ? ' ' : null,
        el('button', { type: 'button', class: 'tk ' + tokenSpan(t).className, title: CW.tokenTitle(t), 'data-line': ln.id, 'data-i': String(i), onclick: ev => pick(ln, i, ev) }, t.raw)]));
      list.appendChild(el('li', { class: 'ln' }, el('span', { class: 'ln-no', text: ln.label || '' }), toks));
      for (const t of trAfter[ln.id] || []) list.appendChild(el('li', { class: 'tr' }, el('span', { class: 'ln-no', text: t.language }), el('span', { class: 'tr-text', text: t.text })));
    }
    box.appendChild(el('p', { class: 'hint', text: 'Select a reading, then another in the same line, to ask about that span.' }));
    box.appendChild(list);
    if (r.lines_truncated) box.appendChild(el('p', { class: 'hint', text: `First ${r.lines.length} lines shown.` }));
    if (r.translations.length) box.appendChild(el('p', { class: 'hint', text: r.translation_note }));
    for (const t of loose) box.appendChild(el('p', { class: 'tr-loose', text: `${t.language}: ${t.text}` }));
    box.appendChild(el('div', { id: 'sel-bar', class: 'sel-bar', hidden: true, role: 'region', 'aria-label': 'Selection' }));
  }
  function pick(ln, i, ev) {
    const s = QS.sel;
    if (s && s.line.id === ln.id && s.end === null && i !== s.start && !(ev && ev.altKey)) QS.sel = { line: ln, start: Math.min(s.start, i), end: Math.max(s.start, i) };
    else QS.sel = { line: ln, start: i, end: null };
    drawSelection();
  }
  function drawSelection() {
    const s = QS.sel, barEl = $('#sel-bar');
    document.querySelectorAll('.tablet-lines .tk.sel').forEach(b => b.classList.remove('sel'));
    if (!s) { barEl.hidden = true; return; }
    const end = s.end === null ? s.start : s.end;
    const toks = s.line.tokens.slice(s.start, end + 1);
    document.querySelectorAll(`.tablet-lines .tk[data-line="${s.line.id}"]`).forEach(b => { const i = Number(b.dataset.i); if (i >= s.start && i <= end) b.classList.add('sel'); });
    const text = toks.map(t => t.reading !== null && t.reading !== undefined ? t.reading : t.raw);
    const slots = toks.map(t => (t.reading !== null && t.reading !== undefined ? { reading: t.reading } : { raw: t.raw }));
    const field = QS.results.tablet.data.field;
    clear(barEl); barEl.hidden = false;
    const tooLong = toks.length > 5;
    barEl.appendChild(el('span', { class: 'sel-text' }, 'Selected ', seqPhrase(slots)));
    barEl.appendChild(el('button', { type: 'button', class: 'primary', disabled: tooLong ? true : null, title: tooLong ? 'At most 5 positions' : null,
      onclick: () => { QS.inputs.sequence = { tokens: text.join(' '), field }; QS.current = 'sequence'; render(); run('sequence', { slots }); } }, 'Where else does this occur?'));
    const canBetween = toks.length >= 3 && toks.length <= 4;
    barEl.appendChild(el('button', { type: 'button', disabled: canBetween ? null : true, title: canBetween ? null : 'Select 3 or 4 readings: the first and last frame the gap',
      onclick: () => { QS.inputs.between = { before: text[0], after: text[text.length - 1], gap: String(toks.length - 2), field }; QS.current = 'between'; render(); run('between', {}); } }, 'What occurs between the ends?'));
    barEl.appendChild(el('button', { type: 'button', disabled: toks.length === 1 ? null : true, title: toks.length === 1 ? null : 'Select one reading',
      onclick: () => { QS.inputs.usage = { token: text[0], field }; QS.current = 'usage'; render(); run('usage', {}); } }, 'How is it used?'));
    barEl.appendChild(el('button', { type: 'button', class: 'ghost icon', 'aria-label': 'Clear selection', onclick: () => { QS.sel = null; drawSelection(); } }, '×'));
  }

  // ---------- passage inspector ----------
  async function openPassage(row, fromEl) {
    const aside = $('#inspector'), body = clear($('#insp-body'));
    QS.inspReturn = fromEl || null;
    document.querySelectorAll('.kwic-row.active').forEach(x => x.classList.remove('active'));
    if (fromEl) fromEl.classList.add('active');
    $('#insp-title').textContent = `${row.tablet} · line ${row.line.label}`;
    aside.hidden = false; document.body.classList.add('insp-open');
    $('#insp-title').tabIndex = -1; $('#insp-title').focus();
    body.appendChild(el('p', { class: 'state loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading passage…'));
    const token = ++QS.seq; QS.insp = token;
    try {
      const p = await api('/api/passage' + q(bound({ line: row.line_id, around: 2 })));
      if (QS.insp !== token) return;
      clear(body);
      const ids = new Set(row.occurrence_ids);
      if (p.tablet.period || p.tablet.description) body.appendChild(el('p', { class: 'desc' }, p.tablet.period ? el('b', { text: p.tablet.period + '. ' }) : null, p.tablet.description || ''));
      const ol = el('ol', { class: 'passage' });
      for (const ln of p.lines) {
        ol.appendChild(el('li', { class: ln.id === row.line_id ? 'cur' : '' }, el('span', { class: 'ln-no', text: [ln.surface && ln.id === row.line_id ? ln.surface : null, ln.label].filter(Boolean).join(' ') }),
          el('span', { class: 'ln-toks' }, ln.tokens.map((t, j) => [j ? ' ' : null, tokenSpan(t, ids.has(t.id))]))));
      }
      body.appendChild(ol);
      body.appendChild(el('p', { class: 'hint', text: `Lines ${p.lines[0].label}–${p.lines[p.lines.length - 1].label} of ${fmt(p.lines_in_text)} in this text.` }));
      const ev = el('div', { class: 'evidence-box' });
      body.appendChild(el('div', { class: 'insp-actions' },
        el('button', { type: 'button', class: 'primary', onclick: () => openTablet(row.tablet, p.field) }, 'Read the whole tablet'),
        el('button', { type: 'button', onclick: async e => { e.currentTarget.disabled = true; ev.appendChild(el('p', { class: 'hint', text: 'Re-reading the original source file; this takes a few seconds on the full release…' }));
          await CW.openOccurrence(row.occurrence_ids[0], row.occurrence_ids, ev); } }, 'Check against the source file')));
      body.appendChild(ev);
    } catch (e) {
      if (QS.insp !== token) return;
      clear(body).appendChild(el('div', { class: 'state error', text: friendlyError(e.message) }));
    }
  }
  function closeInspector() {
    const aside = $('#inspector');
    if (aside.hidden) return;
    aside.hidden = true; document.body.classList.remove('insp-open'); QS.insp = null;
    document.querySelectorAll('.kwic-row.active').forEach(x => x.classList.remove('active'));
    if (QS.inspReturn && document.body.contains(QS.inspReturn)) QS.inspReturn.focus();
  }

  // ---------- suggestions ----------
  function attachSuggest(input, kind, getField) {
    const list = el('ul', { class: 'suggest', role: 'listbox', id: input.id + '-list', hidden: true });
    input.setAttribute('aria-controls', list.id);
    input.setAttribute('role', 'combobox');
    input.parentNode ? input.parentNode.appendChild(list) : setTimeout(() => input.parentNode.appendChild(list));
    let timer = null, n = 0, items = [], active = -1;
    const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
    const choose = i => {
      const it = items[i]; if (!it) return;
      if (kind === 'catalog') input.value = it.value;
      else { const parts = input.value.split(/\s+/); parts[parts.length - 1] = it.value; input.value = parts.join(' ') + ' '; }
      close(); input.focus();
    };
    const draw = () => {
      clear(list);
      items.forEach((it, i) => list.appendChild(el('li', { id: `${list.id}-${i}`, role: 'option', 'aria-selected': String(i === active), class: i === active ? 'on' : '',
        onmousedown: ev => { ev.preventDefault(); choose(i); } }, el('span', { class: 'r' + (it.unparsed ? ' unparsed' : ''), text: it.value }),
        it.positions !== undefined ? el('span', { class: 'n', text: fmt(it.positions) }) : null, it.unparsed ? el('small', { class: 'tag', text: 'unparsed' }) : null)));
      list.hidden = !items.length; input.setAttribute('aria-expanded', String(!!items.length));
      if (active >= 0) input.setAttribute('aria-activedescendant', `${list.id}-${active}`);
    };
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const field = getField();
      const last = kind === 'catalog' ? input.value.trim() : normToken((input.value.split(/\s+/).pop() || ''), field);
      if (!state.version || !last || last === '?' || (kind === 'catalog' && last.length < 2)) { items = []; close(); return; }
      timer = setTimeout(async () => {
        const me = ++n;
        try {
          if (kind === 'catalog') {
            const r = await api('/api/tablets' + q(bound({ prefix: last })));
            if (me !== n) return; items = r.tablets.map(v => ({ value: v }));
          } else {
            const r = await api('/api/suggest' + q(bound({ field, prefix: last })));
            if (me !== n) return;
            items = r.readings.map(x => ({ value: x.value, positions: x.positions })).concat(r.unparsed.map(x => ({ value: x.value, positions: x.positions, unparsed: true })));
          }
          active = -1; draw();
        } catch (_) { items = []; close(); }
      }, 180);
    });
    input.addEventListener('keydown', ev => {
      if (list.hidden) return;
      if (ev.key === 'ArrowDown') { ev.preventDefault(); active = (active + 1) % items.length; draw(); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); active = (active - 1 + items.length) % items.length; draw(); }
      else if (ev.key === 'Enter' && active >= 0) { ev.preventDefault(); choose(active); }
      else if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    });
    input.addEventListener('blur', () => setTimeout(close, 120));
  }

  // ---------- integration with app.js ----------
  const HASH_KEYS = { sequence: ['tokens', 'field'], between: ['before', 'gap', 'after', 'field'], usage: ['token', 'field'], tablet: ['catalog', 'field'] };
  CW.hashParams = () => {
    const v = QS.inputs[QS.current] || {}, out = { q: QS.current };
    for (const k of HASH_KEYS[QS.current] || []) if (v[k] && !(k === 'field' && v[k] === 'atf')) out[k] = v[k];
    return out;
  };
  CW.onReset = () => {
    const userSwitched = QS.lastDs !== null && QS.lastDs !== state.ds;
    const had = Object.keys(QS.results).length > 0;
    QS.results = {}; QS.drill = {}; QS.regFor = null;
    closeInspector();
    const stale = state.stale && state.stale.key === state.ds && !state.version;
    QS.notice = stale ? `The dataset you chose is not being served right now (${String(state.stale.reason).replace(/\.\s*$/, '')}). Nothing else was selected; it returns automatically once it verifies again.`
      : (had && !userSwitched ? 'The dataset was re-verified as a new version, so earlier answers no longer apply. Ask again.' : null);
    const rerun = had && userSwitched && state.version;
    QS.lastDs = state.ds;
    if (state.view === 'questions') boot().then(() => { if (rerun) run(QS.current, {}); });
  };
  CW.onShowQuestions = () => { boot(); };
  async function boot() {
    try { await loadRegistry(); } catch (e) { clear($('#q-result')).appendChild(el('div', { class: 'state error', text: friendlyError(e.message) })); return; }
    render();
  }
  function fromHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const qid = h.get('q');
    if (!qid || !HASH_KEYS[qid]) return false;
    QS.current = qid;
    const v = {};
    for (const k of HASH_KEYS[qid]) if (h.get(k)) v[k] = h.get(k);
    QS.inputs[qid] = v;
    return Object.keys(v).some(k => k !== 'field');
  }
  $('#insp-close').addEventListener('click', closeInspector);
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !$('#inspector').hidden && !ev.target.closest('.suggest, [role=combobox]')) closeInspector(); });
  const auto = fromHash();
  // app.js's first dataset selection calls onReset (and so boot); run the linked question once it is ready.
  const first = CW.onReset;
  CW.onReset = () => {
    first();
    if (auto && !QS._autoDone && state.version) { QS._autoDone = true; boot().then(() => run(QS.current, {})); }
  };
})();
