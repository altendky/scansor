import {test as base, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {exerciseProjectArchiveUi, projectArchiveOracle, projectArchiveUiObserverSuffix} from '../../browser_static/project-archive-ui.mjs';
import {installMainObservers} from '../../browser_static/virtual-scan-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const test = base.extend({
  projectOrigin: [async ({}, use) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    const collect = data => { log = (log + data.toString()).slice(-16000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Native project server did not start: ' + log)), 30000);
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

test('one-file projects preserve either active scan, edits, drafts and portable legacy references', async ({page, context, projectOrigin}, testInfo) => {
  test.setTimeout(240000);
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  await context.addInitScript(installMainObservers);
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({response, body: (await response.text()) + projectArchiveUiObserverSuffix});
  });
  let report;
  try {
    await page.goto(projectOrigin);
    report = await exerciseProjectArchiveUi({page, ...projectArchiveOracle(python), cancelOpen: async () => {
      let release, arrived;
      const held = new Promise(resolve => {release = resolve;});
      const receiving = new Promise(resolve => {arrived = resolve;});
      const pattern = '**/api/sources/imports/*/bytes';
      const handler = async route => {arrived(); await held; await route.continue();};
      await page.route(pattern, handler);
      return {arrived: () => receiving, release: async () => {release(); await page.unroute(pattern, handler);}};
    }});
    expect(report.complete).toBe(true);
    expect(report.archives).toHaveLength(2);
    expect(report.cases.map(value => value.name)).toEqual([
      'both-active-choices', 'failed-open-keeps-draft', 'cancelled-open-keeps-draft', 'known-legacy-portable-migration']);
    expect(errors).toEqual([]);
  } catch (error) {report = error.projectArchiveReport || report; throw error;}
  finally {
    if (report) await testInfo.attach('project-archive-ui-evidence', {body: Buffer.from(JSON.stringify(report, null, 2)), contentType: 'application/json'});
  }
});
