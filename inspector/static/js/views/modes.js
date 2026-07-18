// Modes Map — PCA-3D (numpy SVD on the backend) of failure_signature embeddings
// + failure_mode centroids as stars. Honest empty-state when embeddings absent;
// graceful 2D fallback when the projection has < 3 usable dimensions.
import { api } from '../api.js';
import {
  h, clear, card, emptyState, loading, labelColor, labelName, labelBadge, fmt, pct,
  MUTED, INK, INK2, HAIRLINE,
} from '../util.js';

export async function renderModes(root, app) {
  clear(root);
  const c = card('Modes Map', { sub: 'PCA of 384‑dim embeddings · items ● / centroids ★' });
  root.appendChild(c);
  const body = c.querySelector('.card-body');
  clear(body).appendChild(loading('Projecting embeddings…'));

  const d = await api.modes3d(app.project);
  clear(body);
  if (!d.available) {
    const diag = d.diagnostics || {};
    body.appendChild(emptyState('🪐', 'Embedding space not available yet', d.reason,
      'analyzer.failure_signature.emb (halfvec 384)'));
    body.appendChild(h('div', { class: 'flex gap-8 wrap', style: { justifyContent: 'center', marginTop: '4px' } },
      h('span', { class: 'chip' }, `signatures ${diag.total_signatures ?? 0}`),
      h('span', { class: 'chip' }, `embedded ${diag.embedded_signatures ?? 0}`),
      h('span', { class: 'chip' }, `mode centroids ${diag.modes_with_centroid ?? 0}`)));
    return;
  }

  // header stats + legend
  const evr = d.explained_variance_ratio || [];
  c.querySelector('.card-sub').textContent =
    `${d.n_items} items · ${d.n_modes} centroids · ${d.dimensions}D · variance ${evr.map((v) => pct(v, 0)).join(' / ')}`;
  body.appendChild(legend());

  const chart = h('div', { class: 'chart', style: { height: '560px' } });
  body.appendChild(chart);
  requestAnimationFrame(() => (d.dimensions >= 3 ? draw3d(chart, d) : draw2d(chart, d)));

  if (d.dimensions < 3) {
    body.appendChild(h('p', { class: 'note', style: { marginTop: '8px' } },
      `Only ${d.dimensions} principal dimension(s) carry variance (the embedded set is small/degenerate), so the map falls back to 2D — it becomes 3D once more distinct embeddings exist.`));
  }
}

function legend() {
  const wrap = h('div', { class: 'flex gap-8 wrap mb-8' });
  for (const g of ['pb', 'ab', 'si', 'nd', 'ti']) {
    wrap.appendChild(h('span', { class: 'flex center gap-8', style: { fontSize: '12px' } },
      h('span', { style: { width: '10px', height: '10px', borderRadius: '50%', background: labelColor(g) } }),
      labelName(g)));
  }
  wrap.appendChild(h('span', { class: 'muted', style: { fontSize: '12px', marginLeft: '8px' } }, '★ = mode centroid'));
  return wrap;
}

function seriesFor(points, dims) {
  const items = points.filter((p) => p.kind === 'item');
  const modes = points.filter((p) => p.kind === 'mode');
  const coord = (p) => (dims >= 3 ? [p.x, p.y, p.z] : [p.x, p.y]);
  const byGroup = {};
  for (const p of items) (byGroup[p.label_group] ||= []).push(p);
  return { items, modes, coord, byGroup };
}

function draw3d(el, d) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  const { modes, coord, byGroup } = seriesFor(d.points, 3);
  const series = Object.entries(byGroup).map(([g, pts]) => ({
    type: 'scatter3D', name: labelName(g),
    data: pts.map((p) => ({ value: coord(p), meta: p })),
    symbolSize: 9, itemStyle: { color: labelColor(g), opacity: 0.9 },
  }));
  if (modes.length) series.push({
    type: 'scatter3D', name: 'centroid',
    data: modes.map((p) => ({ value: coord(p), meta: p, itemStyle: { color: labelColor(p.label_group) } })),
    symbol: 'diamond', symbolSize: 20,
    itemStyle: { opacity: 1, borderColor: '#fff', borderWidth: 1 },
  });
  chart.setOption({
    tooltip: { backgroundColor: '#26292d', borderColor: HAIRLINE, textStyle: { color: INK }, formatter: tip },
    xAxis3D: axis('PC1'), yAxis3D: axis('PC2'), zAxis3D: axis('PC3'),
    grid3D: {
      viewControl: { autoRotate: true, autoRotateSpeed: 6, distance: 190 },
      axisLine: { lineStyle: { color: HAIRLINE } }, splitLine: { lineStyle: { color: '#232527' } },
      environment: '#101112',
    },
    series,
  });
}

function draw2d(el, d) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  const { modes, coord, byGroup } = seriesFor(d.points, 2);
  const series = Object.entries(byGroup).map(([g, pts]) => ({
    type: 'scatter', name: labelName(g),
    data: pts.map((p) => ({ value: coord(p), meta: p })),
    symbolSize: 14, itemStyle: { color: labelColor(g), opacity: 0.9, borderColor: '#0d0d0d', borderWidth: 1 },
  }));
  if (modes.length) series.push({
    type: 'scatter', name: 'centroid',
    data: modes.map((p) => ({ value: coord(p), meta: p, itemStyle: { color: labelColor(p.label_group) } })),
    symbol: 'diamond', symbolSize: 24,
    itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
  });
  chart.setOption({
    tooltip: { backgroundColor: '#26292d', borderColor: HAIRLINE, textStyle: { color: INK }, formatter: tip },
    grid: { left: 30, right: 20, top: 20, bottom: 30, containLabel: true },
    xAxis: { name: 'PC1', nameTextStyle: { color: MUTED }, axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: HAIRLINE } } },
    yAxis: { name: 'PC2', nameTextStyle: { color: MUTED }, axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: HAIRLINE } } },
    series,
  });
}

function axis(name) {
  return { name, nameTextStyle: { color: MUTED }, axisLabel: { color: MUTED }, axisLine: { lineStyle: { color: HAIRLINE } } };
}
function tip(p) {
  const m = p.data.meta;
  if (m.kind === 'mode') {
    return `<b>★ ${m.name}</b><br>label ${m.label || '—'} · status ${m.status}<br>purity ${fmt(m.purity, 2)} · support ${m.support}`;
  }
  return `<b>item ${m.item_id}</b><br>${m.name || ''}<br>label ${m.label || 'ti'}${m.is_auto_analyzed ? ' · ⭑ auto' : ''}`;
}
