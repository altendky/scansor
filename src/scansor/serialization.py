from __future__ import annotations

import hashlib
import json
from typing import Any, Never

from pydantic import BaseModel

from scansor.errors import ScansorError


def fail(message: str) -> Never:
    raise ScansorError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: BaseModel | Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_canonical_json(data: bytes, label: str, max_bytes: int) -> Any:
    if len(data) > max_bytes:
        fail(f"{label} exceeds the {max_bytes}-byte limit")
    try:
        value = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: fail(
                f"{label} contains nonfinite JSON token {token}"
            ),
        )
    except ScansorError:
        raise
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        OverflowError,
    ) as error:
        raise ScansorError(f"{label} is invalid JSON: {error}") from error
    try:
        encoded = canonical_json(value)
    except (RecursionError, ValueError, OverflowError) as error:
        raise ScansorError(
            f"{label} contains an invalid numeric value: {error}"
        ) from error
    if encoded != data:
        fail(f"{label} is not canonical JSON")
    return value
