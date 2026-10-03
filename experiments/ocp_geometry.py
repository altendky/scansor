"""OCP construction boundary for the experiment's existing analytic patches.

Kernel objects are derived, never serialized into graph results. Physical face
bounds and display-only preview coverage remain separate at the caller.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import wraps
from typing import Any

import numpy as np
from numpy.typing import NDArray
from OCP import Standard
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Geom import (
    Geom_Circle,
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_Plane,
    Geom_SphericalSurface,
    Geom_Surface,
    Geom_TrimmedCurve,
)
from OCP.GeomAPI import GeomAPI_IntSS
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf
from OCP.IMeshData import IMeshData_Failure
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Wire

from experiments.surface_primitives import basis, primitive

Array = NDArray[np.float64]
KERNEL_TOLERANCE = 1e-9
# The bindings expose native errors as separate Python Exception subclasses,
# not as subclasses of their C++ Standard_Failure base. Normalize only these
# native exceptions so existing graph/HTTP validation paths retain diagnostics.
_NATIVE_ERRORS = tuple(
    error
    for error in vars(Standard).values()
    if isinstance(error, type) and issubclass(error, Exception)
)


def kernel_operation[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return operation(*args, **kwargs)
        except _NATIVE_ERRORS as error:
            raise ValueError(f"OCP {operation.__name__} failed: {error}") from error

    return invoke


def _xyz(value: object) -> Array:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError("kernel coordinates must contain three finite values")
    return result


def _frame(geometry: dict[str, Any]) -> gp_Ax3:
    origin, axis = _xyz(geometry["origin"]), _xyz(geometry["axis"])
    direction = gp_Dir(*axis)
    if "basis_u" in geometry:
        return gp_Ax3(gp_Pnt(*origin), direction, gp_Dir(*_xyz(geometry["basis_u"])))
    return gp_Ax3(gp_Pnt(*origin), direction)


@kernel_operation
def primitive_surface(geometry: dict[str, Any]) -> Geom_Surface:
    """Construct a kernel surface from an adapted Scansor primitive chart."""
    kind = geometry["kind"]
    if kind == "plane":
        axis = _xyz(geometry["axis"])
        origin = axis * float(geometry["offset"])
        return Geom_Plane(_frame({**geometry, "origin": origin}))
    radius, slope = float(geometry["radius"]), float(geometry.get("slope", 0))
    if not np.isfinite([radius, slope]).all():
        raise ValueError("kernel surface radius and taper must be finite")
    if kind == "cylinder" or (kind == "cone" and slope == 0):
        if radius <= 0:
            raise ValueError("cylinder requires a positive finite radius")
        return Geom_CylindricalSurface(_frame(geometry), radius)
    if kind == "cone":
        if radius >= 0:
            # Keep a well-conditioned chart for shallow tapers. Its distant apex
            # is irrelevant to this local patch and would amplify cancellation.
            return Geom_ConicalSurface(_frame(geometry), math.atan(slope), radius)
        # Scansor permits a nonpositive reference radius with positive support
        # elsewhere. Anchor OCCT at the apex instead of passing a negative radius.
        apex = _xyz(geometry["origin"]) - radius / slope * _xyz(geometry["axis"])
        return Geom_ConicalSurface(
            _frame({**geometry, "origin": apex}), math.atan(slope), 0.0
        )
    raise ValueError("unsupported kernel primitive")


@kernel_operation
def circular_intersection(
    plane: dict[str, Any], side: dict[str, Any], segments: int
) -> dict[str, Any]:
    """Intersect existing supported primitives, requiring one exact circle."""
    intersection = GeomAPI_IntSS(
        primitive_surface(plane), primitive_surface(side), KERNEL_TOLERANCE
    )
    if not intersection.IsDone():
        raise ValueError("OCP surface intersection failed")
    if intersection.NbLines() != 1:
        raise ValueError("OCP intersection did not produce one circular edge")
    curve = intersection.Line(1)
    curve = curve.BasisCurve() if isinstance(curve, Geom_TrimmedCurve) else curve
    if not isinstance(curve, Geom_Circle):
        raise ValueError("OCP intersection is not an exact circular edge")
    circle = curve.Circ()
    center = circle.Location()
    frame = circle.Position()
    orientation = 1 if np.dot(frame.Direction().Coord(), plane["axis"]) > 0 else -1
    points = [
        curve.Value(orientation * i * 2 * math.pi / segments) for i in range(segments)
    ]
    positions = [[p.X(), p.Y(), p.Z()] for p in points]
    if not np.isfinite(positions).all():
        raise ValueError("intersection preview coordinates overflow")
    # Keep cutting-plane orientation for positive/negative retained-side intent;
    # the curve's parameter frame itself need not share that orientation.
    return {
        "kind": "circle",
        "center_display": [center.X(), center.Y(), center.Z()],
        "axis_display": list(plane["axis"]),
        "basis_u_display": list(frame.XDirection().Coord()),
        "basis_v_display": (
            orientation * np.asarray(frame.YDirection().Coord())
        ).tolist(),
        "radius": circle.Radius(),
        "preview": {"positions": np.asarray(positions).ravel().tolist(), "indices": []},
    }


def _checked_face(builder: BRepBuilderAPI_MakeFace) -> TopoDS_Face:
    if not builder.IsDone():
        raise ValueError("OCP could not construct the face")
    face = builder.Face()
    if face.IsNull() or not BRepCheck_Analyzer(face).IsValid():
        raise ValueError("OCP constructed an invalid face")
    return face


# Shared kernel operations used by the interval and general-loop constructors.
geometry_frame = _frame
checked_face = _checked_face


def _circle_wire(geometry: dict[str, Any], radius: float) -> TopoDS_Wire:
    frame = _frame(geometry)
    circle = gp_Circ(
        gp_Ax2(frame.Location(), frame.Direction(), frame.XDirection()), radius
    )
    edge = BRepBuilderAPI_MakeEdge(circle)
    if not edge.IsDone():
        raise ValueError("OCP could not construct a circular boundary")
    wire = BRepBuilderAPI_MakeWire(edge.Edge())
    if not wire.IsDone():
        raise ValueError("OCP could not construct a circular wire")
    return wire.Wire()


@kernel_operation
def face_from_record(face: dict[str, Any]) -> TopoDS_Face:
    """Build one explicitly finite physical face; preview bounds are never read."""
    if not face.get("bounded", False):
        raise ValueError("trimmed face export requires finite explicit bounds")
    geometry, kind = face["geometry"], face["surface_kind"]
    _ = _xyz(geometry["origin"])
    frame = np.stack([_xyz(geometry[k]) for k in ("axis", "basis_u", "basis_v")])
    if not np.allclose(frame @ frame.T, np.eye(3), atol=1e-10, rtol=0):
        raise ValueError("trimmed face requires finite orthonormal geometry")
    interval = face["bounds"].get("radial" if kind == "plane" else "axial")
    if interval is None or len(interval) != 2 or any(v is None for v in interval):
        raise ValueError("trimmed face requires finite explicit bounds")
    lower, upper = map(float, interval)
    if not np.isfinite([lower, upper, upper - lower]).all() or upper <= lower:
        raise ValueError("trimmed face bounds must be finite and ordered")
    if kind == "plane":
        if lower < 0:
            raise ValueError("trimmed plane requires nonnegative radial bounds")
        plane = gp_Pln(_frame(geometry))
        builder = BRepBuilderAPI_MakeFace(plane, _circle_wire(geometry, upper), True)
        if lower > 0:
            inner = _circle_wire(geometry, lower)
            inner.Reverse()
            builder.Add(inner)
        return _checked_face(builder)
    if kind not in ("cylinder", "cone"):
        raise ValueError("unsupported trimmed surface type")
    radius, slope = float(geometry["radius"]), float(geometry["slope"])
    radii = radius + slope * np.array([lower, upper])
    if (
        not np.isfinite([radius, slope, *radii]).all()
        or np.any(radii <= 0)
        or (kind == "cylinder" and slope != 0)
    ):
        raise ValueError("trimmed lateral bounds must avoid the apex")
    surface = primitive_surface({**geometry, "kind": kind})
    if kind == "cone" and slope != 0:
        reference_axial = -radius / slope if radius < 0 else 0.0
        factor = math.cos(math.atan(slope))
        lower, upper = (
            (lower - reference_axial) / factor,
            (upper - reference_axial) / factor,
        )
    return _checked_face(
        BRepBuilderAPI_MakeFace(
            surface, 0.0, 2 * math.pi, lower, upper, KERNEL_TOLERANCE
        )
    )


@kernel_operation
def tessellate_face(face: TopoDS_Face, scale: float) -> dict[str, Any]:
    """Display-only OCP triangulation, independent of observation sampling."""
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("face preview scale must be positive and finite")
    # OCCT rejects deflections below its absolute confusion threshold. Normalize
    # only the derived display shape so micron and kilometer models have the
    # same angular/chord quality without changing physical geometry or tolerance.
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box, False)
    center = (
        np.asarray(box.CornerMin().Coord()) + np.asarray(box.CornerMax().Coord())
    ) / 2
    normalize = np.eye(4)
    normalize[:3, :3] /= scale
    normalize[:3, 3] = -center / scale
    failure = "OCP face preview meshing failed"
    for deflection in (0.001, 0.0001, 0.00001):
        # Thin curved cells can collapse at the default display deflection.
        # Retry only unsuccessful meshes, using a fresh derived copy so a failed
        # triangulation cache cannot affect the next attempt or physical face.
        display = transform_shape(face, normalize)
        mesh = BRepMesh_IncrementalMesh(display, deflection, False, 0.1, False)
        if not mesh.IsDone() or mesh.GetStatusFlags() & int(IMeshData_Failure):
            failure = "OCP face preview meshing failed"
            continue
        location = TopLoc_Location()
        # A uniform transform of a face is still a face; downcast for the binding.
        triangulation = BRep_Tool.Triangulation_s(TopoDS.Face(display), location)
        if triangulation and triangulation.NbTriangles() > 0:
            break
        failure = "OCP face preview has no triangles"
    else:
        raise ValueError(failure)
    transform = location.Transformation()
    points = [
        triangulation.Node(i).Transformed(transform)
        for i in range(1, triangulation.NbNodes() + 1)
    ]
    positions = np.array([[p.X(), p.Y(), p.Z()] for p in points]) * scale + center
    triangles = (
        np.array(
            [
                triangulation.Triangle(i).Get()
                for i in range(1, triangulation.NbTriangles() + 1)
            ]
        )
        - 1
    )
    if face.Orientation() == TopAbs_REVERSED:
        triangles = triangles[:, ::-1]
    if not np.isfinite(positions).all():
        raise ValueError("face preview coordinates overflow")
    return {
        "positions": positions.ravel().tolist(),
        "indices": triangles.ravel().tolist(),
    }


@kernel_operation
def surface_patch(surface: dict[str, Any], positions: Array) -> TopoDS_Face:
    """Existing observation-bounded export patches, not inferred physical faces."""
    ids = surface["ids"]
    if not ids:
        raise ValueError("surface has no selected observations")
    selected = np.asarray(positions, dtype=float)[ids]
    if not np.isfinite(selected).all():
        raise ValueError("surface observations must be finite")
    kind = surface["kind"]
    if kind == "sphere":
        parameters = np.asarray(surface["parameters"], dtype=float)
        if (
            parameters.shape != (4,)
            or not np.isfinite(parameters).all()
            or parameters[3] <= 0
        ):
            raise ValueError("sphere parameters require a positive radius")
        sphere = Geom_SphericalSurface(
            gp_Ax3(gp_Pnt(*parameters[:3]), gp_Dir(0, 0, 1)), float(parameters[3])
        )
        return _checked_face(BRepBuilderAPI_MakeFace(sphere, KERNEL_TOLERANCE))
    geometry = primitive(surface)
    axis = np.asarray(geometry["axis"])
    u, v = basis(axis)
    if kind == "plane":
        uv = selected @ np.column_stack([u, v])
        lower, upper = uv.min(axis=0), uv.max(axis=0)
        if np.any(upper - lower <= 1e-12):
            raise ValueError("plane patch needs two-dimensional coverage")
        pad = (upper - lower) * 0.05
        plane = primitive_surface({**geometry, "basis_u": u})
        return _checked_face(
            BRepBuilderAPI_MakeFace(
                plane,
                float(lower[0] - pad[0]),
                float(upper[0] + pad[0]),
                float(lower[1] - pad[1]),
                float(upper[1] + pad[1]),
                KERNEL_TOLERANCE,
            )
        )
    origin = np.asarray(geometry["origin"])
    axial = (selected - origin) @ axis
    lower, upper = float(axial.min()), float(axial.max())
    if upper - lower <= 1e-12:
        raise ValueError("lateral patch needs axial coverage")
    pad = (upper - lower) * 0.05
    domain = surface["axial_domain"]
    lower, upper = max(domain[0], lower - pad), min(domain[1], upper + pad)
    if upper <= lower:
        raise ValueError("surface observations lie outside axial support")
    return face_from_record(
        {
            "bounded": True,
            "surface_kind": kind,
            "geometry": {**geometry, "basis_u": u.tolist(), "basis_v": v.tolist()},
            "bounds": {"axial": [lower, upper]},
        }
    )


@kernel_operation
def transform_shape(shape: TopoDS_Shape, matrix: Array) -> TopoDS_Shape:
    """Transform a patch by the existing rigid/mirror/uniform-scale contract."""
    values = np.asarray(matrix, dtype=float)
    if (
        values.shape != (4, 4)
        or not np.isfinite(values).all()
        or not np.allclose(values[3], [0, 0, 0, 1], atol=1e-12, rtol=0)
    ):
        raise ValueError("shape transform must be a finite affine 4-by-4 matrix")
    gram = values[:3, :3].T @ values[:3, :3]
    scale_squared = float(np.trace(gram) / 3)
    if scale_squared <= 0 or not np.allclose(
        gram, scale_squared * np.eye(3), atol=1e-12 * scale_squared, rtol=1e-10
    ):
        raise ValueError("shape transform requires rigid or uniform-scale geometry")
    transform = gp_Trsf()
    transform.SetValues(*map(float, values[:3].ravel()))
    builder = BRepBuilderAPI_Transform(shape, transform, True)
    if not builder.IsDone():
        raise ValueError("OCP shape transform failed")
    result = builder.Shape()
    if result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
        raise ValueError("OCP shape transform produced invalid geometry")
    return result
