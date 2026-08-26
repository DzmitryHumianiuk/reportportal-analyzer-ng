// Stage 1 — Signature: the field-separated FTS document and its fingerprints.

import type { ReactNode } from 'react';

import { Card, EmptyState, HighlightedPattern } from '../../components';
import type { JourneyResponse, Template } from '../../app/types';

function FpChip({ name, value }: { name: string; value?: string | null }) {
  return (
    <span className="chip mono-strong" title={name}>
      <span
        style={{
          color: 'var(--muted)',
          fontFamily: 'var(--sans)',
          fontSize: '10px',
          textTransform: 'uppercase',
          letterSpacing: '.5px',
        }}
      >
        {name}
      </span>
      {value ?? '—'}
    </span>
  );
}

function TemplateCard({ t }: { t: Template }) {
  return (
    <div
      style={{
        padding: '8px 10px',
        background: 'var(--surface-2)',
        border: '1px solid var(--hairline)',
        borderRadius: '8px',
        marginBottom: '6px',
      }}
    >
      <div className="flex between center mb-8">
        <span className="chip mono">{`#${t.template_id}`}</span>
        {t.missing ? (
          <span className="muted">template row missing</span>
        ) : (
          <span className="muted" style={{ fontSize: '11px' }}>
            {`${t.token_count} tok · ${t.match_count} matches`}
          </span>
        )}
      </div>
      <div className="pattern">
        <HighlightedPattern pattern={t.pattern ?? null} />
      </div>
    </div>
  );
}

const TPL_VISIBLE = 5;

export function SignatureCard({ d }: { d: JourneyResponse }) {
  const s = d.signature;
  if (!s) {
    return (
      <Card title="Signature" step={1} sub="field-separated FTS doc + fingerprints" id="j-signature">
        <EmptyState
          icon="🔤"
          title="No signature"
          body="This item has no failure_signature row — it produced no error logs, so the signature builder had nothing to index."
          tag="analyzer.failure_signature"
        />
      </Card>
    );
  }

  const fields: Array<[string, string, string]> = [
    ['EXCEPTION', 'fb-exc', s.exc_text || ''],
    ['MESSAGE', 'fb-msg', s.msg_text || ''],
    ['FRAMES', 'fb-frames', (s.top_frames || []).join('  ›  ')],
    ['TEMPLATES', 'fb-templates', (s.template_ids || []).join(', ')],
    ['CODES', 'fb-codes', (s.status_codes || []).join(', ')],
  ];

  const templates = d.templates || [];
  const collapse = templates.length > TPL_VISIBLE + 1;
  const shown = collapse ? templates.slice(0, TPL_VISIBLE) : templates;
  const rest = collapse ? templates.slice(TPL_VISIBLE) : [];

  const rows: ReactNode[] = [];
  for (const [name, cls, val] of fields) {
    if (!val) continue;
    rows.push(
      <span key={`${name}-b`} className={`field-badge ${cls}`} style={{ justifySelf: 'end' }}>
        {name}
      </span>,
      <span
        key={`${name}-v`}
        className="mono"
        style={{
          fontSize: '12.5px',
          color: 'var(--ink-2)',
          wordBreak: 'break-word',
          minWidth: 0,
        }}
      >
        {val}
      </span>,
    );
  }

  return (
    <Card title="Signature" step={1} sub="field-separated FTS doc + fingerprints" id="j-signature">
      <div className="flex gap-8 wrap mb-8">
        <FpChip name="exception_fp" value={s.exception_fp} />
        <FpChip name="error_hash" value={s.error_hash} />
        <span className="chip">{`emb_model_ver ${s.emb_model_ver}`}</span>
        <span className="chip" style={{ color: s.has_emb ? 'var(--good)' : 'var(--muted)' }}>
          {s.has_emb ? '● embedded' : '○ not embedded'}
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'max-content 1fr',
          gap: '8px 12px',
          alignItems: 'baseline',
        }}
      >
        {rows}
      </div>

      {templates.length ? (
        <>
          <div className="section-title" style={{ marginTop: '16px' }}>
            {`Referenced Drain3 templates (${templates.length})`}
          </div>
          {shown.map((t) => (
            <TemplateCard key={t.template_id} t={t} />
          ))}
          {collapse ? (
            <details className="out-drawer tpl-more">
              <summary>
                <span className="more">{`show ${rest.length} more templates`}</span>
                <span className="less">show fewer templates</span>
              </summary>
              {rest.map((t) => (
                <TemplateCard key={t.template_id} t={t} />
              ))}
            </details>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
