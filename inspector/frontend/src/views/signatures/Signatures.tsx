// Signatures Explorer — the fingerprint space, one row per distinct error_hash.
// Exposes Stage-A hash collisions / label bleed: rows whose members carry >1
// distinct non-ti ground-truth label are flagged as label-bleed candidates. Row
// click fetches the signature detail (representative exc/msg/frames/templates +
// member items) on demand — no N+1, the list stays a single aggregate query.
//
// The table is the custom `table.data` markup on purpose: the ui-kit Table has
// no API for a detail panel under a row, and the expansion below IS the view.

import { Button, FieldLabel, FieldText, SearchIcon, Toggle } from '@reportportal/ui-kit';
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../../app/api';
import type {
  SignatureHashResponse,
  SignatureLabelCount,
  SignatureRow,
  SignaturesResponse,
  SignaturesSummary,
} from '../../app/types';
import { useApp } from '../../app/state';
import {
  Card,
  ChipLink,
  DefectBadge,
  EmptyState,
  HighlightedPattern,
  Loading,
  RpIcon,
} from '../../components';
import { fmt, shortTime } from '../../lib/format';
import { setDefects } from '../../lib/labels';

import './signatures.css';

const SEARCH_DEBOUNCE_MS = 250;

const COLUMNS = [
  'error_hash',
  'members',
  'labels',
  'exception class',
  'status codes',
  'first / last seen',
];

/** `abcdef…wxyz` for anything longer than 12 chars; the full value rides title=. */
function shortHash(value: string): string {
  const s = String(value);
  return s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
}

// --------------------------------------------------------------------------- //
// Summary chips
// --------------------------------------------------------------------------- //

function Counter({ n, k, cls }: { n: number | null | undefined; k: string; cls?: string }) {
  return (
    <div className={`counter ${cls || ''}`}>
      <span className="n">{fmt(n)}</span>
      <span className="k">{k}</span>
    </div>
  );
}

function SummaryRow({ summary }: { summary: SignaturesSummary | null }) {
  return (
    <div className="flex gap-8 wrap mb-8" id="sig-summary">
      {summary ? (
        <>
          <Counter n={summary.distinct_error_hash} k="error_hash" cls="accent" />
          <Counter n={summary.distinct_exception_fp} k="exception_fp" />
          <Counter n={summary.items_with_signatures} k="signed items" />
          <Counter n={summary.embedded} k="embedded" cls="good" />
          <Counter
            n={summary.conflict_hashes}
            k="conflicts"
            cls={summary.conflict_hashes ? 'warn' : ''}
          />
        </>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Expanded detail panel
// --------------------------------------------------------------------------- //

function SignatureDetail({ detail }: { detail: SignatureHashResponse }) {
  const d = detail;
  const members = d.members || [];
  const labels = d.labels || [];
  const shown = d.member_shown ?? members.length;
  const total = d.member_total ?? members.length;
  const nonTi = labels.filter((l) => l.group !== 'ti' && l.group !== 'none').length;
  const repUrl = members.find((m) => m.item_id === d.representative_item_id)?.ui_url;

  const s = d.signature || {};
  const fields: Array<[string, string, string | null | undefined]> = [
    ['EXC', 'fb-exc', s.exc_text],
    ['MSG', 'fb-msg', s.msg_text],
    // frames_text is the fallback when the parsed top_frames list is empty
    // (as in the original view).
    ['FRAMES', 'fb-frames', (s.top_frames || []).join('  ›  ') || s.frames_text],
    ['CODES', 'fb-codes', (s.status_codes || []).join(', ')],
  ];

  return (
    <div className="sig-detail-body">
      <div className="flex between center wrap mb-8">
        <div className="flex center gap-8 wrap">
          <span className="chip mono-strong" title="error_hash">
            {d.error_hash}
          </span>
          <span className="chip mono" title="exception_fp">
            {`fp ${d.exception_fp ?? '—'}`}
          </span>
          <ChipLink
            label={`representative item ${d.representative_item_id ?? '—'}`}
            url={repUrl}
            className="chip mono"
          />
        </div>
        {d.is_conflict ? (
          <span className="badge sig-bleed-badge">⚠ label-bleed candidate</span>
        ) : null}
      </div>

      {d.is_conflict ? (
        <p className="note recon sig-bleed-note">
          {`This error_hash carries ${nonTi} distinct non-ti labels across its ${total} member${
            total === 1 ? '' : 's'
          }. Stage A inherits labels via exact error_hash equality, so members here risk inheriting the wrong ground-truth label.`}
        </p>
      ) : null}

      <div className="sig-fields">
        {fields.map(([name, cls, val]) =>
          val ? (
            <div className="sig-field-row" key={name}>
              <span className={`field-badge ${cls}`}>{name}</span>
              <span className="mono sig-field-val">{val}</span>
            </div>
          ) : null,
        )}
      </div>

      {d.templates && d.templates.length ? (
        <>
          <div className="section-title sig-section">
            {`Referenced Drain3 templates (${d.templates.length})`}
          </div>
          {d.templates.map((t) => (
            <div className="sig-tpl" key={t.template_id}>
              <div className="flex between center mb-8">
                <span className="chip mono">{`#${t.template_id}`}</span>
                {t.missing ? (
                  <span className="muted">template row missing</span>
                ) : (
                  <span className="muted sig-tpl-meta">
                    {`${t.token_count} tok · ${fmt(t.match_count)} matches`}
                  </span>
                )}
              </div>
              <div className="pattern">
                <HighlightedPattern pattern={t.pattern ?? null} />
              </div>
            </div>
          ))}
        </>
      ) : null}

      <div className="section-title sig-section">
        {`Member items (${shown}${total > shown ? ` of ${total}` : ''})`}
      </div>
      <div className="sig-members">
        {members.map((m) => (
          <div
            className={`flex between center wrap sig-member${m.is_auto_analyzed ? ' halo-auto' : ''}`}
            key={m.item_id}
          >
            <div className="flex center gap-8 wrap sig-member-main">
              <ChipLink label={`item ${m.item_id}`} url={m.ui_url} className="chip mono" />
              <DefectBadge locator={m.issue_type} group={m.label_group} abbr />
              <span className="sig-member-name">{m.item_name || '—'}</span>
              {m.is_auto_analyzed ? (
                <span className="badge sig-auto-badge">
                  <RpIcon name="bolt" size={12} /> auto
                </span>
              ) : null}
            </div>
            <div className="flex center gap-8">
              <ChipLink
                label={m.launch_name || `launch ${m.launch_id}`}
                url={m.launch_url}
                className="chip"
              />
              <span className="muted mono sig-indexed">{shortTime(m.indexed_at)}</span>
            </div>
          </div>
        ))}
      </div>
      {total > shown ? (
        <p className="note mt-8">
          {`${total - shown} more member${total - shown === 1 ? '' : 's'} not shown (capped at ${shown}).`}
        </p>
      ) : null}
    </div>
  );
}

type DetailState =
  | { status: 'loading' }
  | { status: 'ready'; data: SignatureHashResponse }
  | { status: 'error'; message: string };

function DetailCell({ state }: { state: DetailState }) {
  if (state.status === 'loading') return <Loading text="Loading signature…" />;
  if (state.status === 'error') {
    return <EmptyState icon="🚫" title="Detail failed" body={state.message} />;
  }
  return <SignatureDetail detail={state.data} />;
}

// --------------------------------------------------------------------------- //
// Row
// --------------------------------------------------------------------------- //

function LabelChips({ labels }: { labels: SignatureLabelCount[] }) {
  return (
    <div className="flex gap-8 wrap">
      {labels.map((l) => (
        <span className="sig-label-chip" key={`${l.group}-${l.locator}`}>
          <DefectBadge locator={l.locator} group={l.group} abbr />
          <span className="muted mono sig-label-count">{`×${l.count}`}</span>
        </span>
      ))}
    </div>
  );
}

function Row({
  row,
  open,
  onToggle,
}: {
  row: SignatureRow;
  open: boolean;
  onToggle: (hash: string) => void;
}) {
  const excClasses = row.exc_classes || [];
  const statusCodes = row.status_codes || [];
  return (
    <tr
      className={`sig-row${row.is_conflict ? ' conflict' : ''}${open ? ' open' : ''}`}
      style={{ cursor: 'pointer' }}
      onClick={() => onToggle(String(row.error_hash))}
    >
      <td>
        <span className="flex center gap-8">
          {row.is_conflict ? (
            <span
              className="conflict-flag"
              title="label-bleed candidate: members carry >1 distinct non-ti label"
            >
              ⚠
            </span>
          ) : null}
          <span className="mono" title={row.error_hash}>
            {shortHash(row.error_hash)}
          </span>
        </span>
      </td>
      <td className="num">{fmt(row.member_count)}</td>
      <td>
        <LabelChips labels={row.labels || []} />
      </td>
      <td>
        {excClasses.length ? (
          <span className="mono sig-exc">{excClasses.join(', ')}</span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        {statusCodes.length ? (
          <span className="flex gap-8 wrap">
            {statusCodes.map((c) => (
              <span className="chip mono" key={c}>
                {c}
              </span>
            ))}
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="muted mono sig-seen">
        {`${shortTime(row.first_seen)} → ${shortTime(row.last_seen)}`}
      </td>
    </tr>
  );
}

// --------------------------------------------------------------------------- //
// View
// --------------------------------------------------------------------------- //

export default function Signatures() {
  const { project, link, patchLink, refreshTick } = useApp();

  const appliedQ = link.q || '';
  const conflicts = !!link.conflicts;
  const expanded = link.hash || null;

  const [draft, setDraft] = useState(appliedQ);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<SignaturesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [detail, setDetail] = useState<DetailState | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The pager belongs to one project. Offset 120 in a big project points past
  // the end of a small one, so after a project switch the list would come back
  // empty and read as "this project has no failure_signature rows yet", with no
  // way back: the pager only renders when there are rows. Reset while
  // rendering, so the fetch below never runs with the old project's offset.
  // The open row needs no reset here — a project switch already drops
  // link.hash, which collapses the expansion.
  const [pagedProject, setPagedProject] = useState(project);
  if (project !== pagedProject) {
    setPagedProject(project);
    setOffset(0);
  }

  // A hash edit / back-navigation changes q under us: follow it, unless a
  // debounce is still pending (the user is typing — their draft wins).
  useEffect(() => {
    if (debounceRef.current == null) setDraft(appliedQ);
  }, [appliedQ]);

  useEffect(
    () => () => {
      if (debounceRef.current != null) clearTimeout(debounceRef.current);
    },
    [],
  );

  const applyQuery = useCallback(
    (value: string) => {
      setOffset(0);
      patchLink({ q: value, hash: null });
    },
    [patchLink],
  );

  const onSearchInput = useCallback(
    (value: string) => {
      setDraft(value);
      if (debounceRef.current != null) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        applyQuery(value);
      }, SEARCH_DEBOUNCE_MS);
    },
    [applyQuery],
  );

  // ---- list ---------------------------------------------------------------
  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const payload = await api.signatures(project, appliedQ, conflicts, offset);
        if (cancelled) return;
        if (payload.rp) setDefects(payload.rp.defects);
        setData(payload);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, appliedQ, conflicts, offset, refreshTick]);

  // ---- expanded row detail ------------------------------------------------
  useEffect(() => {
    if (project == null || expanded == null) {
      setDetail(null);
      return undefined;
    }
    let cancelled = false;
    setDetail({ status: 'loading' });
    void (async () => {
      try {
        const payload = await api.signatureHash(project, expanded);
        if (cancelled) return;
        if (payload.rp) setDefects(payload.rp.defects);
        setDetail({ status: 'ready', data: payload });
      } catch (e) {
        if (!cancelled) {
          setDetail({ status: 'error', message: String((e as Error)?.message || e) });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // refreshTick: an auto-refresh tick reloads the list, so the row opened
    // under it has to reload too — otherwise the detail drifts from the row.
  }, [project, expanded, refreshTick]);

  const toggleRow = useCallback(
    (hash: string) => {
      patchLink({ hash: expanded === hash ? null : hash });
    },
    [expanded, patchLink],
  );

  // A failed list fetch is the old mount() rejection: hand it to the shell's
  // view error boundary instead of inventing a second error surface here.
  if (error) throw error;

  const rows = data?.rows || [];
  const sub = data
    ? `${data.count} error_hash${data.count === 1 ? '' : 'es'}` +
      (conflicts ? ' · conflicts only' : '') +
      (appliedQ ? ` · matching “${appliedQ}”` : '')
    : 'one row per distinct error_hash';

  const canPrev = !!data && data.offset > 0;
  const canNext = !!data && data.count >= data.limit;

  return (
    <>
      <div className="picker-row">
        <div className="field">
          <FieldLabel className="field-label">Search signatures</FieldLabel>
          <FieldText
            className="sig-search"
            // The kit paints the placeholder as its own element, so the input
            // needs a name of its own for screen readers.
            aria-label="Search signatures"
            placeholder="Search exc / msg / frames (ILIKE)…"
            value={draft}
            defaultWidth={false}
            clearable
            startIcon={<SearchIcon />}
            onChange={(e) => onSearchInput(e.target.value)}
            onClear={() => {
              setDraft('');
              if (debounceRef.current != null) {
                clearTimeout(debounceRef.current);
                debounceRef.current = null;
              }
              applyQuery('');
            }}
          />
        </div>
        <div className="field">
          <FieldLabel className="field-label">Filter</FieldLabel>
          <Toggle
            value={conflicts}
            title="Only error_hashes whose members carry >1 distinct non-ti label"
            onChange={(e) => {
              setOffset(0);
              patchLink({ conflicts: e.target.checked });
            }}
          >
            ⚠ only conflicts
          </Toggle>
        </div>
      </div>

      <SummaryRow summary={data ? data.summary : null} />

      <Card title="Fingerprint space" sub={sub}>
        {loading ? <Loading /> : null}
        {!loading && !rows.length ? (
          <EmptyState
            icon="🧬"
            title="No signatures"
            body={
              conflicts
                ? 'No error_hash in this project has members carrying more than one distinct non-ti label — no hash-collision label bleed detected.'
                : appliedQ
                  ? 'No signature matches your search across exc / msg / frames text.'
                  : 'This project has no failure_signature rows yet — signatures are built as error logs are indexed.'
            }
            tag="analyzer.failure_signature"
          />
        ) : null}
        {!loading && rows.length ? (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    {COLUMNS.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const isOpen = expanded != null && String(r.error_hash) === String(expanded);
                    return (
                      <Fragment key={r.error_hash}>
                        <Row row={r} open={isOpen} onToggle={toggleRow} />
                        {isOpen && detail ? (
                          <tr className="sig-detail">
                            <td colSpan={6}>
                              <DetailCell state={detail} />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {canPrev || canNext ? (
              <div className="flex between center mt-16">
                <span className="muted sig-pager-note">
                  {`rows ${(data?.offset ?? 0) + 1}–${(data?.offset ?? 0) + (data?.count ?? 0)}`}
                </span>
                <div className="flex gap-8">
                  <Button
                    variant="ghost"
                    disabled={!canPrev}
                    onClick={() =>
                      setOffset(Math.max(0, (data?.offset ?? 0) - (data?.limit ?? 0)))
                    }
                  >
                    ← prev
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={!canNext}
                    onClick={() => setOffset((data?.offset ?? 0) + (data?.limit ?? 0))}
                  >
                    next →
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </>
  );
}
