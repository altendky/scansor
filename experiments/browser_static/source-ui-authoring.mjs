// Actual shipped worker authoring: real File/DOM/pointer workflows. Appended
// observers are read-only; transport holds only expose production boundaries.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {sha256} from './server.mjs';
import {sourceEvidenceSuffix,readSourceEvidence} from './source-import-harness.mjs';
import {assertAcceptedSourceGraph} from './source-semantic.mjs';

const appSuffix=`
globalThis.__authoringBackend243=backend;
globalThis.__authoringDisplay243=()=>!positions||!mesh?.geometry?.index?null:({generation:workspaceGeneration,metadata,graph:graphState,
 positions:[...positions],indices:[...mesh.geometry.index.array],mesh_positions:[...mesh.geometry.getAttribute('position').array],
 ready:$('status').dataset.runtimeReady==='true',inert:document.querySelector('main').inert,transition_busy:sourceTransitionBusy,
 evaluation_running:!!activeGraphEvaluation,selection_pending:selectionPending,selection_drawing:selectionDrawing,authoring_applying:authoringApplying});
globalThis.__authoringProjection243=()=>{
 const matrix=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse).multiply(mesh.matrixWorld);
 const vector=new THREE.Vector4(),box=renderer.domElement.getBoundingClientRect(),rows=[];
 for(let id=0;id<positions.length/3;id++){
  vector.set(positions[id*3],positions[id*3+1],positions[id*3+2],1).applyMatrix4(matrix);
  rows.push([box.x+(vector.x/vector.w+1)*viewport.clientWidth/2,box.y+(1-vector.y/vector.w)*viewport.clientHeight/2,vector.z/vector.w,vector.w]);
 }
 return {rows,box:{x:box.x,y:box.y,width:box.width,height:box.height}};
};
`;
const workerSuffix=sourceEvidenceSuffix;
const display=page=>page.evaluate(()=>globalThis.__authoringDisplay243());
const call=(page,method,payload)=>page.evaluate(async({method,payload})=>globalThis.__authoringBackend243[method](payload),{method,payload});
const ready=page=>page.waitForFunction(()=>{const d=globalThis.__authoringDisplay243?.();return d?.ready&&!d.inert&&!d.transition_busy
  &&!d.evaluation_running&&!d.selection_pending&&!d.selection_drawing&&!d.authoring_applying;},null,{timeout:180000});
const frames=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function command(page,name){
  await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name,exact:true}).click();
}
const projectLayout=page=>page.evaluate(()=>{
  const rect=element=>{if(!element)return null;const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
  const trigger=document.querySelector('[aria-label="Project toolbar options"]'),r=trigger.getBoundingClientRect();
  const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
  return {viewport:{width:innerWidth,height:innerHeight},trigger:rect(trigger),popup:rect(document.getElementById('edit-popup')),
    popup_open:document.getElementById('edit-popup').open,header:rect(document.querySelector('.edit-popup-header')),
    center_hit:{tag:hit?.tagName,id:hit?.id,class:hit?.className},center_reachable:hit===trigger||trigger.contains(hit),
    placement:JSON.parse(localStorage.getItem('scansor.toolbars.v1'))};
});
function sameWork(actual,reference,{recipe=true}={}){
  assert.equal(actual.generation,reference.generation);assert.deepEqual(actual.metadata.source,reference.metadata.source);
  if(recipe)assert.deepEqual(actual.graph.recipe,reference.graph.recipe);
  for(const key of ['positions','indices','mesh_positions'])assert.deepEqual(actual[key],reference[key],key);
}
function noComputedStarts(recipe){
  const forbidden=new Set(['initial_parameters','initialization','seed','fit_geometry','solver','residuals','weighted_rms']);
  const walk=value=>{if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')for(const[key,item]of Object.entries(value)){
    assert.equal(forbidden.has(key),false,'Computed state in authored actions: '+key);walk(item);
  }};walk(recipe);
}
async function closeEditor(page){
  const tab=page.getByRole('tab',{name:'Edit',exact:true});
  if(await tab.count()){await tab.getByTitle('Close',{exact:true}).click();await tab.waitFor({state:'detached'});}
  else if(await page.locator('#edit-popup').isVisible())await page.locator('#close-edit').click();
}
async function pointDraft(page,docked,label){
  if(!docked)await closeEditor(page);
  await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#point-dialog').waitFor({state:'visible'});
  await page.locator('#new-point-label').fill(label);
  await page.locator('#new-point-label').evaluate((input,docked)=>{window.__authoringDraft243=input;window.__authoringDraftDocked243=docked;},docked);
  if(docked){
    if(await page.locator('#edit-popup').isVisible())await page.locator('#dock-edit').click();
    await page.getByRole('tab',{name:'Edit',exact:true}).waitFor({state:'visible'});await page.locator('#edit-popup').waitFor({state:'hidden'});
  }else await page.locator('#edit-popup').waitFor({state:'visible'});
  await checkDraft(page,label);
}
async function checkDraft(page,label){
  assert.equal(await page.locator('#point-dialog').isVisible(),true);
  assert.equal(await page.locator('#new-point-label').evaluate((input,label)=>input===window.__authoringDraft243&&input.value===label
    &&!!input.closest('#edit-popup')===!window.__authoringDraftDocked243,label),true);
  const docked=await page.evaluate(()=>window.__authoringDraftDocked243);
  assert.equal(await page.locator('#edit-popup').isVisible(),!docked);
  assert.equal(await page.getByRole('tab',{name:'Edit',exact:true}).count(),docked?1:0);
}

export async function validateSourceUiAuthoring({browser,site,preview,prefix,sources,isolation,report,expected,output}){
  const result=report.ui_authoring={schema:'scansor-source-ui-authoring-v1',complete:false,phase:'startup',cases:[],graphs:{},graph_generations:{},
    evidence:{},gestures:[],instrumentation:{app_suffix_sha256:sha256(Buffer.from(appSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix)),
      description:'Exact manifest-verified shipped modules plus appended read-only source/display/camera observers; real app singleton, Worker, File pickers, pointer gestures and forms. Disclosed source-candidate transport holds expose Cancel/removal boundaries; no recipe writes, numerical-policy changes or authority overrides.'}};
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);
    if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.api_requests.push(url);});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url()),suffix=url.pathname.endsWith('/app.js')?appSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
    if(!suffix)return route.continue();
    assert.equal(url.origin,preview.origin);const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);
    assert.ok(record,'Observed production module must be listed');const bytes=await readFile(path.join(site.root,relative));
    assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(suffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  const page=await context.newPage();page.setDefaultTimeout(60000);page.on('pageerror',error=>report.page_errors.push(String(error)));
  const confirmations=[];
  page.on('dialog',async dialog=>{assert.equal(dialog.type(),'confirm');const accept=confirmations.length?confirmations.shift():true;
    if(accept)await dialog.accept();else await dialog.dismiss();});
  await page.addInitScript(()=>{
    const NativeWorker=window.Worker,h=window.__authoringTransport243={workers:0,target:0,hold:null,held:[],heldRequest:null,
      operations:new Map(),prepared:[],requests:[],graphs:[],release(){for(const deliver of this.held.splice(0))deliver();this.heldRequest=null;}};
    window.Worker=class extends NativeWorker{
      constructor(...args){super(...args);this.index=++h.workers;}
      postMessage(message,...args){
        h.operations.set(message.id,message.operation);h.requests.push(message.operation);
        if(message.operation==='prepare-source')h.prepared.push({kind:message.payload.kind,operationId:message.payload.operationId});
        return super.postMessage(message,...args);
      }
      set onmessage(callback){super.onmessage=event=>{
        const data=event.data,operation=h.operations.get(data.id);
        const graph=data.kind==='graph'?data.state:data.ok&&['evaluateGraph','ensureGraph'].includes(operation)?data.result:null;
        if(graph)h.graphs.push(structuredClone(graph));
        if(this.index===h.target&&(data.kind===h.hold||(h.heldRequest===data.id&&data.kind==='transfer-confirmed'))){
          h.heldRequest=data.id;h.held.push(()=>callback?.(event));return;
        }
        callback?.(event);
      };}
    };
  });
  const prepared=()=>page.evaluate(()=>window.__authoringTransport243.prepared.length);
  const arm=()=>page.evaluate(()=>{const h=window.__authoringTransport243;h.target=h.workers;h.hold='source-candidate';h.heldRequest=null;});
  const held=()=>page.waitForFunction(()=>window.__authoringTransport243.held.length>0,null,{timeout:180000});
  const release=()=>page.evaluate(()=>{const h=window.__authoringTransport243;h.hold=null;h.release();});
  const coherent=async()=>{
    const shown=await display(page),bundle=await call(page,'loadWorkspace');
    assert.equal(shown.generation,bundle.generation);assert.equal(shown.metadata.project_generation,bundle.generation);
    assert.equal(shown.graph.project_generation,bundle.generation);assert.deepEqual(shown.graph.recipe,bundle.graph.recipe);
    assert.deepEqual(shown.metadata.source,bundle.metadata.source);assert.deepEqual(shown.positions,shown.mesh_positions);
    assert.equal(await page.evaluate(()=>crossOriginIsolated),isolation);
    assert.equal(await page.workers()[0].evaluate(()=>crossOriginIsolated),isolation);
    return shown;
  };
  const accepted=async before=>{await page.waitForFunction(epoch=>{const d=globalThis.__authoringDisplay243();
    return d?.generation===epoch+1&&!d.inert&&!d.transition_busy;},before,{timeout:180000});await ready(page);return coherent();};
  const newProject=async()=>{const before=(await display(page)).generation;await command(page,'New project');return accepted(before);};
  const choose=async file=>{
    const waiting=page.waitForEvent('filechooser');
    if(await page.locator('[data-add-scan]').isVisible())await page.locator('[data-add-scan]').click();else await command(page,'Add scan…');
    const chooser=await waiting;assert.equal(await chooser.element().getAttribute('id'),'scan-file');await chooser.setFiles(file);
  };
  const importFile=async name=>{const before=(await display(page)).generation;await choose(path.join(sources,name+'.ply'));return accepted(before);};
  const sourceRows=async name=>{
    const evidence=await readSourceEvidence(page);assert.deepEqual(evidence.descriptor,expected[name].descriptor);
    for(const key of ['positions','normals','triangles','weights'])assert.deepEqual(evidence[key],expected[name][key],name+'.'+key);
    const shown=await coherent();assert.deepEqual(shown.positions,expected[name].positions.flat().map(Math.fround));
    assert.deepEqual(shown.indices,expected[name].triangles.flat());assert.equal(evidence.owned_fs_bytes,0);return evidence;
  };
  const evaluate=async(key,recipe)=>{
    const bundle=await call(page,'loadWorkspace');assert.deepEqual(bundle.graph.recipe,recipe);noComputedStarts(recipe);
    await page.evaluate(()=>{window.__authoringTransport243.graphs=[];});
    await page.getByRole('button',{name:'Evaluate all',exact:true}).click();await ready(page);
    await page.waitForFunction(({epoch,token})=>window.__authoringTransport243.graphs.some(graph=>graph.project_generation===epoch&&graph.token===token
      &&graph.evaluation_running===false&&Array.isArray(graph.required_failures)&&Object.values(graph.states).every(state=>state==='ready')),
    {epoch:bundle.generation,token:bundle.graph.token},{timeout:180000});
    const publication=await page.evaluate(({epoch,token})=>window.__authoringTransport243.graphs.findLast(graph=>graph.project_generation===epoch
      &&graph.token===token&&graph.evaluation_running===false&&Array.isArray(graph.required_failures)),{epoch:bundle.generation,token:bundle.graph.token});
    // The shipped backend restores a token-matched recipe omitted from compact
    // evaluation messages. Compare that real UI state with the observed
    // terminal publication, preserving all geometry and status assertions.
    const graph=(await coherent()).graph;
    for(const [key,value]of Object.entries(publication))assert.deepEqual(graph[key],value,'published '+key);
    assertAcceptedSourceGraph(graph,{generation:bundle.generation,recipe,token:bundle.graph.token});
    result.graphs[key]=graph;result.graph_generations[key]=bundle.generation;
    await writeFile(path.join(output,'ui-authoring-'+key+'-graph.json'),JSON.stringify(graph,null,2)+'\n');return graph;
  };
  const selection=async(label,ids,view='top')=>{
    const before=await display(page),priorIds=new Set(before.graph.recipe.nodes.map(node=>node.id));
    await page.getByRole('button',{name:'Selection',exact:true}).click();
    await page.waitForFunction(before=>globalThis.__authoringDisplay243().graph.recipe.nodes.some(node=>node.operation==='selection'&&!before.includes(node.id)),[...priorIds]);
    const node=(await display(page)).graph.recipe.nodes.find(node=>node.operation==='selection'&&!priorIds.has(node.id));
    await page.locator('#selection-tools').waitFor({state:'visible'});
    if(await page.locator('#edit-popup').isVisible())await page.locator('#dock-edit').click();
    await page.locator('#selection-shape').selectOption('rectangle');await page.locator('#selection-depth').selectOption('through_all');
    assert.ok(view==='top'||view==='side');
    await page.locator('#tool').selectOption('replace');await page.locator('#'+view).click();await frames(page);
    const projection=await page.evaluate(()=>globalThis.__authoringProjection243()),rows=projection.rows;
    assert.ok(ids.length>=3&&ids.length<=25000);ids.forEach(id=>assert.ok(rows[id][3]>0&&Math.abs(rows[id][2])<=1));
    const box=projection.box,wholeSource=ids.length===rows.length;
    // Preregistered all-row raw gesture, independently exercised by the native
    // UI acceptance. The generated patches use Side view: the plane's Z range
    // [-2,2] is separate from the cylinder's Z range [2.5,7.5]. Their projected
    // bounds must still enclose exactly the independently prescribed IDs.
    const x0=wholeSource?box.x+8:Math.min(...ids.map(id=>rows[id][0]))-5,x1=wholeSource?box.x+box.width-8:Math.max(...ids.map(id=>rows[id][0]))+5;
    const y0=wholeSource?box.y+8:Math.min(...ids.map(id=>rows[id][1]))-5,y1=wholeSource?box.y+box.height-8:Math.max(...ids.map(id=>rows[id][1]))+5;
    assert.ok(x0>box.x&&x1<box.x+box.width&&y0>box.y&&y1<box.y+box.height,'Preregistered patch must fit within the actual canvas');
    const enclosed=rows.flatMap((row,id)=>row[3]>0&&Math.abs(row[2])<=1&&row[0]>=x0&&row[0]<=x1&&row[1]>=y0&&row[1]<=y1?[id]:[]);
    assert.deepEqual(enclosed,ids,'Fixture-defined rectangle must isolate exactly its preregistered source IDs');
    const candidates=[[[x0,y0],[x1,y1]],[[x1,y1],[x0,y0]],[[x0,y1],[x1,y0]],[[x1,y0],[x0,y1]]];
    let gesture;
    for(const [start,end]of candidates){if(await page.evaluate(([x,y])=>document.elementFromPoint(x,y)===document.querySelector('#viewport canvas'),start)){gesture={start,end};break;}}
    assert.ok(gesture,'A rectangle corner must be reachable through the actual unobscured canvas');
    await page.mouse.move(...gesture.start);await page.mouse.down();await page.mouse.move(...gesture.end,{steps:10});await page.mouse.up();
    await page.waitForFunction(({id,ids})=>{const d=globalThis.__authoringDisplay243(),node=d.graph.recipe.nodes.find(node=>node.id===id);
      return !d.selection_pending&&!d.selection_drawing&&node?.depth==='through_all'&&JSON.stringify(node.ids)===JSON.stringify(ids);},{id:node.id,ids},{timeout:120000});
    await ready(page);result.gestures.push({name:label,selection:node.id,view,depth:'through_all',expected_ids:ids,canvas:box,...gesture});return node.id;
  };
  const fit=async(kind,input,label)=>{
    await page.getByRole('button',{name:'Surface fit',exact:true}).click();await page.locator('#new-fit-kind').selectOption(kind);
    await page.locator('#new-fit-inputs').selectOption(input);await page.locator('#new-fit-label').fill(label);
    await page.locator('#add-fit-form').getByRole('button',{name:'Add fit',exact:true}).click();
    await page.waitForFunction(label=>globalThis.__authoringDisplay243().graph.recipe.nodes.some(node=>node.operation==='fit'&&node.label===label),label);
    await ready(page);const node=(await display(page)).graph.recipe.nodes.find(node=>node.operation==='fit'&&node.label===label);
    assert.equal(node.kind,kind);assert.deepEqual(node.selections,[input]);
    // Automatic axial support is meaningful for the imported cylinder. Plane
    // fitting retains an inert legacy domain field; it does not clip the plane.
    if(kind==='cylinder')assert.equal(node.axial_domain,null);return node.id;
  };
  const objective=(graph,fitId,ids,source)=>{
    const fit=graph.results[fitId];assert.deepEqual(fit.ids,ids);assert.equal(fit.residuals.length,ids.length);
    let weighted=0,total=0,maxDifference=0;
    ids.forEach((id,index)=>{
      const point=source.positions[id];let residual;
      if(fit.kind==='plane')residual=point.reduce((sum,value,i)=>sum+value*fit.parameters[i],0)-fit.parameters[3];
      else{assert.equal(fit.kind,'cylinder');const g=fit.analytic,offset=point.map((value,i)=>value-g.origin[i]);
        const h=offset.reduce((sum,value,i)=>sum+value*g.axis[i],0);residual=Math.hypot(...offset.map((value,i)=>value-h*g.axis[i]))-g.radius;}
      assert.ok(Number.isFinite(residual));maxDifference=Math.max(maxDifference,Math.abs(residual-fit.residuals[index]));
      weighted+=source.weights[id]*residual*residual;total+=source.weights[id];
    });
    const rms=Math.sqrt(weighted/total);assert.ok(Number.isFinite(rms)&&total>0);
    assert.ok(maxDifference<=1e-10);assert.ok(Math.abs(rms-fit.weighted_rms)<=1e-10+1e-10*Math.abs(rms));
    return {rows:ids.length,weight_sum:total,independent_rms:rms,max_residual_difference:maxDifference};
  };
  const sceneOracle=JSON.parse(await readFile(path.join(sources,'horizontal-scene.expected.json'),'utf8'));
  try{
    await page.goto(preview.origin+prefix+'app/');await ready(page);await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
    const exampleRecipe=await call(page,'getExampleRecipe');await newProject();
    result.phase='empty-entry-controls';const empty=await coherent();assert.deepEqual(empty.graph.recipe.nodes,[]);
    assert.equal(await page.getByRole('button',{name:'Selection',exact:true}).isEnabled(),false);
    for(const id of ['home','side','top'])await page.locator('#'+id).click();sameWork(await coherent(),empty);
    for(const name of ['Add scan…','Choose example…']){
      await page.setViewportSize({width:380,height:700});const selector=name==='Add scan…'?'[data-add-scan]':'[data-choose-example]';
      await page.locator(selector).click({trial:true});
    }await page.setViewportSize({width:1440,height:1000});
    result.cases.push({name:'empty-entry-controls',complete:true,empty_controls_honest:true,camera_preserves_empty:true,narrow_entry_reachable:true});

    result.phase='invalid-and-limit-picker-preservation';let count=await prepared();
    await page.locator('#scan-file').setInputFiles([]);await page.locator('#scan-file').dispatchEvent('cancel');
    assert.equal(await prepared(),count);sameWork(await coherent(),empty);
    const cap=empty.metadata.capabilities.source_import.limits.max_source_bytes;assert.ok(Number.isSafeInteger(cap)&&cap>0);
    await choose({name:'above-advertised-cap.ply',mimeType:'application/octet-stream',buffer:Buffer.alloc(cap+1)});
    await page.waitForFunction(()=>/limit|exceed/i.test(document.querySelector('#status').textContent));
    assert.equal(await prepared(),count);sameWork(await coherent(),empty);
    await choose({name:'malformed.ply',mimeType:'application/octet-stream',buffer:Buffer.from('not a PLY source')});
    await page.waitForFunction(before=>window.__authoringTransport243.prepared.length>before&&document.querySelector('#source-progress').hidden
      &&document.querySelector('#status').classList.contains('error'),count,{timeout:120000});
    sameWork(await coherent(),empty);assert.equal((await readSourceEvidence(page,{arrays:false})).owned_fs_bytes,0);
    result.cases.push({name:'invalid-and-limit-picker-preservation',complete:true,synthetic_picker_cancel_preserves_empty:true,advertised_limit_before_prepare:true,malformed_preserves_owner:true,staging_released:true});

    result.phase='visible-import-cancel-retry';await arm();await choose(path.join(sources,'raw-nozzle.ply'));await held();
    assert.equal(await page.locator('#source-progress').isVisible(),true);assert.equal(await page.locator('#cancel-source-import').isEnabled(),true);
    await page.locator('#cancel-source-import').click();await release();await page.locator('#source-progress').waitFor({state:'hidden',timeout:180000});
    await ready(page);sameWork(await coherent(),empty);assert.equal((await readSourceEvidence(page,{arrays:false})).owned_fs_bytes,0);
    await importFile('raw-nozzle');const rawSource=await sourceRows('raw-nozzle');result.evidence.raw_source=rawSource;
    result.cases.push({name:'visible-import-cancel-retry',complete:true,real_progress_cancel_reachable:true,preaccept_owner_preserved:true,late_candidate_inert:true,ordinary_retry_accepted:true});

    result.phase='raw-file-source-inspection';let current=await coherent();assert.equal(current.graph.recipe.nodes.length,1);
    assert.equal(current.graph.recipe.nodes[0].operation,'source');assert.equal(current.metadata.vertices,24999);assert.equal(current.metadata.triangles,49994);
    await command(page,'Inspect source');const text=await page.locator('#source-details').textContent();
    for(const value of ['raw-nozzle.ply',rawSource.source_sha256,'Unknown','binary-triangle-ply-v1'])assert.ok(text.includes(value));
    assert.equal(await page.locator('#source-properties input, #source-properties select').count(),0);
    assert.ok(text.includes(current.metadata.vertices.toLocaleString()));assert.ok(text.includes(current.metadata.triangles.toLocaleString()));
    assert.ok(text.includes('Isolated vertices'));assert.ok(text.includes('Zero-area triangles'));
    for(const id of ['home','side','top']){await page.locator('#'+id).click();sameWork(await coherent(),current);}
    count=await prepared();await page.locator('#scan-file').setInputFiles(path.join(sources,'horizontal-scene.ply'));
    await page.waitForFunction(()=>/already has a source/.test(document.querySelector('#status').textContent));assert.equal(await prepared(),count);sameWork(await coherent(),current);
    result.cases.push({name:'raw-file-source-inspection',complete:true,source_only_without_starters:true,strict_original_rows:true,readonly_metadata:true,camera_preserves_source:true,stale_second_picker_rejected:true});

    result.phase='raw-pointer-plane-authoring';const rawIds=Array.from({length:rawSource.vertices},(_,id)=>id);
    const rawSelection=await selection('all-raw-original-rows',rawIds),rawFit=await fit('plane',rawSelection,'Whole raw source least-squares plane');
    let graph=await evaluate('raw',(await display(page)).graph.recipe);assert.deepEqual(graph.memberships[rawSelection],rawIds);
    result.evidence.raw_objective=objective(graph,rawFit,rawIds,rawSource);
    result.evidence.raw_ids={selection:rawSelection,fit:rawFit};
    result.cases.push({name:'raw-pointer-plane-authoring',complete:true,actual_pointer_membership:true,ordinary_fit_dialog_and_evaluation:true,every_original_row_weight_accounted:true,seed_free_actions:true});

    result.phase='raw-fit-edit';await page.locator(`#action-list .action-select[data-action-id="${rawFit}"]`).dblclick();
    await page.locator('#fit-properties').waitFor({state:'visible'});assert.equal(await page.locator('#surface-kind').inputValue(),'plane');
    await page.locator('#apply-properties').click();await ready(page);
    await page.locator(`#action-list .action-select[data-action-id="${rawFit}"]`).click({button:'right'});await page.locator('#feature-context-rename').click();
    await page.locator('#rename-feature-label').fill('Edited raw least-squares plane');await page.locator('#rename-feature-form').getByRole('button',{name:'Rename',exact:true}).click();
    await page.waitForFunction(id=>globalThis.__authoringDisplay243().graph.recipe.nodes.find(node=>node.id===id).label==='Edited raw least-squares plane',rawFit);
    graph=await evaluate('raw_edited',(await display(page)).graph.recipe);assert.equal(graph.recipe.nodes.find(node=>node.id===rawFit).label,'Edited raw least-squares plane');
    assert.deepEqual(graph.recipe.nodes.find(node=>node.id===rawFit).selections,[rawSelection]);result.evidence.raw_edited_objective=objective(graph,rawFit,rawIds,rawSource);
    assert.equal(result.graph_generations.raw_edited,result.graph_generations.raw);
    result.cases.push({name:'raw-fit-edit',complete:true,actual_property_apply_and_rename:true,original_selection_ids_preserved:true,ordinary_reevaluation:true,source_epoch_preserved:true});

    result.phase='imported-source-keyboard-menu';await closeEditor(page);const keyboardBefore=await coherent();
    const requestsBefore=await page.evaluate(()=>window.__authoringTransport243.requests.filter(name=>['evaluateGraph','ensureGraph'].includes(name)).length);
    const item=page.locator('#action-list .action-select[data-action-id="scan"]').locator('xpath=ancestor::li[1]');
    await item.focus();await page.keyboard.press('Home');assert.equal(await item.evaluate(element=>element===document.activeElement),true);
    await page.keyboard.press('Shift+F10');await page.locator('#feature-context-menu').waitFor({state:'visible'});await page.keyboard.press('Escape');
    assert.equal(await item.evaluate(element=>element===document.activeElement||element.contains(document.activeElement)),true);
    await page.keyboard.press('Shift+F10');await page.keyboard.press('Enter');await page.locator('#source-properties').waitFor({state:'visible'});await closeEditor(page);
    await page.setViewportSize({width:380,height:700});const trigger=page.getByRole('button',{name:'Project toolbar options',exact:true});
    const focusState=()=>trigger.evaluate(element=>({trigger_connected:element.isConnected,trigger_focused:element===document.activeElement,
      trigger_expanded:element.getAttribute('aria-expanded'),menu_open:document.querySelector('[role="menu"][data-state="open"]')!==null,
      active:{tag:document.activeElement?.tagName,id:document.activeElement?.id,role:document.activeElement?.getAttribute('role'),label:document.activeElement?.getAttribute('aria-label')}}));
    result.evidence.toolbar_focus=[];
    for(const placement of ['Dock left','Dock right','Float','Rotate floating toolbar']){
      await trigger.click();const priorMenu=await page.getByRole('menu').elementHandle();assert.ok(priorMenu);
      await page.getByRole('menuitem',{name:placement,exact:true}).click();
      const placedTrigger=await trigger.elementHandle();
      try{await page.waitForFunction(({menu,trigger})=>!menu.isConnected&&trigger?.isConnected
        &&trigger===document.activeElement&&trigger.getAttribute('aria-expanded')==='false',
      {menu:priorMenu,trigger:placedTrigger},{timeout:10000});}
      finally{await priorMenu.dispose();await placedTrigger?.dispose();}
      const observation={placement,after_placement_settled:await focusState()};result.evidence.toolbar_focus.push(observation);
      assert.equal(observation.after_placement_settled.trigger_focused,true);assert.equal(observation.after_placement_settled.menu_open,false);
      await page.keyboard.press('Enter');
      for(const name of ['New project','Inspect source','Remove scan…']){
        const control=page.getByRole('menuitem',{name,exact:true});await control.click({trial:true});const box=await control.boundingBox();
        assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=381&&box.y+box.height<=701);
      }
      assert.equal(await page.getByRole('menuitem',{name:'Add scan…',exact:true}).getAttribute('data-disabled'),'');
      observation.before_escape=await focusState();
      await page.keyboard.press('Escape');observation.after_escape=await focusState();
      await page.getByRole('menu').waitFor({state:'hidden'});
      const element=await trigger.elementHandle();
      try{await page.waitForFunction(element=>element?.isConnected&&element===document.activeElement,element,{timeout:10000});}
      finally{await element?.dispose();}
      assert.equal(await trigger.evaluate(element=>element===document.activeElement),true);
      observation.after_settled=await focusState();assert.equal(observation.after_settled.menu_open,false);
    }
    await trigger.click();await page.getByRole('menuitem',{name:'Dock top',exact:true}).click();
    await page.getByRole('menu').waitFor({state:'hidden'});
    // Returning to a different rail appends the toolbar. Restore its original
    // first position before testing pointer commands beside a live popup.
    const restoration=result.evidence.toolbar_order_restoration={before:await projectLayout(page),moves:0};
    const moveLimit=await page.locator('.prototype-toolbar').count()-1;
    for(;;){
      await trigger.click();const earlier=page.getByRole('menuitem',{name:'Move earlier',exact:true});
      if(await earlier.getAttribute('data-disabled')===''){
        restoration.first_position_disabled=true;await page.keyboard.press('Escape');await page.getByRole('menu').waitFor({state:'hidden'});break;
      }
      assert.ok(restoration.moves<moveLimit,'Project toolbar order restoration must be bounded');
      await earlier.click();restoration.moves++;await page.getByRole('menu').waitFor({state:'hidden'});
    }
    await page.setViewportSize({width:1440,height:1000});await frames(page);
    restoration.after=await projectLayout(page);assert.equal(restoration.after.placement.project.order,0);
    assert.equal(restoration.after.placement.project.edge,'top');assert.equal(restoration.after.center_reachable,true);
    sameWork(await coherent(),keyboardBefore);
    assert.equal(await page.evaluate(()=>window.__authoringTransport243.requests.filter(name=>['evaluateGraph','ensureGraph'].includes(name)).length),requestsBefore);
    result.cases.push({name:'imported-source-keyboard-menu',complete:true,source_tree_keyboard_inspection:true,menu_enter_escape_focus:true,narrow_rotated_docked_commands_reachable:true,no_unrequested_evaluation_or_source_change:true});

    result.phase='generated-pointer-plane-cylinder';await newProject();await importFile('horizontal-scene');const sceneSource=await sourceRows('horizontal-scene');
    result.evidence.scene_source=sceneSource;const sceneIds={};
    for(const kind of ['plane','cylinder']){const input=await selection('independent-'+kind,sceneOracle.selections[kind],'side');
      sceneIds[kind]={selection:input,fit:await fit(kind,input,'Independent '+kind)};}
    graph=await evaluate('scene',(await display(page)).graph.recipe);result.evidence.scene_ids=sceneIds;
    for(const kind of ['plane','cylinder']){
      const ids=sceneOracle.selections[kind],record=sceneIds[kind];assert.deepEqual(graph.memberships[record.selection],ids);
      result.evidence[kind+'_objective']=objective(graph,record.fit,ids,sceneSource);
    }
    const plane=graph.results[sceneIds.plane.fit],cylinder=graph.results[sceneIds.cylinder.fit],geometry=cylinder.analytic;
    assert.ok(Math.abs(Math.abs(plane.parameters[0])-1)<=1e-12&&Math.hypot(plane.parameters[1],plane.parameters[2])<=1e-12&&Math.abs(Math.abs(plane.parameters[3])-8)<=1e-12);
    assert.ok(plane.weighted_rms<1e-12&&cylinder.weighted_rms<1e-6&&Math.abs(geometry.radius-2.5)<=1e-6);
    assert.ok(Math.abs(Math.abs(geometry.axis[0])-1)<=1e-6&&Math.hypot(geometry.axis[1],geometry.axis[2])<=1e-6&&Math.hypot(geometry.origin[1]+4,geometry.origin[2]-5)<=1e-6);
    const heights=sceneOracle.selections.cylinder.map(id=>sceneSource.positions[id].reduce((sum,value,i)=>sum+(value-geometry.origin[i])*geometry.axis[i],0));
    assert.ok(Math.abs(geometry.axial_domain[0]-Math.min(...heights))<=1e-12&&Math.abs(geometry.axial_domain[1]-Math.max(...heights))<=1e-12);
    result.cases.push({name:'generated-pointer-plane-cylinder',complete:true,exact_independent_memberships:true,actual_fit_dialogs_and_evaluation:true,independent_geometry_objective_support:true,automatic_support_and_seed_free_actions:true});

    result.evidence.draft_project_layout=[];
    for(const docked of [false,true]){
      result.phase=docked?'docked-dependent-work-guards':'popup-dependent-work-guards';const label='Keep '+result.phase;
      await pointDraft(page,docked,label);const prior=await coherent();
      const layout={host:docked?'docked':'popup',...await projectLayout(page)};result.evidence.draft_project_layout.push(layout);
      assert.equal(layout.center_reachable,true,'Project pointer command must remain reachable beside the draft');
      for(const name of ['New project','Remove scan…']){
        confirmations.push(true,false);await command(page,name);assert.equal(confirmations.length,0);
        await checkDraft(page,label);sameWork(await coherent(),prior);
      }
      await page.locator('[data-command-id="choose-example"]').click();
      assert.equal((await call(page,'listExamples')).active,null);await page.locator('#example-choice').selectOption('nozzle');assert.equal(await page.locator('#open-example').isEnabled(),true);
      confirmations.push(true,false);await page.locator('#open-example').click();assert.equal(confirmations.length,0);await checkDraft(page,label);sameWork(await coherent(),prior);
      await page.locator('#cancel-example').click();await page.locator('#new-point-label').focus();assert.equal(await page.locator('#new-point-label').evaluate(input=>input===document.activeElement),true);
      await page.locator('[data-command-id="choose-example"]').click();
      await page.locator('#example-dialog').waitFor({state:'visible'});await page.keyboard.press('Escape');
      await page.locator('#example-dialog').waitFor({state:'hidden'});await checkDraft(page,label);sameWork(await coherent(),prior);
      result.cases.push({name:result.phase,complete:true,live_dom_and_host_preserved:true,declined_new_remove_example_preserve_dependents:true,example_cancel_escape_preserves_work:true,focus_reachable:true});
      await page.locator('#point-dialog').getByRole('button',{name:'Cancel',exact:true}).click();
    }

    result.phase='accepted-dependent-removal';await pointDraft(page,true,'Keep draft until source removal accepts');const prior=await coherent();
    await arm();await command(page,'Remove scan…');await held();sameWork(await display(page),prior);await checkDraft(page,'Keep draft until source removal accepts');
    await release();const removed=await accepted(prior.generation);assert.deepEqual(removed.graph.recipe.nodes,[]);assert.deepEqual(removed.positions,[]);assert.deepEqual(removed.indices,[]);
    assert.equal(await page.locator('#point-dialog').isVisible(),false);await importFile('raw-nozzle');const different=await coherent();
    assert.equal(different.graph.recipe.nodes.length,1);assert.equal(different.metadata.source.source_sha256,rawSource.source_sha256);
    for(const id of prior.graph.recipe.nodes.filter(node=>node.operation!=='source').map(node=>node.id))assert.equal(different.graph.recipe.nodes.some(node=>node.id===id),false);
    result.cases.push({name:'accepted-dependent-removal',complete:true,old_work_and_draft_until_acceptance:true,dependents_removed_atomically:true,different_source_has_no_old_actions:true});

    result.phase='explicit-example-roundtrip';current=await coherent();assert.equal((await call(page,'listExamples')).active,null);
    await page.locator('[data-command-id="choose-example"]').click();await page.locator('#example-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#example-choice').inputValue(),'');
    await page.locator('#example-choice').selectOption('nozzle');assert.equal(await page.locator('#open-example').isEnabled(),true);await page.locator('#open-example').click();
    const example=await accepted(current.generation);assert.deepEqual(example.graph.recipe,exampleRecipe);assert.equal((await call(page,'listExamples')).active,'nozzle');
    assert.equal(example.graph.recipe.nodes[0].descriptor,null);assert.equal(await page.getByRole('button',{name:'Restore example',exact:true}).isEnabled(),true);
    await page.getByRole('button',{name:'Point',exact:true}).click();await page.locator('#new-point-label').fill('Committed example edit');
    await page.getByRole('button',{name:'Add point',exact:true}).click();await page.waitForFunction(()=>globalThis.__authoringDisplay243().graph.recipe.nodes.some(node=>node.label==='Committed example edit'));
    await page.getByRole('button',{name:'Restore example',exact:true}).click();await ready(page);
    await page.waitForFunction(recipe=>JSON.stringify(globalThis.__authoringDisplay243().graph.recipe)===JSON.stringify(recipe),exampleRecipe);
    assert.equal((await coherent()).generation,example.generation);await newProject();assert.equal((await call(page,'listExamples')).active,null);
    const emptyExampleBefore=await coherent();
    await page.locator('[data-command-id="choose-example"]').click();await page.locator('#example-choice').selectOption('nozzle');assert.equal(await page.locator('#open-example').isEnabled(),true);
    await page.locator('#cancel-example').click();sameWork(await coherent(),emptyExampleBefore);
    await page.locator('[data-command-id="choose-example"]').click();await page.locator('#example-choice').selectOption('nozzle');await page.locator('#open-example').click();
    const emptyExampleAccepted=await accepted(emptyExampleBefore.generation);assert.deepEqual(emptyExampleAccepted.graph.recipe,exampleRecipe);
    assert.equal((await call(page,'listExamples')).active,'nozzle');await newProject();await importFile('raw-nozzle');await sourceRows('raw-nozzle');
    assert.equal((await display(page)).graph.recipe.nodes.length,1);assert.equal(await page.getByRole('button',{name:'Restore example',exact:true}).isEnabled(),false);
    result.cases.push({name:'explicit-example-roundtrip',complete:true,empty_and_scan_catalogue_unselected:true,actual_chooser_accepts_example:true,retained_recipe_restore_preserved:true,return_to_user_source_honest:true});
    result.evidence.final=await coherent();assert.equal(await page.locator('#viewport canvas').count(),1);
    assert.equal((await readSourceEvidence(page,{arrays:false})).owned_fs_bytes,0);result.complete=true;result.phase='complete';return result;
  }finally{
    if(!result.complete)result.failure_snapshot=await page.evaluate(()=>({display:globalThis.__authoringDisplay243?.(),
      lifecycle:globalThis.__authoringBackend243?.getLifecycle(),status:document.querySelector('#status')?.textContent,
      prepared:window.__authoringTransport243?.prepared,held:window.__authoringTransport243?.held.length,
      graph_publications:window.__authoringTransport243?.graphs.slice(-3).map(graph=>({token:graph.token,revision:graph.revision,
        project_generation:graph.project_generation,recipe_present:!!graph.recipe,states:graph.states,
        evaluation_running:graph.evaluation_running,required_failures:graph.required_failures}))})).catch(error=>({error:String(error)}));
    await context.close();
  }
}
