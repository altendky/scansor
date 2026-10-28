# Streamed project archives

The issue #359 experiment adds a self-contained `.scansor` ZIP container around
the existing action graph. The initial codec uses Python's standard-library ZIP
implementation; it adds no dependency. Native and packaged-worker adapters
share the archive contract. These limits describe the
bounded experiment, not unrestricted large-scan support.

## Version 1 contract

`project.json` identifies `scansor-project`, version `1`, and `actions.json`.
The action graph retains its existing schema and interpretation. Its Source
declaration adds an explicit `archive_entry` relative member locator; the codec
removes that transport field before graph validation. SHA-256 and interpretation
bindings still establish identity. A filename or ZIP path cannot substitute for
either binding.

The writer stores the exact original member at
`scans/<original-source-sha256>.ply` for PLY or
`scans/<original-source-sha256>.stl` for STL, using the original source profile.
A project with virtual-scan lineage stores
that original once, together with the existing deterministic reduction recipe and
active choice. Reopening reconstructs and validates the derivative through the
shared project coordinator. It does not serialize an inactive graph or computed
fit caches. An empty project carries no scan member.

The container admits STORE or DEFLATE compression. It rejects duplicate or unsafe
names, links, encryption, split archives, ZIP64, unsupported compression, malformed
or overlapping local headers, and inconsistent directory/member metadata.
Admission inspects bounded central and local headers before expanded scan reads.
Control JSON rejects duplicate keys and nonfinite constants. Graph and geometry
validation remain separate steps before accepting a candidate project.

## Ownership and streaming

A `ReplayableInput` supplies a byte length, filename hint and `open()` context.
Opening an archive retains that compressed authority and an explicit member
locator. Each source replay opens a fresh sequential ZIP member, reads bounded
blocks, and verifies expanded length, CRC and SHA-256 through EOF. Incomplete
normal consumption fails. A parse failure preserves its original error. Reading
the actions does not itself establish that the scan member is valid.

Native integration can pin an owned staged archive file; the worker can retain
the selected File/Blob outside its Python runtime and expose seekable compressed
ranges. Neither adapter needs an expanded member file or a complete uncompressed
scan snapshot. An accepted project and any recovery checkpoint must retain their
archive authority until replacement or reset releases it. Cancellation belongs
to the current operation, rather than becoming permanent authority on a retained
member.

Saving streams the original into the ZIP compressor. `iter_project_archive`
returns compressed chunks of at most 64 KiB, with empty progress points when the
compressor buffers a source block. A worker host must acknowledge each chunk
before advancing the iterator, permitting bounded backpressure and cancellation
even without shared memory. The synchronous writer consumes the same iterator
into a sink. The compressed output spool or final host Blob is a separate bounded
allocation; partial output must be discarded after failure or cancellation.

## Initial resource profile

| Resource | Bound |
| --- | ---: |
| Compressed archive/output | 48 MiB |
| Sum of declared expanded members | 48 MiB |
| One expanded member | 32 MiB |
| Actions JSON | 32 MiB |
| Each manifest/reference JSON | 1 MiB |
| Central directory and aggregate local headers | 64 KiB each |
| Entries | 16 |
| Source read/output chunk | 64 KiB |

The scan importer additionally enforces its existing 8 MiB source and 96 MiB
geometry budgets before geometry allocation. The archive member cap does not
increase those scan limits. JSON controls occupy their bounded expanded buffers;
geometry arrays and compressed output are accounted separately. Compressor output
queued between iterator yields has its own bounded receipt observation. These
are component bounds, not a measurement of total process or browser memory.

## Explicit legacy migration

The unversioned nozzle handoff fixture can be opened only through the explicit
known-legacy resolver. It requires its captured source and historical reference
bindings and exactly one PLY plus `actions.json`; arbitrary unversioned archives
are rejected. The migration embeds a frozen reference record at
`references/legacy.json`, identified by the manifest.

Restoration validates the captured record, exact source identity, fit frame,
selection counts and selection-ID digests. It reconstructs the existing legacy
workspace without reading repository fixture metadata. Resaving retains that
reference and the exact original source, so moving the project away from the
checkout does not change its coordinate frame or source-bound IDs. This is a
specific compatibility path, not inferred metadata for arbitrary examples.

## Verification boundary

The codec tests cover exact member resave, virtual-original identity, bounded
source reads and compressed chunks, backpressure progress, cancellation, output
limits, corruption, unsupported ZIP structures, and portable legacy restoration
with filesystem reads disabled. Native and packaged Chromium UI checks exercise
both active-scan choices, renamed one-file reopen, cancelled/failed-open draft
preservation, and portable legacy migration. Worker checks also exercise Force
recovery.

The separate large-project UI resource driver opens a generated 131,072-vertex
archive, saves it, resets, reopens a renamed copy and recovers through Force on
plain and isolated origins. Its reports distinguish source authority lengths,
owned Python source payloads, bounded replay reads, transport chunks, compressed
File/Blob lengths, named arrays and Wasm capacity. A host-side sampler records the
owned Chromium process tree's RSS and, where available, PSS throughout operations.
These overlapping counters must not be added. Sampling adds overhead and can
miss short peaks; the evidence establishes component behavior and observed
process use, not a memory-capacity or browser zero-copy guarantee.
