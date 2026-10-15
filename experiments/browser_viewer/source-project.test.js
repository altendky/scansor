import assert from 'node:assert/strict';
import test from 'node:test';
import { createSourceProjectController } from './source-project.js';

// Small DOM port for controller lifecycle tests; actual markup, File input,
// rendering, focus, and native requests are covered by source-project.spec.js.
function fixture({ sourceImport = false, sourceActions = false, empty = false } = {}) {
  const previous = { document: globalThis.document, window: globalThis.window };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { disabled: false, hidden: true, inert: false, value: '', files: [], listeners: {},
      append() {}, setAttribute() {}, addEventListener(name, listener) { this.listeners[name] = listener; },
      showModal() { this.open = true; }, close() { this.open = false; }, click() { this.clicked = true; } });
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, createElement: () => element(Symbol()),
    querySelectorAll: selector => selector.startsWith('main,') ? [element('main')] : [] };
  globalThis.window = { confirm: () => true };
  const source = { id: 'scan', operation: 'source', source_sha256: 'source', reference_sha256: 'reference' };
  let state = { generation: sourceImport || sourceActions ? 1 : undefined, token: 'before',
    recipe: { nodes: empty ? [] : [source] }, metadata: {} };
  const calls = [], busy = [], installed = [], messages = [], warnings = [];
  let proofCurrent = true;
  const graph = { token: 'after', recipe: { nodes: [source] } };
  const bundle = { metadata: {}, positions: new ArrayBuffer(0), indices: new ArrayBuffer(0) };
  const backend = { capabilities: { sourceImport, sourceActions },
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
    async resetProject() { throw Error('Unsupported reset must not be invoked'); },
  };
  const controller = createSourceProjectController({ backend, getCurrent: () => state,
    canTransition: () => !busy.at(-1), setTransitionBusy: value => busy.push(value),
    prepareTransition: () => ({ isCurrent: () => proofCurrent, finish: () => calls.push('finish') }),
    prepareWorkspace(value) {
      const install = () => {
        calls.push('install'); installed.push(value);
        state = { ...state, token: value.graph.token, recipe: value.graph.recipe };
      };
      install.dispose = () => calls.push('dispose');
      return install;
    }, inspectSource() {}, status: (...message) => messages.push(message), onActionsLoaded: value => {
      assert.equal(busy.at(-1), false, 'Auto and warning callbacks run after the transition lock is released');
      warnings.push(value);
    } });
  return { controller, backend, calls, busy, installed, elements, messages, warnings, source,
    advance: () => { state = { ...state, token: 'changed' }; },
    stale: () => { proofCurrent = false; },
    restore() { globalThis.document = previous.document; globalThis.window = previous.window; } };
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
