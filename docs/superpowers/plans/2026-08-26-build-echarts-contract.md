# Build / Packaging / Charting Contract (Council Report 3, Fable5)

> Spec appendix for the ui-kit migration plan: how the Vite React app fits the
> repo's build and deploy reality, and the per-chart library decisions.

## 1. Current serving & deploy (facts)

### Backend serving (`inspector/backend/app.py`)
- **Static mount**: `app.mount("/static", StaticFiles(directory=STATIC_DIR))` where `STATIC_DIR = inspector/static` (app.py:28, 205-206). Mounted **last** so `/api` routes win.
- **`<!--BASE-->` mechanism** (app.py:208-218): `index.html` is read once at startup and `"<!--BASE-->"` is string-replaced with `<base href="{root_path}/">`. All asset URLs in index.html are **relative**, resolved by the `<base>` tag.
- **Prefix handling**: `INSPECTOR_ROOT_PATH` (default empty; `/inspector` in k8s) → the whole FastAPI app is wrapped in a Starlette `Mount(cfg.root_path, app=inner)` with a top-level `/healthz` for probes (app.py:31-46). Ingress does **no** rewrite; the pod literally receives `/inspector/...` paths.
- **CORS**: none. Same-origin only. **Port**: `INSPECTOR_HTTP_PORT`, default **5005**.
- **Cache middleware**: `Cache-Control: no-cache` on any `/static/` path (app.py:69-74) — exists because files are unhashed ES modules; Vite's hashed filenames make this obsolete (flip to `immutable` for `/assets/`).
- **Realtime**: **none**. No websockets, no SSE. "Auto-refresh" is a 10s `setInterval` poll. Dev proxy only needs plain HTTP `/api` + `/healthz`.

### Image build & deploy
- `inspector/Dockerfile`: single-stage `FROM python:3.12-slim`, pip installs `inspector/requirements.txt`, COPYs `inspector/backend`, `inspector/static`, plus two vendored analyzer files (`queries.py`, `rubric.py`). Build context = **repo root**. Runs as uid 10001, uvicorn on 5005.
- `Dockerfile.inspector-overlay` (repo root): `FROM ${BASE}` (a previous inspector image), COPYs only backend/static/queries/rubric. Exists because **the minikube VM has no outbound DNS — `pip install` fails inside `minikube image build`**. Note in the file: `minikube image build` rejects `--build-arg`, so overlay builds go through `eval $(minikube docker-env)` + plain `docker build`. **`npm install` inside a minikube image build will fail the same way.**
- `inspector/k8s.yaml`: Deployment `analyzer-inspector`, image `analyzer-inspector:insNN` (unique tag per build, `imagePullPolicy: IfNotPresent`, never `:latest`), env `INSPECTOR_ROOT_PATH=/inspector`, port 5005, probes on `/healthz`.
- `inspector/ingress.yaml`: nginx Ingress, `path: /inspector`, `pathType: Prefix`, **no rewrite annotation**.
- **Makefile**: no inspector or image targets today (only install/lint/test/clean, Python 3.12 venv).
- **Node tooling in repo**: **zero** today. Pure Python 3.12, uv.lock present.

### @reportportal/ui-kit consumer requirements
- v `0.0.1-alpha.247`, `"type": "module"`, built with Vite itself.
- **Dist is pre-built ESM JS + one compiled `dist/style.css`** — no SCSS loader needed on the consumer side. Consumer does `import '@reportportal/ui-kit/style.css'`.
- Exports map: `.`, `./style.css`, `./common`, `./*` (per-component entries). Peer deps: react/react-dom 17.x-18.x → **pin React 18**, not 19.
- A stock `@vitejs/plugin-react` config suffices.

## 2. Vite integration contract

### File tree
```
inspector/
  frontend/                  # NEW — Vite React app
    package.json             # react@18, react-dom@18, @reportportal/ui-kit (exact pin), echarts@5, echarts-gl@2, vite
    vite.config.ts
    .nvmrc                   # 22
    index.html               # Vite entry (replaces static/index.html); contains <!--BASE--> in <head>
    src/ ...
    dist/                    # build output (gitignored; built on the host)
  backend/                   # unchanged except static serving paths
  static/                    # DELETED after cutover
```

### Base-path handling (replaces `<!--BASE-->` 1:1)
Keep the runtime-injection property (one image works at `/` and `/inspector`):
- `vite.config.ts`: `base: './'` (relative base) → Vite emits relative asset URLs, exactly like today's hand-written relative URLs.
- Put `<!--BASE-->` in `frontend/index.html`'s `<head>` (Vite passes unknown comments through to dist), and keep app.py's `replace("<!--BASE-->", f'<base href="{base}">')` on `dist/index.html`. The SPA continues to `fetch('api/...')` with **relative** URLs resolved by `<base>`. Zero build-time coupling to the deploy prefix.
- Backend change: point `STATIC_DIR` at `frontend/dist`; serve `dist/assets` at `/assets` with `Cache-Control: immutable`; serve rendered index at `/`.

### Dev workflow
- Terminal 1: `uvicorn backend.app:app --app-dir inspector --port 5005` (no `INSPECTOR_ROOT_PATH`).
- Terminal 2: `cd inspector/frontend && npm run dev` with:
```ts
server: { proxy: { '/api': 'http://127.0.0.1:5005', '/healthz': 'http://127.0.0.1:5005' } }
```
No CORS needed (proxy keeps same-origin). In dev, relative `api/...` fetches from the SPA root work against the proxy since dev serves at `/`.

### Docker: two paths, dictated by the no-DNS minikube constraint
**Path 1 — canonical multi-stage `inspector/Dockerfile`** (for hosts with network / CI):
```dockerfile
FROM node:22-alpine AS ui
WORKDIR /ui
COPY inspector/frontend/package*.json ./
RUN npm ci
COPY inspector/frontend/ ./
RUN npm run build            # -> /ui/dist

FROM python:3.12-slim
# ... existing pip install, backend COPY, user setup unchanged ...
COPY --from=ui /ui/dist /app/frontend/dist
```
**Path 2 — the daily minikube loop**: `minikube image build` cannot run `npm ci` (no DNS) and rejects `--build-arg`. So **build the frontend on the host**, then the overlay only COPYs artifacts:
```dockerfile
# Dockerfile.inspector-overlay (extended)
ARG BASE=analyzer-inspector:insNN
FROM ${BASE}
COPY --chown=inspector:inspector inspector/backend /app/backend
COPY --chown=inspector:inspector inspector/frontend/dist /app/frontend/dist
COPY --chown=inspector:inspector src/analyzer_ng/db/repositories/queries.py /app/analyzer_sql/queries.py
COPY --chown=inspector:inspector src/analyzer_ng/llm/rubric.py /app/analyzer_sql/rubric.py
```
built via `eval $(minikube docker-env) && docker build` — same trick as today, still network-free. `node_modules` stays on the host only.

### Makefile targets (new)
```make
ui-install:   cd inspector/frontend && npm ci
ui-build:     cd inspector/frontend && npm run build
ui-dev:       cd inspector/frontend && npm run dev
inspector-image: ui-build          # then docker build -f inspector/Dockerfile (networked host)
inspector-overlay: ui-build        # then minikube docker-env + docker build -f Dockerfile.inspector-overlay --build-arg BASE=...
```

## 3. ECharts / d3 decisions

Vendored versions today: **echarts 5.6.0**, **echarts-gl 2.x UMD**, **d3 7.9.0**.
npm: `echarts-gl` latest is **2.0.9 and only supports echarts 5.x, not v6** → **pin exactly `echarts@5.6.0` + `echarts-gl@2.0.9`** (exact pins, no caret — matches the plan's Global Constraints). Do **not** move to echarts v6 while the 3D modes view exists.

| View file | Chart | Library today | Series/APIs used | Migration decision |
|---|---|---|---|---|
| `views/modes.js` | Modes Map **3D** scatter | echarts-gl | `scatter3D`, `grid3D`, `xAxis3D/yAxis3D/zAxis3D`, `viewControl.autoRotate` | **Keep `echarts-gl` npm pkg** (only GL consumer; heavy → lazy-load via `import('echarts-gl')` on tab open). Forces echarts **v5**. |
| `views/modes.js` | Modes Map 2D fallback | echarts | `scatter` | npm `echarts` — trivial. |
| `views/loop.js` | Label-event timeline | echarts | `scatter` on `time` x / `category` y | npm `echarts` — trivial. |
| `views/loop.js` | Daily metrics | echarts | stacked `line` + areaStyle, legend | npm `echarts` — trivial. |
| `views/drain.js` | Drain3 token icicle | **d3** (`hierarchy`, `partition`, `scaleSequential`, `interpolateBlues`) | static SVG, native tooltips | **Replace with ECharts `custom` series** rendering the icicle rectangles; the prefix-tree + partition layout math is simple plain TS (no d3 needed). Depth color: interpolate blues in TS. d3 dropped. |
| `views/groups.js` | Launch Groups force graph | **d3** (`forceSimulation`, `zoom`, `drag`) | see inventory §6 | **Replace with ECharts `graph` series, `layout: 'force'`** — `roam: true` replaces zoom/pan, `draggable: true` replaces node drag. Known fidelity losses (accepted): per-group `forceX` gravity wells → approximate with `categories` + per-group initial positions (seed x by group index before layout); auto-fit-until-touched → not replicated (roam + a "fit" button that re-runs `setOption`); in-node RP hyperlinks → move into the HTML tooltip (`enterable: true`, like modes). d3 dropped. |
| journey/signatures/llm/rubric | — | none | DOM/tables only | ui-kit + custom components only. |
| journey Decision gauge | custom SVG | none | hand-rolled arc geometry | **Keep as a hand-rolled React SVG component** (pure geometry; not a chart-library item). |

**Bottom line**: `npm i echarts@5.6 echarts-gl@2.0.9` covers everything; d3 is eliminated entirely. The shared `echartsBase()` theme becomes one theme module used by an `<EChart>` React wrapper (init, setOption, ResizeObserver-driven resize, dispose on unmount).

## 4. Realtime
None. Plain HTTP polling only. Dev proxy config is plain `/api` forwarding.
