"""Solve-local coordinate maps; primitive evaluation stays in its original chart.

The map changes numerical coordinates, not observation membership or geometry
validity. Its tangent converts an original-chart Jacobian to a solve-local one.
Condition numbers measured there describe the centered, length-scaled chart.
No rotation is performed: the existing positive-Z axis charts remain explicit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
NormalCallback = Callable[[Array], tuple[Array, Array]]


@dataclass(frozen=True)
class CoordinateFrame:
    origin: Array
    length: float

    @classmethod
    def from_observations(
        cls, points: Array, weights: Array | None = None
    ) -> CoordinateFrame:
        """Use every observation; normalized positive weights preserve units."""
        if (
            points.ndim != 2
            or points.shape[1] != 3
            or not len(points)
            or not np.isfinite(points).all()
        ):
            raise ValueError("expected finite XYZ observations")
        if weights is None:
            weights = np.ones(len(points))
        if (
            weights.shape != (len(points),)
            or not np.isfinite(weights).all()
            or np.any(weights <= 0)
        ):
            raise ValueError("coordinate weights must be finite and positive")
        normalized = weights / np.max(weights)
        normalized /= normalized.sum()
        if np.any(normalized <= 0):
            raise ValueError(
                "weight range underflows a positive observation contribution"
            )
        origin = points[0] + normalized @ (points - points[0])
        centered = points - origin
        magnitude = float(np.max(np.abs(centered)))
        if not np.isfinite(magnitude) or magnitude <= 0:
            raise ValueError("coordinate frame needs spatially distinct observations")
        scaled = centered / magnitude
        length = magnitude * float(np.sqrt(normalized @ np.sum(scaled**2, axis=1)))
        if not np.isfinite(length) or length <= 0:
            raise ValueError("coordinate frame has no finite positive length")
        return cls(origin=origin, length=length)


@dataclass(frozen=True)
class AxisChart:
    """Indices of a positive-Z axis and its radius, optionally cone taper."""

    cx: int
    cy: int
    a: int
    b: int
    radius: int
    taper: int | None = None


@dataclass(frozen=True)
class GlobalPlaneOffset:
    """Global n·x=h; callback returns n and dn/d(original parameters)."""

    offset: int
    normal: NormalCallback


@dataclass(frozen=True)
class RelativePlaneOffset:
    """Plane distance from shared-axis anchor; n·axis=cos(tilt)."""

    offset: int
    axis: AxisChart
    tilt: int


@dataclass(frozen=True)
class FitCoordinates:
    frame: CoordinateFrame
    size: int
    axes: tuple[AxisChart, ...] = ()
    length_parameters: tuple[int, ...] = ()
    global_planes: tuple[GlobalPlaneOffset, ...] = ()
    relative_planes: tuple[RelativePlaneOffset, ...] = ()

    def _validate(self, parameters: Array) -> None:
        if parameters.shape != (self.size,) or not np.isfinite(parameters).all():
            raise ValueError("coordinate parameter shape or values are invalid")
        if (
            self.frame.origin.shape != (3,)
            or not np.isfinite(self.frame.origin).all()
            or not np.isfinite(self.frame.length)
            or self.frame.length <= 0
        ):
            raise ValueError("invalid solve-local coordinate frame")

    def encode(self, physical: Array) -> Array:
        """Rebase anchors and radii without changing physical geometry."""
        self._validate(physical)
        origin, length = self.frame.origin, self.frame.length
        local = physical.copy()
        for index in self.length_parameters:
            local[index] = physical[index] / length
        for axis in self.axes:
            a, b = physical[axis.a], physical[axis.b]
            delta = float(np.linalg.norm([a, b, 1.0])) * origin[2]
            local[axis.cx] = (physical[axis.cx] - origin[0] + a * origin[2]) / length
            local[axis.cy] = (physical[axis.cy] - origin[1] + b * origin[2]) / length
            taper = 0.0 if axis.taper is None else physical[axis.taper]
            local[axis.radius] = (physical[axis.radius] + taper * delta) / length
        for plane in self.global_planes:
            normal, _ = plane.normal(physical)
            local[plane.offset] = (physical[plane.offset] - normal @ origin) / length
        for plane in self.relative_planes:
            a, b = physical[plane.axis.a], physical[plane.axis.b]
            delta = float(np.linalg.norm([a, b, 1.0])) * origin[2]
            local[plane.offset] = (
                physical[plane.offset] - delta * np.cos(physical[plane.tilt])
            ) / length
        return local

    def decode(self, local: Array) -> tuple[Array, Array]:
        """Return original-chart parameters and their analytic local derivative.

        Evaluate original observations and original support/validity checks with
        the returned parameters. For an original Jacobian J, use J @ tangent.
        """
        self._validate(local)
        origin, length = self.frame.origin, self.frame.length
        physical = local.copy()
        tangent = np.eye(self.size)
        for index in self.length_parameters:
            physical[index] = length * local[index]
            tangent[index, index] = length
        for axis in self.axes:
            a, b = local[axis.a], local[axis.b]
            raw_length = float(np.linalg.norm([a, b, 1.0]))
            delta = raw_length * origin[2]
            physical[axis.cx] = origin[0] + length * local[axis.cx] - a * origin[2]
            physical[axis.cy] = origin[1] + length * local[axis.cy] - b * origin[2]
            tangent[axis.cx] = 0
            tangent[axis.cx, axis.cx] = length
            tangent[axis.cx, axis.a] = -origin[2]
            tangent[axis.cy] = 0
            tangent[axis.cy, axis.cy] = length
            tangent[axis.cy, axis.b] = -origin[2]
            taper = 0.0 if axis.taper is None else local[axis.taper]
            physical[axis.radius] = length * local[axis.radius] - taper * delta
            tangent[axis.radius] = 0
            tangent[axis.radius, axis.radius] = length
            if axis.taper is not None:
                tangent[axis.radius, axis.taper] = -delta
                tangent[axis.radius, axis.a] = -taper * origin[2] * a / raw_length
                tangent[axis.radius, axis.b] = -taper * origin[2] * b / raw_length
        # Normals depend on dimensionless slopes/angles, never offset variables.
        # Snapshot the base tangent so offset rows cannot affect each other.
        base_tangent = tangent.copy()
        for plane in self.global_planes:
            normal, derivative = plane.normal(physical)
            if normal.shape != (3,) or derivative.shape != (3, self.size):
                raise ValueError("invalid plane-normal callback shapes")
            physical[plane.offset] = length * local[plane.offset] + normal @ origin
            tangent[plane.offset] = origin @ derivative @ base_tangent
            tangent[plane.offset, plane.offset] += length
        for plane in self.relative_planes:
            a, b = local[plane.axis.a], local[plane.axis.b]
            raw_length = float(np.linalg.norm([a, b, 1.0]))
            delta = raw_length * origin[2]
            tilt = local[plane.tilt]
            physical[plane.offset] = length * local[plane.offset] + delta * np.cos(tilt)
            tangent[plane.offset] = 0
            tangent[plane.offset, plane.offset] = length
            tangent[plane.offset, plane.axis.a] = (
                origin[2] * a / raw_length * np.cos(tilt)
            )
            tangent[plane.offset, plane.axis.b] = (
                origin[2] * b / raw_length * np.cos(tilt)
            )
            tangent[plane.offset, plane.tilt] = -delta * np.sin(tilt)
        return physical, tangent
