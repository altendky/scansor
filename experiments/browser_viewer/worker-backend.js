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

export function createWorkerBackend({ runtimeURL, cad = false,
  workerFactory = (url, options) => new Worker(url, options),
  onPhase = () => {}, onFailure = () => {}, onLifecycle = () => {},
  startupTimeout = 180_000 } = {}) {
  if (!runtimeURL) throw new BackendError('Browser runtime assets are not configured.', { code: 'missing_runtime' });
  const url = new URL(runtimeURL, import.meta.url), pending = new Map(), observers = new Set();
  let worker, initialization, timer, terminalError, closed = false, nextId = 0;
  let generation = 0, lifetime = 0, cancellationSerial = 0, latestGraph, committedRecipe;
  let sourceIdentity, sourceRevision, interruptBuffer, cancellation, recovery, recovering = false;
  let lifecycle = Object.freeze({ state: 'startup', lifetime, generation, interrupts: false });
  const mutations = new Set(['replaceRecipe', 'importRecipe', 'selectExample', 'applyFeatureReuse', 'applyFaces']);
  const superseded = () => new BackendError('The requested operation changed. Try again.', { code: 'observation_superseded' });
  const cancelled = () => new BackendError('Browser computation was cancelled. Evaluate again when ready.',
    { code: 'computation_cancelled', status: 409 });
  const errorFor = (error, requestId) => new BackendError(String(error?.message || error),
    { code: error?.code || 'worker_error', status: error?.status, requestId });
  const unavailable = () => terminalError || new BackendError('The backend is closed.', { code: 'backend_closed' });
  const wake = () => { for (const notify of observers) notify(); };
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
    for (const request of pending.values()) request.reject(error);
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
    if (state.recipe) {
      latestGraph = state;
      committedRecipe = structuredClone(state.recipe);
    } else if (latestGraph?.token === state.token) {
      const { evaluation_error: oldError, ...previous } = latestGraph;
      void oldError;
      latestGraph = { ...previous, ...state };
    } else return;
    wake();
  };
  const send = (operation, payload = {}, signal) => {
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
        cleanup();
        reject(signal.reason);
      };
      pending.set(id, { operation, generation: activeGeneration,
        resolve: result => { cleanup(); resolve(result); },
        reject: error => { cleanup(); reject(error); } });
      signal?.addEventListener('abort', abort, { once: true });
      try { worker.postMessage({ id, operation, payload, generation: activeGeneration, managed: true }); }
      catch (error) { pending.delete(id); cleanup(); reject(errorFor(error, id)); }
    });
  };
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad, examples: true,
      progress: 'phases-and-snapshots', cancelComputation: true, recovery: true, sources: 'retained-nozzle', sourceImport: false }),
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
              if (request.aborted) { pending.delete(data.id); return; }
              if (request.operation === 'initialize') {
                if (!data.ok || !data.ready || !data.versions || !data.source_revision) {
                  failure(data.error || 'Invalid worker readiness response.'); return;
                }
                clearTimeout(timer);
                pending.delete(data.id);
                if (!recovering) {
                  sourceIdentity = structuredClone(data.source_identity);
                  sourceRevision = data.source_revision;
                  remember(data.graph);
                  publishLifecycle('complete');
                }
                request.resolve(data);
                return;
              }
              if (request.generation !== generation) {
                // Rapid edits may commit before a newer edit fences publication.
                // Cancel/Force remove pending requests, so cancelled edits never enter this cache.
                if (data.ok && mutations.has(request.operation) && data.result?.recipe)
                  committedRecipe = structuredClone(data.result.recipe);
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
              remember(data.result);
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
          owner.postMessage({ id, operation: 'initialize', ...(interruptBuffer ? { interruptBuffer } : {}) });
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
        if ((identity && JSON.stringify(identity) !== JSON.stringify(ready.source_identity)) ||
            (revision && revision !== ready.source_revision))
          throw new BackendError('The immutable example or runtime changed. Download actions before reloading.',
            { code: 'recovery_source_mismatch' });
        const fresh = ready.graph || await send('getGraph');
        const state = recipe ? await send('replaceRecipe', { token: fresh.token, recipe }) : fresh;
        if (serial !== cancellationSerial || closed) throw cancelled();
        sourceIdentity = structuredClone(ready.source_identity);
        sourceRevision = ready.source_revision;
        recovering = false;
        remember(state);
        publishLifecycle('complete', { recovered: true });
        return state;
      };
      recovery = current().catch(error => {
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
      if (mutations.has(operation)) { generation++; latestGraph = undefined; wake(); }
      const value = { ...payload };
      delete value.signal;
      return send(operation, value, signal);
    };
  }
  if (!cad) for (const operation of ['faceCandidates', 'previewFaces', 'applyFaces', 'exportCad'])
    backend[operation] = async () => { throw new BackendError('CAD is unavailable in this numerical browser slice.',
      { code: 'unsupported_capability' }); };
  return backend;
}
