"""Shared fakes + a deterministic mock-Ollama transport for the LLM unit tests.

Ollama is absent in dev/CI (spec 04 testing guidance), so every test runs against
an ``httpx.MockTransport`` scripted per call. The fake stores mirror the store
Protocols with in-memory dicts, keeping per-project cache isolation observable.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from analyzer_ng.llm.client import OllamaClient


class FakeCacheStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[int, str], dict[str, Any]] = {}

    def get(self, project_id: int, cache_key: str) -> dict | None:
        row = self.rows.get((project_id, cache_key))
        return row["output"] if row else None

    def get_fresh(self, project_id: int, cache_key: str, ttl_days: int) -> dict | None:
        """Read-time freshness like the real store: a row older than ``ttl_days``
        misses. Tests age a row by setting ``age_days`` on it (default 0)."""
        row = self.rows.get((project_id, cache_key))
        if row is None or row.get("stale"):
            return None
        if row.get("age_days", 0) > ttl_days:
            return None
        row["hits"] = row.get("hits", 0) + 1
        return row["output"]

    def put(
        self,
        project_id: int,
        cache_key: str,
        role: str,
        model: str,
        output: dict,
        template_hash: int | None = None,
    ) -> None:
        self.rows[(project_id, cache_key)] = {
            "output": output,
            "role": role,
            "model": model,
            "template_hash": template_hash,
            "hits": 0,
        }


class FakeEventStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> int:
        self.events.append(kwargs)
        return len(self.events)


class FakeRoleStateStore:
    def __init__(self) -> None:
        self.disabled: set[tuple[int, str]] = set()

    def is_enabled(self, project_id: int, role: str) -> bool:
        return (project_id, role) not in self.disabled

    def set_state(
        self,
        project_id: int,
        role: str,
        *,
        enabled: bool,
        reason: str | None = None,
        stats: dict | None = None,
    ) -> None:
        if enabled:
            self.disabled.discard((project_id, role))
        else:
            self.disabled.add((project_id, role))


class MockOllama:
    """A scripted mock-Ollama server behind an httpx.MockTransport.

    ``chat_script`` is a list consumed per ``/api/chat`` call. Each entry is one
    of: a ``str`` (assistant content), or a ``dict`` with one of ``content`` (str),
    ``status`` (int → that HTTP status), ``raw`` (non-JSON body → malformed),
    ``tool_calls`` (list), ``exception`` ('timeout'|'connect').
    """

    def __init__(
        self,
        chat_script: list[Any] | None = None,
        *,
        model_present: bool = True,
        version: str = "0.5.0",
        tags: list[str] | None = None,
        reachable: bool = True,
    ) -> None:
        self.chat_script = list(chat_script or [])
        self.model_present = model_present
        self.version = version
        self.tags = tags
        self.reachable = reachable
        self.requests: list[httpx.Request] = []
        self.chat_bodies: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self, model: str = "qwen3:4b-q4_K_M", **kwargs: Any) -> OllamaClient:
        return OllamaClient("http://ollama:11434", model, transport=self.transport(), **kwargs)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.reachable:
            raise httpx.ConnectError("unreachable", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": self.version})
        if path == "/api/tags":
            names = self.tags
            if names is None:
                names = ["qwen3:4b-q4_K_M"] if self.model_present else ["other:1b"]
            return httpx.Response(200, json={"models": [{"name": n} for n in names]})
        if path == "/v1/models":
            names = self.tags
            if names is None:
                names = ["qwen3:4b-q4_K_M"] if self.model_present else ["other:1b"]
            return httpx.Response(200, json={"data": [{"id": n} for n in names]})
        if path in ("/api/chat", "/v1/chat/completions"):
            self.chat_bodies.append(json.loads(request.content))
            return self._chat_response(request)
        return httpx.Response(404)

    def _chat_response(self, request: httpx.Request) -> httpx.Response:
        if not self.chat_script:
            entry: Any = {"content": "{}"}
        else:
            entry = self.chat_script.pop(0)
        if isinstance(entry, str):
            entry = {"content": entry}
        if "exception" in entry:
            if entry["exception"] == "timeout":
                raise httpx.TimeoutException("timeout", request=request)
            raise httpx.ConnectError("connect", request=request)
        if "status" in entry:
            return httpx.Response(entry["status"], json={"error": "boom"})
        if "raw" in entry:
            return httpx.Response(200, content=entry["raw"])
        message: dict[str, Any] = {"role": "assistant", "content": entry.get("content", "{}")}
        if "tool_calls" in entry:
            message["tool_calls"] = entry["tool_calls"]
        # Stop cause: default "stop"; entries set "finish_reason": "length" to
        # simulate a num_predict cap (masked truncation). Ollama surfaces it as
        # ``done_reason``, the OpenAI dialect as choice ``finish_reason``.
        finish_reason = entry.get("finish_reason", "stop")
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200, json={"choices": [{"message": message, "finish_reason": finish_reason}]}
            )
        return httpx.Response(200, json={"message": message, "done_reason": finish_reason})
