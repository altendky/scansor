import { BackendError } from './http-backend.js';

// Bootstrap only. Workspace operations are enabled by the next integration slice.
export function createWorkerBackend({ runtimeURL,
  workerFactory = (url, options) => new Worker(url, options),
  onPhase = () => {}, onFailure = () => {}, startupTimeout = 180_000 } = {}) {
  if (!runtimeURL) throw new BackendError('Browser runtime assets are not configured.',
    { code: 'missing_runtime' });
  const url = new URL(runtimeURL, import.meta.url);
  let worker, initialization, pending, timer, terminalError, closed = false;
  const stop = error => {
    clearTimeout(timer);
    worker?.terminate();
    worker = undefined;
    pending?.reject(error);
    pending = undefined;
  };
  const failure = error => {
    terminalError = new BackendError(String(error?.message || error),
      { code: 'worker_startup_failed' });
    const initializing = !!pending;
    stop(terminalError);
    if (!initializing) onFailure(terminalError);
  };
  const unavailable = () => new BackendError(closed ? 'The backend is closed.'
    : 'Browser workspace operations are not available in this bootstrap slice.',
    { code: closed ? 'backend_closed' : 'unsupported_capability' });
  const backend = {
    capabilities: Object.freeze({ numerical: false, cad: false, examples: false,
      progress: 'startup-phases', cancelComputation: false, sources: false }),
    initialize() {
      if (closed) return Promise.reject(unavailable());
      if (terminalError) return Promise.reject(terminalError);
      initialization ??= new Promise((resolve, reject) => {
        pending = { resolve, reject };
        try {
          worker = workerFactory(url, { type: 'module' });
          worker.onmessage = ({ data }) => {
            if (closed || terminalError) return;
            try {
              if (data?.fatal) { failure(data.fatal); return; }
              if (data?.phase) { onPhase(data.phase); return; }
              if (data?.id !== 1 || !pending) return;
              if (!data.ok) { failure(data.error || 'Worker initialization failed.'); return; }
              if (!data.ready || !data.versions || !data.source_revision) {
                failure('Invalid worker readiness response.'); return;
              }
              clearTimeout(timer);
              pending.resolve(data);
              pending = undefined;
            } catch (error) { failure(error); }
          };
          worker.onerror = event => {
            event.preventDefault?.();
            if (closed || terminalError) return;
            failure(event.message || 'Worker initialization failed.');
          };
          worker.onmessageerror = () => {
            if (!closed && !terminalError) failure('Worker response could not be decoded.');
          };
          // This bounds startup only; it does not escalate cancellation of computation.
          timer = setTimeout(() => failure('Browser runtime initialization timed out.'), startupTimeout);
          worker.postMessage({ id: 1, operation: 'initialize' });
        } catch (error) { failure(error); }
      });
      return initialization;
    },
    close() {
      closed = true;
      stop(unavailable());
    },
    async *graphUpdates() { throw unavailable(); },
  };
  for (const name of ['loadWorkspace', 'getGraph', 'replaceRecipe', 'importRecipe',
    'exportRecipe', 'evaluateGraph', 'ensureGraph', 'getExampleRecipe', 'listExamples',
    'selectExample', 'applyFeatureReuse', 'faceCandidates', 'previewFaces',
    'applyFaces', 'exportCad']) backend[name] = async () => { throw unavailable(); };
  return backend;
}
