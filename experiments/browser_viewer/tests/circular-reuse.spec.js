import {test as base,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {circularReuseUiObserverSuffix,circularUiFixture,exerciseCircularReuseUi} from '../../browser_static/circular-reuse-ui.mjs';

const repository=fileURLToPath(new URL('../../..',import.meta.url)),python=process.env.SCANSOR_TEST_PYTHON||path.join(repository,'.venv/bin/python');
const test=base.extend({sourceOrigin:[async({},use)=>{
  const child=spawn(python,['-m','experiments.nozzle_browser','--empty','--port','0'],{cwd:repository,
    env:{...process.env,PYTHONPATH:[repository,path.join(repository,'src')].join(path.delimiter),OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1'},stdio:['ignore','pipe','pipe']});
  let log='';const collect=data=>{log=(log+data.toString()).slice(-16000);};child.stderr.on('data',collect);
  try{
    const origin=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Circular native server startup: '+log)),30000);
      child.once('error',error=>{clearTimeout(timer);reject(error);});
      child.once('exit',code=>{clearTimeout(timer);reject(Error(`Circular native server exited ${code}: ${log}`));});
      child.stdout.on('data',data=>{collect(data);const match=log.match(/http:\/\/127\.0\.0\.1:\d+\//);if(match){clearTimeout(timer);resolve(match[0]);}});
    });await use(origin);
  }finally{if(child.exitCode===null&&child.signalCode===null){const closed=once(child,'close'),timer=setTimeout(()=>child.kill('SIGKILL'),5000);
    child.kill('SIGINT');try{await closed;}finally{clearTimeout(timer);}}}
},{scope:'worker'}]});

test('circular feature reuse reviews accepted support and refits real hole departures through common UI',async({page,sourceOrigin},testInfo)=>{
  test.setTimeout(240000);page.setDefaultTimeout(30000);
  const records=[],errors=[];page.on('pageerror',error=>errors.push(String(error)));page.on('dialog',dialog=>dialog.accept());
  page.on('response',async response=>{
    const match=response.url().match(/\/api\/graph\/feature-reuse\/(preview|apply)$/);if(!match||!response.ok())return;
    records.push({operation:match[1]==='preview'?'previewFeatureReuse':'applyFeatureReuse',payload:response.request().postDataJSON(),result:await response.json()});
  });
  await page.route('**/app.js',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text())+circularReuseUiObserverSuffix});});
  let result;
  try{await page.goto(sourceOrigin);result=await exerciseCircularReuseUi({page,fixture:circularUiFixture(python),records:async()=>records,
    holdPreview:async()=>{
      let arrived,release,finished;const started=new Promise(resolve=>{arrived=resolve;}),gate=new Promise(resolve=>{release=resolve;}),done=new Promise(resolve=>{finished=resolve;});
      const pattern='**/api/graph/feature-reuse/preview',handler=async route=>{
        try{const response=await route.fetch();arrived();await gate;await route.fulfill({response}).catch(()=>{});}finally{finished();}
      };
      await page.route(pattern,handler);return{arrived:()=>started,release:async()=>{release();await done;await page.unroute(pattern,handler);}};
    }});
    expect(result.complete).toBe(true);expect(errors).toEqual([]);
  }catch(error){result=error.circularReuseReport;throw error;}
  finally{if(result){const filename=testInfo.outputPath('circular-reuse-evidence.json');await writeFile(filename,JSON.stringify(result,null,2));
    await testInfo.attach('circular-reuse-evidence',{path:filename,contentType:'application/json'});}}
});
