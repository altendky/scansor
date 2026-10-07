# Repeated boss selection fixture

**Exploratory generated reference fixture.** This example is intended for
selection-region transfer, repeated compound-feature, and rescan experiments. It
is not physical evidence, a metrology reference, a supported model family, or a
public artifact format.

The exact nominal part is a `120 x 80 x 6 mm` plate with four bosses sharing a
common cross-section and three equal `5 mm`-radius spheres resting on three
plate corners. Each boss has an axis, an outer cylindrical wall, a
cylindrical blind bore, an annular shoulder plane, and a clocking flat. The
occurrences have different positions and clock angles; `boss-b` is deliberately
taller (`22 mm` rather than `14 mm`), and `boss-d` is tilted `12°` toward the
plate center. These variations exercise transfer without assuming equal finite
extent or parallel feature axes. The flat removes the otherwise unobservable
rotation about a boss axis when transferring a partial surface footprint.

The checked-in [fixture recipe](fixture.json) defines four realizations:

- `reference`: exact nominal geometry without noise or missing patches;
- `scan-coarse`: coarse tessellation, deterministic as-built deviations,
  structured missing patches, low-frequency bias, `0.12 mm` normal noise, and
  a visible `(6°, -8°, 11°)` XYZ scan-frame rotation for output-orientation
  experiments;
- `scan-fine`: the same as-built part and capture occlusions with independent,
  denser tessellation and `0.04 mm` normal noise; coincident surface-parameter
  samples reuse the coarse scan's deterministic noise field; and
- `scan-rescan`: another tessellation and occlusion pattern, `0.09 mm` normal
  noise, and a recorded nonidentity rigid pose.

The spheres provide independent curved patches for exercising standalone sphere
fits; their identities, centers, and radii are recorded in the truth sidecar.
The oracle selection bundle also includes the broad plate-top patch used by the
saved interactive recipe as an output-orientation reference.
As-built deviations and sensor corruption are recorded separately. The former
include plate bow, small outer- and bore-wall ovality, slight outer-wall taper,
shoulder-height variation, and one localized outer-wall dent. They deliberately
make the observed copies slightly imperfect while the nominal compound
cross-section remains exact.

Every realization also applies deterministic, bounded parameter-space jitter to
interior mesh vertices. Patch boundaries remain fixed, vertices stay exactly on
their analytic nominal surfaces before the declared imperfections and sensor
offsets, and the resulting triangulation does not present a uniform synthetic
grid. The jitter is part of tessellation, not a geometric imperfection.

## Generate

From the repository root:

```sh
PYTHONPATH=src:. uv run --locked python -m experiments.repeated_boss_fixture \
  --output local-inputs/repeated-boss-selection-v2
```

The generator refuses to overwrite an existing output directory. Each
realization is a browser-compatible example directory containing a triangulated
PLY, source-bound seed selections on `boss-a`, oracle role selections for all
four occurrences, and separate numeric truth arrays. A root manifest binds all
generated files and their SHA-256 digests. Generated outputs remain under the
ignored `local-inputs/` tree rather than adding large binary fixtures to Git.

For a much denser, deterministic performance fixture based on `scan-coarse`,
generate only that realization and multiply every tessellation dimension:

```sh
PYTHONPATH=src:. uv run --locked python -m experiments.repeated_boss_fixture \
  --output local-inputs/repeated-boss-selection-v2-coarse-8x \
  --realization scan-coarse \
  --tessellation-scale 8
```

The scale is linear, so an `8x` tessellation has roughly `64x` the vertices and
triangles. This resamples the fixture's analytic surfaces; it does not interpolate
the generated PLY. The original PRNG seeds, as-built deviations, noise
construction, occlusion rules, and pose remain deterministic, while the generated
`definition.json` and manifests record and hash the denser tessellation. The
checked-in demonstration recipe is intentionally bound to the ordinary coarse
PLY and its vertex IDs, so use the generated selection bundles or create a new
recipe when exercising the denser source.

Open the dense realization without the checked-in recipe:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser \
  --example local-inputs/repeated-boss-selection-v2-coarse-8x/scan-coarse \
  --port 8765
```

## Benchmark generation, loading, and fitting

Run the sequential benchmark from the repository root:

```sh
PYTHONPATH=src:. uv run --locked python -m experiments.repeated_boss_benchmark \
  --scales 1 2 4 8 --repeats 3 --threads 2 \
  --output local-inputs/repeated-boss-benchmark.json
```

Each case runs in a fresh Python process; generated artifacts are temporary and
removed after measurement. The saved JSON retains raw wall/CPU times, actual mesh
and selection counts, source hashes, implementation hashes, runtime versions,
whole-worker peak RSS, and fit failures. Generation includes PLY, truth,
selections, and hashing; workspace loading includes source checks, whole-mesh
triangle areas/weights, saved selection replay, and frame construction. Fitting
uses the saved lateral/plane selections and includes cylinder initialization,
the joint cylinder/plane solve, and result construction. Total worker elapsed time
includes startup/import and shutdown; phase timers exclude them. Filesystem caches
are not flushed, and this benchmark does not measure browser selection,
rendering, or GPU performance.

On 2026-09-29, three sequential runs on the local Linux host with Python 3.12.14,
NumPy 2.5.1, and BLAS/OpenMP thread limits of two gave these median wall times:

| Case | Vertices | Triangles | Fit vertices (lateral + plane) | Generate | Load | Successful fit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Boss 1x | 6,292 | 10,976 | 222 | 0.113 s | 8.0 ms | 4.7 ms |
| Boss 2x | 23,783 | 44,388 | 994 | 0.297 s | 20.8 ms | 6.3 ms |
| Boss 4x | 92,475 | 178,632 | 3,877 | 1.022 s | 68.1 ms | 11.5 ms |
| Boss 8x | 364,614 | 716,638 | 15,195 | 3.959 s | 235.3 ms | 33.6 ms |
| Simplified nozzle | 24,999 | 49,994 | 1,903 | N/A | 57.0 ms | 8.7 ms |

Every case completed in all three repetitions using the shared scaled solver.
The earlier unscaled solver failed the 2x and 4x cases; those attempted-fit times
are not completed-solve comparisons. A separate 8x generation profile attributed
about 70% of its instrumented time to parameter jitter and sensor offsets, mainly
per-vertex Python loops, coordinate key construction, and deterministic hashing.
Profiling adds overhead and is separate from the timings above.

The local original nozzle's hash matches the simplified nozzle manifest's
`original_source_sha256`. Its PLY declares 2,894,759 vertices and 5,789,514
triangles: about 116x the simplified nozzle. The 8x boss mesh has 57.95x the
vertices and 65.29x the triangles of the ordinary coarse boss mesh, about 14x the
simplified nozzle, and only about one eighth of the original nozzle's mesh count.
These backend measurements therefore do not establish full-resolution nozzle
or browser performance. The 8x worker's median lifetime peak RSS was 285 MiB,
including generation; it is not an isolated loader or fit memory measurement.

### Compare the full demo workflow

The cylinder/plane benchmark above is not the full boss demo. To compare the
complete 59-action recipe across mesh sizes on Linux, run:

```sh
PYTHONPATH=src:. uv run --locked \
  python -m experiments.repeated_boss_full_benchmark \
  --scales 1 2 4 8 --repeats 3 --threads 2 \
  --output local-inputs/repeated-boss-full-scaling-benchmark.json
```

Only source bindings and explicit selection IDs change; all fit declarations,
reuse settings, relationships, datums, and output features stay unchanged.
The 1x recipe retains the exact saved memberships. Denser memberships use a
deterministic nearest-ordinary-vertex reconstruction of each painted footprint
in nominal part coordinates, restricted to the same occurrence, analytic role,
and plate face. This is an approximate Voronoi footprint, not a reconstruction of
the original painting gestures. Fixture truth is used only to prepare the
benchmark selections, never to initialize or constrain the numerical fits.
Preparation and generation are timed separately from graph evaluation.

Three fresh-worker repetitions on 2026-09-29, with two-thread BLAS/OpenMP limits,
produced these median wall times:

| Tessellation | Vertices | Load | Evaluation attempt | Ready actions | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 1x | 6,292 | 8.1 ms | 190 ms | 59/59 | Complete |
| 2x | 23,783 | 21.9 ms | 534 ms | 59/59 | Complete |
| 4x | 92,475 | 69.5 ms | 3.964 s | 59/59 | Complete |
| 8x | 364,614 | 252.5 ms | 6.794 s | 59/59 | Complete |

Every case completed all 59 actions in all three repetitions, with identical
source, transferred-recipe, and result hashes within each case. The 8x evaluation
takes 35.7x as long
for 57.95x the mesh vertices and 68.93x the fitted vertex entries
(2,003 versus 138,073, counting observations again when used by multiple fits).
Generation and footprint transfer are excluded from evaluation: at 8x their
medians were 4.026 s and 2.726 s respectively.

These results use the shared centered/scaled SVD solver and corrected support
semantics. Rigidly transported support uses physical unit-axis distances, and
fixed observation factors are not constrained by selection/display intervals.
No observations are removed and no fixture truth is supplied to the fits. Cone
positive-radius/apex checks remain structural requirements. See the
[fitting contract and numerical limits](../../experiments/mesh-cone-fit/README.md).
The earlier unit-axis-only correction completed 1x in 169 ms and 8x in 6.752 s,
but failed 2x and 4x at a support boundary despite available descending steps.
Those failed attempts are not successful-solve comparison timings.

A separate profile of the completed 8x evaluation attributed about 50% of its
instrumented time to three rigid reuse matches, mainly repeated all-pairs
distances on the bounded matching samples, and about 37% to deep-copying result
data, including the returned snapshot. Selection-volume application accounted
for another 8%; the shared nonlinear solver accounted for about 2.4%.
These figures come from a slower, instrumented run and should
not be substituted for the unprofiled wall times above.

JSON response serialization is separate: 6.1 ms for the 0.352 MB baseline
response versus 329 ms for the 18.074 MB dense response. Worker lifetime peak
RSS was 63.3 MiB versus 263 MiB, including generation and selection transfer;
these are not isolated solver memory measurements. Browser rendering, HTTP
transport, Rhino export, and cold-cache behavior are outside this comparison.

Additional non-power-of-two checks on 2026-09-30 used the same 59-action graph,
three fresh sequential workers per size, and the same two-thread limits:

| Tessellation | Vertices | Fitted observation entries | Median evaluation | Ready actions |
| --- | ---: | ---: | ---: | ---: |
| 5x | 143,643 | 53,785 | 5.667 s | 59/59 |
| 7x | 279,784 | 105,204 | 6.764 s | 59/59 |

Both sizes completed every action in every repetition, with identical source,
transferred-recipe, and result hashes within each size. These checks were run
on a different day from the table above; they broaden coverage, rather than
establishing a smooth timing curve or arbitrary-resolution robustness.
Attempts at 13x and 31x were rejected before mesh generation by the existing
tessellation-dimension limits; neither size reached the solver. The browser
also retains a 25,000-vertex limit on each explicit selection. Larger-data
support remains separate work; these benchmarks do not bypass either limit or
downsample the requested observations to claim a successful solve.

## Open the ordinary fixtures

After installing the browser assets, open the coarse realization with:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser \
  --example local-inputs/repeated-boss-selection-v2/scan-coarse \
  --port 8765
```

The checked-in end-to-end demonstration graph is the default for this coarse
example, including when opened from **Choose example…**. To select that recipe
explicitly, run:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser \
  --example local-inputs/repeated-boss-selection-v2/scan-coarse \
  --recipe \
  examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json \
  --port 8765
```

That recipe retains the four source selections, three target match selections,
generated reuse selections and fits, equal-radius and parallel-plane
relationships, coincident shoulders, sphere-center point datums, directed axis,
Frame, measured Scale, and Transform definitions. The captured 131-action recipe
also includes the plate perimeter fits, 22 face-building reviews, 26 retained
faces, and a Body. It is intended as a reproducible UI demonstration, not a
compatibility promise or physical-validation record.

The resampling benchmark and fresh face-construction tests use the separate
`recipes/repeated-boss-fitting-benchmark.json` fitting-only baseline. Saved face
region choices in the editable demo are tied to its coarse fitted geometry;
they are not transferable benchmark inputs for another tessellation.

The adapter retains its historical module name but reads the generated example's
manifest, source, model, and initial selection bundle. The browser's provisional
**Reuse → Feature** action can use one painted reference patch and multiple
target patches to place the source boss's fitted selection regions independently
on several occurrences. The fixture's
oracle labels remain verification truth rather than matcher inputs. This is a
bounded selection-and-fit reuse workflow, not an implemented compound definition
or occurrence model; compound roles and placements remain truth-sidecar evidence.

The coarse browser example deliberately keeps its scan coordinate frame, so the
plate and its features appear rotated rather than pre-aligned with the display
axes. The other realizations use the plate's part frame: their displayed origin
is the plate center at `(0, 0, 0)`, away from every boss, and the rescan's
declared capture pose is inverted for display and fitting. Seed-selection
azimuth gates retain a separate boss-relative frame, so changing the workspace
frame does not change the saved `boss-a` memberships.

## Truth boundary

PLY files contain only positions, normals, and triangles. Occurrence identities,
surface roles, nominal coordinates, as-built coordinates, measured part-frame
coordinates, local surface coordinates, transforms, and oracle memberships stay
in separate truth artifacts. They are test oracles and must not be treated as
information discovered from a scan.

`truth/occurrences.json` records each boss's resolved local-to-part rotation,
base center, axis, and height. Axial oracle footprints retain the seed boss's
physical millimetre span when applied to the taller boss; they do not stretch in
proportion to its height. Radial shoulder bounds remain normalized because they
describe the same shared cross-section.

The saved `normal-part` and `normal-scan` arrays are the nominal analytic normal
directions used to apply sensor displacement, transformed into their named
frames. They are not normals reconstructed from the imperfect measured mesh.
`measured-part` retains pre-pose measured coordinates, while `measured-scan`
records those coordinates after the declared capture pose and before PLY
float32 quantization.

The first generator intentionally uses independently meshed analytic patches, so
some coincident patch boundaries have duplicate vertices and selection growth
does not cross every analytic seam. Region-transfer seed footprints stay away
from those seams. The plate top is not Boolean-trimmed beneath the boss patches,
so this is a surface-role test assembly rather than a watertight manufacturing
solid. Robust outlier fitting and arbitrary deformation are deferred.
