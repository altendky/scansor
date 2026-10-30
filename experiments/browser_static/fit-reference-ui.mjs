import assert from 'node:assert/strict';

// Observe the same backend calls in native and worker profiles without adding
// awaits or changing the request. Every edit below uses product controls.
export const fitReferenceObserver = `
let fitReferenceObservedGraph, fitReferenceObservedPublication = 0;
const fitReferenceWrites = [], fitReferenceEvaluations = [];
for (const name of ['replaceRecipe', 'ensureGraph', 'evaluateGraph']) {
  const invoke = backend[name];
  backend[name] = (...args) => {
    if (name === 'replaceRecipe') fitReferenceWrites.push(structuredClone(args[0]));
    else fitReferenceEvaluations.push(name);
    return invoke(...args);
  };
}
Object.defineProperty(globalThis, '__fitReferenceUi', {value: {
  read: () => {
    if (graphState !== fitReferenceObservedGraph) {fitReferenceObservedGraph = graphState; fitReferenceObservedPublication++;}
    return structuredClone({graph: graphState, publication: fitReferenceObservedPublication,
      generation: workspaceGeneration, vertices: metadata?.vertices,
      writes: fitReferenceWrites, evaluations: fitReferenceEvaluations});
  }
}});
`;

const reference = (feature, output, context = feature) => JSON.stringify({feature, output, context});
const downloadBytes = async download => {
  const parts = []; for await (const part of await download.createReadStream()) parts.push(part); return Buffer.concat(parts);
};

export async function exerciseFitReferenceUi({page, expect, fixture}) {
  const read = () => page.evaluate(() => globalThis.__fitReferenceUi.read()),
    evaluate = page.getByRole('button', {name: 'Evaluate all', exact: true}),
    auto = page.getByRole('checkbox', {name: 'Auto', exact: true});
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#load')).toBeEnabled(); await auto.uncheck();
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  await expect.poll(async () => (await read()).graph.recipe.nodes.length).toBe(0);
  await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream',
    buffer: Buffer.from(fixture.raw_base64, 'base64')});
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
  await page.locator('#file').setInputFiles({name: 'fit-relationship-actions.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(fixture.recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'parallel-planes')).toBe(true);
  const initial = (await read()).graph.recipe, legacy = initial.nodes.filter(node => node.operation === 'fit' && node.id.startsWith('legacy-'));
  assert.equal(legacy.length, 5);
  const evaluateReady = async id => {
    await expect(evaluate).toBeEnabled(); await evaluate.click();
    await expect.poll(async () => {
      const state = (await read()).graph;
      if (Object.values(state.states).some(value => value === 'failed')) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }, {timeout: 60000}).toBe('ready');
    await expect(evaluate).toBeEnabled();
  }, selectOutput = async (row, kind, role, key) => {
    const signature = (await read()).graph.relationship_contracts[kind], peers = signature.selection_pattern === 'peers',
      label = peers ? 'Participants' : signature.role_labels?.[role] || role,
      dropdown = row.getByRole('button', {name: `Show ${label} choices`, exact: true});
    await dropdown.click();
    const list = row.locator(peers ? '[id$="-participants"]' : `[id$="-role-${kind}-${role}-choices"]`),
      inputs = list.locator('input'), keys = await inputs.evaluateAll(inputs => inputs.map(input => input.dataset.participantId)), index = keys.indexOf(key);
    assert(index >= 0, `${kind}/${role} must offer exact output ${key}; got ${JSON.stringify(keys)}`);
    await inputs.nth(index).check();
    const popup = page.locator('#' + await dropdown.getAttribute('aria-controls'));
    if (await popup.evaluate(element => element.matches(':popover-open'))) await dropdown.press('Escape');
    await expect(popup).toBeHidden();
  }, addRelation = async (root, kind, entries, label) => {
    await root.getByRole('button', {name: 'Add relationship', exact: true}).click();
    const row = root.locator('[data-fit-relationship]').last(), mode = row.locator('select[id$="-kind"]'),
      offered = await mode.locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean)),
      registry = (await read()).graph.relationship_contracts;
    assert(offered.every(value => registry[value]), 'Every offered relationship must come from the common registry');
    await mode.selectOption(kind);
    if (label) {
      await row.getByText('Options', {exact: true}).click();
      await row.locator('input[id$="-label"]').fill(label);
    }
    for (const [role, key] of entries) await selectOutput(row, kind, role, key);
    return row;
  }, edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  const cases = [
    {label: 'UI cylinder direction and radius', kind: 'cylinder', selection: 'cylinder-direction', relations: [
      ['direction_alignment', [['reference', reference('direction-axis', 'axis')]]],
      ['equal_radii', [['participants', reference('cylinder', 'radius')]]]]},
    {label: 'UI cylinder normal to plane', kind: 'cylinder', selection: 'cylinder-normal', relations: [
      ['direction_alignment', [['reference', reference('direction-plane', 'plane')]]]]},
    {label: 'UI cylinder coaxial', kind: 'cylinder', selection: 'cylinder-coaxial', relations: [
      ['coaxial', [['axis', reference('axis', 'axis')]]]]},
    {label: 'UI cone coaxial', kind: 'cone', selection: 'cone-ui', relations: [
      ['coaxial', [['axis', reference('axis', 'axis')]]]]},
    {label: 'UI plane normal follows axis', kind: 'plane', selection: 'plane-axis-ui', relations: [
      ['direction_alignment', [['reference', reference('direction-axis', 'axis')]]]]},
    {label: 'UI plane follows publication', kind: 'plane', selection: 'plane-publication-ui', relations: [
      ['parallel_planes', [['participants', reference('plane', 'plane', 'parallel-planes')]]]]},
    {label: 'UI sphere shared center', kind: 'sphere', selection: 'sphere-center-ui', relations: [
      ['shared_center', [['point', reference('center', 'point')]]]]},
    {label: 'UI sphere equal radius', kind: 'sphere', selection: 'sphere-radius-ui', relations: [
      ['equal_radii', [['participants', reference('sphere', 'radius')]]]]},
  ];
  const created = [];
  for (const item of cases) {
    await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
    await page.locator('#new-fit-label').fill(item.label); await page.locator('#new-fit-kind').selectOption(item.kind);
    await page.locator('#new-fit-inputs').selectOption(item.selection + '-selection');
    const before = await read(), root = page.locator('#new-fit-relationships');
    for (const [index, [kind, entries]] of item.relations.entries()) await addRelation(root, kind, entries, item.label + ' relation ' + index);
    await expect(page.locator('#new-fit-kind')).toHaveValue(item.kind);
    await expect(page.locator('#new-fit-inputs')).toHaveValues([item.selection + '-selection']);
    assert.deepEqual((await read()).graph.recipe, before.graph.recipe, 'Building relationship drafts cannot publish a fit');
    assert.equal((await read()).evaluations.length, before.evaluations.length, 'Drafting pending relationships cannot force evaluation');
    await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
    await expect(page.locator('#fit-dialog')).toBeHidden();
    const saved = await read(), node = saved.graph.recipe.nodes.find(node => node.label === item.label),
      relations = saved.graph.recipe.nodes.filter(node => node.label.startsWith(item.label + ' relation '));
    assert(node); assert.equal(node.kind, item.kind);
    for (const field of ['axis', 'point', 'reference_plane']) assert.equal(node[field], null, 'New relationships cannot write legacy fit fields');
    assert.equal(saved.writes.length, before.writes.length + 1, 'The fit and all relationships must use one atomic authoring call');
    assert.equal(saved.evaluations.length, before.evaluations.length, 'Auto off must leave the whole batch pending');
    assert.equal(relations.length, item.relations.length);
    const written = saved.writes.at(-1).recipe.nodes;
    assert(written.some(candidate => candidate.id === node.id));
    for (const relation of relations) assert(written.some(candidate => candidate.id === relation.id));
    assert.deepEqual(saved.graph.recipe.nodes.filter(node => node.operation === 'fit' && node.id.startsWith('legacy-')), legacy);
    await evaluateReady(node.id);
    for (const relation of relations) assert.equal((await read()).graph.states[relation.id], 'ready');
    const result = (await read()).graph.results[node.id];
    assert.deepEqual(result.ids, fixture.ids[item.selection]); assert(result.weighted_rms < 1e-5, `${item.label}: ${result.weighted_rms}`);
    created.push({node, relations, rms: result.weighted_rms, ids: result.ids});
  }
  const publication = created[5].relations[0].surfaces.find(value => value.feature === 'plane');
  assert.deepEqual(publication, {feature: 'plane', output: 'plane', context: 'parallel-planes'});
  // A family change retains its now-invalid relationship; it cannot commit an
  // unrelated fit or erase the reference to make submission appear valid.
  await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
  await page.locator('#new-fit-label').fill('Incompatible creation draft');
  await page.locator('#new-fit-kind').selectOption('cylinder'); await page.locator('#new-fit-inputs').selectOption('sphere-selection');
  const invalidRow = await addRelation(page.locator('#new-fit-relationships'), 'coaxial', [['axis', reference('axis', 'axis')]], 'Retained coaxial'),
    invalidBefore = await read();
  await page.locator('#new-fit-kind').selectOption('sphere');
  await expect(invalidRow.locator('select[id$="-kind"]')).toHaveValue('coaxial');
  await expect(invalidRow.locator('select[id$="-kind"] option:checked')).toHaveJSProperty('disabled', true);
  await evaluateReady('parallel-planes');
  await expect(page.locator('#new-fit-label')).toHaveValue('Incompatible creation draft');
  await expect(invalidRow.locator('select[id$="-kind"]')).toHaveValue('coaxial');
  await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
  await expect(page.locator('#fit-dialog')).toBeVisible();
  assert.deepEqual((await read()).graph.recipe, invalidBefore.graph.recipe); assert.equal((await read()).writes.length, invalidBefore.writes.length);
  await invalidRow.getByRole('button', {name: 'Remove this relationship', exact: true}).click();
  await page.locator('#fit-dialog').getByRole('button', {name: 'Cancel', exact: true}).click();

  const edited = created[0], beforeEdit = await read();
  await edit(edited.node.id); await expect(page.locator('#fit-legacy-binding')).toBeHidden();
  const propertyRows = page.locator('#fit-relationships [data-fit-relationship]'); await expect(propertyRows).toHaveCount(2);
  const directionRow = propertyRows.filter({has: page.locator('select[id$="-kind"] option:checked[value="direction_alignment"]')});
  await selectOutput(directionRow, 'direction_alignment', 'reference', reference('direction-plane', 'plane'));
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).writes.length).toBe(beforeEdit.writes.length + 1);
  const afterEdit = await read(), changed = afterEdit.graph.recipe.nodes.find(node => node.id === edited.relations[0].id);
  assert.deepEqual(changed.reference, {feature: 'direction-plane', output: 'plane', context: 'direction-plane'});
  assert.equal(afterEdit.evaluations.length, beforeEdit.evaluations.length);
  assert.equal(afterEdit.graph.recipe.nodes.length, beforeEdit.graph.recipe.nodes.length); await evaluateReady(edited.node.id);
  const centerRelation = created[6].relations[0];
  await edit(centerRelation.id);
  await expect(page.locator('#relationship-properties')).toBeVisible();
  await expect(page.locator('#relationship-properties-kind')).toHaveValue('shared_center');
  const beforeCenterEdit = await read();
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).writes.length).toBe(beforeCenterEdit.writes.length + 1);
  assert.deepEqual((await read()).graph.recipe.nodes.find(node => node.id === centerRelation.id), centerRelation);
  assert.equal((await read()).evaluations.length, beforeCenterEdit.evaluations.length);
  if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
  // Retargeting an existing group must exclude its superseded declaration from
  // compatibility checks, including while the replacement is only a draft.
  await edit(created[6].node.id);
  const centerRow = page.locator('#fit-relationships [data-fit-relationship]'); await expect(centerRow).toHaveCount(1);
  await selectOutput(centerRow, 'shared_center', 'point', reference('center-other', 'point'));
  const beforeRetarget = await read();
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).writes.length).toBe(beforeRetarget.writes.length + 1);
  const retargeted = (await read()).graph.recipe.nodes.find(node => node.id === centerRelation.id);
  assert.deepEqual(retargeted, {...centerRelation, point: 'center-other'});
  assert.equal((await read()).evaluations.length, beforeRetarget.evaluations.length);
  await edit(created[6].node.id);
  await centerRow.getByRole('button', {name: `Remove ${centerRelation.label}`, exact: true}).click();
  const replacementLabel = 'Replacement sphere center';
  await addRelation(page.locator('#fit-relationships'), 'shared_center', [['point', reference('center', 'point')]], replacementLabel);
  const beforeReplacement = await read();
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).writes.length).toBe(beforeReplacement.writes.length + 1);
  const afterReplacement = await read(), replacement = afterReplacement.graph.recipe.nodes.find(node => node.label === replacementLabel);
  assert(!afterReplacement.graph.recipe.nodes.some(node => node.id === centerRelation.id));
  assert.notEqual(replacement.id, centerRelation.id); assert.equal(replacement.operation, 'shared_center_group');
  assert.equal(replacement.point, 'center'); assert.deepEqual(replacement.surfaces, [created[6].node.id]);
  assert.equal(afterReplacement.graph.recipe.nodes.length, beforeReplacement.graph.recipe.nodes.length);
  assert.equal(afterReplacement.evaluations.length, beforeReplacement.evaluations.length);
  await evaluateReady(created[6].node.id);
  for (const node of legacy) {
    await edit(node.id); await expect(page.locator('#fit-legacy-binding')).toBeVisible();
    await expect(page.locator('#surface-kind')).toHaveValue(node.kind);
    assert.deepEqual((await read()).graph.recipe.nodes.find(candidate => candidate.id === node.id), node);
  }
  const beforeReopen = (await read()).graph.recipe, actionsDownload = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Save actions', exact: true}).click();
  const actions = await downloadBytes(await actionsDownload); assert.deepEqual(JSON.parse(actions), beforeReopen);
  await page.locator('#file').setInputFiles({name: 'reopened-fit-actions.json', mimeType: 'application/json', buffer: actions});
  await expect(page.locator('#source-progress')).toBeHidden(); await evaluateReady(edited.node.id);
  assert.deepEqual((await read()).graph.recipe, beforeReopen);
  const projectDownload = page.waitForEvent('download'); await page.getByRole('button', {name: 'Save project', exact: true}).click();
  const archive = await downloadBytes(await projectDownload), beforeArchive = await read();
  const openProject = page.locator('#open-project'); await expect(openProject).toBeEnabled();
  const chooseProject = page.waitForEvent('filechooser'); await openProject.click();
  await (await chooseProject).setFiles({name: 'reopened-fit.scansor', mimeType: 'application/zip', buffer: archive});
  await expect.poll(async () => (await read()).publication, {timeout: 60000}).toBeGreaterThan(beforeArchive.publication);
  await expect(page.locator('#status')).toHaveText('Project opened. Evaluate to refresh dependent results.');
  assert.equal((await read()).vertices, fixture.vertices);
  await expect(page.locator('#source-progress')).toBeHidden(); await evaluateReady(edited.node.id);
  assert.deepEqual((await read()).graph.recipe, beforeReopen);
  await edit(edited.node.id); await expect(page.locator('#fit-relationships [data-fit-relationship]')).toHaveCount(2);
  return {complete: true, vertices: fixture.vertices, triangles: fixture.triangles,
    cases: created.map(({node, relations, rms, ids}) => ({kind: node.kind, axis: node.axis, point: node.point,
      reference_plane: node.reference_plane, relationships: relations.map(relation => relation.operation), rms, ids})),
    initial_nodes: initial.nodes.length, shared_registry: true, pending_creation: true, atomic_batches: true,
    no_forced_evaluation: true, multiple_relationships: true, exact_publication_preserved: true,
    incompatible_draft_retained: true, incompatible_submit_graph_posts: 0,
    edited_same_relationship_ids: true, shared_center_inspector: true, retargeted_and_replaced_center: true,
    legacy_bindings_preserved: legacy.length, actions_reopened: true, project_reopened: true};
}
