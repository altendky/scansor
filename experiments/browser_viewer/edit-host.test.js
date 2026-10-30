import assert from 'node:assert/strict';
import test from 'node:test';
import { clampEditPopupPosition, createEditHost } from './edit-host.js';

// Only the authoring host's DOM operations are needed. Browser tests exercise
// the actual controls and docking; this fixture checks retained draft guards.
class Element extends EventTarget {
  constructor(tagName, id = '') {
    super();
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.controls = [];
    this.dataset = {};
    this.style = {};
    this.captures = new Set();
    this.classList = { add() {}, remove() {} };
  }
  setAttribute() {}
  append(...children) {
    for (const child of children) {
      if (child.parentElement)
        child.parentElement.children = child.parentElement.children.filter(item => item !== child);
      child.parentElement = this;
      this.children.push(child);
    }
  }
  querySelectorAll() { return this.controls; }
  querySelector() { return null; }
  getBoundingClientRect() { return {left: parseFloat(this.style.left ?? 632), top: parseFloat(this.style.top ?? 72), width: 380, height: 300}; }
  setPointerCapture(id) { this.captures.add(id); }
  hasPointerCapture(id) { return this.captures.has(id); }
  releasePointerCapture(id) { this.captures.delete(id); }
  focus() {}
  show() { this.open = true; }
  close() { this.open = false; this.dispatchEvent(new Event('close')); }
}

test('seeded surface authoring retains dirty controls and fences approved transitions', () => {
  const priorDocument = globalThis.document, priorWindow = globalThis.window, priorResizeObserver = globalThis.ResizeObserver;
  const dialog = new Element('dialog', 'surface-selection-dialog');
  const seed = { id: 'new-surface-selection-seed', tagName: 'SELECT', type: 'select-one',
    value: 'seed', get selectedOptions() { return [{ value: this.value }]; } };
  const barriers = { id: 'new-surface-selection-barriers', tagName: 'SELECT', type: 'select-multiple',
    value: '', selectedOptions: [] };
  const setback = { id: 'new-surface-selection-setback', tagName: 'INPUT', type: 'number', value: '0' };
  dialog.controls = [seed, barriers, setback];
  const body = new Element('body'), parking = new Element('section');
  const documentEvents = new EventTarget();
  const closed = [];
  documentEvents.addEventListener('edit-session-closed', event => closed.push(event.detail.id));
  let confirm = false, confirmations = 0;
  globalThis.document = { body, createElement: tag => new Element(tag),
    getElementById: id => id === dialog.id ? dialog : null,
    addEventListener: (...args) => documentEvents.addEventListener(...args),
    dispatchEvent: event => documentEvents.dispatchEvent(event) };
  globalThis.window = { innerWidth: 1024, innerHeight: 768, addEventListener() {}, confirm() { confirmations++; return confirm; } };
  globalThis.ResizeObserver = class { observe() {} };
  try {
    const host = createEditHost({ parking, hasEditor: () => false,
      revealEditor() {}, dockEditor() {} });
    assert.equal(host.show(dialog.id), true);
    assert.equal(host.active(), dialog);
    assert.equal(dialog.parentElement, host.host);
    setback.value = '0.25';
    barriers.selectedOptions = [{ value: 'stop' }];
    assert.equal(host.prepareTransition(), null);
    assert.equal(confirmations, 1);
    assert.equal(host.active(), dialog);
    assert.equal(setback.value, '0.25');
    assert.deepEqual(barriers.selectedOptions, [{ value: 'stop' }]);
    confirm = true;
    const proof = host.prepareTransition();
    assert.equal(proof.isCurrent(), true);
    seed.value = 'replacement';
    assert.equal(proof.isCurrent(), false);
    assert.throws(() => proof.finish(), /unfinished edit changed/);
    host.applied();
    confirm = false;
    const clean = host.prepareTransition();
    assert.equal(clean.isCurrent(), true);
    clean.finish();
    assert.equal(host.active(), null);
    assert.equal(dialog.open, false);
    assert.equal(dialog.parentElement, parking);
    assert.deepEqual(closed, ['surface-selection-dialog']);
  } finally {
    globalThis.document = priorDocument;
    globalThis.window = priorWindow;
    globalThis.ResizeObserver = priorResizeObserver;
  }
});

test('popup placement keeps its complete bounds inside the viewport and recovers after size changes', () => {
  const viewport = {width: 800, height: 600};
  assert.deepEqual(clampEditPopupPosition({left: -90, top: -5}, {width: 380, height: 300}, viewport), {left: 8, top: 8});
  assert.deepEqual(clampEditPopupPosition({left: 900, top: 900}, {width: 380, height: 300}, viewport), {left: 412, top: 292});
  assert.deepEqual(clampEditPopupPosition({left: 412, top: 292}, {width: 380, height: 500}, viewport), {left: 412, top: 92});
  assert.deepEqual(clampEditPopupPosition({left: 412, top: 292}, {width: 304, height: 244}, {width: 320, height: 260}), {left: 8, top: 8});
});

function popupFixture(run) {
  const previous = {document: globalThis.document, window: globalThis.window, ResizeObserver: globalThis.ResizeObserver},
    body = new Element('body'), parking = new Element('section'), dialog = new Element('dialog', 'surface-selection-dialog'),
    events = new EventTarget(), windowEvents = new EventTarget(), control = {id: 'seed-draft', tagName: 'INPUT', type: 'number', value: '1'};
  dialog.controls = [control];
  let docked = false, observe;
  globalThis.document = {body, createElement: tag => new Element(tag), getElementById: id => id === dialog.id ? dialog : null,
    addEventListener: (...args) => events.addEventListener(...args), dispatchEvent: event => events.dispatchEvent(event)};
  globalThis.window = {innerWidth: 1024, innerHeight: 768, confirm: () => false,
    addEventListener: (...args) => windowEvents.addEventListener(...args)};
  globalThis.ResizeObserver = class { constructor(callback) { observe = callback; } observe() {} };
  try {
    const host = createEditHost({parking, hasEditor: () => docked, revealEditor() {}, dockEditor() { docked = true; }}),
      popup = body.children[0], [move, , reset, dock] = popup.children[0].children,
      send = (element, type, properties = {}) => {
        const event = Object.assign(new Event(type, {cancelable: true}), properties); element.dispatchEvent(event); return event;
      },
      down = (properties = {}) => send(move, 'pointerdown', {button: 0, pointerId: 1, clientX: 650, clientY: 80, ...properties}),
      motion = (x = 450, y = 150, pointerId = 1) => send(move, 'pointermove', {pointerId, clientX: x, clientY: y}),
      position = () => ({left: parseFloat(popup.style.left), top: parseFloat(popup.style.top)});
    host.show(dialog.id);
    run({host, popup, move, reset, dock, dialog, control, events, windowEvents, send, down, motion, position, observe});
  } finally {
    Object.assign(globalThis, previous);
  }
}

test('only primary grip drags move the live workflow and cancelled gestures restore the prior position', () => popupFixture(f => {
  const initial = f.position();
  f.control.value = 'unapplied';
  f.down({button: 2}); f.motion(); assert.deepEqual(f.position(), initial);
  f.down({isPrimary: false}); f.motion(); assert.deepEqual(f.position(), initial);
  assert.equal(f.down().defaultPrevented, true);
  assert.equal(f.move.hasPointerCapture(1), true);
  f.motion(450, 150, 2); assert.deepEqual(f.position(), initial);
  f.motion(); assert.deepEqual(f.position(), {left: 432, top: 142});
  assert.equal(f.host.active(), f.dialog); assert.equal(f.dialog.parentElement, f.host.host);
  assert.equal(f.control.value, 'unapplied');
  f.send(f.move, 'pointerup', {pointerId: 1});
  assert.equal(f.move.hasPointerCapture(1), false);
  const committed = f.position();
  for (const cancel of ['pointercancel', 'lostpointercapture', 'blur', 'Escape']) {
    f.down(); f.motion(550, 180); assert.notDeepEqual(f.position(), committed);
    if (cancel === 'blur') f.send(f.windowEvents, 'blur');
    else if (cancel === 'Escape') assert.equal(f.send(f.events, 'keydown', {key: 'Escape'}).defaultPrevented, true);
    else f.send(f.move, cancel, {pointerId: 1});
    assert.deepEqual(f.position(), committed);
    assert.equal(f.move.hasPointerCapture(1), false);
    assert.equal(f.host.active(), f.dialog);
    assert.equal(f.control.value, 'unapplied');
    assert.equal(f.send(f.events, 'click', {detail: 1, pointerId: 1}).defaultPrevented, true,
      'The cancelled drag cannot turn into a body/model click when the physical pointer is released');
    assert.equal(f.send(f.events, 'click', {detail: 1, pointerId: 1}).defaultPrevented, false);
  }
  f.down(); f.motion(); f.send(f.move, 'pointercancel', {pointerId: 1});
  f.send(f.events, 'pointerdown', {pointerId: 1});
  assert.equal(f.send(f.events, 'click', {detail: 1, pointerId: 1}).defaultPrevented, false,
    'A genuine cancellation with no click must not suppress the next independent gesture');
}));

test('keyboard movement, reset, resize, content growth and docking preserve drafts', () => popupFixture(f => {
  const initial = f.position();
  f.control.value = 'dirty';
  assert.equal(f.send(f.move, 'keydown', {key: 'ArrowLeft'}).defaultPrevented, true);
  f.send(f.move, 'keydown', {key: 'ArrowDown', shiftKey: true});
  assert.deepEqual(f.position(), {left: initial.left - 10, top: initial.top + 1});
  f.reset.onclick(); assert.deepEqual(f.position(), initial);
  f.down(); f.motion(-900, -500); f.send(f.move, 'pointerup', {pointerId: 1});
  assert.deepEqual(f.position(), {left: 8, top: 8});
  f.send(f.move, 'keydown', {key: 'ArrowRight'});
  globalThis.window.innerWidth = 400; globalThis.window.innerHeight = 320;
  f.send(f.windowEvents, 'resize'); assert.deepEqual(f.position(), {left: 12, top: 8});
  f.popup.getBoundingClientRect = () => ({left: 12, top: 8, width: 380, height: 310});
  f.observe(); assert.deepEqual(f.position(), {left: 12, top: 8});
  f.down(); f.motion(700, 200); f.dock.onclick();
  assert.equal(f.move.hasPointerCapture(1), false);
  assert.equal(f.popup.open, false);
  assert.equal(f.host.active(), f.dialog);
  assert.equal(f.control.value, 'dirty');
}));

test('close guards and workflow completion retire a captured drag without changing its draft', () => popupFixture(f => {
  f.control.value = 'unfinished';
  const initial = f.position();
  f.down(); f.motion();
  assert.equal(f.host.prepareTransition(), null, 'Moving the panel cannot bypass the existing discard guard');
  assert.deepEqual(f.position(), initial);
  assert.equal(f.host.active(), f.dialog);
  assert.equal(f.move.hasPointerCapture(1), false);
  f.down(); f.motion(); f.host.finish();
  assert.equal(f.host.active(), null);
  assert.equal(f.popup.open, false);
  assert.equal(f.move.hasPointerCapture(1), false);
  assert.equal(f.control.value, 'unfinished');
}));
