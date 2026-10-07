import { test, expect } from '@playwright/test';
import { initialWorkspace, panelTab } from '../workspace-state.js';

async function ready(page) {
  await page.goto('/');
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"] [data-command-id="new-point"]')).toBeEnabled();
  await expect(page.locator('#viewport canvas')).toBeVisible();
}
async function place(page, label) {
  await toolbarPlace(page, 'Create', label);
}
async function toolbarPlace(page, name, label) {
  await page.getByRole('button', { name: name + ' toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name: label, exact: true }).click();
}
async function attach(page, name) {
  await toolbarAttach(page, 'Create', name);
}
async function toolbarAttach(page, toolbar, host) {
  if (host === 'Edit' && await page.getByRole('tab', { name: 'Edit', exact: true }).count() === 0) {
    await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('editor');
    await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  }
  await page.getByRole('button', { name: toolbar + ' toolbar options', exact: true }).click();
  await page.getByRole('menuitemradio', { name: host, exact: true }).click();
}
async function editOuterSurface(page) {
  await page.locator('#action-list .action-select').filter({ hasText: 'Outer surface' }).click({ button: 'right' });
  await page.locator('#feature-context-edit').click();
  await expect(page.locator('#axial-start')).toBeVisible();
  await page.locator('#dock-edit').click();
}
function tabset(page, name) {
  return page.getByRole('tab', { name, exact: true }).locator(
    'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " flexlayout__tabset ")][1]');
}
async function dragTo(page, from, x, y) {
  const box = await from.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 20 });
  await page.mouse.up();
}

test('local strips reserve space, follow tabs/resize, persist and detach without evaluation', async ({ page }) => {
  await ready(page);
  await page.evaluate(() => { window.hostCanvas = document.querySelector('#viewport canvas'); });
  await editOuterSurface(page);
  await page.locator('#axial-start').fill('1.234');
  const evaluations = [];
  page.on('request', r => { if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(r.url())) evaluations.push(r.url()); });
  const toolbar = page.locator('.prototype-toolbar[data-toolbar-id="create"]'), frame = tabset(page, 'Model');
  await attach(page, 'Model / Graph');
  const host = await toolbar.getAttribute('data-host');
  expect(host).not.toBe('workspace');
  for (const edge of ['top', 'right', 'bottom', 'left']) {
    await place(page, `Dock ${edge}`);
    await expect(toolbar).toHaveAttribute('data-edge', edge);
    await expect.poll(async () => {
      const outer = await frame.boundingBox(), content = await page.locator('#viewport').boundingBox(), bar = await toolbar.boundingBox();
      const header = await frame.locator('.flexlayout__tabset_tabbar_outer').boundingBox();
      if (edge === 'top') return Math.abs(header.y - outer.y - bar.height);
      if (edge === 'left') return Math.max(Math.abs(content.x - outer.x - bar.width), Math.abs(header.x - content.x));
      if (edge === 'bottom') return Math.abs(outer.y + outer.height - content.y - content.height - bar.height);
      return Math.max(Math.abs(outer.x + outer.width - content.x - content.width - bar.width),
        Math.abs(header.x + header.width - content.x - content.width));
    }).toBeLessThan(1);
    expect(await toolbar.evaluate(element => element.parentElement.parentElement.classList.contains('flexlayout__tabset'))).toBe(true);
    expect(await page.locator('#viewport').locator('..').evaluate(element => getComputedStyle(element).padding)).toBe('0px');
    if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-${edge}-outer.png` });
  }
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-local.png` });
  await page.getByRole('tab', { name: 'Graph', exact: true }).click();
  await expect(toolbar).toHaveAttribute('data-host', host);
  await expect(page.locator('#feature-graph-view')).toBeVisible();
  await page.getByRole('tab', { name: 'Model', exact: true }).click();
  await tabset(page, 'Model').getByRole('button', { name: /Maximize/ }).click();
  await expect.poll(async () => Math.abs((await toolbar.boundingBox()).x - (await frame.boundingBox()).x)).toBeLessThan(1);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-maximized.png` });
  await tabset(page, 'Model').getByRole('button', { name: /Restore/ }).click();
  await page.setViewportSize({ width: 1200, height: 850 });
  await expect.poll(async () => Math.abs((await toolbar.boundingBox()).x - (await frame.boundingBox()).x)).toBeLessThan(1);
  await expect(page.locator('#axial-start')).toHaveValue('1.234');
  expect(await page.evaluate(() => window.hostCanvas === document.querySelector('#viewport canvas'))).toBeTruthy();
  expect(evaluations).toEqual([]);
  await page.reload();
  await expect(toolbar).toHaveAttribute('data-host', host);
  await expect(toolbar).toBeVisible();
  await attach(page, 'Workspace');
  await expect(toolbar).toHaveAttribute('data-host', 'workspace');
  await expect.poll(() => frame.evaluate(element => getComputedStyle(element).paddingLeft)).toBe('0px');
  await expect.poll(() => frame.evaluate(element => getComputedStyle(element).padding)).toBe('0px');
  await attach(page, 'Model / Graph');
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(toolbar).toHaveAttribute('data-host', 'workspace');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('scansor.toolbars.v1'))?.create))
    .toEqual({ host: 'workspace', edge: 'top', orientation: 'horizontal', order: 1, x: 70, y: 150 });
});

test('drag targets local tabsets and the strip follows its original tabset when split', async ({ page }) => {
  await ready(page);
  const toolbar = page.locator('.prototype-toolbar[data-toolbar-id="create"]'), grip = page.getByRole('button', { name: 'Move Create toolbar' });
  const initialWidth = (await toolbar.boundingBox()).width;
  let frame = await tabset(page, 'Model').boundingBox();
  await dragTo(page, grip, frame.x + frame.width / 2, frame.y + 12);
  await expect(toolbar).not.toHaveAttribute('data-host', 'workspace');
  const host = await toolbar.getAttribute('data-host');
  await expect(toolbar).toHaveAttribute('data-edge', 'top');
  frame = await page.locator('#viewport').locator('..').boundingBox();
  await dragTo(page, page.getByRole('tab', { name: 'Graph', exact: true }), frame.x + frame.width - 12, frame.y + frame.height / 2);
  await expect(page.locator('#viewport')).toBeVisible();
  await expect(page.locator('#feature-graph-view')).toBeVisible();
  await expect(toolbar).toHaveAttribute('data-host', host);
  await expect.poll(async () => Math.abs((await toolbar.boundingBox()).width -
    Math.min(initialWidth, (await page.locator('#viewport').locator('..').boundingBox()).width))).toBeLessThan(1);
  const graph = await page.locator('#feature-graph-view').locator('..').boundingBox();
  await dragTo(page, grip, graph.x + graph.width - 24, graph.y + graph.height / 2);
  await expect(toolbar).toHaveAttribute('data-edge', 'right');
  expect(await toolbar.getAttribute('data-host')).not.toBe(host);
  expect(await toolbar.getAttribute('data-host')).not.toBe('workspace');
});

test('floating host movement, occlusion, hidden-host reveal and removed-host recovery', async ({ page }) => {
  await ready(page);
  const toolbar = page.locator('.prototype-toolbar[data-toolbar-id="create"]');
  await attach(page, 'Edit');
  const originalHost = await toolbar.getAttribute('data-host');
  await tabset(page, 'Edit').getByRole('button', { name: 'Float selected tab', exact: true }).click();
  // Floating the only tab deletes its old host, so recover to the workspace.
  await expect(toolbar).toHaveAttribute('data-host', 'workspace');
  await attach(page, 'Edit');
  expect(await toolbar.getAttribute('data-host')).not.toBe(originalHost);
  const frame = tabset(page, 'Edit');
  await expect.poll(async () => Math.abs((await toolbar.boundingBox()).y - (await frame.boundingBox()).y)).toBeLessThan(1);
  const window = page.locator('.flexlayout__float_window');
  const box = await window.boundingBox();
  await dragTo(page, window.locator('.flexlayout__float_window_header'), box.x + 130, box.y + 80);
  await expect.poll(async () => Math.abs((await toolbar.boundingBox()).x - (await frame.boundingBox()).x)).toBeLessThan(1);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-host-float.png` });
  await toolbar.getByRole('button', { name: 'Point', exact: true }).click();
  await expect(page.locator('#point-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await tabset(page, 'Features').getByRole('button', { name: 'Float selected tab', exact: true }).click();
  const front = page.locator('.flexlayout__float_window').filter({ has: page.getByRole('tab', { name: 'Features', exact: true }) });
  const rear = page.locator('.flexlayout__float_window').filter({ has: page.getByRole('tab', { name: 'Edit', exact: true }) });
  const frontBox = await front.boundingBox(), rearBox = await rear.boundingBox(), header = await front.locator('.flexlayout__float_window_header').boundingBox();
  await dragTo(page, front.locator('.flexlayout__float_window_header'), header.x + header.width / 2 + rearBox.x - frontBox.x,
    header.y + header.height / 2 + rearBox.y - frontBox.y);
  await expect.poll(() => toolbar.evaluate(element => {
    const box = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(box.x + 15, box.y + 15));
  })).toBe(false);
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('editor');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect.poll(() => toolbar.evaluate(element => {
    const box = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(box.x + 15, box.y + 15));
  })).toBe(true);
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await attach(page, 'Model / Graph');
  await tabset(page, 'Features').getByRole('button', { name: /Maximize/ }).click();
  await expect(toolbar).toBeHidden();
  await page.getByRole('combobox', { name: 'Workspace panel', exact: true }).selectOption('create');
  await page.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(toolbar).toBeVisible();
  await expect(page.locator('#viewport')).toBeVisible();
});

test('small local host recovers rather than clipping its placement controls', async ({ page }) => {
  // Default frame minimums now keep the outer grip/menu usable. Lower the
  // saved preference to exercise recovery from a genuinely undersized frame.
  const layout = initialWorkspace();
  layout.global.tabSetMinHeight = 1;
  // Put Features above Edit so a horizontal splitter can shrink the host.
  const features = layout.layout.children[0].children[0];
  features.weight = 48;
  layout.layout.children[0].children[0] = { type: 'row', weight: 24, width: 310,
    children: [features, { type: 'tabset', weight: 52, children: [panelTab('editor')] }] };
  await page.addInitScript(layout => localStorage.setItem('scansor.flexlayout.workspace.v1',
    JSON.stringify({ version: 1, layout })), layout);
  await ready(page);
  await attach(page, 'Features');
  await place(page, 'Dock left');
  const faces = await tabset(page, 'Features').boundingBox();
  let splitter;
  for (const candidate of await page.locator('.flexlayout__splitter').all()) {
    const box = await candidate.boundingBox();
    if (box && box.width > box.height && Math.abs(box.y - faces.y - faces.height) < 10) {
      splitter = candidate;
      break;
    }
  }
  expect(splitter).toBeTruthy();
  const box = await splitter.boundingBox();
  await dragTo(page, splitter, box.x + box.width / 2, faces.y + 40);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-small-host.png` });
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-host', 'workspace');
  await expect(page.getByText('Create toolbar returned to workspace: host is unavailable or too small.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create toolbar options', exact: true })).toBeVisible();
});

test('real commands, keyboard controls, inert locking and CSP', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.violations = [];
    document.addEventListener('securitypolicyviolation', e => window.violations.push(e.violatedDirective));
  });
  await ready(page);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-top.png` });
  await expect(page.getByRole('tab', { name: 'Create', exact: true })).toHaveCount(0);
  const point = page.locator('.prototype-toolbar[data-toolbar-id="create"] [data-command-id="new-point"]');
  await point.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"] [data-command-id="new-axis"]')).toBeFocused();
  await page.evaluate(() => { document.getElementById('new-point').disabled = true; });
  await expect(point).toBeDisabled();
  await page.evaluate(() => { document.getElementById('new-point').disabled = false; });
  await expect(point).toBeEnabled();
  await page.evaluate(() => { document.getElementById('new-point').closest('nav').inert = true; });
  await expect(point).toBeDisabled();
  await place(page, 'Dock left');
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-orientation', 'vertical');
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-left.png` });
  await page.evaluate(() => { document.getElementById('new-point').closest('nav').inert = false; });
  await expect(point).toBeEnabled();
  await page.getByRole('button', { name: 'Create toolbar options', exact: true }).click();
  await page.evaluate(() => { document.querySelector('main').inert = true; });
  await expect(page.getByRole('menuitem', { name: 'Point', exact: true })).toHaveAttribute('data-disabled', '');
  await page.evaluate(() => { document.querySelector('main').inert = false; });
  await page.keyboard.press('Escape');
  await point.click();
  await expect(page.locator('#point-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => window.violations)).toEqual([]);
  expect(errors).toEqual([]);
});

test('placement persists independently, preserves model and drafts, and resets', async ({ page }) => {
  await ready(page);
  await editOuterSurface(page);
  await page.locator('#axial-start').fill('2.345');
  await page.evaluate(() => { window.canvasBefore = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', r => { if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(r.url())) evaluations.push(r.url()); });
  for (const edge of ['right', 'bottom', 'left', 'top']) {
    await place(page, `Dock ${edge}`);
    await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-edge', edge);
  }
  await place(page, 'Float');
  await place(page, 'Rotate floating toolbar');
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-orientation', 'vertical');
  expect(await page.evaluate(() => window.canvasBefore === document.querySelector('#viewport canvas'))).toBeTruthy();
  await expect(page.locator('#axial-start')).toHaveValue('2.345');
  expect(evaluations).toEqual([]);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('scansor.flexlayout.workspace.v1'))?.version)).toBe(1);
  await page.reload();
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-edge', 'float');
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-orientation', 'vertical');
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="create"]')).toHaveAttribute('data-edge', 'top');
});

test('grip drag docks, floats and cancels without triggering evaluation', async ({ page }) => {
  await page.addInitScript(() => {
    window.violations = [];
    document.addEventListener('securitypolicyviolation', e => window.violations.push(e.violatedDirective));
  });
  await ready(page);
  const toolbar = page.locator('.prototype-toolbar[data-toolbar-id="create"]'), grip = page.getByRole('button', { name: 'Move Create toolbar' });
  const shell = await page.locator('.prototype-shell').boundingBox();
  const evaluations = [];
  page.on('request', r => { if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(r.url())) evaluations.push(r.url()); });
  async function drag(x, y, cancel = false) {
    const box = await grip.boundingBox();
    const before = await toolbar.boundingBox();
    const saved = await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'));
    await toolbar.evaluate(element => { window.dragToolbar = element; });
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(x, y, { steps: 20 });
    await expect(page.locator('.prototype-shell')).toHaveAttribute('data-dragging', 'true');
    await expect.poll(async () => (await toolbar.boundingBox()).x)
      .toBeCloseTo(before.x + x - (box.x + box.width / 2), 0);
    await expect.poll(async () => (await toolbar.boundingBox()).y)
      .toBeCloseTo(before.y + y - (box.y + box.height / 2), 0);
    expect(await toolbar.evaluate(element => element === window.dragToolbar)).toBeTruthy();
    expect(await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'))).toBe(saved);
    await expect(page.locator('.prototype-drag-ghost')).toHaveCount(0);
    if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-drag.png` });
    if (cancel) await page.keyboard.press('Escape');
    await page.mouse.up();
    if (cancel) {
      await expect.poll(async () => (await toolbar.boundingBox()).x).toBeCloseTo(before.x, 0);
      await expect.poll(async () => (await toolbar.boundingBox()).y).toBeCloseTo(before.y, 0);
    }
  }
  await drag(shell.x + shell.width - 10, shell.y + 200);
  await expect(toolbar).toHaveAttribute('data-edge', 'right');
  await drag(shell.x + 450, shell.y + 300);
  await expect(toolbar).toHaveAttribute('data-edge', 'float');
  await drag(shell.x + 10, shell.y + 200, true);
  await expect(toolbar).toHaveAttribute('data-edge', 'float');
  expect(await page.evaluate(() => window.violations)).toEqual([]);
  expect(evaluations).toEqual([]);
  await grip.focus();
  await page.keyboard.press('Space');
  await expect(page.locator('.prototype-shell')).toHaveAttribute('data-dragging', 'true');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  await expect(toolbar).toHaveAttribute('data-edge', 'float');
  await expect(page.locator('.prototype-shell')).toHaveAttribute('data-dragging', 'false');
  expect(await page.evaluate(() => window.violations)).toEqual([]);
});

test('small view has accessible overflow and floating toolbar remains reachable', async ({ page }) => {
  await ready(page);
  await place(page, 'Float');
  await page.setViewportSize({ width: 380, height: 500 });
  const toolbar = await page.locator('.prototype-toolbar[data-toolbar-id="create"]').boundingBox();
  const shell = await page.locator('.prototype-shell').boundingBox();
  expect(toolbar.x).toBeGreaterThanOrEqual(shell.x);
  expect(toolbar.y).toBeGreaterThanOrEqual(shell.y);
  expect(toolbar.x + toolbar.width).toBeLessThanOrEqual(shell.x + shell.width + 1);
  expect(toolbar.y + toolbar.height).toBeLessThanOrEqual(shell.y + shell.height + 1);
  await place(page, 'Dock left');
  await page.getByRole('button', { name: 'Create toolbar options', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: 'Rotate floating toolbar', exact: true })).toHaveAttribute('data-disabled', '');
  await expect(page.getByRole('menuitem', { name: 'Frame', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Create toolbar options', exact: true })).toBeFocused();
});

test('all strips are enabled by default, forward native commands and share Auto state', async ({ page }) => {
  await ready(page);
  await expect(page.locator('.prototype-toolbar')).toHaveCount(5);
  for (const name of ['Project', 'Create', 'Faces', 'Feature tools', 'Run / output']) {
    await expect(page.getByRole('toolbar', { name, exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name, exact: true })).toHaveCount(0);
  }
  const commands = await page.locator('[data-command-id]').evaluateAll(elements => elements.map(element => element.dataset.commandId));
  expect(commands).toHaveLength(28);
  expect(commands).toContain('choose-example');
  expect(commands).toContain('show-icon-legend');
  expect(commands).not.toContain('new-mirror');
  await page.evaluate(commands => {
    window.forwardedCommands = [];
    for (const id of commands.filter(id => id !== 'auto-evaluate')) {
      document.getElementById(id).addEventListener('click', event => {
        window.forwardedCommands.push(id); event.stopImmediatePropagation();
      }, { capture: true });
    }
    window.autoChanges = 0;
    document.getElementById('auto-evaluate').addEventListener('change', () => window.autoChanges++);
  }, commands);
  for (const command of commands.filter(id => id !== 'auto-evaluate')) {
    await page.locator('[data-command-id="' + command + '"]').click();
  }
  expect(await page.evaluate(() => window.forwardedCommands)).toEqual(commands.filter(id => id !== 'auto-evaluate'));
  const auto = page.locator('[data-command-id="auto-evaluate"]');
  await auto.click();
  await expect(auto).toHaveAttribute('aria-checked', 'false');
  expect(await page.locator('#auto-evaluate').isChecked()).toBe(false);
  await page.getByRole('button', { name: 'Run / output toolbar options', exact: true }).click();
  await expect(page.getByRole('menuitemcheckbox', { name: 'Auto', exact: true })).toHaveAttribute('aria-checked', 'false');
  await page.getByRole('menuitemcheckbox', { name: 'Auto', exact: true }).click();
  await expect(auto).toHaveAttribute('aria-checked', 'true');
  expect(await page.locator('#auto-evaluate').isChecked()).toBe(true);
  expect(await page.evaluate(() => window.autoChanges)).toBe(2);
  await expect(auto).toBeEnabled();
  await page.locator('.prototype-toolbar[data-toolbar-id="faces"]').evaluate(element => { element.inert = true; });
  await expect(page.locator('[data-command-id="new-build-faces"]')).toBeDisabled();
  await expect(page.locator('[data-command-id="new-point"]')).toBeEnabled();
});

test('header-free Project strip preserves file actions, workspace controls, locks and placement', async ({ page }) => {
  await ready(page);
  const project = page.locator('.prototype-toolbar[data-toolbar-id="project"]');
  await expect(page.locator('header')).toHaveCount(0);
  await expect(page.getByText('Nozzle selection', { exact: true })).toHaveCount(0);
  await expect(project.locator('.prototype-commands > :first-child')).toHaveText('Scansor');
  expect((await page.locator('.prototype-shell').boundingBox()).y).toBe(0);
  const requests = [];
  page.on('request', request => { if (request.method() === 'POST') requests.push(request.url()); });
  await project.getByText('Scansor', { exact: true }).click();
  expect(requests).toEqual([]);
  const download = page.waitForEvent('download');
  await project.getByRole('button', { name: 'Save actions', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('nozzle-actions.json');
  const chooser = page.waitForEvent('filechooser');
  await project.getByRole('button', { name: 'Load actions', exact: true }).click();
  expect(await (await chooser).element().getAttribute('id')).toBe('file');
  const picker = page.getByRole('combobox', { name: 'Workspace panel', exact: true });
  await picker.selectOption('graph');
  await project.getByRole('button', { name: 'Show panel', exact: true }).click();
  await expect(page.locator('#feature-graph-view')).toBeVisible();
  const shell = await page.locator('.prototype-shell').boundingBox();
  await startStripDrag(page, 'Project');
  await page.mouse.move(shell.x + shell.width - 6, shell.y + shell.height / 2, { steps: 20 });
  const preview = page.locator('.prototype-drop-preview');
  await expect(preview).toHaveAttribute('data-edge', 'right');
  const previewHeight = (await preview.boundingBox()).height;
  await page.mouse.up();
  await expect(project).toHaveAttribute('data-edge', 'right');
  await expect.poll(async () => Math.abs((await project.boundingBox()).height - previewHeight)).toBeLessThan(8);
  await toolbarPlace(page, 'Project', 'Float');
  await expect(picker).toHaveValue('graph');
  await toolbarPlace(page, 'Project', 'Dock left');
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Workspace panel ▸', exact: true }).hover();
  await page.evaluate(() => { document.querySelector('main').inert = true; });
  await expect(page.getByRole('menuitemradio', { name: 'Model', exact: true })).toHaveAttribute('data-disabled', '');
  await expect(picker).toHaveValue('graph');
  await page.evaluate(() => { document.querySelector('main').inert = false; });
  await page.getByRole('menuitemradio', { name: 'Model', exact: true }).click();
  await expect(picker).toHaveValue('view');
  await toolbarPlace(page, 'Project', 'Show panel');
  await expect(page.locator('#viewport')).toBeVisible();
  await page.evaluate(() => { document.getElementById('workspace-panel').disabled = true; });
  await expect(picker).toBeDisabled();
  await page.evaluate(() => { document.getElementById('workspace-panel').disabled = false; document.querySelector('main').inert = true; });
  await expect(picker).toBeDisabled();
  await expect(project.getByRole('button', { name: 'Save actions', exact: true })).toBeDisabled();
  await page.evaluate(() => { document.querySelector('main').inert = false; });
  await expect(picker).toBeEnabled();
  await toolbarAttach(page, 'Project', 'Model / Graph');
  const host = await project.getAttribute('data-host');
  expect(host).not.toBe('workspace');
  await page.reload();
  await expect(project).toHaveAttribute('data-host', host);
  await toolbarPlace(page, 'Project', 'Reset layout');
  await expect(project).toHaveAttribute('data-host', 'workspace');
  await expect(project).toHaveAttribute('data-edge', 'top');
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-project.png` });
});

test('multiple strips share global and local edges without overlap and persist independently', async ({ page }) => {
  await ready(page);
  await page.evaluate(() => { window.packedCanvas = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', r => { if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(r.url())) evaluations.push(r.url()); });
  async function noOverlap() {
    await expect.poll(() => page.locator('.prototype-toolbar').evaluateAll(elements => {
      const boxes = elements.map(element => element.getBoundingClientRect());
      return boxes.every((box, i) => boxes.slice(i + 1).every(other =>
        Math.min(box.right, other.right) - Math.max(box.left, other.left) < 1 ||
        Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) < 1));
    })).toBe(true);
  }
  await noOverlap();
  await page.setViewportSize({ width: 380, height: 700 });
  await noOverlap();
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output'])
    await expect(page.getByRole('button', { name: name + ' toolbar options', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output']) await toolbarAttach(page, name, 'Model / Graph');
  await noOverlap();
  const create = page.locator('.prototype-toolbar[data-toolbar-id="create"]'), host = await create.getAttribute('data-host');
  await expect(page.locator('.prototype-toolbar[data-host="' + host + '"]')).toHaveCount(4);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-all-local.png` });
  await toolbarPlace(page, 'Faces', 'Dock left');
  await toolbarPlace(page, 'Feature tools', 'Dock right');
  await toolbarPlace(page, 'Run / output', 'Dock bottom');
  await noOverlap();
  await expect.poll(() => tabset(page, 'Model').evaluate(element => getComputedStyle(element).padding)).toBe('38px');
  expect(await page.evaluate(() => window.packedCanvas === document.querySelector('#viewport canvas'))).toBe(true);
  expect(evaluations).toEqual([]);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('scansor.toolbars.v1')));
  await page.reload();
  for (const [id, placement] of Object.entries(saved)) {
    await expect(page.locator('.prototype-toolbar[data-toolbar-id="' + id + '"]')).toHaveAttribute('data-edge', placement.edge);
    await expect(page.locator('.prototype-toolbar[data-toolbar-id="' + id + '"]')).toHaveAttribute('data-host', placement.host);
  }
  await noOverlap();
  // Orientation changes on a narrow-but-valid host must not cause premature recovery.
  await toolbarAttach(page, 'Faces', 'Edit');
  const faces = page.locator('.prototype-toolbar[data-toolbar-id="faces"]'), editorHost = await faces.getAttribute('data-host');
  for (const edge of ['top', 'left', 'top', 'right']) {
    await toolbarPlace(page, 'Faces', 'Dock ' + edge);
    await expect(faces).toHaveAttribute('data-host', editorHost);
    await expect(faces).toHaveAttribute('data-edge', edge);
  }
  await page.getByRole('button', { name: 'Reset layout', exact: true }).click();
  await expect(page.locator('.prototype-toolbar[data-host="workspace"][data-edge="top"]')).toHaveCount(5);
  await noOverlap();
});

async function startStripDrag(page, name) {
  const grip = await page.getByRole('button', { name: 'Move ' + name + ' toolbar', exact: true }).boundingBox();
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
}
async function savedOrder(page, host = 'workspace', edge = 'top') {
  return page.evaluate(({ host, edge }) => Object.entries(JSON.parse(localStorage.getItem('scansor.toolbars.v1')))
    .filter(([, placement]) => placement.host === host && placement.edge === edge)
    .sort((a, b) => a[1].order - b[1].order).map(([id]) => id), { host, edge });
}

test('edge markers never overlap and global/local corner drops leave placement unchanged', async ({ page }) => {
  await ready(page);
  const toolbar = page.locator('.prototype-toolbar[data-toolbar-id="faces"]');
  const original = await toolbar.boundingBox();
  const saved = await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'));
  const shell = await page.locator('.prototype-shell').boundingBox(), model = await tabset(page, 'Model').boundingBox();
  for (const corner of [{ x: shell.x + 6, y: shell.y + 6 }, { x: model.x + 6, y: model.y + 6 }]) {
    await startStripDrag(page, 'Faces');
    await page.mouse.move(corner.x, corner.y, { steps: 20 });
    await expect(page.locator('.prototype-shell')).toHaveAttribute('data-dragging', 'true');
    await expect(page.locator('.prototype-drop-preview')).toHaveCount(0);
    await expect(page.locator('.prototype-drop-target[data-active="true"]')).toHaveCount(0);
    expect(await page.locator('.prototype-drop-target').evaluateAll(elements => {
      const boxes = elements.map(element => element.getBoundingClientRect());
      return boxes.every((box, i) => boxes.slice(i + 1).every(other =>
        Math.min(box.right, other.right) <= Math.max(box.left, other.left) + 0.1 ||
        Math.min(box.bottom, other.bottom) <= Math.max(box.top, other.top) + 0.1));
    })).toBe(true);
    if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-neutral-corner.png` });
    await page.mouse.up();
    await expect(page.locator('.prototype-shell')).toHaveAttribute('data-dragging', 'false');
    expect(await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'))).toBe(saved);
    await expect(toolbar).toHaveAttribute('data-host', 'workspace');
    await expect(toolbar).toHaveAttribute('data-edge', 'top');
    await expect.poll(async () => Math.abs((await toolbar.boundingBox()).x - original.x) +
      Math.abs((await toolbar.boundingBox()).y - original.y)).toBeLessThan(0.1);
  }
});

test('shared perimeter has distinct workspace and local targets with an explicit preview', async ({ page }) => {
  await ready(page);
  const shell = await page.locator('.prototype-shell').boundingBox(), model = await tabset(page, 'Model').boundingBox();
  const y = model.y + model.height / 2, right = shell.x + shell.width;
  const saved = await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'));
  await startStripDrag(page, 'Faces');
  await page.mouse.move(right - 6, y, { steps: 20 });
  const preview = page.locator('.prototype-drop-preview');
  await expect(preview).toHaveAttribute('data-host', 'workspace');
  await expect(preview.getByRole('status')).toContainText('Workspace · right');
  await page.mouse.move(right - 24, y, { steps: 5 });
  await expect(preview.getByRole('status')).toContainText('Model / Graph · right');
  const host = await preview.getAttribute('data-host');
  expect(host).not.toBe('workspace');
  const global = await page.locator('.prototype-drop-target[data-host="workspace"][data-edge="right"]').boundingBox();
  const local = await page.locator('.prototype-drop-target[data-host="' + host + '"][data-edge="right"]').boundingBox();
  expect(local.x + local.width).toBeLessThanOrEqual(global.x + 0.1);
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-distinct-targets.png` });
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(preview).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'))).toBe(saved);
  await dragTo(page, page.getByRole('button', { name: 'Move Faces toolbar', exact: true }), right - 24, y);
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="faces"]')).toHaveAttribute('data-host', host);
  await dragTo(page, page.getByRole('button', { name: 'Move Faces toolbar', exact: true }), right - 6, y);
  await expect(page.locator('.prototype-toolbar[data-toolbar-id="faces"]')).toHaveAttribute('data-host', 'workspace');
});

test('horizontal wrapped ordering previews before/after, persists, cancels and has menu controls', async ({ page }) => {
  await ready(page);
  await page.evaluate(() => { window.orderedCanvas = document.querySelector('#viewport canvas'); });
  const evaluations = [];
  page.on('request', r => { if (/\/api\/graph\/(evaluate|ensure)(\?|$)/.test(r.url())) evaluations.push(r.url()); });
  const create = page.locator('.prototype-toolbar[data-toolbar-id="create"]');
  const output = page.locator('.prototype-toolbar[data-toolbar-id="output"]');
  const first = await create.boundingBox();
  await startStripDrag(page, 'Run / output');
  await page.mouse.move(first.x + 24, first.y + first.height / 2, { steps: 20 });
  const preview = page.locator('.prototype-drop-preview');
  await expect(preview).toHaveAttribute('data-before', 'create');
  await expect(preview.getByRole('status')).toContainText('Workspace · top · before Create');
  if (process.env.TOOLBAR_SCREENSHOT) await page.screenshot({ path: `${process.env.TOOLBAR_SCREENSHOT}-order-preview.png` });
  await page.mouse.up();
  await expect.poll(() => savedOrder(page)).toEqual(['project', 'output', 'create', 'faces', 'features']);
  await expect.poll(async () => (await output.boundingBox()).x).toBe(first.x);
  expect(await page.evaluate(() => window.orderedCanvas === document.querySelector('#viewport canvas'))).toBe(true);
  expect(evaluations).toEqual([]);
  await page.reload();
  expect(await savedOrder(page)).toEqual(['project', 'output', 'create', 'faces', 'features']);
  const features = await page.locator('.prototype-toolbar[data-toolbar-id="features"]').boundingBox();
  await startStripDrag(page, 'Run / output');
  await page.mouse.move(features.x + features.width + 20, features.y + features.height / 2, { steps: 20 });
  await expect(preview).toHaveAttribute('data-before', '');
  await expect(preview.getByRole('status')).toContainText('after Feature tools');
  const saved = await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'));
  await page.keyboard.press('Escape'); await page.mouse.up();
  expect(await page.evaluate(() => localStorage.getItem('scansor.toolbars.v1'))).toBe(saved);
  expect(await savedOrder(page)).toEqual(['project', 'output', 'create', 'faces', 'features']);
  await dragTo(page, page.getByRole('button', { name: 'Move Run / output toolbar', exact: true }),
    features.x + features.width + 20, features.y + features.height / 2);
  await expect.poll(() => savedOrder(page)).toEqual(['project', 'create', 'faces', 'features', 'output']);
  await toolbarPlace(page, 'Run / output', 'Move earlier');
  await expect.poll(() => savedOrder(page)).toEqual(['project', 'create', 'faces', 'output', 'features']);
  await toolbarPlace(page, 'Run / output', 'Move later');
  await expect.poll(() => savedOrder(page)).toEqual(['project', 'create', 'faces', 'features', 'output']);
});

test('local horizontal rows and vertical columns reorder by the targeted lane', async ({ page }) => {
  await ready(page);
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output']) await toolbarAttach(page, name, 'Model / Graph');
  const faces = page.locator('.prototype-toolbar[data-toolbar-id="faces"]'), host = await faces.getAttribute('data-host');
  const faceBox = await faces.boundingBox();
  await dragTo(page, page.getByRole('button', { name: 'Move Run / output toolbar', exact: true }), faceBox.x + 24, faceBox.y + faceBox.height / 2);
  await expect.poll(() => savedOrder(page, host)).toEqual(['create', 'output', 'faces', 'features']);
  for (const name of ['Create', 'Faces', 'Feature tools', 'Run / output']) await toolbarPlace(page, name, 'Dock left');
  const left = await faces.boundingBox();
  await dragTo(page, page.getByRole('button', { name: 'Move Run / output toolbar', exact: true }), left.x + left.width / 2, left.y + 24);
  await expect.poll(() => savedOrder(page, host, 'left')).toEqual(['create', 'output', 'faces', 'features']);
  const last = await page.locator('.prototype-toolbar[data-toolbar-id="features"]').boundingBox();
  await dragTo(page, page.getByRole('button', { name: 'Move Run / output toolbar', exact: true }), last.x + last.width / 2, last.y + last.height + 20);
  await expect.poll(() => savedOrder(page, host, 'left')).toEqual(['create', 'faces', 'features', 'output']);
  await page.reload();
  expect(await savedOrder(page, host, 'left')).toEqual(['create', 'faces', 'features', 'output']);
});
