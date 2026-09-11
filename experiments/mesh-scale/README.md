# S6 mesh scale validation

This opt-in work belongs to [issue #37](https://github.com/altendky/scansor/issues/37).
The expectation builder is implemented. Full ingestion, contribution, and visual
export resource measurements remain pending; this directory does not establish
the 512 MiB or 2 GiB targets.

## Independent expected results

From the repository root, using the locked Python environment:

```sh
PYTHONPATH=src python -m experiments.mesh_scale_freeze \
  --workdir /absolute/existing/directory/outside/git \
  --output /absolute/new/expected-grid.json \
  --width 3000 --height 2000 --noisy
```

Omit `--noisy` for the noiseless recipe. The seed defaults to 7; noisy grids use
`b=3,q=-8`. The required populations are:

| Width | Height | Vertices | Triangles |
| --- | --- | --- | --- |
| 3,000 | 2,000 | 6,000,000 | 11,990,002 |
| 10,000 | 6,000 | 60,000,000 | 119,968,002 |

Freeze both noise configurations at each size before interpreting measurements.
The manifest contains the complete expected source hash, all ten canonical
column hashes, three source-order row digests, reference counts, ordered area
and weight sums, and area/weight ranges. It also records the expectation digest,
oracle source hashes, runtime versions/native extension hash, and progress.
Output files are created exclusively; failed attempts retain a failed manifest.

The oracle does not import production Scansor code. It uses independently
implemented integer Philox rounds and exact binary32 coordinate encodings.
The grid's squared cross-product norm is exactly
`144 + (16*dx*dx + 9*dy*dy)/65536`, with height differences in integer units of
1/256. There are only 64 absolute slope pairs. The standard-library rational
oracle independently rounds each square root, half-area, and corner third.
Integer operations then reproduce the required rounding after each ordered
vertex addition, global addition, multiplication, and normalization division.
No floating reduction or production kernel supplies expected values.

The builder streams every source row and canonical record. Arrays are bounded
by 65,536 rows; the largest height read is bounded by 65,536 plus twice the grid
width plus two bytes. Owned temporary files hold one byte per source height and
eight bytes per vertex area, then are removed. They must be outside Git,
including ignored paths. This is a specialized experiment oracle, not a general
floating-point package or a runtime backend.

Tests compare every noisy slope against separate rational geometry, complete
small-grid bytes against a per-face rational oracle, full hashes against the
actual RAM/disk importer, and hashes across the maximum batch boundary. These
checks validate the expectation method; actual full-size source hashes must
still be compared before accepting benchmark results.

The first complete six-million-vertex expectations are retained for the
[noiseless grid](expected-grid-3000x2000-flat.json) and
[noisy grid](expected-grid-3000x2000-noisy.json). Each includes all 11,990,002
triangles and ten canonical columns. These manifests are independent expected
results; they have not yet been matched against full-size production imports.
The sixty-million-vertex expectation freezes remain pending.

## Remaining execution evidence

The full harness still needs sequential equivalent workloads at both budgets,
adversarial source order and valence, whole-worker RSS and phase resource
measurements, cancellation/resource-failure evidence, and full displayable-row
export measurements. Large artifacts remain outside Git. Ordinary CI runs only
small correctness tests. A source or expectation freeze is not a completed
scale, platform, viewer, or physical-accuracy gate.
