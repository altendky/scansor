import test from 'node:test';
import assert from 'node:assert/strict';
import { SOURCE_GRAPH_SCHEMA, SOURCE_GRAPH_PROJECTION, projectSourceGraph,
  assertAcceptedSourceGraph, sourceSemanticCases } from './source-semantic.mjs';
import { compareSourceNumbers } from './source-import-harness.mjs';

function graph() {
  return { recipe: { nodes: [{ id: 'source', operation: 'source' }, { id: 'plane', operation: 'fit' }] },
    token: 'authored-token', states: { source: 'ready', plane: 'ready' }, errors: {}, diagnostics: {},
    memberships: {}, results: { plane: { weighted_rms: 0 } }, result: null, derived: {},
    input_catalogue: [], input_requirements: {}, revision: 7, project_generation: 3,
    evaluation_running: false, required_failures: [] };
}
const accepted = value => ({ generation: 3, recipe: value.recipe, token: value.token });

test('fixed source projection retains every semantic field and leaves raw transport evidence intact', () => {
  const original = graph(), replayed = graph(), native = graph();
  delete native.project_generation; delete native.required_failures; delete native.evaluation_running;
  const snapshots = { scene: original, scene_replayed: replayed, native_scene: native };
  const before = structuredClone(snapshots), cases = sourceSemanticCases(snapshots);
  assert.equal(SOURCE_GRAPH_SCHEMA, 'scansor-source-graph-semantic-v1');
  assert.deepEqual(cases.map(item => [item.id, item.profile, item.path]), [
    ['scene', 'graph-snapshot', 'scene.graph'], ['scene-replayed', 'graph-snapshot', 'scene.replayed_graph'],
  ]);
  for (const item of cases) {
    assert.deepEqual(Object.keys(item.actual), SOURCE_GRAPH_PROJECTION);
    assert.deepEqual(item.actual, item.expected);
  }
  assert.deepEqual(snapshots, before);
  assert.equal(snapshots.scene.project_generation, 3);
});

for (const key of SOURCE_GRAPH_PROJECTION) test('missing semantic field rejects on either side: ' + key, () => {
  for (const side of ['scene', 'scene_replayed', 'native_scene']) {
    const snapshots = { scene: graph(), scene_replayed: graph(), native_scene: graph() };
    delete snapshots[side][key];
    assert.throws(() => sourceSemanticCases(snapshots), /missing required semantic field/);
  }
});

test('unknown published fields cannot be silently removed from either comparand', () => {
  for (const side of ['scene', 'scene_replayed', 'native_scene']) {
    const snapshots = { scene: graph(), scene_replayed: graph(), native_scene: graph() };
    snapshots[side].new_acceptance_classification = 'rejected';
    assert.throws(() => sourceSemanticCases(snapshots), /unknown field.*new_acceptance_classification/);
  }
  const matching = { ...graph(), new_acceptance_classification: 'rejected' };
  assert.throws(() => sourceSemanticCases({ scene: matching, scene_replayed: matching, native_scene: matching }), /unknown field/);
});

test('accepted graph independently checks its source transport epoch and preserves recipe/token', () => {
  const value = graph();
  assertAcceptedSourceGraph(value, accepted(value));
  for (const generation of [2, 4, '3', null]) {
    assert.throws(() => assertAcceptedSourceGraph({ ...value, project_generation: generation }, accepted(value)), /generation/);
  }
  assert.throws(() => assertAcceptedSourceGraph(value, { ...accepted(value), generation: NaN }), /finite integer/);
  assert.throws(() => assertAcceptedSourceGraph({ ...value, token: 'stale' }, accepted(value)), /token/);
  assert.throws(() => assertAcceptedSourceGraph({ ...value, recipe: { nodes: [] } }, accepted(value)), /recipe/);
});

for (const [name, patch] of [
  ['running', { evaluation_running: true }], ['missing running', { evaluation_running: undefined }],
  ['failed closure', { required_failures: ['plane'] }], ['missing closure', { required_failures: undefined }],
  ['error', { errors: { plane: 'failed' } }], ['failed state', { states: { source: 'ready', plane: 'failed' } }],
  ['unevaluated state', { states: { source: 'ready', plane: 'unevaluated' } }], ['empty states', { states: {} }],
  ['evaluation error', { evaluation_error: { code: 'failure' } }],
]) test('source acceptance failure cannot be hidden by matching projected graphs: ' + name, () => {
  const value = graph();
  assert.throws(() => assertAcceptedSourceGraph({ ...value, ...patch }, accepted(value)));
});

test('source array comparisons retain their strict numeric, identity and shape checks', () => {
  assert.equal(compareSourceNumbers([1, 2], [1, 2]).max_difference, 0);
  for (const actual of [[1, 2.000001], [1], [1, Infinity], [1, '2']]) {
    assert.throws(() => compareSourceNumbers(actual, [1, 2]));
  }
  assert.throws(() => compareSourceNumbers({ source_sha256: 'changed' }, { source_sha256: 'original' }));
  assert.throws(() => projectSourceGraph(null));
});
