import { test, expect } from '@playwright/test';

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  await expect(page.locator('#startup-error')).toBeHidden();
}

async function evaluate(page) {
  // Keep focus in the draft while delivering snapshots through the real handler.
  await page.locator('#evaluate-all').evaluate(button => button.click());
  await expect(page.locator('#evaluate-all')).toBeEnabled();
}

async function lists(page, label, present) {
  const target = page.locator('#build-faces-target-options [role=menuitemradio]').filter({ hasText: label });
  const batch = page.locator('#build-faces-surfaces option').filter({ hasText: label });
  await expect(target).toHaveCount(present ? 1 : 0);
  await expect(batch).toHaveCount(present ? 1 : 0);
}

test('open Build faces lists follow evaluation, rename and delete without losing drafts', async ({ page }) => {
  await ready(page);
  const original = await (await page.request.get('/api/graph')).json();
  try {
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    // Creation shares the Edit host, so create the unevaluated input first.
    await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
    await page.locator('#new-fit-label').fill('Additional plane');
    await page.locator('#new-fit-kind').selectOption('plane');
    await page.locator('#new-fit-inputs').selectOption('top_face');
    await page.getByRole('button', { name: 'Show Reference geometry choices', exact: true }).click();
    await page.getByRole('menuitemradio', { name: 'None (standalone)', exact: true }).click();
    await page.locator('#add-fit-form').getByRole('button', { name: 'Add fit', exact: true }).click();
    await expect(page.locator('#fit-dialog')).toBeHidden();
    const additional = page.locator('#action-list .action-select').filter({ hasText: 'Additional plane' });
    await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
    await page.locator('#build-faces-options > summary').click();
    await page.locator('#build-faces-label').fill('Uncommitted faces');
    await page.getByRole('button', { name: 'Show Neighbors choices', exact: true }).click();
    await page.locator('#build-faces-neighbor-filter').fill('My neighbor filter');
    const target = await page.locator('#build-faces-target').textContent();
    await lists(page, 'Additional plane', false);
    await evaluate(page);
    await lists(page, 'Additional plane', true);
    await expect(page.locator('#build-faces-label')).toHaveValue('Uncommitted faces');
    await expect(page.locator('#build-faces-neighbor-filter')).toHaveValue('My neighbor filter');
    await expect(page.locator('#build-faces-target')).toHaveText(target);

    await page.locator('#build-faces-mode').selectOption('batch');
    await page.locator('#build-faces-label').fill('Uncommitted faces');
    const reference = await page.locator('#build-faces-surfaces option').filter({ hasText: 'Additional plane' }).getAttribute('value');
    await page.locator('#build-faces-surfaces').selectOption(reference);
    await page.evaluate(() => { window.originalScope = document.querySelector('#build-faces-scopes select'); });
    await evaluate(page);
    expect(await page.evaluate(() => window.originalScope === document.querySelector('#build-faces-scopes select'))).toBe(true);
    const additionalId = await additional.getAttribute('data-action-id');
    await additional.click({ button: 'right' });
    await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Renamed plane');
    await page.locator('#rename-feature-form').getByRole('button', { name: 'Rename', exact: true }).click();
    await expect(page.locator('#rename-feature-dialog')).toBeHidden();
    await evaluate(page);
    await lists(page, 'Additional plane', false);
    await lists(page, 'Renamed plane', true);
    expect(await page.locator('#build-faces-surfaces').evaluate(select => [...select.selectedOptions].map(option => option.value))).toEqual([reference]);
    await expect(page.locator('#build-faces-label')).toHaveValue('Uncommitted faces');

    await page.locator(`#action-list .action-select[data-action-id="${additionalId}"]`).click({ button: 'right' });
    await page.locator('#delete-selected-features').click();
    await page.locator('#confirm-delete-features').click();
    await expect(page.locator('#delete-features-dialog')).toBeHidden();
    await lists(page, 'Renamed plane', false);
    await expect(page.locator('#build-faces-error')).toContainText('Selected inputs unavailable');
    await page.locator('#preview-build-faces').click();
    await expect(page.locator('#build-faces-error')).toContainText('Restore or update these inputs');
    await expect(page.locator('#apply-build-faces')).toBeDisabled();
    await expect(page.locator('#build-faces-dialog')).toBeVisible();
  } finally {
    const current = await (await page.request.get('/api/graph')).json();
    const restored = await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe: original.recipe },
    });
    expect(restored.ok()).toBe(true);
  }
});

test('readiness refresh retains exact unavailable inputs and unchanged snapshots retain focus and controls', async ({ page }) => {
  await ready(page);
  let graph = await (await page.request.get('/api/graph')).json();
  const readyGraph = structuredClone(graph);
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await page.getByRole('button', { name: 'Show Surface choices', exact: true }).click();
  await page.locator('#build-faces-target-options [role=menuitemradio]').filter({ hasText: 'End surface' }).click();
  await page.locator('#build-faces-options > summary').click();
  await page.locator('#build-faces-label').fill('Retained draft');
  await page.getByRole('button', { name: 'Show Neighbors choices', exact: true }).click();
  await page.locator('#build-faces-neighbor-filter').fill('Retained filter');
  await page.locator('#build-faces-label').focus();
  await page.evaluate(() => {
    window.originalFaceOption = document.querySelector('#build-faces-target-options [role=menuitemradio]');
    window.originalBatchOption = document.querySelector('#build-faces-surfaces option');
  });
  await evaluate(page);
  await expect(page.locator('#build-faces-label')).toBeFocused();
  expect(await page.evaluate(() => window.originalFaceOption === document.querySelector('#build-faces-target-options [role=menuitemradio]') &&
    window.originalBatchOption === document.querySelector('#build-faces-surfaces option'))).toBe(true);
  await page.getByRole('button', { name: 'Show Surface choices', exact: true }).click();
  await evaluate(page);
  await expect(page.locator('#build-faces-target-options')).toBeVisible();
  await page.keyboard.press('Escape');

  for (const state of ['stale', 'failed', 'blocked']) {
    graph = structuredClone(readyGraph);
    graph.states.fit = state;
    await evaluate(page);
    await lists(page, 'End surface', false);
    await lists(page, 'Outer surface', false);
    await expect(page.locator('#build-faces-target')).toContainText('Unavailable: End surface');
    await expect(page.locator('#build-faces-error')).toContainText('Selected inputs unavailable');
    await expect(page.locator('#focus-face-target')).toBeDisabled();
    await page.locator('#preview-build-faces').click();
    await expect(page.locator('#apply-build-faces')).toBeDisabled();
    await expect(page.locator('#build-faces-label')).toHaveValue('Retained draft');
    await expect(page.locator('#build-faces-neighbor-filter')).toHaveValue('Retained filter');
  }
  graph = structuredClone(readyGraph);
  await evaluate(page);
  await lists(page, 'End surface', true);
  await expect(page.locator('#build-faces-target')).toContainText('End surface');
  await expect(page.locator('#build-faces-target')).not.toContainText('Unavailable');
  await expect(page.locator('#build-faces-error')).not.toContainText('inputs unavailable');

  // An old exact context remains selected even if another completed context exists.
  graph.recipe.nodes = graph.recipe.nodes.filter(node => node.id !== 'fit');
  delete graph.results.fit;
  delete graph.states.fit;
  await evaluate(page);
  await lists(page, 'End surface', true);
  await expect(page.locator('#build-faces-target')).toContainText('Unavailable:');
  await expect(page.locator('#build-faces-error')).toContainText('Selected inputs unavailable');
  await expect(page.locator('#apply-build-faces')).toBeDisabled();

  graph = structuredClone(readyGraph);
  graph.recipe.nodes.push({ id: 'saved_faces', label: 'Saved faces', operation: 'build_faces',
    surfaces: [{ feature: 'fit', surface: 'end' }, { feature: 'fit', surface: 'side' }] });
  graph.states.saved_faces = 'ready';
  await page.reload();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.locator('#action-list .action-select').filter({ hasText: 'Saved faces' }).click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await expect(page.locator('#build-faces-surfaces option:checked')).toHaveCount(2);
  graph.states.fit = 'failed';
  await evaluate(page);
  await expect(page.locator('#build-faces-error')).toContainText('Saved inputs unavailable');
  await page.locator('#preview-build-faces').click();
  await expect(page.locator('#apply-build-faces')).toBeDisabled();
  graph.states.fit = 'ready';
  await evaluate(page);
  await expect(page.locator('#build-faces-surfaces option:checked')).toHaveCount(2);
  await expect(page.locator('#build-faces-error')).not.toContainText('inputs unavailable');
});

test('same-token eligibility changes reject a late candidate response', async ({ page }) => {
  await ready(page);
  const graph = await (await page.request.get('/api/graph')).json();
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  let release, started;
  const held = new Promise(resolve => { release = resolve; });
  const captured = new Promise(resolve => { started = resolve; });
  await page.route('**/api/graph/build-faces/candidates', async route => {
    const response = await route.fetch();
    started();
    await held;
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await captured;
  graph.states.fit = 'stale';
  await evaluate(page);
  const delivered = page.waitForResponse('**/api/graph/build-faces/candidates');
  release();
  await delivered;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('.face-candidate')).toHaveCount(0);
  await expect(page.locator('#build-faces-error')).toContainText('Selected inputs unavailable');
  await expect(page.locator('#apply-build-faces')).toBeDisabled();
});
