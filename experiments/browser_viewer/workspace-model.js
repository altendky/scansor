import { Actions, Model, Orientation } from 'flexlayout-react';

// Scansor chooses which branches retain sizes; FlexLayout handles allocation,
// splitter changes and constrained resizing through its native preferences.
export function createWorkspaceModel(json, bounds = null) {
  const model = Model.fromJson(json), savedSizes = new Map();
  if (bounds?.width > 0 && bounds?.height > 0) {
    // Convert older weight-only saves before omitted drafts release their space.
    const measure = (node, size) => {
      savedSizes.set(node.getId(), size);
      if (node.getType() !== 'row') return;
      const axis = node.getOrientation() === Orientation.HORZ ? 'width' : 'height';
      const children = node.getChildren(), total = children.reduce((sum, child) => sum + child.getWeight(), 0);
      const available = Math.max(0, size[axis] - (children.length - 1) * model.getSplitterSize());
      for (const child of children) measure(child, { ...size,
        [axis]: available * (total > 0 ? child.getWeight() / total : 1 / children.length) });
    };
    measure(model.getRootRow(), { width: bounds.width, height: bounds.height });
  }
  let updating = false;
  const preferences = () => {
    const view = model.getNodeById('view');
    if (updating || view?.getLayoutId() !== Model.MAIN_LAYOUT_ID) return;
    const actions = [];
    for (let branch = view.getParent(), row = branch.getParent(); row; branch = row, row = row.getParent()) {
      // A cross-axis preference can also make an ancestor derive a fixed size.
      if (branch.getPreferredWidth() !== undefined || branch.getPreferredHeight() !== undefined) {
        actions.push(Actions.updateNodeAttributes(branch.getId(), {
          preferredWidth: undefined, preferredHeight: undefined,
        }));
      }
      const horizontal = row.getOrientation() === Orientation.HORZ;
      const attribute = horizontal ? 'preferredWidth' : 'preferredHeight';
      const axis = horizontal ? 'width' : 'height';
      for (const child of row.getChildren()) {
        if (child === branch) continue;
        const current = horizontal ? child.getPreferredWidth() : child.getPreferredHeight();
        if (current === undefined) {
          const measured = savedSizes.get(child.getId())?.[axis] ?? child.getRect()[axis];
          const size = measured > 0 ? measured : horizontal ? 350 : 250;
          actions.push(Actions.updateNodeAttributes(child.getId(), { [attribute]: size }));
        }
      }
    }
    if (!actions.length) return;
    updating = true;
    try { model.doAction(Actions.group(actions)); }
    finally { updating = false; }
  };
  preferences();
  savedSizes.clear();
  model.addChangeListener({ onAfterAction: preferences });
  return model;
}
