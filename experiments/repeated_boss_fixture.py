"""Generate a triangulated repeated-boss fixture for selection-transfer experiments."""

from __future__ import annotations

import argparse
import hashlib
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, TypedDict

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scansor._plyio import Writer
from scansor.mesh_ply import mesh_header, mesh_layout
from scansor.selection_bundle import (
    SELECTION_BUNDLE_FORMAT,
    SELECTION_BUNDLE_STATUS,
    SelectionBundle,
    SelectionMembership,
    SelectionSource,
    vertex_ids_sha256,
)
from scansor.serialization import canonical_json, sha256

FIXTURE_FORMAT = "scansor-repeated-boss-selection-fixture-v1"
GENERATOR_REVISION = "repeated-boss-selection-generator-v1"
ROLE_NAMES = ("plate", "outer", "bore", "shoulder", "clock")
ROLE_CODES = {name: index for index, name in enumerate(ROLE_NAMES)}
REGION_BOUNDS = {
    "outer": ((0.30, 0.68), (0.20, 0.82)),
    "bore": ((0.10, 0.36), (0.20, 0.82)),
    "shoulder": ((0.30, 0.68), (0.18, 0.82)),
    "clock": ((0.16, 0.84), (0.20, 0.82)),
}

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int32]


class FixtureRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False
    )


class PlateSpec(FixtureRecord):
    size_mm: tuple[float, float, float]


class BossSpec(FixtureRecord):
    bore_radius_mm: float = Field(gt=0.0)
    flat_offset_mm: float = Field(gt=0.0)
    height_mm: float = Field(gt=0.0)
    outer_radius_mm: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_radii(self) -> BossSpec:
        if not self.bore_radius_mm < self.flat_offset_mm < self.outer_radius_mm:
            raise ValueError("boss radii require bore < flat offset < outer")
        return self


class OccurrenceSpec(FixtureRecord):
    center_mm: tuple[float, float]
    clock_degrees: float
    occurrence_id: str = Field(pattern=r"^boss-[a-z]$")


class ImperfectionSpec(FixtureRecord):
    bore_ovality_mm: float = Field(ge=0.0)
    outer_dent_depth_mm: float = Field(ge=0.0)
    outer_ovality_mm: float = Field(ge=0.0)
    outer_taper_mm: float = Field(ge=0.0)
    plate_bow_mm: float = Field(ge=0.0)
    shoulder_height_mm: float = Field(ge=0.0)


class TessellationSpec(FixtureRecord):
    axial_segments: int = Field(ge=3, le=256)
    angular_segments: int = Field(ge=16, le=512)
    plate_x_segments: int = Field(ge=4, le=256)
    plate_y_segments: int = Field(ge=4, le=256)
    radial_segments: int = Field(ge=2, le=64)


class PoseSpec(FixtureRecord):
    rotation_xyz_degrees: tuple[float, float, float]
    translation_mm: tuple[float, float, float]


class RealizationSpec(FixtureRecord):
    as_built: bool
    bias_amplitude_mm: float = Field(ge=0.0, le=1.0)
    occlusion_profile: Literal["none", "capture-a", "capture-b"]
    pose: PoseSpec
    realization_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    seed: int = Field(ge=0)
    sensor_sigma_mm: float = Field(ge=0.0, le=1.0)
    tessellation: TessellationSpec


class FixtureSpec(FixtureRecord):
    boss: BossSpec
    format: Literal["scansor-repeated-boss-selection-fixture-v1"] = FIXTURE_FORMAT
    imperfections: ImperfectionSpec
    occurrences: tuple[OccurrenceSpec, ...] = Field(min_length=2, max_length=12)
    plate: PlateSpec
    realizations: tuple[RealizationSpec, ...] = Field(min_length=2, max_length=12)
    units: Literal["mm"] = "mm"

    @model_validator(mode="after")
    def validate_fixture(self) -> FixtureSpec:
        if len({item.occurrence_id for item in self.occurrences}) != len(
            self.occurrences
        ):
            raise ValueError("occurrence IDs must be unique")
        if len({item.realization_id for item in self.realizations}) != len(
            self.realizations
        ):
            raise ValueError("realization IDs must be unique")
        if self.realizations[0].realization_id != "reference":
            raise ValueError("the first realization must be the exact reference")
        if self.realizations[0].as_built:
            raise ValueError("the exact reference cannot include as-built deviations")
        reference = self.realizations[0]
        if (
            reference.bias_amplitude_mm != 0.0
            or reference.sensor_sigma_mm != 0.0
            or reference.occlusion_profile != "none"
            or reference.pose.rotation_xyz_degrees != (0.0, 0.0, 0.0)
            or reference.pose.translation_mm != (0.0, 0.0, 0.0)
        ):
            raise ValueError(
                "the exact reference requires zero corruption, full visibility, "
                + "and identity pose"
            )
        x, y, thickness = self.plate.size_mm
        if min(x, y, thickness) <= 0.0:
            raise ValueError("plate dimensions must be positive")
        return self


@dataclass(frozen=True)
class Patch:
    as_built_part: FloatArray
    faces: IntArray
    measured_part: FloatArray
    nominal_part: FloatArray
    normal_part: FloatArray
    normals_scan: FloatArray
    occurrence_codes: NDArray[np.int16]
    positions_scan: FloatArray
    role_codes: NDArray[np.uint8]
    surface_uv: NDArray[np.float32]


@dataclass(frozen=True)
class MeshData:
    as_built_part: FloatArray
    faces: IntArray
    measured_part: FloatArray
    nominal_part: FloatArray
    normal_part: FloatArray
    normals_scan: FloatArray
    occurrence_codes: NDArray[np.int16]
    positions_scan: FloatArray
    role_codes: NDArray[np.uint8]
    surface_uv: NDArray[np.float32]


class ArtifactRecord(TypedDict):
    bytes: int
    sha256: str


class RealizationSummary(TypedDict):
    outer_seed_count: int
    plane_seed_count: int
    realization_id: str
    source_sha256: str
    triangles: int
    vertices: int


class GenerationManifest(TypedDict):
    artifacts: dict[str, ArtifactRecord]
    definition_sha256: str
    format: str
    generator_revision: str
    realizations: list[RealizationSummary]
    status: str


def load_fixture(path: Path) -> FixtureSpec:
    return FixtureSpec.model_validate_json(path.read_bytes())


def _rotation_xyz(degrees: tuple[float, float, float]) -> FloatArray:
    x, y, z = np.radians(degrees)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = np.array(((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx)))
    ry = np.array(((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy)))
    rz = np.array(((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0)))
    return rz @ ry @ rx


def _clock_rotation(degrees: float) -> FloatArray:
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0)))


def _bounded_normal(seed: int, key: str) -> float:
    for attempt in range(128):
        digest = hashlib.sha256(
            f"{GENERATOR_REVISION}\0{seed}\0{key}\0{attempt}".encode("ascii")
        ).digest()
        first = (int.from_bytes(digest[:8], "big") + 0.5) / 2**64
        second = (int.from_bytes(digest[8:16], "big") + 0.5) / 2**64
        value = math.sqrt(-2.0 * math.log(first)) * math.cos(2.0 * math.pi * second)
        if abs(value) <= 4.0:
            return value
    raise RuntimeError("bounded deterministic normal sampler exhausted")


def sensor_offsets(
    realization: RealizationSpec,
    occurrence_id: str,
    role: str,
    uv: FloatArray,
) -> FloatArray:
    flat = uv.reshape(-1, 2)
    noise = np.array(
        [
            _bounded_normal(
                realization.seed,
                ":".join(
                    (
                        occurrence_id,
                        role,
                        float(coordinates[0]).hex(),
                        float(coordinates[1]).hex(),
                    )
                ),
            )
            for coordinates in flat
        ],
        dtype=np.float64,
    ).reshape(uv.shape[:2])
    phase = (realization.seed % 997) / 997.0
    bias = realization.bias_amplitude_mm * np.sin(
        2.0 * math.pi * (1.7 * uv[..., 0] + 0.8 * uv[..., 1] + phase)
    )
    return realization.sensor_sigma_mm * noise + bias


def _cell_mask(
    realization: RealizationSpec,
    occurrence_id: str,
    role: str,
    u_mid: FloatArray,
    v_mid: FloatArray,
) -> NDArray[np.bool_]:
    keep = np.ones(np.broadcast_shapes(u_mid.shape, v_mid.shape), dtype=np.bool_)
    if realization.occlusion_profile == "capture-a":
        if occurrence_id == "boss-b" and role == "outer":
            keep &= ~((u_mid > 0.47) & (u_mid < 0.60) & (v_mid > 0.28))
        if occurrence_id == "boss-c" and role == "shoulder":
            keep &= ~((u_mid > 0.02) & (u_mid < 0.18) & (v_mid > 0.45))
    elif realization.occlusion_profile == "capture-b":
        if occurrence_id == "boss-a" and role == "bore":
            keep &= ~((u_mid > 0.58) & (u_mid < 0.76) & (v_mid > 0.35))
        if occurrence_id == "boss-c" and role == "outer":
            keep &= ~((u_mid > 0.16) & (u_mid < 0.31) & (v_mid < 0.72))
        if occurrence_id == "boss-b" and role == "clock":
            keep &= ~((u_mid > 0.42) & (u_mid < 0.64) & (v_mid > 0.44))
    return keep


def _grid_faces(
    rows: int,
    columns: int,
    *,
    periodic_rows: bool,
    keep: NDArray[np.bool_],
) -> IntArray:
    row_cells = rows if periodic_rows else rows - 1
    if keep.shape != (row_cells, columns - 1):
        raise ValueError("cell mask shape does not match the surface grid")
    faces: list[tuple[int, int, int]] = []
    for row in range(row_cells):
        following = (row + 1) % rows
        for column in range(columns - 1):
            if not keep[row, column]:
                continue
            a = row * columns + column
            b = following * columns + column
            c = following * columns + column + 1
            d = row * columns + column + 1
            faces.extend(((a, b, c), (a, c, d)))
    return np.asarray(faces, dtype=np.int32)


def _make_patch(
    nominal_local: FloatArray,
    as_built_local: FloatArray,
    normal_local: FloatArray,
    uv: FloatArray,
    *,
    occurrence: OccurrenceSpec | None,
    occurrence_code: int,
    realization: RealizationSpec,
    role: str,
    periodic_rows: bool = False,
) -> Patch:
    rows, columns, components = nominal_local.shape
    if components != 3 or as_built_local.shape != nominal_local.shape:
        raise ValueError("surface position grids must be matching three-vectors")
    if normal_local.shape != nominal_local.shape or uv.shape != (rows, columns, 2):
        raise ValueError("surface normals or coordinates disagree with the grid")
    if occurrence is None:
        occurrence_id = "plate"
        occurrence_rotation = np.eye(3)
        occurrence_translation = np.zeros(3)
    else:
        occurrence_id = occurrence.occurrence_id
        occurrence_rotation = _clock_rotation(occurrence.clock_degrees)
        occurrence_translation = np.array((*occurrence.center_mm, 0.0))
    nominal_part = nominal_local @ occurrence_rotation.T + occurrence_translation
    as_built_part = as_built_local @ occurrence_rotation.T + occurrence_translation
    normal_part = normal_local @ occurrence_rotation.T
    offsets = sensor_offsets(realization, occurrence_id, role, uv)
    measured_part = as_built_part + offsets[..., None] * normal_part
    scan_rotation = _rotation_xyz(realization.pose.rotation_xyz_degrees)
    scan_translation = np.asarray(realization.pose.translation_mm)
    positions_scan = measured_part @ scan_rotation.T + scan_translation
    normals_scan = normal_part @ scan_rotation.T
    u = uv[:, 0, 0]
    if periodic_rows:
        u_following = np.roll(u, -1)
        u_mid = ((u + u_following) / 2.0)[:, None]
        u_mid[-1] = (u[-1] + 1.0) / 2.0
    else:
        u_mid = ((u[:-1] + u[1:]) / 2.0)[:, None]
    v = uv[0, :, 1]
    v_mid = ((v[:-1] + v[1:]) / 2.0)[None, :]
    keep = _cell_mask(realization, occurrence_id, role, u_mid, v_mid)
    faces = _grid_faces(rows, columns, periodic_rows=periodic_rows, keep=keep)
    count = rows * columns
    winding_positions = positions_scan.reshape(count, 3)
    flat_normals_scan = normals_scan.reshape(count, 3)
    face_cross = np.cross(
        winding_positions[faces[:, 1]] - winding_positions[faces[:, 0]],
        winding_positions[faces[:, 2]] - winding_positions[faces[:, 0]],
    )
    face_normals = np.sum(flat_normals_scan[faces], axis=1)
    alignment = np.sum(face_cross * face_normals, axis=1)
    if np.any(np.abs(alignment) <= 1e-12):
        raise ValueError(f"{occurrence_id} {role} produced degenerate face winding")
    reversed_faces = alignment < 0.0
    faces[reversed_faces] = faces[reversed_faces][:, (0, 2, 1)]
    return Patch(
        as_built_part=as_built_part.reshape(count, 3),
        faces=faces,
        measured_part=measured_part.reshape(count, 3),
        nominal_part=nominal_part.reshape(count, 3),
        normal_part=normal_part.reshape(count, 3),
        normals_scan=flat_normals_scan,
        occurrence_codes=np.full(count, occurrence_code, dtype=np.int16),
        positions_scan=positions_scan.reshape(count, 3),
        role_codes=np.full(count, ROLE_CODES[role], dtype=np.uint8),
        surface_uv=uv.reshape(count, 2).astype(np.float32),
    )


def _boss_patches(
    spec: FixtureSpec,
    realization: RealizationSpec,
    occurrence: OccurrenceSpec,
    occurrence_code: int,
) -> list[Patch]:
    boss = spec.boss
    tessellation = realization.tessellation
    imperfections = spec.imperfections
    alpha = math.acos(boss.flat_offset_mm / boss.outer_radius_mm)
    theta = np.linspace(
        alpha,
        2.0 * math.pi - alpha,
        tessellation.angular_segments + 1,
    )
    z = np.linspace(0.0, boss.height_mm, tessellation.axial_segments + 1)
    theta_grid, z_grid = np.meshgrid(theta, z, indexing="ij")
    outer_u = (theta_grid - alpha) / (2.0 * (math.pi - alpha))
    outer_v = z_grid / boss.height_mm
    uv = np.stack((outer_u, outer_v), axis=-1)
    phase = math.radians(37.0 * occurrence_code)
    radius_delta = np.zeros_like(theta_grid)
    if realization.as_built:
        radius_delta += imperfections.outer_ovality_mm * np.cos(
            2.0 * theta_grid + phase
        )
        radius_delta += imperfections.outer_taper_mm * (outer_v - 0.5)
        if occurrence.occurrence_id == "boss-c":
            angular_distance = np.arctan2(
                np.sin(theta_grid - math.radians(205.0)),
                np.cos(theta_grid - math.radians(205.0)),
            )
            radius_delta -= imperfections.outer_dent_depth_mm * np.exp(
                -((angular_distance / 0.20) ** 2 + ((outer_v - 0.58) / 0.18) ** 2)
            )
    nominal_radius = np.full_like(theta_grid, boss.outer_radius_mm)
    as_built_radius = nominal_radius + radius_delta
    outer_normal = np.stack(
        (np.cos(theta_grid), np.sin(theta_grid), np.zeros_like(theta_grid)), axis=-1
    )
    outer = _make_patch(
        np.stack(
            (
                nominal_radius * np.cos(theta_grid),
                nominal_radius * np.sin(theta_grid),
                z_grid,
            ),
            axis=-1,
        ),
        np.stack(
            (
                as_built_radius * np.cos(theta_grid),
                as_built_radius * np.sin(theta_grid),
                z_grid,
            ),
            axis=-1,
        ),
        outer_normal,
        uv,
        occurrence=occurrence,
        occurrence_code=occurrence_code,
        realization=realization,
        role="outer",
    )

    bore_theta = np.linspace(
        0.0, 2.0 * math.pi, tessellation.angular_segments, endpoint=False
    )
    bore_z = np.linspace(0.0, boss.height_mm, tessellation.axial_segments + 1)
    bore_theta_grid, bore_z_grid = np.meshgrid(bore_theta, bore_z, indexing="ij")
    bore_u = np.broadcast_to(
        np.arange(tessellation.angular_segments)[:, None]
        / tessellation.angular_segments,
        bore_theta_grid.shape,
    )
    bore_v = bore_z_grid / boss.height_mm
    bore_uv = np.stack((bore_u, bore_v), axis=-1)
    bore_delta = np.zeros_like(bore_theta_grid)
    if realization.as_built:
        bore_delta += imperfections.bore_ovality_mm * np.cos(
            2.0 * bore_theta_grid - 0.5 * phase
        )
    nominal_bore = np.full_like(bore_theta_grid, boss.bore_radius_mm)
    as_built_bore = nominal_bore + bore_delta
    bore_normal = np.stack(
        (
            -np.cos(bore_theta_grid),
            -np.sin(bore_theta_grid),
            np.zeros_like(bore_theta_grid),
        ),
        axis=-1,
    )
    bore = _make_patch(
        np.stack(
            (
                nominal_bore * np.cos(bore_theta_grid),
                nominal_bore * np.sin(bore_theta_grid),
                bore_z_grid,
            ),
            axis=-1,
        ),
        np.stack(
            (
                as_built_bore * np.cos(bore_theta_grid),
                as_built_bore * np.sin(bore_theta_grid),
                bore_z_grid,
            ),
            axis=-1,
        ),
        bore_normal,
        bore_uv,
        occurrence=occurrence,
        occurrence_code=occurrence_code,
        realization=realization,
        role="bore",
        periodic_rows=True,
    )

    shoulder_theta = bore_theta
    radial_fraction = np.linspace(0.0, 1.0, tessellation.radial_segments + 1)
    shoulder_theta_grid, radial_grid = np.meshgrid(
        shoulder_theta, radial_fraction, indexing="ij"
    )
    cosine = np.cos(shoulder_theta_grid)
    outer_limit = np.where(
        cosine > boss.flat_offset_mm / boss.outer_radius_mm,
        boss.flat_offset_mm / cosine,
        boss.outer_radius_mm,
    )
    shoulder_radius = boss.bore_radius_mm + radial_grid * (
        outer_limit - boss.bore_radius_mm
    )
    shoulder_z = np.full_like(shoulder_radius, boss.height_mm)
    if realization.as_built:
        shoulder_z += imperfections.shoulder_height_mm * (
            0.55 * math.sin(phase) + 0.45 * np.cos(shoulder_theta_grid + phase)
        )
    shoulder_uv = np.stack(
        (
            np.broadcast_to(
                np.arange(tessellation.angular_segments)[:, None]
                / tessellation.angular_segments,
                shoulder_theta_grid.shape,
            ),
            radial_grid,
        ),
        axis=-1,
    )
    shoulder_normal = np.zeros((*shoulder_radius.shape, 3))
    shoulder_normal[..., 2] = 1.0
    shoulder_nominal = np.stack(
        (
            shoulder_radius * np.cos(shoulder_theta_grid),
            shoulder_radius * np.sin(shoulder_theta_grid),
            np.full_like(shoulder_radius, boss.height_mm),
        ),
        axis=-1,
    )
    shoulder = _make_patch(
        shoulder_nominal,
        np.stack(
            (
                shoulder_nominal[..., 0],
                shoulder_nominal[..., 1],
                shoulder_z,
            ),
            axis=-1,
        ),
        shoulder_normal,
        shoulder_uv,
        occurrence=occurrence,
        occurrence_code=occurrence_code,
        realization=realization,
        role="shoulder",
        periodic_rows=True,
    )

    half_flat = math.sqrt(boss.outer_radius_mm**2 - boss.flat_offset_mm**2)
    flat_y = np.linspace(-half_flat, half_flat, tessellation.radial_segments * 2 + 1)
    flat_z = z
    flat_y_grid, flat_z_grid = np.meshgrid(flat_y, flat_z, indexing="ij")
    flat_u = (flat_y_grid + half_flat) / (2.0 * half_flat)
    flat_v = flat_z_grid / boss.height_mm
    flat_uv = np.stack((flat_u, flat_v), axis=-1)
    flat_nominal = np.stack(
        (
            np.full_like(flat_y_grid, boss.flat_offset_mm),
            flat_y_grid,
            flat_z_grid,
        ),
        axis=-1,
    )
    flat_as_built = flat_nominal.copy()
    if realization.as_built:
        flat_as_built[..., 0] += 0.35 * imperfections.outer_ovality_mm * math.cos(phase)
    flat_normal = np.zeros_like(flat_nominal)
    flat_normal[..., 0] = 1.0
    clock = _make_patch(
        flat_nominal,
        flat_as_built,
        flat_normal,
        flat_uv,
        occurrence=occurrence,
        occurrence_code=occurrence_code,
        realization=realization,
        role="clock",
    )
    return [outer, bore, shoulder, clock]


def _plate_patches(spec: FixtureSpec, realization: RealizationSpec) -> list[Patch]:
    size_x, size_y, thickness = spec.plate.size_mm
    tessellation = realization.tessellation

    def plane_patch(
        first: FloatArray,
        second: FloatArray,
        nominal: FloatArray,
        normal: tuple[float, float, float],
        *,
        bow: bool = False,
    ) -> Patch:
        first_grid, second_grid = np.meshgrid(first, second, indexing="ij")
        if nominal.shape != (3,):
            raise ValueError("plate plane origin must be a three-vector")
        axis_first = np.zeros(3)
        axis_second = np.zeros(3)
        zero_axes = np.flatnonzero(np.asarray(normal) != 0.0)
        fixed_axis = int(zero_axes[0])
        free_axes = [axis for axis in range(3) if axis != fixed_axis]
        axis_first[free_axes[0]] = 1.0
        axis_second[free_axes[1]] = 1.0
        points = (
            nominal
            + first_grid[..., None] * axis_first
            + second_grid[..., None] * axis_second
        )
        as_built = points.copy()
        if bow and realization.as_built:
            as_built[..., 2] += (
                spec.imperfections.plate_bow_mm
                * np.sin(math.pi * first_grid / size_x)
                * np.sin(math.pi * second_grid / size_y)
            )
        normals = np.broadcast_to(np.asarray(normal), points.shape).copy()
        u = (first_grid - first.min()) / (first.max() - first.min())
        v = (second_grid - second.min()) / (second.max() - second.min())
        return _make_patch(
            points,
            as_built,
            normals,
            np.stack((u, v), axis=-1),
            occurrence=None,
            occurrence_code=0,
            realization=realization,
            role="plate",
        )

    x = np.linspace(-size_x / 2.0, size_x / 2.0, tessellation.plate_x_segments + 1)
    y = np.linspace(-size_y / 2.0, size_y / 2.0, tessellation.plate_y_segments + 1)
    z = np.linspace(-thickness, 0.0, max(3, tessellation.axial_segments // 2) + 1)
    return [
        plane_patch(x, y, np.array((0.0, 0.0, 0.0)), (0.0, 0.0, 1.0), bow=True),
        plane_patch(x, y, np.array((0.0, 0.0, -thickness)), (0.0, 0.0, -1.0)),
        plane_patch(y, z, np.array((-size_x / 2.0, 0.0, 0.0)), (-1.0, 0.0, 0.0)),
        plane_patch(y, z, np.array((size_x / 2.0, 0.0, 0.0)), (1.0, 0.0, 0.0)),
        plane_patch(x, z, np.array((0.0, -size_y / 2.0, 0.0)), (0.0, -1.0, 0.0)),
        plane_patch(x, z, np.array((0.0, size_y / 2.0, 0.0)), (0.0, 1.0, 0.0)),
    ]


def _combine_patches(patches: list[Patch]) -> MeshData:
    offsets: list[int] = []
    total = 0
    for patch in patches:
        offsets.append(total)
        total += len(patch.positions_scan)
    faces = np.concatenate(
        [patch.faces + offset for patch, offset in zip(patches, offsets, strict=True)]
    )
    used = np.unique(faces)
    remap = np.full(total, -1, dtype=np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)

    def values(name: str) -> NDArray[np.generic]:
        return np.concatenate([getattr(patch, name) for patch in patches])[used]

    return MeshData(
        as_built_part=np.asarray(values("as_built_part"), dtype=np.float64),
        faces=remap[faces],
        measured_part=np.asarray(values("measured_part"), dtype=np.float64),
        nominal_part=np.asarray(values("nominal_part"), dtype=np.float64),
        normal_part=np.asarray(values("normal_part"), dtype=np.float64),
        normals_scan=np.asarray(values("normals_scan"), dtype=np.float64),
        occurrence_codes=np.asarray(values("occurrence_codes"), dtype=np.int16),
        positions_scan=np.asarray(values("positions_scan"), dtype=np.float64),
        role_codes=np.asarray(values("role_codes"), dtype=np.uint8),
        surface_uv=np.asarray(values("surface_uv"), dtype=np.float32),
    )


def generate_mesh(spec: FixtureSpec, realization: RealizationSpec) -> MeshData:
    patches = _plate_patches(spec, realization)
    for occurrence_code, occurrence in enumerate(spec.occurrences, start=1):
        patches.extend(_boss_patches(spec, realization, occurrence, occurrence_code))
    return _combine_patches(patches)


def _write_ply(path: Path, mesh: MeshData) -> None:
    header = mesh_header(
        len(mesh.positions_scan),
        len(mesh.faces),
        normals=True,
        comments=(GENERATOR_REVISION, "units millimetres"),
    )
    layout = mesh_layout(header)
    vertices = np.empty(len(mesh.positions_scan), dtype=layout.element("vertex").dtype)
    for index, name in enumerate(("x", "y", "z")):
        vertices[name] = mesh.positions_scan[:, index]
    for index, name in enumerate(("nx", "ny", "nz")):
        vertices[name] = mesh.normals_scan[:, index]
    faces = np.empty(len(mesh.faces), dtype=layout.element("face").dtype)
    faces["vertex_indices"]["count"] = 3
    faces["vertex_indices"]["values"] = mesh.faces
    with path.open("w+b") as stream:
        writer = Writer(
            stream, layout, max_range_bytes=max(vertices.nbytes, faces.nbytes)
        )
        writer.write_range("vertex", 0, vertices)
        writer.write_range("face", 0, faces)
        writer.finish()


def _region_ids(
    mesh: MeshData,
    occurrence_code: int,
    role: str,
) -> tuple[int, ...]:
    (u_min, u_max), (v_min, v_max) = REGION_BOUNDS[role]
    uv = mesh.surface_uv
    mask = (
        (mesh.occurrence_codes == occurrence_code)
        & (mesh.role_codes == ROLE_CODES[role])
        & (uv[:, 0] >= u_min)
        & (uv[:, 0] <= u_max)
        & (uv[:, 1] >= v_min)
        & (uv[:, 1] <= v_max)
    )
    return tuple(int(value) for value in np.flatnonzero(mask))


def _selection(
    selection_id: str,
    label: str,
    ids: tuple[int, ...],
) -> SelectionMembership:
    return SelectionMembership(
        depth_mode="first_surface",
        label=label,
        selection_id=selection_id,
        source_id="scan",
        vertex_count=len(ids),
        vertex_ids=ids,
        vertex_ids_sha256=vertex_ids_sha256(ids),
    )


def _write_bundle(
    path: Path,
    source_sha256: str,
    mesh: MeshData,
    spec: FixtureSpec,
    *,
    all_occurrences: bool,
) -> SelectionBundle:
    memberships: list[SelectionMembership] = []
    occurrences = (
        tuple(enumerate(spec.occurrences, start=1))
        if all_occurrences
        else ((1, spec.occurrences[0]),)
    )
    for occurrence_code, occurrence in occurrences:
        for role in REGION_BOUNDS:
            ids = _region_ids(mesh, occurrence_code, role)
            if len(ids) < 3:
                raise ValueError(
                    f"{occurrence.occurrence_id} {role} region has insufficient support"
                )
            memberships.append(
                _selection(
                    f"{occurrence.occurrence_id}-{role}",
                    f"{occurrence.occurrence_id} {role}",
                    ids,
                )
            )
    bundle = SelectionBundle(
        format=SELECTION_BUNDLE_FORMAT,
        format_status=SELECTION_BUNDLE_STATUS,
        selections=tuple(memberships),
        sources=(
            SelectionSource(
                label="Repeated boss scan",
                source_id="scan",
                source_sha256=source_sha256,
                vertex_count=len(mesh.positions_scan),
            ),
        ),
    )
    _ = path.write_bytes(canonical_json(bundle))
    return bundle


def _frame_for_occurrence(
    realization: RealizationSpec, occurrence: OccurrenceSpec
) -> tuple[FloatArray, FloatArray]:
    scan_rotation = _rotation_xyz(realization.pose.rotation_xyz_degrees)
    occurrence_rotation = _clock_rotation(occurrence.clock_degrees)
    center_part = np.array((*occurrence.center_mm, 0.0))
    origin_scan = scan_rotation @ center_part + np.asarray(
        realization.pose.translation_mm
    )
    return origin_scan, scan_rotation @ occurrence_rotation


def _azimuth_mask(local: FloatArray, bounds: tuple[float, float]) -> NDArray[np.bool_]:
    angle = np.mod(np.degrees(np.arctan2(local[:, 1], local[:, 0])), 360.0)
    return (angle >= bounds[0]) & (angle <= bounds[1])


def _write_legacy_selections(
    root: Path,
    source_sha256: str,
    mesh: MeshData,
    spec: FixtureSpec,
    realization: RealizationSpec,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    selections = root / "selections"
    selections.mkdir()
    occurrence = spec.occurrences[0]
    origin, frame = _frame_for_occurrence(realization, occurrence)
    local = (mesh.positions_scan - origin) @ frame
    axis = frame[:, 2]
    radial = local.copy()
    radial[:, 2] = 0.0
    rho = np.linalg.norm(radial, axis=1)
    radial_direction = np.divide(
        radial,
        rho[:, None],
        out=np.zeros_like(radial),
        where=rho[:, None] > 0.0,
    )
    normals_local = mesh.normals_scan @ frame
    outer_angles = (65.0, 295.0)
    outer_mask = (
        (local[:, 2] >= 2.5)
        & (local[:, 2] <= 11.5)
        & (rho >= spec.boss.outer_radius_mm - 0.7)
        & (rho <= spec.boss.outer_radius_mm + 0.7)
        & (np.sum(normals_local * radial_direction, axis=1) >= 0.82)
        & (np.abs(normals_local[:, 2]) <= 0.35)
        & _azimuth_mask(local, outer_angles)
    )
    plane_angles = (65.0, 295.0)
    plane_mask = (
        (local[:, 2] >= spec.boss.height_mm - 0.6)
        & (local[:, 2] <= spec.boss.height_mm + 0.6)
        & (rho >= spec.boss.bore_radius_mm + 0.7)
        & (rho <= spec.boss.flat_offset_mm - 0.4)
        & (normals_local[:, 2] >= 0.82)
        & _azimuth_mask(local, plane_angles)
    )
    outer_ids = tuple(int(value) for value in np.flatnonzero(outer_mask))
    plane_ids = tuple(int(value) for value in np.flatnonzero(plane_mask))
    if len(outer_ids) < 7 or len(plane_ids) < 3:
        raise ValueError("browser seed gates produced insufficient support")

    def ids_file(name: str, ids: tuple[int, ...]) -> tuple[str, str]:
        filename = f"{name}-vertex-ids.txt"
        data = b"".join(f"{value}\n".encode("ascii") for value in ids)
        _ = (selections / filename).write_bytes(data)
        return filename, sha256(data)

    outer_file, outer_sha = ids_file("outer-band", outer_ids)
    plane_file, plane_sha = ids_file("top-face", plane_ids)
    axis_seed = {"axis": axis.tolist(), "center": origin.tolist()}
    fit_frame = {"columns": frame.tolist(), "origin": origin.tolist()}
    outer = {
        "axis_seed": axis_seed,
        "axial_range": [2.5, 11.5],
        "azimuth_range_degrees": list(outer_angles),
        "fit_frame": fit_frame,
        "initial_parameters": [0.0, 0.0, 0.0, 0.0, spec.boss.outer_radius_mm],
        "maximum_abs_axial_normal_dot": 0.35,
        "minimum_outward_normal_dot": 0.82,
        "radius_range": [
            spec.boss.outer_radius_mm - 0.7,
            spec.boss.outer_radius_mm + 0.7,
        ],
        "selected_vertices": len(outer_ids),
        "source_sha256": source_sha256,
        "vertex_ids_file": outer_file,
        "vertex_ids_sha256": outer_sha,
    }
    plane = {
        "axis_seed": axis_seed,
        "axial_range": [
            spec.boss.height_mm - 0.6,
            spec.boss.height_mm + 0.6,
        ],
        "azimuth_range_degrees": list(plane_angles),
        "fit_frame": fit_frame,
        "minimum_axial_normal_dot": 0.82,
        "radius_range": [
            spec.boss.bore_radius_mm + 0.7,
            spec.boss.flat_offset_mm - 0.4,
        ],
        "selected_vertices": len(plane_ids),
        "source_sha256": source_sha256,
        "vertex_ids_file": plane_file,
        "vertex_ids_sha256": plane_sha,
    }
    _ = (selections / "outer-band.json").write_bytes(canonical_json(outer))
    _ = (selections / "top-face.json").write_bytes(canonical_json(plane))
    return outer_ids, plane_ids


def _write_truth(root: Path, mesh: MeshData, spec: FixtureSpec) -> None:
    truth = root / "truth"
    truth.mkdir()
    arrays = {
        "as-built-part.npy": mesh.as_built_part,
        "measured-part.npy": mesh.measured_part,
        "measured-scan.npy": mesh.positions_scan,
        "nominal-part.npy": mesh.nominal_part,
        "normal-part.npy": mesh.normal_part,
        "normal-scan.npy": mesh.normals_scan,
        "occurrence-code.npy": mesh.occurrence_codes,
        "role-code.npy": mesh.role_codes,
        "surface-uv.npy": mesh.surface_uv,
    }
    for name, values in arrays.items():
        with (truth / name).open("wb") as stream:
            np.save(stream, values, allow_pickle=False)
    _ = (truth / "codes.json").write_bytes(
        canonical_json(
            {
                "occurrence_codes": {
                    "0": "plate",
                    **{
                        str(code): occurrence.occurrence_id
                        for code, occurrence in enumerate(spec.occurrences, start=1)
                    },
                },
                "role_codes": {str(code): name for name, code in ROLE_CODES.items()},
            }
        )
    )


def _model_record(spec: FixtureSpec) -> dict[str, object]:
    return {
        "axial_domain": [-1.0, spec.boss.height_mm + 1.0],
        "constraint": "seed browser model only; compound occurrence semantics remain truth-sidecar evidence",
        "meaning": "padded solver support envelope in millimetres",
        "parameter_order": [
            "center_x",
            "center_y",
            "axis_slope_x",
            "axis_slope_y",
            "reference_radius",
            "plane_offset",
            "taper",
        ],
        "radius": "reference_radius + taper * axial_coordinate",
        "residual": "signed orthogonal distance on the bounded seed surface",
        "selection_policy": "generated source-bound seed memberships; no residual trimming",
        "status": "exploratory generated repeated-boss seed model",
        "weighting": "whole-mesh incident-area weights",
    }


def _write_realization(
    root: Path,
    spec: FixtureSpec,
    realization: RealizationSpec,
    definition_sha256: str,
) -> RealizationSummary:
    target = root / realization.realization_id
    target.mkdir()
    mesh = generate_mesh(spec, realization)
    source_name = f"{realization.realization_id}.ply"
    source_path = target / source_name
    _write_ply(source_path, mesh)
    source_bytes = source_path.read_bytes()
    source_sha = sha256(source_bytes)
    outer_ids, plane_ids = _write_legacy_selections(
        target, source_sha, mesh, spec, realization
    )
    _ = _write_bundle(
        target / "selections" / "user-selection-bundle.json",
        source_sha,
        mesh,
        spec,
        all_occurrences=False,
    )
    _ = _write_bundle(
        target / "selections" / "oracle-selection-bundle.json",
        source_sha,
        mesh,
        spec,
        all_occurrences=True,
    )
    models = target / "models"
    models.mkdir()
    _ = (models / "cone-plane.json").write_bytes(canonical_json(_model_record(spec)))
    _write_truth(target, mesh, spec)
    pose_rotation = _rotation_xyz(realization.pose.rotation_xyz_degrees)
    manifest = {
        "coordinate_note": "generated coordinates and normals are in millimetres",
        "definition_sha256": definition_sha256,
        "files": {
            source_name: {
                "bytes": len(source_bytes),
                "sha256": source_sha,
            }
        },
        "model": "models/cone-plane.json",
        "name": f"repeated-boss-selection-{realization.realization_id}",
        "plane_selection": "selections/top-face.json",
        "pose": {
            "part_to_scan_rotation": pose_rotation.tolist(),
            "part_to_scan_translation_mm": list(realization.pose.translation_mm),
        },
        "realization": realization.model_dump(mode="json"),
        "selection": "selections/outer-band.json",
        "selection_bundle": "selections/user-selection-bundle.json",
        "source_file": source_name,
        "status": "generated exploratory selection-transfer fixture",
        "triangles": len(mesh.faces),
        "units": "mm",
        "vertices": len(mesh.positions_scan),
    }
    _ = (target / "manifest.json").write_bytes(canonical_json(manifest))
    return {
        "outer_seed_count": len(outer_ids),
        "plane_seed_count": len(plane_ids),
        "realization_id": realization.realization_id,
        "source_sha256": source_sha,
        "triangles": len(mesh.faces),
        "vertices": len(mesh.positions_scan),
    }


def publish_fixture(output: Path, definition_path: Path) -> GenerationManifest:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    spec = load_fixture(definition_path)
    definition = canonical_json(spec)
    definition_sha = sha256(definition)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        _ = (temporary / "definition.json").write_bytes(definition)
        summaries = [
            _write_realization(temporary, spec, realization, definition_sha)
            for realization in spec.realizations
        ]
        artifacts: dict[str, ArtifactRecord] = {
            str(path.relative_to(temporary)): {
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()),
            }
            for path in sorted(temporary.rglob("*"))
            if path.is_file()
        }
        manifest: GenerationManifest = {
            "artifacts": artifacts,
            "definition_sha256": definition_sha,
            "format": "scansor-repeated-boss-selection-generation-v1",
            "generator_revision": GENERATOR_REVISION,
            "realizations": summaries,
            "status": "internal/provisional/generated/exploratory/non-public-contract",
        }
        _ = (temporary / "manifest.json").write_bytes(canonical_json(manifest))
        _ = temporary.rename(output)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--definition",
        type=Path,
        default=Path("examples/repeated-boss-selection/fixture.json"),
    )
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = publish_fixture(args.output, args.definition)
    print(canonical_json(manifest).decode("ascii"), end="")


if __name__ == "__main__":
    main()
