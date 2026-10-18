import test from 'node:test';
import assert from 'node:assert/strict';
import { createSelectionOwnedEditor, createVolumePreviewReader, ownedKey,
  readVolumeOptions, referenceKey, volumeAuthorPayload } from './selection-owned-editor.js';

class Element {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.children = []; this.value = ''; this.attributes = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { this.focused = true; }
}
const reference = { selection: 'selection', entry: 'original', volume: 'saved' };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function fixture() {
  const doc = { createElement: tag => new Element(tag, doc) }, root = doc.createElement('section'),
    scan = { id: 'scan', operation: 'source', source_sha256: 'a'.repeat(64), reference_sha256: 'b'.repeat(64) },
    binding = { source_sha256: scan.source_sha256, reference_sha256: scan.reference_sha256, vertices: 12, descriptor: { filename: 'scan.ply' } },
    volume = { id: 'saved', label: 'Saved support', kind: 'mesh', options: { inward_thickness: null }, archive: { sha256: 'digest' } },
    entry = { id: 'original', scan: binding, mode: 'painted', ids: [1, 2], depth: 'through_all', volumes: [volume] },
    selection = { id: 'selection', operation: 'selection', source: 'scan', entries: [entry] },
    state = { graph: { token: 'before', recipe: { nodes: [scan, selection] }, derived: {}, diagnostics: {} }, metadata: { vertices: 12 }, generation: 1 },
    calls = [], publications = [];
  const backend = {
    async getSelectionVolumeChoices(payload) { calls.push(['choices', payload]); return { ...payload,
      surfaces: [{ reference: { feature: 'fit' }, label: 'Plane', kind: 'plane', eligible: true }],
      volumes: [{ reference, label: 'Saved support', kind: 'mesh', source_label: 'scan.ply', archive_sha256: 'digest' }] }; },
    async getSelectionVolumePreview(payload) { calls.push(['preview', payload]); return previewResponse(payload); },
  };
  for (const method of ['authorSelectionVolume', 'chooseSelectionVolume', 'setSelectionVolumeOptions', 'deleteSelectionVolume']) backend[method] = async payload => {
    calls.push([method, payload]); state.graph = { ...state.graph, token: 'after' }; return state.graph;
  };
  const editor = createSelectionOwnedEditor({ root, backend, getCurrent: () => state,
    onGraph: graph => { state.graph = graph; }, onOpen: context => calls.push(['open', context]),
    onPreview: value => publications.push(value), newId: () => 'new-volume', confirmDelete: () => true });
  const all = node => [node, ...node.children.flatMap(all)],
    byId = id => all(root).find(node => node.id === id),
    button = text => all(root).find(node => node.tagName === 'button' && node.textContent === text),
    text = () => all(root).map(node => node.textContent || '').join(' ');
  return { root, state, backend, calls, publications, editor, entry, selection, byId, button, text };
}
function previewResponse(payload) {
  return { ...payload, kind: 'mesh', label: 'Saved support', freshness: 'current',
    options: {}, defaults: { inward_thickness: 0.5 }, resolved: { inward_thickness: 0.5 },
    total_cells: 2, returned_cells: 1, next_offset: payload.offset + 1 === 2 ? null : payload.offset + 1,
    positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], normals: [0, 0, 1, 0, 0, 1, 0, 0, 1],
    bounds: { min: [0, 0, 0], max: [1, 1, 0] } };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('owned identity stays separate from feature identity and reference property order', () => {
  assert.equal(ownedKey({ selection: 's', entry: 'e' }), 'selection-entry/s/e');
  assert.equal(ownedKey({ selection: 's', entry: 'e', volume: 'v' }), 'selection-volume/s/e/v');
  assert.equal(ownedKey({ selection: 's' }), null);
  assert.equal(referenceKey(reference), referenceKey({ volume: 'saved', entry: 'original', selection: 'selection' }));
});

test('Auto controls serialize independent nulls, overrides and invalid inputs', () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original', volume: 'saved' });
  assert.equal(f.byId('owned-inward_thickness-mode').value, 'auto');
  assert.equal(f.byId('owned-inward_thickness').disabled, true);
  const controls = Object.fromEntries(['inward_thickness', 'outward_thickness', 'edge_padding', 'normal_angle_degrees']
    .map(name => [name, { mode: f.byId(`owned-${name}-mode`), input: f.byId(`owned-${name}`) }]));
  assert.deepEqual(readVolumeOptions(controls), { inward_thickness: null, outward_thickness: null, edge_padding: null, normal_angle_degrees: null });
  controls.inward_thickness.mode.value = 'override'; controls.inward_thickness.input.value = '0.25';
  controls.normal_angle_degrees.mode.value = 'override'; controls.normal_angle_degrees.input.value = '45';
  assert.deepEqual(readVolumeOptions(controls), { inward_thickness: 0.25, outward_thickness: null, edge_padding: null, normal_angle_degrees: 45 });
  controls.normal_angle_degrees.input.value = '0'; assert.throws(() => readVolumeOptions(controls), /valid normal/);
  controls.normal_angle_degrees.mode.value = 'auto'; assert.equal(readVolumeOptions(controls).normal_angle_degrees, null);
});

test('authoring payload makes Create and Replace mutually exclusive and requires explicit analytic fit', () => {
  const base = { token: 't', selection: 'selection', entry: 'current', volume: 'new', label: ' support ', kind: 'mesh', options: {} };
  assert.deepEqual(volumeAuthorPayload({ ...base, mode: 'create', target: reference }), { ...base, mode: 'create', label: 'support' });
  const replacement = volumeAuthorPayload({ ...base, mode: 'replace', target: reference });
  assert.equal(Object.hasOwn(replacement, 'volume'), false); assert.deepEqual(replacement.target, reference);
  assert.throws(() => volumeAuthorPayload({ ...base, mode: 'replace' }), /Choose an existing/);
  assert.throws(() => volumeAuthorPayload({ ...base, mode: 'replace', target: { ...reference, selection: 'other' } }), /this Selection/);
  assert.throws(() => volumeAuthorPayload({ ...base, mode: 'create', kind: 'plane' }), /fitted surface/);
});

test('generated entry requires an explicit choice even with one candidate and sends the exact typed reference', async () => {
  const f = fixture(); f.entry.mode = 'generated'; delete f.entry.ids; f.entry.source_volume = null;
  f.editor.show({ selection: 'selection', entry: 'original' }); await settle();
  assert.match(f.text(), /\? Choose source volume/);
  assert.equal(f.calls.filter(([name]) => name === 'chooseSelectionVolume').length, 0);
  f.byId('owned-source-volume').value = referenceKey(reference);
  await f.button('Apply source volume').onclick(); await settle();
  assert.deepEqual(f.calls.find(([name]) => name === 'chooseSelectionVolume')[1],
    { token: 'before', selection: 'selection', entry: 'original', source_volume: reference });
  assert.match(f.text(), /read-only/);
});

test('refresh preserves live option drafts and never recaptures or changes IDs', async () => {
  const f = fixture(), context = { selection: 'selection', entry: 'original', volume: 'saved' };
  f.editor.show(context); await settle();
  const control = f.byId('owned-inward_thickness');
  control.value = '0.75'; f.byId('owned-inward_thickness-mode').value = 'override';
  f.state.graph = { ...f.state.graph, token: 'new-token' }; f.editor.show(context); await settle();
  assert.equal(f.byId('owned-inward_thickness'), control); assert.equal(control.value, '0.75');
  assert.deepEqual(f.entry.ids, [1, 2]);
  assert.equal(f.calls.filter(([name]) => name === 'authorSelectionVolume').length, 0);
  assert.match(f.text(), /Auto: 0.5; resolved: 0.5/);
});

test('explicit capture carries independent live option intent from the saved volume inspector', async () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original', volume: 'saved' }); await settle();
  f.byId('owned-inward_thickness-mode').value = 'override'; f.byId('owned-inward_thickness').value = '0.75';
  f.byId('owned-edge_padding-mode').value = 'override'; f.byId('owned-edge_padding').value = '0.1';
  await f.button('Create / replace volume…').onclick(); await settle();
  assert.equal(f.byId('owned-inward_thickness-mode').value, 'override');
  assert.equal(f.byId('owned-inward_thickness').value, '0.75');
  assert.equal(f.byId('owned-edge_padding').value, '0.1');
  assert.equal(f.byId('owned-outward_thickness-mode').value, 'auto');
  await f.button('Capture volume').onclick(); await settle();
  const payload = f.calls.find(([name]) => name === 'authorSelectionVolume')[1];
  assert.deepEqual(payload.options, { inward_thickness: 0.75, outward_thickness: null,
    edge_padding: 0.1, normal_angle_degrees: null });
});

test('invalid capture override retains the exact inspector control for correction', async () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original', volume: 'saved' }); await settle();
  const control = f.byId('owned-inward_thickness');
  f.byId('owned-inward_thickness-mode').value = 'override'; control.value = '-1';
  await f.button('Create / replace volume…').onclick();
  assert.equal(f.byId('owned-inward_thickness'), control); assert.equal(control.value, '-1');
  assert.match(f.text(), /Enter a valid inward thickness/);
  assert.ok(f.button('Apply volume controls')); assert.equal(f.button('Capture volume'), undefined);
  assert.equal(f.calls.some(([name]) => name === 'authorSelectionVolume'), false);
});

test('creating a volume uses current entry and leaves create/replace explicit', async () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original' }); f.editor.author(); await settle();
  f.byId('owned-volume-label').value = 'Mesh patch';
  await f.button('Capture volume').onclick(); await settle();
  const payload = f.calls.find(([name]) => name === 'authorSelectionVolume')[1];
  assert.equal(payload.mode, 'create'); assert.equal(payload.volume, 'new-volume'); assert.equal(payload.entry, 'original');
  assert.equal(Object.hasOwn(payload, 'target'), false); assert.equal(Object.hasOwn(payload, 'fit'), false);
});

test('preview pages expose their partial bounds and clear rendered resources on context change', async () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original', volume: 'saved' }); await settle();
  assert.match(f.text(), /Partial preview/); assert.equal(f.editor.preview.returned_cells, 1);
  await f.button('Next cells').onclick(); await settle();
  assert.equal(f.calls.filter(([name]) => name === 'preview').at(-1)[1].offset, 1);
  f.editor.show({ selection: 'selection', entry: 'original' });
  assert.equal(f.publications.at(-1), null); assert.equal(f.editor.preview, null);
});

test('late preview is rejected after token, source generation, volume or archive change', async () => {
  for (const change of [{ token: 'new' }, { generation: 2 }, { reference: { ...reference, volume: 'other' } }, { archive_sha256: 'new' }]) {
    let state = { token: 'old', generation: 1, reference, archive_sha256: 'digest' }, published = 0;
    const pending = deferred(), reader = createVolumePreviewReader({ request: () => pending.promise, current: () => state, publish: () => published++ });
    const request = reader.load(reference, 'digest'); state = { ...state, ...change };
    pending.resolve(previewResponse({ token: 'old', reference, archive_sha256: 'digest', offset: 0 }));
    assert.equal(await request, null); assert.equal(published, 0);
  }
});

test('preview rejects invalid cells, nonfinite values and impossible pagination before publishing', async () => {
  const state = { token: 't', generation: 1, reference, archive_sha256: 'digest' };
  for (const defect of [{ returned_cells: 2049 }, { positions: [NaN] }, { next_offset: 0 }, { archive_sha256: 'other' }]) {
    const reader = createVolumePreviewReader({ request: async payload => ({ ...previewResponse(payload), ...defect }),
      current: () => state, publish: () => assert.fail('Invalid preview published') });
    await assert.rejects(reader.load(reference, 'digest'), /incomplete or inconsistent/);
  }
});

test('Delete volume has an explicit reference and never invokes feature deletion', async () => {
  const f = fixture(); f.editor.show({ selection: 'selection', entry: 'original', volume: 'saved' }); await settle();
  await f.button('Delete volume…').onclick(); await settle();
  assert.deepEqual(f.calls.find(([name]) => name === 'deleteSelectionVolume')[1], { token: 'before', reference });
  assert.deepEqual(f.calls.find(([name]) => name === 'open')[1], { selection: 'selection', entry: 'original' });
  assert.deepEqual(f.selection.entries[0].ids, [1, 2]);
});

test('inactive membership stays inspectable and coverage warnings belong to the current entry', async () => {
  const f = fixture(); f.entry.scan = { ...f.entry.scan, source_sha256: 'c'.repeat(64) };
  f.state.graph.derived.selection = { coverage: { partial: true, fraction: 0.5, samples: 20 } };
  f.editor.show({ selection: 'selection', entry: 'original' }); await settle();
  assert.match(f.text(), /Inactive scan/); assert.match(f.text(), /preserved/);
  assert.doesNotMatch(f.text(), /Partial coverage warning/);
  assert.equal(f.button('Create / replace volume…'), undefined);
});

test('cycle-ineligible source volumes remain available as explicit adoption targets', async () => {
  const f = fixture(), original = f.backend.getSelectionVolumeChoices;
  f.backend.getSelectionVolumeChoices = async payload => {
    const result = await original(payload);
    result.volumes[0] = { ...result.volumes[0], eligible: false, reason: 'Authoring cycle', freshness: 'refresh_required' };
    return result;
  };
  f.entry.mode = 'generated'; delete f.entry.ids; f.entry.source_volume = reference;
  f.editor.show({ selection: 'selection', entry: 'original' }); await settle();
  assert.equal(f.byId('owned-source-volume').children[1].disabled, true);
  assert.match(f.byId('owned-source-volume').children[1].textContent, /Refresh required.*Authoring cycle/);
  f.editor.author(); await settle();
  assert.equal(f.byId('owned-author-target').children[1].disabled, false);
});

test('pending and empty authoring support explain the required action without issuing capture', () => {
  for (const mode of ['painted', 'generated']) {
    const f = fixture(); f.entry.mode = mode;
    if (mode === 'painted') f.entry.ids = [];
    else { delete f.entry.ids; f.entry.source_volume = null; }
    f.editor.show({ selection: 'selection', entry: 'original' }); f.editor.author();
    assert.match(f.text(), mode === 'painted' ? /Paint at least one/ : /Choose a source volume/);
    assert.ok(f.button('Back to selection'));
    assert.equal(f.button('Capture volume'), undefined);
    assert.equal(f.calls.some(([name]) => name === 'authorSelectionVolume'), false);
  }
});

test('partial area coverage is a warning and leaves volume actions available', () => {
  const f = fixture(); f.state.graph.derived.selection = { coverage: { partial: true, fraction: 0.5, samples: 20 } };
  f.editor.show({ selection: 'selection', entry: 'original' });
  assert.match(f.text(), /Partial coverage warning: 50.0%.*20 area samples/);
  assert.match(f.text(), /valid fits remain available/);
  assert.ok(f.button('Create / replace volume…'));
});
