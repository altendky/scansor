export const TOOLBAR_STORAGE_KEY = 'scansor.toolbars.v1';
export const TOOLBAR_IDS = ['project', 'create', 'faces', 'features', 'output'];
export const EDGES = ['top', 'right', 'bottom', 'left'];
export const DEFAULT_PLACEMENT = { host: 'workspace', edge: 'top', orientation: 'horizontal', x: 40, y: 110 };
export const defaultPlacements = () => Object.fromEntries(TOOLBAR_IDS.map((id, index) =>
  [id, { ...DEFAULT_PLACEMENT, order: index, x: 40 + index * 30, y: 110 + index * 40 }]));

export function toolbarOrder(placements, host, edge) {
  return TOOLBAR_IDS.filter(id => placements[id]?.host === host && placements[id]?.edge === edge)
    .sort((a, b) => (placements[a].order ?? TOOLBAR_IDS.indexOf(a)) -
      (placements[b].order ?? TOOLBAR_IDS.indexOf(b)) || TOOLBAR_IDS.indexOf(a) - TOOLBAR_IDS.indexOf(b));
}

// Rank only the destination group; gaps in the old group are harmless.
export function insertToolbar(placements, id, placement, before = null) {
  const next = { ...placements, [id]: placement };
  if (!EDGES.includes(placement.edge)) return next;
  const peers = toolbarOrder(placements, placement.host, placement.edge).filter(peer => peer !== id);
  const index = before === null || !peers.includes(before) ? peers.length : peers.indexOf(before);
  peers.splice(index, 0, id);
  peers.forEach((peer, order) => { next[peer] = { ...next[peer], order }; });
  return next;
}

// Choose the nearest packed lane, then before/after a peer's midpoint.
// Peer rectangles stay committed during a drag, so the target cannot chase it.
export function insertionBefore(placements, id, target, point, rects) {
  const peers = toolbarOrder(placements, target.host, target.edge).filter(peer => peer !== id && rects[peer]);
  if (!peers.length) return null;
  const vertical = ['left', 'right'].includes(target.edge);
  const cross = vertical ? 'x' : 'y', axis = vertical ? 'y' : 'x';
  const depth = vertical ? 'width' : 'height', extent = vertical ? 'height' : 'width';
  const distance = peer => {
    const rect = rects[peer];
    return Math.max(rect[cross] - point[cross], point[cross] - rect[cross] - rect[depth], 0);
  };
  const nearest = peers.reduce((best, peer) => distance(peer) < distance(best) ? peer : best);
  const lane = peers.filter(peer => Math.abs(rects[peer][cross] - rects[nearest][cross]) < 1);
  const before = lane.find(peer => point[axis] < rects[peer][axis] + rects[peer][extent] / 2);
  return before || peers[peers.indexOf(lane.at(-1)) + 1] || null;
}

export const containsPoint = (rect, point) => point.x >= rect.x && point.y >= rect.y &&
  point.x < rect.x + rect.width && point.y < rect.y + rect.height;

// Share the geometry between painted targets and hit-testing. The workspace's
// outer gutter/occupied rails are never local targets, even at a shared edge.
export function toolbarDropZones(workspace, hosts, arrangements) {
  const depths = Object.fromEntries(EDGES.map(edge => [edge, Math.min(
    Math.max(12, arrangements.workspace.insets[edge]),
    (['left', 'right'].includes(edge) ? workspace.width : workspace.height) / 2)]));
  const zones = [], corners = [];
  const add = (host, bounds, insets, minimum) => {
    const depths = Object.fromEntries(EDGES.map(edge => [edge, Math.min(Math.max(minimum, insets[edge]),
      (['left', 'right'].includes(edge) ? bounds.width : bounds.height) / 2)]));
    for (const edge of EDGES) {
      const vertical = ['left', 'right'].includes(edge);
      const depth = depths[edge];
      const rect = vertical ? {
        x: bounds.x + (edge === 'right' ? bounds.width - depth : 0), y: bounds.y + depths.top,
        width: depth, height: bounds.height - depths.top - depths.bottom,
      } : { x: bounds.x + depths.left, y: bounds.y + (edge === 'bottom' ? bounds.height - depth : 0),
        width: bounds.width - depths.left - depths.right, height: depth };
      if (rect.width > 0 && rect.height > 0) zones.push({ host: host.id, name: host.name || 'Workspace', edge, rect, zIndex: host.zIndex });
    }
    for (const horizontal of ['top', 'bottom']) for (const vertical of ['left', 'right']) {
      const rect = { x: bounds.x + (vertical === 'right' ? bounds.width - depths.right : 0),
        y: bounds.y + (horizontal === 'bottom' ? bounds.height - depths.bottom : 0),
        width: depths[vertical], height: depths[horizontal] };
      if (rect.width > 0 && rect.height > 0) corners.push({ host: host.id, rect });
    }
  };
  add(workspace, workspace, depths, 12);
  // Clip local targets to the inner workspace, not just their own tabset.
  for (const host of hosts) {
    const x = Math.max(host.x, depths.left), y = Math.max(host.y, depths.top);
    const width = Math.max(0, Math.min(host.x + host.width, workspace.width - depths.right) - x);
    const height = Math.max(0, Math.min(host.y + host.height, workspace.height - depths.bottom) - y);
    add(host, { x, y, width, height }, arrangements[host.id].insets, 24);
  }
  return { zones, corners };
}

export function toolbarSize(placement, size) {
  const orientation = toolbarOrientation(placement);
  if (size && (!size.orientation || size.orientation === orientation)) return size;
  return orientation === 'vertical' ? { width: 38, height: 74 } : { width: 74, height: 38 };
}

// Pack independent strips into lanes. Horizontal edges own the corners;
// vertical edges use the remaining height. Input sizes are preferred (unclipped).
export function arrangeToolbars(placements, sizes, bounds) {
  const result = {}, insets = { top: 0, right: 0, bottom: 0, left: 0 };
  for (const edge of ['top', 'bottom', 'left', 'right']) {
    const vertical = ['left', 'right'].includes(edge);
    const length = Math.max(0, vertical ? bounds.height - insets.top - insets.bottom : bounds.width);
    let offset = 0, lane = 0, thickness = 0;
    const group = TOOLBAR_IDS.find(id => placements[id]?.edge === edge);
    for (const id of toolbarOrder(placements, placements[group]?.host, edge)) {
      const size = toolbarSize(placements[id], sizes[id]);
      const extent = Math.min(length, vertical ? size.height : size.width);
      const depth = vertical ? size.width : size.height;
      if (offset && offset + extent > length) { lane += thickness; offset = 0; thickness = 0; }
      result[id] = vertical ? {
        left: edge === 'left' ? lane : bounds.width - lane - depth,
        top: insets.top + offset, maxHeight: length, maxWidth: bounds.width,
      } : {
        left: offset, top: edge === 'top' ? lane : bounds.height - lane - depth,
        maxWidth: length, maxHeight: bounds.height,
      };
      offset += extent;
      thickness = Math.max(thickness, depth);
    }
    insets[edge] = lane + thickness;
  }
  return { positions: result, insets };
}

export function validPlacement(value) {
  return value && [...EDGES, 'float'].includes(value.edge) &&
    (value.host === undefined || typeof value.host === 'string') &&
    (value.order === undefined || Number.isFinite(value.order)) &&
    ['horizontal', 'vertical'].includes(value.orientation) &&
    Number.isFinite(value.x) && Number.isFinite(value.y);
}

export function tabsetOf(node) {
  for (let parent = node?.getParent(); parent; parent = parent.getParent()) {
    if (parent.getType() === 'tabset') return parent;
  }
  return null;
}

export function toolbarOrientation(placement) {
  return placement.edge === 'float' ? placement.orientation :
    ['left', 'right'].includes(placement.edge) ? 'vertical' : 'horizontal';
}

export function clampToolbar(placement, area, size) {
  return { ...placement,
    x: Math.max(0, Math.min(placement.x, Math.max(0, area.width - size.width))),
    y: Math.max(0, Math.min(placement.y, Math.max(0, area.height - size.height))),
  };
}

// Edge policy belongs to this small shell, not the panel docking manager.
export function edgeAt(point, area, threshold = 32) {
  if (point.x < 0 || point.y < 0 || point.x > area.width || point.y > area.height) return null;
  const distances = [['top', point.y], ['right', area.width - point.x],
    ['bottom', area.height - point.y], ['left', point.x]];
  const [edge, distance] = distances.sort((a, b) => a[1] - b[1])[0];
  return distance <= threshold ? edge : null;
}
