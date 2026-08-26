// IMPORTANT: test/utils must be imported first — it installs the echarts double.
import { mockApi, mockECharts, renderWithApp } from '../../test/utils';

import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import Drain from './Drain';

interface IcicleDatum {
  name: string;
  value: number;
  path: string;
  depth: number;
}

interface CustomSeries {
  type: string;
  coordinateSystem: string;
  data: IcicleDatum[];
}

function series(option: object | null): CustomSeries {
  const s = (option as { series?: CustomSeries[] } | null)?.series;
  if (!s || !s[0]) throw new Error('no series in the captured chart option');
  return s[0];
}

// Three patterns; the first two share the leading tokens "Timeout waiting for".
const TEMPLATES = {
  templates: [
    {
      template_id: 101,
      pattern: 'Timeout waiting for element <NUM>',
      token_count: 5,
      match_count: 10,
      last_seen: '2026-08-20T10:00:00Z',
    },
    {
      template_id: 102,
      pattern: 'Timeout waiting for response <NUM>',
      token_count: 5,
      match_count: 20,
      last_seen: '2026-08-21T11:30:00Z',
    },
    {
      template_id: 103,
      pattern: 'Assertion failed: expected <NUM>',
      token_count: 4,
      match_count: 5,
      last_seen: '2026-08-22T09:15:00Z',
    },
  ],
  count: 3,
};

describe('Drain3 Explorer', () => {
  it('renders the templates table with highlighted pattern tokens', async () => {
    mockECharts();
    mockApi({ templates: TEMPLATES });
    const { container } = await renderWithApp(<Drain />, { hash: '#view=drain&project=1' });

    await screen.findByText('3 template(s)');

    expect(screen.getByText('#101')).toBeTruthy();
    expect(screen.getByText('#102')).toBeTruthy();
    expect(screen.getByText('#103')).toBeTruthy();

    // highlightPattern parity: <NUM> becomes a .tok-num span, once per row.
    const numTokens = container.querySelectorAll('.pattern .tok-num');
    expect(numTokens.length).toBe(3);
    expect(numTokens[0].textContent).toBe('<NUM>');

    // Plain text around the token stays plain text.
    expect(container.querySelector('.pattern')?.textContent).toBe(
      'Timeout waiting for element <NUM>',
    );
  });

  it('builds the token prefix tree for the icicle', async () => {
    const charts = mockECharts();
    mockApi({ templates: TEMPLATES });
    await renderWithApp(<Drain />, { hash: '#view=drain&project=1' });

    await screen.findByText('3 template(s)');
    await waitFor(() => {
      if (!charts.lastOption()) throw new Error('chart not drawn yet');
    });

    const s = series(charts.lastOption());
    expect(s.type).toBe('custom');
    expect(s.coordinateSystem).toBe('none');

    const byName = new Map(s.data.map((d) => [d.name, d]));
    // Shared prefix collapses into one branch, the third pattern into another.
    expect([...byName.keys()].sort()).toEqual(
      ['<NUM>', 'Assertion', 'Timeout', 'element', 'expected', 'failed:', 'for', 'response', 'waiting'].sort(),
    );
    expect(byName.get('Timeout')?.depth).toBe(1);
    expect(byName.get('element')?.depth).toBe(4);
    expect(byName.get('element')?.path).toBe('Timeout waiting for element');

    // Value follows the original d3 `.sum()`: leaves carry match_count, every
    // internal node adds 1 of its own. Timeout = 1 + (1 + (1 + (10 + 20))).
    expect(byName.get('element')?.value).toBe(10);
    expect(byName.get('response')?.value).toBe(20);
    expect(byName.get('Timeout')?.value).toBe(33);

    // The root is not drawn.
    expect(byName.has('root')).toBe(false);
  });

  it('shows both empty states when the project has no templates', async () => {
    mockECharts();
    mockApi({ templates: { templates: [], count: 0 } });
    await renderWithApp(<Drain />, { hash: '#view=drain&project=1' });

    await screen.findByText('No templates');
    expect(
      screen.getByText(
        'Drain3 has not mined any templates for this project yet — templates appear as error logs are parsed.',
      ),
    ).toBeTruthy();
    expect(screen.getByText('Nothing to tree')).toBeTruthy();
    expect(screen.getByText('No patterns to build a parse tree from.')).toBeTruthy();
    expect(screen.getByText('0 template(s)')).toBeTruthy();
  });

  it('drops the previous project from both cards while the new one loads', async () => {
    mockECharts();
    mockApi({
      projects: {
        projects: [
          { project_id: 1, project_name: 'demo', item_count: 12, launch_count: 3 },
          { project_id: 2, project_name: 'other', item_count: 4, launch_count: 1 },
        ],
        rp_status: { configured: false, reachable: false, note: 'RP names: —' },
      },
      templates: TEMPLATES,
    });
    const { container } = await renderWithApp(<Drain />, { hash: '#view=drain&project=1' });
    await screen.findByText('3 template(s)');
    expect(container.querySelector('.chart')).toBeTruthy();

    // Hold the next templates answer open, so the loading state is observable.
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const booted = fetch as unknown as (input: unknown) => Promise<unknown>;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown) => {
        if (String(input).includes('api/templates')) await held;
        return booted(input);
      }),
    );

    act(() => {
      window.location.hash = '#view=drain&project=2';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });

    // Neither the icicle nor the count may keep showing project 1.
    await waitFor(() => {
      expect(screen.queryByText('3 template(s)')).toBeNull();
    });
    expect(screen.getByText('log_template mirror')).toBeTruthy();
    expect(container.querySelector('.chart')).toBeNull();

    await act(async () => {
      release();
      await held;
    });
    await screen.findByText('3 template(s)');
  });

  // Runs last: the filter text is module state in the original app too, so it
  // survives a remount.
  it('debounces the search box and refetches with q', async () => {
    mockECharts();
    mockApi({ templates: TEMPLATES });
    await renderWithApp(<Drain />, { hash: '#view=drain&project=1' });
    await screen.findByText('3 template(s)');

    // ui-kit paints the placeholder as an overlay span, not an input attribute.
    expect(screen.getByText('Filter patterns (ILIKE)…')).toBeTruthy();

    const input = screen.getByLabelText('Search templates');
    await userEvent.type(input, 'time');

    await waitFor(() => {
      const urls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) =>
        String(c[0]),
      );
      if (!urls.some((u) => u.includes('api/templates') && u.includes('q=time'))) {
        throw new Error(`no filtered request yet: ${urls.join(' ')}`);
      }
    });

    // Debounced: one keystroke burst produces one extra request, not four.
    const filtered = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes('api/templates') && u.includes('q='));
    expect(filtered.length).toBe(1);
  });
});
