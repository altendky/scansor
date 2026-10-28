// Source transitions retain the accepted scene and live edit controls until a
// complete backend bundle and its display resources are ready to install.
export function createSourceProjectController({ backend, getCurrent, canTransition,
  setTransitionBusy, prepareWorkspace, inspectSource, prepareTransition, status,
  onActionsLoaded = () => {}, onWorkspaceReady = () => {}, onActiveScanChanged = () => {} }) {
  const $ = id => document.getElementById(id);
  let active = null, unavailable = null, releaseFence = null;
  let runtimeLocked = false, deferredRelease = null;
  let actionsIntent = 0, pendingActions = null;
  let pendingReplacement = null;
  let pendingVirtual = null;
  const empty = document.createElement('section');
  empty.id = 'empty-project';
  empty.setAttribute('aria-label', 'Empty project');
  empty.innerHTML = '<h2>Start a project</h2><div class="row"><button type="button" data-add-scan>Add scan…</button><button type="button" data-choose-example>Choose example…</button></div><p id="empty-project-detail"></p>';
  $('viewport').append(empty);
  const source = () => getCurrent().recipe.nodes.find(node => node.operation === 'source');
  const cursor = () => { const value = getCurrent(); return { generation: value.generation, token: value.token }; };
  const current = before => {
    const now = cursor(); return now.generation === before.generation && now.token === before.token;
  };
  const locked = () => runtimeLocked || !!active || !!unavailable || !canTransition();
  const replacementSupported = () => !!backend.capabilities.sourceReplacement
    && getCurrent().metadata.source_replacement?.supported === true && !!source();
  const virtualProfile = () => {
    const profile = getCurrent().metadata.virtual_scans;
    const validScan = scan => scan && typeof scan.binding === 'string'
      && Number.isSafeInteger(scan.vertices) && scan.vertices > 0
      && Number.isSafeInteger(scan.triangles) && scan.triangles >= 0;
    return backend.capabilities.virtualScans && source()?.descriptor
      && profile?.supported === true && profile.version === 1
      && validScan(profile.original) && (!profile.virtual || validScan(profile.virtual))
      && [profile.original.binding, profile.virtual?.binding].includes(profile.active_binding) ? profile : null;
  };
  const counts = scan => `${scan.vertices.toLocaleString()} vertices · ${scan.triangles.toLocaleString()} triangles`;
  function lockRuntime() {
    invalidateReplacement();
    invalidateVirtual();
    runtimeLocked = true;
    setTransitionBusy(true);
    $('retry-source-workspace').disabled = true;
    refresh();
  }
  function refresh() {
    const hasSource = !!source(), supported = !!backend.capabilities.sourceImport;
    $('load').disabled = locked();
    for (const [id, enabled] of [
      ['new-project', supported], ['add-scan', supported && !hasSource],
      ['inspect-source', hasSource], ['remove-scan', supported && hasSource],
      ['replace-scan', replacementSupported()],
      ['create-virtual-scan', virtualProfile()?.can_create === true],
    ]) $(id).disabled = !enabled || locked();
    // Restore belongs to a retained example, never a user-source substitution.
    $('reset').disabled = locked() || !hasSource || !!source().descriptor;
    $('empty-project').hidden = hasSource;
    const limits = getCurrent().metadata.capabilities?.source_import?.limits;
    const limitText = limits ? ` Up to ${(limits.max_source_bytes / 1048576).toLocaleString()} MiB, ${limits.max_vertices.toLocaleString()} vertices and ${limits.max_faces.toLocaleString()} triangles.` : '';
    $('empty-project-detail').textContent = supported
      ? `Add one binary little-endian triangle PLY with float32 XYZ and complete normals. Units remain unknown; coordinates are preserved.${limitText}`
      : 'Choose an example. Scan import is unavailable in this backend.';
    for (const button of document.querySelectorAll('[data-add-scan]')) button.disabled = !supported || hasSource || locked();
    for (const button of document.querySelectorAll('[data-choose-example]')) button.disabled = locked();
    for (const button of document.querySelectorAll('[data-remove-scan]')) button.disabled = !supported || !hasSource || locked();
    for (const button of document.querySelectorAll('[data-replace-scan]')) button.disabled = !replacementSupported() || locked();
    const profile = virtualProfile();
    $('create-virtual-scan').hidden = !profile?.can_create;
    for (const button of document.querySelectorAll('[data-create-virtual-scan]')) {
      button.hidden = !profile || !profile.can_create;
      button.disabled = !profile?.can_create || locked();
    }
    $('active-scan-controls').hidden = !profile;
    $('active-scan').disabled = !profile?.can_activate || locked();
    if (profile) {
      const scans = [['Original', profile.original], ['Virtual', profile.virtual]].filter(([, scan]) => scan);
      $('active-scan').replaceChildren(...scans.map(([label, scan]) => {
        const option = document.createElement('option');
        option.value = scan.binding; option.textContent = `${label} — ${counts(scan)}`;
        return option;
      }));
      $('active-scan').value = profile.active_binding;
      const virtual = profile.virtual?.binding === profile.active_binding;
      const scan = virtual ? profile.virtual : profile.original;
      $('active-scan-summary').textContent = `${virtual ? 'Virtual scan · Preliminary work' : 'Original scan'} · ${counts(scan)}. Used for display and fitting.`;
    } else {
      $('active-scan').replaceChildren();
      $('active-scan-summary').textContent = '';
    }
  }
  function lockSurfaces() {
    const elements = [...new Set(document.querySelectorAll(
      'main, #project-toolbar, #active-scan-controls, .workspace-toolbar, #edit-popup, #edit-host, .workspace-edit-workflow, #example-dialog'))];
    const previous = elements.map(element => element.inert);
    const hold = () => elements.forEach(element => { element.inert = true; });
    hold();
    const release = () => elements.forEach((element, index) => { element.inert = previous[index]; });
    release.hold = hold;
    return release;
  }
  function progress(record) {
    if (!active) return;
    active.record = record;
    const count = Number.isFinite(record.completed) && Number.isFinite(record.total)
      ? ` ${record.completed.toLocaleString()} / ${record.total.toLocaleString()} ${record.unit || ''}` : '';
    $('source-progress-detail').textContent = `${record.phase || record.status || 'Importing'}${count}`;
    $('cancel-source-import').disabled = !!record.terminal || active.cancelRequested;
    if (active.cancelRequested && record.operationId && !active.cancelSent && !record.terminal) void cancel();
  }
  async function cancel() {
    const operation = active;
    if (!operation || !['scan', 'replacement', 'actions', 'virtual', 'activation'].includes(operation.kind) || operation.record?.terminal) return;
    operation.cancelRequested = true;
    $('cancel-source-import').disabled = true;
    $('source-progress-detail').textContent = 'Cancel requested. Waiting for a safe boundary…';
    if (!operation.record?.operationId || operation.cancelSent) return;
    operation.cancelSent = true;
    try {
      const record = await backend.cancelSourceOperation({ operationId: operation.record.operationId, ...operation.cursor });
      if (active === operation) progress(record);
    } catch (error) {
      if (active === operation) {
        operation.cancelSent = false;
        operation.cancelRequested = false;
        $('cancel-source-import').disabled = !!operation.record?.terminal;
        status(`Could not confirm cancellation: ${error.message}. Retry Cancel while the import is active.`, true);
      }
    }
  }
  function confirmProject(kind, replacesSource) {
    if (['scan', 'replacement', 'virtual', 'activation'].includes(kind) || !getCurrent().recipe.nodes.length) return true;
    if (kind === 'actions' && !replacesSource)
      return window.confirm('Replace the current actions? The saved active scan will be restored using available source data. Save actions first to keep your work; actions do not save unfinished edits.');
    return window.confirm('Discard the current source and committed actions? Save actions first to keep your work. Actions do not include the scan file or unfinished edits.');
  }
  function fence(error, before, release, kind) {
    unavailable = { error, prior: before, kind };
    releaseFence = release;
    // A failed example dialog must not keep the explicit recovery controls inert.
    if ($('example-dialog').open) $('example-dialog').close();
    $('source-unavailable').hidden = false;
    status('The project change may have been accepted, but its display is unavailable. Load the current project to continue; prior actions and unfinished edits are retained.', true);
  }
  async function prepareRuntimeRestore(graph, restoreControls = () => {}, isCurrent = () => true) {
    invalidateReplacement();
    while (active) await active.settled;
    if (!isCurrent()) return false;
    const displayed = getCurrent().generation, recovered = graph?.project_generation;
    const legacy = !backend.capabilities.sourceImport && !backend.capabilities.sourceActions;
    const graphSafe = !unavailable && (legacy || Number.isSafeInteger(displayed) && displayed >= 0
      && Number.isSafeInteger(recovered) && recovered === displayed);
    // Transfer the bootstrap lock synchronously after deciding, before taking a
    // new source fence snapshot. No event can observe an unlocked source fence.
    restoreControls();
    runtimeLocked = false;
    deferredRelease?.(); deferredRelease = null;
    $('retry-source-workspace').disabled = false;
    if (graphSafe) { setTransitionBusy(false); refresh(); return true; }
    if (!unavailable) fence(Error('Recovered source requires a complete workspace.'), getCurrent(), lockSurfaces(), 'recovery');
    else releaseFence.hold();
    setTransitionBusy(true);
    refresh();
    return false;
  }
  async function run(kind, request, { expected = cursor(), replacesSource = true, proof: capturedProof, onFailure } = {}) {
    if (locked()) return false;
    if (kind !== 'actions') invalidateActions();
    if (!current(expected)) { status('The project changed. Load the actions again to review the current project.', true); return false; }
    const before = getCurrent(), proof = capturedProof || prepareTransition();
    if (!proof || !confirmProject(kind, replacesSource)) return false;
    if (!proof.isCurrent() || !current(expected)) {
      status('The project or unfinished edit changed. Review it and try again.', true);
      return false;
    }
    let settled;
    const operation = active = { kind, cursor: expected, record: null, accepted: false, mutationStarted: false,
      settled: new Promise(resolve => { settled = resolve; }) };
    const release = lockSurfaces();
    setTransitionBusy(true);
    $('source-progress').hidden = !['scan', 'replacement', 'actions', 'virtual', 'activation'].includes(kind) || kind === 'actions' && !backend.capabilities.sourceActions;
    $('cancel-source-import').disabled = false;
    $('cancel-source-import').textContent = ['virtual', 'activation'].includes(kind) ? 'Cancel' : 'Cancel import';
    $('source-progress-detail').textContent = kind === 'actions' ? 'Preparing actions…'
      : kind === 'replacement' ? 'Preparing replacement scan…'
        : kind === 'virtual' ? 'Creating virtual scan…'
          : kind === 'activation' ? 'Switching active scan…' : 'Preparing scan import…';
    refresh();
    let install, actionsWarnings, activeScanChanged = false;
    try {
      const bundle = await request(operation);
      operation.accepted = true;
      install = prepareWorkspace(bundle);
      if (!proof.isCurrent()) throw new Error('The unfinished edit changed after acceptance. Review it before loading the accepted project.');
      proof.finish();
      install();
      activeScanChanged = kind === 'virtual' || kind === 'activation';
      if (kind === 'actions') actionsWarnings = bundle.import_warnings ?? bundle.metadata.import_warnings ?? [];
      status(kind === 'scan' ? 'Scan added. Create a selection to start fitting.'
        : kind === 'replacement' ? 'Scan replaced. Review selection entries and choose a source volume where needed.'
        : kind === 'virtual' ? 'Virtual scan active for preliminary work. Review selection entries and choose a source volume where needed, then evaluate.'
        : kind === 'activation' ? 'Active scan changed. Evaluate to refresh results; review selection entries and choose a source volume where needed.'
        : kind === 'reset' ? 'Empty project. Add a scan or choose an example.'
          : kind === 'actions' ? 'Actions loaded. Evaluate to refresh dependent results.' : 'Example loaded.');
      return true;
    } catch (error) {
      const uncertain = operation.accepted || error.code === 'accepted_workspace_unavailable'
        || operation.mutationStarted && (error instanceof TypeError || error.name === 'TimeoutError'
          || ['transport_error', 'observation_superseded', 'worker_failed', 'worker_startup_failed'].includes(error.code));
      if (uncertain) fence(error, before, release, kind);
      else status(error.message, true);
      onFailure?.(error);
      return false;
    } finally {
      try {
        install?.dispose?.();
        active = null;
        setTransitionBusy(runtimeLocked || !!unavailable);
        $('source-progress').hidden = true;
        if (!unavailable) {
          if (runtimeLocked) deferredRelease = release;
          else release();
        }
        refresh();
        if (actionsWarnings && !unavailable) onActionsLoaded(actionsWarnings);
        if (activeScanChanged && !unavailable && !runtimeLocked) onActiveScanChanged();
      } finally { settled(); }
    }
  }
  async function addScan(file) {
    if (!file) return false;
    if (!backend.capabilities.sourceImport || source()) {
      $('scan-file').value = '';
      status(source() ? 'This project already has a source. Use New project to start again.' : 'Scan import is unavailable in this backend.', true);
      return false;
    }
    try {
      return await run('scan', async operation => {
        operation.mutationStarted = true;
        return backend.addScan({ file, ...operation.cursor }, { onProgress: progress });
      });
    } finally { $('scan-file').value = ''; }
  }
  function invalidateReplacement() {
    pendingReplacement = null;
    $('replacement-file').value = '';
  }
  function prepareReplacementChoice(openPicker) {
    if (locked()) return false;
    if (!replacementSupported()) {
      status('Replacing this source is unavailable. Load an imported scan to replace it with an already-aligned scan.', true);
      return false;
    }
    invalidateActions();
    const expected = cursor(), proof = prepareTransition();
    if (!proof) return false;
    if (!proof.isCurrent() || !current(expected)) {
      status('The project or unfinished edit changed. Review it and try again.', true);
      return false;
    }
    pendingReplacement = { cursor: expected, proof };
    if (openPicker) $('replacement-file').click();
    return true;
  }
  function beginReplacementChooser() { return prepareReplacementChoice(true); }
  async function chooseReplacementFile(file) {
    const pending = pendingReplacement;
    invalidateReplacement();
    if (!file || !pending || locked()) return false;
    if (!replacementSupported() || !current(pending.cursor) || !pending.proof.isCurrent()) {
      status('The project or unfinished edit changed. Choose the replacement scan again.', true);
      return false;
    }
    return run('replacement', operation => {
      operation.mutationStarted = true;
      return backend.replaceScan({ file, ...operation.cursor }, { onProgress: progress });
    }, { expected: pending.cursor, proof: pending.proof });
  }
  async function replaceScan(file) {
    if (!file) return false;
    if (!pendingReplacement && !prepareReplacementChoice(false)) return false;
    return chooseReplacementFile(file);
  }
  function invalidateVirtual() {
    pendingVirtual = null;
    if ($('virtual-scan-dialog').open) $('virtual-scan-dialog').close();
  }
  function beginVirtualScan() {
    if (locked()) return false;
    if (!virtualProfile()?.can_create) {
      status('Creating a virtual scan is unavailable for this source.', true);
      return false;
    }
    invalidateActions();
    const expected = cursor(), proof = prepareTransition();
    if (!proof) return false;
    if (!current(expected) || !proof.isCurrent()) {
      status('The project or unfinished edit changed. Review it and try again.', true);
      return false;
    }
    pendingVirtual = { cursor: expected, proof };
    $('virtual-scan-divisions').value = '32';
    $('virtual-scan-error').textContent = '';
    $('virtual-scan-dialog').showModal();
    $('virtual-scan-divisions').focus();
    return true;
  }
  async function createVirtualScan() {
    const pending = pendingVirtual;
    if (!pending || locked()) return false;
    const value = $('virtual-scan-divisions').value.trim();
    const divisions = Number(value);
    if (!value || !Number.isInteger(divisions) || divisions < 2 || divisions > 4096) {
      $('virtual-scan-error').textContent = 'Choose a whole number from 2 to 4096.';
      return false;
    }
    if (!virtualProfile()?.can_create || !current(pending.cursor) || !pending.proof.isCurrent()) {
      invalidateVirtual();
      status('The project or unfinished edit changed. Create the virtual scan again.', true);
      return false;
    }
    invalidateVirtual();
    return run('virtual', operation => {
      operation.mutationStarted = true;
      return backend.createVirtualScan({ settings: { version: 'edge-cell-reduction-v1', divisions }, ...operation.cursor }, { onProgress: progress });
    }, { expected: pending.cursor, proof: pending.proof });
  }
  async function activateScan(binding) {
    const profile = virtualProfile();
    if (locked() || !profile?.can_activate) { refresh(); return false; }
    if (binding === profile.active_binding) { refresh(); return false; }
    if (![profile.original?.binding, profile.virtual?.binding].includes(binding)) {
      status('Choose the original or virtual scan in this project.', true);
      refresh(); return false;
    }
    try {
      return await run('activation', operation => {
        operation.mutationStarted = true;
        return backend.activateScan({ binding, ...operation.cursor }, { onProgress: progress });
      });
    } finally { refresh(); }
  }
  async function resetProject() {
    if (!backend.capabilities.sourceImport) return false;
    return run('reset', operation => {
      operation.mutationStarted = true;
      return backend.resetProject({ ...operation.cursor, confirmedDiscard: true });
    });
  }
  async function selectExample(example) {
    return run('example', async operation => {
      operation.mutationStarted = true;
      await backend.selectExample({ example, token: operation.cursor.token });
      operation.accepted = true;
      return loadBundle();
    });
  }
  async function restoreExample() {
    if (!source() || source().descriptor) return false;
    return run('example', async operation => {
      const recipe = await backend.getExampleRecipe();
      operation.mutationStarted = true;
      const graph = await backend.replaceRecipe({ token: operation.cursor.token, recipe });
      operation.accepted = true;
      return loadBundle({ token: graph.token }, graph);
    });
  }
  async function loadBundle(options, graph) {
    // The delivered fixed-source worker predates bundled graphs. Its immutable
    // example accepts no load payload and has no source-transition epoch.
    const legacy = !backend.capabilities.sourceImport && !backend.capabilities.sourceActions;
    const bundle = await backend.loadWorkspace(legacy ? undefined : options);
    if (legacy && !bundle.graph) {
      const acceptedGraph = graph || await backend.getGraph();
      if (options?.token !== undefined && acceptedGraph.token !== options.token)
        throw new Error('The accepted actions changed before their display could be loaded.');
      return { ...bundle, graph: acceptedGraph };
    }
    return bundle;
  }
  function invalidateActions() {
    invalidateReplacement();
    invalidateVirtual();
    actionsIntent++;
    pendingActions = null;
    if ($('locate-source-dialog').open) $('locate-source-dialog').close();
  }
  function validIntent(intent, expected) {
    return intent === actionsIntent && current(expected) && !active && !unavailable;
  }
  function showLocator(pending, error = '') {
    if (!validIntent(pending.intent, pending.cursor)) return;
    pendingActions = pending;
    const descriptor = pending.inspection.descriptor;
    const virtual = pending.recipe?.nodes?.some(node => node.operation === 'source' && node.virtual_scan);
    $('locate-source-hint').textContent = `Locate the ${virtual ? 'original' : 'source'} scan${descriptor?.filename ? ` (${descriptor.filename})` : ''}. Its bytes and interpretation must match the saved actions; the filename may differ.`;
    $('locate-source-error').textContent = error;
    if (!$('locate-source-dialog').open) $('locate-source-dialog').showModal();
  }
  async function openPending(pending, file) {
    if (!validIntent(pending.intent, pending.cursor)) {
      status('The project changed. Load the actions again.', true);
      return false;
    }
    if ($('locate-source-dialog').open) $('locate-source-dialog').close();
    let failure;
    const accepted = await run('actions', async operation => {
      operation.mutationStarted = true;
      if (backend.capabilities.sourceActions)
        return backend.openActions({ recipe: pending.recipe, file, ...operation.cursor, confirmedDiscard: true }, { onProgress: progress });
      const graph = await backend.importRecipe({ recipe: pending.recipe, token: operation.cursor.token });
      operation.accepted = true;
      return { ...await loadBundle({ token: graph.token }, graph), import_warnings: graph.import_warnings || [] };
    }, { expected: pending.cursor, replacesSource: pending.inspection.classification !== 'matching',
      onFailure: error => { failure = error; } });
    if (accepted) { pendingActions = null; actionsIntent++; }
    else if (file && validIntent(pending.intent, pending.cursor)) showLocator(pending, failure?.message || 'No changes were made. Choose a source to try again.');
    return accepted;
  }
  async function inspectActions(recipe, intent, expected) {
    try {
      let inspection;
      if (backend.capabilities.sourceActions) {
        inspection = await backend.inspectActions({ recipe, ...expected });
        if (inspection?.generation !== expected.generation || inspection?.token !== expected.token
          || !['matching', 'locate', 'empty'].includes(inspection?.classification)
          || inspection.uploadRequired !== (inspection.classification === 'locate')
          || inspection.classification === 'locate' && !inspection.descriptor)
          throw new Error('Invalid actions inspection response. Load the actions again.');
      }
      else {
        const sources = recipe?.nodes?.filter(node => node.operation === 'source') || [];
        const actual = source(), saved = sources[0];
        if (sources.length !== 1 || !actual || saved.descriptor || actual.descriptor
          || typeof saved.source_sha256 !== 'string' || typeof saved.reference_sha256 !== 'string'
          || saved.source_sha256 !== actual.source_sha256 || saved.reference_sha256 !== actual.reference_sha256)
          throw new Error('This backend can load actions only for its current matching example. Source lookup is unavailable.');
        inspection = { classification: 'matching' };
      }
      if (!validIntent(intent, expected)) return false;
      const pending = { recipe, inspection, intent, cursor: expected };
      if (inspection.uploadRequired) { showLocator(pending); return false; }
      return await openPending(pending);
    } catch (error) {
      if (validIntent(intent, expected)) status(error.message, true);
      return false;
    }
  }
  async function loadActions(recipe) {
    if (locked()) return false;
    invalidateActions();
    const intent = actionsIntent, expected = cursor();
    try { return await inspectActions(structuredClone(recipe), intent, expected); }
    catch (error) { if (validIntent(intent, expected)) status(error.message, true); return false; }
  }
  async function loadActionsFile(file) {
    if (!file || locked()) return false;
    invalidateActions();
    const intent = actionsIntent, expected = cursor();
    try {
      const declared = getCurrent().metadata.actions_limits?.max_json_bytes;
      if (declared !== undefined && (!Number.isSafeInteger(declared) || declared <= 0))
        throw new Error('Invalid actions byte limit. Reload the current workspace.');
      const maximum = declared ?? 1_000_000;
      if (file.size > maximum) throw new Error(declared === undefined ? 'Recipe must be under 1 MB' :
        `Actions exceed the ${(maximum / 1048576).toLocaleString()} MiB file limit`);
      const recipe = JSON.parse(await file.text());
      if (!validIntent(intent, expected)) return false;
      return await inspectActions(recipe, intent, expected);
    } catch (error) { if (validIntent(intent, expected)) status(error.message, true); return false; }
    finally { $('file').value = ''; }
  }
  $('load').onclick = () => {
    if (locked()) return;
    invalidateActions();
    $('file').value = '';
    $('file').click();
  };
  $('file').onchange = () => loadActionsFile($('file').files[0]);
  $('file').addEventListener('cancel', invalidateActions);
  $('choose-located-source').onclick = () => {
    if (!pendingActions || !validIntent(pendingActions.intent, pendingActions.cursor)) return;
    $('locate-source-file').value = '';
    $('locate-source-file').click();
  };
  $('locate-source-file').onchange = async () => {
    const pending = pendingActions, file = $('locate-source-file').files[0];
    try { if (pending && file) await openPending(pending, file); }
    finally { $('locate-source-file').value = ''; }
  };
  $('cancel-locate-source').onclick = invalidateActions;
  $('locate-source-dialog').addEventListener('cancel', event => { event.preventDefault(); invalidateActions(); });
  $('new-project').onclick = resetProject;
  $('remove-scan').onclick = resetProject;
  $('add-scan').onclick = () => {
    if (locked() || !backend.capabilities.sourceImport || source()) return;
    invalidateActions();
    $('scan-file').value = '';
    $('scan-file').click();
  };
  $('scan-file').onchange = () => { const file = $('scan-file').files[0]; void addScan(file); };
  $('scan-file').addEventListener('cancel', () => { /* No transaction or draft discard has started. */ });
  $('replace-scan').onclick = beginReplacementChooser;
  $('replacement-file').onchange = () => chooseReplacementFile($('replacement-file').files[0]);
  $('replacement-file').addEventListener('cancel', invalidateReplacement);
  $('inspect-source').onclick = inspectSource;
  $('create-virtual-scan').onclick = beginVirtualScan;
  $('confirm-create-virtual-scan').onclick = createVirtualScan;
  $('cancel-create-virtual-scan').onclick = invalidateVirtual;
  $('virtual-scan-dialog').addEventListener('cancel', event => { event.preventDefault(); invalidateVirtual(); });
  $('active-scan').onchange = () => activateScan($('active-scan').value);
  $('cancel-source-import').onclick = cancel;
  for (const button of document.querySelectorAll('[data-add-scan]')) button.onclick = () => $('add-scan').click();
  for (const button of document.querySelectorAll('[data-choose-example]')) button.onclick = () => $('choose-example').click();
  for (const button of document.querySelectorAll('[data-remove-scan]')) button.onclick = () => $('remove-scan').click();
  for (const button of document.querySelectorAll('[data-replace-scan]')) button.onclick = () => $('replace-scan').click();
  for (const button of document.querySelectorAll('[data-create-virtual-scan]')) button.onclick = () => $('create-virtual-scan').click();
  $('retry-source-workspace').onclick = async () => {
    if (!unavailable || runtimeLocked) return;
    const proof = prepareTransition();
    if (!proof) return;
    $('retry-source-workspace').disabled = true;
    let install;
    try {
      const bundle = await loadBundle();
      install = prepareWorkspace(bundle);
      if (!proof.isCurrent()) throw new Error('The unfinished edit changed. Review it before loading the project.');
      proof.finish();
      install();
      const actions = unavailable.kind === 'actions';
      unavailable = null;
      releaseFence?.(); releaseFence = null;
      setTransitionBusy(false);
      $('source-unavailable').hidden = true;
      status('Current project loaded.');
      onWorkspaceReady();
      if (actions) onActionsLoaded(bundle.metadata.import_warnings || []);
    } catch (error) { status(`Could not load the current project: ${error.message}`, true); }
    finally { install?.dispose?.(); $('retry-source-workspace').disabled = false; refresh(); }
  };
  $('download-prior-actions').onclick = async () => {
    if (!unavailable) return;
    try {
      const recipe = await backend.exportRecipe({ recipe: unavailable.prior.recipe });
      const url = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2) + '\n'], { type: 'application/json' }));
      try {
        const link = document.createElement('a'); link.href = url; link.download = 'scansor-prior-actions.json'; link.click();
      } finally { URL.revokeObjectURL(url); }
      status('Prior actions downloaded. This does not save the scan file or unfinished edits.');
    } catch (error) { status(error.message, true); }
  };
  return { refresh, addScan, beginReplacementChooser, chooseReplacementFile, replaceScan, beginVirtualScan, createVirtualScan, activateScan, resetProject, selectExample, restoreExample, cancel, loadActions, loadActionsFile, lockRuntime, prepareRuntimeRestore };
}
