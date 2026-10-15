import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile, writeFile } from 'node:fs/promises';
import { rawAppObserverSuffix, runRawSourceWorkflowUi } from './raw-source-workflow-harness.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_NATIVE_PYTHON || process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const rawPath = path.join(repository, 'examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply');
const test = base.extend({
  rawServer: [async ({}, use, testInfo) => {
    const child = spawn(python, ['-m', 'experiments.nozzle_browser', '--empty', '--port', '0'], {
      cwd: repository, env: { ...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '', forced = false;
    const record = { pid: child.pid, complete: false, interpreter: python };
    const collect = bytes => { log = (log + bytes.toString()).slice(-32000); };
    child.stderr.on('data', collect);
    try {
      const origin = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Raw native server startup: ' + log)), 30000);
        child.once('error', error => { clearTimeout(timer); reject(error); });
        child.once('exit', code => { clearTimeout(timer); reject(Error(`Raw native server exited ${code}: ${log}`)); });
        child.stdout.on('data', bytes => { collect(bytes); const match = log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if (match) { clearTimeout(timer); resolve(match[0]); } });
      });
      record.origin = origin;
      await use({ origin, pid: child.pid });
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'), timer = setTimeout(() => { forced = true; child.kill('SIGKILL'); }, 5000);
        child.kill('SIGINT'); try { await closed; } finally { clearTimeout(timer); }
      }
      Object.assign(record, { forced_cleanup: forced, exit_code: child.exitCode, signal: child.signalCode, log,
        complete: !forced && (child.exitCode === 0 || child.signalCode === 'SIGINT') });
      await writeFile(testInfo.outputPath('raw-native-server.json'), JSON.stringify(record, null, 2) + '\n');
      expect(record.complete, 'owned native server closed normally').toBe(true);
    }
  }, { scope: 'test' }],
});

test('original raw File pointer cylinder CAD download Save New renamed locate and reevaluate', async ({ page, rawServer }, testInfo) => {
  test.setTimeout(240000);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const pageErrors = [], report = { transport: 'native-http', server: rawServer, page_errors: pageErrors };
  page.on('pageerror', error => pageErrors.push(String(error)));
  await page.route('**/app.js', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()) + rawAppObserverSuffix });
  });
  const output = testInfo.outputPath('raw-workflow'), destination = testInfo.outputPath('raw-workflow.json');
  const checkpoint = value => writeFile(destination, JSON.stringify(value, null, 2) + '\n');
  try {
    await page.goto(rawServer.origin);
    await page.waitForFunction(() => !!globalThis.__rawWorkflow296);
    await runRawSourceWorkflowUi({ page, rawPath, output, report, checkpoint,
      snapshot: options => page.evaluate(options => globalThis.__rawWorkflow296.snapshot(options), options),
      projection: () => page.evaluate(() => globalThis.__rawWorkflow296.projection()) });
    // The same independent raw-byte/STEP/native-replay oracle is used by the
    // packaged-worker wrapper. It consumes actual authored records/downloads.
    const validation = testInfo.outputPath('raw-workflow-validation.json');
    try {
      execFileSync(python, [path.join(repository, 'experiments/browser_static/raw_workflow_expected.py'),
        '--raw', rawPath, '--report', destination, '--cad', report.downloads.cad.path, '--output', validation], {
        cwd: repository, env: { ...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
          OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' }, maxBuffer: 16 * 1024 * 1024, timeout: 180000, killSignal: 'SIGTERM',
      });
      report.oracle_process = { status: 0, timeout_ms: 180000 };
    } catch (error) {
      report.oracle_process = { status: error.status ?? null, signal: error.signal ?? null, code: error.code ?? null,
        stdout: error.stdout?.toString('utf8') ?? '', stderr: error.stderr?.toString('utf8') ?? '', timeout_ms: 180000 };
      throw error;
    }
    const checked = JSON.parse(await readFile(validation, 'utf8'));
    expect(checked.complete).toBe(true);
    expect(Object.keys(checked.checks).sort()).toEqual(['raw_decode', 'incident_weights', 'all_selected_rows', 'residual_objective',
      'support', 'download_metadata', 'reference_mesh', 'step_readback', 'native_replay', 'semantic_parity'].sort());
    expect(Object.values(checked.checks).every(value => value === true)).toBe(true);
    report.independent_validation = { path: validation, complete: checked.complete, checks: checked.checks };
    await testInfo.attach('raw-workflow-independent-validation', { path: validation, contentType: 'application/json' });
    expect(pageErrors).toEqual([]);
  } catch (error) { report.complete = false; report.failure = String(error); throw error; }
  finally {
    report.page_errors = pageErrors;
    if (pageErrors.length) report.complete = false;
    await checkpoint(report);
    await testInfo.attach('raw-source-workflow', { path: destination, contentType: 'application/json' });
  }
});
