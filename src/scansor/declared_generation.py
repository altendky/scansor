from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, Literal, cast

import numpy as np

from scansor.declared_generation_models import (
    DeclaredGeneratedFixtureProvenance,
    FixtureDefinition,
    FixtureSample,
    GenerationGroundTruth,
    GenerationProvenance,
    GenerationRequest,
    GenerationRowProvenance,
    GenerationSource,
    GenerationTruthRow,
    NoiseSummary,
    PartitionRecord,
    PreparedGeneration,
)
from scansor.declared_synthetic_fixtures import fixture_definition
from scansor.errors import ScansorError
from scansor.geometry_evaluator import DeclaredGeometryEvaluator
from scansor.serialization import canonical_json, sha256

_GENERATOR_REVISION = "provisional-1"
_CLIP_SIGMA = 4.0
_QUANTUM_M = 1e-9


def _request(
    definition: FixtureDefinition, *, seed: int, noise_sigma_m: float
) -> GenerationRequest:
    declaration = definition.declaration
    return GenerationRequest(
        declaration=declaration,
        element_ids=tuple(element.element_id for element in declaration.elements),
        fixture_id=definition.fixture_id,
        fixture_revision=definition.revision,
        model_id=declaration.model_id,
        noise_sigma_m=noise_sigma_m,
        parameter_order=tuple(
            parameter.parameter_id for parameter in declaration.parameters
        ),
        seed=seed,
        source_frame=definition.source_frame,
        transform=definition.transform,
    )


def create_generation_request(
    fixture_id: str, *, seed: int, noise_sigma_m: float
) -> GenerationRequest:
    return _request(
        fixture_definition(fixture_id), seed=seed, noise_sigma_m=noise_sigma_m
    )


def _fixture_observation_id(
    definition: FixtureDefinition, sample: FixtureSample
) -> str:
    semantic = {
        "element_id": sample.element_id,
        "fixture_id": definition.fixture_id,
        "fixture_revision": definition.revision,
        "key": sample.key,
        "model_id": definition.declaration.model_id,
        "role": sample.role,
        "sampling_profile": "guarded-grid-v1",
    }
    return f"fixture-observation.{sha256(canonical_json(semantic))[:24]}"


def _uniform(seed: int, role: str, fixture_id: str, attempt: int, index: int) -> float:
    payload = "\0".join(
        (_GENERATOR_REVISION, str(seed), role, fixture_id, str(attempt), str(index))
    ).encode("ascii")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 11
    return (integer + 0.5) / 2**53


def _noise(seed: int, sigma: float, role: str, fixture_id: str) -> float:
    for attempt in range(1024):
        u1 = _uniform(seed, role, fixture_id, attempt, 0)
        u2 = _uniform(seed, role, fixture_id, attempt, 1)
        standard = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        if abs(standard) <= _CLIP_SIGMA:
            quantized = float(round(standard * sigma / _QUANTUM_M)) * _QUANTUM_M
            if abs(quantized) <= _CLIP_SIGMA * sigma:
                return quantized
    raise ScansorError("bounded normal sampler exhausted its deterministic attempts")


def _ply(points: tuple[tuple[float, float, float], ...]) -> bytes:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property double x\nproperty double y\nproperty double z\nend_header\n"
    ).encode("ascii")
    return header + b"".join(struct.pack("<ddd", *point) for point in points)


def _partition(
    role: Literal["training", "held-out"], rows: tuple[GenerationTruthRow, ...]
) -> PartitionRecord:
    selected = tuple(row for row in rows if row.role == role)
    return PartitionRecord(
        coordinate_sha256=sha256(
            canonical_json(tuple(row.generated_point_model_m for row in selected))
        ),
        count=len(selected),
        fixture_observation_ids_sha256=sha256(
            canonical_json(tuple(row.fixture_observation_id for row in selected))
        ),
    )


def prepare_generation(request: GenerationRequest) -> PreparedGeneration:
    """Regenerate only an exact project-owned declaration and sampling definition."""
    request = GenerationRequest.model_validate(request.model_dump(mode="python"))
    definition = fixture_definition(request.fixture_id)
    expected_request = _request(
        definition, seed=request.seed, noise_sigma_m=request.noise_sigma_m
    )
    if canonical_json(request) != canonical_json(expected_request):
        raise ScansorError(
            "generation request does not match the designated fixture declaration and pose"
        )
    nominal = tuple(parameter.nominal for parameter in request.declaration.parameters)
    evaluator = DeclaredGeometryEvaluator(request.declaration, nominal)
    rotation = np.asarray(request.transform.rotation, dtype=np.float64)
    translation = np.asarray(request.transform.translation_m, dtype=np.float64)
    provenance_rows: list[GenerationRowProvenance] = []
    truth_rows: list[GenerationTruthRow] = []
    points: list[tuple[float, float, float]] = []
    for index, sample in enumerate(definition.samples):
        support = evaluator.classify_support(sample.element_id, sample.point_model_m)
        differential = evaluator.evaluate_fixed_pose_shape(
            sample.element_id, sample.point_model_m
        )
        if (
            not support.projected_inside
            or abs(differential.residual_m) > 1e-12
            or not np.allclose(
                differential.point_gradient, sample.normal_model, rtol=0.0, atol=1e-12
            )
        ):
            raise ScansorError(
                "fixture sample disagrees with its declared analytic support or orientation"
            )
        fixture_id = _fixture_observation_id(definition, sample)
        offset = _noise(request.seed, request.noise_sigma_m, sample.role, fixture_id)
        point_model = np.asarray(sample.point_model_m) + offset * np.asarray(
            sample.normal_model
        )
        point_observation = rotation.T @ (point_model - translation)
        points.append(
            (
                float(point_observation[0]),
                float(point_observation[1]),
                float(point_observation[2]),
            )
        )
        row = GenerationRowProvenance(
            expected_element_id=sample.element_id,
            fixture_observation_id=fixture_id,
            role=sample.role,
            row_index=index,
        )
        provenance_rows.append(row)
        truth_rows.append(
            GenerationTruthRow(
                **row.model_dump(mode="python"),
                analytic_normal_model=sample.normal_model,
                generated_point_model_m=(
                    float(point_model[0]),
                    float(point_model[1]),
                    float(point_model[2]),
                ),
                noiseless_point_model_m=sample.point_model_m,
                normal_noise_offset_m=offset,
            )
        )
    source = _ply(tuple(points))
    rows = tuple(truth_rows)
    semantic: dict[str, object] = {
        "held_out_row_indices": tuple(
            row.row_index for row in rows if row.role == "held-out"
        ),
        "model_id": request.model_id,
        "partitions": {
            role: _partition(role, rows) for role in ("training", "held-out")
        },
        "request": request,
        "rows": tuple(provenance_rows),
        "source": GenerationSource(
            byte_count=len(source), frame=request.source_frame, sha256=sha256(source)
        ),
    }
    provisional = GenerationProvenance.model_construct(
        generation_run_id="", **cast(dict[str, Any], semantic)
    )
    semantic["generation_run_id"] = sha256(
        canonical_json(
            provisional.model_dump(mode="json", exclude={"generation_run_id"})
        )
    )
    provenance = GenerationProvenance.model_validate(semantic)
    offsets = tuple(row.normal_noise_offset_m for row in rows)
    truth = GenerationGroundTruth(
        declaration=request.declaration,
        element_ids=request.element_ids,
        generation_run_id=provenance.generation_run_id,
        model_id=request.model_id,
        noise_summary=NoiseSummary(
            maximum_offset_m=max(offsets),
            mean_offset_m=math.fsum(offsets) / len(offsets),
            minimum_offset_m=min(offsets),
            root_mean_square_offset_m=math.sqrt(
                math.fsum(value * value for value in offsets) / len(offsets)
            ),
        ),
        parameter_order=request.parameter_order,
        parameter_values=nominal,
        rows=rows,
        source_sha256=provenance.source.sha256,
    )
    return PreparedGeneration(ground_truth=truth, provenance=provenance, source=source)


def generated_fixture_provenance(
    prepared: PreparedGeneration, canonical_sha256: str
) -> DeclaredGeneratedFixtureProvenance:
    prepared = PreparedGeneration.model_validate(prepared.model_dump(mode="python"))
    semantic: dict[str, object] = {
        "canonical_sha256": canonical_sha256,
        "generation": prepared.provenance,
        "model_id": prepared.provenance.model_id,
        "source_sha256": prepared.provenance.source.sha256,
    }
    provisional = DeclaredGeneratedFixtureProvenance.model_construct(
        content_sha256="", **cast(dict[str, Any], semantic)
    )
    semantic["content_sha256"] = sha256(
        canonical_json(provisional.model_dump(mode="json", exclude={"content_sha256"}))
    )
    return DeclaredGeneratedFixtureProvenance.model_validate(semantic)
