import assert from 'node:assert/strict';
import { uniqueFeatureLabel } from './feature-names.js';

const nodes = [
  { label: 'Reference plane' },
  { label: 'reference plane 2' },
  { label: '  SURFACE FIT  ' },
];

assert.equal(uniqueFeatureLabel('Reference plane', nodes), 'Reference plane 3');
assert.equal(uniqueFeatureLabel('Surface fit', nodes), 'Surface fit 2');
assert.equal(uniqueFeatureLabel('Reference axis', nodes), 'Reference axis');
assert.equal(uniqueFeatureLabel(' ', nodes), 'Feature');
assert.equal(
  uniqueFeatureLabel(
    'A very long default feature name',
    [{ label: 'A very lon' }, { label: 'A very l 2' }],
    10,
  ),
  'A very l 3',
);
