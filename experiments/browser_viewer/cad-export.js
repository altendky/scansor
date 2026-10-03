import { managedOwnerId, managedSubtreeIds } from './action-tree.js';

const faceOperations = new Set(['trimmed_face', 'arranged_face']);

export function exportFaceIds(recipe, selected = null) {
  if (selected === null) return recipe.nodes.filter((node) => faceOperations.has(node.operation))
    .map((node) => node.id);
  const included = new Set(selected);
  for (const node of recipe.nodes) {
    if (node.operation !== 'build_faces' || !selected.has(node.id)) continue;
    for (const id of managedSubtreeIds(node.id, recipe.nodes)) included.add(id);
  }
  for (const node of recipe.nodes) {
    if (node.operation === 'build_faces' && included.has(node.id))
      for (const id of node.reused_faces || []) included.add(id);
  }
  return recipe.nodes.filter((node) => included.has(node.id) && faceOperations.has(node.operation))
    .map((node) => node.id);
}

export function exportScopePlan(state, { scope, selected = new Set(), target = null }) {
  const nodes = state.recipe.nodes, byId = new Map(nodes.map((node) => [node.id, node]));
  if (scope === 'body') {
    const node = byId.get(target);
    if (node?.operation !== 'body') throw new Error('Choose a Body to export a solid.');
    return { faceIds: [], roots: [target], payload: { scope, target } };
  }
  if (scope === 'target') {
    const node = byId.get(target);
    if (!node || !['fit', 'joint_fit', 'axis_solve', 'trimmed_face', 'arranged_face'].includes(node.operation))
      throw new Error('Choose a fit or joint to export.');
    return { faceIds: [], roots: [target], payload: { scope, target } };
  }
  if (!['all_faces', 'selected_faces'].includes(scope)) throw new Error('Choose an export scope.');
  const faceIds = exportFaceIds(state.recipe, scope === 'all_faces' ? null : selected);
  if (!faceIds.length) throw new Error(scope === 'all_faces'
    ? 'No built faces. Build faces first, or choose Fit or joint.'
    : 'Select faces or Build faces groups in the feature tree.');
  const roots = new Set(faceIds);
  const reviewOwners = nodes.filter((node) => node.operation === 'build_faces' && selected.has(node.id))
    .map((node) => node.id);
  for (const node of nodes) {
    if (node.operation === 'build_faces' && (scope === 'all_faces' || selected.has(node.id)))
      roots.add(node.id);
  }
  for (const id of faceIds) {
    const visited = new Set();
    let owner = managedOwnerId(byId.get(id), nodes);
    while (owner && !visited.has(owner)) {
      visited.add(owner);
      const node = byId.get(owner);
      if (node?.operation === 'build_faces') roots.add(owner);
      owner = node && managedOwnerId(node, nodes);
    }
  }
  return { faceIds, roots: [...roots], payload: scope === 'all_faces'
    ? { scope } : { scope, targets: faceIds, ...(reviewOwners.length ? { review_owners: reviewOwners } : {}) } };
}

export function exportInputIssues(state, plan) {
  const nodes = new Map(state.recipe.nodes.map((node) => [node.id, node]));
  return plan.roots.flatMap((id) => {
    const node = nodes.get(id), status = state.states[id];
    if (['failed', 'blocked'].includes(status)) return [`${node?.label || id} (${status})`];
    if (node?.operation === 'body' && status === 'ready' && state.results[id]?.valid !== true)
      return [`${node.label || id} (not a validated solid)`];
    if (plan.faceIds.includes(id) && status === 'ready' && state.results[id]?.bounded !== true)
      return [`${node?.label || id} (open)`];
    return [];
  });
}
