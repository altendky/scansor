# Selected cylinder admission investigation

**Research finding and bounded implementation:** generated comparisons support
replacing the standalone cylinder's raw extent gate with coverage normalized by
fitted geometry. This concerns numerical admission under the existing weighted
positional objective. It does not establish physical acceptance, uncertainty,
global convergence or arbitrary mesh support.

## Prespecified comparison

The baseline is the selected-cylinder implementation merged in PR #263, main
08ecbe8fcbc666f9cbdaf91d215c589048a14e94. An exact cylinder of radius 2.5, 32
angles and seven axial levels over [-300000,300000] fails its raw covariance
ratio gate (about 7.81248e-11), although the true-axis geometric Jacobian has
condition about 1.4142. The same cylinder over [-30000,30000] passes. Thus raw
aspect ratio is not a sufficient standalone observability test.

Before changing the gate, nine generated families were compared using the same
bounded normal/position proposals, positional least squares and original ramp
weights from 0.2 to 2. Each converged attempt was independently assessed on every
selected row. Expected outcomes were admission of full/partial curved patches
and long cylinders, and rejection of narrow strips, a single circle, two axial
generators, a plane and a perturbed plane. Partial patches span angles [-0.8,1.2].
The plane spans X=[-3,3], Y=[-2,2], with optional Z noise 0.005 sin(1.7i).

| Generated family | Geometry-normalized coverage | Aligned Jacobian condition | Evidence |
| --- | ---: | ---: | --- |
| Full cylinder, axial half-span 3 | 0.489059 | 2.49163 | Correct geometry admitted |
| Partial cylinder, half-span 3 | 0.0222708 | 11.3732 | Correct geometry admitted |
| Full cylinder, half-span 300000 | 0.489059 | 1.44464 | Removes observed false rejection |
| Full cylinder, half-span 0.00003 | 0.489059 | 203441 | Axial sensitivity still assessed separately |
| Angular strip [-1e-7,1e-7] | 0 for apparent local result | 1305.995 | Coverage rejects a spurious small radius |
| Single circle or exact plane | No converged full-rank candidate | — | Rank failure |
| Two generators | 0 for apparent local result | 1.1544e8 | Both gates reject |
| Perturbed plane | About 1.27e-13 | About 1.397e8 | Both gates reject |

These are investigation measurements before initializer/step stabilization;
bounded attempts can subsequently fail earlier with explicit rank diagnostics.
The strip is a counterexample to merely deleting the covariance gate: its
apparent local result passes the condition cap but lacks supported curvature.
Further preregistered checks rotated and translated long/partial patches at
scales 1e-6 and 1e6, replaced normals, screened a poisoned hint, and repeated the
strip/perturbed-plane failures. They preserved the intended outcomes.

Fixed/free/joint controls use their actual component charts. One circle can
identify a radius about an exact fixed axis, but not a free axis. Two circles
at different heights can jointly identify their shared axis and independent
radii (rank 6, measured condition 2.1444). Consequently the standalone gate is
not imposed on individual members of joint or fixed-reference fits.

## Numerical policy and reproducible regressions

The selected policy keeps the 1e-10 ratio and 1e6 aligned-condition limits, but
computes coverage after fitting in radius/axial-RMS coordinates. Weighted
centering removes anchor dependence; common unit changes, rigid pose, unsigned
axis reversal and transverse basis rotation preserve the metric in exact
arithmetic. Covariance computation additionally removes one common maximum
magnitude to avoid overflow; diagnostics retain that resulting spectrum.

Widening admission also exposed initializer and stationarity defects on an
axis-aligned cylinder with half-span 3e15 whose transverse positions remain
resolved. The unbalanced projected circle lost rank and produced radius 1.26973
instead of 2.5, despite passing the proposed coverage/condition checks. Balancing
the same weighted circle equation by its centered transverse RMS restores rank 3
and the correct radius. Per-proposal initializer rank and condition are retained;
deficient starts are screened without suppressing later fresh attempts.
The circle equation's columns are also equilibrated independently for the solve,
then mapped back; original rank/conditioning and equilibrated conditioning are
reported separately. This avoids amplifying different transverse column extents
in long wrong-axis proposals without changing the equation or selected weights.

Each rotated chart limits its two slopes to [-2,2], avoiding the singular
direction at infinite slope. This imposes no global orientation restriction:
the three orthogonal normal-moment directions cover every unsigned axis with
both slopes at most sqrt(2) in at least one chart. Their wider boxes overlap. Final
assessment uses the unconstrained geometric gradient, so stationarity against a
numerical chart boundary alone cannot admit a result. The bound also prevents
roundoff from growing into divergent discarded parameter traces across native
and worker runtimes.

The numerical solve additionally requires scaled singular values above 1e-6
of the largest at every iterate, instead of allowing a poorly resolved trajectory
to continue toward a near-plane infinite-radius limit. This is measured in the
actual attempt's solve scales, separately from the unchanged final aligned
condition cap. It is a conservative numerical limitation: an unsupported chart
fails locally while other fresh charts keep their full budgets. Diagnostics
report both criteria; neither defines a physical fit tolerance.

Each attempt fixes its residual scale to the initial normalized radius r
and parameter scales to [r,r,r/h,r/h,r], where h is the selected
axial RMS in that attempt's rotated length-normalized chart. This changes solve
conditioning, while final candidate comparison retains the common original
weighted positional objective. Actual solve scales and stationarity diagnostics
are reported separately from the final aligned condition. On identical noisy
ordinary/extreme partial patches, this removes premature zero-iteration stopping
and recovers the same physical radius and RMS in two iterations.
The initial investigation capped angular scales at 1; removing that cap balances
the first-order angular sensitivity of short axial spans as well. Numerical chart
boxes bound the resulting steps. Zero or unrepresentable scales fail explicitly.

Admission independently checks the gradient in fitted-radius/selected-axial-RMS
units against 1e-10. This prevents an enormous optional starting radius from
making a one-step, still inaccurate result appear stationary solely through its
initial solve scale, tie a valid fresh result and cause spurious ambiguity.
An executed radius-1e10 hint on a long partial patch exercises that safeguard.

Geometry equivalence measures radius and line differences relative to radius,
and orientation differences over the selected extent. An extremely long scan's
global length must not make materially different radii equivalent. Competing
solutions within the shared objective tie tolerance still fail as ambiguous.
The family policy/binding version changes to selected-cylinder-recovery-v2;
computed starts and diagnostics remain outside saved actions.

Run the reproducible generated regression matrix with:

    uv run pytest tests/test_cylinder_admission.py tests/test_scan_cylinder_fit.py

Tests generate independent truth and preserve all rows and weights. The browser
runtime probe also includes a float32 long-cylinder case and compares native and
actual worker records. Existing graph/component regressions retain the separate
fixed/free/joint contracts. No new dependency, robust loss, trimming, physical
tolerance or confidence interval is introduced.

## Remaining limitations

These finite synthetic cases justify the specific false-rejection correction;
they do not map every noise model, coverage boundary or coordinate precision
failure. The aligned condition cap remains provisional and can reject short or
weakly identified patches. Full-objective ties can conservatively reject extreme
aspect ratios when competing geometry cannot be distinguished at the current
comparison tolerance. Finite source precision cannot recover geometry already
lost in storage. Cone/varying-radius admission needs its own investigation.
