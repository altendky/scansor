import { exportFaceIds } from './cad-export.js';

export function bodyFaceChoices(state) {
  return state.recipe.nodes.filter((node) => ['trimmed_face', 'arranged_face'].includes(node.operation))
    .map((node) => ({ node, state: state.states[node.id] || 'unevaluated',
      open: state.states[node.id] === 'ready' && state.results[node.id]?.bounded !== true }));
}

export function bodyFaceSelection(state, selected = null) {
  const included = new Set(exportFaceIds(state.recipe, selected));
  return bodyFaceChoices(state).filter(({ node }) => included.has(node.id)).map(({ node }) => node.id);
}

export function bodyInputs(faces, sewingTolerance) {
  if (!faces.length) throw new Error('Choose at least one face.');
  const tolerance = Number(sewingTolerance);
  if (!Number.isFinite(tolerance) || tolerance <= 0)
    throw new Error('Sewing tolerance must be a positive finite distance.');
  return { faces: [...new Set(faces)], sewing_tolerance: tolerance };
}

export function initialBodyTolerance(positions) {
  const lower = [Infinity, Infinity, Infinity], upper = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i + 2 < (positions?.length || 0); i += 3) {
    if (![positions[i], positions[i + 1], positions[i + 2]].every(Number.isFinite)) continue;
    for (let axis = 0; axis < 3; axis++) {
      lower[axis] = Math.min(lower[axis], positions[i + axis]);
      upper[axis] = Math.max(upper[axis], positions[i + axis]);
    }
  }
  const diagonal = Math.hypot(...upper.map((value, axis) => value - lower[axis]));
  return Number.isFinite(diagonal) ? Number(Math.max(1e-7, diagonal * 1e-6).toPrecision(3)) : 1e-7;
}

export function bodyProblems(diagnostic, nodes) {
  const names = new Map(nodes.map((node) => [node.id, node.label]));
  return (diagnostic?.problems || []).map((problem) => ({ ...problem,
    sourceNames: (problem.source_faces || []).map((id) => names.get(id) || id) }));
}

export function bodyEdgePaths(record) {
  return (record?.preview?.edges || []).filter((edge) => Array.isArray(edge.positions) &&
    edge.positions.length >= 6 && edge.positions.length % 3 === 0 && edge.positions.every(Number.isFinite));
}
