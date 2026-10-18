// Observational native UI benchmark. No backend publication or control hooks.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {readFile,open} from 'node:fs/promises';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {prepareReports} from './server.mjs';

const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const repository=fileURLToPath(new URL('../..',import.meta.url));
const args=process.argv.slice(2),argument=(name,fallback)=>{
  const index=args.indexOf(name);if(index<0)return fallback;
  if(!args[index+1]||args[index+1].startsWith('--'))throw Error('Missing '+name);return args[index+1];
};
const originArgument=argument('--origin'),inputsArgument=argument('--inputs'),outputArgument=argument('--output');
if(!originArgument||!inputsArgument||!outputArgument)throw Error('Required: --origin --inputs --output [--playwright-directory] [--cycles 2] [--event-acks]');
const origin=new URL(originArgument);
assert.equal(origin.protocol,'http:');assert.ok(['127.0.0.1','localhost','[::1]'].includes(origin.hostname));
assert.equal(origin.pathname,'/');assert.equal(origin.search,'');assert.equal(origin.hash,'');
const cycles=Number(argument('--cycles','2'));assert.ok(Number.isSafeInteger(cycles)&&cycles>=1&&cycles<=10);
const playwrightDirectory=path.resolve(argument('--playwright-directory',path.join(repository,'experiments/browser_viewer')));
const output=await prepareReports(outputArgument,[repository,playwrightDirectory]);
const reportPath=path.join(output,'browser-report.json');
const report={schema_version:1,kind:'scansor-native-source-ui-browser',started_utc:new Date().toISOString(),
  complete:false,origin:origin.origin,cycles,stages:[],requests:[],request_records_dropped:0,
  external_requests:[],external_request_count:0,page_errors:[],page_error_count:0,instrumentation_errors:[],checks:{},
  host:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpus:os.cpus().length},
  instrumentation:{event_acks:args.includes('--event-acks'),
    boundaries:'Read-only stage observations; server snapshots follow emitted events. Tiny imports may finish before file-selected snapshot. Ack dwell is measured separately.',
    timing:'Instrumented headless Chromium; routing disables cache. rAF probes add work; wheel-to-next-observed-app-render-submit is a correlation proxy, not physical presentation or isolated event-handler latency. Import event-to-stable observation is an upper-bound observation including file-selected snapshot/hash/ack dwell; not isolated import latency.',
    accounting:'Counters overlap and must not be summed. Named scene ArrayBuffer stores exclude graph/DOM/JS objects. CDP heap/backing is separate. WebGL is logical buffer size, not physical GPU memory.',
    scope:'One actual empty native HTTP app, singleton backend, one page/context/browser, repeated actual File/Source/reset. No fit/CAD, workers, publication hooks, capacity changes or performance thresholds.',
    checksum:'WebCrypto reads current display bytes and may transiently copy internally; only digests/counts are returned, never full arrays. Snapshot/hash overhead is excluded from import timing marker.'}};
let browser,context,page,cdp,sequence=0,ackReader,ackError;
const pendingAcks=new Map();
if(args.includes('--event-acks')){
  ackReader=createInterface({input:process.stdin});
  ackReader.on('line',line=>{
    try{const value=JSON.parse(line);assert.equal(value.schema_version,1);assert.equal(value.event,'ack');
      assert.ok(Number.isSafeInteger(value.sequence));const pending=pendingAcks.get(value.sequence);
      if(!pending)throw Error('Unexpected stage acknowledgement '+value.sequence);pending.resolve();
    }catch(error){ackError=error;for(const pending of pendingAcks.values())pending.reject(error);}
  });
  ackReader.on('close',()=>{ackError??=Error('Stage acknowledgement input closed');for(const pending of pendingAcks.values())pending.reject(ackError);});
}
const appSuffix=`
// Read-only named backing-store observer; no retained array/geometry history.
globalThis.__nativeSourceRender285=()=>({frames,pending});
globalThis.__nativeSourceUi285=async(hashes=false)=>{
 if(!positions||!mesh?.geometry||!graphState)return null;
 const views=[],buffers=new Map();let viewCount=0;
 const add=(name,array)=>{if(!array?.buffer)return;viewCount++;
  if(!buffers.has(array.buffer))buffers.set(array.buffer,buffers.size);
  if(views.length<256)views.push({name,dtype:array.constructor.name,length:array.length,
    logical_bytes:array.byteLength,backing_id:buffers.get(array.buffer),backing_bytes:array.buffer.byteLength});};
 add('positions',positions);
 const names=new Map([[mesh,'mesh'],[selectedPoints,'selectedPoints'],[focusedPoints,'focusedPoints'],
   [overlapMarkers,'overlapMarkers'],[overlapHalo,'overlapHalo']]);
 let objects=0;scene.traverse(object=>{objects++;if(!object.geometry)return;
  const name=names.get(object)||object.type+'.'+objects;
  for(const[key,a]of Object.entries(object.geometry.attributes))add(name+'.'+key,a.array||a.data?.array);
  add(name+'.indices',object.geometry.index?.array);});
 const selection=graphState.recipe.nodes.filter(n=>n.operation==='selection').map(n=>{const ids=graphState.memberships?.[n.id]||n.ids||[];return{id:n.id,count:ids.length,
   min_id:ids.length?ids.reduce((a,b)=>Math.min(a,b),Infinity):null,max_id:ids.length?ids.reduce((a,b)=>Math.max(a,b),-Infinity):null};});
 const indices=mesh.geometry.index?.array;
 const digest=async array=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
   new Uint8Array(array.buffer,array.byteOffset,array.byteLength)))).map(v=>v.toString(16).padStart(2,'0')).join('');
 const snapshot={observed_performance_ms:performance.now(),time_origin_ms:performance.timeOrigin,
  setup:viewerSetup,singleton:backend===getBackend(),generation:workspaceGeneration,
  graph_generation:graphState.project_generation,source:metadata.source||null,
  vertices:metadata.vertices,triangles:metadata.triangles,display_triangles:metadata.display_triangles??metadata.triangles,
  source_nodes:graphState.recipe.nodes.filter(n=>n.operation==='source').length,
  recipe_nodes:graphState.recipe.nodes.length,fit_nodes:graphState.recipe.nodes.filter(n=>n.operation==='fit').length,
  graph_states:Object.fromEntries(Object.entries(graphState.states).reduce((m,[,s])=>m.set(s,(m.get(s)||0)+1),new Map())),
  limits:metadata.capabilities?.source_import?.limits,positions_length:positions.length,indices_length:indices?.length||0,
  selected_index_count:selectedPoints.geometry.index?.count||0,focused_index_count:focusedPoints.geometry.index?.count||0,
  selection,selected_visible:selectedPoints.visible,frames,pending,inert:document.querySelector('main').inert,
  source_progress_hidden:$('source-progress').hidden,input_files:$('scan-file').files.length,
  counts_text:$('counts').textContent,mesh_info:$('mesh-info').textContent,
  arrays:{views,view_count:viewCount,views_truncated:viewCount>views.length,
    unique_backing_bytes:[...buffers.keys()].reduce((n,b)=>n+b.byteLength,0),objects},
  render:{calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,points:renderer.info.render.points,
    geometries:renderer.info.memory.geometries,textures:renderer.info.memory.textures}};
 if(hashes)snapshot.display_hashes={positions:await digest(positions),indices:await digest(indices)};
 return snapshot;
};
`;
report.instrumentation.app_suffix_sha256=sha256(Buffer.from(appSuffix));

async function emitStage(cycle,source,stage,metrics){
  const event={schema_version:1,event:'stage',sequence:++sequence,cycle,source,stage,
    monotonic_ms:Number(process.hrtime.bigint())/1e6,utc:new Date().toISOString()};
  const record={...event,...metrics};report.stages.push(record);
  let timer,promise;
  if(ackReader){if(ackError)throw ackError;promise=new Promise((resolve,reject)=>{
    pendingAcks.set(sequence,{resolve,reject});timer=setTimeout(()=>reject(Error('Stage acknowledgement did not arrive within the harness deadline')),180000);});}
  const start=performance.now();process.stdout.write(JSON.stringify(event)+'\n');
  try{if(promise)await promise;}finally{clearTimeout(timer);pendingAcks.delete(sequence);record.ack_wait_ms=performance.now()-start;}
}
const framesSettled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const observe=hashes=>page.evaluate(hashes=>globalThis.__nativeSourceUi285?.(hashes),hashes);
async function stable(source){
  await page.waitForFunction(expected=>{const d=document.querySelector('#source-progress'),main=document.querySelector('main');
    return !!globalThis.__nativeSourceUi285&&d.hidden&&!main.inert&&
      (expected?document.querySelector('#mesh-info').textContent===`${expected.vertices.toLocaleString()} vertices · ${expected.triangles.toLocaleString()} triangles`:
        document.querySelector('#empty-project')&&!document.querySelector('#empty-project').hidden);},source,{timeout:180000});
  await framesSettled();
  const state=await observe(false);assert.ok(state?.setup);assert.equal(state.singleton,true);
  assert.equal(state.generation,state.graph_generation);assert.equal(state.inert,false);assert.equal(state.source_progress_hidden,true);
  return state;
}
async function snapshot(hashes=false){
  const ui=await observe(hashes),heap=await cdp.send('Runtime.getHeapUsage');
  const probes=await page.evaluate(()=>({gpu:globalThis.__nativeGpu285(),raf:globalThis.__nativeRaf285,
    file_event:globalThis.__nativeFile285,network_window:performance.now(),input:globalThis.__nativeInput285}));
  return {ui,main_heap:heap,...probes};
}
async function command(name){await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
  await page.getByRole('menuitem',{name,exact:true}).click();}
async function inputProxy(){
  await framesSettled();const box=await page.locator('#viewport canvas').boundingBox();assert.ok(box);
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
  const before=await page.evaluate(()=>globalThis.__nativeInput285.sequence);
  await page.mouse.wheel(0,120);
  await page.waitForFunction(before=>globalThis.__nativeInput285.sequence>before&&globalThis.__nativeInput285.observed_ms!==null,before,{timeout:60000});
  return page.evaluate(()=>({...globalThis.__nativeInput285}));
}

try{
  const manifestBytes=await readFile(path.resolve(inputsArgument)),manifest=JSON.parse(manifestBytes);
  assert.equal(manifest.schema_version,1);assert.deepEqual(manifest.sources.map(s=>s.id),['raw','scene','grid']);
  report.inputs={...manifest,manifest_sha256:sha256(manifestBytes)};
  for(const source of manifest.sources){
    assert.ok(path.isAbsolute(source.path));const hash=createHash('sha256');let bytes=0;
    for await(const chunk of createReadStream(source.path)){hash.update(chunk);bytes+=chunk.length;}
    assert.equal(hash.digest('hex'),source.source_sha256);assert.equal(bytes,source.original_bytes);
  }
  report.driver_sha256=sha256(await readFile(fileURLToPath(import.meta.url)));
  const originalApp=await readFile(path.join(repository,'experiments/browser_viewer/app.js'));
  report.instrumentation.original_app_sha256=sha256(originalApp);
  const playwright=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
  browser=await playwright.chromium.launch({headless:true});report.browser={name:'chromium',version:browser.version(),headless:true};
  context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
  let pages=0;context.on('page',()=>pages++);
  context.on('request',request=>{if(new URL(request.url()).origin!==origin.origin&&!request.url().startsWith('blob:'+origin.origin+'/')){
    report.external_request_count++;if(report.external_requests.length<32)report.external_requests.push(request.url());}});
  await context.route('**/app.js',async route=>{
    assert.equal(new URL(route.request().url()).origin,origin.origin);
    const response=await route.fetch(),bytes=await response.body();assert.equal(response.status(),200);assert.equal(sha256(bytes),sha256(originalApp));
    await route.fulfill({response,body:Buffer.concat([bytes,Buffer.from(appSuffix)])});
  });
  page=await context.newPage();page.on('pageerror',error=>{report.page_error_count++;if(report.page_errors.length<32)report.page_errors.push(String(error));});page.on('dialog',dialog=>dialog.accept());
  await page.addInitScript(()=>{
    const raf=globalThis.__nativeRaf285={count:0,sum_gap_ms:0,max_gap_ms:0,over_16_ms:0,over_50_ms:0,last_ms:null};
    globalThis.__nativeInput285={sequence:0,event_ms:null,event_frames:null,observed_ms:null,render_frames:null};
    document.addEventListener('change',event=>{if(event.target.id==='scan-file'){
      const file=event.target.files[0];globalThis.__nativeFile285={event_ms:performance.now(),name:file?.name,size:file?.size};}},true);
    document.addEventListener('wheel',event=>{if(event.target.closest('canvas')){
      const input=globalThis.__nativeInput285;Object.assign(input,{sequence:input.sequence+1,event_ms:performance.now(),
        event_frames:globalThis.__nativeSourceRender285?.().frames??null,observed_ms:null,render_frames:null});}},{capture:true,passive:true});
    const tick=now=>{if(raf.last_ms!==null){const gap=now-raf.last_ms;raf.count++;raf.sum_gap_ms+=gap;raf.max_gap_ms=Math.max(raf.max_gap_ms,gap);
      if(gap>16)raf.over_16_ms++;if(gap>50)raf.over_50_ms++;}raf.last_ms=now;
      const input=globalThis.__nativeInput285,render=globalThis.__nativeSourceRender285?.();
      if(input.event_ms!==null&&input.observed_ms===null&&render?.frames>input.event_frames){input.observed_ms=performance.now();input.render_frames=render.frames;input.proxy_ms=input.observed_ms-input.event_ms;}
      requestAnimationFrame(tick);};requestAnimationFrame(tick);
    const contexts=[],getContext=HTMLCanvasElement.prototype.getContext;
    globalThis.__nativeGpu285=()=>({contexts:contexts.map(s=>({logical_live_buffer_bytes:[...s.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0),
      logical_high_water_bytes:s.high,buffer_data_calls:s.calls,deleted_buffers:s.deleted,renderer:s.renderer,
      context_lost:s.gl.isContextLost(),canvas:[s.gl.drawingBufferWidth,s.gl.drawingBufferHeight]}))});
    HTMLCanvasElement.prototype.getContext=function(...args){const gl=getContext.apply(this,args);
      if(!gl||!/^webgl/.test(args[0])||contexts.some(s=>s.gl===gl))return gl;
      const debug=gl.getExtension('WEBGL_debug_renderer_info'),state={gl,buffers:new Map(),ids:new WeakMap(),next:0,high:0,calls:0,deleted:0,
        renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER)};contexts.push(state);
      const data=gl.bufferData,del=gl.deleteBuffer;
      const binding=new Map([[gl.ARRAY_BUFFER,gl.ARRAY_BUFFER_BINDING],[gl.ELEMENT_ARRAY_BUFFER,gl.ELEMENT_ARRAY_BUFFER_BINDING],
        [gl.COPY_READ_BUFFER,gl.COPY_READ_BUFFER_BINDING],[gl.COPY_WRITE_BUFFER,gl.COPY_WRITE_BUFFER_BINDING],
        [gl.PIXEL_PACK_BUFFER,gl.PIXEL_PACK_BUFFER_BINDING],[gl.PIXEL_UNPACK_BUFFER,gl.PIXEL_UNPACK_BUFFER_BINDING],
        [gl.UNIFORM_BUFFER,gl.UNIFORM_BUFFER_BINDING],[gl.TRANSFORM_FEEDBACK_BUFFER,gl.TRANSFORM_FEEDBACK_BUFFER_BINDING]]);
      gl.bufferData=function(...values){const returned=data.apply(this,values),query=binding.get(values[0]);if(query===undefined)return returned;
        const buffer=this.getParameter(query),size=this.getBufferParameter(values[0],this.BUFFER_SIZE);if(!buffer||!Number.isFinite(size))return returned;
        let id=state.ids.get(buffer);if(!id){id=++state.next;state.ids.set(buffer,id);}state.buffers.set(id,{ref:new WeakRef(buffer),bytes:size});state.calls++;
        state.high=Math.max(state.high,[...state.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0));return returned;};
      gl.deleteBuffer=function(buffer){const returned=del.call(this,buffer);if(state.buffers.delete(state.ids.get(buffer)))state.deleted++;return returned;};return gl;
    };
  });
  cdp=await context.newCDPSession(page);await cdp.send('Network.enable');
  const network=new Map();
  cdp.on('Network.requestWillBeSent',event=>network.set(event.requestId,{url:event.request.url,method:event.request.method,request_timestamp:event.timestamp}));
  cdp.on('Network.responseReceived',event=>{const row=network.get(event.requestId);if(row)Object.assign(row,{status:event.response.status,
    response_timestamp:event.timestamp,resource_type:event.type,generation:event.response.headers['X-Scansor-Generation']??event.response.headers['x-scansor-generation'],
    content_length:event.response.headers['Content-Length']??event.response.headers['content-length']});});
  const finishNetwork=(event,error)=>{const row=network.get(event.requestId);network.delete(event.requestId);if(!row)return;
    if(report.requests.length>=5000){report.request_records_dropped++;return;}
    report.requests.push({...row,finished_timestamp:event.timestamp,encoded_data_length:event.encodedDataLength??null,error:error??null});};
  cdp.on('Network.loadingFinished',event=>finishNetwork(event));cdp.on('Network.loadingFailed',event=>finishNetwork(event,event.errorText));
  await page.goto(origin.href);await stable(null);
  await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
  const baseline=await snapshot();assert.equal(baseline.ui.source_nodes,0);assert.equal(baseline.ui.vertices,0);
  report.identity={pages:1,contexts:1,configured_origins:1,page_target:await cdp.send('Target.getTargetInfo'),page_url:page.url(),
    server_identity:'Same configured owned loopback origin; stable actual server PID/lifetime is attested separately by the Python orchestrator.'};
  let previousGeneration=baseline.ui.generation;
  for(let cycle=1;cycle<=cycles;cycle++)for(const source of manifest.sources){
    await emitStage(cycle,source.id,'empty-baseline',await snapshot());
    assert.ok(source.original_bytes<=baseline.ui.limits.max_source_bytes);assert.ok(source.vertices<=baseline.ui.limits.max_vertices);
    assert.ok(source.triangles<=baseline.ui.limits.max_faces);
    const chooserReady=page.waitForEvent('filechooser');await page.locator('[data-add-scan]').click();
    const chooser=await chooserReady;assert.equal(await chooser.element().getAttribute('id'),'scan-file');
    const deliveredAt=performance.now();await chooser.setFiles(source.path);const deliveryMs=performance.now()-deliveredAt;
    await emitStage(cycle,source.id,'file-selected',{...await snapshot(),picker_delivery_ms:deliveryMs,
      caveat:'setFiles delivery and event timing; no OS picker latency. Import continues while this observation/ack occurs.'});
    const installed=await stable(source),observedAt=await page.evaluate(()=>performance.now());
    assert.equal(installed.source?.source_sha256,source.source_sha256);assert.equal(installed.vertices,source.vertices);
    assert.equal(installed.triangles,source.triangles);assert.equal(installed.display_triangles,source.display_triangles);
    assert.equal(installed.positions_length,source.display_vertices*3);assert.equal(installed.indices_length,source.display_triangles*3);
    assert.equal(installed.source_nodes,1);assert.equal(installed.recipe_nodes,1);assert.equal(installed.fit_nodes,0);
    assert.ok(installed.generation>previousGeneration);previousGeneration=installed.generation;
    assert.equal(installed.input_files,0);assert.equal(await page.locator('[data-add-scan]').isHidden(),true);
    await page.getByRole('button',{name:'Project toolbar options',exact:true}).click();
    assert.equal(await page.getByRole('menuitem',{name:'Add scan…',exact:true}).getAttribute('data-disabled'),'');await page.keyboard.press('Escape');
    const imported=await snapshot(true);assert.equal(imported.ui.display_hashes.positions,source.display.positions.sha256);
    assert.equal(imported.ui.display_hashes.indices,source.display.indices.sha256);
    assert.equal(imported.ui.arrays.views_truncated,false);
    imported.import_event_to_stable_render_observation_ms=observedAt-imported.file_event.event_ms;
    imported.import_timing_includes_file_selected_snapshot_and_ack_dwell=true;
    imported.input_proxy=await inputProxy();await emitStage(cycle,source.id,'imported',imported);
    await command('Inspect source');const details=await page.locator('#source-details').textContent();
    for(const value of [source.filename,source.source_sha256,source.vertices.toLocaleString(),source.triangles.toLocaleString()])assert.ok(details.includes(value));
    await emitStage(cycle,source.id,'inspected',{...await snapshot(),source_dom_verified:true});
    if(source.id==='raw')await emitStage(cycle,source.id,'selection-overlay',{skipped:true,reason:'Raw source display measured completely; bounded real selection overlay exercised on generated scene/grid, not a fitting capacity claim.'});
    else{
      await page.getByRole('button',{name:'Selection',exact:true}).click();
      await page.locator('#selection-shape').selectOption('rectangle');await page.locator('#selection-depth').selectOption('through_all');
      await page.locator('#tool').selectOption('replace');await page.locator('#top').click();await framesSettled();
      const box=await page.locator('#viewport canvas').boundingBox();assert.ok(box);
      const rect=source.id==='scene'?{x0:box.x+8,y0:box.y+65,x1:box.x+box.width-8,y1:box.y+box.height-35}:
        {x0:box.x+box.width*.475,y0:box.y+box.height*.475,x1:box.x+box.width*.525,y1:box.y+box.height*.525};
      await page.mouse.move(rect.x0,rect.y0);await page.mouse.down();await page.mouse.move(rect.x1,rect.y1,{steps:10});await page.mouse.up();
      await page.waitForFunction(async()=>{const s=await globalThis.__nativeSourceUi285();return s.selection.some(v=>v.count>0)&&s.selected_index_count>0&&!s.inert;},null,{timeout:60000});
      await framesSettled();const overlay=await snapshot();assert.equal(overlay.ui.selection.length,1);
      const selected=overlay.ui.selection[0];assert.ok(selected.count>0&&selected.count<=25000);assert.ok(selected.min_id>=0&&selected.max_id<source.vertices);
      assert.ok(overlay.ui.selected_index_count>0);assert.equal(overlay.ui.selected_visible,true);assert.equal(overlay.ui.fit_nodes,0);
      overlay.rectangle=rect;overlay.input_proxy=await inputProxy();await emitStage(cycle,source.id,'selection-overlay',overlay);
    }
    await command('New project');const empty=await stable(null);assert.ok(empty.generation>previousGeneration);previousGeneration=empty.generation;
    assert.equal(empty.source_nodes,0);assert.equal(empty.recipe_nodes,0);assert.equal(empty.vertices,0);assert.equal(empty.indices_length,0);
    assert.equal(empty.positions_length,0);assert.equal(empty.selected_index_count,0);assert.equal(empty.input_files,0);
    for(const view of empty.arrays.views.filter(v=>v.name==='positions'||/^(mesh|selectedPoints|focusedPoints|overlapMarkers|overlapHalo)\./.test(v.name)))assert.equal(view.logical_bytes,0);
    assert.equal(await page.locator('[data-add-scan]').isEnabled(),true);await emitStage(cycle,source.id,'reset',await snapshot());
  }
  assert.equal(pages,1);assert.equal(context.pages().length,1);assert.equal(await page.locator('#viewport canvas').count(),1);
  assert.equal(page.workers().length,0);assert.equal(report.page_error_count,0);assert.equal(report.external_request_count,0);
  report.checks={all_cycles_complete:true,one_page_context_origin:true,actual_picker_source_reset:true,identities_counts_hashes:true,
    generation_increases:true,one_source_enforced:true,full_source_retained:true,bounded_real_overlay:true,reset_releases_display:true,no_fit_or_capacity_changes:true,
    no_page_errors:true,no_external_requests:true};report.complete=true;
}catch(error){report.failure=String(error);process.exitCode=1;}
finally{
  try{if(browser)await browser.close();}catch(error){report.instrumentation_errors.push(String(error));report.complete=false;process.exitCode=1;}
  if(report.page_error_count||report.external_request_count){
    report.complete=false;process.exitCode=1;report.failure??='Page errors or external requests occurred, including during shutdown.';
  }
  ackReader?.close();report.finished_utc=new Date().toISOString();
  const file=await open(reportPath,'wx');try{await file.writeFile(JSON.stringify(report,null,2)+'\n');await file.sync();}finally{await file.close();}
  process.stdout.write(JSON.stringify({schema_version:1,event:'finished',report_path:reportPath,complete:report.complete})+'\n');
}
