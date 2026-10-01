import assert from 'node:assert/strict';
import test from 'node:test';
import { renderActionTree } from './action-tree.js';

// This is deliberately just the DOM surface used by the tree renderer, not a
// browser emulator. Native drag behavior still requires real-browser verification.
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.classes = new Set();
    this.dataset = new Proxy({}, { set(target, key, value) { target[key] = String(value); return true; } });
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      contains: (name) => this.classes.has(name),
      toggle: (name, force) => {
        const enabled = force ?? !this.classes.has(name);
        if (enabled) this.classes.add(name); else this.classes.delete(name);
        return enabled;
      },
    };
    this.clientHeight = 30;
  }
  set className(value) { this.classes = new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(' '); }
  setAttribute(name, value) {
    if (name === 'class') this.className = value;
    this.attributes.set(name, String(value));
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  append(...children) {
    for (const child of children) { child.parentElement = this; this.children.push(child); }
  }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  matches(selector) {
    return selector.split(',').some((part) => {
      const value = part.trim();
      return value.startsWith('.') ? this.classes.has(value.slice(1)) : this.tagName === value;
    });
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  contains(element) {
    return element === this || this.children.some((child) => child.contains(element));
  }
  getBoundingClientRect() { return { top: 100 }; }
  focus() { globalThis.document.activeElement = this; }
}

function event(target, options = {}) {
  return {
    target, clientY: 105, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; },
    dataTransfer: { setData() {}, setDragImage() {} },
    ...options,
  };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const source = { id: 'source', label: 'Scan', operation: 'source' };
const cylinder = { id: 'wall', label: 'Wall', operation: 'fit', kind: 'cylinder', selections: [] };
const plane = { id: 'shoulder', label: 'Shoulder', operation: 'fit', kind: 'plane', selections: [] };
const owner = { id: 'batch', label: 'Build faces', operation: 'build_faces',
  surfaces: [{ feature: 'wall' }, { feature: 'shoulder' }], reused_faces: [], reused_intersections: [] };
const edge = { id: 'edge', label: 'Shared edge', operation: 'surface_intersection',
  first: { feature: 'wall' }, second: { feature: 'shoulder' }, managed_by: 'batch', managed_key: 'edge' };
const face = { id: 'face', label: 'Wall face', operation: 'trimmed_face', surface: { feature: 'wall' },
  boundaries: [{ intersection: 'edge', keep: 'positive' }], managed_by: 'batch', managed_key: 'face' };
const point = (id, group_id = null) => ({ id, label: id, operation: 'point', group_id });
const base = [source, cylinder, plane, owner, edge, face, point('other')];

function fixture(nodes = base, groups = []) {
  globalThis.document = {
    createElement: (tag) => new Element(tag),
    createElementNS: (_namespace, tag) => new Element(tag),
    activeElement: null,
  };
  renderActionTree.expandedManaged = new Set();
  renderActionTree.expandedTargets = new Set();
  renderActionTree.collapsedGroups = new Set();
  const state = { nodes, groups, moves: [], announcements: [], locked: false, list: new Element('ul') };
  state.render = () => renderActionTree(state.list, {
    nodes: state.nodes, groups: state.groups, selected: new Set(), states: {}, errors: {},
    locked: () => state.locked, select() {},
    move: async (next) => { state.moves.push(next); state.nodes = next; state.render(); return true; },
    announce: (message, error) => state.announcements.push({ message, error }),
  });
  state.grip = (key) => state.list.querySelectorAll('.action-grip')
    .find((element) => element.dataset.reorderKey === key);
  state.row = (key) => state.grip(key)?.parentElement;
  state.ids = () => state.nodes.map((node) => node.id);
  state.render();
  return state;
}

test('managed and organizational headers expose whole-block drop bounds collapsed or expanded', () => {
  const state = fixture([...base.slice(0, 3), { ...owner, group_id: 'group' }, edge, face,
    point('member', 'group'), point('other')], [{ id: 'group', label: 'Geometry' }, { id: 'empty', label: 'Empty' }]);
  for (const expanded of [false, true]) {
    renderActionTree.expandedManaged = expanded ? new Set(['batch']) : new Set();
    renderActionTree.collapsedGroups = expanded ? new Set() : new Set(['group']);
    state.render();
    for (const [key, start, end] of [['batch', '3', '6'], ['group', '3', '7']]) {
      const row = state.row(key), grip = state.grip(key);
      assert.equal(row.tagName, 'summary');
      assert.equal(row.classList.contains('action-drop-target'), true);
      assert.equal(row.dataset.dropStart, start);
      assert.equal(row.dataset.dropEnd, end);
      assert.equal(grip.draggable, true);
      const click = event(grip);
      grip.onclick(click);
      assert.equal(click.prevented && click.stopped, true);
    }
    assert.equal(state.grip('empty').disabled, true);
    assert.match(state.grip('empty').title, /no actions/);
  }
  assert.equal(state.list.querySelectorAll('.action-grip-placeholder').every((grip) => grip.disabled), true);
});

test('keyboard crosses a whole managed block and restores the new grip focus', async () => {
  const state = fixture();
  const key = event(state.grip('batch'), { key: 'ArrowDown' });
  state.grip('batch').onkeydown(key);
  await settle();
  assert.equal(key.prevented && key.stopped, true);
  assert.deepEqual(state.ids(), ['source', 'wall', 'shoulder', 'other', 'batch', 'edge', 'face']);
  assert.equal(document.activeElement, state.grip('batch'));
  assert.equal(state.list.getAttribute('aria-busy'), null);
});

test('organizational group keyboard movement carries its managed subtree and other members', async () => {
  const state = fixture([...base.slice(0, 3), { ...owner, group_id: 'group' }, edge, face,
    point('member', 'group'), point('other')], [{ id: 'group', label: 'Geometry' }]);
  state.grip('group').onkeydown(event(state.grip('group'), { key: 'ArrowDown' }));
  await settle();
  assert.deepEqual(state.ids(), ['source', 'wall', 'shoulder', 'other', 'batch', 'edge', 'face', 'member']);
  assert.equal(document.activeElement, state.grip('group'));
});

test('ordinary rows remain drop targets and dragging an owner after one carries every child', async () => {
  const state = fixture();
  const target = state.row('other');
  assert.equal(target.classList.contains('action-drop-target'), true);
  state.grip('batch').ondragstart(event(state.grip('batch')));
  const over = event(target, { clientY: 129 });
  state.list.ondragover(over);
  assert.equal(target.classList.contains('drop-after'), true);
  assert.equal(over.dataTransfer.dropEffect, 'move');
  state.list.ondrop(event(target));
  await settle();
  assert.deepEqual(state.ids(), ['source', 'wall', 'shoulder', 'other', 'batch', 'edge', 'face']);
  assert.equal(document.activeElement, state.grip('batch'));
});

test('ordinary blocks can drop before a collapsed managed owner without splitting it', async () => {
  const state = fixture();
  const target = state.row('batch');
  state.grip('other').ondragstart(event(state.grip('other')));
  state.list.ondragover(event(target, { clientY: 101 }));
  assert.equal(target.classList.contains('drop-before'), true);
  state.list.ondrop(event(target));
  await settle();
  assert.deepEqual(state.ids(), ['source', 'wall', 'shoulder', 'other', 'batch', 'edge', 'face']);
});

test('invalid highlights clear when drag crosses a generated scope or leaves the list', async () => {
  const state = fixture();
  const invalid = state.row('shoulder');
  state.grip('batch').ondragstart(event(state.grip('batch')));
  const over = event(invalid);
  state.list.ondragover(over);
  assert.equal(invalid.classList.contains('drop-invalid'), true);
  assert.equal(over.dataTransfer.dropEffect, 'none');
  const generatedTarget = state.row('batch/');
  state.list.ondragover(event(generatedTarget));
  assert.equal(invalid.classList.contains('drop-invalid'), false);
  state.list.ondrop(event(generatedTarget));
  await settle();
  assert.equal(state.moves.length, 0);
  state.list.ondragover(event(state.row('other'), { clientY: 129 }));
  state.list.ondragleave(event(state.list, { relatedTarget: new Element('div') }));
  assert.equal(state.row('other').classList.contains('drop-after'), false);
  state.list.ondrop(event(state.list));
  await settle();
  assert.equal(state.moves.length, 0);
});

test('busy locks prevent keyboard, drag startup, and committing a prepared drop', async () => {
  const state = fixture();
  state.locked = true;
  state.grip('batch').onkeydown(event(state.grip('batch'), { key: 'ArrowDown' }));
  const start = event(state.grip('batch'));
  state.grip('batch').ondragstart(start);
  assert.equal(start.prevented, true);
  await settle();
  assert.equal(state.moves.length, 0);
  state.locked = false;
  state.grip('batch').ondragstart(event(state.grip('batch')));
  state.list.ondragover(event(state.row('other'), { clientY: 129 }));
  state.locked = true;
  state.list.ondrop(event(state.row('other')));
  await settle();
  assert.equal(state.moves.length, 0);
  assert.match(state.announcements.at(-1).message, /Wait/);
});

test('generated target headers reorder sibling blocks only, preserving input/fit order', async () => {
  const selections = ['reference', 'first', 'second'].map((id) =>
    ({ id, label: id, operation: 'selection', source: 'source' }));
  const reuse = { id: 'reuse', label: 'Reuse', operation: 'feature_reuse', fits: ['wall'],
    lineage: [], reference_selection: 'reference', target_selections: ['first', 'second'] };
  const outputs = ['first', 'second'].flatMap((target) => [
    { id: `${target}-selection`, label: `${target} observations`, operation: 'reuse_selection',
      reuse: 'reuse', fit: 'wall', source_selection: 'reference', target_selection: target,
      managed_by: 'reuse', managed_key: `selection/${target}` },
    { id: `${target}-fit`, label: `${target} fit`, operation: 'fit', kind: 'cylinder',
      selections: [`${target}-selection`], managed_by: 'reuse', managed_key: `fit/${target}` },
  ]);
  const state = fixture([source, ...selections, cylinder, reuse, ...outputs, point('other')]);
  assert.equal(state.row('reuse/first').dataset.dropScope, 'reuse');
  state.grip('reuse/first').onkeydown(event(state.grip('reuse/first'), { key: 'ArrowDown' }));
  await settle();
  assert.deepEqual(state.ids().slice(5), ['reuse', 'second-selection', 'second-fit',
    'first-selection', 'first-fit', 'other']);
  assert.equal(document.activeElement, state.grip('reuse/first'));
  state.grip('reuse/first').ondragstart(event(state.grip('reuse/first')));
  const outside = event(state.row('other'), { clientY: 129 });
  state.list.ondragover(outside);
  assert.equal(outside.prevented, false);
  state.list.ondrop(event(state.row('other')));
  await settle();
  assert.equal(state.moves.length, 1);
});

test('dropping on an invalid dependency boundary never mutates actions', async () => {
  const state = fixture();
  const original = state.nodes;
  state.grip('batch').ondragstart(event(state.grip('batch')));
  state.list.ondragover(event(state.row('shoulder')));
  state.list.ondrop(event(state.row('shoulder')));
  await settle();
  assert.equal(state.nodes, original);
  assert.equal(state.moves.length, 0);
  assert.match(state.announcements.at(-1).message, /needs Shoulder earlier/);
  assert.equal(state.row('shoulder').classList.contains('drop-invalid'), false);
});
