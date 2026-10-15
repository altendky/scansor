// Actual File -> shipped worker -> shared Python, without computation HTTP.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {verifySite,startPreview,prepareReports} from './server.mjs';
import {prepareSourceInputs,runSourcePython,openSourceImportPage,chooseSourceFile,
  readSourceEvidence,compareSourceNumbers} from './source-import-harness.mjs';
import {validateSourceUiRecovery} from './source-ui-recovery.mjs';
import {compareSemanticBatch} from './semantic-parity.mjs';
import {SOURCE_GRAPH_SCHEMA,SOURCE_GRAPH_PROJECTION,assertAcceptedSourceGraph,sourceSemanticCases} from './source-semantic.mjs';

const args=process.argv.slice(2),argument=(name,fallback)=>{
  const index=args.indexOf(name);if(index<0)return fallback;
  if(!args[index+1]||args[index+1].startsWith('--'))throw Error('Missing '+name);return args[index+1];
};
const directory=argument('--directory'),destination=argument('--output');
if(!directory||!destination)throw Error('Usage: validate-source-import.mjs --directory <fresh built site> --output <fresh reports> [--isolation]');
const site=await verifySite(directory), playwrightDirectory=path.resolve(argument('--playwright-directory','experiments/browser_viewer'));
const repository=path.resolve(argument('--repository',path.join(playwrightDirectory,'../..')));
const output=await prepareReports(destination,[site.root,repository]);
const nativePython=path.resolve(argument('--native-python',path.join(repository,'.venv/bin/python')));
const isolation=args.includes('--isolation'),prefix='/source-import/nested/';
const report={schema_version:1,kind:'scansor-worker-source-import',started_utc:new Date().toISOString(),
  manifest_sha256:site.manifest_sha256,source_revision:site.manifest.source_revision,source_worktree_dirty:site.manifest.source_worktree_dirty,
  runtime:site.manifest.runtime,isolation_requested:isolation,cases:[],checks:{},requests:[],unexpected_requests:[],api_requests:[],page_errors:[],native_parity_errors:[],
  source_graph_schema:SOURCE_GRAPH_SCHEMA,source_graph_projection:SOURCE_GRAPH_PROJECTION,source_graph_snapshots:{},complete:false};
let browser,preview;
try{
  const sources=path.join(output,'inputs');report.inputs=await prepareSourceInputs({repository,output:sources,nativePython});
  const runtime=path.join(site.root,site.manifest.runtime.path),expectedPath=path.join(output,'native-source-results.json');
  await runSourcePython(nativePython,['expected','--runtime',runtime,'--inputs',sources,'--output',expectedPath],output);
  const expected=JSON.parse(await readFile(expectedPath,'utf8'));
  report.source_graph_snapshots.native_scene=expected['horizontal-scene'].graph;
  const playwright=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
  preview=await startPreview(site,{prefix,isolation});browser=await playwright.chromium.launch({headless:true});
  report.browser={name:'chromium',version:browser.version()};
  const context=await browser.newContext({serviceWorkers:'block'});
  context.on('request',request=>{const url=request.url();report.requests.push(url);
    if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);
    if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.api_requests.push(url);});
  const page=await context.newPage();page.on('pageerror',error=>report.page_errors.push(String(error)));
  report.instrumentation=await openSourceImportPage(page,{site,preview,prefix,isolation,captureTransport:true});
  report.isolation_actual={page:await page.evaluate(()=>globalThis.crossOriginIsolated),
    worker:await page.workers()[0].evaluate(()=>globalThis.crossOriginIsolated)};
  assert.deepEqual(report.isolation_actual,{page:isolation,worker:isolation});
  const readEvidence=async options=>{
    const result=await readSourceEvidence(page,options);
    assert.equal(await page.workers()[0].evaluate(()=>globalThis.crossOriginIsolated),isolation);
    return result;
  };
  for(const name of ['raw-nozzle','horizontal-scene']){
    await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();
      if(m.metadata.source)await b.resetProject({generation:m.generation,token:m.graph.token,confirmedDiscard:true});});
    await chooseSourceFile(page,path.join(sources,name+'.ply'));
    const imported=await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();
      return b.addScan({file:window.__sourceImportFile242,generation:m.generation,token:m.graph.token},
        {onProgress:record=>window.__sourceImportProgress242.push(record)});});
    const bundle=await page.evaluate(()=>window.__sourceImportBackend242.loadWorkspace());
    assert.equal(bundle.generation,bundle.metadata.project_generation);assert.equal(bundle.graph.project_generation,bundle.generation);
    assert.equal(bundle.graph.recipe.nodes.length,1);assert.equal(bundle.graph.recipe.nodes[0].operation,'source');
    const actual=await readEvidence(),reference=expected[name];
    assert.deepEqual(actual.descriptor,reference.descriptor);
    const sourceParity={};for(const key of ['positions','normals','triangles','weights'])sourceParity[key]=compareSourceNumbers(actual[key],reference[key],name+'.'+key);
    if(name==='horizontal-scene'){
      const oracle=JSON.parse(await readFile(path.join(sources,'horizontal-scene.expected.json'),'utf8'));
      for(const key of ['positions','normals','triangles','weights'])compareSourceNumbers(actual[key],oracle[key],'independent.scene.'+key);
      const graph=await page.evaluate(async recipe=>{const b=window.__sourceImportBackend242,current=await b.getGraph();
        const edited=await b.replaceRecipe({token:current.token,recipe});await b.evaluateGraph({token:edited.token,all_actions:true});
        let last;for await(const state of b.graphUpdates())last=state;return last;},reference.graph.recipe);
      report.source_graph_snapshots.scene=graph;
      await writeFile(path.join(output,'actual-scene-graph.json'),JSON.stringify(graph,null,2)+'\n');
      assertAcceptedSourceGraph(graph,{generation:bundle.generation,recipe:reference.graph.recipe,token:reference.graph.token});
      assert.equal(graph.states.cylinder,'ready');assert.equal(graph.states.plane,'ready');
      // Same independent generated-scene tolerances as native HTTP acceptance.
      assert.ok(Math.abs(graph.results.cylinder.analytic.radius-2.5)<=1e-6);
      graph.results.cylinder.analytic.axis.forEach((value,index)=>assert.ok(Math.abs(Math.abs(value)-(index===0?1:0))<=1e-6));
      assert.ok(graph.results.cylinder.weighted_rms<1e-6);assert.ok(graph.results.plane.weighted_rms<1e-12);
      if(site.manifest.capabilities.cad){
        const bytes=await page.evaluate(async()=>{const b=window.__sourceImportBackend242,g=await b.getGraph();
          return [...new Uint8Array(await b.exportCad({token:g.token,target:'cylinder',units:'Millimeters',axis_up:false,include_mesh:false}))];});
        assert.ok(bytes.length>0);await writeFile(path.join(output,'scene-cad.zip'),Buffer.from(bytes));
        await runSourcePython(nativePython,['cad','--runtime',path.join(output,'scene-cad.zip'),'--inputs',expectedPath,'--output',path.join(output,'scene-step-readback.json')],output);
        sourceParity.step_readback=JSON.parse(await readFile(path.join(output,'scene-step-readback.json'),'utf8'));
      }
    }
    const acceptedGraph=await page.evaluate(()=>window.__sourceImportBackend242.getGraph());
    if(name==='horizontal-scene'){
      // getGraph is a checkpoint snapshot, not a closure-readiness response.
      assert.equal(acceptedGraph.project_generation,bundle.generation);
      assert.deepEqual(acceptedGraph.recipe,report.source_graph_snapshots.scene.recipe);
      assert.equal(acceptedGraph.token,report.source_graph_snapshots.scene.token);
    }
    await page.evaluate(()=>window.__sourceImportBackend242.forceRestart());
    const recovered=await readEvidence();assert.deepEqual(recovered.descriptor,actual.descriptor);
    for(const key of ['positions','normals','triangles','weights'])compareSourceNumbers(recovered[key],actual[key],name+'.force.'+key);
    assert.equal(recovered.source_mode.kind,'scan');assert.equal(recovered.project_generation,bundle.generation);
    assert.equal(recovered.owned_fs_bytes,0);
    const replay=await page.evaluate(()=>window.__sourceImportBackend242.getGraph());
    assert.deepEqual(replay.recipe,acceptedGraph.recipe);
    assert.equal(replay.token,acceptedGraph.token);assert.equal(replay.project_generation,bundle.generation);
    if(name==='horizontal-scene'){
      assert.equal(replay.states.cylinder,'unevaluated');assert.equal(replay.states.plane,'unevaluated');
      assert.equal(replay.results.cylinder,undefined);assert.equal(replay.results.plane,undefined);
      const refitted=await page.evaluate(async()=>{const b=window.__sourceImportBackend242,g=await b.getGraph();
        await b.evaluateGraph({token:g.token,all_actions:true});let result;for await(const state of b.graphUpdates())result=state;return result;});
      report.source_graph_snapshots.scene_replayed=refitted;
      await writeFile(path.join(output,'actual-scene-replayed-graph.json'),JSON.stringify(refitted,null,2)+'\n');
      assertAcceptedSourceGraph(refitted,{generation:recovered.project_generation,recipe:acceptedGraph.recipe,token:acceptedGraph.token});
      assert.equal(refitted.states.cylinder,'ready');assert.equal(refitted.states.plane,'ready');
    }
    report.cases.push({name,source_sha256:actual.source_sha256,vertices:actual.vertices,faces:actual.faces,
      generation:bundle.generation,source_parity:sourceParity,force_reconstructed:true,authored_recipe_preserved:true,
      derived_results_cleared:name==='horizontal-scene',owned_fs_bytes:recovered.owned_fs_bytes,operation_status:imported.status});
  }
  // Invalid source and cancellation operate against an explicit empty project.
  await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();
    await b.resetProject({generation:m.generation,token:m.graph.token,confirmedDiscard:true});});
  const before=await page.evaluate(()=>window.__sourceImportBackend242.loadWorkspace());
  const invalid=path.join(output,'invalid.ply');await writeFile(invalid,'not a PLY');await chooseSourceFile(page,invalid);
  const rejected=await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();
    try{await b.addScan({file:window.__sourceImportFile242,generation:m.generation,token:m.graph.token});return false;}catch{return true;}});
  assert.equal(rejected,true);
  await chooseSourceFile(page,path.join(sources,'horizontal-scene.ply'));
  await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();let cancellation;
    try{await b.addScan({file:window.__sourceImportFile242,generation:m.generation,token:m.graph.token},{onProgress:record=>{
      if(record.operationId&&!cancellation)cancellation=b.cancelSourceOperation({operationId:record.operationId,generation:m.generation,token:m.graph.token});}});}catch{}
    if(!cancellation)throw Error('Import never exposed cancellation identity');await cancellation;});
  const after=await page.evaluate(()=>window.__sourceImportBackend242.loadWorkspace());
  assert.equal(after.generation,before.generation);assert.deepEqual(after.graph.recipe,before.graph.recipe);
  await page.evaluate(()=>window.__sourceImportBackend242.forceRestart());
  const empty=await readEvidence({arrays:false});assert.equal(empty.source_mode.kind,'empty');assert.equal(empty.owned_fs_bytes,0);
  report.boundary_races=[];
  for(const kind of ['source-candidate','transfer-confirmed']){
    await chooseSourceFile(page,path.join(sources,'horizontal-scene.ply'));
    await page.evaluate(async kind=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace(),t=window.__sourceImportTransport242;
      t.holdIncoming=kind;t.heldRequest=null;window.__sourceHeldCursor242={generation:m.generation,token:m.graph.token};
      window.__sourceHeldResult242=b.addScan({file:window.__sourceImportFile242,...window.__sourceHeldCursor242},
        {onProgress:record=>{window.__sourceHeldOperation242=record.operationId;}}).then(()=>({accepted:true}),error=>({code:error.code}));
    },kind);
    await page.waitForFunction(kind=>window.__sourceImportTransport242.held.some(item=>item.kind===kind),kind,{timeout:30000});
    const cancelled=await page.evaluate(async()=>{const b=window.__sourceImportBackend242;
      await b.cancelSourceOperation({operationId:window.__sourceHeldOperation242,...window.__sourceHeldCursor242});
      const result=await window.__sourceHeldResult242,t=window.__sourceImportTransport242;
      const status=await b.getSourceOperation({operationId:window.__sourceHeldOperation242});
      t.holdIncoming=null;t.release();return{status,result,cursor:window.__sourceHeldCursor242};});
    assert.equal(cancelled.status.status,'cancelled');assert.notEqual(cancelled.result.accepted,true);
    const state=await page.evaluate(()=>window.__sourceImportBackend242.loadWorkspace());
    assert.equal(state.generation,cancelled.cursor.generation);assert.deepEqual(state.graph.recipe.nodes,[]);
    assert.equal((await readEvidence({arrays:false})).owned_fs_bytes,0);
    report.boundary_races.push({position:kind,cancelled_before_adoption:true,late_replies_ignored:true});
  }
  for(const position of ['promotion-authorization','promotion-acknowledgement']){
    await chooseSourceFile(page,path.join(sources,'horizontal-scene.ply'));
    await page.evaluate(async position=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace(),t=window.__sourceImportTransport242;
      t.holdPromotion=position==='promotion-authorization';t.holdIncoming=position==='promotion-acknowledgement'?'source-promoted':null;
      window.__sourceHeldCursor242={generation:m.generation,token:m.graph.token};
      window.__sourceHeldResult242=b.addScan({file:window.__sourceImportFile242,...window.__sourceHeldCursor242},
        {onProgress:record=>{window.__sourceHeldOperation242=record.operationId;}}).then(()=>({accepted:true}),error=>({code:error.code}));
    },position);
    await page.waitForFunction(()=>window.__sourceImportTransport242.held.some(item=>item.kind==='accept-source-boundary'||item.kind==='source-promoted'),null,{timeout:30000});
    const adopted=await page.evaluate(async()=>{const b=window.__sourceImportBackend242;
      const status=await b.getSourceOperation({operationId:window.__sourceHeldOperation242});
      const cancelled=await b.cancelSourceOperation({operationId:window.__sourceHeldOperation242,...window.__sourceHeldCursor242});
      const t=window.__sourceImportTransport242;t.holdPromotion=false;t.holdIncoming=null;t.heldRequest=null;
      await b.forceRestart();await window.__sourceHeldResult242;return{status,cancelled};});
    assert.equal(adopted.status.status,'accepted');assert.equal(adopted.cancelled.status,'accepted');
    const restored=await readEvidence();assert.equal(restored.source_mode.kind,'scan');
    assert.deepEqual(restored.descriptor,expected['horizontal-scene'].descriptor);
    assert.equal(restored.project_generation,adopted.status.acceptedGeneration);
    for(const key of ['positions','normals','triangles','weights'])compareSourceNumbers(restored[key],expected['horizontal-scene'][key],'adopted.force.'+key);
    assert.equal(restored.owned_fs_bytes,0);
    report.boundary_races.push({position,accepted_checkpoint_survived_force:true});
    await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();await b.resetProject({generation:m.generation,token:m.graph.token,confirmedDiscard:true});});
  }
  // Cleanup failure is a fatal worker boundary, even if Cancel was requested.
  await chooseSourceFile(page,path.join(sources,'horizontal-scene.ply'));
  await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();
    await b.addScan({file:window.__sourceImportFile242,generation:m.generation,token:m.graph.token});});
  const checkpoint=await readEvidence();
  await page.workers()[0].evaluate(()=>globalThis.__sourceCleanupFault242());
  await chooseSourceFile(page,path.join(sources,'raw-nozzle.ply'));
  const cleanupFailure=await page.evaluate(async()=>{const b=window.__sourceImportBackend242,m=await b.loadWorkspace();let cancellation,operationId;
    const result=await b.addScan({file:window.__sourceImportFile242,generation:m.generation,token:m.graph.token},{onProgress:record=>{
      operationId=record.operationId;
      if(record.phase==='reading'&&record.completed>0&&!cancellation)cancellation=b.cancelSourceOperation({operationId,generation:m.generation,token:m.graph.token});
    }}).then(()=>({accepted:true}),error=>({code:error.code}));
    if(!cancellation)throw Error('Cleanup fault never observed reading Cancel');
    const cancelled=await cancellation;return{result,cancelled,status:await b.getSourceOperation({operationId})};});
  assert.equal(cleanupFailure.result.code,'source_cleanup_failed');
  assert.equal(cleanupFailure.cancelled.status,'cancelling');assert.equal(cleanupFailure.status.status,'failed');
  assert.equal(cleanupFailure.status.error.code,'source_cleanup_failed');
  await page.waitForFunction(()=>window.__sourceImportLifecycle242.some(state=>state.state==='failed'),null,{timeout:30000});
  await page.evaluate(()=>window.__sourceImportBackend242.forceRestart());
  const cleanRecovery=await readEvidence();assert.deepEqual(cleanRecovery.descriptor,checkpoint.descriptor);
  assert.equal(cleanRecovery.project_generation,checkpoint.project_generation);assert.equal(cleanRecovery.owned_fs_bytes,0);
  for(const key of ['positions','normals','triangles','weights'])compareSourceNumbers(cleanRecovery[key],checkpoint[key],'cleanup.force.'+key);
  report.boundary_races.push({position:'staging-cleanup',failure_preserved_through_cancel:true,accepted_checkpoint_survived_force:true});
  await page.evaluate(()=>window.__sourceImportBackend242.close());await context.close();
  await validateSourceUiRecovery({browser,site,preview,prefix,sources,isolation,report});
  report.checks={source_import:true,source_identity:true,recovery:true,cleanup:true,
    cancel_before_adoption:true,force_after_adoption:true,cleanup_failure_recovery:true,isolation_matches:true,
    ui_source_recovery:report.ui_recovery.complete===true,ui_legacy_startup:report.ui_legacy_startup.complete===true};
  // Compare only after the actual recovery/UI observations. The reviewed bridge
  // retains the full operands and failed receipt before a parity error escapes.
  try{
    const receipt=await compareSemanticBatch({cases:sourceSemanticCases(report.source_graph_snapshots),nativePython,site,output,report});
    const parity=report.cases.find(item=>item.name==='horizontal-scene').source_parity;
    parity.graph=receipt.cases[0];parity.replayed_graph=receipt.cases[1];
  }catch(error){report.native_parity_errors.push(String(error));}
  assert.deepEqual(report.unexpected_requests,[]);assert.deepEqual(report.api_requests,[]);assert.deepEqual(report.page_errors,[]);
  assert.deepEqual(report.native_parity_errors,[],'Complete native/worker parity is required');
  report.complete=true;
}catch(error){report.failure=String(error);throw error;}
finally{
  if(browser)await browser.close();if(preview){report.static_requests=preview.requests;await preview.close();}
  try{report.artifact_unchanged=(await verifySite(directory)).manifest_sha256===site.manifest_sha256;}catch{report.artifact_unchanged=false;}
  report.finished_utc=new Date().toISOString();await writeFile(path.join(output,'source-import.json'),JSON.stringify(report,null,2)+'\n');
  if(!report.artifact_unchanged)throw Error('Source import changed artifact');
}
