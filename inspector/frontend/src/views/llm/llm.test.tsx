import { mockApi, renderWithApp } from '../../test/utils';

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Llm from './Llm';

const NOW = new Date().toISOString();

const SUMMARY = {
  model: 'llama3.1:8b',
  event_total: 20,
  roles: [
    {
      role: 'coldstart',
      event_count: 12,
      ok: 9,
      outcomes: { ok: 9, schema_fail: 2, timeout: 1 },
      from_cache: 3,
      last_event: NOW,
      latency: { p50: 120, p90: 400, max: 900, n: 9 },
      state: null,
    },
    {
      role: 'explainer',
      event_count: 8,
      ok: 8,
      outcomes: { ok: 8 },
      from_cache: 0,
      last_event: NOW,
      latency: { p50: 1200, p90: 1800, max: 2000, n: 8 },
      state: { enabled: false, reason: 'nightly eval: worse than classical', decided_at: NOW },
    },
    {
      role: 'judge',
      event_count: 0,
      ok: 0,
      outcomes: {},
      from_cache: 0,
      last_event: null,
      latency: null,
      state: null,
    },
  ],
};

const EVENTS = {
  events: [
    {
      role: 'coldstart',
      outcome: 'ok',
      model: 'llama3.1:8b',
      cache_hit: true,
      item_id: 77,
      launch_id: 5,
      latency_ms: 0,
      created_at: NOW,
      output: { reason: 'assertion on a missing element' },
    },
  ],
  count: 1,
  limit: 50,
};

const CACHE = {
  entries: 2,
  total_hits: 5,
  rows: [
    {
      template_hash: 'a1b2c3d4',
      model: 'llama3.1:8b',
      hits: 4,
      fresh: true,
      created_at: NOW,
      last_hit_at: NOW,
      output: { error_class: 'AssertionError' },
      negative: false,
    },
    {
      template_hash: null,
      model: 'llama3.1:8b',
      hits: 1,
      fresh: false,
      created_at: NOW,
      last_hit_at: null,
      output: null,
      negative: true,
    },
  ],
};

function fetchUrls(): string[] {
  const mock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
  return mock.mock.calls.map((c) => String(c[0]));
}

beforeEach(() => {
  localStorage.clear();
  mockApi({ 'llm/summary': SUMMARY, 'llm/events': EVENTS, 'llm/cache': CACHE });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LLM view', () => {
  it('renders the six cards with a dashed panel for the zero-event role', async () => {
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });

    await waitFor(() => expect(screen.getByText('Roles at a glance')).toBeTruthy());
    for (const title of ['Outcome mix', 'Latency', 'Availability', 'Activity stream', 'Extractor cache']) {
      expect(screen.getByText(title)).toBeTruthy();
    }

    const panels = container.querySelectorAll('.role-panel');
    expect(panels.length).toBe(3);
    expect(panels[0].classList.contains('empty')).toBe(false);
    const judge = panels[2] as HTMLElement;
    expect(judge.classList.contains('empty')).toBe(true);
    expect(judge.textContent).toContain('0 events');
    expect(judge.textContent).toContain(
      'role enabled by default, wired, never fired in this project.',
    );
    expect(judge.textContent).toContain(
      'fires only on the suggest route at confidence [0.45, 0.75) with ≥ 2 Stage-C candidates; none occurred.',
    );

    // state lines: default (no row) and a disabled role
    expect(panels[0].textContent).toContain('on (default — no llm_role_state row)');
    expect(panels[1].textContent).toContain('disabled — nightly eval: worse than classical');

    // non-zero panel: ok counter, non-ok outcome chips, cache chip, last event
    expect(panels[0].textContent).toContain('schema_fail 2');
    expect(panels[0].textContent).toContain('timeout 1');
    expect(panels[0].textContent).toContain('cache 3');
    expect(panels[0].textContent).toContain('last event just now');
  });

  it('renders the outcome-mix takeaway and proportional bar widths', async () => {
    await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(screen.getByText('Outcome mix')).toBeTruthy());

    const mixCard = screen.getByText('Outcome mix').closest('.card') as HTMLElement;
    const takeaway = mixCard.querySelector('.takeaway') as HTMLElement;
    expect(takeaway.textContent).toContain('20 LLM calls');
    expect(takeaway.textContent).toContain(
      ' · 85% ok · 2 rejected by guardrails · 1 unavailable',
    );
    expect(takeaway.textContent).toContain(
      'rejections mean the validator worked, not that the pipeline erred.',
    );

    const rows = mixCard.querySelectorAll('.ocbar-row');
    expect(rows.length).toBe(2); // zero-event roles are skipped
    const segs = rows[0].querySelectorAll('.ocbar i');
    expect(segs.length).toBe(3);
    expect((segs[0] as HTMLElement).style.width).toBe('75%');
    expect((segs[0] as HTMLElement).className).toBe('oc-ok');
    const okOnly = rows[1].querySelectorAll('.ocbar i');
    expect(okOnly.length).toBe(1);
    expect((okOnly[0] as HTMLElement).style.width).toBe('100%');
  });

  it('normalizes latency tracks against the global max and formats the numbers', async () => {
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(screen.getByText('Latency')).toBeTruthy());

    const rows = container.querySelectorAll('.lat-row');
    expect(rows.length).toBe(3);
    expect(rows[0].textContent).toContain('p50 120 ms · p90 400 ms · max 900 ms · n 9');
    expect((rows[0].querySelector('.lat-fill') as HTMLElement).style.width).toBe('6%');
    expect(rows[1].textContent).toContain('p50 1.2 s · p90 1.8 s · max 2.0 s · n 8');
    expect(rows[2].querySelector('.lat-empty')?.textContent).toBe('—');
  });

  it('states the availability honesty block and the breaker drawer copy', async () => {
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(screen.getByText('Availability')).toBeTruthy());

    expect(screen.getByText('in-process state — not in the DB')).toBeTruthy();
    const stats = container.querySelectorAll('.stat-row .st');
    expect(stats.length).toBe(3);
    expect(stats[0].textContent).toBe('just nowlast llm_event');
    expect(stats[1].textContent).toBe('0breaker_open events');
    expect(stats[2].textContent).toBe('1transport timeouts');
    expect(screen.getByText('Breaker limits (code contract, not runtime)')).toBeTruthy();
  });

  it('links an activity row to the journey permalink and prints output as text', async () => {
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(container.querySelector('.llm-tl')).toBeTruthy());

    const chip = container.querySelector('.llm-tl .idchip') as HTMLAnchorElement;
    expect(chip.textContent).toBe('item 77');
    expect(chip.getAttribute('href')).toBe('#view=journey&project=1&launch=5&item=77');

    const row = container.querySelector('.llm-tl li') as HTMLElement;
    expect(row.querySelector('.oc-dot')?.className).toContain('oc-ok');
    const chips = row.querySelectorAll('.tl-mid .chip');
    expect(chips[chips.length - 1].textContent).toBe('cache'); // cache_hit chip
    expect(row.querySelector('.tl-right')?.textContent).toBe('cache · just now');

    const pre = row.querySelector('pre.out-json') as HTMLElement;
    expect(pre.textContent).toBe(
      JSON.stringify({ reason: 'assertion on a missing element' }, null, 2),
    );
    expect(pre.innerHTML).not.toContain('<');
  });

  it('refetches events with the picked role and permalinks it as lrole', async () => {
    await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(screen.getByText('Activity stream')).toBeTruthy());
    await waitFor(() => expect(fetchUrls().some((u) => u.includes('llm/events'))).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: 'judge' }));

    await waitFor(() =>
      expect(fetchUrls().some((u) => u.includes('llm/events') && u.includes('role=judge'))).toBe(
        true,
      ),
    );
    expect(window.location.hash).toContain('lrole=judge');
    expect(screen.getByRole('button', { name: 'judge' }).className).toContain('on');
  });

  it('explains an empty judge filter with the judge-specific sentence', async () => {
    mockApi({
      'llm/summary': SUMMARY,
      'llm/events': { events: [], count: 0, limit: 50 },
      'llm/cache': CACHE,
    });
    await renderWithApp(<Llm />, { hash: '#view=llm&project=1&lrole=judge' });

    await waitFor(() => expect(screen.getByText('No events for this filter')).toBeTruthy());
    expect(
      screen.getByText(
        'No judge events. The judge fires only on suggest-route decisions in [0.45, 0.75) with ≥ 2 candidates — none occurred on this install.',
      ),
    ).toBeTruthy();
  });

  it('marks a negative extractor cache row instead of showing a row of dashes', async () => {
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });
    await waitFor(() => expect(container.querySelector('table.data')).toBeTruthy());

    expect(screen.getByText('2 cached extractor outputs')).toBeTruthy();
    const rows = container.querySelectorAll('table.data tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].className).toBe('');
    expect(rows[0].textContent).toContain('a1b2c3d4');
    expect(rows[0].textContent).toContain('fresh');
    expect(rows[1].className).toBe('row-negative');
    expect(rows[1].querySelector('.tag-negative')?.textContent).toBe('no answer possible');
    expect(rows[1].textContent).toContain('expired');
  });

  it('shows the no-activity empty state when the project has no llm_event rows', async () => {
    mockApi({ 'llm/summary': { model: null, event_total: 0, roles: [] } });
    const { container } = await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });

    await waitFor(() => expect(screen.getByText('No LLM activity')).toBeTruthy());
    expect(container.querySelector('.stage-tag')?.textContent).toBe('analyzer.llm_event');
    expect(container.querySelectorAll('.role-panel').length).toBe(0);
  });

  it('reports a failing summary request instead of an empty page', async () => {
    mockApi({ 'llm/summary': new Error('boom') });
    await renderWithApp(<Llm />, { hash: '#view=llm&project=1' });

    await waitFor(() => expect(screen.getByText('LLM summary failed')).toBeTruthy());
    expect(screen.getByText('500 boom')).toBeTruthy();
  });
});
