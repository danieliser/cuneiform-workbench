/* Bounded neighborhood renderer: radial sectors per relationship type, light collision relaxation.
   All labels are set with textContent; nothing from the corpus is parsed as markup. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = {
    Artifact: '#35679b', Witness: '#617285', SequenceVersion: '#2d5b88', Line: '#4f86b8', SignOccurrence: '#647485',
    SignType: '#7b4f9d', TranslationSegment: '#2c7a4c', SourceRecord: '#5c5c5c', Claim: '#526d91', Composition: '#3d7f7a',
    Passage: '#3d7f7a', WordToken: '#586d82', Lemma: '#586d82', Sense: '#586d82', Aggregate: '#7a8590', '?': '#999'
  };
  const RADIUS = { Artifact: 15, SourceRecord: 13, SequenceVersion: 13, Witness: 12, Line: 11, Claim: 11, TranslationSegment: 11, SignType: 10, SignOccurrence: 9 };

  function svgEl(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
    if (parent) parent.appendChild(e);
    return e;
  }
  const short = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');

  function layout(data, prev) {
    const pos = new Map();
    const center = data.center;
    pos.set(center, { x: 0, y: 0, fixed: true });
    // Group neighbors by (edge kind, direction) for readable sectors.
    const groups = new Map();
    for (const l of data.links) {
      const other = l.source === center ? l.target : l.target === center ? l.source : null;
      if (!other) continue;
      const key = l.kind + (l.source === center ? ':out' : ':in');
      if (!groups.has(key)) groups.set(key, []);
      if (!groups.get(key).includes(other)) groups.get(key).push(other);
    }
    const total = Math.max(1, [...groups.values()].reduce((a, g) => a + g.length + 1.2, 0));
    let angle = -Math.PI / 2;
    for (const members of groups.values()) {
      const span = (2 * Math.PI) * (members.length + 1.2) / total;
      const ring = members.length > 10 ? 2 : 1;
      members.forEach((id, i) => {
        if (pos.has(id)) return;
        const r = 190 + (ring > 1 ? (i % 2) * 90 : 0) + Math.min(80, members.length * 4);
        const a = angle + span * (i + 0.6) / (members.length + 1.2);
        pos.set(id, { x: Math.cos(a) * r, y: Math.sin(a) * r });
      });
      angle += span;
    }
    // Nodes not adjacent to center (merged expansions) start near a linked placed node or a previous position.
    for (const n of data.nodes) {
      if (pos.has(n.id)) continue;
      const p = prev && prev.get(n.id);
      if (p) { pos.set(n.id, { x: p.x, y: p.y }); continue; }
      const link = data.links.find(l => (l.source === n.id && pos.has(l.target)) || (l.target === n.id && pos.has(l.source)));
      const anchor = link ? pos.get(link.source === n.id ? link.target : link.source) : { x: 0, y: 0 };
      const a = Math.random() * Math.PI * 2;
      pos.set(n.id, { x: anchor.x + Math.cos(a) * 120, y: anchor.y + Math.sin(a) * 120 });
    }
    // Collision relaxation + weak springs (bounded iterations, n <= ~150).
    const ids = [...pos.keys()];
    for (let it = 0; it < 160; it++) {
      for (let i = 0; i < ids.length; i++) {
        const a = pos.get(ids[i]);
        for (let j = i + 1; j < ids.length; j++) {
          const b = pos.get(ids[j]);
          let dx = b.x - a.x, dy = b.y - a.y; let d = Math.hypot(dx, dy) || 0.01;
          const min = 74;
          if (d < min) {
            const push = (min - d) / 2; dx /= d; dy /= d;
            if (!a.fixed) { a.x -= dx * push; a.y -= dy * push; }
            if (!b.fixed) { b.x += dx * push; b.y += dy * push; }
          }
        }
      }
      for (const l of data.links) {
        const a = pos.get(l.source), b = pos.get(l.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01;
        const want = 200, k = 0.012 * (d - want) / d;
        if (!a.fixed) { a.x += dx * k; a.y += dy * k; }
        if (!b.fixed) { b.x -= dx * k; b.y -= dy * k; }
      }
    }
    return pos;
  }

  function render(svg, data, opts) {
    opts = opts || {};
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const pos = layout(data, opts.previous);
    const xs = [...pos.values()].map(p => p.x), ys = [...pos.values()].map(p => p.y);
    const pad = 110;
    let view = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + 2 * pad, h: Math.max(...ys) - Math.min(...ys) + 2 * pad };
    const apply = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
    apply();
    const defs = svgEl('defs', {}, svg);
    const marker = svgEl('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 10, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
    svgEl('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#7a8590' }, marker);
    const root = svgEl('g', {}, svg);
    const edgeLayer = svgEl('g', {}, root), labelLayer = svgEl('g', {}, root), nodeLayer = svgEl('g', {}, root);
    const nodeById = new Map(data.nodes.map(n => [n.id, n]));
    const showEdgeLabels = data.links.length <= 90;
    const labelled = new Set();
    for (const l of data.links) {
      const a = pos.get(l.source), b = pos.get(l.target);
      if (!a || !b) continue;
      const rb = (RADIUS[(nodeById.get(l.target) || {}).kind] || 10) + 4;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      const line = svgEl('line', { x1: a.x, y1: a.y, x2: b.x - dx / d * rb, y2: b.y - dy / d * rb, class: 'edge' + (l.aggregate ? ' agg' : ''), 'marker-end': 'url(#arrow)' }, edgeLayer);
      const t = svgEl('title', {}, line); t.textContent = l.kind + (l.qualification && Object.keys(l.qualification).length ? ' ' + JSON.stringify(l.qualification) : '');
      const groupKey = l.kind + (l.source === data.center ? ':o' : l.target === data.center ? ':i' : ':' + l.source);
      if (showEdgeLabels && !l.aggregate && !labelled.has(groupKey)) {
        labelled.add(groupKey);
        const lab = svgEl('text', { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 3, 'text-anchor': 'middle', class: 'elabel' }, labelLayer);
        lab.textContent = l.kind;
      }
    }
    for (const n of data.nodes) {
      const p = pos.get(n.id); if (!p) continue;
      const g = svgEl('g', { class: 'node' + (n.id === data.center ? ' center' : '') + (n.id === opts.selected ? ' sel' : ''), transform: `translate(${p.x},${p.y})`, tabindex: 0, role: 'button' }, nodeLayer);
      g.dataset.id = n.id;
      const color = COLORS[n.kind] || COLORS['?'];
      if (n.kind === 'Aggregate') {
        svgEl('rect', { x: -12, y: -9, width: 24, height: 18, rx: 6, fill: color, 'stroke-dasharray': '3 2' }, g);
      } else {
        svgEl('circle', { r: (RADIUS[n.kind] || 10) + (n.id === data.center ? 5 : 0), fill: color }, g);
      }
      const text = svgEl('text', { y: (RADIUS[n.kind] || 10) + 17, 'text-anchor': 'middle' }, g);
      text.textContent = short(n.label || n.id, 26);
      const title = svgEl('title', {}, g); title.textContent = `${n.kind}: ${n.label || ''}\n${n.id}`;
      g.addEventListener('click', ev => { ev.stopPropagation(); opts.onSelect && opts.onSelect(n); });
      g.addEventListener('dblclick', ev => { ev.stopPropagation(); if (n.kind !== 'Aggregate') opts.onFocus && opts.onFocus(n); });
      g.addEventListener('keydown', ev => { if (ev.key === 'Enter') opts.onSelect && opts.onSelect(n); });
    }
    // Pan and zoom on the viewBox.
    let drag = null;
    svg.onpointerdown = ev => { if (ev.target === svg || ev.target.closest('g') === root) { drag = { x: ev.clientX, y: ev.clientY, v: { ...view } }; svg.setPointerCapture(ev.pointerId); } };
    svg.onpointermove = ev => {
      if (!drag) return;
      const s = view.w / svg.clientWidth;
      view.x = drag.v.x - (ev.clientX - drag.x) * s; view.y = drag.v.y - (ev.clientY - drag.y) * s; apply();
    };
    svg.onpointerup = () => { drag = null; };
    svg.onwheel = ev => {
      ev.preventDefault();
      const f = Math.exp(ev.deltaY * 0.0015), r = svg.getBoundingClientRect();
      const mx = view.x + (ev.clientX - r.left) / r.width * view.w, my = view.y + (ev.clientY - r.top) / r.height * view.h;
      view = { x: mx - (mx - view.x) * f, y: my - (my - view.y) * f, w: view.w * f, h: view.h * f }; apply();
    };
    return pos;
  }

  function legend(container, kinds) {
    while (container.firstChild) container.removeChild(container.firstChild);
    for (const k of kinds) {
      const s = document.createElement('span'); s.style.setProperty('--c', COLORS[k] || COLORS['?']); s.textContent = k; container.appendChild(s);
    }
  }

  window.GraphView = { render, legend, COLORS };
})();
