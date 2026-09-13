# Selection Tools and Feature Graph

## Status and scope

**Future requirements and provisional next-step proposal, 2026-09-12.** These
requirements follow the initial
[nozzle browser experiment](../../../../experiments/browser_viewer/README.md).
The first backend DAG slice now evaluates source, selection, surface,
perpendicular, coaxial and joint-fit records for the captured nozzle. A browser
feature tree and a script use that same backend. Cone/cylinder choice is recipe
data. Current-graph save/load and dependent-result invalidation are implemented;
change history is not. Additional selection tools and surface discovery below
remain future requirements.

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

Additional shapes, painting and first-surface implementation are **future tasks**,
not prerequisites for the first feature-graph implementation. The existing
rectangle tool can initially record its implemented through-all behavior; it
must not advertise first-surface selection before that behavior exists.

## Painted seeds and assisted surface identification

The intended workflow is user-directed identification, not unrestricted automatic
scan interpretation. For example, the user paints three patches around the nozzle
and declares that they belong to a cone side. Retain the following operations:

1. **Seed selection:** retain the current seed definition and resolved source vertex IDs.
2. **Preliminary surface fit:** fit the user-declared type to the seed observations,
   retaining parameters, weighting and diagnostics.
3. **Expand selection:** use that fitted surface to identify additional mesh area
   likely to belong to the same nominal surface.
4. **Inset selection boundary:** retreat from the expanded boundary to avoid noisy
   edge observations, fillets or transitions to neighboring surfaces.
5. **Use the resulting selection:** explicitly reference the resulting membership
   in subsequent fitting, retaining the earlier seed and intermediate results.

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

The frontend presents a **feature tree** for now. The backend uses a
**directed acyclic graph (DAG)**, because one selection or source may feed
multiple operations. Grouping, display order and dependency order need not match.
A joint fit of constrained surfaces is one evaluation node with multiple outputs;
mutual geometric constraints must not be represented as circular execution
references between independently evaluated surface fits.

Cone, cylinder and plane are types of individual surface-fit features, not global
workflow settings. Selecting a surface fit in the tree exposes its name, geometry
type and input selection. The prototype stores these as `surface` declarations;
the joint-fit node solves their parameters together under the constraint. Changing
one declaration invalidates the dependent joint solve. The current adapter supports
one perpendicular plane and any connected group of coaxial cone/cylinder sides,
within the bounded recipe limits. A joint-fit node references a list of constraints.
Adding a side creates a selection, a surface fit and a coaxial constraint referencing
an existing side. All surfaces inform the shared axis; holding it fixed is not
implemented. Broader constraint combinations require additional solver support
rather than silently changing the meaning of a constraint.

Persistence must include the operations and bindings needed for replay, not only
a screenshot or flattened final IDs. Cache policy, schema migration, branching,
suppression and deletion details remain open. A browser UI
must not become the owner of authoritative operation evaluation.

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

The implemented bounded slice follows this sequence. Further generalization,
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

After this slice, add painting, shared first-surface selection semantics, further
selection shapes, seed fitting, growth and boundary inset as independently
inspectable operation types. This order gives those tools an operation graph and
current-recipe replay model from their first implementation, without deciding
edit-history policy.
