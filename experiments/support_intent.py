"""Immutable authored support coordinates, separate from disposable fit geometry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, cast

import numpy as np

SUPPORT_POLICY_VERSION = "explicit-support-intent-v1"
Vector = tuple[float, float, float]
Interval = tuple[float, float]


class SupportIntentFailure(ValueError):
    def __init__(self, message: str, *, code: str = "unsupported-support") -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "support_intent_failure",
            "code": code,
            "message": message,
        }
        super().__init__(message)


def _number(value: Any) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise SupportIntentFailure(
            "support values must be finite real numbers", code="invalid-support"
        )
    number = float(value)
    if not np.isfinite(number):
        raise SupportIntentFailure(
            "support values must be finite real numbers", code="invalid-support"
        )
    return number


def _tuple(value: Any, size: int) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise SupportIntentFailure(
            "support vector/interval has the wrong shape", code="invalid-support"
        )
    try:
        values = tuple(value)
    except TypeError as error:
        raise SupportIntentFailure(
            "support vector/interval has the wrong shape", code="invalid-support"
        ) from error
    if len(values) != size:
        raise SupportIntentFailure(
            "support vector/interval has the wrong shape", code="invalid-support"
        )
    return tuple(_number(v) for v in values)


def _vector(value: Any) -> Vector:
    x, y, z = _tuple(value, 3)
    return x, y, z


def _unit(value: Any) -> tuple[Vector, float, float]:
    vector = np.asarray(_vector(value))
    maximum = float(np.max(np.abs(vector)))
    if maximum <= 0:
        raise SupportIntentFailure(
            "support direction must be nonzero", code="invalid-support"
        )
    # A serialized unit vector has already incurred rounding. Preserve its
    # authored plane offset on replay rather than renormalizing it repeatedly.
    if abs(float(np.linalg.norm(vector)) - 1.0) <= 4 * np.finfo(float).eps:
        return _vector(vector), 1.0, 1.0
    scaled = vector / maximum
    small_length = float(np.linalg.norm(scaled))
    return _vector(scaled / small_length), maximum, small_length


def _interval(value: Any) -> Interval:
    lo, hi = _tuple(value, 2)
    if lo >= hi:
        raise SupportIntentFailure(
            "support interval must be increasing", code="invalid-support"
        )
    return lo, hi


@dataclass(frozen=True)
class SelectionSupport:
    reference: Vector | None = None

    def __post_init__(self) -> None:
        if self.reference is not None:
            object.__setattr__(self, "reference", _vector(self.reference))


@dataclass(frozen=True)
class PlaneSupport:
    normal: Vector
    offset: float
    interval: Interval

    def __post_init__(self) -> None:
        unit, maximum, length = _unit(self.normal)
        original = _number(self.offset)
        offset = original / maximum / length
        if not np.isfinite(offset) or (original != 0 and offset == 0):
            raise SupportIntentFailure(
                "normalized plane offset is unrepresentable", code="invalid-support"
            )
        object.__setattr__(self, "normal", unit)
        object.__setattr__(self, "offset", offset)
        object.__setattr__(self, "interval", _interval(self.interval))


@dataclass(frozen=True)
class PointSupport:
    reference: Vector
    direction: Vector
    interval: Interval

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", _vector(self.reference))
        object.__setattr__(self, "direction", _unit(self.direction)[0])
        object.__setattr__(self, "interval", _interval(self.interval))


type SupportIntent = SelectionSupport | PlaneSupport | PointSupport


@dataclass(frozen=True)
class ResolvedSupport:
    origin: Vector
    axis: Vector
    domain: Interval
    origin_shift: float
    axis_sign: float


def support_record(intent: object) -> dict[str, Any]:
    if isinstance(intent, SelectionSupport):
        return (
            {"kind": "selection"}
            if intent.reference is None
            else {"kind": "selection", "reference": list(intent.reference)}
        )
    if isinstance(intent, PlaneSupport):
        return {
            "kind": "reference-plane",
            "normal": list(intent.normal),
            "offset": intent.offset,
            "interval": list(intent.interval),
        }
    if isinstance(intent, PointSupport):
        return {
            "kind": "point",
            "reference": list(intent.reference),
            "direction": list(intent.direction),
            "interval": list(intent.interval),
        }
    raise SupportIntentFailure("unknown support intent", code="invalid-support")


def parse_support_record(record: Any) -> SupportIntent:
    if not isinstance(record, Mapping):
        raise SupportIntentFailure(
            "support intent must be a record", code="invalid-support"
        )
    record = cast(Mapping[str, Any], record)
    kind = record.get("kind")
    if kind == "selection" and set(record) <= {"kind", "reference"}:
        return SelectionSupport(record.get("reference"))
    if kind == "reference-plane" and set(record) == {
        "kind",
        "normal",
        "offset",
        "interval",
    }:
        return PlaneSupport(record["normal"], record["offset"], record["interval"])
    if kind == "point" and set(record) == {
        "kind",
        "reference",
        "direction",
        "interval",
    }:
        return PointSupport(
            record["reference"], record["direction"], record["interval"]
        )
    raise SupportIntentFailure(
        "unknown or malformed support intent record", code="invalid-support"
    )


def legacy_support(domain: Any, reference: Any = None) -> SupportIntent:
    return (
        SelectionSupport(reference)
        if domain is None
        else PlaneSupport((0.0, 0.0, 1.0), 0.0, domain)
    )


def support_from_result(result: Mapping[str, Any]) -> SupportIntent:
    if "support_intent" in result:
        return parse_support_record(result["support_intent"])
    mode = result.get("support_origin")
    if mode == "legacy-source-z0":
        if "source_axial_domain" not in result:
            raise SupportIntentFailure(
                "source-Z support omitted its authored interval", code="invalid-support"
            )
        return legacy_support(result["source_axial_domain"])
    if mode == "selection":
        # The centroid is derived gauge metadata, not a new authored point.
        return SelectionSupport()
    if mode not in (None, "point"):
        raise SupportIntentFailure(
            "support coordinate mode requires its authored record",
            code="invalid-support",
        )
    geometry = result.get("analytic")
    if not isinstance(geometry, Mapping):
        raise SupportIntentFailure(
            "canonical explicit support requires geometry", code="invalid-support"
        )
    geometry = cast(Mapping[str, Any], geometry)
    return PointSupport(
        result.get("anchor_reference", geometry.get("origin")),
        result.get("anchor_axis", geometry.get("axis")),
        result.get("anchor_axial_domain", geometry.get("axial_domain")),
    )


def resolve_support(
    intent: object,
    line_point: Any,
    line_axis: Any,
    selected_points: Any,
    *,
    reference: Any = None,
) -> ResolvedSupport:
    """Resolve each current line against a fixed authored coordinate reference."""
    point = np.asarray(_vector(line_point))
    incoming = np.asarray(_unit(line_axis)[0])
    axis = incoming.copy()
    sign = 1.0
    shift = 0.0
    if isinstance(intent, PlaneSupport):
        normal = np.asarray(intent.normal)
        dot = float(normal @ axis)
        if abs(dot) <= 1e-8:
            raise SupportIntentFailure(
                "support reference plane is parallel or nearly parallel to the axis"
            )
        sign = 1.0 if dot > 0 else -1.0
        axis *= sign
        with np.errstate(over="ignore", invalid="ignore"):
            delta = float((intent.offset - normal @ point) / (normal @ axis))
            bounds = np.asarray(intent.interval) + delta
    elif isinstance(intent, (SelectionSupport, PointSupport)):
        fixed_reference = intent.reference
        if fixed_reference is None:
            if reference is None:
                try:
                    selected = np.asarray(selected_points, dtype=float)
                except (TypeError, ValueError) as error:
                    raise SupportIntentFailure(
                        "selection support needs finite XYZ observations"
                    ) from error
                if (
                    selected.ndim != 2
                    or selected.shape[1] != 3
                    or not len(selected)
                    or not np.isfinite(selected).all()
                ):
                    raise SupportIntentFailure(
                        "selection support needs finite XYZ observations"
                    )
                fixed_reference = _vector(np.mean(selected, axis=0))
            else:
                fixed_reference = _vector(reference)
        with np.errstate(over="ignore", invalid="ignore"):
            shift = float((np.asarray(fixed_reference) - point) @ incoming)
            point = point + shift * incoming
        if isinstance(intent, PointSupport):
            sign = 1.0 if incoming @ intent.direction >= 0 else -1.0
            axis *= sign
            bounds = np.asarray(intent.interval)
        else:
            try:
                selected = np.asarray(selected_points, dtype=float)
            except (TypeError, ValueError) as error:
                raise SupportIntentFailure(
                    "selection support needs finite XYZ observations"
                ) from error
            if (
                selected.ndim != 2
                or selected.shape[1] != 3
                or not len(selected)
                or not np.isfinite(selected).all()
            ):
                raise SupportIntentFailure(
                    "selection support needs finite XYZ observations"
                )
            with np.errstate(over="ignore", invalid="ignore"):
                heights = (selected - point) @ axis
            bounds = np.array([np.min(heights), np.max(heights)])
    else:
        raise SupportIntentFailure("unknown support intent", code="invalid-support")
    if (
        not np.isfinite(point).all()
        or not np.isfinite(shift)
        or not np.isfinite(bounds).all()
        or bounds[0] >= bounds[1]
    ):
        raise SupportIntentFailure(
            "support interval is nonfinite or collapsed at this axis"
        )
    return ResolvedSupport(
        _vector(point), _vector(axis), (float(bounds[0]), float(bounds[1])), shift, sign
    )


def transform_support(intent: object, rotation: Any, translation: Any) -> SupportIntent:
    """Transform authored references using Xnew=X@rotation+translation."""
    try:
        matrix = np.asarray(rotation, dtype=float)
    except (TypeError, ValueError) as error:
        raise SupportIntentFailure(
            "support placement requires a proper rigid rotation", code="invalid-support"
        ) from error
    offset = np.asarray(_vector(translation))
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-10, rtol=1e-10)
        or not np.isclose(np.linalg.det(matrix), 1.0, atol=1e-10, rtol=1e-10)
    ):
        raise SupportIntentFailure(
            "support placement requires a proper rigid rotation", code="invalid-support"
        )
    if isinstance(intent, SelectionSupport):
        return (
            SelectionSupport()
            if intent.reference is None
            else SelectionSupport(
                _vector(np.asarray(intent.reference) @ matrix + offset)
            )
        )
    if isinstance(intent, PlaneSupport):
        normal = np.asarray(intent.normal) @ matrix
        return PlaneSupport(
            _vector(normal), float(intent.offset + normal @ offset), intent.interval
        )
    if isinstance(intent, PointSupport):
        return PointSupport(
            _vector(np.asarray(intent.reference) @ matrix + offset),
            _vector(np.asarray(intent.direction) @ matrix),
            intent.interval,
        )
    raise SupportIntentFailure("unknown support intent", code="invalid-support")
