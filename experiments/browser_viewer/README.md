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

Choose a surface and rectangle operation: replace, add or remove. Rectangles
select **through** the mesh, including hidden vertices. Added vertices are moved
out of all other participating surfaces, maintaining disjoint memberships. Edits
update current
backend selection nodes. No undo stack, revision chain or change log is retained.
Restore example graph reloads the checked-in example recipe from disk.

The feature tree displays backend nodes, dependencies and evaluation status.
Select a surface-fit feature to edit its name, fit type, input selection and
axial domain, then apply its properties. Selecting a participating fit also
activates its selection for rectangle editing. Each fit owns its type; the joint
solve evaluates them together. This adapter supports one perpendicular plane and
a connected group of coaxial cones/cylinders. Incompatible type choices are
disabled with an explanation.
Shared inputs are referenced rather than copied into separate backend nodes.
Use **Add surface fit** to choose a cone/cylinder type and an existing surface
whose axis it should share. This creates a surface declaration, an empty selection
and an explicit coaxial constraint, then adds the constraint to the joint solve.
The new selection becomes active; use rectangle selection to supply observations.
Select the coaxial constraint in the tree to edit its reference surface. The
backend rejects references that disconnect the group. Initial axial support spans
the mesh's display-Z extent with a small margin; adjust it in fit properties to
bound the intended side. This is an initial guess, not detected segmentation.

Selection references and all relationships come from the recipe.
Changing a node invalidates its dependent results. Evaluate runs the declared
joint fit; all participating surfaces inform the shared axis, using whole-mesh incident-area
weights and fixed memberships. Unsupported combinations and poor geometry fail
visibly. A result from a graph edited during evaluation is discarded by the
backend, not merely hidden by the browser.

Side guides match their selection colors; pink guides show the perpendicular plane.
They show through the mesh for inspection; their display extents are not inferred
physical boundaries or uncertainty. Residual colors use independent symmetric
blue/white/red scales for each region, with limits shown in the UI. Units remain
unconfirmed, and lower training residual is not physical validation.

## Backend graph and current-recipe persistence

The backend DAG is implemented in `experiments/feature_graph.py`; the tree is a
frontend presentation. Supported nodes are source, selection, surface declaration,
perpendicular relationship, coaxial relationship and joint fit. Joint-fit records
reference a list of constraint IDs; older single-constraint recipes can still be
loaded. One joint-fit node evaluates the coupled
surfaces together; geometric constraints do not create execution cycles.

The multi-side solver in `experiments/mesh_coaxial_fit.py` shares four axis
parameters across all sides and the plane normal, with independent radius and
optional taper for each side and one plane offset. It minimizes the combined
area-weighted orthogonal residuals with analytic derivatives. The existing axis
is free to move; there is no fixed-axis mode. Arbitrary constraint networks,
multiple planes and arbitrary model families remain future work. The example's
local parameter frame and positive-radius finite-side support limits still apply.
Synthetic checks cover exact recovery, finite-difference derivatives, and an
added surface moving the original axis. A browser check uses a split of the saved
band selection to exercise multiple fits; that split is not a newly identified
physical feature or a claim of improved reconstruction.

Save graph downloads the **current recipe only**: schema version, nodes and output
reference. Source/reference hashes bind it to the captured mesh and adapter frame.
Load validates bindings, input types, dependencies and memberships before replacing
the current graph. It does not restore historical versions or cached fits.
Previous settings/results, event history, undo and persistent change logs are
explicitly out of scope. In-flight invalidation counters retain no previous state.

The example includes `recipes/cone-plane.json` and `recipes/cylinder-plane.json`.
The same backend used by the UI can execute either recipe without a browser:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.feature_graph \
  --recipe examples/nozzle-bayonette-simplified/recipes/cone-plane.json \
  --output local-inputs/nozzle-graph-fit.json
```

The output path must not already exist. This writes the current evaluated graph
and current result; it is an explicit export, not automatic historical storage.
The earlier fixed runners remain comparative experiment reproducers. New graph
workflows use declaration-driven evaluation instead. The captured-example adapter
still fixes geometry loading, local frame and initialization conventions; it is
not a general mesh/model solver or part of canonical synthetic-only admission.

The browser server accepts `--recipe PATH` to choose its initial graph. Its HTTP
adapter binds to loopback and serves a fixed route list, not arbitrary files:

| Route | Payload / response |
| --- | --- |
| GET `/api/meta` | Source counts, display frame and binary layouts |
| GET `/mesh/positions` | Little-endian float32 XYZ in the saved fit frame |
| GET `/mesh/indices` | Little-endian uint32 triangle triples, source order |
| GET `/api/graph` | Current recipe, token, statuses, errors and current output |
| POST `/api/graph` | Current token and replacement recipe; validate and invalidate |
| POST `/api/graph/evaluate` | Current token; start evaluation in one worker |
| GET `/api/graph/example` | Reload the checked-in cone example recipe |

POST requests require `Content-Type: application/json` and
`X-Scansor-Request: 1`; bodies are limited to 1 MB. Stale edit tokens are rejected
with 409. Old stateless `/api/session` and `/api/fit` endpoints remain available
for the original experiment checks; the graph UI does not use them. The local
server is not a hardened deployment service or stable public API.

Geometry stays resident; camera movement does not refetch it. Frames are requested
on changes rather than an idle animation loop. Submission time is CPU time, not
GPU completion or interactive FPS. This remains a small in-memory experiment,
without bounded-memory or large-model claims.

## Initial local observation

On Linux with Intel UHD 630 and Brave Chromium 153.0.8010.37, one isolated
1440×1000 browser instance measured approximately 269 MiB summed proportional
memory (PSS) with a blank tab and 347 MiB after loading/fitting this example.
Private memory (USS) was about 128/194 MiB respectively. Summed process RSS was
994/1113 MiB and counts some shared pages repeatedly. These are point-in-time
observations across nine browser processes, not peak usage or a native comparison;
they exclude the Python server and test driver. Browser launch selected
ANGLE/OpenGL on the Intel GPU. The UI stopped requesting frames while idle.

## Checks and follow-up

```sh
npm run check --prefix experiments/browser_viewer
OPENBLAS_NUM_THREADS=2 uv run --locked pytest \
  tests/test_feature_graph.py tests/test_nozzle_session.py tests/test_mesh_cone_plane_fit.py
```

Tests cover source/model binding, invalid IDs, disjoint selection edits, fitting
an edited membership, saved fit replay, binary geometry, origin/path boundaries,
and a fit worker that does not block reads or queue concurrent fits. The JavaScript
checks run in CI alongside the Python checks. Actual navigation, selection,
graph save/load and residual display were also exercised locally in Brave.

Before choosing a product frontend, assess visible-surface selection, larger
meshes/LOD, memory across browser processes, keyboard/touch accessibility,
multiple platform/GPU configurations, and a comparable native frontend. Preserve
canonical source IDs if future rendering paths reorder or simplify geometry.

When updating Three.js, check its changelog and migration notes, and repeat
navigation, selection-ID, visual and performance checks. The public controls
APIs and GPU behavior can change between revisions.

[onshape]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-rotation-with-an-upright-vertical-axis
