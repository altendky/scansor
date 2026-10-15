"""Internal, deterministic recovery over one authoritative fitting problem.

Families own initialization mathematics, solve charts, full-problem assessment,
hard constraints and geometry equivalence. Candidates carry only derived starts
and provenance: they cannot replace observations, weights, family or intent.
Nothing here is a recipe field or a claim of global convergence/fit acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import islice
from types import MappingProxyType
from typing import Any, Literal

from experiments.fit_solver import (
    AttemptControl,
    AttemptLimits,
    FitBudgetExhausted,
)
from scansor.constrained_least_squares import ConstrainedLeastSquaresFailure
from scansor.nonlinear_least_squares import LeastSquaresFailure

POLICY_VERSION = "selected-data-recovery-v2"
DEFAULT_ATTEMPT_LIMITS = AttemptLimits()


def initialization_binding(
    source_sha256: str,
    interpretation_sha256: str,
    selected_ids: Sequence[int],
    intent: Mapping[str, object],
    *,
    policy_version: str = POLICY_VERSION,
    policy: RecoveryPolicy | None = None,
) -> InitializationBinding:
    """Bind a disposable hint to ordered membership and complete semantic intent.

    Intent must include family, explicit references/geometry, hard constraints,
    support and semantic settings. The source interpretation determines admitted
    observations/normals/weights. Filenames and computed starts do not belong in
    intent. A family algorithm change must change policy_version. The complete
    effective recovery policy is also bound, including budgets and tolerances.
    """
    for digest in (source_sha256, interpretation_sha256):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("source and interpretation require SHA-256 identities")
    if (
        not selected_ids
        or any(type(i) is not int or i < 0 for i in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
        or not policy_version
    ):
        raise ValueError("binding needs distinct ordered source IDs and a policy")
    effective = DEFAULT_POLICY if policy is None else policy
    record = {
        "source": source_sha256,
        "interpretation": interpretation_sha256,
        "ids": list(selected_ids),
        "intent": dict(intent),
        "algorithm": policy_version,
        "policy": asdict(effective),
    }
    digest = hashlib.sha256(
        json.dumps(
            record, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return InitializationBinding(digest, effective)


@dataclass(frozen=True)
class FitCandidate:
    id: str
    parameters: tuple[float, ...]
    derivation: str
    chart: str
    binding_sha256: str

    def __post_init__(self) -> None:
        # Own the numerical artifact even when a caller supplies a mutable
        # sequence at the runtime boundary.
        object.__setattr__(self, "parameters", tuple(self.parameters))


@dataclass(frozen=True)
class RecoveryPolicy:
    version: str = POLICY_VERSION
    max_proposals: int = 16
    max_fresh_attempts: int = 8
    attempt: AttemptLimits = DEFAULT_ATTEMPT_LIMITS
    objective_atol: float = 1e-12
    objective_rtol: float = 1e-9

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("recovery policy requires a version")
        for value in (self.max_proposals, self.max_fresh_attempts):
            if type(value) is not int or value < 1:
                raise ValueError(
                    "proposal and attempt limits must be positive integers"
                )
        for value in (self.objective_atol, self.objective_rtol):
            if not math.isfinite(value) or value < 0:
                raise ValueError("objective comparison tolerances must be nonnegative")


DEFAULT_POLICY = RecoveryPolicy()


@dataclass(frozen=True)
class InitializationBinding:
    sha256: str
    policy: RecoveryPolicy


@dataclass(frozen=True)
class FitAssessment:
    """Independent full-objective assessment in common normalized units.

    valid includes all original geometry/support and hard-constraint checks.
    Comparison tolerances identify competing local solutions; they are neither
    physical accuracy nor fit-acceptance tolerances.
    """

    objective: float
    valid: bool = True
    rank: int | None = None
    condition: float | None = None
    termination: str = "locally-converged"
    iterations: int | None = None


@dataclass(frozen=True)
class RecoveryProblem[Result]:
    binding: InitializationBinding
    charts: Mapping[str, int]
    solve: Callable[[FitCandidate, AttemptControl], Result]
    assess: Callable[[Result], FitAssessment]
    equivalent: Callable[[Result, Result], bool]

    @property
    def binding_sha256(self) -> str:
        return self.binding.sha256

    def __post_init__(self) -> None:
        if not self.charts or any(
            not name or type(size) is not int or size < 1
            for name, size in self.charts.items()
        ):
            raise ValueError("problem needs named charts with positive dimensions")
        object.__setattr__(self, "charts", MappingProxyType(dict(self.charts)))


@dataclass(frozen=True)
class AttemptDiagnostic:
    candidate: FitCandidate
    role: Literal["fresh", "hint"]
    outcome: str
    evaluations: int = 0
    assessment: FitAssessment | None = None
    solver: dict[str, Any] | None = None


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class RecoveryDiagnostics:
    policy: RecoveryPolicy
    binding_sha256: str
    attempts: tuple[AttemptDiagnostic, ...]
    selected: str | None
    termination: str
    selected_role: Literal["fresh", "hint"] | None = None

    def json(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class RecoveryResult[Result]:
    result: Result
    diagnostics: RecoveryDiagnostics


class RecoveryFailure(ValueError):
    def __init__(self, code: str, diagnostics: RecoveryDiagnostics) -> None:
        self.code: str = code
        self.diagnostics: RecoveryDiagnostics = diagnostics
        super().__init__(f"{code}: bounded fit recovery did not identify a solution")


def recover_fit[Result](
    problem: RecoveryProblem[Result],
    fresh_candidates: Iterable[FitCandidate],
    *,
    hint: FitCandidate | None = None,
    policy: RecoveryPolicy | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RecoveryResult[Result]:
    """Try bounded fresh candidates, then at most one separately budgeted hint.

    Every successful attempt is independently assessed by the same full-problem
    callback. All eligible attempts run before selection; exact objective ties
    prefer stable fresh order. Family adapters must route numerical callbacks
    (including equalities/projection) through the supplied attempt control and
    honor its iteration/trial caps. Unexpected exceptions propagate.
    """
    policy = problem.binding.policy if policy is None else policy
    guard = AttemptControl(policy.attempt, cancelled)
    guard.checkpoint()
    if problem.binding.policy != policy:
        raise ValueError("problem binding and effective recovery policies differ")
    # Take at most the proposal cap even if the family supplies an endless
    # iterator. Generate without consulting hints or results of prior solves.
    proposals: list[FitCandidate] = []
    for candidate in islice(fresh_candidates, policy.max_proposals):
        guard.checkpoint()
        proposals.append(candidate)
    diagnostics: list[AttemptDiagnostic] = []
    successes: list[tuple[Result, FitAssessment, str, Literal["fresh", "hint"]]] = []
    fresh_attempts = 0
    seen: set[str] = set()
    candidates: list[tuple[FitCandidate, Literal["fresh", "hint"]]] = [
        (candidate, "fresh") for candidate in proposals
    ]
    if hint is not None:
        candidates.append((hint, "hint"))
    for candidate, role in candidates:
        guard.checkpoint()
        reason = None
        if (
            not candidate.id
            or not candidate.derivation
            or candidate.chart not in problem.charts
            or len(candidate.parameters) != problem.charts.get(candidate.chart)
            or candidate.binding_sha256 != problem.binding_sha256
            or not candidate.parameters
            or not all(math.isfinite(v) for v in candidate.parameters)
        ):
            reason = "screened-invalid-or-stale"
        elif role == "fresh" and candidate.id in seen:
            reason = "screened-duplicate-id"
        elif role == "fresh" and fresh_attempts >= policy.max_fresh_attempts:
            reason = "fresh-attempt-limit"
        elif role == "hint" and not fresh_attempts:
            reason = "screened-no-fresh-start"
        if reason is not None:
            diagnostics.append(AttemptDiagnostic(candidate, role, reason))
            continue
        if role == "fresh":
            fresh_attempts += 1
            seen.add(candidate.id)
        control = AttemptControl(policy.attempt, cancelled)
        try:
            result = problem.solve(candidate, control)
        except (LeastSquaresFailure, ConstrainedLeastSquaresFailure) as error:
            diagnostics.append(
                AttemptDiagnostic(
                    candidate,
                    role,
                    error.code,
                    control.evaluations,
                    solver=asdict(error.diagnostics) if error.diagnostics else None,
                )
            )
            continue
        except FitBudgetExhausted:
            diagnostics.append(
                AttemptDiagnostic(
                    candidate, role, "evaluation-limit", control.evaluations
                )
            )
            continue
        guard.checkpoint()
        assessment = problem.assess(result)
        guard.checkpoint()
        valid = (
            assessment.valid
            and math.isfinite(assessment.objective)
            and assessment.objective >= 0
        )
        diagnostics.append(
            AttemptDiagnostic(
                candidate,
                role,
                "locally-converged" if valid else "invalid-result",
                control.evaluations,
                assessment,
            )
        )
        if valid:
            successes.append((result, assessment, candidate.id, role))

    def report(
        code: str,
        selected: str | None = None,
        selected_role: Literal["fresh", "hint"] | None = None,
    ) -> RecoveryDiagnostics:
        return RecoveryDiagnostics(
            policy,
            problem.binding_sha256,
            tuple(diagnostics),
            selected,
            code,
            selected_role,
        )

    guard.checkpoint()
    if not successes:
        outcomes = {
            attempt.outcome
            for attempt in diagnostics
            if attempt.role == "fresh" and not attempt.outcome.startswith("screened-")
        }
        code = (
            "no-valid-candidates"
            if not outcomes
            else "rank-deficient"
            if outcomes == {"rank-deficient"}
            else "search-exhausted"
        )
        raise RecoveryFailure(code, report(code))
    # Anchor comparisons to the immutable raw minimum, never a chain of nearby
    # scores. Equivalent attempts within the declared comparison tolerance use
    # stable proposal order, so roundoff cannot choose a different chart/hint.
    # Preserve every measured score in diagnostics; no objective is rounded.
    raw_best = min(successes, key=lambda value: value[1].objective)
    tied: list[tuple[Result, FitAssessment, str, Literal["fresh", "hint"]]] = []
    for attempt in successes:
        guard.checkpoint()
        if math.isclose(
            attempt[1].objective,
            raw_best[1].objective,
            abs_tol=policy.objective_atol,
            rel_tol=policy.objective_rtol,
        ):
            tied.append(attempt)
    # Approximate family equivalence need not be transitive. Every tied pair
    # must agree, including the stable winner and the raw minimum.
    for index, (left, _, _, _) in enumerate(tied):
        for right, _, _, _ in tied[index + 1 :]:
            guard.checkpoint()
            equivalent = problem.equivalent(left, right)
            guard.checkpoint()
            if not equivalent:
                raise RecoveryFailure("ambiguous", report("ambiguous"))
    best, _, selected, selected_role = tied[0]
    guard.checkpoint()
    return RecoveryResult(
        best, report("best-attempted-local-solution", selected, selected_role)
    )
