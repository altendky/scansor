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
