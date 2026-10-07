import { test, expect } from '@playwright/test';

const readGraph = async page => (await page.request.get('/api/graph')).json();
const feature = (page, id) => page.locator(`#action-list .action-select[data-action-id="${id}"]`);
const editTab = page => page.getByRole('tab', { name: 'Edit', exact: true });

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
}

async function contextAction(page, id, action) {
  await feature(page, id).click({ button: 'right' });
  await page.locator(`#feature-context-${action}`).click();
}

async function restoreGraph(page, original) {
  const current = await readGraph(page);
  const response = await page.request.post('/api/graph', {
    headers: { 'X-Scansor-Request': '1' },
    data: { token: current.token, recipe: original.recipe },
  });
  expect(response.ok(), response.ok() ? undefined : await response.text()).toBe(true);
}

async function showEditTab(page) {
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('editor');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(editTab(page)).toBeVisible();
}

test('explicit editing opens a popup and Apply retains its owner after tree selection changes', async ({ page }) => {
  await ready(page);
  const original = await readGraph(page);
  try {
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await expect(editTab(page)).toHaveCount(0);
    await feature(page, 'end').click();
    await expect(page.locator('#edit-popup')).toBeHidden();
    await contextAction(page, 'end', 'edit');
    await expect(page.locator('#edit-popup')).toBeVisible();
    await page.locator('#axial-start').fill('-1');
    await feature(page, 'outer_band').click();
    await expect(page.locator('#properties-title')).toHaveText('End surface');
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#apply-properties').click();
    const response = await savedResponse;
    expect(response.ok(), response.ok() ? undefined : await response.text()).toBe(true);
    const saved = await response.json();
    expect(saved.recipe.nodes.find(node => node.id === 'end').axial_domain[0]).toBe(-1);
    expect(saved.recipe.nodes.find(node => node.id === 'outer_band'))
      .toEqual(original.recipe.nodes.find(node => node.id === 'outer_band'));
    await expect(page.locator('#edit-popup')).toBeVisible();
  } finally { await restoreGraph(page, original); }
});

test('double-click opens feature Edit and respects draft guards while single-click selects', async ({ page }) => {
  await ready(page);
  await feature(page, 'end').click();
  await expect(feature(page, 'end')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#edit-popup')).toBeHidden();
  await feature(page, 'end').dblclick();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await expect(page.locator('#properties-title')).toHaveText('End surface');
  await page.locator('#axial-start').fill('-1');
  page.once('dialog', dialog => dialog.dismiss());
  await feature(page, 'side').dblclick();
  await expect(page.locator('#properties-title')).toHaveText('End surface');
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  page.once('dialog', dialog => dialog.accept());
  await feature(page, 'side').dblclick();
  await expect(page.locator('#properties-title')).toHaveText('Outer surface');
});

test('docking preserves the actual draft, guards shell close, and restores an empty optional tab', async ({ page }) => {
  await ready(page);
  await contextAction(page, 'end', 'edit');
  await page.locator('#axial-start').fill('-1');
  await page.locator('#axial-start').evaluate(input => { window.originalEditInput = input; });
  await page.locator('#dock-edit').click();
  await expect(editTab(page)).toBeVisible();
  await expect(page.locator('#edit-popup')).toBeHidden();
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  expect(await page.locator('#axial-start').evaluate(input => input === window.originalEditInput)).toBe(true);
  page.once('dialog', dialog => dialog.dismiss());
  await editTab(page).getByTitle('Close', { exact: true }).click();
  await expect(editTab(page)).toBeVisible();
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  await page.reload();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(editTab(page)).toBeVisible();
  await expect(page.locator('#edit-host .edit-empty')).toBeVisible();
  await expect(page.locator('#edit-popup')).toBeHidden();
  await editTab(page).getByTitle('Close', { exact: true }).click();
  await expect(editTab(page)).toHaveCount(0);
  await showEditTab(page);
  await expect(page.locator('#edit-host .edit-empty')).toBeVisible();
});

test('Build faces and ordinary creation share the retained Edit tab and otherwise use its popup', async ({ page }) => {
  await ready(page);
  await showEditTab(page);
  await page.locator('[data-command-id="new-build-faces"]').click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  await expect(editTab(page)).toHaveCount(1);
  await expect(page.getByRole('tab', { name: 'Build faces', exact: true })).toHaveCount(0);
  await expect(page.locator('#edit-popup')).toBeHidden();
  await page.locator('#close-build-faces').click();
  await expect(editTab(page)).toBeVisible();
  await page.getByRole('button', { name: 'Axis', exact: true }).click();
  await expect(page.locator('#axis-dialog')).toBeVisible();
  await expect(page.locator('#edit-popup')).toBeHidden();
  await expect(editTab(page)).toHaveCount(1);
  await page.locator('[data-close-dialog="axis-dialog"]').click();
  await editTab(page).getByTitle('Close', { exact: true }).click();
  await page.getByRole('button', { name: 'Axis', exact: true }).click();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await expect(page.locator('#axis-dialog')).toBeVisible();
  await expect(editTab(page)).toHaveCount(0);
  await page.locator('[data-close-dialog="axis-dialog"]').click();
  await expect(page.locator('#edit-popup')).toBeHidden();
});

test('legacy editor and separate authoring tabs migrate to one empty closable Edit tab', async ({ page }) => {
  await ready(page);
  await showEditTab(page);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('scansor.flexlayout.workspace.v1')));
  for (const retainEditor of [true, false]) {
    await page.evaluate(({ saved, retainEditor }) => {
      const old = structuredClone(saved);
      const migrate = node => {
        if (node.children?.some(child => child.id === 'editor')) {
          const editor = node.children.find(child => child.id === 'editor');
          editor.name = 'Feature editor';
          editor.enableClose = false;
          if (!retainEditor) node.children = node.children.filter(child => child.id !== 'editor');
          node.children.push(...['build-faces-dialog', 'relationship-dialog'].map(id => ({
            type: 'tab', id, component: id, name: id === 'build-faces-dialog' ? 'Build faces' : 'Relationships',
          })));
        }
        node.children?.forEach(migrate);
      };
      migrate(old.layout.layout);
      localStorage.setItem('scansor.flexlayout.workspace.v1', JSON.stringify(old));
    }, { saved, retainEditor });
    await page.reload();
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await expect(editTab(page)).toHaveCount(1);
    await expect(page.getByRole('tab', { name: 'Build faces', exact: true })).toHaveCount(0);
    await expect(page.getByRole('tab', { name: 'Relationships', exact: true })).toHaveCount(0);
    await editTab(page).click();
    await expect(page.locator('#edit-host .edit-empty')).toBeVisible();
    await expect(page.locator('#build-faces-dialog')).toBeHidden();
    await expect(page.locator('#relationship-dialog')).toBeHidden();
    await editTab(page).getByTitle('Close', { exact: true }).click();
    await expect(editTab(page)).toHaveCount(0);
  }
});

test('published graph refreshes preserve an ordinary fit draft and the original form controls', async ({ page }) => {
  await ready(page);
  let graph = await readGraph(page);
  await contextAction(page, 'end', 'edit');
  await page.locator('#axial-start').fill('-1');
  await page.locator('#axial-start').focus();
  await page.locator('#axial-start').evaluate(input => { window.originalEditInput = input; });
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  for (const revisionChange of [false, true]) {
    if (revisionChange) {
      graph = structuredClone(graph);
      graph.revision++;
      graph.errors.side = 'Published diagnostic';
    }
    const published = page.waitForResponse('**/api/graph/ensure');
    await page.locator('#evaluate-all').evaluate(button => button.click());
    await published;
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    expect(await page.locator('#axial-start').evaluate(input => input === window.originalEditInput)).toBe(true);
  }
  await expect(page.locator('#properties-title')).toHaveText('End surface');
});

test('tree Rename and Evaluate target the clicked feature without opening an edit session', async ({ page }) => {
  await ready(page);
  const original = await readGraph(page);
  try {
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await feature(page, 'side').click();
    await contextAction(page, 'end', 'rename');
    await expect(page.locator('#rename-feature-dialog')).toBeVisible();
    await page.locator('#rename-feature-label').fill('Renamed end surface');
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#rename-feature-form').evaluate(form => form.requestSubmit());
    expect((await savedResponse).ok()).toBe(true);
    const graph = await readGraph(page);
    expect(graph.recipe.nodes.find(node => node.id === 'end').label).toBe('Renamed end surface');
    expect(graph.recipe.nodes.find(node => node.id === 'side').label).toBe('Outer surface');
    await expect(page.locator('#edit-popup')).toBeHidden();
    await expect(editTab(page)).toHaveCount(0);
    const evaluated = page.waitForRequest('**/api/graph/evaluate');
    await feature(page, 'side').click();
    await contextAction(page, 'end', 'evaluate');
    expect((await evaluated).postDataJSON().target).toBe('end');
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await expect(page.locator('#edit-popup')).toBeHidden();
  } finally { await restoreGraph(page, original); }
});

test('organizational groups open Edit through mouse and keyboard context menus without inline buttons', async ({ page }) => {
  const graph = await readGraph(page);
  graph.recipe.groups = [{ id: 'test-group', label: 'Test group' }];
  graph.recipe.nodes.find(node => node.id === 'end').group_id = 'test-group';
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await ready(page);
  await expect(page.locator('#action-list').getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0);
  const group = page.locator('summary[data-group-id="test-group"]');
  await group.click({ button: 'right' });
  await page.locator('#group-context-edit').click();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await expect(page.locator('#feature-group-label')).toHaveValue('Test group');
  await page.locator('[data-close-dialog="feature-group-dialog"]').click();
  await group.focus();
  await group.press('Shift+F10');
  await expect(page.locator('#group-context-menu')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(group).toBeFocused();
  await group.press('Shift+F10');
  await page.locator('#group-context-edit').click();
  await expect(page.locator('#feature-group-label')).toHaveValue('Test group');
});

test('context group assignment preserves parameter drafts and selections have no general properties block', async ({ page }) => {
  const original = await readGraph(page);
  try {
    const recipe = structuredClone(original.recipe);
    recipe.groups = [...(recipe.groups || []), { id: 'draft-group', label: 'Draft group' }];
    const created = await page.request.post('/api/graph', {
      headers: { 'X-Scansor-Request': '1' }, data: { token: original.token, recipe },
    });
    expect(created.ok()).toBe(true);
    await ready(page);
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await contextAction(page, 'end', 'edit');
    await page.locator('#axial-start').fill('-1');
    await feature(page, 'end').click({ button: 'right' });
    const groupedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#feature-context-group').selectOption('draft-group');
    expect((await groupedResponse).ok()).toBe(true);
    await expect(page.locator('#apply-properties')).toBeEnabled();
    await expect(page.locator('#axial-start')).toHaveValue('-1');
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#apply-properties').click();
    const saved = await (await savedResponse).json();
    expect(saved.recipe.nodes.find(node => node.id === 'end'))
      .toMatchObject({ group_id: 'draft-group',
        axial_domain: [-1, original.recipe.nodes.find(node => node.id === 'end').axial_domain[1]] });
    await expect(page.locator('#delete-action')).toHaveCount(0);
    await expect(page.locator('#action-group')).toHaveCount(0);
    await contextAction(page, 'outer_band', 'edit');
    await expect(page.locator('#action-properties')).toBeHidden();
    await expect(page.locator('#selection-tools')).toBeVisible();
  } finally { await restoreGraph(page, original); }
});

test('overlap inspection and its fit links open the shared editor and guard existing drafts', async ({ page }) => {
  const graph = await readGraph(page);
  const joint = graph.recipe.nodes.find(node => node.operation === 'joint_fit');
  graph.diagnostics = { [joint.id]: {
    kind: 'selection_overlap', ids: [0], conflicts: [{ fits: ['side', 'end'], ids: [0] }],
  } };
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.request().method() === 'GET'
    ? route.fulfill({ json: graph }) : route.continue());
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  await ready(page);
  await page.locator('#inspect-overlap').click();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await expect(page.locator('#properties-title')).toHaveText(joint.label);
  await page.locator('#overlap-details').getByRole('button', { name: 'End surface', exact: true }).click();
  await expect(page.locator('#properties-title')).toHaveText('End surface');
  await page.locator('#axial-start').fill('-1');
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#inspect-overlap').click();
  await expect(page.locator('#properties-title')).toHaveText('End surface');
  await expect(page.locator('#axial-start')).toHaveValue('-1');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#inspect-overlap').click();
  await expect(page.locator('#properties-title')).toHaveText(joint.label);
});

test('pending ordinary creation keeps its session protected until the save finishes', async ({ page }) => {
  await ready(page);
  const original = await readGraph(page);
  let release, saved;
  const hold = new Promise(resolve => { release = resolve; });
  let received = false;
  await page.route(/\/api\/graph(?:\?.*)?$/, async route => {
    if (route.request().method() === 'POST') {
      received = true;
      await hold;
    }
    await route.continue();
  });
  try {
    await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
    await page.getByRole('button', { name: 'Axis', exact: true }).click();
    await page.locator('#new-axis-label').fill('Pending axis');
    saved = page.waitForResponse(response => response.url().endsWith('/api/graph') &&
      response.request().method() === 'POST');
    await page.locator('#add-axis-form').getByRole('button', { name: 'Add axis', exact: true }).click();
    await expect.poll(() => received).toBe(true);
    await page.locator('#close-edit').click();
    await expect(page.locator('#axis-dialog')).toBeVisible();
    // Programmatic activation exercises the guard even if a toolbar control is
    // disabled while this request is in flight.
    await page.locator('#new-point').evaluate(button => button.click());
    await expect(page.locator('#axis-dialog')).toBeVisible();
    await expect(page.locator('#point-dialog')).toBeHidden();
    await expect(page.locator('#new-axis-label')).toHaveValue('Pending axis');
    release();
    expect((await saved).ok()).toBe(true);
    await expect(page.locator('#axis-dialog')).toBeHidden();
    await expect(page.locator('#edit-popup')).toBeHidden();
    expect((await readGraph(page)).recipe.nodes.filter(node => node.label === 'Pending axis')).toHaveLength(1);
  } finally {
    release();
    if (received) await saved;
    await restoreGraph(page, original);
  }
});

test('Build faces protects an ordinary name draft when closing the shared host', async ({ page }) => {
  await ready(page);
  await page.locator('[data-command-id="new-build-faces"]').click();
  await page.locator('#build-faces-options > summary').click();
  await page.locator('#build-faces-label').fill('Unapplied face name');
  const confirmations = [];
  const dismiss = async dialog => { confirmations.push(dialog.message()); await dialog.dismiss(); };
  page.once('dialog', dismiss);
  await page.locator('#close-edit').click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  await expect(page.locator('#build-faces-label')).toHaveValue('Unapplied face name');
  expect(confirmations).toHaveLength(1);
  page.once('dialog', async dialog => { confirmations.push(dialog.message()); await dialog.accept(); });
  await page.locator('#close-edit').click();
  await expect(page.locator('#build-faces-dialog')).toBeHidden();
  await expect(page.locator('#edit-popup')).toBeHidden();
  expect(confirmations).toHaveLength(2);
});

test('graph publications end an edit when its operation or managed owner changes', async ({ page }) => {
  await ready(page);
  const original = await readGraph(page);
  let graph = structuredClone(original);
  await page.route(/\/api\/graph(?:\?.*)?$/, route => route.fulfill({ json: graph }));
  await page.route('**/api/graph/evaluate', route => route.fulfill({ json: { status: 'running' } }));
  await page.route('**/api/graph/ensure', route => route.fulfill({ json: graph }));
  for (const change of ['operation', 'owner']) {
    graph = structuredClone(original);
    await ready(page);
    await contextAction(page, 'end', 'edit');
    await page.locator('#axial-start').fill('-1');
    const index = graph.recipe.nodes.findIndex(node => node.id === 'end');
    if (change === 'operation') {
      graph.recipe.nodes[index] = { id: 'end', label: 'Replacement point', operation: 'point',
        initial_coordinates: [0, 0, 0] };
      delete graph.results.end;
    } else graph.recipe.nodes[index].managed_by = 'side';
    graph.revision++;
    const published = page.waitForResponse('**/api/graph/ensure');
    await page.locator('#evaluate-all').evaluate(button => button.click());
    await published;
    await expect(page.locator('#evaluate-all')).toBeEnabled();
    await expect(page.locator('#edit-popup')).toBeHidden();
    await expect(page.locator('#feature-properties-panel')).toBeHidden();
  }
});
