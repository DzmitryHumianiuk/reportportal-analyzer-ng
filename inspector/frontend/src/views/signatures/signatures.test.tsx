// IMPORTANT: test/utils must be imported first (it installs the module doubles).
import { mockApi, renderWithApp } from '../../test/utils';

import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useApp } from '../../app/state';
import Signatures from './Signatures';

/** Drives the shell controls the view does not own (project pick, auto-refresh). */
function Controls() {
  const { setProject, setAutorefresh } = useApp();
  return (
    <>
      <button type="button" onClick={() => setProject(2)}>
        switch project
      </button>
      <button type="button" onClick={() => setAutorefresh(true)}>
        auto on
      </button>
    </>
  );
}

const RP = {
  project_id: 1,
  project_name: 'demo',
  defects: {},
  status: { configured: false, reachable: false, note: 'RP names: —' },
};

const LONG_HASH = 'aaaaaaaaaaaaaaaa1111';

const SIGNATURES = {
  rp: RP,
  summary: {
    distinct_error_hash: 2,
    distinct_exception_fp: 2,
    items_with_signatures: 7,
    embedded: 5,
    conflict_hashes: 1,
  },
  rows: [
    {
      error_hash: LONG_HASH,
      member_count: 4,
      labels: [
        { locator: 'pb001', group: 'pb', count: 3 },
        { locator: 'ab001', group: 'ab', count: 1 },
      ],
      exc_classes: ['java.lang.NullPointerException'],
      status_codes: ['500'],
      first_seen: '2026-08-01T10:00:00Z',
      last_seen: '2026-08-05T10:00:00Z',
      is_conflict: true,
    },
    {
      error_hash: 'bbbb2222',
      member_count: 1,
      labels: [{ locator: 'ti001', group: 'ti', count: 1 }],
      exc_classes: [],
      status_codes: [],
      first_seen: '2026-08-02T10:00:00Z',
      last_seen: '2026-08-02T11:00:00Z',
      is_conflict: false,
    },
  ],
  count: 2,
  offset: 0,
  limit: 50,
};

const DETAIL = {
  rp: RP,
  error_hash: LONG_HASH,
  exception_fp: 'fp7f3a',
  representative_item_id: 101,
  is_conflict: true,
  labels: [
    { locator: 'pb001', group: 'pb', count: 3 },
    { locator: 'ab001', group: 'ab', count: 1 },
  ],
  member_total: 4,
  member_shown: 2,
  signature: {
    exc_text: 'java.lang.NullPointerException',
    msg_text: 'cannot invoke getId()',
    top_frames: ['com.acme.Cart.total', 'com.acme.Api.checkout'],
    status_codes: ['500'],
  },
  templates: [
    { template_id: 42, pattern: 'timeout after <NUM> ms', token_count: 5, match_count: 128 },
  ],
  members: [
    {
      item_id: 101,
      ui_url: 'https://rp.example/ui/#demo/launches/all/9/101',
      issue_type: 'pb001',
      label_group: 'pb',
      item_name: 'checkout adds item',
      is_auto_analyzed: true,
      launch_id: 9,
      launch_name: 'nightly #12',
      launch_url: 'https://rp.example/ui/#demo/launches/all/9',
      indexed_at: '2026-08-05T09:00:00Z',
    },
    {
      item_id: 102,
      ui_url: null,
      issue_type: 'ab001',
      label_group: 'ab',
      item_name: 'checkout pays',
      is_auto_analyzed: false,
      launch_id: 9,
      launch_name: 'nightly #12',
      launch_url: null,
      indexed_at: '2026-08-05T09:05:00Z',
    },
  ],
};

function fetchUrls(): string[] {
  const mock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
  return mock.mock.calls.map((c) => String(c[0]));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Signatures view', () => {
  it('renders the summary chips, the row table and the conflict flag', async () => {
    mockApi({ signatures: SIGNATURES });
    const { container } = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1',
    });

    await waitFor(() => expect(container.querySelectorAll('tr.sig-row').length).toBe(2));

    const chips = [...container.querySelectorAll('#sig-summary .counter')];
    expect(chips.map((c) => c.querySelector('.k')?.textContent)).toEqual([
      'error_hash',
      'exception_fp',
      'signed items',
      'embedded',
      'conflicts',
    ]);
    expect(chips[0].querySelector('.n')?.textContent).toBe('2');
    expect(chips[4].className).toContain('warn');

    // card sub line reflects the loaded count
    expect(container.querySelector('.card-sub')?.textContent).toBe('2 error_hashes');

    const rows = [...container.querySelectorAll('tr.sig-row')];
    expect(rows[0].className).toContain('conflict');
    expect(rows[0].querySelector('.conflict-flag')?.textContent).toBe('⚠');
    expect(rows[1].className).not.toContain('conflict');

    // long hashes are shortened, the full value stays in the title
    const hashCell = rows[0].querySelector('td .mono');
    expect(hashCell?.textContent).toBe('aaaaaa…1111');
    expect(hashCell?.getAttribute('title')).toBe(LONG_HASH);

    // empty cells render the dash placeholder
    expect(rows[1].textContent).toContain('—');
  });

  it('fetches the detail on row click and renders the member items', async () => {
    mockApi({ signatures: SIGNATURES, 'signature-hash': DETAIL });
    const { container } = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1',
    });
    await waitFor(() => expect(container.querySelectorAll('tr.sig-row').length).toBe(2));

    fireEvent.click(container.querySelectorAll('tr.sig-row')[0]);

    await waitFor(() => expect(container.querySelector('tr.sig-detail')).toBeTruthy());
    expect(container.querySelector('tr.sig-detail td')?.getAttribute('colspan')).toBe('6');

    await screen.findByText('Member items (2 of 4)');
    expect(screen.getByText('item 101')).toBeTruthy();
    expect(screen.getByText('item 102')).toBeTruthy();
    expect(screen.getByText('fp fp7f3a')).toBeTruthy();
    expect(screen.getByText('representative item 101')).toBeTruthy();
    expect(screen.getByText('⚠ label-bleed candidate')).toBeTruthy();
    expect(screen.getByText('Referenced Drain3 templates (1)')).toBeTruthy();
    expect(
      screen.getByText('2 more members not shown (capped at 2).'),
    ).toBeTruthy();

    // the expansion is permalinked and only one row can be open
    expect(window.location.hash).toContain(`hash=${LONG_HASH}`);
    expect(container.querySelectorAll('tr.sig-detail').length).toBe(1);

    // clicking again collapses and drops the hash param
    fireEvent.click(container.querySelectorAll('tr.sig-row')[0]);
    await waitFor(() => expect(container.querySelector('tr.sig-detail')).toBeNull());
    expect(window.location.hash).not.toContain('hash=');
  });

  it('auto-opens a permalinked expansion', async () => {
    mockApi({ signatures: SIGNATURES, 'signature-hash': DETAIL });
    const { container } = await renderWithApp(<Signatures />, {
      hash: `#view=signatures&project=1&hash=${LONG_HASH}`,
    });

    await waitFor(() => expect(container.querySelector('tr.sig-detail')).toBeTruthy());
    await screen.findByText('Member items (2 of 4)');
  });

  it('debounces the search box, writes q to the hash and refetches', async () => {
    mockApi({ signatures: SIGNATURES });
    await renderWithApp(<Signatures />, { hash: '#view=signatures&project=1' });
    await waitFor(() => expect(screen.getAllByRole('row').length).toBeGreaterThan(1));

    // ui-kit paints the placeholder as its own element, not as an attribute
    expect(screen.getByText('Search exc / msg / frames (ILIKE)…')).toBeTruthy();
    const input = screen.getByRole('textbox', { name: 'Search signatures' });
    fireEvent.change(input, { target: { value: 'boom' } });

    // debounced: nothing is written straight away
    expect(window.location.hash).not.toContain('q=boom');

    await waitFor(() => expect(window.location.hash).toContain('q=boom'));
    await waitFor(() => expect(fetchUrls().some((u) => u.includes('q=boom'))).toBe(true));
  });

  it('shows the conflicts-only empty state', async () => {
    mockApi({ signatures: { ...SIGNATURES, rows: [], count: 0 } });
    await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1&conflicts=1',
    });

    await screen.findByText('No signatures');
    expect(
      screen.getByText(
        'No error_hash in this project has members carrying more than one distinct non-ti label — no hash-collision label bleed detected.',
      ),
    ).toBeTruthy();
    expect(screen.getByText('analyzer.failure_signature')).toBeTruthy();
  });

  it('shows the search empty state and the genuinely-empty one', async () => {
    mockApi({ signatures: { ...SIGNATURES, rows: [], count: 0 } });
    const { unmount } = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1&q=nope',
    });
    await screen.findByText(
      'No signature matches your search across exc / msg / frames text.',
    );
    unmount();

    mockApi({ signatures: { ...SIGNATURES, rows: [], count: 0 } });
    await renderWithApp(<Signatures />, { hash: '#view=signatures&project=1' });
    await screen.findByText(
      'This project has no failure_signature rows yet — signatures are built as error logs are indexed.',
    );
  });

  it('pages with the prev / next buttons', async () => {
    mockApi({ signatures: { ...SIGNATURES, count: 2, limit: 2 } });
    const { container } = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1',
    });
    await waitFor(() => expect(container.querySelectorAll('tr.sig-row').length).toBe(2));

    expect(screen.getByText('rows 1–2')).toBeTruthy();
    const prev = screen.getByRole('button', { name: '← prev' }) as HTMLButtonElement;
    const next = screen.getByRole('button', { name: 'next →' }) as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    expect(next.disabled).toBe(false);

    fireEvent.click(next);
    await waitFor(() => expect(fetchUrls().some((u) => u.includes('offset=2'))).toBe(true));
  });

  it('keeps the page when the tab is left and re-entered', async () => {
    mockApi({ signatures: { ...SIGNATURES, count: 2, limit: 2 } });
    const first = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1',
    });
    await waitFor(() => expect(first.container.querySelectorAll('tr.sig-row').length).toBe(2));

    fireEvent.click(screen.getByRole('button', { name: 'next →' }));
    await waitFor(() => expect(fetchUrls().some((u) => u.includes('offset=2'))).toBe(true));

    // leaving the tab unmounts the view; coming back must not send the user
    // to page 1 again
    first.unmount();
    const again = await renderWithApp(<Signatures />, {
      hash: '#view=signatures&project=1',
    });
    await waitFor(() => expect(again.container.querySelectorAll('tr.sig-row').length).toBe(2));

    const asked = fetchUrls().filter((u) => u.includes('api/signatures'));
    expect(asked[asked.length - 1]).toContain('offset=2');
  });

  it('starts the pager over when the project changes', async () => {
    mockApi({ signatures: { ...SIGNATURES, count: 2, limit: 2 } });
    const { container } = await renderWithApp(
      <>
        <Controls />
        <Signatures />
      </>,
      { hash: '#view=signatures&project=1' },
    );
    await waitFor(() => expect(container.querySelectorAll('tr.sig-row').length).toBe(2));

    fireEvent.click(screen.getByRole('button', { name: 'next →' }));
    await waitFor(() => expect(fetchUrls().some((u) => u.includes('offset=2'))).toBe(true));

    fireEvent.click(screen.getByText('switch project'));

    // page 2 of the old project must not be asked for in the new one
    await waitFor(() =>
      expect(
        fetchUrls().some((u) => u.includes('api/signatures') && u.includes('project=2')),
      ).toBe(true),
    );
    const asked = fetchUrls().filter(
      (u) => u.includes('api/signatures') && u.includes('project=2'),
    );
    expect(asked.every((u) => u.includes('offset=0'))).toBe(true);
  });

  it('reloads the open row on an auto-refresh tick', async () => {
    mockApi({ signatures: SIGNATURES, 'signature-hash': DETAIL });
    const { container } = await renderWithApp(
      <>
        <Controls />
        <Signatures />
      </>,
      { hash: `#view=signatures&project=1&hash=${LONG_HASH}` },
    );
    await waitFor(() => expect(container.querySelector('tr.sig-detail')).toBeTruthy());
    await screen.findByText('Member items (2 of 4)');

    const detailCalls = () => fetchUrls().filter((u) => u.includes('signature-hash')).length;
    const before = detailCalls();

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByText('auto on'));
      await act(async () => {
        vi.advanceTimersByTime(10000);
      });
    } finally {
      vi.useRealTimers();
    }

    await waitFor(() => expect(detailCalls()).toBeGreaterThan(before));
  });
});
