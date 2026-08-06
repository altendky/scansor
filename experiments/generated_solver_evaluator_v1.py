#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.12.12"
# dependencies = [
#     "numpy==2.3.1",
#     "scipy==1.16.1",
# ]
# ///
"""Run and verify the experiment-local stepped-rotational-v1 solver gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Never

import numpy as np
import scipy
from scipy.optimize import least_squares

FORMAT = "scansor-generated-solver-evaluator-v1-experiment-local"
FORMAT_STATUS = "provisional/experiment-local/non-public-contract"
GENERATOR_SHA256 = "995a067d7f4bd247defd092a7e8501224ce7393e9e9e09dc47726f13b610a1e8"
CONTRACT_SHA256 = "2c77ce6c586a5f5ebc29f1dfe93f6f19264a8849a8dd62ccb22ef3b5338ca175"
RAW_RANK_THRESHOLD = 1e-9
DIMENSIONLESS_RANK_THRESHOLD = 1e-9
RESIDUAL_SCALE_M = 1e-3
POSE_PARAMETER_SCALES = np.array([1e-3, 1e-3, 1e-3, 1.0, 1.0, 1.0])
SHAPE_PARAMETER_SCALES = np.full(7, 1e-3)
SHAPE_NAMES = (
    "radius.band-1_m",
    "radius.band-2_m",
    "radius.band-3_m",
    "station-20_m",
    "station-50_m",
    "station-80_m",
    "datum-flat-x_m",
)
SHAPE_TRUTH = np.array([0.012, 0.018, 0.014, 0.020, 0.050, 0.080, 0.016])
SHAPE_LOWER = np.array([0.010, 0.017, 0.012, 0.018, 0.047, 0.077, 0.015])
SHAPE_UPPER = np.array([0.0145, 0.020, 0.0145, 0.022, 0.053, 0.083, 0.0165])
SHAPE_INITIAL = np.array([0.0112, 0.0190, 0.0132, 0.0192, 0.0510, 0.0790, 0.0164])
POSE_INITIAL = np.array([4e-4, -3e-4, 2e-4, 0.012, -0.009, 0.025])
POSE_LOWER = np.array([-0.003, -0.003, -0.003, -0.08, -0.08, -0.08])
POSE_UPPER = -POSE_LOWER
REFERENCE_PYTHON = (3, 12, 12)
REFERENCE_NUMPY = "2.3.1"
REFERENCE_SCIPY = "1.16.1"
DERIVATIVE_STEP = 3e-6
DERIVATIVE_ABS_TOL = 3e-9
DERIVATIVE_DIMENSIONLESS_ABS_TOL = 3e-6
DERIVATIVE_REL_TOL = 3e-6
DERIVATIVE_REL_FLOOR = 1e-6
RECOVERY_TOLERANCE = 2e-9
ORACLE_TOLERANCE = 2e-15
ACTIVE_BOUND_TOLERANCE_M = 1e-9
MISMATCH_ANALYTIC_TOLERANCE_M = 2e-9
MISMATCH_NONZERO_TOLERANCE_M = 2e-15
DISPOSITION_PASSED = "passed"
DISPOSITION_REVIEW_REQUIRED = "review-required"
DISPOSITION_FAILED = "failed"
DISPOSITION_UNCLASSIFIED = "unclassified"


class GateError(ValueError):
    """The frozen experiment contract or an evaluator precondition failed."""


@dataclass(frozen=True)
class Observation:
    observation_id: str
    point: np.ndarray
    normal: np.ndarray
    role: str
    memberships: tuple[str, ...]
    row_index: int


@dataclass(frozen=True)
class Mapping:
    mapping_id: str
    variant: str
    element_id: str


@dataclass(frozen=True)
class Factor:
    factor_id: str
    observation_id: str
    mapping_id: str
    variant: str
    element_id: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    input_observation_ids: tuple[str, ...]
    active_factor_ids: tuple[str, ...]
    evaluation_only_ids: tuple[str, ...]
    declaration: dict[str, Any]


@dataclass
class ContractData:
    manifest: dict[str, Any]
    observations: dict[str, Observation]
    mappings: dict[str, Mapping]
    factors: dict[str, Factor]
    scenarios: dict[str, Scenario]
    records: dict[str, dict[str, Any]]


@dataclass
class CallbackInvocation:
    path: str
    coordinates: np.ndarray
    factor_ids: list[str]
    row_count: int | None = None


def fail(message: str) -> Never:
    raise GateError(message)


def verify_runtime(
    version_info: tuple[int, int, int] | None = None,
    implementation: str | None = None,
    numpy_version: str | None = None,
    scipy_version: str | None = None,
) -> str:
    actual = tuple(sys.version_info[:3]) if version_info is None else version_info
    actual_implementation = (
        sys.implementation.name if implementation is None else implementation
    )
    actual_numpy = np.__version__ if numpy_version is None else numpy_version
    actual_scipy = scipy.__version__ if scipy_version is None else scipy_version
    if (
        actual != REFERENCE_PYTHON
        or actual_implementation != "cpython"
        or actual_numpy != REFERENCE_NUMPY
        or actual_scipy != REFERENCE_SCIPY
    ):
        fail(
            "solver evidence requires exact runtime "
            f"CPython {'.'.join(map(str, REFERENCE_PYTHON))}, "
            f"NumPy {REFERENCE_NUMPY}, SciPy {REFERENCE_SCIPY}; got "
            f"{actual_implementation} {'.'.join(map(str, actual))}, "
            f"NumPy {actual_numpy}, SciPy {actual_scipy}"
        )
    return ".".join(map(str, actual))


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def coverage_acceptance_policy(diagnostics: dict[str, Any]) -> str:
    if diagnostics["missing_required_mappings"]:
        return DISPOSITION_FAILED
    if diagnostics["missing_coverage_cells"]:
        return DISPOSITION_REVIEW_REQUIRED
    return DISPOSITION_PASSED


def corrupted_mapping_acceptance_policy(result: dict[str, Any]) -> str:
    diagnostics = result["diagnostics"]
    rejected = (
        diagnostics["classification"] == "mapping-suspect"
        and diagnostics["retarget_attempt_count"] == 0
        and len(diagnostics["support_attempt_mapping_ids"]) == 1
        and result["support"]["factor_traversal_count"] == 1
        and len(result["support"]["failures"]) == 1
        and result["raw"]["residual_traversal_count"] == 0
        and result["raw"]["jacobian_traversal_count"] == 0
        and result["callbacks"]["callback_calls"] == 0
        and not result["solver"]["invoked"]
        and result["termination"]["class"] == "rejected-before-solve"
    )
    return DISPOSITION_FAILED if rejected else DISPOSITION_UNCLASSIFIED


def active_bound_acceptance_policy(result: dict[str, Any]) -> str:
    diagnostics = result["diagnostics"]
    lower_distance = diagnostics["distance_to_lower_bounds_m"][0]
    all_distances = (
        diagnostics["distance_to_lower_bounds_m"]
        + diagnostics["distance_to_upper_bounds_m"]
    )
    foundationally_valid = (
        result["termination"]["success"]
        and diagnostics["geometry_valid"]
        and diagnostics["initialization_valid"]
        and not result["support"]["failures"]
        and len(diagnostics["distance_to_lower_bounds_m"]) == len(SHAPE_NAMES)
        and len(diagnostics["distance_to_upper_bounds_m"]) == len(SHAPE_NAMES)
        and all(math.isfinite(value) and value >= 0.0 for value in all_distances)
    )
    feasible = (
        foundationally_valid
        and diagnostics["classification"] == "expected-active"
        and diagnostics["active_mask"] == [-1, 0, 0, 0, 0, 0, 0]
        and math.isfinite(lower_distance)
        and 0.0 <= lower_distance <= ACTIVE_BOUND_TOLERANCE_M
        and diagnostics["raw_rank"] == 7
        and diagnostics["feasible_tangent_rank"] == 6
    )
    if feasible:
        return DISPOSITION_PASSED
    if foundationally_valid:
        return DISPOSITION_REVIEW_REQUIRED
    return DISPOSITION_FAILED


def invalid_geometry_acceptance_policy(result: dict[str, Any]) -> str:
    rejected = (
        result["diagnostics"]["classification"] == "invalid-geometry"
        and result["diagnostics"]["message"] == "radii must be positive"
        and result["support"]["factor_traversal_count"] == 0
        and result["raw"]["residual_traversal_count"] == 0
        and result["raw"]["jacobian_traversal_count"] == 0
        and result["callbacks"]["callback_calls"] == 0
        and not result["solver"]["invoked"]
        and result["termination"]["class"] == "rejected-at-evaluator-boundary"
    )
    return DISPOSITION_FAILED if rejected else DISPOSITION_UNCLASSIFIED


def mismatch_acceptance_policy(result: dict[str, Any]) -> str:
    diagnostics = result["diagnostics"]
    if (
        result["termination"]["success"]
        and diagnostics["classification"] == "model-mismatch-suspect"
        and diagnostics["analytic_mismatch_nonzero"]
        and diagnostics["matches_analytic_least_squares_expectation"]
    ):
        return DISPOSITION_REVIEW_REQUIRED
    return DISPOSITION_FAILED


def fixed_outlier_acceptance_policy() -> str:
    return DISPOSITION_UNCLASSIFIED


def binary_fixture_acceptance_policy(*measured_predicates: bool) -> str:
    return (
        DISPOSITION_PASSED
        if measured_predicates and all(measured_predicates)
        else DISPOSITION_FAILED
    )


def source_sha256() -> str:
    return sha256(Path(__file__).resolve().read_bytes())


def verify_source() -> str:
    verify_runtime()
    sidecar = Path(__file__).with_suffix(".sha256")
    try:
        checksum, filename = (
            sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
        )
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise GateError(f"invalid solver source sidecar: {error}") from error
    actual = source_sha256()
    if filename != Path(__file__).name or checksum != actual:
        fail(f"solver source checksum mismatch: expected {checksum}, got {actual}")
    return actual


def execute_verified_module(source: bytes, path: Path, name: str) -> ModuleType:
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        # Execute only the already verified bytes; path loaders may accept stale pyc.
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    except (SyntaxError, TypeError) as error:
        raise GateError(f"cannot load frozen generator: {error}") from error
    return module


def load_generator() -> Any:
    path = Path(__file__).with_name("generate_stepped_rotational_v1.py")
    source = path.read_bytes()
    if sha256(source) != GENERATOR_SHA256:
        fail("frozen generator source SHA-256 mismatch")
    return execute_verified_module(source, path, "stepped_rotational_generator")


def load_contract() -> ContractData:
    generator = load_generator()
    prepared = generator.prepare_fixture()
    manifest = prepared.manifest
    if (
        manifest["generator_version"] != "1.0.5"
        or manifest["provenance"]["generator_source_sha256"] != GENERATOR_SHA256
        or manifest["logical_hashes"]["contract"] != CONTRACT_SHA256
        or manifest["counts"]
        != {
            "factors": 454,
            "held_out": 167,
            "memberships": 1888,
            "observations": 621,
            "train": 454,
        }
    ):
        fail("frozen generator identity or counts mismatch")

    observations: dict[str, Observation] = {}
    records: dict[str, dict[str, Any]] = {}
    mappings: dict[str, Mapping] = {}
    factors: dict[str, Factor] = {}
    for record in prepared.records:
        observation_id = record["observation_id"]
        row = prepared.array[record["row_index"]]
        observations[observation_id] = Observation(
            observation_id,
            np.asarray(row[:3], dtype=np.float64),
            np.asarray(row[3:], dtype=np.float64),
            record["role"],
            tuple(record["memberships"]),
            record["row_index"],
        )
        records[observation_id] = record
        mapping = Mapping(record["mapping_id"], record["variant"], record["element_id"])
        previous = mappings.setdefault(mapping.mapping_id, mapping)
        if previous != mapping:
            fail(f"mapping declaration conflict: {mapping.mapping_id}")
        factor_id = record["factor_id"]
        if factor_id is not None:
            if record["role"] != "train":
                fail(f"held-out factor declaration: {factor_id}")
            factor = Factor(
                factor_id,
                observation_id,
                mapping.mapping_id,
                mapping.variant,
                mapping.element_id,
            )
            if factor_id in factors:
                fail(f"duplicate factor declaration: {factor_id}")
            factors[factor_id] = factor

    scenarios: dict[str, Scenario] = {}
    for item in prepared.scenarios:
        scenario = Scenario(
            item["id"],
            tuple(item["inputs"]),
            tuple(item["active_factor_ids"]),
            tuple(item.get("evaluation_only", ())),
            {
                key: value
                for key, value in item.items()
                if key not in {"id", "inputs", "active_factor_ids", "evaluation_only"}
            },
        )
        if scenario.scenario_id in scenarios:
            fail(f"duplicate scenario declaration: {scenario.scenario_id}")
        scenarios[scenario.scenario_id] = scenario

    held_out = {
        item.observation_id for item in observations.values() if item.role == "held_out"
    }
    if held_out & {factor.observation_id for factor in factors.values()}:
        fail("held-out observation instantiated a factor")
    for scenario in scenarios.values():
        unknown = set(scenario.active_factor_ids) - set(factors)
        if unknown:
            fail(f"scenario {scenario.scenario_id} has undeclared active factors")
        if held_out & {
            factors[item].observation_id for item in scenario.active_factor_ids
        }:
            fail(f"scenario {scenario.scenario_id} activates held-out evidence")
    return ContractData(manifest, observations, mappings, factors, scenarios, records)


def skew(vector: np.ndarray) -> np.ndarray:
    return np.array(
        [
            [0.0, -vector[2], vector[1]],
            [vector[2], 0.0, -vector[0]],
            [-vector[1], vector[0], 0.0],
        ]
    )


def rodrigues(rotation_vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(rotation_vector))
    if angle == 0.0:
        return np.eye(3)
    axis = rotation_vector / angle
    axis_skew = skew(axis)
    return (
        np.eye(3)
        + math.sin(angle) * axis_skew
        + (1.0 - math.cos(angle)) * (axis_skew @ axis_skew)
    )


def so3_right_jacobian(rotation_vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(rotation_vector))
    angle_squared = angle * angle
    if angle < 1e-3:
        a = 0.5 - angle_squared / 24.0 + angle_squared**2 / 720.0
        b = 1.0 / 6.0 - angle_squared / 120.0 + angle_squared**2 / 5040.0
    else:
        a = (1.0 - math.cos(angle)) / angle_squared
        b = (angle - math.sin(angle)) / (angle_squared * angle)
    rotation_skew = skew(rotation_vector)
    return np.eye(3) - a * rotation_skew + b * (rotation_skew @ rotation_skew)


def truth_local(
    data: ContractData, observation: Observation
) -> tuple[np.ndarray, np.ndarray]:
    pose = data.manifest["pose_truth"]
    rotation = rodrigues(np.asarray(pose["rotation_vector_rad"], dtype=np.float64))
    translation = np.asarray(pose["translation_m"], dtype=np.float64)
    return rotation.T @ (
        observation.point - translation
    ), rotation.T @ observation.normal


def geometry_valid(shape: np.ndarray) -> tuple[bool, str]:
    r1, r2, r3, s1, s2, s3, datum = shape
    if not np.isfinite(shape).all():
        return False, "non-finite geometry"
    if min(r1, r2, r3) <= 0.0:
        return False, "radii must be positive"
    if not 0.0 < s1 < s2 < s3:
        return False, "stations must be strictly ordered after station-0"
    if min(s1, s2 - s1, s3 - s2) <= 0.004:
        return False, "band domain is empty after axial guards"
    if r2 - r1 <= 0.002 or r2 - r3 <= 0.002:
        return False, "annular domain is empty after radial guards"
    if not 0.0 < datum < r2:
        return False, "datum offset must lie inside middle radius"
    if math.sqrt(r2 * r2 - datum * datum) <= 0.001:
        return False, "datum domain is empty after flat guard"
    return True, "valid"


def support_gradient(point: np.ndarray, element_id: str) -> np.ndarray:
    if element_id.startswith("cylinder.band-"):
        radial = math.hypot(point[0], point[1])
        if radial == 0.0:
            fail(f"degenerate cylinder gradient at axis: {element_id}")
        return np.array([point[0] / radial, point[1] / radial, 0.0])
    if element_id == "plane.datum-flat":
        return np.array([1.0, 0.0, 0.0])
    return np.array(
        [
            0.0,
            0.0,
            -1.0 if element_id in {"plane.station-0", "plane.station-20"} else 1.0,
        ]
    )


def raw_residual(point: np.ndarray, element_id: str, shape: np.ndarray) -> float:
    r1, r2, r3, s1, s2, s3, datum = shape
    if element_id.startswith("cylinder.band-"):
        radius = (r1, r2, r3)[int(element_id[-1]) - 1]
        return float(math.hypot(point[0], point[1]) - radius)
    if element_id == "plane.datum-flat":
        return float(point[0] - datum)
    stations = {
        "plane.station-0": 0.0,
        "plane.station-20": s1,
        "plane.station-50": s2,
        "plane.station-80": s3,
    }
    sign = -1.0 if element_id in {"plane.station-0", "plane.station-20"} else 1.0
    return float(sign * (point[2] - stations[element_id]))


def raw_shape_jacobian(
    factors: tuple[Factor, ...], traversed_factor_ids: list[str] | None = None
) -> np.ndarray:
    result = np.zeros((len(factors), len(SHAPE_NAMES)))
    for row, factor in enumerate(factors):
        if traversed_factor_ids is not None:
            traversed_factor_ids.append(factor.factor_id)
        element = factor.element_id
        if element.startswith("cylinder.band-"):
            result[row, int(element[-1]) - 1] = -1.0
        elif element == "plane.datum-flat":
            result[row, 6] = -1.0
        elif element == "plane.station-20":
            result[row, 3] = 1.0
        elif element == "plane.station-50":
            result[row, 4] = -1.0
        elif element == "plane.station-80":
            result[row, 5] = -1.0
    return result


def raw_shape_residuals(
    factors: tuple[Factor, ...], points: dict[str, np.ndarray], shape: np.ndarray
) -> np.ndarray:
    return np.asarray(
        [
            raw_residual(points[factor.observation_id], factor.element_id, shape)
            for factor in factors
        ]
    )


def raw_pose_residuals(
    factors: tuple[Factor, ...],
    points: dict[str, np.ndarray],
    pose_delta: np.ndarray,
) -> np.ndarray:
    rotation = rodrigues(pose_delta[3:])
    return np.asarray(
        [
            raw_residual(
                rotation @ points[factor.observation_id] + pose_delta[:3],
                factor.element_id,
                SHAPE_TRUTH,
            )
            for factor in factors
        ]
    )


def raw_pose_jacobian(
    factors: tuple[Factor, ...],
    points: dict[str, np.ndarray],
    pose_delta: np.ndarray,
    traversed_factor_ids: list[str] | None = None,
) -> np.ndarray:
    rotation = rodrigues(pose_delta[3:])
    right_jacobian = so3_right_jacobian(pose_delta[3:])
    rows = []
    for factor in factors:
        if traversed_factor_ids is not None:
            traversed_factor_ids.append(factor.factor_id)
        source_point = points[factor.observation_id]
        point = rotation @ source_point + pose_delta[:3]
        gradient = support_gradient(point, factor.element_id)
        rotation_block = -gradient @ rotation @ skew(source_point) @ right_jacobian
        rows.append(np.concatenate((gradient, rotation_block)))
    return np.asarray(rows)


def generated_guard_domain(
    point: np.ndarray, factor: Factor, shape: np.ndarray
) -> tuple[bool, str]:
    r1, r2, r3, s1, s2, s3, datum = shape
    stations = (0.0, s1, s2, s3)
    element = factor.element_id
    variant = factor.variant
    tolerance = 1e-14
    if element.startswith("cylinder.band-"):
        band = int(element[-1]) - 1
        if (
            not stations[band] + 0.002 - tolerance
            <= point[2]
            <= stations[band + 1] - 0.002 + tolerance
        ):
            return False, "outside guarded axial cylinder domain"
        radial = math.hypot(point[0], point[1])
        if radial == 0.0:
            return False, "degenerate cylinder projection at axis"
        projected_x = (r1, r2, r3)[band] * point[0] / radial
        if (
            variant == "asymmetric_datum_flat"
            and band == 1
            and projected_x > datum - 1e-12
        ):
            return False, "outside asymmetric cylinder trim"
        return True, "inside generated guarded domain"
    if element == "plane.datum-flat":
        half_width = math.sqrt(r2 * r2 - datum * datum)
        if (
            not -half_width + 0.001 - tolerance
            <= point[1]
            <= half_width - 0.001 + tolerance
        ):
            return False, "outside guarded datum y domain"
        if not s1 + 0.001 - tolerance <= point[2] <= s2 - 0.001 + tolerance:
            return False, "outside guarded datum z domain"
        return True, "inside generated guarded domain"
    domains = {
        "plane.station-0": (0.0, r1),
        "plane.station-20": (r1, r2),
        "plane.station-50": (r3, r2),
        "plane.station-80": (0.0, r3),
    }
    inner, outer = domains[element]
    radial = math.hypot(point[0], point[1])
    guarded_inner = 0.001 if inner == 0.0 else inner + 0.001
    if not guarded_inner - tolerance <= radial <= outer - 0.001 + tolerance:
        return False, "outside guarded axial-face radial domain"
    if (
        variant == "asymmetric_datum_flat"
        and element in {"plane.station-20", "plane.station-50"}
        and point[0] > datum - 0.001 + tolerance
    ):
        return False, "outside guarded asymmetric axial-face trim"
    return True, "inside generated guarded domain"


def physical_support_domain(
    point: np.ndarray, factor: Factor, shape: np.ndarray
) -> tuple[bool, str]:
    r1, r2, r3, s1, s2, s3, datum = shape
    stations = (0.0, s1, s2, s3)
    element = factor.element_id
    tolerance = 1e-12
    if element.startswith("cylinder.band-"):
        band = int(element[-1]) - 1
        radial = math.hypot(point[0], point[1])
        if radial == 0.0:
            return False, "degenerate cylinder projection at axis"
        inside = (
            stations[band] - tolerance <= point[2] <= stations[band + 1] + tolerance
        )
        if factor.variant == "asymmetric_datum_flat" and band == 1:
            projected_x = (r1, r2, r3)[band] * point[0] / radial
            inside = inside and projected_x <= datum + tolerance
        return (
            inside,
            "inside physical support" if inside else "outside bounded cylinder support",
        )
    if element == "plane.datum-flat":
        half_width = math.sqrt(r2 * r2 - datum * datum)
        inside = (
            -half_width - tolerance <= point[1] <= half_width + tolerance
            and s1 - tolerance <= point[2] <= s2 + tolerance
        )
        return (
            inside,
            "inside physical support" if inside else "outside bounded datum support",
        )
    domains = {
        "plane.station-0": (0.0, r1),
        "plane.station-20": (r1, r2),
        "plane.station-50": (r3, r2),
        "plane.station-80": (0.0, r3),
    }
    inner, outer = domains[element]
    radial = math.hypot(point[0], point[1])
    inside = inner - tolerance <= radial <= outer + tolerance
    if factor.variant == "asymmetric_datum_flat" and element in {
        "plane.station-20",
        "plane.station-50",
    }:
        inside = inside and point[0] <= datum + tolerance
    return (
        inside,
        "inside physical support" if inside else "outside bounded axial-face support",
    )


def active_factors(data: ContractData, scenario_id: str) -> tuple[Factor, ...]:
    scenario = data.scenarios[scenario_id]
    result = tuple(data.factors[factor_id] for factor_id in scenario.active_factor_ids)
    input_ids = set(scenario.input_observation_ids)
    if any(factor.observation_id not in input_ids for factor in result):
        fail(f"scenario {scenario_id} active-factor projection escapes declared inputs")
    return result


class FitCallback:
    def __init__(
        self,
        data: ContractData,
        factors: tuple[Factor, ...],
        local_points: dict[str, np.ndarray],
        support_shape: np.ndarray | None = None,
    ):
        self.data = data
        self.factors = factors
        self.local_points = local_points
        self.support_shape = support_shape
        self.seen_observation_ids: set[str] = set()
        self.calls = 0
        self.jacobian_calls = 0
        self.invocations: list[CallbackInvocation] = []

    def start_invocation(
        self, path: str, coordinates: np.ndarray
    ) -> CallbackInvocation:
        invocation = CallbackInvocation(path, coordinates.copy(), [])
        self.invocations.append(invocation)
        return invocation

    def shape(self, shape: np.ndarray) -> np.ndarray:
        invocation = self.start_invocation("shape.residual", shape)
        valid, diagnostic = geometry_valid(shape)
        if not valid:
            fail(diagnostic)
        self.calls += 1
        residuals = []
        for factor in self.factors:
            invocation.factor_ids.append(factor.factor_id)
            self.seen_observation_ids.add(factor.observation_id)
            point = self.local_points[factor.observation_id]
            inside, reason = physical_support_domain(
                point,
                factor,
                shape if self.support_shape is None else self.support_shape,
            )
            if not inside:
                fail(f"{factor.factor_id}: {reason}")
            residuals.append(raw_residual(point, factor.element_id, shape))
        result = np.asarray(residuals)
        invocation.row_count = len(result)
        return result

    def shape_jacobian(self, shape: np.ndarray) -> np.ndarray:
        invocation = self.start_invocation("shape.analytic_jacobian", shape)
        self.jacobian_calls += 1
        result = raw_shape_jacobian(self.factors, invocation.factor_ids)
        invocation.row_count = len(result)
        return result

    def pose(self, pose_delta: np.ndarray) -> np.ndarray:
        invocation = self.start_invocation("pose.residual", pose_delta)
        self.calls += 1
        rotation = rodrigues(pose_delta[3:])
        residuals = []
        for factor in self.factors:
            invocation.factor_ids.append(factor.factor_id)
            self.seen_observation_ids.add(factor.observation_id)
            point = rotation @ self.local_points[factor.observation_id] + pose_delta[:3]
            inside, reason = physical_support_domain(point, factor, SHAPE_TRUTH)
            if not inside:
                fail(f"{factor.factor_id}: {reason}")
            residuals.append(raw_residual(point, factor.element_id, SHAPE_TRUTH))
        result = np.asarray(residuals)
        invocation.row_count = len(result)
        return result

    def pose_jacobian(self, pose_delta: np.ndarray) -> np.ndarray:
        invocation = self.start_invocation("pose.analytic_jacobian", pose_delta)
        self.jacobian_calls += 1
        result = raw_pose_jacobian(
            self.factors, self.local_points, pose_delta, invocation.factor_ids
        )
        invocation.row_count = len(result)
        return result


def local_points(
    data: ContractData, offsets: dict[str, float] | None = None
) -> dict[str, np.ndarray]:
    result = {}
    offsets = offsets or {}
    for observation in data.observations.values():
        point, normal = truth_local(data, observation)
        result[observation.observation_id] = (
            point + offsets.get(observation.observation_id, 0.0) * normal
        )
    return result


def offset_application_evidence(
    data: ContractData,
    offsets: dict[str, float],
    points: dict[str, np.ndarray],
) -> dict[str, Any]:
    baseline = local_points(data)
    changed_ids = [
        observation_id
        for observation_id in baseline
        if not np.array_equal(baseline[observation_id], points[observation_id])
    ]
    if set(changed_ids) != set(offsets):
        fail("normal offsets did not change exactly the declared observations")
    applied_offsets = {}
    for observation_id in changed_ids:
        _, normal = truth_local(data, data.observations[observation_id])
        applied = float(
            np.dot(points[observation_id] - baseline[observation_id], normal)
        )
        if not math.isclose(
            applied, offsets[observation_id], rel_tol=0.0, abs_tol=1e-15
        ):
            fail(f"normal offset application mismatch: {observation_id}")
        applied_offsets[observation_id] = applied
    return {
        "applied_normal_offsets_m": applied_offsets,
        "changed_observation_count": len(changed_ids),
        "changed_observation_ids_sha256": sha256(canonical_json(changed_ids)),
        "unchanged_observation_count": len(points) - len(changed_ids),
    }


def solve_shape(
    data: ContractData,
    scenario_id: str,
    offsets: dict[str, float] | None = None,
    *,
    points: dict[str, np.ndarray] | None = None,
    initial: np.ndarray | None = None,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
    support_shape: np.ndarray | None = None,
    xtol: float = 1e-13,
) -> tuple[dict[str, Any], FitCallback]:
    factors = active_factors(data, scenario_id)
    callback = FitCallback(
        data,
        factors,
        local_points(data, offsets) if points is None else points,
        support_shape,
    )
    lower = SHAPE_LOWER if lower is None else lower
    upper = SHAPE_UPPER if upper is None else upper
    result = least_squares(
        callback.shape,
        SHAPE_INITIAL if initial is None else initial,
        jac=callback.shape_jacobian,
        bounds=(lower, upper),
        method="trf",
        loss="linear",
        x_scale="jac",
        ftol=1e-13,
        xtol=xtol,
        gtol=1e-13,
        max_nfev=1000,
    )
    estimate = result.x
    return {
        "active_factor_count": len(factors),
        "active_mask": result.active_mask.tolist(),
        "cost": float(result.cost),
        "estimate": dict(zip(SHAPE_NAMES, estimate.tolist(), strict=True)),
        "max_abs_error": float(np.max(np.abs(estimate - SHAPE_TRUTH))),
        "max_abs_residual_m": float(np.max(np.abs(result.fun)))
        if len(result.fun)
        else 0.0,
        "message": result.message,
        "nfev": result.nfev,
        "status": result.status,
        "success": bool(result.success),
    }, callback


def solve_pose(
    data: ContractData, scenario_id: str, initial: np.ndarray | None = None
) -> tuple[dict[str, Any], FitCallback]:
    factors = active_factors(data, scenario_id)
    callback = FitCallback(data, factors, local_points(data))
    result = least_squares(
        callback.pose,
        POSE_INITIAL if initial is None else initial,
        jac=callback.pose_jacobian,
        bounds=(POSE_LOWER, POSE_UPPER),
        method="trf",
        loss="linear",
        x_scale=POSE_PARAMETER_SCALES,
        ftol=1e-13,
        xtol=1e-13,
        gtol=1e-13,
        max_nfev=1000,
    )
    return {
        "active_factor_count": len(factors),
        "cost": float(result.cost),
        "estimate_local_observation_to_model": result.x.tolist(),
        "max_abs_nonroll_error": float(np.max(np.abs(result.x[:5]))),
        "roll_rad": float(result.x[5]),
        "max_abs_residual_m": float(np.max(np.abs(result.fun))),
        "message": result.message,
        "nfev": result.nfev,
        "status": result.status,
        "success": bool(result.success),
    }, callback


def pose_jacobian(
    data: ContractData,
    scenario_id: str,
    pose_delta: np.ndarray | None = None,
) -> np.ndarray:
    return raw_pose_jacobian(
        active_factors(data, scenario_id),
        local_points(data),
        np.zeros(6) if pose_delta is None else pose_delta,
    )


def spectrum(jacobian: np.ndarray) -> dict[str, Any]:
    _, singular, raw_vh = np.linalg.svd(jacobian, full_matrices=False)
    dimensionless = jacobian @ np.diag(POSE_PARAMETER_SCALES) / RESIDUAL_SCALE_M
    _, dimensionless_singular, dimensionless_vh = np.linalg.svd(
        dimensionless, full_matrices=False
    )
    raw_nonzero = singular[singular > RAW_RANK_THRESHOLD]
    scaled_nonzero = dimensionless_singular[
        dimensionless_singular > DIMENSIONLESS_RANK_THRESHOLD
    ]
    return {
        "dimensionless_parameter_scales": POSE_PARAMETER_SCALES.tolist(),
        "dimensionless_rank": int(
            np.sum(dimensionless_singular > DIMENSIONLESS_RANK_THRESHOLD)
        ),
        "dimensionless_rank_threshold": DIMENSIONLESS_RANK_THRESHOLD,
        "dimensionless_residual_scale_m": RESIDUAL_SCALE_M,
        "dimensionless_singular_values": dimensionless_singular.tolist(),
        "dimensionless_smallest_vector_roll_alignment": float(
            abs(dimensionless_vh[-1, 5])
        ),
        "dimensionless_condition_nonnull": float(
            scaled_nonzero[0] / scaled_nonzero[-1]
        ),
        "raw_rank": int(np.sum(singular > RAW_RANK_THRESHOLD)),
        "raw_rank_threshold": RAW_RANK_THRESHOLD,
        "raw_singular_values": singular.tolist(),
        "raw_smallest_vector_roll_alignment": float(abs(raw_vh[-1, 5])),
        "raw_condition_nonnull": float(raw_nonzero[0] / raw_nonzero[-1]),
    }


def pose_spectrum_evidence(
    data: ContractData, scenario_id: str, coordinates: np.ndarray
) -> dict[str, Any]:
    factors = active_factors(data, scenario_id)
    points = local_points(data)
    jacobian = raw_pose_jacobian(factors, points, coordinates)
    coordinate_list = coordinates.tolist()
    jacobian_sha = sha256(canonical_json(jacobian.tolist()))
    return {
        "evaluation_coordinates": coordinate_list,
        "evaluation_coordinates_sha256": sha256(canonical_json(coordinate_list)),
        "jacobian_sha256": jacobian_sha,
        "spectrum": spectrum(jacobian),
        "spectrum_jacobian_sha256": jacobian_sha,
    }


def shape_spectrum(jacobian: np.ndarray) -> dict[str, Any]:
    singular = np.linalg.svd(jacobian, compute_uv=False)
    dimensionless = jacobian @ np.diag(SHAPE_PARAMETER_SCALES) / RESIDUAL_SCALE_M
    dimensionless_singular = np.linalg.svd(dimensionless, compute_uv=False)
    raw_nonzero = singular[singular > RAW_RANK_THRESHOLD]
    scaled_nonzero = dimensionless_singular[
        dimensionless_singular > DIMENSIONLESS_RANK_THRESHOLD
    ]
    return {
        "dimensionless_condition_nonnull": float(
            scaled_nonzero[0] / scaled_nonzero[-1]
        ),
        "dimensionless_parameter_scales": SHAPE_PARAMETER_SCALES.tolist(),
        "dimensionless_rank": int(
            np.sum(dimensionless_singular > DIMENSIONLESS_RANK_THRESHOLD)
        ),
        "dimensionless_rank_threshold": DIMENSIONLESS_RANK_THRESHOLD,
        "dimensionless_residual_scale_m": RESIDUAL_SCALE_M,
        "dimensionless_singular_values": dimensionless_singular.tolist(),
        "raw_condition_nonnull": float(raw_nonzero[0] / raw_nonzero[-1]),
        "raw_rank": int(np.sum(singular > RAW_RANK_THRESHOLD)),
        "raw_rank_threshold": RAW_RANK_THRESHOLD,
        "raw_singular_values": singular.tolist(),
    }


def five_point_jacobian(
    function: Any, coordinates: np.ndarray, step: float
) -> np.ndarray:
    columns = []
    for column in range(len(coordinates)):
        delta = np.zeros_like(coordinates)
        delta[column] = step
        columns.append(
            (
                -function(coordinates + 2.0 * delta)
                + 8.0 * function(coordinates + delta)
                - 8.0 * function(coordinates - delta)
                + function(coordinates - 2.0 * delta)
            )
            / (12.0 * step)
        )
    return np.column_stack(columns)


def five_point_directional(
    function: Any, coordinates: np.ndarray, direction: np.ndarray, step: float
) -> np.ndarray:
    return (
        -function(coordinates + 2.0 * step * direction)
        + 8.0 * function(coordinates + step * direction)
        - 8.0 * function(coordinates - step * direction)
        + function(coordinates - 2.0 * step * direction)
    ) / (12.0 * step)


def factor_kind_counts(factors: tuple[Factor, ...]) -> dict[str, int]:
    result = {"axial_plane": 0, "cylinder": 0, "datum_flat": 0}
    for factor in factors:
        kind = (
            "cylinder"
            if factor.element_id.startswith("cylinder")
            else "datum_flat"
            if factor.element_id == "plane.datum-flat"
            else "axial_plane"
        )
        result[kind] += 1
    return result


def factor_element_counts(factors: tuple[Factor, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for factor in factors:
        result[factor.element_id] = result.get(factor.element_id, 0) + 1
    return dict(sorted(result.items()))


def derivative_stencil_candidates(
    coordinates: np.ndarray,
    direction: np.ndarray,
) -> list[tuple[str, np.ndarray]]:
    candidates = [("center", coordinates.copy())]
    offsets = (
        ("-2h", -2.0),
        ("-h", -1.0),
        ("-h/2", -0.5),
        ("+h/2", 0.5),
        ("+h", 1.0),
        ("+2h", 2.0),
    )
    for column in range(len(coordinates)):
        for magnitude, scale in offsets:
            delta = np.zeros_like(coordinates)
            delta[column] = scale * DERIVATIVE_STEP
            candidates.append(
                (f"coordinate[{column}]:{magnitude}", coordinates + delta)
            )
    for magnitude, scale in offsets:
        candidates.append(
            (
                f"directional:{magnitude}",
                coordinates + scale * DERIVATIVE_STEP * direction,
            )
        )
    return candidates


def supported_derivative_factors(
    factors: tuple[Factor, ...],
    points: dict[str, np.ndarray],
    coordinates: np.ndarray,
    direction: np.ndarray,
    kind: str,
) -> tuple[Factor, ...]:
    candidates = derivative_stencil_candidates(coordinates, direction)
    result = []
    for factor in factors:
        supported = True
        for _, candidate in candidates:
            if kind == "shape":
                valid, _ = geometry_valid(candidate)
                point = points[factor.observation_id]
                inside = valid and physical_support_domain(point, factor, candidate)[0]
            else:
                rotation = rodrigues(candidate[3:])
                point = rotation @ points[factor.observation_id] + candidate[:3]
                inside = physical_support_domain(point, factor, SHAPE_TRUTH)[0]
            if not inside:
                supported = False
                break
        if supported:
            result.append(factor)
    return tuple(result)


def derivative_probe(
    data: ContractData,
    scenario_id: str,
    kind: str,
    label: str,
    coordinates: np.ndarray,
) -> dict[str, Any]:
    points = local_points(data)
    all_factors = active_factors(data, scenario_id)
    raw_direction = (
        np.array([0.23, -0.31, 0.37, -0.41, 0.43, -0.47, 0.53])
        if kind == "shape"
        else np.array([0.31, -0.27, 0.19, -0.41, 0.53, -0.59])
    )
    direction = raw_direction / np.linalg.norm(raw_direction)
    raw_evaluation_coordinates: list[np.ndarray] = []
    callback_supported_factors = supported_derivative_factors(
        all_factors, points, coordinates, direction, kind
    )
    factors = all_factors
    if kind == "shape":

        def function(value: np.ndarray) -> np.ndarray:
            raw_evaluation_coordinates.append(value.copy())
            return raw_shape_residuals(factors, points, value)

        analytic = raw_shape_jacobian(factors)
        parameter_scales = SHAPE_PARAMETER_SCALES
        valid, diagnostic = geometry_valid(coordinates)
        if not valid:
            fail(f"shape derivative probe {label}: {diagnostic}")
        bounds_valid = bool(
            np.all(coordinates > SHAPE_LOWER) and np.all(coordinates < SHAPE_UPPER)
        )
    else:

        def function(value: np.ndarray) -> np.ndarray:
            raw_evaluation_coordinates.append(value.copy())
            return raw_pose_residuals(factors, points, value)

        analytic = raw_pose_jacobian(factors, points, coordinates)
        parameter_scales = POSE_PARAMETER_SCALES
        bounds_valid = bool(
            np.all(coordinates > POSE_LOWER) and np.all(coordinates < POSE_UPPER)
        )
    if not bounds_valid:
        fail(f"{kind} derivative probe {label} is outside runner bounds")

    finite = five_point_jacobian(function, coordinates, DERIVATIVE_STEP)
    refined = five_point_jacobian(function, coordinates, DERIVATIVE_STEP / 2.0)
    coarse_difference = analytic - finite
    difference = analytic - refined

    def relative_error(numerical: np.ndarray, error: np.ndarray) -> np.ndarray:
        denominator = np.maximum(np.abs(analytic), np.abs(numerical))
        return np.divide(
            np.abs(error),
            denominator,
            out=np.zeros_like(error),
            where=denominator > DERIVATIVE_REL_FLOOR,
        )

    relative = relative_error(refined, difference)
    coarse_relative = relative_error(finite, coarse_difference)
    directional_finite = five_point_directional(
        function, coordinates, direction, DERIVATIVE_STEP / 2.0
    )
    directional_coarse = five_point_directional(
        function, coordinates, direction, DERIVATIVE_STEP
    )
    numerical_results = {
        "analytic Jacobian": analytic,
        "Jacobian at h": finite,
        "Jacobian at h/2": refined,
        "directional derivative at h": directional_coarse,
        "directional derivative at h/2": directional_finite,
    }
    nonfinite = [
        name
        for name, value in numerical_results.items()
        if not np.isfinite(value).all()
    ]
    if nonfinite:
        fail(f"{kind} derivative probe {label} has non-finite {', '.join(nonfinite)}")
    directional_difference = analytic @ direction - directional_finite
    directional_coarse_difference = analytic @ direction - directional_coarse
    dimensionless_difference = difference @ np.diag(parameter_scales) / RESIDUAL_SCALE_M
    coarse_dimensionless_difference = (
        coarse_difference @ np.diag(parameter_scales) / RESIDUAL_SCALE_M
    )
    evaluation_coordinate_lists = [
        value.tolist() for value in raw_evaluation_coordinates
    ]
    return {
        "all_active_factor_count": len(all_factors),
        "active_factor_ids_sha256": sha256(
            canonical_json([factor.factor_id for factor in all_factors])
        ),
        "callback_supported_factor_count": len(callback_supported_factors),
        "coordinates": coordinates.tolist(),
        "directional_max_abs_error": float(
            max(
                np.max(np.abs(directional_difference)),
                np.max(np.abs(directional_coarse_difference)),
            )
        ),
        "factor_element_counts": factor_element_counts(factors),
        "factor_kind_counts": factor_kind_counts(factors),
        "finite_difference_step": DERIVATIVE_STEP,
        "max_abs_error": float(
            max(np.max(np.abs(difference)), np.max(np.abs(coarse_difference)))
        ),
        "max_dimensionless_abs_error": float(
            max(
                np.max(np.abs(dimensionless_difference)),
                np.max(np.abs(coarse_dimensionless_difference)),
            )
        ),
        "max_relative_error_above_floor": float(
            max(np.max(relative), np.max(coarse_relative))
        ),
        "point": label,
        "raw_factor_count": len(factors),
        "stencil_execution": {
            "raw_residual_coordinate_sequence_sha256": sha256(
                canonical_json(evaluation_coordinate_lists)
            ),
            "raw_residual_evaluation_count": len(raw_evaluation_coordinates),
            "raw_residual_unique_coordinate_count": len(
                {value.tobytes() for value in raw_evaluation_coordinates}
            ),
            "validated_steps": [DERIVATIVE_STEP, DERIVATIVE_STEP / 2.0],
        },
        "stencil_refinement_max_abs_change": float(np.max(np.abs(finite - refined))),
        "validation_scope": "all active raw residual equations; support-domain acceptance is checked separately",
    }


def callback_invocation_evidence(
    callback: FitCallback,
    candidates: list[tuple[str, np.ndarray]],
    factors: tuple[Factor, ...],
    kind: str,
) -> list[dict[str, Any]]:
    expected_ids = tuple(factor.factor_id for factor in factors)
    expected_hash = sha256(canonical_json(list(expected_ids)))
    expected_paths = (f"{kind}.residual", f"{kind}.analytic_jacobian")
    expected_invocation_count = 2 * len(candidates)
    if len(callback.invocations) != expected_invocation_count:
        fail(
            f"{kind} callback trace count mismatch: expected "
            f"{expected_invocation_count}, got {len(callback.invocations)}"
        )

    result = []
    for index, (label, coordinates) in enumerate(candidates):
        invocations = callback.invocations[2 * index : 2 * index + 2]
        candidate_evidence: dict[str, Any] = {
            "coordinates": coordinates.tolist(),
            "factor_element_counts": factor_element_counts(factors),
            "label": label,
        }
        for path, invocation in zip(expected_paths, invocations, strict=True):
            if invocation.path != path:
                fail(
                    f"{kind} callback {label} path mismatch: expected {path}, "
                    f"got {invocation.path}"
                )
            if not np.array_equal(invocation.coordinates, coordinates):
                fail(f"{kind} callback {label} coordinate mismatch on {path}")
            actual_ids = tuple(invocation.factor_ids)
            if actual_ids != expected_ids:
                fail(f"{kind} callback {label} ordered factor mismatch on {path}")
            if invocation.row_count != len(expected_ids):
                fail(f"{kind} callback {label} row-count mismatch on {path}")
            key = "residual" if path.endswith("residual") else "analytic_jacobian"
            candidate_evidence[key] = {
                "factor_count": len(actual_ids),
                "factor_ids_sha256": sha256(canonical_json(list(actual_ids))),
                "factor_ids_exact_active": actual_ids == expected_ids,
                "row_count": invocation.row_count,
            }
            if candidate_evidence[key]["factor_ids_sha256"] != expected_hash:
                fail(f"{kind} callback {label} factor hash mismatch on {path}")
        result.append(candidate_evidence)
    return result


def callback_domain_probe(
    data: ContractData,
    scenario_id: str,
    kind: str,
    label: str,
    coordinates: np.ndarray,
) -> dict[str, Any]:
    points = local_points(data)
    factors = active_factors(data, scenario_id)
    raw_direction = (
        np.array([0.23, -0.31, 0.37, -0.41, 0.43, -0.47, 0.53])
        if kind == "shape"
        else np.array([0.31, -0.27, 0.19, -0.41, 0.53, -0.59])
    )
    direction = raw_direction / np.linalg.norm(raw_direction)
    candidates = derivative_stencil_candidates(coordinates, direction)
    coordinate_keys = {candidate.tobytes() for _, candidate in candidates}
    if len(coordinate_keys) != len(candidates):
        fail(f"{kind} callback probe {label} has duplicate stencil coordinates")

    callback = FitCallback(data, factors, points)
    residual = callback.shape if kind == "shape" else callback.pose
    jacobian = callback.shape_jacobian if kind == "shape" else callback.pose_jacobian
    for _, candidate in candidates:
        residual(candidate)
        jacobian(candidate)

    active_ids = tuple(factor.factor_id for factor in factors)
    candidate_evidence = callback_invocation_evidence(
        callback, candidates, factors, kind
    )
    callback_trace = [
        {
            "coordinates": invocation.coordinates.tolist(),
            "factor_ids": invocation.factor_ids,
            "path": invocation.path,
            "row_count": invocation.row_count,
        }
        for invocation in callback.invocations
    ]
    return {
        "active_factor_count": len(factors),
        "active_factor_ids_sha256": sha256(canonical_json(list(active_ids))),
        "callback_evaluation_count": len(callback.invocations),
        "callback_paths_per_candidate": [
            f"{kind}.residual",
            f"{kind}.analytic_jacobian",
        ],
        "callback_trace_sha256": sha256(canonical_json(callback_trace)),
        "candidate_coordinates_sha256": sha256(
            canonical_json([candidate.tolist() for _, candidate in candidates])
        ),
        "candidate_labels_sha256": sha256(
            canonical_json([candidate_label for candidate_label, _ in candidates])
        ),
        "coordinates": coordinates.tolist(),
        "coordinate_count": len(coordinates),
        "factor_element_counts": factor_element_counts(factors),
        "factor_element_counts_exact_every_candidate": all(
            candidate["factor_element_counts"] == factor_element_counts(factors)
            for candidate in candidate_evidence
        ),
        "ordered_factor_ids_exact_active_every_evaluation": all(
            invocation[path]["factor_ids_exact_active"]
            for invocation in candidate_evidence
            for path in ("residual", "analytic_jacobian")
        ),
        "ordered_factor_ids_sha256_every_evaluation": sha256(
            canonical_json(list(active_ids))
        ),
        "point": label,
        "row_count_every_evaluation": len(factors),
        "stencil": "unique center and coordinate/directional candidates used by five-point checks at h and h/2",
        "stencil_candidate_count": len(candidates),
        "unique_stencil_candidate_count": len(coordinate_keys),
    }


def derivative_checks(data: ContractData) -> dict[str, Any]:
    shape_near_bound = SHAPE_LOWER + 0.01 * (SHAPE_UPPER - SHAPE_LOWER)
    pose_near_bound = np.array([0.00297, -0.00297, 0.00297, 0.0792, -0.0792, 0.0792])
    probes = {
        "shape": [
            derivative_probe(
                data, "asymmetric-full-pose", "shape", "nominal", SHAPE_TRUTH
            ),
            derivative_probe(
                data, "asymmetric-full-pose", "shape", "perturbed", SHAPE_INITIAL
            ),
            derivative_probe(
                data,
                "asymmetric-full-pose",
                "shape",
                "near-parameter-bound-raw",
                shape_near_bound,
            ),
        ],
        "pose": [
            derivative_probe(
                data, "asymmetric-full-pose", "pose", "nominal", np.zeros(6)
            ),
            derivative_probe(
                data, "asymmetric-full-pose", "pose", "perturbed", POSE_INITIAL
            ),
            derivative_probe(
                data,
                "asymmetric-full-pose",
                "pose",
                "small-angle",
                np.array([0.0, 0.0, 0.0, 1e-4, -2e-4, 3e-4]),
            ),
            derivative_probe(
                data,
                "asymmetric-full-pose",
                "pose",
                "near-parameter-bound-raw",
                pose_near_bound,
            ),
        ],
    }
    stencil_executions = []
    for kind, kind_probes in probes.items():
        for probe in kind_probes:
            stencil_executions.append(
                {
                    "kind": kind,
                    "point": probe["point"],
                    **probe.pop("stencil_execution"),
                }
            )
    return {
        "absolute_tolerance": DERIVATIVE_ABS_TOL,
        "callback_domain_probes": [
            callback_domain_probe(
                data, "asymmetric-full-pose", "shape", "shape-nominal", SHAPE_TRUTH
            ),
            callback_domain_probe(
                data,
                "asymmetric-full-pose",
                "shape",
                "shape-perturbed",
                SHAPE_INITIAL,
            ),
            callback_domain_probe(
                data,
                "asymmetric-full-pose",
                "pose",
                "pose-nominal",
                np.zeros(6),
            ),
            callback_domain_probe(
                data,
                "asymmetric-full-pose",
                "pose",
                "pose-perturbed",
                POSE_INITIAL,
            ),
        ],
        "dimensionless_absolute_tolerance": DERIVATIVE_DIMENSIONLESS_ABS_TOL,
        "method": "independent five-point central differences at h and h/2 plus five-point directional differences",
        "numerical_stencil_executions": stencil_executions,
        "probes": probes,
        "relative_evaluation_floor": DERIVATIVE_REL_FLOOR,
        "relative_tolerance": DERIVATIVE_REL_TOL,
        "scope": "raw derivative probes evaluate every active factor independent of domain; callback-domain probes independently require complete support",
    }


def callback_audit(data: ContractData, callback: FitCallback) -> dict[str, Any]:
    held_out = {
        item.observation_id
        for item in data.observations.values()
        if item.role == "held_out"
    }
    leaked = sorted(callback.seen_observation_ids & held_out)
    expected_ids = [factor.factor_id for factor in callback.factors]
    expected_kind = (
        "shape" if callback.invocations[0].path.startswith("shape.") else "pose"
    )
    expected_paths = (f"{expected_kind}.residual", f"{expected_kind}.analytic_jacobian")
    if len(callback.invocations) != callback.calls + callback.jacobian_calls:
        fail("solver callback invocation count does not match call counters")
    if callback.calls != callback.jacobian_calls:
        fail("solver residual and Jacobian callback counts differ")
    for index, invocation in enumerate(callback.invocations):
        if invocation.path != expected_paths[index % 2]:
            fail("solver callback paths are not paired residual/Jacobian calls")
        if invocation.factor_ids != expected_ids:
            fail("solver callback ordered factors differ from active factors")
        if invocation.row_count != len(expected_ids):
            fail("solver callback row count differs from active factor count")
    return {
        "callback_calls": callback.calls,
        "callback_factor_sequence_sha256": sha256(
            canonical_json(
                [
                    {
                        "factor_ids": invocation.factor_ids,
                        "path": invocation.path,
                        "row_count": invocation.row_count,
                    }
                    for invocation in callback.invocations
                ]
            )
        ),
        "callback_invocation_count": len(callback.invocations),
        "held_out_seen": leaked,
        "jacobian_calls": callback.jacobian_calls,
        "seen_observation_count": len(callback.seen_observation_ids),
    }


def roll_equivalence(data: ContractData, scenario_id: str) -> dict[str, Any]:
    factors = active_factors(data, scenario_id)
    points = local_points(data)
    angle = 0.05
    minus_coordinates = np.array([0.0, 0.0, 0.0, 0.0, 0.0, -angle])
    plus_coordinates = -minus_coordinates
    minus_residual = raw_pose_residuals(factors, points, minus_coordinates)
    plus_residual = raw_pose_residuals(factors, points, plus_coordinates)
    minus_solution, minus_callback = solve_pose(data, scenario_id, minus_coordinates)
    plus_solution, plus_callback = solve_pose(data, scenario_id, plus_coordinates)

    def solution_summary(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "max_abs_nonroll_error": value["max_abs_nonroll_error"],
            "max_abs_residual_m": value["max_abs_residual_m"],
            "roll_rad": value["roll_rad"],
            "success": value["success"],
        }

    return {
        "analytic_solver_jacobians_used": (
            minus_callback.jacobian_calls > 0 and plus_callback.jacobian_calls > 0
        ),
        "held_out_seen": sorted(
            minus_callback.seen_observation_ids
            & {
                item.observation_id
                for item in data.observations.values()
                if item.role == "held_out"
            }
            | plus_callback.seen_observation_ids
            & {
                item.observation_id
                for item in data.observations.values()
                if item.role == "held_out"
            }
        ),
        "initial_rolls_rad": [-angle, angle],
        "minus_max_abs_residual_m": float(np.max(np.abs(minus_residual))),
        "plus_max_abs_residual_m": float(np.max(np.abs(plus_residual))),
        "residual_vector_difference_max_abs_m": float(
            np.max(np.abs(minus_residual - plus_residual))
        ),
        "solution_minus": solution_summary(minus_solution),
        "solution_plus": solution_summary(plus_solution),
    }


def gauge_checks(
    data: ContractData, ranks: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    roll_vector = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    scenarios = {
        scenario_id: roll_equivalence(data, scenario_id)
        for scenario_id in (
            "axisymmetric-free-roll",
            "asymmetric-full-pose",
            "flat-factor-ablation",
        )
    }
    for scenario_id in ("axisymmetric-free-roll", "flat-factor-ablation"):
        jacobian = pose_jacobian(data, scenario_id)
        scenarios[scenario_id]["raw_roll_column_max_abs"] = float(
            np.max(np.abs(jacobian @ roll_vector))
        )
        scenarios[scenario_id]["null_alignment_error"] = float(
            1.0 - ranks[scenario_id]["dimensionless_smallest_vector_roll_alignment"]
        )

    asymmetric_factors = active_factors(data, "asymmetric-full-pose")
    asymmetric_jacobian = pose_jacobian(data, "asymmetric-full-pose")
    flat_indices = [
        index
        for index, factor in enumerate(asymmetric_factors)
        if factor.element_id == "plane.datum-flat"
    ]
    scenarios["asymmetric-full-pose"]["datum_flat_roll_column_norm_m_per_rad"] = float(
        np.linalg.norm(asymmetric_jacobian[flat_indices, 5])
    )
    scenarios["asymmetric-full-pose"]["full_roll_column_norm_m_per_rad"] = float(
        np.linalg.norm(asymmetric_jacobian[:, 5])
    )
    return scenarios


def held_out_oracle(data: ContractData, shape: np.ndarray) -> dict[str, Any]:
    scenario = data.scenarios["coherent-held-out-strips-sectors"]
    residuals = []
    failures = []
    for observation_id in scenario.evaluation_only_ids:
        observation = data.observations[observation_id]
        record = data.records[observation_id]
        mapping = data.mappings[record["mapping_id"]]
        synthetic_factor = Factor(
            "evaluation-only",
            observation_id,
            mapping.mapping_id,
            mapping.variant,
            mapping.element_id,
        )
        point, _ = truth_local(data, observation)
        inside, reason = generated_guard_domain(point, synthetic_factor, shape)
        if not inside:
            failures.append({"observation_id": observation_id, "reason": reason})
            continue
        residuals.append(raw_residual(point, mapping.element_id, shape))
    return {
        "evaluated_count": len(residuals),
        "factor_count": 0,
        "fit_callback_used": False,
        "max_abs_residual_m": float(np.max(np.abs(residuals))),
        "support_failures": failures,
    }


def held_out_oracle_acceptable(result: dict[str, Any]) -> bool:
    residual = result["max_abs_residual_m"]
    return (
        result["evaluated_count"] == 167
        and result["factor_count"] == 0
        and not result["fit_callback_used"]
        and not result["support_failures"]
        and math.isfinite(residual)
        and residual <= ORACLE_TOLERANCE
    )


def recovered_gauge_solution_acceptable(
    result: dict[str, Any], *, require_roll_recovery: bool
) -> bool:
    nonroll_error = result["max_abs_nonroll_error"]
    residual = result["max_abs_residual_m"]
    roll = result["roll_rad"]
    return (
        result["success"]
        and math.isfinite(nonroll_error)
        and nonroll_error <= RECOVERY_TOLERANCE
        and math.isfinite(residual)
        and residual <= ORACLE_TOLERANCE
        and math.isfinite(roll)
        and (not require_roll_recovery or abs(roll) <= RECOVERY_TOLERANCE)
    )


def free_roll_gauge_acceptable(result: dict[str, Any]) -> bool:
    minus_initial_residual = result["minus_max_abs_residual_m"]
    plus_initial_residual = result["plus_max_abs_residual_m"]
    residual_difference = result["residual_vector_difference_max_abs_m"]
    roll_column = result["raw_roll_column_max_abs"]
    null_alignment = result["null_alignment_error"]
    minus_solution = result["solution_minus"]
    plus_solution = result["solution_plus"]
    roll_separation = plus_solution["roll_rad"] - minus_solution["roll_rad"]
    return (
        math.isfinite(minus_initial_residual)
        and minus_initial_residual <= ORACLE_TOLERANCE
        and math.isfinite(plus_initial_residual)
        and plus_initial_residual <= ORACLE_TOLERANCE
        and math.isfinite(residual_difference)
        and residual_difference <= ORACLE_TOLERANCE
        and result["analytic_solver_jacobians_used"]
        and not result["held_out_seen"]
        and math.isfinite(roll_column)
        and roll_column <= 1e-14
        and math.isfinite(null_alignment)
        and null_alignment <= 1e-12
        and recovered_gauge_solution_acceptable(
            minus_solution, require_roll_recovery=False
        )
        and recovered_gauge_solution_acceptable(
            plus_solution, require_roll_recovery=False
        )
        and math.isfinite(roll_separation)
        and abs(roll_separation) >= 0.09
    )


def asymmetric_datum_gauge_acceptable(
    result: dict[str, Any], rank: dict[str, Any]
) -> bool:
    datum_leverage = result["datum_flat_roll_column_norm_m_per_rad"]
    full_leverage = result["full_roll_column_norm_m_per_rad"]
    minus_initial_residual = result["minus_max_abs_residual_m"]
    plus_initial_residual = result["plus_max_abs_residual_m"]
    residual_difference = result["residual_vector_difference_max_abs_m"]
    smallest_singular_value = rank["dimensionless_singular_values"][-1]
    return (
        math.isfinite(datum_leverage)
        and datum_leverage > 0.01
        and math.isfinite(full_leverage)
        and math.isfinite(smallest_singular_value)
        and smallest_singular_value > 1.0
        and math.isfinite(minus_initial_residual)
        and math.isfinite(plus_initial_residual)
        and math.isfinite(residual_difference)
        and residual_difference > 1e-4
        and result["analytic_solver_jacobians_used"]
        and not result["held_out_seen"]
        and recovered_gauge_solution_acceptable(
            result["solution_minus"], require_roll_recovery=True
        )
        and recovered_gauge_solution_acceptable(
            result["solution_plus"], require_roll_recovery=True
        )
    )


def mapping_cell_counts(
    data: ContractData, factors: tuple[Factor, ...]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for factor in factors:
        cell = data.records[factor.observation_id]["coverage_cell"]
        counts = result.setdefault(factor.mapping_id, {})
        counts[cell] = counts.get(cell, 0) + 1
    return {
        mapping_id: dict(sorted(cells.items()))
        for mapping_id, cells in sorted(result.items())
    }


def coverage_reference(data: ContractData) -> dict[str, Any]:
    factors = tuple(data.factors.values())
    if len(factors) != 454 or any(
        data.observations[factor.observation_id].role != "train" for factor in factors
    ):
        fail("coverage reference is not the complete explicit training-factor corpus")
    counts = mapping_cell_counts(data, factors)
    if set(counts) != set(data.mappings):
        fail("coverage reference does not contain every declared mapping")
    factor_ids = [factor.factor_id for factor in factors]
    return {
        "factor_count": len(factors),
        "factor_ids_sha256": sha256(canonical_json(factor_ids)),
        "mapping_cell_counts": counts,
        "mapping_cell_counts_sha256": sha256(canonical_json(counts)),
        "mapping_count": len(counts),
        "source": "complete explicit training-factor corpus",
    }


def coverage_diagnostics(
    data: ContractData,
    scenario_id: str,
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = coverage_reference(data) if reference is None else reference
    required_counts = reference["mapping_cell_counts"]
    required = set(required_counts)
    factors = active_factors(data, scenario_id)
    counts = mapping_cell_counts(data, factors)
    missing_mappings = sorted(required - set(counts))
    missing_cells = {
        mapping_id: sorted(
            set(required_counts[mapping_id]) - set(counts.get(mapping_id, {}))
        )
        for mapping_id in sorted(required)
        if set(required_counts[mapping_id]) - set(counts.get(mapping_id, {}))
    }
    return {
        "covered_mapping_count": len(counts),
        "mapping_cell_counts": counts,
        "missing_coverage_cells": missing_cells,
        "missing_required_mappings": missing_mappings,
        "reference_factor_count": reference["factor_count"],
        "reference_factor_ids_sha256": reference["factor_ids_sha256"],
        "reference_mapping_cell_counts_sha256": reference["mapping_cell_counts_sha256"],
        "reference_source": reference["source"],
        "required_mapping_count": len(required),
    }


def raw_shape_evidence(
    factors: tuple[Factor, ...], points: dict[str, np.ndarray], shape: np.ndarray
) -> dict[str, Any]:
    residuals = raw_shape_residuals(factors, points, shape)
    jacobian = raw_shape_jacobian(factors)
    return {
        "jacobian_sha256": sha256(canonical_json(jacobian.tolist())),
        "jacobian_shape": list(jacobian.shape),
        "max_abs_residual_m": float(np.max(np.abs(residuals)))
        if len(residuals)
        else 0.0,
        "residual_count": len(residuals),
        "residuals_sha256": sha256(canonical_json(residuals.tolist())),
        "spectrum": shape_spectrum(jacobian),
    }


def support_evidence(
    factors: tuple[Factor, ...], points: dict[str, np.ndarray], shape: np.ndarray
) -> dict[str, Any]:
    failures = []
    traversed = []
    valid, diagnostic = geometry_valid(shape)
    if valid:
        for factor in factors:
            traversed.append(factor.factor_id)
            inside, reason = physical_support_domain(
                points[factor.observation_id], factor, shape
            )
            if not inside:
                failures.append({"factor_id": factor.factor_id, "reason": reason})
    return {
        "factor_traversal_count": len(traversed),
        "factor_traversal_sha256": sha256(canonical_json(traversed)),
        "failures": failures,
        "geometry_diagnostic": diagnostic,
        "geometry_valid": valid,
    }


def solved_shape_scenario(
    data: ContractData,
    scenario_id: str,
    solution: dict[str, Any],
    callback: FitCallback,
    points: dict[str, np.ndarray],
    *,
    diagnostics: dict[str, Any],
    support_shape: np.ndarray | None = None,
) -> dict[str, Any]:
    factors = active_factors(data, scenario_id)
    estimate = np.array([solution["estimate"][name] for name in SHAPE_NAMES])
    solver = {
        key: value
        for key, value in solution.items()
        if key not in {"message", "nfev", "status", "success"}
    }
    return {
        "active_factor_count": len(factors),
        "callbacks": callback_audit(data, callback),
        "diagnostics": diagnostics,
        "raw": raw_shape_evidence(factors, points, estimate),
        "solver": solver,
        "support": support_evidence(
            factors, points, estimate if support_shape is None else support_shape
        ),
        "termination": {
            key: solution[key] for key in ("message", "nfev", "status", "success")
        },
    }


def oracle_report(data: ContractData) -> dict[str, Any]:
    max_residual = 0.0
    support_failures = []
    kind_counts: dict[str, int] = {"axial_plane": 0, "cylinder": 0, "datum_flat": 0}
    for factor in data.factors.values():
        point, _ = truth_local(data, data.observations[factor.observation_id])
        inside, reason = generated_guard_domain(point, factor, SHAPE_TRUTH)
        if not inside:
            support_failures.append({"factor_id": factor.factor_id, "reason": reason})
        max_residual = max(
            max_residual, abs(raw_residual(point, factor.element_id, SHAPE_TRUTH))
        )
        kind = (
            "cylinder"
            if factor.element_id.startswith("cylinder")
            else "datum_flat"
            if factor.element_id == "plane.datum-flat"
            else "axial_plane"
        )
        kind_counts[kind] += 1

    invalid_factor = next(iter(data.factors.values()))
    invalid_point, _ = truth_local(
        data, data.observations[invalid_factor.observation_id]
    )
    invalid_point = invalid_point.copy()
    invalid_point[2] = 1.0
    invalid_inside, invalid_reason = generated_guard_domain(
        invalid_point, invalid_factor, SHAPE_TRUTH
    )
    valid, invalid_geometry_reason = geometry_valid(
        np.array([0.012, -0.018, 0.014, 0.020, 0.050, 0.080, 0.016])
    )
    return {
        "factor_kind_counts": kind_counts,
        "invalid_geometry_diagnostic": invalid_geometry_reason,
        "invalid_geometry_rejected": not valid,
        "max_abs_raw_residual_m": max_residual,
        "oracle_tolerance_m": ORACLE_TOLERANCE,
        "out_of_support_diagnostic": invalid_reason,
        "out_of_support_rejected": not invalid_inside,
        "support_failures": support_failures,
    }


def pose_scenario_result(
    data: ContractData,
    scenario_id: str,
    solution: dict[str, Any],
    callback: FitCallback,
) -> dict[str, Any]:
    factors = active_factors(data, scenario_id)
    points = local_points(data)
    coordinates = np.asarray(solution["estimate_local_observation_to_model"])
    residuals = raw_pose_residuals(factors, points, coordinates)
    jacobian = raw_pose_jacobian(factors, points, coordinates)
    jacobian_sha = sha256(canonical_json(jacobian.tolist()))
    coordinate_sha = sha256(canonical_json(coordinates.tolist()))
    recovered_spectrum = spectrum(jacobian)
    solver = {
        key: value
        for key, value in solution.items()
        if key not in {"message", "nfev", "status", "success"}
    }
    return {
        "active_factor_count": len(factors),
        "callbacks": callback_audit(data, callback),
        "diagnostics": {
            "recovered_pose_observability": f"rank-{recovered_spectrum['raw_rank']}"
        },
        "raw": {
            "evaluation_coordinates": coordinates.tolist(),
            "evaluation_coordinates_sha256": coordinate_sha,
            "jacobian_evaluation_coordinates_sha256": coordinate_sha,
            "jacobian_sha256": jacobian_sha,
            "jacobian_shape": list(jacobian.shape),
            "max_abs_residual_m": float(np.max(np.abs(residuals))),
            "residual_count": len(residuals),
            "residuals_sha256": sha256(canonical_json(residuals.tolist())),
            "spectrum": recovered_spectrum,
            "spectrum_jacobian_sha256": jacobian_sha,
        },
        "solver": solver,
        "support": support_evidence(factors, points, SHAPE_TRUTH),
        "termination": {
            key: solution[key] for key in ("message", "nfev", "status", "success")
        },
    }


def rejected_corrupted_mapping(data: ContractData) -> dict[str, Any]:
    scenario = data.scenarios["corrupted-mapping"]
    override = scenario.declaration["mapping_override"]
    if override != {
        "obs.asymmetric_datum_flat.cylinder.band-1.z00.a13": "mapping.axisymmetric.cylinder.band-3"
    }:
        fail("corrupted-mapping declaration drift")
    observation_id, mapping_id = next(iter(override.items()))
    original = next(
        factor
        for factor in active_factors(data, scenario.scenario_id)
        if factor.observation_id == observation_id
    )
    mapping = data.mappings[mapping_id]
    overridden = Factor(
        original.factor_id,
        original.observation_id,
        mapping.mapping_id,
        mapping.variant,
        mapping.element_id,
    )
    point = local_points(data)[observation_id]
    support_attempt_mapping_ids = []
    support_attempt_mapping_ids.append(overridden.mapping_id)
    inside, reason = physical_support_domain(point, overridden, SHAPE_TRUTH)
    if inside:
        fail("corrupted mapping unexpectedly has compatible bounded support")
    return {
        "active_factor_count": len(scenario.active_factor_ids),
        "callbacks": {
            "callback_calls": 0,
            "callback_invocation_count": 0,
            "jacobian_calls": 0,
            "held_out_seen": [],
        },
        "diagnostics": {
            "classification": "mapping-suspect",
            "overridden_factor_id": original.factor_id,
            "overridden_observation_id": observation_id,
            "original_mapping_id": original.mapping_id,
            "override_mapping_id": mapping_id,
            "retarget_attempt_count": 0,
            "support_attempt_mapping_ids": support_attempt_mapping_ids,
            "unmodified_factor_count": len(scenario.active_factor_ids) - 1,
        },
        "raw": {"jacobian_traversal_count": 0, "residual_traversal_count": 0},
        "solver": {"invoked": False},
        "support": {
            "factor_traversal_count": 1,
            "failures": [{"factor_id": original.factor_id, "reason": reason}],
        },
        "termination": {"class": "rejected-before-solve"},
    }


def rejected_invalid_geometry(data: ContractData) -> dict[str, Any]:
    scenario = data.scenarios["invalid-geometry-declaration"]
    if scenario.declaration != {
        "declaration": {"radius.band-2_m": -0.018},
        "expected": "reject before evaluation",
    }:
        fail("invalid-geometry declaration drift")
    shape = SHAPE_TRUTH.copy()
    shape[1] = scenario.declaration["declaration"]["radius.band-2_m"]
    valid, diagnostic = geometry_valid(shape)
    if valid or diagnostic != "radii must be positive":
        fail("invalid-geometry scenario did not produce its exact diagnostic")
    return {
        "active_factor_count": 0,
        "callbacks": {
            "callback_calls": 0,
            "callback_invocation_count": 0,
            "jacobian_calls": 0,
            "held_out_seen": [],
        },
        "diagnostics": {
            "classification": "invalid-geometry",
            "declaration": {"radius.band-2_m": -0.018},
            "message": diagnostic,
        },
        "raw": {"jacobian_traversal_count": 0, "residual_traversal_count": 0},
        "solver": {"invoked": False},
        "support": {"factor_traversal_count": 0, "failures": []},
        "termination": {"class": "rejected-at-evaluator-boundary"},
    }


def mismatch_points_and_bounds(
    data: ContractData,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    points = local_points(data)
    original_points = {key: value.copy() for key, value in points.items()}
    scenario = data.scenarios["out-of-contract-mismatch"]
    declaration = scenario.declaration
    if declaration != {
        "declaration": {
            "middle_cross_section": "ellipse",
            "semi_axes_m": [0.018, 0.017],
        },
        "expected": "model-mismatch-suspect",
    }:
        fail("out-of-contract-mismatch declaration drift")
    semi_major, semi_minor = declaration["declaration"]["semi_axes_m"]
    factors = active_factors(data, "out-of-contract-mismatch")
    middle_ids = [
        factor.observation_id
        for factor in factors
        if factor.element_id == "cylinder.band-2"
    ]
    analytic_radii = []
    analytic_angles = []
    for observation_id in middle_ids:
        point = points[observation_id].copy()
        theta = math.atan2(point[1], point[0])
        analytic_angles.append(theta)
        point[0] = semi_major * math.cos(theta)
        point[1] = semi_minor * math.sin(theta)
        points[observation_id] = point
        analytic_radii.append(math.hypot(point[0], point[1]))
    radii = np.asarray(analytic_radii)
    middle_id_set = set(middle_ids)
    if any(
        not np.array_equal(point, original_points[observation_id])
        for observation_id, point in points.items()
        if observation_id not in middle_id_set
    ):
        fail("out-of-contract mismatch changed an undeclared observation")
    if any(data.observations[item].role != "train" for item in middle_ids):
        fail("out-of-contract mismatch reached non-training evidence")
    expected_fit_radius = float(np.mean(radii))
    expected_residuals = radii - expected_fit_radius
    residual_span = float(np.max(expected_residuals) - np.min(expected_residuals))
    residual_rms = float(np.sqrt(np.mean(expected_residuals**2)))
    mismatch_nonzero = bool(
        abs(semi_major - semi_minor) > MISMATCH_NONZERO_TOLERANCE_M
        and residual_span > MISMATCH_NONZERO_TOLERANCE_M
        and residual_rms > MISMATCH_NONZERO_TOLERANCE_M
    )
    sample_binding = [
        {
            "angle_rad": angle,
            "observation_id": observation_id,
            "sampled_radius_m": radius,
        }
        for observation_id, angle, radius in zip(
            middle_ids, analytic_angles, analytic_radii, strict=True
        )
    ]
    return points, {
        "analytic_circular_least_squares_radius_m": expected_fit_radius,
        "analytic_middle_angles_rad": analytic_angles,
        "analytic_middle_angles_rad_sha256": sha256(canonical_json(analytic_angles)),
        "analytic_middle_residual_max_abs_m": float(np.max(np.abs(expected_residuals))),
        "analytic_middle_residual_rms_m": residual_rms,
        "analytic_middle_residual_span_m": residual_span,
        "analytic_middle_residuals_m": expected_residuals.tolist(),
        "analytic_middle_residuals_sha256": sha256(
            canonical_json(expected_residuals.tolist())
        ),
        "analytic_middle_sample_binding_sha256": sha256(canonical_json(sample_binding)),
        "analytic_mismatch_nonzero": mismatch_nonzero,
        "analytic_sampled_radii_m": analytic_radii,
        "analytic_sampled_radii_sha256": sha256(canonical_json(analytic_radii)),
        "declared_training_middle_factor_count": len(middle_ids),
        "declared_training_middle_ids_sha256": sha256(canonical_json(middle_ids)),
        "input_observation_count": len(scenario.input_observation_ids),
        "mismatch_analytic_tolerance_m": MISMATCH_ANALYTIC_TOLERANCE_M,
        "non_middle_observations_unchanged": True,
        "ordered_middle_observation_ids": middle_ids,
        "realization": "runner-local/non-normative",
        "semi_axes_m": [semi_major, semi_minor],
    }


def run_gate() -> dict[str, Any]:
    tool_sha = verify_source()
    data = load_contract()
    oracle = oracle_report(data)
    fixed, fixed_callback = solve_shape(data, "noiseless-fixed-pose")
    fixed_shape = np.array([fixed["estimate"][name] for name in SHAPE_NAMES])
    axisymmetric, axisymmetric_callback = solve_pose(data, "axisymmetric-free-roll")
    asymmetric, asymmetric_callback = solve_pose(data, "asymmetric-full-pose")
    ablation, ablation_callback = solve_pose(data, "flat-factor-ablation")
    noise_scenario = data.scenarios["balanced-normal-noise"]
    noise_offsets = {
        key: float(value)
        for key, value in noise_scenario.declaration["normal_offsets_m"].items()
    }
    balanced, balanced_callback = solve_shape(
        data, "balanced-normal-noise", noise_offsets
    )
    held_out = held_out_oracle(data, fixed_shape)
    adequate, adequate_callback = solve_shape(data, "coverage-adequate")
    uneven, uneven_callback = solve_shape(data, "coverage-uneven")
    inadequate, inadequate_callback = solve_shape(data, "coverage-inadequate")
    coverage_reference_evidence = coverage_reference(data)
    coverage_results = {
        scenario_id: coverage_diagnostics(
            data, scenario_id, coverage_reference_evidence
        )
        for scenario_id in (
            "coverage-adequate",
            "coverage-uneven",
            "coverage-inadequate",
        )
    }
    outlier_offsets = {
        key: float(value)
        for key, value in data.scenarios["fixed-outliers"]
        .declaration["outlier_offsets_m"]
        .items()
    }
    outlier_points = local_points(data, outlier_offsets)
    outlier_application = offset_application_evidence(
        data, outlier_offsets, outlier_points
    )
    outlier_truth_residuals = raw_shape_residuals(
        active_factors(data, "fixed-outliers"), outlier_points, SHAPE_TRUTH
    )
    if int(np.count_nonzero(np.abs(outlier_truth_residuals) > ORACLE_TOLERANCE)) != 3:
        fail("fixed outliers did not produce exactly three nonzero truth residuals")
    outliers, outlier_callback = solve_shape(
        data, "fixed-outliers", points=outlier_points
    )

    active_lower = SHAPE_LOWER.copy()
    active_lower[0] = data.scenarios["legal-active-bound"].declaration["bound_m"]
    active_initial = SHAPE_TRUTH.copy()
    active_bound, active_bound_callback = solve_shape(
        data,
        "legal-active-bound",
        initial=active_initial,
        lower=active_lower,
        xtol=1e-9,
    )
    active_estimate = np.array([active_bound["estimate"][name] for name in SHAPE_NAMES])
    active_jacobian = raw_shape_jacobian(active_factors(data, "legal-active-bound"))
    free_columns = np.flatnonzero(np.asarray(active_bound["active_mask"]) == 0)
    active_bound_diagnostics = {
        "active_mask": active_bound["active_mask"],
        "active_bound_tolerance_m": ACTIVE_BOUND_TOLERANCE_M,
        "declared_bound_m": data.scenarios["legal-active-bound"].declaration["bound_m"],
        "declared_parameter": data.scenarios["legal-active-bound"].declaration[
            "parameter"
        ],
        "distance_to_lower_bounds_m": (active_estimate - active_lower).tolist(),
        "distance_to_upper_bounds_m": (SHAPE_UPPER - active_estimate).tolist(),
        "expected_active_parameter": "radius.band-1",
        "feasible_tangent_rank": int(
            np.sum(
                np.linalg.svd(active_jacobian[:, free_columns], compute_uv=False)
                > RAW_RANK_THRESHOLD
            )
        ),
        "geometry_valid": geometry_valid(active_estimate)[0],
        "initialization_valid": bool(
            np.all(active_initial >= active_lower)
            and np.all(active_initial <= SHAPE_UPPER)
            and geometry_valid(active_initial)[0]
        ),
        "lower_bounds_m": active_lower.tolist(),
        "raw_rank": shape_spectrum(active_jacobian)["raw_rank"],
    }

    mismatch_points, mismatch_analytic = mismatch_points_and_bounds(data)
    mismatch, mismatch_callback = solve_shape(
        data,
        "out-of-contract-mismatch",
        points=mismatch_points,
        support_shape=SHAPE_TRUTH,
    )
    mismatch_estimate = np.array([mismatch["estimate"][name] for name in SHAPE_NAMES])
    mismatch_factors = active_factors(data, "out-of-contract-mismatch")
    mismatch_middle_factors = tuple(
        factor for factor in mismatch_factors if factor.element_id == "cylinder.band-2"
    )
    mismatch_middle_residuals = raw_shape_residuals(
        mismatch_middle_factors, mismatch_points, mismatch_estimate
    )
    analytic_middle_residuals = np.asarray(
        mismatch_analytic["analytic_middle_residuals_m"]
    )
    mismatch_residual_error = mismatch_middle_residuals - analytic_middle_residuals
    mismatch_radius_error = abs(
        mismatch_estimate[1]
        - mismatch_analytic["analytic_circular_least_squares_radius_m"]
    )
    mismatch_middle_residual_rms = float(np.sqrt(np.mean(mismatch_middle_residuals**2)))
    mismatch_middle_residual_span = float(
        np.max(mismatch_middle_residuals) - np.min(mismatch_middle_residuals)
    )
    mismatch_matches_analytic = bool(
        mismatch_analytic["analytic_mismatch_nonzero"]
        and mismatch_radius_error <= MISMATCH_ANALYTIC_TOLERANCE_M
        and float(np.max(np.abs(mismatch_residual_error)))
        <= MISMATCH_ANALYTIC_TOLERANCE_M
        and abs(
            mismatch_middle_residual_rms
            - mismatch_analytic["analytic_middle_residual_rms_m"]
        )
        <= MISMATCH_ANALYTIC_TOLERANCE_M
        and abs(
            mismatch_middle_residual_span
            - mismatch_analytic["analytic_middle_residual_span_m"]
        )
        <= MISMATCH_ANALYTIC_TOLERANCE_M
    )
    mismatch_diagnostics = {
        **mismatch_analytic,
        "classification": (
            "model-mismatch-suspect"
            if mismatch_matches_analytic
            else "mismatch-analytic-check-failed"
        ),
        "fitted_middle_radius_m": mismatch_estimate[1],
        "fitted_middle_radius_error_m": mismatch_radius_error,
        "middle_max_abs_residual_m": float(np.max(np.abs(mismatch_middle_residuals))),
        "middle_residual_rms_m": mismatch_middle_residual_rms,
        "middle_residual_span_m": mismatch_middle_residual_span,
        "middle_residual_vector_max_abs_error_m": float(
            np.max(np.abs(mismatch_residual_error))
        ),
        "middle_raw_residuals_sha256": sha256(
            canonical_json(mismatch_middle_residuals.tolist())
        ),
        "matches_analytic_least_squares_expectation": mismatch_matches_analytic,
    }

    truth_origin_pose_rank_compatibility = {
        scenario: pose_spectrum_evidence(data, scenario, np.zeros(6))
        for scenario in (
            "axisymmetric-free-roll",
            "asymmetric-full-pose",
            "flat-factor-ablation",
        )
    }
    truth_origin_spectra = {
        scenario: evidence["spectrum"]
        for scenario, evidence in truth_origin_pose_rank_compatibility.items()
    }
    derivatives = derivative_checks(data)
    gauges = gauge_checks(data, truth_origin_spectra)
    held_out_ids = {
        item.observation_id
        for item in data.observations.values()
        if item.role == "held_out"
    }
    all_callbacks = [
        fixed_callback,
        axisymmetric_callback,
        asymmetric_callback,
        ablation_callback,
        balanced_callback,
        adequate_callback,
        uneven_callback,
        inadequate_callback,
        outlier_callback,
        active_bound_callback,
        mismatch_callback,
    ]
    callback_observations = set().union(
        *(callback.seen_observation_ids for callback in all_callbacks)
    )
    flat_scenario = data.scenarios["flat-factor-ablation"]
    flat_active_observations = {
        data.factors[item].observation_id for item in flat_scenario.active_factor_ids
    }
    flat_input_observations = set(flat_scenario.input_observation_ids)
    flat_observations = {
        item
        for item in flat_input_observations
        if data.records[item]["element_id"] == "plane.datum-flat"
    }
    derivative_probes = [
        probe for kind in derivatives["probes"].values() for probe in kind
    ]
    expected_elements = {
        "cylinder.band-1",
        "cylinder.band-2",
        "cylinder.band-3",
        "plane.datum-flat",
        "plane.station-0",
        "plane.station-20",
        "plane.station-50",
        "plane.station-80",
    }
    expected_element_counts = {
        "cylinder.band-1": 56,
        "cylinder.band-2": 48,
        "cylinder.band-3": 56,
        "plane.datum-flat": 16,
        "plane.station-0": 14,
        "plane.station-20": 14,
        "plane.station-50": 12,
        "plane.station-80": 14,
    }
    derivative_active_ids = data.scenarios["asymmetric-full-pose"].active_factor_ids
    derivative_active_ids_sha256 = sha256(canonical_json(list(derivative_active_ids)))
    gauge_scenarios = ("axisymmetric-free-roll", "flat-factor-ablation")
    gauge_equivalence_passed = all(
        free_roll_gauge_acceptable(gauges[item]) for item in gauge_scenarios
    )
    asymmetric_gauge = gauges["asymmetric-full-pose"]

    nominal_points = local_points(data)
    balanced_points = local_points(data, noise_offsets)
    evaluator_factors = active_factors(data, "evaluator-oracle")
    scenario_results = {
        "evaluator-oracle": {
            "active_factor_count": len(evaluator_factors),
            "callbacks": {
                "callback_calls": 0,
                "callback_invocation_count": 0,
                "jacobian_calls": 0,
                "held_out_seen": [],
            },
            "diagnostics": {"classification": "oracle"},
            "raw": raw_shape_evidence(evaluator_factors, nominal_points, SHAPE_TRUTH),
            "solver": {"invoked": False},
            "support": support_evidence(evaluator_factors, nominal_points, SHAPE_TRUTH),
            "termination": {"class": "not-applicable"},
        },
        "noiseless-fixed-pose": solved_shape_scenario(
            data,
            "noiseless-fixed-pose",
            fixed,
            fixed_callback,
            nominal_points,
            diagnostics={"classification": "nominal"},
        ),
        "axisymmetric-free-roll": pose_scenario_result(
            data,
            "axisymmetric-free-roll",
            axisymmetric,
            axisymmetric_callback,
        ),
        "asymmetric-full-pose": pose_scenario_result(
            data,
            "asymmetric-full-pose",
            asymmetric,
            asymmetric_callback,
        ),
        "flat-factor-ablation": pose_scenario_result(
            data,
            "flat-factor-ablation",
            ablation,
            ablation_callback,
        ),
        "coherent-held-out-strips-sectors": {
            "active_factor_count": len(
                data.scenarios["coherent-held-out-strips-sectors"].active_factor_ids
            ),
            "callbacks": callback_audit(data, fixed_callback),
            "diagnostics": {"classification": "held-out-consistent"},
            "raw": held_out,
            "solver": {
                key: value
                for key, value in fixed.items()
                if key not in {"message", "nfev", "status", "success"}
            },
            "support": {"failures": held_out["support_failures"]},
            "termination": {
                key: fixed[key] for key in ("message", "nfev", "status", "success")
            },
        },
        "coverage-adequate": solved_shape_scenario(
            data,
            "coverage-adequate",
            adequate,
            adequate_callback,
            nominal_points,
            diagnostics=coverage_results["coverage-adequate"],
        ),
        "coverage-uneven": solved_shape_scenario(
            data,
            "coverage-uneven",
            uneven,
            uneven_callback,
            nominal_points,
            diagnostics=coverage_results["coverage-uneven"],
        ),
        "coverage-inadequate": solved_shape_scenario(
            data,
            "coverage-inadequate",
            inadequate,
            inadequate_callback,
            nominal_points,
            diagnostics=coverage_results["coverage-inadequate"],
        ),
        "balanced-normal-noise": solved_shape_scenario(
            data,
            "balanced-normal-noise",
            balanced,
            balanced_callback,
            balanced_points,
            diagnostics={
                "classification": "balanced-normal-noise",
                "offset_count": len(noise_offsets),
            },
        ),
        "fixed-outliers": solved_shape_scenario(
            data,
            "fixed-outliers",
            outliers,
            outlier_callback,
            outlier_points,
            diagnostics={
                "classification": "outlier-contaminated",
                "linear_loss_control": True,
                "outlier_offsets_m": outlier_offsets,
                **outlier_application,
                "truth_nonzero_residual_count": 3,
                "truth_residuals_sha256": sha256(
                    canonical_json(outlier_truth_residuals.tolist())
                ),
                "robust_comparison": "open: loss, scale, and disposition not frozen",
            },
        ),
        "corrupted-mapping": rejected_corrupted_mapping(data),
        "legal-active-bound": solved_shape_scenario(
            data,
            "legal-active-bound",
            active_bound,
            active_bound_callback,
            nominal_points,
            diagnostics={
                "classification": "expected-active",
                **active_bound_diagnostics,
            },
        ),
        "invalid-geometry-declaration": rejected_invalid_geometry(data),
        "out-of-contract-mismatch": solved_shape_scenario(
            data,
            "out-of-contract-mismatch",
            mismatch,
            mismatch_callback,
            mismatch_points,
            diagnostics=mismatch_diagnostics,
            support_shape=SHAPE_TRUTH,
        ),
    }

    scenario_results["evaluator-oracle"]["disposition"] = (
        binary_fixture_acceptance_policy(
            not oracle["support_failures"],
            oracle["max_abs_raw_residual_m"] <= ORACLE_TOLERANCE,
        )
    )
    scenario_results["noiseless-fixed-pose"]["disposition"] = (
        binary_fixture_acceptance_policy(
            fixed["success"],
            fixed["max_abs_error"] <= RECOVERY_TOLERANCE,
            not scenario_results["noiseless-fixed-pose"]["support"]["failures"],
        )
    )
    for scenario_id, solution, expected_rank, require_roll in (
        ("axisymmetric-free-roll", axisymmetric, 5, False),
        ("asymmetric-full-pose", asymmetric, 6, True),
        ("flat-factor-ablation", ablation, 5, False),
    ):
        result = scenario_results[scenario_id]
        result["disposition"] = binary_fixture_acceptance_policy(
            solution["success"],
            result["raw"]["spectrum"]["raw_rank"] == expected_rank,
            solution["max_abs_nonroll_error"] <= RECOVERY_TOLERANCE,
            not require_roll or abs(solution["roll_rad"]) <= RECOVERY_TOLERANCE,
            not result["support"]["failures"],
        )
    scenario_results["coherent-held-out-strips-sectors"]["disposition"] = (
        binary_fixture_acceptance_policy(held_out_oracle_acceptable(held_out))
    )
    for scenario_id in (
        "coverage-adequate",
        "coverage-uneven",
        "coverage-inadequate",
    ):
        scenario_results[scenario_id]["disposition"] = coverage_acceptance_policy(
            scenario_results[scenario_id]["diagnostics"]
        )
    scenario_results["balanced-normal-noise"]["disposition"] = (
        binary_fixture_acceptance_policy(
            balanced["success"],
            balanced["max_abs_error"] <= RECOVERY_TOLERANCE,
            len(noise_offsets) == 454,
            not scenario_results["balanced-normal-noise"]["support"]["failures"],
        )
    )
    scenario_results["fixed-outliers"]["disposition"] = (
        fixed_outlier_acceptance_policy()
    )
    scenario_results["corrupted-mapping"]["disposition"] = (
        corrupted_mapping_acceptance_policy(scenario_results["corrupted-mapping"])
    )
    scenario_results["legal-active-bound"]["disposition"] = (
        active_bound_acceptance_policy(scenario_results["legal-active-bound"])
    )
    scenario_results["invalid-geometry-declaration"]["disposition"] = (
        invalid_geometry_acceptance_policy(
            scenario_results["invalid-geometry-declaration"]
        )
    )
    scenario_results["out-of-contract-mismatch"]["disposition"] = (
        mismatch_acceptance_policy(scenario_results["out-of-contract-mismatch"])
    )

    checks = {
        "active_factor_projection_exact": all(
            tuple(
                factor.factor_id
                for factor in active_factors(data, scenario.scenario_id)
            )
            == scenario.active_factor_ids
            for scenario in data.scenarios.values()
        ),
        "analytic_directional_jacobians_all_points": all(
            item["directional_max_abs_error"] <= DERIVATIVE_ABS_TOL
            for item in derivative_probes
        ),
        "analytic_jacobians_all_points": all(
            item["max_abs_error"] <= DERIVATIVE_ABS_TOL
            and item["max_dimensionless_abs_error"] <= DERIVATIVE_DIMENSIONLESS_ABS_TOL
            and item["max_relative_error_above_floor"] <= DERIVATIVE_REL_TOL
            for item in derivative_probes
        ),
        "derivative_element_coverage_all_points": all(
            set(item["factor_element_counts"]) == expected_elements
            and item["factor_element_counts"] == expected_element_counts
            for item in derivative_probes
        ),
        "raw_derivative_probes_use_all_active_factors": all(
            item["raw_factor_count"]
            == item["all_active_factor_count"]
            == len(derivative_active_ids)
            == 230
            and item["active_factor_ids_sha256"] == derivative_active_ids_sha256
            for item in derivative_probes
        ),
        "callback_domain_stencils_use_all_active_factors": all(
            item["active_factor_count"] == len(derivative_active_ids) == 230
            and item["active_factor_ids_sha256"] == derivative_active_ids_sha256
            and item["stencil_candidate_count"] == 1 + 6 * item["coordinate_count"] + 6
            and item["unique_stencil_candidate_count"]
            == item["stencil_candidate_count"]
            and item["callback_evaluation_count"] == 2 * item["stencil_candidate_count"]
            and item["factor_element_counts"] == expected_element_counts
            and item["factor_element_counts_exact_every_candidate"]
            and item["ordered_factor_ids_exact_active_every_evaluation"]
            and item["ordered_factor_ids_sha256_every_evaluation"]
            == derivative_active_ids_sha256
            and item["row_count_every_evaluation"] == len(derivative_active_ids)
            for item in derivatives["callback_domain_probes"]
        ),
        "analytic_solver_jacobians_used": all(
            callback.jacobian_calls > 0 for callback in all_callbacks
        ),
        "asymmetric_pose_recovery": asymmetric["success"]
        and asymmetric["max_abs_nonroll_error"] <= RECOVERY_TOLERANCE
        and abs(asymmetric["roll_rad"]) <= RECOVERY_TOLERANCE,
        "balanced_noise_behavior": balanced["success"]
        and balanced["max_abs_error"] <= RECOVERY_TOLERANCE
        and math.isfinite(balanced["max_abs_residual_m"])
        and len(noise_offsets) == 454
        and balanced_callback.calls > 0
        and balanced_callback.jacobian_calls > 0,
        "coverage_adequate": len(adequate["estimate"]) == 7
        and adequate["active_factor_count"] == 454
        and adequate["max_abs_error"] <= RECOVERY_TOLERANCE
        and scenario_results["coverage-adequate"]["raw"]["spectrum"]["raw_rank"] == 7
        and coverage_results["coverage-adequate"]["covered_mapping_count"] == 15
        and not coverage_results["coverage-adequate"]["missing_required_mappings"]
        and not coverage_results["coverage-adequate"]["missing_coverage_cells"]
        and scenario_results["coverage-adequate"]["disposition"] == "passed",
        "coverage_uneven": uneven["active_factor_count"] == 163
        and uneven["max_abs_error"] <= RECOVERY_TOLERANCE
        and scenario_results["coverage-uneven"]["raw"]["spectrum"]["raw_rank"] == 7
        and coverage_results["coverage-uneven"]["covered_mapping_count"] == 15
        and not coverage_results["coverage-uneven"]["missing_required_mappings"]
        and bool(coverage_results["coverage-uneven"]["missing_coverage_cells"])
        and scenario_results["coverage-uneven"]["disposition"] == "review-required",
        "coverage_inadequate": inadequate["active_factor_count"] == 112
        and scenario_results["coverage-inadequate"]["raw"]["spectrum"]["raw_rank"] == 1
        and bool(coverage_results["coverage-inadequate"]["missing_required_mappings"])
        and bool(coverage_results["coverage-inadequate"]["missing_coverage_cells"])
        and inadequate["success"]
        and scenario_results["coverage-inadequate"]["disposition"] == "failed",
        "fixed_outlier_linear_control": outliers["active_factor_count"] == 454
        and outliers["success"]
        and outlier_callback.calls > 0
        and outlier_callback.jacobian_calls > 0
        and scenario_results["fixed-outliers"]["raw"]["spectrum"]["raw_rank"] == 7
        and outlier_offsets
        == {
            "obs.asymmetric_datum_flat.cylinder.band-1.z00.a09": 0.002,
            "obs.asymmetric_datum_flat.cylinder.band-1.z03.a05": -0.0015,
            "obs.asymmetric_datum_flat.cylinder.band-2.z00.a10": 0.0025,
        }
        and outlier_application["changed_observation_count"] == 3
        and outlier_application["unchanged_observation_count"] == 618
        and int(np.count_nonzero(np.abs(outlier_truth_residuals) > ORACLE_TOLERANCE))
        == 3
        and scenario_results["fixed-outliers"]["disposition"]
        == DISPOSITION_UNCLASSIFIED,
        "corrupted_mapping_rejected": scenario_results["corrupted-mapping"][
            "diagnostics"
        ]["classification"]
        == "mapping-suspect"
        and scenario_results["corrupted-mapping"]["support"]["factor_traversal_count"]
        == 1
        and scenario_results["corrupted-mapping"]["callbacks"]["callback_calls"] == 0
        and scenario_results["corrupted-mapping"]["diagnostics"][
            "support_attempt_mapping_ids"
        ]
        == ["mapping.axisymmetric.cylinder.band-3"]
        and scenario_results["corrupted-mapping"]["diagnostics"][
            "retarget_attempt_count"
        ]
        == 0
        and not scenario_results["corrupted-mapping"]["solver"]["invoked"]
        and scenario_results["corrupted-mapping"]["disposition"] == DISPOSITION_FAILED,
        "coverage_reference_independent": coverage_reference_evidence["factor_count"]
        == 454
        and coverage_reference_evidence["mapping_count"] == 15
        and all(
            result["reference_factor_ids_sha256"]
            == coverage_reference_evidence["factor_ids_sha256"]
            and result["reference_mapping_cell_counts_sha256"]
            == coverage_reference_evidence["mapping_cell_counts_sha256"]
            for result in coverage_results.values()
        ),
        "factor_count_frozen": len(data.factors) == 454,
        "flat_ablation_retains_inputs": bool(flat_observations)
        and flat_observations.isdisjoint(flat_active_observations),
        "held_out_absent_from_callbacks": not (held_out_ids & callback_observations),
        "held_out_absent_from_factors": held_out_ids.isdisjoint(
            factor.observation_id for factor in data.factors.values()
        ),
        "held_out_oracle_separate": held_out_oracle_acceptable(held_out),
        "invalid_geometry_scenario_rejected": scenario_results[
            "invalid-geometry-declaration"
        ]["diagnostics"]["message"]
        == "radii must be positive"
        and scenario_results["invalid-geometry-declaration"]["support"][
            "factor_traversal_count"
        ]
        == 0
        and scenario_results["invalid-geometry-declaration"]["callbacks"][
            "callback_calls"
        ]
        == 0
        and not scenario_results["invalid-geometry-declaration"]["solver"]["invoked"]
        and scenario_results["invalid-geometry-declaration"]["disposition"]
        == DISPOSITION_FAILED,
        "legal_active_bound": active_bound["success"]
        and active_bound["active_mask"] == [-1, 0, 0, 0, 0, 0, 0]
        and 0.0
        <= active_bound_diagnostics["distance_to_lower_bounds_m"][0]
        <= active_bound_diagnostics["active_bound_tolerance_m"]
        and all(
            math.isfinite(value) and value >= 0.0
            for value in active_bound_diagnostics["distance_to_lower_bounds_m"]
            + active_bound_diagnostics["distance_to_upper_bounds_m"]
        )
        and active_bound_diagnostics["declared_parameter"] == "radius.band-1"
        and active_bound_diagnostics["declared_bound_m"] == SHAPE_TRUTH[0]
        and active_bound_diagnostics["raw_rank"] == 7
        and active_bound_diagnostics["feasible_tangent_rank"] == 6
        and active_bound_diagnostics["geometry_valid"]
        and active_bound_diagnostics["initialization_valid"]
        and scenario_results["legal-active-bound"]["disposition"] == DISPOSITION_PASSED,
        "noiseless_fixed_pose_recovery": fixed["success"]
        and fixed["max_abs_error"] <= RECOVERY_TOLERANCE
        and math.isfinite(fixed["max_abs_residual_m"])
        and fixed_callback.calls > 0
        and fixed_callback.jacobian_calls > 0
        and scenario_results["noiseless-fixed-pose"]["raw"]["spectrum"]["raw_rank"]
        == 7,
        "oracle_raw_residuals": oracle["max_abs_raw_residual_m"] <= ORACLE_TOLERANCE,
        "oracle_supports": not oracle["support_failures"],
        "oracle_invalid_cases_rejected": oracle["invalid_geometry_rejected"]
        and oracle["out_of_support_rejected"],
        "rank_and_gauge_5_6_5": [
            truth_origin_spectra[item]["raw_rank"] for item in truth_origin_spectra
        ]
        == [5, 6, 5],
        "dimensionless_rank_5_6_5": [
            truth_origin_spectra[item]["dimensionless_rank"]
            for item in truth_origin_spectra
        ]
        == [5, 6, 5],
        "recovered_pose_rank_bindings": all(
            result["raw"]["evaluation_coordinates"]
            == result["solver"]["estimate_local_observation_to_model"]
            and result["raw"]["evaluation_coordinates_sha256"]
            == result["raw"]["jacobian_evaluation_coordinates_sha256"]
            and result["raw"]["jacobian_sha256"]
            == result["raw"]["spectrum_jacobian_sha256"]
            for result in (
                scenario_results["axisymmetric-free-roll"],
                scenario_results["asymmetric-full-pose"],
                scenario_results["flat-factor-ablation"],
            )
        ),
        "roll_gauge_equivalence_and_null_alignment": gauge_equivalence_passed,
        "datum_flat_restores_roll_observability": asymmetric_datum_gauge_acceptable(
            asymmetric_gauge, truth_origin_spectra["asymmetric-full-pose"]
        ),
        "out_of_contract_mismatch_detected": mismatch["success"]
        and mismatch_diagnostics["analytic_mismatch_nonzero"]
        and mismatch_diagnostics["matches_analytic_least_squares_expectation"]
        and mismatch_diagnostics["fitted_middle_radius_error_m"]
        <= MISMATCH_ANALYTIC_TOLERANCE_M
        and mismatch_diagnostics["middle_residual_vector_max_abs_error_m"]
        <= MISMATCH_ANALYTIC_TOLERANCE_M
        and mismatch_diagnostics["classification"] == "model-mismatch-suspect"
        and scenario_results["out-of-contract-mismatch"]["disposition"]
        == DISPOSITION_REVIEW_REQUIRED,
        "scenario_execution_complete": set(scenario_results) == set(data.scenarios)
        and len(scenario_results) == 15,
        "termination_disposition_separate": inadequate["success"]
        and scenario_results["coverage-inadequate"]["termination"]["success"]
        and scenario_results["coverage-inadequate"]["disposition"] == "failed"
        and "disposition" not in coverage_results["coverage-inadequate"]
        and all(
            key not in scenario_results["coverage-inadequate"]["solver"]
            for key in ("message", "nfev", "status", "success")
        ),
    }
    if not all(checks.values()):
        fail(
            f"gate checks failed: {[name for name, passed in checks.items() if not passed]}"
        )

    report = {
        "acceptance_policy": {
            "active_bound_tolerance_m": ACTIVE_BOUND_TOLERANCE_M,
            "coverage_reference": coverage_reference_evidence,
            "dispositions": [
                DISPOSITION_PASSED,
                DISPOSITION_REVIEW_REQUIRED,
                DISPOSITION_FAILED,
                DISPOSITION_UNCLASSIFIED,
            ],
            "fixed_outliers": "no approved robustness or product threshold; execution remains unclassified",
            "mismatch_analytic_tolerance_m": MISMATCH_ANALYTIC_TOLERANCE_M,
            "mismatch_nonzero_tolerance_m": MISMATCH_NONZERO_TOLERANCE_M,
            "scope": "runner-local preregistered frozen-scenario policy; non-public-contract",
        },
        "checks": checks,
        "contract": {
            "contract": "stepped-rotational-v1",
            "contract_logical_sha256": CONTRACT_SHA256,
            "generator_source_sha256": GENERATOR_SHA256,
            "generator_version": "1.0.5",
            "scope": FORMAT_STATUS,
        },
        "data_separation": {
            "active_factors_selected_only_by": "scenario.active_factor_ids",
            "factor_count": len(data.factors),
            "held_out_count": len(held_out_ids),
            "mapping_count": len(data.mappings),
            "membership_count": sum(
                len(item.memberships) for item in data.observations.values()
            ),
            "observation_count": len(data.observations),
            "scenario_count": len(data.scenarios),
            "training_count": sum(
                item.role == "train" for item in data.observations.values()
            ),
        },
        "derivatives": derivatives,
        "declared_evidence_boundaries": {
            "cad_evidence_consumed": False,
            "classification": "declared experiment input/provenance boundary; not established by executable checks",
            "generated_held_out_used_for_fit_or_tuning": False,
            "generated_truth": "fit and evaluator oracle only",
            "physical_reference_consumed": False,
            "supports_physical_or_product_claims": False,
        },
        "format": FORMAT,
        "format_status": FORMAT_STATUS,
        "oracle": oracle,
        "pose_convention": {
            "coordinates": "local observation-to-model increment [translation_m, rotation_vector_rad] about generator truth",
            "raw_truth_jacobian": "[g^T, (q cross g)^T]",
            "runner": "scipy.optimize.least_squares(method='trf', loss='linear')",
            "scope": "truth-centered local recovery fixture; not a reusable global pose initializer",
        },
        "truth_origin_pose_rank_compatibility": truth_origin_pose_rank_compatibility,
        "gauge_evidence": gauges,
        "runtime": {
            "implementation": sys.implementation.name,
            "numpy": np.__version__,
            "policy": "exact CPython runtime required for retained evidence equality",
            "python": verify_runtime(),
            "scipy": scipy.__version__,
        },
        "scenarios": scenario_results,
        "solver_source_sha256": tool_sha,
        "solver_settings": {
            "bounds": {
                "pose_lower": POSE_LOWER.tolist(),
                "pose_upper": POSE_UPPER.tolist(),
                "shape_lower": SHAPE_LOWER.tolist(),
                "shape_upper": SHAPE_UPPER.tolist(),
            },
            "loss": "linear",
            "method": "trf",
            "provisional": True,
            "robust_probe_executed": False,
            "scenario_overrides": {
                "legal-active-bound": {
                    "shape_lower": active_lower.tolist(),
                    "shape_initial": active_initial.tolist(),
                }
            },
        },
        "trace": {
            "callback_audits": [
                callback_audit(data, callback) for callback in all_callbacks
            ],
            "flat_ablation_active_factor_count": len(flat_scenario.active_factor_ids),
            "flat_ablation_input_count": len(flat_scenario.input_observation_ids),
            "flat_observation_count": len(flat_observations),
        },
    }
    return report


def verify_evidence(path: Path) -> dict[str, Any]:
    verify_source()
    data = path.read_bytes()
    try:
        value = json.loads(
            data.decode("ascii"),
            object_pairs_hook=unique_json_object,
            parse_constant=lambda item: fail(f"non-finite JSON: {item}"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateError(f"invalid evidence JSON: {error}") from error
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum, filename = (
        checksum_path.read_text(encoding="ascii").strip().split(maxsplit=1)
    )
    if filename != path.name or checksum != sha256(data):
        fail("evidence checksum mismatch")
    expected = run_gate()
    if value != expected:
        fail("retained evidence does not match recomputed gate")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify-evidence")
    verify.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            report = run_gate()
            data = canonical_json(report)
            args.output.write_bytes(data)
            args.output.with_suffix(args.output.suffix + ".sha256").write_text(
                f"{sha256(data)}  {args.output.name}\n", encoding="ascii"
            )
            print(f"gate: PASS ({len(report['checks'])} checks)")
            return 0
        report = verify_evidence(args.evidence)
        print(f"evidence: PASS ({len(report['checks'])} checks)")
        return 0
    except (GateError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
