import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {prepareReports,sha256,startPreview,verifySite} from './server.mjs';
import {directionAlignmentObserver,exerciseDirectionAlignmentUi} from './direction-alignment-ui.mjs';

const options={};
for(let i=2;i<process.argv.length;i+=2){
  const key=process.argv[i],value=process.argv[i+1];
  assert(['--directory','--output','--playwright-directory','--native-python'].includes(key)
    &&value&&!value.startsWith('--')&&!(key in options));options[key]=value;
}
assert(options['--directory']&&options['--output']);
const repository=fileURLToPath(new URL('../..',import.meta.url));
const playwrightDirectory=path.resolve(options['--playwright-directory']||path.join(repository,'experiments/browser_viewer'));
const require=createRequire(path.join(playwrightDirectory,'package.json'));
const {chromium}=require('playwright'),{expect}=require('@playwright/test');
const site=await verifySite(options['--directory']);
const output=await prepareReports(options['--output'],[site.root,repository]);
const fixture=JSON.parse(execFileSync(options['--native-python']||path.join(repository,'.venv/bin/python'),
  ['-m','tests.direction_ui_fixture'],{cwd:repository,env:{...process.env,
    PYTHONPATH:[repository,path.join(repository,'src')].join(path.delimiter),OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1'}}));
const report={schema:'scansor-direction-alignment-ui-v1',complete:false,manifest_sha256:site.manifest_sha256,
  runner_sha256:sha256(await readFile(new URL(import.meta.url))),
  harness_sha256:sha256(await readFile(new URL('./direction-alignment-ui.mjs',import.meta.url))),
  fixture_sha256:sha256(await readFile(path.join(repository,'tests/direction_ui_fixture.py'))),
  profiles:[],external_requests:[],api_requests:[],page_errors:[],cleanup_errors:[]};
let browser,preview;
try{
  browser=await chromium.launch({headless:true});report.browser={name:'chromium',version:browser.version()};
  for(const isolation of [false,true]){
    const prefix='/directions/nested/';preview=await startPreview(site,{prefix,isolation});
    const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:1000},acceptDownloads:true});
    const profile={isolation_requested:isolation,complete:false};report.profiles.push(profile);
    try{
      await context.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.origin!==preview.origin){report.external_requests.push(url.href);return route.abort();}
        if(/\/api(?:\/|$)/.test(url.pathname)){report.api_requests.push(url.href);return route.abort();}
        if(!url.pathname.endsWith('/app.js'))return route.continue();
        const relative=url.pathname.slice(prefix.length),record=site.records.get(relative);assert(record);
        const bytes=await readFile(path.join(site.root,relative));
        assert.equal(bytes.length,record.bytes);assert.equal(sha256(bytes),record.sha256);
        await route.fulfill({status:200,contentType:'text/javascript',body:Buffer.concat([bytes,Buffer.from(directionAlignmentObserver)]),
          headers:isolation?{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'}:{}});
      });
      const page=await context.newPage();page.setDefaultTimeout(60000);
      page.on('dialog',dialog=>dialog.accept());page.on('pageerror',error=>report.page_errors.push(String(error)));
      await page.goto(preview.origin+prefix+'app/');
      profile.workflow=await exerciseDirectionAlignmentUi({page,expect:expect.configure({timeout:60000}),fixture});
      profile.isolation_actual={page:await page.evaluate(()=>crossOriginIsolated),worker:await page.workers()[0].evaluate(()=>crossOriginIsolated)};
      assert.deepEqual(profile.isolation_actual,{page:isolation,worker:isolation});profile.complete=true;
    }finally{await context.close();await preview.close();preview=null;}
  }
  assert.deepEqual(report.profiles[0].workflow,report.profiles[1].workflow);
  assert.deepEqual(report.external_requests,[]);assert.deepEqual(report.api_requests,[]);assert.deepEqual(report.page_errors,[]);
  report.complete=true;
}catch(error){report.failure=String(error);throw error;}
finally{
  for(const[label,action]of[['preview',()=>preview?.close()],['browser',()=>browser?.close()]]){
    try{await action();}catch(error){report.cleanup_errors.push({label,error:String(error)});}
  }
  await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
}
