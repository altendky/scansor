import assert from 'node:assert/strict';
import test from 'node:test';
import { createSourceProjectController } from './source-project.js';

// Small DOM port for controller lifecycle tests; actual markup, File input,
// rendering, focus, and native requests are covered by source-project.spec.js.
function fixture({ sourceImport = false, empty = false } = {}) {
  const previous = { document: globalThis.document, window: globalThis.window };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { disabled: false, hidden: true, inert: false, value: '',
      append() {}, setAttribute() {}, addEventListener() {} });
    return elements.get(id);
  };
  globalThis.document = { getElementById: element, createElement: () => element(Symbol()),
    querySelectorAll: selector => selector.startsWith('main,') ? [element('main')] : [] };
  globalThis.window = { confirm: () => true };
  const source = { id: 'scan', operation: 'source' };
  let state = { generation: sourceImport ? 1 : undefined, token: 'before',
    recipe: { nodes: empty ? [] : [source] }, metadata: {} };
  const calls = [], busy = [], installed = [], messages = [];
  let proofCurrent = true;
  const graph = { token: 'after', recipe: { nodes: [source] } };
  const bundle = { metadata: {}, positions: new ArrayBuffer(0), indices: new ArrayBuffer(0) };
  const backend = { capabilities: { sourceImport },
    async getExampleRecipe() { calls.push('recipe'); return graph.recipe; },
    async replaceRecipe(payload) { calls.push(['replace', payload]); return graph; },
    async selectExample(payload) { calls.push(['example', payload]); },
    async loadWorkspace(options) { calls.push(['workspace', options]); return sourceImport ? { ...bundle, graph } : bundle; },
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
    }, inspectSource() {}, status: (...message) => messages.push(message) });
  return { controller, backend, calls, busy, installed, elements, messages,
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
