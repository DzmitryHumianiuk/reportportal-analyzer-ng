"""The analyzer half of the quote-masking contract.

A quote is only evidence if the reader can find it in the log. Between the text
the analyzer stores and the log the modal searches sit two separate masking
implementations that never see each other: :func:`mask_text` here, and
``normalizeLine`` in the fork's ``analyzerSuggestionMeta.js``.

This file pins what the analyzer produces. The fork's test takes the same table
and pins that those outputs still ground against the raw line. Change a masking
rule and this fails; change the modal's rule and the fork's test fails.

See ``tests/fixtures/quote_masking_cases.json`` for the table and why each case
is in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyzer_ng.ml.drain import mask_text

_CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "quote_masking_cases.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["why"] for c in _CASES])
def test_masking_matches_the_shared_contract(case: dict) -> None:
    assert mask_text(case["raw"]) == case["masked"]


def test_masking_is_stable_under_a_second_pass() -> None:
    """The modal masks the stored quote again, so masking must be idempotent.

    If a second pass changed the text, the quote and the log line would drift
    apart by exactly one application and nothing would ever ground.
    """
    for case in _CASES:
        once = mask_text(case["raw"])
        assert mask_text(once) == once


def test_every_token_the_analyzer_emits_is_one_the_modal_knows() -> None:
    """A token the modal has no rule for would sit in the quote forever.

    The modal produces its own ``<URL>``/``<IP>``/``<UUID>``/``<HEX>``/``<PATH>``/
    ``<NUM>``. Emitting anything else here means the quote carries a placeholder
    that appears in no log line, masked or not.
    """
    known = {"<URL>", "<IP>", "<UUID>", "<HEX>", "<PATH>", "<NUM>"}
    emitted = {f"<{token}>" for token, _ in _mask_tokens()}
    assert emitted == known


def _mask_tokens() -> list[tuple[str, str]]:
    from analyzer_ng.ml.drain import MASKING_RULES

    return MASKING_RULES
