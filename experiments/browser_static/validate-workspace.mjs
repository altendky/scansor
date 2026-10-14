import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
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
  throw new Error('Usage: node validate-workspace.mjs --directory <site> --output <fresh external reports> [--playwright-directory experiments/browser_viewer] [--native-python <existing interpreter>] [--browser chromium|firefox|webkit] [--isolation]');
const site = await verifySite(directory);
const playwrightDirectory = path.resolve(argument('--playwright-directory', 'experiments/browser_viewer'));
const repository = path.resolve(argument('--repository', path.join(playwrightDirectory, '../..')));
const output = await prepareReports(outputArgument, [site.root, repository]);
const nativePython = path.resolve(argument('--native-python', path.join(repository, '.venv/bin/python')));
const browserName = argument('--browser', 'chromium');
if (!['chromium', 'firefox', 'webkit'].includes(browserName)) throw new Error(`Unsupported browser ${browserName}`);
const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const prefix = '/workspace/nested/';
const report = {
  started_utc: new Date().toISOString(), manifest_sha256: site.manifest_sha256,
  source_revision: site.manifest.source_revision, source_worktree_dirty: site.manifest.source_worktree_dirty,
  runtime: site.manifest.runtime, capabilities: site.manifest.capabilities,
  requests: [], unexpected_requests: [], api_requests: [], page_errors: [], checks: {},
  artifact_unchanged: null,
};
const saveReport = () => writeFile(path.join(output, 'workspace.json'), JSON.stringify(report, null, 2) + '\n');

// Run native Python against the exact packaged source, rather than the checkout.
// This is part of the project harness, not a dependency-installing standalone script.
const nativeCode = `import json, sys, tempfile, zipfile, platform
from pathlib import Path
import numpy as np
import pydantic
runtime, output = map(Path, sys.argv[1:3])
recipes = json.loads((output / 'recipes.json').read_text())
def unpack(value):
    result = json.loads(value)
    if not result['ok']:
        raise RuntimeError(result['error'])
    return result['result']
with tempfile.TemporaryDirectory(prefix='native-workspace-', dir=output) as temporary:
    root = Path(temporary)
    with zipfile.ZipFile(runtime / 'scansor.zip') as archive:
        for name in archive.namelist():
            if not (root / name).resolve().is_relative_to(root.resolve()):
                raise ValueError('source archive escapes native probe directory')
        archive.extractall(root)
    sys.path.insert(0, str(root))
    from experiments.browser_runtime.workspace import BrowserWorkspace
    snapshots = {}
    for name, recipe in recipes.items():
        workspace = BrowserWorkspace(root / 'examples/nozzle-bayonette-simplified')
        current = unpack(workspace.call_json('getGraph'))
        current = unpack(workspace.call_json('replaceRecipe', json.dumps({'token': current['token'], 'recipe': recipe})))
        prepared = unpack(workspace.prepare_evaluation_json('evaluateGraph', json.dumps({'token': current['token'], 'all_actions': True})))
        snapshots[name] = unpack(workspace.finish_evaluation_json()) if prepared['run'] else prepared['state']
        assert not snapshots[name]['evaluation_running']
        assert workspace.workspace.local.dtype == np.dtype('float64')
        assert not workspace.workspace.local.flags.writeable
        assert workspace.display_positions.dtype == np.dtype('<f4')
        assert workspace.display_indices.dtype == np.dtype('<u4')
    assert not any(name in sys.modules for name in ('OCP', 'duckdb', 'pyarrow', 'psutil'))
    result = {'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pydantic': pydantic.__version__}, 'snapshots': snapshots}
    (output / 'native-workspace.json').write_text(json.dumps(result, allow_nan=False))
`;

async function nativeReference(runtime) {
  await new Promise((resolve, reject) => {
    const child = spawn(nativePython, ['-I', '-c', nativeCode, runtime, output], {
      cwd: output, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', OPENBLAS_NUM_THREADS: '1', TMPDIR: output },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let messages = '', timedOut = false;
    const collect = data => { messages = (messages + data.toString()).slice(-12000); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, 180000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => {
      clearTimeout(timer);
      if (code !== 0 || timedOut) reject(new Error(`Native workspace reference failed (${code}, timeout=${timedOut}): ${messages}`));
      else resolve();
    });
  });
  return JSON.parse(await readFile(path.join(output, 'native-workspace.json'), 'utf8'));
}

function compareSnapshot(actual, expected) {
  for (const key of ['recipe', 'token', 'states', 'errors', 'memberships'])
    assert.deepEqual(actual[key], expected[key], `${key} must preserve exact identity and control semantics`);
  let numbers = 0, maxAbsoluteDifference = 0, maxDifferencePath = null,
    maxParameterDifference = 0, maxResidualDifference = 0;
  const compare = (value, reference, location) => {
    if (typeof reference === 'number') {
      assert.equal(typeof value, 'number', location);
      assert.ok(Number.isFinite(value) && Number.isFinite(reference), location);
      const difference = Math.abs(value - reference);
      assert.ok(difference <= 1e-10 + 1e-10 * Math.abs(reference), `${location}: ${value} != ${reference}`);
      numbers++;
      if (difference > maxAbsoluteDifference) {
        maxAbsoluteDifference = difference;
        maxDifferencePath = location;
      }
      if (/parameters\[/.test(location)) maxParameterDifference = Math.max(maxParameterDifference, difference);
      if (/residual/i.test(location)) maxResidualDifference = Math.max(maxResidualDifference, difference);
    } else if (Array.isArray(reference)) {
      assert.ok(Array.isArray(value), location);
      assert.equal(value.length, reference.length, location);
      reference.forEach((item, index) => compare(value[index], item, `${location}[${index}]`));
    } else if (reference !== null && typeof reference === 'object') {
      assert.deepEqual(Object.keys(value).sort(), Object.keys(reference).sort(), location);
      for (const [key, item] of Object.entries(reference)) compare(value[key], item, `${location}.${key}`);
    } else assert.deepEqual(value, reference, location);
  };
  // Publication revisions count scheduling, not numerical results. All identity,
  // recipe, state, diagnostics, membership and result fields remain compared.
  for (const key of ['recipe', 'token', 'states', 'errors', 'diagnostics', 'memberships',
    'result', 'results', 'derived', 'input_catalogue', 'input_requirements'])
    compare(actual[key], expected[key], key);
  return { numbers_compared: numbers, max_absolute_difference: maxAbsoluteDifference,
    max_difference_path: maxDifferencePath, max_parameter_difference: maxParameterDifference,
    max_residual_difference: maxResidualDifference, rtol: 1e-10, atol: 1e-10 };
}

async function boundedEvaluate(page, operation, value, timeout = 30000) {
  let timer;
  try {
    return await Promise.race([page.evaluate(operation, value), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Browser observation exceeded its harness timeout')), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

const readGraph = page => boundedEvaluate(page, async () => {
  const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
  return getBackend().getGraph();
});
async function idleGraph(page) {
  await page.waitForFunction(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const state = await getBackend().getGraph();
    return state.recipe && !state.evaluation_running &&
      Object.values(state.states).every(value => ['ready', 'failed', 'blocked'].includes(value));
  }, null, { timeout: 120000, polling: 100 });
  return readGraph(page);
}

let browser, preview;
const watchdog = setTimeout(() => {
  report.failure = 'Workspace validation exceeded its 12-minute harness watchdog.';
  void browser?.close();
}, 720000);
try {
  preview = await startPreview(site, { prefix, isolation: args.includes('--isolation') });
  browser = await playwright[browserName].launch({ headless: true });
  report.browser = { name: browserName, version: browser.version() };
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', async route => {
    const url = route.request().url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) {
      report.unexpected_requests.push(url); await route.abort(); return;
    }
    if (url.startsWith(preview.origin + '/') && /\/api(?:\/|$)/.test(new URL(url).pathname)) {
      report.api_requests.push(url); await route.abort(); return;
    }
    await route.continue();
  });
  const makePage = async () => {
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => report.page_errors.push(String(error)));
    await page.addInitScript(() => {
      window.workspaceWorkerReadiness = [];
      const OriginalWorker = window.Worker;
      window.Worker = class extends OriginalWorker {
        constructor(...options) {
          super(...options);
          this.addEventListener('message', ({ data }) => {
            if (data?.ready) window.workspaceWorkerReadiness.push({
              ready: data.ready, versions: data.versions, source_revision: data.source_revision,
              source_worktree_dirty: data.source_worktree_dirty,
            });
          });
        }
      };
    });
    await page.goto(preview.origin + prefix + 'app/', { waitUntil: 'domcontentloaded' });
    await page.locator('#viewport canvas').waitFor({ state: 'visible', timeout: 180000 });
    await page.waitForFunction(() => !document.querySelector('#evaluate-all').disabled,
      null, { timeout: 120000 });
    return page;
  };
  const page = await makePage();
  const initial = await idleGraph(page);
  report.checks.default_thirty_nodes = initial.recipe.nodes.length === 30;
  assert.ok(report.checks.default_thirty_nodes, 'The packaged default must retain all 30 numeric actions');
  report.readiness = await page.evaluate(() => window.workspaceWorkerReadiness);
  const workspace = await page.evaluate(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), loaded = await backend.loadWorkspace();
    return { capabilities: backend.capabilities, metadata: loaded.metadata,
      positions_bytes: loaded.positions.byteLength, indices_bytes: loaded.indices.byteLength,
      transferOwnership: loaded.transferOwnership };
  });
  report.workspace = workspace;
  assert.equal(workspace.capabilities.numerical, true);
  assert.equal(workspace.capabilities.cad, site.manifest.capabilities.cad);
  assert.equal(workspace.positions_bytes, 24999 * 3 * 4);
  assert.equal(workspace.indices_bytes, 49994 * 3 * 4);
  assert.equal(workspace.transferOwnership.sender_detached, true);
  assert.equal(workspace.transferOwnership.strategy, 'copy-contiguous-wasm-view-then-transfer');
  report.checks.display_buffers_transferred = true;

  const fit = initial.recipe.nodes.find(node => node.operation === 'fit');
  await page.locator(`.action-select[data-action-id="${fit.id}"]`).dblclick();
  await page.locator('#metrics').waitFor({ state: 'visible' });
  report.fit_metrics = await page.locator('#metrics').textContent();
  assert.match(report.fit_metrics, /RMS/);
  await page.locator('#close-edit').click();
  await page.locator('#viewport .display-options > summary').click();
  await page.locator('#colors').selectOption('residual');
  await page.locator('#residual-scale').waitFor({ state: 'visible' });
  report.residual_display = await page.locator('#residual-scale').textContent();
  await page.locator('#colors').selectOption('selection');
  await page.locator('#viewport .display-options > summary').click();
  report.checks.real_fit_residual_display = true;

  for (const id of ['new-build-faces', 'new-surface-intersection', 'new-trimmed-face', 'new-body', 'export-cad']) {
    if (workspace.capabilities.cad) continue;
    assert.ok(await page.locator('#' + id).isDisabled(), `CAD control ${id} must be disabled`);
    const rendered = page.locator(`[data-command-id="${id}"]`);
    for (let index = 0; index < await rendered.count(); index++)
      assert.ok(await rendered.nth(index).isDisabled(), `Rendered CAD control ${id} must be disabled`);
  }
  report.checks.cad_controls_match_capabilities = true;
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  const selected = initial.recipe.nodes.filter(node => node.operation === 'selection')
    .sort((left, right) => right.ids.length - left.ids.length)[0];
  await page.locator(`.action-select[data-action-id="${selected.id}"]`).dblclick();
  await page.locator('#selection-tools').waitFor({ state: 'visible' });
  await page.locator('#dock-edit').click();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.locator('#tool').selectOption('remove');
  await page.locator('#selection-shape').selectOption('rectangle');
  await page.locator('#selection-depth').selectOption('through_all');
  const canvas = page.locator('#viewport canvas');
  const bounds = await canvas.boundingBox();
  let edited;
  const gestures = [];
  const offsets = [0, -.1, .1, -.2, .2].flatMap(dx => [0, -.1, .1, -.2, .2].map(dy => [dx, dy]));
  for (const [dx, dy] of offsets) {
    const x = bounds.x + bounds.width * (.5 + dx), y = bounds.y + bounds.height * (.5 + dy);
    const uncovered = await page.evaluate(({ x, y }) =>
      document.elementFromPoint(x, y) === document.querySelector('#viewport canvas') &&
      document.elementFromPoint(x + 24, y + 24) === document.querySelector('#viewport canvas'), { x, y });
    if (!uncovered) continue;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + 24, y + 24, { steps: 3 }); await page.mouse.up();
    await page.waitForFunction(() => !document.querySelector('#status').textContent.includes('Saving selection'), null, { timeout: 15000 });
    const current = await readGraph(page);
    const count = current.recipe.nodes.find(node => node.id === selected.id).ids.length;
    gestures.push({ x, y, count });
    if (count !== selected.ids.length) { edited = current; break; }
  }
  assert.ok(edited, 'A real canvas gesture must change selection membership');
  const changedSelection = edited.recipe.nodes.find(node => node.id === selected.id);
  assert.ok(changedSelection.ids.length >= selected.ids.length * .8 && changedSelection.ids.length < selected.ids.length);
  assert.notEqual(edited.token, initial.token);
  report.selection_edit = { id: selected.id, before: selected.ids.length, after: changedSelection.ids.length, gestures };
  await page.locator('#tool').selectOption('orbit');
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  edited = await idleGraph(page);
  report.checks.ui_selection_edit_and_evaluation = true;

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  const download = await downloadPromise;
  assert.equal(path.basename(download.suggestedFilename()), download.suggestedFilename());
  const downloaded = path.join(output, download.suggestedFilename());
  await download.saveAs(downloaded);
  const recipeBytes = await readFile(downloaded), recipe = JSON.parse(recipeBytes);
  assert.deepEqual(recipe, edited.recipe);
  report.recipe_download = { name: download.suggestedFilename(), bytes: recipeBytes.length, sha256: sha256(recipeBytes) };
  await page.close();
  const replayPage = await makePage();
  await idleGraph(replayPage);
  await replayPage.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  await replayPage.locator('#file').setInputFiles({ name: 'replayed-actions.json', mimeType: 'application/json', buffer: recipeBytes });
  await replayPage.waitForFunction(async token => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    return (await getBackend().getGraph()).token === token;
  }, edited.token, { timeout: 30000 });
  await replayPage.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  const replayed = await idleGraph(replayPage);
  report.replay_comparison = compareSnapshot(replayed, edited);
  report.checks.recipe_download_new_worker_replay = true;

  const browserSnapshots = JSON.stringify({ default: initial, edited, replayed });
  await writeFile(path.join(output, 'browser-graphs.json'), browserSnapshots);
  report.browser_graphs_sha256 = sha256(Buffer.from(browserSnapshots));
  await writeFile(path.join(output, 'recipes.json'), JSON.stringify({ default: initial.recipe, edited: edited.recipe }, null, 2));
  const runtime = path.join(site.root, site.manifest.runtime.path);
  const native = await nativeReference(runtime);
  report.native_versions = native.versions;
  report.native_comparison = { default: compareSnapshot(initial, native.snapshots.default),
    edited: compareSnapshot(edited, native.snapshots.edited), replayed: compareSnapshot(replayed, native.snapshots.edited) };
  report.checks.actual_native_adapter_parity = true;

  report.rejections = await boundedEvaluate(replayPage, async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), results = [];
    const test = async (name, operation, payload) => {
      const before = await backend.getGraph();
      let error;
      try { await backend[operation](payload); }
      catch (value) { error = { code: value.code, status: value.status, message: value.message }; }
      const after = await backend.getGraph();
      results.push({ name, error, atomic: JSON.stringify(before) === JSON.stringify(after) });
    };
    const current = await backend.getGraph();
    await test('stale token', 'replaceRecipe', { token: 'stale-token', recipe: current.recipe });
    for (const key of ['source_sha256', 'reference_sha256']) {
      const recipe = structuredClone(current.recipe);
      recipe.nodes.find(node => node.operation === 'source')[key] = '0'.repeat(64);
      await test(key, 'importRecipe', { token: current.token, recipe });
    }
    if (!backend.capabilities.cad) {
      const recipe = structuredClone(current.recipe), fits = recipe.nodes.filter(node => node.operation === 'fit');
      recipe.nodes.push({ id: 'unavailable_cad_probe', label: 'Unavailable CAD probe', operation: 'surface_intersection',
        first: { feature: fits[0].id }, second: { feature: fits[1].id } });
      await test('CAD recipe', 'importRecipe', { token: current.token, recipe });
      await test('CAD export', 'exportCad', { token: current.token });
    }
    await test('other source', 'selectExample', { token: current.token, example: 'repeated-boss' });
    return { results, examples: await backend.listExamples() };
  });
  for (const rejection of report.rejections.results) {
    assert.ok(rejection.error && rejection.atomic, `${rejection.name} must reject atomically`);
    if (rejection.name === 'stale token') assert.equal(rejection.error.status, 409);
    else if (rejection.name.includes('CAD') || rejection.name === 'other source') assert.equal(rejection.error.code, 'unsupported_capability');
    else assert.equal(rejection.error.status, 422);
  }
  assert.deepEqual(report.rejections.examples.examples.map(example => example.id), ['nozzle']);
  report.checks.invalid_requests_atomic_no_fallback = true;

  report.terminal_stream = await boundedEvaluate(replayPage, async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), initial = await backend.getGraph();
    await backend.ensureGraph({ token: initial.token, targets: [initial.recipe.nodes[0].id] });
    const idle = await backend.getGraph(), updates = [];
    for await (const state of backend.graphUpdates({ revision: idle.revision })) {
      updates.push({ token: state.token, revision: state.revision, unchanged: state.unchanged,
        evaluation_running: state.evaluation_running });
      if (updates.length > 4) throw new Error('Idle observation did not terminate');
    }
    return { token: idle.token, updates };
  });
  assert.ok(report.terminal_stream.updates.length >= 1);
  assert.equal(report.terminal_stream.updates.at(-1).evaluation_running, false);
  assert.equal(report.terminal_stream.updates.at(-1).token, report.terminal_stream.token);
  report.checks.already_terminal_stream_observed = true;

  report.frozen_fixture = await replayPage.evaluate(async () => {
    const configuration = (await import(new URL('backend-config.js', document.baseURI).href)).default;
    const worker = new Worker(new URL(configuration.runtimeURL, document.baseURI), { type: 'module' });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { worker.terminate(); reject(new Error('Frozen numerical probe timed out')); }, 180000);
      const done = (error, result) => { clearTimeout(timer); worker.terminate(); if (error) reject(error); else resolve(result); };
      worker.onerror = event => { event.preventDefault(); done(new Error(event.message)); };
      worker.onmessage = ({ data }) => {
        if (data.fatal) { done(new Error(data.fatal)); return; }
        if (data.id !== 991) return;
        if (!data.ok) done(new Error(typeof data.error === 'string' ? data.error : data.error?.message));
        else done(null, data);
      };
      worker.postMessage({ id: 991, operation: 'numeric' });
    });
  });
  report.checks.frozen_float64_fixture_parity = true;
  assert.equal(report.unexpected_requests.length, 0);
  assert.equal(report.api_requests.length, 0);
  assert.equal(report.page_errors.length, 0);
  report.checks.no_external_or_computation_http = true;
  report.finished_utc = new Date().toISOString();
  await context.close();
} catch (error) { report.failure = String(error); throw error; }
finally {
  clearTimeout(watchdog);
  if (browser) await browser.close();
  if (preview) { report.static_requests = preview.requests; await preview.close(); }
  try {
    const after = await verifySite(directory);
    report.artifact_unchanged = after.manifest_sha256 === site.manifest_sha256;
  } catch (error) { report.artifact_unchanged = false; report.artifact_verification_error = String(error); }
  await saveReport();
  if (!report.artifact_unchanged) throw new Error('Artifact changed during workspace validation');
}
console.log(JSON.stringify({ browser: report.browser, checks: report.checks,
  report: path.join(output, 'workspace.json') }, null, 2));
