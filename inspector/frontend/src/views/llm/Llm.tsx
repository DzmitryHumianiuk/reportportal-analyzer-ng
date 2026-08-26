// LLM tab — per-project sidecar observability (spec 04 tables, read-only).
// Every string is a real column value, a deterministic derivation, or an honest
// absence (including "0 judge events" and "breaker state not persisted").
//
// XSS: model output is untrusted text. It is always rendered as a React text
// child inside <pre> (never dangerouslySetInnerHTML).

import { useEffect, useState, type ReactNode } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type {
  LlmCacheResponse,
  LlmEventOutput,
  LlmEventsResponse,
  LlmRoleState,
  LlmRoleSummary,
  LlmSummaryResponse,
} from '../../app/types';
import { Card, ChipLink, EmptyState, EngDetails, Loading } from '../../components';
import { relTime } from '../../lib/format';
import './llm.css';

const ROLE_GLOSS: Record<string, string> = {
  coldstart: 'provisional labels in zero-label projects',
  explainer: 'rationale on confident suggestions',
  judge: 'mid-band candidate re-ranking',
  extractor: 'structured features from logs',
};
const TRIGGER_GLOSS: Record<string, string> = {
  coldstart: 'classical abstain in a zero-history project',
  explainer: 'decision confidence ≥ τ_suggest 0.45',
  judge: 'suggest route at confidence [0.45, 0.75) with ≥ 2 Stage-C candidates',
  extractor: 'any analyzed failure',
};
const GUARD = ['schema_fail', 'validation_fail'];
const UNAVAIL = ['timeout', 'breaker_open', 'dropped'];

const ROLE_CHIPS = ['all', 'coldstart', 'explainer', 'judge', 'extractor'];
const OUTCOME_CHIPS = ['all', 'ok', 'guardrail', 'unavailable'];

function latFmt(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms === 0) return 'cache';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function ocClass(outcome: string | null | undefined): string {
  if (outcome === 'ok') return 'oc-ok';
  if (outcome != null && GUARD.includes(outcome)) return 'oc-guard';
  return 'oc-unavail';
}

/** Outcome counters are optional in the payload; absent means "none counted". */
function outcomes(r: LlmRoleSummary): Record<string, number> {
  return r.outcomes || {};
}

function sumOf(r: LlmRoleSummary, keys: string[]): number {
  const oc = outcomes(r);
  return keys.reduce((s, k) => s + (oc[k] || 0), 0);
}

// --------------------------------------------------------------------------- //
// view
// --------------------------------------------------------------------------- //

type SummaryState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; summary: LlmSummaryResponse };

export default function Llm() {
  const { project, refreshTick } = useApp();
  const [state, setState] = useState<SummaryState>({ status: 'loading' });

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      try {
        const summary = await api.llmSummary(project);
        if (!cancelled) setState({ status: 'ready', summary });
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String((e as Error)?.message || e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  if (project == null || state.status === 'loading') {
    return <Loading text="Loading LLM bookkeeping…" />;
  }
  if (state.status === 'error') {
    return <EmptyState icon="🚫" title="LLM summary failed" body={state.message} />;
  }

  const { summary } = state;

  return (
    <>
      <div style={{ marginBottom: '16px' }}>
        <h2 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: 600 }}>LLM</h2>
        <div className="note">llm_event · llm_cache · llm_role_state — read-only bookkeeping</div>
        {summary.model ? (
          <div className="note" style={{ marginTop: '4px' }}>
            model <span className="mono">{summary.model}</span> — from llm_event rows; the Inspector
            reads the DB, not live sidecar health.
          </div>
        ) : null}
      </div>

      {!summary.event_total ? (
        <Card title="LLM">
          <EmptyState
            icon="🤖"
            title="No LLM activity"
            body="No llm_event rows in this project — either the analyzer LLM is off, or no analyzed failure has reached an enqueue condition yet. Absence of events is the only signal this DB carries."
            tag="analyzer.llm_event"
          />
        </Card>
      ) : (
        <div className="grid" style={{ gap: '16px' }}>
          <RolesCard summary={summary} />
          <OutcomeMixCard summary={summary} />
          <LatencyCard summary={summary} />
          <AvailabilityCard summary={summary} />
          <ActivityCard />
          <CacheCard />
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- //
// Card 1: roles at a glance
// --------------------------------------------------------------------------- //

function RolesCard({ summary }: { summary: LlmSummaryResponse }) {
  return (
    <Card title="Roles at a glance" sub="the four async roles, project-scoped">
      <div className="role-grid">
        {summary.roles.map((r) => (
          <RolePanel key={r.role} role={r} />
        ))}
      </div>
    </Card>
  );
}

function RolePanel({ role: r }: { role: LlmRoleSummary }) {
  const zero = r.event_count === 0;
  const contract =
    r.role === 'judge'
      ? 'fires only on the suggest route at confidence [0.45, 0.75) with ≥ 2 Stage-C candidates; none occurred.'
      : `its trigger never fired here (${TRIGGER_GLOSS[r.role] || ''}).`;
  const oc = outcomes(r);

  return (
    <div className={`role-panel${zero ? ' empty' : ''}`}>
      <h4>{r.role}</h4>
      <div className="gloss">{ROLE_GLOSS[r.role] || ''}</div>
      {zero ? (
        <>
          <div className="takeaway" style={{ fontSize: '13px', margin: '0 0 6px' }}>
            <b>0 events</b> — role enabled by default, wired, never fired in this project.
          </div>
          <div className="note" title={contract}>
            {contract}
          </div>
        </>
      ) : (
        <>
          <div>
            <span className="counter">
              {String(r.ok)}
              <span className="u">ok</span>
            </span>
          </div>
          <div className="chip-row" style={{ marginTop: '8px' }}>
            {Object.entries(oc)
              .filter(([name]) => name !== 'ok')
              .map(([name, n]) => (
                <span key={name} className={`oc-chip ${ocClass(name)}`}>
                  {`${name} ${n}`}
                </span>
              ))}
            {r.from_cache ? <span className="chip">{`cache ${r.from_cache}`}</span> : null}
          </div>
          <div className="note" style={{ marginTop: '6px' }}>
            {r.last_event ? `last event ${relTime(r.last_event)}` : 'no events'}
          </div>
        </>
      )}
      <StateLine state={r.state} />
    </div>
  );
}

const STATE_DEFAULT_TIP =
  'The nightly eval has never flipped this role here; it disables a role only when demonstrably worse than the classical path (N ≥ 50).';

function StateLine({ state }: { state?: LlmRoleState | null }) {
  if (!state) {
    return (
      <div className="state-line" title={STATE_DEFAULT_TIP}>
        <i className="s-dot" style={{ background: 'var(--good)' }} />
        <span>on (default — no llm_role_state row)</span>
      </div>
    );
  }
  if (state.enabled === false) {
    return (
      <div className="state-line">
        <i className="s-dot" style={{ background: 'var(--warning)' }} />
        <span>
          {`disabled — ${state.reason || 'reason not recorded'} ${
            state.decided_at ? relTime(state.decided_at) : ''
          }`}
        </span>
      </div>
    );
  }
  return (
    <div className="state-line">
      <i className="s-dot" style={{ background: 'var(--good)' }} />
      <span>{`on (re-enabled${state.reason ? `, ${state.reason}` : ''})`}</span>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Card 2: outcome mix
// --------------------------------------------------------------------------- //

function OutcomeMixCard({ summary }: { summary: LlmSummaryResponse }) {
  const total = summary.event_total;
  let ok = 0;
  let guard = 0;
  let unavail = 0;
  for (const r of summary.roles) {
    ok += outcomes(r).ok || 0;
    guard += sumOf(r, GUARD);
    unavail += sumOf(r, UNAVAIL);
  }

  return (
    <Card title="Outcome mix" sub="guardrails as signal — a rejection means the validator worked">
      <p className="takeaway" style={{ fontSize: '13px' }}>
        <b>{`${total} LLM calls`}</b>
        {` · ${Math.round((100 * ok) / total)}% ok · ${guard} rejected by guardrails · ${unavail} unavailable`}
        <span className="muted">
          {' — rejections mean the validator worked, not that the pipeline erred.'}
        </span>
      </p>
      {summary.roles
        .filter((r) => r.event_count)
        .map((r) => {
          const okN = outcomes(r).ok || 0;
          const gN = sumOf(r, GUARD);
          const uN = sumOf(r, UNAVAIL);
          const bar = (n: number, cls: string) =>
            n ? (
              <i
                className={cls}
                style={{ width: `${(100 * n) / r.event_count}%`, background: 'var(--oc)' }}
                title={`${n}`}
              />
            ) : null;
          return (
            <div className="ocbar-row" key={r.role}>
              <span className="ocbar-role">{r.role}</span>
              <span className="ocbar">
                <span className="oc-ok" style={{ display: 'contents' }}>
                  {bar(okN, 'oc-ok')}
                </span>
                <span className="oc-guard" style={{ display: 'contents' }}>
                  {bar(gN, 'oc-guard')}
                </span>
                <span className="oc-unavail" style={{ display: 'contents' }}>
                  {bar(uN, 'oc-unavail')}
                </span>
              </span>
              <span className="ocbar-n">{`${okN} ok · ${gN} guard · ${uN} unavail`}</span>
            </div>
          );
        })}
      <div className="oc-legend">
        <span className="grp">
          <span className="lg-title">result</span>
          <span className="lg-item">
            <i style={{ background: 'var(--rp-status-passed)' }} />
            ok
          </span>
        </span>
        <span className="grp">
          <span className="lg-title">guardrail fired (output rejected)</span>
          <span className="lg-item">
            <i style={{ background: 'var(--rp-topaz)' }} />
            schema_fail / validation_fail
          </span>
        </span>
        <span className="grp">
          <span className="lg-title">sidecar unavailable</span>
          <span className="lg-item">
            <i style={{ background: 'var(--rp-sm-warning)' }} />
            timeout / breaker_open / dropped
          </span>
        </span>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Card 3: latency
// --------------------------------------------------------------------------- //

function LatencyCard({ summary }: { summary: LlmSummaryResponse }) {
  const withLat = summary.roles.filter((r) => r.latency);
  const globalMax = Math.max(1, ...withLat.map((r) => r.latency?.max || 0));

  return (
    <Card title="Latency" sub="wall-clock per call, ok events only, cache hits excluded">
      {summary.roles.map((r) => {
        const lat = r.latency;
        if (!lat) {
          return (
            <div className="lat-row" key={r.role}>
              <span className="lat-role">{r.role}</span>
              <span className="lat-empty">—</span>
            </div>
          );
        }
        const p50pct = (100 * (lat.p50 || 0)) / globalMax;
        const p90pct = (100 * (lat.p90 || 0)) / globalMax;
        return (
          <div className="lat-row" key={r.role}>
            <span className="lat-role">{r.role}</span>
            <div>
              <div
                className="lat-track"
                title="median inference latency, ok calls only, cache hits excluded"
              >
                <i className="lat-fill" style={{ width: `${p50pct}%` }} />
                <b className="lat-tick" style={{ left: `${p90pct}%` }} />
              </div>
              <div className="lat-nums">
                p50 <b>{latFmt(lat.p50)}</b> · p90 <b>{latFmt(lat.p90)}</b> · max{' '}
                <b>{latFmt(lat.max)}</b>
                {` · n ${lat.n}`}
              </div>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Card 4: availability honesty (the breaker panel that refuses to lie)
// --------------------------------------------------------------------------- //

function AvailabilityCard({ summary }: { summary: LlmSummaryResponse }) {
  let breakerOpen = 0;
  let timeouts = 0;
  let lastEvent: string | null = null;
  for (const r of summary.roles) {
    breakerOpen += outcomes(r).breaker_open || 0;
    timeouts += outcomes(r).timeout || 0;
    if (r.last_event && (!lastEvent || r.last_event > lastEvent)) lastEvent = r.last_event;
  }

  return (
    <Card
      title="Availability"
      sub="breaker state is in-process on the analyzer — not persisted, not shown as fact"
    >
      <div className="honesty">
        <div className="h-tag">in-process state — not in the DB</div>
        <div className="note" style={{ marginTop: '4px' }}>
          Breaker state is in-process on the analyzer and not persisted; liveness here is inferred
          from event flow only. A quiet log means either healthy-and-idle or disabled — the Inspector
          cannot distinguish.
        </div>
      </div>
      <div className="stat-row">
        <div className="st">
          <div className="v">{lastEvent ? relTime(lastEvent) : '—'}</div>
          <div className="l">last llm_event</div>
        </div>
        <div className="st">
          <div className="v">{String(breakerOpen)}</div>
          <div className="l">breaker_open events</div>
        </div>
        <div className="st">
          <div className="v">{String(timeouts)}</div>
          <div className="l">transport timeouts</div>
        </div>
      </div>
      <EngDetails storageKey="llm-breaker" summary="Breaker limits (code contract, not runtime)">
        <p className="note">
          Opens after 3 consecutive transport failures; cooldown 60 s doubling to a 15 min cap; the
          first job after cooldown is the half-open probe. Schema / validation failures never trip
          it — the server was up, the output was bad. From breaker.py; reset on analyzer restart.
        </p>
      </EngDetails>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Card 5: activity stream
// --------------------------------------------------------------------------- //

type EventsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: LlmEventsResponse };

function ActivityCard() {
  const { project, refreshTick, link, patchLink } = useApp();
  const role = link.lrole || 'all';
  const outcome = link.loutcome || 'all';
  const [state, setState] = useState<EventsState>({ status: 'loading' });

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      try {
        const data = await api.llmEvents(project, role, outcome, 50);
        if (!cancelled) setState({ status: 'ready', data });
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String((e as Error)?.message || e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick, role, outcome]);

  return (
    <Card title="Activity stream" sub="newest-first, cap 50">
      <div className="filter-row">
        <span className="filter-lbl">role</span>
        {ROLE_CHIPS.map((r) => (
          <button
            type="button"
            key={r}
            className={`fchip${role === r ? ' on' : ''}`}
            onClick={() => patchLink({ lrole: r === 'all' ? null : r })}
          >
            {r}
          </button>
        ))}
        <span className="filter-lbl" style={{ marginLeft: '10px' }}>
          outcome
        </span>
        {OUTCOME_CHIPS.map((v) => (
          <button
            type="button"
            key={v}
            className={`fchip${outcome === v ? ' on' : ''}`}
            onClick={() => patchLink({ loutcome: v === 'all' ? null : v })}
          >
            {v}
          </button>
        ))}
      </div>
      <div>
        <ActivityList state={state} role={role} project={project} />
      </div>
    </Card>
  );
}

function ActivityList({
  state,
  role,
  project,
}: {
  state: EventsState;
  role: string;
  project: number | null;
}) {
  if (state.status === 'loading') return <Loading />;
  if (state.status === 'error') {
    return <EmptyState icon="🚫" title="Events failed" body={state.message} />;
  }
  const d = state.data;
  if (!d.events.length) {
    return (
      <EmptyState
        icon="🤖"
        title="No events for this filter"
        body={
          role === 'judge'
            ? 'No judge events. The judge fires only on suggest-route decisions in [0.45, 0.75) with ≥ 2 candidates — none occurred on this install.'
            : 'No llm_event rows match this role/outcome filter in this project.'
        }
      />
    );
  }
  return (
    <>
      <ol className="llm-tl">
        {d.events.map((e, i) => (
          <li key={`${e.created_at ?? ''}-${e.item_id ?? ''}-${i}`}>
            <span className={`oc-dot ${ocClass(e.outcome)}`} title={e.outcome ?? undefined} />
            <span className="tl-role">{e.role}</span>
            <span className="tl-mid">
              <ChipLink
                label={`item ${e.item_id}`}
                url={`#view=journey&project=${project}&launch=${e.launch_id}&item=${e.item_id}`}
                className="idchip"
              />
              <span className={`oc-chip ${ocClass(e.outcome)}`}>{e.outcome}</span>
              <span className="chip mono">{e.model || '—'}</span>
              {e.cache_hit ? <span className="chip">cache</span> : null}
              {e.output ? <OutputDrawer output={e.output} /> : null}
            </span>
            <span className="tl-right">
              {latFmt(e.latency_ms)} · {relTime(e.created_at)}
            </span>
          </li>
        ))}
      </ol>
      {d.count === d.limit ? (
        <div className="note" style={{ marginTop: '8px' }}>
          {`showing the latest ${d.limit} events (query cap).`}
        </div>
      ) : null}
    </>
  );
}

/** LLM output is untrusted model text — always a text child, never innerHTML. */
function OutputDrawer({ output }: { output: LlmEventOutput }): ReactNode {
  return (
    <details className="out-drawer">
      <summary>output</summary>
      <pre className="out-json">{JSON.stringify(output, null, 2)}</pre>
    </details>
  );
}

// --------------------------------------------------------------------------- //
// Card 6: extractor cache explorer
// --------------------------------------------------------------------------- //

type CacheState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: LlmCacheResponse };

function CacheCard() {
  const { project, refreshTick } = useApp();
  const [state, setState] = useState<CacheState>({ status: 'loading' });

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      try {
        const data = await api.llmCache(project, 'extractor', 50);
        if (!cancelled) setState({ status: 'ready', data });
      } catch (e) {
        if (!cancelled) setState({ status: 'error', message: String((e as Error)?.message || e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  return (
    <Card title="Extractor cache" sub="one model call per template-set per project, then reuse">
      <CacheBody state={state} />
    </Card>
  );
}

function CacheBody({ state }: { state: CacheState }) {
  if (state.status === 'loading') return <Loading />;
  if (state.status === 'error') {
    return <EmptyState icon="🚫" title="Cache failed" body={state.message} />;
  }
  const d = state.data;
  return (
    <>
      <p className="takeaway" style={{ fontSize: '13px' }}>
        <b>{`${d.entries} cached extractor outputs`}</b>
        {` · ${d.total_hits} hits`}
        <span className="muted">
          {
            ' — hits count role-call reuse only; feature-time reads are not counted, so real reuse is higher. Rows marked "no answer possible" hold a remembered failure, not an extraction, and are kept for 7 days.'
          }
        </span>
      </p>
      {!d.rows.length ? (
        <div className="note">No llm_cache rows for the extractor in this project.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {['template_hash', 'model', 'hits', 'fresh', 'created', 'last hit', 'output'].map(
                  (t) => (
                    <th key={t}>{t}</th>
                  ),
                )}
              </tr>
            </thead>
            {/*
              A negative row remembers that this input cannot be answered, so the
              analyzer stops paying for a call that fails the same way every time. It
              holds no extraction and carries no template hash on purpose, so say what
              it is rather than leaving a row of dashes the reader has to decode.
            */}
            <tbody>
              {d.rows.map((r, i) => (
                <tr key={`${r.template_hash ?? 'negative'}-${i}`} className={r.negative ? 'row-negative' : ''}>
                  <td className="mono">
                    {r.negative ? (
                      <span className="tag-negative">no answer possible</span>
                    ) : (
                      r.template_hash || '—'
                    )}
                  </td>
                  <td className="mono">{r.model || '—'}</td>
                  <td className="num">{String(r.hits)}</td>
                  <td className="mono">{r.fresh ? 'fresh' : 'expired'}</td>
                  <td className="mono">{relTime(r.created_at)}</td>
                  <td className="mono">{r.last_hit_at ? relTime(r.last_hit_at) : '—'}</td>
                  <td>{r.output ? <OutputDrawer output={r.output} /> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
