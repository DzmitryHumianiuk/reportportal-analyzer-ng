"""Smoke test: the package imports and exposes its version."""

import analyzer_ng


def test_package_version() -> None:
    assert analyzer_ng.__version__ == "0.1.0"
