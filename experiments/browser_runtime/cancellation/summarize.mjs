import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';

const args=process.argv.slice(2);const index=args.indexOf('--directory');
if(index<0||!args[index+1])throw new Error('Usage: node summarize.mjs --directory <report directory>');
const directory=path.resolve(args[index+1]);
const raw=JSON.parse(await readFile(path.join(directory,'results.json'),'utf8'));
const summary={complete:!!raw.finished_utc&&Array.isArray(raw.runs)&&raw.runs.length>0,started_utc:raw.started_utc,finished_utc:raw.finished_utc,browser:raw.browser,versions:raw.versions,source_revision:raw.source_revision,source_worktree_dirty:raw.source_worktree_dirty,fixture_sha256:raw.fixture_sha256,manifest_sha256:raw.manifest_sha256,runs:[]};
for(const run of raw.runs){
 const events=run.events;const result=id=>events.find(event=>event.type==='result'&&event.id===id);
 const observed={environment:run.environment,heartbeat:run.heartbeat,baselines:{},cancellation:{},restart:{},post_interruption:[],transfer:run.transfer,failure:run.failure??null,unexpected_requests:run.unexpected_requests,page_errors:run.page_errors};
 for(const event of events){
  if(event.type!=='result')continue;
  if(event.id.startsWith('baseline-'))observed.baselines[event.id]=event.result;
  if(event.id.startsWith('interrupt-')){
   const signal=events.find(item=>item.type==='signal-sent'&&item.id===event.id);
   observed.cancellation[event.id]={latency_ms:event.received_ms-signal.received_ms,status:event.status,error:event.error??null,state:event.state,publication_allowed:event.publication_allowed};
  }
  if(event.id.startsWith('post-')||event.id==='recreated-fit')observed.post_interruption.push({id:event.id,status:event.status,result:event.result});
 }
 for(const id of ['ordinary-message','cooperative-message']){
  const outcome=result(id);if(!outcome)continue;
  const sent=events.find(event=>event.type==='sent'&&event.data.id===id+'-cancel');
  const received=events.find(event=>event.type==='cancel-received'&&event.id===id+'-cancel');
  observed.cancellation[id]={latency_ms:outcome.received_ms-sent.received_ms,message_delay_ms:received.received_ms-sent.received_ms,status:outcome.status,completed:outcome.completed??null,publication_allowed:outcome.publication_allowed};
 }
 observed.restart={first_ready:events.find(event=>event.type==='ready'&&event.owner==='a'),recreated_ready:events.find(event=>event.type==='ready'&&event.owner==='b'),force:events.find(event=>event.type==='forced'),no_reply_after_500_ms:run.force_no_reply_after_500_ms};
 const first=observed.restart.first_ready?.state;
 const recreated=observed.restart.recreated_ready?.state;
 observed.checks={
  no_failure:!run.failure,
  no_unexpected_network:run.unexpected_requests.length===0,
  no_page_errors:run.page_errors.length===0,
  baselines_succeeded:['baseline-small','baseline-large','baseline-cad-actual','baseline-cad-native'].every(id=>result(id)?.status==='success'),
  ordinary_cancel_did_not_interrupt:observed.cancellation['ordinary-message']?.status==='success',
  ordinary_cancel_fenced_publication:observed.cancellation['ordinary-message']?.publication_allowed===false,
  cooperative_cancelled:observed.cancellation['cooperative-message']?.status==='cancelled',
  cooperative_cancel_fenced_publication:observed.cancellation['cooperative-message']?.publication_allowed===false,
  expected_sigint_errors:run.isolated?['interrupt-large','interrupt-cad-actual','interrupt-cad-native'].every(id=>observed.cancellation[id]?.status==='error'&&observed.cancellation[id]?.error?.includes('KeyboardInterrupt')&&observed.cancellation[id]?.publication_allowed===false):null,
  subsequent_work_succeeded:(run.isolated?['post-numeric-interrupt-fit','post-cad-interrupt-fit','post-native-interrupt-fit','post-native-interrupt-cad','recreated-fit']:['recreated-fit']).every(id=>result(id)?.status==='success'),
  explicit_force_observed:!!observed.restart.force,
  no_automatic_force_reply:run.force_no_reply_after_500_ms?.result===null&&run.force_no_reply_after_500_ms?.pong===null,
  private_counter_reset:first?.completed===0&&recreated?.completed===0,
  private_file_reset:recreated?.virtual_file_contents==='initialized',
  source_recipe_identity_preserved:!!first&&!!recreated&&['source_sha256','model_sha256','committed_recipe_sha256'].every(key=>first[key]===recreated[key]),
  transfer_detached_sender:run.transfer?.sender_bytes===0,
  worker_received_transfer:run.transfer?.worker_received?.bytes===65536&&run.transfer?.worker_received?.first===0&&run.transfer?.worker_received?.last===2047.75,
  retained_file_reacquired:run.transfer?.retained_file_bytes===65536&&run.transfer?.reacquired_bytes===65536&&run.transfer?.reacquired_first===0&&run.transfer?.reacquired_last===2047.75,
 };
 summary.runs.push(observed);
}
await writeFile(path.join(directory,'summary.json'),JSON.stringify(summary,null,2)+'\n');
console.log(JSON.stringify(summary,null,2));
const failed=summary.runs.flatMap((run,index)=>Object.entries(run.checks).filter(([,value])=>value===false).map(([name])=>({run:index,name})));
if(failed.length)throw new Error(JSON.stringify({failed}));
if(!summary.complete)throw new Error('Incomplete cancellation report; no successful complete run recorded');
