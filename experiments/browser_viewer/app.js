import * as THREE from 'three';
import { onshapeNavigation } from './navigation.js';
import { viewPlaneAnchor } from './navigation-math.js';
import { editSelectionGroups, selectionProjection, brushHits } from './selection.js';

const $ = id => document.getElementById(id);
const viewport = $('viewport');
let renderer, scene, camera, controls, mesh, selectedPoints, overlays;
let metadata, positions, session, result = null, busy = false;
let pending = false, frames = 0;
let graphState, bindings, selectedFeatureId;
let selectionDrawing = false, selectionPending = false;
let previewGrowthId = null, editingSeedFor = null, growthSettingsFor = null;
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
function graphNode(id) { return graphState.recipe.nodes.find(n => n.id === id); }
function seedNode(selectionId) {
  const node = graphNode(selectionId);
  return node.operation === 'growth' ? graphNode(graphNode(node.seed_fit).selection) : node;
}
function growthFor(surface) {
  const selected = graphNode(surface.selection);
  return graphState.recipe.nodes.find(n => n.operation === 'growth' && n.id !== selected.id && graphNode(n.seed_fit).selection === seedNode(surface.selection).id && !graphState.recipe.nodes.some(other => other.selection === n.id || other.barriers?.includes(n.id))) || (selected.operation === 'growth' ? selected : null);
}
function updateGrowthPanel() {
  const surface = bindings.surfaces.find(s => s.id === $('region').value);
  if (!surface) return;
  const growth = growthFor(surface), preview = graphState.derived?.[previewGrowthId];
  const settingsKey = surface.id + ':' + (growth?.id || 'none');
  if (growthSettingsFor !== settingsKey) {
    growthSettingsFor = settingsKey;
    if (growth) { $('growth-distance').value = growth.distance; $('growth-angle').value = growth.angle_degrees; }
  }
  $('propose-growth').disabled = busy || selectionDrawing || selectionPending;
  $('apply-growth').disabled = busy || selectionDrawing || selectionPending || !preview || growth?.id !== previewGrowthId;
  $('edit-growth-seed').hidden = graphNode(surface.selection).operation !== 'growth';
  $('edit-growth-seed').textContent = editingSeedFor === surface.id ? 'Show grown selection' : 'Edit seed';
  if (preview && growth?.id === previewGrowthId) {
    const fitted = graphState.derived[ growth.seed_fit ];
    $('growth-status').textContent = `${preview.added_ids.length} proposed additions · seed RMS ${fitted.weighted_rms.toFixed(5)} · ${preview.rejected_seed_ids.length} seed vertices outside thresholds.`;
  } else if (growth && graphState.errors[growth.id]) $('growth-status').textContent = graphState.errors[growth.id];
  else if (graphNode(surface.selection).operation === 'growth') $('growth-status').textContent = editingSeedFor === surface.id ? 'Editing the retained seed. Growth and final fit will become stale.' : 'Using a derived selection. Choose Edit seed to change its input.';
  else $('growth-status').textContent = 'No proposal preview. Paint seeds, then fit and propose.';
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
  const preview = graphState.derived?.[previewGrowthId];
  if (preview) {
    const green = new THREE.Color('#4dff91');
    for (const id of preview.added_ids) colors.setXYZ(id, green.r, green.g, green.b);
    $('legend').textContent = 'Bright green: proposed additions. Existing selection colors: seeds/current membership.';
  }
  colors.needsUpdate = true;
  selectedPoints.geometry.setIndex([...Object.values(session).flat(), ...(preview?.added_ids || [])]);
  selectedPoints.visible = $('points').checked;
  overlays.visible = $('guides').checked;
  $('counts').textContent = bindings.surfaces.map(s => `${s.label}: ${session[s.id].length.toLocaleString()}`).join(' · ');

  $('fit').disabled = busy || selectionDrawing || selectionPending || bindings.surfaces.some(s => session[s.id].length < (s.kind === 'plane' ? 3 : 7));
  updateGrowthPanel(); draw();
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
  session = Object.fromEntries(bindings.surfaces.map(n => [n.id, editingSeedFor === n.id ? seedNode(n.selection).ids : (state.memberships[n.selection] ?? seedNode(n.selection).ids)]));
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
    const inputs = node.operation === 'joint_fit' ? node.constraints : node.operation === 'coaxial' ? [node.surface, node.reference] : node.operation === 'perpendicular' ? [node.lateral,node.plane] : ['surface','seed_fit'].includes(node.operation) ? [node.selection] : node.operation === 'growth' ? [node.seed_fit, ...node.barriers] : node.operation === 'growth' ? 'Connected growth · uses the preliminary seed fit and treats referenced selections as barriers.'
    : node.operation === 'seed_fit' ? 'Preliminary fit · fitted only to seed observations, without final-fit constraints.'
    : node.operation === 'selection' ? [node.source] : [];
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
    : node.operation === 'growth' ? 'Connected growth · uses the preliminary seed fit and treats referenced selections as barriers.'
    : node.operation === 'seed_fit' ? 'Preliminary fit · fitted only to seed observations, without final-fit constraints.'
    : node.operation === 'selection' ? `Selection · ${node.ids.length} vertices. Edit membership with the selection tools.`
    : 'Source · captured mesh and reference binding.';
  $('feature-diagnostics').replaceChildren();
  const diagnostic = graphState.derived?.[node.id];
  if (node.operation === 'seed_fit' && diagnostic) {
    const p = diagnostic.parameters;
    const values = {'Seed RMS': diagnostic.weighted_rms.toFixed(5), 'Condition': diagnostic.condition.toExponential(3)};
    if (node.kind === 'plane') { values['Normal'] = p.slice(0,3).map(v => v.toFixed(5)).join(', '); values['Plane offset'] = p[3].toFixed(5); }
    else { values['Reference diameter'] = (2*p[4]).toFixed(5); values['Half-angle'] = (Math.atan(p[6])*180/Math.PI).toFixed(4) + '°'; values['Axis slopes'] = p.slice(2,4).map(v => v.toFixed(5)).join(', '); }
    for (const [label, value] of Object.entries(values)) { const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = label; dd.textContent = value; $('feature-diagnostics').append(dt,dd); }
  }
  if (node.operation === 'growth') {
    previewGrowthId = null;
    $('growth-distance').value = node.distance; $('growth-angle').value = node.angle_degrees;
    const surface = bindings.surfaces.find(s => seedNode(s.selection).id === graphNode(node.seed_fit).selection);
    if (surface) $('region').value = surface.id;
    updateGrowthPanel(); paint();
  }
  if (!isSurfaceFit) return;
  $('feature-label').value = node.label;
  $('surface-kind').value = node.kind;
  const asPlane = nodes.some(n => n.operation === 'perpendicular' && n.plane === node.id);
  const asLateral = nodes.some(n => (n.operation === 'perpendicular' && n.lateral === node.id) || (n.operation === 'coaxial' && [n.surface, n.reference].includes(node.id)));
  for (const option of $('surface-kind').options) option.disabled = (asPlane && option.value !== 'plane') || (asLateral && option.value === 'plane');
  $('fit-compatibility').textContent = asPlane
    ? 'This constraint currently requires a plane here. Other pairings need additional solver support.'
    : asLateral ? 'This joint group supports coaxial cones and cylinders, with a perpendicular plane.' : 'Available fit types: cone, cylinder and plane.';
  $('feature-selection').replaceChildren(...nodes.filter(n => ['selection','growth'].includes(n.operation)).map(n => new Option(n.label, n.id)));
  $('feature-selection').value = node.selection;
  $('axial-properties').hidden = node.kind === 'plane';
  $('axial-start').value = node.axial_domain[0]; $('axial-end').value = node.axial_domain[1];
  if (bindings.surfaces.some(s => s.id === node.id)) $('region').value = node.id;
  updateGrowthPanel();
}
async function replaceRecipe(recipe) {
  try {
    acceptGraph(await request('/api/graph', {token: graphState.token, recipe}));
    status('Graph updated. Evaluate to recompute dependent results.');
  } catch (error) {
    acceptGraph(await request('/api/graph')); status(error.message, true);
  }
}
async function change(next, region, depth) {
  const recipe = structuredClone(graphState.recipe);
  for (const surface of bindings.surfaces) {
    const selected = graphNode(surface.selection);
    if (selected.operation === 'growth' && editingSeedFor !== surface.id) {
      if (JSON.stringify(next[surface.id]) !== JSON.stringify(graphState.memberships[surface.selection])) throw new Error('This stroke overlaps another derived selection; edit its seed separately.');
      continue;
    }
    recipe.nodes.find(n => n.id === seedNode(surface.selection).id).ids = next[surface.id];
  }
  const active = bindings.surfaces.find(s => s.id === region);
  recipe.nodes.find(n => n.id === seedNode(active.selection).id).depth = depth;
  previewGrowthId = null;
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
    const source = seedNode(reference.selection).source;
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
  for (const id of ['growth-distance', 'growth-angle']) $(id).addEventListener('input', () => { previewGrowthId = null; paint(); });
  $('propose-growth').onclick = async () => {
    if (busy || selectionPending || selectionDrawing) return;
    const surface = bindings.surfaces.find(s => s.id === $('region').value);
    const distance = Number($('growth-distance').value), angle = Number($('growth-angle').value);
    if (!(distance > 0) || !(angle > 0 && angle <= 90)) { status('Enter a positive distance and an angle between 0 and 90 degrees.', true); return; }
    busy = true; previewGrowthId = null; paint();
    try {
      const recipe = structuredClone(graphState.recipe), seed = seedNode(surface.selection);
      const found = growthFor(surface);
      const existing = found?.id === surface.selection ? null : found;
      const seedFitId = existing?.seed_fit || 'seedfit_' + crypto.randomUUID().replaceAll('-', '');
      const growthId = existing?.id || 'growth_' + crypto.randomUUID().replaceAll('-', '');
      const records = [
        {id: seedFitId, label: (surface.label + ' seed fit').slice(0,120), operation: 'seed_fit', selection: seed.id, kind: surface.kind, axial_domain: surface.axial_domain},
        {id: growthId, label: (surface.label + ' growth').slice(0,120), operation: 'growth', seed_fit: seedFitId, barriers: bindings.surfaces.filter(s => s.id !== surface.id).map(s => s.selection), distance, angle_degrees: angle},
      ];
      for (const node of records) { const index = recipe.nodes.findIndex(n => n.id === node.id); if (index < 0) recipe.nodes.push(node); else recipe.nodes[index] = node; }
      acceptGraph(await request('/api/graph', {token: graphState.token, recipe}));
      await request('/api/graph/evaluate', {token: graphState.token, target: growthId});
      let state;
      do { await new Promise(resolve => setTimeout(resolve,150)); state = await request('/api/graph'); acceptGraph(state); } while (state.evaluation_running);
      if (state.evaluation_error) throw new Error(state.evaluation_error);
      if (!state.derived[growthId]) throw new Error('Proposal became stale; fit and propose again.');
      previewGrowthId = growthId; editingSeedFor = null; acceptGraph(state);
      status('Proposal ready. Inspect the green additions, then Apply if useful.');
    } catch (error) { status(error.message, true); }
    finally { busy = false; paint(); }
  };
  $('apply-growth').onclick = async () => {
    const surface = bindings.surfaces.find(s => s.id === $('region').value);
    if (!graphState.derived?.[previewGrowthId] || growthFor(surface)?.id !== previewGrowthId) return;
    const recipe = structuredClone(graphState.recipe);
    const previous = graphNode(surface.selection);
    recipe.nodes.find(n => n.id === surface.id).selection = previewGrowthId;
    if (previous.operation === 'growth') {
      for (const id of [previous.id, previous.seed_fit]) {
        if (!recipe.nodes.some(n => n.selection === id || n.seed_fit === id || n.barriers?.includes(id))) recipe.nodes = recipe.nodes.filter(n => n.id !== id);
      }
    }
    previewGrowthId = null; editingSeedFor = null;
    await replaceRecipe(recipe);
  };
  $('hide-growth').onclick = () => { previewGrowthId = null; paint(); };
  $('edit-growth-seed').onclick = () => {
    const id = $('region').value;
    editingSeedFor = editingSeedFor === id ? null : id;
    previewGrowthId = null; acceptGraph(graphState);
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
  let stroke = null, projectionCache = null;
  const canvas = renderer.domElement;
  const xy = event => { const r = canvas.getBoundingClientRect(); return [event.clientX - r.left, event.clientY - r.top]; };
  const cursor = event => {
    const brush = $('brush-cursor'), diameter = Number($('brush-size').value), point = xy(event);
    brush.hidden = $('selection-shape').value !== 'paint' || $('tool').value === 'orbit' || (event.buttons && !(event.buttons & 1));
    Object.assign(brush.style, {left: `${point[0] - diameter / 2}px`, top: `${point[1] - diameter / 2}px`, width: `${diameter}px`, height: `${diameter}px`});
  };
  const cancelStroke = () => {
    if (!stroke) return;
    const pointerId = stroke.pointerId;
    stroke = null; selectionDrawing = false; $('rectangle').hidden = true;
    if (canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);
    acceptGraph(graphState); status('Selection gesture cancelled.');
  };
  const preview = () => {
    session = editSelectionGroups(stroke.original, stroke.region, stroke.hits, stroke.operation);
    result = null; clearGuides(); $('metrics').replaceChildren(); paint();
  };
  const extend = point => {
    if (stroke.shape === 'paint') {
      for (const id of brushHits(stroke.projection, stroke.previous, point, stroke.radius, stroke.depth)) stroke.hits.add(id);
      stroke.previous = point; preview();
    } else {
      const start = stroke.start;
      Object.assign($('rectangle').style, {left: `${Math.min(start[0], point[0])}px`, top: `${Math.min(start[1], point[1])}px`, width: `${Math.abs(start[0] - point[0])}px`, height: `${Math.abs(start[1] - point[1])}px`});
      $('rectangle').hidden = false;
    }
  };
  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0) { cancelStroke(); return; }
    if ($('tool').value === 'orbit' || busy || selectionPending) return;
    if (graphNode(bindings.surfaces.find(s => s.id === $('region').value).selection).operation === 'growth' && editingSeedFor !== $('region').value) { status('Choose Edit seed before painting a derived selection.', true); return; }
    previewGrowthId = null;
    cancelStroke(); event.preventDefault(); canvas.focus();
    camera.updateMatrixWorld(); mesh.updateMatrixWorld();
    const matrix = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(mesh.matrixWorld);
    const key = [...matrix.elements, viewport.clientWidth, viewport.clientHeight, $('selection-depth').value].join(',');
    if (projectionCache?.key !== key) {
      const clip = new Float64Array(positions.length / 3 * 4), vector = new THREE.Vector4();
      for (let i = 0; i < positions.length / 3; i++) {
        vector.set(positions[3*i], positions[3*i+1], positions[3*i+2], 1).applyMatrix4(matrix); vector.toArray(clip, i*4);
      }
      projectionCache = {key, value: selectionProjection(clip, geometry.index.array, viewport.clientWidth, viewport.clientHeight, $('selection-depth').value === 'first_surface')};
    }
    const point = xy(event);
    stroke = {pointerId: event.pointerId, original: structuredClone(session), region: $('region').value, operation: $('tool').value,
      shape: $('selection-shape').value, depth: $('selection-depth').value, radius: Number($('brush-size').value) / 2,
      projection: projectionCache.value, start: point, previous: point, hits: new Set()};
    status('Selection preview. Release to apply; Escape to cancel.');
    selectionDrawing = true; canvas.setPointerCapture(event.pointerId); extend(point); paint();
  });
  canvas.addEventListener('pointermove', event => {
    cursor(event);
    if (stroke && stroke.pointerId === event.pointerId) extend(xy(event));
  });
  canvas.addEventListener('pointerleave', () => { $('brush-cursor').hidden = true; });
  canvas.addEventListener('pointerup', async event => {
    if (!stroke || stroke.pointerId !== event.pointerId || event.button !== 0) return;
    const end = xy(event); extend(end);
    if (stroke.shape === 'rectangle') {
      const a = stroke.start;
      stroke.projection.points.forEach((p, id) => {
        if (p && p[0] >= Math.min(a[0], end[0]) && p[0] <= Math.max(a[0], end[0]) && p[1] >= Math.min(a[1], end[1]) && p[1] <= Math.max(a[1], end[1]) && (stroke.depth === 'through_all' || stroke.projection.visible(id))) stroke.hits.add(id);
      });
    }
    const completed = stroke;
    stroke = null; selectionDrawing = false; selectionPending = true; $('rectangle').hidden = true;
    document.querySelector('aside').inert = true;
    status('Saving selection…');
    try { await change(editSelectionGroups(completed.original, completed.region, completed.hits, completed.operation), completed.region, completed.depth); }
    catch (error) { acceptGraph(graphState); status('Could not verify selection save. Reload the viewer: ' + error.message, true); }
    finally { selectionPending = false; document.querySelector('aside').inert = false; paint(); }
  });
  canvas.addEventListener('pointercancel', cancelStroke);
  canvas.addEventListener('lostpointercapture', cancelStroke);
  canvas.addEventListener('wheel', cancelStroke, {capture: true});
  window.addEventListener('blur', cancelStroke);
  window.addEventListener('resize', cancelStroke);
  window.addEventListener('keydown', event => { if (event.key === 'Escape') cancelStroke(); });
  for (const id of ['selection-shape', 'selection-depth', 'tool', 'region', 'brush-size']) {
    $(id).addEventListener('input', cancelStroke);
  }
  $('selection-shape').onchange = () => { $('brush-settings').hidden = $('selection-shape').value !== 'paint'; $('brush-cursor').hidden = true; };
  $('brush-size').oninput = () => { $('brush-size-value').textContent = $('brush-size').value + ' px'; };
  const state = await request('/api/graph'); acceptGraph(state); status('Feature graph loaded. Ready to evaluate.');
}
start().catch(error => status(`Could not start viewer: ${error.message}`, true));
