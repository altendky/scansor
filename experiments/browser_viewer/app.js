import {
  actionDescription,
  featureDeletionPlan,
  featureIcon,
  featureTreePresentation,
  relationshipParticipantChoices,
  renderRelationshipParticipants,
  managedOwnerId,
  managedSubtreeIds,
  nodeReferences as refs,
  renderActionTree,
} from './action-tree.js';
import { uniqueFeatureLabel } from './feature-names.js';
import { inputChoices, requirementOutputs, inputReferenceKey, readInputReference,
  referenceFeature, matchingInputOutput, renderInputChoices } from './feature-inputs.js';
import { requestWorkspaceClose, revealWorkspacePanel, workspaceEditingPanels, workspaceToolbars } from './workspace.js';
import { unobscuredViewport } from './workspace-state.js';
import { ensureGraphCurrent, waitForGraphEvaluation } from './graph-evaluation.js';
import { renderFeatureGraph } from './feature-graph-view.js';
import { activeDisplayTransform } from './display-transform.js';
import { exportInputIssues, exportScopePlan } from './cad-export.js';
import { bodyEdgePaths, bodyFaceChoices, bodyFaceSelection, bodyInputs, bodyProblems, initialBodyTolerance } from './body-ui.js';
import { fitQualities, parseRmsLimit, residualRange, resultResidualSurfaces } from './residual-display.js';
import { selectionVolumePositions } from './reuse-volume.js';
import { faceEdgeLines } from './edge-highlight.js';
import { buildFootprintTopology, fittedSelectionFootprint } from './fit-footprint.js';
import { scanFitCandidates, isModelClick, bindModelPickControls, createPickField, nativePickOptions,
  commitNativePick, segmentDistance } from './model-picking.js';
import {
  surfaceReferenceChoices, eligibleIntersections, boundaryKeepOptions,
  geometryAppendOutput, sameSurfaceReference, validGeometryPreview,
  initialBuildFaceRegions, buildFaceChoices, buildFaceRegionGroups, boundedBuildFaceRegions,
  changeBuildFaceRegionSelection, compactFaceRegionList,
  faceEvidenceSummary,
  validBuildFaceRegion,
  addFittedSurfaceReferences,
  adjacencyPairKey, buildAdjacencyDecisions, faceScopeChoices, buildFaceScopes,
  intersectionCurves, intersectionPreviewPaths, faceBoundaryPreviewPaths, physicalBoundsSummary,
  initialGuidedFaceRegions, guidedCandidateSelected, guidedSurfaceInputs, guidedProposalFaces,
  approvedNeighborPreviewPaths,
  guidedSourceAvailable,
  guidedRejectedDecisions, unavailableGuidedSources,
  faceCandidatePresentation, fittedSurfacePresentation, completeFitSurfaceChoices,
  unavailableRetainedFitReferences, surfaceFootprintState, guidedNeighborHighlights,
  bindFaceContinuationPreview, appliedFaceContext, definedFaceContext,
  faceContinuationPreview, previewedFaceContextIds, renderFaceDialogClose, unbuiltFaceNeighbors,
  prepareFaceContinuation, faceDisplayEntries,
} from './surface-trims.js';
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
viewport.append($('model-pick-hint'));
let renderer, scene, camera, controls, modelRoot, mesh, selectedPoints, overlays, reuseVolumes;
let metadata,
  positions,
  session,
  result = null,
  busy = false;
let pending = false,
  frames = 0;
let graphState, selectedFeatureId, editingGroupId = null;
let updateActionTreeLocks = () => {};
let activeGraphEvaluation = null, faceContinuationPending = false;
let exampleCatalogue = null, exampleSwitchPending = false;
let constructedFaces, constructedFacesState = null;
let bodyInspection;
let fitQualityLimit = null, fitQualityStorageKey = null, fitQualityCache = null;
let selectedFeatureIds = new Set();
let featureDeletionReview = null, featureDeletionPending = false, featureContextAnchor = null;
let resizeViewport = null;
let displayTransformKey = 'identity';
let buildFacesProposal = null, buildFacesOwnerId = null, buildFacesRequest = 0;
let buildFacesReview = new Map(), buildFacesOverlays;
let relationshipOverlays, relationshipInspected = null, relationshipApplying = false;
let buildFacesRegionInspected = null;
let buildFacesAdjacencyDraft = [], buildFacesScopeDraft = [];
let buildFacesApplying = false;
let buildFacesMode = 'guided', buildFacesTarget = null, buildFacesCandidates = null;
let buildFacesBatchDraft = [], buildFacesChoicesKey = null, buildFacesScopesKey = null;
let buildFacesChoicesMessage = '';
let buildFacesCutters = [], buildFacesReviewDirty = false, buildFacesInspected = null;
let buildFacesBoundarySources = [];
let buildFacesSurfacePreview = null, buildFacesSurfaceHovered = false;
let buildFacesContinuePreview = null, buildFacesAcceptedContext = [], buildFacesContext;
let buildFacesContinueInteraction = { pointer: null, focus: null };
let buildFacesContextCache = null;
const buildFacesFootprints = new WeakMap();
let buildFacesMeshTopology = null;
let modelPickMode = null, modelPickGesture = null, modelPickClick = null, modelPickHoverKey = null;
let modelPickField = null, modelPickGuides, modelPickGeometryKey = null;
let modelPickSourceFaces, modelPickSourceState = null;
let modelPickFitState = null, modelPickFitReferences = new Map();
let faceTargetField, faceNeighborField, relationshipParticipantField;
const modelPickButtons = {
  target: 'build-faces-target', neighbor: 'pick-face-neighbor', participant: 'pick-relationship-participant',
};
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
function showImportWarnings(warnings) {
  $('import-warning-list').replaceChildren(...warnings.map(warning => {
    const item = document.createElement('li');
    item.textContent = `${warning.kind === 'group' ? 'Group: ' : ''}“${warning.old_name}” → “${warning.new_name}”`;
    return item;
  }));
  $('import-warning-summary').textContent = `${warnings.length} renamed ${warnings.length === 1 ? 'name' : 'names'}`;
  $('import-warning').hidden = warnings.length === 0;
}
function fittedPickResult(reference) {
  if (!reference || graphState.states[reference.feature] !== 'ready') return null;
  const output = graphState.results[reference.feature];
  return reference.surface ? output?.surfaces?.[reference.surface] : output;
}
function modelPickChoices() {
  if (!modelPickMode || !graphState || featureTreeLocked() ||
      buildFacesApplying || relationshipApplying) return [];
  const nodes = graphState.recipe.nodes;
  if (modelPickMode === 'field') return nativePickOptions(modelPickField?.control).map(option => {
    if (modelPickField.control.dataset.inputRequirement) {
      const output = matchingInputOutput(option.key,
        requirementOutputs(graphState, modelPickField.control.dataset.inputRequirement));
      return { ...option, reference: output?.reference, inputOutput: output,
        icon: output?.capability || 'point' };
    }
    let reference, exactReference = false;
    try { reference = JSON.parse(option.key); exactReference = !!reference?.feature; }
    catch { reference = { feature: option.key }; }
    if (!reference?.feature) reference = { feature: option.key };
    const node = graphNode(reference.surface || reference.feature);
    return { ...option, reference, exactReference, icon: node?.operation === 'fit' ? node.kind : node?.operation || 'selection' };
  });
  if (modelPickMode === 'target') return buildFacesGeometry().choices.map(choice => ({
    ...fittedSurfacePresentation(choice.reference, nodes),
    key: JSON.stringify(choice.reference), reference: choice.reference,
    ids: fittedPickResult(choice.reference)?.ids,
  }));
  if (modelPickMode === 'neighbor') return (buildFacesCandidates?.candidates || []).flatMap(candidate => {
    const key = JSON.stringify(candidate.reference),
      row = [...$('build-faces-candidates').children].find(row => row.dataset.reference === key),
      checkbox = row?.querySelector('summary input');
    return row && candidate.supported && candidate.mathematical?.status !== 'proven_empty' &&
      checkbox && !checkbox.disabled ? [{
      ...fittedFaceCandidatePresentation(candidate), key, candidate, checkbox,
      ids: fittedPickResult(candidate.reference)?.ids,
    }] : [];
  });
  const choices = completeFitSurfaceChoices(nodes, graphState.results, [], graphState.states).choices;
  return [...$('relationship-participants').children].flatMap(row => {
    const checkbox = row.querySelector('input'), id = checkbox?.dataset.participantId,
      node = graphNode(id), reference = choices.find(choice =>
        (choice.reference.surface || choice.reference.feature) === id)?.reference;
    const fitted = reference ? fittedPickResult(reference) :
      (node?.kind === 'sphere' || node?.operation === 'reference_plane') &&
        graphState.states[id] === 'ready' ? graphState.results[id] : null;
    return row.dataset.compatible !== 'false' && checkbox && !checkbox.disabled && fitted ? [{
      ...fittedSurfacePresentation({ feature: id }, nodes), key: id,
      reference: reference || { feature: id }, checkbox, ids: fitted.ids,
    }] : [];
  });
}
function clearModelPickHover() {
  if (modelPickHoverKey === null) return;
  modelPickHoverKey = null;
  buildFacesSurfacePreview = null;
  inspectFaceCandidate(null);
  relationshipInspected = null;
  paintRelationshipPreview();
  paintModelPickGuides();
  for (const row of document.querySelectorAll('.model-pick-hover')) row.classList.remove('model-pick-hover');
}
function inspectModelPick(choice) {
  const key = choice?.key || null;
  if (key === modelPickHoverKey) return;
  clearModelPickHover();
  modelPickHoverKey = key;
  if (!choice) return;
  if (modelPickMode === 'target') {
    buildFacesSurfacePreview = choice.reference;
    paintBuildFacesPreview();
  } else if (modelPickMode === 'neighbor') inspectFaceCandidate(choice.candidate);
  else if (modelPickMode === 'participant') { relationshipInspected = choice.key; paintRelationshipPreview(); }
  paintModelPickGuides();
  choice.checkbox?.closest('.face-candidate, .relationship-participant')?.classList.add('model-pick-hover');
}
function setModelPickMode(mode) {
  const previousField = modelPickField;
  clearModelPickHover();
  $('model-pick-choices').hidePopover();
  modelPickGesture = null;
  modelPickClick = null;
  modelPickMode = mode;
  if (mode !== 'field') modelPickField = null;
  modelPickGeometryKey = null;
  paintModelPickGuides();
  if (previousField && mode !== 'field') {
    previousField.button.setAttribute('aria-pressed', 'false');
    for (const [element, inert] of previousField.locks || []) element.inert = inert;
    if (previousField.suspended) {
      delete previousField.dialog.dataset.modelPickSuspended;
      if (previousField.dialog.isConnected) {
        if (previousField.dialog.open) previousField.dialog.close();
        previousField.dialog.showModal();
      }
    }
    previousField.button.focus();
  }
  syncModelPickControls();
  updateActionTreeLocks();
  updateFaceReviewDisplay();
  draw();
}
function startFieldPick(control, button) {
  if (featureTreeLocked() || !nativePickOptions(control).length) return;
  if (modelPickField?.control === control) return;
  setModelPickMode(null);
  const dialog = control.closest('dialog'), suspended = dialog?.matches(':modal') || false;
  modelPickField = { control, button, dialog, suspended,
    ownerId: control.closest('#feature-properties-panel') ? selectedFeatureId : undefined };
  if (suspended) {
    dialog.dataset.modelPickSuspended = 'true';
    dialog.close();
    dialog.show();
    // Keep the draft visible while exposing the viewport. Another create/edit
    // action must not reinitialize or replace the draft during picking.
    modelPickField.locks = [...document.querySelectorAll('.prototype-toolbar, #feature-properties-panel')]
      .map(element => [element, element.inert]);
    for (const [element] of modelPickField.locks) element.inert = true;
  }
  button.setAttribute('aria-pressed', 'true');
  setModelPickMode('field');
  revealWorkspacePanel('view');
}
function syncModelPickControls() {
  const locked = !graphState || featureTreeLocked() || buildFacesApplying || relationshipApplying,
    facesOpen = $('build-faces-dialog').open && buildFacesMode === 'guided',
    relationshipOpen = $('relationship-dialog').open;
  const fieldValid = modelPickField && nativePickOptions(modelPickField.control).length &&
    (modelPickField.ownerId === undefined || modelPickField.ownerId === selectedFeatureId) &&
    (!modelPickField.dialog || modelPickField.dialog.open || modelPickField.suspended);
  if (modelPickMode && (locked ||
      (modelPickMode === 'neighbor' && !buildFacesCandidates) ||
      (modelPickMode === 'field' ? !fieldValid : modelPickMode === 'participant' ? !relationshipOpen : !facesOpen))) {
    setModelPickMode(null);
    return;
  }
  for (const [mode, id] of Object.entries(modelPickButtons)) {
    $(id).setAttribute('aria-pressed', String(modelPickMode === mode));
    $(id).disabled = locked || (mode === 'participant' ?
      !relationshipOpen : !facesOpen || (mode === 'neighbor' && !buildFacesCandidates) ||
        (mode === 'target' && !buildFacesGeometry().choices.length));
  }
  if (faceTargetField) faceTargetField.dropdown.disabled = $('build-faces-target').disabled;
  updateFaceNeighborSummary();
  updateRelationshipParticipantField();
  const canvas = renderer?.domElement;
  if (canvas) {
    canvas.dataset.modelPick = modelPickMode || '';
    canvas.classList.toggle('model-picking', !!modelPickMode);
  }
  $('model-pick-hint').hidden = !modelPickMode;
  $('stop-model-picking').textContent = modelPickField ? 'Done picking' : 'Stop picking';
  $('model-pick-message').textContent = modelPickMode === 'field' ?
    `Pick ${modelPickField.control.getAttribute('aria-label') || modelPickField.control.labels?.[0]?.childNodes[0]?.textContent?.trim() || 'items'} in the model.` :
    modelPickMode === 'target' ? 'Pick a fitted scan patch for Surface.' :
    modelPickMode === 'neighbor' ? 'Click fitted scan patches to toggle neighbors.' :
      'Click fitted scan patches to toggle participants.';
}
function initializeSpecializedPickFields() {
  const begin = mode => {
    setModelPickMode(mode);
    if (modelPickMode) revealWorkspacePanel('view');
  };
  faceTargetField = createPickField(document, {
    label: 'Surface', onPick: () => begin('target'),
    onChange: key => selectBuildFacesTarget(JSON.parse(key)),
    onChoose: () => { if (modelPickMode === 'target') setModelPickMode(null); },
    onInspect: key => {
      buildFacesSurfacePreview = key ? JSON.parse(key) : null;
      paintBuildFacesPreview();
    },
  });
  faceTargetField.button.id = 'build-faces-target';
  faceTargetField.popup.id = 'build-faces-target-options';
  faceTargetField.dropdown.setAttribute('aria-controls', faceTargetField.popup.id);
  $('build-faces-target-field').append(faceTargetField.element);
  faceNeighborField = createPickField(document, {
    label: 'Neighbors', multiple: true, onPick: () => begin('neighbor'),
    popupContent: $('build-faces-neighbor-options'),
    onChange: key => {
      const row = [...$('build-faces-candidates').children].find(row => row.dataset.reference === key);
      row?.querySelector('summary input')?.click();
    },
    onRemove: key => {
      const input = [...$('build-faces-candidates').children]
        .find(row => row.dataset.reference === key)?.querySelector('summary input');
      if (input) {
        input.checked = false;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else if (canDiscardFaceReview()) {
        const reference = JSON.parse(key);
        buildFacesCutters = buildFacesCutters.filter(item => !sameSurfaceReference(item, reference));
        buildFacesBoundarySources = buildFacesBoundarySources.filter(id =>
          !sameSurfaceReference(graphNode(id)?.surface, reference));
        invalidateBuildFaces('Boundaries changed. Preview again.', true);
        updateFaceNeighborSummary();
        renderBuildFacesScopes();
        renderUnavailableFaceGuidance();
      }
    },
  });
  faceNeighborField.button.id = 'pick-face-neighbor';
  $('build-faces-neighbor-field').append(faceNeighborField.element);
  relationshipParticipantField = createPickField(document, {
    label: 'Participants', multiple: true, onPick: () => begin('participant'),
    popupContent: $('relationship-participant-options'),
    onChange: key => {
      const checkbox = [...$('relationship-participants').querySelectorAll('input')]
        .find(input => input.dataset.participantId === key);
      checkbox?.click();
    },
  });
  relationshipParticipantField.button.id = 'pick-relationship-participant';
  $('relationship-participant-field').append(relationshipParticipantField.element);
}
function modelPickParts(choice) {
  // Named geometry outputs never inherit supporting observations or unrelated
  // members of a provider's numerical result.
  if (choice.inputOutput) return [];
  const reference = choice.reference || choice.candidate?.reference;
  const node = graphNode(reference?.surface || reference?.feature);
  if (!node) return [];
  let fitted = fittedPickResult(reference);
  let fitReference = reference;
  if (node.operation === 'fit' && !reference.surface && !choice.exactReference) {
    if (modelPickFitState !== graphState) {
      modelPickFitState = graphState;
      modelPickFitReferences = new Map(completeFitSurfaceChoices(graphState.recipe.nodes, graphState.results,
        [], graphState.states).choices.map(item => [item.reference.surface || item.reference.feature, item.reference]));
    }
    const resolved = modelPickFitReferences.get(node.id);
    if (resolved) { fitted = fittedPickResult(resolved); fitReference = resolved; }
  }
  if (!fitted && selectionOperations.includes(node.operation) && graphState.states[node.id] === 'ready')
    fitted = { ids: node.ids };
  if (!fitted) return [];
  if (node.operation === 'selection_region') fitted = { ...fitted,
    ids: fittedPickResult({ feature: node.selection })?.ids || graphNode(node.selection)?.ids };
  if (fitted.surfaces && !reference.surface) return Object.entries(fitted.surfaces).flatMap(([id, value]) => {
    const member = graphNode(id);
    return member ? [{ node: member, fitted: value, reference: { feature: reference.feature, surface: id } }] : [];
  });
  return [{ node, fitted, reference: fitReference }];
}
function paintModelPickGuides() {
  if (!modelPickGuides) return;
  paintModelPickSourceFaces();
  const choices = modelPickMode ? modelPickChoices() : [];
  const key = JSON.stringify([modelPickMode, modelPickHoverKey, choices.map(choice => [choice.key, choice.selected])]);
  if (key === modelPickGeometryKey) return;
  modelPickGeometryKey = key;
  for (const child of [...modelPickGuides.children]) {
    modelPickGuides.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  for (const choice of choices) {
    const color = choice.key === modelPickHoverKey ? '#ffe45c' : choice.selected ? '#6deda6' : '#78e2ff';
    const before = modelPickGuides.children.length;
    if (choice.inputOutput?.preview) {
      const output = choice.inputOutput, values = output.preview;
      if (output.capability === 'point') pointGuide(values, color, modelPickGuides);
      else if (output.capability === 'plane') {
        const normal = new THREE.Vector3(...values.normal_display).normalize(),
          u = new THREE.Vector3(Math.abs(normal.z) < 0.9 ? 0 : 1, 0, Math.abs(normal.z) < 0.9 ? 1 : 0)
            .cross(normal).normalize(), v = normal.clone().cross(u).normalize();
        referencePlaneGuide({ ...values, basis_u_display: u.toArray(), basis_v_display: v.toArray(),
          construction: 'perpendicular_to_axis' }, color, modelPickGuides);
      } else axisGuide(values, color, modelPickGuides);
      for (const child of modelPickGuides.children.slice(before)) {
        child.userData.pickGuide = true;
        child.userData.pickKey = choice.key;
      }
      continue;
    }
    for (const { node, fitted, reference } of modelPickParts(choice)) {
      if (node.operation === 'axis') axisGuide(fitted, color, modelPickGuides);
      else if (node.operation === 'point') pointGuide(fitted, color, modelPickGuides);
      else if (['frame', 'transform'].includes(node.operation)) transformGuide(fitted, modelPickGuides);
      else if (node.operation === 'reference_plane') {
        referencePlaneGuide(fitted, color, modelPickGuides);
        const outline = modelPickGuides.children.at(-1).geometry.getAttribute('position');
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(Array.from(outline.array).slice(0, 12), 3));
        geometry.setIndex([0, 1, 2, 0, 2, 3]);
        modelPickGuides.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
          color, side: THREE.DoubleSide, transparent: true, opacity: choice.key === modelPickHoverKey ? 0.16 : 0.06,
          depthTest: false, depthWrite: false })));
      } else if (['trimmed_face', 'arranged_face', 'surface_intersection', 'body'].includes(node.operation)) {
        physicalGeometryGuide(node, fitted, color, modelPickGuides);
      } else if (modelPickMode === 'field' && (choice.key === modelPickHoverKey || choice.selected) && node.operation === 'fit') {
        if (node.kind === 'sphere') surfaceGuide(node.kind, fitted.parameters, node.axial_domain,
          color, fitted.ids, modelPickGuides);
        else paintFaceFootprint(reference, true, color, modelPickGuides);
      } else if (modelPickMode === 'field' && (choice.key === modelPickHoverKey || choice.selected) && fitted.ids?.length) {
        const coordinates = fitted.ids.flatMap(id => Array.from(positions.subarray(id * 3, id * 3 + 3)));
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(coordinates, 3));
        modelPickGuides.add(new THREE.Points(geometry, new THREE.PointsMaterial({
          color, size: 5, sizeAttenuation: false, depthTest: false })));
      }
      if (['axis', 'point', 'frame', 'transform', 'reference_plane', 'trimmed_face', 'arranged_face', 'surface_intersection', 'body'].includes(node.operation))
        for (const child of modelPickGuides.children.slice(before)) child.userData.pickGuide = true;
    }
    for (const child of modelPickGuides.children.slice(before)) child.userData.pickKey = choice.key;
  }
  draw();
}
function paintModelPickSourceFaces() {
  // A sewn body has one merged display mesh. Use identified source previews
  // as pick proxies, without changing body/export topology. Hover changes must
  // not rebuild these potentially large tessellations.
  const state = modelPickMode && $('faces-only').checked ? graphState : null;
  if (state === modelPickSourceState) return;
  modelPickSourceState = state;
  for (const child of [...modelPickSourceFaces.children]) {
    modelPickSourceFaces.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  if (state) for (const { node, result } of faceDisplayEntries(state)) {
    if (graphState.states[node.id] !== 'ready' || !validGeometryPreview(result.preview)) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(result.preview.positions, 3));
    geometry.setIndex(result.preview.indices);
    const proxy = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      side: THREE.DoubleSide, transparent: true, opacity: 0, depthWrite: false }));
    proxy.userData.sourceFace = node.id;
    modelPickSourceFaces.add(proxy);
  }
}
function modelPicksAt(event) {
  if (!modelPickMode) return [];
  paintModelPickGuides();
  const choices = modelPickChoices();
  const bounds = renderer.domElement.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return [];
  camera.updateMatrixWorld();
  modelRoot.updateMatrixWorld(true);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(
    (event.clientX - bounds.left) / bounds.width * 2 - 1,
    1 - (event.clientY - bounds.top) / bounds.height * 2), camera);
  const matches = new Set();
  const hits = raycaster.intersectObjects([
    ...modelPickGuides.children.filter(child => child.isMesh), ...modelPickSourceFaces.children,
  ], false);
  const nearest = hits.find(hit => hit.object.material.depthTest !== false)?.distance;
  for (const hit of hits) if (hit.object.userData.pickKey &&
      (hit.object.material.depthTest === false || hit.distance <= nearest + 1e-5)) matches.add(hit.object.userData.pickKey);
  if ($('faces-only').checked) {
    const face = hits.find(hit => hit.object.userData.sourceFace);
    const node = graphNode(face?.object.userData.sourceFace);
    if (node) for (const choice of choices) {
      if (choice.inputOutput) continue;
      const reference = choice.reference || choice.candidate?.reference;
      if (choice.key === node.id || sameSurfaceReference(reference, node.surface) ||
          modelPickParts(choice).some(part => sameSurfaceReference(part.reference, node.surface)) ||
          graphNode(reference?.feature)?.faces?.includes(node.id)) matches.add(choice.key);
    }
  } else if (mesh.visible) {
    const hit = raycaster.intersectObject(mesh, false)[0];
    const supported = choices.flatMap(choice => {
      if (choice.ids) return [choice];
      return modelPickParts(choice).map(part => ({ ...choice, ids: part.fitted.ids }));
    });
    for (const choice of scanFitCandidates(hit?.face ? [hit.face.a, hit.face.b, hit.face.c] : null, supported)) matches.add(choice.key);
  }
  const point = [event.clientX, event.clientY];
  for (const object of modelPickGuides.children.filter(child => child.userData.pickGuide && (child.isLine || child.isPoints))) {
    const attribute = object.geometry.getAttribute('position');
    let previous = null;
    for (let i = 0; i < attribute.count; i++) {
      const vertex = new THREE.Vector3().fromBufferAttribute(attribute, i).applyMatrix4(object.matrixWorld).project(camera);
      if (vertex.z < -1 || vertex.z > 1) { previous = null; continue; }
      const current = [bounds.left + (vertex.x + 1) * bounds.width / 2, bounds.top + (1 - vertex.y) * bounds.height / 2];
      if ((object.isPoints ? Math.hypot(point[0] - current[0], point[1] - current[1]) :
          previous ? segmentDistance(point, previous, current) : Infinity) <= 7) {
        matches.add(object.userData.pickKey); break;
      }
      previous = current;
    }
    if (object.isLineLoop && attribute.count > 1) {
      const ends = [attribute.count - 1, 0].map(i => {
        const vertex = new THREE.Vector3().fromBufferAttribute(attribute, i).applyMatrix4(object.matrixWorld).project(camera);
        return vertex.z >= -1 && vertex.z <= 1 ?
          [bounds.left + (vertex.x + 1) * bounds.width / 2, bounds.top + (1 - vertex.y) * bounds.height / 2] : null;
      });
      if (ends.every(Boolean) && segmentDistance(point, ...ends) <= 7) matches.add(object.userData.pickKey);
    }
  }
  return choices.filter(choice => matches.has(choice.key));
}
function selectBuildFacesTarget(target) {
  if (featureTreeLocked() || buildFacesApplying || !canDiscardFaceReview()) return false;
  const owner = graphState.recipe.nodes.find(node => node.operation === 'build_faces' &&
    node.target && sameSurfaceReference(node.target, target));
  buildFacesReviewDirty = false;
  openBuildFaces(owner || null, target);
  return true;
}
function commitModelPick(key) {
  // Recheck current lists and locks; a chooser may outlive a candidate refresh.
  const choice = modelPickChoices().find(choice => choice.key === key);
  if (!choice) { $('model-pick-choices').hidePopover(); return; }
  if (modelPickMode === 'field') {
    const control = modelPickField.control, multiple = control.multiple || !control.matches('select');
    if (commitNativePick(control, key)) {
      if (multiple) { modelPickGeometryKey = null; paintModelPickGuides(); }
      else setModelPickMode(null);
    }
  } else if (modelPickMode === 'target') {
    if (selectBuildFacesTarget(choice.reference)) setModelPickMode(null);
  } else {
    choice.checkbox.click();
    inspectModelPick(choice);
    choice.checkbox.closest('.face-candidate, .relationship-participant').scrollIntoView({ block: 'nearest' });
  }
  $('model-pick-choices').hidePopover();
}
function pickModelItem(event) {
  showModelPickChoices(modelPicksAt(event), event);
}
function showModelPickChoices(matches, event) {
  const chooser = $('model-pick-choices');
  chooser.hidePopover();
  if (matches.length === 1) { commitModelPick(matches[0].key); return; }
  if (!matches.length) {
    $('model-pick-message').textContent = 'No eligible item here. Click a scan patch or visible guide.';
    return;
  }
  const title = document.createElement('span');
  title.textContent = 'Choose item';
  chooser.replaceChildren(title, ...matches.map(choice => {
    const button = document.createElement('button'), label = document.createElement('span');
    button.type = 'button';
    label.textContent = choice.label;
    button.append(featureIcon(choice.icon), label);
    button.onpointerenter = button.onfocus = () => inspectModelPick(choice);
    button.onclick = () => commitModelPick(choice.key);
    return button;
  }));
  chooser.showPopover();
  const bounds = chooser.getBoundingClientRect();
  chooser.style.left = `${Math.max(4, Math.min(event.clientX, innerWidth - bounds.width - 4))}px`;
  chooser.style.top = `${Math.max(4, Math.min(event.clientY, innerHeight - bounds.height - 4))}px`;
  chooser.querySelector('button').focus();
}
function pickFeatureInput(id) {
  if (!modelPickMode) return false;
  const matches = modelPickChoices().filter(choice =>
    choice.reference?.surface === id || referenceFeature(choice.reference) === id || choice.key === id);
  const row = [...$('action-list').querySelectorAll('.action-select')]
    .find(button => button.dataset.actionId === id), bounds = row?.getBoundingClientRect();
  showModelPickChoices(matches, { clientX: bounds?.right || innerWidth / 2,
    clientY: bounds?.top || innerHeight / 2 });
  return true;
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
function clearBuildFacesPreview() {
  if (buildFacesOverlays) {
    for (const child of [...buildFacesOverlays.children]) {
      buildFacesOverlays.remove(child);
      child.geometry.dispose();
      child.material.dispose();
    }
  }
  updateFaceReviewDisplay();
}
function updateFaceReviewDisplay() {
  if (!mesh || !overlays) return;
  const reviewing = $('build-faces-dialog').open && buildFacesMode === 'guided';
  const guiding = reviewing || $('relationship-dialog').open || !!modelPickMode;
  const facesOnly = $('faces-only').checked;
  if (guiding || facesOnly) $('brush-cursor').hidden = true;
  if (buildFacesContext) buildFacesContext.visible = reviewing && !facesOnly;
  if (reviewing) paintBuildFacesContext();
  mesh.material.opacity = guiding ? 0.4 : 1;
  mesh.material.transparent = guiding;
  for (const points of [selectedPoints, focusedPoints]) {
    if (!points) continue;
    points.material.opacity = guiding ? 0.18 : 1;
    points.material.transparent = guiding;
  }
  mesh.visible = !facesOnly;
  overlays.visible = $('guides').checked && !guiding && !facesOnly;
  constructedFaces.visible = facesOnly;
  reuseVolumes.visible = $('reuse-volumes').checked && !facesOnly && !guiding;
  if (facesOnly) {
    selectedPoints.visible = focusedPoints.visible = false;
    overlapMarkers.visible = overlapHalo.visible = false;
  }
}
function invalidateBuildFaces(message = '', keepAdjacencyReview = false) {
  renderFaceDialogClose($('close-build-faces'), false);
  buildFacesRequest++;
  buildFacesProposal = null;
  buildFacesReview = new Map();
  buildFacesRegionInspected = null;
  buildFacesReviewDirty = false;
  clearBuildFacesPreview();
  $('apply-build-faces').disabled = true;
  $('select-suggested-face-regions').disabled = true;
  $('build-faces-region-tools').hidden = true;
  $('build-faces-review').replaceChildren();
  if (!keepAdjacencyReview) $('build-faces-adjacencies').replaceChildren();
  $('build-faces-diagnostics').replaceChildren();
  $('build-faces-error').textContent = message;
  $('build-faces-neighbors').open = true;
  draw();
}
function buildFacesSelectedSurfaces() {
  if (buildFacesMode === 'guided') return guidedSurfaceInputs(buildFacesTarget, [
    ...buildFacesCutters, ...guidedRejectedDecisions(buildFacesAdjacencyDraft, buildFacesTarget,
      buildFacesCutters).flatMap((choice) => [choice.first, choice.second]),
  ]);
  return buildFacesBatchDraft;
}
function buildFacesGeometry() {
  const owner = graphNode(buildFacesOwnerId);
  return completeFitSurfaceChoices(graphState.recipe.nodes, graphState.results,
    [...(owner?.surfaces || []), ...buildFacesBatchDraft, ...buildFacesCutters,
      ...(buildFacesTarget ? [buildFacesTarget] : [])], graphState.states);
}
function unavailableFitsMessage(unavailable) {
  return unavailable.length ? `Unavailable fits: ${unavailable.map((fit) =>
    `${fit.label} (${fit.reason.toLowerCase()})`).join(', ')}.` : '';
}
function unavailableSavedFaceInputsMessage(choices = buildFacesGeometry().choices) {
  const references = [...(graphNode(buildFacesOwnerId)?.surfaces || []),
    ...buildFacesSelectedSurfaces()],
    missing = unavailableRetainedFitReferences(references, choices)
      .filter((reference, index, all) => all.findIndex((other) => sameSurfaceReference(reference, other)) === index);
  return missing.length ? `${buildFacesOwnerId ? 'Saved' : 'Selected'} inputs unavailable: ${missing.map((reference) =>
    fittedSurfacePresentation(reference, graphState.recipe.nodes).name).join(', ')}. Restore or update these inputs before previewing.` : '';
}
function fittedFaceCandidatePresentation(candidate) {
  return { ...faceCandidatePresentation(candidate, graphState.recipe.nodes),
    ...fittedSurfacePresentation(candidate.reference, graphState.recipe.nodes) };
}

function canDiscardFaceReview() {
  return !buildFacesReviewDirty || confirm('Discard unapplied retained-cell choices and change the review inputs?');
}
function setBuildFacesModeDisplay() {
  syncModelPickControls();
  $('build-faces-mode').value = buildFacesMode;
  $('build-faces-guided').hidden = buildFacesMode !== 'guided';
  $('build-faces-batch').hidden = buildFacesMode === 'guided';
  $('build-faces-surfaces').required = buildFacesMode === 'batch';
  $('build-faces-target-options').hidePopover();
  const result = graphState.results[buildFacesTarget?.feature];
  const target = buildFacesTarget?.surface ? result?.surfaces?.[buildFacesTarget.surface] : result;
  $('focus-face-target').disabled = !target?.ids?.length || !buildFacesGeometry().choices
    .some((choice) => sameSurfaceReference(choice.reference, buildFacesTarget));
  $('select-suggested-face-regions').hidden = false;
  $('build-faces-adjacency-details').hidden = buildFacesMode === 'guided';
}
function inspectFaceCandidate(candidate) {
  buildFacesInspected = candidate;
  paintBuildFacesPreview();
}
function filterFaceCandidates() {
  const filter = $('build-faces-neighbor-filter').value.trim().toLowerCase();
  for (const row of $('build-faces-candidates').children) {
    const candidate = (buildFacesCandidates?.candidates || []).find(candidate =>
      JSON.stringify(candidate.reference) === row.dataset.reference);
    row.hidden = !candidate?.supported || candidate.mathematical?.status === 'proven_empty' ||
      !row.dataset.search.includes(filter);
  }
}
function updateFaceNeighborSummary() {
  $('build-faces-neighbor-summary').textContent = `Neighbors · ${buildFacesCutters.length} selected`;
  if (!faceNeighborField) return;
  const choices = (buildFacesCandidates?.candidates || []).map(candidate => ({
    key: JSON.stringify(candidate.reference), label: fittedFaceCandidatePresentation(candidate).label,
    selected: buildFacesCutters.some(reference => sameSurfaceReference(reference, candidate.reference)),
    disabled: !candidate.supported || candidate.mathematical?.status === 'proven_empty',
  }));
  for (const reference of buildFacesCutters) {
    const key = JSON.stringify(reference);
    if (!choices.some(choice => choice.key === key)) choices.push({ key,
      label: `Unavailable: ${fittedSurfacePresentation(reference, graphState.recipe.nodes).label}`,
      selected: true, disabled: true });
  }
  faceNeighborField.update(choices, { disabled: !graphState || featureTreeLocked() || buildFacesApplying });
}
function renderFaceCandidates(previous = null, exact = false) {
  const candidates = buildFacesCandidates?.candidates || [];
  buildFacesCutters = candidates.filter((candidate) => guidedCandidateSelected(candidate, previous, exact))
    .map((candidate) => candidate.reference);
  const rows = candidates.map((candidate) => {
    const row = document.createElement('details');
    row.className = 'face-candidate';
    row.dataset.reference = JSON.stringify(candidate.reference);
    const heading = document.createElement('summary');
    const presentation = fittedFaceCandidatePresentation(candidate);
    row.dataset.search = presentation.label.toLowerCase();
    const label = document.createElement('label');
    label.className = 'check';
    const include = document.createElement('input');
    include.type = 'checkbox';
    include.checked = buildFacesCutters.some((ref) => sameSurfaceReference(ref, candidate.reference));
    include.disabled = !candidate.supported || candidate.mathematical?.status === 'proven_empty';
    include.setAttribute('aria-label', presentation.label);
    label.title = presentation.label;
    const text = document.createElement('span');
    text.className = 'face-candidate-text';
    const name = document.createElement('span');
    name.className = 'face-candidate-name';
    name.textContent = presentation.name;
    const context = document.createElement('span');
    context.className = 'face-candidate-kind';
    context.textContent = presentation.detail;
    context.title = presentation.detail;
    text.append(name, context);
    label.append(include, featureIcon(presentation.icon, 'action-type'), text);
    const badge = document.createElement('span');
    badge.className = `face-badge${candidate.conflict || !candidate.supported ? ' warning' : ''}`;
    badge.textContent = presentation.status;
    row.onpointerenter = () => inspectFaceCandidate(candidate);
    row.onpointerleave = () => {
      if (!row.contains(document.activeElement) && buildFacesInspected === candidate) inspectFaceCandidate(null);
    };
    row.addEventListener('focusin', () => inspectFaceCandidate(candidate));
    row.addEventListener('focusout', (event) => {
      if (!row.contains(event.relatedTarget) && !row.matches(':hover') &&
          buildFacesInspected === candidate) inspectFaceCandidate(null);
    });
    include.onchange = () => {
      if (!canDiscardFaceReview()) { include.checked = !include.checked; return; }
      buildFacesCutters = buildFacesCutters.filter((ref) => !sameSurfaceReference(ref, candidate.reference));
      if (include.checked && !include.disabled) buildFacesCutters.push(candidate.reference);
      const allSources = (candidate.shared_faces || []).map((face) => face.id);
      buildFacesBoundarySources = buildFacesBoundarySources.filter((id) => !allSources.includes(id));
      if (include.checked) {
        buildFacesBoundarySources.push(...(candidate.shared_faces || []).filter(guidedSourceAvailable).map((face) => face.id));
        buildFacesAdjacencyDraft = buildFacesAdjacencyDraft.filter((choice) =>
          adjacencyPairKey(choice.first, choice.second) !== adjacencyPairKey(buildFacesTarget, candidate.reference));
      }
      for (const honor of row.querySelectorAll('[data-approved-edge]')) {
        honor.disabled = !include.checked || include.disabled || honor.dataset.unavailable === 'true';
        honor.checked = include.checked && !honor.disabled;
      }
      invalidateBuildFaces('Boundaries changed. Preview again.', true);
      updateFaceNeighborSummary();
      renderBuildFacesScopes();
      renderUnavailableFaceGuidance();
      inspectFaceCandidate(candidate);
    };
    heading.append(label, badge);
    if (candidate.shared_faces?.length) {
      const edges = document.createElement('span');
      edges.className = 'face-badge';
      edges.textContent = `Edges ${candidate.shared_faces.length}`;
      heading.append(edges);
    }
    const detail = document.createElement('div');
    detail.className = 'face-candidate-detail';
    const contextDetail = document.createElement('p');
    contextDetail.className = 'hint';
    contextDetail.textContent = presentation.context || presentation.type;
    const reason = document.createElement('p');
    reason.className = 'hint';
    reason.textContent = candidate.reason || candidate.mathematical?.reason || '';
    if (candidate.conflict) {
      const warning = document.createElement('p');
      warning.className = 'build-face-warning';
      warning.textContent = 'Conflicting adjacency decisions. Review before including.';
      detail.append(warning);
    }
    detail.append(contextDetail, reason);
    const proximity = document.createElement('p');
    proximity.className = 'hint';
    const gap = candidate.evidence?.observation_bounds_gap;
    proximity.textContent = Number.isFinite(gap)
      ? `Scan gap: ${gap.toPrecision(5)}` : 'Scan gap unavailable';
    detail.append(proximity);
    for (const source of candidate.shared_faces || []) {
      const sourceLabel = document.createElement('label');
      sourceLabel.className = 'check';
      const honor = document.createElement('input');
      honor.type = 'checkbox';
      honor.dataset.approvedEdge = source.id;
      honor.dataset.unavailable = String(!guidedSourceAvailable(source));
      honor.checked = buildFacesBoundarySources.includes(source.id);
      honor.disabled = !include.checked || include.disabled || !guidedSourceAvailable(source);
      sourceLabel.append(honor, document.createTextNode(`Honor approved edge from ${source.label}`));
      honor.onchange = () => {
        if (!canDiscardFaceReview()) { honor.checked = !honor.checked; return; }
        buildFacesBoundarySources = buildFacesBoundarySources.filter((id) => id !== source.id);
        if (honor.checked) buildFacesBoundarySources.push(source.id);
        invalidateBuildFaces('Edge guidance changed. Preview again.', true);
        inspectFaceCandidate(candidate);
        renderUnavailableFaceGuidance();
      };
      detail.append(sourceLabel);
      if (!guidedSourceAvailable(source)) {
        const unavailable = document.createElement('p');
        unavailable.className = 'hint build-face-warning';
        unavailable.textContent = `Edge unavailable: ${source.diagnostic || 'no finite boundary'}`;
        detail.append(unavailable);
      }
    }
    row.append(heading, detail);
    return row;
  });
  $('build-faces-candidates').replaceChildren(...rows);
  syncModelPickControls();
  updateFaceNeighborSummary();
  filterFaceCandidates();
  $('build-faces-candidate-status').textContent = '';
  renderBuildFacesScopes();
  renderUnavailableFaceGuidance();
  paintBuildFacesPreview();
}
function renderUnavailableFaceGuidance() {
  const unavailable = unavailableGuidedSources(buildFacesBoundarySources,
    buildFacesCandidates?.candidates || [], buildFacesCutters);
  const container = $('build-faces-unavailable-guidance');
  container.replaceChildren();
  if (!unavailable.length) return;
  const warning = document.createElement('p');
  warning.className = 'hint build-face-warning';
  const names = unavailable.map((id) => graphState.recipe.nodes.find((node) => node.id === id)?.label || id);
  warning.textContent = `Missing edge guidance: ${names.join(', ')}. Restore or discard to preview.`;
  const discard = document.createElement('button');
  discard.type = 'button';
  discard.textContent = 'Discard unavailable guidance';
  discard.onclick = () => {
    if (!canDiscardFaceReview()) return;
    buildFacesBoundarySources = buildFacesBoundarySources.filter((id) => !unavailable.includes(id));
    invalidateBuildFaces('Guidance discarded. Preview again.', true);
    renderUnavailableFaceGuidance();
    renderFaceCandidates(buildFacesCutters, true);
  };
  container.append(warning, discard);
}
async function loadFaceCandidates(previous = null, approvedSources = null, exact = false) {
  clearModelPickHover();
  $('model-pick-choices').hidePopover();
  const { choices, unavailable } = buildFacesGeometry();
  invalidateBuildFaces(unavailableSavedFaceInputsMessage() || unavailableFitsMessage(unavailable));
  buildFacesCandidates = null;
  buildFacesInspected = null;
  buildFacesCutters = [];
  $('build-faces-candidates').replaceChildren();
  syncModelPickControls();
  $('build-faces-candidate-status').textContent = 'Finding candidate neighbors…';
  if (!buildFacesTarget || buildFacesMode !== 'guided' || !choices.some((choice) =>
      sameSurfaceReference(choice.reference, buildFacesTarget))) {
    if (!buildFacesTarget) $('build-faces-error').textContent ||= 'Target fit unavailable.';
    $('build-faces-candidate-status').textContent = '';
    return;
  }
  const token = graphState.token, requestId = buildFacesRequest;
  const target = structuredClone(buildFacesTarget);
  try {
    const candidates = await request('/api/graph/build-faces/candidates', {
      token, target, owner_id: buildFacesOwnerId,
      surfaces: choices.map((choice) => choice.reference),
    });
    if (requestId !== buildFacesRequest || token !== graphState.token ||
        !$('build-faces-dialog').open || buildFacesMode !== 'guided' ||
        !sameSurfaceReference(target, buildFacesTarget)) return;
    buildFacesCandidates = candidates;
    const availableSources = [...new Set(candidates.candidates.filter((candidate) =>
      guidedCandidateSelected(candidate, previous, exact)).flatMap((candidate) =>
      (candidate.shared_faces || []).filter(guidedSourceAvailable).map((face) => face.id)))];
    buildFacesBoundarySources = approvedSources === null ? availableSources
      : [...approvedSources];
    renderFaceCandidates(previous, exact);
  } catch (error) {
    if (requestId === buildFacesRequest) {
      $('build-faces-candidate-status').textContent = 'Candidate discovery failed.';
      $('build-faces-error').textContent = error.message;
    }
  }
}

function guidedAdjacencies() {
  return buildFacesMode === 'guided' ? [
    ...guidedRejectedDecisions(buildFacesAdjacencyDraft, buildFacesTarget, buildFacesCutters),
    ...buildFacesCutters.map((second) => ({ first: buildFacesTarget, second, state: 'confirmed' })),
  ] : buildAdjacencyDecisions(buildFacesAdjacencyDraft, buildFacesSelectedSurfaces());
}
function buildFacesScopesSignature() {
  const surfaces = buildFacesMode === 'guided' ? (buildFacesTarget ? [buildFacesTarget] : []) : buildFacesBatchDraft;
  return JSON.stringify({ choices: buildFacesChoicesKey, scopes: surfaces.map((surface) => ({ surface,
    choices: faceScopeChoices(graphState.recipe.nodes, surface, buildFacesOwnerId) })) });
}
function renderBuildFacesScopes() {
  buildFacesScopesKey = buildFacesScopesSignature();
  const surfaces = buildFacesMode === 'guided' ? (buildFacesTarget ? [buildFacesTarget] : [])
    : buildFacesSelectedSurfaces();
  const options = buildFacesGeometry().choices;
  const rows = surfaces.map((surface) => {
    const row = document.createElement('div');
    row.className = 'build-face-row';
    const label = document.createElement('label');
    label.textContent = options.find((choice) => sameSurfaceReference(choice.reference, surface))?.label || surface.feature;
    const select = document.createElement('select');
    select.multiple = true;
    select.size = 3;
    select.setAttribute('aria-label', `Declared face scope for ${label.textContent}`);
    const existing = faceScopeChoices(graphState.recipe.nodes, surface, buildFacesOwnerId);
    const selected = buildFacesScopeDraft.filter((scope) => sameSurfaceReference(scope.surface, surface))
      .flatMap((scope) => scope.faces);
    for (const face of existing) {
      const option = new Option(face.label, face.id);
      option.selected = selected.includes(face.id);
      select.append(option);
    }
    select.disabled = !existing.length;
    const detail = document.createElement('p');
    detail.className = 'hint';
    detail.textContent = selected.length
      ? `${selected.length} scope faces` : 'Whole surface';
    const unavailable = selected.filter((id) => !existing.some((face) => face.id === id));
    if (unavailable.length) {
      detail.classList.add('build-face-warning');
      detail.textContent = 'Scope unavailable. Replace it or use the whole surface.';
    }
    select.onchange = () => {
      buildFacesScopeDraft = buildFacesScopeDraft.filter((scope) => !sameSurfaceReference(scope.surface, surface));
      const faces = [...select.selectedOptions].map((option) => option.value);
      if (faces.length) buildFacesScopeDraft.push({ surface, faces });
      invalidateBuildFaces('Face scope changed. Preview again before reviewing faces.', true);
      renderBuildFacesScopes();
    };
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = 'Use whole surface';
    clear.disabled = !selected.length;
    clear.onclick = () => {
      buildFacesScopeDraft = buildFacesScopeDraft.filter((scope) => !sameSurfaceReference(scope.surface, surface));
      invalidateBuildFaces('Face scope changed. Preview again before reviewing faces.', true);
      renderBuildFacesScopes();
    };
    label.append(select);
    row.append(label, detail, clear);
    return row;
  });
  $('build-faces-scopes').replaceChildren(...rows);
}
function buildFacesScopeError(surfaces) {
  for (const surface of surfaces) {
    const available = new Set(faceScopeChoices(graphState.recipe.nodes, surface, buildFacesOwnerId)
      .map((face) => face.id));
    if (buildFacesScopeDraft.some((scope) => sameSurfaceReference(scope.surface, surface) &&
        scope.faces.some((id) => !available.has(id))))
      return 'A selected declared face scope is unavailable. Choose replacement faces or explicitly use the whole surface.';
  }
  return null;
}
function renderBuildFacesAdjacencies() {
  const rows = (buildFacesProposal.adjacencies || []).map((adjacency) => {
    const row = document.createElement('div');
    row.className = 'build-face-row';
    const label = document.createElement('label');
    label.textContent = adjacency.label;
    const select = document.createElement('select');
    select.setAttribute('aria-label', `Physical adjacency for ${adjacency.label}`);
    select.append(new Option('Unreviewed / proposed', 'proposed'),
      new Option('Confirmed', 'confirmed'), new Option('Rejected', 'rejected'));
    const provenEmpty = adjacency.mathematical.status === 'proven_empty';
    select.value = provenEmpty ? 'rejected'
      : ['confirmed', 'rejected'].includes(adjacency.state) ? adjacency.state : 'proposed';
    select.disabled = provenEmpty;
    const math = document.createElement('p');
    math.className = 'hint';
    math.textContent = `${adjacency.mathematical.status.replaceAll('_', ' ')} · ` +
      `${adjacency.mathematical.category}: ${adjacency.mathematical.reason}`;
    const evidence = document.createElement('p');
    evidence.className = 'hint';
    const gap = adjacency.evidence?.observation_bounds_gap;
    evidence.textContent = Number.isFinite(gap)
      ? `Scan gap: ${gap.toPrecision(5)}` : 'Scan gap unavailable';
    const detail = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = adjacency.supported ? 'Details' : 'Unsupported intersection';
    detail.append(summary, math, evidence);
    select.onchange = () => {
      const key = adjacencyPairKey(adjacency.first, adjacency.second);
      buildFacesAdjacencyDraft = buildFacesAdjacencyDraft.filter((decision) =>
        adjacencyPairKey(decision.first, decision.second) !== key);
      if (['confirmed', 'rejected'].includes(select.value))
        buildFacesAdjacencyDraft.push({ first: adjacency.first, second: adjacency.second, state: select.value });
      invalidateBuildFaces('Adjacency decision changed. Preview again before reviewing faces.', true);
    };
    label.append(select);
    row.append(label, detail);
    return row;
  });
  $('build-faces-adjacencies').replaceChildren(...rows);
}
function openBuildFaces(owner = null, target = null) {
  if (buildFacesApplying) {
    status('Wait for the current face batch to finish applying.', true);
    return;
  }
  if ($('build-faces-dialog').open && !canDiscardFaceReview()) return;
  setModelPickMode(null);
  if ($('relationship-dialog').open) $('relationship-dialog').close();
  invalidateBuildFaces();
  buildFacesOwnerId = owner?.id || null;
  buildFacesAdjacencyDraft = structuredClone(owner?.adjacencies || []);
  buildFacesScopeDraft = structuredClone(owner?.face_scopes || []);
  buildFacesMode = owner ? (owner.target ? 'guided' : 'batch') : 'guided';
  buildFacesCandidates = null;
  buildFacesInspected = null;
  buildFacesCutters = [];
  buildFacesBatchDraft = structuredClone(owner?.surfaces || []);
  buildFacesChoicesKey = buildFacesScopesKey = null;
  buildFacesChoicesMessage = '';
  buildFacesBoundarySources = structuredClone(owner?.boundary_sources || []);
  buildFacesSurfacePreview = null;
  buildFacesSurfaceHovered = false;
  buildFacesContinuePreview = null;
  buildFacesContinueInteraction = { pointer: null, focus: null };
  $('build-faces-neighbor-filter').value = '';
  $('build-faces-options').open = false;
  $('build-faces-label').value = owner?.label || nextFeatureLabel('Build faces');
  buildFacesTarget = target || owner?.target || null;
  const { choices: options, unavailable } = buildFacesGeometry();
  $('build-faces-error').textContent = unavailableSavedFaceInputsMessage() || unavailableFitsMessage(unavailable);
  const selected = owner?.surfaces || options.filter((choice) =>
    selectedFeatureIds.has(choice.reference.feature) || selectedFeatureIds.has(choice.reference.surface))
    .map((choice) => choice.reference);
  const requestedTarget = target || owner?.target;
  buildFacesTarget = requestedTarget
    ? requestedTarget
    : selected[0] || options[0]?.reference || null;
  buildFacesBatchDraft = structuredClone(selected);
  refreshBuildFacesChoices();
  $('build-faces-target').onpointerenter = () => {
    buildFacesSurfaceHovered = true;
    inspectFaceCandidate(null);
  };
  $('build-faces-target').onpointerleave = () => {
    buildFacesSurfaceHovered = false;
    paintBuildFacesPreview();
  };
  $('build-faces-target').onfocus = () => inspectFaceCandidate(null);
  $('build-faces-target').onblur = () => paintBuildFacesPreview();
  $('build-faces-title').textContent = owner ? 'Review / update faces' : 'Build faces';
  $('build-faces-next-neighbors').hidden = true;
  setBuildFacesModeDisplay();
  if (!$('build-faces-dialog').open) $('build-faces-dialog').show();
  revealWorkspacePanel('build-faces-dialog');
  syncModelPickControls();
  if (buildFacesMode === 'guided') {
    $('build-faces-target').focus();
    void loadFaceCandidates(owner?.surfaces || null, owner ? owner.boundary_sources || [] : null);
  } else $('build-faces-surfaces').focus();
}
function refreshBuildFacesChoices() {
  // Apply restores existing controls in its finally block; refresh afterwards.
  if (buildFacesApplying) return;
  const { choices: options, unavailable } = buildFacesGeometry(),
    message = unavailableSavedFaceInputsMessage(options) || unavailableFitsMessage(unavailable),
    key = JSON.stringify({ options, message });
  if (key !== buildFacesChoicesKey) {
    if (buildFacesChoicesKey !== null) {
      invalidateBuildFaces('Fit choices changed. Find candidate neighbors and preview again.');
      buildFacesCandidates = null;
      buildFacesInspected = null;
      $('build-faces-candidates').replaceChildren();
    }
    buildFacesChoicesKey = key;
    if (message || $('build-faces-error').textContent === buildFacesChoicesMessage)
      $('build-faces-error').textContent = message;
    buildFacesChoicesMessage = message;
    const targetAvailable = options.some((choice) => sameSurfaceReference(choice.reference, buildFacesTarget));
    faceTargetField.update([
      ...options.map(choice => ({
        key: JSON.stringify(choice.reference), label: choice.label,
        selected: sameSurfaceReference(choice.reference, buildFacesTarget),
      })),
      ...(buildFacesTarget && !targetAvailable ? [{
        key: JSON.stringify(buildFacesTarget),
        label: `Unavailable: ${fittedSurfacePresentation(buildFacesTarget, graphState.recipe.nodes).name}`,
        selected: true, disabled: true,
      }] : []),
    ], { disabled: featureTreeLocked() || buildFacesApplying });
    const result = graphState.results[buildFacesTarget?.feature],
      targetFit = buildFacesTarget?.surface ? result?.surfaces?.[buildFacesTarget.surface] : result;
    $('focus-face-target').disabled = !targetAvailable || !targetFit?.ids?.length;
    $('build-faces-surfaces').replaceChildren(...options.map((choice) => {
      const option = document.createElement('option');
      option.value = JSON.stringify(choice.reference);
      option.textContent = choice.label;
      option.selected = buildFacesBatchDraft.some((reference) => sameSurfaceReference(reference, choice.reference));
      return option;
    }));
  }
  if (buildFacesScopesSignature() !== buildFacesScopesKey) renderBuildFacesScopes();
}
function buildFacesPreviewGeometry(preview, color, edge = false, closed = true, emphasis = 'region', group = buildFacesOverlays) {
  if (!validGeometryPreview(preview) || !preview.positions.length) return;
  if (edge) {
    group.add(...faceEdgeLines(preview.positions, color, closed, emphasis));
  } else {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(preview.positions, 3));
    geometry.setIndex(preview.indices);
    geometry.computeVertexNormals();
    const cell = new THREE.Mesh(geometry,
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true,
        opacity: 0.5, depthWrite: false }));
    cell.renderOrder = 100;
    group.add(cell);
    return cell;
  }
}
function inspectFaceRegion(region) {
  if (buildFacesRegionInspected?.faceKey === region?.faceKey &&
      buildFacesRegionInspected?.regionKey === region?.regionKey) return;
  buildFacesRegionInspected = region;
  for (const row of $('build-faces-review').querySelectorAll('.face-region'))
    row.classList.toggle('region-inspected', row.dataset.faceKey === region?.faceKey &&
      row.dataset.regionKey === region?.regionKey);
  paintBuildFacesPreview();
}
function updateFaceRegionTools() {
  const faces = buildFacesMode === 'guided'
    ? guidedProposalFaces(buildFacesProposal, buildFacesTarget) : buildFacesProposal?.faces || [];
  const count = faces.reduce((sum, face) => sum +
    (buildFacesReview.get(face.key)?.region_keys || []).length, 0);
  $('build-faces-region-tools').hidden = !buildFacesProposal;
  $('face-region-count').textContent = `${count} selected`;
  $('clear-face-regions').disabled = buildFacesApplying || !count;
  $('select-suggested-face-regions').disabled = buildFacesApplying || !faces.some((face) =>
    changeBuildFaceRegionSelection(face, buildFacesReview.get(face.key)?.region_keys || [], 'suggested').changed);
}
function changeFaceRegionSelection(face, operation, regionKey = null) {
  const next = changeBuildFaceRegionSelection(face,
    buildFacesReview.get(face.key)?.region_keys || [], operation, regionKey);
  if (!next.changed) return false;
  buildFacesReview.set(face.key, { region_keys: next.region_keys });
  buildFacesReviewDirty = true;
  renderFaceDialogClose($('close-build-faces'), false);
  return true;
}
function paintBuildFacesPreview() {
  clearBuildFacesPreview();
  updateFaceRegionTools();
  if (buildFacesMode === 'guided' && (buildFacesTarget || buildFacesSurfacePreview) && $('build-faces-dialog').open) {
    const { reference, prominent } = surfaceFootprintState(buildFacesTarget, buildFacesSurfacePreview,
      buildFacesSurfaceHovered || document.activeElement === $('build-faces-target'), !!buildFacesInspected);
    paintFaceFootprint(reference, prominent, '#78e2ff');
    if (buildFacesContinuePreview) paintFaceFootprint(buildFacesContinuePreview, true, '#ffe45c');
  }
  $('build-faces-candidate-status').textContent = '';
  if (buildFacesMode === 'guided') {
    const targetResult = graphState.results[buildFacesTarget?.feature],
      targetFit = buildFacesTarget?.surface ? targetResult?.surfaces?.[buildFacesTarget.surface] : targetResult;
    for (const candidate of guidedNeighborHighlights(buildFacesCandidates?.candidates || [],
      buildFacesCutters, buildFacesInspected)) {
      const inspecting = sameSurfaceReference(candidate.reference, buildFacesInspected?.reference);
      const sources = inspecting ? candidate.shared_faces || []
        : (candidate.shared_faces || []).filter((source) => buildFacesBoundarySources.includes(source.id));
      const approved = approvedNeighborPreviewPaths({ ...candidate, shared_faces: sources }, graphState.results,
        buildFacesCandidates?.target_key);
      const paths = approved.length ? approved : intersectionPreviewPaths(candidate.geometry,
        { ids: targetFit?.ids, positions });
      for (const path of paths) buildFacesPreviewGeometry(path.preview,
        inspecting ? '#ffe45c' : '#78c5ff', true, path.closed, inspecting ? 'inspected' : 'neighbor');
      if (inspecting) $('build-faces-candidate-status').textContent =
        `${fittedFaceCandidatePresentation(candidate).name} · ${approved.length ? 'approved edge'
          : paths.length > 1 ? `${paths.length} branches` : paths.length ? 'intersection' : 'no curve'}`;
    }
  }
  if (!buildFacesProposal) { draw(); return; }
  const edges = new Map(buildFacesProposal.intersections.map((edge) => [edge.key, edge]));
  const drawnEdges = new Set();
  const faces = buildFacesMode === 'guided'
    ? guidedProposalFaces(buildFacesProposal, buildFacesTarget) : buildFacesProposal.faces;
  faces.forEach((face, index) => {
    const review = buildFacesReview.get(face.key);
    const selected = new Set(review?.region_keys || []);
    if (face.blocked_by_adjacency || face.blocked_by_geometry) return;
    const color = palette[index % palette.length];
    for (const region of boundedBuildFaceRegions(face).filter((candidate) =>
      validBuildFaceRegion(candidate) && (buildFacesMode === 'guided' || selected.has(candidate.key)))) {
      const inspecting = buildFacesRegionInspected?.faceKey === face.key &&
        buildFacesRegionInspected?.regionKey === region.key;
      const cell = buildFacesPreviewGeometry(region.preview, inspecting ? '#ffe45c'
        : selected.has(region.key) ? color : '#94a6b8');
      if (cell && buildFacesMode === 'guided') {
        cell.material.opacity = inspecting ? 0.65 : selected.has(region.key) ? 0.6 : 0.10;
        cell.userData = { faceKey: face.key, regionKey: region.key };
      }
      if (!selected.has(region.key) && !inspecting) continue;
      const loopPaths = faceBoundaryPreviewPaths(region.bounds, region.loops);
      for (const path of loopPaths) buildFacesPreviewGeometry(path.preview,
        inspecting ? '#ffe45c' : color, true, path.closed, inspecting ? 'inspected' : 'region');
      for (const boundary of region.boundaries || []) {
        if (drawnEdges.has(boundary.intersection_key)) continue;
        drawnEdges.add(boundary.intersection_key);
        if (loopPaths.length) continue;
        for (const path of intersectionPreviewPaths(edges.get(boundary.intersection_key)?.geometry))
          buildFacesPreviewGeometry(path.preview, inspecting ? '#ffe45c' : color,
            true, path.closed, inspecting ? 'inspected' : 'region');
      }
    }
  });
  $('apply-build-faces').disabled = buildFacesApplying || !buildFaceChoices(buildFacesProposal, buildFacesReview).length;
  draw();
}
function paintFaceFootprint(reference, prominent, color, group = buildFacesOverlays) {
  const result = graphState.results[reference?.feature];
  const fitted = reference?.surface ? result?.surfaces?.[reference.surface] : result;
  if (fitted && graphState.states[reference.feature] === 'ready') {
    const indices = mesh.geometry.index?.array;
    if (!buildFacesMeshTopology || buildFacesMeshTopology.positions !== positions ||
        buildFacesMeshTopology.indices !== indices) {
      buildFacesMeshTopology = { positions, indices,
        topology: buildFootprintTopology(indices, positions.length / 3) };
    }
    const topology = buildFacesMeshTopology.topology;
    let cached = buildFacesFootprints.get(fitted);
    if (!cached || cached.positions !== positions || cached.topology !== topology) {
      cached = { positions, topology, paths: fittedSelectionFootprint(fitted, positions, topology) };
      buildFacesFootprints.set(fitted, cached);
    }
    for (const path of cached.paths) {
      const lines = faceEdgeLines(path.preview.positions, color, path.closed,
        prominent ? 'footprint' : 'footprint-context');
      group.add(...lines);
    }
  }
}
function paintBuildFacesContext() {
  if (!graphState || !buildFacesContext) return;
  const excluded = previewedFaceContextIds(buildFacesProposal, buildFacesTarget),
    key = excluded.sort().join('|');
  if (buildFacesContextCache?.state === graphState && buildFacesContextCache.bridges === buildFacesAcceptedContext &&
      buildFacesContextCache.key === key) return;
  buildFacesContextCache = { state: graphState, bridges: buildFacesAcceptedContext, key };
  for (const child of [...buildFacesContext.children]) {
    buildFacesContext.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  const faces = definedFaceContext(graphState, buildFacesAcceptedContext, excluded);
  buildFacesContext.userData.faceCount = faces.length;
  for (const { result: face } of faces) {
    const cell = buildFacesPreviewGeometry(face.preview, '#98aeb9', false, true, 'region', buildFacesContext);
    cell.material.opacity = 0.22;
    cell.material.depthTest = false;
    cell.renderOrder = 90;
    let paths = faceBoundaryPreviewPaths(face.bounds, face.loops);
    if (!paths.length) paths = (face.boundary_ids || []).flatMap((id) =>
      graphState.states[id] === 'ready' ? intersectionPreviewPaths(graphState.results[id]).filter((path) => path.closed) : []);
    for (const path of paths) buildFacesPreviewGeometry(path.preview, '#87a8b5', true,
      path.closed, 'defined-face', buildFacesContext);
  }
}
function renderBuildFacesReview(preserveReview = false) {
  renderFaceDialogClose($('close-build-faces'), false);
  renderBuildFacesAdjacencies();
  const expanded = new Set([...$('build-faces-review').querySelectorAll('.face-region-list[open]')]
    .map((list) => list.dataset.faceKey));
  const faces = buildFacesMode === 'guided'
    ? guidedProposalFaces(buildFacesProposal, buildFacesTarget) : buildFacesProposal.faces;
  const rows = faces.map((face) => {
    const available = new Set(boundedBuildFaceRegions(face).filter(validBuildFaceRegion).map((region) => region.key));
    const selected = new Set(preserveReview
      ? (buildFacesReview.get(face.key)?.region_keys || []).filter((key) => available.has(key)) : buildFacesMode === 'guided'
        ? initialGuidedFaceRegions(face) : initialBuildFaceRegions(face));
    const review = { region_keys: [...selected] };
    buildFacesReview.set(face.key, review);
    const row = document.createElement('div');
    row.className = 'build-face-row';
    const title = document.createElement('strong');
    title.textContent = buildFacesMode === 'guided' ? 'Regions' : face.label;
    row.append(title);
    if (!boundedBuildFaceRegions(face).length) {
      const missing = document.createElement('p');
      missing.className = 'hint';
      missing.textContent = 'No bounded regions. Add boundary surfaces.';
      row.append(missing);
    }
    const groups = buildFaceRegionGroups(face, [...selected]);
    const renderRegion = (region, parent) => {
      const regionRow = document.createElement('div');
      regionRow.className = 'face-region';
      regionRow.dataset.faceKey = face.key;
      regionRow.dataset.regionKey = region.key;
      regionRow.classList.toggle('region-inspected', buildFacesRegionInspected?.faceKey === face.key &&
        buildFacesRegionInspected?.regionKey === region.key);
      const label = document.createElement('label');
      label.className = 'check';
      const accept = document.createElement('input');
      accept.type = 'checkbox';
      accept.checked = selected.has(region.key);
      accept.disabled = !!face.blocked_by_adjacency || !!face.blocked_by_geometry || !validBuildFaceRegion(region);
      const disposition = region.existing_face_id ? ' · reused'
        : region.owned_face_id ? ' · applied' : '';
      accept.setAttribute('aria-label', region.label);
      label.append(accept, document.createTextNode(`${region.label}${disposition}`));
      const badge = document.createElement('span');
      badge.className = `face-badge${region.bounded ? '' : ' warning'}`;
      badge.textContent = !validBuildFaceRegion(region) ? 'Unavailable' : region.bounded ? 'Bounded' : 'Open';
      badge.title = region.bounded ? 'Bounded face' : 'Open extent; preview only';
      const disclosure = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = 'Scan';
      const detail = document.createElement('p');
      detail.className = 'hint';
      detail.textContent = faceEvidenceSummary(region.evidence);
      disclosure.append(summary, detail);
      accept.onchange = () => {
        changeFaceRegionSelection(face, 'toggle', region.key);
        paintBuildFacesPreview();
      };
      const inspect = () => inspectFaceRegion({ faceKey: face.key, regionKey: region.key });
      regionRow.onpointerenter = (event) => { if (!event.buttons) inspect(); };
      regionRow.onpointerleave = () => {
        if (!regionRow.contains(document.activeElement)) inspectFaceRegion(null);
      };
      regionRow.addEventListener('focusin', inspect);
      regionRow.addEventListener('focusout', (event) => {
        if (!regionRow.contains(event.relatedTarget)) inspectFaceRegion(null);
      });
      regionRow.append(label, badge, disclosure);
      parent.append(regionRow);
    };
    if (buildFacesMode === 'guided' && compactFaceRegionList(face)) {
      const folded = document.createElement('details'), summary = document.createElement('summary');
      folded.className = 'face-region-list';
      folded.dataset.faceKey = face.key;
      folded.open = expanded.has(face.key);
      summary.textContent = `Individual regions (${groups.primary.length + groups.other.length})`;
      folded.append(summary);
      for (const region of [...groups.primary, ...groups.other]) renderRegion(region, folded);
      row.append(folded);
    } else {
      for (const region of groups.primary) renderRegion(region, row);
    }
    if (groups.other.length && !(buildFacesMode === 'guided' && compactFaceRegionList(face))) {
      const folded = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `Other regions (${groups.other.length})`;
      folded.append(summary);
      for (const region of groups.other) renderRegion(region, folded);
      row.append(folded);
    }
    if (face.blocked_by_adjacency || face.status === 'requires_adjacency_review') {
      const warning = document.createElement('p');
      warning.className = 'hint build-face-warning';
      warning.textContent = face.blocked_by_adjacency
        ? 'Unsupported confirmed boundary. Review adjacency decisions.'
        : 'Unresolved adjacency. Review decisions before selecting regions.';
      row.append(warning);
    }
    if (face.blocked_by_geometry) {
      const warning = document.createElement('p');
      warning.className = 'hint build-face-warning';
      warning.textContent = 'Invalid geometry. Resolve diagnostics before applying.';
      row.append(warning);
    }
    for (const message of face.diagnostics) {
      const warning = document.createElement('p');
      warning.className = 'hint build-face-warning';
      warning.textContent = message;
      row.append(warning);
    }
    return row;
  });
  $('build-faces-review').replaceChildren(...rows);
  $('build-faces-diagnostics').replaceChildren(...buildFacesProposal.diagnostics.map((diagnostic) => {
    const warning = document.createElement('p');
    warning.className = 'build-face-warning';
    warning.textContent = diagnostic.message;
    return warning;
  }));
  paintBuildFacesPreview();
}
function guidedFaceRegionAt(event) {
  if (!buildFacesProposal || buildFacesApplying || !$('build-faces-dialog').open ||
      buildFacesMode !== 'guided') return null;
  const canvas = renderer.domElement, bounds = canvas.getBoundingClientRect();
  camera.updateMatrixWorld();
  modelRoot.updateMatrixWorld(true);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(
    (event.clientX - bounds.left) / bounds.width * 2 - 1,
    1 - (event.clientY - bounds.top) / bounds.height * 2), camera);
  const cells = buildFacesOverlays.children.filter((child) => child.userData.regionKey);
  const hit = raycaster.intersectObjects(cells, false)[0];
  if (!hit) return null;
  const { faceKey, regionKey } = hit.object.userData;
  const face = guidedProposalFaces(buildFacesProposal, buildFacesTarget).find((item) => item.key === faceKey);
  if (!face || face.blocked_by_adjacency || face.blocked_by_geometry ||
      !boundedBuildFaceRegions(face).some((region) => region.key === regionKey && validBuildFaceRegion(region))) return null;
  return { faceKey, regionKey };
}
function pickGuidedFaceRegion(event) {
  const hit = guidedFaceRegionAt(event);
  if (!hit) return;
  const face = guidedProposalFaces(buildFacesProposal, buildFacesTarget)
    .find((item) => item.key === hit.faceKey);
  if (!changeFaceRegionSelection(face, 'toggle', hit.regionKey)) return;
  inspectFaceRegion(hit);
  renderBuildFacesReview(true);
  const count = buildFacesReview.get(face.key).region_keys.length;
  status(`${count} retained region${count === 1 ? '' : 's'} on ${face.label}. Apply to save the face.`);
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
  return graphState?.recipe.nodes.find((n) => n.id === referenceFeature(readInputReference(id)));
}
function selectOnly(id) {
  selectedFeatureIds = new Set(id ? [id] : []);
  selectedFeatureId = id || null;
}
function toggleFeatureSelection(id) {
  if (selectedFeatureIds.has(id)) selectedFeatureIds.delete(id);
  else selectedFeatureIds.add(id);
  selectedFeatureId = selectedFeatureIds.size === 1 ? [...selectedFeatureIds][0] : null;
}
function updateDisplayTransform() {
  if (!modelRoot || !graphState) return false;
  const active = activeDisplayTransform(graphState, selectedFeatureIds),
    key = active ? `${active.id}:${JSON.stringify(active.matrix)}` : 'identity';
  if (key === displayTransformKey) return false;
  displayTransformKey = key;
  modelRoot.matrixAutoUpdate = false;
  modelRoot.matrix.identity();
  if (active) modelRoot.matrix.set(...active.matrix.flat());
  modelRoot.updateMatrixWorld(true);
  return true;
}
function refreshFeatureSelection(scroll = false) {
  if (scroll) $('feature-properties-panel').scrollTop = 0;
  const transformChanged = updateDisplayTransform();
  renderActions();
  showProperties();
  showResult();
  paint();
  if (transformChanged) home('oblique');
}
function featureGraphOptions() {
  return {
    lens: $('feature-graph-lens').value,
    showSelections: $('feature-graph-selections').checked,
    showGenerated: $('feature-graph-generated').checked,
    selectedNeighborhood: $('feature-graph-neighborhood').checked,
  };
}
function renderGraphView() {
  if (!graphState) return;
  const graph = renderFeatureGraph($('feature-graph-canvas'), {
    recipe: graphState.recipe,
    ...featureTreePresentation(graphState.recipe.nodes, graphState.states, graphState.errors),
    selected: selectedFeatureIds,
    options: featureGraphOptions(),
    onSelect: (id, { clear = false } = {}) => {
      if (modelPickMode) { if (id) pickFeatureInput(id); return; }
      if (clear || !id) selectOnly(null);
      else toggleFeatureSelection(id);
      refreshFeatureSelection(true);
    },
  });
  $('feature-graph-summary').textContent =
    `${graph.nodes.length} feature${graph.nodes.length === 1 ? '' : 's'} · ` +
    `${graph.edges.length} connection${graph.edges.length === 1 ? '' : 's'}`;
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
  if (featureTreeLocked()) return;
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
      : [...selectedFeatureIds],
  );
  $('feature-group-error').textContent = '';
  $('feature-group-dialog').showModal();
  $('feature-group-label').focus();
  $('feature-group-label').select();
}
async function removeFeatureGroup(groupId) {
  if (featureTreeLocked()) return;
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
  // Begin at the first empty required geometry input while keeping the name and
  // other draft settings visible. Existing-feature inspection never starts picks.
  setTimeout(() => {
    const dialog = $(dialogId);
    if (!dialog.open || modelPickMode) return;
    const control = [...dialog.querySelectorAll('select[required]')].find(select =>
      ![...select.selectedOptions].some(option => option.value) && nativePickOptions(select).length &&
      [...dialog.querySelectorAll('[data-pick-control]')].some(button => button.dataset.pickControl === select.id));
    const button = [...dialog.querySelectorAll('[data-pick-control]')]
      .find(button => button.dataset.pickControl === control?.id);
    button?.click();
  }, 0);
}
function clearBodyInspection() {
  if (!bodyInspection) return;
  for (const child of [...bodyInspection.children]) {
    bodyInspection.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
}
function inspectBodyFace(id) {
  clearBodyInspection();
  const node = graphNode(id), fitted = graphState.results[id];
  if (node && fitted) physicalGeometryGuide(node, fitted, '#ffe45c', bodyInspection);
  draw();
}
function bodyCheckedFaces(container) {
  return [...$(container).querySelectorAll('input:checked')].map((input) => input.value);
}
function renderBodyFaces(container, selected) {
  const included = new Set(selected), countId = container === 'new-body-face-choices'
    ? 'new-body-face-count' : 'body-face-count';
  const updateCount = () => { $(countId).textContent = ` · ${bodyCheckedFaces(container).length} selected`; };
  $(container).replaceChildren(...bodyFaceChoices(graphState).map(({ node, state, open }) => {
    const row = document.createElement('label'), input = document.createElement('input'),
      name = document.createElement('span'), readiness = document.createElement('span');
    input.type = 'checkbox';
    input.value = node.id;
    input.checked = included.has(node.id);
    input.onchange = updateCount;
    name.textContent = node.label;
    readiness.className = 'hint';
    readiness.textContent = open ? 'Open' : state === 'ready' ? '' : state;
    row.append(input, featureIcon(node.operation), name, readiness);
    row.onpointerenter = row.onfocusin = () => inspectBodyFace(node.id);
    row.onpointerleave = row.onfocusout = () => { clearBodyInspection(); draw(); };
    return row;
  }));
  updateCount();
}
function showBodyDiagnostics(diagnostic) {
  const container = $('body-diagnostics');
  container.replaceChildren();
  container.hidden = diagnostic?.kind !== 'body';
  if (container.hidden) return;
  const paintEdges = (record = diagnostic) => {
    clearBodyInspection();
    for (const edge of bodyEdgePaths(record))
      bodyInspection.add(...faceEdgeLines(edge.positions, '#ff725d', false, 'inspected'));
    draw();
  };
  paintEdges();
  for (const problem of bodyProblems(diagnostic, graphState.recipe.nodes)) {
    const button = document.createElement('button'), names = document.createElement('span');
    button.type = 'button';
    button.textContent = problem.message;
    names.className = 'hint';
    names.textContent = problem.sourceNames.join(' · ');
    button.append(names);
    const inspect = () => {
      const edges = bodyEdgePaths(diagnostic).filter((edge) =>
        edge.source_faces?.some((id) => problem.source_faces?.includes(id)));
      if (edges.length) paintEdges({ preview: { edges } });
      else {
        clearBodyInspection();
        for (const id of problem.source_faces || []) {
          const node = graphNode(id), face = graphState.results[id];
          if (node && face) physicalGeometryGuide(node, face, '#ff725d', bodyInspection);
        }
        draw();
      }
    };
    button.onpointerenter = button.onfocus = inspect;
    button.onpointerleave = button.onblur = () => paintEdges();
    container.append(button);
  }
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
function populateSurfaceReferences(id, nodes, selected = null) {
  const select = $(id);
  select.replaceChildren(new Option('Choose surface and geometry context…', ''));
  for (const choice of surfaceReferenceChoices(nodes, graphState.results, graphState.recipe.nodes)) {
    select.add(new Option(choice.label, JSON.stringify(choice.reference), false,
      sameSurfaceReference(choice.reference, selected)));
  }
}
function readSurfaceReference(id) {
  if (!$(id).value) throw new Error('Choose a surface and its geometry context.');
  return JSON.parse($(id).value);
}
function faceEditorNodes(prefix) {
  return prefix === 'face'
    ? graphState.recipe.nodes.slice(0, graphState.recipe.nodes.findIndex((node) => node.id === selectedFeatureId))
    : graphState.recipe.nodes;
}
function readFaceBoundaries(prefix) {
  return [...$(`${prefix}-boundary-rows`).children].map((row) => ({
    intersection: row.querySelector('[data-boundary-intersection]').value,
    keep: row.querySelector('[data-boundary-keep]').value,
  }));
}
function renderFaceBoundaries(prefix, nodes, boundaries = []) {
  const container = $(`${prefix}-boundary-rows`);
  container.replaceChildren();
  const selected = $(`${prefix}-surface`).value;
  if (!selected) return;
  const reference = JSON.parse(selected);
  const choice = surfaceReferenceChoices(nodes, graphState.results, graphState.recipe.nodes)
    .find((candidate) => sameSurfaceReference(candidate.reference, reference));
  const intersections = eligibleIntersections(nodes, reference);
  for (const boundary of boundaries) {
    const row = document.createElement('div');
    const label = document.createElement('label');
    label.textContent = 'Shared intersection';
    const select = document.createElement('select');
    select.dataset.boundaryIntersection = '';
    select.required = true;
    select.add(new Option('Choose shared intersection…', ''));
    for (const node of intersections)
      select.add(new Option(node.label, node.id, false, node.id === boundary.intersection));
    label.append(select);
    const sideLabel = document.createElement('label');
    sideLabel.textContent = 'Retain';
    const keep = document.createElement('select');
    keep.dataset.boundaryKeep = '';
    const updateKeepOptions = (selectedKeep) => {
      keep.replaceChildren();
      for (const option of boundaryKeepOptions(choice?.kind, graphState.results[select.value]))
        keep.add(new Option(option.label, option.value, false, option.value === selectedKeep));
    };
    updateKeepOptions(boundary.keep);
    select.onchange = () => updateKeepOptions(keep.value);
    sideLabel.append(keep);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'Remove boundary';
    remove.onclick = () => row.remove();
    row.append(label, sideLabel, remove);
    container.append(row);
  }
}
function addFaceBoundary(prefix) {
  try {
    const nodes = faceEditorNodes(prefix);
    const reference = readSurfaceReference(`${prefix}-surface`);
    const available = eligibleIntersections(nodes, reference);
    const boundaries = readFaceBoundaries(prefix);
    const unused = available.find((node) => !boundaries.some((boundary) => boundary.intersection === node.id));
    if (!unused) throw new Error('Create another shared intersection for this exact surface and geometry context first.');
    const kind = surfaceReferenceChoices(nodes, graphState.results, graphState.recipe.nodes)
      .find((choice) => sameSurfaceReference(choice.reference, reference)).kind;
    boundaries.push({ intersection: unused.id,
      keep: boundaryKeepOptions(kind, graphState.results[unused.id])[0].value });
    renderFaceBoundaries(prefix, nodes, boundaries);
  } catch (error) {
    status(error.message, true);
    if (prefix === 'new-face') $('trimmed-face-error').textContent = error.message;
  }
}
function geometryInputChoices(id, requirement, nodes, selected) {
  const control = typeof id === 'string' ? $(id) : id;
  control.dataset.inputRequirement = requirement;
  if (nodes.length < graphState.recipe.nodes.length)
    control.dataset.inputConsumer = selectedFeatureId || '';
  else delete control.dataset.inputConsumer;
  renderInputChoices(control, inputChoices(graphState, requirement, nodes), selected);
}
function frameGeometry(nodes) {
  return {
    points: inputChoices(graphState, 'point', nodes),
    references: inputChoices(graphState, 'direction', nodes),
  };
}
function pointCoordinates(reference) {
  return matchingInputOutput(reference, requirementOutputs(graphState, 'point'))?.preview?.point_display || null;
}
function currentPointDistance(first, second) {
  const a = pointCoordinates(first),
    b = pointCoordinates(second);
  if (!a || !b) return 1;
  return Math.hypot(...a.map((value, index) => value - b[index]));
}
function scaleDistanceRow(containerId, points, distance = {}) {
  const row = document.createElement('div'),
    firstLabel = document.createElement('label'),
    secondLabel = document.createElement('label'),
    knownLabel = document.createElement('label'),
    first = document.createElement('select'),
    second = document.createElement('select'),
    known = document.createElement('input'),
    remove = document.createElement('button');
  row.className = 'scale-distance-row';
  first.dataset.field = 'first';
  second.dataset.field = 'second';
  known.dataset.field = 'known';
  first.dataset.inputRequirement = second.dataset.inputRequirement = 'point';
  if (containerId === 'scale-distance-rows') first.dataset.inputConsumer = second.dataset.inputConsumer = selectedFeatureId;
  renderInputChoices(first, points, distance.first_point || points.find(point => point.availability === 'ready')?.reference);
  renderInputChoices(second, points, distance.second_point || points.filter(point => point.availability === 'ready')[1]?.reference);
  known.type = 'number';
  known.min = '0.000000000001';
  known.step = 'any';
  known.required = true;
  known.value = distance.known_distance ?? currentPointDistance(first.value, second.value);
  remove.type = 'button';
  remove.textContent = 'Remove';
  remove.onclick = () => row.remove();
  firstLabel.append('From point', first);
  secondLabel.append('To point', second);
  knownLabel.append('Known output distance', known);
  row.append(firstLabel, secondLabel, knownLabel, remove);
  $(containerId).append(row);
  return row;
}
function renderScaleDistances(containerId, points, distances) {
  $(containerId).replaceChildren();
  for (const distance of distances) scaleDistanceRow(containerId, points, distance);
}
function readScaleDistances(containerId) {
  return [...$(containerId).querySelectorAll('.scale-distance-row')].map((row) => ({
    first_point: readInputReference(row.querySelector('[data-field="first"]').value),
    second_point: readInputReference(row.querySelector('[data-field="second"]').value),
    known_distance: Number(row.querySelector('[data-field="known"]').value),
    weight: 1,
  }));
}
function isStandaloneFit(node) {
  return !node.axis && !node.point && !node.reference_plane;
}
let relationshipParticipantIds = new Set(), selectedRelationshipKind = null;
const relationshipDefinitions = [
  {
    id: 'coincident_planes',
    label: 'Coincident planes',
    baseName: 'Coincident planes',
    summary: 'All selected plane fits share one exact plane: normal and offset.',
  },
  {
    id: 'parallel_planes',
    label: 'Parallel planes',
    baseName: 'Parallel planes',
    summary: 'All selected plane fits share one exact normal; their offsets remain independent.',
  },
  {
    id: 'equal_radii',
    label: 'Equal radii',
    baseName: 'Equal radii',
    summary: 'All selected sphere and cylinder fits share one exact radius; their centers and axes remain independent.',
  },
  {
    id: 'mirror',
    label: 'Mirror symmetry',
    baseName: 'Mirrored pair',
    summary: 'Two same-type standalone fits mirror across the selected axis-containing plane.',
  },
  {
    id: 'radius_plane_distance',
    label: 'Radius equals plane distance',
    baseName: 'Radius equals plane distance',
    summary: 'The cylinder radius equals the absolute separation of the fit and reference planes.',
  },
];
function relationshipValidity(kind, participants) {
  const fits = participants.filter((node) => node.operation === 'fit'),
    planes = fits.filter((node) => node.kind === 'plane'),
    cylinders = fits.filter((node) => node.kind === 'cylinder'),
    radiusFits = fits.filter((node) => ['cylinder', 'sphere'].includes(node.kind)),
    datums = participants.filter((node) => node.operation === 'reference_plane'),
    only = (expected) => participants.length === expected;
  if (kind === 'coincident_planes' || kind === 'parallel_planes')
    return participants.length >= 2 && planes.length === participants.length
      ? { valid: true }
      : { valid: false, reason: 'Select two or more plane fits only.' };
  if (kind === 'equal_radii')
    return participants.length >= 2 && radiusFits.length === participants.length
      ? { valid: true }
      : { valid: false, reason: 'Select two or more sphere or cylinder fits only.' };
  if (kind === 'mirror') {
    const supported = fits.every((fit) => ['cone', 'cylinder', 'plane'].includes(fit.kind));
    const valid =
      only(3) &&
      fits.length === 2 &&
      datums.length === 1 &&
      supported &&
      fits.every(isStandaloneFit) &&
      fits[0]?.kind === fits[1]?.kind &&
      datums[0]?.construction === 'contains_axis';
    return valid
      ? { valid: true }
      : { valid: false, reason: 'Select two same-type standalone fits and one plane containing an axis.' };
  }
  const cylinder = cylinders[0],
    plane = planes[0],
    datum = datums[0],
    valid =
      only(3) &&
      cylinders.length === 1 &&
      planes.length === 1 &&
      datums.length === 1 &&
      cylinder.axis &&
      isStandaloneFit(plane) &&
      cylinder.axis === datum.axis;
  return valid
    ? { valid: true }
    : { valid: false, reason: 'Select one axis-bound cylinder, one standalone plane fit, and one reference plane on that axis.' };
}
function renderRelationshipBuilder() {
  const nodes = graphState.recipe.nodes;
  $('relationship-kind').replaceChildren(new Option('Choose a relationship', ''),
    ...relationshipDefinitions.map((definition) => new Option(definition.label, definition.id)));
  $('relationship-kind').value = selectedRelationshipKind || '';
  renderRelationshipParticipants($('relationship-participants'), {
    choices: relationshipParticipantChoices(nodes, selectedRelationshipKind, relationshipParticipantIds)
      .map(({ node, compatible }) => {
        const presentation = node.operation === 'fit'
          ? fittedSurfacePresentation({ feature: node.id }, nodes)
          : { name: node.label, icon: 'reference_plane', detail: `Reference plane · ${graphNode(node.axis)?.label || node.axis}` };
        return { ...presentation, id: node.id, compatible,
          detail: compatible ? presentation.detail : `${presentation.detail} · incompatible` };
      }),
    selected: relationshipParticipantIds,
    locked: () => featureTreeLocked() || relationshipApplying,
    change: (id, included) => {
      if (included) relationshipParticipantIds.add(id);
      else relationshipParticipantIds.delete(id);
      $('relationship-error').textContent = '';
      updateRelationshipSelection();
    },
    inspect: (id) => { relationshipInspected = id; paintRelationshipPreview(); },
    focus: (id) => focusRelationshipParticipants([id]),
  });
  filterRelationshipParticipants();
  updateRelationshipSelection();
}
function filterRelationshipParticipants() {
  const filter = $('relationship-filter').value.trim().toLowerCase();
  for (const row of $('relationship-participants').children)
    row.hidden = row.dataset.compatible === 'false' || !row.dataset.search.includes(filter);
  if (relationshipInspected && ![...$('relationship-participants').children].some((row) =>
    !row.hidden && row.querySelector('input').dataset.participantId === relationshipInspected))
    relationshipInspected = null;
  paintRelationshipPreview();
}
function updateRelationshipParticipantField() {
  if (!relationshipParticipantField) return;
  const nodes = graphState?.recipe.nodes || [];
  relationshipParticipantField.update(relationshipParticipantChoices(nodes,
    selectedRelationshipKind, relationshipParticipantIds).map(({ node, compatible }) => ({
    key: node.id, label: node.label,
    selected: relationshipParticipantIds.has(node.id), disabled: !compatible,
  })), { disabled: !graphState || featureTreeLocked() || relationshipApplying });
}
function updateRelationshipSelection() {
  syncModelPickControls();
  const participants = graphState.recipe.nodes.filter((node) => relationshipParticipantIds.has(node.id)),
    chosen = relationshipDefinitions.find((definition) => definition.id === selectedRelationshipKind),
    validity = chosen ? relationshipValidity(chosen.id, participants) : { valid: false };
  const summaries = {
    coincident_planes: 'Same plane: normal and offset.', parallel_planes: 'Same normal; independent offsets.',
    equal_radii: 'Same radius; independent positions.', mirror: 'Exact mirrored pair.',
    radius_plane_distance: 'Radius equals plane separation.',
  };
  $('relationship-summary').textContent = chosen
    ? validity.valid ? summaries[chosen.id] : validity.reason : 'Choose a relationship, then its participants.';
  $('relationship-count').textContent = `${participants.length} selected`;
  const locked = featureTreeLocked() || relationshipApplying;
  $('relationship-kind').disabled = relationshipApplying;
  $('new-relationship-label').readOnly = relationshipApplying;
  $('clear-relationship-participants').disabled = !participants.length || locked;
  $('focus-relationship').disabled = !participants.length;
  $('add-relationship').disabled = !validity.valid || locked;
  for (const checkbox of $('relationship-participants').querySelectorAll('input')) {
    const row = checkbox.closest('.relationship-participant');
    checkbox.disabled = locked || (row.dataset.compatible === 'false' && !checkbox.checked);
  }
  paintRelationshipPreview();
}
function paintRelationshipPreview() {
  if (!relationshipOverlays) return;
  for (const child of [...relationshipOverlays.children]) {
    relationshipOverlays.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  if ($('relationship-dialog').open) {
    const ids = new Set(relationshipParticipantIds);
    if (relationshipInspected) ids.add(relationshipInspected);
    const choices = completeFitSurfaceChoices(graphState.recipe.nodes, graphState.results,
      [], graphState.states).choices;
    for (const id of ids) {
      const node = graphNode(id), inspected = id === relationshipInspected,
        color = inspected ? '#ffe45c' : '#78e2ff';
      if (node?.operation === 'fit') {
        // Reuse the same complete resolved output as face construction, without
        // replacing the physical participant ID in the relationship declaration.
        const choice = choices.find((choice) =>
            (choice.reference.surface || choice.reference.feature) === id);
        if (choice) paintFaceFootprint(choice.reference, inspected, color, relationshipOverlays);
        else if (node.kind === 'sphere' && graphState.states[id] === 'ready') {
          const fitted = graphState.results[id];
          if (fitted?.parameters) surfaceGuide('sphere', fitted.parameters, null, color,
            fitted.ids, relationshipOverlays);
        }
      } else if (node?.operation === 'reference_plane') {
        const values = graphState.results[id] || referencePlanePreview(node);
        if (values) referencePlaneGuide(values, color, relationshipOverlays);
      }
    }
  }
  updateFaceReviewDisplay();
  draw();
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
  const references = nodes.filter((node) => ['axis', 'reference_plane'].includes(node.operation));
  const validSelected = references.some((node) => node.id === selected) ? selected : '';
  $(id).replaceChildren(
    new Option('None (standalone)', '', false, !validSelected),
    ...references.map(
      (node) =>
        new Option(
          `${node.operation === 'axis' ? 'Axis' : node.operation === 'point' ? 'Point' : 'Plane'} — ${node.label}`,
          node.id,
          false,
          node.id === validSelected,
        ),
    ),
  );
  if (kind === 'sphere') {
    geometryInputChoices(id, 'constrainable_point', nodes, selected);
    $(id).options[0].textContent = 'None (standalone)';
  } else delete $(id).dataset.inputRequirement;
  const hint = $(`${id}-hint`);
  if (hint)
    hint.textContent =
      kind === 'sphere'
        ? 'A point datum supplies the sphere center. A free point is refined by all connected sphere fits; a fit-initialized point remains fixed.'
        : kind === 'plane'
        ? 'An axis makes the fitted plane perpendicular to it. A plane datum fixes its orientation. In either case, observations fit the plane offset.'
        : 'An axis is shared by the fitted surface. Connected fits refine a manually initialized free axis; a fit-initialized axis stays fixed. Choosing a plane datum switches the fit type to Plane.';
}
function updateFitKindForReference(kindId, referenceId, nodes = graphState.recipe.nodes) {
  const operation = graphNode($(referenceId).value)?.operation;
  if (operation === 'reference_plane')
    $(kindId).value = 'plane';
  if (operation === 'point') $(kindId).value = 'sphere';
  if (kindId === 'surface-kind') {
    $('axial-start-row').hidden = $(kindId).value === 'sphere';
    $('axial-end-row').hidden = $(kindId).value === 'sphere';
  }
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
  node.point = reference?.operation === 'point' ? reference.id : null;
  node.reference_plane = reference?.operation === 'reference_plane' ? reference.id : null;
}
function showAxisInitializer(modeId, sourceFieldsId, manualFieldsId, pointFieldsId = null) {
  const mode = $(modeId).value,
    fields = [
      [sourceFieldsId, mode === 'fit'],
      [manualFieldsId, mode === 'free'],
      ...(pointFieldsId ? [[pointFieldsId, mode === 'points']] : []),
    ];
  for (const [id, enabled] of fields) {
    $(id).hidden = !enabled;
    for (const control of $(id).querySelectorAll('input, select'))
      control.disabled = !enabled;
  }
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
    ? 'Contains the axis. Connected fits on a free axis refine its clocking; a mirror joint can also refine it.'
    : perpendicular
      ? 'Normal to the axis. Offset moves it along the axis from the axis point at local Z=0.'
      : 'Parallel to the axis. Connected fits on a free axis refine clocking and offset.';
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
async function replaceRecipe(recipe, autoEvaluate = true, expectedToken = graphState.token) {
  try {
    acceptGraph(await request('/api/graph', { token: expectedToken, recipe }));
    status('Actions updated. Evaluate to refresh dependent results.');
    if (autoEvaluate && $('auto-evaluate').checked) setTimeout(() => void ensureAll(), 0);
    return true;
  } catch (error) {
    acceptGraph(await request('/api/graph'));
    status(error.message, true);
    return false;
  }
}
async function applyFeatureReuse(reuseId, changes, create = false) {
  try {
    const state = await request('/api/graph/feature-reuse/apply', {
      token: graphState.token, reuse_id: reuseId, changes,
      allocation_seed: uid('reuse_ids'), create,
    });
    if (create) selectOnly(state.reuse_authoring.selected_id);
    acceptGraph(state);
    status('Actions updated. Evaluate to refresh dependent results.');
    if ($('auto-evaluate').checked) setTimeout(() => void ensureAll(), 0);
    if (create) $('action-list')
      .querySelector(`[data-action-id="${CSS.escape(selectedFeatureId)}"].action-select`)
      ?.scrollIntoView({ block: 'nearest' });
    return true;
  } catch (error) {
    acceptGraph(await request('/api/graph'));
    status(error.message, true);
    return false;
  }
}
async function appendActions(nodes, autoEvaluate = true, preserveTransformOutput = false) {
  const recipe = structuredClone(graphState.recipe);
  const nextId = nodes.at(-1).id;
  recipe.output = preserveTransformOutput
    ? geometryAppendOutput(recipe.nodes, recipe.output, nextId) : nextId;
  recipe.nodes.push(...nodes);
  selectOnly(nextId);
  const saved = await replaceRecipe(recipe, autoEvaluate);
  if (saved)
    $('action-list')
      .querySelector(`[data-action-id="${CSS.escape(selectedFeatureId)}"].action-select`)
      ?.scrollIntoView({ block: 'nearest' });
  return saved;
}
function acceptGraph(state) {
  const inputDrafts = new Map([...document.querySelectorAll('select[data-input-requirement]')]
    .filter(control => !control.closest('#action-properties')).map(control => [control, control.value]));
  const draftOwner = graphNode(selectedFeatureId),
    preserveProperties = ['axis', 'frame', 'scale'].includes(draftOwner?.operation) &&
      state.recipe.nodes.some(node => node.id === selectedFeatureId && node.operation === draftOwner.operation),
    propertyDraft = preserveProperties ? [...$('action-properties').querySelectorAll('input[id], select[id]')]
      .map(control => ({ id: control.id, value: control.value, checked: control.checked })) : [],
    scaleDraft = preserveProperties && draftOwner.operation === 'scale' ? readScaleDistances('scale-distance-rows') : null;
  if (modelPickMode && graphState && state !== graphState) setModelPickMode(null);
  if ((buildFacesProposal && state.token !== buildFacesProposal.token) ||
      (buildFacesCandidates && state.token !== buildFacesCandidates.token) ||
      (graphState && state.token !== graphState.token && $('build-faces-dialog').open && buildFacesMode === 'guided')) {
    invalidateBuildFaces('Actions changed. Preview again before applying faces.');
    buildFacesCandidates = null;
    buildFacesInspected = null;
    $('build-faces-candidates').replaceChildren();
    $('build-faces-candidate-status').textContent = 'Actions changed. Find candidate neighbors again before previewing.';
  }
  graphState = state;
  buildFacesAcceptedContext = buildFacesAcceptedContext.filter((saved) => saved.token === state.token &&
    state.recipe.nodes.some((node) => node.id === saved.id) && !['ready', 'failed', 'blocked'].includes(state.states[saved.id]));
  const sourceHash = state.recipe.nodes.find((node) => node.operation === 'source')?.source_sha256,
    qualityKey = sourceHash ? `scansor.rmsLimit.${sourceHash}` : null;
  if (qualityKey !== fitQualityStorageKey) {
    fitQualityStorageKey = qualityKey;
    let saved = null;
    try { if (qualityKey) saved = localStorage.getItem(qualityKey); }
    catch { /* Fit quality remains available without preference storage. */ }
    fitQualityLimit = parseRmsLimit(saved);
    $('fit-quality-limit').value = fitQualityLimit === null ? '' : String(fitQualityLimit);
    $('fit-quality-limit').setCustomValidity('');
    $('fit-quality-limit').removeAttribute('aria-invalid');
  }
  selectedFeatureIds = new Set(
    [...selectedFeatureIds].filter((id) => state.recipe.nodes.some((node) => node.id === id)),
  );
  selectedFeatureId = selectedFeatureIds.size === 1 ? [...selectedFeatureIds][0] : null;
  session = Object.fromEntries(
    state.recipe.nodes.filter((n) => n.operation === 'selection').map((n) => [n.id, n.ids]),
  );
  const transformChanged = updateDisplayTransform();
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
  choices(
    'new-point-source',
    fits.filter((n) => n.kind === 'sphere' && isStandaloneFit(n)),
  );
  choices('new-axis-solve-axis', axes);
  choices(
    'new-joint-side',
    fits.filter((n) => ['cone', 'cylinder'].includes(n.kind) && isStandaloneFit(n)),
  );
  choices(
    'new-joint-plane',
    fits.filter((n) => n.kind === 'plane' && isStandaloneFit(n)),
  );
  choices(
    'new-joint-extra',
    fits.filter((n) => ['cone', 'cylinder'].includes(n.kind) && isStandaloneFit(n)),
  );
  renderActions();
  showProperties();
  if (scaleDraft) renderScaleDistances('scale-distance-rows',
    frameGeometry(state.recipe.nodes.slice(0, state.recipe.nodes.findIndex(node => node.id === selectedFeatureId))).points,
    scaleDraft);
  for (const saved of propertyDraft) {
    const control = $(saved.id);
    if (control.dataset.inputRequirement) {
      const consumer = control.dataset.inputConsumer,
        nodes = consumer ? state.recipe.nodes.slice(0, state.recipe.nodes.findIndex(node => node.id === consumer)) : state.recipe.nodes;
      renderInputChoices(control, inputChoices(state, control.dataset.inputRequirement, nodes), saved.value);
    } else { control.value = saved.value; control.checked = saved.checked; }
  }
  if (preserveProperties && draftOwner.operation === 'axis') showAxisInitializer(
    'axis-init-mode', 'axis-source-fields', 'axis-manual-fields', 'axis-point-fields');
  for (const control of document.querySelectorAll('select[data-input-requirement]')) {
    if (control.closest('#action-properties')) continue;
    renderInputChoices(control, inputChoices(state, control.dataset.inputRequirement), inputDrafts.get(control) ?? control.value);
  }
  showResult();
  showReuseVolumes();
  paint();
  if ($('build-faces-dialog').open) {
    refreshBuildFacesChoices();
    paintBuildFacesPreview();
  }
  if ($('relationship-dialog').open) {
    relationshipParticipantIds = new Set([...relationshipParticipantIds].filter((id) => graphNode(id)));
    renderRelationshipBuilder();
  }
  if (transformChanged) home('oblique');
}
function featureTreeLocked() {
  return busy || selectionDrawing || selectionPending || graphState.evaluation_running ||
    featureDeletionPending || relationshipApplying;
}
function focusContextFeature() {
  const target = [...$('action-list').querySelectorAll('.action-select, .managed-owner-summary')]
    .find((element) => element.dataset.actionId === featureContextAnchor);
  (target || $('features-panel')).focus();
}
function openFeatureContextMenu(id, { x, y }) {
  if (featureTreeLocked()) return;
  if (!selectedFeatureIds.has(id)) {
    selectOnly(id);
    refreshFeatureSelection(true);
  }
  featureContextAnchor = id;
  const plan = featureDeletionPlan(graphState.recipe, selectedFeatureIds),
    menu = $('feature-context-menu'), button = $('delete-selected-features');
  button.textContent = `Delete ${selectedFeatureIds.size} selected…`;
  button.disabled = !!plan.error;
  $('feature-context-error').textContent = plan.error || '';
  menu.showPopover();
  const bounds = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(x, innerWidth - bounds.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, innerHeight - bounds.height - 4))}px`;
  (button.disabled ? menu : button).focus();
}
function updateFeatureDeletionButton() {
  const review = featureDeletionReview;
  $('confirm-delete-features').disabled = !review || featureTreeLocked() ||
    review.token !== graphState.token ||
    (!!review.dependents.length && !$('delete-feature-dependents').checked);
}
function reviewFeatureDeletion() {
  if (featureTreeLocked()) return;
  const plan = featureDeletionPlan(graphState.recipe, selectedFeatureIds);
  if (plan.error) { status(plan.error, true); return; }
  featureDeletionReview = { ...plan, token: graphState.token };
  $('feature-context-menu').hidePopover();
  for (const [kind, heading] of [['selected', 'Selected'], ['managed', 'Generated outputs'],
    ['dependents', 'Additional dependents']]) {
    const section = $(`delete-feature-list-${kind}`);
    section.hidden = !plan[kind].length;
    section.querySelector('summary').textContent = `${heading} · ${plan[kind].length}`;
    section.querySelector('ul').replaceChildren(...plan[kind].map((node) => {
      const item = document.createElement('li');
      item.textContent = node.label;
      item.title = node.id;
      return item;
    }));
  }
  $('delete-feature-dependents-row').hidden = !plan.dependents.length;
  $('delete-feature-dependents').checked = false;
  $('delete-feature-dependent-label').textContent =
    `Also delete ${plan.dependents.length} dependent features`;
  $('delete-features-title').textContent = `Delete ${plan.removed.length} features?`;
  $('delete-features-error').textContent = '';
  updateFeatureDeletionButton();
  $('delete-features-dialog').showModal();
}
async function confirmFeatureDeletion() {
  const review = featureDeletionReview;
  updateFeatureDeletionButton();
  if ($('confirm-delete-features').disabled) return;
  featureDeletionPending = true;
  const panels = [document.querySelector('main'), $('creation-toolbar'), $('project-toolbar')],
    previousInert = panels.map((panel) => panel.inert);
  panels.forEach((panel) => { panel.inert = true; });
  $('cancel-delete-features').disabled = true;
  updateFeatureDeletionButton();
  try {
    if (await replaceRecipe(review.recipe, true, review.token)) {
      $('delete-features-dialog').close();
      status(`Deleted ${review.removed.length} features.`);
    } else $('delete-features-error').textContent = $('status').textContent;
  } catch (error) {
    $('delete-features-error').textContent = error.message;
  } finally {
    featureDeletionPending = false;
    panels.forEach((panel, index) => { panel.inert = previousInert[index]; });
    $('cancel-delete-features').disabled = false;
    updateFeatureDeletionButton();
    if (!$('delete-features-dialog').open) focusContextFeature();
  }
}
function currentFitQualities() {
  if (fitQualityCache?.state !== graphState || fitQualityCache?.limit !== fitQualityLimit) {
    fitQualityCache = { state: graphState, limit: fitQualityLimit,
      values: fitQualities(graphState.recipe, graphState.states, graphState.results,
        fitQualityLimit, graphState.errors) };
  }
  return fitQualityCache.values;
}
function renderActions() {
  $('feature-selection-count').textContent = `${selectedFeatureIds.size} selected`;
  $('clear-feature-selection').disabled = !selectedFeatureIds.size;
  updateActionTreeLocks = renderActionTree($('action-list'), {
    scrollContainer: $('features-panel'),
    nodes: graphState.recipe.nodes,
    groups: graphState.recipe.groups || [],
    selected: selectedFeatureIds,
    states: graphState.states,
    errors: graphState.errors,
    locked: () => featureTreeLocked() || !!modelPickMode,
    select: (id, { exclusive = false } = {}) => {
      if (selectionDrawing || selectionPending || featureDeletionPending) return;
      if (pickFeatureInput(id)) return;
      if (exclusive) selectOnly(id);
      else toggleFeatureSelection(id);
      refreshFeatureSelection(true);
    },
    move: (nodes) => replaceRecipe({ ...structuredClone(graphState.recipe), nodes }),
    announce: (message, error = false) => {
      $('action-announcement').textContent = message;
      status(message, error);
    },
    editGroup: openFeatureGroup,
    removeGroup: (groupId) => void removeFeatureGroup(groupId),
    contextMenu: openFeatureContextMenu,
    qualities: currentFitQualities(),
  });
  renderGraphView();
}
function showProperties() {
  const single = selectedFeatureIds.size === 1;
  $('feature-selection-summary').hidden = single;
  $('feature-single-selection').hidden = !single;
  $('selection-tools').hidden = !single;
  $('feature-inspection').hidden = !single;
  if (!single) {
    const selected = graphState.recipe.nodes.filter((node) => selectedFeatureIds.has(node.id));
    $('feature-selection-summary-title').textContent = selected.length
      ? `${selected.length} features selected`
      : 'No features selected';
    $('feature-selection-summary-names').textContent = selected.length
      ? selected.map((node) => node.label).join(' · ')
      : 'Click features to add them to the selection.';
    return;
  }
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
  const presentation = featureTreePresentation(graphState.recipe.nodes, graphState.states, graphState.errors);
  $('feature-state').textContent = actionDescription(
    node, presentation.states[node.id], presentation.errors[node.id],
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
    ['point-properties', node.operation === 'point'],
    ['frame-properties', node.operation === 'frame'],
    ['scale-properties', node.operation === 'scale'],
    ['transform-properties', node.operation === 'transform'],
    ['reference-plane-properties', node.operation === 'reference_plane'],
    ['axis-solve-properties', node.operation === 'axis_solve'],
    ['growth-properties', node.operation === 'growth'],
    ['selection-region-properties', node.operation === 'selection_region'],
    ['region-selection-properties', node.operation === 'region_selection'],
    ['feature-reuse-properties', node.operation === 'feature_reuse'],
    ['reuse-selection-properties', node.operation === 'reuse_selection'],
    ['equal-radii-properties', node.operation === 'equal_radii'],
    ['plane-relationship-properties', node.operation === 'plane_relationship'],
    ['constraint-properties', ['coaxial', 'perpendicular'].includes(node.operation)],
    ['joint-properties', node.operation === 'joint_fit'],
    ['rotation-properties', node.operation === 'rotational_symmetry'],
    ['mirror-properties', node.operation === 'mirror_symmetry'],
    ['parallel-properties', node.operation === 'parallel'],
    ['equal-properties', node.operation === 'equal'],
    ['surface-intersection-properties', node.operation === 'surface_intersection'],
    ['trimmed-face-properties', node.operation === 'trimmed_face'],
    ['arranged-face-properties', node.operation === 'arranged_face'],
    ['build-faces-properties', node.operation === 'build_faces'],
    ['body-properties', node.operation === 'body'],
  ])
    $(id).hidden = !enabled;
  for (const id of ['surface-intersection-properties', 'trimmed-face-properties'])
    for (const select of $(id).querySelectorAll('select')) select.disabled = $(id).hidden;
  for (const input of $('body-properties').querySelectorAll('input'))
    input.disabled = $('body-properties').hidden;
  if (node.operation === 'surface_intersection') {
    populateSurfaceReferences('intersection-first', earlier, node.first);
    populateSurfaceReferences('intersection-second', earlier, node.second);
  } else if (node.operation === 'body') {
    renderBodyFaces('body-face-choices', node.faces);
    $('body-tolerance').value = node.sewing_tolerance;
  } else if (node.operation === 'trimmed_face') {
    populateSurfaceReferences('face-surface', earlier, node.surface);
    renderFaceBoundaries('face', earlier, node.boundaries);
  } else if (node.operation === 'arranged_face') {
    const referenceLabel = (reference) => {
      const feature = graphNode(reference.feature)?.label || reference.feature;
      return reference.surface ? `${feature} → ${graphNode(reference.surface)?.label || reference.surface}`
        : feature;
    };
    $('arranged-face-context').textContent = JSON.stringify({
      surface: referenceLabel(node.surface),
      cutters: node.cutters.map(referenceLabel),
      declared_domains: node.domains.map((id) => graphNode(id)?.label || id),
      selector: node.selector,
    }, null, 2);
  } else if (node.operation === 'build_faces') {
    $('build-faces-inputs').textContent = `${node.surfaces.length} selected surfaces. Review proposals to change inputs or retained regions.`;
    $('review-build-faces').onclick = () => openBuildFaces(node);
  }
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
      node.axis || node.point || node.reference_plane || '',
    );
    $('axial-start').value = node.axial_domain[0];
    $('axial-end').value = node.axial_domain[1];
    $('axial-start-row').hidden = node.kind === 'sphere';
    $('axial-end-row').hidden = node.kind === 'sphere';
  } else if (node.operation === 'axis') {
    $('axis-init-mode').value = node.source_points ? 'points' : node.source_fit ? 'fit' : 'free';
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
    geometryInputChoices('axis-source-point-a', 'point', earlier, node.source_points?.[0]);
    geometryInputChoices('axis-source-point-b', 'point', earlier, node.source_points?.[1]);
    const initial = node.initial_parameters || [0, 0, 0, 0];
    ['axis-point-x', 'axis-point-y', 'axis-direction-x', 'axis-direction-y'].forEach(
      (id, index) => ($(id).value = initial[index]),
    );
    $('axis-direction-reversed').checked = !!node.direction_reversed;
    showAxisInitializer(
      'axis-init-mode',
      'axis-source-fields',
      'axis-manual-fields',
      'axis-point-fields',
    );
  } else if (node.operation === 'point') {
    $('point-init-mode').value = node.source_fit ? 'fit' : 'free';
    choices(
      'point-source-fit',
      earlier.filter(
        (n) => n.operation === 'fit' && n.kind === 'sphere' && isStandaloneFit(n),
      ),
      node.source_fit ? [node.source_fit] : [],
    );
    const initial = node.initial_coordinates || [0, 0, 0];
    ['point-x', 'point-y', 'point-z'].forEach(
      (id, index) => ($(id).value = initial[index]),
    );
    showAxisInitializer('point-init-mode', 'point-source-fields', 'point-manual-fields');
  } else if (node.operation === 'frame') {
    geometryInputChoices('frame-origin', 'point', earlier, node.origin_point);
    geometryInputChoices('frame-primary-reference', 'direction', earlier, node.primary_reference);
    geometryInputChoices('frame-secondary-reference', 'direction', earlier, node.secondary_reference);
    $('frame-primary-output').value = node.primary_output_axis;
    $('frame-secondary-output').value = node.secondary_output_axis;
  } else if (node.operation === 'scale') {
    renderScaleDistances(
      'scale-distance-rows',
      frameGeometry(earlier).points,
      node.distances,
    );
  } else if (node.operation === 'transform') {
    choices(
      'transform-frame',
      earlier.filter((candidate) => candidate.operation === 'frame'),
      [node.frame],
    );
    choices(
      'transform-scale',
      earlier.filter((candidate) => candidate.operation === 'scale'),
      [node.scale],
    );
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
  } else if (node.operation === 'plane_relationship') {
    $('plane-relationship-kind').value = node.relation;
    choices(
      'plane-relationship-surfaces',
      earlier.filter((candidate) => node.surfaces.includes(candidate.id)),
      node.surfaces,
    );
  } else if (['coaxial', 'perpendicular'].includes(node.operation)) {
    choices(
      'constraint-a',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          ['cone', 'cylinder'].includes(n.kind) &&
          isStandaloneFit(n),
      ),
      [node.surface || node.lateral],
    );
    choices(
      'constraint-b',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          isStandaloneFit(n) &&
          (node.operation === 'coaxial'
            ? ['cone', 'cylinder'].includes(n.kind)
            : n.kind === 'plane'),
      ),
      [node.reference || node.plane],
    );
  } else if (node.operation === 'rotational_symmetry') {
    $('rotation-extents').checked = node.symmetric_extents !== false;
    choices(
      'rotation-axis',
      earlier.filter(
        (n) =>
          n.operation === 'fit' &&
          ['cone', 'cylinder'].includes(n.kind) &&
          isStandaloneFit(n),
      ),
      [node.axis],
    );
    node.planes.forEach((id, i) =>
      choices(
        'rotation-input-' + i,
        earlier.filter(
          (n) =>
            n.operation === 'fit' &&
            ['cone', 'cylinder', 'plane'].includes(n.kind) &&
            isStandaloneFit(n),
        ),
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
        earlier.filter(
          (n) =>
            n.operation === 'fit' &&
            ['cone', 'cylinder', 'plane'].includes(n.kind) &&
            isStandaloneFit(n),
        ),
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
        (n) =>
          n.operation === 'fit' &&
          ['cone', 'cylinder'].includes(n.kind) &&
          isStandaloneFit(n) &&
          !used.has(n.id),
      ),
    );
  }
  const ownerId = managedOwnerId(node, graphState.recipe.nodes),
    managed = !!ownerId,
    owner = graphNode(ownerId);
  $('managed-feature-note').hidden = !managed;
  $('action-group-row').hidden = managed;
  if (managed) {
    $('managed-owner-name').textContent = owner?.label || ownerId;
    $('select-managed-owner').onclick = () => {
      selectOnly(ownerId);
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
  $('delete-action').disabled = managed || node.operation === 'source';
  $('delete-action').title = managed
    ? `Managed by ${owner?.label || ownerId}`
    : node.operation === 'source' ? 'The source mesh cannot be deleted.'
      : 'Review deletion of this feature and its dependent features';
  $('fit').textContent = 'Evaluate ' + node.label;
  $('propose-growth').hidden = node.operation !== 'fit' || !isStandaloneFit(node);
  $('use-growth').hidden = node.operation !== 'growth';
}
function axisGuide(axisValues, color = '#ffd166', group = overlays) {
  const axis = new THREE.Vector3(...axisValues.axis_display).normalize();
  const point = new THREE.Vector3(...axisValues.point_display);
  const domain = metadata?.axial_domain || [-2, 5];
  const extent = Math.max(...domain.map(Math.abs), 1),
    endpoints = [-extent, extent].map((distance) =>
      point.clone().addScaledVector(axis, distance),
    );
  group.add(
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
  group.add(
    new THREE.Points(
      new THREE.BufferGeometry().setFromPoints([endpoints[0], point, endpoints[1]]),
      new THREE.PointsMaterial({ color, depthTest: false, size: 7, sizeAttenuation: false }),
    ),
  );
  const arrowLength = Math.min(Math.max(extent * 0.16, 0.45), 2.0),
    arrow = new THREE.Mesh(
      new THREE.ConeGeometry(arrowLength * 0.32, arrowLength, 12),
      new THREE.MeshBasicMaterial({ color, depthTest: false }),
    );
  arrow.position.copy(endpoints[1]);
  arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);
  arrow.renderOrder = 5;
  group.add(arrow);
}
function axisPreview(node) {
  if (node.source_points) {
    const first = pointCoordinates(node.source_points[0]),
      second = pointCoordinates(node.source_points[1]);
    if (!first || !second) return null;
    const point = new THREE.Vector3(...first),
      axis = new THREE.Vector3(...second).sub(point);
    if (axis.lengthSq() <= Number.EPSILON) return null;
    axis.normalize();
    if (node.direction_reversed) axis.negate();
    return { axis_display: axis.toArray(), point_display: point.toArray() };
  }
  let parameters = node.initial_parameters;
  if (!parameters && node.source_fit) parameters = graphState.results[node.source_fit]?.parameters;
  if (!parameters) return null;
  const axis = new THREE.Vector3(parameters[2], parameters[3], 1).normalize();
  if (node.direction_reversed) axis.negate();
  return {
    axis_display: axis.toArray(),
    point_display: [parameters[0], parameters[1], 0],
  };
}
function pointPreview(node) {
  let coordinates = node.initial_coordinates;
  if (!coordinates && node.source_fit)
    coordinates = graphState.results[node.source_fit]?.parameters?.slice(0, 3);
  return coordinates ? { point_display: coordinates } : null;
}
function pointGuide(values, color = '#ffd166', group = overlays) {
  group.add(
    new THREE.Points(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...values.point_display),
      ]),
      new THREE.PointsMaterial({
        color,
        depthTest: false,
        size: 12,
        sizeAttenuation: false,
      }),
    ),
  );
}
function transformGuide(values, group = overlays) {
  const origin = new THREE.Vector3(...values.origin_display),
    extent = Math.max(...(metadata?.axial_domain || [-2, 5]).map(Math.abs), 1) * 0.7;
  for (const [key, color] of [
    ['x_axis_display', '#ff6b6b'],
    ['y_axis_display', '#67dba2'],
    ['z_axis_display', '#72b7ed'],
  ]) {
    const end = origin.clone().addScaledVector(new THREE.Vector3(...values[key]), extent);
    group.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([origin, end]),
        new THREE.LineBasicMaterial({ color, depthTest: false }),
      ),
    );
  }
  pointGuide({ point_display: values.origin_display }, '#ffffff', group);
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
function referencePlaneGuide(values, color = '#ff8fe5', group = overlays) {
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
  group.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(corners),
      new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.9 }),
    ),
  );
}
function surfaceGuide(kind, p, domain, color, ids = [], group = overlays) {
  const line = (points) =>
    group.add(
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
  if (kind === 'sphere') {
    const center = new THREE.Vector3(...p.slice(0, 3)),
      radius = p[3];
    for (const degrees of [-60, -30, 0, 30, 60]) {
      const latitude = (degrees * Math.PI) / 180,
        ringRadius = radius * Math.cos(latitude),
        z = radius * Math.sin(latitude),
        points = [];
      for (let i = 0; i <= 96; i++) {
        const angle = (i * 2 * Math.PI) / 96;
        points.push(
          center
            .clone()
            .add(new THREE.Vector3(
              ringRadius * Math.cos(angle),
              ringRadius * Math.sin(angle),
              z,
            )),
        );
      }
      line(points);
    }
    for (let longitude = 0; longitude < 12; longitude++) {
      const angle = (longitude * Math.PI) / 12,
        points = [];
      for (let i = 0; i <= 96; i++) {
        const polar = (i * 2 * Math.PI) / 96;
        points.push(
          center
            .clone()
            .add(new THREE.Vector3(
              radius * Math.sin(polar) * Math.cos(angle),
              radius * Math.sin(polar) * Math.sin(angle),
              radius * Math.cos(polar),
            )),
        );
      }
      line(points);
    }
    return;
  }
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
function physicalGeometryGuide(node, fitted, color = '#66dbe9', group = overlays, solid = false) {
  if (graphState.states[node.id] !== 'ready') return;
  if (node.operation === 'surface_intersection') {
    for (const path of intersectionPreviewPaths(fitted)) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(path.preview.positions, 3));
      const Line = path.closed ? THREE.LineLoop : THREE.Line;
      group.add(new Line(geometry, new THREE.LineBasicMaterial({ color, depthTest: solid })));
    }
    return;
  }
  if (!validGeometryPreview(fitted.preview) || !fitted.preview.positions.length) return;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(fitted.preview.positions, 3));
  geometry.setIndex(fitted.preview.indices);
  geometry.computeVertexNormals();
  group.add(new THREE.Mesh(geometry, solid
    ? new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide, roughness: 0.75,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })
    : new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide,
      transparent: true, opacity: 0.32, depthWrite: false })));
  if (node.operation === 'body') {
    for (const edge of bodyEdgePaths(fitted)) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(edge.positions, 3));
      group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({
        color: solid ? '#243641' : color, depthTest: solid })));
    }
    return;
  }
  const loopPaths = faceBoundaryPreviewPaths(fitted.bounds, fitted.loops);
  for (const path of loopPaths) {
    const loop = new THREE.BufferGeometry();
    loop.setAttribute('position', new THREE.Float32BufferAttribute(path.preview.positions, 3));
    const Line = path.closed ? THREE.LineLoop : THREE.Line;
    group.add(new Line(loop, new THREE.LineBasicMaterial({ color: solid ? '#243641' : color, depthTest: solid })));
  }
  if (loopPaths.length) return;
  for (const id of fitted.boundary_ids || []) {
    const intersection = graphNode(id);
    const boundary = graphState.results[id];
    if (intersection && boundary) physicalGeometryGuide(intersection, boundary,
      solid ? '#243641' : color, group, solid);
  }
}
function showConstructedFaces() {
  if (!$('faces-only').checked || constructedFacesState === graphState) return;
  constructedFacesState = graphState;
  for (const child of [...constructedFaces.children]) {
    constructedFaces.remove(child);
    child.geometry.dispose();
    child.material.dispose();
  }
  const faces = faceDisplayEntries(graphState);
  const bodies = graphState.recipe.nodes.filter((node) => node.operation === 'body' &&
    graphState.states[node.id] === 'ready' && graphState.results[node.id]?.valid === true);
  const bodyFaces = new Set(bodies.flatMap((node) => node.faces));
  constructedFaces.userData.faceCount = faces.length;
  for (const { node, result } of faces)
    if (!bodyFaces.has(node.id)) physicalGeometryGuide(node, result, '#a8c2cf', constructedFaces, true);
  for (const node of bodies)
    physicalGeometryGuide(node, graphState.results[node.id], '#a8c2cf', constructedFaces, true);
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
    } else if (node.operation === 'point') {
      const values = fitted || pointPreview(node);
      if (values) pointGuide(values, '#f4cf72');
    } else if (node.operation === 'reference_plane') {
      const values = fitted || referencePlanePreview(node);
      if (values) referencePlaneGuide(values);
    } else if (node.operation === 'frame' && fitted) {
      transformGuide(fitted);
    } else if (node.operation === 'fit' && fitted) {
      fitGuide(node, fitted, palette[colorIndex++ % palette.length]);
    } else if (['surface_intersection', 'trimmed_face', 'arranged_face'].includes(node.operation) && fitted &&
               graphState.states[node.id] === 'ready') {
      physicalGeometryGuide(node, fitted, palette[colorIndex++ % palette.length]);
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
        selectOnly(id);
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
  showConstructedFaces();
  clearBodyInspection();
  $('body-diagnostics').hidden = true;
  if (selectedFeatureIds.size !== 1) {
    result = null;
    clearGuides();
    showAvailableGuides();
    $('metrics').replaceChildren();
    return;
  }
  result = graphState.results[selectedFeatureId] || null;
  clearGuides();
  showAvailableGuides();
  $('metrics').replaceChildren();
  if (graphNode(selectedFeatureId)?.operation === 'body')
    showBodyDiagnostics(graphState.diagnostics?.[selectedFeatureId]);
  if (!result) {
    const node = graphNode(selectedFeatureId);
    if (node.operation === 'axis') {
      const preview = axisPreview(node);
      if (preview) axisGuide(preview);
    } else if (node.operation === 'point') {
      const preview = pointPreview(node);
      if (preview) pointGuide(preview);
    } else if (node.operation === 'reference_plane') {
      const preview = referencePlanePreview(node);
      if (preview) referencePlaneGuide(preview);
    } else if (
      [
        'coaxial',
        'perpendicular',
        'rotational_symmetry',
        'equal_radii',
        'plane_relationship',
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
  if (node.operation === 'body') {
    physicalGeometryGuide(node, result);
    values['Solid'] = result.valid === true ? 'Closed and validated' : 'Invalid';
    values['Faces'] = result.face_count;
    values['Volume'] = Number(result.volume).toPrecision(8);
    values['Sewing tolerance'] = node.sewing_tolerance;
  } else if (node.operation === 'surface_intersection') {
    physicalGeometryGuide(node, result);
    const curves = intersectionCurves(result);
    values['Shared curves'] = curves.length;
    values['Curve kinds'] = curves.map((curve) => curve.kind).join(', ') || 'None';
    if (curves.length === 1) {
      const curve = curves[0];
      values['Boundary'] = (curve.closed ?? ['circle', 'ellipse'].includes(curve.kind))
        ? 'Closed analytic boundary' : 'Open analytic boundary';
      if (Number.isFinite(curve.radius)) values['Radius'] = curve.radius.toFixed(5);
      if (Array.isArray(curve.center_display))
        values['Center'] = curve.center_display.map((value) => value.toFixed(5)).join(', ');
    }
    if (result.diagnostics?.length) values['Diagnostics'] = result.diagnostics.join('; ');
  } else if (node.operation === 'build_faces') {
    const faces = [...(result.generated_faces || []), ...(result.reused_faces || [])];
    for (const [index, id] of faces.entries()) {
      const face = graphNode(id), fitted = graphState.results[id];
      if (face && fitted) physicalGeometryGuide(face, fitted, palette[index % palette.length]);
    }
    values['Selected surfaces'] = node.surfaces.length;
    values['Generated faces'] = result.generated_faces?.length || 0;
    values['Reused existing faces'] = result.reused_faces?.length || 0;
    values['Shared intersections'] = (result.generated_intersections?.length || 0) +
      (result.reused_intersections?.length || 0);
    values['Output'] = 'Reviewed faces — not a sewn solid';
  } else if (['trimmed_face', 'arranged_face'].includes(node.operation)) {
    physicalGeometryGuide(node, result);
    values['Surface'] = result.surface_kind;
    if (node.operation === 'arranged_face') {
      values['Arrangement cutters'] = node.cutters.length;
      values['Declared domains'] = node.domains.length;
      values['Reviewed component count'] = node.selector.component_count;
      values['Boundary loops'] = (result.loops || result.bounds?.loops || []).length;
    } else values['Shared boundaries'] = result.boundary_ids.length;
    values['Physical extent'] = result.bounded ? 'Bounded face (not a solid)' : 'Unbounded — preview only';
    for (const [label, value] of physicalBoundsSummary(result.bounds)) values[label] = value;
    values['Preview'] = result.preview_clipped
      ? 'Finite display crop only; not a physical cap' : 'Declared physical bounds';
  } else if (node.operation === 'fit') {
    fitGuide(node, result, '#66dbe9');
    values['Weighted RMS'] = result.weighted_rms.toFixed(5);
    const quality = currentFitQualities()[node.id];
    if (quality?.peak !== undefined) values['Worst residual'] = quality.peak.toPrecision(6);
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
    else if (node.kind === 'sphere') {
      values['Center'] = result.parameters
        .slice(0, 3)
        .map((v) => v.toFixed(5))
        .join(', ');
      values['Diameter'] = (2 * result.parameters[3]).toFixed(5);
    }
    else {
      values['Diameter'] = (2 * result.parameters[4]).toFixed(5);
      values['Half-angle'] = ((Math.atan(result.parameters[6]) * 180) / Math.PI).toFixed(4) + '°';
    }
  } else if (node.operation === 'axis') {
    axisGuide(result);
    values['Constructed by'] = node.source_points
      ? `${graphNode(node.source_points[0]).label} → ${graphNode(node.source_points[1]).label}`
      : node.source_fit
        ? graphNode(node.source_fit).label
        : 'Manual value';
    values['Direction'] = result.axis_display.map((v) => v.toFixed(5)).join(', ');
    values['Direction flipped'] = node.direction_reversed ? 'Yes' : 'No';
  } else if (node.operation === 'point') {
    pointGuide(result);
    values['Initialized by'] = node.source_fit ? graphNode(node.source_fit).label : 'Manual value';
    values['Coordinates'] = result.point_display.map((v) => v.toFixed(5)).join(', ');
  } else if (node.operation === 'frame') {
    transformGuide(result);
    values['Origin'] = result.origin_display.map((v) => v.toFixed(5)).join(', ');
    values['Primary mapping'] = `${graphNode(node.primary_reference).label} → ${node.primary_output_axis}`;
    values['Secondary mapping'] = `${graphNode(node.secondary_reference).label} → ${node.secondary_output_axis}`;
    values['Reference separation'] = `${result.reference_separation_degrees.toFixed(4)}°`;
  } else if (node.operation === 'scale') {
    values['Uniform scale'] = result.scale.toPrecision(9);
    values['Distance observations'] = result.observations.length;
    values['Distance RMS'] = result.weighted_rms.toPrecision(6);
    values['Worst distance residual'] = result.max_abs_residual.toPrecision(6);
  } else if (node.operation === 'transform') {
    transformGuide(result);
    values['Frame'] = graphNode(node.frame).label;
    values['Scale'] = graphNode(node.scale).label;
    values['Uniform scale'] = result.scale.toPrecision(9);
    values['Scale observations'] = result.scale_observation_count;
    values['Scale RMS'] = result.scale_weighted_rms.toPrecision(6);
    values['Origin'] = result.origin_display.map((v) => v.toFixed(5)).join(', ');
    values['Output X'] = result.x_axis_display.map((v) => v.toFixed(5)).join(', ');
    values['Output Y'] = result.y_axis_display.map((v) => v.toFixed(5)).join(', ');
    values['Output Z'] = result.z_axis_display.map((v) => v.toFixed(5)).join(', ');
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
    values['Fits'] = node.surfaces.length;
    Object.entries(result.surfaces).forEach(([id, surface], index) =>
      fitGuide(graphNode(id), surface, palette[index % palette.length]),
    );
  } else if (node.operation === 'plane_relationship') {
    values['Relationship'] = node.relation === 'coincident' ? 'Coincident' : 'Parallel';
    values['Plane fits'] = node.surfaces.length;
    if (Number.isFinite(result.weighted_rms))
      values['Combined RMS'] = result.weighted_rms.toFixed(5);
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
  syncModelPickControls();
  const colors = mesh.geometry.getAttribute('color'),
    gray = new THREE.Color('#8796a2'),
    residualMode = $('colors').value === 'residual',
    selectedNode = graphNode(selectedFeatureId),
    allResiduals = $('all-residuals').checked,
    facesOnly = $('faces-only').checked,
    residualSurfaces = [];
  $('all-residuals').disabled = !residualMode || facesOnly;
  for (const id of ['colors', 'guides', 'all-guides', 'points', 'reuse-volumes'])
    $(id).disabled = facesOnly;
  $('residual-scale').hidden = true;
  if (residualMode && allResiduals) {
    for (const node of graphState.recipe.nodes)
      if (node.operation === 'fit')
        residualSurfaces.push(...resultResidualSurfaces(graphState.results[node.id], node.id));
  } else {
    residualSurfaces.push(...resultResidualSurfaces(result, selectedFeatureId));
  }
  if (residualMode && !allResiduals && selectedNode?.operation === 'feature_reuse') {
    const subtree = managedSubtreeIds(selectedFeatureId, graphState.recipe.nodes);
    for (const node of graphState.recipe.nodes)
      if (subtree.has(node.id) && node.operation === 'fit')
        residualSurfaces.push(...resultResidualSurfaces(graphState.results[node.id], node.id));
  }
  for (let i = 0; i < colors.count; i++) colors.setXYZ(i, gray.r, gray.g, gray.b);
  if (!residualMode) {
    Object.entries(session).forEach(([id, ids], i) => {
      const c = new THREE.Color(palette[i % palette.length]);
      for (const vertex of ids) colors.setXYZ(vertex, c.r, c.g, c.b);
    });
    if (result?.added_ids) {
      const c = new THREE.Color('#4dff91');
      for (const id of result.added_ids) colors.setXYZ(id, c.r, c.g, c.b);
    }
  }
  $('legend').textContent = result?.added_ids
    ? 'Green: proposed additions. Seeds retain selection colors.'
    : 'Colors show raw selections. Inspect an action to see its own fit guides.';
  if (residualMode && residualSurfaces.length) {
    const white = new THREE.Color('#ffffff'),
      blue = new THREE.Color('#245bea'),
      red = new THREE.Color('#e23636'),
      range = residualRange(residualSurfaces),
      limit = range.limit;
    for (const [, s] of residualSurfaces) {
      s.ids.forEach((id, i) => {
        const c = white
          .clone()
          .lerp(s.residuals[i] < 0 ? blue : red, Math.abs(s.residuals[i]) / limit);
        colors.setXYZ(id, c.r, c.g, c.b);
      });
    }
    $('legend').textContent =
      `${allResiduals ? 'All' : 'Selected'} ${residualSurfaces.length} fitted surface${residualSurfaces.length === 1 ? '' : 's'} · ` +
      'blue − / white 0 / red +.';
    $('residual-scale-low').value = `−${limit.toPrecision(4)}`;
    $('residual-scale-high').value = `+${limit.toPrecision(4)}`;
    $('residual-peak-low').value = range.minimum < 0
      ? `−${Math.abs(range.minimum).toPrecision(4)}`
      : 'none';
    $('residual-peak-high').value = range.maximum > 0
      ? `+${range.maximum.toPrecision(4)}`
      : 'none';
    $('residual-scale').setAttribute(
      'aria-label',
      `Residual color scale from minus ${limit.toPrecision(4)} through zero to plus ${limit.toPrecision(4)}. ` +
      `Observed peaks ${range.minimum.toPrecision(4)} and ${range.maximum.toPrecision(4)}.`,
    );
    $('residual-scale').hidden = false;
  } else if (residualMode) {
    $('legend').textContent = allResiduals
      ? 'No evaluated fit residuals are available.'
      : 'No residuals for this action. Select an evaluated fit, relationship, or reuse feature.';
  }
  if ($('reuse-volumes').checked)
    $('legend').textContent += reuseVolumes.userData.volumeCount
      ? ' Translucent overlays show evaluated reuse envelopes; normal-angle filtering still applies.'
      : ' No evaluated reuse envelopes are available.';
  if (facesOnly) {
    $('legend').textContent = `${constructedFaces.userData.faceCount || 0} created faces`;
    $('residual-scale').hidden = true;
  }
  showOverlap();
  const overlap = overlapDiagnostic();
  if (overlap) {
    const color = new THREE.Color('#ff20db');
    for (const id of overlap.ids) colors.setXYZ(id, color.r, color.g, color.b);
  }
  const emphasized = [
    ...new Set(
      [...selectedFeatureIds].flatMap((id) =>
        featureVertexIds(
          graphState.recipe.nodes,
          { ...graphState.memberships, ...session },
          id,
        ),
      ),
    ),
  ];
  const focusColors = focusedPoints.geometry.getAttribute('color');
  const white = new THREE.Color('#ffffff');
  // Unlit markers need their own colors: bright mesh lighting can otherwise
  // make them indistinguishable from the interpolated surface tint.
  const markerColors = selectedPoints.geometry.getAttribute('color');
  for (let id = 0; id < colors.count; id++) {
    const c = new THREE.Color().fromBufferAttribute(colors, id);
    if (!residualMode) c.lerp(white, 0.3);
    markerColors.setXYZ(id, c.r, c.g, c.b);
  }
  markerColors.needsUpdate = true;
  selectedPoints.material.size = residualMode ? 5 : 3;
  for (const id of emphasized) {
    const c = new THREE.Color().fromBufferAttribute(colors, id).lerp(white, 0.6);
    focusColors.setXYZ(id, c.r, c.g, c.b);
  }
  focusColors.needsUpdate = true;
  focusedPoints.geometry.setIndex(emphasized);
  focusedPoints.visible = $('points').checked && !residualMode;
  $('counts').dataset.emphasizedVertices = String(emphasized.length);
  colors.needsUpdate = true;
  selectedPoints.geometry.setIndex(
    residualMode
      ? [...new Set(residualSurfaces.flatMap(([, surface]) => surface.ids))]
      : [
          ...Object.values(session).flat(),
          ...(result?.added_ids || []),
          ...emphasized,
        ],
  );
  selectedPoints.visible = $('points').checked;
  overlays.visible = $('guides').checked;
  updateFaceReviewDisplay();
  $('counts').textContent = activeSelection()
    ? `${session[selectedFeatureId].length.toLocaleString()} selected vertices`
    : '';
  renderer.domElement.style.cursor = !facesOnly && activeSelection() && $('tool').value !== 'orbit'
    ? 'crosshair' : 'default';
  if (!activeSelection()) $('brush-cursor').hidden = true;
  $('fit').disabled =
    busy ||
    selectionDrawing ||
    selectionPending ||
    !selectedNode ||
    [
      'coaxial',
      'perpendicular',
      'rotational_symmetry',
      ...relationshipOperations,
    ].includes(
      selectedNode?.operation,
    );
  $('evaluate-all').disabled = busy || selectionDrawing || selectionPending;
  $('propose-growth').disabled = busy || selectionDrawing || selectionPending;
  $('use-growth').disabled = busy || !graphState.results[selectedFeatureId];
  updateEvaluationControls();
  draw();
}
function updateEvaluationControls() {
  updateActionTreeLocks();
  const selectedNode = graphNode(selectedFeatureId),
    evaluationLocked = busy || graphState.evaluation_running;
  $('action-property-fields').disabled = evaluationLocked;
  $('selection-edit-fields').disabled = evaluationLocked;
  $('choose-example').disabled = featureTreeLocked();
  $('delete-action').disabled = evaluationLocked || !selectedNode ||
    selectedNode.operation === 'source' || !!managedOwnerId(selectedNode, graphState.recipe.nodes);
}
async function fit() {
  return evaluateGraph(false, selectedFeatureId);
}
async function evaluateAll() {
  return evaluateGraph(true);
}
async function ensureAll() {
  return evaluateGraph(true, null, false);
}
async function evaluateGraph(allActions, target = null, retry = true) {
  while (activeGraphEvaluation) await activeGraphEvaluation;
  if (busy) return;
  activeGraphEvaluation = runGraphEvaluation(allActions, target, retry);
  try { await activeGraphEvaluation; }
  finally { activeGraphEvaluation = null; }
}
async function ensureCurrent(targets, { token = graphState.token, isCurrent } = {}) {
  return ensureGraphCurrent({ targets, token, request, acceptState: acceptGraph,
    getState: () => graphState, acceptEvaluationStatus, isCurrent });
}
function acceptEvaluationStatus(state) {
  if (graphState.token !== state.token || graphState.revision !== state.revision) return;
  const runningChanged = graphState.evaluation_running !== state.evaluation_running;
  // Preserve snapshot identity so lifecycle-only changes retain geometry caches.
  graphState.evaluation_running = state.evaluation_running;
  graphState.evaluation_error = state.evaluation_error;
  if (runningChanged) {
    updateEvaluationControls();
    syncModelPickControls();
  }
}
async function runGraphEvaluation(allActions, target, retry) {
  busy = true;
  const toolbars = workspaceToolbars(), previousInert = toolbars.map(panel => panel.inert);
  toolbars.forEach(panel => { panel.inert = true; });
  renderActions();
  paint();
  status(allActions ? 'Evaluating all actions…' : 'Evaluating action and earlier inputs…');
  try {
    const token = graphState.token;
    if (retry) {
      // Evaluate buttons are the explicit retry path; Auto and output consumers
      // only ensure current work, preserving failures until inputs change.
      await waitForGraphEvaluation({ token, request, acceptState: acceptGraph,
        getState: () => graphState, acceptEvaluationStatus });
      await request('/api/graph/evaluate', {
        token, ...(allActions ? { all_actions: true } : { target }),
      });
      await waitForGraphEvaluation({ token, request, acceptState: acceptGraph,
        getState: () => graphState, acceptEvaluationStatus });
    }
    const state = await ensureCurrent(allActions ? graphState.recipe.nodes.map((node) => node.id) : [target]);
    const failed = Object.values(state.states).filter((value) => value === 'failed').length,
      blocked = Object.values(state.states).filter((value) => value === 'blocked').length;
    if (failed || blocked) {
      status(`Evaluation finished: ${failed} failed · ${blocked} blocked. Inspect marked features for details.` +
        (state.evaluation_error ? ` ${state.evaluation_error}` : ''), true);
      return;
    }
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
    toolbars.forEach((panel, index) => { panel.inert = previousInert[index]; });
    renderActions();
    paint();
    if ($('relationship-dialog').open) updateRelationshipSelection();
  }
}
function home(direction = null) {
  mesh.geometry.computeBoundingSphere();
  mesh.updateMatrixWorld(true);
  const sphere = mesh.geometry.boundingSphere.clone().applyMatrix4(mesh.matrixWorld);
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
function focusFaceTarget() {
  const result = graphState.results[buildFacesTarget?.feature];
  const target = buildFacesTarget?.surface ? result?.surfaces?.[buildFacesTarget.surface] : result;
  if (!target?.ids?.length) return;
  modelRoot.updateMatrixWorld(true);
  camera.updateMatrixWorld(true);
  const box = new THREE.Box3(), point = new THREE.Vector3();
  for (const id of target.ids) box.expandByPoint(point.fromArray(positions, id * 3).applyMatrix4(modelRoot.matrixWorld));
  for (const face of guidedProposalFaces(buildFacesProposal, buildFacesTarget)) {
    const selected = new Set(buildFacesReview.get(face.key)?.region_keys || []);
    for (const region of face.regions.filter((region) => selected.has(region.key) && region.bounded && validBuildFaceRegion(region))) {
      for (let offset = 0; offset < region.preview.positions.length; offset += 3)
        box.expandByPoint(point.fromArray(region.preview.positions, offset).applyMatrix4(modelRoot.matrixWorld));
    }
  }
  focusGeometryBox(box, $('build-faces-dialog'));
}
function focusRelationshipParticipants(ids) {
  const box = new THREE.Box3(), point = new THREE.Vector3();
  modelRoot.updateMatrixWorld(true);
  for (const id of ids) {
    const node = graphNode(id), fitted = graphState.results[id];
    if (node?.operation === 'fit') {
      for (const index of fitted?.ids || [])
        box.expandByPoint(point.fromArray(positions, index * 3).applyMatrix4(modelRoot.matrixWorld));
    } else if (node?.operation === 'reference_plane') {
      const values = fitted || referencePlanePreview(node);
      if (!values) continue;
      const group = new THREE.Group();
      referencePlaneGuide(values, '#78e2ff', group);
      group.applyMatrix4(modelRoot.matrixWorld);
      box.union(new THREE.Box3().setFromObject(group));
      for (const child of group.children) { child.geometry.dispose(); child.material.dispose(); }
    }
  }
  focusGeometryBox(box, $('relationship-dialog'));
}
function focusGeometryBox(box, panelElement) {
  if (box.isEmpty()) return;
  revealWorkspacePanel('view');
  resizeViewport?.();
  camera.updateMatrixWorld(true);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const canvas = renderer.domElement.getBoundingClientRect();
  if (!canvas.width || !canvas.height) return;
  const panel = panelElement.getBoundingClientRect();
  const visible = unobscuredViewport(canvas, panel);
  const tangent = Math.tan(camera.fov * Math.PI / 360);
  const halfAngle = Math.min(
    Math.atan(tangent * visible.height / canvas.height),
    Math.atan(tangent * camera.aspect * visible.width / canvas.width));
  const radius = Math.max(sphere.radius, 0.001);
  const distance = radius / Math.sin(halfAngle) * 1.18;
  const direction = camera.getWorldDirection(new THREE.Vector3()).negate();
  const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
  const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
  controls.target.copy(sphere.center).addScaledVector(right,
    distance * tangent * camera.aspect * (1 - (visible.left + visible.right - 2 * canvas.left) / canvas.width));
  controls.target.addScaledVector(up,
    distance * tangent * ((visible.top + visible.bottom - 2 * canvas.top) / canvas.height - 1));
  camera.position.copy(controls.target).addScaledVector(direction, distance);
  camera.near = Math.max(0.0001, radius / 1000);
  camera.far = Math.max(radius * 1000, distance + radius * 4);
  camera.updateProjectionMatrix();
  controls.update();
  draw();
}
async function start() {
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
  renderer.domElement.setAttribute('aria-label', 'Model 3D view');
  scene = new THREE.Scene();
  scene.background = new THREE.Color('#17232e');
  modelRoot = new THREE.Group();
  modelRoot.matrixAutoUpdate = false;
  scene.add(modelRoot);
  camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
  camera.up.set(0, 0, 1);
  const raycaster = new THREE.Raycaster();
  controls = onshapeNavigation(camera, renderer.domElement, draw, (event, fallback) => {
    if (!mesh) return null;
    const bounds = renderer.domElement.getBoundingClientRect();
    camera.updateMatrixWorld();
    modelRoot.updateMatrixWorld(true);
    raycaster.near = camera.near;
    raycaster.far = camera.far;
    raycaster.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        1 - ((event.clientY - bounds.top) / bounds.height) * 2,
      ),
      camera,
    );
    const hit = $('faces-only').checked
      ? raycaster.intersectObjects(constructedFaces.children.filter((child) => child.isMesh), false)[0]
      : raycaster.intersectObject(mesh, false)[0];
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
  modelRoot.add(mesh);
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
  modelRoot.add(selectedPoints);
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
  modelRoot.add(focusedPoints);
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
  modelRoot.add(overlapHalo, overlapMarkers);
  scene.add(new THREE.HemisphereLight('#ffffff', '#738396', 2));
  const light = new THREE.DirectionalLight('#ffffff', 2.5);
  light.position.set(15, -20, 30);
  scene.add(light);
  overlays = new THREE.Group();
  modelRoot.add(overlays);
  bodyInspection = new THREE.Group();
  modelRoot.add(bodyInspection);
  modelPickGuides = new THREE.Group();
  modelRoot.add(modelPickGuides);
  modelPickSourceFaces = new THREE.Group();
  modelRoot.add(modelPickSourceFaces);
  constructedFaces = new THREE.Group();
  constructedFaces.visible = false;
  modelRoot.add(constructedFaces);
  reuseVolumes = new THREE.Group();
  reuseVolumes.userData.volumeCount = 0;
  modelRoot.add(reuseVolumes);
  buildFacesOverlays = new THREE.Group();
  modelRoot.add(buildFacesOverlays);
  relationshipOverlays = new THREE.Group();
  modelRoot.add(relationshipOverlays);
  buildFacesContext = new THREE.Group();
  buildFacesContext.visible = false;
  modelRoot.add(buildFacesContext);
  const resize = () => {
    if (viewport.hidden || !viewport.clientWidth || !viewport.clientHeight) return;
    renderer.setSize(viewport.clientWidth, viewport.clientHeight);
    camera.aspect = viewport.clientWidth / viewport.clientHeight;
    camera.updateProjectionMatrix();
    controls.resize();
    draw();
  };
  resizeViewport = resize;
  new ResizeObserver(resize).observe(viewport);
  resize();
  home('oblique');
  $('mesh-info').textContent =
    `${metadata.vertices.toLocaleString()} vertices · ${metadata.triangles.toLocaleString()} triangles`;
  $('tool').onchange = paint;
  $('fit').onclick = fit;
  $('evaluate-all').onclick = evaluateAll;
  $('auto-evaluate').onchange = () => {
    if ($('auto-evaluate').checked) void ensureAll();
  };
  $('clear-feature-selection').onclick = () => {
    selectOnly(null);
    refreshFeatureSelection();
  };
  $('fit-quality-limit').oninput = () => {
    const input = $('fit-quality-limit');
    fitQualityLimit = parseRmsLimit(input.value);
    const invalid = input.validity.badInput || (input.value !== '' && fitQualityLimit === null);
    input.setCustomValidity(invalid ? 'Enter a positive finite RMS limit, or leave it blank.' : '');
    input.setAttribute('aria-invalid', String(invalid));
    try {
      if (fitQualityStorageKey && !invalid) {
        if (fitQualityLimit === null) localStorage.removeItem(fitQualityStorageKey);
        else localStorage.setItem(fitQualityStorageKey, String(fitQualityLimit));
      }
    } catch { /* A session-only threshold is still useful. */ }
    renderActions();
  };
  $('action-list').onclick = (event) => {
    if (event.target !== $('action-list')) return;
    selectOnly(null);
    refreshFeatureSelection();
  };
  $('features-panel').onclick = (event) => {
    if (event.target !== $('features-panel')) return;
    selectOnly(null);
    refreshFeatureSelection();
  };
  $('new-relationship').onclick = () => {
    if (relationshipApplying) return;
    if ($('build-faces-dialog').open) {
      if (!canDiscardFaceReview()) return;
      $('build-faces-dialog').close();
    }
    relationshipParticipantIds = new Set(relationshipParticipantChoices(graphState.recipe.nodes, null)
      .filter(({ node }) => selectedFeatureIds.has(node.id)).map(({ node }) => node.id));
    selectedRelationshipKind = null;
    const participants = graphState.recipe.nodes.filter((node) => relationshipParticipantIds.has(node.id)),
      valid = relationshipDefinitions.filter((definition) => relationshipValidity(definition.id, participants).valid);
    if (valid.length === 1) selectedRelationshipKind = valid[0].id;
    relationshipInspected = null;
    $('relationship-filter').value = '';
    $('new-relationship-label').value = nextFeatureLabel(
      relationshipDefinitions.find((definition) => definition.id === selectedRelationshipKind)?.baseName || 'Relationship');
    $('relationship-error').textContent = '';
    $('relationship-dialog').show();
    revealWorkspacePanel('relationship-dialog');
    renderRelationshipBuilder();
    $('relationship-kind').focus();
  };
  $('relationship-kind').onchange = () => {
    selectedRelationshipKind = $('relationship-kind').value || null;
    const definition = relationshipDefinitions.find((entry) => entry.id === selectedRelationshipKind);
    if (definition) $('new-relationship-label').value = nextFeatureLabel(definition.baseName);
    relationshipInspected = null;
    $('relationship-error').textContent = '';
    renderRelationshipBuilder();
  };
  $('relationship-filter').oninput = filterRelationshipParticipants;
  $('clear-relationship-participants').onclick = () => {
    if (featureTreeLocked() || relationshipApplying) return;
    relationshipParticipantIds.clear();
    relationshipInspected = null;
    renderRelationshipBuilder();
  };
  $('focus-relationship').onclick = () => focusRelationshipParticipants([...relationshipParticipantIds]);
  initializeSpecializedPickFields();
  $('stop-model-picking').onclick = () => setModelPickMode(null);
  bindModelPickControls(document, startFieldPick);
  document.addEventListener('selection-picker-change', event => {
    if (modelPickField?.control === event.target && !event.target.multiple && event.target.matches('select'))
      setModelPickMode(null);
  });
  for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('close', () => {
    if (modelPickField?.dialog === dialog && !dialog.open) {
      modelPickField.suspended = false;
      delete dialog.dataset.modelPickSuspended;
      setModelPickMode(null);
    }
  });
  $('model-pick-choices').addEventListener('toggle', event => {
    if (event.newState === 'closed') clearModelPickHover();
  });
  $('relationship-dialog').addEventListener('close', () => {
    setModelPickMode(null);
    relationshipInspected = null;
    paintRelationshipPreview();
  });
  $('relationship-dialog').addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    if (modelPickMode) { setModelPickMode(null); return; }
    requestWorkspaceClose('relationship-dialog');
  });
  $('new-build-faces').onclick = () => {
    const node = graphNode(selectedFeatureId);
    openBuildFaces(node?.operation === 'build_faces' ? node : null);
  };
  $('build-faces-surfaces').onchange = () => {
    buildFacesBatchDraft = chosen('build-faces-surfaces').map((value) => JSON.parse(value));
    invalidateBuildFaces('Surface inputs changed. Preview again.');
    renderBuildFacesScopes();
  };
  $('build-faces-mode').onchange = () => {
    if (!canDiscardFaceReview()) { $('build-faces-mode').value = buildFacesMode; return; }
    buildFacesMode = $('build-faces-mode').value;
    setModelPickMode(null);
    // A different construction mode is a new owner, not permission to delete
    // a previously approved owner's other faces.
    buildFacesOwnerId = null;
    $('build-faces-label').value = nextFeatureLabel('Build faces');
    invalidateBuildFaces('Review mode changed. Preview again.');
    buildFacesCandidates = null;
    buildFacesInspected = null;
    setBuildFacesModeDisplay();
    renderBuildFacesScopes();
    if (buildFacesMode === 'guided') void loadFaceCandidates();
  };
  $('refresh-face-candidates').onclick = () => {
    if (canDiscardFaceReview()) void loadFaceCandidates(buildFacesCutters, buildFacesBoundarySources, true);
  };
  $('build-faces-neighbor-filter').oninput = filterFaceCandidates;
  $('build-faces-label').oninput = () => renderFaceDialogClose($('close-build-faces'), false);
  $('build-faces-neighbor-filter').onkeydown = (event) => {
    if (event.key === 'Enter') event.preventDefault();
  };
  $('focus-face-target').onclick = focusFaceTarget;
  $('add-all-fitted-surfaces').onclick = () => {
    const select = $('build-faces-surfaces'),
      choices = [...select.options].map((option) => ({ reference: JSON.parse(option.value) })),
      { references } = addFittedSurfaceReferences(
        graphState.recipe.nodes, choices, buildFacesSelectedSurfaces());
    for (const option of select.options)
      option.selected = references.some((reference) => sameSurfaceReference(reference, JSON.parse(option.value)));
    buildFacesBatchDraft = references;
    invalidateBuildFaces(unavailableFitsMessage(buildFacesGeometry().unavailable));
    renderBuildFacesScopes();
  };
  $('build-faces-dialog').addEventListener('workspace-before-close', event => {
    if (buildFacesApplying || !canDiscardFaceReview()) event.preventDefault();
  });
  $('build-faces-dialog').addEventListener('keydown', event => {
    if (event.key !== 'Escape' || $('build-faces-target-options').matches(':popover-open')) return;
    event.preventDefault();
    event.stopPropagation();
    if (modelPickMode) { setModelPickMode(null); return; }
    requestWorkspaceClose('build-faces-dialog');
  });
  $('relationship-dialog').addEventListener('workspace-before-close', event => {
    if (relationshipApplying) event.preventDefault();
  });
  for (const event of ['close', 'cancel']) $('build-faces-dialog').addEventListener(event, () => {
    setModelPickMode(null);
    buildFacesContinuePreview = null;
    buildFacesContinueInteraction = { pointer: null, focus: null };
    $('build-faces-target-options').hidePopover();
    invalidateBuildFaces();
  });
  window.addEventListener('resize', () => $('build-faces-target-options').hidePopover());
  $('build-faces-dialog').querySelector('.face-panel-body').addEventListener('scroll', () =>
    $('build-faces-target-options').hidePopover());
  $('preview-build-faces').onclick = async () => {
    invalidateBuildFaces('', true);
    const unavailableInputs = unavailableSavedFaceInputsMessage();
    if (unavailableInputs) {
      $('build-faces-error').textContent = unavailableInputs;
      return;
    }
    const surfaces = buildFacesSelectedSurfaces();
    if (buildFacesMode === 'guided' && !buildFacesCandidates) {
      $('build-faces-error').textContent = 'Find candidate neighbors before previewing.';
      return;
    }
    if (buildFacesMode === 'guided' && unavailableGuidedSources(buildFacesBoundarySources,
      buildFacesCandidates.candidates, buildFacesCutters).length) {
      $('build-faces-error').textContent = 'Restore or explicitly discard unavailable approved-edge guidance before previewing.';
      return;
    }
    if (surfaces.length < 2) {
      $('build-faces-error').textContent = 'Choose at least two adjoining surfaces.';
      return;
    }
    const scopeError = buildFacesScopeError(surfaces);
    if (scopeError) {
      $('build-faces-error').textContent = scopeError;
      return;
    }
    const token = graphState.token, requestId = buildFacesRequest;
    $('preview-build-faces').disabled = true;
    try {
      const scopes = buildFaceScopes(buildFacesScopeDraft, surfaces, graphState.recipe.nodes, buildFacesOwnerId);
      await ensureCurrent([
        ...surfaces.flatMap((surface) => [surface.feature, surface.surface]),
        ...buildFacesBoundarySources,
        ...scopes.flatMap((scope) => scope.faces),
      ], { token, isCurrent: () => requestId === buildFacesRequest && $('build-faces-dialog').open });
      const proposal = await request('/api/graph/build-faces/preview', {
        token, owner_id: buildFacesOwnerId, surfaces,
        target: buildFacesMode === 'guided' ? buildFacesTarget : null,
        adjacencies: guidedAdjacencies(),
        boundary_sources: buildFacesMode === 'guided' ? buildFacesBoundarySources : undefined,
        face_scopes: scopes,
      });
      if (requestId !== buildFacesRequest || !$('build-faces-dialog').open) return;
      if (token !== graphState.token) {
        invalidateBuildFaces('Actions changed. Preview again before applying faces.');
        return;
      }
      buildFacesProposal = proposal;
      buildFacesScopeDraft = [
        ...buildFacesScopeDraft.filter((scope) => !surfaces.some((surface) => sameSurfaceReference(scope.surface, surface))),
        ...(proposal.face_scopes || []),
      ];
      renderBuildFacesScopes();
      renderBuildFacesReview();
      if (buildFacesMode === 'guided') $('build-faces-neighbors').open = false;
      $('build-faces-dialog').querySelector('.face-panel-body').scrollTop = 0;
    } catch (error) {
      if (requestId === buildFacesRequest) $('build-faces-error').textContent = error.message;
    } finally { $('preview-build-faces').disabled = false; }
  };
  $('build-faces-form').onsubmit = async (event) => {
    event.preventDefault();
    if (!buildFacesProposal || buildFacesApplying) return;
    const choices = buildFaceChoices(buildFacesProposal, buildFacesReview);
    if (!choices.length) return;
    buildFacesApplying = true;
    $('build-faces-target-options').hidePopover();
    const selectedRegions = buildFacesProposal.faces.flatMap((face) => face.regions.filter((region) =>
      choices.some((choice) => sameSurfaceReference(choice.surface, face.surface) && choice.region_key === region.key)));
    const boundaryKeys = new Set(selectedRegions.flatMap((region) => region.boundary_keys || []));
    const boundaryIntersections = new Set(selectedRegions.flatMap((region) =>
      (region.boundaries || []).map((boundary) => boundary.intersection_key)));
    const actualNeighbors = buildFacesProposal.intersections.filter((edge) => boundaryIntersections.has(edge.key))
      .flatMap((edge) => [edge.first, edge.second]);
    const nextNeighbors = buildFacesMode === 'guided' ? (buildFacesCandidates?.candidates || [])
      .filter((candidate) => boundaryKeys.has(candidate.key) ||
        actualNeighbors.some((ref) => sameSurfaceReference(ref, candidate.reference))) : [];
    const disabledControls = [...$('build-faces-form').querySelectorAll('input, select, button')]
      .map((control) => ({ control, disabled: control.disabled }));
    for (const { control } of disabledControls) control.disabled = true;
    try {
      const state = await request('/api/graph/build-faces/apply', {
        token: buildFacesProposal.token, proposal_token: buildFacesProposal.proposal_token,
        owner_id: buildFacesOwnerId, label: $('build-faces-label').value.trim(),
        surfaces: buildFacesSelectedSurfaces(), choices,
        target: buildFacesMode === 'guided' ? buildFacesTarget : null,
        adjacencies: guidedAdjacencies(),
        boundary_sources: buildFacesMode === 'guided' ? buildFacesBoundarySources : undefined,
        face_scopes: buildFaceScopes(buildFacesScopeDraft, buildFacesSelectedSurfaces(), graphState.recipe.nodes, buildFacesOwnerId),
      });
      const ownerId = buildFacesOwnerId || state.recipe.nodes.find((node) =>
        node.operation === 'build_faces' && !graphState.recipe.nodes.some((before) => before.id === node.id))?.id;
      if (ownerId) selectOnly(ownerId);
      buildFacesAcceptedContext = appliedFaceContext(state, ownerId, buildFacesProposal, choices);
      acceptGraph(state);
      if (buildFacesMode === 'guided') {
        buildFacesOwnerId = ownerId;
        const remainingNeighbors = unbuiltFaceNeighbors(nextNeighbors, state.recipe.nodes);
        $('build-faces-next-buttons').replaceChildren(...remainingNeighbors.map((candidate) => {
          const button = document.createElement('button');
          button.type = 'button';
          const presentation = fittedFaceCandidatePresentation(candidate);
          button.append(featureIcon(presentation.icon, 'action-type'), document.createTextNode(presentation.name));
          button.title = presentation.label;
          bindFaceContinuationPreview(button, candidate.reference, (reference, active, interaction) => {
            buildFacesContinueInteraction = faceContinuationPreview(buildFacesContinueInteraction, reference, active, interaction);
            buildFacesContinuePreview = buildFacesContinueInteraction.pointer || buildFacesContinueInteraction.focus;
            paintBuildFacesPreview();
          });
          button.onclick = async () => {
            if (faceContinuationPending || buildFacesApplying || (busy && !activeGraphEvaluation)) return;
            if (graphState.token !== state.token) {
              $('build-faces-error').textContent = 'Actions changed. Review the current faces again.';
              return;
            }
            faceContinuationPending = true;
            button.disabled = true;
            status('Preparing the next face…');
            const requestId = buildFacesRequest, appliedOwnerId = buildFacesOwnerId;
            try {
              await prepareFaceContinuation({
                target: candidate.reference, appliedOwnerId, getState: () => graphState,
                ensureCurrent,
                isCurrent: () => $('build-faces-dialog').open && requestId === buildFacesRequest,
              });
              if (!$('build-faces-dialog').open || requestId !== buildFacesRequest) return;
              const owner = graphState.recipe.nodes.find((node) => node.operation === 'build_faces' &&
                node.target && sameSurfaceReference(node.target, candidate.reference));
              openBuildFaces(owner || null, candidate.reference);
            } catch (error) {
              if ($('build-faces-dialog').open && requestId === buildFacesRequest)
                $('build-faces-error').textContent = error.message;
            } finally {
              faceContinuationPending = false;
              button.disabled = false;
            }
          };
          return button;
        }));
        $('build-faces-next-neighbors').hidden = !remainingNeighbors.length;
        $('build-faces-error').textContent = '';
        renderFaceDialogClose($('close-build-faces'), true);
      } else $('build-faces-dialog').close();
      status('Reviewed faces applied. Existing manual geometry retained.');
      if ($('auto-evaluate').checked) setTimeout(() => void ensureAll(), 0);
    } catch (error) {
      const message = error.message;
      try {
        acceptGraph(await request('/api/graph'));
        $('build-faces-error').textContent = message;
        if (buildFacesProposal) paintBuildFacesPreview();
      } catch (recoveryError) {
        invalidateBuildFaces(`${message} Unable to refresh actions: ${recoveryError.message}. ` +
          'Preview again once the server is reachable; check current actions before retrying Apply.');
      }
    } finally {
      buildFacesApplying = false;
      for (const { control, disabled } of disabledControls) control.disabled = disabled;
      if ($('build-faces-dialog').open) refreshBuildFacesChoices();
      $('preview-build-faces').disabled = false;
      $('apply-build-faces').disabled = !buildFacesProposal ||
        !buildFaceChoices(buildFacesProposal, buildFacesReview).length;
    }
  };
  $('select-suggested-face-regions').onclick = () => {
    if (!buildFacesProposal || buildFacesApplying) return;
    const faces = buildFacesMode === 'guided'
      ? guidedProposalFaces(buildFacesProposal, buildFacesTarget) : buildFacesProposal.faces;
    for (const face of faces) changeFaceRegionSelection(face, 'suggested');
    renderBuildFacesReview(true);
  };
  $('clear-face-regions').onclick = () => {
    if (!buildFacesProposal || buildFacesApplying) return;
    const faces = buildFacesMode === 'guided'
      ? guidedProposalFaces(buildFacesProposal, buildFacesTarget) : buildFacesProposal.faces;
    for (const face of faces) changeFaceRegionSelection(face, 'clear');
    renderBuildFacesReview(true);
  };
  $('new-surface-intersection').onclick = () => {
    const options = surfaceReferenceChoices(graphState.recipe.nodes, graphState.results);
    const selected = options.filter((choice) => selectedFeatureIds.has(choice.reference.feature));
    populateSurfaceReferences('new-intersection-first', graphState.recipe.nodes, selected[0]?.reference);
    populateSurfaceReferences('new-intersection-second', graphState.recipe.nodes, selected[1]?.reference);
    $('surface-intersection-error').textContent = '';
    showCreateDialog('surface-intersection-dialog', 'new-surface-intersection-label', 'Surface intersection');
  };
  $('new-trimmed-face').onclick = () => {
    const node = graphNode(selectedFeatureId);
    const selected = node?.operation === 'surface_intersection' ? node.first
      : surfaceReferenceChoices(graphState.recipe.nodes, graphState.results)
        .find((choice) => choice.reference.feature === selectedFeatureId)?.reference;
    populateSurfaceReferences('new-face-surface', graphState.recipe.nodes, selected);
    renderFaceBoundaries('new-face', graphState.recipe.nodes,
      node?.operation === 'surface_intersection'
        ? [{ intersection: node.id, keep: 'inside' }] : []);
    $('trimmed-face-error').textContent = '';
    showCreateDialog('trimmed-face-dialog', 'new-trimmed-face-label', 'Trimmed face');
  };
  $('new-body').onclick = () => {
    $('new-body-label').value = nextFeatureLabel('Body');
    $('new-body-tolerance').value = initialBodyTolerance(positions);
    $('body-error').textContent = '';
    const selected = bodyFaceSelection(graphState, selectedFeatureIds);
    renderBodyFaces('new-body-face-choices', selected.length ? selected : bodyFaceSelection(graphState));
    if (!$('body-dialog').open) $('body-dialog').show();
    $('new-body-label').focus();
    $('new-body-label').select();
  };
  $('body-add-all').onclick = () => renderBodyFaces('new-body-face-choices', bodyFaceSelection(graphState));
  $('body-add-selected').onclick = () => renderBodyFaces('new-body-face-choices',
    [...new Set([...bodyCheckedFaces('new-body-face-choices'), ...bodyFaceSelection(graphState, selectedFeatureIds)])]);
  $('body-clear').onclick = () => renderBodyFaces('new-body-face-choices', []);
  $('body-dialog').addEventListener('close', () => { clearBodyInspection(); showResult(); draw(); });
  $('add-body-form').onsubmit = async (event) => {
    event.preventDefault();
    try {
      const inputs = bodyInputs(bodyCheckedFaces('new-body-face-choices'), $('new-body-tolerance').value);
      $('create-body').disabled = true;
      const node = { id: uid('body'), label: submittedFeatureLabel('new-body-label'), operation: 'body', ...inputs };
      if (await appendActions([node], true, true)) $('body-dialog').close();
      else $('body-error').textContent = $('status').textContent;
    } catch (error) { $('body-error').textContent = error.message; }
    finally { $('create-body').disabled = false; }
  };
  for (const prefix of ['face', 'new-face']) {
    $(`${prefix}-surface`).onchange = () => renderFaceBoundaries(prefix, faceEditorNodes(prefix));
    $(prefix === 'face' ? 'add-face-boundary' : 'add-new-face-boundary').onclick = () => addFaceBoundary(prefix);
  }
  $('add-surface-intersection-form').onsubmit = async (event) => {
    event.preventDefault();
    try {
      const node = { id: uid('intersection'), label: submittedFeatureLabel('new-surface-intersection-label'),
        operation: 'surface_intersection', first: readSurfaceReference('new-intersection-first'),
        second: readSurfaceReference('new-intersection-second') };
      if (await appendActions([node], true, true)) $('surface-intersection-dialog').close();
      else $('surface-intersection-error').textContent = $('status').textContent;
    } catch (error) { $('surface-intersection-error').textContent = error.message; }
  };
  $('add-trimmed-face-form').onsubmit = async (event) => {
    event.preventDefault();
    try {
      const node = { id: uid('face'), label: submittedFeatureLabel('new-trimmed-face-label'),
        operation: 'trimmed_face', surface: readSurfaceReference('new-face-surface'),
        boundaries: readFaceBoundaries('new-face') };
      if (await appendActions([node], true, true)) $('trimmed-face-dialog').close();
      else $('trimmed-face-error').textContent = $('status').textContent;
    } catch (error) { $('trimmed-face-error').textContent = error.message; }
  };
  $('inspect-overlap').onclick = () => {
    selectOnly(activeOverlap);
    renderActions();
    showProperties();
    showResult();
    paint();
    $('overlap-details').scrollIntoView({ block: 'nearest' });
  };
  const updateExportPlanes = () => {
    const nodes = graphState.recipe.nodes;
    const explicitTransform = $('export-transform').value;
    const collection = $('export-scope').value !== 'target';
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
    $('export-legacy-frame').hidden = collection || !!explicitTransform;
    select.disabled = collection || !!explicitTransform || !$('export-axis-up').checked;
  };
  const updateExportScope = () => {
    const scope = $('export-scope').value, collection = scope !== 'target';
    const needsTarget = ['target', 'body'].includes(scope);
    $('export-target-field').hidden = !needsTarget;
    $('export-target').disabled = !needsTarget;
    $('export-target').required = needsTarget;
    $('export-target-label').textContent = scope === 'body' ? 'Body' : 'Fit or joint';
    $('export-error').textContent = '';
    try {
      const plan = exportScopePlan(graphState, { scope, selected: selectedFeatureIds, target: $('export-target').value });
      const issues = exportInputIssues(graphState, plan);
      const pendingFaces = plan.faceIds.filter((id) => !['ready', 'failed', 'blocked'].includes(graphState.states[id])).length;
      $('export-summary').textContent = scope === 'body'
        ? `${graphState.states[plan.roots[0]] === 'ready' ? 'Validated solid' : 'Body needs evaluation'}${issues.length ? `. Unavailable: ${issues.join(', ')}.` : ''}`
        : collection
        ? `${plan.faceIds.length} ${plan.faceIds.length === 1 ? 'face' : 'faces'}${pendingFaces ? ` · ${pendingFaces} need evaluation` : ''}${issues.length ? `. Unavailable: ${issues.join(', ')}.` : ''}`
        : 'Observation-bounded fitted patches, not built face boundaries.';
      $('export-download').disabled = !!issues.length;
      if (!collection && issues.length) $('export-error').textContent = `Unavailable: ${issues.join(', ')}.`;
    } catch (error) {
      $('export-summary').textContent = error.message;
      $('export-download').disabled = true;
    }
    updateExportPlanes();
  };
  const populateExportTargets = () => {
    const body = $('export-scope').value === 'body', previous = $('export-target').value;
    const targets = graphState.recipe.nodes.filter((node) => body
      ? node.operation === 'body' : ['fit', 'joint_fit', 'axis_solve'].includes(node.operation));
    choices('export-target', targets, [targets.find((node) => node.id === previous)?.id ||
      targets.find((node) => node.id === selectedFeatureId)?.id ||
      targets.find((node) => ['axis_solve', 'joint_fit'].includes(node.operation))?.id || targets[0]?.id]);
  };
  $('export-scope').onchange = () => { populateExportTargets(); updateExportScope(); };
  $('export-target').onchange = updateExportScope;
  $('export-axis-up').onchange = updateExportPlanes;
  $('export-transform').onchange = updateExportPlanes;
  $('export-cad').onclick = () => {
    $('export-scope').value = graphNode(selectedFeatureId)?.operation === 'body' ? 'body' : 'all_faces';
    populateExportTargets();
    choices(
      'export-transform',
      [
        { id: '', label: 'Original scan coordinates' },
        ...graphState.recipe.nodes.filter((node) => node.operation === 'transform'),
      ],
      [activeDisplayTransform(graphState, selectedFeatureIds)?.id || ''],
    );
    updateExportScope();
    $('export-error').textContent = '';
    $('export-dialog').showModal();
  };
  $('export-form').onsubmit = async (event) => {
    event.preventDefault();
    if (busy || selectionDrawing || selectionPending) return;
    busy = true;
    $('export-fields').disabled = true;
    $('export-download').disabled = true;
    $('export-error').textContent = 'Checking export inputs…';
    const scope = $('export-scope').value,
      transform = $('export-transform').value || null;
    try {
      const plan = exportScopePlan(graphState, { scope, selected: selectedFeatureIds, target: $('export-target').value });
      const issues = exportInputIssues(graphState, plan);
      if (issues.length) throw new Error(`Cannot export: ${issues.join(', ')}. Repair these features first.`);
      const originPlane = scope === 'target' && !transform && $('export-axis-up').checked
        ? $('export-origin-plane').value || null : null;
      const state = await ensureCurrent([...plan.roots, transform, originPlane]);
      $('export-error').textContent = 'Preparing STEP export bundle…';
      const response = await fetch('/api/export/cad', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Scansor-Request': '1' },
        body: JSON.stringify({
          token: state.token,
          ...plan.payload,
          units: $('export-units').value,
          axis_up: scope === 'target' && !transform && $('export-axis-up').checked,
          origin_plane: originPlane,
          transform,
          include_mesh: $('export-mesh').checked,
        }),
      });
      if (!response.ok) throw new Error((await response.json()).error || 'Export failed');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'scansor-cad.zip';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('export-dialog').close();
      status(`CAD export downloaded: ${scope === 'body' ? 'solid, ' : plan.faceIds.length ? `${plan.faceIds.length} faces, ` : ''}STEP, metadata, and optional reference mesh.`);
    } catch (error) {
      $('export-error').textContent = error.message;
    } finally {
      busy = false;
      $('export-fields').disabled = false;
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
    const points = inputChoices(graphState, 'point'),
      selectedPoints = points.filter(output => selectedFeatureIds.has(output.reference.feature));
    choices(
      'new-axis-source',
      sources,
      [selectedFeatureId],
    );
    geometryInputChoices('new-axis-point-a', 'point', graphState.recipe.nodes, selectedPoints[0]?.reference);
    geometryInputChoices('new-axis-point-b', 'point', graphState.recipe.nodes, selectedPoints[1]?.reference);
    $('new-axis-mode').value = selectedPoints.length === 2 ? 'points' : 'free';
    $('new-axis-direction-reversed').checked = false;
    $('axis-error').textContent = '';
    showAxisInitializer(
      'new-axis-mode',
      'new-axis-source-fields',
      'new-axis-manual-fields',
      'new-axis-point-fields',
    );
    showCreateDialog('axis-dialog', 'new-axis-label', 'Reference axis');
  };
  $('new-point').onclick = () => {
    const fits = graphState.recipe.nodes.filter(
      (node) => node.operation === 'fit' && node.kind === 'sphere' && isStandaloneFit(node),
    );
    choices('new-point-source', fits, [selectedFeatureId]);
    $('new-point-mode').value = 'free';
    showAxisInitializer(
      'new-point-mode',
      'new-point-source-fields',
      'new-point-manual-fields',
    );
    showCreateDialog('point-dialog', 'new-point-label', 'Reference point');
  };
  $('new-frame').onclick = () => {
    const geometry = frameGeometry(graphState.recipe.nodes),
      selectedPoint = geometry.points.find(output => selectedFeatureIds.has(output.reference.feature)),
      selectedReferences = geometry.references.filter(output => selectedFeatureIds.has(output.reference.feature));
    if (!geometry.points.length || geometry.references.length < 2) {
      status('Create a point and two direction-bearing references before defining a frame.', true);
      return;
    }
    const primary =
        selectedReferences.find(output => output.capability === 'plane') ||
        selectedReferences[0] ||
        geometry.references.find(output => output.capability === 'plane') ||
        geometry.references[0],
      secondary =
        selectedReferences.find(output => inputReferenceKey(output.reference) !== inputReferenceKey(primary.reference)) ||
        geometry.references.find(output => inputReferenceKey(output.reference) !== inputReferenceKey(primary.reference));
    geometryInputChoices('new-frame-origin', 'point', graphState.recipe.nodes, selectedPoint?.reference || geometry.points[0].reference);
    geometryInputChoices('new-frame-primary-reference', 'direction', graphState.recipe.nodes, primary.reference);
    geometryInputChoices('new-frame-secondary-reference', 'direction', graphState.recipe.nodes, secondary.reference);
    $('new-frame-primary-output').value = '+Z';
    $('new-frame-secondary-output').value = '+X';
    $('frame-error').textContent = '';
    showCreateDialog('frame-dialog', 'new-frame-label', 'Coordinate frame');
  };
  $('new-scale').onclick = () => {
    const geometry = frameGeometry(graphState.recipe.nodes),
      selectedPoints = geometry.points.filter(output => selectedFeatureIds.has(output.reference.feature));
    if (geometry.points.length < 2) {
      status('Create at least two point datums before defining scale.', true);
      return;
    }
    const pairPoints = selectedPoints.length >= 2 ? selectedPoints : geometry.points.slice(0, 2),
      distances = [];
    for (let first = 0; first < pairPoints.length; first++)
      for (let second = first + 1; second < pairPoints.length; second++)
        distances.push({
          first_point: pairPoints[first].reference,
          second_point: pairPoints[second].reference,
          known_distance: currentPointDistance(pairPoints[first].reference, pairPoints[second].reference),
        });
    renderScaleDistances('new-scale-distance-rows', geometry.points, distances);
    $('scale-error').textContent = '';
    showCreateDialog('scale-dialog', 'new-scale-label', 'Output scale');
  };
  $('new-transform').onclick = () => {
    const frames = graphState.recipe.nodes.filter((node) => node.operation === 'frame'),
      scales = graphState.recipe.nodes.filter((node) => node.operation === 'scale');
    if (!frames.length || !scales.length) {
      status('Create a coordinate frame and output scale before composing a transform.', true);
      return;
    }
    choices('new-transform-frame', frames, [...selectedFeatureIds]);
    choices('new-transform-scale', scales, [...selectedFeatureIds]);
    $('transform-error').textContent = '';
    showCreateDialog('transform-dialog', 'new-transform-label', 'Output transform');
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
      (node) =>
        node.operation === 'fit' &&
        ['cone', 'cylinder', 'plane'].includes(node.kind) &&
        isStandaloneFit(node),
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
        (node) =>
          node.operation === 'fit' &&
          ['cone', 'cylinder', 'plane'].includes(node.kind) &&
          isStandaloneFit(node),
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
      if (await applyFeatureReuse(selected.id, { equal_corresponding_dimensions: true }))
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
    const referenceOperation = graphNode($('new-fit-reference').value)?.operation;
    if (
      ($('new-fit-kind').value === 'sphere' && referenceOperation !== 'point') ||
      ($('new-fit-kind').value !== 'sphere' && referenceOperation === 'point') ||
      ($('new-fit-kind').value !== 'plane' &&
        referenceOperation === 'reference_plane')
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
    const referenceOperation = graphNode($('fit-reference').value)?.operation;
    if (
      ($('surface-kind').value === 'sphere' && referenceOperation !== 'point') ||
      ($('surface-kind').value !== 'sphere' && referenceOperation === 'point') ||
      ($('surface-kind').value !== 'plane' &&
        referenceOperation === 'reference_plane')
    )
      $('fit-reference').value = '';
    $('axial-start-row').hidden = $('surface-kind').value === 'sphere';
    $('axial-end-row').hidden = $('surface-kind').value === 'sphere';
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
      'new-axis-point-fields',
    );
  $('new-point-mode').onchange = () =>
    showAxisInitializer(
      'new-point-mode',
      'new-point-source-fields',
      'new-point-manual-fields',
    );
  $('axis-init-mode').onchange = () =>
    showAxisInitializer(
      'axis-init-mode',
      'axis-source-fields',
      'axis-manual-fields',
      'axis-point-fields',
    );
  $('point-init-mode').onchange = () =>
    showAxisInitializer('point-init-mode', 'point-source-fields', 'point-manual-fields');
  $('add-new-scale-distance-row').onclick = () =>
    scaleDistanceRow(
      'new-scale-distance-rows',
      frameGeometry(graphState.recipe.nodes).points,
    );
  $('add-scale-distance-row').onclick = () => {
    const selected = graphNode(selectedFeatureId),
      earlier = graphState.recipe.nodes.slice(0, graphState.recipe.nodes.indexOf(selected));
    scaleDistanceRow('scale-distance-rows', frameGeometry(earlier).points);
  };
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
    const saved = await applyFeatureReuse(uid('feature_reuse'), {
      label: submittedFeatureLabel('new-feature-reuse-label'),
      fits: fitIds,
      reference_selection: referenceSelection,
      target_selections: targetSelections,
      tangent_margin: Number($('new-feature-reuse-tangent-margin').value),
      normal_margin: Number($('new-feature-reuse-normal-margin').value),
      normal_angle_degrees: Number($('new-feature-reuse-normal-angle').value),
      equal_corresponding_dimensions:
        $('new-feature-reuse-equal-dimensions').checked,
    }, true);
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
    const mode = $('new-axis-mode').value,
      fromFit = mode === 'fit',
      fromPoints = mode === 'points',
      sourceId = $('new-axis-source').value,
      source = graphNode(sourceId),
      pointA = $('new-axis-point-a').value,
      pointB = $('new-axis-point-b').value,
      axisLabel = submittedFeatureLabel('new-axis-label');
    if (fromFit && !source) {
      status('Create a standalone cone or cylinder fit first.', true);
      return;
    }
    if (fromPoints && (!pointA || !pointB || pointA === pointB)) {
      $('axis-error').textContent = 'Choose two distinct point datums.';
      return;
    }
    const axis = {
      id: uid('axis'),
      label: axisLabel,
      operation: 'axis',
      direction_reversed: $('new-axis-direction-reversed').checked,
      ...(fromFit
        ? { source_fit: sourceId }
        : fromPoints
          ? { source_points: [readInputReference(pointA), readInputReference(pointB)] }
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
  $('add-point-form').onsubmit = async (event) => {
    event.preventDefault();
    const fromFit = $('new-point-mode').value === 'fit',
      sourceId = $('new-point-source').value,
      source = graphNode(sourceId),
      pointLabel = submittedFeatureLabel('new-point-label');
    if (fromFit && !source) {
      status('Create a standalone sphere fit first.', true);
      return;
    }
    const point = {
      id: uid('point'),
      label: pointLabel,
      operation: 'point',
      ...(fromFit
        ? { source_fit: sourceId }
        : {
            initial_coordinates: [
              Number($('new-point-x').value),
              Number($('new-point-y').value),
              Number($('new-point-z').value),
            ],
          }),
    };
    const nodes = [point];
    if (fromFit && $('new-point-clone').checked)
      nodes.push({
        id: uid('fit'),
        label: nextFeatureLabel(source.label + ' point factor', [pointLabel]),
        operation: 'fit',
        selections: [...source.selections],
        kind: 'sphere',
        axial_domain: [...source.axial_domain],
        point: point.id,
      });
    if (await appendActions(nodes)) $('point-dialog').close();
  };
  $('add-frame-form').onsubmit = async (event) => {
    event.preventDefault();
    const primaryReference = $('new-frame-primary-reference').value,
      secondaryReference = $('new-frame-secondary-reference').value,
      primaryOutput = $('new-frame-primary-output').value,
      secondaryOutput = $('new-frame-secondary-output').value;
    if (
      !primaryReference ||
      !secondaryReference ||
      primaryReference === secondaryReference ||
      primaryOutput.at(-1) === secondaryOutput.at(-1)
    ) {
      $('frame-error').textContent =
        'Choose two different direction references and two different output axes.';
      return;
    }
    const saved = await appendActions([
      {
        id: uid('frame'),
        label: submittedFeatureLabel('new-frame-label'),
        operation: 'frame',
        origin_point: readInputReference($('new-frame-origin').value),
        primary_reference: readInputReference(primaryReference),
        primary_output_axis: primaryOutput,
        secondary_reference: readInputReference(secondaryReference),
        secondary_output_axis: secondaryOutput,
      },
    ]);
    if (saved) $('frame-dialog').close();
    else $('frame-error').textContent = $('status').textContent;
  };
  $('add-scale-form').onsubmit = async (event) => {
    event.preventDefault();
    const distances = readScaleDistances('new-scale-distance-rows');
    if (
      !distances.length ||
      distances.some(
        (distance) =>
          !distance.first_point ||
          !distance.second_point ||
          inputReferenceKey(distance.first_point) === inputReferenceKey(distance.second_point) ||
          !(distance.known_distance > 0),
      )
    ) {
      $('scale-error').textContent =
        'Add at least one valid distance between two distinct point datums.';
      return;
    }
    const saved = await appendActions([
      {
        id: uid('scale'),
        label: submittedFeatureLabel('new-scale-label'),
        operation: 'scale',
        distances,
      },
    ]);
    if (saved) $('scale-dialog').close();
    else $('scale-error').textContent = $('status').textContent;
  };
  $('add-transform-form').onsubmit = async (event) => {
    event.preventDefault();
    const saved = await appendActions([
      {
        id: uid('transform'),
        label: submittedFeatureLabel('new-transform-label'),
        operation: 'transform',
        frame: $('new-transform-frame').value,
        scale: $('new-transform-scale').value,
      },
    ]);
    if (saved) $('transform-dialog').close();
    else $('transform-error').textContent = $('status').textContent;
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
  $('add-relationship-form').onsubmit = async (event) => {
    event.preventDefault();
    if (featureTreeLocked() || relationshipApplying) return;
    const participants = graphState.recipe.nodes.filter((node) =>
        relationshipParticipantIds.has(node.id),
      ),
      validity = relationshipValidity(selectedRelationshipKind, participants);
    if (!selectedRelationshipKind || !validity.valid) {
      $('relationship-error').textContent = validity.reason || 'Choose a compatible relationship.';
      return;
    }
    const fits = participants.filter((node) => node.operation === 'fit'),
      datum = participants.find((node) => node.operation === 'reference_plane');
    let node;
    if (selectedRelationshipKind === 'coincident_planes' || selectedRelationshipKind === 'parallel_planes')
      node = {
        id: uid('plane_relationship'),
        label: submittedFeatureLabel('new-relationship-label'),
        operation: 'plane_relationship',
        relation: selectedRelationshipKind === 'coincident_planes' ? 'coincident' : 'parallel',
        surfaces: fits.map((fit) => fit.id),
      };
    else if (selectedRelationshipKind === 'equal_radii')
      node = {
        id: uid('equal_radii'),
        label: submittedFeatureLabel('new-relationship-label'),
        operation: 'equal_radii',
        surfaces: fits.map((fit) => fit.id),
      };
    else if (selectedRelationshipKind === 'mirror')
      node = {
        id: uid('mirror'),
        label: submittedFeatureLabel('new-relationship-label'),
        operation: 'mirror_symmetry',
        plane: datum.id,
        surfaces: fits.map((fit) => fit.id),
        symmetric_extents: true,
      };
    else {
      const cylinder = fits.find((fit) => fit.kind === 'cylinder'),
        plane = fits.find((fit) => fit.kind === 'plane');
      node = {
        id: uid('equal'),
        label: submittedFeatureLabel('new-relationship-label'),
        operation: 'equal',
        left: { measurement: 'radius', surface: cylinder.id },
        right: { measurement: 'plane_distance', surface: plane.id, reference_plane: datum.id },
      };
    }
    relationshipApplying = true;
    const panels = workspaceEditingPanels(),
      previousInert = panels.map((panel) => panel.inert);
    panels.forEach((panel) => { panel.inert = true; });
    $('feature-context-menu').hidePopover();
    renderActions();
    updateRelationshipSelection();
    try {
      if (await appendActions([node])) $('relationship-dialog').close();
      else $('relationship-error').textContent = $('status').textContent;
    } finally {
      relationshipApplying = false;
      panels.forEach((panel, index) => { panel.inert = previousInert[index]; });
      renderActions();
      if ($('relationship-dialog').open) updateRelationshipSelection();
    }
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
      (n) =>
        n.operation === 'fit' &&
        ['cone', 'cylinder', 'plane'].includes(n.kind) &&
        isStandaloneFit(n) &&
        !used.has(n.id),
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
    selectOnly(jointId);
    if (await replaceRecipe(recipe)) {
      $('rotation-dialog').close();
      $('feature-properties-panel').scrollTop = 0;
    } else $('rotation-error').textContent = $('status').textContent;
  };
  for (const button of document.querySelectorAll('[data-close-dialog]'))
    button.onclick = () => {
      const id = button.dataset.closeDialog;
      if (['build-faces-dialog', 'relationship-dialog'].includes(id)) requestWorkspaceClose(id);
      else $(id).close();
    };
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
  $('choose-example').onclick = async () => {
    if (featureTreeLocked()) return;
    $('choose-example').disabled = true;
    try {
      exampleCatalogue = await request('/api/examples');
      const choices = exampleCatalogue.examples.map(example => new Option(example.label, example.id));
      if (exampleCatalogue.active === null) {
        const current = new Option('Current workspace', '', true, true);
        current.disabled = true;
        choices.unshift(current);
      }
      $('example-choice').replaceChildren(...choices);
      $('example-choice').value = exampleCatalogue.active || '';
      $('example-error').textContent = '';
      $('open-example').disabled = true;
      $('example-dialog').showModal();
    } catch (error) { status(error.message, true); }
    finally { updateEvaluationControls(); }
  };
  $('example-choice').onchange = () => {
    $('open-example').disabled = !$('example-choice').value ||
      $('example-choice').value === exampleCatalogue.active || exampleSwitchPending;
  };
  $('example-dialog').addEventListener('cancel', event => {
    if (exampleSwitchPending) event.preventDefault();
  });
  $('example-form').onsubmit = async event => {
    event.preventDefault();
    const example = $('example-choice').value;
    if (!example || example === exampleCatalogue.active || exampleSwitchPending || featureTreeLocked()) return;
    exampleSwitchPending = true;
    busy = true;
    setModelPickMode(null);
    const panels = [...workspaceEditingPanels(), viewport], previousInert = panels.map(panel => panel.inert),
      controls = [$('example-choice'), $('open-example'), $('cancel-example')];
    panels.forEach(panel => { panel.inert = true; });
    controls.forEach(control => { control.disabled = true; });
    $('example-error').textContent = '';
    status('Opening example…');
    renderActions();
    paint();
    try {
      await request('/api/examples/select', { example, token: graphState.token });
      location.reload();
    } catch (error) {
      $('example-error').textContent = error.message;
      status(error.message, true);
      exampleSwitchPending = false;
      busy = false;
      panels.forEach((panel, index) => { panel.inert = previousInert[index]; });
      controls.forEach(control => { control.disabled = false; });
      renderActions();
      paint();
    }
  };
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
    if (node.operation === 'body') {
      try {
        Object.assign(node, bodyInputs(bodyCheckedFaces('body-face-choices'), $('body-tolerance').value));
      } catch (error) { status(error.message, true); return; }
    }
    if (node.operation === 'surface_intersection') {
      node.first = readSurfaceReference('intersection-first');
      node.second = readSurfaceReference('intersection-second');
    }
    if (node.operation === 'trimmed_face') {
      node.surface = readSurfaceReference('face-surface');
      node.boundaries = readFaceBoundaries('face');
    }
    if (node.operation === 'fit') {
      node.kind = $('surface-kind').value;
      node.selections = chosen('fit-inputs');
      node.axial_domain = [Number($('axial-start').value), Number($('axial-end').value)];
      setFitReference(node, $('fit-reference').value);
    }
    if (node.operation === 'axis') {
      const mode = $('axis-init-mode').value;
      node.direction_reversed = $('axis-direction-reversed').checked;
      if (mode === 'fit') {
        node.source_fit = $('axis-source-fit').value;
        node.source_points = null;
        node.initial_parameters = null;
      } else if (mode === 'points') {
        const pointA = $('axis-source-point-a').value,
          pointB = $('axis-source-point-b').value;
        if (!pointA || !pointB || pointA === pointB) {
          status('Choose two distinct point datums.', true);
          return;
        }
        node.source_fit = null;
        node.source_points = [readInputReference(pointA), readInputReference(pointB)];
        node.initial_parameters = null;
      } else {
        node.source_fit = null;
        node.source_points = null;
        node.initial_parameters = [
          Number($('axis-point-x').value),
          Number($('axis-point-y').value),
          Number($('axis-direction-x').value),
          Number($('axis-direction-y').value),
        ];
      }
    }
    if (node.operation === 'point') {
      if ($('point-init-mode').value === 'fit') {
        node.source_fit = $('point-source-fit').value;
        node.initial_coordinates = null;
      } else {
        node.source_fit = null;
        node.initial_coordinates = [
          Number($('point-x').value),
          Number($('point-y').value),
          Number($('point-z').value),
        ];
      }
    }
    if (node.operation === 'frame') {
      const primaryReference = $('frame-primary-reference').value,
        secondaryReference = $('frame-secondary-reference').value,
        primaryOutput = $('frame-primary-output').value,
        secondaryOutput = $('frame-secondary-output').value;
      if (
        primaryReference === secondaryReference ||
        primaryOutput.at(-1) === secondaryOutput.at(-1)
      ) {
        status('Choose different direction references and output axes.', true);
        return;
      }
      node.origin_point = readInputReference($('frame-origin').value);
      node.primary_reference = readInputReference(primaryReference);
      node.primary_output_axis = primaryOutput;
      node.secondary_reference = readInputReference(secondaryReference);
      node.secondary_output_axis = secondaryOutput;
    }
    if (node.operation === 'scale') {
      const distances = readScaleDistances('scale-distance-rows');
      if (
        !distances.length ||
        distances.some(
          (distance) =>
            !distance.first_point ||
            !distance.second_point ||
            inputReferenceKey(distance.first_point) === inputReferenceKey(distance.second_point) ||
            !(distance.known_distance > 0),
        )
      ) {
        status('Add at least one valid distance between distinct points.', true);
        return;
      }
      node.distances = distances;
    }
    if (node.operation === 'transform') {
      node.frame = $('transform-frame').value;
      node.scale = $('transform-scale').value;
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
      await applyFeatureReuse(node.id, {
        label: node.label,
        group_id: node.group_id,
        fits: chosen('feature-reuse-fits'),
        reference_selection: $('feature-reuse-reference').value,
        target_selections: chosen('feature-reuse-target'),
        equal_corresponding_dimensions:
          $('feature-reuse-equal-dimensions').checked,
        tangent_margin: Number($('feature-reuse-tangent-margin').value),
        normal_margin: Number($('feature-reuse-normal-margin').value),
        normal_angle_degrees: Number($('feature-reuse-normal-angle').value),
      });
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
  $('delete-action').onclick = () => {
    featureContextAnchor = selectedFeatureId;
    reviewFeatureDeletion();
  };
  $('delete-selected-features').onclick = reviewFeatureDeletion;
  $('delete-feature-dependents').onchange = updateFeatureDeletionButton;
  $('confirm-delete-features').onclick = () => void confirmFeatureDeletion();
  $('feature-context-menu').onkeydown = (event) => {
    if (event.key !== 'Escape' && event.key !== 'Tab') return;
    $('feature-context-menu').hidePopover();
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      focusContextFeature();
    }
  };
  $('features-panel').addEventListener('wheel', () => $('feature-context-menu').hidePopover(),
    { passive: true });
  $('delete-features-dialog').oncancel = (event) => {
    if (featureDeletionPending) event.preventDefault();
  };
  $('delete-features-dialog').onclose = () => {
    featureDeletionReview = null;
    focusContextFeature();
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
  for (const name of ['colors', 'all-residuals', 'guides', 'points']) $(name).onchange = paint;
  $('faces-only').onchange = () => {
    modelPickGeometryKey = null;
    paintModelPickGuides();
    showConstructedFaces();
    paint();
  };
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
  $('dismiss-import-warning').onclick = () => { $('import-warning').hidden = true; };
  $('file').onchange = async () => {
    try {
      const file = $('file').files[0];
      if (!file) return;
      if (file.size > 1_000_000) throw new Error('Recipe must be under 1 MB');
      const recipe = JSON.parse(await file.text());
      let state;
      try { state = await request('/api/graph/import', { token: graphState.token, recipe }); }
      catch (error) {
        try { acceptGraph(await request('/api/graph')); }
        catch (refreshError) {
          throw new Error(`${error.message} Could not refresh current actions: ${refreshError.message}`);
        }
        throw error;
      }
      acceptGraph(state);
      showImportWarnings(state.import_warnings || []);
      status('Actions loaded. Evaluate to refresh dependent results.');
      if ($('auto-evaluate').checked) setTimeout(() => void ensureAll(), 0);
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
      $('faces-only').checked ||
      $('relationship-dialog').open ||
      ($('build-faces-dialog').open && buildFacesMode === 'guided') ||
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
  viewport.addEventListener('workspace-panel-visibility', event => {
    if (!event.detail.visible) cancelStroke();
    else requestAnimationFrame(() => resizeViewport?.());
  });
  for (const id of [
    'feature-graph-lens',
    'feature-graph-selections',
    'feature-graph-generated',
    'feature-graph-neighborhood',
  ])
    $(id).onchange = renderGraphView;
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
    if (modelPickMode) {
      cancelStroke();
      modelPickClick = null;
      clearModelPickHover();
      $('model-pick-choices').hidePopover();
      modelPickGesture = event.button === 0 ? {
        pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      } : null;
      if (event.button === 0) {
        event.preventDefault();
        canvas.focus();
        canvas.setPointerCapture(event.pointerId);
      }
      return;
    }
    if (event.button === 0 && $('build-faces-dialog').open && buildFacesMode === 'guided') {
      cancelStroke();
      event.preventDefault();
      canvas.focus();
      pickGuidedFaceRegion(event);
      return;
    }
    if ($('faces-only').checked || $('relationship-dialog').open) return;
    if (event.button !== 0) {
      inspectFaceRegion(null);
      cancelStroke();
      return;
    }
    if ($('tool').value === 'orbit' || busy || selectionPending || relationshipApplying) return;
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
    if (modelPickMode) {
      if (modelPickGesture && Math.hypot(event.clientX - modelPickGesture.clientX,
          event.clientY - modelPickGesture.clientY) > 5) modelPickGesture = null;
      if (event.buttons || $('model-pick-choices').matches(':popover-open')) return;
      const matches = modelPicksAt(event);
      inspectModelPick(matches.length === 1 ? matches[0] : null);
      $('model-pick-message').textContent = matches.length === 1 ? matches[0].label :
        matches.length > 1 ? `${matches.length} items here. Click to choose.` : 'Click a scan patch or visible guide. Esc to stop.';
      return;
    }
    if ($('build-faces-dialog').open && buildFacesMode === 'guided') {
      inspectFaceRegion(event.buttons ? null : guidedFaceRegionAt(event));
      return;
    }
    if (stroke && stroke.pointerId === event.pointerId) extend(xy(event));
  });
  canvas.addEventListener('pointerleave', () => {
    $('brush-cursor').hidden = true;
    inspectFaceRegion(null);
    if (!$('model-pick-choices').matches(':popover-open')) clearModelPickHover();
  });
  canvas.addEventListener('pointerup', async (event) => {
    if (modelPickMode) {
      const clicked = isModelClick(modelPickGesture, event);
      modelPickGesture = null;
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      // Commit on click, after the browser's pointerup light-dismiss step. A
      // popover opened during pointerup would immediately dismiss itself.
      // Keep the subpixel pointer coordinates: MouseEvent click coordinates
      // can be rounded differently and hit an adjacent triangle at a patch edge.
      modelPickClick = clicked ? { clientX: event.clientX, clientY: event.clientY } : null;
      return;
    }
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
    const panels = workspaceEditingPanels(), previousInert = panels.map(panel => panel.inert);
    panels.forEach(panel => { panel.inert = true; });
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
      panels.forEach((panel, index) => { panel.inert = previousInert[index]; });
      paint();
    }
  });
  canvas.addEventListener('pointercancel', cancelStroke);
  canvas.addEventListener('click', () => {
    if (!modelPickMode || !modelPickClick) return;
    const point = modelPickClick;
    modelPickClick = null;
    pickModelItem(point);
  });
  canvas.addEventListener('lostpointercapture', cancelStroke);
  canvas.addEventListener('wheel', cancelStroke, { capture: true });
  window.addEventListener('blur', cancelStroke);
  window.addEventListener('resize', cancelStroke);
  for (const type of ['pointercancel', 'lostpointercapture', 'wheel', 'blur']) {
    canvas.addEventListener(type, () => { modelPickGesture = null; clearModelPickHover(); });
  }
  window.addEventListener('blur', () => { modelPickGesture = null; clearModelPickHover(); });
  window.addEventListener('resize', () => $('model-pick-choices').hidePopover());
  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (modelPickMode) {
      event.preventDefault();
      setModelPickMode(null);
      return;
    }
    if (selectionDrawing || selectionPending) {
      cancelStroke();
      return;
    }
    if (document.querySelector('dialog[open]')) return;
    selectOnly(null);
    refreshFeatureSelection();
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
  if (location.hash === '#graph') revealWorkspacePanel('graph');
  $('save').disabled = false;
  status('Feature graph loaded. Ready to evaluate.');
  if ($('auto-evaluate').checked) await ensureAll();
}
await start();
