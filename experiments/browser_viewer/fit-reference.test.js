import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fitReferenceContracts, fitReferenceForNode, fitReferenceInputs,
  readFitReferenceEditor, renderFitReferenceEditor,
} from './fit-reference.js';
import {inputReferenceKey} from './feature-inputs.js';
import {geometrySelectorIds} from './model-picking.js';

// Model native single-select behavior, including a deliberately selected disabled
// repair option. In particular, adding it must deselect the default placeholder.
class Select {
  constructor() { this.options = []; this.dataset = {}; this.required = false; }
  get value() { return this.options.find(option => option.selected)?.value || ''; }
  set value(value) {
    for (const option of this.options) option.selected = option.value === value;
  }
  replaceChildren(...options) {
    this.options = [];
    for (const option of options) this.add(option);
    if (!this.options.some(option => option.selected)) {
      const first = this.options.find(option => !option.disabled);
      if (first) first.selected = true;
    }
  }
  add(option) {
    if (option.selected) for (const previous of this.options) previous.selected = false;
    this.options.push(option);
  }
}

function documentFixture(t) {
  const previous = globalThis.Option;
  globalThis.Option = class {
    constructor(label, value, _defaultSelected = false, selected = false) {
      Object.assign(this, {label, textContent: label, value, selected, disabled: false});
    }
  };
  t.after(() => { globalThis.Option = previous; });
  const classes = new Set(), control = new Select(), mode = new Select(),
    hint = {textContent: '', classList: {toggle(name, enabled) {
      if (enabled) classes.add(name); else classes.delete(name);
    }}};
  const elements = new Map([['fit-reference', control], ['fit-reference-mode', mode], ['fit-reference-hint', hint]]);
  return {control, mode, hint, classes, doc: {getElementById: id => elements.get(id)}};
}

const reference = (feature, output = 'axis', context = feature) => ({feature, output, context});
const declarations = {
  fit_axis_line: {
    capabilities: ['axis_line'], property: 'axis_line', geometry_kinds: ['axis'],
    fit_reference: {field: 'axis', output: 'axis', kinds: ['cylinder', 'cone'], label: 'Shared axis line'},
  },
  fit_axis_direction: {
    capabilities: ['direction'], property: 'direction', geometry_kinds: ['axis'],
    fit_reference: {field: 'axis', output: 'axis', kinds: ['plane'], label: 'Normal follows axis direction'},
  },
  fit_plane_orientation: {
    capabilities: ['plane_orientation'], property: 'plane_orientation', geometry_kinds: ['plane'],
    fit_reference: {field: 'reference_plane', output: 'plane', kinds: ['plane'], label: 'Parallel plane orientation'},
  },
  fit_point_center: {
    capabilities: ['point'], property: 'point', geometry_kinds: ['point'],
    fit_reference: {field: 'point', output: 'point', kinds: ['sphere'], label: 'Shared sphere center'},
  },
};

function output(feature, name = 'axis', context = feature) {
  const properties = {axis: ['axis_line', 'direction'], plane: ['plane', 'plane_orientation', 'direction'], point: ['point']}[name];
  return {reference: reference(feature, name, context), label: `Datum ${feature}`,
    frame: 'workspace', availability: 'ready', reason: null, compatible: true,
    ownership: 'datum', constrainable: context === feature, dependencies: [feature, context],
    properties: Object.fromEntries(properties
      .map(property => [property, {property, frame: 'workspace', binding_eligible: true}]))};
}

function fixture() {
  const axes = [output('axis'), output('fixed_axis')], plane = output('plane', 'plane'),
    point = output('point', 'point'), solved = {...output('axis', 'axis', 'solve'),
      reason: 'This input requires a direct datum context', compatible: false};
  for (const prop of Object.values(axes[1].properties)) {
    prop.binding_eligible = false;
    prop.parameter_owner = null;
    prop.binding_reason = 'This datum is initialized from an upstream fit';
  }
  const state = {token: 'saved', revision: 1,
    recipe: {nodes: ['axis', 'fixed_axis', 'plane', 'point', 'solve', 'fit'].map(id => ({id}))},
    input_contracts: {...structuredClone(declarations), direction: {capabilities: ['direction']}},
    input_requirements: {
      fit_axis_line: {choices: axes, unavailable: [solved]},
      fit_axis_direction: {choices: axes, unavailable: [solved]},
      fit_plane_orientation: {choices: [plane], unavailable: []},
      fit_point_center: {choices: [point], unavailable: []},
    }};
  return {state, axes, plane, point, solved};
}

const render = (f, dom, kind, selected, nodes = f.state.recipe.nodes) => renderFitReferenceEditor({
  id: 'fit-reference', state: f.state, kind, nodes, selected, doc: dom.doc,
});

test('known legacy fields canonicalize directly even when solved contexts make a feature ambiguous', () => {
  const f = fixture(), before = structuredClone(f.state);
  assert.deepEqual(fitReferenceContracts(f.state).map(contract => contract.id), Object.keys(declarations));
  for (const [kind, field, feature, mode, name] of [
    ['cylinder', 'axis', 'axis', 'fit_axis_line', 'axis'],
    ['cone', 'axis', 'axis', 'fit_axis_line', 'axis'],
    ['plane', 'axis', 'axis', 'fit_axis_direction', 'axis'],
    ['plane', 'reference_plane', 'plane', 'fit_plane_orientation', 'plane'],
    ['sphere', 'point', 'point', 'fit_point_center', 'point'],
  ]) {
    const node = {id: 'fit', kind, [field]: feature}, saved = structuredClone(node),
      draft = fitReferenceForNode(f.state, node);
    assert.deepEqual(draft, {mode, reference: reference(feature, name)});
    assert.deepEqual(fitReferenceInputs(f.state, kind, draft), {
      axis: null, point: null, reference_plane: null, [field]: feature,
    });
    assert.deepEqual(node, saved);
  }
  assert.deepEqual(f.state, before);
  assert.throws(() => fitReferenceInputs(f.state, 'cylinder', {mode: 'fit_axis_line', reference: 'axis'}), /explicit direct/);
});

test('invalid fit-family bindings remain diagnosed without changing the declared family', () => {
  const f = fixture(), node = {kind: 'sphere', axis: 'axis'}, before = structuredClone(node);
  assert.deepEqual(fitReferenceForNode(f.state, node), {mode: 'unavailable:axis', reference: 'axis'});
  assert.throws(() => fitReferenceInputs(f.state, node.kind, {mode: 'fit_axis_line', reference: reference('axis')}), /fit type/);
  assert.deepEqual(node, before);
  assert.deepEqual(fitReferenceInputs(f.state, node.kind, {mode: '', reference: reference('axis')}),
    {axis: null, point: null, reference_plane: null});
});

test('exact output, context, property and frame are checked before flattening', () => {
  const f = fixture(), saved = structuredClone(f.state), draft = {mode: 'fit_axis_line', reference: reference('axis')};
  for (const invalid of [reference('axis', 'surface'), reference('axis', 'axis', 'solve'),
    reference('removed'), null, 'axis'])
    assert.throws(() => fitReferenceInputs(f.state, 'cylinder', {...draft, reference: invalid}), /direct|unavailable/);
  for (const defect of ['property', 'frame', 'compatible', 'reason']) {
    const bad = structuredClone(f.state), candidate = bad.input_requirements.fit_axis_line.choices[0];
    if (defect === 'property') delete candidate.properties.axis_line;
    if (defect === 'frame') candidate.frame = 'other';
    if (defect === 'compatible') candidate.compatible = false;
    if (defect === 'reason') candidate.reason = 'Provider is not a compatible axis';
    assert.throws(() => fitReferenceInputs(bad, 'cylinder', draft), /property|frame|unavailable|compatible/);
  }
  assert.deepEqual(f.state, saved);
});

test('fixed source-fit datums remain usable as geometry without acquiring independent freedom', () => {
  const f = fixture(), draft = {mode: 'fit_axis_line', reference: reference('fixed_axis')},
    before = structuredClone(f.axes[1]);
  assert.deepEqual(fitReferenceInputs(f.state, 'cone', draft), {axis: 'fixed_axis', point: null, reference_plane: null});
  assert.deepEqual(f.axes[1], before);
  assert.equal(f.axes[1].properties.axis_line.binding_eligible, false);
  f.point.properties.point = {property: 'point', frame: 'workspace', parameter_owner: null,
    binding_eligible: false, binding_reason: 'This point is initialized from an upstream sphere'};
  const center = structuredClone(f.point);
  assert.deepEqual(fitReferenceInputs(f.state, 'sphere', {mode: 'fit_point_center', reference: reference('point', 'point')}),
    {axis: null, point: 'point', reference_plane: null});
  assert.deepEqual(f.point, center);
});

test('ordering and later dependencies prevent committing an otherwise ready datum', () => {
  const f = fixture(), draft = {mode: 'fit_axis_line', reference: reference('axis')};
  assert.throws(() => fitReferenceInputs(f.state, 'cylinder', draft, [{id: 'fit'}]), /unavailable/);
  f.axes[0].dependencies.push('later_provider');
  assert.throws(() => fitReferenceInputs(f.state, 'cylinder', draft), /later feature/);
  for (const availability of ['unevaluated', 'stale', 'running', 'failed', 'blocked', 'ambiguous']) {
    const bad = fixture(); bad.axes[0].availability = availability;
    assert.throws(() => fitReferenceInputs(bad.state, 'cylinder', draft), /unavailable/);
  }
});

test('unsupported modes and kinds preserve disabled selections until explicit repair or None', t => {
  const f = fixture(), dom = documentFixture(t), draft = {mode: 'fit_axis_line', reference: reference('axis')};
  render(f, dom, 'plane', draft);
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  assert.equal(dom.mode.options.find(option => option.value === draft.mode).disabled, true);
  assert.equal(dom.control.options.find(option => option.selected).disabled, true);
  assert.match(dom.hint.textContent, /unavailable for this fit type/);
  assert.throws(() => fitReferenceInputs(f.state, 'plane', readFitReferenceEditor('fit-reference', dom.doc)), /fit type/);
  render(f, dom, 'torus');
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  dom.mode.value = '';
  render(f, dom, 'plane');
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), {mode: '', reference: null});
  assert.equal(dom.control.required, false);
  assert.equal(dom.control.dataset.inputRequirement, undefined);
  assert.deepEqual(fitReferenceInputs(f.state, 'plane', readFitReferenceEditor('fit-reference', dom.doc)),
    {axis: null, point: null, reference_plane: null});
});

test('removed references and unknown saved relationships stay visible as disabled repairs', t => {
  const f = fixture(), dom = documentFixture(t), removed = reference('missing');
  for (const draft of [{mode: 'fit_axis_line', reference: removed}, {mode: 'unavailable:axis', reference: 'axis'}]) {
    render(f, dom, 'cylinder', draft);
    assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
    const selected = dom.control.options.find(option => option.selected);
    assert.equal(selected.disabled, true);
    assert.match(selected.label, /Unavailable saved output/);
    assert.match(dom.hint.textContent, /unavailable|Repair/);
    assert.throws(() => fitReferenceInputs(f.state, 'cylinder', draft), /unavailable/);
  }
});

test('ordinary snapshots retain a live typed draft through renaming, stale providers and reordering', t => {
  const f = fixture(), dom = documentFixture(t);
  render(f, dom, 'cylinder', fitReferenceForNode(f.state, {kind: 'cylinder', axis: 'axis'}));
  dom.control.value = inputReferenceKey(reference('fixed_axis'));
  const draft = readFitReferenceEditor('fit-reference', dom.doc);
  f.state = structuredClone(f.state); f.state.token = 'fresh'; f.state.revision++;
  f.state.input_requirements.fit_axis_line.choices.reverse();
  const candidate = f.state.input_requirements.fit_axis_line.choices.find(item => item.reference.feature === 'fixed_axis');
  candidate.label = 'Renamed fixed source datum'; candidate.availability = 'stale'; candidate.reason = 'Reevaluate its source fit';
  render(f, dom, 'cylinder');
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  assert.match(dom.control.options.find(option => option.selected).label, /Renamed.*Reevaluate/);
  assert.equal(dom.control.options.find(option => option.selected).disabled, true);
  assert.throws(() => fitReferenceInputs(f.state, 'cylinder', draft), /Reevaluate/);
  candidate.availability = 'ready'; candidate.reason = null;
  render(f, dom, 'cylinder');
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  assert.equal(dom.control.options.find(option => option.selected).disabled, false);
});

test('a ready output with a later prerequisite stays selected but cannot be picked or committed', t => {
  const f = fixture(), dom = documentFixture(t), draft = {mode: 'fit_axis_direction', reference: reference('axis')};
  f.axes[0].dependencies.push('later_joint');
  render(f, dom, 'plane', draft);
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  assert.equal(dom.control.options.find(option => option.selected).disabled, true);
  assert.match(dom.hint.textContent, /later feature/);
  assert.throws(() => fitReferenceInputs(f.state, 'plane', draft), /later feature/);
  f.state.recipe.nodes.push({id: 'later_joint'});
  render(f, dom, 'plane');
  assert.deepEqual(readFitReferenceEditor('fit-reference', dom.doc), draft);
  assert.equal(dom.control.options.find(option => option.selected).disabled, false);
  assert.deepEqual(fitReferenceInputs(f.state, 'plane', draft), {axis: 'axis', point: null, reference_plane: null});
});

test('relationship modes are enums and fixed references explain their actual geometry role', t => {
  const f = fixture(), dom = documentFixture(t);
  render(f, dom, 'cylinder', {mode: 'fit_axis_line', reference: reference('fixed_axis')});
  assert.match(dom.hint.textContent, /upstream fit/);
  assert.match(dom.hint.textContent, /complete centerline.*direction-only/);
  assert.equal(dom.control.dataset.inputRequirement, 'fit_axis_line');
  assert.equal(dom.mode.dataset.inputRequirement, undefined);
  for (const id of ['fit-reference', 'new-fit-reference']) assert.equal(geometrySelectorIds.has(id), true);
  for (const id of ['fit-reference-mode', 'new-fit-reference-mode']) assert.equal(geometrySelectorIds.has(id), false);
});

function sourceFrameFixture() {
  const f = fixture(), frame = 'b'.repeat(64);
  f.state.recipe.nodes.unshift({id: 'scan', operation: 'source', source_sha256: 'a'.repeat(64), reference_sha256: frame});
  for (const requirement of Object.values(f.state.input_requirements)) {
    for (const item of [...requirement.choices, ...requirement.unavailable]) {
      item.frame = frame;
      for (const property of Object.values(item.properties)) property.frame = frame;
    }
  }
  return {...f, frame};
}

test('actual source binding frame hashes permit each supported fit reference contract', () => {
  const f = sourceFrameFixture(), before = structuredClone(f.state);
  for (const [kind, mode, feature, name, field] of [
    ['cylinder', 'fit_axis_line', 'axis', 'axis', 'axis'],
    ['cone', 'fit_axis_line', 'axis', 'axis', 'axis'],
    ['plane', 'fit_axis_direction', 'axis', 'axis', 'axis'],
    ['plane', 'fit_plane_orientation', 'plane', 'plane', 'reference_plane'],
    ['sphere', 'fit_point_center', 'point', 'point', 'point'],
  ]) {
    const draft = {mode, reference: reference(feature, name)};
    assert.deepEqual(fitReferenceInputs(f.state, kind, draft), {axis: null, point: null, reference_plane: null, [field]: feature});
  }
  assert.deepEqual(f.state, before);
});

test('output and property frames must each match the source binding, not a workspace label or another matching pair', () => {
  const f = sourceFrameFixture(), draft = {mode: 'fit_axis_line', reference: reference('axis')};
  for (const [outputFrame, propertyFrame] of [
    ['workspace', f.frame], [f.frame, 'workspace'], ['workspace', 'workspace'],
    ['c'.repeat(64), f.frame], [f.frame, 'c'.repeat(64)], ['c'.repeat(64), 'c'.repeat(64)],
  ]) {
    const state = structuredClone(f.state), item = state.input_requirements.fit_axis_line.choices[0];
    item.frame = outputFrame;
    item.properties.axis_line.frame = propertyFrame;
    assert.throws(() => fitReferenceInputs(state, 'cylinder', draft), /property.*frame/);
    assert.equal(item.frame, outputFrame);
    assert.equal(item.properties.axis_line.frame, propertyFrame);
  }
});
