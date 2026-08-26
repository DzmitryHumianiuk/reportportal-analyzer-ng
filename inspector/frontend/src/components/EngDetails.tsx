import { useEffect, useRef, useState, type ReactNode } from 'react';

const PREFIX = 'inspector.eng.';

function readOpen(key: string): boolean {
  try {
    return localStorage.getItem(PREFIX + key) === '1';
  } catch {
    return false; // storage off (private mode, blocked cookies) — closed is fine
  }
}

function writeOpen(key: string, open: boolean): void {
  try {
    localStorage.setItem(PREFIX + key, open ? '1' : '0');
  } catch {
    /* storage off — the drawer still works, it just does not remember */
  }
}

export interface EngDetailsProps {
  /** localStorage suffix: the open state lives at `inspector.eng.<storageKey>`. */
  storageKey: string;
  /** Summary line. Default 'Technical Details'. */
  summary?: ReactNode;
  children: ReactNode;
}

/**
 * Shared engineer-details disclosure. Native <details>, closed by default, open
 * state persisted per key across items and launches so an engineer opens it
 * once and it stays open.
 */
export function EngDetails({ storageKey, summary = 'Technical Details', children }: EngDetailsProps) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(() => readOpen(storageKey));

  // A different key means a different drawer: re-read its remembered state.
  useEffect(() => {
    setOpen(readOpen(storageKey));
  }, [storageKey]);

  return (
    <details
      className="eng"
      ref={ref}
      open={open}
      onToggle={() => {
        const next = ref.current?.open ?? false;
        setOpen(next);
        writeOpen(storageKey, next);
      }}
    >
      <summary>
        {summary} <span className="eng-caret">▸</span>
      </summary>
      <div className="eng-body">{children}</div>
    </details>
  );
}
