# S5 CloudCompare display evidence

[The retained report](report.json) records actual generated exports, read-only
display replay, CLI loads, selection of every scalar field, PLY resaves, and
comparisons by split source IDs. It includes commands, headers, exact input and
output hashes, implementation hashes, dependency versions, and bounded examples
of differences. Generated binary files and full viewer logs remain outside Git
at the recorded run directory.

The tested executable reports `CloudCompare v2.14.beta (Sep 10 2026)` in the
standalone codec resave header. Its SHA-256 is
`b324994f65061d901246676ee88289f30738ce192251dc2e5137ff6741dc3cb8`.
CloudCompare is an explicitly authorized, separately installed **GPL** viewer;
it is not linked or bundled with Scansor. No CloudCompare or `plyfile` source was
consulted. The command syntax follows the
[official CLI documentation](https://cloudcompare.org/doc/wiki/index.php/Command_line_mode).

## Observed preservation

- The four-vertex right triangle and orphan fixture preserves every field,
  coordinate, ID, vertex, and face exactly in its validity and weight resaves.
- The six-source-vertex adverse fixture preserves all five finite vertices,
  three usable faces, and seven displayable rejected corners. All nine main-view
  and fifteen rejected-view scalar fields were selected. Statuses, colors,
  coordinates, areas, weights, winding and multiplicity compare exactly.
- The adverse fixture includes two indistinguishable duplicate faces. Their
  topology and multiplicity survive, but individual source face IDs cannot be
  recovered from a resave's identical vertex triples alone.
- Artificial codec labels `2^24+1`, `2^53+1`, `2^63+1`, and `2^64-1` survive
  exactly through the same viewer. This separate diagnostic does not claim a
  four-vertex mesh has those source row ordinals.
- An explicit viewer global shift `(10,20,30)` preserves the simple fixture's
  exported coordinates on resave.

## Observed limits

The precision fixture contains separate very large and very small triangles,
plus an orphan. The display-only transform subtracts an x-origin of `0.5` and
multiplies coordinates by `2^-1`; its own inverse-round-trip losses are recorded
in the display legend, separately from viewer losses. With viewer global shift
explicitly set to zero, both main-view resaves have:

- One rounded x-coordinate: `8388607.75` becomes `8388608`.
- Three positive normalized weights changed to zero.
- Four raw-area values changed to negative infinity: the three small-triangle
  vertices and the zero-area orphan. The large raw areas remain preserved.

The comparison reports these as observed numeric changes, without inferring
CloudCompare's internal cause. The actual resave header declares raw area as
`double`, while coordinates and weights use `float`; declared double storage
alone does not establish preservation.

All five empty views are refused by this build. Deliberately removing one ID
digit after selecting every field makes source mapping unavailable, and the
comparison explicitly declines to certify topology from missing keys.
Renamed-field handling is covered by synthetic resave tests; this actual viewer
run did not rename fields automatically.

These are small CLI interoperability results. They do not establish GUI visual
inspection, full-process memory limits, large-viewer behavior, or physical
validation. S6 scale/resource evidence remains open. Viewer edits and resaves
never replace authoritative import or contribution artifacts.

## Reproduce

From the repository, with the explicitly pinned external executable available:

```sh
uv run --locked python experiments/mesh_display_viewer.py \
  --viewer /path/to/CloudCompare \
  --viewer-sha256 b324994f65061d901246676ee88289f30738ce192251dc2e5137ff6741dc3cb8 \
  --workdir /existing/directory/outside/git
```

The experiment creates a new directory, runs viewer commands sequentially with
an individual timeout, retains failure evidence, and rejects a binary hash or
implementation change. A different viewer build requires a new explicit pin
and a newly assessed report. `gates.representable_views`, `large_id_codec`, and
`explicit_nonzero_global_shift` are true for this run;
`gates.precision_preservation` is false.
