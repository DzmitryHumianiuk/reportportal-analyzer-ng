import type { ReactNode } from 'react';

export interface ChipLinkProps {
  label: ReactNode;
  /** Real ReportPortal deep link. Absent → plain span, never a dead link. */
  url?: string | null;
  /** Class of the surrounding chip/text; the link only adds an affordance. */
  className?: string;
}

/**
 * An id rendered as a ReportPortal deep link when a real URL exists, and as the
 * plain chip it would otherwise be. Keeps the no-dummy-data rule: no link when
 * there is no URL.
 */
export function ChipLink({ label, url, className = 'chip' }: ChipLinkProps) {
  if (!url) {
    return className ? <span className={className}>{label}</span> : <span>{label}</span>;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener"
      className={`${className ? `${className} ` : ''}idlink`}
      title="Open in ReportPortal ↗"
    >
      {label}
    </a>
  );
}
