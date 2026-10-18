import test from 'node:test';
import assert from 'node:assert/strict';
import { editSelection, rectangleHits, featureVertexIds, activeSelectionEntry,
  selectionEditState, resolvedSelectionIds, editPaintedMembership, initialPaintedEntry } from './selection.js';

const scan = { id: 'scan', operation: 'source', source_sha256: 'a'.repeat(64),
  reference_sha256: 'b'.repeat(64), descriptor: { filename: 'scan.ply', vertices: 10 } },
  metadata = { vertices: 10 },
  firstEntry = () => initialPaintedEntry(scan, metadata, 'first'),
  grouped = entries => ({ id: 'selection', operation: 'selection', source: 'scan', entries }),
  snapshot = (node, memberships = {}, states = {}) => ({ recipe: { nodes: [scan, node] }, memberships, states });

test('entry resolution requires exact bytes, reference interpretation and vertex count', () => {
  const first = firstEntry(), other = { ...firstEntry(), id: 'other',
    scan: { ...firstEntry().scan, source_sha256: 'c'.repeat(64) } }, node = grouped([other, first]);
  assert.equal(activeSelectionEntry(node, scan, metadata), first);
  assert.equal(activeSelectionEntry(node, { ...scan, reference_sha256: 'd'.repeat(64) }, metadata), null);
  assert.equal(activeSelectionEntry(node, scan, { vertices: 11 }), null);
  assert.equal(activeSelectionEntry(node, { ...scan, id: 'another-source' }, metadata), null);
  assert.equal(activeSelectionEntry(node, scan, {}), null);
  assert.equal(activeSelectionEntry(grouped([first, { ...first, id: 'duplicate' }]), scan, metadata), null);
  assert.equal(activeSelectionEntry(node, { ...scan, descriptor: { filename: 'renamed.ply' } }, metadata), first);
});

test('painting updates only the active entry and preserves original entries and owned volumes', () => {
  const first = { ...firstEntry(), ids: [1], volumes: [{ id: 'volume', archive: { data: 'retained' } }] },
    previous = { ...firstEntry(), id: 'previous', ids: [8], scan: { ...first.scan, source_sha256: 'c'.repeat(64) } },
    node = grouped([previous, first]), next = editPaintedMembership(node, scan, metadata, [0, 2], 'through_all');
  assert.deepEqual(next.entries[1].ids, [0, 2]);
  assert.equal(next.entries[1].depth, 'through_all');
  assert.deepEqual(next.entries[1].volumes, first.volumes);
  assert.deepEqual(next.entries[0], previous);
  assert.deepEqual(node.entries[1].ids, [1]);
  assert.equal(Object.hasOwn(next, 'ids'), false);
  assert.equal(Object.hasOwn(next, 'depth'), false);
});

test('generated and unavailable entries cannot be painted, including empty pending entries', () => {
  const generated = { ...firstEntry(), mode: 'generated', source_volume: null };
  delete generated.ids;
  const pending = grouped([generated]), chosen = grouped([{ ...generated,
    source_volume: { selection: 'other', entry: 'previous', volume: 'patch' } }]);
  assert.match(selectionEditState(pending, scan, metadata).message, /Choose source volume/);
  assert.match(selectionEditState(chosen, scan, metadata).message, /cannot be painted/);
  for (const node of [pending, chosen, grouped([])]) {
    assert.equal(selectionEditState(node, scan, metadata).editable, false);
    assert.throws(() => editPaintedMembership(node, scan, metadata, [], 'first_surface'));
  }
});

test('resolved generated memberships come only from current ready publications', () => {
  const generated = { ...firstEntry(), mode: 'generated', source_volume: { selection: 's', entry: 'e', volume: 'v' } };
  delete generated.ids;
  const node = grouped([generated]);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, { selection: [2, 4] }, { selection: 'ready' }), metadata), [2, 4]);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, { selection: [2, 4] }, { selection: 'needs_input' }), metadata), []);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, {}, { selection: 'ready' }), metadata), []);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, { selection: [2, 4] }, { selection: 'ready' }), { vertices: 20 }), []);
});

test('published memberships override authored painted IDs without falling back from null', () => {
  const node = grouped([{ ...firstEntry(), ids: [1] }]);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node), metadata), [1]);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, { selection: [2] }), metadata), [2]);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node, { selection: null }), metadata), []);
  const growth = { id: 'growth', operation: 'growth' };
  assert.deepEqual(resolvedSelectionIds(growth, snapshot(node, { growth: [6] }), metadata), [6]);
});

test('legacy selection read and edit preserve the flat format', () => {
  const node = { id: 'selection', operation: 'selection', source: 'scan', ids: [0, 4], depth: 'through_all' };
  assert.equal(selectionEditState(node, scan, metadata).editable, true);
  assert.deepEqual(resolvedSelectionIds(node, snapshot(node), metadata), [0, 4]);
  assert.deepEqual(editPaintedMembership(node, scan, metadata, [2], 'first_surface'),
    { ...node, ids: [2], depth: 'first_surface' });
  assert.deepEqual(node.ids, [0, 4]);
  assert.deepEqual(resolvedSelectionIds(node, { recipe: { nodes: [node] } }, metadata), []);
});

test('new entries contain no default volume and cannot alias the source descriptor', () => {
  const entry = firstEntry();
  assert.deepEqual(entry.ids, []);
  assert.deepEqual(entry.volumes, []);
  assert.equal(entry.mode, 'painted');
  assert.equal(entry.depth, 'first_surface');
  assert.equal(entry.scan.reference_sha256, scan.reference_sha256);
  entry.scan.descriptor.filename = 'changed.ply';
  assert.equal(scan.descriptor.filename, 'scan.ply');
  assert.throws(() => initialPaintedEntry(scan, { vertices: 0 }, 'new'), /identity is unavailable/);
});

test('painting rejects malformed IDs and preserves the source binding', () => {
  const node = grouped([firstEntry()]);
  for (const ids of [[2, 1], [1, 1], [-1], [10], [true], [1.5], ['1'], Array.from({ length: 25_001 }, (_, i) => i)])
    assert.throws(() => editPaintedMembership(node, scan, metadata, ids, 'first_surface'), /sorted, unique/);
  assert.throws(() => editPaintedMembership(node, scan, metadata, [], 'other'), /valid selection depth/);
  assert.deepEqual(node.entries[0], firstEntry());
});

test('adding moves source IDs between regions without mutating the saved session', () => {
  const original = { lateral_ids: [1, 4], plane_ids: [2, 3] };
  assert.deepEqual(editSelection(original, 'lateral_ids', [3, 5], 'add'), {
    lateral_ids: [1, 3, 4, 5],
    plane_ids: [2],
  });
  assert.deepEqual(original.plane_ids, [2, 3]);
});
test('replace, remove and empty selections retain canonical source IDs', () => {
  const original = { lateral_ids: [1, 4], plane_ids: [2, 3] };
  assert.deepEqual(
    editSelection(original, 'lateral_ids', [4, 1, 4], 'replace').lateral_ids,
    [1, 4],
  );
  assert.deepEqual(editSelection(original, 'lateral_ids', [1], 'remove').lateral_ids, [4]);
  assert.deepEqual(editSelection(original, 'lateral_ids', [], 'replace').lateral_ids, []);
});
test('rectangle includes hidden-depth vertices but excludes clipped and outside points', () => {
  const points = new Float32Array([1, 1, -0.5, 1, 1, 0.5, 1, 1, 2, 10, 10, 0]);
  assert.deepEqual(
    rectangleHits(points, (p) => p, { left: 0, right: 2, top: 0, bottom: 2 }),
    [0, 1],
  );
});

test('adding to a third surface removes overlap from every other selection', async () => {
  const { editSelectionGroups } = await import('./selection.js');
  const groups = { side: [1, 2], end: [3, 4], extra: [5] };
  assert.deepEqual(editSelectionGroups(groups, 'extra', [2, 3, 6], 'add'), {
    side: [1],
    end: [4],
    extra: [2, 3, 5, 6],
  });
  assert.deepEqual(groups, { side: [1, 2], end: [3, 4], extra: [5] });
});

test('brush sweep covers sparse pointer events and has a circular footprint', async () => {
  const { brushHits } = await import('./selection.js');
  const projection = {
    points: [[0, 0, 0], [50, 0, 0], [100, 0, 0], [50, 6, 0], [3, 3, 0], null],
    visible: () => true,
  };
  assert.deepEqual(brushHits(projection, [0, 0], [100, 0], 5, 'through_all'), [0, 1, 2, 4]);
  assert.deepEqual(brushHits(projection, [0, 0], [0, 0], 4, 'through_all'), [0]);
});

test('first surface excludes occluded vertices with either face winding', async () => {
  const { selectionProjection, brushHits } = await import('./selection.js');
  const clip = new Float64Array([
    -1, -1, -0.5, 1, 1, -1, -0.5, 1, 0, 1, -0.5, 1, 0, 0, -0.5, 1, 0, 0, 0.5, 1, 0, 0, 2, 1,
  ]);
  for (const indices of [
    [0, 1, 2],
    [2, 1, 0],
  ]) {
    const projected = selectionProjection(clip, indices, 100, 100);
    assert.deepEqual(brushHits(projected, [50, 50], [50, 50], 4, 'first_surface'), [3]);
    assert.deepEqual(brushHits(projected, [50, 50], [50, 50], 4, 'through_all'), [3, 4]);
  }
});

test('near-plane crossing triangles still occlude, and clipped-away triangles do not', async () => {
  const { selectionProjection } = await import('./selection.js');
  const clip = new Float64Array([
    -1, -1, -2, 1, 1, -1, 0, 1, 0, 1, 0, 1, 0, 0, 0.5, 1, 0.9, 0.9, 0.5, 1,
  ]);
  const p = selectionProjection(clip, [0, 1, 2], 100, 100);
  assert.equal(p.visible(3), false);
  assert.equal(p.visible(4), true); // outside the silhouette
  clip[2] = clip[6] = clip[10] = -2;
  assert.equal(selectionProjection(clip, [0, 1, 2], 100, 100).visible(3), true);
});

test('replace uses the union of a whole stroke and remove leaves other patches intact', async () => {
  const { editSelectionGroups } = await import('./selection.js');
  const original = { seed: [9], other: [1, 2, 3] };
  const stroke = new Set([1, 2]);
  assert.deepEqual(editSelectionGroups(original, 'seed', stroke, 'replace'), {
    seed: [1, 2],
    other: [3],
  });
  assert.deepEqual(editSelectionGroups({ seed: [1, 2, 8], other: [3] }, 'seed', [2], 'remove'), {
    seed: [1, 8],
    other: [3],
  });
});

test('feature emphasis follows fit inputs and relationships, excluding growth barriers', () => {
  const nodes = [
    { id: 'a', operation: 'selection' },
    { id: 'b', operation: 'selection' },
    { id: 'c', operation: 'selection' },
    { id: 'grown', operation: 'growth', seed_fit: 'seed', barriers: ['b'] },
    { id: 'seed', operation: 'fit', selections: ['a'] },
    { id: 'side', operation: 'fit', selections: ['a', 'grown'] },
    { id: 'plane', operation: 'fit', selections: ['b'] },
    { id: 'axis', operation: 'axis', source_fit: 'side' },
    { id: 'manual-axis', operation: 'axis', initial_parameters: [0, 0, 0, 0] },
    { id: 'side-factor', operation: 'fit', selections: ['a'], axis: 'axis' },
    { id: 'plane-factor', operation: 'fit', selections: ['b'], axis: 'axis' },
    { id: 'mirror-plane', operation: 'reference_plane', axis: 'axis' },
    { id: 'mirror-member', operation: 'fit', selections: ['b'] },
    {
      id: 'mirror',
      operation: 'mirror_symmetry',
      plane: 'mirror-plane',
      surfaces: ['seed', 'mirror-member'],
    },
    {
      id: 'parallel',
      operation: 'parallel',
      surface: 'mirror-member',
      reference_plane: 'mirror-plane',
    },
    {
      id: 'equal',
      operation: 'equal',
      left: { measurement: 'radius', surface: 'side-factor' },
      right: {
        measurement: 'plane_distance',
        surface: 'mirror-member',
        reference_plane: 'mirror-plane',
      },
    },
    {
      id: 'axis-solve',
      operation: 'axis_solve',
      axis: 'axis',
      factors: ['side-factor', 'plane-factor'],
    },
    { id: 'constraint', operation: 'perpendicular', lateral: 'side', plane: 'plane' },
    { id: 'joint', operation: 'joint_fit', constraints: ['constraint'] },
    {
      id: 'region',
      operation: 'selection_region',
      selection: 'a',
      fit: 'side-factor',
      axial_plane: 'mirror-plane',
      clock_plane: 'mirror-plane',
    },
    {
      id: 'applied',
      operation: 'region_selection',
      region: 'region',
      source: 'source',
      axial_plane: 'mirror-plane',
      clock_plane: 'mirror-plane',
    },
    {
      id: 'reuse',
      operation: 'feature_reuse',
      fits: ['side'],
      reference_selection: 'a',
      target_selections: ['b', 'c'],
    },
    {
      id: 'reused',
      operation: 'reuse_selection',
      reuse: 'reuse',
      fit: 'side',
      source_selection: 'a',
      target_selection: 'b',
    },
    { id: 'arranged', operation: 'arranged_face', surface: { feature: 'side' },
      cutters: [{ feature: 'plane' }], domains: ['reused'] },
  ];
  const memberships = {
    a: [1, 2],
    b: [8],
    c: [9, 10],
    grown: [2, 3],
    applied: [20, 21],
    reused: [30, 31],
  };
  assert.deepEqual(featureVertexIds(nodes, memberships, 'grown'), [2, 3]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'side'), [1, 2, 3]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'constraint'), [1, 2, 3, 8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'joint'), [1, 2, 3, 8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'axis'), [1, 2, 3]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'manual-axis'), []);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'plane-factor'), [8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'axis-solve'), [1, 2, 8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'mirror-plane'), [1, 2, 3]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'mirror'), [1, 2, 8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'parallel'), [8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'equal'), [1, 2, 8]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'region'), [1, 2]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'applied'), [20, 21]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'reuse'), [1, 2, 3, 8, 9, 10]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'reused'), [30, 31]);
  assert.deepEqual(featureVertexIds(nodes, memberships, 'arranged'), [1, 2, 3]);
  assert.deepEqual(featureVertexIds(nodes, { ...memberships, grown: null }, 'grown'), []);
});

test('relationship emphasis unions participant observations without growth seeds, barriers or datum guidance', () => {
  const nodes = [
    { id: 'a', operation: 'selection' },
    { id: 'b', operation: 'selection' },
    { id: 'guidance', operation: 'selection' },
    { id: 'guide-fit', operation: 'fit', selections: ['guidance'] },
    { id: 'guide-axis', operation: 'axis', source_fit: 'guide-fit' },
    { id: 'guide-plane', operation: 'reference_plane', axis: 'guide-axis' },
    { id: 'grown', operation: 'growth', seed_fit: 'guide-fit', barriers: ['guidance'] },
    { id: 'first', operation: 'fit', selections: ['a', 'grown'], axis: 'guide-axis' },
    { id: 'second', operation: 'fit', selections: ['b'], reference_plane: 'guide-plane' },
    { id: 'third', operation: 'fit', selections: ['a'] },
    { id: 'participant-axis', operation: 'axis', source_fit: 'first' },
  ];
  const memberships = { a: [2, 1, 2], b: [4, 2, 3], guidance: [99], grown: [2, 3] };
  const cases = [
    { operation: 'coaxial_group', axis: 'guide-axis', surfaces: ['first', 'second'] },
    { operation: 'coaxial', surface: 'first', reference: 'second' },
    { operation: 'perpendicular', lateral: 'first', plane: 'second' },
    { operation: 'rotational_symmetry', axis: 'participant-axis', planes: ['first', 'second', 'third'] },
    { operation: 'mirror_symmetry', plane: 'guide-plane', surfaces: ['first', 'second'] },
    { operation: 'parallel', surface: 'second', reference_plane: 'guide-plane' },
    { operation: 'equal', left: { measurement: 'radius', surface: 'first' },
      right: { measurement: 'plane_distance', surface: 'second', reference_plane: 'guide-plane' } },
    { operation: 'plane_relationship', relation: 'coincident', surfaces: ['first', 'second', 'first'] },
    { operation: 'plane_relationship', relation: 'parallel', surfaces: ['first', 'second', 'first'] },
    { operation: 'plane_relationship', relation: 'parallel', surfaces: [
      { feature: 'first', output: 'plane', context: 'first' },
      { feature: 'second', output: 'plane', context: 'earlier-publication' },
    ] },
    { operation: 'equal_radii', surfaces: ['first', 'second', 'first'] },
  ];
  for (const relationship of cases) {
    const expected = relationship.operation === 'parallel' ? [2, 3, 4] : [1, 2, 3, 4];
    assert.deepEqual(featureVertexIds([...nodes, { id: 'relationship', ...relationship }],
      memberships, 'relationship'), expected, `${relationship.operation} ${relationship.relation || ''}`);
  }
});

test('aggregate relationship emphasis safely traverses missing references and cycles', () => {
  for (const operation of ['plane_relationship', 'equal_radii']) {
    const nodes = [
      { id: 'a', operation: 'selection' },
      { id: 'b', operation: 'selection' },
      { id: 'first', operation: 'fit', selections: ['a', 'relationship', 'missing-selection'] },
      { id: 'second', operation: 'fit', selections: ['b'] },
      { id: 'relationship', operation, surfaces: ['first', 'second', 'missing-fit', 'relationship'] },
    ];
    assert.deepEqual(featureVertexIds(nodes, { a: [2, 1], b: [2, 3] }, 'relationship'),
      [1, 2, 3], operation);
    assert.deepEqual(featureVertexIds(nodes, { a: [2, 1] }, 'relationship'), [1, 2], operation);
    assert.deepEqual(featureVertexIds(nodes, {}, 'relationship'), [], operation);
    assert.deepEqual(featureVertexIds(nodes, {}, 'missing-relationship'), [], operation);
  }
});

test('rotational symmetry preserves cylinder fit references and rejects selections or mixed types', async () => {
  const { rotationalFitInputs } = await import('./selection.js');
  const nodes = ['a', 'b', 'c'].map((id) => ({
    id,
    operation: 'fit',
    kind: 'cylinder',
    selections: ['selection-' + id],
  }));
  const before = structuredClone(nodes);
  assert.deepEqual(rotationalFitInputs(nodes, ['a', 'b', 'c']), ['a', 'b', 'c']);
  assert.deepEqual(nodes, before);
  assert.throws(() => rotationalFitInputs(nodes, ['a', 'a', 'c']), /distinct/);
  assert.throws(
    () => rotationalFitInputs([...nodes, { id: 's', operation: 'selection' }], ['a', 'b', 's']),
    /existing fits/,
  );
  assert.throws(
    () =>
      rotationalFitInputs(
        nodes.map((n) => (n.id === 'c' ? { ...n, kind: 'plane' } : n)),
        ['a', 'b', 'c'],
      ),
    /same type/,
  );
  assert.throws(
    () => rotationalFitInputs(nodes.map((n) => ({ ...n, kind: 'sphere' })), ['a', 'b', 'c']),
    /currently supports/,
  );
});

test('mirror symmetry preserves two standalone same-type fit references', async () => {
  const { mirrorFitInputs } = await import('./selection.js');
  const nodes = ['a', 'b'].map((id) => ({
    id,
    operation: 'fit',
    kind: 'cone',
    selections: ['selection-' + id],
  }));
  assert.deepEqual(mirrorFitInputs(nodes, ['a', 'b']), ['a', 'b']);
  assert.throws(() => mirrorFitInputs(nodes, ['a', 'a']), /distinct/);
  assert.throws(
    () => mirrorFitInputs(nodes.map((n) => (n.id === 'b' ? { ...n, kind: 'plane' } : n)), ['a', 'b']),
    /same type/,
  );
  assert.throws(
    () => mirrorFitInputs(nodes.map((n) => ({ ...n, kind: 'sphere' })), ['a', 'b']),
    /currently supports/,
  );
  assert.throws(() => mirrorFitInputs([{ ...nodes[0], axis: 'axis' }, nodes[1]], ['a', 'b']), /standalone/);
});
