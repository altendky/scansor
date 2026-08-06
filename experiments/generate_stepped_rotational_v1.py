#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#     "numpy==2.3.1",
# ]
# ///
"""Generate and verify the experiment-local stepped-rotational-v1 fixture."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import platform
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

CONTRACT = "stepped-rotational-v1"
GENERATOR_VERSION = "1.0.5"
MM = 1e-3
RADII = (12 * MM, 18 * MM, 14 * MM)
STATIONS = (0.0, 20 * MM, 50 * MM, 80 * MM)
FLAT_X = 16 * MM
FLAT_Y = 0.008246211251235319
AXIAL_GUARD = 2 * MM
ANGULAR_GUARD = math.radians(5.0)
RADIAL_GUARD = 1 * MM
FLAT_GUARD = 1 * MM
POSE_ROTATION_VECTOR = np.array([0.08, -0.05, 0.12], dtype=np.float64)
POSE_TRANSLATION = np.array([4, -3, 6], dtype=np.float64) * MM
CAD_TOLERANCES = {
    "axis_direction_rad": 1e-9,
    "face_normal_rad": 1e-9,
    "linear_m": 1e-9,
    "radius_m": 1e-9,
    "station_m": 1e-9,
}
FROZEN_PLATFORM_INDEPENDENT_HASHES = {
    "contract": "2c77ce6c586a5f5ebc29f1dfe93f6f19264a8849a8dd62ccb22ef3b5338ca175",
    "records": "ee20662f27374cd9e80fc509acdce64cab613c4b97a9303678eb8a00d97300b6",
    "scenarios": "6ff6c7a5987d667563b4453d848c1ef2e4e29d763d9233924ff1d5d7edffa6c3",
}
FROZEN_REFERENCE_NUMERICAL_HASHES = {
    "dataset": "2b93c6da9a615618aae639faaea22991c0fe4bf7b1fb43294c172f9c62132b87",
    "observations": "06f2424839d049c4c2205394d1e3739b0ad2fd6402d9fa4c8f28009f88d244c9",
    "observations.npy": "2166b22397fc07ee3e87f39ba406db667a0a190a898f476f1efee34d733b41ef",
}
FROZEN_COUNTS = {
    "factors": 454,
    "held_out": 167,
    "memberships": 1888,
    "observations": 621,
    "train": 454,
}
EXPECTED_NOISE_COUNTS = {
    "asymmetric_datum_flat:cylinder.band-1": 56,
    "asymmetric_datum_flat:cylinder.band-2": 48,
    "asymmetric_datum_flat:cylinder.band-3": 56,
    "asymmetric_datum_flat:plane.datum-flat": 16,
    "asymmetric_datum_flat:plane.station-0": 14,
    "asymmetric_datum_flat:plane.station-20": 14,
    "asymmetric_datum_flat:plane.station-50": 12,
    "asymmetric_datum_flat:plane.station-80": 14,
    "axisymmetric:cylinder.band-1": 56,
    "axisymmetric:cylinder.band-2": 56,
    "axisymmetric:cylinder.band-3": 56,
    "axisymmetric:plane.station-0": 14,
    "axisymmetric:plane.station-20": 14,
    "axisymmetric:plane.station-50": 14,
    "axisymmetric:plane.station-80": 14,
}
REFERENCE_RUNTIME_ENVIRONMENT = {
    "architecture": {"machine": "x86_64", "python_architecture": "64bit"},
    "byte_order": "little",
    "libc": {"name": "glibc", "version": "2.43"},
    "numpy": {
        "float64_byte_order": "=",
        "float64_eps": 2.220446049250313e-16,
        "version": "2.3.1",
    },
    "operating_system": {
        "platform": "Linux-7.0.0-27-generic-x86_64-with-glibc2.43",
        "release": "7.0.0-27-generic",
        "system": "Linux",
        "version": "#27-Ubuntu SMP PREEMPT_DYNAMIC Thu Jun 18 19:13:49 UTC 2026",
    },
    "python": {
        "float_mantissa_bits": 53,
        "float_radix": 2,
        "float_repr_style": "short",
        "implementation": "CPython",
        "version": "3.12.12 (main, Feb 12 2026, 00:42:14) [Clang 21.1.4 ]",
        "version_info": [3, 12, 12, "final", 0],
    },
    "runtime_math": {
        "acos_16_over_18_rad": 0.47588224966041665,
        "sqrt_18_squared_minus_16_squared_m": 0.008246211251235326,
    },
}
ARRAY_COLUMNS = (
    "position_x_m",
    "position_y_m",
    "position_z_m",
    "normal_x",
    "normal_y",
    "normal_z",
)
ARTIFACT_NAMES = (
    "observations.npy",
    "records.json",
    "scenarios.json",
    "truth-manifest.json",
)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    variant: str
    element_id: str
    role: str
    memberships: tuple[str, ...]
    local_position: tuple[float, float, float]
    local_normal: tuple[float, float, float]
    coverage_cell: str


@dataclass(frozen=True)
class PreparedFixture:
    array: np.ndarray
    records: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    manifest: dict[str, Any]
    artifact_bytes: dict[str, bytes]
    passed_checks: tuple[str, ...]


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generator_source_sha256() -> str:
    return sha256(Path(__file__).resolve().read_bytes())


def frozen_generator_source_sha256() -> str:
    checksum_path = Path(__file__).with_suffix(".sha256")
    checksum, filename = checksum_path.read_text().strip().split(maxsplit=1)
    if filename != Path(__file__).name or len(checksum) != 64:
        raise ValueError(f"invalid generator checksum sidecar: {checksum_path}")
    return checksum


def verify_generator_source() -> str:
    actual = generator_source_sha256()
    frozen = frozen_generator_source_sha256()
    if actual != frozen:
        raise RuntimeError(
            f"generator source checksum mismatch: expected {frozen}, got {actual}"
        )
    return actual


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes, name: str, expected_type: type[Any]) -> Any:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_json_object,
            parse_constant=reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid {name}: {error}") from error
    if not isinstance(value, expected_type):
        raise TypeError(f"invalid {name}: expected {expected_type.__name__}")
    if canonical_json(value) != data:
        raise ValueError(f"invalid {name}: serialization is not canonical")
    return value


def reference_runtime_environment() -> dict[str, Any]:
    libc_name, libc_version = platform.libc_ver()
    return {
        "architecture": {
            "machine": platform.machine(),
            "python_architecture": platform.architecture()[0],
        },
        "byte_order": sys.byteorder,
        "libc": {"name": libc_name, "version": libc_version},
        "numpy": {
            "float64_byte_order": np.dtype(np.float64).byteorder,
            "float64_eps": np.finfo(np.float64).eps,
            "version": np.__version__,
        },
        "operating_system": {
            "platform": platform.platform(),
            "release": platform.release(),
            "system": platform.system(),
            "version": platform.version(),
        },
        "python": {
            "float_mantissa_bits": sys.float_info.mant_dig,
            "float_radix": sys.float_info.radix,
            "float_repr_style": sys.float_repr_style,
            "implementation": platform.python_implementation(),
            "version": sys.version,
            "version_info": list(sys.version_info),
        },
        "runtime_math": {
            "acos_16_over_18_rad": math.acos(16 / 18),
            "sqrt_18_squared_minus_16_squared_m": math.sqrt(RADII[1] ** 2 - FLAT_X**2),
        },
    }


def array_logical_hash(array: np.ndarray) -> str:
    canonical = np.ascontiguousarray(array, dtype="<f8")
    descriptor = canonical_json({"dtype": "float64-le", "shape": list(canonical.shape)})
    return sha256(descriptor + canonical.tobytes(order="C"))


def circular_angular_distance(left: float, right: float) -> float:
    difference = (left - right) % (2 * math.pi)
    return min(difference, 2 * math.pi - difference)


def rodrigues(rotation_vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(rotation_vector))
    if angle == 0.0:
        return np.eye(3, dtype=np.float64)
    axis = rotation_vector / angle
    skew = np.array(
        [[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]],
        dtype=np.float64,
    )
    return np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew)


def transform(points: np.ndarray, normals: np.ndarray) -> np.ndarray:
    rotation = rodrigues(POSE_ROTATION_VECTOR)
    return np.column_stack(
        ((rotation @ points.T).T + POSE_TRANSLATION, (rotation @ normals.T).T)
    )


def cylinder_angles(variant: str, radius: float, count: int) -> list[tuple[int, float]]:
    angles = np.linspace(0.0, 2 * math.pi, count, endpoint=False) + math.pi / count
    indexed_angles = list(enumerate(float(angle) for angle in angles))
    if variant == "asymmetric_datum_flat" and radius == RADII[1]:
        cut_half_angle = math.acos(FLAT_X / radius)
        indexed_angles = [
            (index, angle)
            for index, angle in indexed_angles
            if cut_half_angle + ANGULAR_GUARD
            <= angle
            <= 2 * math.pi - cut_half_angle - ANGULAR_GUARD
        ]
    return indexed_angles


def make_observations() -> list[Observation]:
    observations: list[Observation] = []
    for variant in ("axisymmetric", "asymmetric_datum_flat"):
        for band_index, (radius, z0, z1) in enumerate(
            zip(RADII, STATIONS[:-1], STATIONS[1:], strict=True)
        ):
            element = f"cylinder.band-{band_index + 1}"
            for z_index, z in enumerate(
                np.linspace(z0 + AXIAL_GUARD, z1 - AXIAL_GUARD, 5)
            ):
                for angle_index, angle in cylinder_angles(variant, radius, 16):
                    role = (
                        "held_out"
                        if (z_index == 2 or angle_index in (3, 4))
                        else "train"
                    )
                    obs_id = (
                        f"obs.{variant}.{element}.z{z_index:02d}.a{angle_index:02d}"
                    )
                    observations.append(
                        Observation(
                            obs_id,
                            variant,
                            element,
                            role,
                            (
                                f"element:{element}",
                                "surface:lateral",
                                f"band:{band_index + 1}",
                            ),
                            (
                                radius * math.cos(angle),
                                radius * math.sin(angle),
                                float(z),
                            ),
                            (math.cos(angle), math.sin(angle), 0.0),
                            f"z{z_index:02d}.a{angle_index // 4:02d}",
                        )
                    )

        plane_specs = (
            ("plane.station-0", STATIONS[0], 0.0, RADII[0] - RADIAL_GUARD, -1.0),
            (
                "plane.station-20",
                STATIONS[1],
                RADII[0] + RADIAL_GUARD,
                RADII[1] - RADIAL_GUARD,
                -1.0,
            ),
            (
                "plane.station-50",
                STATIONS[2],
                RADII[2] + RADIAL_GUARD,
                RADII[1] - RADIAL_GUARD,
                1.0,
            ),
            ("plane.station-80", STATIONS[3], 0.0, RADII[2] - RADIAL_GUARD, 1.0),
        )
        for element, z, radial_min, radial_max, normal_z in plane_specs:
            radii = (
                (radial_max * 0.45, radial_max * 0.75)
                if radial_min == 0
                else (
                    radial_min + (radial_max - radial_min) * 0.35,
                    radial_min + (radial_max - radial_min) * 0.7,
                )
            )
            for radial_index, radius in enumerate(radii):
                for angle_index, angle in enumerate(
                    np.linspace(0.0, 2 * math.pi, 8, endpoint=False) + math.pi / 8
                ):
                    if (
                        variant == "asymmetric_datum_flat"
                        and element in ("plane.station-20", "plane.station-50")
                        and radius * math.cos(angle) > FLAT_X - FLAT_GUARD
                    ):
                        continue
                    role = "held_out" if angle_index == 2 else "train"
                    obs_id = f"obs.{variant}.{element}.r{radial_index:02d}.a{angle_index:02d}"
                    observations.append(
                        Observation(
                            obs_id,
                            variant,
                            element,
                            role,
                            (
                                f"element:{element}",
                                "surface:axial",
                                "selection:all-planes",
                            ),
                            (radius * math.cos(angle), radius * math.sin(angle), z),
                            (0.0, 0.0, normal_z),
                            f"r{radial_index:02d}.a{angle_index // 2:02d}",
                        )
                    )

        if variant == "asymmetric_datum_flat":
            element = "plane.datum-flat"
            for z_index, z in enumerate(
                np.linspace(STATIONS[1] + FLAT_GUARD, STATIONS[2] - FLAT_GUARD, 5)
            ):
                for y_index, y in enumerate(
                    np.linspace(-FLAT_Y + FLAT_GUARD, FLAT_Y - FLAT_GUARD, 5)
                ):
                    role = "held_out" if z_index == 2 or y_index == 2 else "train"
                    observations.append(
                        Observation(
                            f"obs.{variant}.{element}.z{z_index:02d}.y{y_index:02d}",
                            variant,
                            element,
                            role,
                            (
                                f"element:{element}",
                                "surface:lateral",
                                "datum:+x",
                                "selection:middle-band",
                            ),
                            (FLAT_X, float(y), float(z)),
                            (1.0, 0.0, 0.0),
                            f"z{z_index:02d}.y{y_index:02d}",
                        )
                    )

    return sorted(observations, key=lambda observation: observation.observation_id)


def axial_domain(element_id: str) -> tuple[float, float, float, float]:
    return {
        "plane.station-0": (STATIONS[0], 0.0, RADII[0], -1.0),
        "plane.station-20": (STATIONS[1], RADII[0], RADII[1], -1.0),
        "plane.station-50": (STATIONS[2], RADII[2], RADII[1], 1.0),
        "plane.station-80": (STATIONS[3], 0.0, RADII[2], 1.0),
    }[element_id]


def in_support_domain(point: np.ndarray, element_id: str, variant: str) -> bool:
    if element_id.startswith("cylinder.band-"):
        band = int(element_id[-1]) - 1
        return STATIONS[band] + AXIAL_GUARD - 1e-14 <= point[2] <= STATIONS[
            band + 1
        ] - AXIAL_GUARD + 1e-14 and not (
            variant == "asymmetric_datum_flat"
            and band == 1
            and point[0] > FLAT_X - 1e-12
        )
    if element_id == "plane.datum-flat":
        return (
            -FLAT_Y + FLAT_GUARD - 1e-14 <= point[1] <= FLAT_Y - FLAT_GUARD + 1e-14
            and STATIONS[1] + FLAT_GUARD - 1e-14
            <= point[2]
            <= STATIONS[2] - FLAT_GUARD + 1e-14
        )
    _, inner_radius, outer_radius, _ = axial_domain(element_id)
    radius = math.hypot(point[0], point[1])
    guarded_inner = RADIAL_GUARD if inner_radius == 0 else inner_radius + RADIAL_GUARD
    inside = guarded_inner - 1e-14 <= radius <= outer_radius - RADIAL_GUARD + 1e-14
    if variant == "asymmetric_datum_flat" and element_id in (
        "plane.station-20",
        "plane.station-50",
    ):
        inside = inside and point[0] <= FLAT_X - FLAT_GUARD + 1e-14
    return inside


def support_residual(point: np.ndarray, element_id: str) -> float:
    if element_id.startswith("cylinder.band-"):
        radius = RADII[int(element_id[-1]) - 1]
        return float(math.hypot(point[0], point[1]) - radius)
    if element_id == "plane.datum-flat":
        return float(point[0] - FLAT_X)
    station, _, _, normal_z = axial_domain(element_id)
    return float(normal_z * (point[2] - station))


def build_dataset(
    observations: list[Observation],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    points = np.array(
        [observation.local_position for observation in observations], dtype=np.float64
    )
    normals = np.array(
        [observation.local_normal for observation in observations], dtype=np.float64
    )
    array = transform(points, normals)
    records: list[dict[str, Any]] = []
    mapping_ids: dict[tuple[str, str], str] = {}
    for row_index, observation in enumerate(observations):
        mapping_key = (observation.variant, observation.element_id)
        mapping_ids.setdefault(
            mapping_key, f"mapping.{observation.variant}.{observation.element_id}"
        )
        records.append(
            {
                "coverage_cell": observation.coverage_cell,
                "element_id": observation.element_id,
                "factor_id": None
                if observation.role == "held_out"
                else f"factor.{observation.observation_id}",
                "mapping_id": mapping_ids[mapping_key],
                "memberships": list(observation.memberships),
                "normal_noise_pairing_cell": (
                    f"{observation.variant}:{observation.element_id}:"
                    f"{observation.observation_id.rsplit('.', 1)[-1]}"
                    if observation.element_id.startswith("cylinder.band-")
                    else f"{observation.variant}:{observation.element_id}"
                ),
                "observation_id": observation.observation_id,
                "role": observation.role,
                "row_index": row_index,
                "variant": observation.variant,
            }
        )
    return array, records


def construct_factors(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (record["factor_id"], record["observation_id"])
        for record in records
        if record["role"] == "train" and record["factor_id"] is not None
    ]


def scenario_definitions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    train = [
        record["observation_id"] for record in records if record["role"] == "train"
    ]
    held_out = [
        record["observation_id"] for record in records if record["role"] == "held_out"
    ]
    asymmetric_flat = [
        record["observation_id"]
        for record in records
        if record["role"] == "train" and record["element_id"] == "plane.datum-flat"
    ]
    asymmetric = [
        record["observation_id"]
        for record in records
        if record["role"] == "train" and record["variant"] == "asymmetric_datum_flat"
    ]
    axisymmetric = [
        record["observation_id"]
        for record in records
        if record["role"] == "train" and record["variant"] == "axisymmetric"
    ]
    factor_by_observation = {
        record["observation_id"]: record["factor_id"]
        for record in records
        if record["factor_id"] is not None
    }

    def active(observation_ids: list[str]) -> list[str]:
        return [
            factor_by_observation[observation_id] for observation_id in observation_ids
        ]

    noise_groups: dict[str, list[str]] = {}
    for record in records:
        if record["role"] == "train":
            key = record["normal_noise_pairing_cell"]
            noise_groups.setdefault(key, []).append(record["observation_id"])
    paired_noise: dict[str, float] = {}
    for observation_ids in noise_groups.values():
        if len(observation_ids) % 2:
            raise ValueError("normal-noise pairing cell has an odd observation count")
        for index in range(0, len(observation_ids), 2):
            paired_noise[observation_ids[index]] = 2e-5
            paired_noise[observation_ids[index + 1]] = -2e-5
    return [
        {
            "id": "evaluator-oracle",
            "inputs": train,
            "active_factor_ids": active(train),
            "expected": "all raw residuals exactly zero before perturbation",
        },
        {
            "id": "noiseless-fixed-pose",
            "inputs": train,
            "active_factor_ids": active(train),
            "expected": "exact nominal geometry at fixed nonidentity pose",
        },
        {
            "id": "axisymmetric-free-roll",
            "inputs": axisymmetric,
            "active_factor_ids": active(axisymmetric),
            "expected_pose_rank": 5,
            "equivalence_class": "rotation about local +Z modulo 2*pi",
        },
        {
            "id": "asymmetric-full-pose",
            "inputs": asymmetric,
            "active_factor_ids": active(asymmetric),
            "expected_pose_rank": 6,
            "equivalence_class": "unique locally",
        },
        {
            "id": "flat-factor-ablation",
            "inputs": asymmetric,
            "active_factor_ids": active(
                [item for item in asymmetric if item not in asymmetric_flat]
            ),
            "expected_pose_rank": 5,
        },
        {
            "id": "coherent-held-out-strips-sectors",
            "inputs": train,
            "active_factor_ids": active(train),
            "evaluation_only": held_out,
        },
        {
            "id": "coverage-adequate",
            "inputs": train,
            "active_factor_ids": active(train),
            "expected": "all required elements and distributed cells",
        },
        {
            "id": "coverage-uneven",
            "inputs": list(dict.fromkeys(train[::3] + asymmetric_flat)),
            "active_factor_ids": active(
                list(dict.fromkeys(train[::3] + asymmetric_flat))
            ),
            "expected": "review-required uneven coverage",
        },
        {
            "id": "coverage-inadequate",
            "inputs": [item for item in train if ".cylinder.band-1." in item],
            "active_factor_ids": active(
                [item for item in train if ".cylinder.band-1." in item]
            ),
            "expected": "failed missing required elements",
        },
        {
            "id": "balanced-normal-noise",
            "inputs": train,
            "active_factor_ids": active(train),
            "normal_offsets_m": paired_noise,
            "normal_noise_pairing": {
                "cell_field": "normal_noise_pairing_cell",
                "invariant": "all observations in a cell have the same local normal",
                "ordering": "lexicographic observation_id",
                "pairing": "adjacent non-overlapping pairs",
                "unpaired_policy": "error",
            },
            "normal_noise_perturbed_counts": EXPECTED_NOISE_COUNTS,
            "expected": "paired deterministic signed offsets",
        },
        {
            "id": "fixed-outliers",
            "inputs": train,
            "active_factor_ids": active(train),
            "outlier_offsets_m": {
                train[7]: 0.002,
                train[31]: -0.0015,
                train[63]: 0.0025,
            },
        },
        {
            "id": "corrupted-mapping",
            "inputs": train,
            "active_factor_ids": active(train),
            "mapping_override": {train[11]: "mapping.axisymmetric.cylinder.band-3"},
        },
        {
            "id": "legal-active-bound",
            "inputs": train,
            "active_factor_ids": active(train),
            "parameter": "radius.band-1",
            "bound_m": 0.012,
            "truth_m": 0.012,
        },
        {
            "id": "invalid-geometry-declaration",
            "inputs": [],
            "active_factor_ids": [],
            "declaration": {"radius.band-2_m": -0.018},
            "expected": "reject before evaluation",
        },
        {
            "id": "out-of-contract-mismatch",
            "inputs": train,
            "active_factor_ids": active(train),
            "declaration": {
                "middle_cross_section": "ellipse",
                "semi_axes_m": [0.018, 0.017],
            },
            "expected": "model-mismatch-suspect",
        },
    ]


def geometry_contract() -> dict[str, Any]:
    return {
        "axis": {"direction": [0.0, 0.0, 1.0], "origin_m": [0.0, 0.0, 0.0]},
        "bounded_surfaces": [
            {
                "id": "cylinder.band-1",
                "kind": "cylinder",
                "radius_m": RADII[0],
                "z_domain_m": [STATIONS[0], STATIONS[1]],
            },
            {
                "id": "cylinder.band-2",
                "kind": "cylinder",
                "radius_m": RADII[1],
                "z_domain_m": [STATIONS[1], STATIONS[2]],
                "asymmetric_trim": "x <= 0.016 m; underlying support remains a cylinder",
            },
            {
                "id": "cylinder.band-3",
                "kind": "cylinder",
                "radius_m": RADII[2],
                "z_domain_m": [STATIONS[2], STATIONS[3]],
            },
            {
                "id": "plane.station-0",
                "kind": "axial_plane",
                "z_m": STATIONS[0],
                "normal": [0.0, 0.0, -1.0],
                "radial_domain_m": [0.0, RADII[0]],
            },
            {
                "id": "plane.station-20",
                "kind": "axial_plane",
                "z_m": STATIONS[1],
                "normal": [0.0, 0.0, -1.0],
                "radial_domain_m": [RADII[0], RADII[1]],
                "asymmetric_trim": "x <= 0.016 m",
            },
            {
                "id": "plane.station-50",
                "kind": "axial_plane",
                "z_m": STATIONS[2],
                "normal": [0.0, 0.0, 1.0],
                "radial_domain_m": [RADII[2], RADII[1]],
                "asymmetric_trim": "x <= 0.016 m",
            },
            {
                "id": "plane.station-80",
                "kind": "axial_plane",
                "z_m": STATIONS[3],
                "normal": [0.0, 0.0, 1.0],
                "radial_domain_m": [0.0, RADII[2]],
            },
            {
                "id": "plane.datum-flat",
                "kind": "datum_plane",
                "x_m": FLAT_X,
                "normal": [1.0, 0.0, 0.0],
                "z_domain_m": [STATIONS[1], STATIONS[2]],
                "y_domain_m": [-FLAT_Y, FLAT_Y],
                "variant_only": "asymmetric_datum_flat",
            },
        ],
        "dimensions_mm": {
            "radii": [12.0, 18.0, 14.0],
            "stations": [0.0, 20.0, 50.0, 80.0],
            "datum_flat_x": 16.0,
            "datum_flat_y_bounds": [-FLAT_Y / MM, FLAT_Y / MM],
            "datum_flat_y_description": "sqrt(middle_radius^2 - datum_flat_x^2)",
        },
        "parameters": {
            "fixed": ["units", "frame", "topology", "element_order", "mapping_policy"],
            "free": ["global_pose", "radii", "ordered_axial_stations", "datum_flat_x"],
            "shared": ["rotational_axis", "radii", "axial_stations"],
            "derived": ["band_lengths", "flat_y_bounds", "bounded_domains"],
            "admissible": {
                "radii_m": "all > 0",
                "stations_m": "strictly increasing",
                "datum_flat_x_m": "0 < x < middle radius",
                "topology": "all bounded surface interiors nonempty after guards",
            },
        },
        "hard_relationships": [
            "one shared coincident rotational axis",
            "adjacent bands share ordered axial stations",
            "station planes are incident with adjacent bands",
            "datum y bounds use the frozen decimal; the sqrt expression is descriptive within 1e-15 m",
        ],
        "frame": {
            "handedness": "right",
            "plane.datum-flat_defines": "+X",
            "rotational_axis": "+Z",
        },
        "units": {
            "angles": "radian",
            "internal_length": "metre",
            "readable_length": "millimetre",
        },
        "variants": ["axisymmetric", "asymmetric_datum_flat"],
    }


def build_manifest(
    array: np.ndarray,
    records: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    artifact_byte_hashes: dict[str, str],
) -> dict[str, Any]:
    records_hash = sha256(canonical_json(records))
    scenario_hash = sha256(canonical_json(scenarios))
    contract_core = {
        "contract": CONTRACT,
        "contract_status": "internal provisional experiment contract; not a public or durable schema",
        "generator_version": GENERATOR_VERSION,
        "geometry": geometry_contract(),
        "sampling": {
            "construction": "analytic deterministic grids; never CAD tessellation",
            "ordering": "lexicographic observation_id",
            "dtype": "float64",
            "columns": list(ARRAY_COLUMNS),
            "guards": {
                "axial_m": AXIAL_GUARD,
                "angular_rad": ANGULAR_GUARD,
                "radial_m": RADIAL_GUARD,
                "flat_m": FLAT_GUARD,
            },
            "held_out": "coherent center axial strips and fixed angular sectors; evaluation only",
        },
        "semantics": {
            "factor_activation": "each scenario explicitly lists active_factor_ids independently of available observations, mappings, and memberships",
            "factor_limit": "at most one primary geometric fit factor per training observation",
            "factor_residual": "raw signed point-to-oriented-analytic-support distance in metres",
            "memberships": "many-to-many labels only; never imply mappings or factors",
            "normal_noise_balance": "opposite signed offsets pair only within declared same-local-normal cells",
            "robustification": "not applied; raw residual, scale, weight, and future robustification remain distinct",
            "domain_failure": "out-of-domain analytic projection is rejected; never edge-clamped",
        },
        "pose_truth": {
            "direction": "model_to_observation",
            "rotation_vector_rad": POSE_ROTATION_VECTOR.tolist(),
            "translation_m": POSE_TRANSLATION.tolist(),
        },
        "expected": {
            "axisymmetric_pose_rank": 5,
            "asymmetric_pose_rank": 6,
            "flat_ablation_pose_rank": 5,
            "rank_tolerance": 1e-9,
            "axisymmetric_roll_equivalence": "R * RotZ(phi), phi in [0, 2*pi)",
        },
        "cad_comparison_tolerances_preregistered": CAD_TOLERANCES,
        "evidence_classes": [
            "generator_truth",
            "cad_nominal_geometry",
            "generated_train_observations",
            "generated_held_out_observations",
            "future_physical_evidence",
        ],
        "exclusions": [
            "assemblies",
            "blends",
            "captured_data",
            "cones",
            "inferred_correspondence",
            "native_mates",
            "physical_accuracy_claim",
        ],
    }
    contract_hash = sha256(canonical_json(contract_core))
    observation_hash = array_logical_hash(array)
    dataset_hash = sha256(
        canonical_json(
            {
                "array": observation_hash,
                "records": records_hash,
                "scenarios": scenario_hash,
            }
        )
    )
    platform_independent_hashes = {
        "contract": contract_hash,
        "records": records_hash,
        "scenarios": scenario_hash,
    }
    numerical_hashes = {
        "dataset": dataset_hash,
        "observations": observation_hash,
    }
    is_reference_environment = (
        reference_runtime_environment() == REFERENCE_RUNTIME_ENVIRONMENT
    )
    return {
        **contract_core,
        "provenance": {
            "generation_runtime_environment": reference_runtime_environment(),
            "generator_source_sha256": generator_source_sha256(),
            "is_reference_runtime_environment": is_reference_environment,
            "platform_independent_hash_match": (
                platform_independent_hashes == FROZEN_PLATFORM_INDEPENDENT_HASHES
            ),
            "reference_numerical_hash_match": (
                numerical_hashes
                == {
                    key: FROZEN_REFERENCE_NUMERICAL_HASHES[key]
                    for key in numerical_hashes
                }
                if is_reference_environment
                else None
            ),
            "reference_runtime_environment": REFERENCE_RUNTIME_ENVIRONMENT,
        },
        "artifacts": {
            "observations.npy": {
                "logical_sha256": observation_hash,
                "shape": list(array.shape),
            },
            "records.json": {"logical_sha256": records_hash, "count": len(records)},
            "scenarios.json": {
                "logical_sha256": scenario_hash,
                "count": len(scenarios),
            },
        },
        "logical_hashes": {
            "contract": contract_hash,
            "dataset": dataset_hash,
        },
        "frozen_platform_independent_hashes": FROZEN_PLATFORM_INDEPENDENT_HASHES,
        "reference_environment_expected_numerical_hashes": (
            FROZEN_REFERENCE_NUMERICAL_HASHES
        ),
        "generation_environment_artifact_byte_sha256": artifact_byte_hashes,
        "counts": {
            "factors": sum(record["factor_id"] is not None for record in records),
            "held_out": sum(record["role"] == "held_out" for record in records),
            "memberships": sum(len(record["memberships"]) for record in records),
            "observations": len(records),
            "train": sum(record["role"] == "train" for record in records),
        },
        "stable_order": [record["observation_id"] for record in records],
    }


def local_arrays(array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rotation = rodrigues(POSE_ROTATION_VECTOR)
    positions = (rotation.T @ (array[:, :3] - POSE_TRANSLATION).T).T
    normals = (rotation.T @ array[:, 3:].T).T
    return positions, normals


def pose_jacobian(
    positions: np.ndarray,
    normals: np.ndarray,
    records: list[dict[str, Any]],
    active_factor_ids: list[str],
) -> np.ndarray:
    index_by_factor_id = {
        record["factor_id"]: index
        for index, record in enumerate(records)
        if record["factor_id"] is not None
    }
    rows = []
    for factor_id in active_factor_ids:
        index = index_by_factor_id[factor_id]
        normal = normals[index]
        rows.append(np.concatenate((normal, np.cross(positions[index], normal))))
    return np.asarray(rows, dtype=np.float64)


def checks(
    array: np.ndarray,
    records: list[dict[str, Any]],
    scenarios_list: list[dict[str, Any]],
    manifest: dict[str, Any],
    expected_generation_runtime_environment: dict[str, Any],
) -> list[tuple[str, Callable[[], bool]]]:
    positions, normals = local_arrays(array)
    by_id = {
        record["observation_id"]: (index, record)
        for index, record in enumerate(records)
    }
    factors = construct_factors(records)
    scenarios = {scenario["id"]: scenario for scenario in scenarios_list}

    def bounded_membership() -> bool:
        return all(
            in_support_domain(positions[index], record["element_id"], record["variant"])
            for index, record in enumerate(records)
        )

    def exact_supports() -> bool:
        return all(
            abs(support_residual(positions[index], record["element_id"])) < 2e-15
            for index, record in enumerate(records)
        )

    def ablation() -> bool:
        scenario = scenarios["flat-factor-ablation"]
        factor_observations = dict(factors)
        return any(
            by_id[item][1]["element_id"] == "plane.datum-flat"
            for item in scenario["inputs"]
        ) and not any(
            by_id[factor_observations[item]][1]["element_id"] == "plane.datum-flat"
            for item in scenario["active_factor_ids"]
        )

    def held_out_leakage() -> bool:
        held_out = {
            record["observation_id"]
            for record in records
            if record["role"] == "held_out"
        }
        active_observation_ids = {
            observation_id
            for scenario in scenarios_list
            for factor_id in scenario["active_factor_ids"]
            for candidate_factor_id, observation_id in factors
            if factor_id == candidate_factor_id
        }
        return not held_out.intersection(
            observation_id for _, observation_id in factors
        ) and not held_out.intersection(active_observation_ids)

    def balanced_noise() -> bool:
        scenario = scenarios["balanced-normal-noise"]
        offsets = scenario["normal_offsets_m"]
        grouped_sums: dict[str, float] = {}
        grouped_counts: dict[str, int] = {}
        pairing_normals: dict[str, np.ndarray] = {}
        pairing_vector_sums: dict[str, np.ndarray] = {}
        for observation_id, offset in offsets.items():
            index, record = by_id[observation_id]
            key = f"{record['variant']}:{record['element_id']}"
            grouped_sums[key] = grouped_sums.get(key, 0.0) + offset
            grouped_counts[key] = grouped_counts.get(key, 0) + 1
            pairing_cell = record["normal_noise_pairing_cell"]
            if pairing_cell in pairing_normals and not np.array_equal(
                pairing_normals[pairing_cell], normals[index]
            ):
                return False
            pairing_normals.setdefault(pairing_cell, normals[index])
            pairing_vector_sums.setdefault(pairing_cell, np.zeros(3, dtype=np.float64))
            pairing_vector_sums[pairing_cell] += offset * normals[index]
        return (
            set(offsets) == set(scenario["inputs"])
            and grouped_counts == EXPECTED_NOISE_COUNTS
            and scenario["normal_noise_perturbed_counts"] == EXPECTED_NOISE_COUNTS
            and all(abs(value) < 1e-16 for value in grouped_sums.values())
            and all(
                np.linalg.norm(value) < 1e-16 for value in pairing_vector_sums.values()
            )
            and grouped_counts["asymmetric_datum_flat:plane.datum-flat"] == 16
        )

    def asymmetric_middle_cylinder_seam_guard() -> bool:
        intersection = math.acos(FLAT_X / RADII[1])
        seam_angles = (intersection, -intersection)
        guarded_angles = [
            math.atan2(positions[index, 1], positions[index, 0])
            for index, record in enumerate(records)
            if record["variant"] == "asymmetric_datum_flat"
            and record["element_id"] == "cylinder.band-2"
        ]
        return bool(guarded_angles) and all(
            circular_angular_distance(angle, seam_angle) >= ANGULAR_GUARD - 1e-14
            for angle in guarded_angles
            for seam_angle in seam_angles
        )

    def roll_invariance() -> bool:
        phi = 0.37
        rotation = np.array(
            [
                [math.cos(phi), -math.sin(phi), 0],
                [math.sin(phi), math.cos(phi), 0],
                [0, 0, 1],
            ],
            dtype=np.float64,
        )
        indices = [
            index
            for index, record in enumerate(records)
            if record["variant"] == "axisymmetric"
        ]
        return (
            max(
                abs(
                    support_residual(
                        rotation @ positions[index], records[index]["element_id"]
                    )
                )
                for index in indices
            )
            < 2e-15
        )

    def datum_leverage() -> bool:
        phi = 1e-4
        rotation = np.array(
            [
                [math.cos(phi), -math.sin(phi), 0],
                [math.sin(phi), math.cos(phi), 0],
                [0, 0, 1],
            ],
            dtype=np.float64,
        )
        residuals = [
            abs(support_residual(rotation @ positions[index], record["element_id"]))
            for index, record in enumerate(records)
            if record["element_id"] == "plane.datum-flat"
            and abs(positions[index, 1]) > 1e-6
        ]
        return max(residuals) > 1e-7

    def overlap_factor_independence() -> bool:
        augmented = [
            dict(record, memberships=record["memberships"] + ["diagnostic:overlap"])
            for record in records
        ]
        return construct_factors(augmented) == factors

    def oriented_residual_signs() -> bool:
        epsilon = 1e-6
        for index, record in enumerate(records):
            if record["element_id"].startswith("plane.station-"):
                perturbed = positions[index] + epsilon * normals[index]
                if (
                    abs(support_residual(perturbed, record["element_id"]) - epsilon)
                    > 1e-14
                ):
                    return False
        return True

    def expected_pose_ranks() -> bool:
        expected = {
            "axisymmetric-free-roll": 5,
            "asymmetric-full-pose": 6,
            "flat-factor-ablation": 5,
        }
        return all(
            np.linalg.matrix_rank(
                pose_jacobian(
                    positions,
                    normals,
                    records,
                    scenarios[scenario_id]["active_factor_ids"],
                ),
                tol=manifest["expected"]["rank_tolerance"],
            )
            == rank
            for scenario_id, rank in expected.items()
        )

    def valid_geometry_contract() -> bool:
        return (
            all(radius > 0 for radius in RADII)
            and all(left < right for left, right in pairwise(STATIONS))
            and 0 < FLAT_X < RADII[1]
            and FLAT_Y > FLAT_GUARD
            and all(
                (right - left) > 2 * AXIAL_GUARD for left, right in pairwise(STATIONS)
            )
            and (RADII[1] - RADII[0]) > 2 * RADIAL_GUARD
            and (RADII[1] - RADII[2]) > 2 * RADIAL_GUARD
        )

    def scenario_contract() -> bool:
        expected_ids = {
            "evaluator-oracle",
            "noiseless-fixed-pose",
            "axisymmetric-free-roll",
            "asymmetric-full-pose",
            "flat-factor-ablation",
            "coherent-held-out-strips-sectors",
            "coverage-adequate",
            "coverage-uneven",
            "coverage-inadequate",
            "balanced-normal-noise",
            "fixed-outliers",
            "corrupted-mapping",
            "legal-active-bound",
            "invalid-geometry-declaration",
            "out-of-contract-mismatch",
        }
        return set(scenarios) == expected_ids and all(
            len(scenario["inputs"]) == len(set(scenario["inputs"]))
            and len(scenario["active_factor_ids"])
            == len(set(scenario["active_factor_ids"]))
            for scenario in scenarios.values()
        )

    def row_array_correspondence() -> bool:
        observations = make_observations()
        expected_points = np.array(
            [observation.local_position for observation in observations],
            dtype=np.float64,
        )
        expected_normals = np.array(
            [observation.local_normal for observation in observations],
            dtype=np.float64,
        )
        return (
            len(array) == len(records) == len(observations)
            and [record["row_index"] for record in records] == list(range(len(records)))
            and [record["observation_id"] for record in records]
            == [observation.observation_id for observation in observations]
            == manifest["stable_order"]
            and np.array_equal(array, transform(expected_points, expected_normals))
        )

    def role_contract() -> bool:
        roles = {record["role"] for record in records}
        return roles == {"train", "held_out"}

    def held_out_rows_have_no_factors() -> bool:
        return all(
            record["factor_id"] is None
            for record in records
            if record["role"] == "held_out"
        )

    def factor_referential_integrity() -> bool:
        expected = [
            (f"factor.{record['observation_id']}", record["observation_id"])
            for record in records
            if record["role"] == "train"
        ]
        return factors == expected and len(factors) == manifest["counts"]["train"]

    def mapping_referential_integrity() -> bool:
        keys_by_mapping: dict[str, set[tuple[str, str]]] = {}
        for record in records:
            expected = f"mapping.{record['variant']}.{record['element_id']}"
            if record["mapping_id"] != expected:
                return False
            keys_by_mapping.setdefault(record["mapping_id"], set()).add(
                (record["variant"], record["element_id"])
            )
        return all(len(keys) == 1 for keys in keys_by_mapping.values())

    def membership_referential_integrity() -> bool:
        element_ids = {
            surface["id"] for surface in manifest["geometry"]["bounded_surfaces"]
        }
        for record in records:
            memberships = record["memberships"]
            if (
                not isinstance(memberships, list)
                or not memberships
                or not all(isinstance(item, str) and item for item in memberships)
                or len(memberships) != len(set(memberships))
                or f"element:{record['element_id']}" not in memberships
                or record["element_id"] not in element_ids
            ):
                return False
            for membership in memberships:
                if (
                    membership.startswith("element:")
                    and membership.removeprefix("element:") not in element_ids
                ):
                    return False
        return True

    def scenario_referential_integrity() -> bool:
        train_ids = {
            record["observation_id"] for record in records if record["role"] == "train"
        }
        held_out_ids = {
            record["observation_id"]
            for record in records
            if record["role"] == "held_out"
        }
        all_ids = train_ids | held_out_ids
        factor_to_observation = dict(factors)
        factor_ids = set(factor_to_observation)
        for scenario in scenarios.values():
            inputs = scenario["inputs"]
            if not set(inputs) <= train_ids:
                return False
            active_factor_ids = scenario["active_factor_ids"]
            if not set(active_factor_ids) <= factor_ids or not {
                factor_to_observation[factor_id] for factor_id in active_factor_ids
            } <= set(inputs):
                return False
            for field in (
                "evaluation_only",
                "retained_observations",
                "normal_offsets_m",
                "outlier_offsets_m",
                "mapping_override",
            ):
                references = scenario.get(field, {})
                referenced_ids = set(references)
                if not referenced_ids <= all_ids:
                    return False
                if field == "evaluation_only" and not referenced_ids <= held_out_ids:
                    return False
                if field != "evaluation_only" and not referenced_ids <= train_ids:
                    return False
        return True

    def scenario_semantics() -> bool:
        train_ids = {
            record["observation_id"] for record in records if record["role"] == "train"
        }
        held_out_ids = {
            record["observation_id"]
            for record in records
            if record["role"] == "held_out"
        }
        axisymmetric_ids = {
            record["observation_id"]
            for record in records
            if record["role"] == "train" and record["variant"] == "axisymmetric"
        }
        asymmetric_ids = train_ids - axisymmetric_ids
        ordered_train = sorted(train_ids)
        flat_ids = {
            record["observation_id"]
            for record in records
            if record["role"] == "train" and record["element_id"] == "plane.datum-flat"
        }
        mapping_ids = {record["mapping_id"] for record in records}
        factor_by_observation = {
            record["observation_id"]: record["factor_id"]
            for record in records
            if record["factor_id"] is not None
        }

        def active_for(observation_ids: list[str]) -> list[str]:
            return [
                factor_by_observation[observation_id]
                for observation_id in observation_ids
            ]

        noise = scenarios["balanced-normal-noise"]["normal_offsets_m"]
        expected_noise_groups: dict[str, list[str]] = {}
        for record in records:
            if record["role"] == "train":
                key = record["normal_noise_pairing_cell"]
                expected_noise_groups.setdefault(key, []).append(
                    record["observation_id"]
                )
        expected_noise: dict[str, float] = {}
        for observation_ids in expected_noise_groups.values():
            for index in range(0, len(observation_ids), 2):
                expected_noise[observation_ids[index]] = 2e-5
                expected_noise[observation_ids[index + 1]] = -2e-5
        outliers = scenarios["fixed-outliers"]["outlier_offsets_m"]
        override = scenarios["corrupted-mapping"]["mapping_override"]
        override_id, override_mapping = next(iter(override.items()))
        return (
            all(
                scenario["active_factor_ids"] == active_for(scenario["inputs"])
                for scenario_id, scenario in scenarios.items()
                if scenario_id != "flat-factor-ablation"
            )
            and scenarios["evaluator-oracle"]["inputs"] == ordered_train
            and scenarios["evaluator-oracle"]["expected"]
            == "all raw residuals exactly zero before perturbation"
            and scenarios["noiseless-fixed-pose"]["inputs"] == ordered_train
            and scenarios["noiseless-fixed-pose"]["expected"]
            == "exact nominal geometry at fixed nonidentity pose"
            and scenarios["axisymmetric-free-roll"]["inputs"]
            == sorted(axisymmetric_ids)
            and scenarios["axisymmetric-free-roll"]["expected_pose_rank"] == 5
            and scenarios["axisymmetric-free-roll"]["equivalence_class"]
            == "rotation about local +Z modulo 2*pi"
            and scenarios["asymmetric-full-pose"]["inputs"] == sorted(asymmetric_ids)
            and scenarios["asymmetric-full-pose"]["expected_pose_rank"] == 6
            and scenarios["asymmetric-full-pose"]["equivalence_class"]
            == "unique locally"
            and scenarios["flat-factor-ablation"]["inputs"] == sorted(asymmetric_ids)
            and scenarios["flat-factor-ablation"]["active_factor_ids"]
            == active_for(sorted(asymmetric_ids - flat_ids))
            and scenarios["flat-factor-ablation"]["expected_pose_rank"] == 5
            and scenarios["coherent-held-out-strips-sectors"]["inputs"] == ordered_train
            and set(scenarios["coherent-held-out-strips-sectors"]["evaluation_only"])
            == held_out_ids
            and scenarios["coverage-adequate"]["inputs"] == ordered_train
            and scenarios["coverage-adequate"]["expected"]
            == "all required elements and distributed cells"
            and scenarios["coverage-uneven"]["inputs"]
            == list(dict.fromkeys(ordered_train[::3] + sorted(flat_ids)))
            and scenarios["coverage-uneven"]["expected"]
            == "review-required uneven coverage"
            and scenarios["coverage-inadequate"]["inputs"]
            == [
                observation_id
                for observation_id in ordered_train
                if ".cylinder.band-1." in observation_id
            ]
            and scenarios["coverage-inadequate"]["expected"]
            == "failed missing required elements"
            and bool(noise)
            and scenarios["balanced-normal-noise"]["inputs"] == ordered_train
            and noise == expected_noise
            and set(noise) == train_ids
            and scenarios["balanced-normal-noise"]["normal_noise_pairing"]
            == {
                "cell_field": "normal_noise_pairing_cell",
                "invariant": "all observations in a cell have the same local normal",
                "ordering": "lexicographic observation_id",
                "pairing": "adjacent non-overlapping pairs",
                "unpaired_policy": "error",
            }
            and scenarios["balanced-normal-noise"]["normal_noise_perturbed_counts"]
            == EXPECTED_NOISE_COUNTS
            and set(noise) <= train_ids
            and all(np.isfinite(value) and value != 0 for value in noise.values())
            and scenarios["balanced-normal-noise"]["expected"]
            == "paired deterministic signed offsets"
            and scenarios["fixed-outliers"]["inputs"] == ordered_train
            and outliers
            == {
                ordered_train[7]: 0.002,
                ordered_train[31]: -0.0015,
                ordered_train[63]: 0.0025,
            }
            and all(np.isfinite(value) for value in outliers.values())
            and len(override) == 1
            and scenarios["corrupted-mapping"]["inputs"] == ordered_train
            and override == {ordered_train[11]: "mapping.axisymmetric.cylinder.band-3"}
            and override_id in train_ids
            and override_mapping in mapping_ids
            and override_mapping != by_id[override_id][1]["mapping_id"]
            and scenarios["legal-active-bound"]["parameter"] == "radius.band-1"
            and scenarios["legal-active-bound"]["inputs"] == ordered_train
            and scenarios["legal-active-bound"]["bound_m"]
            == scenarios["legal-active-bound"]["truth_m"]
            == RADII[0]
            and scenarios["invalid-geometry-declaration"]["inputs"] == []
            and scenarios["invalid-geometry-declaration"]["declaration"]
            == {"radius.band-2_m": -0.018}
            and scenarios["invalid-geometry-declaration"]["expected"]
            == "reject before evaluation"
            and scenarios["out-of-contract-mismatch"]["declaration"]
            == {
                "middle_cross_section": "ellipse",
                "semi_axes_m": [0.018, 0.017],
            }
            and scenarios["out-of-contract-mismatch"]["inputs"] == ordered_train
            and scenarios["out-of-contract-mismatch"]["expected"]
            == "model-mismatch-suspect"
        )

    def frozen_platform_independent_evidence() -> bool:
        actual_hashes = {
            "contract": manifest["logical_hashes"]["contract"],
            "records": manifest["artifacts"]["records.json"]["logical_sha256"],
            "scenarios": manifest["artifacts"]["scenarios.json"]["logical_sha256"],
        }
        return (
            generator_source_sha256() == frozen_generator_source_sha256()
            and manifest["provenance"]["generator_source_sha256"]
            == frozen_generator_source_sha256()
            and actual_hashes == FROZEN_PLATFORM_INDEPENDENT_HASHES
            and manifest["frozen_platform_independent_hashes"]
            == FROZEN_PLATFORM_INDEPENDENT_HASHES
            and manifest["counts"] == FROZEN_COUNTS
            and manifest["stable_order"]
            == [record["observation_id"] for record in records]
            and manifest["provenance"]["platform_independent_hash_match"]
        )

    def reference_numerical_evidence() -> bool:
        if not manifest["provenance"]["is_reference_runtime_environment"]:
            return manifest["provenance"]["reference_numerical_hash_match"] is None
        actual_hashes = {
            "dataset": manifest["logical_hashes"]["dataset"],
            "observations": manifest["artifacts"]["observations.npy"]["logical_sha256"],
            "observations.npy": manifest["generation_environment_artifact_byte_sha256"][
                "observations.npy"
            ],
        }
        return (
            actual_hashes == FROZEN_REFERENCE_NUMERICAL_HASHES
            and manifest["reference_environment_expected_numerical_hashes"]
            == FROZEN_REFERENCE_NUMERICAL_HASHES
            and manifest["provenance"]["reference_numerical_hash_match"]
        )

    return [
        ("contract identity", lambda: manifest["contract"] == CONTRACT),
        ("float64 array", lambda: array.dtype == np.float64),
        ("finite generated data", lambda: bool(np.isfinite(array).all())),
        (
            "stable IDs unique and sorted",
            lambda: manifest["stable_order"] == sorted(set(manifest["stable_order"])),
        ),
        ("exact row and array correspondence", row_array_correspondence),
        ("role vocabulary", role_contract),
        ("bounded surface membership", bounded_membership),
        (
            "unit oriented normals",
            lambda: bool(
                np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-14, rtol=0)
            ),
        ),
        ("exact radii and stations", exact_supports),
        ("oriented residual signs", oriented_residual_signs),
        (
            "datum plane x and y bounds",
            lambda: (
                FLAT_X == 0.016 and abs(FLAT_Y - math.sqrt(0.018**2 - 0.016**2)) < 1e-15
            ),
        ),
        (
            "no plane observations axisymmetric",
            lambda: (
                not any(
                    record["variant"] == "axisymmetric"
                    and record["element_id"] == "plane.datum-flat"
                    for record in records
                )
            ),
        ),
        ("flat ablation removes factors", ablation),
        ("held-out rows carry no factors", held_out_rows_have_no_factors),
        ("held-out leakage absent", held_out_leakage),
        ("factor referential integrity", factor_referential_integrity),
        ("mapping referential integrity", mapping_referential_integrity),
        ("membership referential integrity", membership_referential_integrity),
        ("scenario referential integrity", scenario_referential_integrity),
        ("scenario-specific semantics", scenario_semantics),
        ("overlap does not change factors", overlap_factor_independence),
        (
            "factor IDs deterministic and unique",
            lambda: len({factor_id for factor_id, _ in factors}) == len(factors),
        ),
        (
            "one factor maximum",
            lambda: (
                len({observation_id for _, observation_id in factors}) == len(factors)
            ),
        ),
        ("edge guards", bounded_membership),
        (
            "asymmetric middle-cylinder angular seam guard",
            asymmetric_middle_cylinder_seam_guard,
        ),
        ("axisymmetric axial-roll invariance", roll_invariance),
        ("nonzero datum roll leverage", datum_leverage),
        ("expected pose ranks", expected_pose_ranks),
        ("valid geometry and topology", valid_geometry_contract),
        (
            "nonidentity observable pose truth",
            lambda: (
                np.linalg.norm(POSE_ROTATION_VECTOR) > 0
                and np.linalg.norm(POSE_TRANSLATION) > 0
            ),
        ),
        ("balanced deterministic noise", balanced_noise),
        (
            "intentional invalid isolated",
            lambda: (
                scenarios["invalid-geometry-declaration"]["inputs"] == []
                and scenarios["invalid-geometry-declaration"]["declaration"][
                    "radius.band-2_m"
                ]
                < 0
            ),
        ),
        ("scenario corpus complete and unique", scenario_contract),
        (
            "logical hashes self-consistent",
            lambda: (
                manifest["artifacts"]["observations.npy"]["logical_sha256"]
                == array_logical_hash(array)
            ),
        ),
        (
            "source and runtime provenance",
            lambda: (
                manifest["provenance"]["generation_runtime_environment"]
                == expected_generation_runtime_environment
                and manifest["provenance"]["reference_runtime_environment"]
                == REFERENCE_RUNTIME_ENVIRONMENT
                and manifest["provenance"]["is_reference_runtime_environment"]
                == (
                    expected_generation_runtime_environment
                    == REFERENCE_RUNTIME_ENVIRONMENT
                )
            ),
        ),
        (
            "frozen platform-independent source, hashes, counts, and IDs",
            frozen_platform_independent_evidence,
        ),
        (
            "reference numerical hashes",
            reference_numerical_evidence,
        ),
    ]


def evaluate_checks(
    array: np.ndarray,
    records: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    manifest: dict[str, Any],
    expected_generation_runtime_environment: dict[str, Any],
) -> tuple[str, ...]:
    evaluated = checks(
        array,
        records,
        scenarios,
        manifest,
        expected_generation_runtime_environment,
    )
    failures: list[str] = []
    for name, check in evaluated:
        try:
            passed = check()
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                f"self-check {name!r} could not be evaluated: {error}"
            ) from error
        if not passed:
            failures.append(name)
    if failures:
        raise RuntimeError(f"self-check failures: {', '.join(failures)}")
    return tuple(name for name, _ in evaluated)


def serialize_array(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def prepare_fixture() -> PreparedFixture:
    verify_generator_source()
    observations = make_observations()
    array, records = build_dataset(observations)
    scenarios = scenario_definitions(records)
    artifact_bytes = {
        "observations.npy": serialize_array(array),
        "records.json": canonical_json(records),
        "scenarios.json": canonical_json(scenarios),
    }
    byte_hashes = {name: sha256(data) for name, data in artifact_bytes.items()}
    manifest = build_manifest(array, records, scenarios, byte_hashes)
    artifact_bytes["truth-manifest.json"] = canonical_json(manifest)
    passed = evaluate_checks(
        array, records, scenarios, manifest, reference_runtime_environment()
    )
    return PreparedFixture(array, records, scenarios, manifest, artifact_bytes, passed)


def generate_fixture(output: Path, *, replace: bool = False) -> PreparedFixture:
    prepared = prepare_fixture()
    if output.exists():
        if not replace:
            raise FileExistsError(f"output already exists: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=False)
    for name in ARTIFACT_NAMES:
        (output / name).write_bytes(prepared.artifact_bytes[name])
    return prepared


def validate_runtime_environment(environment: Any) -> None:
    if not isinstance(environment, dict):
        raise TypeError("generation runtime environment must be an object")
    if set(environment) != set(REFERENCE_RUNTIME_ENVIRONMENT):
        raise ValueError("generation runtime environment fields are incomplete")
    try:
        architecture = environment["architecture"]
        libc = environment["libc"]
        numpy_environment = environment["numpy"]
        operating_system = environment["operating_system"]
        python_environment = environment["python"]
        runtime_math = environment["runtime_math"]
        version_info = python_environment["version_info"]
        valid = (
            set(architecture) == {"machine", "python_architecture"}
            and all(isinstance(value, str) and value for value in architecture.values())
            and environment["byte_order"] in {"little", "big"}
            and set(libc) == {"name", "version"}
            and all(isinstance(value, str) for value in libc.values())
            and set(numpy_environment)
            == {"float64_byte_order", "float64_eps", "version"}
            and numpy_environment["float64_byte_order"] in {"=", "<", ">"}
            and numpy_environment["float64_eps"] == np.finfo(np.float64).eps
            and numpy_environment["version"] == "2.3.1"
            and set(operating_system) == {"platform", "release", "system", "version"}
            and all(isinstance(value, str) for value in operating_system.values())
            and set(python_environment)
            == {
                "float_mantissa_bits",
                "float_radix",
                "float_repr_style",
                "implementation",
                "version",
                "version_info",
            }
            and python_environment["float_mantissa_bits"] == 53
            and python_environment["float_radix"] == 2
            and python_environment["float_repr_style"] == "short"
            and python_environment["implementation"] == "CPython"
            and isinstance(python_environment["version"], str)
            and isinstance(version_info, list)
            and len(version_info) == 5
            and version_info[0] == 3
            and version_info[1] in {12, 13}
            and set(runtime_math)
            == {
                "acos_16_over_18_rad",
                "sqrt_18_squared_minus_16_squared_m",
            }
            and abs(runtime_math["acos_16_over_18_rad"] - math.acos(16 / 18)) < 1e-15
            and abs(runtime_math["sqrt_18_squared_minus_16_squared_m"] - FLAT_Y) < 1e-15
        )
    except (KeyError, TypeError) as error:
        raise ValueError(f"invalid generation runtime environment: {error}") from error
    if not valid:
        raise ValueError("invalid generation runtime environment values")


def load_fixture(directory: Path) -> PreparedFixture:
    if not directory.is_dir():
        raise ValueError(f"fixture is not a directory: {directory}")
    entries = {entry.name for entry in directory.iterdir()}
    if entries != set(ARTIFACT_NAMES):
        missing = sorted(set(ARTIFACT_NAMES) - entries)
        unexpected = sorted(entries - set(ARTIFACT_NAMES))
        raise ValueError(
            f"fixture must contain exactly four artifacts; missing={missing}, unexpected={unexpected}"
        )
    paths = {name: directory / name for name in ARTIFACT_NAMES}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ValueError("fixture artifacts must be regular, non-symlink files")
    artifact_bytes = {name: path.read_bytes() for name, path in paths.items()}
    try:
        loaded_array = np.load(
            io.BytesIO(artifact_bytes["observations.npy"]), allow_pickle=False
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid observations.npy: {error}") from error
    if not isinstance(loaded_array, np.ndarray):
        raise TypeError("invalid observations.npy: expected one ndarray")
    array = loaded_array
    if serialize_array(array) != artifact_bytes["observations.npy"]:
        raise ValueError("invalid observations.npy: serialization is not canonical")
    records = parse_json(artifact_bytes["records.json"], "records.json", list)
    scenarios = parse_json(artifact_bytes["scenarios.json"], "scenarios.json", list)
    manifest = parse_json(
        artifact_bytes["truth-manifest.json"], "truth-manifest.json", dict
    )
    try:
        environment = manifest["provenance"]["generation_runtime_environment"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"invalid truth-manifest.json provenance: {error}") from error
    validate_runtime_environment(environment)
    current_environment = reference_runtime_environment()
    if environment != current_environment:
        raise ValueError(
            "fixture provenance does not match the current validation runtime"
        )
    byte_hashes = {name: sha256(artifact_bytes[name]) for name in ARTIFACT_NAMES[:-1]}
    expected_manifest = build_manifest(array, records, scenarios, byte_hashes)
    if manifest != expected_manifest:
        raise ValueError(
            "truth-manifest.json does not match recomputed fixture evidence"
        )
    passed = evaluate_checks(array, records, scenarios, manifest, current_environment)
    return PreparedFixture(array, records, scenarios, manifest, artifact_bytes, passed)


def compare(left: Path, right: Path) -> None:
    verify_generator_source()
    left_fixture = load_fixture(left)
    right_fixture = load_fixture(right)
    differences = [
        name
        for name in ARTIFACT_NAMES
        if left_fixture.artifact_bytes[name] != right_fixture.artifact_bytes[name]
    ]
    if differences:
        raise RuntimeError(f"repeatability mismatch: {', '.join(differences)}")
    if (
        left_fixture.manifest["logical_hashes"]
        != right_fixture.manifest["logical_hashes"]
    ):
        raise RuntimeError("logical hash mismatch")
    print(
        f"repeatability: PASS ({len(ARTIFACT_NAMES)} independently validated, byte-identical artifacts; logical hashes equal)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new output directory")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("LEFT", "RIGHT"))
    parser.add_argument(
        "--replace", action="store_true", help="replace an existing output directory"
    )
    args = parser.parse_args()
    verify_generator_source()
    if args.compare:
        compare(*args.compare)
        return 0
    if args.output is None:
        parser.error("--output or --compare is required")
    prepared = generate_fixture(args.output, replace=args.replace)
    manifest = prepared.manifest
    print(f"self-checks: PASS ({len(prepared.passed_checks)})")
    print(
        f"observations: {manifest['counts']['observations']} ({manifest['counts']['train']} train, {manifest['counts']['held_out']} held-out)"
    )
    print(
        f"factors: {manifest['counts']['factors']}; scenarios: {manifest['artifacts']['scenarios.json']['count']}"
    )
    print(f"contract logical sha256: {manifest['logical_hashes']['contract']}")
    print(f"dataset logical sha256: {manifest['logical_hashes']['dataset']}")
    if manifest["provenance"]["is_reference_runtime_environment"]:
        print("reference numerical hashes: MATCHED")
    else:
        print("reference numerical hashes: NOT APPLICABLE (non-reference runtime)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
