from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

import scansor.factor_models as factor_models_module
import scansor.stepped_rotational_factors as factors_module
from scansor.errors import ScansorError
from scansor.factor_models import (
    NOMINAL_SHAPE,
    ActiveFactorSelection,
    FactorDeclaration,
    FactorEvaluation,
    InstantiatedFactor,
    InstantiatedFactorSet,
    ParameterVector,
    PreflightDiagnostics,
    content_id,
)
from scansor.mapping_models import MappingResult
from scansor.ply import canonical_npy
from scansor.serialization import canonical_json, parse_canonical_json
from scansor.stepped_rotational import build_mapping
from scansor.stepped_rotational_factors import (
    evaluate_factors,
    instantiate_factors,
    preflight_factors,
    select_active_factors,
)
from tests.test_mapping import canonical_bytes, fixture_points, request_for

FINITE_DIFFERENCE_STEP = 1e-6
FINITE_DIFFERENCE_ABSOLUTE_TOLERANCE = 2e-8


def factor_case(
    variant: str = "asymmetric-datum-flat", *, normals: bool = False
) -> tuple[MappingResult, InstantiatedFactorSet, ActiveFactorSelection]:
    asymmetric = variant == "asymmetric-datum-flat"
    points = fixture_points(asymmetric=asymmetric)
    points.append(points[0])
    canonical = canonical_bytes(points, normals=normals)
    mapping = build_mapping(
        request_for(
            canonical,
            asymmetric=asymmetric,
            held_out=(len(points) - 1,),
        ),
        canonical,
    )
    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(
        factor_set, tuple(factor.factor_id for factor in factor_set.factors)
    )
    return mapping, factor_set, selection


def parameters(
    variant: str,
    problem: str = "fixed-pose-shape",
    values: tuple[float, ...] | None = None,
) -> ParameterVector:
    if problem == "fixed-pose-shape":
        default = (
            NOMINAL_SHAPE if variant == "asymmetric-datum-flat" else NOMINAL_SHAPE[:6]
        )
        units = "metre"
    else:
        default = (0.0,) * 6
        units = "metre/radian"
    return ParameterVector(
        problem=problem,
        units=units,
        values=default if values is None else values,
        variant=variant,
    )


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_declaration_instantiation_activation_and_schema_round_trips(
    variant: str,
) -> None:
    mapping, factor_set, selection = factor_case(variant)
    assert mapping.instantiated_factors is None
    assert mapping.active_factor_ids == ()
    assert mapping.fit_result is None
    assert len(factor_set.declarations) == len(mapping.mappings)
    assert len(factor_set.factors) == len(mapping.mappings)
    assert selection.active_factor_ids == tuple(
        factor.factor_id for factor in factor_set.factors
    )
    for record, model in (
        (factor_set, InstantiatedFactorSet),
        (selection, ActiveFactorSelection),
    ):
        encoded = canonical_json(record)
        restored = model.model_validate(
            parse_canonical_json(encoded, "factor record", len(encoded))
        )
        assert restored == record
        assert canonical_json(restored) == encoded
    assert all(
        declaration.mapping_run_id == mapping.mapping_run_id
        for declaration in factor_set.declarations
    )


def test_activation_is_explicit_and_rejects_unknown_duplicate_and_reordered_ids() -> (
    None
):
    _mapping, factor_set, _selection = factor_case()
    ids = tuple(factor.factor_id for factor in factor_set.factors)
    assert select_active_factors(factor_set, ()).active_factor_ids == ()
    with pytest.raises(ScansorError, match="unique"):
        select_active_factors(factor_set, (ids[0], ids[0]))
    with pytest.raises(ScansorError, match="unknown"):
        select_active_factors(factor_set, ("factor." + "0" * 64,))
    with pytest.raises(ScansorError, match="relative order"):
        select_active_factors(factor_set, (ids[1], ids[0]))


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_nominal_residuals_and_shape_jacobians_are_exact(variant: str) -> None:
    _mapping, factor_set, selection = factor_case(variant)
    evaluation = evaluate_factors(factor_set, selection, parameters(variant))
    assert evaluation.raw_residuals_m == (0.0,) * len(factor_set.factors)
    for declaration, row in zip(
        factor_set.declarations, evaluation.jacobian, strict=True
    ):
        expected = [0.0] * len(evaluation.parameter_order)
        element = declaration.element_id
        if element.startswith("cylinder.band-"):
            expected[int(element[-1]) - 1] = -1.0
        elif element == "plane.station-20":
            expected[3] = 1.0
        elif element == "plane.station-50":
            expected[4] = -1.0
        elif element == "plane.station-80":
            expected[5] = -1.0
        elif element == "plane.datum-flat":
            expected[6] = -1.0
        assert row == tuple(expected)


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_test_only_numpy_least_squares_recovers_shape(variant: str) -> None:
    _mapping, factor_set, selection = factor_case(variant)
    truth = np.asarray(NOMINAL_SHAPE[: 7 if variant == "asymmetric-datum-flat" else 6])
    initial = truth + np.array(
        (-0.0004, 0.0005, -0.0003, -0.0002, 0.0003, -0.0004)
        + ((0.0002,) if len(truth) == 7 else ())
    )
    evaluation = evaluate_factors(
        factor_set, selection, parameters(variant, values=tuple(initial))
    )
    jacobian = np.asarray(evaluation.jacobian)
    residuals = np.asarray(evaluation.raw_residuals_m)
    recovered = initial + np.linalg.lstsq(jacobian, -residuals, rcond=None)[0]
    np.testing.assert_allclose(recovered, truth, rtol=0.0, atol=1e-15)


def numerical_pose_jacobian(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    center: np.ndarray,
) -> np.ndarray:
    columns = []
    for index in range(6):
        delta = np.zeros(6)
        delta[index] = FINITE_DIFFERENCE_STEP
        plus = evaluate_factors(
            factor_set,
            selection,
            parameters(
                factor_set.variant,
                "fixed-geometry-pose-correction",
                tuple(center + delta),
            ),
        )
        minus = evaluate_factors(
            factor_set,
            selection,
            parameters(
                factor_set.variant,
                "fixed-geometry-pose-correction",
                tuple(center - delta),
            ),
        )
        columns.append(
            (np.asarray(plus.raw_residuals_m) - np.asarray(minus.raw_residuals_m))
            / (2.0 * FINITE_DIFFERENCE_STEP)
        )
    return np.column_stack(columns)


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
@pytest.mark.parametrize(
    "center",
    [
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        (0.0003, -0.0002, 0.0001, 0.012, -0.009, 0.025),
        (0.0, 0.0, 0.0, 2e-7, -3e-7, 1e-7),
        (0.0025, -0.0024, 0.0023, 0.075, -0.074, 0.073),
    ],
)
def test_pose_jacobian_matches_independent_finite_differences(
    variant: str, center: tuple[float, ...]
) -> None:
    _mapping, factor_set, selection = factor_case(variant)
    analytic = evaluate_factors(
        factor_set,
        selection,
        parameters(variant, "fixed-geometry-pose-correction", center),
    )
    numerical = numerical_pose_jacobian(factor_set, selection, np.asarray(center))
    np.testing.assert_allclose(
        np.asarray(analytic.jacobian),
        numerical,
        rtol=2e-7,
        atol=FINITE_DIFFERENCE_ABSOLUTE_TOLERANCE,
    )


@pytest.mark.parametrize(
    ("variant", "problem", "expected_rank"),
    [
        ("axisymmetric", "fixed-pose-shape", 6),
        ("asymmetric-datum-flat", "fixed-pose-shape", 7),
        ("axisymmetric", "fixed-geometry-pose-correction", 5),
        ("asymmetric-datum-flat", "fixed-geometry-pose-correction", 6),
    ],
)
def test_nominal_preflight_coverage_rank_and_expected_gauge(
    variant: str, problem: str, expected_rank: int
) -> None:
    _mapping, factor_set, selection = factor_case(variant)
    diagnostic = preflight_factors(factor_set, selection, parameters(variant, problem))
    assert diagnostic.eligible_for_optimization
    assert diagnostic.failure_codes == ()
    assert diagnostic.observed_rank == diagnostic.expected_rank == expected_rank
    assert not diagnostic.missing_active_elements
    assert diagnostic.rank_relative_threshold == 1e-10


def test_missing_and_datum_ablation_return_stable_adverse_preflight() -> None:
    _mapping, factor_set, _selection = factor_case()
    ids = tuple(
        factor.factor_id
        for declaration, factor in zip(
            factor_set.declarations, factor_set.factors, strict=True
        )
        if declaration.element_id != "plane.datum-flat"
    )
    selection = select_active_factors(factor_set, ids)
    diagnostic = preflight_factors(
        factor_set,
        selection,
        parameters("asymmetric-datum-flat", "fixed-geometry-pose-correction"),
    )
    assert diagnostic.failure_codes == (
        "missing-active-elements",
        "rank-deficient",
    )
    assert diagnostic.missing_active_elements == ("plane.datum-flat",)
    assert diagnostic.observed_rank == 5
    assert not diagnostic.eligible_for_optimization


def test_empty_selection_returns_stable_adverse_preflight() -> None:
    _mapping, factor_set, _selection = factor_case()
    empty = select_active_factors(factor_set, ())
    diagnostic = preflight_factors(
        factor_set, empty, parameters("asymmetric-datum-flat")
    )
    assert diagnostic.failure_codes == (
        "empty-active-selection",
        "missing-active-elements",
        "rank-deficient",
    )
    assert diagnostic.observed_rank == 0
    assert not diagnostic.eligible_for_optimization


def test_axisymmetric_perturbed_pose_preserves_expected_roll_gauge() -> None:
    _mapping, factor_set, selection = factor_case("axisymmetric")
    pose = parameters(
        "axisymmetric",
        "fixed-geometry-pose-correction",
        (0.0003, -0.0002, 0.0001, 0.012, -0.009, 0.025),
    )
    diagnostic = preflight_factors(factor_set, selection, pose)
    assert diagnostic.failure_codes == ()
    assert diagnostic.observed_rank == diagnostic.expected_rank == 5
    assert diagnostic.eligible_for_optimization


def test_axisymmetric_five_factor_rank_five_uses_complete_roll_nullspace() -> None:
    _mapping, factor_set, _selection = factor_case("axisymmetric")
    indexes = (0, 1, 3, 4, 9)
    ids = tuple(factor_set.factors[index].factor_id for index in indexes)
    selection = select_active_factors(factor_set, ids)
    diagnostic = preflight_factors(
        factor_set,
        selection,
        parameters("axisymmetric", "fixed-geometry-pose-correction"),
    )
    assert len(selection.active_factor_ids) == 5
    assert diagnostic.observed_rank == diagnostic.expected_rank == 5
    assert diagnostic.failure_codes == ("missing-active-elements",)
    assert diagnostic.missing_active_elements == (
        "cylinder.band-3",
        "plane.station-20",
        "plane.station-50",
        "plane.station-80",
    )
    assert "unexpected-pose-gauge" not in diagnostic.failure_codes
    assert not diagnostic.eligible_for_optimization


def test_axisymmetric_gauge_svd_failure_returns_stable_diagnostic(
    monkeypatch,
) -> None:
    _mapping, factor_set, selection = factor_case("axisymmetric")
    original = factors_module.np.linalg.svd
    calls = 0

    def fail_second_svd(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise np.linalg.LinAlgError("injected gauge SVD failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(factors_module.np.linalg, "svd", fail_second_svd)
    diagnostic = preflight_factors(
        factor_set,
        selection,
        parameters("axisymmetric", "fixed-geometry-pose-correction"),
    )
    assert diagnostic.failure_codes == ("rank-evaluation-failed",)
    assert not diagnostic.eligible_for_optimization


def test_rank_deficiency_and_gauge_svd_failure_use_canonical_code_order(
    monkeypatch,
) -> None:
    _mapping, factor_set, _selection = factor_case("axisymmetric")
    ids = tuple(
        factor.factor_id
        for declaration, factor in zip(
            factor_set.declarations, factor_set.factors, strict=True
        )
        if declaration.element_id == "cylinder.band-1"
    )
    selection = select_active_factors(factor_set, ids)
    original = factors_module.np.linalg.svd
    calls = 0

    def fail_second_svd(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise np.linalg.LinAlgError("injected gauge SVD failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(factors_module.np.linalg, "svd", fail_second_svd)
    diagnostic = preflight_factors(
        factor_set,
        selection,
        parameters("axisymmetric", "fixed-geometry-pose-correction"),
    )
    assert diagnostic.failure_codes == (
        "missing-active-elements",
        "rank-evaluation-failed",
        "rank-deficient",
    )


def test_bounds_and_structural_geometry_are_distinct_failures() -> None:
    _mapping, factor_set, selection = factor_case()
    bad = parameters(
        "asymmetric-datum-flat",
        values=(0.010, 0.017, 0.0145, 0.018, 0.019, 0.083, 0.0165),
    )
    diagnostic = preflight_factors(factor_set, selection, bad)
    assert diagnostic.failure_codes == (
        "parameter-out-of-bounds",
        "structural-geometry-invalid",
    )
    out_of_bounds = parameters(
        "asymmetric-datum-flat",
        values=(0.009, 0.018, 0.014, 0.020, 0.050, 0.080, 0.016),
    )
    assert preflight_factors(factor_set, selection, out_of_bounds).failure_codes == (
        "parameter-out-of-bounds",
    )
    with pytest.raises(ValidationError, match="finite"):
        parameters(
            "asymmetric-datum-flat",
            values=(np.nan, *NOMINAL_SHAPE[1:]),
        )


def replace_factor_point(
    factor_set: InstantiatedFactorSet,
    index: int,
    point: tuple[float, float, float],
) -> InstantiatedFactorSet:
    original = factor_set.factors[index]
    factor_values = original.model_dump(exclude={"factor_id"})
    factor_values["point_model_m"] = point
    provisional = InstantiatedFactor.model_construct(factor_id="", **factor_values)
    replacement = InstantiatedFactor(
        factor_id=content_id("factor", provisional, "factor_id"), **factor_values
    )
    changed_factors = list(factor_set.factors)
    changed_factors[index] = replacement
    set_values = {
        "contract": factor_set.contract,
        "declarations": factor_set.declarations,
        "factors": tuple(changed_factors),
        "mapping_run_id": factor_set.mapping_run_id,
        "source_mapping_disposition": factor_set.source_mapping_disposition,
        "variant": factor_set.variant,
    }
    provisional_set = InstantiatedFactorSet.model_construct(
        factor_set_id="", **set_values
    )
    return InstantiatedFactorSet(
        factor_set_id=content_id("factor-set", provisional_set, "factor_set_id"),
        **set_values,
    )


def test_radial_axis_evaluation_is_explicitly_undefined() -> None:
    _mapping, factor_set, _selection = factor_case("axisymmetric")
    index = next(
        index
        for index, declaration in enumerate(factor_set.declarations)
        if declaration.element_id == "cylinder.band-1"
    )
    changed = replace_factor_point(factor_set, index, (0.0, 0.0, 0.01))
    changed_selection = select_active_factors(
        changed, tuple(factor.factor_id for factor in changed.factors)
    )
    with pytest.raises(ScansorError, match="radial zero"):
        evaluate_factors(changed, changed_selection, parameters("axisymmetric"))
    diagnostic = preflight_factors(
        changed, changed_selection, parameters("axisymmetric")
    )
    assert "radial-zero-evaluation" in diagnostic.failure_codes


def test_nonfinite_evaluation_has_distinct_adverse_code() -> None:
    _mapping, factor_set, _selection = factor_case("axisymmetric")
    index = next(
        index
        for index, declaration in enumerate(factor_set.declarations)
        if declaration.element_id == "cylinder.band-1"
    )
    changed = replace_factor_point(factor_set, index, (1e308, 1e308, 1e308))
    selection = select_active_factors(
        changed, tuple(factor.factor_id for factor in changed.factors)
    )
    pose = parameters(
        "axisymmetric",
        "fixed-geometry-pose-correction",
        (0.0, 0.0, 0.0, 0.08, 0.08, 0.08),
    )
    with np.errstate(over="ignore", invalid="ignore"):
        diagnostic = preflight_factors(changed, selection, pose)
    assert "nonfinite-evaluation" in diagnostic.failure_codes


def test_rejected_source_mapping_cannot_instantiate() -> None:
    points = [*fixture_points(), (1.0, 1.0, 1.0)]
    canonical = canonical_bytes(points)
    rejected = build_mapping(request_for(canonical), canonical)
    assert rejected.disposition == "rejected"
    with pytest.raises(ScansorError, match="rejected source mapping"):
        instantiate_factors(rejected)


def test_public_apis_revalidate_stale_model_copies() -> None:
    mapping, factor_set, selection = factor_case()
    stale_mapping = mapping.model_copy(update={"mapping_run_id": "0" * 64})
    with pytest.raises(ScansorError, match="invalid mapping result"):
        instantiate_factors(stale_mapping)

    stale_factor = factor_set.factors[0].model_copy(
        update={"point_model_m": (9.0, 8.0, 7.0)}
    )
    stale_set = factor_set.model_copy(
        update={"factors": (stale_factor, *factor_set.factors[1:])}
    )
    with pytest.raises(ScansorError, match="invalid instantiated factor set"):
        select_active_factors(stale_set, ())

    stale_selection = selection.model_copy(
        update={"active_factor_ids": tuple(reversed(selection.active_factor_ids))}
    )
    with pytest.raises(ScansorError, match="invalid active-factor selection"):
        evaluate_factors(
            factor_set, stale_selection, parameters("asymmetric-datum-flat")
        )

    stale_parameters = parameters("asymmetric-datum-flat").model_copy(
        update={"values": (math.nan, *NOMINAL_SHAPE[1:])}
    )
    with pytest.raises(ScansorError, match="invalid parameter vector"):
        preflight_factors(factor_set, selection, stale_parameters)


def test_valid_held_out_revision_changes_do_not_change_numerical_behavior() -> None:
    training = fixture_points()
    base_points = [*training, training[0]]
    changed_points = [*training, (0.5, -0.25, 0.75)]
    base_canonical = canonical_bytes(base_points, normals=True)
    changed_array = np.load(io.BytesIO(canonical_bytes(changed_points, normals=True)))
    changed_array = changed_array.copy()
    changed_array["nx"][-1] = 3.0
    changed_array["ny"][-1] = 4.0
    changed_array["nz"][-1] = 0.0
    changed_canonical = canonical_npy(changed_array)
    held_out = (len(training),)
    mappings = (
        build_mapping(request_for(base_canonical, held_out=held_out), base_canonical),
        build_mapping(
            request_for(changed_canonical, held_out=held_out), changed_canonical
        ),
    )
    factor_sets = tuple(instantiate_factors(mapping) for mapping in mappings)
    selections = tuple(
        select_active_factors(
            factor_set, tuple(factor.factor_id for factor in factor_set.factors)
        )
        for factor_set in factor_sets
    )
    evaluations = tuple(
        evaluate_factors(factor_set, selection, parameters("asymmetric-datum-flat"))
        for factor_set, selection in zip(factor_sets, selections, strict=True)
    )
    diagnostics = tuple(
        preflight_factors(factor_set, selection, parameters("asymmetric-datum-flat"))
        for factor_set, selection in zip(factor_sets, selections, strict=True)
    )
    assert factor_sets[0].factor_set_id != factor_sets[1].factor_set_id
    assert evaluations[0].raw_residuals_m == evaluations[1].raw_residuals_m
    assert evaluations[0].jacobian == evaluations[1].jacobian
    assert diagnostics[0].observed_rank == diagnostics[1].observed_rank
    assert (
        diagnostics[0].singular_values_dimensionless
        == diagnostics[1].singular_values_dimensionless
    )
    assert diagnostics[0].failure_codes == diagnostics[1].failure_codes == ()
    assert diagnostics[0].active_element_counts == diagnostics[1].active_element_counts
    assert all(
        '"normal":' not in canonical_json(item).decode("ascii") for item in factor_sets
    )


def reidentified_declaration(
    declaration: FactorDeclaration, **updates: object
) -> FactorDeclaration:
    values = declaration.model_dump(exclude={"declaration_id"}) | updates
    provisional = FactorDeclaration.model_construct(declaration_id="", **values)
    return FactorDeclaration(
        declaration_id=content_id("declaration", provisional, "declaration_id"),
        **values,
    )


def reidentified_factor(
    factor: InstantiatedFactor, declaration_id: str
) -> InstantiatedFactor:
    values = factor.model_dump(exclude={"factor_id"}) | {
        "declaration_id": declaration_id
    }
    provisional = InstantiatedFactor.model_construct(factor_id="", **values)
    return InstantiatedFactor(
        factor_id=content_id("factor", provisional, "factor_id"), **values
    )


def reidentified_factor_set(
    factor_set: InstantiatedFactorSet,
    declarations: tuple[FactorDeclaration, ...],
    factors: tuple[InstantiatedFactor, ...],
) -> InstantiatedFactorSet:
    values = {
        "contract": factor_set.contract,
        "declarations": declarations,
        "factors": factors,
        "mapping_run_id": factor_set.mapping_run_id,
        "source_mapping_disposition": factor_set.source_mapping_disposition,
        "variant": factor_set.variant,
    }
    provisional = InstantiatedFactorSet.model_construct(factor_set_id="", **values)
    return InstantiatedFactorSet(
        factor_set_id=content_id("factor-set", provisional, "factor_set_id"),
        **values,
    )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("mapping_id", "duplicate source mapping ID"),
        ("candidate_id", "duplicate source candidate ID"),
        ("observation_id", "duplicate source observation ID"),
    ],
)
def test_factor_set_rejects_reidentified_duplicate_source_ids(
    field: str, message: str
) -> None:
    _mapping, factor_set, _selection = factor_case()
    declarations = list(factor_set.declarations)
    factors = list(factor_set.factors)
    replacement = reidentified_declaration(
        declarations[1], **{field: getattr(declarations[0], field)}
    )
    declarations[1] = replacement
    factors[1] = reidentified_factor(factors[1], replacement.declaration_id)
    with pytest.raises(ValidationError, match=message):
        reidentified_factor_set(factor_set, tuple(declarations), tuple(factors))


@pytest.mark.parametrize("field", ["mapping_content_sha256", "mapping_request_sha256"])
def test_factor_set_rejects_mixed_revision_provenance(field: str) -> None:
    _mapping, factor_set, _selection = factor_case()
    declarations = list(factor_set.declarations)
    factors = list(factor_set.factors)
    mixed = reidentified_declaration(declarations[1], **{field: "0" * 64})
    declarations[1] = mixed
    factors[1] = reidentified_factor(factors[1], mixed.declaration_id)
    with pytest.raises(ValidationError, match="mix mapping revisions"):
        reidentified_factor_set(factor_set, tuple(declarations), tuple(factors))


def test_factor_set_rejects_duplicate_and_reordered_graphs() -> None:
    _mapping, factor_set, _selection = factor_case()
    duplicate_declarations = list(factor_set.declarations)
    duplicate_declarations[1] = duplicate_declarations[0]
    with pytest.raises(ValidationError, match="duplicate factor declaration ID"):
        reidentified_factor_set(
            factor_set, tuple(duplicate_declarations), factor_set.factors
        )

    duplicate_factors = list(factor_set.factors)
    duplicate_factors[1] = duplicate_factors[0]
    with pytest.raises(ValidationError, match="duplicate instantiated factor ID"):
        reidentified_factor_set(
            factor_set, factor_set.declarations, tuple(duplicate_factors)
        )

    reordered_declarations = (
        factor_set.declarations[1],
        factor_set.declarations[0],
        *factor_set.declarations[2:],
    )
    reordered_factors = (
        factor_set.factors[1],
        factor_set.factors[0],
        *factor_set.factors[2:],
    )
    with pytest.raises(ValidationError, match="mapping-relative row order"):
        reidentified_factor_set(factor_set, reordered_declarations, reordered_factors)


def test_asymmetric_nonzero_pose_preflight_remains_full_rank() -> None:
    _mapping, factor_set, selection = factor_case()
    pose = parameters(
        "asymmetric-datum-flat",
        "fixed-geometry-pose-correction",
        (0.0003, -0.0002, 0.0001, 0.012, -0.009, 0.025),
    )
    diagnostic = preflight_factors(factor_set, selection, pose)
    assert diagnostic.failure_codes == ()
    assert diagnostic.observed_rank == diagnostic.expected_rank == 6
    assert diagnostic.eligible_for_optimization


def test_nonzero_z_rotation_datum_derivative_matches_closed_form_oracle() -> None:
    _mapping, factor_set, selection = factor_case()
    theta = 0.031
    tx = 0.0002
    pose = parameters(
        "asymmetric-datum-flat",
        "fixed-geometry-pose-correction",
        (tx, -0.0001, 0.0003, 0.0, 0.0, theta),
    )
    evaluation = evaluate_factors(factor_set, selection, pose)
    index = next(
        index
        for index, declaration in enumerate(factor_set.declarations)
        if declaration.element_id == "plane.datum-flat"
    )
    point = factor_set.factors[index].point_model_m
    cosine = math.cos(theta)
    sine = math.sin(theta)
    expected_residual = cosine * point[0] - sine * point[1] + tx - 0.016
    expected_phiz = -sine * point[0] - cosine * point[1]
    assert evaluation.raw_residuals_m[index] == pytest.approx(
        expected_residual, abs=1e-15
    )
    assert evaluation.jacobian[index][:3] == (1.0, 0.0, 0.0)
    assert evaluation.jacobian[index][5] == pytest.approx(expected_phiz, abs=1e-15)


@pytest.mark.parametrize(
    ("model", "field", "message"),
    [
        (InstantiatedFactorSet, "factor_set_id", "factor set ID"),
        (ActiveFactorSelection, "selection_id", "selection ID"),
        (FactorEvaluation, "evaluation_id", "evaluation ID"),
        (PreflightDiagnostics, "preflight_id", "preflight ID"),
    ],
)
def test_content_tampering_is_rejected(model, field: str, message: str) -> None:
    _mapping, factor_set, selection = factor_case()
    records = {
        InstantiatedFactorSet: factor_set,
        ActiveFactorSelection: selection,
        FactorEvaluation: evaluate_factors(
            factor_set, selection, parameters("asymmetric-datum-flat")
        ),
        PreflightDiagnostics: preflight_factors(
            factor_set, selection, parameters("asymmetric-datum-flat")
        ),
    }
    value = records[model].model_dump(mode="json")
    value[field] = value[field][:-1] + ("0" if value[field][-1] != "0" else "1")
    with pytest.raises(ValidationError, match=message):
        model.model_validate(value)


def test_semantically_malformed_records_fail_validation() -> None:
    _mapping, factor_set, selection = factor_case()
    declaration = factor_set.declarations[0].model_dump(mode="json")
    declaration["factor_kind"] = "datum-planar"
    with pytest.raises(ValidationError, match="kind disagrees"):
        FactorDeclaration.model_validate(declaration)
    evaluation = evaluate_factors(
        factor_set, selection, parameters("asymmetric-datum-flat")
    ).model_dump(mode="json")
    evaluation["unit_factor_weight"] = 2.0
    with pytest.raises(ValidationError, match="less_than_equal"):
        FactorEvaluation.model_validate(evaluation)
    diagnostic = preflight_factors(
        factor_set, selection, parameters("asymmetric-datum-flat")
    ).model_dump(mode="json")
    diagnostic["failure_codes"] = ["rank-deficient", "missing-active-elements"]
    diagnostic["eligible_for_optimization"] = False
    with pytest.raises(ValidationError, match="out of order"):
        PreflightDiagnostics.model_validate(diagnostic)


def test_preflight_nested_records_are_immutable() -> None:
    _mapping, factor_set, selection = factor_case()
    diagnostic = preflight_factors(
        factor_set, selection, parameters("asymmetric-datum-flat")
    )
    with pytest.raises(ValidationError, match="frozen"):
        diagnostic.active_element_counts[0].count = 999


def test_factor_module_import_boundary() -> None:
    for module in (factor_models_module, factors_module):
        source = Path(module.__file__).read_text(encoding="ascii").lower()
        for prohibited in (
            "experiments",
            "onshape",
            "scansor.cli",
            "scansor.mapping_runs",
            "scipy",
            "optimizer",
            "pathlib",
        ):
            assert prohibited not in source
