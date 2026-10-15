// Optional authored support is separate from the fitted surface's finite extent.
export function copyFitDomain(domain) {
  return domain == null ? null : [...domain];
}

export function parseFitDomain(start, end) {
  const values = [String(start).trim(), String(end).trim()];
  if (values.every(value => value === '')) return null;
  const numbers = values.map(Number);
  if (values.some(value => value === '') || !numbers.every(Number.isFinite) ||
      numbers[0] >= numbers[1])
    throw new TypeError('Enter both increasing axial bounds, or leave both blank for automatic support.');
  return numbers;
}

export function defaultFitDomain(kind, sourceDescriptor) {
  return ['cylinder', 'cone'].includes(kind) && sourceDescriptor != null ? null : [-2, 5];
}

export function editableManagedSupport(node, nodes, sourceDescriptor) {
  return sourceDescriptor != null && node?.operation === 'fit' &&
    ['cylinder', 'cone'].includes(node.kind) &&
    nodes.some(owner => owner.id === node.managed_by && owner.operation === 'feature_reuse');
}

export function fitSupportChanges(authority, start, end) {
  if (authority === 'prototype') return { support_authority: 'prototype' };
  if (authority === 'local')
    return { support_authority: 'local', axial_domain: parseFitDomain(start, end) };
  throw new TypeError('Choose support from the source fit or target bounds.');
}
