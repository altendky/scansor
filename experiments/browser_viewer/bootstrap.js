const byId = id => document.getElementById(id);
let type = new URL(location.href).searchParams.get('backend') || undefined;
let runtimeBackend, configuration, readyWorkspace, runtimeControlIntent = 0;
const disabledBeforeFailure = new Map();
function lockViewer() {
  window.dispatchEvent(new Event('scansor-runtime-lock'));
  delete byId('status').dataset.runtimeReady;
  document.querySelector('main').inert = true;
  for (const element of document.querySelectorAll('#project-toolbar, #creation-toolbar, .workspace-toolbar')) {
    element.inert = true;
    for (const control of element.querySelectorAll('button, input, select, textarea')) {
      if (!disabledBeforeFailure.has(control)) disabledBeforeFailure.set(control, control.disabled);
      control.disabled = true;
    }
  }
}
function checkRuntime() {
  const state = runtimeBackend?.getLifecycle();
  if (type === 'worker' && state && ['failed', 'closed'].includes(state.state))
    throw new Error(state.message || 'Browser runtime unavailable.');
}

function showFailure(error) {
  if (error.code === 'computation_cancelled') return;
  lockViewer();
  byId('status').textContent = 'The viewer could not start or its runtime was lost.';
  byId('startup-error').hidden = false;
  byId('startup-detail').textContent = error.message;
  if (type === 'worker') {
    byId('startup-recovery-hint').textContent = 'Use Force and restart to restore the immutable example and last committed actions. Derived results are discarded; unfinished edits may be lost. You can also download actions below. Reloading the page loses this in-memory recovery copy.';
  } else if (!configuration || !type) {
    byId('startup-recovery-hint').textContent = 'Startup configuration could not be loaded. Reload after checking the application assets.';
    byId('recover-actions').disabled = true;
  }
}

async function restoreViewer(state, intent = runtimeControlIntent) {
  const lifetime = runtimeBackend.getLifecycle().lifetime;
  const isCurrent = () => intent === runtimeControlIntent && runtimeBackend.getLifecycle().lifetime === lifetime;
  const app = await import('./app.js');
  if (!isCurrent()) return;
  const graphSafe = await app.prepareRuntimeRestore({ recoveredState: state, isCurrent, restoreControls() {
    document.querySelector('main').inert = false;
    for (const element of document.querySelectorAll('#project-toolbar, #creation-toolbar, .workspace-toolbar'))
      element.inert = false;
    for (const [control, disabled] of disabledBeforeFailure) control.disabled = disabled;
    disabledBeforeFailure.clear();
  } });
  if (!isCurrent()) return;
  checkRuntime();
  byId('startup-error').hidden = true;
  if (!graphSafe) return;
  await app.initializeViewer({ recoveredState: state, isCurrent });
  if (!isCurrent()) return;
  checkRuntime();
  byId('startup-error').hidden = true;
  byId('load').disabled = false;
  readyWorkspace?.();
  byId('status').dataset.runtimeReady = 'true';
}

function stopAuto() {
  byId('auto-evaluate').checked = false;
  window.dispatchEvent(new Event('scansor-computation-stop'));
}
byId('cancel-computation').onclick = async () => {
  const intent = ++runtimeControlIntent;
  stopAuto();
  try {
    const state = await runtimeBackend.cancelComputation();
    if (state) await restoreViewer(state, intent);
  } catch (error) {
    if (error.code === 'computation_cancelled' || intent !== runtimeControlIntent) return;
    if (type === 'worker') showFailure(error);
    else {
      byId('status').textContent = error.message;
      byId('runtime-detail').textContent = 'Cancellation could not be confirmed. Retry Cancel or load the current project; native work may still be running.';
    }
  }
};
byId('force-restart').onclick = async () => {
  if (type !== 'worker') return;
  const intent = ++runtimeControlIntent;
  stopAuto();
  try { await restoreViewer(await runtimeBackend.forceRestart(), intent); }
  catch (error) { if (error.code !== 'computation_cancelled' && intent === runtimeControlIntent) showFailure(error); }
};

function showLifecycle(state) {
  if (state.state === 'recovering') lockViewer();
  byId('runtime-controls').hidden = false;
  byId('cancel-computation').disabled = !state.canCancel || !(['queued', 'running', 'loading'].includes(state.state) || state.retryCancel);
  byId('force-restart').hidden = type !== 'worker';
  byId('force-restart').disabled = state.state === 'closed';
  const messages = {
    startup: 'Starting runtime. Force restarts from the beginning.',
    loading: 'Loading resources…', queued: 'Operation queued.',
    running: state.interruptible && state.interrupts ? 'Computing. Numerical interruption is available.'
      : 'Computing. Cancel waits for a safe boundary; CAD calls can take time.',
    complete: state.recovered ? 'Committed actions restored. Auto is off; evaluate when ready.' : 'Runtime ready.',
    'cancel-requested': type === 'worker'
      ? 'Cancel requested. Waiting for a safe boundary; Force and restart stops the worker.'
      : 'Cancel requested. Waiting for native work and its numerical tasks to retire. Native calls cannot be forcibly stopped here.',
    cancelled: 'Cancelled. Derived results discarded. Auto is off; evaluate when ready.',
    recovering: 'Restarting runtime and restoring committed actions. This can take about 13–16 seconds on a warm local connection; first downloads take longer.',
    failed: type === 'worker' ? 'Runtime unavailable. Force and restart retries; recovery download remains available.'
      : 'Evaluation failed. Inspect the marked actions; the native server remains available.',
    closed: 'Runtime closed.',
  };
  const context = state.roots?.length ? `${state.operation === 'ensure' ? 'Preparing' : 'Evaluating'} ${state.roots.length} requested action${state.roots.length === 1 ? '' : 's'}. ` : '';
  byId('runtime-detail').textContent = context + (state.message || messages[state.state] || state.state);
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
  readyWorkspace = ready;
  if (type === 'worker') {
    const status = byId('status');
    const scope = document.createElement('span');
    scope.textContent = configuration.cad ? 'Bounded browser experiment · retained nozzle · CAD loads on demand'
      : 'Bounded browser experiment · retained nozzle · CAD unavailable';
    status.closest('footer').append(scope);
    byId('status').textContent = 'Starting browser runtime…';
    const { createWorkerBackend } = await import('./worker-backend.js');
    runtimeBackend = createWorkerBackend({ ...configuration,
      onFailure: showFailure,
      onLifecycle: showLifecycle,
      onPhase: phase => {
        const labels = { manifest: 'Reading runtime manifest',
          'numeric asset verification': 'Verifying runtime files',
          'Python runtime': 'Starting computation engine',
          'NumPy and Pydantic': 'Loading numerical tools',
          'Scansor source and frozen fixture': 'Loading example resources',
          'optional CAD asset verification and extraction': 'Verifying and unpacking CAD tools',
          'CAD kernel': 'Starting CAD tools' };
        byId('status').textContent = `${labels[phase] || 'Starting browser runtime'}…`;
      } });
    getBackend({ type, factories: { worker: () => runtimeBackend } });
    await runtimeBackend.initialize();
  } else {
    runtimeBackend = getBackend({ type, onLifecycle: showLifecycle });
    showLifecycle(runtimeBackend.getLifecycle());
  }
  const app = await import('./app.js');
  await app.initializeViewer();
  checkRuntime();
  byId('load').disabled = false;
  ready();
  if (type === 'worker') byId('status').dataset.runtimeReady = 'true';
}

window.addEventListener('pagehide', () => { if (type === 'worker') runtimeBackend?.close(); });
start().catch(showFailure);
