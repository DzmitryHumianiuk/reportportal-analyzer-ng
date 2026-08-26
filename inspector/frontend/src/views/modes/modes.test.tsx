// IMPORT FIRST — installs the echarts / echarts-gl doubles.
import { mockApi, mockECharts, renderWithApp } from '../../test/utils';

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setDefects } from '../../lib/labels';
import Modes from './Modes';

const RP_BLOCK = {
  project_id: 1,
  defects: {},
  status: { configured: false, reachable: false, note: 'RP names: —' },
};

/** Shape of the option object the EChart double captures. */
interface CapturedSeries {
  type: string;
  name?: string;
  symbol?: string;
  symbolSize?: number;
  itemStyle?: Record<string, unknown>;
  data: Array<{ value: Array<number | null>; meta: Record<string, unknown> }>;
}

interface CapturedOption {
  series: CapturedSeries[];
  grid3D?: { viewControl?: Record<string, unknown>; environment?: string };
  xAxis3D?: { name?: string };
  yAxis3D?: { name?: string };
  zAxis3D?: { name?: string };
  xAxis?: { name?: string };
  yAxis?: { name?: string };
  tooltip: { enterable?: boolean; formatter: (p: unknown) => string };
}

function points3d() {
  return [
    {
      kind: 'item',
      x: 0.1,
      y: 0.2,
      z: 0.3,
      label: 'pb001',
      label_group: 'pb',
      item_id: 42,
      name: 'login smoke',
      ui_url: 'https://rp.example.com/ui/#demo/launches/all/9/42',
      is_auto_analyzed: true,
    },
    {
      kind: 'item',
      x: -0.4,
      y: 0.5,
      z: -0.2,
      label: 'ab001',
      label_group: 'ab',
      item_id: 43,
      name: 'checkout flow',
      ui_url: null,
      is_auto_analyzed: false,
    },
    {
      kind: 'mode',
      x: 0.0,
      y: 0.1,
      z: 0.05,
      label: 'pb001',
      label_group: 'pb',
      name: 'timeout cluster',
      status: 'active',
      purity: 0.8123,
      support: 17,
    },
  ];
}

let charts: { lastOption: () => object | null };

beforeEach(() => {
  setDefects({});
  charts = mockECharts();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Modes Map — unavailable', () => {
  it('renders the honest empty state plus diagnostics chips', async () => {
    mockApi({
      modes3d: {
        available: false,
        reason: 'No failure_signature rows carry an embedding yet.',
        diagnostics: { total_signatures: 7, embedded_signatures: 0, modes_with_centroid: 0 },
        rp: RP_BLOCK,
      },
    });

    const { container } = await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });

    await screen.findByText('Embedding space not available yet');
    expect(
      screen.getByText('No failure_signature rows carry an embedding yet.'),
    ).toBeTruthy();
    expect(screen.getByText('analyzer.failure_signature.emb (halfvec 384)')).toBeTruthy();

    const chips = Array.from(container.querySelectorAll('.chip')).map((c) => c.textContent);
    expect(chips).toContain('signatures 7');
    expect(chips).toContain('embedded 0');
    expect(chips).toContain('mode centroids 0');

    // No chart and no legend when the space is unavailable.
    expect(container.querySelector('.modes-fswrap')).toBeNull();
  });

  it('defaults missing diagnostics counts to 0', async () => {
    mockApi({
      modes3d: { available: false, reason: 'Embeddings are still being written.', rp: RP_BLOCK },
    });

    const { container } = await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });
    await screen.findByText('Embedding space not available yet');

    const chips = Array.from(container.querySelectorAll('.chip')).map((c) => c.textContent);
    expect(chips).toEqual(['signatures 0', 'embedded 0', 'mode centroids 0']);
  });
});

describe('Modes Map — 2D fallback', () => {
  it('renders the fallback note and a plain scatter chart', async () => {
    mockApi({
      modes3d: {
        available: true,
        rp: RP_BLOCK,
        n_items: 2,
        n_modes: 1,
        dimensions: 2,
        explained_variance_ratio: [0.61, 0.22],
        points: points3d(),
      },
    });

    const { container } = await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });

    await screen.findByText(/falls back to 2D/);
    // Exact, un-normalized copy — the note is parity-critical.
    expect(container.querySelector('.note')?.textContent).toBe(
      'Only 2 principal dimension(s) carry variance (the embedded set is small/degenerate), so the map falls back to 2D — it becomes 3D once more distinct embeddings exist.',
    );
    expect(container.querySelector('.card-sub')?.textContent).toBe(
      '2 items · 1 centroids · 2D · variance 61% / 22%',
    );

    await waitFor(() => expect(charts.lastOption()).toBeTruthy());
    const opt = charts.lastOption() as unknown as CapturedOption;
    expect(opt.series.map((s) => s.type)).toEqual(['scatter', 'scatter', 'scatter']);
    expect(opt.grid3D).toBeUndefined();
    expect(opt.xAxis?.name).toBe('PC1');
    expect(opt.yAxis?.name).toBe('PC2');
    // 2D coordinates only.
    expect(opt.series[0].data[0].value).toEqual([0.1, 0.2]);
  });
});

describe('Modes Map — 3D', () => {
  beforeEach(() => {
    mockApi({
      modes3d: {
        available: true,
        rp: RP_BLOCK,
        n_items: 2,
        n_modes: 1,
        dimensions: 3,
        explained_variance_ratio: [0.51, 0.24, 0.11],
        points: points3d(),
      },
    });
  });

  it('builds one scatter3D series per label group plus a diamond centroid series', async () => {
    await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });
    await waitFor(() => expect(charts.lastOption()).toBeTruthy());

    const opt = charts.lastOption() as unknown as CapturedOption;
    expect(opt.series.every((s) => s.type === 'scatter3D')).toBe(true);
    expect(opt.series).toHaveLength(3);
    expect(opt.series[0].data[0].value).toEqual([0.1, 0.2, 0.3]);
    expect(opt.series[0].symbolSize).toBe(9);

    const centroid = opt.series[opt.series.length - 1];
    expect(centroid.name).toBe('centroid');
    expect(centroid.symbol).toBe('diamond');
    expect(centroid.symbolSize).toBe(20);

    expect(opt.xAxis3D?.name).toBe('PC1');
    expect(opt.yAxis3D?.name).toBe('PC2');
    expect(opt.zAxis3D?.name).toBe('PC3');
    expect(opt.grid3D?.viewControl).toMatchObject({
      autoRotate: true,
      autoRotateSpeed: 6,
      distance: 190,
    });
    expect(opt.grid3D?.environment).toBe('#ffffff');
  });

  it('renders enterable tooltips with the RP deep link and the centroid line', async () => {
    await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });
    await waitFor(() => expect(charts.lastOption()).toBeTruthy());

    const opt = charts.lastOption() as unknown as CapturedOption;
    expect(opt.tooltip.enterable).toBe(true);

    const item = opt.tooltip.formatter({ data: opt.series[0].data[0] });
    expect(item).toContain('item 42 ↗');
    expect(item).toContain('https://rp.example.com/ui/#demo/launches/all/9/42');
    expect(item).toContain('login smoke');
    expect(item).toContain('· ⭑ auto');

    const unlinked = opt.tooltip.formatter({ data: opt.series[1].data[0] });
    expect(unlinked).toContain('item 43');
    expect(unlinked).not.toContain('<a href');
    expect(unlinked).not.toContain('⭑ auto');

    const centroidSeries = opt.series[opt.series.length - 1];
    const mode = opt.tooltip.formatter({ data: centroidSeries.data[0] });
    expect(mode).toContain('★ timeout cluster');
    expect(mode).toContain('status active');
    expect(mode).toContain('purity 0.81 · support 17');
  });

  it('escapes untrusted point text instead of injecting markup', async () => {
    await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });
    await waitFor(() => expect(charts.lastOption()).toBeTruthy());

    const opt = charts.lastOption() as unknown as CapturedOption;
    const html = opt.tooltip.formatter({
      data: { value: [0, 0, 0], meta: { kind: 'item', item_id: 9, name: '<img src=x onerror=1>' } },
    });
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img src=x onerror=1&gt;');
  });

  it('renders the legend, the centroid key and a fullscreen toggle', async () => {
    const { container } = await renderWithApp(<Modes />, { hash: '#view=modes&project=1' });
    await screen.findByText('★ = mode centroid');

    // 5 defect-group dots.
    expect(container.querySelectorAll('.modes-legend-dot')).toHaveLength(5);
    expect(screen.getByText('Product bug group')).toBeTruthy();
    expect(screen.getByText('To investigate group')).toBeTruthy();

    const fs = screen.getByTitle('Toggle fullscreen');
    expect(fs.textContent).toContain('fullscreen');
    // jsdom has no Fullscreen API — the handler must not throw.
    fireEvent.click(fs);

    const wrap = container.querySelector('.modes-fswrap');
    expect(wrap).toBeTruthy();
    expect(wrap?.querySelector('.chart')?.getAttribute('style')).toContain('max(560px, 72vh)');
  });
});
