// Source row IDs remain unchanged. Rectangle selection includes hidden vertices.
export function editSelection(session, region, hits, operation) {
  return {...session, ...editSelectionGroups({lateral_ids: session.lateral_ids, plane_ids: session.plane_ids}, region, hits, operation)};
}

export function editSelectionGroups(groups, region, hits, operation) {
  const next = structuredClone(groups);
  const selected = new Set(operation === 'replace' ? [] : next[region]);
  for (const id of hits) {
    if (operation === 'remove') selected.delete(id);
    else selected.add(id);
  }
  next[region] = [...selected].sort((a, b) => a - b);
  if (operation !== 'remove') {
    for (const other of Object.keys(next)) if (other !== region) next[other] = next[other].filter(id => !selected.has(id));
  }
  return next;
}

export function rectangleHits(positions, project, rect) {
  const hits = [];
  for (let id = 0; id < positions.length / 3; id++) {
    const [x, y, z] = project(positions.subarray(id * 3, id * 3 + 3));
    if (z >= -1 && z <= 1 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) hits.push(id);
  }
  return hits;
}
