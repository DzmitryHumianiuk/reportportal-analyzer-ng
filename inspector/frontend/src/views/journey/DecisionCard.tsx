// Stage 4 — Decision: what the analyzer decided, how sure it was, and why.

import { useState, type ReactNode } from 'react';

import { Card, EmptyState, EngDetails } from '../../components';
import type { DecisionFeature, JourneyDecision, JourneyResponse } from '../../app/types';
import { featureGroupColor } from '../../lib/colors';
import { fmt } from '../../lib/format';
import { defectName, renderWithDefectNames } from '../../lib/labels';
import { Gauge } from './Gauge';
import { LlmStrip, latFmt } from './LlmStrip';
import { Sentence, sentence } from './sentence';

const GROUP_NAME: Record<string, string> = {
  retrieval: 'similar past failures (retrieval)',
  history: 'this test’s track record (history)',
  kb: 'known failure modes (kb)',
  grouping: 'this launch’s failure pattern (grouping)',
  signal: 'log contents (signal)',
  llm: 'LLM extractor (llm)',
  discriminant: 'exact-detail agreement (discriminant)',
  other: 'uncatalogued (other)',
};

const METHOD_PLAIN: Record<string, string> = {
  hash: 'exact match',
  kb: 'known failure mode',
  gbm: 'learned model',
  rule_cold: 'starter rules',
  coldstart: 'LLM cold-start rubric',
};

const METHOD_TIP: Record<string, string> = {
  hash: 'An identical failure (same error ID, error_hash) was seen before and labeled by a human. That label is inherited. Extra guards: recent (≤ 180 days), trusted (confidence ≥ 0.9), and the two failures still agree on unmasked details such as status codes and identifiers (discriminant gate). Confidence is fixed at 0.95 (Stage A).',
  kb: 'This failure matches an entry in the catalog of known, confirmed failure modes (knowledge base): match score ≥ 0.85, catalog entry ≥ 95 % label-pure with ≥ 10 confirmed members. Confidence is capped at 0.93.',
  gbm: 'A trained model (gradient-boosted trees, LightGBM) weighed all evidence signals — similar past failures, this test’s track record, the launch failure pattern, log contents — and produced a calibrated probability for each label.',
  rule_cold: 'No trained model is available yet (cold start). Simple safety rules decide: exact match, then catalog, then a pre-configured seed rule for this failure kind (needs confidence ≥ 0.7); otherwise the analyzer abstains.',
  coldstart: 'Zero-label project: the LLM cold-start rubric proposed a PROVISIONAL ai_suggested label. It is never auto-applied and never surfaced in RP’s Make Decision — the classical decision path runs separately.',
};

const BAND_TIP: Record<string, string> = {
  auto: 'Confidence is at or above 0.75 (tau_auto). The label was applied without waiting for a human. A human can still correct it later.',
  suggest: 'Confidence is between 0.45 (tau_suggest) and 0.75 (tau_auto). The label is shown as a suggestion; a human confirms or corrects it.',
  abstain: 'Confidence is below 0.45 (tau_suggest), or a safety guard fired. The analyzer says “I do not know” instead of guessing. The item goes to To Investigate.',
};

const OUTCOME_TIP: Record<string, string> = {
  accepted: 'A person reviewed the suggestion and kept it.',
  corrected: 'A person reviewed the suggestion and picked a different label. This correction feeds future training.',
  ignored: 'Nobody acted on the suggestion before it expired or the item moved on.',
  pending: 'The suggestion is still open; no person has acted on it yet.',
};

// L1 decision takeaway — first matching (band, method) variant wins.
function decisionTakeaway(
  dec: JourneyDecision,
  method: string,
  label: string,
  matchedItemId?: number | null,
  modeTitle?: string | null,
): Sentence {
  const s = sentence();
  const C = fmt(dec.confidence, 2);
  const TA = fmt(dec.tau_auto, 2);
  const TS = fmt(dec.tau_suggest, 2);
  const mode = modeTitle ? `“${modeTitle}”` : 'in the catalog';

  if (dec.coldstart_provisional) {
    // Rubric rows are provisional ai_suggested hints — never a served suggest.
    const modelBase = String(dec.model_ver || '').split(';')[0];
    s.b('LLM cold-start provisional: ')
      .b(label)
      .add(`@${C} `)
      .mono(modelBase)
      .add(' ')
      .muted('(ai_suggested — not shown in RP Make Decision)');
    const cl = dec.classical;
    if (cl) {
      const cLabel = defectName(cl.predicted_label, cl.predicted_group);
      const cC = fmt(cl.confidence, 2);
      s.add('; the classical decision ')
        .add(
          cl.band === 'abstain'
            ? `abstained (${cl.predicted_label === 'ti' ? 'ti' : cLabel}@${cC})`
            : `${cl.band === 'auto' ? 'auto-applied' : 'suggests'} ${cLabel}@${cC}`,
        )
        .add('.');
    } else {
      s.add('; no classical decision row exists for this item.');
    }
    return s;
  }

  if (dec.band === 'auto') {
    if (method === 'hash' && matchedItemId) {
      s.b(label)
        .add(', auto-applied — inherited from human-labeled identical failure ')
        .mono(`#${matchedItemId}`)
        .add(' ')
        .muted(`(exact hash match; ${C} ≥ τ_auto ${TA})`);
    } else if (method === 'kb') {
      s.b(label)
        .add(`, auto-applied — matches known failure mode ${mode} `)
        .muted(`(kb; ${C} ≥ τ_auto ${TA})`);
    } else if (method === 'rule_cold') {
      s.b(label)
        .add(', auto-applied by starter rules — no trained model yet ')
        .muted(`(rule_cold; ${C} ≥ τ_auto ${TA})`);
    } else {
      s.b(label)
        .add(', auto-applied — the learned model was confident enough to act ')
        .muted(`(gbm; ${C} ≥ τ_auto ${TA})`);
    }
  } else if (dec.band === 'suggest') {
    s.b(`Suggests ${label}`).add(' — ');
    if (method === 'hash' && matchedItemId) {
      s.add('resembles labeled failure ').mono(`#${matchedItemId}`).add(', a human confirms ');
    } else if (method === 'kb') {
      s.add(`matches known failure mode ${mode}, a human confirms `);
    } else if (method === 'rule_cold') {
      s.add('a starter rule points here, a human confirms ');
    } else {
      s.add('the learned model leans this way, a human confirms ');
    }
    s.muted(`(${method}; ${TS} ≤ ${C} < τ_auto ${TA})`);
  } else {
    s.b('Abstained → To Investigate').add(' — ');
    if (method === 'hash' && matchedItemId) {
      s.add('an exact-match candidate ').mono(`#${matchedItemId}`).add(' fell below the suggest bar ');
    } else if (method === 'kb') {
      s.add('a catalog match fell below the suggest bar ');
    } else if (method === 'rule_cold') {
      s.add('no confident rule and no trained model yet ');
    } else {
      s.add('confidence below the suggest bar ');
    }
    s.muted(`(${method}; ${method === 'gbm' ? 'p* ' : ''}${C} < τ_suggest ${TS})`);
  }
  return s;
}

function MethodChip({ method }: { method: string }) {
  return (
    <span className="method-chip" title={METHOD_TIP[method] || ''}>
      <span className="k">{method}</span>
      {METHOD_PLAIN[method] || method}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome?: string | null }) {
  const map: Record<string, string> = {
    accepted: 'var(--good)',
    corrected: 'var(--warning)',
    ignored: 'var(--muted)',
    pending: 'var(--accent)',
  };
  const col = map[outcome || ''] || 'var(--muted)';
  const text = outcome === 'pending' ? 'pending review' : outcome;
  return (
    <span
      className="badge"
      title={OUTCOME_TIP[outcome || ''] || undefined}
      style={{ background: `color-mix(in srgb, ${col} 16%, transparent)`, color: col }}
    >
      {text}
    </span>
  );
}

function BandLegend({ dec }: { dec: JourneyDecision }) {
  const TS = fmt(dec.tau_suggest, 2);
  const TA = fmt(dec.tau_auto, 2);
  const item = (band: string, text: string, active: boolean) => (
    <span
      key={band}
      className="bl-item"
      role="listitem"
      title={BAND_TIP[band]}
      {...(active ? { 'data-active': '' } : {})}
    >
      <i className="dot" style={{ background: `var(--band-${band})` }} />
      {text}
    </span>
  );
  return (
    <div className="band-legend" role="list">
      {item('abstain', `abstain · < ${TS} → TI`, dec.band === 'abstain')}
      {item('suggest', `suggest · ${TS}–${TA}`, dec.band === 'suggest')}
      {item('auto', `auto · ≥ ${TA}`, dec.band === 'auto')}
    </div>
  );
}

function abstainReasonText(dec: JourneyDecision, label: string): string {
  const C = fmt(dec.confidence, 2);
  const TS = fmt(dec.tau_suggest, 2);
  switch (dec.abstain_reason) {
    case 'no_confident_rule':
      return 'No rule was confident enough: no identical labeled failure (hash), no catalog match (kb), and no trusted seed rule. With no trained model yet, the honest answer is “investigate”.';
    case 'gbm_below_suggest':
      return dec.predicted_group === 'ti'
        ? `The model scored every label below the suggestion bar of ${TS} (tau_suggest).`
        : `The model scored every label below the suggestion bar of ${TS} (tau_suggest). Best guess was ${label} at ${C}, which is too weak to show.`;
    case 'gbm_boilerplate_only_neighbor':
      return 'The model’s only support was a look-alike failure that shares no concrete evidence with this one — no matching error ID, no shared log templates, no shared identifiers, only generic error text. That is not real support, so the suggestion was withdrawn.';
    default:
      return `The analyzer abstained for reason “${dec.abstain_reason}” (code not yet documented in the Inspector).`;
  }
}

// ---- Evidence groups (feature vector → group rows, per-feature bars) ----

interface GroupModel {
  group: string;
  feats: DecisionFeature[];
  sum: number;
  top: DecisionFeature;
  singleMax: number;
}

function EviRow({ m, maxSum, open }: { m: GroupModel; maxSum: number; open: boolean }) {
  const [expanded, setExpanded] = useState(open);
  const col = featureGroupColor(m.group);
  const feats = m.feats.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const gmax = Math.max(...feats.map((f) => Math.abs(f.value)), 1e-9);
  return (
    <div className="evi-row" data-group={m.group}>
      <button
        type="button"
        className="evi-head"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <i className="dot" style={{ background: col }} />
        <span className="evi-name">{GROUP_NAME[m.group] || m.group}</span>
        <span className="chip">{String(m.feats.length)}</span>
        <span className="evi-top">{`top: ${m.top.label} ${fmt(m.top.value, 2)}`}</span>
        <span className="evi-bar-cell">
          <span className="microbar" style={{ display: 'block' }}>
            <i style={{ width: `${((m.sum / maxSum) * 100).toFixed(0)}%`, background: col }} />
          </span>
        </span>
        <span className="evi-sum mono">{`Σ|v| ${fmt(m.sum, 2)}`}</span>
        <span className="evi-caret">▸</span>
      </button>
      <div className="evi-body" hidden={!expanded}>
        {feats.map((f) => (
          <div
            key={f.key}
            className="feat-row"
            title={`${f.key} = ${fmt(f.value, 4)} · ${f.definition} · range ${f.range} · default ${f.default}`}
          >
            <span>
              <span className="feat-lbl">{f.label}</span>
              <br />
              <span className="feat-key">{f.key}</span>
            </span>
            <span className="feat-bar">
              <i style={{ width: `${(Math.abs(f.value) / gmax) * 100}%`, background: col }} />
            </span>
            <span className="feat-val">{fmt(f.value, 2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function EvidenceGroups({ features }: { features?: DecisionFeature[] | null }) {
  if (!features || !features.length) {
    return (
      <div>
        <p className="note">No stored feature vector for this suggestion.</p>
      </div>
    );
  }
  const byGroup = new Map<string, DecisionFeature[]>();
  for (const f of features) {
    const g = f.group || 'other';
    if (!byGroup.has(g)) byGroup.set(g, []);
    (byGroup.get(g) as DecisionFeature[]).push(f);
  }
  const models: GroupModel[] = [...byGroup.entries()].map(([group, feats]) => {
    const sum = feats.reduce((acc, f) => acc + Math.abs(f.value), 0);
    const top = feats.reduce((mx, f) => (Math.abs(f.value) > Math.abs(mx.value) ? f : mx), feats[0]);
    const singleMax = Math.max(...feats.map((f) => Math.abs(f.value)));
    return { group, feats, sum, top, singleMax };
  });
  models.sort((a, b) => b.sum - a.sum); // "largest first" = Σ|v| desc
  const maxSum = Math.max(...models.map((m) => m.sum), 1e-9);
  // auto-expand the group holding the single largest |value| feature
  const autoGroup = models.reduce((a, b) => (b.singleMax > a.singleMax ? b : a), models[0]).group;
  return (
    <div>
      {models.map((m) => (
        <EviRow key={m.group} m={m} maxSum={maxSum} open={m.group === autoGroup} />
      ))}
    </div>
  );
}

// Numeric snapshot keys outside the feature registry — real data, surfaced in
// Technical Details instead of inflating the waterfall's "N of M" counts.
function extraSnapshotRows(
  extras: Record<string, number> | null | undefined,
  title: string,
): ReactNode {
  if (!extras || !Object.keys(extras).length) return null;
  return (
    <>
      <dt>{title}</dt>
      <dd className="mono" style={{ fontSize: '11px' }}>
        {JSON.stringify(extras)}
      </dd>
    </>
  );
}

// Provenance of a stored explanation: llm explainer / rubric / deterministic.
// Tolerant of unknown future markers — falls back to 'deterministic' wording.
interface Provenance {
  kind: 'rubric' | 'llm' | 'deterministic';
  chip: string;
  validated: boolean;
  ev: { model?: string | null; cache_hit?: boolean | null; latency_ms?: number | null } | null;
}

function explanationProvenance(d: JourneyResponse, row: JourneyDecision): Provenance {
  const events = (d.llm && d.llm.events) || [];
  const exp = events.find((e) => e.role === 'explainer');
  const modelVer = String(row.model_ver || '');
  if (modelVer.startsWith('rubric+')) {
    return { kind: 'rubric', chip: modelVer.split(';')[0], validated: false, ev: null };
  }
  if (row.llm_used && exp && exp.outcome === 'ok') {
    return { kind: 'llm', chip: exp.model || 'llm', validated: true, ev: exp };
  }
  return { kind: 'deterministic', chip: 'deterministic', validated: false, ev: null };
}

// Prominent decision-summary block: the stored explanation text (untrusted →
// text nodes only) + provenance chips. Never generated client-side.
function DecisionSummary({
  d,
  dec,
  row,
}: {
  d: JourneyResponse;
  dec: JourneyDecision;
  row: JourneyDecision;
}) {
  const prov = explanationProvenance(d, row);
  return (
    <div className="llm-quote" style={{ margin: '0 0 14px' }}>
      <div className="flex gap-8 wrap center" style={{ marginBottom: '6px' }}>
        <span className="section-title" style={{ margin: 0 }}>
          {row === dec ? 'decision summary' : 'decision summary — classical row'}
        </span>
        <span
          className="chip mono"
          title={
            prov.kind === 'llm'
              ? 'Model output — the label and confidence are the analyzer’s, not the LLM’s.'
              : prov.kind === 'rubric'
                ? 'Cold-start rubric output (provisional, ai_suggested).'
                : 'Deterministic analyzer narrative.'
          }
        >
          {prov.chip}
        </span>
        {prov.ev && !prov.ev.cache_hit && prov.ev.latency_ms != null ? (
          <span className="chip">{latFmt(prov.ev.latency_ms)}</span>
        ) : null}
        {prov.ev && prov.ev.cache_hit ? <span className="chip">cache hit</span> : null}
        {prov.validated ? (
          <span
            className="chip"
            title="LLM explainer outputs pass a grounding guard: verbatim quotes are substring-validated against the real logs before anything is persisted."
          >
            grounded quotes validated
          </span>
        ) : null}
      </div>
      <div>{renderWithDefectNames(row.explanation || '')}</div>
    </div>
  );
}

export function DecisionCard({ d }: { d: JourneyResponse }) {
  const dec = d.decision;
  if (!dec) {
    return (
      <Card
        title="Decision"
        step={4}
        sub="what the analyzer decided, how sure it was, and why (features & policy)"
        id="j-decision"
      >
        <EmptyState
          icon="🎯"
          title="No decision recorded"
          body="No decision recorded — the analyzer has not scored this item yet (no suggestion row)."
          tag="analyzer.suggestion"
        />
      </Card>
    );
  }

  const method = dec.method || 'gbm';
  const label = defectName(dec.predicted_label, dec.predicted_group);
  const matchedItemId = d.matching && d.matching.matched_item_id;
  const matchedMode = d.matching && d.matching.matched_mode;
  const modeTitle = matchedMode && matchedMode.title;

  const takeaway = decisionTakeaway(dec, method, label, matchedItemId, modeTitle);

  // Decision summary — the stored explanation of the DISPLAYED decision row
  // (primary; classical on provisional items). Honest absence: no block when no
  // explanation is stored. Dedup guard: show it only when it adds words the
  // takeaway doesn't.
  const clForSummary = dec.coldstart_provisional && dec.classical ? dec.classical : null;
  let summarySrc: JourneyDecision | null =
    clForSummary && clForSummary.explanation ? clForSummary : dec.explanation ? dec : null;
  if (summarySrc) {
    const ex = String(summarySrc.explanation).trim();
    if (!ex || takeaway.text.includes(ex)) summarySrc = null;
  }
  // Track specifically whether the block above is showing the coldstart row's
  // OWN text (summarySrc === dec), not a different row's explanation. Those are
  // two different pieces of LLM output; one being shown must never suppress the
  // other.
  const coldstartReasonShownAbove = !!(dec.coldstart_provisional && summarySrc === dec);

  const llm = (
    <LlmStrip
      d={d}
      dec={dec}
      method={method}
      opts={{ explanationShownAbove: !!summarySrc, coldstartReasonShownAbove }}
    />
  );

  // On cold-start provisional items the rubric row carries no feature vector —
  // the gauge and evidence waterfall tell the CLASSICAL decision's story
  // instead, clearly captioned as such.
  const cl =
    dec.coldstart_provisional && dec.classical && dec.classical.features ? dec.classical : null;
  const evDec: JourneyDecision = cl
    ? { ...cl, tau_suggest: dec.tau_suggest, tau_auto: dec.tau_auto }
    : dec;
  const marker = dec.coldstart_provisional
    ? { value: dec.confidence, label: 'LLM proposal' }
    : null;

  return (
    <Card
      title="Decision"
      step={4}
      sub="what the analyzer decided, how sure it was, and why (features & policy)"
      id="j-decision"
    >
      {takeaway.render()}

      {/* Verdict chip row — dedup rule: chips carry ONLY what the takeaway
          sentence doesn't already say (method key + outcome). */}
      <div className="flex gap-8 wrap center" style={{ marginBottom: '16px' }}>
        <MethodChip method={method} />
        <OutcomeBadge outcome={dec.outcome} />
      </div>

      {summarySrc ? <DecisionSummary d={d} dec={dec} row={summarySrc} /> : null}
      {llm}

      <div className="flex gap-12 wrap center" style={{ marginBottom: '6px' }}>
        <Gauge
          confidence={evDec.confidence}
          tauSuggest={evDec.tau_suggest}
          tauAuto={evDec.tau_auto}
          marker={marker}
        />
        <div style={{ flex: '1 1 220px' }}>
          <BandLegend dec={evDec} />
        </div>
      </div>
      {marker ? (
        <p className="note" style={{ margin: '0 0 10px' }}>
          {`needle = classical decision (in effect) · dashed tick = LLM proposed ${label} @ ${fmt(
            dec.confidence,
            2,
          )} (provisional — not applied)`}
        </p>
      ) : null}
      {dec.judge ? (
        <p className="note" style={{ margin: '0 0 10px' }}>
          LLM judge re-ranked the suggest candidates (order only — label and confidence untouched).
        </p>
      ) : null}

      {dec.band === 'abstain' && dec.abstain_reason ? (
        <div className="abstain-reason">
          {abstainReasonText(dec, label)}
          <div style={{ marginTop: '6px' }}>
            <span className="code">{dec.abstain_reason}</span>
          </div>
        </div>
      ) : null}

      <div className="section-title" style={{ marginTop: '4px' }}>
        {`Evidence the ${cl ? 'classical ' : ''}decision weighed (${evDec.feature_count} of ${
          evDec.feature_total
        } signals, largest first) (feature vector)`}
      </div>
      <EvidenceGroups features={evDec.features} />

      <EngDetails storageKey="decision">
        <dl className="kv">
          {cl ? (
            <>
              <dt>rubric row</dt>
              <dd className="mono">no feature vector — rubric path</dd>
              <dt>classical model_ver</dt>
              <dd className="mono" style={{ fontSize: '12px' }}>
                {cl.model_ver}
              </dd>
            </>
          ) : null}
          <dt>model_ver</dt>
          <dd className="mono" style={{ fontSize: '12px' }}>
            {dec.model_ver}
          </dd>
          <dt>method</dt>
          <dd className="mono">{method}</dd>
          {dec.abstain_reason ? (
            <>
              <dt>abstain_reason</dt>
              <dd className="mono">{dec.abstain_reason}</dd>
            </>
          ) : null}
          {matchedItemId ? (
            <>
              <dt>matched_item_id</dt>
              <dd className="mono">{String(matchedItemId)}</dd>
            </>
          ) : null}
          {d.matching && d.matching.matched_mode_id ? (
            <>
              <dt>matched_mode_id</dt>
              <dd className="mono">{String(d.matching.matched_mode_id)}</dd>
            </>
          ) : null}
          <dt>llm_used</dt>
          <dd className="mono">{String(dec.llm_used)}</dd>
          <dt>outcome</dt>
          <dd className="mono">{dec.outcome}</dd>
          {extraSnapshotRows(dec.extra_snapshot, 'snapshot extras')}
          {cl ? extraSnapshotRows(cl.extra_snapshot, 'classical snapshot extras') : null}
        </dl>
      </EngDetails>
    </Card>
  );
}
