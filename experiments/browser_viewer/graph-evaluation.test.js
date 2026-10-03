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
