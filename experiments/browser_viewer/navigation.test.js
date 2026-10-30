import test from 'node:test';
import assert from 'node:assert/strict';
import { Box3, Matrix4, PerspectiveCamera, Quaternion, Raycaster, Vector2, Vector3 } from 'three';
import { anchorRotation, anchorZoom, viewPlaneAnchor, zoomDepthReference, panByPixels, wheelZoomFactor } from './navigation-math.js';

test('wheel scaling normalizes units and keeps extreme gestures positive', () => {
  assert.equal(wheelZoomFactor(16, 0, 800), wheelZoomFactor(1, 1, 800));
  assert.equal(wheelZoomFactor(800, 0, 800), wheelZoomFactor(1, 2, 800));
  for (const delta of [-Number.MAX_VALUE, -4000, -.1, .1, 4000, Number.MAX_VALUE]) {
    const factor = wheelZoomFactor(delta, 0, 800);
    assert.ok(Number.isFinite(factor) && factor > 0);
    assert.equal(factor < 1, delta < 0);
  }
  assert.equal(wheelZoomFactor(NaN, 0, 800), 1);
});

function setup() {
  const camera = new PerspectiveCamera(45, 1.5, .01, 1000);
  camera.position.set(2, -15, 8); camera.up.set(0, 0, 1);
  const target = new Vector3(0, 0, 0);
  camera.lookAt(target); camera.updateMatrixWorld();
  return {camera, target};
}
function sameScreen(before, after) {
  assert.ok(Math.abs(before.x - after.x) < 1e-10);
  assert.ok(Math.abs(before.y - after.y) < 1e-10);
}

for (const factor of [.5, 1.3]) {
  test(`zoom factor ${factor} preserves the picked point under the cursor`, () => {
    const {camera, target} = setup(), anchor = new Vector3(3, -1, 1);
    const screen = anchor.clone().project(camera);
    const previousTarget = target.clone(), distance = camera.position.distanceTo(target);
    camera.position.sub(target).multiplyScalar(factor).add(target);
    anchorZoom(camera, target, anchor, distance, previousTarget);
    sameScreen(screen, anchor.clone().project(camera));
    assert.ok(Math.abs(camera.position.distanceTo(anchor) - setup().camera.position.distanceTo(anchor) * factor) < 1e-10);
  });
}

for (const axis of [new Vector3(0, 0, 1), new Vector3(1, 2, 3).normalize()]) {
  test(`rotation about ${axis.toArray()} preserves the off-center anchor without snapping`, () => {
    const {camera, target} = setup(), anchor = new Vector3(3, -1, 1);
    const screen = anchor.clone().project(camera), distance = camera.position.distanceTo(anchor);
    const previousTarget = target.clone(), previousQuaternion = camera.quaternion.clone();
    const rotation = new Quaternion().setFromAxisAngle(axis, .4);
    camera.position.sub(target).applyQuaternion(rotation).add(target);
    camera.up.applyQuaternion(rotation); camera.lookAt(target);
    anchorRotation(camera, target, anchor, previousQuaternion, previousTarget);
    sameScreen(screen, anchor.clone().project(camera));
    assert.ok(Math.abs(camera.position.distanceTo(anchor) - distance) < 1e-10);
  });
}

test('empty-space zoom anchor uses the cursor ray at the current view depth', () => {
  const {camera, target} = setup(), raycaster = new Raycaster();
  raycaster.setFromCamera(new Vector2(.8, -.7), camera);
  const anchor = viewPlaneAnchor(raycaster.ray, camera, target);
  assert.ok(anchor);
  sameScreen(new Vector3(.8, -.7), anchor.clone().project(camera));
  assert.ok(Math.abs(anchor.clone().sub(target).dot(camera.getWorldDirection(new Vector3()))) < 1e-10);
});

for (const [width,height,fov,zoom,depth] of [[1200,600,45,1,8],[500,900,70,2,3],[1600,800,30,.5,50]]) {
  test(`pan tracks CSS pixels at ${width}x${height}, fov ${fov}, zoom ${zoom}`, () => {
    const {camera,target} = setup();
    camera.aspect=width/height;camera.fov=fov;camera.zoom=zoom;
    camera.rotateZ(.4);camera.updateProjectionMatrix();camera.updateMatrixWorld();
    const anchor = new Vector3(.5,.2,-depth).applyMatrix4(camera.matrixWorld);
    const before = anchor.clone().project(camera), rotation=camera.quaternion.clone();
    panByPixels(camera,target,137,-61,height,depth);
    const after=anchor.clone().project(camera);
    assert.ok(Math.abs((after.x-before.x)*width/2-137)<1e-9);
    assert.ok(Math.abs(-(after.y-before.y)*height/2+61)<1e-9);
    assert.ok(camera.quaternion.equals(rotation));
  });
}

function nearTargetSetup(scale = 1) {
  const camera = new PerspectiveCamera(45, 1.5, .01 * scale, 1000 * scale),
    target = new Vector3(0, 0, 99.99 * scale);
  camera.position.set(0, 0, 100 * scale); camera.lookAt(target); camera.updateMatrixWorld();
  return {camera, target, bounds: new Box3(new Vector3(-10, -10, -1).multiplyScalar(scale), new Vector3(10, 10, 0).multiplyScalar(scale))};
}

function wheelStep(camera, target, anchor, factor) {
  const previousTarget = target.clone(), distance = camera.position.distanceTo(target);
  camera.position.sub(target).multiplyScalar(factor).add(target);
  anchorZoom(camera, target, anchor, distance, previousTarget);
}

test('surface-scale background depth fixes the near-target 10000x zoom slowdown without changing its factor', () => {
  const factor = wheelZoomFactor(-120, 0, 800), old = nearTargetSetup(), next = nearTargetSetup(),
    raycaster = new Raycaster();
  raycaster.setFromCamera(new Vector2(0, 0), old.camera);
  const oldAnchor = viewPlaneAnchor(raycaster.ray, old.camera, old.target),
    reference = zoomDepthReference(next.camera, next.target, next.bounds),
    newAnchor = viewPlaneAnchor(raycaster.ray, next.camera, reference);
  wheelStep(old.camera, old.target, oldAnchor, factor);
  wheelStep(next.camera, next.target, newAnchor, factor);
  const oldMovement = 100 - old.camera.position.z, newMovement = 100 - next.camera.position.z;
  assert(Math.abs(newMovement / oldMovement - 10000) < 1e-5);
  assert(Math.abs(next.camera.position.z - 100 * factor) < 1e-10);
  assert.deepEqual(reference.toArray(), [0, 0, 0]);
});

test('recent real depth and bounds give continuous background anchors over repeated wheel ticks', () => {
  const {camera, target, bounds} = nearTargetSetup(), recent = new Vector3(5, -4, 0),
    savedRecent = recent.clone(), factor = wheelZoomFactor(-80, 0, 900), raycaster = new Raycaster(),
    cursor = new Vector2(.35, -.2);
  let previousDepth = 100;
  for (let tick = 0; tick < 12; tick++) {
    raycaster.setFromCamera(cursor, camera);
    const reference = zoomDepthReference(camera, target, bounds, recent),
      anchor = viewPlaneAnchor(raycaster.ray, camera, reference), before = anchor.clone().project(camera),
      depth = reference.clone().sub(camera.position).dot(camera.getWorldDirection(new Vector3()));
    assert(depth > 0 && depth <= previousDepth + 1e-9); previousDepth = depth;
    const fromBounds = viewPlaneAnchor(raycaster.ray, camera, zoomDepthReference(camera, target, bounds));
    assert(anchor.distanceTo(fromBounds) < 1e-9);
    wheelStep(camera, target, anchor, factor);
    sameScreen(before, anchor.clone().project(camera));
    assert(Math.abs(camera.position.z - 100 * factor ** (tick + 1)) < 1e-10);
  }
  assert.deepEqual(recent, savedRecent);
});

test('invalid, empty and behind-camera bounds retain a cloned target; recent behind or clipped hits are ignored', () => {
  const {camera, target, bounds} = nearTargetSetup(), invalid = [null, new Box3(),
    new Box3(new Vector3(5, 5, 5), new Vector3(-5, -5, -5)),
    new Box3(new Vector3(NaN, 0, 0), new Vector3(1, 1, 1)),
    new Box3(new Vector3(-1, -1, 101), new Vector3(1, 1, 110)),
    new Box3(new Vector3(-1, -1, 99.995), new Vector3(1, 1, 99.999))];
  for (const box of invalid) {
    const reference = zoomDepthReference(camera, target, box);
    assert.deepEqual(reference, target); assert.notEqual(reference, target);
  }
  for (const hit of [new Vector3(0, 0, 101), new Vector3(0, 0, 100),
    new Vector3(0, 0, 99.999), new Vector3(NaN, 0, 0)])
    assert.deepEqual(zoomDepthReference(camera, target, bounds, hit).toArray(), [0, 0, 0]);
  const validRecent = new Vector3(3, 4, -20);
  assert.deepEqual(zoomDepthReference(camera, target, bounds, validRecent), validRecent);
});

test('camera-inside bounds uses a positive center depth, then far depth when the center is not ahead', () => {
  const camera = new PerspectiveCamera(45, 1, .01, 100), target = new Vector3(0, 0, -10),
    bounds = new Box3(new Vector3(-1, -1, -1), new Vector3(1, 1, 1));
  camera.position.set(0, 0, .75); camera.lookAt(target);
  assert.deepEqual(zoomDepthReference(camera, target, bounds).toArray(), [0, 0, 0]);
  camera.position.set(0, 0, -.75); camera.lookAt(target);
  assert.deepEqual(zoomDepthReference(camera, target, bounds).toArray(), [0, 0, -1]);
  camera.position.set(2, 0, 0); camera.lookAt(new Vector3(2, 0, -10));
  assert.deepEqual(zoomDepthReference(camera, target, bounds).toArray(), [2, 0, -1]);
});

test('a clipped positive near bound cannot recreate the shallow background reference', () => {
  const {camera, target} = nearTargetSetup(), bounds = new Box3(new Vector3(-1, -1, 80), new Vector3(1, 1, 99.999));
  assert.deepEqual(zoomDepthReference(camera, target, bounds).toArray(), [0, 0, 80]);
  const recent = new Vector3(3, -4, 90), saved = recent.clone(),
    reference = zoomDepthReference(camera, target, bounds, recent);
  assert.deepEqual(reference, recent); assert.notEqual(reference, recent); assert.deepEqual(recent, saved);
});

test('rotated world bounds use their projected depth interval, with the reference on the forward ray', () => {
  const {camera, target} = setup(), rotation = new Quaternion().setFromAxisAngle(new Vector3(1, 2, 3).normalize(), .63),
    translation = new Vector3(100, -50, 20), transform = new Matrix4().compose(translation, rotation, new Vector3(1, 1, 1)),
    bounds = new Box3(new Vector3(-3, -2, -1), new Vector3(3, 2, 4)).applyMatrix4(transform);
  camera.position.applyMatrix4(transform); target.applyMatrix4(transform); camera.quaternion.premultiply(rotation);
  camera.updateMatrixWorld();
  const direction = camera.getWorldDirection(new Vector3()), depths = [];
  for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y])
    for (const z of [bounds.min.z, bounds.max.z]) depths.push(new Vector3(x, y, z).sub(camera.position).dot(direction));
  const reference = zoomDepthReference(camera, target, bounds), delta = reference.clone().sub(camera.position);
  assert(Math.abs(delta.dot(direction) - Math.min(...depths)) < 1e-10);
  assert(delta.clone().cross(direction).length() < 1e-10);
  assert(delta.dot(direction) > camera.near);
});

test('bounds depth references are read-only and preserve scale, rotation and translation', () => {
  for (const scale of [1e-6, 1, 1e3]) {
    const {camera, target, bounds} = nearTargetSetup(scale), rotation = new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), Math.PI / 2),
      offset = new Vector3(40, -20, 10).multiplyScalar(scale),
      corners = [bounds.min, bounds.max].map(point => point.clone().applyQuaternion(rotation).add(offset));
    bounds.setFromPoints(corners);
    camera.position.applyQuaternion(rotation).add(offset); target.applyQuaternion(rotation).add(offset);
    camera.quaternion.premultiply(rotation); camera.updateMatrixWorld();
    const saved = {camera: camera.toJSON(), target: target.clone(), bounds: bounds.clone()},
      reference = zoomDepthReference(camera, target, bounds), expected = offset;
    assert(reference.distanceTo(expected) < scale * 1e-10);
    assert.deepEqual(camera.toJSON(), saved.camera); assert.deepEqual(target, saved.target); assert.deepEqual(bounds, saved.bounds);
  }
});
