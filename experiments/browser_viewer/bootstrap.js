const byId = id => document.getElementById(id);
let type = new URL(location.href).searchParams.get('backend') || undefined;
let workerBackend, configuration;

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
    byId('startup-recovery-hint').textContent = 'The browser runtime is unavailable. Download the last committed actions if available, then reload and use Load actions. Unsaved draft edits and derived results may be lost.';
  } else if (!configuration || !type) {
    byId('startup-recovery-hint').textContent = 'Startup configuration could not be loaded. Reload after checking the application assets.';
    byId('recover-actions').disabled = true;
  }
}

byId('recover-actions').onclick = async () => {
  const message = byId('startup-detail');
  try {
    const { getBackend } = await import('./backend.js');
    const backend = getBackend({ type }), recipe = await backend.exportRecipe(
      type === 'worker' ? undefined : await backend.getGraph());
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
    message.textContent = 'Actions downloaded. Reload after restoring the runtime and use Load actions.';
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
    const status = byId('status');
    const scope = document.createElement('span');
    scope.textContent = 'Bounded browser experiment · retained nozzle · CAD unavailable';
    status.closest('footer').append(scope);
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
  } else getBackend({ type });
  await import('./app.js');
  byId('load').disabled = false;
  ready();
  if (type === 'worker') byId('status').dataset.runtimeReady = 'true';
}

window.addEventListener('pagehide', () => workerBackend?.close());
start().catch(showFailure);
