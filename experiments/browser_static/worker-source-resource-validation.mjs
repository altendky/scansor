import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

export const RESOURCE_SOURCES=['raw-nozzle','horizontal-scene','grid-512x256'];
export const ORDINARY_STAGES=['empty-baseline','file-selected','imported','inspected','selection-overlay','reset'];
const finite=(value,name)=>assert.ok(typeof value==='number'&&Number.isFinite(value)&&value>=0,name);
const count=(value,name)=>{finite(value,name);assert.ok(Number.isSafeInteger(value),name);};
const digest=(value,name)=>assert.match(value??'',/^[a-f0-9]{64}$/,name);
export function assertTrustedInputsUnchanged(before,after){assert.deepEqual(after,before,'Trusted source/input bytes changed during measurement');}
export function semanticJson(value){return JSON.stringify(value,function(_key,item){
  return item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item;
});}
export function assertWorkerInventoryQuiescent(value){
  assert.equal(value.lifecycle?.state,'complete','Worker inventory requires completed backend lifecycle');
  assert.equal(value.file_serial_exposure_released,true,'File observer exposure remains active');
  assert.ok(value.checkpoint,'Missing quiescent checkpoint observation');
  assert.equal(value.checkpoint.closed,false);assert.equal(value.checkpoint.recovering,false);
  assert.equal(value.checkpoint.source_operation,null,'Worker inventory overlaps source operation');
  assert.equal(value.checkpoint.pending_count,0,'Worker inventory overlaps pending RPC');
  assert.deepEqual(value.checkpoint.pending_operations,[],'Worker inventory overlaps pending operation');
}
const empty=row=>{
  assert.ok(row.ui&&row.worker,'Missing empty boundary evidence');
  for(const key of ['vertices','triangles','positions_length','indices_length','selected_index_count','recipe_nodes','input_files'])assert.equal(row.ui[key],0,key);
  assert.equal(row.worker.immutable_original_bytes,0);assert.equal(row.worker.source_sha256,null);
  assert.equal(row.worker.descriptor,null);assert.equal(row.ui.source,null);assert.deepEqual(row.ui.recipe.nodes,[]);
  assert.deepEqual(row.checkpoint.current_checkpoint.mode,{kind:'empty'});
  assert.deepEqual(row.checkpoint.current_checkpoint.identity,{});
  assert.deepEqual(row.checkpoint.current_checkpoint.file,{serial:null,logical_bytes:0},'Reset checkpoint still owns original File');
  assert.equal(row.checkpoint.source_operation,null,'Reset left active source File ownership');
  assert.equal(row.worker.vertices,0);assert.equal(row.worker.faces,0);
  assert.equal(row.worker.owned_fs_bytes,0);assert.equal(row.worker.transfer.staging_bytes,0);
  assert.ok(Array.isArray(row.worker.views)&&row.worker.views.length>0);
  for(const view of row.worker.views)if(/^(source\.|solver\.local|display\.)/.test(view.name))assert.equal(view.logical_bytes,0,'Reset retained source/display NumPy array');
  for(const view of row.ui.arrays.views)if(/^(positions|mesh\.|selectedPoints\.|focusedPoints\.|overlapMarkers\.|overlapHalo\.)/.test(view.name))assert.equal(view.logical_bytes,0,'Reset retained production display');
};
function resources(row,isolation,limits,sourceRevision){
  count(row.worker_index,'worker index');assert.ok(row.worker_index>0);
  for(const key of ['usedSize','totalSize','backingStorageSize'])finite(row.main_heap[key],'Missing main CDP '+key);
  for(const value of Object.values(row.main_heap))finite(value,'main CDP heap');
  for(const key of ['ticks','max_gap_ms','sum_gap_ms'])finite(row.heartbeat[key],'heartbeat '+key);
  for(const key of ['count','max_gap_ms','sum_gap_ms','over_16_ms','over_50_ms'])finite(row.raf[key],'rAF '+key);
  assert.ok(Array.isArray(row.gpu.contexts)&&row.gpu.contexts.length>0,'Actual WebGL context missing');
  for(const gpu of row.gpu.contexts){finite(gpu.logical_live_buffer_bytes,'WebGL logical bytes');finite(gpu.logical_high_water_bytes,'WebGL high water');
    assert.ok(gpu.logical_live_buffer_bytes<=gpu.logical_high_water_bytes);assert.equal(gpu.context_lost,false);}
  assert.equal(row.file_serial_exposure_released,true,'Transient File observer was not released');
  assert.ok(row.checkpoint,'Missing current outside-worker checkpoint observation');
  assert.equal(row.checkpoint.source_revision,sourceRevision,'Outside-worker checkpoint runtime source revision differs');
  if(row.stage==='file-selected'){
    assert.equal(row.worker,null);assert.equal(row.worker_heap,null);assert.equal(row.ui,null);
    assert.ok(row.unavailable_reason?.includes('active'),'Active-RPC unavailable interval must be explicit');return;
  }
  assertWorkerInventoryQuiescent(row);assertWorkerInventoryQuiescent(row.settled_after);
  assert.equal(row.settled_after.worker_index,row.worker_index,'Worker changed across inventory');
  assert.deepEqual(row.settled_after.lifecycle,row.lifecycle,'Backend lifecycle changed across inventory');
  assert.deepEqual(row.settled_after.checkpoint,row.checkpoint,'Accepted checkpoint changed across inventory');
  assert.ok(row.worker_heap?.session_id&&row.worker_heap?.url,'Missing CDP worker identity');
  for(const key of ['usedSize','totalSize','backingStorageSize'])finite(row.worker_heap[key],'Missing worker CDP '+key);
  for(const[key,value]of Object.entries(row.worker_heap))if(!['session_id','url'].includes(key))finite(value,'worker CDP '+key);
  const w=row.worker,ui=row.ui;assert.ok(w&&ui,'Missing settled evidence');
  assert.deepEqual(ui.lifecycle,row.lifecycle,'UI lifecycle changed after worker inventory');
  const observed=row.checkpoint,current=observed.current_checkpoint;assert.ok(current,'Missing accepted checkpoint');
  assert.equal(observed.closed,false);assert.equal(observed.recovering,false);assert.equal(observed.source_operation,null,'Settled active source operation');
  assert.equal(observed.accepted_generation,ui.generation);assert.equal(current.generation,ui.generation,'Stale checkpoint generation');
  assert.deepEqual(current.mode,w.source_mode,'Checkpoint/source mode differs');assert.deepEqual(current.identity,observed.source_identity,'Checkpoint/source identity differs');
  assert.equal(current.revision,observed.source_revision,'Checkpoint runtime source revision differs');
  assert.equal(current.recipe_sha256,createHash('sha256').update(semanticJson(ui.recipe)).digest('hex'),'Current checkpoint recipe differs');
  assert.equal(current.recipe_nodes,ui.recipe_nodes);
  for(const[field,operation]of [['source_nodes','source'],['selection_nodes','selection'],['fit_nodes','fit']])
    assert.equal(current[field],ui.recipe.nodes.filter(node=>node.operation===operation).length,'Checkpoint recipe count differs');
  assert.equal(w.isolated,isolation);assert.ok(w.wasm_capacity_bytes>0);finite(w.wasm_capacity_bytes,'Wasm capacity');
  for(const key of ['unique_root_bytes','immutable_original_bytes','export_bytes','owned_fs_bytes'])count(w[key],key);
  assert.equal(w.owned_fs_bytes,0);assert.deepEqual(w.owned_fs_files,[]);assert.equal(w.transfer.staging_bytes,0);
  for(const value of Object.values(w.transfer))count(value,'transfer counter');
  assert.equal(ui.singleton,true);assert.equal(ui.generation,ui.graph_generation);assert.equal(ui.generation,w.project_generation);
  assert.deepEqual(ui.limits,limits,'Existing source admission settings changed');
  assert.equal(ui.inert,false);assert.equal(ui.source_progress_hidden,true);assert.equal(ui.arrays.views_truncated,false);
  count(ui.arrays.unique_backing_bytes,'display backing');
  assert.ok(w.transfer.max_staging_bytes<=ui.limits.max_source_bytes,'Staging high water exceeds existing cap');
  assert.ok(w.transfer.largest_chunk_bytes<=ui.limits.chunk_bytes,'Chunk exceeds current cap');
}
function acceptedSource(row,input,reference){
  const observationKeys=['local','normals','original_normals','triangles','weights','xyz'];
  assert.deepEqual(Object.keys(reference.observations).sort(),observationKeys,'Incomplete independent original observations');
  for(const key of observationKeys){
    const record=reference.observations[key];digest(record.sha256,'Original observation digest');
    assert.equal(record.dtype,key==='original_normals'?'float32':key==='triangles'?'int32':'float64');
    assert.deepEqual(record.shape,key==='triangles'?[input.faces,3]:key==='weights'?[input.vertices]:[input.vertices,3]);
  }
  assert.equal(reference.source_sha256,input.sha256);assert.equal(reference.display.positions.bytes,input.vertices*3*4);
  assert.equal(reference.display.indices.bytes,input.faces*3*4);
  const w=row.worker,ui=row.ui;assert.equal(w.source_sha256,input.sha256);assert.equal(w.immutable_original_sha256,input.sha256);
  const checkpoint=row.checkpoint.current_checkpoint;assert.deepEqual(checkpoint.mode,{kind:'scan'});
  assert.deepEqual(checkpoint.identity,reference.source_identity,'Current checkpoint source/settings identity differs');
  count(checkpoint.file.serial,'Current checkpoint File serial');assert.ok(checkpoint.file.serial>0);
  assert.equal(checkpoint.file.logical_bytes,input.source_bytes,'Current checkpoint File size differs');
  assert.equal(w.immutable_original_bytes,input.source_bytes);assert.equal(w.vertices,input.vertices);assert.equal(w.faces,input.faces);
  const{filename:observedFilename,...observedDescriptor}=w.descriptor;
  const{filename:referenceFilename,...referenceDescriptor}=reference.descriptor;void observedFilename;void referenceFilename;
  assert.deepEqual(observedDescriptor,referenceDescriptor,'Source/settings identity');
  assert.deepEqual(w.observations,reference.observations,'Original observations changed');
  assert.ok(w.transfer.max_staging_bytes>=input.source_bytes,'Staging high-water does not include accepted File');
  assert.equal(ui.source.source_sha256,input.sha256);assert.equal(ui.vertices,input.vertices);assert.equal(ui.triangles,input.faces);
  assert.equal(ui.fit_nodes,0);assert.equal(ui.source_nodes,1);assert.equal(ui.input_files,0);
  assert.equal(ui.positions_length,input.vertices*3);assert.equal(ui.indices_length,input.faces*3);
  assert.ok(ui.display_hashes,'Missing stage display identity');
  for(const key of ['positions','indices'])assert.equal(ui.display_hashes[key],reference.display[key].sha256,'Display identity');
  assert.ok(ui.arrays.views.some(view=>/normal/.test(view.name)&&view.logical_bytes>0),'Production normal storage missing');
  assert.ok(ui.arrays.views.some(view=>/color/.test(view.name)&&view.logical_bytes>0),'Production color storage missing');
}

/** Validate meaningful evidence, not resource shrinkage or a fitted threshold. */
export function validateWorkerResourceReport(report){
  assert.equal(report.schema_version,1);assert.equal(report.kind,'scansor-worker-product-ui-resources');assert.equal(report.complete,true);
  assert.equal(report.cycles,2);assert.deepEqual(report.source_names,RESOURCE_SOURCES);
  assert.deepEqual(report.inputs.cases.map(input=>input.name),RESOURCE_SOURCES);
  assert.equal(report.artifact_unchanged,true);assert.equal(report.browser_closed,true);
  for(const key of ['failure','cleanup_failure'])assert.equal(report[key],undefined,key);
  digest(report.manifest_sha256,'manifest');digest(report.reference_sha256,'native reference');
  assert.ok(report.native_reference,'Full independent source digests must accompany report');
  assert.equal(createHash('sha256').update(JSON.stringify(report.native_reference)).digest('hex'),report.reference_value_sha256,'Native reference value identity');
  const guard=report.source_guard;assert.ok(guard,'Missing source freeze guard');
  assert.equal(report.source_worktree_dirty,false);assert.equal(guard.runtime_source_worktree_dirty,false);
  assert.equal(guard.complete,true);assert.equal(guard.source_files_match,true);assert.equal(guard.rehashed_unchanged,true);
  assert.match(report.source_revision,/^[a-f0-9]{40}$/);assert.match(guard.checkout_revision,/^[a-f0-9]{40}$/);
  assert.equal(guard.artifact_source_revision,report.source_revision);assert.equal(guard.runtime_source_revision,report.source_revision);
  assert.ok(guard.runtime_source_records>0&&guard.site_source_records>0&&guard.files.length>=guard.runtime_source_records);
  assert.equal(new Set(guard.files.map(record=>record.path)).size,guard.files.length);
  for(const record of guard.files){assert.ok(record.path&&!record.path.startsWith('/')&&!record.path.split('/').includes('..'));digest(record.sha256,'Trusted source digest');}
  for(const record of guard.frozen_input_files)digest(record.sha256,'Frozen reference/source digest');
  assert.equal(guard.frozen_input_files.find(record=>record.path.endsWith('/native-source-resource-reference.json'))?.sha256,report.reference_sha256);
  assert.equal(report.modes.length,2);assert.deepEqual(report.modes.map(mode=>mode.isolation_requested),[false,true]);
  let ordinary=0;
  for(const mode of report.modes){
    assert.equal(mode.complete,true);assert.equal(mode.failure,undefined);assert.equal(mode.isolation_actual,mode.isolation_requested);
    assert.deepEqual(mode.external_requests,[]);assert.deepEqual(mode.api_requests,[]);assert.deepEqual(mode.page_errors,[]);
    assert.equal(mode.cleanup.context_closed,true);assert.equal(mode.cleanup.preview_closed,true);
    assert.equal(mode.ordinary_worker_index,1);
    assert.equal(mode.backend_observer_injections.length,1,'Backend closure was not observed exactly once');
    const insertion=mode.backend_observer_injections[0];assert.equal(insertion.marker_matches,1);
    digest(insertion.original_sha256,'Original backend identity');digest(insertion.observed_sha256,'Observed backend identity');
    assert.equal(insertion.insertion_sha256,report.instrumentation.backend_observer_insertion_sha256);
    assert.equal(insertion.path,'app/worker-backend.js');
    assert.equal(insertion.original_sha256,guard.files.find(record=>record.path==='experiments/browser_viewer/worker-backend.js')?.sha256);
    assert.notEqual(insertion.observed_sha256,insertion.original_sha256);
    const planned=[];for(let cycle=1;cycle<=2;cycle++)for(const source of RESOURCE_SOURCES)for(const stage of ORDINARY_STAGES)planned.push({cycle,source,stage});
    const rows=mode.stages.filter(row=>row.cycle!==0);
    assert.deepEqual(rows.map(({cycle,source,stage})=>({cycle,source,stage})),planned,'Ordinary preregistered stages missing/reordered');
    let epoch=null,session=null,heartbeat=0;
    for(let i=0;i<rows.length;i+=ORDINARY_STAGES.length){
      const group=rows.slice(i,i+ORDINARY_STAGES.length),input=report.inputs.cases.find(item=>item.name===group[0].source);
      assert.ok(input);digest(input.sha256,'input');assert.ok(input.source_bytes>0);
      for(const row of group){resources(row,mode.isolation_requested,guard.source_limits,report.source_revision);assert.equal(row.worker_index,1,'Ordinary cycle replaced worker');
        assert.ok(row.heartbeat.ticks>=heartbeat,'Heartbeat counter regressed');heartbeat=row.heartbeat.ticks;
        if(row.worker_heap){session??=row.worker_heap.session_id;assert.equal(row.worker_heap.session_id,session,'Ordinary CDP session changed');}}
      const[baseline,file,imported,inspected,overlay,reset]=group;empty(baseline);empty(reset);
      if(epoch!==null)assert.equal(baseline.ui.generation,epoch);
      assert.equal(imported.ui.generation,baseline.ui.generation+1);assert.equal(reset.ui.generation,imported.ui.generation+1);epoch=reset.ui.generation;
      for(const row of [imported,inspected,overlay]){acceptedSource(row,input,report.native_reference[input.name]);assert.equal(row.ui.generation,imported.ui.generation);}
      for(const row of [inspected,overlay])assert.equal(row.checkpoint.current_checkpoint.file.serial,imported.checkpoint.current_checkpoint.file.serial,'Ordinary checkpoint File changed');
      assert.equal(inspected.source_dom_verified,true);assert.equal(imported.ui.recipe_nodes,1);
      assert.ok(file.file_event?.size===input.source_bytes);finite(imported.import_to_visible_observation_ms,'Import observation');
      const window=imported.import_window;assert.ok(window&&window.active===false,'Import responsiveness interval missing');
      for(const key of ['started_ms','end_ms','duration_ms','heartbeat_ticks','heartbeat_max_gap_ms','heartbeat_sum_gap_ms','raf_ticks','raf_max_gap_ms','raf_sum_gap_ms'])finite(window[key],'Import interval '+key);
      assert.equal(window.end_ms-window.started_ms,window.duration_ms);assert.equal(window.duration_ms,imported.import_to_visible_observation_ms);
      assert.ok(imported.heartbeat.ticks>=baseline.heartbeat.ticks);
      assert.equal(overlay.ui.selection.length,1);const selection=overlay.ui.selection[0];
      assert.ok(selection.count>0&&selection.count<=25000,'Bounded selection admission');assert.ok(selection.min_id>=0&&selection.max_id<input.vertices);
      assert.ok(overlay.ui.selected_index_count>0);assert.equal(overlay.ui.selected_visible,true);
      for(const row of [imported,overlay]){assert.ok(row.input_proxy.sequence>0);finite(row.input_proxy.proxy_ms,'Wheel/render correlation proxy');}
      ordinary++;
    }
    const special=mode.stages.filter(row=>row.cycle===0);
    assert.deepEqual(special.map(row=>row.stage),['force-before','force-after','reopen-empty','reopened','final-reset']);
    for(const row of special)resources(row,mode.isolation_requested,guard.source_limits,report.source_revision);
    const[before,after,reopenEmpty,reopened,final]=special,grid=report.inputs.cases.find(item=>item.name==='grid-512x256');
    for(const row of [before,after,reopened])acceptedSource(row,grid,report.native_reference[grid.name]);
    assert.equal(before.worker_index,1);assert.equal(after.worker_index,2);assert.equal(reopened.worker_index,2);
    assert.notEqual(before.worker_heap.session_id,after.worker_heap.session_id,'Explicit Force failed to replace worker');
    assert.equal(reopened.worker_heap.session_id,after.worker_heap.session_id,'Reopen replaced ordinary worker');
    assert.equal(before.ui.generation,epoch+1);assert.equal(after.ui.generation,before.ui.generation);
    assert.equal(reopenEmpty.ui.generation,before.ui.generation+1);assert.equal(reopened.ui.generation,before.ui.generation+2);
    assert.equal(final.ui.generation,reopened.ui.generation+1);empty(reopenEmpty);empty(final);
    assert.deepEqual(after.ui.recipe,before.ui.recipe);assert.deepEqual(reopened.ui.recipe,before.ui.recipe);
    assert.equal(mode.transport.workers,2);assert.deepEqual(mode.transport.terminated,[1]);
    const prepared=mode.transport.prepared.filter(item=>item.file_serial!==null);assert.equal(prepared.length,9,'Unexpected preparation/hidden retry');
    assert.equal(prepared.filter(item=>item.recovery).length,1);assert.equal(prepared[7].worker,2);assert.equal(prepared[7].recovery,true);
    assert.equal(prepared[7].file_serial,prepared[6].file_serial,'Force lost retained original File identity');
    assert.notEqual(prepared[8].file_serial,prepared[6].file_serial,'Renamed reopening did not supply distinct real File');
    assert.equal(prepared[8].worker,2);assert.equal(prepared[8].file_bytes,grid.source_bytes);
    for(let index=0;index<6;index++){
      const group=rows.slice(index*ORDINARY_STAGES.length,(index+1)*ORDINARY_STAGES.length);
      for(const row of group.filter(row=>['imported','inspected','selection-overlay'].includes(row.stage)))
        assert.equal(row.checkpoint.current_checkpoint.file.serial,prepared[index].file_serial,'Prepared/current checkpoint File differs');
    }
    assert.equal(before.checkpoint.current_checkpoint.file.serial,prepared[6].file_serial);
    assert.equal(after.checkpoint.current_checkpoint.file.serial,before.checkpoint.current_checkpoint.file.serial,'Force changed actual current checkpoint File');
    assert.notEqual(reopened.checkpoint.current_checkpoint.file.serial,before.checkpoint.current_checkpoint.file.serial,'Reopened checkpoint retained prior File');
    assert.equal(reopened.checkpoint.current_checkpoint.file.serial,prepared[8].file_serial);
    assert.deepEqual(mode.worker_events.filter(event=>event.event==='created').map(event=>event.index),[1,2]);
    assert.deepEqual(mode.worker_events.filter(event=>event.event==='closed').map(event=>event.index),[1,2]);
  }
  return{complete:true,ordinary_cycles:ordinary,plain_and_isolated:true,explicit_grid_force_and_reopen:true,
    original_observations_and_settings:true,bounded_transfers_and_selection:true,owned_cleanup:true,
    caveat:'Separate overlapping sampled/logical counters; no summed memory, forced GC, peak, threshold or leak verdict.'};
}
