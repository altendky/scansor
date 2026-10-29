import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpBackend } from './http-backend.js';

function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
function snapshot(state = 'running', { id = 'first', sequence = 1, ...extra } = {}) {
  return { token: 'recipe', revision: 5, project_generation: 3,
    recipe: { nodes: [{ id: 'fit', label: 'Hole', operation: 'fit' }], output: null },
    states: { fit: state === 'complete' ? 'ready' : 'stale' }, results: {},
    evaluation_running: ['queued', 'running', 'cancel-requested'].includes(state),
    evaluation: { id, sequence, generation: 3, state, kind: 'evaluate', roots: ['fit'] }, ...extra };
}

test('native lifecycle follows queued, running and compact terminal graph responses', async () => {
  const states = [], replies = [snapshot('queued'), snapshot('running'), {
    ...snapshot('complete'), unchanged: true, recipe: undefined, states: undefined,
  }];
  const backend = createHttpBackend({ onLifecycle: state => states.push(state),
    fetch: async () => Response.json(replies.shift()) });
  assert.equal(backend.capabilities.cancelComputation, true);
  assert.equal(backend.capabilities.forceRestart, false);
  await backend.getGraph(); assert.equal(backend.getLifecycle().canCancel, true);
  await backend.getGraph(); await backend.getGraph({ revision: 5 });
  assert.deepEqual(states.map(state => state.state), ['queued', 'running', 'complete']);
  assert.equal(backend.getLifecycle().canCancel, false);
  assert.equal(backend.getLifecycle().canForce, false);
  assert.equal(await backend.cancelComputation(), null);
  backend.close(); assert.equal(backend.getLifecycle().state, 'closed');
  await assert.rejects(backend.cancelComputation(), error => error.code === 'backend_closed');
});

test('Cancel fences late graph and export replies and waits for real native retirement', async () => {
  const late = deferred(), binary = deferred(), retirement = deferred(), calls = [];
  let reads = 0;
  const backend = createHttpBackend({ wait: () => retirement.promise, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/export/cad') return binary.promise;
    if (path === '/api/graph/cancel') return Response.json(snapshot('cancel-requested'));
    if (++reads === 1) return Response.json(snapshot());
    if (reads === 2) return late.promise;
    return Response.json(snapshot('cancelled'));
  } });
  await backend.getGraph();
  const oldGraph = backend.getGraph(), oldExport = backend.exportCad({ token: 'recipe' });
  const rejectedGraph = assert.rejects(oldGraph, error => error.code === 'computation_cancelled');
  const rejectedExport = assert.rejects(oldExport, error => error.code === 'computation_cancelled');
  const stopping = backend.cancelComputation();
  assert.equal(backend.cancelComputation(), stopping);
  let settled = false;
  stopping.then(() => { settled = true; });
  await new Promise(setImmediate);
  assert.equal(backend.getLifecycle().state, 'cancel-requested');
  assert.equal(settled, false);
  assert.deepEqual(JSON.parse(calls.find(call => call.path.endsWith('/cancel')).init.body),
    { generation: 3, evaluationId: 'first' });
  late.resolve(Response.json(snapshot('complete', { results: { fit: { obsolete: true } } })));
  binary.resolve(new Response(new Uint8Array([80, 75])));
  await Promise.all([rejectedGraph, rejectedExport]);
  assert.equal(settled, false);
  retirement.resolve();
  const current = await stopping;
  assert.deepEqual(current.results, {});
  assert.equal(current.evaluation.state, 'cancelled');
  assert.equal(current.project_generation, 3);
  assert.equal(current.recipe.nodes[0].label, 'Hole');
  assert.equal(await backend.cancelComputation(), null);
  assert.equal(calls.filter(call => call.path.endsWith('/cancel')).length, 1);
  backend.close();
});

test('unconfirmed cancellation remains waiting and can be explicitly retried', async () => {
  let attempts = 0;
  const backend = createHttpBackend({ fetch: async path => {
    if (path !== '/api/graph/cancel') return Response.json(snapshot());
    if (++attempts === 1) throw Error('Reply lost');
    return Response.json(snapshot('cancelled'));
  } });
  await backend.getGraph();
  await assert.rejects(backend.cancelComputation(), error => error.code === 'transport_error');
  assert.equal(attempts, 1);
  assert.equal(backend.getLifecycle().state, 'cancel-requested');
  assert.equal(backend.getLifecycle().retryCancel, true);
  assert.equal(backend.getLifecycle().canCancel, true);
  assert.equal((await backend.cancelComputation()).evaluation.state, 'cancelled');
  assert.equal(attempts, 2); backend.close();
});

test('late lifecycle messages cannot replace a newer operation or regress terminal state', async () => {
  const replies = [snapshot(), snapshot('queued', { id: 'second', sequence: 2 }), snapshot('cancelled'),
    snapshot('running', { id: 'second', sequence: 2 }), snapshot('complete', { id: 'second', sequence: 2 }),
    snapshot('running', { id: 'second', sequence: 2 })];
  const backend = createHttpBackend({ fetch: async () => Response.json(replies.shift()) });
  await backend.getGraph(); await backend.getGraph();
  await assert.rejects(backend.getGraph(), error => error.code === 'observation_superseded');
  assert.equal(backend.getLifecycle().evaluationId, 'second');
  assert.equal(backend.getLifecycle().state, 'queued');
  await backend.getGraph(); await backend.getGraph();
  await assert.rejects(backend.getGraph(), error => error.code === 'observation_superseded');
  assert.equal(backend.getLifecycle().state, 'complete'); backend.close();
});

test('a delayed idle response cannot disable Cancel for newly acknowledged work', async () => {
  for (const method of ['getGraph', 'ensureGraph']) {
    const idle = deferred();
    const backend = createHttpBackend({ fetch: async path => path === '/api/graph/evaluate'
      ? Response.json(snapshot()) : idle.promise });
    const stale = backend[method]({ token: 'recipe', targets: ['fit'] });
    const rejected = assert.rejects(stale, error => error.code === 'observation_superseded');
    await backend.evaluateGraph({ token: 'recipe', all_actions: true });
    assert.equal(backend.getLifecycle().state, 'running');
    idle.resolve(Response.json({ ...snapshot('complete'), evaluation: null }));
    await rejected;
    assert.equal(backend.getLifecycle().state, 'running');
    assert.equal(backend.getLifecycle().canCancel, true);
    assert.equal(backend.getLifecycle().evaluationId, 'first');
    backend.close();
  }
});

test('Cancel retires an entire old graph observer, including its next poll', async () => {
  let reads = 0;
  const backend = createHttpBackend({ wait: async () => {}, fetch: async path => {
    if (path === '/api/graph/cancel') return Response.json(snapshot('cancelled'));
    reads++; return Response.json(snapshot());
  } });
  const iterator = backend.graphUpdates();
  assert.equal((await iterator.next()).value.evaluation_running, true);
  await backend.cancelComputation();
  await assert.rejects(iterator.next(), error => error.code === 'computation_cancelled');
  assert.equal(reads, 1); backend.close();
});

test('malformed native operation descriptors fail without enabling a misleading Cancel', async () => {
  for (const change of [{ generation: -1 }, { sequence: .5 }, { kind: 'import' }, { state: 'retired' }, { roots: [7] }]) {
    const value = snapshot(); Object.assign(value.evaluation, change);
    const backend = createHttpBackend({ fetch: async () => Response.json(value) });
    await assert.rejects(backend.getGraph(), error => error.code === 'invalid_operation');
    assert.equal(backend.getLifecycle().canCancel, false); backend.close();
  }
});
