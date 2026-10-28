// Separate actual-UI acceptance; the established release evidence envelope stays unchanged.
import assert from 'node:assert/strict';
import {sourceAuthorityPython} from './source-authority-observer.mjs';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {verifySite, startPreview, prepareReports, sha256} from './server.mjs';
import {readSourceEvidence} from './source-import-harness.mjs';
import {rawAppObserverSuffix, runRawSourceWorkflowUi} from '../browser_viewer/tests/raw-source-workflow-harness.mjs';

const repository = fileURLToPath(new URL('../..', import.meta.url));
const options = {};
for (let index = 2; index < process.argv.length; index += 2) {
  const name = process.argv[index], value = process.argv[index + 1];
  assert(['--directory', '--output', '--native-python', '--playwright-directory'].includes(name), `Unknown option ${name}`);
  assert(value && !value.startsWith('--') && !(name in options), `Missing/duplicate ${name}`);
  options[name] = value;
}
assert(options['--directory'] && options['--output'], 'Expected --directory and fresh --output');
const nativePython = path.resolve(options['--native-python'] || path.join(repository, '.venv/bin/python'));
const playwrightDirectory = path.resolve(options['--playwright-directory'] || path.join(repository, 'experiments/browser_viewer'));
const site = await verifySite(options['--directory']);
assert.equal(site.manifest.backend.type, 'worker');
assert.equal(site.manifest.capabilities.cad, true);
assert.equal(site.manifest.source_worktree_dirty, false);
assert.match(site.manifest.source_revision, /^[a-f0-9]{40}$/);
const runtime = path.join(site.root, site.manifest.runtime.path);
const runtimeManifest = JSON.parse(await readFile(path.join(runtime, 'manifest.json'), 'utf8'));
assert.equal(runtimeManifest.cad, true);
assert.equal(runtimeManifest.source_revision, site.manifest.source_revision);
assert.equal(runtimeManifest.source_worktree_dirty, false);
const trustedFiles = new Map();
for (const record of runtimeManifest.source_files) {
  assert.equal(sha256(await readFile(path.join(repository, record.path))), record.sha256, `Current trusted source differs: ${record.path}`);
  trustedFiles.set(path.join(repository, record.path), record.sha256);
}
const output = await prepareReports(options['--output'], [site.root, repository]);
const rawPath = path.join(repository, 'examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply');
assert.equal(sha256(await readFile(rawPath)), '537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358');
const harnessPath = path.join(repository, 'experiments/browser_viewer/tests/raw-source-workflow-harness.mjs');
const oraclePath = path.join(repository, 'experiments/browser_static/raw_workflow_expected.py');
for (const filename of [fileURLToPath(import.meta.url), harnessPath, oraclePath, rawPath,
  fileURLToPath(new URL('./server.mjs', import.meta.url)), fileURLToPath(new URL('./source-import-harness.mjs', import.meta.url)),
  fileURLToPath(new URL('./source-authority-observer.mjs',import.meta.url))]) {
  trustedFiles.set(filename, sha256(await readFile(filename)));
}
const sourcePython = sourceAuthorityPython + "import json\nb=browser_workspace;w=b.workspace\njson.dumps({'descriptor':w.source_descriptor.model_dump(),'source_sha256':w.source_sha256,'project_generation':b.workspace_generation,'source_mode':b.source_mode,**_source_authority_observation(w),'positions':w.local.tolist(),'normals':w.data.normals.tolist(),'original_normals':w.data.original_normals.tolist(),'triangles':w.data.triangles.tolist(),'weights':w.data.weights.tolist()},allow_nan=False)";
const sourceObserverSuffix = `
globalThis.__sourceEvidence242 = () => {
 const record=JSON.parse(python.runPython(${JSON.stringify(sourcePython)}));
 record.owned_fs_files=[];
 if(python.FS.analyzePath('/scansor-source-staging').exists)for(const name of python.FS.readdir('/scansor-source-staging')){
  if(name!=='.'&&name!=='..'){const path='/scansor-source-staging/'+name;record.owned_fs_files.push({path,bytes:python.FS.stat(path).size});}
 }
 record.owned_fs_bytes=record.owned_fs_files.reduce((sum,item)=>sum+item.bytes,0);
 return record;
};
`;
const report = {schema: 'scansor-raw-source-worker-ui-v1', complete: false, publication: false,
  source_revision: site.manifest.source_revision, manifest_sha256: site.manifest_sha256, runtime: site.manifest.runtime,
  runner_sha256: sha256(await readFile(new URL(import.meta.url))),
  harness_sha256: sha256(await readFile(harnessPath)), oracle_sha256: sha256(await readFile(oraclePath)),
  trusted_files: [...trustedFiles].map(([filename, hash]) => ({path: path.relative(repository, filename), sha256: hash})),
  instrumentation: {app_suffix_sha256: sha256(Buffer.from(rawAppObserverSuffix)),
    worker_suffix_sha256: sha256(Buffer.from(sourceObserverSuffix)),
    description: 'Manifest-verified shipped modules with appended read-only app/source observers. All workflow mutations use actual product controls.'},
  profiles: [], page_errors: [], api_requests: [], external_requests: [], cleanup_errors: []};

async function cleanup(label, action) {
  try { await action(); } catch (error) { report.cleanup_errors.push({label, error: String(error)}); }
}
async function runOracle(args, destination) {
  const result = await new Promise((resolve, reject) => {
    const child = spawn(nativePython, [oraclePath, ...args], {
      cwd: repository, env: {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1', PYTHONDONTWRITEBYTECODE: '1', TMPDIR: destination},
      stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', stderr = '', expired = false;
    child.stdout.on('data', value => { stdout += value; });
    child.stderr.on('data', value => { stderr += value; });
    const timer = setTimeout(() => { expired = true; child.kill('SIGTERM'); }, 180000);
    const killer = setTimeout(() => child.kill('SIGKILL'), 185000);
    const clear = () => { clearTimeout(timer); clearTimeout(killer); };
    child.once('error', error => { clear(); reject(error); });
    child.once('close', (code, signal) => { clear(); resolve({code, signal, expired, stdout, stderr}); });
  });
  await writeFile(path.join(destination, 'native-oracle-process.json'), JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
  assert.equal(result.expired, false, 'Native raw-workflow oracle timed out');
  assert.equal(result.code, 0, result.stderr.slice(-12000));
}

let browser, preview;
try {
  const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
  browser = await playwright.chromium.launch({headless: true});
  report.browser = {name: 'chromium', version: browser.version()};
  for (const isolation of [false, true]) {
    const label = isolation ? 'isolated' : 'plain', destination = path.join(output, label);
    await mkdir(destination);
    const item = {isolation_requested: isolation, complete: false, page_errors: []};
    report.profiles.push(item);
    const prefix = '/raw-source-workflow/nested/';
    preview = await startPreview(site, {prefix, isolation});
    const context = await browser.newContext({viewport: {width: 1920, height: 1080}, serviceWorkers: 'block', acceptDownloads: true});
    const page = await context.newPage();
    page.setDefaultTimeout(60000);
    page.on('pageerror', error => {
      item.page_errors.push(String(error));
      report.page_errors.push({profile: label, error: String(error)});
    });
    context.on('request', request => {
      const url = request.url();
      if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) report.external_requests.push(url);
      if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
    });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if ((url.origin !== preview.origin && url.protocol !== 'blob:') || /\/api(?:\/|$)/.test(url.pathname)) {
        return route.abort('blockedbyclient');
      }
      const suffix = url.pathname.endsWith('/app.js') ? rawAppObserverSuffix : url.pathname.endsWith('/worker.mjs') ? sourceObserverSuffix : null;
      if (!suffix) return route.continue();
      assert.equal(url.origin, preview.origin);
      const relative = url.pathname.slice(prefix.length), record = site.records.get(relative);
      assert(record, 'Observed module must be manifest-listed');
      const bytes = await readFile(path.join(site.root, relative));
      assert.equal(bytes.length, record.bytes);
      assert.equal(sha256(bytes), record.sha256);
      await route.fulfill({status: 200, contentType: 'text/javascript', body: Buffer.concat([bytes, Buffer.from(suffix)]),
        headers: isolation ? {'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp'} : {}});
    });
    try {
      await page.goto(preview.origin + prefix + 'app/');
      await page.waitForFunction(() => globalThis.__rawWorkflow296?.snapshot()?.ready, null, {timeout: 180000});
      const snapshot = options => page.evaluate(options => globalThis.__rawWorkflow296.snapshot(options), options);
      const projection = () => page.evaluate(() => globalThis.__rawWorkflow296.projection());
      item.worker_sources = {};
      await runRawSourceWorkflowUi({page, rawPath, output: destination, snapshot, projection, report: item,
        checkpoint: async () => {
          for (const name of ['original', 'replayed']) if (item.graphs?.[name] && !item.worker_sources[name]) {
            const source = await readSourceEvidence(page);
            assert.equal(source.project_generation, item.graph_generations[name]);
            assert.equal(source.owned_fs_bytes, 0);
            assert.deepEqual(source.owned_fs_files, []);
            item.worker_sources[name] = source;
          }
          await writeFile(path.join(destination, 'ui-workflow.json'), JSON.stringify(item, null, 2) + '\n');
        }});
      assert.deepEqual(Object.keys(item.worker_sources).sort(), ['original', 'replayed']);
      item.isolation_actual = {page: await page.evaluate(() => crossOriginIsolated),
        worker: await page.workers()[0].evaluate(() => crossOriginIsolated)};
      assert.deepEqual(item.isolation_actual, {page: isolation, worker: isolation});
      const captured = path.join(destination, 'ui-workflow.json'), oracleOutput = path.join(destination, 'native-oracle.json');
      await writeFile(captured, JSON.stringify(item, null, 2) + '\n');
      await runOracle(['--raw', rawPath, '--report', captured, '--cad', path.join(destination, 'scansor-cad.zip'),
        '--runtime', runtime, '--output', oracleOutput], destination);
      item.native_oracle = JSON.parse(await readFile(oracleOutput, 'utf8'));
      assert.equal(item.native_oracle.complete, true);
      assert.deepEqual(Object.keys(item.native_oracle.checks).sort(), ['raw_decode', 'incident_weights', 'all_selected_rows',
        'residual_objective', 'support', 'download_metadata', 'reference_mesh', 'step_readback', 'native_replay', 'semantic_parity'].sort());
      assert(Object.values(item.native_oracle.checks).every(value => value === true));
      item.runner_complete = true;
    } finally {
      await cleanup(label + ' context.close', () => context.close());
      await cleanup(label + ' partial report', () => writeFile(path.join(destination, 'ui-workflow.json'), JSON.stringify(item, null, 2) + '\n'));
      await cleanup(label + ' preview.close', () => preview.close());
      preview = null;
    }
  }
  report.workflow_complete = true;
} catch (error) {
  report.failure = String(error);
  report.failure_stack = error?.stack;
} finally {
  if (browser) await cleanup('browser.close', () => browser.close());
  if (preview) await cleanup('preview.close', () => preview.close());
  await cleanup('artifact verification', async () => { report.artifact_unchanged = (await verifySite(site.root)).manifest_sha256 === site.manifest_sha256; });
  await cleanup('trusted input verification', async () => {
    for (const [filename, expected] of trustedFiles) assert.equal(sha256(await readFile(filename)), expected, `Trusted input changed: ${filename}`);
    report.trusted_inputs_unchanged = true;
  });
  report.complete = report.workflow_complete === true && report.artifact_unchanged === true && report.profiles.length === 2
    && report.trusted_inputs_unchanged === true
    && report.profiles.every(item => item.complete && item.runner_complete) && !report.failure
    && ['page_errors', 'api_requests', 'external_requests', 'cleanup_errors'].every(key => report[key].length === 0);
  await writeFile(path.join(output, 'raw-source-workflow.json'), JSON.stringify(report, null, 2) + '\n');
  if (!report.complete) process.exitCode = 1;
  console.log(JSON.stringify({complete: report.complete, profiles: report.profiles.map(item => ({isolated: item.isolation_requested, complete: item.complete})),
    failure: report.failure, cleanup_errors: report.cleanup_errors}));
}
