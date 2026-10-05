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
  await tabset(page, name).getByRole('button', { name: 'Float selected tab', exact: true }).click();
  await expect(page.locator(floating).getByRole('tab', { name, exact: true })).toBeVisible();
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

test('real viewer starts under CSP with independent feature panels and no errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.cspViolations = [];
    document.addEventListener('securitypolicyviolation', event => window.cspViolations.push(event.violatedDirective));
  });
  await ready(page);
  await expect(page.getByRole('tab', { name: 'Features', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Feature editor', exact: true })).toBeVisible();
  await expect(page.locator('#feature-selection-count')).toHaveText('0 selected');
  const tree = await page.locator('#features-panel').boundingBox();
  const editor = await page.locator('#feature-properties-panel').boundingBox();
  expect(editor.y).toBeGreaterThan(tree.y + tree.height);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBeTruthy();
  expect(await page.evaluate(() => window.cspViolations)).toEqual([]);
  expect(errors).toEqual([]);
  if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT });
});

test('native float, dock and splitter preserve actual draft and canvas without reevaluation', async ({ page }) => {
  await ready(page);
  await page.locator('#action-list .action-select').filter({ hasText: 'Outer band' }).click();
  await page.locator('#action-label').fill('Unsaved real feature draft');
  await page.evaluate(() => { window.originalCanvas = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', request => {
    if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(request.url())) evaluations.push(request.url());
  });
  await floatPanel(page, 'Feature editor');
  await expect(page.locator('#action-label')).toHaveValue('Unsaved real feature draft');
  await dockPanel(page, 'Feature editor');
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
  await expect(page.locator('#action-label')).toHaveValue('Unsaved real feature draft');
  expect(await page.evaluate(() => window.originalCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  expect(evaluations).toEqual([]);
});

test('Build faces and Relationships are native dockable workflows with guarded shell close', async ({ page }) => {
  await ready(page);
  await page.locator('[data-command-id="new-build-faces"]').click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Build faces', exact: true })).toBeVisible();
  await page.locator('#build-faces-options').getByText('Options', { exact: true }).click();
  await page.locator('#build-faces-label').fill('Retained face draft');
  await floatPanel(page, 'Build faces');
  await expect(page.locator('#build-faces-label')).toHaveValue('Retained face draft');
  await dockPanel(page, 'Build faces');
  await page.evaluate(() => {
    const dialog = document.getElementById('build-faces-dialog');
    window.blockWorkspaceClose = event => event.preventDefault();
    dialog.addEventListener('workspace-before-close', window.blockWorkspaceClose);
  });
  const close = page.getByRole('tab', { name: 'Build faces', exact: true }).getByTitle('Close', { exact: true });
  await close.click();
  await expect(page.locator('#build-faces-dialog')).toBeVisible();
  expect(await page.locator('#build-faces-dialog').evaluate(element => element instanceof HTMLDialogElement && element.open)).toBeTruthy();
  await page.evaluate(() => document.getElementById('build-faces-dialog')
    .removeEventListener('workspace-before-close', window.blockWorkspaceClose));
  await close.click();
  await expect(page.getByRole('tab', { name: 'Build faces', exact: true })).toHaveCount(0);
  await page.locator('[data-command-id="new-relationship"]').click();
  await expect(page.locator('#relationship-dialog')).toBeVisible();
  await page.locator('#relationship-filter').fill('surface');
  await floatPanel(page, 'Relationships');
  await dockPanel(page, 'Relationships');
  await expect(page.locator('#relationship-filter')).toHaveValue('surface');
  await page.getByRole('tab', { name: 'Relationships', exact: true }).getByTitle('Close', { exact: true }).click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect(page.getByRole('tab', { name: 'Relationships', exact: true })).toHaveCount(0);
});

test('saved workspace reloads, skips workflow drafts and resets without replacing canvas', async ({ page }) => {
  await ready(page);
  await floatPanel(page, 'Feature editor');
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key))?.version, storageKey)).toBe(1);
  await page.reload();
  await expect(page.locator(floating).getByRole('tab', { name: 'Feature editor', exact: true })).toBeVisible();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#evaluate-all')).toBeEnabled();
  await page.evaluate(() => { window.originalCanvas = document.querySelector('#viewport canvas'); });
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator(floating)).toHaveCount(0);
  await expect(page.locator('#features-panel')).toBeVisible();
  await expect(page.locator('#feature-properties-panel')).toBeVisible();
  expect(await page.evaluate(() => window.originalCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  await page.locator('[data-command-id="new-relationship"]').click();
  await expect(page.getByRole('tab', { name: 'Relationships', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Relationships', exact: true })).toHaveCount(0);
  await expect(page.locator('#relationship-dialog')).toBeHidden();
});

test('unavailable browser storage leaves layout functional and reports persistence failure', async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
    Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'SecurityError'); };
  });
  await ready(page);
  await floatPanel(page, 'Feature editor');
  await expect(page.getByText('Layout cannot be saved in this browser.', { exact: true })).toBeVisible();
  await dockPanel(page, 'Feature editor');
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#features-panel')).toBeVisible();
});

test('Show panel restores a maximized layout and workflow Focus reveals the model', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await ready(page);
  await tabset(page, 'Feature editor').getByRole('button', { name: /Maximize/ }).click();
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
  await floatPanel(page, 'Feature editor');
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
  await expect(page.locator(floating).getByRole('tab', { name: 'Feature editor', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Model', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  await expect(page.locator('#feature-graph-canvas svg')).toBeVisible();
});

test('old toolbar tabs migrate to strips without discarding saved panels', async ({ page }) => {
  await ready(page);
  await floatPanel(page, 'Feature editor');
  await page.evaluate(key => {
    const saved = JSON.parse(localStorage.getItem(key));
    saved.layout.layout.children.unshift({ type: 'row', weight: 12, children:
      ['create', 'faces', 'features', 'output'].map(id => ({ type: 'tabset', children: [
        { type: 'tab', id, name: id, component: id, enableClose: false },
      ] })) });
    localStorage.setItem(key, JSON.stringify(saved));
  }, storageKey);
  await page.reload();
  await expect(page.locator(floating).getByRole('tab', { name: 'Feature editor', exact: true })).toBeVisible();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Features', exact: true })).toBeVisible();
  await expect(page.locator('.prototype-toolbar')).toHaveCount(5);
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output'])
    await expect(page.getByRole('tab', { name, exact: true })).toHaveCount(0);
});
