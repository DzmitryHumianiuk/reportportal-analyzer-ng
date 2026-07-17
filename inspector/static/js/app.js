// App shell: project selector, live counters, tab routing, auto-refresh.
import { api } from './api.js';
import { h, clear, toast, loading, emptyState } from './util.js';
import { renderJourney } from './views/journey.js';
import { renderDrain } from './views/drain.js';
import { renderModes } from './views/modes.js';
import { renderGroups } from './views/groups.js';
import { renderLoop } from './views/loop.js';

const VIEWS = {
  journey: renderJourney, drain: renderDrain, modes: renderModes,
  groups: renderGroups, loop: renderLoop,
};

export const state = {
  project: null,
  view: 'journey',
  autorefresh: false,
  _timer: null,
};

const viewEl = document.getElementById('view');
const tabsEl = document.getElementById('tabs');
const selectEl = document.getElementById('project-select');
const countersEl = document.getElementById('counters');

async function boot() {
  wireTabs();
  wireAutorefresh();
  try {
    const { projects } = await api.projects();
    if (!projects.length) {
      clear(viewEl).appendChild(emptyState('🗄️', 'No projects found',
        'The analyzer database has no project rows. Ingest at least one launch through the analyzer, then reload.',
        'analyzer.project'));
      return;
    }
    clear(selectEl);
    for (const p of projects) {
      selectEl.appendChild(h('option', { value: p.project_id },
        `Project ${p.project_id} · ${p.item_count} items · ${p.launch_count} launches`));
    }
    state.project = projects[0].project_id;
    selectEl.value = state.project;
    selectEl.addEventListener('change', () => {
      state.project = Number(selectEl.value);
      refreshCounters();
      mount();
    });
    setView(location.hash.replace('#', '') || 'journey');
    await refreshCounters();
  } catch (e) {
    clear(viewEl).appendChild(emptyState('🚫', 'Cannot reach the inspector API', String(e.message || e), 'GET /api/projects'));
  }
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
  location.hash = view;
  [...tabsEl.querySelectorAll('.tab')].forEach((t) =>
    t.classList.toggle('active', t.dataset.view === view));
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

window.addEventListener('hashchange', () => {
  const v = location.hash.replace('#', '');
  if (v && v !== state.view) setView(v);
});

boot();
