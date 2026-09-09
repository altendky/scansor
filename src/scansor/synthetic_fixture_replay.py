from __future__ import annotations

from dataclasses import dataclass

from scansor.declared_generation import (
    generated_fixture_provenance as declared_fixture_provenance,
)
from scansor.declared_generation import (
    prepare_generation as prepare_declared_generation,
)
from scansor.declared_generation_models import DeclaredGeneratedFixtureProvenance
from scansor.generation_models import GenerationRequest
from scansor.mapping_models import FixtureProvenance, SyntheticFixtureProvenance
from scansor.ply import canonical_npy, parse_ply
from scansor.serialization import sha256
from scansor.stepped_rotational_generation import (
    generated_fixture_provenance,
    prepare_generation,
)
from scansor.synthetic_fixture import FIXTURE_FRAME, prepare_synthetic_fixture


@dataclass(frozen=True)
class SyntheticFixtureReplay:
    canonical: bytes
    frame: str
    held_out_row_indices: tuple[int, ...]
    inspection_replay_raw: bytes | None
    provenance: FixtureProvenance
    source: bytes


def replay_fixture(provenance: FixtureProvenance) -> SyntheticFixtureReplay:
    """Generation-owned fixture selection, outside shared mapping and execution."""
    if isinstance(provenance, SyntheticFixtureProvenance):
        fixture = prepare_synthetic_fixture(provenance.variant)
        return SyntheticFixtureReplay(
            canonical=fixture.canonical,
            frame=FIXTURE_FRAME,
            held_out_row_indices=fixture.held_out_row_indices,
            inspection_replay_raw=None,
            provenance=fixture.provenance,
            source=fixture.source,
        )
    if isinstance(provenance, DeclaredGeneratedFixtureProvenance):
        declared = prepare_declared_generation(provenance.generation.request)
        parsed = parse_ply(declared.source, "m", 65_536, len(declared.provenance.rows))
        canonical = canonical_npy(parsed.canonical)
        return SyntheticFixtureReplay(
            canonical=canonical,
            frame=declared.provenance.source.frame,
            held_out_row_indices=declared.provenance.held_out_row_indices,
            inspection_replay_raw=declared.source,
            provenance=declared_fixture_provenance(declared, sha256(canonical)),
            source=declared.source,
        )
    generated = prepare_generation(
        GenerationRequest(
            noise_sigma_m=provenance.noise_sigma_m,
            sampling_profile=provenance.sampling_profile,
            seed=provenance.seed,
            variant=provenance.variant,
        )
    )
    parsed = parse_ply(generated.source, "m", 65_536, len(generated.provenance.rows))
    canonical = canonical_npy(parsed.canonical)
    return SyntheticFixtureReplay(
        canonical=canonical,
        frame=generated.provenance.source.frame,
        held_out_row_indices=generated.provenance.held_out_row_indices,
        inspection_replay_raw=generated.source,
        provenance=generated_fixture_provenance(generated, sha256(canonical)),
        source=generated.source,
    )
