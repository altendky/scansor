# Declared Analytic Model Observation Mapping

## Status and boundary

**Provisional internal implementation, snapshot dated 2026-09-09.** This is a
compatibility-free successor to the stepped-rotational-v0 mapping format. It is
synthetic-only, non-public, and carries no compatibility, arbitrary-cloud,
physical-validation, metrology, accuracy, or production-support claim.

Operational publication admits only exact project-generated synthetic inputs that
pass fixture-owned provenance replay. The
[shared generated workflow](declared-analytic-model-generated-workflow.md) admits
the asymmetric stepped comparator and coaxial tube through one declaration-driven
path. Generic PLY, segmentation, pose discovery, correspondence inference,
refinement, and threshold learning remain excluded.

## Model-bound request, provenance, and artifacts

Each mapping request embeds one complete canonical `ModelDeclaration`, including
its content-addressed `model_id`. The request, all model-frame records and their
identities, the mapping result, and the mapping-run manifest bind that same model
ID. The mapping identifiers are now:

- request contract `declared-analytic-fixed-pose-mapping-v2`
- result format `scansor-declared-analytic-model-mapping-v2`
- manifest format `scansor-declared-analytic-model-mapping-manifest-v2`

V2 removes the top-level stepped `variant`. It adds
`DeclaredGeneratedFixtureProvenance` revision `declared-generated-v1`, which binds
the exact generation request and run, declaration and model ID, element and
parameter order, source hash, canonical inspection hash, row IDs, roles, and
held-out indices. The legacy static and stepped generated provenance revisions
remain valid inputs for their existing paths; they are not promoted to or
reinterpreted as declaration-bound generation.

Replay reconstructs and revalidates the embedded declaration, uses the
fixture-owned regeneration selection in
`src/scansor/synthetic_fixture_replay.py`, verifies every record binding,
recomputes mapping from the referenced canonical observation array, and requires
byte-identical output. Declaration tampering, stale model IDs, source or pose
substitution, role or order changes, and cross-model manifest/result mixing fail
closed.

## Deterministic association

Held-out rows are removed before candidate construction. For every training row,
mapping transforms the point by the declared observation-to-model rigid transform
and traverses `declaration.elements` without parsing IDs or assuming an element
count. The shared declared-geometry evaluator supplies primitive distance,
primitive projection, bounded-domain membership, and boundary clearance at the
declaration's nominal parameter vector.

The existing deterministic policy is retained:

1. supports whose projection is outside their bounded domain do not classify;
2. in-domain supports beyond maximum support distance establish an outlier rather
   than a candidate;
3. an in-distance support inside the transition guard makes the row a transition;
4. surviving candidates are ordered by absolute distance and element ID;
5. insufficient separation between the two closest candidates is ambiguous;
6. otherwise the closest candidate supplies one primary geometric mapping.

Transition, ambiguity, gap, and outlier exclusions remain complete, identified
analyses. Any exclusion rejects the mapping while preserving diagnostics.

## Declaration-owned admission

Required-support and coverage minima come only from
`declaration.mapping_admission`; requests cannot override them. Both count only
accepted primary training mappings. Required support is reported in declaration
order. Each ordered coverage cell classifies the mapped element's primitive
projection at nominal parameters through its own declared bounded domain and
reports its declared cell ID and observed count independently of support and rank.

Rank rows are evaluator parameter Jacobians for accepted primary training
mappings at nominal parameters, in mapping order. Columns follow the policy's
declared rank-parameter order. The dimensionless matrix is
`J * parameter_scales / residual_scale`; descending singular values and rank use
the declared strict relative threshold and required rank. Candidates,
memberships, instantiated factors, active factors, and held-out rows never
contribute support, coverage, or rank counts.

Missing required support, insufficient coverage, and rank deficiency are separate
ordered rejection reasons. A required element may contribute no shape rank: the
tube's `end.near` base plane is still required for support and coverage despite
its zero fixed-pose parameter-Jacobian row. Normals remain optional, untrusted
diagnostics and never affect classification or admission.

## Generated publication boundary

`declared_generation_runs.create_generated_mapping_run(output, generation_run,
inspection_run, request)` opens and read-only verifies the exact declared
generation graph before mapping. The generation-derived provenance must exactly
equal the request's model-, pose-, frame-, source-, canonical-, row-, and
held-out-bound fixture provenance.

The recorded generation pose and mapping pose must have identical canonical
bytes; a numerically equal signed-zero substitution is rejected.

Publication uses no-overwrite staging and excludes output from both generation
and inspection input trees. It rechecks the anchored generation and inspection
inputs before and after atomic mapping publication and rejects changed roots,
entries, inventories, or content. Ordinary mapping verification remains read-only
and rebuilds the same shared mapping from fixture replay and the inspection's
canonical coordinates.

Shared mapping contains no topology-name, model-name, known-element, fixed-count,
or fixed-parameter-index dispatch. Fixture revision selection remains outside the
mapping geometry and admission operations. The declared factor, preflight, and
[execution successor](declared-analytic-model-execution.md) reuse the accepted
model-bound result without adding a topology branch.

## Held-out isolation and evidence

Held-out coordinates and attributes cannot affect candidates, mappings,
thresholds, support, coverage, rank, tuning, or any diagnostic derived from
training. They receive only identified
`post-fit-evaluation/not-evaluated` records during mapping.

Executable coverage includes both generated fixture families, deliberate
cross-model and provenance substitution, source/element/order/pose/held-out
tampering, mutation during publication, backend-free read-only downstream
verification, held-out-only noise changes, and complete renamed/reordered fixture
workflows. These are bounded constructed checks, not evidence for external-cloud
admission, arbitrary-domain sampling, physical observations, fit quality, or
product support.
