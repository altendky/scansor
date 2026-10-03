import assert from 'node:assert/strict';
import test from 'node:test';
import { fitQualities, parseRmsLimit, residualRange, resultResidualSurfaces,
  summarizeFitQuality } from './residual-display.js';

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

const qualityRecipe = { nodes: [
  { id: 'plane', label: 'Clock', operation: 'reference_plane' },
  { id: 'source-fit', label: 'Source fit', operation: 'fit', reference_plane: 'plane' },
  { id: 'reused-fit', label: 'Reused fit', operation: 'fit' },
  { id: 'reuse', operation: 'feature_reuse' },
] },
  ready = { 'source-fit': 'ready', 'reused-fit': 'ready' },
  qualityResults = {
    'source-fit': { weighted_rms: 0.382, ids: [1, 2], residuals: [-0.656, 0.1], condition: 37.3 },
    'reused-fit': { weighted_rms: 0.127, ids: [4], residuals: [-0.127], resolved_by: 'equal_radii' },
    reuse: { matches: [{ rms: 999 }] },
  };

test('RMS limits must be explicitly positive and finite; blank means unassessed', () => {
  for (const value of ['', ' ', null, undefined, 0, -1, 'NaN', Infinity, 'garbage'])
    assert.equal(parseRmsLimit(value), null);
  assert.equal(parseRmsLimit('0.2'), 0.2);
  assert.equal(parseRmsLimit('1e-6'), 1e-6);
  const quality = fitQualities(qualityRecipe, ready, qualityResults)['source-fit'];
  assert.equal(quality.status, 'unrated');
  assert.equal(quality.peak, 0.656);
  assert.match(quality.tooltip, /No RMS limit set/);
});

test('fit qualities use each resolved fit, not inherited source or match RMS', () => {
  const qualities = fitQualities(qualityRecipe, ready, qualityResults, 0.2);
  assert.equal(qualities['source-fit'].label, 'RMS 0.382');
  assert.equal(qualities['source-fit'].status, 'warning');
  assert.equal(qualities['reused-fit'].label, 'RMS 0.127');
  assert.equal(qualities['reused-fit'].status, 'within');
  assert.equal(qualities.reuse, undefined);
  assert.match(qualities['source-fit'].tooltip, /Reference plane: Clock/);
  assert.match(qualities['source-fit'].tooltip, /Condition estimate: 37.30 \(not a fit-error score\)/);
  assert.match(qualities['reused-fit'].tooltip, /Current resolved geometry.*Resolved by: equal_radii/);
  assert.match(qualities['source-fit'].tooltip, /Source-scan length units/);
});

test('warning colors compare RMS, not worst residual, against the explicitly named RMS limit', () => {
  const qualities = fitQualities(qualityRecipe, ready, qualityResults, 0.382);
  assert.equal(qualities['source-fit'].status, 'within');
  assert.equal(qualities['source-fit'].peak, 0.656);
});

test('stale, running, failed and errored fit results cannot advertise cached quality', () => {
  for (const state of ['stale', 'running', 'failed', 'unevaluated']) {
    const quality = fitQualities(qualityRecipe, { ...ready, 'source-fit': state }, qualityResults, 0.2)['source-fit'];
    assert.equal(quality.rms, null);
    assert.equal(quality.label, 'RMS —');
    assert.equal(quality.status, 'unavailable');
  }
  assert.equal(fitQualities(qualityRecipe, ready, qualityResults, 0.2,
    { 'source-fit': 'Failed' })['source-fit'].rms, null);
});

test('missing or malformed numerical results are not rated', () => {
  for (const result of [undefined, {}, { ...qualityResults['source-fit'], weighted_rms: -1 },
    { ...qualityResults['source-fit'], weighted_rms: Infinity },
    { ...qualityResults['source-fit'], ids: [] },
    { ...qualityResults['source-fit'], residuals: [NaN, 0] },
    { ...qualityResults['source-fit'], residuals: [0] },
    { weighted_rms: 0.1, surfaces: { a: qualityResults['source-fit'] } }]) {
    assert.equal(fitQualities(qualityRecipe, ready, { 'source-fit': result })['source-fit'].rms, null);
  }
});

test('reuse summaries take the worst member RMS, with warning counts and completeness', () => {
  const qualities = fitQualities(qualityRecipe, ready, qualityResults, 0.2),
    summary = summarizeFitQuality(Object.values(qualities));
  assert.equal(summary.rms, 0.382);
  assert.equal(summary.label, 'max 0.382 · 1!');
  assert.equal(summary.warnings, 1);
  assert.equal(summary.status, 'warning');
  assert.match(summary.tooltip, /2\/2 current fits/);
  assert.equal(summarizeFitQuality([]), null);
  assert.equal(summarizeFitQuality([qualities['reused-fit']]).status, 'within');
  assert.equal(summarizeFitQuality(Object.values(fitQualities(qualityRecipe, ready, qualityResults))).status, 'unrated');
});

test('incomplete summaries do not silently report only the successful subset as complete', () => {
  const qualities = fitQualities(qualityRecipe, { ...ready, 'reused-fit': 'stale' }, qualityResults, 0.2),
    summary = summarizeFitQuality(Object.values(qualities));
  assert.equal(summary.rms, null);
  assert.equal(summary.label, 'RMS — · 1!');
  assert.match(summary.tooltip, /1\/2 current fits/);
  assert.match(summary.tooltip, /Summary incomplete/);
  assert.equal(summary.warnings, 1);
});

test('zero RMS and missing conditioning remain valid fit quality', () => {
  const result = { ids: [1], residuals: [0], weighted_rms: 0 },
    quality = fitQualities(qualityRecipe, ready, { 'source-fit': result }, 0.2)['source-fit'];
  assert.equal(quality.rms, 0);
  assert.equal(quality.status, 'within');
  assert.doesNotMatch(quality.tooltip, /Condition/);
});
