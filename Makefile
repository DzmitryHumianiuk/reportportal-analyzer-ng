.PHONY: install lint format test test-unit test-integration clean \
        ui-install ui-build ui-dev inspector-image inspector-overlay

VENV ?= .venv
PY ?= python3.12

# Inspector frontend (Vite + React). Its dist/ is gitignored and built here.
UI_DIR ?= inspector/frontend
# Tag to produce. Always a fresh one: the cluster pulls IfNotPresent.
INSPECTOR_TAG ?= dev
# Tag the cluster runs TODAY — the overlay's base. Building on a stale base
# silently drops everything that landed after it.
INSPECTOR_BASE ?= analyzer-inspector:ins47

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e '.[dev]'

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

# Full suite (unit + integration). Integration needs a running Docker daemon.
test:
	pytest

# Fast path: pure-python tests only, no Docker required.
test-unit:
	pytest -m "not integration"

# Integration tests: spin real containers via testcontainers (needs Docker).
test-integration:
	pytest -m integration

# ---- Inspector web UI ----

# Install frontend dependencies from the lockfile.
ui-install:
	cd $(UI_DIR) && npm ci

# Build the SPA into inspector/frontend/dist (what the backend serves).
ui-build:
	cd $(UI_DIR) && npm run build

# Dev server with hot reload; run the backend on port 5005 in another terminal.
ui-dev:
	cd $(UI_DIR) && npm run dev

# Full image. Builds the SPA inside docker, so it needs a networked daemon.
inspector-image: ui-build
	docker build -f inspector/Dockerfile -t analyzer-inspector:$(INSPECTOR_TAG) .

# Overlay image for minikube: no network needed, copies the host-built dist.
# Always goes through ui-build so the image can never carry a stale SPA.
#   make inspector-overlay INSPECTOR_BASE=analyzer-inspector:ins47 INSPECTOR_TAG=ins48
inspector-overlay: ui-build
	eval $$(minikube docker-env) && docker build -f Dockerfile.inspector-overlay \
	  --build-arg BASE=$(INSPECTOR_BASE) -t analyzer-inspector:$(INSPECTOR_TAG) .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache dist build
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
