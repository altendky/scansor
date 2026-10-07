import { test, expect } from '@playwright/test';

const read = async page => (await page.request.get('/api/graph')).json();

async function loadFile(page, recipe) {
  const imported = page.waitForResponse(response => response.url().endsWith('/api/graph/import') &&
    response.request().method() === 'POST');
  await page.locator('#file').setInputFiles({ name: 'actions.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(recipe)) });
  return imported;
}

async function ready(page) {
  await expect(page.getByRole('button', { name: 'Load actions', exact: true })).toBeEnabled();
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
}

async function restore(page, recipe) {
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
  const state = await read(page), response = await page.request.post('/api/graph', {
    headers: { 'X-Scansor-Request': '1' }, data: { token: state.token, recipe },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test('file loading renames collisions visibly, preserves ID relationships, and warns through evaluation', async ({ page }) => {
  await page.goto('/');
  await ready(page);
  const original = await read(page),
    source = original.recipe.nodes.find(node => node.operation === 'source'),
    plane = original.recipe.nodes.find(node => node.operation === 'fit' && node.kind === 'plane'),
    selected = original.recipe.nodes.find(node => node.id === plane.selections[0]),
    recipe = { schema_version: 2, nodes: [source,
      { id: 'observations', operation: 'selection', label: 'outer', source: source.id, ids: selected.ids },
      { id: 'first-plane', operation: 'fit', label: 'outer', kind: 'plane', selections: ['observations'] },
      { id: 'occupied-name', operation: 'point', label: 'outer fit', initial_coordinates: [0, 0, 0] },
      { id: 'second-plane', operation: 'fit', label: 'Other plane', kind: 'plane', selections: ['observations'] },
      { id: 'relationship', operation: 'plane_relationship', label: 'Parallel planes', relation: 'parallel',
        surfaces: ['first-plane', 'second-plane'] },
    ], output: 'relationship' };
  try {
    const response = await loadFile(page, recipe);
    expect(response.ok(), await response.text()).toBe(true);
    const imported = await response.json();
    expect(imported.import_warnings).toEqual([{ id: 'first-plane', old_name: 'outer', new_name: 'outer fit 2', kind: 'feature' }]);
    await expect(page.locator('#import-warning')).toBeVisible();
    await expect(page.locator('#import-warning-list')).toHaveText('“outer” → “outer fit 2”');
    expect(imported.recipe.nodes.map(node => node.id)).toEqual(recipe.nodes.map(node => node.id));
    expect(imported.recipe.nodes.find(node => node.id === 'observations').label).toBe('outer');
    expect(imported.recipe.nodes.find(node => node.id === 'occupied-name').label).toBe('outer fit');
    expect(imported.recipe.nodes.find(node => node.id === 'first-plane').selections).toEqual(['observations']);
    expect(imported.recipe.nodes.find(node => node.id === 'relationship').surfaces).toEqual(['first-plane', 'second-plane']);
    await expect.poll(async () => (await read(page)).states.relationship).toBe('ready');
    await ready(page);
    await expect(page.locator('#import-warning')).toBeVisible();
    await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
    await ready(page);
    await expect(page.locator('#import-warning-list')).toHaveText('“outer” → “outer fit 2”');
    const downloaded = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Save actions', exact: true }).click();
    const stream = await (await downloaded).createReadStream();
    let content = '';
    for await (const chunk of stream) content += chunk.toString();
    const saved = JSON.parse(content);
    expect(saved).toEqual((await read(page)).recipe);
    expect(saved.nodes.find(node => node.id === 'relationship').surfaces).toEqual(['first-plane', 'second-plane']);
    expect((await loadFile(page, saved)).ok()).toBe(true);
    await expect(page.locator('#import-warning')).toBeHidden();
    await ready(page);
    expect((await read(page)).recipe).toEqual(saved);
  } finally { await restore(page, original.recipe); }
});

test('invalid files leave actions intact and import warnings render names as text and can be dismissed', async ({ page }) => {
  await page.goto('/');
  await ready(page);
  const original = await read(page), source = original.recipe.nodes.find(node => node.operation === 'source'),
    name = '<img src=x onerror=alert(1)>',
    recipe = { schema_version: 2, nodes: [source,
      { id: 'first', label: name, operation: 'point', initial_coordinates: [0, 0, 0] },
      { id: 'second', label: name, operation: 'point', initial_coordinates: [1, 0, 0] },
    ], output: 'second' };
  try {
    expect((await loadFile(page, recipe)).ok()).toBe(true);
    await ready(page);
    await expect(page.locator('#import-warning')).toBeVisible();
    await expect(page.locator('#import-warning-list')).toContainText(name);
    await expect(page.locator('#import-warning img')).toHaveCount(0);
    const current = await read(page);
    for (const invalid of [
      { ...current.recipe, nodes: [...current.recipe.nodes, current.recipe.nodes[1]] },
      { schema_version: 2, nodes: [source, { id: 'invalid', operation: 'point', label: 'Invalid' }] },
    ]) {
      const response = await loadFile(page, invalid);
      expect(response.status()).toBe(422);
      await expect(page.locator('#status')).toHaveClass('error');
      expect((await read(page)).token).toBe(current.token);
      expect((await read(page)).recipe).toEqual(current.recipe);
      await expect(page.locator('#import-warning')).toBeVisible();
    }
    await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
    await expect(page.locator('#import-warning')).toBeHidden();
    await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
    await ready(page);
    await expect(page.locator('#import-warning')).toBeHidden();
  } finally { await restore(page, original.recipe); }
});

test('a stale import refreshes current actions and an explicit second load uses the refreshed token', async ({ page }) => {
  await page.goto('/');
  await ready(page);
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  const original = await read(page), source = original.recipe.nodes.find(node => node.operation === 'source'),
    initial = { schema_version: 2, nodes: [source,
      { id: 'first', label: 'Shared', operation: 'point', initial_coordinates: [0, 0, 0] },
      { id: 'second', label: 'Shared', operation: 'point', initial_coordinates: [1, 0, 0] },
    ], output: 'second' },
    target = { schema_version: 2, nodes: [source,
      { id: 'loaded-point', label: 'Loaded point', operation: 'point', initial_coordinates: [2, 0, 0] },
    ], output: 'loaded-point' };
  try {
    expect((await loadFile(page, initial)).ok()).toBe(true);
    await expect(page.locator('#import-warning')).toBeVisible();
    const warning = await page.locator('#import-warning-list').textContent(), before = await read(page),
      changed = await page.request.post('/api/graph', {
        headers: { 'X-Scansor-Request': '1' }, data: { token: before.token,
          recipe: { schema_version: 2, nodes: [source,
            { id: 'external-change', label: 'External change', operation: 'point', initial_coordinates: [3, 0, 0] },
          ], output: 'external-change' } },
      });
    expect(changed.ok(), await changed.text()).toBe(true);
    const current = await changed.json(), requests = [];
    page.on('request', request => {
      if (request.url().endsWith('/api/graph/import') && request.method() === 'POST') requests.push(request.postDataJSON());
    });
    const rejected = await loadFile(page, target);
    expect(rejected.status()).toBe(409);
    await expect(page.locator('#status')).toHaveClass('error');
    await expect(page.locator('#action-list .action-select[data-action-id="external-change"]')).toBeVisible();
    await expect(page.locator('#import-warning-list')).toHaveText(warning);
    expect((await read(page)).recipe).toEqual(current.recipe);
    expect((await read(page)).token).toBe(current.token);
    expect(requests).toHaveLength(1);
    expect(requests[0].token).toBe(before.token);
    const retried = await loadFile(page, target);
    expect(retried.ok(), await retried.text()).toBe(true);
    expect(requests).toHaveLength(2);
    expect(requests[1].token).toBe(current.token);
    await expect(page.locator('#action-list .action-select[data-action-id="loaded-point"]')).toBeVisible();
    await expect(page.locator('#import-warning')).toBeHidden();
  } finally { await restore(page, original.recipe); }
});
