// Source row IDs remain unchanged. Rectangle selection includes hidden vertices.
export function editSelection(session, region, hits, operation) {
  return {
    ...session,
    ...editSelectionGroups(
      { lateral_ids: session.lateral_ids, plane_ids: session.plane_ids },
      region,
      hits,
      operation,
    ),
  };
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
    for (const other of Object.keys(next))
      if (other !== region) next[other] = next[other].filter((id) => !selected.has(id));
  }
  return next;
}

export function rectangleHits(positions, project, rect) {
  const hits = [];
  for (let id = 0; id < positions.length / 3; id++) {
    const [x, y, z] = project(positions.subarray(id * 3, id * 3 + 3));
    if (z >= -1 && z <= 1 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom)
      hits.push(id);
  }
  return hits;
}

// Clip occluders before perspective division so triangles crossing the near
// plane still hide rear vertices. Visibility is double-sided, at each vertex's
// exact projected position (not at triangle centers or a front-normal test).
export function selectionProjection(clip, indices, width, height, occlusion = true) {
  const screen = (p) => [
    ((p[0] / p[3] + 1) * width) / 2,
    ((1 - p[1] / p[3]) * height) / 2,
    p[2] / p[3],
  ];
  const planes = [
    (p) => p[3] + p[0],
    (p) => p[3] - p[0],
    (p) => p[3] + p[1],
    (p) => p[3] - p[1],
    (p) => p[3] + p[2],
    (p) => p[3] - p[2],
  ];
  const vertices = Array.from({ length: clip.length / 4 }, (_, i) =>
    Array.from(clip.subarray(i * 4, i * 4 + 4)),
  );
  const points = vertices.map((p) =>
    p[3] > 0 && planes.every((f) => f(p) >= 0) ? screen(p) : null,
  );
  const tile = 32,
    columns = Math.ceil(width / tile),
    rows = Math.ceil(height / tile),
    grid = new Map();
  for (let i = 0; occlusion && i < indices.length; i += 3) {
    let polygon = [vertices[indices[i]], vertices[indices[i + 1]], vertices[indices[i + 2]]];
    for (const distance of planes) {
      const next = [];
      for (let j = 0; j < polygon.length; j++) {
        const a = polygon[j],
          b = polygon[(j + 1) % polygon.length],
          da = distance(a),
          db = distance(b);
        if (da >= 0) next.push(a);
        if (da < 0 !== db < 0) {
          const t = da / (da - db);
          next.push(a.map((x, k) => x + t * (b[k] - x)));
        }
      }
      polygon = next;
      if (!polygon.length) break;
    }
    for (let j = 1; j + 1 < polygon.length; j++) {
      if ([polygon[0], polygon[j], polygon[j + 1]].some((p) => p[3] <= 0)) continue;
      const triangle = [screen(polygon[0]), screen(polygon[j]), screen(polygon[j + 1])];
      const xs = triangle.map((p) => p[0]),
        ys = triangle.map((p) => p[1]);
      const left = Math.max(0, Math.floor(Math.min(...xs) / tile)),
        right = Math.min(columns - 1, Math.floor(Math.max(...xs) / tile));
      const top = Math.max(0, Math.floor(Math.min(...ys) / tile)),
        bottom = Math.min(rows - 1, Math.floor(Math.max(...ys) / tile));
      for (let y = top; y <= bottom; y++)
        for (let x = left; x <= right; x++) {
          const key = y * columns + x;
          if (!grid.has(key)) grid.set(key, []);
          grid.get(key).push(triangle);
        }
    }
  }
  const cache = new Map();
  function visible(id) {
    if (cache.has(id)) return cache.get(id);
    const p = points[id];
    if (!p) return false;
    const key =
      Math.min(rows - 1, Math.floor(p[1] / tile)) * columns +
      Math.min(columns - 1, Math.floor(p[0] / tile));
    for (const [a, b, c] of grid.get(key) || []) {
      const denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
      if (Math.abs(denominator) < 1e-12) continue;
      const u = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / denominator;
      const v = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / denominator;
      if (
        u >= -1e-8 &&
        v >= -1e-8 &&
        u + v <= 1 + 1e-8 &&
        u * a[2] + v * b[2] + (1 - u - v) * c[2] < p[2] - 1e-9
      ) {
        cache.set(id, false);
        return false;
      }
    }
    cache.set(id, true);
    return true;
  }
  return { points, visible };
}

// Swept disk: a fast pointer move paints the entire intervening segment.
export function brushHits(projection, start, end, radius, depth) {
  const dx = end[0] - start[0],
    dy = end[1] - start[1],
    length2 = dx * dx + dy * dy;
  const hits = [];
  projection.points.forEach((p, id) => {
    if (!p) return;
    const t = length2
      ? Math.max(0, Math.min(1, ((p[0] - start[0]) * dx + (p[1] - start[1]) * dy) / length2))
      : 0;
    if (
      (p[0] - start[0] - t * dx) ** 2 + (p[1] - start[1] - t * dy) ** 2 <= radius ** 2 &&
      (depth === 'through_all' || projection.visible(id))
    )
      hits.push(id);
  });
  return hits;
}

// Resolve only the selected feature's observations, not growth barriers or seeds
// discarded by a derived operation. Memberships may include live stroke previews.
export function featureVertexIds(nodes, memberships, featureId) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const seen = new Set(),
    vertices = new Set();
  function visit(id) {
    if (seen.has(id)) return;
    seen.add(id);
    const node = byId.get(id);
    if (!node) return;
    if (
      node.operation === 'selection' ||
      node.operation === 'growth' ||
      node.operation === 'region_selection' ||
      node.operation === 'reuse_selection'
    ) {
      for (const vertex of memberships[id] || []) vertices.add(vertex);
      return;
    }
    const inputs =
      node.operation === 'fit'
        ? node.selections
        : node.operation === 'axis'
          ? node.source_fit
            ? [node.source_fit]
            : []
          : node.operation === 'axis_solve'
            ? node.factors
          : node.operation === 'selection_region'
            ? [node.selection]
          : node.operation === 'feature_reuse'
            ? [node.reference_selection, ...node.target_selections, ...node.fits]
          : node.operation === 'reference_plane'
            ? [node.axis]
        : node.operation === 'joint_fit'
          ? node.constraints
          : node.operation === 'coaxial'
            ? [node.surface, node.reference]
            : node.operation === 'perpendicular'
              ? [node.lateral, node.plane]
              : node.operation === 'rotational_symmetry'
                ? [node.axis, ...node.planes]
                : node.operation === 'mirror_symmetry'
                  ? node.surfaces
                  : node.operation === 'parallel'
                    ? [node.surface]
                    : node.operation === 'equal'
                      ? [node.left.surface, node.right.surface]
                : [];
    inputs.forEach(visit);
  }
  visit(featureId);
  return [...vertices].sort((a, b) => a - b);
}

// Symmetry references existing fits. Never manufacture a different fit for a selection.
export function rotationalFitInputs(nodes, ids) {
  const fits = ids.map((id) => nodes.find((node) => node.id === id));
  if (ids.length !== 3 || new Set(ids).size !== 3 || fits.some((fit) => fit?.operation !== 'fit'))
    throw new Error('Choose three distinct existing fits. Create fits for selections first.');
  if (new Set(fits.map((fit) => fit.kind)).size !== 1)
    throw new Error(
      'Rotational symmetry requires three fits of the same type. Fit types are not changed.',
    );
  if (!['cone', 'cylinder', 'plane'].includes(fits[0].kind))
    throw new Error('Rotational symmetry currently supports cone, cylinder, or plane fits.');
  return [...ids];
}

export function mirrorFitInputs(nodes, ids) {
  const fits = ids.map((id) => nodes.find((node) => node.id === id));
  if (ids.length !== 2 || new Set(ids).size !== 2 || fits.some((fit) => fit?.operation !== 'fit'))
    throw new Error('Choose two distinct existing fits. Create fits for selections first.');
  if (fits.some((fit) => fit.axis || fit.reference_plane))
    throw new Error('Mirror symmetry requires standalone fits; datum-bound fits are already constrained.');
  if (new Set(fits.map((fit) => fit.kind)).size !== 1)
    throw new Error('Mirror symmetry requires two fits of the same type. Fit types are not changed.');
  if (!['cone', 'cylinder', 'plane'].includes(fits[0].kind))
    throw new Error('Mirror symmetry currently supports cone, cylinder, or plane fits.');
  return [...ids];
}
