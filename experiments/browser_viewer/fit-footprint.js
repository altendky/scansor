// Display-only boundaries of projected fitting observations, not physical trims.
// Mesh connectivity preserves gaps/holes; the fallback convex envelope does not.
const TAU = 2 * Math.PI, ANGLE_STEP = Math.PI / 48;
const knownTopologies = new WeakSet();
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const subtract = (a, b) => a.map((value, index) => value - b[index]);
const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];

function unit(vector) {
  const length = Math.hypot(...vector);
  return length > 0 && Number.isFinite(length) ? vector.map((value) => value / length) : null;
}

function chart(normal) {
  let least = 0;
  for (let index = 1; index < 3; index++)
    if (Math.abs(normal[index]) < Math.abs(normal[least])) least = index;
  const basis = [0, 0, 0];
  basis[least] = 1;
  const u = unit(cross(normal, basis));
  return [u, cross(normal, u)];
}

// Build once per scanned mesh. Queries visit only selected vertices' incidents.
export function buildFootprintTopology(indices, vertexCount) {
  if ((!Array.isArray(indices) && !ArrayBuffer.isView(indices)) ||
      !Number.isInteger(indices.length) || !indices.length || indices.length % 3 ||
      !Number.isInteger(vertexCount) || vertexCount <= 0 || vertexCount >= 0xffffffff ||
      indices.length >= 0xffffffff) return null;
  try {
    const copied = new Uint32Array(indices.length), offsets = new Uint32Array(vertexCount + 1);
    for (let index = 0; index < indices.length; index++) {
      const id = indices[index];
      if (!Number.isInteger(id) || id < 0 || id >= vertexCount) return null;
      copied[index] = id;
      offsets[id + 1]++;
      if (index % 3 === 2 && (copied[index] === copied[index - 1] ||
          copied[index] === copied[index - 2] || copied[index - 1] === copied[index - 2])) return null;
    }
    for (let index = 1; index < offsets.length; index++) offsets[index] += offsets[index - 1];
    const cursor = offsets.slice(0, -1), incidentTriangles = new Uint32Array(indices.length);
    for (let index = 0; index < copied.length; index++)
      incidentTriangles[cursor[copied[index]]++] = Math.floor(index / 3);
    const topology = { vertexCount, indices: copied, offsets, incidentTriangles };
    knownTopologies.add(topology);
    return topology;
  } catch (error) {
    if (error instanceof RangeError) return null;
    throw error;
  }
}

function meshLoops(fitted, positions, topology) {
  if (!topology || !knownTopologies.has(topology) || topology.vertexCount !== positions.length / 3)
    return null;
  const selected = new Set(fitted.ids), triangles = new Set(), vertices = new Map(), edges = new Map(),
    { offsets, incidentTriangles, indices } = topology;
  for (const id of selected) {
    const begin = offsets[id], end = offsets[id + 1];
    if (!Number.isInteger(begin) || !Number.isInteger(end) || begin >= end ||
        end > incidentTriangles.length) return null; // Isolated membership: use the hull.
    for (let index = begin; index < end; index++) {
      const triangle = incidentTriangles[index];
      if (!Number.isInteger(triangle) || triangle * 3 + 2 >= indices.length) return null;
      triangles.add(triangle);
    }
  }
  const vertex = (id) => {
    const key = `v:${id}`;
    if (!vertices.has(key)) vertices.set(key, [positions[id * 3], positions[id * 3 + 1], positions[id * 3 + 2]]);
    return key;
  };
  const midpoint = (a, b) => {
    const key = `e:${Math.min(a, b)}:${Math.max(a, b)}`;
    if (!vertices.has(key)) {
      const p = vertices.get(vertex(a)), q = vertices.get(vertex(b));
      vertices.set(key, p.map((value, index) => value * 0.5 + q[index] * 0.5));
    }
    return key;
  };
  for (const triangle of triangles) {
    const ids = [indices[triangle * 3], indices[triangle * 3 + 1], indices[triangle * 3 + 2]], polygon = [];
    if (new Set(ids).size !== 3 || ids.some((id) => !Number.isInteger(id) || id >= topology.vertexCount)) return null;
    for (let index = 0; index < 3; index++) {
      const a = ids[index], b = ids[(index + 1) % 3];
      if (selected.has(a)) polygon.push(vertex(a));
      if (selected.has(a) !== selected.has(b)) polygon.push(midpoint(a, b));
    }
    for (let index = 0; index < polygon.length; index++) {
      const a = polygon[index], b = polygon[(index + 1) % polygon.length],
        key = a < b ? `${a}|${b}` : `${b}|${a}`, edge = edges.get(key);
      if (edge) {
        if (++edge.count > 2) return null;
      } else edges.set(key, { a, b, count: 1 });
    }
  }
  if ([...vertices.values()].some((point) => !point.every(Number.isFinite))) return null;
  const neighbors = new Map();
  for (const { a, b, count } of edges.values()) if (count === 1) {
    if (!neighbors.has(a)) neighbors.set(a, []);
    if (!neighbors.has(b)) neighbors.set(b, []);
    neighbors.get(a).push(b);
    neighbors.get(b).push(a);
  }
  if (!neighbors.size || [...neighbors.values()].some((adjacent) => adjacent.length !== 2)) return null;
  const remaining = new Set([...neighbors.keys()].sort()), loops = [];
  while (remaining.size) {
    const start = remaining.values().next().value, loop = [];
    let previous = null, current = start;
    do {
      if (!remaining.delete(current)) return null;
      loop.push(vertices.get(current));
      const adjacent = neighbors.get(current).sort(), next = adjacent[0] === previous ? adjacent[1] : adjacent[0];
      previous = current;
      current = next;
    } while (current !== start);
    if (loop.length < 3) return null;
    loops.push(loop);
  }
  return loops;
}

function selectedPoints(fitted, positions) {
  if ((!Array.isArray(positions) && !ArrayBuffer.isView(positions)) ||
      !Number.isInteger(positions.length) || positions.length % 3 ||
      !Array.isArray(fitted.ids) || !fitted.ids.length) return null;
  const ids = [...new Set(fitted.ids)].sort((a, b) => a - b), points = [];
  for (const id of ids) {
    if (!Number.isInteger(id) || id < 0 || id >= positions.length / 3) return null;
    const point = [positions[id * 3], positions[id * 3 + 1], positions[id * 3 + 2]];
    if (!point.every(Number.isFinite)) return null;
    points.push(point);
  }
  return points;
}

// Scale the chart coordinates before orientation tests; no absolute length
// threshold should make a small scan's footprint disappear.
function hull(points) {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1])
    .filter((point, index, all) => !index || point[0] !== all[index - 1][0] ||
      point[1] !== all[index - 1][1]);
  if (sorted.length < 3) return [];
  let ymin = Infinity, ymax = -Infinity;
  for (const point of sorted) {
    ymin = Math.min(ymin, point[1]);
    ymax = Math.max(ymax, point[1]);
  }
  const xmin = sorted[0][0], xspan = sorted.at(-1)[0] - xmin, yspan = ymax - ymin;
  if (!(xspan > 0 && yspan > 0) || !Number.isFinite(xspan + yspan)) return [];
  const normalized = new Map(sorted.map((point) =>
    [point, [(point[0] - xmin) / xspan, (point[1] - ymin) / yspan]]));
  const turn = (a, b, c) => {
    const [ax, ay] = normalized.get(a), [bx, by] = normalized.get(b),
      [cx, cy] = normalized.get(c);
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  };
  const half = (sequence) => {
    const chain = [];
    for (const point of sequence) {
      while (chain.length >= 2 && turn(chain.at(-2), chain.at(-1), point) <= 0) chain.pop();
      chain.push(point);
    }
    chain.pop();
    return chain;
  };
  const boundary = [...half(sorted), ...half([...sorted].reverse())];
  if (boundary.length < 3) return [];
  let area = 0;
  for (let index = 1; index < boundary.length - 1; index++)
    area += turn(boundary[0], boundary[index], boundary[index + 1]);
  return area > 64 * Number.EPSILON ? boundary : [];
}

function path(points) {
  const positions = points.flat();
  return positions.every(Number.isFinite)
    ? { preview: { positions, indices: [] }, closed: true } : null;
}

function planeFrame(fitted, first) {
  const p = fitted.plane_equation || fitted.parameters;
  if (!Array.isArray(p) || ![...p].every(Number.isFinite)) return null;
  let normal, offset;
  if (p.length === 4) {
    const length = Math.hypot(...p.slice(0, 3));
    normal = unit(p.slice(0, 3));
    offset = p[3] / length;
  } else if (!fitted.plane_equation && p.length === 7) {
    normal = unit([p[2], p[3], 1]);
    offset = p[5]; // Coaxial fits already store a unit-normal plane offset.
  } else return null;
  if (!normal || !Number.isFinite(offset)) return null;
  const [u, v] = chart(normal), distance = dot(normal, first) - offset,
    anchor = first.map((value, index) => value - distance * normal[index]);
  return {
    coordinates(point) {
      const relative = subtract(point, first);
      return [dot(relative, u), dot(relative, v)];
    },
    point: ([x, y]) => anchor.map((value, index) => value + x * u[index] + y * v[index]),
  };
}

function planeFootprint(fitted, points) {
  const frame = planeFrame(fitted, points[0]);
  if (!frame) return [];
  const boundary = hull(points.map(frame.coordinates));
  if (!boundary.length) return [];
  const result = path(boundary.map(frame.point));
  return result ? [result] : [];
}

function axialFrame(fitted, first) {
  const p = fitted.parameters;
  if (!Array.isArray(p) || p.length !== 7 || ![...p].every(Number.isFinite) ||
      (fitted.kind === 'cylinder' && !(p[4] > 0))) return null;
  const axis = unit([p[2], p[3], 1]);
  if (!axis) return null;
  const [u, v] = chart(axis), origin = [p[0], p[1], 0], slope = fitted.kind === 'cone' ? p[6] : 0,
    firstHeight = dot(subtract(first, origin), axis),
    anchor = origin.map((value, index) => value + firstHeight * axis[index]),
    firstRadial = subtract(first, anchor), baseRadius = p[4] + slope * firstHeight,
    meridianLength = Math.hypot(1, slope), meridianH = 1 / meridianLength,
    meridianR = slope / meridianLength;
  if (!Number.isFinite(baseRadius)) return null;
  return {
    coordinates(point) {
      const relative = subtract(point, first), height = dot(relative, axis),
        radial = firstRadial.map((value, index) => value + relative[index] - height * axis[index]),
        radius = Math.hypot(...radial);
      if (!(radius > 0) || !Number.isFinite(radius)) return null;
      // Closest point on the cone's meridian r = baseRadius + slope * h.
      const h = (height * meridianH + (radius - baseRadius) * meridianR) * meridianH,
        r = baseRadius + slope * h;
      if (!Number.isFinite(h) || !(r > 0) || !Number.isFinite(r)) return null;
      let angle = Math.atan2(dot(radial, v), dot(radial, u));
      if (angle < 0) angle += TAU;
      return [angle, h, Math.max(radius, Math.abs(height), Math.abs(h))];
    },
    point([angle, height]) {
      const radius = baseRadius + slope * height, cosine = Math.cos(angle), sine = Math.sin(angle);
      return anchor.map((value, index) => value + height * axis[index] +
        radius * (cosine * u[index] + sine * v[index]));
    },
  };
}

function axialFootprint(fitted, points) {
  const frame = axialFrame(fitted, points[0]);
  if (!frame) return [];
  const projected = [];
  let minimum = Infinity, maximum = -Infinity, projectionScale = 0;
  for (const point of points) {
    const coordinates = frame.coordinates(point);
    if (!coordinates) return [];
    const [angle, h, scale] = coordinates;
    projected.push([angle, h]);
    minimum = Math.min(minimum, h);
    maximum = Math.max(maximum, h);
    projectionScale = Math.max(projectionScale, scale);
  }
  // Reject rings/lines whose apparent thickness is only projection roundoff.
  if (!(maximum - minimum > 64 * Number.EPSILON * projectionScale)) return [];
  const angles = [...new Set(projected.map(([angle]) => angle))].sort((a, b) => a - b);
  let largestGap = -1, start = 0;
  for (let index = 0; index < angles.length; index++) {
    const next = angles[(index + 1) % angles.length],
      gap = (index + 1 === angles.length ? next + TAU : next) - angles[index];
    if (gap > largestGap) {
      largestGap = gap;
      start = next;
    }
  }
  if (!(TAU - largestGap > 64 * Number.EPSILON)) return [];
  const surfacePoint = (angle, height) => frame.point([angle, height]);
  if (largestGap <= Math.PI / 2 + 64 * Number.EPSILON) {
    // A periodic band has no angular seam. Draw its two end rings only.
    const rings = [minimum, maximum].map((height) => path(Array.from({ length: 96 },
      (_, index) => surfacePoint(index * ANGLE_STEP, height))));
    return rings.every(Boolean) ? rings : [];
  }
  const boundary = hull(projected.map(([angle, height]) => [angle < start ? angle + TAU : angle, height]));
  if (!boundary.length) return [];
  const sampled = [];
  for (let index = 0; index < boundary.length; index++) {
    const a = boundary[index], b = boundary[(index + 1) % boundary.length],
      steps = Math.max(1, Math.ceil(Math.abs(b[0] - a[0]) / ANGLE_STEP));
    for (let sample = 0; sample < steps; sample++) {
      const fraction = sample / steps;
      sampled.push(surfacePoint(a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])));
    }
  }
  const result = path(sampled);
  return result ? [result] : [];
}

function projectMeshLoops(fitted, points, loops) {
  const planar = fitted.kind === 'plane', frame = planar ? planeFrame(fitted, points[0]) : axialFrame(fitted, points[0]);
  if (!frame) return null;
  const result = [];
  for (const loop of loops) {
    const coordinates = loop.map(frame.coordinates), sampled = [];
    if (coordinates.some((point) => !point || !point.every(Number.isFinite))) return null;
    if (planar) {
      if (!hull(coordinates).length) return null; // Projection collapsed the mesh region to a line/point.
      for (const point of coordinates) sampled.push(frame.point(point));
    }
    else for (let index = 0; index < coordinates.length; index++) {
      const a = coordinates[index], b = coordinates[(index + 1) % coordinates.length],
        delta = Math.atan2(Math.sin(b[0] - a[0]), Math.cos(b[0] - a[0])),
        steps = Math.max(1, Math.ceil(Math.abs(delta) / ANGLE_STEP));
      for (let sample = 0; sample < steps; sample++) {
        const fraction = sample / steps;
        sampled.push(frame.point([a[0] + delta * fraction, a[1] + (b[1] - a[1]) * fraction]));
      }
    }
    const projected = path(sampled);
    if (!projected) return null;
    result.push(projected);
  }
  return result;
}

export function fittedSelectionFootprint(fitted, positions, topology = null) {
  if (!fitted || !['plane', 'cylinder', 'cone'].includes(fitted.kind)) return [];
  const points = selectedPoints(fitted, positions);
  if (!points) return [];
  const loops = meshLoops(fitted, positions, topology), projected = loops && projectMeshLoops(fitted, points, loops);
  if (projected) return projected;
  return fitted.kind === 'plane' ? planeFootprint(fitted, points) : axialFootprint(fitted, points);
}
