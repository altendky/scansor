import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash,webcrypto} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ORDINARY_STAGES,RESOURCE_SOURCES,validateWorkerResourceReport,assertTrustedInputsUnchanged,semanticJson,assertWorkerInventoryQuiescent} from './worker-source-resource-validation.mjs';
import {installMainObservers,workerSuffix,backendObserverMarker,backendObserverInsertion,instrumentWorkerBackend} from './worker-source-resource-observers.mjs';

const sha='a'.repeat(64);
function fixture(){
  const inputs=RESOURCE_SOURCES.map((name,index)=>({name,sha256:sha,source_bytes:100+index,vertices:20,faces:30}));
  const reference=Object.fromEntries(inputs.map(input=>[input.name,{source_sha256:sha,descriptor:{filename:input.name+'.ply',source_sha256:sha,settings:{scale:1}},
    source_identity:{descriptor:{source_sha256:sha,settings:{scale:1}},reference_sha256:sha},
    observations:Object.fromEntries(['local','normals','original_normals','triangles','weights','xyz'].map(key=>[key,{sha256:sha,
      dtype:key==='original_normals'?'float32':key==='triangles'?'int32':'float64',shape:key==='triangles'?[input.faces,3]:key==='weights'?[input.vertices]:[input.vertices,3]}])),
    display:{positions:{sha256:sha,bytes:input.vertices*12},indices:{sha256:sha,bytes:input.faces*12}}}]));
  const row=(cycle,input,stage,epoch,index=1)=>({cycle,source:input.name,stage,worker_index:index,
    main_heap:{usedSize:100,totalSize:1000,backingStorageSize:0},heartbeat:{ticks:10,max_gap_ms:19,sum_gap_ms:160},
    raf:{count:10,max_gap_ms:20,sum_gap_ms:160,over_16_ms:2,over_50_ms:0},gpu:{contexts:[{logical_live_buffer_bytes:20,logical_high_water_bytes:50,context_lost:false}]},
    worker_heap:{session_id:'session-'+index,url:'http://127.0.0.1/worker.mjs',usedSize:20,totalSize:50,backingStorageSize:100},
    worker:{isolated:false,wasm_capacity_bytes:10000,unique_root_bytes:500,immutable_original_bytes:input.source_bytes,
      source_mode:{kind:'scan'},
      immutable_original_sha256:sha,export_bytes:0,owned_fs_bytes:0,owned_fs_files:[],project_generation:epoch,
      source_sha256:sha,vertices:input.vertices,faces:input.faces,descriptor:reference[input.name].descriptor,
      views:[{name:'source.xyz',logical_bytes:480},{name:'frame.columns',logical_bytes:72}],
      observations:reference[input.name].observations,transfer:{staging_bytes:0,max_staging_bytes:102,largest_chunk_bytes:64,display_copy_bytes:100}},
    ui:{singleton:true,generation:epoch,graph_generation:epoch,source:{source_sha256:sha},vertices:20,triangles:30,
      positions_length:60,indices_length:90,selected_index_count:0,recipe_nodes:1,source_nodes:1,fit_nodes:0,input_files:0,
      inert:false,source_progress_hidden:true,arrays:{views_truncated:false,unique_backing_bytes:300,views:[{name:'mesh.normal',logical_bytes:10},{name:'mesh.color',logical_bytes:10}]},
      limits:{max_source_bytes:1000,chunk_bytes:64},recipe:{nodes:[{operation:'source'}]},display_hashes:{positions:sha,indices:sha}},
    input_proxy:{sequence:1,proxy_ms:1},source_dom_verified:true,import_to_visible_observation_ms:10,file_event:{size:input.source_bytes},
    import_window:{active:false,started_ms:10,end_ms:20,duration_ms:10,heartbeat_ticks:0,heartbeat_max_gap_ms:10,heartbeat_sum_gap_ms:0,raf_ticks:0,raf_max_gap_ms:10,raf_sum_gap_ms:0}});
  const makeEmpty=value=>{for(const key of ['vertices','triangles','positions_length','indices_length','selected_index_count','recipe_nodes','input_files'])value.ui[key]=0;
    value.worker.immutable_original_bytes=0;value.worker.source_sha256=null;value.worker.vertices=0;value.worker.faces=0;
    value.worker.descriptor=null;value.ui.source=null;value.ui.recipe={nodes:[]};
    value.worker.source_mode={kind:'empty'};
    for(const view of value.worker.views)if(view.name.startsWith('source.'))view.logical_bytes=0;
    for(const view of value.ui.arrays.views)view.logical_bytes=0;
    return value;};
  const attachCheckpoint=(value,serial)=>{
    value.file_serial_exposure_released=true;
    value.lifecycle={state:'complete',lifetime:value.worker_index,generation:value.ui?.generation??0};
    value.checkpoint={source_revision:'b'.repeat(40),current_checkpoint:null,source_operation:null};
    if(!value.ui)return;
    const ui=value.ui,w=value.worker,empty=w.source_sha256===null;
    ui.lifecycle=structuredClone(value.lifecycle);
    value.checkpoint={accepted_generation:ui.generation,source_identity:empty?{}:structuredClone(reference[value.source].source_identity),
      source_revision:'b'.repeat(40),closed:false,recovering:false,source_operation:null,
      pending_count:0,pending_operations:[],
      current_checkpoint:{mode:structuredClone(w.source_mode),generation:ui.generation,
        identity:empty?{}:structuredClone(reference[value.source].source_identity),revision:'b'.repeat(40),
        file:{serial:empty?null:serial,logical_bytes:empty?0:w.immutable_original_bytes},
        recipe_nodes:ui.recipe_nodes,source_nodes:ui.recipe.nodes.filter(node=>node.operation==='source').length,
        selection_nodes:ui.recipe.nodes.filter(node=>node.operation==='selection').length,
        fit_nodes:ui.recipe.nodes.filter(node=>node.operation==='fit').length,
        recipe_sha256:createHash('sha256').update(semanticJson(ui.recipe)).digest('hex')}};
    value.settled_after={worker_index:value.worker_index,lifecycle:structuredClone(value.lifecycle),
      checkpoint:structuredClone(value.checkpoint),file_serial_exposure_released:true};
  };
  const modes=[false,true].map(isolation=>{
    const mode={complete:true,isolation_requested:isolation,isolation_actual:isolation,ordinary_worker_index:1,external_requests:[],api_requests:[],page_errors:[],
      cleanup:{context_closed:true,preview_closed:true},stages:[],transport:{workers:2,terminated:[1],prepared:[]},
      worker_events:[{event:'created',index:1},{event:'closed',index:1},{event:'created',index:2},{event:'closed',index:2}]};
    mode.backend_observer_injections=[{path:'app/worker-backend.js',original_sha256:sha,observed_sha256:'b'.repeat(64),marker_matches:1,insertion_sha256:'c'.repeat(64)}];
    let epoch=1,serial=0;
    for(let cycle=1;cycle<=2;cycle++)for(const input of inputs){
      for(const stage of ORDINARY_STAGES){
        const value=row(cycle,input,stage,stage==='empty-baseline'?epoch:stage==='reset'?epoch+2:epoch+1);
        if(['empty-baseline','reset'].includes(stage))makeEmpty(value);
        if(stage==='file-selected'){value.worker=null;value.worker_heap=null;value.ui=null;value.unavailable_reason='source RPC active';}
        if(stage==='selection-overlay'){value.ui.selection=[{count:5,min_id:1,max_id:5}];value.ui.selected_index_count=5;value.ui.selected_visible=true;
          value.ui.recipe.nodes.push({operation:'selection'});value.ui.recipe_nodes=2;}
        attachCheckpoint(value,serial+1);
        mode.stages.push(value);
      }
      epoch+=2;mode.transport.prepared.push({worker:1,file_serial:++serial,file_bytes:input.source_bytes,recovery:false});
    }
    const grid=inputs[2];epoch++;
    for(const stage of ['force-before','force-after','reopen-empty','reopened','final-reset']){
      const value=row(0,grid,stage,stage==='reopen-empty'?epoch+1:stage==='reopened'?epoch+2:stage==='final-reset'?epoch+3:epoch,stage==='force-before'?1:2);
      if(['reopen-empty','final-reset'].includes(stage))makeEmpty(value);
      attachCheckpoint(value,stage==='reopened'?8:7);mode.stages.push(value);
    }
    mode.transport.prepared.push({worker:1,file_serial:++serial,file_bytes:grid.source_bytes,recovery:false},
      {worker:2,file_serial:serial,file_bytes:grid.source_bytes,recovery:true},
      {worker:2,file_serial:++serial,file_bytes:grid.source_bytes,recovery:false});
    for(const value of mode.stages)if(value.worker)value.worker.isolated=isolation;
    return mode;
  });
  return{schema_version:1,kind:'scansor-worker-product-ui-resources',complete:true,cycles:2,source_names:RESOURCE_SOURCES,
    source_revision:'b'.repeat(40),source_worktree_dirty:false,
    source_guard:{complete:true,source_files_match:true,rehashed_unchanged:true,artifact_source_revision:'b'.repeat(40),runtime_source_revision:'b'.repeat(40),
      checkout_revision:'c'.repeat(40),runtime_source_worktree_dirty:false,runtime_source_records:1,site_source_records:1,
      source_limits:{max_source_bytes:1000,chunk_bytes:64},files:[{path:'experiments/example.py',sha256:sha},{path:'experiments/browser_viewer/worker-backend.js',sha256:sha}],
      frozen_input_files:[{path:'/tmp/native-source-resource-reference.json',sha256:sha}]},
    artifact_unchanged:true,browser_closed:true,manifest_sha256:sha,reference_sha256:sha,native_reference:reference,
    instrumentation:{backend_observer_insertion_sha256:'c'.repeat(64)},
    reference_value_sha256:createHash('sha256').update(JSON.stringify(reference)).digest('hex'),inputs:{cases:inputs},modes};
}

test('valid repeated actual-UI boundary report permits retained capacity and overlapping counters',()=>{
  const report=fixture(),before=JSON.stringify(report),result=validateWorkerResourceReport(report);
  assert.equal(result.ordinary_cycles,12);assert.equal(JSON.stringify(report),before);
});

for(const [name,mutate]of Object.entries({
  'ordinary worker replacement':r=>{r.modes[0].stages[8].worker_index=2;},
  'ordinary CDP replacement':r=>{r.modes[0].stages[8].worker_heap.session_id='replacement';},
  'missing repeated cycle':r=>{r.modes[1].stages.splice(18,6);},
  'missing worker sample disguised as zero':r=>{r.modes[0].stages[2].worker.wasm_capacity_bytes=0;},
  'missing main heap sample':r=>{delete r.modes[0].stages[2].main_heap.usedSize;},
  'missing current checkpoint sample':r=>{delete r.modes[0].stages[2].checkpoint;},
  'missing current checkpoint owner':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint=null;},
  'wrong current checkpoint File size':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint.file.logical_bytes=0;},
  'wrong current checkpoint File serial':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint.file.serial=100;},
  'stale current checkpoint epoch':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint.generation--;},
  'wrong current checkpoint recipe':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint.recipe_sha256='b'.repeat(64);},
  'wrong current checkpoint settings':r=>{r.modes[0].stages[2].checkpoint.current_checkpoint.identity.descriptor.settings.scale=2;},
  'reset retains File despite empty transport':r=>{r.modes[0].stages[5].checkpoint.current_checkpoint.file={serial:1,logical_bytes:100};},
  'reset retains active File operation':r=>{r.modes[0].stages[5].checkpoint.source_operation={file:{serial:1,logical_bytes:100}};},
  'Force checkpoint loses retained File':r=>{r.modes[0].stages[37].checkpoint.current_checkpoint.file.serial=100;},
  'renamed reopen keeps old checkpoint File':r=>{r.modes[0].stages[39].checkpoint.current_checkpoint.file.serial=7;},
  'checkpoint observer File exposure retained':r=>{r.modes[0].stages[2].file_serial_exposure_released=false;},
  'checkpoint backend observer absent':r=>{r.modes[0].backend_observer_injections=[];},
  'inventory begun during active lifecycle':r=>{r.modes[0].stages[2].lifecycle.state='running';},
  'inventory begun with pending Python request':r=>{r.modes[0].stages[2].checkpoint.pending_count=1;r.modes[0].stages[2].checkpoint.pending_operations=['loadWorkspace'];},
  'source accepted across inventory':r=>{r.modes[0].stages[2].settled_after.checkpoint.accepted_generation++;},
  'source operation started across inventory':r=>{r.modes[0].stages[2].settled_after.checkpoint.source_operation={kind:'scan'};},
  'missing worker backing sample':r=>{delete r.modes[0].stages[2].worker_heap.backingStorageSize;},
  'active import inspected anyway':r=>{r.modes[0].stages[1].worker={};},
  'staging leak':r=>{r.modes[0].stages[2].worker.owned_fs_bytes=1;},
  'chunk budget exceeded':r=>{r.modes[0].stages[2].worker.transfer.largest_chunk_bytes=65;},
  'source setting mutation':r=>{r.modes[0].stages[2].worker.descriptor=structuredClone(r.modes[0].stages[2].worker.descriptor);r.modes[0].stages[2].worker.descriptor.settings.scale=2;},
  'original observations changed':r=>{r.modes[0].stages[2].worker.observations={xyz:{sha256:'b'.repeat(64)}};},
  'original normal evidence omitted':r=>{delete r.native_reference['raw-nozzle'].observations.original_normals;
    r.reference_value_sha256=createHash('sha256').update(JSON.stringify(r.native_reference)).digest('hex');},
  'staging high-water erased':r=>{r.modes[0].stages[2].worker.transfer.max_staging_bytes=0;},
  'display identity changed':r=>{r.modes[0].stages[2].ui.display_hashes.positions='b'.repeat(64);},
  'selection exceeds independent fitting cap':r=>{r.modes[0].stages[4].ui.selection[0].count=25001;},
  'reset leaves live display':r=>{r.modes[0].stages[5].ui.positions_length=3;},
  'reset leaves source array':r=>{r.modes[0].stages[5].worker.views[0].logical_bytes=480;},
  'reset leaves production normal':r=>{r.modes[0].stages[5].ui.arrays.views[0].logical_bytes=10;},
  'missing stage display digest':r=>{delete r.modes[0].stages[3].ui.display_hashes;},
  'missing import heartbeat window':r=>{delete r.modes[0].stages[2].import_window;},
  'native reference relabelled':r=>{r.reference_value_sha256='b'.repeat(64);},
  'source freeze incomplete':r=>{r.source_guard.rehashed_unchanged=false;},
  'dirty artifact source':r=>{r.source_worktree_dirty=true;},
  'mixed runtime and app revisions':r=>{r.source_guard.runtime_source_revision='a'.repeat(40);},
  'source admission changed':r=>{r.modes[0].stages[2].ui.limits={max_source_bytes:2000,chunk_bytes:64};},
  'accepted epoch wrong':r=>{r.modes[0].stages[2].ui.generation++;},
  'input response missing':r=>{r.modes[0].stages[4].input_proxy.proxy_ms=null;},
  'Forces File replaced':r=>{r.modes[0].transport.prepared[7].file_serial=100;},
  'hidden retry':r=>{r.modes[0].transport.prepared.push(structuredClone(r.modes[0].transport.prepared[7]));},
  'Force did not replace CDP worker':r=>{r.modes[0].stages[37].worker_heap.session_id='session-1';},
  'Force epoch changed':r=>{r.modes[0].stages[37].ui.generation++;},
  'reopen recipe changed':r=>{r.modes[0].stages[39].ui.recipe={nodes:[]};},
  'wrong isolation':r=>{r.modes[1].isolation_actual=false;},
  'external request':r=>{r.modes[0].external_requests.push('https://example.invalid/');},
  'API fallback':r=>{r.modes[0].api_requests.push('http://127.0.0.1/api/workspace');},
  'cleanup failure':r=>{r.modes[0].cleanup.preview_closed=false;},
  'artifact changed':r=>{r.artifact_unchanged=false;},
}))test('rejects '+name,()=>{const report=fixture();mutate(report);assert.throws(()=>validateWorkerResourceReport(report));});

test('File observer records scalar identities without retaining File history',()=>{
  const code=installMainObservers.toString();assert.match(code,/new WeakMap/);assert.match(code,/file_serial:fileSerial/);
  assert.doesNotMatch(code,/files\.push|file:message\.payload\.file/);
  assert.match(workerSuffix,/workspace\.frame/);assert.match(workerSuffix,/view\?\.release\(\);proxy\.destroy\(\)/);
  assert.match(code,/finally\{delete globalThis\.__workerFileSerial295/);
});

for(const shared of [false,true])test('worker capacity observes current Wasm backing after inventory grows memory, shared='+shared,()=>{
  const memory=new WebAssembly.Memory({initial:1,maximum:3,shared});
  let released=false,destroyed=false;
  const proxy={getBuffer:()=>({data:new Uint8Array(memory.buffer),release(){released=true;}}),
    destroy(){destroyed=true;}};
  const python={runPython(code){
    if(code==='browser_workspace.workspace.frame')return proxy;
    memory.grow(1);return JSON.stringify({views:[]});
  },FS:{analyzePath:()=>({exists:false})}};
  const scope={};
  new Function('globalThis','python','sourceTransferMetrics','crossOriginIsolated',workerSuffix)(scope,python,{},shared);
  const observed=scope.__workerSourceResource295();
  assert.equal(observed.wasm_capacity_bytes,memory.buffer.byteLength);
  assert.equal(observed.wasm_capacity_bytes,2*65536);
  assert.equal(released,true);assert.equal(destroyed,true);
});

test('backend checkpoint observer preserves all producer bytes and requires exactly one closure marker',()=>{
  const source='export function createWorkerBackend(){\n  const backend={};\n'+backendObserverMarker;
  const observed=instrumentWorkerBackend(source);
  assert.equal(observed.replace(backendObserverInsertion,''),source);
  assert.equal(observed.split(backendObserverInsertion).length,2);
  assert.throws(()=>instrumentWorkerBackend('No supported marker'));
  assert.throws(()=>instrumentWorkerBackend(source+'\n'+backendObserverMarker));
});

test('injected observer reads current closure ownership and reflects File release without changing backend state',async()=>{
  const originalFile={size:25},serials=new WeakMap([[originalFile,1]]),scope={__workerFileSerial295:file=>serials.get(file)};
  const source=`function createWorkerBackend(){
    const identityJSON=${semanticJson.toString()},pending=new Map();
    let acceptedGeneration=3,sourceIdentity={source_sha256:'source'},sourceRevision='revision',lifetime=1,closed=false,recovering=false,sourceOperation;
    let checkpoint={mode:{kind:'scan'},generation:3,identity:sourceIdentity,revision:sourceRevision,
      file:originalFile,recipe:{nodes:[{operation:'source'}]}};
    const backend={tag:'unchanged',ownsOriginal:()=>checkpoint.file===originalFile,
      reset(){acceptedGeneration=4;sourceIdentity={};checkpoint={mode:{kind:'empty'},generation:4,identity:sourceIdentity,
        revision:sourceRevision,recipe:{nodes:[]}};}};
  return backend;
}`;
  const create=new Function('globalThis','crypto','TextEncoder','structuredClone','originalFile',
    instrumentWorkerBackend(source)+';return createWorkerBackend();');
  const backend=create(scope,webcrypto,TextEncoder,structuredClone,originalFile);
  const before=await scope.__workerCheckpoint295();
  assert.equal(backend.tag,'unchanged');assert.equal(backend.ownsOriginal(),true);
  assert.deepEqual(before.current_checkpoint.file,{serial:1,logical_bytes:25});
  assert.equal(before.current_checkpoint.generation,3);assert.equal(before.current_checkpoint.recipe_nodes,1);
  assert.equal(before.source_operation,null);assert.equal(JSON.stringify(before).includes('originalFile'),false);
  backend.reset();const after=await scope.__workerCheckpoint295();
  assert.equal(backend.ownsOriginal(),false);assert.equal(after.accepted_generation,4);
  assert.deepEqual(after.current_checkpoint.file,{serial:null,logical_bytes:0});
  assert.deepEqual(after.current_checkpoint.mode,{kind:'empty'});assert.deepEqual(after.current_checkpoint.identity,{});
  assert.equal(after.current_checkpoint.recipe_nodes,0);assert.notEqual(after.current_checkpoint.recipe_sha256,before.current_checkpoint.recipe_sha256);
});

test('recipe comparison ignores object key insertion order while retaining authored values and array order',()=>{
  const first={nodes:[{id:'source',operation:'source'},{id:'selected',ids:[3,7],operation:'selection'}],output:'selected'};
  const reordered={output:'selected',nodes:[{operation:'source',id:'source'},{operation:'selection',ids:[3,7],id:'selected'}]};
  assert.equal(semanticJson(first),semanticJson(reordered));
  assert.notEqual(semanticJson(first),semanticJson({...reordered,output:'source'}));
  assert.notEqual(semanticJson(first),semanticJson({...reordered,nodes:[...reordered.nodes].reverse()}));
  assert.notEqual(semanticJson(first),semanticJson({nodes:[first.nodes[0],{...first.nodes[1],ids:[7,3]}],output:'selected'}));
});

test('complete report accepts reordered recipe object keys and rejects changed recipe values or node order',()=>{
  const report=fixture(),row=report.modes[0].stages[2];
  row.ui.recipe={output:'source',nodes:[{id:'source',operation:'source'}]};
  row.checkpoint.current_checkpoint.recipe_sha256=createHash('sha256').update(semanticJson(row.ui.recipe)).digest('hex');
  row.settled_after.checkpoint=structuredClone(row.checkpoint);
  row.ui.recipe={nodes:[{operation:'source',id:'source'}],output:'source'};
  assert.doesNotThrow(()=>validateWorkerResourceReport(report));
  row.ui.recipe.output='changed';assert.throws(()=>validateWorkerResourceReport(report));
  const ordered=fixture();ordered.modes[0].stages[4].ui.recipe.nodes.reverse();
  assert.throws(()=>validateWorkerResourceReport(ordered));
});

test('worker inventory precondition rejects running lifecycle, active source ownership and pending requests',()=>{
  const value=fixture().modes[0].stages[2];assert.doesNotThrow(()=>assertWorkerInventoryQuiescent(value));
  for(const change of [
    item=>{item.lifecycle.state='running';},item=>{item.checkpoint.source_operation={file:{serial:1}};},
    item=>{item.checkpoint.pending_count=1;item.checkpoint.pending_operations=['prepare-source'];},
  ]){const changed=structuredClone(value);change(changed);assert.throws(()=>assertWorkerInventoryQuiescent(changed));}
});

test('native source ZIP guard validates closed trusted bytes before importing any artifact module',()=>{
  const helper=fileURLToPath(new URL('./worker_source_resource_expected.py',import.meta.url));
  const script=String.raw`
import hashlib, io, json, pathlib, runpy, stat, struct, sys, tempfile, warnings, zipfile
api=runpy.run_path(sys.argv[1]); validate=api['validated_source_members']
payload=b"raise AssertionError('artifact code must never execute')\n"
cases=['valid','tampered-module','extra','duplicate','missing','unsafe-path','path-alias','backslash','absolute','symlink','directory','special','size','wrong-source-record','duplicate-source-record','wrong-asset','encrypted','strong-encryption','compression']
passed=[]
with tempfile.TemporaryDirectory(prefix='worker-source-zip-guard-') as temporary:
 root=pathlib.Path(temporary); repo=root/'checkout'; runtime=root/'runtime'; repo.mkdir();runtime.mkdir()
 (repo/'module.py').write_bytes(payload)
 for fault in cases:
  entries=[('module.py',payload,stat.S_IFREG|0o644)]
  records=[dict(path='module.py',archive_path='module.py',sha256=hashlib.sha256(payload).hexdigest())]
  if fault=='tampered-module':entries[0]=('module.py',payload.replace(b'never',b'NEVER'),stat.S_IFREG|0o644)
  if fault=='extra':entries.append(('unexpected.py',payload,stat.S_IFREG|0o644))
  if fault=='duplicate':entries.append(entries[0])
  if fault=='missing':entries=[]
  if fault in ['unsafe-path','path-alias','backslash','absolute']:
   name={'unsafe-path':'../module.py','path-alias':'./module.py','backslash':'sub\\module.py','absolute':'/module.py'}[fault]
   entries[0]=(name,payload,stat.S_IFREG|0o644);records[0]['archive_path']=name
  if fault in ['symlink','directory','special']:
   kind={'symlink':stat.S_IFLNK,'directory':stat.S_IFDIR,'special':stat.S_IFIFO}[fault]
   entries[0]=('module.py',payload,kind|0o644)
  if fault=='size':entries[0]=('module.py',payload+b' ',stat.S_IFREG|0o644)
  if fault=='wrong-source-record':records[0]['sha256']='0'*64
  if fault=='duplicate-source-record':records.append(dict(records[0]))
  stream=io.BytesIO()
  with warnings.catch_warnings():
   warnings.simplefilter('ignore',UserWarning)
   with zipfile.ZipFile(stream,'w') as archive:
    for name,data,mode in entries:
     member=zipfile.ZipInfo(name);member.create_system=3;member.external_attr=mode<<16
     member.compress_type=zipfile.ZIP_BZIP2 if fault=='compression' else zipfile.ZIP_DEFLATED
     archive.writestr(member,data)
  raw=bytearray(stream.getvalue())
  if fault in ['encrypted','strong-encryption']:
   flag=1 if fault=='encrypted' else 64
   struct.pack_into('<H',raw,6,struct.unpack_from('<H',raw,6)[0]|flag)
   central=raw.index(b'PK\x01\x02');struct.pack_into('<H',raw,central+8,struct.unpack_from('<H',raw,central+8)[0]|flag)
  raw=bytes(raw);(runtime/'scansor.zip').write_bytes(raw)
  manifest=dict(source_files=records,assets=[dict(path='scansor.zip',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())])
  if fault=='wrong-asset':manifest['assets'][0]['sha256']='0'*64
  try:
   result=validate(runtime,manifest,repo)
  except (ValueError,zipfile.BadZipFile):
   if fault=='valid':raise
  else:
   if fault!='valid':raise AssertionError('guard accepted '+fault)
   assert result=={'module.py':payload}
  assert list(runtime.iterdir())==[runtime/'scansor.zip']
  passed.append(fault)
print(json.dumps(passed))
`;
  const result=spawnSync(process.env.SCANSOR_NATIVE_PYTHON||'python3',['-I','-c',script,helper],{encoding:'utf8',timeout:30000});
  assert.equal(result.status,0,result.stderr||result.stdout);
  assert.equal(JSON.parse(result.stdout).length,19);
});

test('end-of-run input freeze rejects code, source, reference, addition and removal drift',()=>{
  const frozen=[{path:'driver.mjs',sha256:sha},{path:'source.ply',sha256:'b'.repeat(64)}];
  assert.doesNotThrow(()=>assertTrustedInputsUnchanged(frozen,structuredClone(frozen)));
  for(const changed of [
    [{...frozen[0],sha256:'c'.repeat(64)},frozen[1]],
    [frozen[0],{...frozen[1],sha256:'c'.repeat(64)}],
    [...frozen,{path:'extra.py',sha256:sha}],frozen.slice(1),
  ])assert.throws(()=>assertTrustedInputsUnchanged(frozen,changed));
});
