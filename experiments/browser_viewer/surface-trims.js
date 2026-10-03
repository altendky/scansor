import { nodeReferences } from './action-tree.js';

const supportedKinds = new Set(['plane', 'cylinder', 'cone']);
const aggregates = new Set(['joint_fit', 'axis_solve', 'equal_radii', 'plane_relationship']);

export function surfaceReferencePresentation(reference, nodes) {
  const contextNode = nodes.find((node) => node.id === reference.feature),
    surface = reference.surface
      ? nodes.find((node) => node.id === reference.surface) : contextNode,
    name = surface?.label || reference.surface || reference.feature || 'Unknown surface',
    icon = surface?.operation === 'reference_plane' ? 'reference_plane' : surface?.kind || 'plane',
    type = surface?.operation === 'reference_plane' ? 'Reference plane'
      : surface?.operation === 'fit' && surface.kind
        ? `${surface.kind[0].toUpperCase()}${surface.kind.slice(1)} fit` : 'Surface',
    context = reference.surface ? contextNode?.label || reference.feature : null,
    detail = `${type}${context ? ` · ${context}` : ''}`;
  return { name, icon, type, context, detail, label: `${name} — ${detail}` };
}

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
      const reference = { feature: node.id, surface: null };
      options.push({ reference,
        label: surfaceReferencePresentation(reference, nodes).label,
        kind: node.operation === 'reference_plane' ? 'plane' : node.kind });
    } else if (aggregates.has(node.operation)) {
      // Numerical component results may include extra geometry. They can limit
      // availability, but never enlarge a context's declared member contract.
      const evaluated = new Set(Object.keys(results[node.id]?.surfaces || {}));
      const members = new Set(), visited = new Set();
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
      for (const id of members) {
        if (evaluated.size && !evaluated.has(id)) continue;
        const member = byId.get(id);
        if (member?.operation !== 'fit' || !supportedKinds.has(member.kind)) continue;
        const reference = { feature: node.id, surface: id };
        options.push({ reference,
          label: surfaceReferencePresentation(reference, nodes).label, kind: member.kind });
      }
    }
  }
  return options;
}

export function fittedSurfacePresentation(reference, nodes) {
  return surfaceReferencePresentation({ feature: reference.surface || reference.feature }, nodes);
}

export function completeFitSurfaceChoices(nodes, results = {}, retained = [], states = null) {
  // Provider identity comes from declarations, not whichever partial outputs
  // happen to be available. An unfinished joint must not expose its seed fit.
  const options = surfaceReferenceChoices(nodes), choices = [], unavailable = [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  for (const node of nodes) {
    if (node.operation !== 'fit' || !supportedKinds.has(node.kind)) continue;
    if (states && states[node.id] !== 'ready') {
      unavailable.push({ label: node.label, reason: 'Evaluate its fit first' });
      continue;
    }
    const available = options.filter((choice) =>
      (choice.reference.surface || choice.reference.feature) === node.id);
    // Saved face actions retain their exact geometry dependencies. Hiding
    // context choices must not silently retarget their approved boundaries.
    const saved = available.filter((choice) => retained.some((reference) =>
      sameSurfaceReference(reference, choice.reference)));
    const joints = available.filter((choice) =>
      ['axis_solve', 'joint_fit'].includes(byId.get(choice.reference.feature)?.operation));
    let choice;
    if (saved.length === 1) choice = saved[0];
    else if (saved.length > 1 || joints.length > 1) {
      unavailable.push({ label: node.label, reason: 'Multiple solve outputs' });
      continue;
    } else if (joints.length === 1) {
      choice = joints[0];
      if (['equal_radii', 'plane_relationship'].includes(results[node.id]?.resolved_by)) {
        unavailable.push({ label: node.label, reason: 'Conflicting solve outputs' });
        continue;
      }
    } else choice = available.find((option) => !option.reference.surface);
    if (!choice) {
      unavailable.push({ label: node.label, reason: 'No complete solve output' });
      continue;
    }
    if (choice.reference.surface && (!results[choice.reference.feature]?.surfaces?.[node.id] ||
        (states && states[choice.reference.feature] !== 'ready'))) {
      unavailable.push({ label: node.label, reason: 'Evaluate its solve first' });
      continue;
    }
    choices.push({ ...choice, ...fittedSurfacePresentation(choice.reference, nodes) });
  }
  return { choices, unavailable };
}

export function unavailableRetainedFitReferences(retained, choices) {
  return retained.filter((reference) => !choices.some((choice) =>
    sameSurfaceReference(reference, choice.reference)));
}

export function surfaceFootprintState(target, preview, surfaceActive, neighborInspected) {
  return { reference: preview || target, prominent: !!preview || (surfaceActive && !neighborInspected) };
}

export function renderFaceDialogClose(button, applied) {
  button.textContent = applied ? 'Done' : 'Cancel';
  button.title = applied ? 'Close; applied faces are saved' : 'Discard unapplied changes; applied faces are saved';
}

export function bindFaceContinuationPreview(button, reference, preview) {
  button.onpointerenter = () => { if (!button.disabled) preview(reference, true, 'pointer'); };
  button.onfocus = () => { if (!button.disabled) preview(reference, true, 'focus'); };
  button.onpointerleave = () => preview(reference, false, 'pointer');
  button.onblur = () => preview(reference, false, 'focus');
}

export function faceContinuationPreview(state, reference, active, interaction) {
  const next = { ...state };
  if (active) next[interaction] = reference;
  else if (sameSurfaceReference(next[interaction], reference)) next[interaction] = null;
  return next;
}

export function previewedFaceContextIds(proposal, target) {
  return guidedProposalFaces(proposal, target).filter((face) =>
    !face.blocked_by_adjacency && !face.blocked_by_geometry).flatMap((face) =>
    boundedBuildFaceRegions(face).filter(validBuildFaceRegion).flatMap((region) =>
      [region.existing_face_id, region.owned_face_id].filter(Boolean)));
}

const physicalFace = (node) => ['trimmed_face', 'arranged_face'].includes(node.operation);

export function unbuiltFaceNeighbors(candidates, nodes) {
  // Continue is a next-authoring shortcut, not a proof of complete surface
  // coverage. Existing reviewed faces remain editable through the picker.
  const reviewed = nodes.filter(physicalFace).map((node) => node.surface);
  return candidates.filter((candidate) => !reviewed.some((reference) =>
    sameSurfaceReference(reference, candidate.reference)));
}

// Bridge accepted display geometry only until its exact new output is evaluated.
export function appliedFaceContext(state, ownerId, proposal, choices) {
  return proposal.faces.flatMap((face) => {
    const regions = face.regions.filter((region) => choices.some((choice) =>
      sameSurfaceReference(choice.surface, face.surface) && choice.region_key === region.key));
    return regions.flatMap((region) => {
      const candidates = state.recipe.nodes.filter((node) => physicalFace(node) &&
        sameSurfaceReference(node.surface, face.surface) &&
        (node.id === region.existing_face_id || node.id === region.owned_face_id ||
          (node.managed_by === ownerId && (node.managed_key?.endsWith('/' + region.key) ||
            (!region.arrangement && regions.length === 1 && node.operation === 'trimmed_face')))));
      return candidates.length === 1 ? [{ id: candidates[0].id, token: state.token,
        signature: JSON.stringify(candidates[0]), result: region }] : [];
    });
  });
}

export function definedFaceContext(state, bridges = [], excludedIds = []) {
  const excluded = new Set(excludedIds);
  return state.recipe.nodes.flatMap((node) => {
    if (!physicalFace(node) || excluded.has(node.id)) return [];
    const bridge = bridges.find((saved) => saved.id === node.id && saved.token === state.token &&
      saved.signature === JSON.stringify(node)),
      result = state.states[node.id] === 'ready' ? state.results[node.id]
        : ['unevaluated', 'stale', 'running'].includes(state.states[node.id]) ? bridge?.result : null;
    return validBuildFaceRegion(result) ? [{ id: node.id, result }] : [];
  });
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

export function boundaryKeepOptions(kind, intersection) {
  const curve = intersection?.curves?.[0] || intersection;
  return kind === 'plane' && curve?.kind !== 'line'
    ? [{ value: 'inside', label: 'Inside closed boundary' },
      { value: 'outside', label: 'Outside closed boundary' }]
    : [{ value: 'positive', label: 'Positive side of cutting plane' },
      { value: 'negative', label: 'Negative side of cutting plane' }];
}

export function intersectionCurves(intersection) {
  if (Array.isArray(intersection?.curves)) return intersection.curves;
  return intersection ? [intersection] : [];
}

// Localize an infinite mathematical line for display, not a physical edge trim.
function observationLinePreview(curve, coverage) {
  const origin = curve.origin_display, direction = curve.direction_display,
    ids = coverage?.ids, positions = coverage?.positions;
  if (!Array.isArray(origin) || origin.length !== 3 || !origin.every(Number.isFinite) ||
      !Array.isArray(direction) || direction.length !== 3 || !direction.every(Number.isFinite) ||
      !Array.isArray(ids) || !ids.length ||
      (!Array.isArray(positions) && !ArrayBuffer.isView(positions)) || positions.length % 3) return null;
  const length = Math.hypot(...direction);
  if (!(length > 0) || !Number.isFinite(length)) return null;
  const axis = direction.map((value) => value / length);
  let first = null, minimum = Infinity, maximum = -Infinity;
  for (const id of ids) {
    if (!Number.isInteger(id) || id < 0 || id >= positions.length / 3) return null;
    const point = [positions[id * 3], positions[id * 3 + 1], positions[id * 3 + 2]];
    if (!point.every(Number.isFinite)) return null;
    first ||= point;
    const t = point.reduce((sum, value, index) => sum + (value - first[index]) * axis[index], 0);
    minimum = Math.min(minimum, t);
    maximum = Math.max(maximum, t);
  }
  if (!Number.isFinite(maximum - minimum) || !(maximum > minimum)) return null;
  const firstT = first.reduce((sum, value, index) => sum + (value - origin[index]) * axis[index], 0),
    anchor = origin.map((value, index) => value + firstT * axis[index]),
    projected = [minimum, maximum].flatMap((t) => anchor.map((value, index) => value + t * axis[index]));
  return projected.every(Number.isFinite) ? { positions: projected, indices: [] } : null;
}

export function intersectionPreviewPaths(intersection, coverage = null) {
  return intersectionCurves(intersection).filter((curve) =>
    validGeometryPreview(curve.preview) && curve.preview.positions.length >= 6)
    .map((curve) => ({ preview: curve.kind === 'line' && !curve.closed && coverage
      ? observationLinePreview(curve, coverage) || curve.preview : curve.preview,
      closed: curve.closed ?? ['circle', 'ellipse'].includes(curve.kind) }));
}

export function faceBoundaryPreviewPaths(bounds, loops) {
  return (loops || bounds?.loops || []).flatMap((loop) => {
    if (Array.isArray(loop.edges)) return loop.edges.filter((edge) => !edge.artificial && !edge.seam)
      .map((edge) => ({ preview: edge.preview, closed: !!edge.closed }));
    const preview = loop.kind === 'polygon'
      ? { positions: (loop.positions || []).flat(), indices: [] } : loop.preview;
    return [{ preview, closed: loop.closed ?? true }];
  }).filter((path) => validGeometryPreview(path.preview) && path.preview.positions.length >= 6);
}

export function physicalBoundsSummary(bounds = {}) {
  return Object.entries(bounds || {}).map(([kind, value]) => {
    if (kind === 'arrangement') return ['Boundary construction', 'Reviewed native arrangement cell'];
    if (['axial', 'radial'].includes(kind) && Array.isArray(value) && value.length === 2 &&
        value.every((endpoint) => endpoint === null || Number.isFinite(endpoint))) {
      return [`${kind === 'axial' ? 'Axial' : 'Radial'} bounds`, value.map((endpoint) =>
        endpoint === null ? 'unbounded' : endpoint.toFixed(5)).join(' → ')];
    }
    const label = kind === 'loops' ? 'Boundary loops' : kind === 'cuts' ? 'Cutting planes' : kind;
    return [label, Array.isArray(value) ? value.length : JSON.stringify(value)];
  });
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
export function suggestedBuildFaceRegions(face) {
  if (face.blocked_by_adjacency || face.blocked_by_geometry || face.status !== 'suggested') return [];
  const suggested = new Set(face.suggested_region_keys ||
    (face.suggested_region_key ? [face.suggested_region_key] : []));
  return boundedBuildFaceRegions(face).filter((region) => suggested.has(region.key) && validBuildFaceRegion(region))
    .map((region) => region.key);
}

export function initialBuildFaceRegions(face) {
  if (face.blocked_by_adjacency || face.blocked_by_geometry || face.status === 'requires_adjacency_review') return [];
  const regions = boundedBuildFaceRegions(face).filter(validBuildFaceRegion);
  const previous = regions.filter((region) => region.previously_selected || region.owned_face_id);
  if (previous.length) return previous.map((region) => region.key);
  const existing = regions.filter((region) => region.existing_face_id);
  if (existing.length === 1) return [existing[0].key];
  return suggestedBuildFaceRegions(face);
}

// Guided reviews require a deliberate retained-cell choice. Reopening may
// restore approval, but observation-populated suggestions are never approvals.
export function initialGuidedFaceRegions(face) {
  if (face.blocked_by_adjacency || face.blocked_by_geometry) return [];
  return boundedBuildFaceRegions(face).filter((region) => validBuildFaceRegion(region) &&
    (region.previously_selected || region.owned_face_id)).map((region) => region.key);
}

// Bulk suggestions remain a review edit, never an implicit approval or Apply.
export function changeBuildFaceRegionSelection(face, selectedKeys, operation, regionKey = null) {
  const previous = new Set(selectedKeys), available = new Set(boundedBuildFaceRegions(face)
    .filter(validBuildFaceRegion).map((region) => region.key)),
    selected = new Set(selectedKeys.filter((key) => available.has(key)));
  if (operation === 'clear') selected.clear();
  else if (!face.blocked_by_adjacency && !face.blocked_by_geometry) {
    if (operation === 'suggested') {
      for (const key of suggestedBuildFaceRegions(face)) selected.add(key);
    } else if (operation === 'toggle' && boundedBuildFaceRegions(face).some((region) =>
      region.key === regionKey && validBuildFaceRegion(region))) {
      if (selected.has(regionKey)) selected.delete(regionKey); else selected.add(regionKey);
    }
  }
  return { region_keys: [...selected], changed: selected.size !== previous.size ||
    [...selected].some((key) => !previous.has(key)) };
}

export function compactFaceRegionList(face) {
  return boundedBuildFaceRegions(face).length > 8;
}

export function guidedCandidateSelected(candidate, previous = null, exact = false) {
  if (!candidate.supported ||
      candidate.mathematical?.status === 'proven_empty') return false;
  if (exact && previous !== null) return previous.some((reference) =>
    sameSurfaceReference(reference, candidate.reference));
  if (candidate.conflict) return false;
  if (candidate.state === 'rejected' ||
      candidate.known_adjacencies?.some((decision) => decision.state === 'rejected')) return false;
  return previous !== null
    ? previous.some((reference) => sameSurfaceReference(reference, candidate.reference))
    : candidate.known_adjacencies?.some((decision) => decision.state === 'confirmed') || false;
}

export function faceCandidatePresentation(candidate, nodes) {
  return {
    ...surfaceReferencePresentation(candidate.reference, nodes),
    status: candidate.conflict ? 'Conflict' : !candidate.supported ? 'Unavailable'
      : candidate.state === 'confirmed' ? 'Approved' : candidate.state === 'rejected' ? 'Rejected' : '',
  };
}

export function guidedNeighborHighlights(candidates, cutters, inspected = null) {
  return candidates.filter((candidate) => cutters.some((ref) =>
    sameSurfaceReference(ref, candidate.reference)) ||
    sameSurfaceReference(inspected?.reference, candidate.reference));
}

export function guidedRejectedDecisions(draft, target, cutters) {
  return draft.filter((choice) => choice.state === 'rejected' &&
    (sameSurfaceReference(choice.first, target) || sameSurfaceReference(choice.second, target)) &&
    !cutters.some((ref) => sameSurfaceReference(ref,
      sameSurfaceReference(choice.first, target) ? choice.second : choice.first)));
}

export function unavailableGuidedSources(selected, candidates, cutters) {
  const available = new Set(candidates.filter((candidate) => cutters.some((ref) =>
    sameSurfaceReference(ref, candidate.reference))).flatMap((candidate) =>
    (candidate.shared_faces || []).filter(guidedSourceAvailable).map((source) => source.id)));
  return selected.filter((id) => !available.has(id));
}

export function guidedSurfaceInputs(target, candidates) {
  return target ? [target, ...candidates.filter((reference, index) =>
    !sameSurfaceReference(reference, target) && !candidates.slice(0, index)
      .some((previous) => sameSurfaceReference(previous, reference)))] : [];
}

export function guidedProposalFaces(proposal, target) {
  return (proposal?.faces || []).filter((face) => sameSurfaceReference(face.surface, target));
}

export function approvedNeighborPreviewPaths(candidate, results, targetKey) {
  return (candidate.shared_faces || []).flatMap((face) => {
    if (Array.isArray(face.preview_paths)) return face.preview_paths.map((path) => ({
      preview: path.preview || { positions: path.positions, indices: [] }, closed: !!path.closed,
    })).filter((path) => validGeometryPreview(path.preview) && path.preview.positions.length >= 6);
    const loops = results[face.id]?.loops || results[face.id]?.bounds?.loops || [];
    return loops.flatMap((loop) => (loop.edges || []).filter((edge) =>
      !edge.artificial && !edge.seam && targetKey && edge.sources?.includes(targetKey))
      .map((edge) => ({ preview: edge.preview, closed: !!edge.closed })))
      .filter((path) => validGeometryPreview(path.preview) && path.preview.positions.length >= 6);
  });
}

export function guidedSourceAvailable(source) {
  return !source.diagnostic && Array.isArray(source.preview_paths) && source.preview_paths.some((path) =>
    validGeometryPreview(path.preview || { positions: path.positions, indices: [] }) &&
    (path.preview?.positions || path.positions).length >= 6);
}

export function initialBuildFaceRegion(face) {
  return initialBuildFaceRegions(face)[0] || null;
}

export function validBuildFaceRegion(region) {
  return validGeometryPreview(region?.preview) && region.preview.positions.length > 0 &&
    region.preview.indices.length > 0;
}

// Open cells remain backend evidence, not face-authoring choices or pick targets.
export function boundedBuildFaceRegions(face) {
  return face.regions.filter((region) => region.bounded === true);
}

export function buildFaceChoices(proposal, reviewed) {
  return proposal.faces.flatMap((face) => {
    const review = reviewed.get(face.key);
    const selected = new Set(review?.region_keys ||
      (review?.accepted && review.region_key ? [review.region_key] : []));
    return face.blocked_by_adjacency || face.blocked_by_geometry ? [] : boundedBuildFaceRegions(face).filter((region) =>
      selected.has(region.key) && validBuildFaceRegion(region))
      .map((region) => ({ surface: face.surface, region_key: region.key }));
  });
}

export function buildFaceRegionGroups(face, selectedKeys = []) {
  const selected = new Set(selectedKeys), suggested = new Set(face.suggested_region_keys ||
    (face.suggested_region_key ? [face.suggested_region_key] : []));
  const primary = [], other = [];
  for (const region of boundedBuildFaceRegions(face)) {
    const visible = selected.has(region.key) || suggested.has(region.key) ||
      region.previously_selected || region.owned_face_id || region.existing_face_id ||
      region.evidence?.interior_count > 0 || region.evidence?.boundary_count > 0;
    (visible ? primary : other).push(region);
  }
  return { primary, other };
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
  return nodes.filter((node) => ['trimmed_face', 'arranged_face'].includes(node.operation) &&
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

export function faceContinuationInputs(state, target, appliedOwnerId) {
  const nodes = state.recipe.nodes;
  const applied = nodes.find((node) => node.id === appliedOwnerId);
  const next = nodes.find((node) => node.operation === 'build_faces' &&
    sameSurfaceReference(node.target, target));
  // Include the physical children, not just the fit: these become the next
  // target's approved finite-boundary guidance after Apply.
  return [...new Set([
    ...nodes.filter((node) => appliedOwnerId && node.managed_by === appliedOwnerId &&
      ['trimmed_face', 'arranged_face'].includes(node.operation)).map((node) => node.id),
    ...(applied?.reused_faces || []),
    ...(appliedOwnerId ? [appliedOwnerId] : []),
    ...(next?.boundary_sources || []),
    ...(next?.face_scopes || []).flatMap((scope) => scope.faces),
    ...(next?.surfaces || []).flatMap((surface) => [surface.feature, surface.surface].filter(Boolean)),
    ...[target.feature, target.surface].filter(Boolean),
  ])];
}

export async function prepareFaceContinuation({ target, appliedOwnerId, getState,
  ensureCurrent, isCurrent = () => true }) {
  const token = getState().token;
  const inputs = faceContinuationInputs(getState(), target, appliedOwnerId);
  const check = () => {
    if (!isCurrent()) throw new Error('Face review changed. Choose Continue again.');
    const state = getState();
    if (state.token !== token) throw new Error('Actions changed. Review the current faces again.');
    for (const id of inputs) {
      if (!state.recipe.nodes.some((node) => node.id === id))
        throw new Error('Face guidance was removed. Review the current faces again.');
      if (['failed', 'blocked'].includes(state.states[id])) throw new Error(
        state.errors?.[id] || `Could not evaluate ${state.recipe.nodes.find((node) => node.id === id).label}.`);
    }
    return inputs.filter((id) => state.states[id] !== 'ready');
  };
  check();
  await ensureCurrent(inputs, { token, isCurrent });
  const pending = check();
  if (pending.length) throw new Error('Face guidance is not ready. Evaluate its inputs before continuing.');
}

export function faceDisplayEntries(state) {
  return state.recipe.nodes.filter((node) =>
    ['trimmed_face', 'arranged_face'].includes(node.operation) &&
    state.states[node.id] === 'ready' && validGeometryPreview(state.results[node.id]?.preview) &&
    state.results[node.id].preview.indices.length)
    .map((node) => ({ node, result: state.results[node.id] }));
}
