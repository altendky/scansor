import test from 'node:test';
import assert from 'node:assert/strict';
import { ensureGraphCurrent, waitForGraphEvaluation } from './graph-evaluation.js';

function snapshot(states = { face: 'ready', transform: 'ready' }, extra = {}) {
  return { token: 'recipe', recipe: { nodes: Object.keys(states).map((id) => ({ id, label: id })) },
    states, errors: {}, evaluation_running: false, ...extra };
}
function fixture(responses, targets = ['face']) {
  const requests = [], accepted = [];
  const options = { targets, token: 'recipe', pause: async () => {},
    request: async (path, value) => {
      requests.push({ path, value });
      assert(responses.length, 'Unexpected readiness request');
      return responses.shift();
    }, acceptState: (state) => accepted.push(state),
  };
  return { options, requests, accepted };
}

test('current outputs use one central check without polling or client evaluation', async () => {
  const f = fixture([snapshot()]);
  await ensureGraphCurrent(f.options);
  assert.deepEqual(f.requests, [{ path: '/api/graph/ensure', value: { token: 'recipe', targets: ['face'] } }]);
  assert.equal(f.accepted.length, 1);
});

test('export target, transform and origin guidance are deduplicated in one readiness request', async () => {
  const f = fixture([snapshot({ face: 'ready', transform: 'ready', origin: 'ready' })],
    ['transform', 'face', 'origin', 'transform', null]);
  await ensureGraphCurrent(f.options);
  assert.deepEqual(f.requests[0].value.targets, ['transform', 'face', 'origin']);
});

test('joined work is polled then rechecked to cover potentially different requested targets', async () => {
  const f = fixture([
    { status: 'running', evaluation_running: true },
    snapshot({ face: 'stale' }, { evaluation_running: true }),
    snapshot({ face: 'stale' }),
    { status: 'running', evaluation_running: true },
    snapshot({ face: 'ready' }),
    snapshot({ face: 'ready' }),
  ]);
  await ensureGraphCurrent(f.options);
  assert.deepEqual(f.requests.map((r) => r.path), ['/api/graph/ensure', '/api/graph', '/api/graph',
    '/api/graph/ensure', '/api/graph', '/api/graph/ensure']);
  assert.equal(f.accepted.length, 4);
});

test('known failures stay visible without implicit retries and unrelated failures do not block outputs', async () => {
  const failed = snapshot({ face: 'failed' }, { errors: { face: 'Boundary coverage failed' } });
  const f = fixture([failed]);
  await assert.rejects(ensureGraphCurrent(f.options), /Boundary coverage failed/);
  assert.deepEqual(f.accepted, [failed]);
  assert.equal(f.requests.length, 1);
  const unrelated = fixture([snapshot({ face: 'ready', other: 'failed' })]);
  await ensureGraphCurrent(unrelated.options);
});

test('readiness rejects removed or incomplete outputs', async () => {
  for (const [state, error] of [[snapshot({}), /removed/], [snapshot({ face: 'stale' }), /not ready/]]) {
    const f = fixture([state]);
    await assert.rejects(ensureGraphCurrent(f.options), error);
  }
});

test('a ready output cannot bypass failures in its required dependency closure', async () => {
  const f = fixture([snapshot({ face: 'ready', child: 'failed' }, {
    required_failures: ['child'], errors: { child: 'Required boundary failed' },
  })]);
  await assert.rejects(ensureGraphCurrent(f.options), /Required boundary failed/);
  assert.equal(f.requests.length, 1);
});

test('changed recipes and cancelled requests cannot publish old snapshots', async () => {
  const f = fixture([snapshot({}, { token: 'changed' })]);
  await assert.rejects(ensureGraphCurrent(f.options), /Actions changed/);
  assert.deepEqual(f.accepted, []);
  const cancelled = fixture([snapshot({}, { evaluation_running: true }),
    snapshot({}, { evaluation_running: true })]);
  let current = true;
  cancelled.options.isCurrent = () => current;
  cancelled.options.pause = async () => { current = false; };
  await assert.rejects(ensureGraphCurrent(cancelled.options), /operation changed/);
  assert.equal(cancelled.accepted.length, 2);
});

test('explicit evaluation waits for a running job without submitting or silently replacing its requested target', async () => {
  const f = fixture([snapshot({ face: 'running' }, { evaluation_running: true }), snapshot()]);
  const state = await waitForGraphEvaluation(f.options);
  assert.equal(state.states.face, 'ready');
  assert.deepEqual(f.requests.map((entry) => entry.path), ['/api/graph', '/api/graph']);
});

test('network failures propagate without substituting cached local geometry', async () => {
  const f = fixture([]);
  f.options.request = async () => { throw new Error('Network failed'); };
  await assert.rejects(ensureGraphCurrent(f.options), /Network failed/);
});

test('unclassified worker errors are shown rather than silently retrying forever', async () => {
  const f = fixture([snapshot({ face: 'stale' }, { evaluation_error: 'Kernel failure' })]);
  await assert.rejects(ensureGraphCurrent(f.options), /Kernel failure/);
  assert.equal(f.requests.length, 1);
});

function unchanged(revision, extra = {}) {
  return { token: 'recipe', revision, unchanged: true, evaluation_running: true, ...extra };
}

test('unchanged polls reuse the accepted revision without accepting or transferring geometry', async () => {
  const cached = snapshot({ face: 'running' }, { revision: 4, evaluation_running: true }),
    f = fixture([unchanged(4), unchanged(4), unchanged(4, { evaluation_running: false })]);
  f.options.getState = () => cached;
  const state = await waitForGraphEvaluation(f.options);
  assert.deepEqual(f.requests.map(entry => entry.path), Array(3).fill('/api/graph?revision=4'));
  assert.deepEqual(f.accepted, []);
  assert.equal(state.recipe, cached.recipe);
  assert.equal(state.evaluation_running, false);
  assert.equal(cached.evaluation_running, true);
});

test('same-token publications are accepted before completion and advance the snapshot revision', async () => {
  const publication = snapshot({ face: 'ready', transform: 'running' },
      { revision: 8, evaluation_running: true }),
    f = fixture([unchanged(4), publication, unchanged(8, { evaluation_running: false })]);
  f.options.getState = () => snapshot({ face: 'running' }, { revision: 4 });
  f.options.pause = async () => {
    if (f.accepted.length) assert.equal(f.accepted[0].evaluation_running, true);
  };
  const state = await waitForGraphEvaluation(f.options);
  assert.deepEqual(f.accepted, [publication]);
  assert.deepEqual(f.requests.map(entry => entry.path),
    ['/api/graph?revision=4', '/api/graph?revision=4', '/api/graph?revision=8']);
  assert.equal(state.revision, 8);
});

test('bootstrap polling takes its cursor from the full snapshot returned by the server', async () => {
  const f = fixture([snapshot({ face: 'ready' }, { revision: 7, evaluation_running: true }),
    unchanged(7, { evaluation_running: false })]);
  await waitForGraphEvaluation(f.options);
  assert.deepEqual(f.requests.map(entry => entry.path), ['/api/graph', '/api/graph?revision=7']);
  assert.equal(f.accepted.length, 1);
});

test('status-only errors propagate and successful job status clears previous worker errors', async () => {
  const cached = snapshot({}, { revision: 1, evaluation_error: 'Old failure' }),
    f = fixture([unchanged(1, { evaluation_running: false, evaluation_error: 'New worker failure' })]);
  f.options.getState = () => cached;
  assert.equal((await waitForGraphEvaluation(f.options)).evaluation_error, 'New worker failure');
  const repaired = fixture([unchanged(1, { evaluation_running: false })]);
  repaired.options.getState = () => cached;
  assert.equal((await waitForGraphEvaluation(repaired.options)).evaluation_error, undefined);
  assert.deepEqual(f.accepted, []);
});

test('compact responses still reject changed tokens, cancellation and invalid revisions', async () => {
  for (const [response, error] of [
    [unchanged(1, { token: 'edited' }), /Actions changed/],
    [unchanged(2), /Invalid unchanged/],
  ]) {
    const f = fixture([response]);
    f.options.getState = () => snapshot({}, { revision: 1 });
    await assert.rejects(waitForGraphEvaluation(f.options), error);
    assert.deepEqual(f.accepted, []);
  }
  const f = fixture([unchanged(1)]);
  let current = true;
  f.options.getState = () => snapshot({}, { revision: 1 });
  f.options.isCurrent = () => current;
  f.options.request = async () => { current = false; return unchanged(1); };
  await assert.rejects(waitForGraphEvaluation(f.options), /operation changed/);
  assert.deepEqual(f.accepted, []);
  const uncached = fixture([unchanged(1)]);
  await assert.rejects(waitForGraphEvaluation(uncached.options), /Invalid unchanged/);
});

test('unchanged completion of joined work still rechecks the callers own required roots', async () => {
  const cached = snapshot({ face: 'stale' }, { revision: 1 }),
    failed = snapshot({ face: 'ready', boundary: 'failed' }, { revision: 2,
      required_failures: ['boundary'], errors: { boundary: 'Required boundary failed' } }),
    f = fixture([{ evaluation_running: true }, unchanged(1, { evaluation_running: false }), failed]);
  f.options.getState = () => cached;
  await assert.rejects(ensureGraphCurrent(f.options), /Required boundary failed/);
  assert.deepEqual(f.requests.map(entry => entry.path),
    ['/api/graph/ensure', '/api/graph?revision=1', '/api/graph/ensure']);
  assert.deepEqual(f.accepted, [failed]);
});

test('compact completion updates lifecycle state even when the final readiness request fails', async () => {
  let cached = snapshot({ face: 'running' }, { revision: 3, evaluation_running: true });
  const f = fixture([{ evaluation_running: true }, unchanged(3, { evaluation_running: false })]);
  f.options.getState = () => cached;
  f.options.acceptEvaluationStatus = status => { cached = { ...cached, ...status }; };
  const request = f.options.request;
  f.options.request = async (...args) => {
    if (!f.requests.length || args[0] !== '/api/graph/ensure') return request(...args);
    throw new Error('Final readiness request failed');
  };
  await assert.rejects(ensureGraphCurrent(f.options), /Final readiness request failed/);
  assert.equal(cached.evaluation_running, false);
  assert.deepEqual(f.accepted, []);
});
