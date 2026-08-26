// Cold-start Rules view. Import the shared test bindings first: they install
// the module doubles, and vitest only substitutes modules not yet evaluated.
import { mockApi, renderWithApp } from '../../test/utils';

import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Rubric from './Rubric';
import type { RubricResponse } from '../../app/types';

const RULES: RubricResponse = {
  rules: [
    {
      rule: 'R1',
      order: 1,
      name: 'Could not reach the service',
      when: 'connection refused, timeout or DNS failure',
      label: 'si',
      label_name: 'System issue',
      confidence: 'high',
    },
    {
      rule: 'R2',
      order: 2,
      name: 'Element not found',
      when: 'selector missed the element on the page',
      label: 'ab',
      label_name: 'Automation bug',
      confidence: 'med',
    },
    {
      rule: 'R3',
      order: 3,
      name: 'Nothing else fits',
      when: 'no earlier rule matched the error',
      label: 'pb',
      label_name: 'Product bug',
      confidence: 'low',
    },
  ],
};

const INTRO =
  'A project with no decided failures has nothing to match against. The analyzer ' +
  'reads the error and applies the first rule below that fits, then reports which ' +
  'one it used. These guesses are never applied on their own: they are offered, ' +
  'and a person decides.';

describe('Cold-start Rules view', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the intro paragraph verbatim and one row per rule', async () => {
    mockApi({ rubric: RULES });
    await renderWithApp(<Rubric />, { hash: '#view=rubric&project=1' });

    expect(await screen.findByText(INTRO)).toBeTruthy();
    expect(screen.getByText('Cold-start rules')).toBeTruthy();
    expect(screen.getByText('3 rules, applied in order')).toBeTruthy();

    for (const head of ['#', 'Rule', 'What it looks for', 'Always produces']) {
      expect(screen.getByRole('columnheader', { name: head })).toBeTruthy();
    }

    expect(screen.getByText('Could not reach the service')).toBeTruthy();
    expect(screen.getByText('selector missed the element on the page')).toBeTruthy();
    expect(screen.getByText('System issue')).toBeTruthy();
    expect(document.querySelectorAll('tbody tr.rubric-row').length).toBe(3);
  });

  it('spells out what each confidence word buys', async () => {
    mockApi({ rubric: RULES });
    const { container } = await renderWithApp(<Rubric />, { hash: '#view=rubric&project=1' });
    await screen.findByText(INTRO);

    const glosses = Array.from(container.querySelectorAll('.rubric-conf')).map(
      (el) => el.textContent,
    );
    expect(glosses).toEqual([
      'high — the pattern is unambiguous',
      'med — the pattern usually holds',
      'low — the closest rule, little more',
    ]);
  });

  it('focuses and scrolls to the rule a guess linked to', async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView');
    mockApi({ rubric: RULES });
    await renderWithApp(<Rubric />, { hash: '#view=rubric&project=1&rule=R2' });
    await screen.findByText(INTRO);

    const focused = document.getElementById('rule-R2');
    expect(focused).toBeTruthy();
    expect(focused?.classList.contains('rubric-row-focus')).toBe(true);
    expect(document.getElementById('rule-R1')?.classList.contains('rubric-row-focus')).toBe(false);
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalled();
    });
    // A re-fetch rebuilds the table, so assert on the newest scroll.
    const calls = scrollIntoView.mock.calls.length - 1;
    expect(scrollIntoView.mock.instances[calls]).toBe(focused);
    expect(scrollIntoView.mock.calls[calls][0]).toEqual({ block: 'center' });
  });

  it('says so when the rubric is empty', async () => {
    mockApi({ rubric: { rules: [] } });
    await renderWithApp(<Rubric />, { hash: '#view=rubric&project=1' });

    expect(await screen.findByText('No rubric available')).toBeTruthy();
    expect(
      screen.getByText('The analyzer rubric file was not found in this build.'),
    ).toBeTruthy();
  });

  it('reports a failed read instead of an empty page', async () => {
    mockApi({ rubric: new Error('rubric file unreadable') });
    await renderWithApp(<Rubric />, { hash: '#view=rubric&project=1' });

    expect(await screen.findByText('Could not read the rubric')).toBeTruthy();
    expect(screen.getByText('500 rubric file unreadable')).toBeTruthy();
  });
});
