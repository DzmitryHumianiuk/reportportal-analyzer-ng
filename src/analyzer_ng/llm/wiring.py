"""Pipeline ↔ sidecar wiring (spec 04 §1.5): fact_loader, applier, factory.

The :class:`~analyzer_ng.llm.manager.LlmSidecar` is deliberately free of pipeline
coupling — it takes a ``fact_loader`` (re-read DB facts by ``(project_id,
item_id)``) and an ``applier`` (turn a validated role output into ``suggestion``
writes) as callables. This module builds those callables over the concrete PG
stores and the pure :mod:`~analyzer_ng.llm.apply` row-update functions, plus the
feature-time extractor lookup (spec 04 §4.2) and a flag-gated :func:`build_sidecar`
factory the service uses at startup.

Everything here is per-project by construction: every fact read and every write
carries ``project_id`` (§5.4). The worker re-reads facts fresh and never trusts the
enqueue payload (§1.5).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from psycopg_pool import ConnectionPool

from analyzer_ng.config import AppConfig
from analyzer_ng.core.decision import default_locator
from analyzer_ng.core.features import LLM_UNKNOWN
from analyzer_ng.db.repositories.llm_cache import PgLlmCacheStore
from analyzer_ng.db.repositories.llm_events import PgLlmEventStore, PgLlmRoleStateStore
from analyzer_ng.db.repositories.suggestion_ops import PgLlmFacts, PgSuggestionOps
from analyzer_ng.llm.apply import (
    apply_coldstart,
    apply_explainer,
    apply_judge,
)
from analyzer_ng.llm.engine import RoleResult
from analyzer_ng.llm.manager import LlmSidecar
from analyzer_ng.llm.roles.extractor import extractor_template_hash

logger = logging.getLogger(__name__)

_EXTRACTOR_TTL_DAYS = 90


def _fact_block(sig: dict) -> dict[str, Any]:
    """Render the DB-derived fact block for a role prompt (spec 04 §3.1)."""
    return {
        "exception": sig.get("exc_text") or "",
        "top_frames": list(sig.get("top_frames") or [])[:8],
        "status_codes": [str(c) for c in (sig.get("status_codes") or [])][:8],
    }


def _log_excerpt(sig: dict) -> str:
    """Best-available masked excerpt (analyzer-ng aggregates logs into the signature)."""
    parts = [p for p in (sig.get("msg_text"), sig.get("frames_text")) if p]
    return "\n".join(parts)


class PgLlmFactLoader:
    """Builds a role's input dict by re-reading facts from PG (§1.5)."""

    def __init__(
        self,
        facts: PgLlmFacts,
        *,
        is_cold: Callable[[int], bool] | None = None,
    ) -> None:
        self._facts = facts
        self._is_cold = is_cold

    def __call__(
        self, role: str, project_id: int, item_id: int, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        sig = self._facts.load_signature(project_id, item_id)
        if sig is None:
            return None  # item deleted / superseded since enqueue
        fact_block = _fact_block(sig)
        excerpt = _log_excerpt(sig)
        error_hash = sig.get("error_hash") or 0
        if role == "explainer":
            return self._explainer_input(project_id, item_id, sig, fact_block, excerpt)
        if role == "extractor":
            return {
                "fact_block": fact_block,
                "log_excerpt": excerpt,
                "exception_fp": sig.get("exception_fp") or 0,
                "template_ids": list(sig.get("template_ids") or []),
            }
        if role == "coldstart":
            if self._is_cold is not None and not self._is_cold(project_id):
                return None  # project is no longer cold → skip (spec §4.4)
            return {
                "error_hash": error_hash,
                "fact_block": fact_block,
                "log_excerpt": excerpt,
                "launch_id": payload.get("launch_id") or sig.get("launch_id") or 0,
                "group_locator": payload.get("group_locator", "ti001"),
            }
        if role == "judge":
            return self._judge_input(project_id, sig, fact_block, excerpt, payload)
        return None

    def _explainer_input(
        self, project_id: int, item_id: int, sig: dict, fact_block: dict, excerpt: str
    ) -> dict[str, Any] | None:
        sug = self._facts.latest_suggestion(project_id, item_id)
        if sug is None or sug.get("predicted_label") in (None, "ti"):
            return None  # nothing shown to explain (§4.1 skips pure abstains)
        return {
            "mode_title": sug.get("predicted_label") or "",
            "mode_label": sug.get("predicted_label") or "",
            "mode_support": 0,
            "mode_purity": 0.0,
            "match_signals": (sug.get("features") or {}).get("match_signals", "matched"),
            "fact_block": fact_block,
            "log_excerpt": excerpt,
            "mode_id": sug.get("matched_mode_id") or 0,
            "error_hash": sig.get("error_hash") or 0,
            "suggestion_id": sug.get("suggestion_id"),
        }

    def _judge_input(
        self, project_id: int, sig: dict, fact_block: dict, excerpt: str, payload: dict
    ) -> dict[str, Any] | None:
        refs = payload.get("candidates") or []
        # Load each candidate's DB facts fresh in the worker (§1.5) — the judge sees
        # real exception chain / top templates / top frames, never empty stubs (§4.3).
        candidates: list[dict[str, Any]] = []
        for ref in refs:
            cand_sig = self._facts.load_signature(project_id, int(ref["id"]))
            if cand_sig is None:
                continue  # candidate superseded / deleted since enqueue
            tmpl = (cand_sig.get("template_ids") or [])[:5]
            candidates.append(
                {
                    "id": ref["id"],
                    "label": ref["label"],
                    "similarity": ref["similarity"],
                    "exc_chain": cand_sig.get("exc_text") or "",
                    "templates": ", ".join(str(t) for t in tmpl),
                    "frames": ", ".join((cand_sig.get("top_frames") or [])[:5]),
                }
            )
        if len(candidates) < 2:
            return None  # judge needs ≥ 2 candidates with evidence (§4.3)
        return {
            "query_error_hash": sig.get("error_hash") or 0,
            "fact_block": fact_block,
            "query_excerpt": excerpt,
            "candidates": candidates,
        }


class PgLlmApplier:
    """Dispatches a validated role output to the concrete ``suggestion`` writes."""

    def __init__(self, ops: PgSuggestionOps, *, model_tag: str) -> None:
        self._ops = ops
        self._model_tag = model_tag

    def __call__(
        self,
        role: str,
        project_id: int,
        item_id: int,
        inp: dict[str, Any],
        result: RoleResult,
    ) -> None:
        if result.output is None:
            return
        if role == "explainer":
            sid = inp.get("suggestion_id")
            if sid is not None:
                apply_explainer(self._ops, project_id, int(sid), result.output)
        elif role == "judge":
            apply_judge(
                self._ops,
                project_id,
                item_id,
                candidates=inp.get("candidates", []),
                output=result.output,
                model=self._model_tag,
                prompt_hash=result.prompt_hash,
            )
        elif role == "coldstart":
            # §4.4: predicted_label is the default locator for the rubric's base label
            # (e.g. rubric 'si' → 'si001') — always inside the suggest band.
            group_locator = default_locator(result.output["label"])
            apply_coldstart(
                self._ops,
                project_id,
                item_id,
                int(inp.get("launch_id") or 0),
                output=result.output,
                group_locator=group_locator,
                model_tag=self._model_tag,
            )
        # extractor: cache-only, no suggestion row update (§4.2).


def build_extractor_feature_lookup(
    cache: PgLlmCacheStore,
) -> Callable[[int, int, Sequence[int]], tuple[str, str] | None]:
    """Feature-time extractor lookup (spec 04 §4.2), project-scoped.

    Returns ``(failing_layer, error_class)`` from the fresh extractor cache row for
    the item's template-set, or ``None`` on a miss (the caller uses the ``unknown``
    sentinel). Never crosses projects — the store filters by ``project_id``.
    """

    def lookup(
        project_id: int, exception_fp: int, template_ids: Sequence[int]
    ) -> tuple[str, str] | None:
        thash = extractor_template_hash(exception_fp, list(template_ids))
        try:
            out = cache.get_extractor_by_template(project_id, thash, _EXTRACTOR_TTL_DAYS)
        except Exception:  # noqa: BLE001 — a feature lookup must never fail a decision
            logger.exception("extractor feature lookup failed for project %s", project_id)
            return None
        if not out:
            return None
        return (out.get("failing_layer") or LLM_UNKNOWN, out.get("error_class") or LLM_UNKNOWN)

    return lookup


def build_sidecar(
    config: AppConfig,
    pool: ConnectionPool | None,
    *,
    metrics: Any | None = None,
    is_cold: Callable[[int], bool] | None = None,
) -> LlmSidecar:
    """Construct the sidecar, PG-wired when enabled (spec 04 §0 / §1.5).

    With ``ANALYZER_LLM_ENABLED=false`` the sidecar is inert (no client/queue/
    thread) and the fact_loader/applier are never invoked — so passing them is
    harmless and keeps the LLM-off path byte-identical. When enabled and a pool is
    present the loaders read/write the real ``suggestion``/cache rows.
    """
    cache = PgLlmCacheStore(pool) if pool is not None else None
    events = PgLlmEventStore(pool) if pool is not None else None
    role_state = PgLlmRoleStateStore(pool) if pool is not None else None
    fact_loader = None
    applier = None
    if pool is not None:
        loader = PgLlmFactLoader(PgLlmFacts(pool), is_cold=is_cold)
        applier = PgLlmApplier(PgSuggestionOps(pool), model_tag=config.analyzer_llm_model)
        fact_loader = loader
    return LlmSidecar(
        config,
        cache_store=cache,  # type: ignore[arg-type]
        event_store=events,  # type: ignore[arg-type]
        role_state_store=role_state,  # type: ignore[arg-type]
        metrics=metrics,
        fact_loader=fact_loader,
        applier=applier,
    )
