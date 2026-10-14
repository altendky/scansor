// Disposable candidate project harness. Actual source/default nozzle and site bytes remain unchanged.
import assert from 'node:assert/strict';
import { readFile, writeFile, lstat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifySite, startPreview, prepareReports, sha256 } from './server.mjs';
const args=process.argv.slice(2), argument=(key,fallback)=>{
  const at=args.indexOf(key);
  if (at < 0) return fallback;
  if (!args[at + 1] || args[at + 1].startsWith('--')) throw Error(`Missing ${key}`);
  return args[at + 1];
};
const directory=argument('--directory'), outputArgument=argument('--output');
if (!directory || !outputArgument) throw Error('Usage: node measure-fitting.mjs --directory <immutable site> --output <fresh external reports> [--playwright-directory <installed viewer>] [--native-python <existing interpreter>] [--fixtures <frozen fixtures>] [--native-timeout-ms <1..120000>]');
const playwrightDirectory=path.resolve(argument('--playwright-directory','experiments/browser_viewer'));
const repository=path.resolve(argument('--repository',path.join(playwrightDirectory,'../..')));
const harnessRepository=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const fixturesArgument=argument('--fixtures');
const helperPath=path.resolve(argument('--helper',fileURLToPath(new URL('./workload_cases.py',import.meta.url))));
const nativePython=argument('--native-python',path.join(repository,'.venv/bin/python'));
const nativeTimeout=Number(argument('--native-timeout-ms','120000'));
if(!Number.isSafeInteger(nativeTimeout)||nativeTimeout<1||nativeTimeout>120000)
  throw Error('--native-timeout-ms must be an integer from 1 through 120000');
const site=await verifySite(directory);
const helper=await readFile(helperPath,'utf8');
const playwright=createRequire(path.join(playwrightDirectory,'package.json'))('playwright');
const output=await prepareReports(outputArgument,[site.root,repository,harnessRepository,...(fixturesArgument?[fixturesArgument]:[])]);
const fixtures=fixturesArgument?path.resolve(fixturesArgument):path.join(output,'fixtures');
let fixtureManifestBytes,fixtureManifest;
const nativeLoader = `import importlib.util, sys
from pathlib import Path
helper, repository = sys.argv[1:]
sys.path[:0] = [str(Path(repository) / 'src'), repository]
spec = importlib.util.spec_from_file_location('_native_workload_cases', helper)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.rpc()
`;
let browser,preview,native,nativeClose,nativeFailure,nativeStderr='',nativeClosed=false;
const pending=new Map();let next=0;
const report={kind:'synthetic-fixed-objective-component-scaling',schema_version:1,started_utc:new Date().toISOString(),
  site_manifest_sha256:site.manifest_sha256,
  helper_sha256:sha256(Buffer.from(helper)),source_revision:site.manifest.source_revision,
  tiers:[],events:[],
  native_command:[nativePython,'-I','-c',nativeLoader,helperPath,repository],
  controls:{native_OPENBLAS_NUM_THREADS:'1',native_rpc_timeout_ms:nativeTimeout,warmups_per_tier:2,samples_per_tier:5,
    ordering:'Strictly serial native then browser for each replicate; warmups excluded; each runtime remains warm across all tiers.',
    timing:'Unchanged fit_cone_plane and cone_plane_residual_jacobian each timed in Python; input/fixture loading, parameter conversion, JSON and transport outside those clocks.',
    contention:'Run only after reserved runtime_readiness and cad_probe_cases windows end.'},
  caveats:['Component datasets are additional test-only arrays in an initialized production worker containing the immutable retained nozzle.',
    'This is not a large-source, interactive UI, startup/download, offline or metrology claim.',
    'Boundary Wasm capacity is allocated linear capacity, not live memory or intra-call peak.',
    'Named residual/Jacobian bytes exclude internal solver temporaries and overlap Wasm memory.',
    'Jacobian dtype/shape/payload sizes are checked; Jacobian values are not compared.',
    'Routed worker instrumentation disables HTTP caching; startup is not benchmarked.']};
const workerSuffix=`\n// Test-only component hook; production protocol and workspace remain unchanged.\n
globalThis.__component209Install = source => {
  python.FS.writeFile('/tmp/numeric_component_209.py',new TextEncoder().encode(source));
  python.runPython("import importlib.util; _test_spec209=importlib.util.spec_from_file_location('_test_component209','/tmp/numeric_component_209.py'); _test_component209=importlib.util.module_from_spec(_test_spec209); _test_spec209.loader.exec_module(_test_component209)");
  return JSON.parse(python.runPython('json.dumps(_test_component209.metadata())'));
};
globalThis.__component209Load = ({base64,tier,hash}) => {
  const filename='/tmp/numeric_fixture_'+tier+'.npz';
  python.FS.writeFile(filename,Uint8Array.from(atob(base64),c=>c.charCodeAt(0)));
  python.globals.set('_test_tier209',tier);python.globals.set('_test_hash209',hash);python.globals.set('_test_path209',filename);
  return JSON.parse(python.runPython('json.dumps(_test_component209.load_fixture(_test_path209,_test_tier209,_test_hash209))'));
};
globalThis.__component209Capacity = () => {
  const proxy=python.runPython('browser_workspace.display_positions');let view;
  try {view=proxy.getBuffer('u8');return view.data.buffer.byteLength;}
  finally {view?.release();proxy.destroy();}
};
globalThis.__component209Measure = tier => {
  const before=globalThis.__component209Capacity();python.globals.set('_test_tier209',tier);
  const result=JSON.parse(python.runPython('json.dumps(_test_component209.measure(_test_tier209),allow_nan=False)'));
  return {...result,linear_capacity_bytes_before:before,linear_capacity_bytes_after:globalThis.__component209Capacity()};
};\n`;
report.worker_hook_sha256=sha256(Buffer.from(workerSuffix));
function failNative(error) {
  nativeFailure??=error;
  noteFailure(nativeFailure);
  for(const request of pending.values())request.reject(nativeFailure);
  pending.clear();
}
function noteFailure(error) {report.failure??=String(error);process.exitCode=1;}
function cleanupError(component,error) {
  (report.cleanup_errors??=[]).push({component,error:String(error)});noteFailure(error);
}
function writeNative(message) {
  try {native.stdin.write(message,error=>{if(error)failNative(error);});}
  catch(error) {failNative(error);}
}
function nativeCall(operation,values={}) {
  if(nativeFailure)return Promise.reject(nativeFailure);
  if(nativeClosed)return Promise.reject(Error('Native RPC process is closed'));
  const id=++next;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>failNative(Error(`Native operation observation exceeded ${nativeTimeout} milliseconds`)),nativeTimeout);
    pending.set(id,{resolve:result=>{clearTimeout(timer);resolve(result);},reject:e=>{clearTimeout(timer);reject(e);}});
    writeNative(JSON.stringify({id,operation,...values})+'\n');
  });
}
async function waitNativeClose(milliseconds) {
  if(nativeClosed)return true;
  let timer;
  try {return await Promise.race([nativeClose.then(()=>true),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),milliseconds);})]);}
  finally {clearTimeout(timer);}
}
function signalNative(signal) {
  try {(report.native_cleanup??=[]).push({signal,sent:native.kill(signal)});}
  catch(error) {(report.native_cleanup??=[]).push({signal,error:String(error)});}
}
async function closeNative() {
  if(!native||nativeClosed)return;
  if(!nativeFailure&&!native.stdin.destroyed&&!native.stdin.writableEnded) {
    writeNative(JSON.stringify({id:++next,operation:'close'})+'\n');
    try {native.stdin.end();}catch(error){failNative(error);}
    if(await waitNativeClose(5000))return;
  }
  signalNative('SIGTERM');
  if(await waitNativeClose(3000))return;
  signalNative('SIGKILL');
  if(!await waitNativeClose(5000))throw Error('Native process did not close after bounded TERM/KILL cleanup');
}
function compare(actual,expected) {
  let fitNumbers=0,fitMaxDifference=0;
  const entireFit=(value,reference,label='fit')=>{
    if(typeof reference==='number') {
      const difference=Math.abs(value-reference);
      assert.ok(typeof value==='number'&&Number.isFinite(value)&&difference<=1e-10+1e-10*Math.abs(reference),label);
      fitNumbers++;fitMaxDifference=Math.max(fitMaxDifference,difference);
    } else if(Array.isArray(reference)) {
      assert.ok(Array.isArray(value),label);assert.equal(value.length,reference.length,label);
      reference.forEach((item,index)=>entireFit(value[index],item,`${label}[${index}]`));
    } else if(reference!==null&&typeof reference==='object') {
      assert.ok(value!==null&&typeof value==='object'&&!Array.isArray(value),label);
      assert.deepEqual(Object.keys(value).sort(),Object.keys(reference).sort(),label);
      for(const[key,item]of Object.entries(reference))entireFit(value[key],item,`${label}.${key}`);
    } else assert.deepEqual(value,reference,label);
  };
  entireFit(actual.fit,expected.fit);
  const numbers=(a,b,label)=>{
    assert.equal(a.length,b.length,label+' row count');let max=0;
    for(let i=0;i<a.length;i++) {const delta=Math.abs(a[i]-b[i]);
      assert.ok(Number.isFinite(a[i])&&delta<=1e-10+1e-10*Math.abs(b[i]),label+'['+i+']');max=Math.max(max,delta);}
    return max;
  };
  for(const key of ['termination','rank','iterations','evaluations'])assert.equal(actual.fit.solver[key],expected.fit.solver[key],key);
  assert.deepEqual(actual.explicit_output_arrays,expected.explicit_output_arrays);
  return {atol:1e-10,rtol:1e-10,entire_fit_numbers_compared:fitNumbers,entire_fit_max_abs_difference:fitMaxDifference,jacobian_values_compared:false,parameter_max_abs_difference:numbers(actual.fit.parameters,expected.fit.parameters,'parameters'),
    residual_max_abs_difference:numbers(actual.residuals,expected.residuals,'residuals'),
    weighted_rms_abs_difference:numbers([actual.fit.weighted_rms],[expected.fit.weighted_rms],'weighted_rms'),
    solver:{termination:actual.fit.solver.termination,rank:actual.fit.solver.rank,iterations:actual.fit.solver.iterations,evaluations:actual.fit.solver.evaluations}};
}
function statistics(samples,key) {
  const values=samples.map(s=>s[key]).sort((a,b)=>a-b);
  return {count:values.length,min:values[0],median:values[Math.floor(values.length/2)],max:values.at(-1),mean:values.reduce((n,v)=>n+v,0)/values.length};
}
const watchdog=setTimeout(()=>{
  const error=Error('Component harness exceeded15minutes');noteFailure(error);failNative(error);
  if(native&&!nativeClosed)signalNative('SIGTERM');
  void Promise.resolve().then(()=>browser?.close()).catch(cause=>cleanupError('watchdog_browser',cause));
},900000);
try {
  native=spawn(nativePython,report.native_command.slice(1),{cwd:repository,env:{...process.env,
    OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1',MKL_NUM_THREADS:'1',
    PYTHONDONTWRITEBYTECODE:'1',TMPDIR:output},stdio:['pipe','pipe','pipe']});
  report.native_pid=native.pid;
  nativeClose=new Promise(resolve=>native.once('close',(code,signal)=>{
    nativeClosed=true;report.native_close={code,signal};report.native_close_observed=true;
    if(code!==0&&!nativeFailure)noteFailure(Error(`Native RPC closed: code=${code}, signal=${signal}`));
    resolve();
  }));
  native.stdin.on('error',failNative);native.stdout.on('error',failNative);native.stderr.on('error',failNative);
  native.stderr.on('data',bytes=>{nativeStderr+=bytes.toString();});
  const nativeLines=createInterface({input:native.stdout});
  nativeLines.on('line',line=>{try{const value=JSON.parse(line),request=pending.get(value.id);
    if(!request)return;pending.delete(value.id);value.ok?request.resolve(value.result):request.reject(Error(value.error));}
    catch(e){failNative(e);}});
  native.on('error',failNative);
  native.on('exit',(code,signal)=>{report.native_exit={code,signal};if(pending.size)failNative(Error(`Native RPC exited: code=${code}, signal=${signal}`));});
  report.native_versions=await nativeCall('metadata');
  assert.equal(report.native_versions.native_OPENBLAS_NUM_THREADS,'1');
  assert.equal(report.native_versions.native_OMP_NUM_THREADS,'1');
  assert.equal(report.native_versions.native_MKL_NUM_THREADS,'1');
  assert.equal(report.native_versions.native_PYTHONDONTWRITEBYTECODE,'1');
  assert.equal(report.native_versions.native_TMPDIR,output);
  const currentSource=await nativeCall('source',{site:site.root,repository});
  if (!fixturesArgument) await nativeCall('freeze',{directory:fixtures,site:site.root,repository});
  fixtureManifestBytes=await readFile(path.join(fixtures,'manifest.json'));
  fixtureManifest=JSON.parse(fixtureManifestBytes);
  assert.equal(fixtureManifest.kind,'component-scaling-fixture');
  assert.equal(fixtureManifest.schema_version,1);
  assert.equal(fixtureManifest.source_revision,site.manifest.source_revision);
  assert.equal(fixtureManifest.worker_protocol.version,4);
  assert.deepEqual(fixtureManifest.source_closure,currentSource.source_closure);
  assert.equal(currentSource.source_revision,site.manifest.source_revision);
  assert.deepEqual(fixtureManifest.fixtures.map(f=>f.tier),[496,4096,16384]);
  for (const fixture of fixtureManifest.fixtures) {
    assert.equal(fixture.file,`fixture-${fixture.tier}.npz`);
    assert.match(fixture.fixture_sha256,/^[a-f0-9]{64}$/);
    const entry=await lstat(path.join(fixtures,fixture.file));
    assert.ok(entry.isFile()&&!entry.isSymbolicLink()&&entry.size<=600000);
    assert.equal(entry.size,fixture.file_bytes);
  }
  Object.assign(report,{fixture_manifest_sha256:sha256(fixtureManifestBytes),worker_protocol:fixtureManifest.worker_protocol,
    source_closure:fixtureManifest.source_closure,fixture_policy:fixtureManifest.adaptation,fixtures:fixtureManifest.fixtures});
  preview=await startPreview(site,{prefix:'/numeric209/'});
  browser=await playwright.chromium.launch({headless:true});
  report.browser={name:'chromium',version:browser.version()};
  const context=await browser.newContext({serviceWorkers:'block'});
  report.requests=[];report.external_requests=[];report.api_requests=[];report.page_errors=[];
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    report.requests.push(url.href);
    if(url.origin!==preview.origin){report.external_requests.push(url.href);return route.abort();}
    if(/\/api(?:\/|$)/.test(url.pathname)){report.api_requests.push(url.href);return route.abort();}
    if(!/\/worker\.mjs$/.test(url.pathname))return route.continue();
    const relative=url.pathname.slice('/numeric209/'.length),record=site.records.get(relative);assert.ok(record);
    const baseWorker=await readFile(path.join(site.root,relative));
    assert.equal(baseWorker.length,record.bytes);assert.equal(sha256(baseWorker),record.sha256);
    report.worker_base_asset={path:relative,bytes:baseWorker.length,sha256:record.sha256};
    await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([baseWorker,Buffer.from(workerSuffix)])});
  });
  const page=await context.newPage();page.on('pageerror',e=>report.page_errors.push(String(e)));
  await page.goto(preview.origin+'/numeric209/manifest.json');
  report.environment=await page.evaluate(()=>({isolated:crossOriginIsolated,secure:isSecureContext,hardwareConcurrency:navigator.hardwareConcurrency}));
  const initialized=await page.evaluate(async origin=>{
    const{createWorkerBackend}=await import(origin+'/numeric209/app/worker-backend.js');
    const config=(await import(origin+'/numeric209/app/backend-config.js')).default;
    window.__componentBackend209=createWorkerBackend({...config,runtimeURL:origin+'/numeric209/app/'+config.runtimeURL,
      workerFactory:(url,options)=>{const worker=new Worker(url,options);window.__componentWorker209=worker;return worker;}});
    return await window.__componentBackend209.initialize();
  },preview.origin);
  report.production_worker_ready={versions:initialized.versions,source_revision:initialized.source_revision,source_identity:initialized.source_identity};
  assert.equal(initialized.source_revision,site.manifest.source_revision);
  assert.equal(page.workers().length,1);const worker=page.workers()[0];
  report.browser_versions=await worker.evaluate(source=>globalThis.__component209Install(source),helper);
  report.linear_capacity_after_runtime_ready=await worker.evaluate(()=>globalThis.__component209Capacity());
  report.fixture_loads=[];
  for(const fixture of fixtureManifest.fixtures) {
    const data=await readFile(path.join(fixtures,fixture.file));assert.equal(sha256(data),fixture.fixture_sha256);
    const nativeLoad=await nativeCall('load',{path:path.join(fixtures,fixture.file),tier:fixture.tier,sha256:fixture.fixture_sha256});
    const browserLoad=await worker.evaluate(value=>globalThis.__component209Load(value),{base64:data.toString('base64'),tier:fixture.tier,hash:fixture.fixture_sha256});
    assert.deepEqual(browserLoad,nativeLoad);report.fixture_loads.push(nativeLoad);
  }
  report.linear_capacity_after_all_fixture_loads=await worker.evaluate(()=>globalThis.__component209Capacity());
  for(const fixture of fixtureManifest.fixtures) {
    const tier={tier:fixture.tier,counts:fixture.counts,warmups:[],samples:[]};report.tiers.push(tier);
    for(let index=0;index<7;index++) {
      const phase=index<2?'warmup':'sample',replicate=index<2?index+1:index-1;
      report.events.push({runtime:'native',tier:fixture.tier,phase,replicate,at_utc:new Date().toISOString()});
      const nativeResult=await nativeCall('measure',{tier:fixture.tier});
      report.events.push({runtime:'browser',tier:fixture.tier,phase,replicate,at_utc:new Date().toISOString()});
      const browserResult=await worker.evaluate(value=>globalThis.__component209Measure(value),fixture.tier);
      const comparison=compare(browserResult,nativeResult);
      const record={replicate,native:nativeResult,browser:browserResult,comparison};
      (phase==='warmup'?tier.warmups:tier.samples).push(record);
    }
    tier.statistics={native:{},browser:{}};
    for(const runtime of ['native','browser'])for(const key of ['fit_seconds','residual_jacobian_seconds','core_seconds'])
      tier.statistics[runtime][key]=statistics(tier.samples.map(s=>s[runtime]),key);
    tier.median_core_slowdown=tier.statistics.browser.core_seconds.median/tier.statistics.native.core_seconds.median;
    tier.parameter_max_abs_difference=Math.max(...tier.samples.map(s=>s.comparison.parameter_max_abs_difference));
    tier.residual_max_abs_difference=Math.max(...tier.samples.map(s=>s.comparison.residual_max_abs_difference));
  }
  const immutable=await verifySite(directory);assert.equal(immutable.manifest_sha256,site.manifest_sha256);
  assert.deepEqual(report.external_requests,[]);assert.deepEqual(report.api_requests,[]);assert.deepEqual(report.page_errors,[]);
  report.immutable_site_verified_after=true;report.completed_utc=new Date().toISOString();
} catch(error) {noteFailure(error);}
finally {
  clearTimeout(watchdog);
  const cleanupNames=['native','browser','preview'];
  const cleanup=await Promise.allSettled([Promise.resolve().then(closeNative),Promise.resolve().then(()=>browser?.close()),Promise.resolve().then(()=>preview?.close())]);
  cleanup.forEach((result,index)=>{if(result.status==='rejected')cleanupError(cleanupNames[index],result.reason);});
  report.native_stderr=nativeStderr;
  const saved=await Promise.allSettled([writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n'),writeFile(path.join(output,'worker-hook.mjs'),workerSuffix)]);
  saved.forEach((result,index)=>{if(result.status==='rejected'){cleanupError(['report_write','hook_write'][index],result.reason);console.error(String(result.reason));}});
  if(saved.some(result=>result.status==='rejected'))await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n').catch(error=>console.error(String(error)));
  console.log(JSON.stringify({failure:report.failure,report:path.join(output,'report.json'),tiers:report.tiers.map(t=>({tier:t.tier,statistics:t.statistics,median_core_slowdown:t.median_core_slowdown,parameter_max_abs_difference:t.parameter_max_abs_difference,residual_max_abs_difference:t.residual_max_abs_difference,solver:t.samples[0]?.comparison.solver}))},null,2));
}
