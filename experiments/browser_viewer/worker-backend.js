import { BackendError } from './http-backend.js';

function abortable(promise, signal) {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const done = (finish, value) => { signal.removeEventListener('abort', abort); finish(value); };
    const abort = () => done(reject, signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    promise.then(value => done(resolve, value), error => done(reject, error));
  });
}

export function createWorkerBackend({ runtimeURL, cad = false, sourceActions = false,
  workerFactory = (url, options) => new Worker(url, options),
  onPhase = () => {}, onFailure = () => {}, onLifecycle = () => {},
  startupTimeout = 180_000, initialMode = 'example' } = {}) {
  if (!runtimeURL) throw new BackendError('Browser runtime assets are not configured.', { code: 'missing_runtime' });
  if (typeof sourceActions !== 'boolean')
    throw new BackendError('Invalid source actions capability.', { code: 'unsupported_capability' });
  if (!['empty', 'example'].includes(initialMode))
    throw new BackendError('Invalid initial workspace mode.', { code: 'invalid_source' });
  const url = new URL(runtimeURL, import.meta.url), pending = new Map(), observers = new Set();
  let worker, initialization, timer, terminalError, closed = false, nextId = 0;
  let generation = 0, lifetime = 0, cancellationSerial = 0, latestGraph, committedRecipe;
  let sourceIdentity, sourceRevision, interruptBuffer, cancellation, recovery, recovering = false;
  let acceptedGeneration, sourceProfile, replacementProfile, checkpoint, sourceOperation, sourceSequence = 0, recipePolling = false;
  let bootstrapMode = initialMode;
  const sourceHistory = new Map();
  let lifecycle = Object.freeze({ state: 'startup', lifetime, generation, interrupts: false });
  const mutations = new Set(['replaceRecipe', 'importRecipe', 'selectExample', 'applyFeatureReuse', 'applyFaces']);
  const superseded = () => new BackendError('The requested operation changed. Try again.', { code: 'observation_superseded' });
  const cancelled = () => new BackendError('Browser computation was cancelled. Evaluate again when ready.',
    { code: 'computation_cancelled', status: 409 });
  const errorFor = (error, requestId) => new BackendError(String(error?.message || error),
    { code: error?.code || 'worker_error', status: error?.status, requestId });
  const unavailable = () => terminalError || new BackendError('The backend is closed.', { code: 'backend_closed' });
  const wake = () => { for (const notify of observers) notify(); };
  const sourceBusy = () => new BackendError('A source operation is still active.', { code: 'source_busy', status: 409 });
  const actionsProfile = value => {
    const keys = ['max_json_bytes', 'max_request_bytes', 'max_volume_encoded_bytes', 'max_volume_expanded_bytes'];
    return value && Object.keys(value).length === 3 && value.supported === true && value.version === 2
      && value.limits && Object.keys(value.limits).length === keys.length
      && keys.every(key => Number.isSafeInteger(value.limits[key]) && value.limits[key] > 0)
      && value.limits.max_request_bytes >= value.limits.max_json_bytes
      && value.limits.max_volume_encoded_bytes <= value.limits.max_json_bytes;
  };
  const unsupportedActions = () => new BackendError('This worker does not support source actions.', { code: 'unsupported_capability' });
  const validReplacementProfile = profile => profile && Object.keys(profile).length === 4 &&
    profile.supported === true && profile.version === 1 && profile.alignment === 'already-aligned' &&
    profile.source_kind === 'generic-scan';
  const identityJSON = value => JSON.stringify(value, function(key, item) {
    return item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item;
  });
  const sourceRecord = operation => structuredClone(operation.record);
  const recordSource = (operation, changes) => {
    Object.assign(operation.record, changes, { revision: operation.record.revision + 1 });
    sourceHistory.set(operation.record.operationId, sourceRecord(operation));
    while (sourceHistory.size > 64) sourceHistory.delete(sourceHistory.keys().next().value);
  };
  const notifySource = operation => {
    if (operation.observerAborted) return;
    try { operation.onProgress(sourceRecord(operation)); }
    catch (error) { operation.observerAborted = true; operation.request?.reject(error); }
  };
  const retireSource = error => {
    if (!sourceOperation) return;
    const operation = sourceOperation;
    if (!operation.adopted) recordSource(operation, { status: 'cancelled', phase: 'cancelled', terminal: true,
      error: { code: error.code || 'computation_cancelled', message: error.message } });
    operation.finish();
    sourceOperation = undefined;
  };
  const publishLifecycle = (state, detail = {}) => {
    lifecycle = Object.freeze({ state, ...detail, lifetime, generation,
      canCancel: !!committedRecipe, interrupts: !!interruptBuffer });
    onLifecycle(lifecycle);
  };
  const stop = error => {
    clearTimeout(timer);
    const retired = worker;
    worker = undefined;
    lifetime++;
    retired?.terminate();
    retireSource(error);
    for (const request of pending.values()) request.reject(request.source?.adopted
      ? acceptedUnavailable(request.source, error) : error);
    pending.clear();
    wake();
  };
  const failure = error => {
    const initializing = [...pending.values()].some(request => request.operation === 'initialize');
    terminalError = new BackendError(String(error?.message || error),
      { code: initializing ? 'worker_startup_failed' : 'worker_failed' });
    stop(terminalError);
    publishLifecycle('failed', { message: terminalError.message });
    onFailure(terminalError);
  };
  const remember = state => {
    if (!state?.token || recovering) return;
    if (acceptedGeneration !== undefined && state.project_generation !== acceptedGeneration) return;
    if (state.recipe) {
      latestGraph = state;
      committedRecipe = structuredClone(state.recipe);
      if (checkpoint) checkpoint.recipe = structuredClone(state.recipe);
    } else if (latestGraph?.token === state.token) {
      const { evaluation_error: oldError, unchanged: oldUnchanged, status: oldStatus, ...previous } = latestGraph;
      void oldError;
      void oldUnchanged;
      void oldStatus;
      const { unchanged, ...publication } = state;
      void unchanged;
      latestGraph = { ...previous, ...publication };
    } else return;
    wake();
  };
  const send = (operation, payload = {}, signal, source) => {
    if (pending.size >= 64) return Promise.reject(new BackendError('Too many queued operations. Wait or cancel before retrying.',
      { code: 'worker_queue_full', status: 429 }));
    const id = ++nextId, activeGeneration = generation;
    return new Promise((resolve, reject) => {
      const cleanup = () => signal?.removeEventListener('abort', abort);
      const abort = () => {
        // Keep a tombstone until the reply to bound queued work even when its
        // caller stops observing. Aborting observation does not stop computation.
        const request = pending.get(id);
        if (request) request.aborted = true;
        if (source) source.observerAborted = true;
        cleanup();
        reject(signal.reason);
      };
      const request = { operation, generation: activeGeneration, source,
        resolve: result => { cleanup(); resolve(result); },
        reject: error => { cleanup(); reject(error); } };
      if (operation === 'inspect-actions') request.cursor = { generation: payload.generation, token: payload.token };
      pending.set(id, request);
      if (source) { source.request = request; source.requestId = id; }
      signal?.addEventListener('abort', abort, { once: true });
      try { worker.postMessage({ id, operation, payload, generation: activeGeneration, managed: true,
        ...(acceptedGeneration === undefined ? {} : { workspaceGeneration: acceptedGeneration }) }); }
      catch (error) { pending.delete(id); cleanup(); reject(errorFor(error, id)); }
    });
  };
  const sourceFailure = record => new BackendError(record.error?.message || 'The source operation was cancelled.',
    { code: record.error?.code || `source_${record.status}`, status: 409 });
  const acceptedUnavailable = (operation, cause) => {
    const error = new BackendError(`The project change was accepted, but its workspace is unavailable: ${cause.message}`,
      { code: 'accepted_workspace_unavailable', status: 409 });
    error.acceptedOperation = sourceRecord(operation);
    error.cause = cause;
    return error;
  };
  const validateBundle = (result, expectedGeneration) => {
    const metadata = result?.metadata, state = result?.graph;
    if (!Number.isSafeInteger(expectedGeneration) || expectedGeneration < 0
      || result.workspaceGeneration !== expectedGeneration || metadata?.project_generation !== expectedGeneration
      || state?.project_generation !== expectedGeneration || !state.token || !Array.isArray(state.recipe?.nodes)
      || !(result.positions instanceof ArrayBuffer) || !(result.indices instanceof ArrayBuffer)
      || !Number.isSafeInteger(metadata.vertices) || metadata.vertices < 0
      || !Number.isSafeInteger(metadata.display_triangles ?? metadata.triangles)
      || (metadata.display_triangles ?? metadata.triangles) < 0
      || result.positions.byteLength !== metadata.vertices * 12
      || result.indices.byteLength !== (metadata.display_triangles ?? metadata.triangles) * 12
      || metadata.positions?.dtype !== '<f4' || metadata.indices?.dtype !== '<u4'
      || metadata.positions.components !== 3 || metadata.indices.components !== 3)
      throw new BackendError('Invalid source workspace bundle.', { code: 'invalid_workspace' });
    return { ...result, generation: expectedGeneration };
  };
  const finishSource = (request, data, owner) => {
    const operation = request.source;
    if (sourceOperation !== operation) return;
    if (data.kind === 'source-candidate') {
      if (!data.ok || request.transfer) throw new Error('Invalid source candidate response.');
      request.transfer = data.result;
      return;
    }
    if (data.kind === 'transfer-confirmed') {
      if (!request.transfer || data.positions_bytes_after !== 0 || data.indices_bytes_after !== 0)
        throw new Error('Source workspace display buffers were not transferred.');
      const permitted = !operation.cancelled && request.generation === generation
        && operation.baseGeneration === acceptedGeneration && operation.baseToken === latestGraph?.token;
      if (!permitted) {
        owner.postMessage({ operation: 'accept-source-boundary', id: data.id, generation: request.generation,
          operationId: operation.record.operationId, workspaceGeneration: request.transfer.workspaceGeneration,
          accepted: false });
        return;
      }
      const expected = operation.recoveryGeneration ?? operation.baseGeneration + 1;
      const bundle = validateBundle(request.transfer, expected);
      if (bundle.operationId !== operation.record.operationId)
        throw new Error('Source candidate identity changed.');
      if (sourceActions && (!Array.isArray(bundle.metadata.import_warnings)
        || operation.recoveryGeneration !== undefined && (
          identityJSON(bundle.source_identity) !== identityJSON(operation.recoveryIdentity)
          || identityJSON(bundle.metadata.import_warnings) !== identityJSON(operation.recoveryWarnings))))
        throw new Error('Source recovery metadata differs from the accepted checkpoint.');
      let acceptedFile = operation.file;
      if (operation.purpose === 'open-actions') {
        const kind = { generic: 'scan', legacy: 'example', empty: 'empty' }[bundle.sourceKind];
        if (bundle.purpose !== 'open-actions' || !kind || bundle.source_mode?.kind !== kind
          || typeof bundle.uploadRequired !== 'boolean' || bundle.uploadRequired !== (operation.file !== undefined)
          || bundle.uploadRequired && kind !== 'scan'
          || !Array.isArray(bundle.metadata.import_warnings)
          || !Number.isSafeInteger(bundle.import_warning_count)
          || bundle.import_warning_count !== bundle.metadata.import_warnings.length)
          throw new Error('Invalid actions candidate publication.');
        if (kind === 'scan' && !bundle.uploadRequired) {
          if (!operation.retainedFile || identityJSON(bundle.source_identity) !== identityJSON(operation.baseIdentity))
            throw new Error('Matching actions lost their accepted source authority.');
          acceptedFile = operation.retainedFile;
        } else if (kind !== 'scan') {
          if (kind === 'example' && (operation.baseMode?.kind !== 'example'
            || identityJSON(bundle.source_identity) !== identityJSON(operation.baseIdentity)))
            throw new Error('Legacy actions do not match the explicitly accepted example.');
          acceptedFile = undefined;
        }
      } else if (bundle.source_mode?.kind !== operation.kind) throw new Error('Source candidate kind changed.');
      if (operation.purpose === 'replace-scan' && bundle.purpose !== 'replace-scan')
        throw new Error('Replacement candidate purpose changed.');
      const importWarnings = structuredClone(bundle.metadata.import_warnings || []);
      bundle.transferOwnership = { sender_detached: true, positions_bytes: bundle.positions.byteLength,
        indices_bytes: bundle.indices.byteLength, copied_js_bytes: bundle.positions.byteLength + bundle.indices.byteLength,
        strategy: 'copy-contiguous-wasm-view-then-transfer' };
      // The complete confirmed bundle is the source transaction's linearization
      // point. Record recovery authority before callbacks or boundary delivery.
      operation.adopted = true;
      operation.bundle = bundle;
      acceptedGeneration = expected;
      sourceIdentity = structuredClone(bundle.source_identity);
      committedRecipe = structuredClone(bundle.graph.recipe);
      checkpoint = { mode: structuredClone(bundle.source_mode), identity: sourceIdentity,
        generation: expected, file: acceptedFile, recipe: committedRecipe, revision: sourceRevision,
        import_warnings: importWarnings };
      latestGraph = bundle.graph;
      recordSource(operation, { status: 'accepted', phase: 'complete', terminal: true, acceptedGeneration: expected,
        ...(operation.purpose === 'open-actions' ? { uploadRequired: bundle.uploadRequired,
          import_warning_count: importWarnings.length } : {}) });
      owner.postMessage({ operation: 'accept-source-boundary', id: data.id, generation: request.generation,
        operationId: operation.record.operationId, workspaceGeneration: expected, accepted: true });
      wake();
      notifySource(operation);
      return;
    }
    if (data.kind === 'source-promoted') {
      if (!operation.adopted || data.workspaceGeneration !== acceptedGeneration
        || data.operationId !== operation.record.operationId) throw new Error('Invalid source promotion acknowledgement.');
      pending.delete(data.id);
      sourceOperation = undefined;
      operation.finish();
      request.resolve(operation.bundle);
      return;
    }
    if (!data.ok) {
      const cause = errorFor(data.error, data.id);
      pending.delete(data.id);
      sourceOperation = undefined;
      operation.finish();
      if (operation.adopted) {
        request.reject(acceptedUnavailable(operation, cause));
        failure(cause);
      } else {
        const wasCancelled = operation.cancelled && cause.code !== 'source_cleanup_failed';
        recordSource(operation, { status: wasCancelled ? 'cancelled' : 'failed', phase: 'complete', terminal: true,
          error: { code: wasCancelled ? 'source_cancelled' : cause.code, message: cause.message } });
        notifySource(operation);
        request.reject(sourceFailure(operation.record));
      }
    }
  };
  const startSource = async (kind, payload, { signal, onProgress = () => {} } = {}, restore) => {
    signal?.throwIfAborted();
    await abortable(restore ? backend.initialize() : recovery || cancellation || backend.initialize(), signal);
    signal?.throwIfAborted();
    if (closed || terminalError) throw unavailable();
    if (!sourceProfile?.supported) throw new BackendError('This worker does not support scan import.', { code: 'unsupported_capability' });
    const opening = payload.purpose === 'open-actions';
    const replacing = payload.purpose === 'replace-scan';
    if (opening && !sourceActions) throw unsupportedActions();
    if (replacing && (!validReplacementProfile(replacementProfile) || checkpoint?.mode.kind !== 'scan'))
      throw new BackendError('This worker requires an imported aligned scan for replacement.', { code: 'unsupported_capability' });
    if (sourceOperation) throw sourceBusy();
    if (payload.generation !== acceptedGeneration || payload.token !== latestGraph?.token) throw superseded();
    if (latestGraph.evaluation_running || pending.size) throw sourceBusy();
    const file = payload.file;
    if (kind === 'scan' || opening && file !== undefined) {
      if (!file || typeof file.slice !== 'function' || typeof file.arrayBuffer !== 'function'
        || !Number.isSafeInteger(file.size) || file.size <= 0)
        throw new BackendError('Choose a nonempty scan file.', { code: 'invalid_source' });
      if (file.size > sourceProfile.limits.max_source_bytes)
        throw new BackendError('Scan exceeds the declared source byte limit.', { code: 'source_limit' });
    }
    const operationId = `source_${lifetime}_${++sourceSequence}`;
    const operation = { kind, purpose: opening ? 'open-actions' : replacing ? 'replace-scan' : 'source', file, onProgress,
      retainedFile: opening ? checkpoint?.file : undefined, baseIdentity: structuredClone(sourceIdentity),
      baseMode: opening ? structuredClone(checkpoint?.mode) : undefined,
      baseGeneration: acceptedGeneration, baseToken: latestGraph.token,
      recoveryGeneration: restore?.generation, record: { operationId, generation: payload.generation,
        token: payload.token, revision: 0, status: 'reserved', phase: 'reserved', completed: 0,
        total: file?.size || 0, unit: 'bytes', terminal: false,
        ...(opening || replacing ? { purpose: opening ? 'open-actions' : 'replace-scan' } : {}) } };
    if (restore) {
      operation.recoveryIdentity = structuredClone(restore.identity);
      operation.recoveryWarnings = structuredClone(restore.import_warnings || []);
    }
    operation.completion = new Promise(resolve => { operation.finish = resolve; });
    sourceOperation = operation;
    generation++;
    wake();
    const value = { operationId, ...(opening ? { purpose: 'open-actions', recipe: payload.recipe,
      confirmedDiscard: payload.confirmedDiscard,
      ...(file === undefined ? {} : { file, source: { filename: file.name || 'scan.ply', size: file.size } }) } : { kind }),
      ...(replacing ? { purpose: 'replace-scan' } : {}),
      generation: payload.generation, token: payload.token,
      ...(kind === 'scan' ? { file, filename: file.name || 'scan.ply' } : {}),
      ...(kind === 'empty' ? { confirmedDiscard: payload.confirmedDiscard } : {}),
      ...(kind === 'example' ? { example: payload.example } : {}),
      ...(restore ? { recovery: { generation: restore.generation, identity: restore.identity, recipe: restore.recipe,
        import_warnings: structuredClone(restore.import_warnings || []) } } : {}) };
    const promise = send('prepare-source', value, signal, operation);
    recordSource(operation, {});
    notifySource(operation);
    return promise.catch(error => {
      if (sourceOperation === operation && !pending.has(operation.requestId)) {
        recordSource(operation, { status: 'failed', phase: 'complete', terminal: true,
          error: { code: error.code || 'transport_error', message: error.message } });
        sourceOperation = undefined;
        operation.finish();
      }
      throw error;
    });
  };
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad, examples: true,
      progress: 'phases-and-snapshots', cancelComputation: true, recovery: true,
      sources: 'single-scan-and-retained-examples', sourceImport: true, sourceActions, sourceReplacement: true }),
    getLifecycle: () => lifecycle,
    initialize() {
      if (closed || terminalError) return Promise.reject(unavailable());
      initialization ??= new Promise((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, { operation: 'initialize', resolve, reject });
        try {
          const owner = workerFactory(url, { type: 'module' }), activeLifetime = ++lifetime;
          worker = owner;
          interruptBuffer = undefined;
          if (globalThis.crossOriginIsolated && typeof SharedArrayBuffer === 'function') {
            try { interruptBuffer = new Uint8Array(new SharedArrayBuffer(2)); } catch { /* Optional. */ }
          }
          publishLifecycle(recovering ? 'recovering' : 'startup');
          owner.onmessage = ({ data }) => {
            if (closed || terminalError || worker !== owner || lifetime !== activeLifetime) return;
            try {
              if (data?.fatal) { failure(data.fatal); return; }
              if (data?.phase) {
                onPhase(data.phase);
                if (!cancellation) publishLifecycle(recovering ? 'recovering' : 'loading', { phase: data.phase });
                return;
              }
              if (data?.kind === 'source-progress') {
                const operation = sourceOperation;
                if (operation && !operation.adopted && !operation.cancelled
                  && data.operationId === operation.record.operationId && data.generation === generation) {
                  const progress = data.progress;
                  if (!progress || typeof progress.phase !== 'string' || !Number.isFinite(progress.completed)
                    || !Number.isFinite(progress.total) || progress.completed < 0 || progress.total < progress.completed
                    || typeof progress.unit !== 'string') throw new Error('Invalid source progress.');
                  recordSource(operation, { status: progress.phase === 'reading' ? 'receiving' : 'preparing', ...progress });
                  notifySource(operation);
                }
                return;
              }
              if (data?.kind === 'lifecycle') {
                if (data.boundary) owner.postMessage({ operation: 'accept-boundary', id: data.id,
                  generation: data.generation, accepted: data.generation === generation && !cancellation });
                if (data.operation !== 'cancel' && data.generation === generation && !cancellation)
                  publishLifecycle(recovering ? 'recovering' : data.state,
                    { operation: data.operation, requestId: data.id, interruptible: data.interruptible });
                return;
              }
              if (data?.kind === 'graph') {
                if (data.generation === generation) remember(data.state);
                return;
              }
              const request = pending.get(data?.id);
              if (!request) return;
              if (request.source) { finishSource(request, data, owner); return; }
              if (request.aborted) { pending.delete(data.id); return; }
              if (request.operation === 'initialize') {
                if (!data.ok || !data.ready || !data.versions || !data.source_revision) {
                  failure(data.error || 'Invalid worker readiness response.'); return;
                }
                clearTimeout(timer);
                sourceProfile = data.source_import;
                replacementProfile = data.worker_protocol?.replace_scan;
                if (data.worker_protocol?.version === 9 && !validReplacementProfile(replacementProfile))
                  throw new Error('Invalid worker scan replacement profile.');
                if (data.worker_protocol?.version !== 9 && replacementProfile !== undefined)
                  throw new Error('Unexpected worker scan replacement profile.');
                recipePolling = data.worker_protocol?.version >= 7;
                if ([8, 9].includes(data.worker_protocol?.version) &&
                  (!data.worker_protocol.selection_entries ||
                    Object.keys(data.worker_protocol.selection_entries).length !== 1 ||
                    data.worker_protocol.selection_entries.version !== 1))
                  throw new Error('Invalid worker selection entries profile.');
                if (sourceProfile?.supported && (![5, 6, 7, 8, 9].includes(data.worker_protocol?.version)
                  || !['max_source_bytes', 'max_vertices', 'max_faces', 'max_estimated_bytes', 'chunk_bytes']
                    .every(key => Number.isSafeInteger(sourceProfile.limits?.[key]) && sourceProfile.limits[key] > 0)))
                  throw new Error('Invalid worker source profile.');
                if (sourceActions && (![7, 8, 9].includes(data.worker_protocol?.version)
                  || !actionsProfile(data.worker_protocol?.source_actions) || !actionsProfile(data.source_actions)
                  || identityJSON(data.worker_protocol.source_actions) !== identityJSON(data.source_actions)
                  || !sourceProfile?.supported || !Number.isSafeInteger(data.graph?.project_generation)
                  || data.graph.project_generation < 0 || !data.graph.token || !Array.isArray(data.graph.recipe?.nodes)))
                  throw new Error('Invalid worker source actions profile.');
                if (!recovering) {
                  sourceIdentity = structuredClone(data.source_identity);
                  sourceRevision = data.source_revision;
                  acceptedGeneration = data.graph?.project_generation;
                  if (acceptedGeneration !== undefined) checkpoint = {
                    mode: structuredClone(data.source_mode), identity: structuredClone(data.source_identity),
                    generation: acceptedGeneration, recipe: structuredClone(data.graph.recipe), revision: sourceRevision,
                    import_warnings: [] };
                  remember(data.graph);
                  publishLifecycle('complete');
                }
                pending.delete(data.id);
                request.resolve(data);
                return;
              }
              if (request.generation !== generation) {
                // Rapid edits may commit before a newer edit fences publication.
                // Cancel/Force remove pending requests, so cancelled edits never enter this cache.
                if (data.ok && mutations.has(request.operation) && data.result?.recipe)
                  if (acceptedGeneration === undefined || data.result.project_generation === acceptedGeneration) {
                    committedRecipe = structuredClone(data.result.recipe);
                    if (checkpoint) checkpoint.recipe = structuredClone(data.result.recipe);
                  }
                pending.delete(data.id);
                request.reject(superseded());
                return;
              }
              if (data.kind === 'transfer-confirmed') {
                if (request.operation === 'exportCad') {
                  if (!(request.transfer instanceof ArrayBuffer) || data.export_bytes_after !== 0)
                    throw new Error('CAD export bytes were not transferred.');
                  pending.delete(data.id);
                  request.resolve(request.transfer);
                  return;
                }
                if (!request.transfer || data.positions_bytes_after !== 0 || data.indices_bytes_after !== 0)
                  throw new Error('Workspace display buffers were not transferred.');
                const result = request.transfer;
                if (acceptedGeneration !== undefined) {
                  if (result.generation !== acceptedGeneration || result.metadata?.project_generation !== acceptedGeneration
                    || result.graph?.project_generation !== acceptedGeneration) throw superseded();
                  remember(result.graph);
                }
                result.transferOwnership = { sender_detached: true,
                  positions_bytes: result.positions.byteLength, indices_bytes: result.indices.byteLength,
                  copied_js_bytes: result.positions.byteLength + result.indices.byteLength,
                  strategy: 'copy-contiguous-wasm-view-then-transfer' };
                pending.delete(data.id);
                request.resolve(result);
                return;
              }
              if (!data.ok) {
                pending.delete(data.id);
                request.reject(errorFor(data.error, data.id));
                return;
              }
              if (request.operation === 'loadWorkspace' || request.operation === 'exportCad') {
                if (request.operation === 'loadWorkspace'
                  ? !(data.result?.positions instanceof ArrayBuffer) || !(data.result?.indices instanceof ArrayBuffer)
                  : !(data.result instanceof ArrayBuffer)) throw new Error('Invalid transferred buffers.');
                request.transfer = data.result;
                return;
              }
              if (request.operation === 'inspect-actions') {
                const result = data.result;
                if (result?.generation !== request.cursor.generation || result?.token !== request.cursor.token
                  || !['matching', 'locate', 'empty'].includes(result?.classification)
                  || result.uploadRequired !== (result.classification === 'locate')
                  || !result.recipe || !Array.isArray(result.recipe.nodes) || !Array.isArray(result.import_warnings)
                  || result.classification === 'locate' && !result.descriptor)
                  throw new Error('Invalid actions inspection response.');
              } else remember(data.result);
              pending.delete(data.id);
              request.resolve(data.result);
            } catch (error) { failure(error); }
          };
          owner.onerror = event => {
            event.preventDefault?.();
            if (worker === owner && lifetime === activeLifetime && !closed && !terminalError)
              failure(event.message || 'Worker failed.');
          };
          owner.onmessageerror = () => {
            if (worker === owner && lifetime === activeLifetime && !closed && !terminalError)
              failure('Worker response could not be decoded.');
          };
          // Startup only: active computation never escalates automatically.
          timer = setTimeout(() => failure('Browser runtime initialization timed out.'), startupTimeout);
          owner.postMessage({ id, operation: 'initialize', ...(bootstrapMode === 'empty' ? { payload: { initialMode: 'empty' } } : {}),
            ...(interruptBuffer ? { interruptBuffer } : {}) });
        } catch (error) { failure(error); }
      });
      return initialization;
    },
    async exportRecipe(state) {
      const recipe = state?.recipe || committedRecipe;
      if (!recipe) throw new BackendError('Actions have not finished loading yet.');
      return structuredClone(recipe);
    },
    cancelComputation() {
      if (closed || terminalError) return Promise.reject(unavailable());
      if (cancellation) return cancellation;
      if (recovery) return Promise.reject(new BackendError('The runtime is restarting.', { code: 'runtime_recovering' }));
      if (sourceOperation) {
        const operation = sourceOperation;
        const cancelSource = operation.adopted ? Promise.resolve() : backend.cancelSourceOperation(operation.record);
        return cancelSource.then(() => operation.completion).catch(error => {
          if (error.code !== 'source_cancelled') throw error;
        }).then(() => latestGraph);
      }
      if (!pending.size && !latestGraph?.evaluation_running)
        return Promise.resolve(latestGraph);
      if (!committedRecipe) return Promise.reject(new BackendError('Startup cannot be interrupted politely. Use Force and restart.',
        { code: 'startup_in_progress' }));
      const interrupt = lifecycle.interruptible && interruptBuffer;
      generation++;
      cancellationSerial++;
      for (const request of pending.values()) request.reject(cancelled());
      pending.clear();
      latestGraph = undefined;
      wake();
      publishLifecycle('cancel-requested', { message: 'Waiting for a safe boundary. Force and restart is available.' });
      if (interrupt) {
        Atomics.store(interruptBuffer, 1, 1);
        Atomics.store(interruptBuffer, 0, 2);
      }
      const serial = cancellationSerial;
      cancellation = send('cancel', { recipe: structuredClone(committedRecipe) }).then(state => {
        if (serial !== cancellationSerial) throw cancelled();
        remember(state);
        publishLifecycle('cancelled');
        return state;
      }).catch(error => {
        if (serial === cancellationSerial && !closed) failure(error);
        throw error;
      }).finally(() => { if (serial === cancellationSerial) cancellation = undefined; });
      return cancellation;
    },
    forceRestart() {
      if (closed) return Promise.reject(unavailable());
      const recipe = committedRecipe && structuredClone(committedRecipe);
      const identity = sourceIdentity, revision = sourceRevision;
      const retained = checkpoint && { ...checkpoint, recipe: structuredClone(checkpoint.recipe),
        import_warnings: structuredClone(checkpoint.import_warnings || []) };
      if (retained) bootstrapMode = 'empty';
      generation++;
      const serial = ++cancellationSerial;
      stop(cancelled());
      cancellation = undefined;
      initialization = undefined;
      terminalError = undefined;
      latestGraph = undefined;
      recovering = true;
      publishLifecycle('recovering');
      const current = async () => {
        const ready = await backend.initialize();
        if (serial !== cancellationSerial || closed) throw cancelled();
        if (retained && sourceProfile?.supported) {
          if (revision && revision !== ready.source_revision)
            throw new BackendError('The pinned runtime changed. Download actions before reloading.', { code: 'recovery_source_mismatch' });
          acceptedGeneration = ready.graph.project_generation;
          latestGraph = ready.graph;
          sourceRevision = ready.source_revision;
          const bundle = await startSource(retained.mode.kind, { generation: acceptedGeneration, token: latestGraph.token,
            file: retained.file, example: retained.mode.example, confirmedDiscard: true }, {}, retained);
          if (serial !== cancellationSerial || closed) throw cancelled();
          recovering = false;
          remember(bundle.graph);
          publishLifecycle('complete', { recovered: true });
          return bundle.graph;
        }
        if ((identity && JSON.stringify(identity) !== JSON.stringify(ready.source_identity)) ||
            (revision && revision !== ready.source_revision))
          throw new BackendError('The immutable example or runtime changed. Download actions before reloading.',
            { code: 'recovery_source_mismatch' });
        const fresh = ready.graph || await send('getGraph');
        const state = recipe ? await send('replaceRecipe', { token: fresh.token, recipe }) : fresh;
        if (serial !== cancellationSerial || closed) throw cancelled();
        sourceIdentity = structuredClone(ready.source_identity);
        sourceRevision = ready.source_revision;
        if (ready.graph?.project_generation !== undefined) {
          acceptedGeneration = ready.graph.project_generation;
          checkpoint = { mode: structuredClone(ready.source_mode), identity: sourceIdentity,
            generation: acceptedGeneration, recipe: structuredClone(state.recipe), revision: sourceRevision };
        }
        recovering = false;
        remember(state);
        publishLifecycle('complete', { recovered: true });
        return state;
      };
      recovery = current().catch(error => {
        if (retained && serial === cancellationSerial) {
          checkpoint = retained;
          acceptedGeneration = retained.generation;
          committedRecipe = structuredClone(retained.recipe);
          sourceIdentity = structuredClone(retained.identity);
        }
        if (serial === cancellationSerial && !closed) failure(error);
        throw error;
      }).finally(() => { if (serial === cancellationSerial) { recovery = undefined; recovering = false; } });
      return recovery;
    },
    async *graphUpdates({ signal, isCurrent = () => true } = {}) {
      signal?.throwIfAborted();
      const serial = cancellationSerial;
      await abortable(recovery || cancellation || backend.initialize(), signal);
      if (serial !== cancellationSerial) throw cancelled();
      const observing = generation;
      if (!latestGraph) await backend.getGraph({ signal });
      let previous;
      const check = () => {
        signal?.throwIfAborted();
        if (closed || terminalError) throw unavailable();
        if (serial !== cancellationSerial) throw cancelled();
        if (generation !== observing || !isCurrent()) throw superseded();
      };
      while (true) {
        check();
        if (latestGraph && latestGraph !== previous) {
          previous = latestGraph;
          yield previous;
          if (!previous.evaluation_running) return;
          continue;
        }
        await new Promise((resolve, reject) => {
          const notify = () => { cleanup(); resolve(); };
          const abort = () => { cleanup(); reject(signal.reason); };
          const cleanup = () => { observers.delete(notify); signal?.removeEventListener('abort', abort); };
          observers.add(notify);
          signal?.addEventListener('abort', abort, { once: true });
          if (closed || terminalError || generation !== observing || latestGraph !== previous) notify();
          else if (signal?.aborted) abort();
        });
      }
    },
    close() { closed = true; cancellationSerial++; stop(unavailable()); publishLifecycle('closed'); },
  };
  for (const operation of ['loadWorkspace', 'getGraph', 'replaceRecipe', 'importRecipe',
    'evaluateGraph', 'ensureGraph', 'getExampleRecipe', 'listExamples', 'selectExample', 'applyFeatureReuse',
    'faceCandidates', 'previewFaces', 'applyFaces', 'exportCad']) {
    backend[operation] = async (payload = {}, options = {}) => {
      const signal = options.signal || payload.signal, serial = cancellationSerial;
      signal?.throwIfAborted();
      await abortable(recovery || cancellation || backend.initialize(), signal);
      if (closed || terminalError) throw unavailable();
      if (serial !== cancellationSerial) throw cancelled();
      signal?.throwIfAborted();
      if (sourceOperation) throw sourceBusy();
      if (operation === 'loadWorkspace' && acceptedGeneration !== undefined
        && (payload.generation !== undefined && payload.generation !== acceptedGeneration
          || payload.token !== undefined && payload.token !== latestGraph?.token)) throw superseded();
      if (mutations.has(operation)) { generation++; latestGraph = undefined; wake(); }
      const value = { ...payload };
      delete value.signal;
      if (operation === 'loadWorkspace') { delete value.generation; delete value.token; }
      if (recipePolling && operation === 'getGraph' && Number.isSafeInteger(value.revision) && value.revision >= 0
        && latestGraph?.recipe) value.recipe_token = latestGraph.token;
      const result = await send(operation, value, signal);
      if (result?.states && !result.recipe && !result.unchanged) {
        if (!latestGraph?.recipe || latestGraph.token !== result.token || latestGraph.revision !== result.revision)
          throw new BackendError('Invalid recipe-less graph response. Reload the current actions.');
        return latestGraph;
      }
      return result;
    };
  }
  backend.addScan = (payload, options) => startSource('scan', payload, options);
  backend.replaceScan = (payload, options) => startSource('scan', { ...payload, purpose: 'replace-scan' }, options);
  backend.inspectActions = async ({ recipe, generation: cursor, token }, { signal } = {}) => {
    if (!sourceActions) throw unsupportedActions();
    const raw = structuredClone(recipe), serial = cancellationSerial;
    signal?.throwIfAborted();
    await abortable(recovery || cancellation || backend.initialize(), signal);
    if (closed || terminalError) throw unavailable();
    if (serial !== cancellationSerial) throw cancelled();
    if (sourceOperation) throw sourceBusy();
    if (cursor !== acceptedGeneration || token !== latestGraph?.token) throw superseded();
    return send('inspect-actions', { recipe: raw, generation: cursor, token }, signal);
  };
  backend.openActions = async ({ recipe, file, generation: cursor, token, confirmedDiscard }, options) => {
    if (!sourceActions) throw unsupportedActions();
    const raw = structuredClone(recipe);
    if (confirmedDiscard !== true) throw new BackendError('Confirm discarding the current project.',
      { code: 'confirmation_required', status: 422 });
    return startSource(undefined, { purpose: 'open-actions', recipe: raw, file,
      generation: cursor, token, confirmedDiscard }, options);
  };
  backend.resetProject = (payload, options) => {
    if (payload?.confirmedDiscard !== true) return Promise.reject(new BackendError('Confirm discarding the current project.',
      { code: 'confirmation_required', status: 422 }));
    return startSource('empty', payload, options);
  };
  backend.getSourceOperation = async ({ operationId }, { signal } = {}) => {
    signal?.throwIfAborted();
    if (closed) throw unavailable();
    const record = sourceHistory.get(operationId);
    if (!record) {
      await abortable(backend.initialize(), signal);
      if (!sourceProfile?.supported)
        throw new BackendError('This worker does not support scan import.', { code: 'unsupported_capability' });
      throw new BackendError('Unknown source operation.', { code: 'unknown_operation', status: 404 });
    }
    return structuredClone(record);
  };
  backend.cancelSourceOperation = async ({ operationId, generation: cursor, token }, { signal } = {}) => {
    const record = await backend.getSourceOperation({ operationId }, { signal });
    if (record.generation !== cursor || record.token !== token)
      throw new BackendError('The source cursor changed.', { code: 'stale_source', status: 409 });
    if (record.terminal) return record;
    const operation = sourceOperation;
    if (!operation || operation.record.operationId !== operationId)
      throw new BackendError('Unknown source operation.', { code: 'unknown_operation', status: 404 });
    if (operation.record.terminal) return sourceRecord(operation);
    if (!operation.cancelled) {
      operation.cancelled = true;
      recordSource(operation, { status: 'cancelling', phase: 'cancelling' });
      worker.postMessage({ operation: 'cancel-source', operationId, generation });
      notifySource(operation);
    }
    return sourceRecord(operation);
  };
  const selectLegacyExample = backend.selectExample;
  backend.selectExample = async (payload, options = {}) => {
    await abortable(recovery || cancellation || backend.initialize(), options.signal);
    if (!sourceProfile?.supported) return selectLegacyExample(payload, options);
    const bundle = await startSource('example', { ...payload, generation: payload.generation ?? acceptedGeneration }, options);
    return { active: bundle.source_mode.example, project_generation: bundle.generation, graph: bundle.graph };
  };
  if (!cad) for (const operation of ['faceCandidates', 'previewFaces', 'applyFaces', 'exportCad'])
    backend[operation] = async () => { throw new BackendError('CAD is unavailable in this numerical browser slice.',
      { code: 'unsupported_capability' }); };
  return backend;
}
