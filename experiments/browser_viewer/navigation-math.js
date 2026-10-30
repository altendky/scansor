import { Vector3, Plane } from 'three';

// Wheel devices report pixels, lines or pages. A positive exponential never
// discards a large inward gesture as a negative linear dolly factor.
export function wheelZoomFactor(delta, mode, viewportHeight) {
  if (!Number.isFinite(delta)) return 1;
  const pixels = delta * (mode === 1 ? 16 : mode === 2 ? viewportHeight : 1);
  return Math.exp(Math.max(-.5, Math.min(.5, pixels * .0003)));
}

// Move an already-rotated camera from a target-centered orbit to an orbit about
// the picked anchor. Translate its look-at target equally, avoiding a view snap.
export function anchorRotation(camera, target, anchor, previousQuaternion, previousTarget) {
  const rotation = camera.quaternion.clone().multiply(previousQuaternion.clone().invert());
  const movedAnchor = anchor.clone().sub(previousTarget).applyQuaternion(rotation).add(previousTarget);
  const correction = anchor.clone().sub(movedAnchor);
  camera.position.add(correction);
  target.add(correction);
  camera.updateMatrixWorld();
}

export function anchorZoom(camera, target, anchor, previousDistance, previousTarget) {
  if (previousDistance <= 0) return;
  const factor = camera.position.distanceTo(target) / previousDistance;
  const correction = anchor.clone().sub(previousTarget).multiplyScalar(1 - factor);
  camera.position.add(correction);
  target.add(correction);
  camera.updateMatrixWorld();
}

export function viewPlaneAnchor(ray, camera, target) {
  const plane = new Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new Vector3()), target);
  return ray.intersectPlane(plane, new Vector3());
}

// Background zoom needs a surface-scale depth even when the orbit target has
// approached the camera. This is only a reference plane, not a synthetic hit.
export function zoomDepthReference(camera, target, worldBounds, recentSurface = null) {
  const position = camera.position, forward = new Vector3(0, 0, -1).applyQuaternion(camera.quaternion).normalize(),
    finitePoint = point => point && [point.x, point.y, point.z].every(Number.isFinite),
    positive = depth => Number.isFinite(depth) && depth > Math.max(0, camera.near),
    reference = depth => position.clone().addScaledVector(forward, depth);
  if (!finitePoint(position) || !finitePoint(forward)) return target.clone();
  if (finitePoint(recentSurface)) {
    const depth = recentSurface.clone().sub(position).dot(forward);
    if (positive(depth)) return recentSurface.clone();
  }
  const lower = worldBounds?.min, upper = worldBounds?.max;
  if (finitePoint(lower) && finitePoint(upper) && ['x', 'y', 'z'].every(axis => lower[axis] <= upper[axis])) {
    let nearDepth = 0, farDepth = 0;
    for (const axis of ['x', 'y', 'z']) {
      const first = (lower[axis] - position[axis]) * forward[axis],
        second = (upper[axis] - position[axis]) * forward[axis];
      nearDepth += Math.min(first, second); farDepth += Math.max(first, second);
    }
    if (positive(nearDepth)) return reference(nearDepth);
    const inside = ['x', 'y', 'z'].every(axis => position[axis] >= lower[axis] && position[axis] <= upper[axis]),
      centerDepth = nearDepth / 2 + farDepth / 2;
    if (inside && positive(centerDepth)) return reference(centerDepth);
    if (positive(farDepth)) return reference(farDepth);
  }
  return target.clone();
}

// Perspective pan measured in CSS pixels at the picked surface's view depth.
export function panByPixels(camera, target, dx, dy, viewportHeight, depth) {
  if (viewportHeight <= 0 || depth <= 0) return;
  const scale = 2 * depth * Math.tan(camera.fov * Math.PI / 360) / camera.zoom / viewportHeight;
  const movement = new Vector3(-dx * scale, dy * scale, 0).applyQuaternion(camera.quaternion);
  camera.position.add(movement);
  target.add(movement);
  camera.updateMatrixWorld();
}
