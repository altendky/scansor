// HTTP is one implementation of the operations described in backend.md.
export class BackendError extends Error {
  constructor(message, { code = 'backend_error', status, requestId } = {}) {
    super(message);
    this.name = 'BackendError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

function pause(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const cancelled = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', cancelled);
      resolve();
    }, milliseconds);
    signal.addEventListener('abort', cancelled, { once: true });
  });
}

export function createHttpBackend({ fetch: fetchResponse = globalThis.fetch,
  pollInterval = 150, wait = pause } = {}) {
  const lifetime = new AbortController();
  let nextRequestId = 0;
  const signalFor = signal => signal
    ? AbortSignal.any([lifetime.signal, signal]) : lifetime.signal;
  async function request(path, value, { signal, binary = false, message } = {}) {
    const requestId = ++nextRequestId, active = signalFor(signal);
    active.throwIfAborted();
    try {
      const response = await fetchResponse(path, {
        signal: active,
        ...(value === undefined ? {} : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Scansor-Request': '1' },
          body: JSON.stringify(value),
        }),
      });
      active.throwIfAborted();
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new BackendError(body.error || message || `Request failed (${response.status})`,
          { code: 'http_error', status: response.status, requestId });
      }
      const body = binary ? await response.arrayBuffer() : await response.json();
      active.throwIfAborted();
      return body;
    } catch (error) {
      if (active.aborted) throw active.reason;
      if (error instanceof BackendError) throw error;
      throw new BackendError(error.message, { code: 'transport_error', requestId });
    }
  }
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad: true, examples: true,
      progress: 'snapshots', cancelComputation: false, sources: 'retained-examples' }),
    async loadWorkspace(options) {
      const metadata = await request('/api/meta', undefined, options);
      const { url: positionsUrl, ...positionsInfo } = metadata.positions,
        { url: indicesUrl, ...indicesInfo } = metadata.indices;
      const [positions, indices] = await Promise.all([
        request(positionsUrl, undefined, { ...options, binary: true, message: 'Could not load mesh buffers' }),
        request(indicesUrl, undefined, { ...options, binary: true, message: 'Could not load mesh buffers' }),
      ]);
      return { metadata: { ...metadata, positions: positionsInfo, indices: indicesInfo }, positions, indices };
    },
    getGraph: ({ revision, ...options } = {}) => request(
      Number.isSafeInteger(revision) && revision >= 0 ? `/api/graph?revision=${revision}` : '/api/graph',
      undefined, options),
    replaceRecipe: (payload, options) => request('/api/graph', payload, options),
    importRecipe: (payload, options) => request('/api/graph/import', payload, options),
    async exportRecipe(state) {
      lifetime.signal.throwIfAborted();
      if (!state?.recipe) throw new BackendError('Actions have not finished loading yet.');
      return structuredClone(state.recipe);
    },
    evaluateGraph: (payload, options) => request('/api/graph/evaluate', payload, options),
    ensureGraph: (payload, options) => request('/api/graph/ensure', payload, options),
    getExampleRecipe: options => request('/api/graph/example', undefined, options),
    listExamples: options => request('/api/examples', undefined, options),
    selectExample: (payload, options) => request('/api/examples/select', payload, options),
    applyFeatureReuse: (payload, options) => request('/api/graph/feature-reuse/apply', payload, options),
    faceCandidates: (payload, options) => request('/api/graph/build-faces/candidates', payload, options),
    previewFaces: (payload, options) => request('/api/graph/build-faces/preview', payload, options),
    applyFaces: (payload, options) => request('/api/graph/build-faces/apply', payload, options),
    exportCad: (payload, options) => request('/api/export/cad', payload,
      { ...options, binary: true, message: 'Export failed' }),
    async *graphUpdates({ revision, signal, isCurrent = () => true } = {}) {
      const active = signalFor(signal);
      do {
        active.throwIfAborted();
        if (!isCurrent()) throw new BackendError('The requested operation changed. Try again.',
          { code: 'observation_superseded' });
        const state = await backend.getGraph({ revision, signal: active });
        active.throwIfAborted();
        if (!isCurrent()) throw new BackendError('The requested operation changed. Try again.',
          { code: 'observation_superseded' });
        if (state.unchanged) {
          if (!Number.isSafeInteger(revision) || revision < 0 || state.revision !== revision)
            throw new BackendError('Invalid unchanged graph response. Reload the current actions.');
        } else revision = state.revision;
        yield state;
        if (!state.evaluation_running) return;
        await wait(pollInterval, active);
      } while (true);
    },
    close() {
      lifetime.abort(new BackendError('Backend connection closed.', { code: 'backend_closed' }));
    },
  };
  return backend;
}
