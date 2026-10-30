// Relationship highlighting consumes exact published ports, never a latest-fit
// alias. Buffers are display-only and cannot become picking or fitting evidence.
import { relationshipExpandedOutputs, relationshipOutputValue } from './relationship-inputs.js';

export const relationshipHighlightColors = {selected: '#78e2ff', overlap: '#ffe45c', hover: '#ffe45c'};
export const maxRelationshipHighlightPoints = 131072;

const validId = (id, positions) => Number.isSafeInteger(id) && id >= 0 && id < positions.length / 3 &&
  Number.isFinite(positions[id * 3]) && Number.isFinite(positions[id * 3 + 1]) && Number.isFinite(positions[id * 3 + 2]);

export function createRelationshipSupportCache(maxEntries = 128) {
  if (!Number.isSafeInteger(maxEntries) || maxEntries < 1) throw Error('Invalid support cache size');
  let accepted, source;
  const records = new Map();
  return {
    clear() { records.clear(); accepted = source = undefined; },
    read(state, positions, value) {
      if (accepted !== state || source !== positions) { records.clear(); accepted = state; source = positions; }
      if (!value || !Array.isArray(value.ids) || !value.ids.length) return null;
      if (records.has(value)) {
        const saved = records.get(value); records.delete(value); records.set(value, saved); return saved;
      }
      const lower = [Infinity, Infinity, Infinity], upper = [-Infinity, -Infinity, -Infinity], mean = [0, 0, 0];
      let count = 0;
      for (const id of value.ids) if (validId(id, positions)) {
        count++;
        for (let axis = 0; axis < 3; axis++) {
          const coordinate = positions[id * 3 + axis];
          lower[axis] = Math.min(lower[axis], coordinate); upper[axis] = Math.max(upper[axis], coordinate);
          mean[axis] += coordinate;
        }
      }
      const saved = count ? {ids: value.ids, bounds: [lower, upper], center: mean.map(sum => sum / count)} : null;
      records.set(value, saved);
      if (records.size > maxEntries) records.delete(records.keys().next().value);
      return saved;
    },
  };
}

export function relationshipHighlightParts(state, outputs, positions, cache, kinds = new Map()) {
  return relationshipExpandedOutputs(state, outputs).flatMap(output => {
    const value = relationshipOutputValue(state, output);
    if (!value) return [];
    const key = output.collectionKey || output.key, support = cache.read(state, positions, value);
    return [{output, value, key, support, kind: kinds.get(key) || 'selected'}];
  });
}

export function relationshipSupportGroups(parts, positions, maxPoints = maxRelationshipHighlightPoints) {
  if (!Number.isSafeInteger(maxPoints) || maxPoints < 1) throw Error('Invalid highlight point limit');
  const priority = {selected: 1, overlap: 2, hover: 3}, kinds = Object.keys(priority),
    membership = new Uint8Array(Math.floor(positions.length / 3)), counts = [0, 0, 0, 0];
  for (const part of parts) for (const id of part.support?.ids || []) if (validId(id, positions))
    membership[id] = Math.max(membership[id], priority[part.kind] || 1);
  for (const code of membership) counts[code]++;
  return kinds.flatMap(kind => {
    const code = priority[kind], total = counts[code];
    if (!total) return [];
    const budget = Math.min(total, maxPoints), chosen = new Set(),
      contributors = parts.filter(part => part.kind === kind && part.support);
    // Reserve a few markers for each exact leaf before sampling a large union.
    // A small overlapping fit must not disappear beside a much larger fit.
    for (const part of contributors) {
      const values = part.support.ids, slots = Math.min(8, values.length);
      let represented = false;
      for (let slot = 0; slot < slots && chosen.size < budget; slot++) {
        const id = values[Math.floor(slot * values.length / slots)];
        if (validId(id, positions) && membership[id] === code) { chosen.add(id); represented = true; }
      }
      if (!represented && chosen.size < budget) for (const id of values)
        if (validId(id, positions) && membership[id] === code) { chosen.add(id); break; }
    }
    const remaining = budget - chosen.size;
    let ordinal = 0, sampled = 0;
    for (let id = 0; id < membership.length && sampled < remaining; id++) if (membership[id] === code) {
      const target = remaining > 1 ? Math.round(sampled * (total - 1) / (remaining - 1)) : 0;
      if (ordinal++ === target) { chosen.add(id); sampled++; }
    }
    for (let id = 0; id < membership.length && chosen.size < budget; id++)
      if (membership[id] === code) chosen.add(id);
    const ids = Uint32Array.from(chosen).sort(), coordinates = new Float32Array(ids.length * 3);
    ids.forEach((id, index) => coordinates.set(positions.subarray(id * 3, id * 3 + 3), index * 3));
    return [{kind, ids, coordinates, totalVertices: total, sampled: total > budget,
      keys: [...new Set(contributors.map(part => part.key))],
      references: contributors.map(part => part.output.reference)}];
  });
}

export function relationshipAxisSpan(axis, origin, support, sceneBounds) {
  if (!Array.isArray(axis) || !Array.isArray(origin) || axis.length !== 3 || origin.length !== 3 ||
      ![...axis, ...origin].every(Number.isFinite) || !(Math.hypot(...axis) > 0)) return null;
  const direction = axis.map(value => value / Math.hypot(...axis)),
    finiteBounds = bounds => Array.isArray(bounds) && bounds.length === 2 && bounds.every(point =>
      Array.isArray(point) && point.length === 3 && point.every(Number.isFinite)) &&
      bounds[0].every((value, i) => value <= bounds[1][i]),
    bounds = finiteBounds(support?.bounds) ? support.bounds : finiteBounds(sceneBounds) ? sceneBounds : null;
  if (!bounds) return null;
  const sceneSize = finiteBounds(sceneBounds) ? Math.hypot(...sceneBounds[1].map((value, i) => value - sceneBounds[0][i])) : 0,
    size = Math.hypot(...bounds[1].map((value, i) => value - bounds[0][i])),
    anchor = support?.center || bounds[0].map((value, i) => (value + bounds[1][i]) / 2),
    distance = direction.reduce((sum, value, i) => sum + value * (anchor[i] - origin[i]), 0),
    center = origin.map((value, i) => value + direction[i] * distance),
    halfSpan = direction.reduce((sum, value, i) => sum + Math.abs(value) * (bounds[1][i] - bounds[0][i]) / 2, 0),
    extent = support ? Math.max(halfSpan * 1.25, size * 0.35, sceneSize * 0.015) : sceneSize * 0.12;
  if (!(extent > 0) || !Number.isFinite(extent) || !center.every(Number.isFinite)) return null;
  return {axis: direction, origin: [...origin], center, extent, arrowLength: extent * 0.16};
}
