import assert from 'node:assert/strict';
import { activeDisplayTransform } from './display-transform.js';

const identity = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
const state = {
  recipe: {
    output: 'output-transform',
    nodes: [
      { id: 'fit', operation: 'fit' },
      { id: 'preview-transform', operation: 'transform' },
      { id: 'output-transform', operation: 'transform' },
    ],
  },
  states: { 'preview-transform': 'ready', 'output-transform': 'ready' },
  results: {
    'preview-transform': { matrix: identity.map((row) => [...row]) },
    'output-transform': { matrix: identity.map((row) => [...row]) },
  },
};

assert.equal(activeDisplayTransform(state, new Set(['fit'])).id, 'output-transform');
assert.equal(
  activeDisplayTransform(state, new Set(['preview-transform'])).id,
  'preview-transform',
);
assert.equal(activeDisplayTransform(state, new Set(['fit', 'preview-transform'])).id, 'output-transform');

const stale = structuredClone(state);
stale.states['output-transform'] = 'stale';
assert.equal(activeDisplayTransform(stale, new Set(['fit'])), null);

const invalid = structuredClone(state);
invalid.results['output-transform'].matrix[0][0] = Number.NaN;
assert.equal(activeDisplayTransform(invalid), null);

const fitOutput = structuredClone(state);
fitOutput.recipe.output = 'fit';
assert.equal(activeDisplayTransform(fitOutput, new Set(['fit'])), null);
