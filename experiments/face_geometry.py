"""Dispatch derived physical face records to their kernel construction paths."""

from typing import Any

from OCP.TopoDS import TopoDS_Face

from experiments.general_face_geometry import face_from_record as loop_face
from experiments.ocp_geometry import face_from_record as interval_face


def face_from_record(record: dict[str, Any]) -> TopoDS_Face:
    if "arrangement" in record.get("bounds", {}):
        from experiments.face_arrangement import face_from_record as arranged_face

        return arranged_face(record)
    if "loops" in record.get("bounds", {}) or "cuts" in record.get("bounds", {}):
        return loop_face(record)
    return interval_face(record)
