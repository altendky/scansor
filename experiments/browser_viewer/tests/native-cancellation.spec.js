import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

test.use({ actionTimeout: 8000 });
const root = fileURLToPath(new URL('../../../', import.meta.url));
let child, nativeURL, stderr = '';
const graph = async page => (await page.request.get(`${nativeURL}/api/graph`)).json();
const row = (page, id) => page.locator(`#action-list .action-select[data-action-id="${id}"]`);
const evaluateAll = page => page.getByRole('button', { name: 'Evaluate all', exact: true });
const auto = page => page.getByRole('checkbox', { name: 'Auto', exact: true });
async function control(page, mode) {
  const response = await page.request.post(`${nativeURL}/__test/cancellation`, { data: { mode } });
  expect(response.ok(), await response.text()).toBe(true);
}

test.beforeAll(async () => {
  child = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.native_cancellation_server'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}:${root}/src`, OPENBLAS_NUM_THREADS: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stderr.on('data', data => { stderr += data.toString(); });
  nativeURL = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error(`Native fixture startup timed out: ${stderr}`)), 20000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Native fixture exited ${code}: ${stderr}`)); });
    child.stdout.on('data', data => {
      output += data.toString();
      for (const line of output.split('\n')) {
        if (!line.startsWith('{"url":')) continue;
        clearTimeout(timer);
        resolve(JSON.parse(line).url);
      }
    });
  });
});

test.afterEach(async ({ page }) => { if (nativeURL) await control(page, 'release'); });
test.afterAll(async () => {
  if (!child || child.exitCode !== null) return;
  await fetch(`${nativeURL}/__test/cancellation`, {
    method: 'POST', body: JSON.stringify({ mode: 'release' }),
  }).catch(() => {});
  const exit = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  let timer;
  const code = await Promise.race([exit, new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 15000); })]);
  clearTimeout(timer);
  if (code === 'timeout') child.kill('SIGKILL');
  expect(code, `Native fixture failed to retire: ${stderr}`).toBe(0);
});

for (const mode of ['cooperative', 'noncooperative', 'queued']) {
  test(`native ${mode} Cancel preserves drafts and source, discards display, and allows reevaluation`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(nativeURL);
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await expect(evaluateAll(page)).toBeEnabled();
    await expect(page.locator('#action-list .state-running')).toHaveCount(0);
    await expect.poll(async () => (await graph(page)).evaluation_running).toBe(false);
    const before = await graph(page);
    expect(before.states.end).toBe('ready');
    const source = before.recipe.nodes.find(node => node.operation === 'source').source_sha256;
    expect(source).toMatch(/^[0-9a-f]{64}$/);
    await row(page, 'end').click();
    await page.locator('details.display-options > summary').click();
    await page.locator('#colors').selectOption('residual');
    await expect(page.locator('#residual-scale')).toBeVisible();
    await expect(page.locator('#metrics dd')).not.toHaveCount(0);
    await row(page, 'end').click({ button: 'right' });
    await page.locator('#feature-context-edit').click();
    await page.locator('#axial-start').fill('-1');
    // Auto starts on. Cancel must switch it off without discarding this edit.
    await expect(auto(page)).toBeChecked();
    await control(page, mode);
    await evaluateAll(page).click();
    await expect.poll(async () => (await (await page.request.get(`${nativeURL}/__test/cancellation`)).json()).entered).toBe(true);
    const cancel = page.locator('#cancel-computation');
    await expect(cancel).toBeEnabled();
    expect(await cancel.evaluate(button => button.closest('[inert]'))).toBeNull();
    await expect(page.locator('#force-restart')).toBeHidden();
    const reply = page.waitForResponse(response => response.url() === `${nativeURL}/api/graph/cancel`);
    const requestedAt = Date.now();
    await cancel.click();
    const stoppedReply = await reply;
    const latency = Date.now() - requestedAt;
    expect(latency, 'Cancel request returns control while native work is gated').toBeLessThan(2000);
    test.info().annotations.push({ type: 'cancel-control-latency-ms', description: `${mode}: ${latency}` });
    console.info(`Native ${mode} Cancel returned control in ${latency} ms`);
    expect(stoppedReply.ok(), await stoppedReply.text()).toBe(true);
    await expect(auto(page)).not.toBeChecked();
    await expect(page.locator('#residual-scale')).toBeHidden();
    await expect(page.locator('#metrics dd')).toHaveCount(0);
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    const stopped = await graph(page);
    expect(stopped.recipe).toEqual(before.recipe);
    expect(stopped.results).toEqual({});
    if (mode === 'noncooperative') {
      expect(stopped.evaluation.state).toBe('cancel-requested');
      expect(stopped.evaluation_running).toBe(true);
      await expect(page.locator('#runtime-detail')).toContainText('Waiting for native work');
      await expect(evaluateAll(page)).toBeDisabled();
      // An independent request stays responsive while the coordinator is blocked.
      expect((await page.request.get(`${nativeURL}/api/meta`)).ok()).toBe(true);
    }
    await control(page, 'release');
    await expect(page.locator('#runtime-detail')).toContainText('Cancelled. Derived results discarded.');
    await expect(evaluateAll(page)).toBeEnabled();
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    expect((await graph(page)).results).toEqual({});
    expect((await graph(page)).recipe.nodes.find(node => node.operation === 'source').source_sha256).toBe(source);
    // Apply the retained draft, then produce fresh geometry through the real UI.
    const saved = page.waitForResponse(response => response.url() === `${nativeURL}/api/graph` && response.request().method() === 'POST');
    await page.locator('#apply-properties').click();
    expect((await saved).ok()).toBe(true);
    await evaluateAll(page).click();
    await expect(evaluateAll(page)).toBeEnabled();
    await expect.poll(async () => (await graph(page)).states.end).toBe('ready');
    expect((await graph(page)).recipe.nodes.find(node => node.id === 'end').axial_domain[0]).toBe(-1);
    expect(errors).toEqual([]);
  });
}

test('native Cancel fences the application default ensureCurrent consumer', async ({ page }) => {
  // Expose the existing consumer only in the test-delivered module; production
  // keeps its private API. No backend requests are mocked or fulfilled here.
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: `${await response.text()}\nexport { ensureCurrent };\n` });
  });
  await page.goto(nativeURL);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(evaluateAll(page)).toBeEnabled();
  await auto(page).uncheck();
  await row(page, 'end').click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await page.locator('#axial-start').fill('-2');
  const saved = page.waitForResponse(response => response.url() === `${nativeURL}/api/graph` && response.request().method() === 'POST');
  await page.locator('#apply-properties').click();
  expect((await saved).ok()).toBe(true);
  expect((await graph(page)).states.end).not.toBe('ready');
  await control(page, 'noncooperative');
  await page.evaluate(async () => {
    const { ensureCurrent } = await import('/app.js');
    window.nativeEnsureOutcome = 'pending';
    void ensureCurrent(['end']).then(() => { window.nativeEnsureOutcome = 'accepted'; },
      error => { window.nativeEnsureOutcome = `rejected: ${error.message}`; });
  });
  await expect.poll(async () => (await (await page.request.get(`${nativeURL}/__test/cancellation`)).json()).entered).toBe(true);
  expect((await graph(page)).evaluation.kind).toBe('ensure');
  await expect(page.locator('#cancel-computation')).toBeEnabled();
  await page.locator('#cancel-computation').click();
  await expect(page.locator('#runtime-detail')).toContainText('Waiting for native work');
  await control(page, 'release');
  await expect(page.locator('#runtime-detail')).toContainText('Cancelled. Derived results discarded.');
  await expect.poll(() => page.evaluate(() => window.nativeEnsureOutcome)).toMatch(/^rejected:/);
  expect((await graph(page)).results).toEqual({});
  await expect(page.locator('#metrics dd')).toHaveCount(0);
  await expect(auto(page)).not.toBeChecked();
});
