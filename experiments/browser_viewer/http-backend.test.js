import assert from 'node:assert/strict';
import test from 'node:test';
import { createHttpBackend } from './http-backend.js';

const surfaceProfile = { supported: true, version: 1, policy: 'seed-normal-envelope-v1', max_preview_ids: 2048 };
const payload = { token: 'accepted', seed_selection: 'seed', barriers: ['stop'], angle_degrees: 20,
  setback: 0, policy: surfaceProfile.policy };
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
function snapshot(extra = {}) {
  return { token: 'accepted', revision: 8, project_generation: 3, recipe: { nodes: [{ id: 'seed', operation: 'selection' }] },
    states: { seed: 'ready' }, results: { seed: { ids: [1] } }, evaluation_running: false, evaluation: null, ...extra };
}
function previewState(state = 'running', extra = {}) {
  return { token: 'accepted', project_generation: 3, evaluation_running: ['queued', 'running', 'cancel-requested'].includes(state),
    evaluation: { id: 'preview-1', sequence: 1, generation: 3, state, kind: 'preview', roots: ['seed', 'stop'] }, ...extra };
}
async function surfaceHttp({ profile = surfaceProfile, fetch, wait = async () => {} } = {}) {
  const calls = [];
  const backend = createHttpBackend({ wait, fetch: async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/meta') return Response.json({ project_generation: 3, surface_selection: profile,
      graph: snapshot(), positions: { url: '/positions' }, indices: { url: '/indices' } });
    if (path === '/positions' || path === '/indices') return new Response(new ArrayBuffer(0),
      { headers: { 'X-Scansor-Generation': '3' } });
    return fetch(path, init);
  } });
  await backend.loadWorkspace();
  return { backend, calls };
}

test('HTTP surface preview negotiates its bounded profile before starting owned work', async () => {
  for (const profile of [null, { ...surfaceProfile, supported: false }, { ...surfaceProfile, version: true },
    { ...surfaceProfile, policy: 'unsupported' }, { ...surfaceProfile, max_preview_ids: 2049 }]) {
    const f = await surfaceHttp({ profile, fetch: () => assert.fail('unnegotiated private work') });
    await assert.rejects(f.backend.previewSurfaceSelection(payload), error => error.code === 'unsupported_capability');
    assert.equal(f.calls.length, 3); f.backend.close();
  }
});

test('HTTP surface preview uses source fences and keeps accepted graph state out of its private result', async () => {
  const f = await surfaceHttp({ fetch: async path => {
    if (path.endsWith('/preview')) return Response.json(previewState('queued'));
    if (path.includes('/preview/')) return Response.json(previewState('complete', { preview: { selected: [2, 3] } }));
    if (path.startsWith('/api/graph')) return Response.json({ token: 'accepted', revision: 8,
      project_generation: 3, states: { seed: 'ready' }, evaluation: previewState('complete').evaluation });
    assert.fail(`unexpected route ${path}`);
  } });
  const result = await f.backend.previewSurfaceSelection(payload);
  assert.deepEqual(result.preview, { selected: [2, 3] });
  const start = f.calls.find(call => call.path.endsWith('/preview'));
  assert.deepEqual(JSON.parse(start.init.body), payload);
  assert.equal(start.init.headers['X-Scansor-Generation'], '3');
  assert.equal(start.init.signal.aborted, false);
  const accepted = await f.backend.getGraph();
  assert.deepEqual(accepted.recipe, snapshot().recipe);
  assert.deepEqual(accepted.results, snapshot().results);
  assert.equal(Object.hasOwn(accepted, 'preview'), false);
  f.backend.close();
});

test('HTTP Abort before the start receipt cancels the owned preview and waits for real retirement', async () => {
  const start = deferred(), retire = deferred();
  const f = await surfaceHttp({ wait: () => retire.promise, fetch: async path => {
    if (path.endsWith('/preview')) return start.promise;
    if (path === '/api/graph/cancel') return Response.json(snapshot(previewState('cancel-requested')));
    if (path.startsWith('/api/graph')) return Response.json(snapshot(previewState('cancelled')));
    assert.fail(`unexpected route ${path}`);
  } });
  const controller = new AbortController(), reason = new DOMException('Inputs changed', 'AbortError');
  const running = f.backend.previewSurfaceSelection(payload, { signal: controller.signal });
  let settled = false;
  const rejected = assert.rejects(running, error => error === reason).then(() => { settled = true; });
  controller.abort(reason);
  assert.equal(f.calls.some(call => call.path === '/api/graph/cancel'), false);
  start.resolve(Response.json(previewState())); await flush();
  const stopping = f.calls.find(call => call.path === '/api/graph/cancel');
  assert.deepEqual(JSON.parse(stopping.init.body), { evaluationId: 'preview-1', generation: 3 });
  assert.equal(f.calls.find(call => call.path.endsWith('/preview')).init.signal.aborted, false);
  assert.equal(settled, false);
  assert.equal(f.backend.getLifecycle().state, 'cancel-requested');
  retire.resolve(); await rejected;
  assert.equal(f.backend.getLifecycle().state, 'cancelled');
  f.backend.close();
});

test('HTTP clearing an obsolete preview never cancels a newer evaluation owned by another request', async () => {
  const poll = deferred();
  const newer = { id: 'other', sequence: 2, generation: 3, state: 'running', kind: 'ensure', roots: ['other'] };
  const f = await surfaceHttp({ fetch: async path => {
    if (path.endsWith('/preview')) return Response.json(previewState());
    if (path.includes('/preview/')) return poll.promise;
    if (path === '/api/graph') return Response.json(snapshot({ evaluation: newer, evaluation_running: true }));
    assert.fail(`must not cancel another owner: ${path}`);
  } });
  const controller = new AbortController(), reason = Error('Form closed');
  const running = f.backend.previewSurfaceSelection(payload, { signal: controller.signal });
  const rejected = assert.rejects(running, error => error === reason);
  await flush(); await f.backend.getGraph();
  controller.abort(reason);
  poll.resolve(Response.json(previewState('complete', { preview: {} })));
  await rejected;
  assert.equal(f.calls.some(call => call.path === '/api/graph/cancel'), false);
  assert.equal(f.backend.getLifecycle().evaluationId, 'other');
  assert.equal(f.backend.getLifecycle().state, 'running');
  f.backend.close();
});

test('HTTP clearing an active polled preview fences its late result and waits for owned cancellation', async () => {
  const poll = deferred(), retire = deferred();
  const f = await surfaceHttp({ wait: () => retire.promise, fetch: async path => {
    if (path.endsWith('/preview')) return Response.json(previewState());
    if (path.includes('/preview/')) return poll.promise;
    if (path === '/api/graph/cancel') return Response.json(snapshot(previewState('cancel-requested')));
    if (path === '/api/graph') return Response.json(snapshot(previewState('cancelled')));
    assert.fail(`unexpected route ${path}`);
  } });
  const controller = new AbortController(), reason = Error('Superseded preview');
  const running = f.backend.previewSurfaceSelection(payload, { signal: controller.signal });
  let settled = false;
  const rejected = assert.rejects(running, error => error === reason).then(() => { settled = true; });
  await flush(); controller.abort(reason); await flush();
  assert.equal(f.calls.filter(call => call.path === '/api/graph/cancel').length, 1);
  poll.resolve(Response.json(previewState('complete', { preview: { obsolete: true } })));
  await flush(); assert.equal(settled, false);
  retire.resolve(); await rejected;
  assert.equal(f.backend.getLifecycle().state, 'cancelled');
  f.backend.close();
});

for (const aborted of [false, true]) test(`HTTP lost start reply remains discoverable for explicit Cancel${aborted ? ' after caller Abort' : ''}`, async () => {
  const start = deferred(), reason = Error('Closed preview form');
  const f = await surfaceHttp({ fetch: async path => {
    if (path.endsWith('/preview')) return start.promise;
    if (path === '/api/graph') return Response.json(snapshot(previewState()));
    if (path === '/api/graph/cancel') return Response.json(snapshot(previewState('cancelled')));
    assert.fail(`unexpected route ${path}`);
  } });
  const controller = new AbortController();
  const pending = f.backend.previewSurfaceSelection(payload, { signal: controller.signal });
  const rejected = assert.rejects(pending, error => aborted ? error === reason
    : error.code === 'transport_error' && /Connection lost/.test(error.message));
  if (aborted) controller.abort(reason);
  start.resolve(Promise.reject(Error('Connection lost after server acceptance')));
  await rejected;
  assert.equal(f.calls.filter(call => call.path === '/api/graph').length, 1);
  assert.equal(f.calls.some(call => call.path === '/api/graph/cancel'), false,
    'a discovered operation is not proof that this form owns it');
  assert.equal(f.backend.getLifecycle().state, 'running');
  assert.equal(f.backend.getLifecycle().canCancel, true);
  assert.equal(f.backend.getLifecycle().operation, 'preview');
  const stopped = await f.backend.cancelComputation();
  assert.equal(stopped.evaluation.state, 'cancelled');
  assert.deepEqual(stopped.results, snapshot().results);
  assert.equal(f.calls.filter(call => call.path === '/api/graph/cancel').length, 1);
  f.backend.close();
});
