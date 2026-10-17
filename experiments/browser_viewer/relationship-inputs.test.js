import assert from 'node:assert/strict';
import test from 'node:test';
import { inputReferenceKey } from './feature-inputs.js';
import { relationshipParticipantChoices, relationshipSelectionForFeatures, relationshipValidity,
  relationshipNodeInputs, relationshipOutputValue } from './relationship-inputs.js';

const plane = (feature, context = feature) => ({
  reference: { feature, output: 'plane', context }, label: `${feature} · ${context}`,
  availability: 'ready', compatible: true, dependencies: [feature, context],
  parameter_owner: context === feature ? { feature, output: 'plane', kind: 'datum' } : null,
  preview: { normal_display: [0, 0, 1], plane_equation: [0, 0, 1, 0], point_display: [0, 0, 0] },
});
function state() {
  return { recipe: { nodes: ['datum', 'other', 'fit', 'solved', 'missing-fit'].map(id => ({ id })) },
    relationship_contracts: {
      parallel_planes: { label: 'Parallel planes', operation: 'plane_relationship', relation: 'parallel', roles: { participants: 'planes' } },
      coincident_planes: { operation: 'plane_relationship', relation: 'coincident', roles: { participants: 'planes' } },
    }, input_contracts: { planes: { min_count: 2, max_count: 33 } },
    input_requirements: { planes: { choices: [plane('datum'), plane('other'), plane('fit'), plane('fit', 'solved')], unavailable: [] } },
    derived: { fit: { plane_equation: [0, 0, 1, 10], ids: [1, 2] },
      solved: { resolved: { fit: { plane_equation: [1, 0, 0, 20], ids: [3, 4] } } } } };
}

test('multiple datums and fit outputs use one relationship signature and retain exact contexts', () => {
  const graph = state(), selected = new Set([plane('datum'), plane('other'), plane('fit', 'solved')]
    .map(output => inputReferenceKey(output.reference)));
  assert.equal(relationshipValidity(graph, 'parallel_planes', selected).valid, true);
  assert.deepEqual(relationshipNodeInputs(graph, 'parallel_planes', selected), {
    operation: 'plane_relationship', relation: 'parallel',
    surfaces: [plane('datum').reference, plane('other').reference, plane('fit', 'solved').reference],
  });
  assert.equal(relationshipNodeInputs(graph, 'coincident_planes', selected).relation, 'coincident');
});

test('eligibility comes from published backend requirements without operation whitelists', () => {
  const graph = state();
  graph.recipe.nodes.forEach(node => { node.operation = 'future_provider'; });
  assert.equal(relationshipParticipantChoices(graph, 'parallel_planes').length, 4);
  graph.input_requirements.planes.unavailable.push({ ...plane('missing-fit'), availability: 'failed', compatible: false, reason: 'Repair its provider' });
  const key = inputReferenceKey(plane('missing-fit').reference), selected = new Set([key]);
  const retained = relationshipParticipantChoices(graph, 'parallel_planes', selected).find(output => output.key === key);
  assert.equal(retained.compatible, false);
  assert.equal(relationshipValidity(graph, 'parallel_planes', selected).valid, false);
});

test('a lost saved publication stays selected and cannot rebind to another ready context', () => {
  const graph = state(), saved = inputReferenceKey(plane('fit', 'removed-solve').reference), selected = new Set([saved]);
  const unavailable = relationshipParticipantChoices(graph, 'parallel_planes', selected).find(output => output.key === saved);
  assert.equal(unavailable.compatible, false);
  assert.equal(unavailable.reference.context, 'removed-solve');
  assert.equal(relationshipOutputValue(graph, unavailable), null);
  assert.throws(() => relationshipNodeInputs(graph, 'parallel_planes', selected), /unavailable/);
});

test('tree preselection chooses declared owners without selecting solved contexts', () => {
  const selected = relationshipSelectionForFeatures(state(), 'parallel_planes', new Set(['datum', 'fit']));
  assert.deepEqual([...selected], [inputReferenceKey(plane('datum').reference), inputReferenceKey(plane('fit').reference)]);
});

test('previews read the exact chosen context rather than the latest feature result', () => {
  const graph = state();
  assert.deepEqual(relationshipOutputValue(graph, plane('fit')).ids, [1, 2]);
  assert.deepEqual(relationshipOutputValue(graph, plane('fit', 'solved')).ids, [3, 4]);
  delete graph.derived.solved;
  assert.deepEqual(relationshipOutputValue(graph, plane('fit', 'solved')), plane('fit', 'solved').preview);
});

test('later contexts remain ineligible even when the physical feature appears earlier', () => {
  const graph = state(), selected = new Set([inputReferenceKey(plane('fit', 'solved').reference)]),
    earlier = graph.recipe.nodes.filter(node => node.id !== 'solved');
  assert.equal(relationshipValidity(graph, 'parallel_planes', selected, earlier).valid, false);
});

test('cardinality is read from the backend contract instead of duplicated in validation', () => {
  const graph = state(), selected = new Set([inputReferenceKey(plane('datum').reference)]);
  assert.equal(relationshipValidity(graph, 'parallel_planes', selected).valid, false);
  graph.input_contracts.planes.min_count = 1;
  assert.equal(relationshipValidity(graph, 'parallel_planes', selected).valid, true);
});
