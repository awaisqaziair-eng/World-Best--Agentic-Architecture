"""A small, dependency-free JSON Schema validator with *lenient coercion*.

Models routinely send ``"5"`` for an integer or ``"true"`` for a boolean, or a
bare string where an array of one string was expected. Rejecting those wastes a
turn; silently accepting garbage corrupts behaviour. We coerce the unambiguous
cases, record each coercion, and reject the rest with a precise, model-readable
error path (``$.items[2].status: must be one of [...]``).

Supported keywords: type (incl. unions), properties, required,
additionalProperties (bool), items, enum, const, minimum, maximum,
minLength, maxLength, minItems, maxItems, default.
"""

from __future__ import annotations

from typing import Any

from .. import jsonutil

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class SchemaError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _is_type(value: Any, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    if t == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _PY_TYPES.get(t, (object,)))


def _coerce(value: Any, t: str) -> tuple[bool, Any]:
    """Try to coerce ``value`` to JSON type ``t``. Returns (ok, new_value)."""
    try:
        if t == "integer":
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                return True, int(value.strip())
            if isinstance(value, float) and value.is_integer():
                return True, int(value)
        elif t == "number" and isinstance(value, str):
            return True, float(value.strip())
        elif t == "boolean" and isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return True, value.strip().lower() == "true"
        elif t == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True, str(value)
        elif t == "array":
            if isinstance(value, str):
                s = value.strip()
                if s.startswith("["):
                    parsed = jsonutil.loads_lenient(s, expect=list)
                    return True, parsed
                return True, [value]
            if isinstance(value, dict):
                return True, [value]
        elif t == "object" and isinstance(value, str) and value.strip().startswith("{"):
            return True, jsonutil.loads_lenient(value, expect=dict)
    except (ValueError, jsonutil.JSONRepairError):
        pass
    return False, value


def validate(value: Any, schema: dict[str, Any], path: str = "$", notes: list[str] | None = None) -> Any:
    """Validate ``value`` against ``schema``; return the (possibly coerced) value.

    Raises ``SchemaError`` listing every violation found.
    """
    notes = notes if notes is not None else []
    errors: list[str] = []
    out = _validate(value, schema, path, errors, notes)
    if errors:
        raise SchemaError(errors)
    return out


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str], notes: list[str]) -> Any:
    if not schema:
        return value
    types = schema.get("type")
    if types is not None:
        tlist = types if isinstance(types, list) else [types]
        if not any(_is_type(value, t) for t in tlist):
            for t in tlist:
                ok, new = _coerce(value, t)
                if ok and _is_type(new, t):
                    notes.append(f"{path}: coerced {type(value).__name__} to {t}")
                    value = new
                    break
            else:
                errors.append(f"{path}: expected {' or '.join(tlist)}, got {type(value).__name__}")
                return value
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            value = [_validate(v, item_schema, f"{path}[{i}]", errors, notes) for i, v in enumerate(value)]
    if isinstance(value, dict):
        props: dict[str, Any] = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required property missing")
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in props:
                out[k] = _validate(v, props[k], f"{path}.{k}", errors, notes)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{k}: unexpected property (allowed: {sorted(props)})")
            else:
                out[k] = v
        for k, sub in props.items():
            if k not in out and isinstance(sub, dict) and "default" in sub:
                out[k] = sub["default"]
        value = out
    return value
