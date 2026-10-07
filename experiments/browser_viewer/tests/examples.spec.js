import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const savedRecipes = {
  nozzle: JSON.parse(readFileSync(new URL('../../../examples/nozzle-bayonette-simplified/recipes/nozzle-selection-and-fitting-demo.json', import.meta.url), 'utf8')),
  'repeated-boss': JSON.parse(readFileSync(new URL('../../../examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json', import.meta.url), 'utf8')),
};

async function ready(page) {
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Choose example…', exact: true })).toBeEnabled({ timeout: 120000 });
}

const read = async page => (await page.request.get('/api/graph')).json();

async function openExample(page, id) {
  await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Choose example', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('combobox', { name: 'Example', exact: true }).selectOption(id);
  await Promise.all([
    page.waitForEvent('load'),
    dialog.getByRole('button', { name: 'Open example', exact: true }).click(),
  ]);
  await ready(page);
}

test('example chooser loads both saved feature recipes and restore preserves their definitions', async ({ page }) => {
  test.setTimeout(240000);
  await page.goto('/');
  await ready(page);
  const original = await read(page),
    nozzleMesh = await (await page.request.get('/mesh/positions')).body();
  try {
    await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Choose example', exact: true });
    await expect(dialog.getByRole('combobox')).toHaveValue('nozzle');
    await expect(dialog.locator('option')).toHaveText(['Nozzle', 'Repeated bosses']);
    await expect(dialog.getByRole('button', { name: 'Open example', exact: true })).toBeDisabled();
    await expect(dialog).toContainText('Save actions first');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await read(page)).token).toBe(original.token);

    await openExample(page, 'repeated-boss');
    expect((await (await page.request.get('/api/examples')).json()).active).toBe('repeated-boss');
    const repeated = await read(page), repeatedMesh = await (await page.request.get('/mesh/positions')).body();
    expect(repeatedMesh.equals(nozzleMesh)).toBe(false);
    expect(repeated.recipe).toMatchObject(savedRecipes['repeated-boss']);
    expect(repeated.recipe.nodes).toHaveLength(131);
    expect(repeated.recipe.nodes.some(node => node.operation === 'fit')).toBe(true);
    expect(repeated.recipe.nodes.find(node => node.operation === 'source').source_sha256)
      .not.toBe(original.recipe.nodes.find(node => node.operation === 'source').source_sha256);
    const response = await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: repeated.token,
        recipe: { ...repeated.recipe, nodes: [...repeated.recipe.nodes,
          { id: 'example-test-point', operation: 'point', label: 'Changed actions', initial_coordinates: [0, 0, 0] }] } },
    });
    expect(response.ok(), await response.text()).toBe(true);
    await page.reload();
    await ready(page);
    expect((await read(page)).recipe.nodes.slice(0, 131)).toEqual(repeated.recipe.nodes);
    expect((await read(page)).recipe.nodes).toHaveLength(132);
    const restored = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Restore example', exact: true }).click();
    expect((await restored).ok()).toBe(true);
    expect((await read(page)).recipe).toEqual(repeated.recipe);
    expect((await read(page)).recipe).toMatchObject(savedRecipes['repeated-boss']);
    expect((await (await page.request.get('/mesh/positions')).body()).equals(repeatedMesh)).toBe(true);
    await ready(page);
    await openExample(page, 'nozzle');
    expect((await (await page.request.get('/api/examples')).json()).active).toBe('nozzle');
    expect((await (await page.request.get('/mesh/positions')).body()).equals(nozzleMesh)).toBe(true);
    const nozzle = await read(page);
    expect(nozzle.recipe).toMatchObject(savedRecipes.nozzle);
    expect(nozzle.recipe.nodes).toHaveLength(30);
    expect(nozzle.recipe.nodes.filter(node => node.operation === 'fit')).toHaveLength(11);
    await page.reload();
    await ready(page);
    expect((await read(page)).recipe).toMatchObject(savedRecipes.nozzle);
    const edited = await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: nozzle.token,
        recipe: { ...nozzle.recipe, nodes: [...nozzle.recipe.nodes,
          { id: 'nozzle-test-point', operation: 'point', label: 'Changed nozzle', initial_coordinates: [0, 0, 0] }] } },
    });
    expect(edited.ok(), await edited.text()).toBe(true);
    await page.reload();
    await ready(page);
    const restoredNozzle = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Restore example', exact: true }).click();
    expect((await restoredNozzle).ok()).toBe(true);
    expect((await read(page)).recipe).toEqual(nozzle.recipe);
    expect((await read(page)).recipe).toMatchObject(savedRecipes.nozzle);
    await ready(page);
    await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
    await expect(page.locator('#example-choice')).toHaveValue('nozzle');
  } finally {
    const state = await read(page);
    await page.request.post('/api/examples/select', {
      headers: { 'X-Scansor-Request': '1' }, data: { example: 'nozzle', token: state.token },
    });
    const current = await read(page);
    await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe: original.recipe },
    });
  }
});

test('failed switch pauses editing and preserves the current mesh and actions', async ({ page }) => {
  await page.goto('/');
  await ready(page);
  const original = await read(page), mesh = await (await page.request.get('/mesh/positions')).body();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/examples/select', async route => {
    await gate;
    await route.fulfill({ status: 422, contentType: 'application/json',
      body: JSON.stringify({ error: 'Example unavailable for this test' }) });
  });
  await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
  await page.locator('#example-choice').selectOption('repeated-boss');
  const requested = page.waitForRequest(request => request.url().endsWith('/api/examples/select'));
  await page.getByRole('button', { name: 'Open example', exact: true }).click();
  expect((await requested).postDataJSON()).toEqual({ example: 'repeated-boss', token: original.token });
  await expect(page.locator('#example-choice')).toBeDisabled();
  await expect(page.locator('#cancel-example')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Choose example…', exact: true })).toBeDisabled();
  await expect(page.locator('#viewport')).toHaveJSProperty('inert', true);
  await page.keyboard.press('Escape');
  await expect(page.locator('#example-dialog')).toBeVisible();
  release();
  await expect(page.locator('#example-error')).toHaveText('Example unavailable for this test');
  await expect(page.locator('#cancel-example')).toBeEnabled();
  await expect(page.locator('#viewport')).toHaveJSProperty('inert', false);
  expect((await read(page)).recipe).toEqual(original.recipe);
  expect((await read(page)).token).toBe(original.token);
  expect((await (await page.request.get('/mesh/positions')).body()).equals(mesh)).toBe(true);
  await page.locator('#cancel-example').click();
  await page.getByRole('button', { name: 'Choose example…', exact: true }).click();
  await expect(page.locator('#example-choice')).toHaveValue('nozzle');
});
