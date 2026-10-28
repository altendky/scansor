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

The shared project coordinator retains one immutable original/reduced workspace
pair and one active graph. Activation rebuilds from the current authored
definitions so edits made on either scan survive subsequent switches. Shared
features and relationships remain the same declarations, while evaluations
belong to the active scan. The active Source carries the versioned derivation;
ordinary recipe edits cannot attach, change or remove that source authority.
An unrelated scan replacement or explicit reset releases the pair.

Selections retain separate source-bound entries. Returning to an exact prior
binding reuses its entry; reduced vertex IDs never become original vertex IDs.
Transferring to a new entry follows the existing explicit
[selection-volume setup](volume-oriented-selections.md). Fresh fitting and
ordinary validity checks run against the target observations. Pending setup or
unavailable authoring refresh remains an actionable needs-input state.

Saved actions preserve the original reference, derivation, active choice and
authored work without embedding scan bytes. Exact original bytes can reconstruct
either active endpoint, while an available retained pair can be reused. A
derivative alone cannot reopen actions requiring its original. Opening ordinary
actions that omit a retained derivative's lineage requires locating its exact
derived bytes; the original File cannot stand in for them.

Native and worker adapters expose no-upload creation and activation transactions.
Worker protocol 11 advertises the bounded capability; earlier profiles reject
these operations. Force recovery retains the original File outside the worker
and reconstructs the accepted active endpoint before validating its identity.
Failed, cancelled or stale creation/activation preserves the previous accepted
project and recovery checkpoint. User-facing controls use the same transactions
and unfinished-edit safeguards.

## Preliminary authoring workflow

The active-scan strip shows the current original or virtual mesh, achieved vertex
and triangle counts, and its use for both display and fitting. It remains visible
outside the feature editor. Imported originals offer **Create virtual scan**;
the creation dialog accepts the bounded spatial-detail setting. Examples and
older backends without the capability keep these controls hidden.
With Auto enabled, successful creation or activation schedules fresh evaluation
after the source transition unlocks. Entries still awaiting a volume choice
remain needs-input states.

1. Import an admitted original and create its reduced derivative.
2. Paint selections, declare fits and relationships, and capture selection
   volumes on the active reduced scan.
3. Choose **Original**, configure each new entry's source volume explicitly,
   and evaluate against the original observations.
4. Return to **Virtual** to continue preliminary work. Shared definitions and
   edits persist; source-bound memberships and evaluated results follow the
   active scan.

Save actions records the active choice and derivation. Reopening can locate
renamed identical original bytes and reconstruct either detail level. Cancelling
lookup or supplying wrong bytes preserves the accepted project. In-memory worker
Force recovery also retains that original-file authority; a page reload still
requires locating source bytes because actions do not embed them.

The first version permits one reduced derivative per original. Changing its
reduction settings after creation and managing arbitrary scan collections remain
future work. Existing imported-scan limits and separate selection-transfer setup
continue to apply.

## Resource and delivery gates

Creating a reduced scan still requires an admitted original. This feature does
not raise original byte, vertex, triangle or memory limits. Retaining two sources
and preparing a switch introduces additional allocations. Aggregate source
estimates are checked against the existing configured working-set bound; this
does not measure process-wide memory or guarantee that every admitted original
can also produce a virtual scan. The UI slice must measure the lifecycle.
Reduced geometry alone is not
evidence of whole-application speedup.

The [compact UI measurement record](../../../../experiments/virtual-scan-ui-measurement.json)
records one generated 289-vertex/512-triangle planar input reduced to 25 vertices
and 32 triangles. Native UI authoring/reopen passed, and actual plain and isolated
Chromium workers each completed ten repeated switches, two original-file reopens
and two Force recoveries. These small synthetic cases establish workflow
behavior, not physical accuracy or large-input performance.

| Worker mode | Creation wall time | Median activation wall time |
| --- | ---: | ---: |
| Plain | 293 ms | 234 ms |
| Isolated | 262 ms | 238 ms |

The renderer reported three geometries and one texture throughout each repeated
switch window, with the same worker retained. Main-heap observations varied with
garbage collection; logical live WebGL buffers ranged from about 1.9 to 23.1 KiB.
Those overlapping observations do not measure physical GPU or whole-browser
memory. Raw report hashes and per-switch observations are retained in the compact
record; the timings are single-host observations without a performance gate.

1. [Reduction foundation #355](https://github.com/altendky/scansor/issues/355):
   deterministic connectivity-aware reduction, lineage, validation, cancellation,
   generated fixtures and shared native/worker numerical probe.
2. [Retained-source switching #356](https://github.com/altendky/scansor/issues/356):
   bounded source ownership, transactional activation, exact selection entry
   reuse, fresh evaluation and adapter operations.
3. [UI and recovery #357](https://github.com/altendky/scansor/issues/357): creation
   controls, active picker, source-aware actions reopen, Force recovery, complete
   workflow and repeated-switch resource evidence.

The three slices form one bounded UI workflow. The broader multiple-scan and
source-settings discussions remain separate.
