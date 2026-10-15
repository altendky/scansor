import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { assertPrototype, runSupportIntentUi } from './support-intent-harness.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_NATIVE_PYTHON || process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const environment = { ...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter), OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' };
let fixtures;
const test = base.extend({
  supportOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], { cwd: repository, env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    const collect = data => { log = (log + data.toString()).slice(-16000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Support native server did not start: ' + log)), 30000);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(Error(`Support server exited ${code}: ${log}`)); });
        child.stdout.on('data', data => { collect(data); const found = log.match(/http:\/\/127\.0\.0\.1:\d+\//); if (found) { clearTimeout(timer); resolve(found[0]); } });
      });
      await use(origin);
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.kill('SIGINT');
        try { await closed; } finally { clearTimeout(timer); }
      }
    }
  }, { scope: 'worker' }],
});
test.beforeAll(() => {
  fixtures = JSON.parse(execFileSync(python, ['-c', 'import json; from tests.support_ui_fixture import support_ui_fixture; print(json.dumps({kind:support_ui_fixture(kind) for kind in ("cylinder","cone")}))'], { cwd: repository, env: environment, encoding: 'utf8' }));
});
test('support UI endpoint oracle rejects malformed geometry instead of passing vacuously', () => {
  const fixture = { prototype_endpoints: [[21, 6, 27], [29, 6, 27]] },
    valid = { analytic: { origin: [25, 6, 27], axis: [1, 0, 0], radius: 2.5, slope: 0, axial_domain: [-4, 4] }, support_intent: { kind: 'reference-plane', interval: [1, 9] } };
  assert.doesNotThrow(() => assertPrototype(valid, fixture));
  for (const [key, malformed] of [['origin', []], ['origin', [25, 6]], ['axis', []], ['axis', [1, 0]], ['axis', [0, 0, 0]],
    ['axial_domain', []], ['axial_domain', [-4]], ['axial_domain', [4, -4]], ['axial_domain', [0, 0]], ['axial_domain', null],
    ['radius', true], ['slope', Infinity]]) {
    const value = structuredClone(valid);
    value.analytic[key] = malformed;
    assert.throws(() => assertPrototype(value, fixture), `invalid ${key}: ${JSON.stringify(malformed)}`);
  }
});
for (const kind of ['cylinder', 'cone']) test(`managed ${kind} support Cancel Apply None Prototype and actions replay through actual native UI`, async ({ page, supportOrigin }, testInfo) => {
  const report = { transport: 'native-http' }, errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const snapshot = async () => (await page.request.get(new URL('/api/meta', supportOrigin).href)).json();
  const current = await snapshot();
  const reset = await page.request.post(new URL('/api/project/reset', supportOrigin).href, { headers: { 'X-Scansor-Request': '1' }, data: { generation: current.project_generation, token: current.graph.token, confirmedDiscard: true } });
  expect(reset.ok(), await reset.text()).toBe(true);
  await page.goto(supportOrigin);
  await expect(page.locator('[data-add-scan]')).toBeEnabled();
  try {
    await runSupportIntentUi({ page, fixture: fixtures[kind], snapshot, report,
      checkpoint: value => writeFile(testInfo.outputPath('support-ui-evidence.json'), JSON.stringify(value, null, 2) + '\n') });
    expect(errors).toEqual([]);
  } catch (error) {
    report.complete = false;
    report.failure = String(error);
    throw error;
  } finally {
    report.page_errors = errors;
    await writeFile(testInfo.outputPath('support-ui-evidence.json'), JSON.stringify(report, null, 2) + '\n');
    await testInfo.attach('support-ui-evidence', { path: testInfo.outputPath('support-ui-evidence.json'), contentType: 'application/json' });
  }
});
