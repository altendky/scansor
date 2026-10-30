import assert from 'node:assert/strict';

// Observation only: all authoring, selection and persistence use product controls.
export const patternRelationshipObserver = `\nObject.defineProperty(globalThis, '__patternRelationshipUi', {value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration,
    vertices: metadata?.vertices, guides: relationshipOverlays?.children.length || 0,
    selected: [...relationshipParticipantIds]})
}});\n`;

async function downloadBytes(download) {
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  return Buffer.concat(chunks);
}

export async function exercisePatternRelationshipUi({page, expect, fixture}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port), 'Pattern UI checks require a private preview');
  const report = {complete: false, phase: 'initialize', cases: []},
    read = () => page.evaluate(() => globalThis.__patternRelationshipUi.read()),
    graph = async () => (await read()).graph,
    node = async id => (await graph()).recipe.nodes.find(candidate => candidate.id === id),
    edit = async id => {
      await page.locator(`#action-list :is(.action-select, .managed-owner-summary)[data-action-id="${id}"]`).click({button: 'right'});
      await page.locator('#feature-context-edit').click();
    },
    closeEdit = async () => {
      if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
    },
    evaluate = async id => {
      await expect(page.getByRole('button', {name: 'Evaluate all', exact: true})).toBeEnabled();
      await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
      await expect.poll(async () => {
        const state = await graph();
        if (Object.keys(state.errors || {}).length) throw Error(JSON.stringify(state.errors));
        return state.states[id];
      }).toBe('ready');
    },
    load = async (recipe, filename) => {
      await page.locator('#file').setInputFiles({name: filename, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
      await expect(page.locator('#source-progress')).toBeHidden();
      await expect.poll(async () => (await graph()).recipe.nodes.map(candidate => candidate.id)).toEqual(recipe.nodes.map(candidate => candidate.id));
    },
    collectionOf = async owner => (await graph()).input_catalogue.find(output => output.reference.feature === owner &&
      output.collection?.template === 'bore' && output.collection.property === 'direction');
  try {
    await expect(page.locator('#viewport canvas')).toBeVisible();
    await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
    await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
    await expect.poll(async () => (await graph()).recipe.nodes.length).toBe(0);
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream',
      buffer: Buffer.from(fixture.raw_base64, 'base64')});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await read()).vertices).toBe(fixture.vertices);
    await load(fixture.recipe, 'pattern-source-actions.json');
    report.phase = 'create-pattern';
    await page.getByRole('button', {name: 'Feature reuse', exact: true}).click();
    await page.locator('#new-feature-reuse-label').fill('Six bolt holes');
    await page.locator('#new-feature-reuse-mode').selectOption('circular');
    await page.locator('#new-feature-reuse-fits').selectOption('bore');
    await page.locator('#new-feature-reuse-axis').selectOption('pattern_axis');
    await page.locator('#new-feature-reuse-count').fill('6');
    const mapping = fixture.circular.volumes.find(value => value.fit === 'bore');
    await page.locator('#new-feature-reuse-circular select[data-fit="bore"][data-source-selection="bore_input"]')
      .selectOption(JSON.stringify(mapping.volume, Object.keys(mapping.volume).sort()));
    await page.locator('#new-feature-reuse-preview').click();
    await expect(page.locator('#new-feature-reuse-preview-status')).toContainText('6 occurrences reviewed');
    await page.locator('#add-feature-reuse-form').getByRole('button', {name: 'Reuse features', exact: true}).click();
    await expect.poll(async () => (await graph()).recipe.nodes.some(candidate => candidate.operation === 'feature_reuse')).toBe(true);
    const owner = (await graph()).recipe.nodes.find(candidate => candidate.operation === 'feature_reuse').id;
    await evaluate(owner);
    const initialCollection = await collectionOf(owner);
    assert.equal(initialCollection.collection.count, 6);
    const ownerReference = initialCollection.reference, ownerKey = JSON.stringify(ownerReference),
      upstream = initialCollection.collection.members.find(reference => reference.feature === 'bore');
    assert(upstream && upstream.context !== upstream.feature);
    report.phase = 'tree-collection-pick';
    if (await page.locator('#clear-feature-selection').isEnabled()) await page.locator('#clear-feature-selection').click();
    await page.getByRole('button', {name: 'Relationship…', exact: true}).click();
    await page.locator('#relationship-kind').selectOption('direction_alignment');
    if (await page.locator('#clear-relationship-participants').isEnabled()) await page.locator('#clear-relationship-participants').click();
    const targets = page.locator('#relationship-dialog [data-relationship-role="participants"]');
    await targets.getByRole('button', {name: /^Pick Apply to in (?:the tree or )?model$/}).click();
    await page.locator(`.managed-owner-summary[data-action-id="${owner}"]`).click();
    await expect(targets.locator('.selection-field-selected > li')).toHaveCount(1);
    await expect(targets.locator('.selection-field-detail')).toContainText('6 axes · 1 upstream source + 5 copies');
    const inspection = targets.locator('.selection-field-members');
    await inspection.locator('summary').click();
    await expect(inspection.locator('li')).toHaveCount(6);
    await expect(page.locator('#relationship-count')).toHaveText('1 selection · 6 outputs');
    await expect.poll(async () => (await read()).guides).toBeGreaterThanOrEqual(6);
    await expect(page.locator('#add-relationship')).toBeEnabled();
    await page.locator('#add-relationship').click();
    await expect(page.locator('#relationship-dialog')).toBeHidden();
    const alignment = (await graph()).recipe.nodes.find(candidate => candidate.operation === 'direction_alignment');
    assert.deepEqual(alignment.directions, [ownerReference]); assert.equal(alignment.reference, null);
    assert.equal((await graph()).results[alignment.id], undefined, 'Auto-off authoring leaves the new relationship pending');
    report.cases.push({name: 'one-owner-chip', authored: 1, expanded: 6, members_inspectable: true, pending_authoring: true});
    report.phase = 'optional-reference-properties';
    await edit(alignment.id);
    await expect(page.locator('#direction-alignment-collections details li')).toHaveCount(6);
    await page.locator('#direction-alignment-reference').selectOption(JSON.stringify(upstream));
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await node(alignment.id)).reference).toEqual(upstream);
    assert.deepEqual((await node(alignment.id)).directions, [ownerReference]);
    await page.getByRole('button', {name: 'Show Same direction as (optional) choices', exact: true}).click();
    await page.getByRole('menuitemradio', {name: 'None (optional)', exact: true}).click();
    await page.locator('#apply-properties').click();
    await expect.poll(async () => (await node(alignment.id)).reference).toBe(null);
    await closeEdit();
    report.cases.push({name: 'optional-source-reference', exact_context: true, target_owner_preserved: true, clearing_serializes_null: true});
    report.phase = 'resize-live-collection';
    for (const count of [3, 6]) {
      await edit(owner);
      await page.locator('#feature-reuse-count').fill(String(count));
      await page.locator('#feature-reuse-preview').click();
      await expect(page.locator('#feature-reuse-preview-status')).toContainText(`${count} occurrences reviewed`);
      await page.locator('#apply-properties').click();
      await expect.poll(async () => (await node(owner)).circular.count).toBe(count);
      await closeEdit();
      await expect.poll(async () => (await collectionOf(owner)).collection.count).toBe(count);
      assert.deepEqual((await node(alignment.id)).directions, [ownerReference]);
      await edit(alignment.id);
      await expect(page.locator('#direction-alignment-collections details li')).toHaveCount(count);
      assert.equal(await page.locator('#direction-alignment-inputs').evaluate(control => control.selectedOptions.length), 1);
      assert.equal(await page.locator('#direction-alignment-inputs').inputValue(), ownerKey);
      await closeEdit();
    }
    report.cases.push({name: 'live-count-resize', counts: [6, 3, 6], authored_reference_unchanged: true});
    report.phase = 'save-reopen';
    const recipe = structuredClone((await graph()).recipe);
    let downloading = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Save actions', exact: true}).click();
    assert.deepEqual(JSON.parse(await downloadBytes(await downloading)), recipe);
    await load(recipe, 'pattern-relationship-actions.json');
    assert.deepEqual((await node(alignment.id)).directions, [ownerReference]);
    downloading = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Save project', exact: true}).click();
    const archive = await downloadBytes(await downloading), generation = (await read()).generation;
    await page.locator('#project-file').setInputFiles({name: 'pattern-relationship.scansor', mimeType: 'application/zip', buffer: archive});
    await expect.poll(async () => (await read()).generation).toBeGreaterThan(generation);
    await expect(page.locator('#source-progress')).toBeHidden();
    assert.deepEqual((await graph()).recipe, recipe);
    await edit(alignment.id);
    await expect(page.locator('#direction-alignment-collections details li')).toHaveCount(6);
    assert.equal(await page.locator('#direction-alignment-reference').inputValue(), '');
    report.cases.push({name: 'actions-project-round-trip', live_owner_only: true, optional_reference_null: true});
    report.phase = 'evaluate-reopened-collection';
    await closeEdit();
    await evaluate(alignment.id);
    const solved = await graph(), sourceAxis = solved.results[owner].outputs.bore.analytic.axis,
      copies = Object.values(solved.results[alignment.id].surfaces);
    assert.equal(copies.length, 5);
    for (const copy of copies) {
      const axis = copy.analytic.axis, cross = [axis[1]*sourceAxis[2]-axis[2]*sourceAxis[1],
        axis[2]*sourceAxis[0]-axis[0]*sourceAxis[2], axis[0]*sourceAxis[1]-axis[1]*sourceAxis[0]];
      assert(Math.hypot(...cross) < 1e-7, 'Every generated axis follows the upstream source');
    }
    report.cases.push({name: 'reopened-numerical-alignment', directions: 6, errors: 0});
    report.phase = 'complete'; report.complete = true; return report;
  } catch (error) {
    error.message = `Pattern relationship UI (${report.phase}): ${error.message}`;
    throw error;
  }
}
