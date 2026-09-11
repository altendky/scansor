# Full-Resolution Mesh Ingestion and Visual Audit

## Status and boundary

**Provisional contract; S1 I/O and S2 numeric/recipe core implemented, 2026-09-11.**
This is the internal contract
and implementation sequence for [issue #29][issue-29], following the requirements
in [PR #28][pr-28]. It specifies full-resolution external mesh ingestion,
area-derived contribution data, and CloudCompare inspection exports. It does not
admit these observations to the existing synthetic-only fitting workflow.

The [existing inspection reader](../../../../src/scansor/ply.py) accepts an in-memory byte
string, a strict vertex-only header, and physical units `m` or `mm`. It creates a
metre-valued float64 array under an internal size limit, rejects nonfinite data,
and does not accept mesh faces or exporter comments. The
[declared generated workflow](declared-analytic-model-generated-workflow.md)
requires replay-verified project-owned synthetic provenance. This design needs a
separate importer and artifact family; neither existing boundary is relaxed by
pretending external observations are synthetic.

The isolated reader/writer, strict mesh profile, and deterministic numeric/recipe
core have the S1/S2 implementations described below. S3–S6 remain unimplemented.
Internal revision
names are implementation targets, not public schemas or compatibility promises. Full-data
solver integration, model mapping, scale/pose estimation, robust refitting,
target-surface importance, additional formats, CAD publication, physical accuracy,
and acceptance remain later work. Implementation issues are
[#32](https://github.com/altendky/scansor/issues/32) through
[#37](https://github.com/altendky/scansor/issues/37).

### S1 implementation evidence

The [independent I/O package](../../../../src/scansor/_plyio/__init__.py) parses
bounded little-endian headers into scalar/list metadata. Fixed list lengths are
explicit caller assertions, validated for every record read or written. It has
no Scansor imports, unit conversion, geometric interpretation or canonicalization.
The [mesh adapter](../../../../src/scansor/mesh_ply.py) enforces this document's
triangle profile and translates plain contextual errors to application errors.
The existing inspection and generated fitting paths retain their previous rules.

`MeshPlyReader.read_range(element, start, stop)` returns owned read-only structured
NumPy records. Scalar properties are named fields; a fixed list is a nested field
with `count` and `values`. A reader validates exact file length at construction;
`validate_all(chunk_rows=...)` additionally checks every record's list count.
Reading a subset does not certify an entire source. The caller owns its seekable
stream, serializes access, and closes it. No mappings or hidden caches are retained.

Each read has a configured maximum result byte count, a bounded transient I/O
block, and at most a one-byte-per-row list-validation mask. Header metadata and
caller-retained results cost additional memory; this is an allocation boundary,
not a demonstrated whole-worker RSS budget. S3 owns that budget planner. The
writer accepts contiguous records with the exact declared dtype in source order,
handles short writes, and checks complete coverage with `finish()`. It leaves
flush, close, snapshots and atomic publication to its caller; write failures
prevent it from certifying a complete payload.

[Focused tests](../../../../tests/test_mesh_ply.py) reconstruct the 261-byte
golden independently, check exact exceptional float/index bits, all truncation
offsets, malformed declarations and list counts, overflow and allocation limits,
short reads/writes, contextual I/O failures, and chunk boundaries. The extraction
test copies the package and runs a read/write round trip while rejecting every
nonstandard-library import except NumPy and the copied package. These tests are
S1 evidence only; source snapshots, geometry/weights, replay, viewer and stress
gates still belong to their later slices.

### S2 implementation and conformance gate

The [recipe module](../../../../src/scansor/mesh_recipes.py) implements the explicit
Philox key/counter contract with unsigned 64-bit arrays, independent source-range
generation, ordered faces, and integer encoding of dyadic height noise. It checks
actual coordinate representability, including interior grid limits and generated
noise coefficients. `write_recipe` writes a caller-owned stream through S1;
`generate_file` requires an explicit directory outside a Git checkout, creates
exclusively, and removes its owned partial file on failure. Both bound work by a
checked chunk size. These are generation primitives, not transactional publication.

[Small recipes and frozen hashes](../../../../tests/fixtures/mesh-numeric-goldens-v1.json)
cover the right triangle and orphan, unequal/irrational areas, duplicate/reversed
faces, degenerate/coincident geometry, no faces, exceptional coordinates/normals,
invalid indices, and an explicit malformed count-byte edit. Their source bytes
are checked against independent `struct` encoding. All eight golden artifact
hashes below are checked using the S2 primitives and independent expected bytes;
this does not substitute for the S3/S4 importer and replay gates.

The [numeric module](../../../../src/scansor/mesh_numeric.py) uses separate
binary64 NumPy operations for independent faces, scalar left folds for dependent
accumulation, and the specified multiply-then-divide normalization. Bounded calls
check scalar and NumPy rounding, square root, signed zero, and subnormal behavior;
an unsupported environment raises `numeric-profile-failure`. The caller supplies
source order to folds and full-population normalization parameters. Disposition
precedence and complete row accounting remain S3/S4 responsibilities.

The [independent oracle](../../../../tests/mesh_rounding_oracle.py) uses integer
IEEE encodings and rational arithmetic, with squared-midpoint comparisons for
square root. Tests compare exact bits at rounding ties, cancellation, subnormal
and extreme coordinates, irrational areas, high valence, byte-swapped arrays,
and shuffled chunk completion. No tolerance or backend reduction tree is used.

[Mesh controls](../../../../src/scansor/mesh_controls.py) enforce float-free,
bounded canonical JSON and explicit semantic file inventories. A generation
request binds its recipe and generator implementation ID. NumPy's semantic
version and owned source bytes enter implementation identity; runtime paths,
chunks, machine/build diagnostics, and later DuckDB/PyArrow execution versions
do not. Source files use LF on all checkouts so platform line-ending conversion
cannot silently change an implementation identity. S3/S4 must extend their
inventories as their source/profile/policy semantics are implemented.

The [CI conformance workflow](../../../../.github/workflows/reflow-mesh-numeric.yml)
runs locked CPython 3.12.13 and 3.13.15 on Linux x86-64, Windows x86-64, and macOS
arm64. Each runs S1/S2 tests in fresh default and baseline NumPy processes,
verifies the actual architecture and effective dispatch settings, and uploads
runtime/build diagnostics, vectors, artifact hashes, and implementation IDs.
An aggregate job requires all twelve reports and identical semantic hashes.
The [runner](../../../../experiments/mesh_numeric_conformance.py) also works
locally. Inspect actual passing workflow reports for build-specific evidence;
the matrix is an internal conformance gate, not a product-support commitment.
Memory-budget and storage-backend invariance remain later gates.

[Retained CI evidence](../../../../experiments/mesh-numeric-conformance-v1-evidence.json)
from [run 34557050231](https://github.com/altendky/scansor/actions/runs/34557050231)
records all twelve passing numeric reports for code commit `ad77b299`, with NumPy
2.5.1 on the two locked Python versions. It includes macOS 15.7.9 arm64,
Windows Server 2025 x86-64, and Linux x86-64 builds. All source/control/column
hashes, Philox vectors, and implementation IDs matched. Baseline runs actually
disabled available dispatch targets, including ASIMDHP/ASIMDDP on arm64 and
X86_V3 plus available AVX-512 targets on x86-64. The evidence retains each
report's full build/runtime diagnostics and its original byte hash.

## Source profile and provenance

### Starting specimen

The optional ignored bundle is `local-inputs/scansor-reference-001/`. Its capture
manifest describes photograph-derived RealityScan Desktop `2.2.0.119430 RS`
output: 2,894,759 exported vertices, 5,789,514 triangles, float32 XYZ and normals,
no colors or classification, and a 144,738,164-byte PLY. The UI displayed
2,902,437 vertices; the cause of the difference remains unknown. The `.ply.rsInfo`
contains multiple XML roots, identity `transformToModel`, zero export translation
and rotation, and unit export scale. The `local:1 - Euclidean` label and other
coordinate-system fields do not establish physical units.

Those observations define a useful first profile, not a general RealityScan
export guarantee. The private specimen, its archive, and all large generated
files remain outside Git and are unnecessary for ordinary CI.

### Accepted profile: `binary-triangle-ply-v1`

- Uncompressed regular file, binary little-endian PLY 1.0. Header bytes are
  ASCII, terminated by `end_header` and a newline. Accept consistently LF or CRLF
  line endings; mixed endings, bare CR, NUL, a BOM, and blank lines fail.
- Header limit: 65,536 bytes including the terminator; line limit: 4,096 bytes.
  Limits are fixed profile constraints. A larger header needs a new profile,
  not a different memory-budget interpretation.
- After `ply` and `format binary_little_endian 1.0`, allow `comment` lines before
  or between declarations. Preserve their exact source bytes. No comment is an
  instruction, path to open, unit declaration, or trusted provenance claim.
- Exactly two elements in order: `vertex N`, then `face M`. Counts are unsigned
  canonical decimal text without leading zeros except `0`. Require
  `1 <= N <= 2^31` and `0 <= M`; signed 32-bit source indices limit addressable
  vertices. Also require every file length, count-derived allocation, and byte
  offset to fit a signed 64-bit offset. Check with nonwrapping integer arithmetic
  before constructing NumPy shapes or accessing data.
- Vertex properties are exactly ordered `float x`, `float y`, `float z`,
  optionally followed by all of `float nx`, `float ny`, `float nz`. All are
  IEEE-754 binary32. No aliases, doubles, partial normals, colors, classifications,
  reordered properties, duplicate names, other elements, or `obj_info` in v1.
  Report the unsupported declaration rather than silently dropping it.
- Faces have exactly `property list uchar int vertex_indices`. Every record has
  count byte `3`, followed by three little-endian signed 32-bit indices in source
  corner order. Preserve winding. No polygon triangulation or topology repair.
- Declaration tokens use single ASCII spaces with no leading/trailing spaces;
  comment text may contain printable ASCII spaces. Comments may be empty.
  These restrictions intentionally describe a bounded profile, not all valid PLY.

If the header length is `H`, vertex stride `V` is 12 or 24 bytes. Expected source
size is exactly `H + V*N + 13*M`. Read and validate every face count byte even
when the file size matches. A list count other than three is a whole-file
unsupported-structure failure: later fixed-stride records cannot be trusted.
Truncation and trailing data also fail the whole import. Do not publish a complete
result for a successfully parsed prefix.

Negative or out-of-range indices within a structurally complete triangle,
nonfinite vertex coordinates, invalid normals, and unusable geometric faces are
individually reportable dispositions defined below. Structural validity is not
geometric validity. A file with no usable surface may still produce a complete
analysis with zero eligible points.

### Source binding and sidecars

Snapshot exact bytes before numeric work. The request names one PLY and at most
one `.rsInfo` explicitly; no directory search or automatic sidecar discovery.
The source inventory has fixed roles `mesh-ply` and optional `realityscan-rsinfo`,
each with byte length and lowercase SHA-256. The bundle identity hashes this
ordered inventory. Filesystem names, directories, timestamps, and capture notes
are execution provenance outside that identity. Absence of a sidecar is explicit
and differs from an attached zero-byte sidecar.

A source point is `(PLY SHA-256, vertex index)`; a source face is `(PLY SHA-256,
face index)`, both zero-based. This identity is revision-local, independent of
memory budget, sidecar interpretation, and importer version. Even an equivalent
re-export with changed comments has a different PLY identity. No physical-point
identity across exports is promised. An import identity additionally binds the
source bundle, profile, importer implementation digest, semantic configuration,
numeric revision, and canonical import artifacts. A contribution identity binds
that import and the contribution policy and artifacts.

Retain the `.rsInfo` bytes as provenance even if interpretation fails. Interpret
at most 1 MiB; larger sidecars are retained and hashed but marked
`not-interpreted-size-limit`. For v1, decode UTF-8, permit one leading XML
declaration, and parse under a synthetic root without altering the retained
bytes. Disable DTDs, external entities, network access, and inclusion. Recognize
the observed `Model`, `ModelExport`, and `CalibrationExportSettings` roots;
duplicate roots/attributes or invalid XML produce `not-interpreted-malformed`.
Unknown roots/attributes produce `partially-interpreted-unknown-fields`; retain
them in the original. Valid recognized fields are recorded as source strings,
never silently converted to calibration. Also record `absent` or `interpreted`.
An unreadable requested sidecar is an input failure, not an absent sidecar.

This bounded interpretation selects no authoritative vendor transform semantics.
Do not follow texture, camera, project, or other paths mentioned by a sidecar.
Interpretation degradation does not reject an otherwise valid mesh and must be
visible in its control record and human summary.

Use `defusedxml.ElementTree`, selected by the user, behind a small replaceable
adapter. S3 owns the conformance checks in the
[library evaluation](full-resolution-mesh-library-evaluation.md#xml-api-and-support-fit).
Keep parser objects and exceptions inside that adapter; return owned sidecar
records. A future replacement must preserve the interpretation and provenance
rules above.

### Coordinates and later attachment

Preserve source coordinates without centering, scaling, axis permutation, normal
normalization, or pose inference. V1 records `physical_unit: unknown` and a
source-frame ID derived from the PLY identity. Coordinates have source-coordinate
units; areas have squared source-coordinate units, never square metres. Normal
components are retained dimensionless exporter values with unverified direction
convention. Bounds and normal validity cannot establish physical scale.

A later independently supplied calibration record can reference the import,
positive physical scale, explicit rotation/translation and frame direction,
provenance, uncertainty, and unresolved freedoms. It creates a derived coordinate
view and identity. Under an explicitly established uniform scale `s`, areas
transform by `s^2`; original arrays remain immutable. No record is attached or
solved here. The existing fixed-pose solver cannot consume unknown-unit data.

## Library reuse and processing boundary

### Library selection and reader isolation

The [library evaluation](full-resolution-mesh-library-evaluation.md) owns the
candidate comparison requested in the issue discussion. It covers parsing,
storage, geometry, compression, generation, visualization writing, property
checks, and resource measurement. Its selections remain provisional until their
assigned implementation gates pass.

**Current user direction, 2026-09-10:** avoid GPL, LGPL, and AGPL dependencies and
alert the user each time one is considered, including transitive dependencies.
`plyfile` is excluded on licensing grounds. Do not consult, copy, translate, or
port its source for Scansor. Develop this bounded reader from the file-format
contract, Scansor-owned fixtures, and eligible dependencies.

Place the reader in an isolated internal module/package with only Python and
NumPy dependencies. It owns bounded header decoding, typed property metadata,
fixed-record range iteration, source byte offsets, and format errors containing
element/row/property context. The writer uses the same generic property/layout
records. Both accept binary streams or narrowly specified seek/read interfaces;
the caller owns paths, snapshots, budgets, and publication.

The module must not import Scansor models, errors, CLI/settings, hashing, audit
records, `.rsInfo` handling, geometric validity, weighting, or synthetic fixture
code. A Scansor adapter enforces this specific profile and translates its plain
errors and arrays into application semantics. Reader tests exercise byte-level
fixtures without bootstrapping Scansor. An import-dependency check and a test that
runs a copied reader package with only its declared dependencies form the
extraction gate. No separate distribution or public API is introduced here.

### Format-neutral arrays

The adapter yields bounded typed array ranges and source offsets. The processing
core consumes arrays and row ranges, with no PLY headers, vendor field names,
library-specific element objects, or per-point Pydantic records. A future adapter
must explicitly translate its precision and topology into a versioned core
profile; it cannot silently narrow double coordinates to this binary32 profile.

Use separate contiguous columns. Every persistent column contains exactly `N`
vertex rows or `M` face rows, including rejected records. Its ordinal is its
source index; no hash or Python object is repeated per row. Temporary array
views may be strided, but persistent arrays use C order and these exact layouts:

| Artifact | Shape | Canonical dtype | Meaning |
| --- | --- | --- | --- |
| `xyz.bin` | `(N, 3)` | `<f4` | Source XYZ |
| `normals.bin` | `(N, 3)` | `<f4` | Source normals; absent only when no normal properties exist |
| `vertex-status.bin` | `(N,)` | `u1` | Position validity |
| `normal-status.bin` | `(N,)` | `u1` | Normal availability/validity |
| `reference-count.bin` | `(N,)` | `<u8` | Number of source corners containing this in-range index |
| `triangles.bin` | `(M, 3)` | `<i4` | All source indices, including invalid ones |
| `face-status.bin` | `(M,)` | `u1` | One primary geometric disposition |
| `face-area.bin` | `(M,)` | `<f8` | Usable area, or positive zero for rejected faces |
| `contribution-status.bin` | `(N,)` | `u1` | Initial policy admission/exclusion |
| `vertex-area.bin` | `(N,)` | `<f8` | Raw accumulated area, or positive zero |
| `weight.bin` | `(N,)` | `<f8` | Initial normalized contribution, or positive zero |

The final three columns belong to the contribution artifact, not the import
artifact. Import success, policy eligibility, future model mapping, factor
instantiation, explicit activation, fitting, and held-out assessment are separate
states. Import creates no model assignments, factors, residuals, or training roles.

Bulk encoding is headerless little-endian bytes, with no padding, compression,
pickle, native-size integers, or container-dependent headers. This is an internal
encoding for this experiment, not a durable production bulk-format selection.
The control inventory supplies each fixed filename, dtype, shape, exact length,
and SHA-256. Readers reject extra bytes, missing columns, unknown columns, object
dtypes, and lengths inconsistent with counts before exposing arrays.

Finite source floats retain their exact binary32 bits except negative zero is
canonicalized to positive zero. Preserve signed infinity, and replace every NaN
payload/sign with quiet-NaN bits `0x7fc00000` using integer bit operations before
arithmetic. Original exceptional bits remain recoverable from source bytes.
Do not substitute a finite coordinate for a nonfinite one. Integer status columns
carry missing/invalid semantics; absent normals are not synthesized. All derived
float64 columns are finite and use positive zero for noncontribution, with status
giving its reason. Whole-pipeline numeric failure has no fabricated result column.

## Dispositions and contribution policy

### Every-record accounting

`vertex-status` is `0=finite-position`, `1=nonfinite-position`.
`normal-status` is `0=absent`, `1=finite-nonzero`, `2=zero-vector`,
`3=nonfinite-vector`; nonfinite takes precedence over zero. A bad or missing
normal is diagnostic only and does not reject a finite position. V1 does not
calculate or threshold agreement with face normals.

For each face, apply the first applicable rule in this order:

| Code | Face disposition | Area contribution |
| ---: | --- | --- |
| 1 | `index-out-of-range`: any index negative or at least `N` | None |
| 2 | `nonfinite-position`: any referenced position invalid | None |
| 3 | `repeated-index`: any two corner indices equal | None |
| 4 | `zero-computed-area`: numeric kernel returns zero | None |
| 0 | `usable`: positive finite computed area | Equal allocation to all three corners |

If finite admitted coordinates produce a nonfinite area intermediate/result,
abort with `numeric-profile-failure` and the source face index. This profile's
binary32 range does not require binary64 overflow; an unsupported arithmetic
environment or implementation error must not masquerade as geometric exclusion.

All raw indices remain available even when a higher-priority rule prevents a
later geometric check. Reference counts count every in-range corner regardless
of face disposition, including repeated corners. Out-of-range corners increment
a separate aggregate count; no negative NumPy indexing is permitted. Checked
64-bit counts must never wrap.

`contribution-status` uses `0=eligible`, `1=nonfinite-position`,
`2=isolated` (finite position and reference count zero), and
`3=no-usable-area` (finite, referenced, but accumulated area zero).
Evaluate exclusions in that order. An eligible row must have strictly positive
finite area and normalized weight. If normalization or accumulation violates
that invariant, fail the contribution stage explicitly; never turn a resource
or numeric failure into a point exclusion.

Duplicate positions with different indices and repeated faces retain source
multiplicity. Each otherwise usable triangle contributes once per source record,
including reversed-winding and overlapping duplicates. V1 neither deduplicates
nor promises duplicate detection. Coincident coordinates within one triangle
produce zero area. Nonmanifold edges and inconsistent winding do not themselves
remove area; this is unoriented triangle-area allocation, not repaired physical
surface area. A later deduplication policy changes contribution identity.

Each completed stage stores exact category counts, ordered row digests, and the
complete detailed columns. Digest tags are `mesh-import-vertices-v1`,
`mesh-import-faces-v1`, and `mesh-contribution-vertices-v1` for the respective
streams. A digest stream begins with its ASCII tag plus NUL and `<u8` row count,
followed by each `<u8` source ordinal and the row's
canonical column bytes in the table order for that stage. The import vertex
stream covers its five vertex columns, omitting normals only when absent; the
face stream covers indices, status, and area. The contribution stream covers its
three columns. Stream SHA-256 incrementally; chunk delimiters never enter it.

Verification must establish contiguous index coverage `0..N-1` and `0..M-1`,
matching column lengths, category sums, ordered digests, and source replay.
Counts, XORs, commutative checksums, or a self-consistent rewritten manifest alone
are insufficient to detect omission, duplication, reordering, or source mismatch.
Point and face query operations return a bounded index range joined to these
columns and source identity. Summaries never replace detailed results.

### Initial policy: `triangle-area-mean-one-v1`

Let `A_f` be a usable triangle's computed area. Its contribution to each corner is
`c_f = RN(A_f / 3)`, where `RN` is specified below. For vertex `i`, fold its
incident usable contributions in increasing `(face index, corner index)` order,
starting at positive zero and rounding after every addition, to obtain `a_i`.
Rejected faces add nothing. This is the barycentric one-third-area baseline.

Let `K` be the count of eligible vertices. Fold their `a_i` in increasing source
vertex order to obtain `S`. For an eligible vertex set
`w_i = RN(RN(a_i * binary64(K)) / S)`; excluded rows have zero weight and an
explicit disposition. `K` is exactly representable under this profile's vertex
limit. For `K=0`, define `S=0`, all weights zero, and result
`complete-no-eligible-points`, with no division. Otherwise require `S>0` finite.

The normalized weights have mean approximately one, retaining relative surface
area while avoiding dependence on the arbitrary area unit. Binary64 rounding
means their sum need not equal `K` exactly; do not repair a chosen last vertex or
redistribute a rounding remainder. Likewise, three rounded thirds and accumulated
vertex area need not sum to exactly the face-area total.

Report raw usable face-area total in face order, `S`, raw per-vertex area range,
normalized weight range and ordered sum, and all exclusion counts. Raw-area
quantities have squared source-coordinate units; weights and their summaries are
dimensionless. Each statistic explicitly names its population and measure. No
residual, fit-quality, or physical-area interpretation is produced at import.

This policy has no user-tuned threshold or alternate normalization in v1. The
resolved request explicitly selects its ID and empty configuration; unknown
options fail. Future policy revisions/configurations produce distinct policy and
contribution identities without changing source point identities.

### Later weights and held-out isolation

Reserve a separate policy-stage record referencing the import, parent stage,
policy code/configuration digest, explicit population/role selection, and ordered
columns for disposition, named component weights, effective weight, and rule ID.
An iteration history is an immutable chain of such records and fit-result
references; unchanged columns may reference prior immutable blobs. Missing
components are absent, never inferred to be tuned or measured at import time.

This does not select target-importance composition, robust losses, defect
thresholds, stopping rules, or a solver's residual-weight convention. Those
features must define whether a value is an objective coefficient or a residual
multiplier and bind that choice explicitly. Current unit-weight synthetic factors
do not gain area weighting through this design.

The initial import is role-agnostic. Its areas can depend on all three coordinates
of every triangle and its normalization uses all eligible vertices. Therefore its
weights must not simply be reused as training weights after declaring held-out
vertices. The later solver-integration slice must define role-safe geometric
support and compute training normalization solely from training data; held-out
coordinates, faces involving them, residuals, or counts must not tune training
weights, thresholds, exclusion, or iteration choices. Separate held-out-only
perturbation tests must prove that isolation before integration is admitted.

## Numerical and serialization determinism

### Arithmetic revision: `ordered-binary64-v1`

`RN` means correctly rounded IEEE-754 binary64, round to nearest with ties to
even, after each individual operation. No extended-precision temporaries, fused
multiply-add contraction, reassociation, flush-to-zero, fast-math, BLAS reduction,
or backend-selected reduction tree is permitted. Convert finite binary32 source
coordinates exactly to binary64 in bounded work arrays.

For ordered corners `p0,p1,p2`, calculate:

```text
u[j] = RN(p1[j] - p0[j]); v[j] = RN(p2[j] - p0[j])
c[0] = RN(RN(u[1]*v[2]) - RN(u[2]*v[1]))
c[1] = RN(RN(u[2]*v[0]) - RN(u[0]*v[2]))
c[2] = RN(RN(u[0]*v[1]) - RN(u[1]*v[0]))
t = RN(RN(RN(c[0]*c[0]) + RN(c[1]*c[1])) + RN(c[2]*c[2]))
A = RN(RN(sqrt(t)) * 0.5)
```

Square root is correctly rounded as an individual operation. Binary32 input
range leaves ample binary64 exponent range for these intermediate products;
unexpected nonfinite calculations must be investigated, not attributed to an
ordinary large-coordinate mesh without evidence. `zero-computed-area` describes
this kernel's result, not an exact symbolic collinearity test. No small-area
epsilon is selected.

Implement elementwise operations with explicit float64 NumPy buffers and `out`
where useful. Vectorize independent faces; a face's operation sequence never
depends on its chunk. Use ordered scalar folds for dependent accumulation, or an
optimization demonstrated to produce those exact bits. A plain `numpy.sum`,
`bincount`, parallel scatter-add, or chunk subtotal is not that contract:
[NumPy documents][numpy-sum] that summation behavior depends on axis/layout.

The test oracle uses exact rational arithmetic for individual operations and
integer comparisons against binary64 rounding midpoints, including squared
midpoints for square root. It must be independent of the NumPy production kernel.
Test representable neighbors, halfway rounding, cancellation, signed zero,
binary32 subnormals/extremes, large valence, and shuffled chunk execution.
An environment failing the arithmetic conformance gate is explicitly unsupported
for canonical processing; do not silently emit a platform-specific identity or
loosen comparisons to a tolerance.

### Portable generation: `mesh-grid-philox-v1`

Use NumPy's explicit `Philox` bit generator through `random_raw`, with a direct
two-word key `[seed, 0]` and a four-word counter `[vertex_index, 0, 0, 0]`, where
all words are unsigned 64-bit integers. One vertex consumes one complete
four-word block. A chunk starting at vertex `a` initializes that counter and
draws exactly `4*chunk_rows` raw words, reshaped in source row order; the unused
lanes remain reserved. No `default_rng`, ambient generator, `SeedSequence`, or
floating-point distribution method participates in the recipe.

[Philox's integer-stream contract][philox] makes it a stronger starting point
than a high-level distribution API. Scansor additionally freezes counter/key
semantics, raw-word vectors, geometry, transforms, and byte output. NumPy's
[general generator compatibility policy][numpy-random] does not itself promise
cross-platform identical generated geometry or independence from call sizes.

The initial generated model is `rectangular-height-grid-v1`, a test mesh, not an
analytic-model declaration or fitting fixture. The recipe records integer width
`W`, height `H`, seed, `noise_bits b` in `0..16`, and power-of-two quantum exponent
`q`. Require `W,H >= 1`, `N=W*H <= 2^31`, and
`M=2*(W-1)*(H-1)`, with the source profile's checked size limits. Enumerate
vertices by `i = row*W + column`, with
`x=3*column`, `y=4*row`. For `b=0`, `z=0`; otherwise set
`z=(2*(raw_word_0 & (2^b-1))-(2^b-1))*2^q`. This is symmetric discrete bounded
height noise, not Gaussian or a physical scanner-noise model. All integer
construction is exact and checked for overflow. Require coordinates to be
exactly representable binary32 dyadics; an invalid recipe fails rather than
platform-rounding an unspecified value.

For each cell in row-major order, let `a=row*W+column`. Emit triangles
`(a,a+1,a+W)` then `(a+1,a+W+1,a+W)`. Normals are absent in this base recipe;
separate explicit small recipes test normal properties. Named adverse edits
apply after construction in their listed order and identify exact source indices
or byte offsets. They may create malformed files deliberately; each recipe binds
the expected failure category or complete accounting outcome.

The generated PLY header uses LF, canonical decimal counts, the exact ordered
properties above, and the single comment `scansor-mesh-recipe-v1` immediately
after the format line. Binary32 bytes
are encoded with explicit little-endian IEEE representation; face records have
count byte three and `<i4` corners. No timestamp, path, platform name, locale,
native byte order, trigonometry, logarithm, or approximate Gaussian transform
affects source bytes. More complex sampling and noise require a new recipe
revision and independent portable construction evidence.

Commit small recipes and expected source, column, and control hashes. They bind
model ID/parameters, vertex and face order, generator revision and implementation
digest, bit-generator key/counter convention, noise, adverse edits, encoding,
and expected accounting. Generate bulk data into an explicitly supplied directory
outside Git. A content-keyed cache is optional: verify hashes before reuse and
publish cache entries without overwrite only after full verification. Caches are
disposable; their paths, access times, and eviction do not affect identity.

### Canonical control records

Use the repository's sorted, two-space-indented ASCII JSON encoding with one
final LF, no duplicate keys, and no nonfinite JSON numbers. For this artifact
family restrict values to objects, arrays, strings, booleans, null, and integers;
store floating-point control values as 16-character lowercase binary64 bit
patterns, most-significant hex digit first. This removes float-to-decimal
formatting from identity. Sidecar-derived text uses JSON escaping without Unicode
normalization. Configurations have explicit defaults and reject unknown fields.
Each control record is limited to 8 MiB; unbounded row lists belong in bulk
columns, not JSON. An oversized request or record fails explicitly.

Use revision-tagged, acyclic records: source inventory; import inventory and
summary; contribution request/inventory/summary; optional visualization inventory.
Each inventory lists its own exact fixed artifact names and child hashes, never
its own digest. Its ID is SHA-256 of the canonical record bytes. The importer
semantic implementation digest hashes an ordered inventory of source decoding,
profile translation, canonicalization, serialization and numeric implementation
files, with versions of dependencies that implement those semantics. Execution-
only query orchestration, temporary storage code, and DuckDB/PyArrow versions
belong in the execution report; they do not independently rekey semantic
artifacts. Any change to a query's semantic rules must revise the bound semantic
contract even if implemented in execution code. Unrelated repository edits and
runtime paths do not rekey artifacts. Policy and generator have their own
implementation inventories. A deliberate semantic implementation revision can
change a root ID even when numeric columns remain identical.

Memory budget, chunk size, backend, elapsed time, memory/disk measurements, host,
library build diagnostics, paths, and timestamps belong in a separate execution
report. Different budgets/backends using the same semantic implementation must
produce identical source, import, and contribution bytes and IDs. Runtime reports
bind those IDs but do not feed back into them. Display configuration has its own
identity and cannot change an authoritative import or contribution identity.

## Bounded execution and publication

### Budget meaning

The requested working-memory budget `B` is a ceiling for the importer worker's
peak resident memory, including interpreter/libraries, live arrays, copies,
allocator overhead, scratch/index buffers, caches, and resident mapped pages.
V1 uses a single worker process; a later worker pool must budget the simultaneous
process-tree total. Virtual address space, free system RAM, and Python-traced
allocations alone are not this measurement.

Unmapped operating-system file cache is outside process RSS and is reported
separately where available, along with cgroup/container memory limits. It may
still cause a resource failure. Most expected hosts have at least 16 GB RAM, but
the first targets are `B=2 GiB` and `B=512 MiB` for the same full workload. These
are engineering targets to validate, not measured guarantees or a minimum host
specification. Budget changes may affect speed and temporary disk use only.

Before work, reserve measured baseline overhead, a safety allowance, maximum
decoder buffers, coordinate cache, arithmetic scratch, sort workspace, merge
buffers, serialization/hashing buffers, and any resident whole columns. The
implementation records this plan and allocation high-water accounting. Choose
chunk sizes from the remaining bytes, including advanced-indexing copies and
dtype conversions. If even the minimum chunk cannot fit, report
`resource-budget-too-small` before publication.

Use `psutil` for worker RSS/progress telemetry and OS peak-RSS counters for the
entire worker lifetime. The stress harness samples at most 10 ms apart and
records kernel high-water measurements; sampling alone can miss peaks. On Linux,
also record cgroup peak/current memory and OOM events when available. An optional
Memray run can localize native allocations on its supported platforms; its
instrumentation overhead is reported separately. Neither it nor `tracemalloc`
replaces process-memory measurements. Acceptance requires the measured peak to
fit `B`, with no swapping-dependent success claim.

### Shared RAM and disk semantics

The storage interface is a typed column with bounded `read_range(start, stop)`
and `write_range(start, values)`, explicit shape/dtype, and explicit close/flush
ownership. No caller retains a borrowed view past its range lifetime. The first
implementation uses NumPy-backed RAM columns and uncompressed canonical raw
columns on disk, as justified by the library evaluation. Whole-column RAM
allocation is allowed only when the complete worst-case plan fits `B`.

Bulk geometry processing consumes typed NumPy arrays in bounded batches. Use
DuckDB directly for staging, scans, ID association and ordering, with explicit
dtype, shape, lifetime, mutability and copy costs at the NumPy conversions.
NumPy interoperability does not imply that NumPy executes directly on database
disk storage. Verify bounded batch input/output without fetching whole results.

### Direct DuckDB processing

The user selected DuckDB and subsequently deferred the proposed backend
compatibility interface. S3/S4 use DuckDB connections, relations and SQL directly;
there is no initial generic backend protocol, opaque table-handle abstraction,
or second engine implementation. The measured path uses PyArrow batches for
DuckDB/NumPy interchange. Keep the independent PLY module's extraction boundary
and the defined NumPy numeric kernels.

Queries must preserve duplicate/missing-ID behavior, validity versus nonfinite
values, required row order and exact dtypes. Verify buffer ownership,
cancellation, cleanup and resource measurements for the concrete implementation.
Plan each complete association or ordering operation so a caller's batch loop
does not force a separate full-table query for every batch. Bounded output alone
does not establish bounded internal memory.

Canonical source snapshots, column encoding, identities and numeric operation
order belong to Scansor. Engine databases, IPC files and other staging are
replaceable working artifacts, rebuilt from canonical inputs when switching
engines. Backend names and execution measurements are execution provenance,
outside semantic artifact identity. A future engine switch will require changing
the DuckDB-specific processing and rerunning correctness/resource checks; a
compatibility layer can be designed then if useful. Polars reconsideration is
tracked in [issue #30](https://github.com/altendky/scansor/issues/30).

### Bounded disk access and processing passes

Disk access uses bounded reads into reusable buffers or explicitly closed mapping
windows with all alignment pages charged to the budget. Do not keep a whole-file
mapping alive while assuming its touched pages cost nothing. Do not rely solely
on garbage collection, advisory page eviction, or `memmap.flush()` to release
resident pages. A documented platform that cannot close windows reliably uses
buffered range reads. Both storage backends execute the same row and arithmetic
semantics.

The raw-column reference strategy processes the mesh in these passes. The initial
DuckDB implementation can own staging, coordinate association and external ordering;
implementing a separate custom run sorter is not required by this plan.

1. Snapshot/hash the source bundle and validate its bounded header and exact
   expected length. Decode every vertex in source order into canonical columns.
2. Decode all faces after the vertex payload. Validate count bytes and indices
   before any coordinate lookup. Fetch referenced coordinates using bounded
   range reads and a capped cache, even for adversarially scattered indices.
   Evaluate faces in bounded vectorized groups and write face results in source
   order. No full adjacency matrix or full `M x 3 x 3` coordinate gather.
3. For every in-range source corner emit a transient packed tuple
   `(<u8 vertex, <u8 face, u1 corner, <f8 allocated_area)`, 25 bytes. Area is zero
   for unusable faces. Sort bounded runs by `(vertex, face, corner)` and externally
   merge with bounded fan-in and buffers. Persist the run inventory if it cannot
   fit the control budget. Sort scratch and merge-heap memory are charged to `B`.
4. Stream merged tuples by vertex. Count all references; fold positive
   contributions in the defined order, carrying a vertex accumulator across any
   chunk/run boundary. Emit zero-reference vertices too. A RAM sort uses the same
   total ordering. No chunk subtotal or uncontrolled scatter reduction.
5. Scan vertex areas in source order for eligibility, `K`, and `S`, then scan
   again to write weights. Stream canonical digests and summaries. Verify
   `sum(reference_count) + out_of_range_corners = 3*M` and that exactly three
   positive tuples exist for every usable face. Replay remains necessary in
   addition to these counts.

The merge key includes unique source corner identity, so run size and sort
stability cannot change accumulation order. Sorting is an implementation method,
not a change to source order in persistent columns. Disk locality may make this
slower; it must not trigger sampling or geometry exclusion.

S3/S4 implement the selected DuckDB path, using this reference strategy and the
[library evaluation](full-resolution-mesh-library-evaluation.md#duckdb-from-source-through-output)
as validation evidence. The implementation must preserve all source rows, the required
numeric operation order, and canonical outputs. DuckDB is selected for initial
implementation; full input-to-output validation remains required beyond its
sorting API. The initial DuckDB probe demonstrates exact fixture round trips and a successful
spilled sort, while smaller engine limits fail in its three-join coordinate
lookup. The subsequent aligned DuckDB/Polars experiment gives both engines equal
byte-budget settings and OS envelopes: both complete the 1.2-million-vertex/face
fixture at the 256 MB engine setting, and Polars spills at a lower setting. These
observations support the initial choice but do not satisfy the scale gates.
In particular, OS cgroup completion differs from the proposed worker RSS bound. The later
[isolated sort and headroom comparison](full-resolution-mesh-library-evaluation.md#isolated-sorting-and-additional-memory-headroom)
finds that DuckDB completes 24 million corner-shaped records under a 512 MiB
cgroup cap; Polars completes with 2 GiB headroom, reaching 1.62 GiB RSS, while
its tested sort path still materializes data. The 512 MiB stress target does not
by itself disqualify a backend: any future comparison must evaluate extra memory,
growth with input size, and alternate bounded ordering cost. Polars'
unstable batch API is acceptable with a comment requiring changelog review and
renewed boundary, cancellation, and resource checks on updates.

With normals present, canonical columns occupy `51*N + 21*M` bytes; without
normals, `39*N + 21*M`. At most `3*M` transient corner tuples occupy `75*M`
bytes per complete sort generation. Budget two such generations during merging,
plus the source snapshot, canonical columns, bounded working files, inventories,
filesystem overhead, and later display outputs. Delete an old run only after its
replacement is closed and verified. Estimate this disk demand before processing
and report actual peak allocated and logical bytes. Sparse allocation or apparent
free space is not a guarantee against later disk-full failure.

This disk estimate describes packed runs; a database-backed alternative needs
its own estimate including staged tables, engine temporary files, and conversions.

### Mutation, failure, and lifecycle

Open regular source files read-only and keep their handles anchored. Record
file identity, length, and change metadata before and after copying and after a
second streamed hash of the anchored source. Require both source passes and the
private snapshot to match. Detect replacement at the supplied path and fail;
no hard-link snapshot may share writable source bytes. A caller changing files
concurrently must provide a stable export. These checks detect ordinary mutation,
not a cryptographic guarantee against a hostile writer able to restore bytes
between checks.

Compute only from the private verified snapshot. Publication uses a new staging
directory on the destination filesystem, no overwrite, flushed and closed files,
verified inventories, and an atomic no-replace final publication mechanism. Seal
import and contribution stages independently: a complete import may survive a
later contribution failure, but must not advertise complete weights. Read-only
verification replays source decoding, numeric policy, exact columns, and all
child hashes; it never repairs or rewrites input artifacts.

Structural, integrity, unsupported-profile, resource, and execution failures are
distinct from successful analyses containing geometric exclusions. Failures
record stage, category, last completed row/range, and available diagnostic
context, but never a complete-result marker for partial outputs. Permission
errors, short writes, disk full, cancellation, source mutation, and OOM are not
point dispositions. The supervisor reports an abnormal worker exit if the worker
cannot write its own failure report.

Report phase, bytes read/total, vertices inspected/total, faces inspected/total,
sort/merge progress, memory usage/budget, and temporary disk usage. Emit progress
at least every five seconds during long phases without per-row logging. Counters
are monotone within a named pass; distinguish rereads from distinct source rows.

Clean only the run's owned temporary directory after closing handles. Optional
retention leaves an explicitly incomplete diagnostic directory. A later invocation
may identify stale staging directories but must not delete arbitrary user paths.
V1 has no resume protocol; restart from a verified snapshot or regenerate the
recipe. Resource failure never silently activates a quick/downsampled mode.

## Visual audit contract

### Authoritative and display artifacts

The import and contribution columns are authoritative. A bounded query by source
vertex/face range exposes all statuses, raw coordinates/indices, areas, weights,
source bindings, and sidecar degradation. A human summary links to that data and
reports how many records cannot be positioned in a 3D view.

Generate a separately inventoried `cloudcompare-audit-v1` export containing:

- `validity.ply`: every finite-position source vertex in original order, with
  disposition colors and scalar fields. Retain every usable face, remapping its
  corners to this view's vertex indices. Preserve source winding and multiplicity.
- `weights.ply`: the same vertices and usable faces, with an area-weight color
  view and both raw area and normalized weight scalar fields.
- `rejected-face-corners.ply`: one point per finite, in-range corner of every
  rejected face, in `(source face, corner)` order. Include face disposition and
  source vertex/face/corner identity. This makes rejected geometry inspectable
  without handing invalid triangles to a viewer that may silently discard them.
- `view-vertices.bin` and `view-faces.bin`: `<u8` source-index maps for the first
  two views, plus an inventory/legend describing fields, counts, omitted rows,
  coordinate transform, precision loss, and source/import/contribution IDs.

Nonfinite-position vertices have no invented display location. They remain in
the detailed result; report their count and exact source indices through bounded
queries. Likewise, report non-displayable rejected corners. All finite excluded
vertices appear in the first two views even if no valid face references them.
V1 emits all displayable points; a future reduced display must be explicitly
labeled and cannot claim to show every source point.

PLY output is binary little-endian with ordered `double x,y,z`, `uchar red,green,
blue`, then `float scalar_vertex_status`, `float scalar_normal_status`,
`float scalar_contribution_status`, `double scalar_raw_area`,
`double scalar_weight`, and four `float scalar_source_vertex_0` through `_3`
properties. Each source-index field holds a successive 16-bit digit, least
significant first; these integers remain exactly representable even in a viewer
using binary32 scalar storage. The rejected-corner view additionally has four
source-face digits, corner number, and face disposition. Do not put a large
integer ID into one floating-point scalar field.

Faces use `property list uchar int vertex_indices`; view size/index limits are
checked before export. Empty views have zero counts and are described in the
legend even if a viewer refuses to load them. Original normals stay in the
authoritative result and are omitted from v1 display exports: automatic viewer
normalization must not look like preservation of measured directions.

### Legend, precision, and round trip

Validity colors are `eligible=(40,170,80)`, `isolated=(255,165,0)`, and
`no-usable-area=(220,50,50)`. Invalid normals are a separate selectable scalar
field and do not overwrite contribution colors. Nonfinite positions are listed
as `not spatially displayable`. Rejected-corner clouds use face-disposition
codes with colors `1=(180,0,180)`, `2=(220,50,50)`, `3=(255,165,0)`, and
`4=(80,120,220)`. These codes/colors are not physical error severity.

For the weight view, excluded vertices are `(128,128,128)`. For eligible points,
let `r=RN(a_i/max_eligible_area)` and set color `(t,t,255-t)`, with integer
`t` obtained by ties-to-even rounding of `RN(255*r)`. Thus the color ramp represents
raw area relative to its maximum, while scalar fields retain raw area and
normalized weight. Record ramp limits and the policy; no automatic percentile,
clipping, log transform, or residual scale is applied. An all-excluded view uses
gray with an explicit empty eligible population.

Default display coordinates are the source coordinates widened exactly to
binary64. An explicit optional display-only origin and power-of-two scale use
the ordered numeric profile and are recorded with an inverse transform. Viewer
global shift/scale choices are execution evidence, never calibration. Record
viewer version/build, import property mapping, shift/scale, scalar precision,
renamed/dropped fields, face loss, and coordinate/scalar differences on resave.
An exporter writing doubles cannot guarantee a viewer retains double precision.

The first integration gate loads the small generated views in CloudCompare,
selects every scalar field, inspects exclusions and usable faces, saves PLY,
and compares the resaved data with the original by the split source IDs.
Require exact ID digits and disposition codes, all displayable vertices, and
face multiplicity/winding after remapping. On the small exactly representable
fixture require exact coordinates and simple weights. A separate precision
fixture records rounding and scalar underflow/overflow or property loss against
the original; unsupported preservation must be visible and blocks the affected
capability claim.

The companion face map binds the original export's face order. If a viewer
reorders identical duplicate faces, their individual source face IDs cannot be
recovered merely from identical vertex triples; explicitly report that loss.
Round-trip validation of their topology and multiplicity is weaker than retaining
each face identity. Viewer edits are never accepted as authoritative import data.
The current source importer need not accept these additional display properties;
the display verifier is a separate narrowly specified reader profile.

CloudCompare is a separately installed GPL-licensed external viewer, explicitly
requested by issue #29, not a linked or bundled Scansor dependency. Its
[license][cloudcompare-license] must remain visible when the integration is
considered. No CloudCompare source is needed to implement these display files.

## Verification and implementation sequence

### Small golden example

The `right-triangle-orphan-v1` recipe has four vertices, in order:
`(0,0,0)`, `(3,0,0)`, `(0,4,0)`, `(9,9,9)`, no normals, and one face `(0,1,2)`.
Use the generator header convention above with `N=4`, `M=1`; header size is 200
bytes and complete PLY size is 261 bytes. Its area is exactly 6, reference counts
are `[1,1,1,0]`, vertex areas `[2,2,2,0]`, weights `[1,1,1,0]`, and contribution
statuses `[0,0,0,2]`. All positions are finite and the face is usable.

These byte hashes were calculated directly from the specified small records
using Python's explicit little-endian `struct` encoding during this design.
S2 now verifies them independently and through its numeric/recipe primitives;
they are not yet importer implementation evidence:

| Artifact | SHA-256 |
| --- | --- |
| `observations.ply` | `6e44511085b23715f96d93dd4bfe338b754c2b24627896efc9ab15e58fb02c5a` |
| `xyz.bin` | `9b095dbdf0ce7a2e05baf0f6d0eda62f13bf9a7afc39575fb4793d15953a1819` |
| `triangles.bin` | `ad5dc1478de06a4c2728ea528bd9361a4b945e92a414bf4d180cedaaeaa5f4cc` |
| `reference-count.bin` | `1f6e031dd33eed75757df11b845fddc49b3e2f1604f9d7b19c9ae32be8d838c2` |
| `face-area.bin` | `3e6357a56fbae74413051d518261f4b70e5b3758172a70e7f101e996e00a9ee0` |
| `contribution-status.bin` | `433ebf5bc03dffa38536673207a21281612cef5faa9bc7a4d5b9be2fdb12cf1a` |
| `vertex-area.bin` | `bbabd9fcaeacacaf62cdc0f08da3f5a75228b9fc963f5ed0dd94bf736e2812a1` |
| `weight.bin` | `be21622c7a8e0bacaf6fd7e6a3121384467dd031da3e3d9d9ad0b18f6cf38c5b` |

Additional small cases must cover unequal adjacent areas, irrational areas,
repeated/reversed triangles, coincident distinct vertices, collinear triangles,
no faces, all-invalid surfaces, orphan vertices, invalid normals, NaN payloads,
infinities, negative zero, invalid indices, and disposition precedence. One
independent unequal-area example uses `(0,0,0),(3,0,0),(0,4,0),(0,0,8)` with
faces `(0,1,2),(0,1,3)`: areas are 6 and 12, vertex areas `[6,6,2,4]`, and ideal
weights `[4/3,4/3,4/9,8/9]`, rounded by the specified operations.

Test byte truncation inside each header/property/vertex/list field, wrong count
bytes, matching-size malformed lists, trailing bytes, unsupported properties,
count/offset overflow, malformed/oversized XML fragments, source mutation during
copy, artifact tampering, stale staging, cancellation, and short writes. Missing
rows replaced by duplicated rows must fail even if totals match. Include source
changes followed by self-consistent rehashing of only the derived artifacts.

Use byte-reader boundaries `1,7,13,23,4093`, array chunks `1,2,7,127`, and
boundaries through high-valence vertex accumulation and merge runs. Compare RAM
and disk columns, every ordered digest, and final identities. Hypothesis may
discover/shrink additional cases; retain important failures as explicit recipes,
not a dependency on Hypothesis's future generation sequence.

### Platform and stress gates

The numeric/recipe verification matrix is Linux x86-64, Windows x86-64, and macOS arm64
with CPython 3.12 and 3.13 and explicitly recorded locked dependency versions.
Cross-endian encoding is additionally tested using byte-swapped arrays. For each
matrix member compare the same committed expected PLY bytes, raw Philox vectors,
canonical columns, controls, and weights, across budgets/chunks/backends. Include
default and baseline CPU-dispatch configurations where available. Record exact
OS/build/CPU/runtime versions in the evidence. S2 adds the CI matrix described
above for the primitives; full-pipeline budgets/backends remain later gates.
A passing single-host probe does not complete the matrix or establish a
product-support commitment.

Opt-in stress recipes use the grid above with seed 7, first noiseless and then
`b=3,q=-8`. They must distinguish vertices from triangles:

| Grid | Vertices | Triangles |
| --- | ---: | ---: |
| `W=3000,H=2000` | 6,000,000 | 11,990,002 |
| `W=10000,H=6000` | 60,000,000 | 119,968,002 |

Freeze expected hashes from independently checked generators before interpreting
stress outcomes. Add a deterministic source-index permutation and reordered face
recipe to defeat locality, plus a high-valence fan and duplicate/degenerate
variants. Run the same workload at 2 GiB and 512 MiB. Both must finish with
identical canonical results, complete accounting, and measured memory within the
requested budget. For the larger workload, use disk storage under both budgets;
small CI exercises RAM/disk equivalence without requiring a 60-million-row RAM
allocation.

Record per-phase elapsed time, peak RSS and platform measurement method,
baseline/peak allocation diagnostics, logical and allocated temporary disk,
bytes read/written, row/category counts, and artifact hashes. No runtime guarantee
is selected; report regressions rather than trading away points or precision.
Generate and clean large files outside Git. Ordinary CI never downloads the real
specimen or runs these opt-in stress workloads.

The private specimen is an optional integration check after generated gates:
recheck its manifest hashes, inspect all 2,894,759 vertices and 5,789,514 faces,
retain unknown units and sidecar ambiguity, report all dispositions, and perform
the visual audit if the viewer is available. Do not infer physical accuracy or
the cause of the UI/export vertex-count difference.

### Bounded implementation slices

Each row assigns ownership of requirements, not a person or an automatically
created ticket. Dependencies are explicit; a later slice must not imply an
earlier gate passed merely because its API exists.

| Slice / owner | Depends on | Deliverable and acceptance gate |
| --- | --- | --- |
| S1: isolated PLY I/O | This design | Python/NumPy-only reader/writer boundary, profile metadata, bounded range API and errors; independent small golden/malformed cases, resource-aware reads, and extraction test; no Scansor imports or excluded dependency/source reuse |
| S2: numeric and recipe core | S1 | Explicit Philox key/counter recipes, exact dyadic encoding, ordered binary64 kernel and independent rounding oracle; frozen small hashes and cross-platform conformance; preserve existing generated fixture revisions |
| S3: source snapshot and import storage | S1, S2 | Source/sidecar binding with replaceable defusedxml adapter, arbitrary units, raw columns, RAM/disk NumPy range interface, mutation detection, row dispositions, budget allocator and progress; direct DuckDB processing with bounded PyArrow/NumPy interchange, identity equality, exact inventories, injected I/O/resource failure tests |
| S4: full contribution and replay | S3 | Bounded coordinate association and contribution ordering with the selected S3/S4 backend, area policy, counts/digests, detailed range queries, atomic stage publication, read-only replay; unequal-area/orphan/duplicate and omission/tampering gates, identical results across chunk/budget settings |
| S5: visual audit | S4 | Full finite-point views, usable faces, rejected-corner view, split IDs/maps, legends and degradation inventory; actual small CloudCompare load/select/save/compare evidence including precision-loss case |
| S6: scale and resource evidence | S4; S5 for visual measurements | Six-million and sixty-million vertex recipes, adversarial locality/valence, both memory targets, phase timing/disk/complete accounting; optional private specimen; all failures explicit |

S1's foundational decisions are resolved: profile, language/NumPy boundary,
ownership, licenses, layout/range semantics, errors, and generated byte oracle.
It does not depend on an upstream library accepting changes. S2 selects and
verifies exact supported runtime builds; S3 measures allocator/cache overhead;
S5 pins the tested viewer build. Those are owned evidence tasks, not permission
to weaken the specified contract.

Later solver integration owns training/held-out-safe area support, model mapping,
activation and objective-weight semantics. Later robust/importance stages own
component composition, exclusions, iteration history, and threshold policy.
Later adapters own double-coordinate and nontriangle profiles. Compression and
alternate storage engines require their own resource and canonical-content gates
described in the library evaluation. Calibration and publication remain separate
project tracks. None of these deferred features blocks S1.

[issue-29]: https://github.com/altendky/scansor/issues/29
[pr-28]: https://github.com/altendky/scansor/pull/28
[numpy-sum]: https://numpy.org/doc/stable/reference/generated/numpy.sum.html
[philox]: https://numpy.org/doc/stable/reference/random/bit_generators/philox.html
[numpy-random]: https://numpy.org/doc/stable/reference/random/compatibility.html
[cloudcompare-license]: https://github.com/CloudCompare/CloudCompare/blob/master/license.txt
