// Stage 2 — Grouping: who else failed like this in the same run.

import { Fragment, useEffect } from 'react';

import { Card, DefectBadge, EmptyState, EngDetails, RpIcon, SiMeter } from '../../components';
import type { JourneyGroupMember, JourneyGrouping, JourneyResponse } from '../../app/types';
import { fmt } from '../../lib/format';
import { DEFECT_GROUPS, defectName } from '../../lib/labels';
import { pulse } from './motion';
import { sentence } from './sentence';

/** Degenerate guard: dominant + solo cannot be a burst. */
export function isBurstGroup(g?: JourneyGrouping | null): boolean {
  return !!(g && g.dominant && (g.member_count ?? 0) > 1);
}

// L1 grouping takeaway — deterministic template (solo / shared / burst).
function GroupTakeaway({ g, burst }: { g: JourneyGrouping; burst: boolean }) {
  const n = g.member_count ?? 0;
  const si = fmt(g.si_prior, 2);
  const s = sentence();
  if (n === 1) {
    s.b('Alone in this launch')
      .add(' — no other failure shares this signature; no group diagnosis applied ')
      .muted('(solo group)')
      .add('.');
  } else if (burst) {
    const lfc = g.launch_failed_count;
    const hasShare = lfc != null && lfc > 0 && n <= lfc;
    s.b('Burst:').add(' a new fingerprint covers ');
    if (hasShare) {
      s.b(`${n} of ${lfc} failures`).add(
        ` in this launch (${Math.round((100 * n) / (lfc as number))}%) — strong `,
      );
    } else {
      s.b(`${n} failures`).add(' of this launch — strong ');
    }
    s.b('System Issue')
      .add(' prior applied ')
      .muted(`(dominant; si_prior ${si})`)
      .add('.');
  } else {
    s.b(`${n} failures share this signature`)
      .add(` in this launch — one diagnosis should cover all ${n} `)
      .muted('(shared-cause group)')
      .add('.');
  }
  return s.render();
}

// Per-label-group count key under the dot strip.
function MemberKey({ g, members }: { g: JourneyGrouping; members: JourneyGroupMember[] }) {
  const byGroup = new Map<string, { count: number; locator?: string | null; group?: string | null }>();
  for (const m of members) {
    const key = m.label_group || 'none';
    const e = byGroup.get(key) || { count: 0, locator: m.issue_type, group: m.label_group };
    e.count += 1;
    byGroup.set(key, e);
  }
  const total = g.member_count ?? members.length;
  const prefix =
    members.length < total
      ? `${members.length} of ${total} members: `
      : `${total} member${total === 1 ? '' : 's'}: `;
  const entries = [...byGroup.entries()].sort((a, b) => b[1].count - a[1].count);
  return (
    <div className="dot-strip-key">
      {prefix}
      {entries.map(([key, e], i) => (
        <Fragment key={key}>
          <DefectBadge abbr locator={e.locator} group={e.group} />
          <span>{`×${e.count}`}</span>
          {i < entries.length - 1 ? <span className="muted">·</span> : null}
        </Fragment>
      ))}
    </div>
  );
}

// L2 member dot-strip; honest degradation when members are not linkable.
function MemberStrip({
  g,
  onNavigate,
}: {
  g: JourneyGrouping;
  onNavigate: (itemId: number) => void;
}) {
  const members = g.members || [];
  if (!members.length) {
    return (
      <div style={{ flex: '1 1 240px' }}>
        <div className="si-label">failures with this error</div>
        <div className="dot-strip">
          <span className="chip">{`${g.member_count} failures with this signature`}</span>
        </div>
        <div className="dot-strip-key">individual members not linkable for this group.</div>
      </div>
    );
  }
  const more = (g.member_count ?? members.length) - members.length;
  return (
    <div style={{ flex: '1 1 240px' }}>
      <div className="si-label">failures with this error</div>
      <div className="dot-strip" role="list" aria-label="group members">
        {members.map((m) => {
          const cls = DEFECT_GROUPS.includes(m.label_group || '') ? (m.label_group as string) : 'none';
          const title = `item ${m.item_id}${m.is_self ? ' · this item' : ''} · ${defectName(
            m.issue_type,
            m.label_group,
          )}`;
          return (
            <button
              key={m.item_id}
              type="button"
              className={`m-dot ${cls}${m.is_self ? ' self' : ''}`}
              role="listitem"
              title={title}
              aria-label={title}
              onClick={() => onNavigate(m.item_id)}
            />
          );
        })}
        {more > 0 ? <span className="chip">{`+${more} more`}</span> : null}
      </div>
      <MemberKey g={g} members={members} />
    </div>
  );
}

export function GroupingCard({
  d,
  onNavigate,
}: {
  d: JourneyResponse;
  onNavigate: (itemId: number) => void;
}) {
  const g = d.grouping;
  const burst = isBurstGroup(g);

  // One attention pulse on first render of a burst card.
  useEffect(() => {
    if (!burst) return;
    const id = requestAnimationFrame(() => pulse(document.getElementById('j-grouping'), 'var(--warning)'));
    return () => cancelAnimationFrame(id);
  }, [burst, d.item.item_id]);

  const title = burst ? (
    <>
      Grouping
      <span
        className="badge"
        style={{
          marginLeft: '4px',
          background: 'color-mix(in srgb,var(--warning) 16%,transparent)',
          color: 'var(--warning)',
          borderColor: 'var(--warning)',
        }}
      >
        <span className="dot" style={{ background: 'var(--warning)' }} />
        <RpIcon name="warning" size={12} /> burst
      </span>
    </>
  ) : (
    'Grouping'
  );

  if (!g) {
    return (
      <Card
        title={title}
        step={2}
        sub="who else failed like this in the same run (co-failure launch group)"
        id="j-grouping"
      >
        <EmptyState
          icon="🧩"
          title="No group"
          body="No group — grouping needs at least one failure with a comparable error ID (error_hash) in this launch. This item’s failure was not comparable to any other."
          tag="analyzer.launch_group"
        />
      </Card>
    );
  }

  const si = g.si_prior ?? 0;

  return (
    <Card
      title={title}
      step={2}
      sub="who else failed like this in the same run (co-failure launch group)"
      className={burst ? 'burst-accent' : undefined}
      id="j-grouping"
    >
      <GroupTakeaway g={g} burst={burst} />

      <div className="grp-l2">
        <MemberStrip g={g} onNavigate={onNavigate} />
        {si > 0 ? (
          <div
            style={{ flex: '1 1 200px', minWidth: '200px' }}
            title="burst signal (si_prior) — burst prior · range 0 to 0.9"
          >
            <SiMeter
              label="burst signal (si_prior)"
              value=""
              ratio={si / 0.9}
              tickRatio={0.5}
              scale={['0', <span className="si-val mono">{fmt(si, 2)}</span>, '0.9 max']}
              note="Prior evidence input for si, capped at 0.9 — one decision signal, not a verdict."
            />
          </div>
        ) : (
          <div className="si-note">
            <div className="si-label">burst signal (si_prior)</div>
            No burst signal: fingerprint not new to history or share of launch failures below the
            gate (new fp · ≥5 members · &gt;40% share).
          </div>
        )}
      </div>

      <EngDetails storageKey="grouping">
        <dl className="kv">
          <dt>group_id</dt>
          <dd className="mono">{String(g.group_id)}</dd>
          <dt>fingerprint</dt>
          <dd className="mono">{String(g.fingerprint)}</dd>
          <dt>member_count</dt>
          <dd className="mono">{String(g.member_count)}</dd>
          <dt>dominant</dt>
          <dd className="mono">{String(g.dominant)}</dd>
          <dt>si_prior</dt>
          <dd className="mono">{String(g.si_prior)}</dd>
          {g.launch_failed_count != null ? (
            <>
              <dt>launch_failed_count</dt>
              <dd className="mono">{String(g.launch_failed_count)}</dd>
            </>
          ) : null}
        </dl>
        <p className="note" style={{ marginTop: '8px' }}>
          co-failure launch group
        </p>
      </EngDetails>
    </Card>
  );
}
