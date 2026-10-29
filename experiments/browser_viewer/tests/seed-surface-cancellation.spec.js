import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

test.use({ actionTimeout: 8000 });
const root = fileURLToPath(new URL('../../../', import.meta.url));
let child, nativeURL, stderr = '';
const row = (page, id) => page.locator(`#action-list .action-select[data-action-id="${id}"]`);
const graph = async page => (await page.request.get(`${nativeURL}/api/graph`)).json();
const gates = async page => (await page.request.get(`${nativeURL}/__test/surface-preview`)).json();
async function control(page, action) {
  const response = await page.request.post(`${nativeURL}/__test/surface-preview`, { data: { action } });
  expect(response.ok(), await response.text()).toBe(true);
}

test.beforeAll(async () => {
  child = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.native_surface_preview_server'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}:${root}/src`, OPENBLAS_NUM_THREADS: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stderr.on('data', data => { stderr += data.toString(); });
  nativeURL = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(`Native preview startup timed out: ${stderr}`)), 20000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Native preview exited ${code}: ${stderr}`)); });
    child.stdout.on('data', data => {
      output += data.toString();
      for (const line of output.split('\n')) if (line.startsWith('{"url":')) {
        clearTimeout(timer); resolve(JSON.parse(line).url);
      }
    });
  });
});

test.afterEach(async ({ page }) => {
  if (!nativeURL) return;
  await control(page, 'release');
  await expect.poll(async () => (await graph(page)).evaluation_running).toBe(false);
});
test.afterAll(async () => {
  if (!child || child.exitCode !== null) return;
  await fetch(`${nativeURL}/__test/surface-preview`, {
    method: 'POST', body: JSON.stringify({ action: 'release' }),
  }).catch(() => {});
  const exit = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  let timer;
  const code = await Promise.race([exit, new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 15000); })]);
  clearTimeout(timer);
  if (code === 'timeout') child.kill('SIGKILL');
  expect(code, `Native preview fixture did not retire: ${stderr}`).toBe(0);
});

async function editSurface(page, { residuals = false } = {}) {
  await page.goto(nativeURL);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate all', exact: true })).toBeEnabled();
  await expect.poll(async () => (await graph(page)).evaluation_running).toBe(false);
  const before = await graph(page);
  expect(before.states.baseline).toBe('ready');
  expect(before.results.baseline.ids).toHaveLength(81);
  if (residuals) {
    await row(page, 'baseline').click();
    await page.locator('details.display-options > summary').click();
    await page.locator('#colors').selectOption('residual');
    await expect(page.locator('#residual-scale')).toBeVisible();
  }
  await row(page, 'surface').click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await expect(page.locator('#surface-selection-properties')).toBeVisible();
  return before;
}

async function startGatedPreview(page, angle = '25', setback = '4') {
  await page.locator('#surface-selection-angle').fill(angle);
  await page.locator('#surface-selection-setback').fill(setback);
  await control(page, 'arm');
  await page.locator('#surface-selection-preview').click();
  await expect.poll(async () => (await gates(page)).entered).toBe(true);
  const active = await graph(page);
  expect(active.evaluation.kind).toBe('preview');
  expect(active.evaluation_running).toBe(true);
  await expect(page.locator('#apply-properties')).toBeDisabled();
  return active.evaluation;
}

function expectRetained(before, after) {
  expect(after.recipe).toEqual(before.recipe);
  expect(after.results).toEqual(before.results);
  expect(after.states.baseline).toBe('ready');
  expect(after.recipe.nodes.find(node => node.operation === 'source'))
    .toEqual(before.recipe.nodes.find(node => node.operation === 'source'));
}

test('footer Cancel retires a private preview while retaining source, previous fits and form drafts', async ({ page }) => {
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const before = await editSurface(page, { residuals: true });
  page.on('request', request => {
    if (request.url() === `${nativeURL}/api/graph` && request.method() === 'POST') writes.push(request.postDataJSON());
  });
  await startGatedPreview(page);
  const cancel = page.locator('#cancel-computation');
  await expect(cancel).toBeEnabled();
  expect(await cancel.evaluate(button => button.closest('[inert]'))).toBeNull();
  await expect(page.locator('#force-restart')).toBeHidden();
  const reply = page.waitForResponse(response => response.url() === `${nativeURL}/api/graph/cancel`);
  const started = Date.now();
  await cancel.click();
  expect((await reply).ok()).toBe(true);
  const latency = Date.now() - started;
  expect(latency, 'Private preview Cancel returns control before the gated safe boundary').toBeLessThan(2000);
  console.info(`Private native preview Cancel returned control in ${latency} ms`);
  await expect(page.locator('#runtime-detail')).toContainText('Waiting for native work');
  await expect(page.locator('#surface-selection-angle')).toHaveValue('25');
  await expect(page.locator('#surface-selection-setback')).toHaveValue('4');
  await expect(page.locator('#residual-scale')).toBeVisible();
  await expect(page.locator('#apply-properties')).toBeDisabled();
  const waiting = await graph(page);
  expect(waiting.evaluation.state).toBe('cancel-requested');
  expect(waiting.evaluation_running).toBe(true);
  expectRetained(before, waiting);
  await control(page, 'release');
  await expect.poll(async () => (await graph(page)).evaluation.state).toBe('cancelled');
  await expect(page.locator('#status')).toContainText('Preview cancelled. Accepted actions and results retained.');
  await expect(page.locator('#apply-properties')).toBeEnabled();
  await expect(page.locator('#surface-selection-angle')).toHaveValue('25');
  await expect(page.locator('#surface-selection-setback')).toHaveValue('4');
  await expect(page.getByRole('checkbox', { name: 'Auto', exact: true })).not.toBeChecked();
  expectRetained(before, await graph(page));
  // An unchanged accepted definition is allowed, but this changed, unreviewed
  // draft must not pass the real Apply handler after cancellation.
  await page.locator('#apply-properties').click();
  await expect(page.locator('#status')).toContainText('Preview the current seed');
  expect(writes).toEqual([]);
  await page.locator('#surface-selection-preview').click();
  await expect(page.locator('#surface-selection-preview-status')).toContainText('selected');
  expect(JSON.parse(await page.locator('#surface-selection-diagnostics').textContent()).angle_degrees).toBe(25);
  expectRetained(before, await graph(page));
  expect(errors).toEqual([]);
});

test('editing preview inputs aborts its lease and waits before starting only the newest queued preview', async ({ page }) => {
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const before = await editSurface(page);
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  page.on('request', request => {
    if (request.url() === `${nativeURL}/api/graph` && request.method() === 'POST') writes.push(request.postDataJSON());
  });
  const first = await startGatedPreview(page);
  await expect(page.locator('#surface-selection-angle')).toBeEnabled();
  await page.locator('#surface-selection-angle').fill('31');
  await page.locator('#surface-selection-setback').fill('8');
  await page.locator('#surface-selection-angle').fill('32');
  await expect.poll(async () => (await graph(page)).evaluation.state).toBe('cancel-requested');
  await expect(page.locator('#apply-properties')).toBeDisabled();
  // Cross the 400 ms form debounce while the native safe boundary is gated.
  // The replacement request must remain queued until the first lease retires.
  await page.waitForTimeout(650);
  const held = await gates(page);
  expect(held.requests).toHaveLength(1);
  expect((await graph(page)).evaluation.id).toBe(first.id);
  await page.locator('#action-properties').evaluate(form => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(writes).toEqual([]);
  expectRetained(before, await graph(page));
  await control(page, 'release');
  await expect.poll(async () => (await gates(page)).requests.length).toBe(2);
  await expect(page.locator('#surface-selection-preview-status')).toContainText('selected');
  const requests = (await gates(page)).requests;
  expect(requests.map(request => [request.angle_degrees, request.setback])).toEqual([[25, 4], [32, 8]]);
  const diagnostic = JSON.parse(await page.locator('#surface-selection-diagnostics').textContent());
  expect(diagnostic.angle_degrees).toBe(32);
  expect(diagnostic.setback).toBe(8);
  expect((await graph(page)).evaluation.id).not.toBe(first.id);
  expectRetained(before, await graph(page));
  const invalid = await page.locator('#action-properties').evaluate(form =>
    [...form.elements].filter(control => control.willValidate && !control.checkValidity())
      .map(control => ({ id: control.id, message: control.validationMessage, disabled: control.disabled })));
  expect(invalid, 'The reviewed surface-selection form must be submittable').toEqual([]);
  // Blurring an already reviewed numeric field must not invalidate its
  // unchanged definition before the real form submission runs.
  await expect(page.locator('#surface-selection-angle')).toBeFocused();
  const saved = page.waitForResponse(response => response.url() === `${nativeURL}/api/graph` && response.request().method() === 'POST');
  await page.locator('#apply-properties').click();
  expect((await saved).ok()).toBe(true);
  const after = await graph(page), selected = after.recipe.nodes.find(node => node.id === 'surface');
  expect([selected.angle_degrees, selected.setback]).toEqual([32, 8]);
  expect(after.results.baseline).toEqual(before.results.baseline);
  expect(writes).toHaveLength(1);
  expect(errors).toEqual([]);
});
