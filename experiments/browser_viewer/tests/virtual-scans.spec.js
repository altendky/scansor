import {test as base, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {exerciseVirtualScanUi, installMainObservers, readVirtualScanUi, virtualScanUiObserverSuffix} from '../../browser_static/virtual-scan-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const test = base.extend({
  sourceOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    const collect = data => { log = (log + data.toString()).slice(-16000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Empty native server did not start: ' + log)), 30000);
        child.once('error', error => {clearTimeout(timer); reject(error);});
        child.once('exit', code => {clearTimeout(timer); reject(Error(`Native server exited ${code}: ${log}`));});
        child.stdout.on('data', data => {collect(data); const found = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if (found) {clearTimeout(timer); resolve(found[0]);}});
      });
      await use(origin);
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'), timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.kill('SIGINT'); try {await closed;} finally {clearTimeout(timer);}
      }
    }
  }, {scope: 'worker'}],
});

test('virtual scan controls author coarse geometry, switch observations and reopen either active choice', async ({page, context, sourceOrigin}, testInfo) => {
  test.setTimeout(240000);
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  await context.addInitScript(installMainObservers);
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: (await response.text()) + virtualScanUiObserverSuffix});
  });
  const cdp = await context.newCDPSession(page);
  await page.goto(sourceOrigin);
  await expect(page.locator('[data-add-scan]')).toBeEnabled();
  await expect(page.locator('#active-scan-controls')).toBeHidden();
  let result;
  try {
    result = await exerciseVirtualScanUi({page, sampleHeap: () => cdp.send('Runtime.getHeapUsage')});
    expect(result.complete).toBe(true);
    expect(result.resources).toHaveLength(10);
    expect(result.reopens).toHaveLength(2);
    expect((await readVirtualScanUi(page)).metadata.virtual_scans.virtual).not.toBeNull();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await expect.poll(async () => (await readVirtualScanUi(page)).graph.recipe.nodes.length).toBe(0);
    await page.locator('[data-command-id="choose-example"]:visible').click();
    await page.locator('#example-choice').selectOption('nozzle');
    await page.locator('#open-example').click();
    await expect.poll(async () => (await readVirtualScanUi(page)).graph.recipe.nodes.find(node => node.operation === 'source')?.descriptor).toBeNull();
    await expect(page.locator('#active-scan-controls')).toBeHidden();
    result.cases.push({name: 'empty-and-example-boundaries', complete: true, unsupported_controls_hidden: true});
    expect(errors).toEqual([]);
  } catch (error) {result = error.virtualScanReport; throw error;}
  finally {
    await cdp.detach().catch(() => {});
    if (result) await testInfo.attach('virtual-scan-ui-evidence', {body: Buffer.from(JSON.stringify(result, null, 2)), contentType: 'application/json'});
  }
});
