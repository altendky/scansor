# Nozzle browser selection experiment

**Provisional.** A local browser frontend for the captured simplified nozzle.
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
Only the captured simplified example is supported by this adapter; this is not
an arbitrary mesh loader or a full-resolution GUI.

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

Creation buttons stay in the top toolbar; fit and joint creation open dialogs.
The left column has two independently scrollable areas: features above, selected
feature information/properties and results below. Selection editing appears only
for a selected selection. Save actions, Load actions and Restore example are in
the project header. Display settings sit beside Navigation over the viewport;
general status and errors appear in the footer.
Drag the divider between the tree and attributes to resize them. With the divider
focused, Up/Down adjusts the split (Shift for larger steps); Home/End selects its
limits. Escape cancels an unfinished drag. The split is remembered in browser
storage when available, and both panes retain their own scrollbars.

The action list is a dependency-valid sequence owned by the Python backend.
Each unnumbered row has a locally drawn line icon for its operation (including
plane, cone and cylinder fits), its name, and a status symbol: green check for
ready, gray ring for unevaluated, amber refresh for stale, blue spinner for
running, and red warning for failed. Tooltips and accessible names explain the
icons; the properties panel shows the full status and any error.
Select an action to inspect its settings and result. Drag its grip to insert it
before or after another row; the insertion line turns red for invalid dependency
orders. Focus a grip and use the Up/Down keys to reorder with the keyboard.
Reordering requires every input to still
appear earlier and waits until evaluation or selection editing finishes.
Stable IDs preserve references when positions change.
A valid reorder preserves cached results. This is recipe order, not edit history.

Creating an action does not evaluate it by default. A manually initialized axis
is previewed immediately because its value is already known. **Evaluate action**
evaluates the selected action and only the earlier inputs it requires;
**Evaluate all** explicitly evaluates every stale or unevaluated action. The
top action toolbar groups primitive actions under **Create**, **Relate**,
**Solve**, and **Run**. **Evaluate all** and the opt-in **Auto** checkbox live
under Run. Enabling automatic evaluation immediately evaluates the
graph and repeats **Evaluate all** after creation, property, selection, reorder,
delete, load, or reset changes. **Show all available fits and references** keeps
available guides visible together; disabling it restores selected-action-only
display. The selected action's evaluation and proposal controls remain in its
properties pane.

1. **Selection** creates an empty selection independently of any fit. Paint
   it, rename it, and add more selections as needed.
2. **Surface fit** chooses cone, cylinder or plane and one or more earlier
   selections. Overlapping input memberships count each source vertex once.
   Evaluate the fit to inspect its own parameters, guide and residuals. Plane
   fits can have independent orientations and do not require a joint solve.
3. **Axis** defaults to a free-axis initial value entered directly as a point
   at local Z=0 and two direction slopes. The zero defaults describe the local Z
   axis. Alternatively, initialize the separate axis action from an earlier
   standalone cone or cylinder fit; in that mode the dialog can also create an
   axis-bound factor of the same type from the same observations.
4. **Plane** creates an explicit reference plane through an earlier axis.
   Its clocking angle initializes rotation about the axis; the plane is previewed
   immediately and remains a separately selectable feature.
5. **Mirror** relates two distinct, same-type standalone fits across an
   explicit reference plane. It defines geometry; it is not itself a solve. Add
   the relationship to a solve group on that plane's axis. The solve may refine
   both the shared axis and the plane's
   clocking while keeping the two adjusted surfaces exact reflected copies.
   Axis-bound fits are not mirror members because coaxial sides and perpendicular
   planes are already invariant under reflection in any plane through their axis.
   The current bounded adapter permits one mirrored pair per reference plane in a
   solve; create another reference-plane action for another independently clocked
   pair.
6. **Parallel** relates a standalone plane fit to a reference plane. **Equal**
   currently supports typed equality between an axis-bound cylinder radius and
   the absolute distance from a standalone plane fit to a reference plane. With
   a mirrored plane pair, these independent primitives compile to exact parallel
   planes at opposite signed cylinder-radius offsets; no penalty weights or
   arch-specific action are introduced.
7. To keep an axis fixed, create a cone, cylinder, or plane fit and choose it
   under **Reference axis**. Evaluating the bound fit adjusts only its radius and
   taper, radius, or plane offset respectively; its observations cannot move the
   explicit axis.
8. To let observations refine the axis together, choose **Solve group**. Select
   the explicit axis and the active bound fits and relationships. The solve
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
and finite axial support. Axis properties expose either their manual initial
value or the standalone cone/cylinder used as their initializer. Reference-plane
properties expose their axis and initial clocking. Mirror properties expose the
plane, two same-type standalone members, and the export-extents choice.
Solve-group properties expose the axis and exact active fit/relationship list.
Constraint properties expose earlier fit references; joint properties expose
constraint inputs. Deleting an action is allowed only when nothing references it.
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

Choose units explicitly: this captured example's source units are unconfirmed.
The option assigns units without rescaling numerical coordinates. Axis-up maps
the fitted joint axis (or standalone lateral axis / plane normal) onto +Z and
applies the same rotation to every surface and mesh vertex. With axis-up off,
geometry is restored to the original scan coordinate frame. The export embeds
the current recipe and coordinate transform as file metadata; it does not modify
the session or introduce change history.

Plane patches use projected rectangular selection bounds with 5% padding.
Cone/cylinder patches use full 360-degree sides over the selected axial span,
with 5% padding clipped to the declared support. These are independent open
surfaces, not reconstructed trim boundaries or a sewn solid. The reference mesh
retains its vertices and triangle connectivity, using double-precision storage;
no decimation or conversion of mesh triangles into CAD faces is performed.

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
