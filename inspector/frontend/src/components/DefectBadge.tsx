import { DEFECT_GROUPS, defectColor, defectInfo, defectName, labelGroup, labelName } from '../lib/labels';

export interface DefectBadgeProps {
  locator?: string | null;
  group?: string | null;
  /** Compact variant for dense lists: RP abbreviation + 10px dot. */
  abbr?: boolean;
}

const PILL_STYLE = {
  background: '#fff',
  color: 'var(--rp-almost-black)',
  borderColor: 'var(--rp-e-200)',
  fontWeight: 600,
} as const;

/**
 * RP defect pill (DESIGN-PLATFORM §4): white fill, 1px e-200 border, radius
 * 100px, a 12px color DOT filled from issue_type.hex_color, name in neutral
 * ink. Color is carried by the dot — never the fill or the text. The raw
 * locator is not shown inline (it lives in Technical Details); it stays in the
 * tooltip.
 */
export function DefectBadge({ locator, group, abbr }: DefectBadgeProps) {
  const g = group || labelGroup(locator);
  const info = defectInfo(locator);
  const col = defectColor(locator, g);

  if (abbr) {
    const text =
      (info && (info.short_name || info.name)) ||
      (DEFECT_GROUPS.includes(g) ? g.toUpperCase() : labelName(g));
    const title = info
      ? [info.name, locator].filter(Boolean).join(' · ')
      : DEFECT_GROUPS.includes(g)
        ? `${labelName(g)} group`
        : '';
    return (
      <span className="badge defect" title={title || undefined} style={PILL_STYLE}>
        <span className="dot" style={{ background: col, width: '10px', height: '10px' }} />
        {text}
      </span>
    );
  }

  // A bare group code (matching/decision labels like 'pb') is not a defect type
  // at all: no defect pill. Render a plain chip like its neighbor chips so the
  // group-level label reads as metadata, not as an applied defect.
  if (!info && (!locator || DEFECT_GROUPS.includes(locator)) && DEFECT_GROUPS.includes(g)) {
    return (
      <span className="chip" title={`Defect group (${g.toUpperCase()}), not a specific defect type`}>
        {`${labelName(g)} group`}
      </span>
    );
  }

  const title = info
    ? [info.name, locator].filter(Boolean).join(' · ')
    : DEFECT_GROUPS.includes(g)
      ? `Defect group (${g.toUpperCase()}), not a specific defect type`
      : locator || undefined;

  return (
    <span className="badge defect" title={title || undefined} style={PILL_STYLE}>
      <span className="dot" style={{ background: col, width: '12px', height: '12px' }} />
      {defectName(locator, g)}
    </span>
  );
}
