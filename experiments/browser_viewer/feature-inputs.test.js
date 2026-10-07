import assert from 'node:assert/strict';
import test from 'node:test';
import { inputChoices, inputOptionRecords, inputReferenceKey, matchingInputOutput,
  readInputReference } from './feature-inputs.js';

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
