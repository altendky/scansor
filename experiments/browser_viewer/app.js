import * as THREE from 'three';
import { onshapeNavigation } from './navigation.js';
import { viewPlaneAnchor } from './navigation-math.js';
import { editSelection, rectangleHits } from './selection.js';

const $ = id => document.getElementById(id);
const viewport = $('viewport');
let renderer, scene, camera, controls, mesh, selectedPoints, overlays;
let metadata, positions, session, result = null, revision = 0, busy = false;
let pending = false, frames = 0;
const undo = [];
const status = (message, error = false) => {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
};
async function request(path, value) {
  const response = await fetch(path, value === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Scansor-Request': '1'}, body: JSON.stringify(value),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
function draw() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    const start = performance.now();
    controls.update();
    renderer.render(scene, camera);
    frames++;
    $('render-stats').textContent = `${frames} frames · ${(performance.now() - start).toFixed(1)} ms submit · idle when unchanged`;
  });
}
function clearGuides() {
  for (const child of [...overlays.children]) {
    overlays.remove(child); child.geometry.dispose(); child.material.dispose();
  }
}
function paint() {
  const colors = mesh.geometry.getAttribute('color');
  const gray = new THREE.Color('#8796a2'), amber = new THREE.Color('#f2b544'), violet = new THREE.Color('#bd91f4');
  for (let i = 0; i < colors.count; i++) colors.setXYZ(i, gray.r, gray.g, gray.b);
  for (const [ids, color] of [[session.lateral_ids, amber], [session.plane_ids, violet]]) {
    for (const id of ids) colors.setXYZ(id, color.r, color.g, color.b);
  }
  $('legend').textContent = 'Amber: cone · violet: plane · gray: context';
  if ($('colors').value === 'residual' && result) {
    const white = new THREE.Color('#f8f8f8'), blue = new THREE.Color('#1e55e6'), red = new THREE.Color('#d22828');
    const scales = [];
    for (const [ids, residuals] of [[session.lateral_ids, result.lateral_residuals], [session.plane_ids, result.plane_residuals]]) {
      const limit = Math.max(1e-12, ...residuals.map(Math.abs)); scales.push(limit);
      ids.forEach((id, i) => {
        const color = white.clone().lerp(residuals[i] < 0 ? blue : red, Math.abs(residuals[i]) / limit);
        colors.setXYZ(id, color.r, color.g, color.b);
      });
    }
    $('legend').textContent = `Blue: negative · white: zero · red: positive. Separate scales: cone ±${scales[0].toFixed(5)}, plane ±${scales[1].toFixed(5)} source units.`;
  } else if ($('colors').value === 'residual') $('legend').textContent = 'Fit the current selection to show residuals. Showing regions for now.';
  colors.needsUpdate = true;
  selectedPoints.geometry.setIndex([...session.lateral_ids, ...session.plane_ids]);
  selectedPoints.visible = $('points').checked;
  overlays.visible = $('guides').checked;
  $('counts').textContent = `${session.lateral_ids.length.toLocaleString()} cone · ${session.plane_ids.length.toLocaleString()} plane vertices`;
  $('undo').disabled = undo.length === 0;
  $('fit').disabled = busy || session.lateral_ids.length < 7 || session.plane_ids.length < 3;
  draw();
}
function change(next, remember = true) {
  if (remember) { undo.push(structuredClone(session)); if (undo.length > 30) undo.shift(); }
  session = next; revision++; result = null; clearGuides(); $('metrics').replaceChildren();
  status('Selection changed. Fit again to update the guides and residuals.'); paint();
}
function guides(data) {
  clearGuides();
  const axis = new THREE.Vector3(...data.axis_display), point = new THREE.Vector3(...data.point_display);
  const reference = Math.abs(axis.z) > .9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
  const u = new THREE.Vector3().crossVectors(axis, reference).normalize(), v = new THREE.Vector3().crossVectors(axis, u);
  const p = data.fit.parameters;
  const ring = (center, radius, color) => {
    const points = [];
    for (let i = 0; i < 128; i++) {
      const theta = i / 128 * Math.PI * 2;
      points.push(center.clone().addScaledVector(u, radius * Math.cos(theta)).addScaledVector(v, radius * Math.sin(theta)));
    }
    overlays.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color, depthTest: false, transparent: true, opacity: .85})));
  };
  for (const z of data.axial_domain) ring(point.clone().addScaledVector(axis, z), p[4] + p[6] * z, '#65dbe9');
  for (let i = 0; i < 12; i++) {
    const theta = i / 12 * Math.PI * 2;
    const points = data.axial_domain.map(z => point.clone().addScaledVector(axis, z).addScaledVector(u, (p[4] + p[6] * z) * Math.cos(theta)).addScaledVector(v, (p[4] + p[6] * z) * Math.sin(theta)));
    overlays.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color: '#65dbe9', depthTest: false, transparent: true, opacity: .55})));
  }
  const planePoint = new THREE.Vector3(...data.plane_point_display);
  ring(planePoint, 8.2, '#ed97e8'); ring(planePoint, 10.2, '#ed97e8');
}
async function fit() {
  if (busy) return;
  const submittedRevision = revision;
  busy = true; paint(); status('Fitting both surfaces… You can still orbit and inspect.');
  try {
    const job = await request('/api/fit', session);
    let state;
    do {
      await new Promise(resolve => setTimeout(resolve, 150));
      state = await request(`/api/fit/${job.job_id}`);
    } while (state.status === 'running');
    if (submittedRevision !== revision) { status('The selection changed during fitting. Fit again for the current regions.'); return; }
    if (state.status === 'failed') throw new Error(state.error);
    result = state.result; guides(result);
    $('metrics').replaceChildren();
    const values = {
      'Reference diameter': result.reference_diameter.toFixed(5),
      'Signed half-angle': `${result.signed_half_angle_degrees.toFixed(4)}°`,
      'Cone RMS': result.fit.cone_weighted_rms.toFixed(5),
      'Plane RMS': result.fit.plane_weighted_rms.toFixed(5),
      'Combined RMS': result.fit.weighted_rms.toFixed(5),
    };
    for (const [label, value] of Object.entries(values)) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = value; $('metrics').append(dt, dd);
    }
    status('Fit complete. Cyan: cone guide · pink: plane guide. Guides show through the mesh; extents are illustrative.');
  } catch (error) { status(error.message, true); }
  finally { busy = false; paint(); }
}
function home(direction = null) {
  mesh.geometry.computeBoundingSphere();
  const sphere = mesh.geometry.boundingSphere;
  const distance = sphere.radius / Math.sin(Math.min(camera.fov * Math.PI / 360, Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect))) * 1.12;
  const vector = direction === null ? camera.getWorldDirection(new THREE.Vector3()).negate() : direction === 'top' ? new THREE.Vector3(0, -.001, 1) : direction === 'side' ? new THREE.Vector3(0, -1, 0) : new THREE.Vector3(1, -1.5, .8);
  if (direction !== null) camera.up.set(0, 0, 1);
  controls.target.copy(sphere.center); camera.position.copy(sphere.center).addScaledVector(vector.normalize(), distance);
  camera.near = Math.max(.001, sphere.radius / 1000); camera.far = sphere.radius * 1000;
  camera.updateProjectionMatrix(); controls.update(); draw();
}
async function start() {
  metadata = await request('/api/meta');
  const [pb, ib] = await Promise.all([metadata.positions.url, metadata.indices.url].map(async url => {
    const response = await fetch(url); if (!response.ok) throw new Error('Could not load mesh buffers'); return response.arrayBuffer();
  }));
  positions = new Float32Array(pb); session = structuredClone(metadata.session);
  renderer = new THREE.WebGLRenderer({antialias: true}); renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  viewport.append(renderer.domElement); renderer.domElement.tabIndex = 0; renderer.domElement.setAttribute('aria-label', 'Nozzle 3D view');
  scene = new THREE.Scene(); scene.background = new THREE.Color('#17232e');
  camera = new THREE.PerspectiveCamera(45, 1, .01, 1000); camera.up.set(0, 0, 1);
  const raycaster = new THREE.Raycaster();
  controls = onshapeNavigation(camera, renderer.domElement, draw, (event, fallback) => {
    if (!mesh) return null;
    const bounds = renderer.domElement.getBoundingClientRect();
    camera.updateMatrixWorld(); mesh.updateMatrixWorld();
    raycaster.near = camera.near; raycaster.far = camera.far;
    raycaster.setFromCamera(new THREE.Vector2((event.clientX - bounds.left) / bounds.width * 2 - 1, 1 - (event.clientY - bounds.top) / bounds.height * 2), camera);
    const hit = raycaster.intersectObject(mesh, false)[0];
    return hit?.point ?? (fallback ? viewPlaneAnchor(raycaster.ray, camera, controls.target) : null);
  });
  $('cursor-zoom').onchange = () => { controls.options.zoomAtCursor = $('cursor-zoom').checked; };
  $('cursor-rotate').onchange = () => { controls.options.rotateAtCursor = $('cursor-rotate').checked; };
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(ib), 1)); geometry.computeVertexNormals();
  geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(positions.length), 3));
  mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({vertexColors: true, side: THREE.DoubleSide, roughness: .85})); scene.add(mesh);
  const pointGeometry = new THREE.BufferGeometry(); pointGeometry.setAttribute('position', geometry.getAttribute('position')); pointGeometry.setAttribute('color', geometry.getAttribute('color'));
  selectedPoints = new THREE.Points(pointGeometry, new THREE.PointsMaterial({vertexColors: true, size: 3, sizeAttenuation: false})); scene.add(selectedPoints);
  scene.add(new THREE.HemisphereLight('#ffffff', '#738396', 2)); const light = new THREE.DirectionalLight('#ffffff', 2.5); light.position.set(15, -20, 30); scene.add(light);
  overlays = new THREE.Group(); scene.add(overlays);
  const resize = () => { renderer.setSize(viewport.clientWidth, viewport.clientHeight); camera.aspect = viewport.clientWidth / viewport.clientHeight; camera.updateProjectionMatrix(); controls.resize(); draw(); };
  new ResizeObserver(resize).observe(viewport); resize(); home('oblique');
  $('mesh-info').textContent = `${metadata.vertices.toLocaleString()} vertices · ${metadata.triangles.toLocaleString()} triangles`;
  $('save').disabled = false;
  $('tool').onchange = () => { renderer.domElement.style.cursor = $('tool').value === 'orbit' ? 'default' : 'crosshair'; };
  $('fit').onclick = fit;
  $('undo').onclick = () => { if (undo.length) change(undo.pop(), false); };
  $('reset').onclick = () => change(structuredClone(metadata.session));
  $('home').onclick = () => home(); $('side').onclick = () => home('side'); $('top').onclick = () => home('top');
  for (const name of ['colors', 'guides', 'points']) $(name).onchange = paint;
  $('save').onclick = async () => {
    try {
      const validated = await request('/api/session', session);
      const url = URL.createObjectURL(new Blob([JSON.stringify(validated, null, 2) + '\n'], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'nozzle-selection.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      status('Session downloaded. It can be loaded here or passed to the Python fitting adapter.');
    } catch (error) { status(error.message, true); }
  };
  $('load').onclick = () => $('file').click();
  $('file').onchange = async () => {
    try {
      const file = $('file').files[0]; if (!file) return;
      if (file.size > 1_000_000) throw new Error('Session must be under 1 MB');
      change(await request('/api/session', JSON.parse(await file.text())));
    } catch (error) { status(error.message, true); }
    finally { $('file').value = ''; }
  };
  let startPoint = null;
  const xy = event => { const r = renderer.domElement.getBoundingClientRect(); return [event.clientX - r.left, event.clientY - r.top]; };
  renderer.domElement.addEventListener('pointerdown', event => {
    if ($('tool').value === 'orbit' || event.button !== 0) return;
    startPoint = xy(event); renderer.domElement.setPointerCapture(event.pointerId);
  });
  renderer.domElement.addEventListener('pointermove', event => {
    if (!startPoint) return; const end = xy(event), box = $('rectangle'); box.hidden = false;
    Object.assign(box.style, {left: `${Math.min(startPoint[0], end[0])}px`, top: `${Math.min(startPoint[1], end[1])}px`, width: `${Math.abs(startPoint[0] - end[0])}px`, height: `${Math.abs(startPoint[1] - end[1])}px`});
  });
  renderer.domElement.addEventListener('pointerup', event => {
    if (!startPoint) return;
    const end = xy(event), rect = {left: Math.min(startPoint[0], end[0]), right: Math.max(startPoint[0], end[0]), top: Math.min(startPoint[1], end[1]), bottom: Math.max(startPoint[1], end[1])};
    startPoint = null; $('rectangle').hidden = true;
    camera.updateMatrixWorld(); const point = new THREE.Vector3();
    const hits = rectangleHits(positions, xyz => { point.fromArray(xyz).project(camera); return [(point.x + 1) / 2 * viewport.clientWidth, (1 - point.y) / 2 * viewport.clientHeight, point.z]; }, rect);
    change(editSelection(session, $('region').value, hits, $('tool').value));
  });
  renderer.domElement.addEventListener('pointercancel', () => { startPoint = null; $('rectangle').hidden = true; });
  paint(); status('Saved regions loaded. Ready to fit.');
}
start().catch(error => status(`Could not start viewer: ${error.message}`, true));
