"""Small JSON-Schema subset validator for tool inputs.

Supports: type (object/string/integer/number/boolean/array), properties,
required, additionalProperties=false, enum, minLength/maxLength,
minimum/maximum, items, maxItems. That is everything MUR AI tools use, and
it lets us reject malformed model output before any tool runs.
"""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict, "string": str, "array": list, "boolean": bool,
}


class SchemaError(ValueError):
    pass


def _check_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return isinstance(value, _TYPES[expected])


def validate(value: Any, schema: dict, path: str = "input") -> None:
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_check_type(value, t) for t in types):
            raise SchemaError(f"{path} must be {' or '.join(types)}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path} is too long (max {schema['maxLength']})")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path} must be <= {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path} has too many items (max {schema['maxItems']})")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                raise SchemaError(f"{path}.{req} is required")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise SchemaError(f"{path} has unknown field(s): {', '.join(sorted(extra))}")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, f"{path}.{key}")
