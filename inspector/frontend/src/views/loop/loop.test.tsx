// IMPORTANT: test/utils must be imported first — it installs the echarts double.
import { mockApi, mockECharts, renderWithApp } from '../../test/utils';

import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setDefects } from '../../lib/labels';
import Loop from './Loop';

interface SeriesOption {
  name?: string;
  type?: string;
  stack?: string;
  symbolSize?: number;
  areaStyle?: { opacity?: number };
  itemStyle?: { color?: string };
  data?: unknown[];
}

interface CapturedOption {
  series?: SeriesOption[];
  yAxis?: { type?: string; data?: string[] };
  xAxis?: { type?: string; data?: string[] };
  tooltip?: { enterable?: boolean; trigger?: string; formatter?: (p: unknown) => string };
  legend?: { top?: number };
}

const RP_BLOCK = {
  project_id: 1,
  defects: {},
  status: { configured: false, reachable: false, note: 'RP names: —' },
};

const LABEL_EVENTS = [
  {
    item_id: 11,
    ui_url: 'https://rp.example/ui/#demo/launches/all/5/11',
    ts: '2026-08-20T10:00:00Z',
    old_label: null,
    old_group: null,
    new_label: 'pb001',
    new_group: 'pb',
    source: 'rp_defect_update',
  },
  {
    item_id: 12,
    ui_url: null,
    ts: '2026-08-21T11:30:00Z',
    old_label: 'ti001',
    old_group: 'ti',
    new_label: 'ab001',
    new_group: 'ab',
    source: 'human_ui',
  },
];

const MODEL_ARTIFACTS = [
  {
    kind: 'gbm',
    version: 'gbm-2026-08-01',
    scope: 'install',
    is_active: true,
    trained_at: '2026-08-01T09:00:00Z',
    metrics: { auc: 0.9123, brier: 0.0771 },
    feature_schema_ver: 3,
    n_events: 1200,
  },
  {
    kind: 'calib',
    version: 'calib-2026-07-02',
    scope: 'project 1',
    is_active: false,
    trained_at: '2026-07-02T09:00:00Z',
    metrics: {},
    feature_schema_ver: 3,
    n_events: 120,
  },
];

const METRICS_DAILY = [
  { day: '2026-08-20', accepted: 5, corrected: 2, abstained: 1, ignored: 0 },
  { day: '2026-08-21', accepted: 7, corrected: 1, abstained: 3, ignored: 2 },
];

const MATURITY_WARM = {
  stage: 'warm',
  labeled_items: 120,
  gbm_min_events: 50,
  calib_min_events: 300,
  label_events: 180,
  human_events: 60,
  modes_confirmed: 0,
  modes_candidate: 4,
  embedded: 30,
  signatures: 40,
  embedded_pct: 0.75,
  install_gbm: 'gbm-2026-08-01',
  install_gbm_events: 1200,
  project_calibrator: null,
  project_calibrator_events: null,
};

/**
 * Fixture builder. `metrics_daily` is empty by default so a render mounts at
 * most ONE chart: vitest's mocker hands the real `echarts` module to the second
 * of two `import('echarts')` calls made in the same tick, which is exactly what
 * two EChart mounts in one commit do. The two charts are therefore asserted in
 * two separate renders (see the first two tests). Nothing about the view itself
 * changes — in a browser both charts share one module load.
 */
function timeline(over: Record<string, unknown> = {}) {
  return {
    rp: RP_BLOCK,
    label_events: LABEL_EVENTS,
    model_artifacts: MODEL_ARTIFACTS,
    metrics_daily: [],
    maturity: MATURITY_WARM,
    ...over,
  };
}

let charts: { lastOption: () => object | null };

beforeEach(() => {
  localStorage.clear();
  setDefects({});
  charts = mockECharts();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Learning Loop', () => {
  it('draws the daily metrics as a stacked area chart', async () => {
    mockApi({
      timeline: timeline({ label_events: [], metrics_daily: METRICS_DAILY }),
      'analyzer-health': { configured: false },
    });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('No label events');
    await waitFor(() => {
      expect(container.querySelectorAll('.chart').length).toBe(1);
      expect(charts.lastOption()).not.toBeNull();
    });

    const opt = charts.lastOption() as CapturedOption;
    expect(opt.series?.map((s) => s.name)).toEqual([
      'accepted',
      'corrected',
      'abstained',
      'ignored',
    ]);
    for (const s of opt.series ?? []) {
      expect(s.type).toBe('line');
      expect(s.stack).toBe('total');
      expect(s.areaStyle?.opacity).toBe(0.5);
    }
    expect(opt.series?.[0].data).toEqual([5, 7]);
    expect(opt.series?.[3].data).toEqual([0, 2]);
    expect(opt.xAxis?.data).toEqual(['2026-08-20', '2026-08-21']);
    expect(opt.tooltip?.trigger).toBe('axis');
    expect(opt.legend?.top).toBe(0);
  });

  it('renders the label-event scatter over a time axis with the defect categories', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('No daily metrics');
    await waitFor(() => {
      expect(container.querySelectorAll('.chart').length).toBe(1);
      expect(charts.lastOption()).not.toBeNull();
    });

    const opt = charts.lastOption() as CapturedOption;
    expect(opt.series?.[0].type).toBe('scatter');
    expect(opt.series?.[0].symbolSize).toBe(15);
    expect(opt.series?.[0].data).toHaveLength(2);
    expect(opt.xAxis?.type).toBe('time');
    expect(opt.yAxis?.type).toBe('category');
    expect(opt.yAxis?.data).toEqual(['pb', 'ab', 'si', 'nd', 'ti', 'none']);
    expect(opt.tooltip?.enterable).toBe(true);

    const html = opt.tooltip?.formatter?.({ data: { meta: LABEL_EVENTS[1] } }) ?? '';
    expect(html).toContain('item 12');
    expect(html).toContain('human_ui');
  });

  it('lists the newest label-event transitions with RP links', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('item 12');
    const link = screen.getByText('item 11');
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe(LABEL_EVENTS[0].ui_url);
    expect(screen.getByText('(new)')).toBeTruthy();
    expect(container.textContent).toContain('rp_defect_update');
  });

  it('shows model artifacts with the active badge and gate metrics', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('● active');
    const cards = container.querySelectorAll('.loop-art');
    expect(cards.length).toBe(2);
    expect(cards[0].querySelector('.loop-art-ver')?.textContent).toBe('gbm-2026-08-01');
    expect(cards[0].classList.contains('active')).toBe(true);
    expect(cards[1].classList.contains('active')).toBe(false);
    // Artifact footers print the raw count, exactly as the original view did.
    expect(container.textContent).toContain('schema v3 · trained on 1200 events');
    expect(container.textContent).toContain('0.912');
  });

  it('marks the WARM stage as the current maturity band', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Project maturity');
    const active = container.querySelector('.stage-pill.active');
    expect(active?.querySelector('.s-k')?.textContent).toBe('WARM · GBM training frame');
    expect(active?.querySelector('.s-v')?.textContent).toBe('50–299 labeled items');
    expect(active?.querySelector('.mat-here')?.textContent).toBe('● this project');
    expect(container.querySelector('.si-val')?.textContent).toBe(
      '120 labeled items · 180 to Hot',
    );
    expect(container.querySelector('.mat-stage.active .mat-stage-name')?.textContent).toBe('Warm');
    expect(container.textContent).toContain('75.0%');
    expect(container.textContent).toContain('0 / 4');
  });

  it('prints the honesty guard when warm modes are all still candidate', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Project maturity');
    const honesty = container.querySelector('.honesty');
    expect(honesty?.querySelector('.h-tag')?.textContent).toBe('reality check');
    expect(honesty?.textContent).toContain(
      '4 KB modes are still candidate (0 confirmed) — no KB short-circuit yet; decisions run ' +
        'through the install-wide GBM.',
    );
  });

  it('prints the hot-by-volume guard when no project calibrator shipped', async () => {
    mockApi({
      timeline: timeline({
        maturity: {
          ...MATURITY_WARM,
          stage: 'hot',
          labeled_items: 420,
          modes_confirmed: 2,
          modes_candidate: 1,
        },
      }),
      'analyzer-health': { configured: false },
    });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Project maturity');
    expect(container.querySelector('.honesty')?.textContent).toContain(
      '420 labeled items ≥ 300, but NO per-project isotonic calibrator has shipped yet ' +
        '(model_artifact has no active calib row for this project) — probabilities are still ' +
        'install-wide / raw. Hot by volume, not yet by machinery.',
    );
    expect(container.querySelector('.si-val')?.textContent).toBe(
      '420 labeled items · Hot band reached',
    );
  });

  it('shows the health empty state when the probe is not configured', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Health probe not configured');
    expect(container.textContent).toContain(
      'Set ANALYZER_HEALTH_URL on the inspector to show a live snapshot of the analyzer service /health here.',
    );
    expect(screen.getByText('env: ANALYZER_HEALTH_URL')).toBeTruthy();
  });

  it('pretty-prints the health payload when the probe is reachable', async () => {
    mockApi({
      timeline: timeline(),
      'analyzer-health': { configured: true, reachable: true, health: { status: 'ok', db: true } },
    });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await waitFor(() => expect(container.querySelector('pre.pattern')).toBeTruthy());
    expect(container.querySelector('pre.pattern')?.textContent).toBe(
      JSON.stringify({ status: 'ok', db: true }, null, 2),
    );
  });

  it('reports an unreachable analyzer with the returned error', async () => {
    mockApi({
      timeline: timeline(),
      'analyzer-health': { configured: true, reachable: false, error: 'connect ECONNREFUSED' },
    });
    await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Analyzer unreachable');
    expect(screen.getByText('connect ECONNREFUSED')).toBeTruthy();
  });

  it('renders the empty states when the loop has no data yet', async () => {
    mockApi({
      timeline: timeline({
        label_events: [],
        model_artifacts: [],
        metrics_daily: [],
        maturity: null,
      }),
      'analyzer-health': { configured: false },
    });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('No label events');
    expect(screen.getByText('No model artifacts')).toBeTruthy();
    expect(screen.getByText('No daily metrics')).toBeTruthy();
    expect(screen.getByText('Maturity unavailable')).toBeTruthy();
    expect(container.querySelectorAll('.chart').length).toBe(0);
    const tags = Array.from(container.querySelectorAll('.stage-tag')).map((t) => t.textContent);
    expect(tags).toContain('analyzer.label_event');
    expect(tags).toContain('analyzer.model_artifact');
    expect(tags).toContain('analyzer.metrics_daily');
  });

  it('keeps the technical details drawer with the real thresholds', async () => {
    mockApi({ timeline: timeline(), 'analyzer-health': { configured: false } });
    const { container } = await renderWithApp(<Loop />, { hash: '#view=loop&project=1' });

    await screen.findByText('Project maturity');
    const eng = container.querySelector('details.eng');
    expect(eng).toBeTruthy();
    expect(within(eng as HTMLElement).getByText('GBM_MIN_EVENTS = 50')).toBeTruthy();
    expect(within(eng as HTMLElement).getByText('CALIB_MIN_EVENTS = 300')).toBeTruthy();
    expect(eng?.textContent).toContain('none — serving install-wide / raw calibration');
    expect(eng?.textContent).toContain('· trained on 1,200 events');
  });
});
