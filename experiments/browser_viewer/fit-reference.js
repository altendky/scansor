import {inputChoices, inputOptionRecords, inputReferenceKey, readInputReference} from './feature-inputs.js';

export function fitReferenceContracts(state) {
  return Object.entries(state?.input_contracts || {}).filter(([, contract]) => contract.fit_reference)
    .map(([id, contract]) => ({id, ...contract}));
}

export function fitReferenceForNode(state, node) {
  const contracts = fitReferenceContracts(state);
  for (const field of ['axis', 'point', 'reference_plane']) {
    if (!node[field]) continue;
    const contract = contracts.find(contract => contract.fit_reference.field === field
      && contract.fit_reference.kinds.includes(node.kind));
    return contract ? {mode: contract.id, reference: {feature: node[field], output: contract.fit_reference.output,
      context: node[field]}} : {mode: `unavailable:${field}`, reference: node[field]};
  }
  return {mode: '', reference: null};
}

export function fitReferenceInputs(state, kind, draft, nodes = state?.recipe?.nodes || []) {
  const fields = {axis: null, point: null, reference_plane: null};
  if (!draft.mode) return fields;
  const contract = fitReferenceContracts(state).find(contract => contract.id === draft.mode);
  if (!contract || !contract.fit_reference.kinds.includes(kind))
    throw Error('This reference relationship is unavailable for this fit type. Choose a supported relationship or None (standalone).');
  const reference = readInputReference(draft.reference);
  if (!reference || typeof reference === 'string' || reference.context !== reference.feature
    || reference.output !== contract.fit_reference.output)
    throw Error('Choose an explicit direct datum property. A solved publication cannot be stored as this fit reference.');
  const output = inputChoices(state, contract.id, nodes)
    .find(output => inputReferenceKey(output.reference) === inputReferenceKey(reference));
  if (!output || output.availability !== 'ready' || output.compatible === false || output.reason)
    throw Error(output?.reason || 'This reference is unavailable. Choose a compatible earlier datum or None (standalone).');
  const expectedFrame = state.recipe?.nodes.find(node => node.operation === 'source')?.reference_sha256 || 'workspace';
  if (!output.properties?.[contract.property] || output.frame !== expectedFrame
    || output.properties[contract.property].frame !== expectedFrame)
    throw Error('The reference does not supply the declared property in the workspace frame.');
  // The retained schema stores a feature identity. Flatten only after proving
  // the exact direct output/context required by that field's shared contract.
  fields[contract.fit_reference.field] = reference.feature;
  return fields;
}

export function readFitReferenceEditor(id, doc = document) {
  return {mode: doc.getElementById(`${id}-mode`).value,
    reference: readInputReference(doc.getElementById(id).value)};
}

export function renderFitReferenceEditor({id, state, kind, nodes, selected, doc = document}) {
  const control = doc.getElementById(id), mode = doc.getElementById(`${id}-mode`), hint = doc.getElementById(`${id}-hint`);
  control.dataset.fitReferenceEditor = 'true';
  const draft = selected ?? readFitReferenceEditor(id, doc), contracts = fitReferenceContracts(state);
  const allowed = contracts.filter(contract => contract.fit_reference.kinds.includes(kind));
  mode.replaceChildren(new Option('None (standalone)', '', false, !draft.mode),
    ...allowed.map(contract => new Option(contract.fit_reference.label, contract.id, false, contract.id === draft.mode)));
  if (draft.mode && !allowed.some(contract => contract.id === draft.mode)) {
    const retained = contracts.find(contract => contract.id === draft.mode), option = new Option(
      `${retained?.fit_reference.label || 'Saved reference relationship'} — unavailable for ${kind}`, draft.mode, false, true);
    option.disabled = true; mode.add(option);
  }
  const contract = contracts.find(contract => contract.id === draft.mode);
  const compatibleMode = contract?.fit_reference.kinds.includes(kind);
  const outputs = contract ? inputChoices(state, contract.id, nodes).map(output => ({...output,
    label: `${contract.fit_reference.label} — ${output.label}`})) : [];
  if (draft.mode) {
    control.dataset.inputRequirement = draft.mode;
    const options = inputOptionRecords(outputs, draft.reference);
    control.replaceChildren(new Option('Choose a reference property…', '', false, !draft.reference), ...options.map(record => {
      const option = new Option(record.label, record.key, false, record.selected);
      option.disabled = record.disabled || !compatibleMode; return option;
    }));
  } else {
    delete control.dataset.inputRequirement;
    control.replaceChildren(new Option('None (standalone)', '', false, true));
  }
  control.required = !!draft.mode;
  const selectedOutput = outputs.find(output => inputReferenceKey(output.reference) === inputReferenceKey(draft.reference));
  const property = selectedOutput?.properties?.[contract?.property];
  const descriptions = allowed.map(item => item.fit_reference.label).join('; ');
  let message = !draft.mode ? `Standalone fit. Reference choices: ${descriptions || 'unavailable in this runtime'}.`
    : !compatibleMode ? 'The saved relationship is unavailable for this fit type. Choose a supported relationship or None (standalone).'
      : selectedOutput?.reason || selectedOutput?.availability !== 'ready' && draft.reference
        ? selectedOutput?.reason || 'The saved property is unavailable. Repair it or choose None (standalone).'
        : `${contract.fit_reference.label}. ${property?.binding_eligible
          ? 'The shared datum keeps its declared freedom in connected fitting.'
          : property?.binding_reason || 'Choose an earlier datum property; fixed upstream geometry remains fixed.'}`;
  if (compatibleMode && !outputs.some(output => output.availability === 'ready' && !output.reason))
    message += ` Create a ${contract.geometry_kinds.join(' or ')} datum before this ${kind} fit.`;
  if (['cylinder', 'cone'].includes(kind)) message += ' This shares the complete centerline; direction-only relationships are a separate operation.';
  hint.textContent = message;
  hint.classList.toggle('error', !!draft.mode && (!compatibleMode || !!selectedOutput?.reason));
  control.dataset.referenceHint = message;
  return {draft, contract, outputs};
}
