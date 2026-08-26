// Launch Groups — co-failure graph. Nodes = items, clustered by launch_group;
// node color = final label; accent ring = auto-analyzed; burst groups flagged
// si_prior.
//
// The old view drew this with a d3 force simulation. It is now an ECharts
// `graph` series with `layout: 'force'`: `roam` replaces d3.zoom, `draggable`
// replaces d3.drag, and the per-group forceX gravity wells are approximated by
// seeding each node's initial x at its group's anchor (groups evenly spaced)
// plus one category per group. The RP deep link that used to sit on the node's
// id text now lives in the (enterable) HTML tooltip, and the auto-fit-until-
// touched loop becomes the explicit "fit" button.

import { Button, Dropdown, FieldLabel } from '@reportportal/ui-kit';
import { useEffect, useMemo, useState } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type { GroupNode, GroupsResponse, Launch, LaunchGroup } from '../../app/types';
import { Card, ChipLink, EChart, EmptyState, Loading, RpIcon } from '../../components';
import { ACCENT, HAIRLINE, INK, INK2 } from '../../lib/colors';
import { fmt } from '../../lib/format';
import { defectColor, defectInfo, defectName, setDefects } from '../../lib/labels';
import './groups.css';

/** Nominal layout box used to seed initial positions (the old svg viewBox). */
const GRAPH_WIDTH = 700;
const GRAPH_HEIGHT = 560;

/** Legend order, same five RP defect groups the original listed. */
const LEGEND_GROUPS = ['pb', 'ab', 'si', 'nd', 'ti'];

/** Golden angle — spreads a group's members deterministically around its anchor. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function nodeLabelText(n: GroupNode): string {
  if (!n.issue_type) return defectName(null, n.label_group);
  const info = defectInfo(n.issue_type);
  return info && info.name ? `${info.name} (${n.issue_type})` : n.issue_type;
}

const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

/**
 * An ECharts tooltip formatter result is injected as HTML, so every
 * API-provided string in it must be escaped first (item names and RP URLs are
 * untrusted input).
 */
function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

/** Only real http(s) or same-origin links become anchors — never `javascript:`. */
function safeUrl(url?: string | null): string | null {
  if (!url) return null;
  const trimmed = String(url).trim();
  return /^(https?:\/\/|\/|\.{1,2}\/)/i.test(trimmed) ? trimmed : null;
}

function nodeTip(n: GroupNode): string {
  const url = safeUrl(n.ui_url);
  const id = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener" style="color:${INK}">item ${n.item_id} ↗</a>`
    : `item ${esc(n.item_id)}`;
  const lines = [`<b>${id}</b>`];
  if (n.name) lines.push(esc(n.name));
  lines.push(`label ${esc(nodeLabelText(n))} · group ${n.group_id ?? 'none'}`);
  if (n.is_auto_analyzed) lines.push('⭑ auto-analyzed');
  return lines.join('<br>');
}

interface GraphNodeItem {
  name: string;
  category: number;
  x: number;
  y: number;
  symbolSize: number;
  itemStyle: { color: string; borderColor: string; borderWidth: number };
  tip: string;
}

interface TooltipParams {
  data?: { tip?: string };
}

/**
 * Build the whole `graph` option from the node list. Links are synthesized the
 * same way the old view did it: inside every group a star from the first member
 * to each of the others.
 */
function buildGraphOption(nodes: GroupNode[]) {
  const groupIds = [
    ...new Set(nodes.map((n) => n.group_id).filter((g): g is number => g != null)),
  ];
  const catOf = new Map<number, number>(groupIds.map((g, i) => [g, i]));
  const categories = groupIds.map((g) => ({ name: `group ${g}` }));
  const hasUngrouped = nodes.some((n) => n.group_id == null);
  const ungroupedCat = categories.length;
  if (hasUngrouped) categories.push({ name: 'no group' });

  const placed = new Map<string, number>();
  const data: GraphNodeItem[] = nodes.map((n) => {
    const gid = n.group_id;
    const anchorX =
      gid != null
        ? ((catOf.get(gid) as number) + 0.5) * (GRAPH_WIDTH / Math.max(1, groupIds.length))
        : GRAPH_WIDTH / 2;
    const key = String(gid);
    const seat = placed.get(key) ?? 0;
    placed.set(key, seat + 1);
    const angle = seat * GOLDEN_ANGLE;
    const radius = 10 * Math.sqrt(seat);
    return {
      name: String(n.item_id),
      category: gid != null ? (catOf.get(gid) as number) : ungroupedCat,
      x: anchorX + Math.cos(angle) * radius,
      y: GRAPH_HEIGHT / 2 + Math.sin(angle) * radius,
      // r = 11 in the old svg.
      symbolSize: 22,
      itemStyle: {
        color: defectColor(n.issue_type, n.label_group),
        borderColor: n.is_auto_analyzed ? ACCENT : '#ffffff',
        borderWidth: n.is_auto_analyzed ? 3 : 1.2,
      },
      tip: nodeTip(n),
    };
  });

  const byGroup = new Map<number, GroupNode[]>();
  for (const n of nodes) {
    if (n.group_id == null) continue;
    const bucket = byGroup.get(n.group_id);
    if (bucket) bucket.push(n);
    else byGroup.set(n.group_id, [n]);
  }
  const links: Array<{ source: string; target: string }> = [];
  for (const members of byGroup.values()) {
    for (let i = 1; i < members.length; i++) {
      links.push({ source: String(members[0].item_id), target: String(members[i].item_id) });
    }
  }

  return {
    tooltip: {
      backgroundColor: '#ffffff',
      borderColor: HAIRLINE,
      borderWidth: 1,
      textStyle: { color: INK2 },
      extraCssText: 'box-shadow:0 8px 40px rgba(0,0,0,.15);border-radius:8px;',
      confine: true,
      enterable: true,
      formatter: (p: TooltipParams) => p.data?.tip ?? '',
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        // Matches the old d3.zoom scaleExtent.
        scaleLimit: { min: 0.1, max: 8 },
        categories,
        data,
        links,
        force: { repulsion: 160, edgeLength: 46, gravity: 0.06 },
        lineStyle: { color: HAIRLINE, opacity: 0.7, width: 1.2 },
        label: {
          show: true,
          position: 'bottom',
          distance: 5,
          fontFamily: 'ui-monospace, monospace',
          fontSize: 10,
          color: INK2,
        },
      },
    ],
  };
}

function GroupList({ groups }: { groups: LaunchGroup[] }) {
  if (!groups.length) return <div className="muted">No launch_group rows.</div>;
  return (
    <div className="groups-list">
      {groups.map((g) => (
        <div className="group-mini" key={g.group_id}>
          <div className="flex between center">
            <span className="chip mono">{`group ${g.group_id}`}</span>
            {g.dominant ? (
              <span className="badge group-mini-burst">{`🔥 burst · si ${fmt(g.si_prior, 2)}`}</span>
            ) : (
              <span className="muted group-mini-si">{`si ${fmt(g.si_prior, 2)}`}</span>
            )}
          </div>
          <div className="flex gap-8 mt-8 group-mini-meta">
            <ChipLink label={`launch ${g.launch_id}`} url={g.launch_url} className="muted" />
            <span className="muted">{`${g.member_count} members`}</span>
            <span className="mono muted">{`fp ${String(g.fingerprint).slice(0, 8)}…`}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Groups() {
  const { project, link, patchLink, refreshTick } = useApp();
  const glaunch = link.glaunch ?? null;

  const [launches, setLaunches] = useState<Launch[]>([]);
  const [data, setData] = useState<GroupsResponse | null>(null);
  const [busy, setBusy] = useState(true);
  const [failure, setFailure] = useState<Error | null>(null);
  const [fitKey, setFitKey] = useState(0);

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    void (async () => {
      try {
        const payload = await api.launches(project);
        if (!cancelled) setLaunches(payload.launches);
      } catch (e) {
        if (!cancelled) setFailure(e as Error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setBusy(true);
    void (async () => {
      try {
        const payload = await api.groups(project, glaunch);
        if (cancelled) return;
        // The payload carries the per-project RP name map; installing it keeps
        // node colors and label names honest even before api/rp settles.
        if (payload.rp) setDefects(payload.rp.defects);
        setData(payload);
        setBusy(false);
      } catch (e) {
        if (!cancelled) setFailure(e as Error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, glaunch, refreshTick]);

  const options = useMemo(
    () => [
      { value: '', label: 'All launches' },
      ...launches.map((l) => ({
        value: String(l.launch_id),
        label: `${l.launch_name || 'launch'} · #${l.launch_number ?? '—'} (${l.item_count})`,
      })),
    ],
    [launches],
  );

  const nodes = data?.nodes ?? [];
  const graphOption = useMemo(() => {
    // fitKey is a deliberate dependency: a new option object makes <EChart>
    // call setOption({notMerge:true}), which drops the roam transform and
    // re-runs the layout from the seeded positions — the old "fit" button.
    void fitKey;
    return nodes.length ? buildGraphOption(nodes) : null;
  }, [nodes, fitKey]);

  // The shell's ViewErrorBoundary owns the failure state for every view.
  if (failure) throw failure;

  const showGraph = !busy && data != null;

  return (
    <>
      <div className="picker-row">
        <div className="field">
          <FieldLabel className="field-label">Launch</FieldLabel>
          <Dropdown
            options={options}
            value={glaunch == null ? '' : String(glaunch)}
            onChange={(v) => {
              const raw = String(v ?? '');
              patchLink({ glaunch: raw === '' ? null : Number(raw) });
            }}
            aria-label="Launch"
          />
        </div>
      </div>

      <div className="grid groups-grid">
        <Card title="Co‑failure graph" sub="items clustered by launch_group">
          {!showGraph ? <Loading /> : null}
          {showGraph && !nodes.length ? (
            <EmptyState
              icon="🕸️"
              title="No items"
              body="No test items for this selection."
              tag="analyzer.test_item"
            />
          ) : null}
          {showGraph && graphOption ? (
            <>
              <div className="flex gap-8 wrap mb-8 groups-legend">
                {LEGEND_GROUPS.map((g) => (
                  <span className="flex center gap-8 groups-legend-item" key={g}>
                    <span className="groups-dot" style={{ background: defectColor(null, g) }} />
                    {defectName(null, g)}
                  </span>
                ))}
                <span className="muted groups-legend-item">
                  {'◯ accent ring = auto‑analyzed'}
                </span>
                <span className="muted groups-legend-item groups-legend-hint">
                  {'wheel: zoom · drag bg: pan'}
                </span>
                <Button
                  variant="ghost"
                  icon={<RpIcon name="cycleArrows" size={12} />}
                  title="Fit graph to view"
                  onClick={() => setFitKey((k) => k + 1)}
                >
                  fit
                </Button>
              </div>
              <div className="groups-graph-wrap">
                <EChart option={graphOption} height={GRAPH_HEIGHT} />
              </div>
            </>
          ) : null}
        </Card>

        <Card title="Groups">
          {showGraph && nodes.length ? <GroupList groups={data.groups} /> : null}
        </Card>
      </div>
    </>
  );
}
