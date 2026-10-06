"""Measure STEP geometry independently of Scansor's CAD construction helpers."""

import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from zipfile import ZipFile

import numpy as np
from numpy.typing import NDArray
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.collections import Sequence_TDF_Label
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_DocumentTool

Array = NDArray[np.float64]

# These allowances cover numerical STEP serialization and reimport, not fit
# uncertainty or a product acceptance threshold. Lengths are in millimeters.
# For this roughly 100 mm fixture, 1e-7 mm accommodates transformed solver and
# STEP round-off (existing export checks use 1e-8 mm). The 1e-9 cross-product
# allowance bounds parallelism error near one nanoradian. Both stay well below
# the deliberate 1e-3 mm / 1e-3 rad export corruptions exercised by the regression.
DIRECTION_TOLERANCE = 1e-9
LENGTH_TOLERANCE = 1e-7


@dataclass(frozen=True)
class AnalyticSurface:
    kind: Literal["plane", "cylinder"]
    direction: Array
    location: Array
    radius: float | None = None


def read_step(bundle: bytes, root: Path) -> dict[str, TopoDS_Shape]:
    """Read exported STEP in fixed millimeters, without trusting its sidecar."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model.step"
    with ZipFile(io.BytesIO(bundle)) as archive:
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPCAFControl_Reader()
    read_file = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read_file(str(path)) == IFSelect_RetDone
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(document, 0.001)
    assert reader.Transfer(document)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    labels = Sequence_TDF_Label()
    tool.GetFreeShapes(labels)
    shapes: dict[str, TopoDS_Shape] = {}
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        attribute = TDataStd_Name()
        assert label.FindAttribute(TDataStd_Name.GetID_s(), attribute)
        name = attribute.Get().ToExtString()
        assert name and name not in shapes, "STEP roots must have unique names"
        shape = tool.GetShape_s(label)
        assert not shape.IsNull()
        shapes[name] = shape
    assert shapes, "STEP must contain named geometry"
    return shapes


def measure_surfaces(shape: TopoDS_Shape) -> list[AnalyticSurface]:
    """Read actual analytic supports, independent of face orientation/order."""
    measurements: list[AnalyticSurface] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        surface = BRepAdaptor_Surface(TopoDS.Face(explorer.Current()))
        if surface.GetType() == GeomAbs_Plane:
            plane = surface.Plane()
            measurements.append(
                AnalyticSurface(
                    "plane",
                    np.asarray(plane.Axis().Direction().Coord(), dtype=float),
                    np.asarray(plane.Location().Coord(), dtype=float),
                )
            )
        else:
            assert surface.GetType() == GeomAbs_Cylinder, "unexpected analytic type"
            cylinder = surface.Cylinder()
            measurements.append(
                AnalyticSurface(
                    "cylinder",
                    np.asarray(cylinder.Axis().Direction().Coord(), dtype=float),
                    np.asarray(cylinder.Location().Coord(), dtype=float),
                    cylinder.Radius(),
                )
            )
        explorer.Next()
    assert measurements, "STEP shape must contain analytic faces"
    return measurements


def expected_surface(record: dict[str, Any], matrix: Array) -> AnalyticSurface:
    """Apply the evaluated transform directly to the graph's numeric fit chart."""
    linear = matrix[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0]))
    assert scale > 0
    rotation = linear / scale
    np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-10, rtol=0)
    kind = record["kind"]
    if kind == "plane":
        equation = np.asarray(record["plane_equation"], dtype=float)
        length = float(np.linalg.norm(equation[:3]))
        direction = equation[:3] / length
        location = direction * (equation[3] / length)
        radius = None
    else:
        assert kind == "cylinder"
        parameters = np.asarray(record["parameters"], dtype=float)
        assert parameters.shape == (7,) and parameters[6] == 0
        direction = np.array([parameters[2], parameters[3], 1.0])
        direction /= np.linalg.norm(direction)
        location = np.array([parameters[0], parameters[1], 0.0])
        radius = scale * float(parameters[4])
    return AnalyticSurface(
        kind,
        rotation @ direction,
        linear @ location + matrix[:3, 3],
        radius,
    )


def assert_surface(actual: AnalyticSurface, expected: AnalyticSurface) -> None:
    """Compare supports while allowing axis gauge and normal sign changes."""
    assert actual.kind == expected.kind
    np.testing.assert_allclose(
        np.cross(actual.direction, expected.direction),
        0.0,
        atol=DIRECTION_TOLERANCE,
        rtol=0,
    )
    if expected.kind == "plane":
        # STEP may reverse the geometric normal independently of solid winding.
        sign = 1.0 if actual.direction @ expected.direction >= 0 else -1.0
        actual_offset = sign * float(actual.direction @ actual.location)
        expected_offset = float(expected.direction @ expected.location)
        np.testing.assert_allclose(
            actual_offset, expected_offset, atol=LENGTH_TOLERANCE, rtol=0
        )
    else:
        # Cylinder.Location can move along its infinite axis without changing it.
        np.testing.assert_allclose(
            np.cross(actual.location - expected.location, expected.direction),
            0.0,
            atol=LENGTH_TOLERANCE,
            rtol=0,
        )
        assert actual.radius is not None and expected.radius is not None
        np.testing.assert_allclose(
            actual.radius, expected.radius, atol=LENGTH_TOLERANCE, rtol=0
        )
