import { inputChoices, inputReferenceKey, readInputReference, referenceFeature } from './feature-inputs.js';

export function relationshipDefinitions(state) {
  return Object.entries(state?.relationship_contracts || {}).map(([id, signature]) => ({
    id, ...signature, baseName: signature.base_name,
  }));
}

export function relationshipParticipantChoices(state, kind, selected = new Set(), nodes = state?.recipe?.nodes || []) {
  const signatures = kind ? [state?.relationship_contracts?.[kind]].filter(Boolean)
      : Object.values(state?.relationship_contracts || {}),
    requirements = [...new Set(signatures.flatMap(signature => Object.values(signature.roles)))],
    records = new Map();
  for (const requirement of requirements) for (const output of inputChoices(state, requirement, nodes)) {
    const key = inputReferenceKey(output.reference), previous = records.get(key),
      compatible = output.compatible !== false && !output.reason && output.availability === 'ready';
    if (!previous || (!previous.compatible && compatible)) records.set(key, {
      ...output, key, id: key, compatible, requirements: previous?.requirements || [],
    });
    if (compatible) records.get(key).requirements.push(requirement);
  }
  // A changed/removed publication remains removable instead of silently changing
  // to another context or disappearing from an open relationship draft.
  for (const key of selected) if (!records.has(key)) records.set(key, {
    key, id: key, reference: readInputReference(key), compatible: false, requirements: [],
    label: 'Unavailable saved output', reason: 'Choose a compatible output or remove this participant',
  });
  return [...records.values()].filter(output => output.compatible || selected.has(output.key));
}

export function relationshipSelectionForFeatures(state, kind, featureIds) {
  const choices = relationshipParticipantChoices(state, kind),
    fallback = kind ? relationshipParticipantChoices(state, null) : choices, selected = new Set();
  for (const id of featureIds) {
    const output = choices.find(choice => choice.parameter_owner && choice.reference.feature === id) ||
      fallback.find(choice => choice.parameter_owner && choice.reference.feature === id);
    if (output) selected.add(output.key);
  }
  return selected;
}

export function relationshipValidity(state, kind, selected, nodes = state?.recipe?.nodes || []) {
  const signature = state?.relationship_contracts?.[kind];
  if (!signature) return { valid: false, reason: 'Choose a relationship.' };
  const outputs = relationshipParticipantChoices(state, kind, selected, nodes)
      .filter(output => selected.has(output.key)), roles = {};
  if (outputs.some(output => !output.compatible))
    return { valid: false, reason: 'Remove or replace unavailable or incompatible outputs.' };
  for (const [role, requirement] of Object.entries(signature.roles)) {
    const contract = state.input_contracts[requirement],
      members = outputs.filter(output => output.requirements.includes(requirement));
    roles[role] = members;
    if (members.length < contract.min_count || members.length > (contract.max_count ?? Infinity))
      return { valid: false, reason: `Choose ${contract.max_count === contract.min_count ? contract.min_count : 'at least ' + contract.min_count} compatible ${role} outputs.`, roles };
  }
  if (new Set(Object.values(roles).flat().map(output => output.key)).size !== selected.size)
    return { valid: false, reason: 'Every participant must satisfy a relationship input role.', roles };
  if (signature.semantic_check === 'same_geometry_kind' &&
      new Set(roles.members.map(output => output.geometry_kind)).size !== 1)
    return { valid: false, reason: 'Mirror members must have the same geometry kind.', roles };
  if (signature.semantic_check === 'same_axis' && roles.radius[0]?.axis !== roles.datum[0]?.axis)
    return { valid: false, reason: 'The radius and reference plane must use the same axis.', roles };
  return { valid: true, roles };
}

export function relationshipNodeInputs(state, kind, selected) {
  const validity = relationshipValidity(state, kind, selected);
  if (!validity.valid) throw new Error(validity.reason);
  const signature = state.relationship_contracts[kind], feature = output => output.reference.feature;
  if (signature.operation === 'plane_relationship') return {
    operation: signature.operation, relation: signature.relation,
    surfaces: validity.roles.participants.map(output => output.reference),
  };
  if (signature.operation === 'equal_radii') return {
    operation: signature.operation, surfaces: validity.roles.participants.map(feature),
  };
  if (signature.operation === 'mirror_symmetry') return {
    operation: signature.operation, plane: feature(validity.roles.datum[0]),
    surfaces: validity.roles.members.map(feature), symmetric_extents: true,
  };
  return { operation: 'equal', left: { measurement: 'radius', surface: feature(validity.roles.radius[0]) },
    right: { measurement: 'plane_distance', surface: feature(validity.roles.plane[0]),
      reference_plane: feature(validity.roles.datum[0]) } };
}

export function relationshipFeatureIds(selected) {
  return [...new Set([...selected].map(key => referenceFeature(readInputReference(key))).filter(Boolean))];
}

export function relationshipOutputValue(state, output) {
  const reference = output?.reference;
  if (!reference || typeof reference === 'string') return null;
  const context = reference.context || reference.feature;
  let value = state.derived?.[context];
  if (context !== reference.feature) value = (value?.resolved || value?.surfaces || value?.outputs)?.[reference.feature];
  return value || output.preview || null;
}
