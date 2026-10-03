import test from 'node:test';
import assert from 'node:assert/strict';
import { exportFaceIds, exportInputIssues, exportScopePlan } from './cad-export.js';

function fixture() {
  const nodes = [
    { id: 'fit', operation: 'fit' },
    { id: 'manual', operation: 'trimmed_face' },
    { id: 'owner', operation: 'build_faces', reused_faces: ['manual'] },
    { id: 'first', operation: 'arranged_face', managed_by: 'owner' },
    { id: 'edge', operation: 'surface_intersection', managed_by: 'owner' },
    { id: 'nested', operation: 'build_faces', managed_by: 'owner', reused_faces: ['manual'] },
    { id: 'second', operation: 'trimmed_face', managed_by: 'nested' },
    { id: 'reusedOnly', operation: 'build_faces', reused_faces: ['manual'] },
    { id: 'other', operation: 'arranged_face' },
  ];
  return { recipe: { nodes }, states: { first: 'failed', other: 'stale' }, results: {} };
}

test('All built faces includes unavailable intent instead of filtering ready previews', () => {
  const state = fixture(), plan = exportScopePlan(state, { scope: 'all_faces' });
  assert.deepEqual(plan.faceIds, ['manual', 'first', 'second', 'other']);
  assert.deepEqual(plan.payload, { scope: 'all_faces' });
  assert.deepEqual(plan.roots, ['manual', 'first', 'second', 'other', 'owner', 'nested', 'reusedOnly']);
});

test('Selected faces expands Build faces children and reused faces, once in recipe order', () => {
  const state = fixture(), selected = new Set(['second', 'owner', 'manual']);
  assert.deepEqual(exportFaceIds(state.recipe, selected), ['manual', 'first', 'second']);
  const plan = exportScopePlan(state, { scope: 'selected_faces', selected });
  assert.deepEqual(plan.payload, { scope: 'selected_faces', targets: ['manual', 'first', 'second'], review_owners: ['owner'] });
  assert.deepEqual(plan.roots, ['manual', 'first', 'second', 'owner', 'nested']);
});

test('a reused-only selected owner remains a required validation root', () => {
  const plan = exportScopePlan(fixture(), { scope: 'selected_faces', selected: new Set(['reusedOnly']) });
  assert.deepEqual(plan.faceIds, ['manual']);
  assert.deepEqual(plan.roots, ['manual', 'reusedOnly']);
  assert.deepEqual(plan.payload.review_owners, ['reusedOnly']);
});

test('a selected face requires its owner, not unrelated owners that reuse the face', () => {
  assert.deepEqual(exportScopePlan(fixture(), { scope: 'selected_faces', selected: new Set(['first']) }).roots,
    ['first', 'owner']);
  assert.deepEqual(exportScopePlan(fixture(), { scope: 'selected_faces', selected: new Set(['manual']) }).roots,
    ['manual']);
});

test('selecting fits does not implicitly export their dependencies or built faces', () => {
  assert.throws(() => exportScopePlan(fixture(), { scope: 'selected_faces', selected: new Set(['fit']) }),
    /Select faces or Build faces groups/);
  assert.deepEqual(exportScopePlan(fixture(), { scope: 'target', target: 'fit' }).payload,
    { scope: 'target', target: 'fit' });
});

test('empty and invalid scopes stop before readiness or file generation', () => {
  assert.throws(() => exportScopePlan({ recipe: { nodes: [] } }, { scope: 'all_faces' }), /No built faces/);
  assert.throws(() => exportScopePlan(fixture(), { scope: 'target', target: 'edge' }), /Choose a fit or joint/);
  assert.throws(() => exportScopePlan(fixture(), { scope: 'invalid' }), /Choose an export scope/);
});

test('failed, blocked and open inputs are named; stale faces remain in the requested set', () => {
  const state = fixture();
  state.states.manual = 'ready';
  state.results.manual = { bounded: false };
  state.states.owner = 'blocked';
  const plan = exportScopePlan(state, { scope: 'all_faces' });
  assert.deepEqual(exportInputIssues(state, plan), ['manual (open)', 'first (failed)', 'owner (blocked)']);
  assert(plan.faceIds.includes('other'));
  const independent = exportScopePlan(state, { scope: 'selected_faces', selected: new Set(['other']) });
  assert.deepEqual(exportInputIssues(state, independent), []);
});

test('solid export requires one Body, not implicit assembly of all faces', () => {
  const state = fixture();
  state.recipe.nodes.push({ id: 'body', label: 'My body', operation: 'body', faces: ['manual'] });
  const plan = exportScopePlan(state, { scope: 'body', target: 'body' });
  assert.deepEqual(plan, { faceIds: [], roots: ['body'], payload: { scope: 'body', target: 'body' } });
  assert.throws(() => exportScopePlan(state, { scope: 'body', target: 'manual' }), /Choose a Body/);
  state.states.body = 'ready';
  assert.deepEqual(exportInputIssues(state, plan), ['My body (not a validated solid)']);
  state.results.body = { valid: true };
  assert.deepEqual(exportInputIssues(state, plan), []);
  state.states.body = 'stale';
  assert.deepEqual(exportInputIssues(state, plan), []);
  state.states.body = 'failed';
  assert.deepEqual(exportInputIssues(state, plan), ['My body (failed)']);
});
