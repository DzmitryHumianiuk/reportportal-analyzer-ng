// LLM involvement strip — lives inside the Decision card.
//
// Role-accurate and item-scoped: it reports what the sidecar actually did for
// THIS item and nothing more. The dedup rules below are load-bearing — the
// strip must never restate a sentence the decision takeaway or the summary
// block already made, and it must never hide the model's own comment because a
// DIFFERENT row's explanation happens to be on screen.

import type { ReactNode } from 'react';

import { ChipLink, EngDetails } from '../../components';
import type { JourneyDecision, JourneyLlmEvent, JourneyResponse } from '../../app/types';
import { fmt, relTime } from '../../lib/format';
import { renderWithDefectNames } from '../../lib/labels';
import { sentence } from './sentence';

export function latFmt(ms?: number | null): string {
  if (ms == null) return '—';
  if (ms === 0) return 'cache';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export function ocClass(outcome?: string | null): string {
  if (outcome === 'ok') return 'oc-ok';
  if (outcome === 'schema_fail' || outcome === 'validation_fail') return 'oc-guard';
  return 'oc-unavail';
}

const UNAVAILABLE = ['timeout', 'breaker_open', 'dropped'];

function LlmTakeaway({
  dec,
  events,
  isColdstart,
  roles,
}: {
  dec: JourneyDecision;
  events: JourneyLlmEvent[];
  isColdstart: boolean;
  roles: string[];
}) {
  const exp = events.find((e) => e.role === 'explainer');
  const s = sentence();
  if (isColdstart) {
    s.b('LLM cold-start labeled this provisionally')
      .add(' — rubric over ')
      .mono(dec.model_ver || '')
      .add(', conf fixed ')
      .mono(fmt(dec.confidence, 2))
      .add(' ')
      .muted('(suggests, never auto-confirms)')
      .add('.');
  } else if (exp && exp.outcome === 'ok' && dec.explanation) {
    s.b('LLM explained this decision')
      .add(' — the rationale below is model output ')
      .muted(`(${exp.model}${exp.cache_hit ? ', cache hit' : `, ${latFmt(exp.latency_ms)}`})`)
      .add('; label and confidence are the analyzer’s, not the LLM’s.');
  } else if (exp && exp.outcome !== 'ok') {
    s.b('LLM explanation withheld')
      .add(' — output failed ')
      .mono(String(exp.outcome))
      .add(', so nothing was persisted ')
      .muted('(guardrail; the decision itself is unaffected)')
      .add('.');
  } else if (dec.judge) {
    s.b('LLM judge re-ranked the suggest-band candidates')
      .add(' — chose ')
      .mono(
        String(
          dec.judge.chosen_item_id != null ? `item ${dec.judge.chosen_item_id}` : dec.judge.choice,
        ),
      )
      .add('; label/confidence untouched by design.');
  } else if (roles.length === 1 && roles[0] === 'extractor') {
    s.b('LLM touched features only')
      .add(' — the extractor fed the ')
      .mono("'llm'")
      .add(' evidence group; it never saw the label path.');
  } else if (events.length && events.every((e) => UNAVAILABLE.includes(String(e.outcome)))) {
    s.b('LLM was asked but unavailable').add(
      ` — ${events.length}× ${events[0].outcome}; the decision proceeded without it.`,
    );
  } else {
    s.b('LLM sidecar activity recorded').add(` — ${events.length} event(s) for this item.`);
  }
  return s.render();
}

function ColdstartTile({
  dec,
  events,
  coldstartReasonShownAbove,
}: {
  dec: JourneyDecision;
  events: JourneyLlmEvent[];
  coldstartReasonShownAbove: boolean;
}) {
  const ev = events.find((e) => e.role === 'coldstart' && e.outcome === 'ok' && e.output);
  const isCurrent = !!dec.coldstart_provisional;
  const cs = dec.coldstart || {};
  const rule = cs.rule || (ev && ev.output && ev.output.rubric_rule_matched) || '—';
  const reason = ev && ev.output && ev.output.reason;
  return (
    <div className="llm-tile">
      <div className="role-line">
        <span className="role-key">coldstart</span>
        <span className="chip mono">{`rule ${rule}`}</span>
        {!isCurrent ? (
          <span
            className="chip"
            title="A later classical decision replaced this cold-start proposal. It is no longer the governing suggestion."
          >
            superseded
          </span>
        ) : null}
      </div>
      {reason && !(isCurrent && coldstartReasonShownAbove) ? (
        <div className="llm-quote">
          {renderWithDefectNames(reason)}
          <span className="attr">
            {isCurrent
              ? "AI proposal's own comment, cold-start"
              : "AI proposal's own comment, earlier cold-start (superseded)"}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function ExplainerTile({ dec, ev }: { dec: JourneyDecision; ev: JourneyLlmEvent }) {
  return (
    <div className="llm-tile">
      <div className="role-line">
        <span className="role-key">explainer</span>
        <span className="chip mono">{ev.model}</span>
      </div>
      <div
        className="llm-quote"
        title={`prompt_hash ${ev.prompt_hash || '—'}${ev.cache_hit ? ' · cache hit' : ''}`}
      >
        {renderWithDefectNames(dec.explanation || '')}
        <span className="attr">
          {`model’s rationale — ${ev.model} · ${latFmt(ev.latency_ms)} · ${relTime(ev.created_at)}`}
        </span>
      </div>
    </div>
  );
}

function JudgeTile({ dec }: { dec: JourneyDecision }) {
  const j = dec.judge || {};
  return (
    <div className="llm-tile">
      <div className="role-line">
        <span className="role-key">judge</span>
        <span className="chip">{`chose ${j.choice ?? '—'}`}</span>
      </div>
      <div className="chip-row">
        {j.chosen_item_id != null ? (
          <ChipLink label={`item ${j.chosen_item_id}`} url={null} className="chip mono" />
        ) : (
          <span className="chip">demoted all</span>
        )}
        <span className="muted" style={{ fontSize: '11px' }}>
          order only — label/conf untouched
        </span>
      </div>
    </div>
  );
}

function ExtractorTile({ ev }: { ev: JourneyLlmEvent }) {
  const o = ev.output || {};
  return (
    <div className="llm-tile">
      <div className="role-line">
        <span className="role-key">extractor</span>
        <span className="oc-chip oc-ok">{ev.cache_hit ? 'cache hit' : 'fresh call'}</span>
      </div>
      <div className="chip-row">
        {o.failing_layer ? <span className="chip">{`layer: ${o.failing_layer}`}</span> : null}
        {o.error_class ? <span className="chip">{`class: ${o.error_class}`}</span> : null}
        {o.root_exception ? <span className="chip mono">{o.root_exception}</span> : null}
      </div>
      <div className="note" style={{ marginTop: '6px' }}>
        feeds the <span className="mono">{"'llm'"}</span> evidence group above.
      </div>
    </div>
  );
}

function GuardNote({ ev }: { ev: JourneyLlmEvent }) {
  return (
    <div className="guard-note">
      <div className="g-tag">{`⛨ guardrail fired · ${ev.outcome}`}</div>
      The model’s output failed validation, so nothing was persisted — no hallucinated explanation
      ever reaches the row. Retried once (seed 7 → 8), then dropped.
    </div>
  );
}

function LlmEng({ dec, events }: { dec: JourneyDecision; events: JourneyLlmEvent[] }) {
  return (
    <EngDetails storageKey="llm">
      <dl className="kv">
        <dt>llm_used</dt>
        <dd className="mono">{String(dec.llm_used)}</dd>
        <dt>model_ver</dt>
        <dd className="mono">{dec.model_ver || '—'}</dd>
      </dl>
      {events.length ? (
        <div className="table-wrap" style={{ marginTop: '10px' }}>
          <table className="data">
            <thead>
              <tr>
                {['role', 'outcome', 'model', 'cache', 'latency', 'when'].map((t) => (
                  <th key={t}>{t}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={`${e.role}-${e.created_at}-${i}`}>
                  <td className="mono">{e.role}</td>
                  <td>
                    <span className={`oc-chip ${ocClass(e.outcome)}`}>{e.outcome}</span>
                  </td>
                  <td className="mono">{e.model || '—'}</td>
                  <td className="mono">{e.cache_hit ? 'cache' : '—'}</td>
                  <td className="mono">{latFmt(e.latency_ms)}</td>
                  <td className="mono">{relTime(e.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="note">No llm_event rows for this item.</p>
      )}
      <p className="note" style={{ marginTop: '8px' }}>
        append-only <span className="mono">analyzer.llm_event</span>, newest 20 for this item ·{' '}
        <a href="#view=llm" className="idlink">
          all events → LLM tab
        </a>
      </p>
    </EngDetails>
  );
}

export interface LlmStripOptions {
  explanationShownAbove: boolean;
  coldstartReasonShownAbove: boolean;
}

export function LlmStrip({
  d,
  dec,
  method,
  opts,
}: {
  d: JourneyResponse;
  dec: JourneyDecision;
  method: string;
  opts: LlmStripOptions;
}): JSX.Element | null {
  const llm = d.llm || {};
  const events = llm.events || []; // newest-first
  const modelVer = dec.model_ver || '';
  const isColdstart = method === 'llm_coldstart' || modelVer.startsWith('rubric+');
  // A cold-start proposal can exist in this item's llm_event history even when a
  // later classical decision superseded it as the governing suggestion row. Its
  // own comment is still real LLM output on this item and stays worth showing,
  // clearly marked as no longer in effect.
  const hasColdstartEvent = events.some(
    (e) => e.role === 'coldstart' && e.outcome === 'ok' && e.output && e.output.reason,
  );
  if (!events.length && !dec.llm_used && !isColdstart) return null;

  const roles = [...new Set(events.map((e) => String(e.role)))];
  const exp = events.find((e) => e.role === 'explainer');
  // Dedup rule: the strip sentence renders only when it carries facts the
  // decision takeaway/summary don't.
  const skipTakeaway =
    isColdstart || !!(exp && exp.outcome === 'ok' && dec.explanation && opts.explanationShownAbove);

  const tiles: ReactNode[] = [];
  if (isColdstart || hasColdstartEvent) {
    tiles.push(
      <ColdstartTile
        key="coldstart"
        dec={dec}
        events={events}
        coldstartReasonShownAbove={opts.coldstartReasonShownAbove}
      />,
    );
  }
  // Skip the quote tile when the summary block above already carries the text.
  if (exp && exp.outcome === 'ok' && dec.explanation && !opts.explanationShownAbove) {
    tiles.push(<ExplainerTile key="explainer" dec={dec} ev={exp} />);
  }
  if (dec.judge) tiles.push(<JudgeTile key="judge" dec={dec} />);
  const ext = events.find((e) => e.role === 'extractor' && e.outcome === 'ok' && e.output);
  if (ext) tiles.push(<ExtractorTile key="extractor" ev={ext} />);

  return (
    <div className="llm-strip">
      <div className="llm-strip-head">
        <div className="llm-strip-title">
          LLM sidecar <span className="k">{events.length ? `LLM · ${events.length}` : 'LLM · —'}</span>
        </div>
        <span className="note">async roles, decision-path-neutral</span>
      </div>
      {skipTakeaway ? null : (
        <LlmTakeaway dec={dec} events={events} isColdstart={isColdstart} roles={roles} />
      )}
      {tiles.length ? <div className="llm-tiles">{tiles}</div> : null}
      {exp && exp.outcome !== 'ok' ? <GuardNote ev={exp} /> : null}
      <LlmEng dec={dec} events={events} />
    </div>
  );
}
