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
  constraint-a constraint-b mirror-plane new-mirror-plane mirror-input-0 mirror-input-1
  new-mirror-input-0 new-mirror-input-1 parallel-surface new-parallel-surface
  parallel-reference new-parallel-reference equal-radius-surface new-equal-radius-surface
  equal-distance-surface new-equal-distance-surface equal-distance-reference new-equal-distance-reference
  rotation-axis rotation-input-0 rotation-input-1 rotation-input-2 rotation-joint
  rotation-plane-0 rotation-plane-1 rotation-plane-2 new-joint-side new-joint-plane new-joint-extra joint-add-fits
  plane-relationship-surfaces equal-radii-surfaces joint-inputs reuse-selection-fit reuse-selection-source
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

// The visible field shows selections; its arrow is the only entry to the list
// of available choices. Native inputs remain the form's source of truth.
export function createPickField(root, { label, multiple = false, onPick, onChange,
  popupContent = null, id = null, onInspect = () => {}, onRemove = onChange, onChoose = () => {} }) {
  const element = root.createElement('div'), selected = root.createElement('ul'),
    button = root.createElement('button'), dropdown = root.createElement('button'),
    popup = root.createElement('div');
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
  popup.id = `selection-field-options-${++pickerSequence}`;
  popup.setAttribute('popover', 'auto');
  popup.setAttribute('role', popupContent ? 'dialog' : 'menu');
  popup.setAttribute('aria-label', `${label} choices`);
  dropdown.setAttribute('aria-controls', popup.id);
  if (popupContent) popup.append(popupContent);
  element.append(selected, button, dropdown, popup);
  let records = [], disabled = false, signature = null;
  const close = (focus = false) => {
    popup.hidePopover();
    onInspect(null);
    if (focus && dropdown.isConnected) dropdown.focus();
  };
  const position = () => {
    const bounds = element.getBoundingClientRect(), width = root.defaultView.innerWidth,
      height = root.defaultView.innerHeight;
    popup.style.width = `${Math.min(Math.max(bounds.width, 260), width - 16)}px`;
    popup.style.maxHeight = `${Math.min(320, height - 16)}px`;
    const box = popup.getBoundingClientRect(), below = height - bounds.bottom - 8,
      above = bounds.top - 8, down = below >= Math.min(box.height, 180) || below >= above;
    popup.style.maxHeight = `${Math.max(40, Math.min(320, down ? below : above))}px`;
    popup.style.left = `${Math.max(8, Math.min(bounds.left, width - box.width - 8))}px`;
    popup.style.top = `${Math.max(8, down ? bounds.bottom + 3 : bounds.top - Math.min(box.height, above) - 3)}px`;
  };
  const open = () => {
    if (disabled) return;
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
    const options = popup.querySelectorAll('[role^="menuitem"]');
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
    const options = [...popup.querySelectorAll('[role^="menuitem"]')];
    if (!options.length) return;
    event.preventDefault();
    event.stopPropagation();
    const current = options.indexOf(root.activeElement), index = event.key === 'Home' ? 0 :
      event.key === 'End' ? options.length - 1 :
        (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    options[index].focus();
  });
  popup.addEventListener('toggle', () => {
    dropdown.setAttribute('aria-expanded', String(popup.matches(':popover-open')));
    if (!popup.matches(':popover-open')) onInspect(null);
  });
  const update = (choices, { disabled: locked = false } = {}) => {
    records = choices;
    disabled = locked;
    const next = JSON.stringify([choices, locked]);
    if (signature === next) return;
    signature = next;
    const chosen = records.filter(record => record.selected), eligible = records.filter(record => !record.disabled);
    button.disabled = locked || !eligible.some(record => record.key);
    dropdown.disabled = locked;
    if (locked) close();
    button.textContent = multiple ? `Pick ${label}…` : chosen[0]?.label || `Pick ${label}…`;
    button.title = multiple ? `Pick ${label} in the model` : chosen[0]?.label || `Pick ${label} in the model`;
    selected.hidden = !multiple;
    if (multiple) selected.replaceChildren(...chosen.map(record => {
      const row = root.createElement('li'), name = root.createElement('button'), remove = root.createElement('button');
      row.className = 'selection-field-row';
      row.dataset.selectionKey = record.key;
      name.type = remove.type = 'button';
      name.className = 'selection-field-name';
      name.textContent = record.label;
      name.title = record.label;
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
    if (popupContent) return;
    // Keep dropdown options in place while multiselect updates their checks.
    const current = new Map([...popup.querySelectorAll('[data-selection-key]')]
      .map(option => [option.dataset.selectionKey, option]));
    const options = eligible.map(record => {
      const option = current.get(record.key) || root.createElement('button');
      option.type = 'button';
      option.className = 'selection-field-option';
      option.dataset.selectionKey = record.key;
      option.setAttribute('role', multiple ? 'menuitemcheckbox' : 'menuitemradio');
      option.setAttribute('aria-checked', String(record.selected));
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
      empty.textContent = 'No eligible choices';
      popup.append(empty);
    }
    if (popup.matches(':popover-open')) position();
  };
  return { element, button, dropdown, popup, update };
}

export function bindModelPickControls(root, pick) {
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
          field.update(records, { disabled: !!control.disabled || control.matches(':disabled') ||
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
        field.button.dataset.pickControl = control.id || control.dataset.field || 'boundary';
        field.element.dataset.selectionControl = control.id || control.dataset.field || 'boundary';
        if (control.matches('select')) {
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
  observer.observe(root.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'inert'] });
  return observer;
}

export function segmentDistance(point, first, second) {
  const dx = second[0] - first[0], dy = second[1] - first[1], length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length)) : 0;
  return Math.hypot(point[0] - first[0] - t * dx, point[1] - first[1] - t * dy);
}
