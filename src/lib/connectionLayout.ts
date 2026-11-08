import type { GraphLayout, Point, View } from "./radialGraph";

/** Dock anchors are screen coordinates; graph paths use world coordinates. */
export function attachConnections(base: GraphLayout, view: View, anchors: Record<string, Point>): GraphLayout {
  const nodes = { ...base.nodes };
  for (const [key, point] of Object.entries(anchors)) {
    nodes[key] = { x: (point.x - view.x) / view.k, y: (point.y - view.y) / view.k };
  }
  return { ...base, nodes };
}

/** Fit only the graph, reserving a real screen-space lane for the connections box. */
export function frameAgents(base: GraphLayout, left: number, width: number, height: number): View {
  const points = [base.c, base.newAgent, ...Object.values(base.agents), ...base.memory,
    ...base.tasks.flatMap(t => [t.hub, ...t.members])];
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const loX = Math.min(...xs), hiX = Math.max(...xs), loY = Math.min(...ys), hiY = Math.max(...ys);
  const k = Math.max(0.025, Math.min(1.1, Math.max(100, width - 220) / Math.max(1, hiX - loX),
    Math.max(100, height - 180) / Math.max(1, hiY - loY)));
  return { k, x: left + width / 2 - (loX + hiX) / 2 * k,
    y: (height - 60) / 2 - (loY + hiY) / 2 * k };
}
