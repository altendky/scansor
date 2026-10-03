import test from 'node:test';
import assert from 'node:assert/strict';
import { faceContinuationInputs, prepareFaceContinuation, faceDisplayEntries } from './surface-trims.js';

const target = { feature: 'wall' };
function fixture() {
  const nodes = [
    { id: 'wall', operation: 'fit', label: 'Wall' },
    { id: 'built', operation: 'build_faces', label: 'Shoulder face', reused_faces: ['reused'] },
    { id: 'face1', operation: 'arranged_face', managed_by: 'built' },
    { id: 'face2', operation: 'trimmed_face', managed_by: 'built' },
    { id: 'edge', operation: 'surface_intersection', managed_by: 'built' },
    { id: 'reused', operation: 'trimmed_face' },
    { id: 'unrelated', operation: 'fit' },
  ];
  let state = { token: 'unchanged', recipe: { nodes },
    states: Object.fromEntries(nodes.map((node) => [node.id, 'ready'])), errors: {} };
  const ensured = [];
  const options = { target, appliedOwnerId: 'built', getState: () => state,
    ensureCurrent: async (ids, options) => {
      ensured.push({ ids, options });
      for (const input of faceContinuationInputs(state, target, 'built')) state.states[input] = 'ready';
    },
  };
  return { get state() { return state; }, set state(value) { state = value; }, options, ensured };
}

test('Continue delegates ready inputs to central readiness, excluding unrelated stale or failed actions', async () => {
  for (const status of ['stale', 'failed', 'blocked']) {
    const f = fixture();
    f.state.states.unrelated = status;
    await prepareFaceContinuation(f.options);
    assert.deepEqual(f.ensured.map((call) => call.ids), [['face1', 'face2', 'reused', 'built', 'wall']]);
    assert.equal(f.ensured[0].options.token, 'unchanged');
  }
});

test('Continue waits for central readiness before moving on', async () => {
  const f = fixture();
  f.state.states.face1 = 'running';
  let finish;
  const evaluation = new Promise((resolve) => { finish = resolve; });
  f.options.ensureCurrent = () => evaluation;
  const pending = prepareFaceContinuation(f.options);
  f.state.states.face1 = 'ready';
  finish();
  await pending;
  assert.equal(f.state.states.face1, 'ready');
});

test('Continue submits all required stale outputs together', async () => {
  const f = fixture();
  f.state.states.built = f.state.states.face1 = f.state.states.face2 = 'stale';
  f.state.states.unrelated = 'stale';
  await prepareFaceContinuation(f.options);
  assert.deepEqual(f.ensured.map((call) => call.ids), [['face1', 'face2', 'reused', 'built', 'wall']]);
  assert.equal(f.state.states.unrelated, 'stale');
});

test('Continue also checks the next saved review boundary sources and scopes', async () => {
  const f = fixture();
  f.state.recipe.nodes.push({ id: 'next', operation: 'build_faces', target,
    boundary_sources: ['reused'], face_scopes: [{ surface: target, faces: ['face2'] }],
    surfaces: [target] });
  assert.deepEqual(faceContinuationInputs(f.state, target, 'built'),
    ['face1', 'face2', 'reused', 'built', 'wall']);
});

test('Continue stops on required failures without retrying or hiding the error', async () => {
  const f = fixture();
  f.state.states.face1 = 'failed';
  f.state.errors.face1 = 'Boundary coverage failed';
  await assert.rejects(prepareFaceContinuation(f.options), /Boundary coverage failed/);
  assert.deepEqual(f.ensured, []);
});

test('Continue rejects a removed applied owner rather than discarding its guidance', async () => {
  const f = fixture();
  f.state.recipe.nodes = f.state.recipe.nodes.filter((node) => node.id !== 'built');
  await assert.rejects(prepareFaceContinuation(f.options), /guidance was removed/);
  assert.deepEqual(f.ensured, []);
});

test('Continue checks both an aggregate provider and its referenced member fit', async () => {
  const f = fixture();
  f.state.recipe.nodes.push({ id: 'joint', operation: 'axis_solve' });
  f.state.states.joint = 'ready';
  f.options.target = { feature: 'joint', surface: 'wall' };
  f.state.states.wall = 'stale';
  await prepareFaceContinuation(f.options);
  assert(f.ensured[0].ids.includes('joint'));
  assert(f.ensured[0].ids.includes('wall'));
  f.state.states.wall = 'failed';
  await assert.rejects(prepareFaceContinuation(f.options), /Could not evaluate Wall/);
});

test('Continue does not navigate on failed or incomplete targeted evaluation', async () => {
  const f = fixture();
  f.state.states.face1 = 'stale';
  f.options.ensureCurrent = async () => {};
  await assert.rejects(prepareFaceContinuation(f.options), /guidance is not ready/);
  f.options.ensureCurrent = async () => { throw new Error('Network failed'); };
  await assert.rejects(prepareFaceContinuation(f.options), /Network failed/);
});

test('Continue cancels if graph or dialog changes while waiting or evaluating', async () => {
  const changed = fixture();
  changed.state.states.face1 = 'stale';
  changed.options.ensureCurrent = async () => { changed.state.token = 'changed'; };
  await assert.rejects(prepareFaceContinuation(changed.options), /Actions changed/);
  const f = fixture();
  f.options.isCurrent = () => false;
  await assert.rejects(prepareFaceContinuation(f.options), /Face review changed/);
  assert.deepEqual(f.ensured, []);
});

test('Faces only includes all ready faces, excluding fits, edges and unavailable geometry', () => {
  const f = fixture();
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  f.state.results = Object.fromEntries(f.state.recipe.nodes.map((node) => [node.id, { preview }]));
  assert.deepEqual(faceDisplayEntries(f.state).map(({ node }) => node.id), ['face1', 'face2', 'reused']);
  for (const status of ['stale', 'failed', 'blocked', 'running']) {
    f.state.states.face1 = status;
    assert.deepEqual(faceDisplayEntries(f.state).map(({ node }) => node.id), ['face2', 'reused']);
  }
  delete f.state.results.face2;
  f.state.results.reused = { preview: { ...preview, indices: [0, 1, 99] } };
  assert.deepEqual(faceDisplayEntries(f.state), []);
});
