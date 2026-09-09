from __future__ import annotations

import ast
import copy
import importlib
from pathlib import Path
from typing import Any

import pytest

import scansor.declared_generation as generation_module
from scansor.declared_execution_runs import verify_execution_run
from scansor.declared_generation_models import FixtureDefinition, FixtureSample
from scansor.declared_synthetic_fixtures import fixture_definition
from scansor.declared_truth_comparison import compare_truth
from scansor.model_declarations import ModelSemanticDeclaration, identify_model
from tests.test_declared_generated_pipeline import (
    FIXTURES,
    assert_completed,
    published_execution,
    published_mapping,
)

SHARED_MODULES = (
    "model_declarations",
    "geometry_evaluator",
    "mapping_models",
    "observation_mapping",
    "mapping_runs",
    "declared_factor_models",
    "declared_factors",
    "declared_execution_models",
    "declared_execution",
    "declared_numpy_backend",
    "declared_execution_run_models",
    "declared_execution_runs",
    "declared_generation_models",
    "declared_generation",
    "declared_generation_runs",
    "declared_truth_comparison",
)


@pytest.mark.parametrize("module_name", SHARED_MODULES)
def test_shared_workflow_has_no_topology_dispatch(module_name: str) -> None:
    module = importlib.import_module(f"scansor.{module_name}")
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imports = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    imports.extend(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    prohibited = (
        "scansor.stepped",
        "scansor.tube",
        "scansor.factor_models",
        "scansor.execution_models",
        "scansor.execution_runs",
        "scansor.generation_models",
        "scansor.generation_runs",
        "scansor.truth_comparison",
    )
    assert not [name for name in imports if name.startswith(prohibited)]
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "variant"
        for node in ast.walk(tree)
    )
    fixtures = tuple(fixture_definition(fixture_id) for fixture_id in FIXTURES)
    opaque_ids = {
        value
        for fixture in fixtures
        for value in (
            fixture.fixture_id,
            fixture.declaration.model_id,
            *(element.element_id for element in fixture.declaration.elements),
            *(parameter.parameter_id for parameter in fixture.declaration.parameters),
        )
    }
    parameter_fields = {
        "parameters",
        "parameter_order",
        "parameter_scales",
        "lower_bounds",
        "upper_bounds",
        "shape_values",
        "initial_parameters",
        "final_parameters",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Compare, ast.match_case)):
            assert not any(
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and item.value in opaque_ids
                for item in ast.walk(node)
            )
            referenced_names = {
                item.attr if isinstance(item, ast.Attribute) else item.id
                for item in ast.walk(node)
                if isinstance(item, (ast.Attribute, ast.Name))
            }
            if referenced_names & (
                parameter_fields
                | {
                    "dimension",
                    "parameter_count",
                    "parameter_dimension",
                    "elements",
                    "element_ids",
                    "element_count",
                }
            ):
                assert not any(
                    isinstance(item, ast.Constant) and item.value in (3, 4, 6, 7, 8)
                    for item in ast.walk(node)
                )
        if isinstance(node, ast.Subscript) and any(
            (isinstance(item, ast.Attribute) and item.attr in parameter_fields)
            or (isinstance(item, ast.Name) and item.id in parameter_fields)
            for item in ast.walk(node.value)
        ):
            assert not any(
                isinstance(item, ast.Constant) and isinstance(item.value, int)
                for item in ast.walk(node.slice)
            )


def _replace_ids(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, tuple):
        return tuple(_replace_ids(item, replacements) for item in value)
    if isinstance(value, dict):
        return {key: _replace_ids(item, replacements) for key, item in value.items()}
    return value


def _renamed_reordered_fixture(original: FixtureDefinition) -> FixtureDefinition:
    declaration = original.declaration
    replacements = {
        parameter.parameter_id: f"dimension-{index}"
        for index, parameter in enumerate(declaration.parameters)
    } | {
        element.element_id: f"surface-{index}"
        for index, element in enumerate(declaration.elements)
    }
    record = _replace_ids(
        copy.deepcopy(declaration.model_dump(mode="python", exclude={"model_id"})),
        replacements,
    )
    record["parameters"] = tuple(reversed(record["parameters"]))
    record["elements"] = tuple(reversed(record["elements"]))
    order = tuple(parameter["parameter_id"] for parameter in record["parameters"])
    record["problem"]["varied_parameter_ids"] = order
    for context in ("mapping_admission", "optimization_preflight"):
        policy = record[context]["relative_rank"]
        scales = dict(
            zip(policy["parameter_ids"], policy["parameter_scales"], strict=True)
        )
        policy["parameter_ids"] = order
        policy["parameter_scales"] = tuple(
            scales[parameter_id] for parameter_id in order
        )
    renamed = identify_model(ModelSemanticDeclaration.model_validate(record))
    element_positions = {
        element.element_id: index for index, element in enumerate(renamed.elements)
    }
    samples = [
        FixtureSample.model_validate(
            _replace_ids(sample.model_dump(mode="python"), replacements)
        )
        for sample in original.samples
    ]
    samples.sort(key=lambda sample: (element_positions[sample.element_id], sample.key))
    return FixtureDefinition(
        declaration=renamed,
        fixture_id=original.fixture_id,
        samples=tuple(samples),
        source_frame="renamed-observation-frame",
        transform=original.transform,
    )


@pytest.mark.parametrize("fixture_id", FIXTURES)
def test_renamed_reordered_fixture_uses_complete_shared_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_id: str
) -> None:
    original = fixture_definition(fixture_id)
    renamed = _renamed_reordered_fixture(original)

    def owned_fixture(selected_id: str) -> FixtureDefinition:
        assert selected_id == fixture_id
        return renamed

    # Only the project-owned fixture construction seam changes. Every generation,
    # mapping, factor, execution, publication and verification entry point is shared.
    monkeypatch.setattr(generation_module, "fixture_definition", owned_fixture)
    _generated, mapping = published_mapping(tmp_path, fixture_id)
    records = published_execution(tmp_path, mapping)
    assert_completed(mapping, records)
    assert mapping.request.declaration == renamed.declaration
    assert mapping.model_id != original.declaration.model_id
    assert all(
        parameter.parameter_id.startswith("dimension-")
        for parameter in mapping.request.declaration.parameters
    )
    assert all(
        element.element_id.startswith("surface-")
        for element in mapping.request.declaration.elements
    )
    assert (
        verify_execution_run(
            tmp_path / "execution", tmp_path / "inspection", tmp_path / "mapping"
        )
        == records
    )
    lines = compare_truth(
        *(
            tmp_path / name
            for name in ("generation", "inspection", "mapping", "execution")
        )
    )
    reported_parameters = tuple(
        line.split(":", 1)[0].removeprefix("parameter ")
        for line in lines
        if line.startswith("parameter ")
    )
    assert reported_parameters == tuple(
        parameter.parameter_id for parameter in renamed.declaration.parameters
    )
