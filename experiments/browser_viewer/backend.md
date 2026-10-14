# Browser backend boundary

This is an internal interface for the bounded viewer experiment. HTTP/native
Python remains the default implementation. The static experiment supplies a
worker backend for the retained nozzle's numerical graph. This interface does
not select a browser-only product architecture.

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

Each backend declares `capabilities`. HTTP currently supports numerical graph
operations, CAD, and retained examples. It reports snapshot progress and no
computation cancellation. `createBackend` rejects CAD-only and example operations
when their corresponding capabilities are unavailable. A reduced backend must
also diagnose unsupported graph action families during validation/evaluation;
the broad CAD flag is not a complete graph-operation support matrix.

## Operations

Methods return promises, except `graphUpdates` (an async iterator) and `close`.
Control payloads and graph snapshots retain the existing experiment schemas.
Optional operation options can carry an `AbortSignal`.

| Operation | Inputs and result |
| --- | --- |
| `loadWorkspace(options)` | Metadata plus display `positions` and `indices` buffers |
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
| `close()` | Stop this connection's requests/observations |

Selections remain recipe edits; authoritative fitting remains graph evaluation.
The native server's legacy session/fit endpoints and unused reuse-preview
endpoint are not mirrored solely to reproduce every HTTP route.

The current HTTP example selector changes server-owned state and reloads the
page. The worker exposes only the retained nozzle and restores its packaged
default on selection/reload. Download actions first to preserve edits; the worker
does not offer additional sources or persist ephemeral state across reload.

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
solver calls. Abort stops waiting; cancellation and worker restoration remain
the dedicated follow-up. A cached copy of the last accepted recipe can be
downloaded after failure, but it excludes unfinished UI drafts and solver caches.

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
`graph-evaluation.test.js` retains graph-revision and required-root regression
coverage through the extracted HTTP adapter.
