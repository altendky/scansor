import test from 'node:test';
import assert from 'node:assert/strict';
import {circularAxisChoices,circularVolumeMappings,circularPlacements,circularSourceVolumeEdits,createCircularReuseEditor} from './circular-reuse-editor.js';
import {nodeReferences} from './action-tree.js';

const identity=[[1,0,0],[0,1,0],[0,0,1]],reference={selection:'input',entry:'entry',volume:'volume'},hash='a'.repeat(64);
const nodes=[{id:'axis',operation:'axis',label:'Axis',managed_by:null}, {id:'fit',operation:'fit',kind:'cylinder',label:'Bore',selections:['input'],managed_by:null},
  {id:'input',operation:'selection',label:'Original observations',entries:[{id:'entry',scan:{descriptor:{filename:'original.ply'}},
    volumes:[{id:'volume',label:'Actual support',archive:{sha256:hash}}]}]}];
test('circular choices retain exact volume identities and exclude unsupported or self-owned axes',()=>{
  assert.deepEqual(circularAxisChoices([...nodes,{id:'sphere',operation:'fit',kind:'sphere'},{id:'child',operation:'axis',managed_by:'owner'}],'owner').map(n=>n.id),['axis','fit']);
  const [record]=circularVolumeMappings(nodes,['fit']);assert.equal(record.source_selection,'input');
  assert.deepEqual(record.choices[0].reference,reference);assert.equal(record.choices[0].archive_sha256,hash);
  assert.equal(circularPlacements({placements:{occurrence_1:{occurrence:1,angle_degrees:180,rotation:identity,translation:[1,2,3]}}})[0].index,0);
});
test('circular occurrence keys never become action references, including placed datum children',()=>{
  const owner={id:'owner',operation:'feature_reuse',fits:['fit'],lineage:[],circular:{axis:'axis',count:3,volumes:[{volume:reference}]},target_selections:['occurrence_1']};
  assert.deepEqual(nodeReferences(owner),['fit','axis','input']);
  assert.deepEqual(nodeReferences({operation:'reuse_selection',reuse:'owner',fit:'fit',source_selection:'input',target_selection:'occurrence_1',occurrence:1}),['owner','fit','input']);
  assert.deepEqual(nodeReferences({operation:'axis',source_fit:'fit',placement:{reuse:'owner',source:'axis',target_selection:'occurrence_1',occurrence:1}}),['fit','owner','axis']);
});
test('source input edits require complete explicit mappings and preserve unrelated owners and roles atomically',()=>{
  const mappings=[{fit:'fit',source_selection:'input',volume:reference},{fit:'other',source_selection:'other_input',volume:{...reference,selection:'other_input'}}],
    owner={id:'owner',label:'Pattern',fits:['fit','other'],circular:{volumes:mappings}},
    other={id:'unrelated',fits:['other'],circular:{volumes:[mappings[1]]}},
    nodes=[owner,other],before=structuredClone(nodes);
  assert.throws(()=>circularSourceVolumeEdits(nodes,'fit',['input','new_input'],(_owner,selection)=>selection==='input'?reference:null),/Choose/);
  assert.deepEqual(nodes,before);
  const edits=circularSourceVolumeEdits(nodes,'fit',['new_input'],()=>({...reference,selection:'new_input'}));
  assert.deepEqual(edits,[{owner:'owner',volumes:[{fit:'fit',source_selection:'new_input',volume:{...reference,selection:'new_input'}},mappings[1]]}]);
  assert.deepEqual(nodes,before);
});

class Element {
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.listeners={};this.dataset={};this._value='';this.selected=false;this.hidden=false;}
  append(...items){for(const item of items)if(typeof item!=='string'){item.parentElement=this;this.children.push(item);}}
  replaceChildren(...items){this.children=[];this.append(...items);if(this.tagName==='SELECT')this.value=items[0]?.value||'';}
  setAttribute(name,value){this[name]=value;}
  addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
  contains(item){return this===item||this.children.some(child=>child.contains(item));}
  querySelectorAll(selector){const children=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);return selector==='*'?children:children.filter(child=>selector==='select'?child.tagName==='SELECT':false);}
  get options(){return this.children.filter(child=>child.tagName==='OPTION');}
  get selectedOptions(){return this.options.filter(item=>item.selected);}
  set value(value){this._value=String(value);for(const option of this.options)option.selected=option.value===this._value;}
  get value(){return this._value;}
  input(target=this){for(const callback of this.listeners.input||[])callback({target});}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({deferred=false,failure=false,volumeFailure=false,volumeDeferred=false,isActive=()=>true,sink=()=>{}}={}){
  const ids=new Map(),doc={createElement:tag=>new Element(tag,doc),getElementById:id=>ids.get(id)},root=doc.createElement('form');
  for(const name of ['mode','fits','circular']){const item=doc.createElement(name==='circular'?'div':'select');item.id='reuse-'+name;ids.set(item.id,item);root.append(item);}
  const OldOption=globalThis.Option;
  globalThis.Option=class extends Element{constructor(label,value){super('option',doc);this.textContent=label;this.value=value;}};
  const fits=ids.get('reuse-fits');fits.append(new Option('Bore','fit'));fits.value='fit';
  const state={graph:{token:'token',recipe:{nodes}},generation:2},calls=[],previews=[];
  let next=0,resolve,resolveVolume;
  const plan={token:'token',circular_preview:{ready:true,count:3,placements:{occurrence_1:{occurrence:1,angle_degrees:120,rotation:identity,translation:[1,0,0]},occurrence_2:{occurrence:2,angle_degrees:240,rotation:identity,translation:[2,0,0]}},
    volumes:[{fit:'fit',source_selection:'input',volume:reference,archive_sha256:hash}]}};
  const backend={async previewFeatureReuse(payload){calls.push(payload);if(failure)throw Error('Selected axis geometry failed');return deferred?await new Promise(done=>{resolve=done;}):plan;},
    async getSelectionVolumePreview(payload){
      if(volumeFailure)throw Error('Accepted support could not be read');
      const page={...payload,kind:'mesh',label:'Actual support',freshness:'current',total_cells:1,returned_cells:1,next_offset:null,
        positions:[0,0,0,1,0,0,0,1,0],normals:[0,0,1,0,0,1,0,0,1],bounds:{min:[0,0,0],max:[1,1,0]},resolved:{}};
      return volumeDeferred?await new Promise(done=>{resolveVolume=()=>done(page);}):page;
    }};
  let editor;
  editor=createCircularReuseEditor({root,prefix:'reuse-',backend,current:()=>state,changes:()=>({fits:['fit'],circular:editor.definition()}),
    isActive,onPreview:value=>{previews.push(value);sink(value);},newId:kind=>kind+'_'+(++next)});
  editor.open(null,{fresh:true});ids.get('reuse-mode').value='circular';
  for(const callback of ids.get('reuse-mode').listeners.change||[])callback();
  const all=root.querySelectorAll('*'),axis=all.find(item=>item.id==='reuse-axis'),count=all.find(item=>item.id==='reuse-count'),mapping=all.find(item=>item.dataset.mapping);
  axis.value='axis';count.value='3';mapping.value=JSON.stringify(reference,Object.keys(reference).sort());
  return{editor,root,state,calls,previews,plan,axis,count,mapping,preview:()=>all.find(item=>item.id==='reuse-preview').onclick(),
    cancel:()=>all.find(item=>item.textContent==='Cancel preview').onclick(),resolve:()=>resolve(plan),resolveVolume:()=>resolveVolume(),restore:()=>{globalThis.Option=OldOption;}};
}
test('preview and apply share owner/allocation seed; editing requires a fresh preview',async()=>{
  const f=fixture();try{
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
    f.preview();await flush();await flush();
    const apply=f.editor.authoring({fits:['fit'],circular:f.editor.definition()});assert.deepEqual(apply,f.calls[0]);
    assert(f.previews.some(value=>value?.geometry.total_cells===1));
    f.count.value='4';f.root.input(f.count);
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
  }finally{f.restore();}
});
test('late preview after a source/token change cannot publish geometry or authorize apply',async()=>{
  const f=fixture({deferred:true});try{
    f.preview();f.state.generation++;f.state.graph.token='new';f.editor.sync();f.resolve();await flush();
    assert(!f.previews.some(value=>value?.geometry));
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/project changed/);
    assert.equal(f.count.value,'3');assert.equal(f.axis.value,'axis');
  }finally{f.restore();}
});
test('cancelled preview preserves draft inputs and ignores a late successful backend reply',async()=>{
  const f=fixture({deferred:true});try{
    f.preview();f.cancel();f.resolve();await flush();
    assert.equal(f.axis.value,'axis');assert.equal(f.count.value,'3');
    assert(!f.previews.some(value=>value?.geometry));
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
  }finally{f.restore();}
});
test('failed preview retains the explicit volume mapping and cannot authorize apply',async()=>{
  const f=fixture({failure:true});try{
    f.preview();await flush();assert.equal(JSON.parse(f.mapping.value).volume,'volume');assert.equal(f.count.value,'3');
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
    assert(!f.previews.some(value=>value?.geometry));
  }finally{f.restore();}
});
test('a placement proposal cannot authorize Apply while its actual support pages are pending',async()=>{
  const f=fixture({volumeDeferred:true});try{
    f.preview();await flush();
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
    f.resolveVolume();await flush();
    assert.deepEqual(f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),f.calls[0]);
  }finally{f.restore();}
});
test('failed actual support loading cannot authorize Apply despite a valid placement proposal',async()=>{
  const f=fixture({volumeFailure:true});try{
    f.preview();await flush();
    assert(!f.previews.some(value=>value?.geometry));
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
    assert.equal(f.axis.value,'axis');assert.equal(f.count.value,'3');assert.equal(JSON.parse(f.mapping.value).volume,'volume');
  }finally{f.restore();}
});
test('cancelling a support-page read fences late geometry and preserves the same draft',async()=>{
  const f=fixture({volumeDeferred:true});try{
    f.preview();await flush();f.cancel();f.resolveVolume();await flush();
    assert(!f.previews.some(value=>value?.geometry));
    assert.equal(f.count.value,'3');assert.equal(JSON.parse(f.mapping.value).volume,'volume');
    assert.throws(()=>f.editor.authoring({fits:['fit'],circular:f.editor.definition()}),/Preview/);
  }finally{f.restore();}
});
test('hidden properties cannot overwrite or clear the active creation preview or authorize a different visible owner',async()=>{
  let active='create';const shown=[];
  const draft=fixture({isActive:()=>active==='create',sink:value=>shown.push({owner:'create',value})});
  const properties=fixture({isActive:()=>active==='properties',sink:value=>shown.push({owner:'properties',value})});
  try{
    draft.preview();await flush();assert.equal(shown.at(-1).value.placements.length,3);
    const owner={id:'old-owner',fits:['fit'],circular:{axis:'axis',count:5,volumes:[{fit:'fit',source_selection:'input',volume:reference}]}};
    properties.editor.open(owner,{fresh:true});properties.editor.updateResult(owner,{matches:Object.fromEntries([1,2,3,4].map(index=>['occurrence_'+index,{occurrence:index,angle_degrees:72*index,rotation:identity,translation:[index,0,0]}]))});
    await flush();assert.equal(shown.at(-1).owner,'create');assert.equal(shown.at(-1).value.placements.length,3);
    assert.doesNotThrow(()=>draft.editor.authoring({fits:['fit'],circular:draft.editor.definition()}));
    active='properties';properties.editor.updateResult(owner,{matches:{occurrence_1:{occurrence:1,angle_degrees:72,rotation:identity,translation:[1,0,0]}}});
    await flush();assert.equal(shown.at(-1).owner,'properties');
    const before=shown.length;draft.editor.clear();assert.equal(shown.length,before,'Inactive editor cleared the current preview');
    assert.throws(()=>draft.editor.authoring({fits:['fit'],circular:draft.editor.definition()}),/Preview/);
  }finally{properties.restore();draft.restore();}
});
