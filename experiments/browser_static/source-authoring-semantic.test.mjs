import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { SOURCE_AUTHORING_SCHEMA, SOURCE_AUTHORING_CASES, authoringSemanticCases } from './source-authoring-semantic.mjs';
import { compareSemanticBatch } from './semantic-parity.mjs';
import { sha256 } from './server.mjs';

function source(letter, generation) {
  return { source_sha256: letter.repeat(64), project_generation: generation, source_mode: { kind: 'scan' },
    original_bytes: 300, vertices: 3, faces: 1, owned_fs_bytes: 0, owned_fs_files: [],
    descriptor: { version: 'interactive-triangle-scan-v1', profile: 'binary-triangle-ply-v1', source_sha256: letter.repeat(64), source_bytes: 300,
      interpretation_sha256: 'c'.repeat(64), vertices: 3, triangles: 1, filename: letter + '.ply', units: 'unknown' },
    positions: [[0, 0, 0], [1, 0, 0], [0, 1, 0]], normals: [[0, 0, 1], [0, 0, 1], [0, 0, 1]],
    triangles: [[0, 1, 2]], weights: [1 / 6, 1 / 6, 1 / 6] };
}
function graph(evidence, edited = false) {
  const { filename: _hint, ...semantic } = evidence.descriptor;
  const canonical = Object.fromEntries(Object.keys(semantic).sort().map(key => [key, semantic[key]]));
  const binding = createHash('sha256').update(JSON.stringify(canonical)).digest('hex');
  return { recipe: { nodes: [
    { id: 'scan', operation: 'source', source_sha256: evidence.source_sha256, reference_sha256: binding, descriptor: structuredClone(evidence.descriptor) },
    { id: 'selected', operation: 'selection', source: 'scan', ids: [0, 1, 2] },
    { id: 'fit', operation: 'fit', kind: 'plane', label: edited ? 'Renamed' : 'Original', selections: ['selected'] },
  ] }, token: edited ? 'edited' : 'original', states: { scan: 'ready', selected: 'ready', fit: 'ready' }, errors: {}, diagnostics: {},
    memberships: { selected: [0, 1, 2] }, results: { fit: { kind: 'plane', parameters: [0, 0, 1, 0], residuals: [0, 0, 0], weighted_rms: 0 } },
    result: null, derived: {}, input_catalogue: [], input_requirements: {}, revision: 3,
    input_contracts: { relationship_plane_orientation: { capabilities: ['plane_orientation'], min_count: 2 } },
    relationship_contracts: { parallel_planes: { operation: 'plane_relationship', relation: 'parallel', roles: { participants: 'relationship_plane_orientation' } } },
    project_generation: evidence.project_generation, evaluation_running: false, required_failures: [] };
}
function report() {
  const raw = source('a', 2), scene = source('b', 4);
  const graphs = { raw: graph(raw), raw_edited: graph(raw, true), scene: graph(scene) };
  const native = structuredClone(graphs);
  for (const value of Object.values(native)) { delete value.evaluation_running; delete value.project_generation; }
  return { ui_authoring: { schema: SOURCE_AUTHORING_SCHEMA, complete: true,
    cases: Object.entries(SOURCE_AUTHORING_CASES).map(([name, fields]) => ({ name, complete: true, ...Object.fromEntries(fields.map(key => [key, true])) })),
    graphs, native_graphs: native, graph_generations: { raw: 2, raw_edited: 2, scene: 4 }, evidence: { raw_source: raw, scene_source: scene } } };
}

test('exact three source-bound graph operands retain every raw field and independent epoch', () => {
  const value = report(), before = structuredClone(value), cases = authoringSemanticCases(value);
  assert.deepEqual(cases.map(item => [item.id, item.profile, item.path]), [
    ['authoring-raw', 'graph-snapshot', 'ui_authoring.raw'], ['authoring-raw-edited', 'graph-snapshot', 'ui_authoring.raw_edited'],
    ['authoring-scene', 'graph-snapshot', 'ui_authoring.scene'],
  ]);
  assert.deepEqual(Object.keys(SOURCE_AUTHORING_CASES), ['empty-entry-controls', 'invalid-and-limit-picker-preservation', 'visible-import-cancel-retry',
    'raw-file-source-inspection', 'raw-pointer-plane-authoring', 'raw-fit-edit', 'imported-source-keyboard-menu', 'generated-pointer-plane-cylinder',
    'popup-dependent-work-guards', 'docked-dependent-work-guards', 'accepted-dependent-removal', 'explicit-example-roundtrip']);
  for (const item of cases) assert.deepEqual(item.actual, item.expected);
  assert.deepEqual(value, before);
});

for (const name of Object.keys(SOURCE_AUTHORING_CASES)) test('case requires its own literal true oracle: ' + name, () => {
  for (const invalid of [undefined, false, 1]) {
    const value = report(), item = value.ui_authoring.cases.find(item => item.name === name);
    const field = SOURCE_AUTHORING_CASES[name][0];
    if (invalid === undefined) delete item[field]; else item[field] = invalid;
    assert.throws(() => authoringSemanticCases(value));
  }
});
for (const [name, mutate] of [
  ['schema', a => { a.schema = 'unknown'; }], ['completion', a => { a.complete = 1; }],
  ['missing case', a => { a.cases.pop(); }], ['duplicate case', a => { a.cases.push(a.cases[0]); }], ['reordered cases', a => { a.cases.reverse(); }],
  ['unknown case', a => { a.cases[0].name = 'unknown'; }], ['unknown oracle', a => { a.cases[0].uncompared = true; }],
  ['missing graph', a => { delete a.graphs.raw; }], ['extra native', a => { a.native_graphs.extra = graph(a.evidence.raw_source); }],
  ['epoch boolean', a => { a.graph_generations.raw = true; }], ['epoch unsafe', a => { a.graph_generations.scene = 2 ** 53; }],
  ['edited source epoch', a => { a.graph_generations.raw_edited = 3; }], ['graph epoch', a => { a.graphs.raw.project_generation = 1; }],
  ['running', a => { a.graphs.scene.evaluation_running = true; }], ['failed closure', a => { a.graphs.raw.required_failures = ['fit']; }],
  ['native closure', a => { a.native_graphs.raw.required_failures = ['fit']; }], ['native running', a => { a.native_graphs.raw.evaluation_running = true; }],
  ['failed state', a => { a.graphs.raw.states.fit = 'failed'; }], ['errors', a => { a.graphs.raw.errors.fit = 'failed'; }],
  ['missing field', a => { delete a.native_graphs.scene.diagnostics; }], ['unknown field', a => { a.graphs.scene.new_classification = 'rejected'; }],
  ['source digest', a => { a.graphs.raw.recipe.nodes[0].source_sha256 = 'd'.repeat(64); }],
  ['descriptor binding', a => { a.graphs.raw.recipe.nodes[0].reference_sha256 = 'd'.repeat(64); }],
  ['native descriptor', a => { a.native_graphs.raw.recipe.nodes[0].descriptor.source_bytes++; }],
  ['wrong evidence', a => { a.evidence.raw_source = a.evidence.scene_source; }],
  ['source count', a => { a.evidence.raw_source.vertices++; }], ['source bytes', a => { a.evidence.raw_source.original_bytes++; }],
  ['source feature ID', a => { a.graphs.raw.recipe.nodes[0].id = 'other-source'; }],
  ['staging bytes', a => { a.evidence.raw_source.owned_fs_bytes = 1; }], ['zero normals', a => { a.evidence.raw_source.normals[0] = [0, 0, 0]; }],
  ['nonfinite rows', a => { a.evidence.raw_source.positions[0][0] = Infinity; }],
  ['bad selection ids', a => { a.graphs.raw.recipe.nodes[1].ids.push(3); }],
]) test('fail closed for ' + name, () => { const value = report(); mutate(value.ui_authoring); assert.throws(() => authoringSemanticCases(value)); });

for (const key of ['initial_parameters', 'initialization', 'seed', 'fit_geometry', 'solver', 'residuals', 'weighted_rms'])
  test('computed starts cannot be embedded in recipe: ' + key, () => {
    const value = report(); value.ui_authoring.graphs.raw.recipe.nodes[2][key] = [0]; assert.throws(() => authoringSemanticCases(value));
  });

async function compare(value) {
  const root = await mkdtemp(path.join(process.env.SCANSOR_TEST_TMPDIR || tmpdir(), 'authoring-semantic-'));
  try {
    await mkdir(path.join(root, 'runtime')); await mkdir(path.join(root, 'reports'));
    const helper = await readFile(new URL('../browser_runtime/semantic_parity.py', import.meta.url));
    await writeFile(path.join(root, 'runtime/manifest.json'), JSON.stringify({ source_files: [
      { archive_path: 'experiments/browser_runtime/semantic_parity.py', sha256: sha256(helper) },
    ] }));
    return await compareSemanticBatch({ cases: authoringSemanticCases(value), nativePython: process.env.SCANSOR_NATIVE_PYTHON || 'python3',
      site: { root, manifest: { runtime: { path: 'runtime' } } }, output: path.join(root, 'reports'), report: {} });
  } finally { await rm(root, { recursive: true, force: true }); }
}

test('actual shared policy protects geometry and native graph operands after projection', async () => {
  const baseline = report(); assert.equal((await compare(baseline)).semantic_complete, true);
  for (const target of ['graphs', 'native_graphs']) {
    const changed = report(); changed.ui_authoring[target].raw.results.fit.parameters[3] = 0.01;
    await assert.rejects(compare(changed), /Semantic comparison failed/);
  }
});

for (const field of ['input_contracts', 'relationship_contracts'])
  test('actual shared policy protects published contracts on both sides: ' + field, async () => {
    for (const target of ['graphs', 'native_graphs']) {
      const changed = report(); changed.ui_authoring[target].raw[field] = {};
      await assert.rejects(compare(changed), /Semantic comparison failed/);
    }
  });

function boundedCertificate() {
  // Compact retained constrained-success schema:2 observations+3 equalities+1 certificate.
  const policy = { version: 'selected-data-recovery-v2', max_proposals: 16, max_fresh_attempts: 8,
    attempt: { max_iterations: 100, max_trials: 16, max_evaluations: 512 }, objective_atol: 1e-12, objective_rtol: 1e-9 };
  const candidate = { id: 'selected-joint-0', chart: 'workspace-joint-state-v1', parameters: [0, 0, 0, 0, 0, 1], derivation: 'selected algebraic observations', binding_sha256: 'd'.repeat(64) };
  return { weighted_rms: 0, objective_history: [1e-4, 0],
    solver: { termination: 'constrained-stationarity', iterations: 1, evaluations: 2, constraint_evaluations: 3,
      rank: 12, constraint_rank: 13, tangent_dimension: 12, condition: 2, objective_history: [1e-4, 0],
      coordinate_origin: [0, 0, 0], coordinate_length: 1, constraint_violation: 0, projected_gradient_norm: 0 },
    initialization: { algorithm: 'selected-symmetry-group-recovery-v1', recovery: { policy, binding_sha256: candidate.binding_sha256,
      attempts: [{ candidate, role: 'fresh', outcome: 'locally-converged', evaluations: 6, assessment: { objective: 0, valid: true,
        rank: 12, condition: 2, termination: 'constrained-stationarity', iterations: 1 }, solver: null }],
      selected: candidate.id, selected_role: 'fresh', termination: 'best-attempted-local-solution' } } };
}
test('actual shared policy rejects both-side overbudget candidate records', async () => {
  const value = report();
  for (const target of ['graphs', 'native_graphs']) value.ui_authoring[target].scene.result = boundedCertificate();
  assert.equal((await compare(value)).budgets_validated, true);
  for (const target of ['graphs', 'native_graphs'])
    value.ui_authoring[target].scene.result.initialization.recovery.attempts[0].evaluations = 513;
  await assert.rejects(compare(value), /Semantic comparison failed/);
});
