"""Local HTTP adapter for the nozzle selection experiment (not a product server)."""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, Literal, cast, override
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from experiments.actions_limits import (
    MAX_ACTIONS_REQUEST_BYTES,
    MAX_CONTROL_JSON_BYTES,
    actions_limits,
    actions_profile,
    check_actions_envelope,
)
from experiments.browser_examples import (
    EXAMPLES,
    example_directory,
    example_identity,
    example_recipe_path,
)
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.face_building_limits import MAX_PAIRS, MAX_REGIONS
from experiments.feature_graph import (
    FeatureGraph,
    GraphFailureCheckpoint,
    GraphRequest,
    Recipe,
    Selection,
    Source,
    StaleGraph,
    workspace_reference_sha256,
)
from experiments.feature_recipe_import import ImportWarning, import_recipe, load_recipe
from experiments.feature_reuse_authoring import (
    ReuseAuthoringRequest,
    apply_feature_reuse,
    preview_feature_reuse,
)
from experiments.fit_solver import FitCancelled
from experiments.guided_face_candidates import FaceCandidatesRequest, candidate_faces
from experiments.native_scan_import import (
    NativeImportContext,
    NativeOpenCandidate,
    NativeScanImports,
    SourceChange,
    SourceChangePurpose,
)
from experiments.nozzle_session import NozzleSession, NozzleWorkspace, SessionFit
from experiments.project_archive import (
    ProjectArchiveLimits,
    project_archive_profile,
    read_project_archive,
    write_project_archive,
)
from experiments.project_legacy import (
    export_legacy_project,
    legacy_workspace,
    migrate_known_legacy,
)
from experiments.scan_actions import inspect_actions
from experiments.scan_project import ScanProject, virtual_scan_metadata
from experiments.scan_sources import ReplayableInput
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    check_cancelled,
)
from experiments.selection_volume_operations import dispatch as volume_operation
from experiments.virtual_scan_reduction import ReductionSettings
from scansor.selection_bundle import SelectionBundle

ASSETS = Path(__file__).with_name("browser_viewer")


class ExampleRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    example: str
    token: str


class RecipeImportRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    token: str
    recipe: dict[str, Any]


class EvaluationCancelRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    generation: int = Field(ge=0)
    evaluationId: str = Field(min_length=1, max_length=128)


class SourceCursor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    generation: int
    token: str


class SourceImportRequest(SourceCursor):
    filename: str
    size: int
    purpose: Literal["add-scan", "replace-scan"] = "add-scan"


class VirtualScanRequest(SourceCursor):
    settings: ReductionSettings


class ActivateScanRequest(SourceCursor):
    binding: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectResetRequest(SourceCursor):
    confirmedDiscard: bool


class ProjectOpenRequest(SourceCursor):
    filename: str
    size: int
    confirmedDiscard: bool


class ActionsInspectRequest(SourceCursor):
    recipe: dict[str, Any]


class ActionsSourceRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    filename: str
    size: int


class ActionsOpenRequest(ActionsInspectRequest):
    confirmedDiscard: bool
    source: ActionsSourceRequest | None = None


@dataclass(frozen=True)
class NativeAccepted:
    generation: int
    workspace: NozzleWorkspace | ScanWorkspace
    graph: FeatureGraph[Any]
    buffers: dict[str, bytes]
    import_warnings: tuple[ImportWarning, ...] = ()
    project_source: ReplayableInput | None = None
    legacy_reference: dict[str, Any] | None = None


EvaluationState = Literal[
    "queued", "running", "cancel-requested", "cancelled", "complete", "failed"
]


@dataclass
class NativeEvaluation:
    id: str
    sequence: int
    generation: int
    kind: Literal["evaluate", "ensure"]
    roots: frozenset[str]
    graph: FeatureGraph[Any]
    checkpoint: GraphFailureCheckpoint
    job_errors: dict[tuple[str, frozenset[str]], str]
    cancel: threading.Event
    state: EvaluationState = "queued"
    future: Future[dict[str, object]] | None = None

    def descriptor(self, *, done: bool | None = None) -> dict[str, object]:
        future = self.future
        if future is not None and (future.done() if done is None else done):
            if self.cancel.is_set() or future.cancelled():
                self.state = "cancelled"
            else:
                error = future.exception()
                self.state = (
                    "cancelled"
                    if isinstance(error, (StaleGraph, ScanCancelled, FitCancelled))
                    else "failed"
                    if error is not None
                    else "complete"
                )
        return {
            "id": self.id,
            "sequence": self.sequence,
            "generation": self.generation,
            "state": self.state,
            "kind": self.kind,
            "roots": sorted(self.roots),
        }


# Native admission tier; callers may supply stricter explicit caps.
# This does not describe total-process, fitting, browser or GPU capacity.
# Leave room for a 104 MiB STL and its reduced derivative. STL connectivity
# admission budgets 960 bytes per facet; reduction needs additional working space.
NATIVE_SCAN_LIMITS = ScanLimits(256 * 1024**2, 8_000_000, 8_000_000, 8 * 1024**3)
NATIVE_PROJECT_LIMITS = ProjectArchiveLimits(
    max_archive_bytes=384 * 1024**2,
    max_member_bytes=256 * 1024**2,
    max_expanded_bytes=384 * 1024**2,
)


def selection_bundle_recipe(workspace: NozzleWorkspace, bundle_path: Path) -> Recipe:
    """Start a fresh action graph from compatibility-free retained selections."""
    bundle = SelectionBundle.model_validate_json(bundle_path.read_bytes())
    if len(bundle.sources) != 1:
        raise ValueError("the nozzle viewer requires exactly one selection source")
    source = bundle.sources[0]
    if (
        source.source_sha256 != workspace.default.source_sha256
        or source.vertex_count != len(workspace.local)
    ):
        raise ValueError("selection bundle belongs to another source mesh")
    selections: list[Selection] = []
    for item in bundle.selections:
        if item.source_id != source.source_id or item.depth_mode is None:
            raise ValueError(
                "the interactive viewer requires source-bound selections with depth"
            )
        selections.append(
            Selection(
                id=item.selection_id,
                label=item.label,
                operation="selection",
                source=source.source_id,
                ids=list(item.vertex_ids),
                depth=item.depth_mode,
            )
        )
    return Recipe(
        schema_version=2,
        nodes=[
            Source(
                id=source.source_id,
                label=source.label,
                operation="source",
                source_sha256=source.source_sha256,
                reference_sha256=workspace_reference_sha256(workspace),
            ),
            *selections,
        ],
        output=selections[-1].id,
    )


def default_example_recipe(workspace: NozzleWorkspace, directory: Path) -> Recipe:
    """Built-ins use saved actions; other CLI workspaces keep retained selections."""
    identifier = example_identity(directory)
    if identifier is not None:
        return load_recipe(example_recipe_path(identifier))
    manifest = json.loads((directory / "manifest.json").read_text())
    return selection_bundle_recipe(
        workspace,
        directory
        / manifest.get("selection_bundle", "selections/user-selection-bundle.json"),
    )


class NozzleServer(ThreadingHTTPServer):
    """One bounded fit worker; HTTP remains available during fitting."""

    def __init__(
        self,
        workspace: NozzleWorkspace | None = None,
        port: int = 0,
        recipe: Recipe | None = None,
        *,
        example_path: Path | None = None,
        scan_limits: ScanLimits = NATIVE_SCAN_LIMITS,
        project_limits: ProjectArchiveLimits = NATIVE_PROJECT_LIMITS,
        staging_directory: Path | None = None,
    ) -> None:
        self.lock: threading.RLock = threading.RLock()
        self._project: ScanProject | None = ScanProject() if workspace is None else None
        self._generation_offset: int = 0
        self._legacy: NativeAccepted | None = None
        self._active_computations: int = 0
        self.active_example: str | None = example_identity(example_path)
        self.worker: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        # One graph coordinator owns publication; independent selected-data
        # numerical fits may overlap in this separate, bounded native pool.
        self.fit_worker: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="scansor-fit"
        )
        self.job: Future[SessionFit] | None = None
        self.graph_job: Future[dict[str, object]] | None = None
        self.evaluation: NativeEvaluation | None = None
        self._evaluation_sequence: int = 0
        if workspace is not None:
            initial_recipe = recipe or (
                default_example_recipe(workspace, example_path)
                if example_path is not None
                else load_recipe(
                    Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json")
                )
            )
            self.example_recipe: Recipe = initial_recipe.model_copy(deep=True)
            self._legacy = NativeAccepted(
                0,
                workspace,
                FeatureGraph(workspace, initial_recipe),
                {
                    "/mesh/positions": workspace.local.astype("<f4").tobytes(),
                    "/mesh/indices": workspace.data.triangles.astype("<u4").tobytes(),
                },
            )
        else:
            if recipe is not None or example_path is not None:
                raise ValueError("an empty server does not accept an example recipe")
            self.example_recipe = Recipe(schema_version=2, nodes=[], output=None)
        self.graph_job_token: str = ""
        self.graph_job_roots: frozenset[str] = frozenset()
        self.graph_job_errors: dict[tuple[str, frozenset[str]], str] = {}
        self.job_id: str = ""
        self.source_imports: NativeScanImports = NativeScanImports(
            lock=self.lock,
            context=self.import_context,
            limits=scan_limits,
            project_limits=project_limits,
            staging_directory=staging_directory,
        )
        try:
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            self.source_imports.close()
            self.worker.shutdown(wait=True, cancel_futures=True)
            self.fit_worker.shutdown(wait=True, cancel_futures=True)
            raise

    def accepted(self, *, buffers: bool = False) -> NativeAccepted:
        """Capture one authoritative generation, never independently mirrored fields."""
        with self.lock:
            if self._project is None:
                assert self._legacy is not None
                return self._legacy
            accepted = self._project.accepted
            return NativeAccepted(
                self._generation_offset + accepted.generation,
                accepted.workspace,
                accepted.graph,
                {
                    "/mesh/positions": accepted.positions.tobytes(),
                    "/mesh/indices": accepted.indices.tobytes(),
                }
                if buffers
                else {},
                accepted.import_warnings,
            )

    @property
    def workspace(self) -> Any:
        # The compatibility facade changes mode after reset/example selection.
        # New operations use the explicitly typed captured accepted record.
        return self.accepted().workspace

    @property
    def graph(self) -> FeatureGraph[Any]:
        return self.accepted().graph

    @graph.setter
    def graph(self, graph: FeatureGraph[Any]) -> None:
        # Existing native tests deliberately install an already evaluated legacy
        # graph; generic state always remains owned by ScanProject.
        with self.lock:
            if self._legacy is None or self._project is not None:
                raise ValueError("generic graphs are owned by the source project")
            self._legacy = replace(self._legacy, graph=graph)

    @property
    def buffers(self) -> dict[str, bytes]:
        return self.accepted(buffers=True).buffers

    @property
    def is_generic(self) -> bool:
        return self._project is not None

    def virtual_scan_profile(self) -> dict[str, object]:
        with self.lock:
            return virtual_scan_metadata(
                self._project.accepted if self._project is not None else None
            )

    def busy(self) -> bool:
        return self._active_computations > 0 or any(
            job is not None and not job.done() for job in (self.job, self.graph_job)
        )

    def check_generation(self, value: str | None) -> NativeAccepted:
        state = self.accepted()
        if value is None and self._project is None:
            return state
        if value != str(state.generation):
            raise ScanImportError(
                "Source generation changed; reload the workspace.", "stale_source"
            )
        return state

    def import_context(self) -> NativeImportContext:
        if self.busy():
            raise ScanImportError(
                "Wait for the active computation before importing.", "source_busy"
            )
        state = self.accepted()
        original_token = str(state.graph.snapshot()["token"])
        original_example = self.active_example
        original_recipe = self.example_recipe
        owner = self._project if self._project is not None else self._legacy
        matching = self._project.accepted if self._project is not None else None

        def prepare_open(
            value: object,
            source_input: ReplayableInput | None,
            filename: str,
            progress: Callable[[ScanProgress], None],
            cancelled: Callable[[], bool],
        ) -> NativeOpenCandidate:
            inspection = inspect_actions(
                value,
                state.workspace,
                retained_sources=matching.retained_sources
                if matching is not None
                else (),
            )
            check_cancelled(cancelled)
            project: ScanProject | None = None
            legacy: NativeAccepted | None = None
            if inspection.actions.kind == "legacy":
                graph = FeatureGraph(state.workspace, inspection.actions.recipe)
                # Reuse only the explicitly captured immutable legacy display/source.
                assert isinstance(owner, NativeAccepted)
                legacy = NativeAccepted(
                    state.generation + 1,
                    state.workspace,
                    graph,
                    owner.buffers,
                    inspection.actions.warnings,
                    owner.project_source,
                    owner.legacy_reference,
                )
                warning_count = len(legacy.import_warnings)
                next_example = original_example
                next_example_recipe = original_recipe.model_copy(deep=True)
            else:
                project = ScanProject()
                initial = project.accepted
                candidate = project.prepare_open(
                    value,
                    generation=initial.generation,
                    token=str(initial.graph.snapshot()["token"]),
                    confirmed_discard=True,
                    matching=matching,
                    stream=source_input,
                    filename=filename or None,
                    limits=self.source_imports.limits,
                    progress=progress,
                    cancelled=cancelled,
                )
                prepared = project.commit_open(candidate, cancelled=cancelled)
                warning_count = len(prepared.import_warnings)
                next_example = None
                next_example_recipe = Recipe(schema_version=2, nodes=[], output=None)
            check_cancelled(cancelled)

            def install() -> int:
                current = self.accepted()
                current_owner = (
                    self._project if self._project is not None else self._legacy
                )
                if (
                    current_owner is not owner
                    or current.generation != state.generation
                    or current.graph.snapshot()["token"] != original_token
                ):
                    raise ScanImportError(
                        "Project changed before opening actions.", "stale_source"
                    )
                if self.busy():
                    raise ScanImportError("Wait for active computation.", "source_busy")
                check_cancelled(cancelled)
                self.clear_jobs()
                self.active_example = next_example
                self.example_recipe = next_example_recipe
                if project is not None:
                    self._generation_offset = (
                        state.generation + 1 - project.accepted.generation
                    )
                    self._project, self._legacy = project, None
                else:
                    assert legacy is not None
                    self._project, self._legacy = None, legacy
                return state.generation + 1

            return NativeOpenCandidate(install, warning_count)

        def prepare_replace(
            source_input: ReplayableInput,
            filename: str,
            progress: Callable[[ScanProgress], None],
            cancelled: Callable[[], bool],
        ) -> NativeOpenCandidate:
            if matching is None or matching.workspace.descriptor is None:
                raise ScanImportError(
                    "Replace scan requires an imported scan; retained example frames are not supported.",
                    "unsupported_replacement",
                )
            project = ScanProject()
            initial = project.accepted
            candidate = project.prepare_replace(
                source_input,
                filename,
                self.source_imports.limits,
                generation=initial.generation,
                token=str(initial.graph.snapshot()["token"]),
                matching=matching,
                progress=progress,
                cancelled=cancelled,
            )
            prepared = project.commit_replace(candidate, cancelled=cancelled)
            check_cancelled(cancelled)

            def install() -> int:
                current = self.accepted()
                current_owner = (
                    self._project if self._project is not None else self._legacy
                )
                if (
                    current_owner is not owner
                    or current.generation != state.generation
                    or current.graph.snapshot()["token"] != original_token
                ):
                    raise ScanImportError(
                        "Project changed before replacing the scan.", "stale_source"
                    )
                if self.busy():
                    raise ScanImportError("Wait for active computation.", "source_busy")
                check_cancelled(cancelled)
                self.clear_jobs()
                self.active_example = None
                self.example_recipe = Recipe(schema_version=2, nodes=[], output=None)
                self._generation_offset = (
                    state.generation + 1 - project.accepted.generation
                )
                self._project, self._legacy = project, None
                return state.generation + 1

            return NativeOpenCandidate(install, len(prepared.import_warnings))

        def prepare_project(
            source_input: ReplayableInput,
            progress: Callable[[ScanProgress], None],
            cancelled: Callable[[], bool],
        ) -> NativeOpenCandidate:
            archive = read_project_archive(
                source_input,
                limits=self.source_imports.project_limits,
                cancelled=cancelled,
                legacy_resolver=migrate_known_legacy,
            )
            check_cancelled(cancelled)
            project: ScanProject | None = None
            legacy: NativeAccepted | None = None
            if archive.legacy_reference is not None:
                if archive.original is None:
                    raise ScanImportError("Legacy project has no source member.")
                workspace = legacy_workspace(
                    archive.original,
                    archive.legacy_reference,
                    self.source_imports.limits,
                    progress=progress,
                    cancelled=cancelled,
                )
                inspected = inspect_actions(archive.actions, workspace)
                legacy = NativeAccepted(
                    state.generation + 1,
                    workspace,
                    FeatureGraph(workspace, inspected.actions.recipe),
                    {
                        "/mesh/positions": workspace.local.astype("<f4").tobytes(),
                        "/mesh/indices": workspace.data.triangles.astype(
                            "<u4"
                        ).tobytes(),
                    },
                    inspected.actions.warnings,
                    archive.original,
                    archive.legacy_reference,
                )
                warning_count = len(legacy.import_warnings)
            else:
                project = ScanProject()
                initial = project.accepted
                candidate = project.prepare_open(
                    archive.actions,
                    generation=initial.generation,
                    token=str(initial.graph.snapshot()["token"]),
                    confirmed_discard=True,
                    stream=archive.original,
                    filename=archive.original.filename if archive.original else None,
                    limits=self.source_imports.limits,
                    progress=progress,
                    cancelled=cancelled,
                )
                prepared = project.commit_open(candidate, cancelled=cancelled)
                warning_count = len(prepared.import_warnings)
            check_cancelled(cancelled)

            def install() -> int:
                current = self.accepted()
                current_owner = (
                    self._project if self._project is not None else self._legacy
                )
                if (
                    current_owner is not owner
                    or current.generation != state.generation
                    or current.graph.snapshot()["token"] != original_token
                ):
                    raise ScanImportError(
                        "Project changed before opening the archive.", "stale_source"
                    )
                if self.busy():
                    raise ScanImportError("Wait for active computation.", "source_busy")
                check_cancelled(cancelled)
                self.clear_jobs()
                self.active_example = None
                self.example_recipe = Recipe(schema_version=2, nodes=[], output=None)
                if project is not None:
                    self._generation_offset = (
                        state.generation + 1 - project.accepted.generation
                    )
                    self._project, self._legacy = project, None
                else:
                    assert legacy is not None
                    self._project, self._legacy = None, legacy
                return state.generation + 1

            return NativeOpenCandidate(install, warning_count)

        def prepare_source_change(
            purpose: SourceChangePurpose,
            change: SourceChange,
            progress: Callable[[ScanProgress], None],
            cancelled: Callable[[], bool],
        ) -> NativeOpenCandidate:
            if matching is None or matching.workspace.descriptor is None:
                raise ScanImportError(
                    "Virtual scans require an imported original scan.",
                    "unsupported_virtual_scan",
                )
            project = ScanProject()
            initial = project.accepted
            if purpose == "create-virtual-scan":
                assert isinstance(change, ReductionSettings)
                candidate = project.prepare_virtual_scan(
                    change,
                    self.source_imports.limits,
                    generation=initial.generation,
                    token=str(initial.graph.snapshot()["token"]),
                    matching=matching,
                    progress=progress,
                    cancelled=cancelled,
                )
            else:
                assert isinstance(change, str)
                candidate = project.prepare_activate_scan(
                    change,
                    self.source_imports.limits,
                    generation=initial.generation,
                    token=str(initial.graph.snapshot()["token"]),
                    matching=matching,
                    progress=progress,
                    cancelled=cancelled,
                )
            prepared = project.commit_replace(candidate, cancelled=cancelled)
            check_cancelled(cancelled)

            def install() -> int:
                current = self.accepted()
                current_owner = (
                    self._project if self._project is not None else self._legacy
                )
                if (
                    current_owner is not owner
                    or current.generation != state.generation
                    or current.graph.snapshot()["token"] != original_token
                ):
                    raise ScanImportError(
                        "Project changed before switching scans.", "stale_source"
                    )
                if self.busy():
                    raise ScanImportError("Wait for active computation.", "source_busy")
                check_cancelled(cancelled)
                self.clear_jobs()
                self.active_example = None
                self.example_recipe = Recipe(schema_version=2, nodes=[], output=None)
                self._generation_offset = (
                    state.generation + 1 - project.accepted.generation
                )
                self._project, self._legacy = project, None
                return state.generation + 1

            return NativeOpenCandidate(install, len(prepared.import_warnings))

        return NativeImportContext(
            self._project,
            state.generation,
            str(state.graph.snapshot()["token"]),
            owner,
            state.workspace,
            prepare_open,
            prepare_replace,
            prepare_source_change,
            prepare_project,
        )

    def project_export_inputs(
        self,
        state: NativeAccepted,
        token: str,
    ) -> tuple[dict[str, Any], ReplayableInput | None, dict[str, Any] | None]:
        try:
            actions = state.graph.export_recipe(token)
        except StaleGraph as error:
            raise ScanImportError(str(error), "stale_source") from error
        source_record = next(
            (node for node in actions["nodes"] if node["operation"] == "source"), None
        )
        source = (
            Source.model_validate(source_record) if source_record is not None else None
        )
        if source is None:
            return actions, None, None
        if isinstance(state.workspace, NozzleWorkspace):
            original, reference = export_legacy_project(
                state.workspace, state.project_source
            )
            return actions, original, state.legacy_reference or reference
        descriptor = (
            source.virtual_scan.original if source.virtual_scan else source.descriptor
        )
        if descriptor is None or self._project is None:
            raise ScanImportError("The project has no replayable original scan.")
        original = next(
            (
                item.source
                for item in (
                    state.workspace,
                    *self._project.accepted.retained_sources,
                )
                if item.descriptor is not None
                and item.descriptor.semantic_record() == descriptor.semantic_record()
            ),
            None,
        )
        if original is None:
            raise ScanImportError(
                "The exact original scan is unavailable.", "source_required"
            )
        return actions, original.input, None

    def inspect_actions(self, request: ActionsInspectRequest) -> dict[str, object]:
        with self.lock:
            state = self.accepted()
            if (
                request.generation != state.generation
                or request.token != state.graph.snapshot()["token"]
            ):
                raise ScanImportError(
                    "Project changed before inspecting actions.", "stale_source"
                )
            return {
                "generation": state.generation,
                "token": request.token,
                **inspect_actions(
                    request.recipe,
                    state.workspace,
                    retained_sources=self._project.accepted.retained_sources
                    if self._project is not None
                    else (),
                ).json(),
            }

    def reset_project(self, request: ProjectResetRequest) -> NativeAccepted:
        with self.lock:
            state = self.accepted()
            if (
                request.generation != state.generation
                or request.token != state.graph.snapshot()["token"]
            ):
                raise ScanImportError("Project changed before reset.", "stale_source")
            if not request.confirmedDiscard:
                raise ScanImportError(
                    "Project reset requires explicit discard confirmation.",
                    "discard_required",
                )
            if self.busy() or self.source_imports.busy:
                raise ScanImportError(
                    "Wait for active work before resetting the project.", "source_busy"
                )
            if self._project is None:
                self._project = ScanProject()
                self._generation_offset = state.generation + 1
                self._legacy = None
            else:
                accepted = self._project.accepted
                _ = self._project.reset(
                    generation=accepted.generation, token=request.token
                )
            self.active_example = None
            self.example_recipe = Recipe(schema_version=2, nodes=[], output=None)
            self.clear_jobs()
            return self.accepted()

    def clear_jobs(self) -> None:
        self.job = None
        self.job_id = ""
        self.graph_job = None
        self.graph_job_token = ""
        self.graph_job_roots = frozenset()
        self.graph_job_errors.clear()
        self.evaluation = None

    @contextmanager
    def computation(self) -> Generator[NativeAccepted]:
        with self.lock:
            if self.busy() or self.source_imports.busy:
                raise ScanImportError(
                    "Wait for active work before computing.", "source_busy"
                )
            stack = ExitStack()
            if self._project is not None:
                _ = stack.enter_context(self._project.evaluation())
            state = self.accepted()
            self._active_computations += 1
        try:
            yield state
        finally:
            with self.lock:
                stack.close()
                self._active_computations -= 1

    def submit[T](self, callback: Callable[..., T], *args: Any) -> Future[T]:
        lease = ExitStack()
        state = lease.enter_context(self.computation())
        state.graph.fit_executor = self.fit_worker

        def run() -> T:
            with lease:
                return callback(*args)

        try:
            future = self.worker.submit(run)
            future.add_done_callback(
                lambda done: lease.close() if done.cancelled() else None
            )
            return future
        except BaseException:
            lease.close()
            raise

    def graph_job_error(self, *, done: bool | None = None) -> BaseException | None:
        job = self.graph_job
        if job is None or not (job.done() if done is None else done) or job.cancelled():
            return None
        if (
            self.evaluation is not None
            and self.evaluation.future is job
            and self.evaluation.cancel.is_set()
        ):
            return None
        error = job.exception()
        return (
            None
            if isinstance(error, (StaleGraph, ScanCancelled, FitCancelled))
            else error
        )

    def evaluation_snapshot(
        self, state: dict[str, Any], *, running: bool | None = None
    ) -> dict[str, Any]:
        """Lifecycle fields remain observable even without a graph revision."""
        with self.lock:
            if running is None:
                running = self.graph_job is not None and not self.graph_job.done()
            state["evaluation"] = (
                self.evaluation.descriptor(done=not running)
                if self.evaluation is not None
                else None
            )
            state["evaluation_running"] = running
            return state

    def start_evaluation(
        self,
        request: GraphRequest,
        kind: Literal["evaluate", "ensure"],
        roots: frozenset[str],
    ) -> dict[str, Any]:
        with self.lock:
            accepted = self.accepted()
            graph = accepted.graph
            if request.token != graph.snapshot()["token"]:
                raise StaleGraph("graph changed before evaluation")
            record = NativeEvaluation(
                uuid.uuid4().hex,
                self._evaluation_sequence + 1,
                accepted.generation,
                kind,
                roots,
                graph,
                graph.failure_checkpoint(),
                self.graph_job_errors.copy(),
                threading.Event(),
            )

            def run() -> dict[str, object]:
                with self.lock:
                    check_cancelled(record.cancel.is_set)
                    record.state = "running"
                if kind == "ensure":
                    return graph.ensure_current(
                        request.token,
                        request.targets,
                        request.all_actions,
                        cancelled=record.cancel.is_set,
                    )
                return graph.evaluate(
                    request.token,
                    request.target,
                    request.all_actions,
                    cancelled=record.cancel.is_set,
                )

            record.future = self.submit(run)
            self._evaluation_sequence = record.sequence
            self.evaluation = record
            self.graph_job = record.future
            self.graph_job_token = request.token
            self.graph_job_roots = roots
            if kind == "evaluate":
                self.graph_job_errors.clear()
            return self.evaluation_snapshot({"status": "running"})

    def cancel_evaluation(self, request: EvaluationCancelRequest) -> dict[str, Any]:
        with self.lock:
            accepted = self.accepted()
            if request.generation != accepted.generation:
                raise ScanImportError(
                    "Cancellation belongs to another source generation.", "stale_source"
                )
            record = self.evaluation
            if (
                record is None
                or record.id != request.evaluationId
                or record.graph is not accepted.graph
            ):
                raise ScanImportError(
                    "The requested evaluation is no longer current.", "stale_evaluation"
                )
            descriptor = record.descriptor()
            if descriptor["state"] in {"queued", "running", "cancel-requested"}:
                if not record.cancel.is_set():
                    record.cancel.set()
                    record.state = "cancel-requested"
                    _ = record.graph.discard_evaluation(record.checkpoint)
                    current_token = record.graph.snapshot()["token"]
                    self.graph_job_errors = {
                        key: error
                        for key, error in record.job_errors.items()
                        if key[0] == current_token
                    }
                if record.future is not None:
                    _ = record.future.cancel()
            return self.evaluation_snapshot(record.graph.snapshot())

    def select_example(
        self, request: ExampleRequest, generation: int | None = None
    ) -> None:
        """Install a fresh workspace only after its source and recipe validate."""
        with self.lock:
            previous = self.accepted()
            if request.token != previous.graph.snapshot()["token"] or (
                generation is not None and generation != previous.generation
            ):
                raise StaleGraph("graph changed before switching examples")
            if self.busy() or self.source_imports.busy:
                raise StaleGraph(
                    "wait for the current evaluation before switching examples"
                )
        directory = example_directory(request.example)
        workspace = NozzleWorkspace(directory)
        recipe = default_example_recipe(workspace, directory)
        graph = FeatureGraph(workspace, recipe)
        buffers = {
            "/mesh/positions": workspace.local.astype("<f4").tobytes(),
            "/mesh/indices": workspace.data.triangles.astype("<u4").tobytes(),
        }
        with self.lock:
            current = self.accepted()
            if (
                current.generation != previous.generation
                or request.token != current.graph.snapshot()["token"]
            ):
                raise StaleGraph("project changed while preparing the example")
            if self.busy() or self.source_imports.busy:
                raise StaleGraph("wait for active work before switching examples")
            self._legacy = NativeAccepted(
                previous.generation + 1, workspace, graph, buffers
            )
            self._project = None
            self.example_recipe = recipe.model_copy(deep=True)
            self.active_example = request.example
            self.clear_jobs()

    @override
    def server_close(self) -> None:
        self.source_imports.begin_close()
        super().server_close()
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.fit_worker.shutdown(wait=True, cancel_futures=True)
        self.source_imports.close()
        with self.lock:
            self.clear_jobs()
            self._project, self._legacy = None, None


class Handler(BaseHTTPRequestHandler):
    reply_generation: int | None = None

    @property
    def app(self) -> NozzleServer:
        assert isinstance(self.server, NozzleServer)
        return self.server

    files: ClassVar[dict[str, tuple[str, str]]] = {
        "/navigation-math.js": ("navigation-math.js", "text/javascript"),
        "/navigation.js": ("navigation.js", "text/javascript"),
        "/vendor/TrackballControls.js": (
            "node_modules/three/examples/jsm/controls/TrackballControls.js",
            "text/javascript",
        ),
        "/": ("index.html", "text/html"),
        "/app.js": ("app.js", "text/javascript"),
        "/source-project.js": ("source-project.js", "text/javascript"),
        "/workspace.js": ("dist/workspace.js", "text/javascript"),
        "/workspace.css": ("dist/workspace.css", "text/css"),
        "/workspace-state.js": ("workspace-state.js", "text/javascript"),
        "/action-tree.js": ("action-tree.js", "text/javascript"),
        "/display-transform.js": ("display-transform.js", "text/javascript"),
        "/cad-export.js": ("cad-export.js", "text/javascript"),
        "/body-ui.js": ("body-ui.js", "text/javascript"),
        "/feature-graph-view.js": ("feature-graph-view.js", "text/javascript"),
        "/feature-names.js": ("feature-names.js", "text/javascript"),
        "/feature-inputs.js": ("feature-inputs.js", "text/javascript"),
        "/relationship-inputs.js": ("relationship-inputs.js", "text/javascript"),
        "/residual-display.js": ("residual-display.js", "text/javascript"),
        "/reuse-volume.js": ("reuse-volume.js", "text/javascript"),
        "/surface-trims.js": ("surface-trims.js", "text/javascript"),
        "/graph-evaluation.js": ("graph-evaluation.js", "text/javascript"),
        "/backend.js": ("backend.js", "text/javascript"),
        "/backend-config.js": ("backend-config.js", "text/javascript"),
        "/bootstrap.js": ("bootstrap.js", "text/javascript"),
        "/worker-backend.js": ("worker-backend.js", "text/javascript"),
        "/http-backend.js": ("http-backend.js", "text/javascript"),
        "/edge-highlight.js": ("edge-highlight.js", "text/javascript"),
        "/fit-footprint.js": ("fit-footprint.js", "text/javascript"),
        "/analytic-geometry.js": ("analytic-geometry.js", "text/javascript"),
        "/fit-intent.js": ("fit-intent.js", "text/javascript"),
        "/model-picking.js": ("model-picking.js", "text/javascript"),
        "/selection.js": ("selection.js", "text/javascript"),
        "/selection-owned-editor.js": ("selection-owned-editor.js", "text/javascript"),
        "/circular-reuse-editor.js": ("circular-reuse-editor.js", "text/javascript"),
        "/style.css": ("style.css", "text/css"),
        "/vendor/three.module.js": (
            "node_modules/three/build/three.module.js",
            "text/javascript",
        ),
        "/vendor/three.core.js": (
            "node_modules/three/build/three.core.js",
            "text/javascript",
        ),
        "/vendor/OrbitControls.js": (
            "node_modules/three/examples/jsm/controls/OrbitControls.js",
            "text/javascript",
        ),
        "/vendor/LICENSE": ("node_modules/three/LICENSE", "text/plain"),
        **{
            f"/vendor/lines/{name}.js": (
                f"node_modules/three/examples/jsm/lines/{name}.js",
                "text/javascript",
            )
            for name in (
                "Line2",
                "LineGeometry",
                "LineMaterial",
                "LineSegments2",
                "LineSegmentsGeometry",
            )
        },
    }

    def reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if (generation := self.reply_generation) is not None:
            self.send_header("X-Scansor-Generation", str(generation))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        _ = self.wfile.write(body)

    def json_reply(self, status: int, value: object) -> None:
        if isinstance(value, dict) and (
            self.app.is_generic
            or self.path == "/api/meta"
            or self.path.startswith("/api/sources/imports")
            or self.path == "/api/project/reset"
            or self.headers.get("X-Scansor-Generation") is not None
        ):
            value = {
                **value,
                "project_generation": self.reply_generation
                if self.reply_generation is not None
                else self.app.accepted().generation,
            }
        self.reply(
            status, json.dumps(value, allow_nan=False).encode(), "application/json"
        )

    def allowed(self) -> bool:
        # Reject cross-origin browser requests and DNS rebinding; native clients
        # can omit Origin, but mutations still require the explicit JSON header.
        host = f"127.0.0.1:{self.app.server_port}"
        if (
            self.headers.get("Host") != host
            or self.headers.get("Origin", f"http://{host}") != f"http://{host}"
        ):
            self.json_reply(403, {"error": "use the printed local URL"})
            return False
        return True

    def do_GET(self) -> None:
        if not self.allowed():
            return
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/sources/imports/"):
            operation_id = parsed.path.removeprefix("/api/sources/imports/")
            try:
                self.json_reply(200, self.app.source_imports.status(operation_id))
            except ScanImportError as error:
                self.source_error(error)
        elif parsed.path in {"/mesh/positions", "/mesh/indices"}:
            try:
                with self.app.lock:
                    query = parse_qs(
                        parsed.query, keep_blank_values=True, strict_parsing=True
                    )
                    if query:
                        values = query.get("generation", [])
                        if set(query) != {"generation"} or len(values) != 1:
                            raise ScanImportError(
                                "Expected one source generation.", "stale_source"
                            )
                        _ = self.app.check_generation(values[0])
                    elif self.app.is_generic:
                        raise ScanImportError(
                            "Mesh reads require their source generation.",
                            "stale_source",
                        )
                    state = self.app.accepted(buffers=True)
                    self.reply_generation = state.generation
                    data = state.buffers[parsed.path]
                self.reply(200, data, "application/octet-stream")
            except (ScanImportError, ValueError) as error:
                self.source_error(
                    error
                    if isinstance(error, ScanImportError)
                    else ScanImportError(str(error))
                )
        elif self.path == "/api/examples":
            with self.app.lock:
                self.json_reply(
                    200, {"active": self.app.active_example, "examples": EXAMPLES}
                )
        elif self.path == "/api/meta":
            with self.app.lock:
                state = self.app.accepted()
                self.reply_generation = state.generation
                metadata = state.workspace.metadata()
                capabilities = cast(dict[str, object], metadata.get("capabilities", {}))
                graph = self.app.evaluation_snapshot(
                    {
                        **state.graph.snapshot(),
                        "project_generation": state.generation,
                    }
                )
                metadata = {
                    **metadata,
                    "import_warnings": list(state.import_warnings),
                    "virtual_scans": self.app.virtual_scan_profile(),
                    "project_archive": project_archive_profile(
                        self.app.source_imports.project_limits
                    ),
                    "legacy_project": state.legacy_reference is not None,
                    "graph": graph,
                    "positions": {
                        "dtype": "<f4",
                        "components": 3,
                        "url": f"/mesh/positions?generation={state.generation}",
                    },
                    "indices": {
                        "dtype": "<u4",
                        "components": 3,
                        "url": f"/mesh/indices?generation={state.generation}",
                    },
                    "capabilities": {
                        **capabilities,
                        "source_actions": actions_profile(),
                        "source_import": {
                            "supported": True,
                            "formats": [
                                "binary-triangle-ply-v1",
                                "triangle-stl-f32-v1",
                            ],
                            "max_sources": 1,
                            "limits": asdict(self.app.source_imports.limits),
                            "cancel": True,
                            "recovery": "active-workspace-original-bytes",
                        },
                    },
                    "face_building_limits": {
                        "max_pair_checks": MAX_PAIRS,
                        "max_regions": MAX_REGIONS,
                    },
                }
            metadata["actions_limits"] = actions_limits()
            metadata["selection_entries"] = {"version": 1}
            metadata["selection_volumes"] = {"version": 1, "max_preview_cells": 2048}
            metadata["source_replacement"] = {
                "supported": isinstance(state.workspace, ScanWorkspace)
                and state.workspace.descriptor is not None,
                "version": 1,
                "alignment": "already-aligned",
                "source_kind": "generic-scan",
            }
            self.json_reply(200, metadata)
        elif self.path == "/api/graph/example":
            with self.app.lock:
                self.json_reply(200, self.app.example_recipe.model_dump())
        elif self.path == "/api/graph" or self.path.startswith("/api/graph?"):
            try:
                query = parse_qs(
                    urlsplit(self.path).query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
                revision = None
                recipe_token = None
                if query:
                    if set(query) - {"revision", "recipe_token"}:
                        raise ValueError("expected one nonnegative integer revision")
                    if "revision" in query:
                        values = query["revision"]
                        if (
                            len(values) != 1
                            or not values[0].isascii()
                            or not values[0].isdecimal()
                        ):
                            raise ValueError(
                                "expected one nonnegative integer revision"
                            )
                        revision = int(values[0])
                    if "recipe_token" in query:
                        values = query["recipe_token"]
                        if (
                            len(values) != 1
                            or not 1 <= len(values[0]) <= 128
                            or not values[0].isascii()
                        ):
                            raise ValueError("expected one known recipe token")
                        recipe_token = values[0]
            except ValueError as error:
                self.json_reply(422, {"error": str(error)})
                return
            with self.app.lock, self.app.graph.lock:
                self.reply_generation = self.app.accepted().generation
                state = self.app.graph.snapshot(
                    if_revision=revision, if_recipe_token=recipe_token
                )
                job, token = self.app.graph_job, self.app.graph_job_token
                running = job is not None and not job.done()
                if (
                    job is not None
                    and not running
                    and state.get("evaluation_origin_token") == token
                ):
                    token = str(state["token"])
                    self.app.graph_job_token = token
                state = self.app.evaluation_snapshot(state, running=running)
                if job is not None and not running and token == state["token"]:
                    error = self.app.graph_job_error(done=True)
                    if error is not None:
                        state["evaluation_error"] = str(error)
            self.json_reply(200, state)
        elif self.path.startswith("/api/fit/"):
            with self.app.lock:
                job, job_id = self.app.job, self.app.job_id
            if job is None or self.path != f"/api/fit/{job_id}":
                self.json_reply(404, {"error": "fit no longer available; run it again"})
            elif not job.done():
                self.json_reply(200, {"status": "running"})
            else:
                try:
                    self.json_reply(200, {"status": "complete", "result": job.result()})
                except Exception as error:  # A failed fit is a visible job outcome.
                    self.json_reply(200, {"status": "failed", "error": str(error)})
        elif (asset_path := urlsplit(self.path).path) in self.files:
            filename, mime = self.files[asset_path]
            try:
                self.reply(200, (ASSETS / filename).read_bytes(), mime)
            except FileNotFoundError:
                self.json_reply(
                    404,
                    {
                        "error": "missing asset; run npm ci then npm run build in experiments/browser_viewer"
                    },
                )
        else:
            self.json_reply(404, {"error": "not found"})

    def source_error(self, error: ScanImportError) -> None:
        status = (
            404
            if error.code == "unknown_operation"
            else 409
            if error.code
            in {
                "stale_source",
                "stale_evaluation",
                "source_exists",
                "source_busy",
                "import_closed",
            }
            else 422
        )
        # A disconnected upload still retains its terminal operation diagnostic;
        # there may no longer be an HTTP observer to receive this response.
        with suppress(BrokenPipeError, ConnectionResetError):
            self.json_reply(status, {"error": str(error), "code": error.code})

    def fence_source(self) -> NativeAccepted:
        state = self.app.check_generation(self.headers.get("X-Scansor-Generation"))
        self.reply_generation = state.generation
        return state

    def source_POST(self) -> bool:
        """Handle bounded source controls and separate original-byte transport."""
        path = urlsplit(self.path).path
        if path not in {
            "/api/sources/imports",
            "/api/sources/virtual",
            "/api/sources/activate",
            "/api/project/reset",
            "/api/project/open",
            "/api/project/export",
            "/api/actions/inspect",
            "/api/actions/open",
        } and not path.startswith("/api/sources/imports/"):
            return False
        binary = path.endswith("/bytes")
        expected_type = "application/octet-stream" if binary else "application/json"
        if (
            self.headers.get("X-Scansor-Request") != "1"
            or self.headers.get("Content-Type") != expected_type
        ):
            self.json_reply(403, {"error": "expected explicit source request"})
            return True
        try:
            raw_length = self.headers.get("Content-Length", "")
            if (
                not raw_length.isascii()
                or not raw_length.isdecimal()
                or self.headers.get("Transfer-Encoding") is not None
            ):
                raise ScanImportError(
                    "Expected an exact Content-Length.", "invalid_upload"
                )
            length = int(raw_length)
            self.connection.settimeout(5)
            if binary:
                operation_id = path.removeprefix("/api/sources/imports/").removesuffix(
                    "/bytes"
                )
                result = self.app.source_imports.receive(
                    operation_id, cast(BinaryIO, cast(object, self.rfile)), length
                )
                self.json_reply(200 if result["terminal"] else 202, result)
                return True
            actions = path in {"/api/actions/inspect", "/api/actions/open"}
            maximum = MAX_ACTIONS_REQUEST_BYTES if actions else MAX_CONTROL_JSON_BYTES
            if not 0 < length <= maximum:
                raise ScanImportError(
                    f"Expected source controls within the {maximum:,}-byte request limit.",
                    "invalid_upload",
                )
            body = self.rfile.read(length)
            if len(body) != length:
                raise ScanImportError(
                    "Truncated source control request.", "invalid_upload"
                )
            if actions:
                check_actions_envelope(json.loads(body))
            if path == "/api/sources/imports":
                request = SourceImportRequest.model_validate_json(body)
                result = self.app.source_imports.reserve(
                    generation=request.generation,
                    token=request.token,
                    filename=request.filename,
                    size=request.size,
                    purpose=request.purpose,
                )
                self.json_reply(202, result)
            elif path == "/api/sources/virtual":
                request = VirtualScanRequest.model_validate_json(body)
                result = self.app.source_imports.change_source(
                    generation=request.generation,
                    token=request.token,
                    purpose="create-virtual-scan",
                    change=request.settings,
                )
                self.json_reply(202, result)
            elif path == "/api/sources/activate":
                request = ActivateScanRequest.model_validate_json(body)
                result = self.app.source_imports.change_source(
                    generation=request.generation,
                    token=request.token,
                    purpose="activate-scan",
                    change=request.binding,
                )
                self.json_reply(202, result)
            elif path == "/api/actions/inspect":
                result = self.app.inspect_actions(
                    ActionsInspectRequest.model_validate_json(body)
                )
                self.json_reply(200, result)
            elif path == "/api/actions/open":
                request = ActionsOpenRequest.model_validate_json(body)
                result = self.app.source_imports.open_actions(
                    generation=request.generation,
                    token=request.token,
                    recipe=request.recipe,
                    confirmed_discard=request.confirmedDiscard,
                    filename=request.source.filename
                    if request.source is not None
                    else None,
                    size=request.source.size if request.source is not None else None,
                )
                self.json_reply(200 if result["terminal"] else 202, result)
            elif path == "/api/project/open":
                request = ProjectOpenRequest.model_validate_json(body)
                result = self.app.source_imports.open_project(
                    generation=request.generation,
                    token=request.token,
                    filename=request.filename,
                    size=request.size,
                    confirmed_discard=request.confirmedDiscard,
                )
                self.json_reply(202, result)
            elif path == "/api/project/export":
                request = SourceCursor.model_validate_json(body)
                with tempfile.TemporaryFile(mode="w+b") as output:
                    with self.app.computation() as state:
                        if request.generation != state.generation:
                            raise ScanImportError(
                                "Project changed before saving.", "stale_source"
                            )
                        actions, original, reference = self.app.project_export_inputs(
                            state, request.token
                        )
                        _ = write_project_archive(
                            actions,
                            original,
                            output,
                            legacy_reference=reference,
                            limits=self.app.source_imports.project_limits,
                        )
                        generation = state.generation
                    # The immutable spool owns the complete archive. Release
                    # computation before response bytes can complete at the client.
                    length = output.tell()
                    _ = output.seek(0)
                    self.reply_generation = generation
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(length))
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="project.scansor"',
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Scansor-Generation", str(generation))
                    self.end_headers()
                    while block := output.read(
                        self.app.source_imports.limits.chunk_bytes
                    ):
                        _ = self.wfile.write(block)
            elif path == "/api/project/reset":
                state = self.app.reset_project(
                    ProjectResetRequest.model_validate_json(body)
                )
                self.reply_generation = state.generation
                self.json_reply(
                    200,
                    {
                        "graph": {
                            **state.graph.snapshot(),
                            "project_generation": state.generation,
                        }
                    },
                )
            elif path.endswith("/cancel"):
                operation_id = path.removeprefix("/api/sources/imports/").removesuffix(
                    "/cancel"
                )
                cursor = SourceCursor.model_validate_json(body)
                self.json_reply(
                    200,
                    self.app.source_imports.cancel(
                        operation_id, generation=cursor.generation, token=cursor.token
                    ),
                )
            else:
                self.json_reply(404, {"error": "not found"})
        except ScanImportError as error:
            self.source_error(error)
        except (ValidationError, ValueError, OSError) as error:
            self.source_error(ScanImportError(str(error), "invalid_upload"))
        return True

    def do_POST(self) -> None:
        if not self.allowed():
            return
        if self.source_POST():
            return
        if self.path not in (
            "/api/session",
            "/api/fit",
            "/api/graph",
            "/api/graph/import",
            "/api/graph/evaluate",
            "/api/graph/ensure",
            "/api/graph/cancel",
            "/api/graph/feature-reuse/preview",
            "/api/graph/feature-reuse/apply",
            "/api/graph/selection-volumes/author",
            "/api/graph/selection-volumes/options",
            "/api/graph/selection-volumes/choose",
            "/api/graph/selection-volumes/choices",
            "/api/graph/selection-volumes/preview",
            "/api/graph/selection-volumes/delete",
            "/api/graph/build-faces/preview",
            "/api/graph/build-faces/candidates",
            "/api/graph/build-faces/apply",
            "/api/export/cad",
            "/api/examples/select",
        ):
            self.json_reply(404, {"error": "not found"})
            return
        if (
            self.headers.get("X-Scansor-Request") != "1"
            or self.headers.get("Content-Type") != "application/json"
        ):
            self.json_reply(403, {"error": "expected explicit JSON request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            actions = self.path in {
                "/api/graph",
                "/api/graph/import",
                "/api/graph/feature-reuse/preview",
                "/api/graph/feature-reuse/apply",
                "/api/graph/build-faces/preview",
                "/api/graph/build-faces/candidates",
                "/api/graph/build-faces/apply",
            }
            maximum = MAX_ACTIONS_REQUEST_BYTES if actions else MAX_CONTROL_JSON_BYTES
            if not 0 < length <= maximum:
                raise ValueError(
                    f"expected a request within the {maximum:,}-byte JSON limit"
                )
            self.connection.settimeout(5)
            body = self.rfile.read(length)
            if actions:
                check_actions_envelope(json.loads(body))
            if self.path == "/api/graph/cancel":
                cancellation = EvaluationCancelRequest.model_validate_json(body)
                with self.app.lock:
                    _ = self.fence_source()
                    state = self.app.cancel_evaluation(cancellation)
                self.json_reply(202 if state["evaluation_running"] else 200, state)
                return
            if self.path == "/api/graph/import":
                imported = RecipeImportRequest.model_validate_json(body)
                recipe, warnings = import_recipe(imported.recipe)
                with self.app.lock:
                    _ = self.fence_source()
                    state = self.app.graph.replace(recipe, imported.token)
                self.json_reply(200, {**state, "import_warnings": warnings})
                return
            if self.path == "/api/examples/select":
                try:
                    with self.app.lock:
                        previous = self.fence_source()
                        example_request = ExampleRequest.model_validate_json(body)
                    self.app.select_example(example_request, previous.generation)
                except OSError as error:
                    raise ValueError(f"could not load example: {error}") from error
                self.reply_generation = self.app.accepted().generation
                self.json_reply(200, {"active": self.app.active_example})
                return
            volume_routes = {
                "/api/graph/selection-volumes/author": "authorSelectionVolume",
                "/api/graph/selection-volumes/options": "setSelectionVolumeOptions",
                "/api/graph/selection-volumes/choose": "chooseSelectionVolume",
                "/api/graph/selection-volumes/choices": "getSelectionVolumeChoices",
                "/api/graph/selection-volumes/preview": "getSelectionVolumePreview",
                "/api/graph/selection-volumes/delete": "deleteSelectionVolume",
            }
            if self.path in volume_routes:
                with self.app.lock:
                    _ = self.fence_source()
                    if self.app.graph_job is not None and not self.app.graph_job.done():
                        self.json_reply(
                            409,
                            {
                                "error": "wait for graph evaluation before editing volumes"
                            },
                        )
                        return
                    state = volume_operation(
                        self.app.graph, volume_routes[self.path], json.loads(body)
                    )
                self.json_reply(200, state)
                return
            if self.path in (
                "/api/graph/feature-reuse/preview",
                "/api/graph/feature-reuse/apply",
            ):
                authoring = ReuseAuthoringRequest.model_validate_json(body)
                with self.app.lock:
                    _ = self.fence_source()
                    state = (
                        preview_feature_reuse(self.app.graph, authoring)
                        if self.path.endswith("/preview")
                        else apply_feature_reuse(self.app.graph, authoring)
                    )
                self.json_reply(200, state)
                return
            if self.path in (
                "/api/graph/build-faces/preview",
                "/api/graph/build-faces/candidates",
                "/api/graph/build-faces/apply",
            ):
                with self.app.lock:
                    _ = self.fence_source()
                    if self.app.graph_job is not None and not self.app.graph_job.done():
                        self.json_reply(
                            409,
                            {
                                "error": "wait for graph evaluation before building faces"
                            },
                        )
                        return
                    if self.path.endswith("/candidates"):
                        state = candidate_faces(
                            self.app.graph,
                            FaceCandidatesRequest.model_validate_json(body),
                        )
                    else:
                        state = (
                            preview_faces(
                                self.app.graph,
                                FacesPreviewRequest.model_validate_json(body),
                            )
                            if self.path.endswith("/preview")
                            else apply_faces(
                                self.app.graph,
                                FacesApplyRequest.model_validate_json(body),
                            )
                        )
                self.json_reply(200, state)
                return
            if self.path == "/api/export/cad":
                with self.app.lock:
                    _ = self.fence_source()
                    export_lease = ExitStack()
                    accepted = export_lease.enter_context(self.app.computation())
                try:
                    # Hold the source lease even while lazy kernel loading runs.
                    from experiments.nozzle_cad import (
                        CadExportRequest,
                        export_cad,
                        export_face_targets,
                    )

                    export_request = CadExportRequest.model_validate_json(body)
                    self.reply_generation = accepted.generation
                    graph, workspace = accepted.graph, accepted.workspace
                    snapshot = cast(dict[str, Any], graph.snapshot())
                    if export_request.scope != "target":
                        roots = export_face_targets(snapshot, export_request)
                        roots.extend(export_request.review_owners)
                        if export_request.scope == "all_faces":
                            roots.extend(
                                node["id"]
                                for node in snapshot["recipe"]["nodes"]
                                if node["operation"] == "build_faces"
                            )
                        if export_request.transform is not None:
                            roots.append(export_request.transform)
                        needed, current = graph.readiness_status(
                            export_request.token, roots
                        )
                        if needed or current is None:
                            raise ValueError(
                                "export inputs are not current; evaluate them before exporting geometry"
                            )
                        snapshot = cast(dict[str, Any], current)
                        failed = snapshot["required_failures"]
                        if failed:
                            labels = {
                                node["id"]: node["label"]
                                for node in snapshot["recipe"]["nodes"]
                            }
                            raise ValueError(
                                (
                                    "cannot export Body: "
                                    if export_request.scope == "body"
                                    else "cannot export built faces: "
                                )
                                + "; ".join(
                                    f"{labels.get(key, key)!r}: {snapshot['errors'].get(key, snapshot['states'][key])}"
                                    for key in failed
                                )
                            )
                    exported = export_cad(workspace, snapshot, export_request)
                finally:
                    export_lease.close()
                self.reply(200, exported, "application/zip")
                return
            if self.path.startswith("/api/graph"):
                payload = GraphRequest.model_validate_json(body)
                if self.path == "/api/graph":
                    if payload.recipe is None:
                        raise ValueError("expected a current recipe")
                    with self.app.lock:
                        _ = self.fence_source()
                        state = self.app.graph.replace_authored(
                            payload.recipe, payload.token
                        )
                    self.json_reply(200, state)
                else:
                    with self.app.lock:
                        _ = self.fence_source()
                        if self.path == "/api/graph/ensure":
                            if payload.target is not None or payload.recipe is not None:
                                raise ValueError(
                                    "ensure expects targets, not a target or recipe"
                                )
                            if (
                                self.app.graph_job is not None
                                and self.app.graph_job.done()
                            ):
                                with suppress(StaleGraph):
                                    self.app.graph_job_token = (
                                        self.app.graph.publication_token(
                                            self.app.graph_job_token
                                        )
                                    )
                            needed, ready_state = self.app.graph.readiness_status(
                                payload.token, payload.targets, payload.all_actions
                            )
                            roots = self.app.graph.evaluation_roots(
                                payload.token, payload.targets, payload.all_actions
                            )
                            self.app.graph_job_errors = {
                                key: error
                                for key, error in self.app.graph_job_errors.items()
                                if key[0] == payload.token
                            }
                            if (
                                self.app.graph_job is not None
                                and self.app.graph_job.done()
                                and self.app.graph_job_token == payload.token
                            ):
                                error = self.app.graph_job_error()
                                # Superseded work is cancellation, not a retained
                                # failure of the current requested outputs.
                                if error is not None:
                                    self.app.graph_job_errors[
                                        (payload.token, self.app.graph_job_roots)
                                    ] = str(error)
                            job_error = self.app.graph_job_errors.get(
                                (payload.token, roots)
                            )
                            if (
                                not needed
                                and ready_state is not None
                                and not ready_state["required_failures"]
                            ):
                                # Another request may have repaired these outputs.
                                _ = self.app.graph_job_errors.pop(
                                    (payload.token, roots), None
                                )
                                job_error = None
                            if (
                                self.app.graph_job is not None
                                and not self.app.graph_job.done()
                            ):
                                self.json_reply(
                                    202,
                                    self.app.evaluation_snapshot({"status": "running"}),
                                )
                            elif not needed or job_error is not None:
                                self.json_reply(
                                    200,
                                    self.app.evaluation_snapshot(
                                        {
                                            **(
                                                ready_state
                                                if ready_state is not None
                                                else self.app.graph.readiness_snapshot(
                                                    payload.token,
                                                    payload.targets,
                                                    payload.all_actions,
                                                )
                                            ),
                                            "evaluation_running": False,
                                            **(
                                                {"evaluation_error": str(job_error)}
                                                if job_error is not None
                                                else {}
                                            ),
                                        }
                                    ),
                                )
                            else:
                                self.json_reply(
                                    202,
                                    self.app.start_evaluation(payload, "ensure", roots),
                                )
                            return
                        if (
                            self.app.graph_job is not None
                            and not self.app.graph_job.done()
                        ):
                            self.json_reply(
                                409, {"error": "a graph evaluation is already running"}
                            )
                            return
                        if payload.token != self.app.graph.snapshot()["token"]:
                            raise StaleGraph("graph changed before evaluation")
                        roots = self.app.graph.evaluation_roots(
                            payload.token,
                            [payload.target] if payload.target is not None else None,
                            payload.all_actions,
                        )
                        state = self.app.start_evaluation(payload, "evaluate", roots)
                    self.json_reply(202, state)
                return
            with self.app.lock:
                accepted = self.fence_source()
                if not isinstance(accepted.workspace, NozzleWorkspace):
                    raise ScanImportError(
                        "Legacy session fitting is unavailable for user scan sources.",
                        "unsupported_operation",
                    )
                session = accepted.workspace.validate(
                    NozzleSession.model_validate_json(body)
                )
                if self.path == "/api/session":
                    self.json_reply(200, session.model_dump())
                    return
                if self.app.job is not None and not self.app.job.done():
                    self.json_reply(409, {"error": "a fit is already running"})
                    return
                self.app.job_id = uuid.uuid4().hex
                self.app.job = self.app.submit(accepted.workspace.fit, session)
                job_id = self.app.job_id
            self.json_reply(202, {"job_id": job_id})
        except ScanImportError as error:
            self.source_error(error)
        except StaleGraph as error:
            self.json_reply(409, {"error": str(error)})
        except ValidationError as error:
            messages = [
                str(item["msg"]).removeprefix("Value error, ")
                for item in error.errors()
            ]
            self.json_reply(422, {"error": "; ".join(dict.fromkeys(messages))})
        except (ValueError, TimeoutError) as error:
            self.json_reply(422, {"error": str(error)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--example", type=Path, default=Path("examples/nozzle-bayonette-simplified")
    )
    _ = parser.add_argument(
        "--recipe",
        type=Path,
        help="Override the example's default action recipe",
    )
    _ = parser.add_argument("--port", type=int, default=0)
    _ = parser.add_argument(
        "--empty", action="store_true", help="Start with no source or actions"
    )
    args = parser.parse_args()
    if not (ASSETS / "node_modules/three/build/three.module.js").is_file():
        parser.error(
            "run npm ci --ignore-scripts --prefix experiments/browser_viewer first"
        )
    if not all(
        (ASSETS / filename).is_file()
        for filename in ("dist/workspace.js", "dist/workspace.css")
    ):
        parser.error("run npm run build --prefix experiments/browser_viewer first")
    if args.empty and args.recipe is not None:
        parser.error("--empty cannot include --recipe")
    workspace = None if args.empty else NozzleWorkspace(args.example)
    recipe = (
        load_recipe(args.recipe)
        if args.recipe is not None
        else default_example_recipe(workspace, args.example)
        if workspace is not None
        else None
    )
    with NozzleServer(
        workspace, args.port, recipe, example_path=None if args.empty else args.example
    ) as server:
        print(
            f"Nozzle selection experiment: http://127.0.0.1:{server.server_port}/",
            flush=True,
        )
        with suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
