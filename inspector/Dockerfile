# analyzer-ng inspector — read-only companion (FastAPI + built SPA) in one image.
# Build context is the REPO ROOT so we can vendor the analyzer's versioned SQL:
#   docker build -f inspector/Dockerfile -t analyzer-inspector:<tag> .
#
# This build compiles the frontend itself, so it needs a docker daemon with
# network (npm + pip both download). The minikube VM has no outbound DNS — use
# Dockerfile.inspector-overlay there (see that file, or `make inspector-overlay`).

# ---- Stage 1: build the Vite + React SPA ----
FROM node:22-alpine AS ui
WORKDIR /ui
COPY inspector/frontend/package.json inspector/frontend/package-lock.json ./
RUN npm ci
COPY inspector/frontend/ ./
RUN npm run build

# ---- Stage 2: the service ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    INSPECTOR_HTTP_PORT=5005 \
    INSPECTOR_QUERIES_PATH=/app/analyzer_sql/queries.py

WORKDIR /app

# Light dependency set only (no drain3/lightgbm/pgvector in this image).
COPY inspector/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# The non-root user is created before the COPY steps: `COPY --chown=inspector`
# (here and in the overlay) fails when the name does not resolve yet.
RUN useradd --system --uid 10001 inspector

# App: backend + the built SPA.
COPY --chown=inspector:inspector inspector/backend /app/backend
COPY --from=ui --chown=inspector:inspector /ui/dist /app/frontend/dist

# Vendor the analyzer's *versioned* hybrid-retrieval SQL, byte-for-byte. It only
# imports the stdlib, so the loader executes it standalone (no analyzer_ng deps).
COPY --chown=inspector:inspector src/analyzer_ng/db/repositories/queries.py /app/analyzer_sql/queries.py
COPY --chown=inspector:inspector src/analyzer_ng/llm/rubric.py /app/analyzer_sql/rubric.py

RUN chown -R inspector:inspector /app
USER inspector

EXPOSE 5005

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5005/healthz',timeout=3).status==200 else 1)"

# create_app() reads INSPECTOR_ROOT_PATH (set to /inspector in k8s) and uses it
# for both FastAPI root_path and the injected <base href>, so the SPA and API
# work behind the ingress with no path rewriting at the proxy.
CMD ["sh", "-c", "exec uvicorn backend.app:app --host 0.0.0.0 --port ${INSPECTOR_HTTP_PORT}"]
