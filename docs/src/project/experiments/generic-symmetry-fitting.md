# Generic symmetry fitting foundation

## Bounded scope

[#272](https://github.com/altendky/scansor/issues/272) adds canonical rotation
and reflection equalities to the shared
[relationship component](../../../../experiments/fit_relationship_component.py).
The internal numerical API is separate from imported graph orchestration,
selected-data joint recovery, provider publication, reuse and CAD integration.
Those remain [#273](https://github.com/altendky/scansor/issues/273); the imported
symmetry dispatch guards stay in place.

The model remains three ordered same-kind members at positive 0/120/240-degree
rotations, or two same-kind members reflected about an axis-containing datum.
Members may be planes, cylinders or cones. Broader repetition counts, automatic
correspondence, additional analytic families and physical acceptance are open
questions. Numerical starting geometry remains disposable, outside saved actions.

## Exact geometry

The complete observation set is centered and length-normalized. Each free axis
has its own local chart, including horizontal members; fixed references and
provider aliases keep their declared roles. In column-vector notation, rotation
about directed line `(C,d)` has Rodrigues matrix `Q` and translation `t=C-Q*C`.
An alias's directed sign determines positive member order. Reflection about the
unit-normal plane `N·X=h` has `Q=I-2*N*Nᵀ` and `t=2*h*N`; reversing both normal
and offset leaves the reflection unchanged. Internal reflection does not relax
the proper-rigid-transform boundary of feature reuse.

For member zero as the base, fixed orientation signs within an attempt give:

- Plane normal `nᵢ=s*Q*n₀` and offset `bᵢ=s*(b₀+(Q*n₀)·t)`. Reversing a normal
  also reverses its offset.
- Lateral direction `dᵢ=s*Q*d₀`. Its anchor `Aᵢ` lies on the transformed line
  through `B=Q*A₀+t`; displacement from `B` has no transverse component.
- Cylinder radii agree. Cone slope `kᵢ=s*k₀` and anchor radius
  `rᵢ=r₀+k₀*((Aᵢ-B)·(Q*d₀))` account for each independent anchor gauge.

These are hard equality rows with analytic derivatives, alongside unit-normal,
axis/datum and supported parallel/radius-distance equations. They are separate
from the weighted observation residuals. Observation-free reference planes add
constraints without invented observations. A free mirror datum can refine its
clocking; fixed providers remain fixed.

## Support and compatibility boundaries

Each member keeps its own support carrier, interval, original observations and
weights. Symmetry constrains infinite geometry without copying finite support
between members. Negative extrapolated cone anchor radii are permitted on the
new path only when finite support and selected projections remain on the positive
sheet. Pure [support mathematics](../../../../experiments/support_intent.py)
supplies those coordinates; adding this internal dependency does not enable
[#264](https://github.com/altendky/scansor/issues/264)'s graph transfer, settings
or source-reuse workflow.

The optional `planes`, `rotations`, `mirrors` and `radius_distances` inputs select
the new path when nonempty. Calls without them retain the existing support
arithmetic, source-Z/point/selection behavior, failure class and diagnostics,
publication fields, normal-equivalence initialization, callback order and solver
settings. The prerequisite stack's anchor rebasing and publication rewrites are
not installed into those existing calls.

New symmetry attempts use normalized projected-gradient tolerance **1e-10**;
the historical component path remains **1e-9**. This is a local numerical control
from the bounded kernel, not a physical tolerance or a comparison-policy change.
No failed iterate is accepted as a fallback; iteration/trial limits and the full
weighted objective retain their meanings.

The component is one constrained attempt. #273 owns bounded joint candidates,
optional hints, unsigned common-axis sign alternatives and complete bindings.
Joint evidence can identify a model whose individual members are rank deficient;
standalone admission is not a prerequisite for the equation kernel. A converged
local attempt does not establish global convergence or physical accuracy.
The CAD `symmetric_extents` switch remains observation-patch pooling, not a
fitting equality or permission to overwrite hard support.

## Verification boundary

The unchanged 53-case
[symmetry oracle](../../../../tests/test_relationship_symmetry.py) constructs
its own Rodrigues/reflection geometry. It covers oblique and horizontal axes,
fixed/free roles, directed aliases, mirror clocking, nonuniform original weights,
noisy observations, unequal support and negative extrapolated cone anchors.
Central finite differences check observation and equality Jacobians, including
off-unit datum perturbations and radius-distance composition. Other cases cover
jointly identifiable collinear plane patches, unidentifiable free mirror
clocking, zero-parameter fixed models, strict JSON, input preservation,
cancellation and evaluation quotas.

Before extraction, two runs of 37 unchanged composition/cylinder tests captured
57 complete calls: 26 legacy and 31 canonical. Inputs, outputs, failures,
control state, array dtype/shape/bytes and exact floating values matched. The
post-extraction run passed the same 37 unchanged tests and matched all 57 records
exactly, with no omitted telemetry. Geometric equivalence alone would not
establish unchanged support records or control semantics.

All 53 unchanged symmetry cases and 31 pure-support cases pass. Together with
the existing composition and cylinder regressions, the focused run passes 191
tests. These are internal numerical/component checks; they do not enable or
claim the separately guarded imported graph workflow.

The full non-mesh Python run passes 2,605 tests and 32 subtests. Configured
type checking reports no errors or warnings; targeted Ruff, formatting,
repository hooks and the browser build also pass. An independent read-only
review found no blocking equation, support or compatibility defects.

The full unchanged static-worker check passes all 20 stages, including actual
packaged workspace, CAD, both recovery modes, numerical workspace, advisory
measurements and final artifact-integrity checks. Existing fixtures and strict
native/worker comparisons remain unchanged. This local check used a dirty
development checkout; it establishes execution and integrity, not release
eligibility or publication.

The [#266](https://github.com/altendky/scansor/issues/266) native/worker diagnostic
contract remains undecided. Existing strict packaged fixtures and comparisons
remain required, and new imported symmetry execution remains #273. This
foundation does not settle that policy or replace packaged execution with unit
evidence.
