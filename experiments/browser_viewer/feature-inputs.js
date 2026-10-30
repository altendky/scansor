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

export function inputChoices(state, requirement, nodes = state?.recipe.nodes || [], {use} = {}) {
  const allowed = new Set(nodes.map(node => node.id));
  return requirementOutputs(state, requirement).filter(output => allowed.has(output.reference.feature))
    .map(output => {
      const contract = state.input_contracts?.[requirement], property = output.properties?.[contract?.property],
        bindingAlias = use !== 'geometry' && (use === 'binding' || contract?.uses?.includes('binding')) &&
          (output.reference.context || output.reference.feature) === output.reference.feature &&
          property?.binding_eligible && property.binding_aliases?.length,
        dependencies = (bindingAlias ? output.binding_dependencies : null) || output.dependencies ||
          [output.reference.feature, output.reference.context].filter(Boolean);
      return dependencies.every(id => allowed.has(id)) ? output : { ...output,
        availability: 'blocked', reason: 'This output depends on a later feature' };
    });
}

export function inputCanBeReferenced(output, {allowPending = false} = {}) {
  if (output.compatible === false) return false;
  if (output.availability === 'ready') return !output.reason;
  return allowPending && output.compatible === true &&
    ['stale', 'unevaluated'].includes(output.availability);
}

export function inputOptionRecords(outputs, selected = null, policy = {}) {
  const parsed = readInputReference(selected), matched = matchingInputOutput(selected, outputs),
    ambiguousLegacy = typeof parsed === 'string' && outputs.filter(output => output.reference.feature === parsed).length > 1,
    retainLegacy = typeof parsed === 'string' && (!matched || !inputCanBeReferenced(matched, policy)),
    selectedKey = matched && !retainLegacy ? inputReferenceKey(matched.reference) : inputReferenceKey(parsed);
  const options = outputs.map(output => ({ key: inputReferenceKey(output.reference),
    label: inputCanBeReferenced(output, policy) && output.availability !== 'ready'
      ? `${output.label} — needs evaluation`
      : output.reason ? `${output.label} — ${output.reason}` : output.label,
    disabled: !inputCanBeReferenced(output, policy),
    selected: inputReferenceKey(output.reference) === selectedKey }));
  if (selectedKey && !options.some(option => option.key === selectedKey)) options.push({
    key: selectedKey, label: ambiguousLegacy ? 'Multiple output contexts — choose an explicit output'
      : matched ? `${matched.label} — ${matched.reason || 'Choose an explicit output to repair this legacy reference'}`
        : 'Unavailable saved output — feature or output removed', disabled: true, selected: true,
  });
  return options;
}

export function renderInputChoices(control, outputs, selected = control.value, options = {}) {
  if (control.multiple) {
    const values = Array.isArray(selected) ? selected : selected ? [selected] : [], records = new Map();
    for (const value of values.length ? values : [null]) for (const option of inputOptionRecords(outputs, value, options)) {
      const previous = records.get(option.key);
      records.set(option.key, { ...option, selected: option.selected || !!previous?.selected });
    }
    control.replaceChildren(...[...records.values()].map(record => {
      const option = new Option(record.label, record.key, false, record.selected);
      option.disabled = record.disabled;
      return option;
    }));
    return;
  }
  const records = inputOptionRecords(outputs, selected, options);
  control.replaceChildren(new Option(options.emptyLabel || 'Choose an output…', '', false, !records.some(option => option.selected)),
    ...records.map(record => {
      const option = new Option(record.label, record.key, false, record.selected);
      option.disabled = record.disabled;
      return option;
    }));
}
