// Separate observational evidence: actual large project controls and a sampled
// owned Chromium process tree. No memory/performance threshold or zero-copy claim.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {readFile,writeFile,copyFile,stat} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {prepareReports,sha256,startPreview,verifySite} from './server.mjs';
import {appSuffix,backendSuffix,workerSuffix,installMainObservers,instrumentWorkerBackend,backendObserverMarker} from './worker-source-resource-observers.mjs';

const args=process.argv.slice(2),argument=(name,fallback)=>{
  const index=args.indexOf(name);if(index<0)return fallback;
  assert(args[index+1]&&!args[index+1].startsWith('--'),'Missing '+name);return args[index+1];
};
assert(argument('--directory')&&argument('--output'),'Required --directory and --output');
const repository=fileURLToPath(new URL('../..',import.meta.url)),nativePython=path.resolve(argument('--native-python',path.join(repository,'.venv/bin/python'))),
  playwrightDirectory=path.resolve(argument('--playwright-directory',path.join(repository,'experiments/browser_viewer')));
const site=await verifySite(argument('--directory')),output=await prepareReports(argument('--output'),[site.root,repository]);
process.env.TMPDIR=output;
const {chromium}=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
const environment={...process.env,PYTHONPATH:[repository,path.join(repository,'src')].join(path.delimiter),OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1'};
const oracle=(...parameters)=>JSON.parse(execFileSync(nativePython,['-m','experiments.browser_static.project_archive_expected',...parameters],
  {cwd:repository,env:environment,encoding:'utf8',maxBuffer:1024*1024}));
// This process and fixture/oracle subprocesses are outside the measured tree.
const fixture=oracle('resource-fixture','--directory',path.join(output,'fixture'));
assert.equal(fixture.vertices,131072);assert.equal(fixture.triangles,260610);
assert(fixture.source_bytes>6*1024**2&&fixture.source_bytes<=8*1024**2);
const runtimeManifest=JSON.parse(await readFile(path.join(site.root,site.manifest.runtime.path,'manifest.json'),'utf8'));
assert.equal(runtimeManifest.worker_protocol.version,12);
const limits=runtimeManifest.worker_protocol.source_import.limits,archiveLimits=runtimeManifest.worker_protocol.project_archive.limits;
assert(fixture.source_bytes<=limits.max_source_bytes&&fixture.vertices<=limits.max_vertices&&fixture.triangles<=limits.max_faces);
const authorityInsertion=`
  globalThis.__projectArchiveAuthority359=()=>({generation:checkpoint?.generation??null,
    authority:structuredClone(checkpoint?.sourceAuthority??null),file_bytes:checkpoint?.file?.size??0});
`;
const passiveTransport=()=>{
  const NativeWorker=globalThis.Worker;
  const observation=globalThis.__archiveResourceTransport359={exports:[],workers:0};
  globalThis.Worker=class extends NativeWorker{
    constructor(...args){super(...args);observation.workers++;
      this.addEventListener('message',event=>{const data=event.data;
        const entry=observation.exports.find(item=>item.id===data.id&&item.worker===this.archiveResourceWorker);
        if(!entry)return;
        if(data.kind==='project-export-chunk'){
          entry.chunks++;entry.bytes+=data.bytes.byteLength;entry.max_chunk_bytes=Math.max(entry.max_chunk_bytes,data.bytes.byteLength);
          entry.outstanding++;entry.max_outstanding=Math.max(entry.max_outstanding,entry.outstanding);
          if(data.sequence!==entry.chunks)entry.ordered=false;
        }else if(data.ok===true&&data.result?.bytes_written!==undefined){entry.complete=true;entry.receipt_bytes=data.result.bytes_written;}
      });this.archiveResourceWorker=observation.workers;
    }
    postMessage(message,...args){
      if(message.operation==='exportProject')observation.exports.push({id:message.id,worker:this.archiveResourceWorker,
        chunks:0,bytes:0,max_chunk_bytes:0,outstanding:0,max_outstanding:0,ordered:true,complete:false});
      if(message.operation==='accept-project-chunk'){
        const entry=observation.exports.find(item=>item.id===message.id&&item.worker===this.archiveResourceWorker);
        if(entry){entry.outstanding--;if(message.accepted!==true)entry.rejected=true;}
      }
      return super.postMessage(message,...args);
    }
  };
};
const report={schema_version:1,kind:'scansor-project-archive-resources',complete:false,started_utc:new Date().toISOString(),
  source_revision:site.manifest.source_revision,manifest_sha256:site.manifest_sha256,fixture,modes:[],
  instrumentation:{driver_sha256:sha256(await readFile(fileURLToPath(import.meta.url))),
    sampler_sha256:sha256(await readFile(new URL('./project_archive_resource_sampler.py',import.meta.url))),
    authority_observer_sha256:sha256(await readFile(new URL('./source-authority-observer.mjs',import.meta.url))),
    app_suffix_sha256:sha256(Buffer.from(appSuffix+backendSuffix)),worker_suffix_sha256:sha256(Buffer.from(workerSuffix)),
    scope:'Actual shipped worker UI and real project file choosers/downloads; source/display snapshots contain counts, bounded EOF hashes and backing-store metadata, never full positions.',
    accounting:'Logical source/member lengths, explicit Python source byte payloads, compressed checkpoint File and final Blob lengths, array roots, Wasm capacity, CDP heap and process RSS/PSS overlap and MUST NOT be added.',
    sampling:'Owned browser process-tree samples span startup and each operation. Stage probes add hashing/allocation overhead. Short peaks/processes may be missed; no whole-browser zero-copy or capacity/performance claim.'}};
let browserServer,browser,preview,sampler;
const watchdog=setTimeout(()=>{report.failure='Project archive resource run exceeded20minutes';void browserServer?.close();},1200000);
async function startSampler(pid,destination){
  const child=spawn(nativePython,['-m','experiments.browser_static.project_archive_resource_sampler','--pid',String(pid),'--output',destination],
    {cwd:repository,env:environment,stdio:['pipe','pipe','pipe']});
  let lines='',errorText='',readyResolve,readyReject,next=0,exited=false;
  const pending=new Map(),ready=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
  child.stderr.on('data',chunk=>{errorText=(errorText+chunk).slice(-12000);});
  child.stdin.on('error',error=>{readyReject(error);for(const item of pending.values())item.reject(error);pending.clear();});
  child.stdout.on('data',chunk=>{
    lines+=chunk.toString();let end;
    while((end=lines.indexOf('\n'))>=0){
      const line=lines.slice(0,end);lines=lines.slice(end+1);let record;
      try{record=JSON.parse(line);}catch(error){readyReject(error);continue;}
      if(record.ready)readyResolve(record);
      if(pending.has(record.id)){pending.get(record.id).resolve(record.sample);pending.delete(record.id);}
    }
  });
  child.once('error',readyReject);
  const closed=new Promise(resolve=>child.once('close',(code,signal)=>{
    exited=true;const error=Error(`Browser memory sampler exited (${code},${signal}): ${errorText}`);
    readyReject(error);for(const item of pending.values())item.reject(error);pending.clear();resolve({code,signal});
  }));
  const timeout=setTimeout(()=>readyReject(Error('Browser memory sampler startup timed out')),15000);
  let identity;
  try{identity=await ready;}catch(error){child.stdin.end();await closed;throw error;}finally{clearTimeout(timeout);}
  return {identity,async mark(label){
    assert(!exited,'Browser memory sampler already exited');const id=++next;
    let timer;
    try{return await new Promise((resolve,reject)=>{
      timer=setTimeout(()=>{pending.delete(id);reject(Error('Browser memory phase observation timed out'));},15000);
      pending.set(id,{resolve,reject});child.stdin.write(JSON.stringify({id,operation:'mark',label})+'\n');
    });}finally{clearTimeout(timer);}
  },async stop(){
    if(!exited)child.stdin.end(JSON.stringify({operation:'stop'})+'\n');
    const timer=setTimeout(()=>child.kill('SIGTERM'),15000);let exit;
    try{exit=await closed;}finally{clearTimeout(timer);}
    const value=JSON.parse(await readFile(destination,'utf8'));
    assert.equal(exit.code,0,errorText);assert.equal(value.complete,true,JSON.stringify(value.errors));
    assert(value.sample_count>0&&value.sampled_tree_rss_peak_bytes>0);return value;
  }};
}
const quiet=value=>value?.setup&&!value.inert&&value.lifecycle.state==='complete'&&value.source_progress_hidden;
async function until(page,predicate,phase){
  const deadline=Date.now()+180000;
  while(Date.now()<deadline){const value=await page.evaluate(()=>globalThis.__workerSourceUi295?.());
    if(predicate(value))return value;await page.waitForTimeout(40);}
  throw Error(`Large project UI timed out: ${phase}; ${await page.locator('#status').textContent()}`);
}
async function chooseProject(page,filename){
  const [chooser]=await Promise.all([page.waitForEvent('filechooser'),page.locator('[data-command-id="open-project"]:visible').click()]);
  assert.equal(await chooser.element().getAttribute('id'),'project-file');await chooser.setFiles(filename);
}
async function reset(page){
  await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name:'New project',exact:true}).click();
  return until(page,value=>quiet(value)&&value.recipe.nodes.length===0,'reset');
}
async function observe(page,cdp,name){
  await page.waitForFunction(()=>globalThis.__workerResourceBackend295?.getLifecycle().state==='complete');
  const checkpoint=await page.evaluate(()=>globalThis.__workerWithFileSerial295(()=>globalThis.__workerCheckpoint295()));
  assert.equal(checkpoint.pending_count,0);assert.equal(checkpoint.source_operation,null);assert.equal(checkpoint.recovering,false);
  assert.equal(page.workers().length,1);
  const worker=await page.workers()[0].evaluate(()=>globalThis.__workerSourceResource295());
  const ui=await page.evaluate(()=>globalThis.__workerSourceUi295(true));
  const authority=await page.evaluate(()=>globalThis.__projectArchiveAuthority359());
  const after=await page.evaluate(()=>globalThis.__workerWithFileSerial295(()=>globalThis.__workerCheckpoint295()));
  assert.deepEqual(after,checkpoint,'Accepted checkpoint changed across resource inventory');
  assert(quiet(ui));assert.equal(worker.isolated,await page.evaluate(()=>crossOriginIsolated));
  assert.equal(worker.worker_protocol,12);assert.equal(worker.owned_fs_bytes,0);assert.deepEqual(worker.owned_fs_files,[]);
  assert.equal(worker.transfer.staging_bytes,0);assert.equal(worker.transfer.max_staging_bytes,0);
  assert(worker.transfer.largest_chunk_bytes<=limits.chunk_bytes);assert(worker.wasm_capacity_bytes>0);
  const memory=await sampler.mark(name);
  return{name,memory,worker,ui,checkpoint,authority,main_heap:await cdp.send('Runtime.getHeapUsage')};
}
function verifySource(row,archiveBytes){
  const w=row.worker,u=row.ui,c=row.checkpoint.current_checkpoint;
  assert.equal(w.source_storage.kind,'ArchiveMemberInput');assert.equal(w.source_storage.backing_kind,'BlobInput');
  assert.equal(w.source_storage.owned_source_payload_bytes,0);assert.equal(w.source_storage.authority_bytes,fixture.source_bytes);
  assert.equal(w.source_replay.bytes,fixture.source_bytes);assert.equal(w.source_replay.sha256,fixture.source_sha256);
  assert.equal(w.source_replay.eof_verified,true);assert(w.source_replay.read_calls>0&&w.source_replay.max_chunk_bytes<=65536);
  // Blob transport counts compressed archive ranges; source_replay counts the
  // independently verified expanded member. Their byte counts are distinct.
  assert(w.transfer.replay_read_calls>0&&w.transfer.replay_read_bytes>=archiveBytes);
  assert.equal(w.vertices,fixture.vertices);assert.equal(w.faces,fixture.triangles);assert.equal(w.source_sha256,fixture.source_sha256);
  assert.equal(u.vertices,fixture.vertices);assert.equal(u.triangles,fixture.triangles);assert.equal(u.positions_length,fixture.vertices*3);
  assert.equal(u.indices_length,fixture.triangles*3);assert.deepEqual(u.recipe,fixture.recipe);
  assert.equal(u.display_hashes.positions,fixture.display.positions.sha256);assert.equal(u.display_hashes.indices,fixture.display.indices.sha256);
  assert.equal(row.authority.authority.kind,'project-file');assert.equal(row.authority.authority.member,fixture.member_name);
  assert.equal(row.authority.file_bytes,archiveBytes);assert.equal(c.file.logical_bytes,archiveBytes);assert(c.file.serial>0);
  assert.equal(c.generation,u.generation);assert.equal(w.project_generation,u.generation);assert.equal(c.mode.kind,'scan');
}
function verifyEmpty(row){
  assert.equal(row.worker.source_storage.kind,'none');assert.equal(row.worker.source_storage.owned_source_payload_bytes,0);
  assert.equal(row.worker.source_storage.authority_bytes,0);assert.equal(row.worker.source_sha256,null);
  assert.equal(row.ui.recipe.nodes.length,0);assert.equal(row.ui.positions_length,0);assert.equal(row.ui.indices_length,0);
  assert.equal(row.authority.authority,null);assert.equal(row.authority.file_bytes,0);
  assert.deepEqual(row.checkpoint.current_checkpoint.file,{serial:null,logical_bytes:0});
}
try{
  for(const isolation of [false,true]){
    const mode={isolation,complete:false,stages:[],requests:[],unexpected_requests:[],page_errors:[],cleanup:{}};report.modes.push(mode);
    preview=await startPreview(site,{prefix:'/project-resources/',isolation});
    browserServer=await chromium.launchServer({headless:true});
    const pid=browserServer.process().pid;assert(Number.isSafeInteger(pid)&&pid>0);
    sampler=await startSampler(pid,path.join(output,`browser-memory-${isolation?'isolated':'plain'}.json`));
    mode.sampler=sampler.identity;browser=await chromium.connect(browserServer.wsEndpoint());
    const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000},acceptDownloads:true});
    await context.addInitScript(installMainObservers);await context.addInitScript(passiveTransport);
    context.on('request',request=>{const url=request.url();mode.requests.push(url);
      if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))mode.unexpected_requests.push(url);});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());if(url.origin!==preview.origin)return route.abort();
      const suffix=url.pathname.endsWith('/app.js')?appSuffix+backendSuffix:url.pathname.endsWith('/worker.mjs')?workerSuffix:null;
      if(!suffix&&!url.pathname.endsWith('/worker-backend.js'))return route.continue();
      const relative=url.pathname.slice('/project-resources/'.length),record=site.records.get(relative);assert(record,'Observed app bytes must be manifest-listed');
      const bytes=await readFile(path.join(site.root,relative));assert.equal(sha256(bytes),record.sha256);assert.equal(bytes.length,record.bytes);
      let body=bytes.toString('utf8');
      if(url.pathname.endsWith('/worker-backend.js'))body=instrumentWorkerBackend(body).replace(backendObserverMarker,authorityInsertion+backendObserverMarker);
      else body+=suffix;
      return route.fulfill({status:200,contentType:'text/javascript',body,
        headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
    });
    const page=await context.newPage();page.setDefaultTimeout(30000);page.on('dialog',dialog=>dialog.accept());
    page.on('pageerror',error=>mode.page_errors.push(String(error)));
    const cdp=await context.newCDPSession(page);
    try{
      await page.goto(preview.origin+'/project-resources/app/');
      await until(page,quiet,'startup');await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
      // The shipped app initially opens its example. Establish the measured
      // empty authority through the same real command used between imports.
      await reset(page);
      mode.stages.push(await observe(page,cdp,'empty-baseline'));verifyEmpty(mode.stages.at(-1));
      await sampler.mark('opening-project');await chooseProject(page,fixture.archive_path);
      await until(page,value=>quiet(value)&&value.vertices===fixture.vertices,'large project open');
      mode.stages.push(await observe(page,cdp,'opened-project'));verifySource(mode.stages.at(-1),fixture.archive_bytes);
      await sampler.mark('saving-project');
      const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save project',exact:true}).click()]);
      const saved=path.join(output,`saved-${isolation?'isolated':'plain'}.scansor`);await download.saveAs(saved);assert.equal(await download.failure(),null);
      assert.equal(download.suggestedFilename(),'project.scansor');const savedBytes=(await stat(saved)).size;
      assert(savedBytes>0&&savedBytes<=archiveLimits.max_archive_bytes);
      await until(page,quiet,'save complete');mode.stages.push(await observe(page,cdp,'saved-project'));verifySource(mode.stages.at(-1),fixture.archive_bytes);
      const exported=await page.evaluate(()=>globalThis.__archiveResourceTransport359.exports);
      assert.equal(exported.length,1);const transfer=exported[0];assert.equal(transfer.complete,true);assert.equal(transfer.ordered,true);
      assert.equal(transfer.bytes,savedBytes);assert.equal(transfer.receipt_bytes,savedBytes);assert(transfer.chunks>0);
      assert.equal(transfer.outstanding,0);assert.equal(transfer.max_outstanding,1);assert(!transfer.rejected);
      assert(transfer.max_chunk_bytes<=archiveLimits.chunk_bytes);
      const inspected=oracle('inspect-file','--file',saved);assert.equal(inspected.source_sha256,fixture.source_sha256);
      assert.equal(inspected.source_bytes,fixture.source_bytes);assert.equal(inspected.source_eof_verified,true);assert.deepEqual(inspected.actions,fixture.recipe);
      mode.saved_project={compressed_final_blob_bytes:savedBytes,budget_bytes:archiveLimits.max_archive_bytes,transfer,independent_readback:inspected};
      await sampler.mark('resetting-project');await reset(page);mode.stages.push(await observe(page,cdp,'reset'));verifyEmpty(mode.stages.at(-1));
      const renamed=path.join(output,`renamed-${isolation?'isolated':'plain'}.scansor`);await copyFile(saved,renamed);
      await sampler.mark('reopening-renamed-project');await chooseProject(page,renamed);
      await until(page,value=>quiet(value)&&value.vertices===fixture.vertices,'renamed reopen');
      const reopened=await observe(page,cdp,'renamed-reopen');mode.stages.push(reopened);verifySource(reopened,savedBytes);
      assert.notEqual(reopened.checkpoint.current_checkpoint.file.serial,mode.stages[1].checkpoint.current_checkpoint.file.serial);
      const workers=await page.evaluate(()=>globalThis.__workerTransport295.workers);
      await sampler.mark('force-recovery');await page.locator('#force-restart').click();
      await page.waitForFunction(count=>globalThis.__workerTransport295.workers>count,workers);
      await until(page,value=>quiet(value)&&value.generation===reopened.ui.generation&&value.vertices===fixture.vertices,'Force recovery');
      const recovered=await observe(page,cdp,'force-recovered');mode.stages.push(recovered);verifySource(recovered,savedBytes);
      assert.equal(recovered.checkpoint.current_checkpoint.file.serial,reopened.checkpoint.current_checkpoint.file.serial);
      assert.deepEqual(recovered.checkpoint.current_checkpoint.identity,reopened.checkpoint.current_checkpoint.identity);
      assert.notEqual(recovered.checkpoint.lifetime,reopened.checkpoint.lifetime);
      await sampler.mark('final-reset');await reset(page);mode.stages.push(await observe(page,cdp,'final-empty'));verifyEmpty(mode.stages.at(-1));
      assert.equal(mode.unexpected_requests.length,0);assert.equal(mode.page_errors.length,0);
      assert.equal(await page.evaluate(()=>crossOriginIsolated),isolation);
      mode.stage_high_water={wasm_capacity_bytes:Math.max(...mode.stages.map(row=>row.worker.wasm_capacity_bytes)),
        named_numpy_root_bytes:Math.max(...mode.stages.map(row=>row.worker.unique_root_bytes)),
        main_heap_used_bytes:Math.max(...mode.stages.map(row=>row.main_heap.usedSize))};
      mode.complete=true;
    }finally{
      await cdp.detach().catch(()=>{});await context.close();mode.cleanup.context_closed=true;
    }
    mode.memory=await sampler.stop();sampler=undefined;
    await browser.close();browser=undefined;await browserServer.close();browserServer=undefined;mode.cleanup.browser_closed=true;
    await preview.close();preview=undefined;mode.cleanup.preview_closed=true;
    process.stdout.write(JSON.stringify({isolation,complete:true,sampled_tree_rss_peak_bytes:mode.memory.sampled_tree_rss_peak_bytes,
      sampled_tree_pss_peak_bytes:mode.memory.sampled_tree_pss_peak_bytes})+'\n');
  }
  const after=await verifySite(site.root);assert.equal(after.manifest_sha256,site.manifest_sha256);report.artifact_unchanged=true;
  report.complete=true;
}catch(error){report.failure=String(error.stack||error);process.exitCode=1;}
finally{
  clearTimeout(watchdog);
  if(sampler)try{report.modes.at(-1).memory=await sampler.stop();}catch(error){report.sampler_cleanup_failure=String(error);}
  await browser?.close().catch(error=>{report.browser_cleanup_failure=String(error);});
  await browserServer?.close().catch(error=>{report.browser_cleanup_failure=String(error);});
  await preview?.close().catch(error=>{report.preview_cleanup_failure=String(error);});
  report.completed_utc=new Date().toISOString();
  await writeFile(path.join(output,'project-archive-resources.json'),JSON.stringify(report,null,2)+'\n');
}
