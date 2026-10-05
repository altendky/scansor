import assert from 'node:assert/strict';
import test from 'node:test';
import { Model, Actions } from 'flexlayout-react';
import { initialWorkspace, PERMANENT_PANELS, panelTab, unobscuredViewport } from './workspace-state.js';

test('default workspace has separate tree/editor, no toolbar tabs and optional workflow panels', () => {
  const model = Model.fromJson(initialWorkspace(['build-faces-dialog']));
  for (const id of [...PERMANENT_PANELS, 'build-faces-dialog']) assert.ok(model.getNodeById(id));
  for (const id of ['create', 'faces', 'features', 'output']) assert.equal(model.getNodeById(id), undefined);
  assert.notEqual(model.getNodeById('tree').getParent(), model.getNodeById('editor').getParent());
  assert.equal(model.getNodeById('view').getName(), 'Model');
  assert.equal(model.getNodeById('graph').getName(), 'Graph');
  assert.equal(model.getNodeById('view').getParent(), model.getNodeById('graph').getParent());
  assert.equal(model.getNodeById('relationship-dialog'), undefined);
  assert.equal(panelTab('build-faces-dialog').enableClose, true);
  assert.equal(panelTab('tree').enableClose, false);
  assert.equal(panelTab('view').enablePopout, false);
});

test('floating and serialized restoration keep stable panel identities', () => {
  const model = Model.fromJson(initialWorkspace());
  model.doAction(Actions.popoutTab('editor', 'float'));
  const restored = Model.fromJson(model.toJson());
  for (const id of PERMANENT_PANELS) assert.ok(restored.getNodeById(id));
  assert.notEqual(restored.getNodeById('editor').getLayoutId(), Model.MAIN_LAYOUT_ID);
});

const canvas = { left: 100, top: 100, right: 900, bottom: 700, width: 800, height: 600 };
test('docked panels on any side do not reduce camera framing area', () => {
  for (const panel of [
    { left: 0, right: 100, top: 100, bottom: 700 },
    { left: 900, right: 1100, top: 100, bottom: 700 },
    { left: 100, right: 900, top: 0, bottom: 100 },
  ]) assert.deepEqual(unobscuredViewport(canvas, panel), canvas);
});
test('DOMRect-like inherited coordinates survive the unobscured fallback', () => {
  const domRect = Object.create(Object.defineProperties({}, Object.fromEntries(
    Object.entries(canvas).map(([key, value]) => [key, { get: () => value }]))));
  assert.deepEqual({ ...domRect }, {});
  assert.deepEqual(unobscuredViewport(domRect, { left: 900, right: 1100, top: 100, bottom: 700 }), canvas);
  assert.deepEqual(unobscuredViewport(domRect, domRect), canvas);
});
test('floating tools leave the largest unobstructed framing rectangle', () => {
  assert.deepEqual(unobscuredViewport(canvas, { left: 600, right: 900, top: 100, bottom: 700 }),
    { left: 100, right: 600, top: 100, bottom: 700, width: 500, height: 600 });
  assert.deepEqual(unobscuredViewport(canvas, { left: 100, right: 300, top: 100, bottom: 700 }),
    { left: 300, right: 900, top: 100, bottom: 700, width: 600, height: 600 });
  assert.deepEqual(unobscuredViewport(canvas, { left: 100, right: 900, top: 500, bottom: 700 }),
    { left: 100, right: 900, top: 100, bottom: 500, width: 800, height: 400 });
  assert.deepEqual(unobscuredViewport(canvas, canvas), canvas);
});
