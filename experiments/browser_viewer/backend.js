import { BackendError, createHttpBackend } from './http-backend.js';

const operations = ['loadWorkspace', 'getGraph', 'replaceRecipe',
  'importRecipe', 'exportRecipe', 'evaluateGraph', 'ensureGraph', 'graphUpdates',
  'getExampleRecipe', 'listExamples', 'selectExample', 'applyFeatureReuse',
  'faceCandidates', 'previewFaces', 'applyFaces', 'exportCad', 'close'];

// Additional implementations supply the same operations; no transport fallback.
export function createBackend({ type = 'http', factories = {}, ...options } = {}) {
  const available = { http: createHttpBackend, ...factories },
    factory = Object.hasOwn(available, type) ? available[type] : undefined;
  if (typeof factory !== 'function')
    throw new BackendError(`Backend "${type}" is unavailable.`, { code: 'unsupported_backend' });
  const backend = factory(options);
  for (const operation of operations) {
    if (typeof backend?.[operation] !== 'function')
      throw new BackendError(`Backend "${type}" does not implement ${operation}.`,
        { code: 'unsupported_operation' });
  }
  if (!backend.capabilities) throw new BackendError(`Backend "${type}" has no capability declaration.`);
  const selected = Object.fromEntries(operations.map(operation =>
    [operation, backend[operation].bind(backend)]));
  selected.capabilities = backend.capabilities;
  // Lifecycle controls are optional capabilities. HTTP keeps its existing
  // request-abort behavior and native shutdown contract.
  for (const method of ['cancelComputation', 'forceRestart']) {
    selected[method] = typeof backend[method] === 'function' ? backend[method].bind(backend)
      : async () => { throw new BackendError('This backend does not support runtime control.',
        { code: 'unsupported_capability' }); };
  }
  selected.getLifecycle = typeof backend.getLifecycle === 'function' ? backend.getLifecycle.bind(backend)
    : () => { throw new BackendError('This backend does not support runtime control.',
      { code: 'unsupported_capability' }); };
  for (const [method, supported] of [
    ['addScan', backend.capabilities.sourceImport], ['resetProject', backend.capabilities.sourceImport],
    ['getSourceOperation', backend.capabilities.sourceImport || backend.capabilities.sourceActions],
    ['cancelSourceOperation', backend.capabilities.sourceImport || backend.capabilities.sourceActions],
    ['inspectActions', backend.capabilities.sourceActions], ['openActions', backend.capabilities.sourceActions],
  ]) {
    if (supported && typeof backend[method] !== 'function')
      throw new BackendError(`Backend "${type}" does not implement ${method}.`,
        { code: 'unsupported_operation' });
    selected[method] = supported ? backend[method].bind(backend)
      : async () => { throw new BackendError('This backend does not support this source operation.',
        { code: 'unsupported_capability' }); };
  }
  for (const [capability, methods] of Object.entries({
    cad: ['faceCandidates', 'previewFaces', 'applyFaces', 'exportCad'],
    examples: ['listExamples', 'selectExample', 'getExampleRecipe'],
  })) {
    if (backend.capabilities[capability]) continue;
    for (const method of methods) selected[method] = async () => {
      throw new BackendError(`This backend does not support ${capability}.`,
        { code: 'unsupported_capability' });
    };
  }
  return selected;
}

let selectedBackend;
export function getBackend(options = {}) {
  selectedBackend ??= createBackend({
    type: typeof location === 'undefined' ? 'http'
      : new URL(location.href).searchParams.get('backend') || 'http',
    ...options,
  });
  return selectedBackend;
}
