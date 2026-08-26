// App shell state: project list, active view, per-view permalink state, the RP
// name map, topbar counters and auto-refresh.
//
// The address-bar hash mirrors the current view + project (+ per-view
// selection). Writes go through history.replaceState, so the app never
// re-enters its own router; a real `hashchange` (browser back/forward, manual
// edit) re-applies the whole state.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { api } from './api';
import { isViewName, readHashParams, writeHash, type LinkState, type ViewName } from './hash';
import type { Project, RpResponse, RpStatus, SummaryResponse } from './types';
import { setDefects } from '../lib/labels';

const AUTOREFRESH_MS = 10000;

const DEFAULT_RP_STATUS: RpStatus = { configured: false, reachable: false, note: 'RP names: —' };

const UNAVAILABLE_RP_STATUS: RpStatus = {
  configured: true,
  reachable: false,
  note: 'RP names: unavailable',
};

export type BootState =
  | { status: 'loading' }
  | { status: 'ready' }
  | { status: 'empty' }
  | { status: 'error'; message: string };

export interface AppContextValue {
  projects: Project[];
  project: number | null;
  view: ViewName;
  /** When loaded, labels.setDefects() has been called with this payload. */
  rp: RpResponse | null;
  /** Header status line under the brand: note + ok/warn state. */
  rpStatus: RpStatus;
  /** Current per-view link state (seeded from the hash on boot). */
  link: LinkState;
  /** Bumps on auto-refresh poll and project change; views re-fetch on change. */
  refreshTick: number;
  /** Topbar counters from api/summary. Best-effort: null while unknown. */
  counters: SummaryResponse | null;
  boot: BootState;
  autorefresh: boolean;
  setView(v: ViewName): void;
  setProject(id: number): void;
  patchLink(patch: Partial<LinkState>): void;
  setAutorefresh(on: boolean): void;
  toast(msg: string): void;
  /** Current toast text (null when nothing is showing) — read by ToastHost. */
  toastMessage: string | null;
  dismissToast(): void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>');
  return ctx;
}

function num(v: string | null | undefined): number | null {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Resolve a `project` hash value (id or RP name, case-insensitive) → id. */
function resolveProject(value: string, projects: Project[]): number | null {
  const s = String(value);
  const byId = projects.find((p) => String(p.project_id) === s);
  if (byId) return byId.project_id;
  const low = s.toLowerCase();
  const byName = projects.find((p) => (p.project_name || '').toLowerCase() === low);
  return byName ? byName.project_id : null;
}

/** Read the per-view selection out of parsed hash params. */
function linkFromHash(hp: Record<string, string>, view: ViewName): LinkState {
  return {
    launch: view === 'groups' ? null : num(hp.launch),
    item: num(hp.item),
    glaunch: view === 'groups' ? num(hp.launch) : null,
    q: hp.q || '',
    conflicts: hp.conflicts === '1',
    hash: hp.hash || null,
    lrole: hp.lrole || null,
    loutcome: hp.loutcome || null,
    rule: view === 'rubric' ? hp.rule || null : null,
  };
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProjectId] = useState<number | null>(null);
  const [view, setViewName] = useState<ViewName>('journey');
  const [rp, setRp] = useState<RpResponse | null>(null);
  const [rpStatus, setRpStatus] = useState<RpStatus>(DEFAULT_RP_STATUS);
  const [link, setLink] = useState<LinkState>({});
  const [refreshTick, setRefreshTick] = useState(0);
  const [counters, setCounters] = useState<SummaryResponse | null>(null);
  const [boot, setBoot] = useState<BootState>({ status: 'loading' });
  const [autorefresh, setAutorefreshState] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Latest values for callbacks that must not re-create on every render.
  const stateRef = useRef({ view, project, link, projects });
  stateRef.current = { view, project, link, projects };

  // Pending "restart the alert" timer. Kept so a fast second toast and unmount
  // both cancel it instead of leaving a stray callback behind.
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toast = useCallback((msg: string) => {
    // Re-toasting the same text must restart the alert, so clear first.
    if (toastTimer.current != null) clearTimeout(toastTimer.current);
    setToastMessage(null);
    toastTimer.current = setTimeout(() => {
      toastTimer.current = null;
      setToastMessage(msg);
    }, 0);
  }, []);

  useEffect(
    () => () => {
      if (toastTimer.current != null) clearTimeout(toastTimer.current);
      toastTimer.current = null;
    },
    [],
  );

  const dismissToast = useCallback(() => setToastMessage(null), []);

  const refreshCounters = useCallback(async (id: number | null) => {
    if (id == null) return;
    try {
      const payload = await api.summary(id);
      // The user may have switched project while this was in flight.
      if (stateRef.current.project !== id) return;
      setCounters(payload);
    } catch {
      /* counters are best-effort */
    }
  }, []);

  // Monotonic token: a slow api/rp answer for a project the user already left
  // must never overwrite the name map of the project now on screen.
  const rpToken = useRef(0);

  // Per-project ReportPortal name map (real project/defect names + colors) plus
  // an honest resolution status. Best-effort: any failure degrades to raw
  // locators and an "unavailable" note.
  const loadRp = useCallback(async (id: number) => {
    const token = (rpToken.current += 1);
    try {
      const payload = await api.rp(id);
      if (token !== rpToken.current) return;
      setDefects(payload.defects || {});
      setRp(payload);
      setRpStatus(payload.status || DEFAULT_RP_STATUS);
    } catch {
      if (token !== rpToken.current) return;
      setDefects({});
      setRp(null);
      setRpStatus(UNAVAILABLE_RP_STATUS);
    }
  }, []);

  /** Restore project + view + per-view selection from the hash, then rewrite it. */
  const applyHash = useCallback(
    async (hp: Record<string, string>, list: Project[], initial: boolean) => {
      const current = stateRef.current.project;
      let projId = current ?? list[0].project_id;
      if (hp.project) {
        const resolved = resolveProject(hp.project, list);
        if (resolved != null) projId = resolved;
      }
      const projectChanged = projId !== current;
      const nextView = isViewName(hp.view) ? hp.view : 'journey';
      const nextLink = linkFromHash(hp, nextView);

      setProjectId(projId);
      setViewName(nextView);
      setLink(nextLink);
      stateRef.current = { view: nextView, project: projId, link: nextLink, projects: list };

      if (projectChanged || initial) await loadRp(projId);
      // A newer project may have been picked while api/rp was in flight.
      if (stateRef.current.project !== projId) return;
      writeHash(nextView, projId, nextLink);
      if (projectChanged || initial) {
        setRefreshTick((t) => t + 1);
        // Counters are a topbar detail: never hold the first view paint on them.
        void refreshCounters(projId);
      }
    },
    [loadRp, refreshCounters],
  );

  // ---- boot ---------------------------------------------------------------
  // The effect must be safely re-runnable: React StrictMode mounts, cleans up
  // and mounts again in dev, so a "run once" guard would leave the app stuck on
  // Loading (the first run is cancelled, the second one never starts). The only
  // cancellation is this run's own `cancelled` flag, and the hashchange
  // listener is attached here so the cleanup + second run re-attach it.
  useEffect(() => {
    let cancelled = false;

    const onHashChange = () => {
      const list = stateRef.current.projects;
      if (!list.length) return;
      void applyHash(readHashParams(), list, false);
    };

    // Browser back/forward + manual hash edits re-apply (replaceState never
    // fires this). It no-ops until the project list has loaded.
    window.addEventListener('hashchange', onHashChange);

    void (async () => {
      try {
        const { projects: list } = await api.projects();
        if (cancelled) return;
        setProjects(list);
        stateRef.current = { ...stateRef.current, projects: list };
        if (!list.length) {
          setBoot({ status: 'empty' });
          return;
        }
        await applyHash(readHashParams(), list, true);
        if (cancelled) return;
        setBoot({ status: 'ready' });
      } catch (e) {
        if (!cancelled) setBoot({ status: 'error', message: String((e as Error)?.message || e) });
      }
    })();

    return () => {
      cancelled = true;
      window.removeEventListener('hashchange', onHashChange);
    };
  }, [applyHash]);

  // ---- auto-refresh -------------------------------------------------------
  useEffect(() => {
    if (!autorefresh) return undefined;
    const timer = setInterval(() => {
      void refreshCounters(stateRef.current.project);
      setRefreshTick((t) => t + 1);
      toast('Refreshed');
    }, AUTOREFRESH_MS);
    return () => clearInterval(timer);
  }, [autorefresh, refreshCounters, toast]);

  const setAutorefresh = useCallback(
    (on: boolean) => {
      setAutorefreshState(on);
      if (on) toast('Auto‑refresh on (10s)');
    },
    [toast],
  );

  const setView = useCallback((next: ViewName) => {
    const v = isViewName(next) ? next : 'journey';
    const { project: id, link: current } = stateRef.current;
    setViewName(v);
    stateRef.current = { ...stateRef.current, view: v };
    writeHash(v, id, current);
  }, []);

  const patchLink = useCallback((patch: Partial<LinkState>) => {
    const next = { ...stateRef.current.link, ...patch };
    stateRef.current = { ...stateRef.current, link: next };
    setLink(next);
    writeHash(stateRef.current.view, stateRef.current.project, next);
  }, []);

  const setProject = useCallback(
    (id: number) => {
      // id-scoped selections do not carry across projects; free-text ones do.
      const next: LinkState = {
        ...stateRef.current.link,
        launch: null,
        item: null,
        glaunch: null,
        hash: null,
      };
      stateRef.current = { ...stateRef.current, project: id, link: next };
      setProjectId(id);
      setLink(next);
      void (async () => {
        await loadRp(id);
        // Slow api/rp (the RP-unreachable case) means the user can pick another
        // project first. Only the project still on screen may write the hash,
        // bump the refresh tick and pull counters.
        if (stateRef.current.project !== id) return;
        writeHash(stateRef.current.view, id, next);
        setRefreshTick((t) => t + 1);
        void refreshCounters(id);
      })();
    },
    [loadRp, refreshCounters],
  );

  const value = useMemo<AppContextValue>(
    () => ({
      projects,
      project,
      view,
      rp,
      rpStatus,
      link,
      refreshTick,
      counters,
      boot,
      autorefresh,
      setView,
      setProject,
      patchLink,
      setAutorefresh,
      toast,
      toastMessage,
      dismissToast,
    }),
    [
      projects,
      project,
      view,
      rp,
      rpStatus,
      link,
      refreshTick,
      counters,
      boot,
      autorefresh,
      setView,
      setProject,
      patchLink,
      setAutorefresh,
      toast,
      toastMessage,
      dismissToast,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
