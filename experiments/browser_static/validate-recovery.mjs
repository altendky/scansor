import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { verifySite, startPreview, prepareReports, sha256 } from './server.mjs';
import { compareSemanticBatch } from './semantic-parity.mjs';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing ${name}`);
  return args[index + 1];
};
const directory = argument('--directory'), outputArgument = argument('--output');
if (!directory || !outputArgument)
  throw new Error('Usage: node validate-recovery.mjs --directory <site> --output <fresh external reports> [--browser chromium|firefox|webkit] [--isolation]');
const site = await verifySite(directory);
const playwrightDirectory = path.resolve(argument('--playwright-directory', 'experiments/browser_viewer'));
const repository = path.resolve(argument('--repository', path.join(playwrightDirectory, '../..')));
const output = await prepareReports(outputArgument, [site.root, repository]);
const nativePython = path.resolve(argument('--native-python', path.join(repository, '.venv/bin/python')));
const browserName = argument('--browser', 'chromium');
if (!['chromium', 'firefox', 'webkit'].includes(browserName)) throw new Error(`Unsupported browser ${browserName}`);
const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const report = {
  started_utc: new Date().toISOString(), manifest_sha256: site.manifest_sha256,
  source_revision: site.manifest.source_revision, runtime: site.manifest.runtime,
  capabilities: site.manifest.capabilities, isolation_requested: args.includes('--isolation'),
  requests: [], api_requests: [], external_requests: [], page_errors: [], checks: {},
  caveats: ['Harness timers bound observation and cleanup; they are not production automatic Force.',
    'Restart timing measures recovered-snapshot availability, not process termination or memory release.',
    'Injected message delays, old-worker messages, startup HTTP404 and uncaught worker JS errors are labelled faults.'],
};
const faultSuffix = `\n// Browser validation only: a requested uncaught worker-JS failure.\nself.addEventListener('message', event => { if (event.data?.operation === '__harness_crash') throw new Error('Harness injected worker JavaScript crash'); });\n`;
report.fault_instrumentation = { worker_script_suffix_sha256: sha256(Buffer.from(faultSuffix)),
  purpose: 'Only __harness_crash activates the appended listener. Production algorithms and runtime bytes on disk remain unchanged.' };

async function boundedEvaluate(page, operation, value, timeout = 240000) {
  let timer;
  try {
    return await Promise.race([page.evaluate(operation, value), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Recovery harness browser observation timed out')), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

let browser, preview, page;
const watchdog = setTimeout(() => { report.failure = 'Recovery harness exceeded 15 minutes'; void browser?.close(); }, 900000);
try {
  preview = await startPreview(site, { prefix: '/recovery/deep/', isolation: args.includes('--isolation') });
  browser = await playwright[browserName].launch({ headless: true });
  report.browser = { name: browserName, version: browser.version() };
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
  let missingStartup = false;
  await context.route('**/*', async route => {
    const url = route.request().url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) {
      report.external_requests.push(url); await route.abort(); return;
    }
    const pathname = new URL(url).pathname;
    if (/\/api(?:\/|$)/.test(pathname)) { report.api_requests.push(url); await route.abort(); return; }
    if (missingStartup && /\/runtime\/pyodide\.asm\.wasm$/.test(pathname)) {
      await route.fulfill({ status: 404, contentType: 'text/plain', body: 'Harness missing startup asset' }); return;
    }
    if (/\/worker\.mjs$/.test(pathname)) {
      const relative = pathname.slice('/recovery/deep/'.length);
      assert.ok(site.records.has(relative));
      const bytes = await readFile(path.join(site.root, relative));
      await route.fulfill({ status: 200, contentType: 'text/javascript',
        headers: args.includes('--isolation') ? { 'Cross-Origin-Opener-Policy': 'same-origin',
          'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin' } : {},
        body: Buffer.concat([bytes, Buffer.from(faultSuffix)]) });
    } else await route.continue();
  });
  page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror', error => report.page_errors.push(String(error)));
  await page.addInitScript(() => {
    const NativeWorker = window.Worker;
    window.recoveryHarness = { workers: [], messages: [], held: [], hold: false,
      lifecycles: [], phases: [], outcomes: [], results: {}, cancelRequested: false };
    const harness = window.recoveryHarness;
    harness.canvasListeners = {};
    const addListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, ...values) {
      if (this instanceof HTMLCanvasElement)
        harness.canvasListeners[type] = (harness.canvasListeners[type] || 0) + 1;
      return addListener.call(this, type, ...values);
    };
    window.Worker = class extends NativeWorker {
      constructor(...options) {
        super(...options);
        this.harnessIndex = harness.workers.length;
        this.harnessOperations = new Map();
        harness.workers.push(this);
      }
      postMessage(value, ...options) {
        if (value?.id !== undefined) this.harnessOperations.set(value.id, value.operation);
        harness.messages.push({ direction: 'send', worker: this.harnessIndex,
          id: value?.id, operation: value?.operation, generation: value?.generation, time: performance.now() });
        return super.postMessage(value, ...options);
      }
      set onmessage(callback) {
        this.harnessHandler = callback;
        super.onmessage = event => {
          const data = event.data;
          if (data?.kind === 'lifecycle' && data.state === 'running' && data.interruptible)
            harness.lastNumericLifecycle = structuredClone(data);
          harness.messages.push({ direction: 'receive', worker: this.harnessIndex,
            id: data?.id, kind: data?.kind, operation: this.harnessOperations.get(data?.id),
            phase: data?.phase, generation: data?.generation, lifetime: data?.lifetime,
            token: data?.state?.token || data?.result?.token,
            running: data?.state?.evaluation_running, time: performance.now() });
          if (harness.hold && this === harness.testWorker &&
            !data?.phase && data?.id && this.harnessOperations.get(data.id) === 'getGraph') {
            harness.held.push(() => callback?.(event));
          } else callback?.(event);
          if (harness.uiCancelArmed && this === harness.workers[0] &&
            data?.kind === 'lifecycle' && data.state === 'running' && data.operation === 'evaluateGraph') {
            const button = document.querySelector('#cancel-computation');
            if (button && !button.disabled) {
              harness.uiCancelArmed = false;
              harness.uiCancelClicked = true;
              button.click();
            }
          }
        };
      }
      get onmessage() { return this.harnessHandler; }
    };
  });
  await page.goto(preview.origin + '/recovery/deep/app/', { waitUntil: 'domcontentloaded' });
  await page.locator('#viewport canvas').waitFor({ state: 'visible', timeout: 180000 });
  report.ui_baseline = await page.evaluate(() => ({ canvases: document.querySelectorAll('#viewport canvas').length,
    canvas_listener_registrations: window.recoveryHarness.canvasListeners }));
  report.environment = await page.evaluate(() => ({ isolated: crossOriginIsolated,
    shared_array_buffer: typeof SharedArrayBuffer === 'function' }));
  assert.equal(report.environment.isolated, args.includes('--isolation'));
  const initial = await boundedEvaluate(page, async () => {
    const { createWorkerBackend } = await import(new URL('worker-backend.js', document.baseURI).href);
    const configuration = (await import(new URL('backend-config.js', document.baseURI).href)).default;
    const h = window.recoveryHarness;
    const backend = createWorkerBackend({ runtimeURL: new URL(configuration.runtimeURL, document.baseURI).href,
      cad: configuration.cad, onPhase: phase => h.phases.push({ phase, time: performance.now() }),
      onLifecycle: state => h.lifecycles.push({ ...state, time: performance.now() }),
      workerFactory: (url, options) => { const worker = new Worker(url, options); h.testWorker = worker; return worker; } });
    h.backend = backend;
    h.settle = promise => promise.then(value => ({ ok: true, value }), error =>
      ({ ok: false, error: { code: error.code, status: error.status, message: error.message } }));
    h.terminal = async () => {
      let state;
      for await (const update of backend.graphUpdates()) state = update;
      if (!state || state.evaluation_running) throw new Error('No terminal graph snapshot');
      return state;
    };
    h.evaluate = async () => {
      const graph = await backend.getGraph();
      await backend.evaluateGraph({ token: graph.token, all_actions: true });
      return h.terminal();
    };
    const ready = await backend.initialize(), graph = await backend.getGraph();
    h.source = graph.recipe.nodes.find(node => node.operation === 'source');
    h.ready = ready;
    return { ready, graph, lifecycle: backend.getLifecycle() };
  });
  report.initial = { ready: initial.ready, lifecycle: initial.lifecycle };
  assert.equal(initial.graph.recipe.nodes.length, 30);
  const edited = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, current = await h.backend.getGraph();
    const recipe = structuredClone(current.recipe);
    const selection = recipe.nodes.filter(node => node.operation === 'selection')
      .sort((a, b) => b.ids.length - a.ids.length)[0];
    selection.ids.pop(); selection.label += ' recovery edit';
    const saved = await h.backend.replaceRecipe({ token: current.token, recipe });
    h.recipe = saved.recipe; h.token = saved.token;
    const complete = await h.evaluate(); h.baseline = complete;
    return { recipe: saved.recipe, token: saved.token, source: h.source,
      states: complete.states, errors: complete.errors };
  });
  report.edited = edited;
  assert.equal(Object.keys(edited.errors).length, 0);
  report.checks.actual_retained_recipe_edit_and_fit = true;
  const idle = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, before = h.baseline;
    const started = performance.now();
    while (h.backend.getLifecycle().state !== 'complete') {
      if (performance.now() - started > 30000) throw new Error('No idle lifecycle completion');
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    const after = await h.backend.cancelComputation();
    return { unchanged: JSON.stringify(before) === JSON.stringify(after), lifecycle: h.backend.getLifecycle() };
  });
  assert.ok(idle.unchanged); report.idle_cancel = idle; report.checks.idle_cancel_preserves_graph = true;

  // Real numeric work is invalidated by an actual selection edit. Lifecycle
  // notification is the available checkpoint; no artificial solver delay runs.
  const numeric = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, before = await h.backend.getGraph();
    const recipe = structuredClone(before.recipe);
    const selection = recipe.nodes.filter(node => node.operation === 'selection')
      .sort((a, b) => b.ids.length - a.ids.length)[0];
    selection.ids.pop(); selection.label += ' cancelled run';
    const saved = await h.backend.replaceRecipe({ token: before.token, recipe });
    h.recipe = saved.recipe; h.token = saved.token;
    const events = [], observed = h.settle((async () => {
      for await (const state of h.backend.graphUpdates()) events.push({ token: state.token,
        running: state.evaluation_running, after_cancel: h.cancelRequested });
    })());
    const running = h.settle(h.backend.evaluateGraph({ token: saved.token, all_actions: true }));
    const started = performance.now();
    while (h.backend.getLifecycle().state !== 'running' || h.backend.getLifecycle().operation !== 'evaluateGraph') {
      if (performance.now() - started > 30000) throw new Error('No numeric running lifecycle');
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    const atCancel = h.backend.getLifecycle(), queued = h.settle(h.backend.getGraph());
    h.cancelRequested = true;
    const cancelAt = performance.now(), barrier = h.backend.cancelComputation();
    const settled = await Promise.all([running, queued, observed]);
    const settlement_ms = performance.now() - cancelAt;
    const recovered = await barrier;
    h.cancelRequested = false;
    return { at_cancel: atCancel, settled, events, settlement_ms,
      barrier_ms: performance.now() - cancelAt, recovered,
      subsequent: await h.evaluate() };
  });
  report.numeric_cancel = numeric;
  assert.ok(numeric.settled.some(item => item.error?.code === 'computation_cancelled'));
  assert.ok(numeric.events.every(event => !event.after_cancel || event.running));
  assert.deepEqual(numeric.recovered.recipe, numeric.subsequent.recipe);
  assert.equal(numeric.recovered.token, numeric.subsequent.token);
  assert.deepEqual(numeric.recovered.recipe.nodes.find(node => node.operation === 'source'), edited.source);
  assert.equal(Object.keys(numeric.subsequent.errors).length, 0);
  report.checks.actual_numeric_cancel_fence_restore_then_fit = true;

  const delayed = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness;
    h.hold = true;
    const pending = h.settle(h.backend.getGraph());
    const started = performance.now();
    while (!h.held.length) {
      if (performance.now() - started > 30000) throw new Error('No held graph reply');
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    const cancellation = h.backend.cancelComputation(), outcome = await pending;
    h.hold = false;
    for (const release of h.held.splice(0)) release();
    const recovered = await cancellation;
    return { fault: 'Held completed getGraph response before client publication', outcome,
      token: recovered.token, lifecycle: h.backend.getLifecycle() };
  });
  report.delayed_response = delayed;
  assert.equal(delayed.outcome.error?.code, 'computation_cancelled');
  report.checks.delayed_completed_reply_cannot_publish = true;

  const force = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, sameBackend = h.backend;
    const old = h.testWorker, previous = await h.backend.getGraph();
    const recipe = structuredClone(previous.recipe);
    recipe.nodes.filter(node => node.operation === 'selection')
      .sort((a, b) => b.ids.length - a.ids.length)[0].ids.pop();
    const before = await h.backend.replaceRecipe({ token: previous.token, recipe });
    const compute = h.settle(h.backend.evaluateGraph({ token: before.token, all_actions: true }));
    const waiting = performance.now();
    while (h.backend.getLifecycle().state !== 'running' || h.backend.getLifecycle().operation !== 'evaluateGraph') {
      if (performance.now() - waiting > 30000) throw new Error('No running numeric operation before Force');
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    const atForce = h.backend.getLifecycle(), pending = h.settle(h.backend.getGraph());
    const started = performance.now();
    const first = h.settle(h.backend.forceRestart()), second = h.backend.forceRestart();
    const outcomes = await Promise.all([compute, pending, first]);
    const recovered = await second;
    const lifecycle = h.backend.getLifecycle();
    const fake = structuredClone(recovered); fake.token = 'old-worker-injected-token';
    old.dispatchEvent(new MessageEvent('message', { data: { kind: 'graph',
      generation: lifecycle.generation, lifetime: lifecycle.lifetime, state: fake } }));
    const actual = await h.backend.getGraph();
    return { same_backend: sameBackend === h.backend, before_recipe: before.recipe, recovered,
      outcomes, at_force: atForce, restart_ms: performance.now() - started, lifecycle,
      old_message_fault: 'Synthetic MessageEvent from replaced Worker with current lifecycle identities',
      old_message_ignored: actual.token === recovered.token, subsequent: await h.evaluate() };
  });
  report.force = force;
  assert.ok(force.same_backend && force.old_message_ignored);
  assert.deepEqual(force.recovered.recipe, force.before_recipe);
  assert.deepEqual(force.recovered.recipe.nodes.find(node => node.operation === 'source'), edited.source);
  assert.equal(Object.keys(force.subsequent.errors).length, 0);
  assert.ok(force.outcomes.some(item => item.error?.code === 'computation_cancelled'));
  report.checks.explicit_repeated_force_same_backend_recipe_source_old_messages = true;

  const crash = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, before = await h.backend.getGraph();
    h.hold = true;
    const pending = h.settle(h.backend.getGraph());
    h.testWorker.postMessage({ id: 0, operation: '__harness_crash' });
    const outcome = await pending;
    h.hold = false; h.held.splice(0);
    const failed = h.backend.getLifecycle(), started = performance.now();
    const recovered = await h.backend.forceRestart();
    return { fault: 'Actual uncaught worker JavaScript exception activated by __harness_crash',
      outcome, failed, recovered, before_recipe: before.recipe,
      restart_ms: performance.now() - started, subsequent: await h.evaluate() };
  });
  report.crash = crash;
  assert.equal(crash.failed.state, 'failed'); assert.equal(crash.outcome.ok, false);
  assert.deepEqual(crash.before_recipe, crash.recovered.recipe);
  assert.equal(Object.keys(crash.subsequent.errors).length, 0);
  report.same_recipe_recovery_result_parity = await compareSemanticBatch({ nativePython, site, output, report,
    cases: [{ id: 'crash-force', profile: 'result-map', actual: crash.subsequent.results, expected: force.subsequent.results }] });
  report.checks.worker_js_crash_settlement_explicit_recovery = true;

  if (site.manifest.capabilities.cad) {
    report.stale_numeric_lifecycle_cad = await boundedEvaluate(page, async () => {
      const h = window.recoveryHarness;
      await h.evaluate();
      const current = await h.backend.getGraph(), fit = current.recipe.nodes.find(node => node.label === 'outer fit');
      const payload = { token: current.token, scope: 'target', target: fit.id, units: 'Millimeters',
        axis_up: false, include_mesh: true };
      // First import OCP without changing the numerical recipe/generation.
      await h.backend.exportCad(payload);
      await h.evaluate();
      const stale = structuredClone(h.lastNumericLifecycle);
      const pending = h.settle(h.backend.exportCad(payload)), started = performance.now();
      while (h.backend.getLifecycle().state !== 'running' || h.backend.getLifecycle().operation !== 'exportCad') {
        if (performance.now() - started > 30000) throw new Error('No CAD lifecycle for stale numeric-signal probe');
        await new Promise(resolve => setTimeout(resolve, 1));
      }
      const realCad = h.backend.getLifecycle();
      h.testWorker.harnessHandler({ data: stale });
      const misleading = h.backend.getLifecycle(), recovered = await h.backend.cancelComputation();
      const outcome = await pending;
      await h.evaluate();
      const bytes = await h.backend.exportCad({ ...payload, token: recovered.token });
      return { fault: 'Previously observed numerical running lifecycle delivered late during real CAD export',
        real_cad: realCad, stale_numeric: misleading, outcome, recovered_token: recovered.token,
        subsequent_zip_bytes: bytes.byteLength };
    });
    assert.equal(report.stale_numeric_lifecycle_cad.real_cad.interruptible, false);
    assert.equal(report.stale_numeric_lifecycle_cad.outcome.error?.code, 'computation_cancelled');
    assert.ok(report.stale_numeric_lifecycle_cad.subsequent_zip_bytes > 0);
    report.checks.stale_numeric_lifecycle_cannot_interrupt_cad_runtime = true;
    const cad = await boundedEvaluate(page, async () => {
      const h = window.recoveryHarness, current = await h.backend.getGraph();
      const recipe = structuredClone(current.recipe), cone = recipe.nodes.find(node => node.label === 'outer fit'),
        plane = recipe.nodes.find(node => node.label === 'top fit');
      recipe.nodes.push({ id: 'recovery_boundary', label: 'Recovery boundary', operation: 'surface_intersection',
        first: { feature: cone.id }, second: { feature: plane.id } },
      { id: 'recovery_face', label: 'Recovery face Ø12', operation: 'trimmed_face', surface: { feature: plane.id },
        boundaries: [{ intersection: 'recovery_boundary', keep: 'inside' }] });
      const saved = await h.backend.replaceRecipe({ token: current.token, recipe });
      await h.evaluate();
      const payload = { token: saved.token, scope: 'all_faces', units: 'Centimeters', axis_up: false, include_mesh: true };
      const operation = h.settle(h.backend.exportCad(payload));
      const started = performance.now();
      while (h.backend.getLifecycle().state !== 'running' || h.backend.getLifecycle().operation !== 'exportCad') {
        if (performance.now() - started > 30000) throw new Error('No CAD running lifecycle');
        await new Promise(resolve => setTimeout(resolve, 1));
      }
      const atCancel = h.backend.getLifecycle(), queued = h.settle(h.backend.getGraph());
      const cancelAt = performance.now(), barrier = h.backend.cancelComputation();
      const settled = await Promise.all([operation, queued]), recovered = await barrier;
      const barrier_ms = performance.now() - cancelAt;
      await h.evaluate();
      const bytes = await h.backend.exportCad({ ...payload, token: recovered.token });
      const forceOperation = h.settle(h.backend.exportCad({ ...payload, token: recovered.token }));
      const waiting = performance.now();
      while (h.backend.getLifecycle().state !== 'running' || h.backend.getLifecycle().operation !== 'exportCad') {
        if (performance.now() - waiting > 30000) throw new Error('No running CAD operation before Force');
        await new Promise(resolve => setTimeout(resolve, 1));
      }
      const forceStarted = performance.now();
      const forced = await h.backend.forceRestart(), forcedOutcome = await forceOperation;
      const restart_ms = performance.now() - forceStarted;
      await h.evaluate();
      const afterForce = await h.backend.exportCad({ ...payload, token: forced.token });
      return { at_cancel: atCancel, settled, barrier_ms,
        recovered, subsequent_zip_bytes: bytes.byteLength, forced, forced_outcome: forcedOutcome,
        restart_ms, post_force_zip_bytes: afterForce.byteLength };
    });
    report.cad = cad;
    assert.equal(cad.at_cancel.interruptible, false);
    assert.ok(cad.settled.some(item => item.error?.code === 'computation_cancelled'));
    assert.deepEqual(cad.recovered.recipe, cad.forced.recipe);
    assert.ok(cad.subsequent_zip_bytes > 0 && cad.post_force_zip_bytes > 0);
    assert.equal(cad.forced_outcome.error?.code, 'computation_cancelled');
    report.checks.actual_cad_cancel_and_force_then_export = true;
  }

  await boundedEvaluate(page, () => window.recoveryHarness.backend.close());
  missingStartup = true;
  const startup = await boundedEvaluate(page, async () => {
    const { createWorkerBackend } = await import(new URL('worker-backend.js', document.baseURI).href);
    const config = (await import(new URL('backend-config.js', document.baseURI).href)).default;
    const h = window.recoveryHarness;
    h.retryBackend = createWorkerBackend({ runtimeURL: new URL(config.runtimeURL, document.baseURI).href,
      cad: config.cad, startupTimeout: 30000 });
    const outcome = await h.settle(h.retryBackend.initialize());
    return { outcome, lifecycle: h.retryBackend.getLifecycle() };
  });
  missingStartup = false;
  assert.equal(startup.outcome.ok, false); assert.equal(startup.lifecycle.state, 'failed');
  const retry = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness, state = await h.retryBackend.forceRestart();
    const recipe = await h.retryBackend.exportRecipe();
    h.retryBackend.close();
    return { state, recipe };
  });
  report.startup_retry = { fault: 'Runtime Wasm response fulfilled HTTP404 until explicit Force', startup, retry };
  assert.equal(retry.recipe.nodes.length, 30);
  report.checks.startup_failure_explicit_retry = true;

  const uiFit = await boundedEvaluate(page, async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const state = await getBackend().getGraph();
    return state.recipe.nodes.find(node => node.operation === 'fit' && node.label === 'outer fit').id;
  });
  await page.locator(`.action-select[data-action-id="${uiFit}"]`).dblclick();
  await page.waitForFunction(() => document.querySelector('#metrics').children.length > 0);
  await boundedEvaluate(page, async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const h = window.recoveryHarness, backend = getBackend(), before = await backend.getGraph();
    const recipe = structuredClone(before.recipe);
    recipe.nodes.filter(node => node.operation === 'selection').sort((a, b) => b.ids.length - a.ids.length)[0].ids.pop();
    h.uiSaved = await backend.replaceRecipe({ token: before.token, recipe });
    h.uiCancelArmed = true;
    h.uiEvaluation = h.settle(backend.evaluateGraph({ token: h.uiSaved.token, all_actions: true }));
  });
  await page.waitForFunction(() => window.recoveryHarness.uiCancelClicked === true, null, { timeout: 30000 });
  await page.waitForFunction(() => /cancelled|safe boundary/i.test(document.querySelector('#runtime-detail').textContent),
    null, { timeout: 180000 });
  report.ui_cancel = await boundedEvaluate(page, async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const h = window.recoveryHarness, backend = getBackend(), state = await backend.getGraph();
    h.uiBarrierAt = performance.now();
    return { mechanism: 'Actual Cancel button click handler invoked at the real numerical running lifecycle checkpoint; no solver or completion delay',
      evaluation: await h.uiEvaluation, state, saved_recipe: h.uiSaved.recipe,
      auto: document.querySelector('#auto-evaluate').checked,
      metric_children: document.querySelector('#metrics').children.length,
      force_visible: !document.querySelector('#force-restart').hidden };
  });
  assert.equal(report.ui_cancel.auto, false);
  assert.deepEqual(report.ui_cancel.state.recipe, report.ui_cancel.saved_recipe);
  assert.equal(report.ui_cancel.state.evaluation_running, false);
  assert.equal(report.ui_cancel.metric_children, 0);
  assert.ok(report.ui_cancel.force_visible);
  report.ui_cancel.quiet_after_barrier = await boundedEvaluate(page, async () => {
    const h = window.recoveryHarness;
    await new Promise(resolve => setTimeout(resolve, 100));
    return { observation_ms: 100, automatic_requests: h.messages.filter(message =>
      message.direction === 'send' && message.worker === 0 && message.time > h.uiBarrierAt &&
      ['ensureGraph', 'evaluateGraph'].includes(message.operation)),
      metric_children: document.querySelector('#metrics').children.length };
  });
  assert.equal(report.ui_cancel.quiet_after_barrier.automatic_requests.length, 0);
  assert.equal(report.ui_cancel.quiet_after_barrier.metric_children, 0);
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  await page.waitForFunction(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const graph = await getBackend().getGraph();
    return !graph.evaluation_running && Object.values(graph.states).every(state => state === 'ready');
  }, null, { timeout: 120000 });
  report.checks.actual_ui_cancel_auto_off_clear_metrics_then_evaluate = true;

  const startupPage = await context.newPage();
  startupPage.on('pageerror', error => report.page_errors.push(String(error)));
  await startupPage.addInitScript(() => {
    const NativeWorker = window.Worker, addListener = EventTarget.prototype.addEventListener;
    window.startupForceHarness = { held: [], workers: 0, firstWorker: null, canvasListeners: {} };
    const h = window.startupForceHarness;
    EventTarget.prototype.addEventListener = function(type, ...values) {
      if (this instanceof HTMLCanvasElement) h.canvasListeners[type] = (h.canvasListeners[type] || 0) + 1;
      return addListener.call(this, type, ...values);
    };
    window.Worker = class extends NativeWorker {
      constructor(...options) {
        super(...options); this.operations = new Map(); h.workers++;
        h.firstWorker ??= this;
      }
      postMessage(value, ...options) {
        this.operations.set(value.id, value.operation);
        return super.postMessage(value, ...options);
      }
      set onmessage(callback) {
        super.onmessage = event => {
          if (this === h.firstWorker && this.operations.get(event.data?.id) === 'loadWorkspace')
            h.held.push(() => callback?.(event));
          else callback?.(event);
        };
      }
    };
  });
  await startupPage.goto(preview.origin + '/recovery/deep/app/', { waitUntil: 'domcontentloaded' });
  await startupPage.waitForFunction(() => window.startupForceHarness.held.length > 0, null, { timeout: 180000 });
  await startupPage.locator('#force-restart').click();
  await startupPage.waitForFunction(() => document.querySelector('#viewport canvas') &&
    document.querySelector('#status')?.dataset.runtimeReady === 'true', null, { timeout: 180000 });
  report.ui_startup_force = await startupPage.evaluate(() => {
    const h = window.startupForceHarness;
    for (const release of h.held.splice(0)) release();
    return { fault: 'First real loadWorkspace replies held until explicit UI Force; retired replies then released',
      canvases: document.querySelectorAll('#viewport canvas').length, workers: h.workers,
      canvas_listener_registrations: h.canvasListeners,
      status: document.querySelector('#runtime-detail').textContent };
  });
  assert.equal(report.ui_startup_force.canvases, 1);
  assert.ok(report.ui_startup_force.workers >= 2);
  assert.deepEqual(report.ui_startup_force.canvas_listener_registrations, report.ui_baseline.canvas_listener_registrations);
  report.checks.ui_startup_force_single_renderer_and_input_setup = true;
  await startupPage.close();

  report.traces = await page.evaluate(() => {
    const h = window.recoveryHarness;
    return { lifecycles: h.lifecycles, phases: h.phases, messages: h.messages,
      workers_created: h.workers.length };
  });
  assert.equal(report.api_requests.length, 0); assert.equal(report.external_requests.length, 0);
  assert.equal(report.page_errors.length, 0);
  report.checks.no_external_or_computation_http = true;
  report.finished_utc = new Date().toISOString();
  await context.close();
} catch (error) {
  report.failure = String(error);
  if (page && !page.isClosed()) {
    try { report.failure_trace = await page.evaluate(() => ({ lifecycle: window.recoveryHarness?.backend?.getLifecycle(),
      startup_detail: document.querySelector('#startup-detail')?.textContent,
      runtime_detail: document.querySelector('#runtime-detail')?.textContent,
      messages: window.recoveryHarness?.messages, phases: window.recoveryHarness?.phases })); }
    catch (traceError) { report.failure_trace_error = String(traceError); }
  }
  throw error;
} finally {
  clearTimeout(watchdog);
  if (browser) await browser.close();
  if (preview) { report.static_requests = preview.requests; await preview.close(); }
  try { report.artifact_unchanged = (await verifySite(directory)).manifest_sha256 === site.manifest_sha256; }
  catch (error) { report.artifact_unchanged = false; report.artifact_error = String(error); }
  // Keep full graph evidence once in a separate file. The principal report
  // contains identities, states and hashes instead of repeated large arrays.
  const graphs = {};
  const compact = (value, location) => {
    if (value && typeof value === 'object' && value.recipe && value.token && value.states && value.results) {
      graphs[location] = value;
      return { graph_evidence: location, token: value.token, revision: value.revision,
        states: value.states, errors: value.errors, evaluation_running: value.evaluation_running,
        recipe_sha256: sha256(Buffer.from(JSON.stringify(value.recipe))),
        results_sha256: sha256(Buffer.from(JSON.stringify(value.results))) };
    }
    if (Array.isArray(value)) return value.map((item, index) => compact(item, `${location}[${index}]`));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
      .map(([key, item]) => [key, compact(item, `${location}.${key}`)]));
    return value;
  };
  const compactReport = compact(report, 'report');
  await writeFile(path.join(output, 'graphs.json'), JSON.stringify(graphs) + '\n');
  await writeFile(path.join(output, 'recovery.json'), JSON.stringify(compactReport, null, 2) + '\n');
  if (!report.artifact_unchanged) throw new Error('Artifact changed during recovery validation');
}
console.log(JSON.stringify({ browser: report.browser, checks: report.checks,
  report: path.join(output, 'recovery.json') }, null, 2));
