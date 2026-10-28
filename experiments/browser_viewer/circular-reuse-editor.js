import {createVolumePreviewReader, referenceKey} from './selection-owned-editor.js';

export function circularAxisChoices(nodes, owner = null) {
  return nodes.filter(node => node.id !== owner && (!owner || node.managed_by !== owner) &&
    (node.operation === 'axis' || node.operation === 'fit' && ['cylinder', 'cone'].includes(node.kind)));
}
export function circularVolumeMappings(nodes, fits) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  return fits.flatMap(fit => (byId.get(fit)?.selections || []).map(source_selection => {
    const selection = byId.get(source_selection);
    return {fit, source_selection, label: `${byId.get(fit)?.label || fit} · ${selection?.label || source_selection}`,
      choices: (selection?.entries || []).flatMap(entry => (entry.volumes || []).map(volume => ({
        reference: {selection: source_selection, entry: entry.id, volume: volume.id},
        archive_sha256: volume.archive.sha256,
        label: `${volume.label} · ${entry.scan.descriptor?.filename || entry.id}`,
      })))};
  }));
}
export function circularPlacements(value) {
  const source = value?.placements || value?.matches || {};
  const values = Array.isArray(source) ? source : Object.entries(source).map(([key, placement]) => ({key, ...placement}));
  const placements = values.map(item => ({...item, index: item.occurrence ?? item.index,
    key: item.key || `occurrence_${item.occurrence ?? item.index}`}));
  if (!placements.some(item => item.index === 0)) placements.unshift({index: 0, key: 'original', angle_degrees: 0,
    rotation: [[1,0,0],[0,1,0],[0,0,1]], translation: [0,0,0]});
  return placements.sort((a,b) => a.index-b.index);
}
export function circularSourceVolumeEdits(nodes, fit, inputs, choose) {
  return nodes.filter(node=>node.circular&&node.fits.includes(fit)).map(owner=>({owner:owner.id,
    volumes:owner.fits.flatMap(id=>id!==fit?owner.circular.volumes.filter(item=>item.fit===id):inputs.map(source_selection=>{
      const volume=choose(owner.id,source_selection);
      if(!volume)throw Error(`Choose an accepted source volume for ${owner.label} · ${source_selection} before applying the fitted feature.`);
      return{fit,source_selection,volume};
    }))}));
}
export function createCircularSourceFitEditor({root,inputs,current}) {
  const doc=root.ownerDocument,panel=doc.createElement('section');panel.id='fit-circular-volumes';root.append(panel);
  let fit=null;
  function render(fresh=false) {
    const previous=new Map([...panel.querySelectorAll('select')].map(control=>[control.dataset.mapping,control.value]));
    const nodes=current().graph.recipe.nodes,owners=nodes.filter(node=>node.circular&&node.fits.includes(fit)),
      selected=[...inputs.selectedOptions].map(option=>option.value),
      changed=nodes.map(node=>node.id===fit?{...node,selections:selected}:node);
    panel.replaceChildren();panel.hidden=!owners.length;
    if(!owners.length)return;
    const title=doc.createElement('h3');title.textContent='Circular reuse source volumes';panel.append(title);
    const hint=doc.createElement('p');hint.className='hint';hint.textContent='Fit inputs and their circular source volumes are saved together. Choose an explicit volume for each new input; Cancel preserves the accepted pattern.';panel.append(hint);
    for(const owner of owners)for(const record of circularVolumeMappings(changed,[fit])) {
      const label=doc.createElement('label'),control=doc.createElement('select'),key=`${owner.id}/${record.source_selection}`;
      label.append(`${owner.label} · ${record.label}`);control.dataset.mapping=key;control.dataset.owner=owner.id;control.dataset.sourceSelection=record.source_selection;
      control.append(new Option(record.choices.length?'Choose an accepted volume…':'No owned volume in this input',''));
      for(const choice of record.choices)control.append(new Option(choice.label,referenceKey(choice.reference)));
      const saved=owner.circular.volumes.find(item=>item.fit===fit&&item.source_selection===record.source_selection)?.volume;
      control.value=!fresh&&previous.has(key)?previous.get(key):referenceKey(saved);
      label.append(control);panel.append(label);
    }
  }
  inputs.addEventListener('change',()=>{if(fit)render();});
  return{
    show(node,{fresh=false}={}){if(node?.id===fit&&!fresh)return;fit=node?.id||null;if(fit)render(true);else{panel.hidden=true;panel.replaceChildren();}},
    apply(recipe,identifier){
      const node=recipe.nodes.find(node=>node.id===identifier),controls=[...panel.querySelectorAll('select')];
      const edits=circularSourceVolumeEdits(recipe.nodes,identifier,node.selections,(owner,source)=>{
        const value=controls.find(control=>control.dataset.owner===owner&&control.dataset.sourceSelection===source)?.value;
        return value?JSON.parse(value):null;
      });
      for(const edit of edits)recipe.nodes.find(item=>item.id===edit.owner).circular.volumes=edit.volumes;
    },
  };
}
export function createCircularReuseEditor({root, prefix, backend, current, changes,
  onPreview = () => {}, onStatus = () => {}, isActive = () => true, newId = kind => `${kind}_${crypto.randomUUID().replaceAll('-', '')}`}) {
  const doc = root.ownerDocument, find = suffix => doc.getElementById(prefix + suffix), mode = find('mode'), fits = find('fits');
  const panel = find('circular'), mappings = doc.createElement('div'), message = doc.createElement('p');
  const field = (label, control, parent = panel) => { const node = doc.createElement('label'); node.append(label, control); parent.append(node); return control; };
  const select = (suffix, label) => { const node = doc.createElement('select'); node.id = prefix + suffix; return field(label, node); };
  const axis = select('axis', 'Pattern axis'), count = doc.createElement('input');
  count.id = prefix + 'count'; count.type = 'number'; count.min = '2'; count.max = '32'; count.step = '1'; count.value = '5';
  field('Total occurrences (including original)', count);
  const hint = doc.createElement('p'); hint.className = 'hint'; hint.textContent =
    'Occurrence 1 is the original. Other occurrences rotate equally around the selected axis. Each copied fit refits its own observations; no pose constraints are added. Choose an accepted volume for every fit input.';
  panel.append(hint, mappings);
  const previewButton = doc.createElement('button'); previewButton.type = 'button'; previewButton.id = prefix + 'preview'; previewButton.textContent = 'Preview circular reuse';
  const cancel = doc.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel preview'; cancel.hidden = true;
  const mappingChoice = select('inspect-volume', 'Inspect volume'), occurrence = select('occurrence', 'Inspect occurrence');
  message.id = prefix + 'preview-status'; message.setAttribute('role','status'); panel.append(previewButton,cancel,message);
  let owner = null, create = true, reuseId, seed, baseToken, baseGeneration, signature = null,
    proposal = null, inspected = null, reviewSignature = null, sequence = 0, controller, selectedReference = null, accepted = null, dirty = false;
  const selectValues = control => [...control.selectedOptions].map(option => option.value);
  const option = (value,label) => new Option(label,value);
  const stateSignature = request => JSON.stringify([request, current().generation]);
  const identity = () => ({token: current().graph.token, generation: current().generation,
    reference: isActive() ? selectedReference?.reference : null, archive_sha256: isActive() ? selectedReference?.archive_sha256 : null});
  const reader = createVolumePreviewReader({request: payload => backend.getSelectionVolumePreview(payload), current: identity,
    dispose: () => { inspected = null; signature = null; if(isActive())onPreview(null); },
    progress: (loaded,total) => { message.textContent = `Loading accepted support: ${loaded.toLocaleString()} of ${total.toLocaleString()} cells…`; },
    publish: geometry => {
      inspected = geometry;
      if (proposal && reviewSignature === stateSignature(request())) signature = reviewSignature;
      paint();
    },
  });
  function paint() {
    if(!isActive())return;
    const plan = proposal?.circular_preview || accepted;
    if (!inspected || !plan) { onPreview(null); return; }
    const all = circularPlacements(plan), chosen = occurrence.value === 'all' ? all : all.filter(item => String(item.index) === occurrence.value);
    onPreview({geometry: inspected, placements: chosen});
    message.textContent = `${inspected.total_cells.toLocaleString()} accepted support cells · ${chosen.length === all.length ? `${all.length} occurrences` : `Occurrence ${chosen[0].index+1} · ${chosen[0].angle_degrees}°`}. ${inspected.freshness === 'current' ? '' : 'Source volume requires refresh; review its accepted support.'}`;
    if (chosen.length === 1 && accepted) {
      const mapping = accepted.volumes[Number(mappingChoice.value)], index = chosen[0].index, state = current().graph;
      if (index === 0) message.textContent += ` Original input: ${(state.memberships[mapping.source_selection] || []).length} vertices.`;
      else {
        const selection = state.recipe.nodes.find(node=>node.operation==='reuse_selection'&&node.reuse===owner&&node.occurrence===index&&node.fit===mapping.fit&&node.source_selection===mapping.source_selection);
        const diagnostic = state.results[selection?.id]?.selection_diagnostics;
        if (diagnostic) message.textContent += ` Selected ${diagnostic.selected_vertices} vertices; source ${diagnostic.source_vertices}; overlap with original ${diagnostic.overlap_occurrence_zero}; other overlaps ${diagnostic.overlaps.reduce((total,item)=>total+item.vertices,0)}. These are vertex counts, not an area-coverage estimate.`;
        else if (selection) message.textContent += ` ${state.errors[selection.id] || `Selection ${state.states[selection.id]}; evaluate to inspect its vertex counts.`}`;
      }
    }
  }
  function invalidate(text = 'Preview placement before applying circular reuse.') {
    sequence++; controller?.abort(); controller = null; proposal = null; signature = null; reviewSignature = null;
    reader.clear(); message.textContent = text; cancel.hidden = true; previewButton.disabled = false;
  }
  function displayMode() {
    const circular = mode.value === 'circular'; panel.hidden = !circular;
    for (const node of root.querySelectorAll('[data-reuse-matched]')) {
      node.hidden = circular;
      for (const control of node.querySelectorAll('input,select')) control.required = !circular;
    }
  }
  function renderMappings(saved = []) {
    const previous = new Map([...mappings.querySelectorAll('select')].map(control => [control.dataset.mapping, control.value]));
    const records = circularVolumeMappings(current().graph.recipe.nodes, selectValues(fits));
    mappings.replaceChildren();
    for (const record of records) {
      const control = doc.createElement('select'), key = `${record.fit}/${record.source_selection}`;
      control.dataset.mapping = key; control.dataset.fit = record.fit; control.dataset.sourceSelection = record.source_selection;
      control.append(option('', record.choices.length ? 'Choose an accepted volume…' : 'No owned volume; author one in this source selection'));
      for (const choice of record.choices) control.append(option(referenceKey(choice.reference), choice.label));
      control.value = previous.get(key) || referenceKey(saved.find(item => item.fit === record.fit && item.source_selection === record.source_selection)?.volume);
      field(record.label, control, mappings);
    }
  }
  function definition() {
    if (mode.value !== 'circular') return null;
    const number = Number(count.value);
    if (!axis.value || !Number.isSafeInteger(number) || number < 2 || number > 32) throw Error('Choose a pattern axis and an occurrence count from 2 to 32.');
    const volumes = [...mappings.querySelectorAll('select')].map(control => {
      if (!control.value) throw Error('Choose an accepted source volume for every fit input.');
      return {fit: control.dataset.fit, source_selection: control.dataset.sourceSelection, volume: JSON.parse(control.value)};
    });
    if (!volumes.length) throw Error('Choose at least one fitted feature with an owned source volume.');
    return {axis: axis.value, count: number, volumes};
  }
  function request(values = changes()) {
    if (baseToken !== current().graph.token || baseGeneration !== current().generation)
      throw Error('The project changed while this draft was open. Close and reopen reuse before applying.');
    return {token: baseToken, reuse_id: reuseId, allocation_seed: seed, create, changes: values};
  }
  function inspection(plan) {
    const references = plan.volumes || [];
    const previous = mappingChoice.value;
    mappingChoice.replaceChildren(...references.map((record,index) => option(String(index),
      `${current().graph.recipe.nodes.find(node => node.id === record.fit)?.label || record.fit} · ${record.source_selection}`)));
    mappingChoice.value = [...mappingChoice.options].some(item => item.value === previous) ? previous : '0';
    const placements = circularPlacements(plan), old = occurrence.value;
    occurrence.replaceChildren(option('all', `All ${placements.length} occurrences`),
      ...placements.map(item => option(String(item.index), `Occurrence ${item.index+1} · ${item.angle_degrees}°${item.index===0?' · Original':''}`)));
    occurrence.value = [...occurrence.options].some(item => item.value === old) ? old : 'all';
    return inspectVolume();
  }
  async function inspectVolume() {
    const plan = proposal?.circular_preview || accepted, item = plan?.volumes?.[Number(mappingChoice.value)];
    reader.clear(); selectedReference = item ? {reference: item.volume, archive_sha256: item.archive_sha256} : null;
    if (!selectedReference) return;
    try { await reader.load(selectedReference.reference, selectedReference.archive_sha256); }
    catch (error) { signature = null; reviewSignature = null; message.textContent = error.message; onStatus(error.message, true); }
  }
  async function preview() {
    const ticket = ++sequence; controller?.abort(); controller = new AbortController();
    proposal = null; signature = null; reviewSignature = null;
    previewButton.disabled = true; cancel.hidden = false; reader.clear();
    try {
      if(!isActive())throw Error('This reuse draft is no longer the active editor.');
      if (current().locked || current().graph.evaluation_running) throw Error('Wait for the current evaluation or edit before authoring reuse.');
      const payload = request(), fingerprint = stateSignature(payload);
      message.textContent = 'Resolving the selected axis and circular placements…';
      const result = await backend.previewFeatureReuse(payload,{signal:controller.signal});
      if (ticket !== sequence || fingerprint !== stateSignature(request())) return;
      if (result.token !== payload.token || !result.circular_preview?.ready) throw Error(result.circular_preview?.error || 'The selected axis or source volumes are not ready.');
      proposal = result; reviewSignature = fingerprint; await inspection(result.circular_preview);
      if (ticket !== sequence) return;
      if (!inspected || signature !== fingerprint) throw Error(message.textContent || 'The accepted support preview could not be loaded.');
      message.textContent = `${result.circular_preview.count} occurrences reviewed. Apply uses these same generated identities.`;
    } catch (error) {
      if (ticket !== sequence) return;
      proposal = null; signature = null; reviewSignature = null; message.textContent = error.message; onStatus(error.message,true);
    } finally { if (ticket === sequence) { controller = null; cancel.hidden = true; previewButton.disabled = false; } }
  }
  root.addEventListener('input',event => {
    if ([mappingChoice,occurrence].includes(event.target)) return;
    if (!root.contains(event.target)) return;
    dirty = true; accepted = null; invalidate();
  });
  mode.addEventListener('change',()=>{ dirty = true; displayMode(); renderMappings(); invalidate(); });
  fits.addEventListener('change',()=>{ dirty = true; renderMappings(); invalidate(); });
  previewButton.onclick = () => void preview(); cancel.onclick = () => invalidate('Preview cancelled. Your reuse draft is unchanged.');
  mappingChoice.onchange = () => void inspectVolume(); occurrence.onchange = paint;
  return {
    get circular() { return mode.value === 'circular'; }, definition,
    open(node = null, {fresh = false} = {}) {
      const key = node?.id || null;
      if (!fresh && reuseId && owner === key) return;
      invalidate(); owner = key; create = !node; reuseId = node?.id || newId('feature_reuse'); seed = newId('reuse_ids');
      baseToken = current().graph.token; baseGeneration = current().generation;
      mode.value = node?.circular ? 'circular' : fresh && current().graph.recipe.nodes.filter(item=>['selection','growth','region_selection','reuse_selection'].includes(item.operation)).length<2 ? 'circular' : 'matched';
      axis.replaceChildren(option('','Choose an axis…'),...circularAxisChoices(current().graph.recipe.nodes,node?.id).map(item=>option(item.id,item.label)));
      axis.value = node?.circular?.axis || ''; count.value = node?.circular?.count || '5';
      mappings.replaceChildren(); renderMappings(node?.circular?.volumes); displayMode(); accepted = null; dirty = false;
    },
    authoring(values) {
      const payload = request(values);
      if (this.circular && (!isActive() || !proposal || !inspected || signature !== stateSignature(payload))) throw Error('Preview the current circular placements before applying.');
      return payload;
    },
    updateResult(node,result) {
      if (!isActive() || !node?.circular || owner !== node.id || proposal || dirty || !result?.matches) return;
      const volumes = node.circular.volumes.map(record => {
        const selection = current().graph.recipe.nodes.find(item=>item.id===record.volume.selection),
          volume = selection?.entries?.find(item=>item.id===record.volume.entry)?.volumes?.find(item=>item.id===record.volume.volume);
        return {...record,archive_sha256:volume?.archive.sha256};
      });
      if (volumes.some(item=>!item.archive_sha256)) return;
      const next = {count:node.circular.count,placements:result.matches,volumes};
      if (JSON.stringify(next) === JSON.stringify(accepted)) { paint(); return; }
      accepted = next; inspection(next);
    },
    sync() {
      if (reuseId && (baseToken !== current().graph.token || baseGeneration !== current().generation)) {
        accepted = null; dirty = true;
        invalidate('The project changed. Close and reopen this draft to use the current source and actions.');
      }
    },
    clear() { invalidate(''); accepted = null; selectedReference = null; reuseId = null; owner = null; reader.clear(); },
  };
}
