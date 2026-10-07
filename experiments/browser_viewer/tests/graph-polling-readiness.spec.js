import { test, expect } from '@playwright/test';

test('Build faces readiness unlocks editing after compact completion and a failed final check', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  await expect(page.locator('#startup-error')).toBeHidden();
  const graph = await (await page.request.get('/api/graph')).json();
  // The readiness consumer uses batch inputs, independently of neighbor discovery.
  await page.route('**/api/graph/build-faces/candidates', route => route.fulfill({
    json: { token: graph.token, candidates: [] },
  }));
  await page.locator('#action-list .action-select').filter({ hasText: 'End surface' }).click();
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await page.locator('#build-faces-options > summary').click();
  await page.locator('#build-faces-mode').selectOption('batch');
  const selected = await page.locator('#build-faces-surfaces option').evaluateAll(options =>
    options.slice(0, 2).map(option => option.value));
  expect(selected).toHaveLength(2);
  await page.locator('#build-faces-surfaces').selectOption(selected);
  await page.locator('#build-faces-label').fill('Retained batch draft');
  await expect(page.locator('#build-faces-surfaces')).toBeEnabled();

  let running = false, published = false, unchangedPolls = 0, ensureRequests = 0;
  await page.route(/\/api\/graph(?:\?.*)?$/, route => {
    if (route.request().method() !== 'GET') return route.continue();
    if (!published) {
      published = true;
      graph.revision++;
      return route.fulfill({ json: { ...graph, evaluation_running: running } });
    }
    expect(new URL(route.request().url()).searchParams.get('revision')).toBe(String(graph.revision));
    unchangedPolls++;
    return route.fulfill({ json: { token: graph.token, revision: graph.revision,
      unchanged: true, evaluation_running: running } });
  });
  await page.route('**/api/graph/ensure', route => {
    ensureRequests++;
    if (ensureRequests === 1) {
      running = true;
      return route.fulfill({ status: 202, json: { evaluation_running: true } });
    }
    return route.fulfill({ status: 503, json: { error: 'Final readiness request failed' } });
  });

  await page.locator('#preview-build-faces').click();
  await expect.poll(() => published).toBe(true);
  await expect(page.locator('#build-faces-surfaces')).toBeDisabled();
  const grip = page.locator('#action-list .action-row > .action-grip:not(.action-grip-placeholder)').first();
  await expect(grip).toBeDisabled();
  await expect(grip).toHaveAttribute('draggable', 'false');
  // Preview readiness does not enter the explicit evaluator's busy/finally path.
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.evaluate(() => {
    window.originalReadinessOption = document.querySelector('#build-faces-surfaces option');
    window.originalReadinessGrip = document.querySelector('#action-list .action-row > .action-grip:not(.action-grip-placeholder)');
  });
  const capturedPolls = unchangedPolls;
  await expect.poll(() => unchangedPolls).toBeGreaterThanOrEqual(capturedPolls + 3);
  expect(await page.evaluate(() => window.originalReadinessOption ===
    document.querySelector('#build-faces-surfaces option'))).toBe(true);
  await expect(page.locator('#build-faces-label')).toHaveValue('Retained batch draft');

  running = false;
  await expect(page.locator('#build-faces-error')).toContainText('Final readiness request failed');
  await expect(page.locator('#build-faces-surfaces')).toBeEnabled();
  await expect(page.locator('#preview-build-faces')).toBeEnabled();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(grip).toBeEnabled();
  await expect(grip).toHaveAttribute('draggable', 'true');
  expect(await page.evaluate(() => window.originalReadinessGrip ===
    document.querySelector('#action-list .action-row > .action-grip:not(.action-grip-placeholder)'))).toBe(true);
  expect(ensureRequests).toBe(2);
  expect(await page.locator('#build-faces-surfaces').evaluate(select =>
    [...select.selectedOptions].map(option => option.value))).toEqual(selected);
  expect(errors).toEqual([]);
});
