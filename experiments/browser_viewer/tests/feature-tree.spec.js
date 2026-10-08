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

test('disclosures expose tree structure and keyboard focus follows visible rows', async ({ page }) => {
  await ready(page, true);
  const tree = page.getByRole('tree', { name: 'Features in evaluation order' });
  await expect(tree).toBeVisible();
  await expect(tree.locator('.action-grip, .group-action')).toHaveCount(0);
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
