import { inputCanBeReferenced, inputChoices, inputReferenceKey, readInputReference, referenceFeature } from './feature-inputs.js';

export function relationshipInputOptions(state, requirement) {
  return {allowPending: Object.values(state?.relationship_contracts || {})
    .some(signature => Object.values(signature.roles).includes(requirement))};
}

export function relationshipPropertySummary(state, output) {
  return [...new Set((output.requirements || []).map(requirement => state.input_contracts?.[requirement]?.property)
    .filter(Boolean))].map(property => property.replaceAll('_', ' ')).join(', ');
}

export function relationshipPropertyAdjustable(output, property) {
  const reference = output.reference || {}, descriptor = output.properties?.[property];
  return (reference.context || reference.feature) === reference.feature && descriptor?.binding_eligible &&
    descriptor.uses?.includes('binding') && !!(descriptor.parameter_owner || descriptor.binding_aliases?.length);
}

// Collections retain their authored owner reference. Expansion is only for
// validation, inspection and geometry; it must never become a saved snapshot.
export function relationshipCollectionMembers(state, output) {
  if (!output?.collection) return [];
  const catalogue = new Map();
  for (const record of [...(state.input_catalogue || []),
    ...Object.values(state.input_requirements || {}).flatMap(value => [...(value.choices || []), ...(value.unavailable || [])])]) {
    const key = inputReferenceKey(record.reference);
    if (!catalogue.has(key)) catalogue.set(key, record);
  }
  return (output.collection.members || []).map(reference => {
    const key = inputReferenceKey(reference), member = catalogue.get(key);
    return member ? {...member, compatible: member.compatible ?? output.compatible,
      requirements: member.requirements || output.requirements || Object.keys(state.input_contracts || {}).filter(requirement => {
        const contract = state.input_contracts[requirement];
        return contract.allow_collections && contract.property === output.collection.property;
      }), key, id: key} : {reference, key, id: key, compatible: false,
      availability: 'unavailable', label: 'Unavailable collection member', reason: 'Reevaluate or repair the collection provider'};
  });
}

export function relationshipExpandedOutputs(state, outputs) {
  return [...outputs].flatMap(output => output.collection
    ? relationshipCollectionMembers(state, output).map(member => ({...member,
      collectionKey: output.key || inputReferenceKey(output.reference), collectionReference: output.reference,
      collectionLabel: output.label}))
    : [{...output, key: output.key || inputReferenceKey(output.reference)}]);
}

export function relationshipSelectionCounts(state, outputs) {
  const authored = [...outputs], expanded = relationshipExpandedOutputs(state, authored),
    collections = authored.filter(output => output.collection);
  return {authored: authored.length, expanded: expanded.length, unique: new Set(expanded.map(output => output.key)).size,
    collections: collections.length,
    source_count: collections.reduce((sum, output) => sum + (output.collection.source_count || 0), 0),
    adjustable_count: collections.reduce((sum, output) => sum + (output.collection.adjustable_count || 0), 0)};
}

export function relationshipParticipantPresentation(state, output) {
  if (output.collection) {
    const collection = output.collection, members = relationshipCollectionMembers(state, output),
      count = members.length, sources = collection.source_count || 0, copies = collection.adjustable_count || 0,
      name = output.label || output.feature_label || 'Direction collection',
      summary = `${count} ${collection.property === 'direction' ? 'axes' : 'outputs'} · ${sources} upstream source${sources === 1 ? '' : 's'} + ${copies} ${copies === 1 ? 'copy' : 'copies'}`,
      group = copies ? 'constrain' : 'reference', groupLabel = copies ? 'Features to constrain' : 'Results to reference',
      detail = [summary, output.availability === 'ready' ? '' : 'Needs evaluation',
        output.compatible ? '' : output.reason || 'Incompatible'].filter(Boolean).join(' · ');
    return {name, accessibleName: name, group, groupLabel, roleLabel: 'Live collection',
      contextLabel: output.producing_context?.label || output.feature_label || 'Feature reuse', detail,
      choiceLabel: `${name} · ${summary}`, members: members.map(member => member.label || member.reference.feature)};
  }
  const reference = output.reference || {}, direct = reference.context === reference.feature,
    requirements = (output.requirements || []).map(requirement => state.input_contracts?.[requirement] || {}),
    bindings = requirements.filter(contract => {
      const property = output.properties?.[contract.property];
      return direct && (contract.uses || ['geometry']).includes('binding') &&
        (property ? relationshipPropertyAdjustable(output, contract.property)
          : !contract.property && output.parameter_owner && output.uses?.includes('binding'));
    }),
    adjustable = output.relationship_use !== 'geometry' && bindings.length > 0, group = adjustable ? 'constrain' : 'reference',
    groupLabel = adjustable ? 'Features to constrain' : 'Results to reference',
    roleLabel = adjustable ? 'Adjustable property' : 'Fixed for this relationship',
    producer = output.producing_context,
    contextLabel = producer?.label || state.recipe?.nodes?.find(node => node.id === reference.context)?.label ||
      (direct ? 'Feature output' : 'Published output'),
    name = output.feature_label ? `${output.feature_label} — ${(reference.output || '').replaceAll('_', ' ')}` : output.label,
    pending = output.availability !== 'ready' || state.states?.[reference.context] && state.states[reference.context] !== 'ready',
    detail = [relationshipPropertySummary(state, output), roleLabel,
      output.ownership === 'relationship' ? 'Common relationship property' : '', contextLabel,
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

export function relationshipDefinitionForNode(state, node) {
  const definitions = relationshipDefinitions(state).filter(definition => definition.operation === node?.operation);
  if (definitions.length === 1) return definitions[0];
  // Creation defaults (for example symmetric extents) are editable values, not
  // relationship identities. Only values distinguishing sibling signatures are
  // discriminators, such as Parallel versus Coincident plane relationships.
  const fields = [...new Set(definitions.flatMap(definition => Object.keys(definition.node_values || {})))]
    .filter(field => new Set(definitions.map(definition => JSON.stringify(definition.node_values?.[field]))).size > 1),
    matching = definitions.filter(definition => fields.every(field =>
      JSON.stringify(node[field]) === JSON.stringify(definition.node_values?.[field])));
  return matching.length === 1 ? matching[0] : null;
}

// Reverse the common serializer without guessing a named publication from a
// retained feature identity. Unavailable saved values stay removable in the UI.
export function relationshipDraftForNode(state, node) {
  const definition = relationshipDefinitionForNode(state, node);
  if (!definition) throw new Error('This saved relationship is not supported by the current runtime.');
  const roles = Object.fromEntries(Object.keys(definition.roles).map(role => [role, new Set()]));
  const fields = Object.entries(definition.node_fields || {});
  if (!fields.length && definition.operation === 'equal') {
    const radius = [node.left, node.right].find(value => value?.measurement === 'radius'),
      plane = [node.left, node.right].find(value => value?.measurement === 'plane_distance');
    for (const [role, value] of [['radius', radius?.surface], ['plane', plane?.surface], ['datum', plane?.reference_plane]]) {
      if (!value) continue;
      const candidates = relationshipRoleChoices(state, definition.id, role)
        .filter(output => output.reference.feature === value && output.reference.context === value);
      roles[role].add(candidates.length === 1 ? candidates[0].key : inputReferenceKey(value));
    }
    return {roles, unassigned: new Set()};
  }
  for (const [role, field] of fields) {
    const values = field.multiple ? node[field.name] || [] : node[field.name] == null ? [] : [node[field.name]];
    for (const value of values) {
      const parsed = readInputReference(value);
      if (field.encoding === 'reference' && typeof parsed !== 'string') roles[role].add(inputReferenceKey(parsed));
      else {
        const feature = referenceFeature(parsed), candidates = relationshipRoleChoices(state, definition.id, role)
          .filter(output => output.reference.feature === feature && (output.reference.context || feature) === feature);
        roles[role].add(candidates.length === 1 ? candidates[0].key : inputReferenceKey(value));
      }
    }
  }
  const roleUses = Object.fromEntries(fields.filter(([, field]) => field.use_field)
    .map(([role, field]) => [role, node[field.use_field] ?? field.use_default]));
  return {roles, unassigned: new Set(), ...(Object.keys(roleUses).length ? {roleUses} : {})};
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
    requirements = [...new Set(signatures.flatMap(signature => Object.values(signature.roles)))];
  return relationshipChoicesForRequirements(state, requirements, selected, nodes);
}

function relationshipChoicesForRequirements(state, requirements, selected, nodes, use) {
  const records = new Map();
  for (const requirement of requirements) for (const output of inputChoices(state, requirement, nodes, {use})) {
    const key = inputReferenceKey(output.reference), previous = records.get(key),
      contract = state.input_contracts?.[requirement],
      compatible = inputCanBeReferenced(output, {allowPending: true}) && (!output.collection ||
        contract?.allow_collections === true && output.collection.property === contract.property &&
        output.collection.members?.length > 0 && output.collection.count === output.collection.members.length);
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

export function relationshipRoleUse(state, kind, role, draft = {}) {
  const field = state?.relationship_contracts?.[kind]?.node_fields?.[role];
  return field?.use_field ? draft.roleUses?.[role] ?? field.use_default : undefined;
}

export function renderRelationshipRoleUse(control, state, kind, role, value) {
  const field = state?.relationship_contracts?.[kind]?.node_fields?.[role];
  control.replaceChildren(...(field?.use_choices || []).map(choice =>
    new Option(choice.label, choice.value, false, choice.value === value)));
  if (value && ![...control.options].some(option => option.value === value))
    control.append(new Option('Unavailable ownership choice', value, false, true));
  control.value = value || field?.use_default || '';
}

export function relationshipRoleChoices(state, kind, role, selected = new Set(), nodes = state?.recipe?.nodes || [], use) {
  const requirement = state?.relationship_contracts?.[kind]?.roles?.[role];
  if (!requirement) return [];
  const field = state.relationship_contracts[kind].node_fields?.[role],
    requestedUse = use ?? (field?.use_field ? field.use_default : undefined),
    property = state.input_contracts[requirement]?.property,
    publications = requestedUse === 'geometry' ? new Map((state.input_catalogue || [])
      .map(output => [inputReferenceKey(output.reference), output])) : null;
  return relationshipChoicesForRequirements(state, [requirement], selected, nodes, requestedUse).flatMap(output => {
    if (!requestedUse) return [output];
    if (requestedUse === 'geometry') {
      // Corrective binding eligibility is independent of a failed/stale result.
      // Fixed reads use the actual publication's availability, never that upgrade.
      const publication = publications.get(output.key), context = output.reference.context || output.reference.feature;
      const availability = publication?.availability || state.states?.[context] || output.availability;
      output = {...output, availability, reason: publication?.reason ||
        (availability === 'ready' ? null : state.errors?.[context] || output.reason)};
      output.compatible &&= inputCanBeReferenced(output, {allowPending: true});
    }
    const compatibleUse = (output.uses || ['geometry']).includes(requestedUse) &&
      (requestedUse !== 'binding' || relationshipPropertyAdjustable(output, property));
    if (!compatibleUse && !selected.has(output.key)) return [];
    return [{...output, relationship_use: requestedUse, compatible: output.compatible && !!compatibleUse,
      ...(!compatibleUse ? {reason: requestedUse === 'binding' ? 'Choose an adjustable property owner.'
        : 'Choose an exact fixed geometry result.'} : {})}];
  });
}

export function relationshipRoleSelection(state, kind, selected, previous = {}) {
  const roles = Object.fromEntries(Object.keys(state?.relationship_contracts?.[kind]?.roles || {})
    .map(role => [role, new Set()])), unassigned = new Set();
  const choices = Object.fromEntries(Object.keys(roles).map(role => [role,
    new Set(relationshipRoleChoices(state, kind, role).map(output => output.key))]));
  for (const key of selected) {
    const compatible = Object.keys(roles).filter(role => choices[role].has(key)),
      retained = compatible.filter(role => previous[role]?.has(key));
    const automatic = compatible.filter(role =>
      state.input_contracts[state.relationship_contracts[kind].roles[role]].min_count !== 0),
      role = retained.length === 1 ? retained[0] : automatic.length === 1 ? automatic[0] : null;
    if (role) roles[role].add(key);
    else unassigned.add(key);
  }
  for (const [role, values] of Object.entries(roles)) {
    const requirement = state.relationship_contracts[kind].roles[role];
    if (state.input_contracts[requirement].max_count === 1 && values.size > 1) {
      for (const key of values) unassigned.add(key);
      values.clear();
    }
  }
  return {roles, unassigned};
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
  const explicit = !(selected instanceof Set), assigned = explicit ? selected.roles || selected : null,
    keys = explicit ? Object.values(assigned).flatMap(values => [...values]) : [...selected],
    union = new Set(keys), roles = {};
  if (explicit && selected.unassigned?.size)
    return {valid: false, reason: Object.keys(signature.roles).length === 1
      ? 'Remove or replace unavailable or incompatible outputs.'
      : 'Assign the remaining participants to a compatible role, or remove them.', roles};
  if (explicit && Object.keys(assigned).some(role => !(role in signature.roles)))
    return {valid: false, reason: 'Remove participants from unknown relationship roles.', roles};
  if (keys.length !== union.size)
    return {valid: false, reason: 'Choose distinct output references; an output cannot fill more than one role.', roles};
  const outputs = relationshipParticipantChoices(state, kind, union, nodes)
      .filter(output => union.has(output.key));
  if (outputs.some(output => !output.compatible))
    return { valid: false, reason: 'Remove or replace unavailable or incompatible outputs.' };
  for (const [role, requirement] of Object.entries(signature.roles)) {
    const use = relationshipRoleUse(state, kind, role, explicit ? selected : {}), field = signature.node_fields?.[role];
    if (field?.use_field && !(field.use_choices || []).some(choice => choice.value === use))
      return {valid: false, reason: 'Choose a supported reference ownership.', roles};
    const contract = state.input_contracts[requirement],
      selectedKeys = explicit ? new Set(assigned[role] || []) : union,
      members = explicit ? relationshipRoleChoices(state, kind, role, selectedKeys, nodes, use)
        .filter(output => selectedKeys.has(output.key) && output.compatible)
        : contract.min_count === 0 ? [] : outputs.filter(output => output.requirements.includes(requirement));
    roles[role] = members;
    if (explicit && members.length !== selectedKeys.size)
      return {valid: false, reason: `${signature.role_labels?.[role] || role}: remove or replace incompatible outputs.`, roles};
    if (members.length < contract.min_count || members.length > (contract.max_count ?? Infinity))
      return { valid: false, reason: explicit
        ? `${signature.role_labels?.[role] || role}: ${members.length} selected; choose ${contract.max_count === contract.min_count
          ? contract.min_count : contract.max_count ? `${contract.min_count}–${contract.max_count}` : `at least ${contract.min_count}`} compatible outputs.`
        : `Choose ${contract.max_count === contract.min_count ? contract.min_count : 'at least ' + contract.min_count} compatible ${role} outputs.`, roles };
  }
  if (new Set(Object.values(roles).flat().map(output => output.key)).size !== union.size)
    return { valid: false, reason: 'Every participant must satisfy a relationship input role.', roles };
  const expandedRoles = Object.fromEntries(Object.entries(roles).map(([role, members]) =>
    [role, relationshipExpandedOutputs(state, members)])), seen = new Map();
  for (const [role, members] of Object.entries(expandedRoles)) for (const member of members) {
    if (member.collectionKey && (!inputCanBeReferenced(member, {allowPending: true}) || member.collection))
      return {valid: false, reason: 'Remove or repair unavailable collection members.', roles};
    const previousMembers = seen.get(member.key) || [];
    for (const previous of previousMembers) {
      const referenceRole = signature.reference_role || 'reference',
        collectionMember = member.collectionKey ? member : previous.member,
        otherRole = member.collectionKey ? previous.role : role,
        owner = Object.values(roles).flat().find(output => output.key === collectionMember.collectionKey),
        property = collectionMember.properties?.[owner?.collection?.property],
        fixedSource = owner && collectionMember.reference.feature === owner.collection.template &&
          (collectionMember.reference.context || collectionMember.reference.feature) !== collectionMember.reference.feature &&
          property?.binding_eligible === false;
      if (!(otherRole === referenceRole && previous.role !== role && fixedSource))
        return {valid: false, reason: 'Choose distinct expanded outputs; selected targets overlap.', roles};
    }
    seen.set(member.key, [...previousMembers, {role, member}]);
  }
  if (seen.size < (signature.expanded_min_count ?? 0) || seen.size > (signature.expanded_max_count ?? Infinity))
    return {valid: false, reason: `Choose ${signature.expanded_min_count ?? 0}–${signature.expanded_max_count ?? 'any number of'} distinct directions; ${seen.size} selected.`, roles};
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
  if (signature.semantic_check === 'compatible_point' && roles.members.some(output =>
    nodes.find(node => node.id === output.reference.feature)?.point &&
    nodes.find(node => node.id === output.reference.feature).point !== roles.point[0].reference.feature))
    return {valid: false, reason: 'A member already uses another center point. Remove its binding or choose that point; fit types and bindings are not changed automatically.', roles};
  if (signature.semantic_check === 'compatible_point' && nodes.some(node => node.operation === 'shared_center_group' &&
    node.point !== roles.point[0].reference.feature && roles.members.some(output => node.surfaces.includes(output.reference.feature))))
    return {valid: false, reason: 'A member belongs to another Shared center relationship using a different point. Edit that relationship or choose the same point.', roles};
  return { valid: true, roles, expandedRoles };
}

export function relationshipNodeInputs(state, kind, selected, nodes = state?.recipe?.nodes || []) {
  const validity = relationshipValidity(state, kind, selected, nodes);
  if (!validity.valid) throw new Error(validity.reason);
  const signature = state.relationship_contracts[kind], feature = output => output.reference.feature;
  if (Object.keys(signature.node_fields || {}).length) return {
    operation: signature.operation, ...signature.node_values,
    ...Object.fromEntries(Object.entries(signature.node_fields).map(([role, field]) => {
      const values = validity.roles[role].map(output => field.encoding === 'reference' ? output.reference : feature(output));
      return [field.name, field.multiple ? values : values[0] ?? (field.optional ? null : undefined)];
    })),
    ...Object.fromEntries(Object.entries(signature.node_fields).filter(([, field]) => field.use_field)
      .map(([role, field]) => [field.use_field, relationshipRoleUse(state, kind, role, selected)])),
  };
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
  if (!output || output.collection || output.availability !== 'ready' || !inputCanBeReferenced(output)) return null;
  const reference = output?.reference;
  if (!reference || typeof reference === 'string') return null;
  const context = reference.context || reference.feature;
  if (state.states?.[context] && state.states[context] !== 'ready') return null;
  let value = state.derived?.[context];
  if (output.ownership === 'relationship') return output.preview ||
    (context === reference.feature ? value?.common_outputs?.[reference.output] : null) || null;
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
