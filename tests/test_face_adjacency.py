"""Negative adjacency proofs never rely on near parallelism or observations."""

from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from experiments.face_adjacency import circle_in_face_domains, classify_pair


def plane(offset: float = 0, axis: list[float] | None = None) -> dict[str, Any]:
    return {"kind": "plane", "axis": axis or [0, 0, 1], "offset": offset}


def side(radius: float = 4, x: float = 0, slope: float = 0) -> dict[str, Any]:
    return {
        "kind": "cone" if slope else "cylinder",
        "axis": [0, 0, 1],
        "origin": [x, 0, 0],
        "radius": radius,
        "slope": slope,
    }


@pytest.mark.parametrize(
    "first,second,status,category",
    [
        (plane(0), plane(2), "proven_empty", "parallel_planes"),
        (plane(2), plane(-2, [0, 0, -1]), "uncertain", "coincident_or_near_planes"),
        (plane(0), plane(0, [1, 0, 0]), "candidate", "plane_line"),
        (plane(0), side(), "candidate", "circle"),
        (plane(5, [1, 0, 0]), side(), "proven_empty", "plane_outside_cylinder"),
        (plane(4, [1, 0, 0]), side(), "uncertain", "tangent_generators"),
        (plane(4 + 1e-12, [1, 0, 0]), side(), "uncertain", "tangent_generators"),
        (plane(3, [1, 0, 0]), side(), "candidate", "generator_lines"),
        (plane(9.5, [1, 0, 0]), side(5), "proven_empty", "plane_outside_cylinder"),
        (plane(9.5, [1, 0, 0]), side(11), "candidate", "generator_lines"),
        (side(4), side(4, 9), "proven_empty", "disjoint_parallel_cylinders"),
        (side(4), side(1, 1), "proven_empty", "disjoint_parallel_cylinders"),
        (side(4), side(4), "uncertain", "coincident_cylinders"),
        (side(4), side(4, 8), "uncertain", "tangent_cylinders"),
        (side(4), side(1, 3), "uncertain", "tangent_cylinders"),
        (side(4), side(1, 4), "candidate", "generator_lines"),
        (
            side(4, slope=1),
            side(5, slope=1),
            "proven_empty",
            "distinct_coaxial_profiles",
        ),
        (side(4, slope=1), side(4, slope=1), "uncertain", "coincident_profiles"),
        (side(4, slope=1), side(5), "candidate", "circle"),
        (plane(-5), side(4, slope=1), "proven_empty", "outside_positive_cone"),
        (plane(-4), side(4, slope=1), "uncertain", "cone_apex"),
        (plane(2, [1, 0, 0]), side(4, slope=1), "uncertain", "general_conic"),
    ],
)
def test_primitive_pair_classification(
    first: dict[str, Any], second: dict[str, Any], status: str, category: str
) -> None:
    original = deepcopy((first, second))
    result = classify_pair(first, second)
    assert result == classify_pair(second, first)
    assert result["status"] == status
    assert result["category"] == category
    assert result["reason"]
    assert (first, second) == original


def test_nearly_parallel_cylinder_axes_cannot_prove_global_disjointness() -> None:
    tilted = side(4, 100)
    tilted["axis"] = [1e-12, 0, 1]
    assert classify_pair(side(), tilted)["status"] == "uncertain"
    assert classify_pair(plane(5, [1, 0, 0]), tilted)["status"] == "candidate"


def test_nearly_parallel_planes_still_intersect() -> None:
    tilted = plane(20, [1e-12, 0, 1])
    assert classify_pair(plane(), tilted)["category"] == "plane_line"


def test_noncoaxial_nearby_profiles_cannot_be_declared_disjoint() -> None:
    result = classify_pair(side(4, slope=1), side(5, 1e-12, slope=1))
    assert result["status"] == "uncertain"


def test_opposite_axis_coaxial_profiles_preserve_classification() -> None:
    first = side(4, slope=1)
    second = side(5, slope=1)
    reversed_side = deepcopy(second)
    reversed_side.update(axis=[0, 0, -1], slope=-1)
    assert classify_pair(first, reversed_side) == classify_pair(first, second)


@pytest.mark.parametrize("reference_radius", [-1, 0])
def test_cone_chart_reference_need_not_have_positive_radius(
    reference_radius: float,
) -> None:
    cone = side(reference_radius, slope=1)
    assert classify_pair(plane(2), cone)["category"] == "circle"
    assert classify_pair(plane(-2), cone)["status"] == "proven_empty"
    assert classify_pair(cone, side(1))["category"] == "circle"


def test_coaxial_profile_crossing_on_negative_sheet_is_empty() -> None:
    assert (
        classify_pair(side(4, slope=1), side(6, slope=1.2))["status"] == "proven_empty"
    )


def test_cone_radius_rechart_accounts_for_axial_origin_translation() -> None:
    source = side(4, slope=1)
    moved_chart = side(5, slope=1)
    moved_chart["origin"] = [0, 0, 1]
    assert classify_pair(source, moved_chart)["category"] == "coincident_profiles"


def test_zero_taper_nonpositive_cone_has_no_positive_sheet() -> None:
    empty = side(-1)
    empty["kind"] = "cone"
    assert classify_pair(empty, plane())["status"] == "proven_empty"
    assert classify_pair(empty, side())["status"] == "proven_empty"


def test_far_translation_preserves_well_separated_plane_cylinder_proof() -> None:
    first, second = plane(9.5, [1, 0, 0]), side(5)
    shift = np.array([1e6, -1e6, 2e6])
    first["offset"] += float(np.asarray(first["axis"]) @ shift)
    second["origin"] = (np.asarray(second["origin"]) + shift).tolist()
    assert classify_pair(first, second)["status"] == "proven_empty"


def test_unsupported_primitive_is_unknown_and_invalid_axis_rejected() -> None:
    assert classify_pair({"kind": "sphere"}, plane())["status"] == "uncertain"
    invalid = side()
    invalid["axis"] = [0, 0, 2]
    with pytest.raises(ValueError, match="unit"):
        _ = classify_pair(invalid, plane())


def circle(radius: float = 2, x: float = 0, z: float = 0) -> dict[str, Any]:
    return {
        "kind": "circle",
        "center_display": [x, 0, z],
        "axis_display": [0, 0, 1],
        "radius": radius,
    }


def radial(lo: float | None, hi: float | None) -> dict[str, Any]:
    return {
        "surface_kind": "plane",
        "geometry": {"origin": [0, 0, 0], "axis": [0, 0, 1]},
        "bounds": {"radial": [lo, hi]},
    }


def axial(lo: float | None, hi: float | None) -> dict[str, Any]:
    return {
        "surface_kind": "cylinder",
        "geometry": {"origin": [0, 0, 0], "axis": [0, 0, 1], "radius": 2, "slope": 0},
        "bounds": {"axial": [lo, hi]},
    }


@pytest.mark.parametrize(
    "edge,domains,expected",
    [
        (circle(), [radial(3, 4)], False),
        (circle(), [radial(1, 3)], True),
        (circle(), [radial(2, 3)], True),
        (circle(), [radial(3, None)], False),
        (circle(), [radial(None, 1)], False),
        (circle(), [radial(3, 4), radial(1, 2)], True),
        (circle(), [], None),
        (circle(x=5), [radial(1, 2)], False),
        (circle(x=3), [radial(1, 2)], True),
        (circle(z=2), [axial(3, 4)], False),
        (circle(z=2), [axial(None, 1)], False),
        (circle(z=2), [axial(2, None)], True),
        (circle(z=2), [axial(0, 3)], True),
        (circle(z=2), [axial(0, 1), axial(3, None)], False),
        (circle(z=2), [axial(0, 1), {"surface_kind": "sphere"}], None),
        (circle(z=2), [radial(0, 4)], None),
        (circle(3, z=2), [axial(0, 4)], None),
        ({"kind": "line"}, [radial(0, 4)], None),
    ],
)
def test_closed_physical_domain_union(
    edge: dict[str, Any], domains: list[dict[str, Any]], expected: bool | None
) -> None:
    original = deepcopy((edge, domains))
    assert circle_in_face_domains(edge, domains) is expected
    assert (edge, domains) == original


def test_near_boundary_is_not_proven_outside() -> None:
    assert circle_in_face_domains(circle(2 + 1e-12), [radial(0, 2)]) is None


@pytest.mark.parametrize("planar", [True, False])
def test_translated_domain_exclusion_accounts_for_coordinate_precision(
    planar: bool,
) -> None:
    shift = np.array([1e12, -1e12, 1e12])
    edge = circle(2 + 1e-5) if planar else circle(z=2 + 1e-5)
    domain = radial(0, 2) if planar else axial(0, 2)
    edge["center_display"] = (np.asarray(edge["center_display"]) + shift).tolist()
    domain["geometry"]["origin"] = shift.tolist()
    assert circle_in_face_domains(edge, [domain]) is not False


def test_nearly_parallel_circle_and_domain_cannot_prove_exclusion() -> None:
    edge = circle(z=100)
    edge["axis_display"] = [1e-12, 0, 1]
    assert circle_in_face_domains(edge, [axial(0, 2)]) is None


def test_unrepresentable_radial_range_is_unknown_not_excluded() -> None:
    edge = circle(x=1e200)
    with np.errstate(over="ignore"):
        assert circle_in_face_domains(edge, [radial(0, 2)]) is None


def test_observation_and_solver_support_fields_are_not_physical_bounds() -> None:
    domain = axial(None, None)
    domain.update(axial_domain=[0, 1], ids=[1, 2], bounded=False, preview_clipped=True)
    assert circle_in_face_domains(circle(z=100), [domain]) is True
