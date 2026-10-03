import assert from 'node:assert/strict';
import test from 'node:test';
import { actionDescription, featureIcon, featureTreePresentation, iconPickerIndex, renderIconPicker, renderActionTree } from './action-tree.js';
import { fitQualities } from './residual-display.js';

// This is deliberately just the DOM surface used by the tree renderer, not a
// browser emulator. Native drag behavior still requires real-browser verification.
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.classes = new Set();
    this.style = {};
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
    if (selector === ':popover-open') return !!this.popoverOpen;
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
  getBoundingClientRect() { return { top: 100, left: 20, bottom: 130, width: 280, height: 30 }; }
  focus() { globalThis.document.activeElement = this; }
  showPopover() { this.popoverOpen = true; }
  hidePopover() { this.popoverOpen = false; }
  scrollIntoView(options) { this.scrollRequest = options; }
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
  const state = { nodes, groups, moves: [], announcements: [], contexts: [], inspections: [], groupEdits: [], groupRemovals: [], selected: new Set(), qualities: {}, states: {}, errors: {}, locked: false,
    list: new Element('ul') };
  state.render = () => renderActionTree(state.list, {
    nodes: state.nodes, groups: state.groups, selected: state.selected, states: state.states, errors: state.errors,
    qualities: state.qualities,
    locked: () => state.locked, select: (id) => state.inspections.push(id),
    move: async (next) => { state.moves.push(next); state.nodes = next; state.render(); return true; },
    announce: (message, error) => state.announcements.push({ message, error }),
    contextMenu: (id, position) => state.contexts.push({ id, position }),
    editGroup: (id) => state.groupEdits.push(id),
    removeGroup: (id) => state.groupRemovals.push(id),
  });
  state.grip = (key) => state.list.querySelectorAll('.action-grip')
    .find((element) => element.dataset.reorderKey === key);
  state.row = (key) => state.grip(key)?.parentElement;
  state.ids = () => state.nodes.map((node) => node.id);
  state.render();
  return state;
}

test('ordinary and managed-owner rows expose pointer and keyboard context menus', () => {
  const state = fixture();
  for (const id of ['wall', 'batch', 'edge']) {
    const row = state.list.querySelectorAll('.action-select, .managed-owner-summary')
      .find((element) => element.dataset.actionId === id),
      pointer = event(row, { currentTarget: row, clientX: 50, clientY: 115 });
    row.oncontextmenu(pointer);
    assert.equal(pointer.prevented && pointer.stopped, true);
    assert.deepEqual(state.contexts.at(-1), { id, position: { x: 50, y: 115 } });
    for (const keys of [{ key: 'F10', shiftKey: true }, { key: 'ContextMenu' }]) {
      const keyboard = event(row, { currentTarget: row, ...keys });
      row.onkeydown(keyboard);
      assert.equal(keyboard.prevented && keyboard.stopped, true);
      assert.deepEqual(state.contexts.at(-1), { id, position: { x: 20, y: 130 } });
    }
  }
});

test('context menus are blocked while evaluation or a tree mutation is running', () => {
  const state = fixture(),
    row = state.list.querySelectorAll('.action-select')[0];
  for (const ariaBusy of [false, true]) {
    state.locked = !ariaBusy;
    state.list.setAttribute('aria-busy', String(ariaBusy));
    row.oncontextmenu(event(row));
    row.onkeydown(event(row, { key: 'F10', shiftKey: true }));
  }
  assert.deepEqual(state.contexts, []);
});

test('evaluation locks mutation controls but leaves feature inspection and expansion available', async () => {
  const state = fixture([...base, point('group-member', 'group')], [{ id: 'group', label: 'Group' }]);
  state.locked = true;
  state.render();
  const controls = state.row('group').querySelectorAll('.group-action');
  assert.equal(controls.every((control) => control.disabled), true);
  assert.equal(state.grip('wall').disabled, true);
  assert.equal(state.grip('wall').draggable, false);
  const inspect = state.list.querySelectorAll('.action-select')
    .find((element) => element.dataset.actionId === 'wall');
  assert.notEqual(inspect.disabled, true);
  inspect.onclick();
  state.row('batch').querySelectorAll('.group-action')[0].onclick(event(state.row('batch')));
  assert.deepEqual(state.inspections, ['wall', 'batch']);
  const details = state.row('batch').parentElement;
  details.open = true;
  details.ontoggle();
  state.render();
  assert.equal(state.row('batch').parentElement.open, true);
  for (const control of controls) control.onclick(event(control));
  state.grip('wall').onkeydown(event(state.grip('wall'), { key: 'ArrowDown' }));
  await settle();
  assert.deepEqual(state.groupEdits, []);
  assert.deepEqual(state.groupRemovals, []);
  assert.deepEqual(state.moves, []);
  state.locked = false;
  state.render();
  assert.equal(state.grip('wall').disabled, false);
  assert.equal(state.grip('wall').draggable, true);
  const unlocked = state.row('group').querySelectorAll('.group-action');
  assert.equal(unlocked.every((control) => !control.disabled), true);
  for (const control of unlocked) control.onclick(event(control));
  assert.deepEqual(state.groupEdits, ['group']);
  assert.deepEqual(state.groupRemovals, ['group']);
});

test('group mutation callbacks recheck locks even before their rows are repainted', () => {
  const state = fixture([source, point('member', 'group')], [{ id: 'group', label: 'Group' }]),
    controls = state.row('group').querySelectorAll('.group-action');
  for (const ariaBusy of [false, true]) {
    state.locked = !ariaBusy;
    state.list.setAttribute('aria-busy', String(ariaBusy));
    for (const control of controls) control.onclick(event(control));
  }
  assert.deepEqual(state.groupEdits, []);
  assert.deepEqual(state.groupRemovals, []);
});

test('selected managed owners remain visibly and accessibly selected', () => {
  const state = fixture();
  state.selected.add('batch');
  state.render();
  assert.equal(state.row('batch').classList.contains('feature-selected'), true);
  assert.match(state.row('batch').getAttribute('aria-label'), /Selected/);
  state.selected.clear();
  state.render();
  assert.equal(state.row('batch').classList.contains('feature-selected'), false);
  assert.doesNotMatch(state.row('batch').getAttribute('aria-label'), /Selected/);
});

test('managed owner and output subgroup expose child failure and causal details', () => {
  const state = fixture();
  state.states = Object.fromEntries(base.map((node) => [node.id, 'ready']));
  state.states.face = 'failed';
  state.errors.face = 'Region witness crossed a boundary; review again';
  state.render();
  assert.equal(state.states.batch, 'ready', 'presentation must not mutate execution state');
  for (const key of ['batch', 'batch/']) {
    const summary = state.row(key);
    assert.equal(summary.querySelectorAll('.state-failed').length, 1);
    assert.match(summary.title, /Wall face.*Region witness crossed/);
    assert.match(summary.getAttribute('aria-label'), /Wall face.*Region witness crossed/);
  }
});

test('nested managed outputs propagate failures through owners and organizational groups', () => {
  const nested = { ...owner, id: 'nested', label: 'Nested build', managed_by: 'batch', group_id: null },
    inner = { ...face, id: 'inner', label: 'Inner face', managed_by: 'nested' },
    nodes = [source, cylinder, plane, { ...owner, group_id: 'group' }, nested, inner],
    state = fixture(nodes, [{ id: 'group', label: 'Faces' }]);
  state.states = Object.fromEntries(nodes.map((node) => [node.id, 'ready']));
  state.states.inner = 'failed';
  state.errors.inner = 'Bad region';
  state.render();
  for (const key of ['nested', 'batch', 'group']) {
    assert.equal(state.row(key).querySelectorAll('.state-failed').length, 1);
    assert.match(state.row(key).title, /Inner face.*Bad region/);
  }
});

test('blocked outputs have a distinct marker and retain upstream causal errors', () => {
  const state = fixture();
  state.states = Object.fromEntries(base.map((node) => [node.id, 'ready']));
  state.states.face = 'blocked';
  state.errors.face = 'Blocked by Shoulder face: region needs review';
  state.render();
  assert.equal(state.row('batch').querySelectorAll('.state-blocked').length, 1);
  assert.match(state.row('batch').title, /Blocked.*Shoulder face/);
  const faceRow = state.list.querySelectorAll('.action-select').find((el) => el.dataset.actionId === 'face');
  assert.match(faceRow.getAttribute('aria-label'), /Blocked.*Shoulder face/);
  assert.equal(faceRow.querySelectorAll('.state-blocked').length, 1);
});

test('reused outputs affect owner presentation and recover without retained errors', () => {
  const nodes = [source, cylinder, plane, owner, edge, face, { ...owner, id: 'reuse-owner', reused_faces: ['face'] }],
    states = Object.fromEntries(nodes.map((node) => [node.id, 'ready']));
  states.face = 'failed';
  const failed = featureTreePresentation(nodes, states, { face: 'Invalid region' });
  assert.equal(failed.states['reuse-owner'], 'failed');
  assert.match(failed.errors['reuse-owner'], /Wall face.*Invalid region/);
  states.face = 'ready';
  const repaired = featureTreePresentation(nodes, states, {});
  assert.equal(repaired.states.batch, 'ready');
  assert.equal(repaired.states['reuse-owner'], 'ready');
  assert.equal(repaired.errors.batch, undefined);
});

test('defined relationships do not hide failed or blocked execution', () => {
  for (const operation of ['mirror_symmetry', 'parallel', 'equal']) {
    const node = { id: operation, label: operation, operation }, state = fixture([node]);
    for (const status of ['failed', 'blocked']) {
      state.states = { [operation]: status };
      state.errors = { [operation]: 'Upstream problem' };
      state.render();
      assert.equal(state.list.querySelectorAll(`.state-${status}`).length, 1);
      assert.doesNotMatch(actionDescription(node, status, 'Upstream problem'), /Defined/);
      assert.match(actionDescription(node, status, 'Upstream problem'), /Upstream problem/);
    }
  }
});

test('shared surface icons use the same paths as feature-tree rows', () => {
  const state = fixture(),
    button = state.list.querySelectorAll('.action-select')
      .find((element) => element.dataset.actionId === 'wall'),
    path = button.querySelectorAll('svg')[0].querySelectorAll('path')[0];
  assert.equal(featureIcon('cylinder').querySelectorAll('path')[0].getAttribute('d'),
    path.getAttribute('d'));
  assert.notEqual(featureIcon('reference_plane').querySelectorAll('path')[0].getAttribute('d'),
    featureIcon('plane').querySelectorAll('path')[0].getAttribute('d'));
});

test('quality badges summarize owned reused fits and organizational groups without double counting', () => {
  const reuse = { id: 'reuse', label: 'Reuse', operation: 'feature_reuse', group_id: 'group' },
    first = { id: 'first', label: 'First copy', operation: 'fit', kind: 'plane', selections: [],
      managed_by: 'reuse', target_selection: 'target-a' },
    second = { ...first, id: 'second', label: 'Second copy', target_selection: 'target-b' },
    all = [source, cylinder, reuse, first, second],
    state = fixture(all, [{ id: 'group', label: 'Copies' }]);
  state.qualities = fitQualities({ nodes: all }, { wall: 'ready', first: 'ready', second: 'ready' }, {
    wall: { weighted_rms: 9, ids: [1], residuals: [9] },
    first: { weighted_rms: 0.4, ids: [2], residuals: [0.4] },
    second: { weighted_rms: 0.1, ids: [3], residuals: [0.1] },
    reuse: { matches: [{ rms: 100 }] },
  }, 0.2);
  state.render();
  const ownerBadge = state.row('reuse').querySelectorAll('.fit-quality')[0],
    groupBadge = state.row('group').querySelectorAll('.fit-quality')[0],
    fitButton = state.list.querySelectorAll('.action-select').find((el) => el.dataset.actionId === 'first');
  assert.equal(ownerBadge.textContent, 'max 0.400 · 1!');
  assert.equal(groupBadge.textContent, ownerBadge.textContent);
  assert.match(ownerBadge.title, /2\/2 current fits/);
  assert.match(state.row('reuse').getAttribute('aria-label'), /max 0.400.*warning/);
  assert.equal(state.row('reuse/target-a').querySelectorAll('.fit-quality')[0].textContent, 'max 0.400 · 1!');
  assert.match(fitButton.getAttribute('aria-label'), /Generated.*RMS 0.400.*warning/);
  assert.match(fitButton.querySelectorAll('.fit-quality')[0].title, /Generated by Reuse/);
  assert.equal(fitButton.querySelectorAll('.generated-badge').length, 0);
});

const pickerChoices = [
  { value: 'datum', name: 'Shoulder', detail: 'Reference plane', icon: 'reference_plane', label: 'Shoulder — Reference plane' },
  { value: 'plane', name: 'Shoulder fit', detail: 'Plane fit', icon: 'plane', label: 'Shoulder fit — Plane fit' },
  { value: 'outer', name: 'Outer', detail: 'Cylinder fit', icon: 'cylinder', label: 'Outer — Cylinder fit' },
  { value: 'solved', name: 'Outer solved', detail: 'Cylinder fit · Joint', icon: 'cylinder', label: 'Outer solved — Cylinder fit · Joint' },
];
function pickerFixture(choices = pickerChoices) {
  fixture();
  globalThis.innerWidth = 800;
  globalThis.innerHeight = 600;
  const state = { button: new Element('button'), list: new Element('div'), choices,
    changes: [], previews: [], locked: false };
  state.list.id = 'surface-options';
  renderIconPicker(state.button, state.list, {
    choices, value: 'plane', change: (value) => state.changes.push(value), locked: () => state.locked,
    preview: (value) => state.previews.push(value),
  });
  state.key = (key, options = {}) => {
    const input = event(state.button, { key, ...options });
    state.button.onkeydown(input);
    return input;
  };
  return state;
}

test('icon picker options retain exact values, SVG icons, labels and selected state', () => {
  const state = pickerFixture();
  assert.equal(state.button.title, pickerChoices[1].label);
  assert.equal(state.button.getAttribute('aria-expanded'), 'false');
  for (const [i, option] of state.list.children.entries()) {
    assert.equal(option.getAttribute('role'), 'option');
    assert.equal(option.getAttribute('aria-selected'), String(i === 1));
    assert.equal(option.getAttribute('aria-label'), pickerChoices[i].label);
    assert.equal(option.querySelectorAll('svg').length, 1);
  }
  state.button.onclick();
  state.list.children[3].onclick();
  assert.deepEqual(state.changes, ['solved']);
  assert.equal(state.list.popoverOpen, false);
  assert.equal(document.activeElement, state.button);
});

test('icon picker keyboard navigation opens without changing the target and commits explicitly', () => {
  const state = pickerFixture();
  const down = state.key('ArrowDown');
  assert.equal(down.prevented && down.stopped, true);
  assert.equal(state.button.getAttribute('aria-expanded'), 'true');
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-1');
  assert.deepEqual(state.changes, []);
  state.key('End');
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-3');
  assert.deepEqual(state.list.children[3].scrollRequest, { block: 'nearest' });
  state.key('Home');
  state.key('ArrowUp');
  state.key('Enter');
  assert.deepEqual(state.changes, ['solved']);
  assert.equal(state.button.getAttribute('aria-activedescendant'), null);
  assert.equal(iconPickerIndex('ArrowDown', 3, 4), 0);
  assert.equal(iconPickerIndex('Home', 3, 0), -1);
});

test('picker hover and keyboard preview fits without committing and clear on dismissal', () => {
  const state = pickerFixture();
  state.button.onclick();
  assert.deepEqual(state.previews, ['plane']);
  state.key('End');
  assert.equal(state.previews.at(-1), 'solved');
  state.list.children[0].onpointermove({ movementX: 1, movementY: 0 });
  assert.equal(state.previews.at(-1), 'datum');
  assert.deepEqual(state.changes, []);
  state.key('Escape');
  assert.equal(state.previews.at(-1), null);
  state.button.onclick();
  state.list.hidePopover();
  state.list.ontoggle();
  assert.equal(state.previews.at(-1), null);
});

test('icon picker Escape, Tab and outside dismissal cancel without changing geometry', () => {
  for (const key of ['Escape', 'Tab']) {
    const state = pickerFixture();
    state.button.onclick();
    state.key('End');
    const exit = state.key(key);
    assert.equal(state.list.popoverOpen, false);
    assert.equal(exit.prevented, key === 'Escape');
    assert.deepEqual(state.changes, []);
  }
  const state = pickerFixture();
  state.button.onclick();
  state.list.hidePopover();
  state.list.ontoggle();
  assert.equal(state.button.getAttribute('aria-expanded'), 'false');
});

test('icon picker name typeahead and repeated initial letters cycle matching options', () => {
  const state = pickerFixture();
  state.key('o');
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-2');
  state.key('o');
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-3');
  state.key('u');
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-3');
  state.key('Enter');
  assert.deepEqual(state.changes, ['solved']);
});

test('a stationary pointer cannot override a keyboard-highlighted picker option', () => {
  const state = pickerFixture();
  state.key('End');
  state.list.children[0].onpointermove({ movementX: 0, movementY: 0 });
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-3');
  state.list.children[0].onpointermove({ movementX: 1, movementY: 0 });
  assert.equal(state.button.getAttribute('aria-activedescendant'), 'surface-options-0');
});

test('empty or locked icon pickers cannot open or commit changes', () => {
  const empty = pickerFixture([]);
  empty.button.onclick();
  assert.equal(empty.button.disabled, true);
  assert.equal(!!empty.list.popoverOpen, false);
  const state = pickerFixture();
  state.locked = true;
  state.button.onclick();
  assert.equal(!!state.list.popoverOpen, false);
  state.locked = false;
  state.button.onclick();
  state.locked = true;
  state.list.children[3].onclick();
  assert.deepEqual(state.changes, []);
});

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
