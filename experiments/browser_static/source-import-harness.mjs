// Trusted project harness helpers. Production artifact bytes stay unchanged.
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {sha256} from './server.mjs';
import {sourceAuthorityPython} from './source-authority-observer.mjs';

const helper = fileURLToPath(new URL('./source_import_expected.py', import.meta.url));
export async function runSourcePython(nativePython, args, output) {
  await new Promise((resolve, reject) => {
    const child = spawn(nativePython, ['-I', helper, ...args], {cwd:output,
      env:{...process.env,OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1',MKL_NUM_THREADS:'1',PYTHONDONTWRITEBYTECODE:'1',TMPDIR:output},stdio:['ignore','pipe','pipe']});
    let message='', expired=false;
    const collect=chunk=>{message=(message+chunk.toString()).slice(-12000);};
    child.stdout.on('data',collect);child.stderr.on('data',collect);
    const timer=setTimeout(()=>{expired=true;child.kill();},180000);
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('close',(code,signal)=>{clearTimeout(timer);code===0&&!expired?resolve():reject(Error(`Source reference failed (${code},${signal}): ${message}`));});
  });
}
export async function prepareSourceInputs({repository, output, nativePython}) {
  await mkdir(output); // Never overwrite frozen inputs or old evidence.
  await runSourcePython(nativePython,['inputs','--repository',repository,'--output',output],output);
  const manifest=JSON.parse(await readFile(path.join(output,'manifest.json'),'utf8'));
  return {...manifest,cases:manifest.cases.map(item=>({...item,path:path.join(output,item.name+'.ply')}))};
}

export const sourceEvidenceSuffix = `
// Test-only safe-boundary inventory, without mutating accepted source state.
globalThis.__sourceEvidence242 = (arrays = true) => {
 const code = ${JSON.stringify(sourceAuthorityPython)} + "import json\\nb=browser_workspace;w=b.workspace\\nrecord={'descriptor':w.source_descriptor.model_dump() if w.source_descriptor else None,'source_sha256':w.source_sha256,'project_generation':b.workspace_generation,'source_mode':b.source_mode,**_source_authority_observation(w),'vertices':len(w.local),'faces':len(w.data.triangles)}\\n" +
 (arrays ? "record.update(positions=w.local.tolist(),normals=w.data.normals.tolist(),triangles=w.data.triangles.tolist(),weights=w.data.weights.tolist())\\n" : '') + "json.dumps(record,allow_nan=False)";
 const record = JSON.parse(python.runPython(code));
 const files=[];
 if(python.FS.analyzePath('/scansor-source-staging').exists)for(const name of python.FS.readdir('/scansor-source-staging')){
   if(name==='.'||name==='..')continue;const file='/scansor-source-staging/'+name,info=python.FS.stat(file);files.push({path:file,bytes:info.size});
 }
 record.owned_fs_files=files;record.owned_fs_bytes=files.reduce((sum,item)=>sum+item.bytes,0);
 record.worker_protocol=manifest.worker_protocol.version;
 if(typeof sourceTransferMetrics!=='undefined')Object.assign(record,sourceTransferMetrics,
   {staging_high_water_bytes:sourceTransferMetrics.max_staging_bytes,max_chunk_bytes:sourceTransferMetrics.largest_chunk_bytes});
 return record;
};
globalThis.__sourceCleanupFault242 = (mode='candidate') => {
 if(manifest.worker_protocol.version>=12){
  if(mode==='candidate'){
   const call=sourceCall;let armed=true;
   sourceCall=function(method,...args){
    if(armed&&method==='discard_source_json'){armed=false;throw Error('Injected private source candidate disposal failure');}
    return call(method,...args);
   };return;
  }
  // A real bounded read fails before publication; subsequent Force replays the
  // previous accepted authority. No obsolete MEMFS cleanup path is exercised.
  const read=FileReaderSync.prototype.readAsArrayBuffer;let armed=true;
  FileReaderSync.prototype.readAsArrayBuffer=function(blob){
   if(armed){armed=false;throw Error('Injected bounded source replay failure');}
   return read.call(this,blob);
  };return;
 }
 const unlink=python.FS.unlink;let armed=true;
 python.FS.unlink=function(path){
  if(armed&&path.startsWith('/scansor-source-staging/')){armed=false;throw Error('Injected owned staging unlink failure');}
  return unlink.apply(this,arguments);
 };
};
`;

export async function openSourceImportPage(page,{site,preview,prefix='/source-import/',extraWorkerSuffix='',initialMode='empty',isolation=false,captureTransport=false}) {
  const context=page.context(), suffix=sourceEvidenceSuffix+extraWorkerSuffix;
  await context.route('**/worker.mjs',async route=>{
    const url=new URL(route.request().url()),relative=url.pathname.slice(prefix.length);
    assert.ok(site.records.has(relative),'Unlisted instrumented worker');
    const bytes=await readFile(path.join(site.root,relative)),record=site.records.get(relative);
    assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(suffix)]),
      headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
  });
  await page.goto(preview.origin+prefix);
  await page.evaluate(async({prefix,initialMode,captureTransport})=>{
    const configuration=(await import(prefix+'app/backend-config.js')).default;
    const {createWorkerBackend}=await import(prefix+'app/worker-backend.js');
    window.__sourceImportProgress242=[];window.__sourceImportLifecycle242=[];
    const transport=window.__sourceImportTransport242={held:[],holdIncoming:null,holdPromotion:false,heldRequest:null,nextWorker:0,
      release(){const held=this.held.splice(0);this.heldRequest=null;for(const item of held)item.deliver();}};
    const workerFactory=(url,options)=>{
      const native=new Worker(url,options),index=++transport.nextWorker;let receive;
      const facade={postMessage(message,transfers){
        if(transport.holdPromotion&&message.operation==='accept-source-boundary'&&message.accepted){
          transport.held.push({worker:index,direction:'outgoing',kind:message.operation,id:message.id,deliver:()=>native.postMessage(message,transfers)});return;
        }native.postMessage(message,transfers);
      },terminate(){transport.held=transport.held.filter(item=>item.worker!==index);native.terminate();},
      set onmessage(value){receive=value;},set onerror(value){native.onerror=value;},set onmessageerror(value){native.onmessageerror=value;}};
      native.onmessage=event=>{
        const data=event.data;
        if(data.kind===transport.holdIncoming||(transport.heldRequest===data.id&&data.kind==='transfer-confirmed')){
          transport.heldRequest=data.id;transport.held.push({worker:index,direction:'incoming',kind:data.kind,id:data.id,deliver:()=>receive(event)});return;
        }receive(event);
      };return facade;
    };
    window.__sourceImportBackend242=createWorkerBackend({...configuration,initialMode,
      ...(captureTransport?{workerFactory}:{}),
      runtimeURL:new URL(configuration.runtimeURL,new URL(prefix+'app/',location.origin)).href,
      onPhase:phase=>window.__sourceImportProgress242.push({phase,at:performance.now()}),
      onLifecycle:state=>window.__sourceImportLifecycle242.push({...state,at:performance.now()})});
    const input=document.createElement('input');input.type='file';input.id='source-file242';document.body.append(input);
    await window.__sourceImportBackend242.initialize();
  },{prefix,initialMode,captureTransport});
  return {workerSuffixSha256:sha256(Buffer.from(suffix))};
}
export async function chooseSourceFile(page,file) {
  await page.locator('#source-file242').setInputFiles(file);
  await page.evaluate(()=>{window.__sourceImportFile242=document.querySelector('#source-file242').files[0];});
}
export async function readSourceEvidence(page,{arrays=true}={}) {
  const workers=page.workers();assert.equal(workers.length,1,'Expected one production worker');
  return workers[0].evaluate(arrays=>globalThis.__sourceEvidence242(arrays),arrays);
}
export function compareSourceNumbers(actual,expected,location='result') {
  let numbers=0,maxDifference=0;
  const walk=(value,reference,name)=>{
    if(typeof reference==='number') {assert.equal(typeof value,'number',name);const difference=Math.abs(value-reference);
      assert.ok(Number.isFinite(value)&&Number.isFinite(reference)&&difference<=1e-10+1e-10*Math.abs(reference),`${name}: ${value} != ${reference}`);numbers++;maxDifference=Math.max(maxDifference,difference);
    } else if(Array.isArray(reference)){assert.ok(Array.isArray(value),name);assert.equal(value.length,reference.length,name);reference.forEach((item,index)=>walk(value[index],item,name+'['+index+']'));
    } else if(reference!==null&&typeof reference==='object'){assert.deepEqual(Object.keys(value).sort(),Object.keys(reference).sort(),name);for(const[key,item]of Object.entries(reference))walk(value[key],item,name+'.'+key);
    } else assert.deepEqual(value,reference,name);
  };walk(actual,expected,location);return{numbers,max_difference:maxDifference,atol:1e-10,rtol:1e-10};
}
