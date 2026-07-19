// App shell: project selector, live counters, tab routing, auto-refresh.
// Shareable permalinks: the address-bar hash mirrors the current view + project
// (+ per-view selection). See applyHashState / serializeHash / updateHash.
import { api } from './api.js';
import { h, clear, toast, loading, emptyState, setDefects, readHashParams, writeHashParams } from './util.js';
import { renderJourney, setJourneyState } from './views/journey.js';
import { renderDrain } from './views/drain.js';
import { renderModes } from './views/modes.js';
import { renderGroups, setGroupsState } from './views/groups.js';
import { renderLoop } from './views/loop.js';
import { renderSignatures, setSignaturesState } from './views/signatures.js';

const VIEWS = {
  journey: renderJourney, drain: renderDrain, modes: renderModes,
  groups: renderGroups, loop: renderLoop, signatures: renderSignatures,
};

export const state = {
  project: null,
  view: 'journey',
  autorefresh: false,
  rp: null,
  _timer: null,
};

// Loaded project list (for id ↔ RP-name resolution on permalink parse).
let PROJECTS = [];
// Per-view selection mirrored into the hash. `launch`/`item` = journey;
// `glaunch` = groups' launch; `q`/`conflicts`/`hash` = signatures.
const linkState = { launch: null, item: null, glaunch: null, q: '', conflicts: false, hash: null };

const num = (v) => {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

// Resolve a `project` hash value (id or RP name) → project_id, or null.
function resolveProject(val) {
  const s = String(val);
  const byId = PROJECTS.find((p) => String(p.project_id) === s);
  if (byId) return byId.project_id;
  const low = s.toLowerCase();
  const byName = PROJECTS.find((p) => (p.project_name || '').toLowerCase() === low);
  return byName ? byName.project_id : null;
}

// Serialize only the params relevant to the active view (stale ones are pruned).
function serializeHash() {
  const p = { view: state.view, project: state.project };
  if (state.view === 'journey') { p.launch = linkState.launch; p.item = linkState.item; }
  else if (state.view === 'signatures') { p.q = linkState.q || null; p.conflicts = linkState.conflicts ? '1' : null; p.hash = linkState.hash; }
  else if (state.view === 'groups') { p.launch = linkState.glaunch; }
  writeHashParams(p);
}

// Views call this on navigation to reflect their selection in the URL.
export function updateHash(patch) {
  Object.assign(linkState, patch);
  serializeHash();
}

const viewEl = document.getElementById('view');
const tabsEl = document.getElementById('tabs');
const selectEl = document.getElementById('project-select');
const countersEl = document.getElementById('counters');
const rpStatusEl = document.getElementById('rp-status');

async function boot() {
  wireTabs();
  wireAutorefresh();
  try {
    const { projects } = await api.projects();
    PROJECTS = projects;
    if (!projects.length) {
      clear(viewEl).appendChild(emptyState('🗄️', 'No projects found',
        'The analyzer database has no project rows. Ingest at least one launch through the analyzer, then reload.',
        'analyzer.project'));
      return;
    }
    clear(selectEl);
    for (const p of projects) {
      // Real RP name when resolved; honest "project #<id>" fallback otherwise
      // (the # marks the raw id — never a fabricated "Project N" name).
      const name = p.project_name || `project #${p.project_id}`;
      selectEl.appendChild(h('option', { value: p.project_id },
        `${name} · ${p.item_count} items · ${p.launch_count} launches`));
    }
    selectEl.addEventListener('change', onProjectChange);
    // Restore everything from the permalink hash (or defaults when absent).
    await applyHashState(readHashParams(), { initial: true });
    // Browser back/forward + manual hash edits re-apply (replaceState never fires this).
    window.addEventListener('hashchange', () => { applyHashState(readHashParams(), {}); });
  } catch (e) {
    clear(viewEl).appendChild(emptyState('🚫', 'Cannot reach the inspector API', String(e.message || e), 'GET /api/projects'));
  }
}

// Restore project + view + per-view selection from parsed hash params, then mount.
// Graceful: unknown project/view → defaults; nonexistent launch/item ids fall
// back to first inside the view (honest empty state, never a crash).
async function applyHashState(hp, opts) {
  let projId = state.project ?? PROJECTS[0].project_id;
  if (hp.project) { const r = resolveProject(hp.project); if (r != null) projId = r; }
  const projectChanged = projId !== state.project;
  state.project = projId;
  selectEl.value = String(projId);

  const view = VIEWS[hp.view] ? hp.view : 'journey';
  linkState.item = num(hp.item);
  linkState.q = hp.q || '';
  linkState.conflicts = hp.conflicts === '1';
  linkState.hash = hp.hash || null;
  linkState.launch = view === 'groups' ? null : num(hp.launch);
  linkState.glaunch = view === 'groups' ? (hp.launch || null) : null;
  setJourneyState({ launch: linkState.launch, item: linkState.item });
  setSignaturesState({ q: linkState.q, conflicts: linkState.conflicts, expanded: linkState.hash });
  setGroupsState({ launch: linkState.glaunch });

  if (projectChanged || opts.initial) await loadRp();
  state.view = view;
  [...tabsEl.querySelectorAll('.tab')].forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  serializeHash();
  mount();
  if (projectChanged || opts.initial) await refreshCounters();
}

async function onProjectChange() {
  state.project = Number(selectEl.value);
  // id-specific selections do not carry across projects; free-text filter does.
  setJourneyState({ launch: null, item: null });
  setGroupsState({ launch: null });
  setSignaturesState({ q: linkState.q, conflicts: linkState.conflicts, expanded: null });
  linkState.launch = linkState.item = linkState.glaunch = linkState.hash = null;
  await loadRp();
  serializeHash();
  mount();
  await refreshCounters();
}

// Load the per-project ReportPortal name map (real project/defect names+colors)
// and reflect an honest resolution status in the header. Best-effort: any failure
// degrades to raw locators + an "unavailable" note.
async function loadRp() {
  try {
    const rp = await api.rp(state.project);
    state.rp = rp;
    setDefects(rp.defects || {});
    setRpStatus(rp.status);
  } catch (e) {
    state.rp = null;
    setDefects({});
    setRpStatus({ configured: true, reachable: false, note: 'RP names: unavailable' });
  }
}

function setRpStatus(status) {
  if (!rpStatusEl) return;
  const s = status || {};
  rpStatusEl.textContent = s.note || 'RP names: —';
  rpStatusEl.classList.toggle('ok', !!s.reachable);
  rpStatusEl.classList.toggle('warn', s.configured && !s.reachable);
}

function wireTabs() {
  tabsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (btn) setView(btn.dataset.view);
  });
}
function setView(view) {
  if (!VIEWS[view]) view = 'journey';
  state.view = view;
  [...tabsEl.querySelectorAll('.tab')].forEach((t) =>
    t.classList.toggle('active', t.dataset.view === view));
  serializeHash();
  mount();
}

function mount() {
  if (state.project == null) return;
  clear(viewEl).appendChild(loading());
  VIEWS[state.view](viewEl, state).catch((e) => {
    clear(viewEl).appendChild(emptyState('🚫', 'Failed to load view', String(e.message || e)));
  });
}

async function refreshCounters() {
  if (state.project == null) return;
  try {
    const s = await api.summary(state.project);
    clear(countersEl);
    const cells = [
      ['accent', s.suggestions, 'suggest'],
      ['', s.abstains, 'abstain'],
      ['good', s.accepted, 'accepted'],
      ['warn', s.corrected, 'corrected'],
    ];
    for (const [cls, n, k] of cells) {
      countersEl.appendChild(h('div', { class: `counter ${cls}` },
        h('span', { class: 'n' }, String(n)), h('span', { class: 'k' }, k)));
    }
  } catch { /* counters are best-effort */ }
}

function wireAutorefresh() {
  const cb = document.getElementById('autorefresh');
  cb.addEventListener('change', () => {
    state.autorefresh = cb.checked;
    if (state._timer) { clearInterval(state._timer); state._timer = null; }
    if (state.autorefresh) {
      state._timer = setInterval(async () => {
        await refreshCounters();
        mount();
        toast('Refreshed');
      }, 10000);
      toast('Auto‑refresh on (10s)');
    }
  });
}

boot();
