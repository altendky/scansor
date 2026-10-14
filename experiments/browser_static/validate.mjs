import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { verifySite, startPreview, prepareReports, sha256 } from './server.mjs';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing ${name}`);
  return args[index + 1];
};
const directory = argument('--directory'), outputArgument = argument('--output');
if (!directory || !outputArgument)
  throw new Error('Usage: node validate.mjs --directory <site> --output <fresh external reports> [--playwright-directory experiments/browser_viewer] [--repository <checkout>] [--browser chromium|firefox|webkit] [--isolation]');
const site = await verifySite(directory);
const playwrightDirectory = path.resolve(argument('--playwright-directory', 'experiments/browser_viewer'));
const repository = path.resolve(argument('--repository', path.join(playwrightDirectory, '../..')));
const output = await prepareReports(outputArgument, [site.root, repository]);
const browserName = argument('--browser', 'chromium');
if (!['chromium', 'firefox', 'webkit'].includes(browserName))
  throw new Error(`Unsupported browser ${browserName}`);
const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const isolation = args.includes('--isolation');
const report = {
  started_utc: new Date().toISOString(), manifest_sha256: site.manifest_sha256,
  source_revision: site.manifest.source_revision, source_worktree_dirty: site.manifest.source_worktree_dirty,
  runtime: site.manifest.runtime, capabilities: site.manifest.capabilities,
  isolation, cases: [], artifact_unchanged: null,
};
const runtimeAsset = [...site.records.keys()].find(name => /\/runtime\/pyodide\.asm\.wasm$/.test(name));
if (!runtimeAsset) throw new Error('Missing hashed Pyodide WASM asset for failure-injection case');
const runtimeWorker = `${site.manifest.runtime?.path}/worker.mjs`;
if (!site.records.has(runtimeWorker)) throw new Error('Missing runtime worker for post-ready failure case');
const injectedFailure = 'Injected runtime worker failure after readiness.';
const injectionDelay = 1000;
let browser;
try {
  browser = await playwright[browserName].launch({ headless: true });
  report.browser = { name: browserName, version: browser.version() };
  for (const prefix of ['/', '/preview/nested/']) {
    const preview = await startPreview(site, { prefix, isolation });
    try {
      for (const scenario of ['ready', 'missing-runtime', 'unsupported-backend', 'post-ready-failure']) {
        const entry = {
          prefix, scenario, requests: [], unexpected_requests: [], api_requests: [],
          page_errors: [], console_errors: [], response_modifications: [], checks: {},
        };
        report.cases.push(entry);
        const context = await browser.newContext({ serviceWorkers: 'block' });
        const targetMissing = preview.origin + prefix + runtimeAsset;
        const targetWorker = preview.origin + prefix + runtimeWorker;
        let missingRuntime = 0;
        await context.route('**/*', async route => {
          const url = route.request().url();
          entry.requests.push(url);
          if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) {
            entry.unexpected_requests.push(url);
            await route.abort();
            return;
          }
          if (url.startsWith(preview.origin + '/') && /\/api(?:\/|$)/.test(new URL(url).pathname)) {
            entry.api_requests.push(url);
            await route.abort();
            return;
          }
          if (scenario === 'missing-runtime' && url === targetMissing) {
            missingRuntime++;
            await route.fulfill({ status: 404, contentType: 'text/plain', body: 'Missing runtime asset' });
            return;
          }
          if (scenario === 'post-ready-failure' && url === targetWorker) {
            const response = await route.fetch();
            const original = await response.text();
            const body = `const originalPostMessage = self.postMessage.bind(self);
let injectedFailureScheduled = false;
self.postMessage = (message, ...options) => {
  originalPostMessage(message, ...options);
  if (!injectedFailureScheduled && message?.ok === true && message?.ready === true) {
    injectedFailureScheduled = true;
    setTimeout(() => originalPostMessage({ fatal: ${JSON.stringify(injectedFailure)} }), ${injectionDelay});
  }
};
` + original;
            entry.response_modifications.push({
              path: runtimeWorker, kind: 'fatal message after actual ready reply', delay_ms: injectionDelay,
              original_sha256: sha256(Buffer.from(original)), modified_sha256: sha256(Buffer.from(body)),
            });
            await route.fulfill({ response, contentType: 'text/javascript', body });
            return;
          }
          await route.continue();
        });
        const page = await context.newPage();
        page.on('pageerror', error => entry.page_errors.push(String(error)));
        page.on('console', message => {
          if (message.type() === 'error') entry.console_errors.push(message.text());
        });
        try {
          const appPath = site.manifest.entry === 'app/index.html' ? 'app/' : site.manifest.entry;
          const url = preview.origin + prefix + appPath +
            (scenario === 'unsupported-backend' ? '?backend=unavailable' : '');
          await page.goto(url, { waitUntil: 'domcontentloaded' });
          if (scenario === 'ready') {
            await page.waitForFunction(() => document.querySelector('#status')?.dataset.runtimeReady === 'true',
              null, { timeout: 180000 });
            const status = await page.locator('#status').textContent();
            entry.status = status;
            entry.checks.runtime_ready = await page.locator('#status').getAttribute('data-runtime-ready') === 'true';
            entry.checks.numerical_workspace_ready = site.manifest.capabilities.numerical === true &&
              await page.locator('canvas').count() === 1 && await page.locator('#save').isEnabled();
            entry.checks.fit_enabled = await page.locator('#new-fit').isEnabled();
            entry.checks.cad_disabled = await page.locator('#export-cad').isDisabled();
            const show = page.getByRole('button', { name: 'Show panel', exact: true });
            entry.checks.layout_show_enabled = await show.isEnabled();
            await show.click();
            entry.checks.runtime_assets_same_prefix = entry.requests.some(request =>
              request === preview.origin + prefix + runtimeAsset);
          } else {
            if (scenario === 'post-ready-failure') {
              // The worker can fail while workspace initialization is still
              // running; both that race and a later failure must retire the UI.
            }
            await page.locator('#startup-error').waitFor({ state: 'visible',
              timeout: scenario === 'missing-runtime' ? 60000 : 15000 });
            entry.startup_detail = await page.locator('#startup-detail').textContent();
            entry.status = await page.locator('#status').textContent();
            entry.checks.startup_detail_present = !!entry.startup_detail.trim();
            entry.checks.runtime_not_ready = await page.locator('#status').getAttribute('data-runtime-ready') !== 'true';
            entry.checks.fit_disabled = await page.locator('#new-fit').isDisabled();
            if (scenario === 'missing-runtime')
              entry.checks.missing_runtime_reported = missingRuntime > 0 &&
                /missing asset.*404/i.test(entry.startup_detail);
            else if (scenario === 'post-ready-failure') {
              entry.checks.injected_failure_reported = entry.response_modifications.length === 1 &&
                entry.startup_detail.includes(injectedFailure);
              entry.checks.ready_status_cleared = !/\bready\b/i.test(entry.status);
            }
            else entry.checks.unsupported_backend_explicit =
              /unavailable|unsupported/i.test(entry.startup_detail) && /backend/i.test(entry.startup_detail);
          }
          entry.checks.no_external_requests = entry.unexpected_requests.length === 0;
          entry.checks.no_http_computation_fallback = entry.api_requests.length === 0;
          entry.checks.no_page_errors = entry.page_errors.length === 0;
          entry.checks.isolation_matches_preview = await page.evaluate(() => crossOriginIsolated) === isolation;
          const failed = Object.entries(entry.checks).filter(([, passed]) => !passed).map(([name]) => name);
          if (failed.length) throw new Error(JSON.stringify({
            prefix, scenario, failed, status: entry.status, detail: entry.startup_detail,
          }));
          console.log(prefix, scenario, 'passed');
        } catch (error) { entry.failure = String(error); throw error; }
        finally {
          entry.missing_runtime_requests = missingRuntime;
          await context.close();
          await writeFile(path.join(output, 'browser-static.json'), JSON.stringify(report, null, 2) + '\n');
        }
      }
    } finally {
      report.static_requests ??= [];
      report.static_requests.push({ prefix, requests: preview.requests });
      await preview.close();
    }
  }
  report.finished_utc = new Date().toISOString();
} finally {
  if (browser) await browser.close();
  let verificationError;
  try {
    const after = await verifySite(directory);
    report.artifact_unchanged = after.manifest_sha256 === site.manifest_sha256;
  } catch (error) {
    report.artifact_unchanged = false;
    report.artifact_verification_error = String(error);
    verificationError = error;
  }
  await writeFile(path.join(output, 'browser-static.json'), JSON.stringify(report, null, 2) + '\n');
  if (!report.artifact_unchanged)
    throw verificationError ?? new Error('Artifact manifest changed during browser validation');
}
console.log(JSON.stringify({
  browser: report.browser, cases: report.cases.length, artifact_unchanged: report.artifact_unchanged,
  report: path.join(output, 'browser-static.json'),
}, null, 2));
