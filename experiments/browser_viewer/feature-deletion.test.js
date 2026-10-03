import assert from 'node:assert/strict';
import test from 'node:test';
import { featureDeletionPlan } from './action-tree.js';

const source = { id: 'source', label: 'Scan', operation: 'source' },
  selection = { id: 'selection', label: 'Nodes', operation: 'selection', source: 'source' },
  fit = { id: 'fit', label: 'Wall', operation: 'fit', selections: ['selection'] },
  owner = { id: 'owner', label: 'Build faces', operation: 'build_faces',
    surfaces: [{ feature: 'fit' }] },
  edge = { id: 'edge', label: 'Edge', operation: 'surface_intersection',
    first: { feature: 'fit' }, second: { feature: 'fit' }, managed_by: 'owner' },
  face = { id: 'face', label: 'Face', operation: 'trimmed_face', surface: { feature: 'fit' },
    boundaries: [{ intersection: 'edge' }], managed_by: 'owner' },
  other = { id: 'other', label: 'Independent point', operation: 'point', group_id: 'group' },
  recipe = { nodes: [source, selection, fit, owner, edge, face, other], output: 'other',
    groups: [{ id: 'group', label: 'Points' }] };
const ids = (nodes) => nodes.map((node) => node.id);

test('independent multi-delete preserves groups, survivor order and the input recipe', () => {
  const input = structuredClone(recipe),
    plan = featureDeletionPlan(input, new Set(['other', 'face', 'owner']));
  assert.deepEqual(ids(plan.selected), ['owner', 'face', 'other']);
  assert.deepEqual(ids(plan.managed), ['edge']);
  assert.deepEqual(plan.dependents, []);
  assert.deepEqual(ids(plan.recipe.nodes), ['source', 'selection', 'fit']);
  assert.equal(plan.recipe.output, 'fit');
  assert.deepEqual(plan.recipe.groups, recipe.groups);
  plan.recipe.groups[0].label = 'Changed';
  plan.recipe.nodes[1].label = 'Changed';
  assert.deepEqual(input, recipe);
});

test('dependency closure includes transitive consumers and all managed outputs', () => {
  const consumer = { id: 'consumer', label: 'Consumer', operation: 'build_faces',
    surfaces: [{ feature: 'fit' }], boundary_sources: ['face'], reused_faces: ['face'] },
    scoped = { id: 'scoped', operation: 'build_faces', surfaces: [{ feature: 'fit' }],
      face_scopes: [{ faces: ['face'] }], reused_intersections: ['edge'] },
    input = { ...recipe, nodes: [...recipe.nodes, consumer, scoped], output: 'source' },
    plan = featureDeletionPlan(input, ['selection']);
  assert.deepEqual(ids(plan.removed), ['selection', 'fit', 'owner', 'edge', 'face', 'consumer', 'scoped']);
  assert.deepEqual(ids(plan.dependents), ['fit', 'owner', 'edge', 'face', 'consumer', 'scoped']);
  assert.deepEqual(ids(plan.recipe.nodes), ['source', 'other']);
  assert.equal(plan.recipe.output, 'source');
});

test('removing a dependency of one managed output also plans its owner and siblings', () => {
  const dependent = { id: 'dependent', operation: 'point', source_fit: 'other', managed_by: 'owner' },
    downstream = { id: 'downstream', operation: 'axis', source_fit: 'dependent' },
    input = { ...recipe, nodes: [...recipe.nodes, dependent, downstream], output: 'downstream' },
    plan = featureDeletionPlan(input, ['other']);
  assert.deepEqual(ids(plan.removed), ['owner', 'edge', 'face', 'other', 'dependent', 'downstream']);
  assert.deepEqual(ids(plan.dependents), ['owner', 'edge', 'face', 'dependent', 'downstream']);
  assert.equal(plan.recipe.output, 'fit');
});

test('nested managed subtrees and inferred reuse fits are removed with their owners', () => {
  const reuse = { id: 'reuse', operation: 'feature_reuse', fits: ['fit'], lineage: [],
    reference_selection: 'selection', target_selections: ['selection'] },
    generated = { id: 'generated', operation: 'reuse_selection', reuse: 'reuse', fit: 'fit',
      source_selection: 'selection', target_selection: 'selection' },
    generatedFit = { id: 'generated-fit', operation: 'fit', selections: ['generated'] },
    nested = { ...owner, id: 'nested', managed_by: 'owner' },
    child = { ...edge, id: 'child', managed_by: 'nested' },
    input = { ...recipe, nodes: [...recipe.nodes, nested, child, reuse, generated, generatedFit] };
  assert.match(featureDeletionPlan(input, ['generated-fit']).error, /Select reuse/);
  assert.deepEqual(ids(featureDeletionPlan(input, ['reuse']).removed),
    ['reuse', 'generated', 'generated-fit']);
  assert.deepEqual(ids(featureDeletionPlan(input, ['owner', 'child']).removed),
    ['owner', 'edge', 'face', 'nested', 'child']);
});

test('empty, stale, source and standalone generated selections fail closed', () => {
  assert.match(featureDeletionPlan(recipe, []).error, /Select features/);
  assert.match(featureDeletionPlan(recipe, ['other', 'missing']).error, /selection changed/);
  assert.match(featureDeletionPlan(recipe, ['source', 'other']).error, /source mesh/);
  assert.match(featureDeletionPlan(recipe, ['face']).error, /Select Build faces/);
  assert.match(featureDeletionPlan(recipe, ['edge', 'face']).error, /Select Build faces/);
});

test('selecting all consumers needs no additional dependent approval', () => {
  const plan = featureDeletionPlan(recipe, ['selection', 'fit', 'owner']);
  assert.deepEqual(plan.dependents, []);
  assert.deepEqual(ids(plan.managed), ['edge', 'face']);
  assert.deepEqual(ids(plan.recipe.nodes), ['source', 'other']);
});
