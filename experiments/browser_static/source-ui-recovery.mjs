// Real shipped app/bootstrap and their singleton worker backend: read-only
// observers for source cases, plus a separately disclosed legacy API-shape fault.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {sha256} from './server.mjs';
import {sourceEvidenceSuffix,readSourceEvidence} from './source-import-harness.mjs';

const appSuffix=`
globalThis.__sourceUiDisplay242=()=>!positions||!mesh?.geometry?.index?null:({generation:workspaceGeneration,metadata,graph:graphState,
 positions:[...positions],indices:[...mesh.geometry.index.array],
 mesh_positions:[...mesh.geometry.getAttribute('position').array],
 ready:$('status').dataset.runtimeReady==='true',inert:document.querySelector('main').inert,
 transition_busy:sourceTransitionBusy,evaluation_running:!!activeGraphEvaluation});
`;
const workerSuffix=sourceEvidenceSuffix+`
globalThis.__sourceUiWorkerDisplay242=()=>JSON.parse(python.runPython("json.dumps({'positions':browser_workspace.display_positions.tolist(),'indices':browser_workspace.display_indices.tolist()},allow_nan=False)"));
`;
const backend = (page,method,payload) => page.evaluate(async({method,payload})=>{
  const {getBackend}=await import(new URL('backend.js',document.baseURI).href);
  globalThis.__sourceUiBackend242=getBackend();
  return globalThis.__sourceUiBackend242[method](payload);
},{method,payload});
const display = page => page.evaluate(()=>globalThis.__sourceUiDisplay242());
const ready = page => page.waitForFunction(()=>{const d=globalThis.__sourceUiDisplay242?.();
  return d?.ready&&!d.inert&&!d.transition_busy&&!d.evaluation_running;},null,{timeout:180000});
const command = async(page,name)=>{
  await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name,exact:true}).click();
};
function sameDisplayedSource(actual,expected) {
  assert.equal(actual.generation,expected.generation);
  assert.deepEqual(actual.metadata.source,expected.metadata.source);
  assert.deepEqual(actual.graph.recipe,expected.graph.recipe);
  assert.deepEqual(actual.positions,expected.positions);assert.deepEqual(actual.indices,expected.indices);
  assert.deepEqual(actual.mesh_positions,expected.mesh_positions);
  assert.equal(actual.metadata.vertices,expected.metadata.vertices);assert.equal(actual.metadata.triangles,expected.metadata.triangles);
  assert.equal(actual.metadata.project_generation,expected.metadata.project_generation);
}
async function coherent(page,isolation) {
  const shown=await display(page),bundle=await backend(page,'loadWorkspace');
  assert.equal(shown.generation,bundle.generation);assert.equal(shown.metadata.project_generation,shown.generation);
  assert.equal(shown.graph.project_generation,shown.generation);assert.deepEqual(shown.graph.recipe,bundle.graph.recipe);
  assert.deepEqual(shown.metadata.source,bundle.metadata.source);
  assert.equal(await page.evaluate(()=>crossOriginIsolated),isolation);
  assert.equal(await page.workers()[0].evaluate(()=>crossOriginIsolated),isolation);
  const actual=await page.workers()[0].evaluate(()=>globalThis.__sourceUiWorkerDisplay242());
  assert.deepEqual(shown.positions,actual.positions.flat());assert.deepEqual(shown.mesh_positions,shown.positions);
  assert.deepEqual(shown.indices,actual.indices.flat());
  assert.equal(shown.metadata.vertices,shown.positions.length/3);assert.equal(shown.metadata.triangles,shown.indices.length/3);
  assert.equal(await page.locator('#mesh-info').textContent(),`${shown.metadata.vertices.toLocaleString()} vertices · ${shown.metadata.triangles.toLocaleString()} triangles`);
  return shown;
}

export async function validateSourceUiRecovery({browser,site,preview,prefix,sources,isolation,report}) {
  const result={instrumentation:{app_suffix_sha256:sha256(Buffer.from(appSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix)),
    description:'Exact verified shipped module bodies with appended read-only display observers; real DOM commands, singleton backend and native Worker; in-memory transport holds only.'},cases:[]};
  report.ui_recovery=result;
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);
    if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.api_requests.push(url);});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    const suffix=url.pathname.endsWith('/app.js')?appSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
    if(!suffix)return route.continue();
    assert.equal(url.origin,preview.origin);const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);
    assert.ok(record,'Instrumented UI module must be listed');const bytes=await readFile(path.join(site.root,relative));
    assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(suffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  const page=await context.newPage();page.on('pageerror',error=>report.page_errors.push(String(error)));
  let declineNext=false;
  page.on('dialog',dialog=>{if(declineNext){declineNext=false;return dialog.dismiss();}return dialog.accept();});
  await page.addInitScript(()=>{
    const NativeWorker=window.Worker;
    const h=window.__sourceUiTransport242={workers:0,target:0,kind:null,held:[],files:[],operations:new Map(),heldRequest:null,completion:false,
      release(){for(const deliver of this.held.splice(0))deliver();this.heldRequest=null;}};
    window.Worker=class extends NativeWorker {
      constructor(...args){super(...args);this.index=++h.workers;}
      postMessage(message,...args){
        h.operations.set(message.id,message.operation);
        if(message.operation==='prepare-source'&&message.payload.file)h.files.push({worker:this.index,file:message.payload.file,recovery:!!message.payload.recovery});
        if(this.index===h.target&&h.kind==='authorization'&&message.operation==='accept-source-boundary'&&message.accepted){h.held.push(()=>super.postMessage(message,...args));return;}
        if(message.operation==='cancel')h.completion=false;
        return super.postMessage(message,...args);
      }
      set onmessage(callback){super.onmessage=event=>{
        const data=event.data,own=this.index===h.target;
        const candidate=own&&(data.kind===h.kind||(h.heldRequest===data.id&&data.kind==='transfer-confirmed'));
        const completion=own&&h.completion&&((['evaluateGraph','ensureGraph'].includes(h.operations.get(data.id))&&data.ok!==undefined)
          || data.kind==='graph'&&!data.state.evaluation_running || data.kind==='lifecycle'&&data.state==='complete');
        if(candidate||completion){if(candidate)h.heldRequest=data.id;h.held.push(()=>callback?.(event));return;}
        callback?.(event);
      };}
    };
  });
  const arm=kind=>page.evaluate(kind=>{const h=window.__sourceUiTransport242;h.target=h.workers;h.kind=kind;h.heldRequest=null;},kind);
  const release=()=>page.evaluate(()=>{const h=window.__sourceUiTransport242;h.kind=null;h.release();});
  const held=()=>page.waitForFunction(()=>window.__sourceUiTransport242.held.length>0,null,{timeout:60000});
  const force=async()=>{const workers=await page.evaluate(()=>window.__sourceUiTransport242.workers);
    await page.locator('#force-restart').click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('main').evaluate(element=>element.inert),true,'Runtime lock must outlive pending source cleanup');
    await page.waitForFunction(before=>window.__sourceUiTransport242.workers>before
      &&window.__sourceUiBackend242.getLifecycle().state==='complete',workers,{timeout:180000});};
  const chooseScene=async()=>{const waiting=page.waitForEvent('filechooser');await page.locator('[data-add-scan]').click();
    const chooser=await waiting;assert.equal(await chooser.element().getAttribute('id'),'scan-file');
    await chooser.setFiles(path.join(sources,'horizontal-scene.ply'));};
  const importScene=async()=>{await chooseScene();
    await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().metadata.source?.source_sha256==='9666be32b5afe9df2b34a496c455339687b53c786fd0429700d0668ef0c586e1'&&!document.querySelector('main').inert,null,{timeout:120000});};
  const sceneOracle=JSON.parse(await readFile(path.join(sources,'horizontal-scene.expected.json'),'utf8'));
  const inspectScene=async shown=>{
    assert.equal(shown.metadata.source.source_sha256,'9666be32b5afe9df2b34a496c455339687b53c786fd0429700d0668ef0c586e1');
    assert.equal(shown.metadata.vertices,sceneOracle.positions.length);assert.equal(shown.metadata.triangles,sceneOracle.triangles.length);
    assert.deepEqual(shown.positions,sceneOracle.positions.flat().map(Math.fround));assert.deepEqual(shown.indices,sceneOracle.triangles.flat());
    await command(page,'Inspect source');
    const text=await page.locator('#source-details').textContent();
    for(const value of [shown.metadata.source.source_sha256,'horizontal-scene.ply',shown.metadata.vertices.toLocaleString(),shown.metadata.triangles.toLocaleString()])assert.ok(text.includes(value));
    assert.equal(await page.locator('#source-properties').isVisible(),true);
  };
  try {
    result.phase='startup';
    await page.goto(preview.origin+prefix+'app/');await ready(page);await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
    await command(page,'New project');await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().graph.recipe.nodes.length===0,null,{timeout:60000});
    await importScene();const accepted=await coherent(page,isolation);await inspectScene(accepted);
    await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#new-point-label').fill('Keep source recovery draft');
    await page.locator('#new-point-label').evaluate(input=>{window.__sourceUiDraft242=input;});
    result.phase='before-host-adoption';await arm('source-candidate');await command(page,'New project');await held();await force();await ready(page);
    sameDisplayedSource(await coherent(page,isolation),accepted);
    assert.equal(await page.locator('#new-point-label').evaluate(input=>input===window.__sourceUiDraft242&&input.value==='Keep source recovery draft'),true);
    await release();sameDisplayedSource(await display(page),accepted);
    result.cases.push({position:'before-host-adoption',old_source_and_live_draft_preserved:true,late_replies_ignored:true});
    await page.getByRole('button',{name:'Cancel',exact:true}).filter({visible:true}).click();
    await command(page,'New project');await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().graph.recipe.nodes.length===0,null,{timeout:60000});
    for(const position of ['authorization','source-promoted']) {
      result.phase=position;
      const before=await display(page);await arm(position);
      await chooseScene();await held();
      const originalFile=await page.evaluate(()=>window.__sourceUiTransport242.files.length-1);
      await force();await page.waitForFunction(()=>!document.querySelector('#source-unavailable').hidden,null,{timeout:60000});
      const fenced=await display(page);sameDisplayedSource(fenced,before);assert.equal(fenced.inert,true);assert.equal(fenced.ready,false);
      const current=await backend(page,'getGraph');assert.notEqual(current.project_generation,before.generation);
      assert.equal(await page.evaluate(index=>{const h=window.__sourceUiTransport242;return h.files.some(item=>item.recovery&&item.file===h.files[index].file);},originalFile),true);
      const download=page.waitForEvent('download');await page.locator('#download-prior-actions').click();
      const saved=await download;assert.deepEqual(JSON.parse(await readFile(await saved.path(),'utf8')),before.graph.recipe);
      await release();sameDisplayedSource(await display(page),before);
      await page.locator('#retry-source-workspace').click();await ready(page);const installed=await coherent(page,isolation);
      assert.equal(installed.generation,current.project_generation);assert.equal(installed.inert,false);
      assert.equal(await page.locator('#source-unavailable').isHidden(),true);
      await inspectScene(installed);
      result.cases.push({position,accepted_file_recovered:true,old_display_fenced_until_retry:true,actual_prior_actions_preserved:true,full_bundle_retry:true});
      if(position==='authorization'){await command(page,'New project');await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().graph.recipe.nodes.length===0,null,{timeout:60000});}
    }
    result.phase='accepted-reset-live-draft';
    await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#new-point-label').fill('Keep accepted reset draft');
    await page.locator('#new-point-label').evaluate(input=>{window.__sourceUiResetDraft242=input;});
    const beforeReset=await coherent(page,isolation);await arm('source-promoted');await command(page,'New project');await held();await force();
    await page.waitForFunction(()=>!document.querySelector('#source-unavailable').hidden,null,{timeout:60000});
    sameDisplayedSource(await display(page),beforeReset);
    const assertResetDraft=async()=>assert.equal(await page.locator('#new-point-label').evaluate(input=>input===window.__sourceUiResetDraft242&&input.value==='Keep accepted reset draft'),true);
    await assertResetDraft();const emptyAccepted=await backend(page,'getGraph');assert.deepEqual(emptyAccepted.recipe.nodes,[]);
    const resetDownload=page.waitForEvent('download');await page.locator('#download-prior-actions').click();
    assert.deepEqual(JSON.parse(await readFile(await (await resetDownload).path(),'utf8')),beforeReset.graph.recipe);
    await release();declineNext=true;await page.locator('#retry-source-workspace').click();
    await assertResetDraft();sameDisplayedSource(await display(page),beforeReset);assert.equal(await page.locator('main').evaluate(element=>element.inert),true);
    await page.locator('#retry-source-workspace').click();await ready(page);
    const emptyShown=await coherent(page,isolation);assert.equal(emptyShown.generation,emptyAccepted.project_generation);
    assert.equal(emptyShown.metadata.vertices,0);assert.equal(emptyShown.metadata.triangles,0);assert.equal(await page.locator('#point-dialog').isVisible(),false);
    result.cases.push({position:'accepted-reset-live-draft',prior_actions_preserved:true,draft_retained_until_fresh_retry_proof:true,declined_retry_keeps_fence:true,accepted_retry_closes_draft:true});
    await importScene();await inspectScene(await coherent(page,isolation));
    result.phase='visible-acceptance';
    const visible=await coherent(page,isolation);await force();await ready(page);sameDisplayedSource(await coherent(page,isolation),visible);
    result.cases.push({position:'visible-acceptance',same_source_force_coherent:true});
    await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#new-point-label').fill('Cancel source remains immutable');
    await page.getByRole('button',{name:'Add point',exact:true}).click();
    await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().graph.recipe.nodes.some(node=>node.operation==='point'),null,{timeout:60000});
    const beforeCancel=await coherent(page,isolation);await arm(null);result.phase='same-source-cancel';
    await page.evaluate(()=>{window.__sourceUiTransport242.completion=true;});
    await page.getByRole('button',{name:'Evaluate all',exact:true}).click();await held();
    await page.locator('#cancel-computation').click();await page.waitForFunction(()=>window.__sourceUiBackend242.getLifecycle().state==='cancelled',null,{timeout:120000});
    await ready(page);await release();sameDisplayedSource(await coherent(page,isolation),beforeCancel);
    assert.equal(await page.getByRole('checkbox',{name:'Auto',exact:true}).isChecked(),false);
    const cancelledGraph=await backend(page,'getGraph');
    const point=cancelledGraph.recipe.nodes.find(node=>node.operation==='point');assert.ok(point);
    assert.equal(cancelledGraph.states[point.id],'unevaluated');assert.equal(cancelledGraph.results[point.id],undefined);
    await page.getByRole('button',{name:'Evaluate all',exact:true}).click({trial:true});
    await page.getByRole('button',{name:'Point',exact:true}).click();assert.equal(await page.locator('#point-dialog').isVisible(),true);
    await page.getByRole('button',{name:'Cancel',exact:true}).filter({visible:true}).click();
    result.cases.push({position:'same-source-cancel',source_epoch_arrays_and_recipe_preserved:true});
    const final=await readSourceEvidence(page,{arrays:false});assert.equal(final.owned_fs_bytes,0);
    assert.equal(await page.locator('#viewport canvas').count(),1);
    result.complete=true;result.phase='complete';
  } finally {
    if(!result.complete)result.failure_snapshot=await page.evaluate(()=>({display:globalThis.__sourceUiDisplay242?.(),
      lifecycle:globalThis.__sourceUiBackend242?.getLifecycle(),runtime_detail:document.querySelector('#runtime-detail')?.textContent,
      status:document.querySelector('#status')?.textContent,unavailable:!document.querySelector('#source-unavailable')?.hidden,
      held:window.__sourceUiTransport242?.held.length})).catch(error=>({error:String(error)}));
    await context.close();
  }
  result.complete=false;result.phase='legacy-shaped-startup';
  await validateLegacySourceStartup({browser,site,preview,prefix,isolation,report});
  result.complete=true;result.phase='complete';
  return result;
}

// Explicit API-shape fault injection, separate from the read-only observers.
// The modern production coordinator still verifies and transfers its full bundle;
// only the app-facing public method's first returned graph is omitted.
const legacyShapeSuffix=`
{
 const originalLoadWorkspace242=backend.loadWorkspace.bind(backend);
 let omitGraph242=true;
 globalThis.__legacySourceBackend242=backend;
 backend.loadWorkspace=async(...args)=>{
  const bundle=await originalLoadWorkspace242(...args);
  if(!omitGraph242)return bundle;
  omitGraph242=false;globalThis.__legacySourceGraphOmitted242=true;
  const {graph,...legacyBundle}=bundle;return legacyBundle;
 };
}
`;

export async function validateLegacySourceStartup({browser,site,preview,prefix,isolation,report}) {
  const result=report.ui_legacy_startup={complete:false,instrumentation:{
    description:'Actual shipped app and singleton modern worker with one disclosed legacy-shaped public loadWorkspace response (graph omitted), held actual getGraph reply, and read-only display observers; not an old worker binary.',
    app_suffix_sha256:sha256(Buffer.from(appSuffix+legacyShapeSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix))}};
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);
    if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.api_requests.push(url);});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    const suffix=url.pathname.endsWith('/app.js')?appSuffix+legacyShapeSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
    if(!suffix)return route.continue();
    assert.equal(url.origin,preview.origin);const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);
    assert.ok(record);const bytes=await readFile(path.join(site.root,relative));
    assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(suffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  const page=await context.newPage();page.on('pageerror',error=>report.page_errors.push(String(error)));page.on('dialog',dialog=>dialog.accept());
  await page.addInitScript(()=>{
    const NativeWorker=window.Worker,h=window.__legacySourceTransport242={workers:0,operations:new Map(),held:[],captured:false};
    window.Worker=class extends NativeWorker {
      constructor(...args){super(...args);this.index=++h.workers;}
      postMessage(message,...args){h.operations.set(message.id,message.operation);return super.postMessage(message,...args);}
      set onmessage(callback){super.onmessage=event=>{
        if(this.index===1&&!h.captured&&h.operations.get(event.data.id)==='getGraph'&&event.data.ok===true){
          h.captured=true;h.held.push(()=>callback?.(event));return;
        }
        callback?.(event);
      };}
    };
  });
  try {
    await page.goto(preview.origin+prefix+'app/');
    await page.waitForFunction(()=>window.__legacySourceTransport242.held.length===1,null,{timeout:180000});
    assert.equal(await page.evaluate(()=>globalThis.__legacySourceGraphOmitted242),true);
    result.canvases_before_force=await page.locator('#viewport canvas').count();
    await page.locator('#force-restart').click();
    await page.waitForFunction(()=>window.__legacySourceTransport242.workers>1
      &&window.__legacySourceBackend242.getLifecycle().state==='complete',null,{timeout:180000});
    await page.waitForFunction(()=>globalThis.__sourceUiDisplay242?.()?.ready||!document.querySelector('#startup-error').hidden,null,{timeout:180000});
    assert.equal(await page.locator('#startup-error').isHidden(),true,await page.locator('#startup-detail').textContent());
    await ready(page);await coherent(page,isolation);
    assert.equal(result.canvases_before_force,0,'Legacy graph fallback must settle before allocating display resources');
    assert.equal(await page.locator('#viewport canvas').count(),1);assert.equal(await page.locator('#empty-project').count(),1);
    await command(page,'Inspect source');assert.equal(await page.locator('#source-properties').isVisible(),true);
    const beforeGraph=await backend(page,'getGraph');
    await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#new-point-label').fill('Legacy startup command remains reachable');
    await page.getByRole('button',{name:'Add point',exact:true}).click();
    await page.waitForFunction(()=>globalThis.__sourceUiDisplay242().graph.recipe.nodes.some(node=>node.label==='Legacy startup command remains reachable'),null,{timeout:60000});
    const accepted=await coherent(page,isolation);
    assert.equal(accepted.graph.recipe.nodes.length,beforeGraph.recipe.nodes.length+1);
    assert.equal(accepted.graph.recipe.nodes.filter(node=>node.label==='Legacy startup command remains reachable').length,1);
    await page.evaluate(()=>{for(const deliver of window.__legacySourceTransport242.held.splice(0))deliver();});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    sameDisplayedSource(await coherent(page,isolation),accepted);
    result.one_renderer_and_controller=true;result.real_commands_reachable=true;result.late_original_graph_ignored=true;result.complete=true;
  } finally {
    if(!result.complete)result.failure_snapshot=await page.evaluate(()=>({display:globalThis.__sourceUiDisplay242?.(),
      lifecycle:globalThis.__legacySourceBackend242?.getLifecycle(),startup_error:!document.querySelector('#startup-error')?.hidden,
      startup_detail:document.querySelector('#startup-detail')?.textContent,canvases:document.querySelectorAll('#viewport canvas').length})).catch(error=>({error:String(error)}));
    await context.close();
  }
  return result;
}
