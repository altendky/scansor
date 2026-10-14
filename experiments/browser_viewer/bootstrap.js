const byId = id => document.getElementById(id);
let type = new URL(location.href).searchParams.get('backend') || undefined;
let workerBackend, configuration;

function disableWorkerOperations() {
  const layout = new Set(['workspace-panel', 'workspace-show', 'workspace-reset', 'show-icon-legend']);
  for (const element of document.querySelectorAll('button, input, select, textarea')) {
    if (layout.has(element.id) || element.closest('#icon-legend-dialog')) continue;
    element.disabled = true;
  }
}

function showFailure(error) {
  workerBackend?.close();
  delete byId('status').dataset.runtimeReady;
  byId('status').textContent = 'The viewer could not start or its runtime was lost.';
  byId('startup-error').hidden = false;
  byId('startup-detail').textContent = error.message;
  document.querySelector('main').inert = true;
  for (const element of document.querySelectorAll('#project-toolbar, #creation-toolbar, .workspace-toolbar')) {
    element.inert = true;
    for (const control of element.querySelectorAll('button, input, select, textarea')) control.disabled = true;
  }
  if (type === 'worker') {
    byId('startup-recovery-hint').textContent = 'The browser runtime is unavailable. No workspace actions have been created in this bootstrap slice. Reload after checking the artifact assets.';
    byId('recover-actions').disabled = true;
  } else if (!configuration || !type) {
    byId('startup-recovery-hint').textContent = 'Startup configuration could not be loaded. Reload after checking the application assets.';
    byId('recover-actions').disabled = true;
  }
}

byId('recover-actions').onclick = async () => {
  const message = byId('startup-detail');
  try {
    const { getBackend } = await import('./backend.js');
    const backend = getBackend({ type }), recipe = await backend.exportRecipe(await backend.getGraph());
    const url = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2) + '\n'], { type: 'application/json' }));
    try {
      const link = document.createElement('a');
      link.href = url;
      link.download = 'nozzle-actions-recovery.json';
      link.hidden = true;
      document.body.append(link);
      link.click();
      link.remove();
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
    message.textContent = 'Actions downloaded. After restarting the server, reload and use Load actions.';
  } catch (error) { message.textContent = error.message; }
};

async function start() {
  configuration = (await import('./backend-config.js')).default;
  if (typeof configuration?.type !== 'string' || !configuration.type)
    throw new Error('Invalid backend startup configuration.');
  type ??= configuration.type;
  const { getBackend } = await import('./backend.js');
  const { initializeWorkspace } = await import('./workspace.js');
  const ready = initializeWorkspace();
  if (type === 'worker') {
    disableWorkerOperations();
    const status = byId('status');
    status.closest('footer').replaceChildren(status, 'Bounded browser experiment · workspace integration pending');
    byId('status').textContent = 'Starting browser runtime…';
    const { createWorkerBackend } = await import('./worker-backend.js');
    workerBackend = createWorkerBackend({ ...configuration,
      onFailure: showFailure,
      onPhase: phase => {
        const labels = { manifest: 'Reading runtime manifest',
          'numeric asset verification': 'Verifying runtime files',
          'Python runtime': 'Starting computation engine',
          'NumPy and Pydantic': 'Loading numerical tools',
          'Scansor source and frozen fixture': 'Loading example resources' };
        byId('status').textContent = `${labels[phase] || 'Starting browser runtime'}…`;
      } });
    getBackend({ type, factories: { worker: () => workerBackend } });
    await workerBackend.initialize();
    ready();
    byId('status').textContent = 'Browser runtime ready. Fitting, selection, recipes and CAD are unavailable in this bootstrap slice.';
    byId('status').dataset.runtimeReady = 'true';
    return;
  }
  getBackend({ type });
  await import('./app.js');
  ready();
}

window.addEventListener('pagehide', () => workerBackend?.close());
start().catch(showFailure);
