import {test as base, expect} from '@playwright/test';
import {execFileSync, spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {exercisePatternRelationshipUi, patternRelationshipObserver} from '../../browser_static/pattern-relationship-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const environment = {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
  OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'};
const test = base.extend({patternFixture: [async ({}, use) => {
  const fixture = JSON.parse(execFileSync(python, ['-c', `import base64, json
from tests.circular_reuse_fixture import circular_fixture
raw, workspace, recipe, circular, expected = circular_fixture(count=6)
print(json.dumps(dict(filename='six-circular-holes.ply', raw_base64=base64.b64encode(raw).decode(),
    vertices=len(workspace.local), recipe=recipe.model_dump(mode='json'), circular=circular)))
`], {cwd: repository, env: environment}));
  const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'],
    {cwd: repository, env: environment, stdio: ['ignore', 'pipe', 'pipe']});
  let log = ''; const collect = bytes => {log = (log + bytes.toString()).slice(-16000);};
  child.stderr.on('data', collect);
  try {
    const origin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Pattern fixture did not start: ' + log)), 30000);
      child.once('error', error => {clearTimeout(timer); reject(error);});
      child.once('exit', code => {clearTimeout(timer); reject(Error(`Pattern fixture exited ${code}: ${log}`));});
      child.stdout.on('data', bytes => {collect(bytes); const match = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
        if (match) {clearTimeout(timer); resolve(match[0]);}});
    });
    await use({origin, fixture});
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 5000);
      child.kill('SIGINT'); try {await closed;} finally {clearTimeout(timer);}
    }
  }
}, {scope: 'worker'}]});

test('pattern directions use one live collection across tree picking, properties, resize and reopen', async ({page, patternFixture}, testInfo) => {
  test.setTimeout(240000); page.setDefaultTimeout(30000);
  const errors = []; page.on('pageerror', error => errors.push(String(error))); page.on('dialog', dialog => dialog.accept());
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: await response.text() + patternRelationshipObserver});
  });
  await page.goto(patternFixture.origin);
  const report = await exercisePatternRelationshipUi({page, expect, fixture: patternFixture.fixture});
  expect(report.complete).toBe(true); expect(errors).toEqual([]);
  const reportPath = testInfo.outputPath('pattern-relationship-ui.json');
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  await testInfo.attach('pattern-relationship-ui-evidence', {path: reportPath, contentType: 'application/json'});
});
