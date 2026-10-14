# Static browser application experiment

This assembles the real docking UI and a numerical Python worker into a
provider-neutral static site under `/app/`. It opens the retained nozzle using
the native 30-action default, edits selections, evaluates numerical graph
actions, and downloads/uploads recipes. A `--cad` runtime additionally exposes
the existing supported face/body workflows and STEP bundle export. A numerical
artifact disables those controls and rejects CAD recipes without replacing
current actions. The native HTTP viewer retains its capabilities. This is a
bounded experiment, not general scan ingestion or a browser-only product decision.

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

The driver tests `/app/` and `/preview/nested/app/`, each with four cases:

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
Fixture tests alone do not demonstrate worker/UI startup or rendering, practical
scan capacity, memory budgets, project recovery or other browser engines.

## Publication boundary

The package deliberately retains `redistribution_ready: false`. Complete the
[runtime redistribution inventory](../browser_runtime/redistribution.md) and
static UI dependency notices before publishing. Hashes are provenance, not a
signed deployment trust root. This build does not authorize hosting, DNS or
publication, select a browser-only architecture, or demonstrate offline use.
