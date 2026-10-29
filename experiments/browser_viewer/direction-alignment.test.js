import assert from 'node:assert/strict';
import test from 'node:test';
import {actionMove, featureDeletionPlan, nodeReferences} from './action-tree.js';

const reference = (feature, output, context = feature) => ({feature, output, context});

function recipe() {
  return {nodes: [
    {id: 'source', label: 'Source', operation: 'source'},
    {id: 'observations', label: 'Observations', operation: 'selection', source: 'source'},
    {id: 'cylinder', label: 'Cylinder', operation: 'fit', kind: 'cylinder', selections: ['observations']},
    {id: 'plane', label: 'Plane', operation: 'fit', kind: 'plane', selections: ['observations']},
    {id: 'publication', label: 'Earlier publication', operation: 'joint_fit', constraints: ['plane']},
    {id: 'aligned', label: 'Aligned directions', operation: 'direction_alignment', sense: 'unsigned',
      directions: [reference('cylinder', 'axis'), reference('plane', 'plane', 'publication')]},
    {id: 'joint', label: 'Direction joint', operation: 'joint_fit', constraints: ['aligned', 'cylinder']},
  ], output: 'joint'};
}

test('direction tree dependencies retain physical owners and exact publication contexts once', () => {
  const selected = recipe().nodes.find(node => node.id === 'aligned'), before = structuredClone(selected);
  assert.deepEqual(nodeReferences(selected), ['cylinder', 'plane', 'publication']);
  assert.deepEqual(selected, before);
  assert.deepEqual(nodeReferences({...selected, directions: [reference('plane', 'plane', 'publication'),
    reference('cylinder', 'axis', 'publication')]}), ['plane', 'publication', 'cylinder']);
  assert.deepEqual(nodeReferences({...selected, directions: [reference('cylinder', 'axis', '@axis/cylinder'),
    reference('plane', 'plane')]}), ['cylinder', 'plane']);
});

test('deleting a named publication removes direction consumers and their joint while keeping physical fits', () => {
  const original = recipe(), before = structuredClone(original), deleted = featureDeletionPlan(original, ['publication']);
  assert.deepEqual(deleted.dependents.map(node => node.id), ['aligned', 'joint']);
  assert.deepEqual(deleted.recipe.nodes.map(node => node.id), ['source', 'observations', 'cylinder', 'plane']);
  assert.equal(deleted.recipe.output, 'plane');
  assert.deepEqual(original, before);
});

test('deleting either direction owner removes the relation and joint without deleting independent evidence', () => {
  const original = recipe();
  for (const id of ['cylinder', 'plane']) {
    const deleted = featureDeletionPlan(original, [id]);
    assert(deleted.removed.some(node => node.id === 'aligned'));
    assert(deleted.removed.some(node => node.id === 'joint'));
    assert(deleted.recipe.nodes.some(node => node.id === 'observations'));
    assert(deleted.recipe.nodes.some(node => node.id === (id === 'plane' ? 'cylinder' : 'plane')));
  }
});

test('reordering cannot move a direction consumer ahead of its selected publication context', () => {
  const original = recipe(), alignedIndex = original.nodes.findIndex(node => node.id === 'aligned');
  assert.match(actionMove(original.nodes, 'publication', alignedIndex + 1).error, /Aligned directions needs Earlier publication earlier/);
});
