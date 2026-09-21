from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import experiments.selection_salvage as salvage_module
from experiments.selection_salvage import (
    SelectionSalvageReport,
    publish_salvage,
    salvage_selections,
)
from scansor.selection_bundle import SelectionBundle, vertex_ids_sha256
from scansor.serialization import canonical_json, sha256

EXAMPLE = Path("examples/nozzle-bayonette-simplified")
RECIPES = (
    EXAMPLE / "recipes/cone-plane.json",
    EXAMPLE / "recipes/cylinder-plane.json",
)
BUNDLE = EXAMPLE / "selections/selection-bundle.json"
REPORT = EXAMPLE / "selections/selection-salvage-report.json"
USER_BUNDLE = EXAMPLE / "selections/user-selection-bundle.json"
USER_REPORT = EXAMPLE / "selections/user-selection-salvage-report.json"
LEGACY_SNAPSHOT_ARTIFACTS = (
    (
        EXAMPLE / "selections/legacy-actions-saved-selection-bundle.json",
        EXAMPLE / "selections/legacy-actions-saved-selection-salvage-report.json",
        (
            (
                "top_face",
                321,
                "c0c3ea46e7148471c5378f444e0f694d482474a8ed7dc80c5b97417fb3d1827f",
            ),
            (
                "outer_band",
                1261,
                "6a34b965a578b3fa94dcad95f3e4236ea804bf22df06bbcbbbf863fd903aeada",
            ),
            (
                "other_patch",
                321,
                "dd41f87aad91817ea09ebd6de07f034016702502e969760038324c0d9728b92f",
            ),
            (
                "growth_5d765d0868ca4396b5aa6ea6a27c1952",
                3284,
                "b69475e5f3c1b99f982f98a8f4adc6fa1c4a2417f413966cc909b237e7388eca",
            ),
        ),
        ("growth_5d765d0868ca4396b5aa6ea6a27c1952",),
    ),
    (
        EXAMPLE / "selections/legacy-rotation-selection-bundle.json",
        EXAMPLE / "selections/legacy-rotation-selection-salvage-report.json",
        (
            (
                "outer_band",
                320,
                "56ed839bf8fbbec717ab43ae49de1cd7df02e378e7b948b35de9483b939cac3e",
            ),
            (
                "top_face",
                49,
                "d109d117980693853d701eca4065b13070ab1c7a46243e49c83a9c507713320e",
            ),
            (
                "rot_selection_0",
                320,
                "f082f0e247347c26326c55e6ef2f99fcf4bc5e4e965614eefd33874938c79c78",
            ),
            (
                "rot_selection_1",
                320,
                "0d0e6cf94b11ba8d9ae5f05d16c0b9db7fdacc2f818537962f7b725e7144e55d",
            ),
            (
                "rot_selection_2",
                320,
                "2d6e01a5ae6d93583e62a39a581c1ccc71c038d66d28aa7c7afdd6c9a0ff4a23",
            ),
        ),
        (),
    ),
)


def _selection_evidence(
    bundle: SelectionBundle,
) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            selection.selection_id,
            selection.vertex_count,
            selection.vertex_ids_sha256,
        )
        for selection in bundle.selections
    )


def test_salvages_checked_in_selection_memberships_without_fit_semantics() -> None:
    bundle, report = salvage_selections(EXAMPLE, RECIPES)
    assert tuple(source.source_id for source in bundle.sources) == ("scan",)
    assert tuple(selection.selection_id for selection in bundle.selections) == (
        "outer_band",
        "top_face",
    )
    assert tuple(len(selection.vertex_ids) for selection in bundle.selections) == (
        1261,
        642,
    )
    assert tuple(selection.vertex_ids_sha256 for selection in bundle.selections) == (
        "6a34b965a578b3fa94dcad95f3e4236ea804bf22df06bbcbbbf863fd903aeada",
        "53e3c3a161472b72c6558031377a3c59ddf67886c1edbfddabd6b21711cab224",
    )
    assert bundle.sources[0].source_sha256 == (
        "537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358"
    )
    assert report.recipes[0].legacy_source_bindings[0].legacy_reference_sha256 == (
        "3f9c48e04a1ad9f31a202ef1cca6a5afce4fbba810aaf9d02e90326cf9d9d676"
    )
    assert all(selection.depth_mode == "through_all" for selection in bundle.selections)
    assert tuple(recipe.preserved_node_ids for recipe in report.recipes) == (
        ("scan", "outer_band", "top_face"),
        ("scan", "outer_band", "top_face"),
    )
    assert tuple(recipe.discarded_node_ids for recipe in report.recipes) == (
        ("side", "end", "perpendicular", "fit"),
        ("side", "end", "perpendicular", "fit"),
    )
    assert all(recipe.discarded_output == "fit" for recipe in report.recipes)
    with pytest.raises(ValidationError):
        _ = SelectionBundle.model_validate_json(RECIPES[0].read_bytes())


def test_checked_in_salvage_artifacts_are_exactly_reproducible() -> None:
    bundle, report = salvage_selections(EXAMPLE, RECIPES)
    assert BUNDLE.read_bytes() == canonical_json(bundle)
    assert REPORT.read_bytes() == canonical_json(report)


def test_user_authored_selection_bundle_is_retained_without_legacy_runtime() -> None:
    bundle_bytes = USER_BUNDLE.read_bytes()
    bundle = SelectionBundle.model_validate_json(bundle_bytes)
    report = SelectionSalvageReport.model_validate_json(USER_REPORT.read_bytes())
    assert len(bundle.selections) == 11
    assert sum(selection.vertex_count for selection in bundle.selections) == 1364
    assert tuple(selection.label for selection in bundle.selections) == (
        "outer",
        "recesses",
        "top",
        "recess axial",
        "recesses axial other",
        "recess slope a",
        "recess slope b",
        "recess slope c",
        "bump a",
        "bump b",
        "bump c",
    )
    assert all(
        selection.depth_mode == "first_surface" for selection in bundle.selections
    )
    expected_evidence = (
        (
            "selection_0350f17f14784ce2b686fafc6e709dfb",
            490,
            "dc519876d45d3a7a68cbac42b98c3673f9dc2a3fbb2bcfd622cf3037105867dd",
        ),
        (
            "selection_2df7d374a3234b18b2d5d96d66844bd0",
            314,
            "9200b30c205c7b81648faa66e95069ccdc313b0e18b26b9caf556d2de4863fcf",
        ),
        (
            "selection_0bcf6d9c18b84ed59026a3c906600409",
            240,
            "7cc32c5be045c1e709209998283d18173b8e936e50cfa837b55d9cd19d776811",
        ),
        (
            "selection_c2eea31a8e5140eca1a9274c23b244e3",
            151,
            "dc74ac688f137ea04554a7a7c05f3eaab6bacba51e96bdea23135b207c2ccb22",
        ),
        (
            "selection_c70b7cf64d6348a5b0ee9e9dd8b6ebb2",
            21,
            "74dc93cecd7527d4718f35999023f1d0c45b9ea540752bf4390dfe309d9a0303",
        ),
        (
            "selection_f1a351baca5946d4978db72fb86a3547",
            22,
            "5623ee648e7880b71dad15737b3a7ed35631fcbb4b05cfe1a65aeb0f9c6fb840",
        ),
        (
            "selection_ee2660f02f9348f28ec1511d55d3199d",
            25,
            "7cf9f82faae26ab9eecc34dafb1d9865fd13b831abf0cb46728d55d565763c89",
        ),
        (
            "selection_fd74c02433ae431daf73ac2836b0ce85",
            23,
            "4d4f72b57d8dd6be3b6bd881b8ad30ea00f2697376e216c744bd7c9c9327e63f",
        ),
        (
            "selection_3cfc10578fe04f2291074d0182f330cb",
            20,
            "7f16961d6909fbff8b2621e4ef5863d058eef2d6cf983ce08fbbd436ed06a910",
        ),
        (
            "selection_58c41f91f2564984a3290e9ec2971806",
            22,
            "ef9d901e55248a9b34df885bacfe3dc937b6532771344091098c0204bee679dd",
        ),
        (
            "selection_65d425a566a04734b45703c6c845cc2c",
            36,
            "73387d4dec2d76f222584723cc1a0df0538cf49c1b08570561f766e08cd56b7e",
        ),
    )
    assert _selection_evidence(bundle) == expected_evidence
    assert (
        tuple(
            (item.selection_id, item.vertex_count, item.vertex_ids_sha256)
            for item in report.selections
        )
        == expected_evidence
    )
    assert report.bundle_sha256 == sha256(bundle_bytes)
    assert tuple(recipe.recipe_sha256 for recipe in report.recipes) == (
        "206d9947ac3c899171af11b8fe598ec83e23055e0595abd863b5196cc290f2c4",
        "b74cf39ca18b0efe52c88706203c1237564a80924301df3ef2ba770cee0e1891",
    )
    assert all(len(recipe.materialized_growth_ids) == 0 for recipe in report.recipes)


@pytest.mark.parametrize(
    ("bundle_path", "report_path", "expected_evidence", "materialized_growth_ids"),
    LEGACY_SNAPSHOT_ARTIFACTS,
)
def test_conflicting_legacy_snapshots_are_retained_as_separate_bundles(
    bundle_path: Path,
    report_path: Path,
    expected_evidence: tuple[tuple[str, int, str], ...],
    materialized_growth_ids: tuple[str, ...],
) -> None:
    bundle_bytes = bundle_path.read_bytes()
    bundle = SelectionBundle.model_validate_json(bundle_bytes)
    report = SelectionSalvageReport.model_validate_json(report_path.read_bytes())
    assert _selection_evidence(bundle) == expected_evidence
    assert (
        tuple(
            (item.selection_id, item.vertex_count, item.vertex_ids_sha256)
            for item in report.selections
        )
        == expected_evidence
    )
    assert report.bundle_sha256 == sha256(bundle_bytes)
    assert report.recipes[0].materialized_growth_ids == materialized_growth_ids


@pytest.mark.parametrize("bad_ids", [(2, 1), (1, 1), (True,)])
def test_bundle_rejects_noncanonical_vertex_ids(bad_ids: tuple[object, ...]) -> None:
    bundle, _report = salvage_selections(EXAMPLE, RECIPES)
    payload = bundle.model_dump(mode="json")
    selection = payload["selections"][0]
    selection["vertex_ids"] = list(bad_ids)
    selection["vertex_count"] = len(bad_ids)
    selection["vertex_ids_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        _ = SelectionBundle.model_validate(payload)


def test_bundle_rejects_vertex_outside_source() -> None:
    bundle, _report = salvage_selections(EXAMPLE, RECIPES)
    payload = bundle.model_dump(mode="json")
    selection = payload["selections"][0]
    bad_ids = (payload["sources"][0]["vertex_count"],)
    selection["vertex_ids"] = list(bad_ids)
    selection["vertex_count"] = 1
    selection["vertex_ids_sha256"] = vertex_ids_sha256(bad_ids)
    with pytest.raises(ValidationError, match="outside its source"):
        _ = SelectionBundle.model_validate(payload)


def test_publication_rejects_path_alias_and_cleans_up_partial_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, report = salvage_selections(EXAMPLE, RECIPES)
    output = tmp_path / "output.json"
    with pytest.raises(ValueError, match="different paths"):
        publish_salvage(output, tmp_path / "." / "output.json", bundle, report)

    bundle_path = tmp_path / "bundle.json"
    report_path = tmp_path / "report.json"
    original = salvage_module.write_new_salvage_artifact
    calls = 0

    def fail_report(path: Path, data: bytes, label: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic report failure")
        original(path, data, label)

    monkeypatch.setattr(salvage_module, "write_new_salvage_artifact", fail_report)
    with pytest.raises(OSError, match="synthetic report failure"):
        publish_salvage(bundle_path, report_path, bundle, report)
    assert not bundle_path.exists()
    assert not report_path.exists()


def test_artifact_write_removes_file_after_post_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "partial.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="synthetic fsync failure"):
        salvage_module.write_new_salvage_artifact(output, b"partial", "test output")
    assert not output.exists()


def test_rejects_legacy_version_instead_of_using_graph_migration(
    tmp_path: Path,
) -> None:
    payload = json.loads(RECIPES[0].read_text())
    payload["schema_version"] = 1
    recipe = tmp_path / "legacy.json"
    _ = recipe.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="exactly browser recipe version 2"):
        _ = salvage_selections(EXAMPLE, (recipe,))


def test_conflicting_selection_identity_fails(tmp_path: Path) -> None:
    payload = cast(dict[str, Any], json.loads(RECIPES[0].read_text()))
    nodes = cast(list[dict[str, Any]], payload["nodes"])
    selection = next(node for node in nodes if node["id"] == "outer_band")
    selection["ids"] = cast(list[int], selection["ids"])[1:]
    conflicting = tmp_path / "conflicting.json"
    _ = conflicting.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="conflicting selection definition"):
        _ = salvage_selections(EXAMPLE, (RECIPES[0], conflicting))


def test_materializes_growth_membership_and_drops_its_fit_dependency(
    tmp_path: Path,
) -> None:
    payload = cast(dict[str, Any], json.loads(RECIPES[0].read_text()))
    nodes = cast(list[dict[str, Any]], payload["nodes"])
    nodes.extend(
        [
            {
                "id": "seed_fit",
                "label": "Seed only",
                "operation": "fit",
                "selections": ["outer_band"],
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "growth",
                "label": "Connected additions",
                "operation": "growth",
                "seed_fit": "seed_fit",
                "barriers": ["top_face"],
                "distance": 0.05,
                "angle_degrees": 20,
            },
        ]
    )
    recipe = tmp_path / "growth.json"
    _ = recipe.write_text(json.dumps(payload))
    bundle, report = salvage_selections(EXAMPLE, (recipe,))
    growth = next(
        selection
        for selection in bundle.selections
        if selection.selection_id == "growth"
    )
    outer = next(
        selection
        for selection in bundle.selections
        if selection.selection_id == "outer_band"
    )
    assert set(outer.vertex_ids) < set(growth.vertex_ids)
    assert growth.depth_mode is None
    assert report.recipes[0].materialized_growth_ids == ("growth",)
    assert "seed_fit" in report.recipes[0].discarded_node_ids
