// Learning Loop — label_event timeline (colored by transition), model_artifact
// versions with active markers + gate metrics, metrics_daily area chart, and an
// optional live analyzer /health snapshot.
import { api } from '../api.js';
import {
  h, clear, card, emptyState, loading, setDefects, defectBadge, defectColor, defectName, defectInfo, idChip,
  shortTime, fmt, echartsBase, MUTED, INK, INK2, HAIRLINE, BAND, WARNING,
} from '../util.js';

export async function renderLoop(root, app) {
  clear(root);
  const evCard = card('Label events', { sub: 'ground-truth transitions over time' });
  const modelCard = card('Model artifacts', { sub: 'versions · gate metrics · active swap' });
  const metricsCard = card('Daily metrics', { sub: 'metrics_daily rollup' });
  const healthCard = card('Analyzer /health', { sub: 'live snapshot (optional)' });
  root.append(
    evCard,
    h('div', { class: 'grid grid-2', style: { marginTop: '16px' } }, modelCard, healthCard),
    h('div', { style: { marginTop: '16px' } }, metricsCard),
  );
  for (const c of [evCard, modelCard, metricsCard]) clear(c.querySelector('.card-body')).appendChild(loading());

  const d = await api.timeline(app.project);
  if (d.rp) setDefects(d.rp.defects);
  renderEvents(evCard.querySelector('.card-body'), d.label_events);
  renderModels(modelCard.querySelector('.card-body'), d.model_artifacts);
  renderMetrics(metricsCard.querySelector('.card-body'), d.metrics_daily);
  renderHealth(healthCard.querySelector('.card-body'));
}

function renderEvents(el, events) {
  clear(el);
  if (!events.length) {
    el.appendChild(emptyState('📈', 'No label events',
      'No label_event rows yet — the training log is empty. Events append on RP defect updates, accepted suggestions, and human edits.',
      'analyzer.label_event'));
    return;
  }
  const chart = h('div', { class: 'chart', style: { height: '260px' } });
  el.appendChild(chart);
  requestAnimationFrame(() => {
    const c = echarts.init(chart, null, { renderer: 'canvas' });
    const data = events.map((e) => ({
      value: [e.ts, e.new_group],
      meta: e,
      itemStyle: { color: defectColor(e.new_label, e.new_group) },
    }));
    const cats = ['pb', 'ab', 'si', 'nd', 'ti', 'none'];
    c.setOption({
      ...echartsBase(),
      grid: { left: 40, right: 24, top: 20, bottom: 40, containLabel: true },
      xAxis: { type: 'time', axisLabel: { color: MUTED }, splitLine: { show: false } },
      yAxis: { type: 'category', data: cats, axisLabel: { color: INK2, formatter: (g) => defectName(null, g) }, splitLine: { lineStyle: { color: HAIRLINE } } },
      tooltip: {
        ...echartsBase().tooltip,
        enterable: true,
        formatter: (p) => {
          const e = p.data.meta;
          const id = e.ui_url
            ? `<a href="${e.ui_url}" target="_blank" rel="noopener" style="color:${INK}">item ${e.item_id} ↗</a>`
            : `item ${e.item_id}`;
          return `<b>${id}</b><br>${transitionText(e.old_label, e.old_group) || '(new)'} → <b>${transitionText(e.new_label, e.new_group)}</b><br><span style="color:${MUTED}">${e.source} · ${shortTime(e.ts)}</span>`;
        },
      },
      series: [{ type: 'scatter', symbolSize: 15, data, encode: { x: 0, y: 1 }, itemStyle: { borderColor: '#ffffff', borderWidth: 1 } }],
    });
  });
  // transition list
  const list = h('div', { class: 'grid mt-16', style: { gap: '6px' } });
  for (const e of [...events].reverse().slice(0, 12)) {
    list.appendChild(h('div', { class: 'flex between center', style: { padding: '7px 11px', background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: '8px' } },
      h('div', { class: 'flex center gap-8 wrap' },
        h('span', { class: 'mono', style: { fontSize: '11px', color: 'var(--muted)' } }, idChip(`item ${e.item_id}`, e.ui_url, '')),
        e.old_label ? defectBadge(e.old_label, e.old_group) : h('span', { class: 'muted' }, '(new)'),
        h('span', { class: 'muted' }, '→'), defectBadge(e.new_label, e.new_group),
        h('span', { class: 'chip', style: { fontSize: '11px' } }, e.source)),
      h('span', { class: 'muted mono', style: { fontSize: '11px' } }, shortTime(e.ts))));
  }
  el.appendChild(list);
}

function transitionText(locator, group) {
  if (!locator) return '';
  const info = defectInfo(locator);
  return info && info.name ? `${info.name} (${locator})` : locator;
}

function renderModels(el, artifacts) {
  clear(el);
  if (!artifacts.length) {
    el.appendChild(emptyState('🧱', 'No model artifacts',
      'No model_artifact rows — the analyzer is running in cold-start / rule mode. Trained GBM + calibrator versions appear here after the first retrain.',
      'analyzer.model_artifact'));
    return;
  }
  const list = h('div', { class: 'grid', style: { gap: '8px' } });
  for (const a of artifacts) {
    const metrics = Object.entries(a.metrics || {}).slice(0, 6);
    list.appendChild(h('div', { style: { padding: '10px 12px', background: 'var(--surface-2)', border: '1px solid ' + (a.is_active ? 'var(--accent)' : 'var(--hairline)'), borderRadius: '8px' } },
      h('div', { class: 'flex between center wrap gap-8' },
        h('div', { class: 'flex center gap-8 wrap' },
          h('span', { class: 'chip mono-strong' }, a.kind),
          h('span', { class: 'mono', style: { fontSize: '12px' } }, a.version),
          h('span', { class: 'muted', style: { fontSize: '11px' } }, a.scope)),
        a.is_active ? h('span', { class: 'badge', style: { background: 'var(--accent-soft)', color: 'var(--accent)' } }, '● active') : h('span', { class: 'muted', style: { fontSize: '11px' } }, shortTime(a.trained_at))),
      metrics.length ? h('div', { class: 'flex gap-8 wrap mt-8' },
        ...metrics.map(([k, v]) => h('span', { class: 'chip', style: { fontSize: '11px' } },
          h('span', { class: 'muted' }, k), typeof v === 'number' ? fmt(v, 3) : String(v)))) : null,
      h('div', { class: 'muted mt-8', style: { fontSize: '11px' } }, `schema v${a.feature_schema_ver} · trained on ${a.n_events} events`)));
  }
  el.appendChild(list);
}

function renderMetrics(el, metrics) {
  clear(el);
  if (!metrics.length) {
    el.appendChild(emptyState('📊', 'No daily metrics',
      'metrics_daily has no rows for this project yet — the nightly rollup (analyzer_ng.ml.reporting) populates suggestions/accepted/corrected/abstained per day.',
      'analyzer.metrics_daily'));
    return;
  }
  const chart = h('div', { class: 'chart', style: { height: '300px' } });
  el.appendChild(chart);
  requestAnimationFrame(() => {
    const c = echarts.init(chart, null, { renderer: 'canvas' });
    const days = metrics.map((m) => m.day);
    const keys = [
      ['accepted', BAND.auto], ['corrected', WARNING],
      ['abstained', BAND.abstain], ['ignored', MUTED],
    ];
    c.setOption({
      ...echartsBase(),
      legend: { textStyle: { color: INK2 }, top: 0 },
      grid: { left: 30, right: 20, top: 34, bottom: 30, containLabel: true },
      xAxis: { type: 'category', data: days, axisLabel: { color: MUTED }, axisLine: { lineStyle: { color: HAIRLINE } } },
      yAxis: { type: 'value', axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: HAIRLINE } } },
      tooltip: { ...echartsBase().tooltip, trigger: 'axis' },
      series: keys.map(([k, col]) => ({
        name: k, type: 'line', stack: 'total', areaStyle: { opacity: 0.5 }, symbol: 'none',
        lineStyle: { width: 1.5 }, itemStyle: { color: col },
        data: metrics.map((m) => m[k] || 0),
      })),
    });
  });
}

async function renderHealth(el) {
  clear(el).appendChild(loading());
  try {
    const d = await api.analyzerHealth();
    clear(el);
    if (!d.configured) {
      el.appendChild(emptyState('🩺', 'Health probe not configured',
        'Set ANALYZER_HEALTH_URL on the inspector to show a live snapshot of the analyzer service /health here.', 'env: ANALYZER_HEALTH_URL'));
      return;
    }
    if (!d.reachable) {
      el.appendChild(emptyState('🔌', 'Analyzer unreachable', d.error || 'Could not reach the configured health URL.', 'ANALYZER_HEALTH_URL'));
      return;
    }
    el.appendChild(h('pre', { class: 'pattern', style: { whiteSpace: 'pre-wrap' } }, JSON.stringify(d.health, null, 2)));
  } catch (e) {
    clear(el).appendChild(emptyState('🩺', 'Health unavailable', String(e.message || e)));
  }
}
