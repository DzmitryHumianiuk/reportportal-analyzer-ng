import type { ReactNode } from 'react';

export interface SiMeterProps {
  label: string;
  /** Right-hand read-out next to the label. */
  value: ReactNode;
  /** 0..1 fill share; clamped. */
  ratio: number;
  color?: string;
  /** 0..1 position of the midpoint tick. Omitted → no tick. */
  tickRatio?: number;
  /**
   * Left / middle / right scale captions under the track. Nodes, not plain
   * text: the journey meter styles its middle caption (bold mono read-out).
   */
  scale?: [ReactNode, ReactNode, ReactNode];
  note?: ReactNode;
}

const clamp = (n: number) => Math.max(0, Math.min(1, Number.isFinite(n) ? n : 0));

/** Banded track meter (si_prior, training-frame position, …). */
export function SiMeter({ label, value, ratio, color, tickRatio, scale, note }: SiMeterProps) {
  return (
    <div className="si-meter">
      <div className="si-label flex between">
        <span>{label}</span>
        <span className="si-val" style={{ textTransform: 'none', letterSpacing: 0 }}>
          {value}
        </span>
      </div>
      <div className="si-track">
        <div className="si-fill" style={{ width: `${clamp(ratio) * 100}%`, background: color }} />
        {tickRatio != null ? (
          <div className="si-tick" style={{ left: `${clamp(tickRatio) * 100}%` }} />
        ) : null}
      </div>
      {scale ? (
        <div className="si-scale">
          <span>{scale[0]}</span>
          <span>{scale[1]}</span>
          <span>{scale[2]}</span>
        </div>
      ) : null}
      {note ? <p className="note si-meter-note">{note}</p> : null}
    </div>
  );
}
