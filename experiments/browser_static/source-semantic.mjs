// The native source probe has no browser transport epoch. Keep that boundary
// explicit while retaining the complete, unprojected graphs in the report.
import assert from 'node:assert/strict';

export const SOURCE_GRAPH_SCHEMA = 'scansor-source-graph-semantic-v3';
export const SOURCE_GRAPH_PROJECTION = Object.freeze([
  'recipe', 'token', 'states', 'errors', 'diagnostics', 'memberships',
  'results', 'result', 'derived', 'input_catalogue', 'input_requirements',
  'input_contracts', 'relationship_contracts',
]);
const transportFields = new Set([
  'revision', 'project_generation', 'required_failures', 'evaluation_running', 'evaluation_error', 'evaluation',
]);

export function projectSourceGraph(graph) {
  assert.ok(graph && typeof graph === 'object' && !Array.isArray(graph), 'Source graph must be an object');
  for (const key of SOURCE_GRAPH_PROJECTION) {
    assert.ok(Object.hasOwn(graph, key), 'Source graph is missing required semantic field: ' + key);
  }
  for (const key of Object.keys(graph)) {
    assert.ok(SOURCE_GRAPH_PROJECTION.includes(key) || transportFields.has(key),
      'Source graph has an unknown field outside the reviewed projection schema: ' + key);
  }
  if (graph.evaluation !== undefined && graph.evaluation !== null) {
    const value = graph.evaluation;
    assert.ok(value && typeof value === 'object' && !Array.isArray(value), 'Invalid evaluation lifecycle');
    assert.deepEqual(Object.keys(value).sort(), ['generation', 'id', 'kind', 'roots', 'sequence', 'state'], 'Invalid evaluation lifecycle fields');
    assert.ok(typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 128);
    assert.ok(Number.isSafeInteger(value.sequence) && value.sequence > 0);
    assert.ok(Number.isSafeInteger(value.generation) && value.generation >= 0);
    if (graph.project_generation !== undefined) assert.equal(value.generation, graph.project_generation);
    assert.ok(['queued', 'running', 'cancel-requested', 'cancelled', 'complete', 'failed'].includes(value.state));
    assert.ok(['evaluate', 'ensure'].includes(value.kind));
    assert.ok(Array.isArray(value.roots) && value.roots.every(root => typeof root === 'string'));
    assert.equal(new Set(value.roots).size, value.roots.length);
  }
  return Object.fromEntries(SOURCE_GRAPH_PROJECTION.map(key => [key, graph[key]]));
}

export function assertAcceptedSourceGraph(graph, { generation, recipe, token }) {
  projectSourceGraph(graph);
  assert.ok(Number.isSafeInteger(generation) && generation >= 0, 'Accepted source generation must be a finite integer');
  assert.equal(graph.project_generation, generation, 'Fitted graph must belong to the accepted source generation');
  assert.equal(graph.evaluation_running, false, 'Fitted source graph must be settled');
  assert.deepEqual(graph.required_failures, [], 'Fitted source graph must have no required failures');
  assert.deepEqual(graph.errors, {}, 'Fitted source graph must have no errors');
  assert.ok(graph.states && typeof graph.states === 'object' && !Array.isArray(graph.states));
  assert.ok(Object.keys(graph.states).length > 0, 'Fitted source graph must expose action states');
  for (const [id, state] of Object.entries(graph.states)) assert.equal(state, 'ready', 'Source action must be ready: ' + id);
  assert.ok(graph.evaluation_error === undefined || graph.evaluation_error === null, 'Fitted source graph must have no evaluation error');
  assert.deepEqual(graph.recipe, recipe, 'Fitted source graph must preserve the accepted authored recipe');
  assert.equal(graph.token, token, 'Fitted source graph must preserve the accepted authored token');
}

export function sourceSemanticCases(snapshots) {
  return [
    { id: 'scene', profile: 'graph-snapshot', path: 'scene.graph',
      actual: projectSourceGraph(snapshots.scene), expected: projectSourceGraph(snapshots.native_scene) },
    { id: 'scene-replayed', profile: 'graph-snapshot', path: 'scene.replayed_graph',
      actual: projectSourceGraph(snapshots.scene_replayed), expected: projectSourceGraph(snapshots.native_scene) },
    { id: 'scene-actions', profile: 'graph-snapshot', path: 'actions.scene_opened',
      actual: projectSourceGraph(snapshots.scene_actions), expected: projectSourceGraph(snapshots.native_scene) },
    { id: 'scene-actions-replayed', profile: 'graph-snapshot', path: 'actions.scene_replayed',
      actual: projectSourceGraph(snapshots.scene_actions_replayed), expected: projectSourceGraph(snapshots.native_scene) },
  ];
}
