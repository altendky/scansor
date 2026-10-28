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

Each vertex weight is one third of each usable incident triangle's area. Zero
or exactly cancelled generated normal directions reject the complete import
with vertex diagnostics, including vertices supported only by zero-area
triangles. The importer does not discard or repair those vertices to make the
file fit.

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
