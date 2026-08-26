import { labelName } from '../lib/labels';

export interface LabelBadgeProps {
  group: string;
  text: string;
}

/** Group-colored pill (`.badge.lbl.<group>`) with the dot carrying the color. */
export function LabelBadge({ group, text }: LabelBadgeProps) {
  const g = group || 'none';
  return (
    <span className={`badge lbl ${g}`}>
      <span className="dot" />
      {text || labelName(g)}
    </span>
  );
}
