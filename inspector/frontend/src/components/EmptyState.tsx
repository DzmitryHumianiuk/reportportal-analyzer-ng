import type { ReactNode } from 'react';

import { RP_ICONS, RpIcon } from './RpIcon';

/**
 * Legacy emoji → RP icon names, so callers that still pass an emoji render a
 * library icon (no-emoji rule) without touching each view. Unmapped emoji
 * render literally, exactly as before (🕳️ 🧩 🧬 🤖).
 */
export const EMOJI_ICON: Record<string, string> = {
  '🚫': 'error',
  '🩺': 'warning',
  '🔌': 'warning',
  '🧭': 'search',
  '🔍': 'search',
  '📭': 'launchType',
  '🪐': 'diamond',
  '🕸️': 'tree',
  '🌳': 'tree',
  '🌵': 'details',
  '🔤': 'details',
  '📈': 'latestExecutions',
  '📊': 'latestExecutions',
  '🧱': 'jar',
  '🗄️': 'moveToFolder',
  '🎯': 'checkmark',
  '🧠': 'info',
  '📄': 'fileOther',
};

export interface EmptyStateProps {
  /** Emoji (mapped through EMOJI_ICON) or an RP icon name. */
  icon: string;
  title: string;
  body?: ReactNode;
  /** Mono tag naming the data source behind the empty result. */
  tag?: string;
}

export function EmptyState({ icon, title, body, tag }: EmptyStateProps) {
  const name = EMOJI_ICON[icon] || (RP_ICONS[icon] ? icon : null);
  return (
    <div className="empty">
      <div className="icon">{name ? <RpIcon name={name} size={28} /> : icon}</div>
      <h4>{title}</h4>
      <p>{body}</p>
      {tag ? <span className="stage-tag">{tag}</span> : null}
    </div>
  );
}
