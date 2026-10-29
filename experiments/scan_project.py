"""Staged zero/one-source graph transactions shared by transport adapters."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import RLock
from typing import BinaryIO, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from experiments.feature_graph import (
    FeatureGraph,
    GraphFailureCheckpoint,
    Recipe,
    Source,
)
from experiments.feature_recipe_import import ImportWarning
from experiments.scan_actions import inspect_actions, preflight_actions
from experiments.scan_replacement import replacement_recipe
from experiments.scan_sources import ReplayableInput
from experiments.scan_workspace import (
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    SourceDescriptor,
    check_cancelled,
    prepare_scan,
)
from experiments.virtual_scan_reduction import (
    ReductionSettings,
    VirtualScanDerivation,
)
from experiments.virtual_scan_reduction import (
    prepare_virtual_scan as reduce_scan,
)


@dataclass(frozen=True)
class AcceptedScan:
    generation: int
    workspace: ScanWorkspace
    graph: FeatureGraph[ScanWorkspace]
    positions: NDArray[np.float32]
    indices: NDArray[np.uint32]
    import_warnings: tuple[ImportWarning, ...] = ()
    retained_sources: tuple[ScanWorkspace, ...] = ()


@dataclass(frozen=True)
class PreparedScan:
    owner: str
    generation: int
    token: str
    prepared_token: str
    accepted: AcceptedScan


@dataclass(frozen=True)
class PreparedProject:
    """Whole-project reopen intent, distinct from adding a Source alone."""

    owner: str
    generation: int
    token: str
    prepared_token: str
    prepared_revision: int
    accepted: AcceptedScan


@dataclass(frozen=True)
class PreparedReplacement:
    """An aligned replacement retains definitions and renews source observations."""

    owner: str
    generation: int
    token: str
    prepared_token: str
    prepared_revision: int
    accepted: AcceptedScan


def _accepted(workspace: ScanWorkspace, generation: int) -> AcceptedScan:
    descriptor = workspace.descriptor
    recipe = (
        Recipe(schema_version=2, nodes=[], output=None)
        if descriptor is None
        else Recipe(
            schema_version=2,
            nodes=[
                Source(
                    id="scan",
                    label=workspace.name[:120],
                    operation="source",
                    source_sha256=descriptor.source_sha256,
                    reference_sha256=workspace.reference_sha256,
                    descriptor=descriptor,
                )
            ],
            output="scan",
        )
    )
    positions, indices = workspace.display_buffers()
    return AcceptedScan(
        generation, workspace, FeatureGraph(workspace, recipe), positions, indices
    )


def _source(accepted: AcceptedScan) -> Source | None:
    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    return next((node for node in recipe.nodes if isinstance(node, Source)), None)


def _check_retained_limits(pair: tuple[ScanWorkspace, ...], limits: ScanLimits) -> None:
    if (
        sum(
            source.source.byte_length if source.source is not None else 0
            for source in pair
        )
        > limits.max_source_bytes
        or sum(len(source.local) for source in pair) > limits.max_vertices
        or sum(len(source.data.triangles) for source in pair) > limits.max_faces
        or sum(source.diagnostics.estimated_bytes for source in pair)
        > limits.max_estimated_bytes
    ):
        raise ScanImportError(
            "Retained scans exceed the aggregate source limits.", "source_limit"
        )


def _matches(workspace: ScanWorkspace, descriptor: SourceDescriptor) -> bool:
    return workspace.descriptor is not None and (
        workspace.descriptor.semantic_record() == descriptor.semantic_record()
        and workspace.source is not None
        and workspace.source.descriptor.semantic_record()
        == descriptor.semantic_record()
        and workspace.source.input.byte_length == descriptor.source_bytes
    )


def _retained_pair(
    accepted: AcceptedScan, lineage: VirtualScanDerivation, limits: ScanLimits
) -> tuple[ScanWorkspace, ScanWorkspace]:
    pair = accepted.retained_sources
    if (
        len(pair) != 2
        or not _matches(pair[0], lineage.original)
        or not _matches(pair[1], lineage.derived)
    ):
        raise ScanImportError(
            "Virtual scan switching requires its exact retained source pair.",
            "source_required",
        )
    if not any(accepted.workspace is source for source in pair):
        raise ScanImportError(
            "The active workspace is outside its retained source pair.",
            "invalid_source_binding",
        )
    _check_retained_limits(pair, limits)
    return pair[0], pair[1]


def _same_derivation(
    first: VirtualScanDerivation, second: VirtualScanDerivation
) -> bool:
    return (
        first.original.semantic_record() == second.original.semantic_record()
        and first.derived.semantic_record() == second.derived.semantic_record()
        and first.settings == second.settings
    )


def reconstruct_virtual_scan(
    original: ScanWorkspace,
    derivation: VirtualScanDerivation,
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[ScanWorkspace, ScanWorkspace]:
    """Recover both exact endpoints from original bytes and a saved derivation."""
    check_cancelled(cancelled)
    lineage = VirtualScanDerivation.model_validate(derivation.model_dump(mode="json"))
    if not _matches(original, lineage.original):
        raise ScanImportError(
            "The original does not match the virtual scan derivation.",
            "source_mismatch",
        )
    reduced = reduce_scan(
        original, lineage.settings, limits, progress=progress, cancelled=cancelled
    )
    if (
        reduced.derivation.derived.semantic_record()
        != lineage.derived.semantic_record()
    ):
        raise ScanImportError(
            "Virtual scan regeneration does not match its saved identity.",
            "source_mismatch",
        )
    pair = (original, reduced.workspace)
    _check_retained_limits(pair, limits)
    check_cancelled(cancelled)
    return pair


def virtual_scan_metadata(accepted: AcceptedScan | None) -> dict[str, object]:
    """Describe one active source and its optional retained virtual derivative."""
    source = _source(accepted) if accepted is not None else None
    descriptor = source.descriptor if source is not None else None
    lineage = source.virtual_scan if source is not None else None

    def record(value: SourceDescriptor | None) -> dict[str, object] | None:
        return (
            None
            if value is None
            else {
                "binding": value.binding_sha256,
                "vertices": value.vertices,
                "triangles": value.triangles,
                "descriptor": value.model_dump(mode="json"),
            }
        )

    return {
        "supported": descriptor is not None,
        "version": 1,
        "active_binding": descriptor.binding_sha256 if descriptor else None,
        "original": record(lineage.original if lineage else descriptor),
        "virtual": record(lineage.derived if lineage else None),
        "settings": lineage.settings.model_dump(mode="json") if lineage else None,
        "can_create": descriptor is not None and lineage is None,
        "can_activate": lineage is not None
        and accepted is not None
        and len(accepted.retained_sources) == 2,
        "source_warnings": [
            warning
            for workspace in (
                (accepted.retained_sources or (accepted.workspace,))
                if accepted is not None
                else ()
            )
            for warning in workspace.normal_warnings()
        ],
    }


class ScanProject:
    def __init__(self) -> None:
        self._lock: RLock = RLock()
        self._owner: str = uuid4().hex
        self._accepted: AcceptedScan = _accepted(ScanWorkspace.empty(), 0)
        self._busy: bool = False

    @property
    def accepted(self) -> AcceptedScan:
        with self._lock:
            return self._accepted

    def _check(self, generation: int, token: str) -> None:
        if (
            generation != self._accepted.generation
            or token != self._accepted.graph.snapshot()["token"]
        ):
            raise ScanImportError(
                "Project changed before source commit.", "stale_source"
            )
        if self._busy:
            raise ScanImportError(
                "Wait for evaluation or explicit cancellation before changing sources.",
                "source_busy",
            )

    def check_add(self, *, generation: int, token: str) -> None:
        """Check an adapter reservation without reading or allocating a source."""
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            recipe = cast(dict[str, object], self._accepted.graph.snapshot()["recipe"])
            if (
                self._accepted.workspace.descriptor is not None
                or recipe["nodes"]
                or recipe["groups"]
            ):
                raise ScanImportError(
                    "Add scan requires an empty project; start a new project explicitly.",
                    "source_exists",
                )

    def prepare_add(
        self,
        stream: ReplayableInput | BinaryIO,
        filename: str,
        limits: ScanLimits,
        *,
        generation: int,
        token: str,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreparedScan:
        self.check_add(generation=generation, token=token)
        workspace = prepare_scan(
            stream, filename, limits, progress=progress, cancelled=cancelled
        )
        check_cancelled(cancelled)
        candidate = _accepted(workspace, generation + 1)
        return PreparedScan(
            self._owner,
            generation,
            token,
            str(candidate.graph.snapshot()["token"]),
            candidate,
        )

    def commit(
        self, candidate: PreparedScan, *, cancelled: Callable[[], bool] | None = None
    ) -> AcceptedScan:
        if not isinstance(cast(object, candidate), PreparedScan):
            raise ScanImportError(
                "Expected an Add Scan candidate.", "invalid_candidate"
            )
        with self._lock, self._accepted.graph.lock:
            check_cancelled(cancelled)
            self._check(candidate.generation, candidate.token)
            if candidate.owner != self._owner:
                raise ScanImportError(
                    "Candidate belongs to another project.",
                    "invalid_candidate",
                )
            if self._accepted.workspace.descriptor is not None:
                raise ScanImportError(
                    "Only one scan is supported in a project.", "source_exists"
                )
            with candidate.accepted.graph.lock:
                check_cancelled(cancelled)
                if (
                    candidate.prepared_token
                    != candidate.accepted.graph.snapshot()["token"]
                ):
                    raise ScanImportError(
                        "Prepared candidate was modified.", "invalid_candidate"
                    )
                self._accepted = candidate.accepted
                return self._accepted

    def check_replace(
        self, *, generation: int, token: str, matching: AcceptedScan | None = None
    ) -> None:
        """Validate a replacement reservation before allocating source bytes."""
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            previous = matching or self._accepted
            if previous.workspace.descriptor is None:
                raise ScanImportError(
                    "Replace scan requires an imported active scan.",
                    "source_required",
                )

    def prepare_replace(
        self,
        stream: ReplayableInput | BinaryIO,
        filename: str,
        limits: ScanLimits,
        *,
        generation: int,
        token: str,
        matching: AcceptedScan | None = None,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreparedReplacement:
        """Prepare replacement bytes and retained definitions without publication.

        An adapter can use a private project with an immutable accepted source
        snapshot; it must also fence its external cursor at publication time.
        """
        with self._lock, self._accepted.graph.lock:
            self.check_replace(generation=generation, token=token, matching=matching)
            previous = matching or self._accepted
            with previous.graph.lock:
                recipe = Recipe.model_validate(previous.graph.snapshot()["recipe"])
        check_cancelled(cancelled)
        workspace = prepare_scan(
            stream, filename, limits, progress=progress, cancelled=cancelled
        )
        check_cancelled(cancelled)
        retained = replacement_recipe(
            recipe, previous.workspace, workspace, cancelled=cancelled
        )
        check_cancelled(cancelled)
        graph = FeatureGraph(workspace, retained)
        positions, indices = workspace.display_buffers()
        state = graph.snapshot()
        accepted = AcceptedScan(
            generation + 1,
            workspace,
            graph,
            positions,
            indices,
            previous.import_warnings,
        )
        check_cancelled(cancelled)
        return PreparedReplacement(
            self._owner,
            generation,
            token,
            str(state["token"]),
            cast(int, state["revision"]),
            accepted,
        )

    def _prepare_virtual_transition(
        self,
        previous: AcceptedScan,
        recipe: Recipe,
        workspace: ScanWorkspace,
        pair: tuple[ScanWorkspace, ScanWorkspace],
        lineage: VirtualScanDerivation,
        limits: ScanLimits,
        *,
        generation: int,
        token: str,
        cancelled: Callable[[], bool] | None,
    ) -> PreparedReplacement:
        _check_retained_limits(pair, limits)
        check_cancelled(cancelled)
        retained = replacement_recipe(
            recipe,
            previous.workspace,
            workspace,
            virtual_scan=lineage,
            cancelled=cancelled,
        )
        graph = FeatureGraph(workspace, retained)
        positions, indices = workspace.display_buffers()
        state = graph.snapshot()
        accepted = AcceptedScan(
            generation + 1,
            workspace,
            graph,
            positions,
            indices,
            previous.import_warnings,
            pair,
        )
        check_cancelled(cancelled)
        return PreparedReplacement(
            self._owner,
            generation,
            token,
            str(state["token"]),
            cast(int, state["revision"]),
            accepted,
        )

    def prepare_virtual_scan(
        self,
        settings: ReductionSettings,
        limits: ScanLimits,
        *,
        generation: int,
        token: str,
        matching: AcceptedScan | None = None,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreparedReplacement:
        """Create and activate one detached derivative while retaining its original."""
        with self._lock, self._accepted.graph.lock:
            self.check_replace(generation=generation, token=token, matching=matching)
            previous = matching or self._accepted
            with previous.graph.lock:
                recipe = Recipe.model_validate(previous.graph.snapshot()["recipe"])
            source = next(node for node in recipe.nodes if isinstance(node, Source))
            if source.virtual_scan is not None or previous.retained_sources:
                raise ScanImportError(
                    "This scan already has a virtual derivative.", "virtual_scan_exists"
                )
        reduced = reduce_scan(
            previous.workspace, settings, limits, progress=progress, cancelled=cancelled
        )
        return self._prepare_virtual_transition(
            previous,
            recipe,
            reduced.workspace,
            (previous.workspace, reduced.workspace),
            reduced.derivation,
            limits,
            generation=generation,
            token=token,
            cancelled=cancelled,
        )

    def prepare_activate_scan(
        self,
        binding: str,
        limits: ScanLimits,
        *,
        generation: int,
        token: str,
        matching: AcceptedScan | None = None,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PreparedReplacement:
        """Switch observations using current shared definitions, never an old graph."""
        with self._lock, self._accepted.graph.lock:
            self.check_replace(generation=generation, token=token, matching=matching)
            previous = matching or self._accepted
            with previous.graph.lock:
                recipe = Recipe.model_validate(previous.graph.snapshot()["recipe"])
            source = next(node for node in recipe.nodes if isinstance(node, Source))
            if source.virtual_scan is None:
                raise ScanImportError(
                    "Create a virtual scan before switching.", "source_required"
                )
            pair = _retained_pair(previous, source.virtual_scan, limits)
            workspace = next(
                (item for item in pair if item.reference_sha256 == binding), None
            )
            if workspace is None:
                raise ScanImportError(
                    "The requested scan is outside the retained pair.",
                    "source_mismatch",
                )
            lineage = source.virtual_scan
        check_cancelled(cancelled)
        for retained in pair:
            assert retained.source is not None
            retained.source.verify(cancelled=cancelled)
        if progress is not None:
            progress(ScanProgress("activate-scan", 0, 1))
        return self._prepare_virtual_transition(
            previous,
            recipe,
            workspace,
            pair,
            lineage,
            limits,
            generation=generation,
            token=token,
            cancelled=cancelled,
        )

    def commit_replace(
        self,
        candidate: PreparedReplacement,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> AcceptedScan:
        """Publish only an intact candidate for this exact accepted cursor."""
        if not isinstance(cast(object, candidate), PreparedReplacement):
            raise ScanImportError(
                "Expected a Replace Scan candidate.", "invalid_candidate"
            )
        with self._lock, self._accepted.graph.lock:
            check_cancelled(cancelled)
            self._check(candidate.generation, candidate.token)
            if (
                candidate.owner != self._owner
                or candidate.accepted.generation != candidate.generation + 1
            ):
                raise ScanImportError(
                    "Candidate belongs to another project.", "invalid_candidate"
                )
            with candidate.accepted.graph.lock:
                check_cancelled(cancelled)
                state = candidate.accepted.graph.snapshot()
                if (
                    candidate.prepared_token != state["token"]
                    or candidate.prepared_revision != state["revision"]
                ):
                    raise ScanImportError(
                        "Prepared replacement was modified.", "invalid_candidate"
                    )
                self._accepted = candidate.accepted
                return self._accepted

    def prepare_open(
        self,
        value: object,
        *,
        generation: int,
        token: str,
        confirmed_discard: bool,
        matching: AcceptedScan | None = None,
        stream: ReplayableInput | BinaryIO | None = None,
        filename: str | None = None,
        limits: ScanLimits | None = None,
        progress: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        check_recipe: Callable[[Recipe], None] | None = None,
    ) -> PreparedProject:
        """Prepare saved definitions and their exact source without publication.

        Adapters may use a private owner and supply an immutable accepted source
        snapshot for reuse. Their external cursor/owner must still be fenced at
        the final adapter publication boundary.
        """
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            if confirmed_discard is not True:
                raise ScanImportError(
                    "Opening actions requires discard approval.", "discard_required"
                )
            previous = self._accepted
        check_cancelled(cancelled)
        actions = preflight_actions(value)
        if check_recipe is not None:
            check_recipe(actions.recipe)
        reusable = matching or previous
        retained_sources: tuple[ScanWorkspace, ...] = ()
        if actions.kind == "empty":
            if stream is not None:
                raise ScanImportError(
                    "Empty actions do not accept a source upload.", "unexpected_source"
                )
            workspace = ScanWorkspace.empty()
            positions, indices = workspace.display_buffers()
        elif actions.kind == "legacy":
            raise ScanImportError(
                "Legacy actions require an explicitly compatible legacy workspace.",
                "incompatible_legacy",
            )
        elif actions.source is not None and actions.source.virtual_scan is not None:
            lineage = actions.source.virtual_scan
            if limits is None:
                raise ScanImportError(
                    "Virtual scan reopen requires declared source limits.",
                    "source_required",
                )
            existing_source = _source(reusable)
            if (
                stream is None
                and existing_source is not None
                and existing_source.virtual_scan is not None
                and len(reusable.retained_sources) == 2
                and _same_derivation(existing_source.virtual_scan, lineage)
            ):
                pair = _retained_pair(reusable, lineage, limits)
                for retained in pair:
                    assert retained.source is not None
                    retained.source.verify(cancelled=cancelled)
            else:
                if stream is not None:
                    if filename is None:
                        raise ScanImportError(
                            "The original source upload requires a filename.",
                            "source_required",
                        )
                    original = prepare_scan(
                        stream, filename, limits, progress=progress, cancelled=cancelled
                    )
                else:
                    original = next(
                        (
                            item
                            for item in (reusable.workspace, *reusable.retained_sources)
                            if _matches(item, lineage.original)
                        ),
                        None,
                    )
                    if original is None:
                        raise ScanImportError(
                            "Locate the exact original scan to reconstruct this virtual scan.",
                            "source_required",
                        )
                pair = reconstruct_virtual_scan(
                    original, lineage, limits, progress=progress, cancelled=cancelled
                )
            workspace = next(
                item
                for item in pair
                if item.reference_sha256 == actions.source.reference_sha256
            )
            retained_sources = pair
            positions, indices = workspace.display_buffers()
        elif (
            inspect_actions(
                actions.recipe.model_dump(mode="json"),
                reusable.workspace,
                retained_sources=reusable.retained_sources,
            ).disposition
            == "matching"
        ):
            if stream is not None:
                raise ScanImportError(
                    "Matching actions reuse the accepted source; no upload is expected.",
                    "unexpected_source",
                )
            workspace = next(
                item
                for item in (reusable.workspace, *reusable.retained_sources)
                if inspect_actions(
                    actions.recipe.model_dump(mode="json"), item
                ).disposition
                == "matching"
            )
            if workspace is reusable.workspace:
                positions, indices = reusable.positions, reusable.indices
            else:
                positions, indices = workspace.display_buffers()
            if workspace.source is not None:
                workspace.source.verify(cancelled=cancelled)
        else:
            if stream is None or limits is None or filename is None:
                raise ScanImportError(
                    "Locate the exact scan referenced by these actions.",
                    "source_required",
                )
            workspace = prepare_scan(
                stream, filename, limits, progress=progress, cancelled=cancelled
            )
            if (
                inspect_actions(
                    actions.recipe.model_dump(mode="json"), workspace
                ).disposition
                != "matching"
            ):
                raise ScanImportError(
                    "The located scan does not match the saved actions.",
                    "source_mismatch",
                )
            positions, indices = workspace.display_buffers()
        check_cancelled(cancelled)
        graph = FeatureGraph(workspace, actions.recipe)
        state = graph.snapshot()
        accepted = AcceptedScan(
            generation + 1,
            workspace,
            graph,
            positions,
            indices,
            actions.warnings,
            retained_sources,
        )
        check_cancelled(cancelled)
        return PreparedProject(
            self._owner,
            generation,
            token,
            str(state["token"]),
            cast(int, state["revision"]),
            accepted,
        )

    def commit_open(
        self, candidate: PreparedProject, *, cancelled: Callable[[], bool] | None = None
    ) -> AcceptedScan:
        """Publish one validated whole project with a fresh source epoch."""
        if not isinstance(cast(object, candidate), PreparedProject):
            raise ScanImportError(
                "Expected an Open Actions candidate.", "invalid_candidate"
            )
        with self._lock, self._accepted.graph.lock:
            check_cancelled(cancelled)
            self._check(candidate.generation, candidate.token)
            if (
                candidate.owner != self._owner
                or candidate.accepted.generation != candidate.generation + 1
            ):
                raise ScanImportError(
                    "Candidate belongs to another project.", "invalid_candidate"
                )
            with candidate.accepted.graph.lock:
                check_cancelled(cancelled)
                state = candidate.accepted.graph.snapshot()
                if (
                    candidate.prepared_token != state["token"]
                    or candidate.prepared_revision != state["revision"]
                ):
                    raise ScanImportError(
                        "Prepared project was modified.", "invalid_candidate"
                    )
                self._accepted = candidate.accepted
                return self._accepted

    def reset(self, *, generation: int, token: str) -> AcceptedScan:
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            self._accepted = _accepted(ScanWorkspace.empty(), generation + 1)
            return self._accepted

    def recover_graph(
        self,
        *,
        generation: int,
        token: str,
        recipe: Recipe,
        checkpoint: GraphFailureCheckpoint,
    ) -> AcceptedScan:
        """Discard computation through the owner, retaining immutable source arrays.

        Called after computation stops. Construct and validate the replacement
        before changing the authoritative accepted graph pointer.
        """
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            graph = FeatureGraph(self._accepted.workspace, checkpoint.recipe)
            graph.restore_failures(checkpoint)
            _ = graph.replace(recipe, checkpoint.token)
            self._accepted = replace(self._accepted, graph=graph)
            return self._accepted

    @contextmanager
    def evaluation(self) -> Generator[AcceptedScan]:
        """Adapters hold this lease until active computation reaches a safe end."""
        with self._lock:
            if self._busy:
                raise ScanImportError(
                    "Project evaluation is already active.", "source_busy"
                )
            self._busy = True
            accepted = self._accepted
        try:
            yield accepted
        finally:
            with self._lock:
                self._busy = False
