// Source transitions retain the accepted scene and live edit controls until a
// complete backend bundle and its display resources are ready to install.
export function createSourceProjectController({ backend, getCurrent, canTransition,
  setTransitionBusy, prepareWorkspace, inspectSource, prepareTransition, status }) {
  const $ = id => document.getElementById(id);
  let active = null, unavailable = null, releaseFence = null;
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
  const locked = () => !!active || !!unavailable || !canTransition();
  function refresh() {
    const hasSource = !!source(), supported = !!backend.capabilities.sourceImport;
    for (const [id, enabled] of [
      ['new-project', supported], ['add-scan', supported && !hasSource],
      ['inspect-source', hasSource], ['remove-scan', supported && hasSource],
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
  }
  function lockSurfaces() {
    const elements = [...new Set(document.querySelectorAll(
      'main, #project-toolbar, .workspace-toolbar, #edit-popup, #edit-host, .workspace-edit-workflow, #example-dialog'))];
    const previous = elements.map(element => element.inert);
    elements.forEach(element => { element.inert = true; });
    return () => elements.forEach((element, index) => { element.inert = previous[index]; });
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
    if (!operation || operation.kind !== 'scan' || operation.record?.terminal) return;
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
  function confirmProject(kind) {
    if (kind === 'scan' || !getCurrent().recipe.nodes.length) return true;
    return window.confirm('Discard the current source and committed actions? Save actions first to keep your work. Actions do not include the scan file or unfinished edits.');
  }
  function fence(error, before, release) {
    unavailable = { error, prior: before };
    releaseFence = release;
    // A failed example dialog must not keep the explicit recovery controls inert.
    if ($('example-dialog').open) $('example-dialog').close();
    $('source-unavailable').hidden = false;
    status('The project change may have been accepted, but its display is unavailable. Load the current project to continue; prior actions and unfinished edits are retained.', true);
  }
  async function run(kind, request) {
    if (locked()) return false;
    const before = getCurrent(), expected = cursor(), proof = prepareTransition();
    if (!proof || !confirmProject(kind)) return false;
    if (!proof.isCurrent() || !current(expected)) {
      status('The project or unfinished edit changed. Review it and try again.', true);
      return false;
    }
    const operation = active = { kind, cursor: expected, record: null, accepted: false, mutationStarted: false };
    const release = lockSurfaces();
    setTransitionBusy(true);
    $('source-progress').hidden = kind !== 'scan';
    $('cancel-source-import').disabled = false;
    $('source-progress-detail').textContent = 'Preparing scan import…';
    refresh();
    let install;
    try {
      const bundle = await request(operation);
      operation.accepted = true;
      install = prepareWorkspace(bundle);
      if (!proof.isCurrent()) throw new Error('The unfinished edit changed after acceptance. Review it before loading the accepted project.');
      proof.finish();
      install();
      status(kind === 'scan' ? 'Scan added. Create a selection to start fitting.'
        : kind === 'reset' ? 'Empty project. Add a scan or choose an example.' : 'Example loaded.');
      return true;
    } catch (error) {
      const uncertain = operation.accepted || error.code === 'accepted_workspace_unavailable'
        || operation.mutationStarted && (error instanceof TypeError || error.name === 'TimeoutError'
          || ['transport_error', 'observation_superseded', 'worker_failed', 'worker_startup_failed'].includes(error.code));
      if (uncertain) fence(error, before, release);
      else status(error.message, true);
      return false;
    } finally {
      install?.dispose?.();
      active = null;
      setTransitionBusy(!!unavailable);
      $('source-progress').hidden = true;
      if (!unavailable) release();
      refresh();
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
    const bundle = await backend.loadWorkspace(options);
    // The delivered fixed-source worker predates bundled graphs. Its immutable
    // example has no source-transition epoch; preserve that explicit adapter.
    if (!backend.capabilities.sourceImport && !bundle.graph)
      return { ...bundle, graph: graph || await backend.getGraph() };
    return bundle;
  }
  $('new-project').onclick = resetProject;
  $('remove-scan').onclick = resetProject;
  $('add-scan').onclick = () => {
    if (locked() || !backend.capabilities.sourceImport || source()) return;
    $('scan-file').value = '';
    $('scan-file').click();
  };
  $('scan-file').onchange = () => { const file = $('scan-file').files[0]; void addScan(file); };
  $('scan-file').addEventListener('cancel', () => { /* No transaction or draft discard has started. */ });
  $('inspect-source').onclick = inspectSource;
  $('cancel-source-import').onclick = cancel;
  for (const button of document.querySelectorAll('[data-add-scan]')) button.onclick = () => $('add-scan').click();
  for (const button of document.querySelectorAll('[data-choose-example]')) button.onclick = () => $('choose-example').click();
  for (const button of document.querySelectorAll('[data-remove-scan]')) button.onclick = () => $('remove-scan').click();
  $('retry-source-workspace').onclick = async () => {
    if (!unavailable) return;
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
      unavailable = null;
      releaseFence?.(); releaseFence = null;
      setTransitionBusy(false);
      $('source-unavailable').hidden = true;
      status('Current project loaded.');
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
  return { refresh, addScan, resetProject, selectExample, restoreExample, cancel };
}
