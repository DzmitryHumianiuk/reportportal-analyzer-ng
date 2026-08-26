// Import the test bindings FIRST: they install the echarts double before the
// EChart wrapper ever imports the real library.
import { mockApi, mockECharts, renderWithApp } from '../../test/utils';

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ViewErrorBoundary } from '../../components';
import { setDefects } from '../../lib/labels';
import Groups from './Groups';

interface GraphSeries {
  type: string;
  layout: string;
  roam: boolean;
  draggable: boolean;
  categories: Array<{ name: string }>;
  force: { repulsion: number; edgeLength: number; gravity: number };
  data: Array<{
    name: string;
    x: number;
    itemStyle: { color: string; borderColor: string; borderWidth: number };
    tip: string;
  }>;
  links: Array<{ source: string; target: string }>;
}

function graphSeries(option: object | null): GraphSeries {
  const series = (option as { series?: GraphSeries[] } | null)?.series;
  if (!series || !series.length) throw new Error('no series on the chart option');
  return series[0];
}

const LAUNCHES = {
  launches: [
    {
      launch_id: 10,
      launch_name: 'nightly',
      launch_number: 7,
      item_count: 3,
      labeled_count: 2,
    },
    {
      launch_id: 11,
      launch_name: null,
      launch_number: null,
      item_count: 2,
      labeled_count: 0,
    },
  ],
};

const RP_BLOCK = {
  project_id: 1,
  defects: {},
  status: { configured: false, reachable: false, note: 'RP names: —' },
};

const GROUPS = {
  rp: RP_BLOCK,
  groups: [
    {
      group_id: 1,
      launch_id: 10,
      launch_url: 'https://rp.example/ui/#demo/launches/all/10',
      dominant: true,
      si_prior: 0.72,
      member_count: 3,
      fingerprint: 'abcdef1234567890',
    },
    {
      group_id: 2,
      launch_id: 11,
      launch_url: null,
      dominant: false,
      si_prior: 0.11,
      member_count: 2,
      fingerprint: '0f0f0f0f0f0f',
    },
  ],
  nodes: [
    {
      item_id: 101,
      group_id: 1,
      issue_type: 'pb001',
      label_group: 'pb',
      is_auto_analyzed: true,
      name: 'checkout smoke',
      ui_url: 'https://rp.example/ui/#demo/launches/all/10/101',
    },
    {
      item_id: 102,
      group_id: 1,
      issue_type: 'ab001',
      label_group: 'ab',
      is_auto_analyzed: false,
      name: 'login flow',
      ui_url: null,
    },
    {
      item_id: 103,
      group_id: 1,
      issue_type: null,
      label_group: 'nd',
      is_auto_analyzed: false,
      name: null,
      ui_url: null,
    },
    {
      item_id: 201,
      group_id: 2,
      issue_type: 'si001',
      label_group: 'si',
      is_auto_analyzed: false,
      name: 'db timeout',
      ui_url: null,
    },
    {
      item_id: 202,
      group_id: 2,
      issue_type: 'ti001',
      label_group: 'ti',
      is_auto_analyzed: false,
      name: 'flaky upload',
      ui_url: null,
    },
  ],
};

let charts: { lastOption: () => object | null };

beforeEach(() => {
  setDefects({});
  charts = mockECharts();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Launch Groups view', () => {
  it('renders group mini cards, the force graph and the launch picker', async () => {
    mockApi({ launches: LAUNCHES, groups: GROUPS });
    const { container } = await renderWithApp(<Groups />, { hash: '#view=groups&project=1' });

    // Group mini cards: burst badge on the dominant group, plain si on the other.
    await screen.findByText('group 1');
    expect(screen.getByText('group 2')).toBeTruthy();
    expect(screen.getByText('🔥 burst · si 0.72')).toBeTruthy();
    expect(screen.getByText('si 0.11')).toBeTruthy();
    expect(screen.getByText('3 members')).toBeTruthy();
    expect(screen.getByText('fp abcdef12…')).toBeTruthy();

    // launch chip is a real RP deep link only when a URL exists.
    const launchChip = screen.getByText('launch 10');
    expect(launchChip.tagName).toBe('A');
    expect(launchChip.getAttribute('href')).toBe(GROUPS.groups[0].launch_url);
    expect(screen.getByText('launch 11').tagName).toBe('SPAN');

    // The chart mounted with a force graph carrying every node.
    await waitFor(() => expect(charts.lastOption()).not.toBeNull());
    const s = graphSeries(charts.lastOption());
    expect(s.type).toBe('graph');
    expect(s.layout).toBe('force');
    expect(s.roam).toBe(true);
    expect(s.draggable).toBe(true);
    expect(s.force).toEqual({ repulsion: 160, edgeLength: 46, gravity: 0.06 });
    expect(s.data.map((n) => n.name)).toEqual(['101', '102', '103', '201', '202']);
    expect(s.categories.map((c) => c.name)).toEqual(['group 1', 'group 2']);

    // Star links per group: first member → every other member.
    expect(s.links).toEqual([
      { source: '101', target: '102' },
      { source: '101', target: '103' },
      { source: '201', target: '202' },
    ]);

    // Groups are seeded at evenly spaced x anchors so the layout keeps wells.
    expect(s.data[0].x).toBeLessThan(s.data[3].x);

    // Auto-analyzed nodes keep the accent ring; the rest a thin white one.
    expect(s.data[0].itemStyle.borderWidth).toBe(3);
    expect(s.data[1].itemStyle.borderWidth).toBe(1.2);
    expect(s.data[1].itemStyle.borderColor).toBe('#ffffff');

    // Tooltip carries the RP deep link plus name / label / group / auto flag.
    expect(s.data[0].tip).toContain('href="https://rp.example/ui/#demo/launches/all/10/101"');
    expect(s.data[0].tip).toContain('item 101 ↗');
    expect(s.data[0].tip).toContain('checkout smoke');
    expect(s.data[0].tip).toContain('group 1');
    expect(s.data[0].tip).toContain('⭑ auto-analyzed');
    expect(s.data[1].tip).not.toContain('<a ');

    // Legend row + hint copy, verbatim.
    expect(screen.getByText('◯ accent ring = auto‑analyzed')).toBeTruthy();
    expect(screen.getByText('wheel: zoom · drag bg: pan')).toBeTruthy();
    expect(screen.getByTitle('Fit graph to view')).toBeTruthy();

    // Launch picker: field label + "All launches" selected by default.
    expect(screen.getByText('Launch')).toBeTruthy();
    expect(screen.getByText('All launches')).toBeTruthy();
    expect(container.querySelector('.picker-row')).toBeTruthy();
  });

  it('lists every launch option, formatted like the original select', async () => {
    mockApi({ launches: LAUNCHES, groups: GROUPS });
    const { container } = await renderWithApp(<Groups />, { hash: '#view=groups&project=1' });
    await screen.findByText('All launches');

    const toggle = container.querySelector<HTMLElement>('.picker-row [role="button"]');
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle as HTMLElement);

    await screen.findByText('nightly · #7 (3)');
    expect(screen.getByText('launch · #— (2)')).toBeTruthy();
  });

  it('loads the permalinked launch and asks the API for it', async () => {
    mockApi({ launches: LAUNCHES, groups: GROUPS });
    await renderWithApp(<Groups />, { hash: '#view=groups&project=1&launch=10' });
    await screen.findByText('group 1');

    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(
      (c) => String(c[0]),
    );
    expect(calls.some((u) => u === 'api/groups?project=1&launch=10')).toBe(true);
    expect(await screen.findByText('nightly · #7 (3)')).toBeTruthy();
  });

  it('shows the no-items empty state and no group list when nodes are empty', async () => {
    mockApi({ launches: LAUNCHES, groups: { ...GROUPS, nodes: [] } });
    await renderWithApp(<Groups />, { hash: '#view=groups&project=1' });

    expect(await screen.findByText('No items')).toBeTruthy();
    expect(screen.getByText('No test items for this selection.')).toBeTruthy();
    expect(screen.getByText('analyzer.test_item')).toBeTruthy();
    expect(screen.queryByText('group 1')).toBeNull();
    expect(charts.lastOption()).toBeNull();
  });

  it('says so when there are no launch_group rows', async () => {
    mockApi({ launches: LAUNCHES, groups: { ...GROUPS, groups: [] } });
    await renderWithApp(<Groups />, { hash: '#view=groups&project=1' });

    expect(await screen.findByText('No launch_group rows.')).toBeTruthy();
  });

  it('surfaces a failed load through the view error state', async () => {
    mockApi({ launches: LAUNCHES, groups: new Error('boom') });
    // App.tsx wraps every view in this boundary; it owns the old mount()
    // rejection state, so the view only has to re-throw.
    await renderWithApp(
      <ViewErrorBoundary>
        <Groups />
      </ViewErrorBoundary>,
      { hash: '#view=groups&project=1' },
    );

    expect(await screen.findByText('Failed to load view')).toBeTruthy();
  });
});
