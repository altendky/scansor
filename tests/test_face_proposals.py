"""Batch proposals preserve evidence and require review of uncertain topology."""

from copy import deepcopy
from itertools import permutations
from typing import Any

import numpy as np
import pytest

from experiments.face_proposals import (
    intersection_key,
    propose_faces,
    reference_key,
    region_key,
)


def item(
    name: str, surface: dict[str, Any], points: list[list[float]]
) -> dict[str, Any]:
    return {
        "reference": {"feature": name},
        "label": name,
        "surface": surface,
        "observations": np.asarray(points, dtype=float).reshape(-1, 3),
        "weights": np.ones(len(points)),
    }


def plane(
    name: str = "shoulder",
    z: float = 2,
    sign: int = 1,
    points: list[list[float]] | None = None,
) -> dict[str, Any]:
    return item(
        name,
        {"kind": "plane", "plane_equation": [0, 0, sign, sign * z]},
        points or [[2, 0, z], [0, 2.5, z], [-3, 0, z]],
    )


def side(
    name: str = "wall",
    radius: float = 4,
    slope: float = 0,
    points: list[list[float]] | None = None,
) -> dict[str, Any]:
    return item(
        name,
        {
            "kind": "cone" if slope else "cylinder",
            "parameters": [0, 0, 0, 0, radius, 0, slope],
        },
        points or [[radius, 0, 0], [0, radius, 0.5], [-radius, 0, 1]],
    )


def face(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(face for face in result["faces"] if face["surface"]["feature"] == name)


def selected(face: dict[str, Any]) -> dict[str, Any]:
    return next(
        region
        for region in face["regions"]
        if region["key"] == face["suggested_region_key"]
    )


def test_annulus_and_open_walls_share_semantic_edges() -> None:
    result = propose_faces([plane(), side("outer"), side("inner", 1)])
    shoulder = face(result, "shoulder")
    assert shoulder["status"] == "suggested"
    assert len(shoulder["regions"]) == 3
    annulus = selected(shoulder)
    assert annulus["bounds"] == {"radial": [1.0, 4.0]}
    assert annulus["bounded"]
    for name in ("inner", "outer"):
        wall = selected(face(result, name))
        assert wall["bounds"] == {"axial": [None, 2.0]}
        assert not wall["bounded"]
        assert wall["boundaries"][0]["intersection_key"] in {
            use["intersection_key"] for use in annulus["boundaries"]
        }


@pytest.mark.parametrize("sign", [-1, 1])
def test_axial_regions_and_normal_reversal(sign: int) -> None:
    wall = side(points=[[4, 0, 2], [4, 0, 3], [4, 0, 4]])
    result = propose_faces([wall, plane("lower", 1, sign), plane("upper", 5, sign)])
    region = selected(face(result, "wall"))
    assert region["bounds"] == {"axial": [1.0, 5.0]}
    assert region["bounded"]
    assert {use["keep"] for use in region["boundaries"]} == {"positive", "negative"}


@pytest.mark.parametrize(
    "points,status",
    [
        ([[0.5, 0, 2]], "suggested"),
        ([[5, 0, 2]], "suggested"),
        ([[4, 0, 2]], "ambiguous"),
        ([[2, 0, 2], [5, 0, 2]], "ambiguous"),
    ],
)
def test_disk_exterior_boundary_and_mixed_evidence(
    points: list[list[float]], status: str
) -> None:
    result = propose_faces([plane(points=points), side()])
    target = face(result, "shoulder")
    assert target["status"] == status
    if status == "suggested":
        assert any("Sparse" in message for message in target["diagnostics"])
    else:
        assert target["suggested_region_key"] is None
    if points == [[4, 0, 2]]:
        assert all(
            region["evidence"]["boundary_count"] == 1 for region in target["regions"]
        )


def test_empty_observations_do_not_invent_caps() -> None:
    wall = side()
    wall["observations"] = np.empty((0, 3))
    wall["weights"] = np.empty(0)
    target = face(propose_faces([wall, plane()]), "wall")
    assert target["status"] == "no_observations"
    assert target["suggested_region_key"] is None
    assert [region["bounds"]["axial"] for region in target["regions"]] == [
        [None, 2.0],
        [2.0, None],
    ]


def test_nonconcentric_plane_is_not_silently_partitioned_by_one_boss() -> None:
    other = side("other", 2)
    other["surface"]["parameters"][0] = 10
    target = face(propose_faces([plane(), side(), other]), "shoulder")
    assert target["status"] == "unsupported"
    assert not target["regions"]
    assert any("Nonconcentric" in message for message in target["diagnostics"])


def test_coincident_distinct_cuts_require_edge_choice() -> None:
    target = face(propose_faces([side(), plane("a"), plane("b")]), "wall")
    assert target["status"] == "ambiguous"
    assert not target["regions"]
    assert "coincident" in " ".join(target["diagnostics"])


def test_unsupported_cut_blocks_default_but_retains_supported_partial_regions() -> None:
    oblique = plane("oblique")
    oblique["surface"]["plane_equation"] = [0.1, 0, 1, 2]
    result = propose_faces([side(), plane(), oblique])
    target = face(result, "wall")
    assert target["status"] == "requires_adjacency_review"
    assert not target["blocked_by_adjacency"]
    assert target["regions"]
    assert target["suggested_region_key"] is None
    assert any(
        "oblique" in diagnostic["message"] for diagnostic in result["diagnostics"]
    )


def test_missing_boundaries_do_not_use_coverage_as_extents() -> None:
    result = propose_faces([side()])
    target = face(result, "wall")
    assert target["status"] == "missing_boundaries"
    assert target["regions"] == []
    assert "extrema are not caps" in " ".join(target["diagnostics"])


def test_cone_evidence_uses_projected_not_raw_axial_coordinate() -> None:
    cone = side("cone", 10, 0.5, [[14, 0, 1.9], [14, 0, 1.8]])
    target = face(propose_faces([cone, plane()]), "cone")
    positive = next(
        region for region in target["regions"] if region["bounds"]["axial"][0] == 2
    )
    assert positive["evidence"]["interior_count"] == 2
    assert positive["evidence"]["projected_range"] == pytest.approx([3.04, 3.12])


def test_undefined_projection_is_reported_and_blocks_default() -> None:
    wall = side(points=[[0, 0, 1], [4, 0, 1]])
    target = face(propose_faces([wall, plane()]), "wall")
    assert target["status"] == "ambiguous"
    assert all(
        region["evidence"]["undefined_count"] == 1 for region in target["regions"]
    )


def test_cone_preview_failure_is_explicit_and_does_not_add_cap() -> None:
    target = face(propose_faces([side("cone", 1, 1), plane()]), "cone")
    assert target["status"] == "unsupported"
    assert any("preview unavailable" in message for message in target["diagnostics"])
    assert any(region["preview"]["positions"] == [] for region in target["regions"])
    assert target["regions"][0]["bounds"]["axial"][0] is None


def test_unequal_coaxial_profiles_report_unsupported_circular_cut() -> None:
    result = propose_faces([side("cylinder", 4), side("cone", 3, 1)])
    assert result["diagnostics"]
    assert all(
        target["status"] == "requires_adjacency_review" for target in result["faces"]
    )


def test_weights_are_normalized_without_overflow_or_row_filtering() -> None:
    shoulder = plane(points=[[2, 0, 2], [5, 0, 2]])
    shoulder["weights"] = np.array([1e308, 5e307])
    target = face(propose_faces([shoulder, side()]), "shoulder")
    assert target["status"] == "ambiguous"
    assert target["regions"][0]["evidence"]["weighted_fraction"] == pytest.approx(2 / 3)
    for region in target["regions"]:
        evidence = region["evidence"]
        assert evidence["total_count"] == 2
        assert (
            sum(
                evidence[key]
                for key in [
                    "interior_count",
                    "boundary_count",
                    "elsewhere_count",
                    "undefined_count",
                ]
            )
            == 2
        )


def test_identity_helpers_retain_context_and_ignore_order() -> None:
    direct = {"feature": "wall"}
    explicit = {"feature": "solve", "surface": "wall"}
    shoulder = {"feature": "shoulder", "surface": None}
    assert reference_key(direct) == reference_key({**direct, "surface": None})
    assert reference_key(direct) != reference_key(explicit)
    assert intersection_key(direct, shoulder) == intersection_key(shoulder, direct)
    uses = [
        {"intersection_key": "one", "keep": "inside"},
        {"intersection_key": "two", "keep": "outside"},
    ]
    assert region_key(direct, uses) == region_key(direct, uses[::-1])
    assert region_key(direct, uses) != region_key(explicit, uses)


def test_permutation_stability_and_input_immutability() -> None:
    inputs = [plane(), side("inner", 1), side("outer", 4)]
    original = deepcopy(inputs)
    expected = propose_faces(inputs)
    for order in permutations(inputs):
        assert propose_faces(list(order)) == expected
    for actual, before in zip(inputs, original, strict=True):
        assert actual["surface"] == before["surface"]
        assert actual["reference"] == before["reference"]
        np.testing.assert_array_equal(actual["observations"], before["observations"])
        np.testing.assert_array_equal(actual["weights"], before["weights"])


def test_refit_does_not_change_semantic_intersection_or_region_keys() -> None:
    inputs = [plane(), side("inner", 1), side("outer", 4)]
    before = propose_faces(inputs)
    inputs[0]["surface"]["plane_equation"][3] = 3
    inputs[1]["surface"]["parameters"][4] = 0.9
    inputs[2]["surface"]["parameters"][4] = 4.2
    after = propose_faces(inputs)
    assert [edge["key"] for edge in before["intersections"]] == [
        edge["key"] for edge in after["intersections"]
    ]
    for first, second in zip(before["faces"], after["faces"], strict=True):
        assert first["key"] == second["key"]
        assert [region["key"] for region in first["regions"]] == [
            region["key"] for region in second["regions"]
        ]


def test_translation_aware_boundary_precision_does_not_choose_a_side() -> None:
    shoulder = plane(points=[[4 + 1e-7, 0, 2]])
    wall = side()
    shift = np.array([1e9, -1e9, 0])
    for value in (shoulder, wall):
        value["observations"] = value["observations"] + shift
    wall["surface"]["parameters"][:2] = shift[:2].tolist()
    target = face(propose_faces([shoulder, wall]), "shoulder")
    assert target["status"] == "ambiguous"
    assert all(
        region["evidence"]["boundary_count"] == 1 for region in target["regions"]
    )


def test_points_projecting_past_cone_apex_are_not_counted_as_evidence() -> None:
    cone = side("cone", 1, 1, [[0.1, 0, -10], [5, 0, 4]])
    target = face(propose_faces([cone, plane()]), "cone")
    assert target["suggested_region_key"] is None
    assert all(
        region["evidence"]["undefined_count"] == 1 for region in target["regions"]
    )


def test_unsupported_surface_is_visible_without_losing_other_regions() -> None:
    sphere = item("sphere", {"kind": "sphere", "parameters": [0, 0, 0, 1]}, [[1, 0, 0]])
    result = propose_faces([sphere, plane(), side()])
    assert face(result, "sphere")["status"] == "unsupported"
    assert face(result, "wall")["regions"]
    assert face(result, "wall")["suggested_region_key"] is None
    assert result["diagnostics"]


@pytest.mark.parametrize("kind", ["count", "duplicate", "weights", "coordinates"])
def test_invalid_requests_are_not_truncated_or_silently_filtered(kind: str) -> None:
    inputs = [side()]
    if kind == "count":
        inputs = []
    elif kind == "duplicate":
        inputs.append(deepcopy(inputs[0]))
    elif kind == "weights":
        inputs[0]["weights"][0] = 0
    else:
        inputs[0]["observations"][0, 0] = np.inf
    with pytest.raises(ValueError):
        _ = propose_faces(inputs)
