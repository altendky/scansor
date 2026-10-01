import { nodeReferences } from './action-tree.js';

const supportedKinds = new Set(['plane', 'cylinder', 'cone']);
const aggregates = new Set(['joint_fit', 'axis_solve', 'equal_radii', 'plane_relationship']);

export function sameSurfaceReference(first, second) {
  return first?.feature === second?.feature &&
    (first?.surface || null) === (second?.surface || null);
}

export function surfaceReferenceChoices(nodes, results = {}, recipeNodes = nodes) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const explicitlySolvedAxes = new Set(recipeNodes
    .filter((node) => node.operation === 'axis_solve').map((node) => node.axis));
  const options = [];
  for (const node of nodes) {
    if ((node.operation === 'fit' && supportedKinds.has(node.kind)) ||
        node.operation === 'reference_plane') {
      const axis = node.axis || byId.get(node.reference_plane)?.axis;
      if (axis && explicitlySolvedAxes.has(axis)) continue;
      options.push({ reference: { feature: node.id, surface: null },
        label: `${node.label} — direct geometry`,
        kind: node.operation === 'reference_plane' ? 'plane' : node.kind });
    } else if (aggregates.has(node.operation)) {
      const members = new Set(Object.keys(results[node.id]?.surfaces || {}));
      if (!members.size) {
        const visited = new Set();
        const visit = (id) => {
          if (visited.has(id)) return;
          visited.add(id);
          const member = byId.get(id);
          if (!member) return;
          if (member.operation === 'fit') members.add(id);
          else if (['axis', 'point', 'reference_plane'].includes(member.operation)) return;
          else nodeReferences(member).forEach(visit);
        };
        if (node.operation === 'axis_solve') {
          // Match backend membership: listed fits plus mirror-factor surfaces,
          // never geometry used only to initialize a datum or relationship.
          for (const id of node.factors) {
            const factor = byId.get(id);
            if (factor?.operation === 'fit') visit(id);
            else if (factor?.operation === 'mirror_symmetry') factor.surfaces.forEach(visit);
          }
        } else if (['equal_radii', 'plane_relationship'].includes(node.operation)) {
          node.surfaces.forEach(visit);
        } else nodeReferences(node).forEach(visit);
      }
      for (const id of members) {
        const member = byId.get(id);
        if (member?.operation !== 'fit' || !supportedKinds.has(member.kind)) continue;
        options.push({ reference: { feature: node.id, surface: id },
          label: `${node.label} → ${member.label}`, kind: member.kind });
      }
    }
  }
  return options;
}

export function addFittedSurfaceReferences(nodes, choices, selected = []) {
  const references = [...selected], ambiguous = [];
  for (const node of nodes) {
    if (node.operation !== 'fit' || !supportedKinds.has(node.kind)) continue;
    const identifies = (reference) => (reference.surface || reference.feature) === node.id;
    if (references.some(identifies)) continue;
    const available = choices.filter((choice) => identifies(choice.reference)),
      direct = available.find((choice) => !choice.reference.surface);
    if (direct || available.length === 1) references.push((direct || available[0]).reference);
    else if (available.length > 1) ambiguous.push(node.label);
  }
  return { references, ambiguous };
}

export function eligibleIntersections(nodes, reference) {
  return nodes.filter((node) => node.operation === 'surface_intersection' &&
    (sameSurfaceReference(node.first, reference) || sameSurfaceReference(node.second, reference)));
}

export function boundaryKeepOptions(kind) {
  return kind === 'plane'
    ? [{ value: 'inside', label: 'Inside circle' }, { value: 'outside', label: 'Outside circle' }]
    : [{ value: 'positive', label: 'Positive side of cutting plane' },
      { value: 'negative', label: 'Negative side of cutting plane' }];
}

export function geometryAppendOutput(nodes, previousOutput, nextId) {
  return nodes.find((node) => node.id === previousOutput)?.operation === 'transform'
    ? previousOutput : nextId;
}

export function validGeometryPreview(preview) {
  return !!preview && Array.isArray(preview.positions) &&
    preview.positions.length % 3 === 0 && preview.positions.every(Number.isFinite) &&
    Array.isArray(preview.indices) && preview.indices.length % 3 === 0 &&
    preview.indices.every((index) => Number.isInteger(index) && index >= 0 &&
      index < preview.positions.length / 3);
}

// Reopening a batch preserves a previously approved region. New ambiguous
// proposals require a deliberate choice rather than guessing a material side.
export function initialBuildFaceRegion(face) {
  if (face.blocked_by_adjacency || face.status === 'requires_adjacency_review') return null;
  const regions = face.regions.filter(validBuildFaceRegion);
  const previous = regions.find((region) => region.previously_selected) ||
    regions.find((region) => region.owned_face_id);
  if (previous) return previous.key;
  const existing = regions.filter((region) => region.existing_face_id);
  if (existing.length === 1) return existing[0].key;
  return face.status === 'suggested' && regions.some((region) => region.key === face.suggested_region_key)
    ? face.suggested_region_key : null;
}

export function validBuildFaceRegion(region) {
  return validGeometryPreview(region?.preview) && region.preview.positions.length > 0 &&
    region.preview.indices.length > 0;
}

export function buildFaceChoices(proposal, reviewed) {
  return proposal.faces.flatMap((face) => {
    const review = reviewed.get(face.key);
    return !face.blocked_by_adjacency && review?.accepted && face.regions.some((region) =>
      region.key === review.region_key && validBuildFaceRegion(region))
      ? [{ surface: face.surface, region_key: review.region_key }] : [];
  });
}

export function adjacencyPairKey(first, second) {
  return JSON.stringify([first, second].map((reference) =>
    [reference.feature, reference.surface || null]).sort((a, b) =>
    JSON.stringify(a).localeCompare(JSON.stringify(b))));
}

export function buildAdjacencyDecisions(draft, surfaces) {
  const selected = (reference) => surfaces.some((surface) => sameSurfaceReference(surface, reference));
  const decisions = new Map();
  for (const row of draft) {
    if (!['confirmed', 'rejected'].includes(row.state) ||
        !selected(row.first) || !selected(row.second) || sameSurfaceReference(row.first, row.second)) continue;
    decisions.set(adjacencyPairKey(row.first, row.second), {
      first: row.first, second: row.second, state: row.state,
    });
  }
  return [...decisions.values()];
}

export function faceScopeChoices(nodes, surface, ownerId = null) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const dependsOnOwner = (node, seen = new Set()) => {
    if (!ownerId || !node || seen.has(node.id)) return false;
    if (node.id === ownerId) return true;
    seen.add(node.id);
    return [node.managed_by, ...nodeReferences(node)].filter(Boolean)
      .some((id) => dependsOnOwner(byId.get(id), seen));
  };
  return nodes.filter((node) => node.operation === 'trimmed_face' &&
    sameSurfaceReference(node.surface, surface) && !dependsOnOwner(node));
}

export function buildFaceScopes(draft, surfaces, nodes, ownerId = null) {
  return surfaces.flatMap((surface) => {
    const eligible = new Set(faceScopeChoices(nodes, surface, ownerId).map((face) => face.id));
    const faces = [...new Set(draft.filter((scope) => sameSurfaceReference(scope.surface, surface))
      .flatMap((scope) => scope.faces).filter((id) => eligible.has(id)))];
    return faces.length ? [{ surface, faces }] : [];
  });
}

export function faceEvidenceSummary(evidence) {
  if (!evidence) return 'No observation evidence.';
  return `${evidence.interior_count} interior · ${evidence.boundary_count} near boundary · ` +
    `${evidence.elsewhere_count} elsewhere · ${evidence.undefined_count} undefined / ` +
    `${evidence.total_count} observations`;
}
