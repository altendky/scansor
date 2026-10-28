// Stage-only observations copied from the reviewed native resource observer.
// Histories contain scalars; Files use WeakMap IDs and buffers are not retained.
import assert from 'node:assert/strict';

export const backendObserverMarker='  return backend;\n}';
export const backendObserverInsertion=`
  // Test-route observation only: no backend operation or authority is replaced.
  globalThis.__workerCheckpoint295=async()=>{
    const serial=globalThis.__workerFileSerial295;
    if(typeof serial!=='function')throw Error('Transient File identity observer missing');
    const file=value=>({serial:value?serial(value):null,logical_bytes:value?.size??0});
    const current=checkpoint?{mode:structuredClone(checkpoint.mode),generation:checkpoint.generation,
      identity:structuredClone(checkpoint.identity),revision:checkpoint.revision,
      file:file(checkpoint.file),recipe_nodes:checkpoint.recipe.nodes.length,
      source_nodes:checkpoint.recipe.nodes.filter(node=>node.operation==='source').length,
      selection_nodes:checkpoint.recipe.nodes.filter(node=>node.operation==='selection').length,
      fit_nodes:checkpoint.recipe.nodes.filter(node=>node.operation==='fit').length}:null;
    const recipeText=checkpoint?identityJSON(checkpoint.recipe):null;
    const value={accepted_generation:acceptedGeneration??null,source_identity:structuredClone(sourceIdentity??null),
      source_revision:sourceRevision??null,lifetime,closed,recovering,current_checkpoint:current,
      pending_count:pending.size,pending_operations:[...pending.values()].map(request=>request.operation).sort(),
      source_operation:sourceOperation?{kind:sourceOperation.kind??null,purpose:sourceOperation.purpose,
        adopted:!!sourceOperation.adopted,status:sourceOperation.record.status,
        file:file(sourceOperation.file),retained_file:file(sourceOperation.retainedFile)}:null};
    if(current)current.recipe_sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
      new TextEncoder().encode(recipeText)))).map(byte=>byte.toString(16).padStart(2,'0')).join('');
    return value;
  };
`;

export function instrumentWorkerBackend(source){
  const first=source.indexOf(backendObserverMarker);
  assert.ok(first>=0&&first===source.lastIndexOf(backendObserverMarker),'Expected exactly one backend closure observer marker');
  return source.slice(0,first)+backendObserverInsertion+source.slice(first);
}

export const appSuffix=`
// Read-only named backing-store observer; no retained array/geometry history.
globalThis.__workerSourceRender295=()=>({frames,pending});
globalThis.__workerSourceReady295=()=>({source_sha256:metadata?.source?.source_sha256,
 generation:workspaceGeneration,selected_index_count:selectedPoints?.geometry?.index?.count||0,
 selections:graphState?.recipe.nodes.filter(n=>n.operation==='selection').map(n=>(graphState.memberships?.[n.id]||n.ids||[]).length)||[]});
globalThis.__workerSourceUi295=async(hashes=false)=>{
 if(!positions||!mesh?.geometry||!graphState)return null;
 const views=[],buffers=new Map();let viewCount=0;
 const add=(name,array)=>{if(!array?.buffer)return;viewCount++;
  if(!buffers.has(array.buffer))buffers.set(array.buffer,buffers.size);
  if(views.length<256)views.push({name,dtype:array.constructor.name,length:array.length,
    logical_bytes:array.byteLength,backing_id:buffers.get(array.buffer),backing_bytes:array.buffer.byteLength});};
 add('positions',positions);
 const names=new Map([[mesh,'mesh'],[selectedPoints,'selectedPoints'],[focusedPoints,'focusedPoints'],
   [overlapMarkers,'overlapMarkers'],[overlapHalo,'overlapHalo']]);
 let objects=0;scene.traverse(object=>{objects++;if(!object.geometry)return;
  const name=names.get(object)||object.type+'.'+objects;
  for(const[key,a]of Object.entries(object.geometry.attributes))add(name+'.'+key,a.array||a.data?.array);
  add(name+'.indices',object.geometry.index?.array);});
 const selection=graphState.recipe.nodes.filter(n=>n.operation==='selection').map(n=>{const ids=graphState.memberships?.[n.id]||n.ids||[];return{id:n.id,count:ids.length,
   min_id:ids.length?ids.reduce((a,b)=>Math.min(a,b),Infinity):null,max_id:ids.length?ids.reduce((a,b)=>Math.max(a,b),-Infinity):null};});
 const indices=mesh.geometry.index?.array;
 const digest=async array=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
   new Uint8Array(array.buffer,array.byteOffset,array.byteLength)))).map(v=>v.toString(16).padStart(2,'0')).join('');
 const snapshot={observed_performance_ms:performance.now(),time_origin_ms:performance.timeOrigin,
  setup:!!viewerSetup,singleton:backend===getBackend(),generation:workspaceGeneration,
  recipe:graphState.recipe,lifecycle:backend.getLifecycle(),
  graph_generation:graphState.project_generation,source:metadata.source||null,
  vertices:metadata.vertices,triangles:metadata.triangles,display_triangles:metadata.display_triangles??metadata.triangles,
  source_nodes:graphState.recipe.nodes.filter(n=>n.operation==='source').length,
  recipe_nodes:graphState.recipe.nodes.length,fit_nodes:graphState.recipe.nodes.filter(n=>n.operation==='fit').length,
  graph_states:Object.fromEntries(Object.entries(graphState.states).reduce((m,[,s])=>m.set(s,(m.get(s)||0)+1),new Map())),
  limits:metadata.capabilities?.source_import?.limits,positions_length:positions.length,indices_length:indices?.length||0,
  selected_index_count:selectedPoints.geometry.index?.count||0,focused_index_count:focusedPoints.geometry.index?.count||0,
  selection,selected_visible:selectedPoints.visible,frames,pending,inert:document.querySelector('main').inert,
  source_progress_hidden:$('source-progress').hidden,input_files:$('scan-file').files.length,
  counts_text:$('counts').textContent,mesh_info:$('mesh-info').textContent,
  arrays:{views,view_count:viewCount,views_truncated:viewCount>views.length,
    unique_backing_bytes:[...buffers.keys()].reduce((n,b)=>n+b.byteLength,0),objects},
  render:{calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,points:renderer.info.render.points,
    geometries:renderer.info.memory.geometries,textures:renderer.info.memory.textures}};
 if(hashes)snapshot.display_hashes={positions:await digest(positions),indices:await digest(indices)};
 return snapshot;
};
`;

export const backendSuffix = `globalThis.__workerResourceBackend295=backend;`;

const pythonInventory = `
def _worker_source_inventory295():
 import json, hashlib, numpy as np, sys
 b=browser_workspace;w=b.workspace
 pairs=[('source.xyz',w.data.xyz),('source.normals',w.data.normals),
  ('source.triangles',w.data.triangles),('source.weights',w.data.weights),
  ('solver.local',w.local),('frame.origin',w.origin),('frame.columns',w.frame),
  ('display.positions',b.display_positions),('display.indices',b.display_indices)]
 for name in ('original_normals','face_status','ids'):
  value=getattr(w.data,name,None)
  if isinstance(value,np.ndarray):pairs.append(('source.'+name,value))
 roots={};views=[]
 for name,array in pairs:
  owner=array
  while isinstance(owner.base,np.ndarray):owner=owner.base
  address=str(owner.__array_interface__['data'][0]);roots[address]=int(owner.nbytes)
  views.append(dict(name=name,dtype=str(array.dtype),shape=list(array.shape),logical_bytes=int(array.nbytes),root_address=address,root_bytes=int(owner.nbytes),writeable=bool(array.flags.writeable)))
 observations={}
 for name,array in [('xyz',w.data.xyz),('original_normals',w.data.original_normals),('triangles',w.data.triangles),('normals',w.data.normals),('weights',w.data.weights),('local',w.local)]:
  observations[name]=dict(dtype=str(array.dtype),shape=list(array.shape),sha256=hashlib.sha256(array.tobytes()).hexdigest())
 raw=getattr(w,'original_bytes',b'')
 return json.dumps(dict(views=views,unique_root_bytes=sum(roots.values()),
  immutable_original_bytes=len(raw),immutable_original_sha256=hashlib.sha256(raw).hexdigest(),
  export_bytes=len(b.export_buffer),cad_imported='OCP' in sys.modules,
  descriptor=w.source_descriptor.model_dump() if w.source_descriptor else None,
  source_sha256=w.source_sha256,project_generation=b.workspace_generation,source_mode=b.source_mode,
  vertices=len(w.local),faces=len(w.data.triangles),observations=observations),allow_nan=False)
_worker_source_inventory295()
`;

export const workerSuffix = `
// Stage-only synchronous observations at quiescent UI boundaries, never RPC polling.
globalThis.__workerSourceResource295=()=>{
 const proxy=python.runPython('browser_workspace.workspace.frame');let view;
 try{
  const record=JSON.parse(python.runPython(${JSON.stringify(pythonInventory)}));
  const files=[];
  if(python.FS.analyzePath('/scansor-source-staging').exists)
   for(const name of python.FS.readdir('/scansor-source-staging')){
    if(name==='.'||name==='..')continue;
    const info=python.FS.stat('/scansor-source-staging/'+name);files.push({name,bytes:info.size});
   }
  // Inventory can grow Wasm memory and detach or stale an earlier buffer.
  view=proxy.getBuffer('u8');
  return {...record,wasm_capacity_bytes:view.data.buffer.byteLength,
   transfer:{...sourceTransferMetrics},owned_fs_files:files,
   owned_fs_bytes:files.reduce((n,f)=>n+f.bytes,0),isolated:crossOriginIsolated};
 }finally{view?.release();proxy.destroy();}
};
`;

export const installMainObservers=()=>{
    const fileIds=new WeakMap();let nextFile=0;
    const fileSerial=file=>{if(!file)return null;let id=fileIds.get(file);if(!id){id=++nextFile;fileIds.set(file,id);}return id;};
    globalThis.__workerWithFileSerial295=async observe=>{
      if(typeof observe!=='function'||Object.hasOwn(globalThis,'__workerFileSerial295'))throw Error('Overlapping File identity observation');
      globalThis.__workerFileSerial295=fileSerial;
      try{return await observe();}finally{delete globalThis.__workerFileSerial295;}
    };
    const transport=globalThis.__workerTransport295={workers:0,terminated:[],prepared:[],files_selected:[]};
    const NativeWorker=globalThis.Worker;
    globalThis.Worker=class extends NativeWorker{
      constructor(...args){super(...args);this.resourceIndex=++transport.workers;}
      terminate(){transport.terminated.push(this.resourceIndex);return super.terminate();}
      postMessage(message,...args){
        if(message.operation==='prepare-source')transport.prepared.push({worker:this.resourceIndex,
          purpose:message.payload.purpose||'source',kind:message.payload.kind,recovery:!!message.payload.recovery,
          file_serial:fileSerial(message.payload.file),file_bytes:message.payload.file?.size??0});
        return super.postMessage(message,...args);
      }
    };
    document.addEventListener('change',event=>{if(['scan-file','locate-source-file'].includes(event.target.id)){
      const file=event.target.files[0];transport.files_selected.push({serial:fileSerial(file),bytes:file?.size??0,
        name:file?.name??null,input:event.target.id,at_ms:performance.now()});}},true);
    document.addEventListener('change',event=>{if(event.target.id==='scan-file')globalThis.__workerImportWindow295={
      active:true,started_ms:performance.now(),heartbeat_ticks:0,heartbeat_max_gap_ms:0,heartbeat_sum_gap_ms:0,
      raf_ticks:0,raf_max_gap_ms:0,raf_sum_gap_ms:0};},true);
    const heartbeat=globalThis.__workerHeartbeat295={ticks:0,max_gap_ms:0,last_ms:performance.now(),sum_gap_ms:0};
    setInterval(()=>{const now=performance.now(),gap=now-heartbeat.last_ms,window=globalThis.__workerImportWindow295;
      if(window?.active){const elapsed=now-Math.max(window.started_ms,heartbeat.last_ms);window.heartbeat_ticks++;
        window.heartbeat_sum_gap_ms+=elapsed;window.heartbeat_max_gap_ms=Math.max(window.heartbeat_max_gap_ms,elapsed);}heartbeat.last_ms=now;
      heartbeat.ticks++;heartbeat.sum_gap_ms+=gap;heartbeat.max_gap_ms=Math.max(heartbeat.max_gap_ms,gap);},16);
    const raf=globalThis.__workerRaf295={count:0,sum_gap_ms:0,max_gap_ms:0,over_16_ms:0,over_50_ms:0,last_ms:null};
    globalThis.__workerInput295={sequence:0,event_ms:null,event_frames:null,observed_ms:null,render_frames:null};
    document.addEventListener('change',event=>{if(event.target.id==='scan-file'){
      const file=event.target.files[0];globalThis.__workerFile295={event_ms:performance.now(),name:file?.name,size:file?.size};}},true);
    document.addEventListener('wheel',event=>{if(event.target.closest('canvas')){
      const input=globalThis.__workerInput295;Object.assign(input,{sequence:input.sequence+1,event_ms:performance.now(),
        event_frames:globalThis.__workerSourceRender295?.().frames??null,observed_ms:null,render_frames:null});}},{capture:true,passive:true});
    const tick=now=>{const window=globalThis.__workerImportWindow295;
      if(window?.active){const elapsed=now-Math.max(window.started_ms,raf.last_ms??window.started_ms);
        if(elapsed>=0){window.raf_ticks++;window.raf_sum_gap_ms+=elapsed;window.raf_max_gap_ms=Math.max(window.raf_max_gap_ms,elapsed);}}
      if(raf.last_ms!==null){const gap=now-raf.last_ms;raf.count++;raf.sum_gap_ms+=gap;raf.max_gap_ms=Math.max(raf.max_gap_ms,gap);
      if(gap>16)raf.over_16_ms++;if(gap>50)raf.over_50_ms++;}raf.last_ms=now;
      const input=globalThis.__workerInput295,render=globalThis.__workerSourceRender295?.();
      if(input.event_ms!==null&&input.observed_ms===null&&render?.frames>input.event_frames){input.observed_ms=performance.now();input.render_frames=render.frames;input.proxy_ms=input.observed_ms-input.event_ms;}
      requestAnimationFrame(tick);};requestAnimationFrame(tick);
    const contexts=[],getContext=HTMLCanvasElement.prototype.getContext;
    globalThis.__workerGpu295=()=>({contexts:contexts.map(s=>({logical_live_buffer_bytes:[...s.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0),
      logical_high_water_bytes:s.high,buffer_data_calls:s.calls,deleted_buffers:s.deleted,renderer:s.renderer,
      context_lost:s.gl.isContextLost(),canvas:[s.gl.drawingBufferWidth,s.gl.drawingBufferHeight]}))});
    HTMLCanvasElement.prototype.getContext=function(...args){const gl=getContext.apply(this,args);
      if(!gl||!/^webgl/.test(args[0])||contexts.some(s=>s.gl===gl))return gl;
      const debug=gl.getExtension('WEBGL_debug_renderer_info'),state={gl,buffers:new Map(),ids:new WeakMap(),next:0,high:0,calls:0,deleted:0,
        renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER)};contexts.push(state);
      const data=gl.bufferData,del=gl.deleteBuffer;
      const binding=new Map([[gl.ARRAY_BUFFER,gl.ARRAY_BUFFER_BINDING],[gl.ELEMENT_ARRAY_BUFFER,gl.ELEMENT_ARRAY_BUFFER_BINDING],
        [gl.COPY_READ_BUFFER,gl.COPY_READ_BUFFER_BINDING],[gl.COPY_WRITE_BUFFER,gl.COPY_WRITE_BUFFER_BINDING],
        [gl.PIXEL_PACK_BUFFER,gl.PIXEL_PACK_BUFFER_BINDING],[gl.PIXEL_UNPACK_BUFFER,gl.PIXEL_UNPACK_BUFFER_BINDING],
        [gl.UNIFORM_BUFFER,gl.UNIFORM_BUFFER_BINDING],[gl.TRANSFORM_FEEDBACK_BUFFER,gl.TRANSFORM_FEEDBACK_BUFFER_BINDING]]);
      gl.bufferData=function(...values){const returned=data.apply(this,values),query=binding.get(values[0]);if(query===undefined)return returned;
        const buffer=this.getParameter(query),size=this.getBufferParameter(values[0],this.BUFFER_SIZE);if(!buffer||!Number.isFinite(size))return returned;
        let id=state.ids.get(buffer);if(!id){id=++state.next;state.ids.set(buffer,id);}state.buffers.set(id,{ref:new WeakRef(buffer),bytes:size});state.calls++;
        state.high=Math.max(state.high,[...state.buffers.values()].reduce((n,b)=>n+(b.ref.deref()?b.bytes:0),0));return returned;};
      gl.deleteBuffer=function(buffer){const returned=del.call(this,buffer);if(state.buffers.delete(state.ids.get(buffer)))state.deleted++;return returned;};return gl;
    };
  };
