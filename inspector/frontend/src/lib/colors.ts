// Resolved hex colors. ECharts' canvas renderer cannot read CSS custom
// properties, so every chart color is resolved once here from the stylesheet
// with a hard fallback to the token value in styles/app.css. jsdom returns an
// empty string for custom properties, which makes the fallbacks the values used
// in tests — deterministic either way.

function cssVar(name: string, fallback: string): string {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

export const INK = cssVar('--ink', '#3f3f3f');
export const INK2 = cssVar('--ink-2', '#464547');
export const MUTED = cssVar('--muted', '#8d95a1');
export const HAIRLINE = cssVar('--hairline', '#e3e7ec');
export const SURFACE = cssVar('--surface-1', '#ffffff');
export const ACCENT = cssVar('--accent', '#00829b');
export const WARNING = cssVar('--warning', '#d78706');
export const GOOD = cssVar('--good', '#3aa76d');
export const SERIOUS = cssVar('--serious', '#dc5959');

/** Decision-band semantics (auto / suggest / abstain). */
export const BAND: Record<'auto' | 'suggest' | 'abstain', string> = {
  auto: cssVar('--band-auto', '#3aa76d'),
  suggest: cssVar('--band-suggest', '#d4a002'),
  abstain: cssVar('--band-abstain', '#a2aab5'),
};

/** Defect-group dot colors — RP group fallbacks; issue_type.hex_color wins per label. */
export const LABEL_COLORS: Record<string, string> = {
  pb: cssVar('--lbl-pb', '#d32f2f'),
  ab: cssVar('--lbl-ab', '#ffc208'),
  si: cssVar('--lbl-si', '#3e7be6'),
  nd: cssVar('--lbl-nd', '#76839b'),
  ti: cssVar('--lbl-ti', '#00829b'),
  none: cssVar('--lbl-none', '#8d95a1'),
  other: cssVar('--lbl-none', '#8d95a1'),
};

export function labelColor(group?: string | null): string {
  return (group && LABEL_COLORS[group]) || LABEL_COLORS.none;
}

/** Feature-vector group colors (Decision card evidence rows). */
export const FEATURE_GROUP_COLORS: Record<string, string> = {
  retrieval: ACCENT,
  history: LABEL_COLORS.ab,
  kb: LABEL_COLORS.si,
  grouping: WARNING,
  signal: LABEL_COLORS.nd,
  discriminant: LABEL_COLORS.pb,
  llm: SERIOUS,
  other: MUTED,
};

export function featureGroupColor(group?: string | null): string {
  return (group && FEATURE_GROUP_COLORS[group]) || FEATURE_GROUP_COLORS.other;
}
