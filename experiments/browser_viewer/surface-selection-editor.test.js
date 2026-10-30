import assert from 'node:assert/strict';
import test from 'node:test';
import { bindSurfaceSelectionEditor, createSurfaceSelectionPreview, surfaceSelectionDefinition, surfaceSelectionPhases,
  surfaceSelectionPolicy, localBoundariesPolicy, validateSurfaceSelectionPreview } from './surface-selection-editor.js';

const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
const definition = extra => surfaceSelectionDefinition({ seed_selection: 'seed', barriers: [],
  angle_degrees: '20', setback: '0', ...extra });
function response(context, extra = {}) {
  return { token: context.graph.token, project_generation: context.generation,
    preview: { policy: surfaceSelectionPolicy,
      counts: Object.fromEntries(Object.keys(surfaceSelectionPhases).map(phase => [phase, 2])),
      samples: Object.fromEntries(Object.keys(surfaceSelectionPhases).map(phase => [phase, [1, 3]])),
      diagnostics: {} }, ...extra };
}
function fixture({ prepare = async () => {}, request, delay = 0 } = {}) {
  const context = { generation: 3, vertices: 10, graph: { token: 'accepted', revision: 4 } };
  const published = [], validity = [], busy = [], calls = [], status = [];
  let value = definition(), active = true;
  const coordinator = createSurfaceSelectionPreview({ read: () => value, current: () => context,
    isActive: () => active, prepare, delay,
    request: (payload, options) => {
      calls.push({ payload, options });
      return request ? request(payload, options) : Promise.resolve(response(context));
    }, publish: item => published.push(item), validity: item => validity.push(item),
    busy: item => busy.push(item), status: (...args) => status.push(args) });
  return { coordinator, context, published, validity, busy, calls, status,
    set: extra => { value = definition(extra); }, close: () => { active = false; } };
}

test('surface definition validates intent and canonicalizes barriers without changing caller arrays', () => {
  const barriers = ['last', 'first', 'last'];
  assert.deepEqual(definition({ barriers }), { seed_selection: 'seed', barriers: ['first', 'last'],
    angle_degrees: 20, setback: 0, policy: surfaceSelectionPolicy, neighborhood_distance: null });
  assert.deepEqual(barriers, ['last', 'first', 'last']);
  for (const changes of [{ seed_selection: '' }, { barriers: ['seed'] }, { angle_degrees: '' },
    { angle_degrees: 0 }, { angle_degrees: 91 }, { angle_degrees: Infinity }, { setback: '' },
    { setback: -1 }, { setback: NaN }]) assert.throws(() => definition(changes));
});

test('Local boundaries accepts automatic or positive physical neighborhoods and Seed orientation clears them', () => {
  for (const value of [undefined, null, '', ' '])
    assert.equal(definition({policy: localBoundariesPolicy, neighborhood_distance: value}).neighborhood_distance, null);
  assert.equal(definition({policy: localBoundariesPolicy, neighborhood_distance: '0.125'}).neighborhood_distance, .125);
  for (const value of [0, -1, Infinity, NaN, true, 'invalid'])
    assert.throws(() => definition({policy: localBoundariesPolicy, neighborhood_distance: value}), /positive neighborhood/);
  assert.throws(() => definition({policy: 'unknown'}), /supported surface selection mode/);
  assert.equal(definition({neighborhood_distance: 4}).neighborhood_distance, null);
  assert.equal(definition({neighborhood_distance: 'invalid hidden value'}).neighborhood_distance, null);
});

test('preview samples are bounded, sorted, source-fenced and independent across display phases', () => {
  const context = fixture().context;
  assert.deepEqual(validateSurfaceSelectionPreview(response(context), context), response(context).preview);
  for (const mutate of [value => { value.token = 'obsolete'; }, value => { value.project_generation++; },
    value => { value.preview.policy = 'other'; }, value => { value.preview.counts.inset = 11; },
    value => { value.preview.counts.boundaries = 0.5; }, value => { value.preview.samples.seeds = [1, 1]; },
    value => { value.preview.samples.barriers = [-1]; }, value => { value.preview.samples.region = [10]; },
    value => { value.preview.samples.inset = [1, 2, 3]; }]) {
    const value = response(context); mutate(value);
    assert.throws(() => validateSurfaceSelectionPreview(value, context));
  }
  const excessive = response(context);
  excessive.preview.counts.inset = 3000;
  excessive.preview.samples.inset = Array.from({ length: 2049 }, (_, index) => index);
  assert.throws(() => validateSurfaceSelectionPreview(excessive, { ...context, vertices: 4096 }));
});

test('a preview must match the requested policy rather than merely another supported policy', () => {
  const context = fixture().context, seed = response(context), local = response(context);
  local.preview.policy = localBoundariesPolicy;
  assert.equal(validateSurfaceSelectionPreview(local, context, definition({policy: localBoundariesPolicy})), local.preview);
  assert.throws(() => validateSurfaceSelectionPreview(seed, context, definition({policy: localBoundariesPolicy})), /Invalid surface selection preview/);
  assert.throws(() => validateSurfaceSelectionPreview(local, context, definition()), /Invalid surface selection preview/);
});

test('a mismatched mode response never authorizes the reviewed definition', async () => {
  const f = fixture();
  f.set({policy: localBoundariesPolicy, neighborhood_distance: 2});
  await f.coordinator.preview();
  assert.equal(f.calls[0].payload.policy, localBoundariesPolicy);
  assert.equal(f.calls[0].payload.neighborhood_distance, 2);
  assert.equal(f.coordinator.reviewed, false);
  assert.equal(f.published.at(-1), null);
  assert.match(f.status.at(-1)[0], /Invalid surface selection preview/);
});

test('only a current successful preview authorizes reviewed input and sync removes obsolete geometry', async () => {
  const f = fixture();
  assert.equal(f.coordinator.reviewed, false);
  await f.coordinator.preview();
  assert.equal(f.coordinator.reviewed, true);
  assert.deepEqual(f.published.at(-1), response(f.context).preview);
  assert.deepEqual(f.busy, [true, false]);
  f.context.graph.revision++;
  assert.equal(f.coordinator.reviewed, false);
  f.coordinator.sync();
  assert.equal(f.published.at(-1), null);
  assert.equal(f.validity.at(-1), false);
});

test('newest queued preview waits for owned retirement and discards every intermediate request', async () => {
  const first = deferred();
  const f = fixture({ request: () => f.calls.length === 1 ? first.promise : Promise.resolve(response(f.context)) });
  const running = f.coordinator.preview();
  await flush();
  assert.equal(f.calls.length, 1);
  f.set({ angle_degrees: 25 });
  assert.equal(f.coordinator.preview(), running);
  f.set({ angle_degrees: 40 });
  assert.equal(f.coordinator.preview(), running);
  assert.equal(f.calls[0].options.signal.aborted, true);
  await flush();
  assert.equal(f.calls.length, 1, 'a replacement cannot overlap an unretired owned preview');
  first.resolve(response(f.context));
  await running;
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].payload.angle_degrees, 40);
  assert.equal(f.published.filter(value => value !== null).length, 1);
  assert.equal(f.coordinator.reviewed, true);
});

test('automatic changes coalesce to the newest settings and clear disables subsequent scheduling', async () => {
  const f = fixture();
  await f.coordinator.preview();
  f.set({ setback: 0.1 }); f.coordinator.changed();
  f.set({ setback: 0.2 }); f.coordinator.changed();
  await new Promise(resolve => setTimeout(resolve, 10)); await flush();
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].payload.setback, 0.2);
  f.coordinator.clear();
  f.set({ setback: 0.3 }); f.coordinator.changed();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.calls.length, 2);
  assert.equal(f.coordinator.reviewed, false);
});

test('clearing a form while preparing inputs leaves accepted evaluation alone and never starts private work', async () => {
  const preparing = deferred(), f = fixture({ prepare: () => preparing.promise });
  const running = f.coordinator.preview(); await flush();
  assert.equal(f.coordinator.busy, true);
  f.coordinator.clear(); f.close();
  preparing.resolve(); await running;
  assert.equal(f.calls.length, 0);
  assert.equal(f.coordinator.busy, false);
  assert.equal(f.coordinator.reviewed, false);
  assert(f.published.every(value => value === null));
});

test('clearing active private work aborts its owner but stays busy until retirement without publishing a late result', async () => {
  const waiting = deferred(), f = fixture({ request: () => waiting.promise });
  const running = f.coordinator.preview(); await flush();
  f.coordinator.clear();
  assert.equal(f.calls[0].options.signal.aborted, true);
  assert.equal(f.coordinator.busy, true);
  waiting.resolve(response(f.context)); await running;
  assert.equal(f.coordinator.busy, false);
  assert.equal(f.coordinator.reviewed, false);
  assert.equal(f.calls.length, 1);
  assert(f.published.every(value => value === null));
});

for (const change of ['source', 'token', 'revision', 'closed', 'definition', 'mode', 'neighborhood'])
  test(`a late preview after ${change} changes cannot publish or authorize Apply`, async () => {
    const waiting = deferred(), f = fixture({ request: () => waiting.promise });
    if (change === 'neighborhood') f.set({policy: localBoundariesPolicy, neighborhood_distance: 1});
    const running = f.coordinator.preview(); await flush();
    if (change === 'source') f.context.generation++;
    if (change === 'token') f.context.graph.token = 'new';
    if (change === 'revision') f.context.graph.revision++;
    if (change === 'closed') f.close();
    if (change === 'definition') f.set({ setback: 1 });
    if (change === 'mode') f.set({policy: localBoundariesPolicy});
    if (change === 'neighborhood') f.set({policy: localBoundariesPolicy, neighborhood_distance: 2});
    const result = response(f.context);
    if (change === 'neighborhood') result.preview.policy = localBoundariesPolicy;
    waiting.resolve(result); await running;
    assert.equal(f.coordinator.reviewed, false);
    assert(f.published.every(value => value === null));
  });

test('failed private previews clear reviewed state without changing input settings', async () => {
  let failing = false;
  const f = fixture({ request: () => failing ? Promise.reject(Error('No usable surface'))
    : Promise.resolve(response(f.context)) });
  await f.coordinator.preview(); assert(f.coordinator.reviewed);
  failing = true;
  await f.coordinator.preview();
  assert.equal(f.coordinator.reviewed, false);
  assert.equal(f.published.at(-1), null);
  assert.deepEqual(f.status.at(-1), ['No usable surface', true]);
  assert.equal(f.calls.at(-1).payload.seed_selection, 'seed');
});

class Element extends EventTarget {
  constructor(tag, document) {
    super(); this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.children = []; this.dataset = {}; this.classList = { toggle() {} };
    this.style = { setProperty() {} }; this._value = '';
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) {
    this.children = items;
    if (this.tagName === 'SELECT' && !this.multiple && !items.some(item => item.selected) && items[0])
      items[0].selected = true;
  }
  get value() { return this.tagName === 'SELECT' ? this.children.find(item => item.selected)?.value || '' : this._value; }
  set value(value) {
    this._value = String(value);
    if (this.tagName === 'SELECT') for (const option of this.children) option.selected = option.value === this._value;
  }
  get selectedOptions() { return this.children.filter(item => item.selected); }
  setAttribute(name, value) { this[name] = value; }
}

test('editor Apply needs reviewed creation settings while display phase toggles preserve that review', async () => {
  const oldWindow = globalThis.window, oldOption = globalThis.Option, ids = new Map();
  const document = { createElement: tag => new Element(tag, document), getElementById: id => ids.get(id) };
  const root = document.createElement('form');
  for (const suffix of ['seed', 'barriers', 'angle', 'setback', 'policy', 'neighborhood-distance', 'neighborhood-row', 'mode-hint',
    'preview-status', 'cancel-preview', 'diagnostics', 'legend', 'preview']) {
    const field = document.createElement(['seed', 'barriers', 'policy'].includes(suffix) ? 'select' : 'input');
    field.id = `surface-${suffix}`; field.multiple = suffix === 'barriers';
    ids.set(field.id, field);
  }
  globalThis.window = new EventTarget();
  globalThis.Option = class extends Element {
    constructor(label, value, _defaultSelected, selected) {
      super('option', document); this.textContent = label; this.value = value; this.selected = selected;
    }
  };
  ids.get('surface-policy').append(new Option('Seed orientation', surfaceSelectionPolicy),
    new Option('Local boundaries', localBoundariesPolicy));
  const context = fixture().context, previews = [], calls = [];
  context.graph.recipe = { nodes: [] };
  const editor = bindSurfaceSelectionEditor({ root, prefix: 'surface-', current: () => context,
    prepare: async () => {}, isActive: () => true, onPreview: value => previews.push(value),
    backend: { previewSurfaceSelection: async payload => {
      calls.push(payload); const result = response(context); result.preview.policy = payload.policy; return result;
    } } });
  try {
    editor.show({ id: '', ...definition({ barriers: ['stop'] }) },
      [{ id: 'seed', label: 'Seed' }, { id: 'stop', label: 'Stop' }]);
    assert.equal(ids.get('surface-policy').value, surfaceSelectionPolicy);
    assert.equal(ids.get('surface-neighborhood-row').hidden, true);
    assert.equal(ids.get('surface-neighborhood-distance').disabled, true);
    assert.match(ids.get('surface-mode-hint').textContent, /Narrow or noisy seed patches/);
    assert.throws(() => editor.definition({ reviewed: true }), /Preview the current/);
    await editor.preview();
    assert.deepEqual(editor.definition({ reviewed: true }), definition({ barriers: ['stop'] }));
    assert.deepEqual(previews.at(-1).visible, Object.keys(surfaceSelectionPhases));
    ids.get('surface-legend').children.find(button => button.dataset.previewPhase === 'boundaries').onclick();
    assert.equal(previews.at(-1).visible.includes('boundaries'), false);
    assert.doesNotThrow(() => editor.definition({ reviewed: true }));
    assert.equal(calls.length, 1);
    const angle = ids.get('surface-angle'); angle.value = '30'; angle.dispatchEvent(new Event('input'));
    assert.throws(() => editor.definition({ reviewed: true }), /Preview the current/);
    await editor.preview();
    assert.doesNotThrow(() => editor.definition({ reviewed: true }));
    angle.dispatchEvent(new Event('change'));
    assert.doesNotThrow(() => editor.definition({ reviewed: true }),
      'The same value emitted on blur must not invalidate the reviewed preview before Apply');
    assert.equal(calls.length, 2);
    const policy = ids.get('surface-policy'), neighborhood = ids.get('surface-neighborhood-distance');
    policy.value = localBoundariesPolicy; policy.dispatchEvent(new Event('change'));
    assert.equal(ids.get('surface-neighborhood-row').hidden, false);
    assert.equal(neighborhood.disabled, false);
    assert.match(ids.get('surface-mode-hint').textContent, /Gradual curvature can wrap/);
    assert.equal(editor.definition().neighborhood_distance, null);
    assert.throws(() => editor.definition({reviewed: true}), /Preview the current/);
    await editor.preview();
    assert.equal(editor.definition({reviewed: true}).policy, localBoundariesPolicy);
    neighborhood.value = '2.5'; neighborhood.dispatchEvent(new Event('input'));
    assert.throws(() => editor.definition({reviewed: true}), /Preview the current/);
    await editor.preview();
    assert.equal(editor.definition({reviewed: true}).neighborhood_distance, 2.5);
    neighborhood.dispatchEvent(new Event('change'));
    assert.doesNotThrow(() => editor.definition({reviewed: true}), 'Unchanged neighborhood blur must preserve review');
    policy.value = surfaceSelectionPolicy; policy.dispatchEvent(new Event('change'));
    assert.equal(ids.get('surface-neighborhood-row').hidden, true);
    assert.equal(neighborhood.disabled, true);
    assert.equal(editor.definition().neighborhood_distance, null, 'Switching back clears a previously authored local value');
    await editor.preview();
    assert.equal(editor.definition({reviewed: true}).neighborhood_distance, null);
    ids.get('surface-cancel-preview').onclick();
    assert.equal(previews.at(-1), null);
    assert.equal(angle.value, '30');
    assert.deepEqual(editor.definition().barriers, ['stop']);
    editor.show({id: 'legacy', seed_selection: 'seed', barriers: [], angle_degrees: 20, setback: 0},
      [{id: 'seed', label: 'Seed'}]);
    assert.equal(editor.definition({reviewed: true}).policy, surfaceSelectionPolicy);
    assert.equal(editor.definition().neighborhood_distance, null);
    neighborhood.disabled = false;
    editor.show({id: 'legacy', seed_selection: 'seed', barriers: [], angle_degrees: 20, setback: 0},
      [{id: 'seed', label: 'Seed'}]);
    assert.equal(neighborhood.disabled, true, 'Refreshing the same owner must restore inactive mode control state');
    neighborhood.disabled = false;
    editor.sync();
    assert.equal(neighborhood.disabled, true, 'Lifecycle sync must restore inactive mode control state');
  } finally {
    editor.clear(); globalThis.window = oldWindow; globalThis.Option = oldOption;
  }
});
