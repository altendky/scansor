import { test, expect } from '@playwright/test';

test('startup recipe recovery uses the backend when the application module fails', async ({ page }) => {
  const expected = (await (await page.request.get('/api/graph')).json()).recipe;
  await page.route('**/app.js', route => route.abort());
  await page.goto('/');
  await expect(page.locator('#startup-error')).toBeVisible();
  const download = page.waitForEvent('download');
  await page.locator('#recover-actions').click();
  const file = await download;
  expect(file.suggestedFilename()).toBe('nozzle-actions-recovery.json');
  const stream = await file.createReadStream();
  let content = '';
  for await (const chunk of stream) content += chunk.toString();
  expect(JSON.parse(content)).toEqual(expected);
  await expect(page.locator('#startup-detail')).toContainText('Actions downloaded');
});

test('an unavailable selected backend fails without HTTP fallback during startup or recovery', async ({ page }) => {
  let requests = 0;
  await page.route('**/api/**', route => { requests++; return route.abort(); });
  await page.goto('/?backend=unavailable');
  await expect(page.locator('#startup-error')).toBeVisible();
  await expect(page.locator('#startup-detail')).toContainText('Backend "unavailable" is unavailable');
  await page.locator('#recover-actions').click();
  await expect(page.locator('#startup-detail')).toContainText('Backend "unavailable" is unavailable');
  expect(requests).toBe(0);
});

test('native startup does not require the worker-only module', async ({ page }) => {
  let workerRequests = 0;
  await page.route('**/worker-backend.js', route => { workerRequests++; return route.abort(); });
  await page.goto('/');
  await expect(page.locator('[data-command-id="new-fit"]')).toBeEnabled();
  await expect(page.locator('#startup-error')).toBeHidden();
  expect(workerRequests).toBe(0);
});

test('configuration fetch failure displays a startup error without computation fallback', async ({ page }) => {
  let requests = 0;
  await page.route('**/backend-config.js', route => route.abort());
  await page.route('**/api/**', route => { requests++; return route.abort(); });
  await page.goto('/');
  await expect(page.locator('#startup-error')).toBeVisible();
  await expect(page.locator('#startup-detail')).not.toBeEmpty();
  await expect(page.locator('#recover-actions')).toBeDisabled();
  expect(requests).toBe(0);
});

test('recipe loading stays disabled until delayed workspace startup installs its handler', async ({ page }) => {
  let release, requested;
  const gate = new Promise(resolve => { release = resolve; }),
    entered = new Promise(resolve => { requested = resolve; });
  await page.route('**/api/meta', async route => {
    requested();
    await gate;
    await route.continue();
  });
  try {
    await page.goto('/');
    await entered;
    await expect(page.getByRole('button', { name: 'Load actions', exact: true })).toBeDisabled();
    release();
    await expect(page.getByRole('button', { name: 'Load actions', exact: true })).toBeEnabled();
    await expect.poll(async () => (await (await page.request.get('/api/graph')).json()).evaluation_running).toBe(false);
    const recipe = (await (await page.request.get('/api/graph')).json()).recipe;
    page.on('dialog', dialog => dialog.accept());
    const imported = page.waitForResponse(response => response.url().endsWith('/api/actions/open') &&
      response.request().method() === 'POST');
    await page.locator('#file').setInputFiles({ name: 'actions.json', mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(recipe)) });
    const response = await imported;
    expect(response.ok(), await response.text()).toBe(true);
    const operation = await response.json();
    await expect.poll(async () => (await (await page.request.get(`/api/sources/imports/${operation.operationId}`)).json()).status).toBe('accepted');
    await expect(page.locator('#source-progress')).toBeHidden();
  } finally { release(); }
});
