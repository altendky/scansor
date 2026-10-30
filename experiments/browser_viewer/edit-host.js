// Keep one set of live authoring controls while changing their presentation.
export const AUTHORING_DIALOGS = [
  'surface-intersection-dialog', 'body-dialog', 'trimmed-face-dialog', 'build-faces-dialog',
  'fit-dialog', 'feature-reuse-dialog', 'feature-group-dialog', 'selection-region-dialog',
  'region-selection-dialog', 'axis-dialog', 'point-dialog', 'reference-plane-dialog',
  'surface-selection-dialog',
  'relationship-dialog', 'mirror-dialog', 'parallel-dialog', 'equal-dialog',
  'axis-solve-dialog', 'joint-dialog', 'rotation-dialog', 'frame-dialog', 'scale-dialog',
  'transform-dialog',
];

export function editFormValues(element) {
  const form = element.id === 'feature-properties-panel' ? element.querySelector('#action-properties') : element;
  // Candidate/review controls are populated asynchronously. Their retained-cell
  // guard lives in Build faces; these stable fields cover its other drafts.
  const selector = element.id === 'build-faces-dialog'
    ? '#build-faces-label, #build-faces-mode, #build-faces-surfaces'
    : 'input, select, textarea';
  return JSON.stringify([...form.querySelectorAll(selector)].map(control => {
    if (control.tagName === 'SELECT') return [control.id, [...control.selectedOptions].map(option => option.value)];
    return [control.id, control.type === 'checkbox' || control.type === 'radio' ? control.checked : control.value];
  }));
}

export function clampEditPopupPosition(position, size, viewport) {
  const margin = 8;
  return {
    left: Math.max(margin, Math.min(position.left, viewport.width - size.width - margin)),
    top: Math.max(margin, Math.min(position.top, viewport.height - size.height - margin)),
  };
}

export function createEditHost({ parking, hasEditor, revealEditor, dockEditor }) {
  const host = document.createElement('section');
  host.id = 'edit-host';
  host.setAttribute('aria-label', 'Edit');
  const empty = document.createElement('p');
  empty.className = 'hint edit-empty';
  empty.textContent = 'Choose Edit from a feature’s context menu, or start a creation tool.';
  host.append(empty);
  const popup = document.createElement('dialog');
  popup.id = 'edit-popup';
  popup.setAttribute('aria-label', 'Edit');
  const header = document.createElement('div');
  header.className = 'edit-popup-header';
  const title = document.createElement('strong');
  title.id = 'edit-popup-title';
  title.textContent = 'Edit';
  const move = document.createElement('button');
  move.type = 'button';
  move.id = 'move-edit';
  move.textContent = '⠿';
  move.setAttribute('aria-label', 'Move edit panel');
  move.title = 'Drag to move. Arrow keys move 10 pixels; Shift moves 1 pixel. Escape cancels dragging.';
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.id = 'reset-edit-position';
  reset.textContent = 'Reset';
  reset.setAttribute('aria-label', 'Reset panel position');
  reset.title = 'Reset panel position';
  const dock = document.createElement('button');
  dock.type = 'button';
  dock.id = 'dock-edit';
  dock.textContent = 'Dock';
  const close = document.createElement('button');
  close.type = 'button';
  close.id = 'close-edit';
  close.textContent = 'Close';
  header.append(move, title, reset, dock, close);
  popup.append(header);
  document.body.append(popup);
  parking.append(host);
  let active = null, baseline = '', faceInputsDirty = false, position = null, drag = null, cancelledPointer = null;
  function place() {
    if (!popup.open) return;
    const size = popup.getBoundingClientRect(), viewport = {width: window.innerWidth, height: window.innerHeight},
      preferred = position || {left: viewport.width - size.width - 12, top: 72},
      next = clampEditPopupPosition(preferred, size, viewport);
    if (position) position = next;
    popup.style.left = `${next.left}px`;
    popup.style.top = `${next.top}px`;
    popup.style.right = 'auto';
  }
  function endDrag(cancel = false) {
    if (!drag) return;
    const previous = drag;
    drag = null;
    popup.classList.remove('edit-popup-dragging');
    if (cancel) { cancelledPointer = previous.pointerId; position = previous.position; place(); }
    if (move.hasPointerCapture(previous.pointerId)) move.releasePointerCapture(previous.pointerId);
  }
  function dismissChoices() {
    for (const choice of [...popup.querySelectorAll('[popover]'), document.getElementById('model-pick-choices')]) {
      if (choice?.matches(':popover-open')) choice.hidePopover();
    }
  }
  move.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.isPrimary === false || !popup.open || drag) return;
    event.preventDefault(); event.stopPropagation();
    move.focus({preventScroll: true});
    const bounds = popup.getBoundingClientRect();
    drag = {pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      left: bounds.left, top: bounds.top, position: position && {...position}, moved: false};
    move.setPointerCapture(event.pointerId);
  });
  move.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault(); event.stopPropagation();
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && !dx && !dy) return;
    if (!drag.moved) { dismissChoices(); drag.moved = true; popup.classList.add('edit-popup-dragging'); }
    position = {left: drag.left + dx, top: drag.top + dy};
    place();
  });
  move.addEventListener('pointerup', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault(); event.stopPropagation();
    endDrag();
  });
  for (const type of ['pointercancel', 'lostpointercapture']) move.addEventListener(type, event => {
    if (drag?.pointerId === event.pointerId) endDrag(true);
  });
  move.addEventListener('keydown', event => {
    const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]},
      direction = directions[event.key];
    if (!direction || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault(); event.stopPropagation();
    endDrag(true); dismissChoices();
    const bounds = popup.getBoundingClientRect(), step = event.shiftKey ? 1 : 10;
    position = {left: bounds.left + direction[0] * step, top: bounds.top + direction[1] * step};
    place();
  });
  reset.onclick = () => { endDrag(true); dismissChoices(); position = null; place(); };
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !drag) return;
    event.preventDefault(); event.stopImmediatePropagation();
    endDrag(true);
  }, true);
  // Restoring the panel can move the grip away before the physical release.
  // Its resulting body click belongs to the cancelled drag, not the model.
  document.addEventListener('pointerdown', () => { cancelledPointer = null; }, true);
  document.addEventListener('click', event => {
    if (cancelledPointer === null || !event.detail ||
        event.pointerId !== undefined && event.pointerId !== cancelledPointer) return;
    cancelledPointer = null;
    event.preventDefault(); event.stopImmediatePropagation();
  }, true);
  window.addEventListener('blur', () => endDrag(true));
  window.addEventListener('resize', () => { endDrag(true); place(); });
  const resize = new ResizeObserver(place);
  resize.observe(popup);
  popup.addEventListener('close', () => endDrag(true));
  const workflows = [document.getElementById('feature-properties-panel'),
    ...AUTHORING_DIALOGS.map(id => document.getElementById(id))];
  for (const element of workflows) {
    if (!element) continue;
    parking.append(element);
    element.classList.add('workspace-edit-workflow');
    if (element.id === 'build-faces-dialog') {
      for (const type of ['input', 'change']) element.addEventListener(type, event => {
        if (active === element && event.target.matches('input:not([type="search"]), select, textarea'))
          faceInputsDirty = true;
      });
    }
    if (element.tagName === 'DIALOG') {
      element.classList.add('workspace-dialog');
      element.addEventListener('close', () => {
        if (active === element && !element.open && element.dataset.modelPickSuspended !== 'true') finish();
      });
      element.addEventListener('cancel', event => {
        if (active !== element || element.dataset.modelPickSuspended === 'true') return;
        event.preventDefault();
        prepare();
      });
    }
  }
  function finish() {
    endDrag(true);
    const previous = active;
    active = null;
    baseline = '';
    faceInputsDirty = false;
    if (previous) document.dispatchEvent(new CustomEvent('edit-session-closed', { detail: { id: previous.id } }));
    if (previous?.tagName === 'DIALOG' && previous.open) previous.close();
    if (previous) parking.append(previous);
    empty.hidden = false;
    if (popup.open) popup.close();
    if (!hasEditor()) parking.append(host);
  }
  function prepare() {
    if (!active) return true;
    const proof = prepareTransition();
    if (!proof) return false;
    proof.finish();
    return true;
  }
  function prepareTransition() {
    endDrag(true);
    const original = active;
    const snapshot = () => active && JSON.stringify({
      values: [...active.querySelectorAll('input, select, textarea')].map(control =>
        [control.id, control.type, control.value, control.checked,
          control.tagName === 'SELECT' ? [...control.selectedOptions].map(option => option.value) : null]),
      baseline, faceInputsDirty,
    });
    if (active) {
      const closeEvent = new Event('workspace-before-close', { cancelable: true });
      if (!active.dispatchEvent(closeEvent)) return null;
      if (!closeEvent.editDiscardConfirmed && (faceInputsDirty || editFormValues(active) !== baseline) &&
          !window.confirm('Discard unapplied changes in this edit?')) return null;
    }
    const values = snapshot();
    return {
      isCurrent: () => active === original && snapshot() === values,
      finish() {
        if (!this.isCurrent()) throw new Error('The unfinished edit changed. Review it before changing projects.');
        finish();
      },
    };
  }
  function present() {
    if (hasEditor()) {
      endDrag(true);
      if (popup.open) popup.close();
      revealEditor();
    } else {
      popup.append(host);
      if (active && !popup.open) popup.show();
      place();
    }
  }
  close.onclick = prepare;
  popup.addEventListener('cancel', event => { event.preventDefault(); prepare(); });
  popup.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    // Let active model-picking and workflow-specific Escape handlers run first.
    if (document.querySelector('.model-picking')) return;
    event.preventDefault();
    prepare();
  });
  dock.onclick = () => { endDrag(true); dockEditor(); present(); };
  return {
    host,
    prepare,
    prepareTransition,
    finish,
    active: () => active,
    applied() { if (active) baseline = editFormValues(active); faceInputsDirty = false; },
    docked() { endDrag(true); if (popup.open) popup.close(); },
    present,
    show(id) {
      endDrag(true);
      const element = document.getElementById(id);
      if (!workflows.includes(element)) throw new Error('Unknown edit workflow: ' + id);
      if (active && active !== element && !prepare()) return false;
      active = element;
      empty.hidden = true;
      host.append(element);
      title.textContent = element.querySelector(element.id === 'feature-properties-panel' ? '#properties-title' : 'h2')?.textContent || 'Edit';
      if (element.tagName === 'DIALOG' && !element.open) element.show();
      baseline = editFormValues(element);
      faceInputsDirty = false;
      present();
      return true;
    },
  };
}
