// Modes Map — PCA-3D (numpy SVD on the backend) of failure_signature embeddings
// + failure_mode centroids as stars. Honest empty-state when embeddings absent;
// graceful 2D fallback when the projection has < 3 usable dimensions.

import { Button } from '@reportportal/ui-kit';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type { ModePoint, Modes3dResponse } from '../../app/types';
import { Card } from '../../components/Card';
import { EChart } from '../../components/EChart';
import { EmptyState } from '../../components/EmptyState';
import { Loading } from '../../components/Loading';
import { RpIcon } from '../../components/RpIcon';
import { HAIRLINE, INK, MUTED } from '../../lib/colors';
import { echartsBase } from '../../lib/echartsTheme';
import { fmt, pct } from '../../lib/format';
import { defectColor, defectInfo, defectName, setDefects } from '../../lib/labels';
import './modes.css';

const LEGEND_GROUPS = ['pb', 'ab', 'si', 'nd', 'ti'] as const;

const HEIGHT_NORMAL = 'max(560px, 72vh)';
const HEIGHT_FULLSCREEN = 'calc(100vh - 24px)';

// --------------------------------------------------------------------------- //
// Tooltip HTML
// --------------------------------------------------------------------------- //

// ECharts tooltips are rendered as HTML, so every value taken from the API is
// escaped here. This is the app's XSS boundary for chart hovers: the text a
// user sees is unchanged, but markup inside a test-item name stays inert.
const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Only real web links become anchors; anything else degrades to plain text. */
function safeUrl(url: unknown): string | null {
  const s = String(url ?? '');
  return /^https?:\/\//i.test(s) ? s : null;
}

function labelText(locator?: string | null, group?: string | null): string {
  if (!locator) return defectName(null, group);
  const info = defectInfo(locator);
  return info && info.name ? `${info.name} (${locator})` : locator;
}

interface TipParam {
  data?: { meta?: ModePoint } | null;
}

function tip(param: unknown): string {
  const m = (param as TipParam)?.data?.meta;
  if (!m) return '';
  if (m.kind === 'mode') {
    return `<b>★ ${esc(m.name)}</b><br>label ${esc(labelText(m.label, m.label_group))} · status ${esc(m.status)}<br>purity ${esc(fmt(m.purity, 2))} · support ${esc(m.support)}`;
  }
  const url = safeUrl(m.ui_url);
  const id = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener" style="color:${INK}">item ${esc(m.item_id)} ↗</a>`
    : `item ${esc(m.item_id)}`;
  return `<b>${id}</b><br>${esc(m.name || '')}<br>label ${esc(labelText(m.label, m.label_group))}${m.is_auto_analyzed ? ' · ⭑ auto' : ''}`;
}

// --------------------------------------------------------------------------- //
// Chart options
// --------------------------------------------------------------------------- //

type Coord = Array<number | null>;

interface GroupedPoints {
  modes: ModePoint[];
  coord: (p: ModePoint) => Coord;
  byGroup: Array<[string, ModePoint[]]>;
}

function seriesFor(points: ModePoint[], dims: number): GroupedPoints {
  const items = points.filter((p) => p.kind === 'item');
  const modes = points.filter((p) => p.kind === 'mode');
  const coord = (p: ModePoint): Coord => (dims >= 3 ? [p.x, p.y, p.z ?? null] : [p.x, p.y]);
  const byGroup = new Map<string, ModePoint[]>();
  for (const p of items) {
    const g = String(p.label_group);
    const bucket = byGroup.get(g);
    if (bucket) bucket.push(p);
    else byGroup.set(g, [p]);
  }
  return { modes, coord, byGroup: Array.from(byGroup.entries()) };
}

function tooltipOption() {
  return { ...echartsBase().tooltip, enterable: true, formatter: tip };
}

function axis3d(name: string) {
  return {
    name,
    nameTextStyle: { color: MUTED },
    axisLabel: { color: MUTED },
    axisLine: { lineStyle: { color: HAIRLINE } },
  };
}

function option3d(points: ModePoint[]) {
  const { modes, coord, byGroup } = seriesFor(points, 3);
  const series: object[] = byGroup.map(([g, pts]) => ({
    type: 'scatter3D',
    name: defectName(null, g),
    data: pts.map((p) => ({ value: coord(p), meta: p })),
    symbolSize: 9,
    itemStyle: { color: defectColor(null, g), opacity: 0.9 },
  }));
  if (modes.length) {
    series.push({
      type: 'scatter3D',
      name: 'centroid',
      data: modes.map((p) => ({
        value: coord(p),
        meta: p,
        itemStyle: { color: defectColor(p.label, p.label_group) },
      })),
      symbol: 'diamond',
      symbolSize: 20,
      itemStyle: { opacity: 1, borderColor: '#fff', borderWidth: 1 },
    });
  }
  return {
    tooltip: tooltipOption(),
    xAxis3D: axis3d('PC1'),
    yAxis3D: axis3d('PC2'),
    zAxis3D: axis3d('PC3'),
    grid3D: {
      viewControl: { autoRotate: true, autoRotateSpeed: 6, distance: 190 },
      axisLine: { lineStyle: { color: HAIRLINE } },
      splitLine: { lineStyle: { color: HAIRLINE } },
      environment: '#ffffff',
    },
    series,
  };
}

function axis2d(name: string) {
  return {
    name,
    nameTextStyle: { color: MUTED },
    axisLabel: { color: MUTED },
    splitLine: { lineStyle: { color: HAIRLINE } },
  };
}

function option2d(points: ModePoint[]) {
  const { modes, coord, byGroup } = seriesFor(points, 2);
  const series: object[] = byGroup.map(([g, pts]) => ({
    type: 'scatter',
    name: defectName(null, g),
    data: pts.map((p) => ({ value: coord(p), meta: p })),
    symbolSize: 14,
    itemStyle: { color: defectColor(null, g), opacity: 0.9, borderColor: '#ffffff', borderWidth: 1 },
  }));
  if (modes.length) {
    series.push({
      type: 'scatter',
      name: 'centroid',
      data: modes.map((p) => ({
        value: coord(p),
        meta: p,
        itemStyle: { color: defectColor(p.label, p.label_group) },
      })),
      symbol: 'diamond',
      symbolSize: 24,
      itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
    });
  }
  return {
    tooltip: tooltipOption(),
    grid: { left: 30, right: 20, top: 20, bottom: 30, containLabel: true },
    xAxis: axis2d('PC1'),
    yAxis: axis2d('PC2'),
    series,
  };
}

// --------------------------------------------------------------------------- //
// View
// --------------------------------------------------------------------------- //

const DEFAULT_SUB = 'PCA of 384‑dim embeddings · items ● / centroids ★';

function statsSub(d: Modes3dResponse): string {
  const evr = d.explained_variance_ratio || [];
  return `${d.n_items} items · ${d.n_modes} centroids · ${d.dimensions}D · variance ${evr
    .map((v) => pct(v, 0))
    .join(' / ')}`;
}

export default function Modes() {
  const { project, refreshTick } = useApp();
  const [data, setData] = useState<Modes3dResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const wrapRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setData(null);
    setError(null);
    void api
      .modes3d(project)
      .then((d) => {
        if (cancelled) return;
        if (d.rp) setDefects(d.rp.defects);
        setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      });
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  // The Fullscreen API elevates the wrapper; the chart follows via the
  // ResizeObserver inside <EChart>, so only the height needs switching here.
  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === wrapRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen?.();
      return;
    }
    const pending = wrapRef.current?.requestFullscreen?.();
    if (pending) void pending.catch(() => {});
  }, []);

  const dims = data?.dimensions ?? 0;
  const option = useMemo(() => {
    if (!data || !data.available) return null;
    const points = data.points || [];
    return (data.dimensions ?? 0) >= 3 ? option3d(points) : option2d(points);
  }, [data]);

  if (error) throw error;

  const sub = data && data.available ? statsSub(data) : DEFAULT_SUB;

  return (
    <Card title="Modes Map" sub={sub} className="modes-card">
      {!data ? <Loading text="Projecting embeddings…" /> : null}

      {data && !data.available ? (
        <>
          <EmptyState
            icon="🪐"
            title="Embedding space not available yet"
            body={data.reason}
            tag="analyzer.failure_signature.emb (halfvec 384)"
          />
          <div className="flex gap-8 wrap modes-diag">
            <span className="chip">signatures {data.diagnostics?.total_signatures ?? 0}</span>
            <span className="chip">embedded {data.diagnostics?.embedded_signatures ?? 0}</span>
            <span className="chip">mode centroids {data.diagnostics?.modes_with_centroid ?? 0}</span>
          </div>
        </>
      ) : null}

      {data && data.available && option ? (
        <>
          <div className="flex gap-8 wrap mb-8 center">
            {LEGEND_GROUPS.map((g) => (
              <span key={g} className="flex center gap-8 modes-legend-item">
                <span
                  className="modes-legend-dot"
                  style={{ background: defectColor(null, g) }}
                />
                {defectName(null, g)}
              </span>
            ))}
            <span className="muted modes-legend-key">★ = mode centroid</span>
            <Button
              variant="ghost"
              className="modes-fs-btn"
              title="Toggle fullscreen"
              icon={<RpIcon name="maximize" size={12} />}
              onClick={toggleFullscreen}
            >
              fullscreen
            </Button>
          </div>

          <div className="modes-fswrap" ref={wrapRef}>
            <EChart
              key={dims >= 3 ? '3d' : '2d'}
              option={option}
              useGl={dims >= 3}
              height={fullscreen ? HEIGHT_FULLSCREEN : HEIGHT_NORMAL}
            />
          </div>

          {dims < 3 ? (
            <p className="note modes-note">
              Only {dims} principal dimension(s) carry variance (the embedded set is
              small/degenerate), so the map falls back to 2D — it becomes 3D once more distinct
              embeddings exist.
            </p>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
