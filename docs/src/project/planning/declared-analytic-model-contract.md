# Declared Analytic Model Contract

## Status

**Provisional internal semantic design, snapshot dated 2026-09-08.** This page
defines the application-owned meaning required for the declared analytic-model
sequence. It is documentation-first design, not an implemented capability, a
public schema, a durable `model.json` format, or a compatibility promise.

The initial declaration vocabulary covers oriented planes and cylinders sharing
one declared axis, each with an explicit bounded support domain. The vocabulary
is intentionally extensible, but no other primitive, arbitrary topology,
physical observation, CAD import, or product-support claim follows from that
shape.

## Boundary and Separations

A declaration records user-confirmed fitting intent. It does not discover that
intent from observations, element names, current CAD placement, or extracted
geometry. The initial workflow therefore requires all of the following as input:

- declared topology, elements, primitives, parameters, relationships, and policy
- a known observation-to-model rigid pose
- explicit observation correspondence produced by a separate mapping stage
- a project-generated synthetic cloud whose exact provenance can be replayed and
  verified before admission

Only declared scalar shape and dimension parameters vary. Recognition,
segmentation, correspondence inference, pose discovery, and joint pose-and-shape
fitting are absent. Authoring, declaration validation, primitive evaluation,
mapping, factor construction, active-factor selection, preflight, execution,
replay, held-out assessment, future acceptance, and publication remain distinct
concerns.

## Terms

| Term | Meaning in this contract |
| --- | --- |
| Declared analytic model | One immutable semantic declaration of a model frame, supported elements, ordered scalar parameters, exact relationships, validity predicates, and model-owned support, coverage, and rank policy. |
| Topology | The declared inventory and ordering of elements and their references to shared primitives, parameters, relationships, and bounded domains. It is input, not inferred adjacency or a general B-rep. |
| Element | A stable identified fitting target with one primitive, one bounded support domain, declared orientation, and explicit parameter relationships. |
| Primitive | An unbounded analytic support equation and projection rule. Initially this is an oriented plane or a cylinder coaxial with the model axis. |
| Bounded support domain | A closed conjunction of declared predicates evaluated at a primitive projection. It limits where that primitive can receive a mapping; it does not clamp a point to a boundary. |
| Parameter | One identified ordered scalar degree of freedom with a nominal value, inclusive bounds, positive diagnostic scale, unit, and ownership. |
| Relationship | A declared exact dependency or predicate. A hard relationship is evaluated structurally and creates no residual penalty. |
| Structural validity | Finite, bound, dependency-domain, and topology-preservation predicates that must hold before residual evaluation. |
| Residual penalty | A separately declared factor contribution. It is not a substitute for a hard relationship or structural-validity predicate. |

## Declaration Content

The strict authoring record must reject unknown fields, duplicate identifiers,
duplicate map keys, nonfinite numbers, invalid references, and omitted required
values. It contains the following semantic content without prescribing a public
wire format:

1. an internal declaration-contract revision and explicit provisional status
2. one model-frame declaration and coordinate unit
3. one ordered parameter sequence
4. one ordered exact-relationship sequence
5. one ordered element sequence, including each primitive and bounded domain
6. model-owned required-support and coverage policy
7. model-owned relative-rank policy
8. an ordered structural-validity predicate sequence
9. the fixed-pose-shape problem declaration
10. the project-generated synthetic-observation admission policy

Human descriptions, display labels, source-platform bindings, file paths,
timestamps, author names, and signatures are not fitting semantics. If retained,
they belong in records outside the model identity. Element and parameter IDs are
semantic even when a UI also supplies display labels.

### IDs and ordering

Element and parameter IDs are non-empty canonical ASCII tokens. They are unique
within one model, opaque to evaluators, and never parsed for type, geometry,
parameter position, or ownership. Renaming an ID changes semantic content.

Array order is semantic. Parameters are evaluated and reported in declared
parameter order. Elements are evaluated in declared element order; that same
order governs deterministic diagnostics and factor ordering before an explicit
factor selection. Relationships and validity predicates are evaluated in their
declared order so multiple failures have deterministic reporting. An
implementation may use lookup maps, but map iteration cannot replace declared
order.

Every reference uses an ID and must resolve exactly once. Element-to-parameter
and domain-to-relationship dependencies are explicit; implicit array positions
and element-name conventions are invalid authoring mechanisms.

## Frame, Pose, and Units

The model frame is a declared finite right-handed Cartesian frame. Its origin,
oriented unit `+Z` model axis, and a perpendicular oriented unit `+X` reference
fix `+Y = +Z cross +X`. Unit-vector and orthogonality checks use an explicit
declaration-validation tolerance. The initial coordinate and scalar-length unit
is the metre; each parameter nevertheless declares its own unit so a later
extension cannot infer units from position or name.

Observation pose is known input to mapping and is not model content:

```text
p_model_m = R_observation_to_model * p_observation_m
            + t_observation_to_model_m
```

`R` is finite, orthonormal, proper, and has determinant `+1` within the mapping
contract's explicit tolerance. Translation is finite and measured in metres.
Scale is exactly one. Mapping applies this transform before primitive and domain
evaluation. Shape evaluation keeps transformed observations and pose fixed and
varies only the declaration's parameters. A missing, inverted, inferred,
variable, or non-rigid transform fails outside model evaluation.

## Scalar Parameters and Exact Relationships

Each parameter declares:

- a stable parameter ID and its unique index in declared order
- ownership by this model
- unit
- finite nominal, lower-bound, upper-bound, and positive diagnostic-scale values
- whether the parameter is varied by the fixed-pose-shape problem

Bounds are inclusive and require `lower <= nominal <= upper`. Initial parameters
and every trial or result vector use the exact declared order, dimension, units,
and bounds. Diagnostic scales nondimensionalize Jacobian columns and do not
select optimizer steps, priors, tolerances, or acceptance thresholds.

The initial relationship vocabulary is deliberately smaller than a general
expression or constraint language:

- **shared parameter:** multiple primitive or domain slots refer to one parameter
  ID
- **oriented offset:** a slot is a declared finite constant plus a declared
  signed multiple of one parameter
- **right-triangle leg:** a positive derived length is
  `sqrt(hypotenuse^2 - other_leg^2)` from two declared scalar references
- **ordered separation:** `right - left` is strictly greater than a declared
  finite minimum

A scalar reference is either a finite literal, a parameter ID, or the ID of one
earlier declared relationship. References cannot form cycles. The
right-triangle relationship is defined only when its radicand is strictly
positive and its result satisfies any declared minimum. Undefined dependencies
make the model structurally invalid; they do not create `NaN`, clipping, or a
penalty. Additional relationship kinds require a later contract revision and do
not become supported merely because the record shape can carry a tagged kind.

## Initial Primitive Semantics

All calculations below occur in the model frame and metres. Inputs, scalar
references, projections, distances, and gradients must be finite. A primitive's
orientation determines residual sign but does not decide whether a factor is
active.

### Oriented plane

An oriented plane declares a finite unit normal `n` and an oriented-offset scalar
`h`. Its unbounded support and signed distance are:

```text
n dot p = h
d_plane(p) = n dot p - h
project_plane(p) = p - d_plane(p) * n
```

Positive distance lies in the direction of `n`, which is the declared outward
normal. The gradient is `n`. Reversing both `n` and `h` preserves the un-oriented
surface but changes semantic orientation and residual sign, so it changes model
identity. A non-unit, nonfinite, or zero normal is invalid; normalization is not
implicit.

### Coaxial cylinder

A coaxial cylinder uses the model's declared axis line through origin `o` with
unit direction `a = +Z`, declares a strictly positive radius `r`, and declares
radial orientation `s` as exactly `+1` (away from the axis) or `-1` (toward the
axis). For `v = p - o`:

```text
z_axis = a dot v
v_radial = v - z_axis * a
rho = norm(v_radial)
d_cylinder(p) = s * (rho - r)
project_cylinder(p) = o + z_axis * a + r * v_radial / rho
```

All initial cylinders share the exact declared axis; a separate displaced or
tilted cylinder is not coaxial and is unsupported by this revision. Projection
and gradient are undefined when `rho = 0`. Mapping may report that no bounded
candidate exists there, but factor evaluation cannot invent a radial direction.
The gradient, where defined, is `s * v_radial / rho`.

## Bounded Support Domains

Primitive support and bounded-domain membership are separate. First project the
point onto the unbounded primitive, then evaluate every domain predicate at that
projection using the parameter vector selected by the calling stage. Mapping and
held-out assignment use nominal declaration values only. A projection is inside
exactly when all predicates are true. Bounds are inclusive; equality is inside
the mathematical domain. Mapping's separate positive transition guard may
exclude boundary-near observations from candidacy. There is no edge clamp,
nearest-boundary fallback, or implicit extension.

This revision supports closed conjunctions of these predicate kinds:

- **axial interval:** `lower <= a dot (q - o) <= upper`
- **radial interval:** `lower <= norm(v_radial(q)) <= upper`
- **directed interval:** `lower <= u dot (q - anchor) <= upper` for a declared
  finite unit direction `u`
- **oriented half-space trim:** `n dot q <= h` or `n dot q >= h`

Every endpoint, anchor coordinate, or offset is an explicit scalar reference.
Lower endpoints must not exceed upper endpoints. A radial lower endpoint is
nonnegative. Predicate direction and inclusivity are explicit. Cylinder axial
limits, annular plane limits, longitudinal-plane extents, and the stepped
model's `x <= datum_x` trims are therefore declaration content rather than
element-name behavior. The conjunction must bound the support surface; an empty
or mathematically unbounded support domain is invalid in this revision.

A primitive projection that is undefined makes that element ineligible for
domain membership. An undefined scalar relationship or malformed domain makes
the model structurally invalid before mapping. Domain predicates classify
support only; they are not residuals and do not modify the primitive distance.
After mapping, trial or result parameters never remap, reclassify, clamp, change
support identity, or filter factor rows. Factor and preflight stages may evaluate
the already mapped primitive at a structurally valid parameter vector, but that
evaluation is distinct from nominal bounded-domain assignment.

## Structural Validity

Universal structural validity requires finite declarations and vectors, exact
dimension and order, parameter values inside inclusive bounds, positive
cylinder radii, valid acyclic relationships, and defined primitive/domain
evaluation. The model also owns an ordered list of typed predicates from the
bounded initial relationship vocabulary, including strict positivity and ordered
minimum separation.

For the stepped model, these predicates declare ordered axial stations, minimum
band widths, radius separations, `0 < datum_x < middle_radius`, and a positive
right-triangle trim half-width. Another topology declares its own predicates;
preflight must not infer stepped-model rules from element counts or IDs.

Structural validity is a Boolean eligibility precondition with ordered
diagnostics. It does not add residual rows, weights, losses, barriers, priors, or
large penalties. Bounds, invalid geometry, undefined primitive evaluation,
missing support, inadequate coverage, and rank deficiency remain distinguishable
conditions.

## Required Support, Coverage, and Relative Rank

These policies belong to the model declaration and use training observations
only.

**Required support** is an ordered sequence of element IDs and positive minimum
active-factor counts. Every listed element must resolve. Presence of an observation,
candidate, membership, or mapping does not activate a factor.

**Coverage** is an ordered sequence of identified cells. Each cell names one
element, a bounded conjunction of the same declared domain-predicate kinds, and
a positive minimum active-factor count. Coverage reports observed counts,
missing cell IDs, and a disposition independently of rank. The initial policy
must not derive cells or minima from element names or from held-out data.

**Relative rank** declares the exact parameter-ID subsequence, corresponding
positive parameter scales, positive residual scale, relative threshold `tau`,
and required rank. Preflight forms the active training-factor Jacobian in factor
order and declared rank-parameter order, then nondimensionalizes it with the
declared scales. If its singular values are descending
`sigma_0, sigma_1, ...`, rank is the number strictly greater than
`sigma_0 * tau`; an empty or all-zero matrix has rank zero. The required rank
must be between zero and the number of declared rank parameters. Expected gauge
directions, if a later problem declares any, require an explicit separate policy
rather than lowering rank implicitly.

For physical Jacobian `J`, parameter-scale diagonal `S_p`, and residual scale
`s_r`, the dimensionless matrix is `J * S_p / s_r`. Neither scaling nor singular
value computation may reorder rows or columns.

Coverage minima and rank policy are diagnostic/preflight policy, not mapping
thresholds, solver convergence criteria, fit-quality acceptance, or evidence of
physical accuracy.

## Canonical Content and Model Identity

Model identity addresses semantic content, not a filename or mutable authoring
object. The identity input includes every item under
[Declaration Content](#declaration-content), including IDs and all declared
orders, literals, units, orientations, relationships, domains, and policy. It
excludes only the model-identity field itself and nonsemantic records explicitly
listed there.

The provisional internal canonicalization is the repository's strict canonical
ASCII JSON convention: no duplicate keys or nonfinite tokens, sorted object
keys, declared array order preserved, canonical defaults present, and one final
newline. The model ID is `model.` followed by the lowercase hexadecimal SHA-256
of those exact canonical semantic bytes. Parsing must reconstruct and validate
the complete strict declaration and reproduce byte-for-byte canonical content
before accepting the ID.

Equal semantic declarations therefore have equal IDs; changing any semantic
field creates a different ID. The digest detects content changes. It does not
authenticate authorship, authorization, generation, a source platform, or a
historical event.

Every model-dependent successor must bind the exact model ID and enough
canonical content or an immutable content reference to revalidate it:

- mapping requests/results and successor mapping-run manifests
- factor contracts, declarations, instantiated factor sets, and selections
- preflight inputs and diagnostics
- execution requests, invocations, traces, results, and held-out assessments
- filesystem manifests, read-only replay, and verification records

Replay must fail on a missing, stale, mismatched, or unverifiable model binding.
Matching element IDs under a different model ID is insufficient. Model-independent
source observations may remain separate, but any artifact whose meaning depends
on model geometry, ordering, relationships, domains, or policy is model-bound.

## Held-Out Isolation and Synthetic Admission

Only exact project-generated synthetic clouds admitted by a generation contract
and successful read-only verification enter the initial declaration-driven
workflow. A user-provided `synthetic` label, generic PLY file, matching dimensions,
or geometric resemblance is insufficient.

Held-out row identities are committed before mapping and removed before candidate
construction. Held-out coordinates, attributes, memberships, and results cannot
influence model authoring, model identity, mapping, activation, parameters,
bounds, scales, relationships, structural predicates, thresholds, required
support, coverage, rank policy, fitting, or tuning. A separately invoked
post-fit assessment may evaluate them against the already sealed model and result
without changing either.

## Non-Normative Topology Examples

These examples demonstrate expressiveness on paper. They are not serialized
fixtures, implementation claims, public schema examples, defaults, or validation
evidence.

### Existing asymmetric stepped model

The existing model declares seven parameters in order: three band radii, three
nonzero axial stations, and one datum-plane offset. Its ordered elements are
three outward coaxial cylinders, four oriented axial planes, and one `+X`
oriented datum plane. Cylinder domains use the successive closed axial station
intervals. Axial-plane domains use explicit closed radial intervals. The middle
cylinder and its adjacent transition planes use the explicit
`x <= datum_x` trim. The datum plane uses a closed axial interval and the directed
`Y` interval whose half-width is the declared right-triangle leg from middle
radius and datum offset.

Station zero is a literal, not an implicit parameter. Each radius and station
slot names its parameter relationship explicitly. Required support names all
eight elements. Coverage cells and shape rank `7/7` are declaration-owned.
Structural predicates reproduce the current ordered stations, minimum band
widths, radius steps, datum-offset interval, and positive trim width without
parsing IDs or relying on fixed parameter positions. Omitting the datum plane and
its parameter, trims, policy entries, and predicates produces the distinct
six-parameter axisymmetric model and a distinct model ID.

### Coaxial tube model

A materially different tube topology declares inner radius, outer radius, and
length parameters. Its ordered elements are one outward outer cylinder, one
inward inner cylinder, a `-Z` oriented annular plane at zero, and a `+Z` oriented
annular plane at length. Both cylinders use the closed axial interval from zero
to length. Both planes use the closed radial interval from inner to outer radius.
The structural policy requires positive inner radius, outer radius greater than
inner radius by a declared minimum wall thickness, and length greater than a
declared minimum.

Required support and coverage refer to those four IDs, and shape rank policy uses
the declared three-parameter order. This topology has a bore, two cylindrical
orientations, four elements, and no datum trim or stepped stations. The same
primitive, domain, relationship, validity, coverage, and rank semantics describe
it without claiming that mapping, factors, execution, or generation for it are
implemented.

## Migration Boundary

The existing `stepped-rotational-v0` mapping, factor, execution, generation, and
CLI formats are experimental fixed-topology records. Model-bound successors may
replace them without reading, writing, or preserving compatibility with those
formats. They must support more than one declared topology through the same
declaration-driven path and must not retain fixed element inventories,
element-name parsing, fixed parameter-index dispatch, or duplicated
stepped-topology policy.

Frozen experiment evidence remains unchanged and keeps its own identities and
integrity constraints. Agreement with it is context, not schema compatibility.
No migration promise exists for experimental artifacts.

## Explicit Deferrals

This contract does not define a public schema, durable format, arbitrary-cloud
admission, automatic recognition or correspondence, pose estimation, joint
pose-and-shape fitting, meshes, NURBS, blends, unrestricted B-reps, a general
constraint language, CAD/Onshape import or publication, generic solver or plugin
APIs, production backend selection, acceptance policy, physical validation,
metrology, accuracy, production readiness, or product support. Additional
primitive and relationship kinds require separately approved semantics and
evidence.
