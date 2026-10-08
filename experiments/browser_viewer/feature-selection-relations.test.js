import assert from 'node:assert/strict';
import test from 'node:test';
import { featureSelectionRelations } from './action-tree.js';

const nodes = [
  { id: 'scan', operation: 'source' },
  { id: 'selection', operation: 'selection', source: 'scan' },
  { id: 'fit', operation: 'fit', selections: ['selection'] },
  { id: 'owner', operation: 'build_faces', surfaces: [{ feature: 'fit' }] },
  { id: 'edge', operation: 'surface_intersection', managed_by: 'owner',
    first: { feature: 'fit' }, second: { feature: 'fit' } },
  { id: 'face', operation: 'trimmed_face', managed_by: 'owner',
    surface: { feature: 'fit' }, boundaries: [{ intersection: 'edge' }] },
  { id: 'body', operation: 'body', faces: ['face'] },
  { id: 'unrelated', operation: 'point' },
];
const sorted = ids => [...ids].sort();

test('selection relationships include indirect inputs and dependents without unrelated features', () => {
  const before = structuredClone(nodes), result = featureSelectionRelations(nodes, new Set(['fit']));
  assert.deepEqual(sorted(result.inputs), ['scan', 'selection']);
  assert.deepEqual(sorted(result.dependents), ['body', 'edge', 'face', 'owner']);
  assert.deepEqual(nodes, before);
});

test('multiselection unions both closures and excludes selected roots from each', () => {
  const result = featureSelectionRelations(nodes, new Set(['selection', 'face']));
  assert.deepEqual(sorted(result.inputs), ['edge', 'fit', 'owner', 'scan']);
  assert.deepEqual(sorted(result.dependents), ['body', 'edge', 'fit', 'owner']);
  assert.deepEqual(featureSelectionRelations(nodes, new Set()), { inputs: new Set(), dependents: new Set() });
});

test('ownership does not turn consumers into inputs or include independent siblings', () => {
  const result = featureSelectionRelations(nodes, new Set(['edge']));
  assert.deepEqual(sorted(result.inputs), ['fit', 'owner', 'scan', 'selection']);
  assert.deepEqual(sorted(result.dependents), ['body', 'face']);
  const owner = featureSelectionRelations(nodes, new Set(['owner']));
  assert.deepEqual(sorted(owner.dependents), ['body', 'edge', 'face']);
});

test('exact output publication contexts are included in either direction', () => {
  const published = [
    { id: 'point', operation: 'point' },
    { id: 'solve', operation: 'joint_fit', constraints: ['point'] },
    { id: 'axis', operation: 'axis', source_points: [
      { feature: 'point', context: '@point/solve', output: 'point' },
    ] },
  ];
  assert.deepEqual(sorted(featureSelectionRelations(published, new Set(['axis'])).inputs), ['point', 'solve']);
  assert.deepEqual(sorted(featureSelectionRelations(published, new Set(['solve'])).dependents), ['axis']);
});

test('inferred generated fit ownership supplies the same ordering edge as dragging', () => {
  const generated = [
    ...nodes.slice(0, 3),
    { id: 'reuse', operation: 'feature_reuse', fits: ['fit'], lineage: [],
      reference_selection: 'selection', target_selections: ['selection'] },
    { id: 'reused-selection', operation: 'reuse_selection', reuse: 'reuse', fit: 'fit',
      source_selection: 'selection', target_selection: 'selection' },
    { id: 'reused-fit', operation: 'fit', selections: ['reused-selection'] },
  ];
  assert.deepEqual(sorted(featureSelectionRelations(generated, new Set(['reuse'])).dependents), ['reused-fit', 'reused-selection']);
  assert.ok(featureSelectionRelations(generated, new Set(['reused-fit'])).inputs.has('reuse'));
});

test('unknown selections, missing references and cycles cannot leak IDs or loop forever', () => {
  const cyclic = [
    { id: 'one', operation: 'joint_fit', constraints: ['two', 'missing'] },
    { id: 'two', operation: 'joint_fit', constraints: ['one'] },
  ];
  const result = featureSelectionRelations(cyclic, new Set(['one', 'gone']));
  assert.deepEqual(sorted(result.inputs), ['two']);
  assert.deepEqual(sorted(result.dependents), ['two']);
});
