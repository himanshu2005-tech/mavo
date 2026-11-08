import { layoutGraph, NODE_H, NODE_W, type GraphEdge, type GraphNode } from "../lib/graphLayout";

const stateText = (n: GraphNode) =>
  n.status === "draft" ? "Draft" : !n.running ? "Stopped" : n.state === "working" ? "Working" : n.state === "error" ? "Needs attention" : "Running";

/** Crew and the results they hand to each other. Reads left to right. */
export function CrewGraph({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect?: (id: string) => void }) {
  const layout = layoutGraph(nodes, edges);
  if (!layout.edges.length) return null;
  const byId = new Map(layout.nodes.map(n => [n.id, n]));
  const pad = 16;
  return (
    <figure className="crew-graph" aria-label="Crew graph">
      <svg role="img" aria-label={layout.nodes.map(n => n.name).join(", ")} width={layout.width + pad * 2} height={layout.height + pad * 2} viewBox={`${-pad} ${-pad} ${layout.width + pad * 2} ${layout.height + pad * 2}`}>
        <defs><marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="#8e8e93" /></marker></defs>
        {layout.edges.map(e => {
          const a = byId.get(e.from)!, b = byId.get(e.to)!;
          const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x - 2, y2 = b.y + NODE_H / 2, mid = (x1 + x2) / 2;
          return <path key={e.from + e.to} d={`M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`} fill="none" stroke="#6e6e73" strokeWidth="1.25" markerEnd="url(#graph-arrow)" />;
        })}
        {layout.nodes.map(n => (
          <g key={n.id} transform={`translate(${n.x} ${n.y})`} className={onSelect ? "graph-node clickable" : "graph-node"} onClick={() => onSelect?.(n.id)}>
            <rect width={NODE_W} height={NODE_H} rx="10" />
            <text x="14" y="20" className="graph-name">{n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name}</text>
            <text x="14" y="36" className="graph-state">{stateText(n)}</text>
          </g>
        ))}
      </svg>
    </figure>
  );
}
