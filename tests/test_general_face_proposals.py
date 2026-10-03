"""Loop-region proposals preserve evidence and expose incomplete topology."""

from copy import deepcopy
from typing import Any

import numpy as np
import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from experiments.face_geometry import face_from_record
from experiments.face_proposals import propose_faces
from experiments.general_face_proposals import project_observations
from experiments.surface_extents import trimmed_face


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
    name: str, equation: list[float], points: list[list[float]] | None = None
) -> dict[str, Any]:
    return item(name, {"kind": "plane", "plane_equation": equation}, points or [])


def cylinder(name: str, x: float, y: float, radius: float) -> dict[str, Any]:
    return item(
        name, {"kind": "cylinder", "parameters": [x, y, 0, 0, radius, 0, 0]}, []
    )


def target(plan: dict[str, Any], name: str = "plate") -> dict[str, Any]:
    return next(face for face in plan["faces"] if face["label"] == name)


def reconstruct(
    plan: dict[str, Any], face: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    region = next(
        region
        for region in face["regions"]
        if region["key"] == face["suggested_region_key"]
    )
    if "arrangement" in region:
        from experiments.face_arrangement import select_face

        intent = region["bounds"]["arrangement"]
        return select_face(
            source["surface"],
            intent["cutters"],
            region["arrangement"]["selector"],
            domains=intent["domains"],
            observations=source["observations"],
        )
    edges = {edge["key"]: edge["geometry"] for edge in plan["intersections"]}
    return trimmed_face(
        source["surface"],
        [
            (
                {"intersection": use["intersection_key"], "keep": use["keep"]},
                edges[use["intersection_key"]],
            )
            for use in region["boundaries"]
        ],
        source["observations"],
    )


def area(record: dict[str, Any]) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face_from_record(record), properties)
    return properties.Mass()


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_nonconcentric_holes_have_one_reviewable_outer_region(scale: float) -> None:
    source = plane("plate", [0, 0, 1, 0], [[0, 3 * scale, 0], [0, -3 * scale, 0]])
    inputs = [
        source,
        cylinder("outer", 0, 0, 6 * scale),
        cylinder("left hole", -2 * scale, 0, scale),
        cylinder("right hole", 2 * scale, 0, scale),
    ]
    originals = deepcopy(inputs)
    plan = propose_faces(inputs)
    face = target(plan)
    assert face["status"] == "suggested" and len(face["regions"]) == 4
    assert sum(region["bounded"] for region in face["regions"]) == 3
    record = reconstruct(plan, face, source)
    assert len(record["bounds"]["loops"]) == 3
    assert area(record) == pytest.approx(34 * np.pi * scale**2, rel=1e-7)
    for actual, original in zip(inputs, originals, strict=True):
        assert actual["surface"] == original["surface"]
        np.testing.assert_array_equal(actual["observations"], original["observations"])
        np.testing.assert_array_equal(actual["weights"], original["weights"])
    reverse = target(propose_faces(list(reversed(inputs))))
    assert reverse == face


@pytest.mark.parametrize("shift", [0, 1e12])
def test_polygon_with_holes_is_not_limited_by_observation_extrema(shift: float) -> None:
    source = plane(
        "plate", [0, 0, 1, 0], [[shift, shift, 0], [shift + 0.5, shift + 1, 0]]
    )
    inputs = [
        source,
        plane("left", [1, 0, 0, shift - 4]),
        plane("right", [1, 0, 0, shift + 4]),
        plane("bottom", [0, 1, 0, shift - 3]),
        plane("top", [0, 1, 0, shift + 3]),
        cylinder("hole", shift - 2, shift, 0.5),
    ]
    plan = propose_faces(inputs)
    face = target(plan)
    assert face["status"] == "suggested"
    record = reconstruct(plan, face, source)
    assert any(
        edge["kind"] == "line"
        for loop in record["bounds"]["loops"]
        for edge in loop["edges"]
    )
    assert len(record["bounds"]["loops"]) == 2
    assert area(record) == pytest.approx(48 - 0.25 * np.pi, rel=1e-5)


def test_crossing_and_touching_holes_preserve_native_cells_and_provenance() -> None:
    for separation in (1.5, 2):
        source = plane("plate", [0, 0, 1, 0], [[0, 3, 0]])
        plan = propose_faces(
            [
                source,
                cylinder("outer", 0, 0, 6),
                cylinder("a", 0, 0, 1),
                cylinder("b", separation, 0, 1),
            ]
        )
        face = target(plan)
        assert face["status"] == "suggested"
        record = reconstruct(plan, face, source)
        assert record["bounded"]
        assert len(record["boundary_keys"]) == 3
        overlap = 2 * np.arccos(separation / 2) - 0.5 * separation * np.sqrt(
            4 - separation**2
        )
        assert area(record) == pytest.approx(34 * np.pi + overlap, rel=1e-7)


def test_holes_alone_account_for_open_plate_without_suggesting_it() -> None:
    face = target(
        propose_faces(
            [
                plane("plate", [0, 0, 1, 0], [[0, 3, 0]]),
                cylinder("a", -2, 0, 1),
                cylinder("b", 2, 0, 1),
            ]
        )
    )
    assert face["status"] == "missing_boundaries"
    assert face["suggested_region_key"] is None
    assert face["suggested_region_keys"] == []
    region = next(
        region for region in face["regions"] if region["evidence"]["interior_count"]
    )
    assert not region["bounded"] and region["preview_clipped"]
    with pytest.raises(ValueError, match="not physical caps"):
        _ = face_from_record(region)
    assert any("open regions omitted" in message for message in face["diagnostics"])


def test_face_with_more_than_sixteen_boundaries_is_not_truncated() -> None:
    source = plane("plate", [0, 0, 1, 0], [[0, 0, 0]])
    inputs = [source, cylinder("outer", 0, 0, 6)]
    for index in range(17):
        angle = 2 * np.pi * index / 17
        inputs.append(
            cylinder(f"hole {index}", 4 * np.cos(angle), 4 * np.sin(angle), 0.1)
        )
    plan = propose_faces(inputs)
    face = target(plan)
    assert face["status"] == "suggested"
    record = reconstruct(plan, face, source)
    assert len(record["boundary_ids"]) == len(record["bounds"]["loops"]) == 18
    assert area(record) == pytest.approx((36 - 0.17) * np.pi, rel=1e-7)


@pytest.mark.parametrize("sign", [-1, 1])
def test_oblique_cylinder_region_respects_cut_normal_reversal(sign: int) -> None:
    source = cylinder("wall", 0, 0, 2)
    source["observations"] = np.array([[2, 0, 0], [0, 2, 0], [-2, 0, 0]], dtype=float)
    source["weights"] = np.array([1, 2, 3], dtype=float)
    inputs = [
        source,
        plane("lower", [sign * 0.2, 0, sign, sign * -2]),
        plane("upper", [sign * -0.1, 0, sign, sign * 3]),
    ]
    plan = propose_faces(inputs)
    face = target(plan, "wall")
    assert face["status"] == "suggested"
    record = reconstruct(plan, face, source)
    assert "arrangement" in record["bounds"] and record["bounded"]
    assert area(record) == pytest.approx(20 * np.pi, rel=1e-7)


@pytest.mark.parametrize("kind", ["plane", "cylinder"])
def test_unrepresentable_projection_is_undefined_not_kernel_input(kind: str) -> None:
    geometry: dict[str, Any] = {
        "kind": kind,
        "axis": [0, 0, 1],
        "offset": 0,
        "origin": [0, 0, 0],
        "radius": 1,
        "slope": 0,
    }
    if kind == "plane":
        geometry["axis"] = (np.ones(3) / np.sqrt(3)).tolist()
    projected, defined = project_observations(
        geometry, np.array([[1.5e308, 1.5e308, 1.5e308], [1, 0, 0]])
    )
    assert defined.tolist() == [False, True]
    assert np.isfinite(projected[defined]).all()


def test_overlapping_native_evidence_blocks_review_instead_of_suggesting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.face_arrangement as native

    original = native.arrange_faces

    def poisoned(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        records = original(*args, **kwargs)
        if len(records) > 1 and records[0]["evidence"]["interior"]:
            for record in records:
                record["evidence"]["interior"] = [True] * len(
                    record["evidence"]["interior"]
                )
        return records

    monkeypatch.setattr(native, "arrange_faces", poisoned)
    face = target(
        propose_faces(
            [
                plane("plate", [0, 0, 1, 0], [[-1, 1, 0], [1, 2, 0]]),
                plane("cut", [1, 0, 0, 0]),
            ]
        )
    )
    assert face["status"] == "unsupported" and face["blocked_by_geometry"]
    assert not face.get("suggested_region_keys")
