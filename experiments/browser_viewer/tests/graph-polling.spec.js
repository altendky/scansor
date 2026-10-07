import { test, expect } from '@playwright/test';

test('unchanged running polls preserve an active parameter draft and its controls', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  const graph = await (await page.request.get('/api/graph')).json();
  let running = false, unchangedPolls = 0;
  await page.locator('#action-list .action-select[data-action-id="end"]').click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await page.locator('#axial-start').fill('-1');
  await page.locator('#axial-start').evaluate(input => { window.originalParameterDraft = input; });
  await page.route(/\/api\/graph(?:\?.*)?$/, route => {
    unchangedPolls++;
    return route.fulfill({ json: { token: graph.token, revision: graph.revision,
      unchanged: true, evaluation_running: running } });
  });
  await page.route('**/api/graph/evaluate', route => {
    running = true;
    return route.fulfill({ json: { status: 'running' } });
  });
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: { ...graph, evaluation_running: false } }));
  await page.locator('#evaluate-all').evaluate(button => button.click());
  await expect.poll(() => unchangedPolls).toBeGreaterThanOrEqual(4);
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  expect(await page.locator('#axial-start').evaluate(input => input === window.originalParameterDraft)).toBe(true);
  running = false;
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#axial-start')).toHaveValue('-1');
});

test('unchanged running polls preserve UI controls and drafts while same-token publications remain incremental', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  let graph = await (await page.request.get('/api/graph')).json(), running = false;
  const responses = [];
  await page.route(/\/api\/graph(?:\?.*)?$/, async route => {
    const revision = new URL(route.request().url()).searchParams.get('revision');
    const body = Number(revision) === graph.revision && revision !== null
      ? { token: graph.token, revision: graph.revision, unchanged: true, evaluation_running: running }
      : { ...graph, evaluation_running: running };
    responses.push(body);
    await route.fulfill({ json: body });
  });
  await page.route('**/api/graph/evaluate', async route => {
    running = true;
    await route.fulfill({ json: { status: 'running' } });
  });
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: { ...graph, evaluation_running: false } }));
  await page.locator('#action-list .action-select').filter({ hasText: 'End surface' }).click();
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await page.locator('#build-faces-options > summary').click();
  await page.locator('#build-faces-label').fill('Unsaved face draft');
  await page.locator('#build-faces-label').focus();
  await page.evaluate(() => {
    window.originalFaceChoice = document.querySelector('#build-faces-target-options [role=menuitemradio]');
    window.originalCanvas = document.querySelector('#viewport canvas');
  });
  await page.locator('#evaluate-all').evaluate(button => button.click());
  await expect.poll(() => responses.filter(body => body.unchanged && body.evaluation_running).length).toBeGreaterThanOrEqual(1);
  // Entering evaluation updates the tree's editing lock once, before polling.
  await page.evaluate(() => { window.originalTreeItem = document.querySelector('#action-list .action-select'); });
  await expect.poll(() => responses.filter(body => body.unchanged && body.evaluation_running).length).toBeGreaterThanOrEqual(4);
  expect(responses.every(body => body.unchanged)).toBe(true);
  await expect(page.locator('#build-faces-label')).toHaveValue('Unsaved face draft');
  await expect(page.locator('#build-faces-label')).toBeFocused();
  expect(await page.evaluate(() => window.originalTreeItem === document.querySelector('#action-list .action-select') &&
    window.originalFaceChoice === document.querySelector('#build-faces-target-options [role=menuitemradio]') &&
    window.originalCanvas === document.querySelector('#viewport canvas'))).toBe(true);

  graph = structuredClone(graph);
  graph.revision++;
  graph.states.end = 'failed';
  graph.errors.end = 'Incremental test failure';
  await expect(page.locator('#action-list .action-select').filter({ hasText: 'End surface' }))
    .toHaveAttribute('aria-label', /Failed.*Incremental test failure/);
  expect(running).toBe(true);
  expect(responses.some(body => body.recipe && body.revision === graph.revision)).toBe(true);
  await expect(page.locator('#build-faces-error')).toContainText('inputs unavailable');
  const completed = page.waitForResponse('**/api/graph/ensure');
  running = false;
  await completed;
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#status')).toContainText('Incremental test failure');
  expect(errors).toEqual([]);
});

test('compact completion unlocks editing when the final readiness request fails', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  const graph = await (await page.request.get('/api/graph')).json();
  let running = false, published = false;
  await page.route(/\/api\/graph(?:\?.*)?$/, route => {
    if (running && !published) {
      published = true;
      graph.revision++;
      return route.fulfill({ json: { ...graph, evaluation_running: true } });
    }
    return route.fulfill({ json: { token: graph.token, revision: graph.revision,
      unchanged: true, evaluation_running: running } });
  });
  await page.route('**/api/graph/evaluate', route => {
    running = true;
    return route.fulfill({ json: { status: 'running' } });
  });
  await page.route('**/api/graph/ensure', route => route.fulfill({ status: 503,
    json: { error: 'Final readiness request failed' } }));
  await page.locator('#action-list .action-select[data-action-id="end"]').click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await page.locator('#axial-start').fill('-1');
  await page.locator('#evaluate-all').evaluate(button => button.click());
  await expect.poll(() => published).toBe(true);
  await expect(page.locator('#axial-start')).toBeDisabled();
  running = false;
  await expect(page.locator('#status')).toContainText('Final readiness request failed');
  await expect(page.locator('#axial-start')).toBeEnabled();
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
});
