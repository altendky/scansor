"""Resolve source-bound Selection entries without treating ownership as a DAG."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from experiments.scan_workspace import Workspace, check_cancelled
from experiments.selection_entries import (
    SelectionEntry,
    SelectionVolume,
    SourceBinding,
    active_entry,
    source_binding,
)
from experiments.selection_volume import (
    apply_selection_volume,
    selection_volume_coverage,
)
from experiments.volume_archive import VolumeArchive, decode_selection_volume

if TYPE_CHECKING:
    from experiments.feature_graph import Recipe, Selection


class VolumeCache:
    """One expanded definition and at most 128 previously verified records."""

    def __init__(self) -> None:
        self._decoded: tuple[str, dict[str, Any]] | None = None
        self._verified: OrderedDict[str, None] = OrderedDict()

    def get(
        self, archive: VolumeArchive, *, cancelled: Callable[[], bool] | None = None
    ) -> dict[str, Any]:
        check_cancelled(cancelled)
        key = hashlib.sha256(archive.model_dump_json().encode()).hexdigest()
        if self._decoded is not None and self._decoded[0] == key:
            return self._decoded[1]
        geometry = decode_selection_volume(archive, cancelled=cancelled)
        self._decoded = key, geometry
        self._verified[key] = None
        self._verified.move_to_end(key)
        while len(self._verified) > 128:
            _ = self._verified.popitem(last=False)
        return geometry

    def verify(self, archive: VolumeArchive) -> None:
        # Identity includes compressed bytes/declared length too. A different
        # encoding with the same claimed content hash still needs verification.
        key = hashlib.sha256(archive.model_dump_json().encode()).hexdigest()
        if key not in self._verified:
            _ = self.get(archive)
            self._verified[key] = None
            while len(self._verified) > 128:
                _ = self._verified.popitem(last=False)


def current_entry(selection: Selection, workspace: Workspace) -> SelectionEntry | None:
    return (
        active_entry(selection.entries, source_binding(workspace).key)
        if selection.entries is not None
        else None
    )


def volume_frame(
    geometry: dict[str, Any],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    frame = geometry.get("provenance", {}).get("source_frame")
    if not isinstance(frame, dict) or set(frame) != {"origin", "basis"}:
        raise ValueError("accepted volume requires its authoring coordinate frame")
    raw_origin: NDArray[Any] = np.asarray(frame["origin"])
    raw_basis: NDArray[Any] = np.asarray(frame["basis"])
    if raw_origin.dtype.kind not in "iuf" or raw_basis.dtype.kind not in "iuf":
        raise ValueError("volume authoring frame must contain real coordinates")
    origin = np.asarray(raw_origin, dtype=np.float64)
    basis = np.asarray(raw_basis, dtype=np.float64)
    if (
        origin.shape != (3,)
        or basis.shape != (3, 3)
        or not np.isfinite(origin).all()
        or not np.isfinite(basis).all()
        or not np.allclose(basis.T @ basis, np.eye(3), rtol=0, atol=1e-10)
        or not np.isclose(np.linalg.det(basis), 1, rtol=0, atol=1e-10)
    ):
        raise ValueError(
            "volume authoring frame must be finite and right-handed orthonormal"
        )
    return origin, basis


def volume_geometry(
    cache: VolumeCache,
    volume: SelectionVolume,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    geometry = cache.get(volume.archive, cancelled=cancelled)
    _ = volume_frame(geometry)
    return geometry


def entry_diagnostic(
    recipe: Recipe, selection: Selection, workspace: Workspace
) -> dict[str, Any] | None:
    """Cheap setup check: no volume expansion or numerical evaluation."""
    if selection.entries is None:
        return None
    return entry_diagnostic_for_binding(recipe, selection, source_binding(workspace))


def entry_diagnostic_for_binding(
    recipe: Recipe, selection: Selection, binding: SourceBinding
) -> dict[str, Any] | None:
    """Use captured scan identity without accessing a live workspace's buffers."""
    if selection.entries is None:
        return None
    entry = active_entry(selection.entries, binding.key)
    base: dict[str, Any] = {
        "code": "needs_input",
        "selection": selection.id,
        "entry": entry.id if entry is not None else None,
    }
    if entry is None or (entry.mode == "generated" and entry.source_volume is None):
        return {
            **base,
            "action": "choose_source_volume",
            "message": "Choose source volume for this scan",
        }
    if entry.mode == "painted":
        return None
    from experiments.selection_volume_authoring import (
        authoring_fingerprint,
        resolve_volume,
    )

    assert entry.source_volume is not None
    owner, original, volume = resolve_volume(recipe, entry.source_volume)
    fingerprint = authoring_fingerprint(recipe, owner, original, volume)
    if fingerprint != volume.authoring_fingerprint:
        loaded = original.scan.key == binding.key
        return {
            **base,
            "action": "refresh_source_volume",
            "volume": entry.source_volume.model_dump(),
            "refresh_fingerprint": fingerprint,
            "source_loaded": loaded,
            "message": "Evaluate the authoring inputs to refresh this volume"
            if loaded
            else "Load the authoring scan to refresh this volume",
        }
    return None


def resolve_membership(
    recipe: Recipe,
    selection: Selection,
    workspace: Workspace,
    cache: VolumeCache,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[int] | None, dict[str, Any] | None]:
    check_cancelled(cancelled)
    if selection.entries is None:
        return selection.ids.copy(), None
    if entry_diagnostic(recipe, selection, workspace) is not None:
        return None, None
    entry = current_entry(selection, workspace)
    assert entry is not None
    if entry.mode == "painted":
        assert entry.ids is not None
        return entry.ids.copy(), None
    from experiments.selection_volume_authoring import resolve_volume

    assert entry.source_volume is not None
    _, _, volume = resolve_volume(recipe, entry.source_volume)
    geometry = volume_geometry(cache, volume, cancelled=cancelled)
    origin, basis = volume_frame(geometry)
    points = (workspace.data.xyz - origin) @ basis
    normals = workspace.data.normals @ basis
    ids = apply_selection_volume(
        points, normals, workspace.data.weights, geometry, cancelled=cancelled
    )
    coverage = selection_volume_coverage(
        points, normals, workspace.data.triangles, geometry, cancelled=cancelled
    )
    return ids, coverage


def validate_records(recipe: Recipe, workspace: Workspace, cache: VolumeCache) -> None:
    from experiments.feature_graph import (
        Selection,
        SurfaceReference,
        surface_reference_kind,
    )
    from experiments.selection_volume_authoring import (
        authoring_fingerprint,
        authoring_selection_ids,
        resolve_volume,
    )

    nodes = {node.id: node for node in recipe.nodes}
    binding = source_binding(workspace)
    volumes: dict[
        tuple[str, str, str], tuple[Selection, SelectionEntry, SelectionVolume]
    ] = {}
    for selection in recipe.nodes:
        if not isinstance(selection, Selection) or selection.entries is None:
            continue
        for entry in selection.entries:
            for volume in entry.volumes:
                key = selection.id, entry.id, volume.id
                volumes[key] = selection, entry, volume
                cache.verify(volume.archive)
                geometry = volume_geometry(cache, volume)
                provenance = geometry["provenance"]
                if any(
                    name in provenance and provenance[name] != expected
                    for name, expected in (
                        ("selection", selection.id),
                        ("entry", entry.id),
                        ("volume", volume.id),
                    )
                ):
                    raise ValueError(
                        "accepted volume does not match its ownership provenance"
                    )
                if geometry["provenance"].get("source_binding") != entry.scan.key:
                    raise ValueError(
                        "accepted volume does not match its authoring scan"
                    )
                origin, basis = volume_frame(geometry)
                if entry.scan.key == binding.key and (
                    not np.array_equal(origin, workspace.origin)
                    or not np.array_equal(basis, workspace.frame)
                ):
                    raise ValueError(
                        "accepted volume does not match the loaded authoring frame"
                    )
                if (
                    volume.fit is not None
                    and surface_reference_kind(
                        SurfaceReference.model_validate(volume.fit.model_dump()), nodes
                    )
                    != volume.kind
                ):
                    raise ValueError(
                        "volume construction does not match its authoring fit"
                    )
                if authoring_fingerprint(
                    recipe, selection, entry, volume
                ) == volume.authoring_fingerprint and (
                    geometry["kind"] != volume.kind
                    or geometry["options"] != volume.options.model_dump(mode="json")
                    or (
                        "surface" in provenance
                        and provenance["surface"]
                        != (volume.fit.model_dump(mode="json") if volume.fit else None)
                    )
                ):
                    raise ValueError(
                        "fresh volume archive contradicts its authoring intent"
                    )
                inputs = authoring_selection_ids(recipe, selection, entry, volume)
                if selection.id not in inputs:
                    raise ValueError("the authoring fit must use its owning Selection")
                if selection.id in volume.captured_inputs:
                    raise ValueError("owning membership belongs in captured_ids")
                if volume.captured_inputs.keys() - inputs:
                    raise ValueError(
                        "captured memberships must belong to the authoring context"
                    )
                for identifier, ids in volume.captured_inputs.items():
                    other = nodes[identifier]
                    assert isinstance(other, Selection)
                    original = (
                        active_entry(other.entries, entry.scan.key)
                        if other.entries is not None
                        else None
                    )
                    if original is None or original.mode != "generated":
                        raise ValueError(
                            "only generated authoring inputs have captured memberships"
                        )
                    if ids and ids[-1] >= entry.scan.vertices:
                        raise ValueError(
                            "captured input is outside the authoring source"
                        )
            if entry.source_volume is not None:
                _ = resolve_volume(recipe, entry.source_volume)
    dependencies: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}
    for key, (selection, entry, volume) in volumes.items():
        dependencies[key] = set()
        for identifier in authoring_selection_ids(recipe, selection, entry, volume):
            if identifier in volume.captured_inputs or (
                identifier == selection.id and volume.captured_ids is not None
            ):
                continue
            other = nodes[identifier]
            assert isinstance(other, Selection)
            original = (
                active_entry(other.entries, entry.scan.key)
                if other.entries is not None
                else None
            )
            if original is not None and original.source_volume is not None:
                ref = original.source_volume
                dependencies[key].add((ref.selection, ref.entry, ref.volume))
    visiting: set[tuple[str, str, str]] = set()
    visited: set[tuple[str, str, str]] = set()

    def visit(key: tuple[str, str, str]) -> None:
        if key in visiting:
            raise ValueError("cyclic selection volume authoring inputs")
        if key in visited:
            return
        visiting.add(key)
        for dependency in dependencies[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in dependencies:
        visit(key)
