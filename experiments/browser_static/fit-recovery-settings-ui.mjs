import assert from 'node:assert/strict';

// Observe accepted state; product controls own all settings and file changes.
export const fitRecoverySettingsObserver = `\nObject.defineProperty(globalThis, '__fitRecoverySettingsUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration})
}});\n`;

async function bytes(download) {
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  return Buffer.concat(chunks);
}

export async function exerciseFitRecoverySettingsUi({page, expect, fixture}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Settings proof requires a private preview');
  const read = () => page.evaluate(() => globalThis.__fitRecoverySettingsUi.read()),
    graph = async () => (await read()).graph,
    auto = page.getByRole('checkbox', {name: 'Auto', exact: true}),
    field = page.locator('#fit-recovery-ppm'), dialog = page.locator('#fit-recovery-dialog'),
    coupled = ['axis-cylinder', 'plane-cylinder', 'fit-recovery-coupled'], requests = [],
    frame = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))),
    save = async name => {const downloading = page.waitForEvent('download');
      await page.getByRole('button', {name, exact: true}).click(); return bytes(await downloading);},
    newProject = async () => {
      await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
      await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
      await expect.poll(async () => (await graph()).recipe.nodes.length).toBe(0);
    },
    open = async () => {await page.getByRole('button', {name: 'Fit settings…', exact: true}).click(); await expect(dialog).toBeVisible();},
    cancel = () => dialog.getByRole('button', {name: 'Cancel', exact: true}).click(),
    ready = async () => {
      await expect.poll(async () => {
        const state = await graph(); assert.deepEqual(state.errors, {});
        return coupled.every(id => state.states[id] === 'ready');
      }, {timeout: 60000}).toBe(true);
      await expect(page.getByRole('button', {name: 'Evaluate all', exact: true})).toBeEnabled();
    },
    evaluate = async () => {await page.getByRole('button', {name: 'Evaluate all', exact: true}).click(); await ready();},
    apply = async value => {
      await open(); await field.fill(String(value)); await page.locator('#apply-fit-recovery').click();
      await expect(dialog).toBeHidden();
      await expect.poll(async () => (await graph()).recipe.fit_recovery.geometric_equivalence_ppm).toBe(value);
    },
    loadActions = async payload => {
      await page.locator('#file').setInputFiles({name: 'fit-recovery-actions.json', mimeType: 'application/json', buffer: payload});
      await expect(page.locator('#source-progress')).toBeHidden();
    },
    loadProject = async archive => {
      const generation = (await read()).generation;
      await page.locator('#project-file').setInputFiles({name: 'fit-recovery.scansor', mimeType: 'application/zip', buffer: archive});
      await expect.poll(async () => (await read()).generation).toBeGreaterThan(generation);
      await expect(page.locator('#source-progress')).toBeHidden();
    },
    recordRequest = request => {
      if (request.method() === 'POST' && /\/api\/graph(?:\/|$)/.test(new URL(request.url()).pathname)) requests.push(request.url());
    };
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#load')).toBeEnabled();
  const original = structuredClone((await graph()).recipe), originalAuto = await auto.isChecked(),
    originalArchive = original.nodes.length ? await save('Save project') : null;
  const report = {complete: false, phase: 'fixture'};
  page.on('request', recordRequest);
  try {
    await auto.uncheck(); await newProject();
    await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream',
      buffer: Buffer.from(fixture.raw_base64, 'base64')});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.operation === 'source')).toBe(true);
    const recipe = structuredClone(fixture.recipe), source = recipe.nodes.find(node => node.operation === 'source'), oldSource = source.id;
    Object.assign(source, (await graph()).recipe.nodes.find(node => node.operation === 'source'));
    for (const node of recipe.nodes) if (node.source === oldSource) node.source = source.id;
    recipe.nodes.push({id: coupled[2], label: 'Recovery settings coupled cylinders', operation: 'direction_alignment',
      directions: coupled.slice(0, 2).map(feature => ({feature, output: 'axis', context: feature}))});
    recipe.output = coupled[2];
    await loadActions(Buffer.from(JSON.stringify(recipe)));
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.id === coupled[2])).toBe(true);
    await evaluate();
    const baseline = structuredClone((await graph()).recipe);
    report.phase = 'default-cancel-invalid';
    await open(); await expect(field).toHaveValue('1');
    await field.fill('9'); await cancel();
    assert.deepEqual((await graph()).recipe, baseline);
    await open(); const beforeInvalid = requests.length;
    for (const value of ['', '0', '-2', '1e309']) {
      await field.fill(value); await page.locator('#apply-fit-recovery').click(); await frame();
      await expect(dialog).toBeVisible();
      assert.equal(await field.evaluate(input => input.validity.valid), false);
      assert.deepEqual((await graph()).recipe, baseline);
      assert.equal(requests.length, beforeInvalid, 'Invalid input cannot replace or evaluate the graph');
    }
    await cancel();
    report.phase = 'auto-off-invalidation';
    const beforeApply = requests.length;
    await apply(2.5); await frame();
    const stale = await graph();
    assert.deepEqual(stale.recipe.nodes, baseline.nodes);
    for (const id of coupled) assert.equal(stale.states[id], 'stale', `${id} must be invalidated by the project policy`);
    assert.equal(requests.slice(beforeApply).filter(url => /\/(?:evaluate|ensure)(?:\?|$)/.test(url)).length, 0,
      'Auto-off settings must preserve deferred evaluation');
    await evaluate();
    report.phase = 'auto-on-reevaluation';
    await auto.check(); await ready();
    const beforeAutoRequests = requests.filter(url => /\/(?:evaluate|ensure)(?:\?|$)/.test(url)).length;
    await apply(7.5); await ready();
    if (requests.length) assert(requests.filter(url => /\/(?:evaluate|ensure)(?:\?|$)/.test(url)).length > beforeAutoRequests,
      'Native Auto-on application must start a real evaluation request');
    assert.deepEqual((await graph()).recipe.nodes, baseline.nodes);
    await auto.uncheck();
    report.phase = 'actions-project-persistence';
    const savedRecipe = structuredClone((await graph()).recipe), actions = await save('Save actions'), archive = await save('Save project');
    assert.deepEqual(JSON.parse(actions), savedRecipe);
    await apply(3.25);
    await loadActions(actions);
    await expect.poll(async () => (await graph()).recipe.fit_recovery.geometric_equivalence_ppm).toBe(7.5);
    await evaluate(); assert.deepEqual((await graph()).recipe, savedRecipe);
    await open(); await expect(field).toHaveValue('7.5'); await cancel();
    await apply(4.25);
    await loadProject(archive);
    await expect.poll(async () => (await graph()).recipe.fit_recovery.geometric_equivalence_ppm).toBe(7.5);
    await evaluate(); assert.deepEqual((await graph()).recipe, savedRecipe);
    await open(); await expect(field).toHaveValue('7.5'); await cancel();
    Object.assign(report, {complete: true, phase: 'complete', default_ppm: 1, invalid_rejected: true, cancelled_unchanged: true,
      coupled_policy_invalidation: true, auto_off_deferred: true, auto_on_ready: true, saved_ppm: 7.5,
      actions_reopened: true, project_reopened: true, authored_nodes_preserved: true});
  } catch (error) {error.message = `Fit recovery settings (${report.phase}): ${error.message}`; throw error;}
  finally {
    page.off('request', recordRequest);
    if (await dialog.isVisible()) await cancel();
    await auto.uncheck();
    if (originalArchive) await loadProject(originalArchive); else await newProject();
    assert.deepEqual((await graph()).recipe, original, 'The shared check restores the original project recipe');
    await auto.setChecked(originalAuto);
  }
  return report;
}
