import test from 'node:test';
import assert from 'node:assert/strict';
import { copyFitDomain, parseFitDomain, defaultFitDomain, editableManagedSupport,
  fitSupportChanges } from './fit-intent.js';

test('blank support remains automatic through editing and cloning', () => {
  assert.equal(parseFitDomain('', '  '), null);
  assert.equal(copyFitDomain(null), null);
  assert.equal(copyFitDomain(undefined), null);
  assert.equal(defaultFitDomain('cylinder', { version: 'interactive-triangle-scan-v1' }), null);
  assert.equal(defaultFitDomain('cone', { version: 'interactive-triangle-scan-v1' }), null);
});

test('explicit support stays authored and copies independently', () => {
  const domain = [-2, 5], copied = copyFitDomain(domain);
  assert.deepEqual(parseFitDomain(' -2 ', '5'), domain);
  assert.deepEqual(copied, domain);
  copied[0] = -3;
  assert.deepEqual(domain, [-2, 5]);
  assert.deepEqual(defaultFitDomain('cylinder', null), [-2, 5]);
  assert.deepEqual(defaultFitDomain('cone', null), [-2, 5]);
});

test('partial, nonfinite and reversed support cannot silently become hard zero bounds', () => {
  for (const [start, end] of [['', '5'], ['-2', ''], ['NaN', '5'], ['-2', 'Infinity'], ['5', '-2'], ['2', '2']])
    assert.throws(() => parseFitDomain(start, end), /both increasing axial bounds/);
});

test('managed imported targets explicitly choose inherited or local support', () => {
  const nodes = [{ id: 'reuse', operation: 'feature_reuse' }],
    fit = { operation: 'fit', kind: 'cylinder', managed_by: 'reuse' };
  assert.equal(editableManagedSupport(fit, nodes, {}), true);
  assert.equal(editableManagedSupport({ ...fit, kind: 'cone' }, nodes, {}), true);
  assert.equal(editableManagedSupport(fit, nodes, null), false);
  assert.equal(editableManagedSupport({ ...fit, managed_by: 'foreign' }, nodes, {}), false);
  assert.equal(editableManagedSupport({ ...fit, kind: 'sphere' }, nodes, {}), false);
  assert.deepEqual(fitSupportChanges('prototype', 'invalid', ''), { support_authority: 'prototype' });
  assert.deepEqual(fitSupportChanges('local', '', ''), { support_authority: 'local', axial_domain: null });
  assert.deepEqual(fitSupportChanges('local', '31', '34'), { support_authority: 'local', axial_domain: [31, 34] });
  assert.throws(() => fitSupportChanges('', '31', '34'), /Choose support/);
  assert.throws(() => fitSupportChanges('local', '', '34'), /both increasing/);
});
