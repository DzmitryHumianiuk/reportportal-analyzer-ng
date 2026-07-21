"""LLM sidecar facade (spec 04): the one object the pipeline talks to.

With ``ANALYZER_LLM_ENABLED=false`` (the default) construction is inert — no
client, no breaker, no queue, no thread — and :meth:`enqueue` is a no-op, so no
code path touches Ollama and behavior is byte-identical to a build without the
sidecar (§0). When enabled it owns the client, circuit breaker, single-worker
queue, the four roles and the execution engine, and exposes :meth:`probe` /
:meth:`health` for startup and ``GET /health``.

Fact-gathering from PG and the concrete suggestion row-updates are injected as
callables (``fact_loader`` / ``appliers``) so the pipeline wires them in; the
sidecar itself stays free of pipeline coupling. Kill-switch reads
(``llm_role_state``) are honored here; the nightly eval that *writes* them is T4.2.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from tenacity import retry, stop_after_attempt, wait_fixed

from analyzer_ng.config import AppConfig
from analyzer_ng.db.repositories.protocols import (
    LlmCacheStore,
    LlmEventStore,
    LlmRoleStateStore,
)
from analyzer_ng.llm.breaker import BreakerState, CircuitBreaker
from analyzer_ng.llm.client import OllamaClient
from analyzer_ng.llm.engine import LlmEngine, RoleResult
from analyzer_ng.llm.queue import LLMJob, LlmQueue
from analyzer_ng.llm.roles import (
    AbstainExplainerRole,
    ColdStartRole,
    ExplainerRole,
    ExtractorRole,
    JudgeRole,
    Role,
)

logger = logging.getLogger(__name__)

_REPROBE_INTERVAL_S = 300.0

# fact_loader: (role, project_id, item_id, payload) -> input dict, or None to skip.
FactLoader = Callable[[str, int, int, dict[str, Any]], dict[str, Any] | None]
# applier: (role, project_id, item_id, payload, result) -> None.
Applier = Callable[[str, int, int, dict[str, Any], RoleResult], None]


class LlmSidecar:
    def __init__(
        self,
        config: AppConfig,
        *,
        cache_store: LlmCacheStore,
        event_store: LlmEventStore,
        role_state_store: LlmRoleStateStore,
        metrics: Any | None = None,
        client: OllamaClient | None = None,
        fact_loader: FactLoader | None = None,
        applier: Applier | None = None,
    ) -> None:
        self.enabled = config.analyzer_llm_enabled
        self.model = config.analyzer_llm_model
        self._config = config
        self._metrics = metrics
        self._role_state = role_state_store
        self._fact_loader = fact_loader
        self._applier = applier
        self._available = False
        self._reason: str | None = "disabled"

        self._role_enabled = {
            "explainer": config.analyzer_llm_explainer,
            # Abstain explanations reuse the explainer flag (extension 2026-07-20).
            "abstain_explainer": config.analyzer_llm_explainer,
            "extractor": config.analyzer_llm_extractor,
            "judge": config.analyzer_llm_judge,
            "coldstart": config.analyzer_llm_coldstart,
        }
        self._roles: dict[str, Role] = {
            "explainer": ExplainerRole(),
            "abstain_explainer": AbstainExplainerRole(),
            "extractor": ExtractorRole(),
            "judge": JudgeRole(),
            "coldstart": ColdStartRole(),
        }

        if not self.enabled:
            # §0: with the flag off, construct nothing that could reach Ollama.
            self._client = None
            self._breaker = None
            self._engine = None
            self._queue = None
            self._reason = "disabled"
            return

        self._reason = "starting"
        self._client = client or OllamaClient(
            config.ollama_url,
            self.model,
            api=config.analyzer_llm_api,
            timeout_s=config.analyzer_llm_timeout_s,
            num_ctx=config.analyzer_llm_num_ctx,
        )
        self._breaker = CircuitBreaker(on_state_change=self._on_breaker_change)
        self._engine = LlmEngine(
            client=self._client,
            breaker=self._breaker,
            cache_store=cache_store,
            event_store=event_store,
            model=self.model,
            metrics=metrics,
        )
        self._queue = LlmQueue(
            self._process,
            maxsize=config.analyzer_llm_queue_max,
            on_drop=self._on_drop,
        )

    # -- lifecycle -------------------------------------------------------- #
    def start(self) -> None:
        if self.enabled and self._queue is not None:
            self._queue.start()

    def stop(self) -> None:
        if self._queue is not None:
            self._queue.stop()
        if self._client is not None:
            self._client.close()

    def probe(self) -> bool:
        """Startup/periodic capability probe (§1.3). Never blocks, never pulls."""
        if not self.enabled or self._client is None:
            self._available = False
            return False
        try:
            result = self._probe_once()
        except Exception:  # noqa: BLE001 — degrade, never crash startup
            result = None
        if result is None or not result.reachable:
            self._available = False
            self._reason = "unreachable"
            return False
        if not result.model_present:
            self._available = False
            self._reason = "model_missing"
            logger.warning(
                "LLM model '%s' not found in Ollama. Run: docker compose exec "
                "ollama ollama pull %s. LLM roles disabled until available.",
                self.model,
                self.model,
            )
            return False
        try:
            self._client.warmup()
        except Exception:  # noqa: BLE001 — warmup failure is non-fatal
            pass
        self._available = True
        self._reason = None
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0), reraise=True)
    def _probe_once(self) -> Any:
        assert self._client is not None
        return self._client.probe()

    # -- production ------------------------------------------------------- #
    def role_enabled(self, role: str) -> bool:
        """True when the master switch AND the role's config flag are on (spec 04 §6).

        The static gate consulted by the synchronous read path (e.g. the suggest
        route surfacing a cold-start rubric provisional). The per-project runtime
        kill-switch (``llm_role_state``) is honored only on the async ``_process``
        path, never on this cheap read.
        """
        return self.enabled and self._role_enabled.get(role, False)

    def enqueue(self, role: str, project_id: int, item_id: int, payload: dict) -> None:
        """Enqueue an async LLM job. No-op when the master switch or role is off."""
        if not self.enabled or self._queue is None:
            return
        if not self._role_enabled.get(role, False):
            return
        self._queue.enqueue(
            LLMJob(role=role, project_id=project_id, item_id=item_id, payload=payload)
        )

    def _process(self, job: LLMJob) -> None:
        if not self._available or self._engine is None:
            return  # roles silently disabled while unavailable (§1.3/§1.4)
        if not self._role_state.is_enabled(job.project_id, job.role):
            return  # per-project runtime kill-switch (§6)
        role = self._roles[job.role]
        inp = job.payload
        if self._fact_loader is not None:
            loaded = self._fact_loader(job.role, job.project_id, job.item_id, job.payload)
            if loaded is None:
                return  # facts gone (item deleted / superseded)
            inp = loaded
        result = self._engine.run(role, job.project_id, job.item_id, inp)
        if result.outcome == "ok" and self._applier is not None:
            self._applier(job.role, job.project_id, job.item_id, inp, result)

    # -- observability ---------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        """Cheap in-process state read for ``GET /health`` — never probes/blocks.

        Every field is a plain attribute or the breaker's in-memory state, so this
        stays sub-ms and safe to call on the health hot path. ``breaker_state`` is
        the honest live circuit signal (closed/open/half_open) the Inspector's
        "breaker: in-process, not in DB" caveat was working around; it is ``None``
        when the sidecar is disabled (no breaker constructed)."""
        return {
            "enabled": self.enabled,
            "available": self._available,
            "model": self.model,
            "reason": self._reason,
            "breaker_state": self._breaker.state.value if self._breaker is not None else None,
        }

    def _on_breaker_change(self, old: BreakerState, new: BreakerState) -> None:
        if new is BreakerState.OPEN:
            logger.warning("LLM circuit breaker opened (was %s); roles paused", old.value)
        if self._metrics is not None:
            self._metrics.set_breaker_state(new.value)

    def _on_drop(self, reason: str) -> None:
        if self._metrics is not None:
            self._metrics.observe_llm_drop(reason)
