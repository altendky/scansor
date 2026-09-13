# nozzle-bayonette-simplified

A user-provided RealityScan mesh of a handheld fan nozzle, simplified to 24,999
vertices and 49,994 triangles. The binary PLY is approximately 1.25 MB and is stored
directly in Git. Source units are unconfirmed. This is a captured exploratory
example, not physical ground truth or an admitted product fitting fixture.

The main outer wall has a small taper, with bayonet features at one end and a
steeper conical region at the other. The current example uses a cone/frustum
approximation with a perpendicular top
plane; the earlier cylinder fits are retained as comparisons.
Cone implementation is tracked in [#48](https://github.com/altendky/scansor/issues/48);
uneven-coverage weighting is tracked in [#47](https://github.com/altendky/scansor/issues/47).

## Interactive selection experiment

The [local browser prototype](../../experiments/browser_viewer/README.md) loads this
example, edits cone/plane vertex memberships, reruns their joint fit, and displays
guides and residual colors. The backend feature graph drives both the UI and
script. Current recipes save as
source-bound JSON; undo and change history are deferred. The checked-in
`recipes/cone-plane.json` and `recipes/cylinder-plane.json` declare alternative
fits using the same selections. This remains an exploratory frontend, not a product UI commitment.

## Stored inputs

- `nozzle-bayonette-simplified.ply`: unchanged simplified export with XYZ,
  vertex normals, and triangle connectivity.
- `nozzle-bayonette-simplified.ply.rsInfo`: unchanged RealityScan export sidecar.
- `manifest.json`: source hashes, actual counts, and provenance limitations.
- `selections/outer-band.json`: geometric gates, weight definition, local fit
  frame, initial guess, and binding to this exact mesh and ID list.
- `selections/outer-band-vertex-ids.txt`: 1,261 zero-based source vertex row IDs,
  one per line, in ascending order.

The selection uses the same pre-fit geometric/normal gates approved for the
full-resolution scan. Simplification changes vertices and connectivity, so these
are new observations and new IDs. The frame and gates were chosen during that
original inspection and are now saved explicitly; replay does not need the
original mesh. No residual-based trimming is applied. Vertex weights are recomputed
as one third of each incident triangle's area on the whole simplified mesh.

## Current workflow: cone/frustum and perpendicular plane

```sh
PYTHONPATH=src:. python -m experiments.run_nozzle_cone_plane \
  --output local-inputs/nozzle-bayonette-simplified-cone-fit
```

This replaces the cylinder with a cone side while keeping the same 1,261 outer-band
vertices and 642 top-face vertices. The top plane normal remains exactly the cone
axis; both regions contribute to the joint fit. Radius and signed taper are fitted,
with finite positive-radius support declared in `models/cone-plane.json`.

| Metric | Joint cylinder/plane | Joint cone/plane |
| --- | ---: | ---: |
| Lateral orthogonal RMS | 0.01247 | 0.00761 |
| Plane orthogonal RMS | 0.02315 | 0.02315 |
| Combined area-weighted RMS | 0.01513 | 0.01225 |

The fitted signed half-angle is about **0.892 degrees**, widening toward the top.
Reference diameter is about **18.79278** source units. The diameter at the top plane
is about **18.92855**, extrapolated beyond the selected lateral band. Taper adds one
parameter; improvement on these fitted observations is not physical validation.

Open the generated `scan-joint-residuals.ply`, `joint-cone-guide.ply`, and
`perpendicular-plane-guide.ply` together in CloudCompare. Cyan is the cone, magenta
the plane, and residual colors are explained in `VIEW.txt`. `fit.json` compares
against the refitted cylinder baseline on exactly the same selections and includes
common axial-bin residual summaries. `residuals.npz` retains every ID, area weight,
and both radial and orthogonal cone residuals.

See [the cone experiment](../../experiments/mesh-cone-fit/README.md) for the
parameterization, exact constraint, finite support rules, and numerical tests.

## Earlier cylinder-only comparison

From the repository root, with the project's Python dependencies installed:

```sh
PYTHONPATH=src:. python -m experiments.run_nozzle_cylinder \
  --output local-inputs/nozzle-bayonette-simplified-fit
```

Choose an output directory that does not already exist. The script verifies input
hashes and exact selection replay, then writes `fit.json` and `residuals.npz`
containing every selected ID, weight, and radial residual. Positive residuals lie
outside the fitted cylinder. Source inputs are not modified.

The exploratory solver is in `experiments/mesh_cylinder_fit.py`. It holds this
small selection in memory; this runner is not a general large-mesh ingestion CLI.
Expected diameter is approximately 18.790952 and area-weighted radial RMS is
0.011969, in source units. Numerical tolerance is appropriate across environments;
bitwise-equivalent results are not promised.

For context, the original full-resolution fit had diameter 18.788752. Evaluating
the reduced fit on the original selected observations increased RMS from 0.011868
to 0.011914 (about 0.39%). This comparison supports retaining this particular small
example; it is not a general claim about simplification accuracy. The original
full-resolution scan and its private reports are not included or needed for replay.

## Earlier joint cylinder and perpendicular top plane

`selections/top-face.json` and `selections/top-face-vertex-ids.txt` retain the
user-approved 642-vertex upper annular face. Its geometric gates use the saved
cylinder-only reference axis, with radial bounds 8.4 to 9.2, axial bounds 4.1 to
4.6, and an axial normal dot product of at least 0.9. The reference and both
selections remain fixed throughout joint fitting; the fitted axis may move.

```sh
PYTHONPATH=src:. python -m experiments.run_nozzle_cylinder_plane \
  --output local-inputs/nozzle-bayonette-simplified-joint-fit
```

The plane normal and cylinder axis are the same unit vector by construction.
Both regions inform that axis. Cylinder position/radius and plane offset also
vary. Whole-mesh incident-area weights are normalized once over both regions;
surfaces do not receive equal total weight by default. The selected cylinder area
is about 129.69 and plane area about 30.96 in squared source units. Every selected
observation participates, without residual trimming.

| Metric | Cylinder fixed; plane offset fitted | Joint fit |
| --- | ---: | ---: |
| Diameter | 18.79095 | 18.79299 |
| Cylinder radial RMS | 0.01197 | 0.01247 |
| Plane normal-distance RMS | 0.05403 | 0.02315 |
| Combined area-weighted RMS | 0.02604 | 0.01513 |

The shared axis changes by about 0.441 degrees. These numbers describe the
constrained compromise on this selection, not physical accuracy. An unconstrained
weighted plane has RMS about 0.02312 on the same plane vertices; the remaining
plane residual is therefore not explained solely by perpendicularity.

Outputs include `fit.json` (parameters, source/selection/implementation hashes,
three starting guesses, comparison metrics), `residuals.npz` (both complete ID
lists, areas, residuals), and three display PLY files. Open those PLY files together
in CloudCompare:

- `scan-joint-residuals.ply`: full source geometry with signed residual colors.
- `joint-cylinder-guide.ply`: cyan fitted-cylinder guide, extended to the top plane.
- `perpendicular-plane-guide.ply`: magenta annular plane guide.

Gray is unselected context. Blue/white/red represents negative/zero/positive
residual. Each region uses its own symmetric scale without clipping; `VIEW.txt`
records the actual ranges. Guide tubes have radius 0.012 for visibility, not an
uncertainty interpretation. Plane guides extend from radius 8.2 to 10.2 for
visibility beyond the selected rim; these are display bounds, not fitted face
boundaries. Hide the guides to inspect the residual colors unobstructed.

This remains a separate experiment with in-memory arrays and a fixed declared
relationship. It does not implement a general constraint system or extend the
existing synthetic-only product admission path.
