// Drain3 Explorer — searchable template table + a d3 icicle approximating the
// Drain parse tree (grouped by leading tokens of each masked pattern).
import { api } from '../api.js';
import {
  h, clear, card, highlightPattern, emptyState, loading, shortTime, fmt, MUTED, INK, INK2, HAIRLINE,
} from '../util.js';

let _q = '';

export async function renderDrain(root, app) {
  clear(root);
  const search = h('input', {
    class: 'select', type: 'search', placeholder: 'Filter patterns (ILIKE)…', value: _q,
    style: { minWidth: '280px' },
    oninput: debounce((e) => { _q = e.target.value; load(); }, 250),
  });
  root.appendChild(h('div', { class: 'picker-row' },
    h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Search templates'), search)));

  const treeCard = card('Parse tree (icicle)', { sub: 'grouped by leading masked tokens — approximates Drain’s tree' });
  const tblCard = card('Templates', { sub: 'log_template mirror' });
  root.append(h('div', { class: 'grid grid-2' }, treeCard, tblCard));
  const treeBody = treeCard.querySelector('.card-body');
  const tblBody = tblCard.querySelector('.card-body');

  async function load() {
    clear(tblBody).appendChild(loading());
    const { templates, count } = await api.templates(app.project, _q);
    tblCard.querySelector('.card-sub').textContent = `${count} template(s)`;
    clear(tblBody); clear(treeBody);
    if (!templates.length) {
      tblBody.appendChild(emptyState('🌵', 'No templates',
        _q ? 'No pattern matches your filter.' : 'Drain3 has not mined any templates for this project yet — templates appear as error logs are parsed.',
        'analyzer.log_template'));
      treeBody.appendChild(emptyState('🌳', 'Nothing to tree', 'No patterns to build a parse tree from.', 'analyzer.log_template'));
      return;
    }
    renderTable(tblBody, templates);
    renderIcicle(treeBody, templates);
  }
  await load();
}

function renderTable(el, templates) {
  const tbl = h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, ...['id', 'pattern', 'tok', 'matches', 'last seen'].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...templates.map((t) => h('tr', {},
      h('td', { class: 'mono' }, `#${t.template_id}`),
      h('td', { style: { whiteSpace: 'normal', maxWidth: '420px' } }, h('div', { class: 'pattern', html: highlightPattern(t.pattern) })),
      h('td', { class: 'num' }, t.token_count),
      h('td', { class: 'num' }, fmt(t.match_count)),
      h('td', { class: 'muted mono', style: { fontSize: '11px' } }, shortTime(t.last_seen))))));
  el.appendChild(h('div', { class: 'table-wrap', style: { maxHeight: '520px', overflowY: 'auto' } }, tbl));
}

function renderIcicle(el, templates) {
  // Build a token-prefix tree (first 4 tokens) — approximates Drain's fixed-depth tree.
  const root = { name: 'root', children: new Map(), count: 0 };
  for (const t of templates) {
    const toks = String(t.pattern || '').split(/\s+/).slice(0, 4);
    let node = root; root.count += t.match_count || 1;
    for (const tok of toks) {
      if (!node.children.has(tok)) node.children.set(tok, { name: tok, children: new Map(), count: 0 });
      node = node.children.get(tok); node.count += t.match_count || 1;
    }
  }
  const toObj = (n) => ({ name: n.name, value: n.count, children: [...n.children.values()].map(toObj) });
  const data = toObj(root);
  if (!data.children.length) { el.appendChild(h('div', { class: 'muted' }, 'No tokens.')); return; }

  const width = el.clientWidth || 560, height = 460;
  const svg = d3.create('svg').attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);
  const hierarchy = d3.hierarchy(data).sum((d) => (d.children && d.children.length ? 0 : d.value) || 1)
    .sort((a, b) => b.value - a.value);
  d3.partition().size([height, width])(hierarchy);
  const color = d3.scaleSequential([0, 4], (t) => d3.interpolateBlues(0.35 + t * 0.14));

  const cell = svg.selectAll('g').data(hierarchy.descendants().filter((d) => d.depth > 0)).join('g')
    .attr('transform', (d) => `translate(${d.y0},${d.x0})`);
  cell.append('rect')
    .attr('width', (d) => Math.max(0, d.y1 - d.y0 - 1))
    .attr('height', (d) => Math.max(0, d.x1 - d.x0 - 1))
    .attr('fill', (d) => color(d.depth)).attr('rx', 3)
    .append('title').text((d) => `${d.ancestors().reverse().slice(1).map((a) => a.data.name).join(' ')}\n${d.value} matches`);
  cell.append('text').attr('x', 6).attr('y', (d) => (d.x1 - d.x0) / 2).attr('dy', '0.35em')
    .attr('fill', INK).attr('font-size', 10).attr('font-family', 'ui-monospace, monospace')
    .style('pointer-events', 'none')
    .text((d) => ((d.x1 - d.x0) > 14 ? truncate(d.data.name, Math.floor((d.y1 - d.y0) / 7)) : ''));
  el.appendChild(svg.node());
}

function truncate(s, n) { s = String(s); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + '…' : s; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
