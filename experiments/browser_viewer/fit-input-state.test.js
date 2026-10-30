import assert from 'node:assert/strict';
import test from 'node:test';
import {projectedFitState} from './fit-input-state.js';
import {inputChoices} from './feature-inputs.js';

function fixture() {
  const marker = '@fit-draft', output = feature => ({reference: {feature, output: 'axis', context: feature},
    label: `${feature} — axis`, feature_label: feature, producing_context: {id: feature, label: feature, kind: 'feature'},
    availability: 'unevaluated', compatible: true, dependencies: [feature], preview: null,
    parameter_owner: {feature, output: 'axis'}, properties: {direction: {reference: {feature, output: 'axis', context: feature},
      parameter_owner: {feature, output: 'axis'}, binding_eligible: true}}});
  const template = output(marker), unrelated = output('existing');
  return {recipe: {nodes: [{id: 'scan', operation: 'source'}, {id: 'selection', operation: 'selection'},
    {id: 'existing', operation: 'fit', kind: 'cylinder'}]}, states: {existing: 'ready'},
    input_catalogue: [unrelated], input_contracts: {direction: {property: 'direction'}},
    input_requirements: {direction: {choices: [unrelated], unavailable: []}},
    fit_input_templates: {cylinder: {feature: marker, input_catalogue: [template],
      input_requirements: {direction: {choices: [], unavailable: [template]}}}}};
}

test('prospective fit properties come only from backend templates and retain the exact frame/roles', () => {
  const state = fixture(), before = structuredClone(state),
    node = {id: 'new-fit', operation: 'fit', kind: 'cylinder', label: 'Bolt hole', selections: ['selection']},
    next = projectedFitState(state, node), output = next.input_catalogue.at(-1);
  assert.equal(next.recipe.nodes.at(-1).id, node.id); assert.equal(next.states[node.id], 'unevaluated');
  assert.deepEqual(output.reference, {feature: node.id, output: 'axis', context: node.id});
  assert.equal(output.parameter_owner.feature, node.id); assert.equal(output.properties.direction.parameter_owner.feature, node.id);
  assert.equal(output.producing_context.id, node.id); assert.equal(output.producing_context.label, node.label);
  assert.equal(output.feature_label, node.label); assert.equal(output.label, 'Bolt hole — axis');
  assert.deepEqual(output.dependencies, [node.id]); assert.equal(output.preview, null);
  assert.equal(inputChoices(next, 'direction').at(-1).reference.feature, node.id);
  assert.deepEqual(state, before, 'projection cannot publish into the accepted graph');
});

test('editing a fit removes obsolete publications while retaining other feature identities', () => {
  const state = fixture(), direct = structuredClone(state.input_catalogue[0]),
    publication = {...structuredClone(direct), reference: {...direct.reference, context: 'solve'}};
  state.recipe.nodes.push({id: 'solve'}); state.input_catalogue.push(publication);
  state.input_requirements.direction.choices.push(publication);
  const next = projectedFitState(state, {...state.recipe.nodes[2], label: 'Renamed cylinder'});
  assert.deepEqual(next.input_catalogue.filter(output => output.reference.feature === 'existing').map(output => output.reference.context), ['existing']);
  assert.equal(next.input_requirements.direction.choices.length, 0);
  assert.equal(next.input_requirements.direction.unavailable[0].label, 'Renamed cylinder — axis');
});

test('saved bindings preserve actual declared freedom and incompatible family changes require repair', () => {
  const state = fixture(), node = state.recipe.nodes[2]; node.axis = 'datum';
  state.input_catalogue[0].properties.direction.binding_eligible = false;
  state.input_catalogue[0].properties.direction.parameter_owner = null;
  const projected = projectedFitState(state, {...node, label: 'Bound fit'});
  assert.equal(projected.input_catalogue.at(-1).properties.direction.binding_eligible, false);
  assert.equal(projected.input_catalogue.at(-1).properties.direction.parameter_owner, null);
  assert.throws(() => projectedFitState(state, {...node, kind: 'plane'}), /saved shared-geometry binding/);
  assert.throws(() => projectedFitState(state, {...node, axis: null, kind: 'unknown'}), /does not declare/);
});

test('a provisional descriptor never replaces a structural incompatibility with an evaluation hint', () => {
  const state = fixture(), template = state.fit_input_templates.cylinder.input_requirements.direction.unavailable[0];
  template.compatible = false; template.reason = 'This output has an incompatible geometry kind';
  const next = projectedFitState(state, {id: 'new', operation: 'fit', kind: 'cylinder', label: 'New fit'});
  assert.equal(next.input_requirements.direction.unavailable[0].compatible, false);
  assert.equal(next.input_requirements.direction.unavailable[0].reason, template.reason);
});
