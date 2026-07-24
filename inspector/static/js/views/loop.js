// Learning Loop — label_event timeline (colored by transition), model_artifact
// versions with active markers + gate metrics, metrics_daily area chart, and an
// optional live analyzer /health snapshot.
import { api } from '../api.js';
import {
  h, clear, card, emptyState, loading, setDefects, defectBadge, defectColor, defectName, defectInfo, idChip, icon,
  shortTime, fmt, pct, echartsBase, engDetails, MUTED, INK, INK2, HAIRLINE, BAND, WARNING,
} from '../util.js';

export async function renderLoop(root, app) {
  clear(root);
  const evCard = card('Label events', { sub: 'ground-truth transitions over time' });
  const modelCard = card('Model artifacts', { sub: 'versions · gate metrics · active swap' });
  const metricsCard = card('Daily metrics', { sub: 'metrics_daily rollup' });
  const healthCard = card('Analyzer /health', { sub: 'live snapshot (optional)' });
  healthCard.classList.add('side-panel');
  const matCard = card('Project maturity', { sub: 'cold-start → warm → hot · training-frame contribution' });
  root.append(
    evCard,
    h('div', { class: 'grid grid-side', style: { marginTop: '16px' } }, modelCard, healthCard),
    h('div', { style: { marginTop: '16px' } }, metricsCard),
    h('div', { style: { marginTop: '16px' } }, matCard),
  );
  for (const c of [evCard, modelCard, metricsCard, matCard]) clear(c.querySelector('.card-body')).appendChild(loading());

  const d = await api.timeline(app.project);
  if (d.rp) setDefects(d.rp.defects);
  renderEvents(evCard.querySelector('.card-body'), d.label_events);
  renderModels(modelCard.querySelector('.card-body'), d.model_artifacts);
  renderMetrics(metricsCard.querySelector('.card-body'), d.metrics_daily);
  renderMaturity(matCard.querySelector('.card-body'), d.maturity, app.project);
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

// --------------------------------------------------------------------------- //
// Project maturity — Cold → Warm → Hot band by the project's training-frame
// contribution (distinct labeled items), with live counters + honesty guards.
// Thresholds ride the payload (backend re-declares the spec constants); copy is
// verbatim from the design brief. Every number here is real DB data.
// --------------------------------------------------------------------------- //
const MAT_STAGES = [
  {
    key: 'cold', name: 'Cold', short: 'rules decide',
    decides: 'Rules decide. Stage A exact-hash inherit (needs identical error_hash repeats), '
      + 'seed catalog (prior ≥ 0.7 auto-applies), rule_cold fallback. The install-wide GBM serves '
      + 'with install calibration only.',
    matters: 'Run repeatability (identical error_hash), first human triage, seed KB.',
  },
  {
    key: 'warm', name: 'Warm', short: 'GBM training frame',
    decides: "The project's labels enter the install-wide GBM training frame (≥ 50). KB modes "
      + 'accumulate support/purity → confirmed (purity ≥ 0.95, support ≥ 10), unlocking the KB '
      + 'short-circuit.',
    matters: 'label_event volume & quality (human > auto), AA enabled (feature snapshots), signature stability.',
  },
  {
    key: 'hot', name: 'Hot', short: 'per-project calibration',
    decides: 'Per-project isotonic calibration (≥ 300) — probabilities reflect THIS project. '
      + 'Confirmed modes + dense Stage A.',
    matters: 'Calibration, confirmed modes, exact-match density.',
  },
];

function renderMaturity(el, m, projectId) {
  clear(el);
  if (!m) {
    el.appendChild(emptyState('📊', 'Maturity unavailable',
      'The timeline payload carried no maturity block for this project.', 'analyzer.label_event'));
    return;
  }
  const warm = m.gbm_min_events, hot = m.calib_min_events;
  const labeled = m.labeled_items || 0;
  const stageIdx = { cold: 0, warm: 1, hot: 2 }[m.stage] ?? 0;

  // ---- Stepper (position marked) ----
  const stepper = h('div', { class: 'stepper' });
  MAT_STAGES.forEach((s, i) => {
    const active = i === stageIdx;
    const range = s.key === 'cold' ? `0–${warm - 1}` : s.key === 'warm' ? `${warm}–${hot - 1}` : `≥ ${hot}`;
    stepper.appendChild(h('div', { class: 'stage-pill' + (active ? ' active' : '') },
      h('div', { class: 's-k' }, `${s.name.toUpperCase()} · ${s.short}`),
      h('div', { class: 's-v' }, `${range} labeled items`),
      active ? h('div', { class: 'mat-here' }, '● this project') : null,
      h('div', { class: 's-flow', 'aria-hidden': 'true' }, icon('arrowRight', { size: 15 }))));
  });
  el.appendChild(stepper);

  // ---- Progress meter (ticks at Warm=50 and Hot=300) ----
  const fillPct = Math.min(100, (labeled / hot) * 100);
  const noun = labeled === 1 ? 'labeled item' : 'labeled items';
  const toNext = m.stage === 'cold' ? `${warm - labeled} to Warm`
    : m.stage === 'warm' ? `${hot - labeled} to Hot`
      : 'Hot band reached';
  el.appendChild(h('div', { class: 'si-meter', style: { marginBottom: '18px', maxWidth: 'none' } },
    h('div', { class: 'si-label flex between' },
      h('span', {}, 'training-frame position'),
      h('span', { class: 'si-val', style: { textTransform: 'none', letterSpacing: 0 } },
        `${fmt(labeled)} ${noun} · ${toNext}`)),
    h('div', { class: 'si-track' },
      h('div', { class: 'si-fill', style: { width: fillPct + '%', background: matColor(m.stage) } }),
      h('div', { class: 'si-tick', style: { left: (warm / hot * 100) + '%' } })),
    h('div', { class: 'si-scale' },
      h('span', {}, '0'),
      h('span', {}, `${warm} · Warm`),
      h('span', {}, `${hot} · Hot`))));

  // ---- Per-stage "who decides / what matters" (active highlighted) ----
  const cols = h('div', { class: 'grid grid-3', style: { marginBottom: '16px' } });
  MAT_STAGES.forEach((s, i) => {
    const active = i === stageIdx;
    cols.appendChild(h('div', { class: 'mat-stage' + (active ? ' active' : '') },
      h('div', { class: 'mat-stage-head' },
        h('span', { class: 'mat-badge', style: { background: matColor(s.key) } }),
        h('span', { class: 'mat-stage-name' }, s.name),
        active ? h('span', { class: 'mat-now' }, 'current') : null),
      h('div', { class: 'mat-decides' }, s.decides),
      h('div', { class: 'mat-key' }, h('span', { class: 'mat-key-lbl' }, 'Key: '), s.matters)));
  });
  el.appendChild(cols);

  // ---- Live counters (real DB, per selected project) ----
  const cov = m.embedded_pct == null ? '—' : pct(m.embedded_pct, 1);
  const stats = h('div', { class: 'stat-row', style: { marginBottom: '14px' } },
    stat(fmt(m.label_events), 'label_events', `${fmt(m.human_events)} human-sourced`),
    stat(fmt(labeled), 'labeled items', 'distinct · training frame'),
    stat(`${fmt(m.modes_confirmed)} / ${fmt(m.modes_candidate)}`, 'KB modes', 'confirmed / candidate'),
    stat(cov, 'embedded coverage', `${fmt(m.embedded)} of ${fmt(m.signatures)} signatures`));
  el.appendChild(stats);

  // ---- Honesty guards (only when the machinery disagrees with the band) ----
  const notes = [];
  if (labeled === 0) {
    notes.push('No label_event rows for this project yet — Cold with no triage. The install-wide '
      + 'GBM still serves (install calibration), but this project contributes nothing to the frame.');
  }
  if (m.stage === 'hot' && !m.project_calibrator) {
    notes.push(`${fmt(labeled)} labeled items ≥ ${hot}, but NO per-project isotonic calibrator has `
      + 'shipped yet (model_artifact has no active calib row for this project) — probabilities are '
      + 'still install-wide / raw. Hot by volume, not yet by machinery.');
  }
  if (m.stage === 'hot' && m.project_calibrator && m.modes_confirmed === 0) {
    notes.push(`Per-project calibrator active (${m.project_calibrator}), but 0 KB modes are confirmed `
      + `(${fmt(m.modes_candidate)} candidate) — the KB short-circuit is not unlocked. Modes reach `
      + 'confirmed at purity ≥ 0.95 & support ≥ 10.');
  }
  if (m.stage === 'warm' && m.modes_confirmed === 0 && m.modes_candidate > 0) {
    notes.push(`${fmt(m.modes_candidate)} KB modes are still candidate (0 confirmed) — no KB `
      + 'short-circuit yet; decisions run through the install-wide GBM.');
  }
  if (notes.length) {
    el.appendChild(h('div', { class: 'honesty' },
      h('span', { class: 'h-tag' }, 'reality check'),
      ...notes.map((t) => h('div', { class: 'note', style: { marginTop: '4px' } }, t))));
  }

  // ---- Technical Details (provenance + serving machinery) ----
  el.appendChild(engDetails('maturity',
    h('div', { class: 'kv' },
      h('dt', {}, 'band metric'), h('dd', {}, 'distinct labeled items (non-ti) — one training example '
        + 'per item; raw label_event churn runs higher (an item re-triaged N times is one example)'),
      h('dt', {}, 'Warm floor'), h('dd', {}, h('span', { class: 'mono' }, `GBM_MIN_EVENTS = ${warm}`),
        ' — src/analyzer_ng/ml/trainer.py (cold-model floor)'),
      h('dt', {}, 'Hot floor'), h('dd', {}, h('span', { class: 'mono' }, `CALIB_MIN_EVENTS = ${hot}`),
        ' — src/analyzer_ng/ml/calibration.py (per-project isotonic)'),
      h('dt', {}, 'install-wide GBM'), h('dd', {}, m.install_gbm
        ? h('span', {}, h('span', { class: 'mono' }, m.install_gbm),
          m.install_gbm_events != null ? ` · trained on ${fmt(m.install_gbm_events)} events` : '')
        : h('span', { class: 'muted' }, 'none active — cold-start / rule mode')),
      h('dt', {}, 'project calibrator'), h('dd', {}, m.project_calibrator
        ? h('span', {}, h('span', { class: 'mono' }, m.project_calibrator),
          m.project_calibrator_events != null ? ` · ${fmt(m.project_calibrator_events)} events` : '')
        : h('span', { class: 'muted' }, 'none — serving install-wide / raw calibration')))));
}

function matColor(stage) {
  return stage === 'hot' ? 'var(--good)' : stage === 'warm' ? 'var(--band-suggest)' : 'var(--band-abstain)';
}

function stat(v, l, sub) {
  return h('div', { class: 'st' },
    h('div', { class: 'v' }, v),
    h('div', { class: 'l' }, l),
    sub ? h('div', { class: 'muted', style: { fontSize: '10.5px', marginTop: '2px' } }, sub) : null);
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
