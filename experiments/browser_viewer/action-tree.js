import { uniqueFeatureLabel } from './feature-names.js';

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
  joint_fit: 'M3 3h6v6H3Zm12 0h6v6h-6ZM9 18h6v4H9ZM6 9v4h12V9m-6 4v5',
  ready: 'M22 12a10 10 0 1 1-5-8.66M7 12l3 3L21 4',
  unevaluated: 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  stale: 'M20 8a9 9 0 1 0 1 7M20 3v5h-5',
  running: 'M12 2a10 10 0 0 1 10 10',
  failed: 'M12 3 2 21h20ZM12 9v5m0 3v.5',
  grip: 'M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01',
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
  joint_fit: 'Legacy joint fit',
};
const states = {
  ready: 'Ready',
  unevaluated: 'Not evaluated',
  stale: 'Needs evaluation',
  running: 'Evaluating',
  failed: 'Failed',
};
export function actionDescription(node, state, error) {
  const kind = node.operation === 'fit' ? `${node.kind} fit` : operations[node.operation];
  if (['mirror_symmetry', 'parallel', 'equal'].includes(node.operation))
    return `${kind || node.operation} · Defined${error ? ` · ${error}` : ''}`;
  return `${kind || node.operation} · ${states[state] || state}${error ? ` · ${error}` : ''}`;
}
export function nodeReferences(node) {
  if (node.operation === 'selection') return [node.source];
  if (node.operation === 'fit')
    return [
      ...node.selections,
      ...(node.axis ? [node.axis] : []),
      ...(node.point ? [node.point] : []),
      ...(node.reference_plane ? [node.reference_plane] : []),
    ];
  if (node.operation === 'axis')
    return node.source_fit ? [node.source_fit] : [...(node.source_points || [])];
  if (node.operation === 'point') return node.source_fit ? [node.source_fit] : [];
  if (node.operation === 'scale')
    return [...new Set([
      ...node.distances.flatMap((distance) => [distance.first_point, distance.second_point]),
    ])];
  if (node.operation === 'frame')
    return [...new Set([node.origin_point, node.primary_reference, node.secondary_reference])];
  if (node.operation === 'transform') return [node.frame, node.scale];
  if (node.operation === 'reference_plane') return [node.axis];
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

export function discoverReuseLineage(nodes, fitIds) {
  const byId = new Map(nodes.map((node) => [node.id, node])),
    selected = new Set(fitIds),
    closure = new Set(fitIds),
    relationshipOperations = new Set([
      'perpendicular',
      'coaxial',
      'rotational_symmetry',
      'joint_fit',
      'mirror_symmetry',
      'parallel',
      'equal',
      'equal_radii',
      'plane_relationship',
      'axis_solve',
    ]);
  const addUpstream = () => {
    const before = closure.size;
    for (const id of [...closure])
      for (const dependency of nodeReferences(byId.get(id) || {})) closure.add(dependency);
    return closure.size !== before;
  };
  const referencedFits = (candidate) => {
    const found = new Set(),
      pending = [...nodeReferences(candidate)],
      visited = new Set();
    while (pending.length) {
      const id = pending.pop();
      if (visited.has(id)) continue;
      visited.add(id);
      const referenced = byId.get(id);
      if (referenced?.operation === 'fit') found.add(id);
      else if (relationshipOperations.has(referenced?.operation))
        pending.push(...nodeReferences(referenced));
    }
    return found;
  };
  while (addUpstream()) { /* Find the full upstream closure. */ }
  let changed = true;
  while (changed) {
    changed = false;
    for (const candidate of nodes) {
      if (closure.has(candidate.id) || !relationshipOperations.has(candidate.operation)) continue;
      const references = nodeReferences(candidate),
        fitted = referencedFits(candidate),
        enclosed = fitted.size > 0 && [...fitted].every((id) => selected.has(id));
      if (!enclosed) continue;
      closure.add(candidate.id);
      references.forEach((id) => closure.add(id));
      changed = true;
    }
    while (addUpstream()) changed = true;
  }
  return nodes.filter((node) => closure.has(node.id)).map((node) => node.id);
}

export function reconcileFeatureReuse(nodes, reuseId, patch, makeId) {
  const next = structuredClone(nodes),
    reuse = next.find((node) => node.id === reuseId);
  if (reuse?.operation !== 'feature_reuse')
    return { error: 'This feature reuse action is no longer available.' };
  Object.assign(reuse, patch);
  if (!reuse.fits.length || !reuse.target_selections.length)
    return { error: 'Choose at least one fit and one target selection.' };
  if (reuse.target_selections.includes(reuse.reference_selection))
    return { error: 'The reference selection cannot also be a target.' };
  reuse.lineage = discoverReuseLineage(next, reuse.fits);

  const desired = new Set(
      reuse.target_selections.flatMap((target) => reuse.fits.map((fit) => `${target}\0${fit}`)),
    ),
    children = next.filter(
      (node) => node.operation === 'reuse_selection' && node.reuse === reuseId,
    ),
    equalities = next.filter(
      (node) => node.operation === 'equal_radii' && node.managed_by === reuseId,
    ),
    groups = new Map();
  for (const child of children) {
    child.group_id = null;
    child.managed_by = reuseId;
    child.managed_key = `selection/${child.target_selection}/${child.fit}/${child.source_selection}`;
    const key = `${child.target_selection}\0${child.fit}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(child);
  }

  const removeIds = new Set(),
    ownedIds = new Set([...children, ...equalities].map((node) => node.id)),
    retained = new Set();
  for (const [key, group] of groups) {
    const [, fitId] = key.split('\0'),
      sourceFit = next.find((node) => node.id === fitId),
      expectedSelections = new Set(sourceFit?.selections || []),
      actualSelections = new Set(group.map((node) => node.source_selection)),
      complete =
        expectedSelections.size === actualSelections.size &&
        [...expectedSelections].every((id) => actualSelections.has(id)),
      childIds = new Set(group.map((node) => node.id)),
      copiedFits = next.filter(
        (node) =>
          node.operation === 'fit' &&
          node.selections.length === childIds.size &&
          node.selections.every((id) => childIds.has(id)),
      ),
      copiedFit = copiedFits[0];
    if (copiedFit) {
      copiedFit.group_id = null;
      copiedFit.managed_by = reuseId;
      copiedFit.managed_key = `fit/${group[0].target_selection}/${fitId}`;
      ownedIds.add(copiedFit.id);
    }
    if (desired.has(key) && complete && copiedFit) {
      retained.add(key);
      continue;
    }
    if (!copiedFit)
      return {
        error: 'The generated outputs were edited, so this reuse action cannot be restructured safely.',
      };
    group.forEach((node) => removeIds.add(node.id));
    removeIds.add(copiedFit.id);
  }
  const desiredEqualityKeys = new Set(
    reuse.equal_corresponding_dimensions
      ? reuse.fits
          .filter((fitId) => next.find((node) => node.id === fitId)?.kind === 'cylinder')
          .map((fitId) => `equal-radius/${fitId}`)
      : [],
  );
  for (const equality of equalities) {
    equality.group_id = null;
    if (!desiredEqualityKeys.has(equality.managed_key)) removeIds.add(equality.id);
  }
  const blocking = next.find(
    (node) =>
      !removeIds.has(node.id) &&
      node.id !== reuseId &&
      node.managed_by !== reuseId &&
      nodeReferences(node).some((id) => removeIds.has(id)),
  );
  if (blocking)
    return { error: `Cannot remove generated outputs used by ${blocking.label}.` };

  let working = next.filter((node) => !removeIds.has(node.id));
  const generated = [];
  for (const targetId of reuse.target_selections) {
    const target = working.find((node) => node.id === targetId);
    for (const fitId of reuse.fits) {
      const key = `${targetId}\0${fitId}`;
      if (retained.has(key)) continue;
      const sourceFit = working.find((node) => node.id === fitId),
        generatedSelections = [];
      for (const sourceSelectionId of sourceFit.selections) {
        const sourceSelection = working.find((node) => node.id === sourceSelectionId),
          selection = {
            id: makeId('reuse_selection'),
            label: uniqueFeatureLabel(
              `${sourceSelection.label} at ${target.label}`,
              [...working, ...generated],
            ),
            operation: 'reuse_selection',
            reuse: reuse.id,
            fit: fitId,
            source_selection: sourceSelectionId,
            target_selection: targetId,
            managed_by: reuse.id,
            managed_key: `selection/${targetId}/${fitId}/${sourceSelectionId}`,
          };
        generated.push(selection);
        generatedSelections.push(selection.id);
      }
      generated.push({
        id: makeId('fit'),
        label: uniqueFeatureLabel(
          `${sourceFit.label} at ${target.label}`,
          [...working, ...generated],
        ),
        operation: 'fit',
        selections: generatedSelections,
        kind: sourceFit.kind,
        axial_domain: [...sourceFit.axial_domain],
        managed_by: reuse.id,
        managed_key: `fit/${targetId}/${fitId}`,
      });
    }
  }
  const availableOutputs = [...working, ...generated],
    managedEqualities = [];
  for (const fitId of reuse.fits) {
    const sourceFit = availableOutputs.find((node) => node.id === fitId);
    if (!reuse.equal_corresponding_dimensions || sourceFit?.kind !== 'cylinder') continue;
    const managedKey = `equal-radius/${fitId}`,
      surfaces = [
        fitId,
        ...reuse.target_selections.map(
          (targetId) =>
            availableOutputs.find(
              (node) =>
                node.operation === 'fit' &&
                node.managed_by === reuseId &&
                node.managed_key === `fit/${targetId}/${fitId}`,
            )?.id,
        ),
      ];
    if (surfaces.some((id) => !id))
      return { error: `Generated fits for ${sourceFit.label} are incomplete.` };
    let equality = equalities.find((node) => node.managed_key === managedKey);
    if (equality) equality.surfaces = surfaces;
    else {
      equality = {
        id: makeId('equal_radii'),
        label: uniqueFeatureLabel(
          `${sourceFit.label} radii all equal`,
          [...availableOutputs, ...managedEqualities],
        ),
        operation: 'equal_radii',
        surfaces,
        managed_by: reuseId,
        managed_key: managedKey,
      };
    }
    managedEqualities.push(equality);
  }
  const blockIds = new Set([reuseId, ...ownedIds]),
    equalityIds = new Set(equalities.map((node) => node.id)),
    block = working.filter((node) => blockIds.has(node.id) && !equalityIds.has(node.id)),
    outside = working.filter((node) => !blockIds.has(node.id)),
    dependencies = new Set(nodeReferences(reuse));
  if ([...dependencies].some((id) => blockIds.has(id)))
    return { error: 'A reuse action cannot use one of its own generated outputs.' };
  const insertionIndex = Math.max(
      -1,
      ...outside.map((node, index) => (dependencies.has(node.id) ? index : -1)),
    ),
    result = [
      ...outside.slice(0, insertionIndex + 1),
      ...block,
      ...generated,
      ...managedEqualities,
      ...outside.slice(insertionIndex + 1),
    ],
    seen = new Set();
  for (const node of result) {
    const missing = nodeReferences(node).find((id) => !seen.has(id));
    if (missing)
      return {
        error: `${node.label} would need to move after ${result.find((item) => item.id === missing)?.label || missing}.`,
      };
    seen.add(node.id);
  }
  return { nodes: result, removedIds: [...removeIds], generatedIds: generated.map((n) => n.id) };
}
// Slot is a boundary in the original list: 0 before the first, length after the last.
export function actionMove(nodes, id, slot) {
  const from = nodes.findIndex((node) => node.id === id);
  if (from < 0 || !Number.isInteger(slot) || slot < 0 || slot > nodes.length)
    return { error: 'This feature is no longer available.' };
  const candidate = [...nodes];
  const [node] = candidate.splice(from, 1);
  candidate.splice(slot > from ? slot - 1 : slot, 0, node);
  const seen = new Set();
  for (const item of candidate) {
    for (const input of nodeReferences(item)) {
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
    move,
    announce,
    editGroup = () => {},
    removeGroup = () => {},
  },
) {
  let dragged = null,
    dropSlot = null;
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
  function clearDrop() {
    dropSlot = null;
    for (const row of list.querySelectorAll('.action-row'))
      row.classList.remove('drop-before', 'drop-after', 'drop-invalid');
  }
  async function commit(id, slot, focusHandle = false) {
    if (unavailable()) {
      announce('Wait for the current edit or evaluation to finish.', true);
      return;
    }
    const candidate = actionMove(nodes, id, slot);
    if (candidate.error) {
      announce(candidate.error, true);
      return;
    }
    if (!candidate.changed) return;
    list.setAttribute('aria-busy', 'true');
    try {
      if (await move(candidate.nodes)) {
        announce(`Moved ${nodes.find((n) => n.id === id).label}.`);
        if (focusHandle) {
          [...list.querySelectorAll('.action-grip')]
            .find((button) => button.dataset.actionId === id)
            ?.focus();
        }
      }
    } finally {
      list.removeAttribute('aria-busy');
    }
  }
  function stateFor(items) {
    for (const state of ['failed', 'running', 'stale', 'unevaluated'])
      if (items.some((item) => states[item.id] === state)) return state;
    return 'ready';
  }
  function actionItem(node, generated = false) {
    const index = nodes.indexOf(node),
      item = document.createElement('li'),
      row = document.createElement('div'),
      owned = managed.get(node.id) || [],
      grip = document.createElement('button');
    item.className = 'action-entry';
    row.className = 'action-row';
    row.dataset.actionIndex = index;
    if (generated) row.dataset.managed = 'true';
    if (generated || owned.length) {
      grip.className = 'action-grip action-grip-placeholder';
      grip.disabled = true;
      grip.tabIndex = -1;
      grip.title = generated
        ? `Managed by ${byId.get(ownerById.get(node.id))?.label || ownerById.get(node.id)}`
        : 'This feature moves with its managed outputs.';
      grip.append(icon(generated ? 'feature_reuse' : 'grip'));
    } else {
      grip.className = 'action-grip';
      grip.dataset.actionId = node.id;
      grip.append(icon('grip'));
      grip.draggable = true;
      grip.title = `Drag to reorder ${node.label}; or focus here and use the arrow keys.`;
      grip.setAttribute('aria-label', `Reorder ${node.label}. Use Up or Down arrow keys.`);
      grip.onpointerdown = () => grip.focus();
      grip.onkeydown = (event) => {
        if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        void commit(
          node.id,
          event.key === 'ArrowUp' ? Math.max(0, index - 1) : Math.min(nodes.length, index + 2),
          true,
        );
      };
      grip.ondragstart = (event) => {
        if (unavailable()) {
          event.preventDefault();
          return;
        }
        dragged = node.id;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', node.id);
        row.classList.add('dragging');
        event.dataTransfer.setDragImage(row, 20, row.clientHeight / 2);
      };
      grip.ondragend = () => {
        dragged = null;
        clearDrop();
        row.classList.remove('dragging');
      };
    }
    const button = document.createElement('button');
    button.className = 'action-select';
    button.dataset.actionId = node.id;
    button.setAttribute('aria-pressed', String(selectedIds.has(node.id)));
    const relationship = ['mirror_symmetry', 'parallel', 'equal'].includes(node.operation),
      description = actionDescription(node, states[node.id], errors[node.id]);
    button.title = `${node.label} · ${description}`;
    button.setAttribute('aria-label', button.title);
    button.onkeydown = (event) => {
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
    if (generated) {
      const badge = document.createElement('span');
      badge.className = 'generated-badge';
      badge.textContent = 'Generated';
      button.append(badge);
    }
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
        edit = document.createElement('button'),
        summaryState = relationship ? 'ready' : states[node.id];
      item.classList.add('managed-owner');
      details.className = 'tree-group managed-owner-group';
      details.open = expandedManaged.has(node.id);
      summary.className = 'managed-owner-summary';
      summary.dataset.actionId = node.id;
      summary.dataset.actionIndex = index;
      summary.title = `${node.label} · ${description}`;
      summary.setAttribute('aria-label', summary.title);
      summary.onkeydown = button.onkeydown;
      label.classList.add('tree-group-name');
      edit.type = 'button';
      edit.className = 'group-action';
      edit.textContent = 'Edit';
      edit.onclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        select(node.id);
      };
      summary.append(
        icon('group', 'group-icon'),
        label,
        edit,
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
        targetDetails.className = 'tree-group managed-group managed-target';
        targetDetails.open = expandedTargets.has(targetKey);
        targetLabel.className = 'tree-group-name';
        targetLabel.textContent = target === '__relationships__'
          ? `Relationships · ${children.length}`
          : target
          ? `${byId.get(target)?.label || target} · ${children.filter((child) => child.operation === 'fit').length} fits`
          : `Other outputs · ${children.length}`;
        targetBadge.className = 'managed-badge';
        targetBadge.textContent = 'Generated';
        targetSummary.append(
          icon('group', 'group-icon'),
          targetLabel,
          targetBadge,
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
      edit = document.createElement('button'),
      remove = document.createElement('button'),
      children = document.createElement('ul');
    item.className = 'feature-group';
    details.className = 'tree-group';
    details.open = !collapsedGroups.has(group.id);
    label.className = 'tree-group-name';
    label.textContent = `${group.label} · ${members.length}`;
    edit.type = remove.type = 'button';
    edit.className = remove.className = 'group-action';
    edit.textContent = 'Edit';
    edit.onclick = (event) => {
      event.preventDefault();
      editGroup(group.id);
    };
    remove.textContent = '×';
    remove.title = `Remove ${group.label} without deleting its features`;
    remove.setAttribute('aria-label', remove.title);
    remove.onclick = (event) => {
      event.preventDefault();
      removeGroup(group.id);
    };
    summary.append(icon('group', 'group-icon'), label, edit, remove);
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
    if (!dragged || unavailable()) return;
    const row = event.target.closest('.action-row');
    if (!row || row.dataset.managed === 'true') return;
    event.preventDefault();
    clearDrop();
    const after = event.clientY > row.getBoundingClientRect().top + row.clientHeight / 2,
      slot = Number(row.dataset.actionIndex) + Number(after),
      candidate = actionMove(nodes, dragged, slot);
    dropSlot = slot;
    row.classList.add(after ? 'drop-after' : 'drop-before');
    row.classList.toggle('drop-invalid', !!candidate.error);
    event.dataTransfer.dropEffect = candidate.error ? 'none' : 'move';
    if (candidate.error) announce(candidate.error, true);
  };
  list.ondragleave = (event) => {
    if (!list.contains(event.relatedTarget)) clearDrop();
  };
  list.ondrop = (event) => {
    event.preventDefault();
    const id = dragged,
      slot = dropSlot;
    clearDrop();
    if (id && slot !== null) void commit(id, slot, true);
  };
}
