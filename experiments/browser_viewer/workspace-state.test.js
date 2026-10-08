import assert from 'node:assert/strict';
import test from 'node:test';
import { Model, Actions, DockLocation } from 'flexlayout-react';
import { initialWorkspace, PERMANENT_PANELS, panelTab, unobscuredViewport } from './workspace-state.js';
import { createWorkspaceModel } from './workspace-model.js';

test('default workspace has tree/model/graph with optional Edit and no toolbar tabs', () => {
  const model = Model.fromJson(initialWorkspace());
  for (const id of PERMANENT_PANELS) assert.ok(model.getNodeById(id));
  for (const id of ['create', 'faces', 'features', 'output']) assert.equal(model.getNodeById(id), undefined);
  assert.equal(model.getNodeById('editor'), undefined);
  assert.equal(model.getNodeById('view').getName(), 'Model');
  assert.equal(model.getNodeById('graph').getName(), 'Graph');
  assert.equal(model.getNodeById('view').getParent(), model.getNodeById('graph').getParent());
  assert.equal(model.getNodeById('relationship-dialog'), undefined);
  assert.equal(panelTab('editor').enableClose, true);
  assert.equal(panelTab('editor').name, 'Edit');
  assert.equal(panelTab('tree').enableClose, false);
  assert.equal(panelTab('view').enablePopout, false);
});

test('floating and serialized restoration keep stable panel identities', () => {
  const model = Model.fromJson(initialWorkspace(['editor']));
  model.doAction(Actions.popoutTab('editor', 'float'));
  const restored = Model.fromJson(model.toJson());
  for (const id of PERMANENT_PANELS) assert.ok(restored.getNodeById(id));
  assert.notEqual(restored.getNodeById('editor').getLayoutId(), Model.MAIN_LAYOUT_ID);
});

test('auxiliary preferences retain chosen sizes while Model stays flexible', () => {
  const model = createWorkspaceModel(initialWorkspace());
  const tree = model.getNodeById('tree').getParent();
  assert.equal(tree.getPreferredWidth(), 310);
  assert.equal(model.getNodeById('view').getParent().getPreferredWidth(), undefined);
  model.doAction(Actions.updateNodeAttributes(tree.getId(), { preferredWidth: 380 }));
  model.doAction(Actions.addTab(panelTab('icon-legend-dialog'),
    model.getNodeById('view').getParent().getId(), DockLocation.RIGHT, -1));
  assert.equal(model.getNodeById('icon-legend-dialog').getParent().getPreferredWidth(), 350);
  model.doAction(Actions.deleteTab('icon-legend-dialog'));
  assert.equal(tree.getPreferredWidth(), 380);
});

test('a new vertical auxiliary split receives a native height preference', () => {
  const model = createWorkspaceModel(initialWorkspace(['icon-legend-dialog']));
  model.doAction(Actions.moveNode('icon-legend-dialog', model.getNodeById('view').getParent().getId(),
    DockLocation.BOTTOM, -1));
  assert.equal(model.getNodeById('icon-legend-dialog').getParent().getPreferredHeight(), 250);
  for (let node = model.getNodeById('view').getParent(); node; node = node.getParent()) {
    assert.equal(node.getPreferredWidth(), undefined);
    assert.equal(node.getPreferredHeight(), undefined);
  }
});

test('Model joining a preferred auxiliary tabset makes that branch flexible', () => {
  const model = createWorkspaceModel(initialWorkspace(['icon-legend-dialog']));
  const target = model.getNodeById('icon-legend-dialog').getParent();
  model.doAction(Actions.moveNode('view', target.getId(), DockLocation.CENTER, -1));
  assert.equal(model.getNodeById('view').getParent(), target);
  assert.equal(target.getPreferredWidth(), undefined);
  assert.equal(model.getNodeById('graph').getParent().getPreferredWidth(), 350);
});

test('weight-only saved allocations become preferences before workflow omission', () => {
  const json = initialWorkspace(['icon-legend-dialog']);
  for (const tabset of json.layout.children[0].children) delete tabset.preferredWidth;
  const model = createWorkspaceModel(json, { width: 1440, height: 1000 });
  const tree = model.getNodeById('tree').getParent();
  const expected = (1440 - 2 * model.getSplitterSize()) * 24 / 128;
  assert.equal(tree.getPreferredWidth(), expected);
  model.doAction(Actions.deleteTab('icon-legend-dialog'));
  assert.equal(tree.getPreferredWidth(), expected);
  assert.equal(model.getNodeById('view').getParent().getPreferredWidth(), undefined);
});

test('restoration in a small window retains saved preferences', () => {
  const model = createWorkspaceModel(initialWorkspace(['icon-legend-dialog']));
  const restored = createWorkspaceModel(model.toJson(), { width: 600, height: 400 });
  assert.equal(restored.getNodeById('tree').getParent().getPreferredWidth(), 310);
  assert.equal(restored.getNodeById('icon-legend-dialog').getParent().getPreferredWidth(), 350);
});

test('cross-axis child preferences cannot make a Model ancestor retain its size', () => {
  const tabset = (id, attributes = {}) => ({ type: 'tabset', id: id + '-tabset', ...attributes,
    children: [panelTab(id)] });
  const model = createWorkspaceModel({ global: { rootOrientationVertical: true }, layout: {
    type: 'row', children: [tabset('tree', { preferredHeight: 100 }), { type: 'row', children: [
      tabset('view', { preferredHeight: 250 }),
      tabset('editor', { preferredWidth: 350, preferredHeight: 250 }),
    ] }],
  } });
  assert.equal(model.getNodeById('view').getParent().getPreferredHeight(), undefined);
  assert.equal(model.getNodeById('editor').getParent().getPreferredHeight(), 250);
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
