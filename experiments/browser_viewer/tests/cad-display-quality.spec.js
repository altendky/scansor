import {test as base, expect} from '@playwright/test';
import {execFileSync, spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {cadDisplayQualityObserver, exerciseCadDisplayQualityUi} from '../../browser_static/cad-display-quality-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url)),
  python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python'),
  environment = {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
    OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'};
const test = base.extend({cadFixture: [async ({}, use) => {
  const fixture = JSON.parse(execFileSync(python, ['-m', 'tests.cad_display_fixture'], {cwd: repository, env: environment})),
    child = spawn(python, ['-m', 'tests.cad_display_fixture', '--serve'],
      {cwd: repository, env: environment, stdio: ['ignore', 'pipe', 'pipe']});
  let log = ''; const collect = bytes => {log = (log + bytes.toString()).slice(-16000);}; child.stderr.on('data', collect);
  try {
    const origin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('CAD display fixture did not start: ' + log)), 30000);
      child.once('error', error => {clearTimeout(timer); reject(error);});
      child.once('exit', code => {clearTimeout(timer); reject(Error(`CAD display fixture exited ${code}: ${log}`));});
      child.stdout.on('data', bytes => {collect(bytes); const match = log.match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) {clearTimeout(timer); resolve(match[0]);}});
    });
    await use({origin, fixture});
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 10000);
      child.kill('SIGINT'); try {await closed;} finally {clearTimeout(timer);}
    }
  }
}, {scope: 'worker'}]});

test('CAD quality refines a real tapered flange and holes without changing physical geometry, export or picking', async ({page, cadFixture}, testInfo) => {
  test.setTimeout(300000); page.setDefaultTimeout(30000);
  const errors = []; page.on('pageerror', error => errors.push(String(error))); page.on('dialog', dialog => dialog.accept());
  await page.route('**/app.js', async route => {
    const response = await route.fetch(); await route.fulfill({response, body: await response.text() + cadDisplayQualityObserver});
  });
  const controlGate = async action => {
    const response = await page.request.post(cadFixture.origin + '/__test/cad-display', {data: {action}});
    expect(response.ok()).toBe(true); return response.json();
  }, gate = {
    arm: () => controlGate('arm'), release: () => controlGate('release'),
    wait: () => expect.poll(async () => (await (await page.request.get(cadFixture.origin + '/__test/cad-display')).json()).entered).toBe(true),
  }, inspectExport = data => JSON.parse(execFileSync(python, ['-m', 'tests.cad_display_fixture', '--inspect-export'],
    {cwd: repository, env: environment, input: data}).toString().trim().split('\n').at(-1));
  await page.goto(cadFixture.origin);
  const report = await exerciseCadDisplayQualityUi({page, expect, fixture: cadFixture.fixture, inspectExport, gate});
  expect(report.complete).toBe(true); expect(errors).toEqual([]);
  const filename = testInfo.outputPath('cad-display-quality-ui.json'); await writeFile(filename, JSON.stringify(report, null, 2) + '\n');
  await testInfo.attach('cad-display-quality-ui', {path: filename, contentType: 'application/json'});
});

test('failed body inspection opens before a gated background CAD refinement retires without mutating the graph', async ({page, cadFixture}, testInfo) => {
  test.setTimeout(120000); page.setDefaultTimeout(30000);
  const errors = [], mutations = [], read = () => page.evaluate(() => globalThis.__cadDisplayUi.read()),
    accepted = state => ({token: state.token, revision: state.revision, recipe: state.recipe,
      results: state.results, states: state.states, errors: state.errors}),
    controlGate = async action => {
      const response = await page.request.post(cadFixture.origin + '/__test/cad-display', {data: {action}});
      expect(response.ok()).toBe(true);
    }, gateStatus = async () => (await (await page.request.get(cadFixture.origin + '/__test/cad-display')).json());
  page.on('pageerror', error => errors.push(String(error))); page.on('dialog', dialog => dialog.accept());
  await page.route('**/app.js', async route => {
    const response = await route.fetch(); await route.fulfill({response, body: await response.text() + cadDisplayQualityObserver});
  });
  await page.goto(cadFixture.origin);
  await expect(page.locator('#load')).toBeEnabled();
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
  await page.locator('#viewport .display-options > summary').click();
  await page.locator('#cad-display-quality').selectOption('draft');
  await page.locator('#scan-file').setInputFiles({name: cadFixture.fixture.filename, mimeType: 'application/octet-stream',
    buffer: Buffer.from(cadFixture.fixture.raw_base64, 'base64')});
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.operation === 'source')).toBe(true);
  const recipe = structuredClone(cadFixture.fixture.recipe), source = recipe.nodes.find(node => node.operation === 'source'), oldSource = source.id;
  Object.assign(source, (await read()).graph.recipe.nodes.find(node => node.operation === 'source'));
  for (const node of recipe.nodes) if (node.source === oldSource) node.source = source.id;
  recipe.nodes.push({id: 'failed-body', label: 'Open single-cap body', operation: 'body', faces: ['flange-top-face'], sewing_tolerance: 1e-6});
  await page.locator('#file').setInputFiles({name: 'failed-body-priority.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'failed-body')).toBe(true);
  await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
  await expect.poll(async () => (await read()).graph.states['failed-body'], {timeout: 60000}).toBe('failed');
  await expect(page.getByRole('button', {name: 'Evaluate all', exact: true})).toBeEnabled();
  if (await page.locator('#clear-feature-selection').isEnabled()) await page.locator('#clear-feature-selection').click();
  const before = accepted((await read()).graph);
  page.on('request', request => {
    if (request.method() === 'POST' && /^\/api\/graph(?:\/import)?$/.test(new URL(request.url()).pathname)) mutations.push(request.url());
  });
  try {
    await controlGate('arm'); await page.locator('#cad-display-quality').selectOption('standard');
    await expect.poll(async () => (await gateStatus()).entered).toBe(true);
    expect((await gateStatus()).released).toBe(false);
    await page.locator('.action-select[data-action-id="failed-body"]').dblclick();
    await expect(page.locator('#body-diagnostics')).toBeVisible({timeout: 5000});
    await expect(page.locator('#body-diagnostics')).toContainText(/open|free|closed|edge/i);
    await expect(page.locator('#feature-properties-panel')).toHaveAttribute('data-edit-owner', 'failed-body');
    const held = await gateStatus();
    expect(held.entered).toBe(true); expect(held.released).toBe(false); expect(held.busy).toBe(true);
    expect(accepted((await read()).graph)).toEqual(before); expect(mutations).toEqual([]); expect(errors).toEqual([]);
    await testInfo.attach('failed-body-inspection-before-refinement-retirement', {contentType: 'application/json',
      body: JSON.stringify({inspector_open_while_kernel_held: true, gate: held, graph_unchanged: true, graph_mutations: mutations})});
  } finally {
    await controlGate('release');
    await expect.poll(async () => (await gateStatus()).busy).toBe(false);
    if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
    await page.locator('#cad-display-quality').selectOption('draft');
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  }
});

test('reloading an already-ready native project automatically renders Standard CAD without Retry or a quality change', async ({page, cadFixture}, testInfo) => {
  test.setTimeout(120000); page.setDefaultTimeout(30000);
  const errors = [], refinements = [], read = () => page.evaluate(() => globalThis.__cadDisplayUi.read()),
    accepted = graph => structuredClone({token: graph.token, revision: graph.revision, recipe: graph.recipe,
      states: graph.states, results: graph.results, errors: graph.errors, diagnostics: graph.diagnostics, derived: graph.derived});
  page.on('pageerror', error => errors.push(String(error))); page.on('dialog', dialog => dialog.accept());
  page.on('request', request => {if (request.method() === 'POST' && /\/api\/graph\/display\/refine$/.test(new URL(request.url()).pathname)) refinements.push(request.url());});
  await page.route('**/app.js', async route => {
    const response = await route.fetch(); await route.fulfill({response, body: await response.text() + cadDisplayQualityObserver});
  });
  await page.goto(cadFixture.origin); await expect(page.locator('#load')).toBeEnabled();
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
  await page.locator('#viewport .display-options > summary').click(); await page.locator('#cad-display-quality').selectOption('draft');
  await page.locator('#scan-file').setInputFiles({name: cadFixture.fixture.filename, mimeType: 'application/octet-stream',
    buffer: Buffer.from(cadFixture.fixture.raw_base64, 'base64')});
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.operation === 'source')).toBe(true);
  const recipe = structuredClone(cadFixture.fixture.recipe), source = recipe.nodes.find(node => node.operation === 'source'), oldSource = source.id;
  Object.assign(source, (await read()).graph.recipe.nodes.find(node => node.operation === 'source'));
  for (const node of recipe.nodes) if (node.source === oldSource) node.source = source.id;
  await page.locator('#file').setInputFiles({name: 'ready-reload-flange.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'flange-body')).toBe(true);
  await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
  await expect.poll(async () => (await read()).graph.states['flange-body'], {timeout: 60000}).toBe('ready');
  const before = accepted((await read()).graph), priorRefinements = refinements.length;
  try {
    await page.reload(); await expect(page.locator('#load')).toBeEnabled();
    await expect(page.getByRole('checkbox', {name: 'Auto', exact: true})).toBeChecked();
    await page.locator('#viewport .display-options > summary').click();
    await expect(page.locator('#cad-display-quality')).toHaveValue('standard');
    await page.locator('#faces-only').check();
    await expect.poll(async () => {
      const state = await read(); return !state.busy && state.cached?.quality === 'standard'
        && state.rendered.some(mesh => mesh.group === 0 && mesh.feature === 'flange-body' && mesh.type === 'Mesh' && mesh.quality === 'standard');
    }, {timeout: 30000}).toBe(true);
    expect(refinements.length).toBeGreaterThan(priorRefinements);
    expect(accepted((await read()).graph)).toEqual(before); expect(errors).toEqual([]);
    await testInfo.attach('ready-reload-automatic-standard', {contentType: 'application/json', body: JSON.stringify({
      automatic_standard_rendered: true, no_retry_or_quality_change: true, accepted_graph_unchanged: true,
      refinement_requests_after_reload: refinements.length - priorRefinements})});
  } finally {
    await page.locator('#cad-display-quality').selectOption('draft');
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  }
});
