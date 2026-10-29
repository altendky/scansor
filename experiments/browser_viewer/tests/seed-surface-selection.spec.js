import {test, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {exerciseSeedSurfaceUi, seedSurfaceObserver} from '../../browser_static/seed-surface-ui.mjs';

test.use({actionTimeout: 10000});
const repository = fileURLToPath(new URL('../../../', import.meta.url));
let child, origin, log = '';
test.beforeAll(async () => {
  child = spawn(`${repository}/.venv/bin/python`, ['-m', 'experiments.nozzle_browser', '--port', '0',
    '--example', 'examples/nozzle-bayonette-simplified', '--recipe', 'examples/nozzle-bayonette-simplified/recipes/cone-plane.json'],
  {cwd: repository, env: {...process.env, PYTHONPATH: `${repository}:${repository}/src`, OPENBLAS_NUM_THREADS: '1'},
    stdio: ['ignore', 'pipe', 'pipe']});
  child.stderr.on('data', data => { log = (log + data.toString()).slice(-16000); });
  origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Surface workflow server did not start: ' + log)), 20000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(Error(`Surface workflow server exited ${code}: ${log}`)); });
    let output = '';
    child.stdout.on('data', data => {
      output += data.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (match) { clearTimeout(timer); resolve(match[0]); }
    });
  });
});
test.afterAll(async () => {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 5000);
  child.kill('SIGINT');
  try { await closed; } finally { clearTimeout(timer); }
});

test('native seed surface preview, barrier, setback, Fit and reopen use the actual controls', async ({page}) => {
  test.setTimeout(90000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: await response.text() + seedSurfaceObserver});
  });
  await page.goto(origin);
  const result = await exerciseSeedSurfaceUi({page, expect});
  expect(result.afterInset).toBeLessThan(result.beforeInset);
  expect(result.reopened_ids).toEqual(result.membership);
  expect(errors).toEqual([]);
});
