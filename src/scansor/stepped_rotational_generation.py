from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Literal

from scansor.factor_models import NOMINAL_SHAPE
from scansor.generation_models import (
    GenerationGroundTruth,
    GenerationProvenance,
    GenerationRequest,
    GenerationRowProvenance,
    GenerationSource,
    GenerationTruthRow,
    ModelTruth,
    NoiseSummary,
    PartitionRecord,
    PreparedGeneration,
)
from scansor.serialization import canonical_json, sha256
from scansor.synthetic_fixture import FIXTURE_ID

_GENERATOR_REVISION = "provisional-1"
_CLIP_SIGMA = 4.0
_QUANTUM_M = 1e-9


@dataclass(frozen=True)
class _Sample:
    element_id: str
    key: str
    normal: tuple[float, float, float]
    point: tuple[float, float, float]
    role: Literal["training", "held-out"]


def _fixture_id(sample: _Sample) -> str:
    payload = "\0".join(
        (
            FIXTURE_ID,
            "2",
            "asymmetric-datum-flat",
            "guarded-grid-v1",
            sample.element_id,
            sample.key,
            sample.role,
        )
    ).encode("ascii")
    return f"fixture-observation.{hashlib.sha256(payload).hexdigest()[:24]}"


def _angles(count: int) -> tuple[tuple[int, float], ...]:
    return tuple(
        (index, (2.0 * math.pi * index / count) + math.pi / count)
        for index in range(count)
    )


def _samples() -> tuple[_Sample, ...]:
    samples: list[_Sample] = []
    radii = (0.012, 0.018, 0.014)
    stations = (0.0, 0.020, 0.050, 0.080)
    for band, radius in enumerate(radii):
        for z_index in range(5):
            z = (
                stations[band]
                + 0.002
                + ((stations[band + 1] - stations[band] - 0.004) * z_index / 4)
            )
            for angle_index, angle in _angles(16):
                x = radius * math.cos(angle)
                if band == 1 and x > 0.016 - 0.001:
                    continue
                y = radius * math.sin(angle)
                role = (
                    "held-out" if z_index == 2 or angle_index in {3, 4} else "training"
                )
                samples.append(
                    _Sample(
                        element_id=f"cylinder.band-{band + 1}",
                        key=f"z{z_index:02d}.a{angle_index:02d}",
                        normal=(math.cos(angle), math.sin(angle), 0.0),
                        point=(x, y, z),
                        role=role,
                    )
                )

    plane_specs = (
        ("plane.station-0", 0.0, 0.0, 0.012 - 0.001, -1.0),
        ("plane.station-20", 0.020, 0.012 + 0.001, 0.018 - 0.001, -1.0),
        ("plane.station-50", 0.050, 0.014 + 0.001, 0.018 - 0.001, 1.0),
        ("plane.station-80", 0.080, 0.0, 0.014 - 0.001, 1.0),
    )
    for element_id, z, radial_min, radial_max, normal_z in plane_specs:
        radial_values = (
            (radial_max * 0.45, radial_max * 0.75)
            if radial_min == 0.0
            else (
                radial_min + (radial_max - radial_min) * 0.35,
                radial_min + (radial_max - radial_min) * 0.70,
            )
        )
        for radial_index, radius in enumerate(radial_values):
            for angle_index, angle in _angles(8):
                x = radius * math.cos(angle)
                if element_id in {"plane.station-20", "plane.station-50"} and (
                    x > 0.016 - 0.001
                ):
                    continue
                samples.append(
                    _Sample(
                        element_id=element_id,
                        key=f"r{radial_index:02d}.a{angle_index:02d}",
                        normal=(0.0, 0.0, normal_z),
                        point=(x, radius * math.sin(angle), z),
                        role="held-out" if angle_index == 2 else "training",
                    )
                )

    half_width = 0.008246211251235319
    for z_index in range(5):
        z = 0.021 + (0.028 * z_index / 4)
        for y_index in range(5):
            y = -half_width + 0.001 + ((2.0 * (half_width - 0.001)) * y_index / 4)
            samples.append(
                _Sample(
                    element_id="plane.datum-flat",
                    key=f"z{z_index:02d}.y{y_index:02d}",
                    normal=(1.0, 0.0, 0.0),
                    point=(0.016, y, z),
                    role=("held-out" if z_index == 2 or y_index == 2 else "training"),
                )
            )
    return tuple(sorted(samples, key=lambda item: (item.element_id, item.key)))


def _uniform(seed: int, role: str, fixture_id: str, attempt: int, index: int) -> float:
    payload = "\0".join(
        (
            _GENERATOR_REVISION,
            str(seed),
            role,
            fixture_id,
            str(attempt),
            str(index),
        )
    ).encode("ascii")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 11
    return (integer + 0.5) / 2**53


def _noise(seed: int, sigma: float, role: str, fixture_id: str) -> float:
    for attempt in range(1024):
        u1 = _uniform(seed, role, fixture_id, attempt, 0)
        u2 = _uniform(seed, role, fixture_id, attempt, 1)
        standard = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        if abs(standard) <= _CLIP_SIGMA:
            quantum_count = round((standard * sigma) / _QUANTUM_M)
            quantized = float(quantum_count) * _QUANTUM_M
            if abs(quantized) <= _CLIP_SIGMA * sigma:
                return quantized
    raise RuntimeError("bounded normal sampler exhausted its deterministic attempts")


def _ply(points: tuple[tuple[float, float, float], ...]) -> bytes:
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property double x\n"
        "property double y\n"
        "property double z\n"
        "end_header\n"
    ).encode("ascii")
    return header + b"".join(struct.pack("<ddd", *point) for point in points)


def _partition(
    role: str,
    rows: tuple[GenerationTruthRow, ...],
) -> PartitionRecord:
    selected = tuple(row for row in rows if row.role == role)
    identifiers = tuple(row.fixture_observation_id for row in selected)
    coordinates = tuple(row.generated_point_model_m for row in selected)
    return PartitionRecord(
        coordinate_sha256=sha256(canonical_json(coordinates)),
        count=len(selected),
        fixture_observation_ids_sha256=sha256(canonical_json(identifiers)),
    )


def prepare_generation(request: GenerationRequest) -> PreparedGeneration:
    samples = _samples()
    truth_rows: list[GenerationTruthRow] = []
    provenance_rows: list[GenerationRowProvenance] = []
    points: list[tuple[float, float, float]] = []
    offsets: list[float] = []
    for row_index, sample in enumerate(samples):
        fixture_observation_id = _fixture_id(sample)
        offset = _noise(
            request.seed,
            request.noise_sigma_m,
            sample.role,
            fixture_observation_id,
        )
        generated = (
            float(sample.point[0] + offset * sample.normal[0]),
            float(sample.point[1] + offset * sample.normal[1]),
            float(sample.point[2] + offset * sample.normal[2]),
        )
        points.append(generated)
        offsets.append(offset)
        provenance_rows.append(
            GenerationRowProvenance(
                fixture_observation_id=fixture_observation_id,
                role=sample.role,
                row_index=row_index,
            )
        )
        truth_rows.append(
            GenerationTruthRow(
                analytic_normal_model=sample.normal,
                expected_element_id=sample.element_id,
                fixture_observation_id=fixture_observation_id,
                generated_point_model_m=generated,
                noiseless_point_model_m=sample.point,
                normal_noise_offset_m=offset,
                role=sample.role,
                row_index=row_index,
            )
        )
    source = _ply(tuple(points))
    truth_tuple = tuple(truth_rows)
    held_out_rows = tuple(
        row.row_index for row in provenance_rows if row.role == "held-out"
    )
    base_semantic = {
        "generator_revision": _GENERATOR_REVISION,
        "noise_sigma_m": request.noise_sigma_m,
        "sampling_profile": request.sampling_profile,
        "seed": request.seed,
        "source_sha256": sha256(source),
        "variant": request.variant,
    }
    generation_run_id = sha256(canonical_json(base_semantic))
    provenance = GenerationProvenance(
        generation_run_id=generation_run_id,
        held_out_count=len(held_out_rows),
        held_out_row_indices=held_out_rows,
        noise_sigma_m=request.noise_sigma_m,
        partitions={
            "training": _partition("training", truth_tuple),
            "held-out": _partition("held-out", truth_tuple),
        },
        point_count=len(samples),
        rows=tuple(provenance_rows),
        seed=request.seed,
        source=GenerationSource(byte_count=len(source), sha256=sha256(source)),
        training_count=len(samples) - len(held_out_rows),
    )
    mean = math.fsum(offsets) / len(offsets)
    truth = GenerationGroundTruth(
        generation_run_id=generation_run_id,
        model_truth=ModelTruth(values=NOMINAL_SHAPE),
        noise_summary=NoiseSummary(
            maximum_offset_m=max(offsets),
            mean_offset_m=mean,
            minimum_offset_m=min(offsets),
            root_mean_square_offset_m=math.sqrt(
                math.fsum(value * value for value in offsets) / len(offsets)
            ),
        ),
        rows=truth_tuple,
    )
    return PreparedGeneration(ground_truth=truth, provenance=provenance, source=source)


def generated_fixture_provenance(prepared: PreparedGeneration, canonical_sha256: str):
    from scansor.mapping_models import (
        GeneratedFixtureRow,
        GeneratedSyntheticFixtureProvenance,
    )

    provenance = prepared.provenance
    semantic = {
        "canonical_sha256": canonical_sha256,
        "generation_run_id": provenance.generation_run_id,
        "generator_revision": provenance.generator_revision,
        "held_out_row_indices": provenance.held_out_row_indices,
        "noise_clip_sigma": provenance.noise_clip_sigma,
        "noise_model": provenance.noise_model,
        "noise_quantum_m": provenance.noise_quantum_m,
        "noise_sigma_m": provenance.noise_sigma_m,
        "outlier_policy": provenance.outlier_policy,
        "revision": "2",
        "rows": [row.model_dump(mode="json") for row in provenance.rows],
        "sampling_profile": provenance.sampling_profile,
        "seed": provenance.seed,
        "source_sha256": provenance.source.sha256,
        "variant": provenance.variant,
    }
    return GeneratedSyntheticFixtureProvenance(
        canonical_sha256=canonical_sha256,
        content_sha256=sha256(canonical_json(semantic)),
        generation_run_id=provenance.generation_run_id,
        held_out_row_indices=provenance.held_out_row_indices,
        noise_sigma_m=provenance.noise_sigma_m,
        rows=tuple(
            GeneratedFixtureRow(
                fixture_observation_id=row.fixture_observation_id,
                role=row.role,
                row_index=row.row_index,
            )
            for row in provenance.rows
        ),
        seed=provenance.seed,
        source_sha256=provenance.source.sha256,
    )
