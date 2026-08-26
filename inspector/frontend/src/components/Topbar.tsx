import { Dropdown, FieldLabel, Toggle } from '@reportportal/ui-kit';

import { useApp } from '../app/state';

/** RP "diamond" brand mark, 22×22, tinted with the topaz token. */
function BrandMark() {
  return (
    <span className="brand-mark" style={{ color: 'var(--rp-topaz)' }}>
      <svg width="22" height="22" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M7.99994 1.46161L1.41877 8.0003L7.99994 14.539L14.5811 8.0003L7.99994 1.46161ZM8.28187 0.332062C8.12587 0.177072 7.87401 0.177073 7.71801 0.332063L0.285555 7.71654C0.128122 7.87296 0.128122 8.12764 0.285555 8.28405L7.71801 15.6685C7.87401 15.8235 8.12586 15.8235 8.28186 15.6685L15.7144 8.28405C15.8718 8.12764 15.8718 7.87295 15.7144 7.71654L8.28187 0.332062Z"
          fill="currentColor"
        />
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M8.00786 4.98127L4.9132 8.01519L8.00783 11.1798L11.0896 8.01517L8.00786 4.98127ZM8.28936 3.85512C8.1339 3.70208 7.88449 3.70182 7.72871 3.85454L3.78523 7.72061C3.62762 7.87513 3.62495 8.1281 3.77927 8.28591L7.72274 12.3186C7.87987 12.4793 8.1385 12.479 8.29529 12.318L12.2224 8.28532C12.3759 8.12766 12.3733 7.87559 12.2165 7.7212L8.28936 3.85512Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

export function Topbar() {
  const { projects, project, rpStatus, counters, autorefresh, setAutorefresh, setProject } = useApp();

  const statusClass = rpStatus.reachable ? ' ok' : rpStatus.configured ? ' warn' : '';

  const options = projects.map((p) => ({
    value: p.project_id,
    // Real RP name when resolved; honest "project #<id>" fallback otherwise
    // (the # marks the raw id — never a fabricated "Project N" name).
    label: `${p.project_name || `project #${p.project_id}`} · ${p.item_count} items · ${p.launch_count} launches`,
  }));

  const cells: Array<[string, number, string]> = counters
    ? [
        ['accent', counters.suggestions, 'suggest'],
        ['', counters.abstains, 'abstain'],
        ['good', counters.accepted, 'accepted'],
        ['warn', counters.corrected, 'corrected'],
      ]
    : [];

  return (
    <header className="topbar">
      <div className="brand">
        <BrandMark />
        <div className="brand-text">
          <span className="brand-title">
            {'analyzer‑ng '}
            <b>inspector</b>
          </span>
          <span
            className={`brand-sub${statusClass}`}
            id="rp-status"
            title="ReportPortal name resolution status"
          >
            {rpStatus.note || 'RP names: —'}
          </span>
        </div>
      </div>

      <div className="topbar-controls">
        <div className="field">
          <FieldLabel className="field-label">Project</FieldLabel>
          {/* Mounted only once a project is resolved: the kit's dropdown must
              not flip from an unselected to a selected state mid-life. */}
          {project != null ? (
            <Dropdown
              options={options}
              value={project}
              onChange={(v) => setProject(Number(v))}
              aria-label="Project"
            />
          ) : null}
        </div>

        <div className="counters" id="counters" aria-live="polite">
          {cells.map(([cls, n, k]) => (
            <div className={`counter ${cls}`} key={k}>
              <span className="n">{String(n)}</span>
              <span className="k">{k}</span>
            </div>
          ))}
        </div>

        <Toggle
          value={autorefresh}
          onChange={(e) => setAutorefresh(e.target.checked)}
          title="Poll every 10s"
        >
          {'Auto‑refresh'}
        </Toggle>
      </div>
    </header>
  );
}
