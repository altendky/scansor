import { test, expect } from '@playwright/test';

const storageKey = 'scansor.flexlayout.workspace.v1';
const floating = '.flexlayout__float_window';

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await expect(page.locator('#action-list .state-running')).toHaveCount(0);
  await expect(page.locator('#startup-error')).toBeHidden();
}

function tabset(page, name) {
  return page.getByRole('tab', { name, exact: true }).locator(
    'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " flexlayout__tabset ")][1]');
}

async function floatPanel(page, name) {
  if (name === 'Edit' && await page.getByRole('tab', { name, exact: true }).count() === 0) await showEditTab(page);
  await tabset(page, name).getByRole('button', { name: 'Float selected tab', exact: true }).click();
  await expect(page.locator(floating).getByRole('tab', { name, exact: true })).toBeVisible();
}

async function showEditTab(page) {
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('editor');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
}

async function editOuterSurface(page) {
  await page.locator('#action-list .action-select').filter({ hasText: 'Outer surface' }).click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await expect(page.locator('#axial-start')).toBeVisible();
  await page.locator('#dock-edit').click();
}

async function dockPanel(page, name) {
  const window = page.locator(floating).filter({ has: page.getByRole('tab', { name, exact: true }) });
  const handle = window.getByTitle('Drag into another layout', { exact: true });
  const view = await page.locator('#viewport').boundingBox();
  const floatBox = await window.boundingBox();
  const rightHalf = floatBox.x + floatBox.width / 2 > view.x + view.width / 2;
  await drag(page, handle, rightHalf ? view.x + 12 : view.x + view.width - 12, view.y + view.height / 2);
  await expect(page.locator(floating).getByRole('tab', { name, exact: true })).toHaveCount(0);
}

async function drag(page, from, x, y) {
  const box = await from.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 20 });
  await page.mouse.up();
}

test('icon legend uses current drawings, docks and floats without changing actions', async ({ page }) => {
  await ready(page);
  const writes = [];
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); });
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  const legend = page.locator('#icon-legend-dialog');
  await expect(legend).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Icon legend', exact: true })).toBeVisible();
  await expect(legend.locator('li[data-icon]')).toHaveCount(45);
  await expect(legend.getByText('Plane fit', { exact: true })).toBeVisible();
  expect(await legend.evaluate(async element => {
    const { featureIcon, featureIconLegend } = await import('/action-tree.js');
    return featureIconLegend.flatMap(section => section.entries).every(({ name }) =>
      element.querySelector(`[data-icon="${name}"] path`).getAttribute('d') ===
        featureIcon(name).querySelector('path').getAttribute('d'));
  })).toBe(true);
  if (process.env.ICON_LEGEND_SCREENSHOT) await page.screenshot({ path: process.env.ICON_LEGEND_SCREENSHOT });
  await floatPanel(page, 'Icon legend');
  await expect(legend).toBeVisible();
  await legend.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Icon legend', exact: true })).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('icon-legend-dialog');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(legend).toBeVisible();
  await page.getByRole('tab', { name: 'Icon legend', exact: true }).getByTitle('Close', { exact: true }).click();
  await expect(legend).toBeHidden();
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  await expect(legend).toBeVisible();
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(legend).toBeVisible();
  expect(writes).toEqual([]);
  await page.reload();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(legend).toBeHidden();
});

test('real viewer starts under CSP with independent feature panels and no errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.cspViolations = [];
    document.addEventListener('securitypolicyviolation', event => window.cspViolations.push(event.violatedDirective));
  });
  await ready(page);
  await expect(page.getByRole('tab', { name: 'Features', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toHaveCount(0);
  await expect(page.locator('#feature-selection-count')).toHaveText('0 selected');
  await expect(page.locator('#feature-properties-panel')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBeTruthy();
  expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
  expect(errors).toEqual([]);
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT });
});

test('closing or floating a right panel gives its width to Model and preserves Features', async ({ page }) => {
  await ready(page);
  for (const method of ['form', 'shell', 'float']) {
    await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
    const tree = await tabset(page, 'Features').boundingBox();
    const model = await tabset(page, 'Model').boundingBox();
    const legend = await tabset(page, 'Icon legend').boundingBox();
    expect(legend.x).toBeGreaterThan(model.x + model.width);
    if (method === 'form') await page.locator('#icon-legend-dialog').getByRole('button', { name: 'Close', exact: true }).click();
    else if (method === 'shell') await page.getByRole('tab', { name: 'Icon legend', exact: true }).getByTitle('Close', { exact: true }).click();
    else await floatPanel(page, 'Icon legend');
    await expect.poll(async () => (await tabset(page, 'Model').boundingBox()).width)
      .toBeGreaterThan(model.width + legend.width - 2);
    expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - tree.width)).toBeLessThan(2);
    if (method === 'float') {
      const expanded = await tabset(page, 'Model').boundingBox();
      await page.locator('#icon-legend-dialog').getByRole('button', { name: 'Close', exact: true }).click();
      await expect(page.getByRole('tab', { name: 'Icon legend', exact: true })).toHaveCount(0);
      expect(Math.abs((await tabset(page, 'Model').boundingBox()).width - expanded.width)).toBeLessThan(2);
    }
  }
});

test('closing a vertically docked panel expands Model through collapsed rows without resizing Features', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  const viewport = await page.locator('#viewport').boundingBox();
  await drag(page, page.getByRole('tab', { name: 'Icon legend', exact: true }),
    viewport.x + viewport.width / 2, viewport.y + viewport.height - 12);
  const tree = await tabset(page, 'Features').boundingBox();
  const model = await tabset(page, 'Model').boundingBox();
  const legend = await tabset(page, 'Icon legend').boundingBox();
  expect(legend.y).toBeGreaterThan(model.y + model.height);
  await page.locator('#icon-legend-dialog').getByRole('button', { name: 'Close', exact: true }).click();
  await expect.poll(async () => (await tabset(page, 'Model').boundingBox()).height)
    .toBeGreaterThan(model.height + legend.height - 2);
  const after = await tabset(page, 'Features').boundingBox();
  expect(Math.abs(after.width - tree.width)).toBeLessThan(2);
  expect(Math.abs(after.height - tree.height)).toBeLessThan(2);
  await ready(page);
  expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - tree.width)).toBeLessThan(2);
});

test('floating a shared tab does not resize docked panels', async ({ page }) => {
  await ready(page);
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  const tree = await tabset(page, 'Features').boundingBox();
  const model = await tabset(page, 'Model').boundingBox();
  await floatPanel(page, 'Graph');
  expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - tree.width)).toBeLessThan(2);
  expect(Math.abs((await tabset(page, 'Model').boundingBox()).width - model.width)).toBeLessThan(2);
});

test('omitting workflow panels on reload gives saved space back to Model', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  const tree = await tabset(page, 'Features').boundingBox();
  const model = await tabset(page, 'Model').boundingBox();
  const legend = await tabset(page, 'Icon legend').boundingBox();
  await page.reload();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Icon legend', exact: true })).toHaveCount(0);
  expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - tree.width)).toBeLessThan(2);
  expect((await tabset(page, 'Model').boundingBox()).width).toBeGreaterThan(model.width + legend.width - 2);
});

test('window resizing gives Model the width change, preserving chosen splitter sizes', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  const tree = await tabset(page, 'Features').boundingBox();
  const splitter = page.locator('.flexlayout__splitter').filter({ visible: true });
  let divider;
  for (const item of await splitter.all()) {
    const box = await item.boundingBox();
    if (box.height > box.width && Math.abs(box.x - tree.x - tree.width) < 15) { divider = item; break; }
  }
  const box = await divider.boundingBox();
  await drag(page, divider, box.x + 70, box.y + box.height / 2);
  await expect.poll(async () => (await tabset(page, 'Features').boundingBox()).width).toBeGreaterThan(tree.width + 25);
  const chosen = await tabset(page, 'Features').boundingBox();
  const legend = await tabset(page, 'Icon legend').boundingBox();
  const model = await tabset(page, 'Model').boundingBox();
  for (const width of [1640, 1300, 600, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    if (width === 600) continue; // Minimum-size constraints may compress panels.
    await expect.poll(async () => (await tabset(page, 'Model').boundingBox()).width)
      .toBeCloseTo(model.width + width - 1440, 0);
    expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - chosen.width)).toBeLessThan(2);
    expect(Math.abs((await tabset(page, 'Icon legend').boundingBox()).width - legend.width)).toBeLessThan(2);
  }
});

test('window height changes go to Model while a panel below it retains its height', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Icon legend', exact: true }).click();
  const viewport = await page.locator('#viewport').boundingBox();
  await drag(page, page.getByRole('tab', { name: 'Icon legend', exact: true }),
    viewport.x + viewport.width / 2, viewport.y + viewport.height - 12);
  const model = await tabset(page, 'Model').boundingBox();
  const legend = await tabset(page, 'Icon legend').boundingBox();
  const tree = await tabset(page, 'Features').boundingBox();
  for (const height of [1200, 850, 1000]) {
    await page.setViewportSize({ width: 1440, height });
    await expect.poll(async () => (await tabset(page, 'Model').boundingBox()).height)
      .toBeCloseTo(model.height + height - 1000, 0);
    expect(Math.abs((await tabset(page, 'Icon legend').boundingBox()).height - legend.height)).toBeLessThan(2);
    expect(Math.abs((await tabset(page, 'Features').boundingBox()).width - tree.width)).toBeLessThan(2);
  }
});

test('workspace tabs use the compact viewer theme and retain selection/focus behavior', async ({ page }) => {
  await ready(page);
  const model = page.getByRole('tab', { name: 'Model', exact: true });
  const graph = page.getByRole('tab', { name: 'Graph', exact: true });
  const styles = element => {
    const style = getComputedStyle(element);
    return { font: style.fontSize, background: style.backgroundColor, color: style.color, shadow: style.boxShadow };
  };
  expect(await model.evaluate(styles)).toEqual({ font: '12px', background: 'rgb(22, 75, 90)',
    color: 'rgb(228, 234, 241)', shadow: 'rgb(121, 216, 238) 0px -2px 0px 0px inset' });
  expect((await graph.evaluate(styles)).color).toBe('rgb(154, 172, 190)');
  const frame = tabset(page, 'Model');
  expect((await frame.locator('.flexlayout__tabset_tabbar_outer').boundingBox()).height).toBe(30);
  expect(await frame.locator('.flexlayout__tabset_tabbar_outer').evaluate(element => getComputedStyle(element).backgroundColor))
    .toBe('rgb(27, 42, 54)');
  await graph.click();
  await expect(page.locator('#feature-graph-view')).toBeVisible();
  expect((await graph.evaluate(styles)).background).toBe('rgb(22, 75, 90)');
  await model.focus();
  await expect(model).toBeFocused();
  await model.click();
  await expect(page.locator('#viewport canvas')).toBeVisible();
});

test('native float, dock and splitter preserve actual draft and canvas without reevaluation', async ({ page }) => {
  await ready(page);
  await editOuterSurface(page);
  await page.locator('#axial-start').fill('1.234');
  await page.evaluate(() => { window.originalCanvas = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', request => {
    if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(request.url())) evaluations.push(request.url());
  });
  await floatPanel(page, 'Edit');
  await expect(page.locator('#axial-start')).toHaveValue('1.234');
  await dockPanel(page, 'Edit');
  const before = await page.locator('#features-panel').boundingBox();
  let resize;
  for (const splitter of await page.locator('.flexlayout__splitter').all()) {
    const box = await splitter.boundingBox();
    if (box && box.height > box.width && Math.abs(box.x - before.x - before.width) < 15) {
      resize = splitter;
      break;
    }
  }
  expect(resize).toBeTruthy();
  const rect = await resize.boundingBox();
  await drag(page, resize, rect.x + 70, rect.y + rect.height / 2);
  await expect.poll(async () => Math.abs((await page.locator('#features-panel').boundingBox()).width - before.width))
    .toBeGreaterThan(25);
  await expect(page.locator('#axial-start')).toHaveValue('1.234');
  expect(await page.evaluate(() => window.originalCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  expect(evaluations).toEqual([]);
});

test('Build faces and Relationships share native dockable Edit with guarded shell close', async ({ page }) => {
  await ready(page);
  await page.locator('[data-command-id="new-build-faces"]').click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await page.locator('#dock-edit').click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await page.locator('#build-faces-options').getByText('Options', { exact: true }).click();
  await page.locator('#build-faces-label').fill('Retained face draft');
  await floatPanel(page, 'Edit');
  await expect(page.locator('#build-faces-label')).toHaveValue('Retained face draft');
  await dockPanel(page, 'Edit');
  await page.evaluate(() => {
    const dialog = document.getElementById('build-faces-dialog');
    window.blockWorkspaceClose = event => event.preventDefault();
    dialog.addEventListener('workspace-before-close', window.blockWorkspaceClose);
  });
  const close = page.getByRole('tab', { name: 'Edit', exact: true }).getByTitle('Close', { exact: true });
  await close.click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  expect(await page.locator('#build-faces-dialog').evaluate(element => element instanceof HTMLDialogElement && element.open)).toBeTruthy();
  await page.evaluate(() => document.getElementById('build-faces-dialog')
    .removeEventListener('workspace-before-close', window.blockWorkspaceClose));
  page.once('dialog', dialog => {
    expect(dialog.message()).toContain('Discard unapplied changes');
    return dialog.accept();
  });
  await close.click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toHaveCount(0);
  await page.locator('[data-command-id="new-relationship"]').click();
  await expect(page.locator('#relationship-dialog')).toBeVisible();
  await page.locator('#dock-edit').click();
  await page.getByRole('button', { name: 'Show Participants choices', exact: true }).click();
  await page.locator('#relationship-filter').fill('surface');
  await page.keyboard.press('Escape');
  await floatPanel(page, 'Edit');
  await dockPanel(page, 'Edit');
  await page.getByRole('button', { name: 'Show Participants choices', exact: true }).click();
  await expect(page.locator('#relationship-filter')).toHaveValue('surface');
  await page.keyboard.press('Escape');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('tab', { name: 'Edit', exact: true }).getByTitle('Close', { exact: true }).click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toHaveCount(0);
});

test('saved workspace reloads, skips workflow drafts and resets without replacing canvas', async ({ page }) => {
  await ready(page);
  await floatPanel(page, 'Edit');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key))?.version, storageKey)).toBe(1);
  await page.reload();
  await expect(page.locator(floating).getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.evaluate(() => { window.originalCanvas = document.querySelector('#viewport canvas'); });
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator(floating)).toHaveCount(0);
  await expect(page.locator('#features-panel')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toHaveCount(0);
  await expect(page.locator('#feature-properties-panel')).toBeHidden();
  expect(await page.evaluate(() => window.originalCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  await page.locator('[data-command-id="new-relationship"]').click();
  await expect(page.locator('#edit-popup')).toBeVisible();
  await page.locator('#dock-edit').click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.locator('#edit-host .edit-empty')).toBeVisible();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
});

test('unavailable browser storage leaves layout functional and reports persistence failure', async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
    Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
  });
  await ready(page);
  await floatPanel(page, 'Edit');
  await expect(page.getByText('Layout cannot be saved in this browser.', { exact: true })).toBeVisible();
  await dockPanel(page, 'Edit');
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#features-panel')).toBeVisible();
});

test('Show panel restores a maximized layout and workflow Focus reveals the model', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  await showEditTab(page);
  await tabset(page, 'Edit').getByRole('button', { name: /Maximize/ }).click();
  await expect(page.locator('#viewport')).toBeHidden();
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('view');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(page.locator('#viewport')).toBeVisible();
  await page.locator('[data-command-id="new-build-faces"]').click();
  await expect(page.locator('#focus-face-target')).toBeEnabled();
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  await expect(page.locator('#viewport')).toBeHidden();
  await page.locator('#focus-face-target').click();
  await expect(page.locator('#viewport')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Model', exact: true })).toHaveAttribute('aria-selected', 'true');
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT.replace('.png', '-focused.png') });
  expect(errors).toEqual([]);
});

test('toolbar controls retain real handlers when floated and docked', async ({ page }) => {
  await ready(page);
  await page.getByRole('button', { name: 'Create toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Float', exact: true }).click();
  await page.locator('[data-command-id="new-point"]').click();
  await expect(page.locator('#point-dialog')).toBeVisible();
  await page.locator('#point-dialog [data-close-dialog]').click();
  await page.getByRole('button', { name: 'Create toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Dock top', exact: true }).click();
  await page.locator('[data-command-id="new-point"]').click();
  await expect(page.locator('#point-dialog')).toBeVisible();
  await page.locator('#point-dialog [data-close-dialog]').click();
});

test('Model and Graph are independent native tabs with simultaneous views and no reevaluation', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  await expect(page.locator('.workspace-tabs')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Model', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Graph', exact: true })).toBeVisible();
  await page.evaluate(() => { window.originalCanvas = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', request => {
    if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(request.url())) evaluations.push(request.url());
  });
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
  await expect(page.locator('#viewport')).toBeHidden();
  await page.locator('#feature-graph-lens').selectOption('relationships');
  await page.getByRole('tab', { name: 'Model', exact: true }).click();
  const modelBox = await page.locator('#viewport').boundingBox();
  await drag(page, page.getByRole('tab', { name: 'Graph', exact: true }),
    modelBox.x + modelBox.width - 12, modelBox.y + modelBox.height / 2);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
  await floatPanel(page, 'Graph');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
  await dockPanel(page, 'Graph');
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
  await expect(page.locator('#feature-graph-lens')).toHaveValue('relationships');
  expect(await page.evaluate(() => window.originalCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  expect(evaluations).toEqual([]);
  expect(errors).toEqual([]);
  await page.reload();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT.replace('.png', '-split.png') });
});

test('saved combined view migrates to Model and Graph without losing other panel placements', async ({ page }) => {
  await ready(page);
  await floatPanel(page, 'Edit');
  await page.evaluate(key => {
    const saved = JSON.parse(localStorage.getItem(key));
    const removeGraph = node => {
      if (node.id === 'view') node.name = 'Model / graph';
      if (node.children) node.children = node.children.filter(child => child.id !== 'graph');
      for (const child of node.children || []) removeGraph(child);
    };
    removeGraph(saved.layout.layout);
    localStorage.setItem(key, JSON.stringify(saved));
  }, storageKey);
  await page.reload();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator(floating).getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Model', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
});

test('old toolbar tabs migrate to strips without discarding saved panels', async ({ page }) => {
  await ready(page);
  await floatPanel(page, 'Edit');
  await page.evaluate(key => {
    const saved = JSON.parse(localStorage.getItem(key));
    saved.layout.layout.children.unshift({ type: 'row', weight: 12, children:
      ['create', 'faces', 'features', 'output'].map(id => ({ type: 'tabset', children: [
        { type: 'tab', id, name: id, component: id, enableClose: false },
      ] })) });
    localStorage.setItem(key, JSON.stringify(saved));
  }, storageKey);
  await page.reload();
  await expect(page.locator(floating).getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Features', exact: true })).toBeVisible();
  await expect(page.locator('.prototype-toolbar')).toHaveCount(5);
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output'])
    await expect(page.getByRole('tab', { name, exact: true })).toHaveCount(0);
});
