from __future__ import annotations

import hashlib
import struct

import numpy as np
import pytest

from scansor.mesh_accumulation import CornerFold
from scansor.mesh_digests import RowDigest
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import NumericProfileError
from scansor.mesh_semantics import ColumnSpec
from tests import mesh_rounding_oracle as oracle


@pytest.mark.parametrize("chunk", (1, 2, 7, 127, 4093))
def test_fold_exact_oracle_across_groups_and_high_valence(chunk: int) -> None:
    # The first addition loses each later half-ulp; grouping into subtotals would
    # change the answer. A high-valence group spans many awkward input batches.
    keys = [(1, i, 0) for i in range(517)] + [(4, 517, 0), (4, 517, 2), (7, 518, 1)]
    words = [0x3FF0000000000000] + [0x3CA0000000000000] * 516 + [1, 2, 0]
    columns = tuple(
        np.array([key[i] for key in keys], dtype=dtype)
        for i, dtype in enumerate(("<u8", "<u8", "u1"))
    )
    allocation = np.array(words, dtype="<u8")
    fold = CornerFold(vertices=13, faces=519, corners=len(keys), batch_rows=chunk)
    expected_counts = [0] * 13
    expected_areas = [0] * 13
    for key, word in zip(keys, words, strict=True):
        expected_counts[key[0]] += 1
        expected_areas[key[0]] = oracle.add(expected_areas[key[0]], word)
    actual_counts, actual_areas = bytearray(), bytearray()

    def keep(rows: object) -> None:
        from scansor.mesh_accumulation import VertexContributions

        assert isinstance(rows, VertexContributions)
        assert rows.start == len(actual_counts) // 8
        assert len(rows.areas) <= chunk
        assert not rows.areas.flags.writeable and not rows.references.flags.writeable
        actual_counts.extend(rows.references.tobytes())
        actual_areas.extend(rows.areas.tobytes())

    for start in range(0, len(keys), chunk):
        stop = start + chunk
        for rows in fold.consume(
            columns[0][start:stop],
            columns[1][start:stop],
            columns[2][start:stop],
            allocation[start:stop],
        ):
            keep(rows)
    for rows in fold.finish():
        keep(rows)
    assert actual_counts == struct.pack("<13Q", *expected_counts)
    assert actual_areas == struct.pack("<13Q", *expected_areas)
    with pytest.raises(MeshImportError, match="finished"):
        _ = list(fold.finish())


def test_orphan_gap_is_bounded_without_input() -> None:
    fold = CornerFold(vertices=100_003, faces=0, corners=0, batch_rows=127)
    seen = 0
    for rows in fold.finish():
        assert rows.start == seen and len(rows.references) <= 127
        assert not np.any(rows.references) and not np.any(rows.areas.view("<u8"))
        seen += len(rows.references)
    assert seen == 100_003


@pytest.mark.parametrize(
    "mode",
    (
        "duplicate",
        "reverse",
        "missing",
        "face",
        "vertex",
        "corner",
        "dtype",
        "cross-batch",
    ),
)
def test_fold_rejects_bad_corner_stream(mode: str) -> None:
    fold = CornerFold(vertices=2, faces=1, corners=2, batch_rows=2)
    vertex = np.array([0, 1], dtype="<u8")
    face = np.zeros(2, dtype="<u8")
    corner = np.array([0, 1], dtype="u1")
    area = np.zeros(2, dtype="<u8")
    if mode == "duplicate":
        vertex[1], corner[1] = 0, 0
    elif mode == "reverse":
        vertex[:] = [1, 0]
    elif mode == "face":
        face[0] = 1
    elif mode == "vertex":
        vertex[1] = 2
    elif mode == "corner":
        corner[1] = 3
    elif mode == "dtype":
        vertex = vertex.astype("<i8")
    with pytest.raises(MeshImportError):
        if mode == "missing":
            _ = list(fold.consume(vertex[:1], face[:1], corner[:1], area[:1]))
        elif mode == "cross-batch":
            for _ in range(2):
                _ = list(fold.consume(vertex[:1], face[:1], corner[:1], area[:1]))
        else:
            _ = list(fold.consume(vertex, face, corner, area))
        _ = list(fold.finish())
    with pytest.raises(MeshImportError, match="failed"):
        _ = list(fold.finish())


@pytest.mark.parametrize(
    "word",
    (
        0x8000000000000000,
        0xBFF0000000000000,
        0x7FF0000000000000,
        0x7FF8000000000001,
        0x7FEFFFFFFFFFFFFF,
    ),
)
def test_fold_invalid_arithmetic_is_failure(word: int) -> None:
    fold = CornerFold(vertices=1, faces=1, corners=2, batch_rows=2)
    with pytest.raises(NumericProfileError):
        _ = list(
            fold.consume(
                np.zeros(2, dtype="<u8"),
                np.zeros(2, dtype="<u8"),
                np.array([0, 1], dtype="u1"),
                np.array([word, word], dtype="<u8"),
            )
        )


@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
def test_row_digest_matches_independent_packed_rows(chunk: int) -> None:
    xyz_words = [
        (0, 0x80000000, 0x7FC00000),
        (0x7FA00001, 1, 0xFF800000),
        (0x3F800000, 0, 0),
    ]
    counts = [0, 9, 17]
    xyz = np.array(xyz_words, dtype="<u4").view("<f4")
    references = np.array(counts, dtype="<u8")
    specs = (
        ColumnSpec("xyz.bin", 3, 3, "<f4"),
        ColumnSpec("reference-count.bin", 3, 1, "<u8"),
    )
    digest = RowDigest("mesh-test-v1", specs, max_rows=chunk)
    expected = b"mesh-test-v1\0" + struct.pack("<Q", 3)
    for ordinal, (row, count) in enumerate(zip(xyz_words, counts, strict=True)):
        expected += struct.pack("<QIIIQ", ordinal, *row, count)
    for start in range(0, 3, chunk):
        digest.update(
            start, (xyz[start : start + chunk], references[start : start + chunk])
        )
    assert digest.finish() == hashlib.sha256(expected).hexdigest()


def test_row_digest_rejects_omission_reorder_and_schema_change() -> None:
    digest = RowDigest(
        "mesh-test-v1", (ColumnSpec("xyz.bin", 2, 3, "<f4"),), max_rows=1
    )
    with pytest.raises(MeshImportError, match="incomplete"):
        _ = digest.finish()
    with pytest.raises(MeshImportError, match="prefix"):
        digest.update(1, (np.zeros((1, 3), dtype="<f4"),))
    with pytest.raises(MeshImportError, match="dtype/shape"):
        digest.update(0, (np.zeros((1, 3), dtype="<f8"),))
    digest.update(0, (np.zeros((1, 3), dtype="<f4"),))
    with pytest.raises(MeshImportError, match="prefix"):
        digest.update(0, (np.zeros((1, 3), dtype="<f4"),))


def test_abandoned_fold_generator_cannot_finish_or_resume() -> None:
    fold = CornerFold(vertices=4, faces=1, corners=1, batch_rows=1)
    batch = fold.consume(
        np.array([3], dtype="<u8"),
        np.zeros(1, dtype="<u8"),
        np.zeros(1, dtype="u1"),
        np.zeros(1, dtype="<u8"),
    )
    first = next(batch)
    assert first.start == 0
    batch.close()
    with pytest.raises(MeshImportError, match="failed"):
        _ = list(fold.finish())
