from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

from scansor.mapping_models import SyntheticFixtureProvenance
from scansor.ply import canonical_npy, parse_ply
from scansor.serialization import canonical_json, sha256

FIXTURE_FRAME = "stepped-rotational-v0-synthetic-model-frame"
FIXTURE_ID = "stepped-rotational-v0-synthetic-fixture"
FIXTURE_REVISION = "1"

Variant = Literal["axisymmetric", "asymmetric-datum-flat"]


@dataclass(frozen=True)
class PreparedSyntheticFixture:
    canonical: bytes
    held_out_row_indices: tuple[int, ...]
    provenance: SyntheticFixtureProvenance
    source: bytes


def fixture_points(variant: Variant) -> tuple[tuple[float, float, float], ...]:
    points: list[tuple[float, float, float]] = []
    for radius, z in ((0.012, 0.010), (0.018, 0.035), (0.014, 0.065)):
        points.extend(((0.0, radius, z), (-radius, 0.0, z), (0.0, -radius, z)))
    for station, radii in (
        (0.0, (0.004, 0.006, 0.008)),
        (0.020, (0.014, 0.015, 0.016)),
        (0.050, (0.015, 0.016, 0.017)),
        (0.080, (0.004, 0.007, 0.010)),
    ):
        points.extend((-radius, 0.0, station) for radius in radii)
    if variant == "asymmetric-datum-flat":
        points.extend(
            ((0.016, -0.004, 0.030), (0.016, 0.0, 0.035), (0.016, 0.004, 0.040))
        )
    points.append(points[0])
    return tuple(points)


def _source_bytes(points: tuple[tuple[float, float, float], ...]) -> bytes:
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property double x\n"
        "property double y\n"
        "property double z\n"
        "end_header\n"
    ).encode("ascii")
    return header + b"".join(struct.pack("<ddd", *point) for point in points)


def prepare_synthetic_fixture(variant: Variant) -> PreparedSyntheticFixture:
    points = fixture_points(variant)
    source = _source_bytes(points)
    parsed = parse_ply(source, "m", 65_536, len(points))
    canonical = canonical_npy(parsed.canonical)
    held_out = (len(points) - 1,)
    source_sha = sha256(source)
    canonical_sha = sha256(canonical)
    content_sha = sha256(
        canonical_json(
            {
                "canonical_sha256": canonical_sha,
                "fixture_id": FIXTURE_ID,
                "frame": FIXTURE_FRAME,
                "held_out_row_indices": held_out,
                "revision": FIXTURE_REVISION,
                "source_sha256": source_sha,
                "variant": variant,
            }
        )
    )
    provenance = SyntheticFixtureProvenance(
        canonical_sha256=canonical_sha,
        content_sha256=content_sha,
        source_sha256=source_sha,
        variant=variant,
    )
    return PreparedSyntheticFixture(
        canonical=canonical,
        held_out_row_indices=held_out,
        provenance=provenance,
        source=source,
    )
