// Observational acceptance for the actual shipped worker product UI.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {readFile,writeFile,rename,readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {prepareReports,sha256,startPreview,verifySite} from './server.mjs';
import {prepareSourceInputs} from './source-import-harness.mjs';
import {appSuffix,backendSuffix,workerSuffix,installMainObservers,
  backendObserverMarker,backendObserverInsertion,instrumentWorkerBackend} from './worker-source-resource-observers.mjs';
import {validateWorkerResourceReport,assertTrustedInputsUnchanged,assertWorkerInventoryQuiescent} from './worker-source-resource-validation.mjs';

const args=process.argv.slice(2),argument=(name,fallback)=>{
  const i=args.indexOf(name);if(i<0)return fallback;
  assert.ok(args[i+1]&&!args[i+1].startsWith('--'),'Missing '+name);return args[i+1];
};
assert.ok(argument('--directory')&&argument('--output'),'Required --directory --output');
const repository=fileURLToPath(new URL('../..',import.meta.url));
const playwrightDirectory=path.resolve(argument('--playwright-directory',path.join(repository,'experiments/browser_viewer')));
const nativePython=path.resolve(argument('--native-python',path.join(repository,'.venv/bin/python')));
const site=await verifySite(argument('--directory'));
const output=await prepareReports(argument('--output'),[site.root,repository]);
process.env.TMPDIR=output;
const {chromium}=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
const sourceNames=['raw-nozzle','horizontal-scene','grid-512x256'];
const report={schema_version:1,kind:'scansor-worker-product-ui-resources',complete:false,
  started_utc:new Date().toISOString(),source_revision:site.manifest.source_revision,
  source_worktree_dirty:site.manifest.source_worktree_dirty,manifest_sha256:site.manifest_sha256,
  runtime:site.manifest.runtime,cycles:2,source_names:sourceNames,modes:[],
  host:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpus:os.cpus().length,node:process.version},
  instrumentation:{driver_sha256:sha256(await readFile(fileURLToPath(import.meta.url))),
    app_suffix_sha256:sha256(Buffer.from(appSuffix+backendSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix)),
    main_observer_sha256:sha256(Buffer.from(installMainObservers.toString())),
    backend_observer_marker_sha256:sha256(Buffer.from(backendObserverMarker)),
    backend_observer_insertion_sha256:sha256(Buffer.from(backendObserverInsertion)),
    scope:'Actual shipped app/bootstrap and singleton worker; two ordinary raw/scene/within-cap-grid cycles per plain/isolated page, separately labelled explicit grid Force and source-aware reopen. No fit/CAD concurrency, capacity change or performance threshold.',
    retention:'WeakMap File serials and scalar histories only. A unique-marker test-route closure observer reads the actual current outside-worker checkpoint and active operation; the File identity function is exposed only during that observation and deleted in finally. No retained File, Worker, geometry, array or buffer history or GC/reachability claim. Ordinary cycles keep the same worker; explicit Force replacement is separately observed.',
    accounting:'File.size, immutable original bytes, NumPy roots, Wasm capacity, CDP heap/backing and logical WebGL allocations overlap and MUST NOT be summed. Logical WebGL omits physical driver/GPU costs.',
    sampling:'Worker/CDP observations run only at quiescent boundaries. File-selected stage is main-only with worker observations explicitly unavailable while source RPC is active. Passive heartbeat spans imports; synchronous worker peaks are not observed.',
    overhead:'Stage hashing, NumPy tobytes, WebCrypto, CDP and routing add overhead and possible transient copies. Import-to-visible observation includes browser delivery and readiness polling; no OS-picker/presentation/isolated parser latency claim.',
    reclamation:'No forced GC, zero-GPU-object requirement, Wasm shrink guarantee, process peak or leak verdict.'}};
let browser;
const watchdog=setTimeout(()=>{report.failure='Worker UI observation exceeded 20 minutes';void browser?.close();},1200000);
async function checkpoint(){
  const target=path.join(output,'worker-source-ui-progress.json'),temporary=target+'.tmp';
  await writeFile(temporary,JSON.stringify(report,null,2)+'\n');await rename(temporary,target);
}
const safeRelative=value=>typeof value==='string'&&!path.isAbsolute(value)&&!value.split('/').some(part=>!part||part==='.'||part==='..')&&!/[\\\0]/.test(value);
async function assertSiteInventory(){
  const files=[];const walk=async relative=>{
    for(const entry of await readdir(path.join(site.root,relative),{withFileTypes:true})){
      const name=relative?relative+'/'+entry.name:entry.name;assert.ok(!entry.isSymbolicLink(),'Site symlink appeared');
      if(entry.isDirectory())await walk(name);else{assert.ok(entry.isFile(),'Unexpected site object');files.push(name);}
    }
  };
  await walk('');assert.deepEqual(files.sort(),[...site.records.keys(),'manifest.json'].sort(),'Site inventory changed');
}
async function freezeInputs(){
  await assertSiteInventory();
  assert.ok(safeRelative(site.manifest.runtime.path),'Unsafe runtime manifest path');
  const runtimeRaw=await readFile(path.join(site.root,site.manifest.runtime.path,'manifest.json'));
  assert.equal(sha256(runtimeRaw),site.manifest.runtime.manifest_sha256);
  const runtime=JSON.parse(runtimeRaw);assert.equal(site.manifest.source_worktree_dirty,false);assert.equal(runtime.source_worktree_dirty,false);
  assert.equal(runtime.source_revision,site.manifest.source_revision);
  const files=new Map(),add=async(relative,expected)=>{
    assert.ok(safeRelative(relative),'Unsafe trusted source path');const digest=sha256(await readFile(path.join(repository,relative)));
    if(expected)assert.equal(digest,expected,'Artifact producer differs from checkout: '+relative);
    if(files.has(relative))assert.equal(files.get(relative),digest);files.set(relative,digest);
  };
  const generated=[];
  for(const record of runtime.source_files)await add(record.path,record.sha256);
  for(const record of site.manifest.source_files){
    assert.ok(safeRelative(record.path),'Unsafe site source path');
    if(record.path.includes('/node_modules/')||record.path.includes('/dist/'))generated.push(record);
    else await add(record.path,record.sha256);
  }
  for(const record of site.manifest.build_inputs)await add(record.path,record.sha256);
  for(const relative of [
    'experiments/browser_static/measure-worker-source-ui.mjs','experiments/browser_static/worker-source-resource-observers.mjs',
    'experiments/browser_static/worker-source-resource-validation.mjs','experiments/browser_static/worker_source_resource_expected.py',
    'experiments/browser_static/source-import-harness.mjs','experiments/browser_static/source_import_expected.py',
    'experiments/browser_static/server.mjs','tests/native_scan_fixture.py',
    '.github/workflows/reflow-browser.yml','docs/src/project/planning/worker-scan-import.md',
  ])await add(relative);
  return{complete:true,source_files_match:true,artifact_source_revision:site.manifest.source_revision,
    runtime_source_revision:runtime.source_revision,runtime_source_worktree_dirty:runtime.source_worktree_dirty,
    source_limits:runtime.worker_protocol.source_import.limits,
    checkout_revision:execFileSync('git',['rev-parse','HEAD'],{cwd:repository,encoding:'utf8'}).trim(),
    checkout_dirty:!!execFileSync('git',['status','--porcelain'],{cwd:repository,encoding:'utf8'}).trim(),
    runtime_source_records:runtime.source_files.length,site_source_records:site.manifest.source_files.length,
    files:[...files].map(([path,sha256])=>({path,sha256})),artifact_generated_sources:generated,
    generated_scope:'Generated/vendor files are protected by the verified artifact asset inventory and unchanged package/build inputs; no checkout-generated output is substituted.',
    frozen_input_files:[]};
}
async function assertFrozenInputs(){
  const files=await Promise.all(report.source_guard.files.map(async record=>({path:record.path,
    sha256:sha256(await readFile(path.join(repository,record.path)))})));
  assertTrustedInputsUnchanged(report.source_guard.files,files);
  for(const input of report.source_guard.frozen_input_files)assert.equal(sha256(await readFile(input.path)),input.sha256,'Frozen source/reference changed');
  assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:repository,encoding:'utf8'}).trim(),report.source_guard.checkout_revision,'Checkout changed during measurement');
  report.source_guard.rehashed_unchanged=true;
}

async function reference(inputs){
  const helper=fileURLToPath(new URL('./worker_source_resource_expected.py',import.meta.url));
  report.instrumentation.reference_helper_sha256=sha256(await readFile(helper));
  const destination=path.join(output,'native-source-resource-reference.json');
  await new Promise((resolve,reject)=>{
    const child=spawn(nativePython,['-I',helper,'--runtime',path.join(site.root,site.manifest.runtime.path),
      '--inputs',inputs,'--output',destination],{cwd:output,
      env:{...process.env,OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1',MKL_NUM_THREADS:'1',PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
    let log='',expired=false;const collect=value=>{log=(log+value).slice(-12000);};
    child.stdout.on('data',collect);child.stderr.on('data',collect);
    const timer=setTimeout(()=>{expired=true;child.kill();},180000);
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('close',(code,signal)=>{clearTimeout(timer);code===0&&!expired?resolve():reject(Error(`Source reference (${code},${signal}): ${log}`));});
  });
  const bytes=await readFile(destination);report.reference_sha256=sha256(bytes);return JSON.parse(bytes);
}

async function collectResources(context,page){
  const cdp=await context.newCDPSession(page),targets=new Map(),pending=new Map();let next=0;
  cdp.on('Target.attachedToTarget',event=>{if(event.targetInfo.type==='worker')targets.set(event.sessionId,event.targetInfo);});
  cdp.on('Target.detachedFromTarget',event=>targets.delete(event.sessionId));
  cdp.on('Target.receivedMessageFromTarget',event=>{const value=JSON.parse(event.message),done=pending.get(value.id);
    if(done){pending.delete(value.id);done(value);}});
  await cdp.send('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:false,flatten:false});
  const workerHeap=async sessionId=>{
    let timer;const id=++next;
    try{return await new Promise((resolve,reject)=>{
      timer=setTimeout(()=>{pending.delete(id);reject(Error('Worker heap observation timed out'));},5000);
      pending.set(id,reply=>reply.error?reject(Error(JSON.stringify(reply.error))):resolve(reply.result));
      cdp.send('Target.sendMessageToTarget',{sessionId,message:JSON.stringify({id,method:'Runtime.getHeapUsage'})})
        .catch(error=>{pending.delete(id);reject(error);});
    });}finally{clearTimeout(timer);}
  };
  const ownership=()=>page.evaluate(async()=>({
    checkpoint:await globalThis.__workerWithFileSerial295(()=>globalThis.__workerCheckpoint295()),
    lifecycle:{...globalThis.__workerResourceBackend295.getLifecycle()},worker_index:globalThis.__workerTransport295.workers,
    file_serial_exposure_released:typeof globalThis.__workerFileSerial295==='undefined'}));
  const main=async()=>({main_heap:await cdp.send('Runtime.getHeapUsage'),...await page.evaluate(()=>({
    gpu:globalThis.__workerGpu295(),raf:{...globalThis.__workerRaf295},heartbeat:{...globalThis.__workerHeartbeat295},
    input:{...globalThis.__workerInput295},file_event:globalThis.__workerFile295??null,
    observed_ms:performance.now()})),...await ownership()});
  return {main,async settled(){
    for(let i=0;i<100&&(targets.size!==1||page.workers().length!==1);i++)await new Promise(resolve=>setTimeout(resolve,50));
    assert.equal(targets.size,1,'Exactly one current CDP worker');assert.equal(page.workers().length,1);
    const [session_id,target]=[...targets][0];assert.equal(target.url,page.workers()[0].url());
    const before=await main();assertWorkerInventoryQuiescent(before);
    const heap={session_id,url:target.url,...await workerHeap(session_id)};
    const immediate=await ownership();assertWorkerInventoryQuiescent(immediate);
    assert.deepEqual(immediate.checkpoint,before.checkpoint,'Checkpoint changed before worker inventory');
    assert.deepEqual(immediate.lifecycle,before.lifecycle,'Lifecycle changed before worker inventory');
    assert.equal(immediate.worker_index,before.worker_index);
    const worker=await page.workers()[0].evaluate(()=>globalThis.__workerSourceResource295());
    const after=await ownership();assertWorkerInventoryQuiescent(after);
    assert.equal(after.worker_index,before.worker_index,'Worker changed across inventory');
    assert.deepEqual(after.lifecycle,before.lifecycle,'Lifecycle changed across inventory');
    assert.deepEqual(after.checkpoint,before.checkpoint,'Checkpoint changed across inventory');
    return {...before,worker_heap:heap,worker,settled_after:after};
  }};
}

const frames=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const observe=(page,hashes=false)=>page.evaluate(hashes=>globalThis.__workerSourceUi295?.(hashes),hashes);
async function command(page,name){await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name,exact:true}).click();}
async function ready(page,empty=false,sourceSha=null){
  await page.waitForFunction(({empty,sourceSha})=>{const d=document.querySelector('#source-progress');
    const settled=!!globalThis.__workerSourceUi295&&document.querySelector('#status').dataset.runtimeReady==='true'
      &&d.hidden&&!document.querySelector('main').inert
      &&(!empty||!document.querySelector('#empty-project').hidden);
    if(!settled)return false;
    return !sourceSha||globalThis.__workerSourceReady295().source_sha256===sourceSha;
  },{empty,sourceSha},{timeout:180000});
  await frames(page);const ui=await observe(page);
  assert.equal(ui.singleton,true);assert.equal(ui.generation,ui.graph_generation);return ui;
}
async function inputProxy(page){
  const box=await page.locator('#viewport canvas').boundingBox();assert.ok(box);await frames(page);
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
  const before=await page.evaluate(()=>globalThis.__workerInput295.sequence);await page.mouse.wheel(0,120);
  await page.waitForFunction(before=>globalThis.__workerInput295.sequence>before&&globalThis.__workerInput295.observed_ms!==null,before);
  return page.evaluate(()=>({...globalThis.__workerInput295}));
}

async function modeRun(isolation,inputs,expected){
  const mode={isolation_requested:isolation,complete:false,stages:[],requests:[],external_requests:[],api_requests:[],
    page_errors:[],worker_events:[],backend_observer_injections:[],cleanup:{context_closed:false,preview_closed:false}};
  report.modes.push(mode);let context,preview;
  try{
    const prefix='/worker-source-ui295/';preview=await startPreview(site,{prefix,isolation});
    mode.origin=preview.origin;
    context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
    context.on('request',request=>{const url=request.url();mode.requests.push(url);
      if(new URL(url).origin!==preview.origin&&!url.startsWith('blob:'+preview.origin+'/'))mode.external_requests.push(url);
      if(/\/api(?:\/|$)/.test(new URL(url).pathname))mode.api_requests.push(url);});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());if(url.origin!==preview.origin)return route.abort();
      if(/\/api(?:\/|$)/.test(url.pathname))return route.abort();
      const backendObserver=url.pathname.endsWith('/worker-backend.js');
      const suffix=url.pathname.endsWith('/app.js')?appSuffix+backendSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
      if(!suffix&&!backendObserver)return route.continue();
      const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);assert.ok(record);
      const bytes=await readFile(path.join(site.root,relative));assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
      const body=backendObserver?Buffer.from(instrumentWorkerBackend(bytes.toString('utf8'))):Buffer.concat([bytes,Buffer.from(suffix)]);
      if(backendObserver)mode.backend_observer_injections.push({path:relative,original_sha256:record.sha256,
        observed_sha256:sha256(body),marker_matches:1,insertion_sha256:report.instrumentation.backend_observer_insertion_sha256});
      await route.fulfill({status:200,contentType:'text/javascript',body,
        headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
    });
    const page=await context.newPage();page.setDefaultTimeout(60000);page.on('pageerror',error=>mode.page_errors.push(String(error)));
    page.on('dialog',dialog=>dialog.accept());
    let created=0,closed=0;page.on('worker',worker=>{const index=++created;mode.worker_events.push({event:'created',index,url:worker.url()});
      worker.on('close',()=>{closed++;mode.worker_events.push({event:'closed',index});});});
    await page.addInitScript(installMainObservers);const metrics=await collectResources(context,page);
    await page.goto(preview.origin+prefix+path.posix.dirname(site.manifest.entry)+'/');await ready(page);
    if((await observe(page)).recipe.nodes.length){await command(page,'New project');await ready(page,true);}
    await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
    mode.isolation_actual=await page.evaluate(()=>crossOriginIsolated);assert.equal(mode.isolation_actual,isolation);
    assert.equal(created,1);assert.equal(closed,0);
    const stage=async(cycle,source,name,{hashes=true,active=false,...extra}={})=>{
      const snapshot=active?{...await metrics.main(),worker:null,worker_heap:null,
        unavailable_reason:'Worker/source RPC active; no inspection polling'}:await metrics.settled();
      const ui=active?null:await observe(page,hashes);
      const row={cycle,source,stage:name,...snapshot,ui,...extra};mode.stages.push(row);mode.phase={cycle,source,stage:name};
      await checkpoint();process.stdout.write(JSON.stringify({isolation,cycle,source,stage:name})+'\n');return row;
    };
    const verify=(row,input)=>{
      const w=row.worker,ui=row.ui,ref=expected[input.name];assert.ok(w&&ui);
      assert.equal(w.isolated,isolation);assert.equal(w.source_sha256,input.sha256);
      assert.equal(w.immutable_original_sha256,input.sha256);assert.equal(w.immutable_original_bytes,input.source_bytes);
      assert.equal(w.vertices,input.vertices);assert.equal(w.faces,input.faces);
      const {filename:observedFilename,...observedDescriptor}=w.descriptor;
      const {filename:expectedFilename,...expectedDescriptor}=ref.descriptor;void observedFilename;void expectedFilename;
      assert.deepEqual(observedDescriptor,expectedDescriptor);assert.deepEqual(w.observations,ref.observations);
      assert.equal(w.owned_fs_bytes,0);assert.deepEqual(w.owned_fs_files,[]);assert.equal(w.transfer.staging_bytes,0);
      assert.ok(w.transfer.max_staging_bytes<=ui.limits.max_source_bytes);assert.ok(w.transfer.largest_chunk_bytes<=ui.limits.chunk_bytes);
      assert.equal(ui.generation,w.project_generation);assert.equal(ui.source.source_sha256,input.sha256);
      assert.equal(ui.vertices,input.vertices);assert.equal(ui.triangles,input.faces);
      if(ui.display_hashes)for(const name of ['positions','indices'])assert.equal(ui.display_hashes[name],ref.display[name].sha256);
      assert.equal(ui.arrays.views_truncated,false);assert.ok(w.wasm_capacity_bytes>0);
      assert.ok(ui.arrays.views.some(view=>/normal/.test(view.name)&&view.logical_bytes>0));
      assert.ok(ui.arrays.views.some(view=>/color/.test(view.name)&&view.logical_bytes>0));
    };
    let generation=(await observe(page)).generation;mode.ordinary_worker_index=1;
    for(let cycle=1;cycle<=2;cycle++)for(const input of inputs){
      await stage(cycle,input.name,'empty-baseline');
      const chooser=page.waitForEvent('filechooser');await page.locator('[data-add-scan]').click();
      const picker=await chooser;assert.equal(await picker.element().getAttribute('id'),'scan-file');
      const started=performance.now();await picker.setFiles(input.path);
      await stage(cycle,input.name,'file-selected',{active:true,picker_delivery_ms:performance.now()-started});
      await ready(page,false,input.sha256);
      const importWindow=await page.evaluate(()=>{
        const value=globalThis.__workerImportWindow295,end=performance.now();
        value.active=false;value.end_ms=end;value.duration_ms=end-value.started_ms;
        value.heartbeat_max_gap_ms=Math.max(value.heartbeat_max_gap_ms,end-Math.max(value.started_ms,globalThis.__workerHeartbeat295.last_ms));
        value.raf_max_gap_ms=Math.max(value.raf_max_gap_ms,end-Math.max(value.started_ms,globalThis.__workerRaf295.last_ms??value.started_ms));
        return {...value};
      });
      const imported=await stage(cycle,input.name,'imported',{hashes:true,import_window:importWindow});verify(imported,input);
      assert.equal(imported.ui.generation,generation+1);generation=imported.ui.generation;
      assert.equal(imported.ui.recipe_nodes,1);assert.equal(imported.ui.fit_nodes,0);assert.equal(imported.worker_index,1);
      imported.import_to_visible_observation_ms=importWindow.duration_ms;
      imported.input_proxy=await inputProxy(page);
      await command(page,'Inspect source');const details=await page.locator('#source-details').textContent();
      for(const value of [input.sha256,input.vertices.toLocaleString(),input.faces.toLocaleString()])assert.ok(details.includes(value));
      const inspected=await stage(cycle,input.name,'inspected',{source_dom_verified:true});verify(inspected,input);
      await page.getByRole('button',{name:'Selection',exact:true}).click();
      await page.locator('#selection-shape').selectOption('rectangle');await page.locator('#selection-depth').selectOption('through_all');
      await page.locator('#tool').selectOption('replace');await page.locator('#top').click();await frames(page);
      const box=await page.locator('#viewport canvas').boundingBox();assert.ok(box);
      const fraction=input.name==='grid-512x256'?.025:.4;
      const rect={x0:box.x+box.width*(.5-fraction),y0:box.y+box.height*(.5-fraction),
        x1:box.x+box.width*(.5+fraction),y1:box.y+box.height*(.5+fraction)};
      await page.mouse.move(rect.x0,rect.y0);await page.mouse.down();await page.mouse.move(rect.x1,rect.y1,{steps:10});await page.mouse.up();
      await page.waitForFunction(()=>{const s=globalThis.__workerSourceReady295();return s.selections.some(v=>v>0)&&s.selected_index_count>0&&!document.querySelector('main').inert;});
      await frames(page);const overlay=await stage(cycle,input.name,'selection-overlay',{rectangle:rect});verify(overlay,input);
      assert.equal(overlay.ui.selection.length,1);assert.ok(overlay.ui.selection[0].count<=25000);
      overlay.input_proxy=await inputProxy(page);
      await command(page,'New project');await ready(page,true);const empty=await stage(cycle,input.name,'reset');
      assert.equal(empty.ui.generation,generation+1);generation=empty.ui.generation;
      for(const key of ['vertices','triangles','positions_length','indices_length','selected_index_count','recipe_nodes','input_files'])assert.equal(empty.ui[key],0,key);
      assert.equal(empty.worker.immutable_original_bytes,0);assert.equal(empty.worker.owned_fs_bytes,0);assert.equal(empty.worker.transfer.staging_bytes,0);
      assert.equal(empty.worker_index,1);assert.equal(created,1);assert.equal(closed,0);
    }
    const grid=inputs.find(input=>input.name==='grid-512x256');
    const pick=page.waitForEvent('filechooser');await page.locator('[data-add-scan]').click();await(await pick).setFiles(grid.path);
    await ready(page,false,grid.sha256);const beforeForce=await stage(0,grid.name,'force-before',{hashes:true});verify(beforeForce,grid);
    const originalRecipe=beforeForce.ui.recipe,originalEpoch=beforeForce.ui.generation;
    const actions=page.waitForEvent('download');await page.getByRole('button',{name:'Save actions',exact:true}).click();
    const downloaded=await actions,actionsBytes=await readFile(await downloaded.path());assert.deepEqual(JSON.parse(actionsBytes),originalRecipe);
    const originalFile=await page.evaluate(()=>globalThis.__workerTransport295.prepared.at(-1).file_serial);
    await page.locator('#force-restart').click();
    await page.waitForFunction(()=>globalThis.__workerTransport295.workers===2&&globalThis.__workerResourceBackend295.getLifecycle().state==='complete',null,{timeout:180000});
    await ready(page,false,grid.sha256);const afterForce=await stage(0,grid.name,'force-after',{hashes:true});verify(afterForce,grid);
    assert.equal(afterForce.ui.generation,originalEpoch);assert.deepEqual(afterForce.ui.recipe,originalRecipe);
    assert.notEqual(afterForce.worker_heap.session_id,beforeForce.worker_heap.session_id);
    const transport=await page.evaluate(()=>structuredClone(globalThis.__workerTransport295));
    assert.equal(transport.prepared.at(-1).file_serial,originalFile);assert.equal(transport.prepared.at(-1).recovery,true);
    assert.equal(created,2);assert.equal(closed,1);
    await command(page,'New project');await ready(page,true);await stage(0,grid.name,'reopen-empty');
    const actionPicker=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Load actions',exact:true}).click();
    await(await actionPicker).setFiles({name:'saved-grid-actions.json',mimeType:'application/json',buffer:actionsBytes});
    await page.locator('#locate-source-dialog').waitFor({state:'visible'});
    const locate=page.waitForEvent('filechooser');await page.locator('#choose-located-source').click();
    await(await locate).setFiles({name:'renamed-identical-grid.ply',mimeType:'application/octet-stream',buffer:await readFile(grid.path)});
    await ready(page,false,grid.sha256);const reopened=await stage(0,grid.name,'reopened',{hashes:true});verify(reopened,grid);
    assert.deepEqual(reopened.ui.recipe,originalRecipe);assert.equal(reopened.ui.generation,originalEpoch+2);
    assert.equal(reopened.worker_index,2);assert.equal(created,2);assert.equal(closed,1);
    const finalTransport=await page.evaluate(()=>structuredClone(globalThis.__workerTransport295));
    assert.notEqual(finalTransport.prepared.at(-1).file_serial,originalFile);
    mode.transport=finalTransport;mode.force={original_file_serial:originalFile,original_epoch:originalEpoch,
      before_session:beforeForce.worker_heap.session_id,after_session:afterForce.worker_heap.session_id,
      actions_sha256:sha256(actionsBytes),actions_bytes:actionsBytes.length};
    await command(page,'New project');await ready(page,true);await stage(0,grid.name,'final-reset');
    assert.equal(context.pages().length,1);assert.equal(await page.locator('#viewport canvas').count(),1);
    assert.deepEqual(mode.external_requests,[]);assert.deepEqual(mode.api_requests,[]);assert.deepEqual(mode.page_errors,[]);
    mode.complete=true;
  }catch(error){mode.failure=String(error);mode.complete=false;await checkpoint();throw error;}
  finally{
    const errors=[];
    if(context)try{await context.close();mode.cleanup.context_closed=true;}catch(error){errors.push('context: '+String(error));}
    if(preview)try{await preview.close();mode.cleanup.preview_closed=true;}catch(error){errors.push('preview: '+String(error));}
    if(errors.length){mode.complete=false;mode.cleanup.errors=errors;await checkpoint();throw Error(errors.join('; '));}
  }
}

try{
  report.source_guard=await freezeInputs();await checkpoint();
  const frozen=await prepareSourceInputs({repository,output:path.join(output,'inputs'),nativePython});
  const inputs=sourceNames.map(name=>frozen.cases.find(item=>item.name===name));assert.ok(inputs.every(Boolean));
  report.inputs={...frozen,cases:inputs};const expected=await reference(path.join(output,'inputs'));report.native_reference=expected;
  report.reference_value_sha256=sha256(Buffer.from(JSON.stringify(expected)));
  report.source_guard.frozen_input_files=[...inputs.map(input=>({path:input.path,sha256:input.sha256})),
    ...await Promise.all(['inputs/manifest.json','native-source-resource-reference.json'].map(async relative=>({path:path.join(output,relative),sha256:sha256(await readFile(path.join(output,relative)))})))];
  browser=await chromium.launch({headless:true});report.browser={name:'chromium',version:browser.version(),headless:true};
  for(const isolation of [false,true])await modeRun(isolation,inputs,expected);
  const finalSite=await verifySite(site.root);assert.equal(finalSite.manifest_sha256,site.manifest_sha256);report.artifact_unchanged=true;
  await assertSiteInventory();
  await assertFrozenInputs();
  await browser.close();browser=null;report.browser_closed=true;report.complete=true;
  report.validation=validateWorkerResourceReport(report);
}catch(error){report.complete=false;report.failure=String(error);process.exitCode=1;}
finally{
  clearTimeout(watchdog);
  try{if(browser){await browser.close();report.browser_closed=true;}}catch(error){report.cleanup_failure=String(error);report.complete=false;process.exitCode=1;}
  report.finished_utc=new Date().toISOString();
  await writeFile(path.join(output,'worker-source-ui-resources.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  process.stdout.write(JSON.stringify({complete:report.complete,output,failure:report.failure})+'\n');
}
