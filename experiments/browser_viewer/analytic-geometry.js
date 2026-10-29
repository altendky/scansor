// Spatial accessors only. Solver charts and authored actions remain separate.
function invalid(message) {
  throw new TypeError(`Invalid analytic geometry: ${message}`);
}

function vector(value, name) {
  if (!Array.isArray(value) || value.length !== 3 || ![...value].every(Number.isFinite))
    invalid(`${name} must contain three finite coordinates`);
  return [...value];
}

function unit(value, name, canonical = false) {
  const result = vector(value, name), length = Math.hypot(...result);
  if (!(length > 0) || !Number.isFinite(length) ||
      (canonical && Math.abs(length - 1) > 1e-10))
    invalid(`${name} must be ${canonical ? 'a unit direction' : 'nonzero and normalizable'}`);
  // Preserve the rounded canonical direction used to resolve its hard caps.
  if (canonical && Math.abs(length - 1) <= 4 * Number.EPSILON) return result;
  return result.map(component => component / length);
}

function support(value, required) {
  if (!required && value == null) return null;
  if (!Array.isArray(value) || value.length !== 2 || ![...value].every(Number.isFinite) ||
      !(value[0] < value[1])) invalid('axial_domain must contain two finite increasing bounds');
  return [...value];
}

export function revolutionGeometry(fitted) {
  if (!fitted || typeof fitted !== 'object') invalid('a fitted surface is required');
  const canonical = Object.hasOwn(fitted, 'analytic'), record = canonical ? fitted.analytic : fitted,
    kind = fitted.kind;
  if (!['cylinder', 'cone'].includes(kind)) invalid('surface must be a cylinder or cone');
  let origin, axis, radius, slope, axialDomain;
  if (canonical) {
    if (!record || typeof record !== 'object' || Array.isArray(record) || record.kind !== kind)
      invalid('analytic kind must match the fitted surface');
    origin = vector(record.origin, 'origin');
    axis = unit(record.axis, 'axis', true);
    radius = record.radius;
    slope = record.slope;
    axialDomain = support(record.axial_domain, true);
  } else {
    const p = record.parameters;
    if (!Array.isArray(p) || p.length !== 7 || ![...p].every(Number.isFinite))
      invalid('legacy revolution parameters must contain seven finite values');
    origin = [p[0], p[1], 0];
    axis = unit([p[2], p[3], 1], 'legacy axis');
    radius = p[4];
    slope = p[6];
    axialDomain = support(record.axial_domain, false);
  }
  if (!Number.isFinite(radius) || !Number.isFinite(slope)) invalid('radius and slope must be finite');
  if (kind === 'cylinder' && (!(radius > 0) || slope !== 0))
    invalid('cylinder requires a positive radius and zero slope');
  if (axialDomain && axialDomain.some(height => {
    const value = radius + slope * height;
    return !(value > 0) || !Number.isFinite(value);
  })) invalid('radius must stay finite and positive throughout axial_domain');
  return { kind, origin, axis, radius, slope, axial_domain: axialDomain };
}

export function axisGeometry(value) {
  if (!value || typeof value !== 'object') invalid('an axis or fitted surface is required');
  if (Object.hasOwn(value, 'analytic') || ['cylinder', 'cone'].includes(value.kind)) {
    const { origin, axis } = revolutionGeometry(value);
    return { origin, axis };
  }
  if (Object.hasOwn(value, 'axis_display') || Object.hasOwn(value, 'point_display'))
    return { origin: vector(value.point_display, 'axis point'), axis: unit(value.axis_display, 'axis') };
  const p = value.parameters;
  if (!Array.isArray(p) || ![4, 7].includes(p.length) || ![...p].every(Number.isFinite))
    invalid('legacy axis parameters must contain four or seven finite values');
  return { origin: [p[0], p[1], 0], axis: unit([p[2], p[3], 1], 'legacy axis') };
}

export function independentPlaneGeometry(initialNormal, offset) {
  const input = vector(initialNormal, 'plane normal'), scale = Math.max(...input.map(Math.abs));
  if (!(scale > 0)) invalid('plane normal must be nonzero');
  if (!Number.isFinite(offset)) invalid('plane offset must be finite');
  const normal = unit(input.map(value => value / scale), 'plane normal'),
    reference = [0, 0, 0];
  reference[normal.map(Math.abs).indexOf(Math.min(...normal.map(Math.abs)))] = 1;
  const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]],
    basisU = unit(cross(normal, reference), 'plane tangent'), basisV = cross(normal, basisU);
  return {normal, offset, point: normal.map(value => value * offset), basisU, basisV};
}
