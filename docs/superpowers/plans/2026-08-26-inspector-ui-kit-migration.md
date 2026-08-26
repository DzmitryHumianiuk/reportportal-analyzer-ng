# Inspector → @reportportal/ui-kit Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the analyzer-ng inspector frontend (vanilla JS, `inspector/static/`) as a React 18 + TypeScript + Vite app using ReportPortal's `@reportportal/ui-kit` design system and Apache ECharts for all charts, with exact feature parity.

**Architecture:** A Vite SPA in `inspector/frontend/` served as static files by the existing FastAPI backend (unchanged API, unchanged `<!--BASE-->` base-path injection). App shell (topbar/tabs/hash router) + 8 lazy-loaded views. ui-kit components for controls/tables/overlays; custom components (ported 1:1 from the old CSS) for everything the kit lacks; ECharts 5 + echarts-gl for charts; d3 eliminated.

**Tech Stack:** React 18.3, TypeScript 5 (strict), Vite 6, `@reportportal/ui-kit@0.0.1-alpha.247` (exact pin), `echarts@5.6.0`, `echarts-gl@2.0.9`, Vitest + @testing-library/react + jsdom.

## Required reading (spec appendices, same directory)

1. `2026-08-26-inspector-inventory.md` — **the behavioral spec.** Complete inventory of every view, control, chart, API payload, CSS token. The original source under `inspector/static/` is the final authority; read the relevant original file for the task you implement.
2. `2026-08-26-ui-kit-reference.md` — ui-kit component APIs (verbatim prop names), setup contract, pitfalls.
3. `2026-08-26-build-echarts-contract.md` — Vite/Docker/Makefile integration and per-chart library decisions.

## Global Constraints

- **Copy**: preserve the original user-facing strings **verbatim** (they are the parity spec). Any NEW copy: plain global english, no em-dashes, no GenAI tells (project CLAUDE.md).
- **Versions**: `@reportportal/ui-kit` pinned exactly `0.0.1-alpha.247` (no caret). `react`/`react-dom` `18.3.1`. `echarts` `5.6.0`, `echarts-gl` `2.0.9` (echarts v6 is FORBIDDEN — echarts-gl only supports v5). Node 22 (`.nvmrc`).
- **No d3.** Charts per the decision table in `2026-08-26-build-echarts-contract.md` §3.
- **All fetch/asset URLs relative** (`api/...`, no leading slash) — they resolve against the `<base href>` injected by the backend. Never hardcode `/inspector`.
- **`<!--BASE-->` comment must survive** in built `dist/index.html` (`vite.config.ts` `base: './'`).
- **Images are built with host `npm run build` + docker COPY of `dist/`** — `npm` never runs inside `minikube image build` (no DNS in the VM).
- **XSS discipline**: journey/LLM payload text is untrusted. Never `dangerouslySetInnerHTML` for API-provided strings; the only innerHTML-equivalents allowed are the static vendored icon SVGs.
- **localStorage keys** unchanged: `inspector.eng.<key>`.
- **Hash permalink scheme unchanged** (see Task 2 contract) — existing bookmarked URLs must keep working.
- TypeScript strict; `npx tsc --noEmit` must pass; `npm run build` must pass; `npm test` must pass. Frontend files live ONLY under `inspector/frontend/` until Task 12.
- Commit after each task: `feat(inspector-ui): <task summary>` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure (ownership map — parallel tasks touch disjoint files)

```
inspector/frontend/
  package.json  vite.config.ts  tsconfig.json  .nvmrc  .gitignore  index.html
  src/
    main.tsx                 # Task 1/3 — bootstrap: ui-kit style.css, styles/app.css, ThemeProvider, <App/>
    App.tsx                  # Task 3 — shell: Topbar, TabsNav, lazy view registry, Suspense, toast host
    app/
      hash.ts                # Task 2 — readHashParams/writeHash (see Shared contracts)
      state.tsx              # Task 3 — AppProvider context (projects, project, view, rp, linkState, autorefresh, toast)
      api.ts                 # Task 2 — typed fetch client
      types.ts               # Task 2 — all API DTOs
    lib/
      format.ts              # Task 2 — fmt, pct, shortTime, relTime
      labels.ts              # Task 2 — LABEL_NAMES, defect resolution, SOURCE_LABELS, srcInfo
      colors.ts              # Task 2 — resolved hex constants for ECharts
      echartsTheme.ts        # Task 2 — echartsBase() equivalent
    components/              # Task 3 — shared components (contracts below)
      Card.tsx EmptyState.tsx Loading.tsx TabsNav.tsx Topbar.tsx ChipLink.tsx
      DefectBadge.tsx LabelBadge.tsx EngDetails.tsx Microbar.tsx SiMeter.tsx
      EChart.tsx RpIcon.tsx HighlightedPattern.tsx ToastHost.tsx
    styles/app.css           # Task 3 — port of static/css/app.css (tokens aliased to --rp-ui-*)
    test/setup.ts test/utils.tsx  # Task 2/3 — vitest setup + renderWithApp helper
    views/
      journey/Journey.tsx (+ Gauge.tsx, subcomponents, journey.css)   # Task 4
      signatures/Signatures.tsx (+ signatures.css)                    # Task 5
      drain/Drain.tsx (+ Icicle.tsx, drain.css)                       # Task 6
      modes/Modes.tsx (+ modes.css)                                   # Task 7
      groups/Groups.tsx (+ groups.css)                                # Task 8
      loop/Loop.tsx (+ loop.css)                                      # Task 9
      llm/Llm.tsx (+ llm.css)                                         # Task 10
      rubric/Rubric.tsx (+ rubric.css)                                # Task 11
```

Rules: a view task creates files ONLY inside its own `src/views/<name>/` directory (plus its test file `src/views/<name>/<name>.test.tsx`). Shared CSS lives in `styles/app.css` (Task 3, ported verbatim — view classnames from the original app.css keep working); a view may add view-only styles to its own `<name>.css`, imported by its component. No view task edits shared files — if a shared contract is insufficient, the view implements locally and notes it in its report.

**Parallel execution rules (Tasks 4–11 run concurrently in ONE working tree):**
- A view task's gate is SCOPED: `npx vitest run src/views/<name>` only. Do NOT run `npm run build`, `npm run typecheck`, or full `npm test` — they compile the other agents' half-written views and fail through no fault of yours.
- View tasks do NOT run `git commit` (concurrent commits race on the git index). The orchestrator commits after each phase.
- Overwriting the Task-3 stub `src/views/<name>/<Name>.tsx` with the real component is each view task's job and is safe — the stub exists only so `App.tsx`'s lazy registry typechecks.
- The full gate (`tsc --noEmit`, `npm test`, `npm run build`) runs once, after all view tasks finish, in Task 12.

---

## Shared contracts (exact signatures — later tasks rely on these)

### Hash routing (`app/hash.ts`, Task 2)

```ts
export type ViewName = 'journey'|'signatures'|'drain'|'modes'|'groups'|'loop'|'llm'|'rubric';

export interface LinkState {
  launch?: number | null;    // journey
  item?: number | null;      // journey
  glaunch?: number | null;   // groups — SERIALIZED AS 'launch' when view === 'groups'
  q?: string | null;         // signatures
  conflicts?: boolean;       // signatures — serialized as '1' or omitted
  hash?: string | null;      // signatures (expanded error_hash)
  lrole?: string | null;     // llm
  loutcome?: string | null;  // llm
  rule?: string | null;      // rubric
}

export function readHashParams(): Record<string, string>;        // parses '#k=v&k2=v2'
export function writeHash(view: ViewName, project: number | null, link: LinkState): void;
// - history.replaceState, no reload; writes ONLY the params belonging to `view`
//   (journey: launch,item; groups: glaunch→'launch'; signatures: q,conflicts,hash;
//    llm: lrole,loutcome; rubric: rule; drain/modes/loop: none) plus view= and project=.
// - null/''/undefined values are dropped.
```

### API client (`app/api.ts`, Task 2)

```ts
// get(path) — relative URL, Accept: application/json; non-OK throws Error(`${status} ${detail}`)
// where detail is the JSON body's `detail` field when parseable.
export const api = {
  projects(): Promise<ProjectsResponse>,
  rp(project: number): Promise<RpResponse>,
  launches(project: number): Promise<LaunchesResponse>,
  items(project: number, launch: number): Promise<ItemsResponse>,
  journey(project: number, itemId: number): Promise<JourneyResponse>,   // api/item/{project}/{itemId}/journey
  rubric(): Promise<RubricResponse>,
  templates(project: number, q?: string | null): Promise<TemplatesResponse>,
  modes3d(project: number): Promise<Modes3dResponse>,
  groups(project: number, launch?: number | null): Promise<GroupsResponse>,
  timeline(project: number): Promise<TimelineResponse>,
  summary(project: number): Promise<SummaryResponse>,
  signatures(project: number, q?: string | null, conflicts?: boolean, offset?: number): Promise<SignaturesResponse>,
  signatureHash(project: number, errorHash: string): Promise<SignatureHashResponse>,
  analyzerHealth(): Promise<AnalyzerHealthResponse>,
  llmSummary(project: number): Promise<LlmSummaryResponse>,
  llmEvents(project: number, role: string, outcome: string, limit?: number): Promise<LlmEventsResponse>,
  llmCache(project: number, role?: string, limit?: number): Promise<LlmCacheResponse>,
};
```

DTO types (`app/types.ts`): one interface per response above, fields exactly as inventory §2/§5 documents them (optional where the payload may omit). Views import types only from `app/types.ts`.

Note on `glaunch`: the original app keeps the groups launch selection as a hash string (`''` means "All launches"). The typed contract uses `number | null` with `null` = All; the groups Dropdown uses `''` as the All option value and coerces to `null` before `patchLink`, so `writeHash` drops it.

### Test environment (`src/test/setup.ts` + `src/test/utils.tsx`, Tasks 2–3 — binding for all view tests)

`setup.ts` MUST stub jsdom gaps: `globalThis.ResizeObserver` (no-op class), `window.matchMedia` (returns `{matches:false, addEventListener(){}, removeEventListener(){}}`), `Element.prototype.scrollIntoView` (no-op). Without these, EChart mount / reduced-motion checks / rubric focus throw in jsdom.

`test/utils.tsx` MUST export, in addition to `mockApi`/`renderWithApp`:

```ts
export function mockECharts(): { lastOption: () => object | null };
// vi.mock-based double for 'echarts' (and dynamic 'echarts-gl'): init() returns a stub chart
// {setOption, resize, dispose, getDom}; the helper records the last option passed to setOption.
// View tests assert on the captured option object, NEVER on canvas output.
```

`mockApi` ships default fixtures for the three boot endpoints (`projects` — one project id 1, `rp` — empty defects + status `{configured:false, reachable:false, note:'RP names: —'}`, `summary` — zeros) so view tests only add their own endpoints. Passing a key overrides the default.

### App context (`app/state.tsx`, Task 3)

```ts
export interface AppContextValue {
  projects: Project[];
  project: number | null;
  view: ViewName;
  rp: RpResponse | null;              // when loaded, labels.setDefects() has been called
  link: LinkState;                    // current per-view link state (seeded from hash on boot)
  refreshTick: number;                // bumps on auto-refresh poll & project change; views re-fetch on change
  setView(v: ViewName): void;
  setProject(id: number): void;       // resets launch/item/glaunch/hash, keeps q/conflicts, reloads rp
  patchLink(patch: Partial<LinkState>): void;   // merges + writeHash for active view
  toast(msg: string): void;           // bottom-center, 2200 ms
}
export function useApp(): AppContextValue;
export function AppProvider(props: { children: ReactNode }): JSX.Element;
```

Boot sequence, empty/error boot states, hashchange handling, auto-refresh interval (10 000 ms → refresh counters + bump refreshTick + toast "Refreshed"): exactly as inventory §1.

### Shared components (Task 3)

```tsx
export function Card(p: { title: ReactNode; step?: number; sub?: ReactNode; right?: ReactNode;
  className?: string; id?: string; children: ReactNode }): JSX.Element;
export function EmptyState(p: { icon: string; title: string; body?: ReactNode; tag?: string }): JSX.Element;
  // icon: emoji or RP icon name; EMOJI_ICON mapping from inventory §3 applies
export function Loading(p: { text?: string }): JSX.Element;                    // default 'Loading…'; uses BubblesLoader
export function TabsNav(p: { view: ViewName; onChange: (v: ViewName) => void }): JSX.Element;
export function ChipLink(p: { label: ReactNode; url?: string | null; className?: string }): JSX.Element;
  // anchor target=_blank rel=noopener title 'Open in ReportPortal ↗' when url, else span
export function DefectBadge(p: { locator?: string | null; group?: string | null; abbr?: boolean }): JSX.Element;
export function LabelBadge(p: { group: string; text: string }): JSX.Element;
export function EngDetails(p: { storageKey: string; summary?: ReactNode; children: ReactNode }): JSX.Element;
  // native <details class="eng">; open state persisted at localStorage['inspector.eng.'+storageKey]
export function Microbar(p: { ratio: number; color?: string; width?: number; title?: string }): JSX.Element;
export function SiMeter(p: { label: string; value: ReactNode; ratio: number; color?: string;
  tickRatio?: number; scale?: [string, string, string]; note?: ReactNode }): JSX.Element;
export function RpIcon(p: { name: string; size?: number; title?: string; className?: string }): JSX.Element;
  // the 17 vendored icons from rp-icons.js; prefer ui-kit icon components where an equivalent exists
export function HighlightedPattern(p: { pattern: string | null }): JSX.Element; // React port of highlightPattern (spans, no innerHTML)
export function EChart(p: { option: object; height: number | string; useGl?: boolean;
  className?: string; onInit?: (chart: unknown) => void }): JSX.Element;
  // echarts.init on mount (theme from echartsTheme), setOption(option, {notMerge:true}) on change,
  // ResizeObserver → resize(), dispose on unmount; useGl → await import('echarts-gl') once before init
export function renderWithDefectNames(text: string): ReactNode; // in labels.ts — React port (text nodes + .defect-inline pills)
```

### ui-kit usage decisions (binding)

- Selects (project, groups launch) → ui-kit `Dropdown` (+ `FieldLabel` when the original had a field label).
- Toggles (auto-refresh, "only conflicts") → ui-kit `Toggle` (label as children; keep original label text incl. `⚠ only conflicts`).
- Search inputs (signatures, drain) → ui-kit `FieldText` with `startIcon={<SearchIcon/>}`, `clearable`, `defaultWidth={false}`, placeholder verbatim; debounce 250 ms stays in the view.
- Toast → ui-kit `SystemAlert` (type info, duration 2200) in a fixed bottom-center host.
- Loaders → `BubblesLoader` (page/section) in `Loading`.
- Small action buttons (fit, fullscreen, pager `← prev`/`next →`) → ui-kit `Button variant="ghost"` or `BaseIconButton` + `Tooltip`.
- Tables: **ui-kit `Table` ONLY for drain templates** (simple list; pattern cell via `DetailedCellData` component). **Custom `table.data`** (ported CSS) stays for: signatures list (row-click inserts the `sig-detail` row — ui-kit Table has NO API to render a detail panel under a row; its "expansion" only untruncates cell text, verified in the bundled implementation), LLM extractor cache (needs per-row `.row-negative` styling), rubric rules (needs row `id="rule-<id>"` + scrollIntoView focus), journey RRF drawer table, journey feedback raw table, journey LLM events drawer table. Do NOT use `isRowsExpandable` for detail panels anywhere.
- Tooltips on chips/badges: keep native `title=` where the original used `title=` (parity); use ui-kit `Tooltip` only where the original had rich hover cards (none today).
- Icons: ui-kit exported icons where a visual equivalent exists (search, refresh, close, external link…); otherwise `RpIcon`.

---

### Task 1: Scaffold & toolchain

**Files:** Create `inspector/frontend/{package.json, vite.config.ts, tsconfig.json, .nvmrc, .gitignore, index.html, src/main.tsx, src/App.tsx (placeholder), src/vite-env.d.ts}`

**Interfaces produced:** a building Vite app; `npm run dev` proxying `/api` and `/healthz` to `127.0.0.1:5005`; `dist/index.html` containing the literal `<!--BASE-->` comment.

- [ ] **Step 1**: `package.json` — exact deps: `react@18.3.1`, `react-dom@18.3.1`, `@reportportal/ui-kit@0.0.1-alpha.247` (exact), `echarts@5.6.0`, `echarts-gl@2.0.9`, `classnames@^2.5.1`; dev: `vite@^6`, `@vitejs/plugin-react@^4`, `typescript@^5.6`, `@types/react@^18`, `@types/react-dom@^18`, `vitest@^3` (v2 only supports vite 5 — v3 is REQUIRED with vite 6), `@testing-library/react@^16`, `@testing-library/user-event@^14`, `jsdom@^25`. Scripts: `dev`, `build` (`tsc --noEmit && vite build`), `test` (`vitest run`), `typecheck`.
- [ ] **Step 2**: `vite.config.ts` — `base: './'`, react plugin, `server.proxy` for `/api` + `/healthz` → `http://127.0.0.1:5005`, `test` block (jsdom, setupFiles `src/test/setup.ts`, globals true).
- [ ] **Step 3**: `index.html` — `<!doctype html>`, `<!--BASE-->` comment on its own line in `<head>` BEFORE any asset tags, title `analyzer-ng inspector`, the original favicon data-URI (inventory), `<div id="root">`, module script `/src/main.tsx`. Note: with `base:'./'` Vite rewrites the script src to a relative path in dist.
- [ ] **Step 4**: `tsconfig.json` strict, `jsx: react-jsx`, `moduleResolution: bundler`, path-less. `.nvmrc` = `22`. `.gitignore` = `node_modules/`, `dist/`.
- [ ] **Step 5**: minimal `src/main.tsx` (imports `@reportportal/ui-kit/style.css`, renders `<ThemeProvider><App/></ThemeProvider>`) and placeholder `App.tsx` rendering `analyzer-ng inspector`.
- [ ] **Step 6**: `npm install` (this pins the lockfile), `npm run build` — PASS; verify `grep -- '<!--BASE-->' dist/index.html` finds the comment and all asset URLs in dist/index.html are relative (`./assets/...`).
- [ ] **Step 7**: Commit `feat(inspector-ui): scaffold Vite React app for inspector frontend`.

### Task 2: Core libraries (hash, api, types, format, labels, colors, echarts theme) + tests

**Files:** Create `src/app/{hash.ts,api.ts,types.ts}`, `src/lib/{format.ts,labels.ts,colors.ts,echartsTheme.ts}`, `src/test/setup.ts`, tests `src/app/hash.test.ts`, `src/lib/format.test.ts`, `src/lib/labels.test.ts`.

**Interfaces:** Consumes Task 1. Produces the contracts in "Shared contracts" above — signatures must match EXACTLY. Port semantics 1:1 from `inspector/static/js/util.js` and `api.js` (read them).

- [ ] **Step 1**: write failing tests first. Cover at minimum:

```ts
// hash.test.ts
it('parses #view=journey&project=1&launch=42', ...);
it('serializes only active-view params: switching to signatures drops launch/item', ...);
it('serializes glaunch as launch for groups view', ...);
it('drops null/empty params and writes conflicts as 1', ...);
// format.test.ts — fmt(null)='—', fmt(12345)='12,345' (localized), pct(0.123)='12.3%', relTime boundaries
// labels.test.ts — labelGroup('pb001')='pb', defectName falls back to '<Name> group', srcInfo unknown token passthrough,
//                  setDefects + defectInfo resolution
```

- [ ] **Step 2**: run `npm test` — expected FAIL (modules missing).
- [ ] **Step 3**: implement all modules per contracts + util.js semantics. `colors.ts`: read `getComputedStyle(document.documentElement)` with hard fallbacks to the hex values from inventory §4 (jsdom returns empty strings — fallbacks make tests deterministic). `echartsTheme.ts`: export `echartsBase()` returning the shared option fragment (Roboto textStyle INK2, white tooltip, hairline border, shadow `0 8px 40px rgba(0,0,0,.15)`, radius 8, `confine:true`).
- [ ] **Step 4**: `npm test` PASS, `npm run typecheck` PASS.
- [ ] **Step 5**: Commit `feat(inspector-ui): core libs — hash routing, typed api client, labels, formatters`.

### Task 3: App shell, context, shared components, global CSS

**Files:** Create `src/app/state.tsx`, `src/App.tsx` (replace placeholder), `src/components/*` (list in file structure), `src/styles/app.css`, `src/test/utils.tsx`, tests `src/components/components.test.tsx`, `src/app/state.test.tsx`. Modify `src/main.tsx` (import `./styles/app.css` AFTER ui-kit style.css; wrap in `AppProvider`).

**Interfaces:** Consumes Task 2 contracts. Produces: everything in "Shared contracts — App context / Shared components"; lazy view registry in `App.tsx`:

```tsx
const VIEWS: Record<ViewName, LazyExoticComponent<ComponentType>> = {
  journey: lazy(() => import('./views/journey/Journey')), /* ...all 8 */ };
```
(create 8 stub view files `export default function X(){return null}` — view tasks will overwrite them); `src/test/utils.tsx`:

```tsx
export function mockApi(fixtures: Partial<Record<string, unknown>>): void;
// installs a fetch mock: key = path prefix after 'api/' (e.g. 'projects', 'item/'), value = JSON payload
export function renderWithApp(ui: ReactNode, opts?: { hash?: string }): RenderResult;
// sets location.hash, wraps in ThemeProvider+AppProvider, waits for boot
```

- [ ] **Step 1**: port `styles/app.css` from `inspector/static/css/app.css` **verbatim**, with ONE change: the `:root` token block aliases ui-kit variables with hex fallbacks, e.g. `--rp-topaz: var(--rp-ui-base-topaz, #00829b);` for every token that has a `--rp-ui-*` counterpart (see appendix `2026-08-26-ui-kit-reference.md` §4); tokens without a counterpart keep their hex. Remove only rules that ui-kit now owns outright (body font-face — ui-kit ships fonts).
- [ ] **Step 2**: failing tests — topbar renders project options `'name · N items · M launches'`; tab click switches view and rewrites hash; boot with empty projects shows "No projects found"; `EngDetails` persists open state to localStorage; `EmptyState` maps 🚫→error icon.
- [ ] **Step 3**: implement `state.tsx` (boot per inventory §1 incl. hashchange listener, project resolution by id OR name, auto-refresh interval, counters from `api/summary`, rp status line states ok/warn/unavailable), `Topbar` (brand SVG mark verbatim from index.html, counters, ui-kit Dropdown for project, ui-kit Toggle for auto-refresh), `TabsNav` (8 tabs, order/labels verbatim), `ToastHost` (SystemAlert), all shared components per contract, `App.tsx` with Suspense fallback `<Loading/>` AND a `ViewErrorBoundary` wrapping the active view: any render/fetch error inside a view renders `EmptyState(icon '🚫', title 'Failed to load view', body = error message)` — this owns the old `mount()` rejection state so 8 view agents don't improvise it.
- [ ] **Step 4**: `npm test` + `typecheck` + `build` PASS.
- [ ] **Step 5**: Commit `feat(inspector-ui): app shell — topbar, tabs, hash router, shared components`.

### Task 4: Item Journey view

**Files:** Create `src/views/journey/{Journey.tsx, Gauge.tsx, journey.css, journey.test.tsx}` and any subcomponent files inside `src/views/journey/` (recommended: `LaunchPicker.tsx, ItemPicker.tsx, SignatureCard.tsx, GroupingCard.tsx, MatchingCard.tsx, DecisionCard.tsx, FeedbackCard.tsx, llmStrip.tsx` — implementer's choice, but ALL inside the directory).

**Interfaces:** Consumes: `useApp()` (`link.launch`, `link.item`, `patchLink`, `refreshTick`), `api.launches/items/journey`, all shared components, `renderWithDefectNames`, `HighlightedPattern`.

- [ ] **Step 1**: read inventory §2.1 END TO END, then the original `inspector/static/js/views/journey.js` (1437 lines — it is the spec for every takeaway string, tooltip, dedup rule, and edge case).
- [ ] **Step 2**: failing smoke test: `mockApi` with a journey fixture exercising signature+grouping(burst)+matching(C)+decision(gbm suggest)+feedback(2 events); assert: stepper shows 5 stages, burst badge present, gauge value rendered, feedback timeline has 2 entries, takeaway sentences match fixture-derived strings.
- [ ] **Step 3**: implement. Non-negotiable behaviors: permalinked-item-missing empty state; member-dot click navigates item within launch (patchLink); stepper click smooth-scroll + 900 ms pulse (skip under `prefers-reduced-motion`); `Gauge.tsx` = React SVG port of the hand-rolled banded gauge incl. provisional dashed tick + threshold chips; evidence groups auto-expand rule; `sug <id> ↑ stage 4` scroll-pulse button; localStorage drawer keys `grouping, matching, matching-recon, decision, llm, feedback`; templates >6 drawer; drift-check note (>0.005); METHOD_TIP/OUTCOME_TIP/BAND_TIP tooltip copy verbatim.
- [ ] **Step 4**: scoped gate `npx vitest run src/views/journey` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Item Journey view`.

### Task 5: Signatures view

**Files:** Create `src/views/signatures/{Signatures.tsx, signatures.css, signatures.test.tsx}`.

**Interfaces:** Consumes `useApp()` (`link.q/conflicts/hash`, `patchLink`, `refreshTick`), `api.signatures/signatureHash`, ui-kit `FieldText`, `Toggle`, shared components (custom `table.data` markup for the table).

- [ ] **Step 1**: read inventory §2.2 + original `views/signatures.js`.
- [ ] **Step 2**: failing test: fixture with 2 rows (one conflict); assert summary chips, conflict row flag, expansion fetches detail and renders member items; search input debounce writes `q`.
- [ ] **Step 3**: implement with the CUSTOM `table.data` markup (ported CSS — ui-kit Table cannot render a detail row, see "ui-kit usage decisions"): 6 columns per inventory §2.2; row click toggles a `<tr class="sig-detail">` spanning 6 cols (max one open, synced to `link.hash`, permalinked expansion auto-opens after render); `.conflict` row styling + ⚠ flag; detail body fetched from `api/signature-hash` (full hash chip, fp chip, representative item, conflict note, signature field rows, template mini-cards, member list with cap note); pager (`← prev`/`next →` ghost Buttons + `rows X–Y`); three distinct empty-state bodies verbatim. ui-kit is still used for the controls (FieldText search, Toggle).
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Signatures view`.

### Task 6: Drain3 Explorer view

**Files:** Create `src/views/drain/{Drain.tsx, Icicle.tsx, drain.css, drain.test.tsx}`.

**Interfaces:** Consumes `api.templates`, `HighlightedPattern`, `EChart`, ui-kit `Table`, `FieldText`.

- [ ] **Step 1**: read inventory §2.3 + §6 (icicle detail) + original `views/drain.js`.
- [ ] **Step 2**: failing test: fixture with 3 patterns sharing a prefix; assert table rows render highlighted tokens; icicle renders (EChart mounted) with the prefix tree.
- [ ] **Step 3**: implement `Icicle.tsx` as ECharts `custom` series: build the token-prefix tree (first 4 masked tokens, counts = match_count) in plain TS, compute partition rectangles (transposed layout per original — x vertical), `renderItem` draws rect (1px gutters, rx 3) + conditional truncated label (only when cell height > 14px, mono 10px); depth color = blues interpolation `0.35 + depth*0.14` implemented in TS; tooltip = ancestor path + `N matches`. Templates table via ui-kit `Table` (pattern cell = `HighlightedPattern` component), 520px scroll container.
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Drain3 Explorer view`.

### Task 7: Modes Map 3D view

**Files:** Create `src/views/modes/{Modes.tsx, modes.css, modes.test.tsx}`.

**Interfaces:** Consumes `api.modes3d`, `EChart` (with `useGl` for 3D), `echartsTheme`, colors/labels libs.

- [ ] **Step 1**: read inventory §2.4 + §6 + original `views/modes.js`.
- [ ] **Step 2**: failing test: fixture `available:false` renders diagnostics chips; fixture with `dimensions:2` renders 2D fallback note.
- [ ] **Step 3**: implement: wide-card breakout CSS; legend + fullscreen chip (Fullscreen API on wrapper, height `max(560px,72vh)` / `calc(100vh - 24px)`); 3D via `EChart useGl` (`scatter3D` per label group + diamond centroid series, `viewControl.autoRotate` speed 6 distance 190); 2D fallback scatter; rich HTML tooltips with `enterable:true` RP links (verbatim formats).
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Modes Map 3D view`.

### Task 8: Launch Groups view

**Files:** Create `src/views/groups/{Groups.tsx, groups.css, groups.test.tsx}`.

**Interfaces:** Consumes `useApp()` (`link.glaunch`, `patchLink`), `api.launches/groups`, `EChart`, ui-kit `Dropdown`, shared components.

- [ ] **Step 1**: read inventory §2.5 + §6 + original `views/groups.js`, and the migration decision in `2026-08-26-build-echarts-contract.md` §3 (graph force replaces d3).
- [ ] **Step 2**: failing test: fixture with 2 groups / 5 nodes; assert group mini-cards (burst badge on dominant), EChart mounted, launch Dropdown options include "All launches".
- [ ] **Step 3**: implement ECharts `graph` series `layout:'force'`: nodes colored by defectColor, `itemStyle.borderColor` accent + borderWidth 3 when auto-analyzed else white 1.2; links = per-group star (first member → rest) synthesized exactly as original; `categories` per group with seeded initial x positions (groups evenly spaced) to approximate the old per-group gravity wells; `roam:true`, `draggable:true`; force params `{repulsion:160, edgeLength:46, gravity:0.06}` tuned to visually match; HTML tooltip (`enterable:true`) carrying the RP deep link + name/label/group/auto flag; "fit" ghost Button re-runs `setOption` fresh; legend row + hint text verbatim.
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Launch Groups view`.

### Task 9: Learning Loop view

**Files:** Create `src/views/loop/{Loop.tsx, loop.css, loop.test.tsx}`.

**Interfaces:** Consumes `api.timeline/analyzerHealth`, `EChart`, `SiMeter`, shared components, labels/colors.

- [ ] **Step 1**: read inventory §2.6 + original `views/loop.js` (maturity copy blocks are verbatim-critical: MAT_STAGES 157–178, honesty guards 249–265).
- [ ] **Step 2**: failing test: fixture with events+artifacts+metrics+maturity(warm); assert: scatter and stacked-area ECharts mounted, active maturity pill = WARM, honesty guard rendering for a crafted condition, health "not configured" empty state.
- [ ] **Step 3**: implement all five cards per inventory: time×category scatter (custom HTML tooltip with RP link), artifacts cards, stacked area chart (accepted/corrected/abstained/ignored, band colors, areaStyle 0.5), maturity stepper+meter+stage cards+stat row+honesty guards+drawer, health side panel (sticky).
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Learning Loop view`.

### Task 10: LLM view

**Files:** Create `src/views/llm/{Llm.tsx, llm.css, llm.test.tsx}`.

**Interfaces:** Consumes `useApp()` (`link.lrole/loutcome`, `patchLink`), `api.llmSummary/llmEvents/llmCache`, shared components (custom `table.data` for the cache table — per-row `.row-negative` styling).

- [ ] **Step 1**: read inventory §2.7 + original `views/llm.js`. XSS rule: model `output` JSON always rendered as text (`<pre>` textContent), never HTML.
- [ ] **Step 2**: failing test: fixture with 3 roles (one zero-event); assert role panels (dashed empty variant), outcome-mix stacked bar widths, filter chip click refetches with `role=` param, item chip links to `#view=journey&...`.
- [ ] **Step 3**: implement six cards per inventory §2.7 verbatim (glosses, state lines, latency `latFmt`, availability honesty block + breaker drawer copy, cache table via custom `table.data` with `.row-negative` rows and output JSON drawers).
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): LLM view`.

### Task 11: Cold-start Rules view

**Files:** Create `src/views/rubric/{Rubric.tsx, rubric.css, rubric.test.tsx}`.

**Interfaces:** Consumes `useApp()` (`link.rule`), `api.rubric`, `LabelBadge` (custom `.rubric-table` markup — rows need `id="rule-<id>"` and focus styling, which ui-kit Table cannot attach).

- [ ] **Step 1**: read inventory §2.8 + original `views/rubric.js`.
- [ ] **Step 2**: failing test: fixture 3 rules; assert intro paragraph verbatim, row focus + scrollIntoView when `link.rule='R2'`, confidence gloss text.
- [ ] **Step 3**: implement (custom `.rubric-table` markup; rule row `id="rule-<id>"`; `.rubric-row-focus` styling; `scrollIntoView({block:'center'})` via ref effect).
- [ ] **Step 4**: scoped gate `npx vitest run src/views/<this view>` PASS (no repo-wide build/typecheck/commit in parallel mode — see Parallel execution rules). Orchestrator commit: `feat(inspector-ui): Cold-start Rules view`.

### Task 12: Backend integration, Docker, Makefile, cutover

**Files:** Modify `inspector/backend/app.py` and `inspector/backend/config.py` (dist dir becomes a `Config` field defaulting to `frontend/dist`, so tests can inject a tmp dist; `/assets` mount with `Cache-Control: immutable`; index (`/`) served with explicit `Cache-Control: no-cache` — a cached index referencing purged hashed chunks would 404 after redeploy; keep `<!--BASE-->` replace + `/healthz` + root-path Mount untouched; keep an `exists()` guard — `dist/` is gitignored and absent on fresh clones/CI); Modify `inspector/Dockerfile` (multi-stage node build per appendix §2 Path 1; ALSO fix the pre-existing bug: `COPY --chown=inspector:...` currently runs BEFORE `useradd inspector` — reorder or chown in RUN); Modify `.dockerignore` (add `inspector/frontend/node_modules` — do NOT add `inspector/frontend/dist`, the overlay COPYs it); Modify `Dockerfile.inspector-overlay` (COPY `inspector/frontend/dist` instead of `inspector/static`); Modify `Makefile` (targets `ui-install`, `ui-build`, `ui-dev`; `inspector-overlay` depends on `ui-build`); Modify `DEVELOPER.md` (dev workflow: two terminals; overlay image builds ONLY via make so a stale gitignored dist is never shipped silently); Delete `inspector/static/` (after everything passes); Modify `inspector/tests/*` only if a test references `static/`.

**Interfaces:** Consumes the built `dist/`. Produces: the served app.

- [ ] **Step 1**: read appendix `2026-08-26-build-echarts-contract.md` §1–2 and current `backend/app.py` fully.
- [ ] **Step 2**: failing backend test (pytest, extend `inspector/tests/`): `GET /` returns HTML containing `<base href="/">` and referencing `./assets/`; with `INSPECTOR_ROOT_PATH=/inspector` returns `<base href="/inspector/">`. Mechanism: the test builds a tiny fake dist (index.html with `<!--BASE-->` + one asset) in `tmp_path` and constructs the app via `create_app(Config(... static_dist=tmp_path ...))` — do NOT test against the module-level `app` singleton (its config is frozen at import). Tests must pass on a checkout with no real `frontend/dist`.
- [ ] **Step 3**: implement backend changes; run `cd inspector/frontend && npm run build`; run backend tests — PASS (`python -m pytest inspector/tests -q`).
- [ ] **Step 4**: manual smoke: `uvicorn backend.app:app --app-dir inspector --port 5005` + `curl -s localhost:5005/ | grep assets` and `curl -s localhost:5005/api/health`.
- [ ] **Step 5**: update Dockerfiles + Makefile + DEVELOPER.md per appendix §2; `git rm -r inspector/static`.
- [ ] **Step 6**: full gate: `npm test`, `npm run typecheck`, `npm run build`, `python -m pytest inspector/tests -q`, `make lint` (must not regress). Image smoke: overlay path via `eval $(minikube docker-env) && docker build -f Dockerfile.inspector-overlay --build-arg BASE=<current inspector tag> .` (network-free, verifiable on this host); the canonical multi-stage Dockerfile needs a networked docker daemon — if none is available, mark it "fixed but unverified on this host" in the task report instead of skipping silently. Commit `feat(inspector-ui): serve Vite build from FastAPI, docker + make integration, remove legacy static app`.

---

## Verification gate (after all tasks)

1. `cd inspector/frontend && npm ci && npm run build && npm test` — all green.
2. `python -m pytest inspector/tests -q` — green.
3. `grep -- '<!--BASE-->' inspector/frontend/dist/index.html` — present.
4. `grep -rn 'from .d3.\|import .d3' inspector/frontend/src` — empty (no d3).
5. `grep -n '"echarts"' inspector/frontend/package.json` — `5.6.0`; ui-kit exact `0.0.1-alpha.247`.
6. Backend serves the app at `/` and `/inspector` (root-path test from Task 12).

## Self-review notes (Fable5)

- Spec coverage: Tasks 1–12 cover inventory §1 (Task 3), §2.1–2.8 (Tasks 4–11), §3 (Task 2/3), §4 (Task 3 CSS), §5 (Task 12), §6 (Tasks 6/7/8 + Gauge in Task 4).
- Known accepted fidelity deltas (approved direction, echo in reviews): groups force-graph gravity wells approximated by categories/seeded positions; in-node RP links move to tooltip; auto-fit-until-touched simplified to a fit button; signature/drain/llm-cache/rubric tables adopt ui-kit Table look (RP consistency is the goal of this migration).
- Type consistency: all cross-task names are pinned in "Shared contracts"; view tasks import only from `app/`, `lib/`, `components/`, ui-kit, echarts.
