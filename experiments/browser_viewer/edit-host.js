// Keep one set of live authoring controls while changing their presentation.
export const AUTHORING_DIALOGS = [
  'surface-intersection-dialog', 'body-dialog', 'trimmed-face-dialog', 'build-faces-dialog',
  'fit-dialog', 'feature-reuse-dialog', 'feature-group-dialog', 'selection-region-dialog',
  'region-selection-dialog', 'axis-dialog', 'point-dialog', 'reference-plane-dialog',
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
  title.textContent = 'Edit';
  const dock = document.createElement('button');
  dock.type = 'button';
  dock.id = 'dock-edit';
  dock.textContent = 'Dock';
  const close = document.createElement('button');
  close.type = 'button';
  close.id = 'close-edit';
  close.textContent = 'Close';
  header.append(title, dock, close);
  popup.append(header);
  document.body.append(popup);
  parking.append(host);
  let active = null, baseline = '', faceInputsDirty = false;
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
    const closeEvent = new Event('workspace-before-close', { cancelable: true });
    if (!active.dispatchEvent(closeEvent)) return false;
    if (!closeEvent.editDiscardConfirmed && (faceInputsDirty || editFormValues(active) !== baseline) &&
        !window.confirm('Discard unapplied changes in this edit?')) return false;
    finish();
    return true;
  }
  function present() {
    if (hasEditor()) {
      if (popup.open) popup.close();
      revealEditor();
    } else {
      popup.append(host);
      if (active && !popup.open) popup.show();
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
  dock.onclick = () => { dockEditor(); present(); };
  return {
    host,
    prepare,
    finish,
    active: () => active,
    applied() { if (active) baseline = editFormValues(active); faceInputsDirty = false; },
    docked() { if (popup.open) popup.close(); },
    present,
    show(id) {
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
