// Stage 5 — Feedback: the append-only label_event log for this item.

import type { CSSProperties } from 'react';

import { Card, DefectBadge, EmptyState, EngDetails } from '../../components';
import type { FeedbackEvent, JourneyResponse } from '../../app/types';
import { relTime, shortTime } from '../../lib/format';
import { srcInfo, type SourceInfo } from '../../lib/labels';
import { scrollPulse } from './motion';
import { sentence } from './sentence';

function FeedbackGlance({ d, events }: { d: JourneyResponse; events: FeedbackEvent[] }) {
  const dec = d.decision;
  const matching = d.matching;
  const n = events.length;
  const last = events[0];
  const first = events[n - 1];
  const actor = (e: FeedbackEvent) => srcInfo(e.source).actor;
  const s = sentence();

  if (n === 1 && last.old_label == null) {
    s.add('Labeled once: ')
      .mono(String(last.new_label))
      .add(` set by ${actor(last)} — `)
      .mono(shortTime(last.ts))
      .add('.');
  } else if (n === 1 && last.old_label != null && last.old_label !== last.new_label) {
    s.add('Relabeled once: ')
      .mono(String(last.old_label))
      .add(' → ')
      .mono(String(last.new_label))
      .add(` by ${actor(last)} — `)
      .mono(shortTime(last.ts))
      .add('.');
  } else if (n === 1) {
    s.add('Label ')
      .mono(String(last.new_label))
      .add(` re-confirmed by ${actor(last)} — `)
      .mono(shortTime(last.ts))
      .add('.');
  } else {
    s.b(`Labeled ${n}×`).add(': ');
    const sugId = matching && matching.suggestion_id;
    if (
      last.suggestion_id != null &&
      dec &&
      sugId != null &&
      last.suggestion_id === sugId &&
      last.old_label === dec.predicted_label
    ) {
      s.add("analyzer's ").mono(String(dec.predicted_label)).add(' overridden — ');
    }
    s.add('latest ')
      .mono(String(last.old_label ?? '(none)'))
      .add(' → ')
      .mono(String(last.new_label))
      .add(` by ${actor(last)}`);
    if (last.old_label != null && last.old_label !== last.new_label) {
      s.add(' ').muted('(correction)');
    }
    s.add(' — ')
      .mono(shortTime(last.ts))
      .add('; first ')
      .mono(shortTime(first.ts))
      .add('.');
    if (n === 50) s.add(' Showing the last 50 events (query cap).');
  }
  return s.render();
}

function SrcChip({ si }: { si: SourceInfo }) {
  const title = `raw source: ${si.raw ?? 'not recorded'}${
    si.weight != null ? ` — src_weight ${si.weight}` : ''
  }`;
  return (
    <span className="method-chip" title={title}>
      <span className="k">{si.k}</span>
      {si.text}
    </span>
  );
}

/** suggestion_id chip; when it is the on-page decision's, it links to stage 4. */
function SuggestionChip({ e, d }: { e: FeedbackEvent; d: JourneyResponse }) {
  if (e.suggestion_id == null) return null;
  const onPage = d.matching && d.matching.suggestion_id;
  if (onPage != null && e.suggestion_id === onPage) {
    return (
      <button
        type="button"
        className="chip mono js-link"
        title={`Overrides suggestion ${e.suggestion_id} — the decision shown on stage 4. Click to locate.`}
        onClick={() => scrollPulse('j-decision')}
      >
        {`sug ${e.suggestion_id} ↑ stage 4`}
      </button>
    );
  }
  return (
    <span
      className="chip mono"
      title="The suggestion this event answered (an older decision — not the one shown on this page)."
    >
      {`sug ${e.suggestion_id}`}
    </span>
  );
}

/** Evidence weight now = src_weight × decay(age); half-life 90 days. */
function DecayLine({ e, si }: { e: FeedbackEvent; si: SourceInfo }) {
  if (si.weight == null || !e.ts) return null;
  const ageDays = Math.max(0, Math.floor((Date.now() - new Date(e.ts).getTime()) / 86400000));
  const decay = Math.exp((-Math.LN2 * ageDays) / 90);
  const eff = si.weight * decay;
  return (
    <div
      className="tl-decay"
      title="decay(d) = exp(−ln2 · d/90), half-life 90 d. Effective pull on future decisions = src_weight × decay(age)."
    >
      {`evidence weight now: src_weight ${si.weight} × decay ${decay.toFixed(2)} = ${eff.toFixed(2)}`}
      {si.weight >= 0.9 ? <span style={{ color: 'var(--good)' }}> · trains the model</span> : null}
    </div>
  );
}

export function FeedbackCard({ d }: { d: JourneyResponse }) {
  const events = d.feedback || []; // newest-first (payload ORDER BY ts DESC)

  if (!events.length) {
    return (
      <Card title="Feedback" step={5} sub="label_event log — append-only" id="j-feedback">
        <EmptyState
          icon="📈"
          title="No label events"
          body="No label events — never labeled or relabeled since ingest. Events append on RP defect updates (rp), UI accepts (human), auto-apply (ai_suggested), seed catalog (seed)."
          tag="analyzer.label_event"
        />
      </Card>
    );
  }

  const chrono = events.slice().reverse(); // oldest → newest

  return (
    <Card title="Feedback" step={5} sub="label_event log — append-only" id="j-feedback">
      <FeedbackGlance d={d} events={events} />

      <ol className="tl">
        {chrono.map((e, i) => {
          const isNewest = i === chrono.length - 1;
          const si = srcInfo(e.source);
          return (
            <li key={e.event_id} className={`tl-ev${isNewest ? ' now' : ''}`}>
              <i
                className="tl-dot"
                style={{ '--c': `var(--lbl-${e.new_group || 'none'})` } as CSSProperties}
                aria-hidden="true"
              />
              <div style={{ flex: '1' }}>
                <div className="tl-main">
                  {e.old_label ? (
                    <DefectBadge locator={e.old_label} group={e.old_group} />
                  ) : (
                    <span
                      className="muted"
                      title="First label event for this item — no prior label recorded."
                    >
                      (first label)
                    </span>
                  )}
                  <span
                    className="muted"
                    title="old_label → new_label as stored on the event; the log is append-only, current label = newest event."
                  >
                    →
                  </span>
                  <DefectBadge locator={e.new_label} group={e.new_group} />
                  <SrcChip si={si} />
                  <SuggestionChip e={e} d={d} />
                </div>
                <DecayLine e={e} si={si} />
              </div>
              <time dateTime={e.ts || undefined} className="mono" title={e.ts || ''}>
                {relTime(e.ts)}
              </time>
            </li>
          );
        })}
      </ol>

      {events.length === 50 ? (
        <div className="cap-line">showing the latest 50 events (query cap).</div>
      ) : null}

      <EngDetails storageKey="feedback">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {['event_id', 'old_label', 'new_label', 'source (raw)', 'suggestion_id', 'ts (ISO)'].map(
                  (t) => (
                    <th key={t}>{t}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id}>
                  <td className="mono">{String(e.event_id)}</td>
                  <td className="mono">{e.old_label ?? '—'}</td>
                  <td className="mono">{e.new_label ?? '—'}</td>
                  <td className="mono">{e.source ?? '—'}</td>
                  <td className="mono">{e.suggestion_id ?? '—'}</td>
                  <td className="mono">{e.ts ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note" style={{ marginTop: '8px' }}>
          append-only <span className="mono">analyzer.label_event</span>, newest-first query, cap 50.
        </p>
      </EngDetails>
    </Card>
  );
}
