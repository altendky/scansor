import test from 'node:test';
import assert from 'node:assert/strict';
import { createBackend, getBackend } from './backend.js';
import { BackendError, createHttpBackend } from './http-backend.js';
import { createWorkerBackend } from './worker-backend.js';
import { ensureGraphCurrent } from './graph-evaluation.js';

test('HTTP mutations preserve payloads, headers, errors and request identities', async () => {
  const calls = [], payload = { token: 'recipe', recipe: { nodes: [] } };
  const backend = createBackend({ fetch: async (path, init) => {
    calls.push({ path, init });
    return calls.length === 1 ? Response.json(payload)
      : Response.json({ error: 'Actions changed' }, { status: 409 });
  } });
  assert.deepEqual(await backend.replaceRecipe(payload), payload);
  assert.equal(calls[0].path, '/api/graph');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['X-Scansor-Request'], '1');
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].init.body), payload);
  await assert.rejects(backend.importRecipe(payload), error => {
    assert(error instanceof BackendError);
    assert.equal(error.message, 'Actions changed');
    assert.equal(error.status, 409);
    assert.equal(error.code, 'http_error');
    assert.equal(error.requestId, 2);
    return true;
  });
  assert.equal(backend.capabilities.cancelComputation, false);
  await assert.rejects(backend.cancelComputation(), error => error.code === 'unsupported_capability');
  await assert.rejects(backend.forceRestart(), error => error.code === 'unsupported_capability');
});

test('HTTP owned volume methods use negotiated bounded routes and retain source generation fences', async () => {
  const calls = [], profile = { version: 1, max_preview_cells: 2048 },
    methods = { authorSelectionVolume: 'author', setSelectionVolumeOptions: 'options', chooseSelectionVolume: 'choose',
      getSelectionVolumeChoices: 'choices', getSelectionVolumePreview: 'preview', deleteSelectionVolume: 'delete' };
  const backend = createBackend({ fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json({ project_generation: 12, selection_volumes: profile,
      graph: { token: 'original', revision: 1, project_generation: 12, states: {}, recipe: { nodes: [] } },
      positions: { url: '/positions' }, indices: { url: '/indices' } });
    if (path === '/positions' || path === '/indices') return new Response(new ArrayBuffer(0),
      { headers: { 'X-Scansor-Generation': '12' } });
    return Response.json({ token: 'current', path });
  } });
  await assert.rejects(backend.authorSelectionVolume({ token: 'original' }), error => error.code === 'unsupported_capability');
  assert.equal(calls.length, 0);
  await backend.loadWorkspace();
  assert.equal(backend.capabilities.selectionVolumes, true);
  for (const [method, route] of Object.entries(methods)) {
    const payload = { token: 'original', reference: { selection: 'rim', entry: 'scan', volume: 'mesh' } };
    const result = await backend[method](payload), request = calls.at(-1);
    assert.equal(request.path, `/api/graph/selection-volumes/${route}`);
    assert.equal(request.init.headers['X-Scansor-Generation'], '12');
    assert.equal(request.init.headers['X-Scansor-Request'], '1');
    assert.deepEqual(JSON.parse(request.init.body), payload);
    assert.equal(result.path, request.path);
  }
  backend.close();
});

test('HTTP rejects malformed volume profiles before mutation and preserves structured stale errors', async () => {
  for (const selection_volumes of [undefined, { version: true, max_preview_cells: 2048 },
    { version: 1, max_preview_cells: 2049 }, { version: 1, max_preview_cells: 2048, extra: true }]) {
    const paths = [], backend = createHttpBackend({ fetch: async path => {
      paths.push(path);
      if (path === '/api/meta') return Response.json({ selection_volumes, positions: { url: '/p' }, indices: { url: '/i' } });
      return new Response(new ArrayBuffer(0));
    } });
    await backend.loadWorkspace();
    await assert.rejects(backend.deleteSelectionVolume({ token: 'known' }), error => error.code === 'unsupported_capability');
    assert.equal(paths.length, 3); backend.close();
  }
  const backend = createHttpBackend({ fetch: async path => path === '/api/meta'
    ? Response.json({ selection_volumes: { version: 1, max_preview_cells: 2048 }, positions: { url: '/p' }, indices: { url: '/i' } })
    : path === '/p' || path === '/i' ? new Response(new ArrayBuffer(0))
      : Response.json({ error: { code: 'stale_graph', message: 'Graph changed before volume operation' } }, { status: 409 }) });
  await backend.loadWorkspace();
  await assert.rejects(backend.getSelectionVolumeChoices({ token: 'old' }),
    error => error.code === 'stale_graph' && error.status === 409);
  backend.close();
});

test('workspace and CAD results expose owned buffers rather than URLs or HTTP responses', async () => {
  const positions = new Float32Array([1, 2, 3]), indices = new Uint32Array([0, 0, 0]),
    bytes = new Uint8Array([80, 75, 3, 4]);
  const backend = createHttpBackend({ fetch: async path => {
    if (path === '/api/meta') return Response.json({ session: { source_sha256: 'source' },
      positions: { url: '/mesh/positions', dtype: '<f4', components: 3 },
      indices: { url: '/mesh/indices', dtype: '<u4', components: 3 } });
    return new Response(path === '/mesh/positions' ? positions
      : path === '/mesh/indices' ? indices : bytes);
  } });
  const workspace = await backend.loadWorkspace();
  assert.deepEqual(workspace.metadata.positions, { dtype: '<f4', components: 3 });
  assert.deepEqual(workspace.metadata.indices, { dtype: '<u4', components: 3 });
  assert.deepEqual(new Float32Array(workspace.positions), positions);
  assert.deepEqual(new Uint32Array(workspace.indices), indices);
  assert.deepEqual(new Uint8Array(await backend.exportCad({ token: 'recipe' })), bytes);
});

test('binary failures preserve validation diagnostics and non-JSON failures remain explicit', async () => {
  const backend = createHttpBackend({ fetch: async path => path === '/api/export/cad'
    ? Response.json({ error: 'Face is open' }, { status: 400 })
    : new Response('Unavailable', { status: 503 }) });
  await assert.rejects(backend.exportCad({}), /Face is open/);
  await assert.rejects(backend.getGraph(), error => error.code === 'http_error' && error.status === 503);
});

test('HTTP observation reads even an idle cursor and yields compact terminal status', async () => {
  const paths = [], waits = [], replies = [
    { revision: 4, evaluation_running: true },
    { revision: 4, unchanged: true, evaluation_running: false, evaluation_error: 'Failure' },
  ];
  const backend = createHttpBackend({ fetch: async path => {
    paths.push(path); return Response.json(replies.shift());
  }, wait: async milliseconds => { waits.push(milliseconds); }, pollInterval: 17 });
  const updates = [];
  for await (const state of backend.graphUpdates()) updates.push(state);
  assert.deepEqual(paths, ['/api/graph', '/api/graph?revision=4']);
  assert.deepEqual(waits, [17]);
  assert.equal(updates.at(-1).evaluation_error, 'Failure');
  assert.equal(updates.at(-1).evaluation_running, false);
});

test('HTTP observation rejects mismatched compact revisions and propagates midstream failures', async () => {
  const bad = createHttpBackend({ fetch: async () => Response.json({ revision: 2, unchanged: true }) });
  await assert.rejects(bad.graphUpdates({ revision: 1 }).next(), /Invalid unchanged/);
  let reads = 0;
  const failed = createHttpBackend({ fetch: async () => {
    if (++reads === 2) throw new Error('Network failed');
    return Response.json({ revision: 1, evaluation_running: true });
  }, wait: async () => {} });
  const iterator = failed.graphUpdates();
  assert.equal((await iterator.next()).value.evaluation_running, true);
  await assert.rejects(iterator.next(), /Network failed/);
});

test('HTTP polling reuses the matching recipe and replaces it when the actions token changes', async () => {
  const original = { nodes: [{ id: 'selection', vertex_ids: [2, 7, 11], volume: { encoded: 'saved-volume' } }] };
  const replacement = { nodes: [{ id: 'replacement', vertex_ids: [3] }] };
  const paths = [], replies = [
    { token: 'original', revision: 1, recipe: original, states: { selection: 'error' },
      evaluation_error: 'Previous failure', status: 'running', evaluation_running: true },
    { token: 'original', revision: 2, states: { selection: 'ready' }, errors: {}, evaluation_running: true },
    { token: 'replacement', revision: 3, recipe: replacement,
      states: { replacement: 'ready' }, evaluation_running: false },
  ];
  const backend = createHttpBackend({ fetch: async path => {
    paths.push(path); return Response.json(replies.shift());
  }, wait: async () => {} });
  const updates = [];
  for await (const state of backend.graphUpdates()) updates.push(state);
  assert.deepEqual(paths, ['/api/graph', '/api/graph?revision=1&recipe_token=original',
    '/api/graph?revision=2&recipe_token=original']);
  assert.deepEqual(updates[1].recipe, original);
  assert.deepEqual(updates[1].states, { selection: 'ready' });
  assert.equal(Object.hasOwn(updates[1], 'evaluation_error'), false);
  assert.equal(Object.hasOwn(updates[1], 'status'), false);
  assert.deepEqual(updates[2].recipe, replacement);
  assert.deepEqual(await backend.exportRecipe(updates[1]), original);
  backend.close();
});

test('late HTTP reads cannot replace the recipe needed by newer polling', async () => {
  let releaseOld, releaseNew;
  const first = { token: 'old', revision: 1, recipe: { nodes: ['old'] }, states: {} };
  const next = { token: 'new', revision: 2, recipe: { nodes: ['new'] }, states: {} };
  let reads = 0;
  const backend = createHttpBackend({ fetch: (path, init) => {
    if (init.method === 'POST') return Response.json(next);
    if (++reads === 1) return Response.json(first);
    if (reads === 2) return new Promise(resolve => { releaseOld = resolve; });
    assert.match(path, /recipe_token=new/);
    return new Promise(resolve => { releaseNew = resolve; });
  } });
  await backend.getGraph();
  const older = backend.getGraph({ revision: 1 });
  await backend.replaceRecipe({ token: 'old', recipe: next.recipe });
  const newer = backend.getGraph({ revision: 2 });
  releaseOld(Response.json(first));
  await older;
  releaseNew(Response.json({ token: 'new', revision: 3, states: { new: 'ready' } }));
  assert.deepEqual((await newer).recipe, next.recipe);
  backend.close();
});

test('HTTP rejects recipe-less graph responses without a matching cached actions token', async () => {
  for (const cached of [false, true]) {
    const paths = [], replies = [
      ...(cached ? [{ token: 'original', revision: 1, recipe: { nodes: [{ id: 'source' }] } }] : []),
      { token: 'different', revision: 2, states: { source: 'ready' }, evaluation_running: false },
    ];
    const backend = createHttpBackend({ fetch: async path => {
      paths.push(path); return Response.json(replies.shift());
    } });
    if (cached) await backend.getGraph();
    await assert.rejects(backend.getGraph({ revision: 1 }), /Invalid recipe-less graph response/);
    assert.equal(paths.at(-1), cached ? '/api/graph?revision=1&recipe_token=original' : '/api/graph?revision=1');
    backend.close();
  }
});

test('aborted reads cannot yield obsolete responses even when fetch ignores its signal', async () => {
  const controller = new AbortController();
  const backend = createHttpBackend({ fetch: async () => {
    controller.abort(); return Response.json({ evaluation_running: false });
  } });
  await assert.rejects(backend.graphUpdates({ signal: controller.signal }).next(),
    error => error.name === 'AbortError');
});

test('closing a backend aborts an in-flight request and prevents later requests', async () => {
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  const backend = createHttpBackend({ fetch: (_path, { signal }) => new Promise((_resolve, reject) => {
    entered(); signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }) });
  const pending = backend.getGraph();
  await started;
  backend.close();
  await assert.rejects(pending, error => error.code === 'backend_closed');
  await assert.rejects(backend.getGraph(), error => error.code === 'backend_closed');
});

test('aborting observation during its pause ends it without another read', async () => {
  let reads = 0;
  const controller = new AbortController(), backend = createHttpBackend({
    fetch: async () => { reads++; return Response.json({ evaluation_running: true }); },
    pollInterval: 60_000,
  });
  const iterator = backend.graphUpdates({ signal: controller.signal });
  await iterator.next();
  const pending = iterator.next();
  controller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
  assert.equal(reads, 1);
});

test('unknown and incomplete backends fail without falling back to HTTP', () => {
  for (const type of ['worker', 'toString'])
    assert.throws(() => createBackend({ type }), error => error.code === 'unsupported_backend');
  assert.throws(() => createBackend({ type: 'memory', factories: { memory: () => ({}) } }),
    error => error.code === 'unsupported_operation');
});

test('unavailable capabilities reject operations without invoking another backend', async () => {
  const backend = createBackend({ type: 'numeric', factories: { numeric: () => ({
    ...createHttpBackend({ fetch: () => assert.fail('Unexpected fallback') }),
    capabilities: { numerical: true, cad: false, examples: false },
  }) } });
  await assert.rejects(backend.exportCad({}), error => error.code === 'unsupported_capability');
  await assert.rejects(backend.listExamples(), error => error.code === 'unsupported_capability');
});

test('factories can return class backends whose methods use private instance state', async () => {
  class MemoryBackend {
    #recipe = { nodes: [{ id: 'source' }] };
    async exportRecipe() { return this.#recipe; }
  }
  const implementation = new MemoryBackend();
  for (const [name, value] of Object.entries(createHttpBackend()))
    if (name !== 'exportRecipe') implementation[name] = value;
  const backend = createBackend({ type: 'memory', factories: { memory: () => implementation } });
  assert.deepEqual(await backend.exportRecipe(), { nodes: [{ id: 'source' }] });
});

test('an injected push backend satisfies readiness without HTTP or polling', async () => {
  const terminal = { token: 'recipe', revision: 2, evaluation_running: false,
    recipe: { nodes: [{ id: 'face', label: 'Face' }] }, states: { face: 'ready' }, errors: {} },
    accepted = [];
  let ensureCalls = 0, closed = false;
  const backend = createBackend({ type: 'push', factories: { push: () => ({
    ...createHttpBackend({ fetch: () => assert.fail('Unexpected HTTP request') }),
    ensureGraph: async ({ token, targets }) => {
      assert.equal(token, 'recipe'); assert.deepEqual(targets, ['face']);
      return ++ensureCalls === 1 ? { evaluation_running: true } : terminal;
    },
    async *graphUpdates() {
      try { yield { ...terminal, states: { face: 'running' }, evaluation_running: true }; yield terminal; }
      finally { closed = true; }
    },
  }) } });
  assert.equal(await ensureGraphCurrent({ backend, targets: ['face'], token: 'recipe',
    acceptState: state => accepted.push(state) }), terminal);
  assert.equal(ensureCalls, 2);
  assert.equal(accepted.length, 3);
  assert.equal(closed, true);
});

test('premature update stream completion cannot substitute cached ready geometry', async () => {
  const backend = { ensureGraph: async () => ({ evaluation_running: true }),
    async *graphUpdates() {} };
  await assert.rejects(ensureGraphCurrent({ backend, targets: ['face'], token: 'recipe',
    acceptState: () => {}, getState: () => ({ states: { face: 'ready' }, evaluation_running: false }) }),
  /Backend stopped/);
});

test('recipe download is an isolated snapshot and unavailable state is diagnosed', async () => {
  const backend = createHttpBackend(), state = { recipe: { nodes: [{ id: 'source' }] } };
  const recipe = await backend.exportRecipe(state);
  recipe.nodes[0].id = 'edited';
  assert.equal(state.recipe.nodes[0].id, 'source');
  await assert.rejects(backend.exportRecipe(null), /not finished loading/);
});

test('bootstrap and recovery share the first selected backend instance', () => {
  const backend = getBackend({ type: 'memory', factories: { memory: () => createHttpBackend({
    fetch: () => assert.fail('Unexpected HTTP request'),
  }) } });
  assert.equal(getBackend(), backend);
  assert.equal(getBackend({ type: 'missing' }), backend);
  backend.close();
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function sourceRecord(overrides = {}) {
  return { operationId: 'scan-1', generation: 0, token: 'empty', revision: 0,
    status: 'reserved', phase: 'reserved', completed: 0, total: 3, unit: 'bytes', terminal: false,
    ...overrides };
}

test('HTTP replacement is explicitly negotiated and preserves the existing import handshake', async () => {
  const profile = { supported: true, version: 1, alignment: 'already-aligned', source_kind: 'generic-scan' },
    calls = [], file = Object.assign(new Blob(['replacement']), { name: 'rescan.ply' });
  let accepted = false;
  const backend = createHttpBackend({ wait: async () => {}, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json({ ...metadata(accepted ? 2 : 1, accepted ? 'replacement' : 'original'), source_replacement: profile });
    if (path.startsWith('/mesh/')) return meshResponse(path, accepted ? 2 : 1);
    if (path === '/api/sources/imports') return Response.json(sourceRecord({ generation: 1, token: 'original', purpose: 'replace-scan' }));
    accepted = true;
    return Response.json(sourceRecord({ generation: 1, token: 'original', purpose: 'replace-scan', revision: 1,
      terminal: true, status: 'accepted', acceptedGeneration: 2 }));
  } });
  await backend.loadWorkspace();
  const bundle = await backend.replaceScan({ file, generation: 1, token: 'original' });
  assert.equal(bundle.generation, 2);
  const reserved = calls.find(call => call.path === '/api/sources/imports');
  assert.deepEqual(JSON.parse(reserved.init.body), { generation: 1, token: 'original', filename: 'rescan.ply', size: file.size, purpose: 'replace-scan' });
  assert.equal(calls.find(call => call.path.endsWith('/bytes')).init.body, file);
  assert.equal(backend.capabilities.sourceReplacement, true);
  backend.close();
});

test('HTTP replacement rejects undeclared profiles before reserving work', async () => {
  for (const profile of [undefined, { supported: false }, { supported: true, version: 1, alignment: 'automatic', source_kind: 'generic-scan' },
    { supported: true, version: 1, alignment: 'already-aligned', source_kind: 'generic-scan', extra: true }]) {
    const paths = [], backend = createHttpBackend({ fetch: async path => {
      paths.push(path);
      return path === '/api/meta' ? Response.json({ ...metadata(), source_replacement: profile }) : meshResponse(path, 0);
    } });
    await backend.loadWorkspace();
    await assert.rejects(backend.replaceScan({ file: new Blob(['scan']), generation: 0, token: 'empty' }), error => error.code === 'unsupported_capability');
    assert(!paths.includes('/api/sources/imports'));
    backend.close();
  }
  const backend = createBackend({ type: 'limited', factories: { limited: () => {
    const value = createHttpBackend(); value.capabilities = { ...value.capabilities, sourceReplacement: false }; return value;
  } } });
  await assert.rejects(backend.replaceScan({}), error => error.code === 'unsupported_capability');
  backend.close();
});

function metadata(generation = 0, token = 'empty', limit = 10_000) {
  return { project_generation: generation, source: generation ? { source_sha256: 'source' } : null,
    bounds: null, vertices: 0, triangles: 0,
    graph: { project_generation: generation, token, recipe: { nodes: [], output: null } },
    capabilities: { source_import: { supported: true, limits: { max_source_bytes: limit } } },
    positions: { url: `/mesh/positions?generation=${generation}`, dtype: '<f4', components: 3 },
    indices: { url: `/mesh/indices?generation=${generation}`, dtype: '<u4', components: 3 } };
}

function meshResponse(path, generation) {
  assert.match(path, new RegExp(`^/mesh/(positions|indices)\\?generation=${generation}$`));
  return new Response(new ArrayBuffer(0), { headers: { 'X-Scansor-Generation': String(generation) } });
}

function scanFile() { return Object.assign(new Blob(['ply']), { name: 'renamed.ply' }); }

test('source operations are optional and capability-gated for old factories and old worker declarations', async () => {
  const unavailable = [createBackend({ type: 'old', factories: { old: () => ({
    ...createHttpBackend({ fetch: () => assert.fail('Unexpected fallback') }),
    capabilities: { numerical: true, cad: false, examples: false },
  }) } }), createBackend({ type: 'worker', factories: { worker: () => {
    const backend = createWorkerBackend({ runtimeURL: '/worker.mjs',
      workerFactory: () => assert.fail('Unsupported source import started a worker') });
    backend.capabilities = { ...backend.capabilities, sourceImport: false };
    return backend;
  } } })];
  for (const backend of unavailable) {
    for (const method of ['addScan', 'getSourceOperation', 'cancelSourceOperation', 'resetProject'])
      await assert.rejects(backend[method]({}), error => error.code === 'unsupported_capability');
    backend.close();
  }
  assert.throws(() => createBackend({ type: 'incomplete', factories: { incomplete: () => {
    const backend = createHttpBackend(); delete backend.addScan; return backend;
  } } }), error => error.code === 'unsupported_operation');
});

test('coherent empty workspace exposes generation/graph and fences generic mutations with cached cursor', async () => {
  const calls = [], backend = createHttpBackend({ fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json(metadata());
    if (path.startsWith('/mesh/')) return meshResponse(path, 0);
    return Response.json({ project_generation: 0, token: 'edited' });
  } });
  const workspace = await backend.loadWorkspace({ generation: 0, token: 'empty' });
  assert.equal(workspace.generation, 0);
  assert.equal(workspace.graph.token, 'empty');
  assert.equal(workspace.metadata.source, null);
  assert.equal(workspace.positions.byteLength, 0);
  assert.equal(workspace.indices.byteLength, 0);
  assert.equal(workspace.metadata.positions.url, undefined);
  await backend.replaceRecipe({ token: 'empty', recipe: { nodes: [] } });
  assert.equal(calls.at(-1).init.headers['X-Scansor-Generation'], '0');
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { token: 'empty', recipe: { nodes: [] } });
});

test('workspace rejects mismatched metadata token, graph generation and binary generation without caching them', async () => {
  for (const mismatch of ['token', 'graph', 'positions', 'indices']) {
    const backend = createHttpBackend({ fetch: async path => {
      if (path === '/api/meta') {
        const value = metadata(2, 'current');
        if (mismatch === 'graph') value.graph.project_generation = 1;
        return Response.json(value);
      }
      if (path.includes(mismatch)) return new Response(new ArrayBuffer(0), { headers: { 'X-Scansor-Generation': '1' } });
      return meshResponse(path, 2);
    } });
    await assert.rejects(backend.loadWorkspace({ generation: 2, token: mismatch === 'token' ? 'old' : 'current' }),
      error => ['observation_superseded', 'invalid_workspace'].includes(error.code));
  }
});

test('add scan reserves JSON, sends original bulk bytes, observes progress and returns one accepted bundle', async () => {
  const file = scanFile(), calls = [], updates = [], upload = deferred();
  let generation = 0, polls = 0;
  const accepted = sourceRecord({ revision: 3, status: 'accepted', phase: 'complete', terminal: true, acceptedGeneration: 1, project_generation: 1 });
  const backend = createHttpBackend({ wait: async () => {}, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json(metadata(generation, generation ? 'scan' : 'empty'));
    if (path.startsWith('/mesh/')) return meshResponse(path, generation);
    if (path === '/api/sources/imports') return Response.json(sourceRecord());
    if (path.endsWith('/bytes')) {
      assert.equal(updates[0].operationId, 'scan-1');
      assert.equal(init.body, file);
      assert.equal(init.headers['Content-Type'], 'application/octet-stream');
      assert.equal(init.headers['Content-Length'], undefined);
      return upload.promise;
    }
    if (++polls === 1) return Response.json(sourceRecord({ revision: 1, phase: 'vertices', status: 'preparing' }));
    generation = 1;
    upload.resolve(Response.json(sourceRecord({ revision: 0 })));
    return Response.json(accepted);
  } });
  await backend.loadWorkspace();
  const result = await backend.addScan({ file, generation: 0, token: 'empty' }, { onProgress: value => updates.push(value) });
  assert.equal(result.generation, 1);
  assert.equal(result.graph.token, 'scan');
  assert.deepEqual(updates.map(value => value.revision), [0, 1, 3]);
  const reserve = calls.find(call => call.path === '/api/sources/imports');
  assert.deepEqual(JSON.parse(reserve.init.body), { generation: 0, token: 'empty', filename: 'renamed.ply', size: 3 });
  assert.equal(reserve.init.headers['X-Scansor-Request'], '1');
  updates[0].operationId = 'edited';
  assert.equal((await backend.exportRecipe({ recipe: result.graph.recipe })).nodes.length, 0);
});

test('client byte preflight uses the current server cap and invalid files reserve nothing', async () => {
  let reservations = 0;
  const backend = createHttpBackend({ fetch: async path => {
    if (path === '/api/meta') return Response.json(metadata(0, 'empty', 2));
    if (path.startsWith('/mesh/')) return meshResponse(path, 0);
    reservations++; assert.fail('Rejected source reached upload reservation');
  } });
  await backend.loadWorkspace();
  await assert.rejects(backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }), error => error.code === 'source_limit');
  await assert.rejects(backend.addScan({ file: new Blob(), generation: 0, token: 'empty' }), error => error.code === 'invalid_source');
  assert.equal(reservations, 0);
});

test('explicit Cancel has its own original cursor and terminal cancellation preserves import diagnostics', async () => {
  const calls = [], backend = createHttpBackend({ wait: async () => {}, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/sources/imports') return Response.json(sourceRecord());
    return Response.json(sourceRecord({ revision: 2, terminal: true, status: 'cancelled', error: { code: 'cancelled', message: 'Accepted work unchanged' } }));
  } });
  const progress = [];
  await assert.rejects(backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }, { onProgress: value => progress.push(value) }),
    error => error.code === 'cancelled' && error.message === 'Accepted work unchanged');
  await backend.cancelSourceOperation({ operationId: 'scan-1', generation: 0, token: 'empty' });
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { generation: 0, token: 'empty' });
  assert.equal(calls.at(-1).path, '/api/sources/imports/scan-1/cancel');
  assert.equal(backend.capabilities.cancelComputation, false);
  assert.equal(progress.at(-1).status, 'cancelled');
  await assert.rejects(backend.getSourceOperation({ operationId: '../file' }), error => error.code === 'invalid_operation');
});

test('lost upload reply queries the operation and cannot downgrade an accepted import', async () => {
  let accepted = false;
  const backend = createHttpBackend({ wait: async () => {}, fetch: async path => {
    if (path === '/api/sources/imports') return Response.json(sourceRecord());
    if (path.endsWith('/bytes')) throw new Error('Upload reply lost');
    if (path === '/api/sources/imports/scan-1') {
      accepted = true;
      return Response.json(sourceRecord({ revision: 2, terminal: true, status: 'accepted', acceptedGeneration: 1 }));
    }
    if (path === '/api/meta') return Response.json(metadata(1, 'scan'));
    return meshResponse(path, 1);
  } });
  assert.equal((await backend.addScan({ file: scanFile(), generation: 0, token: 'empty' })).generation, 1);
  assert.equal(accepted, true);
});

test('accepted import delivery failure retains accepted identity and never sends cancellation', async () => {
  const calls = [], backend = createHttpBackend({ fetch: async path => {
    calls.push(path);
    if (path === '/api/sources/imports') return Response.json(sourceRecord());
    if (path.endsWith('/bytes')) return Response.json(sourceRecord({ revision: 2, terminal: true, status: 'accepted', acceptedGeneration: 1 }));
    if (path === '/api/sources/imports/scan-1') return Response.json(sourceRecord({ revision: 2, terminal: true, status: 'accepted', acceptedGeneration: 1 }));
    if (path === '/api/meta') throw new Error('Display unavailable');
    assert.fail(`Unexpected ${path}`);
  }, wait: async () => {} });
  await assert.rejects(backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }), error => {
    assert.equal(error.code, 'accepted_workspace_unavailable');
    assert.equal(error.acceptedOperation.status, 'accepted');
    assert.equal(error.acceptedOperation.acceptedGeneration, 1);
    assert.equal(error.cause.code, 'transport_error');
    return true;
  });
  assert.equal(calls.some(path => path.endsWith('/cancel')), false);
});

test('Abort and close stop observations without invoking the explicit import Cancel operation', async () => {
  for (const mode of ['abort', 'close']) {
    const calls = [], started = deferred(), controller = new AbortController();
    const backend = createHttpBackend({ fetch: async (path, { signal }) => {
      calls.push(path);
      if (path === '/api/sources/imports') return Response.json(sourceRecord());
      started.resolve();
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
    } });
    const result = backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }, { signal: controller.signal });
    await started.promise;
    if (mode === 'abort') controller.abort(); else backend.close();
    await assert.rejects(result, error => mode === 'abort' ? error.name === 'AbortError' : error.code === 'backend_closed');
    assert.equal(calls.some(path => path.endsWith('/cancel')), false);
  }
});

test('source reset fences old graph and binary replies even when requests ignore abort', async () => {
  const staleGraph = deferred(), staleBinary = deferred();
  let generation = 0;
  const backend = createHttpBackend({ fetch: async (path, init) => {
    if (path === '/api/meta') return Response.json(metadata(generation));
    if (path.startsWith('/mesh/')) return meshResponse(path, generation);
    if (path === '/api/graph') return staleGraph.promise;
    if (path === '/api/export/cad') return staleBinary.promise;
    assert.equal(path, '/api/project/reset');
    assert.equal(init.headers['X-Scansor-Generation'], '0');
    assert.deepEqual(JSON.parse(init.body), { generation: 0, token: 'empty', confirmedDiscard: true });
    generation = 1;
    return Response.json({ project_generation: 1, graph: { token: 'empty' } });
  } });
  await backend.loadWorkspace();
  const graph = backend.getGraph(), binary = backend.exportCad({ token: 'empty' });
  assert.equal((await backend.resetProject({ generation: 0, token: 'empty', confirmedDiscard: true })).generation, 1);
  staleGraph.resolve(Response.json({ project_generation: 0, token: 'empty' }));
  staleBinary.resolve(new Response(new ArrayBuffer(1), { headers: { 'X-Scansor-Generation': '0' } }));
  await assert.rejects(graph, error => error.code === 'observation_superseded');
  await assert.rejects(binary, error => error.code === 'observation_superseded');
});

test('structured server source errors preserve stable codes rather than generic HTTP failures', async () => {
  const backend = createHttpBackend({ fetch: async () => Response.json({ error: { code: 'source_exists', message: 'Explicit new project required' } }, { status: 409 }) });
  await assert.rejects(backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }),
    error => error.code === 'source_exists' && error.status === 409 && error.message === 'Explicit new project required');
});

test('failed bundle delivery leaves the previous coherent generation cached for graph operations', async () => {
  let generation = 0;
  const backend = createHttpBackend({ fetch: async (path, init) => {
    if (path === '/api/meta') return Response.json(metadata(generation));
    if (path.startsWith('/mesh/')) return new Response(new ArrayBuffer(0), { headers: { 'X-Scansor-Generation': '0' } });
    assert.equal(init.headers['X-Scansor-Generation'], '0');
    return Response.json({ project_generation: 0, token: 'empty' });
  } });
  await backend.loadWorkspace();
  generation = 1;
  await assert.rejects(backend.loadWorkspace(), error => error.code === 'observation_superseded');
  await backend.replaceRecipe({ token: 'empty', recipe: { nodes: [] } });
});

test('source mutation fences an already subscribed graph observer before its next read', async () => {
  let generation = 0, graphReads = 0;
  const backend = createHttpBackend({ wait: async () => {}, fetch: async path => {
    if (path === '/api/meta') return Response.json(metadata(generation));
    if (path.startsWith('/mesh/')) return meshResponse(path, generation);
    if (path === '/api/project/reset') {
      generation = 1;
      return Response.json({ project_generation: 1 });
    }
    graphReads++;
    return Response.json({ project_generation: generation, revision: 1, evaluation_running: true });
  } });
  await backend.loadWorkspace();
  const updates = backend.graphUpdates();
  assert.equal((await updates.next()).value.evaluation_running, true);
  await backend.resetProject({ generation: 0, token: 'empty', confirmedDiscard: true });
  await assert.rejects(updates.next(), error => error.code === 'observation_superseded');
  assert.equal(graphReads, 1);
});

test('malformed accepted operation cannot publish an unrelated workspace', async () => {
  const backend = createHttpBackend({ wait: async () => {}, fetch: async path => {
    if (path === '/api/sources/imports') return Response.json(sourceRecord());
    assert.ok(!path.startsWith('/api/meta'));
    return Response.json(sourceRecord({ revision: 1, status: 'accepted', terminal: true }));
  } });
  await assert.rejects(backend.addScan({ file: scanFile(), generation: 0, token: 'empty' }),
    error => error.code === 'accepted_workspace_unavailable' && error.cause.code === 'invalid_operation');
});

test('example selection retains its old generation fence until the new workspace is coherently loaded', async () => {
  let generation = 0;
  const backend = createHttpBackend({ fetch: async (path, init) => {
    if (path === '/api/meta') return Response.json(metadata(generation));
    if (path.startsWith('/mesh/')) return meshResponse(path, generation);
    if (path === '/api/examples/select') {
      generation = 1;
      return Response.json({ active: 'example', project_generation: 1 });
    }
    if (path === '/api/graph') {
      assert.equal(init.headers['X-Scansor-Generation'], '0');
      return Response.json({ project_generation: generation, token: 'empty' });
    }
    assert.fail(path);
  } });
  await backend.loadWorkspace();
  await backend.selectExample({ example: 'example', token: 'empty' });
  await assert.rejects(backend.getGraph(), error => error.code === 'observation_superseded');
  assert.equal((await backend.loadWorkspace()).generation, 1);
});

test('sourceActions is independent of import and retains shared status and Cancel operations', async () => {
  const selected = createBackend({ type: 'actions-only', factories: { 'actions-only': () => {
    const backend = createHttpBackend({ fetch: async () => Response.json({ operationId: 'actions-1' }) });
    backend.capabilities = { ...backend.capabilities, sourceImport: false };
    return backend;
  } } });
  assert.equal((await selected.getSourceOperation({ operationId: 'actions-1' })).operationId, 'actions-1');
  assert.equal((await selected.cancelSourceOperation({ operationId: 'actions-1' })).operationId, 'actions-1');
  await assert.rejects(selected.addScan({}), error => error.code === 'unsupported_capability');
  const old = createBackend({ type: 'old', factories: { old: () => {
    const backend = createHttpBackend(); backend.capabilities = {}; return backend;
  } } });
  await assert.rejects(old.inspectActions({}), error => error.code === 'unsupported_capability');
  await assert.rejects(old.openActions({}), error => error.code === 'unsupported_capability');
  assert.throws(() => createBackend({ type: 'incomplete', factories: { incomplete: () => {
    const backend = createHttpBackend(); delete backend.openActions; return backend;
  } } }), error => error.code === 'unsupported_operation');
  selected.close(); old.close();
});

test('actions inspection is read-only, retains original intent, and validates the original cursor', async () => {
  const recipe = { nodes: [{ label: 'duplicate' }, { label: 'duplicate' }] }, calls = [];
  let stale = false;
  const backend = createHttpBackend({ fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json(metadata());
    if (path.startsWith('/mesh/')) return meshResponse(path, 0);
    return Response.json({ generation: stale ? 1 : 0, token: 'empty', classification: 'locate',
      uploadRequired: true, descriptor: { filename: 'old.ply' }, recipe: { nodes: [] }, import_warnings: ['renamed'] });
  } });
  await backend.loadWorkspace();
  const result = await backend.inspectActions({ recipe, generation: 0, token: 'empty' });
  assert.equal(result.classification, 'locate');
  assert.equal(calls.at(-1).path, '/api/actions/inspect');
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { recipe, generation: 0, token: 'empty' });
  assert.equal(calls.at(-1).init.headers['X-Scansor-Generation'], '0');
  stale = true;
  await assert.rejects(backend.inspectActions({ recipe, generation: 0, token: 'empty' }), error => error.code === 'invalid_operation');
});

test('matching and empty actions open without upload and adopt a new generation with warning receipt', async () => {
  for (const nodes of [[], [{ id: 'same-source', operation: 'source' }]]) {
    const calls = [], recipe = { nodes }, warnings = [{ code: 'duplicate-label' }];
    const backend = createHttpBackend({ wait: async () => {}, fetch: async (path, init) => {
      calls.push({ path, init });
      if (path === '/api/actions/open') return Response.json(sourceRecord({ purpose: 'open-actions', uploadRequired: false }));
      if (path === '/api/sources/imports/scan-1') return Response.json(sourceRecord({ purpose: 'open-actions', uploadRequired: false,
        revision: 2, terminal: true, status: 'accepted', acceptedGeneration: 1, import_warning_count: 1 }));
      if (path === '/api/meta') return Response.json({ ...metadata(1, 'opened'), import_warnings: warnings });
      if (path.startsWith('/mesh/')) return meshResponse(path, 1);
      assert.fail(path);
    } });
    const bundle = await backend.openActions({ recipe, generation: 0, token: 'empty', confirmedDiscard: true });
    assert.equal(bundle.generation, 1);
    assert.deepEqual(bundle.import_warnings, warnings);
    assert.deepEqual(JSON.parse(calls[0].init.body), { recipe, generation: 0, token: 'empty', confirmedDiscard: true });
    assert.equal(calls.some(({ path }) => path.endsWith('/bytes')), false);
  }
});

test('located actions keep raw JSON separate from the exact renamed File and recover a lost upload reply', async () => {
  const recipe = { nodes: [{ label: 'same' }, { label: 'same' }] }, file = scanFile(), calls = [];
  const backend = createHttpBackend({ wait: async () => {}, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/actions/open') return Response.json(sourceRecord({ purpose: 'open-actions', uploadRequired: true }));
    if (path.endsWith('/bytes')) { assert.equal(init.body, file); throw Error('Reply lost'); }
    if (path === '/api/sources/imports/scan-1') return Response.json(sourceRecord({ purpose: 'open-actions', uploadRequired: true,
      revision: 2, terminal: true, status: 'accepted', acceptedGeneration: 1, import_warning_count: 0 }));
    if (path === '/api/meta') return Response.json({ ...metadata(1), import_warnings: [] });
    return meshResponse(path, 1);
  } });
  const bundle = await backend.openActions({ recipe, file, generation: 0, token: 'empty', confirmedDiscard: true });
  assert.equal(bundle.generation, 1);
  assert.deepEqual(JSON.parse(calls[0].init.body), { recipe, generation: 0, token: 'empty', confirmedDiscard: true,
    source: { filename: file.name, size: file.size } });
  assert.equal(calls.some(({ path }) => path.endsWith('/cancel')), false);
});

test('accepted actions cannot lose their warning receipt or publish a different operation epoch', async () => {
  for (const defect of ['warnings', 'epoch', 'purpose']) {
    const backend = createHttpBackend({ fetch: async path => {
      if (path === '/api/actions/open') return Response.json(sourceRecord({ purpose: defect === 'purpose' ? 'add-scan' : 'open-actions',
        uploadRequired: false, terminal: true, status: 'accepted', acceptedGeneration: defect === 'epoch' ? 2 : 1, import_warning_count: 1 }));
      if (path === '/api/meta') return Response.json({ ...metadata(1), import_warnings: [] });
      return meshResponse(path, 1);
    } });
    await assert.rejects(backend.openActions({ recipe: { nodes: [] }, generation: 0, token: 'empty', confirmedDiscard: true }),
      error => error.code === 'accepted_workspace_unavailable'
        && error.cause.code === (defect === 'warnings' ? 'invalid_workspace' : 'invalid_operation'));
  }
});
