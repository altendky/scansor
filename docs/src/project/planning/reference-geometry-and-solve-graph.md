# Reference Geometry and Solve Graph

## Status and scope

**Provisional design direction with a first compatibility-free contract,
2026-09-20.** This page generalizes the implemented browser experiment's
standalone fits, fit-to-fit constraints, and joint solves into explicit reference
geometry, observation factors, and solve requests. The strict internal
`scansor-reference-geometry-model-v1` and solve-request records now implement the
axis/cylinder/perpendicular-plane subset and compile explicit factor-to-quantity
influence. A bounded nozzle-browser adapter now exercises cone/cylinder-and-plane
fits with explicit `axis`, axial `reference_plane`, `mirror_symmetry`, and
`axis_solve` actions, but does not directly consume this contract or yet extend
its declared analytic-element vocabulary beyond a cylinder. It does not establish
a public schema or promise compatibility with the old browser format.

The current browser prototype remains valid bounded evidence: constraint actions
reference earlier fits, and a later joint action refits their observations around
one shared movable axis. The direction here removes that joint container from the
eventual semantic core. A UI may continue to call a simultaneous solve a joint,
but the joint does not own otherwise hidden geometry or relationships.

## Separation of concerns

The generalized model has four distinct kinds of content:

1. **Geometric definitions** identify reference geometry and analytic elements.
2. **Exact dependencies** construct geometry from shared or derived inputs.
3. **Factors and diagnostics** connect observations, priors, soft relationships,
   or diagnostic measurements to geometry.
4. **Solve requests and results** select active factors and free quantities, then
   record an immutable resolved assignment and diagnostics.

These distinctions prevent an observation-backed surface, an exact geometric
relationship, and the act of solving them together from being conflated in one
joint record.

## Reference geometry and analytic elements

Reference geometry has stable identity and typed semantics. The initial design
vocabulary should distinguish at least:

- a point;
- an unoriented or oriented direction, where the distinction is explicit;
- an axis line, geometrically unoriented unless separately paired with an
  oriented direction;
- an oriented plane; and
- a right-handed frame.

An **axis line** is a line, not an implicit coordinate frame. It has no preferred
point along itself, direction sign, or roll about itself. Deriving an oriented
normal or signed axial coordinate from it requires a separate explicit direction
choice. A use case requiring an axial zero, a clocking direction, or both must
declare additional geometry such as a point on the axis or a frame. This avoids
hiding unobservable degrees of freedom in the word `axis`.

Every geometric value belongs to a declared coordinate frame and unit system.
Directly shared geometry must inhabit the same frame. Crossing frames requires an
explicit typed transform with declared direction and provenance; current
placement alone must not imply a transform or relationship.

Analytic elements reference this geometry rather than privately duplicating it.
For example, a cylinder references an axis line and a radius. A plane used as a
fitting target may itself be the reference plane, or a bounded element may wrap
that plane with a separate support domain. Multiple cylinders are exactly
coaxial when they reference the same axis line. A plane perpendicular to an axis
may derive its normal from an explicitly oriented direction parallel to the axis
while retaining an independent offset. Axis orientation is not inferred from the
plane normal.

Exact identity and derivation should be preferred over a generic equality or
large-penalty constraint. Typed construction operations remain necessary where a
relationship cannot be expressed as direct sharing. Their orientation, sign,
degeneracy, and validity rules must be explicit.

## Quantity roles

Freedom belongs to a solve request, not permanently to a geometric identity. One
definition can be fixed in a downstream solve and free in a different solve
without either solve mutating the declaration or the other's result.

Each relevant quantity has one effective role in a solve:

| Role | Meaning |
| --- | --- |
| Fixed | A finite literal or resolved upstream value that the solve cannot change |
| Free | An optimizer variable with explicit initialization, bounds, scale, and units |
| Derived | An exact function of other quantities with no independent degree of freedom |

Fixed and free roles apply to independent quantities; a derived role follows from
an exact definition. The solve request must record or resolve these roles without
ambiguity, and the result records the complete resolved role assignment. Roles
may apply below whole-entity granularity. For example, a plane normal may derive
from an explicitly oriented direction parallel to an axis while its offset is
free. The declaration must describe geometric degrees of freedom rather than
exposing a solver's incidental coordinate chart as product semantics.

Initialization, fixing, and prior evidence are different operations:

- an initial value only starts the solve;
- a fixed value cannot move in that solve; and
- a prior leaves the value free and contributes an explicit weighted factor.

An earlier standalone fit may therefore initialize a later free axis without
silently becoming either its authority or a prior. A model nominal, if retained,
is one possible named initialization source and is not selected implicitly.

## Exact dependencies and influence

An exact dependency is directional for evaluation but introduces no penalty
residual. A derived value changes when its free inputs change. A fixed reference
to an earlier result is read-only within the downstream solve: downstream factors
cannot change the earlier result.

The default feature-graph behavior remains a live forward dependency. Editing or
reevaluating the upstream action invalidates dependent results, which must then
be reevaluated against the new immutable upstream result. A future explicit copy
or pin operation may detach a value from later upstream changes, but ordinary
references must not silently copy or mutate values.

An active factor can influence only free quantities reachable through the exact
dependencies used by its evaluated geometry. Sharing an entity does not imply
that every factor observes all of its degrees of freedom. Rank, gauge, and
coverage diagnostics must report the actual influence.

In particular, observations of a plane perpendicular to an axis can constrain
the shared direction, but an unbounded plane does not locate the axis line in the
plane: every parallel translation of the line remains perpendicular and
intersects the plane. Locating an axial origin requires another declared
relationship, such as a specified point on the axis lying in the plane. Locating
the line transversely requires evidence or relationships that depend on that
transverse location.

## Factors, diagnostics, and activation

Observation bindings attach explicit observation memberships to analytic
elements. They produce residual factors only when instantiated and explicitly
activated by a solve request. Availability, mapping, factor instantiation, and
activation remain separate.

Soft relationships and priors are factors with declared scales or uncertainty.
Diagnostic relationships measure and report without changing a solve. Hard
relationships compile to shared or reduced quantities and exact dependency
evaluation rather than high-weight factors.

A solve request explicitly lists its active factor identities, free quantities,
fixed input references, and applicable exact definitions. It must not
automatically solve an entire connected component merely because references
exist. Explicit activation preserves auditability, supports held-out isolation,
and permits multiple solve requests over the same definitions.

## Operation DAG and factor graph

The backend operation graph and the mathematical factor graph are related views,
not one graph with one edge meaning.

The **operation DAG** records ordered authoring dependencies, evaluation,
invalidation, and provenance. References point to earlier actions. A standalone
fit result may feed later reference geometry without permitting reverse influence.

The **factor graph** for one solve connects active factors to the free quantities
used by their geometry. Several observation sets may influence one shared free
axis. Its connectivity may contain loops that do not create cycles in the
operation DAG.

Each solve emits a separate immutable result. It does not overwrite standalone
fit results, declarations, or results from other solves. Downstream actions
reference the particular result that supplied their resolved input.

## Example workflows

### Sequential fixed-axis workflow

1. Fit a cylinder from one declared observation membership.
2. Derive an axis line from that completed fit result.
3. Choose an oriented direction parallel to the axis, then define a plane whose
   normal derives from that direction and whose offset is free.
4. Solve only the plane observation factor and plane offset.

The axis value is fixed in step 4. Plane observations cannot feed back into the
cylinder result or axis. An upstream cylinder change invalidates the dependent
axis and plane result through the operation DAG.

### Simultaneous shared-axis workflow

1. Declare a free axis line with an explicit initial value.
2. Define a cylinder that references the axis and has a free radius.
3. Choose an oriented direction parallel to the axis, then define a plane whose
   normal derives from that direction and whose offset is free.
4. Activate both observation factors in one solve and declare the axis, radius,
   and offset quantities free.

Both observation sets influence the shared axis components on which their
residuals actually depend. The plane contributes direction evidence but, absent
additional relationships, no transverse axis-location evidence.

### Axial mirror-symmetry workflow

1. Declare an axis and a plane that contains that axis, with an explicit initial
   clocking about it.
2. Fit two distinct observation memberships independently to the same analytic
   surface type.
3. Declare the pair to be exact reflected copies across the reference plane.
4. Activate the mirror relationship with axis-locating and direction-locating
   factors in one solve.

The reference plane owns the symmetry geometry; the relationship identifies the
two members; the solve owns freedom. The standalone fits and reference-plane
declaration remain immutable inputs. The bounded browser implementation supports
plane, cylinder, and cone pairs and refines the plane clocking and shared axis.
It also supports exact plane parallelism and typed equality between a cylinder
radius and plane-to-reference-plane distance. When a mirrored plane pair uses
both relationships, the solver lowers the cluster to two planes at opposite
signed radius offsets. A cylinder and that pair can solve without an unrelated
perpendicular end-plane factor. The browser currently permits one mirrored pair
per reference plane in a solve. These are prototype rank and parameterization
limits, not a settled general contract.

Mirror, parallel, and equality actions define reusable geometry; they are not
solves. A separate solve-group action explicitly activates fits and relationships
and owns the adjusted result. The current compatibility-free contract below does
not yet encode axial-plane clocking or relative parallel-offset planes; extending
that normalized contract coherently remains separate from this browser numerical
slice.

## Implemented compatibility-free contract

The first bounded contract in `scansor.reference_geometry` provides:

- explicit coordinate-frame identities, unoriented axis lines, oriented
  axis-parallel directions, oriented planes, and cylinders;
- independent axis-line, radius, and plane-offset quantities with solve-local
  fixed or free roles and geometric initialization, bounds, and scales;
- self-contained, content-digested resolved upstream values for fixed or initial
  inputs, including frame validation;
- exact source-hash, selection-count, and selection-membership-digest binding for
  observation factors;
- explicit ordered factor activation and exact role coverage; and
- a compiled influence record that distinguishes axis transverse position from
  direction. Plane observations reach direction and offset but not transverse
  axis position; cylinder observations reach both axis components and radius.

The same immutable declaration can therefore compile the two workflows above.
With a fixed upstream axis, the active plane factor has only the plane offset as
a free influence. With a free shared axis, explicitly active cylinder and plane
factors both reach its direction, while only the cylinder factor reaches its
transverse position. Tests bind this model to the salvaged current nozzle
selections without parsing a legacy recipe.

The contract itself is a semantic and dependency compiler: it does not instantiate
source coordinates, evaluate residuals or derivatives, optimize, diagnose numeric
rank, or emit a resolved solve result. The nozzle browser now provides separate,
fixture-specific numerical evidence for the two axis workflows and operation-DAG
invalidation; that adapter is not the generalized contract's execution backend.
Point reference geometry, placed frame values, transforms, partial axis locking,
priors, and generalized numerical preflight remain later work. Structural
reachability must not be mistaken for empirical observability.

## Compatibility and remaining implementation gates

This design is a successor direction, not a reinterpretation of existing
records. The implemented browser `fit`, `constraint`, and `joint_fit` actions and
the fixed-pose declared analytic model retain their documented meanings.

### One-time selection salvage, not backward compatibility

**Data salvage implemented for all discovered saved nozzle selection graphs,
2026-09-20; visual acceptance remains pending.** The isolated exact-version
converter emits strict
`scansor-selection-bundle-v1` records and separate salvage reports:

- the two checked-in recipes produce one two-selection bundle containing their
  identical `outer_band` and `top_face` memberships;
- `nozzle-actions.json` and its recovery copy produce one bundle containing the
  `11` current user-authored selections and `1,364` total memberships; and
- `actions-saved.json` and `rotation-saved.json` produce separate bundles because
  their reused `outer_band` and `top_face` identities conflict with other saved
  memberships. The former materializes its growth output as `3,284` static IDs.

No precedence was silently selected among conflicting snapshots. Their fit,
perpendicular, coaxial, rotational, joint, result, and output semantics are
absent from every bundle. The successor bundle parser rejects an old browser
recipe. Old model/reference binding digests occur only in salvage provenance,
not in the compatibility-free bundle.

The automated gate pins every selection ID, count, and newline-ID hash; verifies
exact source identity and legacy binding provenance; validates strict bundle
replay; and checks deterministic regeneration where the source recipes are
checked in. The documented visual overlay remains a manual gate and is not
complete, so the converted samples are preserved but not yet visually accepted.

For any additional retained user-authored sample, run the same bounded one-time
conversion before replacing the browser recipe contract. Preserve the work that
is expensive to recreate:

- the exact source identity and binding;
- selection IDs and labels;
- resolved canonical source-vertex IDs; and
- applicable selection properties such as depth mode.

If an old selection-producing action such as growth has no directly serialized
membership, evaluate it with the old implementation and materialize its resolved
IDs as an imported selection. Retain a migration note identifying the old action
and recipe, but do not preserve an executable dependency on the old fit graph.

Do not translate old standalone fits, constraints, rotational relationships,
joint solves, cached results, or the output pointer into new geometric semantics.
They may be retained as frozen experiment evidence, but new reference geometry,
elements, factors, and solves must be authored explicitly.

The implementation is an offline, exact-version conversion step, not fallback
parsing in the new runtime. Verify source identity, selection counts and hashes,
and a visual overlay before accepting a converted sample. Once all
identified samples are converted, the conversion code may be retired; the
successor parser has no obligation to read an old browser recipe. An unsaved
in-memory browser session cannot be recovered by this process, so any richer
user-edited action graph must be saved as `nozzle-actions.json` before the old
browser is removed.

Before extending this first contract into a numerical backend, the successor
must additionally specify:

1. reference-geometry identity, orientation, units, coordinate frames,
   transforms, and degeneracy rules;
2. the remaining exact typed constructions and component-binding semantics;
3. prior-factor semantics and any finer solve-local role granularity;
4. immutable result ownership and dependency invalidation;
5. manifold parameterization and derivative conventions without exposing chart
   artifacts as semantic degrees of freedom;
6. rank, gauge, and factor-to-quantity influence diagnostics; and
7. source-coordinate instantiation from the implemented successor selection
   record.

The implemented contract evidence covers the two workflows above structurally,
records that a plane has no transverse-axis influence, binds exact salvaged
selection memberships, reuses one immutable declaration across solve requests,
and rejects rather than interprets an old recipe. Numerical evidence must still
show residual/Jacobian behavior and rank, and operation-DAG evidence must still
show upstream invalidation without reverse influence and immutable solve-result
reuse.
