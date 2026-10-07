import { test, expect } from '@playwright/test';

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible({ timeout: 15000 });
  await expect(page.locator('#evaluate-all')).toBeEnabled();
}

test('multi input shows selected rows and eligible checked choices that stay open while toggling', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
  const field = page.locator('[data-selection-control="new-fit-inputs"]');
  await page.locator('#new-fit-inputs').selectOption([]);
  await expect(field.locator('.selection-field-row')).toHaveCount(0);
  expect((await field.boundingBox()).height).toBeGreaterThanOrEqual(58);
  await field.getByRole('button', { name: 'Show Selections choices', exact: true }).click();
  const menu = field.getByRole('menu', { name: 'Selections choices', exact: true });
  const outer = menu.getByRole('menuitemcheckbox', { name: 'Outer band', exact: true });
  await expect(outer).toHaveAttribute('aria-checked', 'false');
  await outer.click();
  await expect(menu).toBeVisible();
  await expect(outer).toHaveAttribute('aria-checked', 'true');
  await expect(field.locator('.selection-field-row')).toHaveText(['Outer band×']);
  expect(await page.locator('#new-fit-inputs').evaluate(input => [...input.selectedOptions].map(option => option.value)))
    .toEqual(['outer_band']);
  await outer.click();
  await expect(menu).toBeVisible();
  await expect(outer).toHaveAttribute('aria-checked', 'false');
  await expect(field.locator('.selection-field-row')).toHaveCount(0);
  await outer.click();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(page.locator('#fit-dialog')).toBeVisible();
  await field.getByRole('button', { name: 'Remove Outer band', exact: true }).click();
  await expect(field.locator('.selection-field-row')).toHaveCount(0);
  await field.getByRole('button', { name: 'Pick Selections in model', exact: true }).click();
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'field');
  await expect(page.locator('#fit-dialog')).toBeVisible();
  await expect(page.locator('#new-fit-label')).toBeVisible();
  const row = page.locator('#action-list .action-select[data-action-id="outer_band"]');
  await row.click();
  await expect(field.locator('.selection-field-row')).toHaveText(['Outer band×']);
  await expect(page.locator('#action-list .selected')).toHaveCount(0);
  if (process.env.SELECTION_FIELD_SCREENSHOT) await page.screenshot({ path: process.env.SELECTION_FIELD_SCREENSHOT });
  await page.locator('#fit-dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('#fit-dialog')).toBeHidden();
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', '');
  expect(await page.getByRole('button', { name: 'Surface fit', exact: true })
    .evaluate(button => !!button.closest('[inert]'))).toBe(false);
  expect(errors).toEqual([]);
});

test('single input dropdown excludes ineligible items, checks its selected value and closes on selection', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  const field = page.locator('#build-faces-target-field');
  await field.getByRole('button', { name: 'Show Surface choices', exact: true }).click();
  const menu = page.getByRole('menu', { name: 'Surface choices', exact: true });
  const target = menu.getByRole('menuitemradio', { name: /End surface/ });
  await target.click();
  await expect(menu).toBeHidden();
  await expect(page.locator('#build-faces-target')).toContainText('End surface');
  await field.getByRole('button', { name: 'Show Surface choices', exact: true }).click();
  await expect(menu.getByRole('menuitemradio', { name: /End surface/ })).toHaveAttribute('aria-checked', 'true');
  await expect(menu.getByRole('menuitemradio', { name: /Outer surface/ })).toHaveAttribute('aria-checked', 'false');
  await page.keyboard.press('Escape');
  await field.getByRole('button', { name: 'Pick Surface in model', exact: true }).click();
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'target');
  await page.keyboard.press('Escape');
  await page.locator('#close-build-faces').click();
});

test('dynamic geometry dropdown supports keyboard choices, empty eligibility and current selections', async ({ page }) => {
  await ready(page);
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'dynamic-selection-field';
    host.style.cssText = 'position:fixed;top:80px;left:20px;width:280px;z-index:20';
    host.innerHTML = '<label>Boundary<select data-boundary-intersection><option value="">Choose an output…</option><option value="a">First</option><option value="b" disabled>Unavailable</option><optgroup disabled><option value="c">Grouped unavailable</option></optgroup><option value="d">Last</option></select></label>';
    document.body.append(host);
  });
  const host = page.locator('#dynamic-selection-field');
  const arrow = host.getByRole('button', { name: 'Show Boundary choices', exact: true });
  await arrow.focus();
  await page.keyboard.press('ArrowDown');
  const menu = host.getByRole('menu', { name: 'Boundary choices', exact: true });
  await expect(menu.getByRole('menuitemradio')).toHaveText(['First', 'Last']);
  await expect(menu.getByRole('menuitemradio', { name: 'First', exact: true })).toBeFocused();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await expect(menu).toBeHidden();
  await expect(host.locator('select')).toHaveValue('d');
  await expect(host.getByRole('button', { name: 'Pick Boundary in model', exact: true })).toHaveText('Last');
  await page.evaluate(() => {
    document.querySelector('#dynamic-selection-field select').replaceChildren(new Option('Choose an output…', ''));
  });
  await arrow.click();
  await expect(menu).toContainText('No eligible choices');
  await expect(menu.getByRole('menuitemradio')).toHaveCount(0);
  await host.evaluate(element => element.remove());
});

test('unavailable selected multi inputs remain visible and removable but are excluded from the dropdown', async ({ page }) => {
  await ready(page);
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'retained-selection-field';
    host.style.cssText = 'position:fixed;top:80px;left:20px;width:280px;z-index:20';
    host.innerHTML = '<label>Retained inputs<select data-boundary-intersection multiple><option value="saved" disabled selected>Unavailable saved input</option><option value="ready">Ready input</option></select></label>';
    document.body.append(host);
  });
  const host = page.locator('#retained-selection-field');
  await expect(host.locator('.selection-field-row')).toHaveText(['Unavailable saved input×']);
  await host.getByRole('button', { name: 'Show Retained inputs choices', exact: true }).click();
  const menu = host.getByRole('menu');
  await expect(menu.getByRole('menuitemcheckbox')).toHaveText(['Ready input']);
  await page.keyboard.press('Escape');
  await host.getByRole('button', { name: 'Remove Unavailable saved input', exact: true }).click();
  await expect(host.locator('.selection-field-row')).toHaveCount(0);
  expect(await host.locator('select').evaluate(select => select.selectedOptions.length)).toBe(0);
  await host.evaluate(element => element.remove());
});

test('choosing the checked single target closes its dropdown without replacing a draft', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await page.locator('#build-faces-options > summary').click();
  await page.locator('#build-faces-label').fill('Retained face draft');
  const target = await page.locator('#build-faces-target').textContent();
  await page.getByRole('button', { name: 'Show Surface choices', exact: true }).click();
  const menu = page.getByRole('menu', { name: 'Surface choices', exact: true });
  await menu.locator('[aria-checked="true"]').click();
  await expect(menu).toBeHidden();
  await expect(page.locator('#build-faces-label')).toHaveValue('Retained face draft');
  await expect(page.locator('#build-faces-target')).toHaveText(target);
  await page.locator('#close-build-faces').click();
});
