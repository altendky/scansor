# Declared Analytic Model Factor and Preflight

## Status and boundary

**Provisional internal implementation design, snapshot dated 2026-09-08.** This
is a compatibility-free, model-bound successor to the stepped-rotational-v0
factor and preflight records. It is synthetic-only, fixed-pose-shape-only,
non-public, and carries no compatibility, solver, fit-quality, physical-
validation, metrology, accuracy, or production-support claim.

These factor records are consumed by the separately owned
[declared execution successor](declared-analytic-model-execution.md). Factor and
preflight operations themselves do not execute a solver, select a backend or
initialization, evaluate held-out observations, produce a fit result, or
generalize pose correction. The legacy stepped factor and pose-correction path
retains its separate records.

## Model-bound factor construction

Factor construction accepts one fully revalidated, accepted declared-analytic
mapping result. It embeds the complete content-addressed model declaration and
binds the exact mapping-run ID. Exactly one declaration and one instantiated
factor are created for each accepted primary training mapping in canonical row
order. Candidate, membership, or observation availability does not create an
additional factor.

Each factor declaration binds the model, mapping run, mapping, candidate,
observation, element, role, and canonical row. Its instance binds that
declaration to the mapped model-frame point. After strict source-boundary
validation, factor construction consumes only mapped training records, not the
mapping's held-out observations. A changed mapping revision can change provenance
identities without changing the mathematical result; held-out values remain
absent from residual, Jacobian, activation, coverage, rank, and policy calculations.

At construction, each factor also records the ordered optimization-preflight
coverage cells containing its mapped element's primitive projection at the
model's nominal parameter vector. Validation independently recomputes these
classifications from the embedded declaration and factor point. Preflight counts
only these bound nominal classifications; trial parameters never remap or
reclassify a factor.

## Explicit activation and evaluation

An active-factor selection is a separate, content-identified ordered tuple bound
to one exact factor set and model. Empty selection is representable but cannot
pass preflight. Unknown IDs, duplicates, relative reordering, cross-factor-set
selection, and cross-model selection fail closed. Instantiated factors do not
activate themselves and no default selection is inferred.

A parameter vector binds one exact model ID and carries finite metre values.
Factor and preflight boundaries require its dimension to equal the declaration's
ordered parameter count. Evaluation calls the shared declared-geometry evaluator
for every explicitly active factor in factor-set order. It returns raw oriented
metre residuals and full Jacobian rows in declared parameter order with identity
normalization, unit factor weight, and linear loss. Element IDs remain opaque;
factor code does not parse them or dispatch on fixed parameter positions,
variants, element inventories, or counts.

## Optimizer-independent preflight

Malformed declarations, stale content identities, graph tampering, wrong-sized
vectors, and cross-model records fail boundary validation before diagnostics.
For a well-formed graph, preflight reports deterministic, independently
distinguishable conditions:

1. empty active selection;
2. missing declaration-required support;
3. insufficient declaration-owned nominal coverage;
4. values outside inclusive declaration bounds;
5. failed relationships, domains, or structural-validity predicates;
6. undefined or failed declared-geometry evaluation;
7. rank computation failure; and
8. rank below the declaration's required rank.

Required-support counts follow the declaration's ordered
`optimization_preflight.required_support` entries and count only active factors.
Coverage counts follow its ordered `coverage_cells` and count only active
factors' construction-time nominal classifications. These two failures remain
independent even when a policy happens to define similar element domains.

After bounds and structural validity pass, preflight evaluates the active
factors at the supplied vector. It selects Jacobian columns in the declared
relative-rank parameter order and forms
`J * parameter_scales / residual_scale` without reordering rows. Rank counts
singular values strictly greater than the largest singular value times the
declared relative threshold. Empty or all-zero matrices have rank zero. The
reported expected rank is the declaration's minimum required rank, not an
inferred parameter or element count. Observed rank above the requirement passes.
Execution-coordinate normalization and full-vector SVD step truncation do not
replace this declared-subset diagnostic.

Preflight eligibility means only that these declaration-owned checks passed. It
does not imply solver convergence, fit acceptance, physical validity, accuracy,
publication authorization, production readiness, or product support.

## Evidence and exclusions

Regression tests preserve the existing stepped fixed-pose residuals and
Jacobians through each stepped declaration. Focused constructed tests exercise a
one-parameter, one-element shell with unrelated IDs and policy, in addition to
the stepped declarations' multiple parameter relationships. Tests also cover
explicit activation, nominal coverage binding, varied declaration dimensions,
cross-model closure, malformed records, bounds, structural invalidity,
evaluation failure, rank deficiency, and held-out numerical isolation.

This slice does not add a generic factor or constraint language, arbitrary
callbacks, pose correction, joint pose-and-shape fitting, robust loss,
initialization policy, solver execution, backend selection, public schemas,
external clouds, CAD integration, physical validation, or acceptance policy.
