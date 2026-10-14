# Worker recovery validation

Executed on 2026-10-14 UTC for #208, from the change based on
`bfff01dbb212554812d7323254436b9f808375c6`. The assembled candidate deliberately
records a dirty checkout. Runtime manifest SHA-256:
`ed18d17ee25bdbd850dc2d3f2be50c3ddfb5958df9161019a71c95f1928d84e3`;
site manifest SHA-256:
`00d2847a2fbfdaf55454edf461451fc1e5bd9944e4f0684537f9ff87c822ebb3`.
The [reproducible entry point and contract](recovery.md) do not depend on these
temporary artifact/report paths.

## What ran

Chromium 156.0.8078.4 ran the complete static UI and actual Scansor worker
operations at a nested URL, on both plain and COOP/COEP-isolated loopback hosts.
Runtime versions were Pyodide 314.0.7, Python 3.14.2, NumPy 2.4.6, Pydantic
2.12.5 and approved OCP 8.0.1.1 (wheel build
8.0.1.1.post202609241841). Native verification used Python 3.12.14 and the
existing pinned native OCP environment.

Both recovery runs passed all 12 grouped checks:

- Edit and fit the real retained-nozzle recipe; idle Cancel preserves it.
- Cancel at an actual numerical running lifecycle, settle queued callers and
  graph observers, discard derived results and successfully fit again.
- Hold a real completed graph reply, Cancel, then release it and confirm it
  cannot publish. This is an explicit message-delivery fault.
- Explicitly Force/restart during work, Force again while restarting, preserve
  the same backend object/source/committed recipe, ignore an injected retired
  worker reply, and successfully evaluate afterward.
- Inject an uncaught worker JavaScript error, settle the queued caller, then
  explicitly recover. This validates the worker-error path, not an actual
  browser/OS crash or out-of-memory event.
- Inject a stale numerical lifecycle while a real CAD export is running; Cancel
  and export afterward. The worker's eligibility guard keeps SIGINT disabled in
  CAD even though the main-thread status is stale.
- Cancel and explicitly Force during actual supported CAD export, restore the
  CAD-containing recipe, and successfully export ZIP bytes afterward.
- Return HTTP 404 for a real startup WASM asset, verify failure, restore the
  asset and explicitly retry successfully.
- Exercise the actual UI Cancel handler at a numerical running checkpoint:
  Auto turns off, the exact committed recipe remains, visible fit metrics clear,
  no unsolicited ensure/evaluate request appears during a 100 ms observation,
  and the actual Evaluate all control succeeds afterward.
- Hold the first real display-source reply during UI startup, use the actual
  Force button, then release retired replies. One renderer remains, with input
  listener registration counts matching uninterrupted startup.
- Same-recipe Force and injected-error recovery produce exactly equal results
  across 7,248 numeric fields after evaluation.
- No computation HTTP API calls, external requests or page errors; artifact
  verification remains unchanged before and after each run.

The harness appends a dormant JavaScript crash listener to served worker code
and records its digest. Other fault instrumentation acts on messages or asset
responses. Scansor fitting/CAD algorithms and on-disk artifact bytes remain
unchanged. Full graph evidence is written separately from the compact report.

## Observed timing

These are single diagnostic observations while other verification was running,
not a controlled performance comparison or a latency promise. Cancel-barrier
time ends when discarded computation has stopped and a restored snapshot is
available; immediate caller settlement is a different event.

| Observation | Plain host | Isolated host |
| --- | ---: | ---: |
| Numeric Cancel to restored snapshot | 568.0 ms | 25.7 ms |
| CAD Cancel to restored snapshot | 227.6 ms | 195.1 ms |
| Repeated Force to recovered numerical snapshot | 5.93 s | 4.47 s |
| Force to recovered CAD-containing recipe | 15.14 s | 13.14 s |

The isolated run detected a shared buffer and sent the numerical interrupt
signal. The plain run had no shared buffer. CAD remained noninterruptible in
both: these small exports returned at a natural boundary. This does not prove
mesher interruption or safe cancellation inside arbitrary OCCT calls. The
earlier [meshing probe](../browser_runtime/cancellation/validation.md) finished
native meshing before handling SIGINT. Restart includes initialization and
recipe replay; it does not measure process termination or memory reclamation.

## Regression coverage and limits

Local checks passed: 78 scoped Python tests, 319 JavaScript tests, eight native
HTTP startup/recipe-loading browser tests, configured types with zero errors or
warnings, and scoped pre-commit hooks. The final static validator passed eight
root/nested startup/failure cases with isolation. The workspace validator passed
11 selection/replay/native numerical-parity checks, and the CAD validator passed
11 geometry/export/native STEP-readback checks. See the earlier
[workspace evidence](validation.md) and [CAD methodology](cad-validation.md) for
their fixtures and tolerances; the valid closed Body export uses an explicitly
injected source-bound evaluated scaffold rather than a UI-authored closed nozzle.

Python cancellation tests also preserve pre-existing failed fits/Bodies and
job errors without silently retrying them, reject source/reference mismatches
atomically, retain immutable float64 arrays and CAD mode, clear export bytes,
and restore STEP units/temp files after an injected writer interruption. Native
thread tests remain native; the browser worker remains serialized.

Development validation caught and corrected an invalid graph-property lookup,
post-failure readiness publication, duplicate-startup and stale-interrupt risks,
and raw-probe handlers misreading new lifecycle messages. The isolated harness
also needed to preserve COOP/COEP on its modified worker response. Failed
development reports are retained; they are not counted as successes.

This demonstrates recovery for the bounded retained example. It does not
demonstrate large-scan capacity, browser/process crash recovery, durable project
persistence, offline/WAN startup, Firefox/WebKit support, exact memory release,
or cooperative OCCT cancellation. Those remain separate validation or follow-up
work. Publication and DNS changes remain unauthorized.
