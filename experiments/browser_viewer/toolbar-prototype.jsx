import React, { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { DragDropProvider, useDraggable, useDroppable } from '@dnd-kit/react';
import { Accessibility } from '@dnd-kit/dom';
import { createPortal } from 'react-dom';
import * as Toolbar from '@radix-ui/react-toolbar';
import * as Menu from '@radix-ui/react-dropdown-menu';
import * as Tooltip from '@radix-ui/react-tooltip';
import { featureIcon } from './action-tree.js';
import { arrangeToolbars, clampToolbar, containsPoint, defaultPlacements, EDGES,
  insertToolbar, insertionBefore, toolbarDropZones, toolbarOrder, TOOLBAR_IDS,
  TOOLBAR_STORAGE_KEY, tabsetOf, toolbarOrientation, toolbarSize, validPlacement } from './toolbar-state.js';
import { PANEL_NAMES } from './workspace-state.js';
import './toolbar-prototype.css';

const COMMANDS = {
  project: [['save', 'Save actions', 'download'], ['load', 'Load actions', 'upload'],
    ['reset', 'Restore example', 'restore'], ['workspace-show', 'Show panel', 'show_panel'],
    ['workspace-reset', 'Reset layout', 'restore'], ['show-icon-legend', 'Icon legend', 'arranged_face']],
  create: [
    ['add-selection', 'Selection', 'selection'], ['new-fit', 'Surface fit', 'plane'],
    ['new-point', 'Point', 'point'], ['new-axis', 'Axis', 'axis'],
    ['new-reference-plane', 'Plane', 'reference_plane'], ['new-frame', 'Frame', 'frame'],
  ],
  faces: [['new-build-faces', 'Build faces…', 'build_faces'],
    ['new-surface-intersection', 'Surface intersection', 'surface_intersection'],
    ['new-trimmed-face', 'Trimmed face', 'trimmed_face']],
  features: [['new-feature-group', 'Group', 'group'], ['new-feature-reuse', 'Feature reuse', 'feature_reuse'],
    ['new-selection-region', 'Region', 'selection_region'], ['new-region-selection', 'Apply region', 'region_selection'],
    ['new-relationship', 'Relationship…', 'plane_relationship'], ['new-axis-solve', 'Joint', 'axis_solve'],
    ['new-body', 'Body…', 'body']],
  output: [['evaluate-all', 'Evaluate all', 'ready'], ['auto-evaluate', 'Auto', 'ready'],
    ['new-scale', 'Scale', 'scale'], ['new-transform', 'Transform', 'transform'],
    ['export-cad', 'Export CAD…', 'body']],
};
const originalCommand = id => document.getElementById(id);
const ToolbarHostContext = createContext(null);
// Native content is positioned separately from its tabset frame by FlexLayout.
export function useToolbarHost(node, ref) {
  const register = useContext(ToolbarHostContext)?.register;
  useLayoutEffect(() => register?.(node, ref.current), [register, node, ref]);
}
// Isolate the one library DOM selector behind its public header extension.
export function ToolbarHostMarker({ node }) {
  const ref = useRef(null), registerHost = useContext(ToolbarHostContext)?.registerHost;
  useLayoutEffect(() => registerHost?.(node, ref.current.closest('.flexlayout__tabset')), [registerHost, node]);
  return <span ref={ref} hidden aria-hidden="true" />;
}
const available = (id, bar) => {
  const button = originalCommand(id);
  return button && !button.disabled && !button.closest('[inert]') && !bar?.closest('[inert]');
};
function Icon({ name }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const svg = featureIcon(name);
    ref.current.replaceChildren(svg);
  }, [name]);
  return <span className="prototype-icon" ref={ref} />;
}
function EdgeTarget({ zone, active }) {
  const { host, edge, rect, name, zIndex } = zone;
  const { ref } = useDroppable({ id: host + ':' + edge });
  const vertical = ['left', 'right'].includes(edge);
  return <div ref={ref} className={'prototype-drop-target ' + (vertical ? 'prototype-drop-vertical' : '')}
    style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height, zIndex: Math.max(15, zIndex || 0) }}
    data-active={active} data-host={host} data-kind={host === 'workspace' ? 'workspace' : 'local'}
    data-edge={edge} aria-hidden="true">{name} · {edge}</div>;
}

function ToolbarStrip({ id, original, ready, placement, setPlacement, position, registerBar, hosts, hidden, order, moveOrder }) {
  const { ref, handleRef } = useDraggable({ id });
  const barRef = useRef(null), name = PANEL_NAMES[id], commands = COMMANDS[id];
  const [enabled, setEnabled] = useState({}), [auto, setAuto] = useState(true);
  const [panel, setPanel] = useState(() => originalCommand('workspace-panel').value);
  const orientation = toolbarOrientation(placement);
  useLayoutEffect(() => {
    const update = () => {
      const ids = commands.map(([command]) => command);
      if (id === 'project') ids.push('workspace-panel');
      setEnabled(Object.fromEntries(ids.map(command => [command, ready && available(command, barRef.current)])));
      setAuto(originalCommand('auto-evaluate').checked);
    };
    const observer = new MutationObserver(update);
    observer.observe(original, { subtree: true, attributes: true, attributeFilter: ['disabled', 'inert'] });
    // The strip can move between stacking contexts; main's subtree covers new ancestors.
    observer.observe(document.querySelector('main'), { subtree: true, attributes: true, attributeFilter: ['inert'] });
    const checkbox = originalCommand('auto-evaluate');
    checkbox.addEventListener('change', update); checkbox.addEventListener('input', update);
    update();
    return () => { observer.disconnect(); checkbox.removeEventListener('change', update); checkbox.removeEventListener('input', update); };
  }, [original, ready, commands]);
  const invoke = command => {
    if (ready && available(command, barRef.current)) originalCommand(command).click();
  };
  const place = edge => setPlacement({ ...placement, edge });
  const selectPanel = value => {
    if (!ready || !available('workspace-panel', barRef.current)) return;
    originalCommand('workspace-panel').value = value; setPanel(value);
  };
  return <Toolbar.Root ref={element => { ref(element); barRef.current = element; registerBar(id, element); }}
    className="workspace-toolbar prototype-toolbar" aria-label={name} orientation={orientation}
    data-toolbar-id={id} data-edge={placement.edge} data-host={placement.host} hidden={hidden} style={position}>
    <Toolbar.Button ref={handleRef} className="prototype-grip" aria-label={'Move ' + name + ' toolbar'} title={'Drag ' + name + ' to an edge or float'}>
      <Icon name="grip" />
    </Toolbar.Button>
    <div className="prototype-commands">
      {id === 'project' && <strong className="prototype-brand">Scansor</strong>}
      {commands.map(([command, label, icon]) => <React.Fragment key={command}>
        {command === 'workspace-show' && <select className="prototype-panel-picker" aria-label="Workspace panel"
          value={panel} disabled={!enabled['workspace-panel']} onChange={event => selectPanel(event.target.value)}>
          {Object.entries(PANEL_NAMES).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select>}
        <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <Toolbar.Button data-command-id={command} aria-label={label} disabled={!enabled[command]} onClick={() => invoke(command)}
            role={command === 'auto-evaluate' ? 'checkbox' : undefined}
            aria-checked={command === 'auto-evaluate' ? auto : undefined}>
            {command === 'auto-evaluate' ? <span className="prototype-auto-mark" aria-hidden="true">{auto ? '☑' : '☐'}</span> : <Icon name={icon} />}
            <span className="prototype-command-label">{label}</span>
          </Toolbar.Button>
        </Tooltip.Trigger>
        <Tooltip.Portal><Tooltip.Content className="prototype-tooltip" sideOffset={5}>{label}</Tooltip.Content></Tooltip.Portal>
      </Tooltip.Root></React.Fragment>)}
    </div>
    <Menu.Root modal={false}>
      <Toolbar.Button asChild><Menu.Trigger className="prototype-placement-trigger" aria-label={name + ' toolbar options'}>⋮</Menu.Trigger></Toolbar.Button>
      <Menu.Portal><Menu.Content className="prototype-menu" sideOffset={5} collisionPadding={8}>
        <Menu.Label>{name} · Docking host</Menu.Label>
        <Menu.RadioGroup value={placement.host} onValueChange={host => setPlacement({ ...placement, host,
          edge: placement.edge === 'float' ? 'top' : placement.edge })}>
          <Menu.RadioItem value="workspace">Workspace</Menu.RadioItem>
          {hosts.map(host => <Menu.RadioItem key={host.id} value={host.id}>{host.name}</Menu.RadioItem>)}
        </Menu.RadioGroup>
        <Menu.Separator /><Menu.Label>Placement</Menu.Label>
        {EDGES.map(edge => <Menu.Item key={edge} onSelect={() => place(edge)}>Dock {edge}</Menu.Item>)}
        <Menu.Item onSelect={() => setPlacement({ ...placement, host: 'workspace', edge: 'float',
          orientation: toolbarOrientation(placement) })}>Float</Menu.Item>
        <Menu.Item disabled={placement.edge !== 'float'} onSelect={() => setPlacement({ ...placement,
          orientation: orientation === 'horizontal' ? 'vertical' : 'horizontal' })}>Rotate floating toolbar</Menu.Item>
        <Menu.Item disabled={order.indexOf(id) <= 0} onSelect={() => moveOrder(-1)}>Move earlier</Menu.Item>
        <Menu.Item disabled={!order.includes(id) || order.indexOf(id) === order.length - 1} onSelect={() => moveOrder(1)}>Move later</Menu.Item>
        <Menu.Separator /><Menu.Label>All commands</Menu.Label>
        {commands.map(([command, label, icon]) => command === 'auto-evaluate' ?
          <Menu.CheckboxItem key={command} checked={auto} disabled={!enabled[command]} onSelect={() => invoke(command)}>{label}</Menu.CheckboxItem> :
          <Menu.Item key={command} disabled={!enabled[command]} onSelect={() => invoke(command)}><Icon name={icon} />{label}</Menu.Item>)}
        {id === 'project' && <Menu.Sub>
          <Menu.SubTrigger disabled={!enabled['workspace-panel']}>Workspace panel ▸</Menu.SubTrigger>
          <Menu.Portal><Menu.SubContent className="prototype-menu" collisionPadding={8}>
            <Menu.RadioGroup value={panel} onValueChange={selectPanel}>
              {Object.entries(PANEL_NAMES).map(([value, name]) => <Menu.RadioItem key={value} value={value}
                disabled={!enabled['workspace-panel']}>{name}</Menu.RadioItem>)}
            </Menu.RadioGroup>
          </Menu.SubContent></Menu.Portal>
        </Menu.Sub>}
      </Menu.Content></Menu.Portal>
    </Menu.Root>
  </Toolbar.Root>;
}

function readPlacements() {
  const defaults = defaultPlacements();
  try {
    const saved = JSON.parse(localStorage.getItem(TOOLBAR_STORAGE_KEY));
    for (const id of TOOLBAR_IDS) if (validPlacement(saved?.[id])) defaults[id] = { ...saved[id], host: saved[id].host || 'workspace' };
  } catch { /* Layout remains usable when storage is blocked. */ }
  return defaults;
}

export function ToolbarWorkspace({ children, originals, ready, reset, model, persistLayout, revealPanel }) {
  const shellRef = useRef(null), dragStart = useRef(null);
  const preferredSizes = useRef({});
  const frames = useRef(new Map()), hostFrames = useRef(new Map()), bars = useRef(new Map());
  const scheduleRef = useRef(() => {}), observerRef = useRef(null);
  const [mounts] = useState(() => Object.fromEntries(TOOLBAR_IDS.map(id => {
    const element = document.createElement('div'); element.className = 'prototype-toolbar-mount';
    return [id, element];
  })));
  const register = useCallback((node, element) => {
    frames.current.set(node.getId(), element); observerRef.current?.observe(element); scheduleRef.current();
    return () => { frames.current.delete(node.getId()); observerRef.current?.unobserve(element); scheduleRef.current(); };
  }, []);
  const registerHost = useCallback((node, element) => {
    if (!element) return;
    hostFrames.current.set(node.getId(), element); observerRef.current?.observe(element); scheduleRef.current();
    return () => { hostFrames.current.delete(node.getId()); observerRef.current?.unobserve(element); scheduleRef.current(); };
  }, []);
  const registerBar = useCallback((id, element) => {
    const previous = bars.current.get(id);
    if (previous === element) return;
    if (previous) observerRef.current?.unobserve(previous);
    if (element) { bars.current.set(id, element); observerRef.current?.observe(element); }
    else bars.current.delete(id);
    scheduleRef.current();
  }, []);
  const [placements, setPlacements] = useState(readPlacements), [sizes, setSizes] = useState({});
  const placementsRef = useRef(placements);
  placementsRef.current = placements;
  const [area, setArea] = useState({ width: 0, height: 0 }), [hosts, setHosts] = useState([]);
  const [dragging, setDragging] = useState(null), [target, setTarget] = useState(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const [storageError, setStorageError] = useState(''), [hostNotice, setHostNotice] = useState('');
  const previousReset = useRef(reset), resetting = reset !== previousReset.current;
  const save = next => {
    persistLayout();
    try { localStorage.setItem(TOOLBAR_STORAGE_KEY, JSON.stringify(next)); setStorageError(''); }
    catch { setStorageError('Toolbar placement cannot be saved in this browser.'); }
  };
  const setPlacement = (id, next, before) => {
    setHostNotice('');
    const previous = placementsRef.current;
    const changedGroup = next.host !== previous[id].host || next.edge !== previous[id].edge;
    const updated = before !== undefined || changedGroup ? insertToolbar(previous, id, next, before ?? null) : { ...previous, [id]: next };
    placementsRef.current = updated; setPlacements(updated); save(updated);
  };
  useLayoutEffect(() => {
    if (resetting) {
      previousReset.current = reset; const next = defaultPlacements();
      placementsRef.current = next; setPlacements(next); save(next);
    }
  }, [reset]);
  useLayoutEffect(() => {
    let frame;
    const measure = () => {
      frame = undefined;
      const box = shellRef.current.getBoundingClientRect();
      const update = (setter, value) => setter(previous => JSON.stringify(previous) === JSON.stringify(value) ? previous : value);
      update(setArea, { width: box.width, height: box.height });
      const nextSizes = {};
      for (const [id, element] of bars.current) {
        if (element.hidden) continue;
        const commands = element.querySelector('.prototype-commands');
        const vertical = element.dataset.orientation === 'vertical';
        // Measure preferred extent, not the clipped bar: avoids lane feedback.
        nextSizes[id] = { ...(vertical ? { width: 38, height: commands.scrollHeight + 74 } :
          { width: commands.scrollWidth + 74, height: 38 }), orientation: element.dataset.orientation };
        preferredSizes.current[id] = { ...preferredSizes.current[id], [element.dataset.orientation]: nextSizes[id] };
      }
      setSizes(previous => {
        const next = { ...previous, ...nextSizes };
        return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
      });
      const nextHosts = [];
      for (const [id, element] of hostFrames.current) {
        const host = model.getNodeById(id), node = host?.getSelectedNode?.();
        if (!node?.isVisible() || !element.isConnected) continue;
        const rect = element.getBoundingClientRect();
        if (!rect.width || !rect.height) continue;
        let zIndex = 12;
        for (let ancestor = element; ancestor && ancestor !== shellRef.current; ancestor = ancestor.parentElement)
          zIndex = Math.max(zIndex, Number.parseInt(getComputedStyle(ancestor).zIndex) || 0);
        nextHosts.push({ id, name: host.getTabNodes().map(tab => tab.getName()).join(' / '),
          x: rect.left - box.left, y: rect.top - box.top, width: rect.width, height: rect.height, zIndex });
      }
      update(setHosts, nextHosts);
    };
    const schedule = () => { if (frame === undefined) frame = requestAnimationFrame(measure); };
    scheduleRef.current = schedule;
    const observer = new ResizeObserver(schedule); observerRef.current = observer;
    observer.observe(shellRef.current);
    for (const element of [...frames.current.values(), ...hostFrames.current.values(), ...bars.current.values()]) observer.observe(element);
    const mutations = new MutationObserver(schedule);
    mutations.observe(shellRef.current, { subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'hidden'] });
    model.addChangeListener(schedule); schedule();
    return () => {
      observer.disconnect(); mutations.disconnect(); model.removeChangeListener(schedule);
      if (frame !== undefined) cancelAnimationFrame(frame);
      observerRef.current = null; scheduleRef.current = () => {};
    };
  }, [model]);
  const workspace = { id: 'workspace', x: 0, y: 0, ...area, zIndex: 12 };
  const allHosts = [workspace, ...hosts];
  const arrangements = Object.fromEntries(allHosts.map(host => [host.id, arrangeToolbars(
    Object.fromEntries(TOOLBAR_IDS.filter(id => placements[id].host === host.id).map(id => [id, placements[id]])), sizes, host)]));
  const { zones, corners } = toolbarDropZones(workspace, hosts, arrangements);
  useLayoutEffect(() => {
    if (resetting) return;
    const recover = TOOLBAR_IDS.find(id => {
      const placement = placements[id];
      if (placement.host === 'workspace') return false;
      if (model.getNodeById(placement.host)?.getType() !== 'tabset') return true;
      const host = hosts.find(host => host.id === placement.host);
      if (!host) return false; // A hidden host retains its placement.
      const vertical = toolbarOrientation(placement) === 'vertical', inset = arrangements[host.id].insets;
      return host.width < (vertical ? 38 : 72) || host.height < (vertical ? 72 : 38) ||
        host.width - inset.left - inset.right < 20 || host.height - inset.top - inset.bottom < 20;
    });
    if (recover) {
      setPlacement(recover, { ...placements[recover], host: 'workspace', edge: 'top' });
      setHostNotice(PANEL_NAMES[recover] + ' toolbar returned to workspace: host is unavailable or too small.');
    }
  }, [model, hosts, placements, sizes, resetting]);
  useLayoutEffect(() => {
    const root = shellRef.current.parentElement;
    const reveal = event => {
      const id = event.detail, tab = model.getNodeById(placements[id]?.host)?.getSelectedNode?.();
      if (tab) revealPanel(tab.getId());
      requestAnimationFrame(() => bars.current.get(id)?.querySelector('.prototype-placement-trigger')?.focus());
    };
    root.addEventListener('toolbar-reveal', reveal);
    return () => root.removeEventListener('toolbar-reveal', reveal);
  }, [model, placements, revealPanel]);
  useLayoutEffect(() => {
    const decorated = [];
    for (const host of hosts) {
      const container = hostFrames.current.get(host.id), insets = arrangements[host.id].insets;
      if (!container) continue;
      for (const edge of EDGES) container.style.setProperty('--host-strip-' + edge, insets[edge] + 'px');
      container.dataset.toolbarHost = host.id; decorated.push(container);
    }
    for (const id of TOOLBAR_IDS) {
      const placement = placements[id], host = hosts.find(host => host.id === placement.host);
      const destination = dragging !== id && host && placement.edge !== 'float' ?
        hostFrames.current.get(host.id) || shellRef.current : shellRef.current;
      if (mounts[id].parentElement !== destination) {
        const focused = mounts[id].contains(document.activeElement) ? document.activeElement : null;
        destination.append(mounts[id]); focused?.focus({ preventScroll: true });
      }
    }
    scheduleRef.current();
    return () => {
      for (const container of decorated) {
        delete container.dataset.toolbarHost;
        for (const edge of EDGES) container.style.removeProperty('--host-strip-' + edge);
      }
    };
  }, [model, hosts, sizes, placements, dragging, mounts]);
  useLayoutEffect(() => () => Object.values(mounts).forEach(mount => mount.remove()), [mounts]);
  const currentTarget = point => {
    const box = shellRef.current.getBoundingClientRect(), local = { x: point.x - box.left, y: point.y - box.top };
    const pick = bounds => zones.find(zone => zone.host === bounds.id && containsPoint(zone.rect, local));
    const neutral = host => corners.some(corner => corner.host === host && containsPoint(corner.rect, local));
    let zone = pick(workspace);
    if (!zone && neutral('workspace')) return { blocked: true };
    const hit = document.elementFromPoint(point.x, point.y);
    if (!zone) for (const candidate of [...hosts].sort((a, b) => b.zIndex - a.zIndex)) {
      const matches = hostFrames.current.get(candidate.id)?.contains(hit) || [...frames.current.entries()].some(([id, element]) =>
        tabsetOf(model.getNodeById(id))?.getId() === candidate.id && element.contains(hit));
      if (matches) {
        zone = pick(candidate);
        if (!zone && neutral(candidate.id)) return { blocked: true };
        break;
      }
    }
    if (!zone) return null;
    const rects = Object.fromEntries([...bars.current.entries()].filter(([, bar]) => !bar.hidden).map(([id, bar]) => {
      const rect = bar.getBoundingClientRect();
      return [id, { x: rect.left - box.left, y: rect.top - box.top, width: rect.width, height: rect.height }];
    }));
    const destination = { host: zone.host, edge: zone.edge };
    return { ...destination, before: insertionBefore(placements, dragStart.current.id, destination, local, rects) };
  };
  const context = useMemo(() => ({ register, registerHost }), [register, registerHost]);
  const globalInsets = arrangements.workspace.insets;
  let preview;
  if (dragging && target && allHosts.some(host => host.id === target.host)) {
    const bounds = allHosts.find(host => host.id === target.host), orientation = toolbarOrientation(target);
    const extraHeight = dragging === 'project' ?
      (bars.current.get(dragging)?.querySelector('.prototype-brand')?.getBoundingClientRect().width || 80) + 32 : 0;
    const size = preferredSizes.current[dragging]?.[orientation] ||
      (orientation === 'vertical' ? { width: 38, height: COMMANDS[dragging].length * 32 + 72 + extraHeight } : { width: 74, height: 38 });
    const next = insertToolbar(placements, dragging, { ...placements[dragging], host: target.host, edge: target.edge }, target.before);
    const group = Object.fromEntries(TOOLBAR_IDS.filter(id => next[id].host === target.host).map(id => [id, next[id]]));
    const position = arrangeToolbars(group, { ...sizes, [dragging]: size }, bounds).positions[dragging];
    const peers = toolbarOrder(next, target.host, target.edge).filter(id => id !== dragging);
    const orderLabel = target.before ? ' · before ' + PANEL_NAMES[target.before] : peers.length ? ' · after ' + PANEL_NAMES[peers.at(-1)] : '';
    preview = <div className="prototype-drop-preview" data-host={target.host} data-edge={target.edge} data-before={target.before || ''}
      style={{ left: bounds.x + position.left, top: bounds.y + position.top,
        width: Math.min(size.width, position.maxWidth), height: Math.min(size.height, position.maxHeight) }}>
      <span role="status">{(bounds.name || 'Workspace') + ' · ' + target.edge + orderLabel}</span>
    </div>;
  }
  return <DragDropProvider plugins={[Accessibility]} onDragStart={event => {
    const id = event.operation.source.id, bar = bars.current.get(id).getBoundingClientRect(), shell = shellRef.current.getBoundingClientRect();
    dragStart.current = { id, x: bar.left - shell.left, y: bar.top - shell.top, pointer: { ...event.operation.position.initial } };
    setDragging(id); setPointer({ ...event.operation.position.current });
  }} onDragMove={event => {
    const current = event.operation.position.current;
    const next = event.to || { x: current.x + (event.by?.x || 0), y: current.y + (event.by?.y || 0) };
    const destination = currentTarget(next);
    setTarget(destination?.blocked ? null : destination); setPointer({ ...next });
  }} onDragEnd={event => {
    setDragging(null); setTarget(null);
    if (event.canceled || !dragStart.current) return;
    const point = event.operation.position.current, start = dragStart.current, placement = placements[start.id];
    const destination = currentTarget(point);
    if (destination?.blocked) return;
    setPlacement(start.id, clampToolbar({ ...placement, ...(destination ? { host: destination.host, edge: destination.edge } : { host: 'workspace', edge: 'float' }),
      orientation: toolbarOrientation(placement), x: start.x + point.x - start.pointer.x,
      y: start.y + point.y - start.pointer.y }, area, toolbarSize(placement, sizes[start.id])), destination?.before);
  }}>
    <Tooltip.Provider delayDuration={350}><ToolbarHostContext.Provider value={context}>
      <div ref={shellRef} className="prototype-shell" data-dragging={Boolean(dragging)}>
        <div className="prototype-panel-area" style={globalInsets}>{children}</div>
        {TOOLBAR_IDS.map(id => {
          const placement = placements[id], host = allHosts.find(host => host.id === placement.host), bounds = host || workspace;
          const float = clampToolbar(placement, area, toolbarSize(placement, sizes[id]));
          const normal = placement.edge === 'float' ? { left: float.x, top: float.y, maxWidth: area.width, maxHeight: area.height } :
            arrangements[placement.host]?.positions[id] || { left: 0, top: 0 };
          const position = dragging === id ? { ...normal, left: dragStart.current.x + pointer.x - dragStart.current.pointer.x,
            top: dragStart.current.y + pointer.y - dragStart.current.pointer.y } : normal;
          return createPortal(<ToolbarStrip id={id} original={originals[id]} ready={ready} placement={placement}
            order={placement.edge === 'float' ? [] : toolbarOrder(placements, placement.host, placement.edge)} moveOrder={direction => {
              const order = toolbarOrder(placements, placement.host, placement.edge), index = order.indexOf(id);
              setPlacement(id, placement, direction < 0 ? order[index - 1] : order[index + 2] || null);
            }}
            setPlacement={next => setPlacement(id, next)} position={{ ...position, zIndex: dragging === id ? 6000 : placement.edge === 'float' ? 3001 : bounds.zIndex,
              pointerEvents: dragging === id ? 'none' : undefined }}
            registerBar={registerBar} hidden={!host && dragging !== id} hosts={hosts} />, mounts[id], id);
        })}
        {zones.map(zone => <EdgeTarget key={zone.host + ':' + zone.edge} zone={zone}
          active={target?.host === zone.host && target?.edge === zone.edge} />)}
        {preview}
        <div role="status" className="workspace-layout-notice" hidden={!storageError && !hostNotice}>{storageError || hostNotice}</div>
      </div>
    </ToolbarHostContext.Provider></Tooltip.Provider>
  </DragDropProvider>;
}
