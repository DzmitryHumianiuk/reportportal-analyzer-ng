"""Load the analyzer's cold-start rubric as a library.

Same reasoning as :mod:`queries_loader`: the rules a reader is shown must be the
rules the analyzer applied, so this loads the analyzer's own
``analyzer_ng/llm/rubric.py`` rather than keeping a second copy that could drift
the moment a rule is added.

``rubric.py`` imports nothing beyond the standard library, so it can be executed
in isolation without pulling the analyzer's heavy dependencies into this image.

Resolution order for the file:
1. ``INSPECTOR_RUBRIC_PATH`` env var (set in the container to the copied file).
2. A normal package import (works in local dev where ``src`` is on ``sys.path``).
3. A walk up from this file to ``src/analyzer_ng/llm/rubric.py``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("INSPECTOR_RUBRIC_PATH")
    if env:
        paths.append(Path(env))
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "src" / "analyzer_ng" / "llm" / "rubric.py"
        if cand.exists():
            paths.append(cand)
        sibling = parent / "analyzer_sql" / "rubric.py"
        if sibling.exists():
            paths.append(sibling)
    return paths


def load_rubric() -> ModuleType | None:
    """Return the loaded rubric module, or None when it cannot be located.

    None rather than an exception: the rubric page is a reference, and the rest
    of the Inspector must not fail to start because it is missing.
    """
    try:  # pragma: no cover - exercised in local dev only
        from analyzer_ng.llm import rubric as mod  # type: ignore

        return mod
    except Exception:
        pass

    for path in _candidate_paths():
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location("analyzer_rubric_vendored", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


# Short labels for the four defect groups the rubric can produce, so the page can
# say "System Issue" rather than "si".
_LABEL_NAME = {
    "pb": "Product Bug",
    "ab": "Automation Bug",
    "si": "System Issue",
    "nd": "No Defect",
}


def _pattern_only(when: str) -> str:
    """The rule's pattern, without the verdict tail the prompt line carries."""
    text = " ".join(when.split())
    head = text.split("->")[0].strip()
    return head.rstrip(" ,;") or text


def rubric_rows() -> list[dict[str, Any]]:
    """The rubric as a list of rows for the API, in the order the model reads it.

    Order matters and is part of the contract: the model applies the FIRST rule
    that matches, so a reader arguing with a verdict needs to see what came
    before it.
    """
    mod = load_rubric()
    if mod is None:
        return []
    rows: list[dict[str, Any]] = []
    for index, (rule_id, rule) in enumerate(mod.RUBRIC.items(), start=1):
        rows.append(
            {
                "order": index,
                "rule": rule_id,
                "name": rule.name,
                "label": rule.label,
                "label_name": _LABEL_NAME.get(rule.label, rule.label),
                "confidence": rule.confidence,
                # The prompt line itself, so the page shows what the model was
                # actually told rather than a paraphrase of it. Its trailing
                # "-> label, confidence" is dropped: the verdict has its own
                # column and saying it twice reads as two different facts.
                "when": _pattern_only(rule.when),
            }
        )
    return rows
