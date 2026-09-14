import assert from 'node:assert/strict';
import { actionMove } from './action-tree.js';

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
