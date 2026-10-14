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

test('one worker initialization reports phases and cannot enable unfinished operations', async () => {
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
  assert.equal(selected.capabilities.numerical, false);
  await assert.rejects(selected.loadWorkspace(), error => error.code === 'unsupported_capability');
  await assert.rejects(selected.graphUpdates().next(), /not available/);
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
