import assert from 'node:assert/strict';

// Read-only observer. All authored changes below use actual product controls.
export const seedSurfaceObserver = `\nObject.defineProperty(globalThis, '__seedSurface', { value: {
  read: () => structuredClone({graph: graphState, generation: workspaceGeneration, vertices: metadata?.vertices})
}});\n`;

export function seedSurfaceGrid() {
  const size = 9, vertices = size * size, faces = 2 * (size - 1) ** 2;
  const header = Buffer.from(`ply\nformat binary_little_endian 1.0\nelement vertex ${vertices}\nproperty float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\nelement face ${faces}\nproperty list uchar int vertex_indices\nend_header\n`);
  const data = Buffer.alloc(vertices * 24 + faces * 13);
  let offset = 0;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    for (const value of [x, y, Math.max(0, x - 4), 0, 0, 1]) { data.writeFloatLE(value, offset); offset += 4; }
  }
  for (let y = 0; y < size - 1; y++) for (let x = 0; x < size - 1; x++) {
    const a = y * size + x;
    for (const face of [[a, a + 1, a + size], [a + 1, a + size + 1, a + size]]) {
      data.writeUInt8(3, offset++);
      for (const id of face) { data.writeInt32LE(id, offset); offset += 4; }
    }
  }
  return Buffer.concat([header, data]);
}

export async function exerciseSeedSurfaceUi({page, expect}) {
  const read = () => page.evaluate(() => globalThis.__seedSurface.read());
  const evaluateAll = page.getByRole('button', {name: 'Evaluate all', exact: true});
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#load')).toBeEnabled();
  await expect(evaluateAll).toBeEnabled();
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
  await expect.poll(async () => (await read()).graph.recipe.nodes.length).toBe(0);
  await expect(page.locator('#scan-file')).toBeAttached();
  await page.locator('#scan-file').setInputFiles({name: 'seed-grid.ply', mimeType: 'application/octet-stream', buffer: seedSurfaceGrid()});
  await expect(page.locator('#source-progress')).toBeHidden();
  await expect.poll(async () => (await read()).vertices).toBe(81);
  await expect(evaluateAll).toBeEnabled();
  const source = (await read()).graph.recipe.nodes.find(node => node.operation === 'source');
  const barriers = Array.from({length: 9}, (_, row) => row * 9 + 6);
  const recipe = {schema_version: 2, nodes: [source,
    {id: 'seeds', label: 'Seed patch', operation: 'selection', source: source.id, ids: [38]},
    {id: 'barriers', label: 'Painted barrier', operation: 'selection', source: source.id, ids: barriers},
    {id: 'baseline', label: 'Baseline plane', operation: 'fit', kind: 'plane', selections: ['seeds', 'barriers']},
  ], output: 'baseline'};
  await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
  await page.locator('#file').setInputFiles({name: 'seed-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
  await expect.poll(async () => (await read()).graph.recipe.nodes.some(node => node.id === 'baseline')).toBe(true);
  await expect(page.locator('#source-progress')).toBeHidden();
  await evaluateAll.click();
  await expect.poll(async () => (await read()).graph.states.baseline).toBe('ready');
  const edit = async id => {
    await page.locator(`#action-list .action-select[data-action-id="${id}"]`).click({button: 'right'});
    await page.locator('#feature-context-edit').click();
  };
  // A hidden required field in the new editor must not block unrelated edits.
  await edit('baseline');
  await page.locator('#fit-inputs').selectOption(['barriers']);
  await page.locator('#fit-inputs').selectOption(['seeds', 'barriers']);
  await page.locator('#action-properties').evaluate(form => { globalThis.__seedSurfaceSubmit = 0;
    form.addEventListener('submit', () => globalThis.__seedSurfaceSubmit++, {once: true}); });
  await page.locator('#apply-properties').click();
  await expect.poll(() => page.evaluate(() => globalThis.__seedSurfaceSubmit)).toBe(1);
  await page.getByRole('button', {name: 'Surface selection', exact: true}).click();
  await page.locator('#new-surface-selection-seed').selectOption('seeds');
  await page.locator('#new-surface-selection-barriers').selectOption('barriers');
  await expect(page.locator('#create-surface-selection')).toBeDisabled();
  await page.locator('#new-surface-selection-preview').click();
  await expect(page.locator('#create-surface-selection')).toBeEnabled();
  const count = async phase => Number((await page.locator(`#surface-selection-dialog [data-preview-phase="${phase}"]`).textContent()).match(/\d[\d,]*$/)[0].replaceAll(',', ''));
  const narrowAngle = await count('inset');
  await page.locator('#new-surface-selection-angle').fill('60');
  await expect(page.locator('#create-surface-selection')).toBeDisabled();
  await expect(page.locator('#create-surface-selection')).toBeEnabled();
  const beforeInset = await count('inset');
  assert(beforeInset > narrowAngle, 'Increasing the angle crosses the prescribed 45 degree crease');
  assert(beforeInset > 1 && beforeInset < 81);
  await page.locator('#new-surface-selection-setback').fill('1.1');
  await expect(page.locator('#create-surface-selection')).toBeDisabled();
  await expect(page.locator('#create-surface-selection')).toBeEnabled();
  const afterInset = await count('inset');
  assert(afterInset >= 1 && afterInset < beforeInset);
  await page.locator('#surface-selection-dialog [data-preview-phase="boundaries"]').click();
  await expect(page.locator('#surface-selection-dialog [data-preview-phase="boundaries"]')).toHaveAttribute('aria-pressed', 'false');
  await page.locator('#create-surface-selection').click();
  await expect(page.locator('#surface-selection-dialog')).toBeHidden();
  const derived = (await read()).graph.recipe.nodes.find(node => node.operation === 'surface_selection');
  assert(derived && !('ids' in derived));
  assert.deepEqual(derived.barriers, ['barriers']);
  await evaluateAll.click();
  await expect.poll(async () => (await read()).graph.states[derived.id]).toBe('ready');
  const ready = (await read()).graph;
  const membership = ready.memberships[derived.id];
  assert.equal(membership.length, afterInset);
  assert(membership.includes(38) && !membership.some(id => barriers.includes(id)));
  assert.deepEqual(ready.recipe.nodes.find(node => node.id === 'seeds').ids, [38]);
  await edit(derived.id);
  await page.locator('#surface-selection-preview').click();
  await expect(page.locator('#surface-selection-preview-status')).toContainText('selected');
  await page.locator('#surface-selection-setback').fill('1.2');
  await expect.poll(async () => {
    const text = await page.locator('#surface-selection-diagnostics').textContent();
    return text ? JSON.parse(text).setback : null;
  }).toBe(1.2);
  await expect(page.locator('#surface-selection-setback')).toBeFocused();
  // Applying while the last numeric input remains focused must not lose the
  // reviewed preview to the unchanged change event emitted by its blur.
  await page.locator('#apply-properties').click();
  await expect.poll(async () => (await read()).graph.recipe.nodes.find(node => node.id === derived.id).setback).toBe(1.2);
  await evaluateAll.click();
  await expect.poll(async () => (await read()).graph.states[derived.id]).toBe('ready');
  assert.deepEqual((await read()).graph.memberships[derived.id], membership);
  // The derived result is an ordinary observation input to a positional Fit.
  await page.getByRole('button', {name: 'Surface fit', exact: true}).click();
  await page.locator('#new-fit-kind').selectOption('plane');
  await page.locator('#new-fit-inputs').selectOption(derived.id);
  await page.locator('#add-fit-form').getByRole('button', {name: 'Add fit', exact: true}).click();
  await expect(page.locator('#fit-dialog')).toBeHidden();
  await evaluateAll.click();
  await expect.poll(async () => Object.values((await read()).graph.states).every(state => state === 'ready')).toBe(true);
  const fitted = (await read()).graph.recipe.nodes.find(node => node.operation === 'fit' && node.selections.includes(derived.id));
  assert.deepEqual((await read()).graph.results[fitted.id].ids, membership);
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Save actions', exact: true}).click();
  let savedText = '';
  for await (const part of await (await downloading).createReadStream()) savedText += part.toString();
  const saved = JSON.parse(savedText);
  assert(!('ids' in saved.nodes.find(node => node.id === derived.id)));
  await page.locator('#file').setInputFiles({name: 'reopened-actions.json', mimeType: 'application/json', buffer: Buffer.from(savedText)});
  await expect(page.locator('#source-progress')).toBeHidden();
  await evaluateAll.click();
  await expect.poll(async () => (await read()).graph.states[fitted.id]).toBe('ready');
  assert.deepEqual((await read()).graph.memberships[derived.id], membership);
  return {narrowAngle, beforeInset, afterInset, membership, seed_ids: [38], barriers, derived: saved.nodes.find(node => node.id === derived.id),
    fitted_ids: (await read()).graph.results[fitted.id].ids, reopened_ids: (await read()).graph.memberships[derived.id]};
}
