import type { ViewName } from '../app/hash';

/** Tab order and labels are the parity spec — do not reorder or reword. */
export const TABS: ReadonlyArray<{ view: ViewName; label: string }> = [
  { view: 'journey', label: 'Item Journey' },
  { view: 'signatures', label: 'Signatures' },
  { view: 'drain', label: 'Drain3 Explorer' },
  { view: 'modes', label: 'Modes Map 3D' },
  { view: 'groups', label: 'Launch Groups' },
  { view: 'loop', label: 'Learning Loop' },
  { view: 'llm', label: 'LLM' },
  { view: 'rubric', label: 'Cold-start Rules' },
];

export interface TabsNavProps {
  view: ViewName;
  onChange: (v: ViewName) => void;
}

export function TabsNav({ view, onChange }: TabsNavProps) {
  return (
    <nav className="tabs" id="tabs" role="tablist">
      {TABS.map((t) => (
        <button
          key={t.view}
          type="button"
          className={`tab${t.view === view ? ' active' : ''}`}
          data-view={t.view}
          role="tab"
          aria-selected={t.view === view}
          onClick={() => onChange(t.view)}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}
