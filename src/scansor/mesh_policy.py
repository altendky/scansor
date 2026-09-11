"""The resolved triangle-area-mean-one-v1 policy and semantic control records."""

from __future__ import annotations

import numpy as np

from scansor.mesh_controls import (
    Control,
    control_artifact,
    control_id,
    implementation_inventory,
)
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import NumericProfileError

POLICY = "triangle-area-mean-one-v1"


def contribution_inventory(
    *,
    import_id: str,
    eligible: int,
    request: dict[str, Control],
    summary: dict[str, Control],
    artifacts: list[Control],
    row_digest: str,
) -> dict[str, Control]:
    return {
        "revision": "mesh-contribution-v1",
        "status": "complete" if eligible else "complete-no-eligible-points",
        "import_id": import_id,
        "request": control_artifact("request.json", request),
        "summary": control_artifact("summary.json", summary),
        "columns": artifacts,
        "row_digests": {"vertices": row_digest},
    }


def contribution_request(
    import_id: str, *, configuration: dict[str, Control] | None = None
) -> dict[str, Control]:
    if (
        type(import_id) is not str
        or len(import_id) != 64
        or any(c not in "0123456789abcdef" for c in import_id)
    ):
        raise MeshImportError(
            "structure", "contribution-request", "invalid import identity"
        )
    if configuration is not None and configuration != {}:
        raise MeshImportError(
            "structure", "contribution-request", "v1 configuration must be empty"
        )
    return {
        "revision": "mesh-contribution-request-v1",
        "import_id": import_id,
        "policy": POLICY,
        "configuration": {},
        "policy_implementation": control_id(implementation_inventory("policy")),
    }


def contribution_status(
    vertex_status: np.ndarray, references: np.ndarray, areas: np.ndarray
) -> np.ndarray:
    if (
        vertex_status.dtype != np.dtype("u1")
        or vertex_status.ndim != 1
        or references.dtype != np.dtype("<u8")
        or references.shape != vertex_status.shape
        or areas.dtype != np.dtype("<f8")
        or areas.shape != vertex_status.shape
        or np.any(vertex_status > 1)
    ):
        raise MeshImportError(
            "structure",
            "contribution-dispositions",
            "invalid vertex column shapes or encodings",
        )
    if not np.all(np.isfinite(areas)) or np.any(np.signbit(areas)):
        raise NumericProfileError(
            "vertex areas require finite nonnegative values and +0"
        )
    if np.any(((vertex_status != 0) | (references == 0)) & (areas != 0)):
        raise MeshImportError(
            "integrity",
            "contribution-dispositions",
            "excluded source vertex has positive area",
        )
    status = np.zeros(len(areas), dtype="u1")
    status[areas == 0] = 3
    status[references == 0] = 2
    status[vertex_status != 0] = 1
    return status
