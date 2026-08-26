import { mockApi, renderWithApp } from '../../test/utils';

import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useApp } from '../../app/state';
import { setDefects } from '../../lib/labels';
import Journey from './Journey';

/** Every URL the fetch double was asked for, in call order. */
function fetchUrls(): string[] {
  const mock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
  return mock.mock.calls.map((c) => String(c[0]));
}

/** Drives the shell controls the view itself does not own (topbar project pick). */
function Controls() {
  const { setProject } = useApp();
  return (
    <button type="button" onClick={() => setProject(2)}>
      switch project
    </button>
  );
}

const RP = {
  project_id: 1,
  project_name: 'demo',
  defects: {},
  status: { configured: false, reachable: false, note: 'RP names: —' },
};

const LAUNCHES = {
  launches: [
    {
      launch_id: 5,
      launch_name: 'nightly regression',
      launch_number: 42,
      item_count: 9,
      labeled_count: 4,
    },
    { launch_id: 6, launch_name: null, launch_number: null, item_count: 2, labeled_count: 0 },
  ],
};

const ITEMS = {
  items: [
    {
      item_id: 7,
      item_name: 'checkout pays with card',
      exc_text: 'TimeoutException',
      issue_type: 'pb001',
      label_group: 'pb',
      is_auto_analyzed: true,
      ui_url: 'https://rp.example/ui/#demo/launches/all/5/7',
    },
    {
      item_id: 8,
      item_name: 'checkout pays with paypal',
      exc_text: null,
      issue_type: 'ti001',
      label_group: 'ti',
      is_auto_analyzed: false,
      ui_url: null,
    },
  ],
};

const NOW = Date.now();
const iso = (daysAgo: number) => new Date(NOW - daysAgo * 86400000).toISOString();

const JOURNEY = {
  rp: RP,
  item: {
    item_id: 7,
    item_name: 'checkout pays with card',
    issue_type: 'pb001',
    label_group: 'pb',
    is_auto_analyzed: true,
    ui_url: 'https://rp.example/ui/#demo/launches/all/5/7',
    launch_id: 5,
    launch_url: 'https://rp.example/ui/#demo/launches/all/5',
    log_count: 12,
    test_case_hash: 998877,
  },
  signature: {
    exc_text: 'TimeoutException',
    msg_text: 'timed out after 30 s',
    top_frames: ['com.shop.Checkout.pay', 'com.shop.Http.call'],
    template_ids: [11, 12],
    status_codes: ['504'],
    exception_fp: 'fp123',
    error_hash: 'hash999',
    emb_model_ver: 'e5-small-v2',
    has_emb: true,
  },
  templates: [
    { template_id: 11, pattern: 'timed out after <NUM> s', token_count: 5, match_count: 30 },
    { template_id: 12, pattern: 'gateway <*> unreachable', token_count: 3, match_count: 8 },
  ],
  grouping: {
    group_id: 3,
    fingerprint: 'fpx7788',
    member_count: 6,
    dominant: true,
    si_prior: 0.6,
    launch_failed_count: 10,
    members: [
      { item_id: 7, label_group: 'pb', issue_type: 'pb001', is_self: true },
      { item_id: 8, label_group: 'ti', issue_type: 'ti001', is_self: false },
      { item_id: 21, label_group: 'pb', issue_type: 'pb001', is_self: false },
      { item_id: 22, label_group: 'pb', issue_type: 'pb001', is_self: false },
      { item_id: 23, label_group: 'si', issue_type: 'si001', is_self: false },
    ],
  },
  matching: {
    has_suggestion: true,
    stage: 'C',
    stage_label: 'hybrid + GBM',
    stage_note: 'top-20 fused by RRF',
    matched_item_id: null,
    matched_item_url: null,
    matched_mode_id: null,
    matched_mode: null,
    suggestion_id: 555,
  },
  reconstruction: {
    hybrid_retrieval_version: 4,
    note: 'lexical leg weighted, dense leg active',
    query: {
      salient_terms: 'timeout gateway',
      template_ids: [11, 12],
      emb_model_ver: 'e5-small-v2',
      dense_active: true,
    },
    candidates: [
      {
        item_id: 7,
        ui_url: null,
        issue_type: 'pb001',
        label_source: 'rp',
        sparse_rank: 1,
        dense_rank: 1,
        cosine: 0.99,
        jaccard_templates: 1,
        rrf_score: 0.0328,
        is_self: true,
        exception_fp: 'fp123',
        error_hash: 'hash999',
        launch_number: 42,
        mode_id: null,
      },
      {
        item_id: 21,
        ui_url: 'https://rp.example/ui/21',
        issue_type: 'pb001',
        label_source: 'human_ui',
        sparse_rank: 2,
        dense_rank: 3,
        cosine: 0.81,
        jaccard_templates: 0.5,
        rrf_score: 0.0315,
        is_self: false,
        exception_fp: 'fp123',
        error_hash: 'hashother',
        launch_number: 41,
        mode_id: 9,
      },
      {
        item_id: 22,
        ui_url: null,
        issue_type: 'ti001',
        label_source: 'ai_suggested',
        sparse_rank: 4,
        dense_rank: null,
        cosine: null,
        jaccard_templates: 0.2,
        rrf_score: 0.0155,
        is_self: false,
        exception_fp: 'fpother',
        error_hash: 'hashother2',
        launch_number: 40,
        mode_id: null,
      },
    ],
  },
  decision: {
    band: 'suggest',
    method: 'gbm',
    predicted_label: 'pb001',
    predicted_group: 'pb',
    confidence: 0.62,
    tau_suggest: 0.45,
    tau_auto: 0.75,
    explanation: 'The gateway timed out on the same call as three earlier product bugs.',
    model_ver: 'gbm-2024.7',
    llm_used: false,
    outcome: 'pending',
    abstain_reason: null,
    judge: null,
    features: [
      {
        key: 'top1_cosine',
        label: 'closest neighbour cosine',
        value: 0.9,
        group: 'retrieval',
        definition: 'cosine of the closest labeled neighbour',
        range: '0..1',
        default: 0,
      },
      {
        key: 'top1_src_weight',
        label: 'closest neighbour source weight',
        value: 0.9,
        group: 'retrieval',
        definition: 'provenance weight of the closest neighbour label',
        range: '0..1',
        default: 0,
      },
      {
        key: 'hist_pb_share',
        label: 'past product bug share',
        value: 0.4,
        group: 'history',
        definition: 'share of past runs labeled product bug',
        range: '0..1',
        default: 0,
      },
      {
        key: 'si_prior',
        label: 'burst prior',
        value: 0.6,
        group: 'grouping',
        definition: 'burst prior from the launch group',
        range: '0..0.9',
        default: 0,
      },
    ],
    feature_count: 4,
    feature_total: 46,
    extra_snapshot: { log_count: 12 },
    coldstart_provisional: false,
  },
  llm: { events: [], role_state: {} },
  feedback: [
    {
      event_id: 2,
      old_label: 'ti001',
      old_group: 'ti',
      new_label: 'pb001',
      new_group: 'pb',
      source: 'rp_defect_update',
      suggestion_id: 555,
      ts: iso(2),
    },
    {
      event_id: 1,
      old_label: null,
      old_group: null,
      new_label: 'ti001',
      new_group: 'ti',
      source: 'ai_suggested',
      suggestion_id: 555,
      ts: iso(9),
    },
  ],
};

// Same item, the other half of the matrix: solo group with no burst signal, an
// abstaining matcher with nothing retrieved, and an explainer the guardrail
// rejected.
const JOURNEY_ABSTAIN = {
  ...JOURNEY,
  grouping: {
    group_id: 4,
    fingerprint: 'fpx7788',
    member_count: 1,
    dominant: true,
    si_prior: 0,
    launch_failed_count: null,
    members: [{ item_id: 7, label_group: 'pb', issue_type: 'pb001', is_self: true }],
  },
  matching: {
    has_suggestion: true,
    stage: 'abstain',
    stage_label: 'abstain',
    stage_note: 'no trusted candidate',
    matched_item_id: null,
    matched_item_url: null,
    matched_mode_id: null,
    matched_mode: null,
    suggestion_id: 556,
  },
  reconstruction: {
    hybrid_retrieval_version: 4,
    note: '',
    query: { salient_terms: 'timeout', template_ids: [11], emb_model_ver: 'e5-small-v2', dense_active: false },
    candidates: [],
  },
  decision: {
    band: 'abstain',
    method: 'gbm',
    predicted_label: 'ti001',
    predicted_group: 'ti',
    confidence: 0.31,
    tau_suggest: 0.45,
    tau_auto: 0.75,
    explanation: '',
    model_ver: 'gbm-2024.7',
    llm_used: true,
    outcome: 'ignored',
    abstain_reason: 'gbm_below_suggest',
    judge: null,
    features: [],
    feature_count: 0,
    feature_total: 46,
    extra_snapshot: {},
    coldstart_provisional: false,
  },
  llm: {
    events: [
      {
        role: 'explainer',
        outcome: 'schema_fail',
        model: 'gpt-x',
        cache_hit: false,
        latency_ms: 1500,
        created_at: iso(0),
        output: null,
      },
    ],
    role_state: {},
  },
  feedback: [],
};

function bootFixtures(overrides: Record<string, unknown> = {}) {
  mockApi({ launches: LAUNCHES, items: ITEMS, 'item/': JOURNEY, ...overrides });
}

beforeEach(() => {
  setDefects({});
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderJourney(hash = '#view=journey&project=1&launch=5&item=7') {
  const result = await renderWithApp(<Journey />, { hash });
  await screen.findByText('Signature');
  return result;
}

describe('Item Journey', () => {
  it('renders the five pipeline stages, the burst grouping and the gauge', async () => {
    bootFixtures();
    const { container } = await renderJourney();

    const pills = container.querySelectorAll('.stepper .stage-pill');
    expect(pills).toHaveLength(5);
    expect([...pills].map((p) => p.querySelector('.s-k')?.textContent)).toEqual([
      '1 · Signature',
      '2 · Grouping',
      '3 · Matching',
      '4 · Decision',
      '5 · Feedback',
    ]);

    // burst = dominant AND more than one member
    const grouping = container.querySelector('#j-grouping');
    expect(grouping?.classList.contains('burst-accent')).toBe(true);
    expect(grouping?.querySelector('.card-title')?.textContent).toContain('burst');

    // gauge: banded arcs + the confidence read-out
    const gauge = container.querySelector('.gauge-wrap svg');
    expect(gauge?.querySelectorAll('path')).toHaveLength(3);
    expect(gauge?.textContent).toContain('0.62');
    expect([...container.querySelectorAll('.thresh-chip')].map((c) => c.textContent)).toEqual([
      '.45',
      '.75',
    ]);

    // feedback timeline: one entry per event, oldest first
    const events = container.querySelectorAll('ol.tl > li');
    expect(events).toHaveLength(2);
    expect(events[1].classList.contains('now')).toBe(true);
  });

  it('writes the deterministic takeaway sentences', async () => {
    bootFixtures();
    const { container } = await renderJourney();
    const takeaways = [...container.querySelectorAll('.takeaway')].map((p) => p.textContent);

    expect(takeaways).toContain(
      'Burst: a new fingerprint covers 6 of 10 failures in this launch (60%) — strong System Issue prior applied (dominant; si_prior 0.60).',
    );
    expect(takeaways).toContain(
      'No exact hash, no KB short-circuit — went to hybrid retrieval: FTS + cosine fused by RRF, top-20 → GBM scored the evidence (Stage C).',
    );
    expect(takeaways).toContain(
      'Suggests Product bug group — the learned model leans this way, a human confirms (gbm; 0.45 ≤ 0.62 < τ_auto 0.75)',
    );
    const feedback = takeaways.find((t) => t?.startsWith('Labeled 2×'));
    expect(feedback).toContain(
      'Labeled 2×: latest ti001 → pb001 by human (RP defect edit) (correction) — ',
    );
    expect(feedback).toContain('; first ');
  });

  it('shows the C-stage funnel, candidate strip and the drift note', async () => {
    bootFixtures();
    const { container } = await renderJourney();

    const nodes = [...container.querySelectorAll('.fn-node')];
    expect(nodes.map((n) => n.getAttribute('data-state'))).toEqual(['fell', 'fell', 'won']);
    expect(nodes[2].querySelector('.fn-art')?.textContent).toBe('top-1 of 2 → GBM');
    expect(container.querySelector('.fn-term')?.textContent).toBe('decided at C');

    // top non-self candidates only, self excluded
    const cards = container.querySelectorAll('.cand-card');
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain('item 21');

    expect(container.textContent).toContain(
      'stored top1_cosine at decision time 0.900 vs 0.810 now — retrieval has drifted since the decision.',
    );
  });

  it('auto-expands the evidence group holding the largest single feature', async () => {
    bootFixtures();
    const { container } = await renderJourney();

    const heads = [...container.querySelectorAll('.evi-head')];
    expect(heads.map((h) => h.getAttribute('aria-expanded'))).toEqual(['true', 'false', 'false']);
    expect(heads[0].textContent).toContain('similar past failures (retrieval)');

    fireEvent.click(heads[1]);
    expect(heads[1].getAttribute('aria-expanded')).toBe('true');
  });

  it('navigates to a co-failing item when a member dot is clicked', async () => {
    bootFixtures();
    const { container } = await renderJourney();

    const dots = container.querySelectorAll('.m-dot');
    expect(dots).toHaveLength(5);
    expect(dots[0].classList.contains('self')).toBe(true);
    expect(container.querySelector('.dot-strip .chip')?.textContent).toBe('+1 more');

    fireEvent.click(dots[1]); // item 8 — in this launch
    await waitFor(() => {
      expect(window.location.hash).toContain('item=8');
    });
  });

  it('explains a permalinked item that the analyzer never indexed', async () => {
    bootFixtures();
    await renderWithApp(<Journey />, { hash: '#view=journey&project=1&launch=5&item=4242' });

    expect(
      await screen.findByText("Item 4242 is not in the analyzer's index"),
    ).toBeTruthy();
    expect(window.location.hash).toContain('item=4242');
  });

  it('renders the honest empty state when the project has no launches', async () => {
    bootFixtures({ launches: { launches: [] } });
    await renderWithApp(<Journey />, { hash: '#view=journey&project=1' });

    expect(await screen.findByText('No launches')).toBeTruthy();
    expect(screen.getByText('This project has no test items yet.')).toBeTruthy();
  });

  it('links the newest feedback event back to the decision card', async () => {
    bootFixtures();
    const { container } = await renderJourney();

    const link = container.querySelector('button.js-link') as HTMLButtonElement;
    expect(link.textContent).toBe('sug 555 ↑ stage 4');
    const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView');
    fireEvent.click(link);
    expect(scrollIntoView).toHaveBeenCalled();
    scrollIntoView.mockRestore();
  });

  it('collapses long template lists behind a drawer', async () => {
    const templates = Array.from({ length: 8 }, (_, i) => ({
      template_id: 100 + i,
      pattern: `pattern <NUM> ${i}`,
      token_count: 3,
      match_count: i,
    }));
    bootFixtures({ 'item/': { ...JOURNEY, templates } });
    const { container } = await renderJourney();

    const signature = container.querySelector('#j-signature') as HTMLElement;
    expect(signature.querySelector('.section-title')?.textContent).toBe(
      'Referenced Drain3 templates (8)',
    );
    const drawer = signature.querySelector('details.tpl-more') as HTMLElement;
    expect(drawer.querySelector('summary .more')?.textContent).toBe('show 3 more templates');
    expect(drawer.querySelector('summary .less')?.textContent).toBe('show fewer templates');
    expect(drawer.querySelectorAll('.pattern')).toHaveLength(3);
    expect(signature.querySelectorAll('.pattern')).toHaveLength(8);
  });

  it('tells the solo / abstain / guardrail story honestly', async () => {
    bootFixtures({ 'item/': JOURNEY_ABSTAIN });
    const { container } = await renderJourney();
    const takeaways = [...container.querySelectorAll('.takeaway')].map((p) => p.textContent);

    expect(takeaways).toContain(
      'Alone in this launch — no other failure shares this signature; no group diagnosis applied (solo group).',
    );
    expect(container.querySelector('.si-note')?.textContent).toBe(
      'burst signal (si_prior)No burst signal: fingerprint not new to history or share of launch failures below the gate (new fp · ≥5 members · >40% share).',
    );

    expect(takeaways).toContain(
      'Nothing matched — no inheritable hash (A), no confident mode (B), no trusted candidate (C); item stays ti. Reason on the Decision card.',
    );
    expect([...container.querySelectorAll('.fn-node')].map((n) => n.getAttribute('data-state'))).toEqual([
      'fell',
      'fell',
      'fell',
    ]);
    expect(container.querySelector('.fn-term')?.textContent).toBe('abstain → To Investigate');
    expect(screen.getByText('No candidates retrieved')).toBeTruthy();

    expect(takeaways).toContain(
      'Abstained → To Investigate — confidence below the suggest bar (gbm; p* 0.31 < τ_suggest 0.45)',
    );
    expect(container.querySelector('.abstain-reason')?.textContent).toBe(
      'The model scored every label below the suggestion bar of 0.45 (tau_suggest).gbm_below_suggest',
    );
    expect(container.textContent).toContain('No stored feature vector for this suggestion.');

    // guardrail: the withheld-explanation sentence and the guard note, no tiles
    expect(takeaways).toContain(
      'LLM explanation withheld — output failed schema_fail, so nothing was persisted (guardrail; the decision itself is unaffected).',
    );
    expect(container.querySelector('.guard-note .g-tag')?.textContent).toBe(
      '⛨ guardrail fired · schema_fail',
    );
    expect(container.querySelector('.llm-tiles')).toBeNull();

    expect(screen.getByText('No label events')).toBeTruthy();
  });

  it('opens the first item of a launch the user picks, even after a back step', async () => {
    // Repro: open item 8, step back to item 7 (the link arms item 7 again, but
    // the item list is not refetched so nothing consumes it), then pick another
    // launch. Item 7 is not in launch 6 — but it IS in the index, so claiming
    // otherwise would be a lie.
    bootFixtures({
      'items?project=1&launch=6': {
        items: [
          {
            item_id: 9,
            item_name: 'search returns hits',
            exc_text: 'AssertionError',
            issue_type: 'ab001',
            label_group: 'ab',
            is_auto_analyzed: false,
            ui_url: null,
          },
        ],
      },
      'item/1/9': {
        ...JOURNEY,
        item: { ...JOURNEY.item, item_id: 9, item_name: 'search returns hits', launch_id: 6 },
      },
    });
    await renderJourney('#view=journey&project=1&launch=5&item=8');

    // browser Back to item 7 — same launch, so the item list stays put
    await act(async () => {
      window.location.hash = '#view=journey&project=1&launch=5&item=7';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    await waitFor(() => expect(window.location.hash).toContain('item=7'));

    fireEvent.click(screen.getByText('launch 6'));

    await screen.findByText('search returns hits');
    expect(screen.queryByText("Item 7 is not in the analyzer's index")).toBeNull();
    await waitFor(() => expect(window.location.hash).toContain('item=9'));
  });

  it('drops the old project ids when the project changes', async () => {
    bootFixtures({ 'launches?project=2': { launches: [] } });
    await renderWithApp(
      <>
        <Controls />
        <Journey />
      </>,
      { hash: '#view=journey&project=1&launch=5&item=7' },
    );
    await screen.findByText('Signature');

    fireEvent.click(screen.getByText('switch project'));
    await screen.findByText('No launches');

    // launch 5 and item 7 belong to project 1: no request may carry them into 2
    await waitFor(() => expect(screen.getByText('Pick a launch, then an item')).toBeTruthy());
    const urls = fetchUrls();
    expect(urls.some((u) => u.includes('items?project=2'))).toBe(false);
    expect(urls.some((u) => u.includes('item/2/'))).toBe(false);
  });

  it('keeps both threshold chips when the two thresholds are equal', async () => {
    // Equal thresholds used to give the two chips the same React key. React
    // keeps both on the first paint but warns, and drops one the next time the
    // gauge re-renders — so the warning is the thing to assert on.
    const errors: string[] = [];
    const spy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
      errors.push(args.map(String).join(' '));
    });
    try {
      bootFixtures({
        'item/': {
          ...JOURNEY,
          decision: { ...JOURNEY.decision, tau_suggest: 0.5, tau_auto: 0.5 },
        },
      });
      const { container } = await renderJourney();

      expect([...container.querySelectorAll('.thresh-chip')].map((c) => c.textContent)).toEqual([
        '.50',
        '.50',
      ]);
      expect(container.querySelectorAll('.gauge-wrap svg path')).toHaveLength(3);
      expect(errors.filter((e) => e.includes('same key'))).toEqual([]);
    } finally {
      spy.mockRestore();
    }
  });

  it('installs the RP defect names the journey payload carries', async () => {
    bootFixtures({
      'item/': {
        ...JOURNEY,
        rp: {
          ...RP,
          defects: { pb001: { name: 'Product Bug', short_name: 'PB', color: '#ec3900' } },
        },
      },
    });
    const { container } = await renderJourney();

    expect(container.querySelector('h2 .badge.defect')?.textContent).toContain('Product Bug');
  });

  it('remembers the drawer open state per key', async () => {
    localStorage.setItem('inspector.eng.grouping', '1');
    bootFixtures();
    const { container } = await renderJourney();

    const drawers = [...container.querySelectorAll('details.eng')];
    const grouping = drawers.find((d) => d.closest('#j-grouping'));
    expect((grouping as HTMLDetailsElement).open).toBe(true);
    const decision = drawers.find((d) => d.closest('#j-decision'));
    expect((decision as HTMLDetailsElement).open).toBe(false);
  });
});
