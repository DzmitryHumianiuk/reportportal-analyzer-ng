"""Load the analyzer's *versioned* hybrid-retrieval SQL as a library.

The inspector must reconstruct Stage-B/Stage-A retrieval with the **exact** SQL
the analyzer ships (spec 02 §5) — never a reimplementation. We load
``analyzer_ng/db/repositories/queries.py`` and reuse its ``STAGE_B_HYBRID_SQL``,
``STAGE_A_MODE_MATCH_SQL``, ``bind_numbered`` and ``HYBRID_RETRIEVAL_VERSION``.

To keep the inspector image dependency-light (no drain3/lightgbm/pgvector), we do
**not** import the ``analyzer_ng`` package tree (whose ``repositories/__init__``
pulls the heavy stores). Instead we execute the single ``queries.py`` file in
isolation via :mod:`importlib.util` — that module only imports the stdlib, so it
loads cleanly and stays byte-identical to what the analyzer runs.

Resolution order for the file:
1. ``INSPECTOR_QUERIES_PATH`` env var (set in the container to the copied file).
2. A normal package import (works in local dev where ``src`` is on ``sys.path``).
3. A walk up from this file to ``src/analyzer_ng/db/repositories/queries.py``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("INSPECTOR_QUERIES_PATH")
    if env:
        paths.append(Path(env))
    # Walk up from this file looking for the repo's src tree.
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "src" / "analyzer_ng" / "db" / "repositories" / "queries.py"
        if cand.exists():
            paths.append(cand)
    return paths


def load_queries() -> ModuleType:
    """Return the loaded ``queries`` module (real analyzer SQL)."""
    # Prefer a normal import when the package is installed / on the path.
    try:  # pragma: no cover - exercised in local dev only
        from analyzer_ng.db.repositories import queries as mod  # type: ignore

        return mod
    except Exception:
        pass

    for path in _candidate_paths():
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location("analyzer_queries_vendored", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    raise RuntimeError(
        "Could not locate analyzer queries.py. Set INSPECTOR_QUERIES_PATH to the "
        "path of analyzer_ng/db/repositories/queries.py."
    )
