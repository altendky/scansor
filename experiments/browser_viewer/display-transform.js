function validMatrix(matrix) {
  return (
    Array.isArray(matrix) &&
    matrix.length === 4 &&
    matrix.every(
      (row) =>
        Array.isArray(row) &&
        row.length === 4 &&
        row.every((value) => Number.isFinite(value)),
    )
  );
}

export function activeDisplayTransform(state, selected = new Set()) {
  if (!state?.recipe) return null;
  const nodes = new Map(state.recipe.nodes.map((node) => [node.id, node]));
  const selectedId = selected.size === 1 ? [...selected][0] : null;
  const selectedNode = nodes.get(selectedId);
  const outputNode = nodes.get(state.recipe.output);
  const node = selectedNode?.operation === 'transform'
    ? selectedNode
    : outputNode?.operation === 'transform'
      ? outputNode
      : null;
  const result = node && state.states[node.id] === 'ready' ? state.results[node.id] : null;
  return result && validMatrix(result.matrix)
    ? { id: node.id, matrix: result.matrix }
    : null;
}
