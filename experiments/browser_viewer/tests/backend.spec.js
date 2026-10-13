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
