import { checkedFetch } from './integrity.mjs';

const base = new URL('./', import.meta.url);
const started = performance.now();
const phase = name => postMessage({ phase: name, elapsed_ms: performance.now() - started });
let manifest;
let python;
let initialization;
let numericComplete = false;
let cadLoaded = false;
let workspaceLoaded = false;
let workspaceCadEnabled = false;
let workspaceHasCad = false;
let cadInitialization;
const cadOperations = new Set(['faceCandidates', 'previewFaces', 'applyFaces', 'exportCad']);
const selectionVolumeOperations = new Set(['authorSelectionVolume', 'setSelectionVolumeOptions',
  'chooseSelectionVolume', 'getSelectionVolumeChoices', 'getSelectionVolumePreview', 'deleteSelectionVolume']);
const cadActions = new Set(['surface_intersection', 'trimmed_face', 'arranged_face', 'build_faces', 'body']);
let chain = Promise.resolve();
let interruptBuffer;
let minimumGeneration = 0;
let boundary;
let sourceBoundary;
let activeSource;
let sourcePoisoned = false;
let initialMode = 'example';
// Primitive-only accounting for safe-boundary capacity measurements. These are
// overlapping logical categories, not a claim about instantaneous process RSS.
const sourceTransferMetrics = { staging_bytes: 0, max_staging_bytes: 0,
  largest_chunk_bytes: 0, display_copy_bytes: 0 };
const lifecycle = (request, state, interruptible = false) => postMessage({
  kind: 'lifecycle', id: request.id, operation: request.operation,
  generation: request.generation, state, interruptible,
});
const cancellationError = error => {
  // SIGINT can be raised in Python call setup before the JSON envelope begins.
  // The second byte proves that this was the coordinator's explicit request.
  if (interruptBuffer && Atomics.load(interruptBuffer, 1) && /KeyboardInterrupt/.test(error.message))
    return Object.assign(error, { code: 'computation_cancelled', status: 409 });
  return error;
};

async function initialize() {
  phase('manifest');
  const response = await fetch(new URL('manifest.json', base));
  if (!response.ok) throw new Error(`Missing manifest: HTTP ${response.status}`);
  manifest = await response.json();
  if (manifest.schema_version !== 1) throw new Error('Unsupported runtime manifest');
  phase('numeric asset verification');
  for (const record of manifest.assets.filter(r => r.group === 'numeric')) await checkedFetch(record, base);
  phase('Python runtime');
  const { loadPyodide } = await import(new URL('runtime/pyodide.mjs', base));
  python = await loadPyodide({ indexURL: new URL('runtime/', base).href });
  if (python.version !== manifest.versions.pyodide) throw new Error(`Incompatible Pyodide ${python.version}`);
  phase('NumPy and Pydantic');
  await python.loadPackage(['numpy', 'pydantic']);
  phase('Scansor source and frozen fixture');
  const record = path => {
    const asset = manifest.assets.find(r => r.path === path);
    if (!asset) throw new Error(`Missing asset record: ${path}`);
    return asset;
  };
  python.unpackArchive(await checkedFetch(record('scansor.zip'), base), 'zip', { extractDir: '/app' });
  for (const path of ['fixture.npz', 'native-results.json']) python.FS.writeFile(`/${path}`, new Uint8Array(await checkedFetch(record(path), base)));
  // Return JSON strings rather than keeping Python result proxies in JavaScript.
  python.runPython("import sys; sys.path.insert(0, '/app')");
  const versions = JSON.parse(python.runPython("import json, platform, numpy, pydantic; json.dumps({'python': platform.python_version(), 'numpy': numpy.__version__, 'pydantic': pydantic.__version__})"));
  for (const key of ['python', 'numpy', 'pydantic']) if (versions[key] !== manifest.versions[key]) throw new Error(`Incompatible ${key}: ${versions[key]}`);
}

function ensureWorkspace() {
  if (!workspaceLoaded) {
    const profile = manifest.worker_protocol?.source_import;
    python.globals.set('browser_source_limits', JSON.stringify(profile?.limits || null));
    python.globals.set('browser_initial_mode', initialMode);
    python.globals.set('browser_cad_available', manifest.cad === true);
    try {
      python.runPython("from pathlib import Path; from experiments.browser_runtime.workspace import BrowserWorkspace; from experiments.scan_workspace import ScanLimits; browser_workspace = BrowserWorkspace(None if browser_initial_mode == 'empty' else Path('/app/examples/nozzle-bayonette-simplified'), scan_limits=ScanLimits(**json.loads(browser_source_limits)) if json.loads(browser_source_limits) else None, cad_available=browser_cad_available)");
    } finally { python.runPython("del browser_source_limits, browser_initial_mode, browser_cad_available"); }
    workspaceLoaded = true;
  }
}

function sourceCall(method, payload, { path = '', recovery, progress, cancelled } = {}) {
  python.globals.set('browser_source_payload', JSON.stringify(payload));
  python.globals.set('browser_source_path', path);
  python.globals.set('browser_source_recovery', JSON.stringify(recovery || null));
  python.globals.set('browser_source_progress', progress || (() => {}));
  python.globals.set('browser_source_cancelled', cancelled || (() => false));
  try {
    const expression = method === 'prepare_source_json'
      ? "browser_workspace.prepare_source_json(browser_source_payload, browser_source_path, progress=lambda p: browser_source_progress(json.dumps({'phase':p.phase,'completed':p.completed,'total':p.total})), cancelled=browser_source_cancelled, **({'recovery_generation':json.loads(browser_source_recovery)['generation'],'recovery_identity':json.loads(browser_source_recovery)['identity'],'recovery_recipe':json.loads(browser_source_recovery)['recipe'],'recovery_import_warnings':json.loads(browser_source_recovery).get('import_warnings',[])} if json.loads(browser_source_recovery) else {}))"
      : method === 'preflight_actions_json'
        ? 'browser_workspace.preflight_actions_json(browser_source_payload)'
        : method === 'discard_source_json'
        ? 'browser_workspace.discard_source_json(json.loads(browser_source_payload))'
        : 'browser_workspace.promote_source_json(browser_source_payload)';
    const envelope = JSON.parse(python.runPython(expression));
    if (!envelope.ok) throw Object.assign(new Error(envelope.error.message), envelope.error);
    return envelope.result;
  } finally {
    python.runPython('del browser_source_payload, browser_source_path, browser_source_recovery, browser_source_progress, browser_source_cancelled');
  }
}

const sourceCancelled = () => Object.assign(new Error('The scan import was cancelled.'), { code: 'source_cancelled', status: 409 });
const yieldMessages = () => new Promise(resolve => setTimeout(resolve, 0));

async function prepareSource(request) {
  const { id, payload, generation } = request;
  const control = activeSource;
  if (!control || control.id !== id) throw new Error('Missing source operation owner.');
  const check = () => { if (control.cancelled || generation < minimumGeneration) throw sourceCancelled(); };
  const progress = value => postMessage({ kind: 'source-progress', id, generation,
    operationId: payload.operationId, progress: value });
  let path, stream, candidate = false;
  const cleanupStaging = () => {
    let cause;
    const ownedStream = stream, ownedPath = path;
    stream = undefined; path = undefined;
    try { if (ownedStream) python.FS.close(ownedStream); } catch (error) { cause = error; }
    try {
      if (ownedPath) python.FS.unlink(ownedPath);
      sourceTransferMetrics.staging_bytes = 0;
    } catch (error) { cause ??= error; }
    if (cause) {
      sourcePoisoned = true;
      throw Object.assign(new Error(`Source staging could not be released. Use Force and restart: ${cause.message}`),
        { code: 'source_cleanup_failed', status: 500 });
    }
  };
  try {
    const limits = manifest.worker_protocol?.source_import?.limits;
    if (!manifest.worker_protocol?.source_import?.supported || !limits)
      throw Object.assign(new Error('This runtime does not support source import.'), { code: 'unsupported_capability' });
    check();
    const { file: ignoredFile, recovery, ...sourcePayload } = payload;
    void ignoredFile;
    const virtualChange = ['create-virtual-scan', 'activate-scan'].includes(payload.purpose);
    let uploadRequired = payload.kind === 'scan' && !virtualChange;
    if (virtualChange && (manifest.worker_protocol?.version !== 11
      || manifest.worker_protocol?.virtual_scans?.supported !== true))
      throw Object.assign(new Error('This runtime does not support virtual scans.'), { code: 'unsupported_capability' });
    if (virtualChange && (payload.file !== undefined || recovery !== undefined))
      throw Object.assign(new Error('Virtual scan changes do not accept file uploads.'), { code: 'unexpected_source' });
    if (payload.purpose === 'replace-scan' &&
      (![9, 10, 11].includes(manifest.worker_protocol?.version) || manifest.worker_protocol?.replace_scan?.supported !== true))
      throw Object.assign(new Error('This runtime does not support scan replacement.'), { code: 'unsupported_capability' });
    if (payload.purpose === 'open-actions') {
      if (manifest.worker_protocol?.source_actions?.supported !== true)
        throw Object.assign(new Error('This runtime does not support source actions.'), { code: 'unsupported_capability' });
      const preflight = sourceCall('preflight_actions_json', sourcePayload);
      uploadRequired = preflight.uploadRequired;
      if (typeof uploadRequired !== 'boolean' || uploadRequired !== (payload.file !== undefined))
        throw Object.assign(new Error('Source upload does not match the saved actions.'), { code: 'unexpected_source' });
      if (uploadRequired && payload.file?.size !== preflight.descriptor?.source_bytes)
        throw Object.assign(new Error('Located file length differs from the saved source.'), { code: 'source_mismatch', status: 422 });
      if (preflight.requiresCad) await loadCad({ workspace: true });
      check();
    }
    if (uploadRequired) {
      const file = payload.file;
      if (!file || !Number.isSafeInteger(file.size) || file.size <= 0 || typeof file.slice !== 'function')
        throw Object.assign(new Error('Invalid scan file.'), { code: 'invalid_source', status: 422 });
      if (file.size > limits.max_source_bytes)
        throw Object.assign(new Error('Scan exceeds the source byte limit.'), { code: 'source_limit', status: 422 });
      python.FS.mkdirTree('/scansor-source-staging');
      path = `/scansor-source-staging/${id}.ply`;
      stream = python.FS.open(path, 'w');
      for (let offset = 0; offset < file.size;) {
        check();
        const end = Math.min(file.size, offset + limits.chunk_bytes);
        const chunk = new Uint8Array(await file.slice(offset, end).arrayBuffer());
        check();
        if (chunk.byteLength !== end - offset) throw new Error('Scan file changed while reading.');
        python.FS.write(stream, chunk, 0, chunk.byteLength, offset);
        offset = end;
        sourceTransferMetrics.staging_bytes = offset;
        sourceTransferMetrics.max_staging_bytes = Math.max(sourceTransferMetrics.max_staging_bytes, offset);
        sourceTransferMetrics.largest_chunk_bytes = Math.max(sourceTransferMetrics.largest_chunk_bytes, chunk.byteLength);
        progress({ phase: 'reading', completed: offset, total: file.size, unit: 'bytes' });
        await yieldMessages();
      }
      const ownedStream = stream;
      stream = undefined;
      try { python.FS.close(ownedStream); }
      catch (cause) {
        sourcePoisoned = true;
        throw Object.assign(new Error(`Source staging could not close. Use Force and restart: ${cause.message}`),
          { code: 'source_cleanup_failed', status: 500 });
      }
    }
    check();
    if (payload.recovery?.recipe?.nodes?.some(node => cadActions.has(node.operation))) await loadCad({ workspace: true });
    check();
    let prepared;
    try {
      prepared = sourceCall('prepare_source_json', sourcePayload, { path, recovery,
        progress: json => {
          const update = JSON.parse(json);
          progress({ ...update, unit: update.phase === 'reading' ? 'bytes'
            : ['validation', 'vertices', 'faces', 'prepared'].includes(update.phase) ? 'rows' : 'steps' });
        }, cancelled: () => control.cancelled });
      candidate = true;
      prepared.metadata.capabilities = { ...prepared.metadata.capabilities,
        source_import: manifest.worker_protocol.source_import,
        source_actions: manifest.worker_protocol.source_actions };
    } finally {
      cleanupStaging();
    }
    // Permit non-isolated Cancel posted during synchronous Python preparation to
    // be observed before publishing even the private candidate's display bundle.
    await yieldMessages();
    check();
    const positions = ownedBuffer('candidate_display_positions'), indices = ownedBuffer('candidate_display_indices');
    sourceTransferMetrics.display_copy_bytes = positions.byteLength + indices.byteLength;
    const decision = await new Promise(resolve => {
      sourceBoundary = { id, generation, operationId: payload.operationId,
        workspaceGeneration: prepared.workspaceGeneration, resolve };
      postMessage({ id, generation, ok: true, kind: 'source-candidate', result: { ...prepared, positions, indices } }, [positions, indices]);
      postMessage({ id, kind: 'transfer-confirmed', positions_bytes_after: positions.byteLength,
        indices_bytes_after: indices.byteLength });
    });
    sourceBoundary = undefined;
    if (!decision) throw sourceCancelled();
    // Host adoption has already occurred. A later Cancel cannot reverse it.
    sourceCall('promote_source_json', { operationId: payload.operationId, workspaceGeneration: prepared.workspaceGeneration });
    candidate = false;
    workspaceHasCad = prepared.graph.recipe.nodes.some(node => cadActions.has(node.operation));
    postMessage({ id, kind: 'source-promoted', operationId: payload.operationId,
      workspaceGeneration: prepared.workspaceGeneration });
  } finally {
    try {
      if (stream || path) cleanupStaging();
    } finally {
      try { if (candidate) sourceCall('discard_source_json', payload.operationId); }
      finally { if (activeSource === control) activeSource = undefined; }
    }
  }
}

function workspaceCall(method, operation, payload) {
  ensureWorkspace();
  python.globals.set('browser_operation', operation);
  python.globals.set('browser_payload', JSON.stringify(payload || {}));
  python.globals.set('browser_known_recipe_token', payload?.token || null);
  const envelope = JSON.parse(python.runPython(method === 'finish_evaluation_json'
    ? 'browser_workspace.finish_evaluation_json(if_recipe_token=browser_known_recipe_token)'
    : method === 'prepare_evaluation_json'
    ? 'browser_workspace.prepare_evaluation_json(browser_operation, browser_payload, if_recipe_token=browser_known_recipe_token)'
    : `browser_workspace.${method}(browser_operation, browser_payload)`));
  if (!envelope.ok) {
    const error = new Error(envelope.error.message);
    Object.assign(error, envelope.error);
    throw error;
  }
  const recipe = envelope.result?.recipe || envelope.result?.state?.recipe;
  if (recipe) workspaceHasCad = recipe.nodes.some(node => cadActions.has(node.operation));
  return envelope.result;
}

function ownedBuffer(name) {
  const proxy = python.runPython(`browser_workspace.${name}`);
  let view;
  try {
    view = proxy.getBuffer('u8');
    // Wasm owns view.data.buffer. Copy only this contiguous derivative, then transfer
    // the independently owned JS buffer; never detach the Python runtime's memory.
    return new Uint8Array(view.data).buffer;
  } finally { view?.release(); proxy.destroy(); }
}

async function loadCad({ workspace = false } = {}) {
  if (!manifest.cad) {
    const error = new Error('CAD assets are not included. Build the browser runtime with --cad.');
    error.code = 'unsupported_capability';
    throw error;
  }
  cadInitialization ??= (async () => {
    phase('optional CAD asset verification and extraction');
    const wheel = manifest.assets.find(r => r.group === 'cad');
    if (!wheel) throw new Error('Missing CAD wheel record');
    const site = python.runPython('import site; site.getsitepackages()[0]');
    python.unpackArchive(await checkedFetch(wheel, base), 'zip', { extractDir: site });
    phase('CAD kernel');
    const version = python.runPython('import OCP; OCP.__version__');
    if (version !== manifest.versions.ocp) throw new Error(`Incompatible OCP: ${version}`);
    cadLoaded = true;
  })().catch(cause => {
    const error = new Error(`CAD tools could not load: ${cause.message}. Numerical work remains available; reload after fixing the CAD assets.`);
    error.code = 'cad_startup_failed';
    throw error;
  });
  await cadInitialization;
  if (workspace && !workspaceCadEnabled) {
    ensureWorkspace();
    python.runPython('browser_workspace.enable_cad()');
    workspaceCadEnabled = true;
  }
}

async function handle({ operation }) {
  // A failed initialization stays failed; never reuse a partially loaded runtime.
  initialization ??= initialize();
  await initialization;
  if (operation === 'initialize') {
    const graph = workspaceCall('call_json', 'getGraph');
    python.runPython('browser_workspace.checkpoint_operation()');
    return { ready: true, versions: manifest.versions,
      source_revision: manifest.source_revision,
      source_worktree_dirty: manifest.source_worktree_dirty,
      worker_protocol: manifest.worker_protocol,
      source_import: manifest.worker_protocol?.source_import,
      source_actions: manifest.worker_protocol?.source_actions,
      source_mode: JSON.parse(python.runPython('json.dumps(browser_workspace.source_mode)')),
      source_identity: JSON.parse(python.runPython('json.dumps(browser_workspace.source_identity)')), graph };
  }
  if (operation === 'numeric') {
    if (cadLoaded) throw new Error('Run numerical startup in a fresh worker before importing CAD');
    phase('synthetic fitting, retained nozzle and graph replay');
    const result = JSON.parse(python.runPython("from experiments.browser_runtime.probe import browser; browser()"));
    numericComplete = !Object.hasOwn(result, 'parity_failure');
    return { result, source_revision: manifest.source_revision, source_worktree_dirty: manifest.source_worktree_dirty };
  }
  if (operation === 'cad') {
    if (!numericComplete) throw new Error('Numerical parity must succeed before the optional CAD smoke test');
    await loadCad();
    phase('Scansor CAD, tessellation and named STEP smoke test');
    const result = JSON.parse(python.runPython("from experiments.browser_runtime.probe import browser_cad; browser_cad()"));
    if (result.version !== manifest.versions.ocp) throw new Error(`Incompatible OCP: ${result.version}`);
    return { result };
  }
  throw new Error(`Unknown probe operation: ${operation}`);
}

async function dispatch(request) {
  const { id, operation, payload, generation } = request;
  if (['initialize', 'numeric', 'cad'].includes(operation)) {
    postMessage({ id, ok: true, ...await handle(request) });
    return;
  }
  initialization ??= initialize();
  await initialization;
  if (generation < minimumGeneration) return;
  if (operation === 'cancel') {
    // The coordinator already fenced all replies. Reconstruct only at a safe
    // boundary, after native cleanup, and before allowing the next operation.
    if (interruptBuffer) { Atomics.store(interruptBuffer, 0, 0); Atomics.store(interruptBuffer, 1, 0); }
    ensureWorkspace();
    python.globals.set('browser_committed_recipe', JSON.stringify(payload.recipe));
    const envelope = JSON.parse(python.runPython('browser_workspace.restore_cancelled_json(browser_committed_recipe)'));
    if (!envelope.ok) throw Object.assign(new Error(envelope.error.message), envelope.error);
    workspaceHasCad = envelope.result.recipe.nodes.some(node => cadActions.has(node.operation));
    python.runPython('browser_workspace.checkpoint_operation()');
    postMessage({ id, ok: true, result: envelope.result });
    return;
  }
  if (operation === 'cad-regressions') {
    await loadCad();
    const result = JSON.parse(python.runPython('from experiments.browser_runtime.cad_cases import run_cases; json.dumps(run_cases(), allow_nan=False)'));
    postMessage({ id, ok: true, result });
    return;
  }
  if (selectionVolumeOperations.has(operation)) {
    const profile = manifest.worker_protocol?.selection_volumes;
    if (![10, 11].includes(manifest.worker_protocol?.version) || !profile || Object.keys(profile).length !== 2 ||
      profile.version !== 1 || profile.max_preview_cells !== 2048)
      throw Object.assign(new Error('This runtime does not support selection volumes.'), { code: 'unsupported_capability' });
  }
  ensureWorkspace();
  if (request.workspaceGeneration !== undefined
    && request.workspaceGeneration !== python.runPython('browser_workspace.workspace_generation'))
    throw Object.assign(new Error('The accepted workspace changed.'), { code: 'stale_source', status: 409 });
  if (operation === 'prepare-source') { await prepareSource(request); return; }
  if (operation === 'inspect-actions') {
    if (manifest.worker_protocol?.source_actions?.supported !== true)
      throw Object.assign(new Error('This runtime does not support source actions.'), { code: 'unsupported_capability' });
    postMessage({ id, ok: true, result: workspaceCall('call_json', 'inspectActions', payload) });
    return;
  }
  python.runPython('browser_workspace.checkpoint_operation()');
  if (cadOperations.has(operation) || (['replaceRecipe', 'importRecipe'].includes(operation) &&
    Array.isArray(payload?.recipe?.nodes) && payload.recipe.nodes.some(node => cadActions.has(node?.operation))))
    await loadCad({ workspace: true });
  if (generation < minimumGeneration) return;
  const interruptible = (['evaluateGraph', 'ensureGraph'].includes(operation) || selectionVolumeOperations.has(operation)) && !workspaceHasCad;
  // Main-thread lifecycle delivery can lag behind the worker. Eligibility must
  // be enforced here so a late numerical Cancel never injects SIGINT into CAD.
  python.setInterruptBuffer(interruptible ? interruptBuffer?.subarray(0, 1) : undefined);
  if (request.managed) lifecycle(request, 'running', interruptible);
  if (operation === 'loadWorkspace') {
    const metadata = workspaceCall('call_json', operation, payload);
    metadata.capabilities = { ...metadata.capabilities, source_import: manifest.worker_protocol?.source_import,
      source_actions: manifest.worker_protocol?.source_actions };
    const positions = ownedBuffer('display_positions'), indices = ownedBuffer('display_indices');
    postMessage({ id, ok: true, result: { metadata, positions, indices,
      generation: metadata.project_generation, graph: metadata.graph } }, [positions, indices]);
    postMessage({ id, kind: 'transfer-confirmed', positions_bytes_after: positions.byteLength,
      indices_bytes_after: indices.byteLength });
  } else if (operation === 'exportCad') {
    const descriptor = workspaceCall('call_json', operation, payload);
    let bytes;
    try {
      bytes = ownedBuffer('export_buffer');
      if (bytes.byteLength !== descriptor.bytes) throw new Error('Invalid export byte count');
    } finally { python.runPython('browser_workspace.clear_export()'); }
    postMessage({ id, ok: true, result: bytes }, [bytes]);
    postMessage({ id, kind: 'transfer-confirmed', export_bytes_after: bytes.byteLength });
  } else if (['evaluateGraph', 'ensureGraph'].includes(operation)) {
    const prepared = workspaceCall('prepare_evaluation_json', operation, payload);
    postMessage({ id, ok: true, result: prepared.state });
    if (prepared.run) {
      postMessage({ kind: 'graph', generation, state: prepared.state });
      // Keep the serial dispatch queue occupied until Python has finished. The
      // early acknowledgement permits UI observation without overlapping jobs.
      try {
        const state = workspaceCall('finish_evaluation_json', operation, payload);
        postMessage({ kind: 'graph', generation, state });
      } catch (error) {
        // The request was already acknowledged. A fatal message settles every
        // observer rather than leaving it waiting for a nonexistent terminal reply.
        if (cancellationError(error).code !== 'computation_cancelled') postMessage({ fatal: error.message });
      }
    }
  } else postMessage({ id, ok: true, result: workspaceCall('call_json', operation, payload) });
}

// Queue all requests; an async message handler alone would allow overlapping setup.
self.onmessage = event => {
  const request = event.data;
  if (request.operation === 'accept-source-boundary') {
    if (sourceBoundary?.id === request.id && sourceBoundary.generation === request.generation
      && sourceBoundary.operationId === request.operationId
      && sourceBoundary.workspaceGeneration === request.workspaceGeneration)
      sourceBoundary.resolve(request.accepted === true);
    return;
  }
  if (request.operation === 'cancel-source') {
    if (activeSource?.operationId === request.operationId) {
      activeSource.cancelled = true;
      sourceBoundary?.resolve(false);
    }
    return;
  }
  if (request.operation === 'accept-boundary') {
    if (boundary?.id === request.id && boundary.generation === request.generation) {
      try {
        if (request.accepted) python.runPython('browser_workspace.checkpoint_operation()');
      } catch (error) { postMessage({ fatal: error.message }); }
      finally { boundary.resolve(); boundary = undefined; }
    }
    return;
  }
  if (request.operation === 'initialize') {
    interruptBuffer = request.interruptBuffer;
    initialMode = request.payload?.initialMode || 'example';
    if (!['empty', 'example'].includes(initialMode)) {
      postMessage({ id: request.id, ok: false, error: { code: 'invalid_source', message: 'Invalid initial workspace mode.' } });
      return;
    }
  }
  if (request.operation === 'prepare-source') {
    if (sourcePoisoned) {
      postMessage({ id: request.id, ok: false, error: { code: 'source_cleanup_failed', status: 500,
        message: 'Source cleanup failed. Use Force and restart before importing again.' } });
      return;
    }
    if (activeSource) {
      postMessage({ id: request.id, ok: false, error: { code: 'source_busy', status: 409, message: 'A source import is active.' } });
      return;
    }
    activeSource = { id: request.id, operationId: request.payload?.operationId, cancelled: false };
  }
  if (request.operation === 'cancel') {
    minimumGeneration = Math.max(minimumGeneration, request.generation);
    // Receiving this event means synchronous Python/native work has returned.
    if (interruptBuffer) Atomics.store(interruptBuffer, 0, 0);
    boundary?.resolve();
    boundary = undefined;
    if (activeSource) activeSource.cancelled = true;
    sourceBoundary?.resolve(false);
  } else if (request.managed && !['initialize', 'inspect-actions'].includes(request.operation)) lifecycle(request, 'queued');
  chain = chain.then(async () => {
    // Yield between serialized requests so Cancel can fence queued work before
    // the next synchronous Python call. This is not a checkpoint inside OCCT.
    await new Promise(resolve => setTimeout(resolve, 0));
    if (request.generation < minimumGeneration) return;
    try { await dispatch(request); }
    catch (cause) {
      const error = sourcePoisoned && request.operation === 'prepare-source'
        ? Object.assign(new Error(`Source cleanup failed. Use Force and restart: ${cause.message}`),
          { code: 'source_cleanup_failed', status: 500 }) : cancellationError(cause);
      postMessage({ id: request.id, ok: false,
        error: { code: error.code || 'worker_error', status: error.status, message: error.message } });
      if (sourcePoisoned) postMessage({ fatal: 'Source staging cleanup failed. Use Force and restart.' });
    }
    finally {
      if (activeSource?.id === request.id) activeSource = undefined;
      python?.setInterruptBuffer(undefined);
      if (interruptBuffer) Atomics.store(interruptBuffer, 0, 0);
      if (request.managed && !['initialize', 'cancel', 'prepare-source', 'inspect-actions'].includes(request.operation)) {
        // Wait for coordinator acceptance before the next operation can replace
        // the failure checkpoint. Cancel releases this boundary without adopting
        // failed/ready work whose publication was fenced on the main thread.
        await new Promise(resolve => {
          boundary = { id: request.id, generation: request.generation, resolve };
          postMessage({ kind: 'lifecycle', id: request.id, operation: request.operation,
            generation: request.generation, state: 'complete', boundary: true });
        });
      } else if (request.managed && !['initialize', 'inspect-actions'].includes(request.operation)) lifecycle(request, 'complete');
    }
  });
};
self.onmessageerror = () => postMessage({ fatal: 'Worker could not deserialize a request' });
