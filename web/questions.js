/* Question-led exploration. The registry (fields, views, wording) comes from the server; every answer is
   computed server-side over all matches in the bound dataset version. DOM is built with textContent only. */
(function () {
  'use strict';
  const CW = window.CW;
  const { el, clear, fmt, api, q, bound, state } = CW;
  const $ = s => document.querySelector(s);
  const QS = { registry: null, regFor: null, current: null, inputs: {}, results: {}, drill: {}, view: {}, lastDs: null,
    notice: null, seq: 0, insp: null, sel: null, inspReturn: null, glyphs: 'reading' };
  const isX = id => typeof id === 'string' && id.startsWith('x:');

  // ---------- small helpers ----------
  const share = (n, of) => { const v = 100 * n / of; return v < 1 ? '<1%' : (of >= 1000 && v < 10 ? v.toFixed(1) : Math.round(v)) + '%'; };
  const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : (many || one + 's')}`;
  const qdef = id => QS.registry && QS.registry.questions.find(x => x.id === id);
  const xdef = id => QS.registry && (QS.registry.explorations || []).find(x => 'x:' + x.id === id);
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
    const xl = clear($('#x-list'));
    for (const d of QS.registry.explorations || []) {
      xl.appendChild(el('li', {}, el('button', { type: 'button', class: 'q-item', 'aria-current': 'x:' + d.id === QS.current ? 'true' : null,
        onclick: () => select('x:' + d.id, true) }, d.title, el('span', { class: 'q-count', text: fmt(d.texts) + ' texts' }))));
    }
    $('#x-title').hidden = !(QS.registry.explorations || []).length;
    const ol = clear($('#q-list'));
    for (const d of QS.registry.questions) {
      ol.appendChild(el('li', {}, el('button', { type: 'button', class: 'q-item', 'aria-current': d.id === QS.current ? 'true' : null,
        onclick: () => select(d.id, true) }, d.title)));
    }
    // Narrow screens scroll the nav sideways: keep the current entry visible without moving the page.
    for (const list of [xl, ol]) {
      const cur = list.querySelector('[aria-current=true]');
      if (cur && list.scrollWidth > list.clientWidth) list.scrollLeft = Math.max(0, cur.parentNode.offsetLeft - list.offsetLeft - 8);
    }
  }
  function select(id, focus) {
    if (!qdef(id) && !xdef(id)) return;
    if (!isX(QS.current)) readForm();
    QS.current = id;
    $('.qnav').open = false;
    closeInspector();
    render();
    CW.setHash();
    if (focus) $('#q-title').focus();
  }
  function render() {
    if (!QS.registry) return;
    if (!QS.current || (!qdef(QS.current) && !xdef(QS.current))) QS.current = (QS.registry.explorations || []).length ? 'x:' + QS.registry.explorations[0].id : 'sequence';
    if (isX(QS.current)) { renderNav(); return renderExploration(xdef(QS.current)); }
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
    const d = qdef(QS.current); if (!d || isX(QS.current)) return;
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
    const ex = ((QS.registry.examples || {})[d.id] || []).slice(0, 5);
    if (!ex.length) return;
    box.appendChild(el('summary', { class: 'hint ex-title', text: 'Examples' }));
    const list = el('ul', { class: 'ex-list' });
    for (const e of ex) {
      list.appendChild(el('li', {}, el('button', { type: 'button', class: 'ex-btn', title: e.note || null,
        onclick: () => { QS.inputs[d.id] = Object.assign({ field: 'atf' }, e.params); renderForm(d); run(d.id, {}); } },
        el('span', { class: 'ex-label', text: e.label || 'Example' }),
        el('span', { class: 'ex-q' }, el('span', { class: 'seq', text: exampleLabel(d.id, e.params) }),
          el('span', { class: 'n', text: `${fmt(e.n)} ${e.unit || 'in this dataset'}` }))),
        e.note ? el('span', { class: 'ex-note', text: e.note }) : null));
    }
    box.appendChild(list);
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
    if (isX(QS.current)) return renderExplorationResult();
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
    const meta = [['Period (source catalogue)', tb.period ? tb.period + (tb.period_uncertain ? ' (uncertain)' : '') : null], ['Collection', tb.collection], ['Museum', tb.museum],
      ['CDLI', tb.cdli], ['Genre', tb.genres.join('; ') || null], ['Joins', tb.joins ? String(tb.joins) : null]].filter(x => x[1]);
    box.appendChild(el('div', { class: 'answer tablet-head' }, el('h2', { class: 'headline', text: tb.catalog }),
      tb.description ? el('p', { class: 'desc', text: tb.description }) : null,
      meta.length ? el('dl', { class: 'meta' }, meta.map(([k, v]) => el('div', {}, el('dt', { text: k }), el('dd', { text: v })))) : null,
      tb.source_link ? el('p', { class: 'hint' }, 'Source record: ', el('a', { href: tb.source_link, target: '_blank', rel: 'noopener noreferrer', text: `${tb.catalog} in the eBL library` }),
        ' (photographs, where eBL has them, are there; this dataset has none).') : null));
    const field = r.field;
    const redraw = () => renderResult();
    const tools = el('div', { class: 'toolbar' });
    if (r.layers.length > 1) tools.appendChild(segmented('Layer', r.layers.map(l => [l, l === 'atf' ? 'Transliteration' : 'Sign numbers (ABZ)']), field,
      v => { QS.inputs.tablet = Object.assign({}, QS.inputs.tablet, { field: v }); renderForm(qdef('tablet')); run('tablet', {}); }));
    tools.appendChild(glyphToggle(redraw));
    box.appendChild(tools);
    if (!r.lines.length) { box.appendChild(el('div', { class: 'state unavailable', text: `This tablet has no ${r.field_label.toLowerCase()} layer in this dataset.` })); return; }
    const nTr = r.translations.filter(t => t.first_line).length;
    box.appendChild(el('div', { class: 'views-key' },
      el('span', {}, el('b', { text: 'Reading' }), ' the edition’s transliteration: sign values as the editor read them.'),
      QS.glyphs === 'paired' ? el('span', {}, el('b', { text: 'Glyph' }), ' a standard Unicode sign for that reading, not this tablet’s handwriting.') : null,
      field === 'atf' ? el('span', {}, el('b', { text: 'Translation' }), nTr ? ` the source’s own (${fmt(nTr)} lines), beside the lines it covers.` : ' none in the source for this tablet.') : null,
      el('span', { class: 'hint', text: 'Select a reading to explain it, or two in a line to ask about the span.' })));
    QS.sel = null;
    const ctx = { tablet: tb.catalog, record: tb.record || null, recordings: tb.recordings || [] };
    const idx = {}; r.lines.forEach((ln, i) => { idx[ln.id] = i; });
    const starts = {};
    for (const t of r.translations) if (t.first_line && idx[t.first_line] !== undefined) (starts[idx[t.first_line]] = starts[idx[t.first_line]] || []).push(t);
    const list = el('ol', { class: 'tablet-lines', 'aria-label': 'Lines' });
    let lastSurface = null;
    const lineRow = ln => {
      const surf = [ln.surface, ln.column_label ? 'column ' + ln.column_label : null].filter(Boolean).join(', ');
      const out = [];
      if (surf && surf !== lastSurface) out.push(el('li', { class: 'surface', text: surf }));
      lastSurface = surf || lastSurface;
      if (ln.comment) {
        out.push(el('li', { class: 'ln comment' }, el('span', { class: 'ln-no', text: '' }), el('span', { class: 'hint', text: 'A translation or note line that the backend parser kept as text (conservative-3); not part of the transliteration.' })));
        return out;
      }
      const toks = el('span', { class: 'ln-toks' }, ln.tokens.map((t, i) => [i ? ' ' : null,
        readingButton(t, field, ctx, { attrs: { 'data-line': ln.id, 'data-i': String(i) }, onclick: ev => pick(ln, i, ev) })]));
      out.push(el('li', { class: 'ln' }, el('span', { class: 'ln-no', text: ln.label || '' }), toks));
      return out;
    };
    const names = { en: 'English', de: 'German', ar: 'Arabic' };
    let i = 0;
    while (i < r.lines.length) {
      if (!starts[i]) { lineRow(r.lines[i]).forEach(x => list.appendChild(x)); i++; continue; }
      let end = i; const trs = [];
      for (let j = i; j <= end; j++) for (const t of starts[j] || []) { trs.push(t); end = Math.max(end, idx[t.last_line]); }
      const inner = el('ol', { class: 'tb-lines' });
      for (let j = i; j <= end; j++) lineRow(r.lines[j]).forEach(x => inner.appendChild(x));
      const lab = k => r.lines[k].label || '';
      list.appendChild(el('li', { class: 'tblock' }, inner, el('div', { class: 'tb-tr' }, trs.map(t => el('div', { class: 'pb-tr', lang: t.language },
        el('span', { class: 'pb-cap', text: `${names[t.language] || t.language} · source edition · ${t.lines > 1 ? `lines ${lab(idx[t.first_line])}–${lab(idx[t.last_line])}` : translationLabel(t, lab(idx[t.first_line]))}` }),
        trText(t.text),
        t.status === 'extent-unresolved' ? el('span', { class: 'hint', text: `The source says it runs to “${t.extent}”; that label is not in this edition, so it is shown at its first line.` }) : null)))));
      i = end + 1;
    }
    if (QS.glyphs === 'paired') box.appendChild(glyphLegend());
    box.appendChild(list);
    if (r.lines_truncated) box.appendChild(el('p', { class: 'hint', text: `First ${r.lines.length} lines shown.` }));
    if (r.translations.length) box.appendChild(el('p', { class: 'hint', text: r.translation_note }));
    for (const t of r.translations.filter(x => !x.first_line)) box.appendChild(el('p', { class: 'tr-loose', text: `${t.language} (placed before any text line in the source): ${t.text}` }));
    box.appendChild(el('div', { id: 'sel-bar', class: 'sel-bar', hidden: true, role: 'region', 'aria-label': 'Selection' }));
    QS.tabletCtx = ctx;
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
    if (toks.length === 1) barEl.appendChild(el('button', { type: 'button', class: 'primary', onclick: ev => openReading(toks[0], field, QS.tabletCtx || {}, ev.currentTarget) }, 'Explain this reading'));
    barEl.appendChild(el('button', { type: 'button', class: toks.length === 1 ? null : 'primary', disabled: tooLong ? true : null, title: tooLong ? 'At most 5 positions' : null,
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

  // eBL translation markup: @i{…} italic, @akk{…}/@sux{…} words in the ancient language, @url{…} kept as text.
  function trText(text, tag, cls) {
    const kids = [];
    const re = /@(i|akk|sux|url|b|sup|sub)\{([^{}]*)\}/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) kids.push(text.slice(last, m.index));
      const k = m[1];
      kids.push(k === 'url' ? m[2] : el(k === 'b' ? 'b' : k === 'sup' ? 'sup' : k === 'sub' ? 'sub' : 'i', { lang: k === 'akk' ? 'akk' : k === 'sux' ? 'sux' : null, class: k === 'akk' || k === 'sux' ? 'anc' : null, text: m[2] }));
      last = re.lastIndex;
    }
    if (last < text.length) kids.push(text.slice(last));
    return el(tag || 'p', { class: cls || 'tr-text' }, kids);
  }
  // ---------- glyph-paired tokens ----------
  // A glyph is a standardized Unicode sign looked up from the reading (eBL sign list, checked against ORACC OSL);
  // it is never the tablet's handwriting. Unmapped or ambiguous parts get no glyph.
  function glyphLegend() {
    return el('p', { class: 'glyph-legend hint' },
      el('span', { class: 'cun', 'aria-hidden': 'true', text: '𒁹' }), ' Glyphs are standard Unicode cuneiform looked up from each reading in the eBL sign list. They are not this tablet’s handwriting, and this dataset has no photographs. ',
      el('span', { class: 'dim', text: '·' }), ' marks a reading with no unambiguous glyph assignment.');
  }
  function glyphToggle(redraw) {
    return segmented('Show', [['paired', 'Reading + glyph'], ['reading', 'Reading only']], QS.glyphs, v => { QS.glyphs = v; redraw(); });
  }
  function tokBody(t) {
    const kids = [];
    if (QS.glyphs === 'paired') {
      const g = t.glyph && t.glyph.g;
      kids.push(el('span', { class: 'g' + (g ? (t.glyph.full ? '' : ' partial') : ' none'), 'aria-hidden': 'true', text: g || '·' }));
    }
    kids.push(el('span', { class: 'rd', text: t.raw }));
    return kids;
  }
  function readingButton(t, field, ctx, extra) {
    const cls = ['tk', tokenSpan(t).className, QS.glyphs === 'paired' ? 'paired' : ''].join(' ');
    return el('button', Object.assign({ type: 'button', class: cls, title: CW.tokenTitle(t) + ' — select to explain',
      onclick: ev => { if (extra && extra.onclick) return extra.onclick(ev); openReading(t, field, ctx, ev.currentTarget); } }, extra && extra.attrs), tokBody(t));
  }

  // ---------- explorations ----------
  function renderExploration(e) {
    $('#q-title').textContent = e.title;
    $('#q-title').tabIndex = -1;
    $('#q-prompt').textContent = e.prompt;
    clear($('#q-form'));
    const box = clear($('#q-examples'));
    box.appendChild(el('p', { class: 'x-counts' }, el('b', { text: fmt(e.texts) }), ` texts · `, el('b', { text: fmt(e.with_english) }), ' with an English translation in the source',
      e.with_any_translation > e.with_english ? ` · ${fmt(e.with_any_translation)} with any translation` : ''));
    const id = QS.current;
    if (!QS.results[id] || QS.results[id].stale) runExplore(id, {});
    else renderExplorationResult();
  }
  function xParams(id, extra) {
    const v = Object.assign({}, QS.inputs[id] || {}, extra || {});
    for (const k of Object.keys(v)) if (v[k] === null || v[k] === undefined || v[k] === '') delete v[k];
    return v;
  }
  async function runExplore(id, extra) {
    const p = xParams(id, extra);
    QS.inputs[id] = p;
    if (!state.version) { QS.results[id] = { error: 'This dataset is not being served right now.' }; return renderExplorationResult(); }
    const seq = ++QS.seq;
    QS.results[id] = { loading: true, params: p };
    renderExplorationResult();
    CW.setHash();
    try {
      const data = await api('/api/explore', bound({ exploration: id.slice(2), params: p }));
      if (seq !== QS.seq && QS.results[id] && !QS.results[id].loading) return;
      QS.results[id] = { data, params: p };
    } catch (e) { QS.results[id] = { error: friendlyError(e.message), params: p }; }
    if (QS.current === id) renderExplorationResult();
  }
  function renderExplorationResult() {
    const id = QS.current, res = QS.results[id], e = xdef(id);
    const box = clear($('#q-result'));
    box.removeAttribute('aria-busy');
    if (QS.notice) box.appendChild(el('div', { class: 'state stale', role: 'alert' }, el('b', { text: 'Results cleared. ' }), QS.notice));
    if (!res) return;
    if (res.loading) { box.setAttribute('aria-busy', 'true'); box.appendChild(el('p', { class: 'state loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Gathering texts…')); return; }
    if (res.error) { box.appendChild(el('div', { class: 'state error', role: 'alert', text: res.error })); return; }
    const r = res.data;
    const views = [['texts', 'Texts'], ['passages', 'Passages with translations']];
    if (e.id === 'omens') views.unshift(['openings', 'How lines open']);
    const bar = el('div', { class: 'toolbar x-tools' }, segmented('View', views, r.view, v => runExplore(id, { view: v, offset: null })));
    if (r.subgenres.length) {
      const sel = el('select', { 'aria-label': 'Narrow by genre', onchange: ev => runExplore(id, { subgenre: ev.target.value || null, offset: null }) },
        el('option', { value: '', text: 'All genres in this exploration' }),
        r.subgenres.map(g => el('option', { value: g.genre, selected: r.filters.subgenre === g.genre ? true : null, text: `${g.label} (${fmt(g.texts)})` })));
      bar.appendChild(el('label', { class: 'inline' }, el('span', { class: 'sr-only', text: 'Genre' }), sel));
    }
    if (r.view !== 'openings' && r.languages.length > 1) {
      const names = { en: 'English', de: 'German', ar: 'Arabic' };
      bar.appendChild(el('label', { class: 'inline' }, el('span', { class: 'sr-only', text: 'Translation language' }),
        el('select', { 'aria-label': 'Translation language', onchange: ev => runExplore(id, { language: ev.target.value || null, offset: null }) },
          el('option', { value: '', text: 'Any translation language' }),
          r.languages.filter(l => names[l.code]).map(l => el('option', { value: l.code, selected: r.filters.language === l.code ? true : null, text: `${names[l.code]} (${fmt(l.texts)} texts)` })))));
    }
    if (r.view === 'texts' && !e.filter.translated) {
      const cb = el('input', { type: 'checkbox', checked: r.filters.translated_only ? true : null, onchange: ev => runExplore(id, { translated_only: ev.target.checked || null, offset: null }) });
      bar.appendChild(el('label', { class: 'inline check' }, cb, 'Only texts with a translation'));
    }
    box.appendChild(bar);
    box.appendChild(el('p', { class: 'hint x-explain', text: r.explain }));
    if (r.view === 'texts') renderXTexts(r, box, id);
    if (r.view === 'passages') renderXPassages(r, box, id);
    if (r.view === 'openings') renderXOpenings(r, box);
    box.appendChild(el('footer', { class: 'q-foot' }, el('details', { class: 'method' }, el('summary', { text: 'Where these come from' }),
      el('ul', {}, el('li', { text: 'Genres and periods are the source catalogue’s labels (eBL). Texts without a genre label are left out rather than guessed.' }),
        el('li', { text: 'Translations are the source edition’s own lines, shown beside the transliteration lines they cover. Nothing here is machine-translated.' }),
        r.timing ? el('li', { class: 'hint', text: `Answered in ${fmt(Math.round(r.timing.end_to_end_ms))} ms, dataset version ${String(r.dataset.version).slice(0, 12)}.` }) : null))));
  }
  function trSummary(tr) {
    const names = { en: 'English', de: 'German', ar: 'Arabic' };
    const ks = Object.keys(tr || {}).filter(k => names[k]);
    if (!ks.length) return el('span', { class: 'muted', text: 'None in the source' });
    return el('span', {}, ks.sort((a, b) => tr[b] - tr[a]).map((k, i) => [i ? ', ' : null, `${names[k]} · ${fmt(tr[k])} ${tr[k] === 1 ? 'line' : 'lines'}`]));
  }
  function pagerX(r, id, unit) {
    const pg = r.page, total = r.totals.shown_texts;
    return el('div', { class: 'pager' }, el('span', { text: `${fmt(pg.offset + 1)}–${fmt(Math.min(pg.offset + pg.limit, total))} of ${fmt(total)} ${unit}` }),
      el('span', { class: 'btns' },
        el('button', { type: 'button', disabled: pg.offset > 0 ? null : true, onclick: () => runExplore(id, { offset: Math.max(0, pg.offset - pg.limit) }) }, '← Previous'),
        el('button', { type: 'button', disabled: pg.next_offset === null ? true : null, onclick: () => runExplore(id, { offset: pg.next_offset }) }, 'Next →')));
  }
  function renderXTexts(r, box, id) {
    const e = xdef(id);
    if (!r.texts.length) { box.appendChild(el('div', { class: 'state empty', text: 'No texts match these filters.' })); return; }
    box.appendChild(el('table', { class: 'data x-texts' }, el('caption', { class: 'sr-only', text: 'Texts' }),
      el('thead', {}, el('tr', {}, ['Museum number', 'What the catalogue says', 'Period', 'Lines', 'Translation in the source'].map((h, i) => el('th', { scope: 'col', class: i === 3 ? 'n' : null, text: h })))),
      el('tbody', {}, r.texts.map(t => el('tr', {},
        el('td', {}, el('button', { type: 'button', class: 'linkish', onclick: () => openTablet(t.catalog) }, t.catalog)),
        el('td', { class: 'x-desc' }, el('span', { class: 'x-genre', text: t.genres.join('; ') || (e.filter.genres ? 'No finer genre recorded' : 'No genre recorded') }),
          t.description ? el('span', { class: 'x-d', text: t.description }) : null,
          t.recordings ? el('span', { class: 'x-rec', text: 'Scholars’ recordings of this composition exist' }) : null),
        el('td', { text: t.period || '—' }), el('td', { class: 'n', text: fmt(t.lines) }), el('td', {}, trSummary(t.translations)))))));
    box.appendChild(pagerX(r, id, 'texts, translated first'));
  }
  function translationLabel(t, label) {
    return t.status === 'line' ? `attached after line ${label} · no source range; may cover earlier lines` : `line ${label}`;
  }
  function passageBlock(bl, ctx) {
    const tr = bl.translation;
    const lines = el('ol', { class: 'pb-lines' }, bl.lines.map(ln => el('li', {}, el('span', { class: 'ln-no', text: ln.label || '' }),
      el('span', { class: 'ln-toks' }, ln.tokens.map((t, i) => [i ? ' ' : null, readingButton(t, 'atf', ctx)])))));
    const names = { en: 'English', de: 'German', ar: 'Arabic' };
    const span = bl.lines.length > 1 ? `lines ${bl.lines[0].label}–${bl.lines[bl.lines.length - 1].label}` : translationLabel(tr, bl.lines[0].label);
    return el('div', { class: 'pblock' }, lines,
      el('div', { class: 'pb-tr', lang: tr.language },
        el('span', { class: 'pb-cap', text: `${names[tr.language] || tr.language} translation · source edition · ${span}` }),
        trText(tr.text),
        tr.status === 'extent-unresolved' ? el('span', { class: 'hint', text: `The source says this runs to “${tr.extent}”, which is not among this edition’s line labels; shown at its first line.` }) : null));
  }
  function renderXPassages(r, box, id) {
    if (!r.passages.length) { box.appendChild(el('div', { class: 'state empty', text: 'No translated passages match these filters.' })); return; }
    for (const t of r.passages) {
      const ctx = { tablet: t.catalog };
      box.appendChild(el('section', { class: 'x-passage' },
        el('header', {}, el('h2', { class: 'xp-title' }, el('button', { type: 'button', class: 'linkish', onclick: () => openTablet(t.catalog) }, t.catalog)),
          el('span', { class: 'hint', text: [t.genres.join('; '), t.period].filter(Boolean).join(' · ') }),
          el('span', { class: 'hint', text: `first ${t.shown} of ${fmt(Object.values(t.translations).reduce((a, b) => a + b, 0))} translation lines` })),
        t.blocks.map(b => passageBlock(b, ctx)),
        el('button', { type: 'button', class: 'ghost', onclick: () => openTablet(t.catalog) }, 'Read the whole tablet →')));
    }
    box.insertBefore(el('div', { class: 'toolbar' }, glyphToggle(renderExplorationResult)), box.querySelector('.x-passage'));
    if (QS.glyphs === 'paired') box.insertBefore(glyphLegend(), box.querySelector('.x-passage'));
    box.appendChild(pagerX(r, id, 'translated texts'));
  }
  function renderXOpenings(r, box) {
    const o = r.openings;
    const pct = o.lines ? Math.round(100 * o.opened.lines / o.lines) : 0;
    box.appendChild(el('div', { class: 'answer' }, el('h2', { class: 'headline' },
      `${fmt(o.opened.lines)} of ${fmt(o.lines)} lines (${pct}%) in ${fmt(o.tablets)} divination texts open with `, el('span', { class: 'r', text: o.opening }), '.'),
      el('p', { class: 'sub' }, 'In this position DIŠ is usually read (Borger marks this with a query) as ', el('i', { text: 'šumma' }), ' ‘if’: the line states an observation, and the result follows. ',
        el('button', { type: 'button', class: 'linkish', onclick: ev => openReading({ raw: o.opening, reading: o.opening }, 'atf', {}, ev.currentTarget) }, 'Why DIŠ can mean different things'))));
    const max = o.next.length ? o.next[0].lines : 0;
    const tb = el('tbody');
    for (const x of o.next) {
      const slots = [{ reading: o.opening }, x.parsed ? { reading: x.word } : { raw: x.word }];
      const ex = x.example;
      tb.appendChild(el('tr', {},
        el('td', { class: 'filler' }, el('button', { type: 'button', class: 'linkish', title: 'Every context of this opening, in all texts',
          onclick: () => { QS.inputs.sequence = { tokens: `${o.opening} ${x.word}`, field: 'atf' }; QS.current = 'sequence'; render(); run('sequence', { slots }); } },
          el('span', { class: 'r', text: o.opening }), ' ', partEl({ text: x.word, parsed: x.parsed, kind: x.kind }))),
        el('td', { class: 'n', text: fmt(x.lines) }), el('td', { class: 'n', text: fmt(x.tablets) }), el('td', { class: 'barcell' }, bar(x.lines, max)),
        el('td', { class: 'op-ex' }, ex ? [
          el('button', { type: 'button', class: 'op-line', onclick: ev => openPassage({ tablet: ex.tablet, line: { label: ex.line_label }, line_id: ex.line_id, occurrence_ids: ex.tokens.slice(0, 2).map(t => t.id) }, ev.currentTarget) },
            el('span', { class: 'tab-id', text: `${ex.tablet} ${ex.line_label || ''}` }), ' ', ex.tokens.slice(0, 9).map(t => t.raw).join(' ') + (ex.tokens.length > 9 ? ' …' : '')),
          ex.translation ? trText('“' + ex.translation.text + '”', 'span', 'op-tr') : el('span', { class: 'hint', text: 'No source translation covers this example.' })] : null)));
    }
    box.appendChild(el('table', { class: 'data openings' }, el('caption', { class: 'sr-only', text: 'What follows the opening' }),
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', text: 'Opening' }), el('th', { scope: 'col', class: 'n', text: 'Lines' }), el('th', { scope: 'col', class: 'n', text: 'Tablets' }),
        el('th', { scope: 'col' }), el('th', { scope: 'col', text: 'Example, with the source’s translation where it has one' }))), tb));
    box.appendChild(el('p', { class: 'hint', text: `The ${o.next.length} most frequent of ${fmt(o.distinct_next)} different second readings. Select an opening to see every line with it across all texts. Translations are the editors’ renderings of the whole line.` }));
  }

  // ---------- reading explanation ----------
  async function openReading(t, field, ctx, fromEl) {
    const aside = $('#inspector'), body = clear($('#insp-body'));
    QS.inspReturn = fromEl || null;
    const text = t.reading !== null && t.reading !== undefined && field === 'atf' ? t.reading : t.raw;
    $('#insp-title').textContent = text;
    aside.hidden = false; document.body.classList.add('insp-open');
    $('#insp-title').tabIndex = -1; $('#insp-title').focus();
    body.appendChild(el('p', { class: 'state loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Looking up this reading…'));
    const token = ++QS.seq; QS.insp = token;
    try {
      const r = await api('/api/reading' + q(bound(Object.assign({ text, field }, ctx && ctx.record ? { record: ctx.record } : {}))));
      if (QS.insp !== token) return;
      renderReading(clear(body), r, ctx || {}, t);
    } catch (e) {
      if (QS.insp !== token) return;
      clear(body).appendChild(el('div', { class: 'state error', text: friendlyError(e.message) }));
    }
  }
  function srcCite(sources, key, where) {
    const s = sources[key]; if (!s) return null;
    return el('span', { class: 'cite' }, '— ', el('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer', text: s.title }), where ? `, ${where}` : '');
  }
  function signLine(card, label) {
    return el('div', { class: 'sign-card' }, el('span', { class: 'cun big', 'aria-hidden': card.glyph ? 'true' : null, text: card.glyph || '·' }),
      el('span', {}, label ? el('span', { class: 'hint', text: label + ' ' }) : null, el('b', { text: card.name }),
        card.abz && card.abz.length ? el('span', { class: 'hint', text: ` · ABZ ${card.abz.join(', ')}` }) : null,
        card.codepoints && card.codepoints.length ? el('span', { class: 'hint mono', text: ' · ' + card.codepoints.join(' ') }) : null,
        el('br'), el('span', { class: 'hint', text: card.glyph ? (card.check === 'agrees' ? 'Sign name → Unicode agrees in eBL and OSL; this does not verify the reading → sign assignment.' : 'Glyph: eBL sign list only (not in ORACC OSL under this name).') : 'No verified glyph.' }),
        ' ', el('a', { href: card.link, target: '_blank', rel: 'noopener noreferrer', text: 'eBL sign page' })));
  }
  function renderReading(body, r, ctx, t) {
    const S = r.sources;
    const sec = (title, ...kids) => el('section', { class: 'rd-sec' }, el('h3', { text: title }), kids);
    // 1. what is written
    const kinds = r.conventions.map(c => el('li', {}, c.text, ' ', srcCite(S, c.cite, c.where)));
    const parts = [];
    if (r.field === 'signs') {
      const a = r.abz;
      parts.push(el('p', { text: a.status === 'ambiguous' ? `${r.text} is one sign-list number that covers ${a.signs.length} signs; the transliteration of the same line says which one the editor read.` :
        a.status === 'not-in-map' ? `${r.text} is not an ABZ number in the sign map, so no glyph is shown.` : `${r.text} is a sign number from R. Borger’s sign list (ABZ), computed by eBL from the transliteration.` }));
      a.signs.forEach(c => parts.push(c.link ? signLine(c, a.status === 'ambiguous' ? 'could be' : null) : el('p', { text: c.name })));
    } else {
      for (const p of r.parts) {
        if (p.sign) { parts.push(el('p', { class: 'hint', text: p.assignment_note })); parts.push(signLine(p.sign, p.determinative ? `{${p.part}} determinative:` : `${p.part}:`)); }
        else if (p.candidates) { parts.push(el('p', { class: 'hint', text: `${p.part} is a value of ${p.candidates.length} signs in the eBL list:` })); p.candidates.forEach(c => parts.push(signLine(c, 'could be'))); }
        else parts.push(el('p', { class: 'hint', text: `${p.part}: ${p.status === 'number' ? 'a number (no glyph lookup).' : 'not found as a sign value in the eBL list, so no glyph.'}` }));
      }
    }
    body.appendChild(sec('What is written', el('ul', { class: 'rd-list' }, kinds), parts));
    // 2. how it is read (cited contextual uses)
    if (r.note) {
      body.appendChild(sec('How it is read', el('p', { text: r.note.summary }),
        el('ul', { class: 'rd-list' }, r.note.uses.map(u => el('li', {}, el('b', { text: u.context + ': ' }), u.use, ' ', srcCite(S, u.cite, u.where)))),
        el('p', { class: 'hint', text: r.note.caution })));
    }
    // 3. ORACC lemmatization
    const L = r.lemmas;
    if (L) {
      if (!L.available) body.appendChild(sec('Dictionary words (ORACC)', el('p', { class: 'hint', text: L.note })));
      else if (!L.lemmas.length) body.appendChild(sec('Dictionary words (ORACC)', el('p', { class: 'hint', text: `ORACC ccpo (${L.texts} lemmatized texts) has no word written exactly “${L.form}”. That says nothing about the word in general.` })));
      else body.appendChild(sec('Dictionary words (ORACC)', el('p', { class: 'hint', text: `How editors of ${L.project} (${L.texts} texts) lemmatized words written “${L.form}”:` }),
        el('ul', { class: 'rd-list lem' }, L.lemmas.slice(0, 6).map(l => el('li', {}, el('i', { text: l.citation_form }), ` [${l.guideword}] `, el('span', { class: 'hint', text: `${l.pos} · ${fmt(l.n)}×` }),
          l.senses.length > 1 ? el('span', { class: 'hint', text: ' · senses: ' + l.senses.map(s => `${s.gloss} ${s.n}×`).join(', ') } ) : null))),
        el('p', { class: 'hint', text: L.note })));
    }
    // 4. in this dataset
    const C = r.corpus;
    const relatedBtns = el('div', { class: 'insp-actions' },
      el('button', { type: 'button', class: 'primary', onclick: () => { QS.inputs.sequence = { tokens: r.text, field: r.field }; closeInspector(); QS.current = 'sequence'; render(); run('sequence', {}); } }, 'Where else does it occur?'),
      el('button', { type: 'button', onclick: () => { QS.inputs.usage = { token: r.text, field: r.field }; closeInspector(); QS.current = 'usage'; render(); run('usage', {}); } }, 'How is it used?'));
    const exs = (C.translated_examples || []).map(e => el('li', { class: 'rd-ex' },
      el('button', { type: 'button', class: 'op-line', onclick: () => openTablet(e.tablet) }, el('span', { class: 'tab-id', text: `${e.tablet} ${e.line_label || ''}` }), ' ',
        e.tokens.map((x, i) => [i ? ' ' : null, x.id === e.occurrence_id ? el('mark', { class: 't hit', text: x.raw }) : x.raw])),
      trText('“' + e.translation.text + '”', 'span', 'op-tr'),
      e.translation.lines > 1 ? el('span', { class: 'hint', text: ` (this translation covers ${e.translation.lines} lines)` }) : null));
    body.appendChild(sec('In this dataset',
      el('p', { text: C.positions ? `${fmt(C.positions)} times on ${fmt(C.tablets)} tablets${C.matched_as === 'unparsed source text' ? ' (as an unparsed written word)' : ''}.` : 'Not found in this dataset.' }),
      C.by_genre && C.by_genre.length ? el('p', { class: 'hint', text: 'Most often in: ' + C.by_genre.slice(0, 5).map(g => `${g.genre} (${fmt(g.tablets)} tablets)`).join('; ') }) : null,
      exs.length ? [el('p', { class: 'hint', text: 'Lines with this reading that the source translates. The translation renders the whole line or passage, not this reading alone:' }), el('ul', { class: 'rd-exs' }, exs)] :
        (C.positions ? el('p', { class: 'hint', text: 'No line with this reading is covered by an English source translation.' }) : null),
      C.positions ? relatedBtns : null));
    // 5. reading & pronunciation
    const P = r.pronunciation;
    const norm = L && L.available && L.lemmas.length ? L.lemmas.slice(0, 3).map(l => l.citation_form) : [];
    const recs = (ctx.recordings || []);
    body.appendChild(sec(P.label,
      norm.length ? el('p', {}, 'Normalized form in ORACC ccpo: ', norm.map((n, i) => [i ? ', ' : null, el('i', { text: n })]), el('span', { class: 'hint', text: ' (a modern scholarly transcription, not a recording of ancient speech).' })) :
        (r.note ? null : el('p', { class: 'hint', text: 'No cited normalized form for this exact spelling here.' })),
      el('ul', { class: 'rd-list' }, P.points.map(x => el('li', { text: x }))),
      recs.length ? el('p', {}, 'Recordings of this composition (another manuscript, not this tablet): ', recs.map((x, i) => [i ? '; ' : null, el('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer', text: `${x.title}, read by ${x.reader}` })])) : null,
      el('p', { class: 'hint' }, 'Listen to scholars reading Akkadian aloud: ', el('a', { href: S.baplar.url, target: '_blank', rel: 'noopener noreferrer', text: S.baplar.title }), '. Links open the SOAS site; nothing plays here.')));
    body.appendChild(el('p', { class: 'hint rd-foot', text: 'Sign data: eBL sign list (fetched for this workbench) checked against ORACC OSL (CC0). Notes cite their source; nothing here is a word-for-word translation.' }));
    void t;
  }

  // ---------- integration with app.js ----------
  const HASH_KEYS = { sequence: ['tokens', 'field'], between: ['before', 'gap', 'after', 'field'], usage: ['token', 'field'], tablet: ['catalog', 'field'] };
  const XHASH = { view: 'xv', subgenre: 'xg', language: 'xl' };
  CW.hashParams = () => {
    if (!QS.current) return {};
    const v = QS.inputs[QS.current] || {}, out = { q: QS.current };
    if (isX(QS.current)) { for (const [k, h] of Object.entries(XHASH)) if (v[k]) out[h] = v[k]; return out; }
    for (const k of HASH_KEYS[QS.current] || []) if (v[k] && !(k === 'field' && v[k] === 'atf')) out[k] = v[k];
    return out;
  };
  CW.onReset = () => {
    const userSwitched = QS.lastDs !== null && QS.lastDs !== state.ds;
    const had = Object.keys(QS.results).length > 0;
    QS.results = {}; QS.drill = {}; QS.regFor = null; QS.tabletCtx = null;
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
    if (qid && isX(qid)) {
      QS.current = qid; const v = {};
      for (const [k, hk] of Object.entries(XHASH)) if (h.get(hk)) v[k] = h.get(hk);
      QS.inputs[qid] = v; return false;
    }
    if (!qid || !HASH_KEYS[qid]) return false;
    QS.current = qid;
    const v = {};
    for (const k of HASH_KEYS[qid]) if (h.get(k)) v[k] = h.get(k);
    QS.inputs[qid] = v;
    return Object.keys(v).some(k => k !== 'field');
  }
  $('#insp-close').addEventListener('click', closeInspector);
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !$('#inspector').hidden && !ev.target.closest('.suggest, [role=combobox]')) closeInspector(); });
  window.addEventListener('hashchange', async () => {
    const shouldRun = fromHash();
    await boot();
    if (shouldRun && state.version) run(QS.current, {});
  });
  const auto = fromHash();
  // app.js's first dataset selection calls onReset (and so boot); run the linked question once it is ready.
  const first = CW.onReset;
  CW.onReset = () => {
    first();
    if (auto && !QS._autoDone && state.version) { QS._autoDone = true; boot().then(() => run(QS.current, {})); }
  };
})();
