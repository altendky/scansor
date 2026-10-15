import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = path.join(repository, '.venv/bin/python');
let fixtures;
const file = (name, raw) => ({ name, mimeType: 'application/octet-stream', buffer: Buffer.from(raw, 'base64') });
const metadata = async page => (await page.request.get(new URL('/api/meta', page.url()).href)).json();
const graph = async page => (await metadata(page)).graph;
const test = base.extend({
  actionsOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: { ...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    const collect = data => { log = (log + data.toString()).slice(-16000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Actions native server did not start: ' + log)), 30000);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(Error(`Actions server exited ${code}: ${log}`)); });
        child.stdout.on('data', data => {
          collect(data);
          const found = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if (found) { clearTimeout(timer); resolve(found[0]); }
        });
      });
      await use(origin);
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close');
        const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.kill('SIGINT');
        try { await closed; } finally { clearTimeout(timer); }
      }
    }
  }, { scope: 'worker' }],
});

test.beforeAll(() => {
  fixtures = JSON.parse(execFileSync(python, ['-c', [
    'import base64,json',
    'from tests.native_scan_fixture import native_scene_fixture,native_grid_fixture',
    'def record(f): return dict(raw=base64.b64encode(f.raw).decode(), positions=f.positions.tolist())',
    'print(json.dumps(dict(scene=record(native_scene_fixture()),grid=record(native_grid_fixture(4,3)))))',
  ].join('\n')], { cwd: repository, encoding: 'utf8', env: { ...process.env, OPENBLAS_NUM_THREADS: '1' } }));
});

async function command(page, name) {
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}
async function importScan(page, name, fixture) {
  await page.locator('#scan-file').setInputFiles(file(name, fixture.raw));
  await expect.poll(async () => (await metadata(page)).source?.filename).toBe(name);
  await expect(page.locator('#source-progress')).toBeHidden();
  return metadata(page);
}
async function load(page, recipe) {
  await page.locator('#file').setInputFiles({ name: 'saved-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe)) });
}
async function save(page) {
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  const download = await waiting;
  let content = '';
  for await (const part of await download.createReadStream()) content += part.toString();
  return JSON.parse(content);
}
function acceptDialogs(page) {
  const listener = dialog => dialog.accept();
  page.on('dialog', listener);
  return () => page.off('dialog', listener);
}
async function draft(page, docked = false) {
  await page.getByRole('button', { name: 'Point', exact: true }).click();
  await page.locator('#new-point-label').fill('Retain this actions draft');
  if (docked) await page.locator('#dock-edit').click();
  await page.locator('#new-point-label').evaluate(input => { window.actionsDraftInput = input; });
}
async function expectDraft(page) {
  await expect(page.locator('#point-dialog')).toBeVisible();
  await expect(page.locator('#new-point-label')).toHaveValue('Retain this actions draft');
  expect(await page.locator('#new-point-label').evaluate(input => input === window.actionsDraftInput)).toBe(true);
}
async function savedOtherSource(page) {
  await importScan(page, 'original-scene.ply', fixtures.scene);
  const recipe = await save(page);
  const stop = acceptDialogs(page);
  try { await command(page, 'New project'); } finally { stop(); }
  await expect(page.locator('[data-add-scan]')).toBeVisible();
  await importScan(page, 'current-grid.ply', fixtures.grid);
  return recipe;
}

test.beforeEach(async ({ page, actionsOrigin }) => {
  const current = await (await page.request.get(new URL('/api/meta', actionsOrigin).href)).json();
  const response = await page.request.post(new URL('/api/project/reset', actionsOrigin).href, {
    headers: { 'X-Scansor-Request': '1' },
    data: { generation: current.project_generation, token: current.graph.token, confirmedDiscard: true },
  });
  expect(response.ok(), await response.text()).toBe(true);
  await page.goto(actionsOrigin);
  await expect(page.locator('[data-add-scan]')).toBeEnabled();
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
});

test('saved actions locate renamed original bytes and replace another source only after full acceptance', async ({ page }, testInfo) => {
  const recipe = await savedOtherSource(page), before = await metadata(page);
  recipe.nodes.push({ id: 'reopened-origin', operation: 'point', label: 'Reopened origin', initial_coordinates: [1, 2, 3] });
  const sourceWrites = [];
  page.on('request', request => { if (request.method() === 'POST' && /\/bytes$/.test(request.url())) sourceWrites.push(request.url()); });
  await load(page, recipe);
  await expect(page.locator('#locate-source-dialog')).toBeVisible();
  await expect(page.locator('#locate-source-hint')).toContainText('original-scene.ply');
  await page.screenshot({ path: testInfo.outputPath('locator-1440.png') });
  await page.setViewportSize({ width: 380, height: 700 });
  for (const id of ['choose-located-source', 'cancel-locate-source']) {
    await page.locator(`#${id}`).click({ trial: true });
    expect(await page.locator(`#${id}`).evaluate(element => {
      const bounds = element.getBoundingClientRect();
      return bounds.top >= 0 && bounds.bottom <= innerHeight && bounds.left >= 0 && bounds.right <= innerWidth;
    })).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath('locator-380.png') });
  await page.setViewportSize({ width: 1440, height: 1000 });
  expect((await metadata(page)).project_generation).toBe(before.project_generation);
  expect(sourceWrites).toEqual([]);
  const stop = acceptDialogs(page);
  try {
    await page.locator('#locate-source-file').setInputFiles(file('renamed-moved-source.ply', fixtures.scene.raw));
    await expect.poll(async () => (await metadata(page)).project_generation).toBe(before.project_generation + 1);
    await expect(page.locator('#source-progress')).toBeHidden();
  } finally { stop(); }
  await expect(page.locator('#action-list')).toContainText('Reopened origin');
  const accepted = await metadata(page);
  expect(accepted.graph.recipe).toMatchObject(recipe);
  expect(accepted.graph.states['reopened-origin']).not.toBe('ready');
  expect(accepted.source.source_sha256).toBe(recipe.nodes[0].source_sha256);
  const positions = await (await page.request.get(new URL(accepted.positions.url, page.url()).href)).body();
  expect([...new Float32Array(positions.buffer.slice(positions.byteOffset, positions.byteOffset + positions.byteLength))])
    .toEqual(fixtures.scene.positions.flat().map(Math.fround));
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await expect.poll(async () => (await graph(page)).states['reopened-origin']).toBe('ready');
  expect(sourceWrites).toHaveLength(1);
  await command(page, 'Inspect source');
  await expect(page.locator('#source-details')).toContainText('Loaded filename');
  await expect(page.locator('#source-details')).toContainText('renamed-moved-source.ply');
});

for (const docked of [false, true]) test(`lookup cancel, picker cancel and wrong bytes preserve the actual ${docked ? 'docked' : 'popup'} editor`, async ({ page }) => {
  const recipe = await savedOtherSource(page), before = await metadata(page);
  await draft(page, docked);
  await load(page, recipe);
  await expect(page.locator('#locate-source-dialog')).toBeVisible();
  await page.locator('#locate-source-file').dispatchEvent('cancel');
  await expectDraft(page);
  expect((await metadata(page)).graph.recipe).toEqual(before.graph.recipe);
  await page.keyboard.press('Escape');
  await expect(page.locator('#locate-source-dialog')).toBeHidden();
  await expectDraft(page);
  await load(page, recipe);
  await expect(page.locator('#locate-source-dialog')).toBeVisible();
  const stop = acceptDialogs(page);
  try {
    await page.locator('#locate-source-file').setInputFiles(file('original-scene.ply', fixtures.grid.raw));
    await expect(page.locator('#locate-source-dialog')).toBeVisible();
    await expect(page.locator('#locate-source-error')).not.toBeEmpty();
  } finally { stop(); }
  await expectDraft(page);
  expect((await metadata(page)).project_generation).toBe(before.project_generation);
  expect((await metadata(page)).graph.recipe).toEqual(before.graph.recipe);
  // Same-length, parseable changed geometry must also fail after a real upload.
  const changed = Buffer.from(fixtures.scene.raw, 'base64');
  changed[changed.indexOf(Buffer.from('end_header\n')) + Buffer.byteLength('end_header\n')] ^= 1;
  const stopRetry = acceptDialogs(page);
  try {
    await page.locator('#locate-source-file').setInputFiles(file('original-scene.ply', changed.toString('base64')));
    await expect(page.locator('#locate-source-dialog')).toBeVisible();
    await expect(page.locator('#locate-source-error')).toContainText(/match|source/i);
    await expect(page.locator('#source-progress')).toBeHidden();
  } finally { stopRetry(); }
  expect((await metadata(page)).project_generation).toBe(before.project_generation);
  await expectDraft(page);
  await page.locator('#cancel-locate-source').click();
  await expect(page.locator('#locate-source-dialog')).toBeHidden();
  await expectDraft(page);
});

test('matching actions reopen without bytes and empty actions advance the epoch with a fresh graph', async ({ page }) => {
  await importScan(page, 'matching-grid.ply', fixtures.grid);
  const recipe = await save(page), before = await metadata(page), uploads = [];
  recipe.nodes.push({ id: 'point-a', operation: 'point', label: 'Repeated name', initial_coordinates: [1, 2, 3] },
    { id: 'point-b', operation: 'point', label: 'Repeated name', initial_coordinates: [4, 5, 6] });
  page.on('request', request => { if (/\/bytes$/.test(request.url())) uploads.push(request.url()); });
  const stop = acceptDialogs(page);
  try {
    await load(page, recipe);
    await expect.poll(async () => (await metadata(page)).project_generation).toBe(before.project_generation + 1);
    await expect(page.locator('#import-warning')).toBeVisible();
    expect((await graph(page)).recipe.nodes.slice(1).map(node => node.id)).toEqual(['point-a', 'point-b']);
    await expect(page.locator('#locate-source-dialog')).toBeHidden();
    await load(page, { ...recipe, nodes: [], groups: [], output: null });
    await expect.poll(async () => (await metadata(page)).project_generation).toBe(before.project_generation + 2);
    await expect(page.locator('[data-add-scan]')).toBeVisible();
    expect((await graph(page)).recipe.nodes).toEqual([]);
    expect(uploads).toEqual([]);
  } finally { stop(); }
});

test('unsupported interpretation is rejected before a source operation and keeps the live draft', async ({ page }) => {
  await importScan(page, 'current-grid.ply', fixtures.grid);
  const before = await metadata(page), recipe = await save(page), operations = [];
  recipe.nodes[0].descriptor.interpretation_sha256 = 'a'.repeat(64);
  await draft(page);
  page.on('request', request => { if (request.method() === 'POST' && /\/api\/actions\/open$|\/bytes$/.test(request.url())) operations.push(request.url()); });
  await load(page, recipe);
  await expect(page.locator('#status')).toContainText(/interpretation|binding|descriptor/i);
  await expect(page.locator('#locate-source-dialog')).toBeHidden();
  await expectDraft(page);
  expect((await metadata(page)).project_generation).toBe(before.project_generation);
  expect((await graph(page)).recipe).toEqual(before.graph.recipe);
  expect(operations).toEqual([]);
});

test('Cancel during a held located upload preserves the old project and draft', async ({ page }) => {
  const recipe = await savedOtherSource(page), before = await metadata(page);
  await draft(page);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let requested;
  const arrived = new Promise(resolve => { requested = resolve; });
  await page.route('**/api/sources/imports/*/bytes', async route => { requested(); await held; await route.continue(); });
  await load(page, recipe);
  await expect(page.locator('#locate-source-dialog')).toBeVisible();
  const stop = acceptDialogs(page);
  try {
    await page.locator('#locate-source-file').setInputFiles(file('located-scene.ply', fixtures.scene.raw));
    await arrived;
    await expect(page.locator('#locate-source-dialog')).toBeHidden();
    await page.locator('#cancel-source-import').click();
    release();
    await expect(page.locator('#source-progress')).toBeHidden();
    expect((await metadata(page)).project_generation).toBe(before.project_generation);
    expect((await graph(page)).recipe).toEqual(before.graph.recipe);
    await expectDraft(page);
  } finally { release(); stop(); }
});

test('accepted actions with lost display delivery recover explicitly with warnings and preserve prior draft until adoption', async ({ page }) => {
  await importScan(page, 'current-grid.ply', fixtures.grid);
  const before = await metadata(page), recipe = await save(page);
  recipe.nodes.push({ id: 'a', operation: 'point', label: 'Same label', initial_coordinates: [1, 2, 3] },
    { id: 'b', operation: 'point', label: 'Same label', initial_coordinates: [4, 5, 6] });
  await draft(page);
  let fail = true;
  await page.route('**/mesh/positions?*', route => {
    if (!fail) return route.continue();
    fail = false;
    return route.fulfill({ status: 503, json: { error: 'Injected accepted display failure' } });
  });
  const stop = acceptDialogs(page);
  try {
    await load(page, recipe);
    await expect(page.locator('#source-unavailable')).toBeVisible();
    await expectDraft(page);
    expect((await metadata(page)).project_generation).toBe(before.project_generation + 1);
    await page.locator('#retry-source-workspace').click();
    await expect(page.locator('#source-unavailable')).toBeHidden();
    await expect(page.locator('#point-dialog')).toBeHidden();
    await expect(page.locator('#import-warning')).toBeVisible();
    expect((await graph(page)).recipe.nodes.slice(1).map(node => node.id)).toEqual(['a', 'b']);
    await expect(page.locator('#viewport canvas')).toBeVisible();
  } finally { stop(); }
});

test('Auto evaluates a reopened independent plane only after complete project adoption', async ({ page }) => {
  await importScan(page, 'auto-grid.ply', fixtures.grid);
  const recipe = await save(page), source = recipe.nodes[0];
  recipe.nodes.push({ id: 'all-grid', operation: 'selection', label: 'Grid observations', source: source.id, ids: Array.from({ length: 12 }, (_, i) => i) },
    { id: 'grid-plane', operation: 'fit', label: 'Reopened plane', kind: 'plane', selections: ['all-grid'] });
  recipe.output = 'grid-plane';
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).check();
  await expect.poll(async () => (await (await page.request.get(new URL('/api/graph', page.url()).href)).json()).evaluation_running).toBe(false);
  await expect(page.locator('#load')).toBeEnabled();
  const stop = acceptDialogs(page);
  try {
    await load(page, recipe);
    await expect.poll(async () => (await graph(page)).states['grid-plane']).toBe('ready');
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect(page.locator('#action-list')).toContainText('Reopened plane');
    const result = (await graph(page)).results['grid-plane'];
    expect(result.weighted_rms).toBeLessThan(1e-7);
    await expect(page.getByRole('button', { name: 'Point', exact: true })).toBeEnabled();
  } finally { stop(); }
});

test('losing an actual accepted open reply fences prior work and recovers accepted warnings', async ({ page }) => {
  await importScan(page, 'reply-grid.ply', fixtures.grid);
  const before = await metadata(page), recipe = await save(page);
  recipe.nodes.push({ id: 'reply-a', operation: 'point', label: 'Reply duplicate', initial_coordinates: [1, 2, 3] },
    { id: 'reply-b', operation: 'point', label: 'Reply duplicate', initial_coordinates: [4, 5, 6] });
  await draft(page);
  await page.route('**/api/actions/open', async route => {
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBe(true);
    await expect.poll(async () => (await metadata(page)).project_generation).toBe(before.project_generation + 1);
    await route.abort('failed');
  });
  const stop = acceptDialogs(page);
  try {
    await load(page, recipe);
    await expect(page.locator('#source-unavailable')).toBeVisible();
    await expectDraft(page);
    await expect(page.locator('#import-warning')).toBeHidden();
    expect((await metadata(page)).import_warnings).toHaveLength(1);
    await page.locator('#retry-source-workspace').click();
    await expect(page.locator('#source-unavailable')).toBeHidden();
    await expect(page.locator('#import-warning')).toBeVisible();
    await expect(page.locator('#point-dialog')).toBeHidden();
    expect((await graph(page)).recipe.nodes.slice(1).map(node => node.id)).toEqual(['reply-a', 'reply-b']);
  } finally { stop(); }
});
