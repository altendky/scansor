import test from 'node:test';
import assert from 'node:assert/strict';
import { faceEdgeLines } from './edge-highlight.js';

const points = [0, 0, 0, 2, 0, 0, 2, 2, 0];

test('face edges use screen-space widths and a depth-independent contrast halo', () => {
  const [halo, stroke] = faceEdgeLines(points, '#ffe45c', false, 'inspected');
  assert.equal(halo.material.linewidth, 7);
  assert.equal(stroke.material.linewidth, 4);
  assert.equal(stroke.material.color.getHexString(), 'ffe45c');
  for (const line of [halo, stroke]) {
    assert.equal(line.material.worldUnits, false);
    assert.equal(line.material.depthTest, false);
    assert.equal(line.material.depthWrite, false);
    assert.equal(line.material.transparent, true);
    assert.equal(line.material.opacity, 1);
    assert.ok(line.renderOrder > 101);
  }
  assert.ok(stroke.renderOrder > halo.renderOrder);
  assert.notEqual(stroke.geometry, halo.geometry);
});

test('inspected edges render above pinned boundaries and retained region outlines', () => {
  const region = faceEdgeLines(points, '#ffffff', false);
  const pinned = faceEdgeLines(points, '#78c5ff', false, 'neighbor');
  const inspected = faceEdgeLines(points, '#ffe45c', false, 'inspected');
  assert.ok(region[1].renderOrder < pinned[0].renderOrder);
  assert.ok(pinned[1].renderOrder < inspected[0].renderOrder);
  assert.ok(region[1].material.linewidth < pinned[1].material.linewidth);
});

test('selection footprints sit below inspected and selected physical boundaries', () => {
  const footprint = faceEdgeLines(points, '#78e2ff', true, 'footprint');
  const neighbor = faceEdgeLines(points, '#78c5ff', true, 'neighbor');
  assert.equal(footprint[1].material.linewidth, 3);
  assert.ok(footprint[1].renderOrder < neighbor[0].renderOrder);
});

test('the persistent surface footprint stays legible but quieter than physical boundaries', () => {
  const context = faceEdgeLines(points, '#78e2ff', true, 'footprint-context');
  const focused = faceEdgeLines(points, '#78e2ff', true, 'footprint');
  const neighbor = faceEdgeLines(points, '#78c5ff', true, 'neighbor');
  assert.equal(context[1].material.linewidth, 2);
  assert.ok(context[1].material.linewidth < focused[1].material.linewidth);
  assert.ok(context[1].material.linewidth < neighbor[1].material.linewidth);
  for (const line of context) {
    assert.equal(line.material.opacity, 1);
    assert.equal(line.material.depthTest, false);
  }
  assert.equal(context[1].material.color.getHexString(), '75b7c8');
  assert.equal(focused[1].material.color.getHexString(), '78e2ff');
  assert.ok(context[1].renderOrder < neighbor[0].renderOrder);
});

test('persistent footprint segment joins and both halo/stroke passes use full alpha', () => {
  const bent = [0, 0, 0, 1, 0, 0, 1, 1, 0, 2, 1, 0];
  for (const closed of [false, true]) {
    const lines = faceEdgeLines(bent, '#78e2ff', closed, 'footprint-context');
    assert.equal(lines.length, 2);
    for (const line of lines) {
      assert.equal(line.material.opacity, 1);
      assert.equal(line.geometry.attributes.instanceStart.count, closed ? 4 : 3);
    }
  }
});

test('line closure preserves physical open endpoints and does not duplicate a closed ring', () => {
  const segments = (positions, closed) => faceEdgeLines(positions, '#fff', closed)[0].geometry.attributes.instanceStart.count;
  assert.equal(segments(points, false), 2);
  assert.equal(segments(points, true), 3);
  assert.equal(segments([...points, ...points.slice(0, 3)], true), 3);
  assert.deepEqual(points, [0, 0, 0, 2, 0, 0, 2, 2, 0]);
  assert.deepEqual(faceEdgeLines([0, 0, 0], '#fff', false), []);
  assert.deepEqual(faceEdgeLines([0, 0, 0, NaN, 0, 0], '#fff', false), []);
});

test('defined face context boundaries remain quieter and behind the active review', () => {
  const context = faceEdgeLines(points, '#87a8b5', true, 'defined-face'),
    active = faceEdgeLines(points, '#ffffff', true, 'region'),
    footprint = faceEdgeLines(points, '#78e2ff', true, 'footprint-context');
  assert.equal(context[1].material.linewidth, 1);
  assert.ok(context[1].renderOrder < active[0].renderOrder);
  assert.ok(context[1].renderOrder < footprint[0].renderOrder);
  assert.equal(context[1].material.opacity, 1);
});
