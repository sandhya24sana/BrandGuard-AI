from typing import Any, Dict, Iterable

from flask import abort, request


def json_object_payload() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Request body must be a JSON object")
    return payload


def validate_string_fields(
    payload: Dict[str, Any],
    field_names: Iterable[str],
    allow_none: bool = True,
) -> None:
    for field_name in field_names:
        value = payload.get(field_name)
        if field_name in payload and not isinstance(value, str):
            if allow_none and value is None:
                continue
            abort(400, description=f"{field_name} must be a string")


def is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
