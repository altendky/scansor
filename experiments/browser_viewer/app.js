import * as THREE from 'three';
import { onshapeNavigation } from './navigation.js';
import { viewPlaneAnchor } from './navigation-math.js';
import { editSelectionGroups, rectangleHits } from './selection.js';

const $ = id => document.getElementById(id);
const viewport = $('viewport');
let renderer, scene, camera, controls, mesh, selectedPoints, overlays;
let metadata, positions, session, result = null, busy = false;
let pending = false, frames = 0;
let graphState, bindings, selectedFeatureId;
const palette = ['#f2b544', '#bd91f4', '#67dba2', '#ec9174', '#72b7ed', '#e6d979'];
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
  const gray = new THREE.Color('#8796a2');
  for (let i = 0; i < colors.count; i++) colors.setXYZ(i, gray.r, gray.g, gray.b);
  bindings.surfaces.forEach((surface, index) => {
    const color = new THREE.Color(palette[index % palette.length]);
    for (const id of session[surface.id]) colors.setXYZ(id, color.r, color.g, color.b);
  });
  $('legend').textContent = 'Selection colors match the active-surface menu. Gray: context.';
  if ($('colors').value === 'residual' && result) {
    const white = new THREE.Color('#f8f8f8'), blue = new THREE.Color('#1e55e6'), red = new THREE.Color('#d22828');
    const scales = [];
    for (const {ids, residuals} of Object.values(result.surfaces)) {
      const limit = Math.max(1e-12, ...residuals.map(Math.abs)); scales.push(limit);
      ids.forEach((id, i) => {
        const color = white.clone().lerp(residuals[i] < 0 ? blue : red, Math.abs(residuals[i]) / limit);
        colors.setXYZ(id, color.r, color.g, color.b);
      });
    }
    $('legend').textContent = `Blue: negative · white: zero · red: positive. Per-surface scales: ${scales.map(s => '±' + s.toFixed(5)).join(', ')} source units.`;
  } else if ($('colors').value === 'residual') $('legend').textContent = 'Fit the current selection to show residuals. Showing regions for now.';
  colors.needsUpdate = true;
  selectedPoints.geometry.setIndex(Object.values(session).flat());
  selectedPoints.visible = $('points').checked;
  overlays.visible = $('guides').checked;
  $('counts').textContent = bindings.surfaces.map(s => `${s.label}: ${session[s.id].length.toLocaleString()}`).join(' · ');

  $('fit').disabled = busy || bindings.surfaces.some(s => session[s.id].length < (s.kind === 'plane' ? 3 : 7));
  draw();
}
function acceptGraph(state) {
  graphState = state;
  const nodes = Object.fromEntries(state.recipe.nodes.map(node => [node.id, node]));
  const fitNode = nodes[state.recipe.output], constraints = fitNode.constraints.map(id => nodes[id]);
  const relationship = constraints.find(n => n.operation === 'perpendicular');
  const lateral = nodes[relationship.lateral], plane = nodes[relationship.plane];
  const ids = new Set([lateral.id, plane.id]);
  for (const c of constraints) for (const id of c.operation === 'coaxial' ? [c.surface, c.reference] : [c.lateral, c.plane]) ids.add(id);
  bindings = {lateral, plane, surfaces: [...ids].map(id => nodes[id])};
  session = Object.fromEntries(bindings.surfaces.map(n => [n.id, nodes[n.selection].ids]));
  const active = $('region').value;
  $('region').replaceChildren(...bindings.surfaces.map((n, i) => new Option(n.label + ' · ' + ['amber','violet','green','salmon','blue','yellow'][i % palette.length], n.id)));
  if (ids.has(active)) $('region').value = active;
  $('new-surface-reference').replaceChildren(...bindings.surfaces.filter(n => n.kind !== 'plane').map(n => new Option(n.label, n.id)));
  result = state.result; clearGuides(); $('metrics').replaceChildren();
  if (result) showFit(result);
  if (!nodes[selectedFeatureId]) selectedFeatureId = lateral.id;
  $('fit').textContent = 'Evaluate ' + fitNode.label;
  const seen = new Set();
  const tree = id => {
    const node = nodes[id], li = document.createElement('li'), label = document.createElement('button');
    label.textContent = node.label + (seen.has(id) ? ' ↗' : '');
    label.type = 'button'; label.dataset.featureId = id;
    label.setAttribute('aria-pressed', String(id === selectedFeatureId));
    label.onclick = () => { selectedFeatureId = id; showProperties(); }; li.append(label);
    const info = document.createElement('small');
    info.textContent = (state.states[id] || 'unevaluated') + (node.kind ? ' · ' + node.kind : '') + (node.ids ? ' · ' + node.ids.length + ' vertices' : '');
    if (state.errors[id]) { info.textContent += ' · ' + state.errors[id]; info.className = 'failed'; }
    li.append(info); if (seen.has(id)) return li; seen.add(id);
    const inputs = node.operation === 'joint_fit' ? node.constraints : node.operation === 'coaxial' ? [node.surface, node.reference] : node.operation === 'perpendicular' ? [node.lateral,node.plane] : node.operation === 'surface' ? [node.selection] : node.operation === 'selection' ? [node.source] : [];
    if (inputs.length) { const ul = document.createElement('ul'); for (const input of inputs) ul.append(tree(input)); li.append(ul); }
    return li;
  };
  const ul = document.createElement('ul'); ul.append(tree(state.recipe.output));
  for (const node of state.recipe.nodes) if (!seen.has(node.id)) ul.append(tree(node.id));
  $('feature-tree').replaceChildren(ul); showProperties(); paint();
}
function showProperties() {
  const nodes = graphState.recipe.nodes, node = nodes.find(n => n.id === selectedFeatureId);
  for (const button of $('feature-tree').querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.featureId === selectedFeatureId));
  $('properties-title').textContent = node.label;
  const isSurfaceFit = node.operation === 'surface';
  $('feature-properties').hidden = !isSurfaceFit;
  $('constraint-properties').hidden = node.operation !== 'coaxial';
  if (node.operation === 'coaxial') {
    $('constraint-reference').replaceChildren(...nodes.filter(n => n.operation === 'surface' && n.kind !== 'plane' && n.id !== node.surface).map(n => new Option(n.label, n.id)));
    $('constraint-reference').value = node.reference;
  }
  $('feature-description').textContent = isSurfaceFit
    ? 'Surface fit · declares the geometry to fit to its input selection. The joint fit solves linked surfaces together.'
    : node.operation === 'joint_fit' ? 'Joint solve · evaluates the linked surface fits and their constraint.'
    : node.operation === 'coaxial' ? 'Coaxial constraint · both surfaces adjust together on one shared axis.'
    : node.operation === 'perpendicular' ? 'Constraint · aligns the plane normal with the cone or cylinder axis.'
    : node.operation === 'selection' ? `Selection · ${node.ids.length} vertices. Edit membership with the selection tools.`
    : 'Source · captured mesh and reference binding.';
  if (!isSurfaceFit) return;
  $('feature-label').value = node.label;
  $('surface-kind').value = node.kind;
  const asPlane = nodes.some(n => n.operation === 'perpendicular' && n.plane === node.id);
  const asLateral = nodes.some(n => (n.operation === 'perpendicular' && n.lateral === node.id) || (n.operation === 'coaxial' && [n.surface, n.reference].includes(node.id)));
  for (const option of $('surface-kind').options) option.disabled = (asPlane && option.value !== 'plane') || (asLateral && option.value === 'plane');
  $('fit-compatibility').textContent = asPlane
    ? 'This constraint currently requires a plane here. Other pairings need additional solver support.'
    : asLateral ? 'This joint group supports coaxial cones and cylinders, with a perpendicular plane.' : 'Available fit types: cone, cylinder and plane.';
  $('feature-selection').replaceChildren(...nodes.filter(n => n.operation === 'selection').map(n => new Option(n.label + ` (${n.ids.length} vertices)`, n.id)));
  $('feature-selection').value = node.selection;
  $('axial-properties').hidden = node.kind === 'plane';
  $('axial-start').value = node.axial_domain[0]; $('axial-end').value = node.axial_domain[1];
  if (bindings.surfaces.some(s => s.id === node.id)) $('region').value = node.id;
}
async function replaceRecipe(recipe) {
  try {
    acceptGraph(await request('/api/graph', {token: graphState.token, recipe}));
    status('Graph updated. Evaluate to recompute dependent results.');
  } catch (error) {
    acceptGraph(await request('/api/graph')); status(error.message, true);
  }
}
async function change(next) {
  const recipe = structuredClone(graphState.recipe);
  for (const surface of bindings.surfaces) recipe.nodes.find(n => n.id === surface.selection).ids = next[surface.id];
  await replaceRecipe(recipe);
}
function guides(data) {
  clearGuides();
  const axis = new THREE.Vector3(...data.axis_display), point = new THREE.Vector3(...data.point_display);
  const reference = Math.abs(axis.z) > .9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
  const u = new THREE.Vector3().crossVectors(axis, reference).normalize(), v = new THREE.Vector3().crossVectors(axis, u);
  const ring = (center, radius, color) => {
    const points = [];
    for (let i = 0; i < 128; i++) {
      const theta = i / 128 * Math.PI * 2;
      points.push(center.clone().addScaledVector(u, radius * Math.cos(theta)).addScaledVector(v, radius * Math.sin(theta)));
    }
    overlays.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color, depthTest: false, transparent: true, opacity: .85})));
  };
  for (const [id, surface] of Object.entries(data.surfaces)) {
    if (surface.kind === 'plane') continue;
    const p = surface.parameters;
    const color = palette[bindings.surfaces.findIndex(s => s.id === id) % palette.length];
  for (const z of surface.axial_domain) ring(point.clone().addScaledVector(axis, z), p[4] + p[6] * z, color);
  for (let i = 0; i < 12; i++) {
    const theta = i / 12 * Math.PI * 2;
    const points = surface.axial_domain.map(z => point.clone().addScaledVector(axis, z).addScaledVector(u, (p[4] + p[6] * z) * Math.cos(theta)).addScaledVector(v, (p[4] + p[6] * z) * Math.sin(theta)));
    overlays.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color: color, depthTest: false, transparent: true, opacity: .55})));
  }
  }
  const planePoint = new THREE.Vector3(...data.plane_point_display);
  ring(planePoint, 8.2, '#ed97e8'); ring(planePoint, 10.2, '#ed97e8');
}
function showFit(data) {
  guides(data); $('metrics').replaceChildren();
  const values = {'Combined RMS': data.fit.weighted_rms.toFixed(5)};
  for (const [id, surface] of Object.entries(data.surfaces)) {
    const label = bindings.surfaces.find(s => s.id === id).label;
    values[label + ' RMS'] = surface.weighted_rms.toFixed(5);
    if (surface.kind !== 'plane') {
      values[label + ' diameter'] = (2 * surface.parameters[4]).toFixed(5);
      if (surface.kind === 'cone') values[label + ' half-angle'] = (Math.atan(surface.parameters[6]) * 180 / Math.PI).toFixed(4) + '°';
    }
  }
  for (const [label,value] of Object.entries(values)) { const dt=document.createElement('dt'), dd=document.createElement('dd'); dt.textContent=label; dd.textContent=value; $('metrics').append(dt,dd); }
}
async function fit() {
  if (busy) return;
  busy = true; paint(); status('Evaluating the feature graph…');
  try {
    await request('/api/graph/evaluate', {token: graphState.token});
    let state;
    do {
      await new Promise(resolve => setTimeout(resolve, 150));
      state = await request('/api/graph'); acceptGraph(state);
    } while (state.evaluation_running);
    if (state.evaluation_error) throw new Error(state.evaluation_error);
    status(state.result ? 'Fit complete. Side guides match selection colors; pink shows the plane.' : 'Graph changed during evaluation. Evaluate the current graph again.');
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
  $('reset').onclick = async () => replaceRecipe(await request('/api/graph/example'));
  $('surface-kind').onchange = () => { $('axial-properties').hidden = $('surface-kind').value === 'plane'; };
  $('feature-properties').onsubmit = async event => {
    event.preventDefault();
    const recipe = structuredClone(graphState.recipe);
    const node = recipe.nodes.find(node => node.id === selectedFeatureId);
    node.label = $('feature-label').value; node.kind = $('surface-kind').value;
    node.selection = $('feature-selection').value;
    if (node.kind !== 'plane') node.axial_domain = [Number($('axial-start').value), Number($('axial-end').value)];
    await replaceRecipe(recipe);
  };
  $('region').onchange = () => { selectedFeatureId = $('region').value; showProperties(); };
  $('constraint-properties').onsubmit = async event => {
    event.preventDefault();
    const recipe = structuredClone(graphState.recipe);
    recipe.nodes.find(n => n.id === selectedFeatureId).reference = $('constraint-reference').value;
    await replaceRecipe(recipe);
  };
  $('add-surface-form').onsubmit = async event => {
    event.preventDefault();
    const recipe = structuredClone(graphState.recipe);
    const reference = recipe.nodes.find(n => n.id === $('new-surface-reference').value);
    const source = recipe.nodes.find(n => n.id === reference.selection).source;
    const id = 'surface_' + crypto.randomUUID().replaceAll('-', '');
    const label = $('new-surface-label').value;
    const zs = []; for (let i = 2; i < positions.length; i += 3) zs.push(positions[i]);
    const domain = [Math.min(...zs) - .5, Math.max(...zs) + .5];
    recipe.nodes.push(
      {id: id + '_selection', label: label + ' selection', operation: 'selection', source, ids: [], depth: 'through_all'},
      {id, label, operation: 'surface', selection: id + '_selection', kind: $('new-surface-kind').value, axial_domain: domain},
      {id: id + '_axis', label: label + ' coaxial', operation: 'coaxial', surface: id, reference: reference.id},
    );
    recipe.nodes.find(n => n.id === recipe.output).constraints.push(id + '_axis');
    selectedFeatureId = id;
    await replaceRecipe(recipe);
    if (graphState.recipe.nodes.some(n => n.id === id)) {
      $('add-surface-panel').open = false;
      $('tool').value = 'add'; $('tool').onchange();
      status('Surface added with an empty selection. Select its observations, then evaluate the joint fit.');
    }
  };
  $('home').onclick = () => home(); $('side').onclick = () => home('side'); $('top').onclick = () => home('top');
  for (const name of ['colors', 'guides', 'points']) $(name).onchange = paint;
  $('save').onclick = async () => {
    try {
      const validated = (await request('/api/graph')).recipe;
      const url = URL.createObjectURL(new Blob([JSON.stringify(validated, null, 2) + '\n'], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'nozzle-graph.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      status('Current graph downloaded. No change history is recorded.');
    } catch (error) { status(error.message, true); }
  };
  $('load').onclick = () => $('file').click();
  $('file').onchange = async () => {
    try {
      const file = $('file').files[0]; if (!file) return;
      if (file.size > 1_000_000) throw new Error('Session must be under 1 MB');
      await replaceRecipe(JSON.parse(await file.text()));
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
    change(editSelectionGroups(session, $('region').value, hits, $('tool').value));
  });
  renderer.domElement.addEventListener('pointercancel', () => { startPoint = null; $('rectangle').hidden = true; });
  const state = await request('/api/graph'); acceptGraph(state); status('Feature graph loaded. Ready to evaluate.');
}
start().catch(error => status(`Could not start viewer: ${error.message}`, true));
