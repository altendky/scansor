"""Explicit portable migration for the one captured legacy nozzle reference.

The frozen record is copied into migrated archives. Restoration reads the selected
member and this validated record; it never reads repository fixture metadata.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from experiments.nozzle_session import NozzleSession, NozzleWorkspace
from experiments.run_nozzle_cylinder import NozzleExample, selection_azimuth_mask
from experiments.scan_sources import PathInput, ReplayableInput
from experiments.scan_workspace import (
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    prepare_scan,
)

_KNOWN: dict[str, Any] = json.loads(
    '{"format":"scansor-legacy-nozzle-reference-v1","source_sha256":"537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358","reference_sha256":"3f9c48e04a1ad9f31a202ef1cca6a5afce4fbba810aaf9d02e90326cf9d9d676","source_bytes":1250160,"name":"nozzle-bayonette-simplified","model_sha256":"ff384fbabbc5ad82e7b3fa13e4abceeacfdb927cac03e6f63b61167d9e57db02","support":[-2.0,5.0],"selection":{"axis_seed":{"center":[-0.17851424343699349,-1.1589745382023346,26.523937927224004],"axis":[0.000675135678632837,-0.015914832068988666,0.9998731231071426],"meaning":"All-area point covariance smallest-eigenvector orientation for inspection only; not fitted cylinder axis."},"axial_range":[-0.6,1.6],"radius_range":[8.9,10],"minimum_outward_normal_dot":0.7,"maximum_abs_axial_normal_dot":0.35,"status":"exploratory fixed selection on simplified mesh","source_sha256":"537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358","vertex_ids_file":"outer-band-vertex-ids.txt","vertex_ids_sha256":"6a34b965a578b3fa94dcad95f3e4236ea804bf22df06bbcbbbf863fd903aeada","selected_vertices":1261,"indexing":"zero-based source vertex row IDs","weighting":"one third of each usable incident triangle area, computed on the entire simplified mesh","meaning":"Same pre-fit geometric gates as the original band; different observations after simplification. No residual trimming. Unknown units.","fit_frame":{"origin":[-0.17817602244445682,-1.1669473508810067,27.02484180597154],"columns":[[0.9999997720381432,1.0746031922990095e-05,0.000675135678632837],[0.0,0.9998733510401283,-0.015914832068988666],[-0.0006752211946948284,0.015914828441013995,0.9998731231071426]]},"initial_parameters":[0,0,0,0,9.4]},"plane_selection":{"status":"user-approved fixed top-face selection","axial_range":[4.1,4.6],"radius_range":[8.4,9.2],"minimum_axial_normal_dot":0.9,"selected_vertices":642,"source_sha256":"537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358","axis_seed":{"center":[-0.3171482160971745,-1.2705574443335363,27.023286496487028],"axis":[-0.027912834810700574,-0.03379293992759593,0.9990389936653525],"meaning":"Fixed cylinder-only fitted axis used to define selection gates before joint fitting; never updated during the joint fit."},"vertex_ids_file":"top-face-vertex-ids.txt","vertex_ids_sha256":"53e3c3a161472b72c6558031377a3c59ddf67886c1edbfddabd6b21711cab224","indexing":"zero-based source vertex row IDs","weighting":"one third of each usable incident triangle area, computed on the entire simplified mesh","meaning":"Upper annular face, excluding inner and outer edge transitions. User confirmed the displayed gold selection. No residual trimming. Unknown source units."}}'
)
_FILENAME = "nozzle-bayonette-simplified.ply"


def _matches_captured(value: object, captured: object) -> bool:
    if isinstance(captured, dict):
        if not isinstance(value, dict):
            return False
        actual = cast(dict[str, object], value)
        expected = cast(dict[str, object], captured)
        return actual.keys() == expected.keys() and all(
            _matches_captured(actual[key], item) for key, item in expected.items()
        )
    if isinstance(captured, list):
        if not isinstance(value, list):
            return False
        actual_items = cast(list[object], value)
        expected_items = cast(list[object], captured)
        return len(actual_items) == len(expected_items) and all(
            _matches_captured(item, fixed)
            for item, fixed in zip(actual_items, expected_items, strict=True)
        )
    if isinstance(captured, (int, float)):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == captured
        )
    return type(value) is type(captured) and value == captured


def _checked(record: dict[str, Any]) -> dict[str, Any]:
    if not _matches_captured(record, _KNOWN):
        raise ScanImportError(
            "The portable legacy reference is unsupported or changed.",
            "invalid_legacy_reference",
        )
    # JavaScript JSON round trips can spell a captured 0.0 as 0. The known
    # profile restores its frozen Python numeric representation before checking
    # the historical reference hash; equal values never select a new frame.
    canonical = deepcopy(_KNOWN)
    selection = canonical["selection"]
    digest = hashlib.sha256(
        json.dumps(
            {"selection": selection, "model": canonical["model_sha256"]}, sort_keys=True
        ).encode()
    ).hexdigest()
    if digest != canonical["reference_sha256"]:
        raise ScanImportError(
            "The legacy reference metadata does not reproduce its binding.",
            "invalid_legacy_reference",
        )
    return canonical


def migrate_known_legacy(
    actions: dict[str, Any], source: ReplayableInput
) -> dict[str, Any]:
    declarations = [
        node for node in actions.get("nodes", []) if node.get("operation") == "source"
    ]
    if len(declarations) != 1:
        raise ScanImportError(
            "The legacy archive must identify one known source.",
            "invalid_legacy_reference",
        )
    declaration = declarations[0]
    if (
        declaration.get("descriptor") is not None
        or declaration.get("virtual_scan") is not None
        or declaration.get("source_sha256") != _KNOWN["source_sha256"]
        or declaration.get("reference_sha256") != _KNOWN["reference_sha256"]
        or source.byte_length != _KNOWN["source_bytes"]
    ):
        raise ScanImportError(
            "This unversioned legacy source/reference pair is not recognized.",
            "invalid_legacy_reference",
        )
    return _checked(_KNOWN)


def export_legacy_project(
    workspace: NozzleWorkspace, original: ReplayableInput | None = None
) -> tuple[ReplayableInput, dict[str, Any]]:
    if (
        workspace.source_sha256 != _KNOWN["source_sha256"]
        or workspace.reference_sha256 != _KNOWN["reference_sha256"]
    ):
        raise ScanImportError(
            "Only the explicitly captured legacy nozzle can be saved by this migration.",
            "invalid_legacy_reference",
        )
    if original is None:
        # This path is authority only for an already explicitly loaded packaged
        # example. Portable archive callers supply their retained member instead.
        path = workspace.data.selection_path.parent.parent / _FILENAME
        try:
            original = PathInput(path, _FILENAME)
        except OSError as error:
            raise ScanImportError(
                "The legacy project lost its replayable original source.",
                "source_unavailable",
            ) from error
    if original.byte_length != _KNOWN["source_bytes"]:
        raise ScanImportError(
            "The legacy replay source length changed.", "source_mismatch"
        )
    return original, _checked(_KNOWN)


def _ids(
    workspace: ScanWorkspace, selection: dict[str, Any], *, lateral: bool
) -> NDArray[np.int64]:
    axis = np.asarray(selection["axis_seed"]["axis"], dtype=np.float64)
    q = workspace.data.xyz - np.asarray(
        selection["axis_seed"]["center"], dtype=np.float64
    )
    axial = q @ axis
    radial = q - axial[:, None] * axis
    radius = np.linalg.norm(radial, axis=1)
    mask = (
        (axial >= selection["axial_range"][0])
        & (axial <= selection["axial_range"][1])
        & (radius >= selection["radius_range"][0])
        & (radius <= selection["radius_range"][1])
        & selection_azimuth_mask(workspace.data.xyz, selection)
        & (workspace.data.weights > 0)
    )
    if lateral:
        outward = np.divide(
            np.sum(workspace.data.normals * radial, axis=1),
            radius,
            out=np.zeros(len(radius)),
            where=radius > 0,
        )
        mask &= (outward >= selection["minimum_outward_normal_dot"]) & (
            np.abs(workspace.data.normals @ axis)
            <= selection["maximum_abs_axial_normal_dot"]
        )
    else:
        mask &= workspace.data.normals @ axis >= selection["minimum_axial_normal_dot"]
    result = np.asarray(np.flatnonzero(mask), dtype=np.int64)
    encoded = "".join(str(int(value)) + "\n" for value in result).encode("ascii")
    if (
        len(result) != selection["selected_vertices"]
        or hashlib.sha256(encoded).hexdigest() != selection["vertex_ids_sha256"]
    ):
        raise ScanImportError(
            "Legacy source observations do not reproduce the captured selections.",
            "source_mismatch",
        )
    result.flags.writeable = False
    return result


def legacy_workspace(
    source: ReplayableInput,
    record: dict[str, Any],
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> NozzleWorkspace:
    reference = _checked(record)
    prepared = prepare_scan(
        source, source.filename, limits, progress=progress, cancelled=cancelled
    )
    if (
        prepared.source_sha256 != reference["source_sha256"]
        or source.byte_length != reference["source_bytes"]
    ):
        raise ScanImportError(
            "The legacy source differs from its portable reference.", "source_mismatch"
        )
    selection = reference["selection"]
    lateral = _ids(prepared, selection, lateral=True)
    plane = _ids(prepared, reference["plane_selection"], lateral=False)
    # Use the existing frontend-independent legacy evaluator with explicitly
    # reconstructed state, bypassing its directory-loading constructor only.
    workspace = object.__new__(NozzleWorkspace)
    workspace.name = reference["name"]
    workspace.data = NozzleExample(
        prepared.data.xyz,
        prepared.data.normals,
        prepared.data.triangles,
        prepared.data.weights,
        lateral,
        selection,
        Path("archive-reference/selections/outer-band.json"),
        prepared.diagnostics.zero_area_faces,
    )
    workspace.model_sha256 = reference["model_sha256"]
    workspace.support = cast(tuple[float, float], tuple(reference["support"]))
    workspace.origin = np.asarray(selection["fit_frame"]["origin"], dtype=np.float64)
    workspace.frame = np.asarray(selection["fit_frame"]["columns"], dtype=np.float64)
    workspace.local = (prepared.data.xyz - workspace.origin) @ workspace.frame
    for array in (workspace.origin, workspace.frame, workspace.local):
        array.flags.writeable = False
    workspace.default = NozzleSession(
        source_sha256=reference["source_sha256"],
        model_sha256=workspace.model_sha256,
        lateral_ids=lateral.tolist(),
        plane_ids=plane.tolist(),
    )
    if workspace.reference_sha256 != reference["reference_sha256"]:
        raise ScanImportError(
            "Portable legacy restoration changed its reference binding.",
            "invalid_legacy_reference",
        )
    return workspace
