"""Physical adjacency is reviewed separately from infinite-surface intersections."""

from copy import deepcopy
from itertools import permutations
from typing import Any

import numpy as np
import pytest

from experiments.face_proposals import intersection_key, propose_faces
from experiments.surface_extents import circle_intersection, trimmed_face


def item(
    name: str, surface: dict[str, Any], points: list[list[float]]
) -> dict[str, Any]:
    return {
        "reference": {"feature": name},
        "label": name,
        "surface": surface,
        "observations": np.asarray(points, dtype=float),
        "weights": np.asarray([1, 2, 3], dtype=float),
    }


def plane(name: str = "shoulder", z: float = 2) -> dict[str, Any]:
    return item(
        name,
        {"kind": "plane", "plane_equation": [0, 0, 1, z]},
        [[1, 0, z], [0, 2, z], [-3, 0, z]],
    )


def wall(name: str = "outer", radius: float = 4) -> dict[str, Any]:
    return item(
        name,
        {"kind": "cylinder", "parameters": [0, 0, 0, 0, radius, 0, 0]},
        [[radius, 0, 0], [0, radius, 0.5], [-radius, 0, 1]],
    )


def clock(x: float = 3) -> dict[str, Any]:
    return item(
        "clock",
        {"kind": "plane", "plane_equation": [1, 0, 0, x]},
        [[x, 0, 0], [x, 1, 1], [x, 2, 2]],
    )


def decision(
    first: dict[str, Any], second: dict[str, Any], state: str
) -> dict[str, Any]:
    return {"first": first["reference"], "second": second["reference"], "state": state}


def adjacency(
    plan: dict[str, Any], first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    key = intersection_key(first["reference"], second["reference"])
    return next(record for record in plan["adjacencies"] if record["key"] == key)


def face(plan: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        record for record in plan["faces"] if record["surface"]["feature"] == name
    )


def domain(side: dict[str, Any], lower: float, upper: float) -> dict[str, Any]:
    return trimmed_face(
        side["surface"],
        [
            (
                {"intersection": "lo", "keep": "positive"},
                circle_intersection(side["surface"], plane(z=lower)["surface"]),
            ),
            (
                {"intersection": "hi", "keep": "negative"},
                circle_intersection(side["surface"], plane(z=upper)["surface"]),
            ),
        ],
        side["observations"],
    )


def test_unreviewed_generator_pair_proposes_native_cells_without_confirming_adjacency() -> (
    None
):
    side, shoulder, vertical = wall(), plane(), clock()
    plan = propose_faces([side, shoulder, vertical])
    target = face(plan, "outer")
    assert target["regions"]
    assert target["status"] != "unsupported"
    assert target["status"] == "missing_boundaries"
    assert not target["blocked_by_adjacency"]
    assert not target["suggested_region_keys"]
    assert all(not region["bounded"] for region in target["regions"])
    pair = adjacency(plan, side, vertical)
    assert pair["state"] == "proposed"
    assert pair["supported"]
    assert pair["mathematical"]["category"] == "generator_lines"


def test_confirmed_unsupported_pair_blocks_default_but_preserves_review_evidence() -> (
    None
):
    side, shoulder, vertical = wall(), plane(), clock(4)
    plan = propose_faces(
        [side, shoulder, vertical], adjacencies=[decision(side, vertical, "confirmed")]
    )
    target = face(plan, "outer")
    assert target["blocked_by_adjacency"]
    assert target["suggested_region_key"] is None
    assert target["regions"]
    assert adjacency(plan, side, vertical)["state"] == "confirmed"
    assert plan["diagnostics"]


def test_rejected_circle_removes_only_the_boundary_not_original_fit_evidence() -> None:
    side, shoulder, remote = wall(), plane(), plane("remote", 8)
    inputs = [side, shoulder, remote]
    originals = deepcopy(inputs)
    baseline = propose_faces([side, shoulder])
    plan = propose_faces(inputs, adjacencies=[decision(remote, side, "rejected")])
    assert len(plan["intersections"]) == 1
    assert face(plan, "outer")["regions"] == face(baseline, "outer")["regions"]
    assert adjacency(plan, side, remote)["state"] == "rejected"
    for before, after in zip(originals, inputs, strict=True):
        assert before["surface"] == after["surface"]
        np.testing.assert_array_equal(before["observations"], after["observations"])
        np.testing.assert_array_equal(before["weights"], after["weights"])


def test_remote_painted_bounds_are_a_gap_hint_not_an_exclusion_proof() -> None:
    side, shoulder = wall(), plane()
    shoulder["observations"][:, 0] += 100
    plan = propose_faces([side, shoulder])
    pair = adjacency(plan, side, shoulder)
    assert pair["evidence"]["observation_bounds_gap"] > 90
    assert pair["mathematical"]["status"] == "candidate"
    assert pair["state"] != "rejected"
    assert len(plan["intersections"]) == 1


@pytest.mark.parametrize("second_domain,excluded", [(False, True), (True, False)])
def test_explicit_face_domains_are_a_union_not_a_painted_coverage_cap(
    second_domain: bool, excluded: bool
) -> None:
    side, shoulder = wall(), plane(z=4)
    side["face_domains"] = [domain(side, 0, 2)]
    if second_domain:
        side["face_domains"].append(domain(side, 3, 5))
    plan = propose_faces([side, shoulder])
    pair = adjacency(plan, side, shoulder)
    assert (pair["state"] == "rejected") is excluded
    assert bool(plan["intersections"]) is not excluded
    if excluded:
        assert "physical face scope" in pair["reason"].lower()


def test_confirming_proven_physical_domain_exclusion_is_an_error() -> None:
    side, shoulder = wall(), plane(z=4)
    side["face_domains"] = [domain(side, 0, 2)]
    with pytest.raises(ValueError):
        _ = propose_faces(
            [side, shoulder], adjacencies=[decision(side, shoulder, "confirmed")]
        )


def test_clock_plane_outside_bore_but_inside_outer_has_distinct_mathematical_results() -> (
    None
):
    bore, outer, vertical = wall("bore", 5), wall("outer", 11), clock(9)
    plan = propose_faces([bore, outer, vertical])
    empty = adjacency(plan, bore, vertical)
    assert empty["mathematical"]["status"] == "proven_empty"
    assert empty["state"] == "rejected"
    candidate = adjacency(plan, outer, vertical)
    assert candidate["mathematical"]["category"] == "generator_lines"
    assert candidate["state"] == "proposed"
    assert candidate["supported"]


@pytest.mark.parametrize("invalid", ["duplicate", "unknown", "self", "confirm_empty"])
def test_invalid_adjacency_decisions_fail_explicitly(invalid: str) -> None:
    side, shoulder = wall(), plane()
    decisions = [decision(side, shoulder, "confirmed")]
    if invalid == "duplicate":
        decisions.append(decision(shoulder, side, "rejected"))
    elif invalid == "unknown":
        decisions = [decision(side, plane("unknown"), "rejected")]
    elif invalid == "self":
        decisions = [decision(side, side, "confirmed")]
    else:
        shoulder = clock(9)
        decisions = [decision(side, shoulder, "confirmed")]
    with pytest.raises(ValueError):
        _ = propose_faces([side, shoulder], adjacencies=decisions)


def test_adjacency_pair_identity_and_evidence_are_permutation_stable() -> None:
    side, shoulder, vertical = wall(), plane(), clock()
    inputs = [side, shoulder, vertical]
    decisions = [decision(vertical, side, "rejected")]
    baseline = propose_faces(inputs, adjacencies=decisions)
    expected = {record["key"]: record for record in baseline["adjacencies"]}
    for order in permutations(inputs):
        result = propose_faces(list(order), adjacencies=decisions)
        assert {record["key"]: record for record in result["adjacencies"]} == expected
        assert {record["key"] for record in result["intersections"]} == {
            record["key"] for record in baseline["intersections"]
        }


@pytest.mark.parametrize("axis_roundoff", [False, True])
def test_rigid_rotation_preserves_exclusion_and_region_evidence(
    axis_roundoff: bool,
) -> None:
    side, shoulder = wall(), plane()
    side["face_domains"] = [domain(side, 0, 1)]
    baseline = propose_faces([side, shoulder])
    # Rotate around Y without taking the existing revolution chart through Z=0.
    angle = 0.3
    rotation = np.array(
        [
            [np.cos(angle), 0, np.sin(angle)],
            [0, 1, 0],
            [-np.sin(angle), 0, np.cos(angle)],
        ]
    )
    axis = rotation @ np.array([0.0, 0.0, 1.0])
    side["surface"]["parameters"][2:4] = (axis[:2] / axis[2]).tolist()
    shoulder["surface"]["plane_equation"][:3] = axis.tolist()
    for source in (side, shoulder):
        source["observations"] = source["observations"] @ rotation.T
    for scope in side["face_domains"]:
        scope["geometry"]["axis"] = axis.tolist()
        if axis_roundoff:
            scope["geometry"]["axis"][0] = float(np.nextafter(axis[0], np.inf))
    result = propose_faces([side, shoulder])
    assert (
        adjacency(result, side, shoulder)["state"]
        == adjacency(baseline, wall(), plane())["state"]
    )
    assert not result["intersections"]
    del side["face_domains"]
    rotated = propose_faces([side, shoulder])
    original = propose_faces([wall(), plane()])
    for name in ("outer", "shoulder"):
        assert [
            region["evidence"]["interior_count"]
            for region in face(rotated, name)["regions"]
        ] == [
            region["evidence"]["interior_count"]
            for region in face(original, name)["regions"]
        ]
        assert [
            region["evidence"]["weighted_fraction"]
            for region in face(rotated, name)["regions"]
        ] == pytest.approx(
            [
                region["evidence"]["weighted_fraction"]
                for region in face(original, name)["regions"]
            ]
        )
