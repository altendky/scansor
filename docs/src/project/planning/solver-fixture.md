# Track 4: Solver Evidence Fixture

## Status

**Provisional with bounded runner evidence, snapshot dated 2026-07-30.** The
internal
provisional `stepped-rotational-v1` generator, 15 scenarios, expected ranks and
gauges, fixture-local CAD tolerances, hashes, and clean-run checks exist. A
provisional experiment-local Python/SciPy runner now passes the bounded
mathematical execution gate for exactly all `15` frozen scenarios, including the
remaining adverse cases described below. Scenario execution passing means the
expected measured path occurred; individual synthetic-fixture dispositions still
include passed, review-required, failed, and unclassified. Nominal end-to-end
generated-experiment reconciliation is now observed; reusable implementation,
robust-loss selection, and challenger evidence remain open.
`scipy.optimize.least_squares` remains only the provisional first runner; the
contract and challenger evidence remain solver-neutral.

## Observed Bounded Gate

[`generated_solver_evaluator_v1.py`][solver-runner] requires exact CPython
`3.12.12`, NumPy `2.3.1`, and SciPy `1.16.1` under PEP 723. Its source SHA-256 is
`a998a433f402bbe52ff20311580742b55ed48a3565e0745699c764fffb92355d`.
The compact [retained evidence][solver-evidence] has SHA-256
`47d6caec9203e9daa0edd6ee0b9ead87b586e1cbc4c17855109eaad32e7b2256`
and is recomputed by `verify-evidence`. Verification fails before comparison on
another Python implementation or patch version because runtime identity is
retained in the evidence. The large generated corpus remains untracked.

The runner hash-verifies generator revision `1.0.5` and executes those exact
source bytes directly rather than reopening the path through a bytecode-capable
loader. It then materializes
observations, mappings, explicit factor declarations, and scenarios separately.
Only each scenario's `active_factor_ids` projects the objective. Memberships and
input observation IDs never instantiate or activate factors. All `167` held-out
observations have no factors, appeared in no fit callback, and were evaluated by
a separate oracle path against the recovered fixed-pose shape after fitting. The
held-out gate requires exactly `167` successful evaluations, zero support
failures, a finite maximum residual, and no residual above `2e-15 m`; the observed
maximum was `1.9081958235744878e-17 m`.
The reduced shape vector fixes station zero and directly bounds three radii,
three later stations, and the datum offset so positivity, ordering, annular
clearance, and nonempty guarded domains hold without penalty factors. This gate's
objective is the raw metre residual with identity normalization and weighting and
linear loss; SciPy's `x_scale` remains runner scaling rather than diagnostic
scaling.

The observed bounded result includes:

- exact cylinder, oriented axial-plane, and datum-flat raw residuals, exact
  generated guarded-domain decisions for all factors, and specific invalid-
  geometry and out-of-support diagnostics; maximum nominal raw residual was
  `1.3877787807814457e-17 m`
- noiseless fixed-pose shape recovery with maximum parameter error
  `1.5612511283791264e-17` in SI coordinates
- deterministic paired `+/-20 micrometre` normal noise over all `454` training
  factors, including `16` datum factors, preserving the truth estimate within
  `1.5612511283791264e-17 m`
- analytic raw shape Jacobians and exact additive rotation-vector pose Jacobians;
  the latter use `-g^T R [p]x J_r(phi)` away from zero and reduce to
  `[g^T, (p cross g)^T]` at truth. SciPy uses these analytic Jacobians rather than
  numerical differentiation
- independent five-point central differences and directional differences at
  `h = 3e-6` and `h/2`, for shape and pose at nominal, deterministic
  perturbed, and legal near-parameter-bound coordinates, plus a nonzero
  small-angle pose point exercising the series branch. Raw residual equations
  are support-domain independent, so every raw derivative probe evaluates the
  same ordered `230` explicit active factors and exact eight-element counts. The
  near-bound stencils would leave only `200` shape and `171` pose factors in
  callback support; those counts are retained as informational metadata only and
  never filter derivative rows or establish callback-domain validity. Separate
  shape/pose nominal and perturbed callback-domain probes invoke the real residual
  and analytic-Jacobian callbacks at the requested center and every unique
  coordinate and directional perturbation used by the `h` and `h/2` five-point
  checks. Their
  trace hashes prove exact candidate coordinates, callback paths, ordered active
  factor IDs, `230` returned rows, and frozen per-element counts for every
  invocation. Each shape probe has `49` unique candidates and `98` callback
  evaluations; each pose probe has `43` and `86`, respectively (`184` candidates
  and `368` callback evaluations total). Separately, each raw numerical shape
  probe makes `64` residual calls over `48` unique perturbations and each pose
  probe makes `56` calls over `42`; retained sequence hashes prove both step
  sizes were executed, and thresholded errors use the worse result across the
  two. The callback candidate union includes `+/-h/2`, `+/-h`, and `+/-2h`.
  Maximum raw errors are `1.3855583347321954e-12` for shape and
  `6.82413457339659e-12` for pose;
  maximum directional errors are `5.710543149461955e-12` and
  `1.1762563145722993e-11`. Maximum dimensionless errors are
  `1.3855583347321954e-12` and `6.82413457339659e-09`, below declared
  `3e-9` raw and `3e-6` dimensionless thresholds
- recovered-pose raw Jacobians, spectra, ranks, and coordinates bound by hashes,
  giving `5/6/5` for axisymmetric free roll, asymmetric full pose, and flat-factor
  ablation at their retained recovered coordinates. Separately labeled
  truth-origin compatibility spectra also give `5/6/5`; their corresponding
  smallest raw singular values are `7.800959636058453e-18`,
  `0.02291453196064221`, and `7.683570016457092e-18`
- separately labeled truth-origin compatibility dimensionless spectra using
  `1 mm` translation and residual scales and `1 rad` rotation scales, also giving
  `5/6/5`; their null singular values are `7.800959636058453e-15` and
  `7.683570016457091e-15`
- executable gauge evidence: axisymmetric and ablated Jacobian null vectors align
  exactly with local-Z roll, their roll-column maxima are
  `1.734723475976807e-18`, and distinct `-0.05/+0.05 rad` initial rolls retain
  those distinct equivalent solutions. Both free-roll solves in each scenario
  terminated successfully with finite non-roll error and final residual within
  the declared recovery and oracle tolerances. Datum-flat factors restore rank with
  `0.022914531960642207 m/rad` roll-column norm, separate the two roll residual
  vectors by `0.0007243192373931687 m`, and both solves terminate successfully,
  recover finite non-roll pose and final residual within tolerance, and recover
  roll to within `5.4586876224577774e-15 rad`
- the coverage reference comes from the complete immutable corpus of `454`
  explicit training-factor records rather than any coverage scenario selection;
  its mapping/cell hash is
  `b28d937bdda773d5105ad3074503599b75f20ceb101412a0327659d1418d6ff3`.
  Adequate coverage uses all `454` factors, all `15` variant-element mappings and
  their frozen cells, has shape rank `7`, recovers within
  `1.5612511283791264e-17 m`, and passes. Uneven coverage uses the exact frozen
  `163` factors, retains all `15` mappings while missing cells under `8` planar
  mappings, has observed rank `7`, and is review-required. Inadequate coverage
  uses only `112` band-1 factors, has rank `1`, and fails for `13` missing
  mappings and their cells even though SciPy terminates successfully with a
  `3.557640293472275e-13 m` maximum residual
- the linear-loss fixed-outlier control applies only the frozen `+0.002`,
  `-0.0015`, and `+0.0025 m` normal offsets, retains raw rank `7`, and is
  executed with a `0.0024759615384615345 m` maximum raw residual. Its disposition
  is explicitly unclassified because no robustness policy, loss scale, or product
  threshold is approved; no robust probe or classification was invented
- the one-factor mapping override to
  `mapping.axisymmetric.cylinder.band-3` is rejected as mapping-suspect after one
  incompatible-support traversal, with `453` factors unmodified, no fallback,
  callback, residual/Jacobian traversal, or solver invocation
- the legal `radius.band-1 = 0.012 m` lower bound passes with active mask
  `[-1, 0, 0, 0, 0, 0, 0]`, measured distance
  `9.999999960041972e-11 m`, raw rank `7`, feasible-tangent rank `6`, and valid
  geometry. The invalid `radius.band-2 = -0.018 m` declaration separately fails
  at the evaluator boundary with `radii must be positive` and zero factor,
  callback, or solver traversal
- the runner-local, non-normative mismatch realizes the frozen ellipse formula
  only for the `104` declared middle-cylinder training factors while preserving
  observation IDs, sampled angles, mappings, the circular model, and generated
  truth. Before solving, the declared semiaxes and ordered sampled angles give an
  analytic circular least-squares radius `0.017539685975004958 m`, residual span
  `0.0009238242748761359 m`, RMS `0.000321091706973385 m`, and maximum absolute
  residual `0.0005005513672541144 m`. The fitted radius and ordered residual vector
  agree within `9.79001602008367e-13 m`, below the frozen `2e-9 m` analytic-match
  tolerance; raw rank is `7`, classification is model-mismatch-suspect, and the
  measured disposition is review-required. Minimax distance is not acceptance
  evidence for this least-squares fit

The evidence record declares, but cannot prove from numerical execution alone,
that no CAD nominal or physical-reference values were supplied, generator truth
is the fit/evaluator oracle, generated held-out data remains evaluation only, and
Track 5 remains separate read-only upstream evidence. Executable checks establish
the in-memory factor/callback structure and held-out exclusion. The result supports
only the declared constructed mathematical cases. A robust-loss choice and
comparison, additional combined or non-finite adverse cases, a challenger,
physical validation, and product readiness remain open. The separate bounded
nominal CAD reconciliation is observed but does not change this solver gate's
evidence or claim boundary.

Runner-local preregistered acceptance-policy helpers derive dispositions from
retained diagnostics. Coverage derives from missing mappings/cells; mapping and
invalid-geometry failures require their exact fail-closed paths; active-bound
acceptance requires measured feasibility, rank, tangent rank, mask, and distance;
and mismatch review requires the analytic sampled-ellipse comparison. The
fixed-outlier execution has no approved disposition policy and remains
unclassified. The overall execution gate passes because each expected path and
policy result is verified, not because every scenario disposition is `passed`.

## Objective and Ownership

Instantiate Track 1's model/factor contract as deterministic mathematical
scenarios. Track 4 owns deterministic truth and observation generation, exact
evaluator expectations, reduced coordinates, derivative evidence, runner
transformations, scenario outputs, numerical diagnostics, and challenger
comparison. It must not redefine elements, memberships, mappings, factors,
relationships, units, signs, holdout meaning, or physical claims.

Generator truth uses the fixture-local, provisional pair defined by Track 1:
axis `+Z`; radius `12 mm` over `z = 0..20 mm`; radius `18 mm` over
`z = 20..50 mm`; radius `14 mm` over `z = 50..80 mm`; axial/transverse planes;
and, in the asymmetric variant, the genuine `x = 16 mm` cut over
`z = 20..50 mm` with outward normal `+X`.

## Provisional Numerical Shape

Compile positive base dimensions and increments into reduced coordinates so
radii, ordered axial stations, shared axes/stations, and datum intersections stay
structurally valid. Hard relationships create no penalties. For comparison, use
an `SE(3)` reference pose plus a six-dimensional local tangent increment unless
the user selects another explicitly documented convention; compare physical pose
and tangent derivatives rather than runner coordinates.

The fixed-pose/fixed-axis case is a derivative oracle only. Staged evidence then
frees transverse translation, exercises full pose on axisymmetric factors, and
adds datum-flat factors for the asymmetric case.

## Factor and Residual Pipeline

Materialize generator truth, canonical model, observations, explicit mappings,
instantiated factors, per-scenario active factor IDs, and held-out roles as
distinct inputs. Memberships remain independent metadata. Overlapping memberships
must not change factor count or activation. Generated held-out IDs must be absent
from objective construction and solver callbacks; their observations create no
fit factors.

Each factor should retain observation/element/mapping identity, factor kind,
provenance, physical scale, robust grouping, and decomposed weights. Raw physical
residuals and Jacobians are the authoritative mathematical evidence. Record each
transformation separately:

```text
raw physical residual/Jacobian
    -> declared residual normalization
    -> explicit effective weighting
    -> runner scaling
    -> robust objective
```

Never label a robustified or solver-returned Jacobian as the raw model Jacobian.

## Ordered Evidence Gates

1. **Contract/data separation:** pin Track 1 semantics and assert factor counts,
   explicit per-scenario factor activation, mappings, overlap behavior, and zero
   generated held-out factors.
2. **Exact evaluator oracle:** compare each analytic support evaluator and domain
   decision directly with generator truth before fitting.
3. **Reduced parameters:** check expansion and hard invariants over nominal,
   boundary, and randomized legal coordinates without solving.
4. **Expected evidence:** freeze truth, equivalence classes, expected ranks/nulls,
   diagnostics, tolerances, seeds, and synthetic-fixture dispositions before
   solver output.
5. **Derivatives:** compare analytic raw Jacobians with adaptive central
   differences and directional derivatives at nominal, perturbed, and near-bound
   points.
6. **Transformation pipeline:** verify normalization, weights, runner scaling,
   and robustification independently.
7. **Noiseless fixed-pose recovery:** recover shape under exact hard relationships
   before introducing pose freedom.
8. **Pose and ablation:** verify the axisymmetric free-roll equivalence class,
   asymmetric full-pose recovery, and flat-factor ablation restoring the roll
   null.
9. **Balanced noise:** apply deterministic paired perturbations with zero sum per
   declared pairing cell, assert perturbed elements and counts including the
   datum flat, and fix directional bias before fitting.
10. **Bounds/rank/coverage:** distinguish interior and active bounds, structural
   gauge, non-gauge deficiency, weak conditioning, uneven coverage, and
   inadequate coverage.
11. **Held-out regions:** reserve coherent axial strips and angular sectors,
    verify they create no fit factors, and evaluate them only after fitting.
12. **Weights/outliers:** verify overlap has no implicit objective effect; compare
   linear control with a declared smooth robust-loss case on fixed outliers.
13. **Adverse semantics:** isolate corrupted mapping, out-of-contract model
   mismatch, invalid geometry/evaluation, and ambiguous combined causes.
14. **Termination/disposition:** demonstrate that termination and
    synthetic-fixture disposition are independent.
15. **Reproducibility/challenger:** repeat from clean inputs, then freeze the
   fixture before comparing another solver's correctness/diagnostics ahead of
   performance or packaging.

## Diagnostic Evidence

Retain the raw physical Jacobian and a declared dimensionless diagnostic scaling,
including residual and parameter scale matrices, singular values, rank rule,
condition estimate, and interpretations of weak/null directions. Runner `x_scale`
is separate and does not define diagnosis.

The fixture should distinguish:

- native and normalized termination
- valid, invalid, and degenerate geometry
- full observability, structural gauge, rank deficiency, and weak conditioning
- adequate, uneven, and inadequate coverage
- inactive, expected-active, and suspicious-active bounds
- nominal, outlier-contaminated, and non-finite data
- mapping-consistent versus mapping-suspect evidence
- adequate-for-fixture versus model-mismatch-suspect evidence
- held-out-consistent, held-out-sensitive, and unavailable generalization
- passed, review-required, failed, and unclassified synthetic-fixture
  dispositions

Mapping and mismatch diagnoses remain evidence-qualified hypotheses, not claims
of unique cause.

## Disposition and Challenger Evidence

Synthetic-fixture disposition requires valid geometry, exact hard invariants,
declared raw training evidence, expected observability, adequate required-element
coverage, reviewed bound activity, no unresolved mapping/mismatch warning,
required generated held-out behavior, and a termination class allowed by the
preregistered scenario.
Thresholds are fixture-local and must not be inferred from SciPy output.

Passing supports implementation, formulation, and diagnostic correctness only
under these constructed scenarios. It supplies no physical-accuracy, metrology,
external-source, or product-support evidence.

A challenger receives identical semantics, observations, mappings, instantiated
factors and active-factor selections, weights, holdout, and robust objective. It
may use different coordinates,
iterations, or status codes, but must produce geometrically equivalent
predictions, preserve bounds/hard relationships, pass derivative evidence, expose
sufficient raw evaluation data, reproduce diagnostic classes, and keep
termination separate from disposition.

## Stage-Entry User Decisions and Quick Checks

Before the applicable scenarios begin, the user must approve the pose
perturbation convention, initial robust loss (soft-L1 versus another smooth
choice, with linear control), whether the asymmetric baseline estimates full
six-degree pose in one scenario, whether the first challenger must support sparse
Jacobians, and whether invalid candidates must occur during solving or may be
tested at the evaluator boundary. These choices are not immediate alignment or
preflight decisions.

Quick checks include exact factor IDs/counts, held-out exclusion from callbacks,
datum-flat roll-column strength, representation singularity avoidance, raw versus
dimensionless Jacobians, preregistered expected-result hashes, and isolated
single-cause adverse scenarios.

[solver-evidence]: ../../../../experiments/generated-solver-evaluator-v1-evidence.json
[solver-runner]: ../../../../experiments/generated_solver_evaluator_v1.py
