export function uniqueFeatureLabel(base, nodes, maxLength = 120) {
  const taken = new Set(nodes.map((node) => node.label.trim().toLowerCase()));
  const root = base.trim().slice(0, maxLength) || 'Feature';
  if (!taken.has(root.toLowerCase())) return root;
  for (let number = 2; ; number++) {
    const suffix = ` ${number}`,
      candidate = root.slice(0, maxLength - suffix.length).trimEnd() + suffix;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}
