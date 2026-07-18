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
  grouping: getVar('--warning'), signal: getVar('--lbl-nd'), other: getVar('--muted'),
};

function getVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}
export function labelColor(group) { return LABEL_COLORS[group] || LABEL_COLORS.none; }
export function labelName(group) { return LABEL_NAMES[group] || group; }

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
