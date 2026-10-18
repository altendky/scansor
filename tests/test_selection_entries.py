"""Exact source contexts, nested ownership and membership-only projections."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from experiments import volume_archive
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import (
    INTERPRETATION_SHA256,
    ScanLimits,
    ScanWorkspace,
    SourceDescriptor,
    prepare_scan,
)
from experiments.selection_entries import (
    SelectionEntry,
    SelectionVolume,
    SourceBinding,
    SurfaceAuthoringReference,
    VolumeReference,
    active_entry,
    initial_entry_id,
    membership_projection,
    source_binding,
    validate_entries,
)
from experiments.volume_archive import VolumeArchive
from tests.native_scan_fixture import native_grid_fixture


def descriptor(**changes: Any) -> SourceDescriptor:
    values = {
        "source_sha256": "a" * 64,
        "source_bytes": 512,
        "interpretation_sha256": INTERPRETATION_SHA256,
        "vertices": 9,
        "triangles": 8,
        "filename": "original.ply",
    }
    values.update(changes)
    return SourceDescriptor.model_validate(values)


def binding(**changes: Any) -> SourceBinding:
    saved = descriptor(**changes)
    return SourceBinding(
        source_sha256=saved.source_sha256,
        reference_sha256=saved.binding_sha256,
        vertices=saved.vertices,
        descriptor=saved,
    )


def reference() -> VolumeReference:
    return VolumeReference(selection="original", entry="source_entry", volume="patch")


def archived_volume(**changes: Any) -> SelectionVolume:
    # No geometry is adopted by these record tests: syntactically valid archive
    # metadata must not cause expansion at ordinary recipe validation time.
    values: dict[str, Any] = {
        "id": "patch",
        "label": "Saved patch",
        "kind": "mesh",
        "archive": VolumeArchive(sha256="b" * 64, uncompressed_bytes=4, data="AAAA"),
        "authoring_fingerprint": "c" * 64,
    }
    values.update(changes)
    return SelectionVolume.model_validate(values)


def painted(**changes: Any) -> SelectionEntry:
    values: dict[str, Any] = {
        "id": "original_entry",
        "scan": binding(),
        "mode": "painted",
        "ids": [0, 3, 8],
    }
    values.update(changes)
    return SelectionEntry.model_validate(values)


def generated(**changes: Any) -> SelectionEntry:
    values: dict[str, Any] = {
        "id": "target_entry",
        "scan": binding(source_sha256="d" * 64),
        "mode": "generated",
    }
    values.update(changes)
    return SelectionEntry.model_validate(values)


def test_descriptor_identity_ignores_filename_but_tracks_interpretation() -> None:
    first, renamed = binding(), binding(filename="renamed.ply")
    assert first.descriptor is not None
    assert first.key == renamed.key == first.descriptor.binding_sha256
    changed = binding(interpretation_sha256="e" * 64)
    assert changed.key != first.key
    assert binding(source_sha256="f" * 64).key != first.key


@pytest.mark.parametrize(
    "changes",
    [{"source_sha256": "d" * 64}, {"reference_sha256": "d" * 64}, {"vertices": 10}],
)
def test_source_descriptor_mismatch_rejected(changes: dict[str, Any]) -> None:
    values = binding().model_dump()
    values.update(changes)
    with pytest.raises(ValidationError, match="does not match"):
        _ = SourceBinding.model_validate(values)


def test_legacy_identity_retains_reference_and_vertex_count() -> None:
    original = SourceBinding(
        source_sha256="a" * 64, reference_sha256="b" * 64, vertices=9
    )
    assert original.key == SourceBinding.model_validate(original.model_dump()).key
    for field, value in (
        ("source_sha256", "c" * 64),
        ("reference_sha256", "c" * 64),
        ("vertices", 10),
    ):
        changed = SourceBinding.model_validate({**original.model_dump(), field: value})
        assert changed.key != original.key


@pytest.mark.parametrize("vertices", [0, -1, True, 3.0, "3"])
def test_strict_positive_vertex_count(vertices: object) -> None:
    with pytest.raises(ValidationError):
        _ = SourceBinding.model_validate(
            {
                "source_sha256": "a" * 64,
                "reference_sha256": "b" * 64,
                "vertices": vertices,
            }
        )


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "g" * 64, 1, True])
def test_source_hash_shape(digest: object) -> None:
    with pytest.raises(ValidationError):
        _ = SourceBinding.model_validate(
            {"source_sha256": digest, "reference_sha256": "b" * 64, "vertices": 3}
        )


@pytest.mark.parametrize("ids", [[1, 0], [1, 1], [-1], [9], [True], [1.0], ["1"], None])
def test_invalid_painted_membership(ids: object) -> None:
    with pytest.raises(ValidationError):
        _ = painted(ids=ids)


def test_painted_empty_membership_is_explicit_and_valid() -> None:
    assert painted(ids=[]).ids == []
    values = painted().model_dump()
    del values["ids"]
    with pytest.raises(ValidationError, match="require IDs"):
        _ = SelectionEntry.model_validate(values)


def test_membership_count_limit_is_independent_of_scan_size() -> None:
    large = binding(vertices=30_000)
    assert len(painted(scan=large, ids=list(range(25_000))).ids or []) == 25_000
    with pytest.raises(ValidationError, match="25000"):
        _ = painted(scan=large, ids=list(range(25_001)))


def test_generated_entries_wait_for_choice_without_painted_ids() -> None:
    pending = generated()
    assert pending.source_volume is None and pending.ids is None
    chosen = generated(source_volume=reference())
    assert chosen.source_volume == reference()
    for ids in ([], [0]):
        with pytest.raises(ValidationError, match="cannot contain painted IDs"):
            _ = generated(ids=ids)
    with pytest.raises(ValidationError, match="cannot choose"):
        _ = painted(source_volume=reference())


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_analytic_volume_requires_explicit_surface_reference(kind: str) -> None:
    with pytest.raises(ValidationError, match="require an authoring fit"):
        _ = archived_volume(kind=kind)
    fitted = archived_volume(
        kind=kind, fit={"feature": "shared_solve", "surface": "fit"}
    )
    assert fitted.fit == SurfaceAuthoringReference(
        feature="shared_solve", surface="fit"
    )
    with pytest.raises(ValidationError, match="do not reference"):
        _ = archived_volume(fit={"feature": "fit"})


@pytest.mark.parametrize("ids", [[2, 1], [1, 1], [-1], [True], [1.0], ["1"]])
def test_captured_generated_membership_is_strict(ids: object) -> None:
    with pytest.raises(ValidationError):
        _ = archived_volume(captured_ids=ids)


def test_painted_membership_cannot_be_replaced_by_an_authoring_capture() -> None:
    with pytest.raises(ValidationError, match="only generated"):
        _ = painted(volumes=[archived_volume(captured_ids=[0, 1])])


def test_capture_bounds_and_volume_ownership() -> None:
    valid = archived_volume(captured_ids=[0, 8])
    assert generated(volumes=[valid]).volumes == [valid]
    with pytest.raises(ValidationError, match="captured ID"):
        _ = generated(volumes=[archived_volume(captured_ids=[9])])
    with pytest.raises(ValidationError, match="volume IDs must be unique"):
        _ = painted(volumes=[valid, valid])
    volumes = [archived_volume(id=f"patch_{i}") for i in range(64)]
    assert len(painted(volumes=volumes).volumes) == 64
    with pytest.raises(ValidationError, match="64"):
        _ = painted(volumes=[*volumes, archived_volume(id="patch_64")])


def test_entry_uniqueness_and_automatic_active_resolution() -> None:
    first, target = painted(), generated()
    entries = [first, target]
    assert validate_entries(entries) is entries
    assert active_entry(entries, first.scan.key) is first
    assert active_entry(entries, target.scan.key) is target
    assert active_entry(entries, "unknown") is None
    with pytest.raises(ValueError, match="entry IDs"):
        _ = validate_entries([first, generated(id=first.id)])
    with pytest.raises(ValueError, match="one entry per scan"):
        _ = validate_entries(
            [first, painted(id="renamed", scan=binding(filename="other.ply"))]
        )
    with pytest.raises(ValueError, match="one entry per scan"):
        _ = active_entry([first, painted(id="duplicate")], first.scan.key)


def test_membership_projection_ignores_owned_geometry_labels_and_other_scans() -> None:
    first = painted(volumes=[archived_volume()])
    changed = painted(
        volumes=[archived_volume(label="Changed", authoring_fingerprint="d" * 64)]
    )
    assert (
        membership_projection(first)
        == membership_projection(changed)
        == {"mode": "painted", "ids": [0, 3, 8], "depth": "through_all"}
    )
    assert membership_projection(
        painted(depth="first_surface")
    ) != membership_projection(first)
    assert membership_projection(painted(ids=[0])) != membership_projection(first)
    pending = generated()
    assert membership_projection(pending) == {
        "mode": "generated",
        "source_volume": None,
    }
    chosen = generated(source_volume=reference())
    assert membership_projection(chosen) == membership_projection(
        generated(
            source_volume=reference(),
            volumes=[archived_volume()],
            depth="first_surface",
        )
    )
    assert membership_projection(chosen) != membership_projection(pending)
    projection = membership_projection(first)
    projection["ids"].append(7)
    assert first.ids == [0, 3, 8]


def test_records_and_roundtrip_do_not_decode_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("nested recipe validation decoded a saved volume")

    monkeypatch.setattr(volume_archive, "decode_selection_volume", forbidden)
    original = painted(volumes=[archived_volume()])
    restored = SelectionEntry.model_validate_json(original.model_dump_json())
    assert restored == original
    with pytest.raises(ValidationError, match="frozen"):
        restored.id = "changed"


@pytest.mark.parametrize("identifier", ["", "has space", "../path", "a" * 65, 5])
def test_nested_identifier_shape(identifier: object) -> None:
    with pytest.raises(ValidationError):
        _ = VolumeReference.model_validate(
            {"selection": identifier, "entry": "entry", "volume": "volume"}
        )
    with pytest.raises(ValidationError):
        _ = SurfaceAuthoringReference.model_validate({"feature": identifier})


def test_unknown_fields_and_strict_nested_shape() -> None:
    for cls, values in (
        (SourceBinding, binding().model_dump()),
        (VolumeReference, reference().model_dump()),
        (SurfaceAuthoringReference, {"feature": "fit"}),
        (SelectionVolume, archived_volume().model_dump()),
        (SelectionEntry, painted().model_dump()),
    ):
        with pytest.raises(ValidationError, match="Extra inputs"):
            _ = cls.model_validate({**values, "unknown": True})


def test_source_binding_uses_public_imported_and_legacy_workspace_identity() -> None:
    fixture = native_grid_fixture(3, 3)
    workspace = prepare_scan(
        io.BytesIO(fixture.raw), "scan.ply", ScanLimits(100_000, 100, 100, 4_000_000)
    )
    imported = source_binding(workspace)
    assert imported.descriptor == workspace.source_descriptor
    assert imported.key == workspace.reference_sha256
    assert imported.vertices == len(workspace.local)
    legacy_workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
    legacy = source_binding(legacy_workspace)
    assert legacy.descriptor is None
    assert legacy.reference_sha256 == legacy_workspace.reference_sha256
    assert legacy.source_sha256 == legacy_workspace.source_sha256
    assert legacy.vertices == len(legacy_workspace.local)
    with pytest.raises(ValueError, match="empty workspace"):
        _ = source_binding(ScanWorkspace.empty())


def test_initial_entry_identity_is_stable_and_scoped() -> None:
    key = binding().key
    first = initial_entry_id("selection", key)
    assert first == initial_entry_id("selection", binding(filename="other.ply").key)
    assert first != initial_entry_id("different_selection", key)
    assert first != initial_entry_id("selection", binding(source_sha256="d" * 64).key)
    assert len(first) <= 64
    assert painted(id=first).id == first
