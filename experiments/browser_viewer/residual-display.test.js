import assert from 'node:assert/strict';
import test from 'node:test';
import { residualRange, resultResidualSurfaces } from './residual-display.js';

test('a standalone fit supplies one residual surface', () => {
  const fit = { ids: [2, 4], residuals: [-0.2, 0.1] };
  assert.deepEqual(resultResidualSurfaces(fit, 'fit-1'), [['fit-1', fit]]);
});

test('a relationship supplies all of its fitted surfaces', () => {
  const a = { ids: [1], residuals: [-0.25] },
    b = { ids: [8], residuals: [0.5] };
  assert.deepEqual(resultResidualSurfaces({ surfaces: { a, b } }), [['a', a], ['b', b]]);
  assert.deepEqual(residualRange([['a', a], ['b', b]]), {
    minimum: -0.25,
    maximum: 0.5,
    limit: 0.5,
  });
});

test('residual range includes zero when observations lie on only one side', () => {
  const positive = { ids: [1, 2], residuals: [0.1, 0.2] };
  assert.deepEqual(residualRange([['positive', positive]]), {
    minimum: 0,
    maximum: 0.2,
    limit: 0.2,
  });
});

test('results without aligned finite residuals are not displayed', () => {
  assert.deepEqual(resultResidualSurfaces({ ids: [1] }), []);
  assert.deepEqual(resultResidualSurfaces({ ids: [1, 2], residuals: [0.1] }), []);
  assert.deepEqual(resultResidualSurfaces({ ids: [1], residuals: [Number.NaN] }), []);
});
