"""Generic role execution: request → validate → cache → store (spec 04 §3.0).

This is the single flow every role runs through: cache lookup (per-project,
read-time freshness), constrained-decoding call through the circuit breaker,
client-side schema + role post-validation, retry-once-then-drop, then cache upsert
and ``llm_event`` bookkeeping. It never trusts the server-side ``format``
constraint alone and never stores partial output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from analyzer_ng.llm.breaker import CircuitBreaker
from analyzer_ng.llm.client import LlmTransportError, OllamaClient
from analyzer_ng.llm.roles.base import Role, sha256_hex
from analyzer_ng.llm.sanitizer import new_nonce
from analyzer_ng.llm.schema import is_valid


class _CacheStore(Protocol):
    def get_fresh(self, project_id: int, cache_key: str, ttl_days: int) -> dict | None: ...
    def put(
        self,
        project_id: int,
        cache_key: str,
        role: str,
        model: str,
        output: dict,
        template_hash: int | None = None,
    ) -> None: ...


class _EventStore(Protocol):
    def record(
        self,
        *,
        project_id: int,
        item_id: int,
        role: str,
        model: str,
        prompt_hash: str,
        cache_hit: bool,
        outcome: str,
        output: dict | None = None,
        latency_ms: int | None = None,
    ) -> int: ...


class _Metrics(Protocol):
    def observe_llm_call(self, role: str, outcome: str, latency_ms: int | None) -> None: ...


@dataclass(frozen=True)
class RoleResult:
    outcome: str  # ok | schema_fail | validation_fail | timeout | breaker_open
    output: dict[str, Any] | None
    cache_hit: bool
    prompt_hash: str


# --------------------------------------------------------------------------- #
# Masked-truncation screen (§3.0 step 6b).
#
# Constrained decoding hides a token-budget cut: when num_predict runs out mid
# free-text field, the grammar closes the open JSON string and emits the trailing
# schema fields, so the body validates and a mid-sentence fragment flows through
# to the UI (live: coldstart reason for item 3688, "...the exception is a"). The
# ONLY reliable signal is the server's finish/stop reason == "length"; the
# punctuation heuristic then confirms the text lacks a clean ending. The screen is
# deliberately conservative — it fires only when BOTH hold — so a legitimately
# short field that happens to hit the cap at a sentence boundary is left alone,
# and non-English text (whose terminals include the CJK set below) is tolerated.
# --------------------------------------------------------------------------- #
_TERMINAL_CHARS = ".!?…。！？"  # sentence terminals incl. common CJK
_TRAILING_CLOSERS = "\"'”’」』）)]}"  # closing quotes/brackets that may follow a terminal
_TRUNCATION_MARKER = " …"  # honest trailing marker for a cut we could not extend away


def _ends_complete(text: str) -> bool:
    """True if ``text`` ends like a finished sentence (or is blank)."""
    stripped = text.rstrip()
    if not stripped:
        return True  # blank/empty is not a *mid-sentence* cut
    core = stripped.rstrip(_TRAILING_CLOSERS)
    if not core:
        return True  # only closers/quotes — nothing to judge, don't false-positive
    return core[-1] in _TERMINAL_CHARS


def _looks_truncated(text: str, finish_reason: str | None) -> bool:
    """A schema-valid field is a masked cut only if the cap was hit AND it ends mid-sentence."""
    if finish_reason != "length":
        return False  # server stopped on its own (or didn't say) → trust the text
    return not _ends_complete(text)


def _truncated_fields(role: Role, output: dict[str, Any], finish_reason: str | None) -> list[str]:
    fields = []
    for name in getattr(role, "free_text_fields", ()):
        value = output.get(name)
        if isinstance(value, str) and _looks_truncated(value, finish_reason):
            fields.append(name)
    return fields


def _mark_truncated(output: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Append an honest ``…`` marker to still-cut fields and flag the payload.

    ``truncated`` rides in the jsonb output (no schema change) so the UI/audit can
    see the rationale was cut; the outcome stays ``ok`` (a usable, if clipped,
    answer). Never silently persist a mid-sentence cut.
    """
    marked = dict(output)
    for name in fields:
        value = marked.get(name)
        if isinstance(value, str):
            marked[name] = f"{value.rstrip()}{_TRUNCATION_MARKER}"
    marked["truncated"] = True
    return marked


class LlmEngine:
    def __init__(
        self,
        *,
        client: OllamaClient,
        breaker: CircuitBreaker,
        cache_store: _CacheStore,
        event_store: _EventStore,
        model: str,
        metrics: _Metrics | None = None,
    ) -> None:
        self._client = client
        self._breaker = breaker
        self._cache = cache_store
        self._events = event_store
        self._model = model
        self._metrics = metrics

    def run(self, role: Role, project_id: int, item_id: int, inp: dict[str, Any]) -> RoleResult:
        prompt_hash = sha256_hex(role.content_key(inp))
        # §3.0 step 2: cache_key = sha256(model || role || prompt_hash).
        cache_key = sha256_hex(f"{self._model}|{role.name}|{prompt_hash}")

        cached = self._cache.get_fresh(project_id, cache_key, role.ttl_days)
        if cached is not None:
            return self._finish(role, project_id, item_id, prompt_hash, "ok", cached, True, 0)

        if not self._breaker.allow():
            return self._finish(
                role, project_id, item_id, prompt_hash, "breaker_open", None, False, None
            )

        schema = self._schema_for(role, inp)
        outcome, output, latency, finish = self._attempt(role, inp, schema, seed=7)
        if outcome in ("schema_fail", "validation_fail"):
            # §3.0 step 6: retry once (same prompt shape, seed 8), then drop.
            outcome, output, latency, finish = self._attempt(role, inp, schema, seed=8)

        if outcome == "ok" and output is not None:
            # §3.0 step 6b: a schema-valid body may still hide a masked truncation of
            # a free-text field. Screen, and (if cut) retry once with a doubled budget
            # before persisting — marking any residual cut honestly.
            output = self._resolve_truncation(role, inp, schema, output, finish)
            self._cache.put(
                project_id,
                cache_key,
                role.name,
                self._model,
                output,
                role.template_hash(inp),
            )
        return self._finish(role, project_id, item_id, prompt_hash, outcome, output, False, latency)

    @staticmethod
    def _schema_for(role: Role, inp: dict[str, Any]) -> dict[str, Any]:
        schema_for = getattr(role, "schema_for", None)
        return schema_for(inp) if callable(schema_for) else role.schema

    def _attempt(
        self,
        role: Role,
        inp: dict[str, Any],
        schema: dict[str, Any],
        seed: int,
        num_predict: int | None = None,
    ) -> tuple[str, dict[str, Any] | None, int | None, str | None]:
        system, user = role.build_prompt(inp, new_nonce())
        try:
            resp = self._client.chat(
                system=system,
                user=user,
                schema=schema,
                num_predict=role.num_predict if num_predict is None else num_predict,
                seed=seed,
            )
        except LlmTransportError:
            self._breaker.record_failure()
            return ("timeout", None, None, None)

        # A well-formed HTTP response — the sidecar is up, so the breaker heals even
        # if the body is bad (schema failures follow the retry/drop path, §1.4).
        self._breaker.record_response()

        if resp.tool_calls:  # §5.3: any tool_calls ⇒ schema failure, drop.
            return ("schema_fail", None, resp.latency_ms, resp.finish_reason)
        try:
            parsed = json.loads(resp.content)
        except (json.JSONDecodeError, TypeError):
            return ("schema_fail", None, resp.latency_ms, resp.finish_reason)
        if not is_valid(parsed, schema):
            return ("schema_fail", None, resp.latency_ms, resp.finish_reason)
        if not role.post_validate(parsed, inp):
            return ("validation_fail", None, resp.latency_ms, resp.finish_reason)
        return ("ok", parsed, resp.latency_ms, resp.finish_reason)

    def _resolve_truncation(
        self,
        role: Role,
        inp: dict[str, Any],
        schema: dict[str, Any],
        output: dict[str, Any],
        finish_reason: str | None,
    ) -> dict[str, Any]:
        """Screen a schema-valid body for a masked free-text cut; retry-or-mark (§3.0 6b).

        Detection: :func:`_truncated_fields`. On a hit, one retry with a doubled
        num_predict (same seed/prompt shape); if that comes back clean, use it. If
        the retry is still cut (or failed to produce a body), keep the longer text
        available and persist it with an explicit ``…`` marker plus a ``truncated``
        flag — never a silent mid-sentence cut.
        """
        fields = _truncated_fields(role, output, finish_reason)
        if not fields:
            return output

        r_outcome, r_output, _r_latency, r_finish = self._attempt(
            role, inp, schema, seed=7, num_predict=role.num_predict * 2
        )
        if r_outcome == "ok" and r_output is not None:
            r_fields = _truncated_fields(role, r_output, r_finish)
            if not r_fields:
                return r_output  # doubled budget finished the sentence
            return _mark_truncated(r_output, r_fields)  # still cut → mark the longer text
        return _mark_truncated(output, fields)  # retry unusable → mark the original

    def _finish(
        self,
        role: Role,
        project_id: int,
        item_id: int,
        prompt_hash: str,
        outcome: str,
        output: dict[str, Any] | None,
        cache_hit: bool,
        latency_ms: int | None,
    ) -> RoleResult:
        self._events.record(
            project_id=project_id,
            item_id=item_id,
            role=role.name,
            model=self._model,
            prompt_hash=prompt_hash,
            cache_hit=cache_hit,
            outcome=outcome,
            output=output if outcome == "ok" else None,
            latency_ms=latency_ms,
        )
        if self._metrics is not None:
            self._metrics.observe_llm_call(role.name, outcome, latency_ms)
        return RoleResult(outcome, output, cache_hit, prompt_hash)
