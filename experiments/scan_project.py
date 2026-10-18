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
from experiments.scan_workspace import (
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    check_cancelled,
    prepare_scan,
)


@dataclass(frozen=True)
class AcceptedScan:
    generation: int
    workspace: ScanWorkspace
    graph: FeatureGraph[ScanWorkspace]
    positions: NDArray[np.float32]
    indices: NDArray[np.uint32]
    import_warnings: tuple[ImportWarning, ...] = ()


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
        stream: BinaryIO,
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
        stream: BinaryIO,
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
        stream: BinaryIO | None = None,
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
        elif (
            inspect_actions(
                actions.recipe.model_dump(mode="json"), reusable.workspace
            ).disposition
            == "matching"
        ):
            if stream is not None:
                raise ScanImportError(
                    "Matching actions reuse the accepted source; no upload is expected.",
                    "unexpected_source",
                )
            workspace = reusable.workspace
            positions, indices = reusable.positions, reusable.indices
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
            generation + 1, workspace, graph, positions, indices, actions.warnings
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
