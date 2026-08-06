# Stepped Rotational v0 Observation Mapping

## Status

**Provisional internal implementation design, snapshot dated 2026-07-31.** This
page bounds one application-owned observation and mapping slice named
`stepped-rotational-v0`. It is non-public and has no compatibility promise. It
does not replace, import, reinterpret, or modify the frozen experiment-local
`stepped-rotational-v1` generator, solver, Track 5 evidence, reconciliation,
contracts, or tolerances.

## Claim Boundary

This slice applies only to deterministic synthetic canonical arrays constructed
for one declared stepped-rotational variant at a time. It supports the three
fixture-local cylindrical bands, four axial planar regions, and the optional
asymmetric datum-flat region described below. It makes no generic-object,
arbitrary-cloud, physical-cloud, production, compatibility, accuracy, or
metrology claim. It does not establish a public schema, supported adapter, fit,
CAD integration, or product behavior.

The application constants are intentionally restated under a new contract rather
than imported from `experiments/`. Agreement with frozen generated evidence is
context, not schema compatibility or a dependency.

Persisted mapping publication accepts only an application-owned deterministic
synthetic fixture revision. The application recomputes its exact binary PLY,
canonical NPY, frame, variant, held-out row, and content hashes without importing
experiment code. A user-supplied `synthetic` label, geometric compatibility, or
matching dimensions is insufficient. Direct pure mapping remains available for
constructed adverse arrays, but those arrays cannot be published as mapping runs.

## Frames, Transform, and Units

The model frame is right-handed and uses metres:

- the rotational axis is the oriented local `+Z` axis
- the origin is the intersection of that axis with the declared `z = 0` datum
- the asymmetric datum plane has outward normal `+X`; `+Y = +Z cross +X`
- the axisymmetric variant retains the declared `+X` and `+Y` orientation even
  though its geometry does not independently observe roll
- the three cylindrical supports have radii `0.012`, `0.018`, and `0.014 m` over
  closed axial intervals `0..0.020`, `0.020..0.050`, and `0.050..0.080 m`
- axial supports lie at `z = 0`, `0.020`, `0.050`, and `0.080 m`; their radial
  domains are respectively `0..0.012`, `0.012..0.018`, `0.014..0.018`, and
  `0..0.014 m`
- the asymmetric datum support is `x = 0.016 m`, over `z = 0.020..0.050 m` and
  `y = +/-0.008246211251235319 m`; the middle cylinder and adjacent transition
  planes use the corresponding `x <= 0.016 m` trim

The required rigid transform is explicitly observation-to-model:

```text
p_model_m = R_observation_to_model * p_observation_m
            + t_observation_to_model_m
```

`R` must be finite, orthonormal, and proper with determinant `+1` within an
explicit contract validation tolerance. Translation is finite and measured in
metres. Scale is exactly `1`; reflection, affine scale, unit conversion, pose
estimation, transform inversion, and frame inference are absent. Missing,
misdirected, non-rigid, ambiguous, or invalid transforms fail before candidate
construction with a diagnostic naming the violated precondition.

The input must refer to one verified inspection report and `canonical.npy` by
inspection run ID, report SHA-256, canonical SHA-256, row count, coordinate unit,
and frame label. The mapping artifact refers to that array and never duplicates
it. Raw source cloud, canonical cloud, and mapping artifact remain separate.
This bounded slice rejects canonical revisions above `20,000` rows before
mapping construction; the limit is application-owned and not a product capacity
claim.

## Inputs and Identity

One mapping contract and run names exactly one fixture variant: `axisymmetric` or
`asymmetric-datum-flat`. The strict input includes:

- the verified inspection/canonical revision record
- the recomputed synthetic fixture ID, revision, variant, source hash, canonical
  hash, and aggregate content hash
- the explicit observation-to-model rigid transform
- the exact fixture variant
- explicit application-owned thresholds
- an explicit strictly increasing canonical row-index held-out list fixed before
  mapping

Threshold defaults are conservative synthetic-fixture application values. They
are neither copied nor derived from Track 5's `1e-9 m`/`1e-9 rad` CAD
reconciliation tolerances. Every effective threshold is serialized as input.

Observation, candidate, membership, mapping, and exclusion IDs are deterministic
row-derived hashes. Their digest input includes the inspection run ID, report
hash, canonical hash, contract version, variant, and canonical row index. IDs are
revision-scoped and sorted by canonical row index, never source-platform identity
or a compatibility promise.

## Candidate Construction

Mapping is analytic, pointwise, and deterministic. It performs no data-derived
pose, threshold, support, mapping, or iterative refinement. For each training row:

1. Apply the declared observation-to-model transform.
2. Evaluate every supported element in fixed element-ID order.
3. Compute signed analytic-support distance and bounded projected-domain status.
4. Exclude a transition guard around every axial, radial, datum, and trim
   boundary. Cylinder-trim clearance is the Cartesian `x` margin to the trim
   plane. A transition hit on any bounded support that is also within the
   declared support-distance threshold overrides every otherwise valid candidate
   for that row. There is no edge clamp.
5. Retain candidates whose projection is in the guarded interior and whose
   absolute support distance is at most the declared distance threshold.
6. Sort candidates by absolute distance and then element ID.

Candidate kinds are cylindrical, axial planar, and datum planar. A membership is
recorded for every retained candidate; overlapping memberships do not create an
extra mapping or factor. Candidate and mapping counts therefore remain distinct.

The outcomes are:

- one candidate, or a best candidate separated from the runner-up by at least the
  declared minimum geometric clearance, receives one primary mapping
- two or more candidates inside the clearance margin are `ambiguous`; no mapping
  is created and the run is rejected
- a point whose bounded projection is in a transition guard is `transition`; it
  is excluded rather than assigned to an adjacent region
- a point with at least one bounded projected support but no candidate inside the
  distance threshold is `outlier`
- a point with no bounded projected support is `gap`
- held-out rows are `held-out` before candidate construction and are not mapped
- any unsupported or internally inconsistent state is an explicit exclusion or
  a malformed-input failure, never a fallback assignment

Ambiguity, gaps, outliers, transitions, missing required regions, insufficient
coverage, and rank deficiency fail closed. A completely analyzed finite mapping
may still be serialized with disposition `rejected` and ordered reason codes.
Malformed, integrity-mismatched, invalid-transform, shape/schema-invalid, or
nonfinite input produces no mapping run.

The reported margin is **geometric clearance**, not confidence or probability.

## Held-Out Leakage Barrier

Held-out row indices are explicit, sorted, unique, in range, and committed in the
input before mapping. They are removed before candidate construction and cannot
affect initialization, mapping or refinement, factor activation, weights, loss,
bounds, coverage or rank acceptance, thresholds, or threshold tuning. This slice
has none of those fit mechanisms, but records the empty influence paths so replay
can enforce the boundary.

Held-out rows receive only identified `post-fit-evaluation/not-evaluated` records.
They have no training candidates, memberships, mappings, instantiated factors,
active factors, coverage cells, rank rows, or normal-based decisions. The
[successor execution/result contract](stepped-rotational-v0-execution-result.md)
adds only a separately invoked post-fit nominal-support assessment after a sealed
completed result; it does not alter mapping records. Any held-out ID in a
training-derived collection is a leakage failure, not a rejected publishable
mapping.

## Diagnostics and Acceptance

Diagnostics are deterministic and training-only. They include:

- total, training, held-out, candidate, observation, membership, mapping, and
  exclusion counts
- per-element mapping counts in fixed element order
- transition, ambiguity, gap, outlier, and held-out counts and ordered IDs
- candidate absolute distances and geometric clearances in metres
- required-region minimum counts, missing-region IDs, and coverage disposition
- a fixed analytic shape-incidence matrix over mapped training observations,
  singular values, declared rank threshold, observed rank, and required rank
- rank/degeneracy precondition failures distinct from missing coverage
- supplied-normal presence and finite/nonzero magnitude diagnostics
- stable revision, contract, variant, row, candidate, observation, membership,
  mapping, and exclusion identifiers
- ordered rejection reason codes and final `accepted` or `rejected` disposition

The axisymmetric variant requires shape-incidence rank `6`; the asymmetric
datum-flat variant requires rank `7`. These ranks cover only fixture-local shape
incidence for the declared supports, not pose observability, numerical solver
rank, accuracy, or general identifiability. Every required element must meet its
explicit minimum training count. Present normals never affect candidate
classification, acceptance, coverage, rank, ordering, or geometric clearance.

Missing normals are valid. Present normals are untrusted diagnostics only. They
are checked as finite and nonzero because the existing canonical inspection
format already enforces that boundary, but they are neither normalized nor
treated as trusted orientation evidence. Trusted normals are never synthesized.

## Record Separation

The mapping record keeps these concepts explicitly distinct:

- raw cloud: referenced source provenance from inspection, not embedded
- canonical cloud: referenced `canonical.npy`, not embedded
- candidates: analytic per-training-row possibilities
- observations: accepted training observations plus separately held-out records
- memberships: candidate classifications, independent of mappings
- mappings: one accepted primary association per mapped training observation
- instantiated factors: explicitly absent in this slice
- active factors: explicitly empty
- exclusions: deterministic non-mapped training outcomes
- held-out observations: predeclared post-fit-evaluation records only
- fitted results: explicitly absent
- CAD evidence: explicitly absent and prohibited as an input
- future physical reference: explicitly absent and prohibited as an input

Mapping does not instantiate a factor. Observation presence, candidate overlap,
membership, and mapping do not activate factors.

## Application and Filesystem Boundaries

The mathematical application module is small and pure. It consumes validated
Pydantic input plus a canonical NumPy array and returns a validated mapping
record. It imports no experiment runner, CLI, filesystem publication code,
optimizer, or CAD behavior. It performs no CAD access. CAD reconciliation remains
strictly post-fit and outside this slice; frozen evidence and tolerances do not
change.

Serialization and filesystem publication are separate. A mapping run contains
exactly:

- `mapping.json`: canonical ASCII JSON mapping record
- `manifest.json`: canonical ASCII JSON inventory of `mapping.json` plus the
  external inspection/canonical revision references
- `manifest.sha256`: exact SHA-256 sidecar for `manifest.json`

Publication refuses an existing path and prepares all bytes before creating the
output. Descriptor-anchored ancestor checks prevent a mapping output anywhere
within its referenced inspection tree and are repeated around publication. A
post-rename failure rolls the exact unchanged output back before cleanup.
An unpublished stage is removed only after the exact directory is atomically
quarantined and its complete contents still match identities recorded during
this publication; changed or unknown content is preserved rather than deleted.
Integrity or nonfinite failures publish nothing. Read-only replay
requires the exact three-file inventory, revalidates the referenced four-file
inspection run without changing its inventory or verification semantics,
recomputes mapping from the referenced `canonical.npy`, and requires canonical
byte equality.

No `map` or `verify-mapping` CLI command is added in this slice. This avoids
silently expanding the existing CLI settings and artifact contracts before that
integration is separately designed. There is no distinct CLI rejection exit
code. The existing `fit` command remains unavailable.

## Explicit Deferrals

This slice excludes fitting, factor construction, active-factor selection,
weights, losses, bounds, initialization, solver invocation, fitted results,
post-fit held-out evaluation, CAD use, CAD publication, adapters, arbitrary PLY
mapping, generic geometry, physical observations, physical references, GUI work,
photogrammetry, packaging, release work, and changes to frozen evidence.
