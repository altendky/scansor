# Initial runtime validation

Executed on Linux on 2026-10-13, using the repository-owned builder and runner.
These are local static-worker results, not application or deployment benchmarks.

## Inputs and environment

- Source baseline: `b7f2337a602302328287c3d9177d2b969c5c2e15` plus the
  uncommitted runtime harness; no fitting/graph/CAD algorithm changes. The
  artifact records its dirty checkout and individual source hashes.
- CAD-profile source archive SHA-256:
  `f34e6154726d496ce579c2af1a95cdc2ed17d440b27fc87d5bdf749c4e1f096d`.
- Frozen float64 fixture SHA-256:
  `751a6793d99b26b412000b69d47de3d61f790b4c8bce79e884282e3f6b48f84a`.
- Browser: Chromium 156.0.8078.4, Pyodide 314.0.7, Python 3.14.2,
  NumPy 2.4.6, Pydantic 2.12.5 and optional OCP 8.0.1.1.
- Native reference: existing Python 3.12.14 environment, NumPy 2.5.1,
  Pydantic 2.13.4 and pinned native OCP 8.0.1.1. This native interpreter
  satisfies the project range but differs from the current CI interpreter pins.
  The full supported-native matrix is not demonstrated by this local run.
- One serial worker; no Python HTTP computation server or external browser
  requests. Cross-origin isolation was false. Native BLAS was limited to one
  thread; no parallel CAD meshing was requested.

## Executed results

| Case | Result |
| --- | --- |
| Synthetic cone/plane | 432 + 64 observations; max parameter difference `1.11e-16`, residual difference `9.13e-16` |
| Retained nozzle session | 24,999 source vertices / 49,994 triangles; 1,261 + 642 selected observations; max parameter difference `3.55e-15`, residual difference `7.10e-15` |
| Retained recipe graph and replay | All seven nodes ready, empty errors/diagnostics; joint fit and every evaluated action/surface compared successfully |
| Solver diagnostics | Termination, rank, iterations and evaluations matched exactly for these fixtures |
| Numeric-only startup | Passed independently with no OCP or unrelated backend imports |
| Optional CAD | Actual Scansor bounded cylinder face valid, area `18π`; preview 254 vertices / 252 triangles |
| STEP | 4,276 bytes; browser and native readback valid with area `56.54866776462` and name `肩面 Ø12 — café`; millimeter token present and global writer units restored |
| Integrity failure | Same-size fixture corruption rejected by builder CLI; restoring the original bytes passed verification |
| Incompatible runtime | Deliberately mismatched Python version rejected for both queued worker requests; the second request could not reuse partial initialization |

The CAD-profile run took about 7.03 seconds through numeric initialization,
fitting and replay, and 16.94 seconds through optional CAD/readback. The
numeric-only run took about 7.53 seconds. These are fresh workers on loopback
with locally available files; disk/OS caches and other activity were not
controlled, and there is no production network or repeated statistical sample.

The numeric asset group contains 18,336,703 bytes. The CAD-profile artifact
contained about 43.8 MB including source, fixtures, reference results and
notices. Its server sent 61,125,653 bytes; the numeric-only server sent
38,350,382 bytes. Playwright request interception disables its HTTP cache,
so integrity verification and runtime loading re-read some assets. These
served-byte counts are not unique download size or a deployment budget.

The page's 16 ms timer recorded maximum gaps of 21.5 ms in the CAD-profile run
and 26.5 ms in the numeric-only run. This is a heartbeat without Three.js,
selection rendering or GPU uploads, not a real UI responsiveness assessment.

## Limits

Only Chromium was executed for this initial record. No large-scan, total
Python/JavaScript/GPU memory, offline, persistent-project recovery, full CAD/body
workflow, or real UI evidence is claimed. Timing thresholds are not regression
gates. Browser migration, other engines and ownership protections replacing
thread-based test assumptions remain later work.

The first disposable incompatible-version fault probe used an incorrect Wasm
MIME type and timed out during initialization; it was not a successful version
check. With `application/wasm`, the probe rejected both queued requests as
expected. Static hosting must preserve that MIME type; the repository runner
already serves it correctly.

The [redistribution inventory](redistribution.md) records remaining publication
gaps, and the manifest deliberately reports `redistribution_ready: false`.
Run the [documented commands](README.md) to produce fresh artifacts and reports;
none of the original session paths are needed to reproduce the harness.
