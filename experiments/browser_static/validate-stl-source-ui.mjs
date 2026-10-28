// Actual shipped worker UI, including archive-backed Force, in both isolation modes.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {prepareReports, sha256, startPreview, verifySite} from './server.mjs';
import {exerciseStlSourceUi} from './stl-source-ui.mjs';
import {installMainObservers, virtualScanUiObserverSuffix} from './virtual-scan-ui.mjs';

const options = {};
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  assert(['--directory', '--output', '--playwright-directory'].includes(name));
  assert(value && !value.startsWith('--') && !(name in options));
  options[name] = value;
}
assert(options['--directory'] && options['--output'], 'Required --directory and fresh --output');
const repository = fileURLToPath(new URL('../..', import.meta.url));
const playwrightDirectory = path.resolve(options['--playwright-directory'] || path.join(repository, 'experiments/browser_viewer'));
const site = await verifySite(options['--directory']);
assert.equal(site.manifest.backend.type, 'worker');
const runtime = JSON.parse(await readFile(path.join(site.root, site.manifest.runtime.path, 'manifest.json'), 'utf8'));
assert(runtime.worker_protocol.source_import.formats.includes('triangle-stl-f32-v1'));
assert.equal(runtime.worker_protocol.project_archive.supported, true);
assert(runtime.source_files.some(record => record.path === 'src/scansor/mesh_stl.py'));
const output = await prepareReports(options['--output'], [site.root, repository]);
const report = {schema: 'scansor-stl-source-worker-ui-v1', complete: false,
  source_revision: site.manifest.source_revision, manifest_sha256: site.manifest_sha256,
  runner_sha256: sha256(await readFile(new URL(import.meta.url))),
  harness_sha256: sha256(await readFile(new URL('./stl-source-ui.mjs', import.meta.url))),
  profiles: [], external_requests: [], api_requests: [], page_errors: [], cleanup_errors: []};
const {chromium} = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
let browser, preview;
try {
  browser = await chromium.launch({headless: true});
  report.browser = {name: 'chromium', version: browser.version()};
  for (const isolation of [false, true]) {
    const prefix = '/stl-source-workflow/nested/';
    preview = await startPreview(site, {prefix, isolation});
    const context = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1440, height: 1000}, acceptDownloads: true});
    const profile = {isolation_requested: isolation, complete: false, workflows: []};
    report.profiles.push(profile);
    try {
      await context.addInitScript(installMainObservers);
      context.on('request', request => {
        const url = request.url();
        if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) report.external_requests.push(url);
        if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
      });
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== preview.origin || /\/api(?:\/|$)/.test(url.pathname)) return route.abort();
        if (!url.pathname.endsWith('/app.js')) return route.continue();
        const relative = url.pathname.slice(prefix.length), record = site.records.get(relative);
        assert(record, 'Observed app must be manifest-listed');
        const bytes = await readFile(path.join(site.root, relative));
        assert.equal(bytes.length, record.bytes); assert.equal(sha256(bytes), record.sha256);
        await route.fulfill({status: 200, contentType: 'text/javascript', body: Buffer.concat([bytes, Buffer.from(virtualScanUiObserverSuffix)]),
          headers: isolation ? {'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp'} : {}});
      });
      const page = await context.newPage();
      page.setDefaultTimeout(60000);
      page.on('dialog', dialog => dialog.accept());
      page.on('pageerror', error => report.page_errors.push(String(error)));
      const cdp = await context.newCDPSession(page);
      try {
        await page.goto(preview.origin + prefix + 'app/');
        for (const encoding of ['ascii', 'binary']) {
          try {
            profile.workflows.push(await exerciseStlSourceUi({page, encoding, force: true,
              sampleHeap: () => cdp.send('Runtime.getHeapUsage')}));
          } catch (error) {profile.workflows.push(error.stlSourceReport || {encoding, failure: String(error)}); throw error;}
          await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
        }
        profile.isolation_actual = {page: await page.evaluate(() => crossOriginIsolated),
          worker: await page.workers()[0].evaluate(() => crossOriginIsolated)};
        assert.deepEqual(profile.isolation_actual, {page: isolation, worker: isolation});
        profile.complete = true;
      } finally {await cdp.detach();}
    } finally {await context.close(); await preview.close(); preview = null;}
  }
  assert.deepEqual(report.external_requests, []);
  assert.deepEqual(report.api_requests, []);
  assert.deepEqual(report.page_errors, []);
  report.complete = true;
} catch (error) {report.failure = String(error); throw error;}
finally {
  for (const [label, action] of [['preview', () => preview?.close()], ['browser', () => browser?.close()]]) {
    try {await action();} catch (error) {report.cleanup_errors.push({label, error: String(error)});}
  }
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
}
