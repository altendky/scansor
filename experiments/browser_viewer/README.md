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
npm run build --prefix experiments/browser_viewer
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser
```

Open the printed `http://127.0.0.1:PORT/` address in Brave or another WebGL2-capable
browser. An explicit `--port 8765` is optional. Stop the local server with Ctrl+C.
Use **Choose example…** beside **Restore example** in the Project toolbar to
open either **Nozzle** or **Repeated bosses** without restarting the server.
Opening an example replaces the current actions and reloads its mesh with the
checked-in default feature definitions; save actions first to keep your work.
**Restore example** reloads those definitions for the currently open example.
The repeated-boss coarse fixture is generated
on first use under the ignored `local-inputs/` tree if it is not already present.
The adapter supports the captured simplified example plus generated directories
that implement its bounded manifest/selection contract; this is not an arbitrary
mesh loader or a full-resolution GUI. The repeated-boss fixture documents its
generation and `--example` command in
[its example README](../../examples/repeated-boss-selection/README.md).

The built-in defaults are the saved
[nozzle recipe](../../examples/nozzle-bayonette-simplified/recipes/nozzle-selection-and-fitting-demo.json)
from `nozzle-actions.json` and the saved
[repeated-boss recipe](../../examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json)
from `boss-actions.json`. These retain the feature definitions, references, and
selections; numerical results are recomputed. `--recipe PATH` overrides the
startup recipe. Custom example directories without a built-in default continue
to start from their retained selection bundle. These experimental recipes do not
establish a compatibility promise for a successor format.
The nozzle default includes all 11 fitted surfaces. Three fits use the distinct
display names **outer fit**, **recesses fit**, and **top fit** to avoid collisions
with the saved selection names.
Loading saved actions repairs duplicate display names with descriptive suffixes
and reports the renames. References continue to use the original feature IDs.
Duplicate IDs and invalid references still fail validation; normal editing keeps
names unique.

The browser loads the full 24,999-vertex, 49,994-triangle simplified mesh. No mesh
is uploaded externally. Assets are served locally after installation and building.
Three.js, React, React DOM, and FlexLayout are pinned MIT-licensed dependencies.
Vite builds the React/FlexLayout workspace shell; existing feature forms and the
Three.js renderer retain their DOM and behavior. Vite's build-tool dependency
Lightning CSS is MPL-2.0 (file-level copyleft), not GPL/LGPL/AGPL. No desktop
browser wrapper or production Python dependency is added. Node is pinned in mise
for installation, building, and checks; it is not needed to run the Python server
after assets have been built. Rebuild after changing workspace source or updating
its dependencies. Generated `dist/` files are not committed.

## Workspace layout

Features, Model, and Graph are FlexLayout panels. The optional **Edit** tab shares
all feature-editing and creation workflows. Drag tabs to split, stack, or join
panels; drag splitters to resize.

Select a feature to highlight it; double-click to **Edit**, or use its context
menu for **Edit**, **Rename**,
**Evaluate**, organizational group assignment, or deletion. Group assignment
applies immediately; feature editing offers Apply only for editable parameters.
Editing opens in the Edit tab when present, otherwise
in a nonmodal popup. **Dock** retains the popup as an Edit tab; **Show panel →
Edit** also opens an empty tab. Its presence and placement survive reload, while
unfinished edits do not reopen. Generated features offer **Edit owning feature**
and a separate **Inspect** action for their own results and saved context.
An edit stays attached to its original feature while tree selection or model
picking changes. Background graph publication retains parameter drafts. Switching
or closing workflows asks before discarding unapplied changes.

The feature tree uses chevrons to expand organizational groups and generated
outputs. Click a row to toggle its selection; click its chevron to expand or
collapse. Drag a row to reorder it with its generated outputs, subject to the
dependency order. Dragging a selected row moves the selected features together
in their existing order. Generated leaves stay with their owner. Groups offer
**Ungroup** in their context menu, which keeps all their features.

Selecting features adds mild row tints: cool for their inputs, warm for their
dependents, including indirect relationships. Multiple selections combine these
relationships; selected rows keep their stronger highlight. The selection bar
shows the color key, and related rows describe their relationship in tooltips and
accessible descriptions. Collapsed branches show a tint when they contain related
features. These hints follow declared inputs and generated ownership; drop
validation still checks the proposed order and generated-output boundaries.

Keyboard focus follows visible rows: Up/Down moves between rows, Right expands
or enters a branch, Left collapses or returns to its parent, and Home/End moves
to the first/last visible row. Space/Enter toggles feature selection; Alt+Up/Down
reorders the focused row. Shift+F10 opens its context menu. Focus and multiselection
remain separate, so navigating does not change selected features.

The current tree trial uses Headless Tree's core for hierarchy, visible ordering,
focus, expansion, selection metadata, and keyboard navigation. A DOM adapter
preserves Scansor's toggle selection and explicit editing. Pointer dragging and
dependency validation remain in Scansor so wheel scrolling during a drag is
available.

Open **Icon legend** from the Project toolbar (or the workspace panel picker) to
see the current feature, fit, relationship, status and control symbols. The
read-only legend can dock or float alongside the tree and edit dialogs. It uses
the same drawings as those controls, and does not reopen automatically on reload.

Project, Create, Faces, Feature tools, and Run / output are independently dockable
toolbar strips, enabled by default. There is no separate title bar: Project starts
with a non-interactive Scansor title, followed by Save/Load/Restore and workspace
layout controls. The workspace picker is also available in its overflow menu. Drag each
strip's dotted grip to a workspace edge, or drop inside to float. The actual
toolbar follows the drag; Escape restores its previous position. Edge strips
orient automatically and share lanes, wrapping when necessary without overlapping.
Drop before or after a strip to set the order in that row or column; the preview
names the destination and insertion point. The options menu also offers Move
earlier/later. Order is saved per host and edge, and menu docking appends to its
destination group.
Each options menu offers docking, floating, rotation while floating, and all
commands when space is tight. Auto in Run / output and its overflow menu controls
the original evaluation checkbox. Arrow keys navigate controls; placement menus
also work without dragging. dnd-kit owns drag sensors and accessibility, Radix
owns toolbar/menu/tooltip behavior, and a small shell owns strip placement. These
dependencies are MIT-licensed; their added runtime dependencies use MIT or 0BSD.

Panel layout uses `scansor.flexlayout.workspace.v1` and toolbar placements use
`scansor.toolbars.v1`, independently of action recipes. Existing saved panel
layouts preserve placement and migrate weight-only auxiliary sizes to native
FlexLayout preferences before omitted workflow panels release their space.
Reset layout restores
both the panels and all five toolbar placements. No URL parameter is required.

The viewer currently uses the `feat/preferred-panel-sizes` branch from
[FlexLayout PR #530](https://github.com/caplin/FlexLayout/pull/530), pinned to
`6b947d0261d95800a9d1c10378bac3639c1b0d3c` in the dependency and lockfile.
FlexLayout's native preferred sizes keep auxiliary panels at their chosen sizes
while Model absorbs available space changes. Scansor assigns preferences at
docking boundaries; it no longer corrects weights or intercepts window resizing.
The branch is installed from its source archive. `npm run check` and
`npm run build` first compile its runtime using the viewer's existing Vite
dependency, so `npm ci --ignore-scripts` remains supported.

Strips can also dock at any **tabset's outer edge**, outside its tab bar and content.
Drag near that local edge, or choose a **Docking host** in the options menu, then
choose an edge. It reserves space around the whole tab area and stays with the
tabset across tab switches, splits, resizing, and floating; it does not follow an
individual tab that leaves the group. A hidden host temporarily hides its strip;
**Show panel → Create** reveals the host again. Removing the host or shrinking it
too small for the grip/options controls returns the strip to a workspace edge. Local strips
share their host's stacking layer, so other floating panels can cover them.
While dragging, blue workspace targets occupy the outer gutter or existing
global rails. Green tabset targets sit inside those bands, so the two meanings
of a shared perimeter do not overlap. A labeled outline shows the proposed strip
position before release; Escape discards both placement and order changes.
Markers stop short of the corners rather than competing there. Corner areas have
no docking target; releasing in one restores the committed placement and order.

Build faces, Relationships, and the other creation tools use the same Edit host.
Its float control moves it within the browser tab; form values, review choices,
and previews survive rearrangement. The panel close button follows the same
draft-discard guard as the form's Cancel/Done button.
When a docked panel closes or floats, or the browser window resizes, the model
absorbs the space change while other docked panels retain their chosen sizes
where the layout permits. Splitters remain adjustable.
Confirmation, example-choice, rename, and export dialogs remain modal.
Separate browser-window popouts
are disabled in this slice because the existing tools and renderer share a single
document.

Geometry input fields start picking when clicked. Single inputs show their
selected value; multiple inputs show selected rows with remove buttons and a
minimum two-row height. Their dropdown arrows show all eligible choices, with
checkmarks for selected items. Single choices close the dropdown; multiple
choices toggle selection and keep it open. Unavailable choices are excluded from
the dropdown, while unavailable saved selections remain visible for repair.
Creation tools begin picking at the first required empty geometry input when
eligible choices are available; inspecting an existing feature does not.
This also applies to Build faces surfaces/neighbors and relationship participants;
their detailed neighbor/participant controls remain available in the dropdown.
Hover previews the fit/boundary; an overlapping patch opens an icon-and-label
chooser instead of guessing. Picking uses the current complete fit output and
input eligibility rules. Dropdown search does not restrict picks in the model.
Target picking ends after a successful choice; neighbor/participant picking
continues until **Stop picking**, Escape, or closing the workflow. Outside this
mode, clicking retained regions keeps its existing behavior, and painting and
right/middle-button navigation remain unchanged. Geometry inputs in feature
editors and creation dialogs use scan patches
for selections/fits and visible guides for points, axes, planes, frames,
intersections, constructed faces and bodies. While an input is being picked,
eligible feature-tree clicks fill that input rather than changing the inspected
feature. Single-input picking finishes immediately; multi-input picking toggles
items until **Done picking** or Escape. Modal forms stay visible beside the
viewport during picking and return to modal interaction afterward, with their
draft intact. Picking runs the same input change handler as the dropdown and
does not apply the form.

In Faces only mode, identified source-face previews map a click back to a face,
its fitted surface or containing body. Source-face previews are used even when a
sewn body replaces them in the display; they are pick proxies, not changes to the
body or exported topology. Options without evaluated geometric output remain
list-only; arbitrary infinite carriers and metadata are not click targets.

Layout changes are saved automatically under `scansor.flexlayout.workspace.v1`,
separate from action recipes and mockup preferences. Reload restores panel
placement, but does not reopen workflow drafts. **Show panel** selects a panel
or starts the selected workflow; **Reset layout** restores default placement
without changing actions, form drafts, or the camera. If browser storage is
unavailable, docking remains usable with a visible persistence warning. Layout
changes do not request fitting or graph evaluation.

Checks: `npm run check --prefix experiments/browser_viewer` and `npm run build
--prefix experiments/browser_viewer`. Real-browser checks run in a fresh context
against an isolated adapter on 8767: `BROWSER_CHANNEL=chrome npm run test:browser
--prefix experiments/browser_viewer` when host Chrome is available. They do not
connect to or mutate the user's server on 8765. Without that environment variable,
install Playwright's Chromium first.

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

Action and group headers have reorder grips supporting drag-and-drop or Up/Down
keys. Grip dragging auto-scrolls near the top or bottom of the feature panel;
wheel/trackpad scrolling remains available while the drag is held. The insertion
marker follows the newly revealed rows, and Escape cancels without reordering.
Moving an organizational group includes its member actions and their
generated outputs; moving a managed owner such as Build faces includes its whole
generated subtree. Generated subgroups move only among siblings within their
owner, and individual generated actions remain locked. Moves preserve membership
and ownership and are rejected when an input would come after its consumer.
Empty organizational groups have no action position and cannot be reordered.

The action list retains authoring order, but evaluation first compiles a complete
execution graph. Connected axis/point solves and connected plane relationships
wait for all of their inputs, including fits added later through another
relationship. Frames, scales, reusable regions, and physical boundaries wait for
their resolved geometry providers. Connected plane groups are solved once and
publish every relationship result together. Targeted evaluation selects a
dependency closure from this compiled graph; it does not execute the list as a
sequence of provisional results.

Plane relationships connected through shared axes, reference planes, or radius
equalities are fitted together with their lateral and datum observations. The
joint solve preserves perpendicular/parallel/contains-axis constructions and
declared coincidence as hard equalities; it publishes axes, datums, fits, and
updated residuals together. Feasibility and tangent stationarity are checked
before results become ready. Incompatible fixed geometry fails instead of
silently averaging its directions. Changed carriers can invalidate a previously
reviewed face-region choice; that face must be reviewed again before export.

Preliminary growth fits and datum initializers remain separate from resolved
geometry. Feature-reuse membership transfer uses source geometry before radius
or plane relationships that depend on the transferred fits; those final
relationships cannot feed back into the transfer that created their inputs.
If a final group extends an existing source-only group, a reuse-local solve
retains those original source constraints rather than dropping the whole group.
Genuine resolved-geometry feedback cycles
are rejected before fitting, with the involved action names. Switching between
automatic axis resolution and an explicit joint invalidates cached resolved
consumers; raw initializer values are retained. This remains the bounded
adapter's request-local joint precedence, not a general solve-activation contract.

The Features panel contains the tree. Parameters, information, and results appear
in the optional Edit tab or popup during an explicit edit or inspection session.
Selection editing offers painting controls for the selection being edited.
Save actions, Load actions and Restore example are in
the project header. Display settings sit beside Navigation over the viewport;
general status and errors appear in the footer. **Show reuse volumes** displays
all currently evaluated generated reuse-selection envelopes as translucent
target-colored overlays. It is off by default and is display state only; the
surface-normal acceptance test still determines which vertices inside an
envelope become selected.
Features and Edit can be arranged independently. Their placement is remembered
in browser storage when available, and both panels retain their own scrollbars.

**Model** and the read-only **Graph** are separate FlexLayout tabs. They start in
one tabset, but can be docked side by side or floated independently. The feature
tree and optional Edit workflow remain available in both. Graph selection is
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
Fit rows also show their current resolved weighted RMS. Hover for the worst
absolute residual, reference bindings, available condition estimate, and any
reported solve context. A ready check means evaluated, not acceptable fit error.
Reused fits use their own target observations; reuse and organizational headers
summarize the worst descendant fit RMS and number above the limit, not the rigid
matching score. Summaries with missing or stale members are marked incomplete.

Set **RMS limit** above the tree to color values above it amber and those within
it green. The positive limit uses source-scan length units, is stored per source
scan in this browser, and starts unset. Blank disables quality colors. This is
an RMS limit, not a per-point maximum-error limit; output scaling does not change
the displayed residual values. Stale, failed, or unavailable fits show `RMS —`.

Clicking an action toggles it in a persistent feature-selection set without a
modifier key. One selected action exposes its settings and result; multiple
selected actions show an operand summary for **Relationship…**. Click a selected
action again, click empty tree background, choose **Clear**, or press Escape to
remove selections. Drag an action's grip to insert it before or after another
row; the insertion line turns red for invalid dependency orders. Focus a feature
and use the Up/Down keys to move the single selection and keyboard focus without
scrolling the panel; focus its grip to use the same keys to reorder it.
Right-click a selected feature to delete the current multi-selection (or use
Shift+F10 / the Context Menu key). Right-clicking an unselected feature selects
only that feature. Review the deletion before confirming: generated outputs are
removed with their owner, and any additional dependent features require an
explicit checkbox approval. Generated features cannot be deleted separately
from their owner, and the source mesh is protected. Organizational groups and
surviving feature order are preserved.
Reordering requires every input to still
appear earlier and waits until evaluation or selection editing finishes.
Stable IDs preserve references when positions change.
A valid reorder preserves cached results. This is recipe order, not edit history.

Automatic evaluation is enabled by default and evaluates the loaded graph plus
subsequent edits; it can be disabled from **Run → Auto**. A manually initialized
axis is previewed immediately because its value is already known. **Evaluate action**
evaluates the selected action and only the earlier inputs it requires;
**Evaluate all** explicitly evaluates every stale or unevaluated action and
retries failed actions. Auto, face review, Continue, and export instead request
current outputs from the backend graph. It checks the full required dependency
closure and solve context, reuses current results, and joins active work before
rechecking the requested outputs. Unchanged failures remain visible until an
explicit retry or an input edit; unrelated failures do not block a targeted
request.

Evaluation polling uses a publication revision independent of the recipe's
content token. A matching `/api/graph?revision=N` returns only the token,
revision, unchanged flag, and current worker lifecycle/error metadata; it skips
geometry copying and encoding, and UI rebuilding. Changed snapshots still
publish action states and results during the job. Completion always rechecks
the caller's required outputs through `/api/graph/ensure`.

From the repository root, reproduce the polling measurements with:

```sh
OPENBLAS_NUM_THREADS=2 uv run python -m experiments.graph_polling_benchmark
```

Example measurements on Python 3.12, five repetitions (median snapshot time
while holding the graph lock; lock acquisition and JSON encoding excluded):

| Case | Full response bytes | Unchanged bytes | Full snapshot ms | Unchanged snapshot ms |
| --- | ---: | ---: | ---: | ---: |
| Retained nozzle, 15 actions | 74,193 | 140 | 2.626 | 0.0016 |
| Retained boss fitting recipe, 68 actions | 534,865 | 141 | 32.381 | 0.0009 |
| Synthetic preview, 1,000 vertices | 32,169 | 139 | 5.259 | 0.0017 |
| Synthetic preview, 10,000 vertices | 356,167 | 139 | 30.296 | 0.0010 |
| Synthetic preview, 100,000 vertices | 3,956,165 | 139 | 399.897 | 0.0013 |

Both retained recipes evaluated fully ready before measurement. The boss case
regenerates the deterministic coarse fixture and uses its saved selection
memberships; synthetic previews are injected into disposable graphs. These
measurements exclude fitting, fixture generation, network latency, and UI time.
Timings are illustrative; regressions assert skipped copies and bounded payloads
without timing thresholds.

Failures do not stop independent branches. Dependent actions are marked
**Blocked**, with the failed upstream features identified; obsolete dependent
results are discarded. Generator/group status includes its generated outputs,
so a ready generator cannot hide a failed face. Repairing an upstream feature
makes its dependents eligible for evaluation again. The
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
graph and ensures current outputs after creation, property, selection, reorder,
delete, load, or reset changes. **Show all available fits and references** keeps
available guides visible together; disabling it restores selected-action-only
display. The selected action's evaluation and proposal controls remain in its
properties pane. **Relationship…** opens a compact, nonmodal side panel,
prefilled from eligible selected features. Choose the relationship kind, then
use its searchable icon list to select physical fits (or reference planes when
needed). Selected fits retain cyan support outlines on their complete evaluated
surfaces; hover or keyboard focus inspects a participant in yellow. Sphere fits
use analytic sphere guides, and reference planes use datum outlines. **Focus**
frames one participant; **Focus selected** frames the set. These are display-only
helpers, not a preview of newly constrained geometry, and do not trigger a solve.
Changing kind retains incompatible selected participants so they can be removed
explicitly. **Options** contains the name. The model remains rotatable while the
panel is open; closing it clears the helper highlights.

1. **Selection** creates an empty selection independently of any fit. Paint
   it, rename it, and add more selections as needed.
2. **Surface fit** chooses cone, cylinder, plane or sphere and one or more earlier
   selections. Overlapping input memberships count each source vertex once.
   Evaluate the fit to inspect its own parameters, guide and residuals. Plane
   fits can have independent orientations and do not require a joint solve.
   Standalone sphere fits resolve an independent center and radius. A sphere may
   instead reference a point datum. Sphere selection growth and exact STEP
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
   also be constructed through two point outputs, directed from the first toward
   the second. Every axis has an explicit **Flip positive direction** setting
   and is drawn with an arrowhead. Point-pair axes support arbitrary directions,
   including horizontal ones; in this bounded slice they are datum-only and do
   not yet drive surface fits.
5. **Plane** creates an explicit reference plane from an earlier axis. Choose
   **Contains axis**, **Parallel to axis**, or **Perpendicular to axis**. The
   first two use a clocking angle; a parallel plane also has a signed normal
   offset. On a manually initialized free axis, connected plane fits refine the
   clocking angle as well as any free offset, preserving the exact axis relation.
   The entered clocking is an initializer, not a lock. A bare datum without
   connected fitting evidence retains its entered values; a reference on a
   fixed, source-derived axis retains the existing fixed-orientation behavior.
   A perpendicular plane has an axial offset from the axis initializer's
   point at local Z=0. The plane is previewed immediately and remains a
   separately selectable feature. That axis point is a bounded prototype
   convention; the generalized model should use an explicit point or frame.
6. **Frame** defines an explicit right-handed coordinate frame. Choose a point
   output as its origin and map two nonparallel axis, reference-plane, or fitted-
   plane directions to two distinct signed output axes such as `+Z` and `+X`.
   The secondary direction is projected perpendicular to the primary direction;
   the third axis is derived by the right-hand rule. Parallel references and
   conflicting output-axis choices are rejected.
7. **Scale** derives one uniform scale from one or more known distances between
   point outputs. Its least-squares result reports every measured and scaled
   distance and residual, plus weighted RMS and worst absolute residual. With
   multiple distances, weights determine their relative influence.
   These point readers share the experimental backend input contract: a datum
   point, the center of a sphere fit, or exactly one selected scan vertex can
   supply a point. A selection with several vertices never implies a centroid.
   The list and model picker offer the same named outputs; point picking
   highlights the point itself, rather than its supporting observations.
   Frame directions similarly use named axis and plane outputs. New references
   save the feature, output name, and exact publication context. An unavailable
   saved output remains identifiable and is not silently replaced by another
   solve. Candidate discovery does not evaluate fits.
   This is a bounded, internal authoring contract in
   `experiments.feature_inputs`, not a public schema or a migration of every
   input. Geometry readers and adjustable fitting inputs remain distinct:
   supplying a sphere center does not make the sphere an adjustable point datum,
   and a plane geometry output does not make its provider a valid participant
   in every fitting relationship. Existing datum-ID recipes remain readable.
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
   selection for every target and source fit input selection. Each occurrence
   gets its own axes, reference planes, fits, and enclosed fitting relationships;
   internal references are remapped to that occurrence. Datum initialization
   follows the rigid match, then the existing solvers refine the target evidence.
   Occurrences retain independent poses. **Equal
   corresponding dimensions** adds a visible managed **All equal radii**
   relationship for each reused source cylinder, including the source fit and
   every generated copy. The same ordinary relationship can be authored manually
   by selecting the corresponding source and generated cylinder fits, opening
   **Relationship…**, and choosing **Equal radii**. The shared-radius result is
   exact while each cylinder retains its independently fitted axis. Paint an
   asymmetric cue when possible; rings or cylinders alone cannot determine
   clocking. This slice stays on one mesh and supports the existing axis/plane
   and fitting-relationship operations, not arbitrary modeling graphs. Point
   datums and point-pair axes are rejected explicitly. Include a datum's seed fit
   in the reused fits when it supplies an initializer. Reopening and applying a
   reuse action updates its generated graph while retaining existing fit IDs;
   faces whose saved region choices no longer match corrected geometry require
   re-review rather than silently selecting a replacement cell.
   Creation, edits, and the all-equal shortcut use the experimental Python
   authoring layer in `experiments.feature_reuse_authoring`. Its pure
   `reconcile_feature_reuse` transform returns a recipe and identity metadata;
   token-bound preview/apply HTTP adapters validate against the current graph.
   A request's allocation seed makes preview and apply allocate the same new
   IDs. Loading or evaluating a recipe does not reconcile its managed children.
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

### Fitting support and numerical diagnostics

Axial intervals serve selection/growth and display/export support, not implicit
physical-edge constraints on the fixed observation factors. Cylinder and cone
side factors evaluate the analytic surface on every selected observation with
its original weight; a fitted-axis change does not silently remove a row or make
an outside-support projection a line-search failure. Standalone and shared-axis
fits use the same distinction. Cone radii at the reference and declared endpoints
must remain positive, and undefined radial derivatives/apex-crossing projections
remain invalid geometry. Growth continues to classify candidates against support.
Standalone results report inside/outside support counts separately from residuals.

The experimental nonlinear fitting paths now share centered/scaled coordinates
and an SVD-based least-squares engine with adaptive damping. Diagnostics report
scaled rank, condition, projected gradient, termination, iterations, and the
numerical frame. Rank deficiency, invalid initialization, exhausted geometry
trials, numerical failure, nonstationary stagnation, and iteration limits remain
distinct failures. First-order local convergence is not evidence of unique/global
recovery, appropriate correspondence, uncertainty, or physical accuracy.

Constrained-solver failures retain their code and available solver record under
the failed action's graph `diagnostics`, including each failed connected-solve
output and failed prior-geometry transfer. These records have kind
`constrained_solver_failure`; `solver` is `null` when failure occurs before a
solver record is available. Nonfinite diagnostic numbers are transported as the
strings `"Infinity"`, `"-Infinity"`, or `"NaN"`, including within parameter and
objective-history arrays. Last-iterate parameters are failure evidence only;
they are never published as ready geometry. Downstream actions retain their
separate `blocked_by` causes. Readiness preserves failure records until a
successful explicit retry or an edit invalidates them.

No finite-edge/cap distance objective or general nonlinear-constrained optimum is
implied. The positive-Z axis chart and cone positive-reference-radius restriction
remain explicit limits; see the [cone fitting experiment](../mesh-cone-fit/README.md).

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
For CAD export, observations from all three selections are rotated into the
first copy’s frame to bound one patch; that same patch is then rotated to each
copy. This gives matching rectangular plane patches and matching cylinder/cone
spans. Disable the option in the symmetry action’s properties for independent
bounds. It changes exported extents, not selection membership or the fitting
objective; viewport guides still use their existing bounds.

## Explicit physical face boundaries

**Implemented bounded experiment.** The **Faces** toolbar offers **Surface
intersection** and **Trimmed face**. These declarations describe physical output
regions, not selection volumes, fitting support, or an output coordinate Frame.

1. Create a surface intersection by choosing two planes, or a plane and a
   cylinder or cone. Perpendicular revolution cuts compute circles. General
   single-branch cuts compute exact lines or ellipses through OCP, not circular
   approximations. Zero, multiple, open-conic, and near-degenerate branches
   require further review and are not silently substituted for another edge.
2. Create a trimmed face, choosing its underlying surface and adding shared
   intersection features. For a plane, keep inside or outside each closed
   circle/ellipse, or choose the positive/negative side of intersecting planes.
   Cylinder/cone cutting-plane choices also follow the plane's oriented normal.
3. Edit those declarations in the properties panel. Boundaries recompute after
   upstream changes; no scan IDs, weights, residual rows, or fitting support are
   changed by a trim.

For the original boss in the retained demo, intersect **shoulder fit** with
**outer fit** and separately with **bore fit**. Create a face on **shoulder fit**,
keeping inside the outer circle and outside the bore circle. This represents the
ideal annular portion only. Include **clock fit** in **Build faces** to partition
the shoulder and outer wall at the flat using native curve arrangements.
Reused standalone fits may need exact
relationships when a circular edge is intended.

Surface references identify both the feature and, for an explicit joint or
relationship result, its member fit. A face and its boundary must use exactly
the same reference: independently fitted and jointly solved geometry cannot be
silently substituted. Direct geometry on an axis with an explicit shared-axis
joint is ambiguous under the existing evaluator's context rules and is rejected;
choose the member in that named joint. Referencing its solved datum members is
not implemented yet.

Manual boundary declarations retain the simpler circle/ellipse and convex
perimeter path. Batch construction partitions planes, cylinders, and positive
cone sheets using native arrangements. Crossing curves become finite oriented
arcs and lines; a circular shoulder clipped by a flat can therefore form a
D-shaped region. Native wires also represent separated or intersecting openings,
and cylinder/cone cells handle periodic seams. This is geometric partitioning,
not automatic knowledge of material, adjacency, or missing scan coverage.

General construction uses normalized local coordinates, native validity and
classification, and exact boundary provenance. Finite computational carriers
exist only to display otherwise infinite surfaces. Native split history tags
their surviving boundary edges as artificial: such cells remain open and
bounded-face export refuses them. Earlier interval faces likewise retain null
physical endpoints. Observation coverage and native preview meshes are never
physical caps or fitting evidence.

### Guided and batch face building

**Faces → Build faces…** offers a guided single-surface review as well as the
existing batch review. Neither mode infers a complete solid from scan evidence.

In guided mode, choose one target fitted surface, then include the neighboring
surfaces that should bound it. Candidate neighbors are ranked using previous
approval and scan proximity. The ranking is guidance, not proof of physical
adjacency; distant and uncertain candidates remain visible. Conflicting previous
decisions require review. Only the target is partitioned, and only its retained
regions are created. Neighboring surfaces are not trimmed automatically.

The compact panel keeps Preview, Apply, and Cancel visible. Hover or keyboard-focus
a neighbor to highlight its intersection; checked neighbors stay highlighted.
Inspected edges are yellow and selected boundaries are blue, with fixed-pixel
strokes and dark outlines that remain visible over transparent previews.
Expand a neighbor row for geometry context, scan gap, and approved-edge controls.
Surface labels name plane, cylinder, and cone fits;
neighbor rows and every option in the surface picker reuse the feature-tree
icons. Build faces offers each physical plane, cylinder, or cone fit once, using
its current resolved geometry or its sole completed explicit joint output.
Reference datums and alternate solve contexts are not choices in this workflow.
Conflicting joint outputs are reported as unavailable rather than chosen
arbitrarily. Existing face actions retain their exact geometry dependencies.
Review cannot preview an existing action while saved inputs are unavailable;
it never silently removes those inputs and their generated faces.
Open guided and batch surface lists follow accepted fit additions, renames,
deletions, and readiness changes while retaining draft names, selections, and
review settings. Unavailable draft inputs remain explicit and block preview;
they are never silently replaced with another geometry context. Unchanged
progress snapshots preserve the picker, focus, and draft controls.
A second line names only the fit type. The picker
supports arrow keys, Home/End, name typeahead, Enter to choose, and Escape to cancel.
Hovering or keyboard-browsing the surface picker highlights a cyan coverage
outline on that fitted surface, enclosing the projected fitting observations.
With a mesh, the outline follows its selected patch, including concavities and
holes. In partly selected triangles it runs halfway along edges between selected
and unselected nodes. The resulting loops are projected onto the fitted surface.
Mesh connectivity is indexed once, and each fit's outline is cached for browsing.
Without usable connectivity, the fallback is a convex planar/unwrapped outline
or two rings for broad periodic coverage. This display-only approximation never
declares physical face bounds or changes fitting selections. The committed
target's outline remains visible when reviewing neighbors, with a thinner,
muted cyan stroke below the stronger physical-edge highlights, instead of
adding another selected-node highlight. Both stroke and contrast halo use full
opacity so overlapping segment ends do not brighten the joins.
Without approved finite-edge guidance, infinite-line neighbor previews span the
primary fit's observations projected onto the intersection line. This only
positions the display segment; it does not declare physical endpoints.
An axis-parallel secant plane/cylinder pair previews both native generator lines,
each bounded for display by those observations. This does not choose a branch,
create standalone edges, or upgrade uncertain tangencies to supported cuts.
Defined faces remain visible as subdued fills and thin boundaries throughout
guided review, including after continuing to the next surface. Continue only
offers neighboring surfaces without an applied face; existing faces remain
editable through the surface picker. This is an authoring shortcut, not a
surface-coverage check. Continue waits for an evaluation already in progress
and reuses ready results; when necessary it targets the applied Build faces
owner and its generated children instead of reevaluating all actions.
Continue buttons highlight the prospective surface's
fitting footprint on hover or keyboard
focus, without moving the camera or changing the current target. Just-applied
faces retain their accepted preview until evaluation replaces it; changed,
deleted, or failed outputs never retain that temporary display.
**Display → Faces only** shows all ready created faces with opaque shading and
their boundaries, hiding the scan mesh, vertex markers, fit/reference guides,
and reuse volumes. Other display preferences are restored when it is turned off;
interactive face-review highlights remain available.
Filter neighbors by name or type. Focus explicitly
frames the target; inspection and
selection do not move the camera. Unrelated geometry is dimmed during guided
review and restored when the panel closes. After preview, neighbors fold away to
show the region list. Naming, mode, and declared scopes are under Options.

Preview the target regions, then hover to inspect and click to toggle a region
on the model. **Add scan-supported** adds the safely suggested regions to the
current choices; **Clear** resets them. Both require explicit review and Apply.
Only bounded regions are offered in the list or model picking, restored as
choices, or included in scan-supported suggestions. Open cells remain in backend
evidence accounting; an open-only result asks for more boundary surfaces.
Large lists fold under **Individual regions**, with checkboxes as an alternative
to model picking. Model and list inspection use yellow; retained boundaries stay
blue. New guided reviews do not accept regions merely because they contain
observations. Right/middle navigation remains available, and guided
left-clicks never paint or alter scan selections. Changing the target or chosen
boundaries invalidates the preview; uncommitted choices require an explicit
discard before switching reviews.

When reviewing a neighbor later, approved adjacency and existing finite boundary
arcs are offered as guidance. Explicitly honoring an approved source face checks
the selected regions against its shared boundary segments before applying.
For new arrangement proposals, a bounded approved neighboring face also supplies
a finite cutting tool. A completed cylinder wall and clock flat can thus close a
boss footprint without extending the clock-plane cut across the whole plate.
The plate exterior can remain one connected region with multiple holes. Open
source faces retain whole-surface cuts and agreement checks; they do not supply
invented finite caps. Existing face declarations retain their previous cutting
semantics until explicitly reviewed and updated.
Source faces remain graph dependencies, so changing them invalidates dependent
faces. This is geometric boundary agreement, not shared native BRep identity or
sewing. If an older face needs revision, its edge guidance can be unchecked;
that does not silently repair or change the old face. Open ends and unresolved
neighbors remain explicit, and no solid is claimed.

For the first boss, start with **shoulder fit** and include **outer fit**, **bore
fit**, and **clock fit**. Retain the D-shaped shoulder annulus. Then review
**outer fit**, including the shoulder, clock flat, and plate top. Its approved
shoulder arc supplies a shared-boundary check, not its remaining height or
material-side decision.

In batch mode, select adjoining plane/cylinder/cone surface references,
then **Preview**. **Add all fitted surfaces** extends the current selection with
available plane/cylinder/cone fits, excluding construction/reference planes.
It prefers direct geometry, preserves already-selected contexts, and uses a named
solve context only when unambiguous; otherwise choose the context manually.
There is no fixed surface-count cap. Preview work is bounded to one million pair
checks and 10,000 generated regions; oversized work is rejected rather than
silently truncating surfaces or proposals. Recipes allow 10,000 actions so
reviewed multi-cell output does not hit the former 100-action ceiling.
Supported intersections partition each surface into candidate
regions. The original selected observations suggest a region only when their
strict-interior evidence occupies candidate cells, projections are defined and
accounted for, and no unresolved cuts remain. The interval path suggests a
single region; the arrangement path can suggest several disconnected patches.
Boundary-only, undefined, uncovered, or absent evidence requires explicit review.
These suggestions do not prove adjacency or scan coverage;
all observations, fitting weights, and residuals remain unchanged.

The adjacency table separates mathematical intersections from physical neighbours.
Confirm or reject candidate pairs explicitly. Proven-empty pairs are rejected;
unsupported unreviewed pairs require review but do not declare both surfaces
unusable. A confirmed boundary that cannot be constructed blocks the affected
face. Accepting a region confirms its boundary pairs. Decisions persist on the
Build faces action and are restored by **Review / update**.

Each surface can optionally be scoped to a union of existing physical faces in
that exact geometry context. Native arrangement cells are clipped to those
declared domains; interval scopes preserve any open endpoints as open.
An open arranged face cannot yet serve as a scope: declare its closing physical
bounds first. Near-degenerate or numerically ambiguous topology is diagnosed,
not healed into a guessed region.
Leave the scope empty to consider the whole fitted surface. Observation bounds
and gaps are review hints, never proof that surfaces are physically separate.

Review the retained-region choices, evidence, warnings, and colored model
overlays before **Apply faces**. Open regions are not face-building choices;
declare their physical boundaries first. Unsupported preview regions cannot be
applied. For the original boss, select **shoulder fit**, **outer fit**, **bore
fit**, and **plate top fit**; review the shoulder annulus and lateral intervals.
Include **clock fit** for the flat. For the whole boss example, include perimeter
planes as well as boss surfaces to define the plate; holes alone do not close it.
Review individual region checkboxes, or **Add scan-supported**. Other
unpopulated candidates remain available under a folded section rather than
disappearing. Scan noise can populate cells outside an intended perimeter: those
observations remain accounted for, but open cells are omitted from review choices.

Arranged faces persist cutting references, scope references, and a reviewed
cell selector, not a frozen BRep or a native enumeration index. Re-evaluation
checks cutter signs, connected-component multiplicity, and the declared intrinsic
witness. Finite cutters additionally record locally incident physical-boundary
sides rather than imposing infinite halfspaces. A boundary crossing or changed
component topology asks for renewed
review instead of silently attaching the action to a different patch. Preview,
Apply, and evaluation each share exact-input native arrangements across sibling
cells and recursive boundary checks. The cache includes geometry, scope,
observations, and nested selectors; each cell's selector is still validated on
every replay. Cache references are discarded after the pass (including failures)
and are not shared across threads or later evaluations.
An arranged face used as a finite cutter or physical scope replays in its own
normalized frame. Independent native copies transform directly into the consuming
arrangement's frame, retaining source precision without rebuilding nested
arrangements for each frame. Manual face declarations still recenter before
native construction to avoid large-coordinate round trips.

Separately, the graph retains successful shared-boundary validation summaries
across requests. Their fingerprints include exact resolved analytic geometry,
face identity and selectors, source boundaries, retained sides, and whether the
check requires complete coverage. Changed inputs invalidate the review; unrelated
edits preserve it. These entries contain no native shapes, and validation from an
outdated graph epoch cannot publish into the current graph.
Evaluation copies only the physical face records needed by each owner's review.
Body readiness checks likewise read only the relevant states and owner summaries;
neither operation makes a full graph snapshot per owner or body. The returned
snapshot remains an isolated copy of the complete results.

Face-equivalence checks copy validated arrangement cells directly from their
canonical local frames into one pair-normalized frame, without resplitting the
arrangement or a lossy round trip through world coordinates. Manual faces still
rebuild from recentered declarations. Wire topology and both native Boolean
differences remain authoritative; area or preview similarity cannot prove reuse.

Apply creates a persistent **Build faces** action with ordinary, inspectable
intersection/face children. Equivalent existing faces and intersections are
reused unchanged, without taking ownership. **Review / update** on the owning
action updates only its generated outputs. Replacing one owned face with one
reviewed region on the same exact surface retains its ID and label, even when
the region key changes; downstream references remain attached and become stale.
Unchanged region matches keep their IDs. Splits and merges do not infer an
ambiguous correspondence; removal is refused if another action consumes an
obsolete output. External/reused faces are never reassigned. Saved recipes
replay the explicit boundaries, not a fresh automatic region guess. New batch
actions preserve an existing output Transform. A changed recipe or resolved
geometry requires another preview before applying.

Face building itself does not sew faces or construct solids. Shared edges are
declarations in the Scansor graph; face collection exports remain independent.
An explicit Body action performs the separate assembly/validation step described
below. Automatic adjacency, arbitrary intersection branches, and fillets/chamfers
remain deferred.

This bounded slice uses OCP for intersections, face construction, validation,
tessellation, and CAD export. Licensing was explicitly accepted. The swap does
not add general intersection branches or new retained-region types. The subsequent
Body slice adds conservative sewing/solid validation. Scansor still owns fit
charts, explicit physical intent, review evidence,
graph identity, and conservative disjointness/uncertainty policy.

Display tessellation retries unsuccessful native meshes on fresh display copies,
first with absolute and then edge-relative deflection. This does not heal faces
or change their physical tolerances. Previews remain approximations within native
topology uncertainty; unsuccessful or empty triangulations remain explicit errors.

## Experimental Body assembly

Choose **Body…** in Combine, select the physical faces, and create the action.
Selected face entries or Build faces groups seed the list; otherwise all built
faces are offered. Failed, stale, or open inputs are not silently omitted.
The Body waits for its face dependencies and owning coverage reviews, then joins
boundaries within its explicit sewing tolerance, in local model coordinates.
The dialog proposes one millionth of the scan bounding-box diagonal (at least
`1e-7`) as an editable starting value; the direct recipe schema defaults to
`1e-7`. Assembly never increases this value. If existing kernel face tolerances
exceed it, the diagnostic reports the minimum required value instead.
Membership and tolerance remain editable in the feature tree.

Only one connected, closed, consistently oriented manifold shell with valid
topology, no detected self-intersections, and finite positive volume becomes a
ready Body. Input/output topology tolerances cannot exceed the declared sewing
tolerance. Failed assemblies retain inspectable diagnostics, named source faces,
and red boundary highlights. Sewing never invents caps, enlarges the declared
tolerance, or implicitly unions overlapping geometry. Multiple disconnected
bodies and nested-shell cavities are rejected in this first slice; through-holes
within one connected shell are supported. Native validation remains an
experimental kernel check, not a guarantee against every pathological geometry.

Successful Body results use the graph's normal readiness/invalidation system;
changing a face invalidates its Body. **Faces only** displays the assembled body
instead of duplicating its constituent faces. To export a solid, select the Body
and choose **Export CAD… → Body solid**. Export replays the physical declarations
and checks the assembly again; it does not refit the scan or rerun current graph
actions.

## Experimental STEP export

**Export CAD…** defaults to **All built faces**. **Selected faces** exports
selected face entries or the managed/reused faces of selected **Build faces**
groups, deduplicated in recipe order. Neither scope silently drops unavailable
or open faces: repair them, or explicitly choose a smaller selection. Required
owner coverage reviews must also be current. **Fit or joint** retains the
standalone fit and joint patch export workflow. Selecting a Body defaults the
dialog to **Body solid**, which exports one validated solid instead of separate
faces.

Face collections use one chosen Transform (defaulting to the active model-view
Transform) or the original scan coordinates, plus one explicit unit choice.
They do not derive placement from an arbitrary first face. Face collections go into
one `model.step` as separate named faces, without sewing or solid construction.
The ZIP bundle's `metadata.json` preserves the recipe, export
settings, source identity, and boundary declarations (plus source-face membership,
assembly tolerance and local volume for a Body), and the optional reference
mesh is a separate double-precision PLY. The files share output coordinates and
units; the scan mesh is never converted into CAD faces. Rhino `.3dm` output and
the `rhino3dm` dependency have been removed, without a compatibility path.

The adapter uses [OCP](https://github.com/CadQuery/OCP), pinned through
`cadquery-ocp-novtk`; the no-VTK wheel avoids an unused visualization dependency.
The wrapper is Apache-2.0; underlying
[OCCT is LGPL-2.1 with an additional exception](https://www.occt3d.com/dev/doc/overview/html/occt_public_license.html),
explicitly accepted by the user. This is a provisional experiment, not a general
CAD integration or production-stack commitment. Dependency updates require geometry/file
round-trip checks. Actual downstream CAD import remains a separate verification.

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
and legacy axis-up/origin placement are mutually exclusive. The export retains
the current recipe and coordinate transform in bundle metadata; it does not modify
the session or introduce change history.

Legacy fit exports still use observation-bounded patches, not the new face
declarations. Plane patches use projected rectangular selection bounds with 5% padding.
Cone/cylinder patches use full 360-degree sides over the selected axial span,
with 5% padding clipped to the declared support. Sphere fits export as complete
analytic spheres because a spherical trim footprint is not yet modeled. Plane
and lateral patches are independent open surfaces, not reconstructed trim
boundaries or a sewn solid. The reference mesh retains its vertices and triangle
connectivity, using double-precision storage; no decimation or conversion of
mesh triangles into CAD faces is performed.

Exporting a **Trimmed face** or **Arranged face** instead uses its explicit
physical limits, with no observation padding or implicit capping. Ordered planar
loops and holes, polygon vertices, and reviewed native arrangement cells are
reconstructed as analytic faces; the earlier circular cone bounds remain
supported. Source references, boundary uses, and physical loop/cut descriptors
are retained as metadata. No sewn shell or solid is claimed.

Local checks reopen the exported STEP and verify analytic surface recognition,
mesh coordinates and topology, unit metadata, common orientation, and rejection
of stale results. They do not establish downstream import support for the bundle
or replace an actual CAD application check.

Axis-up export also translates the fitted axis onto the Z axis. It preserves
rotated axial heights and applies the same rigid transform to the reference mesh
and every surface. Turning axis-up off retains original scan coordinates.

**Origin along axis** optionally chooses a plane from the exported fit or joint.
Its intersection with the axis becomes the export origin; joint planes use their
constrained geometry. A parallel plane is rejected because there is no unique
intersection. This option requires axis-up export.
