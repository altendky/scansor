"""Serial source and graph operations for the bounded browser worker.

JSON methods return ``{ok: true, result: ...}`` or a bounded structured error.
Only display derivatives are exposed as NumPy buffers; fitting retains float64
source arrays. Scheduling, pushed snapshots and request generations belong to
the JavaScript adapter. Cancellation recovery discards disposable computation;
there are no threads or HTTP handlers here.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, cast, final

import numpy as np
from numpy.typing import NDArray
from pydantic import ValidationError

from experiments.actions_limits import (
    MAX_ACTIONS_JSON_BYTES,
    MAX_ACTIONS_REQUEST_BYTES,
    MAX_CONTROL_JSON_BYTES,
    actions_limits,
    check_actions_envelope,
    check_actions_json,
)
from experiments.face_building_limits import MAX_PAIRS, MAX_REGIONS
from experiments.feature_graph import (
    FeatureGraph,
    GraphFailureCheckpoint,
    GraphRequest,
    Recipe,
    Record,
    StaleGraph,
)
from experiments.feature_recipe_import import ImportWarning, import_recipe, load_recipe
from experiments.feature_reuse_authoring import (
    ReuseAuthoringRequest,
    apply_feature_reuse,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_actions import ActionsInspection, inspect_actions
from experiments.scan_project import ScanProject
from experiments.scan_workspace import (
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    check_cancelled,
)

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


@dataclass(frozen=True)
class _OperationCheckpoint:
    graph: GraphFailureCheckpoint
    job_errors: dict[tuple[str, frozenset[str]], str]
    last_token: str
    last_error: str | None
    source: _BrowserSource
    generation: int


@dataclass(frozen=True)
class _LegacySource:
    workspace: NozzleWorkspace
    graph: FeatureGraph[NozzleWorkspace]
    positions: NDArray[np.float32]
    indices: NDArray[np.uint32]
    recipe: Recipe
    identity: dict[str, Any]


@dataclass(frozen=True)
class _BrowserSource:
    project: ScanProject | None
    legacy: _LegacySource | None
    import_warnings: tuple[ImportWarning, ...] = ()

    @property
    def workspace(self) -> ScanWorkspace | NozzleWorkspace:
        if self.project is not None:
            return self.project.accepted.workspace
        assert self.legacy is not None
        return self.legacy.workspace

    @property
    def graph(self) -> FeatureGraph[Any]:
        if self.project is not None:
            return self.project.accepted.graph
        assert self.legacy is not None
        return self.legacy.graph

    @property
    def positions(self) -> NDArray[np.float32]:
        if self.project is not None:
            return self.project.accepted.positions
        assert self.legacy is not None
        return self.legacy.positions

    @property
    def indices(self) -> NDArray[np.uint32]:
        if self.project is not None:
            return self.project.accepted.indices
        assert self.legacy is not None
        return self.legacy.indices


@dataclass(frozen=True)
class _SourceCandidate:
    operation_id: str
    previous: _BrowserSource
    previous_generation: int
    previous_token: str
    generation: int
    source: _BrowserSource
    prepared_token: str
    prepared_revision: int


@final
class BrowserWorkspace:
    """One accepted source, one private candidate and one serialized evaluation.

    ``call_json('loadWorkspace')`` returns transport-neutral metadata. Retrieve
    ``display_positions`` and ``display_indices`` separately and copy their
    buffers into JavaScript-owned storage before transferring them.

    ``prepare_evaluation_json`` returns ``{run, state}``: only ``run: true``
    schedules ``finish_evaluation_json``. An ensure joining active work returns
    a running acknowledgement without scheduling a second computation.
    """

    def __init__(
        self,
        example: Path | None,
        *,
        scan_limits: ScanLimits | None = None,
        cad_available: bool = False,
    ) -> None:
        self.cad_enabled = False
        self._cad_available = cad_available
        self._export_buffer = b""
        self._example_path = example or (
            Path(__file__).resolve().parents[2] / "examples/nozzle-bayonette-simplified"
        )
        self._scan_limits = scan_limits
        self._source = (
            _BrowserSource(ScanProject(), None)
            if example is None
            else self._example_source()
        )
        self.example_recipe = (
            Recipe(schema_version=2, nodes=[], output=None)
            if self._source.legacy is None
            else self._source.legacy.recipe.model_copy(deep=True)
        )
        self._workspace_generation = 0
        self._candidate: _SourceCandidate | None = None
        self._preparing_source = False
        self._last_discarded: str | None = None
        self._pristine = True
        self._evaluation_lease: ExitStack | None = None
        self._pending: _Evaluation | None = None
        self._last_token = ""
        self._last_error: str | None = None
        self._job_errors: dict[tuple[str, frozenset[str]], str] = {}
        self._closed = False
        self._operation_checkpoint: _OperationCheckpoint | None = None

    def _example_source(self) -> _BrowserSource:
        workspace = NozzleWorkspace(self._example_path)
        default = load_recipe(self._example_path / DEFAULT_RECIPE)
        self._check_capabilities(default)
        graph = FeatureGraph(workspace, default)
        # Immutable source/solver evidence is never detached or rounded for display.
        for array in (
            workspace.local,
            workspace.origin,
            workspace.frame,
            workspace.data.xyz,
            workspace.data.normals,
            workspace.data.triangles,
            workspace.data.weights,
            workspace.data.ids,
        ):
            array.flags.writeable = False
        positions: NDArray[np.float32] = np.ascontiguousarray(
            workspace.local, dtype="<f4"
        )
        indices: NDArray[np.uint32] = np.ascontiguousarray(
            workspace.data.triangles, dtype="<u4"
        )
        positions.flags.writeable = False
        indices.flags.writeable = False
        identity = next(
            node.model_dump(mode="json")
            for node in default.nodes
            if node.operation == "source"
        )
        return _BrowserSource(
            None, _LegacySource(workspace, graph, positions, indices, default, identity)
        )

    @property
    def workspace(self) -> Any:
        # Existing retained-example clients have legacy workspace methods. Generic
        # state is always the actual shared ScanWorkspace, without fake fields.
        return self._source.workspace

    @property
    def graph(self) -> FeatureGraph[Any]:
        return self._source.graph

    @property
    def display_positions(self) -> NDArray[np.float32]:
        return self._source.positions

    @property
    def display_indices(self) -> NDArray[np.uint32]:
        return self._source.indices

    @property
    def workspace_generation(self) -> int:
        return self._workspace_generation

    @staticmethod
    def _mode(source: _BrowserSource) -> dict[str, str]:
        if source.legacy is not None:
            return {"kind": "example", "example": "nozzle"}
        assert source.project is not None
        return {
            "kind": "empty"
            if source.project.accepted.workspace.descriptor is None
            else "scan"
        }

    @staticmethod
    def _identity(source: _BrowserSource) -> dict[str, Any]:
        if source.legacy is not None:
            return deepcopy(source.legacy.identity)
        assert source.project is not None
        workspace = source.project.accepted.workspace
        if workspace.descriptor is None:
            return {}
        return {
            "descriptor": workspace.descriptor.semantic_record(),
            "reference_sha256": workspace.reference_sha256,
        }

    @property
    def source_mode(self) -> dict[str, str]:
        return self._mode(self._source)

    @property
    def source_identity(self) -> dict[str, Any]:
        return self._identity(self._source)

    @property
    def candidate_display_positions(self) -> NDArray[np.float32]:
        return self._require_candidate().source.positions

    @property
    def candidate_display_indices(self) -> NDArray[np.uint32]:
        return self._require_candidate().source.indices

    def _require_candidate(self) -> _SourceCandidate:
        self._check_open()
        if self._candidate is None:
            raise ScanImportError(
                "No source candidate is prepared.", "unknown_operation"
            )
        return self._candidate

    def _source_metadata(
        self, source: _BrowserSource, generation: int
    ) -> dict[str, Any]:
        metadata = source.workspace.metadata()
        for name in ("positions", "indices"):
            descriptor = cast(dict[str, Any], metadata[name])
            _ = descriptor.pop("url", None)
        graph = (
            self._snapshot()
            if source is self._source
            else {
                **source.graph.snapshot(),
                "project_generation": generation,
                "evaluation_running": False,
            }
        )
        capabilities = cast(dict[str, Any], metadata.get("capabilities", {}))
        source_import: dict[str, Any] = {
            "supported": self._scan_limits is not None,
            "max_sources": 1,
            "cancel": True,
        }
        if self._scan_limits is not None:
            source_import["limits"] = asdict(self._scan_limits)
        return {
            **metadata,
            "import_warnings": deepcopy(list(source.import_warnings)),
            "graph": graph,
            "project_generation": generation,
            "source_mode": self._mode(source),
            "source_identity": self._identity(source),
            "actions_limits": actions_limits(),
            "capabilities": {**capabilities, "source_import": source_import},
            "face_building_limits": {
                "max_pair_checks": MAX_PAIRS,
                "max_regions": MAX_REGIONS,
            },
        }

    def _inspect_actions(self, payload: dict[str, Any]) -> ActionsInspection:
        if (
            type(payload.get("generation")) is not int
            or payload["generation"] != self._workspace_generation
            or payload.get("token") != self.graph.snapshot()["token"]
        ):
            raise ScanImportError(
                "Project changed before inspecting actions.", "stale_source"
            )
        inspection = inspect_actions(payload.get("recipe"), self.workspace)
        if not (self._cad_available or self.cad_enabled) and self._requires_cad(
            inspection
        ):
            self._check_capabilities(inspection.actions.recipe)
        return inspection

    @staticmethod
    def _requires_cad(inspection: ActionsInspection) -> bool:
        return any(
            node.operation in CAD_ACTIONS for node in inspection.actions.recipe.nodes
        )

    def _preflight_actions(self, payload: dict[str, Any]) -> ActionsInspection:
        self._check_open()
        self._keys(
            payload,
            {
                "operationId",
                "purpose",
                "generation",
                "token",
                "recipe",
                "confirmedDiscard",
                "source",
            },
        )
        if payload.get("purpose", "open-actions") != "open-actions":
            raise ScanImportError("Unknown actions purpose.", "invalid_actions")
        operation_id = payload.get("operationId")
        if (
            not isinstance(operation_id, str)
            or not 0 < len(operation_id) <= 128
            or any(
                not (c.isascii() and (c.isalnum() or c in "_-")) for c in operation_id
            )
        ):
            raise ScanImportError(
                "Expected a bounded source operation ID.", "invalid_source"
            )
        if self._pending is not None or self._candidate is not None:
            raise ScanImportError("Source work or evaluation is active.", "source_busy")
        inspection = self._inspect_actions(payload)
        if payload.get("confirmedDiscard") is not True:
            raise ScanImportError(
                "Opening actions requires discard approval.", "discard_required"
            )
        if inspection.disposition != "locate":
            if "source" in payload:
                raise ScanImportError(
                    "This project does not require source bytes.", "unexpected_source"
                )
            return inspection
        source = payload.get("source")
        if source is None:
            raise ScanImportError("Locate the saved source first.", "source_required")
        if not isinstance(source, dict):
            raise ScanImportError(
                "Expected source filename and size.", "invalid_source"
            )
        source = cast(dict[str, Any], source)
        self._keys(source, {"filename", "size"})
        filename, size = source.get("filename"), source.get("size")
        if (
            not isinstance(filename, str)
            or not 0 < len(filename) <= 512
            or not PurePosixPath(filename.replace("\\", "/")).name
            or type(size) is not int
            or size <= 0
        ):
            raise ScanImportError("Expected an exact source upload.", "invalid_source")
        if self._scan_limits is None:
            raise ScanImportError(
                "Browser source limits were not configured.", "unsupported_capability"
            )
        if size > self._scan_limits.max_source_bytes:
            raise ScanImportError("Scan exceeds the source byte limit.", "source_limit")
        saved = inspection.actions.source
        assert saved is not None and saved.descriptor is not None
        if size != saved.descriptor.source_bytes:
            raise ScanImportError(
                "Located file length differs from the saved source.", "source_mismatch"
            )
        return inspection

    def preflight_actions_json(self, payload_json: str) -> str:
        """Validate the current opening intent before the adapter reads File bytes."""

        def preflight() -> dict[str, Any]:
            if self._preparing_source:
                raise ScanImportError("Source preparation is active.", "source_busy")
            payload = self._payload(payload_json, actions=True)
            inspection = self._preflight_actions(payload)
            return {
                "generation": self._workspace_generation,
                "token": payload["token"],
                **inspection.json(),
                "purpose": "open-actions",
                "sourceKind": inspection.actions.kind,
                "requiresCad": self._requires_cad(inspection),
            }

        return self._json(preflight)

    def _prepare_actions_source(
        self,
        payload: dict[str, Any],
        source_path: str,
        progress: Callable[[ScanProgress], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[_BrowserSource, dict[str, Any]]:
        inspection = self._preflight_actions(payload)
        self._check_capabilities(inspection.actions.recipe)
        upload = inspection.disposition == "locate"
        if upload and not source_path:
            raise ScanImportError("Locate the saved source first.", "source_required")
        if not upload and source_path:
            raise ScanImportError(
                "This project does not require source bytes.", "unexpected_source"
            )
        check_cancelled(cancelled)
        warnings = tuple(deepcopy(inspection.actions.warnings))
        if inspection.actions.kind == "legacy":
            legacy = self._source.legacy
            assert legacy is not None
            source = _BrowserSource(
                None,
                replace(
                    legacy,
                    graph=FeatureGraph(legacy.workspace, inspection.actions.recipe),
                ),
                warnings,
            )
        else:
            project = ScanProject()
            initial = project.accepted
            matching = (
                self._source.project.accepted
                if self._source.project is not None
                else None
            )
            try:
                with ExitStack() as stack:
                    stream = (
                        stack.enter_context(Path(source_path).open("rb"))
                        if upload
                        else None
                    )
                    filename = (
                        PurePosixPath(
                            str(payload["source"]["filename"]).replace("\\", "/")
                        ).name
                        if upload
                        else None
                    )
                    candidate = project.prepare_open(
                        payload["recipe"],
                        generation=initial.generation,
                        token=str(initial.graph.snapshot()["token"]),
                        confirmed_discard=True,
                        matching=matching,
                        stream=stream,
                        filename=filename,
                        limits=self._scan_limits,
                        progress=progress,
                        cancelled=cancelled,
                        check_recipe=self._check_capabilities,
                    )
                _ = project.commit_open(candidate, cancelled=cancelled)
            except OSError as error:
                raise ScanImportError(
                    f"Staged source is unavailable: {error}", "source_unavailable"
                ) from error
            source = _BrowserSource(project, None, warnings)
        check_cancelled(cancelled)
        return source, {
            "purpose": "open-actions",
            "sourceKind": inspection.actions.kind,
            "uploadRequired": upload,
            "import_warning_count": len(warnings),
        }

    @staticmethod
    def _recovery_warnings(
        value: object,
    ) -> tuple[ImportWarning, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ScanImportError(
                "Invalid accepted warning checkpoint.", "invalid_recovery"
            )
        for warning in cast(list[object], value):
            if (
                not isinstance(warning, dict)
                or set(warning) != {"id", "old_name", "new_name", "kind"}
                or any(
                    not isinstance(warning[key], str)
                    for key in ("id", "old_name", "new_name", "kind")
                )
                or warning["kind"] not in {"feature", "group"}
            ):
                raise ScanImportError(
                    "Invalid accepted warning checkpoint.", "invalid_recovery"
                )
        return tuple(deepcopy(cast(list[ImportWarning], value)))

    def prepare_source_json(
        self,
        payload_json: str,
        source_path: str = "",
        *,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        recovery_generation: int | None = None,
        recovery_identity: dict[str, Any] | None = None,
        recovery_recipe: dict[str, Any] | None = None,
        recovery_import_warnings: list[ImportWarning] | None = None,
    ) -> str:
        """Prepare privately; the worker owns and deletes the staged input path.

        Recovery overrides are trusted host inputs used only on a fresh runtime.
        A successful envelope does not change the accepted source or graph.
        """

        def prepare() -> dict[str, Any]:
            if recovery_recipe is not None:
                check_actions_json(recovery_recipe)
            if self._preparing_source:
                raise ScanImportError("Source preparation is active.", "source_busy")
            self._preparing_source = True
            try:
                return self._prepare_source(
                    self._payload(payload_json, actions=True),
                    source_path,
                    progress,
                    cancelled,
                    recovery_generation,
                    recovery_identity,
                    recovery_recipe,
                    recovery_import_warnings,
                )
            finally:
                self._preparing_source = False

        return self._json(prepare)

    def _prepare_source(
        self,
        payload: dict[str, Any],
        source_path: str,
        progress: Callable[[ScanProgress], None] | None,
        cancelled: Callable[[], bool] | None,
        recovery_generation: int | None,
        recovery_identity: dict[str, Any] | None,
        recovery_recipe: dict[str, Any] | None,
        recovery_import_warnings: list[ImportWarning] | None,
    ) -> dict[str, Any]:
        self._check_open()
        self._keys(
            payload,
            {
                "operationId",
                "kind",
                "generation",
                "token",
                "filename",
                "example",
                "confirmedDiscard",
                "purpose",
                "recipe",
                "source",
            },
        )
        if self._pending is not None or self._candidate is not None:
            raise ScanImportError("Source work or evaluation is active.", "source_busy")
        operation_id, generation, token = (
            payload.get("operationId"),
            payload.get("generation"),
            payload.get("token"),
        )
        if (
            not isinstance(operation_id, str)
            or not 0 < len(operation_id) <= 128
            or any(
                not (c.isascii() and (c.isalnum() or c in "_-")) for c in operation_id
            )
        ):
            raise ScanImportError(
                "Expected a bounded source operation ID.", "invalid_source"
            )
        if (
            type(generation) is not int
            or generation != self._workspace_generation
            or token != self.graph.snapshot()["token"]
        ):
            raise ScanImportError(
                "Project changed before source preparation.", "stale_source"
            )
        recovering = any(
            value is not None
            for value in (
                recovery_generation,
                recovery_identity,
                recovery_recipe,
                recovery_import_warnings,
            )
        )
        if recovering and (
            not self._pristine
            or type(recovery_generation) is not int
            or recovery_generation < 0
            or recovery_identity is None
            or recovery_recipe is None
        ):
            raise ScanImportError(
                "Source recovery requires a pristine runtime and complete checkpoint.",
                "invalid_recovery",
            )
        check_cancelled(cancelled)
        kind = payload.get("kind")
        project: ScanProject | None = None
        facts: dict[str, Any] = {}
        if payload.get("purpose") == "open-actions":
            if recovering:
                raise ScanImportError(
                    "Recovery reconstructs an accepted source checkpoint.",
                    "invalid_recovery",
                )
            source, facts = self._prepare_actions_source(
                payload, source_path, progress, cancelled
            )
        elif "purpose" in payload or "recipe" in payload or "source" in payload:
            raise ScanImportError(
                "Unknown source preparation purpose.", "invalid_source"
            )
        elif kind == "scan":
            if self._scan_limits is None:
                raise ScanImportError(
                    "Browser source limits were not configured.",
                    "unsupported_capability",
                )
            if not recovering:
                if self._source.project is None:
                    raise ScanImportError(
                        "Add scan requires an empty project.", "source_exists"
                    )
                current = self._source.project.accepted
                self._source.project.check_add(
                    generation=current.generation, token=str(token)
                )
            filename = payload.get("filename")
            if (
                not isinstance(filename, str)
                or not 0 < len(filename) <= 512
                or not source_path
            ):
                raise ScanImportError(
                    "Expected a staged scan and filename hint.", "invalid_source"
                )
            project = ScanProject()
            empty = project.accepted
            try:
                with Path(source_path).open("rb") as stream:
                    candidate = project.prepare_add(
                        stream,
                        filename,
                        self._scan_limits,
                        generation=empty.generation,
                        token=str(empty.graph.snapshot()["token"]),
                        progress=progress,
                        cancelled=cancelled,
                    )
            except OSError as error:
                raise ScanImportError(
                    f"Staged source is unavailable: {error}", "source_unavailable"
                ) from error
            _ = project.commit(candidate, cancelled=cancelled)
            source = _BrowserSource(project, None)
        elif kind == "empty":
            if not recovering and payload.get("confirmedDiscard") is not True:
                raise ScanImportError(
                    "New project requires discard confirmation.", "discard_required"
                )
            project = ScanProject()
            source = _BrowserSource(project, None)
        elif kind == "example":
            if payload.get("example") != "nozzle":
                raise ScanImportError(
                    "Only the retained nozzle is available.", "unsupported_capability"
                )
            source = self._example_source()
        else:
            raise ScanImportError("Unknown source candidate kind.", "invalid_source")
        if recovering:
            if self._identity(source) != recovery_identity:
                raise ScanImportError(
                    "Recovery source content or interpretation changed.",
                    "recovery_source_mismatch",
                )
            recipe = Recipe.model_validate(recovery_recipe)
            self._check_capabilities(recipe)
            _ = source.graph.replace(recipe, str(source.graph.snapshot()["token"]))
            source = replace(
                source,
                import_warnings=self._recovery_warnings(recovery_import_warnings),
            )
        check_cancelled(cancelled)
        proposed_generation = (
            recovery_generation if recovering else self._workspace_generation + 1
        )
        assert proposed_generation is not None
        metadata = self._source_metadata(source, proposed_generation)
        result = {
            "operationId": operation_id,
            "workspaceGeneration": proposed_generation,
            "metadata": metadata,
            "graph": metadata["graph"],
            "source_mode": self._mode(source),
            "source_identity": self._identity(source),
            **facts,
        }
        self._check_open()
        self._candidate = _SourceCandidate(
            operation_id,
            self._source,
            self._workspace_generation,
            str(token),
            proposed_generation,
            source,
            str(metadata["graph"]["token"]),
            cast(int, metadata["graph"]["revision"]),
        )
        return result

    def promote_source_json(self, payload_json: str) -> str:
        return self._json(lambda: self._promote_source(self._payload(payload_json)))

    def _promote_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._keys(payload, {"operationId", "workspaceGeneration"})
        candidate = self._require_candidate()
        if (
            payload.get("operationId") != candidate.operation_id
            or type(payload.get("workspaceGeneration")) is not int
            or payload["workspaceGeneration"] != candidate.generation
        ):
            raise ScanImportError(
                "Source promotion does not match its candidate.", "invalid_candidate"
            )
        if (
            self._source is not candidate.previous
            or self._workspace_generation != candidate.previous_generation
            or self.graph.snapshot()["token"] != candidate.previous_token
        ):
            raise ScanImportError(
                "Project changed before source promotion.", "stale_source"
            )
        state = candidate.source.graph.snapshot()
        if (
            state["token"] != candidate.prepared_token
            or state["revision"] != candidate.prepared_revision
        ):
            raise ScanImportError("Prepared project was modified.", "invalid_candidate")
        self._source, self._workspace_generation = (
            candidate.source,
            candidate.generation,
        )
        self.example_recipe = (
            Recipe(schema_version=2, nodes=[], output=None)
            if self._source.legacy is None
            else self._source.legacy.recipe.model_copy(deep=True)
        )
        self._candidate = None
        self._pristine = False
        self.clear_export()
        self._operation_checkpoint = None
        self._last_token, self._last_error = "", None
        self._job_errors.clear()
        return self._snapshot()

    def discard_source_json(self, operation_id: str) -> str:
        def discard() -> dict[str, Any]:
            self._check_open()
            if self._candidate is None:
                if self._last_discarded == operation_id:
                    return self._snapshot()
                raise ScanImportError(
                    "No matching source candidate.", "unknown_operation"
                )
            if operation_id != self._candidate.operation_id:
                raise ScanImportError(
                    "No matching source candidate.", "unknown_operation"
                )
            self._candidate = None
            self._last_discarded = operation_id
            return self._snapshot()

        return self._json(discard)

    def checkpoint_operation(self) -> None:
        """Capture committed failure evidence before a cancellable operation.

        Scheduling and interrupt delivery belong to JavaScript. Call this before
        preparation, because explicit retry intentionally clears error memoization.
        """
        self._check_open()
        if (
            self._pending is not None
            or self._candidate is not None
            or self._preparing_source
        ):
            raise WorkspaceError(
                "Cannot checkpoint an active evaluation.", "evaluation_busy", 409
            )
        self._operation_checkpoint = _OperationCheckpoint(
            self.graph.failure_checkpoint(),
            self._job_errors.copy(),
            self._last_token,
            self._last_error,
            self._source,
            self._workspace_generation,
        )

    def restore_cancelled_json(self, recipe_json: str) -> str:
        """Restore after computation stops and the host clears its interrupt flag.

        The recipe is the host's last accepted committed recipe, without import
        collision renaming. The host starts a new publication epoch for the
        returned graph; source arrays and CAD enablement retain their identities.
        """
        self.clear_export()
        return self._json(lambda: self._restore_cancelled(recipe_json))

    def _restore_cancelled(self, recipe_json: str) -> dict[str, Any]:
        self._check_open()
        checkpoint = self._operation_checkpoint
        if checkpoint is None:
            raise WorkspaceError(
                "No operation checkpoint is available.", "checkpoint_missing", 409
            )
        if (
            checkpoint.source is not self._source
            or checkpoint.generation != self._workspace_generation
        ):
            raise ScanImportError(
                "Cancellation checkpoint belongs to another source.", "stale_source"
            )
        recipe = Recipe.model_validate(self._payload(recipe_json, recipe=True))
        self._check_capabilities(recipe)
        if self._evaluation_lease is not None:
            self._evaluation_lease.close()
            self._evaluation_lease = None
        if self._source.project is not None:
            current = self._source.project.accepted
            accepted = self._source.project.recover_graph(
                generation=current.generation,
                token=str(current.graph.snapshot()["token"]),
                recipe=recipe,
                checkpoint=checkpoint.graph,
            )
            restored = accepted.graph.snapshot()
        else:
            assert self._source.legacy is not None
            graph = FeatureGraph(self._source.legacy.workspace, checkpoint.graph.recipe)
            graph.restore_failures(checkpoint.graph)
            restored = graph.replace(recipe, checkpoint.graph.token)
            self._source = replace(
                self._source, legacy=replace(self._source.legacy, graph=graph)
            )
        token = str(restored["token"])
        self._pending = None
        self._job_errors = {
            key: error
            for key, error in checkpoint.job_errors.items()
            if key[0] == token
        }
        self._last_token = checkpoint.last_token
        self._last_error = (
            checkpoint.last_error if checkpoint.last_token == token else None
        )
        self._operation_checkpoint = None
        return self._snapshot()

    def _check_capabilities(self, recipe: Recipe) -> None:
        if self.cad_enabled:
            return
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
    def _payload(
        value: str, *, actions: bool = False, recipe: bool = False
    ) -> dict[str, Any]:
        maximum = (
            MAX_ACTIONS_JSON_BYTES
            if recipe
            else MAX_ACTIONS_REQUEST_BYTES
            if actions
            else MAX_CONTROL_JSON_BYTES
        )
        if len(value.encode()) > maximum:
            raise ValueError(
                f"expected a request within the {maximum:,}-byte JSON limit"
            )
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object request")
        if recipe:
            check_actions_json(parsed)
        elif actions:
            check_actions_envelope(parsed)
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

    def _check_mutation(self) -> None:
        if self._candidate is not None or self._preparing_source:
            raise ScanImportError(
                "A source candidate awaits its publication decision.", "source_busy"
            )
        self._pristine = False

    def _snapshot(
        self, revision: int | None = None, recipe_token: str | None = None
    ) -> dict[str, Any]:
        state = cast(
            dict[str, Any],
            self.graph.snapshot(if_revision=revision, if_recipe_token=recipe_token),
        )
        state["project_generation"] = self._workspace_generation
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
        elif isinstance(error, ScanImportError):
            code = error.code
            status = (
                409
                if code
                in {
                    "stale_source",
                    "source_busy",
                    "source_exists",
                    "unknown_operation",
                    "invalid_candidate",
                }
                else 422
            )
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

    def _json(
        self, operation: Callable[[], Any], *, if_recipe_token: str | None = None
    ) -> str:
        try:
            value = operation()
            if isinstance(value, dict) and if_recipe_token is not None:
                publication: object = cast(dict[str, Any], value).get("state", value)
                if (
                    isinstance(publication, dict)
                    and cast(dict[str, Any], publication).get("token")
                    == if_recipe_token
                ):
                    _ = cast(dict[str, Any], publication).pop("recipe", None)
            return json.dumps({"ok": True, "result": value}, allow_nan=False)
        except KeyboardInterrupt:
            self.clear_export()
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "computation_cancelled",
                        "status": 409,
                        "message": "Browser computation was cancelled.",
                    },
                },
                allow_nan=False,
            )
        except Exception as error:
            return json.dumps(
                {"ok": False, "error": self._error(error)}, allow_nan=False
            )

    def call_json(self, operation: str, payload_json: str = "{}") -> str:
        if operation == "exportCad":
            # Even malformed or rejected requests must not expose a prior ZIP.
            self.clear_export()
        actions = operation in {
            "replaceRecipe",
            "importRecipe",
            "inspectActions",
            "applyFeatureReuse",
            "faceCandidates",
            "previewFaces",
            "applyFaces",
        }
        return self._json(
            lambda: self._call(operation, self._payload(payload_json, actions=actions))
        )

    def enable_cad(self) -> None:
        """Enable existing CAD paths after the host verifies/extracts the wheel.

        A missing or incompatible import leaves the numerical workspace usable.
        This method does not install packages or alter the current recipe.
        """
        self._check_open()
        try:
            ocp = importlib.import_module("OCP")
        except ImportError as error:
            raise WorkspaceError(
                "Browser CAD runtime is unavailable; load the verified OCP assets first.",
                "cad_runtime_unavailable",
                503,
            ) from error
        if getattr(ocp, "__version__", None) != "8.0.1.1":
            raise WorkspaceError(
                "Browser CAD requires the investigated OCP 8.0.1.1 runtime.",
                "cad_runtime_incompatible",
                503,
            )
        self.cad_enabled = True

    @property
    def export_buffer(self) -> bytes:
        """Most recent successful ZIP; the host copies then clears this buffer."""
        return self._export_buffer

    def clear_export(self) -> None:
        self._export_buffer = b""

    def _call(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation == "close":
            self._keys(payload, set())
            self._closed = True
            self._pending = None
            self._job_errors.clear()
            self._operation_checkpoint = None
            self._candidate = None
            if self._evaluation_lease is not None:
                self._evaluation_lease.close()
                self._evaluation_lease = None
            self.clear_export()
            return None
        self._check_open()
        if operation == "loadWorkspace":
            self._keys(payload, set())
            return self._source_metadata(self._source, self._workspace_generation)
        if operation == "getGraph":
            self._keys(payload, {"revision", "recipe_token"})
            revision = payload.get("revision")
            if revision is not None and (type(revision) is not int or revision < 0):
                raise ValueError("expected a nonnegative integer revision")
            recipe_token = payload.get("recipe_token")
            if recipe_token is not None and (
                not isinstance(recipe_token, str)
                or not 1 <= len(recipe_token) <= 128
                or not recipe_token.isascii()
            ):
                raise ValueError("expected one known recipe token")
            return self._snapshot(revision, recipe_token)
        if operation == "inspectActions":
            self._keys(payload, {"generation", "token", "recipe"})
            inspection = self._inspect_actions(payload)
            return {
                "generation": self._workspace_generation,
                "token": payload["token"],
                **inspection.json(),
            }
        if operation == "replaceRecipe":
            self._check_mutation()
            request = GraphRequest.model_validate(payload)
            if request.recipe is None:
                raise ValueError("expected a current recipe")
            self._check_capabilities(request.recipe)
            _ = self.graph.replace(request.recipe, request.token)
            return self._snapshot()
        if operation == "importRecipe":
            self._check_mutation()
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
                "active": "nozzle" if self._source.legacy is not None else None,
                "examples": [{"id": "nozzle", "label": "Nozzle"}],
            }
        if operation == "selectExample":
            self._check_mutation()
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
            if self._source.legacy is None:
                raise WorkspaceError(
                    "Example transitions require a source candidate.",
                    "unsupported_operation",
                )
            self._source = replace(
                self._source,
                legacy=replace(
                    self._source.legacy,
                    graph=FeatureGraph(
                        self._source.legacy.workspace,
                        self.example_recipe.model_copy(deep=True),
                    ),
                ),
            )
            self._workspace_generation += 1
            self._last_token, self._last_error = "", None
            self._job_errors.clear()
            return {"active": "nozzle"}
        if operation == "applyFeatureReuse":
            self._check_mutation()
            request = ReuseAuthoringRequest.model_validate(payload)
            state = apply_feature_reuse(self.graph, request)
            return {**self._snapshot(), "reuse_authoring": state["reuse_authoring"]}
        if operation in {"faceCandidates", "previewFaces", "applyFaces", "exportCad"}:
            if operation == "applyFaces":
                self._check_mutation()
            return self._cad_call(operation, payload)
        raise WorkspaceError(
            f"Unknown browser operation: {operation}", "unsupported_operation"
        )

    def _cad_call(self, operation: str, payload: dict[str, Any]) -> Any:
        if not self.cad_enabled:
            raise WorkspaceError(
                "Browser CAD is not enabled in this deployment.",
                "unsupported_capability",
            )
        if operation == "exportCad":
            return self._export_cad(payload)
        if self._pending is not None:
            raise WorkspaceError(
                "Wait for graph evaluation before building faces.",
                "evaluation_busy",
                409,
            )
        if operation == "faceCandidates":
            from experiments.guided_face_candidates import (
                FaceCandidatesRequest,
                candidate_faces,
            )

            return candidate_faces(
                self.graph, FaceCandidatesRequest.model_validate(payload)
            )
        from experiments.face_builder import (
            FacesApplyRequest,
            FacesPreviewRequest,
            apply_faces,
            preview_faces,
        )

        if operation == "previewFaces":
            return preview_faces(
                self.graph, FacesPreviewRequest.model_validate(payload)
            )
        _ = apply_faces(self.graph, FacesApplyRequest.model_validate(payload))
        return self._snapshot()

    def _export_cad(self, payload: dict[str, Any]) -> dict[str, Any]:
        from experiments.nozzle_cad import (
            CadExportRequest,
            export_cad,
            export_face_targets,
        )

        request = CadExportRequest.model_validate(payload)
        snapshot = self._snapshot()
        if request.scope != "target":
            if self._pending is not None:
                raise WorkspaceError(
                    "Wait for graph evaluation before exporting geometry.",
                    "evaluation_busy",
                    409,
                )
            roots = export_face_targets(snapshot, request)
            roots.extend(request.review_owners)
            if request.scope == "all_faces":
                roots.extend(
                    node["id"]
                    for node in snapshot["recipe"]["nodes"]
                    if node["operation"] == "build_faces"
                )
            if request.transform is not None:
                roots.append(request.transform)
            needed, current = self.graph.readiness_status(request.token, roots)
            if needed or current is None:
                raise ValueError(
                    "export inputs are not current; evaluate them before exporting geometry"
                )
            snapshot = cast(dict[str, Any], current)
            failed = snapshot["required_failures"]
            if failed:
                labels = {
                    node["id"]: node["label"] for node in snapshot["recipe"]["nodes"]
                }
                raise ValueError(
                    (
                        "cannot export Body: "
                        if request.scope == "body"
                        else "cannot export built faces: "
                    )
                    + "; ".join(
                        f"{labels.get(key, key)!r}: {snapshot['errors'].get(key, snapshot['states'][key])}"
                        for key in failed
                    )
                )
        self._export_buffer = export_cad(self.workspace, snapshot, request)
        return {"bytes": len(self._export_buffer), "content_type": "application/zip"}

    def prepare_evaluation_json(
        self, operation: str, payload_json: str, if_recipe_token: str | None = None
    ) -> str:
        return self._json(
            lambda: self._prepare(operation, self._payload(payload_json)),
            if_recipe_token=if_recipe_token,
        )

    def _prepare(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_open()
        if self._candidate is not None or self._preparing_source:
            raise ScanImportError(
                "A source candidate awaits its publication decision.", "source_busy"
            )
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
                        "project_generation": self._workspace_generation,
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
                state["project_generation"] = self._workspace_generation
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
        lease = ExitStack()
        if self._source.project is not None:
            _ = lease.enter_context(self._source.project.evaluation())
        self._evaluation_lease = lease
        self._pending = _Evaluation(operation, request, roots)
        self._pristine = False
        self._last_token, self._last_error = request.token, None
        return {
            "run": True,
            "state": {
                "status": "running",
                "token": request.token,
                "project_generation": self._workspace_generation,
                "evaluation_running": True,
            },
        }

    def finish_evaluation_json(self, if_recipe_token: str | None = None) -> str:
        return self._json(self._finish, if_recipe_token=if_recipe_token)

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
            if self._evaluation_lease is not None:
                self._evaluation_lease.close()
                self._evaluation_lease = None
        state = cast(
            dict[str, Any],
            self.graph.readiness_snapshot(request.token, sorted(pending.roots)),
        )
        state["evaluation_running"] = False
        state["project_generation"] = self._workspace_generation
        if self._last_error is not None:
            state["evaluation_error"] = self._last_error
        return state
