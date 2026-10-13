// The backend owns readiness, validation dependencies, and in-flight work.
// Callers name their outputs; they do not decide whether cached work is current.
function checkCurrent(state, token, isCurrent) {
  if (!isCurrent()) throw new Error('The requested operation changed. Try again.');
  if (state.token !== token) throw new Error('Actions changed. Try again with the current actions.');
}
export async function waitForGraphEvaluation({ token, backend, acceptState,
  getState = () => null, acceptEvaluationStatus = () => {}, isCurrent = () => true }) {
  let state = getState(), received = false;
  if (!isCurrent()) throw new Error('The requested operation changed. Try again.');
  for await (const response of backend.graphUpdates({ revision: state?.revision, isCurrent })) {
    received = true;
    if (!isCurrent()) throw new Error('The requested operation changed. Try again.');
    const revision = state?.revision,
      conditional = Number.isSafeInteger(revision) && revision >= 0;
    checkCurrent(response, token, isCurrent);
    if (response.unchanged) {
      if (!conditional || response.revision !== revision || !state.recipe)
        throw new Error('Invalid unchanged graph response. Reload the current actions.');
      // Job completion and errors can change without a geometry publication.
      // Keep them for the caller without rebuilding the accepted graph UI.
      state = { ...state, evaluation_running: response.evaluation_running,
        evaluation_error: response.evaluation_error };
      acceptEvaluationStatus(response);
    } else {
      state = response;
      acceptState(state);
    }
    if (!state.evaluation_running) break;
  }
  if (!received || state.evaluation_running)
    throw new Error('Backend stopped before graph evaluation completed.');
  return state;
}

export async function ensureGraphCurrent({ targets, token, backend, acceptState,
  getState = () => null, acceptEvaluationStatus = () => {}, isCurrent = () => true }) {
  const ids = [...new Set(targets.filter(Boolean))];
  let state;
  do {
    if (!isCurrent()) throw new Error('The requested operation changed. Try again.');
    state = await backend.ensureGraph({ token, targets: ids });
    if (state.recipe) {
      checkCurrent(state, token, isCurrent);
      acceptState(state);
    }
    if (!state.evaluation_running) break;
    await waitForGraphEvaluation({ token, backend, acceptState, getState, acceptEvaluationStatus, isCurrent });
    // A joined job can have different targets. Ask the backend again after it
    // finishes, so that available geometry does not bypass required validation.
  } while (true);
  const byId = new Map(state.recipe.nodes.map((node) => [node.id, node]));
  for (const id of state.required_failures || []) {
    throw new Error(state.errors?.[id] || `Could not evaluate ${byId.get(id)?.label || id}.`);
  }
  if (state.evaluation_error) throw new Error(state.evaluation_error);
  for (const id of ids) {
    if (!byId.has(id)) throw new Error('A requested output was removed. Review the current actions.');
    if (['failed', 'blocked'].includes(state.states[id]))
      throw new Error(state.errors?.[id] || `Could not evaluate ${byId.get(id).label || id}.`);
    if (state.states[id] !== 'ready')
      throw new Error(`${byId.get(id).label || id} is not ready.`);
  }
  return state;
}
