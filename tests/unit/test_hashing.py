"""Tests for the deterministic hashing helpers (spec 03 §2.3, §3.2-§3.3)."""

from __future__ import annotations

from analyzer_ng.ml.hashing import to_signed64, xxh3_64_signed, xxh3_64_unsigned

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


def test_to_signed64_bounds():
    assert to_signed64(0) == 0
    assert to_signed64(_INT64_MAX) == _INT64_MAX
    assert to_signed64(1 << 63) == _INT64_MIN
    assert to_signed64((1 << 64) - 1) == -1


def test_signed_hash_in_bigint_range():
    for text in ["", "a", "org.example.Foo|bar#baz", "550e8400"]:
        h = xxh3_64_signed(text)
        assert _INT64_MIN <= h <= _INT64_MAX


def test_hash_is_stable_and_seedless():
    # Same input -> same digest across calls (and, by construction, processes).
    assert xxh3_64_unsigned("payload") == xxh3_64_unsigned("payload")
    assert xxh3_64_signed("payload") == to_signed64(xxh3_64_unsigned("payload"))


def test_distinct_inputs_distinct_hashes():
    assert xxh3_64_signed("a#b") != xxh3_64_signed("a#c")
