import assert from 'node:assert/strict';

// Observation only: all graph writes and evaluation use product controls.
export const staleRelationshipObserver = `\nObject.defineProperty(globalThis, '__staleRelationshipUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration, vertices: metadata?.vertices,
    relationshipGuides: relationshipOverlays.children.length,
    selection: [...selectedFeatureIds], participants: [...relationshipParticipantIds], pickMode: modelPickMode,
    pickGuides: modelPickGuides.children.length,
    modelChoices: modelPickMode === 'participant' ? modelPickChoices().map(({key, reference}) => ({key, reference})) : [],
    treeChoices: modelPickMode === 'participant' ? modelPickChoices({tree: true}).map(({key, reference}) => ({key, reference})) : [],
    pendingGeometry: requirementOutputs(graphState, 'relationship_direction')
      .filter(output => output.reference.feature === 'axis-plane' && output.availability !== 'ready')
      .map(output => ({reference: output.reference, value: relationshipOutputValue(graphState, output)}))
  })
}});\n`;

const reference = (feature, output, context = feature) => ({feature, output, context});
const key = value => JSON.stringify(value);
const sameReferences = values => values.map(key).sort();
const longMemberLabel = 'bolt hole surface at Occurrence 123456789 — independently fitted patterned hole';

async function exerciseParticipantScrolling({page, expect, read, openBuilder, participantRow}) {
  const originalViewport = page.viewportSize(), before = await read(), profiles = [],
    seeds = Array.from({length: 4}, (_, index) => reference(`scroll-plane-${index + 1}`, 'plane')),
    popup = page.locator('.selection-field-options').filter({has: page.locator('#relationship-filter')}),
    list = page.locator('#relationship-participants'), search = page.locator('#relationship-filter'),
    dropdown = page.getByRole('button', {name: 'Show Participants choices', exact: true});
  const geometry = () => popup.evaluate(element => {
    const box = element.getBoundingClientRect(), search = element.querySelector('#relationship-filter').getBoundingClientRect(),
      list = element.querySelector('[data-picker-scroll]'), field = element.closest('.selection-field').getBoundingClientRect(),
      scrollOwners = [element, ...element.querySelectorAll('*')].filter(child =>
        ['auto', 'scroll'].includes(getComputedStyle(child).overflowY) && child.scrollHeight > child.clientHeight + 1);
    return {bounds: {left: box.left, top: box.top, right: box.right, bottom: box.bottom},
      search: {top: search.top, bottom: search.bottom}, viewport: {width: innerWidth, height: innerHeight},
      placement: box.top >= field.bottom - 1 ? 'below' : box.bottom <= field.top + 1 ? 'above' : 'overlap',
      scrollOwners: scrollOwners.map(owner => owner.id || owner.className), outerScroll: element.scrollTop,
      list: {top: list.scrollTop, visible: list.clientHeight, content: list.scrollHeight}};
  });
  const expectBounds = async () => {
    await expect.poll(async () => {
      const {bounds, viewport, search} = await geometry();
      return bounds.left >= 6 && bounds.top >= 6 && bounds.right <= viewport.width - 6 && bounds.bottom <= viewport.height - 6
        && search.top >= bounds.top && search.bottom <= bounds.bottom;
    }).toBe(true);
  };
  const exercise = async name => {
    await search.fill('');
    await expectBounds();
    assert(await list.locator('.relationship-participant:not([hidden])').count() >= 35, 'Exercise a full long participant list');
    await expect(list).toHaveAttribute('tabindex', '0');
    await list.focus(); await page.keyboard.press('Home');
    await expect.poll(() => list.evaluate(element => element.scrollTop)).toBe(0);
    const initial = await geometry();
    assert.deepEqual(initial.scrollOwners, ['relationship-participants'], 'Only the choices list should own vertical scrolling');
    await list.hover(); await page.mouse.wheel(0, 100000);
    await expect.poll(() => list.evaluate(element => element.scrollTop + element.clientHeight >= element.scrollHeight - 2)).toBe(true);
    const scrolled = await geometry();
    assert(scrolled.list.top > 0);
    assert.equal(scrolled.outerScroll, 0, 'The popover must not develop a second scroll position');
    assert(Math.abs(scrolled.search.top - initial.search.top) <= 1, 'Search must stay still while browsing choices');
    const last = list.locator('.relationship-participant:not([hidden])').last(), checkbox = last.locator('input'), focus = last.locator('button'),
      finalKey = await checkbox.getAttribute('data-participant-id'), selected = (await read()).participants.slice().sort();
    assert(await checkbox.evaluate(element => {
      const box = element.getBoundingClientRect(), owner = element.closest('[data-picker-scroll]').getBoundingClientRect();
      return box.top >= owner.top && box.bottom <= owner.bottom &&
        element.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2));
    }), 'Wheel scrolling must make the final checkbox reachable');
    await checkbox.click(); await checkbox.click();
    await focus.click();
    assert.deepEqual((await read()).participants.slice().sort(), selected);
    await search.focus(); await page.keyboard.press('Tab');
    await expect(list).toBeFocused();
    await page.keyboard.press('End');
    await expect.poll(() => list.evaluate(element => element.scrollTop + element.clientHeight >= element.scrollHeight - 2)).toBe(true);
    const controls = await list.locator('input:not(:disabled), button:not(:disabled)').count();
    let reached = false;
    for (let index = 0; index <= controls; index++) {
      await page.keyboard.press('Tab');
      if (await checkbox.evaluate(element => element === document.activeElement)) {reached = true; break;}
    }
    assert(reached, `Keyboard traversal must reach the final checkbox ${finalKey}`);
    await page.keyboard.press('Space'); await page.keyboard.press('Space');
    await page.keyboard.press('Tab'); await expect(focus).toBeFocused(); await page.keyboard.press('Enter');
    assert.deepEqual((await read()).participants.slice().sort(), selected);
    const afterKeyboard = await geometry();
    assert.equal(afterKeyboard.outerScroll, 0);
    assert(Math.abs(afterKeyboard.search.top - initial.search.top) <= 1);
    await search.fill('Scroll test reference plane 30');
    await expect(list.locator('.relationship-participant:not([hidden])')).toHaveCount(5);
    for (const value of seeds) await expect(participantRow(value).locator('input')).toBeChecked();
    assert.deepEqual((await read()).participants.slice().sort(), selected);
    await expectBounds();
    profiles.push({name, ...initial, wheelFinalCheckbox: true, keyboardFinalCheckboxAndFocus: true, searchAndSelectionRetained: true});
  };
  await page.setViewportSize({width: 1440, height: 1000});
  await openBuilder();
  for (const value of seeds) await participantRow(value).locator('input').check();
  await expect(page.locator('#relationship-count')).toHaveText('4 selected');
  await exercise('popup-normal');
  for (const [name, viewport] of [['popup-short', {width: 800, height: 560}], ['popup-reduced-css-space', {width: 640, height: 480}]]) {
    await page.setViewportSize(viewport);
    await expect(search).toHaveValue('Scroll test reference plane 30');
    assert.deepEqual((await read()).participants.slice().sort(), sameReferences(seeds));
    await expectBounds();
    await exercise(name);
  }
  await page.keyboard.press('Escape');
  await page.setViewportSize({width: 1440, height: 1000});
  await page.locator('#dock-edit').click();
  await expect(page.locator('#edit-popup')).toBeHidden();
  await expect(page.getByRole('tab', {name: 'Edit', exact: true})).toBeVisible();
  await dropdown.click();
  await exercise('docked-normal');
  await page.setViewportSize({width: 800, height: 560});
  await expect(search).toHaveValue('Scroll test reference plane 30');
  await expectBounds();
  await exercise('docked-short');
  assert(profiles.some(profile => profile.placement === 'above'), 'Exercise popup placement above the field');
  assert(profiles.some(profile => profile.placement === 'below'), 'Exercise popup placement below the field');
  await page.keyboard.press('Escape');
  await page.locator('#relationship-dialog').getByRole('button', {name: 'Cancel', exact: true}).click();
  await expect(page.locator('#relationship-dialog')).toBeHidden();
  const edit = page.getByRole('tab', {name: 'Edit', exact: true});
  await edit.getByTitle('Close', {exact: true}).click();
  await page.setViewportSize(originalViewport);
  const after = await read();
  assert.deepEqual(after.graph.recipe, before.graph.recipe);
  assert.deepEqual(after.graph.states, before.graph.states);
  assert.deepEqual(after.selection, before.selection);
  return profiles;
}

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
  const openBuilder = async ({list = true} = {}) => {
    await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
    await page.locator('#relationship-kind').selectOption('direction_alignment');
    if (await page.locator('#clear-relationship-participants').isEnabled())
      await page.locator('#clear-relationship-participants').click();
    if (list) await page.getByRole('button', {name: 'Show Participants choices', exact: true}).click();
  };
  const treeParticipant = async value => {
    const before = await read(), choices = before.treeChoices.filter(choice => choice.reference.feature === value.feature),
      index = choices.findIndex(choice => choice.key === key(value));
    assert(index >= 0, `Missing exact tree participant ${key(value)}`);
    await page.locator(`#action-list .action-select[data-action-id="${value.feature}"]`).click();
    const chooser = page.locator('#model-pick-choices');
    if (choices.length > 1) {
      await expect(chooser).toBeVisible();
      await expect(chooser.locator('button')).toHaveCount(choices.length);
      assert.deepEqual((await read()).participants, before.participants, 'Ambiguous tree clicks must not guess an output/context');
      await chooser.locator('button').nth(index).click();
    }
    await expect.poll(async () => (await read()).participants.includes(key(value)))
      .toBe(!before.participants.includes(key(value)));
    await expect(chooser).toBeHidden();
    assert.deepEqual((await read()).selection, before.selection, 'Participant picking must preserve general feature selection');
    return choices.length;
  };
  const participantRow = value => page.locator('#relationship-participants .relationship-participant')
    .filter({has: page.locator(`input[data-participant-id=${JSON.stringify(key(value))}]`)});
  const selectedParticipant = value => page.locator(`#relationship-participant-field .selection-field-row[data-selection-key=${JSON.stringify(key(value))}]`);
  const assertReadable = async (locator, text) => {
    await expect(locator).toBeVisible();
    await expect(locator).toContainText(text);
    const layout = await locator.evaluate((element, text) => {
      const style = getComputedStyle(element), bounds = element.getBoundingClientRect(), walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node, target;
      while ((node = walker.nextNode())) if (node.textContent.includes(text)) {target = node; break;}
      if (!target) return {found: false};
      const range = document.createRange(), start = target.textContent.indexOf(text);
      range.setStart(target, start); range.setEnd(target, start + text.length);
      return {found: true, overflow: style.textOverflow, width: element.clientWidth, scrollWidth: element.scrollWidth,
        height: element.clientHeight, scrollHeight: element.scrollHeight,
        within: [...range.getClientRects()].every(rect => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1
          && rect.top >= bounds.top - 1 && rect.bottom <= bounds.bottom + 1)};
    }, text);
    assert(layout.found && layout.within, `The occurrence identifier must remain visible within its name: ${JSON.stringify(layout)}`);
    assert(layout.scrollWidth <= layout.width + 1 && layout.scrollHeight <= layout.height + 1,
      `The participant name must wrap without clipped overflow: ${JSON.stringify(layout)}`);
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
        construction: 'perpendicular_to_axis', initial_angle_degrees: null, offset: -12},
      ...Array.from({length: 30}, (_, index) => ({id: `scroll-plane-${index + 1}`,
        label: `Scroll test reference plane ${String(index + 1).padStart(2, '0')}`, operation: 'reference_plane',
        construction: 'independent', initial_normal: [0, 0, 1], initial_angle_degrees: null, offset: index})));
    recipe.nodes.find(node => node.id === 'axis-cylinder').axis = 'auto-axis';
    recipe.nodes.find(node => node.id === 'plane-cylinder').label = longMemberLabel;
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
    report.phase = 'participant-role-presentation';
    const oldViewport = page.viewportSize();
    await page.setViewportSize({width: 800, height: 720});
    await openBuilder();
    const rawAxis = reference('auto-axis', 'axis'), publishedAxisRef = reference('auto-axis', 'axis', '@axis/auto-axis'),
      publishedAlias = reference('axis-cylinder', 'axis', '@axis/auto-axis'),
      groups = page.locator('#relationship-participants .relationship-participant-group'),
      dropdown = page.getByRole('button', {name: 'Show Participants choices', exact: true});
    await expect(groups.filter({hasText: 'Features to constrain'})).toBeVisible();
    await expect(groups.filter({hasText: 'Results to reference'})).toBeVisible();
    await expect(participantRow(rawAxis)).toHaveAttribute('data-participant-group', 'constrain');
    for (const value of [publishedAxisRef, publishedAlias, plane]) {
      await expect(participantRow(value)).toHaveAttribute('data-participant-group', 'reference');
      await expect(participantRow(value)).toContainText('Shared-axis solve · Observed shared axis');
      await expect(participantRow(value)).toContainText('Needs evaluation');
    }
    await expect(participantRow(rawAxis).locator('.face-candidate-name')).toHaveText('Observed shared axis — axis');
    await expect(participantRow(publishedAxisRef).locator('.face-candidate-name')).toHaveText('Observed shared axis — axis');
    await page.locator('#relationship-filter').fill('Shared-axis solve');
    await expect(participantRow(publishedAxisRef)).toBeVisible();
    await expect(participantRow(publishedAlias)).toBeVisible();
    await expect(participantRow(rawAxis)).toBeHidden();
    await expect(groups.filter({hasText: 'Features to constrain'})).toBeHidden();
    await page.locator('#relationship-filter').fill('123456789');
    await expect(participantRow(publishedAxisRef)).toBeHidden();
    await expect(participantRow(member)).toHaveAttribute('data-participant-group', 'constrain');
    await assertReadable(participantRow(member).locator('.face-candidate-name'), '123456789');
    await participantRow(member).locator('input').check();
    await page.keyboard.press('Escape');
    await assertReadable(selectedParticipant(member).locator('.selection-field-title'), '123456789');
    await expect(selectedParticipant(member).locator('.selection-field-detail')).toContainText('Adjustable property');
    await page.locator('#clear-relationship-participants').click();
    await dropdown.click();
    await page.locator('#relationship-filter').fill('');
    await participantRow(rawAxis).locator('input').check();
    await participantRow(publishedAxisRef).locator('input').check();
    await page.keyboard.press('Escape');
    await expect(selectedParticipant(rawAxis).locator('.selection-field-detail')).toContainText('Adjustable property');
    await expect(selectedParticipant(publishedAxisRef).locator('.selection-field-detail')).toContainText('Fixed for this relationship');
    await expect(selectedParticipant(publishedAxisRef).locator('.selection-field-detail')).toContainText('Shared-axis solve · Observed shared axis');
    assert.deepEqual((await read()).participants.sort(), [key(rawAxis), key(publishedAxisRef)].sort());
    const beforeInvalidRemoval = evaluations.length;
    await page.locator('#relationship-kind').selectOption('equal_radii');
    await expect(selectedParticipant(publishedAxisRef)).toBeVisible();
    await expect(page.locator('#relationship-summary')).toContainText('Remove or replace');
    await selectedParticipant(publishedAxisRef).locator('.selection-field-remove').click();
    await expect(selectedParticipant(publishedAxisRef)).toHaveCount(0);
    assert(!(await read()).participants.includes(key(publishedAxisRef)), 'Incompatible retained peer outputs remain removable after changing relationship type');
    assert.equal(evaluations.length, beforeInvalidRemoval);
    await page.locator('#close-edit').click();
    await page.setViewportSize(oldViewport);
    report.phase = 'create-before-evaluation';
    const beforeCreate = evaluations.length;
    await openBuilder({list: false});
    const beforeTree = await read();
    await expect(page.locator('#pick-relationship-participant')).toBeEnabled();
    await page.locator('#pick-relationship-participant').click();
    await expect(page.locator('#viewport canvas')).toHaveAttribute('data-model-pick', 'participant');
    assert.equal((await read()).modelChoices.length, 0, 'All pending participants have no pickable 3D output');
    assert.equal((await read()).pickGuides, 0);
    const axisChoices = (await read()).treeChoices.filter(choice => choice.reference.feature === 'auto-axis');
    assert(axisChoices.length > 1, 'The shared axis exposes distinct feature and published contexts');
    const publishedAxis = axisChoices.find(choice => choice.reference.context !== choice.reference.feature);
    assert(publishedAxis, 'An exact published axis context is available before evaluation');
    assert(await treeParticipant(publishedAxis.reference) > 1);
    assert.deepEqual((await read()).participants, [publishedAxis.key]);
    await treeParticipant(publishedAxis.reference);
    assert.deepEqual((await read()).participants, []);
    await treeParticipant(plane);
    await treeParticipant(plane);
    await treeParticipant(plane);
    assert.equal((await read()).relationshipGuides, 0);
    await treeParticipant(member);
    const afterTree = await read();
    assert.deepEqual(afterTree.selection, beforeTree.selection);
    assert.deepEqual(afterTree.graph.recipe, beforeTree.graph.recipe);
    assert.deepEqual(afterTree.graph.states, beforeTree.graph.states);
    assert.deepEqual(afterTree.graph.results, beforeTree.graph.results);
    assert.equal(evaluations.length, beforeCreate);
    assert.equal(afterTree.pickGuides, 0);
    await page.locator('#stop-model-picking').click();
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
    await openBuilder({list: false});
    const beforeMixed = await read(), beforeMixedEvaluation = evaluations.length;
    await page.locator('#pick-relationship-participant').click();
    const mixed = await read();
    assert(mixed.modelChoices.length > 0, 'Mixed state retains ready 3D choices');
    assert(!mixed.modelChoices.some(choice => choice.key === key(plane)), 'Stale output cannot enter 3D picking');
    await treeParticipant(plane);
    const readyMember = mixed.modelChoices.find(choice => choice.reference.feature === 'joint-cylinder');
    assert(readyMember, 'The independent ready cylinder remains selectable alongside the stale plane');
    await treeParticipant(readyMember.reference);
    await treeParticipant(readyMember.reference);
    const afterMixed = await read();
    assert.deepEqual(afterMixed.selection, beforeMixed.selection);
    assert.deepEqual(afterMixed.graph.recipe, beforeMixed.graph.recipe);
    assert.deepEqual(afterMixed.graph.states, beforeMixed.graph.states);
    assert.equal(evaluations.length, beforeMixedEvaluation);
    await page.locator('#stop-model-picking').click();
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
    report.phase = 'participant-scrolling';
    const beforeScrollingEvaluations = evaluations.length;
    report.scrolling = await exerciseParticipantScrolling({page, expect, read, openBuilder, participantRow});
    assert.equal(evaluations.length, beforeScrollingEvaluations);
    report.complete = true;
    report.phase = 'complete';
    report.reference = plane;
    report.auto_off_creation_without_evaluation = true;
    report.auto_on_evaluates_prerequisites = true;
    report.stale_inputs_visible_enabled = true;
    report.stale_geometry_not_previewed = true;
    report.warm_to_stale_preview_cleared = true;
    report.property_context_preserved = true;
    report.pending_tree_picking = true;
    report.mixed_ready_pending_tree_picking = true;
    report.exact_context_tree_chooser = true;
    report.tree_toggles_preserve_general_selection = true;
    report.participant_role_groups = true;
    report.published_context_aliases_retained = true;
    report.selected_participant_roles_and_contexts = true;
    report.narrow_names_readable = true;
    report.incompatible_peer_publication_removable = true;
    report.source_and_observations_preserved = true;
    return report;
  } catch (error) {
    error.message = `${report.phase}: ${error.message}`;
    throw error;
  }
}
