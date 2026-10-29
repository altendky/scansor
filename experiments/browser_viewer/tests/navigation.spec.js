import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Inspect the real camera without adding a production debugging interface.
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: `${await response.text()}\nexport { camera, controls, mesh };` });
  });
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
});

async function zoomPoint(page, hit = false) {
  return page.evaluate(async wantHit => {
    const { camera, controls, mesh } = await import('/app.js');
    const { Raycaster, Vector2 } = await import('three');
    const { viewPlaneAnchor } = await import('/navigation-math.js');
    const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
    camera.updateMatrixWorld(); mesh.updateMatrixWorld(true);
    const ray = new Raycaster();
    for (const y of [.35, .55, .75, .85]) for (const x of [.85, .7, .55, .4, .25, .1]) {
      const clientX = Math.round(box.left + x * box.width), clientY = Math.round(box.top + y * box.height);
      if (document.elementFromPoint(clientX, clientY) !== canvas) continue;
      const screen = [(clientX - box.left) / box.width * 2 - 1, 1 - (clientY - box.top) / box.height * 2];
      ray.setFromCamera(new Vector2(...screen), camera);
      const pick = ray.intersectObject(mesh, false)[0];
      if (!!pick === wantHit) return { x: clientX, y: clientY, hit: !!pick,
        anchor: (pick?.point || viewPlaneAnchor(ray.ray, camera, controls.target)).toArray(),
        screen };
    }
    throw new Error(`No visible ${wantHit ? 'mesh hit' : 'background miss'} in fixture`);
  }, hit);
}

async function cameraState(page, anchor = [0, 0, 0]) {
  return page.evaluate(async point => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const { camera, controls } = await import('/app.js');
    const { Vector3 } = await import('three');
    camera.updateMatrixWorld();
    return { distance: camera.position.distanceTo(controls.target),
      position: camera.position.toArray(), target: controls.target.toArray(),
      quaternion: camera.quaternion.toArray(), screen: new Vector3(...point).project(camera).toArray() };
  }, anchor);
}

async function silhouetteMiss(page) {
  const hit = await zoomPoint(page, true);
  return page.evaluate(async start => {
    const { camera, controls, mesh } = await import('/app.js');
    const { Raycaster, Vector2 } = await import('three');
    const { viewPlaneAnchor } = await import('/navigation-math.js');
    const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect(), ray = new Raycaster();
    for (const [dx, dy] of [[2, 0], [-2, 0], [0, 2], [0, -2]]) {
      for (let step = 1; step < 800; step++) {
        const x = start.x + dx * step, y = start.y + dy * step;
        if (document.elementFromPoint(x, y) !== canvas) break;
        const screen = [(x - box.left) / box.width * 2 - 1, 1 - (y - box.top) / box.height * 2];
        ray.setFromCamera(new Vector2(...screen), camera);
        if (!ray.intersectObject(mesh, false).length) return { x, y, screen,
          anchor: viewPlaneAnchor(ray.ray, camera, controls.target).toArray() };
      }
    }
    throw new Error('No silhouette transition in fixture');
  }, hit);
}

function stableZoom(before, after, inward) {
  if (inward) expect(after.distance).toBeLessThan(before.distance);
  else expect(after.distance).toBeGreaterThan(before.distance);
  for (const value of [...after.position, ...after.target, ...after.quaternion]) expect(Number.isFinite(value)).toBe(true);
  const dot = before.quaternion.reduce((sum, value, index) => sum + value * after.quaternion[index], 0);
  expect(Math.abs(dot)).toBeGreaterThan(1 - 1e-10);
}

for (const view of ['initial', 'Top', 'Side', 'rolled and panned']) {
  test(`background wheel zoom works in ${view} view without rotating`, async ({ page }) => {
    if (['Top', 'Side'].includes(view)) await page.getByRole('button', { name: view, exact: true }).click();
    if (view === 'rolled and panned') await page.evaluate(async () => {
      const { camera, controls } = await import('/app.js');
      const { Vector3 } = await import('three');
      const offset = new Vector3(1, -.5, .3);
      camera.position.add(offset); controls.target.add(offset);
      camera.up.applyAxisAngle(camera.getWorldDirection(new Vector3()), .4);
      controls.update();
    });
    for (const delta of [-4000, 4000, -120, 120, -.25, -.25, .25]) {
      const point = await zoomPoint(page);
      expect(point.hit).toBe(false);
      const before = await cameraState(page, point.anchor);
      await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, delta);
      const after = await cameraState(page, point.anchor);
      stableZoom(before, after, delta < 0);
      for (let i = 0; i < 2; i++) expect(after.screen[i]).toBeCloseTo(point.screen[i], 7);
    }
  });
}

test('on-object anchoring, option-off zoom, and line/page deltas remain usable', async ({ page }) => {
  let point = await zoomPoint(page, true), before = await cameraState(page, point.anchor);
  await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, -120);
  let after = await cameraState(page, point.anchor);
  stableZoom(before, after, true);
  for (let i = 0; i < 2; i++) expect(after.screen[i]).toBeCloseTo(point.screen[i], 7);
  await page.locator('#cursor-zoom').uncheck();
  point = await zoomPoint(page); before = await cameraState(page);
  await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, 120);
  after = await cameraState(page); stableZoom(before, after, false);
  expect(after.target).toEqual(before.target);
  await page.locator('#cursor-zoom').check();
  for (const [deltaMode, deltaY] of [[1, -3], [2, 1]]) {
    point = await zoomPoint(page); before = await cameraState(page, point.anchor);
    await page.locator('#viewport canvas').dispatchEvent('wheel', {
      clientX: point.x, clientY: point.y, deltaY, deltaMode, bubbles: true, cancelable: true,
    });
    after = await cameraState(page, point.anchor); stableZoom(before, after, deltaY < 0);
    for (let i = 0; i < 2; i++) expect(after.screen[i]).toBeCloseTo(point.screen[i], 7);
  }
});

test('releasing a left press outside the canvas restores wheel navigation', async ({ page }) => {
  const point = await zoomPoint(page), before = await cameraState(page);
  await page.mouse.move(point.x, point.y); await page.mouse.down();
  await page.mouse.move(5, 5); await page.mouse.up();
  await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, -120);
  stableZoom(before, await cameraState(page), true);
});

test('wheel anchoring stays stable across the silhouette and distant background', async ({ page }) => {
  for (const pick of [() => zoomPoint(page, true), () => silhouetteMiss(page), () => zoomPoint(page)]) {
    const point = await pick(), before = await cameraState(page, point.anchor);
    await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, -120);
    const after = await cameraState(page, point.anchor);
    stableZoom(before, after, true);
    for (let i = 0; i < 2; i++) expect(after.screen[i]).toBeCloseTo(point.screen[i], 7);
  }
});

test('a wheel event still cancels an unfinished painted selection', async ({ page }) => {
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  await page.getByRole('button', { name: 'Selection', exact: true }).click();
  await expect(page.locator('#selection-tools')).toBeVisible();
  await page.locator('#selection-shape').selectOption('rectangle');
  await page.locator('#selection-depth').selectOption('through_all');
  await page.locator('#tool').selectOption('replace');
  const before = await (await page.request.get('/api/graph')).json(), edits = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/graph') edits.push(request.url());
  });
  const point = await zoomPoint(page, true);
  await page.mouse.move(point.x, point.y); await page.mouse.down();
  await page.mouse.move(point.x + 10, point.y + 10);
  await expect(page.locator('#status')).toContainText('Selection preview');
  await page.mouse.wheel(0, -120);
  await expect(page.locator('#status')).toHaveText('Selection gesture cancelled.');
  await page.mouse.up();
  expect((await (await page.request.get('/api/graph')).json()).recipe).toEqual(before.recipe);
  expect(edits).toEqual([]);
});

for (const interruption of ['pointercancel', 'blur']) {
  test(`${interruption} restores navigation after a selection press`, async ({ page }) => {
    const point = await zoomPoint(page), before = await cameraState(page);
    await page.mouse.move(point.x, point.y); await page.mouse.down();
    if (interruption === 'blur') await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    else await page.locator('#viewport canvas').dispatchEvent('pointercancel', { pointerId: 1, bubbles: true });
    await page.mouse.wheel(0, -120);
    stableZoom(before, await cameraState(page), true);
    await page.mouse.up();
  });
}

for (const upright of [false, true]) {
  test(`blur ends a held ${upright ? 'upright' : 'free'} rotation and permits another gesture`, async ({ page }) => {
    const point = await zoomPoint(page);
    await page.mouse.move(point.x, point.y);
    if (upright) await page.keyboard.down('Alt');
    await page.mouse.down({ button: 'right' });
    await page.mouse.move(point.x + 8, point.y + 8);
    await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    await page.mouse.up({ button: 'right' });
    if (upright) await page.keyboard.up('Alt');
    const before = await cameraState(page);
    await page.mouse.wheel(0, -120);
    stableZoom(before, await cameraState(page), true);
    const rotation = await direction(page);
    await page.mouse.down({ button: 'right' });
    await page.mouse.move(point.x + 20, point.y + 20);
    await page.mouse.up({ button: 'right' });
    const moved = await direction(page);
    expect(moved.some((value, index) => Math.abs(value - rotation[index]) > .001)).toBe(true);
  });
}

test('Faces only with no visible face still zooms over the background', async ({ page }) => {
  await page.locator('.display-options summary').click();
  await page.locator('#faces-only').check();
  const point = await zoomPoint(page), before = await cameraState(page);
  await page.mouse.move(point.x, point.y); await page.mouse.wheel(0, -120);
  stableZoom(before, await cameraState(page), true);
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
