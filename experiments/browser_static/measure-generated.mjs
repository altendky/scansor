import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
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
  throw new Error('Usage: node measure-generated.mjs --directory <clean verified nozzle site> --output <fresh external reports> [--playwright-directory experiments/browser_viewer] [--native-python <existing interpreter>] [--repository <matching source repository>]');
const sourceSite = await verifySite(directory);
if (sourceSite.manifest.benchmark || sourceSite.manifest.bounded_examples?.[0]?.id !== 'nozzle')
  throw new Error('Expected a clean retained-nozzle input artifact, not an already remapped benchmark');
const playwrightDirectory = path.resolve(argument('--playwright-directory', 'experiments/browser_viewer'));
const repository = path.resolve(argument('--repository', path.join(playwrightDirectory, '../..')));
const harnessRepository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = await prepareReports(outputArgument, [sourceSite.root, repository, harnessRepository, path.resolve(playwrightDirectory, '../..')]);
const nativePython = path.resolve(argument('--native-python', path.join(repository, '.venv/bin/python')));
const helper = path.join(path.dirname(fileURLToPath(import.meta.url)), 'generated_cases.py');
const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const report = { started_utc: new Date().toISOString(), benchmark: 'generated fixture remaps, never production nozzle',
  input_site_manifest_sha256: sourceSite.manifest_sha256,
  harness: { javascript_sha256: sha256(await readFile(fileURLToPath(import.meta.url))), python_sha256: sha256(await readFile(helper)) },
  timing_policy: 'Single diagnostic run each, loopback, sequential. Routing disables cache; no controlled performance conclusion. Startup auto evaluation computes the fits; post-startup evaluation is cached.',
  memory_policy: 'Transfer copies are observed buffer sizes; WebGL bufferData bytes are cumulative submissions, not live allocation. Native ndarray.nbytes is logical inventory, not Wasm capacity or backing-root union. Do not sum these ledgers.',
  cleanup_attempts: [], native_attempts: [], cases: [] };
const save = () => writeFile(path.join(output, 'generated.json'), JSON.stringify(report, null, 2) + '\n');
const helperCode = `import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('generated_cases209',sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if sys.argv[2]=='prepare':
 module.prepare(Path(sys.argv[3]),Path(sys.argv[4]),Path(sys.argv[5]))
else:
 module.native_case(Path(sys.argv[3]),Path(sys.argv[4]))
`;
async function python(mode, ...values) {
  await new Promise((resolve, reject) => {
    const child = spawn(nativePython, ['-I', '-c', helperCode, helper, mode, ...values],
      { cwd: output, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', OPENBLAS_NUM_THREADS: '1', TMPDIR: output }, stdio: ['ignore', 'pipe', 'pipe'] });
    let messages = '', failure, killTimer;
    const attempt = { mode, started_utc: new Date().toISOString(), pid: child.pid };
    report.native_attempts.push(attempt);
    const collect = value => { messages = (messages + value.toString()).slice(-12000); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    const timer = setTimeout(() => {
      failure ??= new Error(`Native ${mode} timed out after 180 seconds: ${messages}`);
      attempt.timed_out = true;
      attempt.sigterm_sent = child.kill('SIGTERM');
      killTimer = setTimeout(() => { attempt.sigkill_sent = child.kill('SIGKILL'); }, 2000);
    }, 180000);
    child.once('error', error => { failure ??= error; attempt.process_error = String(error); });
    // Settle only after close, including spawn errors and timeout escalation.
    child.once('close', (code, signal) => {
      clearTimeout(timer); clearTimeout(killTimer);
      Object.assign(attempt, { closed_utc: new Date().toISOString(), exit_code: code, signal });
      if (!failure && code !== 0) failure = new Error(`Native ${mode} failed (${code}): ${messages}`);
      failure ? reject(failure) : resolve();
    });
  });
}
async function cleanup(name, action) {
  const attempt = { name, started_utc: new Date().toISOString() };
  report.cleanup_attempts.push(attempt);
  let timer;
  try {
    await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${name} cleanup timed out after 10 seconds`)), 10000);
    })]);
    attempt.ok = true;
  } catch (error) {
    attempt.ok = false; attempt.error = String(error);
    return error;
  } finally { clearTimeout(timer); attempt.finished_utc = new Date().toISOString(); }
}
async function native(runtime, target) {
  await python('native', runtime, target);
  return JSON.parse(await readFile(target, 'utf8'));
}
function compare(actual, expected) {
  let numbers = 0, max = 0, parameter = 0, residual = 0, where = null;
  const visit = (a, e, location) => {
    if (typeof e === 'number') {
      assert.equal(typeof a, 'number', location);
      const d = Math.abs(a - e); assert.ok(Number.isFinite(a) && d <= 1e-10 + 1e-10 * Math.abs(e), `${location}: ${a} != ${e}`);
      numbers++; if (d > max) { max = d; where = location; }
      if (/parameters\[/.test(location)) parameter = Math.max(parameter, d);
      if (/residual/i.test(location)) residual = Math.max(residual, d);
    } else if (Array.isArray(e)) {
      assert.ok(Array.isArray(a)); assert.equal(a.length, e.length, location);
      e.forEach((v, i) => visit(a[i], v, `${location}[${i}]`));
    } else if (e !== null && typeof e === 'object') {
      assert.deepEqual(Object.keys(a).sort(), Object.keys(e).sort(), location);
      for (const [k, v] of Object.entries(e)) visit(a[k], v, `${location}.${k}`);
    } else assert.deepEqual(a, e, location);
  };
  for (const key of ['recipe', 'token', 'states', 'errors', 'diagnostics', 'memberships', 'results', 'result', 'derived', 'input_catalogue', 'input_requirements'])
    visit(actual[key], expected[key], key);
  return { numbers_compared: numbers, max_absolute_difference: max, max_parameter_difference: parameter,
    max_residual_difference: residual, max_difference_path: where, rtol: 1e-10, atol: 1e-10 };
}
let browser, failure;
const watchdog = setTimeout(() => {
  failure ??= new Error('Generated display validation exceeded its 12-minute watchdog.');
  report.failure = String(failure);
  void cleanup('watchdog-browser', () => browser?.close());
}, 720000);
try {
  await python('prepare', repository, sourceSite.root, output);
  const artifacts = JSON.parse(await readFile(path.join(output, 'generated-artifacts.json'), 'utf8'));
  browser = await playwright.chromium.launch({ headless: true });
  report.browser = browser.version();
  for (const artifact of artifacts) {
    const item = { scale: artifact.scale, source_archive_bytes: artifact.source_archive_bytes, errors: [], requests: [] };
    report.cases.push(item); await save();
    const site = await verifySite(artifact.site);
    item.site_manifest_sha256 = site.manifest_sha256;
    item.benchmark_provenance = site.manifest.benchmark;
    item.fixture = site.manifest.bounded_examples[0];
    const preview = await startPreview(site, { prefix: '/generated/test/' });
    let context, caseFailure;
    try {
      context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
      await context.route('**/*', async route => {
        const url = route.request().url();
        if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) { item.errors.push('external request: ' + url); return route.abort(); }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', e => item.errors.push(String(e)));
      await page.addInitScript(() => {
        window.generatedProbe = { phases: [], messages: [], operations: [], gpu_uploads: [], frames: 0, ticks: 0, max_tick_gap_ms: 0 };
        const p = window.generatedProbe, Original = Worker;
        let last = performance.now();
        setInterval(() => { const now = performance.now(); p.ticks++; p.max_tick_gap_ms = Math.max(p.max_tick_gap_ms, now-last); last=now; },16);
        const frame = () => { p.frames++; requestAnimationFrame(frame); }; requestAnimationFrame(frame);
        window.Worker = class extends Original {
          constructor(...args) {
            super(...args);
            this.addEventListener('message', ({ data }) => {
              const now = performance.now();
              if (data.phase) p.phases.push({ phase: data.phase, elapsed_ms: data.elapsed_ms, main_ms: now });
              else if (data.kind === 'transfer-confirmed') p.messages.push({ kind: data.kind, id: data.id, main_ms: now, ...data });
              else if (data.kind === 'lifecycle') p.messages.push({ kind: data.kind, id: data.id, operation: data.operation, state: data.state, interruptible: data.interruptible, main_ms: now });
              else if (data.ready) p.messages.push({ kind: 'ready', main_ms: now, versions: data.versions });
              else if (data.result?.positions instanceof ArrayBuffer) p.messages.push({ kind:'display',main_ms:now,id:data.id,positions_bytes:data.result.positions.byteLength,indices_bytes:data.result.indices.byteLength,metadata:data.result.metadata });
              else if (!data.kind) p.messages.push({ kind:'reply',main_ms:now,id:data.id,ok:data.ok,error:data.error,json_bytes:JSON.stringify(data).length });
            });
          }
          postMessage(message, ...args) { p.operations.push({ operation: message.operation, id: message.id, main_ms: performance.now(), json_bytes: JSON.stringify(message).length }); return super.postMessage(message,...args); }
        };
        for (const prototype of [WebGLRenderingContext.prototype, WebGL2RenderingContext.prototype]) {
          const original = prototype.bufferData;
          prototype.bufferData = function(target,data,...args) { p.gpu_uploads.push({main_ms:performance.now(),target,bytes:typeof data==='number'?data:data?.byteLength||0}); return original.call(this,target,data,...args); };
        }
      });
      const started = performance.now();
      await page.goto(preview.origin + '/generated/test/app/', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('#status')?.dataset.runtimeReady === 'true', null, { timeout: 180000 });
      item.ui_ready_ms = performance.now() - started;
      await page.locator('#viewport canvas').waitFor({ state:'visible' });
      item.ui_ready_main_ms = await page.evaluate(() => performance.now());
      const graph = await page.evaluate(async () => {
        const {getBackend}=await import(new URL('backend.js',document.baseURI)); const b=getBackend(); const before=await b.getGraph();
        const t=performance.now(); await b.evaluateGraph({token:before.token,all_actions:true});
        let state=await b.getGraph();
        while(state.evaluation_running) { await new Promise(r=>setTimeout(r,20)); state=await b.getGraph(); }
        return {state,evaluate_ms:performance.now()-t};
      });
      assert.equal(graph.state.recipe.nodes.length,7);
      assert.deepEqual(graph.state.errors,{});
      assert.ok(Object.values(graph.state.states).every(v=>v==='ready'));
      item.cached_graph_evaluation_observation_ms=graph.evaluate_ms;
      await writeFile(path.join(output, `browser-${artifact.scale}.json`),JSON.stringify(graph.state,null,2)+'\n');
      item.selection_counts=graph.state.recipe.nodes.filter(n=>n.operation==='selection').map(n=>({id:n.id,count:n.ids.length}));
      assert.deepEqual(item.selection_counts.map(x=>x.count),[128,32]);
      await page.locator('.action-select[data-action-id="side"]').dblclick();
      await page.locator('#metrics').waitFor({state:'visible'});
      item.metrics=await page.locator('#metrics').textContent();
      assert.match(item.metrics,/RMS/);
      await page.locator('#close-edit').click();
      await page.locator('#viewport .display-options > summary').click();
      await page.locator('#colors').selectOption('residual');
      await page.locator('#residual-scale').waitFor({state:'visible'});
      item.residual_display=await page.locator('#residual-scale').textContent();
      await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
      item.ui=await page.evaluate(()=>({status:document.querySelector('#status').textContent,canvas:{width:document.querySelector('#viewport canvas').width,height:document.querySelector('#viewport canvas').height},probe:window.generatedProbe}));
      await page.screenshot({path:path.join(output,`ui-${artifact.scale}.png`)});
      item.requests=preview.requests;
      assert.ok(item.requests.every(request => request.status === 200));
      const probe = item.ui.probe, display = probe.messages.find(message => message.kind === 'display'), transfer = probe.messages.find(message => message.kind === 'transfer-confirmed');
      assert.equal(display.metadata.vertices, item.fixture.vertices);
      assert.equal(display.metadata.triangles, item.fixture.triangles);
      assert.equal(display.positions_bytes, item.fixture.vertices * 12);
      assert.equal(display.indices_bytes, item.fixture.triangles * 12);
      assert.equal(transfer.positions_bytes_after, 0); assert.equal(transfer.indices_bytes_after, 0);
      const ensure = probe.operations.find(operation => operation.operation === 'ensureGraph');
      item.display = { positions_bytes: display.positions_bytes, indices_bytes: display.indices_bytes,
        known_wasm_to_owned_JS_copy_bytes: display.positions_bytes + display.indices_bytes,
        transfer_sender_detached: true, display_receipt_to_first_auto_ensureGraph_ms: ensure.main_ms - display.main_ms,
        display_receipt_to_ui_ready_observation_ms: item.ui_ready_main_ms - display.main_ms };
      item.gpu = { bufferData_calls: probe.gpu_uploads.length, cumulative_submitted_bytes: probe.gpu_uploads.reduce((total, entry) => total + entry.bytes, 0) };
      item.main_timer_max_gap_ms = probe.max_tick_gap_ms;
      item.fetched_bytes=preview.requests.filter(r=>r.status===200).reduce((sum,r)=>sum+(r.bytes||0),0);
      assert.deepEqual(item.errors,[]);
      const reference=await native(artifact.runtime,path.join(output,`native-${artifact.scale}.json`));
      item.native={versions:reference.versions,timings_seconds:reference.timings_seconds,arrays:reference.arrays};
      item.parity=compare(graph.state,reference.graph);
      item.checked_unchanged_after_run=(await verifySite(artifact.site)).manifest_sha256===site.manifest_sha256;
      assert.ok(item.checked_unchanged_after_run, 'Generated artifact changed during validation');
      item.input_artifact_unchanged=(await verifySite(sourceSite.root)).manifest_sha256===sourceSite.manifest_sha256;
      assert.ok(item.input_artifact_unchanged, 'Input artifact changed during validation');
      console.log(JSON.stringify({scale:item.scale,ready_ms:item.ui_ready_ms,parity:item.parity}));
      await save();
    } catch (error) { caseFailure = error; throw error; }
    finally {
      const contextError = await cleanup(`context-tier-${artifact.scale}`, () => context?.close());
      const previewError = await cleanup(`preview-tier-${artifact.scale}`, () => {
        preview.server.closeAllConnections();
        return preview.close();
      });
      if (contextError || previewError) {
        item.cleanup_failed = true;
        if (!caseFailure) throw contextError || previewError;
      }
    }
    item.complete=true;
    await save();
  }
  report.input_artifact_unchanged = (await verifySite(sourceSite.root)).manifest_sha256 === sourceSite.manifest_sha256;
  assert.ok(report.input_artifact_unchanged);
  report.finished_utc=new Date().toISOString();
} catch(error) { failure ??= error; report.failure=String(failure.stack||failure); }
finally {
  clearTimeout(watchdog);
  const browserError = await cleanup('browser', () => browser?.close());
  if (browserError && !failure) { failure = browserError; report.failure = String(browserError); }
  report.complete = !failure && report.input_artifact_unchanged && report.cases.length === 3 && report.cases.every(item => item.complete);
  try { await save(); }
  catch (error) {
    console.error(`Report save failed: ${error}`);
    failure ??= error;
  }
}
if (failure) throw failure;
