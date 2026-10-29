import assert from 'node:assert/strict';
import test from 'node:test';
import { featureIconLegend } from './action-tree.js';

test('icon legend identifies every entry and keeps status indicators distinct', () => {
  const entries = featureIconLegend.flatMap(section => section.entries);
  assert.equal(entries.length, 48);
  assert.equal(new Set(entries.map(entry => entry.name)).size, entries.length);
  assert.ok(entries.every(entry => typeof entry.label === 'string' && entry.label.length > 0));
  assert.deepEqual(entries.filter(entry => entry.state).map(entry => entry.name),
    ['ready', 'unevaluated', 'stale', 'running', 'failed', 'blocked', 'needs_input']);
  assert.match(entries.find(entry => entry.name === 'needs_input').detail, /source volume/);
  assert.match(entries.find(entry => entry.name === 'plane_relationship').detail, /Coincident or parallel/);
  assert.match(entries.find(entry => entry.name === 'joint_fit').label, /Legacy/);
  assert.equal(entries.find(entry => entry.name === 'coaxial_group').label, 'Coaxial relationship');
  assert.equal(entries.find(entry => entry.name === 'surface_selection').label, 'Seeded surface selection');
});
