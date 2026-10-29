import assert from 'node:assert/strict';

// Read-only state observation; every edit and evaluation uses product controls.
export const fitReferenceObserver = `\nlet fitReferenceObservedGraph, fitReferenceObservedPublication = 0;
Object.defineProperty(globalThis, '__fitReferenceUi', {value: {
  read: () => {
    if (graphState !== fitReferenceObservedGraph) {
      fitReferenceObservedGraph = graphState; fitReferenceObservedPublication++;
    }
    return structuredClone({graph: graphState, publication: fitReferenceObservedPublication,
      generation: workspaceGeneration, vertices: metadata?.vertices});
  }
}});\n`;

const reference = (feature, output) => JSON.stringify({feature, output, context: feature});
const downloadBytes = async download => {
  const parts = [];
  for await (const part of await download.createReadStream()) parts.push(part);
  return Buffer.concat(parts);
};

export async function exerciseFitReferenceUi({page, expect, fixture}) {
  const read = () => page.evaluate(() => globalThis.__fitReferenceUi.read());
  const graphWrites = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/graph') graphWrites.push(request.url());
  });
  const evaluate = page.getByRole('button', {name: 'Evaluate all', exact: true});
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#load')).toBeEnabled();
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  await expect.poll(async () => (await read()).graph.recipe.nodes.length).toBe(0);
  await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream',
    buffer: Buffer.from(fixture.raw_base64, 'base64')});
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
  await expect(evaluate).toBeEnabled();
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
  await page.locator('#file').setInputFiles({name: 'fit-reference-actions.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(fixture.recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'parallel-planes')).toBe(true);
  const evaluateReady = async id => {
    await expect(evaluate).toBeEnabled(); await evaluate.click();
    await expect.poll(async () => {
      const state = (await read()).graph;
      if (Object.values(state.states).some(value => value === 'failed')) throw Error(JSON.stringify(state.errors));
      return state.states[id];
    }).toBe('ready');
    await expect(evaluate).toBeEnabled();
  };
  await evaluateReady('parallel-planes');
  const refreshDraft = async () => {
    const before = await read();
    await expect(evaluate).toBeEnabled(); await evaluate.click();
    await expect.poll(async () => {
      const current = await read();
      return current.publication > before.publication && !current.graph.evaluation_running;
    }).toBe(true);
    await expect(evaluate).toBeEnabled();
    const after = await read();
    assert.deepEqual(after.graph.recipe, before.graph.recipe);
    assert.equal(after.generation, before.generation);
    assert.equal(after.vertices, fixture.vertices);
    return {before: before.publication, after: after.publication};
  };
  const initial = (await read()).graph.recipe;
  const cases = [
    {label: 'UI cylinder centerline', kind: 'cylinder', selection: 'cylinder-selection', mode: 'fit_axis_line', datum: 'axis', output: 'axis', field: 'axis'},
    {label: 'UI cone centerline', kind: 'cone', selection: 'cone-selection', mode: 'fit_axis_line', datum: 'axis', output: 'axis', field: 'axis'},
    {label: 'UI plane axis normal', kind: 'plane', selection: 'plane-selection', mode: 'fit_axis_direction', datum: 'axis', output: 'axis', field: 'axis'},
    {label: 'UI plane datum normal', kind: 'plane', selection: 'plane-selection', mode: 'fit_plane_orientation', datum: 'plane-datum', output: 'plane', field: 'reference_plane'},
    {label: 'UI sphere center', kind: 'sphere', selection: 'sphere-selection', mode: 'fit_point_center', datum: 'center', output: 'point', field: 'point'},
  ];
  const created = [], diagnosed = [];
  for (const item of cases) {
    await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
    await page.locator('#new-fit-label').fill(item.label);
    await page.locator('#new-fit-kind').selectOption(item.kind);
    await page.locator('#new-fit-inputs').selectOption(item.selection);
    await page.locator('#new-fit-reference-mode').selectOption(item.mode);
    await expect(page.locator('#new-fit-kind')).toHaveValue(item.kind);
    const options = await page.locator('#new-fit-reference option').evaluateAll(options => options.map(option =>
      ({value: option.value, text: option.textContent, disabled: option.disabled})));
    assert(options.some(option => option.value === reference(item.datum, item.output) && !option.disabled));
    diagnosed.push(...options.filter(option => option.disabled && /direct datum context|explicit datum|geometric property/i.test(option.text)));
    await page.locator('#new-fit-reference').selectOption(reference(item.datum, item.output));
    await expect(page.locator('#new-fit-kind')).toHaveValue(item.kind);
    await expect(page.locator('#new-fit-inputs')).toHaveValues([item.selection]);
    await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
    try { await expect(page.locator('#fit-dialog')).toBeHidden(); }
    catch (error) {
      error.message += '\nFit submit diagnostics: ' + JSON.stringify(await page.evaluate(() => ({
        invalid: [...document.querySelectorAll('#add-fit-form :invalid')].map(element => ({id: element.id, value: element.value, message: element.validationMessage})),
        statuses: [...document.querySelectorAll('footer [role="status"], #status')].map(element => element.textContent),
        fields: [...document.querySelectorAll('#add-fit-form input, #add-fit-form select')].map(element => ({id: element.id, value: element.value, disabled: element.disabled})),
        graph: (({token, states, errors}) => ({token, states, errors}))(globalThis.__fitReferenceUi.read().graph),
      })));
      throw error;
    }
    await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.label === item.label)).toBe(true);
    const node = (await read()).graph.recipe.nodes.find(node => node.label === item.label);
    assert.equal(node.kind, item.kind); assert.equal(node[item.field], item.datum);
    for (const field of ['axis', 'point', 'reference_plane'].filter(field => field !== item.field)) assert.equal(node[field], null);
    await evaluateReady(node.id);
    const result = (await read()).graph.results[node.id];
    assert.deepEqual(result.ids, fixture.ids[item.kind === 'plane' ? 'plane' : item.kind]);
    assert(result.weighted_rms < 1e-5, `${item.label}: ${JSON.stringify(result)}`);
    created.push({node, rms: result.weighted_rms, ids: result.ids});
  }
  assert(diagnosed.length > 0, 'Read-only/unsupported publications must remain visible with a diagnosis');
  assert(diagnosed.some(option => /direct datum context/.test(option.text)
    && JSON.parse(option.value).context === 'parallel-planes'), 'Solved-context choices must remain disabled and explain their direct-datum requirement');
  // A kind change cannot silently reinterpret a saved line relationship.
  await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
  await page.locator('#new-fit-label').fill('Incompatible creation draft');
  await page.locator('#new-fit-kind').selectOption('cylinder');
  await page.locator('#new-fit-inputs').selectOption('sphere-selection');
  await page.locator('#new-fit-reference-mode').selectOption('fit_axis_line');
  await page.locator('#new-fit-reference').selectOption(reference('axis', 'axis'));
  const beforeInvalid = (await read()).graph.recipe;
  await page.locator('#new-fit-kind').selectOption('sphere');
  await expect(page.locator('#new-fit-kind')).toHaveValue('sphere');
  await expect(page.locator('#new-fit-reference-mode')).toHaveValue('fit_axis_line');
  await expect(page.locator('#new-fit-reference-mode option:checked')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#new-fit-reference')).toHaveValue(reference('axis', 'axis'));
  await expect(page.locator('#new-fit-reference option:checked')).toHaveJSProperty('disabled', true);
  const createRefresh = await refreshDraft();
  await expect(page.locator('#new-fit-label')).toHaveValue('Incompatible creation draft');
  await expect(page.locator('#new-fit-kind')).toHaveValue('sphere');
  await expect(page.locator('#new-fit-inputs')).toHaveValues(['sphere-selection']);
  await expect(page.locator('#new-fit-reference-mode')).toHaveValue('fit_axis_line');
  await expect(page.locator('#new-fit-reference-mode option:checked')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#new-fit-reference')).toHaveValue(reference('axis', 'axis'));
  await expect(page.locator('#new-fit-reference option:checked')).toHaveJSProperty('disabled', true);
  const createWrites = graphWrites.length;
  await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
  await expect(page.locator('#fit-dialog')).toBeVisible();
  assert.deepEqual((await read()).graph.recipe, beforeInvalid);
  const createSubmitWrites = graphWrites.length - createWrites;
  assert.equal(createSubmitWrites, 0, 'An incompatible creation draft cannot POST authored graph changes');
  await page.locator('#new-fit-reference-mode').selectOption('');
  await expect(page.locator('#new-fit-reference')).toHaveValue('');
  await page.locator('#fit-dialog').getByRole('button', {name: 'Cancel', exact: true}).click();
  assert.deepEqual((await read()).graph.recipe, beforeInvalid);

  const edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  for (let index = 0; index < created.length; index++) {
    await edit(created[index].node.id);
    await expect(page.locator('#surface-kind')).toHaveValue(cases[index].kind);
    await expect(page.locator('#fit-reference-mode')).toHaveValue(cases[index].mode);
    await expect(page.locator('#fit-reference')).toHaveValue(reference(cases[index].datum, cases[index].output));
  }
  const editedId = created[0].node.id;
  await edit(editedId);
  await expect(page.locator('#fit-reference-mode')).toHaveValue('fit_axis_line');
  await expect(page.locator('#fit-reference')).toHaveValue(reference('axis', 'axis'));
  await page.locator('#surface-kind').selectOption('sphere');
  await expect(page.locator('#surface-kind')).toHaveValue('sphere');
  await expect(page.locator('#fit-reference-mode option:checked')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#fit-reference option:checked')).toHaveJSProperty('disabled', true);
  const editRefresh = await refreshDraft();
  await expect(page.locator('#surface-kind')).toHaveValue('sphere');
  await expect(page.locator('#fit-inputs')).toHaveValues(['cylinder-selection']);
  await expect(page.locator('#fit-reference-mode')).toHaveValue('fit_axis_line');
  await expect(page.locator('#fit-reference-mode option:checked')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#fit-reference')).toHaveValue(reference('axis', 'axis'));
  await expect(page.locator('#fit-reference option:checked')).toHaveJSProperty('disabled', true);
  const editWrites = graphWrites.length;
  await page.locator('#apply-properties').click();
  assert.deepEqual((await read()).graph.recipe, beforeInvalid);
  const editSubmitWrites = graphWrites.length - editWrites;
  assert.equal(editSubmitWrites, 0, 'An incompatible property draft cannot POST authored graph changes');
  await page.locator('#fit-inputs').selectOption('sphere-selection');
  await page.locator('#fit-reference-mode').selectOption('fit_point_center');
  await page.locator('#fit-reference').selectOption(reference('center', 'point'));
  // Merely repairing a draft still cannot publish any authored changes.
  assert.deepEqual((await read()).graph.recipe, beforeInvalid);
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).graph.recipe.nodes.find(node => node.id === editedId).kind).toBe('sphere');
  const repaired = (await read()).graph.recipe.nodes.find(node => node.id === editedId);
  assert.equal(repaired.point, 'center'); assert.equal(repaired.axis, null); assert.equal(repaired.reference_plane, null);
  await evaluateReady(editedId);
  assert((await read()).graph.results[editedId].weighted_rms < 1e-5);

  const beforeReopen = (await read()).graph.recipe;
  const actionsDownload = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Save actions', exact: true}).click();
  const actions = await downloadBytes(await actionsDownload);
  assert.deepEqual(JSON.parse(actions), beforeReopen);
  await page.locator('#file').setInputFiles({name: 'reopened-fit-actions.json', mimeType: 'application/json', buffer: actions});
  await expect(page.locator('#source-progress')).toBeHidden();
  await evaluateReady(editedId);
  assert.deepEqual((await read()).graph.recipe, beforeReopen);
  const projectDownload = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Save project', exact: true}).click();
  const archive = await downloadBytes(await projectDownload);
  const beforeArchiveGeneration = (await read()).generation;
  await page.locator('#project-file').setInputFiles({name: 'reopened-fit.scansor', mimeType: 'application/zip', buffer: archive});
  await expect.poll(async () => (await read()).generation).toBeGreaterThan(beforeArchiveGeneration);
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
  await evaluateReady(editedId);
  assert.deepEqual((await read()).graph.recipe, beforeReopen);
  await edit(editedId);
  await expect(page.locator('#surface-kind')).toHaveValue('sphere');
  await expect(page.locator('#fit-reference-mode')).toHaveValue('fit_point_center');
  await expect(page.locator('#fit-reference')).toHaveValue(reference('center', 'point'));
  return {complete: true, vertices: fixture.vertices, triangles: fixture.triangles,
    cases: created.map(({node, rms, ids}) => ({kind: node.kind, axis: node.axis, point: node.point, reference_plane: node.reference_plane, rms, ids})),
    initial_nodes: initial.nodes.length, diagnosed: diagnosed.map(option => option.text), repaired: {kind: repaired.kind,
      axis: repaired.axis, point: repaired.point, reference_plane: repaired.reference_plane},
    refreshed_incompatible_drafts: {creation: createRefresh.after > createRefresh.before, edit: editRefresh.after > editRefresh.before},
    incompatible_submit_graph_posts: {creation: createSubmitWrites, edit: editSubmitWrites},
    actions_reopened: true, project_reopened: true};
}
