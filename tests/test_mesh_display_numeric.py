from __future__ import annotations

import numpy as np
import pytest

from scansor.mesh_controls import encode_control
from scansor.mesh_display_numeric import (
    DisplayTransform,
    join_ids,
    rejected_colors,
    split_ids,
    validity_colors,
    weight_colors,
)
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import float_bits


@pytest.mark.parametrize("endian", ("<", ">"))
def test_split_ids_preserve_every_digit_beyond_single_float_precision(
    endian: str,
) -> None:
    values = np.array(
        [0, 65535, 65536, 2**24 + 1, 2**32 + 3, 2**53 + 1, 2**63, 2**64 - 1],
        dtype=endian + "u8",
    )
    digits = split_ids(values)
    expected = np.array(
        [
            [(int(value) >> shift) & 65535 for shift in (0, 16, 32, 48)]
            for value in values
        ],
        dtype="<f4",
    )
    assert digits.dtype == np.dtype("<f4") and np.array_equal(digits, expected)
    for dtype in ("<f4", ">f4", "<f8", ">f8"):
        actual = join_ids(digits.astype(dtype))
        assert np.array_equal(actual, values)
        assert not actual.flags.writeable
    assert not digits.flags.writeable


@pytest.mark.parametrize("value", (-1, 65536, 0.5, float("nan"), float("inf")))
def test_malformed_id_digit_is_not_used_as_an_index(value: float) -> None:
    digits = np.zeros((1, 4), dtype="<f8")
    digits[0, 2] = value
    with pytest.raises(MeshImportError):
        _ = join_ids(digits)


def test_display_colors_and_ties_are_contract_values() -> None:
    assert validity_colors(np.array([0, 2, 3], dtype="u1")).tolist() == [
        [40, 170, 80],
        [255, 165, 0],
        [220, 50, 50],
    ]
    assert rejected_colors(np.array([1, 2, 3, 4], dtype="u1")).tolist() == [
        [180, 0, 180],
        [220, 50, 50],
        [255, 165, 0],
        [80, 120, 220],
    ]
    status = np.array([0, 0, 0, 0, 2, 3], dtype="u1")
    colors = weight_colors(
        status, np.array([1, 3, 5, 510, 0, 0], dtype="<f8"), maximum=510.0
    )
    assert colors.tolist() == [
        [0, 0, 255],
        [2, 2, 253],
        [2, 2, 253],
        [255, 255, 0],
        [128, 128, 128],
        [128, 128, 128],
    ]
    assert not colors.flags.writeable
    for chunk in (1, 2, 7):
        areas = np.array([1, 3, 5, 510, 0, 0], dtype="<f8")
        parts = [
            weight_colors(
                status[start : start + chunk],
                areas[start : start + chunk],
                maximum=510.0,
            )
            for start in range(0, len(areas), chunk)
        ]
        assert np.array_equal(np.concatenate(parts), colors)


def test_empty_and_excluded_ramps_and_invalid_populations() -> None:
    assert weight_colors(
        np.array([2, 3], dtype="u1"), np.zeros(2, dtype="<f8"), maximum=0.0
    ).tolist() == [[128, 128, 128], [128, 128, 128]]
    assert weight_colors(
        np.zeros(0, dtype="u1"), np.zeros(0, dtype="<f8"), maximum=0.0
    ).shape == (0, 3)
    for status, area, maximum in (
        (0, 0.0, 0.0),
        (2, 1.0, 1.0),
        (0, 2.0, 1.0),
        (1, 0.0, 1.0),
    ):
        with pytest.raises(MeshImportError):
            _ = weight_colors(
                np.array([status], dtype="u1"),
                np.array([area], dtype="<f8"),
                maximum=maximum,
            )


def test_default_coordinates_are_exact_and_transforms_count_lost_rows() -> None:
    source = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]], dtype="<f4")
    plain = DisplayTransform().apply(source)
    assert plain.coordinates.dtype == np.dtype("<f8")
    assert np.array_equal(plain.coordinates, source.astype("<f8"))
    assert plain.roundtrip_mismatch_rows == 0 and not plain.coordinates.flags.writeable
    shifted = DisplayTransform((float_bits(0.5), float_bits(-0.5), float_bits(1.0)), 2)
    result = shifted.apply(source)
    assert result.coordinates.tolist() == [[2.0, 10.0, 8.0], [-2.0, 2.0, -4.0]]
    assert result.roundtrip_mismatch_rows == 0
    _ = encode_control(shifted.record())
    lossy = DisplayTransform(
        (float_bits(2.0**100), "0000000000000000", "0000000000000000")
    ).apply(source)
    assert lossy.roundtrip_mismatch_rows == 1
    with pytest.raises(MeshImportError, match="nonfinite"):
        _ = DisplayTransform(scale_power=1023).apply(source)


def test_display_primitives_reject_unbounded_and_wrong_shapes() -> None:
    with pytest.raises(MeshImportError):
        _ = split_ids(np.zeros(65537, dtype="<u8"))
    with pytest.raises(MeshImportError):
        _ = split_ids(np.zeros(1, dtype="<i8"))
    with pytest.raises(MeshImportError):
        _ = join_ids(np.zeros((1, 3), dtype="<f4"))
    with pytest.raises(MeshImportError):
        _ = DisplayTransform().apply(np.array([[float("nan"), 0, 0]], dtype="<f4"))
    with pytest.raises(MeshImportError):
        _ = DisplayTransform(scale_power=-1024)
