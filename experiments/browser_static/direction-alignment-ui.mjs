import assert from 'node:assert/strict';

// Observation only. Product controls own all edits, solves, and file operations.
export const directionAlignmentObserver = `\nlet directionObservedGraph, directionObservedPublication = 0;
Object.defineProperty(globalThis, '__directionAlignmentUi', {value: {
  read: () => {
    if (graphState !== directionObservedGraph) {
      directionObservedGraph = graphState; directionObservedPublication++;
    }
    return structuredClone({graph: graphState, publication: directionObservedPublication,
      generation: workspaceGeneration, vertices: metadata?.vertices});
  }
}});\n`;

const reference = (feature, output = 'axis', context = feature) => JSON.stringify({feature, output, context});
const dot = (a, b) => a.reduce((sum, value, i) => sum + value*b[i], 0);
const norm = a => Math.hypot(...a);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const delta = (a, b) => a.map((value, i) => value-b[i]);
const downloadBytes = async download => {
  const parts = [];
  for await (const part of await download.createReadStream()) parts.push(part);
  return Buffer.concat(parts);
};

async function exerciseRelationshipGroupsUi({page, expect, fixture, read, graph, evaluateReady, edit, report}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Group acceptance must use a private preview');
  const evaluations = [], auto = page.getByRole('checkbox', {name: 'Auto', exact: true});
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/graph\/(?:evaluate|ensure)(?:\?|$)/.test(request.url())) evaluations.push(request.url());
  });
  const loadActions = async (recipe, name) => {
    const publication = (await read()).publication;
    await page.locator('#file').setInputFiles({name, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await read()).publication).toBeGreaterThan(publication);
    await expect.poll(async () => (await graph()).recipe.nodes.map(node => node.id)).toEqual(recipe.nodes.map(node => node.id));
  };
  const begin = async (kind, label) => {
    await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
    await page.locator('#relationship-kind').selectOption(kind);
    if (await page.locator('#clear-relationship-participants').isEnabled()) await page.locator('#clear-relationship-participants').click();
    const options = page.locator('#relationship-dialog details');
    if (!await options.evaluate(node => node.open)) await options.locator('summary').click();
    await page.locator('#new-relationship-label').fill(label);
  };
  const add = async label => {
    await expect(page.locator('#add-relationship')).toBeEnabled(); await page.locator('#add-relationship').click();
    await expect(page.locator('#relationship-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === label)).toBe(true);
    return (await graph()).recipe.nodes.find(node => node.label === label);
  };
  const input = (container, value) => container.locator(`input[data-participant-id=${JSON.stringify(value)}]`);
  const roundTrip = async (id, prefix) => {
    const recipe = structuredClone((await graph()).recipe);
    let downloading = page.waitForEvent('download'); await page.getByRole('button', {name: 'Save actions', exact: true}).click();
    const actions = await downloadBytes(await downloading); assert.deepEqual(JSON.parse(actions), recipe);
    await loadActions(recipe, `${prefix}-actions.json`); await evaluateReady(id);
    assert.deepEqual((await graph()).recipe, recipe);
    downloading = page.waitForEvent('download'); await page.getByRole('button', {name: 'Save project', exact: true}).click();
    const archive = await downloadBytes(await downloading), generation = (await read()).generation;
    await page.locator('#project-file').setInputFiles({name: `${prefix}.scansor`, mimeType: 'application/zip', buffer: archive});
    await expect.poll(async () => (await read()).generation).toBeGreaterThan(generation);
    await expect(page.locator('#source-progress')).toBeHidden(); await evaluateReady(id);
    assert.deepEqual((await graph()).recipe, recipe);
  };
  await page.locator('#close-edit').click();
  await auto.uncheck();
  report.phase = 'four-participant-pending-alignment';
  await loadActions(fixture.recipe, 'four-directions-fixture.json');
  const participants = [reference('axis-cylinder'), reference('plane-cylinder'), reference('joint-plane', 'plane'), reference('fixed-axis')],
    label = 'Four mixed aligned directions', beforeGroup = evaluations.length;
  await begin('direction_alignment', label);
  await page.getByRole('button', {name: 'Show Apply to choices', exact: true}).click();
  for (const value of participants) await input(page.locator('[data-relationship-role="participants"]'), value).check();
  await expect(page.locator('#relationship-count')).toHaveText('4 selected');
  await page.keyboard.press('Escape');
  const alignment = await add(label);
  assert.deepEqual(new Set(alignment.directions.map(value => JSON.stringify(value))), new Set(participants));
  assert.equal((await graph()).recipe.nodes.filter(node => node.operation === 'direction_alignment').length, 1);
  assert.equal((await graph()).results[alignment.id], undefined);
  assert.equal(evaluations.length, beforeGroup, 'Auto-off group authoring must not trigger evaluation');
  const checkFour = state => {
    const direction = state.results['fixed-axis'].axis_display;
    for (const id of ['axis-cylinder', 'plane-cylinder']) {
      const surface = state.results[id].analytic;
      assert(norm(cross(surface.axis, direction)) < 1e-7, `${id} participates in the four-way constraint`);
      assert(norm(cross(delta(surface.origin, state.results['fixed-axis'].point_display), direction)) > 7,
        'Direction alignment must preserve independent transverse positions');
      assert(Math.abs(surface.radius - fixture.truth[id].radius) < .04);
    }
    assert(norm(cross(state.results['joint-plane'].plane_equation.slice(0, 3), direction)) < 1e-7);
    for (const [name, ids] of Object.entries(fixture.ids))
      assert.deepEqual(state.recipe.nodes.find(node => node.id === name + '-selection').ids, ids);
    return Object.fromEntries(['axis-cylinder', 'plane-cylinder'].map(id => [id, state.results[id].analytic]));
  };
  const first = checkFour(await evaluateReady(alignment.id));
  report.phase = 'three-and-four-participant-edit';
  await edit(alignment.id);
  assert.deepEqual(new Set(await page.locator('#direction-alignment-inputs')
    .evaluate(control => [...control.selectedOptions].map(option => option.value))), new Set(participants));
  await page.locator('#direction-alignment-inputs').selectOption(participants.slice(0, 3));
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === alignment.id).directions.length).toBe(3);
  let state = await evaluateReady(alignment.id);
  const three = [state.results['axis-cylinder'].analytic.axis, state.results['plane-cylinder'].analytic.axis,
    state.results['joint-plane'].plane_equation.slice(0, 3)];
  assert(three.every(direction => norm(cross(direction, three[0])) < 1e-7));
  await page.locator('#direction-alignment-inputs').selectOption(participants);
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === alignment.id).directions.length).toBe(4);
  await page.locator('#close-edit').click();
  report.phase = 'reordered-group-and-round-trips';
  const reversed = structuredClone((await graph()).recipe);
  reversed.nodes.find(node => node.id === alignment.id).directions.reverse();
  await loadActions(reversed, 'reversed-four-directions.json');
  state = await evaluateReady(alignment.id);
  const reordered = checkFour(state);
  assert.deepEqual(state.recipe.nodes.find(node => node.id === alignment.id).directions,
    reversed.nodes.find(node => node.id === alignment.id).directions);
  for (const id of ['axis-cylinder', 'plane-cylinder']) {
    assert(norm(delta(first[id].origin, reordered[id].origin)) < 1e-6);
    assert(Math.abs(first[id].radius - reordered[id].radius) < 1e-6);
  }
  await roundTrip(alignment.id, 'four-directions');
  checkFour(await graph());

  report.phase = 'explicit-reference-and-targets';
  const roleRecipe = structuredClone(fixture.recipe), original = fixture.ids['axis-cylinder'],
    firstIds = original.filter((_, index) => index % 2 === 0), secondIds = original.filter((_, index) => index % 2),
    support = roleRecipe.nodes.find(node => node.id === 'axis-cylinder-selection'),
    fit = roleRecipe.nodes.find(node => node.id === 'axis-cylinder'), firstFit = roleRecipe.nodes.findIndex(node => node.operation === 'fit');
  support.ids = firstIds;
  roleRecipe.nodes.splice(firstFit, 0,
    {id: 'role-axis', label: 'Adjustable reference axis', operation: 'axis', initial_parameters: [8, -3, 0, 0]},
    {id: 'role-other-axis', label: 'Other reference axis', operation: 'axis', initial_parameters: [8, -3, 0, 0]},
    {...support, id: 'role-second-selection', label: 'Second coaxial observations', ids: secondIds});
  roleRecipe.nodes.push({...fit, id: 'role-second-fit', label: 'Second coaxial target', selections: ['role-second-selection']});
  await loadActions(roleRecipe, 'explicit-role-fixture.json');
  const axisField = page.locator('[data-relationship-role="axis"]'), targets = page.locator('[data-relationship-role="members"]'),
    axis = reference('role-axis'), otherAxis = reference('role-other-axis'), target = reference('axis-cylinder'), second = reference('role-second-fit'),
    choose = async (field, button, value) => {
      await page.getByRole('button', {name: button, exact: true}).click();
      await input(field, value).click(); await page.keyboard.press('Escape');
    };
  const roleLabel = 'Explicit axis and multiple targets', beforeRole = evaluations.length;
  await begin('coaxial', roleLabel);
  await expect(axisField).toBeVisible(); await expect(targets).toBeVisible();
  await expect(page.locator('#relationship-participant-field')).toBeHidden();
  await choose(targets, 'Show Target fits choices', target);
  await expect(page.locator('#add-relationship')).toBeDisabled();
  await expect(axisField.locator('input:checked')).toHaveCount(0);
  await choose(axisField, 'Show Reference axis choices', axis);
  await expect(page.locator('#add-relationship')).toBeEnabled();
  await choose(axisField, 'Show Reference axis choices', otherAxis);
  await expect(input(axisField, axis)).not.toBeChecked(); await expect(input(axisField, otherAxis)).toBeChecked();
  await expect(input(targets, target)).toBeChecked();
  await choose(axisField, 'Show Reference axis choices', axis);
  await choose(targets, 'Show Target fits choices', second);
  await expect(targets.locator('input:checked')).toHaveCount(2);
  await expect(input(axisField, axis)).toBeChecked();
  await expect(page.locator('#add-relationship')).toBeEnabled();
  await choose(targets, 'Show Target fits choices', second);
  const coaxial = await add(roleLabel);
  assert.equal(coaxial.axis, 'role-axis'); assert.deepEqual(coaxial.surfaces, ['axis-cylinder']);
  assert.equal((await graph()).results[coaxial.id], undefined);
  assert.equal(evaluations.length, beforeRole, 'Explicit role authoring must remain pending with Auto off');
  report.phase = 'one-target-free-reference-solve';
  state = await evaluateReady(coaxial.id);
  assert(norm(cross(state.results['role-axis'].axis_display, [0, 0, 1])) > .02, 'The reference role does not freeze a free axis');
  assert.deepEqual(state.recipe.nodes.find(node => node.id === 'role-axis').initial_parameters, [8, -3, 0, 0]);
  await edit(coaxial.id);
  await page.locator('#coaxial-group-members').selectOption([target, second]);
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === coaxial.id).surfaces).toEqual(['axis-cylinder', 'role-second-fit']);
  await page.locator('#close-edit').click();
  report.phase = 'two-target-solve-and-round-trips';
  state = await evaluateReady(coaxial.id);
  const direction = state.results['role-axis'].axis_display;
  for (const id of ['axis-cylinder', 'role-second-fit']) {
    assert(norm(cross(state.results[id].analytic.axis, direction)) < 1e-7);
    assert(Math.abs(state.results[id].analytic.radius - fixture.truth['axis-cylinder'].radius) < .01);
  }
  assert.deepEqual(state.recipe.nodes.find(node => node.id === 'axis-cylinder-selection').ids, firstIds);
  assert.deepEqual(state.recipe.nodes.find(node => node.id === 'role-second-selection').ids, secondIds);
  assert.equal(new Set([...firstIds, ...secondIds]).size, original.length);
  assert.deepEqual([...firstIds, ...secondIds].sort((a, b) => a - b), original);
  await roundTrip(coaxial.id, 'explicit-roles');
  assert.equal((await graph()).recipe.nodes.find(node => node.id === coaxial.id).axis, 'role-axis');
  return {mixed_four_participants: true, all_endpoints_constrained: true, three_member_edit: true,
    order_invariant: true, group_actions_and_project_reopened: true, explicit_reference_and_targets: true,
    reference_replacement_preserves_targets: true, one_and_two_targets: true, reference_remains_adjustable: true,
    role_actions_and_project_reopened: true, disjoint_observations_preserved: true};
}

export async function exerciseDirectionAlignmentUi({page, expect, fixture}) {
  const report = {complete: false, phase: 'initialization'};
  const read = () => page.evaluate(() => globalThis.__directionAlignmentUi.read());
  const graph = async () => (await read()).graph;
  const evaluate = page.getByRole('button', {name: 'Evaluate all', exact: true});
  const evaluateReady = async id => {
    await expect(evaluate).toBeEnabled(); await evaluate.click();
    await expect.poll(async () => {
      const state = await graph();
      if (Object.keys(state.errors).length) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }).toBe('ready');
    await expect(evaluate).toBeEnabled();
    await expect(page.locator('#status')).not.toHaveClass(/\berror\b/);
    return graph();
  };
  const edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  try {
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await expect(page.locator('#load')).toBeEnabled();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await expect.poll(async () => (await graph()).recipe.nodes.length).toBe(0);
    await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.raw_base64, 'base64')});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
    await page.getByRole('checkbox', {name:'Auto',exact:true}).uncheck();
    await page.locator('#file').setInputFiles({name: 'direction-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(fixture.recipe))});
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.id === 'joint-plane')).toBe(true);
    const baseline = await evaluateReady('joint-plane');
    const source = structuredClone(baseline.recipe.nodes.find(node => node.operation === 'source'));
    const assertIntent = async () => {
      const state = await graph();
      const acceptedSource = state.recipe.nodes.find(node => node.operation === 'source');
      assert.equal(acceptedSource.source_sha256, source.source_sha256);
      assert.equal(acceptedSource.reference_sha256, source.reference_sha256);
      for (const [name, ids] of Object.entries(fixture.ids)) {
        const selection = state.recipe.nodes.find(node => node.id === name+'-selection');
        assert.deepEqual(selection.ids, ids);
        const fit = state.recipe.nodes.find(node => node.id === name);
        assert.equal(fit.kind, name === 'joint-plane' ? 'plane' : 'cylinder');
        assert.equal(fit.axis, null); assert.equal(fit.reference_plane, null);
      }
      return state;
    };
    const createAlignment = async ({label, first, second, summary}) => {
      await expect(page.getByRole('button',{name:'Relationship…',exact:true})).toBeEnabled();
      await page.getByRole('button',{name:'Relationship…',exact:true}).click();
      await page.locator('#relationship-kind').selectOption('direction_alignment');
      if (await page.locator('#clear-relationship-participants').isEnabled()) await page.locator('#clear-relationship-participants').click();
      const options = page.locator('#relationship-dialog details');
      if (!await options.evaluate(node => node.open)) await options.locator('summary').click();
      await page.locator('#new-relationship-label').fill(label);
      await page.getByRole('button', {name: 'Show Apply to choices', exact: true}).click();
      for (const key of [first, second]) {
        const inputs = page.locator('[data-relationship-role="participants"] input');
        const index = await inputs.evaluateAll((elements, key) => elements.findIndex(element => element.dataset.participantId === key), key);
        assert(index >= 0, `Missing exact direction choice ${key}`);
        await inputs.nth(index).check();
      }
      await expect(page.locator('#relationship-summary')).toContainText(summary);
      await page.keyboard.press('Escape');
      await expect(page.locator('#add-relationship')).toBeEnabled(); await page.locator('#add-relationship').click();
      await expect(page.locator('#relationship-dialog')).toBeHidden();
      await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === label)).toBe(true);
      const relation = (await graph()).recipe.nodes.find(node => node.label === label);
      assert.equal(relation.operation, 'direction_alignment'); assert.equal(relation.sense, 'unsigned');
      assert.deepEqual(new Set(relation.directions.map(ref => JSON.stringify(ref))), new Set([first, second]));
      return relation;
    };
    report.phase = 'fixed-axis';
    const axisRelation = await createAlignment({label: 'Offset axis alignment', first: reference('axis-cylinder'), second: reference('fixed-axis'), summary: 'Parallel axis directions'});
    let state = await evaluateReady(axisRelation.id);
    const axisFit = state.results['axis-cylinder'].analytic;
    assert(norm(cross(axisFit.axis, [0,0,1])) < 1e-7);
    assert(norm(cross(delta(axisFit.origin, state.results['fixed-axis'].point_display), axisFit.axis)) > 7);
    assert(Math.abs(axisFit.radius-fixture.truth['axis-cylinder'].radius) < .03);
    assert.deepEqual(state.results['fixed-axis'].axis_display, baseline.results['fixed-axis'].axis_display);
    assert.deepEqual(state.results['fixed-axis'].point_display, baseline.results['fixed-axis'].point_display);
    await assertIntent();
    report.phase = 'fixed-plane';
    const planeRelation = await createAlignment({label: 'Offset normal alignment', first: reference('plane-cylinder'), second: reference('fixed-plane', 'plane'), summary: 'Axis perpendicular to plane'});
    state = await evaluateReady(planeRelation.id);
    const planeFit = state.results['plane-cylinder'].analytic;
    assert(norm(cross(planeFit.axis, state.results['fixed-plane'].plane_equation.slice(0,3))) < 1e-7);
    assert(norm(cross(planeFit.origin, planeFit.axis)) > 7);
    assert(Math.abs(planeFit.radius-fixture.truth['plane-cylinder'].radius) < .04);
    assert.deepEqual(state.results['fixed-plane'].plane_equation, baseline.results['fixed-plane'].plane_equation);
    await assertIntent();
    report.phase = 'selected-joint';
    const jointRelation = await createAlignment({label: 'Selected free directions', first: reference('joint-cylinder'), second: reference('joint-plane','plane'), summary: 'Axis perpendicular to plane'});
    await page.getByRole('button',{name:'Joint',exact:true}).click();
    await page.locator('#new-joint-mode').selectOption('directions');
    await expect(page.locator('#new-axis-solve-axis')).toBeDisabled();
    await page.locator('#new-axis-solve-label').fill('Independent position joint');
    await page.locator('#new-axis-solve-factors').selectOption(['joint-cylinder','joint-plane',jointRelation.id]);
    await page.locator('#add-axis-solve-form').getByRole('button', {name: 'Add joint', exact: true}).click();
    await expect(page.locator('#axis-solve-dialog')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.label === 'Independent position joint')).toBe(true);
    const joint = (await graph()).recipe.nodes.find(node => node.label === 'Independent position joint');
    assert.equal(joint.operation, 'joint_fit'); assert.equal(joint.axis, undefined);
    assert.deepEqual(new Set(joint.constraints), new Set(['joint-cylinder','joint-plane',jointRelation.id]));
    state = await evaluateReady(joint.id);
    const solved = state.results[joint.id];
    assert.equal(solved.initialization.algorithm, 'selected-direction-component-recovery-v1');
    const cylinderDirection = solved.surfaces['joint-cylinder'].analytic.axis;
    const planeNormal = solved.surfaces['joint-plane'].plane_equation.slice(0,3);
    assert(norm(cross(cylinderDirection, planeNormal)) < 1e-7);
    const cylinderMoved = norm(cross(cylinderDirection, baseline.results['joint-cylinder'].analytic.axis));
    const baselinePlane = baseline.results['joint-plane'].plane_equation || baseline.results['joint-plane'].parameters;
    assert.equal(baselinePlane.length, 4);
    const planeMoved = norm(cross(planeNormal, baselinePlane.slice(0,3)));
    assert(cylinderMoved > .002 && planeMoved > .002, JSON.stringify({cylinderMoved,planeMoved}));
    for (const name of ['joint-cylinder','joint-plane']) assert.deepEqual(solved.surfaces[name].ids, fixture.ids[name]);
    await assertIntent();
    report.phase = 'edit-and-invalidate';
    await edit(axisRelation.id);
    await page.locator('#direction-alignment-inputs').selectOption([reference('axis-cylinder'), reference('fixed-plane','plane')]);
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === axisRelation.id).directions.some(ref => ref.feature === 'fixed-plane')).toBe(true);
    state = await graph(); assert.notEqual(state.states[axisRelation.id], 'ready');
    await evaluateReady(axisRelation.id);
    report.phase = 'open-joint-draft-snapshot';
    await edit(joint.id);
    const jointInputs = page.locator('#joint-inputs');
    assert.deepEqual(new Set(await jointInputs.evaluate(control => [...control.selectedOptions].map(option => option.value))),
      new Set(joint.constraints));
    // Remove an explicit fit only in the form. The retained alignment still
    // identifies both observed members in the accepted Joint being evaluated.
    await jointInputs.selectOption(['joint-cylinder', jointRelation.id]);
    const draftFactors = await jointInputs.evaluate(control => [...control.selectedOptions].map(option => option.value));
    assert(draftFactors.includes(jointRelation.id)); assert(!draftFactors.includes('joint-plane'));
    const beforeRefresh = await read();
    await evaluateReady(joint.id);
    await expect.poll(async () => (await read()).publication).toBeGreaterThan(beforeRefresh.publication);
    await expect(jointInputs).toHaveValues(draftFactors);
    const afterRefresh = await read();
    assert.deepEqual(afterRefresh.graph.recipe, beforeRefresh.graph.recipe);
    assert.equal(afterRefresh.generation, beforeRefresh.generation);
    assert.equal(afterRefresh.vertices, fixture.vertices);
    assert.deepEqual(new Set(afterRefresh.graph.recipe.nodes.find(node => node.id === joint.id).constraints),
      new Set(joint.constraints));
    await page.locator('#close-edit').click();
    assert.deepEqual((await graph()).recipe, beforeRefresh.graph.recipe);
    await page.locator(`#action-list .action-select[data-action-id="${joint.id}"]`).click({button:'right'});
    await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Reviewed direction joint');
    await page.locator('#rename-feature-form').getByRole('button',{name:'Rename',exact:true}).click();
    await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === joint.id).label).toBe('Reviewed direction joint');
    const finalRecipe = structuredClone((await assertIntent()).recipe);
    report.phase = 'actions-reopen';
    let downloading = page.waitForEvent('download'); await page.getByRole('button',{name:'Save actions',exact:true}).click();
    const actions = await downloadBytes(await downloading); assert.deepEqual(JSON.parse(actions), finalRecipe);
    await page.locator('#file').setInputFiles({name:'reopened-directions.json',mimeType:'application/json',buffer:actions});
    await expect(page.locator('#source-progress')).toBeHidden(); await evaluateReady(joint.id);
    assert.deepEqual((await graph()).recipe, finalRecipe); await assertIntent();
    report.phase = 'project-reopen';
    downloading = page.waitForEvent('download'); await page.getByRole('button',{name:'Save project',exact:true}).click();
    const archive = await downloadBytes(await downloading), generation = (await read()).generation;
    await page.locator('#project-file').setInputFiles({name:'directions.scansor',mimeType:'application/zip',buffer:archive});
    await expect.poll(async () => (await read()).generation).toBeGreaterThan(generation);
    await expect(page.locator('#source-progress')).toBeHidden(); await evaluateReady(joint.id);
    assert.deepEqual((await graph()).recipe, finalRecipe); await assertIntent();
    await edit(axisRelation.id);
    assert.deepEqual(new Set(await page.locator('#direction-alignment-inputs').evaluate(control => [...control.selectedOptions].map(option => option.value))),
      new Set([reference('axis-cylinder'),reference('fixed-plane','plane')]));
    assert(Math.abs(dot(cylinderDirection, planeNormal)) > 1-1e-8);
    report.groups = await exerciseRelationshipGroupsUi({page, expect, fixture, read, graph, evaluateReady, edit, report});
    return {...report, complete:true, phase:'complete', vertices:fixture.vertices, triangles:fixture.triangles,
      fixed_axis:true, fixed_plane:true, independent_positions:true, selected_joint:true, both_members_influence:true,
      explicit_fit_factors_once:true, edited:true, actions_reopened:true, project_reopened:true,
      joint_draft_snapshot_preserved:true,
      source_sha256:source.source_sha256, families_retained:true,
      observations:Object.fromEntries(Object.entries(fixture.ids).map(([key,ids])=>[key,ids.length]))};
  } catch (error) {
    error.message += '\nDirection UI phase: '+report.phase+'\n'+JSON.stringify(await page.evaluate(() => ({
      status:document.getElementById('status')?.textContent,
      invalid:[...document.querySelectorAll('input:invalid,select:invalid,textarea:invalid')].filter(node=>node.getClientRects().length).map(node=>({id:node.id,value:node.value,message:node.validationMessage})),
      errors:globalThis.__directionAlignmentUi?.read().graph?.errors,
    })).catch(()=>({unavailable:true})));
    throw error;
  }
}
