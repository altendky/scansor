import assert from 'node:assert/strict';
import test from 'node:test';
import { axisGeometry, revolutionGeometry } from './analytic-geometry.js';

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} != ${b}`);
const canonical = () => ({ kind: 'cylinder', analytic: {
  kind: 'cylinder', origin: [3, -4, 5], axis: [1, 0, 0],
  radius: 2.5, slope: 0, axial_domain: [-2, 4],
} });

test('horizontal canonical geometry owns anchor, direction and support without legacy parameters', () => {
  const fitted = canonical(), before = structuredClone(fitted), result = revolutionGeometry(fitted);
  assert.deepEqual(result, fitted.analytic);
  assert.deepEqual(axisGeometry(fitted), { origin: [3, -4, 5], axis: [1, 0, 0] });
  assert.equal(result.parameters, undefined);
  result.origin[0] = 100;
  result.axis[0] = 0;
  result.axial_domain[0] = -100;
  assert.deepEqual(fitted, before);
});

test('canonical geometry takes precedence over contradictory legacy parameters and support', () => {
  const fitted = { ...canonical(), parameters: [NaN], axial_domain: ['invalid', null] };
  assert.deepEqual(revolutionGeometry(fitted), canonical().analytic);
});

test('legacy geometry adapts the documented positive-Z chart with optional support', () => {
  const p = [2, -3, 0.2, -0.3, 4, 7, 0.5], domain = [-2, 5],
    result = revolutionGeometry({ kind: 'cone', parameters: p, axial_domain: domain }),
    length = Math.hypot(0.2, -0.3, 1);
  assert.deepEqual(result.origin, [2, -3, 0]);
  result.axis.forEach((value, index) => near(value, [0.2, -0.3, 1][index] / length));
  assert.equal(result.radius, 4);
  assert.equal(result.slope, 0.5);
  assert.deepEqual(result.axial_domain, domain);
  assert.notEqual(result.axial_domain, domain);
  assert.equal(revolutionGeometry({ kind: 'cone', parameters: p }).axial_domain, null);
  assert.deepEqual(p, [2, -3, 0.2, -0.3, 4, 7, 0.5]);
});

test('finite cones permit a nonpositive radius at an extrapolated anchor', () => {
  for (const radius of [-1, 0]) {
    const fitted = { kind: 'cone', analytic: { ...canonical().analytic,
      kind: 'cone', radius, slope: 0.5, axial_domain: [8, 15] } },
      geometry = revolutionGeometry(fitted);
    assert.equal(geometry.radius, radius);
    assert.ok(geometry.radius + geometry.slope * geometry.axial_domain[0] > 0);
  }
  // Missing legacy support remains explicit; observation projection validates
  // each projected radius instead of inventing a source or action domain.
  assert.equal(revolutionGeometry({ kind: 'cone', parameters: [0, 0, 0, 0, -3, 0, 0.5] }).axial_domain, null);
});

test('canonical unit-axis validation accepts only normalization roundoff', () => {
  const fitted = canonical();
  fitted.analytic.axis = [1 + 5e-11, 0, 0];
  assert.deepEqual(revolutionGeometry(fitted).axis, [1, 0, 0]);
  fitted.analytic.axis = [1 + 2e-10, 0, 0];
  assert.throws(() => revolutionGeometry(fitted), /unit direction/);
});

test('malformed explicit analytic records fail without legacy fallback', () => {
  const legacy = { kind: 'cylinder', parameters: [0, 0, 0, 0, 2, 0, 0] },
    valid = canonical().analytic,
    records = [null, [], {}, { ...valid, kind: 'cone' }, { ...valid, origin: [0, 0] },
      { ...valid, origin: [0, NaN, 0] }, { ...valid, axis: [0, 0, 0] },
      { ...valid, axis: [2, 0, 0] }, { ...valid, axis: [Infinity, 0, 0] },
      { ...valid, radius: 0 }, { ...valid, radius: -1 }, { ...valid, radius: NaN },
      { ...valid, slope: 0.1 }, { ...valid, slope: Infinity },
      { ...valid, axial_domain: undefined }, { ...valid, axial_domain: null },
      { ...valid, axial_domain: [0, 0] }, { ...valid, axial_domain: [1, -1] },
      { ...valid, axial_domain: [0, Infinity] }];
  for (const analytic of records) {
    assert.throws(() => revolutionGeometry({ ...legacy, analytic }), TypeError);
    assert.throws(() => axisGeometry({ ...legacy, analytic }), TypeError);
  }
});

test('cone support validates endpoints rather than requiring a positive extrapolated anchor', () => {
  for (const [radius, slope, axial_domain] of [[1, 1, [-2, 3]], [1, -1, [0, 1]],
    [1e308, 1e308, [0, 2]]]) {
    const fitted = { kind: 'cone', analytic: { ...canonical().analytic,
      kind: 'cone', radius, slope, axial_domain } };
    assert.throws(() => revolutionGeometry(fitted), /positive throughout/);
  }
});

test('legacy malformed parameters, cylinder taper and invalid declared support fail explicitly', () => {
  for (const record of [null, {}, { kind: 'sphere', parameters: [0, 0, 0, 2] },
    { kind: 'cylinder', parameters: [0, 0, 0, 0, 2, 0] },
    { kind: 'cylinder', parameters: [0, 0, 0, 0, 2, 0, NaN] },
    { kind: 'cylinder', parameters: [0, 0, 0, 0, 2, 0, 0.1] },
    { kind: 'cylinder', parameters: [0, 0, 0, 0, 2, 0, 0], axial_domain: [4, -2] }])
    assert.throws(() => revolutionGeometry(record), TypeError);
});

test('axis access preserves authored slope intent and explicit directed geometry', () => {
  const authored = { parameters: [2, -3, 0.2, -0.3], direction_reversed: true },
    before = structuredClone(authored), adapted = axisGeometry(authored), length = Math.hypot(0.2, -0.3, 1);
  assert.deepEqual(adapted.origin, [2, -3, 0]);
  adapted.axis.forEach((value, index) => near(value, [0.2, -0.3, 1][index] / length));
  assert.deepEqual(authored, before); // Reversal remains the datum caller's role.
  assert.deepEqual(axisGeometry({ axis_display: [-1, 0, 0], point_display: [3, -4, 5] }),
    { origin: [3, -4, 5], axis: [-1, 0, 0] });
  assert.throws(() => axisGeometry({ axis_display: [0, 0, 0], point_display: [3, -4, 5] }), TypeError);
});
