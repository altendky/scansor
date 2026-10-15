# Generic symmetry fitting foundation

## Bounded scope

[#272](https://github.com/altendky/scansor/issues/272) adds canonical rotation
and reflection equalities to the shared
[relationship component](../../../../experiments/fit_relationship_component.py).
The extracted numerical foundation is delivered. The
[#273](https://github.com/altendky/scansor/issues/273) draft adds selected-data
recovery, named graph/provider publication, reuse and per-member CAD support.
Fresh packaged execution and final main-based current-head CI remain delivery
gates for that integration.

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
supplies those coordinates. The extracted foundation alone did not enable
[#264](https://github.com/altendky/scansor/issues/264)'s graph transfer or
source-reuse workflow. That support-intent workflow is now delivered separately;
the current integration preserves its authority, provenance and support carriers.

The optional `planes`, `rotations`, `mirrors` and `radius_distances` inputs select
the canonical equation path when nonempty. At extraction, calls without them
retained their existing support arithmetic, source-Z/point/selection behavior,
failure class, diagnostics, publication, initialization, callback order and
solver settings. The merged component preserves the current support-authority
wrapper and typed support failures alongside the exact symmetry equations.

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

At extraction, all 53 unchanged symmetry cases and 31 pure-support cases passed.
Together with
the existing composition and cylinder regressions, the focused run passes 191
tests. These are internal numerical/component checks; they do not enable or
claim the separately guarded imported graph workflow.

The extraction's full non-mesh Python run passed 2,605 tests and 32 subtests.
Configured
type checking reports no errors or warnings; targeted Ruff, formatting,
repository hooks and the browser build also pass. An independent read-only
review found no blocking equation, support or compatibility defects.

The full unchanged static-worker check passes all 20 stages, including actual
packaged workspace, CAD, both recovery modes, numerical workspace, advisory
measurements and final artifact-integrity checks. Existing fixtures and strict
native/worker comparisons remain unchanged. This local check used a dirty
development checkout; it establishes execution and integrity, not release
eligibility or publication.

The approved [#266](https://github.com/altendky/scansor/issues/266)
[semantic parity contract](numerical-runtime-parity.md) protects published
geometry, source/recipe intent, acceptance classifications and finite budgets,
while retaining discarded traces and realized solver counts as diagnostics.
It does not permit differences in selected inside/outside counts or hard caps.

## Selected recovery and graph integration

The draft selected-data adapter derives common-axis, orbit and mirror-phase
proposals from original selected positions and normals. Every original row and
incident-area weight remains in the objective; independently admissible member
fits are not a prerequisite. Positional proposals precede arbitrary null
directions from misleading normals. Unsigned ordered rotations consider both
common-axis signs; authored directed aliases preserve their sign. Signed
radius-distance intent retains both oriented free mirror-plane branches.

Policy `selected-symmetry-group-recovery-v1` permits 16 proposals and eight fresh
constrained attempts, with a separate optional hint. Each attempt shares one
callback control across observations and hard equalities. Stale or malformed
free hints cannot replace fresh recovery. Bindings include accepted source,
selected rows, ordered declarations, support and numerical policy; computed
starts and transformed-carrier roundoff remain outside authored actions.
Competing cones are compared over selected and supported extents, including
their radial profiles beyond short hard intervals.

Named execution prepares member selections and actual hard providers directly,
including placed providers, aliases, prior stages and read exclusions. Requested
closure and implicit-provider planning share that boundary. Raw fit requests
retain independent results and failures; joint execution publishes its named
context and solved mirror datums without overwriting standalone results.

CAD builds each lateral member from its own geometry and finite support.
`symmetric_extents` pools observed display patches, preserving authored caps;
an automatic member receives a temporary pooled display extent without changing
its published fit. Empty hard-support intersections reject export. Plane
patches retain their shared rectangle construction. Internal mirror geometry
does not broaden feature reuse beyond proper rigid transforms.

## Integration evidence and remaining gates

The historical draft passed 53 selected-recovery cases, 26 named graph cases and
66 CAD cases, including six actual graph-to-STEP cases. Those cover oblique and
horizontal poses, fixed/free references, joint-only collinear planes,
misleading normals, unequal supports, signed constraints, hints, cancellation
and budgets. Historical test counts describe their original baseline, not
fresh acceptance of the merged draft.

Six frozen strict-PLY scenes and saved recipes cover plane/cylinder/cone
rotations and mirrors. Their raw positions, normals, triangles, source hashes,
incident weights, weighted residuals, known geometry, finite support and renamed
replay are checked independently. The current integration retains complete
graph operands and shared parity receipts: six added cross-runtime receipts
and six local replay pairs, giving expected totals of 24 receipts and 34 local
pairs per runtime. Existing four-cylinder/four-cone/two-support fixtures remain
unchanged. These are numerical integration probes, not additional UI authoring
or physical-accuracy claims.

The retained historical cylinder-rotation result has worker inside/outside
counts 79/1 versus native 80/0. Its published axis has norm
1.0000000000000002; renormalizing it moves an original endpoint one ULP beyond
its stored upper cap. The draft correction preserves already canonical axes
within four machine epsilons, consistently in Python and JavaScript. It keeps
the existing unit-admission limit and legacy normalization; exact caps and
integer classifications remain unchanged. Frozen endpoint, reversed-axis,
repeated-accessor and immediate-outside-cap regressions guard the correction.
No separate cone classification defect has been established.

Five of the six complete historical symmetry pairs pass the current shared
policy; the cylinder classification discrepancy remains a producer correction,
not discarded telemetry. Earlier strict failures and diagnostic captures remain
historical evidence. Fresh native/worker execution must confirm all six cases,
complete replay inventories, exact classifications and bounded attempts. Full
static artifact verification, independent evidence reconstruction and all
required exact-head CI must pass before delivery. Numerical probes do not
establish global convergence, physical accuracy or site publication.
