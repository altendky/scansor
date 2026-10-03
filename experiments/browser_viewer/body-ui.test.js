import assert from 'node:assert/strict';
import { bodyEdgePaths, bodyFaceChoices, bodyFaceSelection, bodyInputs, bodyProblems, initialBodyTolerance } from './body-ui.js';

const nodes = [
  { id: 'owner', operation: 'build_faces', label: 'Build faces', surfaces: [] },
  { id: 'face', operation: 'arranged_face', managed_by: 'owner', label: 'Shoulder' },
  { id: 'bad', operation: 'trimmed_face', label: 'Outer' },
  { id: 'open', operation: 'trimmed_face', label: 'Bore' },
  { id: 'body', operation: 'body', label: 'Body', faces: ['face'] },
  { id: 'later', operation: 'arranged_face', label: 'Later' },
];
const state = { recipe: { nodes }, states: { face: 'stale', bad: 'failed', open: 'ready' },
  results: { open: { bounded: false } } };
assert.deepEqual(bodyFaceChoices(state).map(({ node, state, open }) => [node.id, state, open]),
  [['face', 'stale', false], ['bad', 'failed', false], ['open', 'ready', true], ['later', 'unevaluated', false]]);
assert.deepEqual(bodyFaceSelection(state, new Set(['owner', 'bad'])), ['face', 'bad']);
assert.deepEqual(bodyFaceSelection(state), ['face', 'bad', 'open', 'later']);
assert.deepEqual(bodyInputs(['face', 'face', 'bad'], '1e-7'), { faces: ['face', 'bad'], sewing_tolerance: 1e-7 });
assert.throws(() => bodyInputs([], 1e-7), /at least one/);
for (const value of [0, -1, NaN, Infinity, 'invalid', ''])
  assert.throws(() => bodyInputs(['face'], value), /positive finite/);
assert.deepEqual(bodyProblems({ problems: [{ message: 'Gap', source_faces: ['face', 'unknown'] }] }, nodes),
  [{ message: 'Gap', source_faces: ['face', 'unknown'], sourceNames: ['Shoulder', 'unknown'] }]);
assert.deepEqual(bodyProblems(null, nodes), []);
assert.equal(initialBodyTolerance([0, 0, 0, 3, 4, 0]), 5e-6);
assert.equal(initialBodyTolerance([100, 200, 300, 103, 204, 300]), 5e-6);
assert.equal(initialBodyTolerance([0, 0, 0, 30, 40, 0]), 5e-5);
assert.equal(initialBodyTolerance([0, 0, 0]), 1e-7);
assert.equal(initialBodyTolerance([]), 1e-7);
assert.equal(initialBodyTolerance([NaN, 0, 0]), 1e-7);
const good = { positions: [0, 0, 0, 1, 2, 3], kind: 'free' };
assert.deepEqual(bodyEdgePaths({ preview: { edges: [good, { positions: [0] },
  { positions: [0, 0, NaN, 1, 1, 1] }] } }), [good]);
