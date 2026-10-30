import {test, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';

const repository = fileURLToPath(new URL('../../../', import.meta.url));
let child, origin, work, errors;
const observer = `\nObject.defineProperty(globalThis, '__popupRead', {value: () => structuredClone({
  camera: {position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), target: controls.target.toArray()},
  selection: [...selectedFeatureIds].sort(), workflow: activeEditWorkflow()?.id,
  preview: surfaceSelectionPreview.children.map(child => Array.from(child.geometry.getAttribute('position').array)),
})});\n`;
const read = page => page.evaluate(() => globalThis.__popupRead());
const popup = page => page.locator('#edit-popup');
const grip = page => page.locator('#move-edit');
const rect = async page => {const box = await popup(page).boundingBox(); expect(box).toBeTruthy(); return box;};
const settled = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const graph = async page => (await page.request.get(origin + '/api/graph')).json();
const gate = async (page, action) => {const result = await page.request.post(origin + '/__test/surface-preview', {data: {action}}); expect(result.ok()).toBe(true);};

test.beforeAll(async () => {
  child = spawn(repository + '/.venv/bin/python', ['-m', 'tests.native_surface_preview_server'], {
    cwd: repository, env: {...process.env, PYTHONPATH: repository + ':' + repository + '/src', OPENBLAS_NUM_THREADS: '1'},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stderr.on('data', bytes => {log = (log + bytes).slice(-16000);});
  origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Popup fixture startup timed out: ' + log)), 30000);
    child.once('error', error => {clearTimeout(timer); reject(error);});
    child.once('exit', code => {clearTimeout(timer); reject(Error('Popup fixture exited ' + code + ': ' + log));});
    child.stdout.on('data', bytes => {log += bytes; const match = log.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) {clearTimeout(timer); resolve(match[0]);}});
  });
});
test.afterAll(async () => {
  if (child && child.exitCode === null && child.signalCode === null) {
    const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 10000);
    child.kill('SIGTERM'); try {await closed;} finally {clearTimeout(timer);}
  }
});
test.beforeEach(async ({page}) => {
  errors = []; work = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/app.js', async route => {const response = await route.fetch(); await route.fulfill({response, body: await response.text() + observer});});
  await page.goto(origin);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck(); await settled(page);
  page.on('request', request => {if (request.method() === 'POST' && /\/api\/graph(?:$|\/(?:evaluate|ensure|surface-selection\/preview)$)/.test(request.url())) work.push(request.url());});
});
test.afterEach(() => expect(errors).toEqual([]));

async function remember(page, ids) {
  await page.evaluate(ids => {globalThis.__popupNodes = ids.map(id => document.getElementById(id));}, ids);
}
async function retained(page, ids) {
  expect(await page.evaluate(ids => ids.every((id, index) => document.getElementById(id) === globalThis.__popupNodes[index]), ids)).toBe(true);
}
async function drag(page, dx = -180, dy = 55, release = true) {
  const box = await grip(page).boundingBox(); expect(box).toBeTruthy();
  const start = {x: box.x + box.width / 2, y: box.y + box.height / 2};
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(start.x + dx, start.y + dy, {steps: 8});
  if (release) await page.mouse.up();
}
async function unchanged(page, before, requests) {
  await settled(page); const after = await read(page);
  expect(after.camera).toEqual(before.camera); expect(after.selection).toEqual(before.selection);
  expect(after.workflow).toBe(before.workflow); expect(work).toHaveLength(requests);
}
async function reachable(page, selector) {
  const control = page.locator(selector); await control.scrollIntoViewIfNeeded();
  expect(await control.evaluate(element => {const b = element.getBoundingClientRect(), x = b.x + b.width / 2, y = b.y + b.height / 2;
    return x >= 0 && x < innerWidth && y >= 0 && y < innerHeight && element.contains(document.elementFromPoint(x, y));})).toBe(true);
}

test('real grip gestures retain relationship draft and DOM; keyboard, reset and narrow viewport remain usable', async ({page}) => {
  test.setTimeout(90000);
  await page.locator('.action-select[data-action-id="baseline"]').click();
  await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
  if (!await page.locator('#relationship-dialog details').evaluate(element => element.open))
    await page.locator('#relationship-dialog details > summary').click();
  await page.locator('#new-relationship-label').fill('Retained popup draft');
  const ids = ['relationship-dialog', 'new-relationship-label', 'viewport']; await remember(page, ids); await settled(page);
  const before = await read(page), initial = await rect(page), requests = work.length;
  await drag(page); expect((await rect(page)).x).toBeLessThan(initial.x - 100);
  await unchanged(page, before, requests); await retained(page, ids);
  await expect(page.locator('#new-relationship-label')).toHaveValue('Retained popup draft');
  const released = await rect(page); await page.mouse.move(10, 10); expect((await rect(page)).x).toBe(released.x);
  for (const end of ['Escape', 'lostpointercapture', 'blur', 'pointercancel']) {
    const start = await rect(page); await drag(page, -35, 20, false);
    if (end === 'Escape') await page.keyboard.press('Escape');
    else if (end === 'blur') await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    else if (end === 'lostpointercapture') {
      await grip(page).evaluate(element => element.releasePointerCapture(1));
      // Native lostpointercapture is delivered when the next pointer event
      // processes the pending capture override, not by releasePointerCapture.
      await page.mouse.move(12, 12);
    }
    else await grip(page).dispatchEvent('pointercancel', {pointerId: 1, bubbles: true});
    await expect.poll(async () => {const current = await rect(page); return Math.max(Math.abs(current.x - start.x), Math.abs(current.y - start.y));}).toBeLessThan(1);
    expect((await read(page)).selection, 'selection before mouse release after ' + end).toEqual(before.selection);
    await page.mouse.up(); await expect(page.locator('#relationship-dialog')).toBeVisible();
    await unchanged(page, before, requests); await retained(page, ids);
  }
  await grip(page).focus(); const keyboard = await rect(page);
  await page.keyboard.press('ArrowLeft'); expect((await rect(page)).x).toBeCloseTo(keyboard.x - 10, 0);
  await page.keyboard.press('Shift+ArrowDown'); expect((await rect(page)).y).toBeCloseTo(keyboard.y + 1, 0);
  await page.locator('#reset-edit-position').click(); expect((await rect(page)).x).toBeCloseTo(initial.x, 0);
  expect((await rect(page)).y).toBeCloseTo(initial.y, 0);
  await unchanged(page, before, requests);
  page.once('dialog', dialog => dialog.dismiss()); await page.locator('#close-edit').click();
  await expect(popup(page)).toBeVisible(); await retained(page, ids);
  await page.locator('#dock-edit').click(); await expect(popup(page)).toBeHidden(); await retained(page, ids);
  const tab = page.getByRole('tab', {name: 'Edit', exact: true}), tabset = tab.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," flexlayout__tabset ")][1]');
  await tabset.getByRole('button', {name: 'Float selected tab', exact: true}).click();
  await expect(page.locator('.flexlayout__float_window').getByRole('tab', {name: 'Edit', exact: true})).toBeVisible(); await retained(page, ids);
  page.once('dialog', dialog => dialog.accept()); await tab.getByTitle('Close', {exact: true}).click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await page.getByRole('button', {name: 'Relationship…', exact: true}).click(); await expect(popup(page)).toBeVisible();
  await page.setViewportSize({width: 320, height: 300}); await settled(page);
  for (const selector of ['#move-edit', '#reset-edit-position', '#dock-edit', '#close-edit']) await reachable(page, selector);
  if (!await page.locator('#relationship-dialog details').evaluate(element => element.open))
    await page.locator('#relationship-dialog details > summary').click();
  await page.locator('#new-relationship-label').fill('Narrow retained draft');
  await reachable(page, '#new-relationship-label'); await reachable(page, '#add-relationship');
  expect(work).toHaveLength(requests);
});

test('Build faces moves without recreating its controls, changing the selected surface or starting work', async ({page}) => {
  await page.getByRole('button', {name: 'Build faces…', exact: true}).click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  await page.locator('#build-faces-options > summary').click(); await page.locator('#build-faces-label').fill('Retained face panel');
  const ids = ['build-faces-dialog', 'build-faces-label', 'build-faces-target']; await remember(page, ids); await settled(page);
  const before = await read(page), requests = work.length, target = await page.locator('#build-faces-target').textContent();
  await drag(page); await unchanged(page, before, requests); await retained(page, ids);
  await expect(page.locator('#build-faces-label')).toHaveValue('Retained face panel');
  await expect(page.locator('#build-faces-target')).toHaveText(target);
  page.once('dialog', dialog => dialog.dismiss()); await page.locator('#close-edit').click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
});

test('moving Surface selection preserves reviewed preview and in-flight native preview ownership', async ({page}) => {
  const original = await graph(page);
  await page.getByRole('button', {name: 'Surface selection', exact: true}).click();
  await page.locator('#new-surface-selection-seed').selectOption('seeds');
  await page.locator('#new-surface-selection-preview').click();
  await expect(page.locator('#create-surface-selection')).toBeEnabled();
  const ids = ['surface-selection-dialog', 'new-surface-selection-seed', 'new-surface-selection-angle']; await remember(page, ids); await settled(page);
  const before = await read(page), requests = work.length; expect(before.preview.length).toBeGreaterThan(0);
  await drag(page); await unchanged(page, before, requests); await retained(page, ids);
  expect((await read(page)).preview).toEqual(before.preview); await expect(page.locator('#create-surface-selection')).toBeEnabled();
  await gate(page, 'arm'); await page.locator('#new-surface-selection-preview').click();
  try {
    await expect.poll(async () => (await (await page.request.get(origin + '/__test/surface-preview')).json()).entered).toBe(true);
    const active = await graph(page), moving = await read(page), started = work.length;
    expect(active.evaluation.state).toBe('running');
    await drag(page, 45, 25); await unchanged(page, moving, started); await retained(page, ids);
    const after = await graph(page); expect(after.evaluation.id).toBe(active.evaluation.id);
    expect(after.recipe).toEqual(original.recipe); expect(after.results).toEqual(original.results);
  } finally {await gate(page, 'release');}
  await expect(page.locator('#create-surface-selection')).toBeEnabled();
  await expect(page.locator('#new-surface-selection-seed')).toHaveValue('seeds');
});
