import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createWorkerBackend } from './worker-backend.js';
import { createBackend } from './backend.js';

function fixture(options = {}) {
  const worker = { calls: [], terminated: 0,
    postMessage(message) { this.calls.push(message); },
    terminate() { this.terminated++; },
    reply(data) { this.onmessage({ data }); },
  };
  const backend = createWorkerBackend({ runtimeURL: './runtime/test/worker.mjs',
    workerFactory: (url, settings) => {
      assert(url.pathname.endsWith('/runtime/test/worker.mjs'));
      assert.deepEqual(settings, { type: 'module' });
      return worker;
    }, ...options });
  return { worker, backend };
}
const ready = { id: 1, ok: true, ready: true, versions: { python: '3.14.2' },
  source_revision: 'revision', source_worktree_dirty: false };

test('one worker initialization reports phases and declares the bounded numerical capabilities', async () => {
  const phases = [], { worker, backend } = fixture({ onPhase: phase => phases.push(phase) });
  const pending = backend.initialize();
  assert.equal(backend.initialize(), pending);
  worker.reply({ phase: 'Python runtime' });
  worker.reply({ ...ready, id: 2 });
  worker.reply(ready);
  assert.deepEqual(await pending, ready);
  assert.deepEqual(worker.calls, [{ id: 1, operation: 'initialize' }]);
  assert.deepEqual(phases, ['Python runtime']);
  const selected = createBackend({ type: 'worker', factories: { worker: () => backend } });
  assert.equal(selected.capabilities.numerical, true);
  assert.equal(selected.capabilities.cad, false);
  await assert.rejects(selected.exportCad(), error => error.code === 'unsupported_capability');
  selected.close();
  assert.equal(worker.terminated, 1);
});

test('runtime startup failures stay failed without retry or HTTP fallback', async () => {
  const { worker, backend } = fixture();
  const pending = backend.initialize();
  worker.reply({ id: 1, ok: false, error: 'Missing asset fixture.npz: HTTP 404' });
  await assert.rejects(pending, /Missing asset/);
  await assert.rejects(backend.initialize(), /Missing asset/);
  assert.equal(worker.calls.length, 1);
  assert.equal(worker.terminated, 1);
});

test('closing initialization settles its promise and ignores late readiness', async () => {
  const { worker, backend } = fixture();
  const pending = backend.initialize();
  backend.close();
  worker.reply(ready);
  await assert.rejects(pending, error => error.code === 'backend_closed');
  await assert.rejects(backend.initialize(), error => error.code === 'backend_closed');
  assert.equal(worker.terminated, 1);
});

test('startup timeout destroys the unfinished runtime', async () => {
  const { worker, backend } = fixture({ startupTimeout: 5 });
  await assert.rejects(backend.initialize(), /initialization timed out/);
  assert.equal(worker.terminated, 1);
});

test('message decode, worker construction and invalid ready responses fail explicitly', async () => {
  for (const kind of ['decode', 'invalid', 'worker-error']) {
    const { worker, backend } = fixture(), pending = backend.initialize();
    if (kind === 'decode') worker.onmessageerror();
    else if (kind === 'worker-error') worker.onerror({ message: 'Worker unavailable' });
    else worker.reply({ id: 1, ok: true });
    await assert.rejects(pending, error => error.code === 'worker_startup_failed');
    assert.equal(worker.terminated, 1);
  }
  const failed = createWorkerBackend({ runtimeURL: './worker.mjs', workerFactory: () => {
    throw new Error('Workers unavailable');
  } });
  await assert.rejects(failed.initialize(), /Workers unavailable/);
  assert.throws(() => createWorkerBackend(), error => error.code === 'missing_runtime');
});

test('fatal errors after readiness retire the runtime and notify the UI', async () => {
  const failures = [], { worker, backend } = fixture({ onFailure: error => failures.push(error.message) });
  const pending = backend.initialize();
  worker.reply(ready);
  await pending;
  worker.reply({ fatal: 'Runtime lost' });
  await assert.rejects(backend.initialize(), /Runtime lost/);
  assert.deepEqual(failures, ['Runtime lost']);
  assert.equal(worker.terminated, 1);
});

async function initialized(options) {
  const value = fixture(options), pending = value.backend.initialize();
  value.worker.reply(ready);
  await pending;
  return value;
}
const graph = (extra = {}) => ({ token: 'token', revision: 3,
  recipe: { nodes: [{ id: 'source', operation: 'source' }] },
  states: {}, results: {}, evaluation_running: false, ...extra });
const tick = () => Promise.resolve();
const settle = async () => { for (let n = 0; n < 8; n++) await tick(); };

const sourceProfile = { supported: true, formats: ['binary-triangle-ply-v1'], max_sources: 1,
  limits: { max_source_bytes: 1024, max_vertices: 100, max_faces: 200,
    max_estimated_bytes: 100000, chunk_bytes: 64 }, cancel: true,
  recovery: 'active-workspace-original-bytes' };
function sourceGraph(generation = 0, token = 'empty', nodes = []) {
  return graph({ project_generation: generation, token, recipe: { schema_version: 2, nodes, output: null } });
}
function sourceReady(extra = {}) {
  return { ...ready, worker_protocol: { version: 5 }, source_import: sourceProfile,
    source_mode: { kind: 'empty' }, source_identity: {}, graph: sourceGraph(), ...extra };
}
async function sourceFixture(options) {
  const value = fixture({ initialMode: 'empty', ...options }), loading = value.backend.initialize();
  value.worker.reply(sourceReady());
  await loading;
  return value;
}
function sourceFile(size = 30) {
  const file = Object.assign(new Blob([new Uint8Array(size)]), { name: 'original.ply' });
  file.arrayBuffer = () => assert.fail('The coordinator must not read whole source bytes.');
  return file;
}
function candidate(request, { generation = 1, kind = 'scan', identity = { source_sha256: 'hash' }, token = 'source', nodes,
  ...changes } = {}) {
  const empty = kind === 'empty';
  return { operationId: request.payload.operationId, workspaceGeneration: generation,
    source_mode: { kind, ...(kind === 'example' ? { example: 'nozzle' } : {}) }, source_identity: identity,
    metadata: { project_generation: generation, vertices: empty ? 0 : 3, triangles: empty ? 0 : 1,
      positions: { dtype: '<f4', components: 3 }, indices: { dtype: '<u4', components: 3 },
      capabilities: { source_import: sourceProfile }, import_warnings: [] },
    graph: sourceGraph(generation, token, nodes || (empty ? [] : [{ operation: 'source', id: 'source' }])),
    positions: new ArrayBuffer(empty ? 0 : 36), indices: new ArrayBuffer(empty ? 0 : 12), ...changes };
}
function sourceTransfer(worker, request, result, { confirm = true } = {}) {
  worker.reply({ id: request.id, generation: request.generation, ok: true, kind: 'source-candidate', result });
  if (confirm) worker.reply({ id: request.id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
}
function sourcePromotion(worker, request, generation = 1) {
  worker.reply({ id: request.id, kind: 'source-promoted', operationId: request.payload.operationId,
    workspaceGeneration: generation });
}

const actionsProfile = { supported: true, version: 2, limits: {
  max_json_bytes: 32 * 1024 * 1024, max_request_bytes: 32 * 1024 * 1024 + 1_000_000,
  max_volume_encoded_bytes: 16 * 1024 * 1024, max_volume_expanded_bytes: 128 * 1024 * 1024,
} };
function actionsReady(extra = {}) {
  return sourceReady({ worker_protocol: { version: 7, source_actions: actionsProfile },
    source_actions: actionsProfile, ...extra });
}
async function actionsFixture(options = {}, initial = {}) {
  const value = fixture({ initialMode: 'empty', sourceActions: true, ...options });
  const loading = value.backend.initialize();
  value.worker.reply(actionsReady(initial));
  await loading;
  return value;
}
function actionsCandidate(request, { warnings = [], sourceKind = 'generic', ...options } = {}) {
  const result = candidate(request, options);
  result.metadata.import_warnings = structuredClone(warnings);
  return { ...result, purpose: 'open-actions', sourceKind,
    uploadRequired: request.payload.file !== undefined, import_warning_count: warnings.length };
}
function inspection(request, extra = {}) {
  return { generation: request.payload.generation, token: request.payload.token,
    classification: 'locate', uploadRequired: true, descriptor: { source_bytes: 30 },
    recipe: { nodes: [{ id: 'saved', operation: 'source' }] }, import_warnings: [], ...extra };
}

test('actual worker preparation rejects a mismatched attached File before any slice or staging allocation', async () => {
  // Exercise the production transaction body with its external Python/FS seams
  // stubbed. No copy of the size policy or source-text presence assertion.
  const source = readFileSync(new URL('../browser_runtime/worker.mjs', import.meta.url), 'utf8');
  const start = source.indexOf('async function prepareSource(request)');
  const end = source.indexOf('\nfunction workspaceCall(', start);
  assert(start >= 0 && end > start, 'Production worker transaction boundary changed');
  for (const size of [90, 100, 110]) {
    let slices = 0, allocations = 0, preflights = 0, staged = 0, promotions = 0, unlinked = 0;
    const file = sourceFile(size);
    file.slice = (...args) => { slices++; return Blob.prototype.slice.apply(file, args); };
    const context = vm.createContext({
      activeSource: { id: 1, operationId: 'probe', cancelled: false }, minimumGeneration: 0, sourcePoisoned: false,
      sourceBoundary: undefined, workspaceHasCad: false,
      manifest: { worker_protocol: { source_import: { supported: true,
        limits: { max_source_bytes: 1000, chunk_bytes: 64 } }, source_actions: actionsProfile } },
      sourceTransferMetrics: {}, sourceCancelled: () => Error('Cancelled'),
      python: { FS: { mkdirTree() { allocations++; }, open() { allocations++; return {}; },
        write(stream, chunk) { staged += chunk.byteLength; }, close() {}, unlink() { unlinked++; } } },
      sourceCall(method, payload) {
        if (method === 'preflight_actions_json') {
          assert.equal(payload.file, undefined); assert.equal(payload.source.size, 100); preflights++;
          return { uploadRequired: true, requiresCad: false, descriptor: { source_bytes: 100 } };
        }
        if (method === 'prepare_source_json') {
          assert.equal(staged, 100);
          return { workspaceGeneration: 1, metadata: { capabilities: {} }, graph: { recipe: { nodes: [] } } };
        }
        assert.equal(method, 'promote_source_json'); promotions++;
      },
      loadCad: async () => assert.fail('Unexpected CAD load'), yieldMessages: async () => {}, cadActions: new Set(),
      postMessage(message) { if (message.kind === 'source-candidate') context.sourceBoundary.resolve(true); },
      ownedBuffer: () => { assert.equal(size, 100, 'Rejected source must not transfer display buffers'); return new ArrayBuffer(0); },
    });
    vm.runInContext(source.slice(start, end) + '\nglobalThis.prepare = prepareSource;', context);
    const preparing = context.prepare({ id: 1, generation: 0, payload: { operationId: 'probe', purpose: 'open-actions',
      source: { filename: 'x.ply', size: 100 }, file } });
    if (size === 100) {
      await preparing;
      assert.equal(slices, 2); assert.equal(staged, 100); assert.equal(promotions, 1); assert.equal(unlinked, 1);
    } else {
      await assert.rejects(preparing, error => error.code === 'source_mismatch');
      assert.equal(slices, 0); assert.equal(allocations, 0); assert.equal(promotions, 0);
    }
    assert.equal(preflights, 1);
  }
});

test('source actions are frozen opt-in and missing or malformed readiness never advertises usable methods', async () => {
  const disabled = fixture();
  assert.equal(disabled.backend.capabilities.sourceActions, false);
  for (const name of ['inspectActions', 'openActions'])
    await assert.rejects(disabled.backend[name]({ recipe: {} }), error => error.code === 'unsupported_capability');
  assert.equal(disabled.worker.calls.length, 0);
  disabled.backend.close();
  assert.throws(() => fixture({ sourceActions: 1 }), error => error.code === 'unsupported_capability');
  for (const corrupt of [value => { value.worker_protocol.version = 5; },
    value => { delete value.source_actions; },
    value => { value.source_actions = { supported: 1, version: 1 }; },
    value => { value.worker_protocol = { version: 6, source_actions: { supported: true, version: true } }; },
    value => { value.source_actions = { ...actionsProfile, unknown: true }; },
    value => { value.graph.project_generation = 2 ** 53; }]) {
    const { backend, worker } = fixture({ sourceActions: true }), loading = backend.initialize();
    const value = structuredClone(actionsReady()); corrupt(value); worker.reply(value);
    await assert.rejects(loading, error => error.code === 'worker_startup_failed');
    assert.equal(worker.terminated, 1);
    backend.close();
  }
});

test('inspection clones raw intent before startup and never remembers a saved recipe or changes Force authority', async () => {
  const value = fleet({ initialMode: 'empty', sourceActions: true }), recipe = { nodes: [{ id: 'raw-name', operation: 'source' }] };
  const inspecting = value.backend.inspectActions({ recipe, generation: 0, token: 'empty' });
  recipe.nodes[0].id = 'caller-mutated';
  let worker = value.workers[0]; worker.reply(actionsReady()); await settle();
  const request = worker.calls.at(-1);
  assert.equal(request.operation, 'inspect-actions');
  assert.equal(request.generation, 0);
  assert.equal(request.payload.recipe.nodes[0].id, 'raw-name');
  const response = inspection(request);
  worker.reply({ id: request.id, ok: true, result: response });
  assert.deepEqual(await inspecting, response);
  assert.deepEqual(await value.backend.exportRecipe(), sourceGraph().recipe);
  const restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(actionsReady({ id: worker.calls[0].id })); await settle();
  const restore = worker.calls.at(-1);
  assert.deepEqual(restore.payload.recovery.recipe, sourceGraph().recipe);
  sourceTransfer(worker, restore, candidate(restore, { generation: 0, kind: 'empty', token: 'empty', identity: {} }));
  sourcePromotion(worker, restore, 0); await restoring;
  value.backend.close();
});

test('inspection rejects stale cursors and malformed responses instead of mutating accepted graph', async () => {
  for (const corrupt of [value => { value.token = 'other'; }, value => { value.generation = 1; },
    value => { value.uploadRequired = 1; }, value => { value.import_warnings = {}; },
    value => { value.descriptor = null; }, value => { value.recipe = {}; }]) {
    const { backend, worker } = await actionsFixture();
    await assert.rejects(backend.inspectActions({ recipe: {}, generation: 1, token: 'empty' }),
      error => error.code === 'observation_superseded');
    const inspecting = backend.inspectActions({ recipe: {}, generation: 0, token: 'empty' }); await settle();
    const request = worker.calls.at(-1), response = inspection(request); corrupt(response);
    worker.reply({ id: request.id, ok: true, result: response });
    await assert.rejects(inspecting, error => error.code === 'worker_failed');
    assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe);
    backend.close();
  }
});

test('located actions clone original intent, retain actual File and publish warnings only as accepted metadata/counts', async () => {
  const { backend, worker } = await actionsFixture(), file = sourceFile(), records = [];
  const recipe = { nodes: [{ id: 'raw', operation: 'source' }] };
  await assert.rejects(backend.openActions({ recipe, generation: 0, token: 'empty' }),
    error => error.code === 'confirmation_required');
  const opening = backend.openActions({ recipe, file, generation: 0, token: 'empty', confirmedDiscard: true },
    { onProgress: value => records.push(value) });
  recipe.nodes[0].id = 'mutated'; await settle();
  const request = worker.calls.at(-1);
  assert.equal(request.payload.kind, undefined);
  assert.equal(request.payload.purpose, 'open-actions');
  assert.equal(request.payload.recipe.nodes[0].id, 'raw');
  assert.equal(request.payload.file, file);
  assert.deepEqual(request.payload.source, { filename: 'original.ply', size: 30 });
  await assert.rejects(backend.addScan({ file, generation: 0, token: 'empty' }), error => error.code === 'source_busy');
  await assert.rejects(backend.inspectActions({ recipe, generation: 0, token: 'empty' }), error => error.code === 'source_busy');
  const warnings = [{ id: 'point-2', old_name: 'Point', new_name: 'Point point', kind: 'feature' }];
  const result = actionsCandidate(request, { warnings });
  sourceTransfer(worker, request, result); sourcePromotion(worker, request);
  const accepted = await opening;
  assert.deepEqual(accepted.metadata.import_warnings, warnings);
  const status = await backend.getSourceOperation(records[0]);
  assert.equal(status.purpose, 'open-actions'); assert.equal(status.uploadRequired, true);
  assert.equal(status.import_warning_count, 1); assert.equal(status.acceptedGeneration, 1);
  for (const key of ['recipe', 'file', 'descriptor', 'source_identity', 'import_warnings', 'metadata'])
    assert.equal(Object.hasOwn(status, key), false);
  backend.close();
});

test('matching actions retain the accepted File and original warnings through Force after caller mutates delivered metadata', async () => {
  const value = fleet({ initialMode: 'empty', sourceActions: true }), loading = value.backend.initialize();
  let worker = value.workers[0]; worker.reply(actionsReady()); await loading;
  const file = sourceFile(), importing = value.backend.addScan({ file, generation: 0, token: 'empty' }); await settle();
  let request = worker.calls.at(-1), result = candidate(request, { identity: { b: 2, a: 1 } });
  sourceTransfer(worker, request, result); sourcePromotion(worker, request); await importing;
  const warnings = [{ id: 'point-2', old_name: 'Point', new_name: 'Point point', kind: 'feature' }];
  const opening = value.backend.openActions({ recipe: { nodes: [] }, generation: 1, token: 'source', confirmedDiscard: true });
  await settle(); request = worker.calls.at(-1);
  assert.equal(Object.hasOwn(request.payload, 'file'), false);
  assert.equal(Object.hasOwn(request.payload, 'source'), false);
  result = actionsCandidate(request, { generation: 2, token: 'opened', identity: { a: 1, b: 2 }, warnings });
  sourceTransfer(worker, request, result); sourcePromotion(worker, request, 2);
  const accepted = await opening; accepted.metadata.import_warnings[0].new_name = 'Caller changed its copy';
  const restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(actionsReady({ id: worker.calls[0].id })); await settle(); request = worker.calls.at(-1);
  assert.equal(request.payload.kind, 'scan'); assert.equal(request.payload.file, file);
  assert.equal(request.payload.recovery.generation, 2);
  assert.deepEqual(request.payload.recovery.import_warnings, warnings);
  const recovered = candidate(request, { generation: 2, token: 'opened', identity: { a: 1, b: 2 } });
  recovered.metadata.import_warnings = structuredClone(warnings);
  sourceTransfer(worker, request, recovered);
  sourcePromotion(worker, request, 2); await restoring;
  value.backend.close();
});

test('empty actions advance epoch without upload and clear previous File from Force checkpoint', async () => {
  const value = fleet({ initialMode: 'empty', sourceActions: true }), loading = value.backend.initialize();
  let worker = value.workers[0]; worker.reply(actionsReady()); await loading;
  const importing = value.backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }); await settle();
  let request = worker.calls.at(-1); sourceTransfer(worker, request, candidate(request)); sourcePromotion(worker, request); await importing;
  const opening = value.backend.openActions({ recipe: { nodes: [] }, generation: 1, token: 'source', confirmedDiscard: true });
  await settle(); request = worker.calls.at(-1);
  sourceTransfer(worker, request, actionsCandidate(request, { generation: 2, kind: 'empty', sourceKind: 'empty', token: 'empty', identity: {} }));
  sourcePromotion(worker, request, 2); assert.equal((await opening).generation, 2);
  const restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(actionsReady({ id: worker.calls[0].id })); await settle(); request = worker.calls.at(-1);
  assert.equal(request.payload.kind, 'empty'); assert.equal(request.payload.file, undefined);
  sourceTransfer(worker, request, candidate(request, { generation: 2, kind: 'empty', token: 'empty', identity: {} }));
  sourcePromotion(worker, request, 2); await restoring; value.backend.close();
});

test('matching legacy actions preserve actual example identity without manufacturing a generic File', async () => {
  const identity = { source_sha256: 'legacy' };
  const { backend, worker } = await actionsFixture({}, { source_mode: { kind: 'example', example: 'nozzle' },
    source_identity: identity, graph: sourceGraph(0, 'example', [{ id: 'source', operation: 'source' }]) });
  const opening = backend.openActions({ recipe: { nodes: [] }, generation: 0, token: 'example', confirmedDiscard: true });
  await settle(); const request = worker.calls.at(-1);
  sourceTransfer(worker, request, actionsCandidate(request, { sourceKind: 'legacy', kind: 'example', identity }));
  sourcePromotion(worker, request); assert.equal((await opening).source_mode.example, 'nozzle'); backend.close();
});

test('malformed action candidates fail closed before adopting metadata or source authority', async () => {
  for (const corrupt of [value => { delete value.purpose; }, value => { value.sourceKind = 'other'; },
    value => { value.uploadRequired = 1; }, value => { value.uploadRequired = false; },
    value => { value.metadata.import_warnings = null; }, value => { value.import_warning_count = 1; },
    value => { value.sourceKind = 'empty'; }]) {
    const { backend, worker } = await actionsFixture();
    const opening = backend.openActions({ recipe: {}, file: sourceFile(), generation: 0, token: 'empty', confirmedDiscard: true });
    await settle(); const request = worker.calls.at(-1), result = actionsCandidate(request); corrupt(result);
    sourceTransfer(worker, request, result);
    await assert.rejects(opening, error => error.code === 'worker_failed');
    assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe); backend.close();
  }
});

test('no-upload generic and legacy candidates cannot substitute another accepted source identity', async () => {
  for (const kind of ['generic', 'legacy']) {
    const initial = kind === 'legacy' ? { source_mode: { kind: 'example', example: 'nozzle' },
      source_identity: { source_sha256: 'legacy' }, graph: sourceGraph(0, 'source', [{ id: 'source', operation: 'source' }]) } : {};
    const { backend, worker } = await actionsFixture({}, initial);
    if (kind === 'generic') {
      const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }); await settle();
      const request = worker.calls.at(-1); sourceTransfer(worker, request, candidate(request));
      sourcePromotion(worker, request); await importing;
    }
    const accepted = await backend.exportRecipe(), cursor = kind === 'generic' ? 1 : 0;
    const opening = backend.openActions({ recipe: {}, generation: cursor, token: 'source', confirmedDiscard: true });
    await settle(); const request = worker.calls.at(-1);
    sourceTransfer(worker, request, actionsCandidate(request, { generation: cursor + 1, sourceKind: kind,
      kind: kind === 'generic' ? 'scan' : 'example', identity: { source_sha256: 'substituted' } }));
    await assert.rejects(opening, error => error.code === 'worker_failed');
    assert.deepEqual(await backend.exportRecipe(), accepted); backend.close();
  }
});

test('Force rejects changed accepted warning/source metadata and retains authority for a later explicit retry', async () => {
  for (const fault of ['warnings', 'identity']) {
    const value = fleet({ initialMode: 'empty', sourceActions: true }), loading = value.backend.initialize();
    let worker = value.workers[0]; worker.reply(actionsReady()); await loading;
    const file = sourceFile(), warnings = [{ id: 'point-2', old_name: 'Point', new_name: 'Point point', kind: 'feature' }];
    const opening = value.backend.openActions({ recipe: {}, file, generation: 0, token: 'empty', confirmedDiscard: true });
    await settle(); let request = worker.calls.at(-1), accepted = actionsCandidate(request, { warnings });
    sourceTransfer(worker, request, accepted); sourcePromotion(worker, request); await opening;
    const failed = value.backend.forceRestart(); worker = value.workers.at(-1);
    worker.reply(actionsReady({ id: worker.calls[0].id })); await settle(); request = worker.calls.at(-1);
    const incorrect = candidate(request);
    incorrect.metadata.import_warnings = fault === 'warnings' ? [] : structuredClone(warnings);
    if (fault === 'identity') incorrect.source_identity = { source_sha256: 'substituted' };
    sourceTransfer(worker, request, incorrect);
    await assert.rejects(failed, error => error.code === 'worker_failed');
    assert.deepEqual(await value.backend.exportRecipe(), accepted.graph.recipe);
    const retry = value.backend.forceRestart(); worker = value.workers.at(-1);
    worker.reply(actionsReady({ id: worker.calls[0].id })); await settle(); request = worker.calls.at(-1);
    assert.equal(request.payload.file, file);
    assert.deepEqual(request.payload.recovery.import_warnings, warnings);
    const correct = candidate(request); correct.metadata.import_warnings = structuredClone(warnings);
    sourceTransfer(worker, request, correct); sourcePromotion(worker, request); await retry;
    value.backend.close();
  }
});

test('actions terminal history remains bounded and contains no raw recipe warnings or File payload', async () => {
  const { backend, worker } = await actionsFixture(); let first, last;
  for (let cursor = 0; cursor < 65; cursor++) {
    const opening = backend.openActions({ recipe: { nodes: [] }, generation: cursor, token: 'empty', confirmedDiscard: true });
    await settle(); const request = worker.calls.at(-1);
    first ??= request.payload.operationId; last = request.payload.operationId;
    sourceTransfer(worker, request, actionsCandidate(request, { generation: cursor + 1, kind: 'empty', sourceKind: 'empty',
      token: 'empty', identity: {} }));
    sourcePromotion(worker, request, cursor + 1); await opening;
  }
  await assert.rejects(backend.getSourceOperation({ operationId: first }), error => error.code === 'unknown_operation');
  const record = await backend.getSourceOperation({ operationId: last });
  assert.equal(record.acceptedGeneration, 65); assert.equal(record.uploadRequired, false);
  assert.equal(record.import_warning_count, 0); assert.equal(record.purpose, 'open-actions');
  assert(Object.values(record).every(value => ['string', 'number', 'boolean'].includes(typeof value)));
  backend.close();
});

test('preflight rejection and preadoption Cancel keep old project while late Cancel after adoption cannot reverse actions', async () => {
  for (const stage of ['preflight', 'cancel-before', 'cancel-after']) {
    const { backend, worker } = await actionsFixture(), records = [];
    const opening = backend.openActions({ recipe: {}, file: sourceFile(), generation: 0, token: 'empty', confirmedDiscard: true },
      { onProgress: record => records.push(record) }); await settle();
    const request = worker.calls.at(-1), result = actionsCandidate(request);
    if (stage === 'preflight') {
      worker.reply({ id: request.id, ok: false, error: { code: 'source_mismatch', message: 'Declared byte length differs' } });
      await assert.rejects(opening, error => error.code === 'source_mismatch');
    } else if (stage === 'cancel-before') {
      sourceTransfer(worker, request, result, { confirm: false });
      await backend.cancelSourceOperation(records[0]);
      worker.reply({ id: request.id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
      assert.equal(worker.calls.at(-1).accepted, false);
      worker.reply({ id: request.id, ok: false, error: { code: 'source_cancelled', message: 'Cancelled' } });
      await assert.rejects(opening, error => error.code === 'source_cancelled');
    } else {
      sourceTransfer(worker, request, result);
      assert.equal((await backend.cancelSourceOperation(records[0])).status, 'accepted');
      assert(!worker.calls.some(call => call.operation === 'cancel-source'));
      sourcePromotion(worker, request); await opening;
    }
    assert.deepEqual(await backend.exportRecipe(), stage === 'cancel-after' ? result.graph.recipe : sourceGraph().recipe);
    backend.close();
  }
});

test('Force before and after actions adoption retains the correct File recipe warnings and epoch despite lost ack', async () => {
  for (const adopted of [false, true]) {
    const value = fleet({ initialMode: 'empty', sourceActions: true }), loading = value.backend.initialize();
    let worker = value.workers[0]; worker.reply(actionsReady()); await loading;
    const originalFile = sourceFile(), importing = value.backend.addScan({ file: originalFile, generation: 0, token: 'empty' });
    await settle(); let request = worker.calls.at(-1), original = candidate(request);
    sourceTransfer(worker, request, original); sourcePromotion(worker, request); await importing;
    const newFile = sourceFile(), warnings = [{ id: 'point-2', old_name: 'Point', new_name: 'Point point', kind: 'feature' }];
    const opening = value.backend.openActions({ recipe: {}, file: newFile, generation: 1, token: 'source', confirmedDiscard: true });
    await settle(); request = worker.calls.at(-1);
    const result = actionsCandidate(request, { generation: 2, token: 'opened', identity: { source_sha256: 'new' }, warnings });
    sourceTransfer(worker, request, result, { confirm: adopted });
    const interrupted = assert.rejects(opening, error => error.code === (adopted ? 'accepted_workspace_unavailable' : 'computation_cancelled'));
    const old = worker, restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
    sourcePromotion(old, request, 2);
    worker.reply(actionsReady({ id: worker.calls[0].id })); await settle(); const restore = worker.calls.at(-1);
    assert.equal(restore.payload.file, adopted ? newFile : originalFile);
    assert.equal(restore.payload.recovery.generation, adopted ? 2 : 1);
    assert.deepEqual(restore.payload.recovery.recipe, adopted ? result.graph.recipe : original.graph.recipe);
    assert.deepEqual(restore.payload.recovery.import_warnings, adopted ? warnings : []);
    const recovered = candidate(restore, { generation: adopted ? 2 : 1, token: adopted ? 'opened' : 'source',
      identity: adopted ? result.source_identity : original.source_identity });
    recovered.metadata.import_warnings = structuredClone(adopted ? warnings : []);
    sourceTransfer(worker, restore, recovered);
    sourcePromotion(worker, restore, adopted ? 2 : 1); await restoring; await interrupted;
    value.backend.close();
  }
});

test('source candidate adopts only after complete transfer confirmation and resolves after promotion', async () => {
  const { backend, worker } = await sourceFixture(), progress = [], file = sourceFile();
  const importing = backend.addScan({ file, generation: 0, token: 'empty' }, { onProgress: p => progress.push(p) });
  await settle();
  const request = worker.calls.at(-1), result = candidate(request);
  assert.equal(request.operation, 'prepare-source');
  assert.equal(request.payload.file, file);
  assert.equal(request.workspaceGeneration, 0);
  sourceTransfer(worker, request, result, { confirm: false });
  assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe);
  assert.equal(worker.calls.at(-1), request);
  worker.reply({ id: request.id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
  assert.deepEqual(await backend.exportRecipe(), result.graph.recipe);
  assert.deepEqual(worker.calls.at(-1), { operation: 'accept-source-boundary', id: request.id,
    generation: request.generation, operationId: request.payload.operationId, workspaceGeneration: 1, accepted: true });
  const status = await backend.getSourceOperation(progress[0]);
  assert.equal(status.status, 'accepted');
  assert.equal(status.acceptedGeneration, 1);
  await assert.rejects(backend.getGraph(), error => error.code === 'source_busy');
  let resolved = false;
  importing.then(() => { resolved = true; });
  await settle();
  assert.equal(resolved, false);
  sourcePromotion(worker, request);
  assert.equal((await importing).generation, 1);
  assert.equal(resolved, true);
  assert.deepEqual(progress.map(p => p.status), ['reserved', 'accepted']);
  const reading = backend.getGraph(); await settle();
  assert.equal(worker.calls.at(-1).workspaceGeneration, 1);
  worker.reply({ id: worker.calls.at(-1).id, ok: true, result: result.graph });
  await reading;
  backend.close();
});

test('source preflight uses worker-advertised limits and one slot survives observer Abort', async () => {
  const { backend, worker } = await sourceFixture(), controller = new AbortController();
  await assert.rejects(backend.addScan({ file: sourceFile(1025), generation: 0, token: 'empty' }),
    error => error.code === 'source_limit');
  await assert.rejects(backend.addScan({ file: sourceFile(), generation: 9, token: 'empty' }),
    error => error.code === 'observation_superseded');
  assert.equal(worker.calls.length, 1);
  const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }, { signal: controller.signal });
  await settle();
  const request = worker.calls.at(-1), result = candidate(request);
  controller.abort(new Error('Stopped observing'));
  await assert.rejects(importing, /Stopped observing/);
  await assert.rejects(backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }), error => error.code === 'source_busy');
  sourceTransfer(worker, request, result);
  sourcePromotion(worker, request);
  assert.deepEqual(await backend.exportRecipe(), result.graph.recipe);
  assert.equal((await backend.getSourceOperation({ operationId: request.payload.operationId })).status, 'accepted');
  backend.close();
});

test('Cancel after private commit and before confirmation rejects candidate without adopting its source', async () => {
  const { backend, worker } = await sourceFixture(), progress = [];
  const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }, { onProgress: p => progress.push(p) });
  await settle();
  const request = worker.calls.at(-1);
  sourceTransfer(worker, request, candidate(request), { confirm: false });
  await assert.rejects(backend.cancelSourceOperation({ ...progress[0], token: 'wrong' }), error => error.code === 'stale_source');
  assert.equal((await backend.cancelSourceOperation(progress[0])).status, 'cancelling');
  worker.reply({ id: request.id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
  assert.equal(worker.calls.at(-1).accepted, false);
  worker.reply({ id: request.id, ok: false, error: { code: 'source_cancelled', message: 'Cancelled safely' } });
  await assert.rejects(importing, error => error.code === 'source_cancelled');
  assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe);
  assert.equal((await backend.getSourceOperation(progress[0])).status, 'cancelled');
  backend.close();
});

test('reentrant Cancel after adoption observes accepted source and never sends cancellation', async () => {
  const { backend, worker } = await sourceFixture();
  let cancellation;
  const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }, {
    onProgress: record => { if (record.status === 'accepted') cancellation = backend.cancelSourceOperation(record); },
  });
  await settle();
  const request = worker.calls.at(-1);
  sourceTransfer(worker, request, candidate(request));
  assert.equal((await cancellation).status, 'accepted');
  assert(!worker.calls.some(call => call.operation === 'cancel-source'));
  sourcePromotion(worker, request);
  await importing;
  backend.close();
});

test('malformed source buffers or mismatched generation retire runtime without corrupting accepted recipe', async () => {
  for (const corrupt of [result => { result.metadata.project_generation = 8; },
    result => { result.positions = new ArrayBuffer(12); },
    result => { result.operationId = 'foreign'; }]) {
    const { backend, worker } = await sourceFixture();
    const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' });
    await settle();
    const request = worker.calls.at(-1), result = candidate(request);
    corrupt(result);
    sourceTransfer(worker, request, result);
    await assert.rejects(importing, error => error.code === 'worker_failed');
    assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe);
    assert.equal(worker.terminated, 1);
    backend.close();
  }
});

test('accepted promotion delivery failure keeps accepted record and downloadable recipe', async () => {
  const { backend, worker } = await sourceFixture(), records = [];
  const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }, { onProgress: p => records.push(p) });
  await settle();
  const request = worker.calls.at(-1), result = candidate(request);
  sourceTransfer(worker, request, result);
  worker.onerror({ message: 'Lost promotion acknowledgement' });
  await assert.rejects(importing, error => error.code === 'accepted_workspace_unavailable'
    && error.acceptedOperation.acceptedGeneration === 1);
  assert.equal((await backend.getSourceOperation(records[0])).status, 'accepted');
  assert.deepEqual(await backend.exportRecipe(), result.graph.recipe);
  backend.close();
});

test('reset uses confirmed candidate publication and empty ABA keeps source generations distinct', async () => {
  const { backend, worker } = await sourceFixture();
  await assert.rejects(backend.resetProject({ generation: 0, token: 'empty' }), error => error.code === 'confirmation_required');
  const resetting = backend.resetProject({ generation: 0, token: 'empty', confirmedDiscard: true });
  await settle();
  const request = worker.calls.at(-1);
  sourceTransfer(worker, request, candidate(request, { kind: 'empty', token: 'empty' }));
  sourcePromotion(worker, request);
  assert.equal((await resetting).generation, 1);
  await assert.rejects(backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }),
    error => error.code === 'observation_superseded');
  backend.close();
});

test('source failures retain existing checkpoint and progress uses immutable bounded records', async () => {
  const { backend, worker } = await sourceFixture(), records = [];
  const importing = backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }, { onProgress: record => {
    records.push(record); record.status = 'mutated-by-observer';
  } });
  await settle();
  const request = worker.calls.at(-1);
  worker.reply({ kind: 'source-progress', generation: request.generation, operationId: request.payload.operationId,
    progress: { phase: 'reading', completed: 10, total: 30, unit: 'bytes' } });
  worker.reply({ id: request.id, ok: false, error: { code: 'unsupported_source', message: 'Normals required' } });
  await assert.rejects(importing, error => error.code === 'unsupported_source');
  const status = await backend.getSourceOperation({ operationId: request.payload.operationId });
  assert.equal(status.status, 'failed');
  assert.equal(status.error.code, 'unsupported_source');
  assert.deepEqual(await backend.exportRecipe(), sourceGraph().recipe);
  assert.equal(records.length, 3);
  backend.close();
});

test('Force before versus after source adoption reconstructs the correct accepted File and recipe', async () => {
  for (const adopted of [false, true]) {
    const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
    let worker = value.workers[0];
    worker.reply(sourceReady()); await loading;
    const file = sourceFile(), importing = value.backend.addScan({ file, generation: 0, token: 'empty' });
    await settle();
    const request = worker.calls.at(-1), result = candidate(request);
    sourceTransfer(worker, request, result, { confirm: adopted });
    const interrupted = assert.rejects(importing, error => error.code === (adopted ? 'accepted_workspace_unavailable' : 'computation_cancelled'));
    const restoring = value.backend.forceRestart();
    worker = value.workers.at(-1);
    worker.reply(sourceReady({ id: worker.calls[0].id }));
    await settle();
    const restore = worker.calls.at(-1);
    assert.equal(restore.operation, 'prepare-source');
    assert.equal(restore.payload.kind, adopted ? 'scan' : 'empty');
    assert.equal(restore.payload.file, adopted ? file : undefined);
    assert.equal(restore.payload.recovery.generation, adopted ? 1 : 0);
    assert.deepEqual(restore.payload.recovery.recipe, adopted ? result.graph.recipe : sourceGraph().recipe);
    sourceTransfer(worker, restore, candidate(restore, { generation: adopted ? 1 : 0,
      kind: adopted ? 'scan' : 'empty', token: adopted ? 'source' : 'empty', identity: adopted ? result.source_identity : {} }));
    sourcePromotion(worker, restore, adopted ? 1 : 0);
    assert.equal((await restoring).project_generation, adopted ? 1 : 0);
    await interrupted;
    value.backend.close();
  }
});

test('failed imported Force recovery retains original File authority for a later retry', async () => {
  const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
  let worker = value.workers[0]; worker.reply(sourceReady()); await loading;
  const file = sourceFile(), importing = value.backend.addScan({ file, generation: 0, token: 'empty' });
  await settle();
  const request = worker.calls.at(-1), result = candidate(request);
  sourceTransfer(worker, request, result); sourcePromotion(worker, request); await importing;
  const failed = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(sourceReady({ id: worker.calls[0].id })); await settle();
  const attempt = worker.calls.at(-1);
  worker.reply({ id: attempt.id, ok: false, error: { code: 'recovery_source_mismatch', message: 'File bytes changed' } });
  await assert.rejects(failed, error => error.code === 'recovery_source_mismatch');
  assert.deepEqual(await value.backend.exportRecipe(), result.graph.recipe);
  const retry = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(sourceReady({ id: worker.calls[0].id })); await settle();
  const next = worker.calls.at(-1);
  assert.equal(next.payload.file, file);
  sourceTransfer(worker, next, candidate(next)); sourcePromotion(worker, next);
  await retry;
  value.backend.close();
});

test('old readiness explicitly rejects new source methods without posting bulk work', async () => {
  const { backend, worker } = await initialized();
  await assert.rejects(backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }),
    error => error.code === 'unsupported_capability');
  await assert.rejects(backend.resetProject({ generation: 0, token: 'empty', confirmedDiscard: true }),
    error => error.code === 'unsupported_capability');
  for (const method of ['getSourceOperation', 'cancelSourceOperation'])
    await assert.rejects(backend[method]({ operationId: 'unknown' }), error => error.code === 'unsupported_capability');
  assert.equal(worker.calls.length, 1);
  backend.close();
});

test('Force after initial startup failure establishes the empty source cursor for subsequent import', async () => {
  const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
  value.workers[0].reply({ id: 1, ok: false, error: 'Startup failed' });
  await assert.rejects(loading, /Startup failed/);
  const restoring = value.backend.forceRestart(), worker = value.workers.at(-1);
  worker.reply(sourceReady({ id: worker.calls[0].id }));
  assert.equal((await restoring).project_generation, 0);
  const importing = value.backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' });
  await settle();
  const request = worker.calls.at(-1);
  assert.equal(request.operation, 'prepare-source');
  sourceTransfer(worker, request, candidate(request)); sourcePromotion(worker, request);
  assert.equal((await importing).generation, 1);
  value.backend.close();
});

test('malformed source readiness settles initialization and retires the worker', async () => {
  for (const corrupt of [value => { value.worker_protocol.version = 4; },
    value => { value.source_import = { ...sourceProfile, limits: { ...sourceProfile.limits, chunk_bytes: 0 } }; }]) {
    const { backend, worker } = fixture(), loading = backend.initialize(), value = sourceReady();
    corrupt(value); worker.reply(value);
    await assert.rejects(loading, error => error.code === 'worker_startup_failed');
    assert.equal(worker.terminated, 1);
    backend.close();
  }
});

test('example transition preserves caller token and generation rather than renewing stale discard authority', async () => {
  const { backend, worker } = await sourceFixture();
  for (const payload of [{ example: 'nozzle', token: 'obsolete' },
    { example: 'nozzle', token: 'empty', generation: 8 }])
    await assert.rejects(backend.selectExample(payload), error => error.code === 'observation_superseded');
  assert.equal(worker.calls.length, 1);
  const selecting = backend.selectExample({ example: 'nozzle', token: 'empty' }); await settle();
  const request = worker.calls.at(-1);
  assert.equal(request.payload.token, 'empty');
  assert.equal(request.payload.generation, 0);
  sourceTransfer(worker, request, candidate(request, { kind: 'example' })); sourcePromotion(worker, request);
  assert.equal((await selecting).active, 'nozzle');
  backend.close();
});

test('cleanup failure takes precedence over Cancel, stays queryable, and requires explicit Force', async () => {
  const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
  let worker = value.workers[0]; worker.reply(sourceReady()); await loading;
  const importing = value.backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }); await settle();
  const request = worker.calls.at(-1), identity = { operationId: request.payload.operationId, generation: 0, token: 'empty' };
  await value.backend.cancelSourceOperation(identity);
  worker.reply({ id: request.id, ok: false, error: { code: 'source_cleanup_failed', status: 500, message: 'Unlink failed' } });
  worker.reply({ fatal: 'Source staging cleanup failed. Use Force and restart.' });
  await assert.rejects(importing, error => error.code === 'source_cleanup_failed');
  const record = await value.backend.getSourceOperation(identity);
  assert.equal(record.status, 'failed');
  assert.equal(record.error.code, 'source_cleanup_failed');
  assert.deepEqual(await value.backend.exportRecipe(), sourceGraph().recipe);
  await assert.rejects(value.backend.addScan({ file: sourceFile(), generation: 0, token: 'empty' }),
    error => error.code === 'worker_failed');
  assert.equal(value.workers.length, 1);
  const restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(sourceReady({ id: worker.calls[0].id })); await settle();
  const restore = worker.calls.at(-1);
  assert.equal(restore.payload.kind, 'empty');
  sourceTransfer(worker, restore, candidate(restore, { kind: 'empty', generation: 0, token: 'empty' }));
  sourcePromotion(worker, restore, 0); await restoring;
  value.backend.close();
});

test('source operation history expires old primitive records after 64 terminal transitions', async () => {
  const { backend, worker } = await sourceFixture();
  let first, last;
  for (let cursor = 0; cursor < 65; cursor++) {
    const resetting = backend.resetProject({ generation: cursor, token: 'empty', confirmedDiscard: true });
    await settle();
    const request = worker.calls.at(-1);
    first ??= request.payload.operationId;
    last = request.payload.operationId;
    sourceTransfer(worker, request, candidate(request, { generation: cursor + 1, kind: 'empty', token: 'empty' }));
    sourcePromotion(worker, request, cursor + 1);
    await resetting;
  }
  await assert.rejects(backend.getSourceOperation({ operationId: first }), error => error.code === 'unknown_operation');
  const status = await backend.getSourceOperation({ operationId: last });
  assert.equal(status.status, 'accepted');
  assert.equal(status.acceptedGeneration, 65);
  assert(!Object.values(status).some(value => value instanceof ArrayBuffer || value instanceof Blob));
  backend.close();
});

test('ordinary imported computation Cancel keeps source epoch and File for subsequent Force replay', async () => {
  const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
  let worker = value.workers[0]; worker.reply(sourceReady()); await loading;
  const file = sourceFile(), importing = value.backend.addScan({ file, generation: 0, token: 'empty' });
  await settle();
  const request = worker.calls.at(-1), source = candidate(request);
  sourceTransfer(worker, request, source); sourcePromotion(worker, request); await importing;
  const recipe = { ...source.graph.recipe, nodes: [...source.graph.recipe.nodes,
    { id: 'selection', operation: 'selection', source: 'source', ids: [0, 1, 2] }] };
  const edited = sourceGraph(1, 'edited', recipe.nodes);
  const editing = value.backend.replaceRecipe({ token: 'source', recipe }); await settle();
  worker.reply({ id: worker.calls.at(-1).id, ok: true, result: edited }); await editing;
  const evaluating = value.backend.evaluateGraph({ token: 'edited' }); await settle();
  const interrupted = assert.rejects(evaluating, error => error.code === 'computation_cancelled');
  const cancelling = value.backend.cancelComputation(); await settle();
  const cancel = worker.calls.at(-1);
  assert.equal(cancel.operation, 'cancel');
  assert.equal(cancel.workspaceGeneration, 1);
  assert.deepEqual(cancel.payload.recipe, edited.recipe);
  worker.reply({ id: cancel.id, ok: true, result: edited });
  await cancelling; await interrupted;
  const restoring = value.backend.forceRestart(); worker = value.workers.at(-1);
  worker.reply(sourceReady({ id: worker.calls[0].id })); await settle();
  const restore = worker.calls.at(-1);
  assert.equal(restore.payload.file, file);
  assert.deepEqual(restore.payload.recovery.recipe, edited.recipe);
  sourceTransfer(worker, restore, candidate(restore, { token: 'edited', nodes: edited.recipe.nodes }));
  sourcePromotion(worker, restore); await restoring;
  value.backend.close();
});

test('reentrant Force from accepted progress keeps new File while late old-worker confirmations are fenced', async () => {
  const value = fleet({ initialMode: 'empty' }), loading = value.backend.initialize();
  const old = value.workers[0]; old.reply(sourceReady()); await loading;
  const file = sourceFile(); let restoring;
  const importing = value.backend.addScan({ file, generation: 0, token: 'empty' }, {
    onProgress: record => { if (record.status === 'accepted') restoring = value.backend.forceRestart(); },
  });
  const interrupted = assert.rejects(importing, error => error.code === 'accepted_workspace_unavailable');
  await settle();
  const request = old.calls.at(-1), source = candidate(request);
  sourceTransfer(old, request, source);
  const fresh = value.workers.at(-1);
  sourcePromotion(old, request);
  old.reply({ kind: 'graph', generation: request.generation, state: sourceGraph(1, 'stale', []) });
  fresh.reply(sourceReady({ id: fresh.calls[0].id })); await settle();
  const restore = fresh.calls.at(-1);
  assert.equal(restore.payload.file, file);
  sourceTransfer(fresh, restore, candidate(restore)); sourcePromotion(fresh, restore);
  await restoring; await interrupted;
  assert.deepEqual(await value.backend.exportRecipe(), source.graph.recipe);
  value.backend.close();
});

function fleet(options = {}) {
  const workers = [], events = [];
  const backend = createWorkerBackend({ runtimeURL: './runtime/test/worker.mjs',
    onLifecycle: state => events.push(state), workerFactory: () => {
      const worker = { calls: [], terminated: 0,
        postMessage(message) { this.calls.push(message); },
        terminate() { this.terminated++; }, reply(data) { this.onmessage({ data }); } };
      workers.push(worker);
      return worker;
    }, ...options });
  return { backend, workers, events };
}

test('Cancel immediately settles queued work and observers, fences binary and late graph replies, and restores before reuse', async () => {
  const value = await initialized({ cad: true });
  await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
  const stream = value.backend.graphUpdates();
  await stream.next();
  const observer = stream.next(), zip = value.backend.exportCad(), queued = value.backend.listExamples();
  await tick();
  const old = value.worker.calls.slice(-2), zipId = old[0].id;
  value.worker.reply({ id: zipId, ok: true, result: new ArrayBuffer(20) });
  const restored = value.backend.cancelComputation();
  assert.equal(value.backend.cancelComputation(), restored);
  assert.equal(value.backend.getLifecycle().state, 'cancel-requested');
  for (const promise of [observer, zip, queued]) await assert.rejects(promise, error => error.code === 'computation_cancelled');
  value.worker.reply({ kind: 'graph', generation: 0, state: graph({ revision: 99 }) });
  value.worker.reply({ id: zipId, kind: 'transfer-confirmed', export_bytes_after: 0 });
  const after = value.backend.getGraph();
  await settle();
  const cancel = value.worker.calls.at(-1);
  assert.equal(cancel.operation, 'cancel');
  const clean = graph({ revision: 1 });
  value.worker.reply({ id: cancel.id, ok: true, result: clean });
  assert.deepEqual(await restored, clean);
  await settle();
  const read = value.worker.calls.at(-1);
  assert.equal(read.operation, 'getGraph');
  value.worker.reply({ id: read.id, ok: true, result: clean });
  await after;
  assert.equal(value.worker.terminated, 0);
  value.backend.close();
});

test('cancelled mutations do not overwrite the last accepted recipe and idle Cancel changes nothing', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  const calls = value.worker.calls.length;
  assert.deepEqual(await value.backend.cancelComputation(), graph());
  assert.equal(value.worker.calls.length, calls);
  const change = value.backend.replaceRecipe({ token: 'token', recipe: { nodes: [] } });
  await tick();
  const mutation = value.worker.calls.at(-1), stop = value.backend.cancelComputation();
  await assert.rejects(change, error => error.code === 'computation_cancelled');
  value.worker.reply({ id: mutation.id, ok: true, result: graph({ recipe: { nodes: [] } }) });
  assert.deepEqual(await value.backend.exportRecipe(), graph().recipe);
  const cancel = value.worker.calls.at(-1);
  value.worker.reply({ id: cancel.id, ok: true, result: graph() });
  await stop;
  value.backend.close();
});

test('safe-boundary acknowledgement captures accepted work and rejects cancellation-fenced work', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  const accepted = value.worker.calls.at(-1);
  value.worker.reply({ kind: 'lifecycle', id: accepted.id, generation: 0,
    operation: 'getGraph', state: 'complete', boundary: true });
  assert.deepEqual(value.worker.calls.at(-1), { operation: 'accept-boundary',
    id: accepted.id, generation: 0, accepted: true });
  const work = value.backend.getGraph();
  await tick();
  const request = value.worker.calls.at(-1), stop = value.backend.cancelComputation();
  await assert.rejects(work, error => error.code === 'computation_cancelled');
  const cancel = value.worker.calls.at(-1);
  value.worker.reply({ kind: 'lifecycle', id: request.id, generation: 0,
    operation: 'getGraph', state: 'complete', boundary: true });
  assert.equal(value.worker.calls.at(-1).accepted, false);
  value.worker.reply({ id: cancel.id, ok: true, result: graph() });
  await stop;
  value.backend.close();
});

test('Force restores accepted recipe into the same backend, rejects prior recovery, and ignores every retired-worker callback', async () => {
  const value = fleet(), start = value.backend.initialize(), first = value.workers[0];
  first.reply({ ...ready, graph: graph(), source_identity: { sha: 'immutable' } });
  await start;
  const source = await value.backend.exportRecipe();
  const restarting = value.backend.forceRestart(), second = value.workers[1];
  const again = value.backend.forceRestart(), third = value.workers[2];
  await assert.rejects(restarting, error => error.code === 'computation_cancelled');
  first.reply({ fatal: 'retired failure' });
  second.reply({ ...ready, id: third.calls[0].id, graph: graph({ recipe: { nodes: [] } }) });
  first.onerror({ message: 'old crash' });
  first.onmessageerror();
  assert.equal(value.backend.getLifecycle().state, 'recovering');
  third.reply({ ...ready, id: third.calls[0].id, graph: graph({ token: 'fresh' }), source_identity: { sha: 'immutable' } });
  await settle();
  const replay = third.calls.at(-1);
  assert.equal(replay.operation, 'replaceRecipe');
  assert.deepEqual(replay.payload.recipe, source);
  assert.equal(replay.payload.token, 'fresh');
  assert.deepEqual(await value.backend.exportRecipe(), source);
  third.reply({ id: replay.id, ok: true, result: graph({ revision: 1 }) });
  assert.equal((await again).revision, 1);
  assert.equal(first.terminated, 1);
  assert.equal(second.terminated, 1);
  assert.equal(third.terminated, 0);
  value.backend.close();
});

test('startup failure retries explicitly and recovery source mismatch keeps the downloadable recipe', async () => {
  const value = fleet(), first = value.backend.initialize();
  value.workers[0].reply({ id: 1, ok: false, error: 'missing' });
  await assert.rejects(first, /missing/);
  const retry = value.backend.forceRestart(), worker = value.workers[1];
  worker.reply({ ...ready, id: worker.calls[0].id, graph: graph(), source_identity: { sha: 'original' } });
  await retry;
  const mismatch = value.backend.forceRestart(), changed = value.workers[2];
  changed.reply({ ...ready, id: changed.calls[0].id, graph: graph({ recipe: { nodes: [] } }), source_identity: { sha: 'changed' } });
  await assert.rejects(mismatch, error => error.code === 'recovery_source_mismatch');
  assert.deepEqual(await value.backend.exportRecipe(), graph().recipe);
  assert.equal(changed.terminated, 1);
  value.backend.close();
});

test('optional isolation interrupts only explicitly interruptible work; CAD cancellation remains cooperative', async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crossOriginIsolated');
  Object.defineProperty(globalThis, 'crossOriginIsolated', { configurable: true, value: true });
  try {
    for (const interruptible of [true, false]) {
      const value = fixture(), starting = value.backend.initialize();
      const buffer = value.worker.calls[0].interruptBuffer;
      assert(buffer.buffer instanceof SharedArrayBuffer);
      value.worker.reply({ ...ready, graph: graph({ evaluation_running: true }) });
      await starting;
      value.worker.reply({ kind: 'lifecycle', generation: 0, state: 'running', operation: 'evaluateGraph', interruptible });
      const stop = value.backend.cancelComputation();
      assert.equal(Atomics.load(buffer, 0), interruptible ? 2 : 0);
      const request = value.worker.calls.at(-1);
      value.worker.reply({ id: request.id, ok: true, result: graph() });
      await stop;
      value.backend.close();
    }
  } finally {
    if (original) Object.defineProperty(globalThis, 'crossOriginIsolated', original);
    else delete globalThis.crossOriginIsolated;
  }
});

test('queued requests remain bounded even after callers abort their observations', async () => {
  const value = await initialized();
  const controller = new AbortController(), observations = Array.from({ length: 64 },
    () => value.backend.getGraph({ signal: controller.signal }));
  await settle();
  controller.abort(new Error('stop observing'));
  for (const promise of observations) await assert.rejects(promise, /stop observing/);
  await assert.rejects(value.backend.getGraph(), error => error.code === 'worker_queue_full');
  assert.equal(value.worker.terminated, 0);
  value.backend.close();
});
async function requestReply(value, operation, payload, result) {
  const pending = value.backend[operation](payload);
  await tick();
  const request = value.worker.calls.at(-1);
  assert.equal(request.operation, operation);
  value.worker.reply({ id: request.id, ok: true, result });
  return pending;
}

test('workspace waits for actual sender detachment and returns independently owned buffers', async () => {
  const value = await initialized(), pending = value.backend.loadWorkspace();
  await tick();
  const id = value.worker.calls.at(-1).id;
  const positions = new ArrayBuffer(12), indices = new ArrayBuffer(12);
  let settled = false;
  pending.then(() => { settled = true; });
  value.worker.reply({ id, ok: true, result: { metadata: {}, positions, indices } });
  await tick();
  assert.equal(settled, false);
  value.worker.reply({ id, kind: 'transfer-confirmed', positions_bytes_after: 0, indices_bytes_after: 0 });
  const result = await pending;
  assert.equal(result.positions, positions);
  assert.deepEqual(result.transferOwnership, { sender_detached: true, positions_bytes: 12,
    indices_bytes: 12, copied_js_bytes: 24, strategy: 'copy-contiguous-wasm-view-then-transfer' });
  value.backend.close();
});

test('pushed running acknowledgements and completion before subscription cannot miss terminal status', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  await requestReply(value, 'ensureGraph', { token: 'token' },
    { token: 'token', status: 'running', evaluation_running: true });
  const stream = value.backend.graphUpdates();
  assert.equal((await stream.next()).value.evaluation_running, true);
  const next = stream.next();
  value.worker.reply({ kind: 'graph', generation: 0, state: graph({ revision: 4 }) });
  assert.equal((await next).value.revision, 4);
  assert.equal((await stream.next()).done, true);
  const late = value.backend.graphUpdates();
  assert.equal((await late.next()).value.evaluation_running, false);
  assert.equal((await late.next()).done, true);
  value.backend.close();
});

test('recipe-less worker publications retain saved actions and clear transient unchanged status', async () => {
  const value = await initialized();
  const original = graph({ evaluation_running: true, evaluation_error: 'Previous failure',
    recipe: { nodes: [{ id: 'source', operation: 'source' },
      { id: 'selection', vertex_ids: [2, 7, 11], volume: { encoded: 'saved-volume' } }] } });
  await requestReply(value, 'getGraph', {}, original);
  const stream = value.backend.graphUpdates();
  assert.deepEqual((await stream.next()).value.recipe, original.recipe);
  const compact = stream.next();
  value.worker.reply({ kind: 'graph', generation: 0,
    state: { token: 'token', revision: 3, unchanged: true, status: 'running', evaluation_running: true } });
  const compactState = (await compact).value;
  assert.deepEqual(compactState.recipe, original.recipe);
  assert.equal(Object.hasOwn(compactState, 'unchanged'), false);
  const terminal = stream.next();
  value.worker.reply({ kind: 'graph', generation: 0, state: { token: 'token', revision: 4,
    states: { selection: 'ready' }, errors: {}, results: { selection: { count: 3 } }, evaluation_running: false } });
  const terminalState = (await terminal).value;
  assert.deepEqual(terminalState.recipe, original.recipe);
  assert.deepEqual(terminalState.states, { selection: 'ready' });
  assert.equal(Object.hasOwn(terminalState, 'unchanged'), false);
  assert.equal(Object.hasOwn(terminalState, 'evaluation_error'), false);
  assert.equal(Object.hasOwn(terminalState, 'status'), false);
  assert.deepEqual(await value.backend.exportRecipe(), original.recipe);
  assert.equal((await stream.next()).done, true);
  const reading = value.backend.getGraph({ revision: 4 });
  await settle();
  const request = value.worker.calls.at(-1);
  assert.deepEqual(request.payload, { revision: 4 });
  value.worker.reply({ id: request.id, ok: true, result: { token: 'token', revision: 5,
    states: { selection: 'ready' }, errors: {}, evaluation_running: false } });
  assert.deepEqual((await reading).recipe, original.recipe);
  value.backend.close();
});

test('worker recipe polling uses the negotiated protocol without breaking older runtimes', async () => {
  for (const version of [6, 7]) {
    const value = fixture(), loading = value.backend.initialize();
    value.worker.reply({ ...ready, worker_protocol: { version } });
    await loading;
    await requestReply(value, 'getGraph', {}, graph());
    const reading = value.backend.getGraph({ revision: 3 });
    await settle();
    const request = value.worker.calls.at(-1);
    assert.deepEqual(request.payload, version >= 7
      ? { revision: 3, recipe_token: 'token' } : { revision: 3 });
    value.worker.reply({ id: request.id, ok: true,
      result: { token: 'token', revision: 3, unchanged: true, evaluation_running: false } });
    assert.equal((await reading).unchanged, true);
    assert.deepEqual(await value.backend.exportRecipe(), graph().recipe);
    value.backend.close();
  }
});

test('Force recovery retains the full saved recipe after recipe-less worker publications', async () => {
  const value = fleet(), loading = value.backend.initialize(), first = value.workers[0];
  const original = graph({ recipe: { nodes: [{ id: 'source', operation: 'source' },
    { id: 'selection', vertex_ids: [2, 7, 11], volume: { encoded: 'saved-volume' } }] } });
  first.reply({ ...ready, graph: original, source_identity: { sha: 'immutable' } });
  await loading;
  first.reply({ kind: 'graph', generation: 0, state: { token: 'token', revision: 4,
    states: { selection: 'ready' }, evaluation_running: false } });
  first.reply({ kind: 'graph', generation: 0,
    state: { token: 'token', revision: 4, unchanged: true, evaluation_running: false } });
  assert.deepEqual(await value.backend.exportRecipe(), original.recipe);
  const restarting = value.backend.forceRestart(), second = value.workers[1];
  second.reply({ ...ready, id: second.calls[0].id, graph: graph({ token: 'fresh' }),
    source_identity: { sha: 'immutable' } });
  await settle();
  const replay = second.calls.at(-1);
  assert.equal(replay.operation, 'replaceRecipe');
  assert.deepEqual(replay.payload.recipe, original.recipe);
  second.reply({ id: replay.id, ok: true, result: graph({ recipe: original.recipe, revision: 1 }) });
  const recovered = await restarting;
  assert.deepEqual(recovered.recipe, original.recipe);
  assert.equal(Object.hasOwn(recovered, 'unchanged'), false);
  assert.deepEqual(await value.backend.exportRecipe(), original.recipe);
  value.backend.close();
});

test('an observer without a cached snapshot requests its first graph', async () => {
  const value = await initialized(), stream = value.backend.graphUpdates();
  const first = stream.next();
  await tick();
  await tick();
  const request = value.worker.calls.at(-1);
  assert.equal(request.operation, 'getGraph');
  value.worker.reply({ id: request.id, ok: true, result: graph() });
  assert.equal((await first).value.token, 'token');
  assert.equal((await stream.next()).done, true);
  value.backend.close();
});

test('mutation fences obsolete graph publication and wakes superseded observers', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
  const stream = value.backend.graphUpdates();
  await stream.next();
  const observed = stream.next();
  const mutation = value.backend.replaceRecipe({ token: 'token', recipe: {} });
  await tick();
  const request = value.worker.calls.at(-1);
  value.worker.reply({ kind: 'graph', generation: 0, state: graph({ revision: 99 }) });
  await assert.rejects(observed, error => error.code === 'observation_superseded');
  value.worker.reply({ id: request.id, ok: true, result: graph({ token: 'new', revision: 5 }) });
  await mutation;
  assert.equal((await value.backend.graphUpdates().next()).value.revision, 5);
  value.backend.close();
});

test('structured rejected mutations preserve committed recipe and recovery survives runtime loss', async () => {
  const value = await initialized();
  await requestReply(value, 'getGraph', {}, graph());
  const pending = value.backend.importRecipe({ token: 'token', recipe: {} });
  await tick();
  value.worker.reply({ id: value.worker.calls.at(-1).id, ok: false,
    error: { code: 'unsupported_capability', status: 422, message: 'Unsupported CAD action' } });
  await assert.rejects(pending, error => error.code === 'unsupported_capability' && error.status === 422);
  value.worker.reply({ fatal: 'lost' });
  const recovered = await value.backend.exportRecipe();
  assert.deepEqual(recovered, graph().recipe);
  recovered.nodes.length = 0;
  assert.equal((await value.backend.exportRecipe()).nodes.length, 1);
});

test('close and fatal settle concurrent operations and waiting observers', async () => {
  for (const fatal of [false, true]) {
    const value = await initialized();
    await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
    const stream = value.backend.graphUpdates();
    await stream.next();
    const observed = stream.next(), first = value.backend.getGraph(), second = value.backend.listExamples();
    await tick();
    if (fatal) value.worker.reply({ fatal: 'runtime lost' });
    else value.backend.close();
    for (const pending of [observed, first, second])
      await assert.rejects(pending, error => error.code === (fatal ? 'worker_failed' : 'backend_closed'));
    assert.equal(value.worker.terminated, 1);
  }
});

test('aborting observation stops waiting without terminating computation', async () => {
  const value = await initialized(), controller = new AbortController();
  await requestReply(value, 'getGraph', {}, graph({ evaluation_running: true }));
  const stream = value.backend.graphUpdates({ signal: controller.signal });
  await stream.next();
  const observed = stream.next();
  controller.abort(new Error('stop waiting'));
  await assert.rejects(observed, /stop waiting/);
  assert.equal(value.worker.terminated, 0);
  value.backend.close();
});

test('stale pending graph replies cannot publish after a mutation starts', async () => {
  const value = await initialized();
  const read = value.backend.getGraph();
  await tick();
  const old = value.worker.calls.at(-1);
  const change = value.backend.replaceRecipe({ token: 'token', recipe: {} });
  await tick();
  const mutation = value.worker.calls.at(-1);
  value.worker.reply({ id: old.id, ok: true, result: graph() });
  await assert.rejects(read, error => error.code === 'observation_superseded');
  value.worker.reply({ id: mutation.id, ok: true, result: graph({ token: 'new' }) });
  await change;
  value.backend.close();
});

test('abort during shared startup settles only that observation immediately', async () => {
  for (const stream of [false, true]) {
    const controller = new AbortController(), value = fixture();
    const observation = stream ? value.backend.graphUpdates({ signal: controller.signal }).next()
      : value.backend.getGraph({ signal: controller.signal });
    controller.abort(new Error('leave startup'));
    await assert.rejects(observation, /leave startup/);
    assert.equal(value.worker.terminated, 0);
    const initialization = value.backend.initialize();
    value.worker.reply(ready);
    await initialization;
    value.backend.close();
  }
});

test('CAD profile transfers binary exports and waits for sender detachment', async () => {
  const value = await initialized({ cad: true });
  const selected = createBackend({ type: 'worker', factories: { worker: () => value.backend } });
  assert.equal(selected.capabilities.cad, true);
  const pending = selected.exportCad({ token: 'token' });
  await tick();
  const id = value.worker.calls.at(-1).id, bytes = new Uint8Array([80, 75, 3, 4]).buffer;
  let settled = false;
  pending.then(() => { settled = true; });
  value.worker.reply({ id, ok: true, result: bytes });
  await tick();
  assert.equal(settled, false);
  value.worker.reply({ id, kind: 'transfer-confirmed', export_bytes_after: 0 });
  assert.equal(await pending, bytes);
  selected.close();
});

test('optional CAD loading failure leaves numerical operations available', async () => {
  const value = await initialized({ cad: true });
  const pending = value.backend.faceCandidates({ token: 'token' });
  await tick();
  value.worker.reply({ id: value.worker.calls.at(-1).id, ok: false,
    error: { code: 'cad_startup_failed', message: 'Missing CAD asset; numerical work remains available' } });
  await assert.rejects(pending, error => error.code === 'cad_startup_failed');
  assert.equal(value.worker.terminated, 0);
  assert.deepEqual(await requestReply(value, 'getGraph', {}, graph()), graph());
  value.backend.close();
});

test('a mutation during binary transfer confirmation rejects the obsolete export', async () => {
  const value = await initialized({ cad: true }), exported = value.backend.exportCad({ token: 'token' });
  await tick();
  const id = value.worker.calls.at(-1).id;
  value.worker.reply({ id, ok: true, result: new ArrayBuffer(4) });
  const applied = value.backend.applyFaces({ token: 'token' });
  await tick();
  const mutation = value.worker.calls.at(-1);
  value.worker.reply({ id, kind: 'transfer-confirmed', export_bytes_after: 0 });
  await assert.rejects(exported, error => error.code === 'observation_superseded');
  value.worker.reply({ id: mutation.id, ok: true, result: graph({ token: 'new' }) });
  await applied;
  value.backend.close();
});
