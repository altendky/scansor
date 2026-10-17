import assert from 'node:assert/strict';
import test from 'node:test';
import { geometrySelectorIds, isModelClick, pickFieldFeedback, scanFitCandidates, segmentDistance } from './model-picking.js';

test('picker guidance distinguishes empty inputs, optional blanks, temporary locks and list-only geometry', () => {
  const context = { label: 'Earlier selections', emptyMessage: 'Create a selection first.' };
  assert.equal(pickFieldFeedback([], context), 'No eligible earlier selections. Create a selection first.');
  assert.equal(pickFieldFeedback([{ key: '', label: 'None (standalone)' }], context), pickFieldFeedback([], context));
  const selectable = { key: 'fit', label: 'Fit', modelPickable: false };
  assert.match(pickFieldFeedback([selectable], context), /Eligible choices.*no pickable 3D geometry.*arrow/);
  assert.match(pickFieldFeedback([selectable], { ...context, disabled: true, busy: true }), /^Temporarily unavailable/);
  assert.match(pickFieldFeedback([selectable], { ...context, disabled: true }), /current configuration/);
  assert.equal(pickFieldFeedback([{ ...selectable, modelPickable: true }], context), '');
});

test('saved unavailable inputs remain diagnosed when alternative choices are ready', () => {
  const saved = { key: 'removed', label: 'Missing provider — restore it', selected: true, disabled: true };
  assert.match(pickFieldFeedback([saved], { label: 'Inputs' }), /Missing provider — restore it/);
  assert.match(pickFieldFeedback([saved, { key: 'ready', label: 'Replacement' }], { label: 'Inputs' }),
    /Saved input unavailable.*Choose a replacement or remove it/);
});

test('scan hits return all eligible memberships without guessing at overlaps', () => {
  const first = { key: 'solved:first', ids: [1, 2, 3] },
    second = { key: 'solved:second', ids: [3, 4] },
    locked = { key: 'locked', ids: [1], disabled: true };
  assert.deepEqual(scanFitCandidates([1, 2, 8], [first, second, locked]), [first]);
  assert.deepEqual(scanFitCandidates([3, 8, 9], [first, second]), [first, second]);
  assert.deepEqual(scanFitCandidates([7, 8, 9], [first, second]), []);
  assert.deepEqual(scanFitCandidates(null, [first]), []);
});

test('a new output or changed eligibility cannot reuse a stale pick', () => {
  const seed = { key: 'fit', ids: [1] };
  assert.deepEqual(scanFitCandidates([1], [seed]), [seed]);
  assert.deepEqual(scanFitCandidates([1], [{ ...seed, ids: [2] }]), []);
  assert.deepEqual(scanFitCandidates([1], [{ ...seed, disabled: true }]), []);
  assert.deepEqual(scanFitCandidates([1], [{ key: 'unevaluated' }]), []);
});

test('only a stationary left click from the same pointer commits a model pick', () => {
  const start = { pointerId: 1, clientX: 10, clientY: 20 };
  const end = { ...start, button: 0 };
  assert.equal(isModelClick(start, end), true);
  assert.equal(isModelClick(start, { ...end, clientX: 16 }), false);
  assert.equal(isModelClick(start, { ...end, button: 2 }), false);
  assert.equal(isModelClick(start, { ...end, pointerId: 2 }), false);
  assert.equal(isModelClick(null, end), false);
});

test('guide hit testing clamps to the segment, including a point guide', () => {
  assert.equal(segmentDistance([4, 3], [0, 0], [10, 0]), 3);
  assert.equal(segmentDistance([-4, 3], [0, 0], [10, 0]), 5);
  assert.equal(segmentDistance([14, 3], [0, 0], [10, 0]), 5);
  assert.equal(segmentDistance([4, 3], [0, 0], [0, 0]), 5);
});

test('model pick registry excludes enum, organizational and tree selection controls', () => {
  for (const id of ['new-fit-inputs', 'frame-origin', 'new-intersection-first', 'export-target',
    'coaxial-group-axis', 'coaxial-group-members'])
    assert.equal(geometrySelectorIds.has(id), true);
  for (const id of ['new-fit-kind', 'organizational-group', 'action-list', 'export-mode', 'new-scale-output'])
    assert.equal(geometrySelectorIds.has(id), false);
});
