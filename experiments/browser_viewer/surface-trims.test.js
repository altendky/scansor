import test from 'node:test';
import assert from 'node:assert/strict';
import { surfaceReferenceChoices, eligibleIntersections, sameSurfaceReference,
  boundaryKeepOptions, geometryAppendOutput, validGeometryPreview, initialBuildFaceRegion,
  buildFaceChoices, faceEvidenceSummary, validBuildFaceRegion, addFittedSurfaceReferences,
  adjacencyPairKey, buildAdjacencyDecisions, faceScopeChoices, buildFaceScopes } from './surface-trims.js';
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
    regions: [{ key: 'disk', preview }, { key: 'annulus', preview }] };
  assert.equal(initialBuildFaceRegion(face), 'annulus');
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous' }), null);
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous',
    regions: [{ key: 'disk', existing_face_id: 'manual', preview }, { key: 'annulus', preview }] }), 'disk');
  assert.equal(initialBuildFaceRegion({ ...face, regions: [{ key: 'disk', existing_face_id: 'manual', preview },
    { key: 'annulus', owned_face_id: 'owned', preview }] }), 'annulus');
  assert.equal(initialBuildFaceRegion({ ...face, regions: [{ key: 'disk', previously_selected: true,
    existing_face_id: 'manual', preview }, { key: 'annulus', owned_face_id: 'owned', preview }] }), 'disk');
  const severalExisting = [{ key: 'disk', existing_face_id: 'first', preview },
    { key: 'annulus', existing_face_id: 'second', preview }];
  assert.equal(initialBuildFaceRegion({ ...face, status: 'ambiguous', regions: severalExisting }), null);
  assert.equal(initialBuildFaceRegion({ ...face, regions: severalExisting }), 'annulus');
});

test('batch apply sends only accepted valid region keys, never client geometry', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const proposal = { faces: [
    { key: 'wall', surface: first, regions: [{ key: 'upper', preview }] },
    { key: 'shoulder', surface: second, regions: [{ key: 'annulus', preview }] },
  ] };
  const review = new Map([['wall', { accepted: true, region_key: 'upper' }],
    ['shoulder', { accepted: false, region_key: 'annulus' }]]);
  assert.deepEqual(buildFaceChoices(proposal, review), [{ surface: first, region_key: 'upper' }]);
  review.set('wall', { accepted: true, region_key: 'stale' });
  assert.deepEqual(buildFaceChoices(proposal, review), []);
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

test('unresolved adjacency cannot default a partial region; confirmed unsupported adjacency blocks apply', () => {
  const preview = { positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2] };
  const row = { key: 'partial', surface: first, status: 'requires_adjacency_review',
    suggested_region_key: 'upper', regions: [{ key: 'upper', previously_selected: true, preview }] };
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
