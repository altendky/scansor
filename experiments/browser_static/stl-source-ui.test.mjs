import assert from 'node:assert/strict';
import test from 'node:test';
import {stlSourceFixture} from './stl-source-ui.mjs';

test('UI STL fixtures independently encode the same oriented planar grid with misleading normals', () => {
  const binary = stlSourceFixture('binary'), ascii = stlSourceFixture('ascii');
  assert.equal(binary.subarray(0, 4).toString('ascii'), 'ply\n');
  assert.equal(binary.length, 84 + 512 * 50);
  assert.equal(binary.readUInt32LE(80), 512);
  const points = new Set(), facets = [];
  let area = 0;
  for (let face = 0; face < 512; face++) {
    const start = 84 + face * 50;
    assert.deepEqual([0, 4, 8].map(offset => binary.readFloatLE(start + offset)), [0, 0, -7]);
    assert.equal(binary.readUInt16LE(start + 48), 0);
    const triangle = Array.from({length: 3}, (_, corner) => Array.from({length: 3}, (_, axis) =>
      binary.readFloatLE(start + 12 + corner * 12 + axis * 4)));
    triangle.forEach(point => points.add(point.join(',')));
    facets.push(triangle);
    const [a, b, c] = triangle;
    area += ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2;
  }
  assert.equal(points.size, 289);
  assert.equal(area, 1);
  const rows = [...ascii.toString('ascii').matchAll(/^vertex (.+)$/gm)].map(match => match[1].split(' ').map(Number));
  assert.deepEqual(rows, facets.flat());
  assert.equal((ascii.toString('ascii').match(/^facet normal 0 0 -7$/gm) || []).length, 512);
  assert.throws(() => stlSourceFixture('other'));
});
