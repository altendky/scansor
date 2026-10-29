import {test as base, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {exerciseStlSourceUi} from '../../browser_static/stl-source-ui.mjs';
import {installMainObservers, virtualScanUiObserverSuffix} from '../../browser_static/virtual-scan-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const test = base.extend({
  sourceOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    const collect = data => {log = (log + data.toString()).slice(-16000);};
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Native STL server did not start: ' + log)), 30000);
        child.once('error', error => {clearTimeout(timer); reject(error);});
        child.once('exit', code => {clearTimeout(timer); reject(Error(`Native STL server exited ${code}: ${log}`));});
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

for (const encoding of ['ascii', 'binary']) test(`STL ${encoding} uses real fitting, actions, virtual scans and portable projects`, async ({page, context, sourceOrigin}, testInfo) => {
  test.setTimeout(360000);
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
  let report;
  try {
    await page.goto(sourceOrigin);
    report = await exerciseStlSourceUi({page, encoding, sampleHeap: () => cdp.send('Runtime.getHeapUsage')});
    expect(report.complete).toBe(true);
    expect(report.visible_normal_provenance).toBe(true);
    expect(report.archives).toHaveLength(2);
    expect(report.virtual_workflow.reopens).toHaveLength(2);
    expect(errors).toEqual([]);
  } catch (error) {report = error.stlSourceReport || report; throw error;}
  finally {
    await cdp.detach().catch(() => {});
    if (report) await testInfo.attach('stl-source-ui-evidence', {body: Buffer.from(JSON.stringify(report, null, 2)), contentType: 'application/json'});
  }
});

test('cancelled STL normals import with a persistent warning and retain both triangles', async ({page, sourceOrigin}) => {
  const bytes = Buffer.alloc(184);
  bytes.writeUInt32LE(2, 80);
  let offset = 84;
  for (const face of [ [[0,0,0], [1,0,0], [0,1,0]], [[0,0,0], [0,1,0], [1,0,0]] ]) {
    for (const value of [0, 0, -1, ...face.flat()]) {bytes.writeFloatLE(value, offset); offset += 4;}
    offset += 2;
  }
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  await page.goto(sourceOrigin);
  const empty = async () => {
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await expect(page.locator('#empty-project')).toBeVisible();
  };
  await empty();
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('[data-add-scan]').click()]);
  await chooser.setFiles({name: 'opposite.stl', mimeType: 'application/octet-stream', buffer: bytes});
  const warning = page.locator('#source-import-warning');
  await expect(warning).toBeVisible();
  await expect(warning).toContainText('3 vertices');
  await expect(warning).toContainText('ambiguous');
  await expect(warning).toContainText('All vertices, triangles and original file bytes are preserved');
  const meta = await (await page.request.get(new URL('/api/meta', sourceOrigin).href)).json();
  expect(meta.vertices).toBe(3);
  expect(meta.triangles).toBe(2);
  expect(meta.diagnostics.fallback_normal_first_ids).toEqual([0,1,2]);
  await page.reload();
  await expect(warning).toBeVisible();
  await expect(warning).toContainText('first IDs [0, 1, 2]');
  await empty();
  await expect(warning).toBeHidden();
  expect(errors).toEqual([]);
});
