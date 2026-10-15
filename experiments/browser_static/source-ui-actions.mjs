// Actual shipped UI actions opening. Observers never supply source authority,
// alter a recipe, or replace a production operation with a test implementation.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {sha256} from './server.mjs';
import {sourceEvidenceSuffix,readSourceEvidence,runSourcePython} from './source-import-harness.mjs';
import {assertAcceptedSourceGraph} from './source-semantic.mjs';

const appSuffix=`
globalThis.__actionsUiDisplay244=()=>!positions||!mesh?.geometry?.index?null:({generation:workspaceGeneration,metadata,graph:graphState,
 positions:[...positions],indices:[...mesh.geometry.index.array],mesh_positions:[...mesh.geometry.getAttribute('position').array],
 ready:$('status').dataset.runtimeReady==='true',inert:document.querySelector('main').inert,
 transition_busy:sourceTransitionBusy,evaluation_running:!!activeGraphEvaluation});
globalThis.__actionsUiBackend244=backend;
`;
const workerSuffix=sourceEvidenceSuffix;
const shown=page=>page.evaluate(()=>globalThis.__actionsUiDisplay244());
const call=(page,method,payload)=>page.evaluate(async({method,payload})=>globalThis.__actionsUiBackend244[method](payload),{method,payload});
const ready=page=>page.waitForFunction(()=>{const d=globalThis.__actionsUiDisplay244?.();
  return d?.ready&&!d.inert&&!d.transition_busy&&!d.evaluation_running;},null,{timeout:180000});
const visible=async locator=>assert.equal(await locator.isVisible(),true);
async function command(page,name){
  await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name,exact:true}).click();
}
function sameSource(actual,reference,{recipe=true,generation=true}={}){
  if(generation)assert.equal(actual.generation,reference.generation);
  assert.deepEqual(actual.metadata.source,reference.metadata.source);
  if(recipe)assert.deepEqual(actual.graph.recipe,reference.graph.recipe);
  for(const key of ['positions','indices','mesh_positions'])assert.deepEqual(actual[key],reference[key],key);
}
async function saveActions(page){
  const waiting=page.waitForEvent('download');
  await page.getByRole('button',{name:'Save actions',exact:true}).click();
  const downloaded=await waiting,bytes=await readFile(await downloaded.path());
  return {recipe:JSON.parse(bytes),bytes,name:downloaded.suggestedFilename()};
}
async function pickActions(page,recipe){
  const waiting=page.waitForEvent('filechooser');
  await page.getByRole('button',{name:'Load actions',exact:true}).click();
  const chooser=await waiting;assert.equal(await chooser.element().getAttribute('id'),'file');
  await chooser.setFiles({name:'saved-actions.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(recipe))});
}
async function pickLocated(page,filename,bytes){
  const waiting=page.waitForEvent('filechooser');await page.locator('#choose-located-source').click();
  const chooser=await waiting;assert.equal(await chooser.element().getAttribute('id'),'locate-source-file');
  await chooser.setFiles({name:filename,mimeType:'application/octet-stream',buffer:bytes});
}
async function draft(page,docked,label){
  const tab=page.getByRole('tab',{name:'Edit',exact:true});
  // Closing an already completed workflow retains the optional Edit tab. Close
  // that empty production tab before requesting a popup; never toggle its mode
  // by clicking a hidden Dock button or mutating the layout model.
  if(!docked&&await tab.count()){
    await tab.getByTitle('Close',{exact:true}).click();await tab.waitFor({state:'detached'});
  }
  await page.getByRole('button',{name:'Point',exact:true}).click();
  await page.locator('#point-dialog').waitFor({state:'visible'});await page.locator('#new-point-label').fill(label);
  await page.locator('#new-point-label').evaluate((input,docked)=>{window.__actionsDraft244=input;window.__actionsDraftDocked244=docked;},docked);
  if(docked){
    if(await page.locator('#edit-popup').isVisible())await page.locator('#dock-edit').click();
    await tab.waitFor({state:'visible'});await page.locator('#edit-popup').waitFor({state:'hidden'});
  }else await page.locator('#edit-popup').waitFor({state:'visible'});
  await assertDraft(page,label);
}
async function assertDraft(page,label){
  await visible(page.locator('#point-dialog'));
  assert.equal(await page.locator('#new-point-label').evaluate((input,label)=>input===window.__actionsDraft244&&input.value===label
    &&!!input.closest('#edit-popup')===!window.__actionsDraftDocked244,label),true);
  const docked=await page.evaluate(()=>window.__actionsDraftDocked244);
  assert.equal(await page.locator('#edit-popup').isVisible(),!docked);
  assert.equal(await page.getByRole('tab',{name:'Edit',exact:true}).count(),docked?1:0);
}
async function closeDraft(page){await page.getByRole('button',{name:'Cancel',exact:true}).filter({visible:true}).click();}

export async function validateSourceUiActions({browser,site,preview,prefix,sources,isolation,report,expected,output,nativePython}){
  const result=report.ui_actions={complete:false,phase:'startup',cases:[],observations:{},instrumentation:{
    app_suffix_sha256:sha256(Buffer.from(appSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix)),
    description:'Exact manifest-verified shipped app/worker plus appended read-only display/source observers; native Worker message observation and selected transport holds, no source/recipe/operation authority overrides.'}};
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);
    if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.api_requests.push(url);});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url()),suffix=url.pathname.endsWith('/app.js')?appSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
    if(!suffix)return route.continue();
    assert.equal(url.origin,preview.origin);const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);
    assert.ok(record,'Observed module must be listed in the verified manifest');
    const bytes=await readFile(path.join(site.root,relative));assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(suffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  const page=await context.newPage();page.setDefaultTimeout(60000);
  page.on('pageerror',error=>report.page_errors.push(String(error)));
  let declineNext=false;
  page.on('dialog',async dialog=>{assert.equal(dialog.type(),'confirm');
    if(declineNext){declineNext=false;await dialog.dismiss();}else await dialog.accept();});
  await page.addInitScript(()=>{
    const NativeWorker=window.Worker,h=window.__actionsTransport244={workers:0,target:0,kind:null,held:[],heldRequest:null,
      operations:new Map(),files:[],prepared:[],graphs:[],
      release(){for(const deliver of this.held.splice(0))deliver();this.heldRequest=null;}};
    window.Worker=class extends NativeWorker{
      constructor(...args){super(...args);this.index=++h.workers;}
      postMessage(message,...args){
        h.operations.set(message.id,message.operation);
        if(message.operation==='prepare-source'){
          h.prepared.push({worker:this.index,purpose:message.payload.purpose||'source',kind:message.payload.kind,
            file:!!message.payload.file,recovery:!!message.payload.recovery});
          if(message.payload.file)h.files.push({worker:this.index,file:message.payload.file,recovery:!!message.payload.recovery});
        }
        return super.postMessage(message,...args);
      }
      set onmessage(callback){super.onmessage=event=>{
        const data=event.data;
        if(data.kind==='graph'&&data.state?.recipe)h.graphs.push(structuredClone(data.state));
        const own=this.index===h.target;
        if(own&&(data.kind===h.kind||(h.heldRequest===data.id&&data.kind==='transfer-confirmed')
          ||h.kind==='inspection'&&h.operations.get(data.id)==='inspect-actions'&&data.ok!==undefined)){
          h.heldRequest=data.id;h.held.push(()=>callback?.(event));return;
        }
        callback?.(event);
      };}
    };
  });
  const arm=kind=>page.evaluate(kind=>{const h=window.__actionsTransport244;h.target=h.workers;h.kind=kind;h.heldRequest=null;},kind);
  const release=()=>page.evaluate(()=>{const h=window.__actionsTransport244;h.kind=null;h.release();});
  const held=()=>page.waitForFunction(()=>window.__actionsTransport244.held.length>0,null,{timeout:120000});
  const countPrepared=()=>page.evaluate(()=>window.__actionsTransport244.prepared.length);
  const accepted=async before=>{
    await page.waitForFunction(epoch=>{const d=globalThis.__actionsUiDisplay244();return d?.generation===epoch+1&&!d.inert&&!d.transition_busy;},before,{timeout:180000});
    await ready(page);return coherent();
  };
  const coherent=async()=>{
    const display=await shown(page),bundle=await call(page,'loadWorkspace');
    assert.equal(display.generation,bundle.generation);assert.equal(display.metadata.project_generation,bundle.generation);
    assert.equal(display.graph.project_generation,bundle.generation);assert.deepEqual(display.graph.recipe,bundle.graph.recipe);
    assert.deepEqual(display.metadata.source,bundle.metadata.source);assert.deepEqual(display.mesh_positions,display.positions);
    assert.equal(await page.evaluate(()=>crossOriginIsolated),isolation);
    assert.equal(await page.workers()[0].evaluate(()=>crossOriginIsolated),isolation);
    if(bundle.graph.recipe.nodes.some(node=>node.operation==='source')){
      const evidence=await readSourceEvidence(page);
      assert.deepEqual(display.positions,evidence.positions.flat().map(Math.fround));
      assert.deepEqual(display.indices,evidence.triangles.flat());assert.equal(evidence.owned_fs_bytes,0);
    }else{assert.deepEqual(display.positions,[]);assert.deepEqual(display.indices,[]);}
    return display;
  };
  const sourceParity=async name=>{
    const actual=await readSourceEvidence(page),reference=expected[name];
    // Filename is only advisory. Every numerical source row is compared exactly.
    const {filename:actualFilename,...actualDescriptor}=actual.descriptor;
    const {filename:referenceFilename,...referenceDescriptor}=reference.descriptor;
    void actualFilename;void referenceFilename;assert.deepEqual(actualDescriptor,referenceDescriptor);
    for(const key of ['positions','normals','triangles','weights'])assert.deepEqual(actual[key],reference[key],name+'.'+key);
    assert.equal(actual.owned_fs_bytes,0);return actual;
  };
  const newProject=async()=>{const before=(await shown(page)).generation;await command(page,'New project');return accepted(before);};
  const force=async()=>{
    const workers=await page.evaluate(()=>window.__actionsTransport244.workers);await page.locator('#force-restart').click();
    await page.waitForFunction(before=>window.__actionsTransport244.workers>before
      &&window.__actionsUiBackend244.getLifecycle().state==='complete',workers,{timeout:180000});
  };
  const evaluate=async recipe=>{
    const bundle=await call(page,'loadWorkspace'),epoch=bundle.generation;
    assert.deepEqual(bundle.graph.recipe,recipe);
    await page.getByRole('button',{name:'Evaluate all',exact:true}).click();await ready(page);
    await page.waitForFunction(({epoch,token})=>window.__actionsTransport244.graphs.some(graph=>graph.project_generation===epoch
      &&graph.token===token&&graph.evaluation_running===false&&Array.isArray(graph.required_failures)
      &&Object.values(graph.states).every(state=>state==='ready')),{epoch,token:bundle.graph.token},{timeout:180000});
    const graph=await page.evaluate(({epoch,token})=>window.__actionsTransport244.graphs.findLast(graph=>graph.project_generation===epoch
      &&graph.token===token&&graph.evaluation_running===false&&Array.isArray(graph.required_failures)),{epoch,token:bundle.graph.token});
    assertAcceptedSourceGraph(graph,{generation:epoch,recipe,token:bundle.graph.token});
    const display=await coherent();assert.deepEqual(display.graph.recipe,graph.recipe);return {graph,epoch,display};
  };
  const scenePhysical=graph=>{
    const cylinder=graph.results.cylinder,plane=graph.results.plane,geometry=cylinder.analytic;
    assert.ok(Math.abs(geometry.radius-2.5)<=1e-6);assert.ok(Math.abs(Math.abs(geometry.axis[0])-1)<=1e-6);
    assert.ok(Math.hypot(geometry.axis[1],geometry.axis[2])<=1e-6);
    assert.ok(Math.hypot(geometry.origin[1]+4,geometry.origin[2]-5)<=1e-6);
    assert.ok(cylinder.weighted_rms<1e-6);assert.ok(plane.weighted_rms<1e-12);
    assert.ok(Math.abs(Math.abs(plane.parameters[0])-1)<=1e-12);
    assert.ok(Math.hypot(plane.parameters[1],plane.parameters[2])<=1e-12);
    assert.ok(Math.abs(Math.abs(plane.parameters[3])-8)<=1e-12);
    for(const [name,fit]of [['plane',plane],['cylinder',cylinder]]){
      const ids=sceneOracle.selections[name];assert.deepEqual(fit.ids,ids);assert.equal(fit.residuals.length,ids.length);
      assert.deepEqual(graph.memberships[name+'_selection'],ids);
      assert.deepEqual(graph.recipe.nodes.find(node=>node.id===name+'_selection').ids,ids);
      let squared=0,weight=0;
      ids.forEach((id,index)=>{
        const point=sceneOracle.positions[id];let residual;
        if(name==='plane')residual=point.reduce((sum,value,i)=>sum+value*plane.parameters[i],0)-plane.parameters[3];
        else{
          const offset=point.map((value,i)=>value-geometry.origin[i]),height=offset.reduce((sum,value,i)=>sum+value*geometry.axis[i],0);
          residual=Math.hypot(...offset.map((value,i)=>value-height*geometry.axis[i]))-geometry.radius;
        }
        assert.ok(Math.abs(fit.residuals[index]-residual)<=1e-12);
        squared+=sceneOracle.weights[id]*residual*residual;weight+=sceneOracle.weights[id];
      });
      assert.ok(Math.abs(fit.weighted_rms-Math.sqrt(squared/weight))<=1e-12);
    }
    const support=geometry.axial_domain.map(height=>geometry.origin.map((value,index)=>value+height*geometry.axis[index]));
    const reference=expected['horizontal-scene'].graph.results.cylinder.analytic;
    const expectedSupport=reference.axial_domain.map(height=>reference.origin.map((value,index)=>value+height*reference.axis[index]));
    const distances=(first,second)=>Math.hypot(...first.map((value,index)=>value-second[index]));
    assert.ok(Math.min(Math.max(distances(support[0],expectedSupport[0]),distances(support[1],expectedSupport[1])),
      Math.max(distances(support[0],expectedSupport[1]),distances(support[1],expectedSupport[0])))<=1e-6);
  };
  const raw=await readFile(path.join(sources,'raw-nozzle.ply')),scene=await readFile(path.join(sources,'horizontal-scene.ply'));
  const sceneOracle=JSON.parse(await readFile(path.join(sources,'horizontal-scene.expected.json'),'utf8'));
  try{
    await page.goto(preview.origin+prefix+'app/');await ready(page);
    assert.equal((await page.evaluate(()=>window.__actionsUiBackend244.capabilities)).sourceActions,true);
    await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
    result.phase='explicit-matching-legacy';
    const initial=await coherent(),legacy=await saveActions(page);assert.equal(initial.graph.recipe.nodes[0].descriptor,null);
    let before=initial.generation,prepared=await countPrepared();
    await pickActions(page,legacy.recipe);const matchedLegacy=await accepted(before);
    assert.deepEqual(matchedLegacy.graph.recipe,legacy.recipe);
    assert.equal(await page.locator('#locate-source-dialog').isVisible(),false);
    assert.equal(await page.evaluate(index=>window.__actionsTransport244.prepared.slice(index).some(item=>item.file),prepared),false);
    result.cases.push({name:'explicit-matching-legacy',complete:true,generation:matchedLegacy.generation,no_source_lookup:true,recipe_preserved:true});

    result.phase='raw-save-new-locate';await newProject();
    let chooser=page.waitForEvent('filechooser');await page.locator('[data-add-scan]').click();
    await(await chooser).setFiles(path.join(sources,'raw-nozzle.ply'));before=(await shown(page)).generation;
    await page.waitForFunction(()=>globalThis.__actionsUiDisplay244().metadata.source?.source_sha256==='537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358'
      &&!globalThis.__actionsUiDisplay244().transition_busy,null,{timeout:180000});await ready(page);await sourceParity('raw-nozzle');
    const rawSaved=await saveActions(page);assert.equal(rawSaved.recipe.nodes.length,1);await newProject();
    before=(await shown(page)).generation;await pickActions(page,rawSaved.recipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
    await pickLocated(page,'renamed-exact-nozzle.ply',raw);const reopenedRaw=await accepted(before);await sourceParity('raw-nozzle');
    assert.deepEqual(reopenedRaw.graph.recipe,rawSaved.recipe);assert.equal(reopenedRaw.graph.recipe.nodes.length,1);
    result.cases.push({name:'raw-save-new-renamed-locate',complete:true,generation:reopenedRaw.generation,source_only:true,strict_source_rows:true});

    result.phase='scene-opened';before=reopenedRaw.generation;
    const sceneRecipe=expected['horizontal-scene'].graph.recipe;
    await pickActions(page,sceneRecipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
    await pickLocated(page,'renamed-scene.ply',scene);const opened=await accepted(before);await sourceParity('horizontal-scene');
    for(const id of ['plane','cylinder']){assert.equal(opened.graph.states[id],'unevaluated');assert.equal(opened.graph.results[id],undefined);}
    const first=await evaluate(sceneRecipe);scenePhysical(first.graph);
    result.scene_opened=first.graph;result.scene_opened_generation=first.epoch;result.observations.scene_opened=first.display;
    const sceneSaved=await saveActions(page);assert.deepEqual(sceneSaved.recipe,sceneRecipe);
    result.cases.push({name:'scene-located-fresh-graph-real-evaluate',complete:true,generation:first.epoch,strict_source_rows:true,fresh_dirty_graph:true,real_ui_evaluation:true,independent_geometry_objective_and_support:true});

    result.phase='scene-matching-replayed';before=first.epoch;prepared=await countPrepared();
    const originalFile=await page.evaluate(()=>window.__actionsTransport244.files.length-1);
    await pickActions(page,sceneSaved.recipe);const matching=await accepted(before);
    assert.equal(await page.evaluate(index=>window.__actionsTransport244.prepared.slice(index).some(item=>item.file),prepared),false);
    for(const id of ['plane','cylinder'])assert.equal(matching.graph.states[id],'unevaluated');
    const matchingReady=await evaluate(sceneSaved.recipe);scenePhysical(matchingReady.graph);assert.notEqual(matchingReady.epoch,first.epoch);
    result.observations.matching_ready=matchingReady.display;
    result.cases.push({name:'matching-no-upload-real-evaluate',complete:true,generation:matchingReady.epoch,no_source_upload:true,new_epoch_and_dirty_graph:true,real_ui_evaluation:true,independent_geometry_objective_and_support:true});
    result.phase='matching-force-replayed';await force();await ready(page);const restored=await coherent();
    assert.equal(restored.generation,matchingReady.epoch);assert.deepEqual(restored.graph.recipe,sceneSaved.recipe);
    for(const id of ['plane','cylinder']){assert.equal(restored.graph.states[id],'unevaluated');assert.equal(restored.graph.results[id],undefined);}
    await sourceParity('horizontal-scene');
    assert.equal(await page.evaluate(index=>{const h=window.__actionsTransport244;return h.files.some(item=>item.recovery&&item.file===h.files[index].file);},originalFile),true);
    // Require a newly emitted terminal message, rather than the pre-Force receipt.
    await page.evaluate(()=>{window.__actionsTransport244.graphs=[];});
    const second=await evaluate(sceneSaved.recipe);scenePhysical(second.graph);
    result.scene_replayed=second.graph;result.scene_replayed_generation=second.epoch;result.observations.scene_replayed=second.display;
    result.cases.push({name:'matching-force-replayed',complete:true,generation:second.epoch,original_file_recovered:true,strict_source_rows:true,
      accepted_epoch_preserved:true,derived_results_cleared:true,real_ui_evaluation:true,independent_geometry_objective_and_support:true});
    if(site.manifest.capabilities.cad){
      // CAD export consumes the graph produced by the real UI evaluation above.
      const bytes=await page.evaluate(async()=>{const backend=window.__actionsUiBackend244,graph=await backend.getGraph();
        return [...new Uint8Array(await backend.exportCad({token:graph.token,target:'cylinder',units:'Millimeters',axis_up:false,include_mesh:false}))];});
      const zip=path.join(output,'ui-actions-scene-cad.zip'),reference=path.join(output,'ui-actions-native-reference.json'),step=path.join(output,'ui-actions-scene-step.json');
      await writeFile(zip,Buffer.from(bytes));await writeFile(reference,JSON.stringify(expected));
      await runSourcePython(nativePython,['cad','--runtime',zip,'--inputs',reference,'--output',step],output);
      result.step_readback=JSON.parse(await readFile(step,'utf8'));assert.equal(result.step_readback.valid,true);
    }

    for(const docked of [false,true]){
      result.phase=docked?'docked-failure-preservation':'popup-failure-preservation';
      const label=`Preserve actions ${docked?'docked':'popup'} draft`;await draft(page,docked,label);const prior=await coherent();
      await pickActions(page,rawSaved.recipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
      await page.locator('#locate-source-file').dispatchEvent('cancel');await assertDraft(page,label);sameSource(await coherent(),prior);
      await page.keyboard.press('Escape');await page.locator('#locate-source-dialog').waitFor({state:'hidden'});await assertDraft(page,label);
      await pickActions(page,rawSaved.recipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
      const wrong=Buffer.from(raw),offset=wrong.indexOf(Buffer.from('end_header\n'))+11;assert.ok(offset>=11);wrong[offset]^=1;
      await pickLocated(page,'raw-nozzle.ply',wrong);
      await page.waitForFunction(()=>document.querySelector('#locate-source-dialog').open&&document.querySelector('#locate-source-error').textContent.length>0,null,{timeout:180000});
      sameSource(await coherent(),prior);await assertDraft(page,label);await page.locator('#cancel-locate-source').click();
      const malformed=structuredClone(sceneRecipe);malformed.nodes[0].descriptor.interpretation_sha256='a'.repeat(64);
      prepared=await countPrepared();await pickActions(page,malformed);
      await page.waitForFunction(()=>document.querySelector('#status').classList.contains('error')
        &&/interpretation|descriptor|binding/i.test(document.querySelector('#status').textContent));
      assert.equal(await countPrepared(),prepared);sameSource(await coherent(),prior);await assertDraft(page,label);
      result.cases.push({name:result.phase,complete:true,picker_cancel:true,locator_cancel:true,wrong_same_name_preserves_work:true,settings_rejected_before_prepare:true});
      await closeDraft(page);
    }

    result.phase='stale-inspection';await arm('inspection');await pickActions(page,rawSaved.recipe);await held();
    const staleBefore=await coherent();prepared=await countPrepared();
    await draft(page,false,'Accepted while lookup is pending');await page.getByRole('button',{name:'Add point',exact:true}).click();
    await page.waitForFunction(()=>globalThis.__actionsUiDisplay244().graph.recipe.nodes.some(node=>node.label==='Accepted while lookup is pending'));
    await release();await ready(page);const staleAfter=await coherent();sameSource(staleAfter,staleBefore,{recipe:false});
    assert.equal(await countPrepared(),prepared);assert.equal(await page.locator('#locate-source-dialog').isVisible(),false);
    result.cases.push({name:'stale-inspection',complete:true,newer_actions_preserved:true,no_source_prepare:true});

    result.phase='cancel-before-adoption';await draft(page,false,'Keep cancelled opening draft');let prior=await coherent();
    await arm('source-candidate');await pickActions(page,rawSaved.recipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
    await pickLocated(page,'cancelled-raw.ply',raw);await held();await page.locator('#cancel-source-import').click();await release();
    await page.locator('#source-progress').waitFor({state:'hidden',timeout:180000});sameSource(await coherent(),prior);await assertDraft(page,'Keep cancelled opening draft');
    if(await page.locator('#locate-source-dialog').isVisible())await page.locator('#cancel-locate-source').click();
    result.cases.push({name:'cancel-before-adoption',complete:true,prior_source_actions_and_draft_preserved:true});await closeDraft(page);

    result.phase='force-before-adoption';await draft(page,true,'Keep actions Force draft');prior=await coherent();
    await arm('source-candidate');await pickActions(page,rawSaved.recipe);await page.locator('#locate-source-dialog').waitFor({state:'visible'});
    await pickLocated(page,'unaccepted-raw.ply',raw);await held();await force();await ready(page);await release();
    sameSource(await coherent(),prior);await assertDraft(page,'Keep actions Force draft');
    if(await page.locator('#locate-source-dialog').isVisible())await page.locator('#cancel-locate-source').click();
    assert.equal(await page.evaluate(index=>{const h=window.__actionsTransport244;return h.files.some(item=>item.recovery&&item.file===h.files[index].file);},originalFile),true);
    result.cases.push({name:'force-before-adoption',complete:true,old_file_and_live_docked_draft_preserved:true});

    result.phase='force-after-adoption-lost-ack';prior=await coherent();
    const warned=structuredClone(prior.graph.recipe);warned.nodes.push(
      {id:'ui-actions-warning-a',operation:'point',label:'Actions duplicate',initial_coordinates:[1,2,3]},
      {id:'ui-actions-warning-b',operation:'point',label:'Actions duplicate',initial_coordinates:[4,5,6]});
    await arm('source-promoted');await pickActions(page,warned);await held();await force();
    await page.locator('#source-unavailable').waitFor({state:'visible',timeout:180000});sameSource(await shown(page),prior);await assertDraft(page,'Keep actions Force draft');
    const recovery=await call(page,'loadWorkspace');assert.equal(recovery.generation,prior.generation+1);assert.equal(recovery.metadata.import_warnings.length,1);
    const waiting=page.waitForEvent('download');await page.locator('#download-prior-actions').click();
    assert.deepEqual(JSON.parse(await readFile(await(await waiting).path(),'utf8')),prior.graph.recipe);
    await release();declineNext=true;await page.locator('#retry-source-workspace').click();await assertDraft(page,'Keep actions Force draft');sameSource(await shown(page),prior);
    await page.locator('#retry-source-workspace').click();await ready(page);const retried=await coherent();
    assert.equal(retried.generation,recovery.generation);assert.equal(await page.locator('#point-dialog').isVisible(),false);
    await visible(page.locator('#import-warning'));await sourceParity('horizontal-scene');
    assert.equal(await page.evaluate(index=>{const h=window.__actionsTransport244;return h.files.some(item=>item.recovery&&item.file===h.files[index].file);},originalFile),true);
    result.cases.push({name:'force-after-adoption-lost-ack',complete:true,accepted_file_retained:true,prior_actions_downloaded:true,draft_preserved_until_explicit_retry:true,warnings_recovered:true});

    result.phase='incompatible-legacy';prior=await coherent();prepared=await countPrepared();await pickActions(page,legacy.recipe);
    await page.waitForFunction(()=>document.querySelector('#status').classList.contains('error'));
    assert.equal(await countPrepared(),prepared);sameSource(await coherent(),prior);
    result.cases.push({name:'incompatible-legacy',complete:true,no_example_guessing_or_source_prepare:true});
    result.phase='empty-actions';await pickActions(page,{schema_version:2,nodes:[],groups:[],output:null});const empty=await accepted(prior.generation);
    assert.deepEqual(empty.graph.recipe.nodes,[]);assert.equal(empty.metadata.source,null);await visible(page.locator('[data-add-scan]'));
    result.cases.push({name:'empty-actions',complete:true,generation:empty.generation,new_epoch_and_empty_arrays:true});
    result.observations.final=empty;result.complete=true;result.phase='complete';return result;
  }finally{
    if(!result.complete)result.failure_snapshot=await page.evaluate(()=>({display:globalThis.__actionsUiDisplay244?.(),
      lifecycle:globalThis.__actionsUiBackend244?.getLifecycle(),status:document.querySelector('#status')?.textContent,
      held:window.__actionsTransport244?.held.length,prepared:window.__actionsTransport244?.prepared})).catch(error=>({error:String(error)}));
    await context.close();
  }
}
