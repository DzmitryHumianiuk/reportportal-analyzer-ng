// Learning Loop — label_event timeline (colored by transition), model_artifact
// versions with active markers + gate metrics, metrics_daily area chart, the
// project maturity band, and an optional live analyzer /health snapshot.

import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type {
  AnalyzerHealthResponse,
  LabelEvent,
  Maturity,
  MetricsDay,
  ModelArtifact,
  TimelineResponse,
} from '../../app/types';
import {
  Card,
  ChipLink,
  DefectBadge,
  EChart,
  EmptyState,
  EngDetails,
  Loading,
  RpIcon,
  SiMeter,
} from '../../components';
import { BAND, HAIRLINE, INK, INK2, MUTED, WARNING } from '../../lib/colors';
import { echartsBase } from '../../lib/echartsTheme';
import { fmt, pct, shortTime } from '../../lib/format';
import { esc, safeUrl } from '../../lib/html';
import { defectColor, defectInfo, defectName, setDefects } from '../../lib/labels';
import './loop.css';

// --------------------------------------------------------------------------- //
// View
// --------------------------------------------------------------------------- //

export default function Loop() {
  const { project, refreshTick } = useApp();
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [failure, setFailure] = useState<Error | null>(null);

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setData(null);
    setFailure(null);
    void (async () => {
      try {
        const d = await api.timeline(project);
        if (cancelled) return;
        if (d.rp) setDefects(d.rp.defects);
        setData(d);
      } catch (e) {
        if (!cancelled) setFailure(e as Error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  // A failed load is the view's error state, owned by ViewErrorBoundary.
  if (failure) throw failure;

  return (
    <>
      <Card title="Label events" sub="ground-truth transitions over time">
        {data ? <Events events={data.label_events} /> : <Loading />}
      </Card>
      <div className="grid grid-side loop-block">
        <Card title="Model artifacts" sub="versions · gate metrics · active swap">
          {data ? <Models artifacts={data.model_artifacts} /> : <Loading />}
        </Card>
        <Card title="Analyzer /health" sub="live snapshot (optional)" className="side-panel">
          <Health refreshTick={refreshTick} />
        </Card>
      </div>
      <div className="loop-block">
        <Card title="Daily metrics" sub="metrics_daily rollup">
          {data ? <Metrics metrics={data.metrics_daily} /> : <Loading />}
        </Card>
      </div>
      <div className="loop-block">
        <Card
          title="Project maturity"
          sub="cold-start → warm → hot · training-frame contribution"
        >
          {data ? <MaturityBlock m={data.maturity ?? null} /> : <Loading />}
        </Card>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- //
// Label events
// --------------------------------------------------------------------------- //

const CATS = ['pb', 'ab', 'si', 'nd', 'ti', 'none'];

function transitionText(locator?: string | null): string {
  if (!locator) return '';
  const info = defectInfo(locator);
  return info && info.name ? `${info.name} (${locator})` : locator;
}

function eventTooltip(params: unknown): string {
  const e = (params as { data?: { meta?: LabelEvent } }).data?.meta;
  if (!e) return '';
  // Only a real web link becomes an anchor; anything else stays plain text.
  const url = safeUrl(e.ui_url);
  const id = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener" style="color:${INK}">item ${esc(e.item_id)} ↗</a>`
    : `item ${esc(e.item_id)}`;
  const from = esc(transitionText(e.old_label)) || '(new)';
  const to = esc(transitionText(e.new_label));
  const foot = `${esc(String(e.source ?? ''))} · ${esc(shortTime(e.ts))}`;
  return `<b>${id}</b><br>${from} → <b>${to}</b><br><span style="color:${MUTED}">${foot}</span>`;
}

function buildEventsOption(events: LabelEvent[]) {
  const base = echartsBase();
  const data = events.map((e) => ({
    value: [e.ts ?? null, e.new_group ?? null],
    meta: e,
    itemStyle: { color: defectColor(e.new_label, e.new_group) },
  }));
  return {
    grid: { left: 40, right: 24, top: 20, bottom: 40, containLabel: true },
    xAxis: { type: 'time', axisLabel: { color: MUTED }, splitLine: { show: false } },
    yAxis: {
      type: 'category',
      data: CATS,
      axisLabel: { color: INK2, formatter: (g: string) => defectName(null, g) },
      splitLine: { lineStyle: { color: HAIRLINE } },
    },
    tooltip: { ...base.tooltip, enterable: true, formatter: eventTooltip },
    series: [
      {
        type: 'scatter',
        symbolSize: 15,
        data,
        encode: { x: 0, y: 1 },
        itemStyle: { borderColor: '#ffffff', borderWidth: 1 },
      },
    ],
  };
}

function Events({ events }: { events: LabelEvent[] }) {
  const option = useMemo(() => buildEventsOption(events), [events]);

  if (!events.length) {
    return (
      <EmptyState
        icon="📈"
        title="No label events"
        body="No label_event rows yet — the training log is empty. Events append on RP defect updates, accepted suggestions, and human edits."
        tag="analyzer.label_event"
      />
    );
  }

  const latest = [...events].reverse().slice(0, 12);
  return (
    <>
      <EChart option={option} height={260} />
      <div className="grid mt-16 loop-ev-list">
        {latest.map((e, i) => (
          <div className="flex between center loop-ev" key={`${e.item_id}-${e.ts ?? ''}-${i}`}>
            <div className="flex center gap-8 wrap">
              <span className="mono loop-ev-id">
                <ChipLink label={`item ${e.item_id}`} url={e.ui_url} className="" />
              </span>
              {e.old_label ? (
                <DefectBadge locator={e.old_label} group={e.old_group} />
              ) : (
                <span className="muted">(new)</span>
              )}
              <span className="muted">→</span>
              <DefectBadge locator={e.new_label} group={e.new_group} />
              <span className="chip">{e.source}</span>
            </div>
            <span className="muted mono loop-ev-time">{shortTime(e.ts)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- //
// Model artifacts
// --------------------------------------------------------------------------- //

function Models({ artifacts }: { artifacts: ModelArtifact[] }) {
  if (!artifacts.length) {
    return (
      <EmptyState
        icon="🧱"
        title="No model artifacts"
        body="No model_artifact rows — the analyzer is running in cold-start / rule mode. Trained GBM + calibrator versions appear here after the first retrain."
        tag="analyzer.model_artifact"
      />
    );
  }
  return (
    <div className="grid loop-art-list">
      {artifacts.map((a, i) => {
        const metrics = Object.entries(a.metrics || {}).slice(0, 6);
        return (
          <div
            className={`loop-art${a.is_active ? ' active' : ''}`}
            key={`${a.kind}-${a.version ?? ''}-${i}`}
          >
            <div className="flex between center wrap gap-8">
              <div className="flex center gap-8 wrap">
                <span className="chip mono-strong">{a.kind}</span>
                <span className="mono loop-art-ver">{a.version}</span>
                <span className="muted loop-art-scope">{a.scope}</span>
              </div>
              {a.is_active ? (
                <span className="badge loop-badge-active">● active</span>
              ) : (
                <span className="muted loop-art-when">{shortTime(a.trained_at)}</span>
              )}
            </div>
            {metrics.length ? (
              <div className="flex gap-8 wrap mt-8">
                {metrics.map(([k, v]) => (
                  <span className="chip" key={k}>
                    <span className="muted">{k}</span>
                    {typeof v === 'number' ? fmt(v, 3) : String(v)}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="muted mt-8 loop-art-foot">
              {`schema v${a.feature_schema_ver} · trained on ${a.n_events} events`}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Daily metrics
// --------------------------------------------------------------------------- //

type MetricKey = 'accepted' | 'corrected' | 'abstained' | 'ignored';

const METRIC_SERIES: Array<[MetricKey, string]> = [
  ['accepted', BAND.auto],
  ['corrected', WARNING],
  ['abstained', BAND.abstain],
  ['ignored', MUTED],
];

function buildMetricsOption(metrics: MetricsDay[]) {
  const base = echartsBase();
  return {
    legend: { textStyle: { color: INK2 }, top: 0 },
    grid: { left: 30, right: 20, top: 34, bottom: 30, containLabel: true },
    xAxis: {
      type: 'category',
      data: metrics.map((m) => m.day),
      axisLabel: { color: MUTED },
      axisLine: { lineStyle: { color: HAIRLINE } },
    },
    yAxis: { type: 'value', axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: HAIRLINE } } },
    tooltip: { ...base.tooltip, trigger: 'axis' },
    series: METRIC_SERIES.map(([k, col]) => ({
      name: k,
      type: 'line',
      stack: 'total',
      areaStyle: { opacity: 0.5 },
      symbol: 'none',
      lineStyle: { width: 1.5 },
      itemStyle: { color: col },
      data: metrics.map((m) => m[k] || 0),
    })),
  };
}

function Metrics({ metrics }: { metrics: MetricsDay[] }) {
  const option = useMemo(() => buildMetricsOption(metrics), [metrics]);

  if (!metrics.length) {
    return (
      <EmptyState
        icon="📊"
        title="No daily metrics"
        body="metrics_daily has no rows for this project yet — the nightly rollup (analyzer_ng.ml.reporting) populates suggestions/accepted/corrected/abstained per day."
        tag="analyzer.metrics_daily"
      />
    );
  }
  return <EChart option={option} height={300} />;
}

// --------------------------------------------------------------------------- //
// Project maturity — Cold → Warm → Hot band by the project's training-frame
// contribution (distinct labeled items), with live counters + honesty guards.
// Thresholds ride the payload (backend re-declares the spec constants); copy is
// verbatim from the design brief. Every number here is real DB data.
// --------------------------------------------------------------------------- //

interface MatStage {
  key: 'cold' | 'warm' | 'hot';
  name: string;
  short: string;
  decides: string;
  matters: string;
}

const MAT_STAGES: MatStage[] = [
  {
    key: 'cold',
    name: 'Cold',
    short: 'rules decide',
    decides:
      'Rules decide. Stage A exact-hash inherit (needs identical error_hash repeats), ' +
      'seed catalog (prior ≥ 0.7 auto-applies), rule_cold fallback. The install-wide GBM serves ' +
      'with install calibration only.',
    matters: 'Run repeatability (identical error_hash), first human triage, seed KB.',
  },
  {
    key: 'warm',
    name: 'Warm',
    short: 'GBM training frame',
    decides:
      "The project's labels enter the install-wide GBM training frame (≥ 50). KB modes " +
      'accumulate support/purity → confirmed (purity ≥ 0.95, support ≥ 10), unlocking the KB ' +
      'short-circuit.',
    matters:
      'label_event volume & quality (human > auto), AA enabled (feature snapshots), signature stability.',
  },
  {
    key: 'hot',
    name: 'Hot',
    short: 'per-project calibration',
    decides:
      'Per-project isotonic calibration (≥ 300) — probabilities reflect THIS project. ' +
      'Confirmed modes + dense Stage A.',
    matters: 'Calibration, confirmed modes, exact-match density.',
  },
];

const STAGE_IDX: Record<string, number> = { cold: 0, warm: 1, hot: 2 };

// The payload always carries the thresholds (the backend re-declares the spec
// constants). These fallbacks only cover a payload that omits them, and they are
// the same numbers the drawer cites: trainer.py GBM_MIN_EVENTS = 50 and
// calibration.py CALIB_MIN_EVENTS = 300.
const GBM_MIN_EVENTS = 50;
const CALIB_MIN_EVENTS = 300;

function matColor(stage?: string | null): string {
  return stage === 'hot'
    ? 'var(--good)'
    : stage === 'warm'
      ? 'var(--band-suggest)'
      : 'var(--band-abstain)';
}

function Stat({ value, label, sub }: { value: ReactNode; label: string; sub?: ReactNode }) {
  return (
    <div className="st">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {sub ? <div className="muted loop-stat-sub">{sub}</div> : null}
    </div>
  );
}

function MaturityBlock({ m }: { m: Maturity | null }) {
  if (!m) {
    return (
      <EmptyState
        icon="📊"
        title="Maturity unavailable"
        body="The timeline payload carried no maturity block for this project."
        tag="analyzer.label_event"
      />
    );
  }

  const warm = m.gbm_min_events ?? GBM_MIN_EVENTS;
  const hot = m.calib_min_events ?? CALIB_MIN_EVENTS;
  const labeled = m.labeled_items || 0;
  const stageIdx = STAGE_IDX[m.stage ?? ''] ?? 0;

  const noun = labeled === 1 ? 'labeled item' : 'labeled items';
  const toNext =
    m.stage === 'cold'
      ? `${warm - labeled} to Warm`
      : m.stage === 'warm'
        ? `${hot - labeled} to Hot`
        : 'Hot band reached';

  const cov = m.embedded_pct == null ? '—' : pct(m.embedded_pct, 1);

  // ---- Honesty guards (only when the machinery disagrees with the band) ----
  const notes: string[] = [];
  if (labeled === 0) {
    notes.push(
      'No label_event rows for this project yet — Cold with no triage. The install-wide ' +
        'GBM still serves (install calibration), but this project contributes nothing to the frame.',
    );
  }
  if (m.stage === 'hot' && !m.project_calibrator) {
    notes.push(
      `${fmt(labeled)} labeled items ≥ ${hot}, but NO per-project isotonic calibrator has ` +
        'shipped yet (model_artifact has no active calib row for this project) — probabilities are ' +
        'still install-wide / raw. Hot by volume, not yet by machinery.',
    );
  }
  if (m.stage === 'hot' && m.project_calibrator && m.modes_confirmed === 0) {
    notes.push(
      `Per-project calibrator active (${m.project_calibrator}), but 0 KB modes are confirmed ` +
        `(${fmt(m.modes_candidate)} candidate) — the KB short-circuit is not unlocked. Modes reach ` +
        'confirmed at purity ≥ 0.95 & support ≥ 10.',
    );
  }
  if (m.stage === 'warm' && m.modes_confirmed === 0 && (m.modes_candidate ?? 0) > 0) {
    notes.push(
      `${fmt(m.modes_candidate)} KB modes are still candidate (0 confirmed) — no KB ` +
        'short-circuit yet; decisions run through the install-wide GBM.',
    );
  }

  return (
    <>
      {/* ---- Stepper (position marked) ---- */}
      <div className="stepper">
        {MAT_STAGES.map((s, i) => {
          const active = i === stageIdx;
          const range =
            s.key === 'cold'
              ? `0–${warm - 1}`
              : s.key === 'warm'
                ? `${warm}–${hot - 1}`
                : `≥ ${hot}`;
          return (
            <div className={`stage-pill${active ? ' active' : ''}`} key={s.key}>
              <div className="s-k">{`${s.name.toUpperCase()} · ${s.short}`}</div>
              <div className="s-v">{`${range} labeled items`}</div>
              {active ? <div className="mat-here">● this project</div> : null}
              <div className="s-flow" aria-hidden="true">
                <RpIcon name="arrowRight" size={15} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ---- Progress meter (ticks at Warm and Hot) ---- */}
      <div className="loop-meter">
        <SiMeter
          label="training-frame position"
          value={`${fmt(labeled)} ${noun} · ${toNext}`}
          ratio={labeled / hot}
          color={matColor(m.stage)}
          tickRatio={warm / hot}
          scale={['0', `${warm} · Warm`, `${hot} · Hot`]}
        />
      </div>

      {/* ---- Per-stage "who decides / what matters" (active highlighted) ---- */}
      <div className="grid grid-3 loop-cols">
        {MAT_STAGES.map((s, i) => {
          const active = i === stageIdx;
          return (
            <div className={`mat-stage${active ? ' active' : ''}`} key={s.key}>
              <div className="mat-stage-head">
                <span className="mat-badge" style={{ background: matColor(s.key) }} />
                <span className="mat-stage-name">{s.name}</span>
                {active ? <span className="mat-now">current</span> : null}
              </div>
              <div className="mat-decides">{s.decides}</div>
              <div className="mat-key">
                <span className="mat-key-lbl">Key: </span>
                {s.matters}
              </div>
            </div>
          );
        })}
      </div>

      {/* ---- Live counters (real DB, per selected project) ---- */}
      <div className="stat-row loop-stats">
        <Stat
          value={fmt(m.label_events)}
          label="label_events"
          sub={`${fmt(m.human_events)} human-sourced`}
        />
        <Stat value={fmt(labeled)} label="labeled items" sub="distinct · training frame" />
        <Stat
          value={`${fmt(m.modes_confirmed)} / ${fmt(m.modes_candidate)}`}
          label="KB modes"
          sub="confirmed / candidate"
        />
        <Stat
          value={cov}
          label="embedded coverage"
          sub={`${fmt(m.embedded)} of ${fmt(m.signatures)} signatures`}
        />
      </div>

      {notes.length ? (
        <div className="honesty">
          <span className="h-tag">reality check</span>
          {notes.map((t) => (
            <div className="note loop-note" key={t}>
              {t}
            </div>
          ))}
        </div>
      ) : null}

      {/* ---- Technical Details (provenance + serving machinery) ---- */}
      <EngDetails storageKey="maturity">
        <dl className="kv">
          <dt>band metric</dt>
          <dd>
            distinct labeled items (non-ti) — one training example per item; raw label_event churn
            runs higher (an item re-triaged N times is one example)
          </dd>
          <dt>Warm floor</dt>
          <dd>
            <span className="mono">{`GBM_MIN_EVENTS = ${warm}`}</span>
            {' — src/analyzer_ng/ml/trainer.py (cold-model floor)'}
          </dd>
          <dt>Hot floor</dt>
          <dd>
            <span className="mono">{`CALIB_MIN_EVENTS = ${hot}`}</span>
            {' — src/analyzer_ng/ml/calibration.py (per-project isotonic)'}
          </dd>
          <dt>install-wide GBM</dt>
          <dd>
            {m.install_gbm ? (
              <span>
                <span className="mono">{m.install_gbm}</span>
                {m.install_gbm_events != null
                  ? ` · trained on ${fmt(m.install_gbm_events)} events`
                  : ''}
              </span>
            ) : (
              <span className="muted">none active — cold-start / rule mode</span>
            )}
          </dd>
          <dt>project calibrator</dt>
          <dd>
            {m.project_calibrator ? (
              <span>
                <span className="mono">{m.project_calibrator}</span>
                {m.project_calibrator_events != null
                  ? ` · ${fmt(m.project_calibrator_events)} events`
                  : ''}
              </span>
            ) : (
              <span className="muted">none — serving install-wide / raw calibration</span>
            )}
          </dd>
        </dl>
      </EngDetails>
    </>
  );
}

// --------------------------------------------------------------------------- //
// Analyzer /health (optional live probe)
// --------------------------------------------------------------------------- //

type HealthState =
  | { status: 'loading' }
  | { status: 'ready'; data: AnalyzerHealthResponse }
  | { status: 'error'; message: string };

function Health({ refreshTick }: { refreshTick: number }) {
  const [state, setState] = useState<HealthState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      try {
        const d = await api.analyzerHealth();
        if (!cancelled) setState({ status: 'ready', data: d });
      } catch (e) {
        if (!cancelled) {
          setState({ status: 'error', message: String((e as Error)?.message || e) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  if (state.status === 'loading') return <Loading />;
  if (state.status === 'error') {
    return <EmptyState icon="🩺" title="Health unavailable" body={state.message} />;
  }

  const d = state.data;
  if (!d.configured) {
    return (
      <EmptyState
        icon="🩺"
        title="Health probe not configured"
        body="Set ANALYZER_HEALTH_URL on the inspector to show a live snapshot of the analyzer service /health here."
        tag="env: ANALYZER_HEALTH_URL"
      />
    );
  }
  if (!d.reachable) {
    return (
      <EmptyState
        icon="🔌"
        title="Analyzer unreachable"
        body={d.error || 'Could not reach the configured health URL.'}
        tag="ANALYZER_HEALTH_URL"
      />
    );
  }
  return <pre className="pattern loop-health">{JSON.stringify(d.health, null, 2)}</pre>;
}
