import { test, expect } from '@playwright/test';

const field = (page, id) => page.locator(`.selection-field[data-selection-control="${id}"]`);

async function jointScene(page) {
  const original = await (await page.request.get('/api/graph')).json();
  const axes = ['center', 'other_axis'].map((id, index) => ({ id, label: index ? 'Other axis' : 'Center',
    operation: 'axis', initial_parameters: [0, 0, 0, 0] }));
  const graph = { ...original, evaluation_running: false,
    recipe: { ...original.recipe, groups: [], output: 'side',
      nodes: [...original.recipe.nodes.filter(node => ['source', 'selection', 'fit'].includes(node.operation)), ...axes] },
    results: {}, derived: {}, errors: {} };
  graph.states = Object.fromEntries(graph.recipe.nodes.map(node => [node.id, 'ready']));
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.fulfill({ json: graph }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.goto('/');
  await expect(page.locator('#startup-error')).toBeHidden();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  const publish = async () => {
    graph.revision++;
    await page.locator('#evaluate-all').evaluate(button => button.click());
    await expect(page.locator('#evaluate-all')).toBeEnabled();
  };
  return { graph, publish };
}

test('empty joint explains binding and ordering, then snapshots refresh factors without losing drafts', async ({ page }) => {
  const { graph, publish } = await jointScene(page);
  await page.getByRole('button', { name: 'Joint', exact: true }).click();
  await page.locator('#new-axis-solve-label').fill('My uncommitted joint');
  const axis = field(page, 'new-axis-solve-axis');
  await axis.locator('.selection-field-dropdown').click();
  await axis.getByRole('menuitemradio', { name: 'Other axis', exact: true }).click();
  const factors = field(page, 'new-axis-solve-factors'), feedback = factors.locator('.selection-field-feedback');
  await expect(factors.locator('.model-field-pick')).toBeDisabled();
  await expect(feedback).toContainText('No eligible fits and relationships');
  await expect(feedback).toContainText('Reference geometry');
  await expect(feedback).toContainText('Create a Coaxial relationship');
  await expect(feedback).toContainText('axis must appear before those fits');
  await factors.locator('.selection-field-dropdown').click();
  await expect(factors.getByRole('menu')).toContainText('Creating an axis does not bind existing fits');
  await page.keyboard.press('Escape');

  const standalone = graph.recipe.nodes.find(node => node.id === 'side');
  const first = { ...standalone, id: 'bound_center', label: 'Center cone', axis: 'center' },
    second = { ...standalone, id: 'bound_other', label: 'Other cone', axis: 'other_axis' };
  graph.recipe.nodes.push(first, second);
  graph.states[first.id] = graph.states[second.id] = 'ready';
  await publish();
  await expect(page.locator('#new-axis-solve-axis')).toHaveValue('other_axis');
  await expect(page.locator('#new-axis-solve-label')).toHaveValue('My uncommitted joint');
  await expect(feedback).not.toContainText('No eligible');
  await expect(feedback).toContainText('no pickable 3D geometry');
  await expect(page.locator('#new-axis-solve-factors option')).toHaveCount(1);
  await expect(page.locator('#new-axis-solve-factors option')).toHaveText('Other cone');
  await expect(page.locator('#new-axis-solve-factors')).toHaveValues([]);
  await factors.locator('.selection-field-dropdown').click();
  await factors.getByRole('menuitemcheckbox', { name: 'Other cone', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#new-axis-solve-factors')).toHaveValues(['bound_other']);

  second.axis = 'center';
  await publish();
  await expect(feedback).toContainText('No eligible');
  await expect(factors.locator('.selection-field-selected')).toContainText('Unavailable: Other cone');
  await expect(page.locator('#new-axis-solve-factors')).toHaveValues(['bound_other']);
  await expect(page.locator('#new-axis-solve-factors option:checked')).toHaveJSProperty('disabled', true);
  second.axis = 'other_axis';
  await publish();
  await expect(page.locator('#new-axis-solve-factors')).toHaveValues(['bound_other']);
  await expect(feedback).not.toContainText('Unavailable:');
  await expect(page.locator('#new-axis-solve-label')).toHaveValue('My uncommitted joint');
});

test('sphere Shared center reference requires a point datum and excludes fit centers and axes', async ({ page }) => {
  await jointScene(page);
  await page.getByRole('button', { name: 'Surface fit', exact: true }).click();
  await page.locator('#new-fit-kind').selectOption('sphere');
  await page.locator('#new-fit-relationships').getByRole('button', { name: 'Add relationship', exact: true }).click();
  const relationship = page.locator('#new-fit-relationships [data-fit-relationship]');
  await relationship.locator('select[id$="-kind"]').selectOption('shared_center');
  const pointRole = relationship.locator('[data-relationship-role="point"]'),
    reference = pointRole.locator('.selection-field');
  await expect(reference.locator('.selection-field-feedback')).toContainText('No eligible reference point');
  await expect(reference.locator('.selection-field-feedback')).not.toContainText('axis or plane datum');
  await expect(reference.locator('.model-field-pick')).toBeDisabled();
  await expect(reference.locator('.selection-field-dropdown')).toBeEnabled();
  await reference.locator('.selection-field-dropdown').click();
  await expect(pointRole.locator('.relationship-participants input')).toHaveCount(0);
  // The prospective sphere does declare a center, but it can only be a target:
  // a surface-fit center cannot silently replace the required point datum.
  await expect(relationship.locator('[data-relationship-role="members"] .relationship-participants input')).toHaveCount(1);
  await expect(relationship.locator('[data-relationship-role="members"] .selection-field-selected')).toContainText('center');
});

test('failed Body stays selectable for export diagnostics when model picking is unavailable', async ({ page }) => {
  const { graph, publish } = await jointScene(page);
  graph.recipe.nodes.push({ id: 'failed_body', label: 'Open single-face body', operation: 'body',
    faces: [], sewing_tolerance: 0.000002 });
  graph.states.failed_body = 'failed';
  graph.errors.failed_body = 'Open boundary; Body requires exactly one connected shell';
  await publish();
  await page.getByRole('button', { name: 'Export CAD…', exact: true }).click();
  await page.locator('#export-scope').selectOption('body');
  const target = page.locator('#export-target'), picker = field(page, 'export-target');
  await expect(picker.locator('.model-field-pick')).toBeDisabled();
  await expect(picker.locator('.selection-field-dropdown')).toBeEnabled();
  await expect(picker.locator('.selection-field-feedback')).toContainText('no pickable 3D geometry');
  expect(await target.evaluate(control => control.labels[0].control === control)).toBe(true);
  await target.selectOption('failed_body');
  await expect(target).toHaveValue('failed_body');
  await expect(page.locator('#export-download')).toBeDisabled();
  await expect(page.locator('#export-summary')).toContainText('Unavailable: Open single-face body (failed)');
  await picker.locator('.selection-field-dropdown').click();
  const option = picker.getByRole('menuitemradio', { name: 'Open single-face body', exact: true });
  await expect(option).toBeEnabled();
  await option.click();
  await expect(page.locator('#export-download')).toBeDisabled();
});

test('physical geometry stays list-only until it has a current nonempty preview', async ({ page }) => {
  const { graph, publish } = await jointScene(page);
  graph.recipe.nodes.push({ id: 'test_edge', label: 'Intersection output', operation: 'surface_intersection',
    first: { feature: 'side' }, second: { feature: 'end' } });
  graph.states.test_edge = 'ready';
  graph.results.test_edge = { kind: 'intersection', curves: [] };
  await publish();
  await page.evaluate(() => {
    const label = document.createElement('label'), control = document.createElement('select');
    label.id = 'test-intersection-field';
    label.textContent = 'Boundary intersection';
    label.style.cssText = 'position:fixed;left:500px;top:150px;width:300px;z-index:100';
    control.dataset.boundaryIntersection = '';
    control.append(new Option('Intersection output', 'test_edge'));
    label.append(control);
    document.body.append(label);
  });
  const picker = page.locator('#test-intersection-field .selection-field'), pick = picker.locator('.model-field-pick'),
    feedback = picker.locator('.selection-field-feedback');
  await expect(pick).toBeDisabled();
  const native = page.locator('#test-intersection-field select'), generatedId = await native.getAttribute('id');
  expect(generatedId).toBeTruthy();
  expect(await native.evaluate(control => control.labels[0].control === control)).toBe(true);
  await expect(feedback).toContainText('no pickable 3D geometry');
  graph.results.test_edge.curves = [{ kind: 'line', preview: { positions: [0, 0, 0, 1, 0, 0], indices: [] } }];
  await publish();
  await expect(native).toHaveAttribute('id', generatedId);
  await expect(pick).toBeEnabled();
  await expect(feedback).toBeHidden();
  graph.states.test_edge = 'stale';
  await publish();
  await expect(pick).toBeDisabled();
  await expect(feedback).toContainText('no pickable 3D geometry');
});

test('shared picker renders optional-empty, busy, list-only and unavailable saved feedback accessibly', async ({ page }) => {
  await jointScene(page);
  await page.evaluate(async () => {
    const { createPickField } = await import('/model-picking.js');
    const picker = createPickField(document, { label: 'Reference geometry',
      onPick: () => { window.pickStarted = true; }, onChange: key => { window.chosenKey = key; } });
    picker.element.id = 'test-shared-picker';
    document.body.append(picker.element);
    picker.element.style.cssText = 'position:fixed;left:500px;top:150px;width:300px;z-index:100';
    window.testPicker = picker;
    picker.update([{ key: '', label: 'None (standalone)', selected: true }],
      { emptyMessage: 'Create an earlier axis or plane datum.' });
  });
  const picker = page.locator('#test-shared-picker'), feedback = picker.locator('.selection-field-feedback'),
    pick = picker.locator('.model-field-pick'), dropdown = picker.locator('.selection-field-dropdown');
  await expect(feedback).toBeVisible();
  await expect(feedback).toHaveText('No eligible reference geometry. Create an earlier axis or plane datum.');
  await expect(pick).toHaveAttribute('aria-describedby', await feedback.getAttribute('id'));
  await expect(dropdown).toHaveAttribute('aria-describedby', await feedback.getAttribute('id'));
  await expect(pick).toBeDisabled();
  await expect(dropdown).toBeEnabled();
  await page.evaluate(() => window.testPicker.update([], { disabled: true, busy: true }));
  await expect(feedback).toContainText('Temporarily unavailable');
  await expect(feedback).not.toContainText('No eligible');
  await page.evaluate(() => window.testPicker.update([], { disabled: true }));
  await expect(feedback).toContainText('current configuration');
  await expect(feedback).not.toContainText('Wait');
  await page.evaluate(() => window.testPicker.update([{ key: 'listed', label: 'List-only output', modelPickable: false }]));
  await expect(feedback).toContainText('Use the arrow');
  await expect(pick).toBeDisabled();
  await dropdown.click();
  await picker.getByRole('menuitemradio', { name: 'List-only output', exact: true }).click();
  expect(await page.evaluate(() => window.chosenKey)).toBe('listed');
  expect(await page.evaluate(() => !!window.pickStarted)).toBe(false);
  await page.evaluate(() => window.testPicker.update([
    { key: 'missing', label: 'Missing axis — provider removed', selected: true, disabled: true },
    { key: 'replacement', label: 'Replacement axis' },
  ]));
  await expect(pick).toContainText('Missing axis');
  await expect(feedback).toContainText('Saved input unavailable');
  await dropdown.click();
  await expect(picker.getByRole('menuitemradio', { name: /Missing axis/ })).toBeDisabled();
  await page.keyboard.press('Escape');
  await dropdown.focus();
  await page.keyboard.press('ArrowDown');
  await expect(picker.getByRole('menuitemradio', { name: 'Replacement axis', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(picker.getByRole('menuitemradio', { name: 'Replacement axis', exact: true })).toBeFocused();
  await picker.getByRole('menuitemradio', { name: 'Replacement axis', exact: true }).click();
  expect(await page.evaluate(() => window.chosenKey)).toBe('replacement');
  await page.evaluate(() => window.testPicker.update([{ key: 'replacement', label: 'Replacement axis', selected: true }]));
  await expect(feedback).toBeHidden();
});
