# Static browser application experiment

This assembles the real docking UI and a numerical Python worker into a
provider-neutral static site under `/app/`. It opens the retained nozzle using
the native 30-action default, edits selections, evaluates numerical graph
actions, and downloads/uploads recipes. A `--cad` runtime additionally exposes
the existing supported face/body workflows and STEP bundle export. A numerical
artifact disables those controls and rejects CAD recipes without replacing
current actions. The native HTTP viewer retains its capabilities. This is a
bounded experiment, not general scan ingestion or a browser-only product decision.

The worker backend also implements bounded strict-PLY source operations and
retains accepted user-source recovery references outside the disposable worker.
The common UI exposes the source picker and empty projects; full both-backend
UI acceptance remains #243. Actual source-only measurements are recorded, and
fit verification uses the selected bounded semantic contract from #266. See the
[worker import evidence](../../docs/src/project/planning/worker-scan-import.md)
for ownership, measured limits, verification scope and remaining delivery gates.

## Build and preview

Use empty external output directories, following the AGENTS.md temporary-file
policy for agent probes. Install the existing locked tools as documented in the
[runtime experiment](../browser_runtime/README.md). From the checkout:

```sh
npm run build --prefix experiments/browser_viewer
uv run scripts/build-browser-runtime.py --output "$TASK_RUNTIME" --cad
uv run scripts/build-browser-app.py --runtime "$TASK_RUNTIME" --output "$TASK_SITE"
uv run scripts/build-browser-app.py --output "$TASK_SITE" --verify
node experiments/browser_static/preview.mjs --directory "$TASK_SITE"
```

The variables denote chosen paths, not existing session files. The stdlib-only
app builder consumes an already built workspace and verified runtime; it does
not install dependencies or invoke Vite. Rebuild the UI before assembly after
changes. `--cad` includes the approved optional wheel without importing it at
startup; the first CAD action verifies and loads it. Omit it for a numerical-only
artifact. Runtime assets can be reused
through the runtime builder's hash-checked `--cache`.

The default package retains `redistribution_ready: false`. Assemble the reviewed
[notice/source package](../browser_runtime/redistribution-package.md) and pass
`--redistribution "$TASK_NOTICES"` to both builders to provide the selected
runtime and UI requirements. This option supplies evidence, not publication
authorization. Numerical-only packages omit CAD sources and build controls.

## Package and startup

The site contains a landing page, `/app/` HTML/CSS, bundled React workspace,
application/helper modules, Three.js and its required controls/lines, and runtime
assets under `app/runtime/<runtime-manifest-sha256>/`. Relative application URLs
and import-map entries also work at nested paths. Native `backend-config.js`
defaults to HTTP; assembly generates a worker config with the versioned URL.
HTTP downloads assets in worker mode; `postMessage` carries computation requests
and pushed graph snapshots between the UI and worker.
No computation API server is started or used as a fallback.

One `site/manifest.json` records separate UI/runtime revisions and dirty status,
build-input hashes and retained-example provenance. The source ZIP includes the
nozzle, identity sidecar, model, selections and saved recipes from the runtime
allowlist. Assembly checks source-record and fixture manifest hashes, without
reading ignored local inputs. The manifest uses
with schema_version 1, kind `scansor-static-app`, `entry: app/index.html`, and
`assets: [{path, bytes, sha256}]` using site-relative paths. Both UI and versioned
runtime assets are listed. Browser startup verifies numerical assets before
loading. Missing/corrupt assets, incompatible versions, worker failures and
startup timeouts fail explicitly and terminate the failed worker. The timeout
bounds initialization only, not active computation. Startup/runtime failures
retire the UI. Worker recovery download uses the last accepted recipe kept
outside the worker; it does not preserve drafts, results, or reload state.
The [worker recovery controls](recovery.md) implement polite cancellation and
explicit Force/restart with the same source and committed recipe. Cross-origin
isolation is optional. A CAD-loading failure leaves numerical operations usable;
Force/restart after repairing the assets retries CAD. It does not select another
backend or substitute approximate geometry.

## Verification

No dependency installation is performed. Use the existing browser-viewer
Playwright installation, and provide external artifact/report paths:

```sh
node experiments/browser_static/validate.mjs --directory "$TASK_SITE" \
  --playwright-directory experiments/browser_viewer --output "$TASK_REPORTS"
node experiments/browser_static/validate.mjs --directory "$TASK_SITE" \
  --output "$TASK_ISOLATED_REPORTS" --isolation
node experiments/browser_static/validate-workspace.mjs --directory "$TASK_SITE" \
  --output "$TASK_WORKSPACE_REPORTS" --native-python .venv/bin/python
node experiments/browser_static/validate-cad.mjs --directory "$TASK_SITE" \
  --output "$TASK_CAD_REPORTS" --native-python .venv/bin/python
node --test experiments/browser_static/server.test.mjs
```

Preview options: `--port 4173`, `--prefix /preview/nested/`, `--isolation`.
Validation options: `--browser chromium|firefox|webkit` (installed engine only),
`--isolation`, `--repository <checkout>` (defaults to two directories above the
browser-viewer directory). Output must be fresh and outside both the artifact
and repository, with ancestor/symlink guards before and after mkdir. Existing
reports fail rather than being overwritten. Variables denote chosen paths; the
scripts have no absolute repository/session paths.

The server verifies every listed asset before listening, rejects invalid or
duplicate paths and all symlink components, rechecks hashes and symlinks on
requests, serves only the allowlist plus manifest, and supports GET/HEAD only.
WASM has application/wasm MIME. Unknown files/API paths return 404; no Python
computation server or API handler is started. It listens on loopback only.

The driver tests `/app/` and `/preview/nested/app/`, each with six cases:

1. Real workspace startup reaches `#status.dataset.runtimeReady='true'`, a mesh
   canvas, initialized Save, enabled fitting, CAD capability matching the
   packaged runtime, and a working
   Show panel layout control.
2. A Pyodide WASM request deliberately returns HTTP 404; visible `#startup-error`
   and nonempty `#startup-detail` identify the missing asset. The artifact stays
   unchanged.
3. `?backend=unavailable` produces an explicit startup error without an HTTP
   computation fallback.
4. An injected fatal message after actual runtime initialization clears any
   ready status and disables the failed workspace, including failure during
   workspace startup. Only this fault case changes the
   served worker response; original/modified hashes and the delay are recorded.
   The artifact on disk and ordinary readiness cases remain unchanged.
5. The actual configured worker URL returns HTTP 404. Startup fails visibly,
   never reaches runtime-ready and keeps fitting disabled.
6. The Pyodide WASM response returns HTTP 200 with one changed byte and the
   original length. Startup identifies the asset integrity failure before
   fitting can run; the on-disk artifact remains unchanged.

External network and any `/api/` requests are blocked and fail checks. The
driver closes each context/server/browser and writes raw requests/checks/errors
outside the site. Runtime startup has a 180-second watchdog, fault handling a
60-second watchdog and unsupported selection a 15-second watchdog. These are
probe cleanup limits, not production cancellation or automatic escalation.

The script re-verifies the entire manifest after execution and fails if it
changed. That check does not promise absence of unrelated
unlisted files. The self-tests validate serving/MIME, allowlist and method
boundaries, corruption/path rejection, symlinks, changed assets/manifests, and
report output guards without modifying excluded directories.
Actual executed checks and limitations are recorded in [validation](validation.md).
The CAD profile's separate case inventory and limits are recorded in
[CAD validation](cad-validation.md).

## Static CI artifact verification

The reusable Browser workflow preserves the native HTTP integration job and
adds a separate Linux/Chromium static-worker job. It first assembles and verifies
the reviewed locked notice/source package, then builds the locked CAD
runtime and UI once, verifies manifests, runs all six startup cases at root and
nested paths with and without isolation, numerical workspace/native parity,
CAD download/native STEP readback, both recovery modes, and a numerical-only
profile that disables CAD and rejects CAD recipes. The four workload drivers
run serially afterward. Their correctness assertions are gates; timings,
responsiveness and sampled memory have no hard performance thresholds.
Other engines, mobile, real-network startup and offline operation remain
unverified. Successful closed Body readback uses the labelled synthetic scaffold,
not a successful closed Body fitted through the retained nozzle UI.

Local equivalent, after the existing locked dependency setup and UI build:

```sh
uv run scripts/check-browser-static.py --output "$TASK_STATIC_CHECK" \
  --native-python .venv/bin/python --measurements
```

Output must be empty and external. `--cache "$TASK_RUNTIME"` reuses only
hash-verified runtime downloads. `--redistribution-cache "$TASK_NOTICE_CACHE"`
can reuse a separate private hash-verified source/notice cache; otherwise that
cache stays inside `work/`. The package is passed to both CAD and numerical
runtime/UI builders. Its standalone core requirement remains external until
the consuming runtime verifies its actual locked bytes and component notices.
The script installs nothing, invokes no Python API
server, performs no OCP source build and configures no host. It is a Linux
orchestrator: its 30-minute per-command watchdog terminates an entire disposable
process group. UI cancellation and Force policy are unchanged.

`evidence/verification.json` records actual step exits, elapsed time, source and
site/runtime and selected source/notice manifest hashes. Named JSON reports
retain requests, fault checks, parity, browser/CPU versions and measurement
caveats; local logs and downloaded
binaries stay in `work/`. CI uploads only allowlisted JSON evidence, even on
failure, for 14 days. Trace ZIPs can contain downloaded runtime bytes and are
not uploaded. No persistent binary cache is shared with other workflow runs.

The [reviewed redistribution package](../browser_runtime/redistribution-package.md)
preserves component notices, covered sources and the executed approved OCP/OCCT
replacement evidence. Supplying it permits requirement-based readiness; it does
not bypass the artifact gate or authorize Pages publication. Once both manifests
record explicit clean readiness with no gaps and matching revisions, the script stages
only hash-verified listed assets plus JSON evidence in `release/`; the workflow
can upload that exact tested package as `validated-static-browser`. Deployment
must consume that artifact and its manifest, without rebuilding. No release is
staged while the gate is closed. Preparing this workflow does not publish or
change scansor.app DNS.
Fixture tests alone do not demonstrate worker/UI startup or rendering, practical
scan capacity, memory budgets, project recovery or other browser engines.

## Raw-source UI workflow acceptance

The separate raw-source acceptance driver uses the actual packaged CAD app in
plain and isolated Chromium contexts:

```sh
node experiments/browser_static/validate-raw-source-workflow.mjs \
  --directory "$TASK_SITE" --output "$TASK_RAW_UI_REPORTS" \
  --native-python .venv/bin/python
```

It uses real File, pointer, fit, export/download, Save actions, New project and
renamed-source lookup controls. The Side-view rectangle is frozen before fitting;
membership is independently predicted from delivered pointer coordinates and the
original rows. Independent raw triangle areas and radial residual arithmetic,
full replay/native records and finite budgets are checked. The actual downloaded
STEP is reopened against the UI-produced cylinder at the existing export
precision. These are implementation and export-fidelity checks on a rough scan,
not physical ground truth or a metrology accuracy claim. The generated analytic
scene remains a separate oracle.

CI runs this acceptance before uploading the validated site. Its complete JSON
operands, receipts and actual downloads are retained as the separate
`raw-source-ui-acceptance` artifact when redistribution is ready. They are not
added to the existing 25-stage release envelope or published site. The native
HTTP browser suite uses the same DOM harness. Appended observers only read the
manifest-verified app/worker state; they do not author recipes or selection IDs.

## Workload measurements

The [workload evidence and limits](workload-validation.md) distinguish actual
fresh graph fitting from cached publication, display mesh size from selected
observations, and Wasm capacity from overlapping JavaScript/array/GPU counters.
Run measurements serially with fresh external output paths and the existing
native interpreter/Playwright installation:

```sh
node experiments/browser_static/measure.mjs --directory "$TASK_SITE" \
  --output "$TASK_TIMING_REPORTS" --native-python .venv/bin/python
node experiments/browser_static/measure-memory.mjs --directory "$TASK_SITE" \
  --output "$TASK_MEMORY_REPORTS"
node experiments/browser_static/measure-fitting.mjs --directory "$TASK_SITE" \
  --output "$TASK_FITTING_REPORTS" --native-python .venv/bin/python
node experiments/browser_static/measure-generated.mjs --directory "$TASK_SITE" \
  --output "$TASK_GENERATED_REPORTS" --native-python .venv/bin/python
```

The timing/memory drivers require a CAD-profile site. The fitting driver freezes
three deterministic float64 component fixtures into its output (or reuses
`--fixtures <directory>`); the generated driver creates three explicitly
test-only fixture-remapped artifacts inside its output. Neither changes the
input site or fitting objective. Native source/fixture/asset hashes are retained.
Use `--playwright-directory <installed viewer>` and `--repository <checkout>`
when tools are outside the measuring checkout. Output guards exclude both the
tool checkout and the harness checkout. No dependency installation is performed.

These measurement drivers use Chromium; memory inspection uses its CDP API.
Other installed engines can run the existing correctness validators with
`--browser firefox|webkit`; unavailable engines remain reported gaps.
Memory/component instrumentation is explicit and disables HTTP caching.
The UI timing driver does not intercept requests; its existing preview still
uses `no-store`, so it cannot demonstrate warm HTTP-cache benefit. Local timings
and sampled memory observations are advisory evidence, not CI latency/peak
budgets, support expansion, or publication authorization.

## Publication boundary

The [manual Pages deployment runbook](deployment.md) consumes only a verified
successful main CI artifact, preserves its exact site bytes and keeps publication
disabled until separate approval. Its landing page links to `/app/`; provider
setup, domain verification, DNS, HTTPS and actual-host smoke checks remain gated.

The default package retains `redistribution_ready: false`. The verified
[notice/source package](../browser_runtime/redistribution-package.md) supplies
selected runtime and UI requirements before redistribution. Hashes record
provenance, not a signed deployment trust root. This build does not authorize
hosting, DNS or publication, select a browser-only architecture, or demonstrate
offline use.
