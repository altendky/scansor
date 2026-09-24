# Selection Tools and Feature Graph

## Status and scope

**Current prototype and future requirements, 2026-09-13.** These
requirements follow the initial
[nozzle browser experiment](../../../../experiments/browser_viewer/README.md).
The backend DAG now stores an ordered action sequence: sources, selections,
standalone fits, constraints and joint solves. Fits consume one or more earlier
selections; joints produce separate adjusted results. The browser and script use
the same backend. Valid reordering, current-recipe save/load and dependent-result
invalidation are implemented; change history is not. Painting, shared depth
modes and connected growth are implemented. Additional shapes and inset remain
future requirements.

Browser-first remains an experiment sequence, not a browser-only product choice.
Selection records, operation evaluation, model declarations and fit results must
remain usable without browser code. This work does not admit the captured scan
into the existing synthetic-only canonical workflow or establish physical accuracy.

## Selection tools and depth behavior

Support multiple ways of creating or editing mesh-vertex selections:

- Rectangle.
- Circle.
- Arbitrary polygon.
- Surface painting, including multiple separate seed patches.
- Consider regular polygons as another shape option.

Tool shape and depth behavior are independent properties. Every listed tool
should support both **through all** and **first surface** selection, rather than
making through-selection exclusive to rectangles or surface-selection exclusive
to painting. Record the depth mode with the selection operation.

Through all includes eligible vertices behind intervening surfaces. First surface
uses the frontmost surface under the selection footprint. It is not simply a
front-facing-normal test or the single closest vertex. Precise rules for vertex
visibility, partially occluded triangles, silhouettes, gaps, backfaces, depth
tolerances, and hidden objects remain to be specified consistently across tools.

Add, remove and replace are selection operations distinct from shape and depth.
How memberships may overlap between surfaces is also a separate policy; the
current experiment's disjoint cone/plane requirement is not automatically a
universal selection rule.

**Implemented prototype, 2026-09-13:** a circular painting brush supports Add,
Remove and whole-stroke Replace, with continuous coverage between pointer events.
Painting and rectangles share First surface and Through all modes. First surface
means unoccluded source vertices at their projected positions, using double-sided
mesh triangles clipped to the frustum. Guides do not occlude selections. The
implementation and numerical tolerance are described in the browser README.

Each completed gesture replaces current membership and records its depth setting;
in-progress preview can be cancelled. Resolved source IDs are persisted, but no
stroke log or camera sequence is retained. Full gesture provenance/replay remains
open. Standalone circle/polygon selection and inset remain **future tasks**. The
seed-fit and connected-growth proposal slice below is now implemented.

## Painted seeds and assisted surface identification

The intended workflow is user-directed identification, not unrestricted automatic
scan interpretation. For example, the user paints three patches around the nozzle
and declares that they belong to a cone side. Retain the following operations:

1. **Seed selection:** retain the current seed definition and resolved source vertex
IDs.
2. **Preliminary surface fit:** fit the user-declared type to the seed observations,
   retaining parameters, weighting and diagnostics.
3. **Expand selection:** use that fitted surface to identify additional mesh area
   likely to belong to the same nominal surface.
4. **Inset selection boundary:** retreat from the expanded boundary to avoid noisy
   edge observations, fillets or transitions to neighboring surfaces.
5. **Use the resulting selection:** explicitly reference the resulting membership
   in subsequent fitting, retaining the earlier seed and intermediate results.

### Connected additions

**Requirement, 2026-09-13:** proposed additions must be contiguous with the seed
selection. Geometric agreement alone is insufficient. Every added vertex must
have a path to a seed vertex along valid mesh edges, through vertices that satisfy
the growth criteria. Do not jump between nearby disconnected components, across
rejected vertices, or over gaps using spatial nearest-neighbor links.

Several disconnected painted patches are valid seeds. Grow from each eligible
patch; their proposals may merge when an eligible path connects them. The result
need not be a single connected component. Preserve the explicit seed membership;
seed outliers must be diagnosed and must not serve as bridges through rejected
geometry. Degenerate triangles must not introduce traversal edges.

The implemented prototype uses distance to a fixed preliminary surface
and normal agreement as eligibility criteria, followed by mesh-edge flood fill.
Distance and angular thresholds are explicit recipe settings, not physical
accuracy claims. Vertices assigned to other surface selections are barriers for
proposal growth under the prototype's current disjoint-membership policy. This
avoids silently reallocating observations when asking for a proposal.

Growth follows the mesh and may wrap around the hidden side of the part. The
camera depth setting used to paint a seed does not limit this topology-based
operation. Disconnected but geometrically identical surfaces remain excluded.

The seed-only fit is separate from the final constrained solve. Do not use the
end plane or another surface's observations to silently stabilize the seed fit.
Insufficient seed coverage or an ill-conditioned fit should stop the proposal
with a diagnostic. The adapter now fits cone/cylinder seeds directly and plane seeds
through
area-weighted covariance, independently of final-fit observations.

Preview additions separately from the seed before applying them. Keep seed,
preliminary fit and growth as distinct current graph nodes; accepting the result
creates a later fit referencing the grown selection. Earlier fits and joints
remain unchanged. These are recipe dependencies, not
an edit-history log. Editing the seed or thresholds invalidates descendants.

Boundary inset remains a subsequent operation. It can disconnect a grown region,
so the later policy must specify seed protection and discard additions that lose
all paths to retained seed vertices. Do not claim the contiguity requirement is
satisfied merely because it held before erosion.

The first implementation tests demonstrate: growth on the intended surface;
exclusion of a disconnected matching surface; no crossing of an ineligible strip
or another selection; multiple seed patches; and invalidation after seed edits.
Automatic iterative refitting, inset policy and mesh repair remain deferred.

Each step should be inspectable and editable. Expansion should expose its criteria
and preview the proposed membership; it must not silently replace the seed. A
failed preliminary fit or unsuitable coverage should produce a visible diagnostic.

Provisional algorithm considerations, not settled policies:

- Combine geometric distance, normal agreement and mesh connectivity. Distance
  alone can include a nearby disconnected surface or a transition region.
- Assess seed coverage and conditioning. Circumferential coverage helps a cone,
  but axial extent also matters when estimating taper.
- Prefer a boundary retreat measured along the mesh in source units over a fixed
  number of vertex rows, whose extent changes with mesh density. The distance
  algorithm and handling of holes, disconnected components and complete erosion
  remain open.
- Start with one preliminary fit, one expansion and one inset. Iterative
  fit-and-grow is a later option requiring checks for drift onto other features.

Distinguish the preliminary identification surface from the final constrained
fit. Residual-guided selection can reduce apparent training error by construction;
its provenance must remain visible. The final fit consumes explicit memberships
without silently continuing to trim or grow them. Expansion and inset thresholds
are selection policy, not evidence of physical correctness.

## Backend feature graph and frontend feature tree

**Backend ownership is a requirement.** The backend implements the feature graph:
operation records, dependency validation, evaluation, result invalidation,
diagnostics and persistence of the current graph. It is not a browser-maintained tree
that merely sends final selections to a fitting endpoint. Scripts, browser and
future native frontends use the same backend implementation. Frontends submit
edits and display backend state; they do not independently decide which cached
results remain authoritative.

The feature tree should expose the operations that produced the current state,
not only the latest two selections. Mesh vertices and feature-graph nodes are
different kinds of identity and must not be conflated.

Each operation needs a stable identity, type/version, explicit input references,
parameters, and source bindings. Retain its resulting membership or fit output,
relevant diagnostics, and evaluation status. Large ID lists and numeric results
may be referenced artifacts rather than duplicated inline in every record.
Selection operations must preserve canonical source IDs, not renderer-local IDs.

For screen-based gestures, record the view/projection, footprint or stroke,
operation and depth settings needed to interpret the recipe, alongside the
resolved membership. An explicit imported ID selection is also a valid starting
operation. The saved IDs retain what was selected even where renderer-dependent
visibility replay is not bitwise identical; replay guarantees remain to be defined.

Changing an upstream operation invalidates dependent results. Display stale,
failed and unevaluated states explicitly; do not present an old fit as current.
The current graph preserves distinct processing steps, such as seed, growth and
inset; that does not require retaining earlier versions of each step. Editing a
node replaces its current settings and invalidates dependents without keeping an
edit log or previous graph snapshots.

The frontend presents an ordered **feature tree** over the action graph. User
groups are presentation-only organization: they can be created, renamed,
collapsed, and assigned members without adding dependencies or solve semantics.
Generated actions instead carry an owning action and a stable owner-relative key.
They appear in a read-only **Generated outputs** subtree beneath that owner, are
collapsed by default, and cannot be independently renamed, reordered, deleted,
or moved into a user group. They remain selectable for inspection and usable as
inputs to later actions. Editing the owner synchronizes its managed subtree and
must refuse removal of an output referenced elsewhere. Deleting an owner deletes
its managed subtree as one unit, unless an action outside that subtree depends on
one of those outputs.

The backend remains a **directed acyclic graph (DAG)**, because one input can feed
several actions, with an additional ordering rule: every reference must point to
an earlier action. Organizational grouping does not alter that flat evaluation
order. Reordering is allowed only while the dependency rule holds. Stable IDs
identify actions independently of list position.

Cone, cylinder and plane are types of standalone fit actions. Each fit references
one or more earlier selections and evaluates their deduplicated union. Selections
may overlap; painting one does not silently change another. Independent plane
fits may have arbitrary orientations.

Constraint actions reference earlier fits. A later joint action solves their
observations together under those constraints and produces separate adjusted
results. It preserves each standalone fit and its result. Mutual geometric
constraints do not create circular execution dependencies. The legacy joint
adapter supports independently offset perpendicular planes and connected coaxial
cone/cylinder sides with disjoint observations. All planes share the axis normal
and all surfaces inform the shared axis. The newer bounded browser slice instead
creates an explicit axis from a manual initial value or a standalone cone or
cylinder. Fits connected to a manually initialized axis jointly resolve that
free axis and its derived planes without a separate joint action. Fits connected
to an axis sourced from an earlier fit treat it as fixed. An explicit shared-axis
joint remains available to select a factor set and add relationships, producing
a separate result. Broader
constraint networks remain future work.

**Provisional successor direction with a first internal contract, 2026-09-20:**
generalized model semantics make reference geometry and solve participation
explicit rather than connect fitted surfaces through a joint-owned hidden axis.
The compatibility-free contract now lets cylinders reference an identified axis
line, lets a plane derive its normal from an explicitly oriented direction
parallel to that axis, and makes each solve declare fixed/free quantities and
active observation factors. It validates exact salvaged-selection bindings and
compiles factor influence separately for axis position and direction. The
operation DAG continues to own
ordering, provenance, and invalidation, while a separate factor-graph view owns
simultaneous influence. See the
[reference-geometry design](reference-geometry-and-solve-graph.md). A bounded
numerical/browser slice now exercises cone/cylinder-and-plane behavior, without
reinterpreting the legacy joint adapter or making either internal recipe format a
public compatibility contract.

Before that successor replaces the prototype format, preserve retained
user-authored selections through one exact-version offline conversion. Copy
source bindings, labels, depth settings, and resolved source IDs; materialize any
evaluated growth result as a static imported selection. Do not migrate fit,
constraint, symmetry, joint, cached-result, or output semantics. The new runtime
need not read the old recipe after the selected samples are converted. See
[one-time selection salvage](reference-geometry-and-solve-graph.md#one-time-selection-salvage-not-backward-compatibility).

**Implemented for discovered saved recipes, 2026-09-20:** the exact-v2 offline
converter preserves the two checked-in memberships and the `11` selections in
the user-saved `nozzle-actions.json`. Two older snapshots with conflicting reused
selection IDs are retained as separate bundles rather than merged by an inferred
precedence. One of those snapshots materializes its evaluated growth result as a
static selection. Reports list every discarded fit/constraint/symmetry/joint
node. The new bundle parser has no legacy recipe path; visual overlay remains a
manual check.

Growth references an earlier fit. Using its result means adding a later fit,
possibly followed by a later joint; it cannot rewire an earlier fit to a later
selection. This preserves the seed → fit → growth → fit processing sequence.
Version 1 recipes migrate once to version 2 and a stable dependency order. Version
2 loading rejects invalid order rather than changing it behind the user's back.

Persistence must include the operations and bindings needed for replay, not only
a screenshot or flattened final IDs. Broader cache, migration, branching and suppression
policy remains open.
The prototype permits deletion only when no action references the deleted one. A browser
UI
must not become the owner of authoritative operation evaluation.

## Threefold rotational surface relationship

The symmetry action defaults to **Match exported extents across symmetry copies**.
For Rhino export, observations from all three selections are rotated into the
first copy’s frame to bound one patch; that same patch is then rotated to each
copy. This gives matching rectangular plane patches and matching cylinder/cone
spans. Disable the option in the symmetry action’s properties for independent
bounds. It changes exported extents, not selection membership or the fitting
objective; viewport guides still use their existing bounds.

The bounded prototype supports three same-type fits (planes, cylinders, or cones)
related by exact 0°/120°/240° rotations around the shared cone/cylinder axis.
The relationship references existing fits and preserves their types and selection
memberships. Mixed types are rejected; raw selections need explicit fit actions
first. The legacy serialized `planes` reference list remains readable and now
accepts any of these homogeneous fit groups.

All observations participate in the containing joint's simultaneous solve.
Input correspondence is explicit, with no segmentation or permutation search.
For cylinder/cone copies, the first fit's axial support is transformed with the
surface. The current solver still requires a connected coaxial group and at least
one perpendicular plane, and retains the local Z-axis parameter-chart limitation.
Independent joint actions do not share mutable fitted results. Fixed-axis mode,
other repetition counts and broader symmetry families remain future work.

## Deferred change history

**Do not implement change-history tracking in this slice.** Undo/redo needs future
discussion, as does the possibility of persistently storing every change on disk.
Do not introduce revision chains, an event log, automatic historical snapshots,
or retained prior settings/results to anticipate that decision. The migrated
frontend should not maintain its own parallel undo history either.

Saving and loading the current graph remains in scope. Keeping distinct nodes in
a current processing recipe is not edit history. Identifiers used to detect stale
in-flight results need not retain previous states. History semantics, storage
cost, retention, replay and recovery are deliberately unresolved.

## Initial implementation slice

The initial implementation followed this sequence; the ordered-action update
above supersedes its declaration-only fit and feature-tree presentation. Further
generalization,
new selection mechanisms and discovery algorithms remain deferred.

1. Define a small set of explicit records: source reference, selection creation
   and edit, declared surface, geometric relationship, and joint fit request.
   Fit types and selection-to-surface bindings are recipe data rather than fixed
   browser labels or branches inside the nozzle runner.
2. Implement dependency validation, evaluation, stale-result handling, serialization
   and current-graph replay in Python, independently of HTTP and UI objects. Use a
   bounded set of supported operation types, not an arbitrary executable plugin format.
3. Express the current example as a recipe: source mesh, saved outer-band and
   top-face selections, cone and plane declarations, their exact perpendicular
   relationship, and one joint fit. Reuse existing numerical implementations.
4. Make both the script and browser consume that same recipe. Add an inspectable
   feature tree showing inputs, settings, status and results. Rectangle edits
   update a current selection node and invalidate dependent results, without
   recording every gesture as a historical node.
5. Verify that recipe execution reproduces the current selections and fit within
   appropriate numerical tolerances. Editing either selection must invalidate and
   recompute the shared fit. Save/load must preserve dependencies and provenance;
   reject cycles, missing inputs and source mismatches.

Generalization should first remove hardcoded workflow choices, not promise a
universal solver. Adapt only implemented fit families and relationships, and fail
explicitly for unsupported combinations. The existing cylinder/plane and
cone/plane experiments provide useful alternative recipes to demonstrate that
surface type comes from the declaration without adding a new solver family.
Reuse applicable existing declaration concepts while keeping the captured-example
adapter separate from the canonical synthetic-only admission contract.

Continue with further selection shapes, seed fitting, growth and boundary inset
as independently
inspectable operation types. This order gives those tools an operation graph and
current-recipe replay model from their first implementation, without deciding
edit-history policy.

## Reusable fitted selection regions

**Provisional experiment with a bounded browser slice, 2026-09-23.** A
source-vertex selection can
be lifted into a transferable region by combining its fitted analytic surface,
its bounded surface footprint, and explicit inward/outward offsets. The result is
a volumetric selection region rather than another source-specific vertex list.
Applying it to another occurrence or scan requires an explicit rigid transform,
then resolves a new source-bound membership while retaining both the region
definition and the resolved IDs. This is intended to reuse selection effort; it
must not imply vertex correspondence, identical tessellation, or physical
accuracy.

Rotationally symmetric fits do not determine clocking about their axis. A partial
footprint therefore needs another orientation cue, such as a plane, key, or
clocking flat, before it can be transferred without ambiguity. Compound groups
can supply that frame by owning reference geometry and fitted members while each
occurrence owns its pose. Whether groups become first-class feature-graph nodes
and how transferred boundaries behave near missing data remain open.

The browser implements two same-source cylinder/plane slices. A low-level
**Region** action stores a fitted surface footprint, tangential and
surface-normal margins, source-normal tolerance, and a frame defined by an axis,
perpendicular axial plane, and axis-parallel clock plane. **Apply** places that
region in another such frame and resolves a new source-vertex membership usable
by later fits.

The higher-level **Feature** reuse action takes one or more fitted surfaces, one
user-painted reference correspondence selection, and one or more independently
painted target selections. It estimates a separate approximate rigid transform
from the reference occurrence to each target occurrence without using fixture
labels or vertex correspondence. For every target and source fit selection it
constructs a surface-relative region, applies that region through the target's
estimated transform, and exposes the resulting membership to a new standalone
fit of the same type. The action records the selected fits' complete upstream
lineage and any relationships enclosed wholly by the selected fit set, so
provenance is not lost while compound-feature semantics remain under design.

### Reuse-volume inspection

**Provisional display plan, 2026-09-23.** Reuse regions should remain graph
results rather than becoming additional feature nodes. The viewport should be
able to display their spatial envelopes with translucent faces and stronger
boundary lines. A global Display control governs the entire overlay; later tree
controls may provide tri-state visibility for each reuse action, target group,
and generated selection. Source envelopes should be distinguishable from their
transformed target envelopes, and inspecting one generated selection should be
able to emphasize its corresponding volume. Visibility is presentation state
and must not affect graph identity, evaluation, or saved actions.

The spatial envelope alone cannot represent the surface-normal tolerance. A
complete inspection design should distinguish points which are spatially inside
the envelope and pass the normal test from points which are inside but rejected
by that test. Stale envelopes should be marked as stale or withheld rather than
presented as current geometry.

The first browser slice intentionally provides only an off-by-default **Show
reuse volumes** checkbox under Display. It renders every ready target envelope
from the same retained cylinder/plane region bounds and target placement used by
selection evaluation. It does not yet show source envelopes, rejection points,
per-reuse visibility, per-target visibility, or stale geometry.

This is deliberately an approximate placement stage followed by fresh target
fits. It does not yet clone datums or instantiate target relationships, jointly
solve the copied group, support cone regions, or transfer across a different
source mesh. Ambiguous rotationally symmetric correspondence selections remain
visible through a rotation-ambiguity metric; the user should paint a clocking
cue such as a flat or asymmetric edge.

The exploratory
[repeated-boss fixture](../../../../examples/repeated-boss-selection/README.md)
provides four boss occurrences with a shared cross-section, a clocking flat,
deliberate height and axis-pose variation, independent irregular mesh
topologies, deterministic deviations/noise/occlusions, and a separately posed
rescan. Its oracle role selections and coordinate layers remain test truth, not
algorithm inputs. A transfer test builds the region only from boss A's observed
outer-cylinder patch and its explicit source/target frames, then uses the hidden
labels afterward to verify that the resolved boss B vertices belong exclusively
to the intended outer surface and cover at least 80% of its oracle footprint.
