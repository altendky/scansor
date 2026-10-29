import assert from 'node:assert/strict';

// Observation only. All authoring, evaluation and reopening use product controls.
export const independentPlaneObserver = `\nObject.defineProperty(globalThis, '__independentPlaneUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration, vertices: metadata?.vertices})
}});\n`;

const reference = (feature, output = 'plane', context = feature) => ({feature, output, context});
const key = value => JSON.stringify(value);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const unit = value => value.map(component => component/Math.hypot(...value));
const normalError = (a, b) => Math.hypot(...cross(a, b));
const closeNumber = (actual, expected, tolerance = 1e-6) => assert(Math.abs(actual-expected) < tolerance, `${actual} != ${expected}`);

async function downloadBytes(download) {
  const parts = [];
  for await (const part of await download.createReadStream()) parts.push(part);
  return Buffer.concat(parts);
}

export async function exerciseIndependentPlaneUi({page, expect, fixture}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Evidence must use private previews');
  const report = {complete: false, phase: 'initialize'};
  const read = () => page.evaluate(() => globalThis.__independentPlaneUi.read());
  const graph = async () => (await read()).graph;
  const auto = page.getByRole('checkbox', {name: 'Auto', exact: true});
  const evaluateButton = page.getByRole('button', {name: 'Evaluate all', exact: true});
  const evaluateReady = async id => {
    await expect(evaluateButton).toBeEnabled(); await evaluateButton.click();
    await expect.poll(async () => {
      const state = await graph();
      if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }, {timeout: 60000}).toBe('ready');
    await expect(evaluateButton).toBeEnabled(); return graph();
  };
  const waitReady = async id => {
    await expect.poll(async () => {
      const state = await graph();
      if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }, {timeout: 60000}).toBe('ready');
    await expect(evaluateButton).toBeEnabled(); return graph();
  };
  const edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  const closeEditor = async () => {await page.keyboard.press('Escape'); await page.locator('#close-edit').click();};
  const normalFields = async (prefix, values) => {
    for (const [index, component] of ['x', 'y', 'z'].entries())
      await page.locator(`#${prefix}reference-plane-normal-${component}`).fill(String(values[index]));
  };
  const addIndependent = async (label, normal, offset) => {
    await page.getByRole('button', {name: 'Plane', exact: true}).click();
    await expect(page.locator('#new-reference-plane-construction')).toHaveValue('independent');
    await page.locator('#new-reference-plane-label').fill(label);
    await normalFields('new-', normal);
    await page.locator('#new-reference-plane-offset').fill(String(offset));
    await page.locator('#add-reference-plane-form').getByRole('button', {name: 'Add plane', exact: true}).click();
    await expect(page.locator('#reference-plane-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === label)).toBe(true);
    return (await graph()).recipe.nodes.find(node => node.label === label);
  };
  const addFit = async (label, kind, selection, datum = null) => {
    await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
    await page.locator('#new-fit-label').fill(label);
    await page.locator('#new-fit-kind').selectOption(kind);
    await page.locator('#new-fit-inputs').selectOption(selection);
    await page.locator('#new-fit-reference-mode').selectOption(datum ? 'fit_plane_orientation' : '');
    if (datum) await page.locator('#new-fit-reference').selectOption(key(reference(datum)));
    await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
    await expect(page.locator('#fit-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === label)).toBe(true);
    return (await graph()).recipe.nodes.find(node => node.label === label);
  };
  const addRelationship = async (kind, label, participants, pendingReference = null) => {
    await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
    await page.locator('#relationship-kind').selectOption(kind);
    if (await page.locator('#clear-relationship-participants').isEnabled())
      await page.locator('#clear-relationship-participants').click();
    await page.getByRole('button', {name: 'Show Participants choices', exact: true}).click();
    for (const participant of participants) {
      const inputs = page.locator('#relationship-participants input'), index = await inputs.evaluateAll(
        (elements, wanted) => elements.findIndex(element => element.dataset.participantId === wanted), key(participant));
      assert(index >= 0, `Missing exact relationship participant ${key(participant)}`);
      await expect(inputs.nth(index)).toBeEnabled();
      if (pendingReference && key(participant) === key(pendingReference))
        await expect(inputs.nth(index).locator('xpath=ancestor::label[1]')).toContainText('Needs evaluation');
      await inputs.nth(index).check();
    }
    await page.keyboard.press('Escape');
    const options = page.locator('#relationship-dialog details');
    if (!await options.evaluate(node => node.open)) await options.locator('summary').click();
    await page.locator('#new-relationship-label').fill(label);
    await page.locator('#add-relationship').click();
    await expect(page.locator('#relationship-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === label)).toBe(true);
    return (await graph()).recipe.nodes.find(node => node.label === label);
  };
  try {
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await expect(page.locator('#load')).toBeEnabled();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await expect.poll(async () => (await graph()).recipe.nodes.length).toBe(0);
    await auto.uncheck();
    await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream',
      buffer: Buffer.from(fixture.raw_base64, 'base64')});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
    const recipe = structuredClone(fixture.recipe);
    recipe.nodes = recipe.nodes.filter(node => ['source', 'selection'].includes(node.operation));
    recipe.output = recipe.nodes.at(-1).id;
    await page.locator('#file').setInputFiles({name: 'independent-plane-observations.json', mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(recipe))});
    await expect.poll(async () => (await graph()).recipe.nodes.filter(node => node.operation === 'selection').length).toBe(4);
    const source = structuredClone((await graph()).recipe.nodes.find(node => node.operation === 'source'));
    const assertObservations = async () => {
      const state = await graph(); assert.deepEqual(state.recipe.nodes.find(node => node.operation === 'source'), source);
      for (const [id, ids] of Object.entries(fixture.ids))
        assert.deepEqual(state.recipe.nodes.find(node => node.id === id+'-selection').ids, ids);
      assert.equal((await read()).vertices, fixture.vertices);
    };
    report.phase = 'no-axis-default-and-zero-normal';
    assert(!(await graph()).recipe.nodes.some(node => node.operation === 'axis'));
    await page.getByRole('button', {name: 'Plane', exact: true}).click();
    await expect(page.locator('#new-reference-plane-construction')).toHaveValue('independent');
    await expect(page.locator('#new-reference-plane-axis')).toBeDisabled();
    await expect(page.locator('#new-reference-plane-normal-z')).toBeVisible();
    const beforeInvalid = structuredClone((await graph()).recipe);
    await normalFields('new-', [0, 0, 0]);
    await page.locator('#add-reference-plane-form').getByRole('button', {name: 'Add plane', exact: true}).click();
    await expect(page.locator('#new-reference-plane-error')).toContainText('nonzero');
    await expect(page.locator('#reference-plane-dialog')).toBeVisible();
    assert.deepEqual((await graph()).recipe, beforeInvalid);
    await page.locator('#new-reference-plane-label').fill('Independent datum');
    await normalFields('new-', [0.2, 0.1, 2]);
    await page.locator('#new-reference-plane-offset').fill('4');
    await page.locator('#add-reference-plane-form').getByRole('button', {name: 'Add plane', exact: true}).click();
    await expect(page.locator('#reference-plane-dialog')).toBeHidden();
    const datum = (await graph()).recipe.nodes.find(node => node.label === 'Independent datum');
    assert(datum); assert.equal(datum.axis, null); assert.equal(datum.initial_angle_degrees, null);
    assert.deepEqual(datum.initial_normal, [0.2, 0.1, 2]); assert.equal(datum.offset, 4);
    let state = await evaluateReady(datum.id), equation = state.results[datum.id].plane_equation;
    assert(normalError(equation.slice(0, 3), unit([0.2, 0.1, 2])) < 1e-9); closeNumber(equation[3], 4);
    report.phase = 'property-values-and-legacy-switch';
    await edit(datum.id); await normalFields('', [0.15, -0.1, 1.5]);
    await page.locator('#reference-plane-offset').fill('5'); await page.locator('#apply-properties').click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === datum.id).offset).toBe(5);
    assert.deepEqual((await graph()).recipe.nodes.find(node => node.id === datum.id).initial_normal, [0.15, -0.1, 1.5]);
    await closeEditor();
    await page.getByRole('button', {name: 'Axis', exact: true}).click();
    await page.locator('#new-axis-label').fill('Legacy construction axis');
    await page.locator('#add-axis-form').getByRole('button', {name: 'Add axis', exact: true}).click();
    await expect(page.locator('#axis-dialog')).toBeHidden();
    const axis = (await graph()).recipe.nodes.find(node => node.label === 'Legacy construction axis'); assert(axis);
    // Put the existing datum after its newly authored dependency using the actual tree move command.
    await page.locator(`#action-list .action-select[data-action-id="${datum.id}"]`).locator('xpath=ancestor::li[1]').focus();
    await page.keyboard.press('Alt+ArrowDown');
    await expect.poll(async () => {
      const nodes = (await graph()).recipe.nodes;
      return nodes.findIndex(node => node.id === axis.id) < nodes.findIndex(node => node.id === datum.id);
    }).toBe(true);
    await edit(datum.id); await page.locator('#reference-plane-construction').selectOption('perpendicular_to_axis');
    await expect(page.locator('#reference-plane-normal-z')).toBeDisabled();
    await page.locator('#reference-plane-axis').selectOption(axis.id); await page.locator('#reference-plane-offset').fill('3');
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === datum.id).construction).toBe('perpendicular_to_axis');
    let declared = (await graph()).recipe.nodes.find(node => node.id === datum.id);
    assert.equal(declared.axis, axis.id); assert.equal(declared.initial_normal, null); assert.equal(declared.initial_angle_degrees, null);
    await closeEditor(); state = await evaluateReady(datum.id); closeNumber(state.results[datum.id].offset, 3);
    await edit(datum.id); await page.locator('#reference-plane-construction').selectOption('independent');
    await normalFields('', [0.2, 0.1, 2]); await page.locator('#reference-plane-offset').fill('4');
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === datum.id).construction).toBe('independent');
    declared = (await graph()).recipe.nodes.find(node => node.id === datum.id); assert.equal(declared.axis, null);
    assert.deepEqual(declared.initial_normal, [0.2, 0.1, 2]); await closeEditor();
    report.phase = 'pending-plane-context';
    const bound = await addFit('Plane observations on datum', 'plane', 'joint-plane-selection', datum.id);
    const cylinder = await addFit('Direction follower cylinder', 'cylinder', 'axis-cylinder-selection');
    const exact = reference(datum.id, 'plane', '@plane/'+datum.id), cylinderReference = reference(cylinder.id, 'axis');
    const pending = (await graph()).input_requirements.relationship_direction.unavailable.find(output => key(output.reference) === key(exact));
    assert(pending?.compatible && ['stale', 'unevaluated'].includes(pending.availability));
    const alignment = await addRelationship('direction_alignment', 'Cylinder follows exact plane output', [exact, cylinderReference], exact);
    assert.deepEqual(alignment.directions.map(key).sort(), [exact, cylinderReference].map(key).sort());
    assert.equal((await graph()).results[alignment.id], undefined);
    report.phase = 'free-orientation-unused-offset';
    await auto.check(); state = await waitReady(alignment.id);
    const solvedDatum = state.results[datum.id].plane_equation, solvedFit = state.results[bound.id].plane_equation;
    assert(normalError(solvedDatum.slice(0, 3), fixture.truth['joint-plane'].direction) < 1e-6);
    assert(normalError(solvedDatum.slice(0, 3), unit([0.2, 0.1, 2])) > 0.05);
    closeNumber(solvedDatum[3], 4); assert(Math.abs(solvedFit[3]-4) > 1);
    assert(normalError(solvedDatum.slice(0, 3), state.results[cylinder.id].analytic.axis) < 1e-6);
    assert.deepEqual(state.recipe.nodes.find(node => node.id === datum.id).initial_normal, [0.2, 0.1, 2]);
    closeNumber(state.recipe.nodes.find(node => node.id === datum.id).offset, 4);
    await assertObservations();
    report.phase = 'free-coincident-offset';
    await auto.uncheck();
    const offsetDatum = await addIndependent('Movable offset datum', [0, 0, 1], 30);
    const independentFit = await addFit('Independent observed plane', 'plane', 'joint-plane-selection');
    const coincident = await addRelationship('coincident_planes', 'Datum joins observed plane', [reference(offsetDatum.id), reference(independentFit.id)]);
    await auto.check(); state = await waitReady(coincident.id);
    const moved = state.results[offsetDatum.id].plane_equation, observed = state.results[independentFit.id].plane_equation;
    assert(normalError(moved.slice(0, 3), observed.slice(0, 3)) < 1e-6);
    closeNumber(moved[3], observed[3]); assert(Math.abs(moved[3]-30) > 10);
    assert(normalError(moved.slice(0, 3), fixture.truth['joint-plane'].direction) < 1e-6);
    closeNumber(state.recipe.nodes.find(node => node.id === offsetDatum.id).offset, 30);
    await assertObservations();
    report.phase = 'actions-reopen';
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Save actions', exact: true}).click();
    const savedActions = await downloadBytes(await downloading), before = structuredClone(state.recipe);
    await page.locator('#file').setInputFiles({name: 'independent-plane-actions.json', mimeType: 'application/json', buffer: savedActions});
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.id === coincident.id)).toBe(true);
    state = await waitReady(coincident.id); assert.deepEqual(state.recipe, before);
    assert.deepEqual(state.recipe.nodes.find(node => node.id === alignment.id).directions, alignment.directions);
    await assertObservations();
    report.complete = true; report.phase = 'complete';
    Object.assign(report, {no_axis_creation: true, independent_default: true, zero_normal_rejected: true,
      initial_normal_and_offset: true, properties_edit: true, legacy_switch_roundtrip: true,
      exact_pending_plane_context: true, fitted_orientation_with_unused_offset_preserved: true,
      coincident_offset_adjustable: true, authored_initializers_preserved: true, actions_reopen: true,
      source_and_observation_ids_preserved: true});
    return report;
  } catch (error) {error.message = `${report.phase}: ${error.message}`; throw error;}
}
