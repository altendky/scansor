import assert from 'node:assert/strict';
import {
  actionDescription,
  actionMove,
  discoverReuseLineage,
  managedOwnerId,
  managedSubtreeIds,
  nodeReferences,
  reconcileFeatureReuse,
} from './action-tree.js';

const source = { id: 'source', label: 'Scan', operation: 'source' };
const a = { id: 'a', label: 'Side', operation: 'selection', source: 'source' };
const b = { id: 'b', label: 'End', operation: 'selection', source: 'source' };
const c = { id: 'c', label: 'Other end', operation: 'selection', source: 'source' };
const fit = {
  id: 'fit',
  label: 'Cylinder',
  operation: 'fit',
  selections: ['a'],
  kind: 'cylinder',
  axial_domain: [-2, 5],
};
const nodes = [source, a, b, fit];
assert.deepEqual(nodeReferences({ operation: 'body', faces: ['a', 'b', 'a'] }), ['a', 'b']);
assert.equal(actionDescription({ operation: 'body' }, 'ready'), 'Solid body · Ready');
const ids = (result) => result.nodes.map((node) => node.id);

assert.deepEqual(ids(actionMove(nodes, 'b', 1)), ['source', 'b', 'a', 'fit']);
assert.deepEqual(ids(actionMove(nodes, 'a', 3)), ['source', 'b', 'a', 'fit']);
assert.deepEqual(ids(actionMove(nodes, 'b', 4)), ['source', 'a', 'fit', 'b']);
assert.equal(actionMove(nodes, 'b', 2).changed, false);
assert.equal(actionMove(nodes, 'b', 3).changed, false);
assert.match(actionMove(nodes, 'a', 4).error, /Cylinder needs Side earlier/);
assert.match(actionMove(nodes, 'source', 2).error, /Side needs Scan earlier/);
assert.match(actionMove(nodes, 'fit', 1).error, /Cylinder needs Side earlier/);
assert.ok(actionMove(nodes, 'missing', 1).error);
assert.ok(actionMove(nodes, 'a', -1).error);
assert.ok(actionMove(nodes, 'a', 5).error);
assert.deepEqual(nodes, [source, a, b, fit]);
assert.equal(actionMove(nodes, 'b', 4).nodes[3], b);

// Groups are stable blocks, including the full subtree of each managed owner.
const facesOwner = { id: 'faces-owner', label: 'Build faces', operation: 'build_faces',
  surfaces: [{ feature: 'fit' }], group_id: 'face-group' };
const edge = { id: 'edge', label: 'Circular edge', operation: 'surface_intersection',
  first: { feature: 'fit' }, second: { feature: 'fit' },
  managed_by: 'faces-owner', managed_key: 'edge' };
const face = { id: 'face', label: 'Trimmed face', operation: 'trimmed_face',
  surface: { feature: 'fit' }, boundaries: [{ intersection: 'edge' }],
  managed_by: 'faces-owner', managed_key: 'face' };
const loose = { id: 'loose', label: 'Independent point', operation: 'point' };
const faceNodes = [source, a, fit, facesOwner, edge, face, loose];
assert.deepEqual(nodeReferences({ ...facesOwner,
  face_scopes: [{ surface: { feature: 'fit' }, faces: ['manual-face'] }],
}), ['fit', 'manual-face']);
const movedFaces = actionMove(faceNodes, 'faces-owner', faceNodes.length);
assert.deepEqual(ids(movedFaces), ['source', 'a', 'fit', 'loose', 'faces-owner', 'edge', 'face']);
assert.equal(movedFaces.nodes[4], facesOwner);
assert.equal(movedFaces.nodes[5], edge);
assert.equal(movedFaces.nodes[6], face);
assert.deepEqual(ids(actionMove(movedFaces.nodes, 'faces-owner', 3)), faceNodes.map((n) => n.id));
assert.equal(actionMove(faceNodes, 'faces-owner', 4).changed, false);
assert.match(actionMove(faceNodes, 'faces-owner', 2).error, /Build faces needs Cylinder earlier/);
const usesFace = { id: 'uses-face', label: 'Face consumer', operation: 'joint_fit', constraints: ['face'] };
assert.match(actionMove([...faceNodes, usesFace], 'faces-owner', 8).error, /Face consumer needs Trimmed face earlier/);
assert.match(actionMove(faceNodes, ['edge', 'face'], 7).error, /within their owner/);
assert.match(actionMove(faceNodes, ['edge', 'face'], 3).error, /within their owner/);
assert.deepEqual(faceNodes, [source, a, fit, facesOwner, edge, face, loose]);
const cutter = { id: 'cutter', label: 'Cutting plane', operation: 'fit', kind: 'plane', selections: ['b'] };
const arranged = { id: 'arranged', label: 'Retained cell', operation: 'arranged_face',
  surface: { feature: 'fit' }, cutters: [{ feature: 'cutter' }], domains: ['face'],
  selector: { signs: { cutter: 'positive' }, component_count: 1, witness_chart: [0, 0] } };
const arrangedNodes = [...faceNodes, b, cutter, arranged];
assert.deepEqual(nodeReferences(arranged), ['fit', 'cutter', 'face']);
assert.match(actionDescription(arranged, 'ready'), /Arranged face.*Ready/);
assert.match(actionMove(arrangedNodes, 'arranged', arrangedNodes.indexOf(cutter)).error,
  /Retained cell needs Cutting plane earlier/);
assert.match(actionMove(arrangedNodes, 'faces-owner', arrangedNodes.length).error,
  /Retained cell needs Trimmed face earlier/);
assert.ok(actionMove(faceNodes, [], 4).error);
assert.ok(actionMove(faceNodes, ['faces-owner', 'missing'], 4).error);
// Multiple and noncontiguous members coalesce without changing memberships.
assert.deepEqual(ids(actionMove(faceNodes, ['fit', 'faces-owner'], 7)),
  ['source', 'a', 'loose', 'fit', 'faces-owner', 'edge', 'face']);
const noncontiguous = [source, a, loose, b, fit];
assert.deepEqual(ids(actionMove(noncontiguous, ['a', 'b'], 1)), ['source', 'a', 'b', 'loose', 'fit']);
assert.match(actionMove(noncontiguous, ['a', 'b'], 5).error, /Cylinder needs Side earlier/);
// Independent generated siblings may swap, but stay inside their owner.
const otherEdge = { ...edge, id: 'other-edge', label: 'Other edge', managed_key: 'other-edge' };
const siblingNodes = [source, a, fit, facesOwner, edge, otherEdge, face, loose];
assert.deepEqual(ids(actionMove(siblingNodes, ['other-edge'], 4)),
  ['source', 'a', 'fit', 'faces-owner', 'other-edge', 'edge', 'face', 'loose']);
assert.match(actionMove(siblingNodes, ['edge'], 7).error, /Trimmed face needs Circular edge earlier/);

const axis = { id: 'axis', label: 'Axis', operation: 'axis', source_fit: 'fit' };
const manualAxis = {
  id: 'manual-axis',
  label: 'Manual axis',
  operation: 'axis',
  initial_parameters: [0, 0, 0, 0],
};
assert.deepEqual(nodeReferences(axis), ['fit']);
assert.deepEqual(nodeReferences(manualAxis), []);
assert.deepEqual(
  nodeReferences({
    id: 'point-axis',
    label: 'Point axis',
    operation: 'axis',
    source_points: ['point-a', 'point-b'],
  }),
  ['point-a', 'point-b'],
);
const point = { id: 'point', label: 'Center', operation: 'point', source_fit: 'sphere' };
const manualPoint = {
  id: 'manual-point',
  label: 'Initial center',
  operation: 'point',
  initial_coordinates: [0, 0, 0],
};
assert.deepEqual(nodeReferences(point), ['sphere']);
assert.deepEqual(nodeReferences(manualPoint), []);
assert.deepEqual(
  nodeReferences({
    id: 'scale',
    operation: 'scale',
    distances: [
      { first_point: 'point-a', second_point: 'point-b', known_distance: 10 },
      { first_point: 'point-a', second_point: 'point-c', known_distance: 20 },
    ],
  }),
  ['point-a', 'point-b', 'point-c'],
);
assert.deepEqual(
  nodeReferences({
    id: 'frame',
    operation: 'frame',
    origin_point: 'point-a',
    primary_reference: 'up-plane',
    secondary_reference: 'forward-axis',
  }),
  ['point-a', 'up-plane', 'forward-axis'],
);
assert.deepEqual(
  nodeReferences({ id: 'transform', operation: 'transform', frame: 'frame', scale: 'scale' }),
  ['frame', 'scale'],
);
assert.deepEqual(
  nodeReferences({
    id: 'point-bound-sphere',
    operation: 'fit',
    selections: ['b'],
    point: 'point',
  }),
  ['b', 'point'],
);
const boundPlane = {
  id: 'plane',
  label: 'Plane factor',
  operation: 'fit',
  selections: ['b'],
  axis: 'axis',
};
const solve = {
  id: 'solve',
  label: 'Shared solve',
  operation: 'axis_solve',
  axis: 'axis',
  factors: ['fit', 'plane'],
};
const axisNodes = [...nodes, axis, boundPlane, solve];
assert.equal(actionDescription(solve, 'unevaluated'), 'Joint · Not evaluated');
assert.match(actionMove(axisNodes, 'axis', 3).error, /Axis needs Cylinder earlier/);
assert.match(actionMove(axisNodes, 'plane', 4).error, /Plane factor needs Axis earlier/);
assert.match(actionMove(axisNodes, 'solve', 5).error, /Shared solve needs Plane factor earlier/);
assert.ok(!discoverReuseLineage(axisNodes, ['fit']).includes('solve'));
assert.ok(discoverReuseLineage(axisNodes, ['fit', 'plane']).includes('solve'));

const referencePlane = {
  id: 'mirror-plane',
  label: 'Mirror plane',
  operation: 'reference_plane',
  axis: 'axis',
  initial_angle_degrees: 0,
};
const mirror = {
  id: 'mirror',
  label: 'Mirrored pair',
  operation: 'mirror_symmetry',
  plane: 'mirror-plane',
  surfaces: ['fit', 'other-fit'],
};
assert.deepEqual(nodeReferences(referencePlane), ['axis']);
assert.deepEqual(
  nodeReferences({
    id: 'datum-fit',
    operation: 'fit',
    selections: ['b'],
    reference_plane: 'mirror-plane',
  }),
  ['b', 'mirror-plane'],
);
assert.deepEqual(nodeReferences(mirror), ['mirror-plane', 'fit', 'other-fit']);
const parallel = {
  id: 'parallel',
  label: 'Parallel',
  operation: 'parallel',
  surface: 'other-fit',
  reference_plane: 'mirror-plane',
};
const equal = {
  id: 'equal',
  label: 'Equal',
  operation: 'equal',
  left: { measurement: 'radius', surface: 'fit' },
  right: {
    measurement: 'plane_distance',
    surface: 'other-fit',
    reference_plane: 'mirror-plane',
  },
};
assert.deepEqual(nodeReferences(parallel), ['other-fit', 'mirror-plane']);
assert.deepEqual(nodeReferences(equal), ['fit', 'other-fit', 'mirror-plane']);
const planeRelationship = {
  id: 'plane-relation',
  label: 'Coincident planes',
  operation: 'plane_relationship',
  relation: 'coincident',
  surfaces: ['fit', 'other-fit'],
};
assert.deepEqual(nodeReferences(planeRelationship), ['fit', 'other-fit']);
assert.equal(
  actionDescription(planeRelationship, 'ready'),
  'Plane relationship · Ready',
);
assert.equal(actionDescription(mirror, 'unevaluated'), 'Mirror relationship · Defined');
assert.equal(actionDescription(parallel, 'stale'), 'Parallel relationship · Defined');
assert.equal(actionDescription(equal, 'ready'), 'Numeric equality · Defined');

const region = {
  id: 'region',
  label: 'Reusable region',
  operation: 'selection_region',
  selection: 'a',
  fit: 'fit',
  axial_plane: 'axial',
  clock_plane: 'clock',
};
const applied = {
  id: 'applied',
  label: 'Applied selection',
  operation: 'region_selection',
  region: 'region',
  source: 'source',
  axial_plane: 'target-axial',
  clock_plane: 'target-clock',
};
assert.deepEqual(nodeReferences(region), ['a', 'fit', 'axial', 'clock']);
assert.deepEqual(nodeReferences(applied), [
  'region',
  'source',
  'target-axial',
  'target-clock',
]);
assert.equal(
  actionDescription(region, 'ready'),
  'Reusable selection region · Ready',
);

const reuse = {
  id: 'reuse',
  label: 'Feature reuse',
  operation: 'feature_reuse',
  fits: ['fit'],
  lineage: ['source', 'a', 'fit'],
  reference_selection: 'a',
  target_selections: ['b', 'c'],
};
const reusedSelection = {
  id: 'reused-selection',
  label: 'Reused selection',
  operation: 'reuse_selection',
  reuse: 'reuse',
  fit: 'fit',
  source_selection: 'a',
  target_selection: 'b',
};
assert.deepEqual(nodeReferences(reuse), ['fit', 'source', 'a', 'b', 'c']);
assert.deepEqual(nodeReferences(reusedSelection), ['reuse', 'fit', 'a', 'b']);
assert.equal(managedOwnerId(reusedSelection, [reuse, reusedSelection]), 'reuse');
const inferredFit = {
  id: 'reused-fit',
  operation: 'fit',
  selections: ['reused-selection'],
};
assert.equal(
  managedOwnerId(inferredFit, [reuse, reusedSelection, inferredFit]),
  'reuse',
);
const manualFit = { id: 'manual-fit', operation: 'fit', selections: ['reused-selection'] };
assert.equal(
  managedOwnerId(manualFit, [reuse, reusedSelection, c, manualFit]),
  null,
);
assert.deepEqual(
  managedSubtreeIds('reuse', [reuse, reusedSelection, inferredFit, manualFit]),
  new Set(['reuse', 'reused-selection', 'reused-fit']),
);
assert.deepEqual(discoverReuseLineage([source, a, b, fit], ['fit']), [
  'source',
  'a',
  'fit',
]);
assert.equal(actionDescription(reuse, 'ready'), 'Feature reuse · Ready');

const editableReuseNodes = [
  source,
  a,
  b,
  fit,
  reuse,
  reusedSelection,
  {
    id: 'reused-fit',
    label: 'Cylinder at End',
    operation: 'fit',
    selections: ['reused-selection'],
    kind: 'cylinder',
    axial_domain: [-2, 5],
  },
  c,
];
let sequence = 0;
const reconciled = reconcileFeatureReuse(
  editableReuseNodes,
  'reuse',
  { target_selections: ['b', 'c'] },
  (prefix) => `${prefix}-new-${++sequence}`,
);
assert.equal(reconciled.error, undefined);
assert.equal(
  reconciled.nodes.filter((node) => node.operation === 'reuse_selection').length,
  2,
);
assert.deepEqual(
  reconciled.nodes
    .filter((node) => node.operation === 'reuse_selection')
    .map((node) => node.target_selection),
  ['b', 'c'],
);
assert.ok(
  reconciled.nodes
    .filter((node) => ['reuse_selection', 'fit'].includes(node.operation) && node.id !== 'fit')
    .every((node) => node.managed_by === 'reuse' && node.managed_key),
);
assert.equal(
  reconciled.nodes.find((node) => node.id === 'reuse').lineage.join(','),
  'source,a,fit',
);
assert.ok(
  reconciled.nodes.findIndex((node) => node.id === 'c') <
    reconciled.nodes.findIndex((node) => node.id === 'reuse'),
);
const equalized = reconcileFeatureReuse(
  reconciled.nodes,
  'reuse',
  { equal_corresponding_dimensions: true },
  (prefix) => `${prefix}-equalized`,
);
assert.equal(equalized.error, undefined);
const allEqual = equalized.nodes.find((node) => node.operation === 'equal_radii');
assert.ok(allEqual);
assert.equal(allEqual.managed_by, 'reuse');
assert.equal(allEqual.managed_key, 'equal-radius/fit');
assert.deepEqual(nodeReferences(allEqual), allEqual.surfaces);
assert.equal(allEqual.surfaces[0], 'fit');
assert.equal(allEqual.surfaces.length, 3);
const reduced = reconcileFeatureReuse(
  equalized.nodes,
  'reuse',
  { target_selections: ['c'] },
  (prefix) => `${prefix}-unused`,
);
assert.equal(reduced.error, undefined);
assert.deepEqual(
  reduced.nodes
    .filter((node) => node.operation === 'reuse_selection')
    .map((node) => node.target_selection),
  ['c'],
);
const reducedEquality = reduced.nodes.find((node) => node.operation === 'equal_radii');
assert.equal(reducedEquality.id, allEqual.id);
assert.equal(reducedEquality.surfaces.length, 2);
const independent = reconcileFeatureReuse(
  reduced.nodes,
  'reuse',
  { equal_corresponding_dimensions: false },
  (prefix) => `${prefix}-unused`,
);
assert.equal(independent.error, undefined);
assert.equal(
  independent.nodes.filter((node) => node.operation === 'equal_radii').length,
  0,
);
const blocked = reconcileFeatureReuse(
  [
    ...editableReuseNodes,
    {
      id: 'dependent-axis',
      label: 'Dependent axis',
      operation: 'axis',
      source_fit: 'reused-fit',
    },
  ],
  'reuse',
  { target_selections: ['c'] },
  (prefix) => `${prefix}-blocked`,
);
assert.match(blocked.error, /Dependent axis/);

const reuseAxis = { id: 'reuse-axis', label: 'Boss axis', operation: 'axis',
    initial_parameters: [0.1, -0.2, 3, 4], direction_reversed: true },
  reuseAxial = { id: 'reuse-axial', label: 'Shoulder datum', operation: 'reference_plane',
    axis: reuseAxis.id, construction: 'perpendicular_to_axis', initial_angle_degrees: null,
    offset: 7 },
  reuseClock = { id: 'reuse-clock', label: 'Clock datum', operation: 'reference_plane',
    axis: reuseAxis.id, construction: 'parallel_to_axis', initial_angle_degrees: 28, offset: 2 },
  reuseOuter = { ...fit, id: 'reuse-outer', label: 'Outer', axis: reuseAxis.id },
  reuseBore = { ...fit, id: 'reuse-bore', label: 'Bore', axis: reuseAxis.id },
  reuseShoulder = { ...fit, id: 'reuse-shoulder', label: 'Shoulder', kind: 'plane',
    reference_plane: reuseAxial.id },
  reuseFlat = { ...fit, id: 'reuse-flat', label: 'Flat', kind: 'plane',
    reference_plane: reuseClock.id },
  reuseOtherPlane = { ...fit, id: 'reuse-other-plane', label: 'Other plane', kind: 'plane' },
  reusableFits = [reuseOuter, reuseBore, reuseShoulder, reuseFlat, reuseOtherPlane],
  reusableRelationships = [
    { id: 'reuse-coaxial', label: 'Coaxial', operation: 'coaxial',
      surface: reuseBore.id, reference: reuseOuter.id },
    { id: 'reuse-perpendicular', label: 'Perpendicular', operation: 'perpendicular',
      lateral: reuseOuter.id, plane: reuseShoulder.id },
    { id: 'reuse-parallel', label: 'Parallel', operation: 'parallel',
      surface: reuseFlat.id, reference_plane: reuseClock.id },
    { id: 'reuse-mirror', label: 'Mirror', operation: 'mirror_symmetry',
      plane: reuseClock.id, surfaces: [reuseShoulder.id, reuseOtherPlane.id], symmetric_extents: false },
    { id: 'reuse-rotation', label: 'Rotation', operation: 'rotational_symmetry',
      axis: reuseOuter.id, planes: [reuseShoulder.id, reuseFlat.id, reuseOtherPlane.id],
      symmetric_extents: true },
    { id: 'reuse-equal', label: 'Equal', operation: 'equal',
      left: { measurement: 'radius', surface: reuseOuter.id },
      right: { measurement: 'plane_distance', surface: reuseShoulder.id,
        reference_plane: reuseAxial.id } },
    { id: 'reuse-radii', label: 'Radii', operation: 'equal_radii',
      surfaces: [reuseOuter.id, reuseBore.id] },
    { id: 'reuse-planes', label: 'Planes', operation: 'plane_relationship',
      surfaces: [reuseShoulder.id, reuseOtherPlane.id], relation: 'parallel' },
    { id: 'reuse-joint', label: 'Joint', operation: 'joint_fit',
      constraints: ['reuse-coaxial', 'reuse-perpendicular', 'reuse-parallel', 'reuse-equal'] },
    { id: 'reuse-solve', label: 'Axis solve', operation: 'axis_solve', axis: reuseAxis.id,
      factors: reusableFits.map((node) => node.id), free_axis: true },
  ],
  constrainedReuse = { ...reuse, id: 'constrained-reuse', label: 'Constrained reuse',
    fits: reusableFits.map((node) => node.id), target_selections: ['b'],
    equal_corresponding_dimensions: true },
  constrainedNodes = [source, a, b, c, reuseAxis, reuseAxial, reuseClock,
    ...reusableFits, ...reusableRelationships, constrainedReuse],
  constrainedResult = reconcileFeatureReuse(constrainedNodes, constrainedReuse.id, {},
    (prefix) => `${prefix}-constrained-${++sequence}`);
assert.equal(constrainedResult.error, undefined);
assert.deepEqual(constrainedNodes.at(-1), constrainedReuse);
const copied = (result, family, target, original) => result.nodes.find((node) =>
  node.managed_by === constrainedReuse.id && node.managed_key === `${family}/${target}/${original}`),
  copiedAxis = copied(constrainedResult, 'datum', 'b', reuseAxis.id),
  copiedAxial = copied(constrainedResult, 'datum', 'b', reuseAxial.id),
  copiedClock = copied(constrainedResult, 'datum', 'b', reuseClock.id);
assert.deepEqual(copiedAxis.initial_parameters, reuseAxis.initial_parameters);
assert.equal(copiedAxis.direction_reversed, true);
for (const [node, original] of [[copiedAxis, reuseAxis], [copiedAxial, reuseAxial],
  [copiedClock, reuseClock]]) {
  assert.deepEqual(node.placement, {
    reuse: constrainedReuse.id, source: original.id, target_selection: 'b',
  });
  assert.ok(nodeReferences(node).includes(constrainedReuse.id));
  assert.ok(nodeReferences(node).includes(original.id));
  assert.ok(nodeReferences(node).includes('b'));
}
assert.equal(copiedAxial.axis, copiedAxis.id);
assert.equal(copiedClock.axis, copiedAxis.id);
assert.equal(copiedClock.construction, 'parallel_to_axis');
assert.equal(copiedClock.offset, reuseClock.offset);
assert.equal(copied(constrainedResult, 'fit', 'b', reuseOuter.id).axis, copiedAxis.id);
assert.equal(copied(constrainedResult, 'fit', 'b', reuseBore.id).axis, copiedAxis.id);
assert.equal(copied(constrainedResult, 'fit', 'b', reuseFlat.id).reference_plane, copiedClock.id);
assert.equal(copied(constrainedResult, 'fit', 'b', reuseShoulder.id).reference_plane, copiedAxial.id);
for (const relationship of reusableRelationships) {
  const generatedRelationship = copied(constrainedResult, 'relationship', 'b', relationship.id);
  assert.ok(generatedRelationship);
  const refs = nodeReferences(generatedRelationship);
  assert.ok(refs.length);
  assert.ok(refs.every((id) => constrainedResult.nodes.find((node) => node.id === id)
    .managed_by === constrainedReuse.id));
}
assert.equal(copied(constrainedResult, 'relationship', 'b', 'reuse-mirror').symmetric_extents, false);
assert.equal(copied(constrainedResult, 'relationship', 'b', 'reuse-equal').right.reference_plane,
  copiedAxial.id);
assert.equal(copied(constrainedResult, 'relationship', 'b', 'reuse-solve').free_axis, true);
const stableReuse = reconcileFeatureReuse(constrainedResult.nodes, constrainedReuse.id, {}, () => {
  throw new Error('Idempotent reconciliation must not allocate IDs');
});
assert.equal(stableReuse.error, undefined);
assert.deepEqual(stableReuse.nodes, constrainedResult.nodes);
assert.deepEqual(stableReuse.generatedIds, []);

const withThirdTarget = reconcileFeatureReuse(stableReuse.nodes, constrainedReuse.id,
  { target_selections: ['b', 'c'] }, (prefix) => `${prefix}-third-${++sequence}`);
assert.equal(withThirdTarget.error, undefined);
for (const existing of constrainedResult.nodes.filter((node) => node.managed_by === constrainedReuse.id))
  assert.ok(withThirdTarget.nodes.some((node) => node.id === existing.id && node.label === existing.label));
assert.notEqual(copied(withThirdTarget, 'datum', 'c', reuseAxis.id).id, copiedAxis.id);
assert.equal(copied(withThirdTarget, 'fit', 'c', reuseFlat.id).reference_plane,
  copied(withThirdTarget, 'datum', 'c', reuseClock.id).id);
const withoutFirstTarget = reconcileFeatureReuse(withThirdTarget.nodes, constrainedReuse.id,
  { target_selections: ['c'] }, () => { throw new Error('Remaining target must retain all IDs'); });
assert.equal(withoutFirstTarget.error, undefined);
assert.ok(!withoutFirstTarget.nodes.some((node) => node.managed_key?.startsWith('datum/b/')));
assert.ok(!withoutFirstTarget.nodes.some((node) => node.managed_key?.startsWith('relationship/b/')));
assert.ok(withoutFirstTarget.removedIds.includes(copiedAxis.id));
assert.ok(withoutFirstTarget.removedIds.includes(copiedClock.id));
assert.equal(copied(withoutFirstTarget, 'datum', 'c', reuseAxis.id).id,
  copied(withThirdTarget, 'datum', 'c', reuseAxis.id).id);
const protectedDatum = reconcileFeatureReuse([...withThirdTarget.nodes,
  { id: 'user-plane', label: 'User plane', operation: 'reference_plane', axis: copiedAxis.id }],
constrainedReuse.id, { target_selections: ['c'] }, () => { throw new Error('No new target'); });
assert.match(protectedDatum.error, /User plane/);
const protectedFit = reconcileFeatureReuse([...withThirdTarget.nodes,
  { id: 'user-face', label: 'User face', operation: 'arranged_face',
    surface: { feature: copied(constrainedResult, 'fit', 'b', reuseFlat.id).id },
    cutters: [], domains: [] }], constrainedReuse.id, { target_selections: ['c'] },
() => { throw new Error('No new target'); });
assert.match(protectedFit.error, /User face/);

const axisInitializer = { ...fit, id: 'axis-initializer', label: 'Initializer' },
  initializedAxis = { id: 'fit-derived-axis', label: 'Fit-derived axis', operation: 'axis',
    source_fit: axisInitializer.id, direction_reversed: false },
  axisBoundFit = { ...fit, id: 'axis-bound-fit', label: 'Bound', axis: initializedAxis.id },
  initializerNodes = [source, a, b, axisInitializer, initializedAxis, axisBoundFit,
    { ...constrainedReuse, fits: [axisInitializer.id, axisBoundFit.id],
      equal_corresponding_dimensions: false }],
  initializerResult = reconcileFeatureReuse(initializerNodes, constrainedReuse.id, {},
    (prefix) => `${prefix}-initializer-${++sequence}`);
assert.equal(initializerResult.error, undefined);
assert.equal(copied(initializerResult, 'datum', 'b', initializedAxis.id).placement, undefined);
assert.equal(copied(initializerResult, 'datum', 'b', initializedAxis.id).source_fit,
  copied(initializerResult, 'fit', 'b', axisInitializer.id).id);
const omittedInitializer = reconcileFeatureReuse(initializerNodes, constrainedReuse.id,
  { fits: [axisBoundFit.id] }, () => 'unused');
assert.match(omittedInitializer.error, /Include Initializer/);
const pointPairReuse = reconcileFeatureReuse([
  source, a, b, { id: 'pa', label: 'First point', operation: 'point' },
  { id: 'pb', label: 'Second point', operation: 'point' },
  { ...initializedAxis, source_fit: null, source_points: ['pa', 'pb'] }, axisBoundFit,
  { ...constrainedReuse, fits: [axisBoundFit.id], equal_corresponding_dimensions: false },
], constrainedReuse.id, {}, () => 'unused');
assert.ok(pointPairReuse.error);

const legacyOwnerNodes = [source, a, b, c, fit, reuse, reusedSelection, inferredFit, loose];
assert.deepEqual(ids(actionMove(legacyOwnerNodes, 'reuse', legacyOwnerNodes.length)),
  ['source', 'a', 'b', 'c', 'fit', 'loose', 'reuse', 'reused-selection', 'reused-fit']);
assert.equal(managedOwnerId(inferredFit, actionMove(legacyOwnerNodes, 'reuse', 9).nodes), 'reuse');
const secondOwner = { ...facesOwner, id: 'second-owner', label: 'Other faces' },
  secondEdge = { ...edge, id: 'second-edge', managed_by: 'second-owner' },
  twoOwners = [...faceNodes.slice(0, -1), secondOwner, secondEdge, loose];
assert.deepEqual(ids(actionMove(twoOwners, ['faces-owner', 'second-owner'], twoOwners.length)),
  ['source', 'a', 'fit', 'loose', 'faces-owner', 'edge', 'face', 'second-owner', 'second-edge']);
