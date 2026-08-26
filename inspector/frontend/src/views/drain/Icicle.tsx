// Token-prefix icicle — the ECharts replacement for the old d3 drawing.
//
// The original built a d3 hierarchy over the first four masked tokens of every
// pattern, ran d3.partition() transposed (value spreads down the screen, depth
// runs across it) and painted plain rects with native <title> tooltips. All of
// that is small, exact arithmetic, so it is ported here in plain TS and handed
// to a single ECharts `custom` series. No d3.

import { useMemo } from 'react';

import type { Template } from '../../app/types';
import { EChart } from '../../components';
import { INK } from '../../lib/colors';
import { echartsBase } from '../../lib/echartsTheme';

/** Fixed tree depth: Drain's own tree keys on the leading tokens too. */
const MAX_TOKENS = 4;

/** Chart height, from the original `const height = 460`. */
export const ICICLE_HEIGHT = 460;

/** Cells shorter than this get no label — the text would not fit. */
const LABEL_MIN_HEIGHT = 14;

/** Rough monospace advance at 10px, used to size the truncation. */
const CHAR_WIDTH = 7;

// --------------------------------------------------------------------------- //
// Colors — d3.interpolateBlues without d3
// --------------------------------------------------------------------------- //

// ColorBrewer Blues, 9 classes. d3's sequential Blues ramp is a uniform cubic
// B-spline through exactly these stops, so reproducing the spline reproduces
// the original colors.
const BLUES = [
  '#f7fbff',
  '#deebf7',
  '#c6dbef',
  '#9ecae1',
  '#6baed6',
  '#4292c6',
  '#2171b5',
  '#08519c',
  '#08306b',
];

function basis(t1: number, v0: number, v1: number, v2: number, v3: number): number {
  const t2 = t1 * t1;
  const t3 = t2 * t1;
  return (
    ((1 - 3 * t1 + 3 * t2 - t3) * v0 +
      (4 - 6 * t2 + 3 * t3) * v1 +
      (1 + 3 * t1 + 3 * t2 - 3 * t3) * v2 +
      t3 * v3) /
    6
  );
}

function spline(values: number[]): (t: number) => number {
  const n = values.length - 1;
  return (input: number) => {
    let t = input;
    let i: number;
    if (t <= 0) {
      t = 0;
      i = 0;
    } else if (t >= 1) {
      t = 1;
      i = n - 1;
    } else {
      i = Math.floor(t * n);
    }
    const v1 = values[i];
    const v2 = values[i + 1];
    const v0 = i > 0 ? values[i - 1] : 2 * v1 - v2;
    const v3 = i < n - 1 ? values[i + 2] : 2 * v2 - v1;
    return basis((t - i / n) * n, v0, v1, v2, v3);
  };
}

function channel(hex: string, offset: number): number {
  return parseInt(hex.slice(1 + offset * 2, 3 + offset * 2), 16);
}

const RAMP = [0, 1, 2].map((c) => spline(BLUES.map((hex) => channel(hex, c))));

function clamp255(v: number): number {
  return Math.max(0, Math.min(255, Math.round(v)));
}

function interpolateBlues(t: number): string {
  return `rgb(${clamp255(RAMP[0](t))}, ${clamp255(RAMP[1](t))}, ${clamp255(RAMP[2](t))})`;
}

/**
 * Depth shade. The original was
 * `d3.scaleSequential([0, 4], t => d3.interpolateBlues(0.35 + t * 0.14))`
 * called with the node depth, and a sequential scale normalises its input over
 * the domain first — so the value reaching the interpolator is depth / 4.
 */
export function depthColor(depth: number): string {
  return interpolateBlues(0.35 + (depth / MAX_TOKENS) * 0.14);
}

// --------------------------------------------------------------------------- //
// Prefix tree + partition layout
// --------------------------------------------------------------------------- //

interface TreeNode {
  name: string;
  /** Raw match_count total, used only for leaves. */
  count: number;
  children: Map<string, TreeNode>;
}

interface SumNode {
  name: string;
  value: number;
  depth: number;
  path: string;
  children: SumNode[];
}

/** A laid-out cell. All coordinates are fractions of the plot box. */
export interface IcicleCell {
  name: string;
  value: number;
  depth: number;
  path: string;
  /** Value axis (vertical on screen). */
  x0: number;
  x1: number;
  /** Depth axis (horizontal on screen). */
  y0: number;
  y1: number;
}

function buildTree(templates: Template[]): TreeNode {
  const root: TreeNode = { name: 'root', count: 0, children: new Map() };
  for (const t of templates) {
    const tokens = String(t.pattern || '')
      .split(/\s+/)
      .slice(0, MAX_TOKENS);
    const add = t.match_count || 1;
    root.count += add;
    let node = root;
    for (const token of tokens) {
      let child = node.children.get(token);
      if (!child) {
        child = { name: token, count: 0, children: new Map() };
        node.children.set(token, child);
      }
      child.count += add;
      node = child;
    }
  }
  return root;
}

/**
 * d3's `.sum(d => (d.children && d.children.length ? 0 : d.value) || 1)` then
 * `.sort((a, b) => b.value - a.value)`. Note the `|| 1`: it also turns the 0 an
 * internal node contributes into 1, so every branch node adds one to its own
 * subtree total. That quirk shifts the drawn proportions, so it is kept.
 */
function summarise(node: TreeNode, depth: number, prefix: string): SumNode {
  const path = depth === 0 ? '' : prefix ? `${prefix} ${node.name}` : node.name;
  const kids = [...node.children.values()].map((c) => summarise(c, depth + 1, path));
  const own = kids.length ? 1 : node.count || 1;
  const value = kids.reduce((acc, k) => acc + k.value, own);
  kids.sort((a, b) => b.value - a.value);
  return { name: node.name, value, depth, path, children: kids };
}

function treeHeight(node: SumNode): number {
  return node.children.length ? 1 + Math.max(...node.children.map(treeHeight)) : 0;
}

/**
 * d3.partition().size([height, width]) in fractions of the box: children are
 * diced across the parent's value extent, and each depth gets an equal band of
 * the depth axis. The root cell itself is never drawn.
 */
export function layout(root: SumNode): IcicleCell[] {
  const bands = treeHeight(root) + 1;
  const cells: IcicleCell[] = [];

  const walk = (node: SumNode, x0: number, x1: number): void => {
    if (node.depth > 0) {
      cells.push({
        name: node.name,
        value: node.value,
        depth: node.depth,
        path: node.path,
        x0,
        x1,
        y0: node.depth / bands,
        y1: (node.depth + 1) / bands,
      });
    }
    if (!node.children.length) return;
    const k = node.value ? (x1 - x0) / node.value : 0;
    let cursor = x0;
    for (const child of node.children) {
      const next = cursor + child.value * k;
      walk(child, cursor, next);
      cursor = next;
    }
  };

  walk(root, 0, 1);
  return cells;
}

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //

function truncate(s: string, n: number): string {
  const text = String(s);
  return text.length > n ? `${text.slice(0, Math.max(1, n - 1))}…` : text;
}

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** The slice of the ECharts custom-series render API this chart uses. */
interface RenderApi {
  getWidth(): number;
  getHeight(): number;
}

interface RenderParams {
  dataIndex: number;
}

export interface IcicleProps {
  templates: Template[];
}

export function Icicle({ templates }: IcicleProps): JSX.Element {
  const cells = useMemo(() => layout(summarise(buildTree(templates), 0, '')), [templates]);

  const option = useMemo(() => {
    const base = echartsBase();
    const renderItem = (params: RenderParams, api: RenderApi) => {
      const cell = cells[params.dataIndex];
      if (!cell) return null;
      const boxWidth = api.getWidth();
      const boxHeight = api.getHeight();
      const x = cell.y0 * boxWidth;
      const y = cell.x0 * boxHeight;
      const cellWidth = (cell.y1 - cell.y0) * boxWidth;
      const cellHeight = (cell.x1 - cell.x0) * boxHeight;

      const children: object[] = [
        {
          type: 'rect',
          shape: {
            x,
            y,
            width: Math.max(0, cellWidth - 1),
            height: Math.max(0, cellHeight - 1),
            r: 3,
          },
          style: { fill: depthColor(cell.depth) },
        },
      ];
      if (cellHeight > LABEL_MIN_HEIGHT) {
        children.push({
          type: 'text',
          silent: true,
          style: {
            x: x + 6,
            y: y + cellHeight / 2,
            text: truncate(cell.name, Math.floor(cellWidth / CHAR_WIDTH)),
            fill: INK,
            fontSize: 10,
            fontFamily: 'ui-monospace, monospace',
            verticalAlign: 'middle',
          },
        });
      }
      return { type: 'group', children };
    };

    return {
      tooltip: {
        ...base.tooltip,
        trigger: 'item',
        formatter: (params: { data: IcicleCell }) =>
          `<div class="mono">${esc(params.data.path)}</div>${params.data.value} matches`,
      },
      grid: { left: 0, right: 0, top: 0, bottom: 0 },
      series: [
        {
          type: 'custom',
          coordinateSystem: 'none',
          renderItem,
          data: cells,
        },
      ],
    };
  }, [cells]);

  if (!cells.length) return <div className="muted">No tokens.</div>;

  return <EChart option={option} height={ICICLE_HEIGHT} className="drain-icicle" />;
}
