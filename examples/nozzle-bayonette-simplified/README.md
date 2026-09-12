# nozzle-bayonette-simplified

A user-provided RealityScan mesh of a handheld fan nozzle, simplified to 24,999
vertices and 49,994 triangles. The binary PLY is approximately 1.25 MB and is stored
directly in Git. Source units are unconfirmed. This is a captured exploratory
example, not physical ground truth or an admitted product fitting fixture.

The main outer wall has a small taper, with bayonet features at one end and a
steeper conical region at the other. A cylinder is an initial approximation.
Cone support is tracked in [#48](https://github.com/altendky/scansor/issues/48);
uneven-coverage weighting is tracked in [#47](https://github.com/altendky/scansor/issues/47).

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

## Reproduce the cylinder fit

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
