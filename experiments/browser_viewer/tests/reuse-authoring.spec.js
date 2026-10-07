import { test, expect } from '@playwright/test';

test('reuse creation, edits and equality use backend authoring with stable child IDs', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  const original = await (await page.request.get('/api/graph')).json();
  const read = async () => (await page.request.get('/api/graph')).json();
  const replace = async recipe => {
    const current = await read();
    const response = await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe },
    });
    expect(response.ok()).toBe(true);
  };
  const author = async action => {
    const response = page.waitForResponse('**/api/graph/feature-reuse/apply', { timeout: 30000 });
    await action();
    const saved = await response;
    expect(saved.ok()).toBe(true);
    return saved.json();
  };
  try {
    const source = original.recipe.nodes.find(node => node.operation === 'source');
    const selection = original.recipe.nodes.find(node => node.id === 'outer_band');
    await replace({ schema_version: 2, groups: [{ id: 'group', label: 'Copies' }],
      nodes: [source, ...['a', 'b', 'c'].map(id => ({ ...selection, id, label: id })),
        { id: 'outer', label: 'Outer', operation: 'fit', kind: 'cylinder', selections: ['a'] }],
      output: 'outer' });
    await page.reload();
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Feature reuse', exact: true }).click();
    await page.locator('#new-feature-reuse-label').fill('Copies');
    await page.locator('#new-feature-reuse-fits').selectOption('outer');
    await page.locator('#new-feature-reuse-reference').selectOption('a');
    await page.locator('#new-feature-reuse-target').selectOption('b');
    await page.locator('#new-feature-reuse-equal-dimensions').uncheck();
    expect(await page.locator('#add-feature-reuse-form').evaluate(form => [...form.elements]
      .filter(control => control.willValidate && !control.checkValidity())
      .map(control => ({ id: control.id, value: control.value, message: control.validationMessage })))).toEqual([]);
    const created = await author(() => page.getByRole('button', { name: 'Reuse features', exact: true }).click());
    await expect(page.locator('#feature-reuse-dialog')).toBeHidden();
    const owner = created.recipe.nodes.find(node => node.operation === 'feature_reuse');
    const children = created.recipe.nodes.filter(node => node.managed_by === owner.id);
    expect(created.recipe.output).toBe(children.at(-1).id);
    await expect(page.locator('.action-select').filter({ hasText: children.at(-1).label })).toHaveCount(1);

    const ownerItem = page.locator(`.managed-owner-summary[data-action-id="${owner.id}"]`);
    await ownerItem.click({ button: 'right' });
    await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Updated copies');
    const renamedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#rename-feature-form').getByRole('button', { name: 'Rename', exact: true }).click();
    expect((await renamedResponse).ok()).toBe(true);
    await expect(page.locator('#rename-feature-dialog')).toBeHidden();
    await ownerItem.click({ button: 'right' });
    const groupedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#feature-context-group').selectOption('group');
    expect((await groupedResponse).ok()).toBe(true);
    await ownerItem.dblclick();
    await expect(page.locator('#properties-title')).toHaveText('Updated copies');
    await page.locator('#feature-reuse-target').selectOption(['b', 'c']);
    const edited = await author(() => page.locator('#apply-properties').click());
    expect(edited.recipe.nodes.find(node => node.id === owner.id)).toMatchObject({
      label: 'Updated copies', group_id: 'group', target_selections: ['b', 'c'],
    });
    expect(edited.recipe.output).toBe(created.recipe.output);
    for (const child of children) expect(edited.recipe.nodes.find(node => node.managed_key === child.managed_key).id).toBe(child.id);

    // Exercise the existing equality shortcut, whose legacy toolbar button is hidden.
    const equal = await author(() => page.locator('#new-equal').evaluate(button => button.click()));
    const relation = equal.recipe.nodes.find(node => node.managed_by === owner.id && node.operation === 'equal_radii');
    expect(relation.surfaces).toHaveLength(3);
    expect(equal.recipe.output).toBe(created.recipe.output);
    await ownerItem.click({ button: 'right' });
    await page.locator('#feature-context-edit').click();
    await expect(page.locator('#feature-reuse-equal-dimensions')).toBeChecked();
    await page.locator('#feature-reuse-equal-dimensions').uncheck();
    const unequal = await author(() => page.locator('#apply-properties').click());
    expect(unequal.recipe.nodes.some(node => node.id === relation.id)).toBe(false);
    expect(unequal.recipe.output).toBe(created.recipe.output);
    const generated = page.locator(`.action-select[data-action-id="${children.at(-1).id}"]`);
    await generated.evaluate(element => {
      for (let parent = element.parentElement; parent; parent = parent.parentElement)
        if (parent.tagName === 'DETAILS') parent.open = true;
    });
    await generated.dblclick();
    await expect(page.locator('#properties-title')).toHaveText('Updated copies');
  } finally {
    await replace(original.recipe);
  }
});
