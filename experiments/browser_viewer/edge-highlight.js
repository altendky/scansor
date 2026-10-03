import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

// Screen-space strokes stay legible when the model is scaled or zoomed. Both
// passes render after transparent preview faces, without writing scene depth.
export function faceEdgeLines(positions, color, closed, emphasis = 'region') {
  if (!Array.isArray(positions) || positions.length < 6 || positions.length % 3 ||
      !positions.every(Number.isFinite)) return [];
  const points = [...positions];
  if (closed && points.slice(-3).some((value, index) => value !== points[index]))
    points.push(...points.slice(0, 3));
  const inspecting = emphasis === 'inspected';
  const footprint = ['footprint', 'footprint-context'].includes(emphasis);
  const width = inspecting ? 4 : ['neighbor', 'footprint'].includes(emphasis) ? 3 : emphasis === 'defined-face' ? 1 : 2;
  const order = inspecting ? 220 : emphasis === 'neighbor' ? 210 : footprint ? 205 : emphasis === 'defined-face' ? 190 : 200;
  // Mute the color, not alpha: overlapping segment caps must not brighten joins.
  const strokeColor = emphasis === 'footprint-context' ? '#75b7c8' : color;
  return [
    { color: '#101820', width: width + 3 },
    { color: strokeColor, width },
  ].map((stroke, index) => {
    const geometry = new LineGeometry().setPositions(points);
    const line = new Line2(geometry, new LineMaterial({
      color: stroke.color, linewidth: stroke.width, worldUnits: false,
      depthTest: false, depthWrite: false, transparent: true, opacity: 1,
    }));
    line.renderOrder = order + index;
    return line;
  });
}
