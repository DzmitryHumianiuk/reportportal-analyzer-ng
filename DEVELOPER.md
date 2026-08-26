# DEVELOPER — build and run analyzer-ng locally

This guide takes a developer from a fresh clone to a running system. Read it top
to bottom once. Two paths are offered:

- **Path A** runs the analyzer and the inspector against local Postgres and
  RabbitMQ. Fastest inner loop for working on the analyzer or inspector code.
- **Path B** brings up a full ReportPortal stand so you can see the analyzer
  triage real failures and use the patched Make Decision UI end to end.

## Repositories

The work is split across three repositories, all under the same owner:

| Repository | Branch | What it holds |
|---|---|---|
| `reportportal-analyzer-ng` | `develop` | The analyzer service, the inspector UI, the `rp-migrate` tool, and the ReportPortal patch files. This repo. |
| `service-ui` | `analyzer-ng/5.15.3-bench` | ReportPortal's frontend with the "Bench" Make Decision UI already applied over the `5.15.3` tag. Clone this branch and build it, no manual patching needed. |
| `reportportal-kubernetes` | `analyzer-ng-minikube` | Minikube manifests and an install guide for a full local ReportPortal stand with analyzer-ng wired in. |

## How the pieces fit together

```
  ReportPortal  <--(AMQP: analyzer exchange)-->  analyzer-ng
       |                                              |
       |                                              +--> PostgreSQL + pgvector   (its own `analyzer` DB)
       |                                              +--> Ollama LLM              (optional sidecar)
       |
  service-ui (patched)  --- reads analyzer suggest data in the Make Decision modal
                                                      ^
  inspector  --- read-only web UI over the analyzer `analyzer` DB -----------------+
```

analyzer-ng keeps the same RabbitMQ exchange and queue names as the legacy
`service-auto-analyzer`, so ReportPortal talks to it with no core change. It
stores everything in its own `analyzer` database and never touches the
`reportportal` database.

## Prerequisites

- Python 3.12
- Docker with the Compose plugin (`docker compose ...`)
- Node 20 (only for building `service-ui`; Node 22 also works)
- For Path B on Kubernetes: `minikube` and `kubectl`

---

## Path A — analyzer + inspector on your machine

This boots the service against local backing services. The service starts,
creates its schema, and serves `/health`. To actually analyze failures you need
a ReportPortal feeding the queue (Path B); Path A is for developing the service
and the inspector against a live schema.

### 1. Start the backing services

```bash
docker compose -f docker-compose.dev.yml up -d
```

This gives you:

- PostgreSQL 16 with pgvector on `localhost:5432` (user / password / db all
  `analyzer`)
- RabbitMQ on `localhost:5672`, management UI on `localhost:15672`, vhost
  `analyzer` (user / password `analyzer`)

### 2. Run the analyzer

```bash
make install          # create .venv and install the package with dev extras
source .venv/bin/activate

export ANALYZER_PG_DSN="postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer"
export AMQP_URL="amqp://analyzer:analyzer@127.0.0.1:5672/analyzer"

python -m analyzer_ng.main
```

Check it is up:

```bash
curl -s http://127.0.0.1:5001/health
# {"ready":true,"pg":true,"amqp":true,...}
```

Other runtime toggles (the LLM kill switch, role settings) are listed in
[docs/OPERATIONS.md](docs/OPERATIONS.md). The full list of environment variables
lives in `src/analyzer_ng/config.py`.

### 3. Run the inspector

The inspector is a read-only FastAPI app over the analyzer database, plus a web
UI: a React + TypeScript app (Vite, `@reportportal/ui-kit`) in
`inspector/frontend`. Node 22 (`inspector/frontend/.nvmrc`).

The backend serves the built UI from `inspector/frontend/dist`. That folder is a
build artifact and is **not** in git, so build it once before you start:

```bash
make ui-install     # npm ci, first time only
make ui-build       # writes inspector/frontend/dist
```

Backend from a venv:

```bash
python -m venv .venv-inspector && source .venv-inspector/bin/activate
pip install -r inspector/requirements.txt

export INSPECTOR_PG_DSN="postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer"
# Build context is the repo root so the versioned SQL can be vendored:
export INSPECTOR_QUERIES_PATH="src/analyzer_ng/db/repositories/queries.py"

uvicorn backend.app:app --app-dir inspector --host 0.0.0.0 --port 5005
# open http://127.0.0.1:5005
```

Without `dist/` the API still works, only the page at `/` is missing (404).

#### Working on the web UI

Two terminals. Terminal 1 runs the backend as above (no `INSPECTOR_ROOT_PATH`).
Terminal 2 runs the dev server with hot reload:

```bash
make ui-dev         # http://127.0.0.1:5173
```

The dev server proxies `/api` and `/healthz` to port 5005, so there is no CORS
setup and no rebuild between edits. Frontend checks:

```bash
cd inspector/frontend
npm test            # vitest
npm run typecheck   # tsc --noEmit
npm run build       # typecheck + production build
```

As an image (build context is the repo root, on purpose):

```bash
make inspector-image INSPECTOR_TAG=dev       # needs a docker daemon with network
docker run --rm -p 5005:5005 \
  -e INSPECTOR_PG_DSN="postgresql://analyzer:analyzer@host.docker.internal:5432/analyzer" \
  analyzer-inspector:dev
```

For minikube, use the overlay image: the VM has no outbound DNS, so `npm ci`
and `pip install` cannot run there. It copies the SPA you built on the host.
Always build it through make, never `docker build` by hand, so the image can
never ship a stale `dist/`:

```bash
make inspector-overlay INSPECTOR_BASE=analyzer-inspector:ins47 INSPECTOR_TAG=ins48
kubectl set image deployment/analyzer-inspector inspector=analyzer-inspector:ins48
```

Pass the tag the cluster runs today as `INSPECTOR_BASE`. Building on an older
base silently drops everything that landed after it.

Optional: set `INSPECTOR_RP_PG_DSN` to ReportPortal's own Postgres so the
inspector shows real project names and defect-type names instead of raw
locators. Unset means it falls back to raw locators.

### 4. Tests

```bash
make test            # full suite (integration tests spin their own containers)
make test-unit       # unit only, no containers
make lint
```

The integration tests use testcontainers and do not depend on
`docker-compose.dev.yml`.

---

## Path B — full ReportPortal stand

Pick one of the two hosts.

### Option 1: minikube (recommended for a clean local stand)

Use the `reportportal-kubernetes` fork, branch `analyzer-ng-minikube`. It ships
the manifests (`deploy/minikube/` in that repo) and a step-by-step install
guide. Follow that guide; it brings up ReportPortal, the analyzer, the Postgres
with pgvector, and the optional Ollama sidecar.

### Option 2: drop into an existing ReportPortal docker-compose

If you already run ReportPortal with Compose, follow
[docs/INSTALL.md](docs/INSTALL.md). In short: remove the `opensearch` and
`analyzer-train` services, give the Postgres superuser a password and mount
`docker/pg-init/01-analyzer-db.sh` so pgvector is created, then apply the
overlay `docker-compose.analyzer-ng.yml`.

### Build the patched service-ui

The Make Decision "Bench" UI is a ReportPortal-side change. The `service-ui`
fork branch already has it applied over `5.15.3`, so just build that:

```bash
git clone --branch analyzer-ng/5.15.3-bench \
  https://github.com/DzmitryHumianiuk/service-ui.git
cd service-ui/app
npm ci --legacy-peer-deps
NODE_OPTIONS="--max-old-space-size=4096" npm run build
# build output is in app/build/
```

Package it as a thin overlay over the stock image so the version footer stays
`5.15.3` (see [docs/rp-patches/README-bench.md](docs/rp-patches/README-bench.md)
for the Dockerfile and the minikube rollout commands). If you prefer to apply
the patch by hand to a clean `5.15.3` checkout instead of using the fork branch,
the patch files and full notes are in
[docs/rp-patches/](docs/rp-patches/) (`README-bench.md` and `README-PATCH.md`).

The optional `service-api` "warn context" change ships as a patch only, in
[docs/rp-patches/](docs/rp-patches/) (`README-service-api-5.15.2-warn-context.md`).

### Feed it data and see the full loop

To copy launches and defect labels from a source ReportPortal into your stand
(so the analyzer has something to learn from), use `tools/rp-migrate/` (copy
`.env.example` to `.env` and fill in your instances). For the full end-to-end
scenario (import, auto-analysis, Make Decision, label feedback) see
[docs/G5-WALKTHROUGH.md](docs/G5-WALKTHROUGH.md) and `scripts/g5/`.

---

## Ports at a glance

| Service | Port | Notes |
|---|---|---|
| analyzer-ng | 5001 | `/health` |
| inspector | 5005 | `/healthz`, web UI at `/` |
| PostgreSQL + pgvector | 5432 | dev compose: `analyzer/analyzer/analyzer` |
| RabbitMQ | 5672 | AMQP; vhost `analyzer` |
| RabbitMQ management UI | 15672 | `analyzer/analyzer` |
