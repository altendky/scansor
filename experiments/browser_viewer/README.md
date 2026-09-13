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
out of the other surface, maintaining disjoint memberships. Undo retains up to
30 selection edits; Restore saved regions returns to the captured ID lists.

Fit cone + plane uses every selected observation with whole-mesh incident-area
weights and the existing exact shared-axis constraint. Invalid or insufficient
geometry fails visibly. Membership is fixed during fitting, with no residual
trimming. Editing selections invalidates the displayed fit. Results arriving
for an older selection are discarded by the UI.

Cyan guides show the cone, and pink guides show the perpendicular plane. They
show through the mesh for inspection; their finite display extents are not
inferred physical boundaries or uncertainty. Residual colors use independent
symmetric blue/white/red scales for each region, with limits shown in the UI.
Units remain unconfirmed, and lower training residual is not physical validation.

## Portable session and adapter boundary

Save session downloads JSON containing schema version, source and model hashes,
and sorted zero-based source vertex IDs for both regions. It contains no browser
objects, camera, renderer indices, or cached fit. Load validates the same source
and model before changing the selection; refit to calculate results.

The schema is experimental and requires the captured example and its saved fit
frame. It is not a self-contained replacement for the mesh, model or provenance.
Python replay without HTTP or a browser:

```python
from pathlib import Path

from experiments.nozzle_session import NozzleSession, NozzleWorkspace

workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
session = NozzleSession.model_validate_json(Path("nozzle-selection.json").read_text())
result = workspace.fit(session)
```

`NozzleWorkspace` is the frontend-independent adapter. `nozzle_browser.py` adds
HTTP transport only. The server binds to loopback and exposes a fixed route list,
not a general directory server. Native clients may use the Python API or HTTP:

| Route | Payload / response |
| --- | --- |
| GET `/api/meta` | Source counts, default session, display frame, binary layouts |
| GET `/mesh/positions` | Little-endian float32 XYZ, in saved fit-frame coordinates |
| GET `/mesh/indices` | Little-endian uint32 triangle triples, source order |
| POST `/api/session` | Validate session, return canonical session |
| POST `/api/fit` | Session; returns a job ID, or 409 if a fit is running |
| GET `/api/fit/{id}` | Running, failed, or complete with fit parameters/residuals |

POST requests require `Content-Type: application/json` and
`X-Scansor-Request: 1`. Sessions are limited to 1 MB. Host/Origin checks require
the printed numeric loopback URL. Only the latest fit is retained; an older job
may return 404. Fits run in one worker thread so HTTP and browser interaction
remain available. This is not a hardened deployment service or a stable public API.

Geometry stays resident in the renderer; camera movement does not fetch the mesh
again. Frames are requested on changes rather than an idle animation loop.
The displayed submission time is CPU time, not GPU completion or interactive FPS.
This small in-memory experiment makes no bounded-memory or large-model claim.

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
  tests/test_nozzle_session.py tests/test_mesh_cone_plane_fit.py
```

Tests cover source/model binding, invalid IDs, disjoint selection edits, fitting
an edited membership, saved fit replay, binary geometry, origin/path boundaries,
and a fit worker that does not block reads or queue concurrent fits. The JavaScript
checks run in CI alongside the Python checks. Actual navigation, selection,
save/load and residual display were also exercised locally in Brave.

Before choosing a product frontend, assess visible-surface selection, larger
meshes/LOD, memory across browser processes, keyboard/touch accessibility,
multiple platform/GPU configurations, and a comparable native frontend. Preserve
canonical source IDs if future rendering paths reorder or simplify geometry.

When updating Three.js, check its changelog and migration notes, and repeat
navigation, selection-ID, visual and performance checks. The public controls
APIs and GPU behavior can change between revisions.

[onshape]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-rotation-with-an-upright-vertical-axis
