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
let chain = Promise.resolve();

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

function workspaceCall(method, operation, payload) {
  if (!workspaceLoaded) {
    python.runPython("from pathlib import Path; from experiments.browser_runtime.workspace import BrowserWorkspace; browser_workspace = BrowserWorkspace(Path('/app/examples/nozzle-bayonette-simplified'))");
    workspaceLoaded = true;
  }
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
  return envelope.result;
}

function displayBuffer(name) {
  const proxy = python.runPython(`browser_workspace.${name}`);
  let view;
  try {
    view = proxy.getBuffer('u8');
    // Wasm owns view.data.buffer. Copy only this contiguous derivative, then transfer
    // the independently owned JS buffer; never detach the Python runtime's memory.
    return new Uint8Array(view.data).buffer;
  } finally { view?.release(); proxy.destroy(); }
}

async function handle({ operation }) {
  // A failed initialization stays failed; never reuse a partially loaded runtime.
  initialization ??= initialize();
  await initialization;
  if (operation === 'initialize') {
    return { ready: true, versions: manifest.versions,
      source_revision: manifest.source_revision,
      source_worktree_dirty: manifest.source_worktree_dirty };
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
    if (!manifest.cad) throw new Error('CAD assets were not included; rebuild with --cad');
    if (!cadLoaded) {
      phase('optional CAD asset verification and extraction');
      const wheel = manifest.assets.find(r => r.group === 'cad');
      if (!wheel) throw new Error('Missing CAD wheel record');
      const site = python.runPython('import site; site.getsitepackages()[0]');
      python.unpackArchive(await checkedFetch(wheel, base), 'zip', { extractDir: site });
      cadLoaded = true;
    }
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
  if (operation === 'loadWorkspace') {
    const metadata = workspaceCall('call_json', operation, payload);
    const positions = displayBuffer('display_positions'), indices = displayBuffer('display_indices');
    postMessage({ id, ok: true, result: { metadata, positions, indices } }, [positions, indices]);
    postMessage({ id, kind: 'transfer-confirmed', positions_bytes_after: positions.byteLength,
      indices_bytes_after: indices.byteLength });
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
        postMessage({ fatal: error.message });
      }
    }
  } else postMessage({ id, ok: true, result: workspaceCall('call_json', operation, payload) });
}

// Queue all requests; an async message handler alone would allow overlapping setup.
self.onmessage = event => {
  const request = event.data;
  chain = chain.then(async () => {
    try { await dispatch(request); }
    catch (error) { postMessage({ id: request.id, ok: false,
      error: { code: error.code || 'worker_error', status: error.status, message: error.message } }); }
  });
};
self.onmessageerror = () => postMessage({ fatal: 'Worker could not deserialize a request' });
