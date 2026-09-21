import assert from 'node:assert/strict';
import { actionDescription, actionMove, nodeReferences } from './action-tree.js';

const source = { id: 'source', label: 'Scan', operation: 'source' };
const a = { id: 'a', label: 'Side', operation: 'selection', source: 'source' };
const b = { id: 'b', label: 'End', operation: 'selection', source: 'source' };
const fit = { id: 'fit', label: 'Cylinder', operation: 'fit', selections: ['a'] };
const nodes = [source, a, b, fit];
const ids = (result) => result.nodes.map((node) => node.id);

assert.deepEqual(ids(actionMove(nodes, 'b', 1)), ['source', 'b', 'a', 'fit']);
assert.deepEqual(ids(actionMove(nodes, 'a', 3)), ['source', 'b', 'a', 'fit']);
assert.deepEqual(ids(actionMove(nodes, 'b', 4)), ['source', 'a', 'fit', 'b']);
assert.equal(actionMove(nodes, 'b', 2).changed, false);
assert.equal(actionMove(nodes, 'b', 3).changed, false);
assert.match(actionMove(nodes, 'a', 4).error, /Cylinder needs Side earlier/);
assert.match(actionMove(nodes, 'source', 2).error, /Side needs Scan earlier/);
assert.match(actionMove(nodes, 'fit', 1).error, /Cylinder needs Side earlier/);
assert.ok(actionMove(nodes, 'missing', 1).error);
assert.ok(actionMove(nodes, 'a', -1).error);
assert.ok(actionMove(nodes, 'a', 5).error);
assert.deepEqual(nodes, [source, a, b, fit]);
assert.equal(actionMove(nodes, 'b', 4).nodes[3], b);

const axis = { id: 'axis', label: 'Axis', operation: 'axis', source_fit: 'fit' };
const manualAxis = {
  id: 'manual-axis',
  label: 'Manual axis',
  operation: 'axis',
  initial_parameters: [0, 0, 0, 0],
};
assert.deepEqual(nodeReferences(axis), ['fit']);
assert.deepEqual(nodeReferences(manualAxis), []);
const boundPlane = {
  id: 'plane',
  label: 'Plane factor',
  operation: 'fit',
  selections: ['b'],
  axis: 'axis',
};
const solve = {
  id: 'solve',
  label: 'Shared solve',
  operation: 'axis_solve',
  axis: 'axis',
  factors: ['fit', 'plane'],
};
const axisNodes = [...nodes, axis, boundPlane, solve];
assert.equal(actionDescription(solve, 'unevaluated'), 'Joint · Not evaluated');
assert.match(actionMove(axisNodes, 'axis', 3).error, /Axis needs Cylinder earlier/);
assert.match(actionMove(axisNodes, 'plane', 4).error, /Plane factor needs Axis earlier/);
assert.match(actionMove(axisNodes, 'solve', 5).error, /Shared solve needs Plane factor earlier/);

const referencePlane = {
  id: 'mirror-plane',
  label: 'Mirror plane',
  operation: 'reference_plane',
  axis: 'axis',
  initial_angle_degrees: 0,
};
const mirror = {
  id: 'mirror',
  label: 'Mirrored pair',
  operation: 'mirror_symmetry',
  plane: 'mirror-plane',
  surfaces: ['fit', 'other-fit'],
};
assert.deepEqual(nodeReferences(referencePlane), ['axis']);
assert.deepEqual(nodeReferences(mirror), ['mirror-plane', 'fit', 'other-fit']);
const parallel = {
  id: 'parallel',
  label: 'Parallel',
  operation: 'parallel',
  surface: 'other-fit',
  reference_plane: 'mirror-plane',
};
const equal = {
  id: 'equal',
  label: 'Equal',
  operation: 'equal',
  left: { measurement: 'radius', surface: 'fit' },
  right: {
    measurement: 'plane_distance',
    surface: 'other-fit',
    reference_plane: 'mirror-plane',
  },
};
assert.deepEqual(nodeReferences(parallel), ['other-fit', 'mirror-plane']);
assert.deepEqual(nodeReferences(equal), ['fit', 'other-fit', 'mirror-plane']);
assert.equal(actionDescription(mirror, 'unevaluated'), 'Mirror relationship · Defined');
assert.equal(actionDescription(parallel, 'stale'), 'Parallel relationship · Defined');
assert.equal(actionDescription(equal, 'ready'), 'Numeric equality · Defined');
