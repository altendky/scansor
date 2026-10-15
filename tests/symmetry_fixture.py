"""Independent strict-PLY observations and analytic symmetry truth for #273.

No fitting or production geometry transforms generate these fixtures. Each
selected vertex owns one triangle with two unselected satellites, so incident
areas supply real, nonuniform weights even for selected collinear plane rows.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.support_intent import (
    PlaneSupport,
    PointSupport,
    SelectionSupport,
    SupportIntent,
)

Array = NDArray[np.float64]
SurfaceKind = Literal["plane", "cylinder", "cone"]
SymmetryMode = Literal["rotation", "mirror"]
SupportKind = Literal["auto", "mixed", "point", "plane"]


def _unit(value: Array) -> Array:
    return value / np.linalg.norm(value)


def _frame(direction: Array) -> tuple[Array, Array]:
    reference = np.eye(3)[int(np.argmin(abs(direction)))]
    first = _unit(np.asarray(np.cross(direction, reference), dtype=np.float64))
    return first, np.cross(direction, first)


def _rotation(direction: Array, angle: float) -> Array:
    x, y, z = direction
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * cross
        + (1 - np.cos(angle)) * np.outer(direction, direction)
    )


@dataclass(frozen=True)
class RawSymmetryFixture:
    """Generated source, authored selection IDs and independently known geometry.

    ``positions``/``normals`` are pre-float32 source rows (including noise and
    satellites); ``source_positions``/``source_normals`` are their actual PLY
    float32 values. ``geometric_normals`` precedes optional misleading normals.
    ``incident_weights`` is independently calculated from the float32 triangle
    positions, in original row order. Only ``ids`` rows are fit observations.

    ``truth`` records are raw canonical revolution geometry or plane equations,
    without solver/chart metadata. ``supports`` contains every patch key: None
    for planes, SelectionSupport for main sides and automatic members, otherwise
    the independently authored Point/Plane carrier. ``member_transforms`` are
    full column-vector 4x4 maps from member zero to each member in source space.
    ``quantization_bound`` bounds selected-point Euclidean rounding error versus
    the prescribed geometry, in addition to the separately requested noise.
    """

    raw: bytes
    workspace: ScanWorkspace
    ids: dict[str, list[int]]
    kind: SurfaceKind
    mode: SymmetryMode
    members: tuple[str, ...]
    positions: Array
    normals: Array
    geometric_normals: Array
    source_positions: NDArray[np.float32]
    source_normals: NDArray[np.float32]
    triangles: NDArray[np.int32]
    incident_weights: Array
    truth: dict[str, dict[str, Any]]
    supports: dict[str, SupportIntent | None]
    common_point: Array
    common_axis: Array
    mirror_normal: Array
    member_transforms: tuple[Array, ...]
    quantization_bound: float


def raw_symmetry_fixture(
    kind: SurfaceKind = "cylinder",
    mode: SymmetryMode = "rotation",
    *,
    oblique: bool = False,
    noise: float = 0.0,
    joint_only: bool = False,
    partial: bool = False,
    misleading_normals: bool = False,
    support_kind: SupportKind = "mixed",
    coaxial_members: bool = False,
    tangent_mirror: bool = False,
) -> RawSymmetryFixture:
    """Build a bounded generated mesh without an example, seed or fit frame.

    Keys are ``main``/``other`` coaxial cylinders (radii2/3.5), ``cap`` normal
    to their common axis, and ``member_0`` through ``member_2`` for rotations or
    ``member_1`` for mirrors. Ordinary lateral members have radius2, or cone
    reference radius-1/taper+.5, with selected heights8+i through13+i. Mixed
    support alternates PointSupport and perpendicular authored PlaneSupport.

    ``joint_only`` is plane-only: each selected member is exactly collinear in
    source float32, while back-transformed lines span a plane at source precision.
    ``coaxial_members`` is lateral-only: reflection about an axis-containing plane
    leaves both infinite surfaces identical, supplying no mirror-clock evidence.
    ``tangent_mirror`` is plane/mirror-only: members have normals +/-mirror_normal
    and pass through C+/-2*mirror_normal. Main/other radii remain2/3.5, so an
    equal-radius plus radius-distance composition has deliberately conflicting
    evidence; its common radius must follow the full weighted objective.
    ``partial`` limits lateral azimuths; ``misleading_normals`` changes admitted
    normals only, leaving positions, triangle weights and geometric truth intact.
    These options construct cases; their eligible/adverse expectation belongs to
    the caller's preregistration, not this data helper.
    """
    if kind not in {"plane", "cylinder", "cone"} or mode not in {"rotation", "mirror"}:
        raise ValueError("unsupported generated symmetry kind or mode")
    if not np.isfinite(noise) or noise < 0:
        raise ValueError("noise must be finite and nonnegative")
    if support_kind not in {"auto", "mixed", "point", "plane"}:
        raise ValueError("unsupported generated support mode")
    if joint_only and kind != "plane":
        raise ValueError("joint-only collinear observations require plane members")
    if coaxial_members and kind == "plane":
        raise ValueError("coaxial members require cylinders or cones")
    if tangent_mirror and (kind != "plane" or mode != "mirror"):
        raise ValueError("tangent mirror observations require plane mirror members")

    pose = _rotation(_unit(np.array([1.0, -2, 3])), 0.67) if oblique else np.eye(3)
    translation = np.array([7.0, -4, 2]) if oblique else np.zeros(3)
    center = pose @ np.array([2.0, -3, 4]) + translation
    central = pose @ (np.array([np.sqrt(2), 0, 1]) / np.sqrt(3))
    mirror_normal = pose @ np.array([-0.5, 0.5, 1 / np.sqrt(2)])
    base_origin = (
        center + 4 * central
        if coaxial_members
        else pose @ np.array([6.0, 0, 2]) + translation
    )
    base_direction = central if coaxial_members else pose @ np.array([0.0, 0, 1])
    base_normal = pose @ _unit(np.array([0.3, 0.6, 0.7]))
    if tangent_mirror:
        base_origin = center + 2 * mirror_normal
        base_normal = mirror_normal
    angles = (
        np.linspace(-0.8, 1.2, 16)
        if partial
        else np.linspace(0, 2 * np.pi, 16, endpoint=False)
    )
    positions: list[Array] = []
    normals: list[Array] = []
    triangles: list[tuple[int, int, int]] = []
    ids: dict[str, list[int]] = {}
    truth: dict[str, dict[str, Any]] = {}
    supports: dict[str, SupportIntent | None] = {}

    def append(name: str, points: Array, directions: Array) -> None:
        selected: list[int] = []
        for point, normal in zip(points, directions, strict=True):
            index = len(positions)
            first, second = _frame(normal)
            # Unequal edge lengths, not synthetic weight arguments, determine
            # the incident contribution of each selected vertex after import.
            edge_a = 0.03 * (1 + 0.013 * (index % 17))
            edge_b = 0.05 * (1 + 0.021 * (index % 11))
            positions.extend((point, point + edge_a * first, point + edge_b * second))
            normals.extend((normal.copy(), normal.copy(), normal.copy()))
            triangles.append((index, index + 1, index + 2))
            selected.append(index)
        ids[name] = selected

    def lateral_points(
        anchor: Array,
        direction: Array,
        radius: float,
        taper: float,
        heights: Array,
    ) -> tuple[Array, Array]:
        first, second = _frame(direction)
        angle, height = np.meshgrid(angles, heights)
        radial = (
            np.cos(angle.ravel())[:, None] * first
            + np.sin(angle.ravel())[:, None] * second
        )
        rho = radius + taper * height.ravel()
        rho += noise * np.sin(np.arange(len(rho)) * 1.7)
        points = anchor + height.ravel()[:, None] * direction + rho[:, None] * radial
        directions = (radial - taper * direction) / np.hypot(1, taper)
        return points, directions

    def lateral_truth(
        family: str,
        anchor: Array,
        direction: Array,
        radius: float,
        taper: float,
        domain: tuple[float, float],
    ) -> dict[str, Any]:
        return {
            "kind": family,
            "origin": anchor.tolist(),
            "axis": direction.tolist(),
            "radius": radius,
            "slope": taper,
            "axial_domain": list(domain),
        }

    for name, radius in (("main", 2.0), ("other", 3.5)):
        points, directions = lateral_points(
            center, central, radius, 0, np.linspace(-3, 4, 5)
        )
        append(name, points, directions)
        truth[name] = lateral_truth(
            "cylinder", center, central, radius, 0.0, (-3.0, 4.0)
        )
        supports[name] = SelectionSupport()
    first, second = _frame(central)
    x, y = np.meshgrid([-2.0, -0.4, 0.7, 2.2], [-1.5, 0.2, 1.8])
    cap_origin = center + 5 * central
    points = cap_origin + x.ravel()[:, None] * first + y.ravel()[:, None] * second
    points += noise * np.sin(np.arange(len(points)) * 1.7)[:, None] * central
    append("cap", points, np.broadcast_to(central, points.shape))
    truth["cap"] = {
        "kind": "plane",
        "plane_equation": [*central.tolist(), float(central @ cap_origin)],
    }
    supports["cap"] = None

    members = tuple(
        f"member_{index}" for index in range(3 if mode == "rotation" else 2)
    )
    transforms: list[Array] = []
    grid = 2.0**-18
    for index, name in enumerate(members):
        matrix = (
            _rotation(central, index * 2 * np.pi / 3)
            if mode == "rotation"
            else np.eye(3) - 2 * index * np.outer(mirror_normal, mirror_normal)
        )
        offset = center - matrix @ center
        transform = np.eye(4)
        transform[:3, :3], transform[:3, 3] = matrix, offset
        transforms.append(transform)
        anchor = matrix @ base_origin + offset
        direction = matrix @ base_direction
        normal = matrix @ base_normal
        if kind == "plane":
            first, second = _frame(base_normal)
            if joint_only:
                angle = (0.0, np.pi / 3, -np.pi / 5)[index]
                line = matrix @ (np.cos(angle) * first + np.sin(angle) * second)
                # Shared dyadic center/step preserve exact raw collinearity.
                # The small extra quantization is included in the reported bound.
                line_center = (
                    np.rint((anchor + noise * np.sin(index + 1) * normal) / grid) * grid
                )
                step = np.rint(line / grid) * grid
                points = line_center + np.array([-1.0, 0, 2])[:, None] * step
            else:
                x, y = np.meshgrid([-2.0, -0.5, 0.7, 2.5], [-1.5, -0.2, 1.8])
                local = (
                    base_origin
                    + x.ravel()[:, None] * first
                    + y.ravel()[:, None] * second
                )
                points = local @ matrix.T + offset
                points += noise * np.sin(np.arange(len(points)) * 1.7)[:, None] * normal
            append(name, points, np.broadcast_to(normal, points.shape))
            truth[name] = {
                "kind": kind,
                "plane_equation": [*normal.tolist(), float(normal @ anchor)],
            }
            supports[name] = None
            continue
        domain = (8.0 + index, 13.0 + index)
        radius, taper = (-1.0, 0.5) if kind == "cone" else (2.0, 0.0)
        points, directions = lateral_points(
            anchor, direction, radius, taper, np.linspace(*domain, 4)
        )
        append(name, points, directions)
        truth[name] = lateral_truth(kind, anchor, direction, radius, taper, domain)
        if support_kind == "auto":
            supports[name] = SelectionSupport()
        elif support_kind == "point" or (support_kind == "mixed" and index % 2 == 0):
            supports[name] = PointSupport(tuple(anchor), tuple(direction), domain)
        else:
            supports[name] = PlaneSupport(
                tuple(direction), float(direction @ anchor), domain
            )

    coordinates = np.asarray(positions, dtype=np.float64)
    geometric_normals = np.asarray(normals, dtype=np.float64)
    supplied_normals = geometric_normals.copy()
    if misleading_normals:
        supplied_normals[:] = _unit(np.array([1.0, 2, -1]))
    source_positions = coordinates.astype("<f4")
    source_normals = supplied_normals.astype("<f4")
    faces = np.asarray(triangles, dtype=np.int32)
    vertices = np.column_stack((source_positions, source_normals)).astype("<f4")
    records = np.empty(len(faces), dtype=[("count", "u1"), ("indices", "<i4", (3,))])
    records["count"], records["indices"] = 3, faces
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Independently generated symmetry observations and satellite triangles\n"
        f"element vertex {len(vertices)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    raw = header + vertices.tobytes() + records.tobytes()
    workspace = prepare_scan(
        io.BytesIO(raw),
        f"generated-{mode}-{kind}.ply",
        ScanLimits(4_000_000, 20_000, 20_000, 50_000_000),
    )
    triangles_xyz = source_positions.astype(np.float64)[faces]
    incident = (
        np.linalg.norm(
            np.cross(
                triangles_xyz[:, 1] - triangles_xyz[:, 0],
                triangles_xyz[:, 2] - triangles_xyz[:, 0],
            ),
            axis=1,
        )
        / 6
    )
    incident_weights = np.repeat(incident, 3)
    maximum = float(np.max(abs(coordinates)))
    quantization_bound = float(np.sqrt(3) * np.spacing(np.float32(maximum)) / 2)
    if joint_only:
        quantization_bound += 1.5 * np.sqrt(3) * grid
    return RawSymmetryFixture(
        raw,
        workspace,
        ids,
        kind,
        mode,
        members,
        coordinates,
        supplied_normals,
        geometric_normals,
        source_positions,
        source_normals,
        faces,
        incident_weights,
        truth,
        supports,
        center,
        central,
        mirror_normal,
        tuple(transforms),
        quantization_bound,
    )
