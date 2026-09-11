from __future__ import annotations

import hashlib
import io
import json
import struct
from importlib.metadata import version
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from scansor.errors import ScansorError
from scansor.mesh_controls import (
    Control,
    control_id,
    decode_control,
    encode_control,
    implementation_inventory,
)
from scansor.mesh_numeric import (
    canonical_f32,
    corner_areas,
    face_areas,
    normalized_weights,
    ordered_fold,
)
from scansor.mesh_ply import MeshPlyError, MeshPlyReader
from scansor.mesh_recipes import (
    MAX_CHUNK_ROWS,
    SMALL_RECIPES,
    ByteEdit,
    GridRecipe,
    SmallRecipe,
    generate_file,
    generation_request,
    philox_words,
    write_recipe,
)
from tests import mesh_rounding_oracle as oracle

GOLDENS = Path(__file__).parent / "fixtures" / "mesh-numeric-goldens-v1.json"


def source_bytes(recipe: GridRecipe | SmallRecipe, chunk: int = 7) -> bytes:
    stream = io.BytesIO()
    write_recipe(stream, recipe, chunk_rows=chunk)
    return stream.getvalue()


def golden_artifacts() -> dict[str, bytes]:
    """Exercise S2 primitives on the independent four-row golden, not an importer."""
    recipe = SMALL_RECIPES["right-triangle-orphan-v1"]
    xyz = canonical_f32(recipe.vertices(0, 4))
    triangles = recipe.faces(0, 1)
    areas = face_areas(xyz[triangles])
    thirds = corner_areas(areas)
    reference_counts = np.zeros(4, dtype="<u8")
    per_vertex = np.zeros(4, dtype="<f8")
    for face, corners in enumerate(triangles):
        for index in corners:
            reference_counts[index] += 1
            per_vertex[index] = ordered_fold(
                [thirds[face]], initial=float(per_vertex[index])
            )
    return {
        "observations.ply": source_bytes(recipe),
        "xyz.bin": xyz.tobytes(),
        "triangles.bin": triangles.tobytes(),
        "reference-count.bin": reference_counts.tobytes(),
        "face-area.bin": areas.tobytes(),
        "contribution-status.bin": np.where(reference_counts == 0, 2, 0)
        .astype("u1")
        .tobytes(),
        "vertex-area.bin": per_vertex.tobytes(),
        "weight.bin": normalized_weights(
            per_vertex, eligible_count=3, total=ordered_fold(per_vertex)
        ).tobytes(),
    }


def test_eight_independent_golden_artifacts() -> None:
    expected = {
        "observations.ply": oracle.independent_ply(
            [
                (0, 0, 0),
                (0x40400000, 0, 0),
                (0, 0x40800000, 0),
                (0x41100000, 0x41100000, 0x41100000),
            ],
            [(0, 1, 2)],
        ),
        "xyz.bin": struct.pack("<12f", 0, 0, 0, 3, 0, 0, 0, 4, 0, 9, 9, 9),
        "triangles.bin": struct.pack("<3i", 0, 1, 2),
        "reference-count.bin": struct.pack("<4Q", 1, 1, 1, 0),
        "face-area.bin": struct.pack("<d", 6),
        "contribution-status.bin": bytes([0, 0, 0, 2]),
        "vertex-area.bin": struct.pack("<4d", 2, 2, 2, 0),
        "weight.bin": struct.pack("<4d", 1, 1, 1, 0),
    }
    assert golden_artifacts() == expected
    frozen = json.loads(GOLDENS.read_text(encoding="ascii"))
    assert {
        name: hashlib.sha256(data).hexdigest() for name, data in expected.items()
    } == frozen["right_triangle_artifacts"]


@pytest.mark.parametrize("name", tuple(SMALL_RECIPES))
@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
def test_small_recipe_independent_bytes_and_frozen_hashes(
    name: str, chunk: int
) -> None:
    recipe = SMALL_RECIPES[name]
    expected = bytearray(
        oracle.independent_ply(
            list(recipe.xyz_bits),
            list(recipe.triangles),
            None if recipe.normal_bits is None else list(recipe.normal_bits),
        )
    )
    for edit in recipe.edits:
        expected[edit.offset : edit.offset + len(edit.data)] = edit.data
    actual = source_bytes(recipe, chunk)
    assert actual == expected
    frozen = json.loads(GOLDENS.read_text(encoding="ascii"))["small_recipes"][name]
    assert hashlib.sha256(actual).hexdigest() == frozen["source_sha256"]
    assert recipe.record() == frozen["record"]
    assert control_id(recipe.record()) == frozen["control_sha256"]
    reader = MeshPlyReader(io.BytesIO(actual), max_range_bytes=4096)
    if recipe.expected == "complete":
        reader.validate_all(chunk_rows=chunk)
    else:
        with pytest.raises(MeshPlyError) as caught:
            reader.validate_all(chunk_rows=chunk)
        assert caught.value.detail.category == recipe.expected


def test_frozen_philox_counter_vectors() -> None:
    frozen = json.loads(GOLDENS.read_text(encoding="ascii"))
    whole = philox_words(7, 0, 128)
    for record in frozen["philox_vectors"]:
        index = record["source_index"]
        expected = record["words_hex"]
        assert [
            f"{int(word):016x}" for word in philox_words(7, index, index + 1)[0]
        ] == expected
        assert [f"{int(word):016x}" for word in whole[index]] == expected
    assert philox_words(7, 2**64, 2**64).shape == (0, 4)
    assert philox_words(2**64 - 1, 2**64 - 1, 2**64).shape == (1, 4)
    # Explicit uint64 arrays prevent NumPy from converting the mixed integer
    # list through float64 and corrupting a seed beyond the signed-int64 range.
    assert philox_words(2**64 - 1, 0, 1).tolist() == [
        [
            4333907348786404347,
            13232047798055274199,
            7584883013141392260,
            13210516241684113150,
        ]
    ]


@pytest.mark.parametrize("noise", (0, 3, 16))
@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
def test_grid_independent_integer_construction(noise: int, chunk: int) -> None:
    recipe = GridRecipe(17, 19, noise_bits=noise)
    words = philox_words(7, 0, recipe.vertex_count)
    vertices: list[tuple[int, int, int]] = []
    for index, raw in enumerate(words):
        coefficient = (
            0 if noise == 0 else 2 * (int(raw[0]) & (2**noise - 1)) - (2**noise - 1)
        )
        encoded = struct.pack(
            "<fff", 3 * (index % 17), 4 * (index // 17), coefficient / 256
        )
        vertices.append(struct.unpack("<III", encoded))
    faces: list[tuple[int, int, int]] = []
    for row in range(18):
        for column in range(16):
            a = row * 17 + column
            faces.extend(((a, a + 1, a + 17), (a + 1, a + 18, a + 17)))
    assert source_bytes(recipe, chunk) == oracle.independent_ply(vertices, faces)
    # Chunk completion order cannot select a different raw counter or geometry.
    actual = np.empty((recipe.vertex_count, 3), dtype="<f4")
    for start in reversed(range(0, recipe.vertex_count, chunk)):
        actual[start : start + chunk] = recipe.vertices(
            start, min(start + chunk, recipe.vertex_count)
        )
    assert actual.view("<u4").tolist() == [list(row) for row in vertices]


def test_recipe_exact_representability_limits() -> None:
    for recipe, index, expected in (
        (GridRecipe(5_592_407, 1), 5_592_406, (16_777_218, 0, 0)),
        (GridRecipe(1, 2**24 + 1), 2**24, (0, 2**26, 0)),
    ):
        assert recipe.vertices(index, index + 1).tolist() == [list(expected)]
    for parameters in (
        {"width": 5_592_408, "height": 1},
        {"width": 1, "height": 2**24 + 2},
        {"width": 100_000, "height": 100_000},
    ):
        with pytest.raises(ScansorError):
            _ = GridRecipe(**parameters)
    # Frozen first coefficient for seed 7/b=3 is +1: this q is valid for this
    # one-row recipe despite invalid larger values in the potential distribution.
    assert GridRecipe(1, 1, noise_bits=3, quantum_exponent=127).vertices(0, 1)[
        0, 2
    ] == np.float32(2**127)
    with pytest.raises(ScansorError, match="exactly representable"):
        _ = GridRecipe(2, 1, noise_bits=3, quantum_exponent=127).vertices(0, 2)
    for q in (-150, 128):
        with pytest.raises(ScansorError):
            _ = GridRecipe(1, 1, noise_bits=1, quantum_exponent=q)
    for q in (-149, -148, -140, -126, 100):
        recipe = GridRecipe(3, 1, noise_bits=3, quantum_exponent=q)
        expected = b"".join(
            struct.pack("<f", coefficient * 2.0**q) for coefficient in (1, 5, 1)
        )
        assert recipe.vertices(0, 3)[:, 2].tobytes() == expected
    assert GridRecipe(1, 1, quantum_exponent=10**100).vertices(0, 1).tobytes() == bytes(
        12
    )


@pytest.mark.parametrize(
    "field", ("width", "height", "seed", "noise_bits", "quantum_exponent")
)
@pytest.mark.parametrize("invalid", (True, 1.0, "1"))
def test_recipe_rejects_implicit_integer_conversion(
    field: str, invalid: object
) -> None:
    parameters = {"width": 3, "height": 2, field: invalid}
    with pytest.raises(ScansorError):
        _ = GridRecipe(**cast(dict[str, int], parameters))


def test_recipe_boundaries_and_owned_generation_failure(tmp_path: Path) -> None:
    recipe = GridRecipe(1, 1)
    assert recipe.face_count == 0
    assert recipe.faces(0, 0).shape == (0, 3)
    for start, stop in ((-1, 0), (1, 0), (0, 2), (True, 1)):
        with pytest.raises(ScansorError):
            _ = recipe.vertices(start, stop)
    with pytest.raises(ScansorError):
        _ = philox_words(7, 0, MAX_CHUNK_ROWS + 1)
    path = generate_file(tmp_path, "mesh.ply", recipe)
    assert path.read_bytes() == source_bytes(recipe)
    with pytest.raises(FileExistsError):
        _ = generate_file(tmp_path, "mesh.ply", recipe)
    bad = GridRecipe(2, 1, noise_bits=3, quantum_exponent=127)
    with pytest.raises(ScansorError):
        _ = generate_file(tmp_path, "failed.ply", bad, chunk_rows=1)
    assert not (tmp_path / "failed.ply").exists()
    for name in ("../x", "a/b", "a\\b", "", ".", ".."):
        with pytest.raises(ScansorError):
            _ = generate_file(tmp_path, name, recipe)
    (tmp_path / ".git").mkdir()
    _ = (tmp_path / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n", encoding="ascii"
    )
    with pytest.raises(ScansorError, match="outside the Git"):
        _ = generate_file(tmp_path, "other.ply", recipe)
    with pytest.raises(ScansorError):
        _ = SmallRecipe("bad-v1", ((0, 0, 0),), (), edits=(ByteEdit(-1, b"a"),))


def test_canonical_controls_and_semantic_inventory() -> None:
    recipe = GridRecipe(17, 19, noise_bits=3)
    encoded = encode_control(recipe.record())
    assert encoded == (
        json.dumps(recipe.record(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    assert GridRecipe.from_record(decode_control(encoded)) == recipe
    request = generation_request(recipe)
    assert request["generator_implementation"] == control_id(
        implementation_inventory("generator")
    )
    assert request["recipe"] == recipe.record()
    assert control_id(request) != control_id(generation_request(GridRecipe(17, 19)))
    assert (
        encode_control(
            {"text": "\u00e9 e\u0301", "values": [True, False, None, 2**100]}
        )
        == b'{\n  "text": "\\u00e9 e\\u0301",\n  "values": [\n    true,\n    false,\n    null,\n    1267650600228229401496703205376\n  ]\n}\n'
    )
    for owner in ("generator", "importer", "policy"):
        inventory = implementation_inventory(owner)
        assert decode_control(encode_control(inventory)) == inventory
        assert inventory["dependencies"] == {
            "numpy": np.__version__,
            **({"defusedxml": version("defusedxml")} if owner == "importer" else {}),
        }
        files = cast(list[dict[str, str]], inventory["files"])
        assert [entry["name"] for entry in files] == sorted(
            entry["name"] for entry in files
        )
        root = Path(__file__).parents[1] / "src" / "scansor"
        assert all(
            entry["sha256"]
            == hashlib.sha256((root / entry["name"]).read_bytes()).hexdigest()
            for entry in files
        )
        assert "duckdb" not in str(inventory).lower()
        assert "chunk" not in str(inventory).lower()
    with pytest.raises(ScansorError):
        _ = implementation_inventory("unknown")
    for invalid in (1.0, float("nan"), {1: "key"}, (1, 2), np.int64(3), b"bytes"):
        with pytest.raises(ScansorError):
            _ = encode_control(cast(Control, invalid))
    for data in (
        b'{"x":1,"x":1}\n',
        b"1.0\n",
        b'{\n  "a": NaN\n}\n',
        b"0\n ",
        b" " * (8 * 1024 * 1024 + 1),
    ):
        with pytest.raises(ScansorError):
            _ = decode_control(data)
    with pytest.raises(ScansorError, match="exceeds"):
        _ = encode_control("a" * (8 * 1024 * 1024))
    recursive: list[Control] = []
    recursive.append(recursive)
    with pytest.raises(ScansorError, match="nesting"):
        _ = encode_control(recursive)
    for change in ({"typo": 1}, {"raw_words_per_vertex": 3}, {"width": True}):
        with pytest.raises(ScansorError):
            _ = GridRecipe.from_record(recipe.record() | change)
    missing = recipe.record()
    del missing["seed"]
    with pytest.raises(ScansorError):
        _ = GridRecipe.from_record(missing)
