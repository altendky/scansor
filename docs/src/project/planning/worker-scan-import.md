# Worker scan import and recovery evidence

## Status

The bounded worker transport/recovery implementation for
[#242](https://github.com/altendky/scansor/issues/242) uses the shared strict
source/project core and the same operations as [native import](native-scan-import.md).
Actual Chromium imports, source retention, explicit Force and cleanup have
executed evidence. Fit verification uses the selected
[bounded semantic parity contract](../experiments/numerical-runtime-parity.md)
from [#266](https://github.com/altendky/scansor/issues/266): published results,
intent, acceptance certificates and finite budgets are enforced while complete
discarded traces remain diagnostic evidence. Source arrays retain their strict
comparison. Delivery requires rebuilt current-head native/worker verification.

The native/common UI supplies empty projects, the source picker and read-only
Source inspection, gated by backend capabilities. Bounded native and numerical/CAD
plain/isolated authoring acceptance is delivered through
[#243](https://github.com/altendky/scansor/issues/243). Source-aware actions and the
bounded #244 worker adapter are delivered through
[#291](https://github.com/altendky/scansor/pull/291), with versioned capability
gating and actual both-mode reopening/recovery acceptance.
[#296](https://github.com/altendky/scansor/issues/296) records the actual raw
cylinder/export/save/reopen workflow; [#295](https://github.com/altendky/scansor/issues/295)
records repeated actual worker UI resource observations below. Their final
combined current-head CI and #245 current-main artifact preparation remain
delivery gates. File retention remains memory-only. Numerical acceptance and
direct caller cancellation are accounted separately in #240 and #249.

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

The common UI reuses its renderer. For source-capable backends, graph-only recovery
requires the same valid displayed source generation without a pending or
unavailable source transition. Fixed-source legacy ports retain their explicit
compatibility path. If the worker recovers a different accepted source, the displayed
graph, arrays, metadata, unfinished edit and prior actions stay together under a
recovery fence. The explicit **Load current project** action obtains a fresh edit
discard proof and installs a complete validated source/graph/display bundle before
releasing that fence. Force does not automatically retry this adoption or discard
the old editor. An accepted source still wins when its promotion acknowledgement
was lost; the old displayed scene is not authority to restore the prior source.

File retention is memory-only and is not a guarantee that a disk-backed File can
always be reread unchanged. Failed reading, identity validation or replay keeps
the checkpoint/actions available for diagnosis and retry. Reload, tab closure or
browser/process loss can require reacquiring the source and loading saved actions.
Filename is a locating hint; computed starts/results are not authored actions.

## Source-aware saved actions

The common Load actions workflow uses read-only descriptor inspection before
requesting a source File. Inspection neither replaces the accepted recipe nor
changes the recovery checkpoint. Empty actions need no source; a matching generic
source reuses immutable accepted geometry; a missing source requires explicit
lookup of the exact original bytes. Moving or renaming unchanged bytes is valid.
Filename hints never authorize filesystem access. Legacy actions require their
explicitly compatible loaded example and never select one implicitly.

The protocol-6 worker declares source-actions contract version 1. The generated
application explicitly enables that capability when constructing its backend;
old or missing contracts fail closed, with no capability change after startup.
Authoritative schema, interpretation, source identity, cursor, discard approval,
CAD availability and exact declared File size are checked before source reads.
Preparation repeats validation against parsed evidence, and the actual attached
File size must also match the saved descriptor before slicing or staging.

Opening prepares a fresh graph privately and uses the existing complete transfer,
coordinator adoption and Python promotion boundary. It does not publish a
source-only intermediate project. Matching opens retain the verified original
File without uploading it again; located opens adopt the supplied File. Empty
and example opens release stale user-file authority. Every normal open advances
the accepted source generation; Force retains that accepted generation and
reconstructs computed results as dirty. Accepted import warnings are copied into
the generation-scoped checkpoint so normalized recipes do not erase them during
recovery; warnings in user JSON have no authority.

The actual UI acceptance probe retains full reopened and post-Force evaluation
graphs for comparison against the native scene under the selected bounded
semantic policy. Source arrays remain strict. Its coverage includes raw-source
save/reopen, renamed lookup, matching reuse, unsupported settings, wrong bytes,
empty and explicit legacy actions, stale inspection, popup and docked drafts,
Cancel, pre-adoption Force, and accepted-but-undisplayed recovery with explicit
Retry. Delivery through #291 passed the completed probe and CI; subsequent changes
require fresh current-head gates. Its test hooks observe the shipped application
and disclose transport holds.

## Measured initial browser tier

The initial protocol-5 measurements used the same source policy now declared by
the protocol-6 runtime profile, which injects its limits into both JavaScript byte
preflight and shared Python admission. Initial limits
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
[#307](https://github.com/altendky/scansor/issues/307) preserves the staged
headroom, adverse-workload, transient-copy and capacity-improvement questions.
[#245](https://github.com/altendky/scansor/issues/245) retains final combined
workflow/artifact acceptance. Broader inputs and settings/replacement remain
tracked in #247 and #248.

## Executed actual worker UI observations

On 2026-10-15, the stronger actual-UI protocol below passed from clean signed
revision `f5dbbdf5fad3ff700fba3af5077c5e10858f3ecb`, before this documentation-only
update. The complete `worker-source-ui-resources.json` report is 4,419,690 B,
SHA-256 `8bf4477dbd41b7eb00f66a376255248b6404ba95d5d7d95ebe255d2086c10b68`.
Its site manifest is
`5de867bb8fa34dcc9ca3cb9951f420f7e95726cf541b754017356e00959abfb7`;
the CAD runtime manifest is
`4deffe9840323ffb67cb9c99695d9968d80cca6cfab0fd858c2d131e08a3544b`.
Independent data verification matched all 419 site assets, all 83 source ZIP
members and the full original observation/display digests for all three sources.

The run used headless Chromium 156.0.8078.4, Node 26.10.0, Linux x86-64 and an
Intel Core i9-9880H, with the pinned Python/NumPy runtime above and SwiftShader.
Both actual isolation states were checked. Each mode completed six ordinary
cycles in one page/worker and a separate grid Force/reopen sequence: 41 stages
per mode, 82 total. Source/recipe identity, accepted epochs, actual current File
ownership and quiescence checks passed at every applicable boundary. Force
replaced exactly one worker while preserving the accepted File/recipe/epoch;
renamed reopening used a new File and the replacement worker. Reset emptied
source/display arrays, checkpoint File and recipe, with zero owned staging.
Both contexts/previews and the browser closed normally; no external/API request
or page error occurred. Trusted inputs and artifact bytes remained unchanged.

| Source | Plain observed imports, cycles 1 / 2 | Isolated observed imports, cycles 1 / 2 | Named NumPy roots | Main named display backing at import |
| --- | ---: | ---: | ---: | ---: |
| Raw RealityScan nozzle | 483.7 / 342.1 ms | 394.3 / 488.8 ms | 3,249,866 B | 2,099,868 B |
| Independent cylinder + plane | 236.4 / 257.5 ms | 193.8 / 327.0 ms | 10,116 B | 6,624 B |
| Generated 512 × 256 grid | 1,248.3 / 2,480.8 ms | 1,089.6 / 1,970.0 ms | 17,001,106 B | 10,991,640 B |

These intervals run from observed File delivery to visible readiness, including
the file-selected observation, polling and frame settling. They exclude later
worker inventory/display hashing and are not isolated parser or presentation
latencies. The largest ordinary-import main heartbeat gap was 810.8 ms plain and
389.8 ms isolated; no timing threshold was selected. Grid Wasm capacity reached
94,240,768 B in both modes and stayed allocated after reset. Largest source and
staging high-water were each 6,533,891 B; chunks stayed at most 65,536 B.
Production normals/colors/selection overlays, CDP main/worker heap and backing
categories, and logical WebGL allocations are retained separately in the report.
The categories overlap; these finite, instrumented observations establish no
summed memory peak, immediate reclamation, leak verdict or other-device limit.

## Reproduction and gates

The additional
[actual worker product-UI resource driver](../../../../experiments/browser_static/measure-worker-source-ui.mjs)
runs two preregistered raw nozzle, independent scene and 512 × 256 grid cycles
in one application page and ordinary worker per plain/isolated Chromium mode.
Each cycle uses the real File picker, Inspect source, rectangle/through-all
selection, wheel interaction and New project controls. It observes production
normals, colors and selection overlays. The 25,000-ID selection limit remains
separate from the 131,072-vertex source cap. Grid Force is a separately labelled
worker replacement, followed by saved-actions reopening with a renamed identical
File. Scalar worker/CDP lifecycle and WeakMap File serials establish continuity;
the observer keeps no File, Worker, geometry or buffer history.
The manifest-verified backend module gets one bounded read-only observer inside
its factory closure. At settled stages it records the actual current checkpoint
File serial/size, mode, generation, source/settings identity and recipe digest
and counts. New/reset must show no checkpoint File or active source operation;
Force must preserve that current File and renamed reopening must replace it.
The WeakMap serial function is exposed only during each observation and removed
in `finally`. These ownership observations do not prove GC or reachability.
Checkpoint recipe digests sort object keys consistently with the backend's
identity serializer, while preserving all authored values and array order.

The report keeps main/worker CDP heaps and backing stores, always-nonempty-frame
Wasm capacity, named NumPy roots, immutable source bytes, lifetime staging/chunk
high-water, production display backing and logical WebGL allocation counters
separate. Passive heartbeat and rAF gaps span imports; wheel-to-next-render-submit
is a correlation proxy. Per-import intervals end at observed visible readiness,
before the subsequent worker inventory and display hashing. They include the
file-selected main observation, readiness polling and frame settling. Worker
samples occur only at quiescent boundaries; the
file-selected interval explicitly reports worker observation as unavailable.
Completed lifecycle, no active source ownership and no pending worker RPC are
required before worker inventory. Matching before/after accepted checkpoint,
recipe, lifecycle and worker identities fence each sample; read notifications
are not pending worker requests. No operation lease is bypassed or replaced.
Hashing, conversion, routing and browser instrumentation add measurement work.
These observations do not establish synchronous parser/process/GPU peaks,
immediate reclamation, a leak verdict, a performance threshold, other engines or
fitting/CAD concurrency.

The packaged-native oracle freezes independent source/settings, original row,
normal, triangle, weight and display digests before browser outcomes. Every
accepted inspection/selection/Force/reopen boundary compares them, while reset
checks empty source/display/recipe state and zero owned staging. Source-aware
reopening preserves the recipe and accepted epochs. External/API fallback,
hidden worker replacement, missing samples, failed cleanup or changed artifact
bytes fail the focused report validator. CI runs this gate after the existing
static verification and retains its JSON reports separately from the published
release payload; it adds no release-envelope member or admission capacity.
The driver verifies clean, consistent site/runtime revision identities and exact
manifest-listed runtime source hashes against its checkout, then freezes and
rehashes its own helpers, observers, inputs and references. Generated/vendor
artifact files remain protected by their complete asset inventory and unchanged
package/build inputs. A matching parent artifact can diagnose the candidate when
producer bytes agree; delivery still requires a fresh signed-current build and
the full CI gates. Progress checkpoints preserve the last completed stage, and
context/server cleanup attempts run independently when one fails.
Before importing packaged native code, the oracle verifies the source ZIP asset,
unique exact member closure, paths/types and every expanded member's expected
size and hash against the manifest and trusted checkout. All members pass before
any extraction or import; extra, duplicate, aliased or tampered members fail.

```sh
node experiments/browser_static/measure-worker-source-ui.mjs \
  --directory "$TASK_SITE" --output "$TASK_WORKER_UI_RESOURCES" \
  --native-python .venv/bin/python
node experiments/browser_static/worker-source-resource-validation.test.mjs
```

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
Its [actual UI helper](../../../../experiments/browser_static/source-ui-recovery.mjs)
also exercises the shipped app, singleton backend and real worker across source
adoption boundaries, live drafts, prior-actions export and explicit full-bundle
Retry. A separate legacy-shaped startup fault omits graph from one public
`loadWorkspace` response, holds the actual initial `getGraph` reply and invokes
Force in the shipped app. This disclosed shim uses the current real worker; it
does not establish compatibility by executing an old worker binary. Reports must
include successful `ui_source_recovery` and `ui_legacy_startup` checks as well as
strict source-array evidence and bounded semantic fit receipts. Full original,
replayed and native graph snapshots accompany the exact comparison input and
diagnostic ledger. Source generation and settled readiness are checked
independently of the explicit semantic projection. Fresh assembled-site startup
and these UI cases require their own executed checks; source-only measurements
do not establish them.
Numerical/CAD profiles and plain/isolated runs are distinct required gates. A
diagnostic run may collect remaining lifecycle evidence after a parity failure,
but must retain that failure, incomplete status and nonzero exit.

## Source authoring acceptance

The [authoring helper](../../../../experiments/browser_static/source-ui-authoring.mjs)
uses actual File pickers, pointer gestures, fit/property dialogs and the shipped
singleton worker. It adds twelve required scenario groups: empty controls,
invalid/over-limit/cancelled input, raw inspection, raw pointer fitting/editing,
keyboard commands, generated plane/cylinder authoring, popup/docked work guards,
dependent removal and explicit examples. The example catalogue reports an active
example only for the accepted example owner. Private or cancelled candidates
do not change that status. Empty and imported scan owners have no active example
and cannot falsely disable opening the retained example.

The raw gesture selects all 24,999 original RealityScan rows without a starter
recipe or injected memberships. Its ordinary least-squares plane is checked
against every selected row and independently decoded incident-area weights;
this rough full-source fit makes no metrology or planar-quality claim. Generated
Side-view gestures must select exactly the independent fixture's twelve plane
and seventy-two cylinder rows before fitting. Original coordinates, finite
support, weighted objectives and source identities remain checked. Dialog
selectors distinguish live drafts from modal project commands, and Escape waits
for actual chooser opening and closure.

Complete original, edited and generated UI-authored graphs are retained with
independently observed accepted generations and full source evidence. Native
execution reconstructs each authored recipe from the same frozen bytes using
the identical packaged Python core. Failed native graphs are retained before
the reference run rejects them. All three native/worker pairs enter one seven-pair
trusted semantic batch with the four source import/replay and saved-actions pairs;
source arrays, recipe bindings, selected geometry, acceptance and finite budgets
remain gates.
Computed starts remain absent from actions. The trusted verifier requires all
twelve ordered cases, literal successful oracles, terminal readiness and exact
raw graph operands; refreshed-hash mutations must still reject changed results
and budgets. Both native popup/docked dependent-fit removal and raw pointer
authoring have actual Chromium coverage.

These diagnostic results do not replace the four numerical/CAD and
plain/isolated profiles or complete current-head CI. Read-only interpretation,
empty-only Add and confirmed whole-project removal retain the agreed scope;
settings/replacement and wider numerical/resource delivery remain separately
tracked. Bounded source-aware saved-actions reopening is delivered through #291.

The static checker and trusted release verifier require the new reports and
explicit source/recovery/cleanup checks. Numerical-profile reports bind their
own recorded manifest and the same source revision. Release verification remains
structural/hash validation and never executes downloaded artifact code. Physical
tolerances and finite budgets retain their existing limits under the shared
parity policy. No site publication is authorized by these measurements.
