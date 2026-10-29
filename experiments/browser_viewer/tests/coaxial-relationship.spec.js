import { test, expect } from '@playwright/test';

const read = async page => (await page.request.get('/api/graph')).json();
const axisRef = feature => JSON.stringify({ feature, output: 'axis', context: feature });
const row = (page, id) => page.locator(`#action-list .action-select[data-action-id="${id}"]`);

async function replace(page, recipe) {
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
  const current = await read(page), response = await page.request.post('/api/graph', {
    headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function settled(page) {
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect.poll(async () => (await read(page)).evaluation_running).toBe(false);
}

let original;
test.beforeEach(async ({ page }) => {
  original = (await read(page)).recipe;
  page.on('dialog', dialog => dialog.accept());
  const nodes = structuredClone(original.nodes.filter(node => ['source', 'selection', 'fit'].includes(node.operation))),
    side = nodes.find(node => node.id === 'side'), support = nodes.find(node => node.id === side.selections[0]),
    innerSupport = { ...support, id: 'inner_band', label: 'Inner observations', ids: support.ids.filter((_, index) => index % 2) };
  support.ids = support.ids.filter((_, index) => index % 2 === 0);
  nodes.splice(nodes.indexOf(side), 0, innerSupport);
  nodes.push({ ...side, id: 'inner', label: 'Inner cylinder', kind: 'cylinder', selections: ['inner_band'], axial_domain: null });
  nodes.push({ ...side, id: 'replacement', label: 'Replacement cone', selections: ['inner_band'] });
  for (const [id, label] of [['reference_axis', 'Reference axis'], ['other_axis', 'Other axis']])
    nodes.push({ id, label, operation: 'axis', source_fit: 'side' });
  nodes.push({ id: 'base', label: 'Base datum', operation: 'reference_plane', axis: 'reference_axis',
    construction: 'perpendicular_to_axis', initial_angle_degrees: null, offset: 0 });
  await replace(page, { ...original, groups: [], nodes, output: 'base' });
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#startup-error')).toBeHidden();
  await settled(page);
  await expect.poll(async () => Object.values((await read(page)).states).every(state => state === 'ready')).toBe(true);
});
test.afterEach(async ({ page }) => { if (original) await replace(page, original); });

async function startCoaxial(page) {
  await page.getByRole('button', { name: 'Relationship…', exact: true }).click();
  await expect(page.locator('#relationship-kind option').filter({ hasText: /^Coaxial$/ })).toHaveCount(1);
  await page.locator('#relationship-kind').selectOption('coaxial');
  const details = page.locator('#relationship-dialog details').filter({ has: page.locator('#new-relationship-label') });
  if (!await details.evaluate(node => node.open)) await details.getByText('Options', { exact: true }).click();
  await page.locator('#new-relationship-label').fill('Coaxial surfaces');
}

async function pickList(page, names) {
  const [axis, ...members] = names;
  await page.getByRole('button', { name: 'Show Reference axis choices', exact: true }).click();
  await page.locator('[data-relationship-role="axis"]').getByRole('radio', { name: axis, exact: true }).check();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Show Target fits choices', exact: true }).click();
  for (const name of members)
    await page.locator('[data-relationship-role="members"]').getByRole('checkbox', { name, exact: true }).check();
  await page.keyboard.press('Escape');
}

async function visiblePatch(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const point = await page.evaluate(() => {
    const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
    for (let y = box.y + 90; y < box.bottom - 55; y += 14) for (let x = box.x + 10; x < box.right - 10; x += 14) {
      if (document.elementFromPoint(x, y) !== canvas) continue;
      canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: x, clientY: y, pointerId: 1 }));
      const message = document.querySelector('#model-pick-message').textContent;
      if (message.includes('Outer surface') || message.includes('3 items here')) return { x, y };
    }
    return null;
  });
  expect(point, 'a visible eligible cone/cylinder patch').toBeTruthy();
  return point;
}

async function createdGroup(page) {
  await page.locator('#add-relationship').click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect.poll(async () => {
    const state = await read(page), node = state.recipe.nodes.find(node => node.operation === 'coaxial_group');
    if (node && ['failed', 'blocked'].includes(state.states[node.id]))
      throw new Error(JSON.stringify(state.errors));
    return node && state.states[node.id];
  }).toBe('ready');
  return (await read(page)).recipe.nodes.find(node => node.operation === 'coaxial_group');
}

test('Coaxial combines model and list picks, edits its axis, and survives save/load', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const before = await read(page);
  await startCoaxial(page);
  await pickList(page, ['Reference axis — axis', 'Inner cylinder — axis']);
  await page.locator('[data-relationship-role="members"] .model-field-pick').click();
  const point = await visiblePatch(page);
  await page.mouse.click(point.x, point.y);
  const chooser = page.locator('#model-pick-choices');
  if (await chooser.isVisible()) await chooser.getByRole('button', { name: /Outer surface/ }).click();
  await expect(page.locator('[data-relationship-role="axis"] input:checked')).toHaveCount(1);
  await expect(page.locator('[data-relationship-role="members"] input:checked')).toHaveCount(2);
  await page.keyboard.press('Escape');
  await expect(page.locator('#relationship-summary')).toHaveText('Same axis line; independent radii and taper.');
  const group = await createdGroup(page);
  expect(group).toMatchObject({ axis: 'reference_axis', surfaces: ['side', 'inner'] });
  const created = await read(page);
  for (const id of ['side', 'inner']) expect(created.recipe.nodes.find(node => node.id === id)).toEqual(before.recipe.nodes.find(node => node.id === id));
  expect(created.input_requirements.coaxial_members.choices.every(output => output.reference.context === output.reference.feature)).toBe(true);
  expect(created.input_requirements.relationship_axis.choices.some(output => output.reference.context === group.id)).toBe(true);

  await row(page, group.id).dblclick();
  await expect(page.locator('#coaxial-group-properties')).toBeVisible();
  await page.locator('#coaxial-group-axis').selectOption(axisRef('other_axis'));
  await page.locator('#coaxial-group-members').selectOption([axisRef('side'), axisRef('replacement')]);
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read(page)).recipe.nodes.find(node => node.id === group.id).axis).toBe('other_axis');
  await settled(page);
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  let content = '';
  for await (const chunk of await (await downloading).createReadStream()) content += chunk.toString();
  expect(JSON.parse(content).nodes.find(node => node.id === group.id)).toMatchObject({ operation: 'coaxial_group', axis: 'other_axis', surfaces: ['side', 'replacement'] });
  const generation = (await (await page.request.get('/api/meta')).json()).project_generation;
  await page.locator('#file').setInputFiles({ name: 'coaxial.json', mimeType: 'application/json', buffer: Buffer.from(content) });
  await expect.poll(async () => (await (await page.request.get('/api/meta')).json()).project_generation).not.toBe(generation);
  await settled(page);
  expect((await read(page)).recipe.nodes.find(node => node.id === group.id).axis).toBe('other_axis');
  expect(errors).toEqual([]);
});

test('an unavailable saved Coaxial member stays visible and selected until explicit repair', async ({ page }) => {
  await startCoaxial(page);
  await pickList(page, ['Reference axis — axis', 'Outer surface — axis', 'Inner cylinder — axis']);
  const group = await createdGroup(page), graph = await read(page),
    members = graph.input_requirements.coaxial_members,
    removed = members.choices.find(output => output.reference.feature === 'inner');
  members.choices = members.choices.filter(output => output.reference.feature !== 'inner');
  members.unavailable.push({ ...removed, availability: 'blocked', reason: 'Repair its input declaration' });
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.reload();
  await settled(page);
  await row(page, group.id).dblclick();
  const unavailable = page.locator('#coaxial-group-members option').filter({ hasText: 'Repair its input declaration' });
  await expect(unavailable).toBeAttached();
  expect(await unavailable.evaluate(option => option.disabled && option.selected)).toBe(true);
  expect(await page.locator('#coaxial-group-members').evaluate(control => [...control.selectedOptions].map(option => option.value)))
    .toEqual([axisRef('side'), axisRef('inner')]);
  await page.getByRole('button', { name: /^Remove Inner cylinder.*Repair its input declaration/ }).click();
  await page.getByRole('button', { name: 'Show Coaxial fits choices', exact: true }).click();
  await page.getByRole('menuitemcheckbox', { name: 'Replacement cone — axis', exact: true }).click();
  await page.keyboard.press('Escape');
  expect(await unavailable.evaluate(option => option.selected)).toBe(false);
  await expect(page.locator('#coaxial-group-axis')).toHaveValue(axisRef('reference_axis'));
  expect(await page.locator('#coaxial-group-members').evaluate(control => [...control.selectedOptions].map(option => option.value)))
    .toEqual([axisRef('side'), axisRef('replacement')]);
});

test('Joint axis and snapshot refresh preserve the draft and accept a Coaxial group alone', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await read(page), nodes = [...state.recipe.nodes,
    { id: 'new_coaxial', label: 'New Coaxial', operation: 'coaxial_group', axis: 'reference_axis', surfaces: ['side', 'inner'] },
    { id: 'new_parallel', label: 'New Parallel', operation: 'plane_relationship', relation: 'parallel', surfaces: [
      { feature: 'base', output: 'plane', context: 'base' }, { feature: 'end', output: 'plane', context: 'end' },
    ] }];
  await replace(page, { ...state.recipe, nodes, output: 'new_parallel' });
  await page.reload();
  await settled(page);
  await page.getByRole('button', { name: 'Joint', exact: true }).click();
  await page.locator('#new-axis-solve-axis').selectOption('other_axis');
  await expect(page.locator('#new-axis-solve-factors option:not(:disabled)')).toHaveCount(0);
  await page.locator('#new-axis-solve-label').fill('Selected group joint');
  await page.locator('#new-axis-solve-axis').selectOption('reference_axis');
  await page.locator('#new-axis-solve-factors').selectOption(['new_coaxial']);
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await settled(page);
  await expect(page.locator('#new-axis-solve-factors option[value="new_coaxial"]')).toBeAttached();
  await expect(page.locator('#new-axis-solve-factors option[value="new_parallel"]')).toBeAttached();
  await expect(page.locator('#new-axis-solve-label')).toHaveValue('Selected group joint');
  await expect(page.locator('#new-axis-solve-axis')).toHaveValue('reference_axis');
  expect(await page.locator('#new-axis-solve-factors').evaluate(control => [...control.selectedOptions].map(option => option.value))).toEqual(['new_coaxial']);
  await page.locator('#add-axis-solve-form').getByRole('button', { name: 'Add joint', exact: true }).click();
  await expect(page.locator('#axis-solve-dialog')).toBeHidden();
  await expect.poll(async () => {
    const state = await read(page), joint = state.recipe.nodes.find(node => node.label === 'Selected group joint');
    return joint && state.states[joint.id];
  }).toBe('ready');
  expect((await read(page)).recipe.nodes.find(node => node.label === 'Selected group joint').factors).toEqual(['new_coaxial']);
  expect(errors).toEqual([]);
});
