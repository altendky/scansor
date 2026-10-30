import {inputReferenceKey, readInputReference, referenceFeature} from './feature-inputs.js';
import {relationshipDefinitions, relationshipRoleChoices, relationshipParticipantChoices,
  relationshipRoleSelection, relationshipSelectionForFeatures, relationshipValidity, relationshipNodeInputs,
  relationshipFeatureIds, relationshipExpandedOutputs, relationshipCollectionMembers,
  relationshipParticipantPresentation, relationshipSelectionCounts, relationshipOutputValue,
  directionAlignmentLabel, relationshipRoleUse, renderRelationshipRoleUse} from './relationship-inputs.js';
import {createPickField} from './model-picking.js';
import {renderRelationshipParticipants} from './action-tree.js';

const copyDraft = draft => ({roles: Object.fromEntries(Object.entries(draft?.roles || {})
  .map(([role, keys]) => [role, new Set(keys)])), unassigned: new Set(draft?.unassigned || []),
  ...(draft?.roleUses ? {roleUses: {...draft.roleUses}} : {})});

// State and serialization are shared by standalone relationships and any editor
// that supplies an additional, structurally declared feature as its anchor.
export function createRelationshipDraft({getState, getNodes, anchorFeature = null,
  makeLabel = definition => definition?.baseName || 'Relationship'}) {
  let kind = null, draft = {roles: {}, unassigned: new Set()}, label = '', activeRole = 'participants', anchorPreference = null;
  const state = () => getState(), nodes = () => getNodes?.() || state().recipe.nodes,
    anchor = () => {const value = typeof anchorFeature === 'function' ? anchorFeature() : anchorFeature; return value?.id || value;},
    selected = () => new Set([...Object.values(draft.roles).flatMap(keys => [...keys]), ...draft.unassigned]),
    definition = () => relationshipDefinitions(state()).find(item => item.id === kind),
    usesRoles = () => !!definition() && definition().selection_pattern !== 'peers' && Object.keys(definition().roles).length > 1;
  function anchorChoice() {
    const id = anchor(), signature = definition();
    if (!id || !signature) return null;
    const candidates = Object.keys(signature.roles).flatMap(role => relationshipRoleChoices(state(), kind, role, new Set(), nodes(),
      relationshipRoleUse(state(), kind, role, draft))
      .filter(output => output.reference.feature === id && (output.reference.context || id) === id)
      .map(output => ({role, output})));
    // An existing fit may itself be the reference. Opening its properties must
    // not silently turn that fixed input into an adjustable target.
    if (anchorPreference) return candidates.find(candidate => candidate.role === anchorPreference.role &&
      candidate.output.key === anchorPreference.key) || candidates.find(candidate => candidate.role === anchorPreference.role) || null;
    return candidates.find(candidate => candidate.role !== signature.reference_role) || candidates[0] || null;
  }
  function pin() {
    const id = anchor(), chosen = anchorChoice();
    if (!chosen) return;
    for (const keys of [...Object.values(draft.roles), draft.unassigned]) for (const key of keys) {
      const reference = readInputReference(key);
      if (referenceFeature(reference) === id && (typeof reference === 'string' ||
          (reference.context || id) === id)) keys.delete(key);
    }
    (draft.roles[chosen.role] ||= new Set()).add(chosen.output.key);
  }
  function choices(role = 'participants') {
    const keys = draft.roles[role] || new Set(), explicit = usesRoles(),
      outputs = explicit ? relationshipRoleChoices(state(), kind, role, keys, nodes(), relationshipRoleUse(state(), kind, role, draft))
        : relationshipParticipantChoices(state(), kind, selected(), nodes());
    const pinned = anchorChoice(), pinnedSingle = explicit && pinned?.role === role &&
      state().input_contracts[definition().roles[role]].max_count === 1;
    return outputs.map(output => {
      if (pinnedSingle && output.key !== pinned.output.key)
        return {...output, compatible: false, reason: 'This fit is pinned to this relationship role. Edit the relationship separately to change its reference.'};
      const other = explicit && Object.entries(draft.roles).find(([name, values]) => name !== role && values.has(output.key));
      return other ? {...output, compatible: false, reason: `Already selected for ${definition().role_labels?.[other[0]] || other[0]}`} : output;
    });
  }
  function validity() {
    const chosen = anchorChoice();
    if (anchor() && kind && (!chosen || !draft.roles[chosen.role]?.has(chosen.output.key)))
      return {valid: false, reason: 'This relationship does not accept this fit’s declared output. Choose a compatible relationship or remove it.'};
    return relationshipValidity(state(), kind, draft, nodes());
  }
  const api = {
    reset({kind: nextKind = null, selectedFeatures = [], selected: keys, draft: nextDraft, label: nextLabel} = {}) {
      kind = nextKind;
      draft = nextDraft ? copyDraft(nextDraft) : relationshipRoleSelection(state(), kind,
        keys || relationshipSelectionForFeatures(state(), kind, selectedFeatures));
      anchorPreference = null;
      if (nextDraft && anchor()) for (const [role, values] of Object.entries(draft.roles)) {
        const key = [...values].find(key => {
          const value = readInputReference(key), id = anchor();
          return referenceFeature(value) === id && (typeof value === 'string' || (value.context || id) === id);
        });
        if (key) {anchorPreference = {role, key}; break;}
      }
      label = nextLabel ?? makeLabel(definition() || {}); pin();
      activeRole = 'participants';
      return api.snapshot();
    },
    refresh() { pin(); return api.snapshot(); },
    setKind(value) {
      if ((value || null) === kind) return;
      const previous = draft.roles, keys = selected(), directFeatures = relationshipFeatureIds([...keys].filter(key => {
        const reference = readInputReference(key);
        return typeof reference === 'string' || (reference.context || reference.feature) === reference.feature;
      }));
      kind = value || null;
      anchorPreference = null;
      const remapped = relationshipSelectionForFeatures(state(), kind, directFeatures);
      for (const key of keys) {
        const reference = readInputReference(key);
        if (typeof reference !== 'string' && reference?.context && reference.context !== reference.feature) remapped.add(key);
      }
      draft = relationshipRoleSelection(state(), kind, remapped, previous);
      label = makeLabel(definition() || {}); pin(); activeRole = 'participants';
    },
    change(role, key, included) {
      const pinned = anchorChoice();
      if (!included && pinned?.role === role && pinned.output.key === key) return false;
      if (!kind) {
        if (included) draft.unassigned.add(key); else draft.unassigned.delete(key);
      } else {
        const keys = draft.roles[role];
        if (!keys || included && !choices(role).some(output => output.key === key && output.compatible)) return false;
        if (included) {
          const contract = state().input_contracts[definition().roles[role]];
          if (contract.max_count === 1) keys.clear();
          keys.add(key); draft.unassigned.delete(key);
        } else {keys.delete(key); draft.unassigned.delete(key);}
      }
      pin(); return true;
    },
    clear(role) {
      if (role) draft.roles[role]?.clear();
      else {for (const keys of Object.values(draft.roles)) keys.clear(); draft.unassigned.clear();}
      pin();
    },
    setRoleUse(role, use) {
      const field = definition()?.node_fields?.[role];
      if (!field?.use_field || !(field.use_choices || []).some(choice => choice.value === use)) return false;
      (draft.roleUses ||= {})[role] = use;
      pin(); return true;
    },
    removeUnassigned(key) { draft.unassigned.delete(key); pin(); },
    setLabel(value) {label = value;},
    setActiveRole(role) {activeRole = role;},
    choices, validity,
    inputs() {const result = validity(); if (!result.valid) throw new Error(result.reason); return relationshipNodeInputs(state(), kind, draft, nodes());},
    definitions() {
      return relationshipDefinitions(state()).filter(item => !anchor() || Object.keys(item.roles).some(role =>
        relationshipRoleChoices(state(), item.id, role, new Set(), nodes()).some(output =>
          output.reference.feature === anchor() && (output.reference.context || anchor()) === anchor())));
    },
    focusIds(keys = selected()) {
      return [...new Set(relationshipExpandedOutputs(state(), relationshipParticipantChoices(state(), kind, keys, nodes())
        .filter(output => keys.has(output.key))).map(output => output.reference.feature))];
    },
    snapshot() {return {kind, draft: copyDraft(draft), selected: selected(), activeRole, label, validity: validity()};},
    get kind() {return kind;}, get draft() {return copyDraft(draft);}, get selected() {return selected();},
    get activeRole() {return activeRole;}, get label() {return label;}, get state() {return state();},
    get explicitRoles() {return usesRoles();}, get pinned() {return anchorChoice();},
  };
  return api;
}

export function createRelationshipEditor({root, prefix = 'relationship', nameId = `${prefix}-label`,
  getState, getNodes, locked = () => false, anchorFeature = null, makeLabel,
  onChange = () => {}, onBeforeKindChange = () => {}, onPick = () => {},
  onInspect = () => {}, onFocus = () => {}, isPicking = () => false}) {
  const document = root.ownerDocument, model = createRelationshipDraft({getState, getNodes, anchorFeature, makeLabel}),
    roles = new Map(), elements = {};
  let renderedKind, destroyed = false;
  const el = (tag, className = '', text = '') => {
    const result = document.createElement(tag); result.className = className; result.textContent = text; return result;
  }, button = (text, id) => {const result = el('button', '', text); result.type = 'button'; if (id) result.id = id; return result;},
    option = (text, value, selected = false, disabled = false) => {
      const result = el('option', '', text); result.value = value; result.selected = selected; result.disabled = disabled; return result;
    },
    notify = () => onChange(api.snapshot(), api),
    mutate = action => {if (locked() || destroyed) return false; const result = action(); render(); notify(); return result;};
  const kindLabel = el('label', '', 'Relationship');
  elements.kind = el('select'); elements.kind.id = `${prefix}-kind`;
  elements.kind.setAttribute('aria-label', 'Relationship'); kindLabel.htmlFor = elements.kind.id; kindLabel.append(elements.kind);
  elements.summary = el('p', 'hint'); elements.summary.id = `${prefix}-summary`;
  const tools = el('div', 'relationship-selection-tools');
  elements.count = el('output'); elements.count.id = `${prefix}-count`;
  elements.focus = button('Focus selected', prefix === 'relationship' ? 'focus-relationship' : `${prefix}-focus`);
  elements.clear = button('Clear', prefix === 'relationship' ? 'clear-relationship-participants' : `${prefix}-clear`);
  tools.append(elements.count, elements.focus, elements.clear);
  elements.peer = el('div'); elements.peer.id = `${prefix}-participant-field`;
  elements.options = el('div'); elements.options.id = `${prefix}-participant-options`;
  elements.filter = el('input'); elements.filter.id = `${prefix}-filter`; elements.filter.type = 'search';
  elements.filter.placeholder = 'Filter participants'; elements.filter.setAttribute('aria-label', 'Filter participants');
  elements.list = el('div', 'relationship-participants'); elements.list.id = `${prefix}-participants`;
  elements.list.dataset.pickerScroll = ''; elements.list.tabIndex = 0; elements.list.setAttribute('aria-label', 'Participant choices');
  elements.options.append(elements.filter, elements.list);
  elements.roles = el('div'); elements.roles.id = `${prefix}-role-fields`;
  elements.unassigned = el('div'); elements.unassigned.id = `${prefix}-unassigned`;
  const details = el('details'), summary = el('summary', '', 'Options'), nameLabel = el('label', '', 'Name');
  elements.name = el('input'); elements.name.id = nameId; elements.name.required = true; elements.name.maxLength = 120;
  nameLabel.append(elements.name); details.append(summary, nameLabel);
  root.replaceChildren(kindLabel, elements.summary, tools, elements.peer, elements.options, elements.roles, elements.unassigned, details);
  const peer = makePicker('participants', 'Participants', true, elements.peer, elements.options, elements.filter, elements.list);
  peer.field.button.id = prefix === 'relationship' ? 'pick-relationship-participant' : `${prefix}-pick-participant`;

  function makePicker(name, label, multiple, wrapper, options, filter, list) {
    const field = createPickField(document, {label, multiple, popupContent: options,
      onPick: () => {model.setActiveRole(name); render(); onPick(name, api);},
      onChange: key => {const selected = model.draft.roles[name] || model.selected; api.change(name, key, !selected.has(key));},
      onRemove: key => api.change(name, key, false),
      onInspect: key => onInspect(key, api),
    });
    field.element.classList.add('relationship-participant-field'); wrapper.append(field.element);
    filter.oninput = () => filterRows();
    return {name, label, multiple, wrapper, field, list, filter};
  }
  function pickers() {return model.explicitRoles ? [...roles.values()] : [peer];}
  function hidePopup(picker) {if (picker.field.popup.matches(':popover-open')) picker.field.popup.hidePopover();}
  function renderRoles() {
    elements.peer.hidden = elements.options.hidden = model.explicitRoles;
    elements.roles.hidden = !model.explicitRoles;
    if (renderedKind === model.kind) return;
    hidePopup(peer); for (const picker of roles.values()) hidePopup(picker);
    roles.clear(); elements.roles.replaceChildren(); renderedKind = model.kind;
    if (!model.explicitRoles) return;
    const signature = getState().relationship_contracts[model.kind];
    for (const [role, requirement] of Object.entries(signature.roles)) {
      const label = signature.role_labels?.[role] || role, wrapper = el('section', 'relationship-role-field'),
        heading = el('h3', '', label), clear = button('Clear'), options = el('div'), filter = el('input'),
        list = el('div', 'relationship-participants');
      wrapper.dataset.relationshipRole = role; clear.setAttribute('aria-label', `Clear ${label}`);
      filter.type = 'search'; filter.placeholder = `Filter ${label.toLowerCase()}`; filter.setAttribute('aria-label', `Filter ${label.toLowerCase()}`);
      list.id = `${prefix}-role-${model.kind}-${role}-choices`; list.dataset.pickerScroll = ''; list.tabIndex = 0;
      list.setAttribute('aria-label', `${label} choices`); options.append(filter, list); wrapper.append(heading, clear);
      const picker = makePicker(role, label, getState().input_contracts[requirement].max_count !== 1,
        wrapper, options, filter, list);
      if (signature.node_fields?.[role]?.use_field) {
        const useLabel = el('label', '', 'Reference ownership'), use = el('select');
        use.id = `${prefix}-role-${role}-use`; useLabel.htmlFor = use.id; useLabel.append(use);
        use.setAttribute('aria-label', 'Reference ownership');
        wrapper.insertBefore(useLabel, picker.field.element);
        use.onchange = () => api.setRoleUse(role, use.value); picker.use = use;
      }
      picker.clear = clear; clear.onclick = () => api.clear(role); roles.set(role, picker); elements.roles.append(wrapper);
    }
    if (!roles.has(model.activeRole)) model.setActiveRole(roles.has('participants') ? 'participants' : roles.keys().next().value);
  }
  function filterRows() {
    for (const picker of pickers()) {
      const filter = picker.filter.value.trim().toLowerCase(), rows = [...picker.list.querySelectorAll('.relationship-participant')];
      for (const row of rows) row.hidden = !row.querySelector('input').checked &&
        (row.dataset.compatible === 'false' || !row.dataset.search.includes(filter));
      for (const heading of picker.list.querySelectorAll('.relationship-participant-group'))
        heading.hidden = !rows.some(row => !row.hidden && row.dataset.participantGroup === heading.dataset.participantGroup);
    }
  }
  function render() {
    if (destroyed) return;
    model.refresh();
    const state = getState(), definitions = model.definitions(), definition = relationshipDefinitions(state).find(item => item.id === model.kind),
      selected = model.selected, validity = model.validity(), pinned = model.pinned, isLocked = locked();
    elements.kind.replaceChildren(option('Choose a relationship', '', !model.kind),
      ...definitions.map(item => option(item.label, item.id, item.id === model.kind)));
    if (model.kind && !definitions.some(item => item.id === model.kind))
      elements.kind.append(option(`${definition?.label || 'Saved relationship'} — unavailable for this fit`, model.kind, true, true));
    elements.kind.value = model.kind || ''; elements.kind.disabled = isLocked;
    elements.name.value = model.label; elements.name.readOnly = isLocked;
    renderRoles();
    for (const picker of pickers()) {
      if (picker.use) {
        renderRelationshipRoleUse(picker.use, state, model.kind, picker.name,
          relationshipRoleUse(state, model.kind, picker.name, model.draft));
        picker.use.disabled = isLocked;
      }
      const outputs = model.choices(picker.name), keys = model.explicitRoles ? model.draft.roles[picker.name] || new Set() : selected;
      renderRelationshipParticipants(picker.list, {choices: outputs.map(output => ({
        ...relationshipParticipantPresentation(state, output), id: output.key,
        icon: state.recipe.nodes.find(node => node.id === output.reference?.feature)?.kind ||
          state.recipe.nodes.find(node => node.id === output.reference?.feature)?.operation || 'reference_plane',
        compatible: output.compatible,
      })).sort((a, b) => Number(a.group === 'reference') - Number(b.group === 'reference')),
      selected: keys, multiple: picker.multiple, locked, change: (key, included) => api.change(picker.name, key, included),
      inspect: key => onInspect(key, api), focus: key => onFocus(model.focusIds(new Set([key])), api)});
      picker.field.update(outputs.map(output => {
        const presentation = relationshipParticipantPresentation(state, output);
        return {key: output.key, label: presentation.name, detail: presentation.detail,
          selected: keys.has(output.key), disabled: !output.compatible,
          members: output.collection ? relationshipCollectionMembers(state, output).map(member => {
            const item = relationshipParticipantPresentation(state, member); return [item.name, item.detail].filter(Boolean).join(' — ');
          }) : undefined,
          modelPickable: relationshipExpandedOutputs(state, [output]).some(member => !!relationshipOutputValue(state, member)),
          treePickable: output.compatible};
      }), {disabled: isLocked, busy: isLocked, emptyMessage: 'Choose compatible outputs, including those that need evaluation.'});
      picker.field.button.setAttribute('aria-pressed', String(isPicking(picker.name)));
      if (picker.clear) picker.clear.disabled = isLocked || ![...keys].some(key => key !== pinned?.output.key);
      if (pinned?.role === picker.name || !model.explicitRoles && pinned) {
        for (const input of picker.list.querySelectorAll('input')) if (input.dataset.participantId === pinned.output.key) input.disabled = true;
        for (const row of picker.field.element.querySelectorAll('[data-selection-key]')) if (row.dataset.selectionKey === pinned.output.key) {
          const remove = row.querySelector('.selection-field-remove'); if (remove) remove.disabled = true;
        }
      }
    }
    elements.unassigned.hidden = !model.explicitRoles || !model.draft.unassigned.size;
    elements.unassigned.replaceChildren();
    if (!elements.unassigned.hidden) {
      elements.unassigned.append(el('h3', '', 'Choose roles for remaining participants'));
      for (const key of model.draft.unassigned) {
        const row = el('div'), name = el('span'), role = el('select'), remove = button('Remove'),
          output = relationshipParticipantChoices(state, model.kind, new Set([key])).find(item => item.key === key);
        name.textContent = relationshipParticipantPresentation(state, output).choiceLabel;
        role.setAttribute('aria-label', `Role for ${output.label}`); role.append(option('Choose a role…', ''));
        for (const picker of pickers()) if (model.choices(picker.name).some(item => item.key === key && item.compatible))
          role.append(option(picker.label, picker.name));
        role.disabled = isLocked || role.options.length === 1; role.onchange = () => api.change(role.value, key, true);
        remove.disabled = isLocked; remove.onclick = () => mutate(() => model.removeUnassigned(key));
        row.append(name, role, remove); elements.unassigned.append(row);
      }
    }
    const outputs = relationshipParticipantChoices(state, model.kind, selected).filter(output => selected.has(output.key)),
      counts = relationshipSelectionCounts(state, outputs);
    elements.count.textContent = counts.collections ? `${selected.size} ${selected.size === 1 ? 'selection' : 'selections'} · ${counts.unique} outputs`
      : `${selected.size} selected`;
    elements.focus.disabled = !selected.size; elements.clear.disabled = isLocked || ![...selected].some(key => key !== pinned?.output.key);
    elements.summary.textContent = definition ? validity.valid ?
      (definition.operation === 'direction_alignment' ? `${directionAlignmentLabel(validity.roles.participants)}. ` : '') + definition.summary
      : validity.reason : 'Choose a relationship, then its participants.';
    if (validity.valid && Object.values(validity.roles).flat().some(output => output.availability !== 'ready'))
      elements.summary.textContent += ' This relationship will need evaluation; adding it does not require evaluating its inputs.';
    filterRows();
  }
  const api = {
    root, elements,
    reset(value) {model.reset(value); render(); notify(); return api.snapshot();},
    refresh: render,
    setKind(value) {return mutate(() => {onBeforeKindChange(api); model.setKind(value);});},
    change(role, key, included) {return mutate(() => model.change(role, key, included));},
    setRoleUse(role, use) {return mutate(() => model.setRoleUse(role, use));},
    clear(role) {return mutate(() => model.clear(role));},
    inputs: () => model.inputs(), validity: () => model.validity(), snapshot: () => model.snapshot(),
    picker: role => pickers().find(picker => picker.name === role), pickers, choices: role => model.choices(role),
    setActiveRole(role) {model.setActiveRole(role); render();}, focusIds: keys => model.focusIds(keys),
    destroy() {destroyed = true; hidePopup(peer); for (const picker of roles.values()) hidePopup(picker); root.replaceChildren();},
    get kind() {return model.kind;}, get draft() {return model.draft;}, get selected() {return model.selected;},
    get activeRole() {return model.activeRole;}, get state() {return model.state;},
  };
  elements.kind.onchange = () => api.setKind(elements.kind.value);
  elements.name.oninput = () => mutate(() => model.setLabel(elements.name.value));
  elements.clear.onclick = () => api.clear();
  elements.focus.onclick = () => onFocus(model.focusIds(), api);
  model.reset(); render();
  return api;
}
