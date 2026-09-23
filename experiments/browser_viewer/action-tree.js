// Small, locally drawn SVG symbols; no icon font or network assets required.
const paths = {
  source: 'M12 2 3 7v10l9 5 9-5V7Zm0 10L3 7m9 5 9-5m-9 5v10',
  selection: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M9 9h6v6H9Z',
  plane: 'm3 15 6-9 12 3-6 9Z',
  cylinder: 'M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6',
  cone: 'M12 3 3 18c0 4 18 4 18 0L12 3M3 18c0-4 18-4 18 0',
  axis: 'M3 12h18m-4-4 4 4-4 4M7 8l-4 4 4 4',
  reference_plane: 'm3 15 6-9 12 3-6 9ZM12 3v18',
  axis_solve: 'M3 12h18M7 7l-4 5 4 5m10-10 4 5-4 5M12 3v18',
  growth: 'M12 3v18M3 12h18m-12-6 3-3 3 3m-9 3-3 3 3 3m3 3 3 3 3-3m3-9 3 3-3 3',
  selection_region: 'M4 6c0-3 16-3 16 0v12c0 3-16 3-16 0Zm0 0c0 3 16 3 16 0m-8-3v18',
  region_selection: 'M4 7h10M9 3l5 4-5 4m11 2v7H4v-7',
  feature_reuse: 'M5 7h11M12 3l4 4-4 4m7 6H8m4-4-4 4 4 4',
  reuse_selection: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 12h8m-3-3 3 3-3 3',
  coaxial: 'M12 2v20M5 7c0-4 14-4 14 0s-14 4-14 0Zm0 10c0-4 14-4 14 0s-14 4-14 0Z',
  perpendicular: 'M6 3v15h15M6 13h5v5',
  rotational_symmetry: 'M20 8a9 9 0 1 0 1 7M20 3v5h-5M12 8v4l3 2',
  mirror_symmetry: 'M12 2v20M4 7l6 5-6 5m16-10-6 5 6 5',
  parallel: 'M4 8h16M4 16h16',
  equal: 'M5 9h14M5 15h14',
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
      ...(node.reference_plane ? [node.reference_plane] : []),
    ];
  if (node.operation === 'axis') return node.source_fit ? [node.source_fit] : [];
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
      node.target_selection,
    ])];
  if (node.operation === 'reuse_selection')
    return [node.reuse, node.fit, node.source_selection];
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
  return node.constraints || [];
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
  { nodes, selected, states, errors, locked, select, move, announce },
) {
  let dragged = null,
    dropSlot = null;
  const unavailable = () => list.getAttribute('aria-busy') === 'true' || locked();
  function clearDrop() {
    dropSlot = null;
    for (const row of list.children)
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
  list.replaceChildren(
    ...nodes.map((node, index) => {
      const row = document.createElement('li');
      const grip = document.createElement('button');
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
      const button = document.createElement('button');
      button.className = 'action-select';
      button.dataset.actionId = node.id;
      button.setAttribute('aria-pressed', String(selected === node.id));
      const relationship = ['mirror_symmetry', 'parallel', 'equal'].includes(node.operation);
      const description = actionDescription(node, states[node.id], errors[node.id]);
      button.title = `${node.label} · ${description}`;
      button.setAttribute('aria-label', button.title);
      button.onkeydown = (event) => {
        if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const adjacent = nodes[index + (event.key === 'ArrowUp' ? -1 : 1)];
        if (!adjacent) return;
        select(adjacent.id);
        [...list.querySelectorAll('.action-select')]
          .find((item) => item.dataset.actionId === adjacent.id)
          ?.focus();
      };
      const label = document.createElement('span');
      label.className = 'action-name';
      label.textContent = node.label;
      button.append(
        icon(node.operation === 'fit' ? node.kind : node.operation, 'action-type'),
        label,
        icon(relationship ? 'ready' : states[node.id], `action-state state-${relationship ? 'ready' : states[node.id]}`),
      );
      button.onclick = () => {
        select(node.id);
        [...list.querySelectorAll('.action-select')]
          .find((item) => item.dataset.actionId === node.id)
          ?.focus();
      };
      row.append(grip, button);
      return row;
    }),
  );
  list.ondragover = (event) => {
    if (!dragged || unavailable()) return;
    const row = event.target.closest('li');
    if (!row || row.parentElement !== list) return;
    event.preventDefault();
    clearDrop();
    const after = event.clientY > row.getBoundingClientRect().top + row.clientHeight / 2;
    const slot = [...list.children].indexOf(row) + Number(after);
    const candidate = actionMove(nodes, dragged, slot);
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
