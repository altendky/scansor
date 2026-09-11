"""Portable mesh fixtures, separate from the existing generated fitting revisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

from scansor._plyio import Writer
from scansor.errors import ScansorError
from scansor.mesh_controls import (
    Control,
    control_id,
    encode_control,
    implementation_inventory,
)
from scansor.mesh_numeric import check_arithmetic
from scansor.mesh_ply import mesh_header, mesh_layout

REVISION = "mesh-grid-philox-v1"
COMMENT = "scansor-mesh-recipe-v1"
MAX_CHUNK_ROWS = 1_048_576


def _integer(name: str, value: int, lower: int, upper: int) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise ScansorError(f"mesh recipe {name} must be an integer in {lower}..{upper}")


def _range(start: int, stop: int, count: int) -> None:
    _integer("start", start, 0, count)
    _integer("stop", stop, start, count)
    if stop - start > MAX_CHUNK_ROWS:
        raise ScansorError("mesh recipe range exceeds chunk row limit")


def philox_words(seed: int, start: int, stop: int) -> np.ndarray:
    _integer("seed", seed, 0, 2**64 - 1)
    _range(start, stop, 2**64)
    if start == stop:
        return np.empty((0, 4), dtype="<u8")
    # Keep direct key/counter semantics on upgrades; rerun frozen vectors and
    # inspect NumPy's Philox/random_raw changelog before accepting new builds.
    generator = np.random.Philox(
        key=np.array([seed, 0], dtype=np.uint64),
        counter=np.array([start, 0, 0, 0], dtype=np.uint64),
    )
    return (
        generator.random_raw(4 * (stop - start))
        .reshape(-1, 4)
        .astype("<u8", copy=False)
    )


def _noise_bits(coefficients: np.ndarray, quantum: int) -> np.ndarray:
    """Encode odd signed coefficients (at most 16 bits) using integer operations.

    Validate actual generated values: a small recipe can use a smaller exponent
    range than its distribution's maximum coefficient. Never round a coordinate.
    """
    magnitude = np.abs(coefficients).astype(np.uint32)
    result = np.zeros(len(coefficients), dtype="<u4")
    for highest in range(16):
        selected = (magnitude >= 2**highest) & (magnitude < 2 ** (highest + 1))
        if not np.any(selected):
            continue
        exponent = highest + quantum
        if exponent > 127 or quantum < -149:
            raise ScansorError(
                "mesh recipe noise is not exactly representable binary32"
            )
        if exponent < -126:
            result[selected] = magnitude[selected] << (quantum + 149)
        else:
            result[selected] = ((exponent + 127) << 23) | (
                (magnitude[selected] << (23 - highest)) & 0x7FFFFF
            )
    result[coefficients < 0] |= np.uint32(0x80000000)
    return result


@dataclass(frozen=True)
class GridRecipe:
    width: int
    height: int
    seed: int = 7
    noise_bits: int = 0
    quantum_exponent: int = -8

    def __post_init__(self) -> None:
        _integer("width", self.width, 1, 2**31)
        _integer("height", self.height, 1, 2**31)
        _integer("vertex count", self.vertex_count, 1, 2**31)
        _integer("seed", self.seed, 0, 2**64 - 1)
        _integer("noise_bits", self.noise_bits, 0, 16)
        if type(self.quantum_exponent) is not int:
            raise ScansorError("mesh recipe quantum_exponent must be an integer")
        # The first inexact x is 3*5_592_407; checking only the endpoint would
        # miss interior failures. y=4*row first fails at row 2**24+1.
        if self.width > 5_592_407 or self.height > 2**24 + 1:
            raise ScansorError("mesh recipe grid coordinates are not exact binary32")
        if self.noise_bits and not -149 <= self.quantum_exponent <= 127:
            raise ScansorError(
                "mesh recipe noise is not exactly representable binary32"
            )
        _ = mesh_layout(
            mesh_header(self.vertex_count, self.face_count, comments=(COMMENT,))
        )

    @property
    def vertex_count(self) -> int:
        return self.width * self.height

    @property
    def face_count(self) -> int:
        return 2 * (self.width - 1) * (self.height - 1)

    @property
    def normals(self) -> bool:
        return False

    def record(self) -> dict[str, Control]:
        return {
            "revision": REVISION,
            "model": "rectangular-height-grid-v1",
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "noise_bits": self.noise_bits,
            "quantum_exponent": self.quantum_exponent,
            "raw_words_per_vertex": 4,
            "key": [self.seed, 0],
            "counter": "[source_vertex_index,0,0,0]",
            "adverse_edits": [],
            "expected": "complete",
        }

    @classmethod
    def from_record(cls, value: Control) -> GridRecipe:
        if not isinstance(value, dict):
            raise ScansorError("mesh grid recipe must be an object")
        names = ("width", "height", "seed", "noise_bits", "quantum_exponent")
        if any(type(value.get(name)) is not int for name in names):
            raise ScansorError("mesh grid recipe requires explicit integer parameters")
        # The runtime checks above establish these types, including excluding bool.
        parameters = {name: int(str(value[name])) for name in names}
        recipe = cls(**parameters)
        if encode_control(recipe.record()) != encode_control(value):
            raise ScansorError("unknown or inconsistent mesh grid recipe fields")
        return recipe

    def vertices(self, start: int, stop: int) -> np.ndarray:
        _range(start, stop, self.vertex_count)
        check_arithmetic()
        indices = np.arange(start, stop, dtype=np.int64)
        words = philox_words(self.seed, start, stop)
        vertices = np.empty((stop - start, 3), dtype="<f4")
        vertices[:, 0] = 3 * (indices % self.width)
        vertices[:, 1] = 4 * (indices // self.width)
        if self.noise_bits:
            mask = 2**self.noise_bits - 1
            coefficients = 2 * (words[:, 0] & mask).astype(np.int64) - mask
            vertices.view("<u4")[:, 2] = _noise_bits(
                coefficients, self.quantum_exponent
            )
        else:
            vertices[:, 2] = 0
        return vertices

    def faces(self, start: int, stop: int) -> np.ndarray:
        _range(start, stop, self.face_count)
        if start == stop:
            return np.empty((0, 3), dtype="<i4")
        indices = np.arange(start, stop, dtype=np.int64)
        cells = indices // 2
        a = (cells // (self.width - 1)) * self.width + cells % (self.width - 1)
        odd = indices % 2
        result = np.empty((stop - start, 3), dtype="<i4")
        result[:, 0] = a + odd
        result[:, 1] = a + 1 + odd * self.width
        result[:, 2] = a + self.width
        return result


type Triple = tuple[int, int, int]


@dataclass(frozen=True)
class ByteEdit:
    offset: int
    data: bytes


@dataclass(frozen=True)
class SmallRecipe:
    name: str
    xyz_bits: tuple[Triple, ...]
    triangles: tuple[Triple, ...]
    normal_bits: tuple[Triple, ...] | None = None
    edits: tuple[ByteEdit, ...] = ()
    expected: str = "complete"

    def __post_init__(self) -> None:
        if not self.name.endswith("-v1") or not self.name.isascii():
            raise ScansorError("small mesh recipe requires an ASCII revision name")
        _integer("small vertex count", self.vertex_count, 1, 4096)
        _integer("small face count", self.face_count, 0, 16384)
        if self.normal_bits is not None and len(self.normal_bits) != self.vertex_count:
            raise ScansorError("small mesh recipe normal count mismatch")
        for rows in (self.xyz_bits, self.normal_bits or ()):
            for row in rows:
                if len(row) != 3:
                    raise ScansorError("small mesh coordinates need three bit words")
                for word in row:
                    _integer("binary32 word", word, 0, 2**32 - 1)
        for row in self.triangles:
            if len(row) != 3:
                raise ScansorError("small mesh faces need three indices")
            for index in row:
                _integer("face index", index, -(2**31), 2**31 - 1)
        if self.expected not in ("complete", "unsupported", "structure"):
            raise ScansorError("unknown small mesh expected outcome")
        layout = mesh_layout(
            mesh_header(
                self.vertex_count,
                self.face_count,
                normals=self.normals,
                comments=(COMMENT,),
            )
        )
        for edit in self.edits:
            _integer("edit offset", edit.offset, 0, layout.byte_count)
            if (
                type(edit.data) is not bytes
                or not edit.data
                or edit.offset + len(edit.data) > layout.byte_count
            ):
                raise ScansorError("mesh adverse edit outside constructed source")

    @property
    def vertex_count(self) -> int:
        return len(self.xyz_bits)

    @property
    def face_count(self) -> int:
        return len(self.triangles)

    @property
    def normals(self) -> bool:
        return self.normal_bits is not None

    def vertices(self, start: int, stop: int) -> np.ndarray:
        _range(start, stop, self.vertex_count)
        return (
            np.array(self.xyz_bits[start:stop], dtype="<u4").reshape(-1, 3).view("<f4")
        )

    def faces(self, start: int, stop: int) -> np.ndarray:
        _range(start, stop, self.face_count)
        return np.array(self.triangles[start:stop], dtype="<i4").reshape(-1, 3)

    def record(self) -> dict[str, Control]:
        return {
            "revision": "mesh-small-recipe-v1",
            "name": self.name,
            "xyz_bits": [[f"{word:08x}" for word in row] for row in self.xyz_bits],
            "triangles": [list(row) for row in self.triangles],
            "normal_bits": None
            if self.normal_bits is None
            else [[f"{word:08x}" for word in row] for row in self.normal_bits],
            "adverse_edits": [
                {"offset": edit.offset, "hex": edit.data.hex()} for edit in self.edits
            ],
            "expected": self.expected,
        }


_RIGHT: tuple[Triple, ...] = (
    (0, 0, 0),
    (0x40400000, 0, 0),
    (0, 0x40800000, 0),
    (0x41100000, 0x41100000, 0x41100000),
)
SMALL_RECIPES: dict[str, SmallRecipe] = {
    recipe.name: recipe
    for recipe in (
        SmallRecipe("right-triangle-orphan-v1", _RIGHT, ((0, 1, 2),)),
        SmallRecipe(
            "unequal-adjacent-v1",
            (*_RIGHT[:3], (0, 0, 0x41000000)),
            ((0, 1, 2), (0, 1, 3)),
        ),
        SmallRecipe(
            "irrational-area-v1",
            ((0, 0, 0), (0x3F800000, 0, 0), (0, 0x3F800000, 0x3F800000)),
            ((0, 1, 2),),
        ),
        SmallRecipe(
            "duplicate-reversed-degenerate-v1",
            (*_RIGHT, (0, 0, 0), (0x40C00000, 0, 0)),
            ((0, 1, 2), (0, 1, 2), (2, 1, 0), (0, 1, 4), (0, 1, 5), (1, 1, 2)),
        ),
        SmallRecipe("no-faces-v1", _RIGHT, ()),
        SmallRecipe(
            "exceptional-values-v1",
            ((0x80000000, 0, 0), *_RIGHT[1:], (0x7FA12345, 0, 0), (0xFF800000, 0, 0)),
            ((0, 1, 2), (-1, 4, 4), (4, 4, 1), (5, 1, 2), (0, 6, 2)),
            (
                (0, 0, 0x3F800000),
                (0, 0, 0),
                (0xFFC12345, 0, 0),
                (0x7F800000, 0, 0),
                (0, 0, 0),
                (0, 0, 0),
            ),
        ),
        SmallRecipe("all-invalid-v1", ((0x7FC00001, 0, 0),), ((0, 0, 0), (-1, 0, 0))),
        SmallRecipe(
            "wrong-list-count-v1",
            _RIGHT,
            ((0, 1, 2),),
            edits=(ByteEdit(248, b"\x04"),),
            expected="unsupported",
        ),
    )
}


def generation_request(recipe: GridRecipe | SmallRecipe) -> dict[str, Control]:
    """Semantic request; execution paths/chunks never enter this acyclic record."""
    return {
        "revision": "mesh-generation-request-v1",
        "recipe": recipe.record(),
        "generator_implementation": control_id(implementation_inventory("generator")),
        "encoding": "binary-triangle-ply-v1",
    }


def write_recipe(
    stream: BinaryIO, recipe: GridRecipe | SmallRecipe, *, chunk_rows: int = 65_536
) -> None:
    """Write a caller-owned stream; success covers all rows, errors leave it partial.

    Peak memory is proportional to chunk_rows, never mesh size. Adverse byte
    edits are applied in listed order after the complete canonical construction.
    """
    _integer("chunk_rows", chunk_rows, 1, MAX_CHUNK_ROWS)
    layout = mesh_layout(
        mesh_header(
            recipe.vertex_count,
            recipe.face_count,
            normals=recipe.normals,
            comments=(COMMENT,),
        )
    )
    writer = Writer(stream, layout, max_range_bytes=24 * chunk_rows)
    for start in range(0, recipe.vertex_count, chunk_rows):
        stop = min(start + chunk_rows, recipe.vertex_count)
        rows = np.empty(stop - start, dtype=layout.element("vertex").dtype)
        vertices = recipe.vertices(start, stop)
        for axis, name in enumerate(("x", "y", "z")):
            rows[name].view("<u4")[:] = vertices.view("<u4")[:, axis]
        if isinstance(recipe, SmallRecipe) and recipe.normal_bits is not None:
            normals = np.array(recipe.normal_bits[start:stop], dtype="<u4")
            for axis, name in enumerate(("nx", "ny", "nz")):
                rows[name].view("<u4")[:] = normals[:, axis]
            del normals
        writer.write_range("vertex", start, rows)
        del rows, vertices
    for start in range(0, recipe.face_count, chunk_rows):
        stop = min(start + chunk_rows, recipe.face_count)
        rows = np.empty(stop - start, dtype=layout.element("face").dtype)
        rows["vertex_indices"]["count"] = 3
        rows["vertex_indices"]["values"] = recipe.faces(start, stop)
        writer.write_range("face", start, rows)
        del rows
    writer.finish()
    if isinstance(recipe, SmallRecipe):
        for edit in recipe.edits:
            if edit.offset < 0 or edit.offset + len(edit.data) > layout.byte_count:
                raise ScansorError("mesh adverse edit outside constructed source")
            _ = stream.seek(edit.offset)
            if stream.write(edit.data) != len(edit.data):
                raise ScansorError("mesh adverse edit short write")


def generate_file(
    directory: Path,
    name: str,
    recipe: GridRecipe | SmallRecipe,
    *,
    chunk_rows: int = 65_536,
) -> Path:
    """Generate only into an explicit existing directory outside a Git checkout.

    Exclusive creation avoids overwriting user files. A failed generation removes
    its owned partial file; concurrent access/publication belongs to later stages.
    """
    root = directory.resolve(strict=True)
    if any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in (root, *root.parents)
    ):
        raise ScansorError("generate bulk mesh data outside the Git checkout")
    if (
        not name
        or Path(name).name != name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
    ):
        raise ScansorError("mesh source name must be a single filename")
    path = root / name
    stream = path.open("xb")
    try:
        with stream:
            write_recipe(stream, recipe, chunk_rows=chunk_rows)
    except BaseException:
        # Includes flush/close failure; the owned handle is closed before unlink.
        path.unlink()
        raise
    return path
