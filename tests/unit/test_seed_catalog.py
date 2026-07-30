"""Unit tests for the seed failure-mode catalog + matching engine (spec 03 §9, §11).

Acceptance covered here (no DB):
- YAML loads; all regexes compile; keys unique; 50 modes present (§11).
- Synthetic fixture suite: first-matching mode is the intended one for >= 90% of
  fixtures; zero matches on the benign INFO negatives (§11).
- Loader validation rejects bad labels / confidence / regex / dup keys (§9).
- ``load_seed_kb`` is idempotent and the pure ``match`` needs no KBStore.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from analyzer_ng.seeds.catalog import (
    EXPECTED_MODE_COUNT,
    VALID_LABELS,
    SeedCatalogError,
    default_catalog,
    load_catalog,
)
from analyzer_ng.seeds.loader import SeedKB, load_seed_kb

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "seed_modes" / "fixtures.yaml"


@pytest.fixture(scope="module")
def catalog():
    return default_catalog()


@pytest.fixture(scope="module")
def fixtures() -> dict:
    return yaml.safe_load(_FIXTURES.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Catalog integrity (§11: "YAML loads; all regexes compile; keys unique; 50")
# --------------------------------------------------------------------------- #
def test_catalog_has_exactly_50_modes(catalog):
    assert len(catalog) == EXPECTED_MODE_COUNT == 50


def test_mode_keys_are_unique(catalog):
    keys = [m.mode_key for m in catalog.modes]
    assert len(keys) == len(set(keys))


def test_every_mode_has_valid_prior_and_at_least_one_rule(catalog):
    for m in catalog.modes:
        assert m.prior_label in VALID_LABELS
        assert 0.0 < m.prior_confidence <= 1.0
        assert m.exc_re or m.msg_re or m.kw, f"{m.mode_key} has no rules"


def test_all_regexes_are_compiled_patterns(catalog):
    for m in catalog.modes:
        for pat in (*m.exc_re, *m.msg_re):
            assert isinstance(pat, re.Pattern)


def test_keywords_are_lowercase(catalog):
    for m in catalog.modes:
        for kw in m.kw:
            assert kw == kw.lower()


# --------------------------------------------------------------------------- #
# Matching engine semantics (spec 03 §9)
# --------------------------------------------------------------------------- #
def test_exception_regex_is_case_sensitive(catalog):
    # stack_overflow matches on exc_re only (no kw/msg_re), so it isolates the
    # case-sensitivity of exception-name matching (spec §9).
    assert catalog.match(exc_classes=["java.lang.StackOverflowError"]).mode_key == "stack_overflow"
    assert catalog.match(exc_classes=["java.lang.stackoverflowerror"]) is None


def test_message_regex_is_case_insensitive(catalog):
    hit = catalog.match(msg_text="NO SPACE LEFT ON DEVICE while writing report")
    assert hit is not None and hit.mode_key == "disk_full"


def test_keyword_matches_across_msg_and_templates(catalog):
    hit = catalog.match(msg_text="build broke", template_texts=["kafka broker not available"])
    assert hit is not None and hit.mode_key == "kafka_rabbit"


def test_first_matching_mode_by_priority_wins(catalog):
    # "connection refused" (mode 6) precedes redis (mode 22): priority order wins.
    hit = catalog.match(msg_text="redis: Connection refused")
    assert hit is not None and hit.mode_key == "conn_refused"


def test_no_rule_hit_returns_none(catalog):
    assert catalog.match(msg_text="everything is fine", exc_classes=["Foo"]) is None


# --------------------------------------------------------------------------- #
# Fixture suite accuracy (§11: >= 90% to intended mode; 0 negatives)
# --------------------------------------------------------------------------- #
def test_fixture_suite_is_large_enough(fixtures):
    assert len(fixtures["positives"]) >= 100
    assert len(fixtures["negatives"]) >= 20


def test_seed_catalog_classifies_at_least_90_percent(catalog, fixtures):
    positives = fixtures["positives"]
    misses = []
    for fx in positives:
        hit = catalog.match(fx.get("exc", []), fx.get("msg", ""), fx.get("templates", []))
        got = hit.mode_key if hit else None
        if got != fx["expect"]:
            misses.append((fx["expect"], got, fx.get("msg", "")[:60]))
    accuracy = (len(positives) - len(misses)) / len(positives)
    assert accuracy >= 0.90, f"accuracy {accuracy:.2%}; misses={misses}"


def test_no_benign_negative_matches_any_mode(catalog, fixtures):
    hits = [(n, catalog.match([], n, [])) for n in fixtures["negatives"]]
    matched = [(n, h.mode_key) for n, h in hits if h is not None]
    assert matched == [], f"benign lines matched modes: {matched}"


def test_every_mode_has_at_least_one_fixture(fixtures):
    covered = {fx["expect"] for fx in fixtures["positives"]}
    assert len(covered) == EXPECTED_MODE_COUNT


# --------------------------------------------------------------------------- #
# Loader validation (spec 03 §9 "Loader validation, unit-tested")
# --------------------------------------------------------------------------- #
_GOOD = """
version: 1
modes:
  - mode_key: a
    title: A
    rules: {exc_re: ['Foo'], kw: ["bar"]}
    prior: {label: pb, confidence: 0.5}
    rationale: ok
"""


def test_load_catalog_accepts_minimal_valid_document():
    cat = load_catalog(_GOOD)
    assert len(cat) == 1 and cat.get("a").prior_label == "pb"


def test_load_catalog_rejects_bad_regex():
    bad = _GOOD.replace("'Foo'", "'([unclosed'")
    with pytest.raises(SeedCatalogError, match="does not compile"):
        load_catalog(bad)


def test_load_catalog_rejects_bad_label():
    bad = _GOOD.replace("label: pb", "label: xx")
    with pytest.raises(SeedCatalogError, match="label"):
        load_catalog(bad)


@pytest.mark.parametrize("conf", ["0.0", "1.5", "-0.2"])
def test_load_catalog_rejects_out_of_range_confidence(conf):
    bad = _GOOD.replace("confidence: 0.5", f"confidence: {conf}")
    with pytest.raises(SeedCatalogError, match="confidence"):
        load_catalog(bad)


def test_load_catalog_rejects_duplicate_keys():
    dup = (
        _GOOD
        + """  - mode_key: a
    title: A2
    rules: {kw: ["baz"]}
    prior: {label: si, confidence: 0.5}
    rationale: dup
"""
    )
    with pytest.raises(SeedCatalogError, match="duplicate"):
        load_catalog(dup)


def test_load_catalog_rejects_mode_without_rules():
    bad = _GOOD.replace("rules: {exc_re: ['Foo'], kw: [\"bar\"]}", "rules: {}")
    with pytest.raises(SeedCatalogError, match="no matching rules"):
        load_catalog(bad)


# --------------------------------------------------------------------------- #
# Startup loader (spec 01 §6 step 6): idempotent; pure match needs no DB
# --------------------------------------------------------------------------- #
def test_load_seed_kb_is_idempotent_and_dbless():
    kb1 = load_seed_kb()
    kb2 = load_seed_kb()
    assert isinstance(kb1, SeedKB)
    assert len(kb1.catalog) == len(kb2.catalog) == 50
    # pure match works with no KBStore bound
    assert kb1.match(exc_classes=["java.lang.NullPointerException"]).mode_key == "npe_undefined"


def test_match_and_seed_requires_kb_store():
    kb = load_seed_kb()  # no store
    with pytest.raises(RuntimeError, match="no KBStore"):
        kb.match_and_seed(1, exc_classes=["java.lang.OutOfMemoryError"])
