"""Display-only identities and legends; never rekey authoritative mesh stages."""

from __future__ import annotations

import hashlib
from importlib import resources

import numpy as np

from scansor.mesh_artifacts import ContributionArtifact
from scansor.mesh_controls import Control, control_artifact, control_id
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_display_ply import CORNER_FIELDS, MAX_VERTICES, PROFILE, SCALAR_FIELDS
from scansor.mesh_display_staging import summary_count
from scansor.mesh_numeric import float_bits

ASSOCIATION_REVISION = (
    "all-finite-source-vertices-usable-source-faces-rejected-face-corner-order-v1"
)


def display_implementation() -> dict[str, Control]:
    # A separate inventory avoids changing the generator/import/policy identities
    # when adding display features. SQL/storage tuning is execution provenance;
    # changes to selection/order/remapping must revise ASSOCIATION_REVISION.
    names = (
        "_plyio/format.py",
        "_plyio/stream.py",
        "mesh_controls.py",
        "mesh_numeric.py",
        "mesh_display_numeric.py",
        "mesh_display_ply.py",
        "mesh_display_records.py",
        "mesh_display_semantics.py",
        "serialization.py",
    )
    root = resources.files("scansor")
    files: list[Control] = [
        {
            "name": name,
            "sha256": hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest(),
        }
        for name in names
    ]
    return {
        "revision": "mesh-display-implementation-v1",
        "files": files,
        "dependencies": {"numpy": np.__version__},
    }


def display_legend(
    data: ContributionArtifact,
    *,
    corners: int,
    maximum: float,
    transform: DisplayTransform,
    vertex_loss: int,
    corner_loss: int,
) -> dict[str, Control]:
    source = data.imported
    vertices = summary_count(
        source.summary, "vertex_category_counts", "finite-position"
    )
    faces = summary_count(source.summary, "face_category_counts", "usable")
    rejected = 3 * (source.faces - faces)
    invalid = int(str(source.summary["out_of_range_source_corners"]))
    return {
        "revision": "mesh-display-legend-v1",
        "profile": PROFILE,
        "source_id": source.source_id,
        "import_id": source.identity,
        "contribution_id": data.identity,
        "source_population": {"vertices": source.vertices, "faces": source.faces},
        "display_population": {
            "vertices_per_main_view": vertices,
            "usable_faces_per_main_view": faces,
            "rejected_corners": corners,
        },
        "omissions": {
            "nonfinite_source_vertices": source.vertices - vertices,
            "rejected_corners_out_of_range": invalid,
            "rejected_corners_nonfinite_position": rejected - invalid - corners,
        },
        "omission_access": "Authoritative source vertex and face row queries retain all nondisplayable records; no coordinates are invented.",
        "contribution_categories": data.summary["category_counts"],
        "normal_categories": source.summary["normal_category_counts"],
        "validity_colors": {
            "eligible": [40, 170, 80],
            "isolated": [255, 165, 0],
            "no-usable-area": [220, 50, 50],
            "nonfinite-position": "not spatially displayable",
        },
        "rejected_face_colors": {
            "1": [180, 0, 180],
            "2": [220, 50, 50],
            "3": [255, 165, 0],
            "4": [80, 120, 220],
        },
        "weight_ramp": {
            "measure": "raw accumulated vertex area divided by complete eligible maximum",
            "maximum_binary64_bits": float_bits(maximum),
            "formula": "r=RN(area/max); t=round-ties-even(RN(255*r)); RGB=(t,t,255-t)",
            "excluded_color": [128, 128, 128],
            "clipping": False,
            "percentiles": False,
            "logarithmic": False,
        },
        "fields": {
            "coordinates": "source float32 widened to float64, then the recorded display-only transform",
            "scalar_vertex_status": "0=finite-position; nonfinite positions have no display location",
            "scalar_normal_status": "0=absent, 1=finite-nonzero, 2=zero-vector, 3=nonfinite-vector; independent of color",
            "scalar_contribution_status": "0=eligible, 2=isolated, 3=no-usable-area",
            "scalar_raw_area": "authoritative source vertex area, in unknown source-coordinate squared units",
            "scalar_weight": "dimensionless triangle-area-mean-one-v1 weight; not a solver weight",
            "source_digits": "four successive 16-bit source row ID digits, least significant first, each exactly representable in binary32",
            "scalar_source_corner": "original zero-based corner 0, 1 or 2 of the rejected source face",
            "scalar_face_status": "1=index-out-of-range, 2=nonfinite-position, 3=repeated-index, 4=zero-computed-area",
            "rejected_vertex_values": "area, weight and vertex statuses still describe the source vertex, which may also belong to usable faces",
        },
        "main_scalar_fields": list(SCALAR_FIELDS),
        "additional_rejected_scalar_fields": list(CORNER_FIELDS),
        "source_normals": "retained in authoritative data; omitted from PLY views to avoid viewer normalization implying measurement preservation",
        "transform": transform.record(),
        "transform_roundtrip_mismatch_rows": {
            "finite_source_vertices": vertex_loss,
            "displayable_rejected_corners": corner_loss,
        },
        "precision": {
            "export_coordinates": "binary64",
            "export_area_weight": "binary64",
            "export_ids_statuses": "binary32 exact integers",
            "viewer_preservation": "requires a separate measured viewer round-trip report",
        },
        "topology": "Every usable source face retains winding and multiplicity after index remapping; source maps bind original export row order.",
        "duplicate_face_identity": "A resave that reorders indistinguishable duplicate faces cannot recover their individual source face IDs from vertex triples alone.",
        "limits": {
            "maximum_vertices_per_view": MAX_VERTICES,
            "face_indices": "signed little-endian int32",
        },
        "empty_views": [
            name
            for name, count in (
                ("validity", vertices),
                ("weights", vertices),
                ("rejected-face-corners", corners),
            )
            if count == 0
        ],
        "authority": "Display artifacts and viewer edits never replace import or contribution data; colors do not indicate physical error severity.",
    }


def display_inventory(
    data: ContributionArtifact, legend: dict[str, Control], files: list[Control]
) -> dict[str, Control]:
    implementation = display_implementation()
    return {
        "revision": "mesh-display-v1",
        "profile": PROFILE,
        "status": "complete",
        "source_id": data.imported.source_id,
        "import_id": data.imported.identity,
        "contribution_id": data.identity,
        "association_revision": ASSOCIATION_REVISION,
        "implementation": implementation,
        "implementation_id": control_id(implementation),
        "legend": control_artifact("legend.json", legend),
        "files": files,
    }
