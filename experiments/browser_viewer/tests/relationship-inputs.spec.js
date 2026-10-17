import { test, expect } from '@playwright/test';

const read = async page => (await page.request.get('/api/graph')).json();
const direct = feature => ({ feature, output: 'plane', context: feature });

async function waitReady(page) {
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#startup-error')).toBeHidden();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
}

async function replace(page, recipe) {
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
  const state = await read(page), response = await page.request.post('/api/graph', {
    headers: { 'X-Scansor-Request': '1' }, data: { token: state.token, recipe },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

let original;
test.beforeEach(async ({ page }) => {
  original = (await read(page)).recipe;
  page.on('dialog', dialog => dialog.accept());
  const nodes = original.nodes.filter(node => ['source', 'selection', 'fit'].includes(node.operation));
  nodes.push({ id: 'reference_axis', label: 'Reference axis', operation: 'axis', source_fit: 'side' });
  for (const [index, id] of ['first_datum', 'second_datum', 'third_datum'].entries()) nodes.push({
    id, label: ['First datum', 'Second datum', 'Third datum'][index], operation: 'reference_plane',
    axis: 'reference_axis', construction: 'perpendicular_to_axis', initial_angle_degrees: null, offset: index + 1,
  });
  await replace(page, { ...original, groups: [], nodes, output: 'third_datum' });
  await page.goto('/');
  await waitReady(page);
  await expect.poll(async () => Object.values((await read(page)).states).every(state => state === 'ready')).toBe(true);
});

test.afterEach(async ({ page }) => { if (original) await replace(page, original); });

async function chooseRelationship(page, label) {
  await page.getByRole('button', { name: 'Relationship…', exact: true }).click();
  await page.locator('#relationship-kind').selectOption('parallel_planes');
  const options = page.locator('#relationship-dialog details').filter({ has: page.locator('#new-relationship-label') });
  if (!await options.evaluate(details => details.open)) await options.getByText('Options', { exact: true }).click();
  await page.locator('#new-relationship-label').fill(label);
  await page.getByRole('button', { name: 'Show Participants choices', exact: true }).click();
}

test('Parallel accepts several reference planes and a fit, preserving offsets and saved output identities', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const before = await read(page);
  await chooseRelationship(page, 'Mixed parallel planes');
  for (const name of ['First datum — plane', 'Second datum — plane', 'End surface — plane'])
    await page.getByRole('checkbox', { name, exact: true }).check();
  await expect(page.locator('#relationship-summary')).toHaveText('Same normal; independent offsets.');
  await page.keyboard.press('Escape');
  await page.locator('#add-relationship').click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect.poll(async () => {
    const graph = await read(page), relation = graph.recipe.nodes.find(node => node.label === 'Mixed parallel planes');
    return relation && graph.states[relation.id];
  }).toBe('ready');
  const saved = await read(page), relation = saved.recipe.nodes.find(node => node.label === 'Mixed parallel planes');
  expect(relation.surfaces).toEqual([direct('end'), direct('first_datum'), direct('second_datum')]);
  for (const key of ['first_datum', 'second_datum'])
    expect(saved.results[key].plane_equation).toEqual(before.results[key].plane_equation);
  const normals = relation.surfaces.map(ref => saved.results[ref.feature].plane_equation.slice(0, 3));
  for (const normal of normals.slice(1))
    expect(Math.abs(normal.reduce((sum, value, index) => sum + value * normals[0][index], 0))).toBeCloseTo(1, 9);

  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  let content = '';
  for await (const chunk of await (await downloading).createReadStream()) content += chunk.toString();
  expect(JSON.parse(content).nodes.find(node => node.id === relation.id).surfaces).toEqual(relation.surfaces);
  const generation = (await (await page.request.get('/api/meta')).json()).project_generation;
  await page.locator('#file').setInputFiles({ name: 'parallel-actions.json', mimeType: 'application/json', buffer: Buffer.from(content) });
  await expect.poll(async () => (await (await page.request.get('/api/meta')).json()).project_generation).not.toBe(generation);
  await expect.poll(async () => (await read(page)).recipe.nodes.find(node => node.id === relation.id)?.surfaces).toEqual(relation.surfaces);
  await waitReady(page);
  expect(errors).toEqual([]);
});

test('a chosen solved plane context remains fixed and distinct from its adjustable owner', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await chooseRelationship(page, 'First publication');
  for (const name of ['First datum — plane', 'End surface — plane'])
    await page.getByRole('checkbox', { name, exact: true }).check();
  await page.keyboard.press('Escape');
  await page.locator('#add-relationship').click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect.poll(async () => {
    const graph = await read(page), relation = graph.recipe.nodes.find(node => node.label === 'First publication');
    return relation && graph.states[relation.id];
  }).toBe('ready');
  const first = await read(page), publisher = first.recipe.nodes.find(node => node.label === 'First publication');
  await chooseRelationship(page, 'Fixed publication input');
  await page.getByRole('checkbox', { name: 'Third datum — plane', exact: true }).check();
  await page.getByRole('checkbox', { name: 'End surface — plane · First publication', exact: true }).check();
  await expect(page.getByRole('checkbox', { name: 'End surface — plane', exact: true })).not.toBeChecked();
  await page.keyboard.press('Escape');
  await page.locator('#add-relationship').click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect.poll(async () => {
    const graph = await read(page), relation = graph.recipe.nodes.find(node => node.label === 'Fixed publication input');
    return relation && graph.states[relation.id];
  }).toBe('ready');
  const graph = await read(page), relation = graph.recipe.nodes.find(node => node.label === 'Fixed publication input');
  expect(relation.surfaces).toContainEqual({ feature: 'end', output: 'plane', context: publisher.id });
  expect(relation.surfaces).not.toContainEqual(direct('end'));
  expect(graph.derived[publisher.id]).toEqual(first.derived[publisher.id]);
  expect(errors).toEqual([]);
});
