// Test bindings shared by every view test.
//
// IMPORT THIS MODULE FIRST in a test file. It installs the `echarts` /
// `echarts-gl` doubles, and vitest only substitutes a module that has not been
// evaluated yet.

import { ThemeProvider } from '@reportportal/ui-kit';
import { render, waitFor, type RenderResult } from '@testing-library/react';
import type { ReactNode } from 'react';
import { vi } from 'vitest';

import { AppProvider, useApp } from '../app/state';

// --------------------------------------------------------------------------- //
// ECharts double
// --------------------------------------------------------------------------- //

// vi.hoisted keeps the store above the hoisted vi.mock factory below.
const echartsStore = vi.hoisted(() => ({ options: [] as object[] }));

vi.mock('echarts', () => {
  const init = () => ({
    setOption: (option: object) => {
      echartsStore.options.push(option);
    },
    resize: () => {},
    dispose: () => {},
    getDom: () => document.createElement('div'),
  });
  return { init, default: { init } };
});

vi.mock('echarts-gl', () => ({ default: {} }));

/**
 * Reset the recorded chart options and return a reader for the last option
 * passed to setOption. Assert on this object — never on canvas output.
 */
export function mockECharts(): { lastOption: () => object | null } {
  echartsStore.options.length = 0;
  return {
    lastOption: () => echartsStore.options[echartsStore.options.length - 1] ?? null,
  };
}

// --------------------------------------------------------------------------- //
// API double
// --------------------------------------------------------------------------- //

/** Boot endpoints every view test needs. Pass a key to mockApi to override. */
const DEFAULT_FIXTURES: Record<string, unknown> = {
  projects: {
    projects: [
      { project_id: 1, project_name: 'demo', item_count: 12, launch_count: 3 },
    ],
    rp_status: { configured: false, reachable: false, note: 'RP names: —' },
  },
  rp: {
    project_id: 1,
    project_name: 'demo',
    defects: {},
    status: { configured: false, reachable: false, note: 'RP names: —' },
  },
  summary: {
    project_id: 1,
    rp: {
      project_id: 1,
      defects: {},
      status: { configured: false, reachable: false, note: 'RP names: —' },
    },
    suggestions: 0,
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
  },
};

interface FakeResponse {
  ok: boolean;
  status: number;
  statusText: string;
  json(): Promise<unknown>;
}

function jsonResponse(payload: unknown): FakeResponse {
  return { ok: true, status: 200, statusText: 'OK', json: async () => payload };
}

function errorResponse(status: number, detail: string): FakeResponse {
  return {
    ok: false,
    status,
    statusText: detail,
    json: async () => ({ detail }),
  };
}

/**
 * Install a fetch double. `fixtures` keys are path prefixes after `api/`
 * (`'projects'`, `'item/'`, `'llm/summary'`); values are the JSON payload to
 * return. An `Error` value makes that endpoint fail. Longer keys win, so
 * `'llm/summary'` beats `'llm'`.
 */
export function mockApi(fixtures: Partial<Record<string, unknown>> = {}): void {
  const all: Record<string, unknown> = { ...DEFAULT_FIXTURES, ...fixtures };
  const keys = Object.keys(all).sort((a, b) => b.length - a.length);

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown): Promise<FakeResponse> => {
      const raw = String(input);
      const at = raw.indexOf('api/');
      const path = at === -1 ? raw : raw.slice(at + 4);
      const key = keys.find((k) => path.startsWith(k));
      if (key == null) return errorResponse(404, `no fixture for api/${path}`);
      const value = all[key];
      if (value instanceof Error) return errorResponse(500, value.message);
      return jsonResponse(value);
    }),
  );
}

// --------------------------------------------------------------------------- //
// Render helper
// --------------------------------------------------------------------------- //

/** Invisible marker so renderWithApp can wait for the boot sequence to settle. */
function BootMarker() {
  const { boot } = useApp();
  return <span data-testid="app-boot" data-state={boot.status} hidden />;
}

export interface RenderWithAppOptions {
  /** Permalink to boot from, e.g. '#view=journey&project=1&item=7'. */
  hash?: string;
}

/**
 * Render `ui` inside ThemeProvider + AppProvider and wait for boot to finish.
 * Async: boot does real (mocked) fetches, so this must be awaited.
 */
export async function renderWithApp(
  ui: ReactNode,
  opts: RenderWithAppOptions = {},
): Promise<RenderResult> {
  window.location.hash = opts.hash ?? '';
  const result = render(
    <ThemeProvider>
      <AppProvider>
        <BootMarker />
        {ui}
      </AppProvider>
    </ThemeProvider>,
  );
  await waitFor(() => {
    const marker = result.getByTestId('app-boot');
    if (marker.dataset.state === 'loading') throw new Error('still booting');
  });
  return result;
}
