import assert from 'node:assert/strict';
import { SOURCE_GRAPH_PROJECTION, projectSourceGraph } from './source-semantic.mjs';

export const WORKSPACE_GRAPH_SCHEMA = 'scansor-workspace-graph-semantic-v2';
export const WORKSPACE_GRAPH_PROJECTION = SOURCE_GRAPH_PROJECTION;
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const epoch = value => Number.isSafeInteger(value) && value >= 0;

function terminal(graph) {
  projectSourceGraph(graph);
  assert.equal(graph.evaluation_running, false, 'Workspace graph must be settled');
  assert.deepEqual(graph.errors, {}, 'Workspace graph must have no errors');
  if (Object.hasOwn(graph, 'required_failures')) assert.deepEqual(graph.required_failures, []);
  assert.ok(object(graph.states) && Object.keys(graph.states).length > 0);
  assert.ok(Object.values(graph.states).every(value => value === 'ready'), 'Workspace actions must be ready');
  assert.ok(graph.evaluation_error === undefined || graph.evaluation_error === null);
}

function boundGraph(graph, proof) {
  assert.equal(graph.project_generation, proof.generation, 'Graph must belong to its independently observed workspace epoch');
  const sources = graph.recipe.nodes.filter(node => node.operation === 'source');
  assert.equal(sources.length, 1);
  const identity = proof.metadata.source_identity;
  for (const key of ['source_sha256', 'reference_sha256', 'descriptor'])
    assert.deepEqual(sources[0][key], identity[key], 'Graph source binding differs: ' + key);
}

export function assertWorkspaceEvidence(report) {
  assert.equal(report.workspace_graph_schema, WORKSPACE_GRAPH_SCHEMA);
  assert.deepEqual(report.workspace_graph_projection, WORKSPACE_GRAPH_PROJECTION);
  assert.deepEqual(Object.keys(report.browser_snapshots).sort(), ['default', 'edited', 'replayed']);
  assert.deepEqual(Object.keys(report.native_snapshots).sort(), ['default', 'edited']);
  assert.deepEqual(Object.keys(report.native_evaluation_responses).sort(), ['default', 'edited']);
  assert.deepEqual(Object.keys(report.browser_workspace).sort(), ['default', 'edited', 'replay_after', 'replay_before']);
  assert.deepEqual(Object.keys(report.native_workspace).sort(), ['default', 'edited']);
  let identity;
  for (const proofs of [report.browser_workspace, report.native_workspace]) {
    for (const proof of Object.values(proofs)) {
      assert.ok(object(proof));
      assert.deepEqual(Object.keys(proof).sort(), ['generation', 'graph', 'metadata']);
      assert.ok(epoch(proof.generation), 'Workspace epoch must be a safe nonnegative integer');
      assert.equal(proof.metadata.project_generation, proof.generation);
      assert.equal(proof.graph.project_generation, proof.generation);
      assert.deepEqual(proof.metadata.graph, proof.graph, 'Bundle graph differs from metadata graph');
      assert.deepEqual(proof.metadata.source_mode, { kind: 'example', example: 'nozzle' });
      assert.ok(object(proof.metadata.source_identity));
      for (const key of ['source_sha256', 'reference_sha256'])
        assert.ok(typeof proof.metadata.source_identity[key] === 'string' && /^[0-9a-f]{64}$/.test(proof.metadata.source_identity[key]), 'Invalid legacy source digest: ' + key);
      assert.equal(proof.metadata.source_identity.descriptor, null, 'Legacy example identity must have a null descriptor');
      identity ??= proof.metadata.source_identity;
      assert.deepEqual(proof.metadata.source_identity, identity, 'Workspace source identity changed');
      projectSourceGraph(proof.graph);
      boundGraph(proof.graph, proof);
    }
  }
  const browser = report.browser_workspace, native = report.native_workspace;
  terminal(browser.replay_before.graph);
  assert.equal(browser.replay_after.graph.evaluation_running, false);
  assert.deepEqual(browser.replay_after.graph.errors, {});
  if (Object.hasOwn(browser.replay_after.graph, 'required_failures')) assert.deepEqual(browser.replay_after.graph.required_failures, []);
  assert.ok(browser.replay_after.graph.evaluation_error === undefined || browser.replay_after.graph.evaluation_error === null);
  assert.ok(object(browser.replay_after.graph.states) && Object.keys(browser.replay_after.graph.states).length > 0);
  assert.ok(Object.values(browser.replay_after.graph.states).every(value => value === 'unevaluated'),
    'Reopened actions must start with fresh unevaluated states');
  assert.deepEqual(Object.keys(browser.replay_after.graph.states).sort(), browser.replay_after.graph.recipe.nodes.map(node => node.id).sort(),
    'Reopened actions states must cover every recipe action exactly');
  assert.deepEqual(browser.replay_after.graph.results, {}, 'Reopened actions must discard computed results');
  assert.deepEqual(browser.replay_after.graph.derived, {}, 'Reopened actions must discard computed derived state');
  assert.equal(browser.replay_after.graph.result, null, 'Reopened actions must discard the computed output');
  assert.equal(browser.default.generation, browser.edited.generation);
  assert.equal(browser.replay_after.generation, browser.replay_before.generation + 1,
    'Whole-project reopen must advance the actual replay page epoch exactly once');
  for (const name of ['default', 'edited']) {
    assert.equal(native[name].generation, 0, 'Native reference must use its own fresh workspace epoch');
    for (const [snapshots, proofs] of [[report.browser_snapshots, browser], [report.native_snapshots, native]]) {
      terminal(proofs[name].graph);
      terminal(snapshots[name]);
      boundGraph(snapshots[name], proofs[name]);
      assert.deepEqual(projectSourceGraph(proofs[name].graph), projectSourceGraph(snapshots[name]),
        'Workspace proof differs from the retained raw snapshot');
    }
    const response = report.native_evaluation_responses[name];
    terminal(response);
    assert.deepEqual(response.required_failures, []);
    assert.equal(response.project_generation, native[name].generation);
    assert.deepEqual(response.recipe, report.native_snapshots[name].recipe);
    assert.equal(response.token, report.native_snapshots[name].token);
    assert.deepEqual(projectSourceGraph(response), projectSourceGraph(report.native_snapshots[name]),
      'Native evaluation response differs from its retained semantic snapshot');
  }
  terminal(report.browser_snapshots.replayed);
  boundGraph(report.browser_snapshots.replayed, browser.replay_after);
  assert.deepEqual(report.browser_snapshots.replayed.recipe, browser.replay_after.graph.recipe);
  assert.equal(report.browser_snapshots.replayed.token, browser.replay_after.graph.token);
  assert.deepEqual(browser.replay_after.graph.recipe, report.browser_snapshots.edited.recipe);
  assert.equal(browser.replay_after.graph.token, report.browser_snapshots.edited.token);
}

export function workspaceSemanticCases(report) {
  assertWorkspaceEvidence(report);
  const b = report.browser_snapshots, n = report.native_snapshots;
  return [
    { id: 'replay', profile: 'graph-snapshot', actual: projectSourceGraph(b.replayed), expected: projectSourceGraph(b.edited) },
    { id: 'default', profile: 'graph-snapshot', actual: projectSourceGraph(b.default), expected: projectSourceGraph(n.default) },
    { id: 'edited', profile: 'graph-snapshot', actual: projectSourceGraph(b.edited), expected: projectSourceGraph(n.edited) },
    { id: 'replayed', profile: 'graph-snapshot', actual: projectSourceGraph(b.replayed), expected: projectSourceGraph(n.edited) },
  ];
}
