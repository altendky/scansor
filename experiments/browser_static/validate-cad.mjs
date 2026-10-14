import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { verifySite, startPreview, prepareReports } from './server.mjs';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing ${name}`);
  return args[index + 1];
};
const directory = argument('--directory'), outputArgument = argument('--output');
if (!directory || !outputArgument)
  throw new Error('Usage: node validate-cad.mjs --directory <CAD site> --output <fresh external reports> [--native-python <existing interpreter>] [--browser chromium|firefox|webkit] [--isolation]');
const site = await verifySite(directory);
assert.equal(site.manifest.capabilities.cad, true, 'Build this profile with --cad');
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
  capabilities: site.manifest.capabilities, requests: [], external_requests: [], api_requests: [],
  page_errors: [], checks: {},
  inventory: { ui: 'Retained nozzle: shared intersection, bounded Unicode plane face, tessellation and ZIP export.',
    synthetic: 'Cylinder/cone sides, holes, oblique ellipse, periodic band, closed hollow body, open assembly failure, serial replay ownership, transformed STEP in three units. No UI-authored closed nozzle body claim.' },
};
const nativeCode = `import base64, json, sys, tempfile, zipfile, platform
from pathlib import Path
import numpy as np
import OCP, pydantic
runtime, output = map(Path, sys.argv[1:3])
with tempfile.TemporaryDirectory(prefix='native-cad-', dir=output) as temporary:
    root = Path(temporary)
    with zipfile.ZipFile(runtime / 'scansor.zip') as archive:
        assert all((root / name).resolve().is_relative_to(root.resolve()) for name in archive.namelist())
        archive.extractall(root)
    sys.path.insert(0, str(root))
    from experiments.browser_runtime.cad_cases import run_cases, inspect_bundle, _step_shapes, shape_metrics
    from experiments.browser_runtime.workspace import BrowserWorkspace
    from experiments.face_geometry import face_from_record
    from experiments.ocp_geometry import transform_shape
    def unpack(text):
        value = json.loads(text)
        assert value['ok'], value
        return value['result']
    workspace = BrowserWorkspace(root / 'examples/nozzle-bayonette-simplified')
    workspace.enable_cad()
    graph = unpack(workspace.call_json('getGraph'))
    recipe = json.loads((output / 'ui-graph.json').read_text())['recipe']
    graph = unpack(workspace.call_json('replaceRecipe', json.dumps({'token':graph['token'],'recipe':recipe})))
    prepared = unpack(workspace.prepare_evaluation_json('evaluateGraph', json.dumps({'token':graph['token'],'all_actions':True})))
    graph = unpack(workspace.finish_evaluation_json()) if prepared['run'] else prepared['state']
    assert not graph['required_failures'], graph['errors']
    bundle = inspect_bundle((output / 'ui-export.zip').read_bytes(), workspace.workspace)
    matrix = np.asarray(bundle['metadata']['transform_local_to_export'])
    expected = {n['label']:shape_metrics(transform_shape(face_from_record(graph['results'][n['id']]),matrix))
        for n in recipe['nodes'] if n['operation']=='trimmed_face'}
    cases = run_cases()
    browser = json.loads((output / 'worker-cases.json').read_text())
    reopened = {}
    for units, encoded in browser['step_base64'].items():
        shapes = _step_shapes(base64.b64decode(encoded),units)
        reopened[units] = {name:shape_metrics(shape) for name,shape in shapes.items()}
    body_reopened = {units:inspect_bundle(base64.b64decode(encoded),workspace.workspace)
        for units,encoded in browser['body_zip_base64'].items()}
    result = {'cases':cases,'ui_snapshot':graph,'ui_bundle':bundle,'expected_ui_shapes':expected,
        'browser_step_reopened':reopened,'browser_body_zip_reopened':body_reopened,
        'versions':{'python':platform.python_version(),'numpy':np.__version__,'pydantic':pydantic.__version__,'ocp':OCP.__version__}}
    (output / 'native-cad.json').write_text(json.dumps(result,allow_nan=False))
`;

async function nativeReference() {
  await new Promise((resolve, reject) => {
    const child = spawn(nativePython, ['-I', '-c', nativeCode,
      path.join(site.root, site.manifest.runtime.path), output], {
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
      if (code || timedOut) reject(new Error(`Native CAD reference failed (${code}, timeout=${timedOut}): ${messages}`));
      else resolve();
    });
  });
  return JSON.parse(await readFile(path.join(output, 'native-cad.json'), 'utf8'));
}

function compare(actual, expected, location = 'cases') {
  let numbers = 0, maxDifference = 0, maxDifferencePath = null;
  const walk = (value, reference, name) => {
    if (typeof reference === 'number') {
      assert.equal(typeof value, 'number', name);
      const difference = Math.abs(value - reference);
      assert.ok(Number.isFinite(value) && difference <= 1e-8 + 1e-8 * Math.abs(reference), `${name}: ${value} != ${reference}`);
      numbers++;
      if (difference > maxDifference) { maxDifference = difference; maxDifferencePath = name; }
    } else if (Array.isArray(reference)) {
      assert.ok(Array.isArray(value), name); assert.equal(value.length, reference.length, name);
      reference.forEach((item, index) => walk(value[index], item, `${name}[${index}]`));
    } else if (reference !== null && typeof reference === 'object') {
      assert.deepEqual(Object.keys(value).sort(), Object.keys(reference).sort(), name);
      for (const [key, item] of Object.entries(reference)) walk(value[key], item, `${name}.${key}`);
    } else assert.deepEqual(value, reference, name);
  };
  walk(actual, expected, location);
  return { numbers, max_difference: maxDifference, max_difference_path: maxDifferencePath, atol: 1e-8, rtol: 1e-8 };
}

const graph = page => page.evaluate(async () => {
  const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
  return getBackend().getGraph();
});
async function idle(page) {
  await page.waitForFunction(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const state = await getBackend().getGraph();
    return state.recipe && !state.evaluation_running &&
      Object.values(state.states).every(value => ['ready', 'failed', 'blocked'].includes(value));
  }, null, { timeout: 180000, polling: 100 });
  return graph(page);
}
async function chooseSurface(page, selector, feature) {
  const value = await page.locator(selector).evaluate((select, id) =>
    [...select.options].find(option => {
      if (!option.value) return false;
      const reference = JSON.parse(option.value);
      return reference.feature === id && !reference.surface;
    })?.value, feature);
  assert.ok(value, `Missing surface ${feature} in ${selector}`);
  await page.locator(selector).selectOption(value);
}

let browser, preview;
const watchdog = setTimeout(() => { report.failure = 'CAD harness exceeded 15 minutes'; void browser?.close(); }, 900000);
try {
  preview = await startPreview(site, { prefix: '/cad/deep/', isolation: args.includes('--isolation') });
  browser = await playwright[browserName].launch({ headless: true });
  report.browser = { name: browserName, version: browser.version() };
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', async route => {
    const url = route.request().url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) {
      report.external_requests.push(url); await route.abort(); return;
    }
    if (/\/api(?:\/|$)/.test(new URL(url).pathname)) {
      report.api_requests.push(url); await route.abort(); return;
    }
    await route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror', error => report.page_errors.push(String(error)));
  await page.addInitScript(() => {
    window.cadTransfers = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(...options) {
        super(...options);
        this.addEventListener('message', ({ data }) => {
          if (data?.kind === 'transfer-confirmed' && 'export_bytes_after' in data)
            window.cadTransfers.push({ id: data.id, export_bytes_after: data.export_bytes_after });
        });
      }
    };
  });
  await page.goto(preview.origin + '/cad/deep/app/', { waitUntil: 'domcontentloaded' });
  await page.locator('#viewport canvas').waitFor({ state: 'visible', timeout: 180000 });
  let current = await idle(page);
  assert.equal(current.recipe.nodes.length, 30);
  const cadAssetsBefore = report.requests.filter(url => /\.whl(?:\?|$)/.test(url) && /ocp/i.test(url));
  assert.equal(cadAssetsBefore.length, 0, 'Numeric startup must not download OCP');
  report.checks.numeric_startup_without_ocp = true;
  const cone = current.recipe.nodes.find(node => node.operation === 'fit' && node.label === 'outer fit');
  const plane = current.recipe.nodes.find(node => node.operation === 'fit' && node.label === 'top fit');
  assert.ok(cone && plane);
  await page.getByRole('button', { name: 'Surface intersection', exact: true }).click();
  await page.locator('#new-surface-intersection-label').fill('Shared shoulder boundary');
  await chooseSurface(page, '#new-intersection-first', cone.id);
  await chooseSurface(page, '#new-intersection-second', plane.id);
  await page.locator('#add-surface-intersection-form').getByRole('button', { name: 'Add intersection', exact: true }).click();
  current = await idle(page);
  const intersection = current.recipe.nodes.find(node => node.label === 'Shared shoulder boundary');
  assert.ok(intersection); assert.equal(current.states[intersection.id], 'ready');
  await page.getByRole('button', { name: 'Trimmed face', exact: true }).click();
  const unicode = '肩面 Ø12 — café';
  await page.locator('#new-trimmed-face-label').fill(unicode);
  await chooseSurface(page, '#new-face-surface', plane.id);
  await page.locator('#add-new-face-boundary').click();
  await page.locator('#new-face-boundary-rows [data-boundary-intersection]').selectOption(intersection.id);
  await page.locator('#new-face-boundary-rows [data-boundary-keep]').selectOption('inside');
  await page.locator('#add-trimmed-face-form').getByRole('button', { name: 'Add face', exact: true }).click();
  current = await idle(page);
  const face = current.recipe.nodes.find(node => node.label === unicode);
  assert.ok(face); assert.equal(current.states[face.id], 'ready');
  assert.equal(current.results[face.id].bounded, true);
  assert.ok(current.results[face.id].preview.indices.length > 0);
  report.checks.real_ui_face_authoring_tessellation = true;
  await writeFile(path.join(output, 'ui-graph.json'), JSON.stringify(current));
  await page.getByRole('button', { name: 'Export CAD…', exact: true }).click();
  await page.locator('#export-scope').selectOption('all_faces');
  await page.locator('#export-units').selectOption('Centimeters');
  assert.equal(await page.locator('#export-transform').inputValue(), '');
  await page.locator('#export-mesh').check();
  const downloadPromise = page.waitForEvent('download', { timeout: 180000 });
  await page.locator('#export-download').click();
  const download = await downloadPromise;
  await download.saveAs(path.join(output, 'ui-export.zip'));
  assert.equal(await download.failure(), null);
  report.transfers = await page.evaluate(() => window.cadTransfers);
  assert.ok(report.transfers.some(item => item.export_bytes_after === 0));
  report.checks.zip_buffer_sender_detached = true;
  report.stale_export = await page.evaluate(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), before = await backend.getGraph();
    let error;
    try { await backend.exportCad({ token: 'stale-token', scope: 'all_faces', units: 'Millimeters', axis_up: false, include_mesh: true }); }
    catch (value) { error = { status: value.status, code: value.code, message: value.message }; }
    return { error, atomic: JSON.stringify(before) === JSON.stringify(await backend.getGraph()) };
  });
  assert.equal(report.stale_export.error.status, 409); assert.ok(report.stale_export.atomic);
  report.export_after_rejection = await page.evaluate(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), current = await backend.getGraph();
    const bytes = await backend.exportCad({ token: current.token, scope: 'all_faces',
      units: 'Centimeters', axis_up: false, include_mesh: true });
    return { bytes: bytes.byteLength, array_buffer: bytes instanceof ArrayBuffer };
  });
  assert.ok(report.export_after_rejection.array_buffer && report.export_after_rejection.bytes > 0);
  report.transfers = await page.evaluate(() => window.cadTransfers);
  assert.equal(report.transfers.length, 2);
  assert.ok(report.transfers.every(item => item.export_bytes_after === 0));
  report.checks.stale_export_atomic = true;

  await page.getByRole('button', { name: 'Body…', exact: true }).click();
  await page.locator('#new-body-label').fill('Open single-face body');
  await page.locator('#body-add-all').click();
  // The fitted ellipse carries about 9.47e-7 source-unit face tolerance. Declare
  // a compatible tolerance to reach the open-shell check rather than stopping
  // earlier at the equally valid conservative input-tolerance refusal.
  await page.locator('#new-body-tolerance').fill('0.000002');
  await page.locator('#create-body').click();
  const failedBodyGraph = await idle(page);
  const failedBody = failedBodyGraph.recipe.nodes.find(node => node.label === 'Open single-face body');
  assert.ok(failedBody); assert.equal(failedBodyGraph.states[failedBody.id], 'failed');
  report.ui_failed_body = { node: failedBody, error: failedBodyGraph.errors[failedBody.id],
    diagnostic: failedBodyGraph.diagnostics[failedBody.id] };
  assert.match(report.ui_failed_body.error, /open|free|closed|edge/i);
  if (await page.locator('#body-dialog').isVisible())
    await page.locator('#body-dialog [data-close-dialog]').click();
  // Feature tree clicks toggle selection. Clear first rather than toggling the
  // already-selected newly-created Body off.
  if (await page.locator('#clear-feature-selection').isEnabled())
    await page.locator('#clear-feature-selection').click();
  // Open the existing feature inspector, which owns the diagnostic panel.
  await page.locator(`.action-select[data-action-id="${failedBody.id}"]`).dblclick();
  await page.locator('#body-diagnostics').waitFor({ state: 'visible' });
  assert.match(await page.locator('#body-diagnostics').innerText(), /open|free|closed|edge/i);
  await page.getByRole('button', { name: 'Export CAD…', exact: true }).click();
  await page.locator('#export-scope').selectOption('body');
  await page.locator('#export-target').selectOption(failedBody.id);
  await page.locator('#export-units').selectOption('Millimeters');
  assert.equal(await page.locator('#export-download').isDisabled(), true);
  report.ui_failed_body.export_error = await page.locator('#export-summary').innerText();
  assert.match(report.ui_failed_body.export_error, /Unavailable.*failed/i);
  await page.locator('#export-dialog [data-close-dialog]').click();
  report.checks.real_ui_open_body_diagnostic_export_refusal = true;

  // Call the normal domain API on a separate real default workspace, keeping
  // this integration check separate from the manually authored UI face recipe.
  const apiPage = await context.newPage();
  apiPage.on('pageerror', error => report.page_errors.push(String(error)));
  await apiPage.goto(preview.origin + '/cad/deep/app/', { waitUntil: 'domcontentloaded' });
  await apiPage.locator('#viewport canvas').waitFor({ state: 'visible', timeout: 180000 });
  await idle(apiPage);
  const faceApi = await apiPage.evaluate(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), initial = await backend.getGraph();
    const plane = initial.recipe.nodes.find(node => node.operation === 'fit' && node.label === 'top fit');
    const cone = initial.recipe.nodes.find(node => node.operation === 'fit' && node.label === 'outer fit');
    const surfaces = [{ feature: plane.id }, { feature: cone.id }];
    const candidates = await backend.faceCandidates({ token: initial.token, target: surfaces[0], surfaces });
    const proposal = await backend.previewFaces({ token: initial.token, surfaces });
    const face = proposal.faces.find(item => item.surface.feature === plane.id);
    const region = face.regions.find(item => item.bounded);
    if (!region) throw new Error('No bounded reviewed plane region in retained cone-plane proposal');
    const payload = { token: proposal.token, proposal_token: proposal.proposal_token,
      surfaces, label: 'Worker API face review', choices: [{ surface: face.surface, region_key: region.key }] };
    const rejections = [];
    for (const [operation, request] of [['previewFaces', { token: 'stale', surfaces }],
      ['applyFaces', { ...payload, proposal_token: 'stale-proposal' }]]) {
      const before = await backend.getGraph();
      let error;
      try { await backend[operation](request); }
      catch (value) { error = { code: value.code, status: value.status, message: value.message }; }
      rejections.push({ operation, error, atomic: JSON.stringify(before) === JSON.stringify(await backend.getGraph()) });
    }
    const applied = await backend.applyFaces(payload);
    const owner = applied.recipe.nodes.find(node => node.operation === 'build_faces' && node.label === payload.label);
    if (!owner) throw new Error('Applying reviewed face did not create its authoring owner');
    await backend.evaluateGraph({ token: applied.token, all_actions: true });
    return { candidates, proposal, payload, rejections, owner };
  });
  const appliedGraph = await idle(apiPage);
  const managed = appliedGraph.recipe.nodes.filter(node => node.managed_by === faceApi.owner.id &&
    ['trimmed_face', 'arranged_face'].includes(node.operation));
  assert.ok(managed.length > 0);
  assert.ok(managed.every(node => appliedGraph.states[node.id] === 'ready' && appliedGraph.results[node.id].bounded));
  assert.ok(faceApi.rejections.every(item => item.error?.status === 409 && item.atomic));
  await writeFile(path.join(output, 'face-api.json'), JSON.stringify({ ...faceApi, graph: appliedGraph }));
  report.face_api = { candidates: faceApi.candidates.candidates.length, managed_faces: managed.length,
    rejections: faceApi.rejections, proposal_token: faceApi.proposal.proposal_token };
  report.checks.worker_face_candidates_preview_apply_atomic_rejections = true;
  await apiPage.close();

  const workerProbes = await page.evaluate(async () => {
    const configuration = (await import(new URL('backend-config.js', document.baseURI).href)).default;
    const create = () => {
      const worker = new Worker(new URL(configuration.runtimeURL, document.baseURI), { type: 'module' });
      let id = 0;
      const request = operation => new Promise((resolve, reject) => {
        const current = ++id;
        const timer = setTimeout(() => { worker.terminate(); reject(new Error(`${operation} exceeded harness timeout`)); }, 180000);
        const failure = event => {
          event.preventDefault(); clearTimeout(timer);
          worker.removeEventListener('message', listen);
          reject(new Error(event.message));
        };
        const listen = ({ data }) => {
          if (data.id !== current && !data.fatal) return;
          clearTimeout(timer); worker.removeEventListener('message', listen);
          worker.removeEventListener('error', failure);
          if (data.fatal || !data.ok) reject(new Error(data.fatal || data.error?.message || data.error));
          else resolve(data);
        };
        worker.addEventListener('message', listen); worker.addEventListener('error', failure, { once: true });
        worker.postMessage({ id: current, operation });
      });
      return { worker, request };
    };
    const first = create();
    try {
      const numeric = await first.request('numeric');
      const cases = await first.request('cad-regressions'), repeated = await first.request('cad-regressions');
      first.worker.terminate();
      const second = create();
      try { return { numeric, cases: cases.result, repeated: repeated.result, fresh: (await second.request('cad-regressions')).result }; }
      finally { second.worker.terminate(); }
    } finally { first.worker.terminate(); }
  });
  await writeFile(path.join(output, 'worker-probes.json'), JSON.stringify(workerProbes));
  await writeFile(path.join(output, 'worker-cases.json'), JSON.stringify(workerProbes.cases));
  assert.equal(workerProbes.cases.operation, 1); assert.equal(workerProbes.repeated.operation, 2);
  assert.equal(workerProbes.fresh.operation, 1);
  report.serial_repeated = compare(workerProbes.repeated.cases, workerProbes.cases.cases);
  report.new_worker = compare(workerProbes.fresh.cases, workerProbes.cases.cases);
  report.checks.frozen_numeric_then_cad_serial_and_new_worker = true;
  const native = await nativeReference();
  report.native_versions = native.versions;
  report.native_cases = compare(workerProbes.cases.cases, native.cases.cases);
  assert.equal(workerProbes.cases.version, site.manifest.runtime.versions.ocp);
  report.ui_shapes = compare(native.ui_bundle.shapes, native.expected_ui_shapes, 'ui.step.shapes');
  report.ui_bundle = native.ui_bundle;
  assert.equal(native.ui_bundle.metadata.units, 'Centimeters');
  assert.deepEqual(native.ui_bundle.metadata.recipe, current.recipe);
  assert.equal(native.ui_bundle.metadata.geometry_mode, 'separate_faces');
  assert.equal(native.ui_bundle.metadata.solid, false);
  assert.equal(native.ui_bundle.metadata.sewn, false);
  assert.equal(native.ui_bundle.metadata.objects[0].name, unicode);
  assert.equal(native.ui_bundle.metadata.objects[0].extent_authority, 'declared_boundaries');
  assert.deepEqual(native.ui_bundle.metadata.objects[0].boundary_uses, face.boundaries);
  assert.deepEqual(native.ui_bundle.metadata.objects[0].bounds, current.results[face.id].bounds);
  assert.equal(native.ui_bundle.reference.dtype, 'float64');
  assert.equal(native.ui_bundle.reference.vertices, 24999);
  report.browser_step_reopened = native.browser_step_reopened;
  for (const [units, shapes] of Object.entries(native.browser_step_reopened))
    compare(Object.values(shapes)[0], Object.fromEntries(Object.entries(native.cases.cases[`step_${units}`])
      .filter(([key]) => !['name', 'unit_setting_restored'].includes(key))), `reopened.${units}`);
  report.checks.native_geometry_step_units_unicode_and_float64_reference = true;
  for (const [units, bundle] of Object.entries(native.browser_body_zip_reopened))
    compare(bundle, native.cases.cases[`body_zip_${units}`], `reopened.body.${units}`);
  report.browser_body_zip_reopened = native.browser_body_zip_reopened;
  report.checks.injected_body_zip_native_reopen_transform_provenance_tolerance = true;

  // A separate context has neither an already-loaded OCP module nor an HTTP
  // cache from the successful export. Only its CAD wheel response is modified.
  const missingContext = await browser.newContext({ serviceWorkers: 'block' });
  const missing = { fault: 'CAD wheel response fulfilled HTTP404', intercepted: [], numeric_after_failure: false };
  await missingContext.route('**/*', async route => {
    const url = route.request().url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) {
      report.external_requests.push(url); await route.abort(); return;
    }
    if (/\/api(?:\/|$)/.test(new URL(url).pathname)) {
      report.api_requests.push(url); await route.abort(); return;
    }
    if (/ocp.*\.whl(?:\?|$)/i.test(url)) {
      missing.intercepted.push(url);
      await route.fulfill({ status: 404, contentType: 'text/plain', body: 'Missing CAD asset (harness injection)' });
    } else await route.continue();
  });
  const missingPage = await missingContext.newPage();
  missingPage.on('pageerror', error => report.page_errors.push(String(error)));
  await missingPage.goto(preview.origin + '/cad/deep/app/', { waitUntil: 'domcontentloaded' });
  await missingPage.locator('#viewport canvas').waitFor({ state: 'visible', timeout: 180000 });
  assert.equal((await idle(missingPage)).recipe.nodes.length, 30);
  Object.assign(missing, await missingPage.evaluate(async () => {
    const { getBackend } = await import(new URL('backend.js', document.baseURI).href);
    const backend = getBackend(), before = await backend.getGraph();
    let error;
    try { await backend.faceCandidates({ token: before.token }); }
    catch (value) { error = { code: value.code, message: value.message }; }
    const after = await backend.getGraph();
    await backend.evaluateGraph({ token: after.token, all_actions: true });
    return { error, atomic: JSON.stringify(before) === JSON.stringify(after) };
  }));
  const afterMissing = await idle(missingPage);
  assert.equal(missing.error.code, 'cad_startup_failed');
  assert.match(missing.error.message, /404/);
  assert.ok(missing.atomic && missing.intercepted.length > 0);
  assert.equal(afterMissing.recipe.nodes.length, 30);
  assert.equal(Object.keys(afterMissing.errors).length, 0);
  missing.numeric_after_failure = true;
  report.missing_cad = missing;
  report.checks.missing_cad_numeric_remains_available = true;
  await missingContext.close();
  assert.equal(report.external_requests.length, 0); assert.equal(report.api_requests.length, 0);
  assert.equal(report.page_errors.length, 0);
  report.checks.no_external_or_computation_http = true;
  report.finished_utc = new Date().toISOString();
  await context.close();
} catch (error) { report.failure = String(error); throw error; }
finally {
  clearTimeout(watchdog);
  if (browser) await browser.close();
  if (preview) { report.static_requests = preview.requests; await preview.close(); }
  try { report.artifact_unchanged = (await verifySite(directory)).manifest_sha256 === site.manifest_sha256; }
  catch (error) { report.artifact_unchanged = false; report.artifact_error = String(error); }
  await writeFile(path.join(output, 'cad.json'), JSON.stringify(report, null, 2) + '\n');
  if (!report.artifact_unchanged) throw new Error('Artifact changed during CAD validation');
}
console.log(JSON.stringify({ browser: report.browser, checks: report.checks, report: path.join(output, 'cad.json') }, null, 2));
