export interface MicrobarProps {
  /** 0..1 fill share; values outside the range are clamped. */
  ratio: number;
  color?: string;
  /** Track width in px. Omitted → the CSS min-width (60px) plus flex context. */
  width?: number;
  title?: string;
}

/** Thin inline bar used inside table cells and evidence rows. */
export function Microbar({ ratio, color, width, title }: MicrobarProps) {
  const filled = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  return (
    <div className="microbar" style={width != null ? { width: `${width}px` } : undefined} title={title}>
      <i style={{ width: `${filled * 100}%`, background: color }} />
    </div>
  );
}
