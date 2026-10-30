// Renderer-owned approximations never replace accepted graph geometry.
const ENTRY_LIMIT = 16 * 1024 * 1024;
export const CAD_DISPLAY_CACHE_BYTES = 64 * 1024 * 1024;

export function decodeCadDisplay(value) {
  const preview = value?.preview, edges = value?.edges;
  if (!Array.isArray(preview?.positions) || !Array.isArray(preview.indices) || !Array.isArray(edges)
    || preview.positions.length % 3 || preview.indices.length % 3 || edges.length > 16384)
    throw new Error('Invalid CAD display geometry.');
  let bytes = (preview.positions.length + preview.indices.length) * 4;
  for (const edge of edges) {
    if (!Array.isArray(edge.positions) || edge.positions.length % 3 || typeof edge.closed !== 'boolean')
      throw new Error('Invalid CAD display boundary.');
    bytes += edge.positions.length * 4;
  }
  if (bytes > ENTRY_LIMIT) throw new Error('CAD display exceeds the per-feature memory budget.');
  const positions = Float32Array.from(preview.positions), vertices = positions.length / 3;
  if (!positions.length || !preview.indices.length || !positions.every(Number.isFinite)
    || !preview.indices.every(index => Number.isSafeInteger(index) && index >= 0 && index < vertices))
    throw new Error('Invalid CAD display triangles.');
  const result = {preview: {positions, indices: Uint32Array.from(preview.indices)}, edges: [], bytes};
  if (value.face_ranges !== undefined) {
    if (!Array.isArray(value.face_ranges) || !value.face_ranges.length || value.face_ranges.length > 512)
      throw new Error('Invalid CAD display face provenance.');
    let next = 0;
    result.face_ranges = value.face_ranges.map(range => {
      if (range.start !== next || !Number.isSafeInteger(range.count) || range.count <= 0 || range.count % 3
        || !Array.isArray(range.face_ids) || !range.face_ids.length || range.face_ids.length > 512
        || new Set(range.face_ids).size !== range.face_ids.length
        || range.face_ids.some(id => typeof id !== 'string' || !id || id.length > 256))
        throw new Error('Invalid CAD display face provenance.');
      next += range.count;
      return {start: range.start, count: range.count, face_ids: [...range.face_ids]};
    });
    if (next !== preview.indices.length) throw new Error('CAD display face provenance does not cover its triangles.');
  }
  for (const edge of edges) {
    const points = Float32Array.from(edge.positions);
    if (points.length < 6 || !points.every(Number.isFinite)) throw new Error('Invalid CAD display boundary coordinates.');
    result.edges.push({positions: points, closed: edge.closed});
  }
  return result;
}

export function createCadDisplay({refine, onChange = () => {}, onStatus = () => {},
  byteLimit = CAD_DISPLAY_CACHE_BYTES}) {
  let context, scene, quality, targets = [], enabled = false, blocked = false,
    epoch = 0, active = null, task = null, paused = false, totalBytes = 0, evicted = false, failureReason = '';
  const cache = new Map(), visible = new Map(), failures = new Set(), completed = new Set();
  const key = id => JSON.stringify([quality, id]);
  const notify = (message = '') => onStatus({busy: !!task, message, bytes: totalBytes});
  function abort() { epoch++; active?.abort(); }
  function clear() {
    cache.clear(); visible.clear(); failures.clear(); completed.clear(); totalBytes = 0; evicted = false; failureReason = '';
  }
  function cached(id) {
    const name = key(id), entry = cache.get(name);
    if (entry) { cache.delete(name); cache.set(name, entry); }
    return entry;
  }
  function retain(id, entry) {
    const name = key(id);
    if (entry.bytes > byteLimit) throw new Error('CAD display exceeds the renderer memory budget.');
    totalBytes -= cache.get(name)?.bytes || 0;
    cache.delete(name); cache.set(name, entry); totalBytes += entry.bytes;
    completed.add(name);
    visible.set(id, entry);
    while (totalBytes > byteLimit) {
      const [oldKey, oldEntry] = cache.entries().next().value;
      cache.delete(oldKey); totalBytes -= oldEntry.bytes;
      evicted = true;
      for (const [feature, shown] of visible) if (shown === oldEntry) visible.delete(feature);
    }
  }
  function schedule() {
    if (task || !enabled || blocked || paused || quality === 'draft') return;
    const currentEpoch = epoch;
    task = Promise.resolve().then(async () => {
      let count = 0;
      while (currentEpoch === epoch && enabled && !blocked && !paused) {
        const id = targets.find(id => !completed.has(key(id)) && !failures.has(key(id)));
        if (!id) break;
        const request = {token: context.token, revision: context.revision, feature: id, quality};
        active = new AbortController();
        notify(`Refining CAD display (${++count} of ${targets.length})…`);
        try {
          const result = await refine(request, {signal: active.signal});
          if (currentEpoch !== epoch || active.signal.aborted) break;
          if (result?.token !== request.token || result.revision !== request.revision
            || result.feature !== id || result.quality !== request.quality)
            throw new Error('CAD display result is no longer current.');
          retain(id, {...decodeCadDisplay(result.display), quality: request.quality});
          onChange();
        } catch (error) {
          if (currentEpoch !== epoch || active.signal.aborted) break;
          if (error.code === 'computation_cancelled') { paused = true; break; }
          failures.add(key(id));
          failureReason = error.message;
          notify(`${error.message} Keeping the previous display.`);
        } finally { active = null; }
        // A safe boundary between individual CAD operations lets input and
        // cancellation run even where OCCT cannot be interrupted internally.
        await new Promise(resolve => setTimeout(resolve, 0));
      }
    }).finally(() => {
      task = null;
      if (currentEpoch === epoch) notify(failures.size
        ? `Some CAD displays could not be refined: ${failureReason}. Previous displays are retained; try Draft or Retry.`
        : paused ? 'Display refinement cancelled. Completed displays are retained.'
          : evicted ? 'CAD display cache is full. Uncached features use their original display.' : '');
      else { notify(paused ? 'Display refinement cancelled. Completed displays are retained.' : ''); schedule(); }
    });
  }
  return {
    refresh(next, ids, options = {}) {
      const nextScene = JSON.stringify([next.generation, next.token, next.revision]);
      if (scene !== nextScene) { abort(); clear(); scene = nextScene; paused = false; }
      if (quality !== next.quality) { abort(); failures.clear(); failureReason = ''; paused = false; quality = next.quality; }
      context = {...next}; targets = [...new Set(ids)];
      enabled = options.enabled !== false; blocked = options.blocked === true;
      if ((!enabled || blocked || quality === 'draft') && active) abort();
      if (quality === 'draft') visible.clear();
      for (const id of targets) { const entry = cached(id); if (entry) visible.set(id, entry); }
      schedule();
    },
    lookup(id) { return quality === 'draft' ? null : cached(id) || visible.get(id) || null; },
    cancel() { paused = true; abort(); notify('Stopping CAD display refinement at a safe boundary…'); },
    retry() { paused = false; failures.clear(); failureReason = ''; completed.clear(); schedule(); },
    reset() { abort(); clear(); scene = undefined; enabled = false; },
    settled() { return task || Promise.resolve(); },
    get busy() { return !!task; },
    get bytes() { return totalBytes; },
  };
}
