# Browser cancellation investigation

The [selected policy](decision.md) is optional isolation, polite Cancel and
explicit Force only. See the [validation record](validation.md) for what ran.

This is a portable investigation harness for Scansor issue #203. It consumes
the hash-verified CAD artifact built by `scripts/build-browser-runtime.py` and
an existing Playwright installation. It does not need pytest, the original
session's assets, hard-coded repository paths, or another source bundle. No
dependency installation, production cancellation mechanism, hosting choice,
or project recovery implementation is performed.

From a Scansor checkout with its existing native/browser environment, first
build a fresh artifact outside the repository (or reuse a verified existing
artifact):

```sh
uv run scripts/build-browser-runtime.py --output "$TASK_ARTIFACT" --cad
node experiments/browser_runtime/cancellation/run.mjs --directory "$TASK_ARTIFACT" \
  --playwright-directory experiments/browser_viewer --output "$TASK_RESULTS"
node experiments/browser_runtime/cancellation/summarize.mjs --directory "$TASK_RESULTS"
```

Set `TASK_ARTIFACT` and `TASK_RESULTS` to external temporary directories. A
builder `--cache` option
can reuse existing verified assets. Use the AGENTS.md temporary-directory
policy for disposable output. This command does not make a dirty checkout or
non-redistribution-ready artifact suitable for publication.

The runner defaults to Chromium and performs both isolated and plain runs.
`--isolation isolated|plain|both` selects the cases, and
`--browser chromium|firefox|webkit` selects an already-installed engine. Missing
engines fail rather than downloading or substituting. The report records the
actual browser/runtime versions, artifact source revision, dirty flag, fixture
hash, manifest hash, and candidate-file hashes. It serves under a non-root URL,
rejects external network requests, verifies every artifact asset before use,
and writes partial results on failure. Operation waits have a 60-second cap;
startup has a 180-second cap. Watchdog cleanup closes the probe browser, and
does not define the application's cancellation policy.

The isolated run sets COOP `same-origin` and COEP `require-corp`; the plain run
does not. Both execute unchanged Scansor `fit_cone_plane`, sphere
`surface_patch`/`tessellate_face`, and the same default OCP native mesher call.
Both test ordinary cancellation messages, event-loop yielding between complete
fits, explicit Force termination/recreation, and 64 KiB transferable ownership.
Only the isolated run tests a one-byte SAB/SIGINT interrupt buffer.

**SIGINT is not OCCT `Message_ProgressRange` / `UserBreak`.** Neither an OCCT
cooperative callback nor new binding access is implemented. Investigation of
those facilities remains separate. The cooperative fitting case yields between
whole unchanged fitting calls; no in-solver checkpoint is demonstrated.

The long fit repeats a frozen 496-row fixture 300 times (148,800 observations).
It is a duration stress fixture beyond the current bounded GUI selection cap,
not evidence for actual large scans. Successful fits assert parity against the
native result stored in the artifact. No fresh native cancellation run is made
by this candidate.

The selected policy is optional isolation, polite Cancel and explicit Force,
with no automatic escalation. Use SIGINT where available and safe; otherwise
Cancel waits for natural checkpoints. The page immediately fences publication
for a cancelled request,
while retaining the raw worker outcome so delayed ordinary message handling is
visible. Explicit investigative SIGINT is another diagnostic action, not a
production implementation. Isolation is optional for the bounded deployment;
the non-isolated fallback and explicit Force must remain available.

Terminating a worker does not manufacture replies for queued/in-flight
requests. The report tests for their absence after 500 ms; it does not implement
the real adapter's required promise rejection. The termination API return time
is not an OS-level stop latency or memory-release measurement.

State reports use the artifact's actual committed recipe hash and source/model
identities. A counter and virtual-file marker reveal private state discarded on
termination. Recreated state is freshly initialized from the artifact; actual
Scansor recipe replay after a crash is **not** implemented here. Source
reacquisition, replay time, export cleanup, interrupted global STEP settings,
GPU rendering, memory peaks, persistence, and browser coverage remain separate
validation work. The transfer test only demonstrates sender detachment and
reconstruction from a retained File for a small JavaScript buffer.

`results.json` retains raw events, progress phases, source/recipe state,
publication fencing, heartbeat, request records and errors. `summary.json`
extracts latencies and restart observations. Use a fresh, empty output directory
for each run to preserve earlier reports.

Primary references:

- [Pyodide interrupts and native signal checkpoints](https://pyodide.org/en/stable/usage/keyboard-interrupts.html)
- [Cross-origin isolation](https://developer.mozilla.org/en-US/docs/Web/API/Window/crossOriginIsolated)
- [Worker termination](https://developer.mozilla.org/en-US/docs/Web/API/Worker/terminate)
- [Transferable ownership](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects)
