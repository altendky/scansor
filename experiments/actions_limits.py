"""Portable actions budgets, separate from source-buffer admission limits."""

import json
from typing import cast

MAX_CONTROL_JSON_BYTES = 1_000_000
MAX_ACTIONS_JSON_BYTES = 32 * 1024 * 1024
MAX_ACTIONS_REQUEST_BYTES = MAX_ACTIONS_JSON_BYTES + MAX_CONTROL_JSON_BYTES
MAX_VOLUME_ENCODED_BYTES = 16 * 1024 * 1024
MAX_VOLUME_EXPANDED_BYTES = 128 * 1024 * 1024


def actions_limits() -> dict[str, int]:
    """Return a detached profile shared by native, worker, and browser import."""
    return {
        "max_json_bytes": MAX_ACTIONS_JSON_BYTES,
        "max_request_bytes": MAX_ACTIONS_REQUEST_BYTES,
        "max_volume_encoded_bytes": MAX_VOLUME_ENCODED_BYTES,
        "max_volume_expanded_bytes": MAX_VOLUME_EXPANDED_BYTES,
    }


def actions_profile() -> dict[str, object]:
    return {"supported": True, "version": 2, "limits": actions_limits()}


def check_json_budget(
    value: object, maximum: int, description: str, *, portable: bool = False
) -> None:
    """Bound UTF-8 JSON bytes without constructing a second full JSON string."""
    size = 1 if portable else 0  # Saved actions end with a newline.
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        indent=2 if portable else None,
        separators=None if portable else (",", ":"),
    )
    for part in encoder.iterencode(value):
        size += len(part.encode("utf-8"))
        if size > maximum:
            raise ValueError(f"{description} exceeds the {maximum:,}-byte JSON limit")


def check_actions_json(value: object) -> None:
    check_json_budget(value, MAX_ACTIONS_JSON_BYTES, "Actions")


def check_portable_actions(value: object) -> None:
    """Reserve the saved file's whitespace before accepting generated work."""
    check_json_budget(value, MAX_ACTIONS_JSON_BYTES, "Saved actions", portable=True)


def check_actions_envelope(value: object) -> None:
    """Recipe-bearing requests retain the small budget for other controls."""
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object request")
    payload = cast(dict[str, object], value)
    controls = {key: item for key, item in payload.items() if key != "recipe"}
    check_json_budget(controls, MAX_CONTROL_JSON_BYTES, "Controls")
    if payload.get("recipe") is not None:
        check_actions_json(payload["recipe"])
