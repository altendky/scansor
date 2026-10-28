// Independent STL fixture bytes; all app mutations use real product controls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sha256} from './server.mjs';
import {exerciseVirtualScanUi, readVirtualScanUi} from './virtual-scan-ui.mjs';

export function stlSourceFixture(encoding) {
  assert(['ascii', 'binary'].includes(encoding));
  const faces = [];
  for (let row = 0; row < 16; row++) for (let col = 0; col < 16; col++) {
    const a = [col / 16, row / 16, 0], b = [(col + 1) / 16, row / 16, 0];
    const c = [col / 16, (row + 1) / 16, 0], d = [(col + 1) / 16, (row + 1) / 16, 0];
    faces.push([a, b, c], [b, d, c]);
  }
  if (encoding === 'ascii') return Buffer.from('solid independent-grid\n' + faces.map(face =>
    'facet normal 0 0 -7\nouter loop\n' + face.map(point => 'vertex ' + point.join(' ') + '\n').join('')
      + 'endloop\nendfacet\n').join('') + 'endsolid independent-grid\n', 'ascii');
  const bytes = Buffer.alloc(84 + faces.length * 50);
  // An arbitrary binary header may look like a text PLY header.
  bytes.write('ply\nsolid independent binary STL', 'ascii');
  bytes.writeUInt32LE(faces.length, 80);
  let offset = 84;
  for (const face of faces) {
    for (const value of [0, 0, -7, ...face.flat()]) {bytes.writeFloatLE(value, offset); offset += 4;}
    bytes.writeUInt16LE(0, offset); offset += 2;
  }
  assert.equal(offset, bytes.length);
  return bytes;
}

const quiet = value => value?.ready && !value.inert && !value.transition_busy && !value.evaluation_running
  && !value.selection_pending && !value.selection_drawing && !value.authoring_applying;
async function until(page, predicate, phase) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const state = await readVirtualScanUi(page);
    if (predicate(state)) return state;
    await page.waitForTimeout(50);
  }
  throw Error(`STL UI timed out: ${phase}; ${await page.locator('#status').textContent()}`);
}
async function closeEditor(page) {
  if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
  else {
    const tab = page.getByRole('tab', {name: 'Edit', exact: true});
    if (await tab.count()) await tab.getByTitle('Close', {exact: true}).click();
  }
}
async function newProject(page) {
  await closeEditor(page);
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  return until(page, value => quiet(value) && value.graph.recipe.nodes.length === 0, 'empty project');
}
async function evaluate(page, fit, rows) {
  await closeEditor(page);
  await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
  const state = await until(page, value => quiet(value) && value.graph.states[fit] === 'ready', 'project plane refit');
  assert.deepEqual(state.graph.errors, {});
  assert.equal(state.graph.results[fit].ids.length, rows);
  assert.equal(state.graph.results[fit].residuals.length, rows);
  assert(state.graph.results[fit].weighted_rms < 1e-12);
  return state;
}

export async function exerciseStlSourceUi({page, encoding, force = false, sampleHeap = async () => null}) {
  const raw = stlSourceFixture(encoding), file = {name: `independent-${encoding}.stl`, mimeType: 'application/octet-stream', buffer: raw};
  const result = {schema: 'scansor-stl-source-ui-v1', encoding, complete: false, source_sha256: sha256(raw),
    source_bytes: raw.length, archives: [], force_recoveries: []};
  try {
    result.virtual_workflow = await exerciseVirtualScanUi({page, force, sampleHeap, sourceFile: file,
      onOriginal: async original => {
        assert.equal(original.metadata.source.profile, 'triangle-stl-f32-v1');
        assert.equal(original.metadata.normal_source, 'geometry-area-weighted-smoothed');
        assert.equal(original.metadata.capabilities.normals_required, false);
        assert(original.metadata.capabilities.source_import.formats.includes('triangle-stl-f32-v1'));
        const source = original.graph.recipe.nodes.find(node => node.operation === 'source');
        await page.locator(`[data-action-id="${source.id}"]`).dispatchEvent('dblclick');
        await page.locator('#source-properties').waitFor({state: 'visible'});
        const details = await page.locator('#source-details').textContent();
        assert(details.includes('triangle-stl-f32-v1'));
        assert(details.includes('smoothed at exact shared vertices'));
        assert(details.includes('stored STL facet normals are ignored for fitting'));
        result.visible_normal_provenance = true;
        await closeEditor(page);
      }});
    let state = await until(page, quiet, 'virtual workflow complete');
    const profile = state.metadata.virtual_scans;
    const fit = state.graph.recipe.nodes.find(node => node.operation === 'fit').id;
    for (const binding of [profile.original.binding, profile.virtual.binding]) {
      if (state.metadata.virtual_scans.active_binding !== binding) {
        const generation = state.generation;
        await page.locator('#active-scan').selectOption(binding);
        state = await until(page, value => quiet(value) && value.generation === generation + 1
          && value.metadata.virtual_scans.active_binding === binding, 'project endpoint');
      }
      const rows = binding === profile.original.binding ? 289 : 25;
      state = await evaluate(page, fit, rows);
      const recipe = structuredClone(state.graph.recipe);
      const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', {name: 'Save project', exact: true}).click()]);
      assert.equal(await download.failure(), null);
      const archive = await readFile(await download.path());
      assert(download.suggestedFilename().endsWith('.scansor'));
      await newProject(page);
      const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('[data-command-id="open-project"]:visible').click()]);
      assert.equal(await chooser.element().getAttribute('id'), 'project-file');
      await chooser.setFiles({name: `renamed-${encoding}.scansor`, mimeType: 'application/zip', buffer: archive});
      const reopened = await until(page, value => quiet(value) && value.metadata.virtual_scans?.active_binding === binding, 'self-contained STL project');
      assert.deepEqual(reopened.graph.recipe, recipe);
      assert.equal(reopened.metadata.vertices, rows);
      assert.equal(reopened.metadata.virtual_scans.original.descriptor.profile, 'triangle-stl-f32-v1');
      state = await evaluate(page, fit, rows);
      result.archives.push({active_binding: binding, bytes: archive.length, sha256: sha256(archive), rows,
        recipe_preserved: true, original_stl_retained: true});
      if (force) {
        const workers = state.resources.workers, generation = state.generation;
        await page.locator('#force-restart').click();
        state = await until(page, value => quiet(value) && value.resources.workers > workers
          && value.generation === generation && value.metadata.virtual_scans.active_binding === binding, 'archive-backed Force recovery');
        assert.deepEqual(state.graph.recipe, recipe);
        state = await evaluate(page, fit, rows);
        result.force_recoveries.push({active_binding: binding, rows, archive_backed: true, generation_preserved: true});
      }
    }
    result.complete = true;
    return result;
  } catch (error) {
    result.failure = String(error);
    result.failure_snapshot = await readVirtualScanUi(page).catch(() => null);
    error.stlSourceReport = result;
    throw error;
  }
}
