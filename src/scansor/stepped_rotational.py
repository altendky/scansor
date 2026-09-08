from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from scansor.errors import ScansorError
from scansor.mapping_models import MappingThresholds

R1 = 0.012
R2 = 0.018
R3 = 0.014
S1 = 0.020
S2 = 0.050
S3 = 0.080
DATUM_X = 0.016
DATUM_HALF_WIDTH = 0.008246211251235319


@dataclass(frozen=True)
class Element:
    element_id: str
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]


@dataclass(frozen=True)
class SupportEvaluation:
    clearance_m: float
    projected_inside: bool
    signed_distance_m: float


@dataclass(frozen=True)
class NominalSupportCandidate:
    absolute_distance_m: float
    element_id: str
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]
    signed_distance_m: float


@dataclass(frozen=True)
class NominalSupportAssessment:
    candidates: tuple[NominalSupportCandidate, ...]
    geometric_clearance_m: float | None
    outcome: Literal["assigned", "ambiguous", "gap", "outlier", "transition"]


def _elements(variant: str) -> tuple[Element, ...]:
    items = [
        Element("cylinder.band-1", "cylindrical"),
        Element("cylinder.band-2", "cylindrical"),
        Element("cylinder.band-3", "cylindrical"),
        Element("plane.station-0", "axial-planar"),
        Element("plane.station-20", "axial-planar"),
        Element("plane.station-50", "axial-planar"),
        Element("plane.station-80", "axial-planar"),
    ]
    if variant == "asymmetric-datum-flat":
        items.append(Element("plane.datum-flat", "datum-planar"))
    return tuple(items)


def _support(
    point: np.ndarray, element: Element, asymmetric: bool
) -> SupportEvaluation:
    x, y, z = (float(value) for value in point)
    radial = math.hypot(x, y)
    if element.element_id.startswith("cylinder"):
        index = int(element.element_id[-1]) - 1
        radii = (R1, R2, R3)
        stations = (0.0, S1, S2, S3)
        radius = radii[index]
        if radial == 0.0:
            return SupportEvaluation(0.0, False, -radius)
        projected_x = radius * x / radial
        clearances = [z - stations[index], stations[index + 1] - z]
        inside = min(clearances) >= 0.0
        if asymmetric and index == 1:
            clearances.append(DATUM_X - projected_x)
            inside = inside and projected_x <= DATUM_X
        return SupportEvaluation(min(clearances), inside, radial - radius)

    if element.element_id == "plane.datum-flat":
        clearances = [
            y + DATUM_HALF_WIDTH,
            DATUM_HALF_WIDTH - y,
            z - S1,
            S2 - z,
        ]
        return SupportEvaluation(min(clearances), min(clearances) >= 0.0, x - DATUM_X)

    station, inner, outer, sign = {
        "plane.station-0": (0.0, 0.0, R1, -1.0),
        "plane.station-20": (S1, R1, R2, -1.0),
        "plane.station-50": (S2, R3, R2, 1.0),
        "plane.station-80": (S3, 0.0, R3, 1.0),
    }[element.element_id]
    clearances = [radial - inner, outer - radial]
    inside = min(clearances) >= 0.0
    if asymmetric and element.element_id in {"plane.station-20", "plane.station-50"}:
        clearances.append(DATUM_X - x)
        inside = inside and x <= DATUM_X
    return SupportEvaluation(min(clearances), inside, sign * (z - station))


def assess_nominal_support(
    point_model_m: tuple[float, float, float],
    variant: Literal["axisymmetric", "asymmetric-datum-flat"],
    thresholds: MappingThresholds,
) -> NominalSupportAssessment:
    """Classify one point using only the fixed nominal mapping supports."""
    point = np.asarray(point_model_m, dtype=np.float64)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ScansorError("nominal support point must be a finite three-vector")
    asymmetric = variant == "asymmetric-datum-flat"
    candidates: list[NominalSupportCandidate] = []
    projected_inside = False
    transition = False
    for element in _elements(variant):
        evaluation = _support(point, element, asymmetric)
        if not evaluation.projected_inside:
            continue
        projected_inside = True
        absolute = abs(evaluation.signed_distance_m)
        if absolute > thresholds.max_support_distance_m:
            continue
        if evaluation.clearance_m < thresholds.transition_guard_m:
            transition = True
            continue
        candidates.append(
            NominalSupportCandidate(
                absolute_distance_m=absolute,
                element_id=element.element_id,
                kind=element.kind,
                signed_distance_m=evaluation.signed_distance_m,
            )
        )
    candidates.sort(key=lambda item: (item.absolute_distance_m, item.element_id))
    clearance = (
        candidates[1].absolute_distance_m - candidates[0].absolute_distance_m
        if len(candidates) > 1
        else None
    )
    ambiguous = (
        clearance is not None and clearance < thresholds.minimum_geometric_clearance_m
    )
    if transition:
        outcome: Literal["assigned", "ambiguous", "gap", "outlier", "transition"] = (
            "transition"
        )
    elif ambiguous:
        outcome = "ambiguous"
    elif candidates:
        outcome = "assigned"
    elif projected_inside:
        outcome = "outlier"
    else:
        outcome = "gap"
    return NominalSupportAssessment(
        candidates=tuple(candidates),
        geometric_clearance_m=clearance,
        outcome=outcome,
    )
