import test from 'node:test';
import assert from 'node:assert/strict';
import { createBackend, getBackend } from './backend.js';
import { BackendError, createHttpBackend } from './http-backend.js';
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
