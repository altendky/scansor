// A draft fit has declared properties before it has fitted values. The backend
// supplies those declarations; this projection never invents geometric roles.
const bindingFields = ['axis', 'point', 'reference_plane'];

export function projectedFitState(state, node) {
  const previous = state.recipe.nodes.find(candidate => candidate.id === node.id),
    unchangedBinding = previous?.kind === node.kind &&
      bindingFields.every(field => (previous[field] || null) === (node[field] || null)),
    bound = bindingFields.some(field => node[field]), template = state.fit_input_templates?.[node.kind];
  if (!unchangedBinding && bound)
    throw new Error('Remove or repair the saved shared-geometry binding before changing its fit type.');
  if (!unchangedBinding && !template)
    throw new Error('This runtime does not declare relationship properties for the selected fit type.');
  const marker = unchangedBinding ? node.id : template.feature,
    remap = value => {
      if (Array.isArray(value)) return value.map(remap);
      if (!value || typeof value !== 'object') return value;
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
        ['feature', 'context', 'id'].includes(key) && item === marker ? node.id
          : key === 'dependencies' ? item.map(id => id === marker ? node.id : id) : remap(item)]));
    },
    own = output => output.reference?.feature === node.id,
    direct = output => own(output) && (output.reference.context || node.id) === node.id,
    transform = output => {
      const result = remap(output), label = result.feature_label;
      if (label && result.label?.startsWith(label)) result.label = node.label + result.label.slice(label.length);
      result.feature_label = node.label;
      if (result.producing_context?.id === node.id) result.producing_context.label = node.label;
      result.availability = 'unevaluated';
      if (result.compatible !== false) result.reason = 'Evaluate its provider first';
      result.preview = null;
      return result;
    },
    sourceCatalogue = unchangedBinding ? (state.input_catalogue || []).filter(direct) : template.input_catalogue,
    requirements = {};
  for (const requirement of Object.keys(state.input_contracts || {})) {
    const current = state.input_requirements?.[requirement] || {choices: [], unavailable: []},
      declared = unchangedBinding ? {choices: current.choices.filter(direct), unavailable: current.unavailable.filter(direct)}
        : template.input_requirements?.[requirement] || {choices: [], unavailable: []};
    requirements[requirement] = {
      ...current,
      choices: current.choices.filter(output => !own(output)),
      unavailable: [...current.unavailable.filter(output => !own(output)),
        ...[...declared.choices, ...declared.unavailable].map(transform)],
    };
  }
  const nodes = previous ? state.recipe.nodes.map(candidate => candidate.id === node.id ? {...node} : candidate)
    : [...state.recipe.nodes, {...node}];
  return {...state, recipe: {...state.recipe, nodes}, states: {...state.states, [node.id]: 'unevaluated'},
    input_catalogue: [...(state.input_catalogue || []).filter(output => !own(output)), ...sourceCatalogue.map(transform)],
    input_requirements: requirements};
}
