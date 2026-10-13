# Portable cancellation validation

Executed on 2026-10-13 with the clean repository-owned CAD artifact from commit
`d540f1776add80f23f2376fd90562b09d08371e5` (artifact dirty flag false), Chromium
156.0.8078.4, Pyodide 314.0.7 / Python 3.14.2 / NumPy 2.4.6 / Pydantic 2.12.5 /
OCP 8.0.1.1 from the pinned OCP.wasm wheel. Fixture SHA256 remains
`751a6793d99b26b412000b69d47de3d61f790b4c8bce79e884282e3f6b48f84a`.

The artifact's existing manifest verified every asset before startup. No
original session-only archive, pytest fixtures, wheel downloads, dependency
installations, or source edits were used. The runner mounted the artifact and
candidate at separate paths below a non-root URL; candidate code contains no
absolute repository/session paths. The CLI supplies the artifact, existing
Playwright directory, and output directory.

Both isolated and plain runs completed with no unexpected network requests or
page errors. All summary checks passed; SIGINT checks are explicitly not
applicable to the plain run. Successful fits asserted parameters against the
native result frozen in the artifact (rtol=atol=1e-10). Native Python was not
rerun by the cancellation candidate. Subsequent fitting/CAD work after tested
interruptions and a fit after recreation succeeded.

| Observation | Isolated | Plain |
| --- | ---: | ---: |
| Large repeated-fixture fit baseline | 644 ms | 668 ms |
| Actual Scansor sphere tessellation baseline | 3,294 ms | 3,566 ms |
| Direct default OCP mesher baseline | 2,680 ms | 2,713 ms |
| Ordinary cancel message delay | 1,166 ms | 1,228 ms |
| Yield-between-fits cancellation outcome | 9.93 ms, 11 fits | 2.0 ms, 10 fits |
| SAB/SIGINT large-fit outcome | 30.8 ms | Not available/tested |
| SAB/SIGINT actual tessellation outcome | 2,662 ms | Not available/tested |
| SAB/SIGINT direct mesher outcome | 2,693 ms | Not available/tested |
| Explicit Force API call return | 0.030 ms | 0.100 ms |
| Full worker recreation to ready | 12.64 s | 12.77 s |
| Main-thread 16 ms timer maximum gap | 36.02 ms | 26.10 ms |

These are single observations, not guarantees or comparative benchmarks.
Worker initialization includes the larger fixture allocation. First small-fit
timings also include lazily loaded NumPy testing machinery; the candidate is
not a replacement for the earlier warmed performance measurements.

The repository-owned probe was rerun after adding output containment, incomplete
report checks and bounded transfer-worker cleanup. Both modes passed every
applicable assertion with zero page errors or unexpected requests. In that run,
ordinary cancellation messages waited about 1.39/1.30 seconds, yielding stopped
after ten complete fits in 7.21/4.80 ms, and isolated fitting SIGINT surfaced in
9.50 ms. CAD/native SIGINT outcomes still took about 2.76/2.78 seconds after
meshing. Runtime recreation took 12.98/12.56 seconds. The main-thread timer's
maximum gap was 19.26/26.00 ms. Isolated/plain order applies to paired values.
Force's API return was 0.015 ms in the isolated run and rounded to zero at the
plain page's clock resolution; neither measures OS stop latency. An earlier
repository run recorded a 71.13 ms heartbeat gap, illustrating why these samples
are not responsiveness guarantees. No full Scansor UI was loaded.

Ordinary polite Cancel immediately fenced publication on the coordinator side,
while the worker finished all 150 fitting calls and reported raw success.
Yielding between complete fitting calls allowed ordinary cancellation messages
to take effect at those boundaries. No in-solver checkpoint was added.

The isolated page had an available SharedArrayBuffer; the plain page had none.
The isolated SIGINT experiments again surfaced during Python work after native
meshing, rather than demonstrating interruption inside the OCCT mesher. Direct
meshing had completed and advanced the operation counter before interruption
prevented result serialization. **SIGINT is not OCCT UserBreak.** The candidate
does not implement or establish availability of Message_ProgressRange/UserBreak
callbacks; separate #213 work owns that investigation.

Force was a separate explicit test action in each mode. No pending job/ping
reply appeared during the following 500 ms. This proves lack of automatic
replies in this harness, not OS-level stop latency, memory release, or promise
settlement. The real adapter must reject every queued/in-flight promise itself.
No product policy of automatic escalation is proposed.

Recreation reset the worker-private operation counter and virtual-file marker,
and initialized the same actual source/model/committed-recipe hashes from the
artifact. This is identity/state-loss evidence, **not real recipe recovery
replay**. The independent transfer test detached a 65,536-byte sender buffer;
the retained File yielded a fresh buffer with matching tested endpoints after
its worker was terminated. It does not establish full-scan memory or durability
across page closure.

The first portable attempt used runPythonAsync for synchronous jobs. Although
the awaited interrupt rejection was handled, Pyodide's asyncio webloop emitted
the intentional KeyboardInterrupt again as page errors. The stricter candidate
correctly failed that run, retaining `run-1/results.json` and `run-1.log`.
Switching these entirely synchronous jobs to runPython kept interruption in the
normal catch path. The complete corrected run is `run-2/results.json` with
`run-2/summary.json` and `run-2.log`. The initial session probe did not enforce
a page-error gate; its measured interruption observations remain initial
evidence, not proof that its async harness error classification was complete.

The maintainer selected optional isolation, polite Cancel with explicit Force
only and no automatic escalation; see the [decision](decision.md). The probe
demonstrates that the plain host
can execute fitting/CAD, polite cancellation boundaries and Force/recreation;
SAB diagnostics improve numeric interruption but do not make opaque CAD
immediately interruptible. No hosting provider was selected by this probe.

No real UI/GPU workload, other browser, full-resolution scan, memory peak,
OCCT cooperative cancellation binding, STEP-global cleanup during interruption,
autosave/persistence, or complete project recovery was tested. The 148,800-row
duration fixture is beyond the current bounded GUI cap and repeats the same
observations. No native #141 shutdown change, #213 binding work, or #214
work-loss redesign was implemented.
