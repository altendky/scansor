# Generic symmetry fitting foundation

## Scope

[#272](https://github.com/altendky/scansor/issues/272) extends the shared
[relationship component](../../../../experiments/fit_relationship_component.py)
with the existing ordered rotational and mirror relationships in canonical
source coordinates. The numerical foundation and
[#273](https://github.com/altendky/scansor/issues/273)'s selected-data recovery,
named graph dispatch and per-member CAD support are implemented in the draft
stack. Final integration verification and current-head CI remain delivery gates.

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
Lateral members are constructed separately from their own final geometry and
finite caps after pooling observations. An automatic member gets a temporary
pooled display extent; its published fit remains unchanged. Empty hard-support
intersections reject export. Plane patches retain their existing shared
rectangle construction. Full lateral faces can choose a different parameter
seam while preserving their world geometry.

The component is a single constrained attempt. #273 owns bounded joint
candidate generation, optional hints, unsigned common-axis sign alternatives,
complete source/recipe/policy bindings and result comparison. Individually
rank-deficient member patches must not be excluded when their joint evidence
can identify the declared model. Neither a converged local attempt nor a best
attempted objective proves global convergence or physical accuracy.

The selected-data adapter serves imported named rotational and mirror solves.
It derives algebraic common-axis, orbit and mirror-phase proposals from the
original selected positions and normals without requiring independently
admissible member fits. Original incident-area weights remain attached to every
row. Main-factor positional residuals prioritize the finite common-axis pool;
arbitrary null directions from misleading normals cannot consume all attempts
before useful positional evidence is considered. Explicit signed radius-distance
intent also retains both oriented free mirror-plane branches.

Policy `selected-symmetry-group-recovery-v1` bounds generation to 16 proposals
and eight fresh constrained attempts, with a separate optional hint. Each
attempt shares one callback control across observations and hard equalities.
Stale or malformed free hints cannot replace or reorder fresh recovery. Saved
hard providers, alias direction, support carriers and intervals remain semantic
inputs; computed starts remain disposable. Bindings include accepted source
identity, selected rows, declarations and numerical policy, rather than the
source filename. Competing cones are compared over both selected and supported
extents, including radial profile differences outside a short hard interval.

Named execution prepares member selections and actual hard providers directly.
It does not require every member's standalone fit to succeed. Both requested
closure and implicit-provider planning use this preparation boundary; raw fit
requests retain their own results and failures. A joint publishes only its named
context, including solved mirror planes. Actual upstream source/point providers
and placement stages remain required inputs.

Its 53 independent strict-PLY tests pass, covering all three member families,
rotations and mirrors, oblique poses, fixed/free references, partial patches,
misleading normals, unequal hard supports, signed constraints, stale hints,
jointly observable collinear patches, ill-conditioned clock rejection,
cancellation and callback limits. Tests independently recompute every weighted
residual and resolved support endpoint. The related 202 existing recovery,
relationship and support regressions also pass; full types report zero errors
or warnings. These direct checks do not substitute for the integration gates.

The additional packaged probe reads six frozen independent strict-PLY scenes
and saved recipes, without importing the test generator. It checks original
positions, normals, triangles and incident weights, full weighted residuals,
known geometry, finite support and renamed-source replay. The existing float64
probe fixture and parity tolerances remain unchanged; complete attempt evidence
is still compared under the open #266 contract.

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

The #272 foundation passed **2,728 tests plus 32 subtests**, using four workers
with CI's mesh-suite isolation, and full types with zero errors or warnings.
The separate 751-case serial mesh suite passed on the support-enabled base;
these symmetry changes do not modify that ingestion path.

The integrated #273 non-mesh set passes **2,892 tests plus 32 subtests**.
The initial sandbox run passed 2,838 tests; its 54 HTTP cases failed because the
sandbox prohibited localhost sockets, then all passed with localhost access.
Full types report zero errors or warnings. This includes 26 named graph cases,
66 new CAD cases (six run the actual graph through STEP), and 19 packaged-probe
source, execution and strict-comparison cases. Independent reviews found no
remaining scoped producer defect.

The original packaged worker gate still stops on the previously recorded
discarded cone-attempt difference in #266. A separately marked diagnostic copy
completed all six symmetry cases in about 106 seconds. Every selected physical
result agrees within the existing 1e-10 comparisons, with the same fresh
candidate selected. This is diagnostic evidence, not a passing parity gate.
Some discarded outcomes differ, including inferior native local solutions that
reach the worker's evaluation limit. The selected rotated cylinder also uses
four constraint evaluations in the worker versus three natively: retraction
branches at the internal 1e-15 target, with final violations about 5.55e-16 and
9.99e-16. Rank, selected geometry and the 1e-11 feasibility certificate agree.
These selected-control and discarded-outcome differences extend the unresolved
[#266](https://github.com/altendky/scansor/issues/266) contract question; no
verifier, tolerance, fixture or attempt budget was
relaxed. Final main-based current-head CI remains required before merge.
