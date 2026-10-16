import { test, expect } from '@playwright/test';

const row = (page, id) => page.locator(`#action-list .action-select[data-action-id="${id}"]`);
const status = (page, id) => page.locator(`#action-list .feature-evaluate[data-evaluate-id="${id}"]`);

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
}

test('status mouse and keyboard activation evaluate only that feature without selecting, editing or dragging it', async ({ page }) => {
  const errors = [], requests = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    if (request.url().endsWith('/api/graph/evaluate')) requests.push(request.postDataJSON());
    if (request.url().endsWith('/api/graph')) writes.push(request.postDataJSON());
  });
  await row(page, 'side').click();
  await row(page, 'side').click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await page.locator('#axial-start').fill('-1');
  for (const [index, gesture] of ['mouse', 'Enter', 'Space'].entries()) {
    const requested = page.waitForRequest('**/api/graph/evaluate');
    if (gesture === 'mouse') await status(page, 'end').click();
    else {
      await expect(status(page, 'end')).toBeFocused();
      await page.keyboard.press(gesture);
    }
    expect((await requested).postDataJSON().target).toBe('end');
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    expect(requests).toHaveLength(index + 1);
    await expect(row(page, 'side')).toHaveClass(/feature-selected/);
    await expect(row(page, 'end')).not.toHaveClass(/feature-selected/);
    await expect(page.locator('#properties-title')).toHaveText('Outer surface');
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    await expect(status(page, 'end')).toBeFocused();
  }
  const from = await status(page, 'end').boundingBox(), to = await row(page, 'side').boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + 2, { steps: 12 });
  await page.mouse.up();
  await expect(page.locator('.dragging, .drop-before, .drop-after')).toHaveCount(0);
  expect(writes).toEqual([]);
  // Context-menu Evaluate and the global action retain their original paths.
  await row(page, 'end').click({ button: 'right' });
  let requested = page.waitForRequest('**/api/graph/evaluate');
  await page.locator('#feature-context-evaluate').click();
  expect((await requested).postDataJSON().target).toBe('end');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  requested = page.waitForRequest('**/api/graph/evaluate');
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  expect((await requested).postDataJSON().all_actions).toBe(true);
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  expect(errors).toEqual([]);
});

test('status controls reject duplicate work while busy and leave ineligible indicators informational', async ({ page }) => {
  await ready(page);
  await expect(row(page, 'scan').locator('.feature-evaluate')).toHaveCount(0);
  await expect(row(page, 'outer_band').locator('.feature-evaluate')).toHaveCount(0);
  let release, entered = false, count = 0;
  const held = new Promise(resolve => { release = resolve; });
  await page.route('**/api/graph/evaluate', async route => {
    count++;
    entered = true;
    await held;
    await route.continue();
  });
  try {
    await status(page, 'end').click();
    await expect.poll(() => entered).toBe(true);
    await expect(status(page, 'end')).toBeDisabled();
    await expect(status(page, 'side')).toBeDisabled();
    // Even synthetic activation cannot bypass the live lock guard.
    await status(page, 'end').evaluate(button => button.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await status(page, 'side').evaluate(button => button.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(count).toBe(1);
  } finally { release(); }
  await expect(status(page, 'end')).toBeEnabled();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  expect(count).toBe(1);
});
