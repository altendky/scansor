import assert from 'node:assert/strict';
import test from 'node:test';

import { buildFeatureGraph, layoutFeatureGraph } from './feature-graph-view.js';

const recipe = {
  nodes: [
    { id: 'mesh', label: 'Mesh', operation: 'source' },
    { id: 'a-points', label: 'A points', operation: 'selection', source: 'mesh' },
    { id: 'b-points', label: 'B points', operation: 'selection', source: 'mesh' },
    { id: 'axis', label: 'Axis', operation: 'axis' },
    {
      id: 'outer-a',
      label: 'Outer A',
      operation: 'fit',
      kind: 'cylinder',
      selections: ['a-points'],
      axis: 'axis',
    },
    {
      id: 'outer-b',
      label: 'Outer B',
      operation: 'fit',
      kind: 'cylinder',
      selections: ['b-points'],
    },
    {
      id: 'equal',
      label: 'Equal radii',
      operation: 'equal_radii',
      surfaces: ['outer-a', 'outer-b'],
    },
    {
      id: 'joint',
      label: 'Joint',
      operation: 'axis_solve',
      axis: 'axis',
      factors: ['outer-a', 'equal'],
    },
    {
      id: 'reuse',
      label: 'Reuse',
      operation: 'feature_reuse',
      fits: ['outer-a'],
      lineage: ['a-points', 'outer-a'],
      reference_selection: 'a-points',
      target_selections: ['b-points'],
    },
    {
      id: 'generated-selection',
      label: 'Generated selection',
      operation: 'reuse_selection',
      reuse: 'reuse',
      fit: 'outer-a',
      source_selection: 'a-points',
      target_selection: 'b-points',
      managed_by: 'reuse',
    },
  ],
};

test('combined graph distinguishes dependency, relationship, solve, and ownership edges', () => {
  const graph = buildFeatureGraph(recipe, {
    showSelections: true,
    showGenerated: true,
  });
  assert(graph.edges.some((edge) => edge.from === 'axis' && edge.to === 'outer-a' && edge.kind === 'dependency'));
  assert(graph.edges.some((edge) => edge.from === 'outer-a' && edge.to === 'equal' && edge.kind === 'relationship'));
  assert(graph.edges.some((edge) => edge.from === 'equal' && edge.to === 'joint' && edge.kind === 'solve'));
  assert(graph.edges.some((edge) => edge.from === 'reuse' && edge.to === 'generated-selection' && edge.kind === 'ownership'));
});

test('default graph hides selection details and generated outputs', () => {
  const graph = buildFeatureGraph(recipe);
  assert(!graph.nodes.some((node) => node.id === 'mesh'));
  assert(!graph.nodes.some((node) => node.id === 'a-points'));
  assert(!graph.nodes.some((node) => node.id === 'generated-selection'));
  assert(graph.nodes.some((node) => node.id === 'reuse'));
});

test('relationship and selected-neighborhood lenses retain only useful local nodes', () => {
  const relationships = buildFeatureGraph(recipe, { lens: 'relationships' });
  assert.deepEqual(
    new Set(relationships.nodes.map((node) => node.id)),
    new Set(['outer-a', 'outer-b', 'equal']),
  );

  const neighborhood = buildFeatureGraph(recipe, {
    selected: new Set(['equal']),
    selectedNeighborhood: true,
  });
  assert.deepEqual(
    new Set(neighborhood.nodes.map((node) => node.id)),
    new Set(['outer-a', 'outer-b', 'equal', 'joint']),
  );
});

test('layout is deterministic and advances downstream nodes', () => {
  const graph = buildFeatureGraph(recipe, { lens: 'relationships' }),
    first = layoutFeatureGraph(graph),
    second = layoutFeatureGraph(graph);
  assert.deepEqual(first, second);
  assert(first.positions.get('equal').x > first.positions.get('outer-a').x);
  assert.notEqual(first.positions.get('outer-a').y, first.positions.get('outer-b').y);
});
