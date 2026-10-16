import { test, expect } from '@playwright/test';
import path from 'node:path';

async function ready(page) {
  const original = await (await page.request.get('/api/graph')).json();
  const axis = { id: 'layout_axis', label: 'Center', operation: 'axis', initial_parameters: [0, 0, 0, 0] };
  let graph = { ...original, recipe: { ...original.recipe, nodes: [...original.recipe.nodes, axis] },
    states: { ...original.states, [axis.id]: 'ready' }, evaluation_running: false };
  const submissions = [];
  await page.route(/\/api\/graph(?:\?.*)?$/, async route => {
    if (route.request().method() === 'POST') {
      const recipe = route.request().postDataJSON().recipe;
      submissions.push(recipe);
      graph = { ...graph, recipe, token: `layout-${submissions.length}`, revision: graph.revision + 1 };
    }
    await route.fulfill({ json: graph });
  });
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#startup-error')).toBeHidden();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  return submissions;
}

async function dock(page) {
  await page.locator('#dock-edit').click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.locator('#edit-popup')).toBeHidden();
}

async function expectReachable(control) {
  await control.scrollIntoViewIfNeeded();
  await expect.poll(() => control.evaluate(element => {
    const box = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
  })).toBe(true);
  await control.click({ trial: true });
}

async function expectOrderedActions(dialog, hint, submit, cancel) {
  await expect.poll(async () => {
    const [help, add, close] = await Promise.all([hint.boundingBox(), submit.boundingBox(), cancel.boundingBox()]);
    return !!help && !!add && !!close && help.y + help.height <= add.y && add.y + add.height <= close.y;
  }).toBe(true);
  for (const action of [submit, cancel]) await expectReachable(action);
  expect(await dialog.evaluate(element => {
    for (let parent = element; parent && parent !== document.body; parent = parent.parentElement)
      if (parent.scrollHeight > parent.clientHeight + 1 && ['auto', 'scroll'].includes(getComputedStyle(parent).overflowY)) return true;
    return false;
  })).toBe(true);
}

for (const docked of [false, true]) {
  for (const construction of ['contains_axis', 'parallel_to_axis', 'perpendicular_to_axis']) {
    test(`reference-plane ${construction} keeps help and actions reachable in constrained ${docked ? 'docked' : 'popup'} Edit`, async ({ page }) => {
      // Smaller CSS space also covers the reflow pressure of browser zoom.
      await page.setViewportSize(docked ? { width: 800, height: 448 } : { width: 1000, height: 560 });
      const submissions = await ready(page);
      await page.getByRole('button', { name: 'Plane', exact: true }).click();
      if (docked) await dock(page);
      const dialog = page.locator('#reference-plane-dialog'), hint = page.locator('#new-reference-plane-hint'),
        submit = dialog.getByRole('button', { name: 'Add plane', exact: true }),
        cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
      await page.locator('#new-reference-plane-construction').selectOption(construction);
      await expect(page.locator('#new-reference-plane-axis')).toHaveValue('layout_axis');
      await expect(hint).not.toHaveText('');
      expect(await hint.evaluate(element => element.getBoundingClientRect().height > parseFloat(getComputedStyle(element).lineHeight))).toBe(true);
      await expectOrderedActions(dialog, hint, submit, cancel);
      if (process.env.CREATION_LAYOUT_SCREENSHOTS) {
        await page.screenshot({ path: path.join(process.env.CREATION_LAYOUT_SCREENSHOTS,
          `plane-${construction}-${docked ? 'docked' : 'popup'}.png`) });
      }
      // A real Cancel click must reach the guard, not the submit behind it.
      if (construction !== 'contains_axis') page.once('dialog', confirmation => confirmation.accept());
      await cancel.click();
      await expect(dialog).toBeHidden();
      expect(submissions).toEqual([]);
    });
  }
}

test('Enter submits the rehosted plane form once and keeps native button ownership', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 560 });
  const submissions = await ready(page);
  await page.getByRole('button', { name: 'Plane', exact: true }).click();
  await dock(page);
  await page.locator('#new-reference-plane-construction').selectOption('perpendicular_to_axis');
  const dialog = page.locator('#reference-plane-dialog');
  expect(await dialog.getByRole('button', { name: 'Add plane', exact: true }).evaluate(button => button.form.id)).toBe('add-reference-plane-form');
  expect(await dialog.getByRole('button', { name: 'Cancel', exact: true }).evaluate(button => ({ type: button.type, form: button.form }))).toEqual({ type: 'button', form: null });
  await page.locator('#new-reference-plane-offset').fill('2');
  await page.locator('#new-reference-plane-offset').press('Enter');
  await expect(dialog).toBeHidden();
  expect(submissions).toHaveLength(1);
  expect(submissions[0].nodes.at(-1)).toMatchObject({ operation: 'reference_plane', axis: 'layout_axis',
    construction: 'perpendicular_to_axis', offset: 2, initial_angle_degrees: null });
});

test('another ordinary creation form keeps its help and sibling Cancel below its content', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 560 });
  const submissions = await ready(page);
  await page.getByRole('button', { name: 'Point', exact: true }).click();
  await dock(page);
  const dialog = page.locator('#point-dialog'), hint = page.locator('#add-point-form > p.hint'),
    submit = dialog.getByRole('button', { name: 'Add point', exact: true }),
    cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
  await expectOrderedActions(dialog, hint, submit, cancel);
  await cancel.click();
  await expect(dialog).toBeHidden();
  expect(submissions).toEqual([]);
});
