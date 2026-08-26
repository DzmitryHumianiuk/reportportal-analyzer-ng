// Banded confidence gauge — the hand-rolled SVG from journey.js drawGauge(),
// ported to React. Same geometry: a 220° arc from 200° to −20°, three band arcs
// whose split points come from the payload's tau_suggest / tau_auto, an ink
// needle, the confidence read-out, and HTML threshold chips positioned on the
// arc. No chart library: this is pure geometry.

import { ACCENT, BAND, INK, MUTED } from '../../lib/colors';

const W = 260;
const H = 150;
const CX = 130;
const CY = 118;
const R = 92;
const SW = 14;

function gaugeAngle(v: number): number {
  return 200 - 220 * v;
}

function gaugePolar(cx: number, cy: number, r: number, deg: number): { x: number; y: number } {
  const a = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) };
}

/** Polyline approximation of the band arc (~90 segments per unit of value). */
function gaugeArc(cx: number, cy: number, r: number, v0: number, v1: number): string {
  const steps = Math.max(2, Math.round((v1 - v0) * 90));
  let d = '';
  for (let i = 0; i <= steps; i++) {
    const v = v0 + ((v1 - v0) * i) / steps;
    const p = gaugePolar(cx, cy, r, gaugeAngle(v));
    d += `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)} ${p.y.toFixed(2)} `;
  }
  return d;
}

const clamp = (n: number) => Math.max(0, Math.min(1, n));

export interface GaugeMarker {
  value?: number | null;
  label?: string;
}

export interface GaugeProps {
  confidence?: number | null;
  tauSuggest?: number | null;
  tauAuto?: number | null;
  /** Dashed radial tick for a proposal that is NOT the needle (LLM cold-start). */
  marker?: GaugeMarker | null;
}

export function Gauge({ confidence, tauSuggest, tauAuto, marker }: GaugeProps): JSX.Element {
  const ts = tauSuggest ?? 0;
  const ta = tauAuto ?? 1;
  const conf = clamp(confidence ?? 0);
  const bands: Array<[number, number, string]> = [
    [0, ts, BAND.abstain],
    [ts, ta, BAND.suggest],
    [ta, 1, BAND.auto],
  ];
  const needle = gaugePolar(CX, CY, R - 4, gaugeAngle(conf));

  let tick: JSX.Element | null = null;
  if (marker && marker.value != null) {
    const mv = clamp(marker.value);
    const a = gaugeAngle(mv);
    const p1 = gaugePolar(CX, CY, R - SW / 2 - 6, a);
    const p2 = gaugePolar(CX, CY, R + SW / 2 + 4, a);
    const lp = gaugePolar(CX, CY, R + SW / 2 + 16, a);
    tick = (
      <>
        <line
          x1={p1.x.toFixed(2)}
          y1={p1.y.toFixed(2)}
          x2={p2.x.toFixed(2)}
          y2={p2.y.toFixed(2)}
          stroke={ACCENT}
          strokeWidth={2}
          strokeDasharray="3 2"
        />
        <text
          x={lp.x.toFixed(2)}
          y={lp.y.toFixed(2)}
          textAnchor="middle"
          fill={ACCENT}
          fontSize="9"
        >
          {marker.label || 'LLM'}
        </text>
      </>
    );
  }

  // Keyed by role, not by value: a project can set tau_suggest === tau_auto, and
  // a value key would then be a duplicate and drop one of the two chips.
  const thresholds: Array<[string, number]> = [];
  if (tauSuggest != null) thresholds.push(['suggest', tauSuggest]);
  if (tauAuto != null) thresholds.push(['auto', tauAuto]);

  return (
    <div className="gauge-wrap">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        {/* Same reason as the chips below: with tau_suggest === tau_auto === 0
            two bands share the same start and end, so the key is the slot. */}
        {bands.map(([a, b, col], i) => (
          <path
            key={i}
            d={gaugeArc(CX, CY, R, a, b)}
            fill="none"
            stroke={col}
            strokeWidth={SW}
            strokeLinecap="butt"
          />
        ))}
        {tick}
        <line
          x1={CX}
          y1={CY}
          x2={needle.x.toFixed(2)}
          y2={needle.y.toFixed(2)}
          stroke={INK}
          strokeWidth={3}
          strokeLinecap="round"
        />
        <circle cx={CX} cy={CY} r={5} fill={INK} />
        <text x={CX} y={CY - 26} textAnchor="middle" fill={INK} fontSize="26" fontWeight="700">
          {(confidence ?? 0).toFixed(2)}
        </text>
        <text x={CX} y={CY + 22} textAnchor="middle" fill={MUTED} fontSize="10">
          confidence
        </text>
      </svg>
      {thresholds.map(([role, t]) => {
        const p = gaugePolar(CX, CY, R + 15, gaugeAngle(t));
        return (
          <span
            key={role}
            className="thresh-chip mono"
            style={{ left: `${p.x}px`, top: `${p.y}px` }}
          >
            {t.toFixed(2).replace(/^0/, '')}
          </span>
        );
      })}
    </div>
  );
}
