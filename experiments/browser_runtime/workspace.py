"""Serial, fixed-example Python operations for the bounded browser worker.

JSON methods return ``{ok: true, result: ...}`` or a bounded structured error.
Only display derivatives are exposed as NumPy buffers; fitting retains float64
source arrays. Scheduling, pushed snapshots and request generations belong to
the JavaScript adapter. There are no threads, HTTP handlers or cancellation here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, final

import numpy as np
from numpy.typing import NDArray
from pydantic import ValidationError

from experiments.face_building_limits import MAX_PAIRS, MAX_REGIONS
from experiments.feature_graph import (
    FeatureGraph,
    GraphRequest,
    Recipe,
    Record,
    StaleGraph,
)
from experiments.feature_recipe_import import import_recipe, load_recipe
from experiments.feature_reuse_authoring import (
    ReuseAuthoringRequest,
    apply_feature_reuse,
)
from experiments.nozzle_session import NozzleWorkspace

CAD_ACTIONS = frozenset(
    {"surface_intersection", "trimmed_face", "arranged_face", "build_faces", "body"}
)
DEFAULT_RECIPE = "recipes/nozzle-selection-and-fitting-demo.json"


@final
class WorkspaceError(ValueError):
    def __init__(self, message: str, code: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class _ImportRequest(Record):
    token: str
    recipe: dict[str, Any]


class _ExampleRequest(Record):
    token: str
    example: str


@dataclass(frozen=True)
class _Evaluation:
    operation: str
    request: GraphRequest
    roots: frozenset[str]


@final
class BrowserWorkspace:
    """One retained nozzle, one current recipe and one serialized evaluation.

    ``call_json('loadWorkspace')`` returns transport-neutral metadata. Retrieve
    ``display_positions`` and ``display_indices`` separately and copy their
    buffers into JavaScript-owned storage before transferring them.

    ``prepare_evaluation_json`` returns ``{run, state}``: only ``run: true``
    schedules ``finish_evaluation_json``. An ensure joining active work returns
    a running acknowledgement without scheduling a second computation.
    """

    def __init__(self, example: Path) -> None:
        self.workspace = NozzleWorkspace(example)
        default = load_recipe(example / DEFAULT_RECIPE)
        self._check_capabilities(default)
        self.example_recipe = default.model_copy(deep=True)
        self.graph = FeatureGraph(self.workspace, default)
        # Immutable source/solver evidence is never detached or rounded for display.
        for array in (
            self.workspace.local,
            self.workspace.origin,
            self.workspace.frame,
            self.workspace.data.xyz,
            self.workspace.data.normals,
            self.workspace.data.triangles,
            self.workspace.data.weights,
            self.workspace.data.ids,
        ):
            array.flags.writeable = False
        self.display_positions: NDArray[np.float32] = np.ascontiguousarray(
            self.workspace.local, dtype="<f4"
        )
        self.display_indices: NDArray[np.uint32] = np.ascontiguousarray(
            self.workspace.data.triangles, dtype="<u4"
        )
        self.display_positions.flags.writeable = False
        self.display_indices.flags.writeable = False
        self._pending: _Evaluation | None = None
        self._last_token = ""
        self._last_error: str | None = None
        self._job_errors: dict[tuple[str, frozenset[str]], str] = {}
        self._closed = False

    @staticmethod
    def _check_capabilities(recipe: Recipe) -> None:
        unsupported = [node for node in recipe.nodes if node.operation in CAD_ACTIONS]
        if unsupported:
            names = "; ".join(
                f"{node.label!r} ({node.operation})" for node in unsupported[:4]
            )
            raise WorkspaceError(
                f"Browser CAD is not enabled. Unsupported actions: {names}. The recipe was not changed.",
                "unsupported_capability",
            )

    @staticmethod
    def _payload(value: str) -> dict[str, Any]:
        if len(value.encode()) > 1_000_000:
            raise ValueError("expected a request under 1 MB")
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object request")
        return cast(dict[str, Any], parsed)

    @staticmethod
    def _keys(payload: dict[str, Any], allowed: set[str]) -> None:
        if payload.keys() - allowed:
            raise ValueError("unexpected request fields")

    def _check_open(self) -> None:
        if self._closed:
            raise WorkspaceError(
                "The browser backend is closed.", "backend_closed", 409
            )

    def _snapshot(self, revision: int | None = None) -> dict[str, Any]:
        state = cast(dict[str, Any], self.graph.snapshot(if_revision=revision))
        state["evaluation_running"] = self._pending is not None
        if (
            self._pending is None
            and self._last_token == state["token"]
            and self._last_error
        ):
            state["evaluation_error"] = self._last_error
        return state

    @staticmethod
    def _error(error: Exception) -> dict[str, Any]:
        if isinstance(error, WorkspaceError):
            code, status = error.code, error.status
            message = str(error)
        elif isinstance(error, StaleGraph):
            code, status, message = "stale_graph", 409, str(error)
        elif isinstance(error, ValidationError):
            code, status = "validation_error", 422
            messages = [
                str(item["msg"]).removeprefix("Value error, ")
                for item in error.errors(include_input=False, include_url=False)
            ]
            message = "; ".join(dict.fromkeys(messages))
        elif isinstance(error, ValueError):
            code, status, message = "validation_error", 422, str(error)
        else:
            code, status, message = "evaluation_error", 500, str(error)
        return {
            "code": code,
            "status": status,
            "message": (message or type(error).__name__)[:1200],
        }

    def _json(self, operation: Callable[[], Any]) -> str:
        try:
            return json.dumps({"ok": True, "result": operation()}, allow_nan=False)
        except Exception as error:
            return json.dumps(
                {"ok": False, "error": self._error(error)}, allow_nan=False
            )

    def call_json(self, operation: str, payload_json: str = "{}") -> str:
        return self._json(lambda: self._call(operation, self._payload(payload_json)))

    def _call(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation == "close":
            self._keys(payload, set())
            self._closed = True
            self._pending = None
            self._job_errors.clear()
            return None
        self._check_open()
        if operation == "loadWorkspace":
            self._keys(payload, set())
            metadata = self.workspace.metadata()
            for name in ("positions", "indices"):
                descriptor = cast(dict[str, Any], metadata[name])
                _ = descriptor.pop("url")
            return {
                **metadata,
                "face_building_limits": {
                    "max_pair_checks": MAX_PAIRS,
                    "max_regions": MAX_REGIONS,
                },
            }
        if operation == "getGraph":
            self._keys(payload, {"revision"})
            revision = payload.get("revision")
            if revision is not None and (type(revision) is not int or revision < 0):
                raise ValueError("expected a nonnegative integer revision")
            return self._snapshot(revision)
        if operation == "replaceRecipe":
            request = GraphRequest.model_validate(payload)
            if request.recipe is None:
                raise ValueError("expected a current recipe")
            self._check_capabilities(request.recipe)
            _ = self.graph.replace(request.recipe, request.token)
            return self._snapshot()
        if operation == "importRecipe":
            imported = _ImportRequest.model_validate(payload)
            recipe, warnings = import_recipe(imported.recipe)
            self._check_capabilities(recipe)
            _ = self.graph.replace(recipe, imported.token)
            return {**self._snapshot(), "import_warnings": warnings}
        if operation == "exportRecipe":
            self._keys(payload, set())
            return self._snapshot()["recipe"]
        if operation == "getExampleRecipe":
            self._keys(payload, set())
            return self.example_recipe.model_dump()
        if operation == "listExamples":
            self._keys(payload, set())
            return {
                "active": "nozzle",
                "examples": [{"id": "nozzle", "label": "Nozzle"}],
            }
        if operation == "selectExample":
            request = _ExampleRequest.model_validate(payload)
            if request.token != self._snapshot()["token"]:
                raise StaleGraph("graph changed before switching examples")
            if self._pending is not None:
                raise WorkspaceError(
                    "Wait for graph evaluation before switching examples.",
                    "evaluation_busy",
                    409,
                )
            if request.example != "nozzle":
                raise WorkspaceError(
                    "Only the retained nozzle is available in this browser deployment.",
                    "unsupported_capability",
                )
            self.graph = FeatureGraph(
                self.workspace, self.example_recipe.model_copy(deep=True)
            )
            self._last_token, self._last_error = "", None
            self._job_errors.clear()
            return {"active": "nozzle"}
        if operation == "applyFeatureReuse":
            request = ReuseAuthoringRequest.model_validate(payload)
            state = apply_feature_reuse(self.graph, request)
            return {**self._snapshot(), "reuse_authoring": state["reuse_authoring"]}
        if operation in {"faceCandidates", "previewFaces", "applyFaces", "exportCad"}:
            raise WorkspaceError(
                "Browser CAD is not enabled in this deployment.",
                "unsupported_capability",
            )
        raise WorkspaceError(
            f"Unknown browser operation: {operation}", "unsupported_operation"
        )

    def prepare_evaluation_json(self, operation: str, payload_json: str) -> str:
        return self._json(lambda: self._prepare(operation, self._payload(payload_json)))

    def _prepare(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_open()
        if operation not in {"evaluateGraph", "ensureGraph"}:
            raise WorkspaceError(
                "Expected evaluateGraph or ensureGraph.", "unsupported_operation"
            )
        request = GraphRequest.model_validate(payload)
        self._check_capabilities(Recipe.model_validate(self._snapshot()["recipe"]))
        if operation == "ensureGraph":
            if request.target is not None or request.recipe is not None:
                raise ValueError("ensure expects targets, not a target or recipe")
            needed, ready = self.graph.readiness_status(
                request.token, request.targets, request.all_actions
            )
            roots = self.graph.evaluation_roots(
                request.token, request.targets, request.all_actions
            )
            self._job_errors = {
                key: error
                for key, error in self._job_errors.items()
                if key[0] == request.token
            }
            job_error = self._job_errors.get((request.token, roots))
            if not needed and ready is not None and not ready["required_failures"]:
                _ = self._job_errors.pop((request.token, roots), None)
                job_error = None
            if self._pending is not None:
                return {
                    "run": False,
                    "state": {
                        "status": "running",
                        "token": request.token,
                        "evaluation_running": True,
                    },
                }
            if not needed or job_error is not None:
                state = cast(
                    dict[str, Any],
                    ready
                    if ready is not None
                    else self.graph.readiness_snapshot(
                        request.token, request.targets, request.all_actions
                    ),
                )
                state["evaluation_running"] = False
                if job_error is not None:
                    state["evaluation_error"] = job_error
                return {"run": False, "state": state}
        else:
            if self._pending is not None:
                raise WorkspaceError(
                    "A graph evaluation is already running.", "evaluation_busy", 409
                )
            if request.recipe is not None or request.targets is not None:
                raise ValueError(
                    "evaluate expects a target or all actions, not a recipe or targets"
                )
            roots = self.graph.evaluation_roots(
                request.token,
                [request.target] if request.target is not None else None,
                request.all_actions,
            )
            self._job_errors.clear()
        self._pending = _Evaluation(operation, request, roots)
        self._last_token, self._last_error = request.token, None
        return {
            "run": True,
            "state": {
                "status": "running",
                "token": request.token,
                "evaluation_running": True,
            },
        }

    def finish_evaluation_json(self) -> str:
        return self._json(self._finish)

    def _finish(self) -> dict[str, Any]:
        self._check_open()
        pending = self._pending
        if pending is None:
            raise WorkspaceError(
                "No graph evaluation is pending.", "evaluation_not_pending", 409
            )
        request = pending.request
        try:
            if pending.operation == "ensureGraph":
                _ = self.graph.ensure_current(
                    request.token, request.targets, request.all_actions
                )
            else:
                _ = self.graph.evaluate(
                    request.token, request.target, request.all_actions
                )
        except StaleGraph:
            # Superseded work does not poison the current token's ensure requests.
            raise
        except Exception as error:
            message = str(self._error(error)["message"])
            self._last_error = message
            self._job_errors[(request.token, pending.roots)] = message
        finally:
            self._pending = None
        state = cast(
            dict[str, Any],
            self.graph.readiness_snapshot(request.token, sorted(pending.roots)),
        )
        state["evaluation_running"] = False
        if self._last_error is not None:
            state["evaluation_error"] = self._last_error
        return state
