"""Ollama client + probe tests (spec 04 §1.3, §2.3, §3.0 step 4)."""

from __future__ import annotations

import pytest
from _llm_fakes import MockOllama

from analyzer_ng.llm.client import LlmTransportError

_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def test_think_false_sent_on_every_request() -> None:
    mock = MockOllama(['{"x":"1"}', '{"x":"2"}'])
    client = mock.client()
    client.chat(system="s", user="u", schema=_SCHEMA, num_predict=10)
    client.chat(system="s", user="u", schema=_SCHEMA, num_predict=10)
    assert all(body["think"] is False for body in mock.chat_bodies)
    assert all(body["stream"] is False for body in mock.chat_bodies)


def test_no_think_appended_for_qwen3_only() -> None:
    mock = MockOllama(['{"x":"1"}'])
    mock.client(model="qwen3:4b-q4_K_M").chat(
        system="base", user="u", schema=_SCHEMA, num_predict=10
    )
    assert mock.chat_bodies[0]["messages"][0]["content"].endswith("/no_think")

    mock2 = MockOllama(['{"x":"1"}'])
    mock2.client(model="granite4:micro").chat(
        system="base", user="u", schema=_SCHEMA, num_predict=10
    )
    assert "/no_think" not in mock2.chat_bodies[0]["messages"][0]["content"]


def test_format_schema_and_options_passed() -> None:
    mock = MockOllama(['{"x":"1"}'])
    mock.client(num_ctx=4096).chat(system="s", user="u", schema=_SCHEMA, num_predict=42, seed=8)
    body = mock.chat_bodies[0]
    assert body["format"] == _SCHEMA
    assert body["options"] == {
        "num_ctx": 4096,
        "num_predict": 42,
        "temperature": 0,
        "seed": 8,
    }


def test_think_block_stripped_before_parsing() -> None:
    mock = MockOllama(['<think>reasoning here</think>{"x":"7"}'])
    resp = mock.client().chat(system="s", user="u", schema=_SCHEMA, num_predict=10)
    assert resp.content == '{"x":"7"}'


def test_tool_calls_surfaced() -> None:
    mock = MockOllama([{"content": "{}", "tool_calls": [{"name": "rm"}]}])
    resp = mock.client().chat(system="s", user="u", schema=_SCHEMA, num_predict=10)
    assert resp.tool_calls == [{"name": "rm"}]


def test_5xx_is_transport_error() -> None:
    mock = MockOllama([{"status": 500}])
    with pytest.raises(LlmTransportError):
        mock.client().chat(system="s", user="u", schema=_SCHEMA, num_predict=10)


def test_timeout_is_transport_error() -> None:
    mock = MockOllama([{"exception": "timeout"}])
    with pytest.raises(LlmTransportError):
        mock.client().chat(system="s", user="u", schema=_SCHEMA, num_predict=10)


def test_malformed_body_is_transport_error() -> None:
    mock = MockOllama([{"raw": "not json at all"}])
    with pytest.raises(LlmTransportError):
        mock.client().chat(system="s", user="u", schema=_SCHEMA, num_predict=10)


def test_openai_variant_uses_response_format() -> None:
    mock = MockOllama(['{"x":"1"}'])
    client = mock.client(api="openai")
    resp = client.chat(system="s", user="u", schema=_SCHEMA, num_predict=10)
    assert resp.content == '{"x":"1"}'
    body = mock.chat_bodies[0]
    assert body["response_format"]["type"] == "json_schema"
    assert "think" not in body


def test_probe_model_present() -> None:
    mock = MockOllama(model_present=True)
    result = mock.client().probe()
    assert result.reachable and result.model_present
    assert result.version == "0.5.0"


def test_probe_model_missing_no_pull() -> None:
    mock = MockOllama(model_present=False)
    result = mock.client().probe()
    assert result.reachable and not result.model_present
    # No pull endpoint is ever hit.
    assert all("/api/pull" not in r.url.path for r in mock.requests)


def test_probe_unreachable() -> None:
    mock = MockOllama(reachable=False)
    result = mock.client().probe()
    assert not result.reachable
