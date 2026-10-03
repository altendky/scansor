import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFootprintTopology, fittedSelectionFootprint } from './fit-footprint.js';

const TAU = Math.PI * 2, dot = (a, b) => a.reduce((sum, value, index) => sum + value * b[index], 0),
  unit = (a) => a.map((value) => value / Math.hypot(...a)),
  cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function chart(normal) {
  let least = 0;
  for (let index = 1; index < 3; index++)
    if (Math.abs(normal[index]) < Math.abs(normal[least])) least = index;
  const basis = [0, 0, 0];
  basis[least] = 1;
  const u = unit(cross(normal, basis));
  return [u, cross(normal, u)];
}
const add = (origin, a, x, b, y) => origin.map((value, index) => value + a[index] * x + b[index] * y);
const pointsOf = (path) => Array.from({ length: path.preview.positions.length / 3 },
  (_, index) => path.preview.positions.slice(index * 3, index * 3 + 3));
function near(actual, expected, tolerance = 1e-10) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected} (tolerance ${tolerance})`);
}
function axial(p, angle, height, radialOffset = 0) {
  const axis = unit([p[2], p[3], 1]), [u, v] = chart(axis),
    center = [p[0], p[1], 0].map((value, index) => value + axis[index] * height),
    radius = p[4] + p[6] * height + radialOffset;
  return add(center, u, Math.cos(angle) * radius, v, Math.sin(angle) * radius);
}
function axialCoordinates(p, point) {
  const axis = unit([p[2], p[3], 1]), [u, v] = chart(axis),
    relative = point.map((value, index) => value - [p[0], p[1], 0][index]),
    height = dot(axis, relative),
    radial = relative.map((value, index) => value - axis[index] * height);
  return { height, radius: Math.hypot(...radial), angle: Math.atan2(dot(radial, v), dot(radial, u)) };
}
function fitting(kind, parameters, points) {
  return { fitted: { kind, parameters, ids: points.map((_, index) => index) }, positions: points.flat() };
}

test('plane footprint projects observations onto the analytic fit and excludes interior nodes', () => {
  const n = unit([1, 2, 3]), [u, v] = chart(n), origin = n.map((value) => value * 7),
    xy = [[-2, -1], [2, -1], [2, 1], [-2, 1], [0, 0]],
    points = xy.map(([x, y], index) => add(add(origin, u, x, v, y), n, index + 1, n, 0)),
    { fitted, positions } = fitting('plane', [...n, 7], points),
    paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(paths.length, 1);
  assert.equal(paths[0].closed, true);
  assert.deepEqual(paths[0].preview.indices, []);
  assert.equal(pointsOf(paths[0]).length, 4);
  for (const point of pointsOf(paths[0])) {
    near(dot(n, point), 7);
    near(Math.abs(dot(u, point)), 2);
    near(Math.abs(dot(v, point)), 1);
  }
});

test('explicit plane equation takes precedence and normalizes its offset', () => {
  const fitted = { kind: 'plane', ids: [0, 1, 2, 3], plane_equation: [0, 0, 2, 8],
    parameters: [0, 0, 1, -100] },
    paths = fittedSelectionFootprint(fitted, [0, 0, 6, 2, 0, 7, 2, 3, 9, 0, 3, -3]);
  assert.equal(paths.length, 1);
  for (const point of pointsOf(paths[0])) near(point[2], 4);
});

test('coaxial plane offset already belongs to the normalized axis normal', () => {
  const n = unit([1, 2, 1]), [u, v] = chart(n), origin = n.map((value) => value * 5),
    points = [[0, 0], [1, 0], [1, 2], [0, 2]].map(([x, y]) => add(origin, u, x, v, y)),
    { fitted, positions } = fitting('plane', [0, 0, 1, 2, 0, 5, 0], points),
    paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(paths.length, 1);
  for (const point of pointsOf(paths[0])) near(dot(n, point), 5);
});

test('tilted planes remain stable under translation and uniform scaling', () => {
  const n = unit([0.25, -0.5, 1]), [u, v] = chart(n);
  for (const scale of [1e-6, 1, 1e6]) {
    const origin = [1234, -987, 555].map((value) => value * scale), offset = dot(n, origin),
      points = [[-2, -3], [4, -3], [4, 5], [-2, 5], [1, 1]].map(([x, y]) =>
        add(add(origin, u, x * scale, v, y * scale), n, 0.7 * scale, n, 0)),
      { fitted, positions } = fitting('plane', [...n, offset], points),
      paths = fittedSelectionFootprint(fitted, new Float64Array(positions));
    assert.equal(paths.length, 1);
    assert.equal(pointsOf(paths[0]).length, 4);
    for (const point of pointsOf(paths[0])) near(dot(n, point), offset, scale * 1e-9);
  }
  const origin = [1e9, -2e9, 3e9], offset = dot(n, origin),
    points = [[-2, -3], [4, -3], [4, 5], [-2, 5]].map(([x, y]) => add(origin, u, x, v, y)),
    { fitted, positions } = fitting('plane', [...n, offset], points);
  const paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(pointsOf(paths[0]).length, 4);
  for (const point of pointsOf(paths[0])) near(dot(n, point), offset, 2e-6);
});

test('cylinder partial patches crossing the angle seam stay on the exact surface', () => {
  const p = [2, -3, 0.2, -0.4, 5, 0, 0],
    angles = [TAU - 0.35, 0.25],
    points = angles.flatMap((angle) => [-2, 4].map((height) => axial(p, angle, height, 0.8))),
    { fitted, positions } = fitting('cylinder', p, points),
    paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(paths.length, 1);
  const coordinates = pointsOf(paths[0]).map((point) => axialCoordinates(p, point));
  for (let index = 0; index < coordinates.length; index++) {
    const current = coordinates[index], next = coordinates[(index + 1) % coordinates.length],
      delta = Math.atan2(Math.sin(next.angle - current.angle), Math.cos(next.angle - current.angle));
    near(current.radius, 5);
    assert.ok(current.angle >= -0.35 - 1e-10 && current.angle <= 0.25 + 1e-10);
    assert.ok(Math.abs(delta) <= Math.PI / 48 + 1e-10);
    assert.ok(current.height >= -2 - 1e-10 && current.height <= 4 + 1e-10);
  }
});

test('cone uses closest meridian projection rather than preserving observation height', () => {
  const p = [0, 0, 0, 0, 4, 0, 0.5],
    points = [0.2, 0.8].flatMap((angle) => [1, 3].map((height) => axial(p, angle, height, 2))),
    { fitted, positions } = fitting('cone', p, points),
    paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(paths.length, 1);
  const coordinates = pointsOf(paths[0]).map((point) => axialCoordinates(p, point));
  near(Math.min(...coordinates.map((point) => point.height)), 1.8);
  near(Math.max(...coordinates.map((point) => point.height)), 3.8);
  for (const point of coordinates) near(point.radius, 4 + 0.5 * point.height);
});

test('cone with negative reference radius still encloses its positive-radius support', () => {
  const p = [0, 0, 0, 0, -3, 0, 0.5],
    points = [0.2, 0.8].flatMap((angle) => [8, 12].map((height) => axial(p, angle, height))),
    { fitted, positions } = fitting('cone', p, points),
    paths = fittedSelectionFootprint(fitted, positions);
  assert.equal(paths.length, 1);
  for (const point of pointsOf(paths[0])) {
    const { height, radius } = axialCoordinates(p, point);
    near(radius, -3 + 0.5 * height);
    assert.ok(radius > 0);
  }
});

test('tilted translated cylinder and cone footprints are scale covariant', () => {
  for (const kind of ['cylinder', 'cone']) for (const scale of [1e-6, 1, 1e6]) {
    const p = [123 * scale, -77 * scale, 0.3, 0.7, 4 * scale, 0, kind === 'cone' ? -0.2 : 0],
      points = [0.3, 1].flatMap((angle) => [1, 4].map((height) => axial(p, angle, height * scale, scale))),
      { fitted, positions } = fitting(kind, p, points),
      paths = fittedSelectionFootprint(fitted, positions);
    assert.equal(paths.length, 1);
    for (const point of pointsOf(paths[0])) {
      const { height, radius } = axialCoordinates(p, point);
      near(radius, p[4] + p[6] * height, scale * 1e-9);
    }
  }
});

test('wide periodic cylinder and cone coverage yields only two closed end rings, not a seam', () => {
  for (const kind of ['cylinder', 'cone']) {
    const p = [1, -2, 0.3, -0.2, 5, 0, kind === 'cone' ? 0.25 : 0],
      points = Array.from({ length: 8 }, (_, index) => index * TAU / 8)
        .flatMap((angle) => [-1, 3].map((height) => axial(p, angle, height))),
      { fitted, positions } = fitting(kind, p, points),
      paths = fittedSelectionFootprint(fitted, positions);
    assert.equal(paths.length, 2);
    for (const [index, path] of paths.entries()) {
      assert.equal(path.closed, true);
      assert.equal(pointsOf(path).length, 96);
      for (const point of pointsOf(path)) {
        const coordinates = axialCoordinates(p, point);
        near(coordinates.height, index ? 3 : -1);
        near(coordinates.radius, p[4] + p[6] * coordinates.height);
      }
    }
  }
});

test('a quarter-circle largest gap is conservatively treated as periodic', () => {
  const p = [0, 0, 0, 0, 1, 0, 0], points = [0, Math.PI / 2, Math.PI, Math.PI * 1.5]
    .flatMap((angle) => [0, 1].map((height) => axial(p, angle, height))),
    { fitted, positions } = fitting('cylinder', p, points);
  assert.equal(fittedSelectionFootprint(fitted, positions).length, 2);
});

test('tilted rings and axial lines do not turn projection roundoff into a thin band', () => {
  const p = [12, -34, 0.3, 0.7, 4, 0, 0];
  for (const scale of [1e-6, 1, 1e6]) {
    const scaled = p.map((value, index) => [0, 1, 4].includes(index) ? value * scale : value),
      ring = Array.from({ length: 16 }, (_, index) => axial(scaled, index * TAU / 16, 2 * scale)),
      line = [0, 1, 2, 3].map((height) => axial(scaled, 0.3, height * scale));
    for (const points of [ring, line]) {
      const { fitted, positions } = fitting('cylinder', scaled, points);
      assert.deepEqual(fittedSelectionFootprint(fitted, positions), []);
    }
  }
});

test('all selected observations contribute, with no mutation or order-dependent sampling', () => {
  const points = Array.from({ length: 400 }, (_, index) => [index / 400, (index % 17) / 17, 2]);
  points.push([30, 0, 2], [0, 30, 2], [-30, -30, 2]);
  const { fitted, positions } = fitting('plane', [0, 0, 1, 0], points),
    original = structuredClone({ fitted, positions }),
    paths = fittedSelectionFootprint(fitted, positions),
    reordered = { ...fitted, ids: [...fitted.ids].reverse().concat([0, 1, 0]) };
  assert.deepEqual(fittedSelectionFootprint(reordered, positions), paths);
  assert.deepEqual({ fitted, positions }, original);
  assert.equal(pointsOf(paths[0]).length, 3);
  assert.ok(paths[0].preview.positions.includes(30));
  const typed = new Float32Array(positions), before = typed.slice();
  assert.deepEqual(fittedSelectionFootprint(fitted, typed), paths);
  assert.deepEqual(typed, before);
});

test('invalid and degenerate inputs never produce false geometry', () => {
  const valid = { kind: 'plane', parameters: [0, 0, 1, 0], ids: [0, 1, 2] },
    positions = [0, 0, 1, 1, 0, 1, 0, 1, 1];
  for (const fitted of [null, {}, { ...valid, kind: 'sphere' }, { ...valid, ids: [] },
    { ...valid, ids: [0, 1] }, { ...valid, ids: [0, 1, 3] }, { ...valid, ids: [0, 1, -1] },
    { ...valid, ids: [0, 1, 1.5] }, { ...valid, parameters: [0, 0, 0, 0] },
    { ...valid, parameters: [0, 0, 1, NaN] }, { ...valid, parameters: [0, 0, 1] },
    { ...valid, parameters: [0, 0, 0, 0, , 0, 0] }])
    assert.deepEqual(fittedSelectionFootprint(fitted, positions), []);
  for (const input of [null, [], [0, 0], [...positions.slice(0, -1), NaN],
    [0, 0, 0, 1, 0, 0, 2, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0]])
    assert.deepEqual(fittedSelectionFootprint(valid, input), []);
  for (const kind of ['cylinder', 'cone']) for (const radius of [NaN, Infinity])
    assert.deepEqual(fittedSelectionFootprint({ kind, ids: [0, 1, 2],
      parameters: [0, 0, 0, 0, radius, 0, 0.5] }, positions), []);
  for (const radius of [0, -1]) assert.deepEqual(fittedSelectionFootprint({ kind: 'cylinder',
    ids: [0, 1, 2], parameters: [0, 0, 0, 0, radius, 0, 0] }, positions), []);
  const p = [0, 0, 0, 0, 2, 0, 0],
    line = [0, 1, 2].map((height) => axial(p, 0.3, height)),
    ring = [0, 1, 2, 3, 4, 5].map((angle) => axial(p, angle, 2));
  for (const points of [line, ring, [[0, 0, 0], [1, 0, 1], [0, 1, 2]]]) {
    const { fitted, positions: input } = fitting('cylinder', p, points);
    assert.deepEqual(fittedSelectionFootprint(fitted, input), []);
  }
  const belowTip = fitting('cone', [0, 0, 0, 0, 1, 0, 1], [[1, 0, -3], [0, 1, -3], [1, 1, -2]]);
  assert.deepEqual(fittedSelectionFootprint(belowTip.fitted, belowTip.positions), []);
});

function gridMesh(cells) {
  const byCoordinate = new Map(), points = [], indices = [];
  const vertex = (x, y) => {
    const key = `${x},${y}`;
    if (!byCoordinate.has(key)) {
      byCoordinate.set(key, points.length);
      points.push([x, y, 0.7]);
    }
    return byCoordinate.get(key);
  };
  for (const [x, y] of cells) {
    const a = vertex(x, y), b = vertex(x + 1, y), c = vertex(x + 1, y + 1), d = vertex(x, y + 1);
    indices.push(a, b, c, a, c, d);
  }
  return { ...fitting('plane', [0, 0, 1, 0], points), indices };
}
function area2D(path) {
  const points = pointsOf(path);
  return Math.abs(points.reduce((area, point, index) => {
    const next = points[(index + 1) % points.length];
    return area + point[0] * next[1] - next[0] * point[1];
  }, 0)) / 2;
}

test('selected mesh connectivity preserves an L-shaped concavity instead of its convex hull', () => {
  const { fitted, positions, indices } = gridMesh([[0, 0], [1, 0], [0, 1]]),
    paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology(indices, positions.length / 3));
  assert.equal(paths.length, 1);
  near(area2D(paths[0]), 3);
  assert.ok(pointsOf(paths[0]).some(([x, y]) => x === 1 && y === 1));
  near(area2D(fittedSelectionFootprint(fitted, positions)[0]), 3.5);
  for (const point of pointsOf(paths[0])) near(point[2], 0);
});

test('a crescent mesh keeps both curved boundaries and its open mouth', () => {
  const points = [], indices = [], count = 12;
  for (let index = 0; index <= count; index++) {
    const angle = -Math.PI * 0.75 + index * Math.PI * 1.5 / count;
    points.push([Math.cos(angle), Math.sin(angle), 0.3], [2 * Math.cos(angle), 2 * Math.sin(angle), -0.4]);
    if (index) {
      const a = (index - 1) * 2, b = index * 2;
      indices.push(a, a + 1, b + 1, a, b + 1, b);
    }
  }
  const { fitted, positions } = fitting('plane', [0, 0, 1, 0], points),
    paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology(indices, points.length));
  assert.equal(paths.length, 1);
  assert.equal(pointsOf(paths[0]).length, points.length);
  assert.ok(area2D(paths[0]) < area2D(fittedSelectionFootprint(fitted, positions)[0]));
  assert.equal(pointsOf(paths[0]).filter((point) => Math.hypot(point[0], point[1]) < 1.01).length, count + 1);
});

test('all-selected mesh edges retain holes and disconnected components', () => {
  const cells = [];
  for (let x = 0; x < 3; x++) for (let y = 0; y < 3; y++)
    if (x !== 1 || y !== 1) cells.push([x, y]);
  cells.push([6, 0]);
  const { fitted, positions, indices } = gridMesh(cells),
    paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology(indices, positions.length / 3));
  assert.equal(paths.length, 3);
  assert.deepEqual(paths.map(area2D).sort((a, b) => a - b), [1, 1, 9]);
  assert.ok(paths.every((path) => path.closed && !path.preview.indices.length));
});

test('a fully selected triangle includes its open scan-mesh boundary', () => {
  const { fitted, positions } = fitting('plane', [0, 0, 1, 0], [[0, 0, 3], [2, 0, 4], [0, 2, 5]]),
    paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology([0, 1, 2], 3));
  assert.equal(paths.length, 1);
  near(area2D(paths[0]), 2);
  assert.equal(pointsOf(paths[0]).length, 3);
});

test('partial vertex membership clips each triangle at the binary mask midpoint', () => {
  const positions = [0, 0, 3, 4, 0, 3, 0, 4, 3], topology = buildFootprintTopology([0, 1, 2], 3),
    fitted = { kind: 'plane', parameters: [0, 0, 1, 0], ids: [0] },
    one = fittedSelectionFootprint(fitted, positions, topology),
    two = fittedSelectionFootprint({ ...fitted, ids: [0, 1] }, positions, topology);
  assert.equal(one.length, 1);
  assert.equal(pointsOf(one[0]).length, 3);
  near(area2D(one[0]), 2);
  assert.deepEqual(pointsOf(one[0]).map((point) => point.slice(0, 2)).sort(), [[0, 0], [0, 2], [2, 0]].sort());
  assert.equal(pointsOf(two[0]).length, 4);
  near(area2D(two[0]), 6);
});

test('clipped adjacent polygons cancel shared edges but retain their open mesh perimeter', () => {
  const positions = [0, 0, 0, 2, 0, 0, 2, 2, 0, 0, 2, 0],
    topology = buildFootprintTopology([0, 1, 2, 0, 2, 3], 4),
    paths = fittedSelectionFootprint({ kind: 'plane', parameters: [0, 0, 1, 0], ids: [0, 1] }, positions, topology);
  assert.equal(paths.length, 1);
  near(area2D(paths[0]), 2);
  assert.equal(pointsOf(paths[0]).length, 5);
});

function axialStrip(p, angles, periodic = false) {
  const points = angles.flatMap((angle) => [1, 4].map((height) => axial(p, angle, height, 0.7))), indices = [];
  for (let index = 0; index < angles.length - (periodic ? 0 : 1); index++) {
    const a = index * 2, b = ((index + 1) % angles.length) * 2;
    indices.push(a, b, b + 1, a, b + 1, a + 1);
  }
  return { points, indices };
}

test('mesh loops on cylinders and cones cross the angular seam via short exact-surface arcs', () => {
  for (const kind of ['cylinder', 'cone']) {
    const p = [3, -4, 0.3, -0.2, 5, 0, kind === 'cone' ? 0.25 : 0],
      { points, indices } = axialStrip(p, [TAU - 0.3, 0, 0.3]),
      { fitted, positions } = fitting(kind, p, points),
      paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology(indices, points.length));
    assert.equal(paths.length, 1);
    const coordinates = pointsOf(paths[0]).map((point) => axialCoordinates(p, point));
    for (let index = 0; index < coordinates.length; index++) {
      const a = coordinates[index], b = coordinates[(index + 1) % coordinates.length],
        delta = Math.atan2(Math.sin(b.angle - a.angle), Math.cos(b.angle - a.angle));
      near(a.radius, p[4] + p[6] * a.height);
      assert.ok(Math.abs(delta) <= Math.PI / 48 + 1e-10);
      assert.ok(a.angle >= -0.3 - 1e-10 && a.angle <= 0.3 + 1e-10);
    }
    const shift = p[6] * 0.7 / (1 + p[6] * p[6]);
    near(Math.min(...coordinates.map((point) => point.height)), 1 + shift);
    near(Math.max(...coordinates.map((point) => point.height)), 4 + shift);
  }
});

test('periodic mesh loops have two rings without adding an artificial angular seam', () => {
  for (const kind of ['cylinder', 'cone']) {
    const p = [0, 0, 0.2, 0.3, 4, 0, kind === 'cone' ? 0.3 : 0],
      { points, indices } = axialStrip(p, Array.from({ length: 12 }, (_, index) => index * TAU / 12), true),
      { fitted, positions } = fitting(kind, p, points),
      paths = fittedSelectionFootprint(fitted, positions, buildFootprintTopology(indices, points.length));
    assert.equal(paths.length, 2);
    const shift = p[6] * 0.7 / (1 + p[6] * p[6]), heights = [];
    for (const path of paths) {
      const coordinates = pointsOf(path).map((point) => axialCoordinates(p, point));
      heights.push(coordinates[0].height);
      for (const point of coordinates) {
        near(point.height, coordinates[0].height);
        near(point.radius, p[4] + p[6] * point.height);
      }
      assert.ok(coordinates.length >= 96);
    }
    heights.sort((a, b) => a - b);
    near(heights[0], 1 + shift);
    near(heights[1], 4 + shift);
  }
});

test('mesh loop ordering is canonical and does not mutate any caller inputs', () => {
  const { fitted, positions, indices } = gridMesh([[0, 0], [1, 0], [0, 1], [5, 0]]),
    original = structuredClone({ fitted, positions, indices }),
    topology = buildFootprintTopology(new Uint16Array(indices), positions.length / 3),
    before = structuredClone(topology), paths = fittedSelectionFootprint(fitted, positions, topology),
    reversedTriangles = Array.from({ length: indices.length / 3 }, (_, index) =>
      indices.slice(index * 3, index * 3 + 3).reverse()).reverse().flat();
  assert.deepEqual(fittedSelectionFootprint({ ...fitted, ids: [...fitted.ids].reverse().concat(fitted.ids) },
    positions, buildFootprintTopology(reversedTriangles, positions.length / 3)), paths);
  assert.deepEqual({ fitted, positions, indices }, original);
  assert.deepEqual(topology, before);
  indices.fill(0);
  assert.deepEqual(fittedSelectionFootprint(fitted, positions, topology), paths);
});

test('invalid topology, absent faces, nonmanifold boundaries and isolated membership use the hull fallback', () => {
  const { fitted, positions } = fitting('plane', [0, 0, 1, 0],
    [[0, 0, 0], [3, 0, 0], [0, 3, 0], [3, 3, 0], [-1, 0, 0]]),
    fallback = fittedSelectionFootprint(fitted, positions);
  for (const indices of [null, [], [0, 1], [0, 1, -1], [0, 1, 5], [0, 1, 1], [0, 1, NaN]])
    assert.equal(buildFootprintTopology(indices, 5), null);
  assert.equal(buildFootprintTopology([0, 1, 2], -1), null);
  for (const topology of [null, {}, buildFootprintTopology([0, 1, 2], 3),
    buildFootprintTopology([0, 1, 2], 5),
    buildFootprintTopology([0, 1, 2, 0, 1, 3, 0, 1, 4], 5),
    buildFootprintTopology([0, 1, 2, 0, 3, 4], 5)])
    assert.deepEqual(fittedSelectionFootprint(fitted, positions, topology), fallback);
  assert.deepEqual(fittedSelectionFootprint({ ...fitted, ids: [0, 1, 2] },
    [0, 0, 1, 1, 0, 2, 2, 0, 3], buildFootprintTopology([0, 1, 2], 3)), []);
});

test('footprint queries read only the selected vertices incident triangles, not the whole mesh', () => {
  const triangleCount = 20000, indices = new Uint32Array(triangleCount * 3);
  for (let index = 0; index < indices.length; index++) indices[index] = index;
  const topology = buildFootprintTopology(indices, triangleCount * 3), numericReads = [],
    positions = new Proxy(Array(triangleCount * 9).fill(NaN), {
      get(target, key) {
        if (/^\d+$/.test(String(key))) {
          numericReads.push(Number(key));
          assert.ok(Number(key) < 9, 'Unrelated mesh triangle was inspected');
          return [0, 0, 0, 2, 0, 0, 0, 2, 0][Number(key)];
        }
        return Reflect.get(target, key);
      },
    }),
    paths = fittedSelectionFootprint({ kind: 'plane', parameters: [0, 0, 1, 0], ids: [0] }, positions, topology);
  assert.equal(paths.length, 1);
  assert.ok(numericReads.length <= 12);
  assert.equal(topology.incidentTriangles.length, indices.length);
  assert.equal(topology.offsets.length, triangleCount * 3 + 1);
});
