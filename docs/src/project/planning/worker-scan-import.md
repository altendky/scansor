# Worker scan import and recovery evidence

## Status

The bounded worker transport/recovery implementation for
[#242](https://github.com/altendky/scansor/issues/242) uses the shared strict
source/project core and the same operations as [native import](native-scan-import.md).
Actual Chromium imports, source retention, explicit Force and cleanup have
executed evidence. **Delivery remains gated by strict native/worker fit parity:**
the frozen independent fixture exposes a recovery-attempt diagnostic difference
tracked in [#266](https://github.com/altendky/scansor/issues/266). Source-array or
selected-geometry agreement does not turn that comparison into a pass.

The source picker and empty-project product UI remain
[#243](https://github.com/altendky/scansor/issues/243); transactional source-aware
actions lookup remains [#244](https://github.com/altendky/scansor/issues/244).
This work adds neither persistence nor broader numerical-family support. The
full numerical delivery gate in #240 and its draft stack remain separate.

## Source ownership and publication

The [backend interface](../../../../experiments/browser_viewer/backend.md) exposes
add, status, explicit source Cancel and confirmed reset. The coordinator retains
a pending File separately from the accepted recovery checkpoint. It clones the
File reference to a dedicated worker; bounded File slices, hashing, profile
validation, normals and area preparation occur there. Original bytes travel
separately from JSON controls. No JavaScript PLY interpretation or HTTP
computation fallback is used.

The worker stages one owned seekable virtual file, calls the shared Python
importer, removes staging and constructs a private candidate project. The
currently accepted project remains untouched. After receiving a complete
candidate graph/descriptor/display bundle and sender-buffer detachment
confirmation, the coordinator atomically adopts its File, semantic identity,
recipe and logical source generation. It then authorizes the worker to promote
that already validated candidate before queued work resumes.

Cancel or Force before coordinator adoption discards the candidate and preserves
the accepted source. After adoption the import is accepted, including when
worker promotion acknowledgement or delivery is lost. Force then reconstructs
the new accepted source; it does not silently restore the prior source. Aborting
an observer does not cancel work or discard an accepted recovery reference.
Source generation, worker lifetime, request/publication generation and
cancellation serial remain separate fences.

One source operation owns bytes at a time, independently of the 64-request
quota. Terminal history retains at most 64 small primitive records. Cooperative
Cancel yields during file reading and waits for synchronous Python preparation
to reach a safe boundary. Numerical SIGINT is not installed in parsing,
publication, cleanup or CAD. Failed virtual-file cleanup fences further imports
and requires explicit Force; Cancel cannot hide its diagnostic. There is no
automatic escalation to Force.

The external accepted checkpoint records empty/example/user mode, File or
packaged source reference, content/interpretation identity, committed recipe and
pinned runtime. Force recreates the accepted mode, verifies identity, and validates
the recipe on a private candidate with computed results dirty. User-source
recovery starts empty and never substitutes the nozzle. Same-source computation
Cancel reconstructs its graph through the authoritative project owner while
retaining immutable source arrays and committed failure evidence.

File retention is memory-only and is not a guarantee that a disk-backed File can
always be reread unchanged. Failed reading, identity validation or replay keeps
the checkpoint/actions available for diagnosis and retry. Reload, tab closure or
browser/process loss can require reacquiring the source and loading saved actions.
Filename is a locating hint; computed starts/results are not authored actions.

## Measured initial browser tier

The protocol-5 runtime profile declares the source policy and injects its limits
into both JavaScript byte preflight and shared Python admission. Initial limits
are 8 MiB original bytes, 131,072 vertices, 262,144 triangle records, a 96 MiB
incremental loader estimate and 65,536-byte chunks. These values were evaluated
independently from the native measurements, despite selecting the same bounded
tier. The existing 25,000-ID fitting-selection cap remains separate.

On 2026-10-15, fresh page/worker cases ran in desktop headless Chromium
156.0.8078.4 on Linux x86-64, Intel Core i9-9880H. The pinned worker used Pyodide
314.0.7 / Python 3.14.2 / NumPy 2.4.6 / Pydantic 2.12.5. Both plain and isolated
workers imported all four preregistered sources and released staging. Source
inputs and independent row/normal/triangle/area-weight oracles were frozen before
worker outcomes; generated fixtures are not physical scan truth.

| Source | Original bytes | Vertices / faces | Plain / isolated import | Accepted Wasm capacity | Named unique NumPy roots |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw RealityScan nozzle | 1,250,160 | 24,999 / 49,994 | 101.1 / 106.4 ms | 65,404,928 B | 3,249,866 B |
| Independent cylinder + plane | 3,958 | 84 / 132 | 19.7 / 19.7 ms | 54,460,416 B | 10,116 B |
| Generated 256 × 128 grid | 1,628,673 | 32,768 / 64,770 | 131.4 / 119.4 ms | 65,404,928 B | 4,240,786 B |
| Generated 512 × 256 grid | 6,533,891 | 131,072 / 260,610 | 413.5 / 432.5 ms | 78,512,128 B | 17,001,106 B |

Empty-worker Wasm capacity was 54,460,416 B. It remained allocated after reset;
releasing source objects does not shrink Wasm capacity or prove immediate OS
memory reclamation. An initial diagnostic incorrectly used an empty display
buffer, whose zero-length JS sentinel is not the Wasm heap. The corrected hook
reads the always-nonempty coordinate frame and requires positive capacity at
every boundary.

The largest grid retained 6,533,891 B of immutable original bytes; its staging
high-water was the same source length, with maximum reads of 65,536 B and zero
owned staging at acceptance/reset. Display transfer copied 4,700,184 B into
independently owned JS buffers. A minimal test WebGL2 draw allocated another
4,700,184 B of logical geometry buffers using SwiftShader. This is not the
production UI's normals, selection overlays or complete GPU cost.

The raw source and independent scene were explicitly reconstructed with Force
in both modes, preserving source identity and logical generation. Availability
took approximately 4.5–4.7 seconds over local verified assets. Grid cases were
imported/reset once per fresh worker; their Force and repeated same-worker leak
behavior were not measured by this resource driver.

These are single-run safe-boundary observations, with other development work
active and test routing disabling browser cache. They are not startup/WAN,
latency, full-resolution, total-process or peak-memory guarantees. File.size,
named NumPy backing roots, original snapshots, Wasm capacity, JS heaps/backing
stores and logical WebGL sizes overlap and must not be summed. In particular,
safe-boundary samples do not observe live synchronous parser temporaries.
The 96 MiB estimate excludes interpreter, browser, GPU and unrelated workloads.
Other engines, devices and concurrent fitting/CAD remain unmeasured;
[#245](https://github.com/altendky/scansor/issues/245) retains the improvement and
complete UI/workflow resource questions. Broader inputs and settings/replacement
remain #247 and #248.

## Reproduction and gates

Use a freshly built and verified site from this checkout, with reports outside
the repository and artifact. The
[resource driver](../../../../experiments/browser_static/measure-source-import.mjs)
uses real File inputs through Playwright, a minimal test page, common backend
operations and in-memory diagnostic hooks against verified on-disk source bytes.
It records hook hashes, input hashes, separate memory counters, bounded transfers,
cleanup, exact worker replacement and blocked external/API requests.

```sh
node experiments/browser_static/measure-source-import.mjs \
  --directory "$TASK_SITE" --output "$TASK_SOURCE_MEMORY" \
  --native-python .venv/bin/python
node experiments/browser_static/measure-source-import.mjs \
  --directory "$TASK_SITE" --output "$TASK_SOURCE_MEMORY_ISOLATED" \
  --native-python .venv/bin/python --isolation
```

The actual source acceptance driver separately compares full source arrays,
weights, authored fits and STEP readback with native execution from the exact
packaged source. It exercises cancellation around candidate/transfer boundaries,
Force after adoption with held authorization/acknowledgement, and cleanup failure.
Numerical/CAD profiles and plain/isolated runs are distinct required gates. A
diagnostic run may collect remaining lifecycle evidence after a parity failure,
but must retain that failure, incomplete status and nonzero exit.

The static checker and trusted release verifier require the new reports and
explicit source/recovery/cleanup checks. Numerical-profile reports bind their
own recorded manifest and the same source revision. Release verification remains
structural/hash validation and never executes downloaded artifact code. Existing
strict numerical probes, tolerances and budgets remain unchanged. No site
publication is authorized by these measurements.
