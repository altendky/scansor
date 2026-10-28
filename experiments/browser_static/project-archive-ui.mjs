// Actual project controls and downloads. Instrumentation only reads app state or
// briefly holds transport delivery at an existing source-acceptance boundary.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {sha256} from './server.mjs';
import {installMainObservers, readVirtualScanUi, virtualScanFixture, virtualScanUiObserverSuffix} from './virtual-scan-ui.mjs';

const repository = fileURLToPath(new URL('../..', import.meta.url));
export const projectArchiveUiObserverSuffix = virtualScanUiObserverSuffix;
export function projectArchiveOracle(python) {
  const run = (mode, input) => JSON.parse(execFileSync(python,
    ['-m', 'experiments.browser_static.project_archive_expected', mode], {
      cwd: repository, input, encoding: 'utf8', maxBuffer: 8 * 1024 ** 2,
      env: {...process.env, PYTHONPATH: [repository, path.join(repository, 'src')].join(path.delimiter),
        OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'},
    }));
  return {legacyFixture: run('legacy-fixture'), inspectArchive: async filename => run('inspect', await readFile(filename))};
}

export function installProjectArchiveTransportObserver() {
  const NativeWorker = window.Worker;
  const observation = window.__projectArchiveTransport359 = {workers: 0, armed: false, held: [], request: null,
    release() { this.armed = false; for (const deliver of this.held.splice(0)) deliver(); this.request = null; }};
  window.Worker = class extends NativeWorker {
    constructor(...args) { super(...args); observation.workers++; }
    set onmessage(callback) { super.onmessage = event => {
      const data = event.data;
      if (observation.armed && (data.kind === 'source-candidate'
        || data.id === observation.request && data.kind === 'transfer-confirmed')) {
        observation.request = data.id;
        observation.held.push(() => callback?.(event));
        return;
      }
      callback?.(event);
    }; }
  };
}

const quiet = value => value?.ready && !value.inert && !value.transition_busy && !value.evaluation_running
  && !value.selection_pending && !value.selection_drawing && !value.authoring_applying;
async function until(page, predicate, phase) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const value = await readVirtualScanUi(page);
    if (predicate(value)) return value;
    await page.waitForTimeout(40);
  }
  throw Error(`Project archive UI timed out: ${phase}; ${await page.locator('#status').textContent()}`);
}
async function closeEditor(page) {
  if (await page.locator('#close-edit').isVisible()) await page.locator('#close-edit').click();
  else {
    const tab = page.getByRole('tab', {name: 'Edit', exact: true});
    if (await tab.count()) await tab.getByTitle('Close', {exact: true}).click();
  }
}
async function command(page, name) {
  await closeEditor(page);
  await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
  await page.getByRole('menuitem', {name, exact: true}).click();
}
async function choose(page, button, expected, file) {
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), button.click()]);
  assert.equal(await chooser.element().getAttribute('id'), expected);
  await chooser.setFiles(file);
}
async function openProject(page, file) {
  await choose(page, page.locator('[data-command-id="open-project"]:visible'), 'project-file', file);
}
async function download(page, name) {
  const [result] = await Promise.all([page.waitForEvent('download'),
    page.getByRole('button', {name, exact: true}).click()]);
  const filename = await result.path();
  assert(filename && !(await result.failure()));
  return {filename, bytes: await readFile(filename), suggested: result.suggestedFilename()};
}
function sameSource(actual, expected) {
  assert.equal(actual.generation, expected.generation);
  assert.deepEqual(actual.graph.recipe, expected.graph.recipe);
  assert.deepEqual(actual.metadata.source, expected.metadata.source);
  assert.deepEqual(actual.positions, expected.positions);
  assert.deepEqual(actual.mesh_positions, actual.positions);
}
function sourceIdentity(metadata) {
  if (!metadata.source) return null;
  const {filename, ...identity} = metadata.source;
  void filename;
  return identity;
}
async function assertDraft(page) {
  assert.equal(await page.locator('#new-point-label').inputValue(), 'Keep this project draft');
  assert.equal(await page.locator('#new-point-label').evaluate(input => input === globalThis.__archiveDraft359), true);
}

export async function exerciseProjectArchiveUi({page, inspectArchive, legacyFixture, force = false, cancelOpen}) {
  const result = {schema: 'scansor-project-archive-ui-v1', complete: false, phase: 'startup', cases: [],
    archives: [], force_recoveries: [], timings: [], instrumentation: {
      app_suffix_sha256: sha256(Buffer.from(projectArchiveUiObserverSuffix)),
      main_observer_sha256: sha256(Buffer.from(installMainObservers.toString())),
      transport_observer_sha256: force ? sha256(Buffer.from(installProjectArchiveTransportObserver.toString())) : null,
      description: 'Read-only app observer; real file choosers, editing, project downloads and Force controls. Cancellation holds transport delivery without replacing operation authority.'}};
  const raw = virtualScanFixture(), rawHash = sha256(raw);
  const file = {name: 'project-original.ply', mimeType: 'application/octet-stream', buffer: raw};
  try {
    let state = await until(page, quiet, 'ready');
    await page.getByRole('checkbox', {name: 'Auto', exact: true}).uncheck();
    if (state.graph.recipe.nodes.length) {
      await command(page, 'New project');
      state = await until(page, value => quiet(value) && value.generation === state.generation + 1
        && !value.graph.recipe.nodes.length, 'empty');
    }
    result.phase = 'original-import-and-edit';
    await choose(page, page.locator('[data-add-scan]'), 'scan-file', file);
    const imported = await until(page, value => quiet(value) && value.metadata.vertices === 289, 'original');
    const originalBinding = imported.metadata.virtual_scans.active_binding;
    const sourceId = imported.graph.recipe.nodes.find(node => node.operation === 'source').id;
    await page.locator(`#action-list .action-select[data-action-id="${sourceId}"]`).click({button: 'right'});
    await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Edited inside portable project');
    await page.locator('#rename-feature-form').getByRole('button', {name: 'Rename', exact: true}).click();
    await until(page, value => quiet(value) && value.graph.recipe.nodes.find(node => node.id === sourceId).label === 'Edited inside portable project', 'source rename');
    result.phase = 'create-virtual';
    await page.locator('#create-virtual-scan').click();
    await page.locator('#virtual-scan-divisions').fill('4');
    await page.locator('#confirm-create-virtual-scan').click();
    const reduced = await until(page, value => quiet(value) && value.metadata.virtual_scans.virtual, 'virtual');
    const reducedBinding = reduced.metadata.virtual_scans.active_binding;
    assert.equal(reduced.metadata.vertices, 25);
    const activate = async binding => {
      await closeEditor(page);
      const before = await readVirtualScanUi(page);
      if (before.metadata.virtual_scans.active_binding === binding) return before;
      await page.locator('#active-scan').selectOption(binding);
      return until(page, value => quiet(value) && value.generation === before.generation + 1
        && value.metadata.virtual_scans.active_binding === binding, 'activate');
    };
    let preservedArchive;
    for (const binding of [originalBinding, reducedBinding]) {
      result.phase = `save-reopen-${binding === originalBinding ? 'original' : 'virtual'}`;
      const before = await activate(binding), started = performance.now();
      const saved = await download(page, 'Save project');
      assert.equal(saved.suggested, 'project.scansor');
      const inspected = await inspectArchive(saved.filename);
      assert.equal(inspected.source_sha256, rawHash);
      assert.equal(inspected.source_bytes, raw.length);
      assert.equal(inspected.source_eof_verified, true);
      assert.equal(inspected.archive_entry, inspected.member_name);
      assert.equal(inspected.members.filter(name => name.endsWith('.ply')).length, 1);
      assert.deepEqual(inspected.actions, before.graph.recipe);
      const actions = JSON.parse((await download(page, 'Save actions')).bytes);
      assert.deepEqual(actions, inspected.actions);
      await command(page, 'New project');
      const empty = await until(page, value => quiet(value) && !value.graph.recipe.nodes.length, 'empty before one-file reopen');
      await openProject(page, {name: 'renamed-portable-project.scansor', mimeType: 'application/zip', buffer: saved.bytes});
      const reopened = await until(page, value => quiet(value) && value.generation === empty.generation + 1
        && value.metadata.virtual_scans.active_binding === binding, 'one-file reopen');
      assert.deepEqual(reopened.graph.recipe, actions);
      assert.deepEqual(sourceIdentity(reopened.metadata), sourceIdentity(before.metadata));
      assert.deepEqual(reopened.positions, before.positions);
      assert.deepEqual(reopened.positions, reopened.mesh_positions);
      assert.equal(reopened.metadata.vertices, binding === originalBinding ? 289 : 25);
      assert.equal(await page.locator('#locate-source-dialog').isVisible(), false);
      result.archives.push({active_binding: binding, archive_bytes: saved.bytes.length, original_sha256: inspected.source_sha256,
        original_bytes: inspected.source_bytes, source_entry: inspected.member_name, one_file_reopen: true,
        current_edit_preserved: true, exact_actions: true});
      result.timings.push({operation: 'save-reset-reopen', active_binding: binding, wall_ms: performance.now() - started});
      if (force) {
        const workers = reopened.resources.workers;
        await page.locator('#force-restart').click();
        const recovered = await until(page, value => quiet(value) && value.resources.workers > workers
          && value.generation === reopened.generation && value.metadata.virtual_scans.active_binding === binding, 'archive Force');
        sameSource(recovered, reopened);
        const resaved = await download(page, 'Save project');
        const verified = await inspectArchive(resaved.filename);
        assert.equal(verified.source_sha256, rawHash);
        assert.deepEqual(verified.actions, actions);
        result.force_recoveries.push({active_binding: binding, exact_source_resaved: true, accepted_generation_preserved: true});
      }
      preservedArchive = saved;
    }
    result.cases.push({name: 'both-active-choices', complete: true, exact_original_member: true, renamed_archive: true});
    result.phase = 'failed-and-cancelled-open-preserve-draft';
    await page.getByRole('button', {name: 'Point', exact: true}).click();
    await page.locator('#new-point-label').fill('Keep this project draft');
    await page.locator('#new-point-label').evaluate(input => { globalThis.__archiveDraft359 = input; });
    const prior = await readVirtualScanUi(page);
    await openProject(page, {name: 'broken.scansor', mimeType: 'application/zip', buffer: Buffer.from('not a ZIP project')});
    await page.waitForFunction(() => /archive|directory|compressed|invalid/i.test(document.querySelector('#status').textContent)
      && document.querySelector('#source-progress').hidden, null, {timeout: 30000});
    await until(page, quiet, 'failed open settled');
    sameSource(await readVirtualScanUi(page), prior);
    await assertDraft(page);
    result.cases.push({name: 'failed-open-keeps-draft', complete: true, same_dom_input: true});
    assert(preservedArchive);
    if (cancelOpen) {
      const hold = await cancelOpen();
      try {
        await openProject(page, {name: 'cancelled.scansor', mimeType: 'application/zip', buffer: preservedArchive.bytes});
        await hold.arrived();
        await page.locator('#cancel-source-import').click();
      } finally { await hold.release(); }
      await until(page, quiet, 'cancelled open settled');
      sameSource(await readVirtualScanUi(page), prior);
      await assertDraft(page);
      result.cases.push({name: 'cancelled-open-keeps-draft', complete: true, same_dom_input: true});
    }
    await closeEditor(page);
    result.phase = 'known-legacy-migration';
    const legacyRaw = Buffer.from(legacyFixture.archive_base64, 'base64');
    const current = await readVirtualScanUi(page);
    await openProject(page, {name: 'captured-nozzle.scansor', mimeType: 'application/zip', buffer: legacyRaw});
    const legacy = await until(page, value => quiet(value) && value.generation === current.generation + 1
      && value.metadata.vertices === legacyFixture.vertices, 'legacy migration');
    assert.deepEqual(legacy.graph.recipe, legacyFixture.recipe);
    assert.equal(await page.locator('#reset').isDisabled(), true, 'Portable project cannot restore unrelated example actions');
    const legacySource = legacy.graph.recipe.nodes.find(node => node.operation === 'source');
    assert.equal(legacySource.source_sha256, legacyFixture.source_sha256);
    assert.equal(legacySource.reference_sha256, legacyFixture.reference_sha256);
    const savedLegacy = await download(page, 'Save project'), portable = await inspectArchive(savedLegacy.filename);
    assert.equal(portable.source_sha256, legacyFixture.source_sha256);
    assert.equal(portable.source_bytes, legacyFixture.source_bytes);
    assert(portable.legacy_reference && portable.members.includes('references/legacy.json'));
    await command(page, 'New project');
    const empty = await until(page, value => quiet(value) && !value.graph.recipe.nodes.length, 'empty before portable legacy');
    await openProject(page, {name: 'portable-nozzle-renamed.scansor', mimeType: 'application/zip', buffer: savedLegacy.bytes});
    let portableLegacy = await until(page, value => quiet(value) && value.generation === empty.generation + 1
      && value.metadata.vertices === legacyFixture.vertices, 'portable legacy reopen');
    assert.deepEqual(portableLegacy.graph.recipe, legacy.graph.recipe);
    assert.deepEqual(portableLegacy.positions, legacy.positions);
    assert.deepEqual(portableLegacy.metadata.source, legacy.metadata.source);
    if (force) {
      const workers = portableLegacy.resources.workers;
      await page.locator('#force-restart').click();
      const recovered = await until(page, value => quiet(value) && value.resources.workers > workers
        && value.generation === portableLegacy.generation && value.metadata.vertices === legacyFixture.vertices, 'portable legacy Force');
      sameSource(recovered, portableLegacy);
      portableLegacy = recovered;
    }
    const resavedLegacy = await inspectArchive((await download(page, 'Save project')).filename);
    assert.equal(resavedLegacy.source_sha256, legacyFixture.source_sha256);
    assert.deepEqual(resavedLegacy.actions, portable.actions);
    result.cases.push({name: 'known-legacy-portable-migration', complete: true, source_sha256: legacyFixture.source_sha256,
      reference_sha256: legacyFixture.reference_sha256, embedded_reference: true, one_file_reopen: true, force_recovered: force});
    result.complete = true; result.phase = 'complete';
    return result;
  } catch (error) {
    result.failure = String(error); result.failure_snapshot = await readVirtualScanUi(page).catch(() => null);
    error.projectArchiveReport = result; throw error;
  }
}

export async function validateProjectArchiveUi({browser, site, preview, prefix, isolation, report, nativePython}) {
  const context = await browser.newContext({serviceWorkers: 'block', viewport: {width: 1440, height: 1000}});
  await context.addInitScript(installMainObservers);
  await context.addInitScript(installProjectArchiveTransportObserver);
  context.on('request', request => {
    const url = request.url(); report.requests.push(url);
    if (!url.startsWith(preview.origin + '/') && !url.startsWith('blob:' + preview.origin + '/')) report.unexpected_requests.push(url);
    if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
  });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== preview.origin) return route.abort();
    if (!url.pathname.endsWith('/app.js')) return route.continue();
    const relative = url.pathname.slice(prefix.length), record = site.records.get(relative);
    assert(record, 'Observed production app must be manifest-listed');
    const bytes = await readFile(path.join(site.root, relative));
    assert.equal(bytes.length, record.bytes); assert.equal(sha256(bytes), record.sha256);
    return route.fulfill({status: 200, contentType: 'text/javascript', body: Buffer.concat([bytes, Buffer.from(projectArchiveUiObserverSuffix)]),
      headers: isolation ? {'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp'} : {}});
  });
  const page = await context.newPage(); page.setDefaultTimeout(30000);
  page.on('pageerror', error => report.page_errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  const oracle = projectArchiveOracle(nativePython);
  try {
    await page.goto(preview.origin + prefix + 'app/');
    report.project_archives = await exerciseProjectArchiveUi({page, ...oracle, force: true,
      cancelOpen: async () => {
        await page.evaluate(() => { globalThis.__projectArchiveTransport359.armed = true; });
        return {arrived: () => page.waitForFunction(() => globalThis.__projectArchiveTransport359.held.length > 0),
          release: () => page.evaluate(() => globalThis.__projectArchiveTransport359.release())};
      }});
    assert.equal(await page.evaluate(() => crossOriginIsolated), isolation);
  } catch (error) {
    report.project_archives = error.projectArchiveReport || {complete: false, failure: String(error)};
    throw error;
  } finally { await context.close(); }
}
