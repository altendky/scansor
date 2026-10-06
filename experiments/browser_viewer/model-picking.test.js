import assert from 'node:assert/strict';
import test from 'node:test';
import { geometrySelectorIds, isModelClick, scanFitCandidates, segmentDistance } from './model-picking.js';

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
  for (const id of ['new-fit-inputs', 'frame-origin', 'new-intersection-first', 'export-target'])
    assert.equal(geometrySelectorIds.has(id), true);
  for (const id of ['new-fit-kind', 'organizational-group', 'action-list', 'export-mode', 'new-scale-output'])
    assert.equal(geometrySelectorIds.has(id), false);
});
