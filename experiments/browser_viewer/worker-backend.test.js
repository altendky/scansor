import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorkerBackend } from './worker-backend.js';
import { createBackend } from './backend.js';

function fixture(options = {}) {
  const worker = { calls: [], terminated: 0,
    postMessage(message) { this.calls.push(message); },
    terminate() { this.terminated++; },
    reply(data) { this.onmessage({ data }); },
  };
  const backend = createWorkerBackend({ runtimeURL: './runtime/test/worker.mjs',
    workerFactory: (url, settings) => {
      assert(url.pathname.endsWith('/runtime/test/worker.mjs'));
      assert.deepEqual(settings, { type: 'module' });
      return worker;
    }, ...options });
  return { worker, backend };
}
const ready = { id: 1, ok: true, ready: true, versions: { python: '3.14.2' },
  source_revision: 'revision', source_worktree_dirty: false };

test('one worker initialization reports phases and declares the bounded numerical capabilities', async () => {
  const phases = [], { worker, backend } = fixture({ onPhase: phase => phases.push(phase) });
  const pending = backend.initialize();
  assert.equal(backend.initialize(), pending);
  worker.reply({ phase: 'Python runtime' });
  worker.reply({ ...ready, id: 2 });
  worker.reply(ready);
  assert.deepEqual(await pending, ready);
  assert.deepEqual(worker.calls, [{ id: 1, operation: 'initialize' }]);
  assert.deepEqual(phases, ['Python runtime']);
  const selected = createBackend({ type: 'worker', factories: { worker: () => backend } });
  assert.equal(selected.capabilities.numerical, true);
  assert.equal(selected.capabilities.cad, false);
  await assert.rejects(selected.exportCad(), error => error.code === 'unsupported_capability');
  selected.close();
  assert.equal(worker.terminated, 1);
});

test('runtime startup failures stay failed without retry or HTTP fallback', async () => {
  const { worker, backend } = fixture();
  const pending = backend.initialize();
  worker.reply({ id: 1, ok: false, error: 'Missing asset fixture.npz: HTTP 404' });
  await assert.rejects(pending, /Missing asset/);
  await assert.rejects(backend.initialize(), /Missing asset/);
  assert.equal(worker.calls.length, 1);
  assert.equal(worker.terminated, 1);
});

test('closing initialization settles its promise and ignores late readiness', async () => {
  const { worker, backend } = fixture();
  const pending = backend.initialize();
  backend.close();
  worker.reply(ready);
  await assert.rejects(pending, error => error.code === 'backend_closed');
  await assert.rejects(backend.initialize(), error => error.code === 'backend_closed');
  assert.equal(worker.terminated, 1);
});

test('startup timeout destroys the unfinished runtime', async () => {
  const { worker, backend } = fixture({ startupTimeout: 5 });
  await assert.rejects(backend.initialize(), /initialization timed out/);
  assert.equal(worker.terminated, 1);
});

test('message decode, worker construction and invalid ready responses fail explicitly', async () => {
  for (const kind of ['decode', 'invalid', 'worker-error']) {
    const { worker, backend } = fixture(), pending = backend.initialize();
    if (kind === 'decode') worker.onmessageerror();
    else if (kind === 'worker-error') worker.onerror({ message: 'Worker unavailable' });
    else worker.reply({ id: 1, ok: true });
    await assert.rejects(pending, error => error.code === 'worker_startup_failed');
    assert.equal(worker.terminated, 1);
  }
  const failed = createWorkerBackend({ runtimeURL: './worker.mjs', workerFactory: () => {
    throw new Error('Workers unavailable');
  } });
  await assert.rejects(failed.initialize(), /Workers unavailable/);
  assert.throws(() => createWorkerBackend(), error => error.code === 'missing_runtime');
});

test('fatal errors after readiness retire the runtime and notify the UI', async () => {
  const failures = [], { worker, backend } = fixture({ onFailure: error => failures.push(error.message) });
  const pending = backend.initialize();
  worker.reply(ready);
  await pending;
  worker.reply({ fatal: 'Runtime lost' });
  await assert.rejects(backend.initialize(), /Runtime lost/);
  assert.deepEqual(failures, ['Runtime lost']);
  assert.equal(worker.terminated, 1);
});

async function initialized(options) {
  const value = fixture(options), pending = value.backend.initialize();
  value.worker.reply(ready);
  await pending;
  return value;
}
const graph = (extra = {}) => ({ token: 'token', revision: 3,
  recipe: { nodes: [{ id: 'source', operation: 'source' }] },
  states: {}, results: {}, evaluation_running: false, ...extra });
const tick = () => Promise.resolve();
async function requestReply(value, operation, payload, result) {
  const pending = value.backend[operation](payload);
  await tick();
  const request = value.worker.calls.at(-1);
  assert.equal(request.operation, operation);
  value.worker.reply({ id: request.id, ok: true, result });
  return pending;
}

test('workspace waits for actual sender detachment and returns independently owned buffers', async () => {
  const value = await initialized(), pending = value.backend.loadWorkspace();
  await tick();
  const id = value.worker.calls.at(-1).id;
  const positions = new ArrayBuffer(12), indices = new ArrayBuffer(12);
  let settled = false;
  pending.then(() => { settled = true; });
  value.worker.reply({ id, ok: true, result: { metadata: {}, positions, indices } });
  await tick();
  assert.equal(settled, false);
  value.worker.reply({ id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
  const result = await pending;
  assert.equal(result.positions, positions);
  assert.deepEqual(result.transferOwnership, { sender_detached: true, positions_bytes: 12,
    indices_bytes: 12, copied_js_bytes: 24, strategy: 'copy-contiguous-wasm-view-then-transfer' });
  value.backend.close();
});

test('pushed running acknowledgements and completion before subscription cannot miss terminal status', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  await requestReply(value, 'ensureGraph', { token: 'token' },
    { token: 'token', status: 'running', evaluation_running: true });
  const stream = value.backend.graphUpdates();
  assert.equal((await stream.next()).value.evaluation_running, true);
  const next = stream.next();
  value.worker.reply({ kind: 'graph', generation: 0, state: graph({ revision: 4 }) });
  assert.equal((await next).value.revision, 4);
  assert.equal((await stream.next()).done, true);
  const late = value.backend.graphUpdates();
  assert.equal((await late.next()).value.evaluation_running, false);
  assert.equal((await late.next()).done, true);
  value.backend.close();
});

test('an observer without a cached snapshot requests its first graph', async () => {
  const value = await initialized(), stream = value.backend.graphUpdates();
  const first = stream.next();
  await tick();
  await tick();
  const request = value.worker.calls.at(-1);
  assert.equal(request.operation, 'getGraph');
  value.worker.reply({ id: request.id, ok: true, result: graph() });
  assert.equal((await first).value.token, 'token');
  assert.equal((await stream.next()).done, true);
  value.backend.close();
});

test('mutation fences obsolete graph publication and wakes superseded observers', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
  const stream = value.backend.graphUpdates();
  await stream.next();
  const observed = stream.next();
  const mutation = value.backend.replaceRecipe({ token: 'token', recipe: {} });
  await tick();
  const request = value.worker.calls.at(-1);
  value.worker.reply({ kind: 'graph', generation: 0, state: graph({ revision: 99 }) });
  await assert.rejects(observed, error => error.code === 'observation_superseded');
  value.worker.reply({ id: request.id, ok: true, result: graph({ token: 'new', revision: 5 }) });
  await mutation;
  assert.equal((await value.backend.graphUpdates().next()).value.revision, 5);
  value.backend.close();
});

test('structured rejected mutations preserve committed recipe and recovery survives runtime loss', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  const pending = value.backend.importRecipe({ token: 'token', recipe: {} });
  await tick();
  value.worker.reply({ id: value.worker.calls.at(-1).id, ok: false,
    error: { code: 'unsupported_capability', status: 422, message: 'Unsupported CAD action' } });
  await assert.rejects(pending, error => error.code === 'unsupported_capability' && error.status === 422);
  value.worker.reply({ fatal: 'lost' });
  const recovered = await value.backend.exportRecipe();
  assert.deepEqual(recovered, graph().recipe);
  recovered.nodes.length = 0;
  assert.equal((await value.backend.exportRecipe()).nodes.length, 1);
});

test('close and fatal settle concurrent operations and waiting observers', async () => {
  for (const fatal of [false, true]) {
    const value = await initialized();
    await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
    const stream = value.backend.graphUpdates();
    await stream.next();
    const observed = stream.next(), first = value.backend.getGraph(), second = value.backend.listExamples();
    await tick();
    if (fatal) value.worker.reply({ fatal: 'runtime lost' });
    else value.backend.close();
    for (const pending of [observed, first, second])
      await assert.rejects(pending, error => error.code === (fatal ? 'worker_failed' : 'backend_closed'));
    assert.equal(value.worker.terminated, 1);
  }
});

test('aborting observation stops waiting without terminating computation', async () => {
  const value = await initialized(), controller = new AbortController();
  await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
  const stream = value.backend.graphUpdates({ signal: controller.signal });
  await stream.next();
  const observed = stream.next();
  controller.abort(new Error('stop waiting'));
  await assert.rejects(observed, /stop waiting/);
  assert.equal(value.worker.terminated, 0);
  value.backend.close();
});

test('stale pending graph replies cannot publish after a mutation starts', async () => {
  const value = await initialized();
  const read = value.backend.getGraph();
  await tick();
  const old = value.worker.calls.at(-1);
  const change = value.backend.replaceRecipe({ token: 'token', recipe: {} });
  await tick();
  const mutation = value.worker.calls.at(-1);
  value.worker.reply({ id: old.id, ok: true, result: graph() });
  await assert.rejects(read, error => error.code === 'observation_superseded');
  value.worker.reply({ id: mutation.id, ok: true, result: graph({ token: 'new' }) });
  await change;
  value.backend.close();
});

test('abort during shared startup settles only that observation immediately', async () => {
  for (const stream of [false, true]) {
    const controller = new AbortController(), value = fixture();
    const observation = stream ? value.backend.graphUpdates({ signal: controller.signal }).next()
      : value.backend.getGraph({ signal: controller.signal });
    controller.abort(new Error('leave startup'));
    await assert.rejects(observation, /leave startup/);
    assert.equal(value.worker.terminated, 0);
    const initialization = value.backend.initialize();
    value.worker.reply(ready);
    await initialization;
    value.backend.close();
  }
});

test('CAD profile transfers binary exports and waits for sender detachment', async () => {
  const value = await initialized({ cad: true });
  const selected = createBackend({ type: 'worker', factories: { worker: () => value.backend } });
  assert.equal(selected.capabilities.cad, true);
  const pending = selected.exportCad({ token: 'token' });
  await tick();
  const id = value.worker.calls.at(-1).id, bytes = new Uint8Array([80, 75, 3, 4]).buffer;
  let settled = false;
  pending.then(() => { settled = true; });
  value.worker.reply({ id, ok: true, result: bytes });
  await tick();
  assert.equal(settled, false);
  value.worker.reply({ id, kind: 'transfer-confirmed', export_bytes_after: 0 });
  assert.equal(await pending, bytes);
  selected.close();
});

test('optional CAD loading failure leaves numerical operations available', async () => {
  const value = await initialized({ cad: true });
  const pending = value.backend.faceCandidates({ token: 'token' });
  await tick();
  value.worker.reply({ id: value.worker.calls.at(-1).id, ok: false,
    error: { code: 'cad_startup_failed', message: 'Missing CAD asset; numerical work remains available' } });
  await assert.rejects(pending, error => error.code === 'cad_startup_failed');
  assert.equal(value.worker.terminated, 0);
  assert.deepEqual(await requestReply(value, 'getGraph', {}, graph()), graph());
  value.backend.close();
});

test('a mutation during binary transfer confirmation rejects the obsolete export', async () => {
  const value = await initialized({ cad: true }), exported = value.backend.exportCad({ token: 'token' });
  await tick();
  const id = value.worker.calls.at(-1).id;
  value.worker.reply({ id, ok: true, result: new ArrayBuffer(4) });
  const applied = value.backend.applyFaces({ token: 'token' });
  await tick();
  const mutation = value.worker.calls.at(-1);
  value.worker.reply({ id, kind: 'transfer-confirmed', export_bytes_after: 0 });
  await assert.rejects(exported, error => error.code === 'observation_superseded');
  value.worker.reply({ id: mutation.id, ok: true, result: graph({ token: 'new' }) });
  await applied;
  value.backend.close();
});
