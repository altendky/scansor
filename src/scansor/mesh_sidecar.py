"""Replaceable, bounded rsInfo interpretation; original bytes remain provenance.

No parser object escapes this adapter. Attributes are exporter strings only;
none establishes a transform, coordinate frame, physical scale, or calibration.
"""

from __future__ import annotations

import io
import re
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from scansor.mesh_controls import Control, encode_control

MAX_SIDECAR_BYTES = 1024 * 1024
MAX_ELEMENTS = 4096
MAX_DEPTH = 32
REVISION = "realityscan-rsinfo-strings-v1"

# Observed field names, deliberately not a claim of a vendor transform schema.
_FIELDS = {
    "Model": frozenset(
        [
            "componentId",
            "exportCoordinateSystemType",
            "globalCoordinateSystem",
            "globalCoordinateSystemName",
            "globalCoordinateSystemWkt",
            "transformToModel",
        ]
    ),
    "ModelExport": frozenset(
        [
            "authorComment",
            "classificationLayer",
            "embedTextures",
            "exportBinary",
            "exportCameras",
            "exportCamerasAsModelPart",
            "exportCoordinateSystemType",
            "exportInfoFile",
            "exportMaterials",
            "exportModelByParts",
            "exportRandomPartColor",
            "exportTextureAlpha",
            "exportToOneTexture",
            "exportTriangleStrips",
            "exportTriangles",
            "exportVertexColorSpace",
            "exportVertexColors",
            "exportVertexNormals",
            "exportVertices",
            "exportedLayerCount",
            "formatAndVersionUID",
            "meshColor",
            "moreDetails",
            "normalFlip",
            "normalRange",
            "normalSpace",
            "numberAsciiFormatting",
            "oneTextureMaxSide",
            "oneTextureUsePow2TexSide",
            "settingsAnchor",
            "settingsRotation",
            "settingsScale",
            "shrinkTextures",
            "tileType",
        ]
    ),
    "CalibrationExportSettings": frozenset(
        [
            "exportDisabled",
            "exportImages",
            "exportUndistorted",
            "undistBackColor",
            "undistCutOut",
            "undistFitMode",
            "undistMaxPixels",
            "undistPrincipalMode",
            "undistResMode",
            "undistortCalibration",
            "undistortCustomHeight",
            "undistortCustomWidth",
            "undistortDownscaleFactor",
            "undistortImageLayerType",
            "undistortImageNameSuffix",
            "undistortImagesExtension",
            "undistortImagesWicFormat",
            "undistortImagesWicPixlFormat",
            "undistortNamingConvention",
        ]
    ),
}
_HEADER_FIELDS = frozenset(("magic", "version"))
_DECLARATION = re.compile(r"^<\?xml(?=\s)[^?]*\?>")
_ENCODING = re.compile(r"encoding\s*=\s*(['\"])(.*?)\1")


def _record(
    status: str, *, fields: Control = None, reason: str | None = None
) -> dict[str, Control]:
    result: dict[str, Control] = {
        "revision": REVISION,
        "status": status,
        "fields": {} if fields is None else fields,
        "reason": reason,
    }
    _ = encode_control(result)
    return result


def interpret_sidecar(
    raw: bytes | None, *, byte_count: int | None = None
) -> dict[str, Control]:
    """Pass None for absence or an oversized retained snapshot (with its length).

    Oversized files are hashed/copied by the source layer; no full read is needed
    here. Parsing uses incremental events with depth/element bounds, in addition
    to the byte limit. Unknown material remains in the original source snapshot.
    """
    if byte_count is not None and (type(byte_count) is not int or byte_count < 0):
        raise ValueError("sidecar length must be a nonnegative integer")
    if byte_count is not None and byte_count > MAX_SIDECAR_BYTES:
        return _record("not-interpreted-size-limit")
    if raw is None:
        if byte_count is not None:
            raise ValueError("in-range present sidecar requires its original bytes")
        return _record("absent")
    if byte_count is not None and byte_count != len(raw):
        raise ValueError("sidecar byte count mismatch")
    if len(raw) > MAX_SIDECAR_BYTES:
        return _record("not-interpreted-size-limit")
    try:
        text = raw.decode("utf-8-sig")
        declaration = _DECLARATION.match(text)
        if declaration:
            declared = declaration.group()
            # Let the selected XML library validate declaration syntax; do not
            # silently discard malformed/duplicate declaration attributes.
            _ = ElementTree.fromstring(
                declared + "<check/>",
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
            encoding = _ENCODING.search(declared)
            if encoding and encoding.group(2).lower() not in ("utf-8", "utf8"):
                raise ValueError("declaration conflicts with UTF-8 profile")
            text = text[declaration.end() :]
        if not text.strip():
            raise ValueError("empty XML fragments")
        wrapped = ("<scansor-sidecar-root>" + text + "</scansor-sidecar-root>").encode(
            "utf-8"
        )
        roots: set[str] = set()
        depth, elements = 0, 0
        fields: dict[str, Control] = {}
        unknown = False
        for event, element in ElementTree.iterparse(
            io.BytesIO(wrapped),
            events=("start", "end", "pi"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        ):
            if event == "pi":
                unknown = True
                continue
            if event == "start":
                depth += 1
                elements += 1
                if depth > MAX_DEPTH or elements > MAX_ELEMENTS:
                    raise ValueError("XML structure exceeds interpretation limits")
                continue
            if depth == 2:
                name = element.tag
                if name in roots:
                    raise ValueError("duplicate XML root")
                roots.add(name)
                if name not in _FIELDS:
                    unknown = True
                else:
                    attributes: dict[str, Control] = {}
                    for key, value in element.attrib.items():
                        if key in _FIELDS[name]:
                            attributes[key] = value
                        else:
                            unknown = True
                    header: dict[str, Control] | None = None
                    for child in element:
                        if child.tag == "Header" and name in ("Model", "ModelExport"):
                            if header is not None:
                                raise ValueError("duplicate Header")
                            header = {
                                key: value
                                for key, value in child.attrib.items()
                                if key in _HEADER_FIELDS
                            }
                            unknown |= (
                                bool(set(child.attrib) - _HEADER_FIELDS)
                                or len(child) != 0
                                or bool((child.text or "").strip())
                            )
                        else:
                            unknown = True
                        unknown |= bool((child.tail or "").strip())
                    unknown |= bool((element.text or "").strip())
                    fields[name] = {"attributes": attributes, "header": header}
                if (element.tail or "").strip():
                    raise ValueError("text between XML roots")
                element.clear()
            elif depth == 1 and (element.text or "").strip():
                raise ValueError("text outside XML roots")
            depth -= 1
        if not roots:
            raise ValueError("no XML roots")
        return _record(
            "partially-interpreted-unknown-fields" if unknown else "interpreted",
            fields=fields,
        )
    except UnicodeError:
        return _record("not-interpreted-malformed", reason="invalid-utf8")
    except DefusedXmlException:
        return _record("not-interpreted-malformed", reason="forbidden-xml-construct")
    except ParseError:
        # Expat's diagnostic wording/version is execution detail, not identity.
        return _record("not-interpreted-malformed", reason="invalid-xml-syntax")
    except ValueError as error:
        return _record("not-interpreted-malformed", reason=str(error)[:256])
