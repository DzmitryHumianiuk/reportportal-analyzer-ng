// Vitest setup — fills the three jsdom gaps the app relies on.
//
// jsdom ships no ResizeObserver (EChart mounts one), no matchMedia (the views
// check prefers-reduced-motion before playing pulses) and no scrollIntoView
// (stepper jumps, rubric deep links). Without these stubs those code paths
// throw in tests for reasons that have nothing to do with the code under test.

class ResizeObserverStub {
  observe(): void {
    /* no-op */
  }

  unobserve(): void {
    /* no-op */
  }

  disconnect(): void {
    /* no-op */
  }
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {
    /* no-op */
  },
  removeEventListener: () => {
    /* no-op */
  },
  addListener: () => {
    /* no-op */
  },
  removeListener: () => {
    /* no-op */
  },
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

Element.prototype.scrollIntoView = function scrollIntoView(): void {
  /* no-op */
};
