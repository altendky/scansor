# Native scan import evidence

## Implemented boundary

The native HTTP slice of [#241](https://github.com/altendky/scansor/issues/241)
implements the [single-scan contract](single-scan-import.md) using the shared
source parser/project transaction. It accepts one strict PLY into an empty
project, retaining immutable original bytes and publishing its descriptor,
source-only graph and display buffers as one accepted generation. A filename is
a locating hint; no caller-supplied server path is opened.

The [common backend interface](../../../../experiments/browser_viewer/backend.md)
owns add, operation status, explicit Cancel and confirmed reset. HTTP privately
reserves an operation, uploads bounded binary bytes, polls preparation and loads
a generation-bound workspace bundle. JSON controls retain their existing 1 MB
cap. Metadata and versioned mesh responses bind the same accepted generation;
generic graph mutations require that cursor as well as their graph token.

One operation may reserve/upload/prepare at a time. Unuploaded reservations expire
after 60 seconds, terminal history is limited to 16 records, reads use at most
64 KiB, and upload sockets have a five-second idle timeout. Parsing and weight
preparation run outside the short coordinator lock. Staging is removed before
commit; cancellation, stale cursors, rejected input and shutdown release owned
candidate resources. An empty owned staging directory may remain until shutdown.
The accepted original-byte snapshot lasts for the active workspace; reset,
example replacement and shutdown release its ownership without durable saving.

Cancel fences publication and cooperates at safe read/parser boundaries. An
accepted commit wins a later Cancel; losing its HTTP response does not undo it.
Aborting observation does not itself request work cancellation. Queued and
running fits/CAD hold a source lease, so reset/example switching cannot replace
their inputs. Native computation cancellation/Force is not added by source
Cancel. Server close cancels imports, closes bounded receivers and joins work.

The constructor defaults to an empty project; the CLI retains its nozzle example
default and adds `--empty`. That entry point is for adapter development until
[#243](https://github.com/altendky/scansor/issues/243) supplies source-authoring UI.
Worker import/Force retention is [#242](https://github.com/altendky/scansor/issues/242),
transactional source-aware actions reopening is
[#244](https://github.com/altendky/scansor/issues/244), and full imported numerical
delivery remains [#240](https://github.com/altendky/scansor/issues/240).

## Initial native resource policy

| Admission check | Initial default |
| --- | ---: |
| Original source bytes | 8 MiB |
| Vertices | 131,072 |
| Triangle records | 262,144 |
| Shared loader incremental estimate | 96 MiB |
| Upload/parser chunk bytes | 65,536 |

The caps select a conservative source-only tier from the executed measurements
below. They do not promise every shape or simultaneous fit/CAD workload within
the caps will fit a given machine. The face cap modestly rounds the measured
260,610-record grid; it does not establish a separately measured maximum.
The existing 25,000-ID fitting-selection limit remains separate. Input format,
normal and attribute improvements remain #247; source setting/replacement work
remains #248. Resource-policy expansion and broader runtime validation remain
[#245](https://github.com/altendky/scansor/issues/245).

The shared estimate is `4 * source bytes + 128 * vertices + 64 * faces +
32 * chunk bytes + 1 MiB`. It is an admission estimate, not total process memory.
During preparation, disk staging coexists with original-byte assembly and
float64 geometry/normal/weight working arrays. Accepted state owns original
bytes, authoritative arrays and float32/uint32 display arrays. Encoding HTTP
responses and reading them in a client creates additional copies. Python
imports, allocator reuse, HTTP machinery, browser/GPU memory and unrelated jobs
are separate from that estimate.

## Executed measurements

On 2026-10-15, fresh native Python processes measured each source separately:
Python 3.12.14, NumPy 2.5.1, Pydantic 2.13.4, Linux x86-64. The HTTP run used the
initial caps above, an empty real server and a same-process bounded streaming
client, then read both versioned display buffers. Source SHA-256/original bytes
and the source-only graph were checked. Timings are single-run observations,
with other development tests active, rather than performance guarantees. HTTP
upload and preparation can overlap; emitted phase timestamps report observed
round trips and remaining wait, not isolated parser timings.

| Source | Bytes | Vertices / faces | HTTP import + display readback | Baseline / sampled peak RSS | Core incremental estimate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw retained RealityScan nozzle | 1,250,160 | 24,999 / 49,994 | 0.0324 s | 59,670,528 / 69,013,504 B | 14,545,856 B |
| Independent horizontal cylinder + plane | 3,958 | 84 / 132 | 0.0139 s | 56,811,520 / 60,051,456 B | 3,180,760 B |
| Generated 512 × 256 grid | 6,533,891 | 131,072 / 260,610 | 0.1232 s | 56,795,136 / 96,731,136 B | 62,737,548 B |

The grid's immutable original is 6,533,891 B; its six authoritative arrays total
12,300,826 B; display arrays occupy 4,700,184 B, with another 4,700,184 B in
HTTP-client readback buffers. Each mesh GET also transiently encodes both server
display arrays; those copies are included in sampled RSS but are not a separate
logical allocation field in the report. Sampled incremental RSS was 39,936,000 B.
These categories do not sum to a guaranteed peak: temporary overlap, allocator
reuse and sampling differ. The 1 ms sampler can miss brief peaks. Linux process
high-water measurements are also emitted and can be dominated by imports.

The earlier core-only run observed 0.0974 s and sampled peak 87,580,672 B for the
same grid with a more permissive measurement-only admission ceiling. It does not
replace the real HTTP run or define advertised limits. Neither run measures
browser/GPU memory, full-resolution meshes, fitting-selection capacity, concurrent
CAD, or physical accuracy.

Reproduce from the repository root, selecting the existing project interpreter:

```sh
uv run scripts/measure-native-scan.py --mode http \
  --native-python .venv/bin/python --output /tmp/native-http.json
```

The stdlib driver generates independent sources, starts a fresh project
interpreter for each case and records phases, identities, admission limits,
allocation categories and process measurements. `--mode core` isolates shared
preparation/staging from HTTP. Generated sources are written beside the output
under `native-measure-sources/`; remove that directory and the report after use.

## Acceptance evidence

Native service/HTTP tests cover bounded reads, incorrect/short/disconnected
uploads, explicit cancellation races, stale/empty-reset-empty cursors, one active
operation, expiration/history, rejection preservation, queued fit/CAD leases and
shutdown cleanup. Existing retained-example behavior remains regression coverage.

The independent native acceptance test opens the raw nozzle under a different
filename, verifies its 24,999 rows and source-only graph, and evaluates without
sidecar-derived starter actions. A generated horizontal cylinder plus independent
plane verifies original float32 rows, normals, triangle order, independently
accumulated area weights and coherent display buffers. Authored fits preserve
the normalized actions without computed seeds; residuals retain all selected
rows and reproduce weighted RMS. Explicit Millimeters export is read back through
STEP and checked against the generated cylinder's world axis, radius and finite
extent. This is generated geometric evidence, not a claim about scan units or
physical metrology.
