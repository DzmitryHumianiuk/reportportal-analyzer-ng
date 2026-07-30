"""Unit tests for the ReportPortal name-resolution module and its fallbacks.

No real database is touched: the loader is stubbed with fake maps (resolved),
made to raise (unreachable), or left unconfigured (no DSN). This mirrors the
three states the UI must render honestly: live / unavailable / not configured.
"""

from __future__ import annotations

from backend import payloads
from backend.rp_names import RPNameResolver

# Fake maps modelled on the real RP data (project 1 = superadmin_personal).
_PROJECTS = {1: "superadmin_personal", 2: "default_personal"}
_DEFECTS = {
    1: {
        "pb001": {
            "locator": "pb001",
            "name": "Product Bug",
            "short_name": "PB",
            "color": "#d32f2f",
        },
        "si001": {
            "locator": "si001",
            "name": "System Issue",
            "short_name": "SI",
            "color": "#3e7be6",
        },
    }
}


def _stub_resolved(r: RPNameResolver) -> None:
    def _load() -> None:
        r._projects = dict(_PROJECTS)
        r._defects = {k: dict(v) for k, v in _DEFECTS.items()}
        r._reachable = True
        r._error = None

    r._load = _load  # type: ignore[method-assign]


def test_unconfigured_is_honest_fallback():
    r = RPNameResolver(None)
    assert r.configured is False
    assert r.project_name(1) is None
    assert r.defects(1) == {}
    st = r.status()
    assert st.configured is False and st.reachable is False
    assert "not configured" in st.note
    blk = r.block(1)
    assert blk == {
        "project_id": 1,
        "project_name": None,
        "status": {"configured": False, "reachable": False, "note": st.note},
        "defects": {},
    }


def test_resolved_names_and_colors():
    r = RPNameResolver("postgresql://rp")
    _stub_resolved(r)
    assert r.project_name(1) == "superadmin_personal"
    pb = r.defects(1)["pb001"]
    assert pb["name"] == "Product Bug"
    assert pb["short_name"] == "PB"
    assert pb["color"] == "#d32f2f"
    st = r.status()
    assert st.configured and st.reachable and "live" in st.note
    blk = r.block(1)
    assert blk["project_name"] == "superadmin_personal"
    assert blk["defects"]["si001"]["name"] == "System Issue"


def test_missing_name_falls_back_to_none():
    r = RPNameResolver("postgresql://rp")
    _stub_resolved(r)
    # A project with no RP row → no invented name, empty defect map.
    assert r.project_name(999) is None
    assert r.defects(999) == {}
    # A locator absent from the map → not present (caller shows raw locator).
    assert "ab001" not in r.defects(1)


def test_unreachable_degrades_gracefully():
    r = RPNameResolver("postgresql://bad-host")

    def _boom() -> None:
        raise RuntimeError("connection refused")

    r._load = _boom  # type: ignore[method-assign]
    st = r.status()
    assert st.configured is True and st.reachable is False
    assert "unavailable" in st.note
    assert r.project_name(1) is None
    assert r.defects(1) == {}


def test_ttl_caches_load():
    r = RPNameResolver("postgresql://rp", ttl=10_000)
    calls = {"n": 0}

    def _load() -> None:
        calls["n"] += 1
        r._projects = {1: "p"}
        r._reachable = True
        r._error = None

    r._load = _load  # type: ignore[method-assign]
    r.project_name(1)
    r.project_name(1)
    r.status()
    assert calls["n"] == 1  # loaded once, then served from cache within TTL


def test_payloads_rp_block_fallback_without_resolver():
    blk = payloads._rp_block(None, 7)
    assert blk["project_id"] == 7
    assert blk["project_name"] is None
    assert blk["status"]["configured"] is False
    assert blk["defects"] == {}


def test_payloads_rp_block_uses_resolver():
    r = RPNameResolver("postgresql://rp")
    _stub_resolved(r)
    blk = payloads._rp_block(r, 1)
    assert blk["project_name"] == "superadmin_personal"
    assert blk["defects"]["pb001"]["name"] == "Product Bug"
