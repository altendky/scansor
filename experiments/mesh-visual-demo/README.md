# Visual mesh walkthrough

Generate a small demonstration with the actual import, contribution and display
pipeline, plus complete display replay:

```sh
PYTHONPATH=src uv run --locked python -m experiments.mesh_visual_demo \
  local-inputs/mesh-visual-demo
```

The output directory must not exist. Open `START-HERE.html` in Brave or another
modern browser. It is self-contained, needs no server or third-party JavaScript,
and provides triangulation, validity, weights, rejection and clean-reference
views. Hover over vertex markers to inspect actual exported values. Edge and
marker controls affect presentation only; no input rows are sampled.

The surface has unit spacing on the left, quarter-unit spacing on the right,
and a conforming transition strip. All 556 triangles have positive area; total
area is exactly 32 in arbitrary squared coordinate units. The only boundary is
the outer perimeter. Coarse interior vertices have exactly sixteen times the
weight of fine interior vertices. Boundaries and transition vertices differ.

Four rejected faces are separated below the surface: out-of-range index,
nonfinite position, repeated index and zero computed area. Ten displayable
corners occupy nine positions; the repeated position has a visible multiplicity
label and every original record appears on hover. One additional vertex is
isolated. No position is invented for the two nondisplayable corners. The clean
reference omits these defects and has identical valid-surface exported values.

## Display semantics

Marker RGB values and numeric fields come from the actual exports. Triangle
fills use the first vertex's RGB for an illustrative flat color, not an
interpolated per-pixel field. In the weight ramp, intermediate positive weights
can look gray; inspect the numeric value/status before inferring exclusion.
Marker radii are enlarged in screen space. The browser uses a fixed labeled top
view and does not claim interactive 3D camera behavior.

`CloudCompare-presentation.ply` is an additional RGB-only presentation mesh:
validity on the left, weights on the right, and enlarged rejection glyphs below
the left panel. It has no scalar fields to map at import. The right panel is
translated for comparison; lower rejection glyphs are also translated and
expanded into disks. Duplicate corner glyphs are grouped by position/status.
This decorative geometry is not an authoritative mesh or a round-trip target.
The original PLY views, legends, identities and replay results remain under
`clean/`, `broken/`, and `data.json`.

CloudCompare is the previously authorized, separately installed **GPL** viewer,
not a Scansor dependency. No CloudCompare or `plyfile` source is consulted.
A native scene can be prepared using its
[documented command-line interface](https://cloudcompare.org/doc/wiki/index.php/Command_line_mode):

```sh
QT_QPA_PLATFORM=offscreen CloudCompare -SILENT -AUTO_SAVE OFF \
  -O local-inputs/mesh-visual-demo/CloudCompare-presentation.ply \
  -M_EXPORT_FMT BIN -SAVE_MESHES FILE \
  local-inputs/mesh-visual-demo/CloudCompare-presentation.bin
```

The standalone browser walkthrough is the labeled demonstration. The native
CloudCompare scene provides the geometry comparison; original diagnostic point
clouds still require visible point sizes and RGB or an explicitly selected
scalar field. Weights are not yet connected to external-source fitting.

## Retained visual evidence

[Verification observations](verification.json) retain the five browser view
populations and complete display replay results for both fixtures. Brave mode
switching, diagnostic hover text and the marker toggle were exercised. The
native RGB scene was also opened and inspected in a separate CloudCompare GUI.
These are local observations, not a cross-browser or cross-platform guarantee.

- [Weight comparison screenshot](screenshots/weights.png)
- [Separated rejection markers screenshot](screenshots/rejected-face-corners.png)
- [CloudCompare native scene screenshot](screenshots/cloudcompare.png)

The preceding tiny fixtures exposed several usability traps: an active constant
scalar field can hide exported RGB colors; duplicated property assignments in
the PLY import dialog prevent loading; an empty rejected view may be refused;
and coincident one-pixel corner records can be effectively invisible. The
walkthrough uses explicit modes, large markers and grouped-record inspection.
The separate CloudCompare presentation uses RGB-only geometry and visible disk
glyphs. Neither display replaces the complete original records.
