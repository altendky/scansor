// These are experimental retained-DAG references. Eligibility is supplied by
// the backend; the browser only restricts candidates to this editor's context.
export function inputReferenceKey(reference) {
  if (!reference) return '';
  if (typeof reference === 'string') return reference;
  return JSON.stringify({ feature: reference.feature, output: reference.output,
    context: reference.context || reference.feature });
}

export function readInputReference(value) {
  if (!value) return null;
  if (typeof value !== 'string') return value;
  if (value.startsWith('{')) {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed.feature === 'string' && typeof parsed.output === 'string') return parsed;
    } catch { /* A legacy feature identity is preserved verbatim. */ }
  }
  return value;
}

export function referenceFeature(reference) {
  return typeof reference === 'string' ? reference : reference?.feature;
}

export function requirementOutputs(state, requirement) {
  const resolved = state?.input_requirements?.[requirement];
  return resolved ? [...resolved.choices, ...resolved.unavailable] : [];
}

export function matchingInputOutput(reference, outputs) {
  if (!reference) return null;
  const parsed = readInputReference(reference);
  if (typeof parsed === 'string') {
    const matches = outputs.filter(output => output.reference.feature === parsed);
    return matches.length === 1 ? matches[0] : null;
  }
  return outputs.find(output => inputReferenceKey(output.reference) === inputReferenceKey(parsed)) || null;
}

export function inputChoices(state, requirement, nodes = state?.recipe.nodes || []) {
  const allowed = new Set(nodes.map(node => node.id));
  return requirementOutputs(state, requirement).filter(output => allowed.has(output.reference.feature))
    .map(output => {
      const dependencies = output.dependencies || [output.reference.feature, output.reference.context].filter(Boolean);
      return dependencies.every(id => allowed.has(id)) ? output : { ...output,
        availability: 'blocked', reason: 'This output depends on a later feature' };
    });
}

export function inputOptionRecords(outputs, selected = null) {
  const parsed = readInputReference(selected), matched = matchingInputOutput(selected, outputs),
    ambiguousLegacy = typeof parsed === 'string' && outputs.filter(output => output.reference.feature === parsed).length > 1,
    retainLegacy = typeof parsed === 'string' && (!matched || matched.availability !== 'ready' || !!matched.reason),
    selectedKey = matched && !retainLegacy ? inputReferenceKey(matched.reference) : inputReferenceKey(parsed);
  const options = outputs.map(output => ({ key: inputReferenceKey(output.reference),
    label: output.reason ? `${output.label} — ${output.reason}` : output.label,
    disabled: output.availability !== 'ready' || !!output.reason,
    selected: inputReferenceKey(output.reference) === selectedKey }));
  if (selectedKey && !options.some(option => option.key === selectedKey)) options.push({
    key: selectedKey, label: ambiguousLegacy ? 'Multiple output contexts — choose an explicit output'
      : matched ? `${matched.label} — ${matched.reason || 'Choose an explicit output to repair this legacy reference'}`
        : 'Unavailable saved output — feature or output removed', disabled: true, selected: true,
  });
  return options;
}

export function renderInputChoices(control, outputs, selected = control.value) {
  const options = inputOptionRecords(outputs, selected);
  control.replaceChildren(new Option('Choose an output…', '', false, !options.some(option => option.selected)),
    ...options.map(record => {
      const option = new Option(record.label, record.key, false, record.selected);
      option.disabled = record.disabled;
      return option;
    }));
}
