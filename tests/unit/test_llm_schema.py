"""Draft-07 subset validator tests (spec 04 §3.0 step 5)."""

from __future__ import annotations

from analyzer_ng.llm.roles.explainer import _SCHEMA as EXPLAINER_SCHEMA
from analyzer_ng.llm.schema import is_valid, validate

_OBJ = {
    "type": "object",
    "properties": {
        "choice": {"enum": ["a", "b", "none"]},
        "reason": {"type": "string", "maxLength": 5},
    },
    "required": ["choice", "reason"],
    "additionalProperties": False,
}


def test_valid_object() -> None:
    assert is_valid({"choice": "a", "reason": "hi"}, _OBJ)


def test_out_of_enum_rejected() -> None:
    assert not is_valid({"choice": "z", "reason": "hi"}, _OBJ)


def test_extra_field_rejected() -> None:
    assert not is_valid({"choice": "a", "reason": "hi", "x": 1}, _OBJ)


def test_missing_required_rejected() -> None:
    assert not is_valid({"choice": "a"}, _OBJ)


def test_max_length_rejected() -> None:
    assert not is_valid({"choice": "a", "reason": "toolong"}, _OBJ)


def test_nullable_union_type() -> None:
    schema = {"type": ["string", "null"]}
    assert is_valid("x", schema)
    assert is_valid(None, schema)
    assert not is_valid(3, schema)


def test_array_items_and_maxitems() -> None:
    schema = {"type": "array", "items": {"type": "string"}, "maxItems": 2}
    assert is_valid(["a", "b"], schema)
    assert not is_valid(["a", "b", "c"], schema)
    assert not is_valid(["a", 1], schema)


def test_bool_is_not_integer() -> None:
    assert not is_valid(True, {"type": "integer"})


def test_explainer_schema_accepts_wellformed() -> None:
    assert is_valid({"explanation": "ok", "quoted_lines": ["x"]}, EXPLAINER_SCHEMA)
    assert not is_valid({"explanation": "ok", "quoted_lines": ["a", "b", "c"]}, EXPLAINER_SCHEMA)


def test_validate_raises_with_path() -> None:
    try:
        validate({"choice": "z", "reason": "hi"}, _OBJ)
    except ValueError as exc:
        assert "choice" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SchemaError")
