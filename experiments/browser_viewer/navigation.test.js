import test from 'node:test';
import assert from 'node:assert/strict';
import { PerspectiveCamera, Quaternion, Raycaster, Vector2, Vector3 } from 'three';
import { anchorRotation, anchorZoom, viewPlaneAnchor, panByPixels } from './navigation-math.js';

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
