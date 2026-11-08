export interface GraphNode { id: string; name: string; state?: string; running?: boolean; status?: string }
export interface GraphEdge { from: string; to: string }
export interface Placed extends GraphNode { x: number; y: number; depth: number }

export const NODE_W = 176;
export const NODE_H = 48;
const GAP_X = 72;
const GAP_Y = 20;

/** Layered left-to-right layout: a Crew sits one column right of its deepest upstream Crew.
 * Unknown edge endpoints are ignored and cycles are broken instead of looping forever. */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]) {
  const ids = new Set(nodes.map(n => n.id));
  const valid = edges.filter(e => ids.has(e.from) && ids.has(e.to) && e.from !== e.to);
  const depth = new Map<string, number>(nodes.map(n => [n.id, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of valid) {
      const next = (depth.get(e.from) ?? 0) + 1;
      if (next > (depth.get(e.to) ?? 0) && next < nodes.length) { depth.set(e.to, next); changed = true; }
    }
    if (!changed) break;
  }
  const columns = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    columns.set(d, [...(columns.get(d) ?? []), n]);
  }
  const tallest = Math.max(1, ...[...columns.values()].map(c => c.length));
  const height = tallest * NODE_H + (tallest - 1) * GAP_Y;
  const placed: Placed[] = [];
  for (const [d, column] of columns) {
    const used = column.length * NODE_H + (column.length - 1) * GAP_Y;
    column.forEach((n, i) => placed.push({ ...n, depth: d, x: d * (NODE_W + GAP_X), y: (height - used) / 2 + i * (NODE_H + GAP_Y) }));
  }
  const width = (Math.max(0, ...columns.keys()) + 1) * NODE_W + Math.max(0, ...columns.keys()) * GAP_X;
  return { nodes: placed, edges: valid, width, height };
}
