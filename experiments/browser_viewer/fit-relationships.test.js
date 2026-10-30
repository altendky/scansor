import assert from 'node:assert/strict';
import test from 'node:test';
import {createRelationshipDraft} from './relationship-editor.js';
import {inputReferenceKey} from './feature-inputs.js';
import {stagedRelationshipState, relationshipEditorNodes, relationshipNodeFromEditor} from './fit-relationships.js';

const reference = (feature, output = 'point') => ({feature, output, context: feature});
const key = (feature, output) => inputReferenceKey(reference(feature, output));
const draft = point => ({roles: {point: new Set([key(point)]), members: new Set([key('fit', 'center')])}, unassigned: new Set()});
function fixture() {
  const output = (feature, name) => ({key: key(feature, name), reference: reference(feature, name),
    label: feature, compatible: true, availability: 'unevaluated', properties: {point: {binding_eligible: true}}});
  return {
    recipe: {nodes: [{id: 'a', operation: 'point'}, {id: 'b', operation: 'point'},
      {id: 'fit', operation: 'fit', kind: 'sphere', point: null},
      {id: 'group', label: 'Center', operation: 'shared_center_group', point: 'a', surfaces: ['fit']},
      {id: 'later', operation: 'point'}]},
    input_contracts: {center_point: {min_count: 1, max_count: 1}, center_members: {min_count: 1, max_count: 32}},
    input_requirements: {center_point: {choices: [output('a', 'point'), output('b', 'point'), output('later', 'point')], unavailable: []},
      center_members: {choices: [output('fit', 'center')], unavailable: []}},
    relationship_contracts: {shared_center: {operation: 'shared_center_group', roles: {point: 'center_point', members: 'center_members'},
      selection_pattern: 'reference_targets', reference_role: 'point', semantic_check: 'compatible_point',
      node_fields: {point: {name: 'point', encoding: 'feature', multiple: false},
        members: {name: 'surfaces', encoding: 'feature', multiple: true}}, node_values: {}}},
  };
}

test('retargeting an existing center row validates its staged value without conflicting with its original node', () => {
  const original = fixture(), frozen = structuredClone(original);
  let editor;
  const state = () => stagedRelationshipState(original, new Set(['group']), [
    {id: 'group', kind: 'shared_center', draft: editor.draft}]);
  editor = createRelationshipDraft({getState: state, getNodes: () => relationshipEditorNodes(state(), 'group'), anchorFeature: 'fit'});
  editor.reset({kind: 'shared_center', draft: draft('a')});
  assert.equal(editor.validity().valid, true);
  assert.equal(editor.change('point', key('b'), true), true);
  assert.equal(editor.validity().valid, true);
  assert.equal(editor.inputs().point, 'b');
  assert.deepEqual(original, frozen, 'editing cannot mutate accepted state');
});

test('removed old groups do not block their replacements, while genuine other staged bindings still conflict', () => {
  const original = fixture(), removed = new Set(['group']);
  const view = stagedRelationshipState(original, removed, [{id: 'replacement', kind: 'shared_center', draft: draft('b')}]);
  assert.equal(view.recipe.nodes.some(node => node.id === 'group'), false);
  assert.equal(view.recipe.nodes.at(-1).point, 'b');
  const conflicting = stagedRelationshipState(view, new Set(), [{id: 'another', kind: 'shared_center', draft: draft('a')}]);
  const editor = createRelationshipDraft({getState: () => conflicting,
    getNodes: () => relationshipEditorNodes(conflicting, 'another'), anchorFeature: 'fit'});
  editor.reset({kind: 'shared_center', draft: draft('a')});
  assert.equal(editor.validity().valid, false);
  assert.match(editor.validity().reason, /another Shared center/);
});

test('existing relationship rows preserve their position and cannot select later providers', () => {
  const original = fixture(), view = stagedRelationshipState(original, new Set(['group']),
    [{id: 'group', kind: 'shared_center', draft: draft('b')}]);
  assert.deepEqual(view.recipe.nodes.map(node => node.id), original.recipe.nodes.map(node => node.id));
  const editor = createRelationshipDraft({getState: () => view,
    getNodes: () => relationshipEditorNodes(view, 'group'), anchorFeature: 'fit'});
  editor.reset({kind: 'shared_center', draft: draft('b')});
  assert.equal(editor.choices('point').some(output => output.reference.feature === 'later'), false);
});

test('editing ordinary relationships preserves unexposed saved options without carrying fields to a different type', () => {
  const state = {relationship_contracts: {mirror: {operation: 'mirror_symmetry', node_values: {symmetric_extents: true}},
    directions: {operation: 'direction_alignment', node_values: {sense: 'unsigned'}}}};
  const original = {id: 'saved', label: 'Mirror', group_id: 'organization', operation: 'mirror_symmetry',
    symmetric_extents: false, plane: 'datum', surfaces: ['first', 'second']};
  const editor = {kind: 'mirror', inputs: () => ({operation: 'mirror_symmetry', symmetric_extents: true, plane: 'datum', surfaces: ['first', 'second']}),
    snapshot: () => ({label: 'Kept mirror'})};
  assert.equal(relationshipNodeFromEditor(state, original, editor).symmetric_extents, false);
  editor.kind = 'directions'; editor.inputs = () => ({operation: 'direction_alignment', sense: 'unsigned', directions: []});
  const changed = relationshipNodeFromEditor(state, original, editor);
  assert.equal(changed.group_id, original.group_id); assert.equal(changed.id, original.id);
  assert.equal('plane' in changed, false); assert.equal('symmetric_extents' in changed, false);
});
