# Browser runtime probe

This is a bounded runtime and source-packaging experiment, separate from the
HTTP browser viewer. It executes actual Scansor code inside a module Web Worker;
Node serves static files only. It does not select a browser-only architecture or
provide the complete application UI.

See the [initial validation record](validation.md) for what was actually run.

## Build and run

Use the agent temporary directory prescribed by AGENTS.md, or another empty
output directory outside the repository. No original session files are required.
Install the existing locked native environment and browser test tools first:

```sh
uv sync --locked
npm --prefix experiments/browser_viewer ci
npm --prefix experiments/browser_viewer exec playwright install chromium
uv run scripts/build-browser-runtime.py --output /path/to/empty/artifact
node experiments/browser_runtime/run.mjs --directory /path/to/empty/artifact
```

The builder is a stdlib-only standalone script. Its native reference uses
`.venv/bin/python` by default; `--native-python` can select another existing
environment. `--cache` reuses downloaded assets with matching filenames and
hashes, and rejects corrupt cache entries. Downloads are versioned and pinned
in `runtime-lock.json`, rather than relying on the root project installation in
Pyodide. Root Python remains `>=3.12,<3.14`; this separate browser profile is
Pyodide 314.0.7, Python 3.14.2, NumPy 2.4.6 and Pydantic 2.12.5.

For the separately loaded, approved OCP/OCCT lineage, use `--cad` in both
commands with a fresh output directory. Numeric fitting completes before OCP
is imported. Missing CAD assets fail explicitly. The default CAD profile is for
local investigation. A separate verified
[notice/source package](redistribution-package.md) can supply selected-artifact
redistribution evidence through `--redistribution`; publication authorization
remains separate.

The runner supports `--browser chromium`, `firefox` or `webkit` when that engine
is installed. A failure or unavailable engine is not evidence of support. It
serves under `/runtime-probe/` to exercise relative URLs and rejects external
network requests. Reports identify actual browser/runtime/source versions and
hashes, timings, requests, parity and a main-thread heartbeat. Timing is
observational, without performance gates or a real rendering workload.

## Capabilities and source boundary

`runtime-lock.json` explicitly lists the numerical source closure: source and
sidecar validation through Scansor's isolated PLY reader, `NozzleWorkspace`, the
existing cone/cylinder/plane/sphere and constrained fitting modules, selection
growth/regions, feature input/reuse helpers and `FeatureGraph`. Imports include
the incidental nozzle joint-view helper. Lazy CAD branches in surface extents
are present but cannot execute in the numeric-only profile.

The smoke harness runs the following paths; listing a module in the bundle does
not demonstrate every operation it implements:

- A generated 432-cone/64-plane float64 fixture, frozen once by native Python
  and supplied unchanged to both runtimes, fitted by `fit_cone_plane`.
- The retained 24,999-vertex/49,994-triangle nozzle, with its manifest, sidecar,
  model and saved selections, through `NozzleWorkspace.fit`.
- The retained cone-plane recipe through `FeatureGraph.evaluate`, then saved
  recipe replay in a new graph, with all nodes ready and no errors/diagnostics.
- Optional Scansor cylinder intersections, bounded face construction, kernel
  validity/area, tessellation and named millimeter STEP. Browser-produced STEP
  is reopened natively to verify validity, area and its Unicode name.

No DuckDB, PyArrow, psutil, CLI imports, Linux ingestion supervision, thread creation,
subprocesses, arbitrary file loading or full-resolution scans are bundled. The
worker workspace adds recipe import and feature-reuse authoring helpers through
the same numerical graph. The numerical probe asserts that unrelated dependencies,
including OCP, have not been imported. The optional CAD source closure includes
the native face-authoring/export helpers and deterministic geometry cases.
The bounded UI integration and its separate CAD case inventory are documented
in the [static experiment](../browser_static/README.md).

## Reproducibility and integrity

The source archive uses a sorted allowlist and fixed ZIP timestamps. The
manifest records checkout revision, whether the checkout was dirty, every
source-file hash, fixture/archive/asset hashes and wheel notices. Native results
are produced from that archive in an isolated source directory, so editable
checkout imports cannot mask a missing source dependency. Native fixture bytes
and their SHA-256 are retained in the artifact; do not regenerate the noisy
fixture independently in the browser.

Parameter, residual, RMS and frame comparisons use `rtol=atol=1e-10` for these
small well-conditioned fixtures. This permits floating-point differences far
below their 0.001 synthetic noise and recorded fitting precision. Solver
termination, rank, iterations and evaluations are checked exactly for these
fixtures, with raw solver diagnostics retained in reports. This tolerance and
control-flow agreement do not establish equivalence for arbitrary inputs.

The builder, runner and worker detect missing/corrupt assets. Check an existing
artifact without starting a browser:

```sh
uv run scripts/build-browser-runtime.py --output /path/to/artifact --verify
```

Altering a wheel or source archive must fail verification. The manifest is a
build provenance record, not a cryptographically signed deployment trust root.
The worker verifies numeric assets before importing them and separately checks
the CAD wheel. Re-reading verified URLs assumes an immutable, trusted local
artifact server; the hashes alone cannot protect a server that changes content
between reads.

## Ownership, progress and limitations

One worker executes requests serially. Initialization reports honest phases;
the `initialize` operation reports readiness without running numerical or CAD
smoke tests, supporting the static UI bootstrap.
Synchronous fitting and native kernel calls do not offer granular progress in
this harness. Results cross the boundary as JSON strings, avoiding retained
Python result proxies. The page terminates the worker after the probe, releasing
its virtual filesystem and runtime. It handles worker startup/message failures;
the runner always closes the browser/server and has a bounded watchdog.

The full application cancellation/recovery contract belongs to #203/#208.
The [cancellation investigation](cancellation/README.md) records the portable
probe, measured findings and selected bounded-worker policy.
Prior OCP checks had two failures caused by Python thread creation in
`test_face_arrangement.py`: those environment assumptions cannot run in this
single-worker runtime. Their ownership protections must be checked with worker
generation, serial operation and replay-scope cleanup tests in the later CAD/UI
work, rather than silently dropping their safety intent.

Local artifact requests and heartbeat results do not establish production
download time, total Python/JS/GPU memory, large-scan suitability, offline use,
or browser support beyond executed engines. Integrity checks temporarily hold
asset bytes; the CAD wheel expands substantially in the virtual filesystem.

## Redistribution inventory

The builder extracts license/notice files present in numerical wheels into
`notices/` and records them. That is an inventory, not a complete transitive
license audit. See [redistribution inventory](redistribution.md) for core,
compiled dependency and optional CAD obligations. The artifact is a local probe;
do not publish it as a redistribution-ready runtime until the recorded notice
and source gaps are resolved. Existing OCP/OCCT approval does not approve
alternative bindings or new copyleft dependencies.
