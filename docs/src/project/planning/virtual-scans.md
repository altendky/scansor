# Reduced virtual scans

**Agreed bounded direction:** create a reduced triangle mesh from an imported
original for preliminary selection and modeling, then choose the original as
active to fit its observations. The active scan supplies both display geometry
and fitting inputs. Independent display and fitting resolution is deferred.

This first workflow has one original and one reduced derivative in the same
coordinate frame. It does not include registration, unrelated scan management,
simultaneous fitting across scans, or progressive numerical solving. The
[negative progressive-fitting experiment](progressive-fitting.md) measured a
different proposal: subset stages inside an individual recovery operation.

## Reduction foundation

The isolated [reducer](../../../../experiments/virtual_scan_reduction.py) uses
existing NumPy and independent PLY I/O. Its `edge-cell-reduction-v1` settings
specify an integer number of cells along the longest bounding-box axis, from 2
through 4,096. This is a spatial detail setting, not an exact target vertex count
or a physical error tolerance.

Only usable input triangles participate. Vertices may join a cluster only along
an existing mesh edge whose endpoints share a grid cell and normal-orientation
class. The lowest original vertex ID represents a cluster, retaining its exact
position and supplied normal. This prevents directly welding separate components
or nearby sheets without a connecting in-cell mesh path. It does not establish
general topology preservation or preserve every sharp edge.

Remapped faces retain source order and winding. Collapsed, zero-area and duplicate
triangles are removed, and unused vertices are compacted. If a surviving face
would invert, its affected clusters split back into original vertices. This
conservative refinement has a finite pass budget and may retain more detail than
the grid alone suggests. Achieved counts need not change monotonically with grid
resolution because refinement may restore more vertices at a coarser setting.
Unusable, ineffective or exhausted reductions fail
explicitly. Canonical derived PLY bytes pass through the existing strict importer;
original bytes and arrays remain unchanged.

Fitting the derivative uses its own incident-triangle-area weights. These are
preliminary observations, not the original full-data objective. Small holes,
thin features and narrow gaps can disappear at coarse settings; a volume authored
on that result cannot recover unexpressed selection intent automatically.

Lineage binds exact original byte and interpretation identities, versioned
settings, and the derived descriptor. Filenames are hints rather than identity.
Reproduction must validate the derived bytes before reusing its selection IDs.

## Active source and modeling intent

The switching slice will retain source data, not inactive copies of feature
graphs. Activation must rebuild from the current authored definitions so edits
made on either scan survive subsequent switches. Shared features and relationships
remain the same declarations, while evaluations belong to the active scan.

Selections retain separate source-bound entries. Returning to an exact prior
binding reuses its entry; reduced vertex IDs never become original vertex IDs.
Transferring to a new entry follows the existing explicit
[selection-volume setup](volume-oriented-selections.md). Fresh fitting and
ordinary validity checks run against the target observations. Pending setup or
unavailable authoring refresh remains an actionable needs-input state.

Save/reopen and worker recovery must preserve the original reference, derivation,
active choice and authored work together. Actions will reference source data
rather than embed original bytes. Failed, cancelled or stale creation/activation
must preserve the previous accepted project and recovery checkpoint.

## Resource and delivery gates

Creating a reduced scan still requires an admitted original. This feature does
not raise original byte, vertex, triangle or memory limits. Retaining two sources
and preparing a switch introduces additional allocations; the switching and UI
slices must bound and measure that lifecycle. Reduced geometry alone is not
evidence of whole-application speedup.

1. [Reduction foundation #355](https://github.com/altendky/scansor/issues/355):
   deterministic connectivity-aware reduction, lineage, validation, cancellation,
   generated fixtures and shared native/worker numerical probe.
2. [Retained-source switching #356](https://github.com/altendky/scansor/issues/356):
   bounded source ownership, transactional activation, exact selection entry
   reuse, fresh evaluation and adapter operations.
3. [UI and recovery #357](https://github.com/altendky/scansor/issues/357): creation
   controls, active picker, source-aware actions reopen, Force recovery, complete
   workflow and repeated-switch resource evidence.

The foundation alone does not expose the complete UI workflow. The broader
multiple-scan and source-settings discussions remain separate.
