import { Vector3, Plane } from 'three';

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
