import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, readdir, realpath } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const argument = (name, fallback) => {
  const index = args.indexOf(name);
  if (index < 0) return fallback;
  if (!args[index+1] || args[index+1].startsWith('--')) throw new Error(`Missing ${name}`);
  return args[index+1];
};
const directory = argument('--directory');
if (!directory) throw new Error('Usage: node run.mjs --directory <built CAD artifact> --playwright-directory <existing browser_viewer> --output <report directory> [--isolation both|isolated|plain] [--browser chromium|firefox|webkit]');
const artifact = path.resolve(directory);
const outputArgument = argument('--output');
if (!outputArgument) throw new Error('Provide --output with a fresh temporary report directory outside the repository');
async function physicalPath(filename) {
  try { return await realpath(filename); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const parent = path.dirname(filename);
    if (parent === filename) throw error;
    return path.join(await physicalPath(parent), path.basename(filename));
  }
}
const output = await physicalPath(path.resolve(outputArgument));
const root = await realpath(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..'));
if (output === root || output.startsWith(root + path.sep)) throw new Error('Report output must be outside the repository');
const probe = path.dirname(fileURLToPath(import.meta.url));
const playwrightDirectory = path.resolve(argument('--playwright-directory', 'experiments/browser_viewer'));
const playwright = createRequire(path.join(playwrightDirectory, 'package.json'))('playwright');
const browserName = argument('--browser', 'chromium');
if (!['chromium','firefox','webkit'].includes(browserName)) throw new Error(`Unknown browser ${browserName}`);
const isolation = argument('--isolation', 'both');
if (!['both','isolated','plain'].includes(isolation)) throw new Error(`Unknown isolation ${isolation}`);
await mkdir(output, {recursive:true});
const actualOutput = await realpath(output);
if (actualOutput === root || actualOutput.startsWith(root + path.sep)) throw new Error('Report output must be outside the repository');
if ((await readdir(output)).length) throw new Error('Report output directory must be empty');
const hash = data => createHash('sha256').update(data).digest('hex');
const manifestBytes = await readFile(path.join(artifact, 'manifest.json'));
const manifest = JSON.parse(manifestBytes);
if (manifest.schema_version !== 1 || !manifest.cad) throw new Error('Build the artifact with --cad');
const safe = relative => typeof relative === 'string' && !relative.startsWith('/') && !/[\\?#:]/.test(relative) && !relative.split('/').some(part=>['.','..'].includes(part));
for (const record of manifest.assets) {
  if (!safe(record.path)) throw new Error(`Invalid asset path ${record.path}`);
  const bytes = await readFile(path.join(artifact, record.path));
  if (bytes.length !== record.bytes || hash(bytes) !== record.sha256) throw new Error(`Corrupt artifact ${record.path}`);
}
const probeAssets = ['index.html','worker.mjs','probe.py','run.mjs'];
const probeRecords = await Promise.all(probeAssets.map(async filename=>{
  const bytes=await readFile(path.join(probe,filename));return {path:filename,bytes:bytes.length,sha256:hash(bytes)};
}));
const probeRecord = probeRecords.find(record=>record.path==='probe.py');
const prefix='/cancel-investigation/';
const artifactAllowed=new Set(['manifest.json',...manifest.assets.map(record=>record.path)]);
const probeAllowed=new Set(probeAssets);
const requests=[];
let isolated=true;
const types={'.mjs':'text/javascript','.html':'text/html','.json':'application/json','.wasm':'application/wasm','.py':'text/plain'};
const server=http.createServer(async(request,response)=>{
  try {
    const pathname=decodeURIComponent(new URL(request.url,'http://local.invalid').pathname);
    let base,relative;
    if(pathname.startsWith(prefix+'artifact/')){base=artifact;relative=pathname.slice((prefix+'artifact/').length);if(!artifactAllowed.has(relative)){response.writeHead(404).end();return;}}
    else if(pathname.startsWith(prefix+'cancel/')){base=probe;relative=pathname.slice((prefix+'cancel/').length);if(!probeAllowed.has(relative)){response.writeHead(404).end();return;}}
    else {response.writeHead(404).end();return;}
    const bytes=await readFile(path.join(base,relative));
    requests.push({isolation:isolated,path:pathname,bytes:bytes.length});
    const headers={'Content-Type':types[path.extname(relative)]||'application/octet-stream','Cache-Control':'public, max-age=3600'};
    if(isolated){headers['Cross-Origin-Opener-Policy']='same-origin';headers['Cross-Origin-Embedder-Policy']='require-corp';}
    response.writeHead(200,headers);response.end(bytes);
  }catch{response.writeHead(500).end();}
});
let browser;
const report={started_utc:new Date().toISOString(),manifest_sha256:hash(manifestBytes),source_revision:manifest.source_revision,source_worktree_dirty:manifest.source_worktree_dirty,fixture_sha256:manifest.assets.find(record=>record.path==='fixture.npz').sha256,versions:manifest.versions,probe_files:probeRecords,policy:'Investigative polite Cancel and separately explicit Force; no automatic escalation. SIGINT experiments are not OCCT UserBreak.',runs:[]};
try {
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const origin=`http://127.0.0.1:${server.address().port}`;
  browser=await playwright[browserName].launch({headless:true});
  report.browser={name:browserName,version:browser.version()};
  const modes=isolation==='both'?[true,false]:[isolation==='isolated'];
  for(const mode of modes){
    isolated=mode;
    const page=await browser.newPage();
    const runReport={isolated:mode,events:[],unexpected_requests:[],page_errors:[]};report.runs.push(runReport);
    try {
      await page.route('**/*',async route=>{if(!route.request().url().startsWith(origin+'/')){runReport.unexpected_requests.push(route.request().url());await route.abort();}else await route.continue();});
      page.on('pageerror',error=>runReport.page_errors.push(String(error)));
      await page.goto(origin+prefix+'cancel/index.html');
      runReport.environment=await page.evaluate(()=>({user_agent:navigator.userAgent,isolated:crossOriginIsolated,sab:typeof SharedArrayBuffer}));
      const wait=(predicate,arg,timeout=60000)=>page.waitForFunction(predicate,arg,{timeout});
      const create=async owner=>{
        await page.evaluate(({owner,record,interrupt})=>create(owner,record,interrupt),{owner,record:probeRecord,interrupt:mode});
        await wait(owner=>events.some(event=>event.owner===owner&&[crossOriginIsolated?'configured':'ready','fatal'].includes(event.type)),owner,180000);
        const fatal=await page.evaluate(owner=>events.find(event=>event.owner===owner&&event.type==='fatal'),owner);
        if(fatal)throw new Error(JSON.stringify(fatal));
      };
      await create('a');
      const compute=async(id,job,count,mechanism=null,delay=50)=>{
        await page.evaluate(data=>action('a',{type:'run',...data}),{id,job,count});
        await wait(id=>events.some(event=>event.id===id&&event.type==='started'),id);
        if(mechanism){
          if(job==='cad_native')await wait(()=>events.some(event=>event.type==='native-enter'&&event.received_ms>events.findLast(event=>event.type==='started').received_ms));
          await page.waitForTimeout(delay);
          await page.evaluate(({id,mechanism})=>{action('a',{type:'ping',id:id+'-ping'});if(mechanism==='signal')signal('a',id);else polite('a',id);},{id,mechanism});
        }
        await wait(id=>events.some(event=>event.id===id&&event.type==='result'),id);
        const result=await page.evaluate(id=>events.find(event=>event.id===id&&event.type==='result'),id);
        console.log(mode?'isolated':'plain',id,result.status,result.result?.seconds??'');
        if(!mechanism&&result.status!=='success')throw new Error(JSON.stringify(result));
      };
      await compute('baseline-small','small',1);
      await compute('baseline-large','large',1);
      await compute('baseline-cad-actual','cad_actual',1);
      await compute('baseline-cad-native','cad_native',1);
      await compute('ordinary-message','small',150,'message',100);
      await compute('cooperative-message','cooperative',150,'message',100);
      if(mode){
        await compute('interrupt-large','large',1,'signal');
        await compute('post-numeric-interrupt-fit','small',1);
        await compute('interrupt-cad-actual','cad_actual',1,'signal');
        await compute('post-cad-interrupt-fit','small',1);
        await compute('interrupt-cad-native','cad_native',1,'signal');
        await compute('post-native-interrupt-fit','small',1);
        await compute('post-native-interrupt-cad','cad_actual',1);
      }
      await page.evaluate(()=>action('a',{type:'run',id:'force-long',job:'large',count:100}));
      await wait(()=>events.some(event=>event.id==='force-long'&&event.type==='started'));
      await page.waitForTimeout(50);
      await page.evaluate(()=>{action('a',{type:'ping',id:'force-pending-ping'});force('a');});
      await page.waitForTimeout(500);
      runReport.force_no_reply_after_500_ms=await page.evaluate(()=>({result:events.find(event=>event.id==='force-long'&&event.type==='result')||null,pong:events.find(event=>event.id==='force-pending-ping'&&event.type==='pong')||null}));
      await create('b');
      await page.evaluate(()=>action('b',{type:'run',id:'recreated-fit',job:'small',count:1}));
      await wait(()=>events.some(event=>event.id==='recreated-fit'&&event.type==='result'));
      runReport.transfer=await page.evaluate(async()=>{
        const array=new Float64Array(8192);for(let i=0;i<array.length;i++)array[i]=i/4;
        const source=new File([array.buffer],'retained-source.bin');
        const url=URL.createObjectURL(new Blob(['self.onmessage=({data})=>postMessage({bytes:data.byteLength,first:new Float64Array(data)[0],last:new Float64Array(data).at(-1)})'],{type:'text/javascript'}));
        let worker,timer;
        try {
          worker=new Worker(url);
          const received=new Promise((resolve,reject)=>{
            timer=setTimeout(()=>reject(new Error('Transfer probe timed out')),60000);
            worker.onmessage=({data})=>resolve(data);
            worker.onerror=event=>reject(new Error(event.message||'Transfer worker failed'));
            worker.onmessageerror=()=>reject(new Error('Transfer response could not be deserialized'));
          });
          worker.postMessage(array.buffer,[array.buffer]);const sender_bytes=array.byteLength;const worker_received=await received;
          const recreated=new Float64Array(await source.arrayBuffer());
          return {sender_bytes,worker_received,retained_file_bytes:source.size,reacquired_bytes:recreated.byteLength,reacquired_first:recreated[0],reacquired_last:recreated.at(-1)};
        } finally { clearTimeout(timer);worker?.terminate();URL.revokeObjectURL(url); }
      });
    }catch(error){runReport.failure=String(error);throw error;}
    finally{
      runReport.events=await page.evaluate(()=>events).catch(()=>[]);
      runReport.heartbeat=await page.evaluate(()=>({ticks:tickGaps.length,max_gap_ms:Math.max(...tickGaps)})).catch(()=>null);
      await page.close();
      await writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2)+'\n');
    }
    if(runReport.unexpected_requests.length||runReport.page_errors.length)throw new Error(JSON.stringify(runReport));
  }
  report.finished_utc=new Date().toISOString();
}finally{
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
  report.requests=requests;
  await writeFile(path.join(output,'results.json'),JSON.stringify(report,null,2)+'\n');
}
console.log(JSON.stringify({browser:report.browser,runs:report.runs.map(run=>({isolated:run.isolated,heartbeat:run.heartbeat})),report:path.join(output,'results.json')},null,2));
