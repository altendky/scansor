// Test-only resource observations against verified, unchanged artifact bytes.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { prepareReports, sha256, startPreview, verifySite } from './server.mjs';
import {
  chooseSourceFile, openSourceImportPage, prepareSourceInputs, readSourceEvidence,
} from './source-import-harness.mjs';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw Error(`Missing ${name}`);
  return args[index + 1];
};
const directory = argument('--directory'), outputDirectory = argument('--output');
if (!directory || !outputDirectory) throw Error(
  'Usage: node measure-source-import.mjs --directory <verified site> --output <fresh reports> [--native-python PATH] [--playwright-directory PATH] [--isolation]');
const ownRepository = fileURLToPath(new URL('../..', import.meta.url));
const repository = path.resolve(argument('--repository', ownRepository));
const playwrightDirectory = path.resolve(argument('--playwright-directory', path.join(repository, 'experiments/browser_viewer')));
const nativePython = path.resolve(argument('--native-python', path.join(repository, '.venv/bin/python')));
const site = await verifySite(directory);
const output = await prepareReports(outputDirectory, [site.root, repository, ownRepository]);
process.env.TMPDIR = output;
const { chromium } = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const isolation = args.includes('--isolation');

const pythonInventory = `
def _source_memory_inventory242():
    import json, numpy as np, sys
    b = browser_workspace
    w = b.workspace
    pairs = [('source.xyz', w.data.xyz), ('source.normals', w.data.normals),
             ('source.triangles', w.data.triangles), ('source.weights', w.data.weights),
             ('solver.local', w.local), ('frame.origin', w.origin), ('frame.columns', w.frame),
             ('display.positions', b.display_positions), ('display.indices', b.display_indices)]
    for name in ('original_normals', 'face_status', 'ids'):
        value = getattr(w.data, name, None)
        if isinstance(value, np.ndarray):
            pairs.append(('source.' + name, value))
    roots, views = {}, []
    for name, array in pairs:
        owner = array
        while isinstance(owner.base, np.ndarray):
            owner = owner.base
        address = str(owner.__array_interface__['data'][0])
        roots[address] = int(owner.nbytes)
        views.append(dict(name=name, dtype=str(array.dtype), shape=list(array.shape),
                          logical_bytes=int(array.nbytes), root_address=address,
                          root_bytes=int(owner.nbytes), non_ndarray_base=type(owner.base).__name__,
                          writeable=bool(array.flags.writeable)))
    return json.dumps(dict(views=views, unique_root_bytes=sum(roots.values()),
        immutable_original_bytes=len(getattr(w, 'original_bytes', b'')),
        export_bytes=len(b.export_buffer), cad_imported='OCP' in sys.modules,
        caveat='Named current NumPy payload only; root addresses deduplicate ndarray aliases, not external buffers or allocator/object overhead. No synchronous-call peak measurement.'))
_source_memory_inventory242()
`;
const workerSuffix = `\n// Test-only safe-boundary resource hook.\n
globalThis.__sourceMemory242 = () => {
  // The frame is nonempty even in an empty project. An empty NumPy buffer can
  // expose a zero-length JS sentinel rather than the real Wasm backing store.
  const proxy = python.runPython('browser_workspace.workspace.frame');
  let view;
  try {
    view = proxy.getBuffer('u8');
    return { wasm_capacity_bytes: view.data.buffer.byteLength,
      named_numpy: JSON.parse(python.runPython(${JSON.stringify(pythonInventory)})),
      at_ms: performance.now(),
      caveat: 'Wasm capacity at this boundary, not live bytes, RSS or a peak.' };
  } finally { view?.release(); proxy.destroy(); }
};\n`;

async function metricCollector(context, page) {
  const cdp = await context.newCDPSession(page);
  const targets = new Map(), pending = new Map();
  let requestId = 0;
  cdp.on('Target.attachedToTarget', event => {
    if (event.targetInfo.type === 'worker') targets.set(event.sessionId, event.targetInfo);
  });
  cdp.on('Target.detachedFromTarget', event => targets.delete(event.sessionId));
  cdp.on('Target.receivedMessageFromTarget', event => {
    const message = JSON.parse(event.message), callback = pending.get(message.id);
    if (callback) { pending.delete(message.id); callback(message); }
  });
  await cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: false });
  async function workerHeap(sessionId) {
    const id = ++requestId;
    let timer;
    try {
      return await new Promise((resolve, reject) => {
        timer = setTimeout(() => { pending.delete(id); reject(Error('Worker heap observation timed out')); }, 5000);
        pending.set(id, reply => reply.error ? reject(Error(JSON.stringify(reply.error))) : resolve(reply.result));
        cdp.send('Target.sendMessageToTarget', {
          sessionId, message: JSON.stringify({ id, method: 'Runtime.getHeapUsage' }),
        }).catch(error => { pending.delete(id); reject(error); });
      });
    } finally { clearTimeout(timer); }
  }
  return async name => {
    let workers;
    for (let attempt = 0; attempt < 100; attempt++) {
      workers = page.workers();
      if (workers.length === 1 && targets.size === 1 && [...targets.values()][0].url === workers[0].url()) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(workers.length, 1, 'Expected one current worker at observation boundary');
    assert.equal(targets.size, 1, 'Current worker CDP attachment is missing or stale');
    assert.equal([...targets.values()][0].url, workers[0].url());
    const workerHeaps = [];
    for (const [sessionId, target] of targets) workerHeaps.push({ session_id: sessionId, url: target.url, ...await workerHeap(sessionId) });
    const source = await readSourceEvidence(page, { arrays: false });
    return {
      name, at_utc: new Date().toISOString(),
      main_isolate: await cdp.send('Runtime.getHeapUsage'), worker_isolates: workerHeaps,
      worker_wasm: await workers[0].evaluate(() => globalThis.__sourceMemory242()),
      source,
      display: await page.evaluate(() => {
        const bundle = window.__sourceMeasurementBundle242;
        return {
          positions_bytes: bundle?.positions?.byteLength ?? 0,
          indices_bytes: bundle?.indices?.byteLength ?? 0,
          file_logical_bytes: window.__sourceImportFile242?.size ?? 0,
          webgl: window.__sourceMeasurementGpu242?.record ?? null,
          heartbeat: window.__sourceMeasurementHeartbeat242,
          isolated: crossOriginIsolated,
          caveat: 'File.size is logical payload, not measured backing allocation. Buffers and WebGL counters overlap other heap/process counters.',
        };
      }),
    };
  };
}

async function uploadGpu(page) {
  return page.evaluate(() => {
    const bundle = window.__sourceMeasurementBundle242;
    const positions = new Float32Array(bundle.positions), indices = new Uint32Array(bundle.indices);
    const canvas = document.createElement('canvas');
    canvas.width = 512; canvas.height = 256; document.body.append(canvas);
    const gl = canvas.getContext('webgl2');
    if (!gl) throw Error('WebGL2 unavailable for logical display-buffer observations');
    const shader = (type, source) => {
      const object = gl.createShader(type); gl.shaderSource(object, source); gl.compileShader(object);
      if (!gl.getShaderParameter(object, gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(object));
      return object;
    };
    const vertex = shader(gl.VERTEX_SHADER, '#version 300 es\nin vec3 position; uniform vec3 center; uniform float extent; void main(){gl_Position=vec4((position-center)/extent,1.0);}');
    const fragment = shader(gl.FRAGMENT_SHADER, '#version 300 es\nprecision mediump float; out vec4 color; void main(){color=vec4(0.3,0.7,0.9,1.0);}');
    const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const bounds = bundle.metadata.bounds;
    const center = bounds[0].map((value, i) => (value + bounds[1][i]) / 2);
    const extent = Math.max(...bounds[0].map((value, i) => bounds[1][i] - value)) || 1;
    gl.uniform3fv(gl.getUniformLocation(program, 'center'), center);
    gl.uniform1f(gl.getUniformLocation(program, 'extent'), extent);
    const position = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, position); gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
    const positionBytes = gl.getBufferParameter(gl.ARRAY_BUFFER, gl.BUFFER_SIZE);
    const location = gl.getAttribLocation(program, 'position'); gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 0, 0);
    const index = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    const indexBytes = gl.getBufferParameter(gl.ELEMENT_ARRAY_BUFFER, gl.BUFFER_SIZE);
    gl.viewport(0, 0, canvas.width, canvas.height); gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_INT, 0); gl.finish();
    if (gl.getError() !== gl.NO_ERROR) throw Error('WebGL upload/draw failed');
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const record = {
      positions_bytes: positionBytes, indices_bytes: indexBytes,
      logical_live_buffer_bytes: positionBytes + indexBytes,
      renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      caveat: 'Actual test-harness source geometry buffers and draw; excludes production UI normals/selection overlays, textures, shaders, framebuffers, driver copies and physical GPU memory.',
    };
    window.__sourceMeasurementGpu242 = {
      record,
      dispose() {
        gl.deleteBuffer(position); gl.deleteBuffer(index); gl.deleteProgram(program); gl.deleteShader(vertex); gl.deleteShader(fragment);
        record.logical_live_buffer_bytes = 0; canvas.remove();
      },
    };
    return record;
  });
}

const report = {
  schema_version: 1, kind: 'scansor-source-import-memory', started_utc: new Date().toISOString(),
  source_revision: site.manifest.source_revision, source_worktree_dirty: site.manifest.source_worktree_dirty,
  manifest_sha256: site.manifest_sha256, runtime: site.manifest.runtime, isolation,
  host: { platform: os.platform(), arch: os.arch(), cpu: os.cpus()[0]?.model, logical_cpus: os.cpus().length },
  instrumentation: {
    worker_suffix_sha256: sha256(Buffer.from(workerSuffix)),
    boundaries: 'Fresh page/worker per input; no forced GC. Heap/Wasm observations at safe boundaries cannot measure synchronous parser peaks or total process memory.',
    counters: 'Named NumPy roots, immutable bytes, File payload, JS heaps/backing storage and logical GPU storage overlap. Do not sum them.',
    rendering: 'Minimal test document and source-geometry WebGL draw; source-authoring product UI is a separate delivery gate.',
    file_selection: 'Real File supplied by setInputFiles; Playwright delivery time is not an OS picker benchmark.',
  },
  cases: [], requests: [], unexpected_requests: [], api_requests: [], page_errors: [], checks: {}, artifact_unchanged: null,
};
let browser, preview;
const watchdog = setTimeout(() => { report.failure = 'Source resource harness exceeded 15 minutes'; void browser?.close(); }, 900000);
try {
  const inputs = await prepareSourceInputs({ repository, output: path.join(output, 'inputs'), nativePython });
  report.inputs = inputs;
  preview = await startPreview(site, { prefix: '/source-memory242/', isolation });
  browser = await chromium.launch({ headless: true });
  report.browser = { name: 'chromium', version: browser.version(), headless: true };
  for (const input of inputs.cases) {
    const result = { name: input.name, source_sha256: input.sha256, source_bytes: input.source_bytes, vertices: input.vertices, faces: input.faces, stages: [] };
    report.cases.push(result);
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 800, height: 600 } });
    try {
      await context.route('**/*', route => new URL(route.request().url()).origin === preview.origin ? route.continue() : route.abort());
      context.on('request', request => {
        const url = request.url(); report.requests.push(url);
        if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) report.unexpected_requests.push(url);
        if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
      });
      const page = await context.newPage();
      page.on('pageerror', error => report.page_errors.push(String(error)));
      await page.addInitScript(() => {
        const heartbeat = window.__sourceMeasurementHeartbeat242 = { ticks: 0, max_gap_ms: 0 };
        let previous = performance.now();
        setInterval(() => { const now = performance.now(); heartbeat.ticks++; heartbeat.max_gap_ms = Math.max(heartbeat.max_gap_ms, now - previous); previous = now; }, 16);
      });
      const sample = await metricCollector(context, page);
      const hooks = await openSourceImportPage(page, { site, preview, prefix: '/source-memory242/', extraWorkerSuffix: workerSuffix, initialMode: 'empty', isolation });
      result.instrumentation = hooks;
      await page.evaluate(async () => { window.__sourceMeasurementBundle242 = await window.__sourceImportBackend242.loadWorkspace(); });
      result.stages.push(await sample('empty_ready'));
      await chooseSourceFile(page, input.path);
      result.stages.push(await sample('original_file_selected'));
      const imported = await page.evaluate(async () => {
        const backend = window.__sourceImportBackend242, before = window.__sourceMeasurementBundle242;
        const started = performance.now(), phases = new Map();
        try {
          const bundle = await backend.addScan({ file: window.__sourceImportFile242, generation: before.generation, token: before.graph.token }, {
            onProgress: value => phases.set(value.phase, { phase: value.phase, completed: value.completed, total: value.total, unit: value.unit, status: value.status }),
          });
          window.__sourceMeasurementBundle242 = bundle;
          return { accepted: true, elapsed_ms: performance.now() - started, generation: bundle.generation, metadata: bundle.metadata, phases: [...phases.values()] };
        } catch (error) { return { accepted: false, elapsed_ms: performance.now() - started, error: { code: error.code, message: error.message }, phases: [...phases.values()] }; }
      });
      result.import = imported;
      if (imported.accepted) {
        result.admission_limits = imported.metadata.capabilities.source_import.limits;
        assert.equal(imported.metadata.source.source_sha256, input.sha256);
        assert.equal(imported.metadata.vertices, input.vertices);
        assert.equal(imported.metadata.triangles, input.faces);
        result.stages.push(await sample('accepted_staging_released'));
        assert.equal(result.stages.at(-1).worker_wasm.named_numpy.immutable_original_bytes, input.source_bytes);
        result.gpu = await uploadGpu(page);
        result.stages.push(await sample('display_geometry_drawn'));
        if (input.name === 'raw-nozzle' || input.name === 'horizontal-scene') {
          result.force = await page.evaluate(async () => {
            const started = performance.now();
            await window.__sourceImportBackend242.forceRestart();
            const bundle = await window.__sourceImportBackend242.loadWorkspace();
            window.__sourceMeasurementBundle242 = bundle;
            return { elapsed_ms: performance.now() - started, generation: bundle.generation, metadata: bundle.metadata };
          });
          assert.equal(result.force.generation, imported.generation);
          assert.equal(result.force.metadata.source.source_sha256, input.sha256);
          result.stages.push(await sample('force_reconstructed_same_source'));
          assert.notEqual(result.stages.at(-1).worker_isolates[0].session_id, result.stages[0].worker_isolates[0].session_id);
        }
        await page.evaluate(async () => {
          window.__sourceMeasurementGpu242?.dispose();
          const backend = window.__sourceImportBackend242, bundle = window.__sourceMeasurementBundle242;
          window.__sourceMeasurementBundle242 = await backend.resetProject({ generation: bundle.generation, token: bundle.graph.token, confirmedDiscard: true });
          window.__sourceImportFile242 = undefined;
          for (const input of document.querySelectorAll('input[type=file]')) input.value = '';
        });
        result.stages.push(await sample('reset_empty_references_released'));
        assert.equal(result.stages.at(-1).worker_wasm.named_numpy.immutable_original_bytes, 0);
        assert.equal(result.stages.at(-1).display.positions_bytes + result.stages.at(-1).display.indices_bytes, 0);
      } else {
        const capabilities = await page.evaluate(async () => (await window.__sourceImportBackend242.loadWorkspace()).metadata.capabilities.source_import);
        const limits = capabilities.limits;
        result.admission_limits = limits;
        const estimate = 4 * input.source_bytes + 128 * input.vertices + 64 * input.faces + 32 * limits.chunk_bytes + 1048576;
        assert.ok(input.source_bytes > limits.max_source_bytes || input.vertices > limits.max_vertices || input.faces > limits.max_faces || estimate > limits.max_estimated_bytes,
          'A measured tier within admission controls unexpectedly failed');
        assert.equal(imported.error.code, 'source_limit', 'Expected an explicit admission rejection');
        result.stages.push(await sample('declared_limit_rejected'));
        assert.equal(result.stages.at(-1).worker_wasm.named_numpy.immutable_original_bytes, 0);
      }
      result.complete = true;
      await page.evaluate(() => window.__sourceImportBackend242.close());
    } finally { await context.close(); }
  }
  report.checks.source_import = ['raw-nozzle', 'horizontal-scene'].every(name => report.cases.some(value => value.name === name && value.import.accepted));
  report.checks.recovery = ['raw-nozzle', 'horizontal-scene'].every(name => report.cases.some(value => value.name === name && value.force?.metadata.source.source_sha256 === value.source_sha256));
  report.checks.cleanup = report.cases.every(value => value.stages.at(-1)?.worker_wasm.named_numpy.immutable_original_bytes === 0 && value.stages.at(-1)?.source.owned_fs_bytes === 0);
  report.checks.capacity_nonzero = report.cases.every(value => value.stages.every(stage => stage.worker_wasm.wasm_capacity_bytes > 0));
  report.checks.isolation_matches = report.cases.every(value => value.stages.every(stage => stage.display.isolated === isolation));
  report.checks.chunk_reads_bounded = report.cases.every(value => value.stages.every(stage => stage.source.largest_chunk_bytes <= value.admission_limits.chunk_bytes));
  report.checks.terminal_staging_released = report.cases.every(value => value.stages.every(stage => stage.source.owned_fs_bytes === 0 && stage.source.staging_bytes === 0));
  report.checks.all_cases_complete = report.cases.length === 4 && report.cases.every(value => value.complete);
  report.checks.no_external_computation = report.unexpected_requests.length === 0 && report.api_requests.length === 0;
  report.checks.no_page_errors = report.page_errors.length === 0;
  const after = await verifySite(site.root);
  report.artifact_unchanged = after.manifest_sha256 === site.manifest_sha256;
  report.checks.artifact_unchanged = report.artifact_unchanged;
  assert.ok(Object.values(report.checks).every(Boolean));
  report.complete = true;
} catch (error) {
  report.failure = String(error.stack || error); process.exitCode = 1;
} finally {
  clearTimeout(watchdog); await browser?.close(); await preview?.close();
  report.completed_utc = new Date().toISOString();
  await writeFile(path.join(output, 'source-import-memory.json'), JSON.stringify(report, null, 2) + '\n');
}
