import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Inspect the real camera without adding a production debugging interface.
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: `${await response.text()}\nexport { camera, controls };` });
  });
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
});

async function direction(page) {
  return page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const { camera, controls } = await import('/app.js');
    return camera.position.clone().sub(controls.target).normalize().toArray();
  });
}

async function drag(page, dx, dy) {
  const box = await page.locator('#viewport canvas').boundingBox();
  // Use empty space and a little cross-axis jitter, as in a real mouse gesture.
  const x = box.x + box.width * .8, y = box.y + box.height * .7;
  await page.mouse.move(x, y);
  await page.mouse.down({ button: 'right' });
  const axis = await page.evaluate(async vertical => {
    const { camera } = await import('/app.js');
    const { Vector3 } = await import('three');
    return new Vector3(vertical ? 0 : 1, vertical ? 1 : 0, 0)
      .applyQuaternion(camera.quaternion).toArray();
  }, dy !== 0);
  const directions = [await direction(page)];
  for (let i = 1; i <= 8; i++) {
    await page.mouse.move(x + dx * i + (dy ? i % 2 : 0), y + dy * i + (dx ? i % 2 : 0));
    directions.push(await direction(page));
  }
  const position = await page.evaluate(async () => {
    const { camera, controls } = await import('/app.js');
    const before = { quaternion: camera.quaternion.clone(), position: camera.position.clone(),
      target: controls.target.clone() };
    for (let i = 0; i < 12; i++) controls.update();
    return { angle: before.quaternion.angleTo(camera.quaternion),
      position: before.position.distanceTo(camera.position), target: before.target.distanceTo(controls.target) };
  });
  expect(position.angle).toBeLessThan(1e-7);
  expect(position.position).toBeLessThan(1e-10);
  expect(position.target).toBeLessThan(1e-10);
  await page.mouse.up({ button: 'right' });
  return directions.slice(1).map((value, i) => Math.abs(value.reduce((sum, coordinate, index) =>
    sum + (coordinate - directions[i][index]) * axis[index], 0)));
}

test('releasing Alt during a held drag preserves the view and restores vertical rotation', async ({ page }) => {
  await page.getByRole('button', { name: 'Top', exact: true }).click();
  const box = await page.locator('#viewport canvas').boundingBox();
  const x = box.x + box.width * .8, y = box.y + box.height * .7;
  await page.mouse.move(x, y);
  await page.keyboard.down('Alt');
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(x + 8, y);
  const before = await direction(page);
  await page.keyboard.up('Alt');
  const after = await direction(page);
  for (let i = 0; i < 3; i++) expect(after[i]).toBeCloseTo(before[i], 8);
  await page.mouse.move(x + 9, y + 8);
  const moved = await direction(page);
  expect(Math.abs(moved[1] - after[1])).toBeGreaterThan(.01);
  await page.mouse.up({ button: 'right' });
});

for (const upright of [false, true]) {
  test(`Top view has smooth free rotation${upright ? ' after upright rotation' : ''}`, async ({ page }) => {
    await page.locator('#cursor-rotate').uncheck();
    const steps = [];
    for (const [dx, dy] of [[8, 0], [0, 8], [0, -8]]) {
      await page.getByRole('button', { name: 'Top', exact: true }).click();
      if (upright) {
        await page.keyboard.down('Alt');
        await drag(page, 2, 0);
        await page.keyboard.up('Alt');
      }
      steps.push(...await drag(page, dx, dy));
    }
    // Equal-length gestures should have comparable pitch/yaw response at every
    // step, including the first movement away from the pole.
    expect(Math.min(...steps)).toBeGreaterThan(.01);
    expect(Math.max(...steps) / Math.min(...steps)).toBeLessThan(1.5);
  });
}
