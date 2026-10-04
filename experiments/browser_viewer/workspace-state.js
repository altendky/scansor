export const WORKSPACE_STORAGE_KEY = 'scansor.flexlayout.workspace.v1';
export const PANEL_NAMES = {
  tree: 'Features', editor: 'Feature editor', view: 'Model', graph: 'Graph',
  create: 'Create', faces: 'Faces', features: 'Feature tools', output: 'Run / output',
  'build-faces-dialog': 'Build faces', 'relationship-dialog': 'Relationships',
};
export const DIALOG_PANELS = ['build-faces-dialog', 'relationship-dialog'];
export const PERMANENT_PANELS = Object.keys(PANEL_NAMES).filter(id => !DIALOG_PANELS.includes(id));
export const panelTab = id => ({
  type: 'tab', id, name: PANEL_NAMES[id], component: id,
  enableClose: DIALOG_PANELS.includes(id), enablePopout: false,
});

export function initialWorkspace(openDialogs = []) {
  const tabset = (id, weight, extra = {}) => ({
    type: 'tabset', weight, children: [panelTab(id)], ...extra,
  });
  return {
    global: {
      rootOrientationVertical: true, splitterSize: 5,
      tabEnableClose: false, tabEnablePopout: false,
      tabEnableFloat: true, tabEnableFloatIcon: true,
      tabEnableRenderOnDemand: false,
      tabSetMinWidth: 120, tabSetMinHeight: 50,
    },
    borders: [],
    layout: { type: 'row', children: [
      { type: 'row', weight: 12, height: 88, children: [
        tabset('create', 28), tabset('faces', 23), tabset('features', 29), tabset('output', 20),
      ] },
      { type: 'row', weight: 88, children: [
        { type: 'row', weight: 24, width: 310, children: [tabset('tree', 48), tabset('editor', 52)] },
        tabset('view', 76, { children: [panelTab('view'), panelTab('graph')] }),
        ...openDialogs.map(id => tabset(id, 28, { width: 350 })),
      ] },
    ] },
  };
}

// Camera framing must account for *actual* overlap, not assume every tool is
// a fixed overlay at the canvas's right edge. Docked tools do not obscure it.
export function unobscuredViewport(canvas, panel) {
  // DOMRect coordinates are inherited getters, not enumerable own properties.
  const bounds = { left: canvas.left, right: canvas.right, top: canvas.top, bottom: canvas.bottom,
    width: canvas.width, height: canvas.height };
  const left = Math.max(canvas.left, panel.left), right = Math.min(canvas.right, panel.right),
    top = Math.max(canvas.top, panel.top), bottom = Math.min(canvas.bottom, panel.bottom);
  if (right <= left || bottom <= top) return bounds;
  const candidates = [
    { left: canvas.left, right: left, top: canvas.top, bottom: canvas.bottom },
    { left: right, right: canvas.right, top: canvas.top, bottom: canvas.bottom },
    { left: canvas.left, right: canvas.right, top: canvas.top, bottom: top },
    { left: canvas.left, right: canvas.right, top: bottom, bottom: canvas.bottom },
  ].map(rect => ({ ...rect, width: rect.right - rect.left, height: rect.bottom - rect.top }));
  const best = candidates.sort((a, b) => b.width * b.height - a.width * a.height)[0];
  return best.width > 0 && best.height > 0 ? best : bounds;
}
