const TAU = 2 * Math.PI;

function quad(vertices, a, b, c, d) {
  vertices.push(a, b, c, a, c, d);
}

function planeVolume(region) {
  const normal = region.plane_normal,
    u = region.basis_u,
    v = region.basis_v,
    planePoint = normal.map((value) => value * region.plane_offset),
    points = [];
  for (const normalOffset of region.normal_bounds)
    for (const vOffset of region.v_bounds)
      for (const uOffset of region.u_bounds)
        points.push(
          planePoint.map(
            (value, index) =>
              value +
              normal[index] * normalOffset +
              u[index] * uOffset +
              v[index] * vOffset,
          ),
        );
  const triangles = [];
  // Per normal layer: (v0/u0, v0/u1, v1/u0, v1/u1).
  quad(triangles, points[0], points[1], points[3], points[2]);
  quad(triangles, points[4], points[6], points[7], points[5]);
  quad(triangles, points[0], points[4], points[5], points[1]);
  quad(triangles, points[2], points[3], points[7], points[6]);
  quad(triangles, points[0], points[2], points[6], points[4]);
  quad(triangles, points[1], points[5], points[7], points[3]);
  return triangles;
}

function cylinderVolume(region) {
  const inner = Math.max(0, region.radius + region.normal_bounds[0]),
    outer = region.radius + region.normal_bounds[1],
    span = Math.min(TAU, region.angle_span),
    segments = Math.max(1, Math.ceil((span / TAU) * 64)),
    [lower, upper] = region.axial_bounds,
    triangles = [];
  if (!(outer > inner) || !(span > 0) || !(upper > lower)) return triangles;
  const point = (angle, radius, axial) => [
    radius * Math.cos(angle),
    radius * Math.sin(angle),
    axial,
  ];
  for (let index = 0; index < segments; index++) {
    const firstAngle = region.angle_start + (span * index) / segments,
      secondAngle = region.angle_start + (span * (index + 1)) / segments,
      innerFirstLower = point(firstAngle, inner, lower),
      innerSecondLower = point(secondAngle, inner, lower),
      innerFirstUpper = point(firstAngle, inner, upper),
      innerSecondUpper = point(secondAngle, inner, upper),
      outerFirstLower = point(firstAngle, outer, lower),
      outerSecondLower = point(secondAngle, outer, lower),
      outerFirstUpper = point(firstAngle, outer, upper),
      outerSecondUpper = point(secondAngle, outer, upper);
    quad(
      triangles,
      outerFirstLower,
      outerSecondLower,
      outerSecondUpper,
      outerFirstUpper,
    );
    if (inner > 0)
      quad(
        triangles,
        innerSecondLower,
        innerFirstLower,
        innerFirstUpper,
        innerSecondUpper,
      );
    quad(
      triangles,
      innerFirstLower,
      innerSecondLower,
      outerSecondLower,
      outerFirstLower,
    );
    quad(
      triangles,
      innerFirstUpper,
      outerFirstUpper,
      outerSecondUpper,
      innerSecondUpper,
    );
  }
  if (span < TAU - 1e-9) {
    for (const angle of [region.angle_start, region.angle_start + span])
      quad(
        triangles,
        point(angle, inner, lower),
        point(angle, outer, lower),
        point(angle, outer, upper),
        point(angle, inner, upper),
      );
  }
  return triangles;
}

function coneVolume(region) {
  const slope = region.slope, radius = region.radius, scale = Math.hypot(1, slope),
    generators = region.generator_bounds, offsets = region.normal_bounds;
  if (![radius, slope, region.angle_start, region.angle_span].every(Number.isFinite) ||
      ![generators, offsets].every(values => Array.isArray(values) && values.length === 2 && values.every(Number.isFinite)) ||
      !(generators[1] > generators[0]) || !(offsets[1] > offsets[0]) ||
      generators.some(value => {
        const valueRadius = radius + (slope / scale) * value;
        return !Number.isFinite(valueRadius) || !(valueRadius > 0);
      }))
    throw new TypeError('Invalid finite cone selection volume');
  const span = Math.min(TAU, region.angle_span), segments = Math.max(1, Math.ceil(span / TAU * 64)),
    slices = [...generators], triangles = [];
  if (!(span > 0)) return triangles;
  // A normal layer may meet the axis. Split there so the displayed volume
  // clips at zero radial distance, instead of extending onto the other sheet.
  if (slope !== 0)
    for (const offset of offsets) {
      const crossing = -(radius + offset / scale) / (slope / scale);
      if (crossing > generators[0] && crossing < generators[1]) slices.push(crossing);
    }
  slices.sort((a, b) => a - b);
  const point = (angle, generator, offset) => {
    const height = generator / scale, surfaceRadius = radius + (slope / scale) * generator,
      normalOffset = Math.max(offset, -surfaceRadius * scale),
      radial = Math.max(0, surfaceRadius + normalOffset / scale);
    return [radial * Math.cos(angle), radial * Math.sin(angle), height - (slope / scale) * normalOffset];
  };
  for (let row = 0; row < slices.length - 1; row++) {
    const [lower, upper] = [slices[row], slices[row + 1]];
    for (let index = 0; index < segments; index++) {
      const a = region.angle_start + span * index / segments,
        b = region.angle_start + span * (index + 1) / segments,
        il = point(a, lower, offsets[0]), jl = point(b, lower, offsets[0]),
        iu = point(a, upper, offsets[0]), ju = point(b, upper, offsets[0]),
        ol = point(a, lower, offsets[1]), pl = point(b, lower, offsets[1]),
        ou = point(a, upper, offsets[1]), pu = point(b, upper, offsets[1]);
      quad(triangles, ol, pl, pu, ou);
      quad(triangles, jl, il, iu, ju);
      if (row === 0) quad(triangles, il, jl, pl, ol);
      if (row === slices.length - 2) quad(triangles, iu, ou, pu, ju);
    }
    if (span < TAU - 1e-9)
      for (const angle of [region.angle_start, region.angle_start + span])
        quad(triangles, point(angle, lower, offsets[0]), point(angle, lower, offsets[1]),
          point(angle, upper, offsets[1]), point(angle, upper, offsets[0]));
  }
  return triangles;
}

export function selectionVolumePositions(region, origin, rotation) {
  const local =
    region.kind === 'plane'
      ? planeVolume(region)
      : region.kind === 'cylinder'
        ? cylinderVolume(region)
        : region.kind === 'cone'
          ? coneVolume(region)
          : [];
  return new Float32Array(
    local.flatMap((point) =>
      origin.map(
        (value, row) =>
          value +
          point[0] * rotation[row][0] +
          point[1] * rotation[row][1] +
          point[2] * rotation[row][2],
      ),
    ),
  );
}
