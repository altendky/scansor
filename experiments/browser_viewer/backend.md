# Browser backend boundary

This is an internal interface for the bounded viewer experiment. HTTP/native
Python remains the default implementation. The static experiment supplies a
worker backend for retained examples, an empty project, one imported scan and the
optional approved CAD runtime.
This interface does not select a browser-only product architecture.

The UI calls Scansor operations through `backend.js`. `http-backend.js` owns
HTTP URLs, mutation headers, response decoding, and polling. Neither the UI nor
the graph-readiness helpers fetch computation endpoints directly.

## Bootstrap and capabilities

`createBackend({ type, factories, ...options })` constructs a backend, validates
its operations, and fails explicitly if the requested implementation is missing.
`http` is built in. Tests or a later deployment bootstrap can supply another
factory without changing the UI's operations.

`getBackend(options)` creates one shared instance on first use. The application
and startup recipe recovery use that same instance. The default type comes from
the `backend` URL query parameter, or `http` when omitted. A deployment can call
`getBackend` with its factory before loading the UI. An unavailable selection
fails rather than falling back to native computation.

Each backend declares `capabilities`. HTTP supports numerical graph operations,
CAD, retained examples and the bounded native single-scan transport. It reports
snapshot progress and no computation cancellation. `sourceImport` gates the
separate source transaction methods. The source-capable worker declares it true;
missing capability declarations in older factories reject those operations.
An implementation advertising source import must implement every source method.
`createBackend` rejects CAD-only and example operations
when their corresponding capabilities are unavailable. A reduced backend must
also diagnose unsupported graph action families during validation/evaluation;
the broad CAD flag is not a complete graph-operation support matrix.

## Operations

Methods return promises, except `graphUpdates` (an async iterator) and `close`.
Control payloads and graph snapshots retain the existing experiment schemas.
Optional operation options can carry an `AbortSignal`.

| Operation | Inputs and result |
| --- | --- |
| `loadWorkspace({generation?, token?, signal})` | Coherent metadata, graph, accepted generation and display `positions`/`indices` buffers |
| `getGraph({ revision, signal })` | Full graph or compact unchanged status |
| `replaceRecipe(payload, options)` | Token and recipe; updated graph |
| `importRecipe(payload, options)` | Token and recipe; graph with import warnings |
| `exportRecipe(state)` | Isolated recipe from the accepted snapshot |
| `evaluateGraph(payload, options)` | Explicit retry/evaluation request |
| `ensureGraph(payload, options)` | Required targets; ready graph or running status |
| `graphUpdates({ revision, signal, isCurrent })` | Graph/status updates through completion |
| `getExampleRecipe(options)` | Recipe for the active retained example |
| `listExamples(options)` | Active example and catalogue |
| `selectExample(payload, options)` | Example ID and token; select the workspace |
| `applyFeatureReuse(payload, options)` | Existing reuse-authoring payload and result |
| `faceCandidates(payload, options)` | Existing face-candidate payload and result |
| `previewFaces(payload, options)` | Existing face-preview payload and result |
| `applyFaces(payload, options)` | Reviewed face payload; updated graph |
| `exportCad(payload, options)` | Existing export settings; ZIP `ArrayBuffer` |
| `addScan({file, generation, token}, {signal, onProgress})` | Original File/Blob payload and expected accepted cursor; accepted workspace bundle |
| `getSourceOperation({operationId}, {signal})` | Status/progress for one opaque source operation |
| `cancelSourceOperation({operationId, generation, token}, {signal})` | Explicit cooperative import Cancel, using the reservation's original cursor |
| `resetProject({generation, token, confirmedDiscard:true}, {signal})` | Explicit empty-project transaction; accepted empty workspace bundle |
| `close()` | Stop this connection's requests/observations |

Selections remain recipe edits; authoritative fitting remains graph evaluation.
The native server's legacy session/fit endpoints and unused reuse-preview
endpoint are not mirrored solely to reproduce every HTTP route.

The HTTP example selector changes server-owned state and reloads the page.
Source import operations do not introduce a picker, work-loss dialog or source
authoring UI; those are a separate integration. The worker defaults to the retained
nozzle; `createWorkerBackend({initialMode:'empty'})` explicitly starts empty.
Download actions to preserve edits. Reload does not retain imported File references
or ephemeral work; this interface supplies no durable persistence.

## Native source transactions

The initial native Add Scan operation requires an empty project and accepts the
shared strict binary-triangle PLY profile with complete normals and unknown
units. It creates a Source alone. It does not replace an existing source, change
settings, load actions against a different source or add starter selections.
Reset requires the caller's explicit confirmed-discard intent; confirmation UI
belongs to the source-authoring integration.

The adapter reserves an operation with small JSON metadata, sends the original
File/Blob as a separate octet-stream body, then observes upload/preparation status.
It never encodes original bytes as JSON or exposes HTTP paths to its caller.
The browser supplies Content-Length for the file body. The server's
`metadata.capabilities.source_import` declares formats, one-source policy,
`limits`, cancellation and active-source retention. Client preflight uses its
advertised `limits.max_source_bytes` when available; server validation remains
authoritative. Upload size, count/incremental memory estimates and selected-fit
limits have different meanings and are not a total process/GPU memory guarantee.

Progress records contain `operationId`, the reservation's `generation`/`token`,
monotone operation `revision`, `status`, `phase`, `completed`, `total`, `unit`,
`terminal`, and optional `error:{code,message}`/`acceptedGeneration`. The first
`onProgress` callback runs before byte upload, allowing an explicit Cancel to
target its operation. Later callbacks receive detached records; late older upload
acknowledgements cannot overwrite newer progress. Counts use the supplied unit;
coarse phases are not solver or CAD percentages. Structured operation/server
errors retain their stable code and message.

Publication of source, graph and display occurs as one accepted server
generation. Metadata includes `project_generation` and a graph snapshot from
that generation; versioned mesh URLs and `X-Scansor-Generation` response headers
must agree. `loadWorkspace` returns only a complete matching bundle, including
zero-length buffers and an empty graph for an empty project. A failed read does
not update the cached cursor. Generic graph/authoring/CAD mutation requests carry
the coherently loaded generation in a private transport header. This fences
source reset/reimport even when identical saved definitions reproduce a token.
Starting a local Add Scan, reset or example selection also fences earlier graph,
metadata and binary replies. This request/publication fence is separate from
the accepted workspace generation, and from the worker's runtime generations.

Explicit import Cancel requests a safe-boundary stop and publication fence;
`cancelComputation:false` still describes native fitting/OCCT. AbortSignal and
`close()` stop waiting and abort outstanding HTTP observations/uploads without
claiming that preparation or computation was cancelled. They do not invoke the
explicit Cancel operation. There is no timeout-to-Force escalation or transport
fallback. A source operation that has already committed remains accepted if an
upload acknowledgement or later display delivery is lost. The adapter queries
the operation after a lost upload reply; a later workspace delivery failure uses
`accepted_workspace_unavailable` with its `acceptedOperation` record and cause.
Callers can query status and reacquire the current workspace; definitions are not
silently rolled back. The native workspace retains its immutable original bytes
for its active lifetime; durable persistence/reopening is not supplied here.

## Worker source transactions and recovery

Protocol 5 advertises `single-scan-candidate-transactions` and the same
`source_import` metadata shape as native. The pinned manifest supplies limits to
both Python validation and JS preflight; browser capacities require their own
actual-runtime measurements. The worker reads bounded File slices into an owned
MEMFS staging file. The main thread retains a File/Blob reference and never reads
the whole source for transport. Cloning a File reference is not a zero-copy claim.
Staging, Python streams, buffer views and proxies are released on terminal paths.
If staging cannot be released, `source_cleanup_failed` takes precedence over
Cancel, remains in operation history and retires the runtime. Import stays fenced
until explicit Force reconstructs the accepted checkpoint in a fresh worker.

One pending source operation owns its File independently of the 64-request quota.
Its slot remains occupied until cleanup/promotion, even if its observer aborts.
Status history holds at most 64 detached records, without source bytes or buffers.
Explicit source Cancel is separate from AbortSignal and from numerical SIGINT.
Without isolation, synchronous preparation reaches a safe boundary before posted
Cancel can be received; the host fences publication immediately and reports
`cancelling` until cleanup. Force remains explicit.

Preparation creates a private candidate project. Current source, graph, display
and cancellation checkpoint remain unchanged. The worker finishes validation and
staging cleanup, transfers the candidate's full metadata/graph/display bundle,
confirms detachment, then waits at a dedicated source boundary. The coordinator
validates request ownership, original cursor, candidate identity, logical source
generation and display sizes. In one synchronous handler it adopts the accepted
File, source mode/identity, recipe, generation and bundle before callbacks or
promise settlement. This is the public acceptance point. Its approval then
promotes the already-validated Python project pointer before queued work resumes.
Rejecting a candidate discards it; it does not roll back accepted state.

Cancel/Force before that adoption preserves the old accepted checkpoint. Cancel
after adoption reports accepted. If a worker crashes or loses its promotion reply
after adoption, the new File/recipe remains accepted and the observer receives
`accepted_workspace_unavailable`. Force reconstructs that new checkpoint. Aborting
only an observer likewise cannot suppress valid accepted source adoption or lose
its recovery File. Generic graph operations remain fenced while the source
boundary is pending. Accepted workspace generation is independent of request
generation, runtime lifetime and cancellation serial, including empty/same-byte
ABA transitions.

Force retains the accepted mode: empty, packaged example, or imported File/Blob.
It starts a fresh pinned runtime, rereads the retained source, verifies its exact
semantic identity and validates/replays the saved recipe on a private candidate
before publishing a recovered bundle. Logical source generation is preserved;
runtime/request fences advance. Same-source computation Cancel uses the shared
project graph-recovery transaction and preserves immutable geometry/display.
Failed, changed or unreadable File recovery leaves the accepted File/recipe
available for download/retry. A filename is a hint, and retaining a File is not a
guarantee that disk-backed bytes can always be reread. No fallback source or
transport is substituted.

## Revisions, progress, and readiness

The recipe `token`, source hashes, vertex IDs, graph `revision`, diagnostic
fields, and required-failure closure retain their current meanings. Backends
must preserve them. Transport request IDs do not replace source or graph
identity. HTTP assigns local request IDs to errors; a worker implementation must
correlate requests and worker generations before publishing replies.

`graphUpdates` always obtains at least one update, even if the supplied cursor
appears idle. HTTP internally polls using the latest full snapshot revision and
yields both full snapshots and compact unchanged responses. It yields terminal
status before ending. Poll intervals are adapter options, not UI concerns. A
worker can implement the same iterator with pushed messages.

`graph-evaluation.js` checks the token and caller's `isCurrent` predicate before
accepting results. Compact responses must match the accepted cursor and update
running/error status without rebuilding geometry or discarding UI drafts. An
absent error field clears a previous lifecycle error. A stream ending before
completion is an error, not permission to substitute cached ready geometry.

`ensureGraphCurrent` rechecks the caller's own targets after joined work
finishes, since another request's roots may differ. It checks required failures
and readiness; an available shape does not bypass boundary validation. Explicit
evaluation remains the retry path. Ensuring outputs does not silently retry
known failures.

## Ownership, errors, and cancellation

Bulk geometry and ZIP data are separate from JSON control messages. Display
buffers belong to the caller, which constructs typed arrays and GPU objects;
HTTP asset URLs and `Response` objects remain private to the adapter. Display
positions use the existing float32 encoding and triangle indices use uint32.
These are display derivatives, never replacements for authoritative float64
fitting inputs. A worker implementation must document transferred/detached
buffers and retain its fitting inputs independently. Shared memory is a later
data-transfer choice; no zero-copy claim is made here.

The worker reads contiguous float32/uint32 display derivatives with a Pyodide
buffer view, copies their exact bytes into JavaScript-owned buffers, releases
the views/proxies, and transfers those copies. A following worker acknowledgement
confirms the sender buffers are detached before `loadWorkspace` resolves. The
returned `transferOwnership` records detached status and copied byte counts.
Python source positions/weights remain immutable float64. Main-thread typed
arrays view the received buffers; Three.js still builds normals/colors and
uploads geometry to the GPU. This is not an end-to-end zero-copy path.

Worker requests are serialized. Evaluation replies acknowledge running state
before computation and push terminal snapshots afterward. The adapter retains
terminal state for late subscribers, fences old publications when mutations
are requested, and settles requests/observers on closure or runtime failure.
This stage reports coarse lifecycle snapshots, not progress within synchronous
solver calls. Abort stops waiting; explicit Cancel/Force follows the accepted
source checkpoint described above. A cached copy of the last accepted recipe can be
downloaded after failure, but it excludes unfinished UI drafts and solver caches.

With the CAD profile, the first face operation, CAD recipe import or export
verifies/extracts the pinned OCP wheel and checks its version before enabling
the Python adapter's CAD paths. Numerical startup does not import OCP. Missing
or incompatible CAD produces an operation error and leaves numerical work
available; its failed initialization remains failed until reload. The numerical
profile rejects CAD actions without changing the current recipe.

Face proposals and application reuse the native helpers and their recipe/geometry
tokens. Export preserves the native readiness and required-failure closure,
units, transforms, physical bounds and Body validation. ZIP bytes use a separate
Python buffer, copied and transferred like display data; the Python view/proxy
and retained export bytes are released before delivery. A failed or stale request
cannot deliver a previous export. STEP writing uses the existing virtual
temporary-directory path and restores its global units in `finally`. Native
shapes remain within replay scopes in the worker and never enter control JSON.

Recipe export clones the accepted recipe without a new server read, preserving
the existing Save actions behavior. Recipe import still validates against the
backend's source and current token. The UI owns download names, `Blob` creation,
object URLs, and their cleanup.

`BackendError` preserves user-visible diagnostics and includes a code plus HTTP
status/local request ID when available. Unsupported implementations and
capabilities fail explicitly. Network/decode failures propagate. No backend
substitutes cached geometry or another transport after failure.

Abort signals, superseded observation predicates, and `close` stop waiting and
prevent obsolete responses from being returned. They do not stop native Python
or OCCT computation. HTTP declares `cancelComputation: false`; no successful
computation-cancel operation is invented. The dedicated cancellation issues
must decide and implement that behavior.

## Verification

Run `npm run check` and `npm run build` in this directory, then the existing
`npm run test:browser` HTTP integration suite. `backend.test.js` verifies JSON
and binary behavior, observation lifecycle/abort, unsupported capabilities,
and readiness through an injected push backend with no HTTP calls.
`worker-backend.test.js` covers source candidate/adoption/promotion ordering,
explicit Cancel, observer Abort, capacity preflight, stale generations, empty
reset, accepted-but-unavailable delivery and source-aware Force/retry.
`graph-evaluation.test.js` retains graph-revision and required-root regression
coverage through the extracted HTTP adapter.
