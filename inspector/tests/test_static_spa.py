"""Serving the built Vite SPA: <base href> injection, caching, missing build.

These tests build a tiny fake ``dist/`` in ``tmp_path`` and construct the app
through ``create_app(Config(static_dist=...))``. They never touch the
module-level ``app`` singleton, whose config is frozen at import time, and they
never need a real ``inspector/frontend/dist`` (it is gitignored and absent on a
fresh clone or in CI).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Config

ASSET_NAME = "index-abc12345.js"


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / ASSET_NAME).write_text("export const x = 1;\n", encoding="utf-8")
    (dist / "index.html").write_text(
        "<!doctype html>\n"
        "<html><head>\n"
        "<!--BASE-->\n"
        '<script type="module" src="./assets/' + ASSET_NAME + '"></script>\n'
        '</head><body><div id="root"></div></body></html>\n',
        encoding="utf-8",
    )
    return dist


def _config(dist: Path | None, root_path: str = "") -> Config:
    return Config(
        pg_dsn="postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer",
        http_port=5005,
        root_path=root_path,
        analyzer_health_url=None,
        queries_path=None,
        query_limit=10,
        rp_pg_dsn=None,
        static_dist=dist if dist is not None else Path("/nonexistent/dist"),
    )


def test_index_injects_root_base_and_keeps_relative_assets(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(_fake_dist(tmp_path))))
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<base href="/">' in resp.text
    assert "<!--BASE-->" not in resp.text
    assert "./assets/" in resp.text


def test_index_injects_the_ingress_prefix(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(_fake_dist(tmp_path), root_path="/inspector")))
    resp = client.get("/inspector/")
    assert resp.status_code == 200
    assert '<base href="/inspector/">' in resp.text
    assert "./assets/" in resp.text


def test_index_is_never_cached(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(_fake_dist(tmp_path))))
    resp = client.get("/")
    assert resp.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_forever(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(_fake_dist(tmp_path))))
    resp = client.get(f"/assets/{ASSET_NAME}")
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


def test_hashed_assets_are_cached_forever_behind_the_ingress_prefix(tmp_path: Path) -> None:
    # Same asset, reached through the /inspector mount the ingress uses. The
    # middleware must still recognise it, so the prefix cannot cost caching.
    client = TestClient(create_app(_config(_fake_dist(tmp_path), root_path="/inspector")))
    resp = client.get(f"/inspector/assets/{ASSET_NAME}")
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


def test_a_missing_asset_is_not_cached_forever(tmp_path: Path) -> None:
    # A rolling deploy can send a request for a new chunk to an old pod, which
    # answers 404. Caching that 404 for a year would break the page for that
    # browser long after the deploy finished.
    client = TestClient(create_app(_config(_fake_dist(tmp_path))))
    resp = client.get("/assets/index-deadbeef.js")
    assert resp.status_code == 404
    assert "immutable" not in resp.headers.get("cache-control", "")


def test_healthz_is_served_next_to_the_spa(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(_fake_dist(tmp_path))))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "analyzer-ng-inspector"


def test_app_starts_without_a_frontend_build(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(None)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 404
