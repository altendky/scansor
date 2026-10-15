"""Staged zero/one-source graph transactions shared by transport adapters."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import BinaryIO, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from experiments.feature_graph import FeatureGraph, Recipe, Source
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


@dataclass(frozen=True)
class PreparedScan:
    owner: str
    generation: int
    token: str
    prepared_token: str
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

    def reset(self, *, generation: int, token: str) -> AcceptedScan:
        with self._lock, self._accepted.graph.lock:
            self._check(generation, token)
            self._accepted = _accepted(ScanWorkspace.empty(), generation + 1)
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
