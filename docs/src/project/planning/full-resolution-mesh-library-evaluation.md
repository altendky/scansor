# Full-Resolution Mesh Library Evaluation

## Status and selection

**Research findings and provisional choices, 2026-09-10.** This review addresses
the [library-evaluation comment on issue #29][issue-comment]. The
[ingestion contract](full-resolution-mesh-ingestion.md) owns all numeric,
accounting, identity, storage-interface, and verification semantics. This page
records why its first implementation uses particular building blocks. It adds
no application dependencies or integrations.

The initial selection is an isolated Python/NumPy PLY reader/writer, NumPy numeric
columns with buffered or windowed raw disk storage, explicit Philox raw bits,
and no canonical compression. **The user selected DuckDB as the initial bulk-
processing backend** and directed the initial implementation to use it directly,
deferring the proposed backend compatibility interface. The measured path uses
PyArrow for batched NumPy interchange. Engine staging remains
temporary; canonical artifacts and numeric rules remain application-owned.
Implement DuckDB first and defer a Polars adapter. This is an implementation
direction, not a completed integration or validated production memory guarantee.
defusedxml is selected for future sidecar parsing
behind a small replaceable adapter. Hypothesis is selected for future developer
property checks; psutil for future runtime resource telemetry; Memray remains
an optional development profiler. Existing libraries remain candidates for later
storage, topology, compression, and tabular-export needs identified below.

Follow-up direction: leave SoftFloat out of this work and defer Numba until a
measured bottleneck warrants optimizing owned code. Keep NumPy-compatible bulk
arrays at the processing boundary and validate the selected backend across the
complete proposed data path. The aligned DuckDB/Polars
experiment below establishes two viable fixture paths with different resource
costs; it does not establish production readiness. The user accepts Polars' unstable
batch API with an adapter comment requiring changelog review and renewed boundary
checks on updates.

The subsequent isolated sorting experiment finds a material memory difference:
DuckDB completes a 24-million-record sort under a 512 MiB cgroup cap; Polars
completes with additional headroom, reaching 1.62 GiB RSS under a 2 GiB cap. The
512 MiB failure is a resource observation, not an automatic backend veto. The
initial DuckDB choice reflects its demonstrated larger-than-RAM sorting path.
[Scansor issue #30][polars-followup] records the Polars limitation, additional
scaling evidence and upstream work to review before reconsidering that backend.

These choices follow the representative access pattern: immutable source bytes,
sequential decoding and hashing, bounded random coordinate gathers, external
sorting of face-corner contributions, and repeated sequential column scans.
There is no initial object-store requirement, topology repair, public query
interface, or compressed archival format. The review does not establish that a
custom implementation is generally superior to the compared libraries.

## Licensing and evidence discipline

The user directed Scansor to avoid GPL, LGPL, and AGPL dependencies for now and
explicitly flag every consideration of one, including transitive dependencies.
`plyfile` is excluded because of its declared GPL licensing. Its source must not
be consulted, copied, translated, or ported for Scansor. No application code was
added during this design. The reader's extraction boundary is specified in the
contract and recorded in [AGENTS.md](../../../../AGENTS.md).

Primary project licenses inspected for eligible candidates include
[NumPy's BSD terms][numpy-license], [Zarr MIT][zarr-license],
[h5py BSD and bundled notices][h5py-license], [Arrow Apache-2.0][arrow-license],
[trimesh MIT][trimesh-license], [Open3D MIT][open3d-license],
[meshio MIT][meshio], [python-zstandard BSD][zstandard-license],
[Blosc BSD][blosc-license], [numcodecs MIT][numcodecs],
[psutil BSD][psutil-license], and [Memray Apache-2.0][memray-license].
[Hypothesis is MPL-2.0][hypothesis-license], which is distinct from both
permissive licenses and the specifically excluded GPL/LGPL/AGPL families; it is
considered here as a developer tool under the existing project guidance.

Zstandard's underlying project offers a [BSD license option][zstd-license]
alongside GPL; evaluation uses BSD. CloudCompare is a [GPL-licensed][cc-license]
separately installed viewer explicitly requested by the issue. Exporting PLY for
that external tool does not select its code as a Scansor dependency. Both license
facts were flagged to the user. Adding a linked/bundled viewer would require a
new decision. Recheck the exact distribution and optional/native dependencies
when adopting or upgrading a package; a top-level license is not an inventory
of every component shipped by a wheel.

The XML comparison includes standard-library ElementTree, PSF-licensed
[defusedxml][defusedxml], and [lxml][lxml]. lxml's [license inventory][lxml-licenses]
lists LGPL-2.1 `iconv` in official binary wheels and a GPL test runner alongside
its BSD core. These exclusions were flagged to the user; do not adopt the usual
wheel distribution under the current policy. A differently built artifact would
need its own verified component inventory. DuckDB's core is [MIT][duckdb-license];
the temporary probe installed its default wheel without extras, with automatic
extension installation/loading disabled. The wheel declares no mandatory Python
dependencies; its core license is MIT and its experimental Spark compatibility
directory carries Apache-2.0 notices. These checks are not a complete native
component inventory for future distribution. No project dependency was added.

Additional bulk-processing candidates include [Polars under MIT][polars-license]
and [public-domain SQLite][sqlite-license], through Python's standard-library
interface. The temporary Polars install contains `polars` and `polars-runtime-32`
1.44.2 without extras; both wheel license files declare MIT. This is not a
complete inventory of bundled native components. Polars remains deferred;
DuckDB is selected for future implementation but has not been added to the
project environment by this design work.

Official documentation supports capability findings, while the local probes
below support only their recorded narrow observations. Version numbers describe
the inspected environment, not future support guarantees. Maintenance evidence
is the upstream documentation, release metadata, and published platform policy;
maintainer responsiveness and future releases were not measured. Every selected
wrapper still needs strict boundary typing and the contract's implementation
gates. [Issue #30][polars-followup] is a Scansor follow-up; no upstream Polars
issue or proposal was filed by this work.

## Decision matrix

### Adoption and maintenance snapshot

**Checked 2026-09-10.** [PyPI Stats for DuckDB][duckdb-pypistats] displays
60,787,626 downloads in its last-month window;
[Polars][polars-pypistats] displays 63,340,166. These are top-level package
downloads, not unique users; do not add Polars runtime-package downloads to its
total. [PyPI Stats][pypistats-faq] excludes known mirrors from aggregate counts
but includes CI/CD traffic and does not cover all package distributors. The
similar totals support substantial Python adoption for both, not a meaningful
market-share ranking between them.

Release frequency was calculated from the available
[DuckDB][duckdb-pypi-history] and [Polars][polars-pypi-history] PyPI JSON metadata,
using each version's earliest file upload timestamp, once per version. Windows
end at 2026-09-10 23:34 UTC. A normal release excludes prerelease/development
versions and versions whose files are all currently yanked; yanked versions are
reported separately, not silently counted as usable releases.

| PyPI measure | DuckDB | Polars |
| --- | --- | --- |
| Current normal release | 1.5.5, uploaded July 22 | 1.44.2, uploaded September 9 |
| Normal releases in 90 days | 3 | 5 |
| Normal releases in 180 days | 6 | 10 |
| Normal releases in 365 days | 12 | 18 |
| Additional currently yanked versions first uploaded in 180 days | 0 | 5 |

Polars' five yanked versions in the 180-day window are 1.39.2, 1.41.0, 1.43.0,
1.43.1 and 1.44.0. PyPI gives a missing lockfile as the reason for 1.39.2 and a
`when/then/otherwise` regression for 1.44.0; the other three have no reason in
that metadata. Yank counts alone cannot compare defect rates: release volume,
withdrawal policies and bug severity differ. Both projects are active; release
frequency is evidence of cadence rather than a responsiveness guarantee.

DuckDB has a [published calendar and LTS policy][duckdb-releases], currently with
one year of community support for LTS releases. Polars documents
[versioning, deprecation and unstable-API rules][polars-versioning]. Given the
user's acceptance of updating adapters, Polars' faster release/withdrawal cycle
calls for deliberate update checks rather than exclusion.

Both have company-backed engineering. On August 26, 2026,
[DuckLabs announced it would join AWS][duckdb-aws], with the projects retaining
MIT licensing and DuckDB Foundation stewardship; do not describe the engineering
company as still independently owned based on older sources. Polars announced
[an EUR 18 million Series A in September 2025][polars-funding], with priorities
including the open-source streaming engine and commercial distributed/cloud
execution. Funding and stated commitments do not establish future support
quality. Practitioner accounts from [Decathlon][polars-decathlon] and
[DB Systel][polars-db-systel] provide concrete Polars production-use examples.

The workload fit is more useful than a popularity winner. DuckDB's published
work on [external sorting][duckdb-external-sort] and its current larger-than-
memory join/sort documentation align directly with relational staging and
ordering. Polars' focus on DataFrame transformations and streaming also aligns
with batch numeric pipelines. Neither's popularity demonstrates efficient
scattered retrieval by ID in this application: compare bulk joins with bounded
direct array gathers. In particular, [DuckDB indexes][duckdb-indexing] are aimed
at selective access and do not automatically accelerate bulk joins. The local
aligned experiment and the remaining scale gates still decide technical fit.

### Storage and numeric processing

| Candidate | Correctness, identity, and determinism | Memory and access behavior | Integration cost / disposition |
| --- | --- | --- | --- |
| NumPy arrays plus raw columns | Exact dtype/order/bit encoding under explicit rules; no mesh repair; plain column bytes can be canonical | Range reads into NumPy buffers; mapping requires explicit window lifetime and page accounting; gathers/sorts allocate scratch | Already a project dependency with typed ndarray support; **select a thin bounded column wrapper** for S3, not unrestricted whole-file mapping |
| Zarr 3 | Typed arrays and exact numeric content; metadata, chunks, sharding, and codecs are a separate encoding identity | Chunk reads/writes, async concurrency and codec buffers; scattered coordinates can amplify chunk reads; shards trade object count for write size | Typed Python API plus native codec stack; **defer as an alternate backend** until compression/object storage or array hierarchy justifies it; do not infer a process cap from chunk size |
| HDF5 through h5py | Explicit dtype and selections preserve numeric content; container bytes depend on layout/metadata and are not canonical here | Hyperslabs and `read_direct` can fill reusable buffers; raw-data chunk caches are per dataset, plus HDF5 metadata/cache costs | Mature native HDF5 stack and platform wheels, with additional build/runtime and typing seams; **defer alternate backend** pending a single-container or HDF5 integration need |
| PyArrow IPC / Parquet | Explicit fields, validity, and integer IDs; distinguish NaN from null; physical batch/page layout is not canonical identity | Batches and memory mapping help reads; NumPy conversions may copy; random mutable vertex accumulation is not its primary abstraction | **Use PyArrow for the tested DuckDB/NumPy batch interchange**; canonical IPC/Parquet storage and broader tabular export remain deferred; this adds a C++ runtime |
| DuckDB staging / sorting | Explicit integer identities and total ordering can represent corner records; geometry, ordered accumulation, and canonical encoding stay application-owned | Disk-backed tables, joins and external sorting; batch conversions and allocations outside its memory limit need whole-process measurement | **Selected initial processing backend for S3/S4, used directly**; validate the complete path, including joins and failure handling; defer a generic compatibility layer |
| Polars lazy processing | Typed columns and NumPy interchange; exact fixture path verified alongside DuckDB; preserve explicit IDs, order, and owned arithmetic | Batch output and spill work; tested 24-million-record sort needs 1.62 GiB RSS and fails under a 512 MiB cgroup cap | **Deferred alternate backend**, tracked in issue #30; unstable API accepted with update review; reconsider bounded ordering and memory growth |
| SQLite through `sqlite3` | Integer keys, SQL ordering and exact BLOB storage; ordinary REAL fields do not preserve all source float distinctions | Disk tables and temporary indexes; standard Python API materializes rows as Python values before NumPy conversion | **Lower-priority bulk candidate**; possible index/metadata role, but row conversion and extra encoding weaken its fit as the numeric working store |
| Vectorized NumPy area arithmetic | Can explicitly preserve the contract's operation sequence and source order; no topology mutation | Bounded independent face arrays and explicit scratch; ordered accumulation remains separately controlled | A short owned kernel over an existing dependency; **select for S2/S4** with independent rounding oracle |
| trimesh geometry | Area/connectivity/repair facilities are useful; object construction can process or merge geometry unless disabled; exact IDs need tests even with `process=False` | Mesh objects, gathered triangle arrays, and caches must be charged; no full-pipeline budget established | Lightweight minimal NumPy install, optional dependencies expand costs; **defer substantial topology algorithms** to a new comparison; not needed for the short area kernel |
| Open3D geometry | Rich mesh validation and processing; identity must be checked at import and after each operation, with post-processing/repair disabled | Native mesh/tensor storage can introduce copies; no bounded full-data pipeline inferred from its reader API | Larger compiled CPU/GPU ecosystem and platform constraints; **defer** until a needed geometry algorithm warrants that integration |

Supporting documentation: [NumPy mapping][numpy-memmap],
[Zarr chunks/concurrency][zarr-performance], [h5py datasets][h5py-datasets] and
[cache controls][h5py-files], [Arrow memory/I/O][arrow-memory],
[trimesh constructor and methods][trimesh-api] and [installation][trimesh-install],
and [Open3D mesh reading][open3d-read]. These APIs provide useful components;
none supplies Scansor's accounting, ordered area reduction, or total memory cap.

The first raw-column backend is selected for its fixed layouts and simple bounded
range access, not because the other containers failed exact numeric round trips.
It avoids a new container dependency while keeping the column interface suitable
for replacement. If an alternate backend is added, canonical little-endian
column content and control bytes remain independently reproducible. Container
inventories and compression settings are separate storage metadata; changing
them cannot redefine point identity, disposition, or weight.

### Parsing, generation, output, and tooling

| Candidate / area | Evidence and limitations | Disposition and owning gate |
| --- | --- | --- |
| Isolated Python/NumPy PLY I/O | The first profile has bounded metadata and fixed record widths; generic reader types and errors can remain free of application imports | **Select S1**, under the user's extraction direction; owned format fixtures and an extraction test, no `plyfile` source reuse |
| ElementTree / defusedxml | ElementTree is the standard-library tree API; defusedxml supplies compatible parsing with explicit DTD/entity/external-reference prohibitions | **Select defusedxml for S3** under the user's direction, behind a replaceable adapter; verify the bounded fragment profile with no custom XML grammar |
| lxml | Mainstream ElementTree-compatible API with XPath, XSLT and schema facilities; its distribution includes license exceptions described above | **Do not select current official wheels** under the license policy; the initial metadata profile does not require its additional XML facilities |
| Numba | Compiles owned Python/NumPy numerical functions; adds compiler integration and does not provide the mesh algorithm | **Defer** until profiling establishes a bottleneck; no initial dependency or implementation requirement |
| Berkeley SoftFloat | Previously discussed as a possible independent numerical test reference | **Excluded from this work by user direction**; do not add a binding or native build |
| meshio PLY I/O | Multi-format mesh and point/cell-data APIs; public `read`/`write` interfaces operate on a mesh object; bounded range access and arbitrary scalar preservation are not established | **Defer runtime adoption**; useful eligible independent interoperability candidate for S5 small-file checks |
| trimesh PLY I/O | Documented scalar attributes and export choices; writer returns complete bytes, and mesh processing/attribute conversion must be controlled | **Defer runtime adoption** for full exports; compare small generated files in S5 if it helps expose interoperability problems |
| NumPy Philox raw bits | Explicit counter/key addressing; stronger integer-stream contract than high-level distributions; small chunk/random-access probe passes locally | **Select S2** using one full raw block per source row, explicit integer/dyadic geometry, and cross-platform golden vectors |
| NumPy PCG64 / high-level `Generator` | Explicit bit generators are preferable to an implicit default, but state advancement and distribution call shape need contracts; a seed alone does not bind float transforms | **Not selected** for this recipe; no reason to add a second algorithm or unspecified normal distribution |
| SHA-256 addressed random words | Standard-library availability and direct addressing; would require an owned random-construction/distribution convention and more hashing work | **Not selected**; explicit Philox already supplies the needed counter-based primitive; no performance comparison is claimed |
| python-zstandard | Streaming compression and memory-size controls exist; exact decoded content differs from an across-version guarantee for encoded bytes | **Defer canonical compression**; later optional transport/cache uses established codecs, fixed settings and separate compressed/uncompressed hashes |
| Blosc / numcodecs | Established shuffle/compression pipeline for numerical buffers; block size, shuffle, codec, threading and codec buffers need explicit settings | **Defer** to alternate compressed storage; no custom compressor and no assumed byte stability across versions |
| Hypothesis | Generates and shrinks adverse cases; reproduction is version-sensitive and is not a permanent fixture-byte format | **Select developer checks** in S1-S4; retain explicit minimal recipes/goldens for lasting evidence |
| psutil | Cross-platform RSS/process telemetry; sampled observations can miss short-lived peaks and fields differ by OS | **Select runtime telemetry** in S3 and external sampling in S6, supplemented by kernel peak counters |
| Memray | Native allocation attribution helps find NumPy temporaries; profiler changes runtime/memory, supports Linux/macOS, not native Windows | **Optional developer tool** for S3/S6 investigations; no production dependency or sole memory-acceptance measurement |

Supporting documentation: [meshio I/O][meshio], [trimesh PLY API][trimesh-ply],
[Philox][philox], [NumPy random compatibility][random-compatibility],
[Zstandard compression APIs][zstandard-api], [Blosc design][blosc],
[Hypothesis reproducibility][hypothesis-settings], [psutil][psutil],
[Memray native tracking][memray-run] and [platform limits][memray-platforms].
S1's isolated writer must support bounded output; a convenient library call that
materializes the complete export does not satisfy that requirement.

### XML API and support fit

[ElementTree][elementtree] provides the tree, attributes, namespaces, and traversal
needed for the small `.rsInfo` profile. Its inclusion in Python makes it the
baseline API. [lxml][lxml] is the mainstream third-party comparison for richer XML
work, subject to the license restriction above. Its additional facilities do not
remove the need to interpret vendor fields conservatively.

defusedxml's ElementTree adapter uses the established standard parser and adds
explicit rejection controls. It is a dependency of [Jupyter nbconvert][nbconvert],
but its latest published release is March 2021 and PyPI lists one maintainer.
That is narrower maintenance capacity than a broad XML ecosystem; neither
popularity nor an old release alone proves compatibility or responsiveness.
The user selected `defusedxml.ElementTree` for initial implementation. S3 verifies
supported Python versions, fragment wrapping, duplicate fields, DTD/entity
rejection, and bounded allocation. Explicitly set `forbid_dtd=True`,
`forbid_entities=True`, and `forbid_external=True`; retain the size limit and do
not invoke XInclude processing. Keep parser imports, exceptions, and tree objects
inside a small adapter that returns owned sidecar records. This lets a future
parser replacement preserve the interpretation contract. The dependency is
selected for implementation but has not been added to the project environment.

### DuckDB from source through output

**Provisional design with focused local probe evidence below.** The probe covers
fixture decoding, staging, joins, geometry, sorting, NumPy conversion and source
PLY round trips; it is not the complete importer or a comparative benchmark.
DuckDB has an actively maintained [release and LTS policy][duckdb-releases]. Its
documented [inputs][duckdb-input] and [outputs][duckdb-output] support a batched
integration, but the proposed PLY/raw-column boundary still needs owned adapters.
The narrow candidate path is:

```text
PLY -> isolated decoder -> bounded NumPy geometry / corner records
    -> typed Arrow batches -> DuckDB ORDER BY(vertex, face, corner)
    -> Arrow batches -> ordered numeric fold -> canonical columns / audit PLY
```

[Arrow readers can supply input][duckdb-arrow], and `to_arrow_reader` supplies
bounded output batches. This path adds PyArrow as well as DuckDB; optional Arrow
support is not an existing project dependency. Interleaved PLY fields and packed
corner records can require copies into contiguous Arrow columns. Charge those
copies and the lifetimes of borrowed buffers. `fetchnumpy()` fetches the complete
result; it is unsuitable here. `fetchmany()` avoids Arrow at the cost of Python
tuple/scalar materialization, which needs a separate throughput/memory comparison.
A batch API by itself does not bound the query engine's memory.

NumPy compatibility is required at the bulk-processing boundary. DuckDB supports
[querying NumPy arrays][duckdb-numpy-input] and [returning NumPy columns][duckdb-numpy-output].
That is data interchange; a database table does not expose a writable disk-backed
`ndarray` on which NumPy operations execute directly. Keep explicit column dtypes
and source identities rather than packing mixed integer/float fields into an
implicitly promoted matrix. Test axis orientation and supported strides too.

For bounded output, iterate `to_arrow_reader(batch_size=...)` and expose each
numeric column with [Arrow's `to_numpy(zero_copy_only=True)`][arrow-array].
Compatible primitive columns without nulls can share a read-only buffer. Writable
arrays require copies; separate XYZ columns may need packing into an `N x 3`
working buffer. These are batch-local views, not live database storage, and their
owners must remain alive while used. Query execution and Arrow result creation
still allocate independently. Make any copy explicit and charge it to the budget.
NumPy computes on each batch; write results back explicitly through DuckDB.
Use DuckDB and Arrow directly in the processing code and pass typed NumPy arrays
to the numeric kernels. A generic backend wrapper is deferred. Validate this
entire path in S3/S4.

A broader candidate stores explicit vertex, face, and corner tables in DuckDB,
uses joins for coordinate association, and external sorting for contribution
order. This could replace staging, coordinate-cache machinery, and merge-run
management together. Preserve invalid indices with explicit dispositions and
outer joins as needed; inner joins must not silently erase bad input records.
Retain source ordinals and restore the full required order after joins, because
[joins and grouping do not preserve input order][duckdb-order]. Run canonical
geometry and ordered folds through the defined numeric kernel; ordinary SQL
floating-point aggregation is not a substitute for that arithmetic contract.

[Disk spill supports sorting and joins][duckdb-spill], but these operations can
still fail for memory reasons, and [the configured memory limit does not cover
all allocations][duckdb-memory]. Any broader path also needs round-trip checks
for integer widths, NaN versus null, float bits, and every source disposition.
Keep database bytes outside canonical identity and export the specified columns
and PLY through the owned writer. The packed-run disk estimate in the contract
does not apply to database staging and its temporary files.

DuckDB is the selected initial relational processing backend. Canonical raw
columns remain distinct from temporary database staging. S3/S4 validate the full
implementation, copies, identity checks, cleanup/cancellation behavior, peak RSS,
temporary disk, and runtime against the raw-column reference strategy. The
completed probes support starting this implementation, while its complete-path
acceptance gates still apply. Availability of `ORDER BY` alone does not establish
production readiness.

### Alternatives for the same processing role

Polars is the closest candidate for the relational middle of this pipeline.
Its [streaming engine][polars-streaming] supports larger-than-memory processing,
and its [NumPy conversion][polars-numpy] exposes numeric arrays with explicit
copy controls. One concrete path is PLY decoding into typed IPC/Parquet staging,
lazy scans and joins/sorting, bounded NumPy geometry, and the owned output writer.
The existing PLY adapter and numeric contract remain necessary. Native sinks can
write intermediate files; [collect_batches][polars-batches] can deliver batches
to Python, but is marked unstable and slower than native sinks. Inspect the
physical plan: some operations can fall back to in-memory execution. A streaming
setting is not evidence that the requested process budget is met.

The [aligned local comparison](#aligned-duckdb-and-polars-comparison) uses the
same source recipe, source identities, array dtypes, numeric kernel, final
hashes, engine budget values, and OS resource envelope. It includes file staging
and repeated crossings through NumPy. Both engines complete the larger fixture
at the matched 256 MB setting. The subsequent user decision selects DuckDB for
initial implementation, with Polars retained as a deferred alternative.

The unstable batch API is not a rejection criterion under the user's direction.
Keep a comment at any Polars batch/configuration adapter stating:

```python
# Polars batching and OOC controls are version-sensitive. On every update,
# review changelogs and recheck dtypes/bits, order, batch bounds, copies,
# buffer lifetime, cancellation, spill settings, and whole-process memory.
```

The probe uses `collect_batches`; [sink_batches][polars-sink-batches] provides
a callback with an early-stop signal. A native file sink is another integration
option, with its extra write/read cost included in any comparison.

SQLite supplies [disk-backed ordering/indexes][sqlite-temp] and an existing
[Python interface][sqlite-python]. That interface returns batches of Python rows,
so numeric buffers require conversion. Its [SQL numeric types][sqlite-types]
provide signed 64-bit integers and binary64 REAL values; BLOBs can retain exact
encodings with additional application handling. The focused check below found
NaN-to-NULL conversion in the normal REAL path. SQLite could manage keys over
separate raw numeric columns, but that splits ownership and needs evidence of
benefit. It is a lower-priority bulk alternative, not an ndarray backend.

Zarr and h5py remain alternatives for the array-storage role already evaluated.
They provide range/chunk storage while leaving coordinate association and
external ordering to another component. The initial raw-column baseline remains
provisional; library selection is based on the complete required role and its
measured adapters, not package count alone.

## Focused local design probes

### Exact content through four storage options

**Observed on Linux x86-64, CPython 3.12.13.** An isolated temporary environment
used NumPy 2.5.1, Zarr 3.3.0, h5py 3.16.0, PyArrow 25.0.1, psutil 7.2.2, and
python-zstandard 0.25.0. It did not modify the project environment or lockfile.
The probe exercised 4,099 rows with these independently constructed columns:

```python
i = np.arange(4099, dtype="<u8")
bits = ((i * 104729) % 0x7F800000).astype("<u4")
bits[:8] = [
    0,
    0x80000000,
    1,
    0x7F7FFFFF,
    0x7F800000,
    0xFF800000,
    0x7FC00000,
    0x7FC00001,
]
columns = {
    "xyz_component": bits.view("<f4"),
    "index": i + 2**53,
    "disposition": (i % 4).astype("u1"),
    "area": (i.astype("<f8") / 8).astype("<f8"),
}
```

This intentionally tests exact NaN payload and negative-zero preservation by
storage, before the import's separately specified canonicalization. High integer
IDs test loss that float-based identity handling could conceal. Each column's
raw bytes, not numerical equality with NaN exceptions, are the comparison oracle.

Write each raw/Zarr/HDF5 column in 113-row operations. For Zarr, use an
uncompressed v3 array, explicit dtype, fill zero, concurrency 1, and thread-pool
limit 2. For HDF5, use uncompressed chunked datasets, `track_times=False`, and
`rdcc_nbytes=1048576, rdcc_nslots=521`. For Arrow, write IPC file batches using
`pa.array(values, from_pandas=False)` and exact NumPy-derived field types.
Use storage chunks/batches of 127 and 1,024 rows; raw columns have no storage
chunks. Reopen each and read in ranges of 131 and 997 rows, splitting Arrow
batches as necessary. HDF5 uses `read_direct` into a reusable buffer; raw uses
read-only NumPy mapping; Arrow uses read-only mapping and zero-copy conversion
for these primitive arrays. Hash each reconstructed little-endian column stream.

All four backends, both storage layouts, and both read sizes matched these
expected SHA-256 values exactly:

| Column | SHA-256 |
| --- | --- |
| `xyz_component` | `b76195c7a876ef1449e69fc93301e9576943ab71cc335984c351267cda0d6b10` |
| `index` | `d46fe0f38f1c75819e690120545c28948d2c4390a39721bd334af2b3cb27a776` |
| `disposition` | `fea81bf8d889082bcde89e6891b8ff9185e5b188ccedae55d17495c2ca41d65b` |
| `area` | `9cc1994f544b26ea831d7e3c545c5cc84a3a76ece781311c96cc74238f30ed94` |

The raw payload totals 86,079 bytes. These observed stored sizes are deliberately
a small layout illustration, not a large-mesh performance comparison:

| Backend | 127-row storage: files / bytes | 1,024-row storage: files / bytes |
| --- | ---: | ---: |
| Raw columns | 4 / 86,079 | 4 / 86,079 |
| Zarr, one array per column | 136 / 89,975 | 24 / 109,488 |
| HDF5, one file per column | 4 / 101,995 | 4 / 121,504 |
| Arrow IPC, one file per column | 4 / 109,584 | 4 / 90,608 |

File counts include array metadata. Grouping HDF5 datasets into one file or using
Zarr sharding would change this comparison. Partial final chunks/padding and
metadata affect small-file overhead disproportionately. The first sandboxed
Zarr attempt stalled in its synchronous-to-async bridge; the same full probe
completed outside the sandbox. Its cause was not established, so this is an
environment limitation to investigate, not evidence that Zarr itself deadlocks.

The probe establishes local content preservation for these primitive columns,
not a memory ceiling, portable container bytes, cross-platform correctness,
large random-gather performance, or all missing-value representations. S3/S6 own
those gates for any adopted storage implementation.

### Compression and counter addressing

Concatenating the four columns in their displayed order, Zstandard level 3 with
zero compression worker threads, checksum enabled, and content size enabled
produced 26,058 bytes. Two one-shot compressions were byte-identical and decoding
recovered the exact 86,079 bytes. That is a same-environment observation, not a
portable compressed-byte guarantee. It does not justify canonical compression
or predict a real mesh's compression ratio. Future compression comparisons must
measure streaming buffers, peak memory, runtime, and independently hashed decoded
content on representative coordinate, connectivity, and disposition arrays.

With NumPy 2.5.1, Philox key `[7,0]` and counter `[0,0,0,0]` produced these first
four unsigned 64-bit words, in order:

```text
df4034b829e9fba4 4b9d10cdf8e64087 6b8b857e506aac98 67c7c945b1ba6e52
```

Drawing 19 four-word blocks together exactly matched separate row ranges
`[0,1)`, `[1,7)`, and `[7,19)` initialized with the corresponding starting
counter. This demonstrates the proposed local addressing convention, not the
cross-platform generation gate. S2 must freeze additional vectors, byte-encoded
dyadic fixtures, rounding edge cases, and the full proposed platform matrix.

### DuckDB data-path and resource probe

**Observed on Linux x86-64, CPython 3.12.13**, in the temporary environment above
with DuckDB 1.5.5. This fixture-specific script was kept outside Git and did not
use or inspect `plyfile` source. The generated data was removed after each run,
including failed runs. The probe uses one DuckDB thread, default insertion-order
preservation, explicit total `ORDER BY` keys, a 200 MiB temporary-directory limit,
and separate bounded input/output batches. No optional extensions were installed.

The generated fixture repeats six source vertices per block:
`(0,0,0),(3,0,0),(0,4,0),(0,0,8),(9,9,9),(NaN,0,0)`, as little-endian float32.
Its six local faces are `(0,1,2),(0,1,3),(0,1,2),(0,0,1),(0,1,5),(0,1,-1)`.
For physical face block `j`, the addressed vertex block is
`(104729*j + 17) mod block_count`; add its six-vertex offset to nonnegative
indices and preserve `-1`. This permutes locality while retaining face order.
The small check uses three blocks and seven-row batches. Larger runs use 40,000
blocks: **240,000 vertices and 240,000 faces**, not the future stress workloads.

The probe PLY uses LF throughout, the comment `issue-29-probe-v1` immediately
after `format binary_little_endian 1.0`, vertex properties `float x`, `float y`,
`float z` in order, and `property list uchar int vertex_indices` on faces.
Both element counts are `6*block_count`; each triangle has count byte 3, and
the source NaN encoding is `0x7fc00000`. No additional header lines or trailing
bytes are written. These details and the block recipe define its source bytes.

The path stages decoded NumPy columns through Arrow readers into disk tables,
round-trips every vertex and face back to identical PLY bytes, then uses three
left joins to attach coordinates in source face order. Unmatched references
remain explicit. NumPy evaluates geometry and emits packed corner records;
DuckDB sorts them by `(vertex, face, corner)`, then Arrow supplies read-only NumPy
views to the ordered scalar fold. The expected per-block reference counts are
`[7,6,2,1,0,1]`, areas `[8,8,4,4,0,0]`, and contribution statuses `[0,0,0,0,2,1]`.
Normalization uses this fixture's independently known `K=4*block_count` and
`S=24*block_count`; a production normalization scan was not implemented here.

Both successful large runs inspect 680,000 in-range corners and 40,000 invalid
corners, including 360,000 positive contributions. They agree on every recorded
hash despite different batch sizes and sorting memory. Source PLY SHA-256 is
`4c0763cc41c60ced011ec72fe283790986472cade9c4783b0f84e58e34431fa8`;
ordered vertex-area bytes hash to
`7af6948211ffe146b8a27ff10362090b165d4f860ce905478bc6a40dd4546b61`;
weight bytes hash to
`591eb4cee765c7ca302d421b8986f5036632aca189bdca17d93b090ca8e9222e`.
These are probe hashes, not new canonical recipe revisions.

| Engine memory limit: joins / sort | Rows per batch | Result | Observed peak RSS (MiB) | Sampled peak spill (MiB) | Peak allocated bytes for all working files (MiB) |
| --- | --- | --- | --- | --- | --- |
| 32 / 32 MiB | 4,093 | Explicit OOM during joins | 175.70 | 17.94 | 32.39 |
| 64 / 64 MiB | 997 | Explicit OOM during joins | 189.20 | 0 observed | 14.46 |
| 96 / 96 MiB | 4,093 | Pass | 205.82 | 0 observed | 30.67 |
| 96 / 16 MiB | 997 | Pass, with sorting spill | 192.67 | 10.59 | 41.27 |

RSS is the larger of Linux `ru_maxrss` and 10 ms psutil sampling; baseline after
imports was about 95 MiB. The two mechanisms differed slightly, so both were
retained. Disk figures are 10 ms samples over the probe directory, including
input, restored PLY, packed corners, database and spill files. Sampling can miss
short peaks. Successful runs took about 1.6 and 1.9 seconds respectively; these
are single local observations, not comparative or cross-platform benchmarks.
The engine limit is not the process limit. The failed three-join plans show that
spill alone does not ensure completion; S3/S4 must evaluate query decomposition
or a different coordinate-association path within the requested process budget.

A separate eight-row check preserved float32 bits through an Arrow-to-DuckDB-to-
Arrow/NumPy round trip, including negative zero, a subnormal, maximum finite
value, both infinities, and two quiet-NaN payloads. It also preserved consecutive
uint64 identities starting at `2^53`, using three-row output batches. Every
numeric view was read-only. This supports the interchange path on this build;
it does not prove arithmetic portability, complete memory enforcement, production
publication/replay, or CloudCompare audit output. The six-/sixty-million-vertex
tests remain owned implementation evidence tasks. The subsequent comparison
below supersedes any resource ranking inferred from unmatched engine settings.

### Aligned DuckDB and Polars comparison

**Observed on the same Linux/Python environment, 2026-09-10.** The retained
[measurement records](../../../../experiments/mesh-backend-comparison-v1-evidence.json)
contain versions, settings, timings, RSS, cgroup counters, spill/disk measurements,
output hashes, and hashes of the temporary probe scripts. Fixture-specific probe
scripts remain outside Git; this is design evidence, not an importer integration.
No `plyfile` source was consulted.

An initial comparison set a DuckDB engine budget but left Polars' spill budget
at its default, then terminated Polars on a sampled RSS threshold. It also set
`POLARS_TEMP_DIR` without setting Polars' separate OOC spill directory. That
comparison cannot support a backend ranking or an assertion that Polars cannot
spill. Its resource conclusion is withdrawn.

Inspection of the exact Polars 1.44.2 release found
[`POLARS_OOC_MEMORY_BUDGET_MB`][polars-config-source], parsed as integer decimal
megabytes and defaulting to effectively unlimited. Its memory-fraction setting
is marked unused in that version. The
[memory manager][polars-memory-source] uses the explicit byte budget as a spill
trigger against an estimate of allocations through Polars' Rust allocator.
That estimate is not total process RSS. These controls are not exposed through
the usual Python `Config` setters and require version-specific verification.
The corrected tests set them before importing Polars and set
`POLARS_OOC_SPILL_DIR` inside the measured working directory.

The aligned protocol is:

- Fresh workers run sequentially in separate Linux cgroup v2 scopes, created
  before importing either engine. Both get `memory.max=536870912` bytes and
  `memory.swap.max=0`. A small identical supervisor remains in each scope to
  record peak memory and OOM counters after the worker exits. A 90-second worker
  timeout applies equally; no run reached it.
- Both engines get one configured execution thread, the same 4,093-row input
  and output batch size, and engine budgets of **128,000,000** or **256,000,000**
  bytes. DuckDB receives exact byte values through `memory_limit`; Polars gets
  `POLARS_OOC_MEMORY_BUDGET_MB=128` or `256`. Each gets a **400,000,000-byte**
  engine spill allowance. Neither engine budget is presumed to cover the same
  allocations as the other, or to enforce the OS/process envelope by itself.
- Both use the block recipe above and the same fixture decoder, NumPy geometry,
  ordered fold, and output checks. Polars stages uncompressed Arrow IPC files,
  uses `scan_ipc(memory_map=False)`, three left joins, and explicit sorts. DuckDB
  uses its disk tables and equivalent joins/order keys. These staging differences
  are measured adapter costs in a complete-path comparison, not a comparison
  of isolated query-engine throughput.
- Polars returns batches with `collect_batches(chunk_size=4093,
  maintain_order=True, lazy=True, engine="streaming")`. Numeric Series become
  read-only NumPy views using `to_numpy(allow_copy=False)`; multi-chunk Series
  are explicitly rechunked within one output batch first. These copies, null
  replacement, and XYZ packing are included in the process measurements.
- All temporary files use the same filesystem. Each completed or failed run is
  cleaned before the next. At 1.2 million vertices and faces, each 512 MiB
  configuration is repeated twice, reversing engine order. File caches are not
  deliberately flushed, and these short runs are not statistical benchmarks.

The 240,000-vertex/face check passes in both engines at 128 MB. The larger recipe
uses 200,000 blocks: **1,200,000 vertices, 1,200,000 faces, and 3,400,000 in-range
corner records**. All completed runs reproduce identical source PLY bytes and
all seven recorded hashes. They match the fixture's per-row expected values,
including rejected faces, orphans, nonfinite coordinates, and duplicate faces.
Normalization still uses the fixture-known totals described above.

| Backend | Engine budget (decimal MB) | Result in both 512 MiB cgroup runs | Pipeline time (s) | Worker peak RSS (MiB) | Sampled spill peak (MiB) | All working files, peak allocated (MiB) |
| --- | --- | --- | --- | --- | --- | --- |
| DuckDB 1.5.5 | 128 | Explicit engine OOM during joins | 1.99–2.16 to failure | 290.97–293.59 | 66.81–71.47 | 137.30–141.96 |
| Polars 1.44.2 | 128 | Complete, exact outputs | 7.71–7.95 | 511.42–517.90 | 71.45–72.53 | 339.47–340.59 |
| DuckDB 1.5.5 | 256 | Complete, exact outputs | 7.48–7.50 | 417.91–449.93 | 19.19–19.81 | 151.55 |
| Polars 1.44.2 | 256 | Complete, exact outputs | 6.86–7.02 | 470.23–514.00 | 0 observed | 265.57 |

Pipeline time includes source generation, staging, source reconstruction,
geometry, sorting, folding and validation, but excludes interpreter/import time.
The records also retain worker wall time including imports. RSS uses the larger
of kernel peak and 10 ms sampling. Spill/disk samples may miss short peaks and
report peak occupancy, not total I/O volume. Polars' zero observed spill at 256 MB
does not imply zero file I/O; IPC staging is included in the working-file figure.

**Cgroup completion is not proof of the contract's RSS bound.** Cgroups account
charged memory, including file cache and the supervisor; worker RSS accounts
resident pages differently, including shared mappings. Every run has zero OS
OOM/kill events and zero swap, but the 512 MiB limit caused reclamation in several
successful runs. Polars exceeds 512 MiB RSS slightly in one repeat at each engine
budget. Its stricter RSS acceptance gate therefore remains unresolved. The
DuckDB 128 MB failures are engine-budget errors, not OS OOM kills.

One additional pair retains the matched 256 MB engine budgets but raises both OS
limits to 2 GiB. Both complete with the same hashes: DuckDB takes 7.23 seconds at
411.71 MiB peak RSS; Polars takes 6.73 seconds at 506.79 MiB. Charged cgroup peaks
are 501.26 and 691.26 MiB respectively. This separates the engine settings from
the tighter OS envelope; it is one observation, not the full 2 GiB stress gate.

A separate Polars boundary check preserves float32 negative zero, subnormal,
maximum finite, infinities, and two quiet-NaN payloads, plus consecutive uint64
IDs starting at `2^53`. NumPy views remain read-only and valid after the iterator
and frame references are released. The tested iterator lacks the `stop()` method
mentioned in its documentation. `sink_batches` stops after a callback returns
`True`, propagates an injected callback exception, and permits a subsequent query
to complete. Dropping an unfinished iterator also passes a process-exit smoke
check. None of these checks proves immediate cancellation while a sort is busy.

Source inspection also narrows the scale claim: in this release the
[sort plan][polars-sort-plan-source] uses an in-memory map. Its
[input sink][polars-sort-sink-source] can spill buffered frames but materializes
them again before sorting. Batch output and successful spill therefore do not
establish a bounded external sort for arbitrary input size. S3/S4 must measure
larger ordering stages or use a different ordering strategy; do not infer that
the batch interface itself is the obstacle.

On this fixture, Polars completes somewhat faster;
DuckDB's tested 256 MB path needs less working disk and less RSS. Polars completes
at an engine setting where DuckDB's current three-join plan fails, but the engines
account different allocations, so this is not a claim of lower total memory.
For future backend comparisons, use each backend's best measured configuration
under the same **whole-worker** budget, with adequate margin, identical output hashes,
adversarial locality/valence, disk limits, and failure/cancellation behavior.
Then compare total runtime and adapter/ordering implementation burden against the
raw-column baseline. DuckDB is now the selected starting point; unstable API
status is accepted with update review and was not the reason to defer Polars.

### Isolated sorting and additional memory headroom

**Observed 2026-09-10, Linux x86-64, DuckDB 1.5.5 and Polars 1.44.2.** The
earlier complete pipeline included sorting, but its largest corner table had
only 3.4 million records, or 85 MB of packed data. That was insufficient evidence
for sorting data larger than RAM. This follow-up isolates native Parquet scan,
three-key sorting, and bounded NumPy output. The
[retained evidence](../../../../experiments/mesh-sort-comparison-v1-evidence.json)
contains twelve runs, settings, timings, memory and spill measurements, output
hashes, and the temporary probe source snapshots. It adds no application code
or dependencies.

Both engines read the same uncompressed Parquet file with 65,536-row groups and
no dictionary encoding. The columns are `(v: uint64, f: uint64, c: uint8,
area: float64)`, totaling 25 packed bytes per record. For source row `i`, define
`q = (i * 1000003 + 17) % N`, with the multiplier coprime to each tested `N`.
Values are `v = 2**53 + q // 12`, `f = q // 3`, `c = q % 3`, and
`area = (q % 257) / 8`. Sorting by `(v, f, c)` must produce `q = 0..N-1`.
This exercises duplicate leading keys, exact uint64 identities above `2**53`,
and preservation of a numeric payload. Every output row is checked byte for
byte against the independent closed-form expected sequence; all successful
runs of the same size produce the same four column hashes.

The 2.4-million-record input is 60,000,000 packed bytes. The
24-million-record input is **600,000,000 packed bytes**, or 572.2 MiB, with a
672,480,203-byte Parquet file. Its packed content alone exceeds the initial
512 MiB cgroup limit. File hashes were rechecked after testing.

Workers run **one at a time**, with one configured execution thread per engine,
4,093-row output batches, a 90-second timeout, and swap disabled. Both engines
retain a **128,000,000-byte engine memory setting** and a
**4,000,000,000-byte spill allowance**. These settings do not cover identical
allocations or impose a process RSS cap. The spill allowance is higher than in
the earlier complete-pipeline probe so temporary disk capacity does not decide
this sort comparison. The source is generated before timing; all runs use the
same filesystem. OS file caches are not flushed or controlled.

DuckDB uses `read_parquet(...).order("v,f,c").to_arrow_reader(batch_size=4093)`.
Polars uses `scan_parquet(...).sort("v", "f", "c")` and the streaming
`collect_batches` adapter described above. Both convert numeric columns to
read-only NumPy views. Polars explicitly rechunks individual output Series when
required; those copies are measured and included in timing and RSS.

| Records | Backend | Cgroup cap | Result | Time to first batch (s) | Total time (s) | Peak worker RSS (MiB) |
| --- | --- | --- | --- | --- | --- | --- |
| 2.4 million | DuckDB | 512 MiB | Both repeats complete | 0.241–0.249 | 0.515–0.538 | 187.19–187.25 |
| 2.4 million | Polars | 512 MiB | Both repeats complete | 0.466–0.488 | 0.665–0.698 | 303.09–303.80 |
| 24 million | DuckDB | 512 MiB | Both retained repeats complete | 3.762–4.509 | 8.623–9.247 | 238.91–240.00 |
| 24 million | Polars | 512 MiB | Cgroup OOM before output | No batch | No completion | At least 900.17 sampled before kill |
| 24 million | DuckDB | 2 GiB | Complete | 2.909 | 6.658 | 240.28 |
| 24 million | Polars | 2 GiB | Complete | 6.167 | 8.280 | 1,663.75 |

Total time includes scanning, sorting, consumption, per-row verification,
hashing and connection cleanup; it excludes imports and fixture generation.
First-batch latency measures startup, scanning and sorting before any output
reaches the verifier. Neither number is a pure sorting-kernel benchmark.
Small runs were repeated in reverse engine order; the 2 GiB pair is one
observation per engine. These short local runs do not establish a general speed
ranking across workloads or cache conditions.

Polars' [memory-saving scan option][polars-parquet-scan] was also tested with
`low_memory=True, parallel="none"`. Under the same 512 MiB cap it still incurred
a cgroup OOM before returning a batch. A second control uses those scan settings
and native [`sink_parquet`][polars-parquet-sink], followed by bounded Arrow reads.
That route passes the 240,000-record control but also incurs a cgroup OOM on
24 million records, before the output-reading phase. These observations and
the pinned sort-plan source above support a limitation in this version's sort
execution, rather than a conclusion based only on the Python batch interface.
They do not establish that no future or alternative Polars ordering path can
handle the workload.

**The failures were caused by the cgroup limit.** The preserved counters show
the group's `memory.max=536870912`, peak at that value, local `oom` events, one
`oom_kill`, no swap and no timeout. The desktop notification uses generic
system-memory-shortage wording. Initially, the scope's default `OOMPolicy=stop`
also stopped the supervisor, losing its final report. Subsequent runs use
`OOMPolicy=continue` so the same small supervisor can save counters after the
worker is killed; every scope is stopped before proceeding. The incomplete
Polars runs with only journal evidence are excluded from the retained result
table.

At the user's request, the 2 GiB pair measures the amount of extra headroom
instead of treating a small target overshoot as disqualifying. Polars peaks at
**1.62 GiB RSS**, approximately **3.25 times** 512 MiB; its cgroup peak is
**1.57 GiB**. DuckDB peaks at **240.28 MiB RSS** and **636.48 MiB charged cgroup
memory**. Both have zero cgroup OOM or maximum-limit events in this pair.
Cgroups include charged file cache and the supervisor, while RSS includes shared
and file-backed mappings accounted differently. Consequently a 512 MiB cgroup
cap must not be described as a strict 512 MiB worker-RSS cap, and a killed
worker's last sampled RSS is only a lower bound on its peak.

The two large DuckDB runs at 512 MiB have sampled spill peaks of
405.81–435.56 MiB; with 2 GiB headroom the peak is 439.53 MiB. The successful
Polars 2 GiB run spills 473.60 MiB and explicitly rechunks 24,455,675 output
bytes. Successful DuckDB conversions require no recorded rechunking. Polars
also spills hundreds of MiB before the capped failures: the presence of spill
files does not by itself establish bounded sorting. Disk figures measure peak
occupancy rather than total I/O, exclude the common input file, and may miss
short peaks between samples.

The practical finding is a substantial ordering-memory difference on this
input, with exact results from both engines when enough headroom is available.
DuckDB has the stronger demonstrated native larger-than-RAM sorting path here.
Polars remains usable for this sort within a 2 GiB envelope, and its modest
advantage in the earlier complete pipeline remains separate evidence. The
512 MiB target is an experimental stress setting, not a user-mandated absolute
selection threshold. Choosing Polars still requires deciding acceptable memory
growth as inputs increase, or measuring an alternate bounded ordering strategy.
The user subsequently selected DuckDB for initial implementation. The complete
importer, cancellation behavior and full-scale gates remain unverified.

The user supplied a follow-up investigation, read from the Polars checkout's
`sort-memory-investigation/REPORT.md`, that traced the same materializing sort
path in release `1bd8ec12f42d40fcec62badf32ef2177d2377d8d` and main
`0f7c24ae84d38f06cd44535e67f6b5fc8f82636e`. Main was source-inspected, not built
or benchmarked. Its additional sequential 1.44.2 runs held the engine budget at
128 decimal MB under separate 6 GiB, swap-disabled cgroups: 24 million rows
reached 1,645.34 MiB RSS and 48 million reached 2,774.31 MiB. Both verified every
output exactly. Caches were uncontrolled and charged memory was measured
separately. These are additional observations from that investigation, not
reruns performed in this Scansor session or a fitted capacity model.

[Issue #30][polars-followup] preserves the relevant upstream links: the current
streaming tracker, naive spilling limitations, merged prefetch work, merge
building blocks, and the distinction from the removed old streaming engine.
Reconsider Polars when its native ordering or an explicitly chosen alternate
strategy demonstrates bounded memory as the full input grows, with the same
NumPy, exact-content, cancellation and cleanup contract.

### XML adapter and SQLite type checks

In the same temporary environment, defusedxml 0.7.1 parsed a synthetic root with
two `.rsInfo`-like child elements. With all three rejection flags enabled,
DOCTYPE, internal entity declarations, and an external DTD reference raised
`DTDForbidden`; duplicate attributes raised `ParseError`. This is initial parser
behavior evidence; S3 still owns the complete sidecar adapter and size/lifetime
checks.

Python's sqlite3 module reported SQLite 3.50.4. A STRICT `(id INTEGER, value REAL)`
table retained the integer `2^53+1` exactly, converted a bound Python NaN to SQL
NULL, and returned bound negative zero as positive zero. The current canonical
profile normalizes negative zero anyway, but NaN-to-NULL requires an explicit
representation decision. A raw BLOB path was not tested; no SQLite performance
comparison or integration was performed.

## Remaining feature-specific investigations

S1 owns reader/writer bounded allocation, byte-layout errors, and extraction.
S2 owns raw-bit/counter portability and the independent numeric oracle.
S3/S6 own range-copy accounting, random coordinate access, OS memory measurements,
temporary disk, and full-scale performance. S5 owns actual CloudCompare property
and mesh round trips; third-party writers are not presumed to preserve arbitrary
attributes merely because PLY can encode them.

S3 owns the defusedxml adapter and conformance checks; S3/S4 implement direct
DuckDB processing and extend the probes above to the complete importer and
bounded ordering strategy,
including NumPy interchange, reliable cancellation, and RSS margin at scale.
SoftFloat remains excluded and Numba deferred unless new user direction or a
measured optimization need, respectively, changes those dispositions.

Alternate compressed storage must additionally compare cold/warm access,
adversarial face locality, chunk/shard amplification, cache limits, thread counts,
partial writes, codec failure, and exact decoded content under changed budgets.
Tabular export owns Arrow null-versus-NaN policy and schema/identity round trips.
Later topology processing owns explicit order/identity maps and verification of
any library's merging, reordering, normal changes, or repair. No such algorithm
is selected by the initial area-allocation requirement.

[issue-comment]: https://github.com/altendky/scansor/issues/29#issuecomment-5624206221
[polars-followup]: https://github.com/altendky/scansor/issues/30
[numpy-license]: https://numpy.org/doc/stable/license.html
[numpy-memmap]: https://numpy.org/doc/stable/reference/generated/numpy.memmap.html
[zarr-license]: https://github.com/zarr-developers/zarr-python/blob/main/LICENSE.txt
[zarr-performance]: https://zarr.readthedocs.io/en/stable/user-guide/performance/
[h5py-license]: https://docs.h5py.org/en/stable/licenses.html
[h5py-datasets]: https://docs.h5py.org/en/stable/high/dataset.html
[h5py-files]: https://docs.h5py.org/en/stable/high/file.html
[arrow-license]: https://github.com/apache/arrow/blob/main/LICENSE.txt
[arrow-memory]: https://arrow.apache.org/docs/python/memory.html
[trimesh-license]: https://github.com/mikedh/trimesh/blob/main/LICENSE.md
[trimesh-api]: https://trimesh.org/trimesh.html
[trimesh-install]: https://trimesh.org/install.html
[trimesh-ply]: https://trimesh.org/trimesh.exchange.ply.html
[open3d-license]: https://github.com/isl-org/Open3D/blob/main/LICENSE
[open3d-read]: https://www.open3d.org/docs/release/python_api/open3d.io.read_triangle_mesh.html
[meshio]: https://github.com/nschloe/meshio
[philox]: https://numpy.org/doc/stable/reference/random/bit_generators/philox.html
[random-compatibility]: https://numpy.org/doc/stable/reference/random/compatibility.html
[zstandard-license]: https://github.com/indygreg/python-zstandard/blob/main/LICENSE
[zstd-license]: https://github.com/facebook/zstd/blob/dev/LICENSE
[zstandard-api]: https://python-zstandard.readthedocs.io/en/latest/compressor.html
[blosc-license]: https://github.com/Blosc/c-blosc2/blob/main/LICENSE.txt
[blosc]: https://www.blosc.org/pages/blosc-in-depth/
[numcodecs]: https://github.com/zarr-developers/numcodecs
[hypothesis-license]: https://github.com/HypothesisWorks/hypothesis/blob/master/LICENSE.txt
[hypothesis-settings]: https://hypothesis.readthedocs.io/en/latest/settings.html
[psutil-license]: https://github.com/giampaolo/psutil/blob/master/LICENSE
[psutil]: https://psutil.readthedocs.io/stable/
[memray-license]: https://github.com/bloomberg/memray/blob/main/LICENSE
[memray-run]: https://bloomberg.github.io/memray/run.html
[memray-platforms]: https://bloomberg.github.io/memray/supported_environments.html
[cc-license]: https://github.com/CloudCompare/CloudCompare/blob/master/license.txt
[elementtree]: https://docs.python.org/3.12/library/xml.etree.elementtree.html
[defusedxml]: https://pypi.org/project/defusedxml/
[lxml]: https://lxml.de/index.html
[lxml-licenses]: https://github.com/lxml/lxml/blob/master/LICENSES.txt
[nbconvert]: https://github.com/jupyter/nbconvert/blob/main/pyproject.toml
[duckdb-license]: https://duckdb.org/faq
[duckdb-releases]: https://duckdb.org/release_calendar
[duckdb-input]: https://duckdb.org/docs/current/clients/python/data_ingestion
[duckdb-output]: https://duckdb.org/docs/current/clients/python/relational_api
[duckdb-arrow]: https://duckdb.org/docs/current/guides/python/sql_on_arrow
[duckdb-order]: https://duckdb.org/docs/current/sql/dialect/order_preservation
[duckdb-spill]: https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads
[duckdb-memory]: https://duckdb.org/docs/current/guides/performance/oom
[duckdb-numpy-input]: https://duckdb.org/docs/current/guides/python/import_numpy
[duckdb-numpy-output]: https://duckdb.org/docs/current/guides/python/export_numpy
[duckdb-pypistats]: https://pypistats.org/packages/duckdb
[polars-pypistats]: https://pypistats.org/packages/polars
[pypistats-faq]: https://pypistats.org/faqs
[duckdb-pypi-history]: https://pypi.org/project/duckdb/#history
[polars-pypi-history]: https://pypi.org/project/polars/#history
[polars-versioning]: https://docs.pola.rs/development/versioning/
[duckdb-aws]: https://duckdb.org/2026/08/26/ducklabs-to-join-aws
[polars-funding]: https://pola.rs/posts/series_a/
[polars-decathlon]: https://pola.rs/posts/case-decathlon/
[polars-db-systel]: https://pola.rs/posts/case-db-systel/
[duckdb-external-sort]: https://duckdb.org/2021/08/27/external-sorting
[duckdb-indexing]: https://duckdb.org/docs/current/guides/performance/indexing
[arrow-array]: https://arrow.apache.org/docs/python/generated/pyarrow.Array.html#pyarrow.Array.to_numpy
[polars-license]: https://github.com/pola-rs/polars/blob/main/LICENSE
[polars-streaming]: https://docs.pola.rs/user-guide/concepts/streaming/
[polars-numpy]: https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.to_numpy.html
[polars-batches]: https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.collect_batches.html
[polars-sink-batches]: https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.sink_batches.html
[polars-parquet-scan]: https://docs.pola.rs/api/python/stable/reference/api/polars.scan_parquet.html
[polars-parquet-sink]: https://docs.pola.rs/api/python/stable/reference/api/polars.LazyFrame.sink_parquet.html
[polars-config-source]: https://github.com/pola-rs/polars/blob/1bd8ec12f42d40fcec62badf32ef2177d2377d8d/crates/polars-config/src/lib.rs
[polars-memory-source]: https://github.com/pola-rs/polars/blob/1bd8ec12f42d40fcec62badf32ef2177d2377d8d/crates/polars-ooc/src/memory_manager.rs
[polars-sort-plan-source]: https://github.com/pola-rs/polars/blob/1bd8ec12f42d40fcec62badf32ef2177d2377d8d/crates/polars-stream/src/physical_plan/to_graph.rs
[polars-sort-sink-source]: https://github.com/pola-rs/polars/blob/1bd8ec12f42d40fcec62badf32ef2177d2377d8d/crates/polars-stream/src/nodes/in_memory_sink.rs
[sqlite-license]: https://www.sqlite.org/copyright.html
[sqlite-temp]: https://www.sqlite.org/tempfiles.html
[sqlite-types]: https://www.sqlite.org/datatype3.html
[sqlite-python]: https://docs.python.org/3.12/library/sqlite3.html
