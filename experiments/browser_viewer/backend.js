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
