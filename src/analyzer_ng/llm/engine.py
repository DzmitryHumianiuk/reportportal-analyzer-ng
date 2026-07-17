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
        outcome, output, latency = self._attempt(role, inp, schema, seed=7)
        if outcome in ("schema_fail", "validation_fail"):
            # §3.0 step 6: retry once (same prompt shape, seed 8), then drop.
            outcome, output, latency = self._attempt(role, inp, schema, seed=8)

        if outcome == "ok" and output is not None:
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
        self, role: Role, inp: dict[str, Any], schema: dict[str, Any], seed: int
    ) -> tuple[str, dict[str, Any] | None, int | None]:
        system, user = role.build_prompt(inp, new_nonce())
        try:
            resp = self._client.chat(
                system=system,
                user=user,
                schema=schema,
                num_predict=role.num_predict,
                seed=seed,
            )
        except LlmTransportError:
            self._breaker.record_failure()
            return ("timeout", None, None)

        # A well-formed HTTP response — the sidecar is up, so the breaker heals even
        # if the body is bad (schema failures follow the retry/drop path, §1.4).
        self._breaker.record_response()

        if resp.tool_calls:  # §5.3: any tool_calls ⇒ schema failure, drop.
            return ("schema_fail", None, resp.latency_ms)
        try:
            parsed = json.loads(resp.content)
        except (json.JSONDecodeError, TypeError):
            return ("schema_fail", None, resp.latency_ms)
        if not is_valid(parsed, schema):
            return ("schema_fail", None, resp.latency_ms)
        if not role.post_validate(parsed, inp):
            return ("validation_fail", None, resp.latency_ms)
        return ("ok", parsed, resp.latency_ms)

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
