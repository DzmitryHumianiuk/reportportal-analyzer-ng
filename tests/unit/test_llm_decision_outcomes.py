"""Decision-outcome LLM coverage (extension 2026-07-20).

The Explainer role was extended to cover ALL decision outcomes the data supports:

* abstain WITH retrieved candidates → an ``abstain_explainer`` LLM job (narrative
  synthesis of the conflicting evidence — the one case that earns a model call);
* pure-empty abstain (no candidates) → NO LLM call (nothing to explain);
* cold-start → the rubric ``reason`` copied onto the suggestion explanation (no new
  call — see ``test_llm_apply``);
* Stage-A inherit → a DETERMINISTIC template sentence written in-process (no LLM,
  ``llm_used=false`` provenance marker).

These tests exercise the enqueue routing, the deterministic Stage-A sentence, the
abstain fact-loader/applier, and the abstain role's fact injection + grounding.
"""

from __future__ import annotations

from typing import Any

from analyzer_ng.core.analysis import AnalysisEngine
from analyzer_ng.core.decision import (
    METHOD_GBM,
    METHOD_HASH,
    TAU_AUTO,
    DecisionResult,
)
from analyzer_ng.db.repositories.models import Candidate
from analyzer_ng.llm.roles import AbstainExplainerRole, ExplainerRole
from analyzer_ng.llm.roles.coldstart import (
    _SYSTEM,
    RUBRIC,
    RUBRIC_LABEL,
    ColdStartRole,
    rubric_rule_name,
    strip_rule_ids,
)
from analyzer_ng.llm.wiring import PgLlmApplier, PgLlmFactLoader


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeSidecar:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int, dict]] = []

    def enqueue(self, role: str, project: int, item_id: int, payload: dict) -> None:
        self.calls.append((role, project, item_id, payload))


def _engine(sidecar: FakeSidecar, judge_tau: float = TAU_AUTO) -> AnalysisEngine:
    eng = AnalysisEngine.__new__(AnalysisEngine)
    eng.sidecar = sidecar  # type: ignore[attr-defined]
    eng.judge_tau = judge_tau  # type: ignore[attr-defined]
    return eng


def _cand(item_id: int, cosine: float, issue_type: str) -> Candidate:
    return Candidate(item_id=item_id, mode_id=None, cosine=cosine, issue_type=issue_type)


def _abstain(reason: str, stage_c: list[Candidate]) -> DecisionResult:
    return DecisionResult(
        label="ti",
        issue_type="ti",
        confidence=0.42,
        method=METHOD_GBM,
        action="abstain",
        abstain_reason=reason,
        relevant_item_id=None,
        matched_mode_id=None,
        features={},
        stage_c=stage_c,
    )


# --------------------------------------------------------------------------- #
# Enqueue routing per outcome
# --------------------------------------------------------------------------- #
def test_abstain_with_candidates_enqueues_abstain_explainer() -> None:
    sc = FakeSidecar()
    dec = _abstain(
        "gbm_boilerplate_only_neighbor",
        [_cand(201, 0.977, "pb001"), _cand(202, 0.951, "ab001")],
    )
    _engine(sc)._enqueue_llm(7, 4998, 267, dec, suggestion_id=555)
    roles = [c[0] for c in sc.calls]
    assert "abstain_explainer" in roles
    assert "extractor" in roles  # feature enrichment always runs
    assert "coldstart" in roles  # cold gate is enforced later in the fact_loader
    ax = next(c for c in sc.calls if c[0] == "abstain_explainer")
    payload = ax[3]
    assert payload["suggestion_id"] == 555
    assert payload["abstain_reason"] == "gbm_boilerplate_only_neighbor"
    assert [c["label"] for c in payload["candidates"]] == ["pb001", "ab001"]
    assert payload["candidates"][0]["similarity"] == 0.977


def test_abstain_without_stage_c_still_enqueues_explainer() -> None:
    # The hash-pool conflict (e.g. item 4998, exception_fp=0) lives only in the DB,
    # so enqueue fires on every abstain; the async fact-loader is the emptiness gate.
    sc = FakeSidecar()
    dec = _abstain("no_confident_rule", [])  # no Stage-C candidates carried
    _engine(sc)._enqueue_llm(7, 1, 1, dec, suggestion_id=9)
    roles = [c[0] for c in sc.calls]
    assert "abstain_explainer" in roles
    ax = next(c for c in sc.calls if c[0] == "abstain_explainer")
    assert ax[3]["candidates"] == []  # worker will re-read the hash pool from PG


def test_abstain_without_suggestion_id_skips_explainer() -> None:
    sc = FakeSidecar()
    dec = _abstain("gbm_below_suggest", [_cand(201, 0.9, "pb001")])
    _engine(sc)._enqueue_llm(7, 1, 1, dec, suggestion_id=None)
    assert "abstain_explainer" not in [c[0] for c in sc.calls]


def test_stage_a_inherit_enqueues_no_llm_explainer() -> None:
    sc = FakeSidecar()
    dec = DecisionResult(
        label="pb",
        issue_type="pb001",
        confidence=0.95,
        method=METHOD_HASH,
        action="auto",
        abstain_reason=None,
        relevant_item_id=42,
        matched_mode_id=None,
        features={},
        relevant_label_source="human",
    )
    _engine(sc)._enqueue_llm(7, 1, 1, dec, suggestion_id=5)
    roles = [c[0] for c in sc.calls]
    # Deterministic template only — never the LLM explainer nor abstain explainer.
    assert "explainer" not in roles
    assert "abstain_explainer" not in roles
    assert roles == ["extractor"]


def test_match_suggest_band_still_enqueues_explainer() -> None:
    sc = FakeSidecar()
    dec = DecisionResult(
        label="pb",
        issue_type="pb001",
        confidence=0.60,
        method=METHOD_GBM,
        action="suggest",
        abstain_reason=None,
        relevant_item_id=9,
        matched_mode_id=None,
        features={},
    )
    _engine(sc)._enqueue_llm(7, 1, 1, dec, suggestion_id=5)
    assert "explainer" in [c[0] for c in sc.calls]


# --------------------------------------------------------------------------- #
# Stage-A deterministic sentence
# --------------------------------------------------------------------------- #
def test_stage_a_explanation_human_labeled() -> None:
    dec = DecisionResult(
        label="pb",
        issue_type="pb001",
        confidence=0.95,
        method=METHOD_HASH,
        action="auto",
        abstain_reason=None,
        relevant_item_id=42,
        matched_mode_id=None,
        features={},
        relevant_label_source="human",
    )
    assert AnalysisEngine._stage_a_explanation(dec) == (
        "Inherited from item 42 (human-labeled pb001, same error_hash, discriminant gate passed)."
    )


def test_stage_a_explanation_non_human_source_and_other_paths() -> None:
    ai = DecisionResult(
        label="pb",
        issue_type="pb001",
        confidence=0.95,
        method=METHOD_HASH,
        action="auto",
        abstain_reason=None,
        relevant_item_id=7,
        matched_mode_id=None,
        features={},
        relevant_label_source="ai_suggested",
    )
    assert AnalysisEngine._stage_a_explanation(ai) == (
        "Inherited from item 7 (pb001, same error_hash, discriminant gate passed)."
    )
    # Non-Stage-A decisions get no deterministic sentence.
    gbm = DecisionResult(
        label="pb",
        issue_type="pb001",
        confidence=0.6,
        method=METHOD_GBM,
        action="suggest",
        abstain_reason=None,
        relevant_item_id=7,
        matched_mode_id=None,
        features={},
    )
    assert AnalysisEngine._stage_a_explanation(gbm) is None


# --------------------------------------------------------------------------- #
# Abstain fact loader
# --------------------------------------------------------------------------- #
class _FakeFacts:
    def __init__(self, sig: dict | None, pool: list[dict] | None = None) -> None:
        self._sig = sig
        self._pool = pool or []

    def load_signature(self, project_id: int, item_id: int) -> dict | None:
        return self._sig

    def latest_suggestion(self, project_id: int, item_id: int) -> dict | None:
        return None

    def hash_pool(
        self, project_id: int, error_hash: int, exclude_item_id: int, limit: int = 10
    ) -> list[dict]:
        return self._pool


_SIG = {
    "exception_fp": 0,
    "error_hash": 4998,
    "top_frames": ["com.acme.Checkout.pay"],
    "template_ids": [1, 2],
    "exc_text": "org.example.ApiException",
    "msg_text": "payment failed",
    "frames_text": "at com.acme.Checkout.pay",
    "status_codes": [],
    "launch_id": 267,
}


def _abstain_payload() -> dict:
    return {
        "suggestion_id": 555,
        "abstain_reason": "gbm_boilerplate_only_neighbor",
        "confidence": 0.42,
        "candidates": [
            {"id": 201, "label": "pb001", "similarity": 0.977},
            {"id": 202, "label": "ab001", "similarity": 0.951},
        ],
    }


_POOL = [
    {"issue_type": "pb001", "issue_type_group": "pb", "label_source": "human"},
    {"issue_type": "pb001", "issue_type_group": "pb", "label_source": "human"},
    {"issue_type": "ab_x1", "issue_type_group": "ab", "label_source": "human"},
    {"issue_type": "ab_x1", "issue_type_group": "ab", "label_source": "human"},
]


def test_abstain_fact_loader_builds_conflict_facts() -> None:
    loader = PgLlmFactLoader(_FakeFacts(_SIG, _POOL))
    inp = loader("abstain_explainer", 7, 4998, _abstain_payload())
    assert inp is not None
    facts = inp["fact_block"]
    assert facts["reason_code"] == "gbm_boilerplate_only_neighbor"
    assert "boilerplate guard" in facts["blocking_gate"]
    assert facts["confidence"] == "0.420" and facts["suggest_threshold"] == "0.45"
    assert facts["top_candidates"] == [
        "cosine 0.977 label pb001",
        "cosine 0.951 label ab001",
    ]
    # The exact-hash pool distribution (2x pb001 / 2x ab_x1) is injected as facts.
    assert facts["exact_hash_pool"] == ["2x pb001", "2x ab_x1"]
    assert "pb001" in facts["label_conflict"] and "ab_x1" in facts["label_conflict"]
    assert inp["suggestion_id"] == 555


def test_abstain_fact_loader_uses_hash_pool_when_no_stage_c() -> None:
    # Item 4998: exception_fp=0 → no Stage-A/Stage-C labels, conflict only in the pool.
    loader = PgLlmFactLoader(_FakeFacts(_SIG, _POOL))
    payload = {"suggestion_id": 555, "abstain_reason": "no_confident_rule", "candidates": []}
    inp = loader("abstain_explainer", 7, 4998, payload)
    assert inp is not None
    assert inp["fact_block"]["exact_hash_pool"] == ["2x pb001", "2x ab_x1"]
    assert inp["fact_block"]["top_candidates"] == []


def test_abstain_fact_loader_skips_pure_empty_abstain() -> None:
    loader = PgLlmFactLoader(_FakeFacts(_SIG, pool=[]))  # empty hash pool
    no_cands = {"suggestion_id": 1, "candidates": []}
    assert loader("abstain_explainer", 7, 4998, no_cands) is None  # nothing to explain
    # Missing suggestion row entirely → also skipped.
    assert (
        PgLlmFactLoader(_FakeFacts(None)).__call__(
            "abstain_explainer", 7, 4998, {"suggestion_id": 1, "candidates": []}
        )
        is None
    )
    # Has a suggestion_id and candidates but no row → load_signature None → skip.
    assert (
        PgLlmFactLoader(_FakeFacts(None)).__call__(
            "abstain_explainer",
            7,
            4998,
            {"suggestion_id": 1, "candidates": [{"id": 1, "label": "pb001", "similarity": 0.9}]},
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Abstain applier → explanation on the ti row
# --------------------------------------------------------------------------- #
class _FakeOps:
    def __init__(self) -> None:
        self.explanations: list[tuple] = []

    def set_explanation(self, project_id: int, suggestion_id: int, explanation: str) -> None:
        self.explanations.append((project_id, suggestion_id, explanation))


def _applier(ops: _FakeOps) -> PgLlmApplier:
    app = PgLlmApplier.__new__(PgLlmApplier)
    app._ops = ops  # type: ignore[attr-defined]
    app._model_tag = "m"  # type: ignore[attr-defined]
    return app


def test_abstain_applier_sets_explanation_on_target_row() -> None:
    from analyzer_ng.llm.engine import RoleResult

    ops = _FakeOps()
    result = RoleResult("ok", {"explanation": "declined", "quoted_lines": []}, False, "p")
    _applier(ops)("abstain_explainer", 7, 4998, {"suggestion_id": 555}, result)
    assert ops.explanations == [(7, 555, "declined")]


# --------------------------------------------------------------------------- #
# Abstain role: fact injection + grounding (rejects fabricated quotes)
# --------------------------------------------------------------------------- #
def _abstain_role_input() -> dict[str, Any]:
    return {
        "error_hash": 4998,
        "reason_code": "gbm_boilerplate_only_neighbor",
        "candidate_key": "cosine 0.977 label pb001|cosine 0.951 label ab001",
        "fact_block": {
            "decision": "abstain: analyzer declined to choose a defect type",
            "reason_code": "gbm_boilerplate_only_neighbor",
            "blocking_gate": (
                "the nearest neighbour shared no structural evidence (boilerplate guard)"
            ),
            "confidence": "0.420",
            "suggest_threshold": "0.45",
            "top_candidates": ["cosine 0.977 label pb001"],
            "exact_hash_pool": ["2x pb001", "2x ab_x1"],
            "label_conflict": "evidence splits across labels: pb001, ab_x1",
        },
        "log_excerpt": "org.example.ApiException: payment failed\nat com.acme.Checkout.pay",
    }


def test_abstain_role_injects_facts_into_prompt() -> None:
    role = AbstainExplainerRole()
    system, user = role.build_prompt(_abstain_role_input(), "abcd1234")
    assert "declined" in system.lower()
    # Candidate conflict + hash pool + gate injected as quotable facts.
    assert "evidence splits across labels: pb001, ab_x1" in user
    assert "cosine 0.977 label pb001" in user
    assert "2x ab_x1" in user
    assert "boilerplate guard" in user


def test_abstain_role_grounding_rejects_fabricated_quote() -> None:
    role = AbstainExplainerRole()
    inp = _abstain_role_input()
    role.build_prompt(inp, "abcd1234")  # populates inp["_corpus"]
    grounded = {
        "explanation": 'Declined: "evidence splits across labels: pb001, ab_x1".',
        "quoted_lines": ["cosine 0.977 label pb001"],
    }
    assert role.post_validate(grounded, inp) is True
    fabricated = {"explanation": 'Declined because "the database exploded".', "quoted_lines": []}
    assert role.post_validate(fabricated, inp) is False


def test_abstain_role_fact_value_cannot_ground_as_log_quote() -> None:
    # F1b regression: the abstain role must populate the split corpora like the
    # match explainer does. Gate sentences and thresholds live only in the fact
    # block, and the abstain narrative is the role most tempted to quote them as
    # "log" evidence — a combined-only corpus would let that pass.
    role = AbstainExplainerRole()
    inp = _abstain_role_input()
    role.build_prompt(inp, "abcd1234")
    gate = "the nearest neighbour shared no structural evidence (boilerplate guard)"
    as_log = {"explanation": "ok", "quoted_log_lines": [gate], "quoted_fact_values": []}
    assert role.post_validate(as_log, inp) is False
    as_fact = {"explanation": "ok", "quoted_log_lines": [], "quoted_fact_values": [gate]}
    assert role.post_validate(as_fact, inp) is True
    # And the reverse: a real log line is not a fact value.
    log_line = "org.example.ApiException: payment failed"
    log_as_fact = {"explanation": "ok", "quoted_log_lines": [], "quoted_fact_values": [log_line]}
    assert role.post_validate(log_as_fact, inp) is False
    log_as_log = {"explanation": "ok", "quoted_log_lines": [log_line], "quoted_fact_values": []}
    assert role.post_validate(log_as_log, inp) is True


def test_abstain_role_content_key_distinct_from_match_explainer() -> None:
    # Same error_hash must not collide in the shared 'explainer' cache namespace.
    abstain = AbstainExplainerRole().content_key(_abstain_role_input())
    match = ExplainerRole().content_key(
        {"mode_id": 1, "error_hash": 4998, "match_signals": "cosine 0.9"}
    )
    assert abstain != match
    assert abstain.startswith("abstain|")


# Found on the stand, item 5937 of the migrated project: the excerpt the model is
# shown is a clipped rendering whose message ends in an ellipsis, so a faithful
# quote carried a marker the real log never had, and every consumer grounding
# against the real log threw the explanation away.
class TestExplainerQuoteClipMarker:
    def _validate(self, quoted: list[str], corpus_log: str) -> dict:
        role = ExplainerRole()
        out = {"explanation": "why it failed", "quoted_log_lines": list(quoted)}
        inp = {"_corpus": [corpus_log], "_corpus_log": [corpus_log], "_corpus_facts": []}
        assert role.post_validate(out, inp) is True
        return out

    def test_trailing_ellipsis_is_trimmed_not_rejected(self) -> None:
        corpus = "NoHttpResponseException: host:<NUM> failed to respond ...\nnext.frame.here"
        out = self._validate(["host:<NUM> failed to respond ..."], corpus)
        assert out["quoted_log_lines"] == ["host:<NUM> failed to respond"]

    def test_unicode_ellipsis_is_trimmed_too(self) -> None:
        corpus = "connection reset by peer …"
        out = self._validate(["connection reset by peer …"], corpus)
        assert out["quoted_log_lines"] == ["connection reset by peer"]

    def test_a_clean_quote_is_left_exactly_as_it_was(self) -> None:
        corpus = "assert 1 == 2 in module.py"
        out = self._validate(["assert 1 == 2"], corpus)
        assert out["quoted_log_lines"] == ["assert 1 == 2"]

    def test_an_invented_quote_still_fails(self) -> None:
        role = ExplainerRole()
        out = {"explanation": "x", "quoted_log_lines": ["database was dropped ..."]}
        inp = {"_corpus": ["real log"], "_corpus_log": ["real log"], "_corpus_facts": []}
        assert role.post_validate(out, inp) is False

    def test_a_dot_inside_the_line_is_untouched(self) -> None:
        corpus = "org.apache.http.NoHttpResponseException: boom"
        out = self._validate(["org.apache.http.NoHttpResponseException: boom"], corpus)
        assert out["quoted_log_lines"] == ["org.apache.http.NoHttpResponseException: boom"]


# The rubric rule id is carried in its own field, where a reader can be shown the
# rule's name. In the sentence it is jargon nobody can resolve, and that sentence
# is copied onto the defect comment, which outlives the modal and its tooltips.
# Measured before this existed: 352 of 774 stored reasons cited a rule id.
class TestStripRuleIds:
    def test_trailing_reference_and_its_connective_go(self) -> None:
        assert (
            strip_rule_ids("Assertion failure in test code, aligning with R1.")
            == "Assertion failure in test code."
        )

    def test_leading_connective_goes_too(self) -> None:
        assert (
            strip_rule_ids("Matches R12, the failure passed on retry.")
            == "The failure passed on retry."
        )

    def test_a_clause_that_only_cited_the_rule_is_dropped_whole(self) -> None:
        assert (
            strip_rule_ids("Element not found in the DOM. Rule R3 applies.")
            == "Element not found in the DOM."
        )

    def test_a_bracketed_reference_goes(self) -> None:
        assert (
            strip_rule_ids("TimeoutException indicates infrastructure issue (R6).")
            == "TimeoutException indicates infrastructure issue."
        )

    def test_a_sentence_without_a_reference_is_untouched(self) -> None:
        text = "The service refused the connection and never answered."
        assert strip_rule_ids(text) == text

    def test_an_unrelated_capital_r_token_survives(self) -> None:
        text = "The R2D2 fixture returned nothing."
        assert strip_rule_ids(text) == text

    def test_empty_input_is_empty_output(self) -> None:
        assert strip_rule_ids(None) == ""
        assert strip_rule_ids("") == ""


class TestRubricTableIsOneSource:
    def test_every_rule_has_a_reader_facing_name(self) -> None:
        for rule_id, rule in RUBRIC.items():
            assert rule.name and rule.name[0].isupper(), rule_id
            assert "R" + rule_id[1:] not in rule.name

    def test_the_label_map_and_schema_derive_from_the_table(self) -> None:
        assert set(RUBRIC_LABEL) == set(RUBRIC)
        enum = ColdStartRole.schema["properties"]["rubric_rule_matched"]["enum"]
        assert enum == [*RUBRIC, "none"]

    def test_the_prompt_lists_every_rule(self) -> None:
        for rule_id in RUBRIC:
            assert f"\n{rule_id:<3} " in "\n" + _SYSTEM, rule_id

    def test_the_prompt_forbids_citing_rule_ids(self) -> None:
        assert "Do NOT mention rule ids" in _SYSTEM

    def test_an_unknown_rule_has_no_name_and_none_is_blank(self) -> None:
        assert rubric_rule_name("none") == ""
        assert rubric_rule_name(None) == ""
        assert rubric_rule_name("R999") == ""
