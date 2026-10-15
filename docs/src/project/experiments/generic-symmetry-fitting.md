# Generic symmetry fitting foundation

## Scope

[#272](https://github.com/altendky/scansor/issues/272) extends the shared
[relationship component](../../../../experiments/fit_relationship_component.py)
with the existing ordered rotational and mirror relationships in canonical
source coordinates. The numerical foundation is implemented and verified in the
draft stack. Imported graph dispatch remains guarded until
[#273](https://github.com/altendky/scansor/issues/273)
integrates selected-data recovery, provider publication, reuse and CAD.

This preserves the current bounded relationship model: three ordered same-kind
surfaces at positive 0/120/240-degree rotations, or two same-kind surfaces
reflected about an axis-containing reference plane. Members can be planes,
cylinders or cones. Broader repetition counts, automatic correspondence,
additional analytic families and physical fit acceptance are separate questions.

## Geometry and exact equations

The component already centers and length-normalizes the complete observation
set. Each free axis has its own local chart; a horizontal member needs no
conversion into a source-Z slope chart. Fixed references and source aliases
retain their roles. Numerical starting geometry is disposable and is not added
to saved actions.

In column-vector notation, rotation about the directed line `(C,d)` uses
Rodrigues matrix `Q` at the member's ordered angle and translation `t=C-Q*C`.
An axis alias's directed sign determines the positive rotation order; an
unsigned provider direction cannot silently replace it. Reflection about the
unit-normal plane `N·X=h` uses `Q=I-2*N*Nᵀ` and `t=2*h*N`. Reversing both `N`
and `h` leaves that reflection unchanged. Internal reflection does not change
the proper-rigid-transform boundary of feature reuse.

For base member zero, member equations describe the transformed infinite
surface. Orientation signs are chosen coherently for an attempt and remain
fixed during that solve:

- A plane's normal is `nᵢ=s*Q*n₀`, with offset
  `bᵢ=s*(b₀+(Q*n₀)·t)`. A normal reversal also reverses its offset.
- A lateral member's direction is `dᵢ=s*Q*d₀`. Its axis point `Aᵢ` lies on
  the line through `B=Q*A₀+t`: its displacement from `B` has no transverse
  component. The numerical anchor gauge remains independent for each axis.
- Cylinder radii agree. For cones, `kᵢ=s*k₀` and
  `rᵢ=r₀+k₀*((Aᵢ-B)·(Q*d₀))`. The radius comparison includes the axial
  anchor shift; equal numbers at unrelated anchors are insufficient.

These are hard equality rows with analytic derivatives, alongside existing
unit-normal, axis/datum and supported radius/plane relationships. They are not
additional weighted observation residuals. Observation-free reference planes
contribute constraints without synthetic observations. An active free mirror
plane can refine its clocking while retaining the contains-axis equations;
fixed providers remain fixed.

## Support and recovery boundaries

Each member retains its own authored support carrier, interval and selected
observations. A symmetry relation constrains infinite geometry without copying
one member's finite support into another. Every original observation and weight
remains in the objective. Negative extrapolated cone anchor radii remain
permitted only when declared finite support and selected projections stay on
the positive sheet.

The existing CAD `symmetric_extents` option controls pooling of observed patch
extents. It is not a fitting equality or permission to overwrite hard support.
Its integration, including explicit rejection of incompatible hard support if
necessary, belongs to #273.

The component is a single constrained attempt. #273 owns bounded joint
candidate generation, optional hints, unsigned common-axis sign alternatives,
complete source/recipe/policy bindings and result comparison. Individually
rank-deficient member patches must not be excluded when their joint evidence
can identify the declared model. Neither a converged local attempt nor a best
attempted objective proves global convergence or physical accuracy.

The native/worker comparison contract remains open in
[#266](https://github.com/altendky/scansor/issues/266). This foundation does not
change the verifier or replace full packaged execution with synthetic unit
evidence.

## Executed verification

All 53 independent
[symmetry cases](../../../../tests/test_relationship_symmetry.py) pass. They use
their own Rodrigues/reflection construction rather than production transforms
as geometry oracles. Cases cover horizontal members, oblique poses, fixed/free
axes and mirror clocking, directed axis and plane aliases, original nonuniform
weights, noisy observations, unequal finite support and negative cone anchor
radii. Central finite differences check complete observation and equality
Jacobians, including off-unit datum perturbations and radius-distance
composition.

Three individually collinear plane patches jointly identify their rotated
plane without a standalone fitter. A coaxial cylindrical mirror pair supplies
no clocking evidence: its free mirror datum fails with rank deficiency, while
the fixed-datum case succeeds. Entirely fixed plane models use zero free
parameters and reject inconsistent fixed geometry. Cancellation, evaluation
quotas, preservation of input records and strict-JSON failure diagnostics are also
covered.

Publication regressions supply complete datum records, then verify that refined
plane equations, point/normal vectors, tangent bases, radial guides and clocking
agree. Clocking follows the existing reference-plane convention, which uses the
opposite transverse basis from surface charts. Fixed normalized world equations
are preserved in datum, alias and attached-surface outputs. Incomplete
axis/construction pairs fail before callbacks instead of producing an undefined
display frame.

New symmetry attempts use normalized projected-gradient tolerance **1e-10**;
the historical component default remains **1e-9**. An exact posed free-cylinder
case stopped with RMS about 7.45e-8 at the old setting. The new setting reaches
RMS about 2.03e-10 with gradient about 5.95e-13. Tightening to 1e-12 instead
made a noisy rotational cone stall at a floating-point floor with gradient
about 2.32e-12; 1e-10 retains successful stationarity for both original cases.
No failed iterate is accepted as a fallback, and iteration/trial budgets and
the complete weighted objective retain their existing meanings. This is a
local numerical setting, not a physical error threshold.

The final full non-mesh suite passes **2,728 tests plus 32 subtests**, using four
workers with CI's mesh-suite isolation. Full types pass with zero errors or
warnings. The separate 751-case serial mesh suite passed on the support-enabled
base; this change does not modify that ingestion path. Graph/reuse/CAD symmetry
acceptance and actual packaged symmetry execution remain #273.
