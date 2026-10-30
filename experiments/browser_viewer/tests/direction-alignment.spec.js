import {test as base, expect} from '@playwright/test';
import {execFileSync, spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {directionAlignmentObserver, exerciseDirectionAlignmentUi} from '../../browser_static/direction-alignment-ui.mjs';

const repository = fileURLToPath(new URL('../../..', import.meta.url));
const python = process.env.SCANSOR_TEST_PYTHON || path.join(repository, '.venv/bin/python');
const environment = {...process.env, PYTHONPATH:[repository,path.join(repository,'src')].join(path.delimiter),
  OPENBLAS_NUM_THREADS:'1', OMP_NUM_THREADS:'1'};
const test = base.extend({
  directionFixture:[async ({},use) => {
    const fixture = JSON.parse(execFileSync(python,['-m','tests.direction_ui_fixture'],{cwd:repository,env:environment}));
    const child = spawn(python,['-m','experiments.nozzle_browser','--empty','--port','0'],
      {cwd:repository,env:environment,stdio:['ignore','pipe','pipe']});
    let log=''; const collect=bytes=>{log=(log+bytes.toString()).slice(-16000);};
    child.stderr.on('data',collect);
    try {
      const origin=await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Direction UI native fixture did not start: '+log)),30000);
        child.once('error',error=>{clearTimeout(timer);reject(error);});
        child.once('exit',code=>{clearTimeout(timer);reject(Error(`Direction fixture exited ${code}: ${log}`));});
        child.stdout.on('data',bytes=>{collect(bytes);const found=log.match(/http:\/\/127\.0\.0\.1:\d+\//);
          if(found){clearTimeout(timer);resolve(found[0]);}});
      });
      await use({origin,fixture});
    } finally {
      if(child.exitCode===null&&child.signalCode===null){
        const closed=once(child,'close'),timer=setTimeout(()=>child.kill('SIGKILL'),5000);
        child.kill('SIGINT');try{await closed;}finally{clearTimeout(timer);}
      }
    }
  },{scope:'worker'}],
});

test('unsigned direction controls preserve independent geometry, selected Joint intent and project reopen',async({page,directionFixture},testInfo)=>{
  test.setTimeout(360000); page.setDefaultTimeout(30000);
  const errors=[];page.on('pageerror',error=>errors.push(String(error)));page.on('dialog',dialog=>dialog.accept());
  await page.route('**/app.js',async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:await response.text()+directionAlignmentObserver});
  });
  await page.goto(directionFixture.origin);
  const report=await exerciseDirectionAlignmentUi({page,expect,fixture:directionFixture.fixture});
  expect(report.complete).toBe(true);expect(errors).toEqual([]);
  const reportPath=testInfo.outputPath('direction-alignment-ui.json');
  await writeFile(reportPath,JSON.stringify(report,null,2)+'\n');
  await testInfo.attach('direction-alignment-ui-evidence',{path:reportPath,contentType:'application/json'});
});
