"""Ollama HTTP client + capability probe (spec 04 §1.3, §2.3, §3.0 step 4).

Supports both the native Ollama ``/api/chat`` endpoint with a ``format`` schema
(constrained decoding) and the OpenAI-compatible ``/v1/chat/completions`` endpoint
with ``response_format`` json_schema (for vLLM/llama.cpp). ``think: false`` is sent
on every native request and, for ``qwen3*`` models, ``/no_think`` is appended as
the last system-prompt line (§2.3). A leading ``<think>…</think>`` block is
stripped before returning. Transport failures raise :class:`LlmTransportError`
(the only breaker-countable class); a well-formed HTTP response — even one whose
body fails schema validation later — is a healthy answer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

# Startup connect budget lives inside the per-request wall clock (§1.2).
_CONNECT_TIMEOUT_S = 2.0
_THINK_BLOCK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


class LlmTransportError(Exception):
    """A transport-level failure (timeout, connect error, 5xx, malformed body).

    This is the only failure class that counts toward the circuit breaker (§1.4).
    """


@dataclass(frozen=True)
class ChatResponse:
    content: str  # assistant text, ``<think>`` block already stripped
    tool_calls: list[Any] | None  # any tool_calls ⇒ role treats as schema failure (§5.3)
    latency_ms: int


@dataclass(frozen=True)
class ProbeResult:
    reachable: bool
    model_present: bool
    version: str | None = None


def _strip_think(text: str) -> str:
    """Drop a leading ``<think>…</think>`` block if the model emitted one (§2.3)."""
    return _THINK_BLOCK_RE.sub("", text, count=1)


def _is_qwen3(model: str) -> bool:
    return model.lower().startswith("qwen3")


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api: str = "ollama",
        timeout_s: float = 20.0,
        num_ctx: int = 4096,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api = api
        self._num_ctx = num_ctx
        timeout = httpx.Timeout(timeout_s, connect=_CONNECT_TIMEOUT_S)
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    # -- probe ------------------------------------------------------------- #
    def probe(self) -> ProbeResult:
        """Reachability + model-presence check. Never pulls (§1.3).

        Follows the configured API dialect: native Ollama servers are probed via
        ``/api/version`` + ``/api/tags``; OpenAI-compatible servers (llama.cpp
        ``llama-server``, vLLM, …) via the standard ``/v1/models`` listing —
        those servers do not implement the Ollama management endpoints."""
        if self._api == "openai":
            try:
                resp = self._client.get("/v1/models")
                resp.raise_for_status()
                names = {str(m.get("id", "")) for m in resp.json().get("data", [])}
            except (httpx.HTTPError, ValueError):
                return ProbeResult(reachable=False, model_present=False)
            present = self._model in names or f"{self._model}:latest" in names
            return ProbeResult(reachable=True, model_present=present, version=None)
        try:
            ver = self._client.get("/api/version")
            ver.raise_for_status()
            version = ver.json().get("version")
            tags = self._client.get("/api/tags")
            tags.raise_for_status()
            names = {m.get("name") for m in tags.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return ProbeResult(reachable=False, model_present=False)
        # Ollama tag matching: exact, or bare tag matching a ``:latest`` etc.
        present = self._model in names or f"{self._model}:latest" in names
        return ProbeResult(reachable=True, model_present=present, version=version)

    def warmup(self) -> None:
        """One tiny chat to load weights (§1.3). Raises on transport failure.

        Dispatches through the configured API dialect like :meth:`chat` — a
        native ``/api/chat`` warmup against an OpenAI-only server would 404."""
        messages = [{"role": "user", "content": "ping"}]
        if self._api == "openai":
            self._post_openai(messages, schema=None, num_predict=1, seed=7)
        else:
            self._post_native(messages, schema=None, num_predict=1, seed=7)

    # -- chat -------------------------------------------------------------- #
    def chat(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int = 7,
    ) -> ChatResponse:
        """One constrained-decoding chat turn. Raises :class:`LlmTransportError`."""
        system_prompt = system
        if _is_qwen3(self._model):
            # §2.3 belt-and-braces soft switch, qwen3 family only. Applied in BOTH
            # dialects: the /no_think toggle is honored by the qwen3 chat template
            # itself, and an OpenAI-compatible server (llama.cpp --jinja) would
            # otherwise burn the small num_predict budgets on <think> tokens.
            system_prompt = f"{system}\n/no_think"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ]
        if self._api == "openai":
            return self._post_openai(messages, schema, num_predict, seed)
        return self._post_native(messages, schema, num_predict, seed)

    def _post_native(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        num_predict: int,
        seed: int,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "think": False,  # §2.3 — sent on every native request
            "stream": False,
            "options": {
                "num_ctx": self._num_ctx,
                "num_predict": num_predict,
                "temperature": 0,
                "seed": seed,
            },
        }
        if schema is not None:
            body["format"] = schema  # constrained decoding (§3.0 step 4)
        started = time.monotonic()
        try:
            resp = self._client.post("/api/chat", json=body)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmTransportError(str(exc)) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        message = payload.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise LlmTransportError("malformed /api/chat body: no message.content")
        content = _strip_think(str(message.get("content", "")))
        return ChatResponse(content, message.get("tool_calls"), latency_ms)

    def _post_openai(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        num_predict: int,
        seed: int,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "seed": seed,
            "max_tokens": num_predict,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "role_output", "schema": schema, "strict": True},
            }
        started = time.monotonic()
        try:
            resp = self._client.post("/v1/chat/completions", json=body)
            resp.raise_for_status()
            payload = resp.json()
            choice = payload["choices"][0]["message"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise LlmTransportError(str(exc)) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        content = _strip_think(str(choice.get("content") or ""))
        return ChatResponse(content, choice.get("tool_calls"), latency_ms)
