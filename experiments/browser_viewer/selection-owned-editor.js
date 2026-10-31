import { activeSelectionEntry } from './selection.js';

export const volumeControls = [
  ['inward_thickness', 'Inward thickness'], ['outward_thickness', 'Outward thickness'],
  ['edge_padding', 'Edge padding'], ['normal_angle_degrees', 'Normal tolerance (degrees)'],
];
export const ownedKey = context => context?.entry
  ? `${context.volume ? 'selection-volume' : 'selection-entry'}/${context.selection}/${context.entry}${context.volume ? `/${context.volume}` : ''}` : null;
export const referenceKey = reference => reference ? JSON.stringify(reference, Object.keys(reference).sort()) : '';
export function ownedRecord(graph, context) {
  const selection = graph?.recipe.nodes.find(node => node.id === context?.selection && node.operation === 'selection'),
    entry = selection?.entries?.find(item => item.id === context?.entry),
    volume = entry?.volumes?.find(item => item.id === context?.volume);
  return { selection, entry, volume };
}
export function readVolumeOptions(controls) {
  return Object.fromEntries(volumeControls.map(([name]) => {
    const control = controls[name];
    if (control.mode.value === 'auto') return [name, null];
    const value = Number(control.input.value);
    if (control.input.value.trim() === '' || !Number.isFinite(value) || value < 0 ||
      name === 'normal_angle_degrees' && (value <= 0 || value > 90))
      throw Error(`Enter a valid ${name.replaceAll('_', ' ')} or choose Auto.`);
    return [name, value];
  }));
}
export function volumeAuthorPayload({ token, selection, entry, mode, target, volume, label, kind, fit, options }) {
  if (!['create', 'replace'].includes(mode) || !label.trim()) throw Error('Choose an operation and enter a volume name.');
  if (mode === 'replace' && (!target || target.selection !== selection)) throw Error('Choose an existing volume in this Selection to replace.');
  if (kind !== 'mesh' && !fit) throw Error('Choose an eligible fitted surface for this analytic volume.');
  if (!['mesh', 'plane', 'cylinder', 'cone'].includes(kind)) throw Error('Choose a supported volume definition.');
  return { token, selection, ...(entry ? { entry } : {}), mode, ...(mode === 'create' ? { volume } : { target }),
    label: label.trim(), kind, ...(kind === 'mesh' ? {} : { fit }), options };
}

// Read requests can finish after a source change or a different volume opens.
// Never publish those buffers, even if the old object still exists in a recipe.
export function createVolumePreviewReader({ request, current, publish, progress = () => {}, dispose = () => {} }) {
  let sequence = 0;
  function clear() { sequence++; dispose(); }
  async function load(reference, archive_sha256) {
    const ticket = ++sequence, before = current(), token = before.token, generation = before.generation;
    const stale = () => {
      const after = current();
      return ticket !== sequence || token !== after.token || generation !== after.generation ||
        referenceKey(reference) !== referenceKey(after.reference) || archive_sha256 !== after.archive_sha256;
    };
    dispose();
    let first, positions, normals, signature, offset = 0;
    while (true) {
      let page;
      try { page = await request({ token, reference, archive_sha256, offset, limit: 1024 }); }
      catch (error) { if (stale()) return null; throw error; }
      if (stale()) return null;
      const stop = offset + page.returned_cells;
      if (page.token !== token || page.archive_sha256 !== archive_sha256 ||
        referenceKey(page.reference) !== referenceKey(reference) || page.offset !== offset ||
        !Number.isSafeInteger(page.returned_cells) || page.returned_cells < 1 || page.returned_cells > 1024 ||
        !Number.isSafeInteger(page.total_cells) || page.total_cells < stop || page.total_cells > 600000 ||
        page.positions?.length !== page.returned_cells * 9 || page.normals?.length !== page.positions.length ||
        !page.positions.every(value => Number.isFinite(value) && Math.abs(value) <= 3.402823466e38) || !page.normals.every(Number.isFinite) ||
        ![page.bounds?.min, page.bounds?.max].every(vector => vector?.length === 3 && vector.every(Number.isFinite)) ||
        page.next_offset !== (stop < page.total_cells ? stop : null))
        throw Error('The accepted volume preview is incomplete or inconsistent.');
      const metadata = JSON.stringify([page.total_cells, page.kind, page.label, page.freshness,
        page.options, page.defaults, page.resolved, page.bounds]);
      if (!first) {
        first = page; signature = metadata;
        // Match the accepted archive's 600,000-cell bound without retaining
        // every JSON page or repeatedly copying the accumulated geometry.
        positions = new Float32Array(page.total_cells * 9);
        normals = new Float32Array(positions.length);
      } else if (metadata !== signature) throw Error('The accepted volume preview changed while loading.');
      positions.set(page.positions, offset * 9); normals.set(page.normals, offset * 9);
      offset = stop;
      progress(offset, page.total_cells);
      if (stale()) return null;
      if (page.next_offset === null) {
        const preview = { ...first, offset: 0, returned_cells: offset, next_offset: null, positions, normals };
        publish(preview); return preview;
      }
      first = { ...first, positions: null, normals: null };
    }
  }
  return { load, clear };
}

export function createSelectionOwnedEditor({ root, backend, getCurrent, onGraph, onOpen,
  onPreview = () => {}, onFocus = () => {}, onStatus = () => {}, onApplied = () => {}, onBusy = () => {},
  isLocked = () => false, newId = () => `volume-${crypto.randomUUID()}`,
  confirmDelete = label => window.confirm(`Delete volume “${label}”? This cannot be undone. Linked volumes must be cleared from their generated entries first.`) }) {
  const doc = root.ownerDocument;
  let context = null, renderedKey = null, token = null, generation = null, revision = null, choices = null, choiceSequence = 0,
    choicesKey = null, pendingChoicesKey = null, choiceController = null,
    applying = false, preview = null, controls = {}, author = null, sourcePrefilled = false;
  const element = (tag, text, className) => {
    const node = doc.createElement(tag); if (text !== undefined) node.textContent = text;
    if (className) node.className = className; return node;
  };
  const button = (text, action, parent = root) => {
    const node = element('button', text); node.type = 'button'; node.onclick = () => void action(); parent.append(node); return node;
  };
  const hint = text => { const node = element('p', text, 'hint'); root.append(node); return node; };
  const field = (label, control, parent = root) => { const node = element('label', label); node.append(control); parent.append(node); return control; };
  const select = (id, records, parent = root, label = '') => {
    const node = element('select'); node.id = id;
    for (const [value, text, disabled] of records) { const option = element('option', text); option.value = value; option.disabled = !!disabled; node.append(option); }
    field(label, node, parent); return node;
  };
  const input = (id, value, label, parent = root) => {
    const node = element('input'); node.id = id; node.value = value; field(label, node, parent); return node;
  };
  let message, detail, sourceChoice, fitChoice, fitFeedback, targetChoice, previewStatus, pageFocus;
  const record = () => ownedRecord(getCurrent().graph, context);
  const source = () => getCurrent().graph.recipe.nodes.find(node => node.operation === 'source');
  const active = () => activeSelectionEntry(record().selection, source(), getCurrent().metadata);
  const currentPreview = () => ({ token: getCurrent().graph.token, generation: getCurrent().generation,
    reference: context?.volume ? { selection: context.selection, entry: context.entry, volume: context.volume } : null,
    archive_sha256: record().volume?.archive.sha256 });
  const draftSignature = () => {
    const visit = node => [node, ...[...node.children].flatMap(visit)];
    return JSON.stringify(visit(root).filter(node => ['INPUT', 'SELECT', 'TEXTAREA'].includes(node.tagName.toUpperCase()))
      .map(node => [node.id, node.value]));
  };
  const reader = createVolumePreviewReader({ request: payload => backend.getSelectionVolumePreview(payload),
    current: currentPreview, dispose: () => {
      preview = null; onPreview(null);
      if (pageFocus) pageFocus.disabled = true;
    }, progress: (loaded, total) => {
      if (previewStatus) previewStatus.textContent = `Loading accepted support preview: ${loaded.toLocaleString()} of ${total.toLocaleString()} support cells…`;
    }, publish: value => {
      preview = value; onPreview(value); updateControls(value); updatePreviewStatus();
    } });
  function updatePreviewStatus() {
    if (!previewStatus || !preview) return;
    previewStatus.textContent = `All ${preview.total_cells.toLocaleString()} support cells. Accepted support with inward/outward guides; normal filtering also applies. ${preview.freshness === 'current' ? '' : `Status: ${preview.freshness}.`}`;
    pageFocus.disabled = false;
  }
  function updateControls(values) {
    for (const [name] of volumeControls) {
      const control = controls[name]; if (!control) continue;
      control.detail.textContent = `Auto: ${values.defaults?.[name] ?? 'unavailable'}; resolved: ${values.resolved?.[name] ?? 'unavailable'}`;
      if (control.mode.value === 'auto') control.input.placeholder = String(values.resolved?.[name] ?? 'Auto');
    }
  }
  function optionFields(options = {}) {
    controls = {};
    const group = element('fieldset'); group.append(element('legend', 'Volume controls')); root.append(group);
    for (const [name, label] of volumeControls) {
      const row = element('div', undefined, 'owned-control'),
        mode = select(`owned-${name}-mode`, [['auto', 'Auto'], ['override', 'Override']], row, label),
        value = input(`owned-${name}`, options[name] ?? '', 'Override value', row),
        detail = element('p', 'Automatic defaults are derived from the authoring scan.', 'hint');
      value.type = 'number'; value.step = 'any'; value.min = name === 'normal_angle_degrees' ? '0.000001' : '0';
      if (name === 'normal_angle_degrees') value.max = '90';
      mode.value = options[name] == null ? 'auto' : 'override'; value.disabled = mode.value === 'auto';
      mode.onchange = () => {
        value.disabled = mode.value === 'auto';
        if (value.disabled) value.value = '';
        else { if (value.value === '' && value.placeholder !== 'Auto') value.value = value.placeholder || ''; value.focus(); }
      };
      row.append(detail); group.append(row); controls[name] = { mode, input: value, detail };
    }
  }
  async function mutate(operation) {
    if (applying || isLocked()) return false;
    applying = true; onBusy(true); root.setAttribute('aria-busy', 'true');
    try {
      const graph = await operation(); onGraph(graph); show(context, true); onApplied(); return true;
    } catch (error) { message.textContent = error.message; onStatus(error.message, true); return false; }
    finally { applying = false; onBusy(false); root.setAttribute('aria-busy', 'false'); }
  }
  function fill(control, records, placeholder) {
    if (!control) return;
    const selected = control.value;
    control.replaceChildren();
    const empty = element('option', placeholder); empty.value = ''; control.append(empty);
    for (const item of records) { const option = element('option', `${item.label}${item.eligible === false && item.reason ? ` — ${item.reason}` : ''}`); option.value = referenceKey(item.reference); option.disabled = item.eligible === false; option.title = item.reason || ''; control.append(option); }
    control.value = selected;
  }
  function populateChoices() {
    const volumes = (choices?.volumes || []).map(item => ({ ...item,
      label: `${getCurrent().graph.recipe.nodes.find(node => node.id === item.reference.selection)?.label || item.reference.selection} / ${item.source_label} / ${item.label}${item.freshness === 'refresh_required' ? ' — Refresh required' : ''}` }));
    fill(sourceChoice, volumes, 'Choose source volume…');
    if (sourceChoice && choices && !sourcePrefilled) {
      if (record().entry?.source_volume) sourceChoice.value = referenceKey(record().entry.source_volume);
      sourcePrefilled = true;
    }
    const surfaces = (choices?.surfaces || []).filter(item => item.kind === author?.kind.value);
    fill(fitChoice, surfaces, 'Choose fitted surface…');
    if (fitFeedback) { fitFeedback.hidden = author.kind.value === 'mesh'; fitFeedback.textContent = surfaces.some(item => item.eligible)
      ? 'The fitted surface must use this Selection in its authoring solve.'
      : `No eligible ${author.kind.value} surface. ${surfaces.map(item => item.reason).filter(Boolean).join(' ') || 'Create and evaluate a matching fit using this Selection.'}`; }
    fill(targetChoice, volumes.filter(item => item.reference.selection === context.selection).map(item => ({ ...item, eligible: true, reason: '' })), 'Choose volume to replace…');
    if (targetChoice && choices && author?.target) { targetChoice.value = referenceKey(author.target); author.target = null; }
    if (fitChoice && choices && author?.fit) { fitChoice.value = referenceKey(author.fit); author.fit = null; }
    if (detail && !choices?.volumes?.length && record().entry?.mode === 'generated')
      detail.textContent = 'No source volumes are available. Load an authoring scan and explicitly create a mesh or analytic volume under its Selection entry.';
  }
  // Inspecting painted membership has no candidate input to populate.
  const needsChoices = () => !!context && (!!sourceChoice || !!author &&
    (author.mode.value === 'replace' || author.kind.value !== 'mesh'));
  function cancelChoices() {
    if (!choiceController) return;
    const controller = choiceController;
    choiceController = null; pendingChoicesKey = null; choiceSequence++;
    controller.abort();
  }
  async function loadChoices() {
    if (!needsChoices()) { cancelChoices(); return; }
    const state = getCurrent(), requestToken = state.graph.token, requestGeneration = state.generation, requestRevision = state.graph.revision,
      selection = context.selection, entry = author ? active()?.id : context.entry,
      // Evaluation can change eligibility without changing the authored token.
      requestKey = JSON.stringify([requestGeneration, requestToken, requestRevision, selection, entry]);
    if (choicesKey === requestKey) { populateChoices(); return; }
    if (pendingChoicesKey === requestKey) return;
    cancelChoices();
    const ticket = ++choiceSequence, key = renderedKey, draft = draftSignature(),
      controller = new AbortController();
    choiceController = controller;
    pendingChoicesKey = requestKey;
    const current = () => ticket === choiceSequence && key === renderedKey && needsChoices() &&
      requestToken === getCurrent().graph.token && requestGeneration === getCurrent().generation &&
      requestRevision === getCurrent().graph.revision;
    try {
      const value = await backend.getSelectionVolumeChoices({ token: requestToken, selection, ...(entry ? { entry } : {}) },
        { signal: controller.signal });
      if (!current()) return;
      if (value.token !== requestToken || value.selection !== selection) throw Error('Volume choices belong to an outdated selection.');
      const untouched = draft === draftSignature();
      choices = value; choicesKey = requestKey; populateChoices();
      if (untouched && !author) onApplied();
    } catch (error) { if (current()) message.textContent = error.message; }
    finally {
      if (choiceController === controller) choiceController = null;
      if (ticket === choiceSequence) pendingChoicesKey = null;
    }
  }
  async function loadPreview() {
    const reference = currentPreview();
    if (!reference.reference) return;
    try { await reader.load(reference.reference, reference.archive_sha256); }
    catch (error) { message.textContent = error.message; previewStatus.textContent = 'Accepted support preview unavailable; no incomplete support is displayed.'; }
  }
  function authorForm(replace = null) {
    // Capture is a continuation of this volume's live control intent. Validate
    // before replacing any DOM so an invalid override remains repairable.
    let draftOptions;
    if (volumeControls.every(([name]) => controls[name])) {
      try { draftOptions = readVolumeOptions(controls); }
      catch (error) { message.textContent = error.message; onStatus(error.message, true); return; }
    }
    cancelChoices(); choiceSequence++;
    pendingChoicesKey = null;
    const acceptedPreview = preview;
    reader.clear(); root.replaceChildren(); message = hint('');
    sourceChoice = fitChoice = fitFeedback = targetChoice = null; author = null;
    const current = record(), loaded = active();
    const unavailable = text => { hint(text); button('Back to selection', () => { show(context, true); onApplied(); }); };
    if (!loaded && current.selection.entries) { unavailable('Load an authoring scan before creating or replacing a volume.'); return; }
    if (loaded?.mode === 'generated' && !loaded.source_volume) { unavailable('Choose a source volume for this scan before capturing generated support.'); return; }
    if (loaded?.mode === 'painted' && !loaded.ids.length || !current.selection.entries && !current.selection.ids.length) {
      unavailable('Paint at least one source vertex before capturing a volume.'); return;
    }
    root.append(element('h3', 'Capture selection volume'));
    const mode = select('owned-author-mode', [['create', 'Create new volume'], ['replace', 'Replace existing volume']], root, 'Operation');
    mode.value = replace ? 'replace' : 'create';
    targetChoice = select('owned-author-target', [], root, 'Existing volume');
    const label = input('owned-volume-label', current.volume?.label || 'Selection volume', 'Name'),
      kind = select('owned-volume-kind', [['mesh', 'Mesh support'], ['plane', 'Plane fit'], ['cylinder', 'Cylinder fit'], ['cone', 'Cone fit']], root, 'Definition');
    kind.value = current.volume?.kind || 'mesh';
    fitChoice = select('owned-volume-fit', [], root, 'Fitted surface');
    fitFeedback = hint('');
    author = { mode, label, kind, target: replace, fit: current.volume?.fit };
    hint('Replace explicitly adopts this volume on the current scan entry and updates its links. The original painted entry is preserved.');
    hint('Auto values are recomputed from the current scan when captured. Previously saved defaults are shown only as a reference.');
    optionFields(draftOptions || current.volume?.options);
    if (acceptedPreview) updateControls(acceptedPreview);
    const sync = () => {
      targetChoice.parentElement.hidden = mode.value !== 'replace'; fitChoice.parentElement.hidden = kind.value === 'mesh';
      populateChoices(); void loadChoices();
    };
    mode.onchange = sync; kind.onchange = () => { author.fit = null; sync(); }; sync();
    const footer = element('div', undefined, 'owned-editor-footer'); root.append(footer);
    button('Capture volume', () => mutate(() => backend.authorSelectionVolume(volumeAuthorPayload({
      token: getCurrent().graph.token, selection: context.selection, ...(loaded ? { entry: loaded.id } : {}), mode: mode.value,
      target: targetChoice.value ? JSON.parse(targetChoice.value) : null, volume: newId(), label: label.value,
      kind: kind.value, fit: fitChoice.value ? JSON.parse(fitChoice.value) : null, options: readVolumeOptions(controls),
    }))), footer);
    button('Cancel', () => { show(context, true); onApplied(); }, footer);
  }
  function show(next, force = false) {
    const key = JSON.stringify(next);
    context = next ? { ...next } : null;
    if (!force && key === renderedKey) return refresh();
    cancelChoices(); renderedKey = key; choiceSequence++; reader.clear(); choices = null; choicesKey = pendingChoicesKey = null; author = null; sourcePrefilled = false;
    controls = {}; sourceChoice = fitChoice = fitFeedback = targetChoice = previewStatus = null;
    root.replaceChildren(); root.hidden = !context;
    if (!context) return;
    token = getCurrent().graph.token; generation = getCurrent().generation; revision = getCurrent().graph.revision;
    const { selection, entry, volume } = record();
    if (!selection || context.entry && !entry || context.volume && !volume) { hint('This selection item is no longer available.'); return; }
    message = hint(''); root.append(element('h3', volume?.label || (entry ? 'Scan selection entry' : 'Selection entries')));
    if (!entry) {
      hint('One entry per scan. Fits use the current scan entry automatically. Volumes are created explicitly.');
      for (const child of selection.entries || []) button(`${child.scan.descriptor?.filename || child.scan.source_sha256.slice(0, 12)} · ${child.mode}`, () => onOpen({ selection: selection.id, entry: child.id }));
      if (!selection.entries) hint('This is a legacy painted selection. Creating its first volume will preserve and convert its membership.');
      if (active() || !selection.entries) button('Create volume…', () => authorForm());
      return;
    }
    const loaded = active()?.id === entry.id;
    hint(`${entry.scan.descriptor?.filename || 'Saved scan'} · ${loaded ? 'Current scan' : 'Inactive scan'} · ${entry.mode}`);
    hint(`Source ${entry.scan.source_sha256} · reference ${entry.scan.reference_sha256} · ${entry.scan.vertices.toLocaleString()} vertices`);
    detail = hint('');
    if (volume) {
      hint(`${volume.kind} support. ${loaded ? 'Authoring scan loaded.' : 'Saved support belongs to an inactive scan. Controls remain editable; replacing explicitly adopts support from the current scan.'}`);
      optionFields(volume.options);
      const footer = element('div', undefined, 'owned-editor-footer'); root.append(footer);
      button('Apply volume controls', () => mutate(() => backend.setSelectionVolumeOptions({ token: getCurrent().graph.token,
        reference: { selection: context.selection, entry: context.entry, volume: context.volume }, options: readVolumeOptions(controls) })), footer);
      button('Create / replace volume…', () => authorForm({ selection: context.selection, entry: context.entry, volume: context.volume }), footer).disabled = !active();
      button('Delete volume…', async () => {
        if (!confirmDelete(volume.label)) return;
        const reference = { selection: context.selection, entry: context.entry, volume: context.volume };
        if (await mutate(() => backend.deleteSelectionVolume({ token: getCurrent().graph.token, reference })))
          onOpen({ selection: reference.selection, entry: reference.entry });
      }, footer);
      previewStatus = hint('Loading accepted support preview…');
      const pages = element('div', undefined, 'owned-preview-pages'); root.append(pages);
      pageFocus = button('Focus volume', () => onFocus(preview), pages);
      pageFocus.disabled = true;
      hint('Mesh support is a union of local slabs. Coverage is a sampled warning and can miss small gaps.');
      void loadPreview();
    } else {
      detail.textContent = loaded ? entry.mode === 'painted' ? `${entry.ids.length.toLocaleString()} painted vertices. Paint using the selection controls below.`
        : 'Generated membership is read-only and updates from its chosen source volume.' : 'Inactive scan membership is preserved. Load this scan to paint or regenerate it.';
      if (entry.mode === 'generated') {
        root.append(element('h4', entry.source_volume ? 'Source volume' : '? Choose source volume'));
        sourceChoice = select('owned-source-volume', [], root, 'Source volume');
        const footer = element('div', undefined, 'owned-editor-footer'); root.append(footer);
        button('Apply source volume', () => mutate(() => {
          if (!sourceChoice.value) throw Error('Choose a source volume before applying.');
          return backend.chooseSelectionVolume({ token: getCurrent().graph.token,
            selection: context.selection, entry: context.entry, source_volume: JSON.parse(sourceChoice.value) });
        }), footer);
        if (entry.source_volume) button('Clear source volume', () => mutate(() => backend.chooseSelectionVolume({
          token: getCurrent().graph.token, selection: context.selection, entry: context.entry, source_volume: null })), footer);
      }
      for (const item of entry.volumes) button(item.label, () => onOpen({ ...context, volume: item.id }));
      if (loaded) button('Create / replace volume…', () => authorForm());
      else hint('Load the authoring scan to create new support.');
      void loadChoices();
    }
    refresh();
  }
  function refresh() {
    if (!context) return;
    const state = getCurrent(), { selection, entry } = record();
    if (!selection || context.entry && !entry) return show(null, true);
    const currentEntry = active(), related = context.volume
      ? referenceKey(currentEntry?.source_volume) === referenceKey(currentPreview().reference)
      : !context.entry || currentEntry?.id === context.entry;
    const diagnostic = related ? state.graph.diagnostics?.[selection.id] : null,
      coverage = related ? state.graph.derived?.[selection.id]?.coverage : null;
    if (message && !applying) message.textContent = diagnostic?.message || (coverage
      ? `${coverage.partial ? 'Partial coverage warning' : 'Estimated coverage'}: ${(100 * coverage.fraction).toFixed(1)}% of saved support (${coverage.samples.toLocaleString()} area samples). Sampling can miss small gaps; valid fits remain available.` : '');
    const inputsChanged = token !== state.graph.token || generation !== state.generation;
    if (inputsChanged) {
      token = state.graph.token; generation = state.generation; reader.clear();
      if (context.volume && !author) void loadPreview();
    }
    if (inputsChanged || revision !== state.graph.revision) {
      revision = state.graph.revision; void loadChoices();
    }
  }
  return { show, refresh, author: authorForm, clear: () => show(null, true),
    get context() { return context; }, get preview() { return preview; } };
}
