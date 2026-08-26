import { mockApi, renderWithApp } from '../test/utils';

import { ThemeProvider } from '@reportportal/ui-kit';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { getDefects } from '../lib/labels';
import { AppProvider, useApp } from './state';

describe('app shell', () => {
  beforeEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the project option label as "name · N items · M launches"', async () => {
    mockApi();
    await renderWithApp(<App />);
    expect(await screen.findByText('demo · 12 items · 3 launches')).toBeTruthy();
  });

  it('falls back to "project #<id>" when RP has no name for the project', async () => {
    mockApi({
      projects: { projects: [{ project_id: 9, item_count: 1, launch_count: 1 }] },
    });
    await renderWithApp(<App />);
    expect(await screen.findByText('project #9 · 1 items · 1 launches')).toBeTruthy();
  });

  it('shows the RP status note from api/rp in the header', async () => {
    mockApi({
      rp: {
        project_id: 1,
        defects: {},
        status: { configured: true, reachable: true, note: 'RP names: live' },
      },
    });
    await renderWithApp(<App />);
    const status = await screen.findByTitle('ReportPortal name resolution status');
    expect(status.textContent).toBe('RP names: live');
    expect(status.className).toContain('ok');
  });

  it('marks the status warn when RP is configured but unreachable', async () => {
    mockApi({
      rp: {
        project_id: 1,
        defects: {},
        status: { configured: true, reachable: false, note: 'RP names: unavailable' },
      },
    });
    await renderWithApp(<App />);
    const status = await screen.findByTitle('ReportPortal name resolution status');
    expect(status.className).toContain('warn');
  });

  it('switches view and rewrites the hash when a tab is clicked', async () => {
    mockApi();
    await renderWithApp(<App />, { hash: '#view=journey&project=1&launch=4&item=7' });
    expect(window.location.hash).toContain('item=7');

    fireEvent.click(screen.getByText('Signatures'));

    await waitFor(() => {
      expect(screen.getByText('Signatures').className).toContain('active');
    });
    expect(window.location.hash).toContain('view=signatures');
    // Stale journey params are pruned by the active-view serializer.
    expect(window.location.hash).not.toContain('item=7');
    expect(window.location.hash).not.toContain('launch=4');
  });

  it('resolves the project hash param by RP name, case-insensitively', async () => {
    mockApi();
    await renderWithApp(<App />, { hash: '#view=journey&project=DEMO' });
    expect(window.location.hash).toContain('project=1');
  });

  it('shows the no-projects empty state when the database has none', async () => {
    mockApi({ projects: { projects: [] } });
    await renderWithApp(<App />);
    expect(await screen.findByText('No projects found')).toBeTruthy();
    expect(screen.getByText('analyzer.project')).toBeTruthy();
  });

  it('shows an honest error state when the API is unreachable', async () => {
    mockApi({ projects: new Error('boom') });
    await renderWithApp(<App />);
    expect(await screen.findByText('Cannot reach the inspector API')).toBeTruthy();
    expect(screen.getByText('GET /api/projects')).toBeTruthy();
  });

  it('renders the four topbar counters from api/summary', async () => {
    mockApi({
      summary: {
        project_id: 1,
        rp: { project_id: 1, defects: {}, status: { configured: false, reachable: false, note: '' } },
        suggestions: 5,
        accepted: 4,
        corrected: 3,
        ignored: 0,
        abstains: 2,
        pending: 0,
        llm_used: 0,
        items: 0,
        signatures: 0,
        embedded: 0,
        templates: 0,
        groups: 0,
        modes: 0,
        label_events: 0,
      },
    });
    const { container } = await renderWithApp(<App />);
    await waitFor(() => {
      expect(container.querySelectorAll('.counter').length).toBe(4);
    });
    const cells = [...container.querySelectorAll('.counter')].map((c) => c.textContent);
    expect(cells).toEqual(['5suggest', '2abstain', '4accepted', '3corrected']);
  });

  it('toasts when auto-refresh is switched on', async () => {
    mockApi();
    const { container } = await renderWithApp(<App />);
    const input = container.querySelector('input[type="checkbox"]');
    expect(input).toBeTruthy();
    fireEvent.click(input as HTMLInputElement);
    expect(await screen.findByText('Auto‑refresh on (10s)')).toBeTruthy();
  });

  it('renders all eight tabs in order', async () => {
    mockApi();
    const { container } = await renderWithApp(<App />);
    const labels = [...container.querySelectorAll('.tab')].map((t) => t.textContent);
    expect(labels).toEqual([
      'Item Journey',
      'Signatures',
      'Drain3 Explorer',
      'Modes Map 3D',
      'Launch Groups',
      'Learning Loop',
      'LLM',
      'Cold-start Rules',
    ]);
  });

  it('falls back to journey for an unknown view name in the hash', async () => {
    mockApi();
    await renderWithApp(<App />, { hash: '#view=nope&project=1' });
    expect(window.location.hash).toContain('view=journey');
  });

  it('re-applies state on a real hashchange (back / forward)', async () => {
    mockApi();
    await renderWithApp(<App />, { hash: '#view=journey&project=1' });

    window.location.hash = '#view=rubric&project=1&rule=R6';
    fireEvent(window, new HashChangeEvent('hashchange'));

    await waitFor(() => {
      expect(screen.getByText('Cold-start Rules').className).toContain('active');
    });
    expect(window.location.hash).toContain('rule=R6');
  });

  it('drops id-scoped selections on project change but keeps the text filter', async () => {
    mockApi({
      projects: {
        projects: [
          { project_id: 1, project_name: 'demo', item_count: 12, launch_count: 3 },
          { project_id: 2, project_name: 'other', item_count: 4, launch_count: 1 },
        ],
      },
    });
    await renderWithApp(<Probe />, {
      hash: '#view=signatures&project=1&q=timeout&conflicts=1&hash=abc123',
    });

    fireEvent.click(screen.getByTestId('switch-project'));

    await waitFor(() => {
      expect(window.location.hash).toContain('project=2');
    });
    expect(window.location.hash).toContain('q=timeout');
    expect(window.location.hash).toContain('conflicts=1');
    expect(window.location.hash).not.toContain('hash=abc123');
  });

  it('serializes the groups launch selection as `launch`', async () => {
    mockApi();
    await renderWithApp(<Probe />, { hash: '#view=groups&project=1' });

    fireEvent.click(screen.getByTestId('pick-glaunch'));

    await waitFor(() => {
      expect(window.location.hash).toContain('launch=42');
    });
    expect(window.location.hash).not.toContain('glaunch');
  });

  // main.tsx renders inside <StrictMode>, which mounts every effect twice in
  // dev. Boot must survive that: a "run once" guard cancels the first run and
  // skips the second, leaving the app on Loading forever.
  it('reaches ready when StrictMode mounts the provider twice', async () => {
    mockApi();
    window.location.hash = '#view=signatures&project=1';
    render(
      <StrictMode>
        <ThemeProvider>
          <AppProvider>
            <BootProbe />
          </AppProvider>
        </ThemeProvider>
      </StrictMode>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('boot-status').textContent).toBe('ready');
    });
    expect(window.location.hash).toContain('view=signatures');
  });

  it('re-applies a hashchange after a StrictMode remount', async () => {
    mockApi();
    window.location.hash = '#view=journey&project=1';
    render(
      <StrictMode>
        <ThemeProvider>
          <AppProvider>
            <BootProbe />
          </AppProvider>
        </ThemeProvider>
      </StrictMode>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('boot-status').textContent).toBe('ready');
    });

    window.location.hash = '#view=rubric&project=1&rule=R6';
    fireEvent(window, new HashChangeEvent('hashchange'));

    await waitFor(() => {
      expect(screen.getByTestId('boot-view').textContent).toBe('rubric');
    });
  });

  it('ignores a slow RP answer for a project the user already left', async () => {
    let releaseStale: (payload: unknown) => void = () => {};
    const staleRp = new Promise((resolve) => {
      releaseStale = resolve;
    });

    mockApi({
      projects: {
        projects: [
          { project_id: 1, project_name: 'one', item_count: 1, launch_count: 1 },
          { project_id: 2, project_name: 'two', item_count: 2, launch_count: 1 },
          { project_id: 3, project_name: 'three', item_count: 3, launch_count: 1 },
        ],
      },
      // Project 2 is the RP-unreachable case: its api/rp never answers until
      // the test releases it, long after the user moved on to project 3.
      'rp?project=2': staleRp,
      'rp?project=3': {
        project_id: 3,
        defects: { pb003: { name: 'Fresh bug', short_name: 'FB', color: '#0a0' } },
        status: { configured: true, reachable: true, note: 'RP names: live' },
      },
      'summary?project=3': summaryFixture(3, 7),
    });

    await renderWithApp(<RaceProbe />, { hash: '#view=signatures&project=1' });

    fireEvent.click(screen.getByTestId('to-2'));
    fireEvent.click(screen.getByTestId('to-3'));

    await waitFor(() => {
      expect(screen.getByTestId('rp-project').textContent).toBe('3');
      expect(screen.getByTestId('suggestions').textContent).toBe('7');
    });

    // The abandoned project-2 request lands now. Nothing may move.
    await act(async () => {
      releaseStale({
        project_id: 2,
        defects: { pb002: { name: 'Stale bug', short_name: 'SB', color: '#a00' } },
        status: { configured: true, reachable: true, note: 'RP names: stale' },
      });
      await new Promise((resolve) => {
        setTimeout(resolve, 0);
      });
    });

    expect(screen.getByTestId('rp-project').textContent).toBe('3');
    expect(screen.getByTestId('suggestions').textContent).toBe('7');
    expect(Object.keys(getDefects())).toEqual(['pb003']);
    expect(window.location.hash).toContain('project=3');
    expect(window.location.hash).not.toContain('project=2');
  });
});

/** api/summary payload with only the two fields these tests read. */
function summaryFixture(projectId: number, suggestions: number) {
  return {
    project_id: projectId,
    rp: {
      project_id: projectId,
      defects: {},
      status: { configured: false, reachable: false, note: '' },
    },
    suggestions,
    accepted: 0,
    corrected: 0,
    ignored: 0,
    abstains: 0,
    pending: 0,
    llm_used: 0,
    items: 0,
    signatures: 0,
    embedded: 0,
    templates: 0,
    groups: 0,
    modes: 0,
    label_events: 0,
  };
}

/** Reads the boot state without pulling in the whole App shell. */
function BootProbe() {
  const { boot, view } = useApp();
  return (
    <div>
      <span data-testid="boot-status">{boot.status}</span>
      <span data-testid="boot-view">{view}</span>
    </div>
  );
}

/** Switches projects twice in a row and shows what finally won. */
function RaceProbe() {
  const { setProject, rp, counters } = useApp();
  return (
    <div>
      <button type="button" data-testid="to-2" onClick={() => setProject(2)}>
        two
      </button>
      <button type="button" data-testid="to-3" onClick={() => setProject(3)}>
        three
      </button>
      <span data-testid="rp-project">{String(rp?.project_id ?? 'none')}</span>
      <span data-testid="suggestions">{String(counters?.suggestions ?? 'none')}</span>
    </div>
  );
}

/** Small consumer that exercises the context callbacks a Dropdown would call. */
function Probe() {
  const { setProject, patchLink } = useApp();
  return (
    <div>
      <button type="button" data-testid="switch-project" onClick={() => setProject(2)}>
        switch
      </button>
      <button type="button" data-testid="pick-glaunch" onClick={() => patchLink({ glaunch: 42 })}>
        pick
      </button>
    </div>
  );
}
