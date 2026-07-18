// Shared helpers: DOM builder, label palette, formatters, token highlighting.

export const LABEL_COLORS = {
  pb: getVar('--lbl-pb'), ab: getVar('--lbl-ab'), si: getVar('--lbl-si'),
  nd: getVar('--lbl-nd'), ti: getVar('--lbl-ti'), none: getVar('--lbl-none'),
  other: getVar('--lbl-none'),
};
export const LABEL_NAMES = {
  pb: 'Product bug', ab: 'Automation bug', si: 'System issue',
  nd: 'No defect', ti: 'To investigate', none: 'Unlabeled', other: 'Other',
};
export const INK = getVar('--ink');
export const INK2 = getVar('--ink-2');
export const MUTED = getVar('--muted');
export const HAIRLINE = getVar('--hairline');
export const SURFACE = getVar('--surface-1');
export const ACCENT = getVar('--accent');
export const WARNING = getVar('--warning');
export const GOOD = getVar('--good');
// Resolved hex — ECharts' canvas renderer cannot read CSS custom properties.
export const BAND = { auto: getVar('--band-auto'), suggest: getVar('--band-suggest'), abstain: getVar('--band-abstain') };
export const FEATURE_GROUP_COLORS = {
  retrieval: getVar('--accent'), history: getVar('--lbl-ab'), kb: getVar('--lbl-si'),
  grouping: getVar('--warning'), signal: getVar('--lbl-nd'),
  // v4 groups (2026-07-18): discriminant → --lbl-pb (mockup C5 note); llm → --serious.
  discriminant: getVar('--lbl-pb'), llm: getVar('--serious'),
  other: getVar('--muted'),
};

function getVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}
export function labelColor(group) { return LABEL_COLORS[group] || LABEL_COLORS.none; }
export function labelName(group) { return LABEL_NAMES[group] || group; }

// ---------------------------------------------------------------------------
// ReportPortal name resolution (real defect-type names + colors).
// A per-project locator→{name,short_name,color} map fetched from /api/rp. When
// the map is empty (RP unconfigured/unreachable/name missing) every helper below
// degrades to the raw locator / static group name — never an invented string.
let _defects = {};
export function setDefects(map) { _defects = map || {}; }
export function defectInfo(locator) { return locator ? (_defects[locator] || null) : null; }
export function labelGroup(locator) {
  if (!locator) return 'none';
  for (const g of ['pb', 'ab', 'si', 'nd', 'ti']) if (locator.startsWith(g)) return g;
  return 'other';
}
// Representative RP defect for a label group (its locator starts with the group).
export function defectForGroup(group) {
  for (const loc of Object.keys(_defects)) if (loc.startsWith(group)) return _defects[loc];
  return null;
}
export function defectColor(locator, group) {
  const info = defectInfo(locator);
  if (info && info.color) return info.color;
  const g = group || labelGroup(locator);
  const byGroup = defectForGroup(g);
  return (byGroup && byGroup.color) || labelColor(g);
}
export function defectName(locator, group) {
  const info = defectInfo(locator);
  if (info && info.name) return info.name;
  const g = group || labelGroup(locator);
  const byGroup = defectForGroup(g);
  return (byGroup && byGroup.name) || labelName(g);
}

// Badge showing the REAL defect long-name accented by the RP hex_color, with the
// locator kept as a secondary monospace chip (locator is real data too). Falls
// back to the static group name + palette color when RP names are unavailable.
export function defectBadge(locator, group) {
  const g = group || labelGroup(locator);
  const col = defectColor(locator, g);
  const name = defectName(locator, g);
  const badge = h('span', { class: 'badge defect',
    style: { color: col, borderColor: col, background: `color-mix(in srgb, ${col} 15%, transparent)` } },
    h('span', { class: 'dot', style: { background: col } }), name);
  if (!locator) return badge;
  const info = defectInfo(locator);
  return h('span', { class: 'flex center gap-8' }, badge,
    h('span', { class: 'chip mono', title: info ? 'RP locator' : 'locator (RP name unavailable)' }, locator));
}

// Compact defect badge for dense lists: shows the RP abbreviation
// (issue_type.abbreviation). Falls back to the group code (PB/AB/SI/ND/TI —
// RP's stock abbreviations) when RP names are unavailable; full name + locator
// stay reachable in the tooltip.
export function defectBadgeAbbr(locator, group) {
  const g = group || labelGroup(locator);
  const col = defectColor(locator, g);
  const info = defectInfo(locator);
  const abbr = (info && (info.short_name || info.name))
    || (['pb', 'ab', 'si', 'nd', 'ti'].includes(g) ? g.toUpperCase() : labelName(g));
  const title = [info && info.name, locator].filter(Boolean).join(' · ');
  return h('span', { class: 'badge defect', title: title || null,
    style: { color: col, borderColor: col, background: `color-mix(in srgb, ${col} 15%, transparent)` } },
    h('span', { class: 'dot', style: { background: col } }), abbr);
}

// Render a test-item / launch id as a hyperlink into the ReportPortal UI when a
// real deep link (ui_url/launch_url resolved from RP's DB) is present; otherwise
// return exactly today's plain node. Keeps the no-dummy-data rule: no link when
// there is no real URL. `cls` is the class of the surrounding chip/text so the
// link inherits its look and only gains a link affordance (see .idlink in CSS).
// Opens in a new tab (target=_blank, rel=noopener).
export function idChip(label, url, cls = 'chip') {
  if (!url) return h('span', cls ? { class: cls } : {}, label);
  return h('a', {
    href: url, target: '_blank', rel: 'noopener',
    class: (cls ? cls + ' ' : '') + 'idlink', title: 'Open in ReportPortal ↗',
  }, label);
}

// tiny hyperscript
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  }
  return el;
}
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function labelBadge(group, text) {
  const g = group || 'none';
  return h('span', { class: `badge lbl ${g}` },
    h('span', { class: 'dot' }), text || labelName(g));
}

export function fmt(n, digits = 3) {
  if (n == null || Number.isNaN(n)) return '—';
  if (typeof n !== 'number') return String(n);
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toFixed(digits);
}
export function pct(n, digits = 1) { return n == null ? '—' : (n * 100).toFixed(digits) + '%'; }
export function shortTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// Highlight Drain / masking tokens inside a template pattern.
export function highlightPattern(pattern) {
  if (pattern == null) return '<span class="muted">— pattern unavailable —</span>';
  let s = esc(pattern);
  const rules = [
    [/&lt;NUM&gt;|&lt;\*&gt;|&lt;:NUM:&gt;/g, 'tok tok-num'],
    [/&lt;UUID&gt;|&lt;GUID&gt;/g, 'tok tok-uuid'],
    [/&lt;(URL|PATH|IP|HEX|TOKEN|DATE|TIME|NUMBER)&gt;/g, 'tok tok-url'],
    [/&lt;[A-Za-z0-9_:*]+&gt;/g, 'tok tok-wild'],
    [/\*/g, 'tok tok-wild'],
    [/\b\d{2,}\b/g, 'tok tok-num'],
    [/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, 'tok tok-uuid'],
  ];
  for (const [re, cls] of rules) {
    s = s.replace(re, (m) => `<span class="${cls}">${m}</span>`);
  }
  return s;
}

// Label-provenance vocabulary. Covers the label_event.source CHECK values
// (rp_defect_update / analyzer_suggestion_accepted / human_ui) AND the
// feature-vector label_source tokens (rp / human / ai_suggested / seed) so both
// the Feedback timeline and the Matching candidate strip read from one map.
// `weight` is the src_weight (features.py _SRC_WEIGHT / spec §6.4) — the vote a
// label of this provenance casts as future retrieval evidence.
export const SOURCE_LABELS = {
  rp_defect_update: { k: 'rp', text: 'defect update', plain: 'human · RP', actor: 'human (RP defect edit)', weight: 1.0 },
  analyzer_suggestion_accepted: { k: 'human', text: 'UI accept', plain: 'human · accepted', actor: 'human accepted analyzer suggestion', weight: 0.9 },
  human_ui: { k: 'human', text: 'UI edit', plain: 'human · UI', actor: 'human (Inspector UI)', weight: 0.9 },
  rp: { k: 'rp', text: 'defect update', plain: 'human · RP', actor: 'human (RP defect edit)', weight: 1.0 },
  human: { k: 'human', text: 'UI accept', plain: 'human', actor: 'human (Inspector UI)', weight: 0.9 },
  ai_suggested: { k: 'ai_suggested', text: 'auto', plain: 'analyzer', actor: "analyzer's auto-label", weight: 0.3 },
  seed: { k: 'seed', text: 'catalog', plain: 'seed', actor: 'seed catalog rule', weight: 0.6 },
};
export function srcInfo(token) {
  if (token == null) return { k: '—', text: '', plain: '—', actor: 'unrecorded actor', weight: null, raw: null };
  return { ...(SOURCE_LABELS[token] || { k: token, text: '', plain: token, actor: token, weight: null }), raw: token };
}

// Relative time: "just now" / "{m}m ago" / "{h}h ago" / "{d}d ago" / shortTime.
// The full ISO always rides title= elsewhere — this is a convenience layer only.
export function relTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return shortTime(iso);
}

export function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2200);
}

export function emptyState(icon, title, body, stageTag) {
  return h('div', { class: 'empty' },
    h('div', { class: 'icon' }, icon),
    h('h4', {}, title),
    h('p', {}, body),
    stageTag ? h('span', { class: 'stage-tag' }, stageTag) : null,
  );
}

export function loading(text = 'Loading…') {
  return h('div', { class: 'loading' }, h('span', { class: 'spinner' }), text);
}

export function card(title, opts = {}, ...body) {
  const head = h('div', { class: 'card-head' },
    h('div', { class: 'card-title' },
      opts.step ? h('span', { class: 'step-num' }, opts.step) : null, title),
    opts.sub ? h('div', { class: 'card-sub' }, opts.sub) : (opts.right || null));
  return h('div', { class: 'card' + (opts.class ? ' ' + opts.class : '') }, head,
    h('div', { class: 'card-body' }, ...body));
}

// Shared "Technical Details" disclosure (L4). Native <details>, closed by
// default, open-state persisted per key across items/launches so an ML engineer
// opens it once and it stays open (localStorage `inspector.eng.<key>`).
export function engDrawer(key, summaryText, ...children) {
  const details = h('details', { class: 'eng' });
  const summary = h('summary', {}, summaryText + ' ', h('span', { class: 'eng-caret' }, '▸'));
  details.append(summary, h('div', { class: 'eng-body' }, ...children));
  const sk = 'inspector.eng.' + key;
  try { if (localStorage.getItem(sk) === '1') details.open = true; } catch (_) { /* storage off */ }
  details.addEventListener('toggle', () => {
    try { localStorage.setItem(sk, details.open ? '1' : '0'); } catch (_) { /* storage off */ }
  });
  return details;
}
export function engDetails(key, ...children) {
  return engDrawer(key, 'Technical Details', ...children);
}

// ECharts shared dark options
export function echartsBase() {
  return {
    textStyle: { fontFamily: 'system-ui, sans-serif', color: INK2 },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: '#26292d', borderColor: HAIRLINE, textStyle: { color: INK },
      confine: true,
    },
  };
}
