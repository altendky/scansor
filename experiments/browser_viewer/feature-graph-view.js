import { actionDescription, managedOwnerId, nodeReferences } from './action-tree.js';

export const relationshipOperations = new Set([
  'coaxial',
  'perpendicular',
  'rotational_symmetry',
  'mirror_symmetry',
  'parallel',
  'equal',
  'equal_radii',
  'plane_relationship',
]);

export const solveOperations = new Set(['axis_solve', 'joint_fit']);

const selectionDetailOperations = new Set([
  'source',
  'selection',
  'growth',
  'selection_region',
  'region_selection',
  'reuse_selection',
]);

function edgeKind(node) {
  if (relationshipOperations.has(node.operation)) return 'relationship';
  if (solveOperations.has(node.operation)) return 'solve';
  return 'dependency';
}

function uniqueEdges(edges) {
  const found = new Set();
  return edges.filter((edge) => {
    const key = `${edge.from}\0${edge.to}\0${edge.kind}`;
    if (found.has(key)) return false;
    found.add(key);
    return true;
  });
}

export function buildFeatureGraph(
  recipe,
  {
    lens = 'combined',
    showSelections = false,
    showGenerated = false,
    selected = new Set(),
    selectedNeighborhood = false,
  } = {},
) {
  const allNodes = recipe.nodes || [],
    byId = new Map(allNodes.map((node) => [node.id, node])),
    generated = new Map(allNodes.map((node) => [node.id, managedOwnerId(node, allNodes)])),
    dependencyEdges = [];

  for (const node of allNodes) {
    for (const reference of nodeReferences(node)) {
      if (!byId.has(reference)) continue;
      dependencyEdges.push({
        from: reference,
        to: node.id,
        kind: edgeKind(node),
      });
    }
  }

  const ownershipEdges = allNodes.flatMap((node) => {
      const owner = generated.get(node.id);
      return owner && byId.has(owner)
        ? [{ from: owner, to: node.id, kind: 'ownership' }]
        : [];
    }),
    allowedByDetail = new Set(
      allNodes
        .filter(
          (node) =>
            selected.has(node.id) ||
            ((showSelections || !selectionDetailOperations.has(node.operation)) &&
              (showGenerated || !generated.get(node.id))),
        )
        .map((node) => node.id),
    );

  let edges;
  if (lens === 'relationships')
    edges = dependencyEdges.filter((edge) => edge.kind === 'relationship');
  else if (lens === 'solves')
    edges = dependencyEdges.filter((edge) => edge.kind === 'solve');
  else if (lens === 'dependencies')
    edges = dependencyEdges.map((edge) => ({ ...edge, kind: 'dependency' }));
  else edges = [...dependencyEdges, ...ownershipEdges];

  edges = uniqueEdges(edges).filter(
    (edge) => allowedByDetail.has(edge.from) && allowedByDetail.has(edge.to),
  );

  let visibleIds = new Set(allowedByDetail);
  if (lens === 'relationships' || lens === 'solves') {
    visibleIds = new Set(edges.flatMap((edge) => [edge.from, edge.to]));
    for (const id of selected) if (allowedByDetail.has(id)) visibleIds.add(id);
  }

  if (selectedNeighborhood && selected.size) {
    const neighborhood = new Set([...selected].filter((id) => visibleIds.has(id)));
    for (const edge of edges) {
      if (selected.has(edge.from)) neighborhood.add(edge.to);
      if (selected.has(edge.to)) neighborhood.add(edge.from);
    }
    visibleIds = neighborhood;
    edges = edges.filter(
      (edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to),
    );
  }

  return {
    nodes: allNodes
      .map((node, index) => ({
        ...node,
        generated: Boolean(generated.get(node.id)),
        owner: generated.get(node.id),
        recipeIndex: index,
      }))
      .filter((node) => visibleIds.has(node.id)),
    edges,
  };
}

export function layoutFeatureGraph(
  graph,
  {
    nodeWidth = 196,
    nodeHeight = 56,
    columnGap = 92,
    rowGap = 28,
    padding = 36,
  } = {},
) {
  const ordered = [...graph.nodes].sort((a, b) => a.recipeIndex - b.recipeIndex),
    ranks = new Map(ordered.map((node) => [node.id, 0]));
  for (const node of ordered) {
    const incoming = graph.edges.filter((edge) => edge.to === node.id && ranks.has(edge.from));
    if (incoming.length)
      ranks.set(node.id, Math.max(...incoming.map((edge) => ranks.get(edge.from) + 1)));
  }
  const columns = new Map();
  for (const node of ordered) {
    const rank = ranks.get(node.id);
    if (!columns.has(rank)) columns.set(rank, []);
    columns.get(rank).push(node);
  }
  const maximumRows = Math.max(1, ...[...columns.values()].map((column) => column.length)),
    contentHeight = maximumRows * nodeHeight + (maximumRows - 1) * rowGap,
    positions = new Map();
  for (const [rank, column] of columns) {
    const columnHeight = column.length * nodeHeight + (column.length - 1) * rowGap,
      offset = (contentHeight - columnHeight) / 2;
    column.forEach((node, row) =>
      positions.set(node.id, {
        x: padding + rank * (nodeWidth + columnGap),
        y: padding + offset + row * (nodeHeight + rowGap),
        width: nodeWidth,
        height: nodeHeight,
      }),
    );
  }
  return {
    positions,
    width: Math.max(
      720,
      padding * 2 + (Math.max(0, ...ranks.values()) + 1) * nodeWidth +
        Math.max(0, ...ranks.values()) * columnGap,
    ),
    height: Math.max(440, padding * 2 + contentHeight),
  };
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function nodeType(node) {
  if (relationshipOperations.has(node.operation)) return 'relationship';
  if (solveOperations.has(node.operation)) return 'solve';
  if (['point', 'axis', 'reference_plane', 'frame'].includes(node.operation)) return 'datum';
  if (node.operation === 'fit') return 'fit';
  if (node.operation === 'feature_reuse') return 'reuse';
  if (selectionDetailOperations.has(node.operation)) return 'selection';
  return 'operation';
}

function nodeKindLabel(node) {
  if (node.operation === 'fit') return `${node.kind} fit`;
  if (node.operation === 'axis') return 'reference axis';
  if (node.operation === 'point') return 'reference point';
  if (node.operation === 'frame') return 'coordinate frame';
  if (node.operation === 'scale') return 'output scale';
  if (node.operation === 'transform') return 'output transform';
  if (node.operation === 'reference_plane') return 'reference plane';
  if (node.operation === 'axis_solve') return 'joint';
  if (node.operation === 'joint_fit') return 'legacy joint';
  return node.operation.replaceAll('_', ' ');
}

function clippedLabel(value, maximum = 27) {
  return value.length > maximum ? `${value.slice(0, maximum - 1)}…` : value;
}

function edgeDescription(edge, byId) {
  const from = byId.get(edge.from)?.label || edge.from,
    to = byId.get(edge.to)?.label || edge.to;
  if (edge.kind === 'relationship') return `${from} participates in ${to}`;
  if (edge.kind === 'solve') return `${to} activates ${from}`;
  if (edge.kind === 'ownership') return `${from} generates and manages ${to}`;
  return `${to} depends on ${from}`;
}

export function renderFeatureGraph(
  container,
  {
    recipe,
    states = {},
    errors = {},
    selected = new Set(),
    options = {},
    onSelect,
  },
) {
  const previousLeft = container.scrollLeft,
    previousTop = container.scrollTop,
    graph = buildFeatureGraph(recipe, { ...options, selected }),
    layout = layoutFeatureGraph(graph),
    byId = new Map(graph.nodes.map((node) => [node.id, node]));
  container.replaceChildren();
  if (!graph.nodes.length) {
    const empty = document.createElement('p');
    empty.className = 'feature-graph-empty';
    empty.textContent = 'No features match this graph lens and its current filters.';
    container.append(empty);
    return graph;
  }

  const svg = svgElement('svg', {
      class: 'feature-graph-svg',
      role: 'group',
      'aria-label': `${graph.nodes.length} feature nodes and ${graph.edges.length} connections`,
      viewBox: `0 0 ${layout.width} ${layout.height}`,
      width: layout.width,
      height: layout.height,
    }),
    definitions = svgElement('defs'),
    marker = svgElement('marker', {
      id: 'feature-graph-arrow',
      markerWidth: 8,
      markerHeight: 8,
      refX: 7,
      refY: 4,
      orient: 'auto',
      markerUnits: 'strokeWidth',
    });
  marker.append(svgElement('path', { d: 'M0,0 L8,4 L0,8 Z' }));
  definitions.append(marker);
  svg.append(definitions);

  const edgeLayer = svgElement('g', { class: 'feature-graph-edges' });
  for (const edge of graph.edges) {
    const source = layout.positions.get(edge.from),
      target = layout.positions.get(edge.to);
    if (!source || !target) continue;
    const startX = source.x + source.width,
      startY = source.y + source.height / 2,
      endX = target.x,
      endY = target.y + target.height / 2,
      bend = Math.max(36, (endX - startX) / 2),
      path = svgElement('path', {
        class: `feature-graph-edge edge-${edge.kind}`,
        d: `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`,
      });
    if (edge.kind === 'dependency' || edge.kind === 'solve')
      path.setAttribute('marker-end', 'url(#feature-graph-arrow)');
    const title = svgElement('title');
    title.textContent = edgeDescription(edge, byId);
    path.append(title);
    edgeLayer.append(path);
  }
  svg.append(edgeLayer);

  const nodeLayer = svgElement('g', { class: 'feature-graph-nodes' });
  for (const node of graph.nodes) {
    const position = layout.positions.get(node.id),
      type = nodeType(node),
      group = svgElement('g', {
        class: `feature-graph-node graph-node-${type}${selected.has(node.id) ? ' selected' : ''}${node.generated ? ' generated' : ''}`,
        role: 'button',
        tabindex: 0,
        'aria-pressed': String(selected.has(node.id)),
        'aria-label': `${node.label}, ${actionDescription(node, states[node.id], errors[node.id])}`,
        transform: `translate(${position.x} ${position.y})`,
      }),
      rectangle = svgElement('rect', {
        width: position.width,
        height: position.height,
        rx: type === 'relationship' ? 18 : 7,
      }),
      label = svgElement('text', { x: 15, y: 23, class: 'graph-node-label' }),
      detail = svgElement('text', { x: 15, y: 43, class: 'graph-node-detail' }),
      state = svgElement('circle', {
        cx: position.width - 15,
        cy: 16,
        r: 5,
        class: `graph-node-state state-${states[node.id] || 'unevaluated'}`,
      }),
      title = svgElement('title');
    label.textContent = clippedLabel(node.label);
    detail.textContent = `${nodeKindLabel(node)}${node.generated ? ' · generated' : ''}`;
    title.textContent = `${node.label}\n${actionDescription(node, states[node.id], errors[node.id])}`;
    group.append(rectangle, label, detail, state, title);
    group.onclick = (event) => {
      event.stopPropagation();
      onSelect(node.id);
    };
    group.onkeydown = (event) => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      onSelect(node.id);
    };
    nodeLayer.append(group);
  }
  svg.append(nodeLayer);
  svg.onclick = (event) => {
    if (event.target === svg) onSelect(null, { clear: true });
  };
  container.append(svg);
  container.scrollLeft = previousLeft;
  container.scrollTop = previousTop;
  return graph;
}
