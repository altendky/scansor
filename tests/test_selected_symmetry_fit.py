"""Imported joint recovery uses selected observations, not standalone fit gates."""

import io
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import numpy as np
import pytest

import experiments.selected_symmetry_fit as selected
from experiments.fit_initialization import RecoveryPolicy
from experiments.fit_relationship_component import (
    AxisInput,
    MirrorInput,
    PlaneInput,
    RadiusDistanceInput,
    RotationInput,
    fit_relationship_component,
)
from experiments.fit_solver import AttemptControl, AttemptLimits, FitCancelled
from experiments.nozzle_coaxial import FitSelection
from experiments.scan_workspace import ScanLimits, prepare_scan
from experiments.selected_symmetry_fit import (
    SelectedSurface,
    SelectedSymmetryGroup,
    SymmetryFitFailure,
    fit_selected_symmetry_group,
)
from experiments.support_intent import (
    PlaneSupport,
    PointSupport,
    SelectionSupport,
    support_record,
)
from tests.symmetry_fixture import RawSymmetryFixture, raw_symmetry_fixture


def group_for(
    fixture: RawSymmetryFixture,
    *,
    fixed: bool = False,
    fixed_clock: bool = False,
    reverse_alias: bool = False,
) -> SelectedSymmetryGroup:
    """Declare roles without using known member truth as numerical starts."""
    root = "central"
    common = "central_alias" if reverse_alias else root
    axes = {
        root: AxisInput(
            {
                "point_display": fixture.common_point.tolist(),
                "axis_display": fixture.common_axis.tolist(),
                "axis_is_directed": True,
            }
            if fixed
            else {},
            free=not fixed,
        )
    }
    if reverse_alias:
        axes[common] = AxisInput({}, free=False, source=root)
    surfaces: list[SelectedSurface] = []
    for name in ("main", "other", "cap", *fixture.members):
        kind = (
            "plane"
            if name == "cap"
            else ("cylinder" if name in {"main", "other"} else fixture.kind)
        )
        axis = common if name in {"main", "other", "cap"} else None
        if name in fixture.members and kind != "plane":
            axis = "@member:" + name
            axes[axis] = AxisInput({}, free=True)
        surfaces.append(
            SelectedSurface(
                FitSelection(
                    name, fixture.ids[name], kind, None, fixture.supports[name]
                ),
                axis=axis,
                construction="perpendicular_to_axis" if name == "cap" else None,
            )
        )
    planes = {}
    if fixture.mode == "mirror":
        planes["mirror"] = PlaneInput(
            {
                "plane_equation": [
                    *fixture.mirror_normal.tolist(),
                    float(fixture.mirror_normal @ fixture.common_point),
                ]
            }
            if fixed_clock
            else {},
            free=not fixed_clock,
            axis=common,
            construction="contains_axis",
        )
    members = fixture.members
    if reverse_alias and fixture.mode == "rotation":
        # This is the explicitly authored order for the reversed directed axis.
        members = (members[0], members[2], members[1])
    return SelectedSymmetryGroup(
        tuple(surfaces),
        common,
        axes,
        planes,
        rotations=(RotationInput(common, cast(tuple[str, str, str], members)),)
        if fixture.mode == "rotation"
        else (),
        mirrors=(MirrorInput("mirror", cast(tuple[str, str], members)),)
        if fixture.mode == "mirror"
        else (),
        common_axis_directed=fixed,
        axis_alias_signs={common: -1} if reverse_alias else {},
    )


def check_support(
    fixture: RawSymmetryFixture, name: str, surface: dict[str, Any]
) -> None:
    """Resolve authored support independently against the final published line."""
    support = fixture.supports[name]
    assert support is not None
    assert surface["support_intent"] == support_record(support)
    geometry = surface["analytic"]
    anchor = np.asarray(geometry["origin"])
    direction = np.asarray(geometry["axis"])
    if isinstance(support, SelectionSupport):
        heights = (fixture.workspace.local[fixture.ids[name]] - anchor) @ direction
        domain = np.array([heights.min(), heights.max()])
        endpoints = anchor + domain[:, None] * direction
    elif isinstance(support, PointSupport):
        assert direction @ support.direction > 0
        shift = (np.asarray(support.reference) - anchor) @ direction
        reference = anchor + shift * direction
        np.testing.assert_allclose(reference, anchor, rtol=0, atol=1e-10)
        domain = np.asarray(support.interval)
        endpoints = reference + domain[:, None] * direction
    else:
        assert isinstance(support, PlaneSupport)
        normal = np.asarray(support.normal)
        assert normal @ direction > 0
        shift = (support.offset - normal @ anchor) / (normal @ direction)
        intersection = anchor + shift * direction
        domain = np.asarray(support.interval) + shift
        endpoints = intersection + np.asarray(support.interval)[:, None] * direction
    published_domain = np.asarray(geometry["axial_domain"])
    np.testing.assert_allclose(published_domain, domain, rtol=0, atol=1e-10)
    np.testing.assert_allclose(
        anchor + published_domain[:, None] * direction,
        endpoints,
        rtol=0,
        atol=1e-10,
    )
    radii = geometry["radius"] + geometry["slope"] * published_domain
    assert np.isfinite(radii).all() and np.all(radii > 0)


def check_fixed(group: SelectedSymmetryGroup, result: dict[str, Any]) -> None:
    for key, axis in group.axes.items():
        if not axis.free and axis.source is None:
            for field in ("point_display", "axis_display"):
                np.testing.assert_array_equal(
                    result["axes"][key][field], axis.result[field]
                )
    for key, plane in group.planes.items():
        if not plane.free and plane.source is None:
            np.testing.assert_array_equal(
                result["planes"][key]["plane_equation"], plane.result["plane_equation"]
            )
            np.testing.assert_array_equal(
                result["equations"][key], plane.result["plane_equation"]
            )


def check_rows(fixture: RawSymmetryFixture, result: dict[str, Any]) -> None:
    np.testing.assert_array_equal(fixture.workspace.local, fixture.source_positions)
    np.testing.assert_allclose(
        fixture.workspace.data.weights, fixture.incident_weights, rtol=1e-14, atol=0
    )
    total, area = 0.0, 0.0
    assert set(result["surfaces"]) == set(fixture.ids)
    for name, ids in fixture.ids.items():
        surface = result["surfaces"][name]
        assert surface["ids"] == ids
        xyz, weights = fixture.workspace.local[ids], fixture.incident_weights[ids]
        assert np.ptp(weights) > 0
        if surface["kind"] == "plane":
            equation = np.asarray(surface["plane_equation"])
            residuals = xyz @ equation[:3] - equation[3]
        else:
            geometry = surface["analytic"]
            delta = xyz - np.asarray(geometry["origin"])
            direction = np.asarray(geometry["axis"])
            height = delta @ direction
            rho = np.linalg.norm(delta - height[:, None] * direction, axis=1)
            residuals = (
                rho - geometry["radius"] - geometry["slope"] * height
            ) / np.hypot(1, geometry["slope"])
            check_support(fixture, name, surface)
        assert len(surface["residuals"]) == len(ids)
        np.testing.assert_allclose(
            surface["residuals"], residuals, rtol=1e-10, atol=1e-12
        )
        objective = float(weights @ residuals**2)
        assert surface["weighted_rms"] ** 2 == pytest.approx(
            objective / weights.sum(), rel=1e-8, abs=1e-20
        )
        assert result["selected_area"][name] == pytest.approx(
            float(weights.sum()), rel=1e-14
        )
        total += objective
        area += float(weights.sum())
    assert result["fit"]["weighted_rms"] ** 2 == pytest.approx(
        total / area, rel=1e-8, abs=1e-20
    )
    _ = json.dumps(result, allow_nan=False)


def check_truth(fixture: RawSymmetryFixture, result: dict[str, Any]) -> None:
    # The independent fixture records actual float32 quantization, rather than
    # choosing a tolerance after observing a fit. These are well-conditioned
    # generated cases; this bound is not a physical scan acceptance criterion.
    tolerance = 32 * fixture.quantization_bound
    for name, truth in fixture.truth.items():
        surface = result["surfaces"][name]
        xyz = fixture.workspace.local[fixture.ids[name]]
        extent = max(float(np.max(np.linalg.norm(xyz - xyz.mean(axis=0), axis=1))), 1)
        if truth["kind"] == "plane":
            expected = np.asarray(truth["plane_equation"])
            actual = np.asarray(surface["plane_equation"])
            np.testing.assert_allclose(
                np.cross(actual[:3], expected[:3]), 0, atol=tolerance / extent
            )
            assert np.max(np.abs(xyz @ actual[:3] - actual[3])) <= tolerance
            continue
        geometry = surface["analytic"]
        anchor, direction = np.asarray(geometry["origin"]), np.asarray(geometry["axis"])
        expected_anchor, expected_direction = (
            np.asarray(truth["origin"]),
            np.asarray(truth["axis"]),
        )
        delta = anchor - expected_anchor
        assert (
            np.linalg.norm(delta - (delta @ expected_direction) * expected_direction)
            <= tolerance
        )
        np.testing.assert_allclose(
            np.cross(direction, expected_direction), 0, atol=tolerance / extent
        )
        sign = 1 if direction @ expected_direction >= 0 else -1
        assert sign * geometry["slope"] == pytest.approx(
            truth["slope"], abs=tolerance / extent
        )
        radius_at_reference = geometry["radius"] + geometry["slope"] * (
            (expected_anchor - anchor) @ direction
        )
        assert radius_at_reference == pytest.approx(truth["radius"], abs=tolerance)


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("oblique", [False, True])
@pytest.mark.parametrize("fixed", [False, True])
def test_raw_selected_symmetry_derives_all_starts_without_member_fits(
    kind: Any,
    mode: Any,
    oblique: bool,
    fixed: bool,
) -> None:
    fixture = raw_symmetry_fixture(kind, mode, oblique=oblique)
    group = group_for(fixture, fixed=fixed)
    before_axes = deepcopy(dict(group.axes))
    before_planes = deepcopy(dict(group.planes))
    result = fit_selected_symmetry_group(fixture.workspace, group)
    check_fixed(group, result)
    check_rows(fixture, result)
    check_truth(fixture, result)
    assert dict(group.axes) == before_axes and dict(group.planes) == before_planes
    assert result["initialization"]["recovery"]["selected_role"] == "fresh"


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_noisy_joint_keeps_complete_weighted_objective(kind: Any, mode: Any) -> None:
    fixture = raw_symmetry_fixture(kind, mode, oblique=True, noise=0.002)
    result = fit_selected_symmetry_group(fixture.workspace, group_for(fixture))
    check_rows(fixture, result)
    assert result["fit"]["weighted_rms"] > fixture.quantization_bound


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("misleading", [False, True])
def test_eligible_partial_patches_and_misleading_normals_recover(
    kind: Any,
    mode: Any,
    misleading: bool,
) -> None:
    fixture = raw_symmetry_fixture(
        kind, mode, oblique=True, partial=True, misleading_normals=misleading
    )
    result = fit_selected_symmetry_group(fixture.workspace, group_for(fixture))
    check_rows(fixture, result)
    check_truth(fixture, result)


def test_joint_only_collinear_members_do_not_require_standalone_plane_admission() -> (
    None
):
    fixture = raw_symmetry_fixture("plane", "rotation", joint_only=True)
    for name in fixture.members:
        points = fixture.workspace.local[fixture.ids[name]]
        assert np.linalg.matrix_rank(points - points.mean(axis=0), tol=1e-12) == 1
    result = fit_selected_symmetry_group(fixture.workspace, group_for(fixture))
    check_rows(fixture, result)
    check_truth(fixture, result)


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_fixed_reversed_directed_alias_preserves_authored_positive_order(
    kind: Any,
) -> None:
    fixture = raw_symmetry_fixture(kind, "rotation", oblique=True)
    group = group_for(fixture, fixed=True, reverse_alias=True)
    result = fit_selected_symmetry_group(fixture.workspace, group)
    check_fixed(group, result)
    np.testing.assert_array_equal(result["axis_display"], -fixture.common_axis)
    check_rows(fixture, result)
    check_truth(fixture, result)


def test_tangent_mirror_radius_distance_and_equal_radius_use_all_factor_weights() -> (
    None
):
    fixture = raw_symmetry_fixture("plane", "mirror", oblique=True, tangent_mirror=True)
    group = replace(
        group_for(fixture, fixed=True, fixed_clock=True),
        radii=(("main", "other"),),
        radius_distances=(RadiusDistanceInput("main", "member_0", "mirror"),),
    )
    result = fit_selected_symmetry_group(fixture.workspace, group)
    check_rows(fixture, result)
    measures: list[float] = []
    weights: list[float] = []
    for name in ("main", "other", *fixture.members):
        ids = fixture.ids[name]
        xyz = fixture.workspace.local[ids] - fixture.common_point
        if name in fixture.members:
            measures.extend(abs(xyz @ fixture.mirror_normal).tolist())
        else:
            transverse = (
                xyz - (xyz @ fixture.common_axis)[:, None] * fixture.common_axis
            )
            measures.extend(np.linalg.norm(transverse, axis=1).tolist())
        weights.extend(fixture.incident_weights[ids].tolist())
    expected = float(np.average(measures, weights=weights))
    radius = result["surfaces"]["main"]["analytic"]["radius"]
    assert radius == pytest.approx(expected, abs=1e-8)
    assert result["surfaces"]["other"]["analytic"]["radius"] == pytest.approx(
        radius, abs=1e-10
    )
    assert 2 < radius < 3.5


def test_unobserved_mirror_clock_is_not_regularized_to_a_start() -> None:
    fixture = raw_symmetry_fixture(
        "cylinder", "mirror", coaxial_members=True, support_kind="auto"
    )
    with pytest.raises(SymmetryFitFailure) as captured:
        _ = fit_selected_symmetry_group(
            fixture.workspace, group_for(fixture, fixed=True)
        )
    diagnostic = captured.value.diagnostic
    assert diagnostic["recovery"]["selected"] is None
    # Float32 departures from exact coaxial geometry can lift the clock's exact
    # nullspace into a full-rank but unacceptably ill-conditioned direction.
    assessed = [
        attempt
        for attempt in diagnostic["recovery"]["attempts"]
        if attempt["assessment"] is not None
    ]
    assert assessed
    for attempt in assessed:
        assert attempt["outcome"] == "invalid-result"
        assert attempt["assessment"]["valid"] is False
        assert attempt["assessment"]["rank"] > 0
        assert attempt["assessment"]["condition"] > 1e6
    _ = json.dumps(diagnostic, allow_nan=False)
    fixed_group = group_for(fixture, fixed=True, fixed_clock=True)
    result = fit_selected_symmetry_group(fixture.workspace, fixed_group)
    check_fixed(fixed_group, result)
    check_rows(fixture, result)


def test_poisoned_free_starts_and_stale_hint_do_not_replace_fresh_recovery() -> None:
    fixture = raw_symmetry_fixture("cone", "mirror", oblique=True)
    group = group_for(fixture)
    baseline = fit_selected_symmetry_group(fixture.workspace, group)
    axes = dict(group.axes)
    axes["central"] = AxisInput(
        {"point_display": [1e100, -1e100, 1e100], "axis_display": [float("nan"), 0, 0]},
        True,
    )
    planes = {
        "mirror": replace(
            group.planes["mirror"], result={"plane_equation": [1, 0, 0, 1e100]}
        )
    }
    poisoned = replace(group, axes=axes, planes=planes)
    result = fit_selected_symmetry_group(
        fixture.workspace, poisoned, hint_binding_sha256="0" * 64
    )

    def fresh(value: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            a
            for a in value["initialization"]["recovery"]["attempts"]
            if a["role"] == "fresh"
        ]

    assert fresh(result) == fresh(baseline)
    hints = [
        attempt
        for attempt in result["initialization"]["recovery"]["attempts"]
        if attempt["role"] == "hint"
    ]
    assert len(hints) == 1
    assert hints[0]["outcome"] == "screened-invalid-or-stale"
    assert hints[0]["evaluations"] == 0

    def without_screened_hint(value: dict[str, Any]) -> dict[str, Any]:
        copy = deepcopy(value)
        records = [
            copy["initialization"],
            copy["fit"]["initialization"],
            *(surface["initialization"] for surface in copy["surfaces"].values()),
        ]
        for record in records:
            record["recovery"]["attempts"] = [
                attempt
                for attempt in record["recovery"]["attempts"]
                if attempt["role"] != "hint"
            ]
        return copy

    assert without_screened_hint(result) == without_screened_hint(baseline)
    check_rows(fixture, result)


def test_renamed_identical_source_and_changed_policy_bind_only_semantic_inputs() -> (
    None
):
    fixture = raw_symmetry_fixture("cylinder", "rotation")
    group = group_for(fixture)
    first = fit_selected_symmetry_group(fixture.workspace, group)
    renamed = prepare_scan(
        io.BytesIO(fixture.raw),
        "renamed.ply",
        ScanLimits(4_000_000, 20_000, 20_000, 50_000_000),
    )
    replay = fit_selected_symmetry_group(renamed, group)
    assert replay["surfaces"] == first["surfaces"]
    assert replay["initialization"] == first["initialization"]
    changed = fit_selected_symmetry_group(
        renamed, group, policy=RecoveryPolicy(max_proposals=15)
    )
    assert (
        changed["initialization"]["recovery"]["binding_sha256"]
        != first["initialization"]["recovery"]["binding_sha256"]
    )


def test_attempts_share_one_control_and_obey_callback_limits_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = raw_symmetry_fixture("cylinder", "rotation")
    group = group_for(fixture)
    controls: list[AttemptControl] = []
    real = fit_relationship_component

    def observed(*args: Any, **kwargs: Any) -> Any:
        controls.append(kwargs["control"])
        return real(*args, **kwargs)

    monkeypatch.setattr(selected, "fit_relationship_component", observed)
    result = fit_selected_symmetry_group(fixture.workspace, group)
    attempts = result["initialization"]["recovery"]["attempts"]
    assert len({id(control) for control in controls}) == len(controls) <= 8
    assert all(control.evaluations <= 512 for control in controls)
    assert sum(a["role"] == "fresh" and a["evaluations"] > 0 for a in attempts) == len(
        controls
    )
    with pytest.raises(SymmetryFitFailure) as captured:
        _ = fit_selected_symmetry_group(
            fixture.workspace,
            group,
            policy=RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1)),
        )
    assert captured.value.diagnostic["recovery"]["selected"] is None
    assert all(
        a["evaluations"] <= 1 for a in captured.value.diagnostic["recovery"]["attempts"]
    )
    _ = json.dumps(captured.value.diagnostic, allow_nan=False)
    with pytest.raises(FitCancelled):
        _ = fit_selected_symmetry_group(
            fixture.workspace, group, cancelled=lambda: True
        )
    controls.clear()
    with pytest.raises(FitCancelled):
        _ = fit_selected_symmetry_group(
            fixture.workspace,
            group,
            cancelled=lambda: bool(controls and controls[-1].evaluations >= 3),
        )
    assert controls and controls[-1].evaluations == 3


@pytest.mark.parametrize("reversed_plane_alias", [False, True])
def test_signed_distance_recovers_free_clock_without_an_authored_start(
    reversed_plane_alias: bool,
) -> None:
    fixture = raw_symmetry_fixture("plane", "mirror", oblique=True, tangent_mirror=True)
    group = group_for(fixture, fixed=True)
    reference, sign = "mirror", 1
    if reversed_plane_alias:
        reference, sign = "mirror_alias", -1
        group = replace(
            group,
            planes={
                **group.planes,
                reference: PlaneInput({}, free=False, source="mirror"),
            },
            plane_alias_signs={reference: -1},
        )
    group = replace(
        group,
        radius_distances=(RadiusDistanceInput("main", "member_0", reference, sign),),
    )
    result = fit_selected_symmetry_group(fixture.workspace, group)
    assert result["initialization"]["recovery"]["selected_role"] == "fresh"
    assert all(
        attempt["role"] == "fresh"
        for attempt in result["initialization"]["recovery"]["attempts"]
    )
    equation = np.asarray(result["equations"][reference])
    np.testing.assert_allclose(sign * equation[:3], fixture.mirror_normal, atol=1e-7)
    if reversed_plane_alias:
        np.testing.assert_array_equal(
            equation, -np.asarray(result["equations"]["mirror"])
        )
    check_rows(fixture, result)
    check_truth(fixture, result)


def test_cone_equivalence_covers_selected_rows_outside_a_short_hard_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = raw_symmetry_fixture("cone", "rotation")
    predicates: list[Any] = []

    class Captured(Exception):
        pass

    def capture(problem: Any, *_args: Any, **_kwargs: Any) -> Any:
        predicates.append(problem.equivalent)
        raise Captured

    monkeypatch.setattr(selected, "recover_fit", capture)
    with pytest.raises(Captured):
        _ = fit_selected_symmetry_group(fixture.workspace, group_for(fixture))
    left: dict[str, Any] = {
        "surfaces": {
            key: {"plane_equation": value["plane_equation"]}
            if value["kind"] == "plane"
            else {"analytic": deepcopy(value)}
            for key, value in fixture.truth.items()
        },
        "equations": {},
    }
    first = left["surfaces"]["member_0"]["analytic"]
    first.update(radius=-1.0, slope=0.5, axial_domain=[10.0, 10.0 + 1e-8])
    right = deepcopy(left)
    second = right["surfaces"]["member_0"]["analytic"]
    second.update(radius=-1.1, slope=0.51)
    domain = np.asarray(first["axial_domain"])
    np.testing.assert_allclose(
        first["radius"] + first["slope"] * domain,
        second["radius"] + second["slope"] * domain,
        atol=2e-10,
        rtol=0,
    )
    assert predicates[0](left, deepcopy(left))
    assert not predicates[0](left, right)


def test_perpendicular_plane_uses_its_free_member_axis_without_a_hint() -> None:
    fixture = raw_symmetry_fixture("cylinder", "rotation")
    group = group_for(fixture, fixed=True)
    group = replace(
        group,
        surfaces=tuple(
            replace(surface, axis="@member:member_0")
            if surface.selection.id == "cap"
            else surface
            for surface in group.surfaces
        ),
    )
    result = fit_selected_symmetry_group(fixture.workspace, group)
    check_rows(fixture, result)
    check_fixed(group, result)
    assert result["initialization"]["recovery"]["selected_role"] == "fresh"
    np.testing.assert_allclose(
        np.cross(
            result["surfaces"]["cap"]["plane_equation"][:3],
            result["surfaces"]["member_0"]["analytic"]["axis"],
        ),
        0,
        atol=1e-10,
    )
    # This deliberately changes the declared cap relationship, so the original
    # fixture truth is no longer an accuracy oracle. All rows still contribute.
    assert result["surfaces"]["cap"]["weighted_rms"] > fixture.quantization_bound
    with pytest.raises(SymmetryFitFailure) as captured:
        _ = fit_selected_symmetry_group(
            fixture.workspace,
            group,
            policy=RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1)),
        )
    assert captured.value.diagnostic["recovery"]["selected"] is None
    assert all(
        attempt["evaluations"] <= 1
        for attempt in captured.value.diagnostic["recovery"]["attempts"]
    )


def test_algebraic_backend_failure_has_a_strict_json_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = raw_symmetry_fixture("cylinder", "rotation")

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise np.linalg.LinAlgError("simulated LAPACK nonconvergence")

    monkeypatch.setattr(np.linalg, "lstsq", unavailable)
    with pytest.raises(SymmetryFitFailure) as captured:
        _ = fit_selected_symmetry_group(fixture.workspace, group_for(fixture))
    assert captured.value.code == "numeric-failure"
    assert "LAPACK" in captured.value.diagnostic["message"]
    _ = json.dumps(captured.value.diagnostic, allow_nan=False)


def test_nonfinite_semantic_intent_is_rejected_with_a_strict_json_diagnostic() -> None:
    fixture = raw_symmetry_fixture("cylinder", "rotation")
    group = replace(group_for(fixture), semantic_intent={"invalid": float("nan")})
    with pytest.raises(SymmetryFitFailure) as captured:
        _ = fit_selected_symmetry_group(fixture.workspace, group)
    assert captured.value.code == "invalid-input"
    _ = json.dumps(captured.value.diagnostic, allow_nan=False)
