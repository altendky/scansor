import { createTree, hotkeysCoreFeature, selectionFeature, syncDataLoaderFeature } from '@headless-tree/core';

const controllers = new WeakMap();
const rootKey = '__feature_tree_root__';

// Headless Tree supplies tree state, visible ordering, ARIA metadata and hotkeys.
// This adapter keeps Scansor's toggle-selection and explicit-edit interactions.
const scansorInteractions = {
  key: 'scansor-interactions',
  overwrites: ['selection'],
  treeInstance: {
    updateDomFocus: ({ tree }) => {
      const element = tree.getFocusedItem()?.getElement();
      element?.focus();
      element?.scrollIntoView?.({ block: 'nearest' });
    },
  },
  itemInstance: {
    getProps: ({ item, tree, prev }) => ({
      ...prev(),
      onClick: () => {
        item.setFocused();
        tree.updateDomFocus();
        item.primaryAction();
      },
    }),
  },
};

export function createHeadlessFeatureTree(list, entries, { selectedIds, focusedKey }) {
  let controller = controllers.get(list);
  if (!controller) {
    let data = new Map(), updating = true;
    const tree = createTree({
      rootItemId: rootKey,
      features: [syncDataLoaderFeature, selectionFeature, hotkeysCoreFeature, scansorInteractions],
      dataLoader: {
        getItem: key => data.get(key),
        getChildren: key => data.get(key)?.children || [],
      },
      getItemName: item => item.getItemData().name,
      isItemFolder: item => !!item.getItemData().details,
      onPrimaryAction: item => item.getItemData().activate?.(),
      setState: () => { if (!updating) paint(); },
      hotkeys: {
        // Synthetic groups are navigation entries, not selectable features.
        selectAll: { hotkey: 'Control+KeyA', isEnabled: () => false },
        selectUpwards: { hotkey: 'Shift+ArrowUp', isEnabled: () => false },
        selectDownwards: { hotkey: 'Shift+ArrowDown', isEnabled: () => false },
        toggleSelectedItem: {
          hotkey: 'Space',
          preventDefault: true,
          handler: (_event, instance) => instance.getFocusedItem()?.primaryAction(),
        },
        customActivate: {
          hotkey: 'Enter',
          preventDefault: true,
          handler: (_event, instance) => instance.getFocusedItem()?.primaryAction(),
        },
        expandOrDown: {
          hotkey: 'ArrowRight',
          preventDefault: true,
          canRepeat: true,
          handler: (_event, instance) => {
            const item = instance.getFocusedItem();
            if (!item?.isFolder()) return;
            if (!item.isExpanded()) item.expand();
            else if (item.getChildren().length) {
              item.getChildren()[0].setFocused();
              instance.updateDomFocus();
            }
          },
        },
        collapseOrUp: { hotkey: 'ArrowLeft', preventDefault: true, canRepeat: true },
        focusFirstItem: { hotkey: 'Home', preventDefault: true },
        focusLastItem: { hotkey: 'End', preventDefault: true },
      },
    });
    function paint() {
      const visible = tree.getItems();
      for (const entry of data.values()) {
        if (!entry.item) continue;
        entry.item.tabIndex = -1;
        if (entry.details) entry.renderExpanded(tree.getState().expandedItems.includes(entry.key));
      }
      for (const instance of visible) {
        const entry = instance.getItemData(), props = instance.getProps();
        for (const [name, value] of Object.entries(props)) {
          if (name === 'ref' || name.startsWith('on')) continue;
          if (name === 'tabIndex') entry.item.tabIndex = value;
          else if (value === undefined) entry.item.removeAttribute(name);
          else entry.item.setAttribute(name, value);
        }
        if (!entry.row.dataset.actionId) entry.item.removeAttribute('aria-selected');
        entry.row.classList.toggle('feature-selected', instance.isSelected());
      }
      list.dataset.focusedTreeKey = tree.getFocusedItem()?.getId() || '';
    }
    controller = {
      tree,
      update(nextEntries, options) {
        updating = true;
        for (const entry of data.values())
          if (entry.item) tree.getItemInstance(entry.key).registerElement(null);
        data = new Map(nextEntries.map(entry => [entry.key, { ...entry, children: [],
          name: entry.item.getAttribute('aria-label') || entry.row.title }]));
        data.set(rootKey, { children: [], name: 'Features' });
        for (const entry of data.values()) {
          if (!entry.item) continue;
          const parent = entry.item.parentElement?.closest('.tree-item')?.dataset.treeKey || rootKey;
          data.get(parent).children.push(entry.key);
          tree.getItemInstance(entry.key).registerElement(entry.item);
        }
        const selectedItems = nextEntries.filter(entry => options.selectedIds.has(entry.row.dataset.actionId))
          .map(entry => entry.key),
          expandedItems = nextEntries.filter(entry => entry.details?.open).map(entry => entry.key);
        tree.setConfig(previous => ({ ...previous,
          state: { ...tree.getState(), expandedItems, selectedItems },
        }));
        tree.rebuildTree();
        const visible = tree.getItems(), previousFocus = options.focusedKey || tree.getState().focusedItem,
          focus = visible.find(item => item.getId() === previousFocus) ||
            visible.find(item => selectedItems.includes(item.getId())) || visible[0];
        tree.setConfig(previous => ({ ...previous,
          state: { ...tree.getState(), focusedItem: focus?.getId() || null },
        }));
        updating = false;
        paint();
        if (options.focusedKey) tree.updateDomFocus();
      },
      focus(key) {
        tree.getItemInstance(key).setFocused();
        tree.updateDomFocus();
      },
      onFocus(key) {
        if (tree.getState().focusedItem !== key) tree.getItemInstance(key).setFocused();
      },
      activate(key, event) { tree.getItemInstance(key).getProps().onClick(event); },
      setExpanded(key, open) {
        const item = tree.getItemInstance(key);
        if (open) item.expand(); else item.collapse();
      },
    };
    // Core's framework lifecycle hook is also used by the official React binding.
    // Kept in this adapter and covered against the pinned core version.
    tree.setMounted(true);
    tree.registerElement(list);
    for (const [name, value] of Object.entries(tree.getContainerProps('Features in evaluation order')))
      if (name !== 'ref' && !name.startsWith('on')) list.setAttribute(name, value);
    list.dataset.treeLibrary = 'headless-tree';
    controllers.set(list, controller);
  }
  controller.update(entries, { selectedIds, focusedKey });
  return controller;
}
