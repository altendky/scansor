import React, { useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { Actions, DockLocation, Layout, Model } from 'flexlayout-react';
import 'flexlayout-react/style/dark.css';
import './workspace.css';
import { ToolbarHostMarker, ToolbarWorkspace, useToolbarHost } from './toolbar-prototype.jsx';
import { DIALOG_PANELS, PANEL_NAMES, PERMANENT_PANELS, WORKSPACE_STORAGE_KEY,
  initialWorkspace, panelTab } from './workspace-state.js';
import { TOOLBAR_IDS } from './toolbar-state.js';

let controller;
const byId = id => document.getElementById(id);

export function workspaceEditingPanels() {
  return [byId('features-panel'), byId('feature-properties-panel'), ...workspaceToolbars()];
}

export function workspaceToolbars() {
  return [byId('project-toolbar'), ...document.querySelectorAll('.workspace-toolbar')];
}

export function revealWorkspacePanel(id) {
  flushSync(() => controller.select(id));
}

export function requestWorkspaceClose(id) {
  const dialog = byId(id);
  if (!dialog.open) return true;
  if (!dialog.dispatchEvent(new Event('workspace-before-close', { cancelable: true }))) return false;
  dialog.close();
  return true;
}

function NativePanel({ element, parking, node }) {
  const host = useRef(null);
  useToolbarHost(node, host);
  useLayoutEffect(() => {
    const container = host.current;
    container.append(element);
    node.setEventListener('visibility', ({ visible }) => {
      element.dispatchEvent(new CustomEvent('workspace-panel-visibility', { detail: { visible } }));
    });
    return () => {
      node.removeEventListener('visibility');
      if (element.parentElement === container) parking.append(element);
    };
  }, [element, parking, node]);
  return <div className="workspace-native-host" ref={host} />;
}

const storageKey = WORKSPACE_STORAGE_KEY;
const defaultWorkspace = initialWorkspace;

function loadModel() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved?.version !== 1) return Model.fromJson(defaultWorkspace());
    const model = Model.fromJson(saved.layout), view = model.getNodeById('view'), ids = [];
    // Preserve the user's panels while retiring the old toolbar tabsets.
    for (const id of TOOLBAR_IDS) if (model.getNodeById(id)) model.doAction(Actions.deleteTab(id));
    // Split the old combined view without discarding the user's panel placement.
    if (view && !model.getNodeById('graph')) {
      model.doAction(Actions.addTab(panelTab('graph'), view.getParent().getId(), DockLocation.CENTER, -1, false));
    }
    if (view) model.doAction(Actions.updateNodeAttributes('view', { name: PANEL_NAMES.view }));
    model.visitNodes(node => { if (node.getType() === 'tab') ids.push(node.getId()); });
    if (ids.some(id => !Object.hasOwn(PANEL_NAMES, id)) || new Set(ids).size !== ids.length ||
        PERMANENT_PANELS.some(id => !ids.includes(id))) throw new Error('Unknown workspace panels');
    // Layout preferences are not permission to reopen a face/relationship draft.
    for (const id of DIALOG_PANELS) if (model.getNodeById(id)) model.doAction(Actions.deleteTab(id));
    return model;
  } catch { return Model.fromJson(defaultWorkspace()); }
}

function Workspace({ elements, parking }) {
  const [model, setModel] = useState(loadModel);
  const [storageError, setStorageError] = useState('');
  const [ready, setReady] = useState(false);
  const [toolbarReset, setToolbarReset] = useState(0);
  const modelRef = useRef(model);
  modelRef.current = model;
  const save = next => {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: 1, layout: next.toJson() }));
      setStorageError('');
    } catch { setStorageError('Layout cannot be saved in this browser.'); }
  };
  useLayoutEffect(() => {
    const sync = id => {
      const next = modelRef.current, dialog = elements[id], node = next.getNodeById(id);
      if (dialog.open && !node) {
        const target = next.getNodeById('view').getParent();
        next.doAction(Actions.addTab(panelTab(id), target.getId(), DockLocation.RIGHT, -1, true));
        requestAnimationFrame(() => {
          if (dialog.open) dialog.querySelector('button, select, input')?.focus({ preventScroll: true });
        });
      } else if (!dialog.open && node) next.doAction(Actions.deleteTab(id));
    };
    const observers = DIALOG_PANELS.map(id => {
      const observer = new MutationObserver(() => sync(id));
      observer.observe(elements[id], { attributes: true, attributeFilter: ['open'] });
      return observer;
    });
    controller = {
      ready() { setReady(true); },
      select(id) {
        if (TOOLBAR_IDS.includes(id)) {
          byId('workspace-root').dispatchEvent(new CustomEvent('toolbar-reveal', { detail: id }));
          return;
        }
        const next = modelRef.current;
        if (DIALOG_PANELS.includes(id) && !elements[id].open) {
          // Use the actual launch command to initialize the existing workflow.
          byId(id === 'build-faces-dialog' ? 'new-build-faces' : 'new-relationship').click();
        }
        syncIfDialog(id);
        const node = next.getNodeById(id);
        if (node) {
          const layoutId = node.getLayoutId(), maximized = next.getMaximizedTabset(layoutId);
          if (maximized && maximized !== node.getParent()) next.doAction(Actions.maximizeToggle(maximized.getId(), layoutId));
          if (layoutId !== Model.MAIN_LAYOUT_ID) next.doAction(Actions.movePopoutToFront(layoutId));
          next.doAction(Actions.selectTab(id));
        }
      },
      reset() {
        const next = Model.fromJson(defaultWorkspace(DIALOG_PANELS.filter(id => elements[id].open)));
        setToolbarReset(value => value + 1);
        setModel(next);
        save(next);
      },
    };
    function syncIfDialog(id) { if (DIALOG_PANELS.includes(id)) sync(id); }
    return () => { observers.forEach(observer => observer.disconnect()); controller = null; };
  }, [elements, parking]);
  const layout = <>
    <div className="workspace-layout-notice" role="status" hidden={!storageError}>{storageError}</div>
    <Layout model={model} factory={node => <NativePanel element={elements[node.getId()]} parking={parking} node={node} />}
      onRenderTabSet={(node, values) => {
        if (node.getType() === 'tabset') values.leading = <ToolbarHostMarker node={node} />;
      }}
      onModelChange={save}
      onAction={action => {
        if (action.type === Actions.DELETE_TAB && DIALOG_PANELS.includes(action.data.node)) {
          requestWorkspaceClose(action.data.node);
          return undefined; // Attribute observer removes the tab after native close.
        }
        // Deleting an entire tabset would bypass the workflow close guard.
        if (action.type === Actions.DELETE_TABSET) return undefined;
        return action;
      }} />
  </>;
  return <ToolbarWorkspace originals={elements} ready={ready} reset={toolbarReset}
    model={model} persistLayout={() => save(model)} revealPanel={id => controller.select(id)}>
    {layout}
  </ToolbarWorkspace>;
}

export function initializeWorkspace() {
  const parking = document.createElement('div');
  parking.id = 'workspace-parking';
  parking.hidden = true;
  document.body.append(parking);
  const elements = { tree: byId('features-panel'), editor: byId('feature-properties-panel'),
    view: byId('viewport'), graph: byId('feature-graph-view') };
  for (const id of DIALOG_PANELS) { elements[id] = byId(id); elements[id].classList.add('workspace-dialog'); }
  const groups = [...byId('creation-toolbar').children];
  for (const [id, indexes] of Object.entries({ create: [0], faces: [1], features: [2, 3, 4, 5], output: [6, 7] })) {
    const toolbar = document.createElement('nav');
    toolbar.className = 'workspace-toolbar';
    toolbar.setAttribute('aria-label', PANEL_NAMES[id]);
    toolbar.append(...indexes.map(index => groups[index]));
    elements[id] = toolbar;
  }
  byId('creation-toolbar').hidden = true;
  const main = document.querySelector('main');
  main.replaceChildren();
  main.id = 'workspace-root';
  document.body.classList.add('docking-workspace');
  const controls = document.createElement('nav');
  controls.className = 'workspace-controls';
  controls.setAttribute('aria-label', 'Workspace layout');
  const picker = document.createElement('select');
  picker.id = 'workspace-panel';
  for (const [id, name] of Object.entries(PANEL_NAMES)) picker.add(new Option(name, id));
  const show = document.createElement('button');
  show.id = 'workspace-show';
  show.textContent = 'Show panel';
  show.onclick = () => controller.select(picker.value);
  const reset = document.createElement('button');
  reset.id = 'workspace-reset';
  reset.textContent = 'Reset layout';
  reset.onclick = () => controller.reset();
  controls.append(picker, show, reset);
  elements.project = byId('project-toolbar');
  elements.project.append(controls);
  for (const element of Object.values(elements)) parking.append(element);
  flushSync(() => createRoot(main).render(<Workspace elements={elements} parking={parking} />));
  return () => controller.ready();
}
