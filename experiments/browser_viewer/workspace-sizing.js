import { Actions, Model, Orientation } from 'flexlayout-react';

// Keep the panels beside Model at their chosen sizes when tabs disappear or
// the window resizes, while allowing explicit splitter and docking changes.
export function preserveModelSpace(model, element = null) {
  const pending = new Map();
  let preferred = null, correcting = false;
  const removals = new Set([Actions.DELETE_TAB, Actions.DELETE_TABSET,
    Actions.POPOUT_TAB, Actions.POPOUT_TABSET, Actions.MOVE_NODE]);
  const resizing = new Set([...removals, Actions.ADD_TAB, Actions.ADJUST_WEIGHTS,
    Actions.MAXIMIZE_TOGGLE, Actions.DOCK_FLOAT_TO_LAYOUT]);
  const capture = () => {
    if (model.getMaximizedTabset() ||
        model.getNodeById('view')?.getLayoutId() !== Model.MAIN_LAYOUT_ID) return;
    const sizes = new Map(), tabsets = new Set();
    model.visitLayoutNodes(Model.MAIN_LAYOUT_ID, node => {
      if (!['row', 'tabset'].includes(node.getType())) return;
      const { width, height } = node.getRect();
      sizes.set(node.getId(), { width, height });
      if (node.getType() === 'tabset') tabsets.add(node.getId());
    });
    const root = model.getRootRow(), measured = sizes.get(root.getId()).width > 0 && sizes.get(root.getId()).height > 0;
    const splitter = measured ? model.getSplitterSize() : 0;
    if (!measured) {
      // Saved drafts are omitted before the first render. Their weights still
      // describe the previous proportions, so use those without pixel gaps.
      const proportion = (node, size) => {
        sizes.set(node.getId(), size);
        if (node.getType() !== 'row') return;
        const axis = node.getOrientation() === Orientation.HORZ ? 'width' : 'height';
        const total = node.getChildren().reduce((sum, child) => sum + child.getWeight(), 0);
        for (const child of node.getChildren()) proportion(child,
          { ...size, [axis]: size[axis] * child.getWeight() / total });
      };
      proportion(root, { width: 1, height: 1 });
    }
    return { sizes, tabsets, splitter };
  };
  const redistribute = (before, bounds = before.sizes.get(model.getRootRow().getId())) => {
    const path = [];
    for (let node = model.getNodeById('view').getParent(); node; node = node.getParent()) path.unshift(node);
    let size = bounds && { width: bounds.width, height: bounds.height };
    if (!size || size.width <= 0 || size.height <= 0) return;
    for (let index = 0; index < path.length - 1; index++) {
      const row = path[index], branch = path[index + 1], children = row.getChildren();
      if (row.getType() !== 'row') break;
      const axis = row.getOrientation() === Orientation.HORZ ? 'width' : 'height';
      const weights = children.map(child => child === branch ? 0 : before.sizes.get(child.getId())?.[axis]);
      if (weights.some(weight => !Number.isFinite(weight) || weight < 0)) break;
      const available = size[axis] - (children.length - 1) * before.splitter;
      const remaining = available - weights.reduce((total, weight) => total + weight, 0);
      if (!Number.isFinite(remaining) || remaining <= 0) break;
      weights[children.indexOf(branch)] = remaining;
      if (children.length > 1) {
        correcting = true;
        try { model.doAction(Actions.adjustWeights(row.getId(), weights)); }
        finally { correcting = false; }
      }
      // tidy() may have promoted children or removed ancestor rows. Carry the
      // planned extent down the new path; getRect() still describes the old one.
      size = { ...size, [axis]: remaining };
    }
  };
  const listener = {
    onBeforeAction(action) {
      if (!correcting && resizing.has(action.type)) preferred = null;
      if (!removals.has(action.type)) return;
      const before = capture();
      if (before) pending.set(action, before);
    },
    onAfterAction(action) {
      const before = pending.get(action);
      pending.delete(action);
      if (!before || model.getNodeById('view')?.getLayoutId() !== Model.MAIN_LAYOUT_ID) return;
      const tabsets = new Set();
      model.visitLayoutNodes(Model.MAIN_LAYOUT_ID, node => {
        if (node.getType() === 'tabset') tabsets.add(node.getId());
      });
      // Moving into another tabset can free space too, but docking a new split
      // should continue to use the sizes chosen by the docking operation.
      if (![...before.tabsets].some(id => !tabsets.has(id)) ||
          [...tabsets].some(id => !before.tabsets.has(id))) return;
      redistribute(before);
    },
  };
  const win = element?.ownerDocument.defaultView;
  const resize = () => {
    // The resize event precedes layout measurement, so model rectangles still
    // hold the user's old sizes while the container has its new dimensions.
    const current = capture(), bounds = element.getBoundingClientRect();
    if (!current) return;
    const previous = current.sizes.get(model.getRootRow().getId());
    if (previous.width > 1 && previous.height > 1 &&
        (Math.abs(previous.width - bounds.width) >= 1 || Math.abs(previous.height - bounds.height) >= 1)) {
      // A very small window may compress panels. Retain their preferred sizes
      // so expanding it restores those sizes rather than retaining the squeeze.
      preferred ||= current;
      redistribute(preferred, bounds);
    }
  };
  model.addChangeListener(listener);
  win?.addEventListener('resize', resize, true);
  return () => {
    model.removeChangeListener(listener);
    win?.removeEventListener('resize', resize, true);
  };
}
