"""Direct shared-axis cancellation retains complete data, intent and budgets."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import experiments.nozzle_coaxial as module
import experiments.surface_primitives as primitives
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import recover_fit
from experiments.fit_relationship_component import fit_relationship_component
from experiments.fit_solver import AttemptControl, FitCancelled
from experiments.mesh_cylinder_fit import CylinderFitFailure
from experiments.nozzle_coaxial import FitSelection, ReferencePlaneGroup
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import ScanWorkspace
from scansor.nonlinear_least_squares import LeastSquaresFailure
from tests.test_relationship_composition import (
    _generic_mixed_group,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_scan_cylinder_fit import AXES, ORIGIN
from tests.test_shared_axis_hints import cylinder_group


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass
class Case:
    workspace: ScanWorkspace
    sides: list[FitSelection]
    planes: list[FitSelection]
    references: tuple[ReferencePlaneGroup, ...]
    fixed: bool
    axis: dict[str, Any]
    intent: dict[str, Any]
    baseline: dict[str, Any]


def fit(case: Case, **kwargs: Any) -> dict[str, Any]:
    if case.fixed:
        return module.fit_fixed_axis_group(
            case.workspace,
            case.sides,
            case.planes,
            case.axis,
            semantic_intent=case.intent,
            **kwargs,
        )
    return module.fit_group(
        case.workspace,
        case.sides,
        case.planes,
        reference_plane_groups=case.references,
        semantic_intent=case.intent,
        **kwargs,
    )


@pytest.fixture(
    scope="module",
    params=["cylinder-free", "cylinder-fixed", "mixed-free", "mixed-fixed"],
)
def case(request: pytest.FixtureRequest) -> Case:
    family, role = str(request.param).split("-")
    if family == "cylinder":
        workspace, sides, planes = cylinder_group()
        references = ()
        anchor = ORIGIN
    else:
        workspace, sides, planes, references, anchor = _generic_mixed_group(AXES[0])
    result = Case(
        workspace,
        sides,
        planes,
        references,
        role == "fixed",
        {
            "point_display": anchor.tolist(),
            "axis_display": (-AXES[0]).tolist(),
            "axis_is_directed": True,
            "direction_reversed": True,
        },
        {
            "recipe": {"role": "cancel-baseline", "family": family},
            "axis_role": "fixed-or-free",
        },
        {},
    )
    result.baseline = fit(result)
    assert result.baseline["fit"]["weighted_rms"] < 2e-5
    return result


def fingerprint(case: Case) -> str:
    workspace = case.workspace
    digest = hashlib.sha256(workspace.original_bytes)
    for array in (
        workspace.local,
        workspace.origin,
        workspace.frame,
        workspace.data.normals,
        workspace.data.original_normals,
        workspace.data.triangles,
        workspace.data.weights,
    ):
        digest.update(array.tobytes())
    digest.update(encoded(workspace.metadata()))
    digest.update(repr((case.sides, case.planes, case.references)).encode())
    digest.update(encoded((case.axis, case.intent)))
    return digest.hexdigest()


def test_cancelled_before_initialization_has_no_numerical_work(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)

    def never(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("an already cancelled fit must not initialize or solve")

    monkeypatch.setattr(CoordinateFrame, "from_observations", never)
    monkeypatch.setattr(np.linalg, "eigh", never)
    monkeypatch.setattr(module, "fit_relationship_component", never)
    with pytest.raises(FitCancelled):
        _ = fit(case, cancelled=lambda: True)
    assert fingerprint(case) == before


def test_cancellation_after_frame_stops_before_fresh_moments(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    stopped = False
    original = CoordinateFrame.from_observations

    def prepared(*args: Any, **kwargs: Any) -> Any:
        nonlocal stopped
        result = original(*args, **kwargs)
        stopped = True
        return result

    def never(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail(
            "cancellation must stop fresh initialization before another eigensolve"
        )

    monkeypatch.setattr(CoordinateFrame, "from_observations", prepared)
    monkeypatch.setattr(np.linalg, "eigh", never)
    monkeypatch.setattr(module, "fit_relationship_component", never)
    with pytest.raises(FitCancelled):
        _ = fit(case, cancelled=lambda: stopped)
    assert stopped and fingerprint(case) == before


def test_cancellation_between_attempts_does_not_restart_or_become_search_failure(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    stopped = False
    controls: list[AttemptControl] = []

    def failed(*_args: Any, **kwargs: Any) -> Any:
        nonlocal stopped
        controls.append(kwargs["control"])
        assert len(controls) == 1, "a cancelled group launched another attempt"
        stopped = True
        raise LeastSquaresFailure("rank-deficient", "controlled first-start failure")

    monkeypatch.setattr(module, "fit_relationship_component", failed)
    with pytest.raises(FitCancelled):
        _ = fit(case, cancelled=lambda: stopped)
    assert len(controls) == 1 and controls[0].evaluations == 0
    assert fingerprint(case) == before


@pytest.mark.parametrize("target", ["observations", "equalities"])
def test_actual_component_callback_fence_cancels_without_later_callbacks(
    case: Case,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    stopped = False
    seen: list[str] = []
    controls: list[AttemptControl] = []
    real_call = AttemptControl.call
    real_component = fit_relationship_component

    def component(*args: Any, **kwargs: Any) -> Any:
        assert not stopped
        controls.append(kwargs["control"])
        return real_component(*args, **kwargs)

    def call(self: AttemptControl, callback: Callable[..., Any], values: Any) -> Any:
        assert not stopped, "a callback was reached after cancellation"

        def measured(value: Any) -> Any:
            nonlocal stopped
            result = callback(value)
            seen.append(callback.__name__)
            if callback.__name__ == target:
                stopped = True
            return result

        return real_call(self, measured, values)

    monkeypatch.setattr(module, "fit_relationship_component", component)
    monkeypatch.setattr(AttemptControl, "call", call)
    with pytest.raises(FitCancelled):
        _ = fit(case, cancelled=lambda: stopped)
    assert stopped and seen[-1] == target and seen.count(target) == 1
    assert len(controls) == 1 and controls[0].evaluations == len(seen)
    assert fingerprint(case) == before


def test_completed_recovery_cannot_publish_after_late_cancellation(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    recovered = False
    stopped = False
    real_recover = recover_fit
    real_basis = primitives.basis

    def recovery(*args: Any, **kwargs: Any) -> Any:
        nonlocal recovered
        result = real_recover(*args, **kwargs)
        recovered = True
        return result

    def publication_basis(*args: Any, **kwargs: Any) -> Any:
        nonlocal stopped
        result = real_basis(*args, **kwargs)
        if recovered:
            stopped = True
        return result

    monkeypatch.setattr(module, "recover_fit", recovery)
    monkeypatch.setattr(primitives, "basis", publication_basis)
    with pytest.raises(FitCancelled):
        _ = fit(case, cancelled=lambda: stopped)
    assert recovered and stopped and fingerprint(case) == before


def test_none_and_false_callback_preserve_every_default_trace_and_quota(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    assert encoded(fit(case, cancelled=None)) == encoded(case.baseline)
    polls = 0
    controls: list[AttemptControl] = []
    real_component = fit_relationship_component

    def component(*args: Any, **kwargs: Any) -> Any:
        controls.append(kwargs["control"])
        return real_component(*args, **kwargs)

    def never_cancelled() -> bool:
        nonlocal polls
        polls += 1
        return False

    monkeypatch.setattr(module, "fit_relationship_component", component)
    result = fit(case, cancelled=never_cancelled)
    assert encoded(result) == encoded(case.baseline)
    assert polls > 0 and controls and len({id(c) for c in controls}) == len(controls)
    assert all(
        c.cancelled is never_cancelled
        and c.limits.max_iterations == 100
        and c.limits.max_trials == 16
        and c.limits.max_evaluations == 512
        and c.evaluations <= 512
        for c in controls
    )
    recovery = result["fit"]["initialization"]["recovery"]
    assert recovery["policy"]["max_proposals"] == 16
    assert recovery["policy"]["max_fresh_attempts"] == 8
    assert sum(a["evaluations"] for a in recovery["attempts"]) == sum(
        c.evaluations for c in controls
    )
    assert fingerprint(case) == before


def test_false_callback_keeps_shared_callback_budget_enforced(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = fingerprint(case)
    controls: list[AttemptControl] = []
    polls = 0

    def never_cancelled() -> bool:
        nonlocal polls
        polls += 1
        return False

    def exhaust(*_args: Any, **kwargs: Any) -> Any:
        control = kwargs["control"]
        controls.append(control)

        def identity(values: Any) -> Any:
            return values

        for _ in range(513):
            _ = control.call(identity, np.zeros(1))
        pytest.fail("the 513th callback must not run")

    monkeypatch.setattr(module, "fit_relationship_component", exhaust)
    with pytest.raises(CylinderFitFailure) as caught:
        _ = fit(case, cancelled=never_cancelled)
    assert caught.value.code == "search-exhausted"
    recovery = caught.value.diagnostic["recovery"]
    executed = [a for a in recovery["attempts"] if a["evaluations"]]
    assert 0 < len(controls) == len(executed) <= 8
    assert all(c.evaluations == 512 for c in controls)
    assert all(
        a["outcome"] == "evaluation-limit" and a["evaluations"] == 512 for a in executed
    )
    assert recovery["selected"] is None and polls > sum(c.evaluations for c in controls)
    assert fingerprint(case) == before


def test_unexpected_caller_callback_error_is_not_a_numerical_failure(
    case: Case,
) -> None:
    before = fingerprint(case)

    class CallerError(RuntimeError):
        pass

    def failed() -> bool:
        raise CallerError("caller failure")

    with pytest.raises(CallerError, match="caller failure"):
        _ = fit(case, cancelled=failed)
    assert fingerprint(case) == before


@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("requested", [False, True])
def test_legacy_callbacks_are_rejected_before_accessing_seed_or_polling(
    fixed: bool,
    requested: bool,
) -> None:
    # No legacy attributes exist: a support/seed read would raise AttributeError.
    workspace = object.__new__(NozzleWorkspace)
    polls = 0

    def cancelled() -> bool:
        nonlocal polls
        polls += 1
        return requested

    with pytest.raises(
        ValueError, match="legacy example groups do not support cancellation"
    ):
        if fixed:
            _ = module.fit_fixed_axis_group(
                workspace, [], [], np.zeros(7), cancelled=cancelled
            )
        else:
            _ = module.fit_group(workspace, [], [], cancelled=cancelled)
    assert polls == 0


@pytest.mark.parametrize("fixed", [False, True])
def test_legacy_explicit_none_preserves_complete_default_result(fixed: bool) -> None:
    workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
    sides = [FitSelection("side", workspace.default.lateral_ids, "cone", None)]
    planes = [FitSelection("cap", workspace.default.plane_ids, "plane", None)]
    before = hashlib.sha256(
        workspace.local.tobytes() + workspace.data.weights.tobytes()
    ).hexdigest()
    if fixed:
        axis = np.zeros(7)
        axis[:4] = workspace.data.selection["initial_parameters"][:4]
        original = module.fit_fixed_axis_group(workspace, sides, planes, axis)
        actual = module.fit_fixed_axis_group(
            workspace, sides, planes, axis, cancelled=None
        )
    else:
        original = module.fit_group(workspace, sides, planes)
        actual = module.fit_group(workspace, sides, planes, cancelled=None)
    assert encoded(actual) == encoded(original)
    assert (
        hashlib.sha256(
            workspace.local.tobytes() + workspace.data.weights.tobytes()
        ).hexdigest()
        == before
    )
