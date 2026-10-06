import { test, expect } from '@playwright/test';

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  await expect(page.locator('#startup-error')).toBeHidden();
}

// Discover a visible patch through the real hover/raycast path, without exposing
// app internals or hard-coding coordinates that change when panels are docked.
async function patch(page, label) {
  // Docking schedules renderer resizing on the next frame.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const point = await page.evaluate(label => {
    const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
    for (let y = box.y + 85; y < box.bottom - 55; y += 14) {
      for (let x = box.x + 10; x < box.right - 10; x += 14) {
        if (document.elementFromPoint(x, y) !== canvas) continue;
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: x, clientY: y, pointerId: 1 }));
        if (document.querySelector('#model-pick-message').textContent.includes(label)) return { x, y };
      }
    }
    return null;
  }, label);
  expect(point, `visible fitted patch: ${label}`).toBeTruthy();
  return point;
}

test('model picks use complete fit IDs for target and the existing neighbor checkbox', async ({ page }) => {
  const errors = [], evaluations = [], edits = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    if (request.url().endsWith('/evaluate')) evaluations.push(request.url());
    if (request.url().endsWith('/api/graph') || request.url().endsWith('/api/session')) edits.push(request.url());
  });
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await expect(page.locator('#pick-face-target')).toBeEnabled();
  await page.locator('#pick-face-target').click();
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'target');
  let end = await patch(page, 'End surface');
  // Dragging is not selecting, and empty space cannot change the target.
  const old = await page.locator('#build-faces-target').textContent();
  await page.mouse.move(end.x, end.y);
  await page.mouse.down();
  await page.mouse.move(end.x + 20, end.y + 20);
  await page.mouse.up();
  await expect(page.locator('#build-faces-target')).toHaveText(old);
  end = await patch(page, 'End surface');
  await page.mouse.click(end.x, end.y);
  await expect(page.locator('#build-faces-target')).toContainText('End surface');
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', '');
  await expect(page.locator('#pick-face-neighbor')).toBeEnabled();
  await page.locator('#pick-face-neighbor').click();
  const side = await patch(page, 'Outer surface');
  const checkbox = page.locator('.face-candidate > summary input').first();
  const initially = await checkbox.isChecked();
  await page.mouse.click(side.x, side.y);
  expect(await checkbox.isChecked()).toBe(!initially);
  await page.mouse.click(side.x, side.y);
  expect(await checkbox.isChecked()).toBe(initially);
  await page.keyboard.press('Escape');
  await expect(page.locator('.model-pick-hover')).toHaveCount(0);
  await expect(page.locator('#model-pick-hint')).toBeHidden();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  expect(evaluations).toEqual([]);
  expect(edits).toEqual([]);
  expect(errors).toEqual([]);
  if (process.env.MODEL_PICK_SCREENSHOT) {
    await page.locator('#pick-face-neighbor').click();
    await page.mouse.move(side.x, side.y);
    await page.screenshot({ path: process.env.MODEL_PICK_SCREENSHOT });
  }
});

test('relationship model picks preserve physical participant IDs and list eligibility', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Relationship…', exact: true }).click();
  await page.locator('#pick-relationship-participant').click();
  const side = await patch(page, 'Outer surface');
  const checkbox = page.locator('input[data-participant-id="side"]');
  await page.mouse.click(side.x, side.y);
  await expect(checkbox).toBeChecked();
  await page.mouse.click(side.x, side.y);
  await expect(checkbox).not.toBeChecked();
  await page.locator('#relationship-kind').selectOption('parallel_planes');
  await expect(checkbox).toHaveCount(0);
  await page.mouse.click(side.x, side.y);
  await expect(page.locator('#relationship-participants input:checked')).toHaveCount(0);
  await expect(page.locator('#model-pick-message')).toContainText('No eligible item');
  await page.mouse.move(side.x, side.y);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(side.x + 15, side.y + 15, { steps: 3 });
  await page.mouse.up({ button: 'right' });
  await expect(page.locator('#relationship-participants input:checked')).toHaveCount(0);
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'participant');
  await page.locator('#stop-model-picking').click();
  await page.locator('#viewport .display-options > summary').click();
  await page.locator('#faces-only').check();
  await expect(page.locator('#pick-relationship-participant')).toBeEnabled();
});

test('open Build faces lists and model picking agree after fit readiness, rename and deletion', async ({ page }) => {
  await ready(page);
  let graph = await (await page.request.get('/api/graph')).json();
  const original = structuredClone(graph);
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  const publish = async () => {
    const response = page.waitForResponse('**/api/graph/ensure');
    await page.locator('#evaluate-all').evaluate(button => button.click());
    await response;
    await expect(page.locator('#evaluate-all')).toBeEnabled();
  };
  const listed = async (name, count) => {
    await expect(page.locator('#build-faces-target-options [role=option]').filter({ hasText: name })).toHaveCount(count);
    await expect(page.locator('#build-faces-surfaces option').filter({ hasText: name })).toHaveCount(count);
  };
  await page.getByRole('button', { name: 'Build faces…', exact: true }).click();
  await page.locator('#pick-face-target').click();
  const end = await patch(page, 'End surface');
  await page.mouse.click(end.x, end.y);
  await expect(page.locator('#build-faces-target')).toContainText('End surface');
  await listed('End surface', 1);

  graph.states.end = 'stale';
  await publish();
  await listed('End surface', 0);
  await page.locator('#pick-face-target').click();
  await page.mouse.move(end.x, end.y);
  await expect(page.locator('#model-pick-message')).not.toContainText('End surface');
  await page.mouse.click(end.x, end.y);
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'target');
  await page.keyboard.press('Escape');
  await expect(page.locator('#build-faces-target')).toContainText('Unavailable: End surface');

  graph = structuredClone(original);
  graph.recipe.nodes.find(node => node.id === 'end').label = 'Renamed end';
  await publish();
  await listed('End surface', 0);
  await listed('Renamed end', 1);
  await page.locator('#pick-face-target').click();
  const renamed = await patch(page, 'Renamed end');
  await page.mouse.click(renamed.x, renamed.y);
  await expect(page.locator('#build-faces-target')).toContainText('Renamed end');

  graph.recipe.nodes = graph.recipe.nodes.filter(node => node.id !== 'end');
  delete graph.results.end;
  delete graph.states.end;
  await publish();
  await listed('Renamed end', 0);
  await page.locator('#pick-face-target').click();
  await page.mouse.move(renamed.x, renamed.y);
  await expect(page.locator('#model-pick-message')).not.toContainText('Renamed end');
  await page.mouse.click(renamed.x, renamed.y);
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'target');
  await page.keyboard.press('Escape');
  await expect(page.locator('#build-faces-error')).toContainText('Selected inputs unavailable');
  await expect(page.locator('#apply-build-faces')).toBeDisabled();
});

test('overlapping fit patches open an explicit chooser instead of guessing', async ({ page }) => {
  await ready(page);
  const graph = await (await page.request.get('/api/graph')).json();
  graph.results.end.ids = graph.results.side.ids;
  graph.results.fit.surfaces.end.ids = graph.results.fit.surfaces.side.ids;
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.reload();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('button', { name: 'Relationship…', exact: true }).click();
  await page.locator('#pick-relationship-participant').click();
  const point = await patch(page, '2 items here');
  await page.mouse.click(point.x, point.y);
  const chooser = page.locator('#model-pick-choices');
  await expect(chooser).toBeVisible();
  await expect(chooser.getByRole('button')).toHaveCount(2);
  await expect(page.locator('#relationship-participants input:checked')).toHaveCount(0);
  // A chooser must recheck eligibility even if a filter changes while open.
  await page.locator('#relationship-filter').evaluate(input => {
    input.value = 'Outer surface';
    input.dispatchEvent(new Event('input'));
  });
  await chooser.getByRole('button', { name: /End surface/ }).click();
  await expect(page.locator('#relationship-participants input:checked')).toHaveCount(0);
  await page.locator('#relationship-filter').fill('');
  await page.mouse.click(point.x, point.y);
  await expect(chooser).toBeVisible();
  await chooser.getByRole('button', { name: /End surface/ }).click();
  await expect(page.locator('input[data-participant-id="end"]')).toBeChecked();
  await expect(page.locator('input[data-participant-id="side"]')).not.toBeChecked();
  await expect(chooser).toBeHidden();
});

test('modal multi-input picks preserve the draft, toggle selections and do not apply or select tree features', async ({ page }) => {
  const errors = [], edits = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/(graph|session)$|\/evaluate$/.test(request.url())) edits.push(request.url());
  });
  await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
  await page.locator('#new-fit-label').fill('My uncommitted fit');
  await page.locator('#new-fit-inputs').selectOption([]);
  await page.locator('[data-pick-control="new-fit-inputs"]').click();
  await expect(page.locator('#fit-dialog')).toBeHidden();
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'field');
  const create = page.getByRole('button', { name: 'Surface fit', exact: true });
  expect(await create.evaluate(button => !!button.closest('[inert]'))).toBe(true);
  const box = await create.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.locator('#new-fit-label')).toHaveValue('My uncommitted fit');
  const point = await patch(page, 'Outer band');
  await page.mouse.click(point.x, point.y);
  expect(await page.locator('#new-fit-inputs').evaluate(select => [...select.selectedOptions].map(option => option.value))).toEqual(['outer_band']);
  await page.mouse.click(point.x, point.y);
  expect(await page.locator('#new-fit-inputs').evaluate(select => [...select.selectedOptions].map(option => option.value))).toEqual([]);
  await page.locator('#stop-model-picking').click();
  await expect(page.locator('#fit-dialog')).toBeVisible();
  expect(await create.evaluate(button => !!button.closest('[inert]'))).toBe(false);
  await expect(page.locator('#new-fit-label')).toHaveValue('My uncommitted fit');
  expect(await page.locator('#fit-dialog').evaluate(dialog => dialog.matches(':modal'))).toBe(true);
  await page.locator('[data-pick-control="new-fit-inputs"]').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#fit-dialog')).toBeVisible();
  await expect(page.locator('#new-fit-label')).toHaveValue('My uncommitted fit');
  await expect(page.locator('#action-list .selected')).toHaveCount(0);
  expect(edits).toEqual([]);
  expect(errors).toEqual([]);
});

test('datum guide picks return a modal single-input form with its draft and enum unchanged', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  const graph = await (await page.request.get('/api/graph')).json();
  const fixtures = [
    { node: { id: 'origin', label: 'Pick origin', operation: 'point', initial_coordinates: [0, 0, 0] }, result: { point_display: [0, 0, 0] } },
    { node: { id: 'other_point', label: 'Other point', operation: 'point', initial_coordinates: [1.8, 1.8, 2] }, result: { point_display: [1.8, 1.8, 2] } },
    { node: { id: 'axis', label: 'Pick axis', operation: 'axis', initial_parameters: [0, 0, 0, 0] }, result: { point_display: [0, 0, 0], axis_display: [0, 0, 1] } },
  ];
  for (const { node, result } of fixtures) {
    graph.recipe.nodes.push(node);
    graph.results[node.id] = result;
    graph.states[node.id] = 'ready';
  }
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET' ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.reload();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('button', { name: 'Frame', exact: true }).click();
  await page.locator('#new-frame-label').fill('Uncommitted frame');
  await page.locator('#new-frame-origin').selectOption('other_point');
  await page.locator('[data-pick-control="new-frame-origin"]').click();
  await expect(page.locator('#frame-dialog')).toBeHidden();
  const origin = await patch(page, 'Pick origin');
  await page.mouse.click(origin.x, origin.y);
  await expect(page.locator('#frame-dialog')).toBeVisible();
  await expect(page.locator('#new-frame-origin')).toHaveValue('origin');
  await expect(page.locator('#new-frame-label')).toHaveValue('Uncommitted frame');
  await expect(page.locator('[data-pick-control="new-frame-primary-output"]')).toHaveCount(0);
  await page.locator('[data-pick-control="new-frame-primary-reference"]').click();
  const axis = await patch(page, 'Pick axis');
  await page.mouse.click(axis.x, axis.y);
  await expect(page.locator('#frame-dialog')).toBeVisible();
  await expect(page.locator('#new-frame-primary-reference')).toHaveValue('axis');
  await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', '');
  expect(errors).toEqual([]);
});

test('native picking rejects hidden/disabled inputs, preserves multi-toggle and dispatches existing handlers', async ({ page }) => {
  await ready(page);
  const result = await page.evaluate(async () => {
    const { commitNativePick, nativePickOptions } = await import('/model-picking.js');
    const host = document.createElement('div');
    host.innerHTML = '<label>Dynamic boundary<select data-boundary-intersection multiple><option value="first">First</option><option value="locked" disabled>Locked</option><optgroup disabled><option value="grouped">Grouped</option></optgroup></select></label>';
    document.body.append(host);
    const control = host.querySelector('select'), events = [];
    control.oninput = () => events.push('input');
    control.onchange = () => events.push('change');
    const keys = nativePickOptions(control).map(option => option.key);
    const first = commitNativePick(control, 'first'), selected = control.selectedOptions.length;
    const toggle = commitNativePick(control, 'first'), deselected = control.selectedOptions.length;
    const locked = commitNativePick(control, 'locked');
    host.hidden = true;
    const hidden = commitNativePick(control, 'first');
    host.hidden = false;
    control.disabled = true;
    const disabled = commitNativePick(control, 'first');
    await new Promise(resolve => setTimeout(resolve, 0));
    const button = host.querySelector('.model-field-pick');
    const bound = !!button && button.disabled;
    host.remove();
    return { keys, first, selected, toggle, deselected, locked, hidden, disabled, events, bound };
  });
  expect(result).toEqual({ keys: ['first'], first: true, selected: 1, toggle: true, deselected: 0,
    locked: false, hidden: false, disabled: false, events: ['input', 'change', 'input', 'change'], bound: true });
});

test('faces-only picks retain source face and exact solved surface identities under a sewn body', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  const graph = await (await page.request.get('/api/graph')).json();
  const preview = { positions: [-2, -2, 2, 2, -2, 2, 2, 2, 2, -2, 2, 2], indices: [0, 1, 2, 0, 2, 3] };
  graph.recipe.nodes.push(
    { id: 'pick_face', operation: 'arranged_face', label: 'Pick source face', surface: { feature: 'fit', surface: 'end' }, cutters: [], domains: [] },
    { id: 'pick_body', operation: 'body', label: 'Pick sewn body', faces: ['pick_face'], sewing_tolerance: 1e-5 },
  );
  graph.states.pick_face = graph.states.pick_body = 'ready';
  graph.results.pick_face = { bounded: true, bounds: null, loops: [], preview };
  graph.results.pick_body = { kind: 'body', preview };
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET' ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.reload();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.locator('#viewport .display-options > summary').click();
  await page.locator('#faces-only').check();
  await page.getByRole('button', { name: 'Body…', exact: true }).click();
  await page.locator('[data-pick-control="new-body-face-choices"]').click();
  const point = await patch(page, 'Pick source face');
  const checkbox = page.locator('#new-body-face-choices input[value="pick_face"]');
  const initial = await checkbox.isChecked();
  await page.mouse.click(point.x, point.y);
  expect(await checkbox.isChecked()).toBe(!initial);
  await page.locator('#stop-model-picking').click();
  await page.locator('[data-close-dialog="body-dialog"]').click();
  await page.getByRole('button', { name: 'Trimmed face', exact: true }).click();
  await page.locator('[data-pick-control="new-face-surface"]').click();
  const surface = await patch(page, 'End surface');
  await page.mouse.click(surface.x, surface.y);
  // Do not silently substitute the independently fitted seed plane context.
  await expect(page.locator('#model-pick-choices')).toBeHidden();
  await expect(page.locator('#trimmed-face-dialog')).toBeVisible();
  expect(JSON.parse(await page.locator('#new-face-surface').inputValue())).toEqual({ feature: 'fit', surface: 'end' });
  expect(errors).toEqual([]);
  if (process.env.MODEL_PICK_SCREENSHOT) await page.screenshot({ path: process.env.MODEL_PICK_SCREENSHOT });
});
