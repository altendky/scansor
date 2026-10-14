# Bounded worker cancellation policy

The maintainer selected this policy on 2026-10-13. It is an implementation
contract for the bounded browser experiment. The
[viewer implementation](../../browser_static/recovery.md) follows it. The
[probe](README.md) and [executed results](validation.md) establish the original
evidence and remaining gaps.

## Selected behavior

- **Cancel** requests a safe stop and immediately prevents the relevant
  unfinished result from being published. Show cancel requested until the
  computation actually stops; discarding a result does not free its worker.
- **Force** is a separate explicit user action that terminates the worker and
  rebuilds it. There is no automatic timeout escalation during computation.
  Explain the measured roughly 13–16 second runtime restart, plus any source
  reacquisition and recipe replay time.
- **Cross-origin isolation is optional.** Where available, use Pyodide's shared
  interrupt buffer for supported numerical interruption. Without it, Cancel
  waits for natural checkpoints; explicit Force remains available. Do not
  silently make an isolated host a deployment requirement.
- Preserve committed recipe edits and a recoverable source outside the worker.
  Force discards unfinished results, native shapes, caches and virtual files;
  rebuild derived state from the retained recipe and validated source. Recovery
  must not require the only copy of a transferred source buffer.

Worker crashes and failed initialization/recovery are failures, not user
cancellation. They need actionable states and outstanding-request settlement.
The policy prohibits automatic escalation of a polite cancellation request;
normal disposal of a failed runtime is distinct from forcing active work.

## Progress and publication

Report real phases such as asset download/verification, runtime initialization,
source loading, graph evaluation, fitting, CAD, export and recovery. Report
completed nodes or iterations only where the code provides checkpoints. Do not
invent a percentage or promise immediate interruption inside an opaque OCCT
call. Outbound phase messages can reach the UI even when inbound ordinary
worker messages cannot run during synchronous computation.

Each response must be associated with worker generation, request identity,
source identity and graph revision/epoch. Cancel fences publication immediately
on the coordinator, including a result already in transit. Old workers,
obsolete requests and stale revisions cannot publish ready graph state or
downloads. Serialize computation on each Pyodide instance.

Settle every pending promise exactly once on success, cancellation, error,
worker replacement, crash or startup failure. `Worker.terminate()` generates no
automatic completion reply; ignoring a stale message alone is insufficient.
Keep the existing HTTP adapter's observation-abort behavior separate from a
request to cancel computation, and preserve the native lifecycle semantics.

## Recovery and implementation checks

Retain the last committed recipe, actual source/model identity and a usable
File/Blob, pinned example URL or intentionally persisted source. Initialize a
new worker generation, reload and validate that source, replay the committed
recipe and rebuild derived caches/shapes. Failed replay must settle requests
and preserve the retained edits for retry or export. Retained display geometry
must be identified as belonging to the last committed revision, or cleared.

Interrupts can leave partial mutations, even if no result was published. Compute
into disposable state and commit only a current, uncancelled operation. Clear
the interrupt buffer after handling it. Validate subsequent fitting, geometry
and export, including global STEP-unit restoration and temporary-file cleanup.

The implementation in #208 must cover idle/queued/numerical/CAD cancellation,
rapid edits, worker loss/replacement, initialization/recovery failure, repeated
cancellation and successful subsequent work. Test both isolated and ordinary
hosts and verify promise settlement, stale publication fencing, source
ownership and actual committed-recipe replay. The investigation has not
demonstrated full project recovery or interruption safety for every kernel call.

The tested native mesher completed before Pyodide handled SIGINT. OCCT has its
own progress/user-break mechanism; it was not tested here. Access through OCP
is a separate [non-agent investigation #213](https://github.com/altendky/scansor/issues/213).
Broader loss-of-work and persistence mitigation is the separate
[non-agent review #214](https://github.com/altendky/scansor/issues/214). Neither
is silently included in this cancellation implementation.

Shared bulk arrays, multithreaded fitting, autosave and browser-reload durability
are separate work. Host choice, browser coverage and workload limits remain
separate decisions/validation; optional isolation does not establish them.
