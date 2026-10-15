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
  let sourcePublication = 0, acceptedGeneration, acceptedMetadata;
  const signalFor = signal => signal
    ? AbortSignal.any([lifetime.signal, signal]) : lifetime.signal;
  const superseded = () => new BackendError('The accepted workspace changed. Reload the current workspace.',
    { code: 'observation_superseded', status: 409 });
  const checkPublication = publication => { if (publication !== sourcePublication) throw superseded(); };
  async function request(path, value, { signal, binary = false, message, raw = false,
    generation, publication = sourcePublication, expectedGeneration, sourceMutation = false } = {}) {
    const requestId = ++nextRequestId, active = signalFor(signal);
    active.throwIfAborted();
    checkPublication(publication);
    try {
      const response = await fetchResponse(path, {
        signal: active,
        ...(value === undefined && generation !== undefined
          ? { headers: { 'X-Scansor-Generation': String(generation) } } : {}),
        ...(value === undefined ? {} : {
          method: 'POST',
          headers: { 'Content-Type': raw ? 'application/octet-stream' : 'application/json',
            'X-Scansor-Request': '1',
            ...(generation === undefined ? {} : { 'X-Scansor-Generation': String(generation) }) },
          body: raw ? value : JSON.stringify(value),
        }),
      });
      active.throwIfAborted();
      checkPublication(publication);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new BackendError(body.error?.message || body.error || message || `Request failed (${response.status})`,
          { code: body.error?.code || body.code || 'http_error', status: response.status, requestId });
      }
      if (binary && expectedGeneration !== undefined
        && response.headers.get('X-Scansor-Generation') !== String(expectedGeneration)) throw superseded();
      const body = binary ? await response.arrayBuffer() : await response.json();
      active.throwIfAborted();
      checkPublication(publication);
      if (!binary && !sourceMutation && acceptedGeneration !== undefined
        && body.project_generation !== undefined && body.project_generation !== acceptedGeneration)
        throw superseded();
      return body;
    } catch (error) {
      if (active.aborted) throw active.reason;
      if (error instanceof BackendError) throw error;
      throw new BackendError(error.message, { code: 'transport_error', requestId });
    }
  }
  const graphRequest = (path, payload, options = {}, binary = false) => request(path, payload,
    { ...options, binary, generation: acceptedGeneration,
      ...(binary && acceptedGeneration !== undefined ? { expectedGeneration: acceptedGeneration } : {}) });
  const operationPath = operationId => {
    if (typeof operationId !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(operationId))
      throw new BackendError('Invalid source operation identity.', { code: 'invalid_operation' });
    return `/api/sources/imports/${operationId}`;
  };
  const operationFailure = operation => new BackendError(operation.error?.message || 'Scan import did not complete.',
    { code: operation.error?.code || `source_${operation.status}`, status: 409 });
  async function deliverAccepted(operation, options, publication) {
    try {
      checkPublication(publication);
      return await backend.loadWorkspace({ ...options, generation: operation.acceptedGeneration });
    } catch (cause) {
      if (signalFor(options?.signal).aborted) throw cause;
      const error = new BackendError(`The project change was accepted, but its workspace could not be delivered: ${cause.message}`,
        { code: 'accepted_workspace_unavailable', status: cause.status });
      error.cause = cause;
      error.acceptedOperation = structuredClone(operation);
      throw error;
    }
  }
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad: true, examples: true,
      progress: 'snapshots', cancelComputation: false, sources: 'single-scan-and-retained-examples', sourceImport: true }),
    async loadWorkspace({ generation, token, ...options } = {}) {
      const publication = sourcePublication;
      const metadata = await request('/api/meta', undefined, { ...options, publication, sourceMutation: true });
      const cursor = metadata.project_generation;
      if (generation !== undefined && cursor !== generation
        || token !== undefined && metadata.graph?.token !== token) throw superseded();
      if (cursor !== undefined && (!Number.isSafeInteger(cursor) || cursor < 0
        || metadata.graph?.project_generation !== cursor))
        throw new BackendError('Invalid workspace generation.', { code: 'invalid_workspace' });
      const { url: positionsUrl, ...positionsInfo } = metadata.positions,
        { url: indicesUrl, ...indicesInfo } = metadata.indices;
      const [positions, indices] = await Promise.all([
        request(positionsUrl, undefined, { ...options, publication, binary: true, expectedGeneration: cursor, message: 'Could not load mesh buffers' }),
        request(indicesUrl, undefined, { ...options, publication, binary: true, expectedGeneration: cursor, message: 'Could not load mesh buffers' }),
      ]);
      checkPublication(publication);
      signalFor(options.signal).throwIfAborted();
      acceptedGeneration = cursor;
      acceptedMetadata = metadata;
      return { metadata: { ...metadata, positions: positionsInfo, indices: indicesInfo }, positions, indices,
        generation: cursor, graph: metadata.graph };
    },
    getGraph: ({ revision, ...options } = {}) => request(
      Number.isSafeInteger(revision) && revision >= 0 ? `/api/graph?revision=${revision}` : '/api/graph',
      undefined, { ...options, generation: acceptedGeneration }),
    replaceRecipe: (payload, options) => graphRequest('/api/graph', payload, options),
    importRecipe: (payload, options) => graphRequest('/api/graph/import', payload, options),
    async exportRecipe(state) {
      lifetime.signal.throwIfAborted();
      if (!state?.recipe) throw new BackendError('Actions have not finished loading yet.');
      return structuredClone(state.recipe);
    },
    evaluateGraph: (payload, options) => graphRequest('/api/graph/evaluate', payload, options),
    ensureGraph: (payload, options) => graphRequest('/api/graph/ensure', payload, options),
    getExampleRecipe: options => request('/api/graph/example', undefined, options),
    listExamples: options => request('/api/examples', undefined, options),
    async selectExample(payload, options) {
      const publication = ++sourcePublication;
      const result = await request('/api/examples/select', payload,
        { ...options, generation: acceptedGeneration, publication, sourceMutation: true });
      acceptedMetadata = undefined;
      return result;
    },
    applyFeatureReuse: (payload, options) => graphRequest('/api/graph/feature-reuse/apply', payload, options),
    faceCandidates: (payload, options) => graphRequest('/api/graph/build-faces/candidates', payload, options),
    previewFaces: (payload, options) => graphRequest('/api/graph/build-faces/preview', payload, options),
    applyFaces: (payload, options) => graphRequest('/api/graph/build-faces/apply', payload, options),
    exportCad: (payload, options) => graphRequest('/api/export/cad', payload, options, true),
    getSourceOperation: async ({ operationId }, options) => request(operationPath(operationId), undefined,
      { ...options, sourceMutation: true }),
    cancelSourceOperation: async ({ operationId, generation, token }, options) => request(
      `${operationPath(operationId)}/cancel`, { generation, token }, { ...options, sourceMutation: true }),
    async resetProject(payload, options = {}) {
      const publication = ++sourcePublication;
      const result = await request('/api/project/reset', payload,
        { ...options, generation: acceptedGeneration, publication, sourceMutation: true });
      return deliverAccepted({ status: 'accepted', terminal: true,
        acceptedGeneration: result.project_generation }, options, publication);
    },
    async addScan({ file, generation, token }, { signal, onProgress = () => {} } = {}) {
      if (!file || typeof file.arrayBuffer !== 'function' || !Number.isSafeInteger(file.size) || file.size <= 0)
        throw new BackendError('Choose a nonempty scan file.', { code: 'invalid_source' });
      const limit = acceptedMetadata?.capabilities?.source_import?.limits?.max_source_bytes;
      if (Number.isSafeInteger(limit) && file.size > limit)
        throw new BackendError('Scan exceeds the declared source byte limit.', { code: 'source_limit' });
      const publication = ++sourcePublication, active = signalFor(signal);
      let operation = await request('/api/sources/imports',
        { generation, token, filename: file.name || 'scan.ply', size: file.size },
        { signal: active, publication, sourceMutation: true });
      const operationId = operation.operationId;
      const path = operationPath(operationId), upload = new AbortController();
      const publish = value => {
        checkPublication(publication);
        active.throwIfAborted();
        if (value.operationId !== operationId || value.generation !== generation || value.token !== token
          || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.terminal !== 'boolean'
          || value.status === 'accepted' && (!value.terminal || !Number.isSafeInteger(value.acceptedGeneration)
            || value.acceptedGeneration <= generation))
          throw new BackendError('Invalid scan operation response.', { code: 'invalid_operation' });
        if (value !== operation && (operation.terminal || value.revision <= operation.revision)) return;
        operation = value;
        onProgress(structuredClone(operation));
      };
      publish(operation);
      if (operation.terminal) {
        if (operation.status !== 'accepted') throw operationFailure(operation);
        return deliverAccepted(operation, { signal: active }, publication);
      }
      let uploadError;
      const transfer = request(`${path}/bytes`, file,
        { signal: AbortSignal.any([active, upload.signal]), raw: true, publication, sourceMutation: true })
        .then(publish).catch(error => { uploadError = error; });
      try {
        while (!operation.terminal) {
          await wait(pollInterval, active);
          if (!operation.terminal) {
            // A lost upload reply is not proof that the accepted transaction
            // failed. Its independently queryable record remains authoritative.
            try { publish(await request(path, undefined, { signal: active, publication, sourceMutation: true })); }
            catch (error) { throw uploadError || error; }
          }
        }
        if (operation.status !== 'accepted') throw operationFailure(operation);
        return await deliverAccepted(operation, { signal: active }, publication);
      } finally {
        upload.abort();
        // Handlers are attached before polling; a late upload failure cannot
        // become an unhandled rejection or undo an accepted terminal record.
        void transfer;
      }
    },
    async *graphUpdates({ revision, signal, isCurrent = () => true } = {}) {
      const active = signalFor(signal), publication = sourcePublication;
      do {
        active.throwIfAborted();
        checkPublication(publication);
        if (!isCurrent()) throw new BackendError('The requested operation changed. Try again.',
          { code: 'observation_superseded' });
        const state = await backend.getGraph({ revision, signal: active, publication });
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
