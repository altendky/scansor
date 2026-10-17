import assert from 'node:assert/strict';
import test from 'node:test';
import { inputReferenceKey } from './feature-inputs.js';
import { relationshipParticipantChoices, relationshipSelectionForFeatures, relationshipValidity,
  relationshipNodeInputs, relationshipOutputValue } from './relationship-inputs.js';
import { axisFactorAxis, axisFactorMembers, axisSolveInputs, axisSolveValidity } from './relationship-inputs.js';

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

function coaxialState() {
  const axis = (feature, ownership, geometry_kind, extra = {}) => ({
    ...plane(feature), reference: { feature, output: 'axis', context: feature }, ownership, geometry_kind, ...extra,
  });
  return { recipe: { nodes: ['axis', 'outer', 'inner'].map(id => ({ id })) },
    relationship_contracts: { coaxial: { operation: 'coaxial_group', roles: { axis: 'datum', members: 'members' }, semantic_check: 'compatible_axis' } },
    input_contracts: { datum: { min_count: 1, max_count: 1 }, members: { min_count: 2, max_count: 32 } },
    input_requirements: { datum: { choices: [axis('axis', 'datum', 'axis')], unavailable: [] },
      members: { choices: [axis('outer', 'fit', 'cone'), axis('inner', 'fit', 'cylinder')], unavailable: [] } } };
}

test('Coaxial authors one axis and mixed cone/cylinder owners without changing declarations', () => {
  const graph = coaxialState(), before = structuredClone(graph.recipe), selected = relationshipSelectionForFeatures(graph, 'coaxial', ['axis', 'outer', 'inner']);
  assert.deepEqual(relationshipNodeInputs(graph, 'coaxial', selected), { operation: 'coaxial_group', axis: 'axis', surfaces: ['outer', 'inner'] });
  assert.deepEqual(graph.recipe, before);
  graph.input_requirements.members.choices[1].axis = 'other-axis';
  assert.match(relationshipValidity(graph, 'coaxial', selected).reason, /another axis/);
});

test('Coaxial retains unavailable exact saved outputs and rejects fixed named contexts', () => {
  const graph = coaxialState(), selected = relationshipSelectionForFeatures(graph, 'coaxial', ['axis', 'outer', 'inner']),
    published = inputReferenceKey({ feature: 'outer', output: 'axis', context: 'old-solve' });
  selected.add(published);
  assert.equal(relationshipValidity(graph, 'coaxial', selected).valid, false);
  assert.equal(relationshipParticipantChoices(graph, 'coaxial', selected).find(output => output.key === published).compatible, false);
});

test('editing a Coaxial axis excludes its old declaration while retaining real group conflicts', () => {
  const graph = coaxialState(), other = { ...graph.input_requirements.datum.choices[0],
    reference: { feature: 'other-axis', output: 'axis', context: 'other-axis' } };
  graph.recipe.nodes.push({ id: 'other-axis' }, { id: 'edited', operation: 'coaxial_group', axis: 'old-axis', surfaces: ['outer', 'inner'] });
  graph.input_requirements.datum.choices.push(other);
  const selected = relationshipSelectionForFeatures(graph, 'coaxial', ['other-axis', 'outer', 'inner']),
    earlier = graph.recipe.nodes.filter(node => node.id !== 'edited');
  assert.equal(relationshipValidity(graph, 'coaxial', selected).valid, false);
  assert.equal(relationshipNodeInputs(graph, 'coaxial', selected, earlier).axis, 'other-axis');
  earlier.push({ id: 'other-group', operation: 'coaxial_group', axis: 'old-axis', surfaces: ['outer', 'inner'] });
  assert.throws(() => relationshipNodeInputs(graph, 'coaxial', selected, earlier), /different axis/);
});

test('Joint expands Coaxial and Parallel owner evidence, deduplicates fits and excludes initialization', () => {
  const nodes = [
    { id: 'seed', operation: 'fit', kind: 'cone' }, { id: 'axis', operation: 'axis', source_fit: 'seed' },
    { id: 'outer', operation: 'fit', kind: 'cone' }, { id: 'inner', operation: 'fit', kind: 'cylinder' },
    { id: 'base', operation: 'reference_plane', axis: 'axis' }, { id: 'cap', operation: 'fit', kind: 'plane' },
    { id: 'coaxial', operation: 'coaxial_group', axis: 'axis', surfaces: ['outer', 'inner'] },
    { id: 'parallel', operation: 'plane_relationship', relation: 'parallel', surfaces: [plane('base').reference, plane('cap').reference] },
  ];
  assert.equal(axisFactorAxis(nodes.find(node => node.id === 'outer'), nodes), 'axis');
  assert.deepEqual(axisFactorMembers(nodes.find(node => node.id === 'coaxial'), nodes).map(node => node.id), ['outer', 'inner']);
  assert.deepEqual(axisSolveInputs('axis', nodes).map(node => node.id), ['outer', 'inner', 'coaxial', 'parallel']);
  assert.equal(axisSolveValidity('axis', ['coaxial'], nodes).valid, true);
  assert.deepEqual(axisSolveValidity('axis', ['outer', 'coaxial', 'parallel'], nodes).members.map(node => node.id), ['outer', 'inner', 'cap']);
  assert.equal(axisSolveValidity('axis', ['outer'], nodes).valid, false);
  nodes.find(node => node.id === 'parallel').surfaces[1].context = 'earlier-publication';
  assert.deepEqual(axisFactorMembers(nodes.find(node => node.id === 'parallel'), nodes), []);
});
