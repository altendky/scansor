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

test('brush sweep covers sparse pointer events and has a circular footprint', async () => {
  const {brushHits} = await import('./selection.js');
  const projection = {points: [[0,0,0],[50,0,0],[100,0,0],[50,6,0],[3,3,0],null], visible: () => true};
  assert.deepEqual(brushHits(projection, [0,0], [100,0], 5, 'through_all'), [0,1,2,4]);
  assert.deepEqual(brushHits(projection, [0,0], [0,0], 4, 'through_all'), [0]);
});

test('first surface excludes occluded vertices with either face winding', async () => {
  const {selectionProjection, brushHits} = await import('./selection.js');
  const clip = new Float64Array([-1,-1,-.5,1, 1,-1,-.5,1, 0,1,-.5,1, 0,0,-.5,1, 0,0,.5,1, 0,0,2,1]);
  for (const indices of [[0,1,2], [2,1,0]]) {
    const projected = selectionProjection(clip, indices, 100, 100);
    assert.deepEqual(brushHits(projected, [50,50], [50,50], 4, 'first_surface'), [3]);
    assert.deepEqual(brushHits(projected, [50,50], [50,50], 4, 'through_all'), [3,4]);
  }
});

test('near-plane crossing triangles still occlude, and clipped-away triangles do not', async () => {
  const {selectionProjection} = await import('./selection.js');
  const clip = new Float64Array([-1,-1,-2,1, 1,-1,0,1, 0,1,0,1, 0,0,.5,1, .9,.9,.5,1]);
  const p = selectionProjection(clip, [0,1,2], 100, 100);
  assert.equal(p.visible(3), false);
  assert.equal(p.visible(4), true); // outside the silhouette
  clip[2] = clip[6] = clip[10] = -2;
  assert.equal(selectionProjection(clip, [0,1,2], 100, 100).visible(3), true);
});

test('replace uses the union of a whole stroke and remove leaves other patches intact', async () => {
  const {editSelectionGroups} = await import('./selection.js');
  const original = {seed: [9], other: [1,2,3]};
  const stroke = new Set([1,2]);
  assert.deepEqual(editSelectionGroups(original, 'seed', stroke, 'replace'), {seed: [1,2], other: [3]});
  assert.deepEqual(editSelectionGroups({seed:[1,2,8],other:[3]}, 'seed', [2], 'remove'), {seed:[1,8],other:[3]});
});
