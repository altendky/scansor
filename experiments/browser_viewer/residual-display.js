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
