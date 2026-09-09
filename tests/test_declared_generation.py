from __future__ import annotations

import copy

import numpy as np
import pytest
from pydantic import ValidationError

from scansor.declared_generation import (
    create_generation_request,
    generated_fixture_provenance,
    prepare_generation,
)
from scansor.declared_generation_models import (
    DeclaredGeneratedFixtureProvenance,
    GenerationGroundTruth,
    GenerationRequest,
)
from scansor.errors import ScansorError
from scansor.geometry_evaluator import DeclaredGeometryEvaluator
from scansor.model_declarations import ModelSemanticDeclaration, identify_model
from scansor.ply import canonical_npy, parse_ply
from scansor.serialization import canonical_json, sha256


@pytest.mark.parametrize("fixture_id", ["asymmetric-stepped-v1", "coaxial-tube-v1"])
def test_generation_replays_model_bound_xyz_and_oriented_noise(fixture_id: str) -> None:
    request = create_generation_request(fixture_id, seed=7, noise_sigma_m=25e-6)
    first = prepare_generation(request)
    assert (
        prepare_generation(
            GenerationRequest.model_validate(request.model_dump(mode="json"))
        )
        == first
    )
    assert first.provenance.request == request
    assert first.ground_truth.declaration == request.declaration
    assert first.provenance.model_id == first.ground_truth.model_id == request.model_id
    assert first.ground_truth.parameter_order == request.parameter_order
    assert first.ground_truth.element_ids == request.element_ids
    assert {row.expected_element_id for row in first.provenance.rows} == set(
        request.element_ids
    )
    parsed = parse_ply(first.source, "m", 65_536, len(first.provenance.rows))
    canonical = canonical_npy(parsed.canonical)
    provenance = generated_fixture_provenance(first, sha256(canonical))
    assert (
        DeclaredGeneratedFixtureProvenance.model_validate(
            provenance.model_dump(mode="json")
        )
        == provenance
    )
    rotation = np.asarray(request.transform.rotation)
    translation = np.asarray(request.transform.translation_m)
    evaluator = DeclaredGeometryEvaluator(
        request.declaration, first.ground_truth.parameter_values
    )
    for row in first.ground_truth.rows:
        assert abs(row.normal_noise_offset_m) <= 4.0 * request.noise_sigma_m
        assert row.normal_noise_offset_m / 1e-9 == pytest.approx(
            round(row.normal_noise_offset_m / 1e-9)
        )
        assert row.role == first.provenance.rows[row.row_index].role
        point_observation = np.asarray(
            [parsed.canonical[name][row.row_index] for name in ("x_m", "y_m", "z_m")]
        )
        assert rotation @ point_observation + translation == pytest.approx(
            row.generated_point_model_m, abs=1e-16
        )
        evaluated = evaluator.evaluate_fixed_pose_shape(
            row.expected_element_id, row.generated_point_model_m
        )
        assert evaluated.residual_m == pytest.approx(
            row.normal_noise_offset_m, abs=1e-16
        )
    second = prepare_generation(
        create_generation_request(fixture_id, seed=11, noise_sigma_m=25e-6)
    )
    assert first.source != second.source
    assert first.provenance.generation_run_id != second.provenance.generation_run_id
    assert first.provenance.rows == second.provenance.rows
    assert (
        first.provenance.held_out_row_indices == second.provenance.held_out_row_indices
    )
    assert request.declaration == second.provenance.request.declaration


def test_materially_different_fixture_inventories_and_policies() -> None:
    stepped = create_generation_request(
        "asymmetric-stepped-v1", seed=7, noise_sigma_m=20e-6
    )
    tube = create_generation_request("coaxial-tube-v1", seed=7, noise_sigma_m=20e-6)
    assert len(stepped.parameter_order) == 7
    assert len(tube.parameter_order) == 3
    assert len(stepped.element_ids) == 8
    assert len(tube.element_ids) == 4
    assert set(stepped.parameter_order).isdisjoint(tube.parameter_order)
    assert set(stepped.element_ids).isdisjoint(tube.element_ids)
    assert stepped.declaration.relationships != tube.declaration.relationships
    assert (
        stepped.declaration.structural_predicates
        != tube.declaration.structural_predicates
    )
    assert stepped.declaration.mapping_admission != tube.declaration.mapping_admission
    assert (
        stepped.declaration.optimization_preflight
        != tube.declaration.optimization_preflight
    )
    assert tube.declaration.mapping_admission.relative_rank.required_rank == 3
    assert tube.transform.rotation != (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    assert tube.transform.translation_m != (0.0, 0.0, 0.0)


@pytest.mark.parametrize("field", ["parameter_order", "element_ids", "model_id"])
def test_generation_rejects_inconsistent_declared_binding(field: str) -> None:
    request = create_generation_request("coaxial-tube-v1", seed=7, noise_sigma_m=20e-6)
    record = request.model_dump(mode="python")
    record[field] = (
        "model." + "0" * 64 if field == "model_id" else tuple(reversed(record[field]))
    )
    with pytest.raises(ValidationError, match=r"model binding|order"):
        _ = GenerationRequest.model_validate(record)


def test_self_consistent_replacement_declaration_is_not_fixture_admission() -> None:
    request = create_generation_request("coaxial-tube-v1", seed=7, noise_sigma_m=20e-6)
    semantic = copy.deepcopy(
        request.declaration.model_dump(mode="python", exclude={"model_id"})
    )
    semantic["parameters"][0]["nominal"] += 0.0001
    declaration = identify_model(ModelSemanticDeclaration.model_validate(semantic))
    record = request.model_dump(mode="python")
    record["declaration"] = declaration
    record["model_id"] = declaration.model_id
    replacement = GenerationRequest.model_validate(record)
    with pytest.raises(ScansorError, match="designated fixture declaration"):
        _ = prepare_generation(replacement)


def test_truth_parameter_reorder_fails_even_with_matching_dimensions() -> None:
    prepared = prepare_generation(
        create_generation_request("coaxial-tube-v1", seed=7, noise_sigma_m=20e-6)
    )
    record = prepared.ground_truth.model_dump(mode="python")
    record["parameter_order"] = tuple(reversed(record["parameter_order"]))
    record["parameter_values"] = tuple(reversed(record["parameter_values"]))
    with pytest.raises(ValidationError, match="parameter order"):
        _ = GenerationGroundTruth.model_validate(record)


def test_fixture_source_binding_rejects_rehashed_substitution() -> None:
    prepared = prepare_generation(
        create_generation_request("coaxial-tube-v1", seed=7, noise_sigma_m=20e-6)
    )
    provenance = generated_fixture_provenance(prepared, "a" * 64)
    record = provenance.model_dump(mode="json")
    record["source_sha256"] = "b" * 64
    record["content_sha256"] = sha256(
        canonical_json(
            {key: value for key, value in record.items() if key != "content_sha256"}
        )
    )
    with pytest.raises(ValidationError, match="source binding"):
        _ = DeclaredGeneratedFixtureProvenance.model_validate(record)


def test_unknown_fixture_is_not_admitted() -> None:
    with pytest.raises(ScansorError, match="fixture"):
        _ = create_generation_request("unregistered", seed=7, noise_sigma_m=20e-6)


@pytest.mark.parametrize("fixture_id", ["asymmetric-stepped-v1", "coaxial-tube-v1"])
def test_generation_requires_byte_exact_fixture_pose(fixture_id: str) -> None:
    request = create_generation_request(fixture_id, seed=7, noise_sigma_m=20e-6)
    record = request.model_dump(mode="json")
    record["transform"]["rotation"][0][1] = -0.0
    changed = GenerationRequest.model_validate(record)
    assert changed == request
    assert canonical_json(changed) != canonical_json(request)
    with pytest.raises(ScansorError, match="designated fixture declaration and pose"):
        _ = prepare_generation(changed)
