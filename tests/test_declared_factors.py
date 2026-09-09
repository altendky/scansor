from __future__ import annotations

import copy
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

from scansor.declared_factor_models import (
    ActiveFactorSelection,
    FactorEvaluation,
    InstantiatedFactor,
    InstantiatedFactorSet,
    ParameterVector,
    PreflightDiagnostics,
    content_id,
)
from scansor.declared_factors import (
    evaluate_factors,
    instantiate_factors,
    preflight_factors,
    select_active_factors,
)
from scansor.errors import ScansorError
from scansor.factor_models import ParameterVector as SteppedParameterVector
from scansor.mapping_models import MappingResult
from scansor.model_declarations import (
    ModelDeclaration,
    ModelSemanticDeclaration,
    PolicyContext,
    identify_model,
)
from scansor.observation_mapping import build_mapping
from scansor.serialization import canonical_json, parse_canonical_json
from scansor.stepped_model_declarations import stepped_model_declaration
from scansor.stepped_rotational_factors import (
    evaluate_factors as evaluate_stepped_factors,
)
from scansor.stepped_rotational_factors import (
    instantiate_factors as instantiate_stepped_factors,
)
from scansor.stepped_rotational_factors import (
    select_active_factors as select_stepped_factors,
)
from tests.test_mapping import (
    canonical_bytes,
    constructed_shell_declaration,
    fixture_points,
    request_for,
)


def _mapping_for(
    declaration: ModelDeclaration,
    points: list[tuple[float, float, float]],
    *,
    asymmetric: bool = True,
    held_out: tuple[int, ...] = (),
) -> MappingResult:
    canonical = canonical_bytes(points)
    return build_mapping(
        request_for(
            canonical,
            asymmetric=asymmetric,
            declaration=declaration,
            held_out=held_out,
        ),
        canonical,
    )


def _factor_case(
    declaration: ModelDeclaration,
    points: list[tuple[float, float, float]],
    *,
    asymmetric: bool = True,
    held_out: tuple[int, ...] = (),
) -> tuple[MappingResult, InstantiatedFactorSet, ActiveFactorSelection]:
    mapping = _mapping_for(
        declaration, points, asymmetric=asymmetric, held_out=held_out
    )
    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(
        factor_set, tuple(item.factor_id for item in factor_set.factors)
    )
    return mapping, factor_set, selection


def _parameters(
    declaration: ModelDeclaration, values: tuple[float, ...] | None = None
) -> ParameterVector:
    return ParameterVector(
        model_id=declaration.model_id,
        values=(
            tuple(item.nominal for item in declaration.parameters)
            if values is None
            else values
        ),
    )


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_stepped_shape_results_are_preserved_through_declared_path(
    variant: str,
) -> None:
    asymmetric = variant == "asymmetric-datum-flat"
    declaration = stepped_model_declaration(
        "asymmetric-datum-flat" if asymmetric else "axisymmetric"
    )
    points = fixture_points(asymmetric=asymmetric)
    mapping = _mapping_for(declaration, points, asymmetric=asymmetric)

    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(
        factor_set, tuple(item.factor_id for item in factor_set.factors)
    )
    evaluation = evaluate_factors(factor_set, selection, _parameters(declaration))

    stepped_set = instantiate_stepped_factors(mapping)
    stepped_selection = select_stepped_factors(
        stepped_set, tuple(item.factor_id for item in stepped_set.factors)
    )
    stepped_evaluation = evaluate_stepped_factors(
        stepped_set,
        stepped_selection,
        SteppedParameterVector(
            problem="fixed-pose-shape",
            units="metre",
            values=tuple(item.nominal for item in declaration.parameters),
            variant="asymmetric-datum-flat" if asymmetric else "axisymmetric",
        ),
    )

    assert evaluation.parameter_order == tuple(
        item.parameter_id for item in declaration.parameters
    )
    assert evaluation.raw_residuals_m == stepped_evaluation.raw_residuals_m
    assert evaluation.jacobian == stepped_evaluation.jacobian
    diagnostic = preflight_factors(factor_set, selection, _parameters(declaration))
    assert diagnostic.eligible_for_optimization
    assert (
        diagnostic.observed_rank
        == diagnostic.expected_rank
        == len(declaration.parameters)
    )


def test_nonstepped_declaration_varies_ids_dimensions_and_element_count() -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )

    evaluation = evaluate_factors(factor_set, selection, _parameters(declaration))
    diagnostic = preflight_factors(factor_set, selection, _parameters(declaration))

    assert len(factor_set.declaration.elements) == 1
    assert evaluation.parameter_order == ("shell-radius",)
    assert evaluation.raw_residuals_m == (0.0, 0.0, 0.0)
    assert evaluation.jacobian == ((-1.0,), (-1.0,), (-1.0,))
    assert diagnostic.required_support_counts == {"shell": 3}
    assert diagnostic.observed_rank == diagnostic.expected_rank == 1
    assert diagnostic.eligible_for_optimization


def test_records_are_model_bound_identified_and_canonically_round_trippable() -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    evaluation = evaluate_factors(factor_set, selection, _parameters(declaration))
    diagnostic = preflight_factors(factor_set, selection, _parameters(declaration))

    for record, model in (
        (factor_set, InstantiatedFactorSet),
        (selection, ActiveFactorSelection),
        (evaluation, FactorEvaluation),
        (diagnostic, PreflightDiagnostics),
    ):
        encoded = canonical_json(record)
        restored = model.model_validate(
            parse_canonical_json(encoded, "declared factor record", len(encoded))
        )
        assert restored == record
        assert canonical_json(restored) == encoded
    assert all(
        item.model_id == declaration.model_id for item in factor_set.declarations
    )
    assert all(item.model_id == declaration.model_id for item in factor_set.factors)


def test_activation_is_explicit_ordered_and_model_bound() -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, _selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    ids = tuple(item.factor_id for item in factor_set.factors)

    assert select_active_factors(factor_set, ()).active_factor_ids == ()
    with pytest.raises(ScansorError, match="unique"):
        _ = select_active_factors(factor_set, (ids[0], ids[0]))
    with pytest.raises(ScansorError, match="unknown"):
        _ = select_active_factors(factor_set, ("factor." + "0" * 64,))
    with pytest.raises(ScansorError, match="relative order"):
        _ = select_active_factors(factor_set, (ids[1], ids[0]))


def test_cross_model_selection_and_parameter_vectors_fail_closed() -> None:
    shell = constructed_shell_declaration()
    _mapping, shell_set, shell_selection = _factor_case(
        shell, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    stepped = stepped_model_declaration("axisymmetric")
    _mapping, stepped_set, _selection = _factor_case(
        stepped, fixture_points(asymmetric=False), asymmetric=False
    )

    with pytest.raises(ScansorError, match="another factor set or model"):
        _ = evaluate_factors(stepped_set, shell_selection, _parameters(stepped))
    with pytest.raises(ScansorError, match="another model"):
        _ = evaluate_factors(stepped_set, _selection, _parameters(shell))
    with pytest.raises(ScansorError, match="dimension"):
        _ = evaluate_factors(
            shell_set,
            shell_selection,
            ParameterVector(model_id=shell.model_id, values=(0.02, 0.03)),
        )


def _with_preflight_coverage(declaration: ModelDeclaration) -> ModelDeclaration:
    record = copy.deepcopy(declaration.model_dump(mode="python", exclude={"model_id"}))
    mapping_policy = declaration.mapping_admission
    optimization_policy = declaration.optimization_preflight
    coverage_cells = tuple(
        cell.model_copy(
            update={
                "cell_id": f"preflight.{cell.cell_id}",
                "domain": cell.domain.model_copy(
                    update={
                        "domain_id": f"preflight.{cell.domain.domain_id}",
                        "predicates": tuple(
                            predicate.model_copy(
                                update={
                                    "predicate_id": (
                                        f"preflight.{predicate.predicate_id}"
                                    )
                                }
                            )
                            for predicate in cell.domain.predicates
                        ),
                    }
                ),
            }
        )
        for cell in mapping_policy.coverage_cells
    )
    record["optimization_preflight"] = PolicyContext(
        coverage_cells=coverage_cells,
        relative_rank=optimization_policy.relative_rank,
        required_support=optimization_policy.required_support,
    )
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def test_preflight_coverage_uses_bound_nominal_classification_of_active_factors() -> (
    None
):
    declaration = _with_preflight_coverage(constructed_shell_declaration())
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    full = preflight_factors(factor_set, selection, _parameters(declaration))
    assert full.coverage_cell_order == ("preflight.shell-middle",)
    assert full.coverage_cell_counts == {"preflight.shell-middle": 2}
    assert not full.missing_coverage_cells

    outside = select_active_factors(factor_set, (factor_set.factors[-1].factor_id,))
    adverse = preflight_factors(factor_set, outside, _parameters(declaration))
    assert adverse.required_support_counts == {"shell": 1}
    assert not adverse.missing_required_support
    assert adverse.coverage_cell_counts == {"preflight.shell-middle": 0}
    assert adverse.missing_coverage_cells == ("preflight.shell-middle",)
    assert adverse.failure_codes == ("insufficient-coverage",)


def _with_permissive_lower_bound(declaration: ModelDeclaration) -> ModelDeclaration:
    record = copy.deepcopy(declaration.model_dump(mode="python", exclude={"model_id"}))
    record["parameters"][0]["lower"] = -0.01
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def test_preflight_distinguishes_bounds_and_structural_invalidity() -> None:
    declaration = _with_permissive_lower_bound(constructed_shell_declaration())
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )

    structural = preflight_factors(
        factor_set, selection, _parameters(declaration, (0.0,))
    )
    assert structural.failure_codes == ("structural-geometry-invalid",)
    assert structural.evaluation_id is None

    bounds = preflight_factors(
        factor_set, selection, _parameters(declaration, (0.031,))
    )
    assert bounds.failure_codes == ("parameter-out-of-bounds",)
    assert bounds.evaluation_id is None


def _replace_factor_point(
    factor_set: InstantiatedFactorSet,
    index: int,
    point: tuple[float, float, float],
) -> InstantiatedFactorSet:
    factor = factor_set.factors[index]
    factor_values = cast(
        dict[str, Any],
        factor.model_dump(mode="python", exclude={"factor_id"})
        | {"point_model_m": point},
    )
    provisional_factor = InstantiatedFactor.model_construct(
        factor_id="", **factor_values
    )
    replacement = InstantiatedFactor(
        factor_id=content_id("factor", provisional_factor, "factor_id"),
        **factor_values,
    )
    factors = list(factor_set.factors)
    factors[index] = replacement
    provisional_set = InstantiatedFactorSet.model_construct(
        declaration=factor_set.declaration,
        declarations=factor_set.declarations,
        factor_set_id="",
        factors=tuple(factors),
        format=factor_set.format,
        format_status=factor_set.format_status,
        mapping_run_id=factor_set.mapping_run_id,
        model_id=factor_set.model_id,
    )
    return InstantiatedFactorSet(
        factor_set_id=content_id("factor-set", provisional_set, "factor_set_id"),
        declaration=factor_set.declaration,
        declarations=factor_set.declarations,
        factors=tuple(factors),
        format=factor_set.format,
        format_status=factor_set.format_status,
        mapping_run_id=factor_set.mapping_run_id,
        model_id=factor_set.model_id,
    )


def test_preflight_reports_evaluation_failure_independently() -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, _selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    changed = _replace_factor_point(factor_set, 0, (0.0, 0.0, 0.0))
    selection = select_active_factors(
        changed, tuple(item.factor_id for item in changed.factors)
    )

    diagnostic = preflight_factors(changed, selection, _parameters(declaration))
    assert diagnostic.failure_codes == ("evaluation-failed",)
    assert diagnostic.evaluation_id is None


def test_missing_support_coverage_and_rank_are_distinct_and_ordered() -> None:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    _mapping, factor_set, _selection = _factor_case(declaration, fixture_points())
    selected_ids = tuple(
        factor.factor_id
        for factor_declaration, factor in zip(
            factor_set.declarations, factor_set.factors, strict=True
        )
        if factor_declaration.element_id == "cylinder.band-1"
    )
    selection = select_active_factors(factor_set, selected_ids)

    diagnostic = preflight_factors(factor_set, selection, _parameters(declaration))
    assert diagnostic.failure_codes == (
        "missing-required-support",
        "insufficient-coverage",
        "rank-deficient",
    )
    assert diagnostic.observed_rank == 1


def test_rank_evaluation_failure_is_independently_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )

    def fail_svd(*_args: object, **_kwargs: object) -> object:
        raise np.linalg.LinAlgError("injected SVD failure")

    monkeypatch.setattr(np.linalg, "svd", fail_svd)
    diagnostic = preflight_factors(factor_set, selection, _parameters(declaration))

    assert diagnostic.failure_codes == ("rank-evaluation-failed",)
    assert diagnostic.evaluation_id is not None
    assert diagnostic.observed_rank == 0


def test_malformed_or_tampered_graphs_fail_before_diagnostics() -> None:
    declaration = constructed_shell_declaration()
    _mapping, factor_set, selection = _factor_case(
        declaration, [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    )
    stale_declaration = declaration.model_copy(update={"model_id": "model." + "0" * 64})
    stale_set = factor_set.model_copy(update={"declaration": stale_declaration})

    with pytest.raises(ScansorError, match="invalid instantiated factor set"):
        _ = preflight_factors(stale_set, selection, _parameters(declaration))

    stale_factor = factor_set.factors[0].model_copy(
        update={"nominal_coverage_cell_ids": ("forged",)}
    )
    stale_set = factor_set.model_copy(
        update={"factors": (stale_factor, *factor_set.factors[1:])}
    )
    with pytest.raises(ScansorError, match="invalid instantiated factor set"):
        _ = preflight_factors(stale_set, selection, _parameters(declaration))


def test_rejected_mapping_cannot_instantiate_factors() -> None:
    declaration = constructed_shell_declaration()
    mapping = _mapping_for(declaration, [(0.02, 0.0, 0.0)])
    assert mapping.disposition == "rejected"
    with pytest.raises(ScansorError, match="rejected source mapping"):
        _ = instantiate_factors(mapping)


def test_held_out_changes_do_not_affect_factor_math_or_preflight_policy() -> None:
    declaration = constructed_shell_declaration()
    training = [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    mappings = (
        _mapping_for(declaration, [*training, (0.02, 0.0, 0.9)], held_out=(3,)),
        _mapping_for(declaration, [*training, (0.5, 0.5, -0.9)], held_out=(3,)),
    )
    factor_sets = tuple(instantiate_factors(item) for item in mappings)
    selections = tuple(
        select_active_factors(
            factor_set, tuple(item.factor_id for item in factor_set.factors)
        )
        for factor_set in factor_sets
    )
    evaluations = tuple(
        evaluate_factors(factor_set, selection, _parameters(declaration))
        for factor_set, selection in zip(factor_sets, selections, strict=True)
    )
    diagnostics = tuple(
        preflight_factors(factor_set, selection, _parameters(declaration))
        for factor_set, selection in zip(factor_sets, selections, strict=True)
    )

    assert mappings[0].mapping_run_id != mappings[1].mapping_run_id
    assert evaluations[0].raw_residuals_m == evaluations[1].raw_residuals_m
    assert evaluations[0].jacobian == evaluations[1].jacobian
    assert (
        diagnostics[0].required_support_counts == diagnostics[1].required_support_counts
    )
    assert diagnostics[0].coverage_cell_counts == diagnostics[1].coverage_cell_counts
    assert (
        diagnostics[0].singular_values_dimensionless
        == diagnostics[1].singular_values_dimensionless
    )
    assert diagnostics[0].failure_codes == diagnostics[1].failure_codes


def test_parameter_vector_rejects_nonfinite_values() -> None:
    declaration = constructed_shell_declaration()
    with pytest.raises(ValidationError, match="finite"):
        _ = ParameterVector(model_id=declaration.model_id, values=(float("nan"),))
