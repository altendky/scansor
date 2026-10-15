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
  selectionVolumePositions({ kind: 'sphere' }, [0, 0, 0], identity).length,
  0,
);

for (const [radius, slope, heights] of [[3, 0.5, [-1, 2]], [3, -0.5, [-1, 2]],
  [3, 1e-7, [-1, 2]], [-1, 0.5, [4, 6]]]) {
  const scale = Math.hypot(1, slope), region = {
    kind: 'cone', radius, slope, generator_bounds: heights.map(value => value * scale),
    normal_bounds: [-0.2, 0.3], angle_start: 0, angle_span: Math.PI / 2,
  }, saved = structuredClone(region), rotation = [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
    origin = [10, -20, 30], volume = selectionVolumePositions(region, origin, rotation);
  assert.ok(volume.length > 0);
  const measured = [];
  for (let index = 0; index < volume.length; index += 3) {
    // The chosen placement maps local (x,y,z) to world (z,x,y).
    const [x, y, z] = [volume[index + 1] - origin[1], volume[index + 2] - origin[2], volume[index] - origin[0]],
      rho = Math.hypot(x, y), normal = (rho - radius - slope * z) / scale,
      generator = (z + slope * (rho - radius)) / scale;
    assert.ok(rho >= 0 && radius + slope * generator / scale > 0);
    measured.push([normal, generator]);
  }
  for (const [column, expected] of [[0, region.normal_bounds], [1, region.generator_bounds]]) {
    close(Math.min(...measured.map(row => row[column])), expected[0]);
    close(Math.max(...measured.map(row => row[column])), expected[1]);
  }
  assert.deepEqual(region, saved);
}

assert.throws(() => selectionVolumePositions({ kind: 'cone' }, [0, 0, 0], identity), /finite cone/);
const narrow = selectionVolumePositions({ kind: 'cone', radius: 0.1, slope: 0.5,
  generator_bounds: [0, 2], normal_bounds: [-0.5, 0.1], angle_start: 0, angle_span: Math.PI / 2 },
[0, 0, 0], identity);
assert.ok(narrow.length > 0);
for (let i = 0; i < narrow.length; i += 3) {
  assert.ok(Number.isFinite(narrow[i]) && Number.isFinite(narrow[i + 1]) && Number.isFinite(narrow[i + 2]));
  assert.ok(narrow[i] >= 0 && narrow[i + 1] >= 0, 'normal layers cannot cross onto the opposite angular sheet');
}
assert.throws(() => selectionVolumePositions({ kind: 'cone', radius: -1, slope: 0.5,
  generator_bounds: [0, 3], normal_bounds: [-0.1, 0.1], angle_start: 0, angle_span: 1 },
[0, 0, 0], identity), /finite cone/);
