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
  if (!control?.isConnected || control.disabled || control.closest('[hidden], [inert]')) return [];
  if (control.matches('select')) return [...control.options]
    .filter(option => option.value && !option.disabled && !option.closest('optgroup[disabled]'))
    .map(option => ({ key: option.value, label: option.textContent, selected: option.selected }));
  return [...control.querySelectorAll('input[type="checkbox"]')]
    .filter(input => !input.disabled && !input.closest('[hidden], [inert]'))
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

export function bindModelPickControls(root, pick) {
  const buttons = new WeakMap();
  const sync = () => {
    for (const control of root.querySelectorAll('select, #body-face-choices, #new-body-face-choices')) {
      if (control.matches('select') && !geometrySelectorIds.has(control.id) &&
          !control.matches('[data-boundary-intersection], .scale-distance-row [data-field="first"], .scale-distance-row [data-field="second"]')) continue;
      let button = buttons.get(control);
      if (!button) {
        button = root.createElement('button');
        button.type = 'button';
        button.className = 'model-field-pick';
        button.textContent = 'Pick';
        button.dataset.pickControl = control.id || control.dataset.field || 'boundary';
        button.setAttribute('aria-pressed', 'false');
        button.title = 'Choose this input in the model';
        const label = control.getAttribute('aria-label') || control.labels?.[0]?.childNodes[0]?.textContent?.trim() ||
          (control.matches('select') ? 'input' : 'faces');
        button.setAttribute('aria-label', `Pick ${label} in model`);
        button.onclick = () => { if (!button.disabled) pick(control, button); };
        if (control.matches('select')) {
          // The adjacent button is inside some wrapping labels. Keep its text
          // out of the native input's accessible name.
          if (control.labels?.length && !control.hasAttribute('aria-label') && !control.hasAttribute('aria-labelledby'))
            control.setAttribute('aria-label', label);
          const row = root.createElement('span');
          row.className = 'model-pick-input-row';
          control.before(row);
          row.append(control, button);
        } else control.before(button);
        buttons.set(control, button);
      }
      const disabled = !!control.disabled || !nativePickOptions(control).length;
      if (button.disabled !== disabled) button.disabled = disabled;
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
