import { test, expect } from '@playwright/test';

const row = (page, id) => page.locator(`#action-list [data-action-id="${id}"].action-select, #action-list [data-action-id="${id}"].managed-owner-summary`);
const item = (page, id) => row(page, id).locator('xpath=ancestor::li[1]');
const groupRow = page => page.locator('#action-list summary[data-group-id="tree-group"]');
const groupItem = page => groupRow(page).locator('xpath=ancestor::li[1]');

async function ready(page, grouped = false) {
  const original = await (await page.request.get('/api/graph')).json();
  const selection = original.recipe.nodes.find(node => node.id === 'outer_band');
  const extras = ['a', 'b', 'c'].map((id, index) => ({ ...selection, id,
    label: ['Alpha', 'Beta', 'Gamma'][index], group_id: grouped && id !== 'c' ? 'tree-group' : null }));
  const owner = { id: 'tree-reuse', label: 'Copies', operation: 'feature_reuse',
    fits: ['side'], lineage: [], reference_selection: 'a', target_selections: ['b'],
    tangent_margin: 0.4, normal_margin: 0.5, normal_angle_degrees: 30,
    equal_corresponding_dimensions: false };
  const child = { id: 'tree-output', label: 'Generated selection', operation: 'reuse_selection',
    managed_by: owner.id, managed_key: 'selection/b/side/a', reuse: owner.id,
    fit: 'side', source_selection: 'a', target_selection: 'b' };
  const state = { writes: [], graph: { ...original, evaluation_running: false,
    recipe: { ...original.recipe, groups: grouped ? [{ id: 'tree-group', label: 'Selections' }] : [],
      nodes: [...original.recipe.nodes, ...extras, owner, child] },
    states: { ...original.states, a: 'ready', b: 'ready', c: 'ready',
      [owner.id]: 'unevaluated', [child.id]: 'unevaluated' } } };
  await page.route(/\/api\/graph(?:\?.*)?$/, async route => {
    if (route.request().method() === 'POST') {
      const { recipe } = route.request().postDataJSON();
      state.writes.push(structuredClone(recipe));
      state.graph = { ...state.graph, recipe, revision: state.graph.revision + 1,
        token: `tree-test-${state.writes.length}` };
    }
    await route.fulfill({ json: state.graph });
  });
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: state.graph }));
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  await expect(row(page, 'c')).toBeVisible();
  return state;
}

async function dragRow(page, from, to, after = false) {
  const source = await from.boundingBox(), target = await to.boundingBox();
  expect(source).toBeTruthy();
  expect(target).toBeTruthy();
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2,
    target.y + (after ? target.height - 2 : 2), { steps: 12 });
  await page.mouse.up();
}

test('selection highlights inputs and dependents across multiple rows, collapsed branches and clearing', async ({ page }) => {
  const state = await ready(page, true);
  await expect(page.locator('#feature-relation-legend')).toBeHidden();
  const initialTop = (await row(page, 'a').boundingBox()).y;
  await row(page, 'a').click();
  expect((await row(page, 'a').boundingBox()).y).toBe(initialTop);
  await expect(row(page, 'scan')).toHaveClass(/feature-input/);
  await expect(row(page, 'tree-reuse')).toHaveClass(/feature-dependent/);
  await expect(row(page, 'a')).toHaveClass(/feature-selected/);
  await expect(row(page, 'a')).not.toHaveClass(/feature-input|feature-dependent/);
  await expect(row(page, 'c')).not.toHaveClass(/feature-input|feature-dependent/);
  await expect(item(page, 'scan')).toHaveAttribute('aria-description', /Drag to reorder.*Inputs used by selected/);
  await expect(page.locator('#feature-relation-legend')).toBeVisible();
  await row(page, 'side').click();
  await expect(row(page, 'outer_band')).toHaveClass(/feature-input/);
  await expect(row(page, 'tree-reuse')).toHaveClass(/feature-dependent/);
  await expect(page.locator('#feature-selection-count')).toHaveText('2 selected');
  await page.locator('#clear-feature-selection').click();
  await expect(page.locator('#action-list .feature-input, #action-list .feature-dependent')).toHaveCount(0);
  await expect(page.locator('#feature-relation-legend')).toBeHidden();
  await row(page, 'tree-reuse').click();
  await groupRow(page).locator('.tree-toggle').click();
  await expect(groupRow(page)).toHaveClass(/feature-input/);
  await expect(groupItem(page)).toHaveAttribute('aria-description', /Contains features: Inputs/);
  await groupRow(page).locator('.tree-toggle').click();
  await expect(groupRow(page)).not.toHaveClass(/feature-input/);
  await expect(row(page, 'a')).toHaveClass(/feature-input/);
  await row(page, 'tree-reuse').locator('.tree-toggle').click();
  const target = page.locator('#action-list .managed-target > details > summary');
  await expect(target).toHaveClass(/feature-dependent/);
  await target.locator('.tree-toggle').click();
  await expect(target).not.toHaveClass(/feature-dependent/);
  await expect(row(page, 'tree-output')).toHaveClass(/feature-dependent/);
  await expect(item(page, 'tree-output')).toHaveAttribute('aria-selected', 'false');
  expect(state.writes).toHaveLength(0);
});

test('whole rows preserve clicks and double-click editing while drag suppresses selection', async ({ page }) => {
  const state = await ready(page);
  const gamma = row(page, 'c');
  const box = await gamma.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 2, box.y + box.height / 2 + 1);
  await page.mouse.up();
  await expect(item(page, 'c')).toHaveAttribute('aria-selected', 'true');
  expect(state.writes).toHaveLength(0);
  await expect(page.locator('#edit-popup')).toBeHidden();
  await page.locator('#clear-feature-selection').click();
  await dragRow(page, gamma, row(page, 'a'));
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.filter(node => ['a', 'b', 'c'].includes(node.id))
    .map(node => node.id)).toEqual(['c', 'a', 'b']);
  await expect(item(page, 'c')).toHaveAttribute('aria-selected', 'false');
  await expect(page.locator('#edit-popup')).toBeHidden();
  await row(page, 'side').dblclick();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await expect(page.locator('#properties-title')).toHaveText('Outer surface');
  expect(state.writes).toHaveLength(1);
});

test('dragging either selected row moves noncontiguous selections in recipe order and retains selection', async ({ page }) => {
  const state = await ready(page);
  await row(page, 'c').click();
  await row(page, 'a').click();
  await dragRow(page, row(page, 'c'), row(page, 'side'));
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.filter(node => ['a', 'b', 'c'].includes(node.id))
    .map(node => node.id)).toEqual(['a', 'c', 'b']);
  const ids = state.graph.recipe.nodes.map(node => node.id);
  expect(ids.indexOf('a')).toBeLessThan(ids.indexOf('side'));
  expect(ids.indexOf('c')).toBeLessThan(ids.indexOf('side'));
  await expect(item(page, 'a')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'c')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'c')).toBeFocused();
  await expect(page.locator('#feature-selection-count')).toHaveText('2 selected');
});

test('dragging an unselected row moves only that row and retains the other selections', async ({ page }) => {
  const state = await ready(page);
  await row(page, 'a').click();
  await row(page, 'c').click();
  await dragRow(page, row(page, 'b'), row(page, 'side'));
  await expect.poll(() => state.writes.length).toBe(1);
  const ids = state.graph.recipe.nodes.map(node => node.id);
  expect(ids.indexOf('b')).toBeLessThan(ids.indexOf('side'));
  expect(ids.indexOf('a')).toBeGreaterThan(ids.indexOf('side'));
  expect(ids.indexOf('c')).toBeGreaterThan(ids.indexOf('side'));
  await expect(item(page, 'a')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'c')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'b')).toHaveAttribute('aria-selected', 'false');
});

test('multiselection rejects incompatible generated selections without moving only part of the selection', async ({ page }) => {
  const state = await ready(page);
  await row(page, 'tree-reuse').locator('.tree-toggle').click();
  await page.locator('#action-list .managed-target > details > summary .tree-toggle').click();
  await row(page, 'tree-output').click();
  await row(page, 'a').click();
  await dragRow(page, row(page, 'a'), row(page, 'side'));
  await expect(page.locator('#action-announcement')).toContainText('Select Copies');
  expect(state.writes).toHaveLength(0);
  await expect(item(page, 'a')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'tree-output')).toHaveAttribute('aria-selected', 'true');
});

test('a dependency violation rejects the whole selected block', async ({ page }) => {
  const state = await ready(page);
  await row(page, 'a').click();
  await row(page, 'b').click();
  await dragRow(page, row(page, 'b'), row(page, 'scan'));
  await expect(page.locator('#action-announcement')).toContainText('earlier');
  expect(state.writes).toHaveLength(0);
  await expect(item(page, 'a')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'b')).toHaveAttribute('aria-selected', 'true');
});

test('releasing on the insertion line below the last row commits the move', async ({ page }) => {
  const state = await ready(page);
  const source = await row(page, 'c').boundingBox(), target = await row(page, 'tree-reuse').boundingBox();
  const x = target.x + target.width / 2;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, target.y + target.height - 2, { steps: 12 });
  await expect(row(page, 'tree-reuse')).toHaveClass(/drop-after/);
  await expect(row(page, 'tree-reuse')).not.toHaveClass(/drop-invalid/);
  await page.mouse.move(x, target.y + target.height + 2);
  await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.at(-1).id).toBe('c');
  await expect(item(page, 'c')).toBeFocused();
  expect((await row(page, 'c').boundingBox()).y).toBeGreaterThan(
    (await row(page, 'tree-reuse').boundingBox()).y);
});

test('adjacent rows share one insertion line instead of jumping across the gap', async ({ page }) => {
  const state = await ready(page);
  const source = await row(page, 'c').boundingBox(), first = await row(page, 'a').boundingBox(),
    second = await row(page, 'b').boundingBox(), x = first.x + first.width / 2;
  const indicatorY = (target, pseudo) => target.evaluate((element, pseudo) =>
    element.getBoundingClientRect().top + element.clientTop +
      parseFloat(getComputedStyle(element, pseudo).top), pseudo);
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, first.y + first.height - 6, { steps: 12 });
  await expect(row(page, 'a')).toHaveClass(/drop-after/);
  const afterY = await indicatorY(row(page, 'a'), '::after');
  await page.mouse.move(x, second.y + 6);
  await expect(row(page, 'b')).toHaveClass(/drop-before/);
  expect(await indicatorY(row(page, 'b'), '::before')).toBeCloseTo(afterY, 1);
  await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.filter(node => ['a', 'b', 'c'].includes(node.id))
    .map(node => node.id)).toEqual(['a', 'c', 'b']);
});

test('grouped selections drop on the line before the first child without moving the group', async ({ page }) => {
  const state = await ready(page, true);
  const source = await row(page, 'b').boundingBox(), target = await row(page, 'a').boundingBox(),
    x = target.x + target.width / 2;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, target.y + 6, { steps: 12 });
  await expect(row(page, 'a')).toHaveClass(/drop-before/);
  await page.mouse.move(x, target.y - 2);
  await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.filter(node => ['a', 'b'].includes(node.id))
    .map(node => node.id)).toEqual(['b', 'a']);
  await expect(groupItem(page)).toHaveAttribute('aria-expanded', 'true');
});

test('a drop boundary that preserves the current order does not show a blue marker', async ({ page }) => {
  const state = await ready(page);
  const source = await row(page, 'b').boundingBox(), target = await row(page, 'a').boundingBox();
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height - 6, { steps: 12 });
  await expect(page.locator('#action-list .drop-before, #action-list .drop-after')).toHaveCount(0);
  await page.mouse.up();
  expect(state.writes).toHaveLength(0);
});

test('reordering repeated-boss selections updates reuse lineage through real saves and survives reload', async ({ page }) => {
  const read = async () => (await page.request.get('/api/graph')).json(),
    original = await read(), catalogue = await (await page.request.get('/api/examples')).json(),
    headers = { 'X-Scansor-Request': '1' };
  try {
    const opened = await page.request.post('/api/examples/select', {
      headers, data: { example: 'repeated-boss', token: original.token },
    });
    expect(opened.ok(), await opened.text()).toBe(true);
    // Keep evaluation out of this ordering test; recipe reads and saves are real.
    await page.route('**/api/graph/ensure', async route => route.fulfill({ json: await read() }));
    await page.goto('/');
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    const before = await read(),
      shoulder = before.recipe.nodes.find(node => node.label === 'boss-a shoulder'),
      bore = before.recipe.nodes.find(node => node.label === 'boss-a bore'),
      source = await row(page, shoulder.id).boundingBox(), target = await row(page, bore.id).boundingBox(),
      x = target.x + target.width / 2;
    await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
    await page.mouse.down();
    await page.mouse.move(x, target.y + 6, { steps: 12 });
    await expect(row(page, bore.id)).toHaveClass(/drop-before/);
    await page.mouse.move(x, target.y - 2);
    const saved = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.mouse.up();
    const response = await saved;
    expect(response.ok(), await response.text()).toBe(true);
    const after = await read();
    expect(after.recipe.nodes.filter(node => node.group_id === shoulder.group_id).map(node => node.label))
      .toEqual(['boss-a outer', 'boss-a shoulder', 'boss-a bore', 'boss-a clock']);
    const reuse = after.recipe.nodes.find(node => node.operation === 'feature_reuse');
    expect(reuse.lineage.indexOf(shoulder.id)).toBeLessThan(reuse.lineage.indexOf(bore.id));
    expect(after.recipe.nodes.filter(node => node.managed_by).map(node => node.id))
      .toEqual(before.recipe.nodes.filter(node => node.managed_by).map(node => node.id));
    // Select in reverse order, then drag the second feature in recipe order.
    await row(page, bore.id).click();
    await row(page, shoulder.id).click();
    const outer = before.recipe.nodes.find(node => node.label === 'boss-a outer'),
      multiSaved = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
        response.request().method() === 'POST');
    await dragRow(page, row(page, bore.id), row(page, outer.id));
    const multiResponse = await multiSaved;
    expect(multiResponse.ok(), await multiResponse.text()).toBe(true);
    expect((await read()).recipe.nodes.filter(node => node.group_id === shoulder.group_id).map(node => node.label))
      .toEqual(['boss-a shoulder', 'boss-a bore', 'boss-a outer', 'boss-a clock']);
    await expect(item(page, shoulder.id)).toHaveAttribute('aria-selected', 'true');
    await expect(item(page, bore.id)).toHaveAttribute('aria-selected', 'true');
    await page.reload();
    await expect(item(page, shoulder.id)).toHaveAttribute('aria-posinset', '1');
    await expect(item(page, bore.id)).toHaveAttribute('aria-posinset', '2');
  } finally {
    await page.request.post('/api/examples/select', {
      headers, data: { example: catalogue.active, token: (await read()).token },
    });
    const restored = await page.request.post('/api/graph', {
      headers, data: { token: (await read()).token, recipe: original.recipe },
    });
    expect(restored.ok(), await restored.text()).toBe(true);
  }
});

test('disclosures expose tree structure and keyboard focus follows visible rows', async ({ page }) => {
  await ready(page, true);
  const tree = page.getByRole('tree', { name: 'Features in evaluation order' });
  await expect(tree).toBeVisible();
  await expect(tree).toHaveAttribute('data-tree-library', 'headless-tree');
  await expect(tree.locator('.action-grip, .group-action')).toHaveCount(0);
  await expect(groupItem(page)).toHaveAttribute('aria-level', '1');
  await expect(item(page, 'a')).toHaveAttribute('aria-level', '2');
  await expect(item(page, 'a')).toHaveAttribute('aria-setsize', '2');
  await expect(item(page, 'b')).toHaveAttribute('aria-posinset', '2');
  await page.evaluate(() => {
    window.treeNavigationDefaults = [];
    window.addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
        window.treeNavigationDefaults.push(event.defaultPrevented);
    });
  });
  await expect(groupItem(page)).toHaveAttribute('aria-expanded', 'true');
  await groupRow(page).locator('.tree-toggle').click();
  await expect(groupItem(page)).toHaveAttribute('aria-expanded', 'false');
  await expect(row(page, 'a')).toBeHidden();
  await groupItem(page).focus();
  await page.keyboard.press('ArrowDown');
  await expect(item(page, 'c')).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(groupItem(page)).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(groupItem(page)).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('ArrowRight');
  await expect(item(page, 'a')).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(groupItem(page)).toBeFocused();
  await page.keyboard.press('Home');
  await expect(item(page, 'scan')).toBeFocused();
  await page.keyboard.press('End');
  await expect(item(page, 'tree-reuse')).toBeFocused();
  await expect(item(page, 'tree-reuse')).toHaveAttribute('aria-expanded', 'false');
  await row(page, 'tree-reuse').click();
  await expect(item(page, 'tree-reuse')).toHaveAttribute('aria-selected', 'true');
  await expect(item(page, 'tree-reuse')).toHaveAttribute('aria-expanded', 'false');
  await row(page, 'tree-reuse').locator('.tree-toggle').click();
  await expect(item(page, 'tree-reuse')).toHaveAttribute('aria-expanded', 'true');
  const bucket = page.locator('#action-list .managed-target > details > summary');
  await expect(bucket).toBeVisible();
  await bucket.locator('.tree-toggle').click();
  await expect(row(page, 'tree-output')).toBeVisible();
  await item(page, 'tree-output').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(bucket.locator('xpath=ancestor::li[1]')).toBeFocused();
  await expect(tree.locator('[role="treeitem"][tabindex="0"]')).toHaveCount(1);
  expect(await page.evaluate(() => window.treeNavigationDefaults.every(Boolean))).toBe(true);
});

test('keyboard reorder preserves focus and dependencies while generated leaves cannot drag', async ({ page }) => {
  const state = await ready(page);
  await item(page, 'c').focus();
  await page.keyboard.press('Alt+ArrowUp');
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.graph.recipe.nodes.filter(node => ['a', 'b', 'c'].includes(node.id))
    .map(node => node.id)).toEqual(['a', 'c', 'b']);
  await expect(item(page, 'c')).toBeFocused();
  await item(page, 'scan').focus();
  await page.keyboard.press('Alt+ArrowDown');
  await expect(page.locator('#action-announcement')).toContainText('earlier');
  expect(state.writes).toHaveLength(1);
  await row(page, 'tree-reuse').locator('.tree-toggle').click();
  await page.locator('#action-list .managed-target > details > summary .tree-toggle').click();
  await expect(row(page, 'tree-output')).toHaveAttribute('draggable', 'false');
  await dragRow(page, row(page, 'tree-output'), row(page, 'c'));
  expect(state.writes).toHaveLength(1);
});

test('group context menu Ungroup removes only organization and retains every feature', async ({ page }) => {
  const state = await ready(page, true);
  const before = structuredClone(state.graph.recipe.nodes);
  await groupRow(page).click({ button: 'right' });
  await expect(page.locator('#group-context-ungroup')).toHaveText('Ungroup');
  await page.locator('#group-context-ungroup').click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(groupRow(page)).toHaveCount(0);
  expect(state.graph.recipe.groups).toEqual([]);
  expect(state.graph.recipe.nodes).toEqual(before.map(node => node.group_id === 'tree-group'
    ? { ...node, group_id: null } : node));
  await expect(row(page, 'a')).toBeVisible();
  await expect(row(page, 'b')).toBeVisible();
});

test('Ungroup respects the edited group draft and closes its editor after confirmation', async ({ page }) => {
  const state = await ready(page, true);
  await groupRow(page).click({ button: 'right' });
  await page.locator('#group-context-edit').click();
  await page.locator('#feature-group-label').fill('Unapplied group name');
  page.once('dialog', dialog => dialog.dismiss());
  await groupRow(page).click({ button: 'right' });
  await page.locator('#group-context-ungroup').click();
  await expect(page.locator('#feature-group-label')).toHaveValue('Unapplied group name');
  await expect(groupRow(page)).toBeVisible();
  expect(state.writes).toHaveLength(0);
  page.once('dialog', dialog => dialog.accept());
  await groupRow(page).click({ button: 'right' });
  await page.locator('#group-context-ungroup').click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(groupRow(page)).toHaveCount(0);
  await expect(page.locator('#edit-popup')).toBeHidden();
});
