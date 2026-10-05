import { test } from 'node:test';
import assert from 'node:assert/strict';
import { arrangeToolbars, clampToolbar, containsPoint, DEFAULT_PLACEMENT, defaultPlacements as allPlacements, edgeAt,
  insertToolbar, insertionBefore, toolbarDropZones, toolbarOrder, toolbarOrientation, toolbarSize, validPlacement } from './toolbar-state.js';

// Four geometry strips make the lane/insertion fixtures easy to reason about.
const defaultPlacements = () => {
  const { project, ...geometry } = allPlacements();
  return geometry;
};
test('the project strip joins the default top edge before geometry tools', () => {
  assert.deepEqual(toolbarOrder(allPlacements(), 'workspace', 'top'), ['project', 'create', 'faces', 'features', 'output']);
});

test('toolbar placement validation rejects malformed browser preferences', () => {
  assert.ok(validPlacement(DEFAULT_PLACEMENT));
  for (const value of [null, {}, { ...DEFAULT_PLACEMENT, edge: 'elsewhere' },
    { ...DEFAULT_PLACEMENT, host: 123 }, { ...DEFAULT_PLACEMENT, x: Infinity },
    { ...DEFAULT_PLACEMENT, order: NaN }, { ...DEFAULT_PLACEMENT, order: '1' },
    { ...DEFAULT_PLACEMENT, orientation: 'diagonal' }]) {
    assert.ok(!validPlacement(value));
  }
});
test('edges orient strips and floating toolbars retain chosen orientation', () => {
  assert.equal(toolbarOrientation({ edge: 'left' }), 'vertical');
  assert.equal(toolbarOrientation({ edge: 'bottom' }), 'horizontal');
  assert.equal(toolbarOrientation({ edge: 'float', orientation: 'vertical' }), 'vertical');
});
test('edge targeting does not snap central or outside drops to a distant edge', () => {
  const area = { width: 1000, height: 800 };
  assert.equal(edgeAt({ x: 10, y: 300 }, area), 'left');
  assert.equal(edgeAt({ x: 500, y: 790 }, area), 'bottom');
  assert.equal(edgeAt({ x: 500, y: 400 }, area), null);
  assert.equal(edgeAt({ x: -10, y: 400 }, area), null);
});
test('floating coordinates stay reachable after resize, including oversized strips', () => {
  assert.deepEqual(clampToolbar({ x: 950, y: -20 }, { width: 1000, height: 800 }, { width: 200, height: 40 }),
    { x: 800, y: 0 });
  assert.deepEqual(clampToolbar({ x: 50, y: 50 }, { width: 100, height: 100 }, { width: 200, height: 200 }),
    { x: 0, y: 0 });
});
test('same-edge strips pack and wrap without reserving blank individual rows', () => {
  const placements = defaultPlacements(), sizes = Object.fromEntries(Object.keys(placements).map(id => [id, { width: 180, height: 38 }]));
  const layout = arrangeToolbars(placements, sizes, { width: 400, height: 600 });
  assert.deepEqual(layout.insets, { top: 76, right: 0, bottom: 0, left: 0 });
  assert.equal(layout.positions.create.left, 0);
  assert.equal(layout.positions.faces.left, 180);
  assert.equal(layout.positions.features.top, 38);
  assert.equal(layout.positions.output.left, 180);
});
test('vertical lanes exclude horizontal corners and oversized strips are clipped once', () => {
  const placements = defaultPlacements();
  placements.faces.edge = placements.features.edge = 'left';
  placements.output.edge = 'right';
  const sizes = { create: { width: 700, height: 38 }, faces: { width: 38, height: 180 },
    features: { width: 38, height: 180 }, output: { width: 38, height: 500 } };
  const layout = arrangeToolbars(placements, sizes, { width: 400, height: 300 });
  assert.deepEqual(layout.insets, { top: 38, right: 38, bottom: 0, left: 76 });
  assert.equal(layout.positions.create.maxWidth, 400);
  assert.equal(layout.positions.faces.top, 38);
  assert.equal(layout.positions.features.left, 38);
  assert.equal(layout.positions.output.maxHeight, 262);
});
test('old-orientation measurements cannot turn into huge opposite-edge insets', () => {
  assert.deepEqual(toolbarSize({ edge: 'left' }, { width: 600, height: 38, orientation: 'horizontal' }),
    { width: 38, height: 74 });
  assert.deepEqual(toolbarSize({ edge: 'top' }, { width: 38, height: 400, orientation: 'vertical' }),
    { width: 74, height: 38 });
});
test('ordering accepts old preferences, sorts stable ties and inserts without mutating input', () => {
  const placements = defaultPlacements();
  for (const placement of Object.values(placements)) delete placement.order;
  assert.deepEqual(toolbarOrder(placements, 'workspace', 'top'), ['create', 'faces', 'features', 'output']);
  const moved = insertToolbar(placements, 'output', placements.output, 'faces');
  assert.deepEqual(toolbarOrder(moved, 'workspace', 'top'), ['create', 'output', 'faces', 'features']);
  assert.equal(placements.output.order, undefined);
  assert.deepEqual(toolbarOrder(insertToolbar(moved, 'output', moved.output), 'workspace', 'top'),
    ['create', 'faces', 'features', 'output']);
  const local = insertToolbar(moved, 'create', { ...moved.create, host: 'local' });
  assert.deepEqual(toolbarOrder(local, 'local', 'top'), ['create']);
  assert.equal(local.faces.order, moved.faces.order);
});
test('wrapped insertion chooses a lane first, horizontally and vertically on either edge', () => {
  for (const edge of ['top', 'bottom', 'left', 'right']) {
    const vertical = ['left', 'right'].includes(edge), placements = defaultPlacements();
    for (const placement of Object.values(placements)) placement.edge = edge;
    const rects = vertical ? { create: { x: 0, y: 0, width: 38, height: 100 },
      faces: { x: 0, y: 100, width: 38, height: 100 }, features: { x: 38, y: 0, width: 38, height: 100 } } : {
      create: { x: 0, y: 0, width: 100, height: 38 }, faces: { x: 100, y: 0, width: 100, height: 38 },
      features: { x: 0, y: 38, width: 100, height: 38 } };
    const reverse = ['bottom', 'right'].includes(edge);
    if (reverse) for (const rect of Object.values(rects)) rect[vertical ? 'x' : 'y'] = 38 - rect[vertical ? 'x' : 'y'];
    const point = (axis, cross) => vertical ? { x: reverse ? 76 - cross : cross, y: axis } : { x: axis, y: reverse ? 76 - cross : cross };
    const target = { host: 'workspace', edge };
    assert.equal(insertionBefore(placements, 'output', target, point(110, 19), rects), 'faces');
    assert.equal(insertionBefore(placements, 'output', target, point(190, 19), rects), 'features');
    assert.equal(insertionBefore(placements, 'output', target, point(10, 57), rects), 'features');
    assert.equal(insertionBefore(placements, 'output', target, point(110, 57), rects), null);
  }
});
test('workspace gutter and local bands are disjoint at their shared perimeter', () => {
  const workspace = { id: 'workspace', x: 0, y: 0, width: 400, height: 300 };
  const host = { ...workspace, id: 'local', name: 'Model / Graph' };
  const empty = arrangeToolbars({}, {}, workspace);
  const { zones, corners } = toolbarDropZones(workspace, [host], { workspace: empty, local: empty });
  const global = zones.filter(zone => zone.host === 'workspace'), local = zones.filter(zone => zone.host === 'local');
  assert.equal(global.find(zone => containsPoint(zone.rect, { x: 394, y: 150 })).edge, 'right');
  assert.equal(local.find(zone => containsPoint(zone.rect, { x: 376, y: 150 })).edge, 'right');
  for (const a of global) for (const b of local) {
    assert.ok(Math.min(a.rect.x + a.rect.width, b.rect.x + b.rect.width) <= Math.max(a.rect.x, b.rect.x) ||
      Math.min(a.rect.y + a.rect.height, b.rect.y + b.rect.height) <= Math.max(a.rect.y, b.rect.y));
  }
  for (const [i, a] of zones.entries()) for (const b of zones.slice(i + 1)) {
    assert.ok(Math.min(a.rect.x + a.rect.width, b.rect.x + b.rect.width) <= Math.max(a.rect.x, b.rect.x) ||
      Math.min(a.rect.y + a.rect.height, b.rect.y + b.rect.height) <= Math.max(a.rect.y, b.rect.y));
  }
  for (const point of [{ x: 6, y: 6 }, { x: 394, y: 6 }, { x: 6, y: 294 }, { x: 394, y: 294 }]) {
    assert.ok(!zones.some(zone => containsPoint(zone.rect, point)));
    assert.ok(corners.some(corner => corner.host === 'workspace' && containsPoint(corner.rect, point)));
  }
  for (const point of [{ x: 18, y: 18 }, { x: 382, y: 18 }, { x: 18, y: 282 }, { x: 382, y: 282 }]) {
    assert.ok(!zones.some(zone => containsPoint(zone.rect, point)));
    assert.ok(corners.some(corner => corner.host === 'local' && containsPoint(corner.rect, point)));
  }
});
