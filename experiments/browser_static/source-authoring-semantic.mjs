import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { projectSourceGraph } from './source-semantic.mjs';

export const SOURCE_AUTHORING_SCHEMA = 'scansor-source-ui-authoring-v1';
export const SOURCE_AUTHORING_CASES = Object.freeze({
  'empty-entry-controls': ['empty_controls_honest', 'camera_preserves_empty', 'narrow_entry_reachable'],
  'invalid-and-limit-picker-preservation': ['synthetic_picker_cancel_preserves_empty', 'advertised_limit_before_prepare', 'malformed_preserves_owner', 'staging_released'],
  'visible-import-cancel-retry': ['real_progress_cancel_reachable', 'preaccept_owner_preserved', 'late_candidate_inert', 'ordinary_retry_accepted'],
  'raw-file-source-inspection': ['source_only_without_starters', 'strict_original_rows', 'readonly_metadata', 'camera_preserves_source', 'stale_second_picker_rejected'],
  'raw-pointer-plane-authoring': ['actual_pointer_membership', 'ordinary_fit_dialog_and_evaluation', 'every_original_row_weight_accounted', 'seed_free_actions'],
  'raw-fit-edit': ['actual_property_apply_and_rename', 'original_selection_ids_preserved', 'ordinary_reevaluation', 'source_epoch_preserved'],
  'imported-source-keyboard-menu': ['source_tree_keyboard_inspection', 'menu_enter_escape_focus', 'narrow_rotated_docked_commands_reachable', 'no_unrequested_evaluation_or_source_change'],
  'generated-pointer-plane-cylinder': ['exact_independent_memberships', 'actual_fit_dialogs_and_evaluation', 'independent_geometry_objective_support', 'automatic_support_and_seed_free_actions'],
  'popup-dependent-work-guards': ['live_dom_and_host_preserved', 'declined_new_remove_example_preserve_dependents', 'example_cancel_escape_preserves_work', 'focus_reachable'],
  'docked-dependent-work-guards': ['live_dom_and_host_preserved', 'declined_new_remove_example_preserve_dependents', 'example_cancel_escape_preserves_work', 'focus_reachable'],
  'accepted-dependent-removal': ['old_work_and_draft_until_acceptance', 'dependents_removed_atomically', 'different_source_has_no_old_actions'],
  'explicit-example-roundtrip': ['empty_and_scan_catalogue_unselected', 'actual_chooser_accepts_example', 'retained_recipe_restore_preserved', 'return_to_user_source_honest'],
});
Object.values(SOURCE_AUTHORING_CASES).forEach(Object.freeze);
const names = ['raw', 'raw_edited', 'scene'];
const forbidden = new Set(['initial_parameters', 'initialization', 'seed', 'fit_geometry', 'solver', 'residuals', 'weighted_rms']);
const descriptorKeys = ['version', 'profile', 'source_sha256', 'source_bytes', 'interpretation_sha256', 'vertices', 'triangles', 'filename', 'units'];
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const epoch = value => Number.isSafeInteger(value) && value >= 0;
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function keys(value, expected) {
  assert.ok(object(value)); assert.deepEqual(Object.keys(value).sort(), [...expected].sort());
}
function seedFree(value) {
  if (Array.isArray(value)) value.forEach(seedFree);
  else if (object(value)) for (const [key, child] of Object.entries(value)) {
    assert.equal(forbidden.has(key), false, 'Computed state in authored recipe: ' + key); seedFree(child);
  }
}
function descriptorBinding(descriptor) {
  keys(descriptor, descriptorKeys);
  const semantic = Object.fromEntries(Object.keys(descriptor).filter(key => key !== 'filename').sort().map(key => [key, descriptor[key]]));
  return createHash('sha256').update(JSON.stringify(semantic)).digest('hex');
}
function sourceEvidence(evidence) {
  const d = evidence.descriptor;
  const binding = descriptorBinding(d);
  assert.equal(d.version, 'interactive-triangle-scan-v1');
  assert.equal(d.profile, 'binary-triangle-ply-v1'); assert.equal(d.units, 'unknown');
  assert.ok(digest(d.source_sha256) && digest(d.interpretation_sha256));
  assert.ok(typeof d.filename === 'string' && d.filename.length > 0 && d.filename.length <= 512);
  assert.ok(Number.isSafeInteger(d.source_bytes) && d.source_bytes > 0);
  assert.ok(Number.isSafeInteger(d.vertices) && d.vertices > 0);
  assert.ok(Number.isSafeInteger(d.triangles) && d.triangles >= 0);
  assert.equal(evidence.source_sha256, d.source_sha256);
  assert.equal(evidence.original_bytes, d.source_bytes);
  assert.equal(evidence.vertices, d.vertices); assert.equal(evidence.faces, d.triangles);
  assert.ok(epoch(evidence.project_generation)); assert.deepEqual(evidence.source_mode, { kind: 'scan' });
  assert.equal(evidence.owned_fs_bytes, 0); assert.deepEqual(evidence.owned_fs_files, []);
  for (const field of ['positions', 'normals']) {
    assert.ok(Array.isArray(evidence[field]) && evidence[field].length === d.vertices);
    for (const row of evidence[field]) assert.ok(Array.isArray(row) && row.length === 3 && row.every(Number.isFinite));
  }
  assert.ok(evidence.normals.every(row => Math.hypot(...row) > 0));
  assert.ok(Array.isArray(evidence.weights) && evidence.weights.length === d.vertices && evidence.weights.every(value => Number.isFinite(value) && value >= 0)
    && evidence.weights.some(value => value > 0));
  assert.ok(Array.isArray(evidence.triangles) && evidence.triangles.length === d.triangles);
  for (const row of evidence.triangles) assert.ok(Array.isArray(row) && row.length === 3 && row.every(value => Number.isSafeInteger(value) && value >= 0 && value < d.vertices));
  return binding;
}
function graphEvidence(graph, source, binding, generation) {
  projectSourceGraph(graph); seedFree(graph.recipe);
  assert.deepEqual(graph.required_failures, []);
  if (generation !== undefined) {
    assert.ok(epoch(generation)); assert.equal(graph.project_generation, generation);
    assert.equal(graph.evaluation_running, false);
  } else if (Object.hasOwn(graph, 'evaluation_running')) assert.equal(graph.evaluation_running, false);
  assert.ok(graph.evaluation_error === undefined || graph.evaluation_error === null);
  assert.deepEqual(graph.errors, {});
  assert.ok(object(graph.states) && Object.keys(graph.states).length > 0 && Object.values(graph.states).every(value => value === 'ready'));
  assert.ok(Array.isArray(graph.recipe.nodes));
  const sources = graph.recipe.nodes.filter(node => node.operation === 'source');
  assert.equal(sources.length, 1); const node = sources[0];
  assert.equal(node.id, 'scan');
  assert.equal(node.source_sha256, source.source_sha256); assert.equal(node.reference_sha256, binding);
  assert.deepEqual(node.descriptor, source.descriptor);
  for (const selection of graph.recipe.nodes.filter(value => value.operation === 'selection')) {
    assert.equal(selection.source, node.id);
    assert.ok(Array.isArray(selection.ids) && selection.ids.every(id => Number.isSafeInteger(id) && id >= 0 && id < source.vertices));
  }
}

export function authoringSemanticCases(report) {
  const a = report.ui_authoring;
  assert.ok(object(a)); assert.equal(a.schema, SOURCE_AUTHORING_SCHEMA); assert.equal(a.complete, true);
  assert.ok(Array.isArray(a.cases));
  assert.deepEqual(a.cases.map(value => value.name), Object.keys(SOURCE_AUTHORING_CASES));
  for (const item of a.cases) {
    keys(item, ['name', 'complete', ...SOURCE_AUTHORING_CASES[item.name]]);
    assert.equal(item.complete, true);
    for (const field of SOURCE_AUTHORING_CASES[item.name]) assert.equal(item[field], true, 'Missing authoring oracle: ' + field);
  }
  keys(a.graphs, names); keys(a.native_graphs, names); keys(a.graph_generations, names);
  assert.equal(a.graph_generations.raw_edited, a.graph_generations.raw);
  const raw = a.evidence.raw_source, scene = a.evidence.scene_source;
  const rawBinding = sourceEvidence(raw), sceneBinding = sourceEvidence(scene);
  for (const name of names) {
    const source = name === 'scene' ? scene : raw, binding = name === 'scene' ? sceneBinding : rawBinding;
    assert.equal(a.graph_generations[name], source.project_generation);
    graphEvidence(a.graphs[name], source, binding, a.graph_generations[name]);
    graphEvidence(a.native_graphs[name], source, binding);
  }
  return names.map(name => ({ id: 'authoring-' + name.replace('_', '-'), profile: 'graph-snapshot', path: 'ui_authoring.' + name,
    actual: projectSourceGraph(a.graphs[name]), expected: projectSourceGraph(a.native_graphs[name]) }));
}
