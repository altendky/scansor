// Literal user workflow shared by native Playwright and the shipped CAD app.
// The appended observer only reads production state and real pointer events.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';

export const RAW_WORKFLOW_SCHEMA = 'scansor-raw-source-workflow-v1';
export const RAW_SOURCE_SHA256 = '537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358';
export const rawAppObserverSuffix = `
{
 const delivered=[];let observing=false;
 globalThis.__rawWorkflow296={
  snapshot:({arrays=false}={})=>({generation:workspaceGeneration,metadata,graph:graphState,
   ready:$('status').dataset.runtimeReady==='true'||(!backend.capabilities.recovery&&!!mesh&&!!sourceProject),inert:document.querySelector('main').inert,
   transition_busy:sourceTransitionBusy,evaluation_running:!!activeGraphEvaluation,
   selection_pending:selectionPending,selection_drawing:selectionDrawing,authoring_applying:authoringApplying,
   ...(arrays?{positions:[...positions],indices:[...mesh.geometry.index.array]}:{})}),
  projection:()=>{
   if(!observing){for(const type of ['pointerdown','pointerup']) renderer.domElement.addEventListener(type,event=>{
    delivered.push({type,x:event.clientX,y:event.clientY,button:event.button,pointerId:event.pointerId,time:event.timeStamp});
   },{passive:true});observing=true;}
   const matrix=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse).multiply(mesh.matrixWorld);
   const box=renderer.domElement.getBoundingClientRect();
   return {matrix:[...matrix.elements],box:{x:box.x,y:box.y,width:box.width,height:box.height},
    width:viewport.clientWidth,height:viewport.clientHeight,
    source_center:mesh.geometry.boundingSphere.center.toArray(),delivered:[...delivered]};
  }
 };
}
`;

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const copy = value => structuredClone(value);
const graph = value => value.graph;
const unlocked = value => value?.ready && !value.inert && !value.transition_busy && !value.evaluation_running
  && !value.selection_pending && !value.selection_drawing && !value.authoring_applying;
async function until(page, read, check, label) {
  const deadline = Date.now() + 180000;
  let value;
  while (Date.now() < deadline) {
    value = await read();
    if (check(value)) return value;
    await page.waitForTimeout(50);
  }
  throw Error(`${label}: ${JSON.stringify(value)}`);
}
async function command(page, name) {
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}
function terminal(value) {
  const state = graph(value);
  assert(unlocked(value), 'actual browser controls released');
  assert.equal(state.project_generation, value.generation);
  assert.deepEqual(state.errors, {});
  assert(Array.isArray(state.required_failures), 'full actual terminal closure retained');
  assert.deepEqual(state.required_failures, []);
  assert.equal(state.evaluation_running, false);
  assert(Object.values(state.states).every(item => item === 'ready'), 'all original graph factors ready');
}
function emptyProject(value) {
  assert(value.metadata.source === null || value.metadata.source === undefined, 'empty source');
  assert.deepEqual(graph(value).recipe.nodes, []);
  assert.equal(value.metadata.vertices, 0);
  assert.equal(value.metadata.triangles, 0);
}
function seedFree(recipe) {
  const forbidden = new Set(['initial_parameters', 'initialization', 'seed', 'fit_geometry', 'solver', 'residuals', 'weighted_rms']);
  const walk = value => {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') for (const [key, item] of Object.entries(value)) {
      assert(!forbidden.has(key), `Computed field in authored actions: ${key}`);
      walk(item);
    }
  };
  walk(recipe);
}
function projected(matrix, point, view) {
  const vector = [...point, 1], clip = Array.from({ length: 4 }, (_, row) =>
    vector.reduce((sum, item, column) => sum + matrix[column * 4 + row] * item, 0));
  return [view.box.x + (clip[0] / clip[3] + 1) * view.width / 2,
    view.box.y + (1 - clip[1] / clip[3]) * view.height / 2, clip[2] / clip[3], clip[3]];
}
function rectangleIds(positions, view, endpoints) {
  const x = endpoints.map(point => point.x), y = endpoints.map(point => point.y), ids = [];
  for (let id = 0; id < positions.length / 3; id++) {
    const p = projected(view.matrix, positions.slice(3 * id, 3 * id + 3), view);
    if (p[3] > 0 && p[2] >= -1 && p[2] <= 1 && p[0] >= Math.min(...x) && p[0] <= Math.max(...x)
      && p[1] >= Math.min(...y) && p[1] <= Math.max(...y)) ids.push(id);
  }
  return ids;
}
async function download(page, output, action) {
  const waiting = page.waitForEvent('download');
  await action();
  const value = await waiting, filename = value.suggestedFilename(), destination = path.join(output, filename);
  assert.equal(path.basename(filename), filename, 'download filename has no path');
  assert.equal(await value.failure(), null, 'actual browser download succeeded');
  await value.saveAs(destination);
  const bytes = await readFile(destination);
  return { path: destination, name: filename, bytes: bytes.length, sha256: digest(bytes) };
}

/** snapshot/projection are read-only observations of this page's real app. */
export async function runRawSourceWorkflowUi({ page, rawPath, output, snapshot, projection, report = {}, checkpoint = async () => {} }) {
  await mkdir(output, { recursive: true });
  const raw = await readFile(rawPath);
  assert.equal(digest(raw), RAW_SOURCE_SHA256, 'original raw source bytes');
  Object.assign(report, { schema: RAW_WORKFLOW_SCHEMA, complete: false, phase: 'startup', graphs: {}, graph_generations: {},
    checkpoints: {}, downloads: {}, raw_source: { path: rawPath, bytes: raw.length, sha256: digest(raw) },
    instrumentation: { app_suffix_sha256: digest(Buffer.from(rawAppObserverSuffix)),
      description: 'Real shipped app/backend and DOM File/pointer/export/actions controls; appended passive state, camera and actual pointer-event observers. No recipe, mask, fit seed or operation injection.' } });
  const retain = async (name, value) => { report.checkpoints[name] = copy(value); await checkpoint(report); };
  const ready = () => until(page, snapshot, unlocked, 'actual UI terminal source boundary');
  const visible = () => until(page, () => page.getByRole('button', { name: 'Evaluate all', exact: true }).isEnabled(), Boolean, 'visible Evaluate all enabled');
  const accepted = async before => {
    const value = await until(page, snapshot, value => unlocked(value) && value.generation === before + 1, 'accepted source epoch');
    assert.equal(value.metadata.project_generation, value.generation);
    assert.equal(graph(value).project_generation, value.generation);
    return value;
  };
  const dialogs = [];
  const accept = async dialog => { dialogs.push({ type: dialog.type(), message: dialog.message() }); assert.equal(dialog.type(), 'confirm'); await dialog.accept(); };
  page.on('dialog', accept);
  try {
    let value = await ready();
    await retain('startup', value);
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    // Native starts empty; a shipped worker may initially show its example.
    // Legacy examples omit metadata.source, so actual authored membership is
    // the boundary detector, rather than truthiness of an imported descriptor.
    if (graph(value).recipe.nodes.length) { const before = value.generation; await command(page, 'New project'); value = await accepted(before); }
    emptyProject(value);
    await retain('preimport', value);
    const beforeImport = value.generation, picker = page.waitForEvent('filechooser');
    await page.locator('[data-add-scan]').click();
    const chooser = await picker;
    assert.equal(await chooser.element().getAttribute('id'), 'scan-file');
    await chooser.setFiles(rawPath);
    value = await accepted(beforeImport);
    assert.equal(value.metadata.source.source_sha256, RAW_SOURCE_SHA256);
    value = await snapshot({ arrays: true });
    assert.equal(value.positions.length, 24999 * 3);
    assert.equal(value.indices.length, 49994 * 3);
    await retain('imported', value);

    report.phase = 'pointer-selection';
    await page.getByRole('button', { name: 'Selection', exact: true }).click();
    await page.locator('#selection-tools').waitFor({ state: 'visible' });
    if (await page.locator('#dock-edit').isVisible()) await page.locator('#dock-edit').click();
    await page.locator('#selection-shape').selectOption('rectangle');
    await page.locator('#selection-depth').selectOption('through_all');
    await page.locator('#tool').selectOption('replace');
    await page.locator('#side').click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const view = await projection();
    assert(view.box.width / view.box.height >= 1, 'preregistered side-view aspect');
    const band = [27.8, 29.2], ys = band.map(z => projected(view.matrix, [view.source_center[0], view.source_center[1], z], view)[1]);
    const left = view.box.x + 8, right = view.box.x + view.box.width - 8;
    const alternatives = [[{ x: left, y: ys[0] }, { x: right, y: ys[1] }], [{ x: right, y: ys[0] }, { x: left, y: ys[1] }],
      [{ x: left, y: ys[1] }, { x: right, y: ys[0] }], [{ x: right, y: ys[1] }, { x: left, y: ys[0] }]];
    let planned;
    for (const endpoints of alternatives) if (await page.evaluate(points => points.every(point =>
      document.elementFromPoint(point.x, point.y) === document.querySelector('#viewport canvas')), endpoints)) { planned = endpoints; break; }
    assert(planned, 'frozen rectangle corners reachable through actual canvas');
    report.gesture = { frozen_z_band: band, planned, matrix: view.matrix, box: view.box, width: view.width, height: view.height,
      source_center: view.source_center, prior_pointer_events: view.delivered.length };
    await checkpoint(report);
    await page.mouse.move(planned[0].x, planned[0].y); await page.mouse.down();
    await page.mouse.move(planned[1].x, planned[1].y, { steps: 10 }); await page.mouse.up();
    const delivered = (await projection()).delivered.slice(view.delivered.length);
    assert.equal(delivered.length, 2); assert.equal(delivered[0].type, 'pointerdown'); assert.equal(delivered[1].type, 'pointerup');
    report.gesture.delivered = delivered;
    const expectedIds = rectangleIds(value.positions, view, delivered);
    assert(expectedIds.length > 0 && expectedIds.length < 24999, 'independently projected partial original-source selection');
    report.gesture.expected_ids = expectedIds;
    value = await until(page, snapshot, value => unlocked(value) && graph(value).recipe.nodes.some(node => node.operation === 'selection' && node.ids.length), 'pointer membership committed');
    const selections = graph(value).recipe.nodes.filter(node => node.operation === 'selection');
    assert.equal(selections.length, 1);
    const selection = selections[0];
    assert.deepEqual(selection.ids, expectedIds, 'actual pointer IDs equal independent original-row projection');
    report.selection_id = selection.id;
    await checkpoint(report);

    report.phase = 'fit';
    await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
    await page.locator('#new-fit-label').fill('Raw pointer cylinder');
    await page.locator('#new-fit-kind').selectOption('cylinder');
    await page.locator('#new-fit-inputs').selectOption(selection.id);
    await page.locator('#add-fit-form').getByRole('button', { name: 'Add fit', exact: true }).click();
    value = await until(page, snapshot, value => graph(value).recipe.nodes.some(node => node.label === 'Raw pointer cylinder'), 'authored cylinder');
    const fit = graph(value).recipe.nodes.find(node => node.label === 'Raw pointer cylinder');
    report.fit_id = fit.id;
    assert.deepEqual(fit.selections, [selection.id]); seedFree(graph(value).recipe);
    const evaluate = async () => {
      await visible(); await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
      const evaluated = await until(page, snapshot, value => unlocked(value)
        && ['ready', 'failed', 'blocked'].includes(graph(value).states[fit.id]), 'terminal raw cylinder evaluation');
      await visible(); terminal(evaluated);
      assert.deepEqual(graph(evaluated).results[fit.id].ids, expectedIds);
      assert.equal(graph(evaluated).results[fit.id].residuals.length, expectedIds.length);
      return evaluated;
    };
    value = await evaluate();
    report.graphs.original = copy(graph(value)); report.graph_generations.original = value.generation;
    await retain('original', value);

    report.phase = 'cad-download';
    await page.getByRole('button', { name: 'Export CAD…', exact: true }).click();
    await page.locator('#export-scope').selectOption('target');
    await page.locator('#export-target').selectOption(fit.id);
    await page.locator('#export-units').selectOption('Millimeters');
    // With no authored transform, the real form already selects Original scan
    // coordinates. Its geometry picker has no eligible nonempty alternatives.
    assert.equal(await page.locator('#export-transform').inputValue(), '');
    await page.locator('#export-axis-up').uncheck(); await page.locator('#export-mesh').check();
    report.downloads.cad = await download(page, output, () => page.locator('#export-download').click());
    assert.equal(report.downloads.cad.name, 'scansor-cad.zip');
    await checkpoint(report);

    report.phase = 'actions-download';
    report.downloads.actions = await download(page, output, () => page.getByRole('button', { name: 'Save actions', exact: true }).click());
    report.saved_recipe = JSON.parse(await readFile(report.downloads.actions.path, 'utf8'));
    assert.deepEqual(report.saved_recipe, report.graphs.original.recipe); seedFree(report.saved_recipe);
    await checkpoint(report);
    const originalGeneration = value.generation;
    await command(page, 'New project'); value = await accepted(originalGeneration);
    emptyProject(value);
    await retain('empty', value);

    report.phase = 'renamed-locate-open';
    const beforeOpen = value.generation, actionsPicker = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Load actions', exact: true }).click();
    const actionsChooser = await actionsPicker;
    assert.equal(await actionsChooser.element().getAttribute('id'), 'file');
    await actionsChooser.setFiles(report.downloads.actions.path);
    await page.locator('#locate-source-dialog').waitFor({ state: 'visible' });
    const locate = page.waitForEvent('filechooser'); await page.locator('#choose-located-source').click();
    const located = await locate;
    assert.equal(await located.element().getAttribute('id'), 'locate-source-file');
    await located.setFiles({ name: 'renamed-original-raw.ply', mimeType: 'application/octet-stream', buffer: raw });
    value = await accepted(beforeOpen);
    assert.equal(value.metadata.source.source_sha256, RAW_SOURCE_SHA256);
    assert.equal(value.metadata.source.filename, 'renamed-original-raw.ply');
    assert.deepEqual(graph(value).recipe, report.saved_recipe);
    assert.equal(graph(value).token, report.graphs.original.token);
    assert.equal(graph(value).states[fit.id], 'unevaluated'); assert.equal(graph(value).results[fit.id], undefined);
    await retain('reopened', value);
    report.phase = 'replay-evaluate'; value = await evaluate();
    report.graphs.replayed = copy(graph(value)); report.graph_generations.replayed = value.generation;
    assert.deepEqual(graph(value).recipe, report.saved_recipe);
    assert.equal(graph(value).token, report.graphs.original.token);
    await retain('replayed', value);
    report.dialogs = dialogs; report.phase = 'complete'; report.complete = true; await checkpoint(report);
    return report;
  } catch (error) {
    report.failure = String(error); report.complete = false;
    report.failure_snapshot = await snapshot().catch(failure => ({ observation_error: String(failure) }));
    await checkpoint(report); throw error;
  } finally {
    page.off('dialog', accept);
    report.cleanup = { dialog_listener_removed: true, page_backend_owner: 'caller', downloaded_artifacts: 'retained for independent validation' };
    await checkpoint(report);
  }
}
