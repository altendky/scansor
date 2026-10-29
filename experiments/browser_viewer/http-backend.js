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
  pollInterval = 150, wait = pause, onLifecycle = () => {} } = {}) {
  const lifetime = new AbortController();
  let nextRequestId = 0;
  let sourcePublication = 0, acceptedGeneration, acceptedMetadata, latestGraph;
  let evaluationObservation = 0, evaluationVersion = 0, evaluation, cancellation, runtimeLifetime = 0;
  let lifecycle = Object.freeze({ state: 'complete', lifetime: runtimeLifetime,
    canCancel: false, canForce: false, interrupts: false });
  const cancelled = () => new BackendError('Computation was cancelled. Evaluate again when ready.',
    { code: 'computation_cancelled' });
  const checkObservation = observation => {
    if (observation !== undefined && observation !== evaluationObservation) throw cancelled();
  };
  const publishLifecycle = (state, detail = {}) => {
    lifecycle = Object.freeze({ state, ...detail, lifetime: runtimeLifetime,
      canCancel: detail.canCancel ?? ['queued', 'running'].includes(state), canForce: false, interrupts: false });
    onLifecycle(lifecycle);
  };
  function observeEvaluation(state, version = evaluationVersion) {
    if (!Object.hasOwn(state || {}, 'evaluation')) return;
    const next = state.evaluation;
    if (next === null && version !== evaluationVersion)
      throw new BackendError('A newer evaluation state is already current.', { code: 'observation_superseded' });
    if (next !== null && (!next || typeof next.id !== 'string' || !next.id
      || !Number.isSafeInteger(next.generation) || next.generation < 0
      || next.sequence !== undefined && (!Number.isSafeInteger(next.sequence) || next.sequence < 0)
      || !['queued', 'running', 'cancel-requested', 'cancelled', 'complete', 'failed'].includes(next.state)
      || !['evaluate', 'ensure'].includes(next.kind) || !Array.isArray(next.roots)
      || next.roots.some(root => typeof root !== 'string')))
      throw new BackendError('Invalid evaluation lifecycle response.', { code: 'invalid_operation' });
    const rank = { queued: 0, running: 1, 'cancel-requested': 2, cancelled: 3, complete: 3, failed: 3 };
    if (next && evaluation && (Number.isSafeInteger(next.sequence) && Number.isSafeInteger(evaluation.sequence)
      && next.sequence < evaluation.sequence || next.id === evaluation.id && rank[next.state] < rank[evaluation.state]))
      throw new BackendError('A newer evaluation state is already current.', { code: 'observation_superseded' });
    if (next?.id !== evaluation?.id || next?.state !== evaluation?.state || next?.sequence !== evaluation?.sequence)
      evaluationVersion++;
    evaluation = next;
    const activeState = cancellation?.id === next?.id && ['queued', 'running'].includes(next?.state)
      ? 'cancel-requested' : next?.state || 'complete';
    publishLifecycle(activeState, next ? { operation: next.kind, roots: [...next.roots],
      evaluationId: next.id, generation: next.generation } : {});
  }
  const signalFor = signal => signal
    ? AbortSignal.any([lifetime.signal, signal]) : lifetime.signal;
  const superseded = () => new BackendError('The accepted workspace changed. Reload the current workspace.',
    { code: 'observation_superseded', status: 409 });
  const checkPublication = publication => { if (publication !== sourcePublication) throw superseded(); };
  async function request(path, value, { signal, binary = false, blob = false, message, raw = false,
    generation, publication = sourcePublication, expectedGeneration, sourceMutation = false, observation } = {}) {
    const requestId = ++nextRequestId, active = signalFor(signal);
    active.throwIfAborted();
    checkPublication(publication);
    checkObservation(observation);
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
      checkObservation(observation);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new BackendError(body.error?.message || body.error || message || `Request failed (${response.status})`,
          { code: body.error?.code || body.code || 'http_error', status: response.status, requestId });
      }
      if (binary && expectedGeneration !== undefined
        && response.headers.get('X-Scansor-Generation') !== String(expectedGeneration)) throw superseded();
      const body = binary ? blob ? await response.blob() : await response.arrayBuffer() : await response.json();
      active.throwIfAborted();
      checkPublication(publication);
      checkObservation(observation);
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
  function rememberGraph(state, known = latestGraph, version = evaluationVersion) {
    observeEvaluation(state, version);
    if (!state?.token || state.unchanged) return state;
    if (!state.recipe) {
      const matching = [latestGraph, known].find(value => value?.recipe && value.token === state.token);
      if (!matching)
        throw new BackendError('Invalid recipe-less graph response. Reload the current actions.');
      const { evaluation_error: previousError, status: previousStatus, ...previous } = matching;
      void previousError;
      void previousStatus;
      state = { ...previous, ...state };
    }
    if (!Number.isSafeInteger(latestGraph?.revision) || !Number.isSafeInteger(state.revision)
      || state.revision >= latestGraph.revision) latestGraph = state;
    return state;
  }
  const graphRequest = async (path, payload, options = {}, binary = false) => {
    const known = latestGraph, version = evaluationVersion;
    const result = await request(path, payload,
      { observation: evaluationObservation, ...options, binary, generation: acceptedGeneration,
        ...(binary && acceptedGeneration !== undefined ? { expectedGeneration: acceptedGeneration } : {}) });
    if (result?.states && result.token) return rememberGraph(result, known, version);
    observeEvaluation(result, version);
    if (result?.graph?.states) result.graph = rememberGraph(result.graph, known, version);
    return result;
  };
  const volumeRequest = (path, payload, options) => {
    const profile = acceptedMetadata?.selection_volumes;
    if (!profile || Object.keys(profile).length !== 2 || profile.version !== 1 || profile.max_preview_cells !== 2048)
      return Promise.reject(new BackendError('This backend does not support selection volumes.', { code: 'unsupported_capability' }));
    return graphRequest(`/api/graph/selection-volumes/${path}`, payload, options);
  };
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
  function validateFile(file) {
    if (!file || typeof file.arrayBuffer !== 'function' || !Number.isSafeInteger(file.size) || file.size <= 0)
      throw new BackendError('Choose a nonempty scan file.', { code: 'invalid_source' });
    const limit = acceptedMetadata?.capabilities?.source_import?.limits?.max_source_bytes;
    if (Number.isSafeInteger(limit) && file.size > limit)
      throw new BackendError('Scan exceeds the declared source byte limit.', { code: 'source_limit' });
  }
  async function observeSourceOperation(operation, { generation, token, file, signal,
    onProgress = () => {}, publication, purpose } = {}) {
    const active = signalFor(signal), operationId = operation.operationId;
    const invalidOperation = (value, cause = new BackendError('Invalid source operation response.', { code: 'invalid_operation' })) => {
      if (value.status !== 'accepted') return cause;
      const error = new BackendError('The operation reports acceptance, but its workspace receipt is invalid. Load the current project.',
        { code: 'accepted_workspace_unavailable' });
      error.cause = cause; error.acceptedOperation = structuredClone(value);
      return error;
    };
    let path;
    try { path = operationPath(operationId); }
    catch (error) { throw invalidOperation(operation, error); }
    const upload = new AbortController();
    const retainedSourceChange = ['create-virtual-scan', 'activate-scan'].includes(purpose);
    const publish = value => {
      checkPublication(publication);
      active.throwIfAborted();
      if (value.operationId !== operationId || value.generation !== generation || value.token !== token
        || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.terminal !== 'boolean'
        || value.status === 'accepted' && (!value.terminal || !Number.isSafeInteger(value.acceptedGeneration)
          || value.acceptedGeneration <= generation)
        || purpose && value.purpose !== purpose
        || purpose === 'replace-scan' && value.status === 'accepted' && value.acceptedGeneration !== generation + 1
        || retainedSourceChange && (value.uploadRequired !== false
          || value.status === 'accepted' && value.acceptedGeneration !== generation + 1)
        || ['open-actions', 'open-project'].includes(purpose) && (value.uploadRequired !== !!file
          || value.status === 'accepted' && (value.acceptedGeneration !== generation + 1
            || !Number.isSafeInteger(value.import_warning_count) || value.import_warning_count < 0)))
        throw invalidOperation(value);
      if (value !== operation && (operation.terminal || value.revision <= operation.revision)) return;
      operation = value;
      onProgress(structuredClone(operation));
    };
    const deliver = async () => {
      const bundle = await deliverAccepted(operation, { signal: active }, publication);
      if (!['open-actions', 'open-project'].includes(purpose)) return bundle;
      const warnings = bundle.metadata.import_warnings;
      if (!Array.isArray(warnings) || warnings.length !== operation.import_warning_count) {
        const cause = new BackendError('Accepted actions warnings do not match their workspace receipt.', { code: 'invalid_workspace' });
        const error = new BackendError(cause.message, { code: 'accepted_workspace_unavailable' });
        error.acceptedOperation = structuredClone(operation); error.cause = cause;
        throw error;
      }
      return { ...bundle, import_warnings: warnings };
    };
    publish(operation);
    if (operation.terminal) {
      if (operation.status !== 'accepted') throw operationFailure(operation);
      return deliver();
    }
    let uploadError;
    const transfer = file ? request(`${path}/bytes`, file,
      { signal: AbortSignal.any([active, upload.signal]), raw: true, publication, sourceMutation: true })
      .then(publish).catch(error => { uploadError = error; }) : Promise.resolve();
    try {
      while (!operation.terminal) {
        await wait(pollInterval, active);
        if (!operation.terminal) {
          // Independent operation status remains authoritative after a lost reply.
          try { publish(await request(path, undefined, { signal: active, publication, sourceMutation: true })); }
          catch (error) { throw uploadError || error; }
        }
      }
      if (operation.status !== 'accepted') throw operationFailure(operation);
      return await deliver();
    } finally {
      upload.abort();
      void transfer;
    }
  }
  const backend = {
    capabilities: Object.freeze({ numerical: true, cad: true, examples: true,
      progress: 'snapshots', cancelComputation: true, forceRestart: false, sources: 'single-scan-and-retained-examples',
      sourceImport: true, sourceActions: true, sourceReplacement: true, selectionVolumes: true, virtualScans: true,
      projectArchives: true }),
    async loadWorkspace({ generation, token, ...options } = {}) {
      const publication = sourcePublication, observation = evaluationObservation, version = evaluationVersion;
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
      checkObservation(observation);
      observeEvaluation(metadata.graph, version);
      acceptedGeneration = cursor;
      acceptedMetadata = metadata;
      latestGraph = metadata.graph;
      return { metadata: { ...metadata, positions: positionsInfo, indices: indicesInfo }, positions, indices,
        generation: cursor, graph: metadata.graph };
    },
    async getGraph({ revision, ...options } = {}) {
      const known = latestGraph, version = evaluationVersion;
      const query = new URLSearchParams();
      if (Number.isSafeInteger(revision) && revision >= 0) query.set('revision', revision);
      if (query.has('revision') && known?.recipe) query.set('recipe_token', known.token);
      const suffix = query.size ? `?${query}` : '';
      return rememberGraph(await request(`/api/graph${suffix}`,
        undefined, { observation: evaluationObservation, ...options, generation: acceptedGeneration }), known, version);
    },
    getLifecycle: () => lifecycle,
    cancelComputation() {
      if (lifetime.signal.aborted) return Promise.reject(lifetime.signal.reason);
      if (cancellation) return cancellation.promise;
      if (!evaluation || !['queued', 'running', 'cancel-requested'].includes(evaluation.state)) return Promise.resolve(null);
      const operation = evaluation, observation = ++evaluationObservation;
      const operationRecord = { id: operation.id, promise: null };
      cancellation = operationRecord;
      operationRecord.promise = (async () => {
        try {
          let state = await graphRequest('/api/graph/cancel',
            { generation: operation.generation, evaluationId: operation.id }, { observation });
          while (state.evaluation?.id === operation.id
            && ['queued', 'running', 'cancel-requested'].includes(state.evaluation.state)) {
            await wait(pollInterval, lifetime.signal);
            checkObservation(observation);
            state = await backend.getGraph({ observation });
          }
          return state;
        } catch (error) {
          if (observation === evaluationObservation && evaluation?.id === operation.id && !lifetime.signal.aborted)
            publishLifecycle('cancel-requested', { operation: operation.kind, roots: [...operation.roots],
              evaluationId: operation.id, generation: operation.generation, canCancel: true, retryCancel: true,
              message: 'Cancellation could not be confirmed. Retry Cancel; native work may still be running.' });
          throw error;
        } finally { if (cancellation === operationRecord) cancellation = undefined; }
      })();
      publishLifecycle('cancel-requested', { operation: operation.kind, roots: [...operation.roots],
        evaluationId: operation.id, generation: operation.generation });
      return operationRecord.promise;
    },
    replaceRecipe: (payload, options) => graphRequest('/api/graph', payload, options),
    importRecipe: (payload, options) => graphRequest('/api/graph/import', payload, options),
    authorSelectionVolume: (payload, options) => volumeRequest('author', payload, options),
    setSelectionVolumeOptions: (payload, options) => volumeRequest('options', payload, options),
    chooseSelectionVolume: (payload, options) => volumeRequest('choose', payload, options),
    getSelectionVolumeChoices: (payload, options) => volumeRequest('choices', payload, options),
    getSelectionVolumePreview: (payload, options) => volumeRequest('preview', payload, options),
    deleteSelectionVolume: (payload, options) => volumeRequest('delete', payload, options),
    async inspectActions(payload, options) {
      const result = await graphRequest('/api/actions/inspect', payload, options);
      if (result.generation !== payload.generation || result.token !== payload.token
        || !['matching', 'locate', 'empty'].includes(result.classification)
        || result.uploadRequired !== (result.classification === 'locate')
        || !result.recipe || !Array.isArray(result.import_warnings)
        || result.classification === 'locate' && !result.descriptor)
        throw new BackendError('Invalid actions inspection response.', { code: 'invalid_operation' });
      return result;
    },
    async openActions({ recipe, file, generation, token, confirmedDiscard }, { signal, onProgress } = {}) {
      if (file !== undefined) validateFile(file);
      const publication = ++sourcePublication;
      const operation = await request('/api/actions/open', { recipe, generation, token, confirmedDiscard,
        ...(file === undefined ? {} : { source: { filename: file.name || 'scan.ply', size: file.size } }) },
      { signal, generation: acceptedGeneration, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, file, signal, onProgress,
        publication, purpose: 'open-actions' });
    },
    async exportRecipe(state) {
      lifetime.signal.throwIfAborted();
      if (!state?.recipe) throw new BackendError('Actions have not finished loading yet.');
      return structuredClone(state.recipe);
    },
    async openProject({ file, generation, token, confirmedDiscard }, { signal, onProgress } = {}) {
      const profile = acceptedMetadata?.project_archive;
      if (profile?.supported !== true || profile.version !== 1)
        throw new BackendError('This backend does not support project files.', { code: 'unsupported_capability' });
      if (!file || typeof file.slice !== 'function' || !Number.isSafeInteger(file.size) || file.size <= 0)
        throw new BackendError('Choose a nonempty project file.', { code: 'invalid_source' });
      if (!Number.isSafeInteger(profile.limits?.max_archive_bytes) || file.size > profile.limits.max_archive_bytes)
        throw new BackendError('Project exceeds the archive byte limit.', { code: 'source_limit' });
      const publication = ++sourcePublication;
      const operation = await request('/api/project/open',
        { generation, token, confirmedDiscard, filename: file.name || 'project.scansor', size: file.size },
        { signal, generation: acceptedGeneration, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, file, signal, onProgress,
        publication, purpose: 'open-project' });
    },
    async exportProject({ generation, token }, options = {}) {
      const profile = acceptedMetadata?.project_archive;
      if (profile?.supported !== true || profile.version !== 1)
        throw new BackendError('This backend does not support project files.', { code: 'unsupported_capability' });
      return request('/api/project/export', { generation, token }, { ...options, binary: true, blob: true,
        generation: acceptedGeneration, expectedGeneration: generation });
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
    previewFeatureReuse: (payload, options) => graphRequest('/api/graph/feature-reuse/preview', payload, options),
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
      validateFile(file);
      const publication = ++sourcePublication;
      const operation = await request('/api/sources/imports',
        { generation, token, filename: file.name || 'scan.ply', size: file.size },
        { signal, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, file, signal, onProgress, publication });
    },
    async replaceScan({ file, generation, token }, { signal, onProgress = () => {} } = {}) {
      const profile = acceptedMetadata?.source_replacement;
      if (profile?.supported !== true || Object.keys(profile).length !== 4 || profile.version !== 1 || profile.alignment !== 'already-aligned'
        || profile.source_kind !== 'generic-scan')
        throw new BackendError('This workspace does not support aligned scan replacement.', { code: 'unsupported_capability' });
      validateFile(file);
      const publication = ++sourcePublication;
      const operation = await request('/api/sources/imports',
        { generation, token, filename: file.name || 'scan.ply', size: file.size, purpose: 'replace-scan' },
        { signal, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, file, signal, onProgress, publication, purpose: 'replace-scan' });
    },
    async createVirtualScan({ generation, token, settings }, { signal, onProgress = () => {} } = {}) {
      const profile = acceptedMetadata?.virtual_scans;
      if (profile?.supported !== true || profile.version !== 1 || profile.can_create !== true)
        throw new BackendError('This workspace cannot create a virtual scan.', { code: 'unsupported_capability' });
      const publication = ++sourcePublication;
      const operation = await request('/api/sources/virtual', { generation, token, settings },
        { signal, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, signal, onProgress, publication, purpose: 'create-virtual-scan' });
    },
    async activateScan({ generation, token, binding }, { signal, onProgress = () => {} } = {}) {
      const profile = acceptedMetadata?.virtual_scans;
      if (profile?.supported !== true || profile.version !== 1 || profile.can_activate !== true)
        throw new BackendError('This workspace cannot switch retained scans.', { code: 'unsupported_capability' });
      const publication = ++sourcePublication;
      const operation = await request('/api/sources/activate', { generation, token, binding },
        { signal, publication, sourceMutation: true });
      return observeSourceOperation(operation, { generation, token, signal, onProgress, publication, purpose: 'activate-scan' });
    },
    async *graphUpdates({ revision, signal, isCurrent = () => true } = {}) {
      const active = signalFor(signal), publication = sourcePublication, observation = evaluationObservation;
      do {
        active.throwIfAborted();
        checkPublication(publication);
        checkObservation(observation);
        if (!isCurrent()) throw new BackendError('The requested operation changed. Try again.',
          { code: 'observation_superseded' });
        const state = await backend.getGraph({ revision, signal: active, publication, observation });
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
      runtimeLifetime++;
      publishLifecycle('closed');
    },
  };
  return backend;
}
