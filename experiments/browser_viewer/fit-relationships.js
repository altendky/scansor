import {createRelationshipEditor} from './relationship-editor.js';
import {projectedFitState} from './fit-input-state.js';
import {relationshipDefinitionForNode, relationshipDraftForNode, relationshipRoleUse,
  relationshipFeatureIds} from './relationship-inputs.js';
import {uniqueFeatureLabel} from './feature-names.js';
import {readInputReference} from './feature-inputs.js';

export function relationshipEditorNodes(state, id) {
  const nodes = state.recipe.nodes, index = nodes.findIndex(node => node.id === id);
  return index < 0 ? nodes : nodes.slice(0, index);
}

// This is a structural validation view, never a saved recipe. Use the common
// field declarations so staged edits do not conflict with their own old nodes.
export function stagedRelationshipState(state, originalIds, drafts) {
  const staged = new Map(drafts.map(row => {
    const signature = state.relationship_contracts[row.kind];
    if (!signature) return [row.id, null];
    const node = {id: row.id, operation: signature.operation, ...signature.node_values};
    for (const [role, field] of Object.entries(signature.node_fields || {})) {
      const values = [...(row.draft.roles[role] || [])].map(key => {
        const reference = readInputReference(key);
        return field.encoding === 'feature' && typeof reference !== 'string' ? reference?.feature : reference;
      });
      node[field.name] = field.multiple ? values : values[0] ?? null;
      if (field.use_field) node[field.use_field] = relationshipRoleUse(state, row.kind, role, row.draft);
    }
    return [row.id, node];
  }));
  const nodes = state.recipe.nodes.flatMap(node => {
    if (!originalIds.has(node.id)) return [node];
    const candidate = staged.get(node.id); staged.delete(node.id);
    return candidate ? [candidate] : [];
  });
  nodes.push(...[...staged.values()].filter(Boolean));
  return {...state, recipe: {...state.recipe, nodes}};
}

export function relationshipNodeFromEditor(state, original, editor, id = original?.id) {
  const inputs = editor.inputs(), label = editor.snapshot().label.trim(),
    definition = original && relationshipDefinitionForNode(state, original), sameKind = definition?.id === editor.kind;
  if (!label) throw Error('Enter a name for each relationship.');
  // Creation defaults are not edits to saved options that this editor does not expose.
  if (sameKind) for (const field of Object.keys(definition.node_values || {}))
    if (original[field] !== undefined) inputs[field] = original[field];
  return {...(sameKind ? original : original ? {group_id: original.group_id} : {}), id, label, ...inputs};
}

// A fit owns its observations. Relationships remain independent recipe nodes;
// this panel only stages their creation/editing in the same authoring change.
export function createFitRelationships({root, prefix, getState, getFit, locked,
  onChange = () => {}, onPick, onInspect, onFocus, isPicking, onRemove}) {
  const doc = root.ownerDocument, rows = [], originalIds = new Set();
  let serial = 0, rendering = false, previousState, previousFit, projected;
  const list = doc.createElement('div'), add = doc.createElement('button'),
    hint = doc.createElement('p'), error = doc.createElement('p');
  list.id = `${prefix}-rows`;
  add.id = `${prefix}-add`; add.type = 'button'; add.textContent = 'Add relationship';
  hint.className = error.className = 'hint'; error.setAttribute('role', 'alert');
  hint.textContent = 'Relationships are saved as separate features. The fit and these changes are applied together; Auto controls evaluation.';
  root.replaceChildren(list, add, hint, error);
  const state = () => {
    const accepted = getState(), fit = getFit(), signature = JSON.stringify(fit);
    if (accepted !== previousState || signature !== previousFit) {
      projected = projectedFitState(accepted, fit); previousState = accepted; previousFit = signature;
    }
    return stagedRelationshipState(projected, originalIds, rows.filter(row => row.editor).map(row => ({
      id: row.id, kind: row.editor.kind, draft: row.editor.draft})));
  };
  function changed(snapshot, editor) { if (!rendering) onChange(snapshot, editor); }
  function append(node = null) {
    const wrapper = doc.createElement('section'), editorRoot = doc.createElement('div'),
      heading = doc.createElement('h3'), remove = doc.createElement('button');
    const id = node?.id || `relationship_${crypto.randomUUID().replaceAll('-', '')}`;
    wrapper.dataset.fitRelationship = id; wrapper.className = 'fit-relationship-editor';
    heading.textContent = node ? node.label : 'New relationship';
    remove.type = 'button'; remove.textContent = 'Remove relationship';
    remove.setAttribute('aria-label', node ? `Remove ${node.label}` : 'Remove this relationship');
    wrapper.append(heading, remove, editorRoot); list.append(wrapper);
    const row = {id, original: node, wrapper, remove, editor: null};
    rows.push(row);
    row.editor = createRelationshipEditor({root: editorRoot, prefix: `${prefix}-${++serial}`,
      getState: state, locked, anchorFeature: () => getFit().id,
      getNodes: () => relationshipEditorNodes(state(), row.id),
      makeLabel: definition => uniqueFeatureLabel(definition.baseName || 'Relationship', [
        ...getState().recipe.nodes, ...rows.filter(other => other !== row && other.editor)
          .map(other => ({label: other.editor.snapshot().label}))]),
      onChange: (snapshot, editor) => { error.textContent = ''; changed(snapshot, editor); },
      onBeforeKindChange: editor => onRemove?.(editor),
      onPick, onInspect, onFocus, isPicking: role => isPicking?.(role, row.editor) || false,
    });
    if (node) {
      const definition = relationshipDefinitionForNode(state(), node);
      row.editor.reset({kind: definition.id, draft: relationshipDraftForNode(state(), node), label: node.label});
    } else row.editor.reset({label: 'Relationship'});
    remove.onclick = () => {
      if (locked()) return;
      onRemove?.(row.editor); row.editor.destroy(); wrapper.remove();
      rows.splice(rows.indexOf(row), 1); changed();
    };
    return row.editor;
  }
  add.onclick = () => { if (!locked()) { append(); changed(); } };
  return {
    root,
    reset({includeExisting = false} = {}) {
      rendering = true;
      try {
        for (const row of rows) { onRemove?.(row.editor); row.editor.destroy(); }
        rows.length = 0; originalIds.clear(); list.replaceChildren(); error.textContent = '';
        if (includeExisting) {
          const current = projectedFitState(getState(), getFit()), fit = getFit();
          for (const node of current.recipe.nodes) {
            const definition = relationshipDefinitionForNode(current, node);
            if (!definition) continue;
            const draft = relationshipDraftForNode(current, node);
            const features = relationshipFeatureIds(new Set([
              ...Object.values(draft.roles).flatMap(keys => [...keys]), ...(draft.unassigned || [])]));
            if (!features.includes(fit.id)) continue;
            const direct = [...Object.values(draft.roles).flatMap(keys => [...keys]), ...(draft.unassigned || [])]
              .some(key => {
                const reference = readInputReference(key);
                return reference === fit.id || reference?.feature === fit.id && (reference.context || fit.id) === fit.id;
              });
            if (node.managed_by || !direct) {
              const note = doc.createElement('p'); note.className = 'hint';
              const owner = current.recipe.nodes.find(candidate => candidate.id === node.managed_by);
              note.textContent = node.managed_by
                ? `${node.label} is generated by ${owner?.label || 'another feature'}. Edit its generating feature to change it.`
                : `${node.label} references this fit through a published result. Edit that relationship to preserve its selected context.`;
              list.append(note); continue;
            }
            originalIds.add(node.id); append(node);
          }
        }
      } finally { rendering = false; }
      this.refresh();
    },
    refresh() {
      rendering = true;
      const isLocked = locked();
      for (const row of rows) row.remove.disabled = isLocked;
      try {
        state(); error.textContent = ''; add.disabled = isLocked;
        for (const row of rows) { row.editor.root.inert = false; row.editor.refresh(); }
      } catch (failure) {
        error.textContent = failure.message; add.disabled = true;
        for (const row of rows) row.editor.root.inert = true;
      }
      finally { rendering = false; }
    },
    apply(recipe) {
      const replacements = new Map(rows.map(row => [row.id,
        relationshipNodeFromEditor(state(), row.original, row.editor, row.id)]));
      recipe.nodes = recipe.nodes.flatMap(node => {
        if (!originalIds.has(node.id)) return [node];
        const replacement = replacements.get(node.id);
        replacements.delete(node.id);
        return replacement ? [replacement] : [];
      });
      recipe.nodes.push(...replacements.values());
      if (originalIds.has(recipe.output) && !rows.some(row => row.id === recipe.output)) recipe.output = getFit().id;
      return recipe;
    },
    editors() { return rows.map(row => row.editor); },
    destroy() { for (const row of rows) row.editor.destroy(); rows.length = 0; root.replaceChildren(); },
  };
}
