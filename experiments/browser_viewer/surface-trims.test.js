import test from 'node:test';
import assert from 'node:assert/strict';
import { surfaceReferenceChoices, surfaceReferencePresentation, fittedSurfacePresentation,
  completeFitSurfaceChoices, eligibleIntersections, sameSurfaceReference,
  unavailableRetainedFitReferences, surfaceFootprintState,
  bindFaceContinuationPreview, appliedFaceContext, definedFaceContext,
  faceContinuationPreview, previewedFaceContextIds, renderFaceDialogClose, unbuiltFaceNeighbors,
  boundaryKeepOptions, geometryAppendOutput, validGeometryPreview, initialBuildFaceRegion,
  initialBuildFaceRegions, suggestedBuildFaceRegions,
  changeBuildFaceRegionSelection, compactFaceRegionList,
  buildFaceChoices, buildFaceRegionGroups, boundedBuildFaceRegions, faceEvidenceSummary, validBuildFaceRegion, addFittedSurfaceReferences,
  adjacencyPairKey, buildAdjacencyDecisions, faceScopeChoices, buildFaceScopes,
  intersectionCurves, intersectionPreviewPaths, faceBoundaryPreviewPaths, physicalBoundsSummary,
  initialGuidedFaceRegions, guidedCandidateSelected, guidedSurfaceInputs, guidedProposalFaces,
  approvedNeighborPreviewPaths, guidedSourceAvailable, guidedRejectedDecisions,
  unavailableGuidedSources, faceCandidatePresentation, guidedNeighborHighlights } from './surface-trims.js';
import { nodeReferences, actionMove } from './action-tree.js';
import { featureVertexIds } from './selection.js';
import { buildFeatureGraph } from './feature-graph-view.js';

const selection = { id: 'selected', label: 'Observations', operation: 'selection', source: 'source' };
const cylinder = { id: 'wall', label: 'Wall', operation: 'fit', kind: 'cylinder', selections: ['selected'] };
const plane = { id: 'shoulder', label: 'Shoulder', operation: 'fit', kind: 'plane', selections: ['selected'] };
const solve = { id: 'joint', label: 'Joint', operation: 'axis_solve', axis: 'axis', factors: ['wall', 'shoulder'] };
const first = { feature: 'joint', surface: 'wall' };
const second = { feature: 'joint', surface: 'shoulder' };
const intersection = { id: 'edge', label: 'Shared edge', operation: 'surface_intersection', first, second };
const face = { id: 'face', label: 'Wall face', operation: 'trimmed_face', surface: first,
  boundaries: [{ intersection: 'edge', keep: 'positive' }] };
const nodes = [{ id: 'source', label: 'Scan', operation: 'source' }, selection, cylinder, plane,
  { id: 'axis', label: 'Axis', operation: 'axis' }, solve, intersection, face];

test('scan-supported bulk selection adds to a draft and reports only actual changes', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] },
    proposal = { status: 'suggested', suggested_region_keys: ['supported', 'missing', 'invalid'],
      regions: [{ key: 'manual', preview, bounded: true }, { key: 'supported', preview, bounded: true }, { key: 'invalid' }] },
    draft = ['manual'], before = JSON.stringify(proposal);
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, draft, 'suggested'),
    { region_keys: ['manual', 'supported'], changed: true });
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, ['manual', 'supported'], 'suggested'),
    { region_keys: ['manual', 'supported'], changed: false });
  assert.deepEqual(draft, ['manual']);
  assert.equal(JSON.stringify(proposal), before);
  for (const blocked of [{ blocked_by_geometry: true }, { blocked_by_adjacency: true }, { status: 'ambiguous' }])
    assert.deepEqual(changeBuildFaceRegionSelection({ ...proposal, ...blocked }, draft, 'suggested'),
      { region_keys: draft, changed: false });
});

test('region selection edits share toggle validation and clear can discard a blocked draft', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] },
    proposal = { regions: [{ key: 'valid', preview, bounded: true }, { key: 'invalid' }] };
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, [], 'toggle', 'valid'),
    { region_keys: ['valid'], changed: true });
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, ['valid'], 'toggle', 'valid'),
    { region_keys: [], changed: true });
  for (const key of ['invalid', 'missing'])
    assert.deepEqual(changeBuildFaceRegionSelection(proposal, ['valid'], 'toggle', key),
      { region_keys: ['valid'], changed: false });
  assert.deepEqual(changeBuildFaceRegionSelection({ ...proposal, blocked_by_geometry: true },
    ['valid'], 'toggle', 'valid'), { region_keys: ['valid'], changed: false });
  assert.deepEqual(changeBuildFaceRegionSelection({ ...proposal, blocked_by_geometry: true },
    ['valid', 'stale'], 'clear'), { region_keys: [], changed: true });
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, [], 'clear'), { region_keys: [], changed: false });
});

test('large region sets use the compact model-first review without dropping list access', () => {
  assert.equal(compactFaceRegionList({ regions: [] }), false);
  assert.equal(compactFaceRegionList({ regions: Array(8).fill({ bounded: true }) }), false);
  assert.equal(compactFaceRegionList({ regions: Array(9).fill({ bounded: true }) }), true);
  assert.equal(compactFaceRegionList({ regions: Array(20).fill({ bounded: false }) }), false);
});

test('open regions are excluded from review, restoring choices, toggles and stale bulk drafts', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] },
    bounded = { key: 'bounded', bounded: true, preview },
    open = { key: 'open', bounded: false, preview, previously_selected: true,
      owned_face_id: 'legacy-open', evidence: { interior_count: 8 } },
    unknown = { key: 'unknown', preview, existing_face_id: 'unknown' },
    proposal = { key: 'face', surface: first, status: 'suggested',
      suggested_region_keys: ['open', 'unknown', 'bounded'], regions: [open, unknown, bounded] },
    before = JSON.stringify(proposal);
  assert.deepEqual(boundedBuildFaceRegions(proposal), [bounded]);
  assert.deepEqual(buildFaceRegionGroups(proposal, ['open', 'unknown']), { primary: [bounded], other: [] });
  assert.deepEqual(initialGuidedFaceRegions(proposal), []);
  assert.deepEqual(initialBuildFaceRegions(proposal), ['bounded']);
  for (const key of ['open', 'unknown'])
    assert.deepEqual(changeBuildFaceRegionSelection(proposal, [], 'toggle', key),
      { region_keys: [], changed: false });
  assert.deepEqual(changeBuildFaceRegionSelection(proposal, ['open', 'unknown'], 'suggested'),
    { region_keys: ['bounded'], changed: true });
  assert.deepEqual(buildFaceChoices({ faces: [proposal] }, new Map([
    ['face', { region_keys: ['open', 'unknown'] }],
  ])), []);
  assert.deepEqual(boundedBuildFaceRegions({ regions: [open, unknown] }), []);
  assert.equal(JSON.stringify(proposal), before);
});

test('complete fit choices offer physical fits once, without automatic relationship variants or datums', () => {
  const equality = { id: 'equal', operation: 'equal_radii', surfaces: ['wall'] },
    parallel = { id: 'parallel', operation: 'plane_relationship', surfaces: ['shoulder'] },
    datum = { id: 'datum', operation: 'reference_plane', axis: 'axis' },
    sphere = { id: 'sphere', operation: 'fit', kind: 'sphere' };
  const result = completeFitSurfaceChoices([selection, cylinder, plane, equality, parallel, datum, sphere], {
    wall: { resolved_by: 'equal_radii' }, shoulder: { resolved_by: 'plane_relationship' },
    equal: { surfaces: { wall: {} } }, parallel: { surfaces: { shoulder: {} } },
  });
  assert.deepEqual(result.choices.map((choice) => choice.reference), [
    { feature: 'wall', surface: null }, { feature: 'shoulder', surface: null },
  ]);
  assert.deepEqual(result.choices.map((choice) => choice.label), ['Wall — Cylinder fit', 'Shoulder — Plane fit']);
  assert.deepEqual(result.unavailable, []);
});

test('a sole completed explicit solve is internal to one physical fit choice', () => {
  const bound = { ...cylinder, axis: 'axis' },
    joint = { ...solve, factors: ['wall'] };
  const result = completeFitSurfaceChoices([bound, joint], { joint: { surfaces: { wall: {} } } });
  assert.deepEqual(result.choices.map((choice) => choice.reference), [first]);
  assert.equal(result.choices[0].label, 'Wall — Cylinder fit');
  assert.equal(result.choices[0].context, null);
  assert.deepEqual(result.unavailable, []);
  assert.equal(fittedSurfacePresentation(first, nodes).icon, 'cylinder');
});

test('a completed legacy joint supersedes independent geometry without exposing context choices', () => {
  const cut = { id: 'cut', operation: 'perpendicular', lateral: 'wall', plane: 'shoulder' },
    joint = { id: 'legacy', operation: 'joint_fit', constraints: ['cut'] };
  const result = completeFitSurfaceChoices([cylinder, plane, cut, joint], {
    legacy: { surfaces: { wall: {}, shoulder: {} } },
  });
  assert.deepEqual(result.choices.map((choice) => choice.reference), [
    { feature: 'legacy', surface: 'wall' }, { feature: 'legacy', surface: 'shoulder' },
  ]);
  assert.deepEqual(result.unavailable, []);
});

test('multiple explicit providers are unavailable, never selected by recipe order', () => {
  const bound = { ...cylinder, axis: 'axis' },
    firstJoint = { ...solve, factors: ['wall'] }, secondJoint = { ...firstJoint, id: 'other' },
    results = { joint: { surfaces: { wall: {} } }, other: { surfaces: { wall: {} } } };
  for (const joints of [[firstJoint, secondJoint], [secondJoint, firstJoint]]) {
    const result = completeFitSurfaceChoices([bound, ...joints], results);
    assert.deepEqual(result.choices, []);
    assert.deepEqual(result.unavailable, [{ label: 'Wall', reason: 'Multiple solve outputs' }]);
  }
});

test('incomplete explicit solves are not replaced by standalone or undeclared component geometry', () => {
  const bound = { ...cylinder, axis: 'axis' }, joint = { ...solve, factors: ['wall'] };
  for (const results of [{}, { joint: { surfaces: { shoulder: {} } } }]) {
    const result = completeFitSurfaceChoices([bound, joint], results);
    assert.deepEqual(result.choices, []);
    assert.equal(result.unavailable.length, 1);
  }
});

test('a partial legacy joint does not expose missing members as independent fits', () => {
  const cut = { id: 'cut', operation: 'perpendicular', lateral: 'wall', plane: 'shoulder' },
    joint = { id: 'legacy', operation: 'joint_fit', constraints: ['cut'] };
  const result = completeFitSurfaceChoices([cylinder, plane, cut, joint], {
    legacy: { surfaces: { shoulder: {} } },
  });
  assert.deepEqual(result.choices.map((choice) => choice.reference), [{ feature: 'legacy', surface: 'shoulder' }]);
  assert.deepEqual(result.unavailable, [{ label: 'Wall', reason: 'Evaluate its solve first' }]);
});

test('stale fit and solve outputs are unavailable even if old geometry remains', () => {
  const joint = { ...solve, factors: ['wall'] },
    results = { joint: { surfaces: { wall: {} } } };
  for (const states of [{ wall: 'stale', joint: 'ready' }, { wall: 'ready', joint: 'stale' }]) {
    const result = completeFitSurfaceChoices([cylinder, joint], results, [], states);
    assert.deepEqual(result.choices, []);
    assert.equal(result.unavailable.length, 1);
  }
});

test('joint and automatic relationship outputs are not silently ranked', () => {
  const joint = { ...solve, factors: ['wall'] };
  const result = completeFitSurfaceChoices([cylinder, joint], {
    wall: { resolved_by: 'equal_radii' }, joint: { surfaces: { wall: {} } },
  });
  assert.deepEqual(result.choices, []);
  assert.deepEqual(result.unavailable, [{ label: 'Wall', reason: 'Conflicting solve outputs' }]);
});

test('saved face dependencies stay exact while their labels hide solve contexts', () => {
  const equality = { id: 'equal', label: 'Equal radii', operation: 'equal_radii', surfaces: ['wall'] },
    retained = { feature: 'equal', surface: 'wall' };
  const result = completeFitSurfaceChoices([cylinder, equality], {
    equal: { surfaces: { wall: {} } },
  }, [retained]);
  assert.deepEqual(result.choices.map((choice) => choice.reference), [retained]);
  assert.equal(result.choices[0].label, 'Wall — Cylinder fit');
  assert.deepEqual(result.unavailable, []);
});

test('unavailable saved inputs are identified exactly rather than silently dropped from face review', () => {
  const direct = { feature: 'wall', surface: null },
    datum = { feature: 'datum' }, otherContext = { feature: 'other', surface: 'wall' },
    retained = [direct, datum, otherContext];
  assert.deepEqual(unavailableRetainedFitReferences(retained, [{ reference: direct }]), [datum, otherContext]);
  assert.deepEqual(unavailableRetainedFitReferences(retained, retained.map((reference) => ({ reference }))), []);
  assert.deepEqual(unavailableRetainedFitReferences(retained, []), retained);
});

test('surface footprints emphasize picker inspection and fade for neighbor review', () => {
  assert.deepEqual(surfaceFootprintState(first, null, true, false), { reference: first, prominent: true });
  assert.deepEqual(surfaceFootprintState(first, second, false, true), { reference: second, prominent: true });
  assert.deepEqual(surfaceFootprintState(first, null, true, true), { reference: first, prominent: false });
  assert.deepEqual(surfaceFootprintState(first, null, false, false), { reference: first, prominent: false });
  assert.deepEqual(surfaceFootprintState(null, null, false, false), { reference: null, prominent: false });
});

test('face dialog offers Done after Apply and Cancel for a new or edited review', () => {
  const button = { type: 'button', dataset: { closeDialog: 'build-faces-dialog' } };
  renderFaceDialogClose(button, false);
  assert.equal(button.textContent, 'Cancel');
  renderFaceDialogClose(button, true);
  assert.equal(button.textContent, 'Done');
  assert.equal(button.title, 'Close; applied faces are saved');
  renderFaceDialogClose(button, false);
  assert.equal(button.textContent, 'Cancel');
  assert.match(button.title, /Discard unapplied changes/);
  assert.equal(button.type, 'button');
  assert.equal(button.dataset.closeDialog, 'build-faces-dialog');
});

test('Continue previews respond to pointer and keyboard without clearing the other active interaction', () => {
  let state = { pointer: null, focus: null };
  const calls = [], button = { disabled: false };
  bindFaceContinuationPreview(button, first, (reference, active, interaction) => {
    calls.push([reference, active, interaction]);
    state = faceContinuationPreview(state, reference, active, interaction);
  });
  button.onpointerenter();
  button.onfocus();
  button.onpointerleave();
  assert.deepEqual(state, { pointer: null, focus: first });
  state = faceContinuationPreview(state, second, true, 'pointer');
  assert.deepEqual(state, { pointer: second, focus: first });
  state = faceContinuationPreview(state, first, false, 'pointer');
  assert.equal(state.pointer, second); // A late leave event cannot clear another button.
  state = faceContinuationPreview(state, second, false, 'pointer');
  assert.equal(state.pointer || state.focus, first);
  button.onblur();
  assert.deepEqual(state, { pointer: null, focus: null });
  assert.deepEqual(calls, [[first, true, 'pointer'], [first, true, 'focus'],
    [first, false, 'pointer'], [first, false, 'focus']]);
  button.disabled = true;
  button.onpointerenter();
  button.onfocus();
  assert.equal(calls.length, 4);
});

const contextPreview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
const contextFace = { id: 'context-face', operation: 'arranged_face', surface: first,
  managed_by: 'context-owner', managed_key: 'face/joint:wall/cell' };

test('Continue excludes applied manual, generated, and batch faces but preserves unfinished neighbor order', () => {
  const third = { feature: 'clock' }, fourth = { feature: 'plate' }, fifth = { feature: 'other' },
    candidates = [first, second, third, fourth, fifth].map((reference) => ({ reference })),
    recipe = [contextFace, { ...face, id: 'manual', surface: second },
      { ...contextFace, id: 'batch-face', surface: third, managed_by: 'batch' }],
    before = JSON.stringify({ candidates, recipe });
  assert.deepEqual(unbuiltFaceNeighbors(candidates, recipe), candidates.slice(3));
  assert.equal(JSON.stringify({ candidates, recipe }), before);
  assert.deepEqual(unbuiltFaceNeighbors(candidates, []), candidates);
  assert.deepEqual(unbuiltFaceNeighbors(candidates.slice(0, 3), recipe), []);
});

test('Continue does not mark an unbuilt surface done just because it was an input or cutter', () => {
  const candidates = [first, second].map((reference) => ({ reference })),
    owner = { operation: 'build_faces', target: first, surfaces: [first, second], reused_faces: [] },
    differentContext = { ...contextFace, surface: { feature: 'other-joint', surface: 'wall' } };
  assert.deepEqual(unbuiltFaceNeighbors(candidates, [owner, intersection, differentContext]), candidates);
  assert.deepEqual(unbuiltFaceNeighbors(candidates, [contextFace]), [candidates[1]]);
  // Removing the face restores its surface as a next-authoring option.
  assert.deepEqual(unbuiltFaceNeighbors(candidates, [owner]), candidates);
});

test('only valid, actually previewed target cells replace defined-face context', () => {
  const region = { preview: contextPreview, bounded: true, existing_face_id: 'existing', owned_face_id: 'owned' },
    proposal = { faces: [
      { surface: first, regions: [region, { ...region, existing_face_id: 'invalid', owned_face_id: null, preview: null }] },
      { surface: first, blocked_by_geometry: true, regions: [{ ...region, existing_face_id: 'blocked' }] },
      { surface: first, blocked_by_adjacency: true, regions: [{ ...region, existing_face_id: 'unsupported' }] },
      { surface: second, regions: [{ ...region, existing_face_id: 'neighbor' }] },
    ] };
  assert.deepEqual(previewedFaceContextIds(proposal, first), ['existing', 'owned']);
  assert.deepEqual(previewedFaceContextIds({ faces: [{ surface: first, regions: [{ ...region, bounded: false }] }] }, first), []);
  assert.deepEqual(previewedFaceContextIds(null, first), []);
});

test('defined-face context includes manual and other-owner ready faces, but never arbitrary stale results', () => {
  const manual = { ...contextFace, id: 'manual', operation: 'trimmed_face', managed_by: null },
    other = { ...contextFace, id: 'other', managed_by: 'another-owner' },
    stale = { ...contextFace, id: 'stale' }, failed = { ...contextFace, id: 'failed' },
    invalid = { ...contextFace, id: 'invalid' }, empty = { ...contextFace, id: 'empty' },
    state = { token: 'current', recipe: { nodes: [manual, other, stale, failed, invalid, empty, cylinder] },
      states: { manual: 'ready', other: 'ready', stale: 'stale', failed: 'failed', invalid: 'ready', empty: 'ready', wall: 'ready' },
      results: Object.fromEntries(['manual', 'other', 'stale', 'failed', 'wall'].map((id) => [id, { preview: contextPreview }])) };
  state.results.invalid = { preview: { ...contextPreview, indices: [0, 1, 99] } };
  state.results.empty = { preview: { positions: [], indices: [] } };
  assert.deepEqual(definedFaceContext(state).map((record) => record.id), ['manual', 'other']);
  assert.deepEqual(definedFaceContext(state, [], ['manual']).map((record) => record.id), ['other']);
  assert.deepEqual(definedFaceContext({ ...state, recipe: { nodes: [other] } }).map((record) => record.id), ['other']);
});

test('just-applied face previews bridge pending evaluation only for the exact accepted output and token', () => {
  const region = { key: 'cell', arrangement: {}, preview: contextPreview },
    proposal = { faces: [{ surface: first, regions: [region] }] },
    choices = [{ surface: first, region_key: 'cell' }],
    state = { token: 'accepted', recipe: { nodes: [contextFace] },
      states: { 'context-face': 'unevaluated' }, results: {} },
    before = structuredClone({ state, proposal, choices }),
    bridges = appliedFaceContext(state, 'context-owner', proposal, choices);
  assert.equal(bridges.length, 1);
  assert.deepEqual(definedFaceContext(state, bridges), [{ id: 'context-face', result: region }]);
  for (const status of ['stale', 'running'])
    assert.equal(definedFaceContext({ ...state, states: { 'context-face': status } }, bridges).length, 1);
  for (const status of ['failed', 'ready'])
    assert.deepEqual(definedFaceContext({ ...state, states: { 'context-face': status } }, bridges), []);
  assert.deepEqual(definedFaceContext({ ...state, token: 'edited' }, bridges), []);
  assert.deepEqual(definedFaceContext({ ...state, recipe: { nodes: [] } }, bridges), []);
  assert.deepEqual(definedFaceContext({ ...state, recipe: { nodes: [{ ...contextFace, label: 'changed' }] } }, bridges), []);
  const evaluated = { preview: { ...contextPreview, positions: [0, 0, 2, 1, 0, 2, 0, 1, 2] } };
  assert.deepEqual(definedFaceContext({ ...state, states: { 'context-face': 'ready' },
    results: { 'context-face': evaluated } }, bridges), [{ id: 'context-face', result: evaluated }]);
  assert.deepEqual({ state, proposal, choices }, before);
});

test('accepted previews map reused/manual and multi-cell output identities without guessing', () => {
  const manual = { ...contextFace, id: 'manual', managed_by: null },
    secondCell = { ...contextFace, id: 'second-cell', managed_key: 'face/joint:wall/cell2' },
    state = { token: 'accepted', recipe: { nodes: [manual, contextFace, secondCell] } },
    proposal = { faces: [{ surface: first, regions: [
      { key: 'manual-cell', existing_face_id: 'manual', preview: contextPreview },
      { key: 'cell', arrangement: {}, preview: contextPreview },
      { key: 'cell2', arrangement: {}, preview: contextPreview },
    ] }] }, choices = proposal.faces[0].regions.map((region) => ({ surface: first, region_key: region.key }));
  assert.deepEqual(appliedFaceContext(state, 'context-owner', proposal, choices).map((record) => record.id),
    ['manual', 'context-face', 'second-cell']);
  assert.deepEqual(appliedFaceContext(state, 'context-owner', proposal, []), []);
  const ambiguous = { ...state, recipe: { nodes: [contextFace, { ...contextFace, id: 'duplicate' }] } };
  assert.deepEqual(appliedFaceContext(ambiguous, 'context-owner', proposal, choices), []);
});

test('compact neighbor names retain exact context and actionable statuses', () => {
  assert.deepEqual(faceCandidatePresentation({ reference: first, supported: true }, nodes),
    { name: 'Wall', icon: 'cylinder', type: 'Cylinder fit', context: 'Joint',
      detail: 'Cylinder fit · Joint', label: 'Wall — Cylinder fit · Joint', status: '' });
  assert.deepEqual(faceCandidatePresentation({ reference: { feature: 'wall' },
    supported: true, state: 'confirmed' }, nodes),
    { name: 'Wall', icon: 'cylinder', type: 'Cylinder fit', context: null,
      detail: 'Cylinder fit', label: 'Wall — Cylinder fit', status: 'Approved' });
  assert.equal(faceCandidatePresentation({ reference: first, supported: false, conflict: true }, nodes).status, 'Conflict');
  assert.equal(faceCandidatePresentation({ reference: first, supported: false }, nodes).status, 'Unavailable');
});

test('surface labels distinguish datum planes, plane fits and exact solved contexts', () => {
  const datum = { id: 'datum', operation: 'reference_plane', label: 'Shoulder', axis: 'datum-axis' },
    cone = { id: 'cone', operation: 'fit', kind: 'cone', label: 'Taper', selections: [] },
    all = [...nodes, datum, cone];
  assert.deepEqual(surfaceReferencePresentation({ feature: 'datum' }, all), {
    name: 'Shoulder', icon: 'reference_plane', type: 'Reference plane', context: null,
    detail: 'Reference plane', label: 'Shoulder — Reference plane',
  });
  assert.equal(surfaceReferencePresentation({ feature: 'shoulder' }, all).icon, 'plane');
  assert.equal(surfaceReferencePresentation({ feature: 'shoulder' }, all).label, 'Shoulder — Plane fit');
  assert.equal(surfaceReferencePresentation(second, all).label, 'Shoulder — Plane fit · Joint');
  assert.equal(surfaceReferencePresentation({ feature: 'cone' }, all).icon, 'cone');
  assert.equal(surfaceReferencePresentation({ feature: 'cone' }, all).label, 'Taper — Cone fit');
  assert.deepEqual(surfaceReferenceChoices(all).map((choice) => choice.label),
    ['Wall — Cylinder fit', 'Shoulder — Plane fit', 'Wall — Cylinder fit · Joint',
      'Shoulder — Plane fit · Joint', 'Shoulder — Reference plane', 'Taper — Cone fit']);
});

test('presentation uses reference identities rather than parsing user names', () => {
  const all = [{ ...cylinder, label: 'Wall → detail' }, { ...solve, label: 'Joint → context' }];
  assert.equal(faceCandidatePresentation({ label: 'Misleading label', reference: first,
    supported: true }, all).name, 'Wall → detail');
  assert.equal(surfaceReferencePresentation(first, all).context, 'Joint → context');
});

test('hover highlights extend pinned exact-context neighbors without duplicates', () => {
  const direct = { feature: 'wall', surface: null };
  const candidates = [{ reference: first }, { reference: second }, { reference: direct }];
  assert.deepEqual(guidedNeighborHighlights(candidates, [first], candidates[1]), candidates.slice(0, 2));
  assert.deepEqual(guidedNeighborHighlights(candidates, [first], candidates[0]), [candidates[0]]);
  assert.deepEqual(guidedNeighborHighlights(candidates, [first]), [candidates[0]]);
  assert.deepEqual(guidedNeighborHighlights(candidates, [direct], candidates[1]), candidates.slice(1));
});

test('guided inputs retain exact target context and deduplicate chosen neighbor contexts', () => {
  const direct = { feature: 'wall', surface: null };
  assert.deepEqual(guidedSurfaceInputs(first, [first, second, second, direct]), [first, second, direct]);
  assert.deepEqual(guidedSurfaceInputs(null, [second]), []);
  const proposal = { faces: [{ surface: first }, { surface: second }, { surface: direct }] };
  assert.deepEqual(guidedProposalFaces(proposal, first), [proposal.faces[0]]);
});

test('guided boundary defaults require prior approval without contradictory decisions', () => {
  const candidate = { reference: second, supported: true, mathematical: { status: 'candidate' } };
  assert.equal(guidedCandidateSelected(candidate), false);
  assert.equal(guidedCandidateSelected(candidate, [second]), true);
  assert.equal(guidedCandidateSelected({ ...candidate, known_adjacencies: [{ state: 'confirmed' }] }), true);
  assert.equal(guidedCandidateSelected({ ...candidate, known_adjacencies: [{ state: 'rejected' }] }), false);
  assert.equal(guidedCandidateSelected({ ...candidate, state: 'rejected',
    known_adjacencies: [{ state: 'rejected' }] }, [second]), false);
  assert.equal(guidedCandidateSelected({ ...candidate, conflict: true }, [second]), false);
  assert.equal(guidedCandidateSelected({ ...candidate, supported: false }, [second]), false);
  assert.equal(guidedCandidateSelected({ ...candidate, mathematical: { status: 'proven_empty' } }, [second]), false);
});

test('candidate refresh preserves exact current selection, including deliberate rejection overrides', () => {
  const approved = { reference: second, supported: true, state: 'confirmed',
    known_adjacencies: [{ state: 'confirmed' }], mathematical: { status: 'candidate' } };
  assert.equal(guidedCandidateSelected(approved, null), true);
  assert.equal(guidedCandidateSelected(approved, [], true), false);
  assert.equal(guidedCandidateSelected(approved, [second], true), true);
  const rejected = { ...approved, state: 'rejected', known_adjacencies: [{ state: 'rejected' }] };
  assert.equal(guidedCandidateSelected(rejected, [second]), false);
  assert.equal(guidedCandidateSelected(rejected, [second], true), true);
});

test('owner target-incident rejections persist as noncutting participants until explicitly overridden', () => {
  const other = { feature: 'other', surface: null };
  const rejected = { first, second, state: 'rejected' };
  const draft = [rejected, { first: second, second: other, state: 'rejected' },
    { first, second: other, state: 'confirmed' }];
  assert.deepEqual(guidedRejectedDecisions(draft, first, []), [rejected]);
  assert.deepEqual(guidedRejectedDecisions(draft, first, [second]), []);
  const rejectedRefs = guidedRejectedDecisions(draft, first, []).flatMap((choice) => [choice.first, choice.second]);
  assert.deepEqual(guidedSurfaceInputs(first, [other, ...rejectedRefs]), [first, other, second]);
});

test('saved source intent remains identifiable when unavailable; opt-outs do not become defaults', () => {
  const selected = ['good', 'missing', 'unavailable'];
  const original = [...selected];
  const candidates = [{ reference: second, shared_faces: [
    { id: 'good', preview_paths: [{ positions: [0, 0, 0, 1, 0, 0] }] },
    { id: 'unavailable', preview_paths: [], diagnostic: 'edge is open' },
  ] }];
  assert.deepEqual(unavailableGuidedSources(selected, candidates, [second]), ['missing', 'unavailable']);
  assert.deepEqual(selected, original);
  assert.deepEqual(unavailableGuidedSources([], candidates, [second]), []);
  assert.deepEqual(unavailableGuidedSources(selected, candidates, []), selected);
});

test('guided regions do not turn scan suggestions or independent manual faces into approvals', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const proposed = { status: 'suggested', suggested_region_keys: ['suggested'], regions: [
    { key: 'suggested', preview, bounded: true }, { key: 'manual', preview, bounded: true, existing_face_id: 'manual' },
    { key: 'owned', preview, bounded: true, owned_face_id: 'owned' },
    { key: 'prior', preview, bounded: true, previously_selected: true },
    { key: 'invalid', preview: {}, previously_selected: true },
  ] };
  assert.deepEqual(initialGuidedFaceRegions(proposed), ['owned', 'prior']);
  assert.deepEqual(initialGuidedFaceRegions({ ...proposed, blocked_by_geometry: true }), []);
  assert.deepEqual(initialGuidedFaceRegions({ ...proposed, blocked_by_adjacency: true }), []);
  assert.deepEqual(initialGuidedFaceRegions({ ...proposed, regions: proposed.regions.slice(0, 2) }), []);
});

test('neighbor guides show only finite physical edge arcs for this target', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0], indices: [] };
  const candidate = { shared_faces: [{ id: 'neighbor' }] };
  const result = { loops: [{ edges: [
    { preview, sources: ['target'], closed: false },
    { preview, sources: ['target'], closed: true },
    { preview, sources: ['other'] },
    { preview, sources: ['target'], artificial: true },
    { preview, sources: ['target'], seam: true },
  ] }] };
  assert.deepEqual(approvedNeighborPreviewPaths(candidate, { neighbor: result }, 'target'),
    [{ preview, closed: false }, { preview, closed: true }]);
  assert.deepEqual(approvedNeighborPreviewPaths(candidate, { neighbor: result }, null), []);
  assert.deepEqual(approvedNeighborPreviewPaths(candidate, {}, 'target'), []);
});

test('native approved-edge previews distinguish unavailable guidance from mathematical curves', () => {
  const source = { id: 'neighbor', preview_paths: [{ source_id: 'neighbor',
    positions: [0, 0, 0, 1, 0, 0], closed: false }] };
  assert.equal(guidedSourceAvailable(source), true);
  assert.deepEqual(approvedNeighborPreviewPaths({ shared_faces: [source] }, {}, 'target'),
    [{ preview: { positions: [0, 0, 0, 1, 0, 0], indices: [] }, closed: false }]);
  assert.equal(guidedSourceAvailable({ ...source, diagnostic: 'edge is display-clipped' }), false);
  assert.equal(guidedSourceAvailable({ id: 'neighbor', preview_paths: [] }), false);
  assert.equal(guidedSourceAvailable({ id: 'neighbor' }), false);
  assert.equal(guidedSourceAvailable({ preview_paths: [{ positions: [NaN, 0, 0, 1, 0, 0] }] }), false);
  assert.equal(guidedSourceAvailable({ preview_paths: [{ positions: [0, 0, 0] }] }), false);
});

test('approved neighbor faces participate in dependencies but not fitting memberships', () => {
  const arranged = { id: 'arranged', operation: 'arranged_face', surface: first,
    cutters: [second], domains: [], boundary_sources: ['face'], managed_by: 'builder' };
  assert.deepEqual(nodeReferences(arranged), ['joint', 'face', 'builder']);
  const bounded = { ...face, boundary_sources: ['neighbor'] };
  assert.deepEqual(nodeReferences(bounded), ['joint', 'edge', 'neighbor']);
  const builder = { operation: 'build_faces', surfaces: [first, second], boundary_sources: ['face'] };
  assert.deepEqual(nodeReferences(builder), ['joint', 'face']);
  assert.deepEqual(featureVertexIds([...nodes, arranged], { selected: [3, 7] }, 'arranged'), [3, 7]);
});

test('infinite-line previews span only projected primary fit observations, without changing geometry', () => {
  const line = { kind: 'line', origin_display: [1000, -500, 30], direction_display: [3, 4, 0],
    preview: { positions: [999.4, -500.8, 30, 1000.6, -499.2, 30], indices: [] } },
    circle = { kind: 'circle', preview: { positions: [1, 0, 0, 0, 1, 0, -1, 0, 0], indices: [] } },
    geometry = { curves: [line, circle], preview_clipped: true },
    coverage = { ids: [2, 0, 2], positions: new Float64Array([
      1006, -492, 33, 9000, 9000, 9000, 994, -508, 25,
    ]) }, before = structuredClone({ geometry, coverage }),
    paths = intersectionPreviewPaths(geometry, coverage);
  assert.equal(paths.length, 2);
  const expected = [994, -508, 30, 1006, -492, 30];
  paths[0].preview.positions.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 1e-10));
  assert.deepEqual(paths[0].preview.indices, []);
  assert.equal(paths[0].closed, false);
  assert.deepEqual(paths[1], { preview: circle.preview, closed: true });
  assert.deepEqual({ geometry, coverage }, before);
  assert.equal(intersectionPreviewPaths(geometry)[0].preview, line.preview);
});

test('line preview localization is invariant to direction scale/sign and handles large translations', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0], indices: [] },
    origin = [1e12, -1e12, 3], coverage = { ids: [0, 1], positions: [
      1e12 + 10, -1e12 + 7, 5, 1e12 + 20, -1e12 - 4, 1,
    ] };
  for (const magnitude of [1e-200, 1, 1e200, -4]) {
    const paths = intersectionPreviewPaths({ kind: 'line', origin_display: origin,
      direction_display: [magnitude, 0, 0], preview }, coverage),
      points = [paths[0].preview.positions.slice(0, 3), paths[0].preview.positions.slice(3)].sort((a, b) => a[0] - b[0]);
    assert.deepEqual(points, [[1e12 + 10, -1e12, 3], [1e12 + 20, -1e12, 3]]);
  }
});

test('both cylinder generator branches get independent display bounds without selecting a branch', () => {
  const geometry = { kind: 'intersection_preview', preview_only: true, preview_clipped: true,
    curves: [
      { kind: 'line', origin_display: [-2, 1, 100], direction_display: [0, 0, 2], closed: false,
        preview: { positions: [-2, 1, 99, -2, 1, 101], indices: [] } },
      { kind: 'line', origin_display: [2, 1, -100], direction_display: [0, 0, -3], closed: false,
        preview: { positions: [2, 1, -99, 2, 1, -101], indices: [] } },
    ] }, coverage = { ids: [0, 2], positions: [0, 0, 5, 0, 0, 999, 1, 1, 9] },
    before = structuredClone(geometry), paths = intersectionPreviewPaths(geometry, coverage);
  assert.deepEqual(paths, [
    { preview: { positions: [-2, 1, 5, -2, 1, 9], indices: [] }, closed: false },
    { preview: { positions: [2, 1, 9, 2, 1, 5], indices: [] }, closed: false },
  ]);
  assert.deepEqual(geometry, before);
});

test('invalid or degenerate observation coverage keeps the original line display fallback', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0], indices: [] },
    line = { kind: 'line', origin_display: [0, 0, 0], direction_display: [1, 0, 0], preview },
    coverage = { ids: [0, 1], positions: [2, 0, 0, 3, 0, 0] };
  for (const invalid of [null, {}, { ...coverage, ids: [] }, { ...coverage, ids: [-1, 1] },
    { ...coverage, ids: [0, 2] }, { ...coverage, ids: [0.5] }, { ...coverage, ids: [0] },
    { ...coverage, positions: [2, NaN, 0, 3, 0, 0] }, { ...coverage, positions: [2, 0] },
    { ...coverage, positions: [2, 0, 0, 2, 1, 0] }])
    assert.deepEqual(intersectionPreviewPaths(line, invalid), [{ preview, closed: false }]);
  for (const invalid of [{ ...line, direction_display: [0, 0, 0] },
    { ...line, direction_display: [Infinity, 0, 0] }, { ...line, origin_display: [NaN, 0, 0] },
    { ...line, origin_display: [] }])
    assert.deepEqual(intersectionPreviewPaths(invalid, coverage), [{ preview, closed: false }]);
  assert.deepEqual(intersectionPreviewPaths({ ...line, closed: true }, coverage), [{ preview, closed: true }]);
});

test('physical polygon outlines use trimmed loop endpoints rather than infinite-line preview coverage', () => {
  const points = [[-4, -3, 0], [4, -3, 0], [4, 3, 0], [-4, 3, 0]];
  const hole = { positions: [1, 0, 0, 0, 1, 0, -1, 0, 0], indices: [] };
  const bounds = { loops: [{ kind: 'polygon', positions: points }, { kind: 'circle', preview: hole }] };
  const original = structuredClone(bounds);
  assert.deepEqual(faceBoundaryPreviewPaths(bounds), [
    { preview: { positions: points.flat(), indices: [] }, closed: true },
    { preview: hole, closed: true },
  ]);
  assert.deepEqual(bounds, original);
  assert.deepEqual(faceBoundaryPreviewPaths({ loops: [{ kind: 'polygon', positions: [[NaN, 0, 0]] }] }), []);
  assert.deepEqual(faceBoundaryPreviewPaths({ axial: [0, 1] }), []);
});

test('geometry choices preserve explicit direct versus solved-member contexts', () => {
  const choices = surfaceReferenceChoices(nodes);
  assert.deepEqual(choices.map((choice) => choice.reference), [
    { feature: 'wall', surface: null }, { feature: 'shoulder', surface: null }, first, second,
  ]);
  assert.equal(sameSurfaceReference(first, { feature: 'wall', surface: null }), false);
  assert.equal(sameSurfaceReference({ feature: 'wall' }, { feature: 'wall', surface: null }), true);
  assert.deepEqual(eligibleIntersections(nodes, first), [intersection]);
  assert.deepEqual(eligibleIntersections(nodes, { feature: 'wall', surface: null }), []);
});

test('evaluated aggregate membership overrides inferred participants', () => {
  const choices = surfaceReferenceChoices(nodes, { joint: { surfaces: { shoulder: {} } } });
  assert.deepEqual(choices.filter((choice) => choice.reference.feature === 'joint')
    .map((choice) => choice.reference), [second]);
});

test('solver-component output never enlarges declared geometry context membership', () => {
  const extra = { ...plane, id: 'extra', label: 'Extra component plane' };
  const relation = { id: 'parallel', label: 'Declared pair', operation: 'plane_relationship',
    surfaces: ['shoulder'] };
  const equality = { id: 'equal', label: 'Declared radius', operation: 'equal_radii', surfaces: ['wall'] };
  const legacy = { id: 'legacy', label: 'Declared legacy', operation: 'joint_fit', constraints: ['cut'] };
  const cut = { id: 'cut', operation: 'perpendicular', lateral: 'wall', plane: 'shoulder' };
  const candidateNodes = [...nodes, extra, relation, equality, legacy, cut];
  const component = { surfaces: { wall: {}, shoulder: {}, extra: {} } };
  const results = { joint: component, parallel: component, equal: component, legacy: component };
  const choices = surfaceReferenceChoices(candidateNodes, results);
  const members = (id) => choices.filter((choice) => choice.reference.feature === id)
    .map((choice) => choice.reference.surface);
  assert.deepEqual(members('parallel'), ['shoulder']);
  assert.deepEqual(members('equal'), ['wall']);
  assert.deepEqual(members('joint'), ['wall', 'shoulder']);
  assert.deepEqual(members('legacy'), ['wall', 'shoulder']);
});

test('implicit axis geometry is available directly; explicit solves require named member context', () => {
  const boundWall = { ...cylinder, axis: 'axis' };
  const datum = { id: 'datum', label: 'Datum', operation: 'reference_plane', axis: 'axis' };
  const boundPlane = { ...plane, reference_plane: 'datum' };
  const implicitNodes = [boundWall, datum, boundPlane];
  assert.deepEqual(surfaceReferenceChoices(implicitNodes).map((choice) => choice.reference), [
    { feature: 'wall', surface: null }, { feature: 'datum', surface: null },
    { feature: 'shoulder', surface: null },
  ]);
  const explicitNodes = [...implicitNodes, solve];
  assert.deepEqual(surfaceReferenceChoices(explicitNodes).map((choice) => choice.reference), [first, second]);
  // Editing an earlier operation still sees ambiguity introduced later in the recipe.
  assert.deepEqual(surfaceReferenceChoices(implicitNodes, {}, explicitNodes), []);
});

test('unevaluated aggregates do not expose geometry used only as datum initializers', () => {
  const initializer = { ...cylinder, id: 'initializer', label: 'Initializer' };
  const axis = { id: 'axis', operation: 'axis', source_fit: 'initializer' };
  const relation = { id: 'cut', operation: 'perpendicular', lateral: 'wall', plane: 'shoulder' };
  const explicit = { ...solve, factors: ['wall', 'cut'] };
  const legacy = { id: 'legacy', label: 'Legacy joint', operation: 'joint_fit', constraints: ['cut'] };
  const rotation = { id: 'rotation', operation: 'rotational_symmetry', axis: 'axis', planes: ['shoulder'] };
  const legacyRotation = { ...legacy, id: 'legacy-rotation', constraints: ['rotation'] };
  const choices = surfaceReferenceChoices([initializer, axis, cylinder, plane,
    relation, rotation, explicit, legacy, legacyRotation]);
  assert.deepEqual(choices.filter((choice) => choice.reference.feature === 'joint')
    .map((choice) => choice.reference.surface), ['wall']);
  assert.deepEqual(choices.filter((choice) => choice.reference.feature === 'legacy')
    .map((choice) => choice.reference.surface), ['wall', 'shoulder']);
  assert.deepEqual(choices.filter((choice) => choice.reference.feature === 'legacy-rotation')
    .map((choice) => choice.reference.surface), ['shoulder']);
});

test('unsupported primitives are not offered as face geometry', () => {
  assert.deepEqual(surfaceReferenceChoices([{ id: 'ball', operation: 'fit', kind: 'sphere' }]), []);
  assert.deepEqual(boundaryKeepOptions('plane').map((choice) => choice.value), ['inside', 'outside']);
  assert.deepEqual(boundaryKeepOptions('cone').map((choice) => choice.value), ['positive', 'negative']);
});

test('retained sides distinguish planar lines from closed loops and lateral cuts', () => {
  const values = (kind, result) => boundaryKeepOptions(kind, result).map((choice) => choice.value);
  assert.deepEqual(values('plane', { kind: 'circle' }), ['inside', 'outside']);
  assert.deepEqual(values('plane', { kind: 'surface_intersection', curves: [{ kind: 'ellipse' }] }),
    ['inside', 'outside']);
  assert.deepEqual(values('plane', { kind: 'surface_intersection', curves: [{ kind: 'line' }] }),
    ['positive', 'negative']);
  assert.deepEqual(values('plane', { kind: 'line' }), ['positive', 'negative']);
  assert.deepEqual(values('cylinder', { curves: [{ kind: 'ellipse' }] }), ['positive', 'negative']);
});

test('intersection preview paths preserve separate branches and explicit closedness', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 1, 1, 0], indices: [] };
  const circle = { kind: 'circle', preview };
  assert.deepEqual(intersectionCurves(circle), [circle]);
  assert.deepEqual(intersectionPreviewPaths(circle), [{ preview, closed: true }]);
  const curves = [{ kind: 'ellipse', closed: true, preview },
    { kind: 'line', closed: false, preview }, { kind: 'spline', closed: true, preview },
    { kind: 'circle', closed: false, preview }];
  assert.deepEqual(intersectionCurves({ kind: 'surface_intersection', curves, preview }), curves);
  assert.deepEqual(intersectionPreviewPaths({ curves, preview }), [true, false, true, false]
    .map((closed) => ({ preview, closed })));
  assert.deepEqual(intersectionPreviewPaths({ curves: [], preview }), []);
  assert.deepEqual(intersectionPreviewPaths(), []);
  assert.deepEqual(intersectionPreviewPaths({ curves: [{ kind: 'line',
    preview: { positions: [NaN, 0, 0, 1, 0, 0], indices: [] } },
  { kind: 'line', preview: { positions: [0, 0, 0], indices: [] } }] }), []);
});

test('physical extent details accept intervals and generalized loop/cut records', () => {
  assert.deepEqual(physicalBoundsSummary({ radial: [0, 3], axial: [null, 5] }), [
    ['Radial bounds', '0.00000 → 3.00000'], ['Axial bounds', 'unbounded → 5.00000'],
  ]);
  assert.deepEqual(physicalBoundsSummary({ loops: [{ role: 'outer' }, { role: 'hole' }],
    cuts: [{ offset: 1, axis: [0, 0, 1] }] }), [['Boundary loops', 2], ['Cutting planes', 1]]);
  assert.deepEqual(physicalBoundsSummary({ loops: { outer: 'edge', holes: ['hole'] } }),
    [['Boundary loops', '{"outer":"edge","holes":["hole"]}']]);
  assert.deepEqual(physicalBoundsSummary(), []);
  assert.deepEqual(physicalBoundsSummary({ arrangement: { observations: [[1, 2, 3]], selector: {} } }),
    [['Boundary construction', 'Reviewed native arrangement cell']]);
});

test('arrangement loop paths draw finite arcs separately and do not promote seams or artificial edges', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0], indices: [] };
  const loops = [{ closed: true, edges: [
    { kind: 'circle', closed: false, preview },
    { kind: 'line', closed: false, preview },
    { kind: 'circle', closed: true, preview },
    { preview, artificial: true }, { preview, seam: true },
    { preview: { positions: [NaN, 0, 0, 1, 0, 0], indices: [] } },
  ] }];
  assert.deepEqual(faceBoundaryPreviewPaths({}, loops), [
    { preview, closed: false }, { preview, closed: false }, { preview, closed: true },
  ]);
  assert.deepEqual(faceBoundaryPreviewPaths({ loops }), faceBoundaryPreviewPaths({}, loops));
});

test('unevaluated solves expose mirror-member geometry without its datum initializers', () => {
  const mirrored = { ...plane, id: 'mirrored', label: 'Mirrored plane' },
    initializer = { ...plane, id: 'initializer', label: 'Datum initializer' },
    datum = { id: 'datum', operation: 'reference_plane', source_fit: 'initializer' },
    mirror = { id: 'mirror', operation: 'mirror_symmetry',
      plane: 'datum', surfaces: ['shoulder', 'mirrored'] },
    mirroredSolve = { ...solve, factors: ['wall', 'mirror'] },
    candidates = [cylinder, plane, mirrored, initializer, datum, mirror, mirroredSolve];
  assert.deepEqual(surfaceReferenceChoices(candidates)
    .filter((choice) => choice.reference.feature === 'joint')
    .map((choice) => choice.reference.surface), ['wall', 'shoulder', 'mirrored']);
});

test('add all fits excludes datums and unsupported primitives, preserving existing selections', () => {
  const datum = { id: 'datum', label: 'Construction plane', operation: 'reference_plane' },
    sphere = { id: 'sphere', operation: 'fit', kind: 'sphere' },
    cone = { id: 'cone', operation: 'fit', kind: 'cone' },
    candidates = [...nodes, datum, sphere, cone],
    selected = [{ feature: 'datum', surface: null }, first],
    result = addFittedSurfaceReferences(candidates, surfaceReferenceChoices(candidates), selected);
  assert.deepEqual(result, { references: [...selected,
    { feature: 'shoulder', surface: null }, { feature: 'cone', surface: null }], ambiguous: [] });
  assert.deepEqual(selected, [{ feature: 'datum', surface: null }, first]);
  assert.deepEqual(addFittedSurfaceReferences(candidates, surfaceReferenceChoices(candidates), result.references), result);
  const fromEmpty = addFittedSurfaceReferences(candidates, surfaceReferenceChoices(candidates));
  assert.deepEqual(fromEmpty.references, [{ feature: 'wall', surface: null },
    { feature: 'shoulder', surface: null }, { feature: 'cone', surface: null }]);
});

test('add all fits uses an unambiguous named context when direct geometry is unavailable', () => {
  const bound = [{ ...cylinder, axis: 'axis' }, { ...plane, axis: 'axis' }, solve];
  assert.deepEqual(addFittedSurfaceReferences(bound, surfaceReferenceChoices(bound)),
    { references: [first, second], ambiguous: [] });
});

test('add all fits does not guess among multiple explicit solve contexts', () => {
  const another = { ...solve, id: 'another-joint', label: 'Other joint' },
    bound = [{ ...cylinder, axis: 'axis' }, { ...plane, axis: 'axis' }, solve, another],
    choices = surfaceReferenceChoices(bound);
  assert.deepEqual(addFittedSurfaceReferences(bound, choices),
    { references: [], ambiguous: ['Wall', 'Shoulder'] });
  assert.deepEqual(addFittedSurfaceReferences(bound, choices, [first]),
    { references: [first], ambiguous: ['Shoulder'] });
});

test('add all fits cannot select surfaces absent from the current picker', () => {
  assert.deepEqual(addFittedSurfaceReferences(nodes, []), { references: [], ambiguous: [] });
});

test('trims retain graph dependencies and cannot move before shared edges', () => {
  assert.deepEqual(nodeReferences(intersection), ['joint']);
  assert.deepEqual(nodeReferences(face), ['joint', 'edge']);
  assert.match(actionMove(nodes, 'face', nodes.indexOf(intersection)).error, /Wall face needs Shared edge earlier/);
  const graph = buildFeatureGraph({ nodes });
  assert.ok(graph.edges.some((edge) => edge.from === 'edge' && edge.to === 'face'));
});

test('inspection highlights source observations without changing memberships', () => {
  const memberships = { selected: [7, 3, 9] };
  assert.deepEqual(featureVertexIds(nodes, memberships, 'edge'), [3, 7, 9]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'face'), [3, 7, 9]);
  assert.deepEqual(memberships, { selected: [7, 3, 9] });
});

test('adding physical geometry preserves existing transform output', () => {
  assert.equal(geometryAppendOutput([{ id: 'output', operation: 'transform' }], 'output', 'face'), 'output');
  assert.equal(geometryAppendOutput(nodes, 'joint', 'face'), 'face');
});

test('batch region defaults preserve reviewed faces but never guess ambiguous regions', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const face = { status: 'suggested', suggested_region_key: 'annulus',
    regions: [{ key: 'disk', preview, bounded: true }, { key: 'annulus', preview, bounded: true }] };
  assert.equal(initialBuildFaceRegion(face), 'annulus');
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous' }), null);
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous',
    regions: [{ key: 'disk', existing_face_id: 'manual', preview, bounded: true }, { key: 'annulus', preview, bounded: true }] }), 'disk');
  assert.equal(initialBuildFaceRegion({ ...face, regions: [{ key: 'disk', existing_face_id: 'manual', preview, bounded: true },
    { key: 'annulus', owned_face_id: 'owned', preview, bounded: true }] }), 'annulus');
  assert.equal(initialBuildFaceRegion({ ...face, regions: [{ key: 'disk', previously_selected: true,
    existing_face_id: 'manual', preview, bounded: true }, { key: 'annulus', owned_face_id: 'owned', preview, bounded: true }] }), 'disk');
  const severalExisting = [{ key: 'disk', existing_face_id: 'first', preview, bounded: true },
    { key: 'annulus', existing_face_id: 'second', preview, bounded: true }];
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous', regions: severalExisting }), null);
  assert.equal(initialBuildFaceRegion({ ...face, regions: severalExisting }), 'annulus');
});

test('batch apply sends only accepted valid region keys, never client geometry', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const proposal = { faces: [
    { key: 'wall', surface: first, regions: [{ key: 'upper', preview, bounded: true }] },
    { key: 'shoulder', surface: second, regions: [{ key: 'annulus', preview, bounded: true }] },
  ] };
  const review = new Map([['wall', { accepted: true, region_key: 'upper' }],
    ['shoulder', { accepted: false, region_key: 'annulus' }]]);
  assert.deepEqual(buildFaceChoices(proposal, review), [{ surface: first, region_key: 'upper' }]);
  review.set('wall', { accepted: true, region_key: 'stale' });
  assert.deepEqual(buildFaceChoices(proposal, review), []);
});

test('open regions stay accounted for but cannot be suggested or applied by the browser', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const face = { key: 'cells', surface: first, status: 'suggested',
    suggested_region_keys: ['left', 'right', 'missing', 'unavailable'],
    regions: [{ key: 'left', preview, bounded: true },
      { key: 'right', preview, bounded: false, preview_clipped: true },
      { key: 'unavailable', preview: { positions: [], indices: [] } }] };
  assert.deepEqual(suggestedBuildFaceRegions(face), ['left']);
  assert.deepEqual(initialBuildFaceRegions(face), ['left']);
  assert.deepEqual(buildFaceChoices({ faces: [face] }, new Map([
    ['cells', { region_keys: ['right', 'left', 'right', 'missing', 'unavailable'] }],
  ])), [{ surface: first, region_key: 'left' }]);
  assert.equal(face.regions[1].bounded, false);
  assert.equal(face.regions[1].preview_clipped, true);
  assert.deepEqual(suggestedBuildFaceRegions({ ...face, blocked_by_adjacency: true }), []);
  assert.deepEqual(initialBuildFaceRegions({ ...face, blocked_by_adjacency: true }), []);
  assert.deepEqual(buildFaceChoices({ faces: [{ ...face, blocked_by_adjacency: true }] },
    new Map([['cells', { region_keys: ['left', 'right'] }]])), []);
  assert.deepEqual(suggestedBuildFaceRegions({ ...face, status: 'requires_adjacency_review' }), []);
  assert.deepEqual(initialBuildFaceRegions({ ...face, status: 'requires_adjacency_review' }), []);
  assert.deepEqual(suggestedBuildFaceRegions({ ...face, status: 'ambiguous' }), []);
});

test('reopening a multi-cell batch preserves all valid reviewed cells but not stale choices', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const face = { key: 'cells', surface: first, status: 'ambiguous', regions: [
    { key: 'left', previously_selected: true, preview, bounded: true },
    { key: 'right', owned_face_id: 'owned-right', preview, bounded: true },
    { key: 'other', preview, bounded: true },
    { key: 'stale', previously_selected: true, preview: { positions: [], indices: [] } },
  ] };
  assert.deepEqual(initialBuildFaceRegions(face), ['left', 'right']);
  assert.deepEqual(buildFaceChoices({ faces: [face] },
    new Map([['cells', { region_keys: ['right'] }]])), [{ surface: first, region_key: 'right' }]);
  assert.deepEqual(buildFaceChoices({ faces: [face] },
    new Map([['cells', { region_keys: [] }]])), []);
  assert.deepEqual(initialBuildFaceRegions({ ...face, status: 'requires_adjacency_review' }), []);
});

test('large cell reviews fold only unselected, unpopulated candidates without dropping any', () => {
  const regions = Array.from({ length: 1500 }, (_, index) => ({ key: `cell-${index}`, bounded: true,
    evidence: { interior_count: index < 140 ? 1 : 0, boundary_count: index === 140 ? 1 : 0 } }));
  regions[141].previously_selected = true;
  regions[142].owned_face_id = 'retained';
  regions[143].existing_face_id = 'manual';
  const face = { regions, suggested_region_keys: ['cell-144'] };
  const before = JSON.stringify(face);
  const groups = buildFaceRegionGroups(face, ['cell-145', 'missing', 'cell-145']);
  assert.equal(groups.primary.length, 146);
  assert.equal(groups.other.length, 1354);
  assert.deepEqual(groups.primary, regions.slice(0, 146));
  assert.deepEqual(groups.other, regions.slice(146));
  assert.equal(new Set([...groups.primary, ...groups.other]).size, regions.length);
  assert.equal(JSON.stringify(face), before);
  // A checked candidate remains accessible and moves upfront on rerender.
  const rerender = buildFaceRegionGroups(face, ['cell-1499']);
  assert.equal(rerender.primary.at(-1), regions[1499]);
  assert.ok(!rerender.other.includes(regions[1499]));
  assert.ok(rerender.other.includes(regions[145]));
  assert.deepEqual(buildFaceRegionGroups({ regions: [] }), { primary: [], other: [] });
});

test('geometry-blocked cells cannot default, become suggestions, or enter apply choices', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const face = { key: 'cell', surface: first, status: 'suggested', blocked_by_geometry: true,
    suggested_region_keys: ['region'], regions: [{ key: 'region', previously_selected: true,
      preview, bounded: true, evidence: { interior_count: 1 } }] };
  assert.deepEqual(suggestedBuildFaceRegions(face), []);
  assert.deepEqual(initialBuildFaceRegions(face), []);
  assert.deepEqual(buildFaceChoices({ faces: [face] },
    new Map([['cell', { region_keys: ['region'] }]])), []);
  // Valid explicitly reviewed legacy partial regions are not blocked merely
  // because another unsupported candidate produced a diagnostic.
  const legacyPartial = { ...face, status: 'unsupported', blocked_by_geometry: false };
  assert.deepEqual(buildFaceChoices({ faces: [legacyPartial] },
    new Map([['cell', { region_keys: ['region'] }]])), [{ surface: first, region_key: 'region' }]);
});

test('batch candidates without nonempty valid triangle geometry cannot default or apply', () => {
  for (const preview of [{ positions: [], indices: [] }, { positions: [0, 0, 0], indices: [] },
    { positions: [0, 0, 0], indices: [0, 1, 2] }]) {
    const region = { key: 'empty', owned_face_id: 'old', previously_selected: true, preview };
    const face = { key: 'face', surface: first, status: 'suggested', suggested_region_key: 'empty', regions: [region] };
    assert.equal(validBuildFaceRegion(region), false);
    assert.equal(initialBuildFaceRegion(face), null);
    assert.deepEqual(buildFaceChoices({ faces: [face] },
      new Map([['face', { accepted: true, region_key: 'empty' }]])), []);
  }
});

test('batch owners depend on inputs and reused manual geometry, never generated children', () => {
  const owner = { id: 'batch', operation: 'build_faces', surfaces: [first, second],
    reused_faces: ['face'], reused_intersections: ['edge'] };
  const child = { ...face, id: 'generated-face', managed_by: 'batch' };
  assert.deepEqual(nodeReferences(owner), ['joint', 'face', 'edge']);
  assert.deepEqual(nodeReferences(child), ['joint', 'edge', 'batch']);
  const memberships = { selected: [3, 7] };
  assert.deepEqual(featureVertexIds([...nodes, owner], memberships, 'batch'), [3, 7]);
  const graph = buildFeatureGraph({ nodes: [...nodes, owner, child] }, { showGenerated: true });
  assert.ok(graph.edges.some((edge) => edge.from === 'batch' && edge.to === 'generated-face'));
});

test('batch evidence explicitly distinguishes boundary and conflicting observations', () => {
  assert.equal(faceEvidenceSummary({ interior_count: 10, boundary_count: 2, elsewhere_count: 3,
    undefined_count: 1, total_count: 16 }),
  '10 interior · 2 near boundary · 3 elsewhere · 1 undefined / 16 observations');
});

test('physical adjacency decisions preserve contexts and serialize only explicit selected-pair reviews', () => {
  const direct = { feature: 'wall', surface: null };
  const draft = [
    { first, second, state: 'confirmed' },
    { first: second, second: { ...first, surface: 'wall' }, state: 'rejected' },
    { first, second: direct, state: 'confirmed' },
    { first: direct, second, state: 'proposed' },
    { first: direct, second, state: 'uncertain' },
    { first, second: first, state: 'confirmed' },
  ];
  assert.equal(adjacencyPairKey(first, second), adjacencyPairKey(second, first));
  assert.notEqual(adjacencyPairKey(first, second), adjacencyPairKey(direct, second));
  assert.deepEqual(buildAdjacencyDecisions(draft, [first, second]), [
    { first: second, second: first, state: 'rejected' },
  ]);
  assert.deepEqual(buildAdjacencyDecisions([{ first: { feature: 'wall' }, second, state: 'confirmed' }],
    [direct, second]), [{ first: { feature: 'wall' }, second, state: 'confirmed' }]);
  assert.deepEqual(draft[0], { first, second, state: 'confirmed' });
});

test('declared face scope choices use exact geometry context and exclude owner descendants', () => {
  const owner = { id: 'batch', operation: 'build_faces', surfaces: [first, second],
    reused_faces: [], reused_intersections: [] };
  const ownedEdge = { ...intersection, id: 'owned-edge', managed_by: 'batch' };
  const ownedFace = { ...face, id: 'owned-face', managed_by: 'batch' };
  const dependentFace = { ...face, id: 'dependent-face',
    boundaries: [{ intersection: 'owned-edge', keep: 'positive' }] };
  const differentContext = { ...face, id: 'direct-face', surface: { feature: 'wall', surface: null } };
  const scopeNodes = [...nodes, owner, ownedEdge, ownedFace, dependentFace, differentContext];
  assert.deepEqual(faceScopeChoices(scopeNodes, first, 'batch').map((node) => node.id), ['face']);
  assert.deepEqual(faceScopeChoices(scopeNodes, { feature: 'wall' }, 'batch').map((node) => node.id), ['direct-face']);
  assert.deepEqual(faceScopeChoices(scopeNodes, second, 'batch'), []);
  assert.deepEqual(faceScopeChoices(scopeNodes, first).map((node) => node.id),
    ['face', 'owned-face', 'dependent-face']);
});

test('face scope payload filters selected surfaces and eligible exact-context faces without mutating drafts', () => {
  const draft = [{ surface: first, faces: ['face', 'face', 'missing', 'edge'] },
    { surface: second, faces: ['face'] }, { surface: { feature: 'wall' }, faces: ['direct'] }];
  assert.deepEqual(buildFaceScopes(draft, [first, second], nodes), [{ surface: first, faces: ['face'] }]);
  assert.deepEqual(buildFaceScopes([], [first], nodes), []);
  assert.deepEqual(draft[0].faces, ['face', 'face', 'missing', 'edge']);
});

test('arranged faces retain cutter/domain dependencies and cannot scope their own owner', () => {
  const owner = { id: 'batch', operation: 'build_faces', surfaces: [first, second] };
  const arranged = { id: 'arranged', label: 'Reviewed cell', operation: 'arranged_face',
    surface: first, cutters: [second, { feature: 'cut-plane', surface: null }],
    domains: ['face'], selector: { signs: { cutter: 'positive' }, component_count: 1,
      witness_chart: [1, 2] } };
  const owned = { ...arranged, id: 'owned-arranged', managed_by: 'batch' };
  const dependent = { ...arranged, id: 'dependent-arranged', domains: ['owned-arranged'] };
  const scopeNodes = [...nodes, owner, arranged, owned, dependent];
  assert.deepEqual(nodeReferences(arranged), ['joint', 'cut-plane', 'face']);
  assert.deepEqual(nodeReferences(owned), ['joint', 'cut-plane', 'face', 'batch']);
  assert.deepEqual(faceScopeChoices(scopeNodes, first, 'batch').map((node) => node.id),
    ['face', 'arranged']);
  assert.deepEqual(buildFaceScopes([{ surface: first, faces: ['arranged', 'owned-arranged', 'dependent-arranged'] }],
    [first], scopeNodes, 'batch'), [{ surface: first, faces: ['arranged'] }]);
  const graph = buildFeatureGraph({ nodes: scopeNodes }, { showGenerated: true });
  assert.ok(graph.edges.some((edge) => edge.from === 'face' && edge.to === 'arranged'));
});

test('unresolved adjacency cannot default a partial region; confirmed unsupported adjacency blocks apply', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const row = { key: 'partial', surface: first, status: 'requires_adjacency_review',
    suggested_region_key: 'upper', regions: [{ key: 'upper', previously_selected: true, preview, bounded: true }] };
  const review = new Map([['partial', { accepted: true, region_key: 'upper' }]]);
  assert.equal(initialBuildFaceRegion(row), null);
  assert.deepEqual(buildFaceChoices({ faces: [row] }, review), [{ surface: first, region_key: 'upper' }]);
  assert.equal(initialBuildFaceRegion({ ...row, blocked_by_adjacency: true }), null);
  assert.deepEqual(buildFaceChoices({ faces: [{ ...row, blocked_by_adjacency: true }] }, review), []);
});

test('preview rejects nonfinite positions and invalid triangle indices', () => {
  assert.equal(validGeometryPreview({ positions: [0, 0, 0], indices: [] }), true);
  assert.equal(validGeometryPreview({ positions: [0, 0, 0], indices: [0, 0, 0] }), true);
  assert.equal(validGeometryPreview({ positions: [0, 0, NaN], indices: [] }), false);
  assert.equal(validGeometryPreview({ positions: [0, 0, 0], indices: [0, 0, 1] }), false);
  assert.equal(validGeometryPreview({ positions: [0, 0, 0], indices: [0] }), false);
});
