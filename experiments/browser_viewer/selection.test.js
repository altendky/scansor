import test from 'node:test';
import assert from 'node:assert/strict';
import {editSelection, rectangleHits} from './selection.js';

test('adding moves source IDs between regions without mutating the saved session', () => {
  const original = {lateral_ids: [1, 4], plane_ids: [2, 3]};
  assert.deepEqual(editSelection(original, 'lateral_ids', [3, 5], 'add'), {lateral_ids: [1, 3, 4, 5], plane_ids: [2]});
  assert.deepEqual(original.plane_ids, [2, 3]);
});
test('replace, remove and empty selections retain canonical source IDs', () => {
  const original = {lateral_ids: [1, 4], plane_ids: [2, 3]};
  assert.deepEqual(editSelection(original, 'lateral_ids', [4, 1, 4], 'replace').lateral_ids, [1, 4]);
  assert.deepEqual(editSelection(original, 'lateral_ids', [1], 'remove').lateral_ids, [4]);
  assert.deepEqual(editSelection(original, 'lateral_ids', [], 'replace').lateral_ids, []);
});
test('rectangle includes hidden-depth vertices but excludes clipped and outside points', () => {
  const points = new Float32Array([1, 1, -.5, 1, 1, .5, 1, 1, 2, 10, 10, 0]);
  assert.deepEqual(rectangleHits(points, p => p, {left: 0, right: 2, top: 0, bottom: 2}), [0, 1]);
});

test('adding to a third surface removes overlap from every other selection', async () => {
  const {editSelectionGroups} = await import('./selection.js');
  const groups = {side: [1, 2], end: [3, 4], extra: [5]};
  assert.deepEqual(editSelectionGroups(groups, 'extra', [2, 3, 6], 'add'), {side: [1], end: [4], extra: [2, 3, 5, 6]});
  assert.deepEqual(groups, {side: [1, 2], end: [3, 4], extra: [5]});
});
