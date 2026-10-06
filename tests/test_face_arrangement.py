"""Native arrangements retain real regions, not idealized full-loop substitutes."""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from threading import Barrier, get_ident
from typing import Any

import numpy as np
import pytest
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.collections import List_TopoDS_Shape
from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.TopLoc import TopLoc_Location
from pytest import MonkeyPatch

from experiments import face_arrangement
from experiments.face_arrangement import (
    arrange_faces,
    face_from_record,
    prepare_faces,
    same_region,
    select_face,
)
from experiments.general_face_geometry import face_from_boundaries, intersection_record
from experiments.native_replay import native_replay_scope
from experiments.ocp_geometry import checked_face, primitive_surface


def plane(
    axis: tuple[float, float, float] = (0, 0, 1), offset: float = 0
) -> dict[str, Any]:
    vector = np.asarray(axis, dtype=float)
    vector /= np.linalg.norm(vector)
    return {"kind": "plane", "axis": vector.tolist(), "offset": offset}


def cylinder(
    radius: float = 2, origin: tuple[float, float, float] = (0, 0, 0)
) -> dict[str, Any]:
    return {
        "kind": "cylinder",
        "axis": [0, 0, 1],
        "origin": list(origin),
        "radius": radius,
        "slope": 0,
    }


def cutter(key: str, geometry: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "geometry": geometry}


def observations(scale: float = 1, translation: float = 0) -> np.ndarray:
    return np.array(
        [[-5, -5, 0], [-5, 5, 0], [5, -5, 0], [5, 5, 0], [0, 0, 0]], dtype=float
    ) * scale + np.array([translation, translation, 0])


def area(record: dict[str, Any]) -> float:
    face = face_from_record(json.loads(json.dumps(record)))
    assert BRepCheck_Analyzer(face).IsValid()
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    return properties.Mass()


def region(records: list[dict[str, Any]], signs: dict[str, str]) -> dict[str, Any]:
    matches = [
        record for record in records if record["region_identity"]["signs"] == signs
    ]
    assert len(matches) == 1
    return matches[0]


def test_d_shape_uses_partial_circle_arc_and_line():
    p = plane()
    cuts = [cutter("outer", cylinder()), cutter("clock", plane((1, 0, 0), 1))]
    records = arrange_faces(p, cuts, observations=observations())
    kept = region(records, {"outer": "negative", "clock": "negative"})
    assert kept["bounded"] and not kept["preview_clipped"]
    expected = 4 * math.pi - (4 * math.acos(0.5) - math.sqrt(3))
    assert area(kept) == pytest.approx(expected, rel=1e-8)
    edges = [edge for loop in kept["loops"] for edge in loop["edges"]]
    assert {edge["kind"] for edge in edges} == {"line", "circle"}
    assert any(
        edge["kind"] == "circle" and edge["range"][1] - edge["range"][0] < 2 * math.pi
        for edge in edges
    )
    assert kept["boundary_keys"] == ["clock", "outer"]
    assert all(edge["sources"] for edge in edges)


def test_plane_arrangement_multiple_holes_from_actual_cells():
    p = plane()
    cuts = [
        cutter("outer", cylinder(5)),
        cutter("first", cylinder(1, (-2, 0, 0))),
        cutter("second", cylinder(1, (2, 0, 0))),
    ]
    kept = region(
        arrange_faces(p, cuts, observations=observations()),
        {"outer": "negative", "first": "positive", "second": "positive"},
    )
    assert area(kept) == pytest.approx(23 * math.pi, rel=1e-8)
    assert len(kept["loops"]) == 3


def test_plate_perimeter_plane_lines_and_boss_holes():
    p = plane()
    cuts = [
        cutter("left", plane((1, 0, 0), -4)),
        cutter("right", plane((1, 0, 0), 4)),
        cutter("front", plane((0, 1, 0), -3)),
        cutter("back", plane((0, 1, 0), 3)),
        cutter("hole", cylinder(1)),
    ]
    kept = region(
        arrange_faces(p, cuts, observations=observations()),
        {
            "left": "positive",
            "right": "negative",
            "front": "positive",
            "back": "negative",
            "hole": "positive",
        },
    )
    assert area(kept) == pytest.approx(48 - math.pi, rel=1e-8)
    assert kept["bounded"]


def finite_boss_sources(
    scale: float = 1,
    shift: tuple[float, float, float] = (0, 0, 0),
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    x, y, z = shift
    target = plane(offset=z)
    outer = cylinder(2 * scale, shift)
    clock = plane((1, 0, 0), x + scale)
    lower, upper = plane(offset=z), plane(offset=z + 4 * scale)
    side = region(
        arrange_faces(
            outer,
            [cutter("clock", clock), cutter("lower", lower), cutter("upper", upper)],
        ),
        {"clock": "negative", "lower": "positive", "upper": "negative"},
    )
    flat = region(
        arrange_faces(
            clock,
            [cutter("outer", outer), cutter("lower", lower), cutter("upper", upper)],
        ),
        {"outer": "negative", "lower": "positive", "upper": "negative"},
    )
    plate = region(
        arrange_faces(
            target,
            [
                cutter("left", plane((1, 0, 0), x - 4 * scale)),
                cutter("right", plane((1, 0, 0), x + 4 * scale)),
                cutter("front", plane((0, 1, 0), y - 3 * scale)),
                cutter("back", plane((0, 1, 0), y + 3 * scale)),
            ],
        ),
        {
            "left": "positive",
            "right": "negative",
            "front": "positive",
            "back": "negative",
        },
    )
    return (
        target,
        [
            {**cutter("outer", outer), "face_domains": [side]},
            {**cutter("clock", clock), "face_domains": [flat]},
        ],
        plate,
    )


@pytest.mark.parametrize(
    "scale,shift", [(1e-6, (0, 0, 0)), (1, (1e6, -2e6, 3e6)), (1e6, (0, 0, 0))]
)
def test_finite_neighbor_faces_close_boss_footprint_without_remote_clock_cut(
    scale: float, shift: tuple[float, float, float]
):
    target, cuts, plate = finite_boss_sources(scale, shift)
    points = (
        np.asarray([[-3, -2, 0], [3, -2, 0], [0, 0, 0]], dtype=float) * scale + shift
    )
    records = arrange_faces(target, cuts, domains=[plate], observations=points)
    assert len(records) == 2
    expected = (4 * math.pi - (4 * math.acos(0.5) - math.sqrt(3))) * scale**2
    inside, outside = sorted(records, key=area)
    assert area(inside) == pytest.approx(expected, rel=2e-7)
    assert area(outside) == pytest.approx(48 * scale**2 - expected, rel=2e-7)
    assert (
        inside["region_identity"]["signs"] == outside["region_identity"]["signs"] == {}
    )
    assert inside["region_identity"]["component_count"] == 1
    assert inside["region_identity"]["finite_sides"] == {
        "clock": ["negative"],
        "outer": ["negative"],
    }
    assert outside["region_identity"]["finite_sides"] == {
        "clock": ["positive"],
        "outer": ["positive"],
    }
    assert outside["evidence"]["interior"] == [True, True, False]
    assert inside["evidence"]["interior"] == [False, False, True]
    assert len(outside["loops"]) == 2
    assert outside["boundary_keys"] == ["clock", "outer"]
    assert all(
        not edge["artificial"]
        for record in records
        for loop in record["loops"]
        for edge in loop["edges"]
    )
    replay = select_face(
        target, list(reversed(cuts)), outside["region_identity"], domains=[plate]
    )
    assert same_region(outside, replay)
    # A finite-cut arrangement remains a valid physical scope, recursively
    # normalized when a later arrangement uses it at its own local scale.
    scoped = arrange_faces(target, [], domains=[outside])
    assert len(scoped) == 1 and same_region(outside, scoped[0])


def test_finite_cutter_rejects_open_or_empty_domain_guidance():
    target, cuts, plate = finite_boss_sources()
    for domains in ([], [{**cuts[0]["face_domains"][0], "bounded": False}]):
        with pytest.raises(ValueError, match="nonempty bounded physical faces"):
            _ = arrange_faces(
                target, [{**cuts[0], "face_domains": domains}], domains=[plate]
            )


def test_finite_replay_rejects_witness_that_crosses_a_physical_edge():
    target, cuts, plate = finite_boss_sources()
    records = arrange_faces(target, cuts, domains=[plate])
    inside, outside = sorted(records, key=area)
    # Keeping the same component count is insufficient: the opposite cell
    # must not be accepted merely because it now contains the saved witness.
    bad = {
        **inside["region_identity"],
        "witness_chart": outside["region_identity"]["witness_chart"],
    }
    with pytest.raises(ValueError, match="witness crossed"):
        _ = select_face(target, cuts, bad, domains=[plate])
    _, moved_cuts, _ = finite_boss_sources(shift=(-1.9, 0, 0))
    moved = arrange_faces(target, moved_cuts, domains=[plate])
    assert len(moved) == len(records)
    assert sorted(r["region_identity"]["component_count"] for r in moved) == [1, 1]
    with pytest.raises(ValueError, match="witness crossed"):
        _ = select_face(target, moved_cuts, inside["region_identity"], domains=[plate])


def test_finite_neighbor_footprints_keep_multiple_holes_and_full_cutters():
    target, _, plate = finite_boss_sources()
    cuts: list[dict[str, Any]] = []
    for label, x in (("first", -1.5), ("second", 1.5)):
        _, source_cuts, _ = finite_boss_sources(0.5, (x, 0, 0))
        cuts.extend({**cut, "key": label + "_" + cut["key"]} for cut in source_cuts)
    records = arrange_faces(target, cuts, domains=[plate])
    assert len(records) == 3
    exterior = max(records, key=area)
    assert len(exterior["loops"]) == 3
    assert exterior["boundary_keys"] == [
        "first_clock",
        "first_outer",
        "second_clock",
        "second_outer",
    ]
    assert all(
        sides == ["positive"]
        for sides in exterior["region_identity"]["finite_sides"].values()
    )
    expected_holes = 0.5 * (4 * math.pi - (4 * math.acos(0.5) - math.sqrt(3)))
    assert area(exterior) == pytest.approx(48 - expected_holes, rel=1e-8)
    # A genuinely unscoped cutter still partitions the entire surface and
    # retains its explicit halfspace identity alongside the finite neighbors.
    full = [*cuts, cutter("partition", plane((0, 1, 0), -2.5))]
    divided = arrange_faces(target, full, domains=[plate])
    assert len(divided) == 4
    lower = region(divided, {"partition": "negative"})
    assert area(lower) == pytest.approx(4)
    assert lower["region_identity"].get("finite_sides", {}) == {}


def test_two_generator_lines_split_a_bounded_cylinder_band():
    c = cylinder()
    angles = np.linspace(0, 2 * math.pi, 32, endpoint=False)
    points = np.concatenate(
        [
            np.column_stack(
                [2 * np.cos(angles), 2 * np.sin(angles), np.full_like(angles, z)]
            )
            for z in (-1, 5)
        ]
    )
    cuts = [
        cutter("lower", plane(offset=0)),
        cutter("upper", plane(offset=4)),
        cutter("clock", plane((1, 0, 0), 1)),
    ]
    kept = region(
        arrange_faces(c, cuts, observations=points),
        {"lower": "positive", "upper": "negative", "clock": "negative"},
    )
    assert kept["bounded"]
    assert area(kept) == pytest.approx(
        2 * 4 * (2 * math.pi - 2 * math.acos(0.5)), rel=1e-8
    )
    assert any(
        edge["kind"] == "line" for loop in kept["loops"] for edge in loop["edges"]
    )


def test_display_envelope_is_never_a_physical_cap():
    p = plane()
    records = arrange_faces(
        p, [cutter("hole", cylinder())], observations=observations()
    )
    exterior = region(records, {"hole": "positive"})
    assert not exterior["bounded"] and exterior["preview_clipped"]
    assert any(
        edge["artificial"] for loop in exterior["loops"] for edge in loop["edges"]
    )
    with pytest.raises(ValueError, match="physical caps"):
        _ = face_from_record(exterior)


def test_explicit_physical_domain_boundary_is_exportable():
    p = plane()
    domain = face_from_boundaries(
        p,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                intersection_record(p, cylinder(5)),
            )
        ],
    )
    cuts = [cutter("clock", plane((1, 0, 0), 1))]
    kept = region(arrange_faces(p, cuts, domains=[domain]), {"clock": "negative"})
    assert kept["bounded"]
    assert area(kept) > 0
    assert any(
        edge["domain_sources"] for loop in kept["loops"] for edge in loop["edges"]
    )


def test_replay_preserves_selector_and_refuses_crossed_witness():
    p = plane()
    cuts = [cutter("outer", cylinder()), cutter("clock", plane((1, 0, 0), 1))]
    kept = region(
        arrange_faces(p, cuts, observations=observations()),
        {"outer": "negative", "clock": "negative"},
    )
    selector = kept["region_identity"]
    replay = select_face(p, list(reversed(cuts)), selector, observations=observations())
    assert replay["region_identity"] == selector
    assert area(replay) == pytest.approx(area(kept), rel=1e-8)
    bad = {**selector, "witness_chart": [100, 100]}
    with pytest.raises(ValueError, match="witness crossed"):
        _ = select_face(p, cuts, bad, observations=observations())


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_d_shape_at_different_scales(scale: float):
    p = plane()
    cuts = [
        cutter("outer", cylinder(2 * scale)),
        cutter("clock", plane((1, 0, 0), scale)),
    ]
    kept = region(
        arrange_faces(p, cuts, observations=observations(scale)),
        {"outer": "negative", "clock": "negative"},
    )
    assert area(kept) == pytest.approx(
        (4 * math.pi - (4 * math.acos(0.5) - math.sqrt(3))) * scale**2, rel=1e-8
    )


def test_raw_fit_records_are_adapted_without_schema_compatibility_shims():
    p = {"kind": "plane", "plane_equation": [0, 0, 1, 0]}
    c = {"kind": "cylinder", "parameters": [0, 0, 0, 0, 2, 0, 0]}
    kept = region(
        arrange_faces(p, [cutter("outer", c)], observations=observations()),
        {"outer": "negative"},
    )
    assert area(kept) == pytest.approx(4 * math.pi)
    assert len(kept["evidence"]["defined"]) == 5


@pytest.mark.parametrize("coverage", [None, np.array([[0.0, 0.0, 0.0]])])
def test_closed_cutter_disk_exists_without_broad_scan_coverage(
    coverage: np.ndarray | None,
):
    kept = region(
        arrange_faces(plane(), [cutter("outer", cylinder())], observations=coverage),
        {"outer": "negative"},
    )
    assert kept["bounded"]
    assert area(kept) == pytest.approx(4 * math.pi)


def test_overlapping_physical_domains_form_a_union_not_duplicate_cells():
    p = plane()
    domains = [
        face_from_boundaries(
            p,
            [
                (
                    {"intersection": f"domain-{i}", "keep": "inside"},
                    intersection_record(p, cylinder(2, (x, 0, 0))),
                )
            ],
        )
        for i, x in enumerate((-1, 1))
    ]
    kept = region(arrange_faces(p, [], domains=domains), {})
    assert kept["region_identity"]["component_count"] == 1
    assert area(kept) == pytest.approx(16 * math.pi / 3 + 2 * math.sqrt(3), rel=1e-8)


def test_manual_region_matching_uses_native_difference_not_witness_or_mesh():
    p = plane()
    manual = face_from_boundaries(
        p,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                intersection_record(p, cylinder()),
            )
        ],
    )
    actual = region(
        arrange_faces(p, [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    assert same_region(manual, actual)
    smaller = face_from_boundaries(
        p,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                intersection_record(p, cylinder(1)),
            )
        ],
    )
    assert not same_region(smaller, actual)


def test_region_equality_does_not_fill_a_small_domain_origin_hole():
    p = plane()
    outer = (
        {"intersection": "outer", "keep": "inside"},
        intersection_record(p, cylinder()),
    )
    disk = face_from_boundaries(p, [outer])
    annulus = face_from_boundaries(
        p,
        [
            outer,
            (
                {"intersection": "tiny", "keep": "outside"},
                intersection_record(p, cylinder(1e-4)),
            ),
        ],
    )
    actual = region(arrange_faces(p, [], domains=[annulus]), {})
    assert actual["boundary_keys"] == []
    assert not same_region(disk, actual)
    assert not same_region(actual, disk)
    assert same_region(annulus, actual)


def test_region_equality_does_not_ignore_a_small_notch_with_same_loop_count():
    p = plane()
    disk = region(
        arrange_faces(p, [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    notched = region(
        arrange_faces(
            p,
            [cutter("outer", cylinder()), cutter("notch", plane((1, 0, 0), 2 - 1e-5))],
        ),
        {"outer": "negative", "notch": "negative"},
    )
    assert len(disk["loops"]) == len(notched["loops"]) == 1
    assert not same_region(disk, notched)
    assert not same_region(notched, disk)


def test_region_equality_does_not_ignore_moved_equal_area_holes():
    p = plane()
    regions = [
        face_from_boundaries(
            p,
            [
                (
                    {"intersection": "outer", "keep": "inside"},
                    intersection_record(p, cylinder()),
                ),
                (
                    {"intersection": "hole", "keep": "outside"},
                    intersection_record(p, cylinder(1e-4, (x, 0, 0))),
                ),
            ],
        )
        for x in (0, 1e-4)
    ]
    assert not same_region(*regions)
    assert not same_region(*regions[::-1])


def test_region_comparison_reuses_canonical_splits_not_pair_specific_splits(
    monkeypatch: MonkeyPatch,
):
    disk = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    half = region(
        arrange_faces(
            plane(),
            [cutter("outer", cylinder()), cutter("half", plane((1, 0, 0)))],
        ),
        {"outer": "negative", "half": "negative"},
    )
    original = vars(face_arrangement)["_split"]
    calls = 0

    def measured(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", measured)
    with native_replay_scope():
        assert not same_region(disk, half)
        assert same_region(disk, deepcopy(disk))
        assert same_region(half, deepcopy(half))
        assert not same_region(half, disk)
        assert calls == 2
    assert same_region(disk, deepcopy(disk))
    assert calls == 3


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_region_comparison_replays_different_carriers_at_small_and_large_scales(
    scale: float,
):
    cuts = [
        cutter("outer", cylinder(2 * scale)),
        cutter("clock", plane((1, 0, 0), scale)),
    ]
    first = region(
        arrange_faces(plane(), cuts), {"outer": "negative", "clock": "negative"}
    )
    second = region(
        arrange_faces(
            plane(),
            cuts,
            coverage=np.array([[-20, -10, 0], [30, 40, 0]], dtype=float) * scale,
        ),
        {"outer": "negative", "clock": "negative"},
    )
    assert (
        first["bounds"]["arrangement"]["carrier"]
        != second["bounds"]["arrangement"]["carrier"]
    )
    assert same_region(first, second)
    assert same_region(second, first)


def test_region_comparison_handles_huge_translation_without_world_round_trip():
    distance = 1e12
    cuts = [
        cutter("left", plane((1, 0, 0), distance - 4)),
        cutter("right", plane((1, 0, 0), distance + 4)),
        cutter("front", plane((0, 1, 0), distance - 3)),
        cutter("back", plane((0, 1, 0), distance + 3)),
    ]
    signs = {
        "left": "positive",
        "right": "negative",
        "front": "positive",
        "back": "negative",
    }
    first = region(arrange_faces(plane(), cuts), signs)
    second = region(
        arrange_faces(
            plane(),
            cuts,
            coverage=np.array(
                [[distance - 20, distance - 10, 0], [distance + 30, distance + 40, 0]]
            ),
        ),
        signs,
    )
    assert (
        first["bounds"]["arrangement"]["carrier"]
        != second["bounds"]["arrangement"]["carrier"]
    )
    assert same_region(first, second)
    assert same_region(second, first)


@pytest.mark.parametrize("invalid", ["witness", "components"])
def test_region_comparison_revalidates_selectors_after_cache_warmup(invalid: str):
    disk = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    changed = deepcopy(disk)
    selector = changed["bounds"]["arrangement"]["selector"]
    if invalid == "witness":
        selector["witness_chart"] = [100, 100]
        message = "witness crossed"
    else:
        selector["component_count"] += 1
        message = "components changed"
    with native_replay_scope():
        assert same_region(disk, deepcopy(disk))
        with pytest.raises(ValueError, match=message):
            _ = same_region(disk, changed)
        assert same_region(disk, deepcopy(disk))


def test_region_comparison_never_passes_cached_cells_to_boolean_operations(
    monkeypatch: MonkeyPatch,
):
    disk = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    half = region(
        arrange_faces(
            plane(),
            [cutter("outer", cylinder()), cutter("half", plane((1, 0, 0)))],
        ),
        {"outer": "negative", "half": "negative"},
    )
    intent = disk["bounds"]["arrangement"]
    selected = vars(face_arrangement)["_selected"]
    box = vars(face_arrangement)["_box"]
    original = vars(face_arrangement)["BRepAlgoAPI_Cut"]
    calls = 0
    with native_replay_scope():
        cell, center, scale = selected(intent, intent["selector"])
        native = cell["face"]
        comparison = vars(face_arrangement)["_comparison_domain"](disk, center, scale)
        assert not comparison.IsSame(native)
        before = tuple(bound.copy() for bound in box(native))
        before_edges = len(vars(face_arrangement)["_edges"](native))
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(native, properties)
        before_area = properties.Mass()

        def measured(left: Any, right: Any) -> Any:
            nonlocal calls
            calls += 1
            assert not left.IsSame(native)
            assert not right.IsSame(native)
            return original(left, right)

        monkeypatch.setattr(face_arrangement, "BRepAlgoAPI_Cut", measured)
        assert same_region(disk, deepcopy(disk))
        assert same_region(disk, deepcopy(disk))
        assert not same_region(disk, half)
        assert calls == 5
        after = box(native)
        for expected, actual in zip(before, after, strict=True):
            np.testing.assert_array_equal(expected, actual)
        BRepGProp.SurfaceProperties_s(native, properties)
        assert properties.Mass() == before_area
        assert len(vars(face_arrangement)["_edges"](native)) == before_edges
        assert BRepCheck_Analyzer(native).IsValid()
        assert selected(intent, intent["selector"])[0] is cell


@pytest.mark.parametrize("axis,distance", [((0, 0, 1), 1e12), ((0.6, 0, 0.8), 1e6)])
def test_region_comparison_still_recenters_manual_declarations(
    monkeypatch: MonkeyPatch,
    axis: tuple[float, float, float],
    distance: float,
):
    origin = (distance, -2 * distance, 3 * distance)
    p = plane(axis, float(np.dot(axis, origin)))
    outer = {**cylinder(2, origin), "axis": list(axis)}
    manual = face_from_boundaries(
        p,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                intersection_record(p, outer),
            )
        ],
    )
    arranged = region(arrange_faces(p, [cutter("outer", outer)]), {"outer": "negative"})
    original = vars(face_arrangement)["_local_domain"]
    localized: list[dict[str, Any]] = []

    def measured(record: dict[str, Any], center: Any, scale: float) -> Any:
        localized.append(record)
        assert "arrangement" not in record["bounds"]
        assert np.linalg.norm(center - origin) < 1
        return original(record, center, scale)

    monkeypatch.setattr(face_arrangement, "_local_domain", measured)
    assert same_region(manual, arranged)
    assert localized == [manual]


def test_comparison_domain_copies_oblique_declarations_at_huge_translation():
    # World-space export at this translation is outside the kernel's precision
    # for an oblique circle. The local comparison helper must not round-trip
    # through those world-space shapes before producing conditioned copies.
    axis = (0.6, 0, 0.8)
    origin = np.array([1e12, -2e12, 3e12])
    p = plane(axis)
    outer = {**cylinder(), "axis": list(axis)}
    manual = face_from_boundaries(
        p,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                intersection_record(p, outer),
            )
        ],
    )
    local_record = vars(face_arrangement)["_local_record"]
    shifted_manual = local_record(manual, -origin, 1)
    shifted_plane = plane(axis, float(np.dot(axis, origin)))
    shifted_outer = {**outer, "origin": origin.tolist()}
    arranged = region(
        arrange_faces(shifted_plane, [cutter("outer", shifted_outer)]),
        {"outer": "negative"},
    )
    comparison_domain = vars(face_arrangement)["_comparison_domain"]
    with native_replay_scope():
        faces = [
            comparison_domain(record, origin, 4)
            for record in (shifted_manual, arranged)
        ]
        for face in faces:
            assert BRepCheck_Analyzer(face).IsValid()
        for left, right in (faces, faces[::-1]):
            difference = vars(face_arrangement)["BRepAlgoAPI_Cut"](left, right)
            assert difference.IsDone()
            assert BRepCheck_Analyzer(difference.Shape()).IsValid()
            assert not vars(face_arrangement)["_shape_faces"](difference.Shape())


@pytest.mark.parametrize("slope", [-0.2, 0.2])
def test_cone_positive_sheet_oblique_bounded_band(slope: float):
    cone = {**cylinder(), "kind": "cone", "slope": slope}
    cuts = [
        cutter("lower", plane((0.1, 0, 1), 0)),
        cutter("upper", plane((-0.1, 0, 1), 3)),
        cutter("clock", plane((1, 0, 0), 0.5)),
    ]
    kept = region(
        arrange_faces(cone, cuts),
        {"lower": "positive", "upper": "negative", "clock": "negative"},
    )
    assert kept["bounded"]
    assert area(kept) > 0


def test_periodic_cells_never_double_count_interior_evidence():
    c = cylinder(4)
    points = np.array([[4, 0, 0], [0, 4, 0.5], [-4, 0, 1]], dtype=float)
    cuts = [cutter("upper", plane(offset=2)), cutter("clock", plane((1, 0, 0), 2))]
    records = arrange_faces(c, cuts, observations=points)
    multiplicity = np.sum(
        [record["evidence"]["interior"] for record in records], axis=0
    )
    assert multiplicity.tolist() == [1, 1, 1]


def test_disconnected_same_signature_uses_witness_not_native_index():
    p = plane()
    domains = [
        face_from_boundaries(
            p,
            [
                (
                    {"intersection": f"domain-{i}", "keep": "inside"},
                    intersection_record(p, cylinder(1, (x, 0, 0))),
                )
            ],
        )
        for i, x in enumerate((-3, 3))
    ]
    records = arrange_faces(p, [], domains=domains)
    assert len(records) == 2
    for record in records:
        assert record["region_identity"]["component_count"] == 2
        assert area(
            select_face(
                p, [], record["region_identity"], domains=list(reversed(domains))
            )
        ) == pytest.approx(math.pi)


def test_prepared_arrangement_splits_once_and_isolated_selection_errors(
    monkeypatch: MonkeyPatch,
):
    calls = 0
    original = vars(face_arrangement)["_split"]

    def count(intent: dict[str, Any]):
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", count)
    p = plane()
    cuts = [cutter("outer", cylinder()), cutter("clock", plane((1, 0, 0), 1))]
    prepared = prepare_faces(p, cuts)
    records = prepared.records()
    kept = region(records, {"outer": "negative", "clock": "negative"})
    selector = kept["region_identity"]
    for _ in range(3):
        assert prepared.select(selector)["region_identity"] == selector
    with pytest.raises(ValueError, match="witness crossed"):
        _ = prepared.select({**selector, "witness_chart": [100, 100]})
    assert prepared.select(selector)["bounded"]
    assert calls == 1
    cuts[0]["geometry"]["radius"] = 999
    kept["region_identity"]["signs"]["outer"] = "positive"
    assert (
        prepared.records()[0]["bounds"]["arrangement"]["cutters"][0]["geometry"][
            "radius"
        ]
        == 2
    )


def test_shared_cut_evidence_is_boundary_not_two_interiors():
    c = cylinder(4)
    points = np.array([[2, math.sqrt(12), 0.5], [4, 0, 0.5]], dtype=float)
    records = arrange_faces(
        c, [cutter("clock", plane((1, 0, 0), 2))], observations=points
    )
    interiors = np.sum([record["evidence"]["interior"] for record in records], axis=0)
    boundaries = np.sum([record["evidence"]["boundary"] for record in records], axis=0)
    assert interiors.tolist() == [0, 1]
    assert boundaries.tolist() == [2, 0]


def test_native_shape_list_preserves_values_and_history(monkeypatch: MonkeyPatch):
    first = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge()
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(1, 0, 0))
    located = first.Moved(TopLoc_Location(transform))
    face = checked_face(
        BRepBuilderAPI_MakeFace(primitive_surface(plane()), -1, 1, -1, 1, 1e-8)
    )
    expected = [first, first.Reversed(), first, located, face]
    shapes = List_TopoDS_Shape()
    for shape in expected:
        _ = shapes.Append(shape)

    def expensive_iterator(_self: Any) -> Any:
        pytest.fail("native history traversal must not construct OCP's list iterator")

    monkeypatch.setattr(List_TopoDS_Shape, "__iter__", expensive_iterator)
    converted = vars(face_arrangement)["_shape_list"](shapes)
    assert shapes.Extent() == len(expected)
    assert len(converted) == len(expected)
    assert all(
        actual.IsEqual(original)
        for actual, original in zip(converted, expected, strict=True)
    )
    assert vars(face_arrangement)["_shape_list"](List_TopoDS_Shape()) == []
    # Returned handles survive deletion of all list nodes, not just the copy.
    shapes.Clear()
    assert all(
        actual.IsEqual(original)
        for actual, original in zip(converted, expected, strict=True)
    )


def test_native_shape_list_returns_independent_shape_values():
    first = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge()
    shapes = List_TopoDS_Shape()
    _ = shapes.Append(first)
    converted = vars(face_arrangement)["_shape_list"](shapes)
    converted[0].Reverse()
    assert shapes.First().IsEqual(first)
    assert converted[0].IsSame(first) and not converted[0].IsEqual(first)


def test_cone_negative_sheet_creates_no_phantom_planar_cell():
    cone = {**cylinder(), "kind": "cone", "slope": 0.2}
    records = arrange_faces(plane(offset=-15), [cutter("cone", cone)])
    assert len(records) == 1
    assert records[0]["region_identity"]["signs"] == {"cone": "positive"}
    assert not records[0]["bounded"]


def test_periodic_native_classifier_cannot_select_opposite_cutter_side():
    c = cylinder(4)
    cuts = [cutter("clock", plane((1, 0, 0), 2))]
    records = arrange_faces(c, cuts)
    record = region(records, {"clock": "negative"})
    # With the intrinsic basis generated for the z-axis, angle pi/2 is x=+r.
    selector = {**record["region_identity"], "witness_chart": [math.pi / 2, 0]}
    with pytest.raises(ValueError, match="witness crossed"):
        _ = select_face(c, cuts, selector)


def test_arranged_face_can_be_a_physical_domain_for_another_arrangement():
    p = plane()
    original_cuts = [cutter("outer", cylinder()), cutter("clock", plane((1, 0, 0), 1))]
    domain = region(
        arrange_faces(p, original_cuts), {"outer": "negative", "clock": "negative"}
    )
    children = arrange_faces(p, [cutter("middle", plane((0, 1, 0)))], domains=[domain])
    assert len(children) == 2
    assert all(child["bounded"] for child in children)
    assert all(
        area(child) == pytest.approx(area(domain) / 2, rel=1e-8) for child in children
    )


def test_large_translated_domain_is_rebuilt_locally_before_clipping():
    p = plane()
    distance = 1e12
    planes = [
        plane((1, 0, 0), distance - 4),
        plane((1, 0, 0), distance + 4),
        plane((0, 1, 0), distance - 3),
        plane((0, 1, 0), distance + 3),
    ]
    domain = face_from_boundaries(
        p,
        [
            (
                {"intersection": f"edge-{i}", "keep": keep},
                intersection_record(p, cutter_plane),
            )
            for i, (keep, cutter_plane) in enumerate(
                zip(
                    ("positive", "negative", "positive", "negative"),
                    planes,
                    strict=True,
                )
            )
        ],
    )
    cuts = [cutter("middle", plane((1, 0, 0), distance))]
    records = arrange_faces(p, cuts, domains=[domain])
    assert len(records) == 2
    assert all(area(record) == pytest.approx(24, rel=1e-8) for record in records)


def open_domain(
    geometry: dict[str, Any], interval: list[float | None], kind: str
) -> dict[str, Any]:
    from experiments.surface_primitives import basis

    adapted = geometry.copy()
    adapted.setdefault(
        "origin", (np.asarray(geometry["axis"]) * geometry.get("offset", 0)).tolist()
    )
    u, v = basis(np.asarray(geometry["axis"], dtype=float))
    adapted.update(basis_u=u.tolist(), basis_v=v.tolist())
    return {
        "surface_kind": geometry["kind"],
        "geometry": adapted,
        "bounds": {kind: interval},
        "bounded": False,
    }


def test_open_cylinder_scope_preserves_missing_cap_until_new_cut_closes_it():
    c = cylinder()
    domain = open_domain(c, [None, 4], "axial")
    records = arrange_faces(c, [], domains=[domain])
    assert records and all(not record["bounded"] for record in records)
    cuts = [cutter("lower", plane(offset=0))]
    kept = region(arrange_faces(c, cuts, domains=[domain]), {"lower": "positive"})
    assert kept["bounded"]
    assert area(kept) == pytest.approx(16 * math.pi, rel=1e-8)
    assert any(
        edge["domain_sources"] for loop in kept["loops"] for edge in loop["edges"]
    )


def test_open_radial_exterior_scope_can_be_closed_by_an_outer_cutter():
    p = plane()
    domain = open_domain(p, [1, None], "radial")
    cuts = [cutter("outer", cylinder(3))]
    kept = region(arrange_faces(p, cuts, domains=[domain]), {"outer": "negative"})
    assert kept["bounded"]
    assert area(kept) == pytest.approx(8 * math.pi, rel=1e-8)
    outside = region(arrange_faces(p, cuts, domains=[domain]), {"outer": "positive"})
    assert not outside["bounded"]


def test_open_cone_scope_preserves_positive_sheet_and_closes_with_plane():
    cone = {**cylinder(), "kind": "cone", "slope": 0.2}
    domain = open_domain(cone, [None, 3], "axial")
    kept = region(
        arrange_faces(cone, [cutter("lower", plane(offset=0))], domains=[domain]),
        {"lower": "positive"},
    )
    assert kept["bounded"]
    assert area(kept) > 0


def test_general_unbounded_arrangement_scope_is_not_reinterpreted_as_a_cap():
    p = plane()
    outside = region(
        arrange_faces(p, [cutter("hole", cylinder())]), {"hole": "positive"}
    )
    with pytest.raises(ValueError, match="physical closing bounds"):
        _ = arrange_faces(p, [], domains=[outside])


def test_tiny_cells_mesh_at_their_own_scale_and_replay(monkeypatch: MonkeyPatch):
    measured: list[float] = []
    original = vars(face_arrangement)["tessellate_face"]

    def measure(face: Any, scale: float) -> dict[str, Any]:
        measured.append(scale)
        return original(face, scale)

    monkeypatch.setattr(face_arrangement, "tessellate_face", measure)
    extent = 4e-4
    cuts = [
        cutter("x", plane((1, 0, 0))),
        cutter("y", plane((0, 1, 0))),
        cutter("diagonal", plane((1, 1, 0), extent / math.sqrt(2))),
    ]
    records = arrange_faces(plane(), cuts, coverage=observations())
    kept = region(records, {"x": "positive", "y": "positive", "diagonal": "negative"})
    assert kept["bounded"] and kept["preview"]["indices"]
    assert min(measured) < 1e-4
    assert area(kept) == pytest.approx(extent**2 / 2, rel=1e-7)
    replayed = select_face(
        plane(), cuts, kept["region_identity"], coverage=observations()
    )
    assert replayed["region_identity"] == kept["region_identity"]


def test_native_uv_witness_does_not_require_an_inside_mesh_centroid(
    monkeypatch: MonkeyPatch,
):
    kept = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    native = face_from_record(kept)
    original = vars(face_arrangement)["tessellate_face"]

    def misplaced_candidates(face: Any, scale: float) -> dict[str, Any]:
        preview = original(face, scale)
        positions = np.asarray(preview["positions"]).reshape(-1, 3)
        preview["positions"] = (positions + np.array([100, 100, 0])).ravel().tolist()
        return preview

    monkeypatch.setattr(face_arrangement, "tessellate_face", misplaced_candidates)
    witness, _ = vars(face_arrangement)["_witness"](kept["geometry"], native)
    assert np.linalg.norm(witness[:2]) < 2
    assert witness[2] == pytest.approx(0)


def test_thin_cylindrical_cell_has_a_strict_native_chart_witness():
    geometry = cylinder(radius=0.01)
    lower, upper = 0.00479, 0.0048014
    native = checked_face(
        BRepBuilderAPI_MakeFace(
            primitive_surface(geometry),
            2.28,
            2 * math.pi,
            lower,
            upper,
            face_arrangement.TOLERANCE,
        )
    )
    witness = vars(face_arrangement)["_native_witness"](native)
    assert witness[2] == pytest.approx((lower + upper) / 2, abs=1e-14)
    assert np.linalg.norm(witness[:2]) == pytest.approx(geometry["radius"])
    inside = vars(face_arrangement)["_strictly_inside"]
    assert inside(native, witness)
    # A successful witness must not turn chart boundaries into interior points.
    surface = BRep_Tool.Surface_s(native)
    for u, v in ((4.0, lower), (4.0, upper), (1.0, (lower + upper) / 2)):
        assert not inside(native, np.asarray(surface.Value(u, v).Coord()))


def test_raw_cell_classification_does_not_require_a_display_mesh(
    monkeypatch: MonkeyPatch,
):
    kept = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    native = face_from_record(kept)

    def unavailable_preview(_face: Any, _scale: float) -> dict[str, Any]:
        raise ValueError("OCP face preview has no triangles")

    monkeypatch.setattr(face_arrangement, "tessellate_face", unavailable_preview)
    witness = vars(face_arrangement)["_native_witness"](native)
    assert np.isfinite(witness).all()
    assert np.linalg.norm(witness[:2]) < 2
    assert witness[2] == pytest.approx(0)
    # Native classification is not permission to publish an empty final preview.
    with pytest.raises(ValueError, match="preview has no triangles"):
        _ = vars(face_arrangement)["_witness"](kept["geometry"], native)


def test_raw_classification_precedes_final_region_meshing(monkeypatch: MonkeyPatch):
    classified: list[Any] = []
    original_witness = vars(face_arrangement)["_native_witness"]
    original_preview = vars(face_arrangement)["tessellate_face"]

    def classify(face: Any) -> Any:
        point = original_witness(face)
        classified.append(face)
        return point

    def preview(face: Any, scale: float) -> dict[str, Any]:
        assert classified, "raw fragments must be classified before display meshing"
        return original_preview(face, scale)

    monkeypatch.setattr(face_arrangement, "_native_witness", classify)
    monkeypatch.setattr(face_arrangement, "tessellate_face", preview)
    records = arrange_faces(plane(), [cutter("outer", cylinder())])
    assert records
    assert all(record["preview"]["indices"] for record in records)


def test_replay_scope_reuses_sibling_cells_but_revalidates_each_selector(
    monkeypatch: MonkeyPatch,
):
    records = arrange_faces(
        plane(),
        [cutter("outer", cylinder()), cutter("half", plane((1, 0, 0)))],
    )
    halves = [record for record in records if record["bounded"]]
    assert len(halves) == 2
    original = vars(face_arrangement)["_split"]
    calls = 0

    def measured(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", measured)
    with native_replay_scope():
        assert area(halves[0]) == pytest.approx(2 * math.pi)
        with native_replay_scope():
            assert area(halves[1]) == pytest.approx(2 * math.pi)
        invalid = deepcopy(halves[0])
        invalid["bounds"]["arrangement"]["selector"]["witness_chart"] = [100, 100]
        with pytest.raises(ValueError, match="witness crossed"):
            _ = face_from_record(invalid)
        assert area(halves[0]) == pytest.approx(2 * math.pi)
        assert calls == 1
    assert area(halves[0]) == pytest.approx(2 * math.pi)
    assert calls == 2


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_local_arranged_domains_reuse_source_frame_and_revalidate_selectors(
    monkeypatch: MonkeyPatch, kind: str
):
    if kind == "plane":
        surface = plane()
        cuts = [cutter("outer", cylinder()), cutter("hole", cylinder(0.2))]
        signs = {"outer": "negative", "hole": "positive"}
    else:
        surface = {**cylinder(), "kind": kind, "slope": 0.2 if kind == "cone" else 0}
        cuts = [cutter("bottom", plane()), cutter("top", plane(offset=3))]
        signs = {"bottom": "positive", "top": "negative"}
    record = region(arrange_faces(surface, cuts), signs)
    expected_area = area(record)
    before = deepcopy(record)
    original = vars(face_arrangement)["_split"]
    calls = 0

    def measured(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", measured)
    local_domain = vars(face_arrangement)["_local_domain"]
    with native_replay_scope():
        for center, scale in (
            (np.zeros(3), 1.0),
            (np.array([1000, -400, 23]), 0.1),
            (np.array([-200, 17, 50]), 1e4),
        ):
            first = local_domain(record, center, scale)
            second = local_domain(record, center, scale)
            assert not first.IsSame(second)
            assert BRepCheck_Analyzer(first).IsValid()
            properties = GProp_GProps()
            BRepGProp.SurfaceProperties_s(first, properties)
            assert properties.Mass() * scale**2 == pytest.approx(
                expected_area, rel=1e-8
            )
        assert calls == 1
        invalid = deepcopy(record)
        invalid["bounds"]["arrangement"]["selector"]["witness_chart"] = [100, 100]
        with pytest.raises(ValueError, match="witness crossed"):
            _ = local_domain(invalid, np.zeros(3), 1.0)
        changed_components = deepcopy(record)
        changed_components["bounds"]["arrangement"]["selector"]["component_count"] += 1
        with pytest.raises(ValueError, match="component"):
            _ = local_domain(changed_components, np.zeros(3), 1.0)
        open_record = deepcopy(record)
        open_record["bounded"] = False
        with pytest.raises(ValueError, match="display-envelope boundaries"):
            _ = local_domain(open_record, np.zeros(3), 1.0)
        assert calls == 1
    assert record == before
    _ = local_domain(record, np.zeros(3), 1.0)
    assert calls == 2


def test_replay_scope_reuses_prepared_arrangements_without_exposing_mutable_records(
    monkeypatch: MonkeyPatch,
):
    original = vars(face_arrangement)["_split"]
    calls = 0

    def measured(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return original(intent)

    monkeypatch.setattr(face_arrangement, "_split", measured)
    with native_replay_scope():
        first = prepare_faces(plane(), [cutter("outer", cylinder())])
        expected = first.records()
        mutated = first.records()
        mutated[0]["loops"].clear()
        mutated[0]["region_identity"]["signs"].clear()
        second = prepare_faces(plane(), [cutter("outer", cylinder())])
        assert second.records() == expected
        assert calls == 1


def test_local_arranged_domain_identity_copy_isolates_native_tolerances():
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    record = region(
        arrange_faces(plane(), [cutter("outer", cylinder())]), {"outer": "negative"}
    )
    intent = record["bounds"]["arrangement"]
    selected = vars(face_arrangement)["_selected"]
    local_domain = vars(face_arrangement)["_local_domain"]

    def vertices(face: Any) -> list[Any]:
        result: list[Any] = []
        explorer = TopExp_Explorer(face, TopAbs_VERTEX)
        while explorer.More():
            result.append(TopoDS.Vertex(explorer.Current()))
            explorer.Next()
        return result

    with native_replay_scope():
        cell, center, scale = selected(intent, intent["selector"])
        source = cell["face"]
        source_edges = vars(face_arrangement)["_edges"](source)
        source_vertices = vertices(source)
        originals = [source, *source_edges, *source_vertices]
        tolerances = [BRep_Tool.Tolerance_s(shape) for shape in originals]
        copied = local_domain(record, center, scale)
        copied_edges = vars(face_arrangement)["_edges"](copied)
        copied_vertices = vertices(copied)
        assert not copied.IsSame(source)
        assert len(copied_edges) == len(source_edges)
        assert len(copied_vertices) == len(source_vertices)
        assert all(
            not edge.IsSame(original)
            for edge in copied_edges
            for original in source_edges
        )
        assert all(
            not vertex.IsSame(original)
            for vertex in copied_vertices
            for original in source_vertices
        )
        builder = BRep_Builder()
        builder.UpdateFace(copied, 1e-3)
        for edge in copied_edges:
            builder.UpdateEdge(edge, 1e-3)
        for vertex in copied_vertices:
            builder.UpdateVertex(vertex, 1e-3)
        assert all(
            BRep_Tool.Tolerance_s(shape) >= 1e-3
            for shape in [copied, *copied_edges, *copied_vertices]
        )
        assert [BRep_Tool.Tolerance_s(shape) for shape in originals] == tolerances
        assert BRepCheck_Analyzer(source).IsValid()
        assert selected(intent, intent["selector"])[0] is cell


@pytest.mark.parametrize(
    "changed",
    ["surface", "cutter", "carrier", "domain", "finite_domain", "observations"],
)
def test_replay_scope_keys_all_geometry_and_evidence_inputs(
    monkeypatch: MonkeyPatch,
    changed: str,
):
    intent: dict[str, Any] = {
        "surface": {"offset": 0},
        "cutters": [{"geometry": {"offset": 0}, "face_domains": [{"selector": 0}]}],
        "domains": [{"selector": 0}],
        "carrier": {"scale": 1},
        "observations": [[0, 0, 0]],
        "selector": {"witness_chart": [0, 0]},
    }
    changed_intent = deepcopy(intent)
    match changed:
        case "surface":
            changed_intent["surface"]["offset"] = 1
        case "cutter":
            changed_intent["cutters"][0]["geometry"]["offset"] = 1
        case "carrier":
            changed_intent["carrier"]["scale"] = 2
        case "domain":
            changed_intent["domains"][0]["selector"] = 1
        case "finite_domain":
            changed_intent["cutters"][0]["face_domains"][0]["selector"] = 1
        case "observations":
            changed_intent["observations"] = [[1, 0, 0]]
        case _:
            raise AssertionError(f"unknown input variation: {changed}")
    calls = 0

    def measured(_intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return [], np.zeros(3), 1.0, {}

    monkeypatch.setattr(face_arrangement, "_split", measured)
    replay = vars(face_arrangement)["_replay_split"]
    with native_replay_scope():
        _ = replay(intent)
        other_selector = deepcopy(intent)
        other_selector["selector"] = {"witness_chart": [1, 1]}
        _ = replay(other_selector)
        assert calls == 1
        _ = replay(changed_intent)
        assert calls == 2
        _ = replay(intent)
        assert calls == 2


def test_replay_scope_does_not_cache_failed_builds_or_leak_after_exceptions(
    monkeypatch: MonkeyPatch,
):
    calls = 0

    def measured(_intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("native build failed")
        return [], np.zeros(3), 1.0, {}

    monkeypatch.setattr(face_arrangement, "_split", measured)
    replay = vars(face_arrangement)["_replay_split"]
    with pytest.raises(RuntimeError, match="abort pass"), native_replay_scope():
        with pytest.raises(ValueError, match="native build failed"):
            _ = replay({})
        _ = replay({})
        _ = replay({})
        assert calls == 2
        raise RuntimeError("abort pass")
    with native_replay_scope():
        _ = replay({})
        assert calls == 3


def test_replay_scopes_are_independent_across_threads(monkeypatch: MonkeyPatch):
    calls: list[int] = []
    rendezvous = Barrier(2)

    def measured(_intent: dict[str, Any]) -> Any:
        identity = get_ident()
        calls.append(identity)
        return [], np.zeros(3), 1.0, {"thread": identity}

    monkeypatch.setattr(face_arrangement, "_split", measured)
    replay = vars(face_arrangement)["_replay_split"]

    def run() -> int:
        with native_replay_scope():
            _ = rendezvous.wait(timeout=5)
            first = replay({})
            _ = rendezvous.wait(timeout=5)
            assert replay({}) is first
            assert first[3]["thread"] == get_ident()
            return get_ident()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        identities = [future.result(timeout=10) for future in futures]
    assert sorted(calls) == sorted(identities)


def test_copied_context_cannot_reuse_native_shapes_on_another_thread(
    monkeypatch: MonkeyPatch,
):
    calls: list[int] = []

    def measured(_intent: dict[str, Any]) -> Any:
        identity = get_ident()
        calls.append(identity)
        return [], np.zeros(3), 1.0, {"thread": identity}

    monkeypatch.setattr(face_arrangement, "_split", measured)
    replay = vars(face_arrangement)["_replay_split"]

    def run() -> int:
        with native_replay_scope():
            first = replay({})
            assert replay({}) is first
            assert first[3]["thread"] == get_ident()
            return get_ident()

    with native_replay_scope(), ThreadPoolExecutor(max_workers=1) as executor:
        parent = replay({})
        inherited = copy_context()
        child_identity = executor.submit(inherited.run, run).result(timeout=10)
        assert replay({}) is parent
    assert calls == [get_ident(), child_identity]
    assert get_ident() != child_identity


def test_copied_context_cannot_reuse_native_shapes_after_origin_scope_disposal(
    monkeypatch: MonkeyPatch,
):
    calls = 0

    def measured(_intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return [], np.zeros(3), 1.0, {}

    monkeypatch.setattr(face_arrangement, "_split", measured)
    replay = vars(face_arrangement)["_replay_split"]
    with native_replay_scope():
        _ = replay({})
        inherited = copy_context()

    def later() -> None:
        with native_replay_scope():
            _ = replay({})
            _ = replay({})

    inherited.run(later)
    assert calls == 2
