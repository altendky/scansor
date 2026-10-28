// Actual native/shipped-worker controls; appended observers only read app state.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {sha256} from './server.mjs';
import {installMainObservers} from './worker-source-resource-observers.mjs';

export {installMainObservers};
export const virtualScanUiObserverSuffix = `
globalThis.__virtualScanUi357=()=>{const info=renderer?.info;return ({generation:workspaceGeneration,metadata,graph:graphState,
 positions:positions?[...positions]:[],mesh_positions:mesh?.geometry?.getAttribute('position')?[...mesh.geometry.getAttribute('position').array]:[],
 ready:!!(info&&metadata&&graphState)&&(backend.capabilities.recovery?$('status').dataset.runtimeReady==='true':!$('load').disabled),
 inert:document.querySelector('main').inert,
 transition_busy:sourceTransitionBusy,evaluation_running:!!activeGraphEvaluation,
 selection_pending:selectionPending,selection_drawing:selectionDrawing,authoring_applying:authoringApplying,
 resources:{render:info?{geometries:info.memory.geometries,textures:info.memory.textures,
 calls:info.render.calls,triangles:info.render.triangles}:null,gpu:globalThis.__workerGpu295?.(),
 workers:globalThis.__workerTransport295?.workers,terminated:globalThis.__workerTransport295?.terminated.length}});};
`;

export function virtualScanFixture() {
  const count = 17, vertices = count * count, faces = 2 * (count - 1) ** 2;
  const header = Buffer.from('ply\nformat binary_little_endian 1.0\n'
    + `element vertex ${vertices}\nproperty float x\nproperty float y\nproperty float z\n`
    + 'property float nx\nproperty float ny\nproperty float nz\n'
    + `element face ${faces}\nproperty list uchar int vertex_indices\nend_header\n`);
  const body = Buffer.alloc(vertices * 24 + faces * 13);
  let offset = 0;
  for (let row = 0; row < count; row++) for (let col = 0; col < count; col++) {
    for (const value of [col / 16, row / 16, 0, 0, 0, 1]) { body.writeFloatLE(value, offset); offset += 4; }
  }
  for (let row = 0; row < count - 1; row++) for (let col = 0; col < count - 1; col++) {
    const a = row * count + col;
    for (const face of [[a, a + 1, a + count], [a + 1, a + count + 1, a + count]]) {
      body.writeUInt8(3, offset++);
      for (const id of face) { body.writeInt32LE(id, offset); offset += 4; }
    }
  }
  assert.equal(offset, body.length);
  return Buffer.concat([header, body]);
}

export const readVirtualScanUi = page => page.evaluate(() => globalThis.__virtualScanUi357?.());
const settled = state => state?.ready && !state.inert && !state.transition_busy && !state.evaluation_running
  && !state.selection_pending && !state.selection_drawing && !state.authoring_applying;
async function until(page, predicate, label) {
  const deadline = Date.now() + 180000;
  let state;
  while (Date.now() < deadline) {
    state = await readVirtualScanUi(page);
    if (predicate(state)) return state;
    await page.waitForTimeout(50);
  }
  throw Error(`Virtual scan UI timed out: ${label}; status=${await page.locator('#status').textContent()}; states=${JSON.stringify(state?.graph?.states)}`);
}
async function closeEditor(page) {
  if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
  else {
    const tab = page.getByRole('tab', {name: 'Edit', exact: true});
    if (await tab.count()) await tab.getByTitle('Close', {exact: true}).click();
  }
}
async function command(page, name) {
  await closeEditor(page);
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name, exact: true}).click();
}
async function choose(page, button, input, file) {
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), button.click()]);
  assert.equal(await chooser.element().getAttribute('id'), input);
  await chooser.setFiles(file);
}
async function evaluate(page, fit, count) {
  await closeEditor(page);
  await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
  const state = await until(page, value => settled(value) && value.graph.states[fit] === 'ready', 'fresh plane fit');
  const result = state.graph.results[fit];
  assert.equal(result.ids.length, count);
  assert.equal(result.residuals.length, count);
  assert(result.residuals.every(Number.isFinite));
  assert(result.weighted_rms < 1e-12, 'Generated planar fixture should fit its own plane');
  assert(Math.abs(result.parameters[2] - 1) < 1e-12);
  assert.deepEqual(state.graph.errors, {});
  return state;
}

export async function exerciseVirtualScanUi({page, force = false, sampleHeap = async () => null,
  sourceFile, onOriginal = async () => {}}) {
  const result = {schema: 'scansor-virtual-scan-ui-v1', complete: false, phase: 'startup', timings: [],
    resources: [], reopens: [], force_recoveries: [], cases: [],
    instrumentation: {app_suffix_sha256: sha256(Buffer.from(virtualScanUiObserverSuffix)),
      main_observer_sha256: sha256(Buffer.from(installMainObservers.toString())),
      description: 'Read-only app state observer and existing forwarded Worker/WebGL accounting observers; every semantic change uses actual DOM controls.'},
    resource_scope: 'Main CDP heap/backing and logical WebGL buffer observations overlap and must not be summed; no whole-browser or physical GPU memory claim.'};
  const raw = sourceFile?.buffer || virtualScanFixture();
  const file = sourceFile || {name: 'virtual-scan-original.ply', mimeType: 'application/octet-stream', buffer: raw};
  try {
    const initial = await until(page, settled, 'initial UI ready');
    if (initial.graph.recipe.nodes.length) {
      await command(page, 'New project');
      await until(page, state => settled(state) && state.generation === initial.generation + 1
        && state.graph.recipe.nodes.length === 0, 'empty project before original import');
    }
    result.phase = 'original-import';
    await choose(page, page.locator('[data-add-scan]'), 'scan-file', file);
    const original = await until(page, state => settled(state) && state.metadata?.vertices === 289, 'original File accepted');
    assert.deepEqual(original.positions, original.mesh_positions);
    const originalBinding = original.metadata.virtual_scans.active_binding;
    result.original = {binding: originalBinding, vertices: original.metadata.vertices, triangles: original.metadata.triangles, bytes_sha256: sha256(raw)};
    await onOriginal(original);
    result.phase = 'reduced-scan-creation';
    await page.locator('#create-virtual-scan').click();
    await page.locator('#virtual-scan-dialog').waitFor({state: 'visible'});
    await page.locator('#virtual-scan-divisions').fill('4');
    const creation = performance.now();
    await page.locator('#confirm-create-virtual-scan').click();
    const coarse = await until(page, state => settled(state) && state.generation === original.generation + 1
      && state.metadata.virtual_scans.virtual, 'reduced scan created');
    result.timings.push({operation: 'create', wall_ms: performance.now() - creation});
    const reducedBinding = coarse.metadata.virtual_scans.active_binding, reducedCount = coarse.metadata.vertices;
    assert.equal(reducedBinding, coarse.metadata.virtual_scans.virtual.binding);
    assert.equal(reducedCount, 25);
    assert.deepEqual(coarse.positions, coarse.mesh_positions);
    result.reduced = {binding: reducedBinding, vertices: reducedCount, triangles: coarse.metadata.triangles};
    result.phase = 'coarse-pointer-selection';
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    await page.getByRole('button', {name: 'Selection', exact: true}).click();
    await page.locator('#selection-tools').waitFor({state: 'visible'});
    await page.locator('#selection-shape').selectOption('rectangle');
    await page.locator('#selection-depth').selectOption('through_all');
    await page.locator('#tool').selectOption('replace');
    await page.locator('#top').click();
    const canvas = await page.locator('#viewport canvas').boundingBox();
    assert(canvas);
    await page.mouse.move(canvas.x + 8, canvas.y + 8);
    await page.mouse.down();
    await page.mouse.move(canvas.x + canvas.width - 8, canvas.y + canvas.height - 8, {steps: 10});
    await page.mouse.up();
    const painted = await until(page, state => settled(state) && state.graph.recipe.nodes.some(node => node.operation === 'selection'
      && state.graph.memberships[node.id]?.length === reducedCount), 'coarse pointer membership');
    const selection = painted.graph.recipe.nodes.find(node => node.operation === 'selection');
    assert.deepEqual(painted.graph.memberships[selection.id], Array.from({length: reducedCount}, (_, id) => id));
    await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
    await page.locator('#new-fit-label').fill('Virtual scan plane');
    await page.locator('#new-fit-kind').selectOption('plane');
    await page.locator('#new-fit-inputs').selectOption(selection.id);
    await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
    const fitted = await until(page, state => settled(state) && state.graph.recipe.nodes.some(node => node.label === 'Virtual scan plane'), 'coarse fit authored');
    const fit = fitted.graph.recipe.nodes.find(node => node.label === 'Virtual scan plane');
    assert.equal(fit.initial_parameters, undefined);
    await evaluate(page, fit.id, reducedCount);
    result.phase = 'coarse-volume-capture';
    await page.locator(`[data-action-id="${selection.id}"]`).dispatchEvent('dblclick');
    const editor = page.locator('#selection-owned-editor');
    await editor.getByRole('button', {name: 'Create volume…', exact: true}).click();
    await page.locator('#owned-volume-label').fill('Coarse scan footprint');
    await page.locator('#owned-volume-kind').selectOption('mesh');
    await editor.getByRole('button', {name: 'Capture volume', exact: true}).click();
    const captured = await until(page, state => settled(state) && state.graph.recipe.nodes.find(node => node.id === selection.id)
      .entries?.[0]?.volumes?.length === 1, 'coarse volume captured');
    const coarseEntry = captured.graph.recipe.nodes.find(node => node.id === selection.id).entries[0];
    const reference = {selection: selection.id, entry: coarseEntry.id, volume: coarseEntry.volumes[0].id};
    const activate = async binding => {
      await closeEditor(page);
      const before = await readVirtualScanUi(page);
      if (before.metadata.virtual_scans.active_binding === binding) return before;
      const started = performance.now();
      await page.locator('#active-scan').selectOption(binding);
      const after = await until(page, state => settled(state) && state.generation === before.generation + 1
        && state.metadata.virtual_scans.active_binding === binding, 'active scan switch');
      result.timings.push({operation: 'activate', binding, wall_ms: performance.now() - started});
      assert.equal(await page.locator('#active-scan').inputValue(), binding);
      assert.equal(await page.locator('#active-scan-summary').isVisible(), true);
      const summary = await page.locator('#active-scan-summary').textContent();
      assert(summary.includes(`${binding === originalBinding ? 289 : reducedCount} vertices`));
      assert(summary.includes('display and fitting'));
      assert.deepEqual(after.positions, after.mesh_positions);
      assert.deepEqual(after.positions, binding === originalBinding ? original.positions : coarse.positions);
      return after;
    };
    result.phase = 'full-scan-selection-transfer';
    const pending = await activate(originalBinding);
    assert.equal(pending.graph.states[fit.id], 'needs_input');
    assert.equal(pending.graph.results[fit.id], undefined);
    const fullSelection = pending.graph.recipe.nodes.find(node => node.id === selection.id);
    const targetEntry = fullSelection.entries.find(entry => entry.scan.reference_sha256 === originalBinding);
    assert(targetEntry);
    assert.equal(targetEntry.source_volume, null);
    await page.getByRole('button', {name: `Choose source volume for ${selection.label} · ${file.name}`, exact: true}).click();
    await page.locator('#owned-source-volume').selectOption(JSON.stringify(reference, Object.keys(reference).sort()));
    await editor.getByRole('button', {name: 'Apply source volume', exact: true}).click();
    await until(page, state => settled(state) && state.graph.recipe.nodes.find(node => node.id === selection.id)
      .entries.find(entry => entry.id === targetEntry.id).source_volume, 'explicit source volume chosen');
    const full = await evaluate(page, fit.id, 289);
    assert.deepEqual(full.graph.memberships[selection.id], Array.from({length: 289}, (_, id) => id));
    await page.locator(`#action-list .action-select[data-action-id="${fit.id}"]`).click({button: 'right'});
    await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Edited on full scan');
    await page.locator('#rename-feature-form').getByRole('button', {name: 'Rename', exact: true}).click();
    await until(page, state => settled(state) && state.graph.recipe.nodes.find(node => node.id === fit.id).label === 'Edited on full scan', 'current label edit');
    result.phase = 'return-to-coarse-preserves-current-edit';
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).check();
    const returned = await activate(reducedBinding);
    assert.equal(returned.graph.recipe.nodes.find(node => node.id === fit.id).label, 'Edited on full scan');
    assert.deepEqual(returned.graph.recipe.nodes.find(node => node.id === selection.id).entries[0], coarseEntry);
    const autoRefitted = await until(page, state => settled(state) && state.graph.states[fit.id] === 'ready'
      && state.graph.results[fit.id]?.ids.length === reducedCount, 'Auto refits configured reduced entry');
    assert.equal(autoRefitted.graph.recipe.nodes.find(node => node.id === fit.id).label, 'Edited on full scan');
    assert.equal(autoRefitted.graph.results[fit.id].residuals.length, reducedCount);
    assert(autoRefitted.graph.results[fit.id].weighted_rms < 1e-12);
    assert.deepEqual(autoRefitted.graph.errors, {});
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    result.cases.push({name: 'coarse-authoring-to-original', complete: true, real_pointer_selection: true,
      explicit_volume_transfer: true, full_rows: 289, coarse_rows: reducedCount, current_edit_preserved: true,
      auto_return_refitted: true});
    result.phase = 'repeated-switch-resources';
    for (let index = 0; index < 10; index++) {
      const state = await activate(index % 2 ? reducedBinding : originalBinding);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const shown = await readVirtualScanUi(page), heap = await sampleHeap();
      const gpu = shown.resources.gpu;
      assert(gpu.contexts.length > 0);
      assert(gpu.contexts.every(context => !context.context_lost && Number.isFinite(context.logical_live_buffer_bytes)));
      result.resources.push({index, binding: state.metadata.virtual_scans.active_binding, vertices: state.metadata.vertices,
        main_heap: heap, render: shown.resources.render, gpu, workers: shown.resources.workers});
    }
    assert(result.resources.at(-1).gpu.contexts.some(context => context.buffer_data_calls > 0));
    assert.equal(new Set(result.resources.map(row => row.workers)).size, 1, 'Ordinary switching must keep the same worker');
    result.phase = 'save-reopen-and-force';
    for (const binding of [originalBinding, reducedBinding]) {
      await activate(binding);
      const count = binding === originalBinding ? 289 : reducedCount;
      await evaluate(page, fit.id, count);
      const [download] = await Promise.all([page.waitForEvent('download'),
        page.getByRole('button', {name: 'Save actions', exact: true}).click()]);
      const bytes = await readFile(await download.path()), recipe = JSON.parse(bytes);
      const before = await readVirtualScanUi(page);
      await command(page, 'New project');
      const empty = await until(page, state => settled(state) && state.generation === before.generation + 1
        && state.graph.recipe.nodes.length === 0, 'new project before reopen');
      const pickActions = () => choose(page, page.getByRole('button', {name: 'Load actions', exact: true}), 'file',
        {name: 'virtual-scan-actions.json', mimeType: 'application/json', buffer: bytes});
      await pickActions();
      await page.locator('#locate-source-dialog').waitFor({state: 'visible'});
      if (binding === originalBinding) {
        await page.locator('#cancel-locate-source').click();
        assert.equal((await readVirtualScanUi(page)).generation, empty.generation);
        assert.deepEqual((await readVirtualScanUi(page)).graph.recipe, empty.graph.recipe);
        await pickActions();
        await page.locator('#locate-source-dialog').waitFor({state: 'visible'});
        const wrong = Buffer.from(raw), body = wrong.indexOf(Buffer.from('end_header\n')) + 11;
        wrong[body] ^= 1;
        await choose(page, page.locator('#choose-located-source'), 'locate-source-file', {...file, buffer: wrong});
        await page.waitForFunction(() => document.querySelector('#locate-source-dialog').open
          && document.querySelector('#locate-source-error').textContent.length > 0, null, {timeout: 180000});
        assert.equal((await readVirtualScanUi(page)).generation, empty.generation);
        assert.deepEqual((await readVirtualScanUi(page)).graph.recipe, empty.graph.recipe);
        result.cases.push({name: 'missing-and-wrong-original', complete: true, cancelled_locate_preserved_project: true, wrong_same_name_preserved_project: true});
      }
      await choose(page, page.locator('#choose-located-source'), 'locate-source-file', {...file, name: 'renamed-original.ply'});
      const reopened = await until(page, state => settled(state) && state.generation === empty.generation + 1
        && state.metadata.virtual_scans.active_binding === binding, 'actions reconstructed from renamed original');
      assert.deepEqual(reopened.graph.recipe, recipe);
      assert.equal(reopened.metadata.vertices, count);
      const refitted = await evaluate(page, fit.id, count);
      result.reopens.push({active_binding: binding, vertices: count, actions_sha256: sha256(bytes),
        original_filename: recipe.nodes[0].virtual_scan.original.filename, renamed_original_accepted: true,
        current_label: refitted.graph.recipe.nodes.find(node => node.id === fit.id).label,
        fit: {parameters: refitted.graph.results[fit.id].parameters, weighted_rms: refitted.graph.results[fit.id].weighted_rms,
          rows: refitted.graph.results[fit.id].ids.length}});
      if (force) {
        const workers = refitted.resources.workers;
        await page.locator('#force-restart').click();
        const recovered = await until(page, state => settled(state) && state.resources.workers > workers
          && state.generation === reopened.generation && state.metadata.virtual_scans.active_binding === binding, 'Force either active scan');
        assert.deepEqual(recovered.graph.recipe, recipe);
        assert.equal(recovered.graph.results[fit.id], undefined);
        const verified = await evaluate(page, fit.id, count);
        assert.equal(verified.graph.recipe.nodes.find(node => node.id === fit.id).label, 'Edited on full scan');
        result.force_recoveries.push({active_binding: binding, original_file_reconstructed: true, workers_before: workers,
          workers_after: verified.resources.workers, accepted_generation_preserved: true, rows: verified.graph.results[fit.id].ids.length});
      }
    }
    result.complete = true; result.phase = 'complete';
    return result;
  } catch (error) {
    result.failure = String(error);
    result.failure_snapshot = await readVirtualScanUi(page).catch(() => null);
    error.virtualScanReport = result;
    throw error;
  }
}

export async function validateVirtualScanUi({browser, site, preview, prefix, isolation, report}) {
  const context = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1440, height: 1000}});
  await context.addInitScript(installMainObservers);
  context.on('request', request => {
    const url = request.url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) report.unexpected_requests.push(url);
    if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
  });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== preview.origin) return route.abort();
    if (!url.pathname.endsWith('/app.js')) return route.continue();
    const relative = url.pathname.slice(prefix.length), record = site.records.get(relative);
    assert(record, 'Observed production app must be manifest-listed');
    const bytes = await readFile(path.join(site.root, relative));
    assert.equal(bytes.length, record.bytes); assert.equal(sha256(bytes), record.sha256);
    await route.fulfill({status: 200, contentType: 'text/javascript', body: Buffer.concat([bytes, Buffer.from(virtualScanUiObserverSuffix)]),
      headers: isolation ? {'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp'} : {}});
  });
  const page = await context.newPage(); page.setDefaultTimeout(60000);
  page.on('pageerror', error => report.page_errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  const cdp = await context.newCDPSession(page);
  try {
    await page.goto(preview.origin + prefix + 'app/');
    report.virtual_scans = await exerciseVirtualScanUi({page, force: true, sampleHeap: () => cdp.send('Runtime.getHeapUsage')});
    assert.equal(await page.evaluate(() => crossOriginIsolated), isolation);
  } catch (error) {
    report.virtual_scans = error.virtualScanReport || {complete: false, failure: String(error)};
    throw error;
  } finally { await cdp.detach(); await context.close(); }
}
