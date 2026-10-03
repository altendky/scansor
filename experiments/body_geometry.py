"""Conservative, replayable assembly of bounded faces into one finite solid.

Sewing may join coincident boundaries within the declared tolerance; it never
adds faces, caps openings, unions solids, or repairs the input surfaces.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from hashlib import sha256
from typing import Any

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BOPAlgo import BOPAlgo_CheckerSI
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Copy,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
)
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_NoError, BRepCheck_Shell
from OCP.BRepGProp import BRepGProp
from OCP.BRepLib import BRepLib
from OCP.collections import List_TopoDS_Shape
from OCP.GProp import GProp_GProps
from OCP.ShapeFix import ShapeFix_Shell
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Shape, TopoDS_Solid

from experiments.face_geometry import face_from_record
from experiments.native_replay import cached_native_replay
from experiments.ocp_geometry import kernel_operation, tessellate_face


class BodyAssemblyError(ValueError):
    """A failed assembly with serializable, source-linked boundary diagnostics."""

    diagnostic: dict[str, Any]

    def __init__(self, diagnostic: dict[str, Any]):
        self.diagnostic = diagnostic
        super().__init__("; ".join(p["message"] for p in diagnostic["problems"]))


def _shapes(shape: TopoDS_Shape, kind: Any) -> list[TopoDS_Shape]:
    explorer = TopExp_Explorer(shape, kind)
    result: list[TopoDS_Shape] = []
    while explorer.More():
        result.append(explorer.Current())
        explorer.Next()
    return result


def _edge_preview(edge: TopoDS_Edge) -> dict[str, Any]:
    curve = BRepAdaptor_Curve(edge)
    lo, hi = curve.FirstParameter(), curve.LastParameter()
    if not math.isfinite(lo) or not math.isfinite(hi):
        return {"positions": [], "indices": []}
    points = [curve.Value(float(t)).Coord() for t in np.linspace(lo, hi, 33)]
    return {"positions": np.asarray(points).ravel().tolist(), "indices": []}


def _edges_with_uses(
    shape: TopoDS_Shape,
) -> list[tuple[TopoDS_Edge, list[TopoDS_Face]]]:
    # Count occurrences, not unique ancestors: a periodic face uses its seam
    # twice. Degenerate pole edges do not form an opening in a closed shell.
    buckets: dict[int, list[tuple[TopoDS_Edge, list[TopoDS_Face]]]] = {}
    for raw_face in _shapes(shape, TopAbs_FACE):
        face = TopoDS.Face(raw_face)
        for raw_edge in _shapes(face, TopAbs_EDGE):
            edge = TopoDS.Edge(raw_edge)
            if BRep_Tool.Degenerated_s(edge):
                continue
            bucket = buckets.setdefault(hash(edge), [])
            for known, uses in bucket:
                if edge.IsSame(known):
                    uses.append(face)
                    break
            else:
                bucket.append((edge, [face]))
    return [item for bucket in buckets.values() for item in bucket]


def _problem(
    code: str, message: str, sources: list[str], preview: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "source_faces": sources,
        "preview": preview or {"positions": [], "indices": []},
    }


def _failure(problems: list[dict[str, Any]], tolerance: float) -> BodyAssemblyError:
    return BodyAssemblyError(
        {
            "kind": "body",
            "valid": False,
            "sewing_tolerance": tolerance,
            "problems": problems,
            "preview": {
                "positions": [],
                "indices": [],
                "edges": [
                    {
                        **problem["preview"],
                        "source_faces": problem["source_faces"],
                        "kind": problem["code"],
                    }
                    for problem in problems
                    if problem["preview"]["positions"]
                ],
            },
        }
    )


def _source_ids(
    sewn_faces: list[TopoDS_Face], modified: dict[str, TopoDS_Shape]
) -> list[str]:
    return [
        identifier
        for identifier, shape in modified.items()
        if any(
            candidate.IsSame(face)
            for candidate in _shapes(shape, TopAbs_FACE)
            for face in sewn_faces
        )
    ]


def _max_tolerance(shape: TopoDS_Shape) -> float:
    return max(
        [
            BRep_Tool.Tolerance_s(TopoDS.Edge(edge))
            for edge in _shapes(shape, TopAbs_EDGE)
        ]
        + [
            BRep_Tool.Tolerance_s(TopoDS.Vertex(vertex))
            for vertex in _shapes(shape, TopAbs_VERTEX)
        ]
        + [
            BRep_Tool.Tolerance_s(TopoDS.Face(face))
            for face in _shapes(shape, TopAbs_FACE)
        ],
        default=0.0,
    )


def _interference_problems(
    checker: BOPAlgo_CheckerSI, modified: dict[str, TopoDS_Shape]
) -> list[dict[str, Any]]:
    """Link failed native interference checks back to participating source faces."""
    sources: set[str] = set()
    edges: dict[int, tuple[TopoDS_Edge, set[str]]] = {}
    for pair in checker.DS().Interferences():
        for index in pair.Indices():
            suspect = checker.DS().Shape(index)
            owners = {
                identifier
                for identifier, shape in modified.items()
                if any(
                    candidate.IsSame(suspect)
                    for candidate in _shapes(shape, suspect.ShapeType())
                )
            }
            sources.update(owners)
            for raw_edge in _shapes(suspect, TopAbs_EDGE):
                edge = TopoDS.Edge(raw_edge)
                if not BRep_Tool.Degenerated_s(edge):
                    _, existing = edges.setdefault(hash(edge), (edge, set()))
                    existing.update(owners)
    return [
        _problem(
            "self_intersection",
            "Shell has intersecting or overlapping boundaries/faces, or intersection validation failed",
            sorted(sources) or list(modified),
        ),
        *[
            _problem(
                "intersecting_boundary",
                "Boundary of an intersecting face",
                sorted(owners),
                _edge_preview(edge),
            )
            for edge, owners in edges.values()
        ],
    ]


@kernel_operation
def _assemble_native(
    records: dict[str, dict[str, Any]], tolerance: float
) -> tuple[TopoDS_Solid, dict[str, Any]]:
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("body sewing tolerance must be positive and finite")
    if not records:
        raise _failure(
            [_problem("empty", "Body requires bounded faces", [])], tolerance
        )
    faces: dict[str, TopoDS_Face] = {}
    for identifier, record in records.items():
        if record.get("bounded") is not True:
            raise _failure(
                [
                    _problem(
                        "open_face",
                        "Body input must be an explicitly bounded physical face",
                        [identifier],
                    )
                ],
                tolerance,
            )
        try:
            # Native replay can share faces with boundary reviews in a graph
            # pass. Isolate topology and geometry before sewing updates either.
            faces[identifier] = TopoDS.Face(
                BRepBuilderAPI_Copy(face_from_record(record), True, False).Shape()
            )
        except ValueError as error:
            raise _failure(
                [_problem("invalid_face", f"{identifier}: {error}", [identifier])],
                tolerance,
            ) from error
    # OCCT geometry already carries tolerances. Refuse inputs whose uncertainty
    # exceeds the requested tolerance instead of silently broadening the join.
    input_tolerances = {key: _max_tolerance(face) for key, face in faces.items()}
    oversized = [
        key for key, value in input_tolerances.items() if value > tolerance * (1 + 1e-6)
    ]
    if oversized:
        required = max(input_tolerances.values())
        problem = _problem(
            "input_tolerance",
            f"Input face tolerance requires at least {required:.8g}; declared sewing tolerance is {tolerance:.8g}",
            oversized,
        )
        problem["required_tolerance"] = required
        problem["face_tolerances"] = input_tolerances
        raise _failure(
            [problem],
            tolerance,
        )
    sewing = BRepBuilderAPI_Sewing(tolerance, True, True, True, True)
    sewing.SetLocalTolerancesMode(False)
    sewing.SetMinTolerance(min(tolerance, 1e-12))
    sewing.SetMaxTolerance(tolerance)
    for face in faces.values():
        sewing.Add(face)
    sewing.Perform()
    shape = sewing.SewedShape()
    if shape.IsNull():
        raise _failure(
            [_problem("empty", "Sewing produced no shell", list(faces))], tolerance
        )
    modified = {
        key: sewing.Modified(face) if sewing.IsModified(face) else face
        for key, face in faces.items()
    }
    problems: list[dict[str, Any]] = []
    for edge, uses in _edges_with_uses(shape):
        if len(uses) != 2:
            code = "free_edge" if len(uses) == 1 else "nonmanifold_edge"
            problems.append(
                _problem(
                    code,
                    "Open boundary"
                    if len(uses) == 1
                    else "Boundary shared by more than two face uses",
                    _source_ids(uses, modified),
                    _edge_preview(edge),
                )
            )
    if sewing.NbDeletedFaces() or len(_shapes(shape, TopAbs_FACE)) != len(faces):
        problems.append(
            _problem(
                "changed_faces",
                "Sewing dropped or merged input faces; check duplicate or tiny faces",
                list(faces),
            )
        )
    if shape.ShapeType() != TopAbs_SHELL:
        problems.append(
            _problem(
                "disconnected",
                "Body requires exactly one connected shell; use separate Bodies for disconnected shells. Nested cavity shells are not supported",
                list(faces),
            )
        )
    if problems:
        raise _failure(problems, tolerance)
    shell = TopoDS.Shell(shape)
    orientation = ShapeFix_Shell()
    orientation.Init(shell)
    _ = orientation.FixFaceOrientation(shell, False, False)
    shell = orientation.Shell()
    if not orientation.ErrorFaces().IsNull() or orientation.NbShells() != 1:
        raise _failure(
            [
                _problem(
                    "orientation",
                    "Shell faces cannot be oriented consistently",
                    list(faces),
                )
            ],
            tolerance,
        )
    if BRepCheck_Shell(shell).Closed() != BRepCheck_NoError:
        raise _failure(
            [_problem("open_shell", "Shell is not closed", list(faces))], tolerance
        )
    if not BRepCheck_Analyzer(shell).IsValid():
        raise _failure(
            [
                _problem(
                    "invalid_topology", "Sewn shell has invalid topology", list(faces)
                )
            ],
            tolerance,
        )
    checker = BOPAlgo_CheckerSI()
    arguments = List_TopoDS_Shape()
    _ = arguments.Append(shell)
    checker.SetArguments(arguments)
    checker.SetLevelOfCheck(5)
    checker.Perform()
    if checker.HasErrors() or checker.DS().Interferences().Extent():
        raise _failure(
            _interference_problems(checker, modified),
            tolerance,
        )
    solid_builder = BRepBuilderAPI_MakeSolid(shell)
    solid = solid_builder.Solid()
    if (
        not solid_builder.IsDone()
        or not BRepLib.OrientClosedSolid_s(solid)
        or not BRepCheck_Analyzer(solid).IsValid()
    ):
        raise _failure(
            [
                _problem(
                    "invalid_solid",
                    "Closed shell did not form a valid oriented solid",
                    list(faces),
                )
            ],
            tolerance,
        )
    if _max_tolerance(solid) > tolerance * (1 + 1e-6):
        raise _failure(
            [
                _problem(
                    "output_tolerance",
                    "Assembled topology exceeds the declared sewing tolerance",
                    list(faces),
                )
            ],
            tolerance,
        )
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, properties)
    volume = properties.Mass()
    if not math.isfinite(volume) or volume <= 0:
        raise _failure(
            [
                _problem(
                    "volume",
                    "Body does not enclose a finite positive volume",
                    list(faces),
                )
            ],
            tolerance,
        )
    box = Bnd_Box()
    BRepBndLib.Add_s(solid, box, False)
    scale = float(
        np.linalg.norm(np.subtract(box.CornerMax().Coord(), box.CornerMin().Coord()))
    )
    preview: dict[str, Any] = {"positions": [], "indices": [], "edges": []}
    for raw_face in _shapes(solid, TopAbs_FACE):
        piece = tessellate_face(TopoDS.Face(raw_face), scale)
        offset = len(preview["positions"]) // 3
        preview["positions"].extend(piece["positions"])
        preview["indices"].extend(index + offset for index in piece["indices"])
    for edge, uses in _edges_with_uses(solid):
        preview["edges"].append(
            {
                **_edge_preview(edge),
                "source_faces": _source_ids(uses, modified),
                "kind": "boundary",
            }
        )
    result = {
        "kind": "body",
        "valid": True,
        "face_ids": list(records),
        "sewing_tolerance": tolerance,
        "volume": volume,
        "face_count": len(faces),
        "maximum_input_tolerance": max(input_tolerances.values()),
        "maximum_output_tolerance": _max_tolerance(solid),
        "problems": [],
        "preview": preview,
    }
    return solid, result


def _replay(
    faces: dict[str, dict[str, Any]], tolerance: float
) -> tuple[TopoDS_Solid, dict[str, Any]]:
    key = body_input_fingerprint(faces, tolerance)

    def construct() -> tuple[TopoDS_Solid, dict[str, Any]]:
        solid, record = _assemble_native(faces, tolerance)
        record["input_fingerprint"] = key
        return solid, record

    return cached_native_replay(("body", key), construct)


def body_input_fingerprint(faces: dict[str, dict[str, Any]], tolerance: float) -> str:
    """Fingerprint exact resolved inputs without storing duplicate face records."""
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("body sewing tolerance must be positive and finite")
    canonical = json.dumps(
        [faces, tolerance], sort_keys=True, allow_nan=False, separators=(",", ":")
    )
    return sha256(canonical.encode()).hexdigest()


def assemble_body(
    faces: dict[str, dict[str, Any]], sewing_tolerance: float
) -> dict[str, Any]:
    """Validate one closed solid and return JSON evidence, never a native object."""
    return deepcopy(_replay(faces, sewing_tolerance)[1])


def body_from_record(
    record: dict[str, Any], faces: dict[str, dict[str, Any]]
) -> TopoDS_Solid:
    """Reconstruct and validate declared physical inputs for STEP export."""
    if record.get("kind") != "body" or record.get("valid") is not True:
        raise ValueError("solid export requires a successfully evaluated Body")
    if set(record.get("face_ids", [])) != set(faces) or len(
        record.get("face_ids", [])
    ) != len(faces):
        raise ValueError("Body face inputs do not match its evaluated result")
    tolerance = float(record["sewing_tolerance"])
    if body_input_fingerprint(faces, tolerance) != record.get("input_fingerprint"):
        raise ValueError(
            "Body face inputs changed since assembly; evaluate the Body again"
        )
    return _replay(faces, tolerance)[0]
