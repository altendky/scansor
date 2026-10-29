# Bounded STL scan import

The experiment admits ASCII and binary triangle STL alongside the existing PLY
profile. STL is interpreted as vertex observations with triangle connectivity;
triangle-centroid observations remain deferred. This does not establish a
general-purpose mesh repair or topology-preservation contract.

## Coordinates, identity and connectivity

Both STL encodings use the versioned `triangle-stl-f32-v1` interpretation. Binary
coordinates are IEEE float32. ASCII decimal tokens are converted to binary64 and
then rounded to float32, so float64 mesh support is outside this profile. Every
admitted coordinate must be finite. Physical units remain unknown and there is
no inferred scaling or alignment.

Vertices share an observation ID only when all three interpreted float32
coordinates are exactly equal. Positive and negative zero share the same
coordinate. IDs follow the first occurrence of each coordinate in source facet
order. Near coordinates remain separate: there is no tolerance welding. Facet
order, duplicate triangles and degenerate-triangle accounting are retained.
Coincident disconnected surfaces can therefore share exact coordinate IDs;
source STL carries no distinct topological vertex identity to preserve there.

Raw source SHA-256 and interpretation SHA-256 both participate in source
binding. Equivalent ASCII and binary meshes share the interpretation policy,
but retain different raw-byte identities. Moving or renaming an exact source
does not change its binding. The previous PLY interpretation hash and profile
are unchanged.

Content determines the format; the filename extension is only a picker hint.
The native server admits up to 256 MiB of retained scan sources, eight million
vertices and eight million triangles, with an 8 GiB incremental working-set
estimate. These are admission caps, not measured total-process or GPU memory
guarantees. They include room for a roughly 104 MiB binary STL and its reduced
virtual scan. Native project archives admit 256 MiB per member and 384 MiB
compressed or expanded in aggregate. The browser worker retains its smaller
8 MiB source and 96 MiB working-set profile; a large native project can exceed
that worker's import limits.

The opt-in `tests/test_large_native_stl.py` native HTTP check exercised a
109,202,584-byte connected binary STL with 1,094,116 vertices and 2,184,050
triangles. It imported, reduced to 1,089 vertices and 2,048 triangles, saved an
8,635,647-byte project containing the original STL, and reopened the same recipe
and virtual scan. This generated planar case does not establish capacity for
every mesh at the admission caps.

An exact binary STL layout is recognized before text magic, including arbitrary
binary headers beginning with `solid` or `ply`. Strict ASCII grammar, bounded
lines/tokens and exact binary facet lengths reject unsupported input. Nonzero
binary attribute/color fields and recognized color/material extensions are
rejected rather than silently discarded.

## Derived normals and weights

Stored STL facet normals do not control fitting. At each exact shared vertex,
the importer accumulates the oriented geometric cross vectors of incident
triangles in facet/corner order using float64 arithmetic. This is an
area-weighted direction. It is normalized and quantized to float32 for the
original observation-normal array, then normalized to float64 unit directions
for fitting. The original raw STL remains available through its verified
replayable source.

These normals are derived and smoothed across exact shared coordinates. Sharp
positions remain unchanged, but sharp normal discontinuities are not preserved.
A small angular threshold in connected selection growth can consequently reject
a patch whose boundary normals include adjacent faces. There is no crease-angle
parameter or hidden normal fan in this initial slice. The source inspector
labels this policy, and the import guidance identifies STL normals as smoothed.

Each vertex weight is one third of each usable incident triangle's area. When
positive incident areas produce an exactly zero summed direction, import uses
the largest incident triangle's geometric area vector, with first source facet
order breaking equal-area ties. A persistent import warning gives the count and
first affected vertex IDs and explains that the chosen directions are ambiguous
and can affect normal-based selection and fitting. This does not apply a
near-zero threshold or change positions, connectivity, weights or original
source bytes. Zero-area-only vertices and nonfinite directions still reject
import because no usable geometric direction exists.

Only sources that need this fallback receive its separate interpretation hash;
previously admitted STL sources keep their existing interpretation and binding.
Both hashes are supported on actions/project reopen. The warning is reconstructed
from source bytes and remains visible when a reduced virtual scan is active.

## Source lifecycle and bounds

Native and worker imports use the same replayable source and prepared-candidate
publication boundary as PLY. The parser uses bounded sequential reads and fresh
streams for inspection/replay, checks exact length and SHA-256, and exposes
progress and cancellation. Admission includes source length, facets, unique
vertices, final geometry and conservative connectivity/deduplication working
memory. It does not retain an additional whole raw-source byte buffer.

An STL original can produce one reduced virtual scan through the existing
connected clustering policy. The canonical derivative is PLY; its own descriptor
and normal interpretation remain explicit. Active scan selection controls both
display and fitting. Source-bound selection entries and explicit volume choices
transfer modeling intent, while switching invalidates computed results.

Saved actions request the exact STL original when reconstruction needs it.
Self-contained `.scansor` projects retain that original source once and
reconstruct the canonical derivative. Worker Force recovery uses the retained
original File or archive member, with the same source/hash and recipe checks.

## Verification scope

Parser and workspace tests use independently generated ASCII/binary fixtures,
including misleading headers, float32 rounding, signed zero, near coordinates,
facet order, duplicate/degenerate accounting, independently calculated area
weights and normals, cancelled directions, source mutation, limits and
cancellation. Core project tests cover actions, virtual replay and exact source
binding. Adapter tests cover native HTTP and worker preparation/recovery.

`experiments/browser_viewer/tests/stl-sources.spec.js` exercises actual native
File pickers, source inspection, pointer selection, plane fitting, explicit
volume transfer, active switching, Auto, actions and project reopen. The shared
STL UI harness also runs against the shipped static worker through
`experiments/browser_static/validate-stl-source-ui.mjs`, including plain and
isolated modes and Force recovery from raw-source and archive-backed states.
These bounded planar fixtures establish workflow integration, not broad scan
quality, performance or topology-preservation claims.
