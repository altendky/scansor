// Explicit test-only instrumentation. On-disk artifact bytes remain unchanged.
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import { verifySite, startPreview, prepareReports, sha256 } from './server.mjs';
const args=process.argv.slice(2);
const argument=(name,fallback)=>{
  const index=args.indexOf(name);
  if (index<0) return fallback;
  if (!args[index+1]||args[index+1].startsWith('--')) throw Error(`Missing ${name}`);
  return args[index+1];
};
const directory=argument('--directory'), outputArgument=argument('--output');
if (!directory||!outputArgument) throw Error('Usage: node measure-memory.mjs --directory <verified CAD site> --output <fresh external reports> [--playwright-directory experiments/browser_viewer] [--isolation]');
const site=await verifySite(directory);
const playwrightDirectory=path.resolve(argument('--playwright-directory','experiments/browser_viewer'));
const repository=path.resolve(argument('--repository',path.join(playwrightDirectory,'../..')));
const ownRepository=fileURLToPath(new URL('../..',import.meta.url));
const output=await prepareReports(outputArgument,[site.root,repository,ownRepository]);
process.env.TMPDIR=output;
const playwright=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
const pythonInventory = `
def _test_array_inventory():
    import json, numpy as np, sys
    from collections import Counter
    b = browser_workspace
    pairs = [('source.xyz', b.workspace.data.xyz), ('source.normals', b.workspace.data.normals),
             ('source.triangles', b.workspace.data.triangles), ('source.weights', b.workspace.data.weights),
             ('source.ids', b.workspace.data.ids), ('solver.local', b.workspace.local),
             ('frame.origin', b.workspace.origin), ('frame.columns', b.workspace.frame),
             ('display.positions', b.display_positions), ('display.indices', b.display_indices)]
    roots, views = {}, []
    for name, a in pairs:
        owner = a
        while isinstance(owner.base, np.ndarray):
            owner = owner.base
        address = str(owner.__array_interface__['data'][0])
        roots[address] = int(owner.nbytes)
        views.append(dict(name=name, dtype=str(a.dtype), shape=list(a.shape), logical_bytes=int(a.nbytes),
                          root_address=address, root_bytes=int(owner.nbytes), root_dtype=str(owner.dtype),
                          non_ndarray_base=type(owner.base).__name__, writeable=bool(a.flags.writeable)))
    return json.dumps(dict(views=views, unique_root_bytes=sum(roots.values()),
                           export_bytes=len(b._export_buffer),
                           graph_states=dict(Counter(b.graph._states.values())),
                           cad_imported='OCP' in sys.modules,
                           caveat='Named NumPy payload only; excludes Python objects, lists, graph results, allocator free space and solver temporaries.'))
_test_array_inventory()
`;
const workerSuffix = `\n// Test-only hook; exact production source closure remains unchanged on disk.\n
globalThis.__memory209 = () => {
  const proxy = python.runPython('browser_workspace.display_positions');
  let view;
  try {
    view = proxy.getBuffer('u8');
    const capacity = view.data.buffer.byteLength;
    return { linear_capacity_bytes: capacity, named_numpy_payload: JSON.parse(python.runPython(${JSON.stringify(pythonInventory)})),
      measured_at_ms: performance.now(), capacity_caveat: 'Capacity at this stage; not live allocation, RSS or sampled intra-call peak.' };
  } finally { view?.release(); proxy.destroy(); }
};\n`;
const mainSuffix = `\n// Test-only CPU display backing-store inventory, no retained array references.\n
$('auto-evaluate').checked = false; // Test-only: retain a genuine pre-solve stage with unchanged source/default recipe.
globalThis.__evaluate209 = () => evaluateAll();
globalThis.__publish209 = value => acceptGraph(value);
globalThis.__selectExample209 = () => sourceProject.selectExample('nozzle');
globalThis.__mainArrays209 = () => {
  const arrays = [], buffers = new Map();
  const add = (name, array) => { if (!array?.buffer) return;
    if (!buffers.has(array.buffer)) buffers.set(array.buffer, buffers.size);
    arrays.push({name, logical_bytes:array.byteLength, backing_id:buffers.get(array.buffer), backing_bytes:array.buffer.byteLength}); };
  add('positions', positions);
  scene?.traverse(object => { if (!object.geometry) return;
    for (const [key, attribute] of Object.entries(object.geometry.attributes)) add(object.type+'.'+key, attribute.array || attribute.data?.array);
    add(object.type+'.indices', object.geometry.index?.array);
  });
  return {views:arrays, unique_backing_bytes:[...buffers.keys()].reduce((n,b)=>n+b.byteLength,0),
    caveat:'Scene geometry typed-array backing stores; excludes graph objects, selection caches and DOM. Shared arrays counted once.'};
};\n`;
let browser, preview;
const report = { started_utc: new Date().toISOString(), source_revision: site.manifest.source_revision,
  site_manifest_sha256: site.manifest_sha256, source_site:site.root,
  runtime:site.manifest.runtime,
  host:{platform:os.platform(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpus:os.cpus().length},
  instrumentation: { worker_suffix_sha256:sha256(Buffer.from(workerSuffix)), main_suffix_sha256:sha256(Buffer.from(mainSuffix)),
    routing:'Test route appends diagnostics in memory only. Routing disables browser cache; this is not a performance or startup timing measurement.',
    auto_evaluation:'Unchecked in test-only appended UI hook to preserve an unevaluated baseline. Immutable source/default recipe unchanged.',
    repeats:'Five fresh graph resets through a test-only invocation of the production source-project selectExample controller, including complete workspace adoption, then numerical evaluation; bounded face authoring, five CAD exports, actual UI Force and reevaluation. This hook does not test the example chooser. Boundary samples cannot observe synchronous-call allocation peaks.' },
  stages:[], errors:[], requests:[], unexpected_requests:[], api_requests:[] };
const watchdog=setTimeout(()=>{report.failure='Memory validation exceeded its 12-minute harness watchdog.';void browser?.close();},720000);
try {
    preview = await startPreview(site, {prefix:'/memory209/',isolation:args.includes('--isolation')});
  browser = await playwright.chromium.launch({headless:true});
  report.browser = {name:'chromium', version:browser.version(),headless:true};
  const context = await browser.newContext({serviceWorkers:'block', viewport:{width:1440,height:1000}});
  context.on('request',request=>{
    const url=request.url();report.requests.push(url);
    if (!url.startsWith(preview.origin+'/')&&!url.startsWith('blob:'+preview.origin+'/')) report.unexpected_requests.push(url);
    if (/\/api(?:\/|$)/.test(new URL(url).pathname)) report.api_requests.push(url);
  });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== preview.origin) return route.abort();
    const suffix = /\/worker\.mjs$/.test(url.pathname) ? workerSuffix : /\/app\.js$/.test(url.pathname) ? mainSuffix : null;
    if (!suffix) return route.continue();
    const relative=url.pathname.slice('/memory209/'.length);
    if (!site.records.has(relative)) throw Error('Unlisted instrumented source');
    const baseBytes=await readFile(path.join(site.root,relative)), record=site.records.get(relative);
    assert.equal(baseBytes.length,record.bytes,'Instrumented base length changed');
    assert.equal(sha256(baseBytes),record.sha256,'Instrumented base hash changed');
    await route.fulfill({status:200, contentType:'text/javascript',
      headers:args.includes('--isolation')?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{},
      body:Buffer.concat([baseBytes,Buffer.from(suffix)])});
  });
  const page = await context.newPage();
  page.on('pageerror',error=>report.errors.push(String(error)));
  await page.addInitScript(() => {
    const contexts = [], getContext=HTMLCanvasElement.prototype.getContext;
    window.__gpu209 = () => ({contexts:contexts.map(s=>({ logical_live_buffer_bytes:[...s.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0),
      logical_high_water_bytes:s.high, buffer_data_calls:s.calls, deleted_buffers:s.deleted,
      renderer:s.renderer,canvas:[s.gl.drawingBufferWidth,s.gl.drawingBufferHeight], context_lost:s.gl.isContextLost()})),
      caveat:'Logical WebGL buffer sizes only; excludes textures, shaders, framebuffers, driver copies and physical GPU memory.'});
    HTMLCanvasElement.prototype.getContext=function(...args) {
      const gl=getContext.apply(this,args);
      if (!gl || !/^webgl/.test(args[0]) || contexts.some(s=>s.gl===gl)) return gl;
      const debug=gl.getExtension('WEBGL_debug_renderer_info');
      const state={gl,buffers:new Map(),ids:new WeakMap(),next:0,high:0,calls:0,deleted:0,
        renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER)}; contexts.push(state);
      const data=gl.bufferData, del=gl.deleteBuffer;
      const binding=new Map([[gl.ARRAY_BUFFER,gl.ARRAY_BUFFER_BINDING],[gl.ELEMENT_ARRAY_BUFFER,gl.ELEMENT_ARRAY_BUFFER_BINDING],
        [gl.COPY_READ_BUFFER,gl.COPY_READ_BUFFER_BINDING],[gl.COPY_WRITE_BUFFER,gl.COPY_WRITE_BUFFER_BINDING],
        [gl.PIXEL_PACK_BUFFER,gl.PIXEL_PACK_BUFFER_BINDING],[gl.PIXEL_UNPACK_BUFFER,gl.PIXEL_UNPACK_BUFFER_BINDING],
        [gl.UNIFORM_BUFFER,gl.UNIFORM_BUFFER_BINDING],[gl.TRANSFORM_FEEDBACK_BUFFER,gl.TRANSFORM_FEEDBACK_BUFFER_BINDING]]);
      gl.bufferData=function(...values) {
        const returned=data.apply(this,values), target=values[0], query=binding.get(target);
        if (query===undefined) return returned;
        const buffer=this.getParameter(query), size=this.getBufferParameter(target,this.BUFFER_SIZE);
        if (!buffer || !Number.isFinite(size)) return returned;
        let id=state.ids.get(buffer); if (!id) {id=++state.next;state.ids.set(buffer,id);}
        state.buffers.set(id,{ref:new WeakRef(buffer),bytes:size}); state.calls++;
        state.high=Math.max(state.high,[...state.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0));
        return returned;
      };
      gl.deleteBuffer=function(buffer) {const returned=del.call(this,buffer); const id=state.ids.get(buffer);
        if (state.buffers.delete(id)) state.deleted++; return returned;};
      return gl;
    };
  });
  const cdp=await context.newCDPSession(page), workerSessions=new Map(), pending=new Map();let next=0;
  cdp.on('Target.attachedToTarget',event=>{if(event.targetInfo.type==='worker')workerSessions.set(event.sessionId,event.targetInfo);});
  cdp.on('Target.detachedFromTarget',event=>workerSessions.delete(event.sessionId));
  cdp.on('Target.receivedMessageFromTarget',event=>{const message=JSON.parse(event.message), callback=pending.get(message.id);
    if(callback){pending.delete(message.id);callback(message);}});
  await cdp.send('Target.setAutoAttach',{autoAttach:true, waitForDebuggerOnStart:false, flatten:false});
  async function workerMetric(sessionId) {
    const id=++next;let timer;
    try {return await new Promise(async(resolve,reject)=>{
      timer=setTimeout(()=>{pending.delete(id);reject(Error('Worker metric timed out'));},5000);
      pending.set(id,reply=>reply.error?reject(Error(JSON.stringify(reply.error))):resolve(reply.result));
      try{await cdp.send('Target.sendMessageToTarget',{sessionId,message:JSON.stringify({id,method:'Runtime.getHeapUsage'})});}catch(e){pending.delete(id);reject(e);}
    });} finally {clearTimeout(timer);}
  }
  async function sample(name) {
    let workers;
    for(let attempt=0;attempt<100;attempt++) {
      workers=page.workers();
      if(workers.length===1&&workerSessions.size===1&&[...workerSessions.values()][0].url===workers[0].url()) break;
      await new Promise(resolve=>setTimeout(resolve,50));
    }
    assert.equal(workers.length,1,'Expected one live production worker');
    assert.equal(workerSessions.size,1,'Dedicated-worker CDP attachment missing or stale');
    assert.equal([...workerSessions.values()][0].url,workers[0].url(),'CDP metric target differs from the production worker');
    const main=await cdp.send('Runtime.getHeapUsage');
    const worker_heap=[];for(const[id,target]of workerSessions)worker_heap.push({target_url:target.url,session_id:id,...await workerMetric(id)});
    const wasm=[];for(const worker of workers)wasm.push({url:worker.url(),...await worker.evaluate(()=>globalThis.__memory209())});
    report.stages.push({name,at_utc:new Date().toISOString(),main_isolate:main,worker_isolates:worker_heap,worker_wasm:wasm,
      main_display_arrays:await page.evaluate(()=>globalThis.__mainArrays209()),webgl:await page.evaluate(()=>globalThis.__gpu209()),
      optional_memory_api:await page.evaluate(()=>({isolated:crossOriginIsolated,available:typeof performance.measureUserAgentSpecificMemory==='function'}))});
  }
  await page.goto(preview.origin+'/memory209/app/');
  await page.locator('#status[data-runtime-ready="true"]').waitFor({timeout:180000});
  await page.evaluate(async()=>{const{getBackend}=await import('./backend.js');const b=getBackend();for await(const state of b.graphUpdates()){}
    window.__backend209=b;});
  await sample('source_and_UI_ready');
  assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{unevaluated:30});
  await page.evaluate(()=>globalThis.__evaluate209());
  await sample('numerical_graph_complete_and_UI_published');
  assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{ready:30});
  for (let iteration = 1; iteration <= 5; iteration++) {
    page.once('dialog', dialog => dialog.accept());
    await page.evaluate(async()=>{
      const backend=window.__backend209, current=await backend.getGraph();
      if (!await globalThis.__selectExample209()) throw Error('Fresh example workspace was not adopted');
      const fresh=await backend.getGraph();
      if (fresh.project_generation !== current.project_generation + 1) throw Error('Fresh example did not advance the accepted source generation');
      if (!Object.values(fresh.states).every(value=>value==='unevaluated')) throw Error('Fresh graph reset retained derived results');
    });
    await page.evaluate(()=>globalThis.__evaluate209());
    await sample(`fresh_graph_solve_${iteration}_UI_published`);
    assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{ready:30});
  }
  const cad = await page.evaluate(async()=>{
    const backend = window.__backend209, initial = await backend.getGraph();
    const plane = initial.recipe.nodes.find(node=>node.operation==='fit'&&node.label==='top fit');
    const cone = initial.recipe.nodes.find(node=>node.operation==='fit'&&node.label==='outer fit');
    if (!plane || !cone) throw Error('Retained production fits absent');
    const surfaces = [{feature:plane.id},{feature:cone.id}];
    const candidates = await backend.faceCandidates({token:initial.token,target:surfaces[0],surfaces});
    return {surfaces,candidates: candidates.candidates.length};
  });
  assert.ok(cad.candidates>0);
  report.cad = cad;
  await sample('first_CAD_kernel_and_candidates_complete');
  const authored = await page.evaluate(async({surfaces})=>{
    const backend=window.__backend209, current=await backend.getGraph();
    const proposal=await backend.previewFaces({token:current.token,surfaces});
    const face=proposal.faces.find(item=>item.surface.feature===surfaces[0].feature);
    const region=face?.regions.find(item=>item.bounded);
    if (!region) throw Error('No bounded retained plane region');
    const applied=await backend.applyFaces({token:proposal.token,proposal_token:proposal.proposal_token,
      surfaces,label:'Memory diagnostic plane',choices:[{surface:face.surface,region_key:region.key}]});
    globalThis.__publish209(applied);
    return {nodes:applied.recipe.nodes.length,token:applied.token};
  },cad);
  report.cad.authored=authored;
  assert.ok(authored.nodes>30);
  await page.evaluate(()=>globalThis.__evaluate209());
  await sample('bounded_face_evaluated_and_UI_published');
  assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{ready:authored.nodes});
  report.exports=[];
  for (let iteration=1;iteration<=5;iteration++) {
    const exported=await page.evaluate(async()=>{
      const backend=window.__backend209, current=await backend.getGraph();
      const bytes=await backend.exportCad({token:current.token,scope:'all_faces',units:'Millimeters',
        axis_up:false,include_mesh:true});
      return {bytes:bytes.byteLength,array_buffer:bytes instanceof ArrayBuffer};
    });
    assert.ok(exported.array_buffer&&exported.bytes>0);
    report.exports.push(exported);
    await sample(`CAD_export_${iteration}_settled`);
    assert.equal(report.stages.at(-1).worker_wasm[0].named_numpy_payload.export_bytes,0);
  }
  await page.locator('#force-restart').click();
  await page.waitForFunction(()=>document.querySelector('#status')?.dataset.runtimeReady==='true'&&!document.querySelector('#evaluate-all').disabled,
    null,{timeout:180000});
  await sample('explicit_Force_recreated_worker_before_evaluation');
  assert.notEqual(report.stages.at(-1).worker_isolates[0].session_id,report.stages[0].worker_isolates[0].session_id,'Force did not replace the CDP worker target');
  assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{unevaluated:authored.nodes});
  await page.evaluate(()=>globalThis.__evaluate209());
  await sample('recovered_face_graph_evaluated_and_UI_published');
  assert.deepEqual(report.stages.at(-1).worker_wasm[0].named_numpy_payload.graph_states,{ready:authored.nodes});
  const after=await verifySite(site.root);
  report.artifact_unchanged=after.manifest_sha256===site.manifest_sha256;
  assert.ok(report.artifact_unchanged);
  assert.equal(report.errors.length,0);
  assert.deepEqual(report.unexpected_requests,[]);
  assert.deepEqual(report.api_requests,[]);
  report.completed_utc=new Date().toISOString();
} catch(error) { report.failure=String(error); throw error; }
finally {
  clearTimeout(watchdog);
  const cleanup=await Promise.allSettled([browser?.close(),preview?.close()]);
  report.cleanup_errors=cleanup.filter(result=>result.status==='rejected').map(result=>String(result.reason));
  if(report.cleanup_errors.length){report.failure??='Harness cleanup failed';process.exitCode=1;}
  await writeFile(path.join(output,'memory.json'),JSON.stringify(report,null,2)+'\n');
}
