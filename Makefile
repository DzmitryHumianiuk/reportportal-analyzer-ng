.PHONY: install lint format test clean

VENV ?= .venv
PY ?= python3.12

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e '.[dev]'

lint:
	ruff check .

format:
	ruff format .

test:
	pytest

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache dist build
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
