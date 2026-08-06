# First CLI Vertical Slice

## Status

**Provisional implementation design, snapshot dated 2026-07-31.** This page
defines the first reusable Scansor CLI fixture narrowly enough to implement and
test. It does not define a public CLI, durable configuration schema, canonical
observation format, supported point-cloud adapter, production package, or fit
contract. All command, configuration, report, manifest, sidecar, and canonical
array formats in this slice are internal, provisional, and non-public.

## Purpose and Boundary

The slice tests local ingestion, explicit job and settings resolution,
deterministic audit records, and read-only replay without inventing
correspondence. It has two commands; the command itself selects the job:

- `scansor inspect` validates one deliberately narrow PLY source, converts its
  coordinates to canonical metres, summarizes the source, and atomically
  publishes a new inspection run directory.
- `scansor verify` reads an existing run without modifying it, revalidates every
  artifact, and repeats inspection from the recorded source path or an explicit
  replacement source path.

There is deliberately no `fit` command. A PLY vertex is not yet a canonical
Scansor observation, and this slice creates no stable observation identity,
membership, mapping, factor, active-factor selection, held-out role, model, or
result. Fitting remains deferred until those contracts are approved rather than
guessing that an arbitrary raw point cloud can be fitted.

The implementation remains separate from retained generated solver and CAD
evidence. It imports no behavior from experiment runners and reads no retained
Track 5 artifact.

## Input Subset

The adapter accepts only PLY `binary_little_endian 1.0` with exactly one
nonempty `vertex` element and no other elements. The header must contain only:

1. `ply`
2. `format binary_little_endian 1.0`
3. one `element vertex N`
4. one accepted ordered property sequence
5. `end_header`

Comments, `obj_info`, list properties, blank header lines, extra elements,
unknown properties, aliases, and trailing bytes are rejected. Accepted property
sequences begin with homogeneous `float` or homogeneous `double` `x y z`, then
may contain a complete homogeneous coordinate-type `nx ny nz` group, a complete
`uchar red green blue` group, or normals followed by RGB. Partial or reordered
groups are rejected. Coordinates and normals must be finite. Every supplied
normal must have finite, nonzero magnitude; normalization is intentionally not
performed. RGB bytes are preserved in the canonical artifact as source
provenance and are not interpreted as memberships or fitting semantics.

Input units are mandatory and exactly `m` or `mm`. Coordinates are converted to
canonical `float64` metres with explicit factors `1` and `0.001`; normals and RGB
are not scaled. The source frame is an opaque, required nonempty label. No frame
transform or orientation is inferred.

Regular non-symlink input files are opened through a non-symlink parent and are
bounded by resolved settings. Header length, vertex count, row-size
multiplication, payload size, and complete-file size are checked before array
allocation or publication. Special files and files that change size while read
are rejected.

## Settings and Precedence

Cyclopts owns explicit source loading into a plain Pydantic v2 model. There is no
implicit configuration-file search and no `pydantic-settings` dependency. The
exact precedence is:

1. explicit CLI values
2. `SCANSOR_*` environment variables
3. one TOML file selected by the explicit `--config` meta option
4. validated Pydantic model defaults

The selected Cyclopts version and executable tests must establish the actual
settings-source and unknown-key behavior. Unknown TOML keys fail. Unknown
`SCANSOR_*` variables also fail so misspelled settings are not silently ignored.
The run report records each resolved bounded operational value and its source.
This slice has no secret setting. The required inspect source path, output run
path, unit, and frame have no defaults and may come from CLI, environment, or the
explicit TOML file under the same precedence. Configuration never supplies a
model, fit options, normal-policy choice, random seed, or another job selector.

The provisional TOML document uses exactly one `[scansor]` table. A complete
relative-path example is:

```toml
[scansor]
input_path = "scan.ply"
output_path = "runs/inspection"
unit = "mm"
frame = "scanner-frame"
max_header_bytes = 65536
max_input_bytes = 67108864
max_vertices = 5000000
log_level = "warning"
```

Relative `input_path` and `output_path` values are resolved against the command's
working directory, not the TOML file's directory. The corresponding environment
names are `SCANSOR_INPUT_PATH`, `SCANSOR_OUTPUT_PATH`, `SCANSOR_UNIT`,
`SCANSOR_FRAME`, `SCANSOR_MAX_HEADER_BYTES`, `SCANSOR_MAX_INPUT_BYTES`,
`SCANSOR_MAX_VERTICES`, and `SCANSOR_LOG_LEVEL`. Configuration syntax and names
are not compatibility promises.

The strict internal job model records selection as `inspect`, deterministic
operation as true, and random seed and model as explicit null absences. Its
supported-fit-options collection is explicitly empty; these are absence records,
not fake model or fit values. Its fixed normal policy validates every supplied
normal as finite and nonzero and preserves it unchanged, while a source without
normals continues with the absence represented by fields and a null normal-bound
summary. This policy does not create unnecessary configuration choices.

## Deterministic Run

`inspect` refuses an existing output path. It prepares and validates all bytes in
memory, writes a private sibling staging directory, syncs files and the staging
directory, then rechecks through the held descriptor the exact entry set, created
entry identities, byte counts, and SHA-256 values before renaming the complete
directory into place without replacement. Failure before publication leaves the
private staging directory and any partial artifacts intact because their names
cannot be deleted conditionally by inode; the requested output path remains
absent. If another process moves or replaces the path after publication, the
command fails its identity check without deleting the unknown replacement; an
externally moved published directory can remain at its new location. A concurrent
publisher cannot be overwritten.

These checks harden ordinary no-overwrite publication and detect tested namespace
races. Deliberate hostile same-UID namespace tampering is outside this slice's
threat model; the checks are not represented as a security boundary against a
process with the same filesystem permissions.

Each run contains exactly:

- `canonical.npy`: an internal, little-endian structured NumPy array containing
  canonical metre coordinates and any source normals/RGB
- `report.json`: canonical ASCII JSON inspection and explicit semantic absences
- `manifest.json`: canonical ASCII JSON artifact inventory and hashes
- `manifest.sha256`: exact SHA-256 sidecar for `manifest.json`

Canonical JSON is sorted, indented, newline-terminated, duplicate-key-free, and
contains no nonfinite values. The run ID is the SHA-256 of semantic report content
that excludes source location and output location but includes source and
canonical hashes, resolved settings, units, frame label, fixed job policy,
layout, counts, bounds, and semantic absences. The report retains the absolute
source path so default replay can find the original bytes; moving identical
source bytes therefore changes report and manifest bytes but not the run ID. The
output path is publication placement, so it is intentionally omitted from all
authoritative artifacts. Reports contain no timestamps, host names, process IDs,
random values, filesystem inode values, or temporary names.

The report records raw source bytes and SHA-256, canonical bytes and SHA-256,
source and canonical units, frame label, coordinate dtype, optional attribute
presence, point count, metre bounds, normal magnitude bounds, RGB preservation,
resolved settings with provenance, and explicit empty/absent observations,
memberships, mappings, factors, active factors, held-out roles, model, fit result,
and publication state. A hash establishes integrity, not authenticity.

## Read-Only Verification

`verify` requires an exact four-file run directory. It rejects a symlinked run
root, symlinked or special entries, nested or unexpected entries, oversized
artifacts, malformed sidecars, duplicate JSON keys, non-ASCII JSON, noncanonical
JSON, nonfinite JSON, persisted-model canonical round-trip mismatch, manifest
mismatch, and canonical-array schema or content mismatch. It opens files without
following symlinks and confirms stable size.

Verification recomputes the report from the recorded source path by default. An
explicit replacement input is allowed for relocation or replay, but its bytes
and computed semantic report must match the run. The strictly validated recorded
settings, including their original provenance, job policy, unit, and frame are
authoritative for replay; current inspect environment or TOML values do not
replace them. Of current configuration, `verify` accepts only its operational
logging level. Source and run trees are never modified. A successful command
writes only a short diagnostic to standard output; structured operational logs
go to standard error and are not authoritative records.

## Module Boundaries

- CLI and settings-source binding
- strict Pydantic configuration and report models
- bounded local-file and PLY adapter
- canonical NumPy representation and unit conversion
- canonical serialization, hashing, and provenance
- atomic run publication and read-only verification
- structured operational logging

Observation construction, model/mapping parsing, factor construction, solver
invocation, held-out evaluation, CAD access, and publication executors remain
absent. These explicit absences are the boundary, not placeholders.

## Evidence and Non-Claims

Focused tests must cover real Cyclopts CLI/config/environment precedence,
complete TOML-only inspect invocation, relative paths, unknown and malformed job
fields, verification isolation from current inspect configuration, malformed PLY
headers and payloads, nonfinite values, nonzero normals, unit conversion,
path/symlink/special-file rejection, arithmetic and size bounds, deterministic
output, refusal to overwrite, failed-publication staging preservation,
duplicate/noncanonical/corrupt JSON, artifact corruption, unexpected files,
source replay, and subprocess exit behavior.

The configured `uv run pytest` command discovers only `tests/`, the package and
CLI fixture suite. Retained generated solver, Track 5, and reconciliation
regressions remain separate pinned, exact-runtime commands; they are not silently
collected into the package suite or represented by its pass count.

Passing this slice can provide evidence about this provisional local fixture. It
does not establish automatic correspondence, arbitrary point-cloud fitting,
physical accuracy, metrology suitability, a public or stable format, production
packaging, supported platforms, external-source support, or product readiness.

## Deferred Work

- approve canonical observation identity, membership, mapping, factor,
  active-factor, held-out, model, and result contracts
- connect a fitting command only after those contracts exist
- perform separately authorized external-source and physical trials
- decide production packaging, distribution, public CLI, schema compatibility,
  support matrix, and release process
