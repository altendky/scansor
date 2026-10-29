import assert from 'node:assert/strict';
import test from 'node:test';
import { createEditHost } from './edit-host.js';

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
    this.classList = { add() {} };
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
  show() { this.open = true; }
  close() { this.open = false; this.dispatchEvent(new Event('close')); }
}

test('seeded surface authoring retains dirty controls and fences approved transitions', () => {
  const priorDocument = globalThis.document, priorWindow = globalThis.window;
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
    dispatchEvent: event => documentEvents.dispatchEvent(event) };
  globalThis.window = { confirm() { confirmations++; return confirm; } };
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
  }
});
