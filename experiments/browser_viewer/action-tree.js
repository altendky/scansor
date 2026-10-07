import { summarizeFitQuality } from './residual-display.js';

// Small, locally drawn SVG symbols; no icon font or network assets required.
const paths = {
  source: 'M12 2 3 7v10l9 5 9-5V7Zm0 10L3 7m9 5 9-5m-9 5v10',
  selection: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M9 9h6v6H9Z',
  plane: 'm3 15 6-9 12 3-6 9Z',
  cylinder: 'M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6',
  cone: 'M12 3 3 18c0 4 18 4 18 0L12 3M3 18c0-4 18-4 18 0',
  sphere: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 0c-4 3-4 17 0 20m0-20c4 3 4 17 0 20M2 12h20',
  point: 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0-5v3m0 14v3M2 12h3m14 0h3',
  frame: 'M5 19V7m0 0-2 3m2-3 3 2M5 19h12m0 0-3-2m3 2-2 3M5 19l7-7m0 0h-4m4 0v4',
  scale: 'M3 17 17 3m-9 9-3-3m7-1-3-3m7-1-3-3M3 17l4 4 14-14-4-4Z',
  transform: 'M4 20V4m0 0-2 3m2-3 3 2m-3 14h16m0 0-3-2m3 2-2 3M9 15l6-6m0 0h-4m4 0v4',
  axis: 'M3 12h18m-4-4 4 4-4 4M7 8l-4 4 4 4',
  reference_plane: 'm3 15 6-9 12 3-6 9ZM12 3v18',
  axis_solve: 'M3 12h18M7 7l-4 5 4 5m10-10 4 5-4 5M12 3v18',
  growth: 'M12 3v18M3 12h18m-12-6 3-3 3 3m-9 3-3 3 3 3m3 3 3 3 3-3m3-9 3 3-3 3',
  selection_region: 'M4 6c0-3 16-3 16 0v12c0 3-16 3-16 0Zm0 0c0 3 16 3 16 0m-8-3v18',
  region_selection: 'M4 7h10M9 3l5 4-5 4m11 2v7H4v-7',
  feature_reuse: 'M5 7h11M12 3l4 4-4 4m7 6H8m4-4-4 4 4 4',
  group: 'M3 6h7l2 2h9v11H3Z',
  reuse_selection: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 12h8m-3-3 3 3-3 3',
  coaxial: 'M12 2v20M5 7c0-4 14-4 14 0s-14 4-14 0Zm0 10c0-4 14-4 14 0s-14 4-14 0Z',
  perpendicular: 'M6 3v15h15M6 13h5v5',
  rotational_symmetry: 'M20 8a9 9 0 1 0 1 7M20 3v5h-5M12 8v4l3 2',
  mirror_symmetry: 'M12 2v20M4 7l6 5-6 5m16-10-6 5 6 5',
  parallel: 'M4 8h16M4 16h16',
  equal: 'M5 9h14M5 15h14',
  equal_radii: 'M5 7h14M5 12h14M5 17h14',
  plane_relationship: 'M4 8h16M4 16h16',
  surface_intersection: 'M3 12h18M12 3v18M6 6l12 12',
  trimmed_face: 'M4 4h16v16H4Zm4 4h8v8H8Z',
  arranged_face: 'M4 4h16v16H4ZM4 12h16M12 4v16',
  build_faces: 'M3 3h8v8H3Zm10 10h8v8h-8ZM7 13v4h4m2-10h4v4',
  body: 'm12 2-9 5v10l9 5 9-5V7Zm0 10L3 7m9 5 9-5m-9 5v10',
  joint_fit: 'M3 3h6v6H3Zm12 0h6v6h-6ZM9 18h6v4H9ZM6 9v4h12V9m-6 4v5',
  ready: 'M22 12a10 10 0 1 1-5-8.66M7 12l3 3L21 4',
  unevaluated: 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  stale: 'M20 8a9 9 0 1 0 1 7M20 3v5h-5',
  running: 'M12 2a10 10 0 0 1 10 10',
  failed: 'M12 3 2 21h20ZM12 9v5m0 3v.5',
  blocked: 'M8 10V7a4 4 0 0 1 8 0v3M5 10h14v11H5Zm7 4v3',
  grip: 'M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01',
  download: 'M4 15v5h16v-5M12 3v12m-5-5 5 5 5-5',
  upload: 'M4 15v5h16v-5M12 15V3m-5 5 5-5 5 5',
  restore: 'M4 10a8 8 0 1 1 1 8M4 4v6h6',
  show_panel: 'M3 4h18v16H3zM9 4v16',
};
const operations = {
  source: 'Source mesh',
  selection: 'Selection',
  fit: 'Surface fit',
  axis: 'Reference axis',
  point: 'Reference point',
  frame: 'Coordinate frame',
  scale: 'Output scale',
  transform: 'Output transform',
  reference_plane: 'Reference plane',
  axis_solve: 'Joint',
  growth: 'Selection growth',
  selection_region: 'Reusable selection region',
  region_selection: 'Applied region selection',
  feature_reuse: 'Feature reuse',
  reuse_selection: 'Reused selection',
  coaxial: 'Coaxial constraint',
  perpendicular: 'Perpendicular constraint',
  rotational_symmetry: 'Rotational symmetry',
  mirror_symmetry: 'Mirror relationship',
  parallel: 'Parallel relationship',
  equal: 'Numeric equality',
  equal_radii: 'All equal radii',
  plane_relationship: 'Plane relationship',
  surface_intersection: 'Surface intersection',
  trimmed_face: 'Trimmed face',
  arranged_face: 'Arranged face',
  build_faces: 'Build faces',
  body: 'Solid body',
  joint_fit: 'Legacy joint fit',
};
const states = {
  ready: 'Ready',
  unevaluated: 'Not evaluated',
  stale: 'Needs evaluation',
  running: 'Evaluating',
  failed: 'Failed',
  blocked: 'Blocked',
};
// Reference entries use the same names and drawings as the tree and pickers.
export const featureIconLegend = [
  { title: 'Fits and reference geometry', icons: [
    'plane', 'cylinder', 'cone', 'sphere', 'reference_plane', 'axis', 'point', 'frame', 'scale', 'transform',
  ] },
  { title: 'Scan, selections and reuse', icons: [
    'source', 'selection', 'growth', 'selection_region', 'region_selection', 'reuse_selection', 'feature_reuse',
  ] },
  { title: 'Faces, body and groups', icons: [
    'build_faces', 'surface_intersection', 'trimmed_face', 'arranged_face', 'body', 'group',
  ] },
  { title: 'Relationships and solve', icons: [
    'plane_relationship', 'parallel', 'perpendicular', 'coaxial', 'equal', 'equal_radii',
    'mirror_symmetry', 'rotational_symmetry', 'axis_solve', 'joint_fit',
  ] },
  { title: 'Evaluation status', icons: Object.keys(states) },
  { title: 'Toolbar and tree controls', icons: ['download', 'upload', 'restore', 'show_panel', 'grip'] },
].map(({ title, icons }) => ({ title, entries: icons.map(name => ({
  name,
  label: states[name] || operations[name] || {
    plane: 'Plane fit', cylinder: 'Cylinder fit', cone: 'Cone fit', sphere: 'Sphere fit',
    group: 'Group / generated outputs', download: 'Save actions', upload: 'Load actions',
    restore: 'Restore example / Reset layout', show_panel: 'Show panel', grip: 'Drag handle',
  }[name],
  state: Object.hasOwn(states, name),
  detail: {
    plane_relationship: 'Coincident or parallel planes',
    joint_fit: 'Also used as the fallback for unknown feature types',
    blocked: 'A dependency or generated output prevents evaluation',
  }[name],
})) }));
export function actionDescription(node, state, error) {
  const kind = node.operation === 'fit' ? `${node.kind} fit` : operations[node.operation];
  if (['mirror_symmetry', 'parallel', 'equal'].includes(node.operation) &&
      !['failed', 'blocked'].includes(state))
    return `${kind || node.operation} · Defined${error ? ` · ${error}` : ''}`;
  return `${kind || node.operation} · ${states[state] || state}${error ? ` · ${error}` : ''}`;
}
// Presentation aggregates ownership without changing execution dependencies:
// a generator can be ready while one of its generated outputs has failed.
export function featureTreePresentation(nodes, featureStates, featureErrors = {}) {
  const byId = new Map(nodes.map((node) => [node.id, node])), children = new Map(),
    presentedStates = { ...featureStates }, presentedErrors = { ...featureErrors },
    visited = new Set(), visiting = new Set();
  for (const node of nodes) {
    const owner = managedOwnerId(node, nodes);
    if (!owner) continue;
    if (!children.has(owner)) children.set(owner, []);
    children.get(owner).push(node.id);
  }
  function visit(id) {
    if (visited.has(id) || visiting.has(id)) return;
    visiting.add(id);
    const node = byId.get(id), outputs = [...new Set([
      ...(children.get(id) || []), ...(node?.reused_faces || []),
    ])].filter((child) => byId.has(child));
    outputs.forEach(visit);
    const members = [id, ...outputs];
    for (const state of ['failed', 'blocked', 'running', 'stale', 'unevaluated', 'ready']) {
      const cause = members.find((member) => (presentedStates[member] || 'unevaluated') === state);
      if (cause === undefined) continue;
      presentedStates[id] = state;
      if (cause !== id) presentedErrors[id] = `${byId.get(cause).label} · ${presentedErrors[cause] || states[state]}`;
      break;
    }
    visiting.delete(id);
    visited.add(id);
  }
  nodes.forEach((node) => visit(node.id));
  return { states: presentedStates, errors: presentedErrors };
}
function outputReferenceIds(reference) {
  if (typeof reference === 'string') return [reference];
  if (!reference?.feature) return [];
  const context = reference.context?.replace(/^@(axis|point)\//, '');
  return [...new Set([reference.feature, ...(context ? [context] : [])])];
}
export function nodeReferences(node) {
  if (node.operation === 'body') return [...new Set(node.faces)];
  if (node.operation === 'build_faces')
    return [...new Set([...node.surfaces.map((reference) => reference.feature),
      ...(node.reused_faces || []), ...(node.reused_intersections || []),
      ...(node.face_scopes || []).flatMap((scope) => scope.faces), ...(node.boundary_sources || [])])];
  if (node.operation === 'surface_intersection')
    return [...new Set([node.first.feature, node.second.feature, ...(node.managed_by ? [node.managed_by] : [])])];
  if (node.operation === 'trimmed_face')
    return [...new Set([node.surface.feature, ...node.boundaries.map((boundary) => boundary.intersection),
      ...(node.boundary_sources || []),
      ...(node.managed_by ? [node.managed_by] : [])])];
  if (node.operation === 'arranged_face')
    return [...new Set([node.surface.feature, ...node.cutters.map((reference) => reference.feature),
      ...node.domains, ...(node.boundary_sources || []), ...(node.managed_by ? [node.managed_by] : [])])];
  if (node.operation === 'selection') return [node.source];
  if (node.operation === 'fit')
    return [
      ...node.selections,
      ...(node.axis ? [node.axis] : []),
      ...(node.point ? [node.point] : []),
      ...(node.reference_plane ? [node.reference_plane] : []),
    ];
  if (node.operation === 'axis')
    return [...new Set([
      ...(node.source_fit ? [node.source_fit] : (node.source_points || []).flatMap(outputReferenceIds)),
      ...(node.placement
        ? [node.placement.reuse, node.placement.source, node.placement.target_selection] : []),
    ])];
  if (node.operation === 'point') return node.source_fit ? [node.source_fit] : [];
  if (node.operation === 'scale')
    return [...new Set([
      ...node.distances.flatMap((distance) =>
        [distance.first_point, distance.second_point].flatMap(outputReferenceIds)),
    ])];
  if (node.operation === 'frame')
    return [...new Set([node.origin_point, node.primary_reference, node.secondary_reference]
      .flatMap(outputReferenceIds))];
  if (node.operation === 'transform') return [node.frame, node.scale];
  if (node.operation === 'reference_plane') return [...new Set([
    node.axis,
    ...(node.placement
      ? [node.placement.reuse, node.placement.source, node.placement.target_selection] : []),
  ])];
  if (node.operation === 'axis_solve') return [node.axis, ...node.factors];
  if (node.operation === 'growth') return [node.seed_fit, ...node.barriers];
  if (node.operation === 'selection_region')
    return [node.selection, node.fit, node.axial_plane, node.clock_plane];
  if (node.operation === 'region_selection')
    return [node.region, node.source, node.axial_plane, node.clock_plane];
  if (node.operation === 'feature_reuse')
    return [...new Set([
      ...node.fits,
      ...node.lineage,
      node.reference_selection,
      ...node.target_selections,
    ])];
  if (node.operation === 'reuse_selection')
    return [node.reuse, node.fit, node.source_selection, node.target_selection];
  if (node.operation === 'coaxial') return [node.surface, node.reference];
  if (node.operation === 'perpendicular') return [node.lateral, node.plane];
  if (node.operation === 'rotational_symmetry') return [node.axis, ...node.planes];
  if (node.operation === 'mirror_symmetry') return [node.plane, ...node.surfaces];
  if (node.operation === 'parallel') return [node.surface, node.reference_plane];
  if (node.operation === 'equal') {
    const refs = [node.left.surface, node.right.surface];
    if (node.left.reference_plane) refs.push(node.left.reference_plane);
    if (node.right.reference_plane) refs.push(node.right.reference_plane);
    return [...new Set(refs)];
  }
  if (node.operation === 'equal_radii') return node.surfaces;
  if (node.operation === 'plane_relationship') return node.surfaces;
  return node.constraints || [];
}

export function managedOwnerId(node, nodes) {
  if (node.managed_by) return node.managed_by;
  if (node.operation === 'reuse_selection') return node.reuse;
  if (node.operation !== 'fit' || !node.selections?.length) return null;
  const byId = new Map(nodes.map((candidate) => [candidate.id, candidate])),
    nodeIndex = nodes.indexOf(node),
    selectionIndices = node.selections.map((id) => nodes.findIndex((candidate) => candidate.id === id)),
    owners = new Set(
      node.selections.map((id) => {
        const selected = byId.get(id);
        return (
          selected?.managed_by ||
          (selected?.operation === 'reuse_selection' ? selected.reuse : null)
        );
      }),
    ),
    contiguousGeneratedInputs =
      selectionIndices.every((index) => index >= 0) &&
      Math.max(...selectionIndices) === nodeIndex - 1 &&
      Math.max(...selectionIndices) - Math.min(...selectionIndices) + 1 === selectionIndices.length;
  return owners.size === 1 && !owners.has(null) && contiguousGeneratedInputs
    ? [...owners][0]
    : null;
}

export function managedSubtreeIds(ownerId, nodes) {
  const result = new Set([ownerId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      if (result.has(node.id) || !result.has(managedOwnerId(node, nodes))) continue;
      result.add(node.id);
      changed = true;
    }
  }
  return result;
}

// Plan one atomic deletion. Additional dependents require explicit UI approval;
// generated features cannot be removed independently of their owning action.
export function featureDeletionPlan(recipe, selected) {
  const nodes = recipe.nodes,
    byId = new Map(nodes.map((node) => [node.id, node])),
    selectedIds = new Set(selected);
  if (!selectedIds.size) return { error: 'Select features to delete.' };
  if ([...selectedIds].some((id) => !byId.has(id)))
    return { error: 'The selection changed. Select features again.' };
  if ([...selectedIds].some((id) => byId.get(id).operation === 'source'))
    return { error: 'The source mesh cannot be deleted.' };
  const removalIds = new Set();
  for (const id of selectedIds)
    for (const child of managedSubtreeIds(id, nodes)) removalIds.add(child);
  for (const id of selectedIds) {
    const owner = managedOwnerId(byId.get(id), nodes);
    if (owner && !removalIds.has(owner))
      return { error: `Select ${byId.get(owner)?.label || owner} to delete its generated outputs.` };
  }
  const initialIds = new Set(removalIds);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      const owner = managedOwnerId(node, nodes);
      if (!removalIds.has(node.id) &&
          (removalIds.has(owner) || nodeReferences(node).some((id) => removalIds.has(id)))) {
        removalIds.add(node.id);
        changed = true;
      }
      // A dependent generated output brings its owner (and therefore siblings).
      if (removalIds.has(node.id) && owner && !removalIds.has(owner)) {
        removalIds.add(owner);
        changed = true;
      }
    }
  }
  if (nodes.some((node) => removalIds.has(node.id) && node.operation === 'source'))
    return { error: 'The source mesh cannot be deleted.' };
  const remaining = nodes.filter((node) => !removalIds.has(node.id));
  if (!remaining.length) return { error: 'Keep at least the source mesh.' };
  return {
    selected: nodes.filter((node) => selectedIds.has(node.id)),
    managed: nodes.filter((node) => initialIds.has(node.id) && !selectedIds.has(node.id)),
    dependents: nodes.filter((node) => removalIds.has(node.id) && !initialIds.has(node.id)),
    removed: nodes.filter((node) => removalIds.has(node.id)),
    recipe: { ...structuredClone(recipe), nodes: structuredClone(remaining),
      output: removalIds.has(recipe.output) ? remaining.at(-1).id : recipe.output },
  };
}

// Slot is a boundary in the original list: 0 before the first, length after the last.
export function actionMove(nodes, id, slot) {
  const ids = Array.isArray(id) ? id : [id];
  if (!ids.length || ids.some((key) => !nodes.some((node) => node.id === key)) ||
      !Number.isInteger(slot) || slot < 0 || slot > nodes.length)
    return { error: 'This feature is no longer available.' };
  const moving = new Set(ids.flatMap((key) => [...managedSubtreeIds(key, nodes)])),
    block = nodes.filter((node) => moving.has(node.id)),
    candidate = nodes.filter((node) => !moving.has(node.id)),
    insertion = nodes.slice(0, slot).filter((node) => !moving.has(node.id)).length;
  const owners = new Set(ids.map((key) => managedOwnerId(nodes.find((node) => node.id === key), nodes)));
  if (owners.size === 1 && !owners.has(null)) {
    const owner = [...owners][0];
    if (!moving.has(owner)) {
      const family = managedSubtreeIds(owner, nodes),
        positions = nodes.flatMap((node, index) => family.has(node.id) ? [index] : []);
      if (slot <= Math.min(...positions) || slot > Math.max(...positions) + 1)
        return { error: 'Generated outputs must stay within their owner group.' };
    }
  }
  candidate.splice(insertion, 0, ...block);
  const seen = new Set();
  for (const item of candidate) {
    const owner = managedOwnerId(item, nodes);
    for (const input of [...nodeReferences(item), ...(owner ? [owner] : [])]) {
      if (!seen.has(input))
        return {
          error: `${item.label} needs ${nodes.find((n) => n.id === input)?.label || input} earlier.`,
        };
    }
    seen.add(item.id);
  }
  return { nodes: candidate, changed: candidate.some((item, i) => item !== nodes[i]) };
}
function icon(name, className = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', `feature-icon ${className}`);
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', paths[name] || paths.joint_fit);
  svg.append(path);
  return svg;
}
export { icon as featureIcon };

// Relationship authorship uses physical fit IDs, not partial solve outputs.
// Keep incompatible selected participants visible so changing kind is reversible.
export function relationshipParticipantChoices(nodes, kind, selected = new Set()) {
  const standalone = (node) => !node.axis && !node.point && !node.reference_plane;
  const eligible = (node) => {
    if (node.operation === 'reference_plane')
      return !kind || kind === 'radius_plane_distance' ||
        (kind === 'mirror' && node.construction === 'contains_axis');
    if (node.operation !== 'fit') return false;
    if (['coincident_planes', 'parallel_planes'].includes(kind)) return node.kind === 'plane';
    if (kind === 'equal_radii') return ['cylinder', 'sphere'].includes(node.kind);
    if (kind === 'mirror') return ['cone', 'cylinder', 'plane'].includes(node.kind) && standalone(node);
    if (kind === 'radius_plane_distance')
      return (node.kind === 'cylinder' && !!node.axis) || (node.kind === 'plane' && standalone(node));
    return ['plane', 'cylinder', 'cone', 'sphere'].includes(node.kind);
  };
  return nodes.filter((node) => eligible(node) || selected.has(node.id))
    .map((node) => ({ node, compatible: eligible(node) }));
}

export function renderRelationshipParticipants(container, {
  choices, selected, change, inspect, focus, locked = () => false,
}) {
  const scroll = container.scrollTop;
  const active = container.contains(document.activeElement) ? document.activeElement : null;
  const activeId = active?.dataset.participantId;
  const activeFocus = active?.dataset.participantFocus;
  let hoveredId = null, focusedId = null, inspectedId = null;
  const inspectActive = () => {
    const id = hoveredId || focusedId;
    if (id === inspectedId) return;
    inspectedId = id;
    inspect(id);
  };
  container.replaceChildren(...choices.map((choice) => {
    const row = document.createElement('div'), label = document.createElement('label'),
      checkbox = document.createElement('input'), text = document.createElement('span'),
      name = document.createElement('span'), detail = document.createElement('span'),
      focusButton = document.createElement('button');
    row.className = 'relationship-participant';
    row.dataset.compatible = String(choice.compatible);
    row.dataset.search = `${choice.name} ${choice.detail}`.toLowerCase();
    row.classList.toggle('participant-selected', selected.has(choice.id));
    label.title = `${choice.name} — ${choice.detail}`;
    checkbox.type = 'checkbox';
    checkbox.checked = selected.has(choice.id);
    checkbox.disabled = locked() || (!choice.compatible && !checkbox.checked);
    checkbox.dataset.participantId = choice.id;
    checkbox.setAttribute('aria-label', choice.name);
    checkbox.onchange = () => {
      if (locked()) { checkbox.checked = selected.has(choice.id); return; }
      change(choice.id, checkbox.checked);
      row.classList.toggle('participant-selected', checkbox.checked);
      // An incompatible retained participant can be removed, not added back.
      checkbox.disabled = !choice.compatible && !checkbox.checked;
    };
    text.className = 'face-candidate-text';
    name.className = 'face-candidate-name';
    detail.className = 'face-candidate-kind';
    name.textContent = choice.name;
    detail.textContent = choice.detail;
    text.append(name, detail);
    label.append(checkbox, icon(choice.icon, 'action-type'), text);
    focusButton.type = 'button';
    focusButton.textContent = 'Focus';
    focusButton.dataset.participantId = choice.id;
    focusButton.dataset.participantFocus = 'true';
    focusButton.setAttribute('aria-label', `Focus ${choice.name}`);
    focusButton.onclick = () => focus(choice.id);
    row.onpointerenter = () => { hoveredId = choice.id; inspectActive(); };
    row.onpointerleave = () => {
      if (hoveredId === choice.id) hoveredId = null;
      inspectActive();
    };
    row.onfocusin = () => { focusedId = choice.id; inspectActive(); };
    row.onfocusout = (event) => {
      if (!row.contains(event.relatedTarget) && focusedId === choice.id) focusedId = null;
      inspectActive();
    };
    row.append(label, focusButton);
    return row;
  }));
  if (activeId) {
    const replacement = [...container.querySelectorAll('[data-participant-id]')].find((element) =>
      element.dataset.participantId === activeId && element.dataset.participantFocus === activeFocus);
    replacement?.focus();
  }
  container.scrollTop = scroll;
}

export function iconPickerIndex(key, index, count) {
  if (!count) return -1;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  if (key === 'ArrowDown') return (index + 1 + count) % count;
  if (key === 'ArrowUp') return (index - 1 + count) % count;
  return index;
}

export function renderIconPicker(button, list, { choices, value, change, locked = () => false, preview = () => {} }) {
  if (list.matches(':popover-open')) list.hidePopover();
  let active = Math.max(0, choices.findIndex((choice) => choice.value === value)),
    search = '', searchTime = 0;
  const selected = choices.find((choice) => choice.value === value),
    text = (choice) => {
      const column = document.createElement('span'),
        name = document.createElement('span'), detail = document.createElement('span');
      column.className = 'icon-picker-text';
      name.className = 'icon-picker-name';
      detail.className = 'icon-picker-detail';
      name.textContent = choice?.name || 'Choose a surface';
      detail.textContent = choice?.detail || '';
      column.append(name, detail);
      return column;
    },
    close = () => {
      list.hidePopover();
      button.setAttribute('aria-expanded', 'false');
      button.removeAttribute('aria-activedescendant');
      search = '';
      preview(null);
    },
    highlight = (index, scroll = false) => {
      active = index;
      [...list.children].forEach((option, i) => option.classList.toggle('picker-active', i === active));
      if (list.children[active]) {
        button.setAttribute('aria-activedescendant', list.children[active].id);
        if (scroll) list.children[active].scrollIntoView({ block: 'nearest' });
      }
      if (!locked() && list.matches(':popover-open')) preview(choices[active]?.value || null);
    },
    open = () => {
      if (locked() || button.disabled) return false;
      const bounds = button.getBoundingClientRect(),
        viewportWidth = globalThis.innerWidth, viewportHeight = globalThis.innerHeight;
      list.style.width = `${Math.min(Math.max(bounds.width, 320), viewportWidth - 16)}px`;
      list.style.maxHeight = `${Math.min(320, viewportHeight - 16)}px`;
      list.showPopover();
      const popup = list.getBoundingClientRect(),
        below = viewportHeight - bounds.bottom - 8,
        above = bounds.top - 8;
      const down = below >= Math.min(popup.height, 180) || below >= above;
      list.style.maxHeight = `${Math.max(40, Math.min(320, down ? below : above))}px`;
      list.style.left = `${Math.max(8, Math.min(bounds.left, viewportWidth - popup.width - 8))}px`;
      list.style.top = `${Math.max(8, down ? bounds.bottom + 3 : bounds.top - Math.min(popup.height, above) - 3)}px`;
      button.setAttribute('aria-expanded', 'true');
      highlight(active, true);
      return true;
    },
    choose = (index) => {
      if (locked() || button.disabled || !choices[index]) return;
      close();
      button.focus();
      if (choices[index].value !== value) change(choices[index].value);
    };
  const arrow = document.createElement('span');
  arrow.textContent = '▾';
  arrow.setAttribute('aria-hidden', 'true');
  button.replaceChildren(...(selected ? [icon(selected.icon, 'action-type')] : []), text(selected), arrow);
  button.title = selected?.label || 'Choose a surface';
  button.disabled = !choices.length;
  button.setAttribute('aria-label', `Surface: ${button.title}`);
  button.setAttribute('aria-expanded', 'false');
  button.removeAttribute('aria-activedescendant');
  list.replaceChildren(...choices.map((choice, index) => {
    const option = document.createElement('div');
    option.id = `${list.id}-${index}`;
    option.className = 'icon-picker-option';
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(choice.value === value));
    option.setAttribute('aria-label', choice.label);
    option.title = choice.label;
    option.append(icon(choice.icon, 'action-type'), text(choice));
    // Scrolling to a keyboard option can put a different row under a stationary
    // pointer. Only actual pointer movement should replace keyboard navigation.
    option.onpointermove = (event) => {
      if (event.movementX || event.movementY) highlight(index);
    };
    option.onmousedown = (event) => event.preventDefault();
    option.onclick = () => choose(index);
    return option;
  }));
  list.ontoggle = () => { if (!list.matches(':popover-open')) close(); };
  button.onclick = () => list.matches(':popover-open') ? close() : open();
  button.onkeydown = (event) => {
    const expanded = list.matches(':popover-open');
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      if (!expanded) {
        if (open() && ['Home', 'End'].includes(event.key))
          highlight(iconPickerIndex(event.key, active, choices.length), true);
      } else if (['Enter', ' '].includes(event.key)) choose(active);
      else highlight(iconPickerIndex(event.key, active, choices.length), true);
    } else if (expanded && ['Escape', 'Tab'].includes(event.key)) {
      close();
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); }
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (!expanded && !open()) return;
      const now = Date.now(), letter = event.key.toLowerCase();
      search = now - searchTime > 700 || search === letter ? letter : search + letter;
      searchTime = now;
      const indices = choices.map((_, i) => (active + (search.length > 1 ? 0 : 1) + i) % choices.length),
        match = indices.find((i) => choices[i].name.toLowerCase().startsWith(search));
      if (match !== undefined) highlight(match, true);
    }
  };
}
export function treeDragScrollSpeed(y, bounds) {
  if (y < bounds.top || y > bounds.bottom) return 0;
  const band = Math.min(48, (bounds.bottom - bounds.top) / 3);
  if (band <= 0) return 0;
  if (y < bounds.top + band) return -650 * (1 - (y - bounds.top) / band);
  if (y > bounds.bottom - band) return 650 * (1 - (bounds.bottom - y) / band);
  return 0;
}

const treeDragCleanups = new WeakMap();
function afterContextGesture(event, open) {
  // Chromium can dispatch contextmenu before pointerup. Opening an auto
  // popover then lets that same pointerup immediately light-dismiss it.
  if (event.buttons & 2) document.addEventListener('pointerup', () => setTimeout(open, 0), { once: true });
  else open();
}
export function renderActionTree(
  list,
  {
    nodes,
    groups = [],
    selected,
    states,
    errors,
    locked,
    select,
    edit = () => {},
    move,
    announce,
    groupContextMenu = () => {},
    removeGroup = () => {},
    contextMenu = () => {},
    qualities = {},
    scrollContainer = list.parentElement || list,
  },
) {
  treeDragCleanups.get(list)?.();
  ({ states, errors } = featureTreePresentation(nodes, states, errors));
  let dragged = null,
    dropSlot = null, pointerDrag = null, dragPoint = null, scrollFrame = null,
    scrollTime = null, wheelPauseUntil = 0;
  const expandedManaged = renderActionTree.expandedManaged ||= new Set(),
    expandedTargets = renderActionTree.expandedTargets ||= new Set(),
    collapsedGroups = renderActionTree.collapsedGroups ||= new Set(),
    byId = new Map(nodes.map((node) => [node.id, node])),
    ownerById = new Map(nodes.map((node) => [node.id, managedOwnerId(node, nodes)])),
    managed = new Map(),
    selectedIds = selected instanceof Set ? selected : new Set(selected ? [selected] : []);
  for (const node of nodes) {
    const owner = ownerById.get(node.id);
    if (!owner) continue;
    if (!managed.has(owner)) managed.set(owner, []);
    managed.get(owner).push(node);
  }
  const unavailable = () => list.getAttribute('aria-busy') === 'true' || locked();
  const lockControls = [];
  function qualityBadge(quality, generatedOwner = null) {
    const badge = document.createElement('span');
    badge.className = `fit-quality quality-${quality.status}`;
    badge.textContent = quality.label;
    badge.title = `${generatedOwner ? `Generated by ${byId.get(generatedOwner)?.label || generatedOwner}.\n` : ''}${quality.tooltip}`;
    return badge;
  }
  function groupQuality(members) {
    const fits = new Map(), visited = new Set();
    function visit(node) {
      if (visited.has(node.id)) return;
      visited.add(node.id);
      if (qualities[node.id]) fits.set(node.id, qualities[node.id]);
      for (const child of managed.get(node.id) || []) visit(child);
    }
    members.forEach(visit);
    return summarizeFitQuality([...fits.values()]);
  }
  function clearDrop() {
    dropSlot = null;
    for (const row of list.querySelectorAll('.action-drop-target'))
      row.classList.remove('drop-before', 'drop-after', 'drop-invalid');
  }
  function stopScrolling() {
    if (scrollFrame !== null) globalThis.cancelAnimationFrame?.(scrollFrame);
    scrollFrame = scrollTime = null;
  }
  function finishDrag() {
    stopScrolling();
    const gesture = pointerDrag;
    pointerDrag = dragPoint = dragged = null;
    clearDrop();
    scrollContainer.classList.remove('feature-reordering');
    for (const row of list.querySelectorAll('.dragging')) row.classList.remove('dragging');
    if (gesture?.grip.hasPointerCapture?.(gesture.id)) gesture.grip.releasePointerCapture(gesture.id);
  }
  function pointerInside() {
    if (!dragPoint) return false;
    const bounds = scrollContainer.getBoundingClientRect();
    return dragPoint.x >= bounds.left && dragPoint.x <= bounds.right &&
      dragPoint.y >= bounds.top && dragPoint.y <= bounds.bottom;
  }
  function updateDrop(row, y, transfer = null) {
    clearDrop();
    if (!dragged || unavailable() || !row || !list.contains(row) ||
        (row.dataset.dropScope || null) !== dragged.scope) return;
    const after = y > row.getBoundingClientRect().top + row.clientHeight / 2,
      slot = Number(after ? row.dataset.dropEnd : row.dataset.dropStart),
      candidate = actionMove(nodes, dragged.ids, slot);
    dropSlot = slot;
    row.classList.add(after ? 'drop-after' : 'drop-before');
    row.classList.toggle('drop-invalid', !!candidate.error);
    if (transfer) transfer.dropEffect = candidate.error ? 'none' : 'move';
    if (candidate.error) announce(candidate.error, true);
  }
  function updatePointerDrop() {
    if (!pointerInside()) { clearDrop(); return; }
    const row = document.elementFromPoint?.(dragPoint.x, dragPoint.y)?.closest('.action-drop-target');
    updateDrop(row, dragPoint.y);
  }
  function scrollTick(time) {
    scrollFrame = null;
    if (!dragged || unavailable()) { finishDrag(); return; }
    if (!pointerInside()) { stopScrolling(); return; }
    const speed = time < wheelPauseUntil ? 0
      : treeDragScrollSpeed(dragPoint.y, scrollContainer.getBoundingClientRect()),
      elapsed = Math.min(32, scrollTime === null ? 16 : time - scrollTime),
      previous = scrollContainer.scrollTop;
    scrollTime = time;
    scrollContainer.scrollTop = Math.max(0, Math.min(
      scrollContainer.scrollHeight - scrollContainer.clientHeight, previous + speed * elapsed / 1000));
    updatePointerDrop();
    if (time < wheelPauseUntil || (speed && scrollContainer.scrollTop !== previous))
      scrollFrame = globalThis.requestAnimationFrame?.(scrollTick) ?? null;
    else stopScrolling();
  }
  function startScrolling() {
    if (scrollFrame === null && dragged && pointerInside())
      scrollFrame = globalThis.requestAnimationFrame?.(scrollTick) ?? null;
  }
  function scrolled() {
    if (!dragged) return;
    if (unavailable()) { finishDrag(); return; }
    updatePointerDrop();
  }
  function wheeled() {
    if (!dragged) return;
    // Keep native pixel/line/page wheel semantics, including trackpad inertia.
    // Briefly yield edge scrolling so it doesn't oppose the user's wheel.
    wheelPauseUntil = performance.now() + 180;
    startScrolling();
  }
  function escapeDrag(event) {
    if (event.key !== 'Escape' || !pointerDrag) return;
    event.preventDefault();
    event.stopPropagation();
    finishDrag();
  }
  scrollContainer.addEventListener?.('scroll', scrolled, { passive: true });
  scrollContainer.addEventListener?.('wheel', wheeled, { passive: true });
  document.addEventListener?.('keydown', escapeDrag, true);
  globalThis.addEventListener?.('blur', finishDrag);
  treeDragCleanups.set(list, () => {
    finishDrag();
    scrollContainer.removeEventListener?.('scroll', scrolled);
    scrollContainer.removeEventListener?.('wheel', wheeled);
    document.removeEventListener?.('keydown', escapeDrag, true);
    globalThis.removeEventListener?.('blur', finishDrag);
  });
  async function commit(block, slot, focusHandle = false) {
    if (unavailable()) {
      announce('Wait for the current edit or evaluation to finish.', true);
      return;
    }
    const candidate = actionMove(nodes, block.ids, slot);
    if (candidate.error) {
      announce(candidate.error, true);
      return;
    }
    if (!candidate.changed) return;
    list.setAttribute('aria-busy', 'true');
    try {
      if (await move(candidate.nodes)) {
        announce(`Moved ${block.label}.`);
        if (focusHandle) {
          [...list.querySelectorAll('.action-grip')]
            .find((button) => button.dataset.reorderKey === block.key)
            ?.focus();
        }
      }
    } finally {
      list.removeAttribute('aria-busy');
    }
  }
  function reorderHandle(row, ids, key, label, scope = null) {
    const grip = document.createElement('button'),
      moving = new Set(ids.flatMap((id) => [...managedSubtreeIds(id, nodes)])),
      indices = nodes.flatMap((node, index) => moving.has(node.id) ? [index] : []),
      start = Math.min(...indices), end = Math.max(...indices) + 1,
      block = { ids, key, label, scope };
    grip.type = 'button';
    grip.className = 'action-grip';
    grip.dataset.reorderKey = key;
    grip.append(icon('grip'));
    const updateLock = () => {
      grip.disabled = !ids.length || unavailable();
      grip.draggable = !!ids.length && !unavailable();
    };
    lockControls.push(updateLock);
    updateLock();
    grip.title = `Drag to reorder ${label} with its actions; or focus here and use the arrow keys.`;
    grip.setAttribute('aria-label', `Reorder ${label}. Use Up or Down arrow keys.`);
    if (!ids.length) {
      grip.title = 'Empty groups have no actions to reorder.';
      return grip;
    }
    row.classList.add('action-drop-target');
    row.dataset.dropStart = start;
    row.dataset.dropEnd = end;
    row.dataset.dropScope = scope || '';
    grip.onclick = (event) => { event.preventDefault(); event.stopPropagation(); };
    grip.onpointerdown = (event) => {
      grip.focus();
      if (event.button !== 0 || unavailable()) return;
      event.preventDefault();
      event.stopPropagation();
      finishDrag();
      pointerDrag = { id: event.pointerId, grip, row, block,
        startX: event.clientX, startY: event.clientY };
      grip.setPointerCapture(event.pointerId);
    };
    grip.onpointermove = (event) => {
      if (pointerDrag?.id !== event.pointerId) return;
      if (unavailable()) { finishDrag(); return; }
      dragPoint = { x: event.clientX, y: event.clientY };
      if (!dragged && Math.hypot(event.clientX - pointerDrag.startX,
        event.clientY - pointerDrag.startY) < 5) return;
      dragged = block;
      row.classList.add('dragging');
      scrollContainer.classList.add('feature-reordering');
      updatePointerDrop();
      startScrolling();
    };
    grip.onpointerup = (event) => {
      if (pointerDrag?.id !== event.pointerId) return;
      dragPoint = { x: event.clientX, y: event.clientY };
      updatePointerDrop();
      const moving = dragged, slot = dropSlot;
      finishDrag();
      if (moving && slot !== null) void commit(moving, slot, true);
    };
    grip.onpointercancel = grip.onlostpointercapture = () => finishDrag();
    grip.onkeydown = (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      if (scope) {
        const siblings = [...list.querySelectorAll('.action-drop-target')]
          .filter((target) => target.dataset.dropScope === scope && target !== row),
          up = event.key === 'ArrowUp',
          adjacent = siblings.filter((target) => up
            ? Number(target.dataset.dropEnd) <= start
            : Number(target.dataset.dropStart) >= end)
            .sort((a, b) => up
              ? Number(b.dataset.dropEnd) - Number(a.dataset.dropEnd)
              : Number(a.dataset.dropStart) - Number(b.dataset.dropStart))[0];
        if (adjacent) void commit(block, Number(up ? adjacent.dataset.dropStart : adjacent.dataset.dropEnd), true);
        return;
      }
      const up = event.key === 'ArrowUp', adjacent = nodes[up ? start - 1 : end];
      if (!adjacent) return;
      let owner = adjacent;
      while (ownerById.get(owner.id)) owner = byId.get(ownerById.get(owner.id));
      const peers = owner.group_id && !ids.some((id) => byId.get(id).group_id === owner.group_id)
        ? nodes.filter((node) => node.group_id === owner.group_id).map((node) => node.id)
        : [owner.id],
        adjacentIds = new Set(peers.flatMap((id) => [...managedSubtreeIds(id, nodes)])),
        positions = nodes.flatMap((node, index) => adjacentIds.has(node.id) ? [index] : []);
      void commit(block, up ? Math.min(...positions) : Math.max(...positions) + 1, true);
    };
    grip.ondragstart = (event) => {
      // Real mouse/touch gestures use pointer capture so wheel scrolling stays
      // available. Retain native handlers for non-pointer/synthetic clients.
      if (pointerDrag) { event.preventDefault(); return; }
      if (unavailable()) { event.preventDefault(); return; }
      dragged = block;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', key);
      row.classList.add('dragging');
      event.dataTransfer.setDragImage(row, 20, row.clientHeight / 2);
    };
    grip.ondragend = () => {
      finishDrag();
    };
    return grip;
  }
  function stateFor(items) {
    for (const state of ['failed', 'blocked', 'running', 'stale', 'unevaluated'])
      if (items.some((item) => states[item.id] === state)) return state;
    return 'ready';
  }
  function stateDescription(items, state) {
    return items.filter((item) => states[item.id] === state)
      .map((item) => `${item.label} · ${errors[item.id] || actionDescription(item, state)}`).join('\n');
  }
  function actionItem(node, generated = false) {
    const index = nodes.indexOf(node),
      item = document.createElement('li'),
      row = document.createElement('div'),
      owned = managed.get(node.id) || [],
      grip = generated ? document.createElement('button') : reorderHandle(row, [node.id], node.id, node.label);
    item.className = 'action-entry';
    row.classList.add('action-row');
    row.dataset.actionIndex = index;
    if (generated) row.dataset.managed = 'true';
    if (generated) {
      grip.className = 'action-grip action-grip-placeholder';
      grip.disabled = true;
      grip.tabIndex = -1;
      grip.title = `Managed by ${byId.get(ownerById.get(node.id))?.label || ownerById.get(node.id)}`;
      grip.append(icon('feature_reuse'));
    }
    const button = document.createElement('button');
    button.className = 'action-select';
    button.dataset.actionId = node.id;
    button.setAttribute('aria-pressed', String(selectedIds.has(node.id)));
    const relationship = ['mirror_symmetry', 'parallel', 'equal'].includes(node.operation) &&
        !['failed', 'blocked'].includes(states[node.id]),
      description = actionDescription(node, states[node.id], errors[node.id]),
      quality = qualities[node.id];
    button.title = `${node.label} · ${description}${quality ? `\n${quality.tooltip}` : ''}`;
    button.setAttribute('aria-label', `${node.label} · ${description}${generated ? ' · Generated' : ''}${quality ? ` · ${quality.label} · ${quality.status}` : ''}`);
    const openContextMenu = (event, keyboard = false) => {
      event.preventDefault();
      event.stopPropagation();
      if (unavailable()) return;
      const row = event.currentTarget || button;
      const bounds = row.getBoundingClientRect();
      const position = {
        x: keyboard ? bounds.left : event.clientX,
        y: keyboard ? bounds.bottom : event.clientY,
      };
      afterContextGesture(event, () => { if (!unavailable()) contextMenu(node.id, position); });
    };
    button.oncontextmenu = openContextMenu;
    button.ondblclick = event => {
      event.preventDefault();
      event.stopPropagation();
      if (unavailable() || event.target.closest('.action-grip')) return;
      edit(node.id);
    };
    button.onkeydown = (event) => {
      if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
        openContextMenu(event, true);
        return;
      }
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const adjacent = nodes[index + (event.key === 'ArrowUp' ? -1 : 1)];
      if (!adjacent) return;
      select(adjacent.id, { exclusive: true });
      [...list.querySelectorAll('.action-select, .managed-owner-summary')]
        .find((candidate) => candidate.dataset.actionId === adjacent.id)
        ?.focus();
    };
    const label = document.createElement('span');
    label.className = 'action-name';
    label.textContent = node.label;
    button.append(icon(node.operation === 'fit' ? node.kind : node.operation, 'action-type'), label);
    if (generated && !quality) {
      const badge = document.createElement('span');
      badge.className = 'generated-badge';
      badge.textContent = 'Generated';
      button.append(badge);
    }
    if (quality) button.append(qualityBadge(quality, generated ? ownerById.get(node.id) : null));
    button.append(
      icon(
        relationship ? 'ready' : states[node.id],
        `action-state state-${relationship ? 'ready' : states[node.id]}`,
      ),
    );
    button.onclick = () => {
      select(node.id);
      [...list.querySelectorAll('.action-select')]
        .find((candidate) => candidate.dataset.actionId === node.id)
        ?.focus();
    };
    if (!owned.length) {
      row.append(grip, button);
      item.append(row);
    } else {
      const details = document.createElement('details'),
        summary = document.createElement('summary'),
        summaryState = relationship ? 'ready' : states[node.id],
        ownerQuality = groupQuality([node]);
      item.classList.add('managed-owner');
      details.className = 'tree-group managed-owner-group';
      details.open = expandedManaged.has(node.id);
      summary.className = 'managed-owner-summary';
      summary.dataset.actionId = node.id;
      summary.dataset.actionIndex = index;
      summary.classList.toggle('feature-selected', selectedIds.has(node.id));
      summary.title = `${node.label} · ${description}`;
      summary.setAttribute('aria-label', `${summary.title}${selectedIds.has(node.id) ? ' · Selected' : ''}${ownerQuality ? ` · ${ownerQuality.label} · ${ownerQuality.status}` : ''}`);
      summary.onkeydown = button.onkeydown;
      summary.oncontextmenu = openContextMenu;
      summary.ondblclick = button.ondblclick;
      label.classList.add('tree-group-name');
      summary.append(
        reorderHandle(summary, [node.id], node.id, node.label, generated ? ownerById.get(node.id) : null),
        icon('group', 'group-icon'),
        label,
        ...(ownerQuality ? [qualityBadge(ownerQuality)] : []),
        icon(summaryState, `action-state state-${summaryState}`),
      );
      details.ontoggle = () => {
        if (details.open) expandedManaged.add(node.id);
        else expandedManaged.delete(node.id);
      };
      details.append(summary);
      const targets = new Map();
      for (const child of owned) {
        const target =
          child.operation === 'equal_radii'
            ? '__relationships__'
            : child.target_selection || byId.get(child.selections?.[0])?.target_selection || '';
        if (!targets.has(target)) targets.set(target, []);
        targets.get(target).push(child);
      }
      for (const [target, children] of targets) {
        const targetDetails = document.createElement('details'),
          targetSummary = document.createElement('summary'),
          targetLabel = document.createElement('span'),
          targetBadge = document.createElement('span'),
          targetKey = `${node.id}/${target}`,
          childList = document.createElement('ul'),
          targetState = stateFor(children);
        targetSummary.title = stateDescription(children, targetState);
        targetDetails.className = 'tree-group managed-group managed-target';
        targetDetails.open = expandedTargets.has(targetKey);
        targetLabel.className = 'tree-group-name';
        targetLabel.textContent = target === '__relationships__'
          ? `Relationships · ${children.length}`
          : target
          ? `${byId.get(target)?.label || target} · ${children.filter((child) => child.operation === 'fit').length} fits`
          : `Other outputs · ${children.length}`;
        targetSummary.setAttribute('aria-label', `${targetLabel.textContent} · ${targetSummary.title}`);
        targetBadge.className = 'managed-badge';
        targetBadge.textContent = 'Generated';
        const quality = groupQuality(children);
        targetSummary.append(
          reorderHandle(targetSummary, children.map((child) => child.id), targetKey, targetLabel.textContent, node.id),
          icon('group', 'group-icon'),
          targetLabel,
          quality ? qualityBadge(quality, node.id) : targetBadge,
          icon(targetState, `action-state state-${targetState}`),
        );
        targetDetails.ontoggle = () => {
          if (targetDetails.open) expandedTargets.add(targetKey);
          else expandedTargets.delete(targetKey);
        };
        childList.className = 'nested-actions';
        childList.append(...children.map((child) => actionItem(child, true)));
        targetDetails.append(targetSummary, childList);
        details.append(targetDetails);
      }
      item.append(details);
    }
    return item;
  }
  function groupItem(group, members) {
    const item = document.createElement('li'),
      details = document.createElement('details'),
      summary = document.createElement('summary'),
      label = document.createElement('span'),
      remove = document.createElement('button'),
      children = document.createElement('ul');
    item.className = 'feature-group';
    details.className = 'tree-group';
    details.open = !collapsedGroups.has(group.id);
    label.className = 'tree-group-name';
    label.textContent = `${group.label} · ${members.length}`;
    remove.type = 'button';
    remove.className = 'group-action';
    const updateLock = () => { remove.disabled = unavailable(); };
    lockControls.push(updateLock);
    updateLock();
    const openContextMenu = (event, keyboard = false) => {
      event.preventDefault();
      event.stopPropagation();
      if (unavailable()) return;
      const bounds = summary.getBoundingClientRect();
      const position = {
        x: keyboard ? bounds.left : event.clientX,
        y: keyboard ? bounds.bottom : event.clientY,
      };
      afterContextGesture(event, () => { if (!unavailable()) groupContextMenu(group.id, position); });
    };
    summary.dataset.groupId = group.id;
    summary.oncontextmenu = openContextMenu;
    summary.onkeydown = event => {
      if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey))
        openContextMenu(event, true);
    };
    remove.textContent = '×';
    remove.title = `Remove ${group.label} without deleting its features`;
    remove.setAttribute('aria-label', remove.title);
    remove.onclick = (event) => {
      event.preventDefault();
      if (unavailable()) return;
      removeGroup(group.id);
    };
    const quality = groupQuality(members), groupState = stateFor(members);
    summary.title = stateDescription(members, groupState);
    summary.setAttribute('aria-label', `${group.label} · ${summary.title}`);
    summary.append(reorderHandle(summary, members.map((node) => node.id), group.id, group.label),
      icon('group', 'group-icon'), label, ...(quality ? [qualityBadge(quality)] : []), remove,
      icon(groupState, `action-state state-${groupState}`));
    details.ontoggle = () => {
      if (details.open) collapsedGroups.delete(group.id);
      else collapsedGroups.add(group.id);
    };
    children.className = 'nested-actions group-actions';
    children.append(...members.map((node) => actionItem(node)));
    details.append(summary, children);
    item.append(details);
    return item;
  }
  const ordinary = nodes.filter((node) => !ownerById.get(node.id)),
    grouped = new Map(groups.map((group) => [group.id, []]));
  for (const node of ordinary)
    if (node.group_id && grouped.has(node.group_id)) grouped.get(node.group_id).push(node);
  const renderedGroups = new Set(),
    items = [];
  for (const node of ordinary) {
    if (!node.group_id) {
      items.push(actionItem(node));
      continue;
    }
    if (renderedGroups.has(node.group_id)) continue;
    renderedGroups.add(node.group_id);
    items.push(
      groupItem(groups.find((group) => group.id === node.group_id), grouped.get(node.group_id)),
    );
  }
  for (const group of groups)
    if (!renderedGroups.has(group.id)) items.push(groupItem(group, []));
  list.replaceChildren(...items);
  list.ondragover = (event) => {
    if (!dragged || unavailable()) {
      clearDrop();
      return;
    }
    const row = event.target.closest('.action-drop-target');
    updateDrop(row, event.clientY, event.dataTransfer);
    if (dropSlot !== null) event.preventDefault();
  };
  list.ondragleave = (event) => {
    if (!list.contains(event.relatedTarget)) clearDrop();
  };
  list.ondrop = (event) => {
    event.preventDefault();
    const id = dragged,
      slot = dropSlot;
    finishDrag();
    if (id && slot !== null) void commit(id, slot, true);
  };
  // Lifecycle-only polling can refresh locks without replacing tree controls.
  return () => { lockControls.forEach(update => update()); };
}
