"""A tiny draft-07 JSON-Schema validator (spec 04 §3.0 step 5).

The role schemas are a closed, fixed subset of draft-07 — objects with typed
properties, ``enum``, ``maxLength``, ``maxItems``, array ``items``, ``required``
and ``additionalProperties: false``. Rather than pull in a dependency, this
module validates exactly that subset. We never trust the server-side ``format``
constraint alone (§3.0): the parsed output is re-validated client-side here, then
by role post-validation.
"""

from __future__ import annotations

from typing import Any

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ValueError):
    """The instance does not conform to the schema."""


def _check_type(value: Any, type_spec: Any, path: str) -> None:
    types = [type_spec] if isinstance(type_spec, str) else list(type_spec)
    for t in types:
        expected = _TYPE_MAP[t]
        # bool is a subclass of int; keep them distinct so a boolean never
        # satisfies "integer"/"number" and vice versa.
        if t in ("integer", "number") and isinstance(value, bool):
            continue
        if t == "boolean" and not isinstance(value, bool):
            continue
        if isinstance(value, expected):
            return
    raise SchemaError(f"{path}: expected type {type_spec!r}, got {type(value).__name__}")


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate ``instance`` against ``schema``; raise :class:`SchemaError` if invalid."""
    if "enum" in schema:
        if instance not in schema["enum"]:
            raise SchemaError(f"{path}: {instance!r} not in enum {schema['enum']!r}")
        # enum members are literals; no further structural checks needed.
        return

    if "type" in schema:
        _check_type(instance, schema["type"], path)

    if isinstance(instance, str) and "maxLength" in schema:
        if len(instance) > schema["maxLength"]:
            raise SchemaError(f"{path}: string longer than maxLength {schema['maxLength']}")

    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaError(f"{path}: array longer than maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(instance):
                validate(item, item_schema, f"{path}[{i}]")

    if isinstance(instance, dict):
        props: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                raise SchemaError(f"{path}: missing required property {key!r}")
        if schema.get("additionalProperties", True) is False:
            extra = set(instance) - set(props)
            if extra:
                raise SchemaError(f"{path}: additional properties not allowed: {sorted(extra)}")
        for key, sub in props.items():
            if key in instance:
                validate(instance[key], sub, f"{path}.{key}")


def is_valid(instance: Any, schema: dict[str, Any]) -> bool:
    """Return ``True`` iff ``instance`` conforms to ``schema``."""
    try:
        validate(instance, schema)
    except SchemaError:
        return False
    return True
