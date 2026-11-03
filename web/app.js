/* Cuneiform Workbench client. Corpus content is untrusted: DOM is built with
   createElement/textContent only (no innerHTML), and links come from the server's own config. */
(function () {
  'use strict';
  const $ = sel => document.querySelector(sel);
  const SAVED_KEY = 'cuneiform-ui:saved-queries';
  const state = { status: null, ds: null, version: null, view: 'query', lastQuery: null, result: null, selectedRow: null,
    graph: { center: null, data: null, history: [], selected: null, pos: null }, report: null, falkor: null,
    epoch: 0,        // bumped whenever dataset-bound views are reset; late responses from an older epoch are dropped
    stale: null };   // explicit stale state for the chosen dataset (review N-1): { key, version, reason }

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
    const epoch = state.epoch;
    const datasetBound = /[?&]dataset=/.test(path) || (body && typeof body === 'object' && 'dataset' in body);
    const res = await fetch(path, opts);
    let data;
    try { data = await res.json(); } catch (_) { data = { error: `HTTP ${res.status}` }; }
    // A response for a dataset view that was reset meanwhile must never render into the new view: drop it.
    if (datasetBound && epoch !== state.epoch) return new Promise(() => {});
    if (res.status === 409 && data.code === 'dataset-version-changed') {  // the version this view was built from is gone
      staleReset(data.error || 'dataset version changed');
      throw new Error('Results cleared: ' + (data.error || 'dataset version changed'));
    }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  const q = params => '?' + new URLSearchParams(params).toString();
  // Every dataset-scoped request names the exact dataset version the view was built from.
  const bound = extra => Object.assign({ dataset: state.ds, version: state.version || '' }, extra || {});
  async function staleReset(why) {
    // Keep the user's chosen dataset; mark its old version stale. Never switch to another dataset (review N-1).
    if (state.ds) state.stale = { key: state.ds, version: state.version, reason: why };
    state.version = null;
    try { state.status = await api('/api/status'); } catch (_) { /* keep old status */ }
    renderStatus(null, true);
  }

  // ---------- status / datasets ----------
  async function loadStatus() {
    state.status = await api('/api/status');
    renderStatus();
  }
  const statusSig = x => x.datasets.map(d => [d.key, d.status, d.version, d.reverifying, d.stale && d.stale.version].join('/')).join();
  async function pollStatus() {
    const st = await api('/api/status');
    const changed = statusSig(st) !== statusSig(state.status);
    state.status = st;
    if (changed) renderStatus();
  }
  function tagFor(d) {
    if (d.kind === 'snapshot') return 'SNAPSHOT' + (d.selection && d.selection.label ? ' · ' + d.selection.label : '');
    return d.kind === 'sample' ? 'SAMPLE' : 'SYNTHETIC';
  }
  function renderStatus(notice, forceReset) {
    const st = state.status;
    const b = st.backend;
    const chip = $('#backend-chip');
    chip.className = 'chip ' + (b.compatibility === 'tested' ? 'ok' : b.compatibility === 'incompatible' ? 'bad' : 'warn');
    chip.textContent = `backend ${b.commit ? b.commit.slice(0, 7) : '?'}${b.code_equivalent_to && b.code_equivalent_to !== b.commit ? ' (code = ' + b.code_equivalent_to.slice(0, 7) + ')' : ''}${b.dirty ? '+dirty code' : ''} · schema ${b.versions.schema} · ${b.versions.parser} · ${b.versions.projection} · ${b.compatibility}`;
    chip.title = b.repo + (b.problems.length ? '\n' + b.problems.join('\n') : '');
    const sel = clear($('#dataset'));
    const label = d => `[${tagFor(d)}] ${d.title}${d.status !== 'ready' ? ' — ' + d.status : d.reverifying ? ' — re-verifying' : ''}`;
    for (const d of st.datasets) {
      // The chosen dataset stays selected (shown) even while it is not ready, so the picker never jumps.
      sel.appendChild(el('option', { value: d.key, disabled: d.status !== 'ready' && d.key !== state.ds, text: label(d) }));
    }
    const ready = st.datasets.filter(d => d.status === 'ready');
    const keys = ready.map(d => d.key);
    const fromHash = new URLSearchParams(location.hash.slice(1)).get('ds');
    const before = { ds: state.ds, version: state.version };
    let cur = st.datasets.find(d => d.key === state.ds);
    const msgs = [];
    if (!state.ds) {
      // First load only: choose a default. After that the dataset changes only when the user picks one.
      state.ds = keys.includes(fromHash) ? fromHash : keys[0] || null;
      cur = st.datasets.find(d => d.key === state.ds);
      state.version = cur ? cur.version : null;
    } else if (!cur) {
      // Removed from disk: explicit stale state; nothing else is selected for the user.
      if (state.version) state.stale = { key: state.ds, version: state.version, reason: 'the snapshot was removed from the store' };
      state.version = null;
      sel.appendChild(el('option', { value: state.ds, disabled: true, text: `${state.ds} — removed` }));
    } else if (cur.status === 'ready') {
      if (state.version && cur.version !== state.version) {
        state.stale = null;
        msgs.push(`Dataset ${state.ds} now serves a new verified version (${String(cur.version).slice(0, 12)}); results from ${String(state.version).slice(0, 12)} were cleared.`);
      } else if (!state.version && state.stale && state.stale.key === state.ds) {
        msgs.push(`Dataset ${state.ds} verified again (version ${String(cur.version).slice(0, 12)}). Run your query again; earlier results stay cleared.`);
        state.stale = null;
      }
      state.version = cur.version;
    } else if (state.version) {
      // Not ready any more (changed on disk, failed or refused re-verification): results can no longer be verified.
      state.stale = { key: state.ds, version: state.version, reason: (cur.stale && cur.stale.reason) || cur.error || cur.status };
      state.version = null;
    } else if (!state.stale || state.stale.key !== state.ds) {
      state.stale = { key: state.ds, version: null, reason: cur.error || cur.status };
    }
    if (state.ds) sel.value = state.ds;
    const banner = $('#banner');
    // Review F-5 / N-1: a version change clears results; the dataset itself never switches by itself.
    const switched = before.ds !== null && (before.ds !== state.ds || before.version !== state.version);
    const reset = !!forceReset || switched;
    if (notice) msgs.push(notice);
    const sx = state.stale && state.stale.key === state.ds && !state.version ? state.stale : null;
    if (sx) msgs.push(`STALE — ${state.ds}${sx.version ? ' version ' + String(sx.version).slice(0, 12) : ''} is not being served: ${sx.reason}. `
      + 'Earlier results were cleared; nothing else was selected. It returns automatically if it verifies again, or pick another dataset.');
    else if (cur && cur.reverifying) msgs.push(`Re-verifying ${state.ds.slice(0, 21)}… in the background. It keeps serving version ${String(state.version).slice(0, 12)}; every request is still hash-checked.`);
    if (b.compatibility === 'incompatible') msgs.push('Backend is incompatible with this UI build: ' + b.problems.join('; '));
    if (b.code_changed_since_start) msgs.push((b.problems_runtime || []).join('; '));
    else if (b.compatibility !== 'tested') msgs.push(`Backend revision ${b.commit ? b.commit.slice(0, 7) : 'unknown'}${b.dirty ? ' (uncommitted changes)' : ''} has matching versions but was not the tested revision; results come from it unchanged.`);
    const scanned = st.last_discovery ? new Date(st.last_discovery * 1000).toLocaleTimeString() : 'never';
    if (!st.real_snapshots) msgs.push(`No persisted snapshots found under ${st.snapshot_dir}${st.snapshot_dir_exists ? '' : ' (directory not created yet)'} as of the last scan at ${scanned} — showing labeled samples and synthetic fixtures.`);
    const failing = st.datasets.filter(d => d.kind === 'snapshot' && d.status !== 'ready' && d.key !== state.ds);
    const pubTag = d => (d.publication && d.publication.status && d.publication.status !== 'not-a-snapshot' ? ` [${d.publication.status}]` : '');
    for (const d of failing) msgs.push(`Snapshot ${d.key.slice(0, 21)}…: ${d.status}${pubTag(d)}${d.error ? ' — ' + d.error : ''} (last scan ${scanned})`);
    if (cur && cur.status === 'ready' && cur.publication && cur.publication.status === 'legacy-incident') msgs.push(`Note for ${cur.key.slice(0, 21)}…: ${cur.publication.note}`);
    banner.hidden = !msgs.length;
    banner.className = 'banner' + (b.compatibility === 'incompatible' || sx ? ' bad' : '');
    clear(banner);
    msgs.forEach(m => banner.appendChild(el('div', { text: m })));
    $('#foot').textContent = `Server ${st.server.bind} · pid ${st.server.pid} · backend ${b.repo} · Python ${b.python} · DuckDB ${b.duckdb} · research root ${b.research_root} · last scan ${scanned}`;
    renderDatasetBadge();
    if (reset || notice) resetDatasetViews();
  }
  function current() { return state.status && state.status.datasets.find(d => d.key === state.ds); }
  function renderDatasetBadge() {
    const d = current(), badge = $('#dataset-badge');
    if (!d) { badge.textContent = state.ds ? 'stale · removed' : 'no data'; badge.className = 'badge' + (state.ds ? ' stale' : ''); return; }
    const pub = d.publication || {};
    if (d.status !== 'ready') {
      badge.textContent = `stale · ${d.status}`; badge.className = 'badge stale';
      badge.title = `Not served: ${d.error || d.status}`; return;
    }
    badge.textContent = (d.kind === 'snapshot' ? (pub.status === 'legacy-incident' ? 'verified · legacy ID incident' : `verified · ${pub.status || 'snapshot'}`)
      : d.kind === 'sample' ? 'in-memory sample' : 'synthetic') + (d.reverifying ? ' · re-verifying…' : '');
    badge.className = 'badge ' + d.kind + (pub.status === 'legacy-incident' ? ' incident' : '') + (d.reverifying ? ' pending' : '');
    badge.title = `${d.authority}\n${pub.note || ''}\n${(d.selection && d.selection.note) || ''}\nversion ${d.version || '—'}`;
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
    if (!state.version) {
      clear($('#qsummary')).appendChild(el('p', { class: 'error', text: `${state.ds} has no verified version being served right now (see the banner). Pick another dataset or wait for it to verify.` }));
      return;
    }
    const body = Object.assign(bound({ offset: offset || 0 }), v);
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
      : el('div', { class: 'count' }, fmt(r.count), el('small', { text: (r.kind === 'postings' ? 'postings' : 'matching windows') + (r.selection_label ? ` within ${r.selection_label}` : '') }));
    sum.appendChild(el('div', { style: null }, head));
    if (r.scope_note) sum.appendChild(el('div', { class: 'callout scope', text: 'Scope: ' + r.scope_note }));
    const ds = current() || { kind: r.dataset.kind, authority: '' };
    sum.appendChild(el('div', { class: 'summary-grid' },
      el('div', {}, el('b', { text: 'Pattern' }), el('span', { class: 'mono', text: r.pattern.join(' ') })),
      el('div', {}, el('b', { text: 'Field' }), r.field),
      el('div', {}, el('b', { text: 'Policy' }), r.policy),
      el('div', {}, el('b', { text: 'Engine' }), r.engine + (r.max_skip ? ` (skip ≤ ${r.max_skip})` : '')),
      el('div', {}, el('b', { text: 'Dataset' }), el('span', { class: 'badge ' + ds.kind, text: ds.kind }), ' ', el('span', { class: 'ids', title: 'dataset version this result is bound to', text: 'v ' + String(r.dataset.version || '').slice(0, 12) })),
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
      r.strict_note ? el('li', { text: r.strict_note }) : null,
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
    pager.appendChild(el('span', { text: `Rows ${from}–${to} of ${fmt(r.count)}` + (r.materialized_cap_reached ? ` (graph engine lists only the first ${fmt(r.retrievable)}; use Index to page the rest)` : '') }));
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
      const d = await api('/api/occurrence' + q(bound({ id })));
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

    box.appendChild(el('h3', { text: 'Parsed record field (canonical DB)' }));
    box.appendChild(el('div', { class: 'hint', text: d.excerpt.origin }));
    box.appendChild(el('div', { class: 'evidence' }, d.excerpt.before, el('mark', { text: d.excerpt.token }), d.excerpt.after));
    box.appendChild(el('h3', { text: 'Raw-byte trace' }));
    if (d.trace) {
      const t = d.trace;
      box.appendChild(el('div', { class: 'check ok', text: `✓ Exact round-trip in ${t.origin || 'raw source'}: UTF-8 bytes [${t.utf8_span[0]}, ${t.utf8_span[1]}) of field “${t.decoded_field}” decode to “${t.resolved_raw}”.` }));
      box.appendChild(kv([
        ['Raw SHA-256', el('span', { class: 'ids', text: t.raw_sha256 })], ['Record locator', t.record_locator || '(document root)'],
        ['Source key', t.source_key], ['Rights', `${t.rights.status}${t.rights.reuse ? ' — ' + t.rights.reuse : ''}`],
        ['Rights basis', t.rights_attribution]]));
      const pt = t.parent_trace;
      if (pt) {
        const ok = pt.status === 'verified';
        box.appendChild(el('div', { class: 'check ' + (ok ? 'ok' : 'bad'), text: ok
          ? `✓ Parent object verified: the same token re-read from the original parent file at absolute bytes [${pt.parent_file_byte_span[0]}, ${pt.parent_file_byte_span[1]}) (JSON-escaped form “${pt.parent_file_fragment}”).`
          : `✗ Parent object not re-read: ${pt.status}. Only the cohort payload slice was checked.` }));
        box.appendChild(kv([['Parent SHA-256', el('span', { class: 'ids', text: pt.parent_sha256 })], ['Parent URL', pt.parent_url],
          ['Parent receipt', pt.parent_receipt_id], ['Parent pointer', pt.parent_pointer],
          ['Record byte range in parent', `[${pt.parent_byte_range[0]}, ${pt.parent_byte_range[1]})`], ['Record SHA-256', el('span', { class: 'ids', text: pt.record_sha256 })]]));
      } else if (d.dataset.kind === 'snapshot') {
        box.appendChild(el('div', { class: 'hint', text: 'No parent trace: this snapshot is not a cohort of a larger parent object (the backend provides parent traces for eBL cohorts).' }));
      }
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
    box.appendChild(el('div', { class: 'hint', text: d.word_tokens_in_sequence ? `${d.word_tokens_in_sequence} word tokens in this sequence.`
      : d.word_tokens_in_dataset ? 'No word tokens are attached to this sequence (other sequences in this dataset have words). This is a per-sequence observation, not a source-level claim.'
      : 'Word / lemma / sense layers: unavailable in this dataset (no supplied rows; not zero linguistic words).' }));
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
      const t = await api('/api/top' + q(bound({ field: $('#field').value, policy: document.querySelector('input[name=policy]:checked').value })));
      if (!t.postings.length && !t.bigrams.length) { box.appendChild(el('span', { class: 'muted', text: `No indexed ${$('#field').value} postings in this dataset.` })); return; }
      for (const g of t.bigrams.slice(0, 8)) box.appendChild(el('button', { title: 'bigram', onclick: () => { $('#pattern').value = g.readings.join(' '); runQuery(0); } }, g.readings.join(' '), el('small', { text: String(g.n) })));
      for (const p of t.postings.slice(0, 12)) box.appendChild(el('button', { title: 'single reading', onclick: () => { $('#pattern').value = p.label; runQuery(0); } }, p.label, el('small', { text: String(p.n) })));
    } catch (e) { box.appendChild(el('span', { class: 'error', text: e.message })); }
  }

  // ---------- graph ----------
  async function startGraph() {
    if (!state.ds) return;
    try {
      const rep = state.report && state.report.dataset.key === state.ds && state.report.dataset.version === state.version ? state.report : await api('/api/report' + q(bound()));
      state.report = rep;
      const entry = rep.entry_nodes.Artifact || rep.entry_nodes.SourceRecord || Object.values(rep.entry_nodes)[0];
      if (entry) focusNode(entry, true);
      else clear($('#gdetail')).appendChild(el('div', { class: 'missing', text: 'This dataset has no graph nodes.' }));
    } catch (e) { clear($('#gdetail')).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  function openGraph(id) { show('graph', true); focusNode(id); }
  async function focusNode(id, reset) {
    try {
      const data = await api('/api/neighborhood' + q(bound({ id, per: $('#gper').value })));
      if (reset) state.graph.history = [];
      if (state.graph.center && state.graph.center !== id && !reset) state.graph.history.push(state.graph.center);
      state.graph.center = id; state.graph.data = data; state.graph.selected = id;
      drawGraph();
      selectNode(data.nodes.find(n => n.id === id));
    } catch (e) { graphError(e); }
  }
  async function expandNode(id) {
    const g = state.graph;
    try {
      const more = await api('/api/neighborhood' + q(bound({ id, per: $('#gper').value })));
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
    } catch (e) { graphError(e); }
  }
  function graphError(e) {  // visible in both the graph note and the detail panel (review F-6)
    $('#gnote').textContent = 'Graph request failed: ' + e.message;
    $('#gnote').className = 'gnote error';
    clear($('#gdetail')).appendChild(el('p', { class: 'error', text: e.message }));
  }
  function drawGraph() {
    const g = state.graph;
    g.pos = GraphView.render($('#graph'), g.data, { selected: g.selected, previous: g.pos,
      onSelect: n => { g.selected = n.id; drawGraphSelection(); selectNode(n); }, onFocus: n => focusNode(n.id) });
    GraphView.legend($('#glegend'), [...new Set(g.data.nodes.map(n => n.kind))]);
    $('#gnote').className = 'gnote muted';
    $('#gnote').textContent = `${g.data.nodes.length} nodes · ${g.data.links.length} links · ${g.data.note}`;
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
      box.appendChild(el('p', { text: `${n.hidden} more ${n.edge_kind} neighbors (${n.direction === 'out' ? 'outgoing' : 'incoming'}; ${n.total} in total, exact) are hidden to keep this view readable.` }));
      box.appendChild(el('p', { class: 'muted', text: 'Raise “Per type”, or focus a visible sibling. Aggregates are display groupings only; they assert nothing.' }));
      return;
    }
    box.appendChild(el('p', { class: 'muted', text: 'Loading…' }));
    try {
      const d = await api('/api/node' + q(bound({ id: n.id })));
      clear(box);
      box.appendChild(el('h2', {}, el('span', {}, el('span', { class: 'badge', text: d.kind }), ' ', d.label)));
      box.appendChild(el('div', { class: 'actions' },
        el('button', { class: 'primary', disabled: d.id === state.graph.center, onclick: () => focusNode(d.id) }, 'Focus'),
        el('button', { onclick: () => expandNode(d.id) }, 'Expand here'),
        d.kind === 'SignOccurrence' ? el('button', { onclick: () => { show('query'); openOccurrence(d.id, [d.id]); } }, 'Evidence panel') : null,
        d.kind === 'WordToken' ? el('button', { onclick: () => { show('query'); openWord(d.id); } }, 'Word trace') : null));
      if (d.gloss_note) box.appendChild(el('div', { class: 'callout', text: d.gloss_note }));
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
  function renderCoveragePage(box, r) {
    clear(box);
    const pager = () => el('div', { class: 'pager' },
      el('span', { class: 'hint', text: `Showing ${r.coverage_shown}${r.coverage_truncated ? ` (${r.coverage_page_size} per page)` : ''}` }), ' ',
      r.coverage_prev_offset !== null && r.coverage_prev_offset !== undefined ? el('button', { class: 'ghost', onclick: () => coveragePage(box, r.coverage_prev_offset) }, '← Previous') : null, ' ',
      r.coverage_next_offset !== null && r.coverage_next_offset !== undefined ? el('button', { class: 'ghost', onclick: () => coveragePage(box, r.coverage_next_offset) }, `Next ${Math.min(r.coverage_page_size, r.coverage_total - r.coverage_next_offset)} →`) : null);
    box.appendChild(el('div', { class: 'count-line', text: `${fmt(r.coverage_total)} source records in this dataset` }));
    if (r.coverage_truncated) box.appendChild(pager());
    box.appendChild(r.coverage.length ? table([['source_record_id', 'Source record'], ['namespace', 'NS'], ['sequences', 'Seq'], ['words', 'Words'], ['lemmatized_words', 'Lemmatized'], ['words_with_sense', 'With sense gloss'], ['translations', 'Transl.'], ['unresolved_translations', 'Unresolved']],
      r.coverage.map(c => ({ ...c, source_record_id: idLink(c.source_record_id) })), ['sequences', 'words', 'lemmatized_words', 'words_with_sense', 'translations', 'unresolved_translations']) : el('div', { class: 'missing', text: 'No source records.' }));
    if (r.coverage_truncated) box.appendChild(pager());
  }
  async function coveragePage(box, offset) {
    clear(box).appendChild(el('p', { class: 'muted', text: 'Loading records…' }));
    try { renderCoveragePage(box, await api('/api/coverage' + q(bound({ offset })))); }
    catch (e) { clear(box).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  async function renderCoverage() {
    const box = clear($('#coverage'));
    if (!state.ds) { box.appendChild(el('div', { class: 'panel missing', text: 'No dataset ready.' })); return; }
    if (!state.version) { box.appendChild(el('div', { class: 'panel missing', text: `${state.ds} is not being served right now (see the banner); its report is unavailable.` })); return; }
    box.appendChild(el('div', { class: 'panel', text: 'Loading report…' }));
    try {
      const r = await api('/api/report' + q(bound()));
      state.report = r;
      clear(box);
      const d = r.dataset, sel = d.selection || {}, pub = d.publication || {};
      box.appendChild(el('div', { class: 'panel wide' }, el('h2', {}, el('span', { class: 'badge ' + d.kind, text: tagFor(d) }), ' ', d.title),
        el('div', { class: 'callout scope', text: 'Scope: ' + (sel.note || '—') }), el('p', { class: 'muted', text: d.authority }),
        kv([['Dataset version', el('span', { class: 'ids', text: d.version || '—' })], ['Verified at', d.verified_at ? new Date(d.verified_at * 1000).toLocaleString() : '—'],
          ['Publication', `${pub.status || '—'}${pub.note ? ' — ' + pub.note : ''}`]]),
        table([['source', 'Source'], ['file', 'Input'], ['bytes', 'Bytes'], ['sha256', 'SHA-256'], ['snapshot_id', 'Snapshot ID'], ['source_url', 'Source URL'], ['receipt', 'Receipt']], d.inputs, ['bytes']),
        d.manifest ? kv([['Rights', JSON.stringify(d.manifest.rights)], ['Imported', d.manifest.retrieved_or_imported_at], ['Coverage', d.manifest.coverage],
          ['Manifest SHA-256', el('span', { class: 'ids', text: d.manifest.manifest_sha256 || '—' })]]) : null));
      if (sel.kind === 'cohort' || sel.kind === 'selection') {
        const pairs = [['Selection', sel.label], ['Kind', sel.backend_kind || sel.kind], ['Rule', sel.rule_name || '—']];
        if (sel.kind === 'cohort') pairs.push(['Cohort size', fmt(sel.cohort_size)], ['Eligible records', fmt(sel.eligible_records)], ['Parent records', fmt(sel.parent_records)],
          ['Selector', sel.selector], ['Provenance', sel.provenance], ['Exclusions (parent)', el('pre', { class: 'cell', text: JSON.stringify(sel.excluded_by_reason, null, 1) })],
          ['Rule detail', el('pre', { class: 'cell', text: JSON.stringify(sel.rule, null, 1) })]);
        else pairs.push(['Selected', fmt(sel.selected)]);
        box.appendChild(el('div', { class: 'panel wide' }, el('h2', { text: 'Population these counts describe' }), kv(pairs)));
      }
      const counts = r.layers;
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Canonical layers' }), table([['layer', 'Table'], ['rows', 'Rows'], ['state', 'Note']], counts, ['rows']),
        el('p', { class: 'hint', text: `${fmt(r.nodes)} nodes · ${fmt(r.edges)} portable edges` })));
      const covBox = el('div');
      box.appendChild(el('div', { class: 'panel' }, el('h2', { text: 'Translation & linguistic coverage per source record' }),
        el('div', { class: 'callout', text: r.coverage_note }),
        el('div', { class: 'hint', text: 'Sense glosses (words with sense) are lexical labels for single words, not translations. Translations are separate strings with unresolved spans.' }),
        covBox));
      renderCoveragePage(covBox, r);
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
  function loadReceipt(r) {
    return kv([['Graph', el('span', { class: 'mono', text: r.graph })], ['Status', `rebuilt in ${r.seconds}s; contents read back and ${r.content_verified ? 'verified equal to the export ✓' : 'NOT verified'}`],
          ['Nodes / edges', `${fmt(r.counts.nodes)} / ${fmt(r.counts.edges)}`], ['Dataset version', el('span', { class: 'ids', text: r.receipt.dataset_version || '—' })],
          ['Export ID', r.receipt.export_id], ['Content digest', el('span', { class: 'ids', text: r.content_digest })], ['Export dir', el('span', { class: 'ids', text: r.receipt.path })],
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
    box.appendChild(el('div', { class: 'panel' }, el('h2', {}, 'Isolated FalkorDB ', el('span', { class: 'pill ' + (st.reachable ? 'ok' : 'bad'), text: st.reachable ? 'running' : 'unreachable' })),
      kv([['Server', `redis://${st.host}:${st.port} (loopback relay; ACL login required)`], ['Version', st.falkordb_version || '—'], ['Isolation', st.isolation],
        ['Projection graphs', st.graphs ? String(st.graphs.length) : '—'], ['Other graphs', st.foreign_graphs ? String(st.foreign_graphs) + ' (not managed here)' : '0']]),
      st.reachable ? null : el('div', { class: 'missing', text: `Not reachable: ${st.error || ''} Query, evidence and graph views do not need it.` }),
      el('h3', { text: 'FalkorDB Browser' }), el('div', { class: 'missing', text: st.browser.reason }),
      el('h3', { text: 'Cypher' }), el('div', { class: 'missing', text: st.console.reason }),
      el('div', { class: 'callout', text: 'Everything in FalkorDB is a disposable projection exported from one dataset version. The DuckDB/Parquet snapshot (or hashed in-memory input) remains authoritative. Every load rebuilds the graph and reads it back to prove its contents equal the export.' })));
    const loadPanel = el('div', { class: 'panel' }, el('h2', { text: 'Projection for current dataset' }),
      d ? el('p', {}, el('span', { class: 'badge ' + d.kind, text: tagFor(d) }), ' ', d.title, ' ', el('span', { class: 'ids', text: 'v ' + String(d.version || '').slice(0, 12) })) : null);
    const out = el('div');
    loadPanel.appendChild(el('button', { class: 'primary', disabled: !st.reachable || !d, onclick: async ev => {
      ev.target.disabled = true; clear(out).appendChild(el('p', { class: 'muted', text: 'Exporting, rebuilding and verifying…' }));
      try {
        const r = await api('/api/falkordb/load', bound());
        state.lastLoad = r;
        clear(out).appendChild(loadReceipt(r));
        setTimeout(renderFalkor, 600);
      } catch (e) { clear(out).appendChild(el('p', { class: 'error', text: e.message })); ev.target.disabled = false; }
    } }, 'Export, rebuild & verify'));
    if (state.lastLoad && state.lastLoad.receipt.dataset === state.ds && state.lastLoad.receipt.dataset_version === state.version) out.appendChild(loadReceipt(state.lastLoad));
    loadPanel.appendChild(el('p', { class: 'hint', text: 'Writes content-addressed nodes/edges JSONL + receipt under the UI runtime exports directory (write-once, hash-checked), deletes and rebuilds graph cuneiform_<dataset>_<export>, then compares every node and edge read back from FalkorDB with the export.' }));
    loadPanel.appendChild(out);
    box.appendChild(loadPanel);
    const graphs = (st.graphs || []);
    const gp = el('div', { class: 'panel wide' }, el('h2', { text: 'Loaded projection graphs' }));
    const vres = el('div');
    if (!graphs.length) gp.appendChild(el('div', { class: 'missing', text: 'None loaded yet.' }));
    else gp.appendChild(table([['graph', 'Graph'], ['dataset', 'Dataset'], ['version', 'Version'], ['counts', 'Nodes / edges'], ['backend', 'Backend'], ['act', '']], graphs.map(g => ({
      graph: el('span', { class: 'mono', text: g.graph }), dataset: g.receipt ? `${g.receipt.dataset} (${g.receipt.dataset_kind})` : 'no receipt',
      version: g.receipt ? String(g.receipt.dataset_version || 'unrecorded').slice(0, 12) : '—',
      counts: g.receipt ? `${fmt(g.receipt.counts.nodes)} / ${fmt(g.receipt.counts.edges)}` : '—',
      backend: g.receipt ? `${(g.receipt.backend.commit || '').slice(0, 7)} ${g.receipt.backend.versions.parser}` : '—',
      act: el('span', {}, el('button', { onclick: () => { $('#cy-graph').value = g.graph; } }, 'Use'), ' ',
        g.verifiable === false ? el('span', { class: 'pill warn', title: g.verify_note || '', text: g.verify_status === 'unsupported-format' ? 'old format · rebuild required' : 'rebuild required' })
        : el('button', { class: 'ghost', onclick: async () => {
          clear(vres).appendChild(el('p', { class: 'muted', text: 'Reading back ' + g.graph + '…' }));
          try { const v = await api('/api/falkordb/verify', { graph: g.graph });
            clear(vres).appendChild(el('p', { class: v.content_verified ? 'check ok' : 'check bad', text: v.content_verified
              ? `✓ ${g.graph}: contents equal the export (${fmt(v.counts.nodes)} nodes / ${fmt(v.counts.edges)} edges; compared ${v.compared}).`
              : `✗ ${g.graph} [${v.status}]: ${v.problems.join('; ')}${v.rebuild_required ? '' : '. Rebuild it.'}` }));
          } catch (e) { clear(vres).appendChild(el('p', { class: 'error', text: e.message })); } } }, 'Verify'), ' ',
        el('button', { class: 'ghost', onclick: async () => { try { await api('/api/falkordb/drop', { graph: g.graph }); renderFalkor(); } catch (e) { alert(e.message); } } }, 'Drop'))
    }))));
    gp.appendChild(vres);
    box.appendChild(gp);
    // Review F-14: preselect the current dataset version's projection and echo the graph in every result.
    const mine = graphs.filter(g => g.receipt && g.receipt.dataset === state.ds && g.receipt.dataset_version === state.version);
    const con = el('div', { class: 'panel wide' }, el('h2', {}, 'Read-only templates ', el('span', { class: 'pill warn', text: 'allowlisted · bounded · disposable projection · not canonical' })));
    const gsel = el('select', { id: 'cy-graph' }, graphs.map(g => el('option', { value: g.graph, text: g.graph + (mine.includes(g) ? '  (current dataset)' : '') })));
    if (mine.length) gsel.value = mine[0].graph;
    const tsel = el('select', { id: 'cy-template' }, st.templates.map(t => el('option', { value: t.id, text: t.label })));
    const pbox = el('div', { class: 'row2' });
    const lim = el('input', { id: 'cy-limit', type: 'number', min: '1', max: '500', value: '50' });
    const syncParams = () => {
      clear(pbox);
      const t = st.templates.find(x => x.id === tsel.value);
      for (const [name, typ] of Object.entries(t.params)) pbox.appendChild(el('div', {}, el('label', { for: 'cy-p-' + name, text: `${name} (${typ === 'node' ? 'node ID' : 'reading'})` }),
        el('input', { id: 'cy-p-' + name, 'data-param': name, maxlength: '64', spellcheck: 'false' })));
    };
    tsel.addEventListener('change', syncParams); syncParams();
    const res = el('div');
    con.appendChild(el('label', { for: 'cy-graph', text: 'Graph' })); con.appendChild(gsel);
    if (!mine.length && graphs.length) con.appendChild(el('div', { class: 'hint', text: 'No projection of the current dataset version is loaded; the graphs listed belong to other datasets or versions.' }));
    con.appendChild(el('label', { for: 'cy-template', text: 'Template' })); con.appendChild(tsel);
    con.appendChild(pbox);
    con.appendChild(el('label', { for: 'cy-limit', text: 'Row limit (1–500)' })); con.appendChild(lim);
    con.appendChild(el('div', { class: 'actions' }, el('button', { class: 'primary', disabled: !graphs.length, onclick: async () => {
      clear(res).appendChild(el('p', { class: 'muted', text: 'Running…' }));
      const params = {};
      pbox.querySelectorAll('input[data-param]').forEach(i => { params[i.dataset.param] = i.value.trim(); });
      try {
        const r = await api('/api/falkordb/template', { graph: gsel.value, template: tsel.value, params, limit: Number(lim.value) });
        clear(res);
        res.appendChild(el('p', { class: 'hint', text: `${r.graph} · ${r.label} · ${r.row_count} rows${r.truncated ? ` (more exist beyond the limit of ${r.limit})` : ' (complete)'} · ${r.ms} ms · ${r.mode}` }));
        const cols = r.columns.map((c, i) => [String(i), String(c)]);
        res.appendChild(table(cols, r.rows.map(row => Object.fromEntries(row.map((v, i) => [String(i), typeof v === 'string' || typeof v === 'number' || v === null ? v : el('pre', { class: 'cell', text: JSON.stringify(v) })])))));
      } catch (e) { clear(res).appendChild(el('p', { class: 'error', text: e.message })); }
    } }, 'Run template')));
    con.appendChild(res);
    box.appendChild(con);
  }

  // ---------- ORACC words: gloss is not translation ----------
  async function openWord(id) {
    const box = clear($('#detail'));
    box.appendChild(el('p', { class: 'muted', text: 'Loading word…' }));
    try {
      const d = await api('/api/word' + q(bound({ id })));
      clear(box);
      const w = d.word;
      box.appendChild(el('h2', {}, el('span', {}, 'Word ', el('span', { class: 'tok focus', text: w.form || '∅' }))));
      box.appendChild(el('div', { class: 'callout', text: d.gloss_note }));
      box.appendChild(kv([['Citation form', w.citation_form], ['Language', w.language], ['POS', w.pos], ['Guideword', w.guideword],
        ['Sense gloss', w.sense_gloss === null || w.sense_gloss === undefined ? el('span', { class: 'muted', text: 'none supplied' }) : `${w.sense_gloss} — ${w.gloss_status}`],
        ['Word ID', idLink(w.word_id, w.word_id)], ['Source record', idLink(w.source_record_id)]]));
      box.appendChild(el('h3', { text: 'Backend word trace' }));
      if (d.trace) {
        const t = d.trace, pt = t.parent_trace || {};
        box.appendChild(el('div', { class: 'check ok', text: `✓ Word → lemma → sense re-resolved in the snapshot payload member ${t.member} at ${t.payload_pointer}.` }));
        box.appendChild(el('div', { class: 'check ' + (pt.status === 'verified' ? 'ok' : 'bad'), text: pt.status === 'verified'
          ? `✓ Parent ZIP member ${pt.member} is byte-identical to the payload member (zip ${String(pt.zip_sha256).slice(0, 16)}…, CRC ${pt.crc32}).` : `✗ Parent ZIP not re-read: ${pt.status || 'not requested'}.` }));
        box.appendChild(kv([['Text ref', t.ref], ['Line', t.line_label], ['Source word ID', t.source_word_id], ['Gloss status', t.gloss_status],
          ['Rights', `${t.rights.status}${t.rights.reuse ? ' — ' + t.rights.reuse : ''}`]]));
      } else box.appendChild(el('div', { class: 'check bad', text: '✗ ' + (d.trace_error || 'no trace') }));
    } catch (e) { clear(box).appendChild(el('p', { class: 'error', text: e.message })); }
  }
  async function wordSearch(ev) {
    ev.preventDefault();
    const box = clear($('#wresults'));
    const text = $('#wq').value.trim();
    if (!text || !state.ds) return;
    try {
      const r = await api('/api/words' + q(bound({ q: text })));
      box.appendChild(el('div', { class: 'hint', text: r.gloss_note }));
      if (!r.rows.length) { box.appendChild(el('div', { class: 'muted', text: 'No word rows match (or this dataset has no word layer).' })); return; }
      for (const w of r.rows) box.appendChild(el('button', { class: 'ex', onclick: () => openWord(w.word_id) }, `${w.form} → ${w.citation_form || '∅'}`,
        el('span', { class: 'meta', text: `  gloss: ${w.sense_gloss ?? '—'}` })));
      if (r.truncated) box.appendChild(el('div', { class: 'hint', text: 'First 50 shown.' }));
    } catch (e) { box.appendChild(el('div', { class: 'error', text: e.message })); }
  }

  // ---------- wiring ----------
  function resetDatasetViews() {
    state.epoch += 1;
    state.report = null; state.graph = { center: null, data: null, history: [], selected: null, pos: null };
    state.result = null; state.lastQuery = null;
    clear($('#results thead')); clear($('#results tbody')); clear($('#pager'));
    clear($('#qsummary')).appendChild(el('p', { class: 'muted', text: 'Pick an example or type a pattern.' }));
    clear($('#detail')).appendChild(el('p', { class: 'muted', text: 'Select a result row, then a token, to see occurrence detail and raw-source evidence.' }));
    clear($('#graph')); clear($('#gdetail')); clear($('#wresults'));
    $('#gnote').textContent = '';
    renderDatasetBadge(); renderExamples(); setHash();
    const d = current();
    if (d && d.examples.length) setForm(d.examples[0]);
    if (state.view !== 'query') show(state.view);
  }
  function onDatasetChange() {
    state.ds = $('#dataset').value || null;
    const d = current();
    state.version = d && d.status === 'ready' ? d.version : null;
    if (state.stale && state.stale.key !== state.ds) state.stale = null;
    resetDatasetViews();
  }
  async function init() {
    document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
    $('#dataset').addEventListener('change', onDatasetChange);
    $('#refresh').addEventListener('click', async () => {
      $('#refresh').disabled = true;
      try {
        await api('/api/refresh', { deep: true });
        // Poll quickly while the re-check runs, so the pending -> done (or stale) transition shows promptly.
        for (let i = 0; i < 120; i++) {
          await pollStatus();
          if (!state.status.datasets.some(d => d.reverifying || d.status === 'verifying')) break;
          await new Promise(r => setTimeout(r, 1000));
        }
      } catch (e) { renderStatus('Refresh failed: ' + e.message); } finally { $('#refresh').disabled = false; }
    });
    $('#qform').addEventListener('submit', ev => { ev.preventDefault(); runQuery(0); });
    $('#wform').addEventListener('submit', wordSearch);
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
        const r = await api('/api/search' + q(bound({ q: $('#gq').value.trim() })));
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
    // Poll status only; the server scans in the background (an open tab no longer triggers discovery).
    setInterval(() => pollStatus().catch(() => { /* server restarting */ }), 30000);
  }
  init().catch(e => { document.body.appendChild(el('p', { class: 'error', text: 'Startup failed: ' + e.message })); });
})();
