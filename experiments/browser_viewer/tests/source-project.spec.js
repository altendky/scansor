import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = path.join(repository, '.venv/bin/python');
const nozzle = path.join(repository, 'examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply');
const prepareFixtures = () => JSON.parse(execFileSync(python, ['-c', [
  'import base64,json',
  'from tests.native_scan_fixture import native_scene_fixture,native_grid_fixture',
  'def record(f): return dict(raw=base64.b64encode(f.raw).decode(), positions=f.positions.tolist(), triangles=f.triangles.tolist())',
  'print(json.dumps(dict(scene=record(native_scene_fixture()),grid=record(native_grid_fixture(4,3)))))',
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
  await page.getByRole('button', { name: 'Point', exact: true }).click();
  await page.locator('#new-point-label').fill('Keep this live draft');
  if (docked) await page.locator('#dock-edit').click();
  await page.locator('#new-point-label').focus();
  await page.locator('#new-point-label').evaluate(input => { window.sourceDraftInput = input; });
}

async function expectDraft(page) {
  await expect(page.locator('#point-dialog')).toBeVisible();
  await expect(page.locator('#new-point-label')).toHaveValue('Keep this live draft');
  expect(await page.locator('#new-point-label').evaluate(input => input === window.sourceDraftInput)).toBe(true);
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
  await expect.poll(async () => (await graph(page)).recipe.nodes.find(node => node.operation === 'selection')?.ids.length).toBe(12);
  const selection = (await graph(page)).recipe.nodes.find(node => node.operation === 'selection');
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

test('malformed and over-limit Files preserve the empty graph and leave import available', async ({ page }) => {
  const before = await metadata(page);
  for (const value of [
    { name: 'invalid.ply', mimeType: 'application/octet-stream', buffer: Buffer.from('not a PLY') },
    { name: 'oversized.ply', mimeType: 'application/octet-stream', buffer: Buffer.alloc(before.capabilities.source_import.limits.max_source_bytes + 1) },
  ]) {
    await choose(page, value);
    await expect(page.locator('#status')).toContainText(/invalid|PLY|limit|large|exceed/i);
    await expect(page.locator('#source-progress')).toBeHidden();
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
    expect((await graph(page)).recipe).toEqual(before.graph.recipe);
    await expect(page.locator('[data-add-scan]')).toBeEnabled();
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
