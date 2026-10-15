# Single-scan import contract

## Status and authority

**Agreed bounded implementation contract, 2026-10-15; evidence baseline
`80788df`.** This records
the investigation for [#238](https://github.com/altendky/scansor/issues/238).
The user accepted the strict initial input policy and source lifecycle with
improvement follow-ups, then approved keeping computed solver seeds out of saved
actions while preserving explicit reference geometry. User-file import is not
implemented by this document; algorithms, resource caps and workflow claims still
require the implementation evidence below.

**Implemented source foundation:** the shared
[scan workspace](../../../../experiments/scan_workspace.py) and
[project transaction](../../../../experiments/scan_project.py) now prepare the raw
strict PLY as an immutable, source-only graph without example sidecars. An empty
project is valid. Generic Source definitions carry exact-byte/interpretation
descriptors; original IDs/coordinates, raw normals, normalized direction
derivatives and incident-area weights remain separate from display buffers.
Failed/cancelled/stale preparation preserves the accepted generation. Retained
examples use the same geometry reader through an explicit legacy adapter that
preserves their existing reference hashes and optional starter recipes.

The native HTTP adapter now implements bounded source import, progress,
cooperative Cancel and confirmed project reset through the common backend
operations. Its source-only empty constructor/`--empty` entry point is available
for bounded native source authoring through #278. The common UI now exposes
empty projects, a file picker and read-only Source inspection where backend
capabilities permit them. Full both-backend UI acceptance remains #243;
worker import/recovery remains #242. See the
[native measurements and lifecycle](native-scan-import.md). The full numerical
delivery gate in #240 remains open; this transport slice does not claim its
pending cone/symmetry integration or cross-runtime parity work is delivered.

Standalone plane fitting now uses only selected source positions, incident-area
weights and admitted normal directions. Its direct covariance solve is centered
and length-normalized, with separate coverage/eigengap diagnostics. A unique
unsigned plane remains valid when supplied normals cannot determine its sign;
that result records a canonical sign and ambiguous orientation, and directional
growth rejects it rather than arbitrarily favoring one normal direction. No
numerical seed or model support is read for an independent plane. Existing
explicit reference-plane orientation remains authoritative.

Standalone sphere fitting also reads only the selected positions and weights.
It retains the centered algebraic initializer followed by geometric refinement,
with explicit rank/conditioning evidence for insufficient curvature. A shared
free center is initialized from all participating sphere patches together: two
individually planar circular patches can identify a center jointly. Its bounded
recovery compares a fresh joint solution with an optional authored free-point
hint; a distant or invalid hint cannot prevent a fresh fit. Fixed and
source-fit-derived centers remain exact, and radius-equality relationships retain
their existing meaning. Computed starts and recovery evidence are derived
results, not saved actions. Authored point definitions remain in actions.

The frozen native/Pyodide numerical probe includes independently posed imported
planes and spheres, nonuniform cells, multiple scales, growth and recipe replay
against renamed identical source bytes. This verifies shared numerical semantics
through the worker parity gate. Its distance and area comparisons use the frozen case's
length scale while retaining the same dimensionless tolerances; source identity,
membership and dimensionless evidence are compared separately. Adapter/UI file
opening remains separate. Kernel/STEP
geometry tests use practical scales; tiny-scale numerical fitting does not imply
CAD-kernel capability or physical accuracy.

Standalone cylinders now use selected normal-moment and position-covariance axes,
weighted projected-circle starts, and bounded geometric refinement in separate
rotated charts. Publication uses canonical source-coordinate geometry, including
horizontal axes; optional wrong/stale hints do not replace fresh attempts. Free
shared cylinder groups use their combined observations and independent radii;
fixed axes remain exact. Per-axis constrained charts, derived axes, radius
equalities and rigid reuse consume the canonical geometry. Imported cones and
rotational/mirror groups remain guarded pending their integration work.

Cylinder admission is deliberately conservative: selected positional covariance
must have a smallest/largest eigenvalue ratio above 1e-10, and the final weighted
geometric Jacobian, measured in a normalized chart aligned with the fitted axis,
must have condition at most 1e6. Narrow strips, circles, two generators and
planar/near-planar fixtures exercise explicit failure. These provisional
numerical limits are neither physical acceptance nor confidence estimates;
[#262](https://github.com/altendky/scansor/issues/262) tracks their evaluation and
improvement. Joint reference solves assess combined identifiability rather than
requiring every participating surface to pass standalone admission.

New cylinder actions use nullable support: absent bounds derive finite extent
from the selected observations. That automatic extent does not prevent axial
selection growth. Previously authored bounds retain the legacy source-Z0
meaning and still clip growth; an exactly or nearly horizontal axis cannot use
that particular explicit chart and reports unsupported support. A fixed semantic
anchor and its explicit support remain separate from disposable numerical hints.
Computed starts and diagnostics remain derived results, outside saved actions.

Generated reused imported lateral fits with explicitly inherited bounds are
temporarily rejected: copying a source-Z0 interval into a translated/rotated
target can move the geometry while leaving its hard support behind. Automatic
support reuse and independently authored target bounds remain available.
[#264](https://github.com/altendky/scansor/issues/264) tracks anchor-aware transfer,
target override authority and its fixed/free/group/replay verification.

Worker delivery/Force source retention, both-backend source-aware reopening and
worker measured caps remain their listed tickets. The native/shared reopening
child [#280](https://github.com/altendky/scansor/issues/280) uses private source
and recipe preparation before one native owner installation; full #244 includes
the worker path. Native defaults have executed
source-only import evidence; byte/count caps and the loader's incremental
allocation estimate are admission checks, not a measured total-runtime memory
guarantee. Input snapshots and authoritative/display copies are accounted for
separately from the interpreter, GPU and unrelated caller state.

Already agreed: a scan is a Source feature; initially each project has zero or
one scan; native HTTP and browser workers use interchangeable operations and
shared Python import/workspace/fitting semantics. Preserve original coordinates,
ordering and source provenance. Actions files reference original source bytes
rather than embedding them. Multiple-scan UX remains
[#237](https://github.com/altendky/scansor/issues/237). No automatic repair,
decimation, autosave, remote computation fallback or new site publication is
authorized here. Existing examples and supported numerical/CAD behavior remain
regression requirements.

## Observed source and implementation

The retained [manifest](../../../../examples/nozzle-bayonette-simplified/manifest.json)
identifies actual simplified RealityScan output, with unknown source units and
incompletely recorded simplification settings. Its raw PLY is implementation
evidence from a real file, not generated geometry or physical-accuracy truth.
The `.rsInfo` sidecar is provenance; opening the raw PLY must not require it.

The inspected file has 1,250,160 bytes, a 262-byte LF-terminated header, and SHA-256
`537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358`.
Its header is:

```text
ply
format binary_little_endian 1.0
comment Created in RealityScan
element vertex 24999
property float x
property float y
property float z
property float nx
property float ny
property float nz
element face 49994
property list uchar int vertex_indices
end_header
```

Each vertex occupies 24 bytes; each face occupies 13 bytes with a list count of
three and three signed little-endian int32 indices. The exact length is
`262 + 24 * 24999 + 13 * 49994`. This already matches
[`binary-triangle-ply-v1`](../../../../src/scansor/mesh_ply.py).
The distinct [point inspection reader](../../../../src/scansor/ply.py) is not
the mesh-import profile and must not supply its unit-conversion semantics.

All 24,999 positions and normals are finite, and every normal is nonzero.
Normal lengths range approximately from `0.9999998903` to `1.0000001374`.
Coordinate bounds, in original unknown units, are:

| Axis | Minimum | Maximum |
| --- | ---: | ---: |
| X | -9.868260383605957 | 9.098114967346191 |
| Y | -10.770027160644531 | 8.148521423339844 |
| Z | 21.880172729492188 | 32.007484436035156 |

All 49,994 faces have in-range distinct indices and positive computed area.
There are no isolated vertices or vertices without usable incident area.
These observations describe this retained file only.

Reproduce structural validation from the repository root with the locked project
environment:

```sh
uv run --locked python -c '
from pathlib import Path
from scansor.mesh_ply import MeshPlyReader
path = Path("examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply")
with path.open("rb") as stream:
    reader = MeshPlyReader(stream, max_range_bytes=2_000_000)
    reader.validate_all(chunk_rows=5_000)
    print(reader.layout.header.raw.decode("ascii"), reader.layout.byte_count)
'
```

Current implementation gaps:

- [`NozzleWorkspace`](../../../../experiments/nozzle_session.py) requires
  manifests, saved selections, a fit frame and model support. The
  [example loader](../../../../experiments/run_nozzle_cylinder.py) additionally
  verifies saved IDs and geometric selection gates. These are example factories,
  not a generic source contract.
- Legacy example [`Source` reference identity](../../../../experiments/feature_graph.py)
  still includes fixture selection/model configuration through its explicit
  adapter. Independent plane/sphere/cylinder fits use generic source identity;
  the legacy cone path still reads the fixture's initial parameters until #253.
- [Fit coordinate conditioning](../../../../experiments/fit_coordinates.py)
  centers and scales legacy numerical coordinates. Generic cylinders additionally
  rotate each proposal chart, while relationship components use one chart per
  free axis; canonical publication does not expose legacy chart placeholders.
- [Native source import](../../../../experiments/native_scan_import.py)
  stages bounded uploads separately and prepares outside the coordinator lock.
  Commit publishes one authoritative project state after cursor/cancellation
  checks; queued evaluation and CAD retain source leases. Example switching
  remains an explicit legacy adapter rather than a user-source interpretation.
- [Worker recovery](../../../../experiments/browser_static/recovery.md)
  retains the accepted recipe and reconstructs the packaged nozzle. It does not
  yet retain or reconstruct an arbitrary user source.

## Agreed initial policies

The initial workflow is deliberately bounded. Broader formats, attributes and
normal handling are tracked in
[#247](https://github.com/altendky/scansor/issues/247); safe source replacement
and setting edits are tracked in
[#248](https://github.com/altendky/scansor/issues/248). Those improvement issues
record later design work and do not block the initial slice. Existing
[full-resolution ingestion](full-resolution-mesh-ingestion.md) dispositions are
separate from admission to this interactive fitting experiment.

| ID | Initial policy | Consequence and deferred work |
| --- | --- | --- |
| P1: formats and attributes | Use the existing strict binary little-endian triangle profile with ordered float32 XYZ and complete normals. Reject extra properties/elements, other encodings/types, partial/missing normals and non-triangle lists with actionable diagnostics. | Broader PLY and missing-normal support require a separately specified admission policy; no silently ignored colors or attributes. |
| P2: invalid geometry and weights | Reject nonfinite positions, invalid indices and zero/nonfinite normals. Retain and count zero-area/repeated-index faces without area contribution; preserve all source row IDs. Use one third of each usable incident triangle's area as the relative observation weight; zero-area-only and isolated vertices cannot enter a fit. | This follows bounded example fitting rather than silently adopting the storage importer's more permissive normal policy. No welding, deduplication, winding repair or residual trimming. |
| P3: units | Default to explicitly unknown source units and preserve coordinates. Require an explicit output interpretation/calibration for physical-unit export; do not infer units from file names or `.rsInfo`. | Requiring physical units at import is an alternative. Existing explicit Frame/Scale/Transform and export behavior must be reconciled before a new physical-unit claim. |
| P4: initialization and support | Compute numerical starts from the current fit selections and explicit references. Keep computed seeds out of actions files. Use bounded automatic fresh initialization/retries for poor nonlinear starts, with explicit solve-local charts mapped back to source coordinates. Derive proposed finite support from selected geometry and retain explicit user support/reference intent. | No hidden nozzle seed, required manual seed-entry workflow, whole-scan model-intent inference or universal convergence claim. Report insufficient/ambiguous data or exhausted search explicitly. Family algorithms, rank checks and cone support remain evidence gates in #240 and its children. |
| P5: source lifecycle | Keep source settings read-only after acceptance. Add scan only in an empty project. New project/source removal explicitly clears dependent actions after a work-loss confirmation; no in-place replacement or implicit rebinding. | Editable units/import settings and dependent-preserving source replacement are deferred. A cancelled confirmation or file picker preserves all accepted work and drafts. |
| P6: resource policy | Use explicit per-adapter byte, vertex, face, staging and estimated-working-set caps, verified before dangerous allocations. Choose initial values from executed import/worker measurements; reject unsupported sizes rather than reduce observations. | No universal scan-size promise or invented memory guarantee. The existing 25,000-ID per-selection cap remains separate from whole-source capacity. |

For P2, preserve raw source normals; use separately normalized float64 directions
for normal-dependent algorithms, recording that interpretation. Mesh normals
computed for display are not fitting evidence. Duplicate usable face records
retain their multiplicity. Report counted noncontributing faces/vertices to the
user; no hidden geometry repair occurs. Relative area weights do not establish
measurement uncertainty, independent held-out validation or fit acceptance.
Any change from the existing accumulation order must be documented and compared
numerically in example regressions; do not claim identity with the separate
ordered full-resolution contribution implementation without checking it.

P4 authorizes no automatic success on insufficient or ill-conditioned data.
Plane/cylinder/cone/sphere and fixed/free reference relationships must retain
their current meanings. A generated independently posed cylinder and plane must
verify chart/world conversions; nozzle replay alone cannot establish that gate.
Result publication must explicitly bind chart-dependent parameters to their
chart, including fit-derived axes, fixed/free constrained groups, reuse and CAD.
Mapping display vectors alone is insufficient. Test a cylinder perpendicular to
source Z, its derived axis, a fit constrained to that axis and exported geometry;
no downstream consumer may silently interpret rotated parameters in the old
workspace positive-Z chart.

### Internal analytic geometry boundary

The spatial foundation in [#259](https://github.com/altendky/scansor/issues/259)
adds a canonical internal `analytic` record for cylinders/cones: kind, XYZ
origin, unit axis, radius at that origin, taper and finite axial support relative
to the origin. Shared Python and browser accessors prefer that record and reject
malformed explicit geometry without falling back to legacy parameters. Legacy
seven-value charts remain explicitly adapted; absent legacy support stays absent.
This is a spatial representation boundary, not a new saved-action schema.

Rebasing an anchor also rebases its radius and support; reversing the surface
axis reverses taper and support bounds. Rigid reuse transformations retain the
physical support endpoints. A cone may have a negative extrapolated radius at its
anchor when its complete declared finite segment has positive radii. Distances,
normals, support membership, regions, browser guides/footprints, extents and
standalone CAD export consume the common geometry rather than reconstructing a
positive-Z axis independently. Physical face boundaries remain separate from
fitting/display support.

The cylinder implementation in [#252](https://github.com/altendky/scansor/issues/252)
migrates the following numerical consumers. The cone child #253 supplies its
own initialization and positive-sheet support evidence:

| Consumer | Cylinder numerical integration |
| --- | --- |
| `feature_graph` | Canonical fit-derived axes, connected publication, radius equalities and relationship lowering |
| `nozzle_coaxial` | Bounded generic fixed/free cylinder groups and explicit legacy example charts |
| `fit_relationship_component` | A chart basis per free axis, semantic anchor/support rebasing and canonical constrained publication |
| `feature_reuse` | Rigid canonical hints and axis geometry; legacy numerical helpers stay explicit |

The internal numerical/graph cylinder path is enabled for the independently
tested eligible cases. Adapter/UI file import and full workflow acceptance remain
in #240 and its integration children; this does not claim a completed end-user
import feature. Cone dispatch remains guarded until its family gates pass.

### Saved intent and computed starts

The internal [recovery foundation](../../../../experiments/fit_initialization.py)
separates input/policy bindings and derived candidates from authoritative fitting
callbacks. It bounds fresh proposals and attempts separately from one optional
hint, with cooperative cancellation and callback evaluation limits. Each result
is assessed against the same full problem before deterministic comparison;
competing geometry is reported separately from local solver failure or exhausted
search. Diagnostics carry charts, attempted starts and termination and serialize
without nonstandard JSON numbers. These are internal numerical artifacts, not
new action fields. The existing single-start adapter keeps its historical limits
unless the caller supplies attempt control.

Recovery policy `selected-data-recovery-v2` anchors objective ties to the raw
minimum, checks every tied geometry pair for equivalence, and selects the first
equivalent proposal within the configured comparison tolerance. This prevents
roundoff-sized objective differences from choosing different equivalent charts
or favoring a hint over fresh geometry. Measured objectives and every attempt
remain unchanged in diagnostics; materially better candidates still win and
materially different tied geometry remains ambiguous. The policy version is
part of hint bindings, so the changed selection rule invalidates old caches.

The initial internal policy permits 16 fresh proposals, 8 fresh attempts and one
additional bound hint. Each attempt permits 100 iterations, 16 trials per step
and 512 combined observation/equality evaluations when a family adapter supplies
them. Geometry guards are bounded by the solver's iteration/trial limits. Final
full-problem assessment remains mandatory outside that quota. These provisional
numerical limits are not measured browser resource
caps, convergence guarantees or physical acceptance thresholds. Family-specific
initializers, graph/growth integration and arbitrary-pose geometry publication
remain in #250–#253 and #240; this foundation alone does not implement their
recovery acceptance cases.

Actions files save source/selection bindings, fit families, explicit reference
geometry, constraints and semantic settings. They do not save computed solver
starts or require a previous fitted result. Recompute initialization from the
current selected data when fitting. Optional internal caches are disposable and
bound to their inputs/policy; a stale or misleading cached start cannot be the
only route to a solution. Record actual attempted starts, initialization policy
identity, retries, validity/conditioning and termination with result diagnostics
when useful for explanation and reproduction.

An axis or point explicitly created by the user remains saved reference geometry,
even when its current schema field is named `initial_parameters` or
`initial_coordinates`. Preserve fixed/free roles and hard constraints exactly;
automatic recovery cannot move a fixed reference, change the declared family,
drop observations, alter relative weights or silently change declared support.
The intended interaction remains select a patch and Fit. Insufficient information
produces a meaningful diagnostic rather than a mandatory numerical-seed editor.

Plane fitting may retain its direct weighted-covariance solution, and sphere
fitting its selection-derived algebraic initializer and geometric refinement.
Cylinder/cone fitting needs family-specific candidates, orientation charts and
bounded restart behavior. Deterministic candidate comparison uses the same
selected objective and checked geometry. The best attempted solution is not a
certificate of global optimality or physical validity.

## Common source and workspace contract

This section defines the internal operation semantics. Method names and record
fields remain implementation suggestions, not a published schema or compatibility
promise. Implementations must satisfy P1–P6 and the acceptance evidence below.

### Descriptor and identities

Separate these records rather than including fixture selections in source identity:

- Exact-byte identity: SHA-256 and byte length of the original PLY, independent
  of file name, path and adapter. Never hash only a normalized geometry array.
- Interpretation identity: versioned accepted mesh profile, importer semantic
  policy, normal/weight rules, units declaration and any relevant settings.
  Canonicalize/hash shared semantic fields consistently in native and worker
  execution. Adapter paths, chunking and runtime resource budgets are not
  semantic identity unless they change interpretation.
- Source descriptor: both identities, original counts/order, diagnostics and a
  file-name/relative-path locating hint. Original row number is the vertex ID,
  scoped to that exact source and interpretation. Hint edits do not rebind IDs.
- Workspace: immutable authoritative float64 coordinates/directions/weights and
  original triangle connectivity, plus separate float32/uint32 display
  derivatives. Camera fitting does not change source coordinates or units.

The PLY I/O layer remains independent of Scansor feature records, units and
admission policy. Reuse project-owned parsing rather than introduce a second
JavaScript reader. No new dependency is needed for this contract.

An empty workspace has no source, selection or fit; it remains a valid UI state.
Adding a scan creates exactly one Source and no example starter actions.
Enforce zero/one source in shared Python validation. Choosing an example uses
the same source factory boundary with an explicitly optional starter recipe.
Existing example hashes/actions require either preserved behavior or a documented
experimental migration/rejection; do not fabricate a manifest for a user scan.

### Import transaction and observable lifecycle

Extend the [backend boundary](../../../../experiments/browser_viewer/backend.md)
with a common add/open-source operation. The UI supplies a file handle/payload
and semantic options to the adapter, with the expected accepted workspace
generation/token. HTTP owns upload framing/staging; workers own byte delivery and
virtual files. No endpoint, arbitrary server path or worker message belongs in
graph semantics. Bulk original bytes remain separate from JSON control messages.

The operation moves through reading/staging, profile validation, geometry and
weight preparation, candidate construction, then accepted commit or terminal
failure/cancellation. Expose counts/byte progress where known and coarse phases
elsewhere; do not invent solver/CAD percentages or cancellation guarantees.
Capabilities describe formats, limits, progress, cancellation and recovery
support with matching meanings across adapters.

Prepare outside the graph's long-held lock and without changing its accepted
source, recipe, display, drafts or recovery checkpoint. Validate the entire
payload, counts, settings, geometry, source-only graph and display buffers before
commit. Recheck generation/token and one-source preconditions at commit; stale
or competing candidates cannot overwrite accepted work. Publish descriptor,
workspace, graph, buffers and recovery reference as one accepted generation.
Do not replace an actively evaluating graph: require idle state or an explicitly
requested cancellation completed at a safe boundary, and recheck at commit.

Failure, resource rejection, stale requests and cancellation discard only owned
candidate state. Release staging/proxies/buffer views on every terminal path.
Distinguish cancelling an observation from cancelling import work. Polite Cancel
fences publication and stops preparation at safe boundaries; Force occurs only
on explicit request, with no timeout escalation. Current native solver/OCCT
limitations remain visible rather than reporting an invented successful stop.

### Recovery, saved actions and source lookup

Retain an accepted browser `File`/equivalent original-source reference outside
the disposable worker, alongside cloned interpretation and committed recipe.
Force reconstructs that source, verifies exact bytes and interpretation, then
replays the recipe through normal validation with derived state dirty. It never
substitutes the packaged nozzle. Keep the accepted checkpoint available when
reconstruction/replay fails. Fence old worker generations and late binary replies.

Native adapters retain an owned immutable source snapshot for the active
workspace lifetime, with explicit cleanup. An actions-file locator must never
grant arbitrary server-file access. Download actions preserve definitions and
source references, not source bytes, unsaved UI drafts, solver caches or a
self-contained project. Browser source/recipe retention is memory-only; reload,
tab closure and browser/process loss require source reacquisition and saved
actions. No automatic source persistence or autosave is implied.

Load actions validates the descriptor/schema/settings before changing accepted
state. With a matching active source, validate and replay normally. Otherwise
ask the user to locate the exact bytes and stage source plus recipe as one
transaction, subject to the explicit work-loss decision. A rename/move can
match; same-name different bytes, reordered/re-encoded geometry and changed
interpretation cannot silently match. Cancellation or failed replay preserves
the earlier accepted project and drafts. Legacy example actions need explicit
bounded recognition/migration or a clear rejection, never example substitution.

Here replay during import/recovery means structural, binding and capability
validation followed by installation of definitions with computed outputs dirty.
Failure before that acceptance preserves the previous checkpoint. Numerical/CAD
evaluation follows through the normal graph operation; a subsequent fit failure
appears in the newly accepted graph and does not roll back definitions or
silently reinstate an older project. Complete precommit numerical evaluation is
not proposed as an additional import transaction requirement.

## Readiness and acceptance gates

The recorded decisions complete the policy gate in
[#238](https://github.com/altendky/scansor/issues/238). The dependent queue is:

| Issue | Deliverable | Readiness gate |
| --- | --- | --- |
| [#239](https://github.com/altendky/scansor/issues/239) | Generic source/workspace | P1–P3, P5 and resource policy; shared descriptor/transaction recorded |
| [#240](https://github.com/altendky/scansor/issues/240) | Selection/reference fit integration | #239 and the initialization/family children below; independent arbitrary-pose, graph/reference/joint/reuse/CAD evidence |
| [#241](https://github.com/altendky/scansor/issues/241) | Native import adapter | #239–#240; native staging/cancellation and measured caps |
| [#242](https://github.com/altendky/scansor/issues/242) | Worker import and recovery | #239–#240; worker source ownership, measured caps and Force evidence |
| [#243](https://github.com/altendky/scansor/issues/243) | Source authoring UI | #241–#242 and P5; empty state, drafts and keyboard/focus checks |
| [#244](https://github.com/altendky/scansor/issues/244) | Source-aware saved actions | #239/#243; transactional lookup and legacy behavior |
| [#245](https://github.com/altendky/scansor/issues/245) | Complete workflow gates | #240/#244 plus actual native and worker UI paths |

The fit implementation is split into
[#249](https://github.com/altendky/scansor/issues/249) for shared initialization,
bounded recovery and diagnostics,
[#250](https://github.com/altendky/scansor/issues/250) for plane,
[#251](https://github.com/altendky/scansor/issues/251) for sphere,
[#252](https://github.com/altendky/scansor/issues/252) for cylinder, and
[#253](https://github.com/altendky/scansor/issues/253) for cone/frustum. The cone
child reuses the cylinder child's agreed axis/chart boundary where appropriate;
it must not require cylinder convergence as a universal cone prerequisite.
The shared contract and generic source boundary precede the family work.

For both adapters, exercise fresh project → raw retained PLY without sidecars →
Source → manual selection → plane and arbitrarily oriented cylinder → supported
CAD/STEP readback → save actions → locate/reopen/replay. Add an independently
constructed non-nozzle fixture, explicitly labeled generated. Compare source and
interpretation identities, ordered row IDs, weights, numerical results and world
geometry through the shared Python path; no external computation fallback.

Negative cases include unsupported attributes/encoding, malformed lengths/list
counts, invalid geometry/normals, counted zero-area faces/isolated vertices,
zero/one-source guards, oversized declarations before allocation, changed file
bytes/settings, stale token/generation, concurrent import, picker/lookup cancel,
failed candidate and failed replay. Test Cancel/Force before and after candidate
preparation and during publication; the accepted project must survive rejection.

Measure import/startup, UI responsiveness, and input/staging/float64 temporary/
display/GPU memory categories on stated runtimes. Existing browser measurements
are not new-import capacity evidence. Keep source-size, selected-ID and solver
capacity limits distinct. Extend deterministic CI/static artifact verification
and source/notice closure after implementation. Preserve example/reference/CAD
regressions and mark untested engines/larger files explicitly. A new public site
artifact still requires separate publication authorization.
