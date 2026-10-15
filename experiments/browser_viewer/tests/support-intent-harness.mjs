// Real shared DOM workflow. snapshot is a read-only view of the app's own backend.
import assert from 'node:assert/strict';

const copy = value => structuredClone(value);
async function until(page, read, check, message) {
  const deadline = Date.now() + 30000;
  let value;
  while (Date.now() < deadline) {
    value = await read();
    if (check(value)) return value;
    await page.waitForTimeout(50);
  }
  throw Error(`${message}: ${JSON.stringify(value)}`);
}
const graphOf = value => value.graph;
const epoch = value => value.project_generation ?? value.generation;
const nodeOf = (value, id) => graphOf(value).recipe.nodes.find(node => node.id === id);
function protectedRecipe(recipe, id) {
  const value = copy(recipe), node = value.nodes.find(node => node.id === id);
  delete node.support_authority;
  delete node.axial_domain;
  return value;
}
async function command(page, name) {
  await page.getByRole('button', { name: 'Project toolbar options', exact: true }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}
async function closeEdit(page) {
  const messages = [];
  const accept = async dialog => { messages.push(dialog.message()); await dialog.accept(); };
  page.once('dialog', accept);
  await page.locator('#close-edit').click();
  assert(messages.some(message => /Discard unapplied/.test(message)));
  await page.locator('#edit-popup').waitFor({ state: 'hidden' });
}
async function actionsFile(page, recipe, snapshot) {
  const previous = await snapshot(), accept = dialog => dialog.accept();
  page.on('dialog', accept);
  try {
    await page.locator('#file').setInputFiles({ name: 'support-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe)) });
    await until(page, snapshot, value => epoch(value) === epoch(previous) + 1, 'matching actions accepted');
    await page.locator('#source-progress').waitFor({ state: 'hidden' });
  } finally { page.off('dialog', accept); }
}
function finiteGeometry(fit) {
  const geometry = fit?.analytic;
  assert(geometry && typeof geometry === 'object' && !Array.isArray(geometry));
  for (const key of ['origin', 'axis'])
    assert(Array.isArray(geometry[key]) && geometry[key].length === 3 && geometry[key].every(Number.isFinite), `finite XYZ ${key}`);
  assert(Array.isArray(geometry.axial_domain) && geometry.axial_domain.length === 2 && geometry.axial_domain.every(Number.isFinite), 'finite pair axial_domain');
  assert(geometry.axial_domain[0] < geometry.axial_domain[1], 'increasing axial_domain');
  assert(Number.isFinite(geometry.radius) && Number.isFinite(geometry.slope));
  assert(Math.abs(Math.hypot(...geometry.axis) - 1) <= 1e-10, 'canonical unit axis');
}
export function assertPrototype(fit, fixture) {
  finiteGeometry(fit);
  const geometry = fit.analytic;
  assert.equal(fit.support_intent.kind, 'reference-plane');
  assert.deepEqual(fit.support_intent.interval, [1, 9]);
  const endpoints = geometry.axial_domain.map(height => geometry.origin.map((coordinate, i) => coordinate + height * geometry.axis[i]));
  endpoints.forEach((point, index) => point.forEach((coordinate, i) => assert(Math.abs(coordinate - fixture.prototype_endpoints[index][i]) <= 1e-5, 'independent prototype endpoint')));
}
function assertSource(value, reference, fixture) {
  assert.equal(epoch(value), epoch(reference));
  assert.deepEqual(value.source, reference.source);
  assert.equal(value.source.source_sha256, fixture.source_sha256);
}
function seedFree(value) {
  const forbidden = new Set(['initial_parameters', 'initialization', 'seed', 'fit_geometry', 'solver', 'residuals', 'weighted_rms']);
  const walk = item => {
    if (Array.isArray(item)) item.forEach(walk);
    else if (item && typeof item === 'object') for (const [key, child] of Object.entries(item)) {
      assert(!forbidden.has(key), `Computed authored field ${key}`);
      walk(child);
    }
  };
  walk(value);
}

export async function runSupportIntentUi({ page, fixture, snapshot, report = {}, checkpoint = async () => {} }) {
  Object.assign(report, { kind: fixture.kind, complete: false, cases: [], graphs: {}, graph_generations: {}, source_sha256: fixture.source_sha256 });
  const checked = async name => { report.cases.push({ name, complete: true }); await checkpoint(report); };
  const retain = (name, value) => { report.graphs[name] = copy(graphOf(value)); report.graph_generations[name] = epoch(value); };
  await page.getByRole('checkbox', { name: 'Auto', exact: true }).uncheck();
  const picker = page.waitForEvent('filechooser');
  await page.locator('[data-add-scan]').click();
  const chooser = await picker;
  assert.equal(await chooser.element().getAttribute('id'), 'scan-file');
  await chooser.setFiles({ name: fixture.filename, mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.raw, 'base64') });
  await until(page, snapshot, value => value.source?.source_sha256 === fixture.source_sha256, 'source import');
  await page.locator('#source-progress').waitFor({ state: 'hidden' });
  await actionsFile(page, fixture.recipe, snapshot);
  await until(page, snapshot, value => nodeOf(value, fixture.clone)?.support_authority === 'prototype', 'matching actions');
  await page.locator('#source-progress').waitFor({ state: 'hidden' });
  const accepted = await snapshot(), before = graphOf(accepted).recipe;
  seedFree(before);
  report.accepted = copy(accepted);
  await checkpoint(report);
  const stable = value => {
    assertSource(value, accepted, fixture);
    assert.deepEqual(protectedRecipe(graphOf(value).recipe, fixture.clone), protectedRecipe(before, fixture.clone));
  };
  const unlocked = () => until(page, () => page.getByRole('button', { name: 'Evaluate all', exact: true }).isEnabled(), Boolean, 'visible evaluation controls unlocked');
  const open = async () => {
    await unlocked();
    const owner = page.locator('#action-list .managed-owner-summary[data-action-id="reuse"]');
    if (!await owner.evaluate(element => element.parentElement.open)) await owner.locator('.tree-toggle').click();
    const target = page.locator('#action-list .managed-target > details > summary').first();
    if (!await target.evaluate(element => element.parentElement.open)) await target.locator('.tree-toggle').click();
    await page.locator(`#action-list .action-select[data-action-id="${fixture.clone}"]`).dblclick();
    await page.locator('#fit-support-authority').waitFor({ state: 'visible' });
    for (const id of ['surface-kind', 'fit-reference']) assert.equal(await page.locator(`#${id}`).isDisabled(), true);
    assert.equal(await page.locator('#apply-properties').isEnabled(), true);
  };
  const local = async (start, end) => {
    await page.locator('#fit-support-authority').selectOption('local');
    await page.locator('#axial-start').fill(start);
    await page.locator('#axial-end').fill(end);
  };
  const apply = async predicate => {
    await page.locator('#apply-properties').click();
    const value = await until(page, snapshot, value => predicate(nodeOf(value, fixture.clone)), 'support Apply');
    stable(value);
    await until(page, () => page.locator('#apply-properties').isEnabled(), Boolean, 'Apply released');
    return value;
  };
  const evaluate = async () => {
    await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
    const value = await until(page, snapshot, value => graphOf(value).states[fixture.clone] === 'ready' && !graphOf(value).evaluation_running, 'evaluated support');
    stable(value);
    assert.deepEqual(graphOf(value).errors, {});
    assert.deepEqual(graphOf(value).required_failures ?? [], []);
    assert(Object.values(graphOf(value).states).every(state => state === 'ready'));
    assert.deepEqual(graphOf(value).results[fixture.clone].ids, fixture.target_ids);
    finiteGeometry(graphOf(value).results[fixture.clone]);
    await unlocked();
    return value;
  };

  await open();
  assert.equal(await page.locator('#fit-support-authority').inputValue(), 'prototype');
  assert.equal(await page.locator('#axial-start').isDisabled(), true);
  await local('1', '9');
  await closeEdit(page);
  let value = await snapshot();
  assertSource(value, accepted, fixture);
  assert.equal(graphOf(value).token, graphOf(accepted).token);
  assert.deepEqual(graphOf(value).recipe, before);
  await open();
  assert.equal(await page.locator('#fit-support-authority').inputValue(), 'prototype');
  await checked('finite-local-cancel-preserves-project');

  await local('1', '9');
  value = await apply(node => node.support_authority === 'local' && JSON.stringify(node.axial_domain) === '[1,9]');
  retain('local_finite', value);
  // The target axis is horizontal; Local finite is target-Z0, not inherited X caps.
  // Persistence is the oracle here. Deliberately do not demand numerical success.
  await page.locator('#close-edit').click();
  await open();
  assert.equal(await page.locator('#fit-support-authority').inputValue(), 'local');
  assert.equal(await page.locator('#axial-start').inputValue(), '1');
  assert.equal(await page.locator('#axial-end').inputValue(), '9');
  await checked('finite-local-apply-protects-other-fields');

  await local('', '');
  await apply(node => node.support_authority === 'local' && node.axial_domain === null);
  await page.locator('#close-edit').click();
  value = await evaluate();
  const automatic = graphOf(value).results[fixture.clone], geometry = automatic.analytic;
  assert.equal(automatic.support_intent.kind, 'selection');
  const heights = fixture.target_ids.map(id => fixture.positions[id].reduce((sum, coordinate, i) => sum + (coordinate - geometry.origin[i]) * geometry.axis[i], 0));
  for (const [actual, expected] of [[geometry.axial_domain[0], Math.min(...heights)], [geometry.axial_domain[1], Math.max(...heights)]])
    assert(Math.abs(actual - expected) <= 1e-10, 'independent final-gauge automatic bound');
  retain('local_auto', value);
  await checked('local-none-evaluates-original-target-rows');

  await open();
  await page.locator('#fit-support-authority').selectOption('prototype');
  await apply(node => node.support_authority === 'prototype');
  await page.locator('#close-edit').click();
  value = await evaluate();
  assertPrototype(graphOf(value).results[fixture.clone], fixture);
  retain('prototype', value);
  await checked('prototype-restores-authored-world-endpoints');

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save actions', exact: true }).click();
  let text = '';
  for await (const part of await (await download).createReadStream()) text += part.toString();
  const saved = JSON.parse(text);
  seedFree(saved);
  assert.deepEqual(saved, graphOf(value).recipe);
  const previous = value;
  await actionsFile(page, saved, snapshot);
  await until(page, snapshot, value => epoch(value) === epoch(previous) + 1, 'saved actions replay accepted');
  // Opening actions is a new accepted epoch; it preserves the original source bytes.
  const replayAccepted = await snapshot();
  await page.getByRole('button', { name: 'Evaluate all', exact: true }).click();
  value = await until(page, snapshot, value => graphOf(value).states[fixture.clone] === 'ready' && !graphOf(value).evaluation_running, 'saved actions replay fit');
  assert.equal(epoch(value), epoch(replayAccepted));
  assert.equal(value.source.source_sha256, fixture.source_sha256);
  assert.deepEqual(graphOf(value).recipe, saved);
  assert.deepEqual(graphOf(value).errors, {});
  assert(Object.values(graphOf(value).states).every(state => state === 'ready'));
  assert.deepEqual(graphOf(value).results[fixture.clone].ids, fixture.target_ids);
  assertPrototype(graphOf(value).results[fixture.clone], fixture);
  await unlocked();
  retain('replayed', value);
  report.saved_recipe = saved;
  await checked('actual-save-open-and-reevaluate');

  await open();
  await local('2', '8');
  await page.locator('#axial-start').evaluate(input => { window.supportDraft264 = input; });
  const pending = await snapshot(), dialogs = [];
  const decline = async dialog => { dialogs.push(dialog.message()); await dialog.dismiss(); };
  page.on('dialog', decline);
  try { await command(page, 'New project'); } finally { page.off('dialog', decline); }
  assert(dialogs.some(message => /Discard unapplied/.test(message)));
  value = await snapshot();
  assert.equal(epoch(value), epoch(pending));
  assert.deepEqual(graphOf(value).recipe, graphOf(pending).recipe);
  assert.equal(await page.locator('#axial-start').evaluate(input => input === window.supportDraft264 && input.value === '2'), true);
  assert.equal(await page.locator('#fit-support-authority').inputValue(), 'local');
  await closeEdit(page);
  assert.equal(await page.getByRole('button', { name: 'Evaluate all', exact: true }).isEnabled(), true);
  await checked('declined-project-discard-preserves-live-support-draft');
  report.complete = true;
  return report;
}
