export function resultResidualSurfaces(result, fallbackId = 'standalone') {
  if (!result) return [];
  const surfaces = result.surfaces || (result.residuals ? { [fallbackId]: result } : {});
  return Object.entries(surfaces).filter(([, surface]) =>
    Array.isArray(surface?.ids) &&
    Array.isArray(surface?.residuals) &&
    surface.ids.length > 0 &&
    surface.ids.length === surface.residuals.length &&
    surface.residuals.every(Number.isFinite)
  );
}

export function residualRange(surfaces) {
  const residuals = surfaces.flatMap(([, surface]) => surface.residuals);
  const minimum = Math.min(0, ...residuals),
    maximum = Math.max(0, ...residuals);
  return {
    minimum,
    maximum,
    limit: Math.max(1e-12, -minimum, maximum),
  };
}

export function parseRmsLimit(value) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const limit = Number(value);
  return Number.isFinite(limit) && limit > 0 ? limit : null;
}

export function fitQualities(recipe, states, results, limit = null, errors = {}) {
  limit = parseRmsLimit(limit);
  const qualities = {}, byId = new Map(recipe.nodes.map((node) => [node.id, node]));
  for (const node of recipe.nodes) {
    if (node.operation !== 'fit') continue;
    const result = results[node.id],
      context = ['Current resolved geometry',
        ...[['axis', 'Axis'], ['reference_plane', 'Reference plane'], ['point', 'Point']]
          .filter(([key]) => node[key]).map(([key, label]) => `${label}: ${byId.get(node[key])?.label || node[key]}`),
        ...(result?.resolved_by ? [`Resolved by: ${result.resolved_by}`] : [])].join(' · ');
    if (states[node.id] !== 'ready' || errors[node.id] ||
        !Number.isFinite(result?.weighted_rms) || result.weighted_rms < 0 ||
        !resultResidualSurfaces({ ids: result?.ids, residuals: result?.residuals }, node.id).length) {
      qualities[node.id] = { label: 'RMS —', status: 'unavailable', rms: null,
        tooltip: `${context}\nNo current fit quality (${errors[node.id] || (states[node.id] !== 'ready'
          ? states[node.id] || 'not evaluated' : 'missing or invalid residual metrics')}).` };
      continue;
    }
    let peak = 0;
    for (const residual of result.residuals) peak = Math.max(peak, Math.abs(residual));
    const rms = result.weighted_rms,
      status = limit === null ? 'unrated' : rms > limit ? 'warning' : 'within',
      assessment = limit === null ? 'No RMS limit set.'
        : `${status === 'warning' ? 'Above' : 'Within'} RMS limit ${limit.toPrecision(5)}.`,
      conditioning = Number.isFinite(result.condition)
        ? `\nCondition estimate: ${result.condition.toPrecision(4)} (not a fit-error score).` : '';
    qualities[node.id] = { rms, peak, status, label: `RMS ${rms.toPrecision(3)}`,
      tooltip: `${context}\nWeighted RMS: ${rms.toPrecision(6)}; worst residual: ${peak.toPrecision(6)}.\nSource-scan length units. ${assessment}${conditioning}` };
  }
  return qualities;
}

export function summarizeFitQuality(qualities) {
  if (!qualities.length) return null;
  const current = qualities.filter((quality) => quality.rms !== null),
    warnings = current.filter((quality) => quality.status === 'warning').length,
    complete = current.length === qualities.length;
  let worst = 0;
  for (const quality of current) worst = Math.max(worst, quality.rms);
  const status = warnings ? 'warning' : !complete ? 'unavailable'
    : current.every((quality) => quality.status === 'within') ? 'within' : 'unrated';
  return {
    rms: complete ? worst : null, warnings, status,
    label: `${complete ? `max ${worst.toPrecision(3)}` : 'RMS —'}${warnings ? ` · ${warnings}!` : ''}`,
    tooltip: `${current.length}/${qualities.length} current fits.\n${complete ? `Worst member RMS: ${worst.toPrecision(6)} (source-scan length units).` : 'Summary incomplete: evaluate all members.'}\n${current.some((quality) => quality.status === 'unrated') ? 'No RMS limit set.' : current.length ? `${warnings} above the RMS limit.` : 'No current assessment.'} Alignment/matching error is not included.`,
  };
}
