# Stepped Rotational v0 Factor Contract

## Status

**Provisional internal implementation design, snapshot dated 2026-07-31.** This
page defines the next bounded application-owned `stepped-rotational-v0` slice. It
is synthetic-only, fixed-topology, non-public, and has no compatibility promise.
It does not import, modify, or replace frozen experiment contracts, evidence, or
tolerances. Passing this slice is implementation evidence for constructed inputs,
not a fit, product-support, physical-accuracy, or metrology claim.

## Boundary

The slice consumes one accepted application mapping in memory and produces pure,
content-bound factor and diagnostic records. Mapping publication remains
unchanged: `MappingResult.instantiated_factors`, `active_factor_ids`, and
`fit_result` remain truthfully absent or empty. There is no factor publication
format, factor CLI, placeholder CLI, `fit` command, filesystem access, CAD access,
Onshape access, or optimizer in this slice.

These records remain distinct:

- a **factor contract** declares fixed model semantics, parameters, bounds,
  scales, residual normalization, unit weight, linear loss, and rank policy
- a **factor declaration** binds one intended contribution to an exact mapping,
  candidate, observation, element, canonical row, and mapping revision
- an **instantiated factor set** contains validated declarations and their mapped
  model-frame points in mapping-relative order
- an **active-factor selection** explicitly projects an instantiated factor set;
  mapping, membership, candidate, or observation presence never activates it
- an **evaluation** contains raw metre residuals and exact Jacobian rows for one
  declared mathematical problem
- **preflight diagnostics** report structural, support, coverage, and
  dimensionless-rank evidence without invoking an optimizer
- **solver execution** is absent and remains separately gated
- **held-out evaluation** is absent; held-out records are never traversed here
- a **fit result** is absent because no solver executes
- **CAD evidence** remains separate post-fit source evidence and is prohibited as
  factor input
- an **independent physical reference** remains later validation evidence and is
  prohibited as factor input

## Fixed Model And Problems

Both variants fix station 0 at `0 m`, the local `+Z` axis through the origin, the
right-handed frame, topology, element order and identity, mapping transform,
support identity, and nominal pose. The asymmetric datum plane defines `+X`.
Normals are optional untrusted mapping diagnostics and do not enter any factor,
residual, Jacobian, selection, weight, rank, or acceptance calculation.

This contract defines two separate mathematical problems and no joint solver:

1. **Fixed-pose shape** varies shape while every mapped point and pose remain
   fixed. Axisymmetric parameter order is `(r1, r2, r3, s20, s50, s80)` in metres.
   Asymmetric order appends `datum_x` in metres.
2. **Fixed-geometry pose correction** fixes nominal fixture geometry and varies
   `(tx, ty, tz, phix, phiy, phiz)` in metres and radians. For mapped model-frame
   point `p`, evaluation uses `q = Exp(phi) p + t`. Pose correction changes only
   residual evaluation. It never remaps, reclassifies, clamps, changes a support,
   changes element identity, or filters factor rows.

Nominal shape is `(0.012, 0.018, 0.014, 0.020, 0.050, 0.080)` with asymmetric
`datum_x = 0.016`. Application-owned inclusive bounds are:

| Parameter | Lower | Upper | Scale |
| --- | ---: | ---: | ---: |
| `r1` | `0.010 m` | `0.0145 m` | `0.001 m` |
| `r2` | `0.017 m` | `0.020 m` | `0.001 m` |
| `r3` | `0.012 m` | `0.0145 m` | `0.001 m` |
| `s20` | `0.018 m` | `0.022 m` | `0.001 m` |
| `s50` | `0.047 m` | `0.053 m` | `0.001 m` |
| `s80` | `0.077 m` | `0.083 m` | `0.001 m` |
| `datum_x` | `0.015 m` | `0.0165 m` | `0.001 m` |

Pose-correction translation bounds are `[-0.003, +0.003] m`; rotation-vector
bounds are `[-0.08, +0.08] rad`. Translation scales are `0.001 m`, rotation
scales are `1 rad`, and the residual scale for dimensionless diagnostics is
`0.001 m`. These are application-owned fixture values, not initialization,
optimizer scaling, public defaults, CAD reconciliation tolerances, or acceptance
thresholds. This slice owns no initialization.

Structural validity requires finite values, parameters inside their declared
bounds, positive radii, `0 < s20 < s50 < s80`, every axial band wider than
`0.004 m`, `r2-r1 > 0.002 m`, `r2-r3 > 0.002 m`, and, for the asymmetric
variant, `0 < datum_x < r2` with
`sqrt(r2^2-datum_x^2) > 0.001 m`. These checks preserve the fixed guarded
topology; they are not residual penalties.

## Residuals And Derivatives

Only mapped training rows on the three cylindrical, four axial-planar, and
optional datum-planar elements are supported. Raw oriented residuals in metres
are:

```text
cylinder:          sqrt(x*x + y*y) - radius
station 0 and 20: -(z - station)
station 50 and 80: +(z - station)
datum flat:         x - datum_x
```

The axial signs follow the application mapping element contract: outward `-Z` at
stations 0 and 20, and outward `+Z` at stations 50 and 80. Shape Jacobians are
exact sparse rows. Station 0 is fixed and therefore has a zero shape row.

Pose uses the truth-centred rotation-vector convention above. If `g` is the
support gradient at `q`, the analytic row is
`[g^T, -g^T R [p]x J_r(phi)]`, where `J_r` is the SO(3) right Jacobian. At zero it
reduces to `[g^T, (p cross g)^T]`. Independent finite differences must verify
zero, perturbed, small-angle, and legal near-bound coordinates. Nonfinite or
wrong-sized vectors fail. Cylinder evaluation at zero radial distance fails as
undefined rather than inventing a gradient.

Residual normalization is identity, factor weight is exactly one, and loss is
exactly linear. They are explicit immutable declarations, not tunable or
data-derived choices. CAD and evidence values never tune this contract.

## Identity, Provenance, And Activation

All Pydantic records are strict, frozen, extra-forbidding, and nonfinite-rejecting.
Canonical semantic JSON is ASCII, sorted, and newline-terminated. Full SHA-256
content IDs exclude only their own ID field. Factor identity binds the contract,
factor-set provenance, exact mapping run ID, mapping request and content hashes,
variant, canonical row, observation, candidate, mapping, element, factor kind,
and mapped point. Tampering therefore invalidates validation rather than changing
meaning under an old ID.

Every public pure factor API revalidates supplied Pydantic records from their
Python representation before reading them. Frozen models alone are insufficient:
Pydantic `model_copy(update=...)` does not validate updates. A stale content ID,
forged nested point, malformed parameter vector, or altered selection therefore
fails at the API boundary before declaration, selection, evaluation, or preflight
traversal.

Instantiation requires an accepted mapping and follows its mapping-relative
order. It creates exactly one declaration and factor per primary mapping, never
per candidate or membership. A rejected mapping cannot instantiate factors.
Selection is a separate explicit ordered ID tuple bound to the exact factor set.
It rejects unknown IDs, duplicates, factor-set-relative reordering, held-out
references, and content or provenance tampering. Empty selection is representable
but cannot pass preflight. Selection never defaults from available records.

## Held-Out Isolation

Instantiation traverses only `MappingResult.mappings` and their corresponding
training observations and candidates. It must not traverse
`held_out_observations`. No held-out row, ID, value, normal, candidate, membership,
or mapping may influence declarations, instantiation, activation, parameters,
bounds, scales, normalization, weights, loss, residuals, Jacobians, coverage,
rank, thresholds, or failure codes. Tests replace held-out IDs, coordinates, and
normals by constructing separate valid exact mapping revisions and require equal
training residuals, Jacobians, coverage, singular values, ranks, and failure
codes. An unchecked mutation inside an already identified revision is tampering
and fails boundary revalidation rather than producing records.

The exact source mapping run ID remains provenance. Regenerating the canonical
artifact after changing any row or source attribute creates a different mapping
revision because the mapping contract deliberately uses revision-scoped canonical
hashes for every row-derived ID. Different provenance IDs across such revisions
are not mathematical influence. A later durable identity design must decide
whether it needs separately content-addressed training and held-out partitions
before making a stronger cross-revision identity claim.

## Deterministic Preflight

Preflight is analyzable and optimizer-independent. It evaluates active factors in
factor-set order and returns stable ordered failure codes. Malformed or tampered
records fail Pydantic validation; a well-formed adverse case returns diagnostics
and `eligible_for_optimization = false` for future use.

Preflight entry requires an accepted source mapping and validated factor graph;
rejected source mappings and malformed or tampered records fail before an
analyzable preflight record. The required active-element inventory is all seven
axisymmetric elements or all eight asymmetric elements. Every required element
needs at least one active factor. Structural validity, bounds, finite evaluation,
and defined cylinder gradients are separate checks. Dimensionless Jacobians use
the declared parameter and residual scales. Rank is the number of singular values
strictly greater than `largest_singular_value * 1e-10`, an application-owned
relative policy rather than a frozen experiment or CAD tolerance.

Fixed-pose shape requires rank `6/6` axisymmetric or `7/7` asymmetric. Pose
correction requires the expected axisymmetric roll gauge and rank `5/6`, or full
asymmetric rank `6/6`. Axisymmetric rank 5 is eligible only when the smallest
right-singular vector aligns with local-Z roll; other deficiencies fail.
Asymmetric datum-factor ablation must report missing coverage and rank 5, while
missing elements, invalid geometry, out-of-bounds parameters, nonfinite
evaluation, radial-axis evaluation, unexpected gauge, and rank deficiency remain
distinguishable ordered preflight failures.

Preflight does not classify fit quality, execute a solver, evaluate held-out
observations, produce a fit result, accept geometry, or authorize publication.

Tests may use NumPy linear algebra, including `numpy.linalg.lstsq`, as an
independent exact linear recovery oracle over returned residuals and Jacobians.
That test-only calculation is not imported or invoked by `src/scansor`, does not
select initialization or policy, and is not an application optimizer, solver
execution, fit result, or product claim.

## Successor Execution Boundary

The [successor execution/result contract](stepped-rotational-v0-execution-result.md)
now bounds solver-independent invocation metadata, callback validation, untrusted
response handling, deterministic replay, and separately invoked raw held-out
assessment. It does not select a backend, persistence, fit-result publication,
expert acceptance, or CLI. Those choices, CAD reconciliation, and physical
validation remain later independent gates.
