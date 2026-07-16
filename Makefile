.PHONY: install lint format test test-unit test-integration clean

VENV ?= .venv
PY ?= python3.12

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

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache dist build
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
