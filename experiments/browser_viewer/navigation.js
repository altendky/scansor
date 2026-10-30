import { MOUSE, Vector3 } from 'three';
import { anchorRotation, anchorZoom, panByPixels, wheelZoomFactor } from './navigation-math.js';
import { TrackballControls } from './vendor/TrackballControls.js';
import { OrbitControls } from './vendor/OrbitControls.js';

// Onshape-style mouse mapping. Keep navigation separate from selection and fitting.
// Free rotation can roll; Alt rotation keeps the example's Z axis upright.
export function onshapeNavigation(camera, canvas, redraw, cursorAnchor) {
  const free = new TrackballControls(camera, canvas);
  const constrained = new OrbitControls(camera, canvas);
  free.staticMoving = true;
  free.rotateSpeed = 2;
  free.noPan = true; // Use projection-correct pixel panning below.
  free.mouseButtons = {LEFT: -1, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE};
  free.keys = [];
  constrained.mouseButtons = {LEFT: -1, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE};
  constrained.enabled = false;
  constrained.target = free.target;
  let active = free, dragging = false, rotationAnchor = null;
  let gesture = null, selectionPointer = null, transitioning = false, panDepth = 0;
  const mode = event => event.button === 1 || event.ctrlKey ? 'pan' : event.altKey ? 'upright' : 'rotate';
  const options = {zoomAtCursor: true, rotateAtCursor: true};
  const update = () => {
    if (active === free) {
      // Presets and upright rotation can leave world-up almost parallel to the
      // view. Trackball needs screen-up or tiny horizontal jitter dominates pitch.
      // Reconstruct it from the rendered orientation, preserving the current roll.
      camera.lookAt(free.target);
      camera.up.set(0, 1, 0).applyQuaternion(camera.quaternion);
    }
    const previousQuaternion = camera.quaternion.clone(), previousTarget = free.target.clone();
    active.update();
    if (rotationAnchor) anchorRotation(camera, free.target, rotationAnchor, previousQuaternion, previousTarget);
  };
  const configure = (event, preserveAnchor = false) => {
    const nextMode = mode(event);
    if (nextMode === 'pan') {
      const anchor = cursorAnchor(event, true) || free.target;
      panDepth = anchor.clone().sub(camera.position).dot(camera.getWorldDirection(new Vector3()));
    }
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
    if (event.button === 0) {
      selectionPointer = event.pointerId;
      free.enabled = false; constrained.enabled = false; return;
    }
    gesture = snapshot(event); dragging = true; configure(gesture);
  };
  canvas.addEventListener('pointerdown', begin, {capture: true});
  const finish = () => {
    update(); dragging = false; rotationAnchor = null; gesture = null; selectionPointer = null;
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
  const end = event => {
    if (transitioning || !gesture && selectionPointer === null) return;
    if (gesture && event.pointerId !== gesture.pointerId
      || selectionPointer !== null && event.pointerId !== selectionPointer) return;
    // Both controls must receive the real release before changing enabled flags.
    gesture = null; dragging = false; selectionPointer = null;
    setTimeout(() => { if (!gesture && selectionPointer === null) finish(); }, 0);
  };
  // Left-button orbit-mode presses are not captured by either control. Their
  // release can be outside the canvas; it must still reenable navigation.
  canvas.ownerDocument.addEventListener('pointerup', end);
  canvas.ownerDocument.addEventListener('pointercancel', end);
  canvas.ownerDocument.defaultView.addEventListener('blur', () => {
    if (gesture) canvas.dispatchEvent(new PointerEvent('pointerup', {...gesture, buttons: 0, bubbles: true}));
    else finish();
  });
  canvas.ownerDocument.addEventListener('pointermove', event => {
    if (!gesture || transitioning || event.pointerId !== gesture.pointerId) return;
    rebase(event);
    if (mode(gesture) === 'pan') {
      panByPixels(camera, free.target, event.clientX - gesture.clientX,
        event.clientY - gesture.clientY, canvas.getBoundingClientRect().height, panDepth);
    }
    gesture = {...gesture, clientX: event.clientX, clientY: event.clientY};
  }, {capture: true});
  canvas.ownerDocument.addEventListener('pointermove', () => { if (dragging) redraw(); });
  const modifiersChanged = event => {
    if (event.key === 'Control' || event.key === 'Alt') rebase(event);
  };
  canvas.ownerDocument.addEventListener('keydown', modifiersChanged);
  canvas.ownerDocument.addEventListener('keyup', modifiersChanged);
  let restoreWheel = () => {};
  canvas.addEventListener('wheel', () => {
    // Suppress only the controls' wheel zoom. Other application listeners must
    // still cancel selection strokes and clear model-pick state for this event.
    restoreWheel();
    const noZoom = free.noZoom, enableZoom = constrained.enableZoom;
    free.noZoom = true; constrained.enableZoom = false;
    const timer = setTimeout(() => restoreWheel(), 0);
    restoreWheel = () => {
      clearTimeout(timer);
      free.noZoom = noZoom; constrained.enableZoom = enableZoom;
      restoreWheel = () => {};
    };
  }, {capture: true, passive: true});
  canvas.addEventListener('wheel', event => {
    // Own wheel scaling while preserving the controls' touch/pinch behavior.
    // Apply each tick at its own current cursor position.
    event.preventDefault();
    try {
      if (selectionPointer !== null) return;
      update();
      const anchor = !dragging && options.zoomAtCursor ? cursorAnchor(event, true, 'zoom') : null;
      const previousTarget = free.target.clone(), previousDistance = camera.position.distanceTo(free.target);
      const factor = wheelZoomFactor(event.deltaY, event.deltaMode, canvas.getBoundingClientRect().height);
      const distance = Math.max(free.minDistance, Math.min(free.maxDistance, previousDistance * factor));
      if (!(previousDistance > 0) || !(distance > 0) || !Number.isFinite(distance)) return;
      camera.position.sub(free.target).multiplyScalar(distance / previousDistance).add(free.target);
      camera.updateMatrixWorld();
      if (anchor) anchorZoom(camera, free.target, anchor, previousDistance, previousTarget);
      redraw();
    } finally { restoreWheel(); }
  }, {passive: false});
  free.addEventListener('change', redraw);
  constrained.addEventListener('change', redraw);
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  return {target: free.target, options, update, resize: () => free.handleResize()};
}
