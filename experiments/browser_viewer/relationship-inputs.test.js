import assert from 'node:assert/strict';
import test from 'node:test';
import { inputReferenceKey } from './feature-inputs.js';
import { relationshipParticipantChoices, relationshipSelectionForFeatures, relationshipValidity,
  relationshipNodeInputs, relationshipOutputValue, relationshipPropertySummary } from './relationship-inputs.js';
import { axisFactorAxis, axisFactorMembers, axisSolveInputs, axisSolveValidity } from './relationship-inputs.js';
import { directionAlignmentLabel, directionJointInputs, directionJointValidity, isDirectionJoint,
  relationshipDefinitions } from './relationship-inputs.js';

test('relationship property descriptions follow the same role contracts as eligibility', () => {
  const state = {input_contracts: {orientation: {property: 'plane_orientation'}, plane: {property: 'plane'},
    repeated: {property: 'plane_orientation'}}};
  assert.equal(relationshipPropertySummary(state, {requirements: ['orientation', 'plane', 'repeated']}), 'plane orientation, plane');
  assert.equal(relationshipPropertySummary(state, {requirements: ['unknown']}), '');
});

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

const directionOutput = (feature, projection, {context = feature, output = projection === 'plane_normal' ? 'plane' : 'axis',
  binding = true, properties = {}, ...extra} = {}) => ({
  reference: {feature, output, context}, label: `${feature} — ${output} — ${context}`,
  availability: 'ready', compatible: true, reason: null, dependencies: [...new Set([feature, context])],
  parameter_owner: binding && context === feature ? {feature, kind: 'fit'} : null,
  properties: {direction: {projection, binding_eligible: binding}, ...properties}, ...extra,
});

function directionState() {
  const cylinder = directionOutput('cylinder', 'axis_direction', {properties: {radius: {binding_eligible: true}}}),
    cap = directionOutput('cap', 'plane_normal'),
    datum = directionOutput('datum', 'axis_direction', {binding: false}),
    solved = directionOutput('cap', 'plane_normal', {context: 'publication', binding: false}),
    sphere = directionOutput('sphere', 'future_projection', {output: 'radius', binding: false,
      properties: {radius: {binding_eligible: true}}}),
    readOnly = directionOutput('read-only', 'axis_direction', {binding: false});
  return {
    recipe: {nodes: [
      {id: 'source', operation: 'source'},
      {id: 'cylinder', operation: 'fit', kind: 'cylinder', axis: null},
      {id: 'cap', operation: 'fit', kind: 'plane', reference_plane: null},
      {id: 'sphere', operation: 'fit', kind: 'sphere', point: null},
      {id: 'read-only', operation: 'fit', kind: 'cylinder'},
      {id: 'datum', operation: 'axis', source_fit: 'cylinder'},
      {id: 'publication', operation: 'joint_fit', constraints: []},
      {id: 'aligned', operation: 'direction_alignment', directions: [cylinder.reference, cap.reference], sense: 'unsigned'},
      {id: 'parallel', operation: 'plane_relationship', relation: 'parallel', surfaces: []},
      {id: 'radii', operation: 'equal_radii', surfaces: []},
      {id: 'mirror', operation: 'mirror_symmetry', surfaces: []},
    ]},
    relationship_contracts: {
      direction_alignment: {operation: 'direction_alignment', label: 'Aligned directions', base_name: 'Aligned directions',
        joint_modes: ['direction'], roles: {participants: 'arbitrary_direction_requirement'}},
      parallel_planes: {operation: 'plane_relationship', label: 'Parallel planes', base_name: 'Parallel planes',
        joint_modes: ['direction'], roles: {participants: 'arbitrary_orientation_requirement'}},
      equal_radii: {operation: 'equal_radii', joint_modes: ['direction'], roles: {participants: 'arbitrary_radius_requirement'}},
      mirror: {operation: 'mirror_symmetry', roles: {members: 'arbitrary_orientation_requirement'}},
    },
    input_contracts: {
      arbitrary_direction_requirement: {property: 'direction', min_count: 2, max_count: 2},
      arbitrary_orientation_requirement: {property: 'plane_orientation', min_count: 2, max_count: 32},
      arbitrary_radius_requirement: {property: 'radius', min_count: 2, max_count: 32},
    },
    input_catalogue: [cylinder, cap, sphere, datum, solved, readOnly],
    input_requirements: {
      arbitrary_direction_requirement: {choices: [cylinder, cap, datum, solved, readOnly], unavailable: []},
      arbitrary_orientation_requirement: {choices: [], unavailable: []},
      arbitrary_radius_requirement: {choices: [cylinder, sphere], unavailable: []},
    },
    derived: {cap: {plane_equation: [0, 0, 1, 2]}, publication: {resolved: {cap: {plane_equation: [1, 0, 0, 7]}}}},
  };
}

test('direction labels distinguish normal and axis meanings using declared projections', () => {
  const axis = directionOutput('axis', 'axis_direction'), planeNormal = directionOutput('plane', 'plane_normal');
  assert.equal(directionAlignmentLabel([axis, axis]), 'Parallel axis directions');
  assert.equal(directionAlignmentLabel([planeNormal, planeNormal]), 'Parallel plane normals');
  assert.equal(directionAlignmentLabel([axis, planeNormal]), 'Axis perpendicular to plane');
  assert.equal(directionAlignmentLabel([planeNormal, axis]), 'Axis perpendicular to plane');
  for (const outputs of [[], [axis], [axis, axis, axis], [axis, {}],
    [axis, directionOutput('future', 'future_projection')]]) {
    assert.equal(directionAlignmentLabel(outputs), 'Aligned directions');
  }
  // Labels and owner kinds cannot substitute for the declared property meaning.
  assert.equal(directionAlignmentLabel([{...axis, label: 'A plane', geometry_kind: 'plane'},
    {...planeNormal, label: 'An axis', geometry_kind: 'axis'}]), 'Axis perpendicular to plane');
});

test('adding direction relationships preserves stable legacy defaults and metadata order', () => {
  const legacy = [
    ['coincident', {operation: 'plane_relationship', base_name: 'Coincident planes'}],
    ['parallel', {operation: 'plane_relationship', base_name: 'Parallel planes'}],
    ['radii', {operation: 'equal_radii', base_name: 'Equal radii'}],
  ];
  for (const insertion of [0, 1, legacy.length]) {
    const entries = [...legacy];
    entries.splice(insertion, 0, ['directions', {operation: 'direction_alignment', base_name: 'Aligned directions'}]);
    const contracts = Object.fromEntries(entries), before = structuredClone(contracts),
      definitions = relationshipDefinitions({relationship_contracts: contracts});
    assert.deepEqual(definitions.map(item => item.id), ['coincident', 'parallel', 'radii', 'directions']);
    assert.equal(definitions[0].baseName, 'Coincident planes');
    assert.deepEqual(contracts, before);
  }
  assert.deepEqual(relationshipDefinitions({}), []);
});

test('direction serialization retains exact solved contexts and fixed read-only references', () => {
  const graph = directionState(), before = structuredClone(graph),
    datum = graph.input_catalogue.find(output => output.reference.feature === 'datum'),
    solved = graph.input_catalogue.find(output => output.reference.context === 'publication'),
    selected = new Set([inputReferenceKey(solved.reference), inputReferenceKey(datum.reference)]),
    result = relationshipNodeInputs(graph, 'direction_alignment', selected);
  assert.deepEqual(result, {operation: 'direction_alignment', sense: 'unsigned',
    directions: [datum.reference, solved.reference]});
  assert.equal(datum.properties.direction.binding_eligible, false);
  assert.equal(solved.parameter_owner, null);
  assert.deepEqual(relationshipOutputValue(graph, solved), {plane_equation: [1, 0, 0, 7]});
  assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
  assert.deepEqual(graph, before);
});

test('direction cardinality and availability follow backend contracts and cannot rebind a stale publication', () => {
  const graph = directionState(), choices = graph.input_requirements.arbitrary_direction_requirement.choices,
    direct = inputReferenceKey(choices[1].reference), saved = inputReferenceKey(choices[3].reference),
    datum = inputReferenceKey(choices[2].reference);
  assert.equal(relationshipValidity(graph, 'direction_alignment', new Set([direct])).valid, false);
  assert.equal(relationshipValidity(graph, 'direction_alignment', new Set([direct, datum, saved])).valid, false);
  graph.input_contracts.arbitrary_direction_requirement.max_count = 3;
  assert.equal(relationshipValidity(graph, 'direction_alignment', new Set([direct, datum, saved])).valid, true);
  choices.splice(3, 1);
  const selected = new Set([saved, datum]), retained = relationshipParticipantChoices(graph, 'direction_alignment', selected)
    .find(output => output.key === saved);
  assert.equal(retained.reference.context, 'publication');
  assert.equal(retained.compatible, false);
  assert.throws(() => relationshipNodeInputs(graph, 'direction_alignment', selected), /unavailable/);
  assert.notEqual(saved, direct);
});

test('direction references require their exact earlier context even when the underlying fit is earlier', () => {
  const graph = directionState(), datum = graph.input_catalogue.find(output => output.reference.feature === 'datum'),
    solved = graph.input_catalogue.find(output => output.reference.context === 'publication'),
    selected = new Set([inputReferenceKey(datum.reference), inputReferenceKey(solved.reference)]),
    earlier = graph.recipe.nodes.filter(node => node.id !== 'publication');
  assert.equal(relationshipValidity(graph, 'direction_alignment', selected, earlier).valid, false);
  assert.throws(() => relationshipNodeInputs(graph, 'direction_alignment', selected, earlier), /unavailable/);
  assert.equal(relationshipValidity(graph, 'direction_alignment', selected).valid, true);
});

test('direction Joint inputs use advertised modes and property owners, including radius-only fits', () => {
  const graph = directionState(), before = structuredClone(graph),
    ids = () => directionJointInputs(graph).map(node => node.id);
  assert.deepEqual(ids(), ['cylinder', 'cap', 'sphere', 'aligned', 'parallel', 'radii']);
  assert.deepEqual(graph, before);
  graph.relationship_contracts.equal_radii.joint_modes = [];
  assert.deepEqual(ids(), ['cylinder', 'cap', 'aligned', 'parallel']);
  graph.relationship_contracts.custom = {operation: 'future_relation', joint_modes: ['direction'], roles: {inputs: 'custom_scalar'}};
  graph.input_contracts.custom_scalar = {property: 'custom_owned_property'};
  graph.recipe.nodes.push({id: 'new-fit', operation: 'fit', kind: 'future_supported_family'}, {id: 'new-relation', operation: 'future_relation'});
  graph.input_catalogue.push({reference: {feature: 'new-fit', output: 'value', context: 'new-fit'},
    properties: {custom_owned_property: {binding_eligible: true}}});
  assert.deepEqual(ids(), ['cylinder', 'cap', 'aligned', 'parallel', 'new-fit', 'new-relation']);
  assert.deepEqual(directionJointInputs(graph, graph.recipe.nodes.slice(0, 3)).map(node => node.id), ['cylinder', 'cap']);
});

test('a weak direct fit can participate in joint recovery while read-only solved geometry cannot become an observation owner', () => {
  const graph = directionState();
  const cap = graph.input_catalogue.find(output => output.reference.feature === 'cap' && output.reference.context === 'cap');
  Object.assign(cap, {availability: 'failed', reason: 'Standalone plane has insufficient coverage'});
  graph.input_catalogue.push(directionOutput('read-only', 'axis_direction', {context: 'publication', binding: true}));
  assert(directionJointInputs(graph).some(node => node.id === 'cap'));
  assert(!directionJointInputs(graph).some(node => node.id === 'read-only'));
  const selected = new Set([inputReferenceKey(cap.reference), inputReferenceKey(graph.input_catalogue[0].reference)]);
  assert.equal(relationshipValidity(graph, 'direction_alignment', selected).valid, false);
  assert.equal(directionJointValidity(graph, ['aligned', 'cap']).valid, true);
});

test('direction Joint validity requires an advertised direction relation and compatible earlier factor IDs', () => {
  const graph = directionState();
  assert.deepEqual(directionJointValidity(graph, ['aligned']), {valid: true});
  assert.deepEqual(directionJointValidity(graph, ['cylinder', 'parallel', 'aligned', 'radii']), {valid: true});
  assert.match(directionJointValidity(graph, ['cylinder', 'parallel']).reason, /Include an aligned-directions/);
  for (const ids of [[], ['aligned', 'source'], ['aligned', 'datum'], ['aligned', 'mirror'], ['aligned', 'removed']]) {
    assert.equal(directionJointValidity(graph, ids).valid, false);
  }
  assert.equal(directionJointValidity(graph, ['aligned'], graph.recipe.nodes.filter(node => node.id !== 'aligned')).valid, false);
  assert.deepEqual(directionJointInputs({}), []);
  assert.equal(isDirectionJoint({operation: 'joint_fit', constraints: ['parallel', 'aligned']}, graph.recipe.nodes), true);
  assert.equal(isDirectionJoint({operation: 'joint_fit', constraints: ['parallel']}, graph.recipe.nodes), false);
  assert.equal(isDirectionJoint({operation: 'axis_solve', constraints: ['aligned']}, graph.recipe.nodes), false);
});
