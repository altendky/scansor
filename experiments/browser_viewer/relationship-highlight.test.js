import assert from 'node:assert/strict';
import test from 'node:test';
import {inputReferenceKey} from './feature-inputs.js';
import {createRelationshipSupportCache, relationshipHighlightParts, relationshipSupportGroups,
  relationshipAxisSpan, relationshipHighlightColors} from './relationship-highlight.js';

const port = (feature, context = feature) => ({reference: {feature, output: 'axis', context},
  availability: 'ready', compatible: true});
const source = new Float32Array([0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
const part = (ids, kind = 'hover', feature = 'fit') => ({kind, key: feature,
  output: port(feature), support: {ids}});

test('support resolves the exact published context without reading latest or unrelated ports', () => {
  const output = port('fit', 'old-solve'), old = {ids: [0, 2]}, state = {
    states: {'old-solve': 'ready'}, derived: {'old-solve': {surfaces: {fit: old}}, fit: {ids: [1]}},
    get results() {throw Error('Do not substitute latest aliases');},
  };
  Object.defineProperty(state.derived, 'unrelated', {get() {throw Error('Do not inspect unrelated geometry');}});
  const parts = relationshipHighlightParts(state, [output], source, createRelationshipSupportCache());
  assert.equal(parts.length, 1); assert.equal(parts[0].value, old);
  assert.equal(parts[0].support.ids, old.ids);
  assert.deepEqual(relationshipSupportGroups(parts, source)[0].ids, new Uint32Array([0, 2]));
  assert.deepEqual(output.reference, {feature: 'fit', output: 'axis', context: 'old-solve'});
});

test('compact datum previews retain guides without inventing observation support', () => {
  const output = {...port('fit', '@axis/datum'), preview: {axis_display: [0, 0, 1], point_display: [2, 3, 4]}},
    state = {states: {}, derived: {fit: {ids: [1, 2]}}},
    parts = relationshipHighlightParts(state, [output], source, createRelationshipSupportCache());
  assert.equal(parts.length, 1); assert.equal(parts[0].value, output.preview); assert.equal(parts[0].support, null);
  assert.deepEqual(relationshipSupportGroups(parts, source), []);
  state.states['@axis/datum'] = 'failed';
  assert.deepEqual(relationshipHighlightParts(state, [output], source, createRelationshipSupportCache()), []);
});

test('collections expand exact ready leaves while retaining their authored owner key', () => {
  const first = port('fit', 'reuse'), second = port('copy'), missing = port('missing'),
    collection = {...port('reuse'), reference: {feature: 'reuse', output: 'direction:fit', context: 'reuse'},
      collection: {members: [first.reference, second.reference, missing.reference]}},
    state = {input_catalogue: [first, second], states: {reuse: 'ready', copy: 'ready'},
      derived: {reuse: {outputs: {fit: {ids: [0, 1]}}}, copy: {ids: [2, 3]}}},
    saved = structuredClone(collection), key = inputReferenceKey(collection.reference),
    parts = relationshipHighlightParts(state, [collection], source, createRelationshipSupportCache(), new Map([[key, 'hover']]));
  assert.equal(parts.length, 2);
  assert(parts.every(item => item.key === key && item.kind === 'hover'));
  assert.deepEqual(parts.map(item => item.output.reference), [first.reference, second.reference]);
  assert.deepEqual([...relationshipSupportGroups(parts, source)[0].ids], [0, 1, 2, 3]);
  assert.deepEqual(collection, saved);
});

test('support union deduplicates leaves and gives hover priority over selected and overlap', () => {
  const groups = relationshipSupportGroups([part([0, 1], 'selected', 'a'), part([1, 2], 'overlap', 'b'),
    part([2, 3], 'hover', 'c')], source);
  assert.deepEqual(groups.map(group => [group.kind, [...group.ids]]),
    [['selected', [0]], ['overlap', [1]], ['hover', [2, 3]]]);
  for (const group of groups) assert.deepEqual([...group.coordinates], [...group.ids].flatMap(id => [...source.subarray(id * 3, id * 3 + 3)]));
  assert.notEqual(relationshipHighlightColors.selected, relationshipHighlightColors.hover);
});

test('invalid and duplicate source IDs cannot allocate or display invalid coordinates', () => {
  const positions = new Float32Array(source); positions[6] = NaN;
  const value = {ids: [-1, NaN, Infinity, 1.5, 1e12, 1, 1, 2]},
    support = createRelationshipSupportCache().read({}, positions, value);
  assert(support);
  const groups = relationshipSupportGroups([{...part([]), support}], positions);
  assert.deepEqual([...groups[0].ids], [1]); assert.equal(groups[0].coordinates.length, 3);
  assert(groups[0].coordinates.every(Number.isFinite));
  assert.throws(() => relationshipSupportGroups([], source, Infinity), /point limit/);
});

test('bounded sampling preserves a tiny candidate beside a much larger overlapping fit', () => {
  const positions = new Float32Array(30000);
  for (let id = 0; id < 10000; id++) positions[id * 3] = id;
  const big = Array.from({length: 9999}, (_, id) => id), tiny = [9999],
    groups = relationshipSupportGroups([part(big, 'overlap', 'big'), part(tiny, 'overlap', 'tiny')], positions, 32);
  assert.equal(groups[0].ids.length, 32);
  assert(groups[0].ids.includes(9999)); assert(groups[0].ids.includes(0));
  assert.equal(groups[0].totalVertices, 10000); assert.equal(groups[0].sampled, true);
  assert.deepEqual(relationshipSupportGroups([part(big, 'overlap', 'big'), part(tiny, 'overlap', 'tiny')], positions, 32), groups);
  assert.equal(big.length, 9999); assert.deepEqual(tiny, [9999]);
});

test('support cache keeps source IDs by reference and expires on accepted graph or positions', () => {
  const cache = createRelationshipSupportCache(2), state = {}, value = {ids: [0, 1]},
    first = cache.read(state, source, value);
  assert.equal(first.ids, value.ids); assert.equal(cache.read(state, source, value), first);
  cache.read(state, source, {ids: [2]}); cache.read(state, source, {ids: [3]});
  assert.notEqual(cache.read(state, source, value), first);
  const second = cache.read(state, source, value);
  assert.notEqual(cache.read({...state}, source, value), second);
  const moved = new Float32Array(source); moved[0] = 100;
  assert.deepEqual(cache.read(state, moved, value).bounds[1], [100, 2, 3]);
  const beforeClear = cache.read(state, moved, value); cache.clear();
  assert.notEqual(cache.read(state, moved, value), beforeClear);
});

test('relationship axis display centers on its support and scales without absolute unit floors', () => {
  for (const scale of [1e-6, 1, 1000]) {
    const origin = [5 * scale, 7 * scale, 0], support = {
      center: [6 * scale, 8 * scale, 102 * scale],
      bounds: [[4 * scale, 6 * scale, 100 * scale], [8 * scale, 10 * scale, 104 * scale]],
    }, span = relationshipAxisSpan([0, 0, 1], origin, support, [[0, 0, 0], [20 * scale, 20 * scale, 200 * scale]]);
    assert.deepEqual(span.center, [5 * scale, 7 * scale, 102 * scale]);
    assert(span.extent >= 2 * scale); assert(span.extent < 10 * scale);
    assert.equal(span.arrowLength, span.extent * 0.16);
    assert.deepEqual(span.origin, origin);
  }
});

test('compact axes use scene size and maintain the same exact line under oblique projection', () => {
  const span = relationshipAxisSpan([1, 1, 0], [2, -2, 0], null, [[90, 90, 0], [110, 110, 20]]);
  assert(span); assert(span.extent > 4);
  assert(Math.abs((span.center[0] - 2) - (span.center[1] + 2)) < 1e-10);
  assert.deepEqual(relationshipAxisSpan([0, 0, 0], [0, 0, 0], null, [[0, 0, 0], [1, 1, 1]]), null);
});
