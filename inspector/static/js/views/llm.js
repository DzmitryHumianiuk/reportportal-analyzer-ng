// LLM tab — per-project sidecar observability (spec 04 tables, read-only).
// Every string is a real column value, a deterministic derivation, or an honest
// absence (including "0 judge events" and "breaker state not persisted").
import { api } from '../api.js';
import { updateHash } from '../app.js';
import {
  h, clear, card, emptyState, loading, relTime, fmt, idChip, engDrawer,
} from '../util.js';

const ROLE_GLOSS = {
  coldstart: 'provisional labels in zero-label projects',
  explainer: 'rationale on confident suggestions',
  judge: 'mid-band candidate re-ranking',
  extractor: 'structured features from logs',
};
const TRIGGER_GLOSS = {
  coldstart: 'classical abstain in a zero-history project',
  explainer: 'decision confidence ≥ τ_suggest 0.45',
  judge: 'suggest route at confidence [0.45, 0.75) with ≥ 2 Stage-C candidates',
  extractor: 'any analyzed failure',
};
const GUARD = ['schema_fail', 'validation_fail'];
const UNAVAIL = ['timeout', 'breaker_open', 'dropped'];

// filter state (permalinked as lrole / loutcome)
const lstate = { role: 'all', outcome: 'all' };
export function setLlmState({ role, outcome }) {
  lstate.role = role || 'all';
  lstate.outcome = outcome || 'all';
}

function latFmt(ms) {
  if (ms == null) return '—';
  if (ms === 0) return 'cache';
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : Math.round(ms) + ' ms';
}
function ocClass(outcome) {
  if (outcome === 'ok') return 'oc-ok';
  if (GUARD.includes(outcome)) return 'oc-guard';
  return 'oc-unavail';
}

export async function renderLlm(root, app) {
  clear(root);
  root.appendChild(loading('Loading LLM bookkeeping…'));
  let summary;
  try {
    summary = await api.llmSummary(app.project);
  } catch (e) {
    clear(root).appendChild(emptyState('🚫', 'LLM summary failed', String(e.message || e)));
    return;
  }
  clear(root);

  // header line
  root.appendChild(h('div', { style: { marginBottom: '16px' } },
    h('h2', { style: { margin: '0 0 4px', fontSize: '20px', fontWeight: 600 } }, 'LLM'),
    h('div', { class: 'note' }, 'llm_event · llm_cache · llm_role_state — read-only bookkeeping'),
    summary.model
      ? h('div', { class: 'note', style: { marginTop: '4px' } }, 'model ', h('span', { class: 'mono' }, summary.model),
        ' — from llm_event rows; the Inspector reads the DB, not live sidecar health.')
      : null));

  if (!summary.event_total) {
    root.appendChild(card('LLM', {}, emptyState('🤖', 'No LLM activity',
      'No llm_event rows in this project — either the analyzer LLM is off, or no analyzed failure has reached an enqueue condition yet. Absence of events is the only signal this DB carries.',
      'analyzer.llm_event')));
    return;
  }

  const container = h('div', { class: 'grid', style: { gap: '16px' } });
  root.appendChild(container);
  container.append(
    rolesCard(summary),
    outcomeMixCard(summary),
    latencyCard(summary),
    availabilityCard(summary),
    activityCard(app),
    cacheCard(app),
  );
}

// ---- Card 1: roles at a glance ----
function rolesCard(summary) {
  const c = card('Roles at a glance', { sub: 'the four async roles, project-scoped' });
  const body = c.querySelector('.card-body');
  const grid = h('div', { class: 'role-grid' });
  for (const r of summary.roles) {
    const zero = r.event_count === 0;
    const panel = h('div', { class: 'role-panel' + (zero ? ' empty' : '') });
    panel.appendChild(h('h4', {}, r.role));
    panel.appendChild(h('div', { class: 'gloss' }, ROLE_GLOSS[r.role] || ''));
    if (zero) {
      const contract = r.role === 'judge'
        ? 'fires only on the suggest route at confidence [0.45, 0.75) with ≥ 2 Stage-C candidates; none occurred.'
        : `its trigger never fired here (${TRIGGER_GLOSS[r.role] || ''}).`;
      panel.appendChild(h('div', { class: 'takeaway', style: { fontSize: '13px', margin: '0 0 6px' } },
        h('b', {}, '0 events'), ' — role enabled by default, wired, never fired in this project.'));
      panel.appendChild(h('div', { class: 'note', title: contract }, contract));
    } else {
      panel.appendChild(h('div', {}, h('span', { class: 'counter' }, String(r.ok), h('span', { class: 'u' }, 'ok'))));
      const chips = h('div', { class: 'chip-row', style: { marginTop: '8px' } });
      for (const [oc, n] of Object.entries(r.outcomes)) {
        if (oc === 'ok') continue;
        chips.appendChild(h('span', { class: `oc-chip ${ocClass(oc)}` }, `${oc} ${n}`));
      }
      if (r.from_cache) chips.appendChild(h('span', { class: 'chip' }, `cache ${r.from_cache}`));
      panel.appendChild(chips);
      panel.appendChild(h('div', { class: 'note', style: { marginTop: '6px' } },
        r.last_event ? `last event ${relTime(r.last_event)}` : 'no events'));
    }
    panel.appendChild(stateLine(r.state));
    grid.appendChild(panel);
  }
  body.appendChild(grid);
  return c;
}

function stateLine(state) {
  const line = h('div', { class: 'state-line' });
  if (!state) {
    line.append(h('i', { class: 's-dot', style: { background: 'var(--good)' } }),
      h('span', {}, 'on (default — no llm_role_state row)'));
    line.title = 'The nightly eval has never flipped this role here; it disables a role only when demonstrably worse than the classical path (N ≥ 50).';
  } else if (state.enabled === false) {
    line.append(h('i', { class: 's-dot', style: { background: 'var(--warning)' } }),
      h('span', {}, `disabled — ${state.reason || 'reason not recorded'} ${state.decided_at ? relTime(state.decided_at) : ''}`));
  } else {
    line.append(h('i', { class: 's-dot', style: { background: 'var(--good)' } }),
      h('span', {}, `on (re-enabled${state.reason ? ', ' + state.reason : ''})`));
  }
  return line;
}

// ---- Card 2: outcome mix ----
function outcomeMixCard(summary) {
  const c = card('Outcome mix', { sub: 'guardrails as signal — a rejection means the validator worked' });
  const body = c.querySelector('.card-body');
  const total = summary.event_total;
  let ok = 0; let guard = 0; let unavail = 0;
  for (const r of summary.roles) {
    ok += r.outcomes.ok || 0;
    guard += GUARD.reduce((s, o) => s + (r.outcomes[o] || 0), 0);
    unavail += UNAVAIL.reduce((s, o) => s + (r.outcomes[o] || 0), 0);
  }
  body.appendChild(h('p', { class: 'takeaway', style: { fontSize: '13px' } },
    h('b', {}, `${total} LLM calls`), ` · ${Math.round(100 * ok / total)}% ok · ${guard} rejected by guardrails · ${unavail} unavailable`,
    h('span', { class: 'muted' }, ' — rejections mean the validator worked, not that the pipeline erred.')));

  for (const r of summary.roles) {
    if (!r.event_count) continue;
    const okN = r.outcomes.ok || 0;
    const gN = GUARD.reduce((s, o) => s + (r.outcomes[o] || 0), 0);
    const uN = UNAVAIL.reduce((s, o) => s + (r.outcomes[o] || 0), 0);
    const seg = (n, cls) => (n ? h('i', { class: cls, style: { width: `${100 * n / r.event_count}%`, background: 'var(--oc)' }, title: `${n}` }) : null);
    body.appendChild(h('div', { class: 'ocbar-row' },
      h('span', { class: 'ocbar-role' }, r.role),
      h('span', { class: 'ocbar' },
        h('span', { class: 'oc-ok', style: { display: 'contents' } }, seg(okN, 'oc-ok')),
        h('span', { class: 'oc-guard', style: { display: 'contents' } }, seg(gN, 'oc-guard')),
        h('span', { class: 'oc-unavail', style: { display: 'contents' } }, seg(uN, 'oc-unavail'))),
      h('span', { class: 'ocbar-n' }, `${okN} ok · ${gN} guard · ${uN} unavail`)));
  }
  body.appendChild(h('div', { class: 'oc-legend' },
    h('span', { class: 'grp' }, h('span', { class: 'lg-title' }, 'result'),
      h('span', { class: 'lg-item' }, h('i', { style: { background: 'var(--rp-status-passed)' } }), 'ok')),
    h('span', { class: 'grp' }, h('span', { class: 'lg-title' }, 'guardrail fired (output rejected)'),
      h('span', { class: 'lg-item' }, h('i', { style: { background: 'var(--rp-topaz)' } }), 'schema_fail / validation_fail')),
    h('span', { class: 'grp' }, h('span', { class: 'lg-title' }, 'sidecar unavailable'),
      h('span', { class: 'lg-item' }, h('i', { style: { background: 'var(--rp-sm-warning)' } }), 'timeout / breaker_open / dropped'))));
  return c;
}

// ---- Card 3: latency ----
function latencyCard(summary) {
  const c = card('Latency', { sub: 'wall-clock per call, ok events only, cache hits excluded' });
  const body = c.querySelector('.card-body');
  const withLat = summary.roles.filter((r) => r.latency);
  const globalMax = Math.max(1, ...withLat.map((r) => r.latency.max || 0));
  for (const r of summary.roles) {
    const lat = r.latency;
    if (!lat) {
      body.appendChild(h('div', { class: 'lat-row' },
        h('span', { class: 'lat-role' }, r.role), h('span', { class: 'lat-empty' }, '—')));
      continue;
    }
    const p50pct = 100 * (lat.p50 || 0) / globalMax;
    const p90pct = 100 * (lat.p90 || 0) / globalMax;
    body.appendChild(h('div', { class: 'lat-row' },
      h('span', { class: 'lat-role' }, r.role),
      h('div', {},
        h('div', { class: 'lat-track', title: 'median inference latency, ok calls only, cache hits excluded' },
          h('i', { class: 'lat-fill', style: { width: `${p50pct}%` } }),
          h('b', { class: 'lat-tick', style: { left: `${p90pct}%` } })),
        h('div', { class: 'lat-nums' }, 'p50 ', h('b', {}, latFmt(lat.p50)), ' · p90 ', h('b', {}, latFmt(lat.p90)),
          ' · max ', h('b', {}, latFmt(lat.max)), ` · n ${lat.n}`))));
  }
  return c;
}

// ---- Card 4: activity stream ----
function activityCard(app) {
  const c = card('Activity stream', { sub: 'newest-first, cap 50' });
  const body = c.querySelector('.card-body');
  const filters = h('div', { class: 'filter-row' });
  const listWrap = h('div', {});

  const roleChips = ['all', 'coldstart', 'explainer', 'judge', 'extractor'];
  const outChips = [['all', 'all'], ['ok', 'ok'], ['guardrail', 'guardrail'], ['unavailable', 'unavailable']];
  filters.appendChild(h('span', { class: 'filter-lbl' }, 'role'));
  for (const r of roleChips) {
    filters.appendChild(h('button', { class: 'fchip' + (lstate.role === r ? ' on' : ''),
      onclick: () => { lstate.role = r; updateHash({ lrole: r === 'all' ? null : r }); load(); } }, r));
  }
  filters.appendChild(h('span', { class: 'filter-lbl', style: { marginLeft: '10px' } }, 'outcome'));
  for (const [k, v] of outChips) {
    filters.appendChild(h('button', { class: 'fchip' + (lstate.outcome === v ? ' on' : ''),
      onclick: () => { lstate.outcome = v; updateHash({ loutcome: v === 'all' ? null : v }); load(); } }, k));
  }
  body.append(filters, listWrap);

  async function load() {
    // reflect active chip state
    [...filters.querySelectorAll('.fchip')].forEach((b) => {
      const txt = b.textContent;
      const isRole = roleChips.includes(txt);
      b.classList.toggle('on', isRole ? txt === lstate.role : (txt === 'all' ? lstate.outcome === 'all' : txt === lstate.outcome));
    });
    clear(listWrap).appendChild(loading());
    let d;
    try {
      d = await api.llmEvents(app.project, lstate.role, lstate.outcome, 50);
    } catch (e) {
      clear(listWrap).appendChild(emptyState('🚫', 'Events failed', String(e.message || e)));
      return;
    }
    clear(listWrap);
    if (!d.events.length) {
      listWrap.appendChild(emptyState('🤖', 'No events for this filter',
        lstate.role === 'judge'
          ? 'No judge events. The judge fires only on suggest-route decisions in [0.45, 0.75) with ≥ 2 candidates — none occurred on this install.'
          : 'No llm_event rows match this role/outcome filter in this project.'));
      return;
    }
    const ol = h('ol', { class: 'llm-tl' });
    for (const e of d.events) {
      const row = h('li', {},
        h('span', { class: 'oc-dot ' + ocClass(e.outcome), title: e.outcome }),
        h('span', { class: 'tl-role' }, e.role),
        h('span', { class: 'tl-mid' },
          idChip(`item ${e.item_id}`, `#view=journey&project=${app.project}&launch=${e.launch_id}&item=${e.item_id}`, 'idchip'),
          h('span', { class: `oc-chip ${ocClass(e.outcome)}` }, e.outcome),
          h('span', { class: 'chip mono' }, e.model || '—'),
          e.cache_hit ? h('span', { class: 'chip' }, 'cache') : null,
          e.output ? outputDrawer(e.output) : null),
        h('span', { class: 'tl-right' }, latFmt(e.latency_ms), ' · ', relTime(e.created_at)));
      ol.appendChild(row);
    }
    listWrap.appendChild(ol);
    if (d.count === d.limit) listWrap.appendChild(h('div', { class: 'note', style: { marginTop: '8px' } }, `showing the latest ${d.limit} events (query cap).`));
  }
  load();
  return c;
}

// LLM output is untrusted model text — always rendered as a text node (never html:).
function outputDrawer(output) {
  const d = h('details', { class: 'out-drawer' });
  const pre = h('pre', { class: 'out-json' });
  pre.textContent = JSON.stringify(output, null, 2);
  d.append(h('summary', {}, 'output'), pre);
  return d;
}

// ---- Card 5: availability honesty (the breaker panel that refuses to lie) ----
function availabilityCard(summary) {
  const c = card('Availability', { sub: 'breaker state is in-process on the analyzer — not persisted, not shown as fact' });
  const body = c.querySelector('.card-body');
  let breakerOpen = 0; let timeouts = 0; let lastEvent = null;
  for (const r of summary.roles) {
    breakerOpen += r.outcomes.breaker_open || 0;
    timeouts += r.outcomes.timeout || 0;
    if (r.last_event && (!lastEvent || r.last_event > lastEvent)) lastEvent = r.last_event;
  }
  body.appendChild(h('div', { class: 'honesty' },
    h('div', { class: 'h-tag' }, 'in-process state — not in the DB'),
    h('div', { class: 'note', style: { marginTop: '4px' } },
      'Breaker state is in-process on the analyzer and not persisted; liveness here is inferred from event flow only. A quiet log means either healthy-and-idle or disabled — the Inspector cannot distinguish.')));
  body.appendChild(h('div', { class: 'stat-row' },
    h('div', { class: 'st' }, h('div', { class: 'v' }, lastEvent ? relTime(lastEvent) : '—'), h('div', { class: 'l' }, 'last llm_event')),
    h('div', { class: 'st' }, h('div', { class: 'v' }, String(breakerOpen)), h('div', { class: 'l' }, 'breaker_open events')),
    h('div', { class: 'st' }, h('div', { class: 'v' }, String(timeouts)), h('div', { class: 'l' }, 'transport timeouts'))));
  body.appendChild(engDrawer('llm-breaker', 'Breaker limits (code contract, not runtime)',
    h('p', { class: 'note' }, 'Opens after 3 consecutive transport failures; cooldown 60 s doubling to a 15 min cap; the first job after cooldown is the half-open probe. Schema / validation failures never trip it — the server was up, the output was bad. From breaker.py; reset on analyzer restart.')));
  return c;
}

// ---- Card 6: extractor cache explorer ----
function cacheCard(app) {
  const c = card('Extractor cache', { sub: 'one model call per template-set per project, then reuse' });
  const body = c.querySelector('.card-body');
  body.appendChild(loading());
  api.llmCache(app.project, 'extractor', 50).then((d) => {
    clear(body);
    body.appendChild(h('p', { class: 'takeaway', style: { fontSize: '13px' } },
      h('b', {}, `${d.entries} cached extractor outputs`), ` · ${d.total_hits} hits`,
      h('span', { class: 'muted' }, ' — hits count role-call reuse only; feature-time reads are not counted, so real reuse is higher. Rows marked "no answer possible" hold a remembered failure, not an extraction, and are kept for 7 days.')));
    if (!d.rows.length) {
      body.appendChild(h('div', { class: 'note' }, 'No llm_cache rows for the extractor in this project.'));
      return;
    }
    const tbl = h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, ...['template_hash', 'model', 'hits', 'fresh', 'created', 'last hit', 'output'].map((t) => h('th', {}, t)))),
      // A negative row remembers that this input cannot be answered, so the
      // analyzer stops paying for a call that fails the same way every time. It
      // holds no extraction and carries no template hash on purpose, so say what
      // it is rather than leaving a row of dashes the reader has to decode.
      h('tbody', {}, ...d.rows.map((r) => h('tr', { class: r.negative ? 'row-negative' : '' },
        h('td', { class: 'mono' }, r.negative
          ? h('span', { class: 'tag-negative' }, 'no answer possible')
          : (r.template_hash || '—')),
        h('td', { class: 'mono' }, r.model || '—'),
        h('td', { class: 'num' }, String(r.hits)),
        h('td', { class: 'mono' }, r.fresh ? 'fresh' : 'expired'),
        h('td', { class: 'mono' }, relTime(r.created_at)),
        h('td', { class: 'mono' }, r.last_hit_at ? relTime(r.last_hit_at) : '—'),
        h('td', {}, r.output ? outputDrawer(r.output) : '—')))));
    body.appendChild(h('div', { class: 'table-wrap' }, tbl));
  }).catch((e) => { clear(body).appendChild(emptyState('🚫', 'Cache failed', String(e.message || e))); });
  return c;
}
