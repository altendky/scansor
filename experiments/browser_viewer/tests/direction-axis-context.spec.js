import {test as base, expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {execFileSync, spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const environment = {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
  OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'};
const observer = `\nObject.defineProperty(globalThis, '__axisContextUi', {value: {read: () => structuredClone({
  graph: graphState, selectedFeature: selectedFeatureId, vertices: metadata?.vertices,
  builderLines: relationshipOverlays.children.filter(child => child.isLine).map(child => Array.from(child.geometry.getAttribute('position').array)),
  resultLines: overlays.children.filter(child => child.isLine).map(child => Array.from(child.geometry.getAttribute('position').array)),
})}});\n`;

const test = base.extend({
  axisFixture: [async ({}, use) => {
    const fixture = JSON.parse(execFileSync(python, ['-m', 'tests.direction_ui_fixture'], {cwd: repository, env: environment}));
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'],
      {cwd: repository, env: environment, stdio: ['ignore', 'pipe', 'pipe']});
    let log = '';
    const collect = bytes => {log = (log + bytes.toString()).slice(-16000);};
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Native automatic-axis fixture did not start: ' + log)), 30000);
        child.once('error', error => {clearTimeout(timer); reject(error);});
        child.once('exit', code => {clearTimeout(timer); reject(Error(`Native automatic-axis fixture exited ${code}: ${log}`));});
        child.stdout.on('data', bytes => {collect(bytes); const found = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if (found) {clearTimeout(timer); resolve(found[0]);}});
      });
      await use({origin, fixture});
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.kill('SIGINT'); try {await closed;} finally {clearTimeout(timer);}
      }
    }
  }, {scope: 'worker'}],
});

const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = value => Math.hypot(...value);
function containsExactAxis(lines, preview) {
  return lines.some(line => {
    if (line.length !== 6 || !line.every(Number.isFinite)) return false;
    const start = line.slice(0, 3), end = line.slice(3), delta = end.map((value, index) => value-start[index]);
    return norm(delta) > 0 && norm(cross(delta, preview.axis_display))/norm(delta) < 1e-5
      && norm(cross(start.map((value, index) => value-preview.point_display[index]), preview.axis_display)) < 1e-4;
  });
}

test('automatic solved-axis contexts render compact direction previews in builder and saved states', async ({page, axisFixture}, testInfo) => {
  test.setTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: await response.text() + observer});
  });
  const read = () => page.evaluate(() => globalThis.__axisContextUi.read());
  const evaluate = page.getByRole('button', {name: 'Evaluate all', exact: true});
  const evaluateReady = async id => {
    await expect(evaluate).toBeEnabled(); await evaluate.click();
    await expect.poll(async () => {
      const state = (await read()).graph;
      if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }, {timeout: 30000}).toBe('ready');
    await expect(evaluate).toBeEnabled();
  };
  await page.goto(axisFixture.origin);
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#load')).toBeEnabled();
  await page.locator('#scan-file').setInputFiles({name: axisFixture.fixture.filename, mimeType: 'application/octet-stream',
    buffer: Buffer.from(axisFixture.fixture.raw_base64, 'base64')});
  await expect.poll(async () => (await read()).vertices).toBe(axisFixture.fixture.vertices);
  await expect(page.locator('#source-progress')).toBeHidden();
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
  const recipe = structuredClone(axisFixture.fixture.recipe), firstFit = recipe.nodes.findIndex(node => node.operation === 'fit');
  recipe.nodes.splice(firstFit, 0, {id: 'auto-axis', label: 'Automatic shared axis', operation: 'axis',
    initial_parameters: [8, -3, 0.055, -0.015]});
  for (const id of ['axis-cylinder', 'joint-plane']) recipe.nodes.find(node => node.id === id).axis = 'auto-axis';
  await page.locator('#file').setInputFiles({name: 'automatic-axis-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'auto-axis')).toBe(true);
  await evaluateReady('axis-cylinder');
  const baseline = (await read()).graph,
    fixedReference = {feature: 'axis-cylinder', output: 'axis', context: '@axis/auto-axis'},
    freeReference = {feature: 'plane-cylinder', output: 'axis', context: 'plane-cylinder'},
    compact = baseline.input_requirements.relationship_direction.choices.find(output =>
      JSON.stringify(output.reference) === JSON.stringify(fixedReference));
  assert(compact, 'The actual backend must offer the automatic solved context');
  assert.equal(baseline.derived[fixedReference.context], undefined);
  assert.deepEqual(Object.keys(compact.preview).sort(), ['axis_display', 'point_display']);
  assert.equal(compact.properties.direction.binding_eligible, false);
  // A generic global axis guide cannot satisfy the saved-relation assertions.
  const display = page.locator('#all-guides').locator('xpath=ancestor::details[1]');
  if (!await display.evaluate(node => node.open)) await display.locator('summary').click();
  await page.locator('#all-guides').uncheck();
  await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
  await page.locator('#relationship-kind').selectOption('direction_alignment');
  if (await page.locator('#clear-relationship-participants').isEnabled()) await page.locator('#clear-relationship-participants').click();
  const options = page.locator('#relationship-dialog details');
  if (!await options.evaluate(node => node.open)) await options.locator('summary').click();
  await page.locator('#new-relationship-label').fill('Exact automatic axis read');
  await page.getByRole('button', {name: 'Show Participants choices', exact: true}).click();
  for (const reference of [fixedReference, freeReference]) {
    const inputs = page.locator('#relationship-participants input'), key = JSON.stringify(reference),
      index = await inputs.evaluateAll((elements, key) => elements.findIndex(element => element.dataset.participantId === key), key);
    assert(index >= 0, `Missing exact reference ${key}`);
    await inputs.nth(index).check();
  }
  await expect(page.locator('#relationship-summary')).toContainText('Parallel axis directions');
  await expect.poll(async () => containsExactAxis((await read()).builderLines, compact.preview)).toBe(true);
  assert.deepEqual(errors, []);
  await page.keyboard.press('Escape');
  await page.locator('#add-relationship').click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.label === 'Exact automatic axis read')).toBe(true);
  const saved = (await read()).graph.recipe.nodes.find(node => node.label === 'Exact automatic axis read');
  assert.deepEqual(new Set(saved.directions.map(reference => JSON.stringify(reference))), new Set([fixedReference, freeReference].map(reference => JSON.stringify(reference))));
  assert.equal((await read()).graph.results[saved.id], undefined);
  if ((await read()).selectedFeature !== saved.id) await page.locator(`.action-select[data-action-id="${saved.id}"]`).click();
  await expect.poll(async () => (await read()).selectedFeature).toBe(saved.id);
  await expect.poll(async () => containsExactAxis((await read()).resultLines, compact.preview)).toBe(true);
  assert.deepEqual(errors, []);
  await evaluateReady(saved.id);
  if ((await read()).selectedFeature !== saved.id) await page.locator(`.action-select[data-action-id="${saved.id}"]`).click();
  await expect.poll(async () => (await read()).selectedFeature).toBe(saved.id);
  await expect.poll(async () => containsExactAxis((await read()).resultLines, compact.preview)).toBe(true);
  const ready = (await read()).graph;
  assert.deepEqual(ready.recipe.nodes.find(node => node.id === saved.id).directions, saved.directions);
  assert.equal(ready.derived[fixedReference.context], undefined);
  assert.deepEqual(errors, []);
  const report = {complete: true, exact_reference: fixedReference, compact_preview: compact.preview,
    all_available_guides_disabled: true, builder_guide: true, saved_unevaluated_guide: true, saved_ready_guide: true, page_errors: errors};
  const reportPath = testInfo.outputPath('direction-axis-context.json');
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  await testInfo.attach('direction-axis-context', {path: reportPath, contentType: 'application/json'});
});
