import { mockApi, mockECharts, renderWithApp } from '../test/utils';

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setDefects } from '../lib/labels';
import { Card } from './Card';
import { ChipLink } from './ChipLink';
import { DefectBadge } from './DefectBadge';
import { EChart } from './EChart';
import { EmptyState } from './EmptyState';
import { EngDetails } from './EngDetails';
import { HighlightedPattern } from './HighlightedPattern';
import { LabelBadge } from './LabelBadge';
import { Loading } from './Loading';
import { Microbar } from './Microbar';
import { RpIcon } from './RpIcon';
import { SiMeter } from './SiMeter';
import { ViewErrorBoundary } from './ViewErrorBoundary';

beforeEach(() => {
  localStorage.clear();
  setDefects({});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Card', () => {
  it('renders the step square, title and sub line', () => {
    const { container } = render(
      <Card title="Signature" step={1} sub="field-separated FTS doc + fingerprints" id="j-sig">
        body
      </Card>,
    );
    expect(container.querySelector('.step-num')?.textContent).toBe('1');
    expect(container.querySelector('.card-title')?.textContent).toContain('Signature');
    expect(container.querySelector('.card-sub')?.textContent).toBe(
      'field-separated FTS doc + fingerprints',
    );
    expect(container.querySelector('#j-sig')).toBeTruthy();
    expect(container.querySelector('.card-body')?.textContent).toBe('body');
  });
});

describe('EmptyState', () => {
  it('maps the 🚫 emoji to the RP error icon', () => {
    const { container } = render(<EmptyState icon="🚫" title="Failed to load view" body="nope" />);
    const svg = container.querySelector('.empty .icon svg');
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute('viewBox')).toBe('0 0 20 20'); // the vendored error icon
    expect(container.querySelector('.empty .icon')?.textContent).not.toContain('🚫');
  });

  it('renders an unmapped emoji literally', () => {
    const { container } = render(<EmptyState icon="🧬" title="No signatures" />);
    expect(container.querySelector('.empty .icon')?.textContent).toBe('🧬');
  });

  it('renders the stage tag when given', () => {
    render(<EmptyState icon="📭" title="No launches" tag="analyzer.test_item" />);
    expect(screen.getByText('analyzer.test_item').className).toContain('stage-tag');
  });
});

describe('Loading', () => {
  it('defaults to the original copy', () => {
    const { container } = render(<Loading />);
    expect(container.textContent).toContain('Loading…');
  });

  it('accepts custom text', () => {
    const { container } = render(<Loading text="Reading the rubric…" />);
    expect(container.textContent).toContain('Reading the rubric…');
  });
});

describe('ChipLink', () => {
  it('renders a plain span without a url', () => {
    const { container } = render(<ChipLink label="item 7" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.tagName).toBe('SPAN');
    expect(el.className).toBe('chip');
  });

  it('renders a ReportPortal deep link when a url exists', () => {
    const { container } = render(<ChipLink label="item 7" url="https://rp.example/ui/#p/1" />);
    const a = container.querySelector('a') as HTMLAnchorElement;
    expect(a.getAttribute('target')).toBe('_blank');
    expect(a.getAttribute('rel')).toBe('noopener');
    expect(a.getAttribute('title')).toBe('Open in ReportPortal ↗');
    expect(a.className).toBe('chip idlink');
  });
});

describe('DefectBadge', () => {
  it('renders a bare group code as a plain chip, not a defect pill', () => {
    const { container } = render(<DefectBadge locator="pb" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toBe('chip');
    expect(el.textContent).toBe('Product bug group');
    expect(el.getAttribute('title')).toBe('Defect group (PB), not a specific defect type');
  });

  it('renders the configured RP name and color on the dot', () => {
    setDefects({ pb001: { name: 'Product Bug', short_name: 'PB', color: '#d32f2f' } });
    const { container } = render(<DefectBadge locator="pb001" />);
    expect(container.textContent).toBe('Product Bug');
    const dot = container.querySelector('.dot') as HTMLElement;
    expect(dot.style.background).toContain('rgb(211, 47, 47)');
    expect(container.querySelector('.badge')?.getAttribute('title')).toBe('Product Bug · pb001');
  });

  it('uses the RP abbreviation in the compact variant', () => {
    setDefects({ ab001: { name: 'Automation Bug', short_name: 'AB', color: '#ffc208' } });
    const { container } = render(<DefectBadge locator="ab001" abbr />);
    expect(container.textContent).toBe('AB');
  });

  it('falls back to the stock group abbreviation without RP names', () => {
    const { container } = render(<DefectBadge locator="si001" abbr />);
    expect(container.textContent).toBe('SI');
  });
});

describe('LabelBadge', () => {
  it('carries the group class and the given text', () => {
    const { container } = render(<LabelBadge group="si" text="System issue" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toBe('badge lbl si');
    expect(el.textContent).toBe('System issue');
  });
});

describe('EngDetails', () => {
  it('restores the remembered open state', () => {
    localStorage.setItem('inspector.eng.grouping', '1');
    const { container } = render(
      <EngDetails storageKey="grouping">
        <p>kv</p>
      </EngDetails>,
    );
    expect((container.querySelector('details') as HTMLDetailsElement).open).toBe(true);
  });

  it('persists the open state under inspector.eng.<key>', () => {
    const { container } = render(
      <EngDetails storageKey="decision">
        <p>kv</p>
      </EngDetails>,
    );
    const details = container.querySelector('details') as HTMLDetailsElement;
    expect(details.open).toBe(false);
    details.open = true;
    fireEvent(details, new Event('toggle'));
    expect(localStorage.getItem('inspector.eng.decision')).toBe('1');

    details.open = false;
    fireEvent(details, new Event('toggle'));
    expect(localStorage.getItem('inspector.eng.decision')).toBe('0');
  });

  it('defaults the summary to "Technical Details"', () => {
    const { container } = render(
      <EngDetails storageKey="llm">
        <p>kv</p>
      </EngDetails>,
    );
    expect(container.querySelector('summary')?.textContent).toContain('Technical Details');
  });
});

describe('Microbar / SiMeter', () => {
  it('clamps the microbar fill to 0..1', () => {
    const { container } = render(<Microbar ratio={2} width={80} title="rrf" />);
    const bar = container.querySelector('.microbar') as HTMLElement;
    expect(bar.style.width).toBe('80px');
    expect((bar.querySelector('i') as HTMLElement).style.width).toBe('100%');
  });

  it('renders the si-meter track, tick and scale', () => {
    const { container } = render(
      <SiMeter
        label="burst signal (si_prior)"
        value="0.60"
        ratio={0.6}
        tickRatio={0.5}
        scale={['0', '0.60', '0.9 max']}
        note="one decision signal, not a verdict."
      />,
    );
    expect((container.querySelector('.si-fill') as HTMLElement).style.width).toBe('60%');
    expect((container.querySelector('.si-tick') as HTMLElement).style.left).toBe('50%');
    expect(container.querySelector('.si-scale')?.textContent).toBe('00.600.9 max');
    expect(container.querySelector('.note')?.textContent).toContain('not a verdict');
  });
});

describe('RpIcon', () => {
  it('falls back to the info icon for an unknown name', () => {
    const { container } = render(<RpIcon name="not-an-icon" />);
    expect(container.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 16 16');
  });

  it('honors the size prop', () => {
    const { container } = render(<RpIcon name="bolt" size={28} />);
    expect(container.querySelector('svg')?.getAttribute('width')).toBe('28');
  });
});

describe('HighlightedPattern', () => {
  it('highlights masked tokens with the specific token class', () => {
    const { container } = render(<HighlightedPattern pattern="Timeout after <NUM> ms on <URL>" />);
    const toks = [...container.querySelectorAll('.tok')].map((t) => [t.className, t.textContent]);
    expect(toks).toEqual([
      ['tok tok-num', '<NUM>'],
      ['tok tok-url', '<URL>'],
    ]);
  });

  it('never treats the pattern as markup', () => {
    const { container } = render(<HighlightedPattern pattern="<img src=x onerror=1>" />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toBe('<img src=x onerror=1>');
  });

  it('renders an honest placeholder for a missing pattern', () => {
    const { container } = render(<HighlightedPattern pattern={null} />);
    expect(container.textContent).toBe('— pattern unavailable —');
  });
});

describe('EChart', () => {
  it('passes the option through to setOption', async () => {
    const chart = mockECharts();
    const option = { series: [{ type: 'scatter', data: [[1, 2]] }] };
    const { container } = render(<EChart option={option} height={260} />);
    expect((container.querySelector('.chart') as HTMLElement).style.height).toBe('260px');
    await waitFor(() => {
      expect(chart.lastOption()).not.toBeNull();
    });
    expect((chart.lastOption() as { series: unknown[] }).series).toEqual(option.series);
  });
});

describe('ViewErrorBoundary', () => {
  it('renders the failed-to-load empty state with the error message', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    function Boom(): JSX.Element {
      throw new Error('404 item not found');
    }
    render(
      <ViewErrorBoundary>
        <Boom />
      </ViewErrorBoundary>,
    );
    expect(screen.getByText('Failed to load view')).toBeTruthy();
    expect(screen.getByText('404 item not found')).toBeTruthy();
    spy.mockRestore();
  });

  // App passes "view:project:refreshTick", so a project switch or a refresh
  // gives a failed view a fresh try instead of trapping the user on the error.
  it('clears a previous failure when the reset key changes', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    let broken = true;
    function Maybe(): JSX.Element {
      if (broken) throw new Error('404 item not found');
      return <div>view body</div>;
    }
    const { rerender } = render(
      <ViewErrorBoundary resetKey="journey:1:0">
        <Maybe />
      </ViewErrorBoundary>,
    );
    expect(screen.getByText('Failed to load view')).toBeTruthy();

    broken = false;
    rerender(
      <ViewErrorBoundary resetKey="journey:2:1">
        <Maybe />
      </ViewErrorBoundary>,
    );

    expect(screen.getByText('view body')).toBeTruthy();
    expect(screen.queryByText('Failed to load view')).toBeNull();
    spy.mockRestore();
  });
});

describe('renderWithApp', () => {
  it('boots the provider so views can render against it', async () => {
    mockApi();
    const { container } = await renderWithApp(<div data-testid="child">view</div>);
    expect(container.querySelector('[data-testid="child"]')).toBeTruthy();
    expect(screen.getByTestId('app-boot').dataset.state).toBe('ready');
  });
});
