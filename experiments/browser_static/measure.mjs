// Disposable, read-only-site timing probe. No page.route or modified responses.
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import os from 'node:os';
import {verifySite,startPreview,sha256,prepareReports} from './server.mjs';
const args=process.argv.slice(2);
const value=(flag,fallback)=>{if(!args.includes(flag))return fallback;const v=args[args.indexOf(flag)+1];if(!v||v.startsWith('--'))throw Error('Missing '+flag+' value');return v;};
if(!value('--directory')||!value('--output'))throw Error('Usage: node measure.mjs --directory <static-site> --output <fresh external reports> [--playwright-directory <viewer>] [--repository <checkout>] [--native-python <existing interpreter>]');
const playwrightDirectory=path.resolve(value('--playwright-directory',fileURLToPath(new URL('../browser_viewer',import.meta.url))));
const repository=path.resolve(value('--repository',path.join(playwrightDirectory,'../..')));
const directory=path.resolve(value('--directory'));
const nativePython=path.resolve(value('--native-python',path.join(repository,'.venv/bin/python')));
const playwright=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
const site=await verifySite(directory);
const output=await prepareReports(value('--output'),[site.root,repository,fileURLToPath(new URL('../..',import.meta.url))]);
process.env.TMPDIR=output; // Browser profiles/download staging stay inside the owned report directory.
const report={started_utc:new Date().toISOString(),source_revision:site.manifest.source_revision,
  site_manifest_sha256:site.manifest_sha256,runtime:site.manifest.runtime,
  host:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpus:os.cpus().length},
  method:{hosting:'Plain loopback, exact existing no-store static server. No route interception or response modification.',
    startup:'Fresh contexts; filesystem/OS/code caches and competing host activity uncontrolled. Not WAN or cache benefit.',
    auto:'Mutation observer unchecks existing Auto input before application evaluation, to separate startup/first fit.',
    repetitions:'Two warmup + five measured serial cached evaluation, fresh-graph evaluation and export operations; browser suite followed by native suite, not simultaneous.',
    numeric_cache:'Repeated Evaluate all reuses ready graph caches and measures scheduler/publication. Fresh samples reopen the frozen numeric recipe through the actual UI matching-actions transaction; accepted epoch/source and all30 unevaluated states are checked before timing. Preparation/adoption is outside the timed solve.',
    responsiveness:'16ms timer, continuous rAF observer, and canvas wheel/pointermove to render-stats mutations. Instrumentation adds small overhead.',
    native:'Same packaged source/recipes, existing interpreter, BLAS/OMP/MKL threads1. Adapter timings versus worker lifecycle and full UI latency are distinct.'},
  startup:[],cached_numeric_samples:[],fresh_numeric_samples:[],cad_samples:[],requests:[],page_errors:[],unexpected_requests:[]};
let browser,preview;
const watchdog=setTimeout(()=>{report.failure='20 minute watchdog';void browser?.close();},1200000);
function installProbe(){
  const p=window.timing209={events:[],inputs:[],renders:[],ticks:[],rafs:[],auto_disabled:false};
  const disable=()=>{const auto=document.querySelector('#auto-evaluate');if(auto){auto.checked=false;p.auto_disabled=true;return true;}return false;};
  if(!disable()){const observer=new MutationObserver(()=>{if(disable())observer.disconnect();});observer.observe(document,{childList:true,subtree:true});}
  let previous=performance.now();setInterval(()=>{const now=performance.now();p.ticks.push({at:now,gap:now-previous});previous=now;},16);
  let last;function frame(now){if(last!==undefined)p.rafs.push({at:now,gap:now-last});last=now;requestAnimationFrame(frame);}requestAnimationFrame(frame);
  const NativeWorker=Worker;
  window.Worker=class extends NativeWorker{
    constructor(...options){super(...options);this.addEventListener('message',({data})=>{
      const at=performance.now();
      if(data.phase)p.events.push({at,type:'phase',name:data.phase,elapsed_ms:data.elapsed_ms});
      if(data.ready||data.result?.ready){const result=data.result||data;p.events.push({at,type:'ready',versions:result.versions,source_revision:result.source_revision});}
      if(data.kind==='lifecycle')p.events.push({at,type:'lifecycle',id:data.id,operation:data.operation,state:data.state});
      if(data.kind==='graph')p.events.push({at,type:'graph',running:!!data.state.evaluation_running,states:Object.values(data.state.states||{})});
      if(data.kind==='transfer-confirmed')p.events.push({at,type:'transfer',id:data.id,positions:data.positions_bytes_after,indices:data.indices_bytes_after,export_bytes:data.export_bytes_after});
    });}
    postMessage(data,...rest){if(data?.operation)p.events.push({at:performance.now(),type:'request',id:data.id,operation:data.operation});return super.postMessage(data,...rest);}
  };
  for(const name of ['pointermove','wheel'])document.addEventListener(name,event=>{
    if(event.target===document.querySelector('#viewport canvas'))p.inputs.push({at:performance.now(),type:name,trusted:event.isTrusted});
  },{capture:true,passive:true});
  const changes=new MutationObserver(()=>{const stats=document.querySelector('#render-stats');if(!stats)return;
    const text=stats.textContent;if(p.last_stats!==text){p.last_stats=text;p.renders.push({at:performance.now(),text});}});
  changes.observe(document,{subtree:true,childList:true,characterData:true});
}
const graph=page=>page.evaluate(async()=>{const {getBackend}=await import(new URL('backend.js',document.baseURI));return getBackend().getGraph();});
async function waitComplete(page,offset,operation){
  await page.waitForFunction(({offset,operation})=>window.timing209.events.slice(offset).some(e=>e.type==='lifecycle'&&e.operation===operation&&e.state==='complete'),{offset,operation},{timeout:180000,polling:20});
}
function windowMetrics(probe,start,end){
  const summarize=rows=>{const gaps=rows.filter(x=>x.at>=start&&x.at<=end).map(x=>x.gap).sort((a,b)=>a-b);return {count:gaps.length,max_ms:gaps.at(-1)||null,p95_ms:gaps.length?gaps[Math.min(gaps.length-1,Math.floor(gaps.length*.95))]:null};};
  const inputs=probe.inputs.filter(e=>e.at>=start&&e.at<=end).map(e=>{const render=probe.renders.find(r=>r.at>=e.at);return {...e,render_latency_ms:render?render.at-e.at:null};});
  return {timer:summarize(probe.ticks),raf:summarize(probe.rafs),inputs};
}
async function evaluateUi(page,label,input=false){
  const before=await page.evaluate(()=>({offset:timing209.events.length,at:performance.now()}));
  const click=page.getByRole('button',{name:'Evaluate all',exact:true}).click();
  if(input){
    await page.waitForFunction(offset=>timing209.events.slice(offset).some(e=>e.type==='lifecycle'&&e.operation==='evaluateGraph'&&e.state==='running'),before.offset,{timeout:30000,polling:10});
    const box=await page.locator('#viewport canvas').boundingBox();
    await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.wheel(0,20);
  }
  await click;await waitComplete(page,before.offset,'evaluateGraph');
  await page.waitForFunction(()=>!document.querySelector('#evaluate-all').disabled,null,{timeout:30000});
  const after=await graph(page);assert.deepEqual(after.errors,{});assert.ok(Object.values(after.states).every(s=>s==='ready'));
  const probe=await page.evaluate(()=>timing209),events=probe.events.slice(before.offset);
  const running=events.find(e=>e.type==='lifecycle'&&e.operation==='evaluateGraph'&&e.state==='running');
  const complete=events.find(e=>e.type==='lifecycle'&&e.operation==='evaluateGraph'&&e.state==='complete');
  const requested=events.find(e=>e.type==='request'&&e.operation==='evaluateGraph');
  const entry={label,worker_running_ms:complete.at-running.at,request_to_complete_ms:complete.at-requested.at,
    ui_start_to_complete_ms:complete.at-before.at,metrics:windowMetrics(probe,running.at,complete.at),
    full_request_metrics:windowMetrics(probe,requested.at,complete.at),graph:after};
  for(const event of entry.metrics.inputs)event.within_worker_running=event.at>=running.at&&event.at<=complete.at;
  return entry;
}
async function chooseSurface(page,selector,id){
  const selected=await page.locator(selector).evaluate((select,id)=>[...select.options].find(o=>o.value&&JSON.parse(o.value).feature===id&&!JSON.parse(o.value).surface)?.value,id);
  assert.ok(selected);await page.locator(selector).selectOption(selected);
}
async function nativeReference(){
  const code=`import json,sys,tempfile,zipfile,time,platform,os
from pathlib import Path
import numpy as np,pydantic,OCP
runtime,output=map(Path,sys.argv[1:3])
recipes=json.loads((output/'recipes.json').read_text())
def unpack(text):
    value=json.loads(text)
    assert value['ok'],value
    return value['result']
with tempfile.TemporaryDirectory(prefix='native-',dir=output) as temporary:
    root=Path(temporary)
    with zipfile.ZipFile(runtime/'scansor.zip') as z:
        assert all((root/name).resolve().is_relative_to(root.resolve()) for name in z.namelist())
        z.extractall(root)
    sys.path.insert(0,str(root))
    from experiments.browser_runtime.workspace import BrowserWorkspace
    from experiments.browser_runtime.cad_cases import inspect_bundle
    result={'versions':{'python':platform.python_version(),'numpy':np.__version__,'pydantic':pydantic.__version__,'ocp':OCP.__version__},'threads':{k:os.environ.get(k) for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']},'cached_numeric_samples':[],'fresh_numeric_samples':[],'cad_samples':[]}
    workspace=BrowserWorkspace(root/'examples/nozzle-bayonette-simplified')
    assert workspace.workspace.local.dtype==np.dtype('float64') and not workspace.workspace.local.flags.writeable
    initial=unpack(workspace.call_json('getGraph'))
    initial=unpack(workspace.call_json('replaceRecipe',json.dumps({'token':initial['token'],'recipe':recipes['numeric']})))
    for i in range(7):
        workspace.checkpoint_operation()
        start=time.perf_counter()
        prepared=unpack(workspace.prepare_evaluation_json('evaluateGraph',json.dumps({'token':initial['token'],'all_actions':True})))
        snapshot=unpack(workspace.finish_evaluation_json()) if prepared['run'] else prepared['state']
        result['cached_numeric_samples'].append({'warmup':i<2,'milliseconds':(time.perf_counter()-start)*1000,'run':prepared['run'],'initial_graph_was_fresh':i==0})
        assert not snapshot['errors'],snapshot['errors']
    for i in range(7):
        current=unpack(workspace.call_json('getGraph'))
        unpack(workspace.call_json('selectExample',json.dumps({'token':current['token'],'example':'nozzle'})))
        current=unpack(workspace.call_json('getGraph'))
        current=unpack(workspace.call_json('importRecipe',json.dumps({'token':current['token'],'recipe':recipes['numeric']})))
        assert len(current['states'])==30 and set(current['states'].values())=={'unevaluated'}
        workspace.checkpoint_operation()
        start=time.perf_counter()
        prepared=unpack(workspace.prepare_evaluation_json('evaluateGraph',json.dumps({'token':current['token'],'all_actions':True})))
        snapshot=unpack(workspace.finish_evaluation_json()) if prepared['run'] else prepared['state']
        result['fresh_numeric_samples'].append({'warmup':i<2,'milliseconds':(time.perf_counter()-start)*1000,'run':prepared['run'],'unevaluated_nodes_before':30,'ready_nodes_after':sum(v=='ready' for v in snapshot['states'].values())})
        assert not snapshot['errors'] and set(snapshot['states'].values())=={'ready'},snapshot['errors']
    result['numeric_graph']=snapshot
    workspace.enable_cad()
    current=unpack(workspace.call_json('getGraph'))
    current=unpack(workspace.call_json('replaceRecipe',json.dumps({'token':current['token'],'recipe':recipes['cad']})))
    prepared=unpack(workspace.prepare_evaluation_json('evaluateGraph',json.dumps({'token':current['token'],'all_actions':True})))
    snapshot=unpack(workspace.finish_evaluation_json()) if prepared['run'] else prepared['state']
    result['cad_graph']=snapshot
    for i in range(7):
        workspace.checkpoint_operation()
        start=time.perf_counter()
        descriptor=unpack(workspace.call_json('exportCad',json.dumps({'token':snapshot['token'],'scope':'all_faces','units':'Centimeters','axis_up':False,'include_mesh':True})))
        result['cad_samples'].append({'warmup':i<2,'milliseconds':(time.perf_counter()-start)*1000,'bytes':descriptor['bytes']})
        workspace.clear_export()
    result['browser_zip_readback']=inspect_bundle((output/'ui-export.zip').read_bytes(),workspace.workspace)
    (output/'native.json').write_text(json.dumps(result,allow_nan=False))
`;
  const executable=nativePython;
  await new Promise((resolve,reject)=>{
    const child=spawn(executable,['-I','-c',code,path.join(site.root,site.manifest.runtime.path),output],{
      cwd:output,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1',MKL_NUM_THREADS:'1',TMPDIR:output},stdio:['ignore','pipe','pipe']});
    let text='',timedOut=false,killTimer;
    const collect=data=>{text=(text+data.toString()).slice(-12000);};
    child.stdout.on('data',collect);child.stderr.on('data',collect);
    const timer=setTimeout(()=>{timedOut=true;child.kill('SIGTERM');killTimer=setTimeout(()=>child.kill('SIGKILL'),10000);},180000);
    child.once('error',error=>{clearTimeout(timer);clearTimeout(killTimer);reject(error);});
    child.once('close',(status,signal)=>{
      clearTimeout(timer);clearTimeout(killTimer);
      report.native_process={status,signal,timed_out:timedOut};
      if(status!==0||signal||timedOut) reject(Error(`Native reference failed (status=${status}, signal=${signal}, timeout=${timedOut}): ${text}`));
      else resolve();
    });
  });
  return JSON.parse(await readFile(path.join(output,'native.json'),'utf8'));
}
function compareGraph(actual,expected){let n=0,max=0,pathAt;
  function walk(a,b,p){if(typeof b==='number'){assert.equal(typeof a,'number',p);const d=Math.abs(a-b);assert.ok(Number.isFinite(a)&&d<=1e-10+Math.abs(b)*1e-10,p+': '+a+' != '+b);n++;if(d>max){max=d;pathAt=p;}}
    else if(Array.isArray(b)){assert.equal(a.length,b.length,p);b.forEach((v,i)=>walk(a[i],v,p+'['+i+']'));}
    else if(b!==null&&typeof b==='object'){assert.deepEqual(Object.keys(a).sort(),Object.keys(b).sort(),p);for(const k of Object.keys(b))walk(a[k],b[k],p+'.'+k);}
    else assert.deepEqual(a,b,p);}
  for(const key of ['recipe','token','states','errors','diagnostics','memberships','result','results','derived','input_catalogue','input_requirements'])walk(actual[key],expected[key],key);
  return {fields:n,max_absolute_difference:max,path:pathAt,rtol:1e-10,atol:1e-10};
}
try{
  preview=await startPreview(site,{prefix:'/timing209/'});
  browser=await playwright.chromium.launch({headless:true});report.browser={name:'chromium',version:browser.version(),playwright:createRequire(path.join(playwrightDirectory,'package.json'))('playwright/package.json').version};
  let firstPage,firstContext;
  for(let run=0;run<3;run++){
    const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000}});
    const page=await context.newPage();await page.addInitScript(installProbe);
    page.on('pageerror',e=>report.page_errors.push(String(e)));
    page.on('request',request=>{const url=request.url();report.requests.push(url);if(!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/'))report.unexpected_requests.push(url);if(/\/api(?:\/|$)/.test(new URL(url).pathname))report.unexpected_requests.push(url);});
    const started=performance.now();await page.goto(preview.origin+'/timing209/app/',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#status')?.dataset.runtimeReady==='true'&&!document.querySelector('#save').disabled,null,{timeout:180000});
    await page.locator('#viewport canvas').waitFor({state:'visible'});
    const initial=await graph(page);assert.ok(Object.values(initial.states).every(s=>s==='unevaluated'),'Startup must be pre-fit');
    const baseline=await page.evaluate(()=>({probe:timing209,isolated:crossOriginIsolated,resource_timing:performance.getEntriesByType('resource').map(e=>({name:e.name,duration:e.duration,transfer:e.transferSize,encoded:e.encodedBodySize,decoded:e.decodedBodySize}))}));
    const sample={run,wall_to_ui_ready_ms:performance.now()-started,baseline,...baseline};delete sample.baseline;
    const source=baseline.probe.events.find(e=>e.type==='request'&&e.operation==='loadWorkspace');
    const transfer=baseline.probe.events.find(e=>e.type==='transfer'&&e.positions===0);
    sample.display_request_to_transfer_ms=transfer?.at-source?.at;
    const sourcePhase=baseline.probe.events.find(e=>e.type==='phase'&&e.name==='Scansor source and frozen fixture'),ready=baseline.probe.events.find(e=>e.type==='ready');
    sample.source_phase_to_runtime_ready_ms=ready?.at-sourcePhase?.at;
    sample.source_phase_caveat='Includes source/fixture unpacking, Scansor imports, immutable nozzle validation/loading, graph setup and readiness publication; not isolated file ingestion.';
    sample.first_numeric=await evaluateUi(page,'first-numeric-'+run,true);report.startup.push(sample);
    console.log('cold',run,sample.wall_to_ui_ready_ms,'first-fit-worker',sample.first_numeric.worker_running_ms);
    if(run===0){firstPage=page;firstContext=context;}else await context.close();
  }
  const page=firstPage;
  for(let i=0;i<7;i++){const sample=await evaluateUi(page,i<2?'cached-warmup-'+i:'cached-sample-'+(i-2),true);sample.warmup=i<2;report.cached_numeric_samples.push(sample);console.log('cached',i,sample.worker_running_ms);}
  const frozenNumericRecipe=(await graph(page)).recipe;
  for(let i=0;i<7;i++){
    const previous=await page.evaluate(async()=>{const {getBackend}=await import(new URL('backend.js',document.baseURI));const b=await getBackend().loadWorkspace();return {generation:b.generation,identity:b.metadata.source_identity,mode:b.metadata.source_mode};});
    const approveActions=page.waitForEvent('dialog').then(async dialog=>{assert.equal(dialog.type(),'confirm');assert.match(dialog.message(),/Replace the current actions\?/);await dialog.accept();});
    await page.locator('#file').setInputFiles({name:'fresh-nozzle.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(frozenNumericRecipe))});
    await approveActions;
    await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Actions loaded')&&document.querySelector('#file').value===''&&!document.querySelector('main').inert&&!document.querySelector('#load').disabled&&document.querySelector('#source-progress').hidden,null,{timeout:30000});
    const accepted=await page.evaluate(async()=>{const {getBackend}=await import(new URL('backend.js',document.baseURI));const b=await getBackend().loadWorkspace();return {generation:b.generation,metadata:b.metadata,graph:b.graph};});
    assert.equal(accepted.generation,previous.generation+1);
    assert.equal(accepted.metadata.project_generation,accepted.generation);assert.equal(accepted.graph.project_generation,accepted.generation);
    assert.deepEqual(accepted.metadata.source_identity,previous.identity);assert.deepEqual(accepted.metadata.source_mode,previous.mode);
    assert.deepEqual(accepted.metadata.source_mode,{kind:'example',example:'nozzle'});assert.deepEqual(accepted.graph.recipe,frozenNumericRecipe);
    const before=await graph(page);assert.equal(Object.keys(before.states).length,30);assert.ok(Object.values(before.states).every(s=>s==='unevaluated'));
    assert.equal(before.project_generation,accepted.generation);assert.equal(before.token,accepted.graph.token);
    const sample=await evaluateUi(page,i<2?'fresh-warmup-'+i:'fresh-sample-'+(i-2),true);sample.warmup=i<2;sample.unevaluated_nodes_before=30;sample.ready_nodes_after=Object.values(sample.graph.states).filter(s=>s==='ready').length;sample.preparation={previous,accepted};report.fresh_numeric_samples.push(sample);console.log('fresh',i,sample.worker_running_ms);
  }
  const numeric=await graph(page);
  const cone=numeric.recipe.nodes.find(n=>n.operation==='fit'&&n.label==='outer fit');
  const plane=numeric.recipe.nodes.find(n=>n.operation==='fit'&&n.label==='top fit');assert.ok(cone&&plane);
  await page.getByRole('checkbox',{name:'Auto',exact:true}).check();
  const firstCadStart=await page.evaluate(()=>({at:performance.now(),offset:timing209.events.length}));
  await page.getByRole('button',{name:'Surface intersection',exact:true}).click();
  await page.locator('#new-surface-intersection-label').fill('Timing shoulder boundary');
  await chooseSurface(page,'#new-intersection-first',cone.id);await chooseSurface(page,'#new-intersection-second',plane.id);
  const submitAt=await page.evaluate(()=>performance.now());
  await page.locator('#add-surface-intersection-form').getByRole('button',{name:'Add intersection',exact:true}).click();
  await page.waitForFunction(async()=>{const {getBackend}=await import(new URL('backend.js',document.baseURI));const g=await getBackend().getGraph();const node=g.recipe.nodes.find(n=>n.label==='Timing shoulder boundary');return node&&g.states[node.id]==='ready';},null,{timeout:180000,polling:100});
  const withIntersection=await graph(page),intersection=withIntersection.recipe.nodes.find(n=>n.label==='Timing shoulder boundary');
  const cadProbe=await page.evaluate(()=>timing209);
  report.first_cad={ui_authoring_started_at:firstCadStart.at,submit_at:submitAt,ready_at:await page.evaluate(()=>performance.now()),phases:cadProbe.events.slice(firstCadStart.offset).filter(e=>e.type==='phase'),events:cadProbe.events.slice(firstCadStart.offset)};
  report.first_cad.submit_to_ready_ms=report.first_cad.ready_at-submitAt;
  console.log('first-cad',report.first_cad.submit_to_ready_ms);
  await page.getByRole('button',{name:'Trimmed face',exact:true}).click();await page.locator('#new-trimmed-face-label').fill('肩面 Ø12 — café');
  await chooseSurface(page,'#new-face-surface',plane.id);await page.locator('#add-new-face-boundary').click();
  await page.locator('#new-face-boundary-rows [data-boundary-intersection]').selectOption(intersection.id);await page.locator('#new-face-boundary-rows [data-boundary-keep]').selectOption('inside');
  await page.locator('#add-trimmed-face-form').getByRole('button',{name:'Add face',exact:true}).click();
  await page.waitForFunction(async()=>{const {getBackend}=await import(new URL('backend.js',document.baseURI));const g=await getBackend().getGraph();const node=g.recipe.nodes.find(n=>n.label==='肩面 Ø12 — café');return node&&g.states[node.id]==='ready';},null,{timeout:60000,polling:100});
  await page.getByRole('checkbox',{name:'Auto',exact:true}).uncheck();
  const cad=await graph(page);assert.deepEqual(cad.errors,{});
  await page.getByRole('button',{name:'Export CAD…',exact:true}).click();await page.locator('#export-scope').selectOption('all_faces');await page.locator('#export-units').selectOption('Centimeters');await page.locator('#export-mesh').check();
  for(let i=0;i<7;i++){
    if(i){await page.getByRole('button',{name:'Export CAD…',exact:true}).click();await page.locator('#export-scope').selectOption('all_faces');await page.locator('#export-units').selectOption('Centimeters');await page.locator('#export-mesh').check();}
    const before=await page.evaluate(()=>({at:performance.now(),offset:timing209.events.length}));
    const downloaded=page.waitForEvent('download',{timeout:120000});await page.locator('#export-download').click();const download=await downloaded;
    assert.equal(await download.failure(),null);if(i===6)await download.saveAs(path.join(output,'ui-export.zip'));
    await waitComplete(page,before.offset,'exportCad');
    const probe=await page.evaluate(()=>timing209),events=probe.events.slice(before.offset),start=events.find(e=>e.type==='lifecycle'&&e.operation==='exportCad'&&e.state==='running'),end=events.find(e=>e.type==='lifecycle'&&e.operation==='exportCad'&&e.state==='complete'),request=events.find(e=>e.type==='request'&&e.operation==='exportCad');
    const sample={warmup:i<2,worker_running_ms:end.at-start.at,request_to_complete_ms:end.at-request.at,ui_start_to_complete_ms:end.at-before.at,metrics:windowMetrics(probe,start.at,end.at)};report.cad_samples.push(sample);console.log('cad',i,sample.worker_running_ms);
  }
  await writeFile(path.join(output,'recipes.json'),JSON.stringify({numeric:numeric.recipe,cad:cad.recipe}));
  await writeFile(path.join(output,'browser-graphs.json'),JSON.stringify({numeric,cad}));
  report.final_probe=await page.evaluate(()=>timing209);
  await firstContext.close();await browser.close();browser=undefined;
  report.native=await nativeReference();
  report.parity={numeric:compareGraph(numeric,report.native.numeric_graph),cad:compareGraph(cad,report.native.cad_graph)};
  for(const sample of [...report.cached_numeric_samples,...report.fresh_numeric_samples])assert.deepEqual(compareGraph(sample.graph,numeric).max_absolute_difference,0);
  report.served_requests=preview.requests;
  report.served_body_bytes=preview.requests.reduce((sum,r)=>sum+(r.bytes||0),0);
  const unique=new Map(preview.requests.filter(r=>r.status===200&&r.bytes).map(r=>[r.path,r.bytes]));report.unique_served_body_bytes=[...unique.values()].reduce((a,b)=>a+b,0);
  assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.unexpected_requests,[]);
  report.artifact_unchanged=(await verifySite(directory)).manifest_sha256===site.manifest_sha256;assert.ok(report.artifact_unchanged);
  const summary=rows=>{const values=rows.filter(r=>!r.warmup).map(r=>r.worker_running_ms??r.milliseconds).sort((a,b)=>a-b);return {median_ms:values[Math.floor(values.length/2)],range_ms:[values[0],values.at(-1)],n:values.length};};
  report.summary={browser_cached_graph:summary(report.cached_numeric_samples),native_cached_graph:summary(report.native.cached_numeric_samples),browser_fresh_graph:summary(report.fresh_numeric_samples),native_fresh_graph:summary(report.native.fresh_numeric_samples),browser_cad:summary(report.cad_samples),native_cad:summary(report.native.cad_samples)};
  report.interpretation={input:'Wheel to next render-stats update is a render-submission proxy. Pointermove-to-next-render includes later wheel input and is not independent pointer response latency.',
    worker:'Lifecycle timestamps are main-thread receipt times and include adapter/publication work; they are not isolated solver CPU times.',
    startup:'First context remained open but computationally idle during subsequent startup runs. Fresh contexts do not clear OS/disk/code caches.',
    cache:'Current no-store preview and duplicate verification/runtime reads were measured directly. No warm HTTP-cache or production Pages-cache benefit was demonstrated.'};
  console.log(JSON.stringify({summary:report.summary,parity:report.parity,artifact_unchanged:report.artifact_unchanged},null,2));
}catch(error){report.failure=String(error);console.error(error);process.exitCode=1;}
finally{
  clearTimeout(watchdog);
  const cleanup=await Promise.allSettled([browser?.close(),preview?.close()]);
  report.cleanup_errors=cleanup.filter(result=>result.status==='rejected').map(result=>String(result.reason));
  if(report.cleanup_errors.length){report.failure??='Harness cleanup failed';process.exitCode=1;}
  report.finished_utc=new Date().toISOString();await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
}
