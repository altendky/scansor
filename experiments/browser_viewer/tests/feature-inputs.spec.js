import { test, expect } from '@playwright/test';

const reference = (feature, output, context = feature) => ({ feature, output, context });
const key = (feature, output, context = feature) => JSON.stringify(reference(feature, output, context));

async function guidePoint(page, label) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const point = await page.evaluate(label => {
    const canvas = document.querySelector('#viewport canvas'), box = canvas.getBoundingClientRect();
    for (let y = box.y + 85; y < box.bottom - 55; y += 8) {
      for (let x = box.x + 10; x < box.right - 10; x += 8) {
        if (document.elementFromPoint(x, y) !== canvas) continue;
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: x, clientY: y, pointerId: 1 }));
        if (document.querySelector('#model-pick-message').textContent.includes(label)) return { x, y };
      }
    }
    return null;
  }, label);
  expect(point, `visible output guide: ${label}`).toBeTruthy();
  return point;
}

test('real backend output choices persist explicit point and plane meanings through create and edit', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  const original = await (await page.request.get('/api/graph')).json(),
    read = async () => (await page.request.get('/api/graph')).json(),
    replace = async recipe => {
      const current = await read(), response = await page.request.post('/api/graph', {
        headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe },
      });
      expect(response.ok(), response.ok() ? undefined : await response.text()).toBe(true);
      return response.json();
    },
    save = async action => {
      const response = page.waitForResponse(response => response.url().endsWith('/api/graph') && response.request().method() === 'POST');
      await action();
      const saved = await response;
      expect(saved.ok(), saved.ok() ? undefined : await saved.text()).toBe(true);
      return saved.json();
    };
  try {
    const source = original.recipe.nodes.find(node => node.operation === 'source'),
      samples = [...new Set(original.recipe.nodes.filter(node => node.operation === 'selection').flatMap(node => node.ids))].sort((a, b) => a - b),
      observations = samples.filter((_, index) => index % Math.max(1, Math.floor(samples.length / 200)) === 0);
    await replace({ schema_version: 2, nodes: [source,
      { id: 'single', label: 'Single scan node', operation: 'selection', source: source.id, ids: [samples[0]] },
      { id: 'observations', label: 'Sphere observations', operation: 'selection', source: source.id, ids: observations },
      { id: 'sphere', label: 'Sphere provider', operation: 'fit', kind: 'sphere', selections: ['observations'] },
      { id: 'plane_fit', label: 'Planar provider', operation: 'fit', kind: 'plane', selections: ['observations'] },
      { id: 'other', label: 'Other point', operation: 'point', initial_coordinates: [1.8, 1.8, 2] },
      { id: 'direction', label: 'Datum axis', operation: 'axis', initial_parameters: [0, 0, 0, 0] },
      { id: 'datum', label: 'Datum plane', operation: 'reference_plane', axis: 'direction', construction: 'contains_axis', initial_angle_degrees: 0 },
    ], output: 'sphere' });
    let state = await read();
    const ensured = await page.request.post('/api/graph/ensure', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: state.token, targets: ['sphere', 'plane_fit', 'single', 'other', 'datum', 'direction'] },
    });
    expect(ensured.ok(), ensured.ok() ? undefined : await ensured.text()).toBe(true);
    await expect.poll(async () => {
      state = await read();
      return state.evaluation_running;
    }).toBe(false);
    expect(state.states.sphere, state.errors.sphere).toBe('ready');
    const center = state.input_requirements.point.choices.find(output => output.reference.feature === 'sphere');
    expect(center.reference).toEqual(reference('sphere', 'center'));
    await page.reload();
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Frame', exact: true }).click();
    await page.locator('#new-frame-label').fill('Named output frame');
    await expect(page.locator('#new-frame-primary-reference option').filter({ hasText: 'Planar provider' })).toHaveCount(1);
    await expect(page.locator('#new-frame-primary-reference option').filter({ hasText: 'Datum plane' })).toHaveCount(1);
    await page.locator('#new-frame-origin').selectOption(key('other', 'point'));
    await page.locator('[data-pick-control="new-frame-origin"]').click();
    const centerGuide = await guidePoint(page, 'Sphere provider — center');
    await page.mouse.click(centerGuide.x, centerGuide.y);
    await expect(page.locator('#frame-dialog')).toBeVisible();
    await expect(page.locator('#new-frame-origin')).toHaveValue(key('sphere', 'center'));
    await expect(page.locator('#new-frame-label')).toHaveValue('Named output frame');
    await page.locator('#new-frame-primary-reference').selectOption(key('datum', 'plane'));
    await page.locator('#new-frame-secondary-reference').selectOption(key('direction', 'axis'));
    let saved = await save(() => page.locator('#add-frame-form').getByRole('button', { name: 'Add frame', exact: true }).click());
    const frame = saved.recipe.nodes.find(node => node.operation === 'frame');
    expect(frame).toMatchObject({ origin_point: reference('sphere', 'center'),
      primary_reference: reference('datum', 'plane'), secondary_reference: reference('direction', 'axis') });
    await expect(page.locator('#frame-origin')).toHaveValue(key('sphere', 'center'));
    await page.locator('#action-label').fill('Edited named frame');
    saved = await save(() => page.locator('#apply-properties').click());
    expect(saved.recipe.nodes.find(node => node.id === frame.id).origin_point).toEqual(frame.origin_point);

    await page.getByRole('button', { name: 'Axis', exact: true }).click();
    await page.locator('#new-axis-mode').selectOption('points');
    await page.locator('#new-axis-point-a').selectOption(key('single', 'single_node'));
    await page.locator('#new-axis-point-b').selectOption(key('other', 'point'));
    saved = await save(() => page.locator('#add-axis-form').getByRole('button', { name: 'Add axis', exact: true }).click());
    expect(saved.recipe.nodes.find(node => node.operation === 'axis' && node.source_points).source_points)
      .toEqual([reference('single', 'single_node'), reference('other', 'point')]);

    await page.getByRole('button', { name: 'Scale', exact: true }).click();
    await page.locator('#new-scale-distance-rows [data-field="first"]').first().selectOption(key('sphere', 'center'));
    await page.locator('#new-scale-distance-rows [data-field="second"]').first().selectOption(key('single', 'single_node'));
    saved = await save(() => page.locator('#add-scale-form').getByRole('button', { name: 'Add scale', exact: true }).click());
    expect(saved.recipe.nodes.find(node => node.operation === 'scale').distances[0]).toMatchObject({
      first_point: reference('sphere', 'center'), second_point: reference('single', 'single_node'),
    });
    await page.reload();
    expect((await read()).recipe.nodes.find(node => node.id === frame.id).primary_reference).toEqual(frame.primary_reference);
  } finally { await replace(original.recipe); }
});

test('readiness refresh preserves an edited frame draft and keeps native and model eligibility identical', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  const original = await (await page.request.get('/api/graph')).json(),
    read = async () => (await page.request.get('/api/graph')).json(),
    replace = async recipe => {
      const current = await read(), response = await page.request.post('/api/graph', {
        headers: { 'X-Scansor-Request': '1' }, data: { token: current.token, recipe },
      });
      expect(response.ok(), response.ok() ? undefined : await response.text()).toBe(true);
    };
  try {
    await replace({ schema_version: 2, nodes: [original.recipe.nodes.find(node => node.operation === 'source'),
      { id: 'origin', label: 'Origin provider', operation: 'point', initial_coordinates: [0, 0, 0] },
      { id: 'axis', label: 'Direction provider', operation: 'axis', initial_parameters: [0, 0, 0, 0] },
      { id: 'plane', label: 'Plane provider', operation: 'reference_plane', axis: 'axis', construction: 'contains_axis', initial_angle_degrees: 0 },
      { id: 'frame', label: 'Retained frame', operation: 'frame', origin_point: 'origin',
        primary_reference: 'axis', primary_output_axis: '+Z', secondary_reference: 'plane', secondary_output_axis: '+X' },
    ], output: 'frame' });
    let graph = await read();
    await page.request.post('/api/graph/ensure', { headers: { 'X-Scansor-Request': '1' },
      data: { token: graph.token, targets: ['frame'] } });
    await expect.poll(async () => (await read()).states.frame).toBe('ready');
    graph = await read();
    await page.reload();
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await page.locator('.action-select[data-action-id="frame"]').click();
    await page.locator('#action-label').fill('Uncommitted frame name');
    await page.locator('#frame-primary-output').selectOption('-Y');
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
    const originOutput = graph.input_requirements.point.choices.find(output => output.reference.feature === 'origin');
    graph = structuredClone(graph);
    graph.states.origin = 'stale';
    graph.input_requirements.point = { choices: [], unavailable: [{ ...originOutput,
      availability: 'stale', reason: 'Reevaluate its changed provider' }] };
    await publish();
    await expect(page.locator('#frame-origin')).toHaveValue(key('origin', 'point'));
    await expect(page.locator('#frame-origin option:checked')).toHaveJSProperty('disabled', true);
    await expect(page.locator('#frame-origin option:checked')).toContainText('Reevaluate');
    await expect(page.locator('#action-label')).toHaveValue('Uncommitted frame name');
    await expect(page.locator('#frame-primary-output')).toHaveValue('-Y');
    const modelOptions = await page.locator('#frame-origin').evaluate(async control => {
      const { nativePickOptions } = await import('/model-picking.js');
      return nativePickOptions(control);
    });
    expect(modelOptions).toEqual([]);
    await expect(page.locator('[data-pick-control="frame-origin"]')).toBeDisabled();
    graph.states.origin = 'ready';
    graph.recipe.nodes.find(node => node.id === 'origin').label = 'Renamed origin';
    graph.input_requirements.point = { choices: [{ ...originOutput, label: 'Renamed origin — point' }], unavailable: [] };
    await publish();
    await expect(page.locator('#frame-origin')).toHaveValue(key('origin', 'point'));
    await expect(page.locator('#frame-origin option:checked')).toHaveJSProperty('disabled', false);
    await expect(page.locator('#frame-origin option:checked')).toContainText('Renamed origin');
    await expect(page.locator('#action-label')).toHaveValue('Uncommitted frame name');
    await expect(page.locator('[data-pick-control="frame-origin"]')).toBeEnabled();
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
    await replace(original.recipe);
  }
});
