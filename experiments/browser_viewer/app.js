import {
  actionDescription,
  discoverReuseLineage,
  managedOwnerId,
  managedSubtreeIds,
  nodeReferences as refs,
  reconcileFeatureReuse,
  renderActionTree,
} from './action-tree.js';
import { uniqueFeatureLabel } from './feature-names.js';
import { selectionVolumePositions } from './reuse-volume.js';
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
let renderer, scene, camera, controls, mesh, selectedPoints, overlays, reuseVolumes;
let metadata,
  positions,
  session,
  result = null,
  busy = false;
let pending = false,
  frames = 0;
let graphState, selectedFeatureId, editingGroupId = null;
let overlapMarkers, overlapHalo, activeOverlap, focusedPoints;
let selectionDrawing = false,
  selectionPending = false;
const palette = ['#f2b544', '#bd91f4', '#67dba2', '#ec9174', '#72b7ed', '#e6d979'];
const status = (message, error = false) => {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
};
function downloadJson(filename, value) {
  const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }),
    ),
    link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
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
function clearReuseVolumes() {
  for (const child of [...reuseVolumes.children]) {
    reuseVolumes.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  reuseVolumes.userData.volumeCount = 0;
}
function showReuseVolumes() {
  clearReuseVolumes();
  if (!$('reuse-volumes').checked || !graphState) return;
  const targetColors = new Map();
  for (const node of graphState.recipe.nodes) {
    if (node.operation !== 'reuse_selection' || graphState.states[node.id] !== 'ready') continue;
    const result = graphState.results[node.id];
    if (!result?.region || !result.target_origin || !result.target_rotation) continue;
    if (!targetColors.has(node.target_selection))
      targetColors.set(
        node.target_selection,
        palette[targetColors.size % palette.length],
      );
    const volumePositions = selectionVolumePositions(
      result.region,
      result.target_origin,
      result.target_rotation,
    );
    if (!volumePositions.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(volumePositions, 3));
    geometry.computeVertexNormals();
    const color = targetColors.get(node.target_selection),
      volume = new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: 0.14,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      ),
      outline = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, 12),
        new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0.8,
          depthTest: false,
        }),
      );
    volume.renderOrder = 3;
    outline.renderOrder = 4;
    reuseVolumes.add(volume, outline);
    reuseVolumes.userData.volumeCount++;
  }
}
function graphNode(id) {
  return graphState.recipe.nodes.find((n) => n.id === id);
}
function nextFeatureLabel(base, reserved = []) {
  return uniqueFeatureLabel(base, [
    ...(graphState?.recipe.nodes || []),
    ...reserved.map((label) => ({ label })),
  ]);
}
function submittedFeatureLabel(inputId) {
  const input = $(inputId),
    label = nextFeatureLabel(input.value);
  input.value = label;
  return label;
}
function reserveFeatureLabel(base, reserved) {
  const label = nextFeatureLabel(base, reserved);
  reserved.push(label);
  return label;
}
function nextGroupLabel(base) {
  return uniqueFeatureLabel(base, graphState?.recipe.groups || []);
}
function openFeatureGroup(groupId = null) {
  editingGroupId = groupId;
  const group = (graphState.recipe.groups || []).find((candidate) => candidate.id === groupId),
    members = graphState.recipe.nodes.filter(
      (node) => !managedOwnerId(node, graphState.recipe.nodes),
    );
  $('feature-group-dialog-title').textContent = group
    ? 'Edit organizational group'
    : 'New organizational group';
  $('save-feature-group').textContent = group ? 'Apply group' : 'Create group';
  $('feature-group-label').value = group?.label || nextGroupLabel('Group');
  choices(
    'feature-group-members',
    members,
    group
      ? members.filter((node) => node.group_id === group.id).map((node) => node.id)
      : [selectedFeatureId],
  );
  $('feature-group-error').textContent = '';
  $('feature-group-dialog').showModal();
  $('feature-group-label').focus();
  $('feature-group-label').select();
}
async function removeFeatureGroup(groupId) {
  const recipe = structuredClone(graphState.recipe);
  recipe.groups = (recipe.groups || []).filter((group) => group.id !== groupId);
  for (const node of recipe.nodes)
    if (node.group_id === groupId) node.group_id = null;
  await replaceRecipe(recipe, false);
}
function showCreateDialog(dialogId, labelId, defaultLabel) {
  const input = $(labelId);
  input.value = nextFeatureLabel(defaultLabel);
  $(dialogId).showModal();
  input.focus();
  input.select();
}
function factorAxis(node) {
  if (node?.operation === 'fit')
    return node.axis || graphNode(node.reference_plane)?.axis;
  if (node?.operation === 'mirror_symmetry') return graphNode(node.plane)?.axis;
  if (node?.operation === 'parallel') return graphNode(node.reference_plane)?.axis;
  if (node?.operation === 'equal') {
    const distance = [node.left, node.right].find((value) => value.measurement === 'plane_distance');
    return graphNode(distance?.reference_plane)?.axis;
  }
  return null;
}
const relationshipOperations = ['mirror_symmetry', 'parallel', 'equal'];
const selectionOperations = ['selection', 'growth', 'region_selection', 'reuse_selection'];
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
function isStandaloneFit(node) {
  return !node.axis && !node.reference_plane;
}
function axialDatumPlanes(nodes) {
  return nodes.filter(
    (node) =>
      node.operation === 'reference_plane' &&
      node.construction === 'perpendicular_to_axis',
  );
}
function clockDatumPlanes(nodes, axis = null) {
  return nodes.filter(
    (node) =>
      node.operation === 'reference_plane' &&
      ['contains_axis', 'parallel_to_axis'].includes(node.construction) &&
      (!axis || node.axis === axis),
  );
}
function fitReferenceChoices(id, kind, nodes, selected = '') {
  const references = nodes.filter((node) =>
    ['axis', 'reference_plane'].includes(node.operation),
  );
  $(id).replaceChildren(
    new Option('None (standalone)', '', false, !selected),
    ...references.map(
      (node) =>
        new Option(
          `${node.operation === 'axis' ? 'Axis' : 'Plane'} — ${node.label}`,
          node.id,
          false,
          node.id === selected,
        ),
    ),
  );
  const hint = $(`${id}-hint`);
  if (hint)
    hint.textContent =
      kind === 'plane'
        ? 'An axis makes the fitted plane perpendicular to it. A plane datum fixes its orientation. In either case, observations fit the plane offset.'
        : 'An axis is shared by the fitted surface. Connected fits refine a manually initialized free axis; a fit-initialized axis stays fixed. Choosing a plane datum switches the fit type to Plane.';
}
function updateFitKindForReference(kindId, referenceId, nodes = graphState.recipe.nodes) {
  if (graphNode($(referenceId).value)?.operation === 'reference_plane')
    $(kindId).value = 'plane';
  fitReferenceChoices(
    referenceId,
    $(kindId).value,
    nodes,
    $(referenceId).value,
  );
}
function setFitReference(node, referenceId) {
  const reference = graphNode(referenceId);
  node.axis = reference?.operation === 'axis' ? reference.id : null;
  node.reference_plane = reference?.operation === 'reference_plane' ? reference.id : null;
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
function axisContainingPlanes(nodes) {
  return nodes.filter(
    (node) =>
      node.operation === 'reference_plane' &&
      (node.construction || 'contains_axis') === 'contains_axis',
  );
}
function planeConstructionLabel(construction) {
  return {
    contains_axis: 'Contains axis',
    parallel_to_axis: 'Parallel to axis',
    perpendicular_to_axis: 'Perpendicular to axis',
  }[construction || 'contains_axis'];
}
function showReferencePlaneFields(prefix = '') {
  const construction = $(`${prefix}reference-plane-construction`).value,
    angleRow = $(`${prefix}reference-plane-angle-row`),
    angle = $(`${prefix}reference-plane-angle`),
    offsetRow = $(`${prefix}reference-plane-offset-row`),
    offsetLabel = $(`${prefix}reference-plane-offset-label`),
    hint = $(`${prefix}reference-plane-hint`),
    perpendicular = construction === 'perpendicular_to_axis',
    contains = construction === 'contains_axis';
  angleRow.hidden = perpendicular;
  angle.disabled = perpendicular;
  offsetRow.hidden = contains;
  $(`${prefix}reference-plane-offset`).disabled = contains;
  offsetLabel.textContent = perpendicular
    ? 'Axial offset from axis origin'
    : 'Signed normal offset from axis';
  hint.textContent = contains
    ? 'Contains the entire axis. Clocking sets its orientation around the axis; mirror symmetry may refine it in a joint.'
    : perpendicular
      ? 'Normal to the axis. Offset moves it along the axis from the axis point at local Z=0.'
      : 'Parallel to, but not necessarily containing, the axis. Clocking sets orientation and offset sets signed separation.';
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
    state.recipe.nodes.filter((n) => selectionOperations.includes(n.operation)),
    [selectedFeatureId],
  );
  const fits = state.recipe.nodes.filter((n) => n.operation === 'fit');
  const axes = state.recipe.nodes.filter((n) => n.operation === 'axis');
  fitReferenceChoices('new-fit-reference', $('new-fit-kind').value, state.recipe.nodes);
  choices('new-reference-plane-axis', axes);
  choices(
    'new-mirror-plane',
    axisContainingPlanes(state.recipe.nodes),
  );
  choices(
    'new-axis-source',
    fits.filter((n) => ['cone', 'cylinder'].includes(n.kind) && isStandaloneFit(n)),
  );
  choices('new-axis-solve-axis', axes);
  choices(
    'new-joint-side',
    fits.filter((n) => n.kind !== 'plane' && isStandaloneFit(n)),
  );
  choices(
    'new-joint-plane',
    fits.filter((n) => n.kind === 'plane' && isStandaloneFit(n)),
  );
  choices(
    'new-joint-extra',
    fits.filter((n) => n.kind !== 'plane' && isStandaloneFit(n)),
  );
  renderActions();
  showProperties();
  showResult();
  showReuseVolumes();
  paint();
}
function renderActions() {
  renderActionTree($('action-list'), {
    nodes: graphState.recipe.nodes,
    groups: graphState.recipe.groups || [],
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
    editGroup: openFeatureGroup,
    removeGroup: (groupId) => void removeFeatureGroup(groupId),
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
  choices(
    'action-group',
    [{ id: '', label: 'No group' }, ...(graphState.recipe.groups || [])],
    [node.group_id || ''],
  );
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
    ['selection-region-properties', node.operation === 'selection_region'],
    ['region-selection-properties', node.operation === 'region_selection'],
    ['feature-reuse-properties', node.operation === 'feature_reuse'],
    ['reuse-selection-properties', node.operation === 'reuse_selection'],
    ['equal-radii-properties', node.operation === 'equal_radii'],
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
      earlier.filter((n) => selectionOperations.includes(n.operation)),
      node.selections,
    );
    fitReferenceChoices(
      'fit-reference',
      node.kind,
      earlier,
      node.axis || node.reference_plane || '',
    );
    $('axial-start').value = node.axial_domain[0];
    $('axial-end').value = node.axial_domain[1];
  } else if (node.operation === 'axis') {
    $('axis-init-mode').value = node.source_fit ? 'fit' : 'free';
    choices(
      'axis-source-fit',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          ['cone', 'cylinder'].includes(n.kind) &&
          isStandaloneFit(n),
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
    $('reference-plane-construction').value = node.construction || 'contains_axis';
    $('reference-plane-angle').value = node.initial_angle_degrees ?? 0;
    $('reference-plane-offset').value = node.offset ?? 0;
    showReferencePlaneFields();
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
      earlier.filter((n) => n.operation === 'fit' && isStandaloneFit(n)),
      [node.seed_fit],
    );
    choices(
      'growth-barriers',
      earlier.filter((n) => selectionOperations.includes(n.operation)),
      node.barriers,
    );
    $('growth-distance').value = node.distance;
    $('growth-angle').value = node.angle_degrees;
  } else if (node.operation === 'selection_region') {
    choices(
      'selection-region-selection',
      earlier.filter((n) => selectionOperations.includes(n.operation)),
      [node.selection],
    );
    choices(
      'selection-region-fit',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          ['cylinder', 'plane'].includes(n.kind) &&
          n.selections.includes(node.selection),
      ),
      [node.fit],
    );
    choices('selection-region-axial', axialDatumPlanes(earlier), [node.axial_plane]);
    const sourceAxis = graphNode(node.axial_plane)?.axis;
    choices(
      'selection-region-clock',
      clockDatumPlanes(earlier, sourceAxis),
      [node.clock_plane],
    );
    $('selection-region-tangent-margin').value = node.tangent_margin;
    $('selection-region-normal-margin').value = node.normal_margin;
    $('selection-region-normal-angle').value = node.normal_angle_degrees;
  } else if (node.operation === 'region_selection') {
    choices(
      'region-selection-region',
      earlier.filter((n) => n.operation === 'selection_region'),
      [node.region],
    );
    choices('region-selection-axial', axialDatumPlanes(earlier), [node.axial_plane]);
    const targetAxis = graphNode(node.axial_plane)?.axis;
    choices(
      'region-selection-clock',
      clockDatumPlanes(earlier, targetAxis),
      [node.clock_plane],
    );
  } else if (node.operation === 'feature_reuse') {
    const ownedSelections = new Set(
        graphState.recipe.nodes
          .filter(
            (candidate) =>
              candidate.operation === 'reuse_selection' && candidate.reuse === node.id,
          )
          .map((candidate) => candidate.id),
      ),
      owned = new Set([
        ...ownedSelections,
        ...graphState.recipe.nodes
          .filter(
            (candidate) =>
              candidate.operation === 'fit' &&
              candidate.selections.length > 0 &&
              candidate.selections.every((id) => ownedSelections.has(id)),
          )
          .map((candidate) => candidate.id),
      ]),
      available = graphState.recipe.nodes.filter(
        (candidate) => candidate.id !== node.id && !owned.has(candidate.id),
      );
    choices(
      'feature-reuse-fits',
      available.filter(
        (candidate) =>
          candidate.operation === 'fit' && ['cylinder', 'plane'].includes(candidate.kind),
      ),
      node.fits,
    );
    choices(
      'feature-reuse-reference',
      available.filter((candidate) => selectionOperations.includes(candidate.operation)),
      [node.reference_selection],
    );
    choices(
      'feature-reuse-target',
      available.filter((candidate) => selectionOperations.includes(candidate.operation)),
      node.target_selections,
    );
    $('feature-reuse-tangent-margin').value = node.tangent_margin;
    $('feature-reuse-normal-margin').value = node.normal_margin;
    $('feature-reuse-normal-angle').value = node.normal_angle_degrees;
    $('feature-reuse-equal-dimensions').checked =
      node.equal_corresponding_dimensions === true;
  } else if (node.operation === 'reuse_selection') {
    choices(
      'reuse-selection-reuse',
      earlier.filter((candidate) => candidate.operation === 'feature_reuse'),
      [node.reuse],
    );
    choices(
      'reuse-selection-fit',
      earlier.filter((candidate) => candidate.operation === 'fit'),
      [node.fit],
    );
    choices(
      'reuse-selection-source',
      earlier.filter((candidate) => selectionOperations.includes(candidate.operation)),
      [node.source_selection],
    );
  } else if (node.operation === 'equal_radii') {
    choices(
      'equal-radii-surfaces',
      earlier.filter((candidate) => node.surfaces.includes(candidate.id)),
      node.surfaces,
    );
  } else if (['coaxial', 'perpendicular'].includes(node.operation)) {
    choices(
      'constraint-a',
      earlier.filter(
        (n) => n.operation === 'fit' && n.kind !== 'plane' && isStandaloneFit(n),
      ),
      [node.surface || node.lateral],
    );
    choices(
      'constraint-b',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          isStandaloneFit(n) &&
          (node.operation === 'coaxial' ? n.kind !== 'plane' : n.kind === 'plane'),
      ),
      [node.reference || node.plane],
    );
  } else if (node.operation === 'rotational_symmetry') {
    $('rotation-extents').checked = node.symmetric_extents !== false;
    choices(
      'rotation-axis',
      earlier.filter(
        (n) => n.operation === 'fit' && n.kind !== 'plane' && isStandaloneFit(n),
      ),
      [node.axis],
    );
    node.planes.forEach((id, i) =>
      choices(
        'rotation-input-' + i,
        earlier.filter((n) => n.operation === 'fit' && isStandaloneFit(n)),
        [id],
      ),
    );
  } else if (node.operation === 'mirror_symmetry') {
    choices(
      'mirror-plane',
      axisContainingPlanes(earlier),
      [node.plane],
    );
    node.surfaces.forEach((id, i) =>
      choices(
        'mirror-input-' + i,
        earlier.filter((n) => n.operation === 'fit' && isStandaloneFit(n)),
        [id],
      ),
    );
    $('mirror-extents').checked = node.symmetric_extents !== false;
  } else if (node.operation === 'parallel') {
    choices(
      'parallel-surface',
      earlier.filter(
        (n) => n.operation === 'fit' && n.kind === 'plane' && isStandaloneFit(n),
      ),
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
      earlier.filter(
        (n) => n.operation === 'fit' && n.kind === 'plane' && isStandaloneFit(n),
      ),
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
        (n) => n.operation === 'fit' && isStandaloneFit(n) && !used.has(n.id),
      ),
    );
  }
  const removalIds = managedSubtreeIds(node.id, graphState.recipe.nodes),
    dependents = graphState.recipe.nodes.filter(
      (candidate) =>
        !removalIds.has(candidate.id) &&
        refs(candidate).some((reference) => removalIds.has(reference)),
    );
  const ownerId = managedOwnerId(node, graphState.recipe.nodes),
    managed = !!ownerId,
    owner = graphNode(ownerId);
  $('managed-feature-note').hidden = !managed;
  $('action-group-row').hidden = managed;
  if (managed) {
    $('managed-owner-name').textContent = owner?.label || ownerId;
    $('select-managed-owner').onclick = () => {
      selectedFeatureId = ownerId;
      renderActions();
      showProperties();
      showResult();
      paint();
    };
  }
  for (const control of $('action-properties').querySelectorAll('input, select, button')) {
    if (control.id === 'select-managed-owner') continue;
    if (managed && !control.disabled) {
      control.disabled = true;
      control.dataset.managedDisabled = 'true';
    } else if (!managed && control.dataset.managedDisabled === 'true') {
      control.disabled = false;
      delete control.dataset.managedDisabled;
    }
  }
  $('delete-action').disabled =
    managed || !!dependents.length || graphState.recipe.nodes.length === 1;
  $('delete-action').title = managed
    ? `Managed by ${owner?.label || ownerId}`
    : dependents.length
      ? 'Referenced by ' + dependents.map((n) => n.label).join(', ')
      : removalIds.size > 1
        ? `Delete this action and ${removalIds.size - 1} managed outputs`
        : 'Delete this unused action';
  $('fit').textContent = 'Evaluate ' + node.label;
  $('propose-growth').hidden = node.operation !== 'fit' || !isStandaloneFit(node);
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
  const construction = node.construction || 'contains_axis',
    offset = node.offset ?? 0,
    anchor = new THREE.Vector3(...values.point_display);
  let basisU, basisV, normal, point;
  if (construction === 'perpendicular_to_axis') {
    basisU = u;
    basisV = v;
    normal = axis;
    point = anchor.clone().addScaledVector(axis, offset);
  } else {
    const angle = ((node.initial_angle_degrees ?? 0) * Math.PI) / 180;
    basisU = axis;
    basisV = u.multiplyScalar(Math.cos(angle)).addScaledVector(v, Math.sin(angle));
    normal = new THREE.Vector3().crossVectors(basisU, basisV);
    point = anchor.clone().addScaledVector(normal, offset);
  }
  return {
    axis_display: axis.toArray(),
    basis_u_display: basisU.toArray(),
    basis_v_display: basisV.toArray(),
    point_display: point.toArray(),
    radial_display: basisV.toArray(),
    normal_display: normal.toArray(),
    angle_degrees: node.initial_angle_degrees ?? null,
    offset,
    construction,
  };
}
function referencePlaneGuide(values, color = '#ff8fe5') {
  const basisU = new THREE.Vector3(
      ...(values.basis_u_display || values.axis_display),
    ).normalize(),
    basisV = new THREE.Vector3(
      ...(values.basis_v_display || values.radial_display),
    ).normalize(),
    point = new THREE.Vector3(...values.point_display),
    domain = metadata?.axial_domain || [-2, 5],
    halfWidth = Math.max(1, (domain[1] - domain[0]) * 0.35);
  let corners;
  if (values.construction === 'perpendicular_to_axis')
    corners = [
      point.clone().addScaledVector(basisU, -halfWidth).addScaledVector(basisV, -halfWidth),
      point.clone().addScaledVector(basisU, -halfWidth).addScaledVector(basisV, halfWidth),
      point.clone().addScaledVector(basisU, halfWidth).addScaledVector(basisV, halfWidth),
      point.clone().addScaledVector(basisU, halfWidth).addScaledVector(basisV, -halfWidth),
    ];
  else
    corners = [
      point.clone().addScaledVector(basisU, domain[0]).addScaledVector(basisV, -halfWidth),
      point.clone().addScaledVector(basisU, domain[0]).addScaledVector(basisV, halfWidth),
      point.clone().addScaledVector(basisU, domain[1]).addScaledVector(basisV, halfWidth),
      point.clone().addScaledVector(basisU, domain[1]).addScaledVector(basisV, -halfWidth),
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
    ...Object.keys(selectedResult?.reference_planes || {}),
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
        'equal_radii',
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
    // Some constrained solves replace an earlier fit result with an exact
    // relationship result. Those results retain residuals but do not always
    // have a standalone-fit condition estimate.
    if (Number.isFinite(result.condition))
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
    values['Construction'] = planeConstructionLabel(result.construction);
    if (result.angle_degrees != null)
      values['Clocking'] = `${result.angle_degrees.toFixed(4)}°`;
    if (result.construction !== 'contains_axis')
      values['Offset'] = result.offset.toFixed(5);
    values['Normal'] = result.normal_display.map((v) => v.toFixed(5)).join(', ');
  } else if (['joint_fit', 'axis_solve'].includes(node.operation)) {
    axisGuide(result, '#ffd166');
    for (const plane of Object.values(result.mirror_planes || {}))
      referencePlaneGuide(plane, '#ff8fe5');
    for (const plane of Object.values(result.reference_planes || {}))
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
  } else if (node.operation === 'selection_region') {
    values['Surface type'] = result.kind;
    values['Source vertices'] = result.selected_count;
    values['Footprint margin'] = result.tangent_margin.toFixed(3);
    values['Surface-normal margin'] = result.normal_margin.toFixed(3);
  } else if (node.operation === 'region_selection') {
    values['Resolved vertices'] = result.vertex_count;
  } else if (node.operation === 'feature_reuse') {
    const matches = Object.values(result.matches);
    values['Targets'] = matches.length;
    values['Worst match RMS'] = Math.max(...matches.map((match) => match.rms)).toFixed(4);
    values['Lowest rotation ambiguity ratio'] = Math.min(
      ...matches.map((match) => match.ambiguity_ratio),
    ).toFixed(2);
    values['Reference vertices'] = matches[0].source_count;
    values['Captured lineage actions'] = result.lineage.length;
  } else if (node.operation === 'reuse_selection') {
    values['Resolved vertices'] = result.vertex_count;
    values['Source selection'] = graphNode(result.source_selection).label;
  } else if (node.operation === 'equal_radii') {
    values['Shared radius'] = result.value.toFixed(5);
    values['Cylinders'] = node.surfaces.length;
    Object.entries(result.surfaces).forEach(([id, surface], index) =>
      fitGuide(graphNode(id), surface, palette[index % palette.length]),
    );
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
  if ($('reuse-volumes').checked)
    $('legend').textContent += reuseVolumes.userData.volumeCount
      ? ' Translucent overlays show evaluated reuse envelopes; normal-angle filtering still applies.'
      : ' No evaluated reuse envelopes are available.';
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
  reuseVolumes = new THREE.Group();
  reuseVolumes.userData.volumeCount = 0;
  scene.add(reuseVolumes);
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
      graphState.recipe.nodes.filter((node) => selectionOperations.includes(node.operation)),
      [selectedFeatureId],
    );
    fitReferenceChoices(
      'new-fit-reference',
      $('new-fit-kind').value,
      graphState.recipe.nodes,
    );
    showCreateDialog('fit-dialog', 'new-fit-label', 'Surface fit');
  };
  $('new-feature-group').onclick = () => openFeatureGroup();
  $('feature-group-form').onsubmit = async (event) => {
    event.preventDefault();
    const memberIds = new Set(chosen('feature-group-members')),
      recipe = structuredClone(graphState.recipe),
      label = $('feature-group-label').value.trim();
    if (!label) {
      $('feature-group-error').textContent = 'Enter a group name.';
      return;
    }
    recipe.groups ||= [];
    let groupId = editingGroupId;
    if (groupId) recipe.groups.find((group) => group.id === groupId).label = label;
    else {
      groupId = uid('group');
      recipe.groups.push({ id: groupId, label });
    }
    for (const node of recipe.nodes) {
      if (managedOwnerId(node, recipe.nodes)) continue;
      if (memberIds.has(node.id)) node.group_id = groupId;
      else if (node.group_id === groupId) node.group_id = null;
    }
    if (await replaceRecipe(recipe, false)) $('feature-group-dialog').close();
    else $('feature-group-error').textContent = $('status').textContent;
  };
  $('new-feature-reuse').onclick = () => {
    const fits = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && ['cylinder', 'plane'].includes(node.kind),
      ),
      selections = graphState.recipe.nodes.filter(
        (node) => selectionOperations.includes(node.operation),
      );
    if (!fits.length || selections.length < 2) {
      status('Feature reuse needs at least one cylinder or plane fit and two selections.', true);
      return;
    }
    const selected = graphNode(selectedFeatureId),
      reference = selectionOperations.includes(selected?.operation)
        ? selected
        : selections.find((node) => /^ref(?:erence)?(?:\b|[-_])/i.test(node.label)) || selections[0],
      target = selections.find(
        (node) => node.id !== reference.id && /^target(?:\b|[-_])/i.test(node.label),
      ) || selections.find((node) => node.id !== reference.id);
    choices(
      'new-feature-reuse-fits',
      fits,
      selected?.operation === 'fit' && fits.includes(selected) ? [selected.id] : [],
    );
    choices('new-feature-reuse-reference', selections, [reference.id]);
    choices('new-feature-reuse-target', selections, [target.id]);
    $('new-feature-reuse-equal-dimensions').checked = false;
    $('feature-reuse-error').textContent = '';
    showCreateDialog('feature-reuse-dialog', 'new-feature-reuse-label', 'Feature reuse');
  };
  const regionFits = (selectionId, nodes = graphState.recipe.nodes) =>
    nodes.filter(
      (node) =>
        node.operation === 'fit' &&
        ['cylinder', 'plane'].includes(node.kind) &&
        node.selections.includes(selectionId),
    );
  const updateNewRegionFitChoices = () =>
    choices(
      'new-selection-region-fit',
      regionFits($('new-selection-region-selection').value),
    );
  const updateNewRegionClockChoices = () => {
    const axis = graphNode($('new-selection-region-axial').value)?.axis;
    choices('new-selection-region-clock', clockDatumPlanes(graphState.recipe.nodes, axis));
  };
  $('new-selection-region').onclick = () => {
    const selections = graphState.recipe.nodes.filter(
        (node) => selectionOperations.includes(node.operation),
      ),
      fits = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && ['cylinder', 'plane'].includes(node.kind),
      ),
      axial = axialDatumPlanes(graphState.recipe.nodes),
      clock = clockDatumPlanes(graphState.recipe.nodes);
    if (!selections.length || !fits.length || !axial.length || !clock.length) {
      status(
        'A reusable region needs a fitted selection, a perpendicular axial plane, and an axis-parallel clock plane.',
        true,
      );
      return;
    }
    const selectedNode = graphNode(selectedFeatureId),
      selectedSelection = selectionOperations.includes(selectedNode?.operation)
        ? selectedNode.id
        : selectedNode?.operation === 'fit'
          ? selectedNode.selections[0]
          : selections[0].id;
    choices('new-selection-region-selection', selections, [selectedSelection]);
    updateNewRegionFitChoices();
    choices('new-selection-region-axial', axial);
    updateNewRegionClockChoices();
    $('selection-region-error').textContent = '';
    showCreateDialog(
      'selection-region-dialog',
      'new-selection-region-label',
      'Selection region',
    );
  };
  $('new-selection-region-selection').onchange = updateNewRegionFitChoices;
  $('new-selection-region-axial').onchange = updateNewRegionClockChoices;
  const updateNewAppliedRegionClockChoices = () => {
    const axis = graphNode($('new-region-selection-axial').value)?.axis;
    choices('new-region-selection-clock', clockDatumPlanes(graphState.recipe.nodes, axis));
  };
  $('new-region-selection').onclick = () => {
    const regions = graphState.recipe.nodes.filter(
        (node) => node.operation === 'selection_region',
      ),
      axial = axialDatumPlanes(graphState.recipe.nodes);
    if (!regions.length || !axial.length) {
      status('Create a reusable region and target datum frame first.', true);
      return;
    }
    choices('new-region-selection-region', regions, [selectedFeatureId]);
    choices('new-region-selection-axial', axial);
    updateNewAppliedRegionClockChoices();
    $('region-selection-error').textContent = '';
    showCreateDialog(
      'region-selection-dialog',
      'new-region-selection-label',
      'Applied selection',
    );
  };
  $('new-region-selection-axial').onchange = updateNewAppliedRegionClockChoices;
  const updateAxisSolveFactors = () => {
    const axis = $('new-axis-solve-axis').value;
    const inputs = solveInputs(axis);
    choices('new-axis-solve-factors', inputs, inputs.map((node) => node.id));
  };
  $('new-axis').onclick = () => {
    const sources = graphState.recipe.nodes.filter(
      (node) =>
        node.operation === 'fit' &&
        ['cone', 'cylinder'].includes(node.kind) &&
        isStandaloneFit(node),
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
    showCreateDialog('axis-dialog', 'new-axis-label', 'Reference axis');
  };
  $('new-reference-plane').onclick = () => {
    const axes = graphState.recipe.nodes.filter((node) => node.operation === 'axis');
    if (!axes.length) {
      status('Create an explicit axis before creating a reference plane.', true);
      return;
    }
    choices('new-reference-plane-axis', axes, [graphNode(selectedFeatureId)?.axis || selectedFeatureId]);
    $('new-reference-plane-construction').value = 'contains_axis';
    $('new-reference-plane-angle').value = 0;
    $('new-reference-plane-offset').value = 0;
    showReferencePlaneFields('new-');
    showCreateDialog(
      'reference-plane-dialog',
      'new-reference-plane-label',
      'Reference plane',
    );
  };
  const updateMirrorChoices = () => {
    const fits = graphState.recipe.nodes.filter(
      (node) => node.operation === 'fit' && isStandaloneFit(node),
    );
    const first = fits.find((fit) => fit.id === selectedFeatureId) || fits[0];
    const second = fits.find((fit) => fit.id !== first?.id);
    choices('new-mirror-input-0', fits, [first?.id]);
    choices('new-mirror-input-1', fits, [second?.id]);
  };
  $('new-mirror').onclick = () => {
    const planes = axisContainingPlanes(graphState.recipe.nodes);
    if (!planes.length) {
      status('Create a reference plane through an axis before adding mirror symmetry.', true);
      return;
    }
    if (
      graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && isStandaloneFit(node),
      ).length < 2
    ) {
      status('Create two standalone fits before adding mirror symmetry.', true);
      return;
    }
    choices('new-mirror-plane', planes, [graphNode(selectedFeatureId)?.plane || selectedFeatureId]);
    updateMirrorChoices();
    $('mirror-error').textContent = '';
    showCreateDialog('mirror-dialog', 'new-mirror-label', 'Mirrored pair');
  };
  $('new-parallel').onclick = () => {
    const fits = graphState.recipe.nodes.filter(
        (node) =>
          node.operation === 'fit' && node.kind === 'plane' && isStandaloneFit(node),
      ),
      planes = graphState.recipe.nodes.filter((node) => node.operation === 'reference_plane');
    if (!fits.length || !planes.length) {
      status('Create a standalone plane fit and a reference plane first.', true);
      return;
    }
    choices('new-parallel-surface', fits, [selectedFeatureId]);
    choices('new-parallel-reference', planes, [graphNode(selectedFeatureId)?.plane]);
    $('parallel-error').textContent = '';
    showCreateDialog('parallel-dialog', 'new-parallel-label', 'Parallel to plane');
  };
  $('new-equal').onclick = async () => {
    const selected = graphNode(selectedFeatureId);
    if (selected?.operation === 'feature_reuse') {
      if (selected.equal_corresponding_dimensions) {
        status(`${selected.label} already has equal corresponding dimensions.`);
        return;
      }
      if (!selected.fits.some((id) => graphNode(id)?.kind === 'cylinder')) {
        status('This reuse feature has no compatible cylinder dimensions.', true);
        return;
      }
      const recipe = structuredClone(graphState.recipe),
        reconciled = reconcileFeatureReuse(
          recipe.nodes,
          selected.id,
          { equal_corresponding_dimensions: true },
          uid,
        );
      if (reconciled.error) {
        status(reconciled.error, true);
        return;
      }
      recipe.nodes = reconciled.nodes;
      if (await replaceRecipe(recipe))
        status(`Created all-equal radius relationships for ${selected.label}.`);
      return;
    }
    const cylinders = graphState.recipe.nodes.filter(
        (node) => node.operation === 'fit' && node.kind === 'cylinder' && node.axis,
      ),
      fits = graphState.recipe.nodes.filter(
        (node) =>
          node.operation === 'fit' && node.kind === 'plane' && isStandaloneFit(node),
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
    showCreateDialog(
      'equal-dialog',
      'new-equal-label',
      'Radius equals plane distance',
    );
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
    showCreateDialog('axis-solve-dialog', 'new-axis-solve-label', 'Shared-axis joint');
  };
  $('new-axis-solve-axis').onchange = updateAxisSolveFactors;
  $('new-fit-kind').onchange = () => {
    if (
      $('new-fit-kind').value !== 'plane' &&
      graphNode($('new-fit-reference').value)?.operation === 'reference_plane'
    )
      $('new-fit-reference').value = '';
    fitReferenceChoices(
      'new-fit-reference',
      $('new-fit-kind').value,
      graphState.recipe.nodes,
      $('new-fit-reference').value,
    );
  };
  $('new-fit-reference').onchange = () =>
    updateFitKindForReference('new-fit-kind', 'new-fit-reference');
  $('surface-kind').onchange = () => {
    const node = graphNode(selectedFeatureId);
    if (
      $('surface-kind').value !== 'plane' &&
      graphNode($('fit-reference').value)?.operation === 'reference_plane'
    )
      $('fit-reference').value = '';
    fitReferenceChoices(
      'fit-reference',
      $('surface-kind').value,
      graphState.recipe.nodes.slice(0, graphState.recipe.nodes.indexOf(node)),
      $('fit-reference').value,
    );
  };
  $('fit-reference').onchange = () => {
    const node = graphNode(selectedFeatureId);
    updateFitKindForReference(
      'surface-kind',
      'fit-reference',
      graphState.recipe.nodes.slice(0, graphState.recipe.nodes.indexOf(node)),
    );
  };
  $('new-axis-mode').onchange = () =>
    showAxisInitializer(
      'new-axis-mode',
      'new-axis-source-fields',
      'new-axis-manual-fields',
    );
  $('axis-init-mode').onchange = () =>
    showAxisInitializer('axis-init-mode', 'axis-source-fields', 'axis-manual-fields');
  $('new-reference-plane-construction').onchange = () => showReferencePlaneFields('new-');
  $('reference-plane-construction').onchange = () => showReferencePlaneFields();
  $('axis-solve-axis').onchange = () => {
    const axis = $('axis-solve-axis').value;
    choices('axis-solve-factors', solveInputs(axis));
  };
  $('selection-region-selection').onchange = () =>
    choices(
      'selection-region-fit',
      regionFits(
        $('selection-region-selection').value,
        graphState.recipe.nodes.slice(
          0,
          graphState.recipe.nodes.indexOf(graphNode(selectedFeatureId)),
        ),
      ),
    );
  $('selection-region-axial').onchange = () =>
    choices(
      'selection-region-clock',
      clockDatumPlanes(
        graphState.recipe.nodes,
        graphNode($('selection-region-axial').value)?.axis,
      ),
    );
  $('region-selection-axial').onchange = () =>
    choices(
      'region-selection-clock',
      clockDatumPlanes(
        graphState.recipe.nodes,
        graphNode($('region-selection-axial').value)?.axis,
      ),
    );
  $('add-feature-reuse-form').onsubmit = async (event) => {
    event.preventDefault();
    const fitIds = chosen('new-feature-reuse-fits'),
      referenceSelection = $('new-feature-reuse-reference').value,
      targetSelections = chosen('new-feature-reuse-target');
    if (!fitIds.length || !referenceSelection || !targetSelections.length) {
      $('feature-reuse-error').textContent =
        'Choose at least one fit, a painted reference, and one or more painted targets.';
      return;
    }
    if (targetSelections.includes(referenceSelection)) {
      $('feature-reuse-error').textContent =
        'The reference selection cannot also be a target.';
      return;
    }
    const label = submittedFeatureLabel('new-feature-reuse-label'),
      reserved = [label],
      reuse = {
        id: uid('feature_reuse'),
        label,
        operation: 'feature_reuse',
        fits: fitIds,
        lineage: discoverReuseLineage(graphState.recipe.nodes, fitIds),
        reference_selection: referenceSelection,
        target_selections: targetSelections,
        tangent_margin: Number($('new-feature-reuse-tangent-margin').value),
        normal_margin: Number($('new-feature-reuse-normal-margin').value),
        normal_angle_degrees: Number($('new-feature-reuse-normal-angle').value),
        equal_corresponding_dimensions:
          $('new-feature-reuse-equal-dimensions').checked,
      },
      generated = [reuse],
      copiedFits = new Map(fitIds.map((fitId) => [fitId, []]));
    for (const targetSelection of targetSelections) {
      const targetLabel = graphNode(targetSelection).label;
      for (const fitId of fitIds) {
        const sourceFit = graphNode(fitId),
          generatedSelections = [];
        for (const sourceSelection of sourceFit.selections) {
          const selection = graphNode(sourceSelection),
            target = {
              id: uid('reuse_selection'),
              label: reserveFeatureLabel(
                `${selection.label} at ${targetLabel}`,
                reserved,
              ),
              operation: 'reuse_selection',
              reuse: reuse.id,
              fit: fitId,
              source_selection: sourceSelection,
              target_selection: targetSelection,
              managed_by: reuse.id,
              managed_key: `selection/${targetSelection}/${fitId}/${sourceSelection}`,
            };
          generated.push(target);
          generatedSelections.push(target.id);
        }
        const copiedFit = {
          id: uid('fit'),
          label: reserveFeatureLabel(`${sourceFit.label} at ${targetLabel}`, reserved),
          operation: 'fit',
          selections: generatedSelections,
          kind: sourceFit.kind,
          axial_domain: [...sourceFit.axial_domain],
          managed_by: reuse.id,
          managed_key: `fit/${targetSelection}/${fitId}`,
        };
        generated.push(copiedFit);
        copiedFits.get(fitId).push(copiedFit.id);
      }
    }
    if (reuse.equal_corresponding_dimensions)
      for (const fitId of fitIds) {
        const sourceFit = graphNode(fitId);
        if (sourceFit.kind !== 'cylinder') continue;
        generated.push({
          id: uid('equal_radii'),
          label: reserveFeatureLabel(`${sourceFit.label} radii all equal`, reserved),
          operation: 'equal_radii',
          surfaces: [fitId, ...copiedFits.get(fitId)],
          managed_by: reuse.id,
          managed_key: `equal-radius/${fitId}`,
        });
      }
    const saved = await appendActions(generated);
    if (saved) $('feature-reuse-dialog').close();
    else $('feature-reuse-error').textContent = $('status').textContent;
  };
  $('add-selection-region-form').onsubmit = async (event) => {
    event.preventDefault();
    const fit = $('new-selection-region-fit').value,
      axial = $('new-selection-region-axial').value,
      clock = $('new-selection-region-clock').value;
    if (!fit || !axial || !clock) {
      $('selection-region-error').textContent =
        'Choose a compatible fit and both planes of the source datum frame.';
      return;
    }
    const saved = await appendActions([
      {
        id: uid('selection_region'),
        label: submittedFeatureLabel('new-selection-region-label'),
        operation: 'selection_region',
        selection: $('new-selection-region-selection').value,
        fit,
        axial_plane: axial,
        clock_plane: clock,
        tangent_margin: Number($('new-selection-region-tangent-margin').value),
        normal_margin: Number($('new-selection-region-normal-margin').value),
        normal_angle_degrees: Number($('new-selection-region-normal-angle').value),
      },
    ]);
    if (saved) $('selection-region-dialog').close();
    else $('selection-region-error').textContent = $('status').textContent;
  };
  $('add-region-selection-form').onsubmit = async (event) => {
    event.preventDefault();
    const region = $('new-region-selection-region').value,
      axial = $('new-region-selection-axial').value,
      clock = $('new-region-selection-clock').value;
    if (!region || !axial || !clock) {
      $('region-selection-error').textContent =
        'Choose a reusable region and both planes of the target datum frame.';
      return;
    }
    const source = graphState.recipe.nodes.find((node) => node.operation === 'source');
    const saved = await appendActions([
      {
        id: uid('region_selection'),
        label: submittedFeatureLabel('new-region-selection-label'),
        operation: 'region_selection',
        region,
        source: source.id,
        axial_plane: axial,
        clock_plane: clock,
      },
    ]);
    if (saved) $('region-selection-dialog').close();
    else $('region-selection-error').textContent = $('status').textContent;
  };
  $('add-axis-form').onsubmit = async (event) => {
    event.preventDefault();
    const fromFit = $('new-axis-mode').value === 'fit',
      sourceId = $('new-axis-source').value,
      source = graphNode(sourceId),
      axisLabel = submittedFeatureLabel('new-axis-label');
    if (fromFit && !source) {
      status('Create a standalone cone or cylinder fit first.', true);
      return;
    }
    const axis = {
      id: uid('axis'),
      label: axisLabel,
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
        label: nextFeatureLabel(source.label + ' axis factor', [axisLabel]),
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
    const construction = $('new-reference-plane-construction').value;
    const saved = await appendActions([
      {
        id: uid('reference_plane'),
        label: submittedFeatureLabel('new-reference-plane-label'),
        operation: 'reference_plane',
        axis: $('new-reference-plane-axis').value,
        construction,
        initial_angle_degrees:
          construction === 'perpendicular_to_axis'
            ? null
            : Number($('new-reference-plane-angle').value),
        offset:
          construction === 'contains_axis'
            ? 0
            : Number($('new-reference-plane-offset').value),
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
        label: submittedFeatureLabel('new-mirror-label'),
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
        label: submittedFeatureLabel('new-parallel-label'),
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
        label: submittedFeatureLabel('new-equal-label'),
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
        'Choose one free axis, a cone or cylinder on it, and either a plane fit on that axis or one of its planes.';
      return;
    }
    const saved = await appendActions([
      {
        id: uid('axis_solve'),
        label: submittedFeatureLabel('new-axis-solve-label'),
        operation: 'axis_solve',
        axis,
        factors,
      },
    ]);
    if (saved) $('axis-solve-dialog').close();
    else $('axis-solve-error').textContent = $('status').textContent;
  };
  $('new-joint').onclick = () =>
    showCreateDialog('joint-dialog', 'new-joint-label', 'Joint fit');
  const rotationChoices = () => {
    const joint = graphNode($('rotation-joint').value);
    const used = new Set(joint ? joint.constraints.flatMap((id) => refs(graphNode(id))) : []);
    const planes = graphState.recipe.nodes.filter(
      (n) => n.operation === 'fit' && isStandaloneFit(n) && !used.has(n.id),
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
    showCreateDialog(
      'rotation-dialog',
      'rotation-label',
      'Threefold surface symmetry',
    );
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
      label: submittedFeatureLabel('rotation-label'),
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
    const reserved = [],
      relations = selected.map((id) =>
        graphNode(id).kind === 'plane'
          ? {
              id: uid('perpendicular'),
              label: reserveFeatureLabel(
                graphNode(id).label + ' perpendicular',
                reserved,
              ),
              operation: 'perpendicular',
              lateral: side,
              plane: id,
            }
          : {
              id: uid('coaxial'),
              label: reserveFeatureLabel(graphNode(id).label + ' coaxial', reserved),
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
    const ownerId = managedOwnerId(node, recipe.nodes);
    if (ownerId) {
      status(`Edit ${graphNode(ownerId)?.label || 'the generating feature'} instead.`, true);
      return;
    }
    node.label = $('action-label').value;
    node.group_id = $('action-group').value || null;
    if (node.operation === 'fit') {
      node.kind = $('surface-kind').value;
      node.selections = chosen('fit-inputs');
      node.axial_domain = [Number($('axial-start').value), Number($('axial-end').value)];
      setFitReference(node, $('fit-reference').value);
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
      node.construction = $('reference-plane-construction').value;
      node.initial_angle_degrees =
        node.construction === 'perpendicular_to_axis'
          ? null
          : Number($('reference-plane-angle').value);
      node.offset =
        node.construction === 'contains_axis'
          ? 0
          : Number($('reference-plane-offset').value);
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
    if (node.operation === 'selection_region') {
      node.selection = $('selection-region-selection').value;
      node.fit = $('selection-region-fit').value;
      node.axial_plane = $('selection-region-axial').value;
      node.clock_plane = $('selection-region-clock').value;
      node.tangent_margin = Number($('selection-region-tangent-margin').value);
      node.normal_margin = Number($('selection-region-normal-margin').value);
      node.normal_angle_degrees = Number($('selection-region-normal-angle').value);
    }
    if (node.operation === 'region_selection') {
      node.region = $('region-selection-region').value;
      node.axial_plane = $('region-selection-axial').value;
      node.clock_plane = $('region-selection-clock').value;
    }
    if (node.operation === 'feature_reuse') {
      const reconciled = reconcileFeatureReuse(
        recipe.nodes,
        node.id,
        {
          label: node.label,
          fits: chosen('feature-reuse-fits'),
          reference_selection: $('feature-reuse-reference').value,
          target_selections: chosen('feature-reuse-target'),
          equal_corresponding_dimensions:
            $('feature-reuse-equal-dimensions').checked,
          tangent_margin: Number($('feature-reuse-tangent-margin').value),
          normal_margin: Number($('feature-reuse-normal-margin').value),
          normal_angle_degrees: Number($('feature-reuse-normal-angle').value),
        },
        uid,
      );
      if (reconciled.error) {
        status(reconciled.error, true);
        return;
      }
      recipe.nodes = reconciled.nodes;
      if (reconciled.removedIds.includes(recipe.output)) recipe.output = node.id;
      await replaceRecipe(recipe);
      return;
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
    const removalIds = managedSubtreeIds(selectedFeatureId, recipe.nodes);
    recipe.nodes = recipe.nodes.filter((node) => !removalIds.has(node.id));
    if (removalIds.has(recipe.output)) recipe.output = recipe.nodes.at(-1).id;
    selectedFeatureId = recipe.output;
    await replaceRecipe(recipe);
  };
  $('add-selection').onclick = async () => {
    const node = {
      id: uid('selection'),
      label: nextFeatureLabel('Selection'),
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
    const node = {
      id: uid('fit'),
      label: submittedFeatureLabel('new-fit-label'),
      operation: 'fit',
      selections: chosen('new-fit-inputs'),
      kind: $('new-fit-kind').value,
      axial_domain: [-2, 5],
    };
    setFitReference(node, $('new-fit-reference').value);
    const saved = await appendActions([node]);
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
    const label = submittedFeatureLabel('new-joint-label'),
      reserved = [label],
      relations = planes.map((plane) => ({
        id: uid('perpendicular'),
        label: reserveFeatureLabel(
          graphNode(plane).label + ' perpendicular',
          reserved,
        ),
        operation: 'perpendicular',
        lateral: side,
        plane,
      }));
    for (const ref of chosen('new-joint-extra'))
      if (ref !== side)
        relations.push({
          id: uid('coaxial'),
          label: reserveFeatureLabel(graphNode(ref).label + ' coaxial', reserved),
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
          label: nextFeatureLabel(node.label + ' growth'),
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
        label: nextFeatureLabel(seed.label + ' grown fit'),
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
  $('reuse-volumes').onchange = () => {
    showReuseVolumes();
    paint();
  };
  $('save').onclick = async () => {
    try {
      if (!graphState?.recipe) throw new Error('Actions have not finished loading yet.');
      downloadJson('nozzle-actions.json', graphState.recipe);
      status('Actions downloaded as nozzle-actions.json.');
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
  $('save').disabled = false;
  status('Feature graph loaded. Ready to evaluate.');
}
await start();
