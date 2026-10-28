// Real file choosers, reuse controls, accepted support and ordinary evaluation.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {sha256} from './server.mjs';

export const circularReuseUiObserverSuffix=`
globalThis.__circularReuseUi364=()=>({ready:!!viewerSetup,generation:workspaceGeneration,metadata,graph:graphState,
 inert:document.querySelector('main').inert,transition:sourceTransitionBusy,evaluating:!!activeGraphEvaluation,
 applying:authoringApplying||featurePropertiesApplying,
 preview:{cells:circularReusePreview?.userData.supportCells||0,occurrences:circularReusePreview?.userData.occurrences||0,
 instances:circularReusePreview?.children.map(child=>child.count)||[],
 matrices:circularReusePreview?.children[0]?Array.from(circularReusePreview.children[0].instanceMatrix.array):[]},
 workers:globalThis.__circularTransport364?.workers||0});
`;
export function installCircularTransportObserver(){
  const NativeWorker=globalThis.Worker,records=globalThis.__circularTransport364={workers:0,requests:[],holdPreview:false,held:[],
    release(){this.holdPreview=false;for(const deliver of this.held.splice(0))deliver();}};
  globalThis.Worker=class extends NativeWorker{
    constructor(...args){super(...args);this.circularWorker=++records.workers;
      this.addEventListener('message',event=>{
        const request=records.requests.find(item=>item.id===event.data.id&&item.worker===this.circularWorker);
        if(request&&event.data.ok){const value=event.data.result;request.result={token:value.token,
          generated_ids:value.generated_ids||value.reuse_authoring?.generated_ids,
          circular_preview:value.circular_preview,recipe:value.recipe};}
      });
    }
    set onmessage(callback){super.onmessage=event=>{
      const request=records.requests.find(item=>item.id===event.data.id&&item.worker===this.circularWorker);
      if(records.holdPreview&&request?.operation==='previewFeatureReuse'&&event.data.ok){
        if(records.held.length)throw Error('More than one circular preview reply was held');
        records.held.push(()=>callback?.(event));return;
      }
      callback?.(event);
    };}
    postMessage(message,...args){
      if(['previewFeatureReuse','applyFeatureReuse'].includes(message.operation))records.requests.push({id:message.id,worker:this.circularWorker,
        operation:message.operation,payload:structuredClone(message.payload)});
      return super.postMessage(message,...args);
    }
  };
}
export function circularUiFixture(python){
  const repository=fileURLToPath(new URL('../..',import.meta.url));
  return JSON.parse(execFileSync(python,['-m','experiments.browser_static.circular_reuse_expected'],{cwd:repository,
    env:{...process.env,PYTHONPATH:[repository,path.join(repository,'src')].join(path.delimiter),OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1'},encoding:'utf8',maxBuffer:2*1024**2}));
}
export const readCircularUi=page=>page.evaluate(()=>globalThis.__circularReuseUi364?.());
const quiet=state=>state?.ready&&!state.inert&&!state.transition&&!state.evaluating&&!state.applying;
async function until(page,predicate,phase){
  const deadline=Date.now()+(phase==='ready'?180000:30000);
  while(Date.now()<deadline){const state=await readCircularUi(page);if(predicate(state))return state;
    if(await page.locator('#startup-error').isVisible())throw Error(`Circular viewer startup failed: ${await page.locator('#startup-detail').textContent()}`);
    await page.waitForTimeout(40);}
  throw Error(`Circular UI timed out: ${phase}; ${await page.locator('#status').textContent()}; create=${await page.locator('#new-feature-reuse-preview-status').textContent()}; edit=${await page.locator('#feature-reuse-preview-status').textContent()}`);
}
async function choose(page,button,id,file){
  const [picker]=await Promise.all([page.waitForEvent('filechooser'),button.click()]);
  assert.equal(await picker.element().getAttribute('id'),id);await picker.setFiles(file);
}
async function closeEditor(page){
  if(await page.locator('#close-edit').isVisible())await page.locator('#close-edit').click();
  else{const tab=page.getByRole('tab',{name:'Edit',exact:true});if(await tab.count())await tab.getByTitle('Close',{exact:true}).click();}
}
async function reset(page){
  await closeEditor(page);await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name:'New project',exact:true}).click();
  return until(page,state=>quiet(state)&&state.graph.recipe.nodes.length===0,'empty project');
}
async function evaluate(page){
  await closeEditor(page);await page.getByRole('button',{name:'Evaluate all',exact:true}).click();
  return until(page,state=>quiet(state)&&!state.graph.evaluation_running&&Object.values(state.graph.states).every(value=>value==='ready'),'ordinary evaluation');
}
function children(state,owner){return new Map(state.graph.recipe.nodes.filter(node=>node.managed_by===owner).map(node=>[node.managed_key,node]));}
function physicalEvidence(state,owner,fixture){
  const owned=children(state,owner),radii=[state.graph.results.bore.analytic.radius];
  for(let occurrence=1;occurrence<5;occurrence++)for(const role of ['bore','chamfer']){
    const selection=owned.get(`selection/occurrence_${occurrence}/${role}/${role}_input`),fit=owned.get(`fit/occurrence_${occurrence}/${role}`);
    assert(selection&&fit);assert.equal(state.graph.states[fit.id],'ready');
    assert.deepEqual(state.graph.memberships[selection.id],fixture.expected_ids[occurrence][role]);
    assert.equal(state.graph.results[selection.id].occurrence,occurrence);
    assert.equal(state.graph.results[selection.id].region,undefined);
    assert(state.graph.results[fit.id].weighted_rms<1e-5);
    if(role==='bore')radii.push(state.graph.results[fit.id].analytic.radius);
  }
  radii.forEach((radius,index)=>assert(Math.abs(radius-(0.5+0.003*index))<2e-6));
  assert(new Set(radii.map(value=>value.toFixed(5))).size===5,'Occurrences must independently fit measured radius departures');
  assert(!state.graph.recipe.nodes.some(node=>['rotational_symmetry','build_faces','body'].includes(node.operation)));
  return{radii,exact_original_memberships:true,opposite_normal_sheet_excluded:true,independent_refits:true};
}
export async function exerciseCircularReuseUi({page,fixture,force=false,records=async()=>[],holdPreview}){
  const result={schema:'scansor-circular-reuse-ui-v1',complete:false,phase:'startup',cases:[],physical:null};
  try{
    let state=await until(page,quiet,'ready');await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
    if(state.graph.recipe.nodes.length)await reset(page);
    await choose(page,page.locator('[data-add-scan]'),'scan-file',{name:fixture.filename,mimeType:'application/octet-stream',buffer:Buffer.from(fixture.raw_base64,'base64')});
    await until(page,state=>quiet(state)&&state.metadata.vertices===936,'physical source');
    await choose(page,page.getByRole('button',{name:'Load actions',exact:true}),'file',
      {name:'source-only-circular-actions.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture.recipe))});
    const sourceOnly=await until(page,state=>quiet(state)&&state.graph.recipe.nodes.some(node=>node.id==='chamfer'),'source-only actions');
    assert(!sourceOnly.graph.recipe.nodes.some(node=>node.operation==='feature_reuse'));
    result.phase='draft-preview';
    await page.getByRole('button',{name:'Feature reuse',exact:true}).click();
    await page.locator('#new-feature-reuse-label').fill('Five physical holes');
    await page.locator('#new-feature-reuse-mode').selectOption('circular');
    await page.locator('#new-feature-reuse-fits').selectOption(['bore','chamfer']);
    await page.locator('#new-feature-reuse-axis').selectOption('pattern_axis');await page.locator('#new-feature-reuse-count').fill('5');
    for(const mapping of fixture.circular.volumes){
      await page.locator(`#new-feature-reuse-circular select[data-fit="${mapping.fit}"][data-source-selection="${mapping.source_selection}"]`)
        .selectOption(JSON.stringify(mapping.volume,Object.keys(mapping.volume).sort()));
    }
    if(holdPreview){
      const hold=await holdPreview();
      try{await page.locator('#new-feature-reuse-preview').click();await hold.arrived();
        await page.locator('#new-feature-reuse-circular').getByRole('button',{name:'Cancel preview',exact:true}).click();}
      finally{await hold.release();}
      assert.match(await page.locator('#new-feature-reuse-preview-status').textContent(),/cancelled/);
      assert.equal(await page.locator('#new-feature-reuse-count').inputValue(),'5');
      assert.deepEqual((await readCircularUi(page)).graph.recipe,sourceOnly.graph.recipe);
      result.cases.push({name:'cancel-preview',complete:true,draft_and_accepted_recipe_preserved:true,late_reply_inert:true});
    }
    await page.locator('#new-feature-reuse-preview').click();
    await until(page,state=>state.preview.cells>0&&state.preview.occurrences===5,'actual support preview');
    const previewed=await readCircularUi(page);assert.deepEqual(previewed.graph.recipe,sourceOnly.graph.recipe,'Preview allocated authored children');
    assert.deepEqual(previewed.preview.instances,[5,5,5]);
    const placementResponse=(await records()).filter(item=>item.operation==='previewFeatureReuse').at(-1).result.circular_preview;
    const placements=[{rotation:[[1,0,0],[0,1,0],[0,0,1]],translation:[0,0,0]},...Object.values(placementResponse.placements)];
    for(const[index,placement]of placements.entries()){
      const expected=placement.rotation.flatMap(row=>[...row,0]).concat([...placement.translation,1]);
      expected.forEach((value,offset)=>assert(Math.abs(previewed.preview.matrices[index*16+offset]-value)<2e-6,'Displayed occurrence transform differs from the reviewed rigid placement'));
    }
    await page.locator('#new-feature-reuse-occurrence').selectOption('2');
    await until(page,state=>state.preview.occurrences===1,'single named occurrence');
    assert.match(await page.locator('#new-feature-reuse-preview-status').textContent(),/Occurrence 3/);
    await page.locator('#new-feature-reuse-count').fill('4');
    await page.locator('#add-feature-reuse-form').getByRole('button',{name:'Reuse features',exact:true}).click();
    assert.match(await page.locator('#feature-reuse-error').textContent(),/Preview/);
    assert.equal(await page.locator('#new-feature-reuse-count').inputValue(),'4');
    await page.locator('#new-feature-reuse-count').fill('5');await page.locator('#new-feature-reuse-preview').click();
    await until(page,state=>state.preview.cells>0,'repreview after edit');
    result.phase='apply';
    await page.locator('#add-feature-reuse-form').getByRole('button',{name:'Reuse features',exact:true}).click();
    const authored=await until(page,state=>quiet(state)&&state.graph.recipe.nodes.some(node=>node.operation==='feature_reuse'),'apply circular');
    const owner=authored.graph.recipe.nodes.find(node=>node.operation==='feature_reuse');result.owner=owner.id;
    assert.deepEqual(owner.circular,fixture.circular);assert.equal(owner.equal_corresponding_dimensions,false);
    assert.equal(authored.graph.recipe.nodes.filter(node=>node.managed_by===owner.id).length,16);
    const calls=await records(),applied=calls.filter(item=>item.operation==='applyFeatureReuse').at(-1),
      reviewed=calls.filter(item=>item.operation==='previewFeatureReuse').at(-1);
    assert(applied&&reviewed);assert.equal(applied.payload.allocation_seed,reviewed.payload.allocation_seed);
    assert.equal(applied.payload.reuse_id,reviewed.payload.reuse_id);assert.deepEqual(applied.payload.changes,reviewed.payload.changes);
    assert.deepEqual(authored.graph.recipe,reviewed.result.recipe);
    result.cases.push({name:'reviewed-actual-support',complete:true,shared_geometry_instances:true,stable_preview_apply_ids:true,changed_draft_requires_preview:true});
    const evaluated=await evaluate(page);result.physical=physicalEvidence(evaluated,owner.id,fixture);
    result.phase='occurrence-inspection';
    const ownerRow=page.locator(`.managed-owner-summary[data-action-id="${owner.id}"]`);await ownerRow.dblclick();
    await until(page,state=>state.preview.cells>0,'accepted occurrence support');
    await page.locator('#feature-reuse-occurrence').selectOption('3');
    await until(page,state=>state.preview.occurrences===1,'accepted named occurrence');
    assert.match(await page.locator('#feature-reuse-preview-status').textContent(),/Occurrence 4/);
    assert(await page.locator('#action-list').getByText(/Occurrence 2 · 72°/).count());
    result.cases.push({name:'named-occurrence-inspection',complete:true,accepted_volume_support:true,measured_refits:true});
    result.phase='count-edit';
    await page.locator('#feature-reuse-count').fill('4');await page.locator('#feature-reuse-preview').click();
    await page.locator('#feature-reuse-occurrence').selectOption('all');
    await until(page,state=>state.preview.occurrences===4,'edited count preview');
    await page.locator('#apply-properties').click();
    const reduced=await until(page,state=>quiet(state)&&state.graph.recipe.nodes.find(node=>node.id===owner.id).circular.count===4,'edited count apply');
    for(const [key,node]of children(reduced,owner.id))assert.equal(children(evaluated,owner.id).get(key).id,node.id);
    await page.locator('#feature-reuse-count').fill('5');await page.locator('#feature-reuse-preview').click();
    await until(page,state=>state.preview.occurrences===5,'restore count preview');await page.locator('#apply-properties').click();
    await until(page,state=>quiet(state)&&state.graph.recipe.nodes.find(node=>node.id===owner.id).circular.count===5,'restore count');
    state=await evaluate(page);physicalEvidence(state,owner.id,fixture);
    result.cases.push({name:'count-edits',complete:true,surviving_roles_keep_ids:true});
    result.phase='source-fit-edits';
    const beforeSource=await readCircularUi(page),beforeIds=children(beforeSource,owner.id);
    await closeEditor(page);await page.locator('.action-select[data-action-id="bore"]').dblclick();
    await page.locator('#surface-kind').selectOption('cone');await page.locator('#apply-properties').click();
    const coneSource=await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='bore').kind==='cone','source kind edit');
    for(const[key,node]of children(coneSource,owner.id))if(key.startsWith('fit/')&&key.endsWith('/bore')){
      assert.equal(node.kind,'cone');assert.equal(node.id,beforeIds.get(key).id);
    }
    await page.locator('#surface-kind').selectOption('cylinder');await page.locator('#apply-properties').click();
    await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='bore').kind==='cylinder','restore source kind');
    await page.locator('#fit-inputs').selectOption(['bore_input','bore_extra_input']);
    const extraChoice=page.locator(`#fit-circular-volumes select[data-owner="${owner.id}"][data-source-selection="bore_extra_input"]`);
    assert.equal(await extraChoice.inputValue(),'','A unique source volume must still be explicitly selected');
    const priorAtomic=await readCircularUi(page);await page.locator('#apply-properties').click();
    assert.match(await page.locator('#status').textContent(),/Choose an accepted source volume/);
    assert.deepEqual((await readCircularUi(page)).graph.recipe,priorAtomic.graph.recipe);
    assert.deepEqual(await page.locator('#fit-inputs').evaluate(control=>[...control.selectedOptions].map(option=>option.value)),['bore_input','bore_extra_input']);
    await closeEditor(page);assert.deepEqual((await readCircularUi(page)).graph.recipe,priorAtomic.graph.recipe);
    await page.locator('.action-select[data-action-id="bore"]').dblclick();
    await page.locator('#fit-inputs').selectOption(['bore_input','bore_extra_input']);
    await extraChoice.selectOption(JSON.stringify(fixture.extra_volume,Object.keys(fixture.extra_volume).sort()));
    await page.locator('#apply-properties').click();
    const addedInput=await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='bore').selections.length===2,'atomic source input+volume apply');
    assert(addedInput.graph.recipe.nodes.find(node=>node.id===owner.id).circular.volumes.some(item=>item.fit==='bore'&&item.source_selection==='bore_extra_input'));
    for(let ordinal=1;ordinal<5;ordinal++)assert.equal(children(addedInput,owner.id).get(`fit/occurrence_${ordinal}/bore`).id,beforeIds.get(`fit/occurrence_${ordinal}/bore`).id);
    const withExtra=await evaluate(page);physicalEvidence(withExtra,owner.id,fixture);
    for(let ordinal=1;ordinal<5;ordinal++)assert.deepEqual(withExtra.graph.memberships[children(withExtra,owner.id).get(`selection/occurrence_${ordinal}/bore/bore_extra_input`).id],fixture.expected_ids[ordinal].bore);
    await page.locator('.action-select[data-action-id="bore"]').dblclick();await page.locator('#fit-inputs').selectOption('bore_input');
    await page.locator('#apply-properties').click();
    await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='bore').selections.length===1,'remove added source input');
    state=await evaluate(page);physicalEvidence(state,owner.id,fixture);
    result.cases.push({name:'atomic-source-edits',complete:true,source_kind_updates_copy_roles:true,explicit_new_input_mapping:true,
      missing_mapping_keeps_draft:true,cancel_keeps_accepted_recipe:true,surviving_ids_preserved:true});
    result.phase='axis-edit';
    await page.locator('.action-select[data-action-id="pattern_axis"]').dblclick();
    const x=await page.locator('#axis-point-x').inputValue();await page.locator('#axis-point-x').fill(String(Number(x)+0.002));
    await page.locator('#apply-properties').click();await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='pattern_axis').initial_parameters[0]!==Number(x),'axis edit');
    await page.locator('#axis-point-x').fill(x);await page.locator('#apply-properties').click();
    await until(page,value=>quiet(value)&&value.graph.recipe.nodes.find(node=>node.id==='pattern_axis').initial_parameters[0]===Number(x),'axis restore');
    state=await evaluate(page);physicalEvidence(state,owner.id,fixture);
    for(const[key,node]of children(state,owner.id))assert.equal(node.id,beforeIds.get(key).id);
    result.cases.push({name:'axis-edit',complete:true,ordinary_axis_edit_invalidates_dependents:true,stable_child_ids:true});
    result.phase='visible-equality-and-auto';
    await ownerRow.dblclick();await page.getByRole('checkbox',{name:'Auto',exact:true}).check();
    await page.locator('#feature-reuse-equal-dimensions').check();await page.locator('#feature-reuse-preview').click();
    await until(page,value=>value.preview.cells>0,'equality preview');await page.locator('#apply-properties').click();
    const equal=await until(page,value=>quiet(value)&&!value.graph.evaluation_running&&value.graph.recipe.nodes.some(node=>node.managed_by===owner.id&&node.operation==='equal_radii')
      &&Object.values(value.graph.states).every(status=>status==='ready'),'Auto equality evaluation');
    const relation=equal.graph.recipe.nodes.find(node=>node.managed_by===owner.id&&node.operation==='equal_radii');
    assert.equal(relation.surfaces.length,5);assert.equal(equal.graph.states[relation.id],'ready');
    assert(await page.locator('#action-list').getByText(/Relationships · 1/).count());
    await page.locator('#feature-reuse-equal-dimensions').uncheck();await page.locator('#feature-reuse-preview').click();
    await until(page,value=>value.preview.cells>0,'independent fit preview');await page.locator('#apply-properties').click();
    state=await until(page,value=>quiet(value)&&!value.graph.evaluation_running&&!value.graph.recipe.nodes.some(node=>node.id===relation.id)
      &&Object.values(value.graph.states).every(status=>status==='ready'),'Auto independent reevaluation');
    physicalEvidence(state,owner.id,fixture);await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();await closeEditor(page);
    result.cases.push({name:'visible-equality-and-auto',complete:true,ordinary_equal_radii_relationship:true,auto_evaluates_both_edits:true,independent_results_restored:true});
    result.phase='ordinary-downstream-and-protection';
    const lastFit=children(state,owner.id).get('fit/occurrence_4/bore').id;
    await page.getByRole('button',{name:'Axis',exact:true}).click();await page.locator('#new-axis-label').fill('Ordinary downstream axis');
    await page.locator('#new-axis-mode').selectOption('fit');await page.locator('#new-axis-source').selectOption(lastFit);await page.locator('#new-axis-clone').uncheck();
    await page.locator('#add-axis-form').getByRole('button',{name:'Add axis',exact:true}).click();
    const linked=await until(page,value=>quiet(value)&&value.graph.recipe.nodes.some(node=>node.label==='Ordinary downstream axis'),'ordinary downstream feature');
    const downstream=linked.graph.recipe.nodes.find(node=>node.label==='Ordinary downstream axis');assert.equal(downstream.source_fit,lastFit);
    state=await evaluate(page);assert.equal(state.graph.states[downstream.id],'ready');
    await ownerRow.dblclick();await page.locator('#feature-reuse-count').fill('4');await page.locator('#feature-reuse-preview').click();
    await page.waitForFunction(()=>/Cannot remove|Ordinary downstream axis/.test(document.querySelector('#feature-reuse-preview-status').textContent));
    assert.equal(await page.locator('#feature-reuse-count').inputValue(),'4');assert.deepEqual((await readCircularUi(page)).graph.recipe,state.graph.recipe);
    await closeEditor(page);
    result.cases.push({name:'ordinary-downstream-and-protection',complete:true,generated_fit_is_ordinary_operand:true,protected_removal_keeps_draft_and_recipe:true});
    result.phase='save-and-reopen';
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save actions',exact:true}).click()]);
    const saved=await readFile(await download.path()),recipe=JSON.parse(saved);assert.deepEqual(recipe,state.graph.recipe);
    await choose(page,page.getByRole('button',{name:'Load actions',exact:true}),'file',{name:'renamed-circular-actions.json',mimeType:'application/json',buffer:saved});
    const reopened=await until(page,value=>quiet(value)&&value.generation===state.generation+1,'reopen actions');
    assert.deepEqual(reopened.graph.recipe,recipe);await evaluate(page);
    result.cases.push({name:'actions-reopen',complete:true,definitions_and_generated_ids_preserved:true});
    if(force){const before=await readCircularUi(page);await page.locator('#force-restart').click();
      await until(page,value=>quiet(value)&&value.workers>before.workers&&value.generation===before.generation,'Force');
      assert.deepEqual((await readCircularUi(page)).graph.recipe,recipe);const recovered=await evaluate(page);physicalEvidence(recovered,owner.id,fixture);
      result.cases.push({name:'worker-force',complete:true,exact_authority_and_definitions:true});}
    result.complete=true;result.phase='complete';return result;
  }catch(error){result.failure=String(error);result.snapshot=await readCircularUi(page).catch(()=>null);error.circularReuseReport=result;throw error;}
}

export async function validateCircularReuseUi({browser,site,preview,prefix,isolation,report,nativePython}){
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  await context.addInitScript(installCircularTransportObserver);
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);});
  await context.route('**/app.js',async route=>{
    const relative=new URL(route.request().url()).pathname.slice(prefix.length),record=site.records.get(relative);assert(record);
    const bytes=await readFile(path.join(site.root,relative));assert.equal(sha256(bytes),record.sha256);
    return route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(circularReuseUiObserverSuffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  const page=await context.newPage();page.setDefaultTimeout(30000);page.on('dialog',dialog=>dialog.accept());
  page.on('pageerror',error=>report.page_errors.push(String(error)));
  try{
    await page.goto(preview.origin+prefix+'app/');
    report.circular_reuse=await exerciseCircularReuseUi({page,fixture:circularUiFixture(nativePython),force:true,
      records:()=>page.evaluate(()=>globalThis.__circularTransport364.requests),holdPreview:async()=>{
        await page.evaluate(()=>{globalThis.__circularTransport364.holdPreview=true;});
        return{arrived:()=>page.waitForFunction(()=>globalThis.__circularTransport364.held.length===1),
          release:()=>page.evaluate(()=>globalThis.__circularTransport364.release())};
      }});
    assert.equal(await page.evaluate(()=>crossOriginIsolated),isolation);
  }catch(error){report.circular_reuse=error.circularReuseReport||{complete:false,failure:String(error)};throw error;}
  finally{await context.close();}
}
