"""Build a small, fully processed mesh walkthrough; outputs stay outside Git."""

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any, cast

import numpy as np

from scansor.mesh_accounting import account_import
from scansor.mesh_display import export_display
from scansor.mesh_display_ply import DisplayPlyReader
from scansor.mesh_display_verify import verify_display
from scansor.mesh_import import prepare_import
from scansor.mesh_publication import publish_contributions, publish_import
from scansor.mesh_recipes import SmallRecipe, write_recipe


def fixture(broken: bool) -> SmallRecipe:
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    ids: dict[tuple[float, float], int] = {}

    def vertex(x: float, y: float) -> int:
        key = (x, y)
        if key not in ids:
            ids[key] = len(points)
            points.append((x, y, 0.0))
        return ids[key]

    def quad(x: float, y: float, step: float) -> None:
        a, b, c, d = (
            vertex(x, y),
            vertex(x + step, y),
            vertex(x + step, y + step),
            vertex(x, y + step),
        )
        faces.extend(((a, b, c), (a, c, d)))

    for x in range(3):
        for y in range(4):
            quad(x, y, 1)
    # Conforming transition: split the right edge of each coarse cell into four.
    for y in range(4):
        a, d = vertex(3, y), vertex(3, y + 1)
        for j in range(4):
            faces.append((a, vertex(4, y + j / 4), vertex(4, y + (j + 1) / 4)))
        faces.append((a, vertex(4, y + 1), d))
    for i in range(16):
        for j in range(16):
            quad(4 + i / 4, j / 4, 0.25)
    if broken:
        faces.append((vertex(0, -2), vertex(1, -2), -1))
        a, b = vertex(2, -2), vertex(3, -2)
        missing = len(points)
        points.append((float("nan"), 0, 0))
        faces.append((a, b, missing))
        a, b = vertex(4, -2), vertex(5, -2)
        faces.append((a, a, b))
        faces.append((vertex(6, -2), vertex(7, -2), vertex(8, -2)))
        _ = vertex(1, -3)
    words = tuple(
        tuple(struct.unpack("<I", struct.pack("<f", x))[0] for x in p) for p in points
    )
    return SmallRecipe(
        "visual-broken-v1" if broken else "visual-clean-v1", words, tuple(faces)
    )


def process(root: Path, broken: bool) -> dict[str, Any]:
    root.mkdir()
    recipe = fixture(broken)
    source = root / "input.ply"
    with source.open("xb") as stream:
        write_recipe(stream, recipe)
    with (
        prepare_import(source, root, storage="disk", chunk_rows=127) as data,
        account_import(data) as accounted,
    ):
        imported = publish_import(accounted, root)
        contribution = publish_contributions(
            accounted, accounted.complete_contributions(), root
        )
    exported = export_display(
        imported.path, contribution.path, root, root, chunk_rows=127
    )
    verified = verify_display(
        exported.stage.path, imported.path, contribution.path, root, chunk_rows=61
    )
    views: dict[str, Any] = {}
    for kind in ("validity", "weights", "rejected-face-corners"):
        with (exported.stage.path / f"{kind}.ply").open("rb") as stream:
            reader = DisplayPlyReader(stream, kind, chunk_rows=4096)
            vertices = reader.read_range("vertex", 0, reader.vertices)
            triangles = reader.read_range("face", 0, reader.faces)["vertex_indices"][
                "values"
            ].tolist()
            rows = [
                {n: row[n].item() for n in vertices.dtype.names or ()}
                for row in vertices
            ]
            views[kind] = {"vertices": rows, "faces": triangles}
    v = views["weights"]["vertices"]
    coarse = [r["scalar_weight"] for r in v if 0 < r["x"] < 3 and 0 < r["y"] < 4]
    fine = [r["scalar_weight"] for r in v if 4 < r["x"] < 8 and 0 < r["y"] < 4]
    ratio = float(np.median(coarse) / np.median(fine))
    assert ratio == 16.0, ratio
    assert len(views["weights"]["faces"]) == 556
    assert cast(dict[str, Any], exported.legend["display_population"])[
        "rejected_corners"
    ] == (10 if broken else 0)
    return {
        "views": views,
        "legend": exported.legend,
        "display": str(exported.stage.path),
        "verification": verified,
        "interior_weight_ratio": ratio,
    }


def presentation_mesh(root: Path, broken: dict[str, Any]) -> None:
    """RGB-only presentation, with enlarged corner glyphs; never authoritative."""
    vertices: list[tuple[float, float, float, int, int, int]] = []
    faces: list[tuple[int, int, int]] = []
    for panel, kind in enumerate(("validity", "weights")):
        view = broken["views"][kind]
        offset = len(vertices)
        for row in view["vertices"]:
            vertices.append(
                (
                    row["x"] + panel * 11,
                    row["y"],
                    row["z"],
                    row["red"],
                    row["green"],
                    row["blue"],
                )
            )
        faces.extend(tuple(offset + i for i in f) for f in view["faces"])
    # Rejection glyphs below both panels; unique coordinate/status groups only.
    seen: set[tuple[float, float, int]] = set()
    for row in broken["views"]["rejected-face-corners"]["vertices"]:
        key = (row["x"], row["y"], row["scalar_face_status"])
        if key in seen:
            continue
        seen.add(key)
        offset = len(vertices)
        for i in range(12):
            a = 2 * math.pi * i / 12
            vertices.append(
                (
                    row["x"] + 0.10 * math.cos(a),
                    row["y"] - 1 + 0.10 * math.sin(a),
                    0,
                    row["red"],
                    row["green"],
                    row["blue"],
                )
            )
        faces.extend((offset, offset + i, offset + i + 1) for i in range(1, 11))
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment Presentation only: left validity; right weights; lower glyphs are enlarged rejected corners\n"
        f"element vertex {len(vertices)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    )
    with (root / "CloudCompare-presentation.ply").open("xb") as stream:
        _ = stream.write(header.encode())
        for row in vertices:
            _ = stream.write(struct.pack("<fffBBB", *row))
        for face in faces:
            _ = stream.write(struct.pack("<Biii", 3, *face))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    options = parser.parse_args()
    root: Path = options.output.resolve()
    root.mkdir(exist_ok=False, parents=True)
    clean, broken = process(root / "clean", False), process(root / "broken", True)
    assert (
        clean["views"]["weights"]["vertices"]
        == broken["views"]["weights"]["vertices"][
            : len(clean["views"]["weights"]["vertices"])
        ]
    )
    presentation_mesh(root, broken)
    data = {"clean": clean, "broken": broken}
    payload = json.dumps(data, allow_nan=False)
    _ = (root / "data.json").write_text(payload + "\n")
    template = Path(__file__).with_name("mesh-visual-demo") / "walkthrough.html"
    _ = (root / "START-HERE.html").write_text(
        template.read_text().replace("__DEMO_DATA__", payload)
    )
    print(root / "START-HERE.html")


if __name__ == "__main__":
    main()
