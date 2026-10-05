import { featureIcon, featureIconLegend } from './action-tree.js';

export function renderIconLegend(container) {
  container.replaceChildren(...featureIconLegend.map(({ title, entries }) => {
    const section = document.createElement('section'), heading = document.createElement('h3'),
      list = document.createElement('ul');
    heading.textContent = title;
    for (const { name, label, state, detail } of entries) {
      const item = document.createElement('li'), text = document.createElement('span');
      item.dataset.icon = name;
      text.textContent = label;
      if (detail) {
        const description = document.createElement('small');
        description.textContent = detail;
        text.append(description);
      }
      item.append(featureIcon(name, state ? `action-state state-${name}` : ''), text);
      list.append(item);
    }
    section.append(heading, list);
    return section;
  }));
}
