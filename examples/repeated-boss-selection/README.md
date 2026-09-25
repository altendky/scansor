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

After installing the browser assets, open the coarse realization with:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser \
  --example local-inputs/repeated-boss-selection-v2/scan-coarse \
  --port 8765
```

To load the checked-in end-to-end demonstration graph, run:

```sh
OPENBLAS_NUM_THREADS=2 PYTHONPATH=src:. uv run --locked \
  python -m experiments.nozzle_browser \
  --example local-inputs/repeated-boss-selection-v2/scan-coarse \
  --recipe examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json \
  --port 8765
```

That recipe retains the four source selections, three target match selections,
generated reuse selections and fits, equal-radius and parallel-plane
relationships, sphere-center point datums, directed axis, output Frame, measured
Scale, and applied Transform. It is intended as a reproducible UI demonstration,
not a compatibility promise or physical-validation record.

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
