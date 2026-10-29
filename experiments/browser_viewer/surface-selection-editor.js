export const surfaceSelectionPolicy = 'seed-normal-envelope-v1';
export const surfaceSelectionPhases = {
  seeds: { label: 'Seeds', color: '#63d9ff' },
  region: { label: 'Region', color: '#ffb14e' },
  boundaries: { label: 'Boundaries', color: '#ff526c' },
  barriers: { label: 'Barriers', color: '#da82ff' },
  inset: { label: 'Inset result', color: '#75ed8a' },
};

export function surfaceSelectionDefinition(values) {
  const seed_selection = values.seed_selection;
  const barriers = [...new Set(values.barriers || [])].sort();
  if (typeof seed_selection !== 'string' || !seed_selection) throw Error('Choose a seed selection.');
  if (barriers.includes(seed_selection)) throw Error('The seed selection cannot also be a barrier.');
  const angle_degrees = Number(values.angle_degrees), setback = Number(values.setback);
  if (String(values.angle_degrees).trim() === '' || !Number.isFinite(angle_degrees) || angle_degrees <= 0 || angle_degrees > 90)
    throw Error('Enter a boundary angle greater than zero and at most 90 degrees.');
  if (String(values.setback).trim() === '' || !Number.isFinite(setback) || setback < 0)
    throw Error('Enter a nonnegative setback in scan length units.');
  return { seed_selection, barriers, angle_degrees, setback, policy: surfaceSelectionPolicy };
}

export function surfaceSelectionSignature(definition, context) {
  return JSON.stringify([definition, context.generation, context.graph.token, context.graph.revision]);
}

export function validateSurfaceSelectionPreview(response, context) {
  if (response?.token !== context.graph.token || response.project_generation !== context.generation)
    throw Error('The scan or actions changed. Preview the surface selection again.');
  const preview = response.preview;
  if (preview?.policy !== surfaceSelectionPolicy || !preview.counts || !preview.samples)
    throw Error('Invalid surface selection preview.');
  for (const phase of Object.keys(surfaceSelectionPhases)) {
    const count = preview.counts[phase], ids = preview.samples[phase];
    if (!Number.isSafeInteger(count) || count < 0 || count > context.vertices || !Array.isArray(ids)
      || ids.length > Math.min(2048, count) || ids.some((id, index) => !Number.isSafeInteger(id)
        || id < 0 || id >= context.vertices || index > 0 && id <= ids[index - 1]))
      throw Error('Invalid or excessive surface selection preview samples.');
  }
  return preview;
}

// One running request and one newest queued request. Invalidating a preview
// stops its owned computation and waits for retirement before starting another.
export function createSurfaceSelectionPreview({ read, current, prepare, request, isActive = () => true,
  publish = () => {}, busy = () => {}, validity = () => {}, status = () => {}, delay = 400 }) {
  let serial = 0, queued = null, active = null, controller, timer, automatic = false, reviewed = null;
  const signature = () => surfaceSelectionSignature(read(), current());
  function invalidate(message) {
    serial++; queued = null; clearTimeout(timer); controller?.abort(new DOMException('Preview inputs changed.', 'AbortError'));
    reviewed = null; publish(null); validity(false);
    if (message) status(message);
  }
  function start() {
    if (active) return active;
    busy(true);
    active = Promise.resolve().then(async () => {
      while (queued !== null) {
        const ticket = queued; queued = null;
        try {
          const definition = read();
          status('Preparing seed and barrier selections…');
          // Preparing accepted inputs keeps its normal graph observer alive,
          // even if the authoring form closes. Footer Cancel stops that work.
          await prepare(definition);
          if (ticket !== serial || !isActive()) continue;
          if (JSON.stringify(read()) !== JSON.stringify(definition)) {
            status('Inputs changed while preparing selections. Preview again.'); continue;
          }
          const context = current(), expected = signature();
          controller = new AbortController();
          status('Finding the connected surface and applying setback…');
          const response = await request({ token: context.graph.token, ...definition }, { signal: controller.signal });
          if (ticket !== serial || !isActive() || signature() !== expected) continue;
          const preview = validateSurfaceSelectionPreview(response, context);
          reviewed = expected; publish(preview); validity(true);
          status(`${preview.counts.inset.toLocaleString()} selected · ${preview.counts.region.toLocaleString()} before setback · ${preview.counts.boundaries.toLocaleString()} boundary vertices`);
        } catch (error) {
          if (ticket === serial && isActive()) { reviewed = null; publish(null); validity(false); status(error.message, true); }
        } finally { controller = null; }
      }
    }).finally(() => {
      active = null; busy(false);
      if (queued !== null) start();
    });
    return active;
  }
  return {
    preview() {
      automatic = true; invalidate(); queued = serial;
      return start();
    },
    changed() {
      invalidate('Inputs changed. Preview again before applying.');
      if (automatic && isActive()) timer = setTimeout(() => { queued = serial; void start(); }, delay);
    },
    clear(message = 'Preview cleared. Input evaluation may still be running; footer Cancel stops that work.') {
      automatic = false; invalidate(message);
    },
    sync() {
      if (!reviewed) return;
      try { if (signature() === reviewed) return; } catch { /* Keep the saved choice available for repair. */ }
      automatic = false; invalidate('The project changed. Preview again before applying.');
    },
    get reviewed() { try { return reviewed !== null && signature() === reviewed; } catch { return false; } },
    get busy() { return active !== null; },
  };
}

export function bindSurfaceSelectionEditor({ root, prefix, backend, current, prepare,
  isActive, onPreview = () => {}, onBusy = () => {}, onValidity = () => {}, onStatus = () => {} }) {
  const doc = root.ownerDocument, find = suffix => doc.getElementById(prefix + suffix);
  const fields = { seed: find('seed'), barriers: find('barriers'), angle: find('angle'), setback: find('setback') };
  const message = find('preview-status'), buttons = new Map(), visible = new Set(Object.keys(surfaceSelectionPhases));
  let owner = null, initialDefinition = null, proposal = null, choices = [];
  const fieldValues = () => JSON.stringify([fields.seed.value,
    [...fields.barriers.selectedOptions].map(option => option.value).sort(), fields.angle.value, fields.setback.value]);
  let lastFieldValues = fieldValues();
  const read = () => surfaceSelectionDefinition({ seed_selection: fields.seed.value,
    barriers: [...fields.barriers.selectedOptions].map(option => option.value),
    angle_degrees: fields.angle.value, setback: fields.setback.value });
  function paint() { onPreview(proposal ? { preview: proposal, visible: [...visible] } : null); }
  function status(text, error = false) {
    message.textContent = text; message.classList.toggle('error', error);
    if (error) onStatus(text, true);
  }
  const coordinator = createSurfaceSelectionPreview({ read, current, prepare, isActive,
    request: (payload, options) => backend.previewSurfaceSelection(payload, options),
    status, busy: value => { find('cancel-preview').disabled = !value; onBusy(value); },
    validity: onValidity,
    publish: value => {
      proposal = value;
      for (const [phase, button] of buttons) button.textContent = `${surfaceSelectionPhases[phase].label} ${value?.counts[phase]?.toLocaleString() || '0'}`;
      find('diagnostics').textContent = value ? JSON.stringify(value.diagnostics || {}, null, 2) : '';
      paint();
    } });
  for (const [phase, description] of Object.entries(surfaceSelectionPhases)) {
    const button = doc.createElement('button'); button.type = 'button';
    button.textContent = `${description.label} 0`; button.style.setProperty('--phase-color', description.color);
    button.setAttribute('aria-pressed', 'true'); button.dataset.previewPhase = phase;
    button.onclick = () => {
      if (visible.has(phase)) visible.delete(phase); else visible.add(phase);
      button.setAttribute('aria-pressed', String(visible.has(phase))); paint();
    };
    find('legend').append(button); buttons.set(phase, button);
  }
  for (const field of Object.values(fields)) {
    const changed = () => {
      const value = fieldValues();
      // Number inputs emit input while typing and change again on blur. The
      // latter must not invalidate a reviewed preview just before Apply.
      if (value === lastFieldValues) return;
      lastFieldValues = value; coordinator.changed();
    };
    field.addEventListener('input', changed);
    field.addEventListener('change', changed);
  }
  find('preview').onclick = () => { void coordinator.preview(); };
  find('cancel-preview').onclick = () => coordinator.clear('Preview cancelled. Input evaluation, if active, can be stopped with footer Cancel.');
  window.addEventListener('scansor-computation-stop', () => coordinator.clear('Computation stopped. Preview again when ready.'));
  function refreshChoices() {
    const selected = fields.seed.value, barriers = [...fields.barriers.selectedOptions].map(option => option.value);
    for (const [control, values] of [[fields.seed, selected ? [selected] : []], [fields.barriers, barriers]]) {
      const retained = values.filter(id => !choices.some(node => node.id === id));
      control.replaceChildren(...(control.multiple ? [] : [new Option('Choose a seed selection…', '')]),
        ...choices.map(node => new Option(node.label, node.id, false, values.includes(node.id))),
        ...retained.map(id => { const option = new Option(`Unavailable saved selection — ${id}`, id, false, true); option.disabled = true; return option; }));
    }
  }
  return {
    show(node, available) {
      choices = available;
      if (owner !== null && owner === node.id) { refreshChoices(); coordinator.sync(); return; }
      coordinator.clear(); owner = node.id;
      fields.seed.replaceChildren(new Option(node.seed_selection || '', node.seed_selection || '', false, true));
      fields.barriers.replaceChildren(...(node.barriers || []).map(id => new Option(id, id, false, true)));
      fields.angle.value = node.angle_degrees ?? 20; fields.setback.value = node.setback ?? 0;
      refreshChoices();
      lastFieldValues = fieldValues();
      try { initialDefinition = node.id ? JSON.stringify(read()) : null; } catch { initialDefinition = null; }
      status('Preview the connected surface and setback before applying.');
    },
    sync() {
      if (owner) {
        const accepted = current().graph.recipe.nodes.find(node => node.id === owner);
        try { if (accepted && JSON.stringify(surfaceSelectionDefinition(accepted)) === JSON.stringify(read())) initialDefinition = JSON.stringify(read()); } catch { /* Repair incompatible retained inputs explicitly. */ }
      }
      coordinator.sync();
    },
    definition({ reviewed = false } = {}) {
      const value = read();
      if (reviewed && !coordinator.reviewed && (!owner || JSON.stringify(value) !== initialDefinition))
        throw Error('Preview the current seed, barriers, angle and setback before applying.');
      return value;
    },
    clear() { coordinator.clear(); owner = null; initialDefinition = null; },
    preview: () => coordinator.preview(),
    get busy() { return coordinator.busy; },
  };
}
