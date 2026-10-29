'use strict';

// Activity categories (must match collector.classify). Edit colours here.
const CATS = {
  coding:      { c: '#3b82f6', l: 'Coding' },
  reporting:   { c: '#a855f7', l: 'Writing watched files' },
  writing:     { c: '#ec4899', l: 'Writing docs' },
  running:     { c: '#f59e0b', l: 'Running commands' },
  researching: { c: '#14b8a6', l: 'Researching' },
  delegating:  { c: '#6366f1', l: 'Delegating' },
  thinking:    { c: '#38bdf8', l: 'Thinking' },
  waiting:     { c: '#ef4444', l: 'Needs your input' },
  failed:      { c: '#b91c1c', l: 'Failed' },
  done:        { c: '#22c55e', l: 'Done' },
  idle:        { c: '#6b7280', l: 'Idle' },
};
const EDGE_COLOR = CATS.reporting.c;
const GROUP_COLORS = ['#a855f7', '#3b82f6', '#14b8a6', '#f59e0b', '#ec4899', '#22c55e', '#ef4444', '#94a3b8'];
const POLL_MS = 2000;
const CARD_W = 250, CARD_H = 132;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const trunc = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');
const uid = (p) => `${p}:${Math.random().toString(36).slice(2, 9)}`;
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

let layout = { positions: {}, groups: {}, parents: {}, notes: {}, links: [], hidden: [] };
let snap = null;
let agentsById = new Map();
let mode = null;          // null | 'box' | 'link'
let linkSource = null;
let selOrder = [];        // ids in the order they were selected (for L = parent → child)
let panelId = null;

// ---------------------------------------------------------------- cytoscape
const cy = cytoscape({
  container: $('#cy'),
  boxSelectionEnabled: true,
  selectionType: 'additive',
  minZoom: 0.08,
  maxZoom: 3,
  style: [
    { selector: 'node', style: { label: '', 'font-size': 11, color: '#e6e8ec', 'overlay-opacity': 0 } },
    { selector: 'node.agent', style: {
      shape: 'round-rectangle', width: CARD_W, height: CARD_H, 'background-color': '#1a1e27',
      'background-opacity': 0.01, 'border-width': 0 } },
    { selector: 'node.file', style: {
      shape: 'cut-rectangle', width: 'data(w)', height: 30, 'background-color': '#1b2029', 'border-width': 1.5,
      'border-color': '#3a4150', label: 'data(label)', 'text-valign': 'center', 'text-halign': 'center',
      'font-family': 'ui-monospace, SFMono-Regular, Menlo, monospace', 'font-size': 11, color: '#c9ced6' } },
    { selector: 'node.file.touched', style: { 'border-color': EDGE_COLOR, 'border-width': 2.5, color: '#fff', 'background-color': '#261b38' } },
    { selector: 'node.file.changed', style: { 'border-color': '#f59e0b', 'border-style': 'dashed' } },
    { selector: 'node.file.missing', style: { 'border-style': 'dotted', color: '#8b919c' } },
    { selector: 'node.file:selected', style: { 'border-color': '#fff', 'border-width': 2.5 } },
    { selector: 'node.group', style: {
      shape: 'round-rectangle', 'background-color': 'data(color)', 'background-opacity': 0.07,
      'border-width': 1.5, 'border-style': 'dashed', 'border-color': 'data(color)', label: 'data(label)',
      'text-valign': 'top', 'text-halign': 'center', 'text-margin-y': -4, 'font-size': 14, 'font-weight': 'bold',
      color: 'data(color)', padding: 24, width: 180, height: 100, 'min-width': 180, 'min-height': 100,
      'corner-radius': 14 } },
    { selector: 'node.group:selected', style: { 'border-style': 'solid', 'border-width': 2.5, 'background-opacity': 0.13 } },
    { selector: 'node.note', style: {
      shape: 'round-rectangle', 'background-color': '#fde68a', 'border-width': 0, label: 'data(text)',
      'text-wrap': 'wrap', 'text-max-width': 196, color: '#1f2937', 'font-size': 12, width: 220, height: 'data(h)',
      'text-valign': 'center', 'text-halign': 'center', 'text-justification': 'left' } },
    { selector: 'node.note:selected', style: { 'border-width': 2.5, 'border-color': '#fff' } },
    { selector: 'edge', style: {
      'curve-style': 'bezier', width: 1.5, 'line-color': '#4b5563', 'target-arrow-shape': 'triangle',
      'target-arrow-color': '#4b5563', 'arrow-scale': 0.9, 'overlay-opacity': 0 } },
    { selector: 'edge.write', style: {
      'line-color': EDGE_COLOR, 'target-arrow-color': EDGE_COLOR, width: 'data(w)', opacity: 'data(op)',
      label: 'data(short)', 'font-size': 10.5, color: '#efe3ff', 'text-background-color': '#0f1115',
      'text-background-opacity': 0.9, 'text-background-padding': 3, 'text-background-shape': 'round-rectangle',
      'text-rotation': 'autorotate' } },
    { selector: 'edge.write.active', style: { 'line-style': 'dashed', 'line-dash-pattern': [9, 5] } },
    { selector: 'edge.hier', style: {
      'line-style': 'dashed', 'line-dash-pattern': [3, 4], 'line-color': '#7b8494', 'target-arrow-color': '#7b8494',
      'target-arrow-shape': 'vee', label: 'data(label)', 'font-size': 10, color: '#9aa1ad',
      'text-background-color': '#0f1115', 'text-background-opacity': 0.8, 'text-background-padding': 2 } },
    { selector: 'edge.user', style: { 'line-color': '#b6bfcc', 'target-arrow-color': '#b6bfcc', width: 2, 'line-style': 'solid' } },
    { selector: 'edge:selected', style: { 'line-color': '#fff', 'target-arrow-color': '#fff' } },
    { selector: '.cdnd-drop-target', style: { 'border-style': 'solid', 'border-width': 3, 'background-opacity': 0.2 } },
  ],
});

cy.nodeHtmlLabel([{
  query: 'node.agent', halign: 'center', valign: 'center', halignBox: 'center', valignBox: 'center',
  tpl: (d) => {
    const cat = CATS[d.category] || CATS.idle;
    return `<div class="card ${d.sub ? 'sub' : ''} ${d.status === 'busy' || d.category === 'running' ? 'busy' : ''} ${d.sel ? 'sel' : ''}" style="--c:${cat.c}">
      <div class="card-h"><span class="dot"></span><span class="t">${esc(d.name)}</span></div>
      <div class="card-p">${esc(d.purpose)}</div>
      <div class="card-n">▸ ${esc(d.now)}</div>
      <div class="card-f"><span class="mono">${esc(d.short)}${d.sub ? ' · subagent' : ''}</span><span class="cat">${esc(cat.l)}</span></div>
    </div>`;
  },
}]);

cy.compoundDragAndDrop({
  grabbedNode: (n) => !n.hasClass('group'),
  dropTarget: (n) => n.hasClass('group'),
  dropSibling: () => false,
  outThreshold: 40,
});
cy.on('cdnddrop', 'node', (e, target) => { layout.parents[e.target.id()] = target.id(); save(); });
cy.on('cdndout', 'node', (e) => { delete layout.parents[e.target.id()]; save(); });

// ---------------------------------------------------------------- layout persistence
let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (snap) layout.watchDir = snap.watchDir;
    fetch('/api/layout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(layout) })
      .catch(() => toast('could not save layout', true));
  }, 400);
}

function savePositions() {
  cy.nodes().forEach((n) => {
    if (n.isParent()) return;  // compound boxes are sized by their children
    const p = n.position();
    layout.positions[n.id()] = { x: Math.round(p.x), y: Math.round(p.y) };
  });
  if (cy.nodes('.note').length) cy.nodes('.note').forEach((n) => { if (layout.notes[n.id()]) layout.notes[n.id()].parent = n.data('parent') || null; });
  save();
}
cy.on('free', 'node', savePositions);

function noteHeight(text) {
  const lines = String(text || ' ').split('\n').reduce((a, l) => a + Math.max(1, Math.ceil(l.length / 30)), 0);
  return Math.max(44, 22 + lines * 16);
}

function restoreUserElements() {
  // boxes (parents first, so nesting works), then notes
  const pending = Object.entries(layout.groups);
  let guard = pending.length + 1;
  while (pending.length && guard--) {
    for (let i = pending.length - 1; i >= 0; i--) {
      const [id, g] = pending[i];
      const parent = layout.parents[id];
      if (parent && !layout.groups[parent]) delete layout.parents[id];
      if (parent && layout.groups[parent] && cy.getElementById(parent).empty()) continue;
      cy.add({ group: 'nodes', classes: 'group', data: { id, type: 'group', label: g.label, color: g.color, parent: layout.parents[id] || undefined },
        position: layout.positions[id] || { x: 0, y: 0 } });
      pending.splice(i, 1);
    }
  }
  for (const [id, n] of Object.entries(layout.notes)) {
    const parent = layout.parents[id];
    cy.add({ group: 'nodes', classes: 'note', data: { id, type: 'note', text: n.text, h: noteHeight(n.text),
      parent: parent && layout.groups[parent] ? parent : undefined }, position: layout.positions[id] || { x: 0, y: 0 } });
  }
}

// ---------------------------------------------------------------- live sync
function desired(s) {
  const hidden = new Set(layout.hidden);
  const showIdle = $('#opt-idle').checked;
  const allFiles = $('#opt-allfiles').checked;
  const nodes = new Map();
  const edges = new Map();
  for (const a of s.agents) {
    if (hidden.has(a.id)) continue;
    if (!showIdle && (a.category === 'idle' || a.category === 'done')) continue;
    nodes.set(a.id, { group: 'nodes', classes: 'agent', data: {
      id: a.id, type: 'agent', name: a.name, purpose: a.purpose, now: a.now, short: a.short,
      category: a.category, status: a.status, sub: a.id.startsWith('sub:'), parentAgent: a.parent || '' } });
  }
  const touched = new Set(s.edges.map((e) => e.target));
  for (const f of s.files) {
    const isTouched = touched.has(f.id);
    if (hidden.has(f.id) || (!allFiles && !isTouched)) continue;
    const cls = ['file'];
    if (isTouched) cls.push('touched'); else if (s.generatedAt - f.mtime < 90) cls.push('changed');
    if (!f.exists) cls.push('missing');
    const label = trunc(f.label, 42);
    nodes.set(f.id, { group: 'nodes', classes: cls.join(' '), data: {
      id: f.id, type: 'file', label, w: Math.round(Math.min(300, 34 + 6.7 * label.length)) } });
  }
  for (const e of s.edges) {
    if (!nodes.has(e.source) || !nodes.has(e.target)) continue;
    const op = Math.max(0.3, 1 - e.age / (s.edgeWindow || 900));
    edges.set(e.id, { group: 'edges', classes: e.age < 60 ? 'write active' : 'write', data: {
      id: e.id, source: e.source, target: e.target,
      short: trunc(e.label, 52) + (e.count > 1 ? `  ×${e.count}` : ''),
      w: +(2 + Math.min(e.count, 6) * 0.5).toFixed(1), op: +op.toFixed(2) } });
  }
  for (const l of s.links) {
    const id = `auto:${l.source}->${l.target}`;
    if (nodes.has(l.source) && nodes.has(l.target)) {
      edges.set(id, { group: 'edges', classes: 'hier', data: { id, source: l.source, target: l.target, label: l.kind === 'subagent' ? 'spawned' : '' } });
    }
  }
  const present = (id) => nodes.has(id) || layout.groups[id] || layout.notes[id];
  for (const l of layout.links) {
    if (present(l.source) && present(l.target)) {
      edges.set(l.id, { group: 'edges', classes: 'hier user', data: { id: l.id, source: l.source, target: l.target, label: l.label || '' } });
    }
  }
  return { nodes, edges };
}

function makePlacer() {
  const cursors = {};
  const perParent = {};
  const start = (sel, stepX, stepY, fallbackY) => {
    const ns = cy.nodes(sel);
    if (!ns.length) return { x0: 0, x: 0, y: fallbackY(), stepX, stepY };
    let minX = Infinity, maxY = -Infinity;
    ns.forEach((n) => { const p = n.position(); minX = Math.min(minX, p.x); maxY = Math.max(maxY, p.y); });
    return { x0: minX, x: minX, y: maxY + stepY, stepX, stepY };
  };
  const lowestAgent = () => { let y = 0; cy.nodes('.agent').forEach((n) => { y = Math.max(y, n.position().y); }); return y + 230; };
  return (def) => {
    const d = def.data;
    if (d.type === 'agent' && d.parentAgent && cy.getElementById(d.parentAgent).nonempty()) {
      const k = (perParent[d.parentAgent] = (perParent[d.parentAgent] || 0) + 1);
      const p = cy.getElementById(d.parentAgent).position();
      return { x: p.x + (k - 1) * (CARD_W + 30) - 60, y: p.y + CARD_H + 70 };
    }
    const key = d.type;
    if (!cursors[key]) {
      cursors[key] = key === 'agent' ? start('node.agent', CARD_W + 30, CARD_H + 50, () => 0) : start('node.file', 310, 48, lowestAgent);
    }
    const c = cursors[key];
    const pos = { x: c.x, y: c.y };
    c.x += c.stepX;
    if (c.x - c.x0 > 1600) { c.x = c.x0; c.y += c.stepY; }
    return pos;
  };
}

function update(el, def) {
  const patch = {};
  for (const [k, v] of Object.entries(def.data)) if (k !== 'id' && k !== 'source' && k !== 'target' && el.data(k) !== v) patch[k] = v;
  if (Object.keys(patch).length) el.data(patch);
  if (el.scratch('_cls') !== def.classes) {
    // keep drag-and-drop helper classes while a drag is in flight
    const keep = el.classes().filter((c) => c.startsWith('cdnd'));
    el.classes([def.classes, ...keep].join(' '));
    el.scratch('_cls', def.classes);
  }
}

function sync() {
  if (!snap) return;
  const { nodes, edges } = desired(snap);
  const placeNew = makePlacer();
  cy.batch(() => {
    cy.edges().forEach((e) => { if (!edges.has(e.id())) e.remove(); });
    cy.nodes('.agent, .file').forEach((n) => { if (!nodes.has(n.id())) n.remove(); });
    for (const [id, def] of nodes) {
      const el = cy.getElementById(id);
      if (el.nonempty()) { update(el, def); continue; }
      const parent = layout.parents[id];
      if (parent && cy.getElementById(parent).nonempty()) def.data.parent = parent;
      def.position = layout.positions[id] ? { ...layout.positions[id] } : placeNew(def);
      cy.add(def).scratch('_cls', def.classes);
    }
    for (const [id, def] of edges) {
      const el = cy.getElementById(id);
      if (el.nonempty()) update(el, def); else cy.add(def).scratch('_cls', def.classes);
    }
  });
  const n = layout.hidden.length;
  $('#btn-unhide').hidden = !n;
  $('#btn-unhide').textContent = `Show hidden (${n})`;
}

let firstLoad = true;
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await fetch('/api/graph');
    if (!r.ok) throw new Error(r.statusText);
    snap = await r.json();
    agentsById = new Map(snap.agents.map((a) => [a.id, a]));
    sync();
    renderLegend();
    if (firstLoad) {
      firstLoad = false;
      $('#dir-input').value = snap.watchDir || '';
      if (!Object.keys(layout.positions).length) runLayout('hierarchy'); else cy.fit(undefined, 40);
    }
    if (panelId && !$('#panel').contains(document.activeElement)) showPanel(panelId, true);
    renderStatus(true);
  } catch (err) {
    renderStatus(false);
  } finally {
    polling = false;
  }
}

function renderStatus(ok) {
  const el = $('#status');
  el.classList.toggle('stale', !ok);
  if (!ok || !snap) { $('#status-text').textContent = 'disconnected from agentgraph server — retrying…'; return; }
  const busy = snap.agents.filter((a) => a.status === 'busy' || a.category === 'running').length;
  const waiting = snap.agents.filter((a) => a.category === 'waiting').length;
  $('#status-text').textContent = [
    `${snap.agents.length} agents`, `${busy} busy`, waiting ? `${waiting} need input` : null,
    snap.watchDir ? `${snap.files.length} files` : 'no folder watched', `${snap.edges.length} file edits`,
    snap.summarizer ? 'AI summaries on' : null, `updated ${new Date(snap.generatedAt * 1000).toLocaleTimeString()}`,
  ].filter(Boolean).join(' · ');
}

// ---------------------------------------------------------------- layouts
// Layered placement: rank = longest chain of links above a node (cycles are cut off), rows keep the
// current left-to-right order (children follow their parents), are packed, centred, top-aligned and wrapped at maxW.
function layered(items, links, { gapX, gapY, maxW }) {
  const rank = new Map(items.map((it) => [it.id, 0]));
  for (let i = 0; i < items.length; i++) {
    let changed = false;
    for (const [s, t] of links) {
      if (rank.get(t) < rank.get(s) + 1) { rank.set(t, rank.get(s) + 1); changed = true; }
    }
    if (!changed) break;
  }
  const preds = new Map(items.map((it) => [it.id, []]));
  for (const [s, t] of links) preds.get(t).push(s);
  const rows = [];
  for (const it of items) (rows[rank.get(it.id)] = rows[rank.get(it.id)] || []).push(it);
  const pos = new Map();
  let y = 0;
  for (const row of rows.filter(Boolean)) {
    const key = (it) => {
      const ps = preds.get(it.id).filter((p) => pos.has(p));
      return ps.length ? ps.reduce((acc, p) => acc + pos.get(p).x, 0) / ps.length : it.x;
    };
    row.forEach((it) => { it.k = key(it); });
    row.sort((a, b) => a.k - b.k);
    const lines = [[]];
    let w = 0;
    for (const it of row) {
      if (lines[lines.length - 1].length && w + it.w > maxW) { lines.push([]); w = 0; }
      lines[lines.length - 1].push(it);
      w += it.w + gapX;
    }
    for (const line of lines) {
      const lw = line.reduce((acc, it) => acc + it.w, 0) + gapX * (line.length - 1);
      const lh = Math.max(...line.map((it) => it.h));
      let x = -lw / 2;
      for (const it of line) { pos.set(it.id, { x: x + it.w / 2, y: y + it.h / 2 }); x += it.w + gapX; }
      y += lh + gapX;
    }
    y += gapY - gapX;
  }
  return pos;
}

// Hierarchy layout that understands boxes. Dagre ignores edges that touch compound nodes, so each box's
// contents are laid out first, then the box is treated as one block, with every link "lifted" to the
// outermost block on each side.
function hierarchyLayout() {
  const leaves = cy.nodes().filter((n) => !n.isParent());
  const before = new Map(leaves.map((n) => [n.id(), { ...n.position() }]));
  const skip = (n) => n.hasClass('file') && !n.hasClass('touched') && !n.isChild();  // tidied separately

  const arrange = (container) => {
    const kids = (container ? container.children() : cy.nodes().orphans()).filter((n) => !skip(n));
    if (!kids.length) return;
    kids.filter((n) => n.isParent()).forEach(arrange);
    const ids = new Set(kids.map((n) => n.id()));
    const unitOf = (n) => {
      while (n && n.nonempty()) {
        if (ids.has(n.id())) return n.id();
        n = n.parent();
      }
      return null;
    };
    const items = kids.map((n) => {
      const bb = n.boundingBox({ includeLabels: true });
      return { id: n.id(), w: Math.max(bb.w, 40), h: Math.max(bb.h, 30), x: (bb.x1 + bb.x2) / 2 };
    });
    const links = [];
    const seen = new Set();
    cy.edges().forEach((e) => {
      const s = unitOf(e.source()), t = unitOf(e.target());
      if (!s || !t || s === t || seen.has(s + '>' + t)) return;
      seen.add(s + '>' + t);
      links.push([s, t]);
    });
    const top = !container;
    const pos = layered(items, links, top ? { gapX: 70, gapY: 130, maxW: 3600 } : { gapX: 36, gapY: 80, maxW: 1100 });
    kids.forEach((n) => {
      const p = pos.get(n.id());
      if (!n.isParent()) { n.position(p); return; }
      const bb = n.boundingBox({ includeLabels: true });
      const dx = p.x - (bb.x1 + bb.x2) / 2, dy = p.y - (bb.y1 + bb.y2) / 2;
      n.descendants().filter((d) => !d.isParent()).forEach((d) => {
        const q = d.position();
        d.position({ x: q.x + dx, y: q.y + dy });
      });
    });
  };

  arrange(null);  // not batched: box bounds must update as their contents move
  tidyFiles(cy.nodes('.file').filter((n) => skip(n)), true);
  // animate from the old positions to the new ones
  const after = new Map(leaves.map((n) => [n.id(), { ...n.position() }]));
  leaves.forEach((n) => n.position(before.get(n.id())));
  leaves.forEach((n) => n.animate({ position: after.get(n.id()), duration: 350 }));
  setTimeout(() => { savePositions(); cy.animate({ fit: { eles: cy.elements(), padding: 40 }, duration: 250 }); }, 380);
}

function runLayout(kind) {
  if (kind === 'hierarchy') { hierarchyLayout(); return; }
  if (kind !== 'groups') {
    tidyFiles(cy.nodes('.file').filter((n) => !n.isChild()));
    savePositions();
    cy.fit(undefined, 40);
    return;
  }
  const l = cy.elements().layout({ name: 'cose', animate: true, animationDuration: 500, nodeRepulsion: () => 12000,
    idealEdgeLength: () => 150, nestingFactor: 1.1, gravity: 0.35, numIter: 1500, padding: 40, componentSpacing: 90,
    nodeDimensionsIncludeLabels: true, fit: true });
  l.one('layoutstop', () => { savePositions(); cy.fit(undefined, 40); });
  l.run();
}

function tidyFiles(files, instant) {
  if (!files.length) return;
  const others = cy.elements().difference(files);
  const bb = others.nonempty() ? others.boundingBox() : { x1: 0, y2: 0, w: 1200 };
  const sorted = files.sort((a, b) => a.data('label').localeCompare(b.data('label')));
  const cols = Math.max(1, Math.min(6, Math.ceil(Math.sqrt(sorted.length * 2))));
  const colW = 320, rowH = 46;
  const x0 = bb.x1 + Math.max(0, (bb.w - cols * colW) / 2) + colW / 2;
  sorted.forEach((n, i) => {
    const pos = { x: x0 + (i % cols) * colW, y: bb.y2 + 90 + Math.floor(i / cols) * rowH };
    if (instant) n.position(pos); else n.animate({ position: pos }, { duration: 300 });
  });
  if (!instant) setTimeout(savePositions, 350);
}

// ---------------------------------------------------------------- boxes, notes, links
function createGroup(members, label) {
  members = members.filter((n) => n.isNode());
  const id = uid('group');
  const color = GROUP_COLORS[Object.keys(layout.groups).length % GROUP_COLORS.length];
  label = label || `Group ${Object.keys(layout.groups).length + 1}`;
  const parents = new Set(members.map((n) => n.data('parent') || ''));
  const common = parents.size === 1 ? [...parents][0] : '';
  layout.groups[id] = { label, color };
  if (common) layout.parents[id] = common;
  const bb = members.nonempty() ? members.boundingBox() : null;
  cy.add({ group: 'nodes', classes: 'group', data: { id, type: 'group', label, color, parent: common || undefined },
    position: bb ? { x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2 } : cy.extent() && centerOfView() });
  members.forEach((n) => {
    if (n.id() === id || n.ancestors().some((a) => members.contains(a))) return;  // nested ones move with their box
    n.move({ parent: id });
    layout.parents[n.id()] = id;
  });
  savePositions();
  cy.elements().unselect();
  cy.getElementById(id).select();
  showPanel(id);
  setTimeout(() => { const i = $('#panel input[name=label]'); if (i) { i.focus(); i.select(); } }, 30);
  return id;
}

function ungroup(id) {
  const g = cy.getElementById(id);
  if (g.empty()) return;
  const up = g.data('parent') || null;
  g.children().forEach((c) => {
    c.move({ parent: up });
    if (up) layout.parents[c.id()] = up; else delete layout.parents[c.id()];
  });
  cy.getElementById(id).remove();
  delete layout.groups[id];
  delete layout.parents[id];
  delete layout.positions[id];
  layout.links = layout.links.filter((l) => l.source !== id && l.target !== id);
  if (panelId === id) hidePanel();
  savePositions();
}

function centerOfView() {
  const e = cy.extent();
  return { x: (e.x1 + e.x2) / 2, y: (e.y1 + e.y2) / 2 };
}

function createNote(pos, parent) {
  const id = uid('note');
  const text = 'New note';
  layout.notes[id] = { text };
  if (parent) layout.parents[id] = parent;
  cy.add({ group: 'nodes', classes: 'note', data: { id, type: 'note', text, h: noteHeight(text), parent: parent || undefined }, position: pos || centerOfView() });
  savePositions();
  cy.elements().unselect();
  cy.getElementById(id).select();
  showPanel(id);
  setTimeout(() => { const t = $('#panel textarea'); if (t) { t.focus(); t.select(); } }, 30);
}

function addLink(source, target) {
  if (!source || !target || source === target) return;
  if (layout.links.some((l) => l.source === source && l.target === target)) return;
  layout.links.push({ id: uid('link'), source, target, label: '' });
  save();
  sync();
  toast('Hierarchy link added — run “Hierarchy” layout to arrange');
}

function removeNode(id) {
  const el = cy.getElementById(id);
  if (el.hasClass('group')) return ungroup(id);
  if (el.hasClass('note')) {
    el.remove();
    delete layout.notes[id];
    delete layout.parents[id];
    delete layout.positions[id];
    layout.links = layout.links.filter((l) => l.source !== id && l.target !== id);
    if (panelId === id) hidePanel();
    save();
    return;
  }
  if (!layout.hidden.includes(id)) layout.hidden.push(id);
  if (panelId === id) hidePanel();
  save();
  sync();
}

function removeFromGroup(id) {
  const el = cy.getElementById(id);
  if (el.empty() || !el.data('parent')) return;
  const up = cy.getElementById(el.data('parent')).data('parent') || null;
  el.move({ parent: up });
  if (up) layout.parents[id] = up; else delete layout.parents[id];
  savePositions();
}

function moveToGroup(id, gid) {
  const el = cy.getElementById(id);
  if (el.empty() || id === gid) return;
  el.move({ parent: gid });
  layout.parents[id] = gid;
  savePositions();
}

function deleteSelected() {
  const sel = cy.$(':selected');
  if (!sel.length) return;
  let hidden = 0;
  sel.edges('.user').forEach((e) => { layout.links = layout.links.filter((l) => l.id !== e.id()); });
  sel.nodes().forEach((n) => {
    if (n.removed()) return;
    if (n.hasClass('agent') || n.hasClass('file')) hidden++;
    removeNode(n.id());
  });
  save();
  sync();
  if (hidden) toast(`Hid ${hidden} node${hidden > 1 ? 's' : ''} — “Show hidden” brings them back`);
}

// ---------------------------------------------------------------- modes
function setMode(m, hint) {
  mode = m;
  linkSource = null;
  $('#btn-box').classList.toggle('on', m === 'box');
  cy.userPanningEnabled(m !== 'box');
  $('#mode-hint').hidden = !m;
  $('#mode-hint').textContent = hint || '';
}

function startLink(sourceId) {
  setMode('link', sourceId ? 'Click the child node (Esc to cancel)' : 'Click the parent node, then the child (Esc to cancel)');
  linkSource = sourceId || null;
}

function linkSelected() {
  const ids = selOrder.filter((id) => cy.getElementById(id).selected());
  if (ids.length >= 2) { addLink(ids[ids.length - 2], ids[ids.length - 1]); return; }
  startLink(ids[0]);
}

cy.on('boxstart', () => { if (mode === 'box') cy.elements().unselect(); });
cy.on('boxend', () => {
  if (mode !== 'box') return;
  setTimeout(() => {
    const sel = cy.$('node:selected');
    setMode(null);
    if (sel.length) createGroup(sel); else toast('Nothing inside that box');
  }, 0);
});

cy.on('select', 'node', (e) => {
  selOrder = selOrder.filter((id) => id !== e.target.id()).concat(e.target.id()).slice(-10);
  if (e.target.hasClass('agent')) e.target.data('sel', true);
});
cy.on('unselect', 'node', (e) => { if (e.target.hasClass('agent')) e.target.data('sel', false); });

// ---------------------------------------------------------------- pointer interactions
cy.on('tap', (e) => {
  hideCtx();
  if (e.target === cy) { if (mode === 'link') setMode(null); hidePanel(); return; }
  if (mode === 'link' && e.target.isNode()) {
    if (!linkSource) { linkSource = e.target.id(); $('#mode-hint').textContent = 'Now click the child node'; return; }
    const src = linkSource;
    setMode(null);
    addLink(src, e.target.id());
    return;
  }
  showPanel(e.target.id());
});

cy.on('dbltap', 'node.agent', (e) => attach(e.target.id()));
cy.on('dbltap', 'node.note', (e) => { showPanel(e.target.id()); setTimeout(() => $('#panel textarea')?.focus(), 20); });
cy.on('dbltap', 'node.group', (e) => { showPanel(e.target.id()); setTimeout(() => $('#panel input[name=label]')?.focus(), 20); });

cy.on('cxttap', (e) => {
  const pos = e.renderedPosition || { x: 40, y: 40 };
  const model = e.position;
  const t = e.target;
  const items = [];
  if (t === cy) {
    items.push(['✎ New note here', () => createNote(model)]);
    items.push(['▭ New empty box here', () => {
      const id = createGroup(cy.collection());
      cy.getElementById(id).position(model);
      savePositions();
    }]);
    items.push('-');
    items.push(['Hierarchy layout', () => runLayout('hierarchy')]);
    items.push(['Fit to screen', () => cy.fit(undefined, 40)]);
  } else if (t.isEdge()) {
    if (t.hasClass('user')) {
      items.push(['Label link…', () => {
        const l = layout.links.find((x) => x.id === t.id());
        const v = prompt('Link label', l?.label || '');
        if (l && v !== null) { l.label = v; save(); sync(); }
      }]);
      items.push(['Delete link', () => { layout.links = layout.links.filter((l) => l.id !== t.id()); save(); sync(); }]);
    } else {
      items.push(['Details', () => showPanel(t.id())]);
    }
  } else {
    const id = t.id();
    if (t.hasClass('agent')) {
      const a = agentsById.get(id);
      if (a?.attachable) {
        items.push(['⤴ Open in terminal', () => attach(id)]);
        items.push(['Copy attach command', () => copy(`claude attach ${a.short}`)]);
      }
    }
    if (t.hasClass('group')) {
      items.push(['Rename / recolor…', () => { showPanel(id); setTimeout(() => $('#panel input[name=label]')?.focus(), 20); }]);
      items.push(['✎ Add note inside', () => createNote(model, id)]);
      items.push(['Ungroup (keep contents)', () => ungroup(id)]);
    }
    if (t.hasClass('note')) items.push(['Edit note', () => { showPanel(id); setTimeout(() => $('#panel textarea')?.focus(), 20); }]);
    items.push(['↳ Link to… (this is the parent)', () => startLink(id)]);
    const groups = cy.nodes('.group').filter((g) => g.id() !== id && !g.ancestors().contains(t) && g.id() !== t.data('parent'));
    groups.forEach((g) => items.push([`Move into “${trunc(g.data('label'), 24)}”`, () => moveToGroup(id, g.id())]));
    if (t.data('parent')) items.push(['Remove from box', () => removeFromGroup(id)]);
    items.push('-');
    if (t.hasClass('agent') || t.hasClass('file')) items.push(['Hide', () => removeNode(id)]);
    if (t.hasClass('note')) items.push(['Delete note', () => removeNode(id)]);
  }
  showCtx(pos, items);
});

function showCtx(pos, items) {
  const el = $('#ctx');
  el.innerHTML = '';
  for (const it of items) {
    if (it === '-') { el.appendChild(document.createElement('hr')); continue; }
    const b = document.createElement('button');
    b.textContent = it[0];
    b.onclick = () => { hideCtx(); it[1](); };
    el.appendChild(b);
  }
  el.hidden = false;
  const box = $('main').getBoundingClientRect();
  el.style.left = `${Math.min(pos.x, box.width - el.offsetWidth - 8)}px`;
  el.style.top = `${Math.min(pos.y, box.height - el.offsetHeight - 8)}px`;
}
function hideCtx() { $('#ctx').hidden = true; }
document.addEventListener('pointerdown', (e) => { if (!$('#ctx').contains(e.target)) hideCtx(); });

// hover tooltips for edit edges and files
cy.on('mouseover', 'edge.write, node.file', (e) => {
  const t = e.target;
  let html = '';
  if (t.isEdge()) {
    const s = snap.edges.find((x) => x.id === t.id());
    if (!s) return;
    html = `<div>${esc(s.label)}</div><div class="m">${esc(agentsById.get(s.source)?.name || s.source)} · ${esc(s.mechanical)} · ${s.count}× · ${ago(s.lastTs)}</div>`;
  } else {
    const f = snap.files.find((x) => x.id === t.id());
    if (!f) return;
    html = `<div class="mono">${esc(f.label)}</div><div class="m">modified ${f.mtime ? ago(f.mtime) : '—'}</div>`;
  }
  const tip = $('#tip');
  tip.innerHTML = html;
  tip.hidden = false;
  const p = e.renderedPosition;
  tip.style.left = `${p.x + 14}px`;
  tip.style.top = `${p.y + 14}px`;
});
cy.on('mouseout', 'edge, node', () => { $('#tip').hidden = true; });
cy.on('pan zoom drag', () => { $('#tip').hidden = true; });

// animated dashes on edges written in the last minute
let dash = 0;
setInterval(() => {
  const active = cy.edges('.write.active');
  if (!active.length) return;
  dash = (dash - 1.5) % 28;
  active.style('line-dash-offset', dash);
}, 60);

// ---------------------------------------------------------------- side panel
function hidePanel() { panelId = null; $('#panel').hidden = true; }

function showPanel(id, refresh) {
  const el = cy.getElementById(id);
  if (el.empty()) { if (!refresh) hidePanel(); return; }
  panelId = id;
  const p = $('#panel');
  const type = el.isEdge() ? 'edge' : el.data('type');
  if (refresh && (type === 'group' || type === 'note')) return;  // don't clobber edits in progress
  let html = '<button class="close" data-a="close" title="Close">✕</button>';
  if (type === 'agent') html += agentPanel(agentsById.get(id));
  else if (type === 'file') html += filePanel(id);
  else if (type === 'edge') html += edgePanel(el);
  else if (type === 'group') html += groupPanel(el);
  else if (type === 'note') html += notePanel(el);
  const scroll = p.scrollTop;
  p.innerHTML = html;
  p.scrollTop = scroll;
  p.hidden = false;
  wirePanel(el, type);
}

function agentPanel(a) {
  if (!a) return '<p>Agent has exited.</p>';
  const cat = CATS[a.category] || CATS.idle;
  const edits = snap.edges.filter((e) => e.source === a.id);
  return `
    <h2>${esc(a.name)}</h2>
    <span class="badge" style="--c:${cat.c};background:${cat.c}">${esc(cat.l)}</span>
    <span class="mono" style="color:var(--muted);margin-left:6px">${esc(a.short)}</span>
    <h3>Purpose</h3><div>${esc(a.purpose)}</div>
    <h3>Right now</h3><div>${esc(a.now)}</div>
    ${a.attachable
      ? `<div class="row"><button class="primary" data-a="attach">⤴ Open in terminal</button><button data-a="copy">Copy <span class="mono">claude attach ${esc(a.short)}</span></button></div>`
      : `<p style="color:var(--muted)">${esc(a.kind)} session${a.tty ? ` on <span class="mono">${esc(a.tty)}</span>` : ''} — switch to its terminal to talk to it.</p>`}
    <h3>Details</h3>
    <dl class="kv">
      <dt>status</dt><dd>${esc(a.status)}${a.jobState ? ` · job ${esc(a.jobState)}` : ''}${a.inFlight ? ` · ${a.inFlight} background task${a.inFlight > 1 ? 's' : ''}` : ''}</dd>
      <dt>kind</dt><dd>${esc(a.kind)}</dd>
      <dt>cwd</dt><dd class="mono">${esc(a.cwd)}</dd>
      ${a.pid ? `<dt>pid</dt><dd>${a.pid}</dd>` : ''}
      ${a.tokens ? `<dt>tokens</dt><dd>${a.tokens.toLocaleString()}</dd>` : ''}
      <dt>last active</dt><dd>${a.lastActivity ? ago(a.lastActivity) : '—'}</dd>
    </dl>
    ${edits.length ? `<h3>Edits to watched files</h3><ul>${edits.map((e) => `<li><span class="k mono">${esc(e.path.split('/').pop())}</span> — ${esc(e.label)}<div class="when">${esc(e.mechanical)} · ${e.count}× · ${ago(e.lastTs)}</div></li>`).join('')}</ul>` : ''}
    <h3>Recent activity</h3>
    <ul>${a.recent.map((r) => `<li><span class="k">${esc(r.tool)}</span> ${esc(r.summary)}${r.narration ? `<div>${esc(r.narration)}</div>` : ''}<div class="when">${ago(r.ts)}</div></li>`).join('') || '<li>none yet</li>'}</ul>
    <h3>Task</h3>
    <details><summary>original prompt</summary><div class="intent">${esc(a.intent)}</div></details>
    <div class="row">${cy.getElementById(a.id).data('parent') ? '<button data-a="ungroup">Remove from box</button>' : ''}<button data-a="hide">Hide</button></div>`;
}

function filePanel(id) {
  const f = snap.files.find((x) => x.id === id);
  if (!f) return '<p>File no longer listed.</p>';
  const edits = snap.edges.filter((e) => e.target === id);
  return `
    <h2 class="mono">${esc(f.name)}</h2>
    <div class="mono" style="color:var(--muted);font-size:11.5px;overflow-wrap:anywhere">${esc(f.path)}</div>
    <dl class="kv" style="margin-top:8px"><dt>modified</dt><dd>${f.mtime ? ago(f.mtime) : '—'}</dd></dl>
    <h3>Agents editing it</h3>
    <ul>${edits.map((e) => `<li><span class="k">${esc(agentsById.get(e.source)?.name || e.source)}</span>
      <ul style="margin-top:4px">${e.history.slice().reverse().map((h) => `<li>${esc(h.label)}<div class="when">${esc(h.summary)} · ${ago(h.ts)}</div></li>`).join('')}</ul></li>`).join('')
      || '<li>No agent has edited this recently.</li>'}</ul>
    <div class="row">${cy.getElementById(id).data('parent') ? '<button data-a="ungroup">Remove from box</button>' : ''}<button data-a="hide">Hide</button></div>`;
}

function edgePanel(el) {
  const s = snap.edges.find((x) => x.id === el.id());
  if (!s) {
    const l = layout.links.find((x) => x.id === el.id());
    const name = (id) => esc(agentsById.get(id)?.name || cy.getElementById(id).data('label') || cy.getElementById(id).data('text') || id);
    return `<h2>Hierarchy link</h2><p>${name(el.data('source'))} → ${name(el.data('target'))}</p>
      ${l ? '<div class="row"><button data-a="dellink">Delete link</button></div>' : '<p style="color:var(--muted)">Detected automatically (subagent).</p>'}`;
  }
  return `<h2>${esc(s.label)}</h2>
    <p>${esc(agentsById.get(s.source)?.name || s.source)} → <span class="mono">${esc(s.path.split('/').pop())}</span></p>
    <h3>History</h3><ul>${s.history.slice().reverse().map((h) => `<li>${esc(h.label)}<div class="when">${esc(h.summary)} · ${ago(h.ts)}</div></li>`).join('')}</ul>`;
}

function groupPanel(el) {
  const color = el.data('color');
  return `<h2>Box</h2>
    <h3>Label</h3><input type="text" name="label" value="${esc(el.data('label'))}">
    <h3>Colour</h3><div class="swatches">${GROUP_COLORS.map((c) => `<button data-color="${c}" class="${c === color ? 'on' : ''}" style="background:${c}" title="${c}"></button>`).join('')}</div>
    <h3>Contents</h3><div>${el.children().length} item(s). Drag nodes in or out of the box, or right-click a node → Move into.</div>
    <div class="row"><button data-a="note">✎ Add note inside</button><button data-a="ungroup-box">Ungroup</button></div>`;
}

function notePanel(el) {
  return `<h2>Note</h2><textarea name="text">${esc(el.data('text'))}</textarea>
    <div class="row">${el.data('parent') ? '<button data-a="ungroup">Remove from box</button>' : ''}<button data-a="delnote">Delete note</button></div>`;
}

function wirePanel(el, type) {
  const p = $('#panel');
  const id = el.id();
  p.querySelectorAll('[data-a]').forEach((b) => {
    b.onclick = () => {
      const a = b.dataset.a;
      if (a === 'close') hidePanel();
      else if (a === 'attach') attach(id);
      else if (a === 'copy') copy(`claude attach ${agentsById.get(id)?.short}`);
      else if (a === 'hide') removeNode(id);
      else if (a === 'ungroup') { removeFromGroup(id); showPanel(id); }
      else if (a === 'ungroup-box') ungroup(id);
      else if (a === 'delnote') removeNode(id);
      else if (a === 'note') createNote(el.position(), id);
      else if (a === 'dellink') { layout.links = layout.links.filter((l) => l.id !== id); hidePanel(); save(); sync(); }
    };
  });
  if (type === 'group') {
    const inp = p.querySelector('input[name=label]');
    inp.oninput = () => { el.data('label', inp.value); layout.groups[id].label = inp.value; save(); };
    inp.onkeydown = (e) => { if (e.key === 'Enter') inp.blur(); };
    p.querySelectorAll('[data-color]').forEach((b) => {
      b.onclick = () => {
        el.data('color', b.dataset.color);
        layout.groups[id].color = b.dataset.color;
        p.querySelectorAll('[data-color]').forEach((x) => x.classList.toggle('on', x === b));
        save();
      };
    });
  }
  if (type === 'note') {
    const ta = p.querySelector('textarea');
    ta.oninput = () => { el.data({ text: ta.value, h: noteHeight(ta.value) }); layout.notes[id].text = ta.value; save(); };
  }
}

// ---------------------------------------------------------------- actions
async function attach(id) {
  const a = agentsById.get(id);
  if (!a) return;
  if (!a.attachable) { toast(`${a.kind} session${a.tty ? ` on ${a.tty}` : ''} — switch to its terminal`, true); return; }
  try {
    const r = await fetch('/api/attach', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    toast(`Attaching to ${a.name} (${j.message}) — ← in that window returns to agent view`);
  } catch (err) {
    copy(`claude attach ${a.short}`, true);
    toast(`${err.message}. Copied “claude attach ${a.short}” instead.`, true);
  }
}

async function copy(text, quiet) {
  try { await navigator.clipboard.writeText(text); if (!quiet) toast(`Copied: ${text}`); } catch { if (!quiet) toast(text); }
}

let toastTimer = null;
function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('err', !!err);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
}

// ---------------------------------------------------------------- toolbar
document.querySelectorAll('[data-layout]').forEach((b) => { b.onclick = () => runLayout(b.dataset.layout); });
document.querySelectorAll('[data-act]').forEach((b) => {
  b.onclick = () => {
    const a = b.dataset.act;
    if (a === 'fit') cy.fit(undefined, 40);
    else if (a === 'box') setMode(mode === 'box' ? null : 'box', 'Drag a rectangle around the nodes to box (Esc to cancel)');
    else if (a === 'group') groupSelected();
    else if (a === 'note') createNote();
    else if (a === 'link') linkSelected();
    else if (a === 'unhide') { layout.hidden = []; save(); sync(); }
  };
});
$('#opt-allfiles').onchange = $('#opt-idle').onchange = () => {
  layout.options = { allFiles: $('#opt-allfiles').checked, showIdle: $('#opt-idle').checked };
  save();
  sync();
};

function groupSelected() {
  const sel = cy.$('node:selected');
  if (!sel.length) { setMode('box', 'Drag a rectangle around the nodes to box (Esc to cancel)'); return; }
  createGroup(sel);
}

let dirTimer = null;
$('#dir-input').addEventListener('input', () => {
  clearTimeout(dirTimer);
  dirTimer = setTimeout(async () => {
    const v = $('#dir-input').value;
    try {
      const r = await fetch(`/api/dirs?prefix=${encodeURIComponent(v)}`);
      const list = await r.json();
      $('#dir-suggest').innerHTML = list.map((d) => `<option value="${esc(d)}">`).join('');
    } catch { /* suggestions are optional */ }
  }, 150);
});
$('#dir-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const dir = $('#dir-input').value.trim();
  const r = await fetch('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dir }) });
  const j = await r.json();
  if (!r.ok) { toast(j.error, true); return; }
  toast(j.watchDir ? `Watching ${j.watchDir}` : 'Not watching a folder');
  $('#dir-input').value = j.watchDir || '';
  await poll();
  tidyFiles(cy.nodes('.file').filter((n) => !layout.positions[n.id()] && !n.isChild()));
});

document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, textarea') || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === 'escape') { setMode(null); hideCtx(); }
  else if (k === 'g') groupSelected();
  else if (k === 'n') createNote();
  else if (k === 'l') linkSelected();
  else if (k === 'f') cy.fit(undefined, 40);
  else if (k === 'h') runLayout('hierarchy');
  else if (k === 'b') setMode(mode === 'box' ? null : 'box', 'Drag a rectangle around the nodes to box (Esc to cancel)');
  else if (k === 'delete' || k === 'backspace') { e.preventDefault(); deleteSelected(); }
  else return;
});

// Live legend: how many agents are in each colour right now; click a colour to select those agents.
function renderLegend() {
  const agents = snap ? snap.agents : [];
  const byCat = {};
  for (const a of agents) (byCat[a.category] = byCat[a.category] || []).push(a);
  $('#legend').innerHTML = Object.entries(CATS).map(([k, c]) => {
    const list = byCat[k] || [];
    const tip = list.length ? list.map((a) => a.name).join('\n') : 'no agents right now';
    return `<button class="it ${list.length ? '' : 'none'}" data-cat="${k}" title="${esc(tip)}">`
      + `<span class="sw" style="background:${c.c}"></span>${esc(c.l)}<span class="n">${list.length || ''}</span></button>`;
  }).join('') + '<div class="it"><span class="sw file"></span>file (edited)</div>';
}
$('#legend').addEventListener('click', (e) => {
  const b = e.target.closest('[data-cat]');
  if (!b) return;
  const ns = cy.nodes('.agent').filter((n) => n.data('category') === b.dataset.cat);
  if (!ns.length) { toast(`No visible agents are ${CATS[b.dataset.cat].l.toLowerCase()}`); return; }
  cy.$(':selected').unselect();
  ns.select();
  const bb = ns.boundingBox();
  const z = Math.min(1.1, cy.width() / (bb.w + 160), cy.height() / (bb.h + 160));
  cy.animate({ zoom: z, center: { eles: ns }, duration: 350 });
});

// ---------------------------------------------------------------- boot
(async function boot() {
  renderLegend();
  try {
    const saved = await (await fetch('/api/layout')).json();
    layout = { ...layout, ...saved };
    for (const k of ['positions', 'groups', 'parents', 'notes']) layout[k] = layout[k] || {};
    layout.links = layout.links || [];
    layout.hidden = layout.hidden || [];
    if (layout.options) {
      $('#opt-allfiles').checked = layout.options.allFiles !== false;
      $('#opt-idle').checked = layout.options.showIdle !== false;
    }
  } catch { /* first run */ }
  restoreUserElements();
  await poll();
  setInterval(poll, POLL_MS);
})();
