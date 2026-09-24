import assert from 'node:assert/strict';
import { selectionVolumePositions } from './reuse-volume.js';

const identity = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];
const bounds = (values, coordinate) => {
  const selected = [];
  for (let index = coordinate; index < values.length; index += 3) selected.push(values[index]);
  return [Math.min(...selected), Math.max(...selected)];
};
const close = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 1e-5, `${actual} != ${expected}`);

const plane = selectionVolumePositions(
  {
    kind: 'plane',
    plane_normal: [0, 0, 1],
    plane_offset: 0,
    basis_u: [1, 0, 0],
    basis_v: [0, 1, 0],
    u_bounds: [-1, 2],
    v_bounds: [-3, 4],
    normal_bounds: [-0.5, 0.75],
  },
  [10, 20, 30],
  identity,
);
assert.equal(plane.length, 36 * 3);
assert.deepEqual(bounds(plane, 0), [9, 12]);
assert.deepEqual(bounds(plane, 1), [17, 24]);
assert.deepEqual(bounds(plane, 2), [29.5, 30.75]);

const cylinder = selectionVolumePositions(
  {
    kind: 'cylinder',
    radius: 2,
    angle_start: 0,
    angle_span: Math.PI / 2,
    axial_bounds: [-1, 3],
    normal_bounds: [-0.5, 0.5],
  },
  [0, 0, 0],
  identity,
);
assert.ok(cylinder.length > 0);
for (const [actual, expected] of [
  [bounds(cylinder, 0)[0], 0],
  [bounds(cylinder, 0)[1], 2.5],
  [bounds(cylinder, 1)[0], 0],
  [bounds(cylinder, 1)[1], 2.5],
  [bounds(cylinder, 2)[0], -1],
  [bounds(cylinder, 2)[1], 3],
])
  close(actual, expected);

assert.equal(
  selectionVolumePositions({ kind: 'cone' }, [0, 0, 0], identity).length,
  0,
);
