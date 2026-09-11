"""Bounded profile dispositions; structural/resource failures never become rows."""

from __future__ import annotations

import numpy as np

from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import NumericProfileError, face_areas


def vertex_status(xyz: np.ndarray) -> np.ndarray:
    if xyz.dtype != np.dtype("<f4") or xyz.ndim != 2 or xyz.shape[1] != 3:
        raise MeshImportError(
            "structure", "vertex-dispositions", "expected canonical XYZ binary32 rows"
        )
    words = xyz.view("<u4") & np.uint32(0x7FFFFFFF)
    return np.any(words >= 0x7F800000, axis=1).astype("u1")


def normal_status(normals: np.ndarray | None, rows: int) -> np.ndarray:
    if type(rows) is not int or rows < 0:
        raise MeshImportError("structure", "normal-dispositions", "invalid row count")
    if normals is None:
        return np.zeros(rows, dtype="u1")
    if normals.shape != (rows, 3):
        raise MeshImportError(
            "structure", "normal-dispositions", "normal count differs from vertex count"
        )
    invalid = vertex_status(normals)
    words = normals.view("<u4") & np.uint32(0x7FFFFFFF)
    result = np.ones(rows, dtype="u1")
    result[np.all(words == 0, axis=1)] = 2
    result[invalid != 0] = 3
    return result


def face_dispositions(
    indices: np.ndarray, corners: np.ndarray, *, vertices: int, start: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Caller supplies one coordinate triple per source corner, in source order.

    Invalid indices may have placeholder coordinates; precedence rejects their
    whole face before geometry. Repeated faces retain their source multiplicity.
    S4 owns complete reference counts and ordered contribution accumulation.
    """
    if (
        indices.dtype != np.dtype("<i4")
        or indices.ndim != 2
        or indices.shape[1] != 3
        or corners.dtype != np.dtype("<f4")
        or corners.shape != (len(indices), 3, 3)
        or type(vertices) is not int
        or not 1 <= vertices <= 2**31
        or type(start) is not int
        or start < 0
    ):
        raise MeshImportError(
            "structure", "face-dispositions", "invalid bounded face input"
        )
    status = np.zeros(len(indices), dtype="u1")
    areas = np.zeros(len(indices), dtype="<f8")
    status[np.any((indices < 0) | (indices >= vertices), axis=1)] = 1
    invalid = np.any(vertex_status(corners.reshape(-1, 3)).reshape(-1, 3), axis=1)
    status[(status == 0) & invalid] = 2
    repeated = (
        (indices[:, 0] == indices[:, 1])
        | (indices[:, 1] == indices[:, 2])
        | (indices[:, 0] == indices[:, 2])
    )
    status[(status == 0) & repeated] = 3
    candidate = np.flatnonzero(status == 0)
    if len(candidate):
        try:
            computed = face_areas(corners[candidate])
        except NumericProfileError as error:
            # Locate the source face only on the exceptional path, so the error
            # reports the actual row rather than an arbitrary batch boundary.
            for index in candidate:
                try:
                    _ = face_areas(corners[index : index + 1])
                except NumericProfileError as individual:
                    raise MeshImportError(
                        "numeric-profile-failure",
                        "face-dispositions",
                        str(individual),
                        row=start + int(index),
                    ) from individual
            raise MeshImportError(
                "numeric-profile-failure", "face-dispositions", str(error), row=start
            ) from error
        areas[candidate] = computed
        status[candidate[computed == 0]] = 4
    return status, areas
