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

export function residualLimit(surfaces) {
  return Math.max(
    1e-12,
    ...surfaces.flatMap(([, surface]) => surface.residuals.map(Math.abs)),
  );
}
