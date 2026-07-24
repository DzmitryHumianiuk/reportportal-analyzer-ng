#!/usr/bin/env python3
"""Unit tests for migrate.py — focused on the 401 re-authentication path.

Run: ``python3 -m pytest test_migrate.py`` (or ``python3 test_migrate.py``).
No network and no ReportPortal instance are touched: the session transport is
stubbed so we control the exact status sequence a request sees.
"""

from __future__ import annotations

import unittest

import migrate


class FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.content = text.encode()
        self.headers: dict[str, str] = {}

    def json(self) -> dict | None:
        return self._json


class FakeSession:
    """Stand-in for requests.Session: hands back queued responses in order and
    records every send so the test can assert the request was retried."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **kw) -> FakeResponse:
        self.calls.append((method, url))
        return self.responses.pop(0)


def _client(responses: list[FakeResponse]) -> tuple[migrate.RP, list[int]]:
    """An API-key RP client with a stubbed transport and a login() that only
    counts invocations (re-auth is observable without touching the network)."""
    client = migrate.RP("http://rp.test", "proj", "dst", api_key="k")
    client.s = FakeSession(responses)  # type: ignore[assignment]
    login_calls: list[int] = []

    def fake_login() -> None:
        login_calls.append(1)
        client.s.headers["Authorization"] = "Bearer refreshed"

    client.login = fake_login  # type: ignore[method-assign]
    return client, login_calls


class ReauthOn401Test(unittest.TestCase):
    def test_401_then_200_reauths_and_retries(self) -> None:
        client, login_calls = _client(
            [
                FakeResponse(401, text="expired"),
                FakeResponse(200, json_body={"id": "abc"}, text='{"id": "abc"}'),
            ]
        )
        out = client.post("/api/v2/proj/item", {"name": "x"})
        self.assertEqual(out, {"id": "abc"})  # returns the post-refresh body
        self.assertEqual(len(login_calls), 1)  # re-login happened exactly once
        self.assertEqual(len(client.s.calls), 2)  # original + one retry

    def test_401_twice_raises_with_body(self) -> None:
        client, login_calls = _client(
            [FakeResponse(401, text="nope-first"), FakeResponse(401, text="nope-second")]
        )
        with self.assertRaises(RuntimeError) as ctx:
            client.post("/api/v2/proj/item", {"name": "x"})
        self.assertIn("nope-second", str(ctx.exception))  # second 401 body is surfaced
        self.assertIn("-> 401", str(ctx.exception))
        self.assertEqual(len(login_calls), 1)  # tried to refresh once
        self.assertEqual(len(client.s.calls), 2)  # original + one retry, no more

    def test_get_also_reauths(self) -> None:
        client, login_calls = _client(
            [FakeResponse(401, text="expired"), FakeResponse(200, json_body={"content": []})]
        )
        out = client.get("/api/v1/proj/launch")
        self.assertEqual(out, {"content": []})
        self.assertEqual(len(login_calls), 1)


class ApiKeyLoginTest(unittest.TestCase):
    def test_api_key_login_sets_header_without_network(self) -> None:
        client = migrate.RP("http://rp.test", "proj", "src", api_key="secret")
        self.assertEqual(client.s.headers["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
