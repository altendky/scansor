import assert from 'node:assert/strict';
import test from 'node:test';
import { inputCanBeReferenced, inputChoices, inputOptionRecords, inputReferenceKey, matchingInputOutput,
  readInputReference, renderInputChoices } from './feature-inputs.js';

const point = (feature, output = 'point', context = feature) => ({
  reference: { feature, output, context }, capability: 'point', label: `${feature} — ${output}`,
  availability: 'ready', reason: null, dependencies: [feature, context],
  preview: { point_display: [0, 0, 0] },
});
const datum = point('datum'), center = point('sphere', 'center'),
  single = point('selected', 'single_node');

test('same-capability outputs come from backend decisions, including new providers', () => {
  const state = { recipe: { nodes: ['datum', 'sphere', 'selected'].map(id => ({ id })) },
    input_requirements: { point: { choices: [datum, center, single], unavailable: [] } } };
  assert.deepEqual(inputChoices(state, 'point'), [datum, center, single]);
  assert.deepEqual(inputChoices(state, 'plane'), []);
  assert.deepEqual(inputChoices(state, 'point', [{ id: 'datum' }]), [datum]);
});

test('output keys preserve meaning and exact solve identity across serialization', () => {
  for (const id of ['123', 'true', 'false', 'null']) assert.equal(readInputReference(id), id);
  const solved = point('sphere', 'center', 'solve');
  assert.notEqual(inputReferenceKey(solved.reference), inputReferenceKey(center.reference));
  assert.notEqual(inputReferenceKey(center.reference), inputReferenceKey({ ...center.reference, output: 'point' }));
  assert.deepEqual(readInputReference(inputReferenceKey(solved.reference)), solved.reference);
  assert.equal(matchingInputOutput('sphere', [center, solved]), null);
  assert.equal(matchingInputOutput('datum', [datum]), datum);
});

test('availability refresh keeps the selected output and explains unavailable retained choices', () => {
  const saved = inputReferenceKey(center.reference),
    stale = { ...center, availability: 'stale', reason: 'Evaluate its fit first' };
  assert.deepEqual(inputOptionRecords([datum, stale], saved)[1], {
    key: saved, label: 'sphere — center — Evaluate its fit first', disabled: true, selected: true,
  });
  assert.equal(inputOptionRecords([datum, center], saved)[1].selected, true);
  const removed = inputOptionRecords([datum], saved).at(-1);
  assert.equal(removed.key, saved);
  assert.equal(removed.disabled, true);
  assert.equal(removed.selected, true);
  assert.equal(inputOptionRecords([datum, { ...center, label: 'Renamed center' }], saved)[1].label, 'Renamed center');
});

test('later solve dependencies cannot become eligible through an earlier physical fit', () => {
  const solved = point('sphere', 'center', 'solve'),
    state = { input_requirements: { point: { choices: [solved], unavailable: [] } } };
  const output = inputChoices(state, 'point', [{ id: 'sphere' }])[0];
  assert.equal(output.availability, 'blocked');
  assert.equal(output.reason, 'This output depends on a later feature');
  assert.equal(inputOptionRecords([output], solved.reference)[0].selected, true);
});

test('unavailable or ambiguous legacy meanings remain strings until explicit repair', () => {
  for (const availability of ['unevaluated', 'ambiguous', 'failed']) {
    const output = { ...datum, availability, reason: 'Evaluate or repair this provider' },
      saved = inputOptionRecords([output], 'datum').at(-1);
    assert.equal(saved.key, 'datum');
    assert.equal(saved.selected, true);
    assert.equal(readInputReference(saved.key), 'datum');
  }
  assert.equal(inputOptionRecords([datum], 'datum')[0].selected, true);
  const ambiguous = inputOptionRecords([center, point('sphere', 'center', 'solve')], 'sphere').at(-1);
  assert.equal(ambiguous.key, 'sphere');
  assert.equal(ambiguous.label, 'Multiple output contexts — choose an explicit output');
  assert.equal(ambiguous.disabled, true);
  assert.equal(ambiguous.selected, true);
});

test('ready geometry rejected by parameter ownership remains unavailable to the picker', () => {
  const readOnly = { ...center, reason: 'This input requires an adjustable parameter owner, not read-only geometry' };
  assert.equal(inputOptionRecords([readOnly])[0].disabled, true);
});

test('pending authoring is explicit and requires backend structural compatibility', () => {
  for (const availability of ['stale', 'unevaluated']) {
    const pending = {...center, availability, compatible: true, reason: 'Evaluate its provider first'};
    assert.equal(inputCanBeReferenced(pending), false);
    assert.equal(inputCanBeReferenced(pending, {allowPending: true}), true);
    const option = inputOptionRecords([pending], pending.reference, {allowPending: true})[0];
    assert.equal(option.disabled, false);
    assert.equal(option.selected, true);
    assert.match(option.label, /needs evaluation/);
    assert.equal(inputOptionRecords([pending], pending.reference)[0].disabled, true);
    assert.equal(inputCanBeReferenced({...pending, compatible: false}, {allowPending: true}), false);
    assert.equal(inputCanBeReferenced({...pending, compatible: undefined}, {allowPending: true}), false);
  }
  for (const availability of ['failed', 'blocked', 'needs_input', 'ambiguous', 'running'])
    assert.equal(inputCanBeReferenced({...center, availability, compatible: true}, {allowPending: true}), false);
});

test('relationship option refresh preserves pending exact contexts and still blocks later providers', () => {
  const pending = {...point('sphere', 'center', 'solve'), availability: 'stale', compatible: true,
    reason: 'Reevaluate its changed provider'}, saved = inputReferenceKey(pending.reference);
  for (const availability of ['stale', 'unevaluated', 'ready']) {
    const output = {...pending, availability, reason: availability === 'ready' ? null : pending.reason};
    assert.equal(inputOptionRecords([output], saved, {allowPending: true})[0].selected, true);
    assert.equal(inputOptionRecords([output], saved, {allowPending: true})[0].disabled, false);
  }
  const state = {input_requirements: {point: {choices: [], unavailable: [pending]}}},
    later = inputChoices(state, 'point', [{id: 'sphere'}])[0];
  assert.equal(inputOptionRecords([later], saved, {allowPending: true})[0].disabled, true);
});

test('multiple owner input controls retain all saved choices, including unavailable repairs', () => {
  const previous = globalThis.Option;
  globalThis.Option = class {
    constructor(label, value, _defaultSelected, selected) { Object.assign(this, { label, value, selected }); }
  };
  try {
    const control = { multiple: true, replaceChildren(...options) { this.options = options; } },
      missing = { feature: 'removed', output: 'axis', context: 'removed' };
    renderInputChoices(control, [datum, center], [datum.reference, missing]);
    assert.deepEqual(control.options.filter(option => option.selected).map(option => option.value),
      [inputReferenceKey(datum.reference), inputReferenceKey(missing)]);
    assert.equal(control.options.at(-1).disabled, true);
    renderInputChoices(control, [datum, center], []);
    assert.equal(control.options.some(option => option.selected), false);
  } finally { globalThis.Option = previous; }
});
