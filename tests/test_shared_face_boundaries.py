"""Approved boundary segments constrain regions without requiring identical edges."""

from copy import deepcopy
from typing import Any

import numpy as np
import pytest
from pytest import MonkeyPatch

from experiments import face_arrangement
from experiments.face_arrangement import arrange_faces
from experiments.shared_face_boundaries import (
    approved_boundary_previews,
    check_shared_boundaries,
)


def plane(axis: tuple[float, float, float], offset: float = 0) -> dict[str, Any]:
    return {"kind": "plane", "axis": list(axis), "offset": offset}


def rectangle(
    surface: dict[str, Any],
    cuts: dict[str, dict[str, Any]],
    signs: dict[str, str],
    ref: dict[str, Any],
) -> dict[str, Any]:
    records = arrange_faces(
        surface,
        [{"key": key, "geometry": geometry} for key, geometry in cuts.items()],
        coverage=np.array([[-5, -5, -5], [5, 5, 5]], dtype=float),
    )
    record = next(
        record for record in records if record["region_identity"]["signs"] == signs
    )
    record["surface"] = ref
    return record


TARGET = {"feature": "top", "context": "direct"}
SOURCE = {"feature": "side", "context": "direct"}
TOP = plane((0, 0, 1))
SIDE = plane((0, 1, 0))


def side() -> dict[str, Any]:
    return rectangle(
        SIDE,
        {
            "left": plane((1, 0, 0), -2),
            "right": plane((1, 0, 0), 2),
            "top": TOP,
            "bottom": plane((0, 0, 1), -2),
        },
        {
            "left": "positive",
            "right": "negative",
            "top": "negative",
            "bottom": "positive",
        },
        SOURCE,
    )


def top(lo: float = -2, hi: float = 2) -> dict[str, Any]:
    return rectangle(
        TOP,
        {
            "left": plane((1, 0, 0), lo),
            "right": plane((1, 0, 0), hi),
            "front": SIDE,
            "back": plane((0, 1, 0), 2),
        },
        {
            "left": "positive",
            "right": "negative",
            "front": "positive",
            "back": "negative",
        },
        TARGET,
    )


def test_complete_shared_edge_and_guidance():
    sources = [{"id": "approved-side", "record": side()}]
    assert check_shared_boundaries([top()], TARGET, sources, TOP)["complete"]
    previews = approved_boundary_previews(sources, TARGET, TOP)
    assert len(previews) == 1
    points = np.asarray(previews[0]["positions"]).reshape(-1, 3)
    assert np.allclose(points[:, 1:], 0)
    assert points[:, 0].min() == pytest.approx(-2)
    assert points[:, 0].max() == pytest.approx(2)


def test_approved_edge_may_be_split_across_target_cells():
    _ = check_shared_boundaries(
        [top(-2, 0), top(0, 2)], TARGET, [{"id": "approved", "record": side()}], TOP
    )


def test_subset_valid_for_replay_but_not_complete_apply():
    sources = [{"id": "approved", "record": side()}]
    _ = check_shared_boundaries(
        [top(-2, 0)], TARGET, sources, TOP, require_complete=False
    )
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries([top(-2, 0)], TARGET, sources, TOP)


def test_target_may_not_extend_past_approved_arc():
    with pytest.raises(ValueError, match="extend beyond"):
        _ = check_shared_boundaries(
            [top(-3, 2)], TARGET, [{"id": "approved", "record": side()}], TOP
        )


def test_small_gap_is_not_absorbed_by_relative_length_tolerance():
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries(
            [top(-2, -0.00001), top(0.00001, 2)],
            TARGET,
            [{"id": "approved", "record": side()}],
            TOP,
        )


def test_open_source_artificial_envelope_is_not_a_physical_boundary():
    record = rectangle(SIDE, {"top": TOP}, {"top": "negative"}, SOURCE)
    assert not record["bounded"]
    # Its infinite line cannot acquire finite physical endpoints from a carrier.
    with pytest.raises(ValueError, match="clipped by a display envelope"):
        _ = approved_boundary_previews([{"id": "open", "record": record}], TARGET, TOP)


def test_legacy_open_interval_keeps_only_declared_ring():
    geometry = {
        "kind": "cylinder",
        "origin": [0, 0, 0],
        "axis": [0, 0, 1],
        "basis_u": [1, 0, 0],
        "basis_v": [0, 1, 0],
        "radius": 2,
        "slope": 0,
    }
    record = {
        "kind": "trimmed_face",
        "surface_kind": "cylinder",
        "geometry": geometry,
        "bounded": False,
        "bounds": {"axial": [None, 0]},
        "surface": SOURCE,
    }
    previews = approved_boundary_previews(
        [{"id": "open-cylinder", "record": record}], TARGET, TOP
    )
    assert len(previews) == 1
    assert np.allclose(np.asarray(previews[0]["positions"]).reshape(-1, 3)[:, 2], 0)
    with pytest.raises(ValueError, match="no physical boundary"):
        _ = check_shared_boundaries(
            [], TARGET, [{"id": "open-cylinder", "record": record}], plane((0, 0, 1), 5)
        )


@pytest.mark.parametrize("scale", [1e-3, 1, 1e3])
def test_shared_edges_covariant_under_scale_and_large_translation(scale: float):
    translation = np.array([1e6, -1e6, 1e6])

    def moved(geometry: dict[str, Any]) -> dict[str, Any]:
        return {
            **geometry,
            "offset": geometry["offset"] * scale
            + np.asarray(geometry["axis"]) @ translation,
        }

    def moved_record(record: dict[str, Any]) -> dict[str, Any]:
        intent = record["bounds"]["arrangement"]
        records = arrange_faces(
            moved(intent["surface"]),
            [
                {"key": cut["key"], "geometry": moved(cut["geometry"])}
                for cut in intent["cutters"]
            ],
            coverage=np.array([[-5, -5, -5], [5, 5, 5]]) * scale + translation,
        )
        result = next(
            item
            for item in records
            if item["region_identity"]["signs"] == record["region_identity"]["signs"]
        )
        result["surface"] = record["surface"]
        return result

    approved = [{"id": "side", "record": moved_record(side())}]
    _ = check_shared_boundaries(
        [moved_record(top(-2, 0)), moved_record(top(0, 2))],
        TARGET,
        approved,
        moved(TOP),
    )
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries(
            [moved_record(top(-2, 0))], TARGET, approved, moved(TOP)
        )


def test_curved_arc_may_be_split_across_target_cells():
    cylinder = {
        "kind": "cylinder",
        "axis": [0, 0, 1],
        "origin": [0, 0, 0],
        "radius": 2,
        "slope": 0,
    }
    clock = plane((1, 0, 0), 1)
    approved = rectangle(
        TOP,
        {"outer": cylinder, "clock": clock},
        {"outer": "negative", "clock": "negative"},
        SOURCE,
    )
    records = arrange_faces(
        cylinder,
        [
            {"key": "top", "geometry": TOP},
            {"key": "bottom", "geometry": plane((0, 0, 1), -2)},
            {"key": "clock", "geometry": clock},
            {"key": "split", "geometry": plane((0, 1, 0))},
        ],
        coverage=np.array([[-3, -3, -3], [3, 3, 3]], dtype=float),
    )
    selected = [
        record
        for record in records
        if record["region_identity"]["signs"]["top"] == "negative"
        and record["region_identity"]["signs"]["bottom"] == "positive"
        and record["region_identity"]["signs"]["clock"] == "negative"
    ]
    assert len(selected) == 2
    for record in selected:
        record["surface"] = TARGET
    sources = [{"id": "shoulder", "record": approved}]
    _ = check_shared_boundaries(selected, TARGET, sources, cylinder)
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries(selected[:1], TARGET, sources, cylinder)


def test_open_source_valid_arc_is_not_blocked_by_unrelated_clipped_edge():
    cylinder = {
        "kind": "cylinder",
        "axis": [0, 0, 1],
        "origin": [0, 0, 0],
        "radius": 2,
        "slope": 0,
    }
    clock = plane((1, 0, 0), 1)
    approved = rectangle(
        cylinder,
        {"top": TOP, "clock": clock},
        {"top": "negative", "clock": "negative"},
        SOURCE,
    )
    assert not approved["bounded"]
    retained = rectangle(
        TOP,
        {"outer": cylinder, "clock": clock},
        {"outer": "negative", "clock": "negative"},
        TARGET,
    )
    _ = check_shared_boundaries(
        [retained], TARGET, [{"id": "open-outer", "record": approved}], TOP
    )


def test_multiple_approved_source_cells_form_one_shared_segment_union():
    def half(lo: float, hi: float) -> dict[str, Any]:
        return rectangle(
            SIDE,
            {
                "left": plane((1, 0, 0), lo),
                "right": plane((1, 0, 0), hi),
                "top": TOP,
                "bottom": plane((0, 0, 1), -2),
            },
            {
                "left": "positive",
                "right": "negative",
                "top": "negative",
                "bottom": "positive",
            },
            SOURCE,
        )

    sources = [
        {"id": "left", "record": half(-2, 0)},
        {"id": "right", "record": half(0, 2)},
    ]
    _ = check_shared_boundaries([top()], TARGET, sources, TOP)


def test_internal_cell_edge_is_not_a_boundary_of_selected_region_union():
    other_side = rectangle(
        TOP,
        {
            "left": plane((1, 0, 0), -2),
            "right": plane((1, 0, 0), 2),
            "front": plane((0, 1, 0), -2),
            "back": SIDE,
        },
        {
            "left": "positive",
            "right": "negative",
            "front": "positive",
            "back": "negative",
        },
        TARGET,
    )
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries(
            [top(), other_side],
            TARGET,
            [{"id": "approved-side", "record": side()}],
            TOP,
        )


def test_cone_negative_radius_sheet_is_not_a_shared_physical_boundary():
    source = {
        "kind": "trimmed_face",
        "surface_kind": "plane",
        "surface": SOURCE,
        "bounded": True,
        "geometry": {
            "kind": "plane",
            "origin": [0, 0, -4],
            "axis": [0, 0, 1],
            "offset": -4,
            "basis_u": [1, 0, 0],
            "basis_v": [0, 1, 0],
        },
        "bounds": {"radial": [0, 2]},
    }
    cone = {
        "kind": "cone",
        "origin": [0, 0, 0],
        "axis": [0, 0, 1],
        "radius": 2,
        "slope": 1,
    }
    assert (
        approved_boundary_previews(
            [{"id": "negative-sheet", "record": source}], TARGET, cone
        )
        == []
    )


def test_shared_boundary_pass_replays_each_arrangement_once_and_discards_cache(
    monkeypatch: MonkeyPatch,
):
    records = arrange_faces(
        TOP,
        [
            {"key": key, "geometry": geometry}
            for key, geometry in {
                "left": plane((1, 0, 0), -2),
                "right": plane((1, 0, 0), 2),
                "front": SIDE,
                "back": plane((0, 1, 0), 2),
                "half": plane((1, 0, 0)),
            }.items()
        ],
        coverage=np.array([[-5, -5, -5], [5, 5, 5]], dtype=float),
    )
    selected = [record for record in records if record["bounded"]]
    assert len(selected) == 2
    for record in selected:
        record["surface"] = TARGET
    back = rectangle(
        plane((0, 1, 0), 2),
        {
            "left": plane((1, 0, 0), -2),
            "right": plane((1, 0, 0), 2),
            "top": TOP,
            "bottom": plane((0, 0, 1), -2),
        },
        {
            "left": "positive",
            "right": "negative",
            "top": "negative",
            "bottom": "positive",
        },
        {"feature": "back", "context": "direct"},
    )
    sources = [
        {"id": "front", "record": side()},
        {"id": "back", "record": back},
    ]
    pristine = deepcopy((selected, sources))
    original = vars(face_arrangement)["_split"]
    calls = 0

    def measured(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", measured)
    assert check_shared_boundaries(selected, TARGET, sources, TOP)["complete"]
    # Two approved-source arrangements and one arrangement for both target
    # cells, even though both targets are checked against two source surfaces.
    assert calls == 3
    assert check_shared_boundaries(selected, TARGET, sources, TOP)["complete"]
    assert calls == 6
    assert (selected, sources) == pristine
    # Replay reuse must not weaken complete-union coverage validation.
    with pytest.raises(ValueError, match="do not cover"):
        _ = check_shared_boundaries(selected[:1], TARGET, sources, TOP)
    assert calls == 9
    assert check_shared_boundaries(selected, TARGET, sources, TOP)["complete"]
    assert calls == 12
