import { actionDescription, nodeReferences as refs, renderActionTree } from './action-tree.js';
import * as THREE from 'three';
import { onshapeNavigation } from './navigation.js';
import { viewPlaneAnchor } from './navigation-math.js';
import {
  editSelectionGroups,
  selectionProjection,
  brushHits,
  featureVertexIds,
  mirrorFitInputs,
  rotationalFitInputs,
} from './selection.js';

const $ = (id) => document.getElementById(id);
const viewport = $('viewport');
let renderer, scene, camera, controls, mesh, selectedPoints, overlays;
let metadata,
  positions,
  session,
  result = null,
  busy = false;
let pending = false,
  frames = 0;
let graphState, selectedFeatureId;
let overlapMarkers, overlapHalo, activeOverlap, focusedPoints;
let selectionDrawing = false,
  selectionPending = false;
const palette = ['#f2b544', '#bd91f4', '#67dba2', '#ec9174', '#72b7ed', '#e6d979'];
const status = (message, error = false) => {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
};
async function request(path, value) {
  const response = await fetch(
    path,
    value === undefined
      ? {}
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Scansor-Request': '1' },
          body: JSON.stringify(value),
        },
  );
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
    $('render-stats').textContent =
      `${frames} frames · ${(performance.now() - start).toFixed(1)} ms submit · idle when unchanged`;
  });
}
function clearGuides() {
  for (const child of [...overlays.children]) {
    overlays.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
}
function graphNode(id) {
  return graphState.recipe.nodes.find((n) => n.id === id);
}
function factorAxis(node) {
  if (node?.operation === 'fit') return node.axis;
  if (node?.operation === 'mirror_symmetry') return graphNode(node.plane)?.axis;
  if (node?.operation === 'parallel') return graphNode(node.reference_plane)?.axis;
  if (node?.operation === 'equal') {
    const distance = [node.left, node.right].find((value) => value.measurement === 'plane_distance');
    return graphNode(distance?.reference_plane)?.axis;
  }
  return null;
}
const relationshipOperations = ['mirror_symmetry', 'parallel', 'equal'];
const solveInputs = (axis) =>
  graphState.recipe.nodes.filter(
    (node) =>
      (node.operation === 'fit' || relationshipOperations.includes(node.operation)) &&
      factorAxis(node) === axis,
  );
function activeSelection() {
  return graphState?.recipe.nodes.find(
    (node) => node.id === selectedFeatureId && node.operation === 'selection',
  );
}
function choices(id, nodes, selected = []) {
  $(id).replaceChildren(
    ...nodes.map((n) => new Option(n.label, n.id, false, selected.includes(n.id))),
  );
}
function axisChoices(id, nodes, selected = [], standalone = false) {
  choices(id, nodes, selected);
  if (standalone)
    $(id).prepend(new Option('No reference axis (standalone)', '', false, !selected.length));
}
function showAxisInitializer(modeId, sourceFieldsId, manualFieldsId) {
  const manual = $(modeId).value === 'free';
  $(sourceFieldsId).hidden = manual;
  $(manualFieldsId).hidden = !manual;
  for (const control of $(sourceFieldsId).querySelectorAll('input, select'))
    control.disabled = manual;
  for (const control of $(manualFieldsId).querySelectorAll('input, select'))
    control.disabled = !manual;
}
const chosen = (id) => [...$(id).selectedOptions].map((o) => o.value);
const uid = (prefix) => prefix + '_' + crypto.randomUUID().replaceAll('-', '');
function editMembership(groups, region, hits, operation) {
  return {
    ...groups,
    ...editSelectionGroups({ [region]: groups[region] }, region, hits, operation),
  };
}
async function change(next, region, depth) {
  const recipe = structuredClone(graphState.recipe),
    node = recipe.nodes.find((n) => n.id === region);
  node.ids = next[region];
  node.depth = depth;
  await replaceRecipe(recipe);
}
async function replaceRecipe(recipe, autoEvaluate = true) {
  try {
    acceptGraph(await request('/api/graph', { token: graphState.token, recipe }));
    status('Actions updated. Evaluate to refresh dependent results.');
    if (autoEvaluate && $('auto-evaluate').checked) setTimeout(() => void evaluateAll(), 0);
    return true;
  } catch (error) {
    acceptGraph(await request('/api/graph'));
    status(error.message, true);
    return false;
  }
}
async function appendActions(nodes, autoEvaluate = true) {
  const recipe = structuredClone(graphState.recipe);
  recipe.nodes.push(...nodes);
  recipe.output = nodes.at(-1).id;
  selectedFeatureId = recipe.output;
  const saved = await replaceRecipe(recipe, autoEvaluate);
  if (saved)
    $('action-list')
      .querySelector(`[data-action-id="${CSS.escape(selectedFeatureId)}"].action-select`)
      ?.scrollIntoView({ block: 'nearest' });
  return saved;
}
function acceptGraph(state) {
  graphState = state;
  if (!graphNode(selectedFeatureId)) selectedFeatureId = state.recipe.output;
  session = Object.fromEntries(
    state.recipe.nodes.filter((n) => n.operation === 'selection').map((n) => [n.id, n.ids]),
  );
  choices(
    'new-fit-inputs',
    state.recipe.nodes.filter((n) => ['selection', 'growth'].includes(n.operation)),
    [selectedFeatureId],
  );
  const fits = state.recipe.nodes.filter((n) => n.operation === 'fit');
  const axes = state.recipe.nodes.filter((n) => n.operation === 'axis');
  axisChoices('new-fit-axis', axes, [], true);
  choices('new-reference-plane-axis', axes);
  choices(
    'new-mirror-plane',
    state.recipe.nodes.filter((n) => n.operation === 'reference_plane'),
  );
  choices(
    'new-axis-source',
    fits.filter((n) => ['cone', 'cylinder'].includes(n.kind) && !n.axis),
  );
  choices('new-axis-solve-axis', axes);
  choices(
    'new-joint-side',
    fits.filter((n) => n.kind !== 'plane' && !n.axis),
  );
  choices(
    'new-joint-plane',
    fits.filter((n) => n.kind === 'plane' && !n.axis),
  );
  choices(
    'new-joint-extra',
    fits.filter((n) => n.kind !== 'plane' && !n.axis),
  );
  renderActions();
  showProperties();
  showResult();
  paint();
}
function renderActions() {
  renderActionTree($('action-list'), {
    nodes: graphState.recipe.nodes,
    selected: selectedFeatureId,
    states: graphState.states,
    errors: graphState.errors,
    locked: () => busy || selectionDrawing || selectionPending || graphState.evaluation_running,
    select: (id) => {
      if (selectionDrawing || selectionPending) return;
      selectedFeatureId = id;
      $('feature-properties-panel').scrollTop = 0;
      renderActions();
      showProperties();
      showResult();
      paint();
    },
    move: (nodes) => replaceRecipe({ ...structuredClone(graphState.recipe), nodes }),
    announce: (message, error = false) => {
      $('action-announcement').textContent = message;
      status(message, error);
    },
  });
}
function showProperties() {
  const node = graphNode(selectedFeatureId),
    earlier = graphState.recipe.nodes.slice(0, graphState.recipe.nodes.indexOf(node));
  $('selection-tools').hidden = node.operation !== 'selection';
  $('feature-inspection').hidden = [
    'source',
    'selection',
    ...relationshipOperations,
  ].includes(node.operation);
  if (node.operation === 'selection') $('selection-depth').value = node.depth;
  $('properties-title').textContent = node.label;
  $('feature-state').textContent = actionDescription(
    node, graphState.states[node.id], graphState.errors[node.id],
  );
  $('action-label').value = node.label;
  $('feature-description').textContent = refs(node).length
    ? 'Inputs: ' +
      refs(node)
        .map((id) => graphNode(id).label)
        .join(', ')
    : node.operation === 'source'
      ? 'Captured source mesh.'
      : 'No earlier action inputs.';
  for (const [id, enabled] of [
    ['fit-properties', node.operation === 'fit'],
    ['axis-properties', node.operation === 'axis'],
    ['reference-plane-properties', node.operation === 'reference_plane'],
    ['axis-solve-properties', node.operation === 'axis_solve'],
    ['growth-properties', node.operation === 'growth'],
    ['constraint-properties', ['coaxial', 'perpendicular'].includes(node.operation)],
    ['joint-properties', node.operation === 'joint_fit'],
    ['rotation-properties', node.operation === 'rotational_symmetry'],
    ['mirror-properties', node.operation === 'mirror_symmetry'],
    ['parallel-properties', node.operation === 'parallel'],
    ['equal-properties', node.operation === 'equal'],
  ])
    $(id).hidden = !enabled;
  if (node.operation === 'fit') {
    $('surface-kind').value = node.kind;
    choices(
      'fit-inputs',
      earlier.filter((n) => ['selection', 'growth'].includes(n.operation)),
      node.selections,
    );
    axisChoices(
      'fit-axis',
      earlier.filter((n) => n.operation === 'axis'),
      node.axis ? [node.axis] : [],
      true,
    );
    $('axial-start').value = node.axial_domain[0];
    $('axial-end').value = node.axial_domain[1];
  } else if (node.operation === 'axis') {
    $('axis-init-mode').value = node.source_fit ? 'fit' : 'free';
    choices(
      'axis-source-fit',
      earlier.filter(
        (n) => n.operation === 'fit' && ['cone', 'cylinder'].includes(n.kind) && !n.axis,
      ),
      node.source_fit ? [node.source_fit] : [],
    );
    const initial = node.initial_parameters || [0, 0, 0, 0];
    ['axis-point-x', 'axis-point-y', 'axis-direction-x', 'axis-direction-y'].forEach(
      (id, index) => ($(id).value = initial[index]),
    );
    showAxisInitializer('axis-init-mode', 'axis-source-fields', 'axis-manual-fields');
  } else if (node.operation === 'reference_plane') {
    choices(
      'reference-plane-axis',
      earlier.filter((n) => n.operation === 'axis'),
      [node.axis],
    );
    $('reference-plane-angle').value = node.initial_angle_degrees;
  } else if (node.operation === 'axis_solve') {
    choices(
      'axis-solve-axis',
      earlier.filter((n) => n.operation === 'axis'),
      [node.axis],
    );
    choices(
      'axis-solve-factors',
      earlier.filter(
        (n) =>
          (n.operation === 'fit' || relationshipOperations.includes(n.operation)) &&
          factorAxis(n) === node.axis,
      ),
      node.factors,
    );
  } else if (node.operation === 'growth') {
    choices(
      'growth-fit',
      earlier.filter((n) => n.operation === 'fit' && !n.axis),
      [node.seed_fit],
    );
    choices(
      'growth-barriers',
      earlier.filter((n) => ['selection', 'growth'].includes(n.operation)),
      node.barriers,
    );
    $('growth-distance').value = node.distance;
    $('growth-angle').value = node.angle_degrees;
  } else if (['coaxial', 'perpendicular'].includes(node.operation)) {
    choices(
      'constraint-a',
      earlier.filter((n) => n.operation === 'fit' && n.kind !== 'plane' && !n.axis),
      [node.surface || node.lateral],
    );
    choices(
      'constraint-b',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          !n.axis &&
          (node.operation === 'coaxial' ? n.kind !== 'plane' : n.kind === 'plane'),
      ),
      [node.reference || node.plane],
    );
  } else if (node.operation === 'rotational_symmetry') {
    $('rotation-extents').checked = node.symmetric_extents !== false;
    choices(
      'rotation-axis',
      earlier.filter((n) => n.operation === 'fit' && n.kind !== 'plane' && !n.axis),
      [node.axis],
    );
    node.planes.forEach((id, i) =>
      choices(
        'rotation-input-' + i,
        earlier.filter((n) => n.operation === 'fit' && !n.axis),
        [id],
      ),
    );
  } else if (node.operation === 'mirror_symmetry') {
    choices(
      'mirror-plane',
      earlier.filter((n) => n.operation === 'reference_plane'),
      [node.plane],
    );
    node.surfaces.forEach((id, i) =>
      choices(
        'mirror-input-' + i,
        earlier.filter((n) => n.operation === 'fit' && !n.axis),
        [id],
      ),
    );
    $('mirror-extents').checked = node.symmetric_extents !== false;
  } else if (node.operation === 'parallel') {
    choices(
      'parallel-surface',
      earlier.filter((n) => n.operation === 'fit' && n.kind === 'plane' && !n.axis),
      [node.surface],
    );
    choices(
      'parallel-reference',
      earlier.filter((n) => n.operation === 'reference_plane'),
      [node.reference_plane],
    );
  } else if (node.operation === 'equal') {
    const radius = [node.left, node.right].find((value) => value.measurement === 'radius'),
      distance = [node.left, node.right].find(
        (value) => value.measurement === 'plane_distance',
      );
    choices(
      'equal-radius-surface',
      earlier.filter((n) => n.operation === 'fit' && n.kind === 'cylinder' && n.axis),
      [radius.surface],
    );
    choices(
      'equal-distance-surface',
      earlier.filter((n) => n.operation === 'fit' && n.kind === 'plane' && !n.axis),
      [distance.surface],
    );
    choices(
      'equal-distance-reference',
      earlier.filter((n) => n.operation === 'reference_plane'),
      [distance.reference_plane],
    );
  } else if (node.operation === 'joint_fit')
    choices(
      'joint-inputs',
      earlier.filter((n) =>
        ['coaxial', 'perpendicular', 'rotational_symmetry'].includes(n.operation),
      ),
      node.constraints,
    );
  if (node.operation === 'joint_fit') {
    const used = new Set(node.constraints.flatMap((id) => refs(graphNode(id))));
    choices(
      'joint-add-fits',
      graphState.recipe.nodes.filter(
        (n) => n.operation === 'fit' && !n.axis && !used.has(n.id),
      ),
    );
  }
  const dependents = graphState.recipe.nodes.filter((n) => refs(n).includes(node.id));
  $('delete-action').disabled = !!dependents.length || graphState.recipe.nodes.length === 1;
  $('delete-action').title = dependents.length
    ? 'Referenced by ' + dependents.map((n) => n.label).join(', ')
    : 'Delete this unused action';
  $('fit').textContent = 'Evaluate ' + node.label;
  $('propose-growth').hidden = node.operation !== 'fit' || !!node.axis;
  $('use-growth').hidden = node.operation !== 'growth';
}
function axisGuide(axisValues, color = '#ffd166') {
  const axis = new THREE.Vector3(...axisValues.axis_display).normalize();
  const point = new THREE.Vector3(...axisValues.point_display);
  const domain = metadata?.axial_domain || [-2, 5];
  const endpoints = domain.map((distance) => point.clone().addScaledVector(axis, distance));
  overlays.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(endpoints),
      new THREE.LineBasicMaterial({
        color,
        depthTest: false,
        transparent: true,
        opacity: 1,
      }),
    ),
  );
  overlays.add(
    new THREE.Points(
      new THREE.BufferGeometry().setFromPoints([endpoints[0], point, endpoints[1]]),
      new THREE.PointsMaterial({ color, depthTest: false, size: 7, sizeAttenuation: false }),
    ),
  );
}
function axisPreview(node) {
  let parameters = node.initial_parameters;
  if (!parameters && node.source_fit) parameters = graphState.results[node.source_fit]?.parameters;
  if (!parameters) return null;
  const axis = new THREE.Vector3(parameters[2], parameters[3], 1).normalize();
  return {
    axis_display: axis.toArray(),
    point_display: [parameters[0], parameters[1], 0],
  };
}
function referencePlanePreview(node) {
  const axisNode = graphNode(node.axis);
  const values = graphState.results[node.axis] || axisPreview(axisNode);
  if (!values) return null;
  const axis = new THREE.Vector3(...values.axis_display).normalize();
  const basis = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)];
  const reference = basis.sort((a, b) => Math.abs(axis.dot(a)) - Math.abs(axis.dot(b)))[0];
  const u = new THREE.Vector3().crossVectors(axis, reference).normalize();
  const v = new THREE.Vector3().crossVectors(axis, u);
  const angle = (node.initial_angle_degrees * Math.PI) / 180;
  const radial = u.multiplyScalar(Math.cos(angle)).addScaledVector(v, Math.sin(angle));
  return {
    axis_display: axis.toArray(),
    point_display: values.point_display,
    radial_display: radial.toArray(),
    normal_display: new THREE.Vector3().crossVectors(axis, radial).toArray(),
    angle_degrees: node.initial_angle_degrees,
  };
}
function referencePlaneGuide(values, color = '#ff8fe5') {
  const axis = new THREE.Vector3(...values.axis_display).normalize();
  const radial = new THREE.Vector3(...values.radial_display).normalize();
  const point = new THREE.Vector3(...values.point_display);
  const domain = metadata?.axial_domain || [-2, 5];
  const halfWidth = Math.max(1, (domain[1] - domain[0]) * 0.35);
  const corners = [
    point.clone().addScaledVector(axis, domain[0]).addScaledVector(radial, -halfWidth),
    point.clone().addScaledVector(axis, domain[0]).addScaledVector(radial, halfWidth),
    point.clone().addScaledVector(axis, domain[1]).addScaledVector(radial, halfWidth),
    point.clone().addScaledVector(axis, domain[1]).addScaledVector(radial, -halfWidth),
  ];
  corners.push(corners[0]);
  overlays.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(corners),
      new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.9 }),
    ),
  );
}
function surfaceGuide(kind, p, domain, color, ids = []) {
  let axis, point;
  if (kind === 'plane') {
    axis = new THREE.Vector3(...p.slice(0, 3));
    point = new THREE.Vector3();
    for (const id of ids) point.add(new THREE.Vector3().fromArray(positions, id * 3));
    if (ids.length) point.divideScalar(ids.length);
    point.addScaledVector(axis, p[3] - point.dot(axis));
  } else {
    axis = new THREE.Vector3(p[2], p[3], 1).normalize();
    point = new THREE.Vector3(p[0], p[1], 0);
  }
  const u = new THREE.Vector3()
      .crossVectors(
        axis,
        Math.abs(axis.z) > 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1),
      )
      .normalize(),
    v = new THREE.Vector3().crossVectors(axis, u);
  const line = (points) =>
    overlays.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({
          color: new THREE.Color(color).lerp(new THREE.Color('#ffffff'), 0.18),
          depthTest: false,
          transparent: true,
          opacity: 1,
        }),
      ),
    );
  const ring = (z, r) => {
    const points = [];
    for (let i = 0; i <= 96; i++) {
      const a = (i * 2 * Math.PI) / 96;
      points.push(
        point
          .clone()
          .addScaledVector(axis, z)
          .addScaledVector(u, r * Math.cos(a))
          .addScaledVector(v, r * Math.sin(a)),
      );
    }
    line(points);
  };
  if (kind === 'plane') {
    let extent = 1;
    for (const id of ids)
      extent = Math.max(extent, new THREE.Vector3().fromArray(positions, id * 3).distanceTo(point));
    ring(0, extent);
    ring(0, extent * 0.8);
  } else {
    for (const z of domain) ring(z, p[4] + p[6] * z);
    for (let i = 0; i < 12; i++) {
      const a = (i * 2 * Math.PI) / 12;
      line(
        domain.map((z) =>
          point
            .clone()
            .addScaledVector(axis, z)
            .addScaledVector(u, (p[4] + p[6] * z) * Math.cos(a))
            .addScaledVector(v, (p[4] + p[6] * z) * Math.sin(a)),
        ),
      );
    }
  }
}
function fitGuide(node, fitted, color) {
  let parameters = fitted.plane_equation || fitted.parameters;
  if (node.kind === 'plane' && !fitted.plane_equation && parameters.length > 4) {
    const normal = new THREE.Vector3(parameters[2], parameters[3], 1).normalize();
    parameters = [...normal.toArray(), parameters[5]];
  }
  surfaceGuide(
    node.kind,
    parameters,
    fitted.axial_domain || node.axial_domain,
    color,
    fitted.ids,
  );
}
function showAvailableGuides() {
  if (!$('all-guides').checked) return;
  const selected = graphNode(selectedFeatureId);
  const selectedResult = graphState.results[selectedFeatureId];
  const renderedBySelected = new Set([
    ...Object.keys(selectedResult?.surfaces || {}),
    ...Object.keys(selectedResult?.mirror_planes || {}),
    ...(['axis_solve'].includes(selected?.operation) ? [selected.axis] : []),
  ]);
  let colorIndex = 0;
  for (const node of graphState.recipe.nodes) {
    if (node.id === selectedFeatureId || renderedBySelected.has(node.id)) continue;
    const fitted = graphState.results[node.id];
    if (node.operation === 'axis') {
      const values = fitted || axisPreview(node);
      if (values) axisGuide(values, '#f4cf72');
    } else if (node.operation === 'reference_plane') {
      const values = fitted || referencePlanePreview(node);
      if (values) referencePlaneGuide(values);
    } else if (node.operation === 'fit' && fitted) {
      fitGuide(node, fitted, palette[colorIndex++ % palette.length]);
    }
  }
}
function overlapDiagnostic() {
  const diagnostics = graphState.diagnostics || {};
  if (diagnostics[selectedFeatureId]?.kind === 'selection_overlap')
    activeOverlap = selectedFeatureId;
  if (!diagnostics[activeOverlap])
    activeOverlap = Object.keys(diagnostics).find(
      (id) => diagnostics[id].kind === 'selection_overlap',
    );
  return diagnostics[activeOverlap];
}
function showOverlap() {
  const diagnostic = overlapDiagnostic();
  $('overlap-banner').hidden = !diagnostic;
  $('overlap-details').hidden = !diagnostic;
  $('overlap-details').replaceChildren();
  overlapMarkers.visible = overlapHalo.visible = !!diagnostic;
  overlapMarkers.geometry.setIndex(diagnostic?.ids || []);
  if (!diagnostic) return;
  $('overlap-summary').textContent =
    `${diagnostic.ids.length} overlapping vertices · ${graphNode(activeOverlap).label}`;
  const title = document.createElement('strong');
  title.textContent = 'Conflicting fit inputs';
  $('overlap-details').append(title);
  for (const pair of diagnostic.conflicts) {
    const row = document.createElement('p');
    row.textContent = `${pair.ids.length} shared vertices: `;
    for (const [i, id] of pair.fits.entries()) {
      if (i) row.append(' ↔ ');
      const button = document.createElement('button'),
        node = graphNode(id);
      button.textContent = node.label;
      button.title = 'Selections: ' + node.selections.map((ref) => graphNode(ref).label).join(', ');
      button.onclick = () => {
        selectedFeatureId = id;
        renderActions();
        showProperties();
        showResult();
        paint();
        $('feature-properties-panel').scrollTop = 0;
      };
      row.append(button);
    }
    $('overlap-details').append(row);
  }
}
function showResult() {
  result = graphState.results[selectedFeatureId] || null;
  clearGuides();
  showAvailableGuides();
  $('metrics').replaceChildren();
  if (!result) {
    const node = graphNode(selectedFeatureId);
    if (node.operation === 'axis') {
      const preview = axisPreview(node);
      if (preview) axisGuide(preview);
    } else if (node.operation === 'reference_plane') {
      const preview = referencePlanePreview(node);
      if (preview) referencePlaneGuide(preview);
    } else if (
      [
        'coaxial',
        'perpendicular',
        'rotational_symmetry',
        ...relationshipOperations,
      ].includes(
        node.operation,
      )
    ) {
      refs(node).forEach((id, i) => {
        const fit = graphState.results[id],
          surface = graphNode(id);
        if (!fit) return;
        if (surface.operation === 'reference_plane') referencePlaneGuide(fit);
        else if (surface.operation === 'axis') axisGuide(fit);
        else fitGuide(surface, fit, palette[i % palette.length]);
      });
    }
    return;
  }
  const node = graphNode(selectedFeatureId),
    values = {};
  if (node.operation === 'fit') {
    fitGuide(node, result, '#66dbe9');
    values['Weighted RMS'] = result.weighted_rms.toFixed(5);
    values['Condition'] = result.condition.toExponential(3);
    if (node.kind === 'plane')
      values['Plane normal'] = (result.plane_equation || result.parameters)
        .slice(0, 3)
        .map((v) => v.toFixed(4))
        .join(', ');
    else {
      values['Diameter'] = (2 * result.parameters[4]).toFixed(5);
      values['Half-angle'] = ((Math.atan(result.parameters[6]) * 180) / Math.PI).toFixed(4) + '°';
    }
  } else if (node.operation === 'axis') {
    axisGuide(result);
    values['Initialized by'] = node.source_fit ? graphNode(node.source_fit).label : 'Manual value';
    values['Direction'] = result.axis_display.map((v) => v.toFixed(5)).join(', ');
  } else if (node.operation === 'reference_plane') {
    referencePlaneGuide(result);
    values['Initial clocking'] = `${result.angle_degrees.toFixed(4)}°`;
    values['Normal'] = result.normal_display.map((v) => v.toFixed(5)).join(', ');
  } else if (['joint_fit', 'axis_solve'].includes(node.operation)) {
    axisGuide(result, '#ffd166');
    for (const plane of Object.values(result.mirror_planes || {}))
      referencePlaneGuide(plane, '#ff8fe5');
    values['Combined RMS'] = result.fit.weighted_rms.toFixed(5);
    Object.entries(result.surfaces).forEach(([id, s], i) => {
      fitGuide(graphNode(id), s, palette[i % palette.length]);
      values[graphNode(id).label + ' adjusted RMS'] = s.weighted_rms.toFixed(5);
    });
  } else if (node.operation === 'growth') {
    values['Proposed additions'] = result.added_ids.length;
    values['Seed outliers'] = result.rejected_seed_ids.length;
    values['Total vertices'] = result.ids.length;
  }
  for (const [label, value] of Object.entries(values)) {
    const dt = document.createElement('dt'),
      dd = document.createElement('dd');
    dt.textContent = label;
    dd.textContent = value;
    $('metrics').append(dt, dd);
  }
}
function paint() {
  const colors = mesh.geometry.getAttribute('color'),
    gray = new THREE.Color('#8796a2');
  for (let i = 0; i < colors.count; i++) colors.setXYZ(i, gray.r, gray.g, gray.b);
  Object.entries(session).forEach(([id, ids], i) => {
    const c = new THREE.Color(palette[i % palette.length]);
    for (const vertex of ids) colors.setXYZ(vertex, c.r, c.g, c.b);
  });
  if (result?.added_ids) {
    const c = new THREE.Color('#4dff91');
    for (const id of result.added_ids) colors.setXYZ(id, c.r, c.g, c.b);
  }
  $('legend').textContent = result?.added_ids
    ? 'Green: proposed additions. Seeds retain selection colors.'
    : 'Colors show raw selections. Inspect an action to see its own fit guides.';
  if ($('colors').value === 'residual' && (result?.surfaces || result?.residuals)) {
    const white = new THREE.Color('#ffffff'),
      blue = new THREE.Color('#245bea'),
      red = new THREE.Color('#e23636');
    for (const s of Object.values(result.surfaces || { standalone: result })) {
      const limit = Math.max(1e-12, ...s.residuals.map(Math.abs));
      s.ids.forEach((id, i) => {
        const c = white
          .clone()
          .lerp(s.residuals[i] < 0 ? blue : red, Math.abs(s.residuals[i]) / limit);
        colors.setXYZ(id, c.r, c.g, c.b);
      });
    }
    $('legend').textContent =
      'Residuals: blue negative, red positive; each surface has its own scale.';
  }
  showOverlap();
  const overlap = overlapDiagnostic();
  if (overlap) {
    const color = new THREE.Color('#ff20db');
    for (const id of overlap.ids) colors.setXYZ(id, color.r, color.g, color.b);
  }
  const emphasized = featureVertexIds(
    graphState.recipe.nodes,
    { ...graphState.memberships, ...session },
    selectedFeatureId,
  );
  const focusColors = focusedPoints.geometry.getAttribute('color');
  const white = new THREE.Color('#ffffff');
  // Unlit markers need their own colors: bright mesh lighting can otherwise
  // make them indistinguishable from the interpolated surface tint.
  const markerColors = selectedPoints.geometry.getAttribute('color');
  for (let id = 0; id < colors.count; id++) {
    const c = new THREE.Color().fromBufferAttribute(colors, id).lerp(white, 0.3);
    markerColors.setXYZ(id, c.r, c.g, c.b);
  }
  markerColors.needsUpdate = true;
  for (const id of emphasized) {
    const c = new THREE.Color().fromBufferAttribute(colors, id).lerp(white, 0.6);
    focusColors.setXYZ(id, c.r, c.g, c.b);
  }
  focusColors.needsUpdate = true;
  focusedPoints.geometry.setIndex(emphasized);
  focusedPoints.visible = $('points').checked;
  $('counts').dataset.emphasizedVertices = String(emphasized.length);
  colors.needsUpdate = true;
  selectedPoints.geometry.setIndex([
    ...Object.values(session).flat(),
    ...(result?.added_ids || []),
    ...emphasized,
  ]);
  selectedPoints.visible = $('points').checked;
  overlays.visible = $('guides').checked;
  $('counts').textContent = activeSelection()
    ? `${session[selectedFeatureId].length.toLocaleString()} selected vertices`
    : '';
  renderer.domElement.style.cursor = activeSelection() && $('tool').value !== 'orbit'
    ? 'crosshair' : 'default';
  if (!activeSelection()) $('brush-cursor').hidden = true;
  $('fit').disabled =
    busy ||
    selectionDrawing ||
    selectionPending ||
    [
      'coaxial',
      'perpendicular',
      'rotational_symmetry',
      ...relationshipOperations,
    ].includes(
      graphNode(selectedFeatureId).operation,
    );
  $('evaluate-all').disabled = busy || selectionDrawing || selectionPending;
  $('propose-growth').disabled = busy || selectionDrawing || selectionPending;
  $('use-growth').disabled = busy || !graphState.results[selectedFeatureId];
  draw();
}
async function fit() {
  return evaluateGraph(false, selectedFeatureId);
}
async function evaluateAll() {
  return evaluateGraph(true);
}
async function evaluateGraph(allActions, target = null) {
  if (busy) return;
  busy = true;
  document.querySelector('aside').inert = true;
  $('creation-toolbar').inert = true;
  $('project-toolbar').inert = true;
  paint();
  status(allActions ? 'Evaluating all actions…' : 'Evaluating action and earlier inputs…');
  try {
    await request('/api/graph/evaluate', {
      token: graphState.token,
      ...(allActions ? { all_actions: true } : { target }),
    });
    let state;
    do {
      await new Promise((resolve) => setTimeout(resolve, 150));
      state = await request('/api/graph');
      acceptGraph(state);
    } while (state.evaluation_running);
    if (state.evaluation_error) throw new Error(state.evaluation_error);
    status(
      allActions
        ? 'All actions evaluated. Available fits and references are shown together.'
        : 'Evaluation complete. Select another action to inspect its retained result.',
    );
  } catch (error) {
    status(error.message, true);
  } finally {
    busy = false;
    document.querySelector('aside').inert = false;
    $('creation-toolbar').inert = false;
    $('project-toolbar').inert = false;
    paint();
  }
}
function home(direction = null) {
  mesh.geometry.computeBoundingSphere();
  const sphere = mesh.geometry.boundingSphere;
  const distance =
    (sphere.radius /
      Math.sin(
        Math.min(
          (camera.fov * Math.PI) / 360,
          Math.atan(Math.tan((camera.fov * Math.PI) / 360) * camera.aspect),
        ),
      )) *
    1.12;
  const vector =
    direction === null
      ? camera.getWorldDirection(new THREE.Vector3()).negate()
      : direction === 'top'
        ? new THREE.Vector3(0, -0.001, 1)
        : direction === 'side'
          ? new THREE.Vector3(0, -1, 0)
          : new THREE.Vector3(1, -1.5, 0.8);
  if (direction !== null) camera.up.set(0, 0, 1);
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(vector.normalize(), distance);
  camera.near = Math.max(0.001, sphere.radius / 1000);
  camera.far = sphere.radius * 1000;
  camera.updateProjectionMatrix();
  controls.update();
  draw();
}
function setupFeatureDivider() {
  const divider = $('feature-divider');
  const sidebar = divider.parentElement;
  const storageKey = 'scansor.featurePanelRatio';
  let ratio = 0.4, drag = null;
  try {
    const saved = Number(localStorage.getItem(storageKey));
    if (saved >= 0.15 && saved <= 0.85) ratio = saved;
  } catch { /* Layout preferences are optional when browser storage is unavailable. */ }
  const availableHeight = () => Math.max(1, sidebar.clientHeight - divider.offsetHeight);
  const update = (next) => {
    ratio = Math.max(0.15, Math.min(0.85, next));
    sidebar.style.setProperty('--tree-height', `${availableHeight() * ratio}px`);
    divider.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    divider.setAttribute('aria-valuetext', `Feature tree ${Math.round(ratio * 100)} percent`);
  };
  const save = () => {
    try { localStorage.setItem(storageKey, String(ratio)); }
    catch { /* Keep resizing available without persistent browser storage. */ }
  };
  const finish = (cancel = false) => {
    if (!drag) return;
    const previous = drag;
    drag = null;
    if (cancel) update(previous.ratio);
    else save();
    document.body.classList.remove('resizing-features');
    if (divider.hasPointerCapture(previous.id)) divider.releasePointerCapture(previous.id);
  };
  divider.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag) return;
    event.preventDefault();
    divider.focus();
    drag = { id: event.pointerId, y: event.clientY, ratio };
    divider.setPointerCapture(event.pointerId);
    document.body.classList.add('resizing-features');
  });
  divider.addEventListener('pointermove', (event) => {
    if (drag?.id === event.pointerId)
      update(drag.ratio + (event.clientY - drag.y) / availableHeight());
  });
  divider.addEventListener('pointerup', (event) => {
    if (drag?.id === event.pointerId) finish();
  });
  divider.addEventListener('pointercancel', () => finish(true));
  divider.addEventListener('lostpointercapture', () => finish(true));
  window.addEventListener('blur', () => finish(true));
  divider.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { finish(true); return; }
    const step = event.shiftKey ? 0.1 : 0.025;
    const next = { ArrowUp: ratio - step, ArrowDown: ratio + step, Home: 0.15, End: 0.85 }[event.key];
    if (next === undefined || drag) return;
    event.preventDefault();
    update(next);
    save();
  });
  new ResizeObserver(() => update(ratio)).observe(sidebar);
  update(ratio);
}
async function start() {
  setupFeatureDivider();
  metadata = await request('/api/meta');
  const [pb, ib] = await Promise.all(
    [metadata.positions.url, metadata.indices.url].map(async (url) => {
      const response = await fetch(url);
      if (!response.ok) throw new Error('Could not load mesh buffers');
      return response.arrayBuffer();
    }),
  );
  positions = new Float32Array(pb);
  session = structuredClone(metadata.session);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  viewport.append(renderer.domElement);
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute('aria-label', 'Nozzle 3D view');
  scene = new THREE.Scene();
  scene.background = new THREE.Color('#17232e');
  camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
  camera.up.set(0, 0, 1);
  const raycaster = new THREE.Raycaster();
  controls = onshapeNavigation(camera, renderer.domElement, draw, (event, fallback) => {
    if (!mesh) return null;
    const bounds = renderer.domElement.getBoundingClientRect();
    camera.updateMatrixWorld();
    mesh.updateMatrixWorld();
    raycaster.near = camera.near;
    raycaster.far = camera.far;
    raycaster.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        1 - ((event.clientY - bounds.top) / bounds.height) * 2,
      ),
      camera,
    );
    const hit = raycaster.intersectObject(mesh, false)[0];
    return (
      hit?.point ?? (fallback ? viewPlaneAnchor(raycaster.ray, camera, controls.target) : null)
    );
  });
  $('cursor-zoom').onchange = () => {
    controls.options.zoomAtCursor = $('cursor-zoom').checked;
  };
  $('cursor-rotate').onchange = () => {
    controls.options.rotateAtCursor = $('cursor-rotate').checked;
  };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(ib), 1));
  geometry.computeVertexNormals();
  geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(positions.length), 3));
  mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      roughness: 0.85,
      // Offset only rasterized mesh depth, not coordinates or selection picking.
      // The slope term keeps point sprites from being cut by their own surface.
      polygonOffset: true,
      polygonOffsetFactor: 4,
      polygonOffsetUnits: 2,
    }),
  );
  scene.add(mesh);
  const pointGeometry = new THREE.BufferGeometry();
  pointGeometry.setAttribute('position', geometry.getAttribute('position'));
  pointGeometry.setAttribute(
    'color',
    new THREE.BufferAttribute(new Float32Array(positions.length), 3),
  );
  selectedPoints = new THREE.Points(
    pointGeometry,
    new THREE.PointsMaterial({
      vertexColors: true,
      size: 3,
      sizeAttenuation: false,
      depthWrite: false,
    }),
  );
  selectedPoints.renderOrder = 1;
  scene.add(selectedPoints);
  const focusedGeometry = new THREE.BufferGeometry();
  focusedGeometry.setAttribute('position', geometry.getAttribute('position'));
  focusedGeometry.setAttribute(
    'color',
    new THREE.BufferAttribute(new Float32Array(positions.length), 3),
  );
  focusedPoints = new THREE.Points(
    focusedGeometry,
    new THREE.PointsMaterial({
      vertexColors: true,
      size: 5,
      sizeAttenuation: false,
      depthWrite: false,
    }),
  );
  focusedPoints.renderOrder = 2;
  scene.add(focusedPoints);
  const overlapGeometry = new THREE.BufferGeometry();
  overlapGeometry.setAttribute('position', geometry.getAttribute('position'));
  overlapMarkers = new THREE.Points(
    overlapGeometry,
    new THREE.PointsMaterial({
      color: '#ff20db',
      size: 7,
      sizeAttenuation: false,
      depthTest: false,
      depthWrite: false,
    }),
  );
  overlapHalo = new THREE.Points(
    overlapGeometry,
    new THREE.PointsMaterial({
      color: '#ffffff',
      size: 11,
      sizeAttenuation: false,
      depthTest: false,
      depthWrite: false,
    }),
  );
  overlapHalo.renderOrder = 100;
  overlapMarkers.renderOrder = 101;
  scene.add(overlapHalo, overlapMarkers);
  scene.add(new THREE.HemisphereLight('#ffffff', '#738396', 2));
  const light = new THREE.DirectionalLight('#ffffff', 2.5);
  light.position.set(15, -20, 30);
  scene.add(light);
  overlays = new THREE.Group();
  scene.add(overlays);
  const resize = () => {
    renderer.setSize(viewport.clientWidth, viewport.clientHeight);
    camera.aspect = viewport.clientWidth / viewport.clientHeight;
    camera.updateProjectionMatrix();
    controls.resize();
    draw();
  };
  new ResizeObserver(resize).observe(viewport);
  resize();
  home('oblique');
  $('mesh-info').textContent =
    `${metadata.vertices.toLocaleString()} vertices · ${metadata.triangles.toLocaleString()} triangles`;
  $('save').disabled = false;
  $('tool').onchange = paint;
  $('fit').onclick = fit;
  $('evaluate-all').onclick = evaluateAll;
  $('auto-evaluate').onchange = () => {
    if ($('auto-evaluate').checked) void evaluateAll();
  };
  $('inspect-overlap').onclick = () => {
    selectedFeatureId = activeOverlap;
    renderActions();
    showProperties();
    showResult();
    paint();
    $('overlap-details').scrollIntoView({ block: 'nearest' });
  };
  const updateExportPlanes = () => {
    const nodes = graphState.recipe.nodes;
    const target = nodes.find((n) => n.id === $('export-target').value);
    const ids = new Set([target?.id]);
    for (const id of target?.factors || []) {
      ids.add(id);
      const factor = nodes.find((n) => n.id === id);
      if (factor?.operation === 'mirror_symmetry')
        for (const surface of factor.surfaces) ids.add(surface);
    }
    for (const id of target?.constraints || []) {
      const constraint = nodes.find((n) => n.id === id);
      for (const ref of [constraint?.plane, ...(constraint?.planes || [])]) ids.add(ref);
    }
    const select = $('export-origin-plane');
    const previous = select.value;
    select.replaceChildren(new Option('Keep current axial position', ''));
    for (const node of nodes.filter(
      (n) => ids.has(n.id) && n.operation === 'fit' && n.kind === 'plane',
    )) {
      select.add(new Option(node.label, node.id));
    }
    select.value = [...select.options].some((o) => o.value === previous) ? previous : '';
    select.disabled = !$('export-axis-up').checked;
  };
  $('export-target').onchange = updateExportPlanes;
  $('export-axis-up').onchange = updateExportPlanes;
  $('export-rhino').onclick = () => {
    const targets = graphState.recipe.nodes.filter((n) =>
      ['fit', 'joint_fit', 'axis_solve'].includes(n.operation),
    );
    choices('export-target', targets, [
      targets.find((n) => n.id === selectedFeatureId)?.id ||
        targets.find((n) => ['axis_solve', 'joint_fit'].includes(n.operation))?.id,
    ]);
    updateExportPlanes();
    $('export-error').textContent = '';
    $('export-dialog').showModal();
  };
  $('export-form').onsubmit = async (event) => {
    event.preventDefault();
    if (busy || selectionDrawing || selectionPending) return;
    busy = true;
    $('export-download').disabled = true;
    $('export-error').textContent = 'Evaluating surfaces…';
    const target = $('export-target').value;
    try {
      await request('/api/graph/evaluate', { token: graphState.token, target });
      let state;
      do {
        await new Promise((resolve) => setTimeout(resolve, 150));
        state = await request('/api/graph');
        acceptGraph(state);
      } while (state.evaluation_running);
      if (state.evaluation_error) throw new Error(state.evaluation_error);
      $('export-error').textContent = 'Preparing Rhino file…';
      const response = await fetch('/api/export/rhino', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Scansor-Request': '1' },
        body: JSON.stringify({
          token: state.token,
          target,
          units: $('export-units').value,
          axis_up: $('export-axis-up').checked,
          origin_plane: $('export-axis-up').checked ? $('export-origin-plane').value || null : null,
          include_mesh: $('export-mesh').checked,
        }),
      });
      if (!response.ok) throw new Error((await response.json()).error || 'Export failed');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'nozzle-fitted-surfaces.3dm';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('export-dialog').close();
      status('Rhino export downloaded. Surfaces and mesh share the same orientation and units.');
    } catch (error) {
      $('export-error').textContent = error.message;
    } finally {
      busy = false;
      $('export-download').disabled = false;
      paint();
    }
  };
  $('new-fit').onclick = () => {
    choices(
      'new-fit-inputs',
      graphState.recipe.nodes.filter((node) => ['selection', 'growth'].includes(node.operation)),
      [selectedFeatureId],
    );
    axisChoices(
      'new-fit-axis',
      graphState.recipe.nodes.filter((node) => node.operation === 'axis'),
      [],
      true,
    );
    $('fit-dialog').showModal();
  };
  const updateAxisSolveFactors = () => {
    const axis = $('new-axis-solve-axis').value;
    choices('new-axis-solve-factors', solveInputs(axis));
  };
  $('new-axis').onclick = () => {
    const sources = graphState.recipe.nodes.filter(
      (node) =>
        node.operation === 'fit' && ['cone', 'cylinder'].includes(node.kind) && !node.axis,
    );
    choices(
      'new-axis-source',
      sources,
      [selectedFeatureId],
    );
    $('new-axis-mode').value = 'free';
    showAxisInitializer(
      'new-axis-mode',
      'new-axis-source-fields',
      'new-axis-manual-fields',
    );
    $('axis-dialog').showModal();
  };
  $('new-reference-plane').onclick = () => {
    const axes = graphState.recipe.nodes.filter((node) => node.operation === 'axis');
    if (!axes.length) {
      status('Create an explicit axis before creating a reference plane.', true);
      return;
    }
    choices('new-reference-plane-axis', axes, [graphNode(selectedFeatureId)?.axis || selectedFeatureId]);
    $('reference-plane-dialog').showModal();
  };
  const updateMirrorChoices = () => {
    const fits = graphState.recipe.nodes.filter((node) => node.operation === 'fit' && !node.axis);
    const first = fits.find((fit) => fit.id === selectedFeatureId) || fits[0];
    const second = fits.find((fit) => fit.id !== first?.id);
    choices('new-mirror-input-0', fits, [first?.id]);
    choices('new-mirror-input-1', fits, [second?.id]);
  };
  $('new-mirror').onclick = () => {
    const planes = graphState.recipe.nodes.filter((node) => node.operation === 'reference_plane');
    if (!planes.length) {
      status('Create a reference plane through an axis before adding mirror symmetry.', true);
      return;
    }
    if (graphState.recipe.nodes.filter((node) => node.operation === 'fit' && !node.axis).length < 2) {
      status('Create two standalone fits before adding mirror symmetry.', true);
      return;
    }
    choices('new-mirror-plane', planes, [graphNode(selectedFeatureId)?.plane || selectedFeatureId]);
    updateMirrorChoices();
    $('mirror-error').textContent = '';
    $('mirror-dialog').showModal();
  };
  $('new-parallel').onclick = () => {
    const fits = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && node.kind === 'plane' && !node.axis,
      ),
      planes = graphState.recipe.nodes.filter((node) => node.operation === 'reference_plane');
    if (!fits.length || !planes.length) {
      status('Create a standalone plane fit and a reference plane first.', true);
      return;
    }
    choices('new-parallel-surface', fits, [selectedFeatureId]);
    choices('new-parallel-reference', planes, [graphNode(selectedFeatureId)?.plane]);
    $('parallel-error').textContent = '';
    $('parallel-dialog').showModal();
  };
  $('new-equal').onclick = () => {
    const cylinders = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && node.kind === 'cylinder' && node.axis,
      ),
      fits = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && node.kind === 'plane' && !node.axis,
      ),
      planes = graphState.recipe.nodes.filter((node) => node.operation === 'reference_plane');
    if (!cylinders.length || !fits.length || !planes.length) {
      status('Create an axis-bound cylinder, standalone plane fit, and reference plane first.', true);
      return;
    }
    choices('new-equal-radius-surface', cylinders, [selectedFeatureId]);
    choices('new-equal-distance-surface', fits);
    choices('new-equal-distance-reference', planes);
    $('equal-error').textContent = '';
    $('equal-dialog').showModal();
  };
  $('new-axis-solve').onclick = () => {
    const axes = graphState.recipe.nodes.filter((node) => node.operation === 'axis');
    if (!axes.length) {
      status('Create an explicit axis before creating a joint.', true);
      return;
    }
    choices(
      'new-axis-solve-axis',
      axes,
      [graphNode(selectedFeatureId)?.axis || selectedFeatureId],
    );
    updateAxisSolveFactors();
    $('axis-solve-error').textContent = '';
    $('axis-solve-dialog').showModal();
  };
  $('new-axis-solve-axis').onchange = updateAxisSolveFactors;
  $('new-axis-mode').onchange = () =>
    showAxisInitializer(
      'new-axis-mode',
      'new-axis-source-fields',
      'new-axis-manual-fields',
    );
  $('axis-init-mode').onchange = () =>
    showAxisInitializer('axis-init-mode', 'axis-source-fields', 'axis-manual-fields');
  $('axis-solve-axis').onchange = () => {
    const axis = $('axis-solve-axis').value;
    choices('axis-solve-factors', solveInputs(axis));
  };
  $('add-axis-form').onsubmit = async (event) => {
    event.preventDefault();
    const fromFit = $('new-axis-mode').value === 'fit',
      sourceId = $('new-axis-source').value,
      source = graphNode(sourceId);
    if (fromFit && !source) {
      status('Create a standalone cone or cylinder fit first.', true);
      return;
    }
    const axis = {
      id: uid('axis'),
      label: $('new-axis-label').value,
      operation: 'axis',
      ...(fromFit
        ? { source_fit: sourceId }
        : {
            initial_parameters: [
              Number($('new-axis-point-x').value),
              Number($('new-axis-point-y').value),
              Number($('new-axis-direction-x').value),
              Number($('new-axis-direction-y').value),
            ],
          }),
    };
    const nodes = [axis];
    if (fromFit && $('new-axis-clone').checked)
      nodes.push({
        id: uid('fit'),
        label: (source.label + ' axis factor').slice(0, 120),
        operation: 'fit',
        selections: [...source.selections],
        kind: source.kind,
        axial_domain: [...source.axial_domain],
        axis: axis.id,
      });
    if (await appendActions(nodes)) $('axis-dialog').close();
  };
  $('add-reference-plane-form').onsubmit = async (event) => {
    event.preventDefault();
    const saved = await appendActions([
      {
        id: uid('reference_plane'),
        label: $('new-reference-plane-label').value,
        operation: 'reference_plane',
        axis: $('new-reference-plane-axis').value,
        initial_angle_degrees: Number($('new-reference-plane-angle').value),
      },
    ]);
    if (saved) $('reference-plane-dialog').close();
  };
  $('add-mirror-form').onsubmit = async (event) => {
    event.preventDefault();
    let surfaces;
    try {
      surfaces = mirrorFitInputs(graphState.recipe.nodes, [
        $('new-mirror-input-0').value,
        $('new-mirror-input-1').value,
      ]);
    } catch (error) {
      $('mirror-error').textContent = error.message;
      return;
    }
    const saved = await appendActions([
      {
        id: uid('mirror'),
        label: $('new-mirror-label').value,
        operation: 'mirror_symmetry',
        plane: $('new-mirror-plane').value,
        surfaces,
        symmetric_extents: $('new-mirror-extents').checked,
      },
    ]);
    if (saved) $('mirror-dialog').close();
    else $('mirror-error').textContent = $('status').textContent;
  };
  $('add-parallel-form').onsubmit = async (event) => {
    event.preventDefault();
    const saved = await appendActions([
      {
        id: uid('parallel'),
        label: $('new-parallel-label').value,
        operation: 'parallel',
        surface: $('new-parallel-surface').value,
        reference_plane: $('new-parallel-reference').value,
      },
    ]);
    if (saved) $('parallel-dialog').close();
    else $('parallel-error').textContent = $('status').textContent;
  };
  $('add-equal-form').onsubmit = async (event) => {
    event.preventDefault();
    const saved = await appendActions([
      {
        id: uid('equal'),
        label: $('new-equal-label').value,
        operation: 'equal',
        left: {
          measurement: 'radius',
          surface: $('new-equal-radius-surface').value,
        },
        right: {
          measurement: 'plane_distance',
          surface: $('new-equal-distance-surface').value,
          reference_plane: $('new-equal-distance-reference').value,
        },
      },
    ]);
    if (saved) $('equal-dialog').close();
    else $('equal-error').textContent = $('status').textContent;
  };
  $('add-axis-solve-form').onsubmit = async (event) => {
    event.preventDefault();
    const axis = $('new-axis-solve-axis').value,
      factors = chosen('new-axis-solve-factors'),
      kinds = new Set(
        factors.filter((id) => graphNode(id).operation === 'fit').map((id) => graphNode(id).kind),
      ),
      hasSide = kinds.has('cone') || kinds.has('cylinder'),
      hasPlaneEvidence = kinds.has('plane') || factors.some((id) => graphNode(id).operation === 'mirror_symmetry');
    if (!axis || !hasSide || !hasPlaneEvidence) {
      $('axis-solve-error').textContent =
        'Choose one axis, an axis-bound cone or cylinder, and either a bound plane or mirror relationship.';
      return;
    }
    const saved = await appendActions([
      {
        id: uid('axis_solve'),
        label: $('new-axis-solve-label').value,
        operation: 'axis_solve',
        axis,
        factors,
      },
    ]);
    if (saved) $('axis-solve-dialog').close();
    else $('axis-solve-error').textContent = $('status').textContent;
  };
  $('new-joint').onclick = () => $('joint-dialog').showModal();
  const rotationChoices = () => {
    const joint = graphNode($('rotation-joint').value);
    const used = new Set(joint ? joint.constraints.flatMap((id) => refs(graphNode(id))) : []);
    const planes = graphState.recipe.nodes.filter(
      (n) => n.operation === 'fit' && !n.axis && !used.has(n.id),
    );
    for (let i = 0; i < 3; i++) choices('rotation-plane-' + i, planes, [planes[i]?.id]);
  };
  $('new-rotation').onclick = () => {
    choices(
      'rotation-joint',
      graphState.recipe.nodes.filter((n) => n.operation === 'joint_fit'),
      [selectedFeatureId],
    );
    rotationChoices();
    $('rotation-error').textContent = '';
    $('rotation-dialog').showModal();
  };
  $('rotation-joint').onchange = rotationChoices;
  $('rotation-form').onsubmit = async (event) => {
    event.preventDefault();
    const jointId = $('rotation-joint').value,
      planes = [0, 1, 2].map((i) => $('rotation-plane-' + i).value);
    if (!jointId || planes.some((id) => !id) || new Set(planes).size !== 3) {
      $('rotation-error').textContent =
        'Choose a joint and three distinct unconstrained fits of the same type.';
      return;
    }
    const recipe = structuredClone(graphState.recipe),
      joint = recipe.nodes.find((n) => n.id === jointId);
    const axis = graphNode(
      joint.constraints.find((id) => graphNode(id).operation === 'perpendicular'),
    ).lateral;
    let fittedPlanes;
    try {
      fittedPlanes = rotationalFitInputs(recipe.nodes, planes);
    } catch (error) {
      $('rotation-error').textContent = error.message;
      return;
    }
    const constraint = {
      id: uid('rotation'),
      label: $('rotation-label').value,
      operation: 'rotational_symmetry',
      axis,
      planes: fittedPlanes,
    };
    joint.constraints.push(constraint.id);
    recipe.nodes = [...recipe.nodes.filter((n) => n.id !== jointId), constraint, joint];
    selectedFeatureId = jointId;
    if (await replaceRecipe(recipe)) {
      $('rotation-dialog').close();
      $('feature-properties-panel').scrollTop = 0;
    } else $('rotation-error').textContent = $('status').textContent;
  };
  for (const button of document.querySelectorAll('[data-close-dialog]'))
    button.onclick = () => $(button.dataset.closeDialog).close();
  $('extend-joint').onclick = async () => {
    const selected = chosen('joint-add-fits');
    if (!selected.length) {
      status('Choose fitted surfaces to add.', true);
      return;
    }
    const recipe = structuredClone(graphState.recipe),
      joint = recipe.nodes.find((n) => n.id === selectedFeatureId);
    const side = graphNode(
      joint.constraints.find((id) => graphNode(id).operation === 'perpendicular'),
    ).lateral;
    const relations = selected.map((id) =>
      graphNode(id).kind === 'plane'
        ? {
            id: uid('perpendicular'),
            label: (graphNode(id).label + ' perpendicular').slice(0, 120),
            operation: 'perpendicular',
            lateral: side,
            plane: id,
          }
        : {
            id: uid('coaxial'),
            label: (graphNode(id).label + ' coaxial').slice(0, 120),
            operation: 'coaxial',
            surface: id,
            reference: side,
          },
    );
    joint.constraints.push(...relations.map((n) => n.id));
    recipe.nodes = [...recipe.nodes.filter((n) => n.id !== joint.id), ...relations, joint];
    await replaceRecipe(recipe);
  };
  $('reset').onclick = async () => replaceRecipe(await request('/api/graph/example'));
  $('action-properties').onsubmit = async (event) => {
    event.preventDefault();
    const recipe = structuredClone(graphState.recipe),
      node = recipe.nodes.find((n) => n.id === selectedFeatureId);
    node.label = $('action-label').value;
    if (node.operation === 'fit') {
      node.kind = $('surface-kind').value;
      node.selections = chosen('fit-inputs');
      node.axial_domain = [Number($('axial-start').value), Number($('axial-end').value)];
      node.axis = $('fit-axis').value || null;
    }
    if (node.operation === 'axis') {
      if ($('axis-init-mode').value === 'fit') {
        node.source_fit = $('axis-source-fit').value;
        node.initial_parameters = null;
      } else {
        node.source_fit = null;
        node.initial_parameters = [
          Number($('axis-point-x').value),
          Number($('axis-point-y').value),
          Number($('axis-direction-x').value),
          Number($('axis-direction-y').value),
        ];
      }
    }
    if (node.operation === 'reference_plane') {
      node.axis = $('reference-plane-axis').value;
      node.initial_angle_degrees = Number($('reference-plane-angle').value);
    }
    if (node.operation === 'axis_solve') {
      node.axis = $('axis-solve-axis').value;
      node.factors = chosen('axis-solve-factors');
    }
    if (node.operation === 'growth') {
      node.seed_fit = $('growth-fit').value;
      node.barriers = chosen('growth-barriers');
      node.distance = Number($('growth-distance').value);
      node.angle_degrees = Number($('growth-angle').value);
    }
    if (node.operation === 'coaxial') {
      node.surface = $('constraint-a').value;
      node.reference = $('constraint-b').value;
    }
    if (node.operation === 'perpendicular') {
      node.lateral = $('constraint-a').value;
      node.plane = $('constraint-b').value;
    }
    if (node.operation === 'joint_fit') node.constraints = chosen('joint-inputs');
    if (node.operation === 'rotational_symmetry') {
      node.axis = $('rotation-axis').value;
      node.symmetric_extents = $('rotation-extents').checked;
      node.planes = [0, 1, 2].map((i) => $('rotation-input-' + i).value);
    }
    if (node.operation === 'mirror_symmetry') {
      node.plane = $('mirror-plane').value;
      try {
        node.surfaces = mirrorFitInputs(
          recipe.nodes,
          [0, 1].map((i) => $('mirror-input-' + i).value),
        );
      } catch (error) {
        status(error.message, true);
        return;
      }
      node.symmetric_extents = $('mirror-extents').checked;
    }
    if (node.operation === 'parallel') {
      node.surface = $('parallel-surface').value;
      node.reference_plane = $('parallel-reference').value;
    }
    if (node.operation === 'equal') {
      node.left = {
        measurement: 'radius',
        surface: $('equal-radius-surface').value,
      };
      node.right = {
        measurement: 'plane_distance',
        surface: $('equal-distance-surface').value,
        reference_plane: $('equal-distance-reference').value,
      };
    }
    await replaceRecipe(recipe);
  };
  $('delete-action').onclick = async () => {
    const recipe = structuredClone(graphState.recipe);
    recipe.nodes = recipe.nodes.filter((n) => n.id !== selectedFeatureId);
    if (recipe.output === selectedFeatureId) recipe.output = recipe.nodes.at(-1).id;
    selectedFeatureId = recipe.output;
    await replaceRecipe(recipe);
  };
  $('add-selection').onclick = async () => {
    const node = {
      id: uid('selection'),
      label: 'Selection ' + (Object.keys(session).length + 1),
      operation: 'selection',
      source: graphState.recipe.nodes.find((n) => n.operation === 'source').id,
      ids: [],
      depth: 'first_surface',
    };
    if (await appendActions([node])) {
      $('tool').value = 'add';
      $('tool').onchange();
      status('Selection added. Paint observations, then add a standalone fit.');
    }
  };
  $('add-fit-form').onsubmit = async (event) => {
    event.preventDefault();
    const saved = await appendActions([
      {
        id: uid('fit'),
        label: $('new-fit-label').value,
        operation: 'fit',
        selections: chosen('new-fit-inputs'),
        kind: $('new-fit-kind').value,
        axial_domain: [-2, 5],
        axis: $('new-fit-axis').value || null,
      },
    ]);
    if (saved) $('fit-dialog').close();
  };
  $('add-joint-form').onsubmit = async (event) => {
    event.preventDefault();
    const side = $('new-joint-side').value,
      planes = chosen('new-joint-plane');
    if (!side || !planes.length) {
      status('Choose an axis fit and at least one plane.', true);
      return;
    }
    const label = $('new-joint-label').value;
    const relations = planes.map((plane) => ({
      id: uid('perpendicular'),
      label: (graphNode(plane).label + ' perpendicular').slice(0, 120),
      operation: 'perpendicular',
      lateral: side,
      plane,
    }));
    for (const ref of chosen('new-joint-extra'))
      if (ref !== side)
        relations.push({
          id: uid('coaxial'),
          label: (graphNode(ref).label + ' coaxial').slice(0, 120),
          operation: 'coaxial',
          surface: ref,
          reference: side,
        });
    if (
      await appendActions([
        ...relations,
        {
          id: uid('joint'),
          label,
          operation: 'joint_fit',
          constraints: relations.map((n) => n.id),
        },
      ])
    )
      $('joint-dialog').close();
  };
  $('propose-growth').onclick = async () => {
    const node = graphNode(selectedFeatureId);
    const inputs = new Set();
    const visit = (id) => {
      if (inputs.has(id)) return;
      inputs.add(id);
      for (const dep of refs(graphNode(id))) visit(dep);
    };
    node.selections.forEach(visit);
    const barriers = graphState.recipe.nodes
      .filter((n) => n.operation === 'selection' && !inputs.has(n.id))
      .map((n) => n.id);
    if (
      await appendActions([
        {
          id: uid('growth'),
          label: (node.label + ' growth').slice(0, 120),
          operation: 'growth',
          seed_fit: node.id,
          barriers,
          distance: 0.05,
          angle_degrees: 20,
        },
      ], false)
    )
      await fit();
  };
  $('use-growth').onclick = async () => {
    const growth = graphNode(selectedFeatureId),
      seed = graphNode(growth.seed_fit);
    await appendActions([
      {
        id: uid('fit'),
        label: (seed.label + ' grown fit').slice(0, 120),
        operation: 'fit',
        selections: [growth.id],
        kind: seed.kind,
        axial_domain: seed.axial_domain,
      },
    ]);
  };
  $('home').onclick = () => home();
  $('side').onclick = () => home('side');
  $('top').onclick = () => home('top');
  for (const name of ['colors', 'guides', 'points']) $(name).onchange = paint;
  $('all-guides').onchange = () => {
    showResult();
    paint();
  };
  $('save').onclick = async () => {
    try {
      const recipe = (await request('/api/graph')).recipe;
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(recipe, null, 2) + '\n'], { type: 'application/json' }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = 'nozzle-actions.json';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      status(error.message, true);
    }
  };
  $('load').onclick = () => $('file').click();
  $('file').onchange = async () => {
    try {
      const file = $('file').files[0];
      if (!file) return;
      if (file.size > 1_000_000) throw new Error('Recipe must be under 1 MB');
      await replaceRecipe(JSON.parse(await file.text()));
    } catch (error) {
      status(error.message, true);
    } finally {
      $('file').value = '';
    }
  };
  let stroke = null,
    projectionCache = null;
  const canvas = renderer.domElement;
  const xy = (event) => {
    const r = canvas.getBoundingClientRect();
    return [event.clientX - r.left, event.clientY - r.top];
  };
  const cursor = (event) => {
    const brush = $('brush-cursor'),
      diameter = Number($('brush-size').value),
      point = xy(event);
    brush.hidden =
      !activeSelection() || busy || selectionPending ||
      $('selection-shape').value !== 'paint' ||
      $('tool').value === 'orbit' ||
      (event.buttons && !(event.buttons & 1));
    Object.assign(brush.style, {
      left: `${point[0] - diameter / 2}px`,
      top: `${point[1] - diameter / 2}px`,
      width: `${diameter}px`,
      height: `${diameter}px`,
    });
  };
  const cancelStroke = () => {
    if (!stroke) return;
    const pointerId = stroke.pointerId;
    stroke = null;
    selectionDrawing = false;
    $('rectangle').hidden = true;
    if (canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);
    acceptGraph(graphState);
    status('Selection gesture cancelled.');
  };
  const preview = () => {
    session = editMembership(stroke.original, stroke.region, stroke.hits, stroke.operation);
    result = null;
    clearGuides();
    $('metrics').replaceChildren();
    paint();
  };
  const extend = (point) => {
    if (stroke.shape === 'paint') {
      for (const id of brushHits(
        stroke.projection,
        stroke.previous,
        point,
        stroke.radius,
        stroke.depth,
      ))
        stroke.hits.add(id);
      stroke.previous = point;
      preview();
    } else {
      const start = stroke.start;
      Object.assign($('rectangle').style, {
        left: `${Math.min(start[0], point[0])}px`,
        top: `${Math.min(start[1], point[1])}px`,
        width: `${Math.abs(start[0] - point[0])}px`,
        height: `${Math.abs(start[1] - point[1])}px`,
      });
      $('rectangle').hidden = false;
    }
  };
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) {
      cancelStroke();
      return;
    }
    if ($('tool').value === 'orbit' || busy || selectionPending) return;
    if (!activeSelection()) return;
    cancelStroke();
    event.preventDefault();
    canvas.focus();
    camera.updateMatrixWorld();
    mesh.updateMatrixWorld();
    const matrix = new THREE.Matrix4()
      .multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
      .multiply(mesh.matrixWorld);
    const key = [
      ...matrix.elements,
      viewport.clientWidth,
      viewport.clientHeight,
      $('selection-depth').value,
    ].join(',');
    if (projectionCache?.key !== key) {
      const clip = new Float64Array((positions.length / 3) * 4),
        vector = new THREE.Vector4();
      for (let i = 0; i < positions.length / 3; i++) {
        vector
          .set(positions[3 * i], positions[3 * i + 1], positions[3 * i + 2], 1)
          .applyMatrix4(matrix);
        vector.toArray(clip, i * 4);
      }
      projectionCache = {
        key,
        value: selectionProjection(
          clip,
          geometry.index.array,
          viewport.clientWidth,
          viewport.clientHeight,
          $('selection-depth').value === 'first_surface',
        ),
      };
    }
    const point = xy(event);
    stroke = {
      pointerId: event.pointerId,
      original: structuredClone(session),
      region: selectedFeatureId,
      operation: $('tool').value,
      shape: $('selection-shape').value,
      depth: $('selection-depth').value,
      radius: Number($('brush-size').value) / 2,
      projection: projectionCache.value,
      start: point,
      previous: point,
      hits: new Set(),
    };
    status('Selection preview. Release to apply; Escape to cancel.');
    selectionDrawing = true;
    canvas.setPointerCapture(event.pointerId);
    extend(point);
    paint();
  });
  canvas.addEventListener('pointermove', (event) => {
    cursor(event);
    if (stroke && stroke.pointerId === event.pointerId) extend(xy(event));
  });
  canvas.addEventListener('pointerleave', () => {
    $('brush-cursor').hidden = true;
  });
  canvas.addEventListener('pointerup', async (event) => {
    if (!stroke || stroke.pointerId !== event.pointerId || event.button !== 0) return;
    const end = xy(event);
    extend(end);
    if (stroke.shape === 'rectangle') {
      const a = stroke.start;
      stroke.projection.points.forEach((p, id) => {
        if (
          p &&
          p[0] >= Math.min(a[0], end[0]) &&
          p[0] <= Math.max(a[0], end[0]) &&
          p[1] >= Math.min(a[1], end[1]) &&
          p[1] <= Math.max(a[1], end[1]) &&
          (stroke.depth === 'through_all' || stroke.projection.visible(id))
        )
          stroke.hits.add(id);
      });
    }
    const completed = stroke;
    stroke = null;
    selectionDrawing = false;
    selectionPending = true;
    $('rectangle').hidden = true;
    document.querySelector('aside').inert = true;
    $('creation-toolbar').inert = true;
    $('project-toolbar').inert = true;
    status('Saving selection…');
    try {
      await change(
        editMembership(completed.original, completed.region, completed.hits, completed.operation),
        completed.region,
        completed.depth,
      );
    } catch (error) {
      acceptGraph(graphState);
      status('Could not verify selection save. Reload the viewer: ' + error.message, true);
    } finally {
      selectionPending = false;
      document.querySelector('aside').inert = false;
      $('creation-toolbar').inert = false;
      $('project-toolbar').inert = false;
      paint();
    }
  });
  canvas.addEventListener('pointercancel', cancelStroke);
  canvas.addEventListener('lostpointercapture', cancelStroke);
  canvas.addEventListener('wheel', cancelStroke, { capture: true });
  window.addEventListener('blur', cancelStroke);
  window.addEventListener('resize', cancelStroke);
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') cancelStroke();
  });
  for (const id of ['selection-shape', 'selection-depth', 'tool', 'brush-size']) {
    $(id).addEventListener('input', cancelStroke);
  }
  $('selection-shape').onchange = () => {
    $('brush-settings').hidden = $('selection-shape').value !== 'paint';
    $('brush-cursor').hidden = true;
  };
  $('brush-size').oninput = () => {
    $('brush-size-value').textContent = $('brush-size').value + ' px';
  };
  const state = await request('/api/graph');
  acceptGraph(state);
  status('Feature graph loaded. Ready to evaluate.');
}
await start();
