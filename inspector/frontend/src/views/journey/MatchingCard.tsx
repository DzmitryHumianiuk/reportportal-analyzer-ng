// Stage 3 — Matching: the A → B → C cascade plus the live Stage-C reconstruction.

import type { CSSProperties, ReactNode } from 'react';

import { Card, ChipLink, DefectBadge, EmptyState, EngDetails, Microbar } from '../../components';
import type { JourneyResponse, RetrievalCandidate } from '../../app/types';
import { fmt } from '../../lib/format';
import { labelGroup, srcInfo } from '../../lib/labels';
import { sentence } from './sentence';

const STAGE_COLOR: Record<string, string> = {
  A: 'var(--lbl-nd)',
  AB: 'var(--lbl-si)',
  C: 'var(--accent)',
};

type NodeState = 'won' | 'fell' | 'skip';

function MatchTakeaway({ d }: { d: JourneyResponse }) {
  const m = d.matching || {};
  const mm = m.matched_mode;
  const s = sentence();
  if (m.stage === 'A') {
    s.b('Inherited via exact hash')
      .add(` from item ${m.matched_item_id} — same `)
      .mono('error_hash')
      .add(', guards passed (≤ 180 d, human-trusted); conf fixed 0.95 ')
      .muted('(Stage A)')
      .add('.');
  } else if (m.stage === 'AB' && mm) {
    s.b('Matched KB mode')
      .add(
        ` “${mm.title || `mode ${m.matched_mode_id}`}” (#${m.matched_mode_id}) — purity ${fmt(
          mm.purity,
          2,
        )}, support ${mm.support}`,
      )
      .add(mm.seed_key ? `, seed:${mm.seed_key}` : '')
      .add(' ')
      .muted('(Stage B; conf cap 0.93)')
      .add('.');
  } else if (m.stage === 'AB') {
    s.b('Matched KB mode')
      .add(
        ` #${m.matched_mode_id} — mode row not found in failure_mode (retired?); score details unavailable `,
      )
      .muted('(Stage B)')
      .add('.');
  } else if (m.stage === 'abstain') {
    s.b('Nothing matched')
      .add(' — no inheritable hash (A), no confident mode (B), no trusted candidate (C); item stays ')
      .mono('ti')
      .add('. Reason on the Decision card.');
  } else {
    s.b('No exact hash, no KB short-circuit')
      .add(' — went to hybrid retrieval: FTS + cosine fused by ')
      .mono('RRF')
      .add(', top-20 → GBM scored the evidence ')
      .muted('(Stage C)')
      .add('.');
  }
  return s.render();
}

function fnCaption(state: NodeState, letter: string): string {
  if (state === 'won') return 'decided here';
  if (state === 'skip') return 'not reached';
  return letter === 'B' ? 'passed — scored, no short-circuit' : 'passed — no decision';
}

function FnNode({
  kText,
  state,
  cap,
  letter,
  art,
}: {
  kText: string;
  state: NodeState;
  cap: string;
  letter: string;
  art?: ReactNode;
}) {
  const style =
    state === 'won'
      ? ({ '--stage-c': STAGE_COLOR[letter] || 'var(--accent)' } as CSSProperties)
      : undefined;
  return (
    <div className="fn-node" role="listitem" data-state={state} style={style}>
      <div className="fn-k">{kText}</div>
      <div className="fn-cap">{cap}</div>
      {art ? <div className="fn-art">{art}</div> : null}
    </div>
  );
}

/** True when a non-self live candidate carries this item's exact error_hash. */
function sameHashHistory(d: JourneyResponse): boolean {
  const eh = d.signature && d.signature.error_hash;
  if (!eh || !d.reconstruction) return false;
  return (d.reconstruction.candidates || []).some((c) => !c.is_self && c.error_hash === eh);
}

const Arrow = () => (
  <span className="fn-arrow" aria-hidden="true">
    →
  </span>
);

// Three-stage funnel derived from matching.stage (A runs first, then B, then C).
function Funnel({ d }: { d: JourneyResponse }) {
  const m = d.matching || {};
  const sig = d.signature;
  const stage = m.stage;
  const states: Record<'A' | 'B' | 'C', NodeState> = {
    A: stage === 'A' ? 'won' : 'fell',
    B: stage === 'AB' ? 'won' : stage === 'A' ? 'skip' : 'fell',
    C: stage === 'C' ? 'won' : stage === 'A' || stage === 'AB' ? 'skip' : 'fell',
  };

  let aArt: ReactNode = null;
  if (states.A === 'won') {
    aArt = <ChipLink label={`item ${m.matched_item_id}`} url={m.matched_item_url} className="chip mono" />;
  } else if (states.A === 'fell') {
    if (sig && sig.exception_fp === '0') {
      aArt = null; // handled by the caption below
    } else if (sameHashHistory(d)) {
      aArt = (
        <span
          className="chip"
          title="An identical error_hash is in labeled history but the Stage-A guards did not inherit it."
        >
          same-hash history exists
        </span>
      );
    } else {
      aArt = (
        <span
          className="chip mono"
          title="No non-self candidate shares this item's error_hash — no inheritable exact match exists."
        >
          no same-hash history
        </span>
      );
    }
  }
  const aCap =
    states.A === 'fell' && sig && sig.exception_fp === '0'
      ? 'disabled — exception_fp = 0'
      : fnCaption(states.A, 'A');

  let bArt: ReactNode = null;
  if (states.B === 'won' && m.matched_mode) {
    bArt = (
      <>
        <DefectBadge locator={m.matched_mode.label} group={m.matched_mode.label_group} />
        <span className="chip">{m.matched_mode.title || `mode ${m.matched_mode_id}`}</span>
      </>
    );
  } else if (states.B === 'won') {
    bArt = <span className="chip mono">{`mode #${m.matched_mode_id}`}</span>;
  }

  let cArt: ReactNode = null;
  if (states.C === 'won') {
    const nonSelf = ((d.reconstruction && d.reconstruction.candidates) || []).filter(
      (x) => !x.is_self,
    ).length;
    cArt = (
      <span
        className="chip mono"
        title="Top-1 candidate fed the 46-feature vector → GBM. The GBM confidence lives on the Decision card."
      >
        {`top-1 of ${nonSelf} → GBM`}
      </span>
    );
  }

  return (
    <div className="funnel" role="list">
      <FnNode kText="A · exact hash" state={states.A} cap={aCap} letter="A" art={aArt} />
      <Arrow />
      <FnNode kText="B · KB modes" state={states.B} cap={fnCaption(states.B, 'B')} letter="B" art={bArt} />
      <Arrow />
      <FnNode
        kText="C · hybrid + GBM"
        state={states.C}
        cap={fnCaption(states.C, 'C')}
        letter="C"
        art={cArt}
      />
      <Arrow />
      {stage === 'abstain' ? (
        <span className="fn-term" style={{ color: 'var(--lbl-ti)' }}>
          abstain → To Investigate
        </span>
      ) : (
        <span className="fn-term">{`decided at ${stage === 'AB' ? 'B' : stage}`}</span>
      )}
    </div>
  );
}

function decisionFeatureValue(d: JourneyResponse, key: string): number | null {
  if (!d.decision || !d.decision.features) return null;
  const f = d.decision.features.find((x) => x.key === key);
  return f ? f.value : null;
}

// Top-3 non-self candidate evidence strip.
function CandStrip({ d }: { d: JourneyResponse }) {
  const r = d.reconstruction;
  const sig = d.signature || {};
  const all = (r && r.candidates) || [];
  const cands = all.filter((c) => !c.is_self).slice(0, 3);
  const stored = decisionFeatureValue(d, 'top1_cosine');
  const liveTop = cands.find((c) => c.cosine != null);
  const drifted =
    stored != null && liveTop && liveTop.cosine != null && Math.abs(stored - liveTop.cosine) > 0.005;

  return (
    <div>
      <div className="section-title" style={{ marginTop: '16px' }}>
        {`Closest labeled history — top ${cands.length} of ${all.length} retrieved (live)`}
      </div>
      <div className="cand-strip">
        {cands.map((cd, i) => (
          <CandCard key={cd.item_id} cd={cd} rank={i + 1} sigFp={sig.exception_fp} sigHash={sig.error_hash} />
        ))}
      </div>
      {drifted && liveTop ? (
        <p className="note" style={{ marginTop: '8px' }}>
          {`stored top1_cosine at decision time ${fmt(stored, 3)} vs ${fmt(
            liveTop.cosine,
            3,
          )} now — retrieval has drifted since the decision.`}
        </p>
      ) : null}
    </div>
  );
}

function CandCard({
  cd,
  rank,
  sigFp,
  sigHash,
}: {
  cd: RetrievalCandidate;
  rank: number;
  sigFp?: string | null;
  sigHash?: string | null;
}) {
  const si = srcInfo(cd.label_source);
  return (
    <div className="cand-card">
      <div className="cand-head">
        <span className="cand-rank">{`#${rank}`}</span>
        <ChipLink label={`item ${cd.item_id}`} url={cd.ui_url} className="chip mono" />
        <DefectBadge locator={cd.issue_type} group={labelGroup(cd.issue_type)} />
      </div>
      <div className="cand-cos">
        <span className="cc-k">cos</span>
        <span className="cc-bar">
          <Microbar ratio={cd.cosine ?? 0} />
        </span>
        <span
          className="cc-v"
          title={cd.cosine == null ? 'no dense score — dense leg inactive for this pair' : undefined}
        >
          {cd.cosine == null ? '—' : fmt(cd.cosine, 3)}
        </span>
      </div>
      <div className="cand-chips">
        {cd.exception_fp != null && sigFp != null && cd.exception_fp === sigFp ? (
          <span className="chip mono" title={`same exception_fp ${cd.exception_fp}`}>
            fp =
          </span>
        ) : null}
        {cd.error_hash != null && sigHash != null && cd.error_hash === sigHash ? (
          <span className="chip mono" title="same error_hash — Stage-A grade match">
            hash =
          </span>
        ) : null}
        <span className="chip mono" title="template-set Jaccard vs this item">
          {`jac ${fmt(cd.jaccard_templates, 2)}`}
        </span>
        <span
          className="chip"
          title={`label_source: ${si.raw ?? 'not recorded'}${
            si.weight != null ? ` — src_weight ${si.weight}` : ''
          }`}
        >
          {`src ${si.plain}`}
        </span>
        {cd.launch_number != null ? (
          <span className="chip mono">{`launch #${cd.launch_number}`}</span>
        ) : null}
        {cd.mode_id != null ? <span className="chip mono">{`mode ${cd.mode_id}`}</span> : null}
      </div>
    </div>
  );
}

// Full reconstruction table behind an expander, with the RRF explainer.
function RrfDrawer({ d }: { d: JourneyResponse }) {
  const r = d.reconstruction;
  const candidates = (r && r.candidates) || [];
  const maxRrf = Math.max(...candidates.map((x) => x.rrf_score || 0), 1e-9);
  return (
    <EngDetails
      storageKey="matching-recon"
      summary={`Live Stage-C reconstruction — ${candidates.length} candidates · HYBRID_RETRIEVAL v${
        r ? r.hybrid_retrieval_version : '?'
      }`}
    >
      <p className="note" style={{ marginBottom: '8px' }}>
        <b>RRF</b> fuses the two rank lists — lexical (weighted tsvector) and dense (cosine): each
        candidate scores <span className="mono">Σ 1/(k + rank)</span> over the lists it appears in,{' '}
        <span className="mono">k = 60</span>. The large k damps rank-1 dominance so one leg cannot
        outvote the other.
      </p>
      <p className="note recon" style={{ marginBottom: '10px' }}>
        {(r && r.note) || ''}
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              {['#', 'item', 'label', 'src', 'lex rank', 'dense rank', 'cosine', 'jaccard', 'RRF fused'].map(
                (t) => (
                  <th key={t}>{t}</th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {candidates.map((cd, i) => {
              const si = srcInfo(cd.label_source);
              return (
                <tr key={`${cd.item_id}-${i}`} className={cd.is_self ? 'is-self' : ''}>
                  <td className="rank">{String(i + 1)}</td>
                  <td>
                    <ChipLink label={cd.item_id} url={cd.ui_url} className="mono" />
                    {cd.is_self ? (
                      <span
                        className="chip"
                        style={{ marginLeft: '6px' }}
                        title="The query item retrieved itself — proof the index sees it; excluded from candidate features."
                      >
                        this item
                      </span>
                    ) : (
                      ''
                    )}
                  </td>
                  <td>
                    <DefectBadge locator={cd.issue_type} group={labelGroup(cd.issue_type)} />
                  </td>
                  <td>
                    <span className="chip" title={`raw label_source: ${si.raw ?? 'none'}`}>
                      {si.plain}
                    </span>
                  </td>
                  <td className="rank">{cd.sparse_rank ?? '—'}</td>
                  <td className="rank">{cd.dense_rank ?? '—'}</td>
                  <td className="num">{cd.cosine == null ? '—' : fmt(cd.cosine, 3)}</td>
                  <td className="num">{fmt(cd.jaccard_templates, 2)}</td>
                  <td>
                    <div className="flex center gap-8">
                      <Microbar
                        ratio={(cd.rrf_score || 0) / maxRrf}
                        width={80}
                        title={`1/(60+${cd.sparse_rank ?? '∞'}) + 1/(60+${
                          cd.dense_rank ?? '∞'
                        }) = ${fmt(cd.rrf_score, 4)}`}
                      />
                      <span className="mono" style={{ fontSize: '11px' }}>
                        {fmt(cd.rrf_score, 4)}
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note" style={{ marginTop: '8px' }}>
        ordered by <span className="mono">rrf_score DESC, item_id DESC</span> (tie-break).
      </p>
    </EngDetails>
  );
}

function MatchingEng({ d }: { d: JourneyResponse }) {
  const m = d.matching || {};
  const r = d.reconstruction || {};
  const q = r.query || {};
  const mm = m.matched_mode;
  return (
    <EngDetails storageKey="matching">
      <dl className="kv">
        <dt>stage</dt>
        <dd className="mono">{m.stage}</dd>
        <dt>matched_item_id</dt>
        <dd className="mono">{m.matched_item_id ?? 'null'}</dd>
        <dt>matched_mode_id</dt>
        <dd className="mono">{m.matched_mode_id ?? 'null'}</dd>
        <dt>suggestion_id</dt>
        <dd className="mono">{m.suggestion_id ?? '—'}</dd>
        {mm ? (
          <>
            <dt>mode.label</dt>
            <dd className="mono">{mm.label}</dd>
            <dt>mode.purity</dt>
            <dd className="mono">{String(mm.purity)}</dd>
            <dt>mode.support</dt>
            <dd className="mono">{String(mm.support)}</dd>
            <dt>mode.seed_key</dt>
            <dd className="mono">{mm.seed_key ?? '—'}</dd>
          </>
        ) : null}
        <dt>hybrid_retrieval_version</dt>
        <dd className="mono">{String(r.hybrid_retrieval_version ?? '—')}</dd>
        <dt>stage_note</dt>
        <dd>{m.stage_note || '—'}</dd>
      </dl>
      <div className="section-title" style={{ margin: '12px 0 6px' }}>
        reconstruction query (bound into the versioned SQL)
      </div>
      <dl className="kv">
        <dt>salient_terms</dt>
        <dd className="mono" style={{ fontSize: '11px', wordBreak: 'break-word' }}>
          {q.salient_terms || '—'}
        </dd>
        <dt>template_ids</dt>
        <dd className="mono">{`[${(q.template_ids || []).join(', ')}]`}</dd>
        <dt>emb_model_ver</dt>
        <dd className="mono">{String(q.emb_model_ver ?? '—')}</dd>
        <dt>dense_active</dt>
        <dd className="mono">{String(q.dense_active ?? '—')}</dd>
      </dl>
    </EngDetails>
  );
}

export function MatchingCard({ d }: { d: JourneyResponse }) {
  const m = d.matching || {};
  const r = d.reconstruction;
  const candidates = (r && r.candidates) || [];

  if (!m.has_suggestion) {
    return (
      <Card title="Matching" step={3} sub="stage A → B → C cascade" id="j-matching">
        <EmptyState
          icon="🎯"
          title="Not matched yet"
          body="Not matched yet — no suggestion row; the matcher has not run for this item (or it was never a failure)."
          tag="analyzer.suggestion"
        />
      </Card>
    );
  }

  const mm = m.matched_mode;
  const version = r ? r.hybrid_retrieval_version : '?';

  return (
    <Card title="Matching" step={3} sub="stage A → B → C cascade" id="j-matching">
      <MatchTakeaway d={d} />
      <Funnel d={d} />

      {mm ? (
        <div className="flex gap-8 center wrap" style={{ marginTop: '12px' }}>
          <DefectBadge locator={mm.label} group={mm.label_group} />
          <span className="chip">{mm.title || `mode ${mm.mode_id}`}</span>
          <span
            className="chip"
            title="Share of this mode's members carrying its label — 1.00 = unanimous. ≥ 0.95 required to short-circuit."
          >
            {`purity ${fmt(mm.purity, 2)} `}
            <span className="muted">(≥ 0.95)</span>
          </span>
          <span className="chip" title="Confirmed members of this mode. ≥ 10 required to short-circuit.">
            {`support ${mm.support} `}
            <span className="muted">(≥ 10)</span>
          </span>
          {mm.status ? <span className="chip mono">{mm.status}</span> : null}
          {mm.seed_key ? <span className="chip mono">{`seed:${mm.seed_key}`}</span> : null}
        </div>
      ) : null}

      {candidates.length ? (
        <>
          <CandStrip d={d} />
          <RrfDrawer d={d} />
        </>
      ) : (
        <>
          <div className="section-title" style={{ marginTop: '16px' }}>
            {`Live retrieval re-run (Stage C, HYBRID_RETRIEVAL v${version})`}
          </div>
          <EmptyState
            icon="🔍"
            title="No candidates retrieved"
            body={`0 candidates from HYBRID_RETRIEVAL v${version} — no lexical or dense hit in scope against current data (too few comparable signatures).`}
          />
        </>
      )}
      <MatchingEng d={d} />
    </Card>
  );
}
