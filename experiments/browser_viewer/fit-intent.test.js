import test from 'node:test';
import assert from 'node:assert/strict';
import { copyFitDomain, parseFitDomain, defaultFitDomain } from './fit-intent.js';

test('blank support remains automatic through editing and cloning', () => {
  assert.equal(parseFitDomain('', '  '), null);
  assert.equal(copyFitDomain(null), null);
  assert.equal(copyFitDomain(undefined), null);
  assert.equal(defaultFitDomain('cylinder', { version: 'interactive-triangle-scan-v1' }), null);
});

test('explicit support stays authored and copies independently', () => {
  const domain = [-2, 5], copied = copyFitDomain(domain);
  assert.deepEqual(parseFitDomain(' -2 ', '5'), domain);
  assert.deepEqual(copied, domain);
  copied[0] = -3;
  assert.deepEqual(domain, [-2, 5]);
  assert.deepEqual(defaultFitDomain('cylinder', null), [-2, 5]);
});

test('partial, nonfinite and reversed support cannot silently become hard zero bounds', () => {
  for (const [start, end] of [['', '5'], ['-2', ''], ['NaN', '5'], ['-2', 'Infinity'], ['5', '-2'], ['2', '2']])
    assert.throws(() => parseFitDomain(start, end), /both increasing axial bounds/);
});
