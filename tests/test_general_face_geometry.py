"""General reviewed topology is exact, finite, and independent of scan bounds."""

import json
import math
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from experiments.general_face_geometry import (
    classify_face_points,
    classify_loop_pair,
    face_from_boundaries,
    face_from_record,
    generator_intersection_preview,
    intersection_record,
)
from experiments.ocp_geometry import circular_intersection


def plane(
    axis: tuple[float, float, float] = (0, 0, 1), offset: float = 0
) -> dict[str, Any]:
    vector = np.asarray(axis, dtype=float)
    vector /= np.linalg.norm(vector)
    return {"kind": "plane", "axis": vector.tolist(), "offset": offset}


def cylinder(
    radius: float = 1, origin: tuple[float, float, float] = (0, 0, 0)
) -> dict[str, Any]:
    return {
        "kind": "cylinder",
        "origin": list(origin),
        "axis": [0, 0, 1],
        "radius": radius,
        "slope": 0,
    }


def use(keep: str, number: int) -> dict[str, Any]:
    return {"keep": keep, "intersection": f"edge-{number}"}


def area(record: dict[str, Any]) -> float:
    face = face_from_record(json.loads(json.dumps(record)))
    assert BRepCheck_Analyzer(face).IsValid()
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    return properties.Mass()


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
@pytest.mark.parametrize("rotated", [False, True])
def test_generator_previews_keep_both_native_lines_without_authoring_edges(
    scale: float, rotated: bool
) -> None:
    side = cylinder(2 * scale, (12 * scale, -8 * scale, 4 * scale))
    normal = np.array([1.0, 0, 0])
    if rotated:
        axis = np.array([1.0, 2, 3])
        axis /= np.linalg.norm(axis)
        side["axis"] = axis.tolist()
        normal = np.cross(axis, [2.0, 7, 4])
        normal /= np.linalg.norm(normal)
    p = plane(tuple(normal), float(normal @ side["origin"]) + scale)
    for first, second in ((p, side), (side, p)):
        record = generator_intersection_preview(first, second)
        assert record["kind"] == "intersection_preview"
        assert record["preview_only"] and record["preview_clipped"]
        assert len(record["curves"]) == 2
        radial_origins: list[NDArray[np.float64]] = []
        axis = np.asarray(side["axis"])
        for branch in record["curves"]:
            assert branch["kind"] == "line" and not branch["closed"]
            points = np.array(branch["preview"]["positions"], dtype=np.float64).reshape(
                -1, 3
            )
            assert points @ normal == pytest.approx(p["offset"], abs=1e-7 * scale)
            relative = points - side["origin"]
            radial = relative - np.outer(relative @ axis, axis)
            assert np.linalg.norm(radial, axis=1) == pytest.approx(2 * scale)
            assert abs(np.dot(branch["direction_display"], axis)) == pytest.approx(1)
            radial_origins.append(radial[0])
        assert np.linalg.norm(radial_origins[0] - radial_origins[1]) == pytest.approx(
            2 * np.sqrt(3) * scale
        )
        with pytest.raises(ValueError, match="branch"):
            _ = intersection_record(first, second)


def test_generator_previews_at_large_world_translation() -> None:
    side = cylinder(10, (1e9, -2e9, 3e9))
    record = generator_intersection_preview(plane((1, 0, 0), 1e9 + 5), side)
    for curve in record["curves"]:
        points = np.array(curve["preview"]["positions"]).reshape(-1, 3)
        assert points[:, 0] == pytest.approx(1e9 + 5, abs=1e-6, rel=0)
        assert np.linalg.norm(points[:, :2] - [1e9, -2e9], axis=1) == pytest.approx(10)


@pytest.mark.parametrize("offset", [2, 2 - 1e-11, 3])
def test_generator_previews_do_not_promote_tangencies_or_outside_planes(
    offset: float,
) -> None:
    with pytest.raises(ValueError, match="unambiguous secant"):
        _ = generator_intersection_preview(plane((1, 0, 0), offset), cylinder(2))


def test_generator_preview_scope_does_not_snap_oblique_or_closed_cuts_to_lines() -> (
    None
):
    for p in (plane((0, 0, 1)), plane((1, 0, 0.01))):
        with pytest.raises(ValueError, match="pair of native generator lines"):
            _ = generator_intersection_preview(p, cylinder())
    with pytest.raises(ValueError, match="plane and cylinder"):
        _ = generator_intersection_preview(plane(), plane((1, 0, 0)))


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_separate_holes_and_serialized_exact_geometry(scale: float):
    p = plane()
    outer = intersection_record(p, cylinder(10 * scale))
    holes = [
        intersection_record(p, cylinder(scale, (x * scale, 0, 0))) for x in (-3, 3)
    ]
    record = face_from_boundaries(
        p,
        [
            (use("inside", 0), outer),
            *[(use("outside", i + 1), h) for i, h in enumerate(holes)],
        ],
        np.array([[999, 999, 999]]),
    )
    assert area(record) == pytest.approx(98 * math.pi * scale**2, rel=1e-8)
    interior, boundary = classify_face_points(
        record, np.array([[0, 0, 0], [3 * scale, 0, 0], [10 * scale, 0, 0]])
    )
    assert interior.tolist() == [True, False, False]
    assert boundary.tolist() == [False, False, True]
    assert classify_loop_pair(outer, holes[0], p) == "first_contains_second"
    assert classify_loop_pair(holes[0], outer, p) == "second_contains_first"
    assert classify_loop_pair(holes[0], holes[1], p) == "disjoint"


def test_tiny_loops_far_from_origin():
    p = plane()
    c = cylinder(3e-6, (1e6, 0, 0))
    record = face_from_boundaries(p, [(use("inside", 0), intersection_record(p, c))])
    assert area(record) == pytest.approx(math.pi * c["radius"] ** 2, rel=1e-4)


def test_oblique_ellipse_disk():
    p = plane((0.4, 0, 1))
    edge = intersection_record(p, cylinder(2))
    descriptor = edge["curves"][0]
    assert descriptor["kind"] == "ellipse"
    record = face_from_boundaries(p, [(use("inside", 0), edge)])
    assert area(record) == pytest.approx(4 * math.pi * math.sqrt(1.16), rel=1e-8)


@pytest.mark.parametrize("offset", [3, 4, 5, 12])
def test_touching_crossing_or_escaping_holes_rejected(offset: float):
    p = plane()
    outer = intersection_record(p, cylinder(5))
    hole = intersection_record(p, cylinder(2, (offset, 0, 0)))
    with pytest.raises(ValueError, match=r"inside|intersect|touch"):
        _ = face_from_boundaries(
            p, [(use("inside", 0), outer), (use("outside", 1), hole)]
        )


def test_nested_holes_rejected():
    p = plane()
    edges = [intersection_record(p, cylinder(r)) for r in (10, 4, 1)]
    with pytest.raises(ValueError, match="nested"):
        _ = face_from_boundaries(
            p,
            [
                (use(k, i), e)
                for i, (k, e) in enumerate(
                    zip(("inside", "outside", "outside"), edges, strict=True)
                )
            ],
        )


def rectangle_boundaries(
    p: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    planes = [
        plane((1, 0, 0), -3),
        plane((1, 0, 0), 3),
        plane((0, 1, 0), -2),
        plane((0, 1, 0), 2),
    ]
    return [
        (use(k, i), intersection_record(p, cutter))
        for i, (k, cutter) in enumerate(
            zip(("positive", "negative", "positive", "negative"), planes, strict=True)
        )
    ]


def test_line_polygon_with_hole():
    p = plane()
    edges = rectangle_boundaries(p)
    edges.append((use("outside", 4), intersection_record(p, cylinder(1))))
    record = face_from_boundaries(p, edges)
    assert area(record) == pytest.approx(24 - math.pi, rel=1e-8)
    assert record["bounds"]["loops"][0]["kind"] == "polygon"


def test_incomplete_polygon_and_exterior_diagnostic():
    p = plane()
    with pytest.raises(ValueError, match=r"unbounded|closed"):
        _ = face_from_boundaries(p, rectangle_boundaries(p)[:3])
    with pytest.raises(ValueError, match="outer loop"):
        _ = face_from_boundaries(
            p, [(use("outside", 0), intersection_record(p, cylinder()))]
        )


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_oblique_cylindrical_band_valid_periodic_seams(scale: float):
    c = cylinder(2 * scale)
    cuts = [plane((0.4, 0, 1), 4 * scale), plane((-0.2, 0, 1), 0)]
    edges = [intersection_record(c, p) for p in cuts]
    record = face_from_boundaries(
        c, [(use("negative", 0), edges[0]), (use("positive", 1), edges[1])]
    )
    assert record["bounded"] and not record["preview_clipped"]
    assert area(record) > 0
    assert "cuts" in record["bounds"]


def test_same_direction_cuts_never_export_artificial_carrier_cap():
    c = cylinder()
    edges = [intersection_record(c, plane(offset=z)) for z in (0, 4)]
    with pytest.raises(ValueError, match="unbounded"):
        _ = face_from_boundaries(
            c, [(use("positive", i), e) for i, e in enumerate(edges)]
        )


def test_coincident_and_open_multibranch_intersections_not_empty_proof():
    with pytest.raises(ValueError, match="zero or multiple"):
        _ = intersection_record(plane(), plane())
    with pytest.raises(ValueError, match="zero or multiple"):
        _ = intersection_record(cylinder(), plane((1, 0, 0)))


def test_wrong_plane_boundary_rejected():
    edge = intersection_record(plane(offset=1), cylinder())
    with pytest.raises(ValueError, match="face plane"):
        _ = face_from_boundaries(plane(), [(use("inside", 0), edge)])


def test_loop_contact_is_ambiguous_without_sampled_proof():
    p = plane()
    first = intersection_record(p, cylinder(2))
    second = intersection_record(p, cylinder(1, (3, 0, 0)))
    assert classify_loop_pair(first, second, p) == "ambiguous"


def test_outer_conic_accepts_only_strictly_redundant_lines():
    p = plane()
    outer = intersection_record(p, cylinder())
    line = intersection_record(p, plane((1, 0, 0), 2))
    record = face_from_boundaries(
        p, [(use("inside", 0), outer), (use("negative", 1), line)]
    )
    assert area(record) == pytest.approx(math.pi)
    crossing = intersection_record(p, plane((1, 0, 0), 0.5))
    with pytest.raises(ValueError, match="arrangement"):
        _ = face_from_boundaries(
            p, [(use("inside", 0), outer), (use("negative", 1), crossing)]
        )


def test_oblique_band_accepts_legacy_circle_boundary():
    c = cylinder()
    lower = circular_intersection(plane(), c, 96)
    upper = intersection_record(plane((0.2, 0, 1), 4), c)
    record = face_from_boundaries(
        c, [(use("positive", 0), lower), (use("negative", 1), upper)]
    )
    assert area(record) > 0


def test_circle_winding_does_not_select_the_wrong_planar_region():
    p = plane((0, 0, -1))
    edge = intersection_record(p, cylinder())
    record = face_from_boundaries(p, [(use("inside", 0), edge)])
    assert area(record) == pytest.approx(math.pi)


def test_oblique_cone_intersection_can_bound_a_plane():
    cone = {**cylinder(2), "kind": "cone", "slope": 0.2}
    p = plane((0.1, 0, 1), 3)
    edge = intersection_record(p, cone)
    assert edge["curves"][0]["kind"] == "ellipse"
    record = face_from_boundaries(p, [(use("inside", 0), edge)])
    assert area(record) > 0


def test_small_polygon_at_large_tangential_translation():
    p = plane()
    distance = 1e12
    cutters = [
        plane((1, 0, 0), distance - 4),
        plane((1, 0, 0), distance + 4),
        plane((0, 1, 0), distance - 3),
        plane((0, 1, 0), distance + 3),
    ]
    edges = [
        (use(k, i), intersection_record(p, c))
        for i, (k, c) in enumerate(
            zip(("positive", "negative", "positive", "negative"), cutters, strict=True)
        )
    ]
    record = face_from_boundaries(p, edges)
    assert area(record) == pytest.approx(48, rel=1e-8)


def test_reversed_cut_normal_with_reversed_choice_retains_same_band():
    c = cylinder()
    lower = intersection_record(c, plane())
    upper = intersection_record(c, plane((0.2, 0, 1), 4))
    flipped_upper = intersection_record(c, plane((-0.2, 0, -1), -4))
    normal_record = face_from_boundaries(
        c, [(use("positive", 0), lower), (use("negative", 1), upper)]
    )
    flipped_record = face_from_boundaries(
        c, [(use("positive", 0), lower), (use("positive", 1), flipped_upper)]
    )
    assert area(normal_record) == pytest.approx(area(flipped_record), rel=1e-8)


@pytest.mark.parametrize("slope", [-0.2, 0.2])
def test_oblique_cone_negative_sheet_cannot_author_a_plane_cap(slope: float):
    cone = {**cylinder(2), "kind": "cone", "slope": slope}
    p = plane((0.1, 0, 1), -15 if slope > 0 else 15)
    with pytest.raises(ValueError, match="negative-radius sheet"):
        _ = intersection_record(p, cone)


def test_ordered_loop_roles_and_sources_preserve_input_edges():
    p = plane()
    polygon_edges = rectangle_boundaries(p)
    hole = intersection_record(p, cylinder())
    original = json.dumps(hole)
    record = face_from_boundaries(p, [*polygon_edges, (use("outside", 4), hole)])
    loops = record["bounds"]["loops"]
    assert loops[0]["role"] == "outer"
    assert loops[0]["sources"] == ["edge-0", "edge-1", "edge-2", "edge-3"]
    assert loops[1]["role"] == "hole"
    assert loops[1]["sources"] == ["edge-4"]
    assert json.dumps(hole) == original


def test_empty_evidence_masks_keep_boolean_dtype():
    p = plane()
    record = face_from_boundaries(
        p, [(use("inside", 0), intersection_record(p, cylinder()))]
    )
    interior, boundary = classify_face_points(record, np.empty((0, 3)))
    assert interior.dtype == boundary.dtype == np.dtype(bool)
    assert len(interior) == len(boundary) == 0


def test_tangent_generator_is_not_a_planar_halfspace_boundary():
    with pytest.raises(ValueError, match="branch"):
        _ = intersection_record(plane((1, 0, 0), 1), cylinder())


def test_wrong_radius_shared_conic_is_not_replaced_by_a_new_cut():
    correct = intersection_record(plane((0.1, 0, 1), 2), cylinder())
    wrong = intersection_record(plane((-0.1, 0, 1), 4), cylinder(2))
    with pytest.raises(ValueError, match="does not lie"):
        _ = face_from_boundaries(
            cylinder(), [(use("positive", 0), correct), (use("negative", 1), wrong)]
        )
