import assert from 'node:assert/strict';

export const cadDisplayQualityObserver = `\nObject.defineProperty(globalThis, '__cadDisplayUi', {value: {
  read: () => {
    const entry = cadDisplay.lookup('flange-body');
    return structuredClone({graph: graphState, generation: workspaceGeneration, mode: modelPickMode, faceTarget: buildFacesTarget,
      busy: cadDisplay.busy, bytes: cadDisplay.bytes, lifecycle: backend.getLifecycle(),
      selected: [...selectedFeatureIds], camera: [...camera.position.toArray(), ...controls.target.toArray()],
      cached: entry && {quality: entry.quality, positions: Array.from(entry.preview.positions),
        indices: Array.from(entry.preview.indices), edges: entry.edges.map(edge => ({closed: edge.closed, positions: Array.from(edge.positions)}))},
      sourceProxies: modelPickSourceFaces.children.map(child => ({faces: child.userData.sourceFaces || [child.userData.sourceFace],
        sharedVertices: child.geometry.getAttribute('position').array === entry?.preview.positions,
        indices: Array.from(child.geometry.index?.array || [])})),
      rendered: [constructedFaces, overlays, modelPickGuides].flatMap((group, index) => group.children
        .filter(child => child.userData.cadFeature).map(child => ({group: index, feature: child.userData.cadFeature,
          quality: child.userData.cadQuality, edge: !!child.userData.cadEdge, type: child.type,
          positions: Array.from(child.geometry.getAttribute('position').array),
          indices: Array.from(child.geometry.index?.array || [])}))) });
  },
  point: coordinates => {
    modelRoot.updateMatrixWorld(true); camera.updateMatrixWorld();
    const point = new THREE.Vector3(...coordinates).applyMatrix4(modelRoot.matrixWorld).project(camera),
      box = renderer.domElement.getBoundingClientRect(), x = box.left + (point.x + 1) * box.width / 2,
      y = box.top + (1 - point.y) * box.height / 2, ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(point.x, point.y), camera);
    return {x, y, element: document.elementFromPoint(x, y)?.tagName, features: ray.intersectObjects(constructedFaces.children, true)
      .filter(hit => hit.object.isMesh).map(hit => hit.object.userData.cadFeature),
      sourceFaces: ray.intersectObjects(modelPickSourceFaces.children, false)
        .map(hit => hit.object.userData.sourceFaces || [hit.object.userData.sourceFace])};
  }
}});\n`;

const accepted = state => structuredClone({token: state.token, revision: state.revision,
  recipe: state.recipe, results: state.results, states: state.states, errors: state.errors});
const bodyMesh = state => state.rendered.find(item => item.group === 0 && item.feature === 'flange-body' && item.type === 'Mesh');
const triangleCount = state => bodyMesh(state).indices.length / 3;
const closeNumber = (value, expected, tolerance = 1e-5) => assert(Math.abs(value - expected) < tolerance, `${value} differs from ${expected}`);

function assertBoundaryRings(state) {
  const vertices = new Set();
  for (let i = 0; i < state.cached.positions.length; i += 3) vertices.add(state.cached.positions.slice(i, i + 3).join(','));
  for (const edge of state.cached.edges) for (let i = 0; i < edge.positions.length; i += 3)
    assert(vertices.has(edge.positions.slice(i, i + 3).join(',')), 'The visible shared boundary must use the actual face mesh vertices');
  for (const z of [0, -2]) for (const [x, y, radius] of [[-2, 0, 1], [2, 0, .75], [0, 0, 5 + .15 * z]]) {
    let span = 0;
    for (const edge of state.cached.edges) {
      const points = [];
      for (let i = 0; i < edge.positions.length; i += 3) points.push(edge.positions.slice(i, i + 3));
      if (!points.every(point => Math.abs(point[2] - z) < 1e-4 && Math.abs(Math.hypot(point[0] - x, point[1] - y) - radius) < 1e-4)) continue;
      if (edge.closed) points.push(points[0]);
      let arc = 0;
      for (let i = 1; i < points.length; i++) {
        const a = [points[i - 1][0] - x, points[i - 1][1] - y], b = [points[i][0] - x, points[i][1] - y];
        arc += Math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]);
      }
      span += Math.abs(arc);
    }
    closeNumber(span, 2 * Math.PI, 1e-4);
  }
}

async function download(page, name) {
  const downloading = page.waitForEvent('download'); await page.getByRole('button', {name, exact: true}).click();
  const chunks = [];
  for await (const chunk of await (await downloading).createReadStream()) chunks.push(chunk);
  return Buffer.concat(chunks);
}

export async function exerciseCadDisplayQualityUi({page, expect, fixture, inspectExport, gate = null}) {
  assert(!['8765', '8766'].includes(new URL(page.url()).port));
  const read = () => page.evaluate(() => globalThis.__cadDisplayUi.read()), graph = async () => (await read()).graph,
    quality = page.locator('#cad-display-quality'), faces = page.locator('#faces-only'),
    auto = page.getByRole('checkbox', {name: 'Auto', exact: true}), report = {complete: false, phase: 'fixture'}, requests = [],
    record = request => {if (request.method() === 'POST' && /\/api\/graph\/display\/refine$/.test(new URL(request.url()).pathname)) requests.push(request.url());},
    settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))),
    quiet = async () => {await expect.poll(async () => {const state = await read();
      return state.busy || ['queued', 'running', 'cancel-requested', 'loading'].includes(state.lifecycle.state);
    }, {timeout: 60000}).toBe(false); await settle();},
    readyQuality = async value => {
      await expect.poll(async () => {const state = await read(); return !state.busy && bodyMesh(state)?.quality;}, {timeout: 60000}).toBe(value);
      const state = await read();
      if (value !== 'draft') {
        assert.equal(state.cached.quality, value);
        assert.deepEqual(bodyMesh(state).positions, state.cached.positions);
        assert.deepEqual(bodyMesh(state).indices, state.cached.indices, 'Render the returned refinement, rather than the accepted semantic mesh');
        const edges = state.rendered.filter(item => item.group === 0 && item.feature === 'flange-body' && item.edge);
        assert.deepEqual(edges.map(item => item.positions), state.cached.edges.map(item => item.positions));
        assertBoundaryRings(state);
      }
      return state;
    },
    newProject = async () => {
      await page.getByRole('button', {name: 'Project toolbar options', exact: true}).click();
      await page.getByRole('menuitem', {name: 'New project', exact: true}).click();
      await expect.poll(async () => (await graph()).recipe.nodes.length).toBe(0);
    },
    exportBody = async () => {
      await page.getByRole('button', {name: 'Export CAD…', exact: true}).click();
      await page.locator('#export-scope').selectOption('body');
      await page.locator('#export-target').selectOption('flange-body');
      await page.locator('#export-units').selectOption('Millimeters');
      await page.locator('#export-mesh').uncheck();
      const downloading = page.waitForEvent('download'); await page.locator('#export-download').click();
      const chunks = []; for await (const chunk of await (await downloading).createReadStream()) chunks.push(chunk);
      const result = await inspectExport(Buffer.concat(chunks));
      closeNumber(result.volume, 43.935 * Math.PI);
      assert.equal(result.faces, 5); assert.equal(result.solids, 1);
      for (const [row, values] of [[0, [-5, -5, -2]], [1, [5, 5, 0]]])
        values.forEach((value, axis) => closeNumber(result.bounds[row][axis], value));
      return result;
    };
  await expect(page.locator('#viewport canvas')).toBeVisible(); await expect(page.locator('#load')).toBeEnabled();
  const display = page.locator('#viewport .display-options'), originalDisplayOpen = await display.evaluate(element => element.open);
  if (!originalDisplayOpen) await display.locator('summary').click();
  const original = structuredClone((await graph()).recipe), originalAuto = await auto.isChecked(),
    originalQuality = await quality.inputValue(), originalFaces = await faces.isChecked(),
    originalArchive = original.nodes.length ? await download(page, 'Save project') : null;
  assert.equal(originalQuality, 'standard', 'The shipped default CAD display quality is Standard');
  page.on('request', record);
  try {
    await auto.uncheck(); await quality.selectOption('draft'); await quiet(); await newProject();
    await page.locator('#scan-file').setInputFiles({name: fixture.filename, mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.raw_base64, 'base64')});
    await expect(page.locator('#source-progress')).toBeHidden();
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.operation === 'source')).toBe(true);
    const recipe = structuredClone(fixture.recipe), source = recipe.nodes.find(node => node.operation === 'source'), oldSource = source.id;
    Object.assign(source, (await graph()).recipe.nodes.find(node => node.operation === 'source'));
    for (const node of recipe.nodes) if (node.source === oldSource) node.source = source.id;
    await page.locator('#file').setInputFiles({name: 'flange-actions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipe))});
    await expect.poll(async () => (await graph()).recipe.nodes.some(node => node.id === 'flange-body')).toBe(true);
    await page.getByRole('button', {name: 'Evaluate all', exact: true}).click();
    await expect.poll(async () => {const state = await graph(); assert.deepEqual(state.errors, {}); return state.states['flange-body'];}, {timeout: 60000}).toBe('ready');
    await quality.selectOption('standard'); await faces.check(); await readyQuality('standard');
    await quality.selectOption('draft'); await readyQuality('draft');
    const draft = await read(), baseline = accepted(draft.graph), beforeExport = await exportBody();
    report.phase = 'standard-fine-render-and-cache';
    await quality.selectOption('standard'); const standard = await readyQuality('standard');
    await quality.selectOption('draft'); await readyQuality('draft');
    await quality.selectOption('standard'); await readyQuality('standard');
    const cacheRequests = requests.length;
    await quality.selectOption('draft'); await readyQuality('draft');
    await quality.selectOption('standard'); await readyQuality('standard');
    assert.equal(requests.length, cacheRequests, 'Switching back to a cached quality must not remesh');
    if (gate) {
      report.phase = 'native-cancel-at-real-meshing-boundary';
      await gate.arm(); await quality.selectOption('fine'); await gate.wait();
      await expect(page.locator('#cancel-cad-display')).toBeEnabled();
      await page.locator('#cancel-cad-display').click();
      const cancelledAt = requests.length;
      await gate.release(); await quiet();
      assert.equal(requests.length, cancelledAt, 'Cancelling a multi-feature display queue cannot start another refinement');
      assert.equal(bodyMesh(await read()).quality, 'standard', 'Cancellation retains the completed standard display');
      await expect(page.locator('#cad-display-status')).toContainText(/cancel/i);
      await page.locator('#retry-cad-display').click();
    } else await quality.selectOption('fine');
    const fine = await readyQuality('fine');
    assert(triangleCount(fine) > triangleCount(standard), 'Fine quality must improve actual curved-surface tessellation');
    assert(triangleCount(standard) > triangleCount(draft), 'Standard quality must improve the coarse flange display');
    assert(fine.bytes <= 64 * 1024 * 1024 && fine.bytes > 0);
    assert.deepEqual(accepted((await read()).graph), baseline, 'Display refinement cannot publish accepted graph geometry');
    report.phase = 'refined-holes-silhouette-and-picking';
    await page.getByRole('button', {name: 'Top', exact: true}).click(); await settle();
    for (const [coordinates, hit] of [[[-2, 0, 0], false], [[2, 0, 0], false], [[0, 2, 0], true], [[5.8, 0, 0], false]]) {
      const point = await page.evaluate(coordinates => globalThis.__cadDisplayUi.point(coordinates), coordinates);
      assert.equal(point.features.includes('flange-body'), hit, 'Raycast the rendered refined solid, including its open through-holes');
    }
    await page.getByRole('button', {name: 'Export CAD…', exact: true}).click();
    await page.locator('#export-scope').selectOption('body'); await page.locator('#export-target-field .model-field-pick').click();
    await expect.poll(async () => (await read()).mode).toBeTruthy();
    const pick = await page.evaluate(() => globalThis.__cadDisplayUi.point([0, 2, 0]));
    await page.mouse.click(pick.x, pick.y);
    await expect.poll(async () => (await read()).mode).toBeFalsy();
    await expect(page.locator('#export-dialog')).toBeVisible(); await expect(page.locator('#export-target')).toHaveValue('flange-body');
    await page.locator('#export-dialog [data-close-dialog]').click();
    await quiet();
    await page.getByRole('button', {name: 'Side', exact: true}).click(); await settle();
    await page.getByRole('button', {name: 'Build faces…', exact: true}).click();
    await page.locator('#build-faces-target').click();
    let sourcePick;
    for (const coordinates of [[0, -4.85, -1], [2, -Math.sqrt(4.85 ** 2 - 4), -1], [-2, -Math.sqrt(4.85 ** 2 - 4), -1]]) {
      const point = await page.evaluate(coordinates => globalThis.__cadDisplayUi.point(coordinates), coordinates);
      if (point.element === 'CANVAS' && point.sourceFaces[0]?.includes('joint-cylinder-wall')) { sourcePick = point; break; }
    }
    assert(sourcePick, 'The tapered wall must have an unobstructed canvas point');
    const picking = await read();
    assert(sourcePick.sourceFaces[0].includes('joint-cylinder-wall'), 'The frontmost body triangle retains its exact source-face identity');
    assert(picking.sourceProxies.some(proxy => proxy.faces.includes('joint-cylinder-wall') && proxy.sharedVertices && proxy.indices.length),
      'Source-face picking must share the actual refined body vertices');
    await page.mouse.click(sourcePick.x, sourcePick.y);
    const chooser = page.locator('#model-pick-choices');
    if (await chooser.isVisible()) {
      const cone = chooser.getByRole('button').filter({hasText: 'Tapered outer surface'});
      await expect(cone).toHaveCount(1); await cone.click();
    }
    await expect.poll(async () => (await read()).faceTarget?.feature).toBe('joint-cylinder');
    await page.locator('#close-edit').click(); await quiet();
    report.phase = 'unchanged-step-export';
    const afterExport = await exportBody();
    assert.deepEqual(afterExport, beforeExport, 'Actual STEP topology, dimensions, volume and export metadata remain unchanged');
    assert.deepEqual(accepted((await read()).graph), baseline);
    if (gate) {
      report.phase = 'global-cancel-and-late-quality-reply';
      await quality.selectOption('draft'); await readyQuality('draft');
      await page.locator('.action-select[data-action-id="flange-body"]').click({button: 'right'});
      await page.locator('#feature-context-rename').click();
      await page.locator('#rename-feature-label').fill('Renamed flange cache context');
      await page.locator('#rename-feature-form').getByRole('button', {name: 'Rename', exact: true}).click();
      await expect.poll(async () => (await graph()).recipe.nodes.find(node => node.id === 'flange-body').label).toBe('Renamed flange cache context');
      const renamed = accepted(await graph());
      await gate.arm(); await quality.selectOption('standard'); await gate.wait();
      await expect(page.locator('#cancel-computation')).toBeEnabled(); await page.locator('#cancel-computation').click();
      const stoppedAt = requests.length;
      await gate.release(); await quiet();
      assert.equal(requests.length, stoppedAt, 'Global Cancel must pause the complete display queue until explicit Retry');
      assert.deepEqual(accepted((await read()).graph), renamed);
      await gate.arm(); await quality.selectOption('fine'); await gate.wait();
      await quality.selectOption('draft'); await gate.release(); await quiet();
      assert.equal(bodyMesh(await read()).quality, 'draft', 'A late refinement cannot paint after a different quality was selected');
      assert.deepEqual(accepted((await read()).graph), renamed);
    }
    Object.assign(report, {complete: true, phase: 'complete', draft_triangles: triangleCount(draft), standard_triangles: triangleCount(standard),
      fine_triangles: triangleCount(fine), rendered_buffers_match: true, cap_and_hole_edges: true, cache_reused: true,
      default_standard_on_faces: true,
      through_holes_pickable: true, refined_body_picked: true, accepted_graph_unchanged: true,
      refined_source_face_picked: true,
      step_faces: afterExport.faces, step_solids: afterExport.solids, step_dimensions_unchanged: true, step_volume_unchanged: true});
  } catch (error) {error.message = `CAD display quality (${report.phase}): ${error.message}`; throw error;}
  finally {
    page.off('request', record);
    if (gate) await gate.release();
    if (await page.locator('#export-dialog').isVisible()) await page.locator('#export-dialog [data-close-dialog]').click();
    await quality.selectOption('draft'); await quiet();
    await auto.uncheck();
    if (originalArchive) {
      const generation = (await read()).generation;
      await page.locator('#project-file').setInputFiles({name: 'restored-before-cad.scansor', mimeType: 'application/zip', buffer: originalArchive});
      await expect.poll(async () => (await read()).generation).toBeGreaterThan(generation);
    } else await newProject();
    assert.deepEqual((await graph()).recipe, original);
    await faces.setChecked(originalFaces); await quality.selectOption(originalQuality); await auto.setChecked(originalAuto);
    if (!originalDisplayOpen) await display.locator('summary').click();
  }
  return report;
}
