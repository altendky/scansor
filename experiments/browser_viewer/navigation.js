import { MOUSE } from 'three';
import { anchorRotation, anchorZoom } from './navigation-math.js';
import { TrackballControls } from '/vendor/TrackballControls.js';
import { OrbitControls } from '/vendor/OrbitControls.js';

// Onshape-style mouse mapping. Keep navigation separate from selection and fitting.
// Free rotation can roll; Alt rotation keeps the example's Z axis upright.
export function onshapeNavigation(camera, canvas, redraw, cursorAnchor) {
  const free = new TrackballControls(camera, canvas);
  const constrained = new OrbitControls(camera, canvas);
  free.staticMoving = true;
  free.rotateSpeed = 2;
  free.mouseButtons = {LEFT: -1, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE};
  free.keys = [];
  constrained.mouseButtons = {LEFT: -1, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE};
  constrained.enabled = false;
  constrained.target = free.target;
  let active = free, dragging = false, rotationAnchor = null;
  let gesture = null, transitioning = false;
  const mode = event => event.button === 1 || event.ctrlKey ? 'pan' : event.altKey ? 'upright' : 'rotate';
  const options = {zoomAtCursor: true, rotateAtCursor: true};
  const update = () => {
    const previousQuaternion = camera.quaternion.clone(), previousTarget = free.target.clone();
    active.update();
    if (rotationAnchor) anchorRotation(camera, free.target, rotationAnchor, previousQuaternion, previousTarget);
  };
  const configure = (event, preserveAnchor = false) => {
    const nextMode = mode(event);
    rotationAnchor = nextMode !== 'pan' && options.rotateAtCursor
      ? (preserveAnchor ? rotationAnchor : cursorAnchor(event, false)) : null;
    active = nextMode === 'upright' ? constrained : free;
    free.mouseButtons.RIGHT = nextMode === 'pan' ? MOUSE.PAN : MOUSE.ROTATE;
    free.enabled = active === free;
    constrained.enabled = active === constrained;
    if (active === constrained) { camera.up.set(0, 0, 1); update(); }
  };
  const snapshot = event => ({button: event.button, pointerId: event.pointerId,
    pointerType: event.pointerType, clientX: event.clientX, clientY: event.clientY,
    ctrlKey: event.ctrlKey, altKey: event.altKey, buttons: event.buttons});
  const begin = event => {
    if (transitioning) return;
    if (event.button === 0) { free.enabled = false; constrained.enabled = false; return; }
    gesture = snapshot(event); dragging = true; configure(gesture);
  };
  canvas.addEventListener('pointerdown', begin, {capture: true});
  const finish = () => {
    update(); dragging = false; rotationAnchor = null; gesture = null;
    free.enabled = true; constrained.enabled = false; active = free; redraw();
  };
  const rebase = modifiers => {
    if (!gesture || transitioning) return;
    const next = {...gesture, ctrlKey: modifiers.ctrlKey, altKey: modifiers.altKey};
    if (mode(next) === mode(gesture)) { gesture = next; return; }
    update();
    // Use public DOM events to end/restart the controls' internal drag at the
    // current pointer, avoiding private state fields and stale movement deltas.
    transitioning = true;
    try {
      canvas.dispatchEvent(new PointerEvent('pointerup', {...gesture, buttons: 0, bubbles: true}));
      const preserveAnchor = mode(gesture) !== 'pan' && mode(next) !== 'pan';
      configure(next, preserveAnchor); gesture = next;
      canvas.dispatchEvent(new PointerEvent('pointerdown', {...next, bubbles: true}));
    } finally { transitioning = false; }
    redraw();
  };
  const end = () => {
    if (transitioning) return;
    // Both controls must receive the real release before changing enabled flags.
    gesture = null; dragging = false;
    setTimeout(() => { if (!gesture) finish(); }, 0);
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.ownerDocument.addEventListener('pointermove', event => {
    if (!gesture || transitioning || event.pointerId !== gesture.pointerId) return;
    rebase(event);
    gesture = {...gesture, clientX: event.clientX, clientY: event.clientY};
  }, {capture: true});
  canvas.ownerDocument.addEventListener('pointermove', () => { if (dragging) redraw(); });
  const modifiersChanged = event => {
    if (event.key === 'Control' || event.key === 'Alt') rebase(event);
  };
  canvas.ownerDocument.addEventListener('keydown', modifiersChanged);
  canvas.ownerDocument.addEventListener('keyup', modifiersChanged);
  canvas.addEventListener('wheel', event => {
    // The controls have consumed this wheel event but have not updated the
    // camera yet. Apply each event separately, including its current cursor.
    const anchor = !dragging && options.zoomAtCursor ? cursorAnchor(event, true) : null;
    const previousTarget = free.target.clone(), previousDistance = camera.position.distanceTo(free.target);
    update();
    if (anchor) anchorZoom(camera, free.target, anchor, previousDistance, previousTarget);
    redraw();
  }, {passive: true});
  free.addEventListener('change', redraw);
  constrained.addEventListener('change', redraw);
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  return {target: free.target, options, update, resize: () => free.handleResize()};
}
