// Value formatters shared by every view. Missing values always render as an
// em-dash placeholder — never as 0, never as an invented string.

const DASH = '—';

/** Integers localized, floats fixed to `digits`, anything missing → '—'. */
export function fmt(n: number | string | null | undefined, digits = 3): string {
  if (n == null || (typeof n === 'number' && Number.isNaN(n))) return DASH;
  if (typeof n !== 'number') return String(n);
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toFixed(digits);
}

/** Ratio → percentage string, e.g. pct(0.123) === '12.3%'. */
export function pct(n: number | null | undefined, digits = 1): string {
  return n == null ? DASH : `${(n * 100).toFixed(digits)}%`;
}

/** ISO timestamp → locale short form 'Mon D, HH:MM'. Unparsable input passes through. */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Relative time: 'just now' / '{m}m ago' / '{h}h ago' / '{d}d ago', falling back
 * to shortTime beyond 30 days. The full ISO always rides `title=` elsewhere —
 * this is a convenience layer only.
 */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return shortTime(iso);
}
