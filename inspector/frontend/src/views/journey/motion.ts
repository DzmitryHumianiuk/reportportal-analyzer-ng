// Attention motion, suppressed for readers who asked for less of it.

export function prefersReducedMotion(): boolean {
  return !!(
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/** One 900 ms accent ring on an element (no-op under reduced motion). */
export function pulse(el: Element | null | undefined, color = 'var(--accent)'): void {
  if (!el || prefersReducedMotion()) return;
  const host = el as HTMLElement;
  if (typeof host.animate !== 'function') return; // jsdom / older engines
  host.animate([{ boxShadow: `0 0 0 2px ${color}` }, { boxShadow: 'var(--shadow)' }], {
    duration: 900,
  });
}

/** Scroll a card into view and pulse it — the Feedback → Decision cross-link. */
export function scrollPulse(id: string, block: ScrollLogicalPosition = 'center'): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block });
  pulse(el);
}
