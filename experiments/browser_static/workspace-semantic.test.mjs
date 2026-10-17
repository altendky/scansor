import test from 'node:test';
import assert from 'node:assert/strict';
import { WORKSPACE_GRAPH_SCHEMA, WORKSPACE_GRAPH_PROJECTION, assertWorkspaceEvidence,
  workspaceSemanticCases } from './workspace-semantic.mjs';

const source = { id: 'scan', operation: 'source', source_sha256: 'a'.repeat(64), reference_sha256: 'b'.repeat(64), descriptor: null };
function graph(generation, edited = false) {
  return { recipe: { nodes: [structuredClone(source), { id: 'fit', operation: 'fit', label: edited ? 'Edited' : 'Original' }] },
    token: edited ? 'edited-token' : 'default-token', states: { scan: 'ready', fit: 'ready' }, errors: {}, diagnostics: {},
    memberships: {}, results: { fit: { radius: 2 } }, result: { radius: 2 }, derived: {},
    input_catalogue: [], input_requirements: {}, project_generation: generation, revision: 3,
    input_contracts: {}, relationship_contracts: {},
    evaluation_running: false };
}
function proof(value) {
  return { generation: value.project_generation, graph: structuredClone(value), metadata: {
    project_generation: value.project_generation, graph: structuredClone(value),
    source_mode: { kind: 'example', example: 'nozzle' }, source_identity: structuredClone(source),
  } };
}
function evidence() {
  const b = { default: graph(0), edited: graph(0, true), replayed: graph(1, true) };
  const n = { default: graph(0), edited: graph(0, true) };
  const dirty = graph(1, true); dirty.states = { scan: 'unevaluated', fit: 'unevaluated' }; dirty.results = {}; dirty.result = null;
  return { workspace_graph_schema: WORKSPACE_GRAPH_SCHEMA, workspace_graph_projection: [...WORKSPACE_GRAPH_PROJECTION],
    browser_snapshots: b, native_snapshots: n,
    native_evaluation_responses: Object.fromEntries(Object.entries(n).map(([key, value]) => [key, { ...structuredClone(value), required_failures: [] }])),
    browser_workspace: { default: proof(b.default), edited: proof(b.edited), replay_before: proof(graph(0)), replay_after: proof(dirty) },
    native_workspace: { default: proof(n.default), edited: proof(n.edited) } };
}

test('real independent epochs permit only transport projection, preserving all raw records', () => {
  assert.equal(WORKSPACE_GRAPH_SCHEMA, 'scansor-workspace-graph-semantic-v2');
  const report = evidence(), before = structuredClone(report);
  const cases = workspaceSemanticCases(report);
  assert.deepEqual(cases.map(value => [value.id, value.profile]), [
    ['replay', 'graph-snapshot'], ['default', 'graph-snapshot'], ['edited', 'graph-snapshot'], ['replayed', 'graph-snapshot'],
  ]);
  for (const value of cases) assert.deepEqual(value.actual, value.expected);
  assert.equal(report.browser_snapshots.replayed.project_generation, 1);
  assert.equal(report.native_snapshots.edited.project_generation, 0);
  assert.deepEqual(report, before);
});

for (const [name, mutate] of [
  ['schema', r => { r.workspace_graph_schema = 'unknown'; }],
  ['projection', r => { r.workspace_graph_projection.pop(); }],
  ['missing before', r => { delete r.browser_workspace.replay_before; }],
  ['uncompared browser snapshot', r => { r.browser_snapshots.other = graph(0); }],
  ['uncompared native snapshot', r => { r.native_snapshots.other = graph(0); }],
  ['uncompared native evaluation', r => { r.native_evaluation_responses.other = graph(0); }],
  ['unknown proof', r => { r.browser_workspace.default.unreviewed = true; }],
  ['bool epoch', r => { r.browser_workspace.default.generation = false; }],
  ['negative epoch', r => { r.browser_workspace.default.generation = -1; }],
  ['unsafe epoch', r => { r.browser_workspace.default.generation = 2 ** 53; }],
  ['metadata epoch', r => { r.browser_workspace.edited.metadata.project_generation = 8; }],
  ['bundle graph epoch', r => { r.browser_workspace.edited.graph.project_generation = 8; }],
  ['metadata graph', r => { r.browser_workspace.default.metadata.graph.result.radius = 3; }],
  ['mode', r => { r.native_workspace.default.metadata.source_mode = { kind: 'scan' }; }],
  ['identity', r => { r.native_workspace.default.metadata.source_identity.source_sha256 = 'c'.repeat(64); }],
  ['raw source', r => { r.browser_snapshots.edited.recipe.nodes[0].reference_sha256 = 'c'.repeat(64); }],
  ['raw epoch', r => { r.browser_snapshots.replayed.project_generation = 0; }],
  ['wrong increment', r => { const p = r.browser_workspace.replay_before; p.generation = p.graph.project_generation = p.metadata.project_generation = p.metadata.graph.project_generation = 1; }],
  ['native copied epoch', r => { const p = r.native_workspace.edited; p.generation = p.graph.project_generation = p.metadata.project_generation = p.metadata.graph.project_generation = 1; }],
  ['raw proof mismatch', r => { r.browser_snapshots.edited.result.radius = 3; }],
  ['replay token', r => { r.browser_snapshots.replayed.token = 'wrong'; }],
  ['replay recipe', r => { r.browser_snapshots.replayed.recipe.nodes[1].label = 'wrong'; }],
  ['running', r => { r.browser_snapshots.default.evaluation_running = true; }],
  ['not ready', r => { r.native_snapshots.default.states.fit = 'unevaluated'; }],
  ['errors', r => { r.browser_snapshots.replayed.errors.fit = 'failed'; }],
  ['evaluation error', r => { r.browser_snapshots.edited.evaluation_error = 'failed'; }],
  ['native closure', r => { r.native_evaluation_responses.default.required_failures = ['fit']; }],
  ['native response output geometry', r => { r.native_evaluation_responses.default.result.radius = 3; }],
  ['native response node geometry', r => { r.native_evaluation_responses.edited.results.fit.radius = 3; }],
  ['native response diagnostics', r => { r.native_evaluation_responses.default.diagnostics.unbound = 'changed'; }],
  ['snapshot closure', r => { r.browser_snapshots.replayed.required_failures = ['fit']; }],
]) test('independent workspace evidence rejects ' + name, () => {
  const report = evidence(); mutate(report); assert.throws(() => assertWorkspaceEvidence(report));
});

for (const [name, mutate] of [
  ['ready state', graph => { graph.states.fit = 'ready'; }],
  ['missing action state', graph => { delete graph.states.fit; }],
  ['computed results', graph => { graph.results.fit = { radius: 2 }; }],
  ['computed derived state', graph => { graph.derived.fit = { surface: 'plane' }; }],
  ['computed output', graph => { graph.result = { radius: 2 }; }],
]) test('whole-project replay cannot retain ' + name + ' in a consistent full bundle', () => {
  const report = evidence(), proof = report.browser_workspace.replay_after;
  mutate(proof.graph);
  proof.metadata.graph = structuredClone(proof.graph);
  assert.throws(() => assertWorkspaceEvidence(report), /Reopened actions/);
});

test('fresh replay cannot publish a failed required closure in an otherwise consistent bundle', () => {
  const report = evidence(), proof = report.browser_workspace.replay_after;
  proof.graph.required_failures = ['fit'];
  proof.metadata.graph = structuredClone(proof.graph);
  assert.throws(() => assertWorkspaceEvidence(report));
});

for (const field of WORKSPACE_GRAPH_PROJECTION) test('semantic field cannot be removed from every raw operand: ' + field, () => {
  const report = evidence();
  for (const values of [report.browser_snapshots, report.native_snapshots]) for (const value of Object.values(values)) delete value[field];
  assert.throws(() => workspaceSemanticCases(report));
});
test('unknown semantic publication is not silently projected', () => {
  const report = evidence(); report.browser_snapshots.replayed.new_acceptance = 'failed';
  assert.throws(() => workspaceSemanticCases(report), /unknown field/);
});

for (const [field, value] of [
  ['source_sha256', 'a'.repeat(63)], ['source_sha256', 'A'.repeat(64)],
  ['reference_sha256', 3], ['reference_sha256', 'not-a-digest'], ['descriptor', {}],
]) test('matching invalid legacy identities cannot bypass source validation: ' + field + String(value), () => {
  const report = evidence();
  for (const proofs of [report.browser_workspace, report.native_workspace])
    for (const p of Object.values(proofs)) p.metadata.source_identity[field] = value;
  assert.throws(() => assertWorkspaceEvidence(report), /digest|descriptor/);
});
