# Worker progress, cancellation and recovery

The bounded browser viewer implements the maintainer's
[cancellation policy](../browser_runtime/cancellation/decision.md). The HTTP
backend retains observation-abort and native shutdown behavior; its separate
source-import Cancel does not add native fitting/CAD interruption.
There is no automatic escalation of active computation to worker termination.

## User controls

**Cancel computation** immediately fences pending replies, graph publications
and binary downloads. It turns Auto off. Without isolation, synchronous fitting
or CAD must return before the worker can receive the request. The viewer shows
"Cancel requested" until the safe-boundary restoration finishes. Progress is
coarse loading/queued/running/completed state, with no percentage or invented
iteration/node progress inside a synchronous call.

Optional cross-origin isolation enables a shared numerical interrupt buffer.
The worker installs it only during numerical graph evaluation when the recipe
contains no CAD actions. Other phases, CAD, export, cleanup and restoration
have interrupts disabled. This restriction also protects CAD from delayed
numerical lifecycle messages. Pyodide describes both the
[shared-buffer mechanism](https://pyodide.org/en/stable/usage/keyboard-interrupts.html)
and [disabling interrupts](https://pyodide.org/en/stable/usage/api/js-api.html#pyodide.setInterruptBuffer).
Neither mechanism provides OCCT cooperative cancellation. That remains
[issue #213](https://github.com/altendky/scansor/issues/213).

**Force and restart** is an explicit action. It terminates the current worker,
settles pending operations, creates a new worker, verifies the same pinned
runtime and accepted empty/example/user source, and replays the last accepted recipe through
ordinary replacement validation. It also retries failed startup or recovery.
The previous probe measured roughly 13–16 seconds for local runtime recreation;
downloads, source loading, recipe validation and subsequent CAD loading can add
time. The button does not promise that operating-system memory is reclaimed
before it returns. Browser termination gives worker code no cleanup opportunity,
as documented by [the Worker API](https://developer.mozilla.org/en-US/docs/Web/API/Worker/terminate).

The controls remain outside locked editing panels. Recovery reuses the renderer
and immutable display geometry, clears derived geometry/review previews, and
leaves Auto off until the user chooses to evaluate again. A startup failure
cannot mark the viewer ready merely because its earlier startup callback ended.

## State and ownership

The coordinator retains a cloned last accepted recipe and source checkpoint.
For an example, the backup is its hash-verified packaged source; for a user scan,
it retains the accepted File reference outside the disposable worker. Empty mode
recovers empty. A transferred display buffer is not an original-source backup.
Recovery verifies content and interpretation before validating the accepted
recipe on a private candidate. It never substitutes the nozzle for an imported
source. Failed source reading, identity checks or replay preserves the accepted
checkpoint and recipe for recovery. File retention is memory-only and cannot
guarantee that a disk-backed File remains readable unchanged.

Source transitions remain private until the coordinator adopts a complete
confirmed source/graph/display bundle and its recovery reference together.
Cancel/Force before that boundary preserves the prior checkpoint; after it,
the accepted source wins even if the worker promotion acknowledgement is lost.
See the [worker import evidence and delivery gates](../../docs/src/project/planning/worker-scan-import.md).

For graph work, polite cancellation reconstructs the graph against the same immutable float64
source. A failure-only checkpoint preserves unchanged failed/blocked nodes,
diagnostics and escaped-job failure memoization. Explicit Evaluate remains the
retry path. Successful derived results, partial mutations, pending evaluations
and export bytes are discarded before another request is allowed. A
coordinator acknowledgement at each managed operation boundary prevents queued
work from adopting a cancelled operation's failures as its recovery baseline. A
`KeyboardInterrupt` is a distinct `computation_cancelled`/409 error and is not
memoized as a failed evaluation. A new publication generation permits a fresh
graph revision without accepting an old terminal reply.

Force and crashes lose worker-only results, native objects, failure diagnostics,
caches and virtual files. Recovery restores definitions and starts with dirty
derived state. Unacknowledged edits and unsaved form drafts can be lost. The
coordinator copy is memory-only: tab reload, closure or browser/process loss can
lose it too. Download actions for durable preservation. Broader mitigation is
[non-agent issue #214](https://github.com/altendky/scansor/issues/214); this work
does not introduce autosave or durable source saving. Common worker source
operations now have an implementation; the source-authoring UI remains #243,
and strict imported-fit parity remains a delivery gate in #266.

Request IDs remain monotonic across workers. Callback ownership and a distinct
worker lifetime fence old phases, errors, readiness, graph state and binary
transfer confirmations. Publication generations fence rapid edits; cancellation
has its own serial so superseded mutation recovery cannot adopt a cancelled
edit. Source identity and recipe tokens validate graph operations. Graph
snapshots carry revisions. Pending requests are bounded to 64, including
aborted-observation tombstones awaiting their replies. `AbortSignal` only stops
observation; it is separate from Cancel computation. Startup has a watchdog;
active work does not.

## Reproduce validation

Use an assembled external site and fresh report directories:

```sh
node experiments/browser_static/validate-recovery.mjs --directory "$TASK_SITE" \
  --output "$TASK_RECOVERY_REPORTS"
node experiments/browser_static/validate-recovery.mjs --directory "$TASK_SITE" \
  --output "$TASK_ISOLATED_RECOVERY_REPORTS" --isolation
```

The harness runs actual Scansor operations in a worker and separately labels
faults such as held replies, old-worker messages, failed asset fetches and an
uncaught worker error. Its observation watchdogs are test limits, not production
automatic Force. See the [recorded validation](recovery-validation.md) for what
ran and the remaining limits.
