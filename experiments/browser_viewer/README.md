# Nozzle browser selection experiment

**Provisional.** A local browser frontend for the captured simplified nozzle and
bounded generated fixture examples.
This is an experiment in selection and fitting interaction, not a browser-only
product decision. Python owns source validation, selections and fitting. A native
frontend can reuse the session records and Python adapter without browser code.

## Run

From the repository root, using the locked mise tools and Python environment:

```sh
mise install --locked
uv sync --locked
npm ci --ignore-scripts --no-audit --no-fund --prefix experiments/browser_viewer
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser
```

Open the printed `http://127.0.0.1:PORT/` address in Brave or another WebGL2-capable
browser. An explicit `--port 8765` is optional. Stop the local server with Ctrl+C.
The adapter supports the captured simplified example plus generated directories
that implement its bounded manifest/selection contract; this is not an arbitrary
mesh loader or a full-resolution GUI. The repeated-boss fixture documents its
generation and `--example` command in
[its example README](../../examples/repeated-boss-selection/README.md).

The default graph starts from the checked-in compatibility-free bundle containing
the 11 retained user selections. It deliberately does not reconstruct old fits,
constraints, joints, or results. `--recipe PATH` remains available only for
opening an explicit experimental action recipe; it is not a compatibility
promise for a successor format.

The browser loads the full 24,999-vertex, 49,994-triangle simplified mesh. No mesh
is uploaded externally. Assets are served locally after installation. Three.js
0.186.0 is the sole npm dependency, has an MIT license, and is pinned with npm
integrity metadata. No framework, bundler, desktop browser wrapper, or production
Python dependency is added. Node is pinned in mise for installation and checks;
it is not needed to run the Python server after asset installation.

## Interaction

Mouse bindings follow the user's Onshape configuration:

| Gesture | Action |
| --- | --- |
| Right-drag | Free rotation, including roll |
| Alt + right-drag | Upright rotation about the example's vertical axis |
| Ctrl + right-drag | Pan |
| Middle-drag | Pan |
| Wheel | Zoom |
| Left-drag | Chosen rectangle-selection operation |

Ctrl+right-drag and middle-drag pan in CSS pixels at the surface depth picked at
pan start, so that point follows the cursor. Over empty space they use the view
target's depth. Scaling accounts for field of view, camera zoom and viewport
height; it does not use TrackballControls' default pan sensitivity.

Ctrl and Alt take effect immediately during a held drag, including key release.
Ctrl switches right-drag to pan; releasing it resumes rotation. Alt switches to
upright rotation unless Ctrl is held. Mode transitions restart movement deltas at
the current pointer position rather than applying the previous mode's drag offset.

The always-visible Navigation panel at the upper-right has separate **Zoom at
cursor** and **Rotate at cursor** toggles, both on by default. Rotation picks a
surface anchor at drag start and keeps that pivot for the gesture without
recentering the view. Zoom keeps the picked surface point under the cursor.
Over empty space, rotation uses the current view target; zoom uses the cursor ray
at the current target depth. Only the scan is picked, not guides or selection
markers. Turning a toggle off uses the current view target for that operation.

Fit view recenters and fits the mesh while preserving the viewing direction and
roll. Side and Top explicitly change orientation. The Alt behavior follows
[Onshape's upright rotation description][onshape], but this is not a claim of
identical sensitivity, pivot picking, or all Onshape shortcuts. Other navigation
configurations remain future work. Navigation is isolated in `navigation.js`.

Select a selection in the feature tree, then choose **Paint** or **Rectangle**,
and Add, Remove or Replace in its properties pane. Painting always edits the
highlighted selection; inspecting a fit, axis, solve, constraint, source or joint
disables painting and hides the selection tools. There is no separate active
selection.
The shared **Depth** selector offers **First surface** (default) and **Through all**
for both tools. Paint uses a circular brush with an adjustable diameter in CSS
pixels and a visible footprint. Click to dab, or drag to sweep a continuous strip;
separate Add strokes create multiple patches. Replace replaces the selection with
the whole stroke, not only the latest dab. Selections are independent and may
overlap. Fits count their input union once; joints require disjoint observations
between participating fits.

Painting previews membership during the stroke. Release commits one current-graph
update and invalidates dependent fits. Escape, pointer cancellation, focus loss,
resize or starting camera navigation cancels the unfinished gesture. Navigation
bindings are unchanged. Edits wait for any running fit to finish. A pending
selection save temporarily disables sidebar controls and further painting.

First surface tests visibility at each candidate vertex's exact projected
position against the captured mesh's double-sided triangles. Occluders are clipped
to the camera frustum, including triangles crossing the near plane. A screen-tile
index and a cache for the current view avoid full triangle scans for every dab.
Changing the view or depth mode replaces that cache. Through all does not build
the occlusion index.
An occluder must be closer by more than `1e-9` in normalized device depth; this is
a numerical tolerance, not a physical surface-distance threshold. Guides and
selection markers do not occlude the scan. Through all skips occlusion tests but
still excludes vertices outside the camera frustum. These semantics are shared
with rectangles. Brush footprints smaller than the local vertex spacing can
select nothing; the tool selects existing vertices, not new surface samples.

The backend retains resolved source vertex IDs and the active selection's most
recently applied depth mode. Mixed strokes do not retain a stroke log or camera
snapshots, and their gesture sequence cannot be replayed. Saved IDs reproduce the
current membership. Gesture provenance and boundary inset remain future work; no undo
stack, revision chain or change log is retained.
Restore example graph reloads the retained-selection starting graph from disk.

## Ordered actions

Creation buttons stay in the top toolbar; fit, relationship, and joint creation
open dialogs.
The left column has two independently scrollable areas: features above, selected
feature information/properties and results below. Selection editing appears only
for a selected selection. Save actions, Load actions and Restore example are in
the project header. Display settings sit beside Navigation over the viewport;
general status and errors appear in the footer. **Show reuse volumes** displays
all currently evaluated generated reuse-selection envelopes as translucent
target-colored overlays. It is off by default and is display state only; the
surface-normal acceptance test still determines which vertices inside an
envelope become selected.
Drag the divider between the tree and attributes to resize them. With the divider
focused, Up/Down adjusts the split (Shift for larger steps); Home/End selects its
limits. Escape cancels an unfinished drag. The split is remembered in browser
storage when available, and both panes retain their own scrollbars.

The main workspace switches between **Model** and a read-only **Graph** view; the
feature tree and properties remain available in both. Graph selection is
synchronized with the tree and 3D model. Its **Combined** lens distinguishes
directed evaluation dependencies, geometric-relationship participants, joint
activation, and generated ownership. Separate **Dependencies**,
**Relationships**, and **Joints and solves** lenses isolate those meanings.
Selection details and generated outputs are shown by default so the initial graph
is complete; either can be hidden for a higher-level view. **Selected
neighborhood** is on by default to show the selected features and their immediate
connections without opening a large recipe as a hairball; uncheck it to reveal
the full selected lens. The layout and filters are presentation state only and do
not change recipe order or evaluation. Appending `#graph` to the local viewer URL
opens this workspace directly.

The action list is a dependency-valid sequence owned by the Python backend.
Each unnumbered row has a locally drawn line icon for its operation (including
point datums and plane, cone, cylinder, and sphere fits), its name, and a status
symbol: green check for
ready, gray ring for unevaluated, amber refresh for stale, blue spinner for
running, and red warning for failed. Tooltips and accessible names explain the
icons; the properties panel shows the full status and any error.
Clicking an action toggles it in a persistent feature-selection set without a
modifier key. One selected action exposes its settings and result; multiple
selected actions show an operand summary for **Relationship…**. Click a selected
action again, click empty tree background, choose **Clear**, or press Escape to
remove selections. Drag an action's grip to insert it before or after another
row; the insertion line turns red for invalid dependency orders. Focus a feature
and use the Up/Down keys to move the single selection and keyboard focus without
scrolling the panel; focus its grip to use the same keys to reorder it.
Reordering requires every input to still
appear earlier and waits until evaluation or selection editing finishes.
Stable IDs preserve references when positions change.
A valid reorder preserves cached results. This is recipe order, not edit history.

Automatic evaluation is enabled by default and evaluates the loaded graph plus
subsequent edits; it can be disabled from **Run → Auto**. A manually initialized
axis is previewed immediately because its value is already known. **Evaluate action**
evaluates the selected action and only the earlier inputs it requires;
**Evaluate all** explicitly evaluates every stale or unevaluated action. The
top action toolbar groups primitive actions under **Create**, **Organize**,
**Reuse**, **Relate**, **Combine**, and **Run**. **Group** creates or edits
presentation-only feature-tree groups without changing graph dependencies or
evaluation. A generator action is itself a collapsed group, with its generated
target groups directly beneath it. Generator and target groups use the same
folder-based, collapsible presentation as user groups; generated target groups
and actions carry a **Generated** marker. Generated actions remain inspectable
and usable as inputs, but their properties, deletion, and reordering are managed
by their owner. **Evaluate all** and the **Auto** checkbox live under Run.
Enabling automatic evaluation immediately evaluates the
graph and repeats **Evaluate all** after creation, property, selection, reorder,
delete, load, or reset changes. **Show all available fits and references** keeps
available guides visible together; disabling it restores selected-action-only
display. The selected action's evaluation and proposal controls remain in its
properties pane. **Relationship…** opens the common exact-relationship builder,
prefilled from the persistent feature-selection set; participants can also be
changed in the dialog. Incompatible relationship kinds remain visible there with
an explanation.

1. **Selection** creates an empty selection independently of any fit. Paint
   it, rename it, and add more selections as needed.
2. **Surface fit** chooses cone, cylinder, plane or sphere and one or more earlier
   selections. Overlapping input memberships count each source vertex once.
   Evaluate the fit to inspect its own parameters, guide and residuals. Plane
   fits can have independent orientations and do not require a joint solve.
   Standalone sphere fits resolve an independent center and radius. A sphere may
   instead reference a point datum. Sphere selection growth and exact Rhino
   export are implemented, while reuse volumes and axis-based relationships
   remain limited to the surface types documented for those operations.
3. **Point** defaults to a manual XYZ initial value. Sphere fits referencing a
   manual point jointly refine that shared center. Alternatively initialize the
   point from an earlier standalone sphere fit, which makes it fixed; the dialog
   can also create a point-bound factor from the same observations.
4. **Axis** defaults to a free-axis initial value entered directly as a point
   at local Z=0 and two direction slopes. The zero defaults describe the local Z
   axis. Alternatively, initialize the separate axis action from an earlier
   standalone cone or cylinder fit; in that mode the dialog can also create an
   axis-bound factor of the same type from the same observations. An axis can
   also be constructed through two point datums, directed from the first toward
   the second. Every axis has an explicit **Flip positive direction** setting
   and is drawn with an arrowhead. Point-pair axes support arbitrary directions,
   including horizontal ones; in this bounded slice they are datum-only and do
   not yet drive surface fits.
5. **Plane** creates an explicit reference plane from an earlier axis. Choose
   **Contains axis**, **Parallel to axis**, or **Perpendicular to axis**. The
   first two use a clocking angle; a parallel plane also has a signed normal
   offset. A perpendicular plane has an axial offset from the axis initializer's
   point at local Z=0. The plane is previewed immediately and remains a
   separately selectable feature. That axis point is a bounded prototype
   convention; the generalized model should use an explicit point or frame.
6. **Frame** defines an explicit right-handed coordinate frame. Choose a point
   datum as its origin and map two nonparallel axis, reference-plane, or fitted-
   plane directions to two distinct signed output axes such as `+Z` and `+X`.
   The secondary direction is projected perpendicular to the primary direction;
   the third axis is derived by the right-hand rule. Parallel references and
   conflicting output-axis choices are rejected.
7. **Scale** derives one uniform scale from one or more known distances between
   point datums. Its least-squares result reports every measured and scaled
   distance and residual, plus weighted RMS and worst absolute residual. With
   multiple distances, weights determine their relative influence.
8. **Transform** composes one earlier Frame and one earlier Scale into an
   applicable 4×4 output matrix. When it is the graph output, the model view
   applies it to the mesh, selections, fits, datums, residual markers, and reuse
   volumes; selecting another evaluated Transform previews that transform instead.
   Export applies the same matrix. Upstream fitted values remain in source
   coordinates, and the construction pieces remain independently inspectable.
9. **Feature** is the high-level reuse workflow. Choose one or more existing
   cylinder/plane fits, a painted reference selection on that occurrence, and
   one or more painted target selections on similar occurrences. It estimates
   an independent approximate rigid transform for each target, captures the
   source fits' datum/relationship lineage, and generates a separate target
   selection plus fresh standalone fit for every target and source fit input
   selection. Generated fits remain independent by default. **Equal
   corresponding dimensions** adds a visible managed **All equal radii**
   relationship for each reused source cylinder, including the source fit and
   every generated copy. The same ordinary relationship can be authored manually
   by selecting the corresponding source and generated cylinder fits, opening
   **Relationship…**, and choosing **Equal radii**. The shared-radius result is
   exact while each cylinder retains its independently fitted axis. Paint an
   asymmetric cue when possible; rings or cylinders alone cannot determine
   clocking. This first slice stays on one mesh and does not yet recreate target
   datums or arbitrary source relationships.
10. **Region** lifts an earlier selection and its cylinder or plane fit into a
   reusable surface-following volume. Choose a perpendicular axial plane for
   the frame origin and an axis-parallel plane for clocking, plus footprint,
   surface-normal, and normal-angle margins. **Apply** places that region in
   another datum frame on the same source mesh and resolves a new selection.
   The applied selection is a normal input to later surface fits. Fit it
   standalone before using that fit to initialize a refined target axis; making
   the applied selection directly drive the same free frame would be circular.
   This first slice does not yet support cones, target-pose discovery, or another
   scan.
11. **Relationship…** accepts either the features already selected in the tree or
   participants chosen entirely in its dialog. The currently implemented choices
   are exact coincident planes, parallel plane fits, equal sphere/cylinder radii,
   mirror symmetry, and cylinder-radius-equals-plane-distance. Coincident and parallel
   plane-fit relationships resolve their connected planes directly without a
   joint. Equal radii preserves independent sphere centers and cylinder axes while
   fitting one exact shared radius. Mirror relates two distinct, same-type
   standalone fits across an axis-containing reference plane.
   Radius/plane-distance equality relates an
   axis-bound cylinder, a standalone plane fit, and a reference plane on that
   axis. Mirror and radius/plane-distance definitions participate in an explicit
   joint when they must refine the shared axis or reference-plane clocking. The
   current bounded adapter permits one mirrored pair per reference plane in a
   joint. No penalty weights or arch-specific relationship are introduced.
12. Choose shared upstream geometry under **Reference geometry**. A sphere can
   reference a point; a cone or
   cylinder can reference an axis; a plane can reference an axis (making the
   fitted plane perpendicular while fitting its offset) or a reference plane
   (fixing its orientation while fitting a new offset). In every case,
   sphere fits connected to a manually initialized point refine that free point
   together, and fits connected to a manually initialized axis refine that free
   axis together. A point or axis initialized from an earlier fit remains fixed.
13. To explicitly choose the observations and relationships participating in one
   solve, choose **Joint**. Select
   the explicit axis and the active bound fits and relationships. Evaluation
   returns a separate result: bound factor sets influence axis direction,
   while a perpendicular plane does not locate the axis transversely. Mirror
   member observations refine their symmetry plane and shared axis. Standalone
   fits, reference definitions, and the axis initializer remain unchanged.

The older constraint/joint implementation remains readable as bounded experiment
evidence, but its creation controls are no longer part of the default workflow.

To extend an existing joint, select it, choose standalone fits under **Add fitted
surfaces**, then click **Add fits to this joint**. Each additional plane gets a
perpendicular constraint to the shared axis; each cone/cylinder gets a coaxial
constraint. The joint moves after the new inputs/constraints while preserving its
ID and existing constraints. Evaluate it to update its adjusted results. Planes
have independent offsets but share the exact axis normal; additional planes can
change the fitted axis. Joint observations must remain disjoint.

If a joint fails because its fits share observations, the backend reports every
conflicting fit pair and the exact shared source IDs. The viewer shows magenta
vertices with white outlines through the mesh, independently of the normal
selection-point and guide toggles. An always-visible banner gives the total and
opens the conflicting-fit list; its buttons inspect the fits involved. Highlights
remain while inspecting other features, then clear when an affected input changes.
Re-evaluate the joint after editing to check the new memberships.

Fit properties expose type, earlier selection inputs, optional reference axis,
and finite axial support. Axis properties expose their manual value, standalone
cone/cylinder initializer, or ordered point pair plus direction flip. Reference-plane
properties expose their axis and initial clocking. Mirror properties expose the
plane, two same-type standalone members, and the export-extents choice.
Joint properties expose the axis and exact active fit/relationship list.
Legacy constraint properties expose earlier fit references; legacy joint-fit
properties expose constraint inputs. Deleting an action is allowed only when
nothing references it.
Editing settings invalidates dependent results. In-flight results from an older
recipe are discarded by the backend. Empty/ill-conditioned fits fail visibly.

Ordinary markers use a lighter version of their region color to stay distinct
from the lit surface tint. Selecting a feature further brightens its vertex colors
and increases markers
from 3 to 5 CSS pixels. Selections highlight their memberships; fits highlight the
union of their inputs; constraints and joints highlight participating fits.
Growth highlights its resolved output, excluding barriers. The camera stays put.
Normal point markers retain depth testing: a small slope-aware polygon offset on
the rendered mesh prevents its own surface from cutting through the markers,
without moving geometry or changing selection picking. Genuinely intervening
surfaces still occlude normal markers. The selected-vertices toggle controls both
normal and emphasized markers; overlap diagnostics remain separate. Selected fit
and joint guides are brighter, and constraints show available standalone guides.

Guides show through the mesh; their extents are display support, not inferred
physical boundaries or uncertainty. Residual colors use independent symmetric
blue/white/red scales per surface. Units remain unconfirmed and lower training
residual is not physical validation. Cone/cylinder initialization and local frame
remain specific to this example, including the default axial support `[-2, 5]`.

## Rotational surface symmetry

**Rotational symmetry** adds a relationship to an existing joint. Choose three
existing fits of the same type (plane, cylinder, or cone), in 0°/120°/240° order.
Create fits for raw selections first. Creation references the original fit IDs;
it does not create replacement fits, convert their types, or edit memberships.
Mixed types are rejected in both the UI and backend. Edit the relationship's
references to change correspondence. The serialized `planes` field is retained
for existing recipes, but now references same-type surface fits.

The surfaces are exact rotated copies about the common axis. Their observations
participate in the same area-weighted joint solve and can move that axis.
Standalone fit results remain separate. Cylinders share a radius and rotated
axis lines; cones also share taper. The first fit initializes the common side;
its axial support is rigidly transformed for the other two copies. As with the
existing lateral solver, axes must stay within the local Z parameter chart.
Plane derivatives are analytic. Lateral shape derivatives are analytic, with
central differences for the four moving symmetry-axis parameters.

The joint still requires a connected coaxial group and at least one perpendicular
plane. A symmetry member cannot also be a coaxial/perpendicular member or belong
to another rotational group in the same joint. Other repetition counts, automatic
correspondence, and fixed-axis mode remain future work. Overlapping observations
fail with the existing highlighted diagnostics.

Generated checks cover plane/cylinder/cone recovery, derivatives, graph replay,
type preservation, and rejection without mutation. These verify implementation,
not physical accuracy or the user's chosen correspondence.

[onshape]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-rotation-with-an-upright-vertical-axis

The symmetry action defaults to **Match exported extents across symmetry copies**.
For Rhino export, observations from all three selections are rotated into the
first copy’s frame to bound one patch; that same patch is then rotated to each
copy. This gives matching rectangular plane patches and matching cylinder/cone
spans. Disable the option in the symmetry action’s properties for independent
bounds. It changes exported extents, not selection membership or the fitting
objective; viewport guides still use their existing bounds.

## Experimental Rhino export

**Export Rhino…** evaluates a chosen standalone fit, shared-axis joint, or legacy
joint and downloads one Rhino 8 `.3dm` containing named analytic surface bodies
and, optionally, the loaded original reference mesh on a separate layer. It uses
`rhino3dm` (MIT,
including the underlying openNURBS library); Rhino is not required locally.
This is a provisional export path, not a general CAD integration commitment.
When updating rhino3dm, review its changelog and repeat geometry/file round-trip
and Onshape import checks; its bundled type stubs currently omit runtime APIs.

Choose units explicitly. When an evaluated **Transform** is applied, its Scale's
known distances rescale the numerical coordinates, its Frame supplies output
origin and orientation, and the chosen unit label must match those distances.
The same matrix is applied to every exported surface and mesh vertex. Without a
Transform, the unit option only assigns units. The older
axis-up control maps
the fitted joint axis (or standalone lateral axis / plane normal) onto +Z and
applies the same rotation to every surface and mesh vertex. A standalone sphere
has no orientation; axis-up only centers its centerline on Z. With axis-up off,
geometry is restored to the original scan coordinate frame. Explicit Transform
and legacy axis-up/origin placement are mutually exclusive. The export embeds
the current recipe and coordinate transform as file metadata; it does not modify
the session or introduce change history.

Plane patches use projected rectangular selection bounds with 5% padding.
Cone/cylinder patches use full 360-degree sides over the selected axial span,
with 5% padding clipped to the declared support. Sphere fits export as complete
analytic spheres because a spherical trim footprint is not yet modeled. Plane
and lateral patches are independent open surfaces, not reconstructed trim
boundaries or a sewn solid. The reference mesh retains its vertices and triangle
connectivity, using double-precision storage; no decimation or conversion of
mesh triangles into CAD faces is performed.

Local checks reopen the exported file and verify analytic surface recognition,
mesh coordinates and topology, unit metadata, common orientation, and rejection
of stale results. Onshape documentation lists Rhino surface and mesh import, but
this experiment's combined-file import still needs an actual Onshape check.

Axis-up export also translates the fitted axis onto the Z axis. It preserves
rotated axial heights and applies the same rigid transform to the reference mesh
and every surface. Turning axis-up off retains original scan coordinates.

**Origin along axis** optionally chooses a plane from the exported fit or joint.
Its intersection with the axis becomes the export origin; joint planes use their
constrained geometry. A parallel plane is rejected because there is no unique
intersection. This option requires axis-up export.
