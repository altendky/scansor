import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { open, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { paintedSelectionMembership } from '../../browser_static/source-authoring-semantic.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const nozzle = path.join(repository, 'examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply');
const prepareFixtures = () => JSON.parse(execFileSync(python, ['-c', [
  'import base64,json',
  'from tests.native_scan_fixture import native_scene_fixture,native_grid_fixture',
  'def record(f): return dict(raw=base64.b64encode(f.raw).decode(), positions=f.positions.tolist(), triangles=f.triangles.tolist())',
  'print(json.dumps(dict(scene=record(native_scene_fixture()),grid=record(native_grid_fixture(4,3)),dense=record(native_grid_fixture(6,5)),multipage=record(native_grid_fixture(16,16)))))',
].join('\n')], { cwd: repository, encoding: 'utf8', env: { ...process.env, OPENBLAS_NUM_THREADS: '1' } }));
let fixture;

// Each worker owns a real empty native server, separate from the retained-example
// server used by the existing suite. Inputs stay in memory or use the retained PLY.
const test = base.extend({
  sourceOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: { ...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    const collect = data => { log = (log + data.toString()).slice(-16000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Empty native server did not start: ' + log)), 30000);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(Error(`Empty native server exited ${code}: ${log}`)); });
        child.stdout.on('data', data => {
          collect(data);
          const found = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if (found) { clearTimeout(timer); resolve(found[0]); }
        });
      });
      await use(origin);
    }
    finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close');
        const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.kill('SIGINT');
        try { await closed; } finally { clearTimeout(timer); }
      }
    }
  }, { scope: 'worker' }],
});

test.beforeAll(() => { fixture = prepareFixtures(); });

const metadata = async page => (await page.request.get(new URL('/api/meta', page.url()).href)).json();
const graph = async page => (await metadata(page)).graph;
const painted = (selection, state, meta) => paintedSelectionMembership(selection,
  state.recipe.nodes.find(node => node.operation === 'source'), meta.vertices,
  { nestedRequired: meta.selection_entries?.version === 1 });

async function command(page, name) {
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}

async function choose(page, value) {
  const waiting = page.waitForEvent('filechooser');
  await page.locator('[data-add-scan]').click();
  const chooser = await waiting;
  expect(await chooser.element().getAttribute('id')).toBe('scan-file');
  await chooser.setFiles(value);
}

const memoryFile = (name, data) => ({ name, mimeType: 'application/octet-stream', buffer: Buffer.from(data.raw, 'base64') });

async function imported(page, value) {
  await choose(page, value);
  await expect.poll(async () => (await metadata(page)).source?.filename).toBe(typeof value === 'string' ? path.basename(value) : value.name);
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect(page.locator('[data-add-scan]')).toBeHidden();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  return metadata(page);
}

function acceptDialogs(page) {
  const listener = dialog => dialog.accept();
  page.on('dialog', listener);
  return () => page.off('dialog', listener);
}

async function draft(page, { docked = false } = {}) {
  const editTab = page.getByRole('tab', { name: 'Edit', exact: true });
  if (!docked && await editTab.count()) await editTab.getByTitle('Close', { exact: true }).click();
  await page.getByRole('button', { name: 'Point', exact: true }).click();
  await page.locator('#new-point-label').fill('Keep this live draft');
  if (docked && await page.locator('#dock-edit').isVisible()) await page.locator('#dock-edit').click();
  await expect(page.locator(docked ? '#edit-host #new-point-label' : '#edit-popup #new-point-label')).toBeVisible();
  await page.locator('#new-point-label').focus();
  await page.locator('#new-point-label').evaluate(input => { window.sourceDraftInput = input; });
}

async function expectDraft(page) {
  await expect(page.locator('#point-dialog')).toBeVisible();
  await expect(page.locator('#new-point-label')).toHaveValue('Keep this live draft');
  expect(await page.locator('#new-point-label').evaluate(input => input === window.sourceDraftInput)).toBe(true);
}

async function rectanglePlane(page, count, label) {
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  await page.getByRole('button', { name: 'Selection', exact: true }).click();
  await expect(page.locator('#selection-tools')).toBeVisible();
  await page.locator('#selection-shape').selectOption('rectangle');
  await page.locator('#selection-depth').selectOption('through_all');
  await page.locator('#tool').selectOption('replace');
  await page.locator('#top').click();
  const canvas = await page.locator('#viewport canvas').boundingBox();
  // Preregistered full-source top-view rectangle, used unchanged for both the
  // 24,999-row raw source and the independent twelve-row grid. No saved IDs.
  await page.mouse.move(canvas.x + 8, canvas.y + 8);
  await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width - 8, canvas.y + canvas.height - 8, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => {
    const state = await graph(page), selection = state.recipe.nodes.find(node => node.operation === 'selection');
    return state.memberships[selection?.id]?.length;
  }).toBe(count);
  const selected = await metadata(page), selection = selected.graph.recipe.nodes.find(node => node.operation === 'selection');
  expect(painted(selection, selected.graph, selected).ids).toEqual(Array.from({ length: count }, (_, id) => id));
  await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
  await page.locator('#new-fit-label').fill(label);
  await page.locator('#new-fit-kind').selectOption('plane');
  await page.locator('#new-fit-inputs').selectOption(selection.id);
  await page.locator('#add-fit-form').getByRole('button', { name: 'Add fit', exact: true }).click();
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await expect.poll(async () => {
    const state = await graph(page), fit = state.recipe.nodes.find(node => node.label === label);
    return fit && state.states[fit.id];
  }).toBe('ready');
  // Backend publication can precede the browser's terminal graph adoption.
  await expect(page.getByRole('button', { name: 'Evaluate all', exact: true })).toBeEnabled();
  const state = await graph(page), fit = state.recipe.nodes.find(node => node.label === label);
  expect(fit.kind).toBe('plane');
  expect(fit.selections).toEqual([selection.id]);
  expect(fit.initial_parameters).toBeUndefined();
  expect(state.errors).toEqual({});
  expect(state.results[fit.id].residuals).toHaveLength(count);
  expect(state.results[fit.id].residuals.every(Number.isFinite)).toBe(true);
  expect(Number.isFinite(state.results[fit.id].weighted_rms)).toBe(true);
  return { selection, fit, state };
}

test.beforeEach(async ({ page, sourceOrigin }) => {
  await expect.poll(async () => (await (await page.request.get(new URL('/api/graph', sourceOrigin).href)).json()).evaluation_running,
    { timeout: 30000 }).toBe(false);
  const current = await (await page.request.get(new URL('/api/meta', sourceOrigin).href)).json();
  const reset = await page.request.post(new URL('/api/project/reset', sourceOrigin).href, {
    headers: { 'X-Scansor-Request': '1' },
    data: { generation: current.project_generation, token: current.graph.token, confirmedDiscard: true },
  });
  expect(reset.ok(), await reset.text()).toBe(true);
  await page.goto(sourceOrigin);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('[data-add-scan]')).toBeEnabled();
  await expect(page.locator('#startup-error')).toBeHidden();
});

test('Replace scan toolbar chooser preserves cancelled drafts and retains definitions with pending new entries', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await imported(page, memoryFile('original-grid.ply', fixture.grid));
  const authored = await rectanglePlane(page, fixture.grid.positions.length, 'Retained plane');
  await draft(page);
  const before = await metadata(page);
  const confirmations = [];
  const accept = dialog => { confirmations.push(dialog.message()); void dialog.accept(); };
  page.on('dialog', accept);
  try {
    const cancelledChooser = page.waitForEvent('filechooser');
    await command(page, 'Replace scan…');
    const cancelled = await cancelledChooser;
    expect(await cancelled.element().getAttribute('id')).toBe('replacement-file');
    await cancelled.setFiles([]);
    await page.locator('#replacement-file').dispatchEvent('cancel');
    await expectDraft(page);
    expect((await metadata(page)).graph.recipe).toEqual(before.graph.recipe);
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
    await expect(page.locator('#source-progress')).toBeHidden();

    const selectedChooser = page.waitForEvent('filechooser');
    await command(page, 'Replace scan…');
    await (await selectedChooser).setFiles(memoryFile('aligned-replacement.ply', fixture.scene));
    await expect.poll(async () => (await metadata(page)).source?.filename).toBe('aligned-replacement.ply');
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect(page.locator('#viewport canvas')).toBeVisible();
    const after = await metadata(page);
    expect(after.project_generation).toBe(before.project_generation + 1);
    expect(after.vertices).toBe(fixture.scene.positions.length);
    expect(after.graph.recipe.nodes.map(node => node.id)).toEqual(before.graph.recipe.nodes.map(node => node.id));
    expect(after.graph.recipe.nodes.filter(node => !['source', 'selection'].includes(node.operation)))
      .toEqual(before.graph.recipe.nodes.filter(node => !['source', 'selection'].includes(node.operation)));
    const originalSelection = before.graph.recipe.nodes.find(node => node.id === authored.selection.id);
    const retainedSelection = after.graph.recipe.nodes.find(node => node.id === authored.selection.id);
    if (originalSelection.entries) {
      expect(retainedSelection.entries.slice(0, originalSelection.entries.length)).toEqual(originalSelection.entries);
    } else {
      expect(retainedSelection.entries[0].ids).toEqual(originalSelection.ids);
      expect(retainedSelection.entries[0].depth).toBe(originalSelection.depth);
    }
    const target = retainedSelection.entries.at(-1);
    expect(target.mode).toBe('generated');
    expect(target.source_volume).toBeNull();
    expect(target.ids).toBeNull();
    expect(target.volumes).toEqual([]);
    expect(after.graph.states[retainedSelection.id]).toBe('needs_input');
    expect(after.graph.states[authored.fit.id]).toBe('needs_input');
    expect(after.graph.errors).toEqual({});
    expect(confirmations.some(message => /current source and committed actions/i.test(message))).toBe(false);
    expect(errors).toEqual([]);
  } finally { page.off('dialog', accept); }
});

test('owned volume preview automatically displays complete support across bounded pages', async ({ page }) => {
  const errors = [], pages = [];
  page.on('pageerror', error => errors.push(error.message));
  await imported(page, memoryFile('multipage-grid.ply', fixture.multipage));
  const authored = await rectanglePlane(page, fixture.multipage.positions.length, 'Multipage plane');
  await page.locator(`[data-action-id="${authored.selection.id}"]`).dispatchEvent('dblclick');
  const editor = page.locator('#selection-owned-editor');
  await editor.getByRole('button', { name: 'Create volume…', exact: true }).click();
  await editor.getByRole('button', { name: 'Capture volume', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === authored.selection.id).entries?.[0].volumes.length).toBe(1);
  const before = await graph(page), selection = before.recipe.nodes.find(node => node.id === authored.selection.id),
    entry = selection.entries[0], volume = entry.volumes[0];
  await page.route('**/api/graph/selection-volumes/preview', async route => {
    const response = await route.fetch(), value = await response.json();
    pages.push({ request: route.request().postDataJSON(), response: value });
    await route.fulfill({ response });
  });
  const row = page.locator(`[data-owned-selection-id="${selection.id}"][data-owned-entry-id="${entry.id}"][data-owned-volume-id="${volume.id}"]`);
  await row.dispatchEvent('keydown', { key: 'F2' });
  await expect(editor).toContainText(/All [\d,]+ support cells/);
  expect(pages.length).toBeGreaterThan(1);
  const total = pages[0].response.total_cells;
  expect(total).toBeGreaterThan(1024);
  let loaded = 0;
  for (const { request, response } of pages) {
    expect(request.offset).toBe(loaded); expect(request.limit).toBe(1024);
    expect(response.offset).toBe(loaded); expect(response.total_cells).toBe(total);
    expect(response.returned_cells).toBeLessThanOrEqual(1024);
    loaded += response.returned_cells;
    expect(response.next_offset).toBe(loaded < total ? loaded : null);
  }
  expect(loaded).toBe(total);
  await expect(editor).toContainText(`All ${total.toLocaleString('en-US')} support cells`);
  await expect(editor.getByRole('button', { name: 'Focus volume', exact: true })).toBeEnabled();
  await expect(editor.getByRole('button', { name: 'Next cells', exact: true })).toHaveCount(0);
  expect((await graph(page)).recipe).toEqual(before.recipe);
  expect(errors).toEqual([]);
});

test('owned mesh volume capture and preview drive explicit replacement membership without repainting', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await imported(page, memoryFile('authored-grid.ply', fixture.grid));
  const authored = await rectanglePlane(page, fixture.grid.positions.length, 'Shared grid plane');
  await page.locator(`[data-action-id="${authored.selection.id}"]`).dispatchEvent('dblclick');
  const editor = page.locator('#selection-owned-editor');
  await expect(editor).toBeVisible();
  await editor.getByRole('button', { name: 'Create volume…', exact: true }).click();
  await page.locator('#owned-volume-label').fill('Grid support');
  await page.locator('#owned-volume-kind').selectOption('mesh');
  await editor.getByRole('button', { name: 'Capture volume', exact: true }).click();
  await expect.poll(async () => {
    const node = (await graph(page)).recipe.nodes.find(node => node.id === authored.selection.id);
    return node.entries?.[0].volumes?.length;
  }).toBe(1);
  const captured = await metadata(page), selection = captured.graph.recipe.nodes.find(node => node.id === authored.selection.id),
    entry = selection.entries[0], volume = entry.volumes[0];
  expect(entry.ids).toEqual(Array.from({ length: fixture.grid.positions.length }, (_, id) => id));
  const volumeRow = page.locator(`[data-owned-selection-id="${selection.id}"][data-owned-entry-id="${entry.id}"][data-owned-volume-id="${volume.id}"]`);
  await expect(volumeRow).toBeVisible();
  await volumeRow.dispatchEvent('keydown', { key: 'F2' });
  await expect(editor).toContainText('support cells');
  await expect(editor).not.toContainText('Loading accepted support preview…');
  await expect(page.locator('#owned-inward_thickness-mode')).toHaveValue('auto');
  await page.locator('#owned-inward_thickness-mode').selectOption('override');
  await page.locator('#owned-inward_thickness').fill('0.01');
  await page.locator('#owned-inward_thickness').evaluate(input => { window.retainedOwnedDraft = input; });
  const draftDialogs = [], dismissDraftDialog = dialog => { draftDialogs.push(dialog.message()); void dialog.dismiss(); };
  page.on('dialog', dismissDraftDialog);
  try {
    await page.locator(`[data-action-id="${captured.graph.recipe.nodes.find(node => node.operation === 'source').id}"]`).click();
    await expect(page.locator('#owned-inward_thickness')).toBeVisible();
    await expect(page.locator('#owned-inward_thickness')).toHaveValue('0.01');
    expect(await page.locator('#owned-inward_thickness').evaluate(input => input === window.retainedOwnedDraft)).toBe(true);
    expect(draftDialogs).toEqual([]);
  } finally { page.off('dialog', dismissDraftDialog); }
  await page.locator('#owned-inward_thickness-mode').selectOption('auto');

  const reference = { selection: selection.id, entry: entry.id, volume: volume.id };
  const preview = async state => {
    const savedVolume = state.graph.recipe.nodes.find(node => node.id === selection.id).entries[0].volumes[0];
    const response = await page.request.post(new URL('/api/graph/selection-volumes/preview', page.url()).href, {
      headers: { 'X-Scansor-Request': '1', 'X-Scansor-Generation': String(state.project_generation) },
      data: { token: state.graph.token, reference, archive_sha256: savedVolume.archive.sha256, offset: 0, limit: 2048 },
    });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const originalPreview = await preview(captured);
  await page.locator('#owned-outward_thickness-mode').selectOption('override');
  await page.locator('#owned-outward_thickness').fill('0.1');
  await editor.getByRole('button', { name: 'Apply volume controls', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === selection.id).entries[0].volumes[0].options.outward_thickness).toBe(0.1);
  await expect(editor).toHaveAttribute('aria-busy', 'false');
  const overridden = await metadata(page), overriddenPreview = await preview(overridden);
  expect(overriddenPreview.positions).toEqual(originalPreview.positions);
  expect(overriddenPreview.normals).toEqual(originalPreview.normals);
  expect(overriddenPreview.resolved.inward_thickness).toBe(originalPreview.resolved.inward_thickness);
  expect(overriddenPreview.options.inward_thickness).toBeNull();
  expect(overriddenPreview.resolved.outward_thickness).toBe(0.1);
  await page.locator('#owned-outward_thickness-mode').selectOption('auto');
  await editor.getByRole('button', { name: 'Apply volume controls', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === selection.id).entries[0].volumes[0]).toEqual(volume);
  await expect(editor).toHaveAttribute('aria-busy', 'false');
  const choosing = page.waitForEvent('filechooser');
  await command(page, 'Replace scan…');
  await (await choosing).setFiles(memoryFile('dense-aligned-grid.ply', fixture.dense));
  await expect.poll(async () => (await metadata(page)).source?.filename).toBe('dense-aligned-grid.ply');
  await expect(page.locator('#source-progress')).toBeHidden();
  const pending = await metadata(page), targetSelection = pending.graph.recipe.nodes.find(node => node.id === selection.id),
    targetEntry = targetSelection.entries.at(-1);
  expect(targetSelection.entries[0]).toEqual(entry);
  expect(targetEntry.source_volume).toBeNull();
  expect(pending.graph.states[selection.id]).toBe('needs_input');
  await page.getByRole('button', { name: `Choose source volume for ${selection.label} · dense-aligned-grid.ply`, exact: true }).click();
  const sourceChoice = page.locator('#owned-source-volume');
  const value = JSON.stringify(reference, Object.keys(reference).sort());
  await expect(sourceChoice.locator(`option[value='${value}']`)).toBeEnabled();
  await sourceChoice.selectOption(value);
  await editor.getByRole('button', { name: 'Apply source volume', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === selection.id).entries.at(-1).source_volume).toEqual(reference);
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await expect.poll(async () => (await graph(page)).states[authored.fit.id]).toBe('ready');
  const regenerated = await graph(page);
  expect(regenerated.memberships[selection.id]).toEqual(Array.from({ length: fixture.dense.positions.length }, (_, id) => id));
  expect(regenerated.results[authored.fit.id].ids).toEqual(regenerated.memberships[selection.id]);
  expect(regenerated.recipe.nodes.find(node => node.id === selection.id).entries[0]).toEqual(entry);
  expect(regenerated.errors).toEqual({});

  await expect(page.getByRole('button', { name: 'Evaluate all', exact: true })).toBeEnabled();
  await page.locator(`[data-owned-selection-id="${selection.id}"][data-owned-entry-id="${entry.id}"][data-owned-volume-id="${volume.id}"]`).dispatchEvent('keydown', { key: 'F2' });
  await expect(editor).toContainText('support cells');
  const acceptDelete = dialog => { void dialog.accept(); };
  page.on('dialog', acceptDelete);
  try { await editor.getByRole('button', { name: 'Delete volume…', exact: true }).click(); }
  finally { page.off('dialog', acceptDelete); }
  await expect(editor).toContainText(/linked|referenced|used by/i);
  expect((await graph(page)).recipe).toEqual(regenerated.recipe);
  await editor.getByRole('button', { name: 'Create / replace volume…', exact: true }).click();
  await page.locator('#owned-author-mode').selectOption('create');
  await page.locator('#owned-volume-label').fill('Adopted dense support');
  await editor.getByRole('button', { name: 'Capture volume', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === selection.id).entries.at(-1).volumes.length).toBe(1);
  const adopted = (await graph(page)).recipe.nodes.find(node => node.id === selection.id);
  expect(adopted.entries[0]).toEqual(entry);
  expect(adopted.entries.at(-1).volumes[0].captured_ids).toEqual(regenerated.memberships[selection.id]);
  await expect(editor).toHaveAttribute('aria-busy', 'false');
  const beforeSave = await graph(page), downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  const download = await downloading;
  let savedText = '';
  for await (const part of await download.createReadStream()) savedText += part.toString();
  const saved = JSON.parse(savedText);
  expect(saved).toEqual(beforeSave.recipe);

  const sourceUploads = [];
  const recordUploads = request => {
    if (request.method() === 'POST' && /\/bytes$/.test(request.url())) sourceUploads.push(request.url());
  };
  page.on('request', recordUploads);
  const stopDialogs = acceptDialogs(page);
  try {
    await command(page, 'New project');
    await expect(page.locator('[data-add-scan]')).toBeEnabled();
    expect((await graph(page)).recipe.nodes).toEqual([]);
    const openingActions = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Load actions', exact: true }).click();
    const actionsChooser = await openingActions;
    expect(await actionsChooser.element().getAttribute('id')).toBe('file');
    await actionsChooser.setFiles({ name: 'saved-volumes.json', mimeType: 'application/json', buffer: Buffer.from(savedText) });
    await expect(page.locator('#locate-source-dialog')).toBeVisible();
    const hint = await page.locator('#locate-source-hint').textContent();
    expect(hint).toContain('dense-aligned-grid.ply');
    expect(hint).not.toContain('authored-grid.ply');
    expect(sourceUploads).toEqual([]);
    const locatingActive = page.waitForEvent('filechooser');
    await page.locator('#choose-located-source').click();
    const activeChooser = await locatingActive;
    expect(await activeChooser.element().getAttribute('id')).toBe('locate-source-file');
    await activeChooser.setFiles(memoryFile('renamed-exact-active-grid.ply', fixture.dense));
    await expect(page.locator('#locate-source-dialog')).toBeHidden();
    await expect.poll(async () => (await metadata(page)).source?.filename).toBe('renamed-exact-active-grid.ply');
    await expect(page.locator('#source-progress')).toBeHidden();
    const reopened = await metadata(page);
    expect(reopened.vertices).toBe(fixture.dense.positions.length);
    expect(reopened.graph.recipe).toEqual(saved);
    await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
    await expect.poll(async () => (await graph(page)).states[authored.fit.id]).toBe('ready');
    const replay = await graph(page), replaySelection = replay.recipe.nodes.find(node => node.id === selection.id);
    expect(replay.recipe).toEqual(saved);
    expect(replaySelection.entries).toEqual(adopted.entries);
    expect(replaySelection.entries.at(-1).source_volume).toEqual(reference);
    expect(replay.memberships[selection.id]).toEqual(regenerated.memberships[selection.id]);
    expect(replay.results[authored.fit.id].ids).toEqual(regenerated.memberships[selection.id]);
    expect(replay.errors).toEqual({});
    expect(sourceUploads).toHaveLength(1);
    await expect(page.locator('#locate-source-dialog')).toBeHidden();
  } finally { stopDialogs(); page.off('request', recordUploads); }
  expect(errors).toEqual([]);
});

test('empty entry opens the actual RealityScan File as one inspected source with unchanged coordinates', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const empty = await metadata(page);
  expect(empty.source).toBeNull();
  expect(empty.graph.recipe.nodes).toEqual([]);
  await expect(page.getByRole('button', { name: 'Selection', exact: true })).toBeDisabled();
  await page.locator('#home').click();
  await page.locator('#side').click();
  await page.locator('#top').click();
  await page.setViewportSize({ width: 380, height: 700 });
  for (const selector of ['[data-add-scan]', '[data-choose-example]']) {
    const control = page.locator(selector);
    await control.click({ trial: true });
    expect(await control.evaluate(button => {
      const box = button.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth
        && button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    })).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const meta = await imported(page, nozzle);
  expect(meta.graph.project_generation).toBe(meta.project_generation);
  expect(meta.graph.recipe.nodes).toHaveLength(1);
  expect(meta.graph.recipe.nodes[0].operation).toBe('source');
  expect(meta.vertices).toBe(24999);
  expect(meta.triangles).toBe(49994);
  expect(meta.source.source_sha256).toBe('537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358');
  expect(meta.bounds).toEqual([
    [-9.868260383605957, -10.770027160644531, 21.880172729492188],
    [9.098114967346191, 8.148521423339844, 32.007484436035156],
  ]);
  await command(page, 'Inspect source');
  await expect(page.locator('#source-details')).toContainText(path.basename(nozzle));
  await expect(page.locator('#source-details')).toContainText(meta.source.source_sha256);
  await expect(page.locator('#source-details')).toContainText(/unknown/i);
  await expect(page.locator('#source-details')).toContainText(/binary-triangle-ply-v1/);
  await expect(page.locator('#source-properties input, #source-properties select')).toHaveCount(0);
  await page.setViewportSize({ width: 380, height: 700 });
  await page.locator('[data-remove-scan]').click({ trial: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: 'Add scan…', exact: true })).toHaveAttribute('data-disabled', '');
  await page.keyboard.press('Escape');
  const reservations = [];
  page.on('request', request => { if (/\/api\/sources\/imports$/.test(request.url()) && request.method() === 'POST') reservations.push(request.url()); });
  // A stale native chooser completion must also be rejected after a source exists.
  await page.locator('#scan-file').setInputFiles(memoryFile('second-source.ply', fixture.scene));
  await expect(page.locator('#status')).toContainText('already has a source');
  expect(reservations).toEqual([]);
  expect((await metadata(page)).project_generation).toBe(meta.project_generation);
  expect((await metadata(page)).source.source_sha256).toBe(meta.source.source_sha256);
  expect(errors).toEqual([]);
});

test('independent source display keeps all original rows and user rectangle selection can create a plane fit', async ({ page }) => {
  const meta = await imported(page, memoryFile('independent-scene.ply', fixture.scene));
  const positions = await (await page.request.get(new URL(meta.positions.url, page.url()).href)).body();
  expect([...new Float32Array(positions.buffer.slice(positions.byteOffset, positions.byteOffset + positions.byteLength))])
    .toEqual(fixture.scene.positions.flat().map(Math.fround));
  const finish = acceptDialogs(page);
  await command(page, 'New project');
  await expect(page.locator('[data-add-scan]')).toBeVisible();
  finish();
  await imported(page, memoryFile('independent-plane.ply', fixture.grid));
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  await page.getByRole('button', { name: 'Selection', exact: true }).click();
  await expect(page.locator('#selection-tools')).toBeVisible();
  await page.locator('#selection-shape').selectOption('rectangle');
  await page.locator('#selection-depth').selectOption('through_all');
  await page.locator('#tool').selectOption('replace');
  // The top view makes this independent near-horizontal grid a complete patch.
  await page.locator('#top').click();
  const canvas = await page.locator('#viewport canvas').boundingBox();
  await page.mouse.move(canvas.x + 8, canvas.y + 65);
  await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width - 8, canvas.y + canvas.height - 35, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => {
    const state = await graph(page), selection = state.recipe.nodes.find(node => node.operation === 'selection');
    return state.memberships[selection?.id]?.length;
  }).toBe(12);
  const selected = await metadata(page), selection = selected.graph.recipe.nodes.find(node => node.operation === 'selection');
  expect(painted(selection, selected.graph, selected).ids).toEqual(Array.from({ length: 12 }, (_, id) => id));
  await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
  await page.locator('#new-fit-kind').selectOption('plane');
  await page.locator('#new-fit-inputs').selectOption(selection.id);
  await page.locator('#add-fit-form').getByRole('button', { name: 'Add fit', exact: true }).click();
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await expect.poll(async () => {
    const state = await graph(page), fit = state.recipe.nodes.find(node => node.operation === 'fit');
    return fit && state.states[fit.id];
  }).toBe('ready');
  const state = await graph(page), fit = state.recipe.nodes.find(node => node.operation === 'fit');
  expect(state.results[fit.id].weighted_rms).toBeLessThan(1e-7);
  expect(fit.initial_parameters).toBeUndefined();
});

test('raw RealityScan File supports real full-row pointer selection, plane evaluation and an actual fit edit', async ({ page }) => {
  test.setTimeout(120000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const meta = await imported(page, nozzle);
  expect(meta.graph.recipe.nodes).toHaveLength(1);
  const positionsResponse = await page.request.get(new URL(meta.positions.url, page.url()).href);
  const indicesResponse = await page.request.get(new URL(meta.indices.url, page.url()).href);
  const positionsBytes = await positionsResponse.body(), indicesBytes = await indicesResponse.body();
  const points = new Float32Array(positionsBytes.buffer.slice(positionsBytes.byteOffset, positionsBytes.byteOffset + positionsBytes.byteLength));
  const indices = new Uint32Array(indicesBytes.buffer.slice(indicesBytes.byteOffset, indicesBytes.byteOffset + indicesBytes.byteLength));
  const { selection, fit, state } = await rectanglePlane(page, 24999, 'Raw pointer plane');
  expect(state.recipe.nodes).toHaveLength(3);
  const result = state.results[fit.id], equation = result.parameters;
  expect(equation).toHaveLength(4);
  expect(equation.every(Number.isFinite)).toBe(true);
  expect(Math.hypot(...equation.slice(0, 3))).toBeCloseTo(1, 12);
  // Independent incident-area weighting and residual/RMS readback over every
  // original selected row. This checks reachability, not raw-patch fit quality.
  const weights = new Float64Array(meta.vertices);
  for (let offset = 0; offset < indices.length; offset += 3) {
    const [a, b, c] = indices.slice(offset, offset + 3);
    const u = [0, 1, 2].map(axis => points[b * 3 + axis] - points[a * 3 + axis]);
    const v = [0, 1, 2].map(axis => points[c * 3 + axis] - points[a * 3 + axis]);
    const share = Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]) / 6;
    for (const id of [a, b, c]) weights[id] += share;
  }
  let squared = 0, total = 0, minimumWeight = Infinity, maximumResidualDifference = 0;
  const selectedIds = painted(selection, state, meta).ids;
  for (const [offset, id] of selectedIds.entries()) {
    minimumWeight = Math.min(minimumWeight, weights[id]);
    const residual = points[id * 3] * equation[0] + points[id * 3 + 1] * equation[1] + points[id * 3 + 2] * equation[2] - equation[3];
    maximumResidualDifference = Math.max(maximumResidualDifference, Math.abs(result.residuals[offset] - residual));
    squared += weights[id] * residual * residual; total += weights[id];
  }
  expect(minimumWeight).toBeGreaterThan(0);
  expect(maximumResidualDifference).toBeLessThan(1e-10);
  expect(Math.abs(result.weighted_rms - Math.sqrt(squared / total))).toBeLessThan(1e-10);
  await page.locator(`#action-list .action-select[data-action-id="${fit.id}"]`).click({ button: 'right' });
  await page.locator('#feature-context-rename').click();
  await page.locator('#rename-feature-label').fill('Edited raw pointer plane');
  await page.locator('#rename-feature-form').getByRole('button', { name: 'Rename', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.id === fit.id)?.label).toBe('Edited raw pointer plane');
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await expect.poll(async () => (await graph(page)).states[fit.id]).toBe('ready');
  const after = await metadata(page);
  expect(after.project_generation).toBe(meta.project_generation);
  expect(after.source.source_sha256).toBe(meta.source.source_sha256);
  expect(painted(after.graph.recipe.nodes.find(node => node.id === selection.id), after.graph, after).ids).toEqual(selectedIds);
  expect((await (await page.request.get(new URL(after.positions.url, page.url()).href)).body()).equals(positionsBytes)).toBe(true);
  expect(errors).toEqual([]);
});

for (const docked of [false, true]) test(`real selection and dependent fit survive declined removal with the same ${docked ? 'docked' : 'popup'} draft until accepted`, async ({ page }) => {
  await imported(page, memoryFile('dependent-plane.ply', fixture.grid));
  const { selection, fit, state } = await rectanglePlane(page, 12, 'Dependent pointer plane');
  expect(state.results[fit.id].weighted_rms).toBeLessThan(1e-7);
  const before = await metadata(page);
  await draft(page, { docked });
  for (const name of ['New project', 'Remove scan…']) {
    page.once('dialog', dialog => dialog.dismiss()); await command(page, name);
    await expectDraft(page);
    expect((await graph(page)).recipe).toEqual(before.graph.recipe);
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
  }
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/project/reset', async route => { await gate; await route.continue(); });
  const stop = acceptDialogs(page);
  try {
    const requested = page.waitForRequest('**/api/project/reset');
    await command(page, 'Remove scan…'); await requested;
    await expectDraft(page);
    expect((await graph(page)).recipe).toEqual(before.graph.recipe);
    expect((await graph(page)).states[fit.id]).toBe('ready');
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
    release();
    await expect(page.locator('[data-add-scan]')).toBeVisible();
    await expect(page.locator('#point-dialog')).toBeHidden();
    const empty = await metadata(page);
    expect(empty.project_generation).toBe(before.project_generation + 1);
    expect(empty.graph.recipe.nodes).toEqual([]);
    expect(empty.graph.results[selection.id]).toBeUndefined();
    expect(empty.graph.results[fit.id]).toBeUndefined();
    const after = await imported(page, memoryFile('different-after-removal.ply', fixture.scene));
    expect(after.source.source_sha256).not.toBe(before.source.source_sha256);
    expect(after.vertices).toBe(84);
    expect(after.graph.recipe.nodes).toHaveLength(1);
    expect(after.graph.recipe.nodes[0].operation).toBe('source');
    expect(after.graph.recipe.nodes.some(node => [selection.id, fit.id].includes(node.id))).toBe(false);
  } finally { release(); stop(); }
});

test('empty selection and synthetic picker cancel preserve accepted empty state without an import request', async ({ page }) => {
  const before = await metadata(page), writes = [];
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await choose(page, []);
  // This is an explicit browser event seam, not a claim about an OS dialog.
  await page.locator('#scan-file').dispatchEvent('cancel');
  const after = await metadata(page);
  expect(after.project_generation).toBe(before.project_generation);
  expect(after.graph.recipe).toEqual(before.graph.recipe);
  expect(writes).toEqual([]);
  await expect(page.locator('[data-add-scan]')).toBeEnabled();
});

test('malformed and over-limit Files preserve the empty graph and leave import available', async ({ page }, testInfo) => {
  const before = await metadata(page);
  const oversized = testInfo.outputPath('oversized.ply');
  const file = await open(oversized, 'w');
  try {
    // Playwright's in-memory File payloads are capped at 50 MiB. A sparse
    // on-disk fixture exercises the actual advertised limit without a huge Buffer.
    await file.truncate(before.capabilities.source_import.limits.max_source_bytes + 1);
  } finally { await file.close(); }
  try {
    for (const value of [
      { name: 'invalid.ply', mimeType: 'application/octet-stream', buffer: Buffer.from('not a PLY') },
      oversized,
    ]) {
      await choose(page, value);
      await expect(page.locator('#status')).toContainText(/invalid|PLY|STL|limit|large|exceed/i);
      await expect(page.locator('#source-progress')).toBeHidden();
      expect((await metadata(page)).project_generation).toBe(before.project_generation);
      expect((await graph(page)).recipe).toEqual(before.graph.recipe);
      await expect(page.locator('[data-add-scan]')).toBeEnabled();
    }
  } finally {
    await rm(oversized, { force: true });
  }
});

test('explicit Cancel reaches the real reserved operation and late held upload cannot publish', async ({ page }) => {
  const before = await metadata(page);
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const held = new Promise(resolve => { entered = resolve; });
  await page.route('**/api/sources/imports/*/bytes', async route => {
    entered();
    await gate;
    await route.continue().catch(() => {});
  });
  let failCancel = true;
  await page.route('**/api/sources/imports/*/cancel', route => {
    if (!failCancel) return route.continue();
    failCancel = false;
    return route.fulfill({ status: 503, json: { error: 'Temporary cancellation failure' } });
  });
  try {
    await choose(page, nozzle);
    await held;
    await expect(page.locator('#source-progress')).toBeVisible();
    const rejectedCancel = page.waitForResponse(response => /\/api\/sources\/imports\/[^/]+\/cancel$/.test(response.url()));
    await page.locator('#cancel-source-import').click();
    expect((await rejectedCancel).status()).toBe(503);
    await expect(page.locator('#status')).toContainText('Temporary cancellation failure');
    await expect(page.locator('#cancel-source-import')).toBeEnabled();
    const cancelled = page.waitForResponse(response => /\/api\/sources\/imports\/[^/]+\/cancel$/.test(response.url()));
    await page.locator('#cancel-source-import').click();
    const response = await cancelled;
    expect(response.ok(), await response.text()).toBe(true);
    const operation = await response.json();
    await expect.poll(async () => (await (await page.request.get(new URL(`/api/sources/imports/${operation.operationId}`, page.url()).href)).json()).status).toBe('cancelled');
    release();
    await expect(page.locator('#source-progress')).toBeHidden();
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
    expect((await graph(page)).recipe).toEqual(before.graph.recipe);
    await expect(page.locator('[data-add-scan]')).toBeEnabled();
  } finally { release(); }
});

for (const docked of [false, true]) test(`declined New/remove and failed example preserve the same ${docked ? 'docked' : 'popup'} editor draft`, async ({ page }) => {
  await imported(page, memoryFile('draft-source.ply', fixture.scene));
  const before = await metadata(page);
  await draft(page, { docked });
  for (const label of ['New project', 'Remove scan…']) {
    page.once('dialog', dialog => dialog.dismiss());
    await command(page, label);
    await expectDraft(page);
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
  }
  await page.route('**/api/examples/select', route => route.fulfill({ status: 422, json: { error: 'Unavailable test example' } }));
  await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
  await page.locator('#example-choice').selectOption('nozzle');
  const stop = acceptDialogs(page);
  await page.locator('#open-example').click();
  await expect(page.locator('#example-error')).toContainText('Unavailable test example');
  stop();
  await expectDraft(page);
  expect((await graph(page)).recipe).toEqual(before.graph.recipe);
  expect((await metadata(page)).project_generation).toBe(before.project_generation);
  await page.locator('#cancel-example').click();
  await page.locator('#new-point-label').focus();
  await expect(page.locator('#new-point-label')).toBeFocused();
});

test('confirmed removal discards dependent work only after acceptance and examples remain explicit', async ({ page }) => {
  const meta = await imported(page, memoryFile('remove-source.ply', fixture.scene));
  await page.getByRole('button', { name: 'Point', exact: true }).click();
  await page.locator('#new-point-label').fill('Committed point');
  await page.locator('#add-point-form').getByRole('button', { name: 'Add point', exact: true }).click();
  await expect.poll(async () => (await graph(page)).recipe.nodes.length).toBe(2);
  await draft(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/project/reset', async route => { await gate; await route.continue(); });
  const stop = acceptDialogs(page);
  try {
    const requested = page.waitForRequest('**/api/project/reset');
    await command(page, 'Remove scan…');
    await requested;
    await expectDraft(page);
    expect((await metadata(page)).project_generation).toBe(meta.project_generation);
    release();
    await expect(page.locator('[data-add-scan]')).toBeVisible();
    await expect(page.locator('#point-dialog')).toBeHidden();
    expect((await graph(page)).recipe.nodes).toEqual([]);
    await page.locator('[data-choose-example]').click();
    await page.locator('#example-choice').selectOption('nozzle');
    await page.locator('#open-example').click();
    await expect.poll(async () => (await (await page.request.get(new URL('/api/examples', page.url()).href)).json()).active).toBe('nozzle');
    await expect(page.locator('#example-dialog')).toBeHidden();
    await expect(page.locator('#viewport canvas')).toBeVisible();
    expect((await graph(page)).recipe.nodes.length).toBeGreaterThan(1);
  } finally { release(); stop(); }
});

for (const failure of ['display delivery', 'accepted mutation reply']) test(`example with failed ${failure} fences edits, preserves prior actions and retries explicitly`, async ({ page }) => {
  await imported(page, memoryFile('prior-source.ply', fixture.scene));
  const before = await graph(page);
  await draft(page);
  let failDelivery = true;
  if (failure === 'display delivery') {
    await page.route('**/mesh/positions?*', route => {
      if (!failDelivery) return route.continue();
      failDelivery = false;
      return route.fulfill({ status: 503, json: { error: 'Injected display delivery failure' } });
    });
  } else {
    await page.route('**/api/examples/select', async route => {
      // Execute the real mutation and then lose only its response. The UI must
      // fence the uncertain delivery even though no success reply reached it.
      const accepted = await route.fetch();
      expect(accepted.ok(), await accepted.text()).toBe(true);
      await route.abort('failed');
    });
  }
  const stop = acceptDialogs(page);
  try {
    await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
    await page.locator('#example-choice').selectOption('nozzle');
    await page.locator('#open-example').click();
    await expect(page.locator('#source-unavailable')).toBeVisible();
    await expectDraft(page);
    await expect(page.getByRole('button', { name: 'Point', exact: true })).toBeDisabled();
    expect((await graph(page)).recipe.nodes[0].source_sha256).not.toBe(before.recipe.nodes[0].source_sha256);
    const downloading = page.waitForEvent('download');
    await page.locator('#download-prior-actions').click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe('scansor-prior-actions.json');
    let content = '';
    for await (const part of await download.createReadStream()) content += part.toString();
    expect(JSON.parse(content)).toEqual(before.recipe);
    await page.locator('#retry-source-workspace').click();
    await expect(page.locator('#source-unavailable')).toBeHidden();
    await expect(page.locator('#point-dialog')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Point', exact: true })).toBeEnabled();
    const accepted = await graph(page);
    await expect(page.locator('#action-list')).toContainText(accepted.recipe.nodes[0].label);
    await expect(page.locator('#action-list')).not.toContainText(before.recipe.nodes[0].label);
    await expect(page.locator('#viewport canvas')).toBeVisible();
  } finally { stop(); }
});
