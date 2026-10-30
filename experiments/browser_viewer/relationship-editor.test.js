import assert from 'node:assert/strict';
import test from 'node:test';
import {createRelationshipDraft} from './relationship-editor.js';
import {inputReferenceKey} from './feature-inputs.js';
import {relationshipDefinitionForNode, relationshipDraftForNode, relationshipNodeInputs} from './relationship-inputs.js';

const reference = (feature, output, context = feature) => ({feature, output, context});
const key = (feature, output, context = feature) => inputReferenceKey(reference(feature, output, context));
function fixture() {
  const state = {recipe: {nodes: [{id: 'fit', kind: 'cylinder'}, {id: 'other', kind: 'cylinder'},
    {id: 'axis', operation: 'axis'}, {id: 'solve', operation: 'direction_alignment'}, {id: 'point', operation: 'point'}]},
  input_contracts: {direction: {min_count: 1, max_count: 33, property: 'direction'},
    fixed: {min_count: 0, max_count: 1, property: 'direction', uses: ['geometry']},
    radius: {min_count: 2, max_count: 33, property: 'radius'},
    axis: {min_count: 1, max_count: 1, property: 'axis_line'},
    members: {min_count: 1, max_count: 32, property: 'axis_line'}},
  relationship_contracts: {
    direction_alignment: {operation: 'direction_alignment', label: 'Aligned directions', base_name: 'Aligned directions',
      roles: {reference: 'fixed', participants: 'direction'}, selection_pattern: 'reference_targets', reference_role: 'reference',
      expanded_min_count: 2, role_labels: {reference: 'Same direction as (optional)', participants: 'Apply to'},
      node_fields: {reference: {name: 'reference', encoding: 'reference', multiple: false, optional: true},
        participants: {name: 'directions', encoding: 'reference', multiple: true}}, node_values: {sense: 'unsigned'}},
    equal_radii: {operation: 'equal_radii', label: 'Equal radii', base_name: 'Equal radii', roles: {participants: 'radius'},
      selection_pattern: 'peers', node_fields: {participants: {name: 'surfaces', encoding: 'feature', multiple: true}}},
    coaxial: {operation: 'coaxial_group', label: 'Coaxial', roles: {axis: 'axis', members: 'members'},
      selection_pattern: 'reference_targets', reference_role: 'axis', node_fields: {
        axis: {name: 'axis', encoding: 'feature', multiple: false}, members: {name: 'surfaces', encoding: 'feature', multiple: true}}},
  }, input_requirements: {}, input_catalogue: []};
  const output = (feature, name, context = feature) => ({reference: reference(feature, name, context),
    key: key(feature, name, context), label: `${feature} — ${name}`, availability: 'unevaluated', compatible: true,
    dependencies: [feature, context], parameter_owner: context === feature ? {feature, output: name} : null});
  const axis = output('axis', 'axis'), fit = output('fit', 'axis'), other = output('other', 'axis'),
    published = output('other', 'axis', 'solve'), radius = output('fit', 'radius'), otherRadius = output('other', 'radius');
  for (const [requirement, outputs] of Object.entries({direction: [fit, other, axis], fixed: [fit, axis, published],
    radius: [radius, otherRadius], axis: [axis], members: [fit, other]})) {
    state.input_requirements[requirement] = {choices: [], unavailable: outputs};
    state.input_catalogue.push(...outputs);
  }
  return state;
}

test('fit anchoring follows common role metadata and offers relations before partners exist', () => {
  const state = fixture();
  for (const record of Object.values(state.input_requirements)) record.unavailable = record.unavailable
    .filter(output => output.reference.feature === 'fit');
  const model = createRelationshipDraft({getState: () => state, anchorFeature: () => 'fit'});
  assert.deepEqual(model.definitions().map(item => item.id).sort(), ['coaxial', 'direction_alignment', 'equal_radii']);
  model.reset({kind: 'direction_alignment'});
  assert.deepEqual(model.draft.roles.participants, new Set([key('fit', 'axis')]));
  assert.equal(model.draft.roles.reference.size, 0);
  assert.equal(model.validity().valid, false, 'missing partners do not hide a supported relation');
  assert.equal(model.change('participants', key('fit', 'axis'), false), false);
  model.clear(); assert.deepEqual(model.selected, new Set([key('fit', 'axis')]));
});

test('the same draft controller serializes a fit anchor or a standalone relationship', () => {
  const state = fixture(), before = structuredClone(state),
    model = createRelationshipDraft({getState: () => state, anchorFeature: 'fit'});
  model.reset({kind: 'direction_alignment', label: 'Follow upstream'});
  assert.equal(model.change('reference', key('other', 'axis', 'solve'), true), true);
  assert.deepEqual(model.inputs(), {operation: 'direction_alignment', sense: 'unsigned',
    reference: reference('other', 'axis', 'solve'), directions: [reference('fit', 'axis')]});
  const standalone = createRelationshipDraft({getState: () => state});
  standalone.reset({kind: model.kind, draft: model.draft, label: model.label});
  assert.deepEqual(standalone.inputs(), model.inputs());
  assert.deepEqual(state, before);
  const snapshot = model.snapshot(); snapshot.draft.roles.participants.clear(); snapshot.selected.clear();
  assert.equal(model.selected.size, 2, 'snapshots cannot mutate a live draft');
});

test('saved fit anchors retain their reference or target role through refresh and Clear', () => {
  const state = fixture(), node = {operation: 'direction_alignment', sense: 'unsigned',
    reference: reference('fit', 'axis'), directions: [reference('other', 'axis')]};
  state.input_requirements.fixed.unavailable.push(state.input_requirements.direction.unavailable
    .find(output => output.reference.feature === 'other'));
  for (const anchorFeature of ['fit', 'other']) {
    const model = createRelationshipDraft({getState: () => state, anchorFeature});
    model.reset({kind: 'direction_alignment', draft: relationshipDraftForNode(state, node)});
    assert.deepEqual(model.inputs(), node, `${anchorFeature} must keep the saved endpoint role`);
    model.refresh(); assert.deepEqual(model.inputs(), node);
    model.setKind(model.kind); assert.deepEqual(model.inputs(), node);
    const role = anchorFeature === 'fit' ? 'reference' : 'participants',
      anchorKey = key(anchorFeature, 'axis');
    if (role === 'reference') {
      const alternative = model.choices(role).find(output => output.key === key('axis', 'axis'));
      assert.equal(alternative.compatible, false); assert.match(alternative.reason, /pinned/);
      assert.equal(model.change(role, alternative.key, true), false);
      assert.deepEqual(model.inputs(), node);
    }
    model.clear(role);
    assert.ok(model.draft.roles[role].has(anchorKey), 'role Clear retains the anchor in its saved role');
    assert.deepEqual(model.inputs(), node);
    model.clear(); model.refresh();
    assert.deepEqual(model.selected, new Set([anchorKey]));
    assert.ok(model.draft.roles[role].has(anchorKey), 'Clear all retains the saved role preference');
    assert.equal(model.change(role, anchorKey, false), false);
  }
});

test('changing relation remaps direct outputs but retains an unavailable exact publication', () => {
  const state = fixture(), model = createRelationshipDraft({getState: () => state, anchorFeature: 'fit'});
  model.reset({kind: 'direction_alignment'});
  model.change('reference', key('other', 'axis', 'solve'), true);
  model.setKind('equal_radii');
  assert.ok(model.selected.has(key('fit', 'radius')));
  assert.ok(model.selected.has(key('other', 'axis', 'solve')), 'the selected solve publication is never flattened');
  assert.equal(model.validity().valid, false);
  model.removeUnassigned(key('other', 'axis', 'solve'));
  model.change('participants', key('other', 'radius'), true);
  assert.deepEqual(model.inputs(), {operation: 'equal_radii', surfaces: ['fit', 'other']});
});

test('family changes preserve incompatible relation drafts and repair compatible anchor outputs', () => {
  let state = fixture();
  const model = createRelationshipDraft({getState: () => state, anchorFeature: () => 'fit'});
  model.reset({kind: 'direction_alignment'}); model.change('reference', key('axis', 'axis'), true);
  state = structuredClone(state);
  const replacement = {reference: reference('fit', 'plane'), key: key('fit', 'plane'), label: 'Plane fit',
    availability: 'unevaluated', compatible: true, dependencies: ['fit'], parameter_owner: {feature: 'fit', output: 'plane'}};
  state.recipe.nodes[0].kind = 'plane';
  state.input_requirements.direction.unavailable[0] = replacement;
  model.refresh();
  assert.ok(model.selected.has(key('fit', 'plane'))); assert.equal(model.validity().valid, true);
  state.input_requirements.direction.unavailable = state.input_requirements.direction.unavailable.filter(output => output.reference.feature !== 'fit');
  state.input_requirements.fixed.unavailable = state.input_requirements.fixed.unavailable.filter(output => output.reference.feature !== 'fit');
  model.refresh(); assert.equal(model.kind, 'direction_alignment');
  assert.ok(model.selected.has(key('fit', 'plane'))); assert.equal(model.validity().valid, false);
});

test('new point relationships work through the same controller without fit-family cases', () => {
  const state = fixture(), fit = {reference: reference('fit', 'center'), key: key('fit', 'center'),
      availability: 'unevaluated', compatible: true, dependencies: ['fit']},
    point = {reference: reference('point', 'point'), key: key('point', 'point'), availability: 'unevaluated', compatible: true, dependencies: ['point']};
  state.input_contracts.center = {min_count: 1, max_count: 32, property: 'point'};
  state.input_contracts.datum = {min_count: 1, max_count: 1, property: 'point'};
  state.input_requirements.center = {choices: [], unavailable: [fit]};
  state.input_requirements.datum = {choices: [], unavailable: [point]};
  state.relationship_contracts.shared_center = {label: 'Shared center', operation: 'shared_center_group', semantic_check: 'compatible_point',
    selection_pattern: 'reference_targets', reference_role: 'point', roles: {point: 'datum', members: 'center'},
    node_fields: {point: {name: 'point', encoding: 'feature', multiple: false}, members: {name: 'surfaces', encoding: 'feature', multiple: true}}};
  const model = createRelationshipDraft({getState: () => state, anchorFeature: 'fit'});
  model.reset({kind: 'shared_center'}); assert.ok(model.selected.has(key('fit', 'center')));
  model.change('point', key('point', 'point'), true);
  assert.deepEqual(model.inputs(), {operation: 'shared_center_group', point: 'point', surfaces: ['fit']});
  state.recipe.nodes[0].point = 'other-point';
  assert.match(model.validity().reason, /already uses another center/);
  delete state.recipe.nodes[0].point;
  state.recipe.nodes.push({id: 'other-center-group', operation: 'shared_center_group', point: 'other-point', surfaces: ['fit']});
  assert.match(model.validity().reason, /another Shared center/);
});

test('common inverse serialization preserves owner and named-publication identities', () => {
  const state = fixture(), node = {operation: 'direction_alignment', sense: 'unsigned',
    reference: reference('other', 'axis', 'solve'), directions: [reference('fit', 'axis')]};
  assert.equal(relationshipDefinitionForNode(state, node).id, 'direction_alignment');
  const draft = relationshipDraftForNode(state, node);
  assert.deepEqual(relationshipNodeInputs(state, 'direction_alignment', draft), node);
  const radii = {operation: 'equal_radii', surfaces: ['fit', 'other']};
  assert.deepEqual(relationshipNodeInputs(state, 'equal_radii', relationshipDraftForNode(state, radii)), radii);
  const invalid = relationshipDraftForNode(state, {operation: 'equal_radii', surfaces: ['fit', 'removed']});
  assert.ok(invalid.roles.participants.has('removed'));
  assert.throws(() => relationshipNodeInputs(state, 'equal_radii', invalid), /unavailable|incompatible/);
  assert.throws(() => relationshipDraftForNode(state, {operation: 'unknown'}), /not supported/);
});

test('relationship identity distinguishes signature discriminators from editable creation defaults', () => {
  const state = fixture();
  state.relationship_contracts.mirror = {label: 'Mirror symmetry', operation: 'mirror_symmetry',
    node_values: {symmetric_extents: true}, roles: {participants: 'direction'}};
  assert.equal(relationshipDefinitionForNode(state, {operation: 'mirror_symmetry', symmetric_extents: false}).id, 'mirror');
  for (const relation of ['coincident', 'parallel']) state.relationship_contracts[relation] = {
    operation: 'plane_relationship', node_values: {relation}, roles: {participants: 'direction'}};
  assert.equal(relationshipDefinitionForNode(state, {operation: 'plane_relationship', relation: 'parallel'}).id, 'parallel');
  assert.equal(relationshipDefinitionForNode(state, {operation: 'plane_relationship', relation: 'coincident'}).id, 'coincident');
  assert.equal(relationshipDefinitionForNode(state, {operation: 'plane_relationship', relation: 'unknown'}), null);
});

test('legacy reference-field strings resolve only their unique direct role output', () => {
  const state = fixture(), plane = feature => ({reference: reference(feature, 'plane'), key: key(feature, 'plane'),
    compatible: true, availability: 'unevaluated', dependencies: [feature]}),
    first = plane('fit'), second = plane('other'), publication = {...plane('fit'), reference: reference('fit', 'plane', 'solve')};
  state.input_contracts.planes = {property: 'plane_orientation', min_count: 2, max_count: 33};
  state.input_requirements.planes = {choices: [], unavailable: [first, second, publication]};
  state.relationship_contracts.parallel_planes = {operation: 'plane_relationship', node_values: {relation: 'parallel'},
    selection_pattern: 'peers', roles: {participants: 'planes'},
    node_fields: {participants: {name: 'surfaces', encoding: 'reference', multiple: true}}};
  const draft = relationshipDraftForNode(state, {operation: 'plane_relationship', relation: 'parallel', surfaces: ['fit', 'other']});
  assert.deepEqual(draft.roles.participants, new Set([key('fit', 'plane'), key('other', 'plane')]));
  assert.deepEqual(relationshipNodeInputs(state, 'parallel_planes', draft).surfaces, [reference('fit', 'plane'), reference('other', 'plane')]);
  const named = relationshipDraftForNode(state, {operation: 'plane_relationship', relation: 'parallel',
    surfaces: [reference('fit', 'plane', 'solve'), 'other']});
  assert.ok(named.roles.participants.has(key('fit', 'plane', 'solve')));
  state.input_requirements.planes.unavailable = [publication, second];
  assert.ok(relationshipDraftForNode(state, {operation: 'plane_relationship', relation: 'parallel', surfaces: ['fit', 'other']})
    .roles.participants.has('fit'), 'an absent direct owner never binds to a named publication');
});
