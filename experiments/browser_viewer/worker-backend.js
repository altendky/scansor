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

export function createWorkerBackend({ runtimeURL,
  workerFactory = (url, options) => new Worker(url, options),
  onPhase = () => {}, onFailure = () => {}, startupTimeout = 180_000 } = {}) {
  if (!runtimeURL) throw new BackendError('Browser runtime assets are not configured.',
    { code: 'missing_runtime' });
  const url = new URL(runtimeURL, import.meta.url), pending = new Map(), observers = new Set();
  let worker, initialization, timer, terminalError, closed = false, nextId = 1;
  let generation = 0, latestGraph, committedRecipe;
  const mutations = new Set(['replaceRecipe', 'importRecipe', 'selectExample', 'applyFeatureReuse']);
  const superseded = () => new BackendError('The requested operation changed. Try again.',
    { code: 'observation_superseded' });
  const errorFor = (error, requestId) => new BackendError(String(error?.message || error),
    { code: error?.code || 'worker_error', status: error?.status, requestId });
  const unavailable = () => terminalError || new BackendError('The backend is closed.',
    { code: 'backend_closed' });
  const wake = () => { for (const notify of observers) notify(); };
  const stop = error => {
    clearTimeout(timer);
    worker?.terminate();
    worker = undefined;
    for (const request of pending.values()) request.reject(error);
    pending.clear();
    wake();
  };
  const failure = error => {
    const initializing = pending.has(1);
    terminalError = new BackendError(String(error?.message || error),
      { code: initializing ? 'worker_startup_failed' : 'worker_failed' });
    stop(terminalError);
    if (!initializing) onFailure(terminalError);
  };
  const remember = state => {
    if (!state?.token) return;
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
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad: false, examples: true,
      progress: 'snapshots', cancelComputation: false, sources: 'retained-nozzle' }),
    initialize() {
      if (closed || terminalError) return Promise.reject(unavailable());
      initialization ??= new Promise((resolve, reject) => {
        pending.set(1, { resolve, reject });
        try {
          worker = workerFactory(url, { type: 'module' });
          worker.onmessage = ({ data }) => {
            if (closed || terminalError) return;
            try {
              if (data?.fatal) { failure(data.fatal); return; }
              if (data?.phase) { onPhase(data.phase); return; }
              if (data?.kind === 'graph') {
                if (data.generation === generation) remember(data.state);
                return;
              }
              const request = pending.get(data?.id);
              if (!request) return;
              if (data.id === 1) {
                if (!data.ok || !data.ready || !data.versions || !data.source_revision) {
                  failure(data.error || 'Invalid worker readiness response.'); return;
                }
                clearTimeout(timer);
                pending.delete(1);
                resolve(data);
                return;
              }
              if (data.kind === 'transfer-confirmed') {
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
              if (request.generation !== generation) {
                // A mutation may have committed before a newer request fenced
                // its publication. Preserve its recovery recipe, never its UI result.
                if (mutations.has(request.operation) && data.result?.recipe)
                  committedRecipe = structuredClone(data.result.recipe);
                pending.delete(data.id);
                request.reject(superseded());
                return;
              }
              if (request.operation === 'loadWorkspace') {
                if (!(data.result?.positions instanceof ArrayBuffer) || !(data.result?.indices instanceof ArrayBuffer))
                  throw new Error('Invalid workspace display buffers.');
                request.transfer = data.result;
                return;
              }
              remember(data.result);
              pending.delete(data.id);
              request.resolve(data.result);
            } catch (error) { failure(error); }
          };
          worker.onerror = event => {
            event.preventDefault?.();
            if (!closed && !terminalError) failure(event.message || 'Worker failed.');
          };
          worker.onmessageerror = () => {
            if (!closed && !terminalError) failure('Worker response could not be decoded.');
          };
          // Startup only: no timeout escalates cancellation of active computation.
          timer = setTimeout(() => failure('Browser runtime initialization timed out.'), startupTimeout);
          worker.postMessage({ id: 1, operation: 'initialize' });
        } catch (error) { failure(error); }
      });
      return initialization;
    },
    async exportRecipe(state) {
      const recipe = state?.recipe || committedRecipe;
      if (!recipe) throw new BackendError('Actions have not finished loading yet.');
      return structuredClone(recipe);
    },
    async *graphUpdates({ signal, isCurrent = () => true } = {}) {
      signal?.throwIfAborted();
      await abortable(backend.initialize(), signal);
      const observing = generation;
      if (!latestGraph) await backend.getGraph({ signal });
      let previous;
      const check = () => {
        signal?.throwIfAborted();
        if (closed || terminalError) throw unavailable();
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
          // Registration and inspection are synchronous, so a terminal event cannot be missed.
          if (closed || terminalError || generation !== observing || latestGraph !== previous) notify();
          else if (signal?.aborted) abort();
        });
      }
    },
    close() { closed = true; stop(unavailable()); },
  };
  for (const operation of ['loadWorkspace', 'getGraph', 'replaceRecipe', 'importRecipe',
    'evaluateGraph', 'ensureGraph', 'getExampleRecipe', 'listExamples', 'selectExample', 'applyFeatureReuse']) {
    backend[operation] = async (payload = {}, options = {}) => {
      const signal = options.signal || payload.signal;
      signal?.throwIfAborted();
      await abortable(backend.initialize(), signal);
      if (closed || terminalError) throw unavailable();
      signal?.throwIfAborted();
      if (mutations.has(operation)) { generation++; latestGraph = undefined; wake(); }
      const id = ++nextId, activeGeneration = generation;
      const value = { ...payload };
      delete value.signal;
      return new Promise((resolve, reject) => {
        const cleanup = () => signal?.removeEventListener('abort', abort);
        const abort = () => { pending.delete(id); cleanup(); reject(signal.reason); };
        pending.set(id, { operation, generation: activeGeneration,
          resolve: result => { cleanup(); resolve(result); },
          reject: error => { cleanup(); reject(error); } });
        signal?.addEventListener('abort', abort, { once: true });
        try { worker.postMessage({ id, operation, payload: value, generation: activeGeneration }); }
        catch (error) { pending.delete(id); cleanup(); reject(errorFor(error, id)); }
      });
    };
  }
  for (const operation of ['faceCandidates', 'previewFaces', 'applyFaces', 'exportCad'])
    backend[operation] = async () => { throw new BackendError('CAD is unavailable in this numerical browser slice.',
      { code: 'unsupported_capability' }); };
  return backend;
}
