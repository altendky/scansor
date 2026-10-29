import { inputCanBeReferenced, inputChoices, inputReferenceKey, readInputReference, referenceFeature } from './feature-inputs.js';

export function relationshipInputOptions(state, requirement) {
  return {allowPending: Object.values(state?.relationship_contracts || {})
    .some(signature => Object.values(signature.roles).includes(requirement))};
}

export function relationshipPropertySummary(state, output) {
  return [...new Set((output.requirements || []).map(requirement => state.input_contracts?.[requirement]?.property)
    .filter(Boolean))].map(property => property.replaceAll('_', ' ')).join(', ');
}

export function relationshipParticipantPresentation(state, output) {
  const reference = output.reference || {}, direct = reference.context === reference.feature,
    requirements = (output.requirements || []).map(requirement => state.input_contracts?.[requirement] || {}),
    bindings = requirements.filter(contract => {
      const property = output.properties?.[contract.property];
      return direct && (contract.uses || ['geometry']).includes('binding') &&
        (property ? property.binding_eligible && property.parameter_owner && property.uses?.includes('binding')
          : !contract.property && output.parameter_owner && output.uses?.includes('binding'));
    }),
    adjustable = bindings.length > 0, group = adjustable ? 'constrain' : 'reference',
    groupLabel = adjustable ? 'Features to constrain' : 'Results to reference',
    roleLabel = adjustable ? 'Adjustable property' : 'Fixed for this relationship',
    producer = output.producing_context,
    contextLabel = producer?.label || state.recipe?.nodes?.find(node => node.id === reference.context)?.label ||
      (direct ? 'Feature output' : 'Published output'),
    name = output.feature_label ? `${output.feature_label} — ${(reference.output || '').replaceAll('_', ' ')}` : output.label,
    pending = output.availability !== 'ready' || state.states?.[reference.context] && state.states[reference.context] !== 'ready',
    detail = [relationshipPropertySummary(state, output), roleLabel, contextLabel,
      adjustable ? '' : 'Provider updates still apply',
      pending ? 'Needs evaluation' : '', output.compatible ? '' : output.reason || 'Incompatible'].filter(Boolean).join(' · ');
  return {name, accessibleName: output.label, group, groupLabel, roleLabel, contextLabel, detail,
    choiceLabel: `${name} · ${roleLabel} · ${contextLabel}`};
}

export function directionAlignmentLabel(outputs) {
  const projections = outputs.map(output => output?.properties?.direction?.projection);
  if (projections.length !== 2) return 'Aligned directions';
  if (projections.every(value => value === 'plane_normal')) return 'Parallel plane normals';
  if (projections.every(value => value === 'axis_direction')) return 'Parallel axis directions';
  if (projections.includes('plane_normal') && projections.includes('axis_direction')) return 'Axis perpendicular to plane';
  return 'Aligned directions';
}

export function relationshipDefinitions(state) {
  return Object.entries(state?.relationship_contracts || {}).map(([id, signature]) => ({
    id, ...signature, baseName: signature.base_name,
  })).sort((a, b) => Number(a.operation === 'direction_alignment') - Number(b.operation === 'direction_alignment'));
}

export function isDirectionJoint(node, nodes) {
  return node?.operation === 'joint_fit' && node.constraints.some(id =>
    nodes.find(candidate => candidate.id === id)?.operation === 'direction_alignment');
}

export function directionJointInputs(state, nodes = state?.recipe?.nodes || []) {
  const signatures = Object.values(state?.relationship_contracts || {}).filter(signature => signature.joint_modes?.includes('direction'));
  const operations = new Set(signatures.map(signature => signature.operation));
  const properties = new Set(signatures.flatMap(signature => Object.values(signature.roles))
    .map(requirement => state.input_contracts?.[requirement]?.property).filter(Boolean));
  const owners = new Set((state?.input_catalogue || []).filter(output =>
    output.reference.context === output.reference.feature && [...properties].some(property =>
      output.properties?.[property]?.binding_eligible)).map(output => output.reference.feature));
  return nodes.filter(node => operations.has(node.operation) || node.operation === 'fit' && owners.has(node.id));
}

export function directionJointValidity(state, ids, nodes = state?.recipe?.nodes || []) {
  const allowed = new Set(directionJointInputs(state, nodes).map(node => node.id));
  if (!ids.length || ids.some(id => !allowed.has(id)))
    return {valid: false, reason: 'Choose compatible earlier fits and relationships for this joint.'};
  if (!ids.some(id => nodes.find(node => node.id === id)?.operation === 'direction_alignment'))
    return {valid: false, reason: 'Include an aligned-directions relationship. Its observed surfaces contribute once.'};
  return {valid: true};
}

export function relationshipParticipantChoices(state, kind, selected = new Set(), nodes = state?.recipe?.nodes || []) {
  const signatures = kind ? [state?.relationship_contracts?.[kind]].filter(Boolean)
      : Object.values(state?.relationship_contracts || {}),
    requirements = [...new Set(signatures.flatMap(signature => Object.values(signature.roles)))],
    records = new Map();
  for (const requirement of requirements) for (const output of inputChoices(state, requirement, nodes)) {
    const key = inputReferenceKey(output.reference), previous = records.get(key),
      compatible = inputCanBeReferenced(output, {allowPending: true});
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
    const matching = choices.filter(choice => choice.reference.feature === id),
      output = matching.find(choice => choice.parameter_owner) ||
        (matching.length === 1 ? matching[0] : null) ||
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
  if (signature.semantic_check === 'compatible_axis' && roles.members.some(output =>
    output.axis && output.axis !== roles.axis[0].reference.feature))
    return { valid: false, reason: 'A member already uses another axis. Remove its binding or choose that axis; fit types and bindings are not changed automatically.', roles };
  if (signature.semantic_check === 'compatible_axis' && nodes.some(node => node.operation === 'coaxial_group' &&
    node.axis !== roles.axis[0].reference.feature && roles.members.some(output => node.surfaces.includes(output.reference.feature))))
    return { valid: false, reason: 'A member belongs to another Coaxial relationship using a different axis. Edit that relationship or choose the same axis.', roles };
  return { valid: true, roles };
}

export function relationshipNodeInputs(state, kind, selected, nodes = state?.recipe?.nodes || []) {
  const validity = relationshipValidity(state, kind, selected, nodes);
  if (!validity.valid) throw new Error(validity.reason);
  const signature = state.relationship_contracts[kind], feature = output => output.reference.feature;
  if (signature.operation === 'direction_alignment') return {
    operation: signature.operation, directions: validity.roles.participants.map(output => output.reference), sense: 'unsigned',
  };
  if (signature.operation === 'coaxial_group') return {
    operation: signature.operation, axis: feature(validity.roles.axis[0]),
    surfaces: validity.roles.members.map(feature),
  };
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
  if (!output || output.availability !== 'ready' || !inputCanBeReferenced(output)) return null;
  const reference = output?.reference;
  if (!reference || typeof reference === 'string') return null;
  const context = reference.context || reference.feature;
  if (state.states?.[context] && state.states[context] !== 'ready') return null;
  let value = state.derived?.[context];
  if (context !== reference.feature) value = (value?.resolved || value?.surfaces || value?.outputs)?.[reference.feature];
  return value || output.preview || null;
}

export function axisFactorMembers(node, nodes) {
  const byId = new Map(nodes.map(candidate => [candidate.id, candidate]));
  if (node?.operation === 'fit') return [node];
  const references = ['coaxial_group', 'mirror_symmetry', 'plane_relationship'].includes(node?.operation)
    ? node.surfaces : node?.operation === 'parallel' ? [node.surface]
      : node?.operation === 'equal' ? [node.left.surface, node.right.surface] : [];
  return [...new Set((references || []).filter(reference => typeof reference === 'string' ||
    !reference.context || reference.context === reference.feature).map(referenceFeature))]
    .map(id => byId.get(id)).filter(member => member?.operation === 'fit');
}

export function axisFactorAxis(node, nodes) {
  const byId = new Map(nodes.map(candidate => [candidate.id, candidate])),
    memberAxis = member => member.axis || byId.get(member.reference_plane)?.axis;
  if (node?.operation === 'coaxial_group') return node.axis;
  if (node?.operation === 'fit') {
    const axes = new Set([memberAxis(node), ...nodes.filter(candidate => candidate.operation === 'coaxial_group' &&
      candidate.surfaces.includes(node.id)).map(candidate => candidate.axis)].filter(Boolean));
    return axes.size === 1 ? [...axes][0] : null;
  }
  if (node?.operation === 'mirror_symmetry') return byId.get(node.plane)?.axis;
  if (node?.operation === 'parallel') return byId.get(node.reference_plane)?.axis;
  if (node?.operation === 'equal') {
    const distance = [node.left, node.right].find(value => value.measurement === 'plane_distance');
    return byId.get(distance?.reference_plane)?.axis;
  }
  if (node?.operation === 'plane_relationship') {
    if (node.surfaces.some(reference => typeof reference !== 'string' &&
      reference.context && reference.context !== reference.feature)) return null;
    const axes = new Set((node.surfaces || []).filter(reference => typeof reference === 'string' ||
      !reference.context || reference.context === reference.feature)
      .map(reference => byId.get(referenceFeature(reference))).filter(Boolean).map(memberAxis).filter(Boolean));
    return axes.size === 1 ? [...axes][0] : null;
  }
  return null;
}

export function axisSolveInputs(axis, nodes) {
  return axis ? nodes.filter(node => axisFactorAxis(node, nodes) === axis) : [];
}

export function axisSolveValidity(axis, factorIds, nodes) {
  const byId = new Map(nodes.map(node => [node.id, node])),
    factors = factorIds.map(id => byId.get(id));
  if (!axis || !factors.length || factors.some(node => !node || axisFactorAxis(node, nodes) !== axis))
    return { valid: false, reason: 'Choose an axis and compatible fits or relationships on that axis.' };
  const members = [...new Map(factors.flatMap(node => axisFactorMembers(node, nodes)).map(member => [member.id, member])).values()],
    lateral = members.filter(member => ['cone', 'cylinder'].includes(member.kind)),
    plane = members.some(member => member.kind === 'plane') || factors.some(node => node.operation === 'mirror_symmetry');
  if (members.length < 2 || !lateral.length || !(lateral.length >= 2 || plane))
    return { valid: false, reason: 'Choose at least two distinct fitted surfaces: two coaxial cone/cylinder fits, or one with plane evidence. A Coaxial relationship supplies its member fits.' };
  return { valid: true, members };
}
