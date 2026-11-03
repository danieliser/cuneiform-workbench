/* Cuneiform Workbench client. Corpus content is untrusted: DOM is built with
   createElement/textContent only (no innerHTML), and links come from the server's own config. */
(function () {
  'use strict';
  const $ = sel => document.querySelector(sel);
  const SAVED_KEY = 'cuneiform-ui:saved-queries';
  const state = { status: null, ds: null, view: 'query', lastQuery: null, result: null, selectedRow: null,
    graph: { center: null, data: null, history: [], selected: null, pos: null }, report: null, falkor: null };

  // ---------- DOM helpers ----------
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else e.setAttribute(k, v === true ? '' : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      e.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return e;
  }
  const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); return node; };
  const fmt = n => (typeof n === 'number' ? n.toLocaleString() : n ?? '—');
  const shortId = id => (id && id.length > 22 ? id.slice(0, id.indexOf(':') + 9) + '…' : id || '');
  function kv(pairs) {
    const dl = el('dl', { class: 'kv' });
    for (const [k, v] of pairs) {
      if (v === undefined) continue;
      dl.appendChild(el('dt', { text: k }));
      dl.appendChild(el('dd', {}, v instanceof Node ? v : (v === null ? el('span', { class: 'muted', text: 'null' }) : String(v))));
    }
    return dl;
  }
  function idLink(id, label) {
    return el('button', { class: 'link', title: id + ' — open in graph', onclick: () => openGraph(id) }, label || shortId(id));
  }

  async function api(path, body) {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const res = await fetch(path, opts);
    let data;
    try { data = await res.json(); } catch (_) { data = { error: `HTTP ${res.status}` }; }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  const q = params => '?' + new URLSearchParams(params).toString();

  // ---------- status / datasets ----------
  async function loadStatus(rediscover) {
    const st = rediscover ? Object.assign(state.status || {}, await api('/api/refresh', {})) : await api('/api/status');
    if (!rediscover) state.status = st;
    renderStatus();
  }
  function renderStatus() {
    const st = state.status;
    const b = st.backend;
    const chip = $('#backend-chip');
    chip.className = 'chip ' + (b.compatibility === 'tested' ? 'ok' : b.compatibility === 'incompatible' ? 'bad' : 'warn');
    chip.textContent = `backend ${b.commit ? b.commit.slice(0, 7) : '?'}${b.dirty ? '+dirty' : ''} · schema ${b.versions.schema} · ${b.versions.parser} · ${b.versions.projection} · ${b.compatibility}`;
    chip.title = b.repo + (b.problems.length ? '\n' + b.problems.join('\n') : '');
    const sel = clear($('#dataset'));
    for (const d of st.datasets) {
      const tag = d.kind === 'snapshot' ? 'SNAPSHOT' : d.kind === 'sample' ? 'SAMPLE' : 'SYNTHETIC';
      sel.appendChild(el('option', { value: d.key, disabled: d.status !== 'ready', text: `[${tag}] ${d.title}${d.status !== 'ready' ? ' — ' + d.status : ''}` }));
    }
    const keys = st.datasets.filter(d => d.status === 'ready').map(d => d.key);
    const fromHash = new URLSearchParams(location.hash.slice(1)).get('ds');
    if (!state.ds || !keys.includes(state.ds)) state.ds = keys.includes(fromHash) ? fromHash : keys[0] || null;
    if (state.ds) sel.value = state.ds;
    const banner = $('#banner');
    const msgs = [];
    if (b.compatibility === 'incompatible') msgs.push('Backend is incompatible with this UI build: ' + b.problems.join('; '));
    else if (b.compatibility !== 'tested') msgs.push(`Backend revision ${b.commit ? b.commit.slice(0, 7) : 'unknown'}${b.dirty ? ' (uncommitted changes)' : ''} has matching versions but was not the tested revision; results come from it unchanged.`);
    if (!st.real_snapshots) msgs.push(`No persisted snapshots found under ${st.snapshot_dir}${st.snapshot_dir_exists ? '' : ' (directory not created yet)'} — showing labeled samples and synthetic fixtures. The corpus pilot will appear here after Refresh data once it is persisted.`);
    const failing = st.datasets.filter(d => d.kind === 'snapshot' && d.status !== 'ready');
    for (const d of failing) msgs.push(`Snapshot ${d.key.slice(0, 21)}…: ${d.status}${d.error ? ' — ' + d.error : ''}`);
    banner.hidden = !msgs.length;
    banner.className = 'banner' + (b.compatibility === 'incompatible' ? ' bad' : '');
    clear(banner);
    msgs.forEach(m => banner.appendChild(el('div', { text: m })));
    $('#foot').textContent = `Server ${st.server.bind} · pid ${st.server.pid} · backend ${b.repo} · Python ${b.python} · DuckDB ${b.duckdb} · research root ${b.research_root}`;
    renderDatasetBadge();
  }
  function current() { return state.status && state.status.datasets.find(d => d.key === state.ds); }
  function renderDatasetBadge() {
    const d = current(), badge = $('#dataset-badge');
    if (!d) { badge.textContent = 'no data'; badge.className = 'badge'; return; }
    badge.textContent = d.kind === 'snapshot' ? 'verified snapshot' : d.kind === 'sample' ? 'in-memory sample' : 'synthetic';
    badge.className = 'badge ' + d.kind;
    badge.title = d.authority;
  }
  function setHash() {
    const p = new URLSearchParams({ view: state.view });
    if (state.ds) p.set('ds', state.ds);
    history.replaceState(null, '', '#' + p.toString());
  }

  // ---------- views ----------
  function show(view, skipStart) {
    state.view = view;
    document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.view === view)));
    document.querySelectorAll('.view').forEach(s => { s.hidden = s.id !== 'view-' + view; });
    setHash();
    if (view === 'graph' && !state.graph.data && !skipStart) startGraph();
    if (view === 'coverage') renderCoverage();
    if (view === 'falkordb') renderFalkor();
  }

  // ---------- query ----------
  function formValues() {
    return {
      pattern: $('#pattern').value.trim().split(/\s+/).filter(Boolean),
      field: $('#field').value, engine: $('#engine').value,
      policy: document.querySelector('input[name=policy]:checked').value,
      max_skip: Number($('#maxskip').value), limit: Number($('#limit').value)
    };
  }
  function setForm(ex) {
    $('#pattern').value = (ex.pattern || []).join(' ');
    $('#field').value = ex.field || 'atf';
    $('#engine').value = ex.engine || 'index';
    document.querySelector(`input[name=policy][value=${ex.policy === 'strict' ? 'strict' : 'source'}]`).checked = true;
    $('#maxskip').value = String(ex.max_skip || 0);
    syncSkip();
  }
  function syncSkip() {
    const graph = $('#engine').value === 'graph';
    $('#maxskip').disabled = !graph;
    if (!graph) $('#maxskip').value = '0';
  }
  async function runQuery(offset) {
    const v = formValues();
    if (!state.ds) return;
    const body = Object.assign({ dataset: state.ds, offset: offset || 0 }, v);
    state.lastQuery = body;
    const sum = clear($('#qsummary'));
    sum.appendChild(el('p', { class: 'muted', text: 'Running…' }));
    try {
      const r = await api('/api/query', body);
      state.result = r; state.selectedRow = null;
      renderResults(r);
    } catch (e) {
      clear(sum).appendChild(el('p', { class: 'error', text: e.message }));
      clear($('#results tbody')); clear($('#results thead')); clear($('#pager'));
    }
  }
  function tokenClass(t, hit) {
    const c = ['tok'];
    if (t.kind === 'gap') c.push('gap'); if (t.kind === 'unsupported') c.push('unsupported');
    if (t.certainty === 'uncertain') c.push('uncertain');
    if (t.preservation === 'lost') c.push('lost'); if (t.preservation === 'damaged') c.push('damaged');
    if (hit) c.push('hit');
    return c.join(' ');
  }
  function tokenTitle(t) {
    return `${t.raw ?? ''}\nreading: ${t.reading ?? '∅ (blocking/unreadable)'}\nkind: ${t.kind}  preservation: ${t.preservation}  certainty: ${t.certainty}\n${t.id}`;
  }
  function renderResults(r) {
    const sum = clear($('#qsummary'));
    const head = r.missing ? el('div', { class: 'count' }, 'Unavailable', el('small', { text: 'no sequences for this field' }))
      : el('div', { class: 'count' }, fmt(r.count), el('small', { text: r.kind === 'postings' ? 'postings' : 'matching windows' }));
    sum.appendChild(el('div', { style: null }, head));
    const ds = current();
    sum.appendChild(el('div', { class: 'summary-grid' },
      el('div', {}, el('b', { text: 'Pattern' }), el('span', { class: 'mono', text: r.pattern.join(' ') })),
      el('div', {}, el('b', { text: 'Field' }), r.field),
      el('div', {}, el('b', { text: 'Policy' }), r.policy),
      el('div', {}, el('b', { text: 'Engine' }), r.engine + (r.max_skip ? ` (skip ≤ ${r.max_skip})` : '')),
      el('div', {}, el('b', { text: 'Dataset' }), el('span', { class: 'badge ' + ds.kind, text: ds.kind })),
      r.scope ? el('div', {}, el('b', { text: 'Scope' }), el('span', { class: 'mono', text: `${r.scope.namespace} · ${r.scope.snapshot_id}` })) : null,
      r.parity && r.parity.ok !== undefined ? el('div', {}, el('b', { text: 'Graph/index parity' }),
        el('span', { class: 'pill ' + (r.parity.ok ? 'ok' : 'bad'), text: `index ${r.parity.index} · graph ${r.parity.graph} ${r.parity.ok ? '✓' : '✗'}` })) : null,
      r.graph_summary ? el('div', {}, el('b', { text: 'Support' }), `${fmt(r.graph_summary.witnesses)} witnesses · ${fmt(r.graph_summary.artifacts)} artifacts`) : null,
      r.graph_summary ? el('div', {}, el('b', { text: 'Denominator' }), `${fmt(r.graph_summary.denominators.represented_positions)} represented positions · ${fmt(r.graph_summary.denominators.sequences)} sequences`) : null,
      r.graph_summary ? el('div', {}, el('b', { text: 'Certain physical evidence' }), fmt(r.graph_summary.certain_physical_evidence_count)) : null
    ));
    const notes = el('ul', { class: 'notes' },
      el('li', { text: `Evidence: ${r.evidence}. ${r.policy_note}.` }),
      el('li', { text: `Gap extent: ${r.gap_extent}. Matching is line-local.` }),
      r.graph_summary ? el('li', { text: `Bounds: ${r.graph_summary.bounds}. Composition coverage unknown.` }) : null,
      r.policy === 'strict' && r.count === 0 ? el('li', { text: 'Strict = 0 is expected for current eBL data: the source asserts neither intact nor certain from an absence of editorial marks. See Coverage → position states.' }) : null,
      r.note ? el('li', { text: r.note }) : null,
      ds.kind !== 'snapshot' ? el('li', { text: ds.authority }) : null);
    sum.appendChild(notes);
    if (r.missing) sum.appendChild(el('div', { class: 'missing', text: r.missing }));
    const thead = clear($('#results thead')), tbody = clear($('#results tbody'));
    thead.appendChild(el('tr', {}, el('th', { text: '#' }), el('th', { text: 'Artifact' }), el('th', { text: 'Line' }), el('th', { text: 'Window' }), el('th', { text: 'Positions' }), el('th', { text: 'Match ID' })));
    if (!r.rows.length) {
      tbody.appendChild(el('tr', {}, el('td', { colspan: 6, class: 'muted', text: r.missing ? 'No sequences in this field for this dataset.' : 'No matches for this pattern under this policy.' })));
    }
    r.rows.forEach((h, i) => {
      const tr = el('tr', { onclick: () => selectRow(i, h, 0) });
      const toks = el('div', {}, h.tokens.map((t, j) => el('button', { class: tokenClass(t, r.kind === 'graph-motif' ? (t.reading !== null && r.pattern.includes(t.reading)) : r.pattern[j] !== '?'), title: tokenTitle(t),
        onclick: ev => { ev.stopPropagation(); selectRow(i, h, j); } }, t.raw ?? '?')));
      tr.appendChild(el('td', { class: 'num', text: String((r.offset || 0) + i + 1) }));
      tr.appendChild(el('td', {}, h.artifact || shortId(h.artifact_id)));
      tr.appendChild(el('td', {}, h.line ? `${h.line.label}${h.line.surface ? ' · ' + h.line.surface : ''}` : '—'));
      tr.appendChild(el('td', {}, toks));
      tr.appendChild(el('td', { class: 'ids', text: h.start_position !== undefined ? `${h.start_position}–${h.end_position}` : (h.skipped ? `skip ${h.skipped}` : '') }));
      tr.appendChild(el('td', { class: 'ids', title: h.match_id, text: shortId(h.match_id) }));
      tbody.appendChild(tr);
    });
    const pager = clear($('#pager'));
    const from = r.rows.length ? (r.offset || 0) + 1 : 0, to = (r.offset || 0) + r.rows.length;
    pager.appendChild(el('span', { text: `Rows ${from}–${to} of ${fmt(r.count)}` }));
    pager.appendChild(el('div', { class: 'btns' },
      el('button', { disabled: !(r.offset > 0), onclick: () => runQuery(Math.max(0, r.offset - r.limit)) }, '← Prev'),
      el('button', { disabled: !r.truncated, onclick: () => runQuery(r.next_offset) }, 'Next →')));
  }
  function selectRow(i, h, j) {
    document.querySelectorAll('#results tbody tr').forEach((tr, k) => tr.classList.toggle('sel', k === i));
    state.selectedRow = h;
    openOccurrence(h.occurrence_ids[j] || h.occurrence_ids[0], h.occurrence_ids);
  }

  // ---------- occurrence detail ----------
  async function openOccurrence(id, windowIds) {
    const box = clear($('#detail'));
    box.appendChild(el('p', { class: 'muted', text: 'Loading occurrence…' }));
    try {
      const d = await api('/api/occurrence' + q({ dataset: state.ds, id }));
      renderOccurrence(d, windowIds || []);
    } catch (e) { clear(box).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  function renderOccurrence(d, windowIds) {
    const o = d.occurrence, box = clear($('#detail'));
    box.appendChild(el('h2', {}, el('span', {}, 'Occurrence ', el('span', { class: 'tok focus', text: o.raw })),
      el('button', { class: 'ghost', onclick: () => openGraph(o.id) }, 'Open in graph')));
    box.appendChild(kv([
      ['Reading', o.reading === null ? el('span', { class: 'muted', text: '∅ — blocking position (no reading identity)' }) : el('span', { class: 'mono', text: o.reading })],
      ['Kind', o.kind], ['Preservation', o.preservation], ['Certainty', o.certainty], ['Restoration', o.restoration_basis],
      ['Field', `${o.field} (${o.evidence_kind})`], ['Line', `${d.line.label}${d.line.surface ? ' · ' + d.line.surface : ''}${d.line.column_label ? ' · col ' + d.line.column_label : ''}`],
      ['Position', `${o.position} in line · ${o.sequence_position} in sequence`],
      ['Artifact', idLink(o.artifact_id, d.artifact_catalog_key || shortId(o.artifact_id))],
      ['Source record', idLink(o.source_record_id, `${d.source.source_key} (${d.source.locator || 'root'})`)],
    ]));
    box.appendChild(el('h3', { text: 'Line context' }));
    const ctx = el('div', { class: 'line-ctx' });
    for (const p of d.line_positions.rows) {
      ctx.appendChild(el('button', { class: tokenClass(p, windowIds.includes(p.id)) + (p.id === o.id ? ' focus' : ''), title: tokenTitle(p),
        onclick: () => openOccurrence(p.id, windowIds) }, p.raw));
    }
    box.appendChild(ctx);
    if (d.line_positions.truncated) box.appendChild(el('div', { class: 'hint', text: 'Line shows first 100 positions.' }));

    box.appendChild(el('h3', { text: 'Raw source evidence' }));
    box.appendChild(el('div', { class: 'evidence' }, d.excerpt.before, el('mark', { text: d.excerpt.token }), d.excerpt.after));
    if (d.trace) {
      const t = d.trace;
      box.appendChild(el('div', { class: 'check ok', text: `✓ Exact round-trip: UTF-8 bytes [${t.utf8_span[0]}, ${t.utf8_span[1]}) of “${t.decoded_field}” in the raw source decode to “${t.resolved_raw}”.` }));
      box.appendChild(kv([
        ['Raw SHA-256', el('span', { class: 'ids', text: t.raw_sha256 })], ['Record locator', t.record_locator || '(document root)'],
        ['Source key', t.source_key], ['Rights', `${t.rights.status}${t.rights.reuse ? ' — ' + t.rights.reuse : ''}`],
        ['Rights basis', t.rights_attribution]]));
    } else {
      box.appendChild(el('div', { class: 'check bad', text: '✗ Trace unavailable: ' + (d.trace_error || 'unknown') }));
    }
    if (d.sign) {
      box.appendChild(el('h3', { text: 'Sign mapping' }));
      box.appendChild(kv([['SignType', idLink(d.sign.id, d.sign.label)], ['Rule', d.sign.qualification.rule], ['ATF alignment', d.sign.qualification.alignment_to_atf], ['Sign list rev.', d.sign.qualification.sign_list_revision]]));
    }
    box.appendChild(el('h3', { text: `Translation context (${d.translations.length} in sequence · ${d.translation_total_in_record} in record)` }));
    if (d.translations.length) {
      box.appendChild(el('div', { class: 'callout', text: d.translation_note }));
      for (const t of d.translations.slice(0, 12)) {
        box.appendChild(el('div', { class: 'translation' }, el('div', { text: t.text }),
          el('div', { class: 'meta', text: `${t.language} · ${t.granularity} · ${t.locator}` })));
      }
    } else {
      box.appendChild(el('div', { class: 'missing', text: d.translation_total_in_record ? 'No translation placed in this sequence (the record has translations elsewhere).' : 'No translation supplied for this source record. Unavailable — not an empty translation.' }));
    }
    box.appendChild(el('div', { class: 'hint', text: d.word_tokens_in_sequence ? `${d.word_tokens_in_sequence} word tokens in sequence.` : 'Word / lemma / sense layers: unavailable for this source (no supplied rows; not zero linguistic words).' }));
    box.appendChild(el('h3', { text: `Indexed windows containing this position (${d.motifs.rows.length}${d.motifs.truncated ? '+' : ''})` }));
    const mw = el('div', { class: 'tags' });
    const seen = new Set();
    for (const m of d.motifs.rows) {
      const key = m.policy + ':' + JSON.stringify(m.readings);
      if (seen.has(key)) continue; seen.add(key);
      const pattern = m.readings.map(x => x === null ? '?' : x);
      mw.appendChild(el('button', { class: 'tok', title: `${m.policy} policy, length ${m.length}; click to query (blocking positions become ?)`,
        onclick: () => { setForm({ pattern, field: m.field, policy: m.policy, engine: 'index' }); runQuery(0); } },
        pattern.join(' '), el('small', { class: 'muted', text: ' ' + m.policy })));
    }
    box.appendChild(mw.childNodes.length ? mw : el('div', { class: 'hint', text: 'None (blocking position or no projection).' }));
    box.appendChild(el('h3', { text: 'Canonical IDs' }));
    box.appendChild(kv([['Occurrence', idLink(o.id, o.id)], ['Line', idLink(o.line_id, o.line_id)], ['Sequence', idLink(o.sequence_id, o.sequence_id)],
      ['Witness', idLink(o.witness_id, o.witness_id)], ['Snapshot', el('span', { class: 'ids', text: o.snapshot_id })]]));
  }

  // ---------- examples / saved / frequent ----------
  function renderExamples() {
    const ul = clear($('#examples'));
    const d = current();
    if (!d || !d.examples.length) { ul.appendChild(el('li', { class: 'muted', text: 'No built-in examples for this dataset.' })); }
    else for (const ex of d.examples) {
      ul.appendChild(el('li', {}, el('button', { class: 'ex', onclick: () => { setForm(ex); runQuery(0); } }, ex.label,
        ex.expect !== undefined ? el('span', { class: 'meta', text: `  expect ${ex.expect}` }) : null)));
    }
    renderSaved();
    renderFrequent();
  }
  function saved() { try { return JSON.parse(localStorage.getItem(SAVED_KEY) || '[]').filter(x => x && Array.isArray(x.pattern)); } catch (_) { return []; } }
  function renderSaved() {
    const ul = clear($('#saved'));
    const items = saved().filter(x => x.dataset === state.ds);
    if (!items.length) ul.appendChild(el('li', { class: 'muted', text: 'Nothing saved for this dataset yet.' }));
    items.forEach(ex => ul.appendChild(el('li', {}, el('button', { class: 'ex', onclick: () => { setForm(ex); runQuery(0); } },
      `${ex.pattern.join(' ')}`, el('span', { class: 'meta', text: `  ${ex.field}/${ex.policy}/${ex.engine}` })),
      el('button', { class: 'x ghost', title: 'Remove', onclick: () => { localStorage.setItem(SAVED_KEY, JSON.stringify(saved().filter(s => s !== ex && JSON.stringify(s) !== JSON.stringify(ex)))); renderSaved(); } }, '×'))));
  }
  async function renderFrequent() {
    const box = clear($('#frequent'));
    if (!state.ds) return;
    try {
      const t = await api('/api/top' + q({ dataset: state.ds, field: $('#field').value, policy: document.querySelector('input[name=policy]:checked').value }));
      if (!t.postings.length && !t.bigrams.length) { box.appendChild(el('span', { class: 'muted', text: `No indexed ${$('#field').value} postings in this dataset.` })); return; }
      for (const g of t.bigrams.slice(0, 8)) box.appendChild(el('button', { title: 'bigram', onclick: () => { $('#pattern').value = g.readings.join(' '); runQuery(0); } }, g.readings.join(' '), el('small', { text: String(g.n) })));
      for (const p of t.postings.slice(0, 12)) box.appendChild(el('button', { title: 'single reading', onclick: () => { $('#pattern').value = p.label; runQuery(0); } }, p.label, el('small', { text: String(p.n) })));
    } catch (e) { box.appendChild(el('span', { class: 'error', text: e.message })); }
  }

  // ---------- graph ----------
  async function startGraph() {
    if (!state.ds) return;
    try {
      const rep = state.report && state.report.dataset.key === state.ds ? state.report : await api('/api/report' + q({ dataset: state.ds }));
      state.report = rep;
      const entry = rep.entry_nodes.Artifact || rep.entry_nodes.SourceRecord || Object.values(rep.entry_nodes)[0];
      if (entry) focusNode(entry, true);
      else clear($('#gdetail')).appendChild(el('div', { class: 'missing', text: 'This dataset has no graph nodes.' }));
    } catch (e) { clear($('#gdetail')).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  function openGraph(id) { show('graph', true); focusNode(id); }
  async function focusNode(id, reset) {
    try {
      const data = await api('/api/neighborhood' + q({ dataset: state.ds, id, per: $('#gper').value }));
      if (reset) state.graph.history = [];
      if (state.graph.center && state.graph.center !== id && !reset) state.graph.history.push(state.graph.center);
      state.graph.center = id; state.graph.data = data; state.graph.selected = id;
      drawGraph();
      selectNode(data.nodes.find(n => n.id === id));
    } catch (e) { clear($('#gdetail')).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  async function expandNode(id) {
    const g = state.graph;
    try {
      const more = await api('/api/neighborhood' + q({ dataset: state.ds, id, per: $('#gper').value }));
      const ids = new Set(g.data.nodes.map(n => n.id));
      const merged = { ...g.data, nodes: [...g.data.nodes], links: [...g.data.links] };
      for (const n of more.nodes) {
        if (ids.has(n.id) || merged.nodes.length >= 150) continue;
        merged.nodes.push(n.kind === 'Aggregate' ? { ...n, id: n.id + '@' + id } : n); ids.add(n.kind === 'Aggregate' ? n.id + '@' + id : n.id);
      }
      const linkIds = new Set(merged.links.map(l => l.id));
      for (const l of more.links) {
        const fix = l.aggregate ? { ...l, id: l.id + '@' + id, source: l.source.startsWith('more:') ? l.source + '@' + id : l.source, target: l.target.startsWith('more:') ? l.target + '@' + id : l.target } : l;
        if (!linkIds.has(fix.id) && ids.has(fix.source) && ids.has(fix.target)) merged.links.push(fix);
      }
      merged.note = more.nodes.length + g.data.nodes.length > 150 ? 'View capped at 150 nodes; focus a node to explore further.' : g.data.note;
      g.data = merged; drawGraph();
    } catch (e) { clear($('#gdetail')).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  function drawGraph() {
    const g = state.graph;
    g.pos = GraphView.render($('#graph'), g.data, { selected: g.selected, previous: g.pos,
      onSelect: n => { g.selected = n.id; drawGraphSelection(); selectNode(n); }, onFocus: n => focusNode(n.id) });
    GraphView.legend($('#glegend'), [...new Set(g.data.nodes.map(n => n.kind))]);
    $('#gnote').textContent = `${g.data.nodes.length} nodes · ${g.data.links.length} links · ${g.data.note}${g.data.edges_truncated ? ' Incident edges truncated; counts are lower bounds.' : ''}`;
    $('#gback').disabled = !g.history.length;
    const crumbs = clear($('#gcrumbs'));
    [...g.history.slice(-4), g.center].forEach((id, i, arr) => crumbs.appendChild(el('button', { class: 'ghost', disabled: i === arr.length - 1, title: id, onclick: () => focusNode(id) }, shortId(id))));
  }
  function drawGraphSelection() {
    document.querySelectorAll('#graph .node').forEach(n => n.classList.toggle('sel', n.dataset.id === state.graph.selected));
  }
  async function selectNode(n) {
    const box = clear($('#gdetail'));
    if (!n) return;
    if (n.kind === 'Aggregate') {
      box.appendChild(el('h2', { text: n.label }));
      box.appendChild(el('p', { text: `${n.hidden}${n.lower_bound ? '+' : ''} more ${n.edge_kind} neighbors (${n.direction === 'out' ? 'outgoing' : 'incoming'}) are hidden to keep this view readable.` }));
      box.appendChild(el('p', { class: 'muted', text: 'Raise “Per type”, or focus a visible sibling. Aggregates are display groupings only; they assert nothing.' }));
      return;
    }
    box.appendChild(el('p', { class: 'muted', text: 'Loading…' }));
    try {
      const d = await api('/api/node' + q({ dataset: state.ds, id: n.id }));
      clear(box);
      box.appendChild(el('h2', {}, el('span', {}, el('span', { class: 'badge', text: d.kind }), ' ', d.label)));
      box.appendChild(el('div', { class: 'actions' },
        el('button', { class: 'primary', disabled: d.id === state.graph.center, onclick: () => focusNode(d.id) }, 'Focus'),
        el('button', { onclick: () => expandNode(d.id) }, 'Expand here'),
        d.kind === 'SignOccurrence' ? el('button', { onclick: () => { show('query'); openOccurrence(d.id, [d.id]); } }, 'Evidence panel') : null));
      box.appendChild(el('h3', { text: 'Properties' }));
      const pairs = [['ID', el('span', { class: 'ids', text: d.id })]];
      for (const [k, v] of Object.entries(d.props)) {
        if (k.endsWith('_id') && typeof v === 'string' && /^[a-z-]+:[0-9a-f]{32}$/.test(v)) pairs.push([k, idLink(v)]);
        else if (k === 'metadata' || k === 'details') { let pretty = v; try { pretty = JSON.stringify(JSON.parse(v), null, 1); } catch (_) { /* keep raw */ } pairs.push([k, el('pre', { class: 'cell', text: pretty })]); }
        else pairs.push([k, v]);
      }
      box.appendChild(kv(pairs));
      if (d.catalog_candidates) {
        box.appendChild(el('h3', { text: `Catalog candidates for ${d.props.object_key}` }));
        box.appendChild(el('div', { class: 'callout', text: d.candidate_note }));
        if (!d.catalog_candidates.length) box.appendChild(el('div', { class: 'missing', text: 'No artifact with this exact catalog key in this dataset (unresolved string).' }));
        for (const c of d.catalog_candidates) box.appendChild(el('div', {}, idLink(c.id, `${c.namespace}:${c.catalog_key}`), el('span', { class: 'ids', text: ' ' + c.snapshot_id })));
      }
      if (d.alias_claims && d.alias_claims.length) {
        box.appendChild(el('h3', { text: 'Alias claims made by this artifact’s source' }));
        for (const c of d.alias_claims) box.appendChild(el('div', {}, idLink(c.id, `${c.predicate} → ${c.object_key}`), el('span', { class: 'hint', text: ` ${c.status}` })));
      }
      if (d.incoming_alias_claims && d.incoming_alias_claims.length) {
        box.appendChild(el('h3', { text: 'Other sources’ claims pointing at this key' }));
        for (const c of d.incoming_alias_claims) box.appendChild(el('div', {}, idLink(c.id, `${c.namespace}: ${c.object_key}`), el('span', { class: 'hint', text: ` ${c.status}` })));
      }
      if (d.lines) {
        box.appendChild(el('h3', { text: `Lines (${d.lines.length}${d.lines.length >= 100 ? '+' : ''})` }));
        const t = el('div', { class: 'tags' });
        d.lines.forEach(l => t.appendChild(el('button', { class: 'tok', onclick: () => focusNode(l.id) }, l.label + (l.surface ? ' ' + l.surface : ''))));
        box.appendChild(t);
      }
      if (d.translations) {
        box.appendChild(el('h3', { text: `Translation context (${d.translations.length})` }));
        if (d.translations.length) box.appendChild(el('div', { class: 'callout', text: 'Placement only; span unresolved. Not word-level alignment.' }));
        d.translations.slice(0, 10).forEach(t => box.appendChild(el('div', { class: 'translation' }, el('div', { text: t.text }), el('div', { class: 'meta', text: `${t.language} · ${t.granularity}` }))));
        if (!d.translations.length) box.appendChild(el('div', { class: 'missing', text: 'No translation supplied for this sequence.' }));
      }
      if (d.positions) {
        box.appendChild(el('h3', { text: 'Positions' }));
        const t = el('div', { class: 'line-ctx' });
        d.positions.rows.forEach(p => t.appendChild(el('button', { class: tokenClass(p), title: tokenTitle(p), onclick: () => focusNode(p.id) }, p.raw)));
        box.appendChild(t);
      }
    } catch (e) { clear(box).appendChild(el('p', { class: 'error', text: e.message })); }
  }

  // ---------- coverage ----------
  function table(cols, rows, numeric) {
    const t = el('table', { class: 'plain' });
    t.appendChild(el('thead', {}, el('tr', {}, cols.map(c => el('th', { text: c[1] })))));
    const tb = el('tbody');
    for (const r of rows) tb.appendChild(el('tr', {}, cols.map(c => {
      const v = r[c[0]];
      return el('td', { class: (numeric && numeric.includes(c[0]) ? 'n' : '') + (v === 0 ? ' zero' : '') }, v instanceof Node ? v : (v === null || v === undefined ? '—' : String(v)));
    })));
    t.appendChild(tb);
    return t;
  }
  async function renderCoverage() {
    const box = clear($('#coverage'));
    if (!state.ds) { box.appendChild(el('div', { class: 'panel missing', text: 'No dataset ready.' })); return; }
    box.appendChild(el('div', { class: 'panel', text: 'Loading report…' }));
    try {
      const r = await api('/api/report' + q({ dataset: state.ds }));
      state.report = r;
      clear(box);
      const d = r.dataset;
      box.appendChild(el('div', { class: 'panel wide' }, el('h2', {}, el('span', { class: 'badge ' + d.kind, text: d.kind }), ' ', d.title),
        el('p', { text: d.description }), el('p', { class: 'muted', text: d.authority }),
        table([['source', 'Source'], ['file', 'Input'], ['bytes', 'Bytes'], ['sha256', 'SHA-256'], ['snapshot_id', 'Snapshot ID'], ['receipt', 'Receipt']], d.inputs, ['bytes']),
        d.manifest ? kv([['Rights', JSON.stringify(d.manifest.rights)], ['Imported', d.manifest.retrieved_or_imported_at], ['Coverage', d.manifest.coverage]]) : null));
      const unavailable = new Set(r.unavailable_layers);
      const counts = Object.entries(r.counts).map(([k, v]) => ({ layer: k, rows: v, state: unavailable.has(k) ? 'unavailable (not supplied)' : (v === 0 ? 'none' : '') }));
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Canonical layers' }), table([['layer', 'Table'], ['rows', 'Rows'], ['state', 'Note']], counts, ['rows']),
        el('p', { class: 'hint', text: `${fmt(r.nodes)} nodes · ${fmt(r.edges)} portable edges` })));
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Translation & linguistic coverage per source record' }),
        el('div', { class: 'callout', text: r.coverage_note }),
        r.coverage.length ? table([['source_record_id', 'Source record'], ['namespace', 'NS'], ['sequences', 'Seq'], ['words', 'Words'], ['lemmas', 'Lemmas'], ['translations', 'Transl.'], ['unresolved_translations', 'Unresolved']],
          r.coverage.map(c => ({ ...c, source_record_id: idLink(c.source_record_id) })), ['sequences', 'words', 'lemmas', 'translations', 'unresolved_translations']) : el('div', { class: 'missing', text: 'No source records.' })));
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Position states (why strict may be empty)' }),
        r.position_kinds.length ? table([['field', 'Field'], ['kind', 'Kind'], ['preservation', 'Preservation'], ['certainty', 'Certainty'], ['n', 'Positions']], r.position_kinds, ['n']) : el('div', { class: 'missing', text: 'No represented text positions in this dataset.' })));
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Graph shape' }),
        table([['k', 'Node kind'], ['n', 'Count']], Object.entries(r.node_kinds).map(([k, n]) => ({ k, n })), ['n']),
        el('h3', { text: 'Edge kinds' }), table([['k', 'Edge kind'], ['n', 'Count']], Object.entries(r.edge_kinds).map(([k, n]) => ({ k, n })), ['n'])));
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Exclusions & rejections' }),
        r.exclusions.length ? table([['reason', 'Exclusion reason'], ['n', 'Count']], r.exclusions, ['n']) : el('p', { class: 'muted', text: 'No exclusions.' }),
        el('h3', { text: 'Rejected records' }),
        r.rejections.length ? table([['locator', 'Locator'], ['reason', 'Reason']], r.rejections) : el('p', { class: 'muted', text: 'None.' })));
      const reg = r.registry;
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Projection registry' }),
        kv([['ID', el('span', { class: 'ids', text: reg.id })], ['Revision', reg.revision], ['Config', el('pre', { class: 'cell', text: reg.config })], ['Assets', reg.assets],
          ['Source hash', el('span', { class: 'ids', text: reg.source_hash })], ['Canonical hash', el('span', { class: 'ids', text: reg.canonical_hash })]])));
    } catch (e) { clear(box).appendChild(el('div', { class: 'panel error', text: e.message })); }
  }

  // ---------- FalkorDB ----------
  const SAMPLE_CYPHER = [
    ['Node kinds', 'MATCH (n) RETURN labels(n)[0] AS kind, count(*) AS n ORDER BY n DESC'],
    ['Frequent adjacent readings', 'MATCH (a:SignOccurrence)-[:NEXT]->(b:SignOccurrence)\nWHERE a.reading IS NOT NULL AND b.reading IS NOT NULL\nRETURN a.reading, b.reading, count(*) AS n ORDER BY n DESC LIMIT 20'],
    ['Artifact → lines', 'MATCH (w:Witness)-[:ON_ARTIFACT]->(a:Artifact), (w)-[:HAS_SEQUENCE]->(s:SequenceVersion)-[:HAS_LINE]->(l:Line)\nRETURN a.catalog_key, s.field, count(l) AS lines LIMIT 20'],
    ['Alias claims', 'MATCH (c:Claim)-[:CLAIM_SUBJECT]->(a:Artifact)\nRETURN a.catalog_key, c.object_key, c.status LIMIT 25'],
    ['Projection receipt', 'MATCH (p:ProjectionReceipt) RETURN p.export_id, p.dataset, p.receipt'],
  ];
  function loadReceipt(r) {
    return kv([['Graph', el('span', { class: 'mono', text: r.graph })], ['Status', r.reused ? 'already loaded (receipt + counts match)' : `loaded in ${r.seconds}s`],
          ['Nodes / edges', `${fmt(r.counts.nodes)} / ${fmt(r.counts.edges)}`], ['Export ID', r.receipt.export_id], ['Export dir', el('span', { class: 'ids', text: r.receipt.path })],
          ['nodes.jsonl', el('span', { class: 'ids', text: r.receipt.files['nodes.jsonl'] })], ['edges.jsonl', el('span', { class: 'ids', text: r.receipt.files['edges.jsonl'] })]]);
  }
  async function renderFalkor() {
    const box = clear($('#falkordb'));
    box.appendChild(el('div', { class: 'panel', text: 'Checking FalkorDB…' }));
    let st;
    try { st = await api('/api/falkordb/status'); } catch (e) { clear(box).appendChild(el('div', { class: 'panel error', text: e.message })); return; }
    state.falkor = st;
    clear(box);
    const d = current();
    const statusPanel = el('div', { class: 'panel' }, el('h2', {}, 'Isolated FalkorDB ', el('span', { class: 'pill ' + (st.reachable ? 'ok' : 'bad'), text: st.reachable ? 'running' : 'unreachable' })),
      kv([['Server', `redis://${st.host}:${st.port}`], ['Version', st.falkordb_version || '—'], ['Projection graphs', st.graphs ? String(st.graphs.length) : '—'],
        ['Other graphs', st.foreign_graphs ? String(st.foreign_graphs) + ' (not managed here)' : '0']]),
      st.reachable ? null : el('div', { class: 'missing', text: `Not reachable: ${st.error || ''}. Start it with bin/cuneiform-ui falkordb-up. Query and graph views do not need it.` }),
      el('h3', { text: 'FalkorDB Browser (official UI)' }),
      el('p', {}, el('a', { href: st.browser_url, target: '_blank', rel: 'noopener noreferrer', text: st.browser_url })),
      el('p', { class: 'hint', text: `Log in with host “${st.browser_login.host}”, port ${st.browser_login.port}, no username/password, then pick a cuneiform_* graph. ${st.browser_login.note}.` }),
      el('div', { class: 'callout', text: 'Everything in FalkorDB is a disposable projection exported from the selected dataset. The DuckDB/Parquet snapshot (or hashed in-memory input) remains authoritative. Writes made in the Browser affect only the projection; reload to restore it.' }));
    box.appendChild(statusPanel);
    const loadPanel = el('div', { class: 'panel' }, el('h2', { text: 'Projection for current dataset' }),
      d ? el('p', {}, el('span', { class: 'badge ' + d.kind, text: d.kind }), ' ', d.title) : null);
    const out = el('div');
    loadPanel.appendChild(el('button', { class: 'primary', disabled: !st.reachable || !d, onclick: async ev => {
      ev.target.disabled = true; clear(out).appendChild(el('p', { class: 'muted', text: 'Exporting and loading…' }));
      try {
        const r = await api('/api/falkordb/load', { dataset: state.ds });
        state.lastLoad = r;
        clear(out).appendChild(loadReceipt(r));
        setTimeout(renderFalkor, 600);
      } catch (e) { clear(out).appendChild(el('p', { class: 'error', text: e.message })); ev.target.disabled = false; }
    } }, 'Export & load into FalkorDB'));
    if (state.lastLoad && state.lastLoad.receipt.dataset === state.ds) out.appendChild(loadReceipt(state.lastLoad));
    loadPanel.appendChild(el('p', { class: 'hint', text: 'Writes content-addressed nodes/edges JSONL + receipt under ~/.local/share/cuneiform-ui/exports (write-once, hash-checked), then (re)builds graph cuneiform_<dataset>_<export>. Same input ⇒ same export ID ⇒ reused.' }));
    loadPanel.appendChild(out);
    box.appendChild(loadPanel);
    const graphs = (st.graphs || []);
    const gp = el('div', { class: 'panel wide' }, el('h2', { text: 'Loaded projection graphs' }));
    if (!graphs.length) gp.appendChild(el('div', { class: 'missing', text: 'None loaded yet.' }));
    else gp.appendChild(table([['graph', 'Graph'], ['dataset', 'Dataset'], ['counts', 'Nodes / edges'], ['backend', 'Backend'], ['act', '']], graphs.map(g => ({
      graph: el('span', { class: 'mono', text: g.graph }), dataset: g.receipt ? `${g.receipt.dataset} (${g.receipt.dataset_kind})` : 'no receipt',
      counts: g.receipt ? `${fmt(g.receipt.counts.nodes)} / ${fmt(g.receipt.counts.edges)}` : '—',
      backend: g.receipt ? `${(g.receipt.backend.commit || '').slice(0, 7)} ${g.receipt.backend.versions.parser}` : '—',
      act: el('span', {}, el('button', { onclick: () => { $('#cy-graph').value = g.graph; } }, 'Use'), ' ',
        el('button', { class: 'ghost', onclick: async () => { try { await api('/api/falkordb/drop', { graph: g.graph }); renderFalkor(); } catch (e) { alert(e.message); } } }, 'Drop'))
    }))));
    box.appendChild(gp);
    const con = el('div', { class: 'panel wide' }, el('h2', {}, 'Cypher console ', el('span', { class: 'pill warn', text: 'read-only · disposable projection · not canonical' })));
    const gsel = el('select', { id: 'cy-graph' }, graphs.map(g => el('option', { value: g.graph, text: g.graph })));
    const ta = el('textarea', { id: 'cy-q', spellcheck: 'false', maxlength: '4000' });
    ta.value = SAMPLE_CYPHER[1][1];
    const res = el('div');
    con.appendChild(el('label', { for: 'cy-graph', text: 'Graph' })); con.appendChild(gsel);
    con.appendChild(el('div', { class: 'tags', style: null }, SAMPLE_CYPHER.map(([n, c]) => el('button', { class: 'ghost', onclick: () => { ta.value = c; } }, n))));
    con.appendChild(el('label', { for: 'cy-q', text: 'Query (GRAPH.RO_QUERY, 3 s timeout, 500 rows shown)' })); con.appendChild(ta);
    con.appendChild(el('div', { class: 'actions' }, el('button', { class: 'primary', disabled: !graphs.length, onclick: async () => {
      clear(res).appendChild(el('p', { class: 'muted', text: 'Running…' }));
      try {
        const r = await api('/api/falkordb/query', { graph: gsel.value, query: ta.value });
        clear(res);
        res.appendChild(el('p', { class: 'hint', text: `${r.row_count} rows${r.truncated ? ' (first 500 shown)' : ''} · ${r.ms} ms · ${r.mode}` }));
        const cols = r.columns.map((c, i) => [String(i), String(c)]);
        res.appendChild(table(cols, r.rows.map(row => Object.fromEntries(row.map((v, i) => [String(i), typeof v === 'string' || typeof v === 'number' || v === null ? v : el('pre', { class: 'cell', text: JSON.stringify(v) })])))));
      } catch (e) { clear(res).appendChild(el('p', { class: 'error', text: e.message })); }
    } }, 'Run read-only query')));
    con.appendChild(res);
    box.appendChild(con);
  }

  // ---------- wiring ----------
  function onDatasetChange() {
    state.ds = $('#dataset').value;
    state.report = null; state.graph = { center: null, data: null, history: [], selected: null, pos: null };
    state.result = null;
    clear($('#results thead')); clear($('#results tbody')); clear($('#pager'));
    clear($('#qsummary')).appendChild(el('p', { class: 'muted', text: 'Pick an example or type a pattern.' }));
    clear($('#detail')).appendChild(el('p', { class: 'muted', text: 'Select a result row, then a token, to see occurrence detail and raw-source evidence.' }));
    clear($('#graph')); clear($('#gdetail'));
    renderDatasetBadge(); renderExamples(); setHash();
    const d = current();
    if (d && d.examples.length) setForm(d.examples[0]);
    if (state.view !== 'query') show(state.view);
  }
  async function init() {
    document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
    $('#dataset').addEventListener('change', onDatasetChange);
    $('#refresh').addEventListener('click', async () => { $('#refresh').disabled = true; try { await api('/api/refresh', {}); await loadStatus(); renderExamples(); } finally { $('#refresh').disabled = false; } });
    $('#qform').addEventListener('submit', ev => { ev.preventDefault(); runQuery(0); });
    $('#engine').addEventListener('change', syncSkip);
    $('#field').addEventListener('change', renderFrequent);
    document.querySelectorAll('input[name=policy]').forEach(r => r.addEventListener('change', renderFrequent));
    $('#save-query').addEventListener('click', () => {
      const v = formValues(); if (!v.pattern.length) return;
      const item = { dataset: state.ds, pattern: v.pattern, field: v.field, policy: v.policy, engine: v.engine, max_skip: v.max_skip };
      const all = saved().filter(s => JSON.stringify(s) !== JSON.stringify(item)); all.unshift(item);
      localStorage.setItem(SAVED_KEY, JSON.stringify(all.slice(0, 50))); renderSaved();
    });
    $('#gsearch').addEventListener('submit', async ev => {
      ev.preventDefault();
      const box = $('#gresults'); clear(box); box.hidden = false;
      try {
        const r = await api('/api/search' + q({ dataset: state.ds, q: $('#gq').value.trim() }));
        if (!r.rows.length) box.appendChild(el('div', { class: 'muted', text: 'No matches (searches artifact keys, sign labels, source keys, alias targets, exact IDs).' }));
        r.rows.forEach(n => box.appendChild(el('button', { onclick: () => { box.hidden = true; focusNode(n.id); } }, el('span', { class: 'badge', text: n.kind }), ' ', n.label, el('span', { class: 'ids', text: '  ' + shortId(n.id) }))));
      } catch (e) { box.appendChild(el('div', { class: 'error', text: e.message })); }
    });
    document.addEventListener('click', ev => { if (!ev.target.closest('#gresults') && !ev.target.closest('#gsearch')) $('#gresults').hidden = true; });
    $('#gper').addEventListener('change', () => state.graph.center && focusNode(state.graph.center, false));
    $('#gback').addEventListener('click', () => { const prev = state.graph.history.pop(); if (prev) { state.graph.center = null; focusNode(prev).then(() => { }); } });
    await loadStatus();
    const hashView = new URLSearchParams(location.hash.slice(1)).get('view');
    onDatasetChange();
    show(['query', 'graph', 'coverage', 'falkordb'].includes(hashView) ? hashView : 'query');
    if (current() && current().examples.length) runQuery(0);
    // Discover newly persisted snapshots without a restart.
    setInterval(async () => {
      try {
        const before = state.status.datasets.map(d => d.key + d.status).join();
        const fresh = await api('/api/refresh', {});
        const st = await api('/api/status');
        if (st.datasets.map(d => d.key + d.status).join() !== before) { state.status = st; renderStatus(); }
        void fresh;
      } catch (_) { /* server restarting */ }
    }, 30000);
  }
  init().catch(e => { document.body.appendChild(el('p', { class: 'error', text: 'Startup failed: ' + e.message })); });
})();
