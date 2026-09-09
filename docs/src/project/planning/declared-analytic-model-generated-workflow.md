# Declared Analytic Model Generated Workflow

## Status and boundary

**Provisional internal implementation and bounded executable coverage, snapshot
dated 2026-09-09.** This workflow generates, maps, fits, assesses, publishes, and
read-only verifies two project-owned synthetic fixture families through the same
declaration-driven fixed-pose shape path. It supplies the bounded two-topology
evidence requested by issue #14. It does not define a public schema, compatibility
contract, general fixture registry, arbitrary declaration-authoring mechanism,
arbitrary-cloud admission, physical-validation result, metrology claim, production
stack, CAD integration, acceptance policy, or product support.

The two fixtures are an existing asymmetric stepped comparator and a coaxial tube.
Their declarations use only the initially supported oriented planes, coaxial
cylinders, exact scalar relationships, structural predicates, and bounded support
domains. The fixture declarations and sample selections are project-owned inputs;
they are not discovered from the generated coordinates.

## Implemented fixture evidence

| Fixture ID | Shape parameters | Elements | Rows | Known observation-to-model pose |
| --- | --- | --- | --- | --- |
| `asymmetric-stepped-v1` | 7 | 8 | 317: 230 training, 87 held-out | identity |
| `coaxial-tube-v1` | 3 | 4 | 192: 140 training, 52 held-out | `+90 deg` about `X`, then translation `(0.031, -0.017, 0.009) m` |

The stepped fixture retains the existing asymmetric declaration: three outward
cylinders, four axial planes, one datum plane, seven parameters, and eight
surfaces. It is a regression comparator, not a second interpretation of the tube.

### Coaxial tube declaration

The tube has nominal bore radius `8 mm`, outside radius `13 mm`, and length
`60 mm`, or `0.008 m`, `0.013 m`, and `0.060 m` respectively:

| Parameter ID | Inclusive bounds (m) | Nominal (m) | Diagnostic/execution scale (m) |
| --- | --- | --- | --- |
| `bore-radius` | `0.006..0.010` | `0.008` | `0.0005` |
| `outside-radius` | `0.011..0.016` | `0.013` | `0.0015` |
| `axial-length` | `0.045..0.075` | `0.060` | `0.004` |

Its ordered element inventory is:

| Element ID | Primitive and orientation | Exact bounded support domain |
| --- | --- | --- |
| `wall.outer` | coaxial cylinder, outward | `0 <= z <= axial-length` |
| `wall.bore` | coaxial cylinder, inward | `0 <= z <= axial-length` |
| `end.near` | oriented plane, normal `-Z`, offset `0` | `bore-radius <= rho <= outside-radius` |
| `end.far` | oriented plane, normal `+Z` | `bore-radius <= rho <= outside-radius` |

The `end.far` offset is an oriented-offset relationship with coefficient `1`,
constant `0`, and parameter `axial-length`. The structural predicates require a
strictly positive bore radius, wall separation strictly greater than `0.002 m`,
and axial length strictly greater than `0.040 m`.

The declared policies are intentionally context-specific:

| Context | Required support in element order | Coverage minima in element order | Required relative rank |
| --- | --- | --- | --- |
| mapping admission | `12, 12, 8, 8` | `8, 8, 4, 4` | `3` |
| optimization preflight | `6, 6, 4, 4` | `4, 4, 2, 2` | `3` |

Each coverage cell repeats the corresponding exact domain in the element table;
the cells are not inferred quadrants, sectors, or axial subdivisions. The near
base plane is required for support and coverage even though its fixed-pose shape
Jacobian is zero. The separate outer-wall, bore-wall, and far-end rows supply the
three declared shape-rank directions. This keeps topology-presence policy distinct
from rank policy rather than silently dropping a required surface because it does
not constrain this shape vector.

The tube sampling definition uses guarded grids on both cylinder walls and both
annular ends. Its model-to-observation inversion and the recorded mapping transform
use the exact observation-to-model rotation

```text
((1, 0,  0),
 (0, 0, -1),
 (0, 1,  0))
```

and translation `(0.031, -0.017, 0.009) m`. The stepped comparator uses identity
rotation and zero translation. These are known inputs, not estimated poses.

## Generation contract and artifacts

`src/scansor/declared_synthetic_fixtures.py` owns the closed fixture-ID selection,
the exact sample grids, canonical sample order, roles, and known pose.
`src/scansor/tube_model_declarations.py` owns the tube declaration and policies.
The shared generation records and operations are in
`src/scansor/declared_generation_models.py`,
`src/scansor/declared_generation.py`, and
`src/scansor/declared_generation_runs.py`.

`create_generation_request` accepts a fixture ID, seed, and positive noise sigma no
larger than `25e-6 m`. The current fixture and generator revisions are
`provisional-1`, and the sampling profile is `guarded-grid-v1`.
`prepare_generation` accepts only the exact matching
project-owned fixture declaration, ID, element and parameter order, sampling
revision, source frame, and pose. A self-consistent substituted declaration is not
thereby admitted. Request and pose bindings compare canonical bytes, including
the distinction between `+0.0` and `-0.0`; Python numeric equality alone cannot
establish exact fixture identity.

For every sample, generation checks the named analytic support and orientation at
the nominal declaration vector. It adds deterministic normal-only noise from the
bounded-normal revision, rejects draws beyond four sigma, and quantizes accepted
offsets to `1 nm`. Stable fixture-observation IDs bind the fixture and model,
sample key, expected element, sampling profile, and training or held-out role;
they do not depend on seed, noise sigma, run path, or realization.

The raw source is binary little-endian `float64` XYZ PLY in metres, expressed in
the declared observation frame. It contains coordinates only. Row IDs, order,
roles, expected element IDs, model-frame truth, analytic normals, and realized
normal offsets remain in provenance or ground truth rather than becoming trusted
source attributes.

A generation run contains exactly:

- `observations.ply`
- `provenance.json`
- `ground-truth.json`
- `manifest.json`
- `manifest.sha256`

The new generation format identifiers are:

- `scansor-declared-analytic-model-generated-provenance-v1`
- `scansor-declared-analytic-model-ground-truth-v1`
- `scansor-declared-analytic-model-generation-run-manifest-v1`

The provenance, truth, and manifest bind the exact declaration and model ID,
parameter and element order, row identities and roles, pose and source frame,
source hash, and generation identity. The manifest inventories the exact source,
truth, and provenance bytes. Exact replay remains provisional for the same
supported implementation and mathematical-library environment; the analytic grid
and normal transform still use platform mathematics, so no cross-platform
byte-identity claim follows.

## Shared staged workflow

The internal sequence is:

1. `create_generation_run` prepares and atomically publishes an exact owned
   fixture generation; `verify_generation_run` replays it read-only.
2. The existing inspection path reads the generated PLY without learning fixture
   semantics and publishes the canonical `float64` XYZ observation array.
3. `generated_fixture_provenance` binds the verified generation graph to that
   canonical inspection hash.
4. `declared_generation_runs.create_generated_mapping_run(output, generation_run,
   inspection_run, request)` verifies the exact generation and inspection inputs,
   runs shared declared mapping, and atomically publishes the mapping.
5. Shared factor instantiation, explicit activation, declared geometry evaluation,
   and optimizer-independent preflight consume the accepted mapping.
6. `declared_execution_runs.create_execution_run` invokes the bounded declared
   NumPy adapter and publishes the result and, for a completed result, separately
   derived held-out assessment.
7. `declared_execution_runs.verify_execution_run` reconstructs factors, selection,
   execution evidence, held-out assessment, and input references without invoking
   the optimizer.
8. `declared_truth_comparison.compare_truth` read-only verifies all four generation,
   inspection, mapping, and execution roots and presents raw parameter errors and
   training and held-out residual summaries. It publishes nothing and applies no
   quality threshold.

Generation and mapping use no top-level topology `variant`. Mapping contract,
result, and manifest identifiers advance to
`declared-analytic-fixed-pose-mapping-v2`,
`scansor-declared-analytic-model-mapping-v2`, and
`scansor-declared-analytic-model-mapping-manifest-v2`. V2 adds declaration-bound
generated provenance with wrapper revision `declared-generated-v1` while retaining
the legacy static and stepped generated provenance revisions. Declared
execution-run manifests remain v1 records but now bind an exact v2 mapping and v2
mapping-manifest reference.

`src/scansor/synthetic_fixture_replay.py` is the fixture-owned regeneration
selection boundary for retained synthetic provenance revisions. Shared mapping,
factor, preflight, declared execution, and geometry code operate on declarations
and records; they do not select topology by model or fixture name, parse known
element IDs, or dispatch on fixed parameter counts or positions. The legacy CLI
continues through the explicit verified stepped-provenance helper
`require_stepped_variant`. This workflow adds no new CLI exposure.

## Publication and verification boundary

Generation, mapping, and execution publication use new-directory, no-overwrite
staging with exact file inventories, size limits, descriptor-anchored reads,
entry identities, hashes, input-tree exclusion, and checks before and after atomic
publication. Mapping re-verifies the generation and inspection inputs during
publication. Execution similarly re-verifies mapping and inspection inputs.
Read-only verification rejects root replacement, changed inventories or content,
noncanonical records, source or declaration substitution, order changes, role or
held-out changes, pose changes, and cross-model artifact graphs even if nearby
hashes or manifests are recomputed.

## Bounded executable coverage

Executable coverage exercises both fixtures independently through generation,
inspection, mapping, factor construction, full-factor activation, preflight,
bounded NumPy execution, held-out assessment, publication, truth comparison, and
read-only verification. The final shape estimates are checked against an
independent unscaled linear least-squares oracle for these affine fixture supports.

Additional cases cover cross-model substitution; source, model, parameter- and
element-order, role, pose, and held-out tampering; publication mutation and
no-overwrite behavior; root relocation; backend-forbidden read-only verification;
and held-out-only sign-flipped noise that leaves training observations and
training execution numerics unchanged. Static checks prohibit topology dispatch
and fixed known parameter indices or counts in shared modules. Renamed and reordered copies
of each fixture exercise the complete shared workflow by changing only the
project-owned fixture-construction seam.

### Recorded nominal run

A Linux/Python `3.12.13` run with seed `7`, sigma `20e-6 m`, and all instantiated
training factors active produced the following raw summaries. Initial values
were nominal values plus alternating `+0.1` and `-0.1` times each parameter's
declared execution scale, in declaration order. Both reported
`backend-converged` termination and sealed `completed-not-assessed` results:

| Fixture | Training residual RMS (micrometres) | Held-out residual RMS (micrometres) | Maximum absolute parameter error (micrometres) |
| --- | --- | --- | --- |
| asymmetric stepped | 17.781509 | 18.957560 | 4.339667 |
| coaxial tube | 19.860663 | 20.014658 | 2.091214 |

These are observations from the constructed clouds, not thresholds. Verification
and raw truth comparison succeeded independently of these error magnitudes. The
integration tests reproduce this setup and compare the solved vector to the
independent affine-support oracle; they do not assign product acceptance.

This evidence demonstrates that two materially different, nominal constructed
families can use one declaration-driven implementation while preserving exact
identity, ordering, pose, provenance, role isolation, and model-owned policy. It
also demonstrates that inward and outward cylinders, a relationship-bound moving
plane, annular and axial domains, different inventories, and a nonidentity known
pose fit within the implemented vocabulary.

It does **not** demonstrate arbitrary topology, arbitrary-domain sampling,
automatic model or correspondence discovery, general declaration authoring,
external observation admission, robust behavior under adversarial combined
conditions, or physical accuracy. Two nominal constructed families cannot establish
that all valid declarations are sampleable or that future primitives,
relationships, policies, and degeneracies need no further design and evidence.

## Explicit deferrals and open ownership

The fixture registry shape and the authority for project-owned sampling remain
open, as does a broader declaration-authoring workflow. Arbitrary-domain sampling
and adversarial combined conditions remain beyond the two nominal constructed
families. Generic PLY, segmentation, recognition, correspondence inference, pose
estimation, joint pose-and-shape fitting, outlier-product policy, robust loss,
general solver or plugin APIs, CAD/Onshape import or publication, physical
observations, acceptance, metrology, production readiness, public formats, and
compatibility remain separately gated.

The earlier frozen experiments and their identities remain unchanged. This new
workflow is application implementation evidence and does not reinterpret CAD,
solver-fixture, or physical evidence.
