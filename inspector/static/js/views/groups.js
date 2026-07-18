// Launch Groups — d3 force graph. Nodes = items, clustered by launch_group;
// node color = final label; halo = auto-analyzed; burst groups flagged si_prior.
import { api } from '../api.js';
import {
  h, clear, card, emptyState, loading, setDefects, defectColor, defectName, defectInfo,
  fmt, MUTED, INK, INK2, HAIRLINE,
} from '../util.js';

let _launch = null;

export async function renderGroups(root, app) {
  clear(root);
  const launchSel = h('select', { class: 'select', onchange: (e) => { _launch = e.target.value || null; load(); } });
  root.appendChild(h('div', { class: 'picker-row' },
    h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Launch'), launchSel)));

  const { launches } = await api.launches(app.project);
  launchSel.appendChild(h('option', { value: '' }, 'All launches'));
  for (const l of launches) launchSel.appendChild(h('option', { value: l.launch_id }, `${l.launch_name || 'launch'} · #${l.launch_number ?? '—'} (${l.item_count})`));
  if (_launch) launchSel.value = _launch;

  const graphCard = card('Co‑failure graph', { sub: 'items clustered by launch_group' });
  const legendCard = card('Groups', {});
  root.append(h('div', { class: 'grid', style: { gridTemplateColumns: '1fr 320px', alignItems: 'start' } }, graphCard, legendCard));
  const graphBody = graphCard.querySelector('.card-body');
  const legendBody = legendCard.querySelector('.card-body');

  async function load() {
    clear(graphBody).appendChild(loading());
    clear(legendBody);
    const d = await api.groups(app.project, _launch);
    if (d.rp) setDefects(d.rp.defects);
    clear(graphBody);
    if (!d.nodes.length) {
      graphBody.appendChild(emptyState('🕸️', 'No items', 'No test items for this selection.', 'analyzer.test_item'));
      return;
    }
    renderGroupList(legendBody, d.groups);
    renderForce(graphBody, d);
  }
  await load();
}

function renderGroupList(el, groups) {
  if (!groups.length) { el.appendChild(h('div', { class: 'muted' }, 'No launch_group rows.')); return; }
  const list = h('div', { class: 'grid', style: { gap: '8px' } });
  for (const g of groups) {
    list.appendChild(h('div', { style: { padding: '9px 11px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px' } },
      h('div', { class: 'flex between center' },
        h('span', { class: 'chip mono' }, `group ${g.group_id}`),
        g.dominant ? h('span', { class: 'badge', style: { background: 'color-mix(in srgb, var(--warning) 16%, transparent)', color: 'var(--warning)' } }, `🔥 burst · si ${fmt(g.si_prior, 2)}`) : h('span', { class: 'muted', style: { fontSize: '11px' } }, `si ${fmt(g.si_prior, 2)}`)),
      h('div', { class: 'flex gap-8 mt-8', style: { fontSize: '11px' } },
        h('span', { class: 'muted' }, `launch ${g.launch_id}`),
        h('span', { class: 'muted' }, `${g.member_count} members`),
        h('span', { class: 'mono muted' }, `fp ${String(g.fingerprint).slice(0, 8)}…`))));
  }
  el.appendChild(list);
}

function renderForce(el, d) {
  const width = el.clientWidth || 700, height = 520;
  const groupIds = [...new Set(d.nodes.map((n) => n.group_id).filter((x) => x != null))];
  const gx = new Map(groupIds.map((g, i) => [g, ((i + 0.5) / Math.max(1, groupIds.length)) * width]));

  const nodes = d.nodes.map((n) => ({ ...n }));
  const links = [];
  // connect items sharing the same group into a small clique-ish chain
  const byGroup = {};
  for (const n of nodes) if (n.group_id != null) (byGroup[n.group_id] ||= []).push(n);
  for (const arr of Object.values(byGroup)) {
    for (let i = 1; i < arr.length; i++) links.push({ source: arr[0].item_id, target: arr[i].item_id });
  }

  const svg = d3.create('svg').attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);
  const link = svg.append('g').attr('stroke', HAIRLINE).attr('stroke-opacity', 0.7)
    .selectAll('line').data(links).join('line').attr('stroke-width', 1.2);

  const node = svg.append('g').selectAll('g').data(nodes).join('g').style('cursor', 'grab');
  node.append('circle')
    .attr('r', 11)
    .attr('fill', (n) => defectColor(n.issue_type, n.label_group))
    .attr('stroke', (n) => (n.is_auto_analyzed ? 'var(--accent)' : '#0d0d0d'))
    .attr('stroke-width', (n) => (n.is_auto_analyzed ? 3 : 1.2));
  node.append('title').text((n) => `item ${n.item_id}\n${n.name || ''}\nlabel ${nodeLabelText(n)}\ngroup ${n.group_id ?? 'none'}${n.is_auto_analyzed ? '\n⭑ auto-analyzed' : ''}`);
  node.append('text').text((n) => n.item_id).attr('text-anchor', 'middle').attr('dy', 26)
    .attr('fill', INK2).attr('font-size', 10).attr('font-family', 'ui-monospace, monospace').style('pointer-events', 'none');

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((n) => n.item_id).distance(46).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-160))
    .force('x', d3.forceX((n) => (n.group_id != null ? gx.get(n.group_id) : width / 2)).strength(0.35))
    .force('y', d3.forceY(height / 2).strength(0.06))
    .force('collide', d3.forceCollide(20))
    .on('tick', () => {
      link.attr('x1', (l) => l.source.x).attr('y1', (l) => l.source.y).attr('x2', (l) => l.target.x).attr('y2', (l) => l.target.y);
      node.attr('transform', (n) => `translate(${n.x},${n.y})`);
    });
  node.call(d3.drag()
    .on('start', (ev, n) => { if (!ev.active) sim.alphaTarget(0.3).restart(); n.fx = n.x; n.fy = n.y; })
    .on('drag', (ev, n) => { n.fx = ev.x; n.fy = ev.y; })
    .on('end', (ev, n) => { if (!ev.active) sim.alphaTarget(0); n.fx = null; n.fy = null; }));

  el.appendChild(h('div', { class: 'flex gap-8 wrap mb-8' },
    ...['pb', 'ab', 'si', 'nd', 'ti'].map((g) => h('span', { class: 'flex center gap-8', style: { fontSize: '12px' } },
      h('span', { style: { width: '10px', height: '10px', borderRadius: '50%', background: defectColor(null, g) } }), defectName(null, g))),
    h('span', { class: 'muted', style: { fontSize: '12px' } }, '◯ accent ring = auto‑analyzed')));
  el.appendChild(svg.node());
}

function nodeLabelText(n) {
  if (!n.issue_type) return defectName(null, n.label_group);
  const info = defectInfo(n.issue_type);
  return info && info.name ? `${info.name} (${n.issue_type})` : n.issue_type;
}
