import assert from 'node:assert/strict';

// Observation only: all graph writes and evaluation use product controls.
export const staleRelationshipObserver = `\nObject.defineProperty(globalThis, '__staleRelationshipUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration, vertices: metadata?.vertices,
    relationshipGuides: relationshipOverlays.children.length,
    pendingGeometry: requirementOutputs(graphState, 'relationship_direction')
      .filter(output => output.reference.feature === 'axis-plane' && output.availability !== 'ready')
      .map(output => ({reference: output.reference, value: relationshipOutputValue(graphState, output)}))
  })
}});\n`;

const reference = (feature, output, context = feature) => ({feature, output, context});
const key = value => JSON.stringify(value);
const sameReferences = values => values.map(key).sort();

export async function exerciseStaleRelationshipUi({page, expect, fixture}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Evidence must use a private preview');
  const report = {complete: false, phase: 'import'};
  const evaluations = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/graph\/(?:evaluate|ensure)(?:\?|$)/.test(request.url()))
      evaluations.push(request.url());
  });
  const read = () => page.evaluate(() => globalThis.__staleRelationshipUi.read());
  const auto = page.getByRole('checkbox', {name: 'Auto', exact: true});
  const graph = async () => (await read()).graph;
  const edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  const close = async () => {
    await page.keyboard.press('Escape');
    await page.locator('#close-edit').click();
  };
  const chooseParticipant = async value => {
    const inputs = page.locator('#relationship-participants input'), wanted = key(value),
      index = await inputs.evaluateAll((items, wanted) => items.findIndex(item => item.dataset.participantId === wanted), wanted);
    assert(index >= 0, `Missing exact pending participant ${wanted}`);
    await expect(inputs.nth(index)).toBeEnabled();
    await expect(inputs.nth(index).locator('xpath=ancestor::label[1]')).toContainText('Needs evaluation');
    await inputs.nth(index).check();
  };
  const openBuilder = async () => {
    await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
    await page.locator('#relationship-kind').selectOption('direction_alignment');
    if (await page.locator('#clear-relationship-participants').isEnabled())
      await page.locator('#clear-relationship-participants').click();
    await page.getByRole('button', {name: 'Show Participants choices', exact: true}).click();
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
    const recipe = structuredClone(fixture.recipe), firstFit = recipe.nodes.findIndex(node => node.operation === 'fit');
    recipe.nodes.splice(firstFit, 0,
      {id: 'auto-axis', label: 'Observed shared axis', operation: 'axis', initial_parameters: [8, -3, 0.055, -0.015]},
      {id: 'axis-plane', label: 'Pending reference plane', operation: 'reference_plane', axis: 'auto-axis',
        construction: 'perpendicular_to_axis', initial_angle_degrees: null, offset: -12});
    recipe.nodes.find(node => node.id === 'axis-cylinder').axis = 'auto-axis';
    await page.locator('#file').setInputFiles({name: 'pending-direction-actions.json', mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(recipe))});
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.id === 'axis-plane')).toBe(true);
    const source = structuredClone((await graph()).recipe.nodes.find(node => node.operation === 'source'));
    const plane = reference('axis-plane', 'plane', '@axis/auto-axis'), member = reference('plane-cylinder', 'axis');
    const assertIntent = async () => {
      const state = await graph();
      assert.deepEqual(state.recipe.nodes.find(node => node.operation === 'source'), source);
      for (const [id, ids] of Object.entries(fixture.ids)) {
        assert.deepEqual(state.recipe.nodes.find(node => node.id === id+'-selection').ids, ids);
        assert.equal(state.recipe.nodes.find(node => node.id === id).kind, id === 'joint-plane' ? 'plane' : 'cylinder');
      }
    };
    const pending = (await graph()).input_requirements.relationship_direction.unavailable
      .find(output => key(output.reference) === key(plane));
    assert(pending?.compatible && ['unevaluated', 'stale'].includes(pending.availability));
    report.phase = 'create-before-evaluation';
    const beforeCreate = evaluations.length;
    await openBuilder();
    await chooseParticipant(plane);
    assert.equal((await read()).relationshipGuides, 0);
    const memberInputs = page.locator('#relationship-participants input'), memberIndex = await memberInputs.evaluateAll(
      (items, wanted) => items.findIndex(item => item.dataset.participantId === wanted), key(member));
    assert(memberIndex >= 0); await memberInputs.nth(memberIndex).check();
    await expect(page.locator('#relationship-summary')).toContainText('will need evaluation');
    await page.keyboard.press('Escape');
    const options = page.locator('#relationship-dialog details');
    if (!await options.evaluate(node => node.open)) await options.locator('summary').click();
    await page.locator('#new-relationship-label').fill('Pending exact plane normal');
    await page.locator('#add-relationship').click();
    await expect(page.locator('#relationship-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === 'Pending exact plane normal')).toBe(true);
    const relation = (await graph()).recipe.nodes.find(node => node.label === 'Pending exact plane normal');
    assert.deepEqual(sameReferences(relation.directions), sameReferences([plane, member]));
    assert.equal(evaluations.length, beforeCreate);
    assert.equal((await graph()).results[relation.id], undefined);
    assert.equal((await graph()).derived['axis-cylinder'], undefined);
    report.phase = 'auto-evaluate-dependencies';
    await auto.check();
    await expect.poll(async () => {
      const state = await graph();
      if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
      return state.states[relation.id];
    }, {timeout: 60000}).toBe('ready');
    await expect(page.getByRole('button', {name: 'Evaluate all', exact: true})).toBeEnabled();
    let state = await graph();
    assert.equal(state.states['axis-cylinder'], 'ready');
    assert.equal(state.states['axis-plane'], 'ready');
    const warmPlane = state.input_requirements.relationship_direction.choices.find(output => key(output.reference) === key(plane));
    assert(warmPlane?.preview?.plane_equation, 'The reference plane had actual ready geometry before invalidation');
    assert.deepEqual(sameReferences(state.recipe.nodes.find(node => node.id === relation.id).directions), sameReferences([plane, member]));
    await assertIntent();
    report.phase = 'invalidate-upstream';
    await auto.uncheck();
    await edit('auto-axis');
    await page.locator('#axis-direction-x').fill('0.06');
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === 'auto-axis').initial_parameters[2]).toBe(0.06);
    await expect.poll(async () => (await graph()).states[relation.id]).toBe('stale');
    await close();
    const staleState = await graph(), stalePlane = staleState.input_requirements.relationship_direction.unavailable
      .find(output => key(output.reference) === key(plane));
    assert(stalePlane?.compatible && stalePlane.availability === 'stale');
    assert.equal(stalePlane.preview, null, 'The formerly ready plane no longer supplies a stale preview');
    report.phase = 'stale-builder-preview';
    await openBuilder();
    await chooseParticipant(plane);
    await expect.poll(async () => (await read()).relationshipGuides).toBe(0);
    for (const pending of (await read()).pendingGeometry) assert.equal(pending.value, null);
    await close();
    report.phase = 'stale-property-input';
    const beforeApply = evaluations.length;
    await edit(relation.id);
    const select = page.locator('#direction-alignment-inputs'), option = select.locator('option').filter({hasText: 'Pending reference plane'});
    const exactOption = select.locator('option').filter({hasText: 'Needs evaluation'});
    assert(await option.count() >= 1); assert(await exactOption.count() >= 1);
    await select.selectOption([key(plane), key(member)]);
    const saved = await select.evaluate(control => [...control.selectedOptions].map(option => ({value: option.value, disabled: option.disabled})));
    assert(saved.every(option => !option.disabled));
    await page.locator('#apply-properties').click();
    await expect.poll(async () => sameReferences((await graph()).recipe.nodes.find(node => node.id === relation.id).directions))
      .toEqual(sameReferences([plane, member]));
    assert.equal(evaluations.length, beforeApply);
    assert.equal((await graph()).states[relation.id], 'stale');
    await assertIntent();
    await close();
    await auto.check();
    await expect.poll(async () => {
      const current = await graph();
      if (Object.keys(current.errors).length) throw Error(JSON.stringify(current.errors));
      return current.states[relation.id];
    }, {timeout: 60000}).toBe('ready');
    await assertIntent();
    report.complete = true;
    report.phase = 'complete';
    report.reference = plane;
    report.auto_off_creation_without_evaluation = true;
    report.auto_on_evaluates_prerequisites = true;
    report.stale_inputs_visible_enabled = true;
    report.stale_geometry_not_previewed = true;
    report.warm_to_stale_preview_cleared = true;
    report.property_context_preserved = true;
    report.source_and_observations_preserved = true;
    return report;
  } catch (error) {
    error.message = `${report.phase}: ${error.message}`;
    throw error;
  }
}
