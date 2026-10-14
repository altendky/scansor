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
const cadActions = new Set(['surface_intersection', 'trimmed_face', 'arranged_face', 'build_faces', 'body']);
let chain = Promise.resolve();
let interruptBuffer;
let minimumGeneration = 0;
let boundary;
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
    python.runPython("from pathlib import Path; from experiments.browser_runtime.workspace import BrowserWorkspace; browser_workspace = BrowserWorkspace(Path('/app/examples/nozzle-bayonette-simplified'))");
    workspaceLoaded = true;
  }
}

function workspaceCall(method, operation, payload) {
  ensureWorkspace();
  python.globals.set('browser_operation', operation);
  python.globals.set('browser_payload', JSON.stringify(payload || {}));
  const envelope = JSON.parse(python.runPython(method === 'finish_evaluation_json'
    ? 'browser_workspace.finish_evaluation_json()'
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
      source_identity: graph.recipe.nodes.find(node => node.operation === 'source'), graph };
  }
  if (operation === 'numeric') {
    if (cadLoaded) throw new Error('Run numerical startup in a fresh worker before importing CAD');
    phase('synthetic fitting, retained nozzle and graph replay');
    const result = JSON.parse(python.runPython("from experiments.browser_runtime.probe import browser; browser()"));
    numericComplete = true;
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
  ensureWorkspace();
  python.runPython('browser_workspace.checkpoint_operation()');
  if (cadOperations.has(operation) || (['replaceRecipe', 'importRecipe'].includes(operation) &&
    Array.isArray(payload?.recipe?.nodes) && payload.recipe.nodes.some(node => cadActions.has(node?.operation))))
    await loadCad({ workspace: true });
  if (generation < minimumGeneration) return;
  const interruptible = ['evaluateGraph', 'ensureGraph'].includes(operation) && !workspaceHasCad;
  // Main-thread lifecycle delivery can lag behind the worker. Eligibility must
  // be enforced here so a late numerical Cancel never injects SIGINT into CAD.
  python.setInterruptBuffer(interruptible ? interruptBuffer?.subarray(0, 1) : undefined);
  if (request.managed) lifecycle(request, 'running', interruptible);
  if (operation === 'loadWorkspace') {
    const metadata = workspaceCall('call_json', operation, payload);
    const positions = ownedBuffer('display_positions'), indices = ownedBuffer('display_indices');
    postMessage({ id, ok: true, result: { metadata, positions, indices } }, [positions, indices]);
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
  if (request.operation === 'accept-boundary') {
    if (boundary?.id === request.id && boundary.generation === request.generation) {
      try {
        if (request.accepted) python.runPython('browser_workspace.checkpoint_operation()');
      } catch (error) { postMessage({ fatal: error.message }); }
      finally { boundary.resolve(); boundary = undefined; }
    }
    return;
  }
  if (request.operation === 'initialize') interruptBuffer = request.interruptBuffer;
  if (request.operation === 'cancel') {
    minimumGeneration = Math.max(minimumGeneration, request.generation);
    // Receiving this event means synchronous Python/native work has returned.
    if (interruptBuffer) Atomics.store(interruptBuffer, 0, 0);
    boundary?.resolve();
    boundary = undefined;
  } else if (request.managed && request.operation !== 'initialize') lifecycle(request, 'queued');
  chain = chain.then(async () => {
    // Yield between serialized requests so Cancel can fence queued work before
    // the next synchronous Python call. This is not a checkpoint inside OCCT.
    await new Promise(resolve => setTimeout(resolve, 0));
    if (request.generation < minimumGeneration) return;
    try { await dispatch(request); }
    catch (cause) {
      const error = cancellationError(cause);
      postMessage({ id: request.id, ok: false,
        error: { code: error.code || 'worker_error', status: error.status, message: error.message } });
    }
    finally {
      python?.setInterruptBuffer(undefined);
      if (interruptBuffer) Atomics.store(interruptBuffer, 0, 0);
      if (request.managed && !['initialize', 'cancel'].includes(request.operation)) {
        // Wait for coordinator acceptance before the next operation can replace
        // the failure checkpoint. Cancel releases this boundary without adopting
        // failed/ready work whose publication was fenced on the main thread.
        await new Promise(resolve => {
          boundary = { id: request.id, generation: request.generation, resolve };
          postMessage({ kind: 'lifecycle', id: request.id, operation: request.operation,
            generation: request.generation, state: 'complete', boundary: true });
        });
      } else if (request.managed && request.operation !== 'initialize') lifecycle(request, 'complete');
    }
  });
};
self.onmessageerror = () => postMessage({ fatal: 'Worker could not deserialize a request' });
