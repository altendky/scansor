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
    input_contracts: { relationship_plane_orientation: { capabilities: ['plane_orientation'], min_count: 2 } },
    relationship_contracts: { parallel_planes: { operation: 'plane_relationship', relation: 'parallel', roles: { participants: 'relationship_plane_orientation' } } },
    evaluation_running: false, required_failures: [] };
}
const accepted = value => ({ generation: 3, recipe: value.recipe, token: value.token });
const snapshotKeys = ['scene', 'scene_replayed', 'scene_actions', 'scene_actions_replayed', 'native_scene'];
const graphs = () => Object.fromEntries(snapshotKeys.map(key => [key, graph()]));

test('fixed source projection retains every semantic field and leaves raw transport evidence intact', () => {
  const original = graph(), replayed = graph(), native = graph();
  delete native.project_generation; delete native.required_failures; delete native.evaluation_running;
  const snapshots = { ...graphs(), scene: original, scene_replayed: replayed, native_scene: native };
  const before = structuredClone(snapshots), cases = sourceSemanticCases(snapshots);
  assert.equal(SOURCE_GRAPH_SCHEMA, 'scansor-source-graph-semantic-v3');
  assert.deepEqual(cases.map(item => [item.id, item.profile, item.path]), [
    ['scene', 'graph-snapshot', 'scene.graph'], ['scene-replayed', 'graph-snapshot', 'scene.replayed_graph'],
    ['scene-actions', 'graph-snapshot', 'actions.scene_opened'],
    ['scene-actions-replayed', 'graph-snapshot', 'actions.scene_replayed'],
  ]);
  for (const item of cases) {
    assert.deepEqual(Object.keys(item.actual), SOURCE_GRAPH_PROJECTION);
    assert.deepEqual(item.actual, item.expected);
  }
  assert.deepEqual(snapshots, before);
  assert.equal(snapshots.scene.project_generation, 3);
});

for (const key of SOURCE_GRAPH_PROJECTION) test('missing semantic field rejects on either side: ' + key, () => {
  for (const side of snapshotKeys) {
    const snapshots = graphs();
    delete snapshots[side][key];
    assert.throws(() => sourceSemanticCases(snapshots), /missing required semantic field/);
  }
});

for (const key of ['input_contracts', 'relationship_contracts']) test('published contract changes remain semantic: ' + key, () => {
  for (const side of snapshotKeys) {
    const snapshots = graphs();
    snapshots[side][key] = {};
    const cases = sourceSemanticCases(snapshots);
    assert.ok(cases.some(item => {
      try { compareSourceNumbers(item.actual, item.expected); return false; }
      catch { return true; }
    }));
  }
});

test('unknown published fields cannot be silently removed from either comparand', () => {
  for (const side of snapshotKeys) {
    const snapshots = graphs();
    snapshots[side].new_acceptance_classification = 'rejected';
    assert.throws(() => sourceSemanticCases(snapshots), /unknown field.*new_acceptance_classification/);
  }
  const matching = { ...graph(), new_acceptance_classification: 'rejected' };
  assert.throws(() => sourceSemanticCases(Object.fromEntries(snapshotKeys.map(key => [key, matching]))), /unknown field/);
});

test('reviewed native lifecycle remains raw transport evidence without hiding unknown classifications', () => {
  const original = graph(), expected = projectSourceGraph(original);
  const evaluation = { id: 'native-job', sequence: 1, generation: 3, kind: 'ensure', state: 'complete', roots: ['plane'] };
  const value = { ...original, evaluation }, before = structuredClone(value);
  assert.deepEqual(projectSourceGraph(value), expected);
  assert.deepEqual(projectSourceGraph({ ...original, evaluation: null }), expected);
  assert.deepEqual(value, before);
  for (const defect of [{ sequence: true }, { generation: 4 }, { kind: 'unknown' }, { state: 'unknown' },
    { roots: ['plane', 'plane'] }, { new_acceptance_classification: 'rejected' }])
    assert.throws(() => projectSourceGraph({ ...original, evaluation: { ...evaluation, ...defect } }));
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
