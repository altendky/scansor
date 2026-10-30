import assert from 'node:assert/strict';

// Observation only: controls and real pointer events own all authoring/picking.
export const relationshipHoverObserver = `\nObject.defineProperty(globalThis, '__relationshipHoverUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration,
    mode: modelPickMode, role: relationshipActiveRole, selected: [...relationshipParticipantIds],
    camera: [...camera.position.toArray(), ...controls.target.toArray()],
    positions: Array.from(positions || []), colors: Array.from(mesh.geometry.getAttribute('color').array),
    overlays: relationshipOverlays.children.filter(child => child.userData.relationshipSupport || child.userData.relationshipGuide).map(child => ({
      type: child.type, support: child.userData.relationshipSupport ? {...child.userData.relationshipSupport,
        ids: Array.from(child.userData.relationshipSupport.ids)} : null,
      guide: child.userData.relationshipGuide || null,
      positions: Array.from(child.geometry.getAttribute('position')?.array || []),
      color: child.material.color?.toArray(), depthTest: child.material.depthTest,
      pickGuide: !!child.userData.pickGuide, pickKey: child.userData.pickKey || null
    }))}),
  candidates: ids => {
    const wanted = new Set(ids), indices = mesh.geometry.index.array, canvas = renderer.domElement,
      box = canvas.getBoundingClientRect(), result = [], seen = new Set();
    for (let offset = 0; offset < indices.length; offset += 3) {
      const triangle = Array.from(indices.subarray(offset, offset + 3));
      if (!triangle.every(id => wanted.has(id))) continue;
      const vertex = new THREE.Vector3();
      for (const id of triangle) vertex.add(new THREE.Vector3(...positions.subarray(id * 3, id * 3 + 3)));
      vertex.multiplyScalar(1 / 3).applyMatrix4(modelRoot.matrixWorld).project(camera);
      const x = box.left + (vertex.x + 1) * box.width / 2, y = box.top + (1 - vertex.y) * box.height / 2,
        key = Math.round(x / 5) + ':' + Math.round(y / 5);
      if (vertex.z < -1 || vertex.z > 1 || seen.has(key) || document.elementFromPoint(x, y) !== canvas) continue;
      seen.add(key); result.push({x, y});
    }
    return result.sort((a, b) => Math.hypot(a.x - box.left - box.width / 2, a.y - box.top - box.height / 2)
      - Math.hypot(b.x - box.left - box.width / 2, b.y - box.top - box.height / 2)).slice(0, 48);
  }
}});\n`;

const reference = feature => ({feature, output: 'axis', context: feature});
const key = value => JSON.stringify(value);
const norm = value => Math.hypot(...value);
const delta = (a, b) => a.map((value, index) => value - b[index]);
const dot = (a, b) => a.reduce((sum, value, index) => sum + value * b[index], 0);
const supports = state => state.overlays.filter(overlay => overlay.support);

function scaledFixture(fixture, scale) {
  const raw = Buffer.from(fixture.raw_base64, 'base64'), header = raw.indexOf(Buffer.from('end_header\n')) + 11;
  assert(header >= 11);
  for (let vertex = 0; vertex < fixture.vertices; vertex++) for (let axis = 0; axis < 3; axis++) {
    const offset = header + vertex * 24 + axis * 4;
    raw.writeFloatLE(raw.readFloatLE(offset) * scale + (axis === 2 ? 100 * scale : 0), offset);
  }
  const recipe = structuredClone(fixture.recipe);
  for (const node of recipe.nodes) {
    if (node.initial_coordinates) node.initial_coordinates = node.initial_coordinates
      .map((value, axis) => value * scale + (axis === 2 ? 100 * scale : 0));
    if (node.operation === 'reference_plane') node.offset *= scale;
  }
  return {raw, recipe};
}

function assertSupport(state, overlay, ids, references) {
  assert(overlay, 'The supporting observation patch must have a visible colored overlay');
  const expected = [...new Set(ids)].sort((a, b) => a - b);
  assert.deepEqual(overlay.support.ids, expected);
  assert.deepEqual(overlay.positions, expected.flatMap(id => state.positions.slice(id * 3, id * 3 + 3)));
  assert.equal(overlay.support.sampled, false);
  assert.equal(overlay.depthTest, false);
  assert.equal(overlay.pickGuide, false, 'Feedback points cannot become independent picking evidence');
  assert.equal(overlay.pickKey, null);
  for (const value of references) assert(overlay.support.references.some(found => key(found) === key(value)), 'Retain the exact output context');
}

function assertAxisGuide(state, exact, ids) {
  const guide = state.overlays.find(overlay => overlay.guide && key(overlay.guide.reference) === key(exact)
    && overlay.type === 'Line' && overlay.positions.length === 6);
  assert(guide, 'The property axis must be drawn as well as its observation patch');
  const first = guide.positions.slice(0, 3), last = guide.positions.slice(3, 6), center = first.map((value, index) => (value + last[index]) / 2),
    mean = [0, 0, 0], low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (const id of ids) for (let axis = 0; axis < 3; axis++) {
    const value = state.positions[id * 3 + axis]; mean[axis] += value / ids.length;
    low[axis] = Math.min(low[axis], value); high[axis] = Math.max(high[axis], value);
  }
  const axis = guide.guide.axis, origin = guide.guide.origin,
    projected = origin.map((value, index) => value + axis[index] * dot(delta(mean, origin), axis)),
    patchSize = norm(delta(high, low)), length = norm(delta(last, first));
  assert(norm(delta(center, projected)) < patchSize * 1e-5, 'Center the guide on the observations projected onto the exact fitted axis');
  assert(norm(center) > patchSize * 5, 'Exercise a guide centered on imported observations far from the world origin');
  assert(length > patchSize * .5 && length < patchSize * 4, 'Scale the guide to its observations instead of a small fixed display length');
  assert(length > 100, 'Exercise the imported scan at a scale that exposes the old tiny-guide defect');
  return {length, patchSize};
}

export async function exerciseRelationshipHoverUi({page, expect, fixture}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Hover proof requires a private preview');
  const report = {complete: false, phase: 'initialize', cases: []}, requests = [],
    read = () => page.evaluate(() => globalThis.__relationshipHoverUi.read()),
    graph = async () => (await read()).graph,
    evaluate = async () => {
      await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
      await expect.poll(async () => {
        const state = await graph(); if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
        return Object.values(state.states).every(value => value === 'ready');
      }, {timeout: 60000}).toBe(true);
      await expect(page.getByRole('button', {name: 'Evaluate all', exact: true})).toBeEnabled();
    },
    begin = async kind => {
      await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
      await page.locator('#relationship-kind').selectOption(kind);
      if (await page.locator('#clear-relationship-participants').isEnabled()) await page.locator('#clear-relationship-participants').click();
      const role = kind === 'coaxial' ? 'members' : 'participants';
      await page.locator(`#relationship-dialog [data-relationship-role="${role}"] .model-field-pick`).click();
    },
    hover = async (ids, accept) => {
      const candidates = await page.evaluate(ids => globalThis.__relationshipHoverUi.candidates(ids), ids);
      assert(candidates.length, 'The imported patch must have visible triangle interiors');
      for (const point of candidates) {
        await page.mouse.move(point.x, point.y);
        const state = await read();
        if (accept(state)) return {point, state};
      }
      throw Error('No real raycast hover identified the expected exact candidate');
    },
    leave = async () => {
      await page.mouse.move(4, 4);
      await expect.poll(async () => supports(await read()).filter(overlay => overlay.support.kind !== 'selected').length).toBe(0);
    },
    close = async () => {
      if ((await read()).mode) await page.keyboard.press('Escape');
      if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
      else await page.locator('#relationship-dialog').getByRole('button', {name: 'Cancel', exact: true}).click();
    },
    load = async recipe => {
      await page.locator('#file').setInputFiles({name: 'hover-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
      await expect(page.locator('#source-progress')).toBeHidden();
      await expect.poll(async () => (await graph()).recipe.nodes.map(node => node.id)).toEqual(recipe.nodes.map(node => node.id));
      await evaluate();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/graph\/(?:evaluate|ensure)(?:\?|$)/.test(request.url())) requests.push(request.url());
  });
  try {
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    const {raw, recipe} = scaledFixture(fixture, 1000), source = recipe.nodes.find(node => node.operation === 'source');
    await page.locator('#scan-file').setInputFiles({name: 'large-relationship-hover.ply', mimeType: 'application/octet-stream', buffer: raw});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.operation === 'source')).toBe(true);
    const accepted = (await graph()).recipe.nodes.find(node => node.operation === 'source'), oldSource = source.id;
    Object.assign(source, accepted);
    for (const node of recipe.nodes) if (node.source === oldSource) node.source = source.id;
    await load(recipe);
    const baseline = await read(), first = reference('axis-cylinder'), second = reference('plane-cylinder');
    for (const kind of ['coaxial', 'direction_alignment']) {
      report.phase = kind + '-single-hover';
      const before = requests.length;
      await begin(kind);
      const firstHover = await hover(fixture.ids['axis-cylinder'], state => supports(state).some(overlay =>
        overlay.support.kind === 'hover' && overlay.support.references.some(value => key(value) === key(first))));
      const firstOverlay = supports(firstHover.state).find(overlay => overlay.support.kind === 'hover');
      assertSupport(firstHover.state, firstOverlay, fixture.ids['axis-cylinder'], [first]);
      const scale = assertAxisGuide(firstHover.state, first, fixture.ids['axis-cylinder']);
      await page.mouse.click(firstHover.point.x, firstHover.point.y);
      await expect.poll(async () => (await read()).selected).toEqual([key(first)]);
      await leave();
      const selected = supports(await read()).find(overlay => overlay.support.kind === 'selected');
      assertSupport(await read(), selected, fixture.ids['axis-cylinder'], [first]);
      const secondHover = await hover(fixture.ids['plane-cylinder'], state => supports(state).some(overlay =>
        overlay.support.kind === 'hover' && overlay.support.references.some(value => key(value) === key(second))));
      const hovered = supports(secondHover.state).find(overlay => overlay.support.kind === 'hover');
      assertSupport(secondHover.state, hovered, fixture.ids['plane-cylinder'], [second]);
      assertAxisGuide(secondHover.state, second, fixture.ids['plane-cylinder']);
      assert(norm(delta(hovered.color, selected.color)) > .05, 'Hover and selected observations need distinct colors');
      const ordinary = baseline.colors.slice(fixture.ids['axis-cylinder'][0] * 3, fixture.ids['axis-cylinder'][0] * 3 + 3);
      assert(norm(delta(hovered.color, ordinary)) > .05 && norm(delta(selected.color, ordinary)) > .05);
      if (kind === 'coaxial') {
        const referencePick = page.locator('#relationship-dialog [data-relationship-role="axis"] .model-field-pick');
        await referencePick.focus(); await page.keyboard.press('Enter');
        assert.equal(supports(await read()).filter(overlay => overlay.support.kind !== 'selected').length, 0, 'Changing the active role clears hover');
      } else {
        await page.keyboard.press('Escape');
        assert.equal(supports(await read()).filter(overlay => overlay.support.kind !== 'selected').length, 0, 'Escape clears hover');
      }
      await close();
      await expect.poll(async () => (await read()).overlays.length).toBe(0);
      assert.equal(requests.length, before, 'Hover, picking, role switching and cancellation must not evaluate');
      assert.deepEqual((await graph()).recipe, baseline.graph.recipe);
      assert.deepEqual((await read()).camera, baseline.camera);
      report.cases.push({kind, exact_support_ids: true, exact_reference_selected: true, scale_relative_guide: scale.length / scale.patchSize,
        distinct_colors: true, cleared: true, no_evaluation: true});
    }
    report.phase = 'overlapping-candidates';
    const overlapping = structuredClone(recipe), half = fixture.ids['axis-cylinder'].filter((_, index) => index % 2 === 0),
      selection = overlapping.nodes.find(node => node.id === 'axis-cylinder-selection'), fit = overlapping.nodes.find(node => node.id === 'axis-cylinder'),
      other = reference('overlap-cylinder');
    overlapping.nodes.splice(overlapping.nodes.findIndex(node => node.operation === 'fit'), 0,
      {...selection, id: 'overlap-selection', label: 'Overlapping observations', ids: half});
    overlapping.nodes.push({...fit, id: other.feature, label: 'Overlapping half patch', selections: ['overlap-selection']});
    await load(overlapping);
    const beforeOverlap = requests.length, acceptedRecipe = structuredClone((await graph()).recipe);
    await begin('coaxial');
    const overlap = await hover(fixture.ids['axis-cylinder'], state => supports(state).some(overlay => overlay.support.kind === 'overlap'));
    const union = supports(overlap.state).find(overlay => overlay.support.kind === 'overlap');
    assertSupport(overlap.state, union, fixture.ids['axis-cylinder'], [first, other]);
    await expect(page.locator('#model-pick-message')).toContainText('2');
    assert.deepEqual(overlap.state.selected, []);
    await page.mouse.click(overlap.point.x, overlap.point.y);
    const chooser = page.locator('#model-pick-choices'); await expect(chooser).toBeVisible();
    await expect(chooser.locator('button')).toHaveCount(2);
    await chooser.locator('button').first().focus(); await page.keyboard.press('Tab');
    const focus = await read(), focusOverlay = supports(focus).find(overlay => overlay.support.kind === 'hover');
    assertSupport(focus, focusOverlay, half, [other]);
    await page.keyboard.press('Enter'); await expect(chooser).toBeHidden();
    await expect.poll(async () => (await read()).selected).toEqual([key(other)]);
    await leave();
    assertSupport(await read(), supports(await read()).find(overlay => overlay.support.kind === 'selected'), half, [other]);
    // Dock the live editor so the normal viewport display controls remain directly reachable.
    await page.keyboard.press('Escape');
    await page.locator('#dock-edit').click();
    const display = page.locator('#viewport .display-options');
    if (!await display.evaluate(element => element.open)) await display.locator('summary').click();
    await page.locator('#faces-only').check();
    await expect.poll(async () => supports(await read()).length).toBe(0);
    await page.locator('#faces-only').uncheck();
    await expect.poll(async () => supports(await read()).some(overlay => overlay.support.kind === 'selected')).toBe(true);
    assertSupport(await read(), supports(await read()).find(overlay => overlay.support.kind === 'selected'), half, [other]);
    // The new role cannot use cylinder axes: an eligibility change must discard their hover evidence.
    await page.locator('#relationship-kind').selectOption('parallel_planes');
    assert.equal(supports(await read()).filter(overlay => overlay.support.kind !== 'selected').length, 0);
    await close();
    assert.equal(requests.length, beforeOverlap);
    assert.deepEqual((await graph()).recipe, acceptedRecipe);
    report.cases.push({kind: 'overlap', union_exact: true, chooser_focus_exact_subset: true, exact_reference_selected: true,
      eligibility_change_clears_hover: true, faces_only_clears_support: true, no_evaluation: true});
    report.complete = true; report.phase = 'complete'; return report;
  } catch (error) {
    error.message = `Relationship hover (${report.phase}): ${error.message}`;
    throw error;
  }
}
