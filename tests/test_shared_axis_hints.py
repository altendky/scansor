"""Shared-axis numerical hints cannot replace fresh data or hard reference roles."""

import hashlib
import io
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

import experiments.nozzle_coaxial as module
from experiments.fit_solver import AttemptControl
from experiments.mesh_cylinder_fit import CylinderFitFailure
from experiments.nozzle_coaxial import FitSelection, ReferencePlaneGroup
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from tests.scan_source_fixture import read_source_bytes

# Deliberately reuse the existing independent fixture without editing its owner.
from tests.test_relationship_composition import (
    _generic_mixed_group,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_scan_cylinder_fit import (
    AXES,
    ORIGIN,
    cylinder_grid,
    strict_ply,
    transverse_frame,
)

Array = NDArray[np.float64]


@dataclass
class Group:
    workspace: ScanWorkspace
    sides: list[FitSelection]
    planes: list[FitSelection]
    references: tuple[ReferencePlaneGroup, ...]
    anchor: Array
    baseline: dict[str, Any]


def cylinder_group() -> tuple[ScanWorkspace, list[FitSelection], list[FitSelection]]:
    """The existing horizontal inner/outer-cylinder and cap fixture, unchanged."""
    points, normals, triangles = cylinder_grid(AXES[0])
    axis, first, second = transverse_frame(AXES[0])
    heights = (points - ORIGIN) @ axis
    radial = points - ORIGIN - heights[:, None] * axis
    outer = ORIGIN + heights[:, None] * axis + 1.6 * radial
    u, v = np.meshgrid(np.linspace(-1, 1, 5), np.linspace(-1, 1, 5))
    cap = ORIGIN + 4 * axis + u.ravel()[:, None] * first + v.ravel()[:, None] * second
    cap_triangles: list[tuple[int, int, int]] = []
    for row in range(4):
        for col in range(4):
            a = 5 * row + col
            cap_triangles.extend([(a, a + 1, a + 5), (a + 1, a + 6, a + 5)])
    count = len(points)
    workspace = prepare_scan(
        io.BytesIO(
            strict_ply(
                np.vstack((points, outer, cap)),
                np.vstack((normals, normals, np.tile(axis, (len(cap), 1)))),
                np.vstack(
                    (
                        triangles,
                        triangles + count,
                        np.asarray(cap_triangles) + 2 * count,
                    )
                ),
            )
        ),
        "joint-cylinders-and-cap.ply",
        ScanLimits(200_000, 2_000, 4_000, 10_000_000),
    )
    return (
        workspace,
        [
            FitSelection("inner", list(range(count)), "cylinder", None),
            FitSelection("outer", list(range(count, 2 * count)), "cylinder", None),
        ],
        [
            FitSelection(
                "cap", list(range(2 * count, 2 * count + len(cap))), "plane", None
            )
        ],
    )


def fit(group: Group, record: Any = None, **kwargs: Any) -> dict[str, Any]:
    return module.fit_group(
        group.workspace,
        group.sides,
        group.planes,
        axis_initial=record,
        reference_plane_groups=group.references,
        **kwargs,
    )


@pytest.fixture(scope="module", params=["cylinder", "mixed"])
def group(request: pytest.FixtureRequest) -> Group:
    if request.param == "cylinder":
        workspace, sides, planes = cylinder_group()
        result = Group(workspace, sides, planes, (), ORIGIN.copy(), {})
    else:
        workspace, sides, planes, references, anchor = _generic_mixed_group(AXES[0])
        result = Group(workspace, sides, planes, references, anchor, {})
    result.baseline = fit(result)
    assert result.baseline["fit"]["weighted_rms"] < 2e-5
    return result


def recovery(result: dict[str, Any]) -> dict[str, Any]:
    return result["fit"]["initialization"]["recovery"]


def without_hint(result: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(result)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "attempts" in value and "selected_role" in value:
                value["attempts"] = [
                    a for a in value["attempts"] if a["role"] != "hint"
                ]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


def fingerprint(group: Group, record: Any) -> str:
    digest = hashlib.sha256(read_source_bytes(group.workspace))
    for array in (
        group.workspace.local,
        group.workspace.data.normals,
        group.workspace.data.original_normals,
        group.workspace.data.triangles,
        group.workspace.data.weights,
    ):
        digest.update(array.tobytes())
    digest.update(repr((group.sides, group.planes, group.references, record)).encode())
    return digest.hexdigest()


def observe_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> list[AttemptControl]:
    original = AttemptControl.call
    controls: list[AttemptControl] = []

    def call(
        self: AttemptControl, callback: Callable[[Array], Any], values: Array
    ) -> Any:
        if self not in controls:
            controls.append(self)
        return original(self, callback, values)

    monkeypatch.setattr(AttemptControl, "call", call)
    return controls


INVALID_DIRECTIONS = [
    pytest.param({}, id="empty-record"),
    pytest.param({"point_display": [0, 0, 0]}, id="missing-direction"),
    pytest.param({"axis_display": None}, id="null"),
    pytest.param({"axis_display": [0, 0]}, id="short"),
    pytest.param({"axis_display": [[1, 0, 0]]}, id="nested"),
    pytest.param({"axis_display": [True, 0, 0]}, id="bool"),
    pytest.param({"axis_display": ["1", 0, 0]}, id="string"),
    pytest.param({"axis_display": [10**1000, 0, 0]}, id="huge-integer"),
    pytest.param({"axis_display": [object(), 0, 0]}, id="object"),
    pytest.param({"axis_display": [1j, 0, 0]}, id="complex"),
    pytest.param({"axis_display": [np.nan, 0, 0]}, id="nan"),
    pytest.param({"axis_display": [np.inf, 0, 0]}, id="infinity"),
    pytest.param({"axis_display": [0, 0, 0]}, id="zero"),
    pytest.param({"axis_display": [1e308, 1e308, 1e308]}, id="overflow"),
    pytest.param({"axis_display": [1e-300, 0, 0]}, id="underflow"),
]


@pytest.mark.parametrize("record", INVALID_DIRECTIONS)
def test_unusable_free_direction_preserves_fresh_fit_and_all_inputs(
    group: Group, record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    record = deepcopy(record)
    before = fingerprint(group, record)
    controls = observe_callbacks(monkeypatch)
    result = fit(group, record)
    assert without_hint(result) == group.baseline
    hints = [a for a in recovery(result)["attempts"] if a["role"] == "hint"]
    assert len(hints) == 1
    assert hints[0]["outcome"] == "screened-invalid-or-stale"
    assert hints[0]["evaluations"] == 0
    assert hints[0]["assessment"] is None and hints[0]["solver"] is None
    assert sum(c.evaluations for c in controls) == sum(
        a["evaluations"] for a in recovery(group.baseline)["attempts"]
    )
    assert all(c.evaluations <= 512 for c in controls)
    assert fingerprint(group, record) == before
    _ = json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("reverse", [False, True])
def test_screened_geometry_retains_explicit_reversal_and_role_binding(
    group: Group, reverse: bool
) -> None:
    record = {"axis_display": [0, 0, 0], "direction_reversed": reverse}
    result = fit(group, record)
    baseline_direction = np.asarray(group.baseline["axis_display"])
    canonical = baseline_direction * (
        1 if baseline_direction[int(np.argmax(np.abs(baseline_direction)))] >= 0 else -1
    )
    np.testing.assert_allclose(
        result["axis_display"], -canonical if reverse else canonical
    )
    for name, surface in result["surfaces"].items():
        assert {k: v for k, v in surface.items() if k != "initialization"} == {
            k: v
            for k, v in group.baseline["surfaces"][name].items()
            if k != "initialization"
        }
    assert result["fit"]["weighted_rms"] == group.baseline["fit"]["weighted_rms"]
    if reverse:
        assert (
            recovery(result)["binding_sha256"]
            != recovery(group.baseline)["binding_sha256"]
        )
    else:
        assert without_hint(result) == group.baseline


@pytest.mark.parametrize("reverse", [False, True])
def test_valid_directed_reference_and_unused_free_point_keep_existing_semantics(
    group: Group, reverse: bool
) -> None:
    direction = [-1.0, 0.0, 0.0] if reverse else [1.0, 0.0, 0.0]
    record = {"axis_display": direction, "direction_reversed": reverse}
    direction_only = fit(group, record)
    distant = fit(group, {**record, "point_display": [1e100, -1e100, 1e100]})
    assert distant == direction_only
    np.testing.assert_allclose(distant["axis_display"], direction, atol=2e-5)
    assert distant["fit"]["weighted_rms"] < 2e-5
    opposite = fit(group, {"axis_display": [-v for v in direction]})
    assert recovery(opposite)["binding_sha256"] != recovery(distant)["binding_sha256"]


def test_recipe_binding_preserves_authored_roles_without_hashing_evaluated_starts(
    group: Group,
) -> None:
    intent = {"recipe": {"axis": {"initial_parameters": [0, 0, 0, 0]}}, "role": "free"}
    baseline = fit(group, semantic_intent=intent)
    for record in ({}, {"axis_display": [0, 0, -1]}, {"axis_display": [np.nan, 0, 0]}):
        result = fit(group, record, semantic_intent=intent)
        assert (
            recovery(result)["binding_sha256"] == recovery(baseline)["binding_sha256"]
        )
    changed = fit(
        group, semantic_intent={"recipe": {"axis": {"direction_reversed": True}}}
    )
    assert recovery(changed)["binding_sha256"] != recovery(baseline)["binding_sha256"]


@pytest.mark.parametrize("record", INVALID_DIRECTIONS)
@pytest.mark.parametrize("recipe_bound", [False, True])
def test_invalid_fixed_direction_is_typed_before_callbacks(
    group: Group,
    record: dict[str, Any],
    recipe_bound: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {"point_display": group.anchor.tolist(), **deepcopy(record)}
    controls = observe_callbacks(monkeypatch)
    before = fingerprint(group, record)
    with pytest.raises(CylinderFitFailure) as caught:
        _ = module.fit_fixed_axis_group(
            group.workspace,
            group.sides,
            group.planes,
            record,
            semantic_intent={"recipe": {}} if recipe_bound else None,
        )
    assert caught.value.code == "invalid-reference"
    assert controls == []
    assert fingerprint(group, record) == before
    _ = json.dumps(caught.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "point",
    [
        None,
        [0, 0],
        [[0, 0, 0]],
        [True, 0, 0],
        ["0", 0, 0],
        [np.nan, 0, 0],
        [np.inf, 0, 0],
    ],
)
@pytest.mark.parametrize("recipe_bound", [False, True])
def test_invalid_fixed_point_is_typed_before_callbacks(
    group: Group, point: Any, recipe_bound: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    controls = observe_callbacks(monkeypatch)
    record = {"axis_display": [1.0, 0, 0], "point_display": point}
    before = fingerprint(group, record)
    with pytest.raises(CylinderFitFailure) as caught:
        _ = module.fit_fixed_axis_group(
            group.workspace,
            group.sides,
            group.planes,
            record,
            semantic_intent={"recipe": {}} if recipe_bound else None,
        )
    assert caught.value.code == "invalid-reference" and controls == []
    assert fingerprint(group, record) == before
    _ = json.dumps(caught.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("record", [None, [], np.zeros(7), {"axis_display": [1, 0, 0]}])
def test_missing_fixed_geometry_never_falls_back_to_data(
    group: Group, record: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    controls = observe_callbacks(monkeypatch)
    with pytest.raises(CylinderFitFailure) as caught:
        _ = module.fit_fixed_axis_group(
            group.workspace, group.sides, group.planes, record
        )
    assert caught.value.code == "invalid-reference" and controls == []


@pytest.mark.parametrize("role", [None, 0, 1, "false", np.bool_(True)])
@pytest.mark.parametrize("fixed", [False, True])
def test_invalid_reference_role_is_not_coerced_or_screened_as_a_hint(
    group: Group, role: Any, fixed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {
        "point_display": group.anchor.tolist(),
        "axis_display": [1, 0, 0],
        "direction_reversed": role,
    }
    controls = observe_callbacks(monkeypatch)
    with pytest.raises(CylinderFitFailure) as caught:
        if fixed:
            _ = module.fit_fixed_axis_group(
                group.workspace, group.sides, group.planes, record
            )
        else:
            _ = fit(group, record)
    assert caught.value.code == "invalid-reference" and controls == []


def test_valid_offset_fixed_axis_remains_exact_when_it_worsens_residual(
    group: Group,
) -> None:
    anchor = group.anchor + np.array([0.0, 0.2, 0.0])
    record = {
        "point_display": anchor.tolist(),
        "axis_display": [-1.0, 0, 0],
        "direction_reversed": True,
    }
    before = fingerprint(group, record)
    result = module.fit_fixed_axis_group(
        group.workspace, group.sides, group.planes, record
    )
    np.testing.assert_array_equal(result["point_display"], anchor)
    np.testing.assert_array_equal(result["axis_display"], [-1.0, 0, 0])
    assert result["fit"]["weighted_rms"] > group.baseline["fit"]["weighted_rms"]
    assert fingerprint(group, record) == before
