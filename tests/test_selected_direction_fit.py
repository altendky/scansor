"""Selected-data direction recovery keeps observed placements independent."""

from __future__ import annotations

import io
import struct
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from experiments.fit_initialization import RecoveryPolicy
from experiments.fit_relationship_component import (
    AxisInput,
    DirectionAlignmentInput,
    DirectionOperand,
    PlaneInput,
    PointInput,
)
from experiments.fit_solver import AttemptControl, AttemptLimits, FitCancelled
from experiments.nozzle_coaxial import FitSelection
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selected_direction_fit import (
    DirectionFitFailure,
    SelectedDirectionComponent,
    SelectedDirectionSurface,
    fit_selected_direction_component,
    private_direction_axis,
)
from experiments.selection_growth import fit_seed, surface_distance
from experiments.support_intent import (
    PlaneSupport,
    PointSupport,
    SelectionSupport,
    resolve_support,
    support_record,
)

Array = NDArray[np.float64]


def unit(value: Array) -> Array:
    return value / np.linalg.norm(value)


def basis(direction: Array) -> tuple[Array, Array]:
    first = unit(
        np.asarray(
            np.cross(direction, np.eye(3)[int(np.argmin(abs(direction)))]), dtype=float
        )
    )
    return first, np.asarray(np.cross(direction, first), dtype=float)


def lateral(
    direction: Array,
    center: Array,
    radius: float,
    *,
    slope: float = 0,
    noise: float = 0,
    ring: bool = False,
) -> tuple[Array, Array]:
    first, second = basis(direction)
    angle, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 16, endpoint=False),
        [0] if ring else np.linspace(-2, 3, 5),
    )
    radial = np.cos(angle)[..., None] * first + np.sin(angle)[..., None] * second
    perturb = noise * np.sin(3 * angle + 0.7 * height)
    points = (
        center
        + height[..., None] * direction
        + (radius + slope * height + perturb)[..., None] * radial
    )
    normals = (radial - slope * direction) / np.hypot(1, slope)
    return points.reshape(-1, 3), normals.reshape(-1, 3)


def plane(
    direction: Array, center: Array, *, line: bool = False, noise: float = 0
) -> tuple[Array, Array]:
    first, second = basis(direction)
    x, y = np.meshgrid(np.linspace(-2, 2, 6), [0] if line else np.linspace(-3, 3, 5))
    points = (
        center
        + x[..., None] * first
        + y[..., None] * second
        + (noise * np.sin(x + 2 * y))[..., None] * direction
    )
    return points.reshape(-1, 3), np.broadcast_to(direction, points.shape).reshape(
        -1, 3
    ).copy()


def workspace_for(
    patches: dict[str, tuple[Array, Array]],
) -> tuple[ScanWorkspace, dict[str, list[int]]]:
    positions: list[Array] = []
    normals: list[Array] = []
    faces: list[tuple[int, int, int]] = []
    ids: dict[str, list[int]] = {}
    for name, (points, directions) in patches.items():
        ids[name] = []
        for point, normal in zip(points, directions, strict=True):
            first, second = basis(normal)
            index = len(positions)
            ids[name].append(index)
            positions.extend(
                [
                    point,
                    point + (0.04 + 0.001 * (index % 7)) * first,
                    point + (0.07 + 0.002 * (index % 5)) * second,
                ]
            )
            normals.extend([normal, normal, normal])
            faces.append((index, index + 1, index + 2))
    rows = np.column_stack([np.asarray(positions), np.asarray(normals)])
    header = f"ply\nformat binary_little_endian 1.0\nelement vertex {len(rows)}\nproperty float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\nelement face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    raw = header.encode() + np.asarray(rows, dtype="<f4").tobytes()
    raw += b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    return prepare_scan(
        io.BytesIO(raw),
        "direction-selected.ply",
        ScanLimits(5_000_000, 20_000, 20_000, 100_000_000),
    ), ids


def member(
    name: str, ids: dict[str, list[int]], kind: str, **kwargs: Any
) -> SelectedDirectionSurface:
    return SelectedDirectionSurface(
        FitSelection(
            name,
            ids[name],
            kind,
            None,
            SelectionSupport() if kind in {"cylinder", "cone"} else None,
        ),
        **kwargs,
    )


def relation(
    left: str, right: str, *, right_plane: bool = False
) -> DirectionAlignmentInput:
    return DirectionAlignmentInput(
        DirectionOperand("axis", left),
        DirectionOperand("plane" if right_plane else "axis", right),
    )


def check_rows(
    workspace: ScanWorkspace, ids: dict[str, list[int]], output: dict[str, Any]
) -> None:
    total = area = 0.0
    for name, rows in ids.items():
        value = output["surfaces"][name]
        assert value["ids"] == rows
        weights = workspace.data.weights[rows]
        assert np.ptp(weights) > 0
        assert output["selected_area"][name] == pytest.approx(float(weights.sum()))
        residual = surface_distance(workspace.local[rows], value)[0]
        total += float(weights @ residual**2)
        area += float(weights.sum())
        assert value["weighted_rms"] == pytest.approx(
            float(np.sqrt(weights @ residual**2 / weights.sum())), abs=1e-10
        )
    assert output["weighted_rms"] == pytest.approx(
        float(np.sqrt(total / area)), abs=1e-10
    )
    assert output["initialization"]["observations"] == sum(map(len, ids.values()))
    assert output["session"]["source_sha256"] == workspace.source_sha256


@pytest.mark.parametrize("oblique", [False, True])
def test_offset_parallel_cylinders_and_plane_keep_private_positions_and_dimensions(
    oblique: bool,
) -> None:
    direction = unit(np.array([0.3, -0.4, 0.85])) if oblique else np.array([0.0, 0, 1])
    first, second = basis(direction)
    base = np.array([7.0, -4, 3])
    origins = [base, base + 6 * first + 2 * second]
    workspace, ids = workspace_for(
        {
            "a": lateral(direction, origins[0], 2, noise=0.002),
            "b": lateral(direction, origins[1], 3.5, noise=0.003),
            "cap": plane(direction, base + 8 * direction, noise=0.001),
        }
    )
    before = workspace.local.copy()
    a, b = private_direction_axis("a"), private_direction_axis("b")
    component = SelectedDirectionComponent(
        (
            member("a", ids, "cylinder", axis=a),
            member("b", ids, "cylinder", axis=b),
            member("cap", ids, "plane"),
        ),
        {
            a: AxisInput({}, True),
            b: AxisInput({}, True),
            "fixed": AxisInput(
                {"point_display": [100, -40, 20], "axis_display": direction.tolist()},
                False,
            ),
        },
        (relation(a, "fixed"), relation(b, a), relation(a, "cap", right_plane=True)),
    )
    output = fit_selected_direction_component(workspace, component)
    for name, origin, radius in zip(("a", "b"), origins, (2, 3.5), strict=True):
        geometry = output["surfaces"][name]["analytic"]
        axis = np.asarray(geometry["axis"])
        assert abs(float(axis @ direction)) > 1 - 1e-12
        assert (
            np.linalg.norm(np.cross(np.asarray(geometry["origin"]) - origin, direction))
            < 0.005
        )
        assert geometry["radius"] == pytest.approx(radius, abs=0.005)
    assert (
        np.linalg.norm(
            np.cross(
                np.asarray(output["axes"][a]["point_display"])
                - output["axes"][b]["point_display"],
                direction,
            )
        )
        > 5
    )
    assert output["surfaces"]["cap"]["plane_equation"][3] == pytest.approx(
        float(direction @ (base + 8 * direction)), abs=0.01
    )
    np.testing.assert_array_equal(workspace.local, before)
    check_rows(workspace, ids, output)


def test_free_cylinder_and_plane_both_influence_shared_direction_without_shared_offset() -> (
    None
):
    d = unit(np.array([0.2, -0.3, 1.0]))
    u, _ = basis(d)
    tilted = unit(d + 0.06 * u)
    base = np.array([3.0, -2, 5])
    workspace, ids = workspace_for(
        {
            "side": lateral(d, base, 2, noise=0.004),
            "cap": plane(tilted, base + 9 * d, noise=0.002),
        }
    )
    axis = private_direction_axis("side")
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (member("side", ids, "cylinder", axis=axis), member("cap", ids, "plane")),
            {axis: AxisInput({}, True)},
            (relation(axis, "cap", right_plane=True),),
        ),
    )
    fitted = np.asarray(output["surfaces"]["side"]["analytic"]["axis"])
    normal = np.asarray(output["surfaces"]["cap"]["plane_equation"][:3])
    assert abs(float(fitted @ normal)) > 1 - 1e-12
    assert np.linalg.norm(np.cross(fitted, d)) > 0.002
    assert np.linalg.norm(np.cross(fitted, tilted)) > 0.002
    assert output["weighted_rms"] > 0.005
    assert (
        abs(
            float(normal @ (base + 9 * d))
            - output["surfaces"]["cap"]["plane_equation"][3]
        )
        < 0.005
    )
    check_rows(workspace, ids, output)


def test_joint_recovers_collinear_plane_without_requiring_standalone_plane() -> None:
    d = unit(np.array([0.3, 0.4, 1.0]))
    center = np.array([2.0, -4, 6])
    workspace, ids = workspace_for(
        {"side": lateral(d, center, 2), "weak": plane(d, center + 7 * d, line=True)}
    )
    with pytest.raises(ValueError):
        _ = fit_seed(
            workspace.local[ids["weak"]],
            workspace.data.weights[ids["weak"]],
            workspace.data.normals[ids["weak"]] @ workspace.frame,
            "plane",
            None,
            None,
        )
    axis = private_direction_axis("side")
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (member("side", ids, "cylinder", axis=axis), member("weak", ids, "plane")),
            {axis: AxisInput({}, True)},
            (relation(axis, "weak", right_plane=True),),
        ),
    )
    assert output["weighted_rms"] < 1e-6
    assert (
        abs(float(np.asarray(output["surfaces"]["weak"]["plane_equation"][:3]) @ d))
        > 1 - 1e-10
    )
    check_rows(workspace, ids, output)


def test_tilted_reversed_cone_and_cylinder_preserve_explicit_support() -> None:
    d = unit(np.array([-0.4, 0.7, 0.6]))
    u, _ = basis(d)
    center = np.array([4.0, -3, 8])
    workspace, ids = workspace_for(
        {
            "cone": lateral(d, center, 2.5, slope=0.25),
            "cylinder": lateral(d, center + 5 * u, 1.75),
        }
    )
    cone_support = PointSupport(tuple(center), tuple(-d), (-3.5, 2.5))
    cylinder_support = PlaneSupport(tuple(d), float(d @ center), (-2.5, 3.5))
    a, b = private_direction_axis("cone"), private_direction_axis("cylinder")
    cone = replace(
        member("cone", ids, "cone", axis=a),
        selection=FitSelection("cone", ids["cone"], "cone", None, cone_support),
    )
    cylinder = replace(
        member("cylinder", ids, "cylinder", axis=b),
        selection=FitSelection(
            "cylinder", ids["cylinder"], "cylinder", None, cylinder_support
        ),
    )
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (cone, cylinder),
            {a: AxisInput({}, True), b: AxisInput({}, True)},
            (relation(a, b),),
        ),
    )
    for name, support in [("cone", cone_support), ("cylinder", cylinder_support)]:
        value = output["surfaces"][name]
        geometry = value["analytic"]
        assert value["support_intent"] == support_record(support)
        resolved = resolve_support(
            support,
            geometry["origin"],
            geometry["axis"],
            workspace.local[ids[name]],
            reference=output["initialization"]["origin"],
        )
        np.testing.assert_allclose(geometry["axial_domain"], resolved.domain, atol=1e-9)
        assert np.all(
            geometry["radius"]
            + geometry["slope"] * np.asarray(geometry["axial_domain"])
            > 0
        )
    assert (
        float(np.asarray(output["surfaces"]["cone"]["analytic"]["axis"]) @ -d)
        > 1 - 1e-10
    )
    assert output["surfaces"]["cone"]["analytic"]["slope"] == pytest.approx(
        -0.25, abs=1e-6
    )
    check_rows(workspace, ids, output)


def test_existing_plane_and_equal_radius_factors_compose_in_same_selected_solve() -> (
    None
):
    d = unit(np.array([0.2, 0.3, 1.0]))
    u, _ = basis(d)
    workspace, ids = workspace_for(
        {
            "a": lateral(d, np.zeros(3), 2),
            "b": lateral(d, 7 * u, 3),
            "cap": plane(d, 6 * d),
            "other": plane(d, 10 * d),
        }
    )
    a, b = private_direction_axis("a"), private_direction_axis("b")
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            tuple(
                member(
                    name,
                    ids,
                    "cylinder" if name in {"a", "b"} else "plane",
                    **({"axis": a if name == "a" else b} if name in {"a", "b"} else {}),
                )
                for name in ids
            ),
            {a: AxisInput({}, True), b: AxisInput({}, True)},
            (relation(a, b), relation(a, "cap", right_plane=True)),
            relationships=(("parallel", ("cap", "other")),),
            radii=(("a", "b"),),
        ),
    )
    radius_a = output["surfaces"]["a"]["analytic"]["radius"]
    radius_b = output["surfaces"]["b"]["analytic"]["radius"]
    assert radius_a == pytest.approx(radius_b, abs=1e-10)
    assert 2.1 < radius_a < 2.9
    na, nb = (
        np.asarray(output["surfaces"][key]["plane_equation"][:3])
        for key in ("cap", "other")
    )
    assert abs(float(na @ nb)) > 1 - 1e-12
    check_rows(workspace, ids, output)


def test_direction_only_datums_keep_authored_unused_position_and_world_offset() -> None:
    d = unit(np.array([0.2, 0.3, 1.0]))
    workspace, ids = workspace_for(
        {"side": lateral(d, np.array([100.0, -200, 300]), 2)}
    )
    axis = private_direction_axis("side")
    anchor = [500.0, -40, 900]
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (member("side", ids, "cylinder", axis=axis),),
            {
                axis: AxisInput({}, True),
                "direction-only": AxisInput(
                    {"point_display": anchor, "axis_display": [1, 0, 0]},
                    True,
                    orientation_free=True,
                    position_free=False,
                ),
            },
            (
                relation(axis, "direction-only"),
                relation(axis, "normal-only", right_plane=True),
            ),
            planes={
                "normal-only": PlaneInput(
                    {"plane_equation": [0, 1, 0, 37]},
                    True,
                    orientation_free=True,
                    offset_free=False,
                )
            },
        ),
    )
    np.testing.assert_allclose(
        output["axes"]["direction-only"]["point_display"], anchor, atol=1e-10, rtol=0
    )
    assert output["planes"]["normal-only"]["plane_equation"][3] == pytest.approx(
        37, abs=1e-10
    )
    assert (
        abs(float(np.asarray(output["axes"]["direction-only"]["axis_display"]) @ d))
        > 1 - 1e-10
    )
    assert (
        abs(
            float(np.asarray(output["planes"]["normal-only"]["plane_equation"][:3]) @ d)
        )
        > 1 - 1e-10
    )
    check_rows(workspace, ids, output)


def test_existing_sphere_center_and_equal_radius_factor_compose_without_standalone_gate() -> (
    None
):
    d = np.array([0.0, 0, 1])
    angle, elevation = np.meshgrid(
        np.linspace(0, 2 * np.pi, 12, endpoint=False), np.linspace(-1, 1, 5)
    )
    normals = np.stack(
        [
            np.cos(elevation) * np.cos(angle),
            np.cos(elevation) * np.sin(angle),
            np.sin(elevation),
        ],
        axis=-1,
    ).reshape(-1, 3)
    center = np.array([8.0, 4, 3])
    workspace, ids = workspace_for(
        {
            "a": lateral(d, np.zeros(3), 2),
            "cap": plane(d, 6 * d),
            "sphere": (center + 3 * normals, normals),
        }
    )
    axis = private_direction_axis("a")
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (
                member("a", ids, "cylinder", axis=axis),
                member("cap", ids, "plane"),
                member("sphere", ids, "sphere", point="center"),
            ),
            {axis: AxisInput({}, True)},
            (relation(axis, "cap", right_plane=True),),
            centers={"center": PointInput({}, True)},
            radii=(("a", "sphere"),),
        ),
    )
    assert output["surfaces"]["a"]["analytic"]["radius"] == pytest.approx(
        output["surfaces"]["sphere"]["parameters"][3], abs=1e-10
    )
    assert 2 < output["surfaces"]["sphere"]["parameters"][3] < 3
    assert (
        np.linalg.norm(np.asarray(output["points"]["center"]["point_display"]) - center)
        < 0.02
    )
    check_rows(workspace, ids, output)


def test_cancellation_during_real_callbacks_is_not_a_recovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = np.array([0.0, 0, 1])
    workspace, ids = workspace_for(
        {"a": lateral(d, np.zeros(3), 2), "cap": plane(d, 5 * d)}
    )
    axis = private_direction_axis("a")
    component = SelectedDirectionComponent(
        (member("a", ids, "cylinder", axis=axis), member("cap", ids, "plane")),
        {axis: AxisInput({}, True)},
        (relation(axis, "cap", right_plane=True),),
    )
    before = [m.selection.ids.copy() for m in component.surfaces]
    calls = 0
    original = AttemptControl.call

    def call(self: AttemptControl, callback: Any, values: Any) -> Any:
        nonlocal calls
        result = original(self, callback, values)
        calls += 1
        return result

    monkeypatch.setattr(AttemptControl, "call", call)
    with pytest.raises(FitCancelled):
        _ = fit_selected_direction_component(
            workspace, component, cancelled=lambda: calls > 0
        )
    assert calls > 0
    assert [m.selection.ids for m in component.surfaces] == before
    with pytest.raises(FitCancelled):
        _ = fit_selected_direction_component(
            workspace, component, cancelled=lambda: True
        )


@pytest.mark.parametrize("plane_operand", [False, True])
def test_datum_only_direction_solve_does_not_invent_observations_or_recovery(
    plane_operand: bool,
) -> None:
    workspace, _ = workspace_for(
        {"unused": lateral(np.array([0.0, 0, 1]), np.zeros(3), 2)}
    )
    fixed = AxisInput({"point_display": [20, 30, 40], "axis_display": [0, 0, 1]}, False)
    free = AxisInput(
        {"point_display": [4, 5, 6], "axis_display": [1, 0, 0]},
        True,
        orientation_free=True,
        position_free=False,
    )
    component = SelectedDirectionComponent(
        (),
        {"fixed": fixed, **({} if plane_operand else {"free": free})},
        (
            relation(
                "fixed", "plane" if plane_operand else "free", right_plane=plane_operand
            ),
        ),
        planes={
            "plane": PlaneInput(
                {"plane_equation": [1, 0, 0, 7]},
                True,
                orientation_free=True,
                offset_free=False,
            )
        }
        if plane_operand
        else {},
    )
    output = fit_selected_direction_component(workspace, component)
    assert output["surfaces"] == {}
    assert output["selected_area"] == {}
    assert output["initialization"]["initializer"] == "authored-datums"
    assert output["initialization"]["observations"] == 0
    assert "recovery" not in output["initialization"]
    if plane_operand:
        equation = output["planes"]["plane"]["plane_equation"]
        assert abs(equation[2]) == pytest.approx(1)
        assert equation[3] == pytest.approx(7)
    else:
        np.testing.assert_allclose(
            output["axes"]["free"]["point_display"], [4, 5, 6], atol=1e-12
        )
        assert abs(output["axes"]["free"]["axis_display"][2]) == pytest.approx(1)


def test_datum_only_both_free_directions_report_real_unconstrained_freedom() -> None:
    workspace, _ = workspace_for(
        {"unused": lateral(np.array([0.0, 0, 1]), np.zeros(3), 2)}
    )
    component = SelectedDirectionComponent(
        (),
        {
            key: AxisInput(
                {"point_display": [0, 0, 0], "axis_display": [0, 0, 1]},
                True,
                orientation_free=True,
                position_free=False,
            )
            for key in ("a", "b")
        },
        (relation("a", "b"),),
    )
    with pytest.raises(DirectionFitFailure) as error:
        _ = fit_selected_direction_component(workspace, component)
    assert error.value.code == "rank-deficient"


def test_datum_only_evaluation_budget_remains_bounded_and_typed() -> None:
    workspace, _ = workspace_for(
        {"unused": lateral(np.array([0.0, 0, 1]), np.zeros(3), 2)}
    )
    component = SelectedDirectionComponent(
        (),
        {
            "fixed": AxisInput(
                {"point_display": [0, 0, 0], "axis_display": [0, 0, 1]}, False
            ),
            "free": AxisInput(
                {"point_display": [0, 0, 0], "axis_display": [0.5, 0, 1]},
                True,
                orientation_free=True,
                position_free=False,
            ),
        },
        (relation("fixed", "free"),),
    )
    with pytest.raises(DirectionFitFailure) as error:
        _ = fit_selected_direction_component(
            workspace,
            component,
            policy=RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1)),
        )
    assert error.value.code == "search-exhausted"
    assert error.value.diagnostic["initializer"] == "authored-datums"


def test_invalid_overlap_has_typed_diagnostic_and_does_not_mutate_members() -> None:
    d = np.array([0.0, 0, 1])
    workspace, ids = workspace_for({"a": lateral(d, np.zeros(3), 2)})
    axis = private_direction_axis("a")
    a = member("a", ids, "cylinder", axis=axis)
    b = replace(
        a, selection=replace(a.selection, id="b"), axis=private_direction_axis("b")
    )
    assert b.axis is not None
    with pytest.raises(DirectionFitFailure) as error:
        _ = fit_selected_direction_component(
            workspace,
            SelectedDirectionComponent(
                (a, b),
                {axis: AxisInput({}, True), b.axis: AxisInput({}, True)},
                (relation(axis, str(b.axis)),),
            ),
        )
    assert error.value.code == "selection-overlap"
    assert error.value.diagnostic["kind"] == "direction_fit_failure"
    assert a.selection.ids == ids["a"]


def test_recovery_binding_tracks_fixed_geometry_but_not_solver_metadata() -> None:
    d = np.array([0.0, 0, 1])
    workspace, ids = workspace_for({"side": lateral(d, np.array([3.0, 4, 5]), 2)})
    axis = private_direction_axis("side")
    component = SelectedDirectionComponent(
        (member("side", ids, "cylinder", axis=axis),),
        {
            axis: AxisInput({}, True),
            "fixed": AxisInput(
                {
                    "point_display": [0, 0, 0],
                    "axis_display": d.tolist(),
                    "solver": {"irrelevant_condition": float("inf")},
                },
                False,
            ),
        },
        (relation(axis, "fixed"),),
    )
    original = fit_selected_direction_component(workspace, component)
    record = component.axes["fixed"]
    metadata = fit_selected_direction_component(
        workspace,
        replace(
            component,
            axes={
                **component.axes,
                "fixed": replace(
                    record,
                    result={**record.result, "solver": {"irrelevant_condition": 3}},
                ),
            },
        ),
    )
    changed = fit_selected_direction_component(
        workspace,
        replace(
            component,
            axes={
                **component.axes,
                "fixed": replace(
                    record, result={**record.result, "point_display": [50, 40, 30]}
                ),
            },
        ),
    )
    key = "binding_sha256"
    assert original["initialization"][key] == metadata["initialization"][key]
    assert original["initialization"][key] != changed["initialization"][key]
    assert len(original["initialization"][key]) == 64
    attempts = original["initialization"]["recovery"]["attempts"]
    assert 1 <= len(attempts) <= 8
    assert all(
        attempt["candidate"]["binding_sha256"] == original["initialization"][key]
        for attempt in attempts
    )
    assert original["surfaces"]["side"]["ids"] == ids["side"]


def test_reversed_provider_alias_keeps_its_declared_sense_without_aliasing_private_lines() -> (
    None
):
    d = unit(np.array([0.2, 0.3, 1.0]))
    workspace, ids = workspace_for(
        {"side": lateral(d, np.zeros(3), 2), "cap": plane(d, 6 * d)}
    )
    axis = private_direction_axis("side")
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (member("side", ids, "cylinder", axis=axis), member("cap", ids, "plane")),
            {
                axis: AxisInput({}, True),
                "reversed-source": AxisInput(
                    {"direction_reversed": True}, False, source=axis
                ),
            },
            (relation("reversed-source", "cap", right_plane=True),),
        ),
    )
    np.testing.assert_allclose(
        output["axes"]["reversed-source"]["axis_display"],
        -np.asarray(output["axes"][axis]["axis_display"]),
        atol=1e-12,
    )
    check_rows(workspace, ids, output)


@pytest.mark.parametrize("reverse", [False, True])
def test_perpendicular_parent_sense_overrides_opposing_finite_plane_hint(
    reverse: bool,
) -> None:
    direction = np.array([0.0, 0, 1])
    workspace, ids = workspace_for({"side": lateral(direction, np.zeros(3), 2)})
    key = private_direction_axis("side")
    parent_direction = -direction if reverse else direction
    output = fit_selected_direction_component(
        workspace,
        SelectedDirectionComponent(
            (member("side", ids, "cylinder", axis=key),),
            {
                key: AxisInput({}, True),
                "parent": AxisInput({"direction_reversed": reverse}, False, source=key),
                "fixed": AxisInput(
                    {"point_display": [0, 0, 0], "axis_display": direction.tolist()},
                    False,
                ),
            },
            (relation(key, "fixed"),),
            planes={
                "datum": PlaneInput(
                    {"plane_equation": [*(-parent_direction), 2]},
                    True,
                    axis="parent",
                    construction="perpendicular_to_axis",
                    offset_from_axis=2,
                )
            },
        ),
    )
    parent = output["axes"]["parent"]
    datum = output["planes"]["datum"]
    normal = np.asarray(datum["plane_equation"][:3])
    np.testing.assert_allclose(normal, parent["axis_display"], atol=1e-10)
    point = np.asarray(datum["point_display"])
    anchor = np.asarray(parent["point_display"])
    assert normal @ (point - anchor) == pytest.approx(2.0, abs=1e-10)
    np.testing.assert_allclose(point, anchor + 2 * parent_direction, atol=1e-10)
    assert output["surfaces"]["side"]["analytic"]["radius"] == pytest.approx(
        2.0, abs=1e-6
    )
    check_rows(workspace, ids, output)
