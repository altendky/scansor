import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createCadDisplay, decodeCadDisplay} from './cad-display.js';

const display = () => ({preview: {positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2]},
  edges: [{positions: [0, 0, 0, 1, 0, 0], closed: false}]});
const context = (quality = 'standard', revision = 1) => ({generation: 1, token: 'a', revision, quality});
const answer = request => ({...request, display: display()});
const delay = () => new Promise(resolve => setTimeout(resolve, 3));
async function done(controller) {
  for (let count = 0; count < 500 && controller.busy; count++) await delay();
  assert.equal(controller.busy, false, 'bounded display work settles');
}

test('CAD display decoder copies validated bounded buffers and rejects malformed geometry', () => {
  const source = display(), decoded = decodeCadDisplay(source);
  source.preview.positions[0] = 99;
  assert.equal(decoded.preview.positions[0], 0);
  assert.equal(decoded.bytes, 72);
  for (const change of [value => value.preview.positions[0] = Infinity,
    value => value.preview.positions[0] = 1e300, value => value.preview.indices[0] = 3,
    value => value.preview.indices[0] = -1, value => value.preview.indices[0] = 0.5,
    value => value.edges[0].positions[0] = NaN, value => delete value.edges[0].closed]) {
    const invalid = display(); change(invalid); assert.throws(() => decodeCadDisplay(invalid));
  }
});

test('quality cache reuses complete geometry and Draft does not change accepted input', async () => {
  const requests = [], state = context(), frozen = structuredClone(state);
  const controller = createCadDisplay({refine: async request => { requests.push(request); return answer(request); }});
  controller.refresh(state, ['one', 'one']); await done(controller);
  assert.equal(controller.lookup('one').quality, 'standard');
  controller.refresh(context('fine'), ['one']); await done(controller);
  assert.equal(controller.lookup('one').quality, 'fine');
  controller.refresh(context(), ['one']); await done(controller);
  assert.equal(requests.length, 2);
  controller.refresh(context('draft'), ['one']); await done(controller);
  assert.equal(controller.lookup('one'), null); assert.equal(requests.length, 2);
  assert.deepEqual(state, frozen);
});

test('Body display provenance must partition all triangles with nonempty source identities', () => {
  const value = {...display(), face_ranges: [{start: 0, count: 3, face_ids: ['face']} ]};
  const decoded = decodeCadDisplay(value);
  value.face_ranges[0].face_ids[0] = 'changed';
  assert.deepEqual(decoded.face_ranges, [{start: 0, count: 3, face_ids: ['face']}]);
  for (const face_ranges of [[], [{start: 3, count: 3, face_ids: ['face']}],
    [{start: 0, count: 6, face_ids: ['face']}], [{start: 0, count: 3, face_ids: []}],
    [{start: 0, count: 3, face_ids: ['face', 'face']}]])
    assert.throws(() => decodeCadDisplay({...display(), face_ranges}));
});

test('bounded LRU never endlessly regenerates evicted features in the same request set', async () => {
  const requests = [], controller = createCadDisplay({byteLimit: 80,
    refine: async request => { requests.push(request.feature); return answer(request); }});
  controller.refresh(context(), ['a', 'b', 'c']); await done(controller);
  assert.deepEqual(requests, ['a', 'b', 'c']); assert.equal(controller.bytes, 72);
  controller.refresh(context(), ['a', 'b', 'c']); await done(controller);
  assert.equal(requests.length, 3); assert.equal(controller.lookup('a'), null);
  assert.ok(controller.lookup('c'));
});

test('revision fence retires a late reply and releases the previous scene immediately', async () => {
  let release, calls = 0;
  const controller = createCadDisplay({refine: request => {
    calls++;
    if (calls === 1) return new Promise(resolve => { release = () => resolve(answer(request)); });
    return Promise.resolve(answer(request));
  }});
  controller.refresh(context(), ['a']); await delay();
  controller.refresh(context('standard', 2), ['b']);
  assert.equal(controller.lookup('a'), null); release(); await done(controller);
  assert.equal(controller.lookup('a'), null); assert.ok(controller.lookup('b')); assert.equal(calls, 2);
});

test('cancellation waits for safe retirement, keeps completed displays and needs explicit retry', async () => {
  let release, calls = 0;
  const controller = createCadDisplay({refine: request => {
    calls++;
    if (calls === 2) return new Promise(resolve => { release = () => resolve(answer(request)); });
    return Promise.resolve(answer(request));
  }});
  controller.refresh(context(), ['a']); await done(controller);
  controller.refresh(context('fine'), ['a']); await delay();
  controller.cancel(); assert.equal(controller.busy, true);
  assert.equal(controller.lookup('a').quality, 'standard'); release(); await done(controller);
  controller.refresh(context('fine'), ['a']); await done(controller);
  assert.equal(calls, 2); assert.equal(controller.lookup('a').quality, 'standard');
  controller.retry(); await done(controller);
  assert.equal(controller.lookup('a').quality, 'fine'); assert.equal(calls, 3);
});

test('failed or mismatched refinement keeps the previous complete display without a retry loop', async () => {
  let fail = false, calls = 0;
  const controller = createCadDisplay({refine: async request => {
    calls++;
    return fail ? {...answer(request), revision: request.revision + 1} : answer(request);
  }});
  controller.refresh(context(), ['a']); await done(controller); fail = true;
  controller.refresh(context('fine'), ['a']); await done(controller);
  assert.equal(controller.lookup('a').quality, 'standard');
  controller.refresh(context('fine'), ['a']); await done(controller); assert.equal(calls, 2);
  fail = false; controller.retry(); await done(controller);
  assert.equal(controller.lookup('a').quality, 'fine');
});

test('temporary backend suspension resumes unchanged ready geometry without overriding user cancellation', async () => {
  let release, calls = 0;
  const controller = createCadDisplay({refine: request => {
    calls++;
    if (calls === 1) return new Promise(resolve => { release = () => resolve(answer(request)); });
    return Promise.resolve(answer(request));
  }});
  controller.refresh(context(), ['a']); await delay();
  controller.suspend(); release(); await done(controller);
  assert.equal(calls, 1); assert.equal(controller.lookup('a'), null);
  await delay(); assert.equal(calls, 1, 'no restart before explicit graph work finishes');
  controller.refresh(context(), ['a']); await done(controller);
  assert.equal(calls, 2); assert.equal(controller.lookup('a').quality, 'standard');
  controller.cancel(); controller.suspend();
  controller.refresh(context(), ['a', 'b']); await done(controller);
  assert.equal(calls, 2, 'suspension does not clear a previous user Cancel');
  controller.retry(); await done(controller); assert.ok(controller.lookup('b'));
});

test('blocked/disabled work starts only when requested and scene reset discards buffers', async () => {
  let calls = 0;
  const controller = createCadDisplay({refine: async request => { calls++; return answer(request); }});
  controller.refresh(context(), ['a'], {blocked: true}); await done(controller); assert.equal(calls, 0);
  controller.refresh(context(), ['a'], {enabled: false}); await done(controller); assert.equal(calls, 0);
  controller.refresh(context(), ['a']); await done(controller); assert.equal(calls, 1);
  controller.reset(); assert.equal(controller.lookup('a'), null); assert.equal(controller.bytes, 0);
});

test('backend global cancellation pauses a multi-feature queue until explicit Retry', async () => {
  const requests = [];
  let stop = true;
  const controller = createCadDisplay({refine: async request => {
    requests.push(request.feature);
    if (stop) throw Object.assign(new Error('Stopped globally'), {code: 'computation_cancelled'});
    return answer(request);
  }});
  controller.refresh(context(), ['a', 'b']); await done(controller);
  controller.refresh(context(), ['a', 'b']); await done(controller);
  assert.deepEqual(requests, ['a']);
  stop = false; controller.retry(); await done(controller);
  assert.deepEqual(requests, ['a', 'a', 'b']);
});
