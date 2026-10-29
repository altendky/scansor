// Scan triangle indices remain source vertex IDs, even under a display transform.
// Cache by the immutable result's IDs array, not feature ID, so reevaluation or
// a different solve provider cannot reuse stale membership.
const memberships = new WeakMap();

export function scanFitCandidates(vertices, choices) {
  if (!vertices?.length) return [];
  return choices.filter(choice => {
    if (choice.disabled || !choice.ids?.length) return false;
    let membership = memberships.get(choice.ids);
    if (!membership) {
      membership = new Set(choice.ids);
      memberships.set(choice.ids, membership);
    }
    return vertices.some(id => membership.has(id));
  });
}

export function isModelClick(start, end, threshold = 5) {
  return !!start && end.button === 0 && start.pointerId === end.pointerId &&
    Math.hypot(end.clientX - start.clientX, end.clientY - start.clientY) <= threshold;
}

// Geometry inputs only: enum, group, output-scale, and presentation controls
// are deliberately not pickers. The current native options own eligibility.
export const geometrySelectorIds = new Set(`
  fit-inputs new-fit-inputs fit-reference new-fit-reference growth-fit growth-barriers
  surface-selection-seed new-surface-selection-seed surface-selection-barriers new-surface-selection-barriers
  axis-source-fit new-axis-source axis-source-point-a axis-source-point-b new-axis-point-a new-axis-point-b
  point-source-fit new-point-source reference-plane-axis new-reference-plane-axis
  frame-origin new-frame-origin frame-primary-reference new-frame-primary-reference
  frame-secondary-reference new-frame-secondary-reference transform-frame new-transform-frame
  selection-region-selection new-selection-region-selection selection-region-fit new-selection-region-fit
  selection-region-axial new-selection-region-axial selection-region-clock new-selection-region-clock
  region-selection-region new-region-selection-region region-selection-axial new-region-selection-axial
  region-selection-clock new-region-selection-clock feature-reuse-fits new-feature-reuse-fits
  feature-reuse-reference new-feature-reuse-reference feature-reuse-target new-feature-reuse-target
  axis-solve-axis new-axis-solve-axis axis-solve-factors new-axis-solve-factors
  coaxial-group-axis coaxial-group-members
  constraint-a constraint-b mirror-plane new-mirror-plane mirror-input-0 mirror-input-1
  new-mirror-input-0 new-mirror-input-1 parallel-surface new-parallel-surface
  parallel-reference new-parallel-reference equal-radius-surface new-equal-radius-surface
  equal-distance-surface new-equal-distance-surface equal-distance-reference new-equal-distance-reference
  rotation-axis rotation-input-0 rotation-input-1 rotation-input-2 rotation-joint
  rotation-plane-0 rotation-plane-1 rotation-plane-2 new-joint-side new-joint-plane new-joint-extra joint-add-fits
  plane-relationship-surfaces direction-alignment-inputs equal-radii-surfaces joint-inputs reuse-selection-fit reuse-selection-source
  intersection-first intersection-second new-intersection-first new-intersection-second
  face-surface new-face-surface build-faces-surfaces export-target export-origin-plane export-transform
`.trim().split(/\s+/));

export function nativePickOptions(control) {
  if (!control?.isConnected || control.disabled || control.matches(':disabled') ||
      control.closest('[hidden], [inert], fieldset[disabled]')) return [];
  if (control.matches('select')) return [...control.options]
    .filter(option => option.value && !option.disabled && !option.closest('optgroup[disabled]'))
    .map(option => ({ key: option.value, label: option.textContent, selected: option.selected }));
  return [...control.querySelectorAll('input[type="checkbox"]')]
    .filter(input => !input.disabled && !input.matches(':disabled') && !input.closest('[hidden], [inert]'))
    .map(input => ({ key: input.value, label: input.closest('label').textContent.trim(), selected: input.checked }));
}

export function commitNativePick(control, key) {
  if (!nativePickOptions(control).some(option => option.key === key)) return false;
  if (control.matches('select')) {
    const option = [...control.options].find(option => option.value === key);
    if (control.multiple) option.selected = !option.selected;
    else control.value = key;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
  } else [...control.querySelectorAll('input')].find(input => input.value === key).click();
  return true;
}

let pickerSequence = 0;

export function pickFieldFeedback(choices, { label, disabled = false, busy = false, emptyMessage = '' } = {}) {
  if (disabled) return busy ? 'Temporarily unavailable while another action is running. Wait for it to finish.'
    : 'This input is unavailable in the current configuration.';
  const eligible = choices.filter(choice => choice.key && !choice.disabled),
    unavailable = choices.filter(choice => choice.key && choice.disabled),
    saved = unavailable.filter(choice => choice.selected);
  if (!eligible.length) return `No eligible ${label.toLowerCase()}. ${emptyMessage ||
    'Create a compatible feature before this input, or evaluate or repair an unavailable provider.'}` +
    (unavailable.length ? ` ${saved[0]?.label || unavailable[0].label}.` : '');
  if (saved.length) return `Saved input unavailable: ${saved.map(choice => choice.label).join('; ')}. Choose a replacement or remove it.`;
  if (eligible.every(choice => choice.modelPickable === false))
    return eligible.some(choice => choice.treePickable)
      ? 'Pick compatible features in the tree or use the arrow to choose exact outputs. Evaluate their providers to show available 3D geometry.'
      : 'Eligible choices have no pickable 3D geometry. Use the arrow to choose from the list; evaluate their providers to show available geometry.';
  return '';
}

function emptyPickMessage(control) {
  if (control.dataset.referenceHint) return control.dataset.referenceHint;
  if (control.dataset.pickerEmptyMessage) return control.dataset.pickerEmptyMessage;
  const id = control.id.replace(/^new-/, '');
  if (control.dataset.inputRequirement === 'constrainable_point') return 'Create a point datum before this sphere fit.';
  if (id === 'fit-inputs') return 'Create a selection before this surface fit.';
  if (id === 'surface-selection-seed') return 'Create a selection on the surface before this feature. Evaluate a derived seed selection first.';
  if (id === 'surface-selection-barriers') return 'Create an earlier selection where surface traversal must stop. Barriers are optional.';
  if (id === 'fit-reference') return 'Create an axis or plane datum before this fit. Move a later datum earlier, or create a new fit after it.';
  if (['reference-plane-axis', 'axis-solve-axis', 'rotation-axis'].includes(id))
    return 'Create an explicit axis before this feature.';
  if (['axis-source', 'axis-source-fit'].includes(id)) return 'Create a cone or cylinder surface fit before this axis.';
  if (['point-source', 'point-source-fit'].includes(id)) return 'Create a sphere fit before this point.';
  if (control.dataset.inputRequirement === 'point') return 'Create a point datum, sphere fit, or single-vertex selection before this feature. Evaluate its provider.';
  if (control.dataset.inputRequirement === 'direction') return 'Create an axis or plane output before this feature. Evaluate its provider.';
  if (control.dataset.inputRequirement === 'coaxial_axis') return 'Create a datum axis before this Coaxial relationship. Published solve axes cannot be rebound.';
  if (control.dataset.inputRequirement === 'coaxial_members') return 'Create at least two cone or cylinder fits before this Coaxial relationship. Published solve outputs cannot be rebound.';
  return '';
}

// The visible field shows selections; its arrow is the only entry to the list
// of available choices. Native inputs remain the form's source of truth.
export function createPickField(root, { label, multiple = false, onPick, onChange,
  popupContent = null, id = null, onInspect = () => {}, onRemove = onChange, onChoose = () => {} }) {
  const element = root.createElement('div'), selected = root.createElement('ul'),
    button = root.createElement('button'), dropdown = root.createElement('button'),
    popup = root.createElement('div'), feedback = root.createElement('p');
  element.className = 'selection-field';
  element.classList.toggle('selection-field-multiple', multiple);
  selected.className = 'selection-field-selected';
  selected.setAttribute('aria-label', `Selected ${label}`);
  button.type = dropdown.type = 'button';
  button.className = 'model-field-pick';
  button.setAttribute('aria-label', `Pick ${label} in model`);
  button.setAttribute('aria-pressed', 'false');
  if (id) button.id = id;
  dropdown.className = 'selection-field-dropdown';
  dropdown.textContent = '▾';
  dropdown.setAttribute('aria-label', `Show ${label} choices`);
  dropdown.setAttribute('aria-haspopup', popupContent ? 'dialog' : 'menu');
  dropdown.setAttribute('aria-expanded', 'false');
  popup.className = 'selection-field-options';
  popup.classList.toggle('selection-field-options-custom', !!popupContent);
  popup.id = `selection-field-options-${++pickerSequence}`;
  feedback.id = `selection-field-feedback-${pickerSequence}`;
  feedback.className = 'selection-field-feedback hint';
  feedback.hidden = true;
  button.setAttribute('aria-describedby', feedback.id);
  dropdown.setAttribute('aria-describedby', feedback.id);
  popup.setAttribute('popover', 'auto');
  popup.setAttribute('role', popupContent ? 'dialog' : 'menu');
  popup.setAttribute('aria-label', `${label} choices`);
  dropdown.setAttribute('aria-controls', popup.id);
  if (popupContent) {
    popupContent.classList.add('selection-field-content');
    popup.append(popupContent);
  }
  element.append(selected, button, dropdown, popup, feedback);
  let records = [], disabled = false, signature = null;
  const close = (focus = false) => {
    popup.hidePopover();
    onInspect(null);
    if (focus && dropdown.isConnected) dropdown.focus();
  };
  const position = () => {
    const bounds = element.getBoundingClientRect(), width = root.defaultView.innerWidth,
      height = root.defaultView.innerHeight, availableHeight = Math.max(0, height - 16);
    popup.style.width = `${Math.min(Math.max(bounds.width, 260), Math.max(0, width - 16))}px`;
    popup.style.maxHeight = `${Math.min(320, availableHeight)}px`;
    const box = popup.getBoundingClientRect(), below = Math.max(0, height - Math.max(8, bounds.bottom) - 8),
      above = Math.max(0, Math.min(height - 8, bounds.top) - 8),
      down = below >= Math.min(box.height, 180) || below >= above;
    // If neither side can show a usable slice, overlap the field while staying
    // inside the viewport rather than clipping every choice beneath search.
    const sideHeight = down ? below : above,
      maxHeight = sideHeight >= Math.min(box.height, 180) ? sideHeight : availableHeight;
    popup.style.maxHeight = `${Math.min(320, availableHeight, maxHeight)}px`;
    const finalBox = popup.getBoundingClientRect();
    popup.style.left = `${Math.max(8, Math.min(bounds.left, width - finalBox.width - 8))}px`;
    popup.style.top = `${Math.max(8, Math.min(down ? bounds.bottom + 3 : bounds.top - finalBox.height - 3,
      height - finalBox.height - 8))}px`;
  };
  const reposition = event => {
    if (!element.isConnected) { stopTracking(); onInspect(null); return; }
    if (popup.matches(':popover-open') && (event.type === 'resize' || !popup.contains(event.target))) position();
  };
  const resize = new root.defaultView.ResizeObserver(() => {
    if (!element.isConnected) { stopTracking(); onInspect(null); }
    else if (popup.matches(':popover-open')) position();
  });
  const stopTracking = () => {
    root.defaultView.removeEventListener('resize', reposition);
    root.removeEventListener('scroll', reposition, true);
    resize.disconnect();
  };
  const open = () => {
    if (disabled) return;
    if (!button.disabled) onPick(button);
    popup.showPopover();
    position();
    dropdown.setAttribute('aria-expanded', 'true');
  };
  button.onclick = () => {
    if (button.disabled) return;
    close();
    onPick(button);
  };
  dropdown.onclick = () => popup.matches(':popover-open') ? close() : open();
  dropdown.onkeydown = event => {
    if (event.key === 'Escape' && popup.matches(':popover-open')) {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    open();
    const options = popup.querySelectorAll('[role^="menuitem"]:not(:disabled)');
    options[event.key === 'ArrowUp' ? options.length - 1 : 0]?.focus();
  };
  popup.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (popupContent || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const options = [...popup.querySelectorAll('[role^="menuitem"]:not(:disabled)')];
    if (!options.length) return;
    event.preventDefault();
    event.stopPropagation();
    const current = options.indexOf(root.activeElement), index = event.key === 'Home' ? 0 :
      event.key === 'End' ? options.length - 1 :
        (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    options[index].focus();
  });
  popup.addEventListener('toggle', () => {
    const opened = popup.matches(':popover-open');
    dropdown.setAttribute('aria-expanded', String(opened));
    if (opened) {
      root.defaultView.addEventListener('resize', reposition);
      root.addEventListener('scroll', reposition, true);
      resize.observe(element);
    } else {
      stopTracking();
      onInspect(null);
    }
  });
  const update = (choices, { disabled: locked = false, busy = false, emptyMessage = '' } = {}) => {
    records = choices;
    disabled = locked;
    const next = JSON.stringify([choices, locked, busy, emptyMessage]);
    if (signature === next) return;
    signature = next;
    const chosen = records.filter(record => record.selected), eligible = records.filter(record => !record.disabled);
    const treePickable = eligible.some(record => record.key && record.treePickable);
    button.disabled = locked || !eligible.some(record => record.key && (record.modelPickable !== false || record.treePickable));
    dropdown.disabled = locked;
    if (locked) close();
    button.textContent = multiple ? `Pick ${label}…` : chosen[0]?.label || `Pick ${label}…`;
    button.setAttribute('aria-describedby', feedback.id);
    if (!multiple && chosen[0]?.detail) {
      const title = root.createElement('span'), detail = root.createElement('span');
      title.className = 'selection-field-title'; title.textContent = chosen[0].label;
      detail.className = 'selection-field-detail'; detail.textContent = chosen[0].detail;
      title.id = `${popup.id}-selected-title`;
      detail.id = `${popup.id}-selected-description`;
      button.setAttribute('aria-describedby', `${feedback.id} ${title.id} ${detail.id}`);
      button.replaceChildren(title, detail);
    }
    const pickLabel = `Pick ${label} in ${treePickable ? 'the tree or model' : 'model'}`;
    button.setAttribute('aria-label', pickLabel);
    button.title = multiple ? pickLabel : [chosen[0]?.label, chosen[0]?.detail].filter(Boolean).join(' — ') || pickLabel;
    feedback.textContent = pickFieldFeedback(records, { label, disabled: locked, busy, emptyMessage });
    feedback.hidden = !feedback.textContent;
    selected.hidden = !multiple;
    if (multiple) selected.replaceChildren(...chosen.map((record, index) => {
      const row = root.createElement('li'), name = root.createElement('button'), remove = root.createElement('button');
      row.className = 'selection-field-row';
      row.dataset.selectionKey = record.key;
      name.type = remove.type = 'button';
      name.className = 'selection-field-name';
      if (record.detail) {
        const title = root.createElement('span'), detail = root.createElement('span');
        title.className = 'selection-field-title';
        title.textContent = record.label;
        detail.className = 'selection-field-detail';
        detail.id = `${popup.id}-selected-detail-${index}`;
        detail.textContent = record.detail;
        remove.setAttribute('aria-describedby', detail.id);
        name.append(title, detail);
      } else name.textContent = record.label;
      name.title = [record.label, record.detail].filter(Boolean).join(' — ');
      name.disabled = remove.disabled = locked;
      name.onclick = () => button.click();
      remove.className = 'selection-field-remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${record.label}`);
      remove.onclick = () => { onRemove(record.key); button.focus(); };
      if (record.disabled) row.classList.add('selection-field-unavailable');
      row.append(name, remove);
      return row;
    }));
    if (popupContent) {
      if (popup.matches(':popover-open')) position();
      return;
    }
    // Keep dropdown options in place while multiselect updates their checks.
    const current = new Map([...popup.querySelectorAll('[data-selection-key]')]
      .map(option => [option.dataset.selectionKey, option]));
    const options = records.map(record => {
      const option = current.get(record.key) || root.createElement('button');
      option.type = 'button';
      option.className = 'selection-field-option';
      option.dataset.selectionKey = record.key;
      option.setAttribute('role', multiple ? 'menuitemcheckbox' : 'menuitemradio');
      option.setAttribute('aria-checked', String(record.selected));
      option.disabled = !!record.disabled;
      if (option.dataset.selectionLabel !== record.label) {
        option.dataset.selectionLabel = record.label;
        const check = root.createElement('span'), text = root.createElement('span');
        check.className = 'selection-field-check';
        check.setAttribute('aria-hidden', 'true');
        text.textContent = record.label;
        option.replaceChildren(check, text);
      }
      option.firstElementChild.textContent = record.selected ? '✓' : '';
      option.onpointerenter = option.onfocus = () => onInspect(record.key);
      option.onclick = () => {
        if (disabled || !records.some(item => item.key === record.key && !item.disabled)) return;
        if (multiple || !record.selected) onChange(record.key);
        onChoose(record.key);
        if (!multiple) close(true);
      };
      return option;
    });
    for (const child of [...popup.children]) if (!options.includes(child)) child.remove();
    options.forEach((option, index) => {
      if (popup.children[index] !== option) popup.insertBefore(option, popup.children[index] || null);
    });
    if (!options.length) {
      const empty = root.createElement('p');
      empty.className = 'hint';
      empty.textContent = feedback.textContent || 'No eligible choices';
      popup.append(empty);
    }
    if (popup.matches(':popover-open')) position();
  };
  return { element, button, dropdown, popup, feedback, update };
}

export function bindModelPickControls(root, pick, { modelPickable = () => true, busy = () => false } = {}) {
  const fields = new WeakMap();
  const sync = () => {
    for (const control of root.querySelectorAll('select, #body-face-choices, #new-body-face-choices')) {
      if (control.matches('select') && !geometrySelectorIds.has(control.id) &&
          !control.matches('[data-boundary-intersection], .scale-distance-row [data-field="first"], .scale-distance-row [data-field="second"]')) continue;
      let field = fields.get(control);
      if (!field) {
        const label = control.getAttribute('aria-label') || control.labels?.[0]?.childNodes[0]?.textContent?.trim() ||
          (control.matches('select') ? 'input' : 'faces');
        const syncField = () => {
          if (!control.matches('select')) for (const input of control.querySelectorAll('input')) input.tabIndex = -1;
          const records = control.matches('select') ? [...control.options]
            .filter(option => option.value || (!control.required && /^None\b/.test(option.textContent)))
            .map(option => ({ key: option.value, label: option.textContent, selected: option.selected,
              disabled: option.disabled || !!option.closest('optgroup[disabled]') })) :
            [...control.querySelectorAll('input[type="checkbox"]')].map(input => ({ key: input.value,
              label: input.closest('label').textContent.trim(), selected: input.checked,
              disabled: input.disabled || input.matches(':disabled') }));
          field.update(records.map(record => ({ ...record, modelPickable: modelPickable(control, record.key) })),
            { emptyMessage: emptyPickMessage(control), busy: busy(), disabled: !!control.disabled || control.matches(':disabled') ||
            !!control.closest('[hidden], [inert], fieldset[disabled]') });
        };
        field = createPickField(root, { label, multiple: control.multiple || !control.matches('select'),
          onPick: button => pick(control, button), onChange: key => {
            if (control.matches('select') && !key && !control.required) {
              control.value = '';
              control.dispatchEvent(new Event('input', { bubbles: true }));
              control.dispatchEvent(new Event('change', { bubbles: true }));
            } else commitNativePick(control, key);
            syncField();
          }, onChoose: () => control.dispatchEvent(new CustomEvent('selection-picker-change', { bubbles: true })),
          onRemove: key => {
            if (control.disabled || control.matches(':disabled') || control.closest('[hidden], [inert], fieldset[disabled]')) return;
            if (control.matches('select')) {
              const option = [...control.options].find(item => item.value === key && item.selected);
              if (!option) return;
              option.selected = false;
              control.dispatchEvent(new Event('input', { bubbles: true }));
            } else {
              const input = [...control.querySelectorAll('input[type="checkbox"]')]
                .find(item => item.value === key && item.checked);
              if (!input) return;
              input.checked = false;
              input.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (control.matches('select')) control.dispatchEvent(new Event('change', { bubbles: true }));
            syncField();
          } });
        if (control.matches('select')) {
          // Preserve the native label's control before inserting labelable
          // picker buttons ahead of it. A disabled model-pick button must not
          // become the label target of otherwise eligible list options.
          for (const nativeLabel of [...(control.labels || [])]) {
            if (!control.id) control.id = `geometry-input-${pickerSequence}`;
            if (!nativeLabel.htmlFor) nativeLabel.htmlFor = control.id;
          }
          // The adjacent button is inside some wrapping labels. Keep its text
          // out of the native input's accessible name.
          if (control.labels?.length && !control.hasAttribute('aria-label') && !control.hasAttribute('aria-labelledby'))
            control.setAttribute('aria-label', label);
          control.classList.add('geometry-native-input');
          control.setAttribute('aria-hidden', 'true');
          control.tabIndex = -1;
          control.addEventListener('invalid', event => {
            event.preventDefault();
            field.button.setAttribute('aria-invalid', 'true');
            field.button.focus();
          });
        } else {
          control.classList.add('geometry-native-input');
          control.setAttribute('aria-hidden', 'true');
        }
        field.button.dataset.pickControl = control.id || control.dataset.field || 'boundary';
        field.element.dataset.selectionControl = control.id || control.dataset.field || 'boundary';
        control.before(field.element);
        control.addEventListener('input', syncField);
        control.addEventListener('change', () => { field.button.removeAttribute('aria-invalid'); syncField(); });
        fields.set(control, field);
        field.sync = syncField;
      }
      field.sync();
    }
  };
  sync();
  const observer = new MutationObserver(sync);
  observer.observe(root.body, { childList: true, subtree: true, attributes: true,
    attributeFilter: ['disabled', 'hidden', 'inert', 'data-picker-empty-message'] });
  return observer;
}

export function segmentDistance(point, first, second) {
  const dx = second[0] - first[0], dy = second[1] - first[1], length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length)) : 0;
  return Math.hypot(point[0] - first[0] - t * dx, point[1] - first[1] - t * dy);
}
