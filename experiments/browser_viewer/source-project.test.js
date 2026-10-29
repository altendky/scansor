import assert from 'node:assert/strict';
import test from 'node:test';
import { createSourceProjectController } from './source-project.js';

// Small DOM port for controller lifecycle tests; actual markup, File input,
// rendering, focus, and native requests are covered by source-project.spec.js.
function fixture({ sourceImport = false, sourceActions = false, sourceReplacement = false, virtualScans = false, projectArchives = false, pair = false, empty = false } = {}) {
  const previous = { document: globalThis.document, window: globalThis.window };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { disabled: false, hidden: true, inert: false, value: '', files: [], listeners: {},
      append() {}, replaceChildren(...children) { this.children = children; }, focus() { this.focused = true; },
      setAttribute() {}, addEventListener(name, listener) { this.listeners[name] = listener; },
      showModal() { this.open = true; }, close() { this.open = false; }, click() { this.clicked = true; } });
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, createElement: () => element(Symbol()),
    querySelectorAll: selector => selector.startsWith('main,') ? [element('main')] : [] };
  globalThis.window = { confirm: () => true };
  element('main');
  const source = { id: 'scan', operation: 'source', source_sha256: 'source', reference_sha256: 'reference' };
  if (virtualScans) source.descriptor = { filename: 'original.ply' };
  let state = { generation: sourceImport || sourceActions || sourceReplacement || virtualScans || projectArchives ? 1 : undefined, token: 'before',
    recipe: { nodes: empty ? [] : [source] }, metadata: {
      ...(sourceReplacement ? { source_replacement: { supported: true } } : {}),
      ...(virtualScans ? { virtual_scans: virtualMetadata({ pair }) } : {}),
      ...(projectArchives ? { project_archive: { supported: true, version: 1, limits: { max_archive_bytes: 4096 } } } : {}),
    } };
  const calls = [], busy = [], installed = [], messages = [], warnings = [], scanChanges = [];
  let proofCurrent = true, proofAllowed = true, proofRequests = 0;
  const graph = { token: 'after', recipe: { nodes: [source] } };
  const bundle = { metadata: {}, positions: new ArrayBuffer(0), indices: new ArrayBuffer(0) };
  const backend = { capabilities: { sourceImport, sourceActions, sourceReplacement, virtualScans, projectArchives },
    async getExampleRecipe() { calls.push('recipe'); return graph.recipe; },
    async replaceRecipe(payload) { calls.push(['replace', payload]); return graph; },
    async selectExample(payload) { calls.push(['example', payload]); },
    async loadWorkspace(options) {
      calls.push(['workspace', options]);
      if (!sourceImport && !sourceActions) assert.equal(options, undefined, 'Fixed worker loadWorkspace accepts no payload fields');
      return sourceImport || sourceActions ? { ...bundle, graph } : bundle;
    },
    async getGraph() { calls.push('graph'); return graph; },
    async addScan() { throw Error('Unsupported import must not be invoked'); },
    async replaceScan() { throw Error('Unsupported replacement must not be invoked'); },
    async createVirtualScan() { throw Error('Unsupported virtual creation must not be invoked'); },
    async activateScan() { throw Error('Unsupported activation must not be invoked'); },
    async resetProject() { throw Error('Unsupported reset must not be invoked'); },
    async openProject() { throw Error('Unsupported project open must not be invoked'); },
    async exportProject() { throw Error('Unsupported project export must not be invoked'); },
  };
  const controller = createSourceProjectController({ backend, getCurrent: () => state,
    canTransition: () => !busy.at(-1), setTransitionBusy: value => busy.push(value),
    prepareTransition: () => { proofRequests++; return proofAllowed ? { isCurrent: () => proofCurrent, finish: () => calls.push('finish') } : null; },
    prepareWorkspace(value) {
      const install = () => {
        calls.push('install'); installed.push(value);
        state = { ...state, generation: value.generation ?? state.generation,
          token: value.graph.token, recipe: value.graph.recipe, metadata: value.metadata ?? state.metadata };
      };
      install.dispose = () => calls.push('dispose');
      return install;
    }, inspectSource() {}, status: (...message) => messages.push(message), onWorkspaceReady: () => calls.push('ready'), onActionsLoaded: value => {
      assert.equal(busy.at(-1), false, 'Auto and warning callbacks run after the transition lock is released');
      warnings.push(value);
    }, onActiveScanChanged: () => {
      assert.equal(busy.at(-1), false, 'Active-scan evaluation is queued after the source lock is released');
      assert.equal(element('main').inert, false, 'Active-scan evaluation follows release of the surface fence');
      calls.push('active-scan-changed'); scanChanges.push(state);
    } });
  return { controller, backend, calls, busy, installed, elements, messages, warnings, scanChanges, source, getCurrent: () => state,
    advance: () => { state = { ...state, token: 'changed' }; },
    stale: () => { proofCurrent = false; },
    denyProof: () => { proofAllowed = false; }, proofRequests: () => proofRequests,
    restore() { globalThis.document = previous.document; globalThis.window = previous.window; } };
}

function virtualMetadata({ pair = false, active = 'original' } = {}) {
  return { supported: true, version: 1, active_binding: active,
    original: { binding: 'original', vertices: 289, triangles: 512 },
    virtual: pair ? { binding: 'virtual', vertices: 25, triangles: 32 } : null,
    settings: pair ? { version: 'edge-cell-reduction-v1', divisions: 4 } : null,
    can_create: !pair, can_activate: pair };
}

function virtualBundle(f, { active = 'virtual' } = {}) {
  return { generation: 2, metadata: { virtual_scans: virtualMetadata({ pair: true, active }) },
    graph: { token: 'virtual-project', recipe: structuredClone(f.getCurrent().recipe) },
    positions: new ArrayBuffer(0), indices: new ArrayBuffer(0) };
}

test('Restore retains the delivered fixed-source worker graph without requiring the new source protocol', async () => {
  const f = fixture();
  try {
    assert.equal(await f.controller.restoreExample(), true);
    assert.equal(f.installed.length, 1);
    assert.equal(f.installed[0].graph.token, 'after');
    assert.ok(!f.calls.includes('graph'), 'Use the graph explicitly returned by the accepted mutation');
    assert.ok(f.calls.indexOf('finish') < f.calls.indexOf('install'));
    assert.deepEqual(f.busy, [true, false]);
  } finally { f.restore(); }
});

const deferred = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { resolve, promise }; };
const locatedInspection = { classification: 'locate', uploadRequired: true, descriptor: { filename: 'original.ply' } };

test('project files require both capability and advertised archive profile', async () => {
  const f = fixture();
  try {
    f.controller.refresh();
    assert.equal(f.elements.get('open-project').disabled, true);
    assert.equal(f.controller.beginProjectChooser(), false);
    assert.equal(await f.controller.saveProject(), false);
    assert.equal(f.installed.length, 0);
  } finally { f.restore(); }
});

test('a portable legacy project cannot restore unrelated packaged-example actions', async () => {
  const f = fixture({ sourceImport: true, projectArchives: true });
  try {
    f.getCurrent().metadata.legacy_project = true;
    f.controller.refresh();
    assert.equal(f.elements.get('reset').disabled, true);
    assert.equal(await f.controller.restoreExample(), false);
    assert.ok(!f.calls.includes('recipe'));
  } finally { f.restore(); }
});

test('project picker retains its cursor and draft proof without reading archive bytes in the controller', async () => {
  const f = fixture({ projectArchives: true });
  try {
    let opens = 0;
    f.backend.openProject = async () => { opens++; assert.fail('Stale picker reached backend'); };
    assert.equal(f.controller.beginProjectChooser(), true);
    f.advance();
    assert.equal(await f.controller.openProjectFile({ text: () => assert.fail('Archive was read as JSON') }), false);
    assert.equal(opens, 0); assert.equal(f.installed.length, 0);
    assert.ok(!f.calls.includes('finish'));
  } finally { f.restore(); }
});

test('one-file project acceptance installs its complete bundle before finishing the transition callbacks', async () => {
  const f = fixture({ projectArchives: true });
  try {
    const file = { name: 'saved.scansor', text: () => assert.fail('Archive was read as JSON') };
    f.backend.openProject = async payload => {
      assert.equal(payload.file, file); assert.equal(payload.generation, 1);
      assert.equal(payload.token, 'before'); assert.equal(payload.confirmedDiscard, true);
      return { generation: 2, metadata: f.getCurrent().metadata,
        graph: { token: 'opened-project', recipe: structuredClone(f.getCurrent().recipe) }, import_warnings: [] };
    };
    assert.equal(await f.controller.openProjectFile(file), true);
    assert.equal(f.getCurrent().generation, 2); assert.equal(f.installed.length, 1);
    assert.deepEqual(f.busy, [true, false]); assert.deepEqual(f.warnings, [[]]);
    assert.ok(f.calls.indexOf('finish') < f.calls.indexOf('install'));
  } finally { f.restore(); }
});

test('corrupt project rejection preserves the current source, actions and unfinished edit', async () => {
  const f = fixture({ projectArchives: true });
  try {
    f.backend.openProject = async () => { throw Object.assign(Error('Invalid archive'), { code: 'invalid_project' }); };
    assert.equal(await f.controller.openProjectFile({ name: 'broken.scansor' }), false);
    assert.equal(f.getCurrent().token, 'before'); assert.equal(f.installed.length, 0);
    assert.ok(!f.calls.includes('finish')); assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('a superseded project export cannot download stale bytes or alter the project', async () => {
  const f = fixture({ projectArchives: true });
  try {
    const pending = deferred();
    f.backend.exportProject = () => pending.promise;
    const saving = f.controller.saveProject();
    assert.equal(f.elements.get('main').inert, true);
    f.advance(); pending.resolve(new Blob(['old compressed bytes']));
    assert.equal(await saving, false); assert.equal(f.installed.length, 0);
    assert.equal(f.getCurrent().token, 'changed'); assert.equal(f.elements.get('main').inert, false);
    assert.match(f.messages.at(-1)[0], /changed while saving/);
  } finally { f.restore(); }
});

test('actions file admission honors the workspace byte limit before reading', async () => {
  for (const declared of [undefined, 32 * 1024 * 1024]) {
    const f = fixture({ sourceActions: true });
    try {
      if (declared !== undefined) f.getCurrent().metadata.actions_limits = { max_json_bytes: declared };
      const maximum = declared ?? 1_000_000;
      let reads = 0, inspections = 0;
      f.backend.inspectActions = async payload => {
        inspections++; return { ...payload, ...locatedInspection, recipe: payload.recipe };
      };
      await f.controller.loadActionsFile({ size: maximum, text: async () => { reads++; return '{"nodes":[]}'; } });
      assert.equal(reads, 1); assert.equal(inspections, 1);
      await f.controller.loadActionsFile({ size: maximum + 1, text: () => assert.fail('Oversized file was read') });
      assert.equal(inspections, 1);
      assert.match(f.messages.at(-1)[0], declared === undefined ? /under 1 MB/ : /32 MiB/);
    } finally { f.restore(); }
  }
});

test('invalid advertised actions limit cannot disable file admission', async () => {
  for (const maximum of [0, -1, NaN, '33554432', 2 ** 53]) {
    const f = fixture({ sourceActions: true });
    try {
      f.getCurrent().metadata.actions_limits = { max_json_bytes: maximum };
      assert.equal(await f.controller.loadActionsFile({ size: 10, text: () => assert.fail('Invalid limit read the file') }), false);
      assert.match(f.messages.at(-1)[0], /Invalid actions byte limit/);
    } finally { f.restore(); }
  }
});

test('lookup, chooser cancellation and wrong-file retry preserve the raw recipe and live draft', async () => {
  const f = fixture({ sourceActions: true });
  try {
    const raw = { nodes: [{ id: 'scan', operation: 'source', descriptor: { filename: 'original.ply' } }], marker: 'raw' };
    f.backend.inspectActions = async payload => { f.calls.push(['inspect', payload]); return { ...payload, ...locatedInspection, recipe: { marker: 'normalized' } }; };
    assert.equal(await f.controller.loadActions(raw), false);
    raw.marker = 'caller mutation';
    assert.equal(f.elements.get('locate-source-dialog').open, true);
    assert.deepEqual(f.busy, []);
    assert.equal(f.calls.includes('finish'), false);
    f.elements.get('locate-source-file').files = [];
    await f.elements.get('locate-source-file').onchange();
    assert.equal(f.elements.get('locate-source-dialog').open, true);
    const file = { name: 'renamed.ply' };
    f.elements.get('locate-source-file').files = [file];
    f.backend.openActions = async payload => {
      assert.equal(f.elements.get('locate-source-dialog').open, false);
      assert.equal(payload.recipe.marker, 'raw');
      assert.equal(payload.file, file);
      assert.equal(payload.generation, 1);
      throw Object.assign(Error('Wrong source bytes'), { code: 'source_mismatch' });
    };
    await f.elements.get('locate-source-file').onchange();
    assert.equal(f.elements.get('locate-source-dialog').open, true);
    assert.match(f.elements.get('locate-source-error').textContent, /Wrong source/);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.installed.length, 0);
    assert.equal(f.busy.at(-1), false);
    f.elements.get('cancel-locate-source').onclick();
    assert.equal(f.elements.get('locate-source-dialog').open, false);
  } finally { f.restore(); }
});

test('matching actions adopt a full bundle before publishing warnings and ending the draft', async () => {
  const f = fixture({ sourceActions: true });
  try {
    f.backend.inspectActions = async payload => ({ ...payload, classification: 'matching', uploadRequired: false });
    f.backend.openActions = async payload => {
      assert.equal(payload.file, undefined);
      assert.equal(payload.confirmedDiscard, true);
      return { ...await f.backend.loadWorkspace(), metadata: { import_warnings: ['renamed'] } };
    };
    assert.equal(await f.controller.loadActions({ nodes: [] }), true);
    assert.equal(f.installed.length, 1);
    assert.ok(f.calls.indexOf('finish') < f.calls.indexOf('install'));
    assert.deepEqual(f.warnings, [['renamed']]);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('late file reads and inspections cannot replace a newer load or acquire a fresh cursor', async () => {
  const f = fixture({ sourceActions: true });
  try {
    const read = deferred(), inspected = deferred();
    let opens = 0;
    f.backend.inspectActions = () => inspected.promise;
    f.backend.openActions = async () => { opens++; return f.backend.loadWorkspace(); };
    const first = f.controller.loadActionsFile({ size: 20, text: () => read.promise });
    const second = f.controller.loadActions({ nodes: [] });
    read.resolve('{"nodes":[]}');
    assert.equal(await first, false);
    f.advance();
    inspected.resolve({ classification: 'matching', uploadRequired: false });
    assert.equal(await second, false);
    assert.equal(opens, 0);
    assert.deepEqual(f.busy, []);
  } finally { f.restore(); }
});

test('locator Escape invalidates an outstanding file choice without touching the draft', async () => {
  const f = fixture({ sourceActions: true });
  try {
    f.backend.inspectActions = async payload => ({ ...payload, ...locatedInspection });
    f.backend.openActions = () => assert.fail('Cancelled intent must not open');
    await f.controller.loadActions({ nodes: [] });
    let prevented = false;
    f.elements.get('locate-source-dialog').listeners.cancel({ preventDefault() { prevented = true; } });
    f.elements.get('locate-source-file').files = [{ name: 'late.ply' }];
    await f.elements.get('locate-source-file').onchange();
    assert.equal(prevented, true);
    assert.deepEqual(f.busy, []);
  } finally { f.restore(); }
});

test('malformed non-HTTP inspection cannot invoke project mutation', async () => {
  for (const defect of [{ generation: 2 }, { token: 'other' }, { classification: 'unexpected' }, { uploadRequired: true }]) {
    const f = fixture({ sourceActions: true });
    try {
      f.backend.inspectActions = async payload => ({ ...payload, classification: 'matching', uploadRequired: false, ...defect });
      f.backend.openActions = () => assert.fail('Invalid producer response must not mutate');
      assert.equal(await f.controller.loadActions({ nodes: [] }), false);
      assert.match(f.messages.at(-1)[0], /Invalid actions inspection/);
      assert.equal(f.calls.includes('finish'), false);
    } finally { f.restore(); }
  }
});

test('a newer source chooser or cancelled actions picker invalidates a pending file read', async () => {
  for (const next of ['source', 'cancel']) {
    const f = fixture({ sourceActions: true, sourceImport: true, empty: true });
    try {
      const read = deferred();
      f.backend.inspectActions = () => assert.fail('Superseded file must not inspect');
      const pending = f.controller.loadActionsFile({ size: 20, text: () => read.promise });
      if (next === 'source') f.elements.get('add-scan').onclick();
      else f.elements.get('file').listeners.cancel();
      read.resolve('{"nodes":[]}');
      assert.equal(await pending, false);
      assert.deepEqual(f.busy, []);
    } finally { f.restore(); }
  }
});

test('legacy worker imports only its exact descriptor-free source through a protected full bundle', async () => {
  const f = fixture();
  try {
    f.backend.importRecipe = async payload => {
      f.calls.push(['import', payload]);
      return { token: 'legacy-import', recipe: payload.recipe, import_warnings: ['legacy rename'] };
    };
    assert.equal(await f.controller.loadActions({ nodes: [{ ...f.source, source_sha256: 'wrong' }] }), false);
    assert.equal(f.calls.length, 0);
    assert.equal(await f.controller.loadActions({ nodes: [{ ...f.source, descriptor: {} }] }), false);
    assert.equal(f.calls.length, 0);
    assert.equal(await f.controller.loadActions({ nodes: [f.source] }), true);
    assert.equal(f.installed[0].graph.token, 'legacy-import');
    assert.deepEqual(f.warnings, [['legacy rename']]);
  } finally { f.restore(); }
});

test('lost accepted actions delivery retains the draft and recovers accepted warnings explicitly', async () => {
  const f = fixture({ sourceActions: true });
  try {
    f.backend.inspectActions = async payload => ({ ...payload, classification: 'matching', uploadRequired: false });
    f.backend.openActions = async () => { throw Object.assign(Error('Display reply lost'), { code: 'accepted_workspace_unavailable' }); };
    await f.controller.loadActions({ nodes: [] });
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.busy.at(-1), true);
    const load = f.backend.loadWorkspace;
    f.backend.loadWorkspace = async () => ({ ...await load(), metadata: { import_warnings: ['retained receipt'] } });
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.installed.length, 1);
    assert.deepEqual(f.warnings, [['retained receipt']]);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('capability-off source commands cannot import or reset; explicit examples still use the old contract', async () => {
  const f = fixture({ empty: true });
  try {
    f.controller.refresh();
    assert.equal(f.elements.get('add-scan').disabled, true);
    assert.equal(f.elements.get('new-project').disabled, true);
    assert.equal(await f.controller.addScan({ size: 10 }), false);
    assert.equal(await f.controller.resetProject(), false);
    assert.deepEqual(f.calls, []);
    assert.equal(await f.controller.selectExample('nozzle'), true);
    assert.deepEqual(f.calls.find(value => Array.isArray(value) && value[0] === 'example'),
      ['example', { example: 'nozzle', token: 'before' }]);
    assert.equal(f.installed[0].graph.token, 'after');
  } finally { f.restore(); }
});

test('an ambiguous accepted POST reply keeps the app busy until explicit current-workspace delivery', async () => {
  const f = fixture({ sourceImport: true });
  try {
    f.backend.resetProject = async () => { throw Object.assign(Error('Reply lost'), { code: 'transport_error' }); };
    assert.equal(await f.controller.resetProject(), false);
    assert.equal(f.installed.length, 0);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.elements.get('source-unavailable').hidden, false);
    assert.equal(f.busy.at(-1), true);
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.installed.length, 1);
    assert.equal(f.busy.at(-1), false);
    assert.equal(f.elements.get('main').inert, false);
  } finally { f.restore(); }
});

test('a changed draft after acceptance disposes prepared resources and fences without ending the draft', async () => {
  const f = fixture({ sourceImport: true });
  try {
    f.backend.resetProject = async () => { f.stale(); return f.backend.loadWorkspace(); };
    assert.equal(await f.controller.resetProject(), false);
    assert.equal(f.installed.length, 0);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.calls.includes('dispose'), true);
    assert.equal(f.elements.get('source-unavailable').hidden, false);
    assert.equal(f.busy.at(-1), true);
  } finally { f.restore(); }
});

test('runtime graph recovery requires an equal valid displayed source epoch', async () => {
  for (const epoch of [undefined, -1, 1.5, NaN, 2]) {
    const f = fixture({ sourceImport: true });
    try {
      let restored = 0;
      assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: epoch }, () => restored++), false);
      assert.equal(restored, 1);
      assert.equal(f.elements.get('main').inert, true);
      assert.equal(f.elements.get('load').disabled, true);
      assert.equal(f.elements.get('source-unavailable').hidden, false);
      assert.equal(f.calls.includes('finish'), false);
      assert.equal(f.installed.length, 0);
    } finally { f.restore(); }
  }
  for (const sourceImport of [false, true]) {
    const f = fixture({ sourceImport });
    try {
      assert.equal(await f.controller.prepareRuntimeRestore(sourceImport ? { project_generation: 1 } : {}, () => {}), true);
      assert.equal(f.installed.length, 0);
      assert.equal(f.calls.includes('finish'), false);
    } finally { f.restore(); }
  }
});

test('Force waits for pending acceptance cleanup and retains prior actions until explicit full-bundle Retry', async () => {
  const f = fixture({ sourceImport: true }), pending = deferred();
  try {
    f.backend.resetProject = async () => { await pending.promise; throw Object.assign(Error('Promotion lost'), { code: 'accepted_workspace_unavailable' }); };
    const operation = f.controller.resetProject();
    let restored = false;
    const recovery = f.controller.prepareRuntimeRestore({ project_generation: 2 }, () => {
      restored = true; f.elements.get('main').inert = false;
    });
    await Promise.resolve();
    assert.equal(restored, false);
    pending.resolve();
    assert.equal(await operation, false);
    assert.equal(await recovery, false);
    assert.equal(restored, true);
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.busy.at(-1), true);
    assert.equal(f.calls.includes('finish'), false);
    f.backend.exportRecipe = async value => { f.calls.push(['prior', value]); return value.recipe; };
    await f.elements.get('download-prior-actions').onclick();
    assert.deepEqual(f.calls.find(value => value?.[0] === 'prior')[1].recipe, f.getCurrent().recipe);
    const originalLoad = f.backend.loadWorkspace;
    f.backend.loadWorkspace = async () => ({ ...await originalLoad(), generation: 2 });
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.installed.length, 1);
    assert.equal(f.getCurrent().generation, 2);
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.elements.get('source-unavailable').hidden, true);
    assert.equal(f.busy.at(-1), false);
    assert.equal(f.calls.filter(value => value === 'finish').length, 1);
    assert.equal(f.calls.filter(value => value === 'ready').length, 1);
  } finally { f.restore(); }
});

test('a same-epoch unavailable source stays fenced and a new fence does not inherit the runtime inert layer', async () => {
  const f = fixture({ sourceImport: true });
  try {
    f.elements.get('main').inert = true; // Bootstrap owns this layer before the handshake.
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 2 }, () => { f.elements.get('main').inert = false; }), false);
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => { f.elements.get('main').inert = false; }), false);
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.calls.includes('finish'), false);
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('Force before adoption settles cancellation then permits unchanged-epoch graph recovery without ending the draft', async () => {
  const f = fixture({ sourceImport: true }), pending = deferred();
  try {
    f.backend.resetProject = async () => { await pending.promise; throw Object.assign(Error('Stopped'), { code: 'computation_cancelled' }); };
    const operation = f.controller.resetProject();
    const recovery = f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => { f.elements.get('main').inert = false; });
    pending.resolve();
    assert.equal(await operation, false);
    assert.equal(await recovery, true);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.installed.length, 0);
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('pending source cleanup cannot release a runtime lock acquired during Force', async () => {
  const f = fixture({ sourceImport: true }), pending = deferred();
  try {
    f.backend.resetProject = async () => { await pending.promise; throw Object.assign(Error('Stopped'), { code: 'computation_cancelled' }); };
    const operation = f.controller.resetProject();
    // The old bootstrap directly sets inert; the new hook must also retain its
    // ownership until the completed-runtime handshake transfers that layer.
    f.controller.lockRuntime();
    f.elements.get('main').inert = true;
    pending.resolve();await operation;
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.busy.at(-1), true);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => { f.elements.get('main').inert = false; }), true);
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('a superseded Force retains the original source lock snapshot for the next recovery', async () => {
  const f = fixture({ sourceImport: true }), pending = deferred();
  try {
    f.backend.resetProject = async () => { await pending.promise; throw Object.assign(Error('Stopped'), { code: 'computation_cancelled' }); };
    const operation = f.controller.resetProject();
    f.controller.lockRuntime();
    f.elements.get('main').inert = true;
    pending.resolve();await operation;
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => assert.fail('Stale recovery cannot unlock'), () => false), false);
    f.controller.lockRuntime();
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.busy.at(-1), true);
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => { f.elements.get('main').inert = false; }), true);
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
    assert.equal(f.calls.includes('finish'), false);
  } finally { f.restore(); }
});

test('source Retry stays blocked during runtime recovery and becomes reachable at fence handoff', async () => {
  const f = fixture({ sourceImport: true });
  try {
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 2 }), false);
    f.controller.lockRuntime();
    assert.equal(f.elements.get('retry-source-workspace').disabled, true);
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.calls.some(value => value?.[0] === 'workspace'), false);
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 2 }, () => { f.elements.get('main').inert = false; }), false);
    assert.equal(f.elements.get('retry-source-workspace').disabled, false);
    assert.equal(f.elements.get('main').inert, true);
    await f.elements.get('retry-source-workspace').onclick();
    assert.equal(f.installed.length, 1);
    assert.equal(f.elements.get('main').inert, false);
  } finally { f.restore(); }
});

test('replacement requires both the backend capability and active-source metadata', async () => {
  for (const options of [{}, { sourceImport: true }, { sourceReplacement: true, empty: true }]) {
    const f = fixture(options);
    try {
      f.controller.refresh();
      assert.equal(f.elements.get('replace-scan').disabled, true);
      assert.equal(f.controller.beginReplacementChooser(), false);
      assert.equal(await f.controller.replaceScan({ name: 'new.ply' }), false);
      assert.equal(f.proofRequests(), 0);
      assert.equal(f.calls.length, 0);
    } finally { f.restore(); }
  }
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    f.getCurrent().metadata.source_replacement.supported = false;
    f.controller.refresh();
    assert.equal(f.elements.get('replace-scan').disabled, true);
    assert.equal(f.controller.beginReplacementChooser(), false);
  } finally { f.restore(); }
});

test('replacement chooser cancellation retains the draft and committed actions', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    window.confirm = () => assert.fail('Preserving replacement must not discard all committed actions');
    const before = structuredClone(f.getCurrent());
    f.controller.refresh();
    assert.equal(f.elements.get('replace-scan').disabled, false);
    assert.equal(f.controller.beginReplacementChooser(), true);
    assert.equal(f.elements.get('replacement-file').clicked, true);
    assert.equal(f.proofRequests(), 1);
    f.elements.get('replacement-file').listeners.cancel();
    assert.equal(await f.controller.chooseReplacementFile({ name: 'late.ply' }), false);
    assert.deepEqual(f.getCurrent(), before);
    assert.deepEqual(f.busy, []);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.installed.length, 0);
  } finally { f.restore(); }
});

test('declining unfinished-edit transition proof never opens a replacement picker', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    f.denyProof();
    assert.equal(f.controller.beginReplacementChooser(), false);
    assert.equal(f.elements.get('replacement-file').clicked, undefined);
    assert.equal(await f.controller.chooseReplacementFile({ name: 'late.ply' }), false);
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.busy, []);
  } finally { f.restore(); }
});

test('replacement selection keeps its original cursor and draft proof across the native picker', async () => {
  for (const change of ['cursor', 'draft', 'another chooser', 'no file']) {
    const f = fixture({ sourceImport: true, sourceReplacement: true });
    try {
      assert.equal(f.controller.beginReplacementChooser(), true);
      if (change === 'cursor') f.advance();
      if (change === 'draft') f.stale();
      if (change === 'another chooser') f.elements.get('load').onclick();
      const proofRequests = f.proofRequests();
      assert.equal(await f.controller.chooseReplacementFile(change === 'no file' ? undefined : { name: 'new.ply' }), false);
      assert.equal(f.proofRequests(), proofRequests, 'Late picker completion cannot acquire fresh draft authority');
      assert.equal(f.calls.includes('finish'), false);
      assert.equal(f.installed.length, 0);
      assert.deepEqual(f.busy, []);
    } finally { f.restore(); }
  }
});

test('successful replacement finishes the captured edit proof only after complete display preparation', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    window.confirm = () => assert.fail('Replacement must preserve authored definitions');
    const file = { name: 'aligned.ply' }, before = structuredClone(f.getCurrent());
    const bundle = { ...await f.backend.loadWorkspace(), generation: 2, graph: { token: 'new-scan', recipe: before.recipe } };
    f.backend.replaceScan = async (payload, { onProgress }) => {
      f.calls.push(['replacement', payload]);
      assert.equal(f.elements.get('main').inert, true);
      assert.equal(f.elements.get('source-progress').hidden, false);
      assert.equal(f.calls.includes('finish'), false);
      onProgress({ operationId: 'replace', status: 'preparing', phase: 'faces', completed: 1, total: 2 });
      assert.match(f.elements.get('source-progress-detail').textContent, /faces.*1.*2/);
      return bundle;
    };
    assert.equal(await f.controller.replaceScan(file), true);
    assert.deepEqual(f.calls.find(value => value?.[0] === 'replacement'), ['replacement', { file, generation: 1, token: 'before' }]);
    assert.equal(f.proofRequests(), 1);
    assert.ok(f.calls.indexOf('finish') < f.calls.indexOf('install'));
    assert.equal(f.getCurrent().generation, 2);
    assert.deepEqual(f.getCurrent().recipe, before.recipe);
    assert.equal(f.elements.get('main').inert, false);
    assert.equal(f.busy.at(-1), false);
    assert.match(f.messages.at(-1)[0], /Scan replaced.*source volume/);
  } finally { f.restore(); }
});

test('replacement Cancel uses the reserved cursor and keeps the prior editor until safe completion', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true }), pending = deferred();
  try {
    const before = structuredClone(f.getCurrent());
    f.backend.replaceScan = async (_, { onProgress }) => {
      onProgress({ operationId: 'replacement', status: 'preparing', phase: 'vertices' });
      await pending.promise;
      throw Object.assign(Error('Stopped'), { code: 'cancelled' });
    };
    f.backend.cancelSourceOperation = async payload => {
      assert.deepEqual(payload, { operationId: 'replacement', generation: 1, token: 'before' });
      return { operationId: 'replacement', status: 'cancelling' };
    };
    const operation = f.controller.replaceScan({ name: 'aligned.ply' });
    await f.controller.cancel();
    assert.equal(f.elements.get('cancel-source-import').disabled, true);
    assert.equal(f.elements.get('main').inert, true);
    pending.resolve();
    assert.equal(await operation, false);
    assert.deepEqual(f.getCurrent(), before);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.installed.length, 0);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('known replacement rejection restores controls while uncertain acceptance requires explicit workspace reload', async () => {
  for (const code of ['invalid_source', 'transport_error', 'accepted_workspace_unavailable']) {
    const f = fixture({ sourceImport: true, sourceReplacement: true });
    try {
      const before = structuredClone(f.getCurrent());
      f.backend.replaceScan = async () => { throw Object.assign(Error('Replacement reply'), { code }); };
      assert.equal(await f.controller.replaceScan({ name: 'aligned.ply' }), false);
      assert.deepEqual(f.getCurrent(), before);
      assert.equal(f.calls.includes('finish'), false);
      assert.equal(f.installed.length, 0);
      if (code === 'invalid_source') {
        assert.equal(f.busy.at(-1), false);
        assert.equal(f.elements.get('main').inert, false);
      } else {
        assert.equal(f.busy.at(-1), true);
        assert.equal(f.elements.get('source-unavailable').hidden, false);
        f.backend.loadWorkspace = async () => ({ generation: 2, metadata: {}, graph: { token: 'replacement', recipe: before.recipe } });
        await f.elements.get('retry-source-workspace').onclick();
        assert.equal(f.getCurrent().generation, 2);
        assert.deepEqual(f.getCurrent().recipe, before.recipe);
        assert.equal(f.calls.filter(value => value === 'finish').length, 1);
        assert.equal(f.busy.at(-1), false);
      }
    } finally { f.restore(); }
  }
});

test('replacement accepted after a draft edit remains fenced without closing the changed editor', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    f.backend.replaceScan = async () => { f.stale(); return f.backend.loadWorkspace(); };
    assert.equal(await f.controller.replaceScan({ name: 'aligned.ply' }), false);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.calls.includes('dispose'), true);
    assert.equal(f.installed.length, 0);
    assert.equal(f.elements.get('main').inert, true);
  } finally { f.restore(); }
});

test('Force on either side of replacement adoption keeps the right source fence and prior draft', async () => {
  for (const adopted of [false, true]) {
    const f = fixture({ sourceImport: true, sourceReplacement: true }), pending = deferred();
    try {
      f.backend.replaceScan = async () => {
        await pending.promise;
        throw Object.assign(Error('Worker stopped'), { code: adopted ? 'accepted_workspace_unavailable' : 'computation_cancelled' });
      };
      const operation = f.controller.replaceScan({ name: 'aligned.ply' });
      f.controller.lockRuntime();
      const recovery = f.controller.prepareRuntimeRestore({ project_generation: adopted ? 2 : 1 }, () => { f.elements.get('main').inert = false; });
      pending.resolve();
      assert.equal(await operation, false);
      assert.equal(await recovery, !adopted);
      assert.equal(f.calls.includes('finish'), false);
      assert.equal(f.installed.length, 0);
      assert.equal(f.elements.get('main').inert, adopted);
      assert.equal(f.busy.at(-1), adopted);
    } finally { f.restore(); }
  }
});

test('Force invalidates a replacement file picker even when the recovered cursor is unchanged', async () => {
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    assert.equal(f.controller.beginReplacementChooser(), true);
    f.controller.lockRuntime();
    assert.equal(await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => {}), true);
    assert.equal(await f.controller.chooseReplacementFile({ name: 'late.ply' }), false);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.installed.length, 0);
  } finally { f.restore(); }
});

test('virtual scan commands require capability, supported profile and an imported source', async () => {
  for (const missing of ['capability', 'profile', 'source', 'version', 'counts', 'original']) {
    const f = fixture({ virtualScans: true });
    try {
      if (missing === 'capability') f.backend.capabilities.virtualScans = false;
      if (missing === 'profile') f.getCurrent().metadata.virtual_scans.supported = false;
      if (missing === 'source') delete f.source.descriptor;
      if (missing === 'version') f.getCurrent().metadata.virtual_scans.version = 2;
      if (missing === 'counts') f.getCurrent().metadata.virtual_scans.original.vertices = '289';
      if (missing === 'original') f.getCurrent().metadata.virtual_scans.original = null;
      f.controller.refresh();
      assert.equal(f.elements.get('create-virtual-scan').disabled, true);
      assert.equal(f.elements.get('active-scan-controls').hidden, true);
      assert.equal(f.controller.beginVirtualScan(), false);
      assert.equal(await f.controller.activateScan('virtual'), false);
      assert.equal(f.proofRequests(), 0);
      assert.deepEqual(f.installed, []);
    } finally { f.restore(); }
  }
});

test('active scan strip shows achieved counts and a single original before creation', () => {
  const f = fixture({ virtualScans: true });
  try {
    f.controller.refresh();
    assert.equal(f.elements.get('active-scan-controls').hidden, false);
    assert.equal(f.elements.get('active-scan').disabled, true);
    assert.equal(f.elements.get('active-scan').children.length, 1);
    assert.equal(f.elements.get('active-scan').value, 'original');
    assert.match(f.elements.get('active-scan-summary').textContent, /Original scan.*289 vertices.*512 triangles.*display and fitting/);
    assert.equal(f.elements.get('create-virtual-scan').hidden, false);
    assert.equal(f.elements.get('create-virtual-scan').disabled, false);
  } finally { f.restore(); }
});

test('virtual dialog cancellation and denied draft proof preserve authored work', async () => {
  const f = fixture({ virtualScans: true });
  try {
    window.confirm = () => assert.fail('Virtual creation preserves committed actions');
    const before = structuredClone(f.getCurrent());
    assert.equal(f.controller.beginVirtualScan(), true);
    assert.equal(f.elements.get('virtual-scan-dialog').open, true);
    assert.equal(f.elements.get('virtual-scan-divisions').value, '32');
    assert.equal(f.proofRequests(), 1);
    f.elements.get('cancel-create-virtual-scan').onclick();
    assert.equal(f.elements.get('virtual-scan-dialog').open, false);
    assert.equal(await f.controller.createVirtualScan(), false);
    assert.deepEqual(f.getCurrent(), before);
    assert.deepEqual(f.busy, []);
    f.denyProof();
    assert.equal(f.controller.beginVirtualScan(), false);
    assert.equal(f.elements.get('virtual-scan-dialog').open, false);
    assert.equal(f.calls.includes('finish'), false);
  } finally { f.restore(); }
});

test('invalid virtual detail is rejected without consuming the captured proof', async () => {
  const f = fixture({ virtualScans: true });
  try {
    assert.equal(f.controller.beginVirtualScan(), true);
    for (const value of ['', '1', '4097', '2.5', 'Infinity', 'invalid']) {
      f.elements.get('virtual-scan-divisions').value = value;
      assert.equal(await f.controller.createVirtualScan(), false);
      assert.match(f.elements.get('virtual-scan-error').textContent, /whole number from 2 to 4096/);
      assert.equal(f.elements.get('virtual-scan-dialog').open, true);
    }
    assert.equal(f.proofRequests(), 1);
    assert.deepEqual(f.busy, []);
    assert.equal(f.calls.includes('finish'), false);
  } finally { f.restore(); }
});

test('virtual creation installs a full bundle before ending its captured draft and shows achieved detail', async () => {
  const f = fixture({ virtualScans: true });
  try {
    window.confirm = () => assert.fail('Virtual creation cannot discard committed work');
    const bundle = virtualBundle(f);
    f.backend.createVirtualScan = async (payload, { onProgress }) => {
      f.calls.push(['virtual', payload]);
      assert.equal(f.elements.get('virtual-scan-dialog').open, false);
      assert.equal(f.elements.get('main').inert, true);
      assert.equal(f.elements.get('source-progress').hidden, false);
      assert.equal(f.calls.includes('finish'), false);
      onProgress({ operationId: 'virtual-operation', status: 'preparing', phase: 'cluster-edges', completed: 2, total: 3 });
      assert.match(f.elements.get('source-progress-detail').textContent, /cluster-edges.*2.*3/);
      return bundle;
    };
    assert.equal(f.controller.beginVirtualScan(), true);
    f.elements.get('virtual-scan-divisions').value = '4';
    assert.equal(await f.controller.createVirtualScan(), true);
    assert.deepEqual(f.calls.find(value => value?.[0] === 'virtual'), ['virtual', {
      settings: { version: 'edge-cell-reduction-v1', divisions: 4 }, generation: 1, token: 'before' }]);
    assert.equal(f.proofRequests(), 1);
    assert.ok(f.calls.indexOf('finish') < f.calls.indexOf('install'));
    assert.equal(f.elements.get('active-scan').value, 'virtual');
    assert.equal(f.elements.get('active-scan').children.length, 2);
    assert.match(f.elements.get('active-scan-summary').textContent, /Virtual scan.*Preliminary work.*25 vertices.*32 triangles/);
    assert.equal(f.elements.get('create-virtual-scan').hidden, true);
    assert.equal(f.elements.get('active-scan').disabled, false);
    assert.equal(f.busy.at(-1), false);
  } finally { f.restore(); }
});

test('stale virtual dialogs cannot acquire new cursor or draft authority', async () => {
  for (const change of ['cursor', 'draft', 'chooser', 'escape', 'Force']) {
    const f = fixture({ virtualScans: true });
    try {
      f.controller.beginVirtualScan();
      if (change === 'cursor') f.advance();
      if (change === 'draft') f.stale();
      if (change === 'chooser') f.elements.get('load').onclick();
      if (change === 'escape') f.elements.get('virtual-scan-dialog').listeners.cancel({ preventDefault() {} });
      if (change === 'Force') {
        f.controller.lockRuntime();
        await f.controller.prepareRuntimeRestore({ project_generation: 1 }, () => {});
      }
      const requests = f.proofRequests();
      assert.equal(await f.controller.createVirtualScan(), false);
      assert.equal(f.proofRequests(), requests);
      assert.equal(f.calls.includes('finish'), false);
      assert.deepEqual(f.installed, []);
    } finally { f.restore(); }
  }
});

test('active picker dispatches the chosen binding and current choice is a no-op', async () => {
  const f = fixture({ virtualScans: true, pair: true });
  try {
    window.confirm = () => assert.fail('Activation preserves definitions');
    f.backend.activateScan = async payload => { f.calls.push(['activation', payload]); return virtualBundle(f); };
    f.controller.refresh();
    f.elements.get('active-scan').value = 'virtual';
    assert.equal(await f.elements.get('active-scan').onchange(), true);
    assert.deepEqual(f.calls.find(value => value?.[0] === 'activation'), ['activation', { binding: 'virtual', generation: 1, token: 'before' }]);
    assert.equal(f.proofRequests(), 1);
    assert.equal(await f.controller.activateScan('virtual'), false);
    assert.equal(f.proofRequests(), 1);
    assert.equal(f.calls.filter(value => value?.[0] === 'activation').length, 1);
    assert.match(f.messages.at(-1)[0], /Active scan changed.*Evaluate/);
  } finally { f.restore(); }
});

test('rejected activation proof and unknown bindings restore the actual picker choice', async () => {
  const f = fixture({ virtualScans: true, pair: true });
  try {
    f.controller.refresh();
    assert.equal(await f.controller.activateScan('unrelated'), false);
    assert.match(f.messages.at(-1)[0], /original or virtual/);
    f.elements.get('active-scan').value = 'virtual';
    f.denyProof();
    assert.equal(await f.elements.get('active-scan').onchange(), false);
    assert.equal(f.elements.get('active-scan').value, 'original');
    assert.deepEqual(f.installed, []);
    assert.equal(f.calls.includes('finish'), false);
  } finally { f.restore(); }
});

test('virtual creation and activation Cancel use reserved source cursors and retain drafts', async () => {
  for (const kind of ['virtual', 'activation']) {
    const f = fixture({ virtualScans: true, pair: kind === 'activation' }), pending = deferred();
    try {
      const before = structuredClone(f.getCurrent());
      const method = kind === 'virtual' ? 'createVirtualScan' : 'activateScan';
      f.backend[method] = async (_, { onProgress }) => {
        onProgress({ operationId: kind, status: 'preparing', phase: 'preparing' });
        await pending.promise;
        throw Object.assign(Error('Stopped'), { code: 'cancelled' });
      };
      f.backend.cancelSourceOperation = async payload => {
        assert.deepEqual(payload, { operationId: kind, generation: 1, token: 'before' });
        return { operationId: kind, status: 'cancelling' };
      };
      if (kind === 'virtual') f.controller.beginVirtualScan();
      const operation = kind === 'virtual' ? f.controller.createVirtualScan() : f.controller.activateScan('virtual');
      await f.controller.cancel();
      assert.equal(f.elements.get('cancel-source-import').disabled, true);
      assert.equal(f.elements.get('main').inert, true);
      pending.resolve();
      assert.equal(await operation, false);
      assert.deepEqual(f.getCurrent(), before);
      assert.equal(f.calls.includes('finish'), false);
      assert.equal(f.busy.at(-1), false);
      assert.equal(f.elements.get('active-scan').value, 'original');
    } finally { f.restore(); }
  }
});

test('virtual rejection restores controls and uncertain delivery fences until explicit current bundle', async () => {
  for (const code of ['invalid_reduction', 'transport_error', 'accepted_workspace_unavailable']) {
    const f = fixture({ virtualScans: true });
    try {
      f.backend.createVirtualScan = async () => { throw Object.assign(Error('Reduction failed'), { code }); };
      f.controller.beginVirtualScan();
      assert.equal(await f.controller.createVirtualScan(), false);
      assert.equal(f.calls.includes('finish'), false);
      assert.deepEqual(f.installed, []);
      if (code === 'invalid_reduction') {
        assert.equal(f.elements.get('main').inert, false);
        assert.equal(f.busy.at(-1), false);
        assert.equal(f.elements.get('create-virtual-scan').disabled, false);
      } else {
        assert.equal(f.elements.get('main').inert, true);
        assert.equal(f.elements.get('active-scan').disabled, true);
        assert.equal(f.busy.at(-1), true);
        f.backend.loadWorkspace = async () => virtualBundle(f);
        await f.elements.get('retry-source-workspace').onclick();
        assert.equal(f.elements.get('active-scan').value, 'virtual');
        assert.equal(f.busy.at(-1), false);
      }
    } finally { f.restore(); }
  }
});

test('virtual acceptance after a draft edit keeps the changed editor fenced', async () => {
  const f = fixture({ virtualScans: true });
  try {
    f.backend.createVirtualScan = async () => { f.stale(); return virtualBundle(f); };
    f.controller.beginVirtualScan();
    assert.equal(await f.controller.createVirtualScan(), false);
    assert.equal(f.calls.includes('finish'), false);
    assert.equal(f.calls.includes('dispose'), true);
    assert.equal(f.elements.get('main').inert, true);
    assert.equal(f.elements.get('active-scan').disabled, true);
    assert.deepEqual(f.installed, []);
  } finally { f.restore(); }
});

test('virtual saved actions request their original while ordinary actions name their source', async () => {
  for (const virtual of [false, true]) {
    const f = fixture({ sourceActions: true });
    try {
      f.backend.inspectActions = async payload => ({ ...payload, ...locatedInspection });
      const recipe = { nodes: [{ ...f.source, ...(virtual ? { virtual_scan: {} } : {}) }] };
      assert.equal(await f.controller.loadActions(recipe), false);
      assert.match(f.elements.get('locate-source-hint').textContent,
        virtual ? /Locate the original scan.*original.ply/ : /Locate the source scan.*original.ply/);
    } finally { f.restore(); }
  }
});

test('matching actions confirmation describes restoring the saved active scan from available data', async () => {
  const f = fixture({ sourceActions: true });
  try {
    const confirmations = [];
    window.confirm = message => { confirmations.push(message); return true; };
    f.backend.inspectActions = async payload => ({ ...payload, classification: 'matching', uploadRequired: false });
    f.backend.openActions = async () => f.backend.loadWorkspace();
    assert.equal(await f.controller.loadActions({ nodes: [f.source] }), true);
    assert.match(confirmations[0], /saved active scan.*available source data/);
  } finally { f.restore(); }
});

test('active-scan callback follows successful adoption and unlocked refreshed controls', async () => {
  for (const kind of ['virtual', 'activation']) {
    const f = fixture({ virtualScans: true, pair: kind === 'activation' });
    try {
      const method = kind === 'virtual' ? 'createVirtualScan' : 'activateScan';
      f.backend[method] = async () => {
        assert.equal(f.scanChanges.length, 0);
        return virtualBundle(f);
      };
      if (kind === 'virtual') f.controller.beginVirtualScan();
      assert.equal(await (kind === 'virtual' ? f.controller.createVirtualScan() : f.controller.activateScan('virtual')), true);
      assert.equal(f.scanChanges.length, 1);
      assert.equal(f.scanChanges[0], f.getCurrent());
      assert.ok(f.calls.indexOf('install') < f.calls.indexOf('active-scan-changed'));
      assert.ok(f.calls.indexOf('dispose') < f.calls.indexOf('active-scan-changed'));
      assert.equal(f.elements.get('active-scan').value, 'virtual');
      assert.equal(f.elements.get('active-scan').disabled, false);
    } finally { f.restore(); }
  }
});

test('failed, stale, fenced and runtime-locked active-scan changes cannot trigger evaluation', async () => {
  for (const outcome of ['rejected', 'stale', 'unavailable', 'runtime-locked']) {
    const f = fixture({ virtualScans: true });
    try {
      f.backend.createVirtualScan = async () => {
        if (outcome === 'rejected') throw Object.assign(Error('Rejected'), { code: 'invalid_reduction' });
        if (outcome === 'stale') f.stale();
        if (outcome === 'unavailable') throw Object.assign(Error('Reply lost'), { code: 'accepted_workspace_unavailable' });
        if (outcome === 'runtime-locked') f.controller.lockRuntime();
        return virtualBundle(f);
      };
      f.controller.beginVirtualScan();
      await f.controller.createVirtualScan();
      assert.deepEqual(f.scanChanges, []);
    } finally { f.restore(); }
  }
  const f = fixture({ sourceImport: true, sourceReplacement: true });
  try {
    f.backend.replaceScan = async () => f.backend.loadWorkspace();
    assert.equal(await f.controller.replaceScan({ name: 'aligned.ply' }), true);
    assert.deepEqual(f.scanChanges, [], 'The existing aligned-replacement evaluation policy stays unchanged');
  } finally { f.restore(); }
});
