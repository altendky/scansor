# Browser workload validation

This investigates the actual bounded worker application for #209. It does not
establish general source ingestion, full-resolution processing, mobile support,
or a browser-only product direction. Native HTTP operation remains available.

## Evidence and measurement boundaries

The clean application source revision is
`691476d4d5b2975e28737992396d0722cfb87ccc`, with site manifest SHA-256
`db233e3821dcd0bc9d39debdec3224efe05bbf9fc38c4a669f97580df6bf41b6`.
The worker and application bytes match the merged #208 implementation. Reports
record source/runtime hashes, exact versions, fixture provenance, requests,
errors and artifact re-verification. Measurement instrumentation belongs to
the harness, not the deployed application.

Executed on Linux on 2026-10-14 UTC: Playwright 1.64.0 and Chromium
156.0.8078.4, Pyodide 314.0.7, Python 3.14.2, NumPy 2.4.6, Pydantic 2.12.5,
and approved OCP 8.0.1.1. Native comparison uses the existing Python 3.12.14,
NumPy 2.5.1, Pydantic 2.13.4 and native OCP 8.0.1.1 environment. Native OpenBLAS
thread limits are one in controlled fitting comparisons; retained-graph runs
also limit OpenMP and MKL to one. These version differences remain part of
the parity evidence.

The host is an Intel Core i9-9880H with 16 logical CPUs. Chromium is headless;
the memory probe reports ANGLE/Vulkan SwiftShader rendering. These are local
software-rendered observations, not hardware-GPU or end-user device budgets.

## Independent size dimensions

The retained source has 24,999 vertices and 49,994 triangles. Its default
30-action graph is exercised through the real UI. Source mesh size, selected
observations, graph complexity and CAD tessellation are separate dimensions.
Increasing one does not demonstrate capacity for the others.

Generated source/display probes use the existing repeated-boss generator and
scale its scan-coarse tessellation. They remap only the temporary fixture assets
at the worker's fixed example path; production Python modules, worker and UI
bytes remain unchanged. Their manifests explicitly identify test-only generated
fixtures and regenerate source/archive/runtime/site hashes. They are not newly
supported uploaded examples. Each seven-action recipe uses exactly 128 lateral
and 32 plane observations selected deterministically from existing seed gates.

| Tessellation scale | Source vertices | Source triangles | Selected observations |
| --- | ---: | ---: | ---: |
| 1 | 6,292 | 10,976 | 160 |
| 2 | 23,783 | 44,388 | 160 |
| 4 | 92,475 | 178,632 | 160 |

All three load/render through the actual worker UI, reach seven ready actions,
display fit/residual results, and match the exact packaged native graph at
relative and absolute tolerance 1e-10. Each comparison covers 2,489 numeric
fields; maximum parameter difference is 1.24e-14 and maximum residual difference
is 5.38e-15. Single diagnostic UI-ready observations were 5.69, 5.74 and 6.71
seconds, respectively, on loopback. These are not controlled scaling timings.
Display-buffer receipt to the first automatic graph request took approximately
272, 356 and 694 ms. Maximum gaps in the main-thread 16 ms timer over startup
and UI inspection were 244, 324 and 755 ms. These intervals are not isolated
fit timings, but they show that worker fitting does not eliminate main-thread
source preparation/rendering stalls. The larger displayed tier is evidence
of successful execution, not a smooth-interaction support promise.

Separate component probes freeze float64 coordinates/noise once natively for
both interpreters. They call unchanged `fit_cone_plane` and
`cone_plane_residual_jacobian`, preserving the existing fixture geometry,
weights, initial parameters, seed 123456 and axial support (-3, 6).
Two warmups precede five measured samples, with native/browser calls alternating
serially. Fixture loading, message transfer and JSON conversion are outside
the core timing interval.

| Selected observations | Cone / plane | Native median | Browser median | Browser/native |
| --- | --- | ---: | ---: | ---: |
| 496 | 432 / 64 | 4.538 ms | 12.600 ms | 2.78 |
| 4,096 | 3,584 / 512 | 12.550 ms | 23.800 ms | 1.90 |
| 16,384 | 14,336 / 2,048 | 41.987 ms | 73.500 ms | 1.75 |

All measured and warmup fits agree at relative/absolute tolerance 1e-10, including
parameters and residuals; termination, rank, iterations and evaluations agree
exactly. These component ratios do not predict whole UI, connected graph, CAD
or arbitrary scan performance. Both execution paths are serial here, so these
ratios do not measure a native multithreading advantage.
The final harness also compares the entire fit return, including objective
history and remaining diagnostics: 26 numeric fields per pair plus exact
structure/nonnumerics, with maximum absolute difference 6.32e-13. Jacobian
values are not compared; their dtype/shape/size are checked and reported.
An earlier serial run of the same fixture arrays measured ratios 2.80, 2.12 and
1.59; variation between these runs remains visible rather than being treated
as an exact performance characteristic. Boundary Wasm capacity stayed
78,512,128 bytes. The largest tier's explicit residual/Jacobian payload is
131,072/917,504 bytes, excluding internal solver temporaries.
Interpreter versions and numerical-library builds differ, so these tests do
not attribute the remaining slowdown solely to WebAssembly code generation.

## Retained application startup and responsiveness

The final portable UI timing driver ran three fresh browser contexts, then two
warmups and five samples per steady-state operation. Fresh fitting resets the
graph with the ordinary `selectExample` API and publishes it using the actual
recipe file-upload UI. Each sample verifies 30 unevaluated actions before
Evaluate all and 30 ready actions afterward. Reset/upload is outside fitting
timing. Native comparison uses the identical packaged source and accepted recipe.

| Operation | Browser median | Native median | Interpretation |
| --- | ---: | ---: | --- |
| Fresh 30-action numerical graph | 719.5 ms | 317.4 ms | 2.27x; worker lifecycle versus native adapter, including graph/publication work |
| Already-ready graph | 36.2 ms | 33.4 ms | Cached graph scheduling/publication, not another fit |
| Bounded face STEP/reference-mesh ZIP export | 202.2 ms | 141.3 ms | 1.43x; supported retained face, not arbitrary meshing |

Fresh-context UI readiness took 5.73–6.12 seconds on loopback. First numerical
worker evaluation took 0.899–1.017 seconds. First CAD intersection submission
to a ready result took 10.264 seconds, including optional OCP verification,
extraction/import and operation setup. These phases are not pure download or
kernel timings. A prior controlled run observed fresh graph medians 725.5/321.4
ms, providing a repeat observation rather than a latency guarantee.

The ordinary UI authored an intersection and Unicode-labelled bounded plane
face and downloaded its STEP/reference-mesh ZIP. Numeric/CAD graph comparisons
covered 13,202/16,692 fields at relative/absolute tolerance 1e-10. Maximum
parameter/residual differences were 6.04e-14/4.97e-15; the largest absolute field
difference, 2.91e-10, was a solver condition diagnostic and satisfies the relative
tolerance. Native OCP reopened and inspected the browser ZIP. Existing
[CAD validation](cad-validation.md) additionally covers supported geometry
families, units/transforms and an explicitly injected valid Body scaffold;
that Body is not a UI-authored closed nozzle.

Trusted wheel input during all five measured fresh fits reached the next
render-stat update in 4.5–4.6 ms. This is a render-submission proxy, not physical
presentation latency. Maximum 16 ms timer gaps were 152–213 ms and rAF gaps
66.7–100 ms during those fits. Pointermove-to-next-render also includes the later
wheel action and is not an independent pointer latency. Navigation works during
computation, with observable jank; large selections and rendering still require
bounded handling. Progress consists of truthful lifecycle/phases, not an
intra-solver percentage. Actual plain/isolated Cancel, publication fencing and
Force/recovery evidence remains in [recovery validation](recovery-validation.md).

No request interception was used for these timing runs. The exact existing
preview sends `Cache-Control: no-store`; Wasm, NumPy and Pydantic wheels were
requested twice per fresh startup by verification then runtime loading.
Three startups plus CAD served 149,253,475 body bytes, versus 46,557,490 unique
served body bytes. Each numerical startup served approximately 42.18 MB;
source ZIP/fixture/reference results were requested once per startup, and
the optional OCP wheel once overall. These request totals differ from listed
artifact inventory. No WAN bandwidth, warm HTTP-cache benefit, offline or
GitHub Pages cache policy was measured. Reloads use fresh contexts in this
evidence; repeated operations use a warm worker, not a warmed download cache.

## Copies and memory interpretation

The instrumented Chromium memory driver passed 16 stage observations: source/UI
readiness, first numerical solve, five fresh graph resets/solves, first CAD
candidate operation, bounded face authoring/evaluation, five exports, actual
UI Force/restart, and reevaluation. Every solve asserts ready states, every
export clears retained Python bytes, and Force changes the observed dedicated
worker/CDP session. Asset lengths/hashes are checked before appending diagnostic
hooks in memory; the artifact remains unchanged. Routing disables HTTP caching,
so this separate driver provides no performance/cache timings.

The following are decimal MB, independent counters rather than additive memory:

| Stage | Wasm capacity | Worker JS heap used | Worker backing stores | Main JS heap used | Main backing stores |
| --- | ---: | ---: | ---: | ---: | ---: |
| Source/UI ready | 78.51 | 10.59 | 25.48 | 9.50 | 5.74 |
| Five fresh numerical solves | 78.51 | 12.07 | 26.32 | 28.36 | 5.94 |
| First CAD candidates | 302.78 | 79.81 | 364.11 | 29.42 | 5.94 |
| Five CAD exports | 302.78 | 84.37 | 343.23 | 16.77 | 8.72 |
| Recreated worker, graph evaluated | 302.78 | 86.31 | 364.93 | 14.68 | 5.83 |

Named NumPy backing roots total 3,559,942 bytes throughout. Scene typed-array
roots are 2,102,596 bytes at source readiness, 2,129,644 after numerical fitting,
and 2,133,988 with the bounded face. Logical WebGL buffers are about 2.10–2.18 MB,
including temporary updates. Five exports each returned 588,822 bytes in this
run; generated recipe identifiers can change compressed ZIP size across runs. Wasm
capacity did not grow further across those repetitions. Force restores a
CAD-containing committed recipe, so the replacement worker loads CAD again and
does not return to the numerical-only capacity. None of these observations
proves prompt physical memory reclamation or a leak-free application.

Source positions, normals, local fitting coordinates and weights remain
immutable float64 NumPy arrays. Selected fitting rows use those authoritative
coordinates, not the float32 render mesh. BrowserWorkspace adds contiguous
float32 positions and uint32 triangle indices. The worker obtains a temporary
Pyodide buffer view, copies it to an independently owned JavaScript ArrayBuffer,
releases the view/destroys its proxy, and transfers the owned buffer. The client
checks sender detachment. The whole Wasm memory buffer is never transferred.

The UI creates typed-array views over the transferred buffers; multiple point
geometries share the source position attribute. Computed normals, mesh colors
and point/focus colors are separate allocations. WebGL uploads add logical GPU
buffers. CAD previews and graph residuals also travel through Python JSON,
JavaScript parsing/structured cloning and render-array construction. Exports
are copied from Python bytes to owned JavaScript storage, clear the retained
Python export buffer and transfer it before download. These paths require more
than one representation of the same evidence.

Memory categories must remain separate: Wasm linear-memory capacity, main/worker
JavaScript heaps and backing stores, named NumPy roots, scene typed-array roots,
and logical WebGL buffer allocation. NumPy payload is already inside Wasm;
scene arrays overlap JavaScript backing stores. Summing them double-counts.
Wasm capacity is not live allocation or process RSS. Browser CDP backing-store
counters are not a complete Wasm accounting surface.

The source triangle view has 599,928 logical bytes but retains a 649,922-byte
structured PLY root. Array views alone therefore undercount even known payload.
Named arrays exclude Python objects, selection lists, graph results, allocator
free space, virtual filesystem files and solver temporaries. GPU buffer sizes
exclude textures, drawing buffers, shaders, driver staging and deferred frees.
Stage-boundary observations cannot sample inside synchronous NumPy/OCCT calls;
logical high-water observations are not physical peak memory. Five repetitions
do not establish absence of leaks.

The instrumentation uses public [Pyodide buffer APIs](https://pyodide.org/en/stable/usage/api/js-api.html#pyodide.ffi.PyBuffer.getBuffer),
dedicated-worker/main [DevTools heap counters](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/js_protocol.json),
array-root deduplication and actual WebGL buffer sizes. [Playwright CDP](https://playwright.dev/docs/api/class-browsercontext#browser-context-new-cdp-session)
is Chromium-specific. [ArrayBuffer transfer](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects)
ownership differs from the explicit preceding Wasm-to-JavaScript copy. GPU
queries, mutation observers, CDP inspection and WeakRef bookkeeping introduce
observer effects; no GC was forced and no physical GPU residency was sampled.
The optional total-page memory API was unavailable on the plain host and was
not substituted for these separate counters.

Each seven-parameter float64 Jacobian alone occupies 56 bytes per residual row,
before weighted/scaled copies, SVD factors and trial arrays. A large displayed
mesh with 160 selected observations tests a different memory burden from a
large solve. The documented multi-million-vertex native ingestion targets remain
unproved in the browser, and the browser slice does not include that ingestion
pipeline or its Linux resource supervision.

## Browser and publication boundaries

Chromium is demonstrated on this Linux host. Firefox and WebKit launch attempts
failed because the installed Playwright version's expected Firefox 1555 and
WebKit 2370 executables were absent. No older cached revisions were substituted
and no new engines were installed. Safari, Firefox, mobile devices and other
operating systems remain untested; this is not a claim that they cannot work.

The recommended initial release remains the fixed retained nozzle, desktop
Chromium, supported numerical graph/selection/recipe operations and the already
validated CAD families. Generated tiers are test evidence, not new public size
limits. Optional isolation retains the demonstrated numerical interrupt path;
plain hosting retains polite boundary cancellation and explicit Force.
No hard timing or total-memory budget follows from these measurements.

For #210, gate deterministic source/fixture integrity, real retained UI startup,
selection/recipe semantics, fresh-result parity, CAD validity/native STEP
readback, stale/error behavior, and the decided plain/isolated recovery contract.
Require the fitting harness to prove its uncached setup. Retain startup,
responsiveness and separate memory counters as reports; do not convert these
single-host timings or boundary capacities into hard release thresholds.
Broader engine, source-size, latency, peak-memory, cache and offline support
requires new evidence and maintainer acceptance before making those promises.

The cheapest confidence-changing follow-up is the same retained workflow on a
normal interactive desktop browser/GPU and a realistic throttled network,
comparing a fresh profile with reload on the intended host cache policy.
Measure UI input and actual rendering pauses as well as worker completion;
first CAD loading and recovery are separate stages. A matching Firefox/WebKit
run would change engine confidence, rather than extrapolating Chromium results.
These probes do not require changing fitting algorithms or importing real
full-resolution scans.

This is possible and demonstrated for the bounded example. Practical startup on
real networks and devices, cache behavior, offline operation and additional
engines remain unproved. Public distribution is still blocked by the recorded
[redistribution obligations](../browser_runtime/redistribution.md), followed by
release review and separate publication/DNS authority. Hosting selection alone
does not make the artifact suitable to publish.
