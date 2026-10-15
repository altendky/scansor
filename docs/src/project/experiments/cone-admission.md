# Cone numerical admission investigation

## Status and scope

**Implemented bounded policy, 2026-10-15; actual packaged execution completed,
full parity gate still blocked.**
[#267](https://github.com/altendky/scansor/issues/267) addresses numerical
rejection of long, identifiable cone patches in
[the standalone helper](../../../../experiments/mesh_cone_fit.py), under policy
`selected-cone-recovery-v2`. The independently executed original-helper baseline
below uses `990e845015fe5a91c3bb614fefd8d65b3ee92bdd`; preregistered tests ran on
the later support-enabled baseline before these numerical edits. Native test
results, observed producer correction and remaining runtime gate are recorded
separately below.

This experiment concerns finite-precision conditioning and admission to a
bounded numerical search. It does not establish physical fit acceptance,
measurement uncertainty, a general aspect-ratio guarantee or global convergence.
The [single-scan contract](../planning/single-scan-import.md) retains the selected
observations, authored support and reference geometry as authoritative inputs.

The bounded change preserves the full weighted positional objective, seven fresh
proposals, a separate optional-hint budget, and the existing per-attempt limits
of 100 iterations, 16 line-search trials and 512 callbacks. Cancellation,
positive-sheet checks, support semantics and fixed/group constraints remain in
force. No trimming, new loss function, optimizer replacement or dependency is
part of this investigation. The provisional final aligned-Jacobian condition cap
remains **1e6**, measured in the original independent fitted-axis chart; the
provisional positional-coverage threshold remains **1e-10**. The trial solver's
scaled rank cutoff is **1e-6**, a separate conservative chart guard. These
numerical limits do not estimate physical error or statistical confidence.

## Preregistered independent matrix

Generate positions as `p = A + h*d + (R + k*h)*u(theta)`, with
`A = [80, -60, 40]`, axis `d = Z`, unit radial vector `u`, and ideal supplied
normal `(u - k*d)/hypot(1, k)`. Positive ramp weights run from 0.2 to 2 in
height-major order. Full coverage uses 32 angles; partial coverage uses 24 angles
over `[-1.4, 1.6]`. The long case uses seven heights from -600000 to +600000:
600000 is the half-span. Noise, where specified, adds radial displacement
`0.005*sin(1.7*i)`. All generated observations and positive weights participate in
the helper's actual objective. Known generating geometry is used independently
to assess diagnostics, rather than supplied as an initialization hint.

| Case | Required distinction |
| --- | --- |
| Ordinary full/partial cone, R=3, k=0.15, seven heights from -3 to 3 | Preserve ordinary and noisy-patch behavior. |
| Long full/partial near-cylinder cone, R=3, k=1e-7, height half-span 600000 | Expose the existing raw-covariance rejection despite healthy cone sensitivity. |
| Varying-radius cone, R=3, k=0.8, heights from -3 to 3 | Positive radii range from 0.6 to 5.4; one anchor radius cannot stand for the whole patch. |
| Full steep/short frusta, R=3, k=10 or 100, height half-span 0.003 | Preserve identifiable cone tilt sensitivity when taper dominates axial height. |
| Apex-adjacent partial cone, R=0.3, k=0.15, heights [-1.99, -1.9, -1.5, -0.5, 1] | Preserve the positive finite sheet down to radius 0.0015. |
| The same partial cone rebased to an extrapolated negative-radius anchor | Coverage and physical geometry must be invariant under line-anchor gauge changes. |
| k=0 and k=1e-7 | Retain a declared six-variable cone, including its cylinder limit. |
| Single ring, two generators, angular strip of width 2e-7, exact/noisy planes | Preserve explicit failures; low residual or a full-rank Jacobian at an arbitrary bad geometry is insufficient. |
| Long full/partial cases at units 1e-6 and 1e6 under a known oblique rigid pose | Separate numerical policy from raw world-unit tolerances. |
| Extreme noisy partial near-cylinder cone, height magnitude 3e15, k=1e-16 | Independently check final stationarity at transverse rather than whole-cloud scale. |
| A nearly parallel hard support plane and a tiny transverse line-shift hint | Preserve materially different resolved endpoints as ambiguity even when the hint is stationary. |

Fixed axes and joint patches require their own active tangent-rank evidence. A
fixed line leaves radius/taper free; it does not leave six free cone variables.
An individually insufficient patch can contribute to an identifiable combined
problem. This standalone experiment does not impose its gate on every group
member.

## Executed original-helper evidence

The independent [admission matrix](../../../../tests/test_cone_admission.py)
was run before numerical edits against the support-enabled baseline
`dbb2b9510127d408d09c97da9e51a8f0b4c03d84`: **12 passed, 17 failed**. The
17 failures were the eight long pose/unit cases, two noisy-long cases, six
hint-recovery cases and the callback-budget case, which never reached recovery
under the old raw early gate. Ordinary, adverse-family and cancellation controls
passed. Two steep/short controls were then added and executed before numerical
edits: k=10 returned `ambiguous`, while k=100 passed exact geometric assertions.
The complete pre-edit baseline was therefore **13 passed, 18 failed across
31 cases**. A 32nd independent support-conditioning regression was added during
review, as described below. Baseline failures record the behavior before the
bounded improvement; all 32 current matrix cases now pass.

Reproduce the matrix from a development environment with:

```console
uv run --locked pytest -q --tb=short tests/test_cone_admission.py
```

The separate read-only measurements below preceded those tests and used the
original cone helper at `990e845015fe5a91c3bb614fefd8d65b3ee92bdd`.
The baseline bound point, weight and normal bytes and ordered row IDs to genuine
SHA initialization bindings. Independent spectra use the same normalized
positive weights. The table's geometric-Jacobian conditions were measured at the
known generating geometry, with lengths normalized by the existing selected-data
coordinate frame. They are evidence about that fixture, not a substitute for
assessing the helper's actual fitted geometry.

| Fixture | Raw covariance ratio | Proposed affine ratio | Independent aligned-Jacobian condition | Actual original helper |
| --- | ---: | ---: | ---: | --- |
| Ordinary full R=3, k=0.15 | 0.616796 | 0.490284 | 2.92316 | Success, RMS 3.21e-15 |
| Ordinary partial with radial noise 0.005 | 0.184217 | 0.0921168 | 8.07524 at generating geometry | Success, 2 iterations, RMS 0.00350238 |
| Long full R=3, k=1e-7 | 3.57957e-11 | 0.489239 | 1.45515 | Insufficient coverage before attempts |
| Varying radius, k=0.8 | 0.383166 | 0.492823 | 2.83094 | Success, RMS 3.09e-15 |
| Positive apex-adjacent partial patch | 0.00548008 | 0.0681751 | 4.83648 | Success, RMS 3.36e-15 |
| Full k=0, declared cone | 0.678651 | 0.489059 | 2.84467 | Success, RMS 2.91e-15 |

The long full/partial patches also failed the raw gate after an independent
proper row rotation, translation `[20, 30, -10]`, and unit scales 1e-6 and 1e6.
Their proposed affine ratios stayed about 0.489239 and 0.0926007, and independent
conditions about 1.45515 and 4.76530. At the larger units, source-coordinate
subtraction produced world RMS around 1.3e-5 to 1.6e-5, or 1.3e-11 to 1.6e-11
per unit scale. This is roundoff evidence, not an admissible physical-error
threshold.

The original helper rejected the ring, strip, two generators and exact plane as
insufficient coverage. A perturbed plane exhausted search despite a raw ratio
about 4.02e-6. Candidate affine ratios for the strip and two generators stayed
below 1e-18. A ring has zero axial RMS and must fail before dividing by it.

## Global affine coverage

For a candidate cone, let `h` be axial position and `rho` the observed distance
from its fitted line. The orthogonal projected sheet radius is
`r_proj = (r + k*h + k*k*rho)/(1 + k*k)`. The helper evaluates this
with scaled arithmetic and rejects nonfinite or nonpositive projected radii.
This radius is invariant when the line anchor is rebased, including a negative
extrapolated anchor radius with a positive finite patch.

Define one global weighted RMS projected radius `Rrms` and one weighted axial
RMS `Hrms` about the selected centroid. With finite positive scales, the map
`(x/Rrms, y/Rrms, (h-hmean)/Hrms)` is affine in the original positions. It
preserves exact positional rank. Policy v2 requires this post-fit covariance's
smallest/largest eigenvalue ratio to exceed 1e-10. The raw ratio remains labeled
evidence and no longer rejects a patch before attempts solely because of its
aspect ratio. Neither scale changes individual row weights, residuals or the
objective being minimized.

Dividing each row's radial coordinates by its own radius is nonlinear and does
not preserve rank. An independently generated exact plane `A + [x, 1, h]` has
raw and global-affine covariance ratios zero; per-row radial unit normalization
instead produced ratio **0.0341985**. An arbitrary cone evaluated on those same
rows even had a full six-column Jacobian with condition about 10.17, but RMS
about 1.68 and gradient about 0.746. Those diagnostics describe a bad,
nonstationary geometry. They do not demonstrate a valid cone optimum.

For the ordinary partial cone, projected-radius RMS was about 3.15224, axial
RMS about 1.76498, and affine ratio about 0.0921164. Rebasing the same cone to
`A-30*d`, where anchor radius becomes -1.5, left those values unchanged. The
apex fixture's projected-radius RMS was about 0.286101 despite a minimum radius
of 0.0015: aggregate scaling does not certify precision near the apex.

## Stopping, sensitivity and competing solutions

Removing the raw early gate alone is insufficient. An independently executed
single-good-chart reconstruction used the existing cone initializer and analytic
residual/Jacobian, without changing the helper or its verifier. It was a bounded
prospective calculation, rather than full recovery or worker-parity evidence.

For an extreme noisy partial cone with height magnitude 3e15 and k=1e-16, the
original isotropic solve stopped at zero iterations with RMS 0.00354172345 and
independent radius-relative gradient about 3.07e-6. A radius/axial-scaled solve
of the same weighted objective refined in two iterations to RMS 0.00354156391
and gradient about 5.98e-14. The reference radius moved from about 3.00007863
to 3.00004976. A small whole-cloud normalized gradient therefore cannot certify
transverse stationarity in this newly investigated range.

Tilt depends on `h*u + k*xy`, not just axial extent. Independent steep/short
checks found that copying the cylinder's R/H tilt scale would produce scaled
Jacobian conditions about 2.95e6 for k=10 and 3.16e6 for k=100, although their
original aligned conditions are about 12273 and 125686, both below the unchanged
1e6 cap. Cone-specific tilt scaling reduces those scaled conditions to about
354 and 36.8. The cone equation determines the scale; the cylinder limit alone
does not validate it for steep frusta.

Policy v2 evaluates scales in the line gauge closest to the fixed weighted
selected centroid. Let `s=hypot(1,k)`, `R=Rrms`, `H=Hrms`,
`Ts=weightedRMS(h/s+(k/s)*rho)` and `E=R/s`. The six parameter scales, in order
of transverse center, tilt, radius and taper, are
`[R, R, E/Ts, E/Ts, R, R/H]`; the residual scale is E. Lengths here use the
same normalized coordinate chart as the solver. Max-scaled RMS arithmetic and
finite positive scale checks avoid silent overflow or division by collapsed
extents. Unrepresentable scales fail explicitly rather than being clamped.

E matters independently of tilt scaling. On the k=100 short frustum, a radius
offset 1e-6 exceeds the numerical radius-equivalence tolerance. Using R as the
residual scale nevertheless gives a gradient about 3.23e-11, below the stopping
threshold. Using E exposes a gradient about 3.23e-7. Each attempt keeps its
scales fixed while minimizing the same complete objective up to one constant.
Final assessment recomputes scales from fitted geometry and requires the full
unprojected six-variable gradient infinity norm to be at most 1e-10. It does
not inherit the hint's initial radius or mistake a box-projected gradient for
free-axis stationarity. Common candidate scores remain the original weighted
squared residual objective, without candidate-specific radius normalization.

The solver bounds each chart's two slopes to [-2, 2]. Three orthonormal proposals
provide an unsigned chart with both slopes at most sqrt(2), strictly inside
those overlapping bounds. This establishes representation coverage, rather than
initializer success or global recovery. Final positive-sheet/support validity,
affine coverage, independent aligned condition and stationarity gates all remain
necessary.

Equivalence aligns sign and line gauge, then compares transverse line position,
radius, axis and taper effects across the selected and resolved-support extents.
The axis chord is multiplied by a conservative maximum meridian norm
`hypot(z, r+k*z)` over a symmetric interval covering both extents; taper
differences are multiplied by that axial extent. These differences, and actual
resolved endpoint positions and radii, must each fit within 1e-7 times the smaller
fitted projected-radius RMS. Nonfinite comparisons remain inequivalent.

The endpoint check catches a real counterexample missed by line/axis checks.
For full zero-taper R=3 observations at source origin zero, support plane normal
`normalize([1,0,1e-7])`, offset zero and interval [8,15], a hint line shifted in
X by only 3e-11 is stationary below 1e-10, but its plane-resolved endpoints move
by 3e-4. The full pipeline returns typed `ambiguous` while preserving every fresh
attempt record. A tiny line shift is not equivalent hard support after a nearly
parallel plane intersection.

Global affine coverage, six-variable sensitivity, final stationarity and bounded
geometry equivalence answer different questions. Passing these numerical tests
still does not establish physical accuracy or global convergence.

## Observed projection correction

Actual packaged execution exposed a producer-side support classification error
on the new long source: the worker classified 49 observations inside and 11
outside, while native classified all 60 inside. Independent 80-digit Decimal
orthogonal projections of both emitted geometries and all 60 original
float32 source positions found all 60 inside. The earlier expression could lose
an axial ULP by dividing a large height twice; this changed a boundary decision
despite otherwise agreeing fitted geometry.

The shared
[projected axial parameter helper](../../../../experiments/surface_primitives.py)
now evaluates the meridian foot as `z + (k/s)*residual` for `abs(k)<=1`, where
`s=hypot(1,k)` and the residual is the signed orthogonal cone distance. For
steeper cones it retains the scaled rational expression, avoiding cancellation
between large axial terms. Cone fitting, surface-distance/growth and the two
selection-region projection sites use this common arithmetic. The correction
changes no interval, tolerance, observation weight or source fixture.

The independent
[projection regressions](../../../../tests/test_cone_projection.py) freeze the
actual pre-correction native/worker geometry constants and regenerate the
admitted float32 positions without importing the runtime probe generator.
Thirteen cases check the observed row and reversed-axis foot against Decimal,
both complete support classifications, branch boundaries, negative anchor radius
and steep slopes through +/-1e160. The corrected actual artifact classifies
60 inside and zero outside in both runtimes.

## Verification and remaining gates

All **32 independent admission tests pass**, including the original long cases,
noisy extreme stationarity, steep/short frusta, adverse families, hint isolation,
callback bounds, cancellation and hard-support endpoint ambiguity. The admission
test file passes Ruff and basedpyright. Together with the 13 projection cases,
the independent new tests report **45 passed**. The full suite passed **2661
tests and 32 subtests before the final projection correction**. After that
correction, **561 related family, support, region, growth, runtime and component
cases pass**. Full type checking passed before the arithmetic correction;
subsequent targeted checks of the four changed producer files and new tests are
also clean. This records the scope of verification without claiming a complete
post-correction CI run.

The native runtime test file passes **218 tests**. Its frozen cone matrix now
adds a fourth raw-float32 long case with R=2.5, k=1e-7, heights from -3e6 to +3e6
and source origin `[80,-60,40]`; the original three cases remain unchanged.
This adds actual source admission, fitting, growth, reference-axis and replay
coverage without making float64 generation interchangeable with source-float32
numerics.

Actual packaged Chromium/Pyodide execution now exercises four cone cases and two
support-transfer cases. Selected initializer and selected-attempt records agree
under the current strict comparison limits, as do physical geometry, hard
support, IDs, bindings, growth and recipe replay. **The complete strict verifier
still fails**; execution and selected-result agreement are not full parity.

A separately marked post-hoc diagnostic capture, retaining the original strict
failure artifact, reports 264 raw difference entries: 256 discarded-attempt trace
fields, six repeated entries for two discarded wrong-chart algebraic
`center_condition` values differing by about 1e-6 relatively, and two previously
observed registration ratios. Selected-attempt and selected-initializer evidence
does not exhibit those differences. This classification describes the observed
artifact; it neither removes fields from comparison nor establishes acceptable
new tolerances. Inactive initializer diagnostics are therefore additional scope
for the unresolved backend contract, alongside discarded solve traces and
registration diagnostics.

[#266](https://github.com/altendky/scansor/issues/266) still governs the unresolved
backend recovery-trace contract. This experiment does not authorize wider parity
tolerances, omitted failed attempts, changed fixtures to evade a failure or
increased search budgets. The actual strict failure remains evidence requiring
that decision; selected-result agreement does not close the parity gate.
