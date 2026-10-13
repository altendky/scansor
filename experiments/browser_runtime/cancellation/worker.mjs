import { checkedFetch } from '../artifact/integrity.mjs';

const base = new URL('../artifact/', import.meta.url);
const start = performance.now();
const emit = data => postMessage({ ...data, worker_ms: performance.now() - start });
let python;
let interrupt;
let cancelled = false;
let active = false;
let initialization;

async function initialize(probeRecord) {
  emit({ type: 'phase', phase: 'manifest' });
  const response = await fetch(new URL('manifest.json', base));
  if (!response.ok) throw new Error(`Manifest HTTP ${response.status}`);
  const manifest = await response.json();
  if (manifest.schema_version !== 1 || !manifest.cad) throw new Error('Build a schema-1 CAD artifact');
  const record = path => {
    const found = manifest.assets.find(asset => asset.path === path);
    if (!found) throw new Error(`Missing asset record: ${path}`);
    return found;
  };
  emit({ type: 'phase', phase: 'numeric asset verification' });
  for (const asset of manifest.assets.filter(asset => asset.group === 'numeric')) await checkedFetch(asset, base);
  emit({ type: 'phase', phase: 'Python runtime' });
  const { loadPyodide } = await import(new URL('runtime/pyodide.mjs', base));
  python = await loadPyodide({ indexURL: new URL('runtime/', base).href });
  if (python.version !== manifest.versions.pyodide) throw new Error(`Unexpected Pyodide ${python.version}`);
  emit({ type: 'phase', phase: 'NumPy and Pydantic' });
  await python.loadPackage(['numpy', 'pydantic']);
  emit({ type: 'phase', phase: 'Scansor source and frozen fixture' });
  python.unpackArchive(await checkedFetch(record('scansor.zip'), base), 'zip', { extractDir: '/app' });
  for (const path of ['fixture.npz', 'native-results.json']) python.FS.writeFile(`/${path}`, new Uint8Array(await checkedFetch(record(path), base)));
  python.runPython("import sys; sys.path.insert(0, '/app')");
  emit({ type: 'phase', phase: 'CAD verification and extraction' });
  const wheel = manifest.assets.find(asset => asset.group === 'cad');
  if (!wheel) throw new Error('Missing CAD wheel');
  const site = python.runPython('import site; site.getsitepackages()[0]');
  python.unpackArchive(await checkedFetch(wheel, base), 'zip', { extractDir: site });
  emit({ type: 'phase', phase: 'fixture allocation and imports' });
  const code = new TextDecoder().decode(await checkedFetch(probeRecord, new URL('./', import.meta.url)));
  python.runPython(code);
  const versions = JSON.parse(python.runPython('json.dumps(versions)'));
  for (const key of ['python', 'numpy', 'pydantic', 'ocp']) if (versions[key] !== manifest.versions[key]) throw new Error(`Unexpected ${key}: ${versions[key]}`);
  emit({ type: 'ready', ready_ms: performance.now() - start, versions, isolated: crossOriginIsolated,
    source_revision: manifest.source_revision, source_worktree_dirty: manifest.source_worktree_dirty,
    state: JSON.parse(python.runPython('json.dumps(state())')) });
}

self.onmessage = async ({ data }) => {
  if (data.type === 'initialize') {
    initialization ??= initialize(data.probe_record);
    try { await initialization; } catch (error) { emit({ type: 'fatal', error: String(error), stack: error.stack }); }
    return;
  }
  if (data.type === 'configure') {
    interrupt = data.interrupt;
    python.setInterruptBuffer(interrupt);
    emit({ type: 'configured' });
    return;
  }
  if (data.type === 'cancel') { cancelled = true; emit({ type: 'cancel-received', id: data.id }); return; }
  if (data.type === 'ping') { emit({ type: 'pong', id: data.id }); return; }
  if (data.type !== 'run') return;
  if (active) { emit({ type: 'result', id: data.id, status: 'error', error: 'Probe allows one active computation' }); return; }
  active = true;
  cancelled = false;
  if (interrupt) Atomics.store(interrupt, 0, 0);
  python.globals.set('job', data.job);
  python.globals.set('count', data.count);
  emit({ type: 'started', id: data.id, job: data.job });
  try {
    if (data.job === 'cooperative') {
      let completed = 0;
      while (completed < data.count) {
        // Do not retain a PyProxy for the discarded dictionary result.
        python.runPython('small_fit(); None');
        completed++;
        emit({ type: 'checkpoint', id: data.id, completed });
        await new Promise(resolve => setTimeout(resolve, 0));
        if (cancelled) break;
      }
      emit({ type: 'result', id: data.id, status: cancelled ? 'cancelled' : 'success', completed,
        state: JSON.parse(python.runPython('json.dumps(state())')) });
    } else {
      // These jobs contain no Python await. Synchronous execution also keeps
      // KeyboardInterrupt out of the asyncio webloop's unhandled-error channel.
      const result = JSON.parse(python.runPython('json.dumps(run_job(job,count))'));
      // Deliberately report raw computation outcome. The page separately marks
      // cancellation/publication fencing so the ordinary-message race is visible.
      emit({ type: 'result', id: data.id, status: 'success', result,
        state: JSON.parse(python.runPython('json.dumps(state())')) });
    }
  } catch (error) {
    if (interrupt) Atomics.store(interrupt, 0, 0);
    emit({ type: 'result', id: data.id, status: 'error', error: String(error),
      state: JSON.parse(python.runPython('json.dumps(state())')) });
  } finally { active = false; }
};
self.onmessageerror = () => emit({ type: 'fatal', error: 'Worker request could not be deserialized' });
